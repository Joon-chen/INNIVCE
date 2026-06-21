from __future__ import annotations

from app.services.runtime_v5.capabilities import capabilities_for_strategy, execution_identity_for_strategy, strategy_requires_confirmation
from app.services.runtime_v5.models import IntentResult, PermissionDecision, PlannerResult, RuntimeContext


_HIGH_RISK_ACTIONS = {
    "approval_decision",
    "approval_approve",
    "approval_reject",
    "approval_transfer",
    "approval_add_sign",
    "approval_rollback",
    "approval_remind",
    "approval_cancel",
    "approval_cc",
    "message_send",
    "calendar_create",
    "mail_draft_create",
    "mail_send",
    "task_create",
    "task_complete",
    "task_update",
    "task_reopen",
    "task_delete",
    "task_subtask_create",
    "task_comment",
    "task_assign_members",
    "task_update_followers",
    "task_update_reminders",
    "task_upload_attachment",
    "task_add_to_tasklist",
    "task_set_ancestor",
    "task_clear_ancestor",
    "tasklist_create",
    "tasklist_update",
    "tasklist_delete",
    "tasklist_update_members",
    "tasklist_set_members",
    "task_section_create",
    "task_section_update",
    "task_section_delete",
    "chat_create",
    "chat_auto_join_public",
    "data_update",
    "organization_export",
}


def check_runtime_permission(
    *,
    context: RuntimeContext,
    intent: IntentResult,
    plan: PlannerResult,
) -> PermissionDecision:
    requested_identity = str(intent.entities.get("execution_identity") or "").strip()
    source_capabilities = capabilities_for_strategy(plan.strategy, plan.sources)
    default_identity = execution_identity_for_strategy(plan.strategy, intent.question_type, plan.sources)
    execution_identity = requested_identity if requested_identity in {"bot", "user"} else default_identity
    requires_confirmation = (
        strategy_requires_confirmation(plan.strategy, plan.sources)
        or intent.intent in _HIGH_RISK_ACTIONS
        or plan.strategy in _HIGH_RISK_ACTIONS
        or intent.question_type == "action"
    )
    high_risk_action = intent.intent in _HIGH_RISK_ACTIONS or plan.strategy in _HIGH_RISK_ACTIONS
    confirmation_reasons = _confirmation_reasons(
        intent=intent,
        plan=plan,
        source_capabilities=source_capabilities,
        requires_confirmation=requires_confirmation,
    )
    metadata = {
        "strategy": plan.strategy,
        "sources": list(plan.sources),
        "question_type": intent.question_type,
        "data_scope": intent.data_scope,
        "requested_identity": requested_identity,
        "default_identity": default_identity,
        "execution_identity": execution_identity,
        "execution_identity_source": "user_requested" if requested_identity in {"bot", "user"} else "runtime_default",
        "requires_confirmation": requires_confirmation,
        "high_risk_action": high_risk_action,
        "action_question": intent.question_type == "action",
        "confirmation_policy": "required" if requires_confirmation else "not_required",
        "write_confirmation_contract": {
            "status": "ready" if (not requires_confirmation or execution_identity == "user") else "incomplete",
            "contract": "dry_run_then_confirmation_token" if requires_confirmation else "readonly",
            "requires_dry_run": bool(requires_confirmation),
            "requires_confirmation_token": bool(requires_confirmation),
            "requires_confirmation": requires_confirmation,
            "execution_identity": execution_identity,
            "strategy": plan.strategy,
            "sources": list(plan.sources),
        },
        "source_capabilities": [
            {
                "source": item.source,
                "operation": item.operation,
                "label": item.label,
                "requires_confirmation": item.requires_confirmation,
                "execution_identity": item.execution_identity,
            }
            for item in source_capabilities
        ],
        "confirmation_reasons": confirmation_reasons,
    }

    if context.runtime_scope.scope_type in {"multi_company", "all_companies"}:
        per_company = {
            str(company_id): PermissionDecision(
                allowed=_is_company_allowed(context, intent),
                reason="",
                requires_confirmation=requires_confirmation,
                execution_identity=execution_identity,
                metadata=metadata,
            )
            for company_id in context.runtime_scope.company_ids
        }
        allowed = any(item.allowed for item in per_company.values())
        return PermissionDecision(
            allowed=allowed,
            reason="" if allowed else "no_company_permission",
            requires_confirmation=requires_confirmation,
            execution_identity=execution_identity,
            per_company=per_company,
            metadata=metadata,
        )

    allowed = _is_company_allowed(context, intent)
    return PermissionDecision(
        allowed=allowed,
        reason="" if allowed else "permission_denied",
        requires_confirmation=requires_confirmation,
        execution_identity=execution_identity,
        metadata=metadata,
    )


def _confirmation_reasons(
    *,
    intent: IntentResult,
    plan: PlannerResult,
    source_capabilities,
    requires_confirmation: bool,
) -> list[str]:
    if not requires_confirmation:
        return []
    reasons: list[str] = []
    if any(item.requires_confirmation for item in source_capabilities):
        reasons.append("capability_requires_confirmation")
    if intent.intent in _HIGH_RISK_ACTIONS or plan.strategy in _HIGH_RISK_ACTIONS:
        reasons.append("high_risk_action")
    if intent.question_type == "action":
        reasons.append("action_question")
    return reasons


def _is_company_allowed(context: RuntimeContext, intent: IntentResult) -> bool:
    role = context.identity.role
    if role in {"owner", "admin"}:
        return True
    if intent.data_scope == "self":
        return True
    if intent.data_scope in {"person", "department", "organization", "company"}:
        return bool(context.identity.domains)
    return True
