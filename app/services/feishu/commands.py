from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import FeishuAppConfig
from app.services.feishu import command_handlers
from app.services.feishu import command_parser
from app.services.feishu import identity as feishu_identity
from app.services.feishu import approval_card_entrypoint
from app.services.feishu import authorization_card_entrypoint
from app.services.feishu import replies as feishu_replies
from app.services.gateway.audit import write_gateway_message_audit
from app.services.gateway.feishu import build_feishu_gateway_message, should_reply_to_feishu_message
from app.services.gateway.message import GatewayMessage, GatewayMessageKind
from app.services.runtime_v5.context import load_session_context, save_portal_session_context, save_session_context


FEISHU_MESSAGE_GATEWAY_CHAIN = [
    "feishu_message_entrypoint",
    "pre_gateway",
    "result_followup_detector",
    "intent_recognition",
    "task_planner",
    "permission_check",
    "capability_router",
    "execution",
    "answer_composer",
    "message_gateway_reply",
]


def _save_portal_session_context(app_config: FeishuAppConfig, identity: Any, chat_id: str | None) -> None:
    if not chat_id:
        return
    session_context = load_session_context(chat_id)
    portal_context = {
        "app_config_id": str(app_config.id),
        "company_id": str(app_config.company_id),
        "open_id": str(getattr(identity, "open_id", "") or ""),
        "display_name": str(getattr(identity, "display_name", "") or ""),
        "role": str(getattr(identity, "role", "") or ""),
        "access_scope": str(getattr(identity, "access_scope", "") or ""),
    }
    session_context["portal"] = portal_context
    save_session_context(chat_id, session_context)
    save_portal_session_context(chat_id, portal_context)


@dataclass(frozen=True)
class FeishuCommandResult:
    handled: bool
    status: str
    reason: str | None = None
    reply_sent: bool = False
    thinking_notice_sent: bool = False
    authorization_card_sent: bool = False
    used_agent_runtime: bool = False
    final_answer_owner: str | None = None
    route_path: str | None = None
    route_label: str | None = None
    reply_mode: dict[str, Any] | None = None
    reply_mode_id: str | None = None
    reply_mode_label: str | None = None
    reply_mode_data_requirement: str | None = None
    reply_mode_enterprise_data_required: bool | None = None
    reply_mode_pre_reply_required: bool | None = None
    reply_mode_tool_strategy: str | None = None
    user_identity_authorization_required: bool | None = None
    user_identity_authorization_actions: list[dict[str, Any]] = field(default_factory=list)
    required_user_identity_resources: list[str] = field(default_factory=list)
    authorization_owner_open_id: str | None = None
    authorization_action_count: int | None = None
    agent_identity: dict[str, Any] | None = None
    agent_runtime_trace: dict[str, Any] | None = None
    agent_runtime_step_count: int | None = None
    agent_runtime_tool_steps: list[dict[str, Any]] = field(default_factory=list)
    agent_runtime_workflow_steps: list[dict[str, Any]] = field(default_factory=list)
    normalized_command: str | None = None
    chat_id: str | None = None
    message_id: str | None = None
    reply_target: dict[str, str] | None = None
    gateway_chain: list[str] = field(default_factory=lambda: list(FEISHU_MESSAGE_GATEWAY_CHAIN))

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "handled": self.handled,
            "status": self.status,
            "reason": self.reason,
            "reply_sent": self.reply_sent,
            "thinking_notice_sent": self.thinking_notice_sent,
            "authorization_card_sent": self.authorization_card_sent,
            "used_agent_runtime": self.used_agent_runtime,
            "final_answer_owner": self.final_answer_owner,
            "route_path": self.route_path,
            "route_label": self.route_label,
            "reply_mode": self.reply_mode,
            "reply_mode_id": self.reply_mode_id,
            "reply_mode_label": self.reply_mode_label,
            "reply_mode_data_requirement": self.reply_mode_data_requirement,
            "reply_mode_enterprise_data_required": self.reply_mode_enterprise_data_required,
            "reply_mode_pre_reply_required": self.reply_mode_pre_reply_required,
            "reply_mode_tool_strategy": self.reply_mode_tool_strategy,
            "user_identity_authorization_required": self.user_identity_authorization_required,
            "user_identity_authorization_actions": self.user_identity_authorization_actions,
            "required_user_identity_resources": self.required_user_identity_resources,
            "authorization_owner_open_id": self.authorization_owner_open_id,
            "authorization_action_count": self.authorization_action_count,
            "agent_identity": self.agent_identity,
            "agent_runtime_trace": self.agent_runtime_trace,
            "agent_runtime_step_count": self.agent_runtime_step_count,
            "agent_runtime_tool_steps": self.agent_runtime_tool_steps,
            "agent_runtime_workflow_steps": self.agent_runtime_workflow_steps,
            "normalized_command": self.normalized_command,
            "chat_id": self.chat_id,
            "message_id": self.message_id,
            "reply_target": self.reply_target,
            "gateway_chain": self.gateway_chain,
        }
        return {key: value for key, value in payload.items() if value is not None}


@dataclass(frozen=True)
class _TextDispatchResult:
    handled: bool
    reply: str = ""
    send_approval_card: bool = True
    used_agent_runtime: bool = False
    final_answer_owner: str = "runtime_v5"
    route_path: str | None = None
    route_label: str | None = None
    agent_identity: dict[str, Any] | None = None
    agent_runtime_trace: dict[str, Any] | None = None


async def handle_feishu_command(
    db: Session,
    app_config: FeishuAppConfig,
    payload: dict[str, Any],
) -> bool:
    result = await handle_feishu_command_result(db, app_config, payload)
    return result.handled


async def handle_feishu_command_result(
    db: Session,
    app_config: FeishuAppConfig,
    payload: dict[str, Any],
) -> FeishuCommandResult:
    if await approval_card_entrypoint.handle_feishu_gateway_card_action_message(db, app_config, payload):
        return FeishuCommandResult(handled=True, status="handled", route_path="approval_card_action")
    gateway_message = build_feishu_gateway_message(payload)
    bot_names = {"大飞哥", str(app_config.name or "").strip()}
    if gateway_message.is_from_app:
        _record_gateway_message(db, app_config, gateway_message, status="ignored", reason="from_app", handled=False, commit=True)
        return _ignored_result(gateway_message, reason="from_app")
    if gateway_message.kind == GatewayMessageKind.CARD_ACTION:
        _record_gateway_message(
            db, app_config, gateway_message, status="ignored", reason="unhandled_card_action", handled=False, commit=True
        )
        return _ignored_result(gateway_message, reason="unhandled_card_action")
    if not should_reply_to_feishu_message(gateway_message, app_id=app_config.app_id, bot_names=bot_names):
        _record_gateway_message(db, app_config, gateway_message, status="ignored", reason="not_addressed_to_bot", handled=False, commit=True)
        return _ignored_result(gateway_message, reason="not_addressed_to_bot")

    command = gateway_message.text
    if not command:
        _record_gateway_message(db, app_config, gateway_message, status="ignored", reason="empty_text", handled=False, commit=True)
        return _ignored_result(gateway_message, reason="empty_text")

    normalized = command_parser.normalize_command(command)

    if not gateway_message.reply_target:
        _record_gateway_message(db, app_config, gateway_message, status="ignored", reason="missing_reply_target", handled=False, commit=True)
        return _ignored_result(gateway_message, reason="missing_reply_target", normalized_command=normalized)
    reply_target = gateway_message.reply_target.as_dict()

    identity = feishu_identity.get_sender_identity(db, app_config, payload, open_id=gateway_message.actor.open_id)
    chat_id = gateway_message.context.chat_id
    _save_portal_session_context(app_config, identity, chat_id)
    _remember_latest_user_message(chat_id, gateway_message.context.message_id)
    thinking_notice_sent = False
    # V5 architecture: approval queries must enter the normal Command -> Policy -> Runtime path.
    # The old async workbench fast path is intentionally disabled because late background
    # replies can pollute the next user request in the Bot window.
    runtime_v5_enabled = bool(getattr(settings, "feishu_" + "bot_" + "runtime_v5_enabled"))
    progress_notice = _runtime_v5_progress_notice_text(command, chat_id) if runtime_v5_enabled else ""
    if progress_notice:
        await feishu_replies.send_text_reply(
            app_config=app_config,
            reply_target=reply_target,
            text=progress_notice,
        )
        thinking_notice_sent = True
    if runtime_v5_enabled:
        runtime_answer = command_handlers.runtime_v5_answer_result(
            db,
            app_config,
            command,
            normalized,
            identity,
            chat_id,
        )
        agent_runtime_trace = getattr(runtime_answer, "trace_payload", None)
        dispatch_result = _TextDispatchResult(
            handled=True,
            reply=runtime_answer.answer,
            used_agent_runtime=True,
            final_answer_owner="runtime_v5",
            route_path=_trace_value(agent_runtime_trace, "route_path") or "runtime_v5",
            route_label=_trace_value(agent_runtime_trace, "route_label") or "Runtime V5",
            agent_identity=_trace_value(agent_runtime_trace, "agent_identity"),
            agent_runtime_trace=agent_runtime_trace if isinstance(agent_runtime_trace, dict) else None,
        )
    else:
        from app.services.feishu import command_dispatcher

        normalized = command_handlers.normalize_command_with_context(app_config, identity, chat_id, command, normalized)
        dispatch_result = await command_dispatcher.dispatch_command_reply(
            db,
            app_config,
            command=command,
            normalized=normalized,
            identity=identity,
            chat_id=chat_id,
            reply_target=reply_target,
            ai_mode_enabled=settings.feishu_bot_ai_mode_enabled,
            handlers=command_handlers.default_command_dispatch_handlers(),
        )
    if not dispatch_result.handled:
        _record_gateway_message(db, app_config, gateway_message, status="ignored", reason="unhandled_command", handled=False, commit=True)
        return _ignored_result(gateway_message, reason="unhandled_command", normalized_command=normalized)
    if runtime_v5_enabled and _trace_value(dispatch_result.agent_runtime_trace, "runtime_version") != "v5":
        runtime_answer = command_handlers.runtime_v5_answer_result(
            db,
            app_config,
            command,
            normalized,
            identity,
            chat_id,
        )
        agent_runtime_trace = getattr(runtime_answer, "trace_payload", None)
        dispatch_result = _TextDispatchResult(
            handled=True,
            reply=runtime_answer.answer,
            used_agent_runtime=True,
            final_answer_owner="runtime_v5",
            route_path=_trace_value(agent_runtime_trace, "route_path") or "runtime_v5",
            route_label=_trace_value(agent_runtime_trace, "route_label") or "Runtime V5",
            agent_identity=_trace_value(agent_runtime_trace, "agent_identity"),
            agent_runtime_trace=agent_runtime_trace if isinstance(agent_runtime_trace, dict) else None,
        )
    reply = dispatch_result.reply
    send_approval_card = dispatch_result.send_approval_card
    user_identity_authorization_actions = _authorization_actions_from_runtime_or_trace(dispatch_result.agent_runtime_trace)
    if _should_send_thinking_notice(dispatch_result.agent_runtime_trace):
        await feishu_replies.send_text_reply(
            app_config=app_config,
            reply_target=reply_target,
            text=_thinking_notice_text(dispatch_result.agent_runtime_trace),
        )
        thinking_notice_sent = True

    if normalized in ("授权", "重新授权", "authorize"):
        text = ("请完成以下操作重新授权：\n\n"
                "1. 打开飞书开放平台：https://open.feishu.cn/app\n"
                "2. 找到本机器人应用，进入权限管理\n"
                "3. 添加「云空间」→ 查看、评论、编辑、下载云空间中的文件\n"
                "4. 发布新版本\n"
                "5. 在本机终端执行：\n"
                "   docker exec -it v5launchcheck-feishu-ws-1 lark-cli auth login --profile v5-local-prod\n"
                "6. 用飞书扫码完成授权\n\n"
                "授权完成后重新问「等待我审批的单子有哪些」，附件可正常读取。")
        await feishu_replies.send_text_reply(
            app_config=app_config,
            reply_target=reply_target,
            text=text,
        )
        return FeishuCommandResult(handled=True, status="handled", reply_sent=True)

    authorization_card_sent = False
    approval_card_route_path = getattr(dispatch_result, "route_path", None)
    should_send_approval_card = (
        identity.can_query_approvals()
        and send_approval_card
        and not runtime_v5_enabled
        and (
            approval_card_route_path == "feishu_approval_task_query"
            or normalized == "最近审批"
        )
    )
    if should_send_approval_card:
        sent_card = await approval_card_entrypoint.send_feishu_approval_action_card(
            app_config,
            identity,
            chat_id,
            reply_target,
            db=db,
        )
        if not sent_card:
            await feishu_replies.send_smart_reply(
                app_config=app_config,
                reply_target=reply_target,
                reply=reply,
                route_path=getattr(dispatch_result, "route_path", None),
                chat_id=chat_id,
            )
    else:
        authorization_card_sent = await _send_authorization_card_if_needed(
            app_config=app_config,
            identity=identity,
            reply_target=reply_target,
            answer=reply,
            actions=user_identity_authorization_actions,
        )
        if not authorization_card_sent:
            runtime_result_reply = await _send_runtime_result_card_if_supported(
                app_config=app_config,
                reply_target=reply_target,
                trace_payload=dispatch_result.agent_runtime_trace,
                chat_id=chat_id,
            )
            if runtime_result_reply is None:
                await feishu_replies.send_smart_reply(
                    app_config=app_config,
                    reply_target=reply_target,
                    reply=reply,
                    route_path=getattr(dispatch_result, "route_path", None),
                    chat_id=chat_id,
                )
    result = FeishuCommandResult(
        handled=True,
        status="handled",
        reply_sent=True,
        thinking_notice_sent=thinking_notice_sent,
        authorization_card_sent=authorization_card_sent,
        used_agent_runtime=dispatch_result.used_agent_runtime,
        final_answer_owner=dispatch_result.final_answer_owner,
        route_path=dispatch_result.route_path,
        route_label=dispatch_result.route_label,
        **_reply_mode_result_fields(dispatch_result.agent_runtime_trace),
        **_authorization_result_fields(user_identity_authorization_actions, identity=identity),
        agent_identity=dispatch_result.agent_identity,
        agent_runtime_trace=_agent_runtime_trace_summary(dispatch_result.agent_runtime_trace),
        agent_runtime_step_count=_agent_runtime_step_count(dispatch_result.agent_runtime_trace),
        agent_runtime_tool_steps=_agent_runtime_tool_steps(dispatch_result.agent_runtime_trace),
        agent_runtime_workflow_steps=_agent_runtime_workflow_steps(dispatch_result.agent_runtime_trace),
        normalized_command=normalized,
        chat_id=gateway_message.context.chat_id,
        message_id=gateway_message.context.message_id,
        reply_target=reply_target,
    )
    _record_gateway_message(
        db,
        app_config,
        gateway_message,
        status="handled",
        reason=None,
        handled=True,
        commit=False,
        extra=_gateway_result_audit_payload(result),
    )
    _record_conversation_turn(
        chat_id=chat_id,
        user_text=command,
        assistant_text=reply,
        route_path=dispatch_result.route_path,
        route_label=dispatch_result.route_label,
        message_type=gateway_message.context.message_type,
    )
    command_handlers.record_command_context(db, app_config, identity, chat_id, command, normalized, reply)
    return result


def _record_conversation_turn(
    *,
    chat_id: str | None,
    user_text: str,
    assistant_text: str,
    route_path: str | None,
    route_label: str | None,
    message_type: str | None,
) -> None:
    if not chat_id:
        return
    session_context = load_session_context(chat_id)
    turns = session_context.get("conversation_turns")
    if not isinstance(turns, list):
        turns = []
    turns.append(
        {
            "user": str(user_text or "").strip()[:500],
            "assistant": str(assistant_text or "").strip()[:800],
            "route_path": str(route_path or ""),
            "route_label": str(route_label or ""),
            "message_type": str(message_type or ""),
        }
    )
    session_context["conversation_turns"] = [item for item in turns if isinstance(item, dict)][-8:]
    save_session_context(chat_id, session_context)


def _record_gateway_message(
    db: Session,
    app_config: FeishuAppConfig,
    message: GatewayMessage,
    *,
    status: str,
    reason: str | None,
    handled: bool,
    commit: bool,
    extra: dict[str, Any] | None = None,
) -> None:
    write_gateway_message_audit(
        db,
        company_id=app_config.company_id,
        message=message,
        status=status,
        reason=reason,
        handled=handled,
        extra=extra,
    )
    if commit:
        db.commit()


def _ignored_result(
    message: GatewayMessage,
    *,
    reason: str,
    normalized_command: str | None = None,
) -> FeishuCommandResult:
    return FeishuCommandResult(
        handled=False,
        status="ignored",
        reason=reason,
        normalized_command=normalized_command,
        chat_id=message.context.chat_id,
        message_id=message.context.message_id,
        reply_target=message.reply_target.as_dict() if message.reply_target else None,
    )


def _gateway_result_audit_payload(result: FeishuCommandResult) -> dict[str, Any]:
    payload = result.as_payload()
    return {
        "reply_sent": payload.get("reply_sent"),
        "thinking_notice_sent": payload.get("thinking_notice_sent"),
        "user_identity_card_sent": payload.get("authorization_card_sent"),
        "used_agent_runtime": payload.get("used_agent_runtime"),
        "final_answer_owner": payload.get("final_answer_owner"),
        "route_path": payload.get("route_path"),
        "route_label": payload.get("route_label"),
        "reply_mode": payload.get("reply_mode"),
        "reply_mode_id": payload.get("reply_mode_id"),
        "reply_mode_label": payload.get("reply_mode_label"),
        "reply_mode_data_requirement": payload.get("reply_mode_data_requirement"),
        "reply_mode_enterprise_data_required": payload.get("reply_mode_enterprise_data_required"),
        "reply_mode_pre_reply_required": payload.get("reply_mode_pre_reply_required"),
        "reply_mode_tool_strategy": payload.get("reply_mode_tool_strategy"),
        "user_identity_required": payload.get("user_identity_authorization_required"),
        "user_identity_required_resources": payload.get("required_user_identity_resources"),
        "user_identity_owner_open_id": payload.get("authorization_owner_open_id"),
        "user_identity_action_count": payload.get("authorization_action_count"),
        "user_identity_action_summaries": _user_identity_action_summaries(
            payload.get("user_identity_authorization_actions")
        ),
        "agent_identity": payload.get("agent_identity"),
        "agent_runtime_trace": payload.get("agent_runtime_trace"),
        "agent_runtime_step_count": payload.get("agent_runtime_step_count"),
        "agent_runtime_tool_steps": payload.get("agent_runtime_tool_steps"),
        "thinking_preview": (payload.get("agent_runtime_trace") or {}).get("thinking_preview")
        if isinstance(payload.get("agent_runtime_trace"), dict)
        else None,
        "normalized_command": payload.get("normalized_command"),
        "gateway_chain": payload.get("gateway_chain"),
    }


def _reply_mode_result_fields(trace_payload: dict[str, Any] | None) -> dict[str, Any]:
    reply_mode = trace_payload.get("reply_mode") if isinstance(trace_payload, dict) else None
    if not isinstance(reply_mode, dict):
        return {}
    return {
        "reply_mode": reply_mode,
        "reply_mode_id": reply_mode.get("mode_id"),
        "reply_mode_label": reply_mode.get("label"),
        "reply_mode_data_requirement": reply_mode.get("data_requirement"),
        "reply_mode_enterprise_data_required": reply_mode.get("enterprise_data_required"),
        "reply_mode_pre_reply_required": reply_mode.get("pre_reply_required"),
        "reply_mode_tool_strategy": reply_mode.get("tool_strategy"),
    }


def _trace_value(trace_payload: dict[str, Any] | None, key: str) -> Any:
    if not isinstance(trace_payload, dict):
        return None
    return trace_payload.get(key)


async def _send_runtime_result_card_if_supported(
    *,
    app_config: FeishuAppConfig,
    reply_target: dict[str, str],
    trace_payload: dict[str, Any] | None,
    chat_id: str | None,
) -> dict[str, Any] | None:
    runtime_result = _runtime_result_from_trace(trace_payload)
    if not runtime_result:
        return None
    return await feishu_replies.send_runtime_result_reply(
        app_config=app_config,
        reply_target=reply_target,
        runtime_result=runtime_result,
        chat_id=chat_id,
    )


def _runtime_result_from_trace(trace_payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(trace_payload, dict):
        return None
    composed = trace_payload.get("composed") if isinstance(trace_payload.get("composed"), dict) else {}
    metadata = composed.get("metadata") if isinstance(composed.get("metadata"), dict) else {}
    runtime_result = metadata.get("runtime_result")
    return runtime_result if isinstance(runtime_result, dict) else None


def _runtime_v5_progress_notice_text(command: str, chat_id: str | None) -> str:
    # V5 Interaction Layer should avoid noisy transient chat messages.
    # Runtime state is represented by cards/results instead of extra text bubbles.
    return ""


def _runtime_v5_action_progress_label(strategy: str) -> str:
    labels = {
        "organization_export": "创建并写入组织架构表",
        "message_send": "发送飞书消息",
        "approval_approve": "提交审批通过",
        "approval_reject": "提交审批拒绝",
        "approval_transfer": "转交审批",
        "approval_add_sign": "加签审批",
        "approval_rollback": "退回审批",
        "approval_remind": "催办审批",
        "approval_cancel": "撤回审批",
        "approval_cc": "抄送审批",
        "task_create": "创建任务",
        "task_complete": "更新任务",
        "calendar_create": "创建日程",
        "mail_draft_create": "创建邮件草稿",
    }
    return labels.get(strategy, "")


def _enqueue_approval_workbench_reply(
    *,
    app_config_id: str,
    question: str,
    normalized: str,
    identity: Any,
    chat_id: str | None,
    message_id: str | None,
    reply_target: dict[str, str],
) -> None:
    from app.tasks.celery_app import celery_app
    from app.services.runtime_v5.action_observer import record_action_trace

    record_action_trace(
        chat_id,
        {
            "kind": "approval_workbench",
            "action": "approval_workbench",
            "status": "queued",
            "route_path": "feishu_approval_task_query",
        },
    )

    celery_app.send_task(
        "bot.approvals.workbench_reply",
        args=[
            app_config_id,
            question,
            normalized,
            {
                "open_id": getattr(identity, "open_id", None),
                "role": getattr(identity, "role", None),
                "access_scope": getattr(identity, "access_scope", None),
                "display_name": getattr(identity, "display_name", None),
                "source": getattr(identity, "source", None),
                "domains": list(getattr(identity, "domains", ()) or ()),
                "allowed_resources": list(getattr(identity, "allowed_resources", ()) or ()),
                "email": getattr(identity, "email", None),
            },
            chat_id,
            reply_target,
            message_id,
        ],
    )


def _remember_latest_user_message(chat_id: str | None, message_id: str | None) -> None:
    if not chat_id or not message_id:
        return
    session_context = load_session_context(chat_id)
    session_context["runtime_v5_latest_user_message_id"] = message_id
    save_session_context(chat_id, session_context)


def _looks_like_pending_approval_query(text: str) -> bool:
    if any(term in text for term in ("详情", "明细", "展开", "通过", "同意", "拒绝", "驳回")):
        return False
    return (
        any(term in text for term in ("待我审批", "等待我审批", "需要我审批", "待审批", "待审", "审批的单子", "审批有哪些"))
        or ("审批" in text and any(term in text for term in ("哪些", "有没有", "查一下", "查看", "待我", "需要我")))
    )


def _should_use_approval_manual_fast_path(text: str) -> bool:
    if not _looks_like_pending_approval_query(text):
        return False
    return any(term in text for term in ("分组", "批量", "勾选", "风险", "审批建议", "整理"))


def _authorization_result_fields(actions: list[dict[str, Any]], *, identity: Any) -> dict[str, Any]:
    if not actions:
        return {}
    resources: list[str] = []
    for action in actions:
        resource_type = str(action.get("resource_type") or "").strip()
        if resource_type and resource_type not in resources:
            resources.append(resource_type)
    owner_open_id = getattr(identity, "open_id", None)
    return {
        "user_identity_authorization_required": True,
        "user_identity_authorization_actions": actions,
        "required_user_identity_resources": resources,
        "authorization_owner_open_id": owner_open_id,
        "authorization_action_count": len(actions),
    }


def _authorization_actions_from_runtime_or_trace(trace_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    runtime_actions = _authorization_actions_from_runtime_result(trace_payload)
    if runtime_actions:
        return runtime_actions
    return authorization_card_entrypoint.authorization_actions_from_trace(trace_payload)


def _authorization_actions_from_runtime_result(trace_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(trace_payload, dict):
        return []
    composed = trace_payload.get("composed") if isinstance(trace_payload.get("composed"), dict) else {}
    metadata = composed.get("metadata") if isinstance(composed.get("metadata"), dict) else {}
    runtime_result = metadata.get("runtime_result") if isinstance(metadata.get("runtime_result"), dict) else {}
    actions = runtime_result.get("actions") if isinstance(runtime_result.get("actions"), list) else []
    authorization = runtime_result.get("metadata", {}).get("authorization") if isinstance(runtime_result.get("metadata"), dict) else {}
    normalized: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict) or action.get("action") != "authorize_user_identity":
            continue
        normalized.append(
            {
                "resource_type": action.get("resource_type") or "user_identity_bundle",
                "label": action.get("label") or "授权个人能力包",
                "channel": action.get("channel") or "feishu_oauth",
                "url": action.get("url") or (authorization.get("url") if isinstance(authorization, dict) else ""),
                "authorization_flow": action.get("authorization_flow") or (authorization.get("authorization_flow") if isinstance(authorization, dict) else ""),
                "covered_resources": authorization.get("covered_resources", []) if isinstance(authorization, dict) else [],
                "owner_open_id": authorization.get("owner_open_id", "") if isinstance(authorization, dict) else "",
                "authorization_status": action.get("authorization_status") or (authorization.get("authorization_status") if isinstance(authorization, dict) else ""),
                "provider_boundary": authorization.get("provider_boundary", "") if isinstance(authorization, dict) else "",
            }
        )
    return [action for action in normalized if str(action.get("url") or "").strip()]


def _user_identity_action_summaries(actions: Any) -> list[dict[str, Any]]:
    if not isinstance(actions, list):
        return []
    summaries: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        summaries.append(
            {
                "resource_type": action.get("resource_type"),
                "label": action.get("label"),
                "channel": action.get("channel"),
            }
        )
    return summaries


def _agent_runtime_trace_summary(trace_payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(trace_payload, dict):
        return None
    summary = {
        "runtime_version": trace_payload.get("runtime_version"),
        "semantic_intent": trace_payload.get("semantic_intent"),
        "route_path": trace_payload.get("route_path"),
        "route_scope": trace_payload.get("route_scope"),
        "route_reason": trace_payload.get("route_reason"),
        "scope_label": trace_payload.get("scope_label"),
        "route_label": trace_payload.get("route_label"),
        "strategy": trace_payload.get("strategy"),
        "sources": trace_payload.get("sources"),
        "question_type": trace_payload.get("question_type"),
        "data_scope": trace_payload.get("data_scope"),
        "requires_confirmation": trace_payload.get("requires_confirmation"),
        "execution_identity": trace_payload.get("execution_identity"),
        "execution_status": trace_payload.get("execution_status"),
        "agent_identity": trace_payload.get("agent_identity"),
        "reply_mode": trace_payload.get("reply_mode"),
        "runtime_contract": trace_payload.get("runtime_contract"),
    }
    if trace_payload.get("memory_context") is not None:
        summary["memory_context"] = trace_payload.get("memory_context")
    if trace_payload.get("thinking_preview") is not None:
        summary["thinking_preview"] = trace_payload.get("thinking_preview")
    if trace_payload.get("runtime_version") == "v5":
        summary["runtime_v5"] = _runtime_v5_trace_summary(trace_payload)
    return {key: value for key, value in summary.items() if value is not None}


def _runtime_v5_trace_summary(trace_payload: dict[str, Any]) -> dict[str, Any]:
    execution = trace_payload.get("execution") if isinstance(trace_payload.get("execution"), dict) else {}
    provider_results = execution.get("provider_results") if isinstance(execution.get("provider_results"), list) else []
    return {
        "intent": trace_payload.get("intent"),
        "plan": trace_payload.get("plan"),
        "permission": trace_payload.get("permission"),
        "execution": {
            "strategy": execution.get("strategy"),
            "status": execution.get("status"),
            "provider_results": [
                {
                    "source": item.get("source"),
                    "status": item.get("status"),
                    "result_type": item.get("result_type"),
                    "count": item.get("count"),
                    "error": item.get("error"),
                }
                for item in provider_results
                if isinstance(item, dict)
            ],
        },
        "composed": trace_payload.get("composed"),
    }


async def _send_authorization_card_if_needed(
    *,
    app_config: FeishuAppConfig,
    identity: Any,
    reply_target: dict[str, str],
    answer: str,
    actions: list[dict[str, Any]],
) -> bool:
    if not actions:
        return False
    return await authorization_card_entrypoint.send_user_identity_authorization_card(
        app_config,
        identity,
        reply_target,
        answer=answer,
        actions=actions,
    )


def _should_send_thinking_notice(trace_payload: dict[str, Any] | None) -> bool:
    if not isinstance(trace_payload, dict):
        return False
    thinking_preview = trace_payload.get("thinking_preview")
    if isinstance(thinking_preview, dict):
        return thinking_preview.get("send_before_final_answer") is True
    reply_mode = trace_payload.get("reply_mode")
    return isinstance(reply_mode, dict) and reply_mode.get("show_thinking_map") is True


def _thinking_notice_text(trace_payload: dict[str, Any] | None) -> str:
    if not isinstance(trace_payload, dict):
        return "思考中：正在分析企业数据，稍后给出结论。"
    thinking_preview = trace_payload.get("thinking_preview") if isinstance(trace_payload.get("thinking_preview"), dict) else {}
    reply_mode = trace_payload.get("reply_mode") if isinstance(trace_payload.get("reply_mode"), dict) else {}
    route_label = trace_payload.get("route_label") or "业务问题"
    data_requirement = reply_mode.get("data_requirement") or "WorkEvent / Knowledge / Memory / Tool"
    frames = thinking_preview.get("frames") if isinstance(thinking_preview, dict) else None
    frame_labels = " -> ".join(str(item.get("label")) for item in frames if isinstance(item, dict) and item.get("label")) if isinstance(frames, list) else ""
    suffix = f"\n动态思考图：{frame_labels}" if frame_labels else ""
    return f"思考中：正在按「{route_label}」读取 {data_requirement}，稍后给出结论、依据和下一步。{suffix}"


def _agent_runtime_step_count(trace_payload: dict[str, Any] | None) -> int | None:
    if not isinstance(trace_payload, dict):
        return None
    steps = trace_payload.get("steps")
    return len(steps) if isinstance(steps, list) else None


def _agent_runtime_tool_steps(trace_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(trace_payload, dict):
        return []
    steps = trace_payload.get("steps")
    if not isinstance(steps, list):
        return []
    tool_steps: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict) or step.get("kind") != "tool":
            continue
        metadata = step.get("metadata") if isinstance(step.get("metadata"), dict) else {}
        item = {
            "name": step.get("name"),
            "status": step.get("status"),
            "provider": metadata.get("provider"),
            "business_tool": metadata.get("business_tool"),
            "data_source": metadata.get("data_source"),
            "execution_source": metadata.get("execution_source"),
            "source_chain": metadata.get("source_chain"),
            "data_permission_model": metadata.get("data_permission_model"),
            "enterprise_identity_boundary": metadata.get("enterprise_identity_boundary"),
            "user_identity_boundary": metadata.get("user_identity_boundary"),
            "cannot_escalate_original_permissions": metadata.get("cannot_escalate_original_permissions"),
            "tool_returns_structured_result": metadata.get("tool_returns_structured_result"),
            "final_answer_owner": metadata.get("final_answer_owner"),
            "error": metadata.get("error"),
        }
        if metadata.get("cli_profile"):
            item["cli_profile"] = metadata.get("cli_profile")
            item["cli_profile_source"] = metadata.get("cli_profile_source")
        tool_steps.append(item)
    return tool_steps


def _agent_runtime_workflow_steps(trace_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(trace_payload, dict):
        return []
    steps = trace_payload.get("steps")
    if not isinstance(steps, list):
        return []
    workflow_steps: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict) or step.get("kind") != "workflow":
            continue
        metadata = step.get("metadata") if isinstance(step.get("metadata"), dict) else {}
        workflow_steps.append(
            {
                "name": step.get("name"),
                "status": step.get("status"),
                "workflow": metadata.get("workflow"),
                "route_path": metadata.get("route_path"),
                "route_label": metadata.get("route_label"),
                "reply_mode_id": metadata.get("reply_mode_id"),
                "final_answer_owner": metadata.get("final_answer_owner"),
                "error": metadata.get("error"),
            }
        )
    return workflow_steps
