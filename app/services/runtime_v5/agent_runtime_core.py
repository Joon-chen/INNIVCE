from __future__ import annotations

from uuid import uuid4

from app.services.runtime_v5.capability_router import CapabilityRouter, ResourceProvider
from app.services.runtime_v5.models import CommandPlan, ExecutionResult, PermissionDecision, RuntimeContext, RuntimeStep, RuntimeTask


def build_runtime_task(*, command_plan: CommandPlan) -> RuntimeTask:
    """Create the V5 Runtime task state object.

    Runtime is the only execution entry. Tools remain passive and are invoked
    through the router from here.
    """

    steps = tuple(
        RuntimeStep(
            source=str(step.get("source") or ""),
            operation=str(step.get("strategy") or command_plan.planner_result.strategy),
            metadata=dict(step),
        )
        for step in command_plan.steps
    )
    return RuntimeTask(
        task_id=uuid4().hex,
        intent=command_plan.intent,
        strategy=command_plan.planner_result.strategy,
        status="pending",
        steps=steps,
        metadata={
            "target_ui": command_plan.target_ui,
            "tool_candidates": list(command_plan.tool_candidates),
            "context_scope": command_plan.context_scope,
        },
    )


def execute_runtime_task(
    *,
    context: RuntimeContext,
    command_plan: CommandPlan,
    permission: PermissionDecision,
    providers: dict[str, ResourceProvider] | None,
) -> tuple[RuntimeTask, ExecutionResult]:
    task = build_runtime_task(command_plan=command_plan)
    execution = CapabilityRouter(providers=providers).execute(
        context=context,
        intent=command_plan.intent_result,
        plan=command_plan.planner_result,
        permission=permission,
    )
    terminal_status = "done" if execution.status == "success" else "failed"
    task = RuntimeTask(
        task_id=task.task_id,
        intent=task.intent,
        strategy=task.strategy,
        status=terminal_status,
        steps=tuple(
            RuntimeStep(
                source=step.source,
                operation=step.operation,
                status=terminal_status,
                metadata=step.metadata,
            )
            for step in task.steps
        ),
        metadata={**task.metadata, "execution_status": execution.status},
    )
    return task, execution
