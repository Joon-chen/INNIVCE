from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.services.runtime_v5.capabilities import capabilities_for_strategy
from app.services.runtime_v5.command_frame import build_command_frame, intent_with_command_frame
from app.services.runtime_v5.dialogue_resolver import build_conversation_first_frame, conversation_first_intent_result
from app.services.runtime_v5.intent import recognize_intent
from app.services.runtime_v5.intent_layers import build_intent_layer_decision
from app.services.runtime_v5.models import CommandPlan, RuntimeContext, TargetUI
from app.services.runtime_v5.planner import plan_task


def build_command_plan(
    *,
    context: RuntimeContext,
    runtime_provider_snapshot: dict[str, Any] | None = None,
) -> CommandPlan:
    """Build the V5 Command Layer plan.

    This layer only plans. It does not check permissions, execute tools,
    render cards, or send replies.
    """

    command_frame = build_conversation_first_frame(context)
    if _conversation_first_v1_supported(context, command_frame):
        if _conversation_first_v1_allowed(context) or _conversation_first_strict_domain(command_frame):
            intent = conversation_first_intent_result(context=context, frame=command_frame)
        else:
            intent = recognize_intent(context.current_message, context)
            command_frame = None
    else:
        intent = recognize_intent(context.current_message, context)
        command_frame = None
    planner = plan_task(intent, runtime_provider_snapshot=runtime_provider_snapshot)
    if command_frame is not None and command_frame.domain == "Communication":
        command_frame = _conversation_first_action_frame(
            context=context,
            command_frame=command_frame,
            intent=intent,
            planner=planner,
        )
        intent = intent_with_command_frame(intent, command_frame)
    if command_frame is None:
        command_frame = build_command_frame(context=context, intent=intent, planner=planner)
        intent = intent_with_command_frame(intent, command_frame)
    return CommandPlan(
        intent=intent.intent,
        steps=_command_steps(strategy=planner.strategy, sources=planner.sources),
        target_ui=_target_ui_for(strategy=planner.strategy, question_type=intent.question_type),
        tool_candidates=tuple(
            capability.operation
            for capability in capabilities_for_strategy(planner.strategy, planner.sources)
        ),
        context_scope={
            "scope_type": context.runtime_scope.scope_type,
            "company_id": str(context.runtime_scope.active_company_id or ""),
            "company_ids": [str(company_id) for company_id in context.runtime_scope.company_ids],
            "data_scope": intent.data_scope,
        },
        intent_result=intent,
        planner_result=planner,
        command_frame=command_frame,
    )


def _conversation_first_v1_allowed(context: RuntimeContext) -> bool:
    result_context = context.result_context
    metadata = result_context.metadata if result_context is not None and isinstance(result_context.metadata, dict) else {}
    if metadata.get("execution_status") == "clarification":
        operation = str(metadata.get("operation") or "")
        return operation.startswith("people") or operation in {"organization_snapshot", "general_query"}
    if context.session_context.get("runtime_v5_action_input") or _has_blocking_runtime_state(context.session_context):
        return False
    return True


def _conversation_first_strict_domain(command_frame) -> bool:
    return getattr(command_frame, "domain", "") in {"People", "Knowledge"}


def _has_blocking_runtime_state(session_context: dict[str, Any]) -> bool:
    state = session_context.get("runtime_v5_state")
    if not isinstance(state, dict):
        return False
    actions = state.get("actions")
    if not isinstance(actions, (list, tuple)):
        return False
    blocking_statuses = {"waiting_input", "waiting_confirmation", "confirmed", "executing"}
    return any(isinstance(action, dict) and str(action.get("status") or "") in blocking_statuses for action in actions)


def _conversation_first_v1_supported(context: RuntimeContext, command_frame) -> bool:
    from app.services.runtime_v5.intent import _reserved_non_v1_or_action_surface

    if _reserved_non_v1_or_action_surface(question=context.current_message, context=context):
        return False
    if command_frame.domain in {"People", "Knowledge"}:
        return not _non_conversation_first_action_requested(context)
    return _conversation_first_communication_allowed(context, command_frame)


def _non_conversation_first_action_requested(context: RuntimeContext) -> bool:
    from app.services.runtime_v5.intent import _recognize_intent_by_rules

    rule_intent = _recognize_intent_by_rules(context.current_message, context)
    if rule_intent.question_type != "action":
        return False
    return rule_intent.intent not in {"message_send"}


def _conversation_first_communication_allowed(context: RuntimeContext, command_frame) -> bool:
    if command_frame.domain != "Communication" or command_frame.intent != "message_send":
        return False
    from app.services.runtime_v5.intent import _recognize_intent_by_rules

    rule_intent = _recognize_intent_by_rules(context.current_message, context)
    return rule_intent.intent == "message_send"


def _conversation_first_action_frame(*, context: RuntimeContext, command_frame, intent, planner):
    layers = build_intent_layer_decision(context=context, intent=intent, planner=planner)
    gates = dict(layers.gates)
    route_gate = gates.get("route") if isinstance(gates.get("route"), dict) else {}
    gates["route"] = {
        **route_gate,
        "path": command_frame.route_path,
        "reason": command_frame.route_reason,
    }
    return replace(
        command_frame,
        context_mode=layers.context_mode,
        action_type=layers.action_type,
        safety_level=layers.safety_level,
        missing_slots=intent.missing_params,
        gates=gates,
        route_reason="dialogue_resolver",
        route_path="conversation_first_v1",
    )


def _command_steps(*, strategy: str, sources: tuple[str, ...]) -> tuple[dict[str, Any], ...]:
    if not sources:
        return ({"kind": "compose", "strategy": strategy},)
    return tuple(
        {
            "kind": "provider",
            "strategy": strategy,
            "source": source,
        }
        for source in sources
    )


def _target_ui_for(*, strategy: str, question_type: str) -> TargetUI:
    if strategy == "approval_query":
        return "card"
    if strategy in {"approval_detail", "approval_approve", "approval_reject", "approval_transfer", "approval_add_sign"}:
        return "sidepanel"
    if question_type == "action":
        return "card"
    if strategy in {"organization_export", "sheets_write", "drive_upload"}:
        return "webview"
    return "card"
