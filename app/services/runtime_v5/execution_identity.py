from __future__ import annotations

from app.services.runtime_v5.models import (
    CredentialMode,
    CredentialOwner,
    ExecutionIdentity,
    ExecutionIdentityContract,
    RuntimeContext,
)


_USER_TOKEN_STRATEGIES = {
    "approval_approve",
    "approval_reject",
    "approval_transfer",
    "approval_add_sign",
    "approval_rollback",
    "approval_cancel",
    "approval_cc",
    "approval_remind",
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
    "mail_query",
    "mail_search",
    "mail_get_message",
    "mail_draft_create",
    "calendar_create",
    "attendance_query",
}

_TENANT_TOKEN_STRATEGIES = {
    "approval_query",
    "approval_detail",
    "approval_initiated",
    "people_lookup",
    "department_members",
    "organization_snapshot",
    "task_query",
    "task_search",
    "calendar_query",
    "chat_search",
    "message_query",
    "docs_read",
    "wiki_search",
    "drive_list",
    "base_query",
    "okr_query",
}

_INTERNAL_STRATEGIES = {
    "company_intro",
    "risk_analysis",
    "general_analysis",
    "decision_advice",
    "general_query",
}

_DEV_CLI_FALLBACK_STRATEGIES = _USER_TOKEN_STRATEGIES | _TENANT_TOKEN_STRATEGIES


def build_execution_identity_contract(
    *,
    strategy: str,
    source: str,
    operation: str,
    execution_identity: ExecutionIdentity,
    context: RuntimeContext,
    resource_scope: str,
) -> ExecutionIdentityContract:
    credential_mode = credential_mode_for_strategy(strategy=strategy, execution_identity=execution_identity)
    requires_authorization = credential_mode in {"USER_TOKEN", "ADMIN_SESSION"}
    allows_cli_fallback = strategy in _DEV_CLI_FALLBACK_STRATEGIES
    company_id = str(context.runtime_scope.active_company_id or "")
    owner = CredentialOwner(
        company_id=company_id,
        open_id=context.identity.open_id,
        user_id=context.identity.user_id,
    )
    return ExecutionIdentityContract(
        actor_identity=actor_identity_for_execution_identity(execution_identity),
        credential_mode=credential_mode,
        credential_owner=owner,
        resource_scope=resource_scope,
        requires_authorization=requires_authorization,
        allows_cli_fallback=allows_cli_fallback,
        authorization_status="UNKNOWN" if requires_authorization else "AUTHORIZED",
        fallback_used=False,
        reason=_identity_reason(strategy=strategy, source=source, operation=operation, credential_mode=credential_mode),
    )


def actor_identity_for_execution_identity(execution_identity: ExecutionIdentity) -> str:
    return "USER" if execution_identity == "user" else "BOT"


def credential_mode_for_strategy(*, strategy: str, execution_identity: ExecutionIdentity) -> CredentialMode:
    if strategy in _INTERNAL_STRATEGIES:
        return "INTERNAL"
    if strategy in _USER_TOKEN_STRATEGIES:
        return "USER_TOKEN"
    if strategy in _TENANT_TOKEN_STRATEGIES:
        return "TENANT_TOKEN"
    return "USER_TOKEN" if execution_identity == "user" else "TENANT_TOKEN"


def execution_identity_contract_payload(contract: ExecutionIdentityContract) -> dict[str, object]:
    return contract.payload()


def identity_contract_for_registry(
    *,
    strategy: str,
    execution_identity: str,
    data_scope: str,
) -> dict[str, object]:
    credential_mode = credential_mode_for_strategy(
        strategy=strategy,
        execution_identity="user" if execution_identity == "user" else "bot",
    )
    requires_authorization = credential_mode in {"USER_TOKEN", "ADMIN_SESSION"}
    return {
        "actor_identity": "USER" if execution_identity == "user" else "BOT",
        "credential_mode": credential_mode,
        "resource_scope": _resource_scope(data_scope),
        "requires_authorization": requires_authorization,
        "allows_cli_fallback": strategy in _DEV_CLI_FALLBACK_STRATEGIES,
        "authorization_status": "UNKNOWN" if requires_authorization else "AUTHORIZED",
        "fallback_used": False,
    }


def _resource_scope(data_scope: str) -> str:
    normalized = data_scope.strip().lower()
    if normalized == "self":
        return "SELF"
    if normalized in {"person", "user"}:
        return "USER"
    if normalized == "department":
        return "DEPARTMENT"
    if normalized in {"project", "team"}:
        return "TEAM"
    if normalized in {"company", "organization"}:
        return "COMPANY"
    return normalized.upper() if normalized else "SELF"


def _identity_reason(*, strategy: str, source: str, operation: str, credential_mode: CredentialMode) -> str:
    if credential_mode == "USER_TOKEN":
        return f"{strategy}:{source}.{operation} represents a user-owned resource or user action."
    if credential_mode == "TENANT_TOKEN":
        return f"{strategy}:{source}.{operation} is a company resource or bot-readable query."
    if credential_mode == "INTERNAL":
        return f"{strategy}:{source}.{operation} uses internal cognitive data."
    return f"{strategy}:{source}.{operation} has no explicit credential rule."
