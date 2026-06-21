from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import AuditLog
from app.services.agent.policies import BotActor
from app.services.audit import write_audit_log
from app.services.tools.base import ToolContext, ToolProvider, ToolRequest, ToolResult
from app.services.tools.config import (
    IncompatibleToolProviderError,
    UnknownToolConfigError,
    compatible_tool_providers,
    get_tool_config,
    list_tool_configurations,
    tool_config_payload,
    upsert_tool_config,
    validate_tool_provider,
)
from app.services.tools.router import TOOL_REGISTRY, execute_agent_tool
from app.services.tools.write_audit import write_target_summary


def list_tools_for_company(db: Session, *, company_id: UUID) -> dict[str, Any]:
    items = list_tool_configurations(db, company_id=company_id)
    return {"items": items, "counts": _count_by(items, "provider")}


def list_tool_execution_logs(
    db: Session,
    *,
    company_id: UUID,
    tool_name: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    bounded_limit = min(max(limit, 1), 200)
    query = (
        select(AuditLog)
        .where(AuditLog.company_id == company_id)
        .where(AuditLog.target_type == "tool")
        .order_by(AuditLog.created_at.desc())
    )
    if tool_name:
        query = query.where(AuditLog.target_id == tool_name)
    raw_items = list(db.scalars(query.limit(min(bounded_limit * 3, 500))).all())
    if status:
        raw_items = [item for item in raw_items if (item.payload or {}).get("status") == status]
    items = [tool_execution_payload(item) for item in raw_items[:bounded_limit]]
    return {"items": items, "counts": _count_by(items, "status")}


def execute_admin_tool(
    db: Session,
    *,
    company_id: UUID,
    tool_name: str,
    question: str | None,
    normalized_command: str | None,
    params: dict[str, Any],
    chat_id: str | None,
    actor_role: str,
    actor_access_scope: str,
    actor_domains: list[str],
    actor_display_name: str | None,
    actor_open_id: str | None,
    actor_email: str | None,
) -> dict[str, Any]:
    actor = BotActor(
        role=actor_role,
        access_scope=actor_access_scope,
        domains=tuple(actor_domains),
        display_name=actor_display_name,
        open_id=actor_open_id,
        email=actor_email,
    )
    result = execute_agent_tool(
        ToolContext(db=db, company_id=company_id, actor=actor, chat_id=chat_id),
        ToolRequest(
            tool_name=tool_name,
            question=question or tool_name,
            normalized_command=normalized_command or question or tool_name,
            params=dict(params or {}),
        ),
    )
    if callable(getattr(db, "commit", None)):
        db.commit()
    return tool_execution_result_payload(result)


def update_tool_configuration(
    db: Session,
    *,
    company_id: UUID,
    tool_name: str,
    enabled: bool | None,
    provider: ToolProvider | None,
    config_json: dict[str, Any] | None,
) -> dict[str, Any]:
    config = upsert_tool_config(
        db,
        company_id=company_id,
        tool_name=tool_name,
        enabled=enabled,
        provider=provider,
        config_json=config_json,
    )
    db.commit()
    return tool_config_payload(config)


def batch_update_tool_configurations(
    db: Session,
    *,
    company_id: UUID,
    all_tools: bool,
    tool_names: list[str],
    include_prefixes: list[str],
    include_providers: list[ToolProvider],
    supports_write: bool | None,
    enabled: bool | None,
    provider: ToolProvider | None,
    config_json: dict[str, Any] | None,
    dry_run: bool,
) -> dict[str, Any]:
    if enabled is None and provider is None and config_json is None:
        raise ValueError("Batch tool update requires enabled, provider, or config_json.")
    names = tool_batch_target_names(
        all_tools=all_tools,
        tool_names=tool_names,
        include_prefixes=include_prefixes,
        include_providers=include_providers,
        supports_write=supports_write,
    )
    if not names:
        raise ValueError("Batch tool update matched no tools.")

    items = []
    updated_count = 0
    failed_count = 0
    for name in names:
        if name not in TOOL_REGISTRY:
            items.append(
                {
                    "tool_name": name,
                    "status": "failed",
                    "error": f"Unknown tool: {name}",
                    "compatible_providers": [],
                    "config_json_changed": config_json is not None,
                }
            )
            failed_count += 1
            continue
        definition = TOOL_REGISTRY[name]
        current = get_tool_config(db, company_id=company_id, tool_name=name)
        current_provider = current.provider if current is not None else definition.provider.value
        current_enabled = bool(current.enabled) if current is not None else definition.enabled
        next_provider = provider.value if provider is not None else current_provider
        next_enabled = current_enabled if enabled is None else enabled
        item = {
            "tool_name": name,
            "status": "dry_run" if dry_run else "updated",
            "current_provider": current_provider,
            "provider": next_provider,
            "current_enabled": current_enabled,
            "enabled": next_enabled,
            "supports_write": definition.supports_write,
            "compatible_providers": compatible_tool_providers(definition),
            "config_json_changed": config_json is not None,
        }
        try:
            if provider is not None:
                validate_tool_provider(tool_name=name, provider=provider, definition=definition)
            if not dry_run:
                config = upsert_tool_config(
                    db,
                    company_id=company_id,
                    tool_name=name,
                    enabled=enabled,
                    provider=provider,
                    config_json=config_json,
                )
                item.update(tool_config_payload(config))
                updated_count += 1
        except (UnknownToolConfigError, IncompatibleToolProviderError, ValueError) as exc:
            item["status"] = "failed"
            item["error"] = str(exc)
            failed_count += 1
        items.append(item)

    if not dry_run:
        write_audit_log(
            db,
            action="tool.config.batch_update",
            company_id=company_id,
            actor="admin_console",
            target_type="tool_config",
            target_id="batch",
            payload={
                "status": "partial_failed" if failed_count else "success",
                "matched_count": len(names),
                "updated_count": updated_count,
                "failed_count": failed_count,
                "selection": tool_batch_selection_payload(
                    all_tools=all_tools,
                    tool_names=tool_names,
                    include_prefixes=include_prefixes,
                    include_providers=include_providers,
                    supports_write=supports_write,
                ),
                "updates": {
                    "enabled": enabled,
                    "provider": provider.value if provider else None,
                    "config_json_keys": sorted((config_json or {}).keys()),
                },
            },
        )
        db.commit()
    return {
        "dry_run": dry_run,
        "matched_count": len(names),
        "updated_count": updated_count,
        "failed_count": failed_count,
        "items": items,
    }


def tool_execution_payload(item: AuditLog) -> dict[str, Any]:
    payload = item.payload or {}
    return {
        "id": str(item.id),
        "company_id": str(item.company_id) if item.company_id else None,
        "tool_name": item.target_id,
        "actor": item.actor,
        "action": item.action,
        "provider": payload.get("provider"),
        "business_tool": payload.get("business_tool"),
        "status": payload.get("status"),
        "error": payload.get("error"),
        "duration_ms": payload.get("duration_ms"),
        "required_permissions": payload.get("required_permissions") or [],
        "supports_write": bool(payload.get("supports_write")),
        "data_source": payload.get("data_source"),
        "execution_source": payload.get("execution_source"),
        "source_chain": payload.get("source_chain") or [],
        "source_kind": payload.get("source_kind"),
        "tool_decides_data_or_execution_source": payload.get("tool_decides_data_or_execution_source"),
        "tool_returns_structured_result": payload.get("tool_returns_structured_result"),
        "final_answer_owner": payload.get("final_answer_owner"),
        "data_permission_model": payload.get("data_permission_model"),
        "data_boundary_policy": payload.get("data_boundary_policy"),
        "company_scope": payload.get("company_scope"),
        "role_scope": payload.get("role_scope"),
        "enterprise_identity": payload.get("enterprise_identity"),
        "enterprise_identity_boundary": payload.get("enterprise_identity_boundary"),
        "enterprise_identity_constraints": payload.get("enterprise_identity_constraints") or [],
        "enterprise_company_scope": payload.get("enterprise_company_scope"),
        "enterprise_role_scope": payload.get("enterprise_role_scope"),
        "can_exceed_feishu_app_permissions": payload.get("can_exceed_feishu_app_permissions"),
        "user_identity": payload.get("user_identity"),
        "user_identity_boundary": payload.get("user_identity_boundary"),
        "user_identity_owner_open_id": payload.get("user_identity_owner_open_id"),
        "can_exceed_user_original_authorization": payload.get("can_exceed_user_original_authorization"),
        "data_boundary_enforcement": payload.get("data_boundary_enforcement"),
        "digital_advisor_permission_policy": payload.get("digital_advisor_permission_policy"),
        "cannot_escalate_original_permissions": payload.get("cannot_escalate_original_permissions"),
        "execution_chain": payload.get("execution_chain") or [],
        "mcp_provider": payload.get("mcp_provider"),
        "preferred_execution_engine": payload.get("preferred_execution_engine"),
        "realtime_policy": payload.get("realtime_policy"),
        "api_role": payload.get("api_role"),
        "agent_runtime_direct_access": payload.get("agent_runtime_direct_access"),
        "sync_engine_mcp_access_allowed": payload.get("sync_engine_mcp_access_allowed"),
        "cli_profile": payload.get("cli_profile"),
        "cli_profile_source": payload.get("cli_profile_source"),
        "write_mode": payload.get("write_mode"),
        "dry_run": payload.get("dry_run"),
        "confirmed": payload.get("confirmed"),
        "has_confirmation_token": payload.get("has_confirmation_token"),
        "write_target": payload.get("write_target"),
        "write_target_summary": write_target_summary(payload.get("write_target")),
        "chat_id": payload.get("chat_id"),
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def tool_execution_result_payload(result: ToolResult) -> dict[str, Any]:
    return {
        "tool_name": result.tool_name,
        "provider": result.provider.value,
        "status": result.status.value,
        "answer": result.answer,
        "error": result.error,
        "metadata": {
            **result.metadata,
            "write_target_summary": write_target_summary(result.metadata.get("write_target")),
        },
    }


def tool_batch_target_names(
    *,
    all_tools: bool,
    tool_names: list[str],
    include_prefixes: list[str],
    include_providers: list[ToolProvider],
    supports_write: bool | None,
) -> list[str]:
    if not all_tools and not tool_names and not include_prefixes and not include_providers:
        return []
    explicit_names = {name.strip() for name in tool_names if name.strip()}
    provider_values = {provider.value for provider in include_providers}
    prefixes = tuple(prefix.strip() for prefix in include_prefixes if prefix.strip())
    names = []
    for name, definition in TOOL_REGISTRY.items():
        matched = all_tools
        if explicit_names and name in explicit_names:
            matched = True
        if prefixes and name.startswith(prefixes):
            matched = True
        if provider_values and definition.provider.value in provider_values:
            matched = True
        if not matched:
            continue
        if supports_write is not None and definition.supports_write is not supports_write:
            continue
        names.append(name)
    missing_names = sorted(explicit_names - set(TOOL_REGISTRY))
    return sorted(names + missing_names)


def tool_batch_selection_payload(
    *,
    all_tools: bool,
    tool_names: list[str],
    include_prefixes: list[str],
    include_providers: list[ToolProvider],
    supports_write: bool | None,
) -> dict[str, Any]:
    return {
        "all_tools": all_tools,
        "tool_names": tool_names,
        "include_prefixes": include_prefixes,
        "include_providers": [provider.value for provider in include_providers],
        "supports_write": supports_write,
    }


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts
