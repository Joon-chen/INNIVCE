from dataclasses import replace
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import ToolConfig
from app.services.tools.base import (
    ToolDefinition,
    ToolProvider,
)
from app.services.tools.providers.feishu_api import FEISHU_API_CAPABILITIES, FEISHU_API_PROVIDER_CONTRACT
from app.services.tools.providers.feishu_mcp import FEISHU_MCP_CONTRACT, feishu_mcp_bound_tool_names
from app.services.tools.providers.lark_cli import LARK_CLI_EXECUTION_CONTRACT
from app.services.tools.router import (
    FEISHU_REALTIME_EXECUTION_CHAIN,
    FEISHU_REALTIME_RESPONSIBILITY_BOUNDARY,
    TOOL_REGISTRY,
)


LOCAL_CACHE_FALLBACK_TOOLS = {"bitable_qa", "calendar_qa", "mail_qa", "task_qa"}

SYSTEM_TOOL_NAMES = {"feishu_cli_status", "feishu_cli_doctor"}


class ToolConfigError(ValueError):
    pass


class UnknownToolConfigError(ToolConfigError):
    pass


class IncompatibleToolProviderError(ToolConfigError):
    pass


def effective_tool_definition(
    db: Session | None,
    *,
    company_id: UUID,
    definition: ToolDefinition,
) -> ToolDefinition:
    config = get_tool_config(db, company_id=company_id, tool_name=definition.name)
    if config is None:
        return definition
    provider_value = ToolProvider(config.provider)
    validate_tool_provider(tool_name=definition.name, provider=provider_value, definition=definition)
    return replace(
        definition,
        provider=provider_value,
        required_permissions=tuple(config.required_permissions or definition.required_permissions),
        audit_action=config.audit_action or definition.audit_action,
        supports_write=bool(config.supports_write),
        enabled=bool(config.enabled),
    )


def get_tool_config(db: Session | None, *, company_id: UUID, tool_name: str) -> ToolConfig | None:
    if db is None or not callable(getattr(db, "scalar", None)):
        return None
    return db.scalar(select(ToolConfig).where(ToolConfig.company_id == company_id).where(ToolConfig.tool_name == tool_name))


def list_tool_configurations(db: Session | None, *, company_id: UUID) -> list[dict[str, Any]]:
    items = []
    for definition in TOOL_REGISTRY.values():
        config = get_tool_config(db, company_id=company_id, tool_name=definition.name)
        if config is None:
            items.append(tool_configuration_payload(definition))
            continue
        effective_definition = replace(
            definition,
            provider=ToolProvider(config.provider),
            required_permissions=tuple(config.required_permissions or definition.required_permissions),
            audit_action=config.audit_action or definition.audit_action,
            supports_write=bool(config.supports_write),
            enabled=bool(config.enabled),
        )
        items.append(tool_configuration_payload(effective_definition, config_json=config.config_json))
    return items


def upsert_tool_config(
    db: Session,
    *,
    company_id: UUID,
    tool_name: str,
    enabled: bool | None = None,
    provider: ToolProvider | str | None = None,
    config_json: dict[str, Any] | None = None,
) -> ToolConfig:
    definition = TOOL_REGISTRY.get(tool_name)
    if definition is None:
        raise UnknownToolConfigError(f"Unknown tool: {tool_name}")
    config = get_tool_config(db, company_id=company_id, tool_name=tool_name)
    if config is None:
        provider_value = ToolProvider(provider or definition.provider)
        validate_tool_provider(tool_name=tool_name, provider=provider_value, definition=definition)
        config = ToolConfig(
            company_id=company_id,
            tool_name=tool_name,
            provider=provider_value.value,
            enabled=definition.enabled if enabled is None else enabled,
            required_permissions=list(definition.required_permissions),
            supports_write=definition.supports_write,
            audit_action=definition.audit_action,
            config_json=config_json or {},
        )
        db.add(config)
        return config
    provider_value = ToolProvider(provider or config.provider or definition.provider)
    validate_tool_provider(tool_name=tool_name, provider=provider_value, definition=definition)
    config.provider = provider_value.value
    if enabled is not None:
        config.enabled = enabled
    if config_json is not None:
        config.config_json = config_json
    config.required_permissions = list(definition.required_permissions)
    config.supports_write = definition.supports_write
    config.audit_action = definition.audit_action
    return config


def tool_config_payload(config: ToolConfig) -> dict[str, Any]:
    definition = TOOL_REGISTRY.get(config.tool_name)
    if definition is None:
        raise UnknownToolConfigError(f"Unknown tool: {config.tool_name}")
    effective_definition = replace(
        definition,
        provider=ToolProvider(config.provider),
        required_permissions=tuple(config.required_permissions or definition.required_permissions),
        audit_action=config.audit_action or definition.audit_action,
        supports_write=bool(config.supports_write),
        enabled=bool(config.enabled),
    )
    return tool_configuration_payload(effective_definition, config_json=config.config_json)


def compatible_tool_providers(definition: ToolDefinition) -> list[str]:
    base_definition = TOOL_REGISTRY.get(definition.name, definition)
    providers = {base_definition.provider, definition.provider}
    if base_definition.name in feishu_mcp_bound_tool_names():
        providers.add(ToolProvider.FEISHU_MCP)
    if base_definition.provider == ToolProvider.LOCAL or base_definition.name in LOCAL_CACHE_FALLBACK_TOOLS:
        providers.add(ToolProvider.LOCAL)
    if base_definition.provider == ToolProvider.REPORT:
        providers.add(ToolProvider.REPORT)
    return [provider.value for provider in ToolProvider if provider in providers]


def validate_tool_provider(*, tool_name: str, provider: ToolProvider, definition: ToolDefinition) -> None:
    if provider == definition.provider:
        return
    if provider == ToolProvider.FEISHU_MCP and tool_name in feishu_mcp_bound_tool_names():
        return
    if provider == ToolProvider.LOCAL and tool_name in LOCAL_CACHE_FALLBACK_TOOLS:
        return
    if provider == ToolProvider.FEISHU_MCP and FEISHU_MCP_CONTRACT.requires_explicit_tool_binding:
        raise IncompatibleToolProviderError(
            f"Feishu MCP provider requires explicit tool binding before configuration: {tool_name}"
        )
    raise IncompatibleToolProviderError(f"Tool provider is not compatible with tool: {tool_name} -> {provider.value}")


def tool_configuration_payload(definition: ToolDefinition, *, config_json: dict[str, Any] | None = None) -> dict[str, Any]:
    provider_boundaries = provider_boundaries_payload(definition)
    return {
        "tool_name": definition.name,
        "provider": definition.provider.value,
        "required_permissions": list(definition.required_permissions),
        "audit_action": definition.audit_action,
        "supports_write": definition.supports_write,
        "enabled": definition.enabled,
        "compatible_providers": compatible_tool_providers(definition),
        "provider_boundaries": provider_boundaries,
        "config_json": config_json or {},
        "family": definition.family,
        "capabilities": definition.capabilities,
    }

def provider_boundaries_payload(definition: ToolDefinition) -> dict[str, Any]:
    base_definition = TOOL_REGISTRY.get(definition.name, definition)
    feishu_api_capability = FEISHU_API_CAPABILITIES.get(base_definition.name)
    feishu_mcp_bound = base_definition.name in feishu_mcp_bound_tool_names()
    registered_api_write_capability = bool(feishu_api_capability and feishu_api_capability.supports_write)
    sync_engine_direct_api_allowed = bool(feishu_api_capability and not registered_api_write_capability)
    return {
        "feishu_api": {
            "available": feishu_api_capability is not None,
            "role": FEISHU_API_PROVIDER_CONTRACT.role if feishu_api_capability else None,
            "allowed_entrypoints": (
                list(FEISHU_API_PROVIDER_CONTRACT.allowed_entrypoints) if feishu_api_capability else []
            ),
            "registered_api_write_capability": registered_api_write_capability,
            "preferred_execution_engine": (
                feishu_api_capability.preferred_execution_engine if feishu_api_capability else None
            ),
            "realtime_policy": "not_realtime" if feishu_api_capability else None,
            "realtime_bridge": None,
            "api_role": "sync_engine_only" if feishu_api_capability else None,
            "responsibility_boundary": (
                dict(FEISHU_REALTIME_RESPONSIBILITY_BOUNDARY) if feishu_api_capability else {}
            ),
            "agent_runtime_direct_access": False,
            "sync_engine_direct_api_allowed": sync_engine_direct_api_allowed,
            "sync_engine_mcp_access_allowed": False,
            "mcp_access_allowed": FEISHU_API_PROVIDER_CONTRACT.mcp_access_allowed,
            "realtime_read_allowed": FEISHU_API_PROVIDER_CONTRACT.realtime_read_allowed,
            "realtime_write_allowed": FEISHU_API_PROVIDER_CONTRACT.realtime_write_allowed,
            "controlled_validation_allowed": FEISHU_API_PROVIDER_CONTRACT.controlled_validation_allowed,
            "requires_dry_run_for_write": registered_api_write_capability,
            "supports_write": False,
            "supports_confirmed_realtime_write": False,
            "confirmed_write_policy": (
                "blocked_realtime_use_tool_router_mcp_cli" if registered_api_write_capability else None
            ),
        },
        "feishu_mcp": {
            "available": feishu_mcp_bound,
            "role": FEISHU_MCP_CONTRACT.role if feishu_api_capability or feishu_mcp_bound else None,
            "realtime_bridge_available": feishu_api_capability is not None or feishu_mcp_bound,
            "realtime_execution_role": (
                "agent_realtime_operations" if feishu_api_capability or feishu_mcp_bound else None
            ),
            "mcp_provider": "feishu_mcp" if feishu_api_capability or feishu_mcp_bound else None,
            "responsibility_boundary": (
                dict(FEISHU_REALTIME_RESPONSIBILITY_BOUNDARY)
                if feishu_api_capability or feishu_mcp_bound
                else {}
            ),
            "execution_chain": (
                list(FEISHU_REALTIME_EXECUTION_CHAIN) if feishu_api_capability or feishu_mcp_bound else []
            ),
            "cli_behind_mcp": feishu_api_capability is not None or feishu_mcp_bound,
            "agent_runtime_direct_access": False,
            "sync_engine_access_allowed": False,
            "binding_status": "bound" if feishu_mcp_bound else "unbound",
            "blocked_reason": None if feishu_mcp_bound else "explicit_mcp_tool_binding_required",
            "requires_explicit_tool_binding": FEISHU_MCP_CONTRACT.requires_explicit_tool_binding,
            "default_write_enabled": FEISHU_MCP_CONTRACT.default_write_enabled,
            "allowed_entrypoints": list(FEISHU_MCP_CONTRACT.allowed_entrypoints),
            "action_executor": FEISHU_MCP_CONTRACT.action_executor if feishu_api_capability or feishu_mcp_bound else None,
            "performs_action_execution": FEISHU_MCP_CONTRACT.performs_action_execution,
            "cli_contract": (
                {
                    "engine_name": LARK_CLI_EXECUTION_CONTRACT.engine_name,
                    "role": LARK_CLI_EXECUTION_CONTRACT.role,
                    "allowed_caller": LARK_CLI_EXECUTION_CONTRACT.allowed_caller,
                    "allowed_caller_module": LARK_CLI_EXECUTION_CONTRACT.allowed_caller_module,
                    "accepts_business_capability": LARK_CLI_EXECUTION_CONTRACT.accepts_business_capability,
                    "schedules_tools": LARK_CLI_EXECUTION_CONTRACT.schedules_tools,
                    "performs_action_execution": LARK_CLI_EXECUTION_CONTRACT.performs_action_execution,
                }
                if feishu_api_capability or feishu_mcp_bound
                else {}
            ),
        },
    }
