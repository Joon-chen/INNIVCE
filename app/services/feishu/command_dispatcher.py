from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import FeishuAppConfig
from app.services.agent.policies import BotActor
from app.services.agent.runtime import agent_runtime_result_payload, agent_runtime_trace_payload, finalize_agent_workflow_reply_with_trace
from app.services.feishu.identity import BotIdentity
from app.services.tools.base import (
    DATA_BOUNDARY_POLICY,
    DATA_PERMISSION_MODEL,
    DIGITAL_ADVISOR_PERMISSION_POLICY,
    ENTERPRISE_IDENTITY_CONSTRAINTS,
    TOOL_ACCESS_POLICY,
    TOOL_SHARING_MODEL,
    USER_IDENTITY_CONSTRAINTS,
    USER_IDENTITY_RESOURCES,
    SHARED_TOOL_COUNT,
    identity_permission_contract,
)


@dataclass(frozen=True)
class CommandDispatchResult:
    handled: bool
    reply: str = ""
    send_approval_card: bool = True
    used_agent_runtime: bool = False
    final_answer_owner: str = "command_dispatcher"
    route_path: str | None = None
    route_label: str | None = None
    agent_identity: dict[str, Any] | None = None
    agent_runtime_trace: dict[str, Any] | None = None


@dataclass(frozen=True)
class CommandDispatchHandlers:
    help_text: str
    member_help_text: Callable[[BotIdentity], str]
    identity_reply: Callable[[BotIdentity], str]
    bot_identity_reply: Callable[[BotIdentity], str]
    online_status_reply: Callable[[BotIdentity], str]
    greeting_reply: Callable[[BotIdentity], str]
    permission_denied_reply: Callable[[BotIdentity, str], str]
    mail_capability_reply: Callable[[BotIdentity], str]
    build_daily_report_reply: Callable[[Session, FeishuAppConfig], str]
    sync_mail_reply: Callable[[Session, FeishuAppConfig], Awaitable[str]]
    quick_sync_for_question: Callable[[Session, FeishuAppConfig, str], Awaitable[dict[str, Any]]]
    recent_mail_reply: Callable[..., str]
    sync_approvals_reply: Callable[[Session, FeishuAppConfig], Awaitable[str]]
    sync_contacts_reply: Callable[[Session, FeishuAppConfig], Awaitable[str]]
    prepare_approval_action_reply: Callable[..., Awaitable[str]]
    execute_pending_approval_action_reply: Callable[..., Awaitable[str]]
    clear_pending_approval_action: Callable[[FeishuAppConfig, BotIdentity, str | None], None]
    employee_bot_answer: Callable[[Session, FeishuAppConfig, str, str, BotIdentity, str | None], str]
    employee_bot_answer_result: Callable[[Session, FeishuAppConfig, str, str, BotIdentity, str | None], Any] | None = None


async def dispatch_command_reply(
    db: Session,
    app_config: FeishuAppConfig,
    *,
    command: str,
    normalized: str,
    identity: BotIdentity,
    chat_id: str | None,
    reply_target: dict[str, str],
    ai_mode_enabled: bool,
    handlers: CommandDispatchHandlers,
) -> CommandDispatchResult:
    is_admin = identity.can_query_company
    used_agent_runtime = False
    agent_runtime_trace: dict[str, Any] | None = None
    route_path: str | None = normalized
    route_label: str | None = None

    if normalized == "帮助":
        reply, agent_runtime_trace = _workflow_agent_reply(
            db,
            app_config,
            command=command,
            normalized=normalized,
            identity=identity,
            chat_id=chat_id,
            route_path="general_chat",
            raw_reply=handlers.help_text if is_admin else handlers.member_help_text(identity),
            workflow_name="meta_help",
        )
        used_agent_runtime = True
        route_path = _agent_trace_value(agent_runtime_trace, "route_path") or "agent_runtime"
        route_label = _agent_trace_value(agent_runtime_trace, "route_label") or "Agent Runtime"
    elif normalized == "身份确认":
        reply, agent_runtime_trace = _workflow_agent_reply(
            db,
            app_config,
            command=command,
            normalized=normalized,
            identity=identity,
            chat_id=chat_id,
            route_path="general_chat",
            raw_reply=handlers.identity_reply(identity),
            workflow_name="meta_identity",
        )
        used_agent_runtime = True
        route_path = _agent_trace_value(agent_runtime_trace, "route_path") or "agent_runtime"
        route_label = _agent_trace_value(agent_runtime_trace, "route_label") or "Agent Runtime"
    elif normalized == "机器人身份":
        reply, agent_runtime_trace = _workflow_agent_reply(
            db,
            app_config,
            command=command,
            normalized=normalized,
            identity=identity,
            chat_id=chat_id,
            route_path="general_chat",
            raw_reply=handlers.bot_identity_reply(identity),
            workflow_name="meta_bot_identity",
        )
        used_agent_runtime = True
        route_path = _agent_trace_value(agent_runtime_trace, "route_path") or "agent_runtime"
        route_label = _agent_trace_value(agent_runtime_trace, "route_label") or "Agent Runtime"
    elif normalized == "在线状态":
        reply, agent_runtime_trace = _workflow_agent_reply(
            db,
            app_config,
            command=command,
            normalized=normalized,
            identity=identity,
            chat_id=chat_id,
            route_path="general_chat",
            raw_reply=handlers.online_status_reply(identity),
            workflow_name="meta_online_status",
        )
        used_agent_runtime = True
        route_path = _agent_trace_value(agent_runtime_trace, "route_path") or "agent_runtime"
        route_label = _agent_trace_value(agent_runtime_trace, "route_label") or "Agent Runtime"
    elif normalized == "寒暄":
        reply, agent_runtime_trace = _workflow_agent_reply(
            db,
            app_config,
            command=command,
            normalized=normalized,
            identity=identity,
            chat_id=chat_id,
            route_path="general_chat",
            raw_reply=handlers.greeting_reply(identity),
            workflow_name="meta_greeting",
        )
        used_agent_runtime = True
        route_path = _agent_trace_value(agent_runtime_trace, "route_path") or "agent_runtime"
        route_label = _agent_trace_value(agent_runtime_trace, "route_label") or "Agent Runtime"
    elif normalized.startswith("驾驶舱"):
        reply, agent_runtime_trace = _employee_agent_reply(db, app_config, command, normalized, identity, chat_id, handlers)
        used_agent_runtime = True
        route_path = _agent_trace_value(agent_runtime_trace, "route_path") or "agent_runtime"
        route_label = _agent_trace_value(agent_runtime_trace, "route_label") or "Agent Runtime"
    elif normalized == "管理人员查询":
        reply, agent_runtime_trace = _employee_agent_reply(db, app_config, command, normalized, identity, chat_id, handlers)
        used_agent_runtime = True
        route_path = _agent_trace_value(agent_runtime_trace, "route_path") or "agent_runtime"
        route_label = _agent_trace_value(agent_runtime_trace, "route_label") or "Agent Runtime"
    elif normalized == "邮件能力":
        reply, agent_runtime_trace = _workflow_agent_reply(
            db,
            app_config,
            command=command,
            normalized=normalized,
            identity=identity,
            chat_id=chat_id,
            route_path="general_chat",
            raw_reply=handlers.mail_capability_reply(identity),
            workflow_name="meta_mail_capability",
        )
        used_agent_runtime = True
        route_path = _agent_trace_value(agent_runtime_trace, "route_path") or "agent_runtime"
        route_label = _agent_trace_value(agent_runtime_trace, "route_label") or "Agent Runtime"
    elif normalized == "今日日报":
        if is_admin:
            handlers.build_daily_report_reply(db, app_config)
        reply, agent_runtime_trace = _employee_agent_reply(db, app_config, command, normalized, identity, chat_id, handlers)
        used_agent_runtime = True
        route_path = _agent_trace_value(agent_runtime_trace, "route_path") or "agent_runtime"
        route_label = _agent_trace_value(agent_runtime_trace, "route_label") or "Agent Runtime"
    elif normalized == "同步邮箱":
        if not is_admin:
            raw_reply = handlers.permission_denied_reply(identity, "邮箱同步")
        else:
            raw_reply = await handlers.sync_mail_reply(db, app_config)
        reply, agent_runtime_trace = _workflow_agent_reply(
            db,
            app_config,
            command=command,
            normalized=normalized,
            identity=identity,
            chat_id=chat_id,
            route_path=normalized,
            raw_reply=raw_reply,
            workflow_name="sync_mail",
        )
        used_agent_runtime = True
        route_path = _agent_trace_value(agent_runtime_trace, "route_path") or "agent_runtime"
        route_label = _agent_trace_value(agent_runtime_trace, "route_label") or "Agent Runtime"
    elif normalized == "最近邮件":
        if is_admin and _should_quick_sync_before_agent(command, normalized):
            await handlers.quick_sync_for_question(db, app_config, "最近邮件")
        reply, agent_runtime_trace = _employee_agent_reply(db, app_config, command, normalized, identity, chat_id, handlers)
        used_agent_runtime = True
        route_path = _agent_trace_value(agent_runtime_trace, "route_path") or "agent_runtime"
        route_label = _agent_trace_value(agent_runtime_trace, "route_label") or "Agent Runtime"
    elif normalized == "最近一封邮件":
        if is_admin and _should_quick_sync_before_agent(command, normalized):
            await handlers.quick_sync_for_question(db, app_config, "最近一封邮件")
        reply, agent_runtime_trace = _employee_agent_reply(db, app_config, command, normalized, identity, chat_id, handlers)
        used_agent_runtime = True
        route_path = _agent_trace_value(agent_runtime_trace, "route_path") or "agent_runtime"
        route_label = _agent_trace_value(agent_runtime_trace, "route_label") or "Agent Runtime"
    elif normalized == "同步审批":
        if not is_admin:
            raw_reply = handlers.permission_denied_reply(identity, "审批同步")
        else:
            raw_reply = await handlers.sync_approvals_reply(db, app_config)
        reply, agent_runtime_trace = _workflow_agent_reply(
            db,
            app_config,
            command=command,
            normalized=normalized,
            identity=identity,
            chat_id=chat_id,
            route_path=normalized,
            raw_reply=raw_reply,
            workflow_name="sync_approvals",
        )
        used_agent_runtime = True
        route_path = _agent_trace_value(agent_runtime_trace, "route_path") or "agent_runtime"
        route_label = _agent_trace_value(agent_runtime_trace, "route_label") or "Agent Runtime"
    elif normalized == "同步通讯录":
        if not is_admin:
            raw_reply = handlers.permission_denied_reply(identity, "通讯录同步")
        else:
            raw_reply = await handlers.sync_contacts_reply(db, app_config)
        reply, agent_runtime_trace = _workflow_agent_reply(
            db,
            app_config,
            command=command,
            normalized=normalized,
            identity=identity,
            chat_id=chat_id,
            route_path=normalized,
            raw_reply=raw_reply,
            workflow_name="sync_contacts",
        )
        used_agent_runtime = True
        route_path = _agent_trace_value(agent_runtime_trace, "route_path") or "agent_runtime"
        route_label = _agent_trace_value(agent_runtime_trace, "route_label") or "Agent Runtime"
    elif normalized == "组织架构":
        reply, agent_runtime_trace = _employee_agent_reply(db, app_config, command, normalized, identity, chat_id, handlers)
        used_agent_runtime = True
        route_path = _agent_trace_value(agent_runtime_trace, "route_path") or "agent_runtime"
        route_label = _agent_trace_value(agent_runtime_trace, "route_label") or "Agent Runtime"
    elif normalized == "最近审批":
        reply, agent_runtime_trace = _employee_agent_reply(db, app_config, command, normalized, identity, chat_id, handlers)
        used_agent_runtime = True
        route_path = _agent_trace_value(agent_runtime_trace, "route_path") or "agent_runtime"
        route_label = _agent_trace_value(agent_runtime_trace, "route_label") or "Agent Runtime"
    elif normalized == "审批建议":
        reply, agent_runtime_trace = _employee_agent_reply(db, app_config, command, normalized, identity, chat_id, handlers)
        used_agent_runtime = True
        route_path = _agent_trace_value(agent_runtime_trace, "route_path") or "agent_runtime"
        route_label = _agent_trace_value(agent_runtime_trace, "route_label") or "Agent Runtime"
    elif normalized == "审批详情请求":
        reply, agent_runtime_trace = _employee_agent_reply(db, app_config, command, normalized, identity, chat_id, handlers)
        used_agent_runtime = True
        route_path = _agent_trace_value(agent_runtime_trace, "route_path") or "agent_runtime"
        route_label = _agent_trace_value(agent_runtime_trace, "route_label") or "Agent Runtime"
    elif normalized == "审批通过请求":
        if not identity.can_query_approvals():
            reply = handlers.permission_denied_reply(identity, "审批操作")
        else:
            raw_reply = await handlers.prepare_approval_action_reply(
                db,
                app_config,
                identity,
                chat_id=chat_id,
                action="approve",
                selector_text=command,
            )
            reply, agent_runtime_trace = _workflow_agent_reply(
                db,
                app_config,
                command=command,
                normalized=normalized,
                identity=identity,
                chat_id=chat_id,
                route_path="feishu_approval_task_approve",
                raw_reply=raw_reply,
                workflow_name="approval_prepare_approve",
            )
            used_agent_runtime = True
            route_path = _agent_trace_value(agent_runtime_trace, "route_path") or "agent_runtime"
            route_label = _agent_trace_value(agent_runtime_trace, "route_label") or "Agent Runtime"
    elif normalized == "审批拒绝请求":
        if not identity.can_query_approvals():
            reply = handlers.permission_denied_reply(identity, "审批操作")
        else:
            raw_reply = await handlers.prepare_approval_action_reply(
                db,
                app_config,
                identity,
                chat_id=chat_id,
                action="reject",
                selector_text=command,
            )
            reply, agent_runtime_trace = _workflow_agent_reply(
                db,
                app_config,
                command=command,
                normalized=normalized,
                identity=identity,
                chat_id=chat_id,
                route_path="feishu_approval_task_reject",
                raw_reply=raw_reply,
                workflow_name="approval_prepare_reject",
            )
            used_agent_runtime = True
            route_path = _agent_trace_value(agent_runtime_trace, "route_path") or "agent_runtime"
            route_label = _agent_trace_value(agent_runtime_trace, "route_label") or "Agent Runtime"
    elif normalized == "确认审批通过":
        if not identity.can_query_approvals():
            reply = handlers.permission_denied_reply(identity, "审批操作")
        else:
            raw_reply = await handlers.execute_pending_approval_action_reply(
                db,
                app_config,
                identity,
                chat_id=chat_id,
                expected_action="approve",
            )
            reply, agent_runtime_trace = _workflow_agent_reply(
                db,
                app_config,
                command=command,
                normalized=normalized,
                identity=identity,
                chat_id=chat_id,
                route_path="feishu_approval_task_approve",
                raw_reply=raw_reply,
                workflow_name="approval_execute_approve",
            )
            used_agent_runtime = True
            route_path = _agent_trace_value(agent_runtime_trace, "route_path") or "agent_runtime"
            route_label = _agent_trace_value(agent_runtime_trace, "route_label") or "Agent Runtime"
    elif normalized == "确认审批拒绝":
        if not identity.can_query_approvals():
            reply = handlers.permission_denied_reply(identity, "审批操作")
        else:
            raw_reply = await handlers.execute_pending_approval_action_reply(
                db,
                app_config,
                identity,
                chat_id=chat_id,
                expected_action="reject",
            )
            reply, agent_runtime_trace = _workflow_agent_reply(
                db,
                app_config,
                command=command,
                normalized=normalized,
                identity=identity,
                chat_id=chat_id,
                route_path="feishu_approval_task_reject",
                raw_reply=raw_reply,
                workflow_name="approval_execute_reject",
            )
            used_agent_runtime = True
            route_path = _agent_trace_value(agent_runtime_trace, "route_path") or "agent_runtime"
            route_label = _agent_trace_value(agent_runtime_trace, "route_label") or "Agent Runtime"
    elif normalized == "取消审批操作":
        handlers.clear_pending_approval_action(app_config, identity, chat_id)
        reply, agent_runtime_trace = _workflow_agent_reply(
            db,
            app_config,
            command=command,
            normalized=normalized,
            identity=identity,
            chat_id=chat_id,
            route_path="approval_qa",
            raw_reply="已取消刚才准备执行的审批操作。",
            workflow_name="approval_cancel_pending_action",
        )
        used_agent_runtime = True
        route_path = _agent_trace_value(agent_runtime_trace, "route_path") or "agent_runtime"
        route_label = _agent_trace_value(agent_runtime_trace, "route_label") or "Agent Runtime"
    elif normalized == "待办事项":
        reply, agent_runtime_trace = _employee_agent_reply(db, app_config, command, normalized, identity, chat_id, handlers)
        used_agent_runtime = True
        route_path = _agent_trace_value(agent_runtime_trace, "route_path") or "agent_runtime"
        route_label = _agent_trace_value(agent_runtime_trace, "route_label") or "Agent Runtime"
    elif ai_mode_enabled:
        # ── Pre Gateway: session loading, query rewrite, rule matching ──
        from app.services.gateway.pre_gateway import process_pre_gateway
        _pg = process_pre_gateway(
            question=command,
            identity=identity,
            chat_id=chat_id,
            company_id=str(getattr(app_config, "company_id", None)) if getattr(app_config, "company_id", None) else None,
        )
        if _pg.is_result_followup and not settings.feishu_bot_runtime_v5_enabled:
            reply = _pg.rule_reply or ""
            agent_runtime_trace = None
            used_agent_runtime = False
            route_path = "result_followup"
            route_label = "结果续问"
        elif _pg.matched_rule:
            reply = _pg.rule_reply or ""
            agent_runtime_trace = None
            used_agent_runtime = False
            route_path = "rule_match"
            route_label = "规则匹配"
        else:
            if _pg.rewritten_question != command and not _pg.is_result_followup:
                command = _pg.rewritten_question
                from app.services.feishu.command_parser import normalize_command
                normalized = normalize_command(command)
            if identity.can_query_company and _should_quick_sync_before_agent(command, normalized):
                await handlers.quick_sync_for_question(db, app_config, command)
            reply, agent_runtime_trace = _employee_agent_reply(db, app_config, command, normalized, identity, chat_id, handlers)
            used_agent_runtime = True
            route_path = _agent_trace_value(agent_runtime_trace, "route_path") or "agent_runtime"
            route_label = _agent_trace_value(agent_runtime_trace, "route_label") or "Agent Runtime"
    else:
        return CommandDispatchResult(handled=False)

    return CommandDispatchResult(
        handled=True,
        reply=reply,
        used_agent_runtime=used_agent_runtime,
        final_answer_owner="agent_runtime" if used_agent_runtime else "command_dispatcher",
        route_path=route_path,
        route_label=route_label,
        agent_identity=_dispatch_agent_identity(app_config, identity, agent_runtime_trace),
        agent_runtime_trace=agent_runtime_trace,
    )


# Allow-list for result types that can be followed-up
_RESULT_ALLOW_LABELS = {"审批", "通讯录", "任务", "待办", "会议", "邮件", "文档", "组织架构", "联系人"}
def _should_store_result_context(answer: str, route_label: str = "") -> bool:
    """Check if this result should be stored as Result Context."""
    if not answer or len(answer) < 20:
        return False
    if any(kw in route_label for kw in _RESULT_ALLOW_LABELS):
        return True
    if any(kw in answer for kw in ("条", "笔", "项", "个", "位")) and any(k in answer for k in ("审批", "待办", "任务", "会议", "邮件")):
        return True
    return False

def _save_result_context(chat_id: str | None, question: str, answer: str, route_label: str = "") -> None:
    if settings.feishu_bot_runtime_v5_enabled:
        return
    if not chat_id or not answer:
        return
    if not _should_store_result_context(answer, route_label):
        return
    try:
        from app.core.config import settings as _st
        import json as _rj
        import redis as _rd

        _rc = _rd.Redis.from_url(_st.redis_url, decode_responses=True)
        _rc.setex(f'feishu:result:{chat_id}', 600, _rj.dumps({"question": question, "answer": answer}, ensure_ascii=False))
    except Exception:
        pass


def _employee_agent_reply(
    db: Session,
    app_config: FeishuAppConfig,
    command: str,
    normalized: str,
    identity: BotIdentity,
    chat_id: str | None,
    handlers: CommandDispatchHandlers,
) -> tuple[str, dict[str, Any] | None]:
    if handlers.employee_bot_answer_result is None:
        reply = handlers.employee_bot_answer(db, app_config, command, normalized, identity, chat_id)
        _save_result_context(chat_id, command, reply)
        return reply, None
    result = handlers.employee_bot_answer_result(db, app_config, command, normalized, identity, chat_id)
    if isinstance(result, str):
        _save_result_context(chat_id, command, result)
        return result, None
    answer = str(getattr(result, "answer", "") or "")
    trace_payload = getattr(result, "trace_payload", None)
    if trace_payload is None:
        trace_payload = getattr(result, "trace", None)
    if isinstance(trace_payload, dict) and trace_payload.get("runtime_version") == "v5":
        return answer, trace_payload
    _rl = ""
    if isinstance(trace_payload, dict):
        _rl = trace_payload.get("route_label", "")
    elif trace_payload is not None and hasattr(trace_payload, "route_path"):
        _rl = getattr(trace_payload, "route_label", "")
    _save_result_context(chat_id, command, answer, _rl)
    if isinstance(trace_payload, dict):
        return answer, trace_payload
    if trace_payload is not None and hasattr(trace_payload, "route_path"):
        return answer, agent_runtime_trace_payload(trace_payload)
    return answer, None


def _should_quick_sync_before_agent(command: str, normalized: str) -> bool:
    text = f"{command} {normalized}".lower()
    if any(term in text for term in ("邮件", "邮箱", "mail", "email")):
        return False
    return True


def _workflow_agent_reply(
    db: Session,
    app_config: FeishuAppConfig,
    *,
    command: str,
    normalized: str,
    identity: BotIdentity,
    chat_id: str | None,
    route_path: str,
    raw_reply: str,
    workflow_name: str,
) -> tuple[str, dict[str, Any]]:
    result = finalize_agent_workflow_reply_with_trace(
        db,
        company_id=app_config.company_id,
        question=command,
        normalized_command=normalized,
        chat_id=chat_id,
        actor=_actor_from_identity(identity),
        route_path=route_path,
        raw_answer=raw_reply,
        workflow_name=workflow_name,
        workflow_metadata={
            "workflow_result_source": "approval_workflow",
            "workflow_final_answer_delegated_to": "agent_runtime",
        },
    )
    payload = agent_runtime_result_payload(result)
    return result.answer, payload["trace"]


def _actor_from_identity(identity: BotIdentity) -> BotActor:
    return BotActor(
        role=identity.role,
        access_scope=identity.access_scope,
        domains=identity.domains,
        display_name=identity.display_name,
        open_id=identity.open_id,
        email=identity.email,
    )


def _agent_trace_value(trace_payload: dict[str, Any] | None, key: str) -> str | None:
    if not isinstance(trace_payload, dict):
        return None
    value = trace_payload.get(key)
    return str(value) if value else None


def _dispatch_agent_identity(
    app_config: FeishuAppConfig,
    identity: BotIdentity,
    trace_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    if isinstance(trace_payload, dict) and isinstance(trace_payload.get("agent_identity"), dict):
        return trace_payload["agent_identity"]
    company_id = getattr(app_config, "company_id", None)
    owner_key = identity.open_id or identity.email or identity.display_name or identity.role or "unknown"
    agent_id = f"{company_id}:{owner_key}" if company_id else owner_key
    data_access_scope = "company" if identity.can_query_company else identity.access_scope
    user_identity_required = data_access_scope in {"personal", "self", "user"}
    return {
        "agent_type": "employee_personal_agent",
        "agent_id": agent_id,
        "company_id": str(company_id) if company_id else None,
        "agent_owner_open_id": identity.open_id,
        "agent_owner_email": identity.email,
        "agent_owner_display_name": identity.display_name,
        "entrypoint": "feishu_bot",
        "tool_access_policy": TOOL_ACCESS_POLICY,
        "tool_sharing_model": TOOL_SHARING_MODEL,
        "shared_business_tools": ["ApprovalTool","KnowledgeTool","BitableTool","ChatTool","CalendarTool","MeetingTool","ReportTool","AutomationTool","PeopleTool"],
        "shared_business_tool_count": SHARED_TOOL_COUNT,
        "agent_can_call_all_business_tools": True,
        "data_permission_model": DATA_PERMISSION_MODEL,
        "data_access_scope": data_access_scope,
        "identity_permission_contract": identity_permission_contract(user_identity_required=user_identity_required),
        "enterprise_identity": "app_identity",
        "enterprise_identity_constraints": list(ENTERPRISE_IDENTITY_CONSTRAINTS),
        "enterprise_resource_boundary": {
            "identity": "app_identity",
            "constraints": list(ENTERPRISE_IDENTITY_CONSTRAINTS),
            "can_exceed_feishu_app_permissions": False,
        },
        "user_identity": "resource_owner_identity" if user_identity_required else None,
        "user_identity_constraints": list(USER_IDENTITY_CONSTRAINTS) if user_identity_required else [],
        "user_identity_supported_resources": list(USER_IDENTITY_RESOURCES),
        "user_resource_boundary": {
            "identity": "resource_owner_identity",
            "supported_resources": list(USER_IDENTITY_RESOURCES),
            "constraints": list(USER_IDENTITY_CONSTRAINTS),
            "can_exceed_original_authorization": False,
        },
        "data_boundary_policy": DATA_BOUNDARY_POLICY,
        "digital_advisor_permission_policy": DIGITAL_ADVISOR_PERMISSION_POLICY,
        "cannot_escalate_original_permissions": True,
        "final_answer_owner": "agent_runtime",
    }
