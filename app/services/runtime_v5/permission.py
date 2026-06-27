from __future__ import annotations

from app.services.runtime_v5.capabilities import capabilities_for_strategy, execution_identity_for_strategy, strategy_requires_confirmation
from app.services.runtime_v5.execution_identity import allows_user_fallback_for_query, is_bot_first_query_strategy
from app.services.identity_fact import identity_fact_from_context, identity_fact_payload
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
    bot_first_query = intent.question_type == "query" or is_bot_first_query_strategy(plan.strategy)
    if bot_first_query:
        execution_identity = "bot"
        execution_identity_source = "bot_first_query_policy"
    else:
        execution_identity = requested_identity if requested_identity in {"bot", "user"} else default_identity
        execution_identity_source = "user_requested" if requested_identity in {"bot", "user"} else "runtime_default"
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
    allowed = _is_company_allowed(context, intent)
    user_fallback_allowed = _user_fallback_allowed(
        intent=intent,
        plan=plan,
        bot_first_query=bot_first_query,
    )
    source_resource_types = list(dict.fromkeys(plan.sources))
    metadata = {
        "strategy": plan.strategy,
        "sources": list(plan.sources),
        "question_type": intent.question_type,
        "data_scope": intent.data_scope,
        "requested_identity": requested_identity,
        "default_identity": default_identity,
        "execution_identity": execution_identity,
        "execution_identity_source": execution_identity_source,
        "query_identity_policy": "bot_first" if bot_first_query else "",
        "user_fallback_allowed": user_fallback_allowed,
        "policy_subject": _policy_subject(context),
        "policy_scope": _policy_scope(context=context, intent=intent),
        "identity_decision": _identity_decision(
            execution_identity=execution_identity,
            bot_first_query=bot_first_query,
            user_fallback_allowed=user_fallback_allowed,
        ),
        "allowed_resource_types": source_resource_types if allowed else [],
        "denied_resource_types": [] if allowed else source_resource_types,
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
        "intent_contract": _intent_contract(intent, plan=plan),
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

    return PermissionDecision(
        allowed=allowed,
        reason="" if allowed else "permission_denied",
        requires_confirmation=requires_confirmation,
        execution_identity=execution_identity,
        metadata=metadata,
    )


def _policy_subject(context: RuntimeContext) -> dict[str, object]:
    identity = identity_fact_from_context(context)
    organization_subject = context.organization_subject if isinstance(context.organization_subject, dict) else {}
    organization_department_ids = _string_list(organization_subject.get("department_ids"))
    organization_department_names = _string_list(organization_subject.get("department_names"))
    department_ids = organization_department_ids or ([identity.department_id] if identity.department_id else [])
    department_names = organization_department_names or list(identity.department_names)
    managed_departments = organization_subject.get("managed_departments")
    management_scope = organization_subject.get("management_scope")
    return {
        "actor_user_id": str(organization_subject.get("actor_user_id") or identity.user_id),
        "actor_open_id": str(organization_subject.get("actor_open_id") or identity.open_id),
        "company_id": str(context.runtime_scope.active_company_id or ""),
        "role": identity.role,
        "departments": department_ids,
        "department_names": department_names,
        "managed_departments": managed_departments if isinstance(managed_departments, list) else [],
        "management_scope": management_scope if isinstance(management_scope, list) else [],
        "subject_source": str(organization_subject.get("source") or "runtime_identity"),
        "is_owner": identity.is_owner,
        "is_admin": identity.is_admin,
        "identity_fact": identity_fact_payload(context),
    }


def _string_list(value: object) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _policy_scope(*, context: RuntimeContext, intent: IntentResult) -> dict[str, str]:
    return {
        "requested_scope": intent.data_scope,
        "resolved_scope": intent.data_scope,
        "target_user_id": str(intent.entities.get("target_user_id") or intent.entities.get("user_id") or ""),
        "target_department_id": str(
            intent.entities.get("target_department_id")
            or intent.entities.get("department_id")
            or context.runtime_scope.active_department_id
            or ""
        ),
        "target_company_id": str(context.runtime_scope.active_company_id or ""),
        "target_group_id": str(intent.entities.get("target_group_id") or intent.entities.get("group_id") or ""),
    }


def _intent_contract(intent: IntentResult, *, plan: PlannerResult) -> dict[str, object]:
    domain_query = intent.entities.get("domain_query") if isinstance(intent.entities.get("domain_query"), dict) else {}
    operation_kind = str(domain_query.get("operation_kind") or _intent_contract_operation_kind(intent))
    return {
        "intent": intent.intent,
        "question_type": intent.question_type,
        "operation_kind": operation_kind,
        "domain": str(domain_query.get("domain") or _intent_contract_domain(intent, plan=plan)),
        "scope": intent.data_scope,
        "presentation_hint": str(domain_query.get("presentation_hint") or ""),
        "risk_hint": str(domain_query.get("risk_hint") or _intent_contract_risk_hint(intent, operation_kind=operation_kind)),
        "evidence_requirement": str(domain_query.get("evidence_requirement") or ""),
        "needs_clarification": intent.needs_clarification,
    }


def _intent_contract_operation_kind(intent: IntentResult) -> str:
    if intent.question_type != "action":
        return "read"
    if intent.intent == "mail_draft_create":
        return "draft"
    if intent.intent == "message_send":
        return "send"
    if intent.intent == "organization_export":
        return "export"
    if intent.intent.startswith("approval_"):
        return "approval_action"
    return "write"


def _intent_contract_domain(intent: IntentResult, *, plan: PlannerResult) -> str:
    if plan.sources:
        source = plan.sources[0]
        aliases = {
            "people": "people",
            "base": "business",
            "mail": "communication",
            "im": "communication",
            "task": "workspace",
            "calendar": "workspace",
            "approval": "process",
            "knowledge": "knowledge",
        }
        if source in aliases:
            return aliases[source]
    prefix = intent.intent.split("_", 1)[0]
    return {
        "people": "people",
        "organization": "people",
        "department": "people",
        "mail": "communication",
        "message": "communication",
        "chat": "communication",
        "task": "workspace",
        "calendar": "workspace",
        "approval": "process",
        "general": "knowledge",
    }.get(prefix, "")


def _intent_contract_risk_hint(intent: IntentResult, *, operation_kind: str) -> str:
    if intent.needs_clarification:
        return "clarify"
    if operation_kind in {"send", "approval_action", "write", "export"}:
        return "high"
    if operation_kind == "draft":
        return "medium"
    return "low"


def _identity_decision(
    *,
    execution_identity: str,
    bot_first_query: bool,
    user_fallback_allowed: bool,
) -> dict[str, object]:
    actor_identity = "BOT" if execution_identity == "bot" else "USER"
    credential_mode = "TENANT_TOKEN" if execution_identity == "bot" else "USER_TOKEN"
    return {
        "actor_identity": actor_identity,
        "credential_mode": credential_mode,
        "allows_fallback": user_fallback_allowed,
        "requires_authorization": bool(user_fallback_allowed and not bot_first_query),
        "authorization_status": "AUTHORIZED" if execution_identity == "bot" else "UNKNOWN",
    }


def _user_fallback_allowed(
    *,
    intent: IntentResult,
    plan: PlannerResult,
    bot_first_query: bool,
) -> bool:
    return bool(bot_first_query and intent.data_scope == "self" and allows_user_fallback_for_query(plan.strategy))


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
