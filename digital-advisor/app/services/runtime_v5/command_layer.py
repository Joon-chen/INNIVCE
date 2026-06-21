from __future__ import annotations

from typing import Any

from app.services.runtime_v5.capabilities import capabilities_for_strategy
from app.services.runtime_v5.intent import recognize_intent
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

    intent = recognize_intent(context.current_message, context)
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
