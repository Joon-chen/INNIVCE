from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.services.runtime_v5.capabilities import capabilities_for_strategy
from app.services.runtime_v5.command_frame import command_frame_payload
from app.services.runtime_v5.dialogue_resolver import build_conversation_first_frame, conversation_first_intent_result
from app.services.runtime_v5.explicit_command import explicit_command_intent
from app.services.runtime_v5.models import CommandFrame, CommandPlan, IntentResult, RuntimeContext, TargetUI
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

    explicit_intent = explicit_command_intent(context.current_message, context)
    if explicit_intent is not None:
        command_frame = _explicit_command_frame(context=context, intent=explicit_intent)
        intent = replace(
            explicit_intent,
            entities={**explicit_intent.entities, "command_frame": command_frame_payload(command_frame)},
        )
    else:
        command_frame = build_conversation_first_frame(context)
        intent = conversation_first_intent_result(context=context, frame=command_frame)
    planner = plan_task(intent, runtime_provider_snapshot=runtime_provider_snapshot)
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


def _explicit_command_frame(*, context: RuntimeContext, intent: IntentResult) -> CommandFrame:
    explicit = intent.entities.get("explicit_command") if isinstance(intent.entities.get("explicit_command"), dict) else {}
    family = str(explicit.get("family") or "")
    return CommandFrame(
        utterance_type="explicit_command",
        dialogue_mode="answer",
        user_goal=context.current_message,
        intent=intent.intent,
        question_type=intent.question_type,
        domain="System",
        context_mode="new_question",
        skill_intent=intent.intent,
        scope=intent.data_scope,
        action_type="read",
        safety_level="low",
        params={"explicit_command": explicit},
        gates={
            "utterance": {"type": "explicit_command"},
            "domain": {
                "domain": "System",
                "reason": f"explicit_command:{family}" if family else "explicit_command",
                "source": "explicit_command",
                "confidence": 1.0,
            },
            "scope": {
                "scope": intent.data_scope,
                "requested_scope": intent.data_scope,
                "resolved_scope": intent.data_scope,
                "reason": "explicit_command_scope",
                "source": "explicit_command",
                "resource_boundary": "control_plane",
                "target": {"type": intent.data_scope},
            },
            "safety": {"level": "low", "confirmation_expected": False, "blocks_execution": False, "risk_reasons": []},
            "route": {"path": "explicit_command", "foundation_route": "", "source": "explicit_command"},
        },
        response_intent={
            "mode": "natural_text",
            "surface": "text",
            "natural_language": True,
            "tone": "concise",
            "should_render_card": False,
            "intro_intent": "natural_reply",
        },
        confidence=1.0,
        route_reason=f"explicit_command:{family}" if family else "explicit_command",
        route_path="explicit_command",
    )
