import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import BotUserAccess, FeishuAppConfig, Person, Resource
from app.schemas.common import WorkEventCreate
from app.services.ai.extraction import extract_items_for_event
from app.services.audit import write_audit_log
from app.services.feishu.approval import approval_amount, approval_attachment_refs, approval_form_fields, first_matching_field
from app.services.feishu.approval_attachments import ApprovalAttachmentReadResult, FeishuApprovalAttachmentService
from app.services.feishu.client import FeishuClient, ingest_feishu_message
from app.services.llm.approval_advisor import generate_approval_llm_advice
from app.services.feishu.mail import FeishuMailService, decode_mail_body, extract_mail_items
from app.services.permissions import infer_permission_profile, merge_permission_settings
from app.services.resource_registry import migrate_legacy_feishu_resources
from app.services.sync_runs import finish_sync_run, start_sync_run
from app.services.user_identity_authorizations import default_user_identity_authorizations
from app.services.work_events import upsert_work_event


V5_SHARED_BUSINESS_TOOL_COUNT = 9


@dataclass(frozen=True)
class FeishuInfoPlan:
    key: str
    label: str
    category: str
    sync_mode: str
    realtime_event_types: list[str]
    direct_api: bool
    scheduled_ingest: bool
    supported_sync: bool
    required_params: list[str]
    notes: str


FEISHU_INFO_PLANS: list[FeishuInfoPlan] = [
    FeishuInfoPlan(
        key="messages",
        label="飞书消息",
        category="communication",
        sync_mode="event_realtime + api_history + work_events",
        realtime_event_types=["im.message.receive_v1"],
        direct_api=True,
        scheduled_ingest=True,
        supported_sync=True,
        required_params=[],
        notes="实时消息走长连接事件；群资源由系统自动发现，历史消息按已登记群资源补数。",
    ),
    FeishuInfoPlan(
        key="mail",
        label="飞书邮箱",
        category="communication",
        sync_mode="feishu_mail_api + scheduled_ingest",
        realtime_event_types=["mail.user_mailbox.event.message_received_v1"],
        direct_api=True,
        scheduled_ingest=True,
        supported_sync=True,
        required_params=[],
        notes="飞书邮箱作为主邮箱通道；邮箱和文件夹由系统自动发现后进入资源库。",
    ),
    FeishuInfoPlan(
        key="chats",
        label="群组/会话",
        category="communication",
        sync_mode="api_snapshot + scheduled_ingest",
        realtime_event_types=["im.chat.member.user.added_v1", "im.chat.member.user.deleted_v1"],
        direct_api=True,
        scheduled_ingest=True,
        supported_sync=True,
        required_params=[],
        notes="定时同步群组元数据；成员变化适合事件订阅。",
    ),
    FeishuInfoPlan(
        key="contacts",
        label="通讯录",
        category="organization",
        sync_mode="event_realtime + api_snapshot + scheduled_ingest",
        realtime_event_types=["contact.user.created_v3", "contact.user.updated_v3", "contact.user.deleted_v3"],
        direct_api=True,
        scheduled_ingest=True,
        supported_sync=True,
        required_params=[],
        notes="人员/部门变化用事件订阅，周期性同步通讯录快照用于长期分析。",
    ),
    FeishuInfoPlan(
        key="calendar",
        label="日历日程",
        category="schedule",
        sync_mode="api_snapshot + scheduled_ingest",
        realtime_event_types=["calendar.event.created_v4", "calendar.event.updated_v4", "calendar.event.deleted_v4"],
        direct_api=True,
        scheduled_ingest=True,
        supported_sync=True,
        required_params=[],
        notes="实时日程问答走 Tool Router/MCP/CLI；API 只负责日程同步入库、WorkEvent 生成和长期分析。部分接口可能需要用户授权。",
    ),
    FeishuInfoPlan(
        key="approvals",
        label="审批",
        category="workflow",
        sync_mode="event_realtime + api_snapshot + scheduled_ingest",
        realtime_event_types=["approval_instance", "approval_task", "approval_cc"],
        direct_api=True,
        scheduled_ingest=True,
        supported_sync=True,
        required_params=[],
        notes="审批状态变化适合实时事件；审批资源标识由系统从审批任务、事件和资源探测中自动发现并登记。",
    ),
    FeishuInfoPlan(
        key="drive",
        label="云文档/云盘",
        category="knowledge",
        sync_mode="event_realtime + metadata_sync",
        realtime_event_types=["drive.file.bitable_field_changed_v1", "drive.file.permission_member_added_v1"],
        direct_api=True,
        scheduled_ingest=True,
        supported_sync=True,
        required_params=[],
        notes="先同步文档元数据；正文内容通常需要按文档类型和权限单独拉取。",
    ),
    FeishuInfoPlan(
        key="bitable",
        label="多维表格",
        category="data",
        sync_mode="configured_api_sync + scheduled_ingest",
        realtime_event_types=["drive.file.bitable_record_changed_v1"],
        direct_api=True,
        scheduled_ingest=True,
        supported_sync=True,
        required_params=[],
        notes="多维表格从云盘、事件和已同步资源中自动发现；只同步主数据索引和关键字段摘要。",
    ),
    FeishuInfoPlan(
        key="tasks",
        label="任务",
        category="workflow",
        sync_mode="api_snapshot + scheduled_ingest",
        realtime_event_types=["task.v2.task.created", "task.v2.task.updated"],
        direct_api=True,
        scheduled_ingest=True,
        supported_sync=True,
        required_params=[],
        notes="适合定时同步待办，结合 AI 抽取统一进入 extracted_items。",
    ),
    FeishuInfoPlan(
        key="meetings",
        label="会议",
        category="schedule",
        sync_mode="api_snapshot + scheduled_ingest",
        realtime_event_types=["vc.meeting.started_v1", "vc.meeting.ended_v1"],
        direct_api=True,
        scheduled_ingest=True,
        supported_sync=True,
        required_params=[],
        notes="会议列表/会议状态适合 API 同步；纪要内容通常需要额外权限和文档接口。",
    ),
]


def get_feishu_sync_plan() -> list[dict[str, Any]]:
    return [asdict(plan) for plan in FEISHU_INFO_PLANS]


async def probe_feishu_capabilities(app_config: FeishuAppConfig) -> dict[str, Any]:
    client = FeishuClient(app_config)
    checks = {
        "chats": ("GET", "/open-apis/im/v1/chats", {"page_size": 1}),
        "contacts": (
            "GET",
            "/open-apis/contact/v3/users/find_by_department",
            {
                "department_id": "0",
                "department_id_type": "open_department_id",
                "user_id_type": "open_id",
                "page_size": 1,
            },
        ),
        "calendar": (
            "GET",
            "/open-apis/calendar/v4/calendars/primary/events",
            {
                "page_size": 50,
                "start_time": str(int((datetime.now(UTC) - timedelta(days=1)).timestamp())),
                "end_time": str(int((datetime.now(UTC) + timedelta(days=7)).timestamp())),
            },
        ),
        "drive": ("GET", "/open-apis/drive/v1/files", {"page_size": 1}),
        "tasks": ("GET", "/open-apis/task/v2/tasks", {"page_size": 1}),
        "meetings": (
            "GET",
            "/open-apis/vc/v1/meeting_list",
            {
                "page_size": 20,
                "start_time": str(int((datetime.now(UTC) - timedelta(days=7)).timestamp())),
                "end_time": str(int(datetime.now(UTC).timestamp())),
                "meeting_status": 2,
                "user_id_type": "open_id",
            },
        ),
    }
    results: dict[str, Any] = {}
    for key, (method, path, params) in checks.items():
        try:
            if method == "POST":
                await client.api_post(path, params)
            else:
                await client.api_get(path, params=params)
            results[key] = {"available": True, "mode": _plan_by_key(key).sync_mode}
        except HTTPException as exc:
            results[key] = {
                "available": False,
                "mode": _plan_by_key(key).sync_mode,
                "reason": _safe_feishu_error(exc.detail),
            }
    results["messages"] = {
        "available": True,
        "mode": _plan_by_key("messages").sync_mode,
        "reason": "消息实时接收已由长连接事件处理；历史补数使用系统自动发现的群资源。",
    }
    results["mail"] = {
        "available": False,
        "mode": _plan_by_key("mail").sync_mode,
        "reason": "飞书邮箱同步使用系统自动发现的邮箱与文件夹资源；未发现时会进入资源盲区监控。",
    }
    results["approvals"] = {
        "available": False,
        "mode": _plan_by_key("approvals").sync_mode,
        "reason": "审批同步依赖系统自动发现的审批资源标识；如果未发现，通常是审批权限、事件订阅或管理员身份尚未打通。",
    }
    results["bitable"] = {
        "available": False,
        "mode": _plan_by_key("bitable").sync_mode,
        "reason": "多维表格同步使用云盘、事件和本地数据中自动发现的表格资源。",
    }
    return {"plan": get_feishu_sync_plan(), "capabilities": results}


async def sync_feishu_information(
    db: Session,
    app_config: FeishuAppConfig,
    *,
    kinds: list[str],
    limit: int,
    max_pages: int = 3,
    chat_id: str | None = None,
    user_mailbox_id: str | None = None,
    folder_id: str | None = None,
    app_token: str | None = None,
    table_id: str | None = None,
    approval_code: str | None = None,
    document_id: str | None = None,
    document_type: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    extract_items: bool = False,
    sync_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected = kinds or [plan.key for plan in FEISHU_INFO_PLANS if plan.supported_sync and plan.key != "messages"]
    sync_run = start_sync_run(
        db,
        company_id=app_config.company_id,
        provider="feishu",
        sync_type="information",
        cursor={"kinds": selected, "chat_id": chat_id},
    )
    results: dict[str, Any] = {}
    try:
        for kind in selected:
            if kind == "messages":
                results[kind] = await _sync_messages(
                    db,
                    app_config,
                    chat_id=chat_id,
                    start_time=start_time,
                    end_time=end_time,
                    limit=limit,
                    max_pages=max_pages,
                    extract_items=extract_items,
                )
            elif kind == "mail":
                results[kind] = await _sync_feishu_mail(
                    db,
                    app_config,
                    user_mailbox_id=user_mailbox_id,
                    folder_id=folder_id,
                    limit=limit,
                    max_pages=max_pages,
                    extract_items=extract_items,
                )
            elif kind == "bitable":
                results[kind] = await _sync_bitable_records(
                    db,
                    app_config,
                    app_token=app_token,
                    table_id=table_id,
                    limit=limit,
                    max_pages=max_pages,
                    extract_items=extract_items,
                )
            elif kind == "chats":
                results[kind] = await _sync_api_collection(
                    db,
                    app_config,
                    kind=kind,
                    path="/open-apis/im/v1/chats",
                    params={"page_size": min(limit, 100)},
                    item_keys=["items"],
                    extract_items=extract_items,
                    limit=limit,
                    max_pages=max_pages,
                )
            elif kind == "contacts":
                results[kind] = await _sync_contacts(
                    db,
                    app_config,
                    limit=limit,
                    max_pages=max_pages,
                    extract_items=extract_items,
                )
            elif kind == "calendar":
                now = datetime.now(UTC)
                results[kind] = await _sync_api_collection(
                    db,
                    app_config,
                    kind=kind,
                    path="/open-apis/calendar/v4/calendars/primary/events",
                    params={
                        "page_size": max(50, min(limit, 100)),
                        "start_time": str(
                            int((start_time or now.replace(hour=0, minute=0, second=0, microsecond=0)).timestamp())
                        ),
                        "end_time": str(int((end_time or (now + timedelta(days=7))).timestamp())),
                    },
                    item_keys=["items"],
                    extract_items=extract_items,
                    limit=limit,
                    max_pages=max_pages,
                )
            elif kind == "approvals":
                results[kind] = await _sync_approvals(
                    db,
                    app_config,
                    extract_items=extract_items,
                    approval_code=approval_code,
                    start_time=start_time,
                    end_time=end_time,
                    limit=limit,
                    max_pages=max_pages,
                )
            elif kind in {"doc", "docx", "document", "wiki"}:
                results[kind] = await _sync_document_content(
                    db,
                    app_config,
                    document_id=document_id,
                    document_type=document_type or kind,
                    extract_items=extract_items,
                    sync_context=sync_context,
                )
            elif kind == "drive":
                results[kind] = await _sync_api_collection(
                    db,
                    app_config,
                    kind=kind,
                    path="/open-apis/drive/v1/files",
                    params={"page_size": min(limit, 100)},
                    item_keys=["files", "items"],
                    extract_items=extract_items,
                    limit=limit,
                    max_pages=max_pages,
                )
            elif kind == "tasks":
                results[kind] = await _sync_api_collection(
                    db,
                    app_config,
                    kind=kind,
                    path="/open-apis/task/v2/tasks",
                    params={"page_size": min(limit, 100)},
                    item_keys=["items", "tasks"],
                    extract_items=extract_items,
                    limit=limit,
                    max_pages=max_pages,
                )
            elif kind == "meetings":
                results[kind] = await _sync_api_collection(
                    db,
                    app_config,
                    kind=kind,
                    path="/open-apis/vc/v1/meeting_list",
                    params={
                        "page_size": max(20, min(limit, 100)),
                        "start_time": str(int((start_time or datetime.now(UTC) - timedelta(days=7)).timestamp())),
                        "end_time": str(int((end_time or datetime.now(UTC)).timestamp())),
                        "meeting_status": 2,
                        "user_id_type": "open_id",
                    },
                    item_keys=["meeting_list", "items", "meetings"],
                    extract_items=extract_items,
                    limit=limit,
                    max_pages=max_pages,
                    min_page_size=20,
                )
            else:
                results[kind] = {"available": False, "saved_count": 0, "error": "Unsupported sync kind"}
        saved_count = sum(item.get("saved_count", 0) for item in results.values() if isinstance(item, dict))
        error_count = sum(1 for item in results.values() if isinstance(item, dict) and not item.get("available", True))
        finish_sync_run(
            sync_run,
            status="success" if error_count == 0 else "partial",
            saved_count=saved_count,
            error_count=error_count,
            cursor=_combined_cursor(results),
            summary=results,
        )
    except Exception as exc:
        finish_sync_run(
            sync_run,
            status="failed",
            error_count=1,
            summary={"error": str(exc)[:500], "partial_results": results},
        )
        raise
    db.commit()
    return results


async def _sync_messages(
    db: Session,
    app_config: FeishuAppConfig,
    *,
    chat_id: str | None,
    start_time: datetime | None,
    end_time: datetime | None,
    limit: int,
    max_pages: int,
    extract_items: bool,
) -> dict[str, Any]:
    if not chat_id:
        return {"available": False, "saved_count": 0, "error": "messages sync requires an auto-discovered chat resource"}
    end = end_time or datetime.now(UTC)
    start = start_time or (end - timedelta(days=1))
    client = FeishuClient(app_config)
    saved_ids = []
    page_token = None
    page_count = 0
    has_more = False
    try:
        while page_count < max_pages and len(saved_ids) < limit:
            body = await client.list_messages(
                container_id_type="chat",
                container_id=chat_id,
                start_time=int(start.timestamp()),
                end_time=int(end.timestamp()),
                page_size=min(limit - len(saved_ids), 50),
                page_token=page_token,
            )
            page_count += 1
            data = body.get("data") or {}
            for message in data.get("items") or []:
                event = ingest_feishu_message(db, app_config, message)
                saved_ids.append(str(event.id))
                if extract_items:
                    extract_items_for_event(db, event)
                if len(saved_ids) >= limit:
                    break
            has_more = bool(data.get("has_more"))
            page_token = data.get("page_token")
            if not has_more or not page_token:
                break
    except HTTPException as exc:
        return {"available": False, "saved_count": len(saved_ids), "error": _safe_feishu_error(exc.detail)}
    return {
        "available": True,
        "saved_count": len(saved_ids),
        "work_event_ids": saved_ids,
        "has_more": has_more,
        "page_token": page_token,
        "page_count": page_count,
    }


async def _sync_approvals(
    db: Session,
    app_config: FeishuAppConfig,
    *,
    extract_items: bool,
    approval_code: str | None,
    start_time: datetime | None,
    end_time: datetime | None,
    limit: int,
    max_pages: int,
) -> dict[str, Any]:
    approval_items = _approval_metadata_for_sync(db, app_config=app_config, approval_code=approval_code)
    if not approval_items:
        return {
            "available": False,
            "saved_count": 0,
            "error": "approval resources not discovered",
            "next_step": "先让系统执行资源发现，或让机器人读取一次老板/管理员待审批任务；系统会自动登记审批资源标识。",
        }

    saved_ids: list[str] = []
    errors: list[dict[str, str]] = []
    cursors: dict[str, Any] = {}
    per_code_limit = max(1, min(limit, 100))
    for approval in approval_items:
        code = approval["approval_code"]
        result = await _sync_api_collection(
            db,
            app_config,
            kind="approvals",
            path="/open-apis/approval/v4/instances/query",
            params={
                "page_size": max(5, min(per_code_limit, 100)),
                "user_id_type": "open_id",
                "approval_code": code,
                "instance_start_time_from": str(
                    int((start_time or datetime.now(UTC) - timedelta(days=30)).timestamp() * 1000)
                ),
                "instance_start_time_to": str(int((end_time or datetime.now(UTC)).timestamp() * 1000)),
            },
            item_keys=["instance_list", "instances", "items"],
            extract_items=extract_items,
            method="POST",
            limit=per_code_limit,
            max_pages=max_pages,
            item_context=approval,
        )
        cursors[code] = {"has_more": result.get("has_more"), "page_token": result.get("page_token")}
        saved_ids.extend(result.get("work_event_ids") or [])
        if not result.get("available", True):
            errors.append({"approval_code": code, "error": str(result.get("error") or "unknown")[:500]})

    return {
        "available": len(errors) == 0,
        "saved_count": len(saved_ids),
        "work_event_ids": saved_ids,
        "approval_codes": [item["approval_code"] for item in approval_items],
        "approval_code_count": len(approval_items),
        "has_more": any(bool(cursor.get("has_more")) for cursor in cursors.values()),
        "page_token": None,
        "page_count": len(approval_items),
        "cursors": cursors,
        **({"errors": errors, "error": errors[0]["error"]} if errors else {}),
    }


async def _sync_contacts(
    db: Session,
    app_config: FeishuAppConfig,
    *,
    limit: int,
    max_pages: int,
    extract_items: bool,
) -> dict[str, Any]:
    client = FeishuClient(app_config)
    saved_ids: list[str] = []
    errors: list[str] = []
    departments: list[dict[str, Any]] = []
    users_by_open_id: dict[str, dict[str, Any]] = {}
    agent_synced_count = 0
    agent_created_count = 0
    queue = ["0"]
    seen_departments = set(queue)
    page_count = 0
    target_department_limit = max(limit, 100)

    while queue and page_count < max(max_pages, 1) * 20 and len(departments) < target_department_limit:
        department_id = queue.pop(0)
        try:
            body = await client.api_get(
                f"/open-apis/contact/v3/departments/{quote(department_id, safe='')}/children",
                params={
                    "department_id_type": "department_id",
                    "page_size": min(max(limit, 20), 50),
                },
            )
        except HTTPException as exc:
            errors.append(_safe_feishu_error(exc.detail))
            continue
        page_count += 1
        for department in _extract_items_from_data(body.get("data") or {}, ["items", "departments"]):
            departments.append(department)
            child_id = str(department.get("department_id") or "").strip()
            if child_id and child_id not in seen_departments:
                seen_departments.add(child_id)
                queue.append(child_id)

    for department in departments:
        event = upsert_work_event(db, _contact_department_to_event(app_config, department))
        saved_ids.append(str(event.id))
        if extract_items:
            extract_items_for_event(db, event)

    department_names_by_id = {
        str(department.get("department_id") or department.get("open_department_id") or ""): str(
            department.get("name") or department.get("i18n_name") or ""
        )
        for department in departments
        if department.get("department_id") or department.get("open_department_id")
    }

    user_department_ids = ["0", *[str(item.get("department_id")) for item in departments if item.get("department_id")]]
    for department_id in user_department_ids[: max(limit, 100)]:
        try:
            body = await client.api_get(
                "/open-apis/contact/v3/users/find_by_department",
                params={
                    "department_id": department_id,
                    "department_id_type": "department_id",
                    "user_id_type": "open_id",
                    "page_size": min(max(limit, 20), 50),
                },
            )
        except HTTPException as exc:
            errors.append(_safe_feishu_error(exc.detail))
            continue
        for user in _extract_items_from_data(body.get("data") or {}, ["items", "users"]):
            open_id = str(user.get("open_id") or user.get("user_id") or "")
            if not open_id:
                continue
            existing = users_by_open_id.get(open_id) or {}
            department_ids = set(existing.get("department_ids") or [])
            department_ids.add(department_id)
            users_by_open_id[open_id] = {
                **existing,
                **user,
                "department_ids": sorted(department_ids),
                "department_names": _unique_strings(
                    [department_names_by_id.get(item, "") for item in sorted(department_ids)]
                ),
            }

    for user in users_by_open_id.values():
        event = upsert_work_event(db, _contact_user_to_event(app_config, user))
        _upsert_person_from_contact(db, app_config=app_config, user=user)
        if settings.feishu_sync_contacts_to_bot_users:
            agent_result = _upsert_bot_user_access_from_contact(db, app_config=app_config, user=user)
            if agent_result.get("synced"):
                agent_synced_count += 1
            if agent_result.get("created"):
                agent_created_count += 1
        saved_ids.append(str(event.id))
        if extract_items:
            extract_items_for_event(db, event)

    return {
        "available": not errors,
        "saved_count": len(saved_ids),
        "work_event_ids": saved_ids,
        "department_count": len(departments),
        "user_count": len(users_by_open_id),
        "agent_synced_count": agent_synced_count,
        "agent_created_count": agent_created_count,
        "agent_sync_model": "contact_sync_creates_employee_agents",
        "page_count": page_count,
        **({"errors": errors, "error": errors[0]} if errors else {}),
    }


def _contact_department_to_event(app_config: FeishuAppConfig, department: dict[str, Any]) -> WorkEventCreate:
    department_id = str(department.get("department_id") or department.get("open_department_id") or _stable_item_id(department))
    name = str(department.get("name") or department.get("i18n_name") or department_id)
    return WorkEventCreate(
        company_id=app_config.company_id,
        source="feishu",
        event_type="feishu.contacts.department",
        external_id=f"contact_department:{department_id}",
        thread_id=None,
        title=f"部门：{name}",
        content_text=_compact_item_text(department),
        occurred_at=datetime.now(UTC),
        actors=[],
        labels=["feishu", "contacts", "department", "api_sync"],
        payload={"kind": "contacts.department", "item": department},
    )


def _contact_user_to_event(app_config: FeishuAppConfig, user: dict[str, Any]) -> WorkEventCreate:
    open_id = str(user.get("open_id") or user.get("user_id") or _stable_item_id(user))
    name = str(user.get("name") or user.get("en_name") or user.get("email") or open_id)
    department_ids = user.get("department_ids") or []
    return WorkEventCreate(
        company_id=app_config.company_id,
        source="feishu",
        event_type="feishu.contacts.user",
        external_id=f"contact_user:{open_id}",
        thread_id=None,
        title=f"人员：{name}",
        content_text=_compact_item_text(
            {
                "name": name,
                "job_title": user.get("job_title"),
                "email": user.get("enterprise_email") or user.get("email"),
                "department_ids": department_ids,
                "status": user.get("status"),
            }
        ),
        occurred_at=datetime.now(UTC),
        actors=[],
        labels=["feishu", "contacts", "user", "api_sync"],
        payload={"kind": "contacts.user", "item": user},
    )


def _upsert_person_from_contact(db: Session, *, app_config: FeishuAppConfig, user: dict[str, Any]) -> None:
    open_id = str(user.get("open_id") or user.get("user_id") or "").strip()
    name = str(user.get("name") or user.get("en_name") or "").strip()
    if not open_id or not name:
        return
    person = db.scalar(
        select(Person)
        .where(Person.company_id == app_config.company_id)
        .where(Person.external_id == open_id)
    )
    if not person:
        person = Person(company_id=app_config.company_id, name=name, external_id=open_id)
        db.add(person)
    person.name = name
    person.email = user.get("enterprise_email") or user.get("email")
    person.role_title = user.get("job_title")
    person.payload = {
        "source": "feishu_contacts",
        "department_ids": user.get("department_ids") or [],
        "raw": user,
    }


def _upsert_bot_user_access_from_contact(db: Session, *, app_config: FeishuAppConfig, user: dict[str, Any]) -> dict[str, Any]:
    open_id = str(user.get("open_id") or user.get("user_id") or "").strip()
    if not open_id:
        return {"synced": False, "created": False}
    display_name = str(user.get("name") or user.get("en_name") or user.get("email") or open_id).strip()
    email = user.get("enterprise_email") or user.get("email")
    job_title = user.get("job_title")
    profile = infer_permission_profile(
        display_name=display_name,
        job_title=job_title,
        department_names=user.get("department_names") or [],
        email=email,
    )
    access = db.scalar(
        select(BotUserAccess)
        .where(BotUserAccess.company_id == app_config.company_id)
        .where(BotUserAccess.open_id == open_id)
    )
    created = False
    if not access:
        created = True
        access = BotUserAccess(
            company_id=app_config.company_id,
            open_id=open_id,
            display_name=display_name,
            role=profile.role,
            access_scope=profile.access_scope,
            is_active=True,
        )
        db.add(access)
    else:
        access.display_name = access.display_name or display_name
        if access.role not in {"owner", "admin"}:
            access.role = profile.role
            access.access_scope = profile.access_scope
    synced_at = datetime.now(UTC).isoformat()
    settings_data = merge_permission_settings(
        access.settings,
        profile=profile,
        source="feishu_contacts",
        department_ids=user.get("department_ids") or [],
        department_names=user.get("department_names") or [],
        email=email,
        job_title=job_title,
        status=user.get("status"),
        synced_at=synced_at,
    )
    agent_profile = settings_data.get("agent_profile") if isinstance(settings_data.get("agent_profile"), dict) else {}
    settings_data["agent_profile"] = {
        **agent_profile,
        "status": agent_profile.get("status") or "active",
        "scope": agent_profile.get("scope") or access.access_scope,
        "entrypoint": agent_profile.get("entrypoint") or "feishu_bot",
        "activation_source": agent_profile.get("activation_source") or "feishu_contacts",
        "activated_at": agent_profile.get("activated_at") or synced_at,
        "requires_feishu_app_config": False,
    }
    settings_data["agent_model"] = "per_user_personal_agent"
    settings_data["user_identity_authorizations"] = settings_data.get(
        "user_identity_authorizations"
    ) or default_user_identity_authorizations(open_id=open_id, created_at=synced_at)
    access.settings = settings_data
    if created:
        write_audit_log(
            db,
            action="agent.employee.created_from_contact",
            company_id=app_config.company_id,
            actor=open_id,
            target_type="employee_agent",
            target_id=open_id,
            payload={
                "status": "success",
                "agent_type": "employee_personal_agent",
                "agent_owner_open_id": open_id,
                "agent_owner_display_name": display_name,
                "agent_entrypoint": "feishu_bot",
                "agent_scope": access.access_scope,
                "activation_source": "feishu_contacts",
                "synced_from": "contact_sync",
                "activated_at": synced_at,
                "shared_business_tool_count": V5_SHARED_BUSINESS_TOOL_COUNT,
                "enterprise_scope_status": "available",
                "enterprise_resources_available_after_agent_created": True,
                "user_identity_access_model": "bundle_authorization",
                "user_identity_bundle_status": "not_authorized",
                "digital_advisor_can_only_tighten": True,
                "can_escalate_original_permissions": False,
            },
        )
    return {"synced": True, "created": created, "open_id": open_id}


def _approval_codes_for_sync(
    db: Session,
    *,
    app_config: FeishuAppConfig,
    approval_code: str | None,
) -> list[str]:
    if approval_code and approval_code.strip():
        return [approval_code.strip()]
    return [item["approval_code"] for item in _approval_metadata_for_sync(db, app_config=app_config, approval_code=None)]


def _approval_metadata_for_sync(
    db: Session,
    *,
    app_config: FeishuAppConfig,
    approval_code: str | None,
) -> list[dict[str, str]]:
    if approval_code and approval_code.strip():
        return [{"approval_code": approval_code.strip(), "approval_name": approval_code.strip()}]
    v5_resources = db.scalars(
        select(Resource)
        .where(Resource.company_id == app_config.company_id)
        .where(Resource.platform == "feishu")
        .where(Resource.resource_type.in_(("approval", "approval_code")))
        .where(Resource.enabled.is_(True))
        .order_by(Resource.created_at.desc())
    ).all()
    items: list[dict[str, str]] = []
    seen = set()
    for resource in v5_resources:
        code = str(resource.resource_id or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        items.append({"approval_code": code, "approval_name": resource.resource_name or code})
    for migration in migrate_legacy_feishu_resources(db, app_config=app_config, resource_type="approval_code"):
        code = migration.external_id
        if not code or code in seen:
            continue
        seen.add(code)
        items.append({"approval_code": code, "approval_name": migration.name or code})
    return items


async def _sync_feishu_mail(
    db: Session,
    app_config: FeishuAppConfig,
    *,
    user_mailbox_id: str | None,
    folder_id: str | None,
    limit: int,
    max_pages: int,
    extract_items: bool,
) -> dict[str, Any]:
    if not user_mailbox_id or not folder_id:
        return {
            "available": False,
            "saved_count": 0,
            "error": "mail sync requires an auto-discovered mailbox resource",
        }

    client = FeishuClient(app_config)
    mailbox = quote(user_mailbox_id, safe="")
    path = f"/open-apis/mail/v1/user_mailboxes/{mailbox}/messages"
    saved_ids: list[str] = []
    page_token = None
    has_more = False
    page_count = 0
    try:
        while page_count < max_pages and len(saved_ids) < limit:
            params = {
                "folder_id": folder_id,
                "page_size": min(limit - len(saved_ids), 20),
            }
            if page_token:
                params["page_token"] = page_token
            body = await client.api_get(path, params=params)
            page_count += 1
            data = body.get("data") or {}
            for item in _extract_mail_items(data):
                detail = await _get_mail_detail(client, user_mailbox_id, item)
                event = upsert_work_event(db, _mail_item_to_event(app_config, detail))
                saved_ids.append(str(event.id))
                if extract_items:
                    extract_items_for_event(db, event)
                if len(saved_ids) >= limit:
                    break
            has_more = bool(data.get("has_more"))
            page_token = data.get("page_token")
            if not has_more or not page_token:
                break
    except HTTPException as exc:
        return {"available": False, "saved_count": len(saved_ids), "error": _safe_feishu_error(exc.detail)}
    return {
        "available": True,
        "saved_count": len(saved_ids),
        "work_event_ids": saved_ids,
        "has_more": has_more,
        "page_token": page_token,
        "page_count": page_count,
    }


async def _sync_bitable_records(
    db: Session,
    app_config: FeishuAppConfig,
    *,
    app_token: str | None,
    table_id: str | None,
    limit: int,
    max_pages: int,
    extract_items: bool,
) -> dict[str, Any]:
    if not app_token or not table_id:
        return {
            "available": False,
            "saved_count": 0,
            "error": "bitable sync requires an auto-discovered bitable resource",
        }
    path = f"/open-apis/bitable/v1/apps/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}/records"
    saved_ids = []
    page_token = None
    has_more = False
    page_count = 0
    try:
        client = FeishuClient(app_config)
        while page_count < max_pages and len(saved_ids) < limit:
            remaining = limit - len(saved_ids)
            params = {"page_size": min(remaining, 100)}
            if page_token:
                params["page_token"] = page_token
            body = await client.api_get(path, params=params)
            page_count += 1
            data = body.get("data") or {}
            for item in _extract_items_from_data(data, ["items", "records"]):
                event = upsert_work_event(
                    db,
                    _bitable_record_to_index_event(
                        app_config,
                        app_token=app_token,
                        table_id=table_id,
                        item=item,
                    ),
                )
                saved_ids.append(str(event.id))
                if extract_items:
                    extract_items_for_event(db, event)
                if len(saved_ids) >= limit:
                    break
            has_more = bool(data.get("has_more"))
            page_token = data.get("page_token")
            if not has_more or not page_token:
                break
    except HTTPException as exc:
        return {"available": False, "saved_count": len(saved_ids), "error": _safe_feishu_error(exc.detail)}
    return {
        "available": True,
        "saved_count": len(saved_ids),
        "work_event_ids": saved_ids,
        "has_more": has_more,
        "page_token": page_token,
        "page_count": page_count,
        "mode": "master_data_index",
    }


async def _sync_document_content(
    db: Session,
    app_config: FeishuAppConfig,
    *,
    document_id: str | None,
    document_type: str,
    extract_items: bool,
    sync_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not document_id:
        return {"available": False, "saved_count": 0, "error": "document sync requires document_id"}
    sync_context = sync_context or {}
    data_layer = str(sync_context.get("data_layer") or "")
    sync_action = str(sync_context.get("sync_action") or "")
    query_path = str(sync_context.get("query_path") or "")
    vectorize = str(sync_context.get("vectorize") or "")
    if (
        data_layer != "knowledge_hot"
        or sync_action != "knowledge_vectorize"
        or query_path != "local_rag"
        or vectorize not in {"", "full_chunk_or_summary_chunk"}
    ):
        return {
            "available": False,
            "saved_count": 0,
            "error": "document content sync is only allowed for L1 hot knowledge_vectorize local_rag resources",
            "data_layer": data_layer or None,
            "query_path": query_path or None,
            "sync_action": sync_action or None,
        }
    normalized_type = document_type.lower()
    client = FeishuClient(app_config)
    resolved_document_id = document_id
    resolved_document_type = normalized_type
    wiki_node: dict[str, Any] | None = None
    if normalized_type == "wiki":
        try:
            node_body = await client.api_get(
                "/open-apis/wiki/v2/spaces/get_node",
                params={"token": document_id},
            )
        except HTTPException as exc:
            return {"available": False, "saved_count": 0, "error": _safe_feishu_error(exc.detail)}
        wiki_node = (node_body.get("data") or {}).get("node") or {}
        resolved_document_id = str(wiki_node.get("obj_token") or "").strip()
        resolved_document_type = str(wiki_node.get("obj_type") or "").strip().lower()
        if not resolved_document_id:
            return {"available": False, "saved_count": 0, "error": "wiki node did not return obj_token"}
    paths = {
        "docx": f"/open-apis/docx/v1/documents/{quote(resolved_document_id, safe='')}/raw_content",
        "document": f"/open-apis/docx/v1/documents/{quote(resolved_document_id, safe='')}/raw_content",
        "doc": f"/open-apis/doc/v2/{quote(resolved_document_id, safe='')}/content",
    }
    path = paths.get(resolved_document_type)
    if not path:
        return {"available": False, "saved_count": 0, "error": "document_type must be docx, document, doc, or wiki"}
    try:
        body = await client.api_get(path)
    except HTTPException as exc:
        return {"available": False, "saved_count": 0, "error": _safe_feishu_error(exc.detail)}
    data = body.get("data") or {}
    text = data.get("content") or data.get("raw_content") or data.get("text") or json.dumps(data, ensure_ascii=False)
    vectorize = vectorize or "full_chunk_or_summary_chunk"
    stored_text = str(text)[:12000]
    document_chunks = _knowledge_document_chunks(stored_text)
    rag_indexing = _rag_indexing_metadata(
        vectorize=vectorize,
        chunking="queued" if document_chunks else "skipped_empty",
        chunk_count=len(document_chunks),
    )
    event = upsert_work_event(
        db,
        WorkEventCreate(
            company_id=app_config.company_id,
            resource_id=_resource_uuid(sync_context.get("resource_id")),
            source="feishu",
            business_domain="knowledge",
            event_type=f"feishu.{normalized_type}.content",
            external_id=f"{normalized_type}:{document_id}",
            title=str(sync_context.get("resource_name") or f"飞书文档 {document_id}"),
            content_text=stored_text,
            occurred_at=datetime.now(UTC),
            labels=["feishu", normalized_type, "content_sync", data_layer],
            payload={
                "kind": normalized_type,
                "document_id": document_id,
                "resolved_document_id": resolved_document_id,
                "resolved_document_type": resolved_document_type,
                "wiki_node": wiki_node,
                "document_chunks": document_chunks,
                "data": data,
                "data_layer": data_layer,
                "query_path": query_path,
                "vectorize": vectorize,
                "rag_indexing": rag_indexing,
                "sync_context": sync_context,
            },
        ),
    )
    if extract_items:
        extract_items_for_event(db, event)
    document_store = _document_store_metadata(
        work_event_ids=[str(event.id)],
        chunk_count=len(document_chunks),
        rag_indexing=rag_indexing,
    )
    return {
        "available": True,
        "saved_count": 1,
        "work_event_ids": [str(event.id)],
        "document_store": document_store,
        "data_layer": data_layer,
        "query_path": query_path,
        "sync_action": sync_action,
        "vectorize": vectorize,
        "rag_indexing": rag_indexing,
        "chunk_count": len(document_chunks),
        "resolved_document_id": resolved_document_id,
        "resolved_document_type": resolved_document_type,
    }


def _resource_uuid(value: Any) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _rag_indexing_metadata(*, vectorize: str, chunking: str = "pending", chunk_count: int = 0) -> dict[str, str]:
    return {
        "document_store": "work_events",
        "chunking": chunking,
        "chunk_count": str(chunk_count),
        "vector_db": "qdrant_vectors",
        "rag_index": "pending",
        "vector_status": "pending",
        "vectorize": vectorize,
    }


def _document_store_metadata(
    *,
    work_event_ids: list[str],
    chunk_count: int,
    rag_indexing: dict[str, Any],
) -> dict[str, Any]:
    return {
        "store": "work_events",
        "work_event_ids": work_event_ids,
        "chunk_count": chunk_count,
        "vector_db": rag_indexing.get("vector_db"),
        "rag_index": rag_indexing.get("rag_index"),
        "vector_status": rag_indexing.get("vector_status"),
    }


def _knowledge_document_chunks(
    text: str,
    *,
    max_chars: int = 1600,
    overlap_chars: int = 160,
    max_chunks: int = 40,
) -> list[dict[str, Any]]:
    normalized = "\n".join(line.strip() for line in str(text or "").splitlines() if line.strip())
    if not normalized:
        return []
    chunks: list[dict[str, Any]] = []
    start = 0
    text_length = len(normalized)
    while start < text_length and len(chunks) < max_chunks:
        end = min(start + max_chars, text_length)
        if end < text_length:
            boundary = max(normalized.rfind("\n", start, end), normalized.rfind("。", start, end))
            if boundary > start + max_chars // 2:
                end = boundary + 1
        chunk_text = normalized[start:end].strip()
        if chunk_text:
            chunks.append(
                {
                    "chunk_id": f"chunk-{len(chunks) + 1}",
                    "index": len(chunks),
                    "char_start": start,
                    "char_end": end,
                    "text": chunk_text,
                }
            )
        if end >= text_length:
            break
        start = max(end - overlap_chars, start + 1)
    return chunks


async def _get_mail_detail(client: FeishuClient, user_mailbox_id: str, item: Any) -> dict[str, Any]:
    return await FeishuMailService(getattr(client, "app_config", None), client=client).get_message_detail_safe(
        user_mailbox_id=user_mailbox_id,
        item=item,
    )


def _mail_item_to_event(app_config: FeishuAppConfig, item: dict[str, Any]) -> WorkEventCreate:
    message = item.get("message") if isinstance(item.get("message"), dict) else item
    message_id = _first_present(message, ["message_id", "id"]) or _first_present(item, ["message_id", "id"])
    subject = _first_present(message, ["subject"]) or "飞书邮箱邮件"
    body = _decode_mail_body(message.get("body_plain_text") or message.get("body_html") or "")
    sender = message.get("head_from") or message.get("from") or {}
    recipients = {
        "to": message.get("to") or [],
        "cc": message.get("cc") or [],
        "bcc": message.get("bcc") or [],
    }
    attachment_names = [
        attachment.get("filename")
        for attachment in (message.get("attachments") or [])
        if isinstance(attachment, dict) and attachment.get("filename")
    ]
    content = {
        "subject": subject,
        "from": sender,
        **recipients,
        "folder_id": message.get("folder_id"),
        "body": body,
        "attachments": attachment_names,
    }
    return WorkEventCreate(
        company_id=app_config.company_id,
        source="feishu",
        event_type="feishu.mail.snapshot",
        external_id=f"mail:{message_id or _stable_item_id(item)}",
        thread_id=message.get("thread_id"),
        title=subject,
        content_text=json.dumps(content, ensure_ascii=False, default=str)[:6000],
        occurred_at=_parse_item_time(message),
        actors=[sender] if sender else [],
        labels=["feishu", "mail", "api_sync", str(message.get("folder_id") or "")],
        payload={"kind": "mail", "item": item},
    )


def _decode_mail_body(value: Any) -> str:
    return decode_mail_body(value)


def _extract_mail_items(data: dict[str, Any]) -> list[Any]:
    return extract_mail_items(data)


async def _sync_api_collection(
    db: Session,
    app_config: FeishuAppConfig,
    *,
    kind: str,
    path: str,
    params: dict[str, Any],
    item_keys: list[str],
    extract_items: bool,
    method: str = "GET",
    limit: int | None = None,
    max_pages: int = 3,
    item_context: dict[str, Any] | None = None,
    min_page_size: int = 1,
) -> dict[str, Any]:
    saved_ids = []
    page_token = params.get("page_token")
    has_more = False
    page_count = 0
    target_limit = limit or int(params.get("page_size", 100))
    try:
        client = FeishuClient(app_config)
        approval_attachment_service = FeishuApprovalAttachmentService(app_config) if kind == "approvals" else None
        while page_count < max_pages and len(saved_ids) < target_limit:
            request_params = {**params}
            remaining = target_limit - len(saved_ids)
            if "page_size" in request_params:
                request_params["page_size"] = max(min_page_size, min(int(request_params["page_size"]), remaining))
            if page_token:
                request_params["page_token"] = page_token
            if method == "POST":
                body = await client.api_post(path, request_params)
            else:
                body = await client.api_get(path, params=request_params)
            page_count += 1
            data = body.get("data") or {}
            items = _extract_items_from_data(data, item_keys)
            for item in items:
                if approval_attachment_service is not None:
                    await _enrich_approval_item_with_instance_detail(item, client)
                    await _enrich_approval_item_with_attachments(item, approval_attachment_service)
                    _enrich_approval_item_with_ai_advice(item)
                event = upsert_work_event(db, _api_item_to_event(app_config, kind, item, item_context=item_context))
                saved_ids.append(str(event.id))
                if extract_items:
                    extract_items_for_event(db, event)
                if len(saved_ids) >= target_limit:
                    break
            has_more = bool(data.get("has_more"))
            page_token = data.get("page_token")
            if not has_more or not page_token:
                break
    except HTTPException as exc:
        return {"available": False, "saved_count": len(saved_ids), "error": _safe_feishu_error(exc.detail)}
    return {
        "available": True,
        "saved_count": len(saved_ids),
        "work_event_ids": saved_ids,
        "has_more": has_more,
        "page_token": page_token,
        "page_count": page_count,
    }


async def _enrich_approval_item_with_instance_detail(item: dict[str, Any], client: FeishuClient) -> None:
    if item.get("form") or isinstance(item.get("instance_detail"), dict):
        return
    instance_code = str(_approval_instance_code_for_sync(item) or "").strip()
    if not instance_code:
        return
    try:
        body = await client.api_get(
            f"/open-apis/approval/v4/instances/{quote(instance_code, safe='')}",
            params={"user_id_type": "open_id"},
        )
    except HTTPException as exc:
        item["detail_error"] = _safe_feishu_error(exc.detail)
        return
    data = body.get("data") or {}
    if not isinstance(data, dict):
        return
    item["instance_detail"] = data
    item.setdefault("instance_code", instance_code)
    for key in (
        "form",
        "status",
        "serial_number",
        "start_time",
        "end_time",
        "user_id",
        "open_id",
        "approval_name",
        "instance_code",
    ):
        if item.get(key) in (None, "") and data.get(key) not in (None, ""):
            item[key] = data[key]


async def _enrich_approval_item_with_attachments(
    item: dict[str, Any],
    attachment_service: FeishuApprovalAttachmentService,
) -> None:
    detail = item.get("instance_detail") if isinstance(item.get("instance_detail"), dict) else {}
    refs = approval_attachment_refs(item.get("form") or detail.get("form"))
    if not refs:
        item["attachment_ingest"] = {"total": 0, "read": 0, "failed": 0, "source": "sync"}
        return
    results = await attachment_service.read_attachment_refs(
        refs,
        instance_code=str(_approval_instance_code_for_sync(item) or ""),
        max_files=min(len(refs), 8),
    )
    item["attachment_refs"] = [_approval_attachment_ref_payload(ref) for ref in refs]
    item["attachment_read_results"] = [_approval_attachment_result_payload(result) for result in results]
    item["attachment_summary"] = _approval_attachment_summary(results)
    item["attachment_ingest"] = {
        "total": len(refs),
        "read": sum(1 for result in results if result.text_preview),
        "fetched": sum(1 for result in results if result.fetched),
        "failed": sum(1 for result in results if result.error),
        "source": "sync",
        "synced_at": datetime.now(UTC).isoformat(),
    }


def _enrich_approval_item_with_ai_advice(item: dict[str, Any]) -> None:
    detail = item.get("instance_detail") if isinstance(item.get("instance_detail"), dict) else {}
    form = item.get("form") or detail.get("form")
    fields = dict(approval_form_fields(form, max_fields=30))
    attachment_summary = str(item.get("attachment_summary") or "").strip()
    if not fields and not attachment_summary:
        return
    rule_conclusion, rule_reason = _sync_approval_rule_baseline(
        approval_name=str(item.get("approval_name") or (item.get("approval") or {}).get("name") or "审批"),
        fields=fields,
        amount=approval_amount(fields),
        attachment_summary=attachment_summary,
    )
    advice = generate_approval_llm_advice(
        approval_name=str(item.get("approval_name") or (item.get("approval") or {}).get("name") or "审批"),
        fields=fields,
        amount=approval_amount(fields),
        attachment_summary=attachment_summary,
        history=[],
        rule_conclusion=rule_conclusion,
        rule_reason=rule_reason,
    )
    if advice:
        item["approval_ai_advice"] = advice
        item["approval_ai_advice_synced_at"] = datetime.now(UTC).isoformat()


def _sync_approval_rule_baseline(
    *,
    approval_name: str,
    fields: dict[str, str],
    amount: float | None,
    attachment_summary: str,
) -> tuple[str, str]:
    text = f"{approval_name} {' '.join(fields.keys())} {' '.join(fields.values())}".lower()
    reason = first_matching_field(fields, ["付款事由", "借款事由", "用章事由", "报销事由", "申请事由", "事由", "用途"])
    if "借款" in text or "reserve fund" in text:
        return "可通过", f"借款用途为{reason or '已填写'}，需关注归还或抵扣闭环。"
    if attachment_summary:
        return "谨慎通过" if amount and amount >= 30000 else "可通过", "已读取附件摘要，可结合表单做预研判。"
    if "报销" in text or "用章" in text or "合同" in text or "协议" in text:
        return "先展开附件", "该类审批通常依赖附件摘要，当前摘要不足。"
    return "可通过", f"申请事由{reason or '已填写'}，未见明显异常。"


def _approval_attachment_ref_payload(ref: dict[str, str]) -> dict[str, str]:
    return {
        "field_name": str(ref.get("field_name") or ""),
        "name": str(ref.get("name") or ""),
        "type": str(ref.get("type") or ""),
        "url": str(ref.get("url") or ""),
        "has_token": bool(ref.get("token")),
    }


def _approval_attachment_result_payload(result: ApprovalAttachmentReadResult) -> dict[str, Any]:
    return {
        "name": result.name,
        "token": result.token,
        "content_type": result.content_type,
        "storage_bucket": result.storage_bucket,
        "storage_key": result.storage_key,
        "text_preview": result.text_preview,
        "error": result.error,
        "fetched": result.fetched,
        "downloaded": result.downloaded,
    }


def _approval_attachment_summary(results: list[ApprovalAttachmentReadResult]) -> str:
    previews = [" ".join(result.text_preview.split()) for result in results if result.text_preview]
    return "；".join(previews)[:1200]


def _combined_cursor(results: dict[str, Any]) -> dict[str, Any]:
    return {
        kind: {"has_more": result.get("has_more"), "page_token": result.get("page_token")}
        for kind, result in results.items()
        if isinstance(result, dict) and ("has_more" in result or "page_token" in result)
    }


def _bitable_record_to_index_event(
    app_config: FeishuAppConfig,
    *,
    app_token: str,
    table_id: str,
    item: dict[str, Any],
) -> WorkEventCreate:
    record_id = _first_present(item, ["record_id", "id"]) or _stable_item_id(item)
    fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
    key_fields = _bitable_key_fields(fields)
    field_names = sorted(str(key) for key in fields.keys())
    title = _bitable_record_title(record_id, key_fields)
    content = {
        "record_id": record_id,
        "table_id": table_id,
        "field_names": field_names[:80],
        "key_fields": key_fields,
    }
    return WorkEventCreate(
        company_id=app_config.company_id,
        source="feishu",
        event_type="feishu.bitable.master_data_index",
        external_id=f"bitable:{app_token}:{table_id}:{record_id}",
        title=title,
        content_text=json.dumps(content, ensure_ascii=False, default=str)[:3000],
        occurred_at=_parse_item_time(item),
        actors=[],
        labels=["feishu", "bitable", "master_data_index"],
        payload={
            "kind": "bitable",
            "data_layer": "master_data_index",
            "sync_action": "master_data_index",
            "query_path": "bitable_api",
            "mode": "master_data_index",
            "app_token": app_token,
            "table_id": table_id,
            "record_id": record_id,
            "field_names": field_names[:80],
            "key_fields": key_fields,
        },
    )


def _api_item_to_event(
    app_config: FeishuAppConfig,
    kind: str,
    item: dict[str, Any],
    *,
    item_context: dict[str, Any] | None = None,
) -> WorkEventCreate:
    item_id = (
        _approval_instance_code_for_sync(item)
        if kind == "approvals"
        else _first_present(
            item,
            [
                "message_id",
                "chat_id",
                "open_id",
                "user_id",
                "department_id",
                "event_id",
                "approval_instance_id",
                "message_id",
                "thread_id",
                "instance_code",
                "token",
                "file_token",
                "task_id",
                "guid",
                "id",
            ],
        )
    )
    context = item_context or {}
    title = _first_present(item, ["subject", "name", "title", "summary", "chat_name", "user_name", "en_name"])
    if kind == "approvals" and context.get("approval_name"):
        title = f"{context['approval_name']} - {title or _approval_instance_code_for_sync(item) or '审批实例'}"
    occurred_at = _parse_item_time(item)
    content = _compact_item_text({**context, **item} if context else item)
    return WorkEventCreate(
        company_id=app_config.company_id,
        source="feishu",
        event_type=f"feishu.{kind}.snapshot",
        external_id=f"{kind}:{item_id or _stable_item_id(item)}",
        thread_id=item.get("chat_id") or item.get("calendar_id") or item.get("thread_id"),
        title=title or f"飞书{kind}同步记录",
        content_text=content,
        occurred_at=occurred_at,
        actors=[],
        labels=["feishu", kind, "api_sync", *([str(context.get("approval_name"))] if kind == "approvals" and context.get("approval_name") else [])],
        payload={"kind": kind, "item": item, **({"context": context} if context else {})},
    )


def _extract_items_from_data(data: dict[str, Any], item_keys: list[str]) -> list[dict[str, Any]]:
    for key in item_keys:
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    if isinstance(data.get("item"), list):
        return [item for item in data["item"] if isinstance(item, dict)]
    return []


def _compact_item_text(item: dict[str, Any]) -> str:
    important = {
        key: value
        for key, value in item.items()
        if key
        in {
            "name",
            "title",
            "summary",
            "subject",
            "from",
            "to",
            "cc",
            "description",
            "status",
            "chat_name",
            "user_name",
            "email",
            "mobile",
            "start_time",
            "end_time",
            "create_time",
            "update_time",
            "approval_name",
            "instance_code",
        }
    }
    payload = important or item
    return json.dumps(payload, ensure_ascii=False, default=str)[:3000]


def _first_present(item: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        value = item.get(key)
        if value is not None and value != "":
            return str(value)
    return None


def _approval_instance_code_for_sync(item: dict[str, Any]) -> str | None:
    direct = _first_present(item, ["instance_code", "approval_instance_id", "process_code"])
    if direct:
        return direct
    instance = item.get("instance") if isinstance(item.get("instance"), dict) else {}
    return _first_present(instance, ["code", "instance_code", "approval_instance_id", "process_code"])


def _bitable_key_fields(fields: dict[str, Any]) -> dict[str, str]:
    if not fields:
        return {}
    preferred_markers = (
        "名称",
        "客户",
        "公司",
        "项目",
        "订单",
        "合同",
        "编号",
        "状态",
        "阶段",
        "负责人",
        "金额",
        "日期",
        "name",
        "title",
        "customer",
        "company",
        "project",
        "order",
        "contract",
        "status",
        "stage",
        "owner",
        "amount",
        "date",
    )
    selected: dict[str, str] = {}
    for key, value in fields.items():
        key_text = str(key)
        lowered = key_text.lower()
        if not any(marker in key_text or marker in lowered for marker in preferred_markers):
            continue
        selected[key_text] = _compact_bitable_value(value)
        if len(selected) >= 12:
            break
    if selected:
        return selected
    for key, value in list(fields.items())[:8]:
        selected[str(key)] = _compact_bitable_value(value)
    return selected


def _compact_bitable_value(value: Any) -> str:
    if isinstance(value, list):
        value = value[:5]
    if isinstance(value, dict):
        compact = {
            str(key): inner
            for key, inner in value.items()
            if str(key) in {"text", "name", "email", "open_id", "link", "url", "value"}
        }
        value = compact or ({"type": value.get("type")} if "type" in value else {})
    return json.dumps(value, ensure_ascii=False, default=str)[:160] if not isinstance(value, str) else value[:160]


def _bitable_record_title(record_id: str, key_fields: dict[str, str]) -> str:
    for key in ("名称", "客户名称", "项目名称", "订单编号", "合同编号", "name", "title"):
        if key_fields.get(key):
            return f"多维表格主数据索引：{key_fields[key]}"
    return f"多维表格主数据索引：{record_id}"


def _parse_item_time(item: dict[str, Any]) -> datetime:
    for key in ("update_time", "updated_at", "create_time", "created_at", "start_time"):
        value = item.get(key)
        if value is None:
            continue
        try:
            if isinstance(value, str) and value.isdigit():
                value = int(value)
            if isinstance(value, int | float):
                timestamp = value / 1000 if value > 10_000_000_000 else value
                return datetime.fromtimestamp(timestamp, tz=UTC)
            if isinstance(value, str):
                return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
        except (TypeError, ValueError):
            continue
    return datetime.now(UTC)


def _stable_item_id(item: dict[str, Any]) -> str:
    return str(abs(hash(json.dumps(item, ensure_ascii=False, sort_keys=True, default=str))))


def _rfc3339(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _plan_by_key(key: str) -> FeishuInfoPlan:
    for plan in FEISHU_INFO_PLANS:
        if plan.key == key:
            return plan
    raise KeyError(key)


def _safe_feishu_error(detail: Any) -> str:
    if isinstance(detail, dict):
        body = detail.get("body") if isinstance(detail.get("body"), dict) else {}
        message = body.get("msg") or body.get("message") or detail.get("message")
        code = body.get("code")
        if code is not None and message:
            return f"Feishu code {code}: {message}"
        if message:
            return str(message)
    return str(detail)[:300]


def _unique_strings(values: list[str]) -> list[str]:
    seen = set()
    result: list[str] = []
    for value in values:
        clean = str(value or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result
