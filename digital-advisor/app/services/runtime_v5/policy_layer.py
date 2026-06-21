from __future__ import annotations

from dataclasses import replace

from app.services.runtime_v5.models import CommandPlan, PermissionDecision, RuntimeContext
from app.services.runtime_v5.permission import check_runtime_permission


def evaluate_policy(
    *,
    context: RuntimeContext,
    command_plan: CommandPlan,
) -> PermissionDecision:
    """Apply V5 Policy & Context rules.

    Policy does not plan, execute providers, or render UI. It only decides
    whether the already planned request is allowed in the current tenant
    context and whether confirmation is required.
    """

    tenant_decision = _tenant_boundary_decision(context)
    if tenant_decision is not None:
        return tenant_decision
    permission = check_runtime_permission(
        context=context,
        intent=command_plan.intent_result,
        plan=command_plan.planner_result,
    )
    return replace(
        permission,
        metadata={
            **permission.metadata,
            "policy_layer": "v5",
            "tenant_boundary": {
                "scope_type": context.runtime_scope.scope_type,
                "active_company_id": str(context.runtime_scope.active_company_id or ""),
                "company_ids": [str(company_id) for company_id in context.runtime_scope.company_ids],
                "company_id_required": True,
            },
        },
    )


def _tenant_boundary_decision(context: RuntimeContext) -> PermissionDecision | None:
    scope = context.runtime_scope
    if scope.scope_type == "single_company" and scope.active_company_id is None:
        return PermissionDecision(
            allowed=False,
            reason="missing_company_id",
            metadata={
                "policy_layer": "v5",
                "tenant_boundary": {
                    "company_id_required": True,
                    "scope_type": scope.scope_type,
                    "violation": "missing_active_company_id",
                },
            },
        )
    if scope.scope_type in {"multi_company", "all_companies"} and not scope.company_ids:
        return PermissionDecision(
            allowed=False,
            reason="missing_company_scope",
            metadata={
                "policy_layer": "v5",
                "tenant_boundary": {
                    "company_id_required": True,
                    "scope_type": scope.scope_type,
                    "violation": "missing_company_ids",
                },
            },
        )
    return None
