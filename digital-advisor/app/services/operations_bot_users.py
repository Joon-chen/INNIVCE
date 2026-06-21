from types import SimpleNamespace
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.serialization import json_safe
from app.models.entities import AuditLog, BotUserAccess, FeishuAppConfig, WorkEvent
from app.services.agent.reply_modes import reply_mode_catalog
from app.services.permissions import DOMAIN_RULES, infer_permission_profile, merge_permission_settings
from app.services.tools.base import (
    DATA_BOUNDARY_POLICY,
    DIGITAL_ADVISOR_PERMISSION_POLICY,
    ENTERPRISE_IDENTITY_CONSTRAINTS,
    ENTERPRISE_IDENTITY_RESOURCE_OWNER,
    USER_IDENTITY_BUNDLE_RESOURCE,
    USER_IDENTITY_CONSTRAINTS,
    USER_IDENTITY_RESOURCES,
    SHARED_TOOL_COUNT,
    identity_permission_contract,
)
from app.services.user_identity_authorizations import user_identity_authorization_url_status


USER_IDENTITY_RESOURCE_LABELS = {
    USER_IDENTITY_BUNDLE_RESOURCE: "用户级能力包",
    "personal_feishu": "个人飞书",
    "external_mail": "其他邮箱",
    "personal_dingtalk": "个人钉钉",
    "personal_wechat": "个人微信",
}

USER_IDENTITY_RESOURCE_TYPES = tuple(
    (resource_type, USER_IDENTITY_RESOURCE_LABELS[resource_type]) for resource_type in USER_IDENTITY_RESOURCES
)

class BotUserAccessCreate(BaseModel):
    company_id: UUID
    open_id: str
    display_name: str | None = None
    role: str = "member"
    access_scope: str = "chat"
    is_active: bool = True
    settings: dict[str, Any] = Field(default_factory=dict)


class RecalculateBotPermissionsRequest(BaseModel):
    company_id: UUID
    dry_run: bool = True
    update_manual_admins: bool = False


def upsert_bot_user_access_from_request(db: Session, data: BotUserAccessCreate) -> dict[str, Any]:
    return upsert_bot_user_access(
        db,
        company_id=data.company_id,
        open_id=data.open_id,
        display_name=data.display_name,
        role=data.role,
        access_scope=data.access_scope,
        is_active=data.is_active,
        settings_payload=data.settings,
    )


def recalculate_bot_user_permissions_from_request(
    db: Session,
    data: RecalculateBotPermissionsRequest,
) -> dict[str, Any]:
    return recalculate_bot_user_permissions(
        db,
        company_id=data.company_id,
        dry_run=data.dry_run,
        update_manual_admins=data.update_manual_admins,
    )


def upsert_bot_user_access(
    db: Session,
    *,
    company_id: UUID,
    open_id: str,
    display_name: str | None = None,
    role: str = "member",
    access_scope: str = "chat",
    is_active: bool = True,
    settings_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    user = db.scalar(
        select(BotUserAccess)
        .where(BotUserAccess.company_id == company_id)
        .where(BotUserAccess.open_id == open_id)
    )
    if not user:
        user = BotUserAccess(company_id=company_id, open_id=open_id)
        db.add(user)
    user.display_name = display_name
    user.role = role
    user.access_scope = access_scope
    user.is_active = is_active
    user.settings = json_safe(settings_payload or {})
    db.commit()
    db.refresh(user)
    return {"id": str(user.id), "role": user.role, "access_scope": user.access_scope}


def list_bot_user_access(db: Session, *, company_id: UUID | None = None) -> dict[str, Any]:
    query = select(BotUserAccess).order_by(BotUserAccess.created_at.desc())
    if company_id:
        query = query.where(BotUserAccess.company_id == company_id)
    access_items = list(db.scalars(query).all())
    recent_executions = recent_tool_executions_by_actor(
        db,
        company_id=company_id,
        open_ids={item.open_id for item in access_items if item.open_id},
    )
    recent_gateway_messages = recent_gateway_messages_by_actor(
        db,
        company_id=company_id,
        open_ids={item.open_id for item in access_items if item.open_id},
    )
    db_items = [
        bot_user_access_payload(
            item,
            latest_tool_execution=recent_executions.get(item.open_id),
            latest_gateway_message=recent_gateway_messages.get(item.open_id),
        )
        for item in access_items
    ]
    env_items = _env_bot_admin_items(db, company_id=company_id, existing_open_ids={item["open_id"] for item in db_items})
    return {"items": db_items + env_items}


def bot_user_access_payload(
    item: BotUserAccess,
    *,
    latest_tool_execution: dict[str, Any] | None = None,
    latest_gateway_message: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings_data = item.settings or {}
    agent_profile = settings_data.get("agent_profile") if isinstance(settings_data.get("agent_profile"), dict) else {}
    user_identity_authorizations = user_identity_authorizations_payload(
        settings_data,
        company_id=item.company_id,
        owner_open_id=item.open_id,
    )
    agent_status = agent_profile.get("status") or ("active" if item.is_active else "disabled")
    agent_scope = agent_profile.get("scope") or item.access_scope
    agent_entrypoint = agent_profile.get("entrypoint") or "feishu_bot"
    agent_requires_feishu_app_config = bool(agent_profile.get("requires_feishu_app_config"))
    return {
        "id": str(item.id),
        "company_id": str(item.company_id),
        "open_id": item.open_id,
        "display_name": item.display_name,
        "role": item.role,
        "access_scope": item.access_scope,
        "agent_title": f"{item.display_name or item.open_id} 的大飞哥",
        "agent_status": agent_status,
        "agent_scope": agent_scope,
        "agent_entrypoint": agent_entrypoint,
        "agent_activated_at": agent_profile.get("activated_at"),
        "agent_first_chat_id": agent_profile.get("first_chat_id"),
        "agent_first_message_id": agent_profile.get("first_message_id"),
        "agent_activation_source": agent_profile.get("activation_source") or settings_data.get("source"),
        "agent_requires_feishu_app_config": agent_requires_feishu_app_config,
        "agent_launch_summary": (
            "待绑定公司飞书 AppConfig 后接入真实飞书消息入口"
            if agent_requires_feishu_app_config
            else "飞书消息入口按当前 AppConfig 状态运行"
        ),
        "agent_contract": "每个用户一个 Agent；Tool 全局共享；数据按身份边界收紧",
        "is_active": item.is_active,
        "domains": ", ".join(settings_data.get("permission_domains") or []),
        "resources": "、".join((settings_data.get("allowed_resources") or [])[:4]),
        "global_shared_tools": ["ApprovalTool","KnowledgeTool","BitableTool","ChatTool","CalendarTool","MeetingTool","ReportTool","AutomationTool","PeopleTool"],
        "global_shared_tool_count": SHARED_TOOL_COUNT,
        "global_shared_tool_summary": f"{SHARED_TOOL_COUNT} 个共享业务 Tool",
        "global_shared_tool_statuses": global_shared_tool_statuses(),
        "agent_can_call_all_business_tools": True,
        "tool_access_policy": "all_business_tools_shared",
        "tool_sharing_model": "shared_business_tools_per_employee_agent",
        "identity_permission_contract": identity_permission_contract(user_identity_required=True),
        "entrypoint_chain": (
            "飞书消息入口 -> Message Gateway -> Agent Runtime -> Tool Router -> Tool -> 数据源/执行源 -> "
            "Agent Runtime -> 回复用户"
        ),
        "runtime_contract": {
            "tool_decides_data_or_execution_source": True,
            "tool_returns_structured_result": True,
            "final_answer_owner": "agent_runtime",
            "agent_runtime_direct_cli_or_api_access": False,
        },
        "reply_mode_summary": reply_mode_summary(),
        "reply_modes": reply_mode_catalog(),
        "enterprise_identity_boundary": enterprise_identity_boundary_summary(item),
        "enterprise_identity_constraints": list(ENTERPRISE_IDENTITY_CONSTRAINTS),
        "enterprise_resource_boundary": {
            "identity": "app_identity",
            "resource_owner": ENTERPRISE_IDENTITY_RESOURCE_OWNER,
            "constraints": list(ENTERPRISE_IDENTITY_CONSTRAINTS),
            "company_scope": str(item.company_id),
            "role_scope": {
                "role": item.role,
                "access_scope": item.access_scope,
                "domains": settings_data.get("permission_domains") or [],
            },
            "can_exceed_feishu_app_permissions": False,
        },
        "user_identity_boundary": user_identity_boundary_summary(user_identity_authorizations),
        "user_identity_constraints": list(USER_IDENTITY_CONSTRAINTS),
        "user_identity_supported_resources": list(USER_IDENTITY_RESOURCES),
        "user_resource_boundary": {
            "identity": "resource_owner_identity",
            "supported_resources": list(USER_IDENTITY_RESOURCES),
            "constraints": list(USER_IDENTITY_CONSTRAINTS),
            "can_exceed_original_authorization": False,
        },
        "data_boundary_policy": DATA_BOUNDARY_POLICY,
        "data_permission_policy": "只能收紧，不能突破 App Identity 或 User Identity",
        "digital_advisor_permission_policy": DIGITAL_ADVISOR_PERMISSION_POLICY,
        "cannot_escalate_original_permissions": True,
        "user_identity_authorizations": user_identity_authorizations,
        "user_identity_summary": user_identity_authorization_summary(user_identity_authorizations),
        "authorized_user_resource_count": sum(
            1 for authorization in user_identity_authorizations if authorization.get("needs_authorization") is False
        ),
        "pending_user_resource_count": sum(
            1 for authorization in user_identity_authorizations if authorization.get("needs_authorization") is True
        ),
        "personal_authorization_next_action": personal_authorization_next_action(user_identity_authorizations),
        "agent_next_action": agent_next_action(agent_status=agent_status, authorizations=user_identity_authorizations),
        "latest_gateway_message": latest_gateway_message,
        "latest_gateway_message_summary": latest_gateway_message_summary(latest_gateway_message),
        "latest_gateway_status": (latest_gateway_message or {}).get("status"),
        "latest_gateway_route_path": (latest_gateway_message or {}).get("route_path"),
        "latest_gateway_reply_mode_label": (latest_gateway_message or {}).get("reply_mode_label"),
        "latest_gateway_used_agent_runtime": (latest_gateway_message or {}).get("used_agent_runtime"),
        "latest_gateway_final_answer_owner": (latest_gateway_message or {}).get("final_answer_owner"),
        "latest_gateway_tool_summary": latest_gateway_tool_summary(latest_gateway_message),
        "latest_gateway_authorization_summary": latest_gateway_authorization_summary(latest_gateway_message),
        "latest_gateway_thinking_notice_sent": (latest_gateway_message or {}).get("thinking_notice_sent"),
        "latest_gateway_authorization_card_sent": (latest_gateway_message or {}).get("authorization_card_sent"),
        "latest_gateway_authorization_required": (latest_gateway_message or {}).get(
            "user_identity_authorization_required"
        ),
        "latest_gateway_required_user_identity_resources": (latest_gateway_message or {}).get(
            "required_user_identity_resources"
        )
        or [],
        "latest_gateway_authorization_owner_open_id": (latest_gateway_message or {}).get("authorization_owner_open_id"),
        "latest_gateway_authorization_action_count": (latest_gateway_message or {}).get("authorization_action_count"),
        "latest_gateway_created_at": (latest_gateway_message or {}).get("created_at"),
        "latest_tool_execution": latest_tool_execution,
        "latest_tool_execution_summary": latest_tool_execution_summary(latest_tool_execution),
        "latest_tool_business_tool": (latest_tool_execution or {}).get("business_tool"),
        "latest_tool_status": (latest_tool_execution or {}).get("status"),
        "latest_tool_source": latest_tool_source_summary(latest_tool_execution),
        "latest_tool_final_answer_owner": (latest_tool_execution or {}).get("final_answer_owner"),
        "latest_tool_data_permission_model": (latest_tool_execution or {}).get("data_permission_model"),
        "latest_tool_enterprise_identity_boundary": (latest_tool_execution or {}).get("enterprise_identity_boundary"),
        "latest_tool_user_identity_boundary": (latest_tool_execution or {}).get("user_identity_boundary"),
        "latest_tool_cannot_escalate_original_permissions": (latest_tool_execution or {}).get(
            "cannot_escalate_original_permissions"
        ),
        "latest_tool_cli_profile": (latest_tool_execution or {}).get("cli_profile"),
        "latest_tool_created_at": (latest_tool_execution or {}).get("created_at"),
        "source": settings_data.get("source") or "database",
    }


def global_shared_tool_statuses() -> list[dict[str, Any]]:
    return [
        {
            "business_tool": tool_name,
            "availability": "shared",
            "agent_can_call": True,
            "data_boundary": "identity_scoped_tighten_only",
        }
        for tool_name in ["ApprovalTool","KnowledgeTool","BitableTool","ChatTool","CalendarTool","MeetingTool","ReportTool","AutomationTool","PeopleTool"]
    ]


def recent_tool_executions_by_actor(
    db: Session,
    *,
    company_id: UUID | None,
    open_ids: set[str],
) -> dict[str, dict[str, Any]]:
    if not open_ids:
        return {}
    query = select(AuditLog).where(AuditLog.target_type == "tool").order_by(AuditLog.created_at.desc()).limit(500)
    if company_id:
        query = query.where(AuditLog.company_id == company_id)
    results: dict[str, dict[str, Any]] = {}
    for log in db.scalars(query).all():
        actor = str(log.actor or "").strip()
        if actor not in open_ids or actor in results:
            continue
        results[actor] = latest_tool_execution_payload(log)
    return results


def recent_gateway_messages_by_actor(
    db: Session,
    *,
    company_id: UUID | None,
    open_ids: set[str],
) -> dict[str, dict[str, Any]]:
    if not open_ids:
        return {}
    query = (
        select(AuditLog)
        .where(AuditLog.action == "gateway.feishu.message")
        .where(AuditLog.target_type == "gateway_message")
        .order_by(AuditLog.created_at.desc())
        .limit(500)
    )
    if company_id:
        query = query.where(AuditLog.company_id == company_id)
    results: dict[str, dict[str, Any]] = {}
    for log in db.scalars(query).all():
        actor = str(log.actor or "").strip()
        if actor not in open_ids or actor in results:
            continue
        results[actor] = latest_gateway_message_payload(log)
    return results


def latest_gateway_message_payload(log: AuditLog) -> dict[str, Any]:
    payload = log.payload or {}
    return {
        "message_id": payload.get("message_id") or log.target_id,
        "chat_id": payload.get("chat_id"),
        "chat_type": payload.get("chat_type"),
        "status": payload.get("status"),
        "handled": payload.get("handled"),
        "used_agent_runtime": payload.get("used_agent_runtime"),
        "final_answer_owner": payload.get("final_answer_owner"),
        "route_path": payload.get("route_path"),
        "route_label": payload.get("route_label"),
        "reply_mode_id": payload.get("reply_mode_id"),
        "reply_mode_label": payload.get("reply_mode_label"),
        "reply_mode_data_requirement": payload.get("reply_mode_data_requirement"),
        "thinking_notice_sent": payload.get("thinking_notice_sent"),
        "authorization_card_sent": payload.get("user_identity_card_sent"),
        "user_identity_authorization_required": payload.get("user_identity_required"),
        "user_identity_authorization_actions": payload.get("user_identity_action_summaries") or [],
        "required_user_identity_resources": payload.get("user_identity_required_resources") or [],
        "authorization_owner_open_id": payload.get("user_identity_owner_open_id"),
        "authorization_action_count": payload.get("user_identity_action_count"),
        "agent_runtime_step_count": payload.get("agent_runtime_step_count"),
        "agent_runtime_tool_steps": payload.get("agent_runtime_tool_steps") or [],
        "gateway_chain": payload.get("gateway_chain") or [],
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }


def latest_tool_execution_payload(log: AuditLog) -> dict[str, Any]:
    payload = log.payload or {}
    return {
        "tool_name": log.target_id,
        "business_tool": payload.get("business_tool"),
        "status": payload.get("status"),
        "provider": payload.get("provider"),
        "data_source": payload.get("data_source"),
        "execution_source": payload.get("execution_source"),
        "source_chain": payload.get("source_chain") or [],
        "source_kind": payload.get("source_kind"),
        "tool_returns_structured_result": payload.get("tool_returns_structured_result"),
        "final_answer_owner": payload.get("final_answer_owner"),
        "data_permission_model": payload.get("data_permission_model"),
        "enterprise_identity_boundary": payload.get("enterprise_identity_boundary"),
        "user_identity_boundary": payload.get("user_identity_boundary"),
        "cannot_escalate_original_permissions": payload.get("cannot_escalate_original_permissions"),
        "cli_profile": payload.get("cli_profile"),
        "cli_profile_source": payload.get("cli_profile_source"),
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }


def latest_tool_execution_summary(execution: dict[str, Any] | None) -> str:
    if not execution:
        return "暂无 Tool 调用证据"
    business_tool = execution.get("business_tool") or execution.get("tool_name") or "Tool"
    status = execution.get("status") or "unknown"
    source = latest_tool_source_summary(execution)
    return f"{business_tool} · {status} · {source}"


def latest_gateway_message_summary(message: dict[str, Any] | None) -> str:
    if not message:
        return "暂无飞书消息入口证据"
    route = message.get("route_label") or message.get("route_path") or "未路由"
    mode = message.get("reply_mode_label") or message.get("reply_mode_id") or "未识别回复模式"
    status = message.get("status") or "unknown"
    return f"{status} · {route} · {mode}"


def latest_gateway_authorization_summary(message: dict[str, Any] | None) -> str:
    if not message or not message.get("user_identity_authorization_required"):
        return "无需个人资源授权"
    resources = message.get("required_user_identity_resources")
    resource_text = "、".join(str(item) for item in resources) if isinstance(resources, list) else "个人资源"
    owner = message.get("authorization_owner_open_id") or "资源所有者本人"
    count = message.get("authorization_action_count") or 0
    return f"{resource_text} · owner={owner} · {count} 个授权入口"


def latest_gateway_tool_summary(message: dict[str, Any] | None) -> str:
    if not message:
        return "-"
    steps = message.get("agent_runtime_tool_steps")
    if not isinstance(steps, list) or not steps:
        return "无 Tool 调用"
    names = [str(step.get("business_tool") or step.get("name") or "Tool") for step in steps if isinstance(step, dict)]
    return " / ".join(names) if names else "无 Tool 调用"


def latest_tool_source_summary(execution: dict[str, Any] | None) -> str:
    if not execution:
        return "-"
    return str(execution.get("execution_source") or execution.get("data_source") or "无企业数据")


def reply_mode_summary() -> str:
    return "Fast（无企业数据） / Normal（实时单 Tool） / Thinking（WorkEvent、Knowledge、Memory、多 Tool 分析）"


def user_identity_authorizations_payload(
    settings_data: dict[str, Any],
    *,
    company_id: UUID | None = None,
    owner_open_id: str | None = None,
) -> list[dict[str, Any]]:
    raw = settings_data.get("user_identity_authorizations")
    configured = raw if isinstance(raw, dict) else {}
    bundle = configured.get(USER_IDENTITY_BUNDLE_RESOURCE) if isinstance(configured.get(USER_IDENTITY_BUNDLE_RESOURCE), dict) else {}
    legacy_authorized = next(
        (
            value
            for resource_type, _label in USER_IDENTITY_RESOURCE_TYPES
            if isinstance((value := configured.get(resource_type)), dict)
            and str(value.get("status") or "") in {"authorized", "connected"}
        ),
        None,
    )
    value = bundle or legacy_authorized or {}
    status = str(value.get("status") or "not_authorized")
    resource_owner_open_id = value.get("owner_open_id") or owner_open_id or settings_data.get("open_id")
    return [
        {
            "resource_type": USER_IDENTITY_BUNDLE_RESOURCE,
            "label": USER_IDENTITY_RESOURCE_LABELS[USER_IDENTITY_BUNDLE_RESOURCE],
            "status": status,
            "owner_open_id": resource_owner_open_id,
            "authorization_owner": "resource_owner",
            "authorization_policy": "owner_granted_tighten_only",
            "authorization_model": "bundle_authorization",
            "covered_resources": list(USER_IDENTITY_RESOURCES),
            "needs_authorization": status not in {"authorized", "connected"},
            "can_escalate_original_permissions": False,
            "first_use_guidance": _user_identity_first_use_guidance(USER_IDENTITY_BUNDLE_RESOURCE, status),
            "authorization_actions": _user_identity_authorization_actions(
                USER_IDENTITY_BUNDLE_RESOURCE,
                company_id=company_id,
                owner_open_id=resource_owner_open_id,
            ),
        }
    ]


def user_identity_authorization_summary(items: list[dict[str, Any]]) -> str:
    authorized = [str(item["label"]) for item in items if item.get("needs_authorization") is False]
    pending = [str(item["label"]) for item in items if item.get("needs_authorization") is True]
    if not authorized:
        return "未授权用户级能力包；首次使用个人能力时引导本人一次整体授权"
    if pending:
        return f"已授权：{'、'.join(authorized)}；待授权：{'、'.join(pending)}"
    return f"已授权：{'、'.join(authorized)}"


def enterprise_identity_boundary_summary(item: BotUserAccess) -> str:
    return f"App Identity + Company Scope + Role Scope：{item.role}/{item.access_scope}"


def user_identity_boundary_summary(items: list[dict[str, Any]]) -> str:
    authorized = [str(item["label"]) for item in items if item.get("needs_authorization") is False]
    if authorized:
        return f"User Identity：本人已授权 {'、'.join(authorized)}"
    return "User Identity：未授权前不读取个人资源"


def agent_next_action(*, agent_status: str, authorizations: list[dict[str, Any]]) -> str:
    if agent_status != "active":
        return "启用员工 Agent"
    pending = [item for item in authorizations if item.get("needs_authorization") is True]
    if pending:
        return "首次使用用户级能力时引导本人一次整体授权"
    return "可直接通过大飞哥使用共享 Tool"


def personal_authorization_next_action(authorizations: list[dict[str, Any]]) -> str:
    pending = [item for item in authorizations if item.get("needs_authorization") is True]
    if not pending:
        return "个人资源授权已完成；继续按资源所有者范围使用。"
    return "等待本人一次整体授权：用户级能力包"


def _user_identity_first_use_guidance(resource_type: str, status: str) -> str:
    if status in {"authorized", "connected"}:
        return "已完成本人一次整体授权，仍只按资源所有者原始授权范围读取。"
    if resource_type == USER_IDENTITY_BUNDLE_RESOURCE:
        return "首次使用用户级能力时，引导本人完成一次整体授权；授权后长期开放个人能力。"
    guidance = {
        "personal_feishu": "首次使用个人飞书能力时，引导本人完成用户级能力包授权。",
        "external_mail": "首次使用其他邮箱能力时，引导本人完成邮箱 OAuth 或账号授权。",
        "personal_dingtalk": "首次使用个人钉钉能力时，引导本人完成钉钉授权。",
        "personal_wechat": "个人微信为未来能力，未授权前不读取任何微信数据。",
    }
    return guidance.get(resource_type, "首次使用对应个人能力时，引导资源所有者本人授权。")


def _user_identity_authorization_actions(
    resource_type: str,
    *,
    company_id: UUID | None,
    owner_open_id: str | None,
) -> list[dict[str, Any]]:
    if company_id is None or not owner_open_id:
        return []
    query = urlencode({"company_id": str(company_id), "open_id": owner_open_id})
    base_url = settings.api_base_url.rstrip("/")
    url_status = user_identity_authorization_url_status(base_url)
    return [
        {
            "label": "授权个人能力包",
            "channel": "feishu_oauth",
            "authorization_flow": "feishu_in_app_oauth",
            "url": f"{base_url}/api/user-identity/oauth/feishu/start?{query}",
            "start_endpoint": "/api/user-identity/oauth/feishu/start",
            "callback_endpoint": "/api/feishu/oauth/callback",
            "fallback_debug_flow": "feishu_cli_split_flow",
            "fallback_debug_url": f"{base_url}/user-auth/feishu-cli?{query}",
            "owner_open_id": owner_open_id,
            "authorization_model": "bundle_authorization",
            "covered_resources": list(USER_IDENTITY_RESOURCES),
            "can_escalate_original_permissions": False,
            "employee_reachable": url_status["employee_reachable"],
            "local_only": url_status["local_only"],
            "api_base_url_status": url_status,
        }
    ]


def bot_permission_rules_payload() -> dict[str, Any]:
    return {
        "rules": [
            {
                "domain": domain,
                "labels": rule["labels"],
                "resources": rule["resources"],
            }
            for domain, rule in DOMAIN_RULES.items()
        ],
        "notes": [
            "通讯录同步会根据部门名、岗位名、邮箱自动匹配业务域。",
            "默认成员仍是 chat 权限；只有负责人/经理/总监等岗位会升级为 domain 权限。",
            "老板/管理员 company 权限不会被规则自动降级。",
        ],
    }


def count_bot_user_access(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(BotUserAccess)) or 0


def recalculate_bot_user_permissions(
    db: Session,
    *,
    company_id: UUID,
    dry_run: bool = True,
    update_manual_admins: bool = False,
) -> dict[str, Any]:
    department_names = _department_names_by_id(db, company_id=company_id)
    users = list(
        db.scalars(
            select(BotUserAccess)
            .where(BotUserAccess.company_id == company_id)
            .order_by(BotUserAccess.created_at.asc())
        ).all()
    )
    changed = 0
    previews = []
    for user in users:
        settings_data = user.settings or {}
        if user.role in {"owner", "admin"} and not update_manual_admins:
            previews.append(_permission_preview(user, user.role, user.access_scope, settings_data, skipped=True))
            continue
        department_ids = [str(item) for item in settings_data.get("department_ids") or []]
        names = settings_data.get("department_names") or [
            department_names[item] for item in department_ids if item in department_names
        ]
        profile = infer_permission_profile(
            display_name=user.display_name,
            job_title=settings_data.get("job_title"),
            department_names=names,
            email=settings_data.get("email"),
        )
        new_settings = merge_permission_settings(
            settings_data,
            profile=profile,
            source=settings_data.get("source") or "manual_recalculate",
            department_ids=department_ids,
            department_names=names,
            email=settings_data.get("email"),
            job_title=settings_data.get("job_title"),
            status=settings_data.get("status"),
            synced_at=settings_data.get("synced_at") or "",
        )
        changed_item = (
            user.role != profile.role
            or user.access_scope != profile.access_scope
            or (settings_data.get("permission_domains") or []) != profile.domains
        )
        previews.append(_permission_preview(user, profile.role, profile.access_scope, new_settings, skipped=False))
        if changed_item:
            changed += 1
        if not dry_run:
            user.role = profile.role
            user.access_scope = profile.access_scope
            user.settings = json_safe(new_settings)
    if not dry_run:
        db.commit()
    return {"dry_run": dry_run, "checked": len(users), "changed": changed, "items": previews[:100]}


def _env_bot_admin_items(db: Session, *, company_id: UUID | None, existing_open_ids: set[str]) -> list[dict[str, Any]]:
    default_company = _default_company_id(db)
    if company_id and default_company and str(company_id) != default_company:
        return []
    admins = [item.strip() for item in settings.feishu_bot_admin_open_ids.split(",") if item.strip()]
    results = []
    for open_id in admins:
        if open_id in existing_open_ids:
            continue
        results.append(
            bot_user_access_payload(
                SimpleNamespace(
                    id=f"env:{open_id}",
                    company_id=default_company,
                    open_id=open_id,
                    display_name="环境配置管理员",
                    role="owner",
                    access_scope="company",
                    is_active=True,
                    settings={"source": "env"},
                )
            )
        )
    return results


def _department_names_by_id(db: Session, *, company_id: UUID) -> dict[str, str]:
    events = db.scalars(
        select(WorkEvent)
        .where(WorkEvent.company_id == company_id)
        .where(WorkEvent.event_type == "feishu.contacts.department")
        .order_by(WorkEvent.occurred_at.desc())
    ).all()
    names: dict[str, str] = {}
    for event in events:
        item = ((event.payload or {}).get("item") or {}) if isinstance(event.payload, dict) else {}
        department_id = str(item.get("department_id") or item.get("open_department_id") or "").strip()
        name = str(item.get("name") or item.get("i18n_name") or "").strip()
        if department_id and name and department_id not in names:
            names[department_id] = name
    return names


def _default_company_id(db: Session) -> str | None:
    if settings.feishu_default_app_config_id:
        app_config = db.get(FeishuAppConfig, UUID(settings.feishu_default_app_config_id))
        if app_config:
            return str(app_config.company_id)
    company_id = db.scalar(
        select(WorkEvent.company_id).group_by(WorkEvent.company_id).order_by(func.count().desc()).limit(1)
    )
    return str(company_id) if company_id else None


def _permission_preview(
    user: BotUserAccess,
    role: str,
    access_scope: str,
    settings_data: dict[str, Any],
    *,
    skipped: bool,
) -> dict[str, Any]:
    return {
        "open_id": user.open_id,
        "display_name": user.display_name,
        "before": {"role": user.role, "access_scope": user.access_scope},
        "after": {"role": role, "access_scope": access_scope},
        "domains": settings_data.get("permission_domains") or [],
        "resources": settings_data.get("allowed_resources") or [],
        "department_names": settings_data.get("department_names") or [],
        "job_title": settings_data.get("job_title"),
        "skipped": skipped,
    }
