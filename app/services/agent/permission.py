"""Permission Check — Step 4 of the Agent Runtime pipeline.

Checks permissions for each planned tool step before execution.
Annotates steps with permitted/denied status.
"""

from dataclasses import replace as _replace
from typing import Any

from app.services.agent.planner import AgentPlan
from app.services.tools.router import TOOL_REGISTRY, preflight_agent_tool_data_permission
from app.services.tools.base import ToolContext, ToolRequest


def check_plan_permissions(plan: AgentPlan, tool_context: ToolContext) -> AgentPlan:
    """Check permissions for each plan step.

    For each tool step:
      - If tool supports writes: check write permission.
      - If denied: set step.permitted = False, step.deny_reason.
      - If not a tool step or no restriction: pass through unchanged.

    Returns a new AgentPlan with annotated steps.
    Does not call any external tool or API.
    Keeps existing permission logic — does not change permission results.
    """
    new_steps: list[Any] = []
    for step in plan.steps:
        if step.kind != "tool":
            new_steps.append(step)
            continue

        definition = TOOL_REGISTRY.get(step.name)
        if definition is not None and definition.supports_write:
            tool_request = ToolRequest(
                tool_name=step.name,
                question="",
                normalized_command="",
                params=step.metadata.get("tool_params", {}),
            )
            result = preflight_agent_tool_data_permission(tool_context, tool_request)
            if result is not None:
                from app.services.agent.planner import AgentPlanStep as _Step
                new_steps.append(_Step(
                    kind=step.kind,
                    name=step.name,
                    purpose=step.purpose,
                    metadata=step.metadata,
                    required=step.required,
                    on_error=step.on_error,
                    depends_on=step.depends_on,
                    permitted=False,
                    deny_reason=result.error or "permission_denied",
                ))
                continue

        new_steps.append(step)

    return _replace(plan, steps=tuple(new_steps))
