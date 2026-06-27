from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.routes.portal_request_models import PortalApprovalActionRequest, PortalApprovalDetailRequest, PortalPendingApprovalsRequest
from app.core.config import settings
from app.services.feishu import get_feishu_app_or_404
from app.services.feishu.approval import FeishuApprovalService
from app.services.feishu.cli_profile import feishu_app_cli_profile
from app.services.runtime_v5.feishu_resource_providers import _approval_item
from app.services.runtime_v5.context import load_portal_session_context, load_result_context, load_session_context
from app.services.runtime_v5.feishu_resource_providers import build_feishu_provider_registry
from app.services.runtime_v5.interaction_layer import interaction_payload_from_runtime_result, interaction_payload_payload
from app.services.runtime_v5.models import ResultContext, RuntimeContext, RuntimeIdentity, RuntimeProfile, RuntimeScope
from app.services.runtime_v5.runtime import run_runtime_v5
from app.services.runtime_v5.runtime_action_input import build_runtime_action_input_payload
from app.services.runtime_v5.runtime_result import cached_result_context_runtime_result_payload, runtime_result_from_payload


def portal_pending_approvals_payload(
    db: Session,
    data: PortalPendingApprovalsRequest,
    x_admin_token: str | None,
) -> dict[str, Any]:
    _require_portal_session(
        chat_id=data.chat_id,
        app_config_id=data.app_config_id,
        open_id=data.open_id,
        x_admin_token=x_admin_token,
    )
    app_config = get_feishu_app_or_404(db, data.app_config_id)
    envelope = run_runtime_v5(
        context=_portal_runtime_context(
            app_config=app_config,
            open_id=data.open_id,
            chat_id=data.chat_id,
            message=f"Portal 查询待我审批，最多 {data.limit} 条",
        ),
        providers=build_feishu_provider_registry(db=db, cli_profile=feishu_app_cli_profile(app_config)),
    )
    runtime_result = envelope.composed.metadata.get("runtime_result") if isinstance(envelope.composed.metadata, dict) else {}
    interaction_payload = _portal_interaction_payload(runtime_result if isinstance(runtime_result, dict) else {})
    result_context_metadata = interaction_payload.metadata.get("result_context") if isinstance(interaction_payload.metadata.get("result_context"), dict) else {}
    return {
        "available": interaction_payload.status == "success",
        "items": list(interaction_payload.items),
        "answer": interaction_payload.summary or envelope.composed.answer,
        "error": str(result_context_metadata.get("error") or ""),
        "metadata": {
            **result_context_metadata,
            "runtime_version": "v5",
            "runtime_status": interaction_payload.status or "not_executed",
            "interaction_payload": interaction_payload_payload(interaction_payload),
        },
    }


def portal_cached_approvals_payload(
    data: PortalPendingApprovalsRequest,
    x_admin_token: str | None,
) -> dict[str, Any]:
    _require_portal_session(
        chat_id=data.chat_id,
        app_config_id=data.app_config_id,
        open_id=data.open_id,
        x_admin_token=x_admin_token,
    )
    result_context = load_result_context(data.chat_id)
    if result_context is None or result_context.result_type not in {"approval_list", "approval_query"}:
        return {"available": False, "items": [], "answer": "", "metadata": {}}
    runtime_result = cached_result_context_runtime_result_payload(result_context)
    interaction_payload = _portal_interaction_payload(runtime_result)
    result_context_metadata = interaction_payload.metadata.get("result_context") if isinstance(interaction_payload.metadata.get("result_context"), dict) else {}
    return {
        "available": interaction_payload.status == "success",
        "items": list(interaction_payload.items),
        "answer": interaction_payload.summary or result_context.answer,
        "metadata": {
            **result_context_metadata,
            "runtime_version": "v5",
            "runtime_status": interaction_payload.status or "not_executed",
            "interaction_payload": interaction_payload_payload(interaction_payload),
        },
    }


def portal_approval_detail_payload(
    db: Session,
    data: PortalApprovalDetailRequest,
    x_admin_token: str | None,
) -> dict[str, Any]:
    _require_portal_session(
        chat_id=data.chat_id,
        app_config_id=data.app_config_id,
        open_id=data.open_id,
        x_admin_token=x_admin_token,
    )
    instance_code = str(data.instance_code or "").strip()
    if not instance_code:
        return {"available": False, "item": {}, "error": "missing_instance_code", "metadata": {}}
    app_config = get_feishu_app_or_404(db, data.app_config_id)
    detail = _run_portal_async(
        FeishuApprovalService(app_config).get_instance(
            instance_code=instance_code,
            user_id_type="open_id",
        )
    )
    raw_detail = detail.get("data") if isinstance(detail.get("data"), dict) else detail
    if not isinstance(raw_detail, dict):
        return {"available": False, "item": {}, "error": "invalid_detail_payload", "metadata": {}}
    raw_item = {
        "instance_code": instance_code,
        "process_code": instance_code,
        "task_id": data.task_id or "",
        "instance_detail": raw_detail,
    }
    for key in ("approval_code", "definition_code", "approval_name", "definition_name", "serial_number", "status"):
        value = raw_detail.get(key)
        if value not in (None, "", [], {}):
            raw_item[key] = value
    return {
        "available": True,
        "item": _approval_item(raw_item),
        "error": "",
        "metadata": {
            "runtime_version": "v5",
            "source": "feishu_live_instance_detail",
            "instance_code": instance_code,
        },
    }


def portal_bootstrap_payload(chat_id: str | None = None) -> dict[str, Any]:
    portal = load_portal_session_context(chat_id)
    if not portal:
        session_context = load_session_context(chat_id)
        portal = session_context.get("portal") if isinstance(session_context.get("portal"), dict) else {}
    return {
        "available": bool(portal.get("app_config_id") and portal.get("open_id")),
        "app_config_id": portal.get("app_config_id") or "",
        "company_id": portal.get("company_id") or "",
        "open_id": portal.get("open_id") or "",
        "display_name": portal.get("display_name") or "",
        "role": portal.get("role") or "",
        "access_scope": portal.get("access_scope") or "",
    }


def portal_result_context_payload(chat_id: str | None) -> dict[str, Any]:
    result_context = load_result_context(chat_id)
    if result_context is None or not result_context.items:
        return {"available": False, "items": [], "answer": "", "metadata": {}}
    metadata = result_context.metadata if isinstance(result_context.metadata, dict) else {}
    entity_domain = str(metadata.get("entity_domain") or _portal_entity_domain(result_context.result_type))
    canonical_items = tuple(_canonical_result_context_item(item, entity_domain=entity_domain) for item in result_context.items)
    sidepanel_context = _result_context_sidepanel_context(result_context)
    visible_fields = tuple(sidepanel_context.get("visible_fields") or _visible_result_context_fields(canonical_items))
    items = [_portal_result_context_item(item, visible_fields=visible_fields) for item in canonical_items]
    return {
        "available": True,
        "items": items,
        "answer": result_context.answer,
        "metadata": {
            "runtime_version": "v5",
            "result_type": result_context.result_type,
            "query_id": result_context.query_id,
            "context_kind": metadata.get("context_kind") or "query_result",
            "sidepanel_context": sidepanel_context,
            "item_count": len(items),
            "field_projection": metadata.get("field_projection") or "",
            "display_offset": metadata.get("display_offset", 0),
            "display_end": metadata.get("display_end", min(len(items), 20)),
            "display_limit": metadata.get("display_limit", 20),
            "has_more": bool(metadata.get("has_more")),
        },
    }


def _result_context_sidepanel_context(result_context: ResultContext) -> dict[str, Any]:
    metadata = result_context.metadata if isinstance(result_context.metadata, dict) else {}
    result_type = str(result_context.result_type or "")
    entity_domain = str(metadata.get("entity_domain") or _portal_entity_domain(result_type))
    canonical_items = tuple(_canonical_result_context_item(item, entity_domain=entity_domain) for item in result_context.items)
    visible_fields = _visible_result_context_fields(canonical_items)
    total = len(result_context.items)
    display_limit = _positive_int(metadata.get("display_limit"), default=20)
    display_offset = _non_negative_int(metadata.get("display_offset"), default=0)
    display_end = _non_negative_int(metadata.get("display_end"), default=min(total, display_limit))
    display_end = min(max(display_end, display_offset), total)
    return {
        "available": True,
        "kind": "result_context",
        "route": "/sidepanel",
        "presentation": "table_detail",
        "result_type": result_type,
        "entity_domain": entity_domain,
        "item_count": total,
        "display_offset": display_offset,
        "display_end": display_end,
        "display_limit": display_limit,
        "has_more": bool(metadata.get("has_more")) or display_end < total,
        "field_projection": str(metadata.get("field_projection") or ""),
        "visible_fields": list(visible_fields),
        "title": _portal_result_context_title(result_type=result_type, entity_domain=entity_domain),
    }


def _portal_result_context_item(item: dict[str, Any], *, visible_fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: _display_value(item.get(field)) for field in visible_fields if not _empty_display_value(item.get(field))}


def _canonical_result_context_item(item: dict[str, Any], *, entity_domain: str) -> dict[str, Any]:
    if entity_domain != "people":
        return item
    canonical: dict[str, Any] = {}
    name = item.get("name")
    title = item.get("title") or item.get("job_title")
    department = item.get("department") or item.get("department_names") or item.get("departments")
    mobile = item.get("mobile") or item.get("phone")
    email = item.get("email")
    gender = item.get("gender")
    if "gender_source" in item and str(item.get("gender_source") or "").strip() != "source":
        gender = ""
    for key, value in (
        ("name", name),
        ("title", title),
        ("department", department),
        ("mobile", mobile),
        ("email", email),
        ("gender", gender),
    ):
        if not _empty_display_value(value):
            canonical[key] = _display_value(value)
    return canonical


def _visible_result_context_fields(items: tuple[dict[str, Any], ...]) -> tuple[str, ...]:
    fields: list[str] = []
    for item in items[:20]:
        for key, value in item.items():
            field = str(key)
            if field in fields or _internal_result_context_field(field) or _empty_display_value(value):
                continue
            fields.append(field)
    return tuple(fields[:12])


def _internal_result_context_field(field: str) -> bool:
    normalized = field.strip().lower()
    return (
        normalized == "raw"
        or normalized.startswith("_")
        or normalized
        in {
            "open_id",
            "union_id",
            "user_id",
            "company_id",
            "allowed_user_ids",
            "source_object_id",
            "source_event_ids",
            "department_id",
            "department_ids",
            "department_id_list",
            "gender_source",
            "title_source",
            "source_system",
            "resource_plane",
            "resource_type",
            "index",
        }
        or normalized.endswith("_open_id")
        or normalized.endswith("_user_id")
        or normalized.endswith("_company_id")
        or normalized.endswith("_source")
    )


def _display_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        values: list[str] = []
        for item in value:
            text = str(item).strip()
            if not text or text in values:
                continue
            values.append(text)
            if len(values) >= 5:
                break
        return "、".join(values)
    if isinstance(value, dict):
        return "、".join(f"{key}: {val}" for key, val in list(value.items())[:5] if val not in (None, ""))
    return str(value)


def _empty_display_value(value: Any) -> bool:
    return value is None or value == "" or value == () or value == [] or value == {}


def _portal_entity_domain(result_type: str) -> str:
    if result_type in {"people_search", "department_members", "organization_snapshot"}:
        return "people"
    if result_type in {"task_list", "task_query"}:
        return "workspace"
    if result_type.startswith("mail"):
        return "communication"
    if result_type.startswith("knowledge"):
        return "knowledge"
    return ""


def _portal_result_context_title(*, result_type: str, entity_domain: str) -> str:
    if entity_domain == "people":
        return "人员明细"
    if entity_domain == "workspace":
        return "任务明细"
    if entity_domain == "communication":
        return "消息/邮件明细"
    if entity_domain == "knowledge":
        return "知识资料"
    return "结果明细"


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _non_negative_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(parsed, 0)


def portal_approval_action_payload(
    db: Session,
    data: PortalApprovalActionRequest,
    x_admin_token: str | None,
) -> dict[str, Any]:
    _require_portal_session(
        chat_id=data.chat_id,
        app_config_id=data.app_config_id,
        open_id=data.open_id,
        x_admin_token=x_admin_token,
    )
    app_config = get_feishu_app_or_404(db, data.app_config_id)
    action_message = "同意这个审批" if data.action == "approve" else "拒绝这个审批"
    result_context = _approval_action_result_context(data, company_id=str(app_config.company_id))
    session_context = load_session_context(data.chat_id)
    session_context = {
        **session_context,
        "runtime_v5_action_input": build_runtime_action_input_payload(
            action_id=data.confirmation_token or "",
            action_type=data.action,
            message=action_message,
            intent=f"approval_{data.action}",
            strategy=f"approval_{data.action}",
            target={
                "approval_code": data.approval_code,
                "instance_code": data.instance_code,
                "task_id": data.task_id,
            },
            confirmed=bool(data.confirmed),
            confirmation_token=data.confirmation_token or "",
            company_id=str(app_config.company_id),
            chat_id=data.chat_id or "",
            open_id=data.open_id,
            source_ui="portal",
            sources=["approval"],
        ),
    }
    envelope = run_runtime_v5(
        context=_portal_runtime_context(
            app_config=app_config,
            open_id=data.open_id,
            chat_id=data.chat_id,
            message=action_message,
            session_context=session_context,
            result_context=result_context,
        ),
        providers=build_feishu_provider_registry(db=db, cli_profile=feishu_app_cli_profile(app_config)),
    )
    runtime_result = envelope.composed.metadata.get("runtime_result") if isinstance(envelope.composed.metadata, dict) else {}
    interaction_payload = _portal_interaction_payload(runtime_result if isinstance(runtime_result, dict) else {})
    result_context_metadata = interaction_payload.metadata.get("result_context") if isinstance(interaction_payload.metadata.get("result_context"), dict) else {}
    return {
        "ok": interaction_payload.status == "success",
        "status": interaction_payload.status or "waiting",
        "answer": interaction_payload.summary or envelope.composed.answer,
        "error": str(result_context_metadata.get("error") or ""),
        "metadata": {
            "runtime_version": "v5",
            "requires_confirmation": bool(interaction_payload.metadata.get("requires_confirmation")),
            "interaction_payload": interaction_payload_payload(interaction_payload),
            "runtime_result": runtime_result if isinstance(runtime_result, dict) else {},
            "result_context": result_context_metadata,
        },
    }


def _require_portal_session(
    *,
    chat_id: str | None,
    app_config_id,
    open_id: str,
    x_admin_token: str | None,
) -> None:
    if settings.admin_api_token and x_admin_token == settings.admin_api_token:
        return
    if not settings.admin_api_token and not chat_id:
        return
    portal = load_portal_session_context(chat_id)
    if not portal:
        session_context = load_session_context(chat_id)
        portal = session_context.get("portal") if isinstance(session_context.get("portal"), dict) else {}
    if str(portal.get("app_config_id") or "") != str(app_config_id) or str(portal.get("open_id") or "") != open_id:
        raise HTTPException(status_code=403, detail="Portal 会话身份已失效，请从机器人卡片重新打开。")


def _portal_interaction_payload(runtime_result: dict[str, Any]):
    return interaction_payload_from_runtime_result(runtime_result_from_payload(runtime_result))


def _portal_runtime_context(
    *,
    app_config,
    open_id: str,
    chat_id: str | None,
    message: str,
    session_context: dict[str, Any] | None = None,
    result_context: ResultContext | None = None,
) -> RuntimeContext:
    return RuntimeContext(
        identity=RuntimeIdentity(open_id=open_id, role="employee"),
        runtime_scope=RuntimeScope(
            scope_type="single_company",
            company_ids=(app_config.company_id,),
            active_company_id=app_config.company_id,
        ),
        current_message=message,
        session_context=session_context or {},
        result_context=result_context,
        chat_id=chat_id,
        profile=RuntimeProfile(),
    )


def _approval_action_result_context(data: PortalApprovalActionRequest, *, company_id: str) -> ResultContext:
    item = {
        "title": data.approval_code or data.instance_code or "审批单",
        "approval_code": data.approval_code,
        "process_code": data.approval_code,
        "instance_code": data.instance_code,
        "task_id": data.task_id,
        "raw": {
            "approval_code": data.approval_code,
            "process_code": data.approval_code,
            "instance_code": data.instance_code,
            "task_id": data.task_id,
        },
    }
    return ResultContext(
        result_type="approval_detail",
        query_id=f"portal:approval:{data.instance_code}:{data.task_id}",
        count=1,
        items=(item,),
        metadata={
            "company_id": company_id,
            "source": "portal",
            "context_kind": "query_result",
            "item_count": 1,
        },
        answer="Portal 审批动作上下文",
    )


def _run_portal_async(coro):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(coro)).result()
