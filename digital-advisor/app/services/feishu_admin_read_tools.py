import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.entities import FeishuAppConfig
from app.services.audit import write_audit_log
from app.services.agent.policies import BotActor
from app.services.feishu.approval import extract_approval_task_items
from app.services.feishu.cli_profile import feishu_app_cli_profile
from app.services.tools.base import ToolContext, ToolExecutionStatus, ToolRequest
from app.services.tools.router import execute_agent_tool


def admin_feishu_tool_context(
    db: Session,
    app_config: FeishuAppConfig,
    *,
    role: str = "admin",
    domains: tuple[str, ...] = (),
) -> ToolContext:
    return ToolContext(
        db=db,
        company_id=app_config.company_id,
        actor=BotActor(
            role=role,
            access_scope="company",
            domains=domains,
            display_name="feishu_admin_api",
        ),
        cli_profile=feishu_app_cli_profile(app_config),
    )


def execute_admin_feishu_read_tool(
    db: Session,
    app_config: FeishuAppConfig,
    *,
    tool_name: str,
    question: str,
    params: dict[str, Any],
    role: str = "admin",
    domains: tuple[str, ...] = (),
) -> dict[str, Any]:
    result = execute_agent_tool(
        admin_feishu_tool_context(db, app_config, role=role, domains=domains),
        ToolRequest(
            tool_name=tool_name,
            question=question,
            normalized_command=question,
            params={**params, "response_format": "raw_json"},
        ),
    )
    if result.status != ToolExecutionStatus.SUCCESS:
        raise HTTPException(status_code=502, detail={"tool_name": tool_name, "error": result.error})
    try:
        payload = json.loads(result.answer)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"{tool_name} returned non-JSON realtime payload") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail=f"{tool_name} returned non-object realtime payload")
    return payload


def cli_offset_from_page_token(page_token: str | None, *, endpoint: str) -> int | None:
    if page_token is None or not page_token.strip():
        return None
    if page_token.isdecimal():
        return int(page_token)
    raise HTTPException(
        status_code=400,
        detail=f"{endpoint} uses Feishu CLI offset pagination; page_token must be a numeric offset.",
    )


def fetch_admin_pending_approval_tasks(
    db: Session,
    app_config: FeishuAppConfig,
    *,
    open_id: str | None,
    limit: int,
) -> dict[str, Any]:
    if not open_id:
        return {"available": False, "items": [], "error": "缺少 open_id，无法精确查询待审批任务"}
    payload = execute_admin_feishu_read_tool(
        db,
        app_config,
        tool_name="feishu_approval_task_query",
        question="管理后台读取飞书待审批任务",
        domains=("approval",),
        params={
            "open_id": open_id,
            "topic": "1",
            "user_id_type": "open_id",
            "page_size": limit,
        },
    )
    data = payload.get("data") if isinstance(payload, dict) else {}
    items = extract_approval_task_items(data if isinstance(data, dict) else {})[:limit]
    normalize_admin_approval_task_items(items)
    attach_admin_approval_instance_details(db, app_config, items)
    return {"available": True, "items": items}


def pending_approval_tasks_payload(
    db: Session,
    app_config: FeishuAppConfig,
    *,
    open_id: str,
    limit: int,
) -> dict[str, Any]:
    result = fetch_admin_pending_approval_tasks(db, app_config, open_id=open_id, limit=limit)
    write_audit_log(
        db,
        action="feishu.approvals.pending",
        company_id=app_config.company_id,
        target_type="feishu_app",
        target_id=str(app_config.id),
        payload={"open_id": open_id, "limit": limit, "available": result.get("available")},
    )
    db.commit()
    return result


def contact_snapshot_payload(
    db: Session,
    app_config: FeishuAppConfig,
    data: Any,
) -> dict[str, Any]:
    result = execute_admin_feishu_read_tool(
        db,
        app_config,
        tool_name="feishu_contact_organization_snapshot",
        question="管理后台读取飞书通讯录组织快照",
        domains=("contact",),
        params={
            "root_department_id": data.root_department_id,
            "max_departments": data.max_departments,
            "max_users": data.max_users,
        },
    )
    write_audit_log(
        db,
        action="feishu.contacts.snapshot",
        company_id=app_config.company_id,
        target_type="feishu_app",
        target_id=str(app_config.id),
        payload={
            "department_count": result.get("department_count"),
            "user_count": result.get("user_count"),
            "available": result.get("available"),
        },
    )
    db.commit()
    return result


def normalize_admin_approval_task_items(items: list[dict[str, Any]]) -> None:
    for item in items:
        code = str(item.get("definition_code") or item.get("approval_code") or "").strip()
        if code:
            item.setdefault("approval_code", code)
            item.setdefault("approval_name", item.get("definition_name") or code)
        else:
            item.setdefault("approval_name", item.get("definition_name") or item.get("title") or "审批")
        instance_code = str(item.get("instance_code") or item.get("process_code") or "").strip()
        if instance_code:
            item.setdefault("process_code", instance_code)


def attach_admin_approval_instance_details(
    db: Session,
    app_config: FeishuAppConfig,
    items: list[dict[str, Any]],
) -> None:
    for item in items:
        instance_code = str(item.get("process_code") or item.get("instance_code") or "").strip()
        if not instance_code:
            continue
        payload = execute_admin_feishu_read_tool(
            db,
            app_config,
            tool_name="feishu_approval_instance_get",
            question="管理后台读取飞书审批实例详情",
            domains=("approval",),
            params={"instance_code": instance_code, "user_id_type": "open_id"},
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            continue
        item["instance_detail"] = data
        for key in ("serial_number", "approval_name", "start_time"):
            if data.get(key) and not item.get(key):
                item[key] = data[key]
