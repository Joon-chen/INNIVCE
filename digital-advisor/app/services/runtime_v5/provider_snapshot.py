from __future__ import annotations

from typing import Any

from app.services.runtime_v5.capabilities import RUNTIME_CAPABILITIES


HEALTHY_PROVIDER_STATES = {"healthy", "ok", "ready"}


def build_runtime_provider_snapshot(providers: dict[str, Any] | None) -> dict[str, Any]:
    provider_map = providers or {}
    provider_items: dict[str, Any] = {}
    for source, provider in sorted(provider_map.items()):
        enabled = bool(getattr(provider, "enabled", True))
        health = str(getattr(provider, "health_status", "healthy") or "healthy")
        operations = getattr(provider, "_OPERATIONS", None)
        operation_items: dict[str, Any] = {}
        if isinstance(operations, dict):
            for operation, spec in sorted(operations.items()):
                tool_name = spec[0] if isinstance(spec, tuple) and spec else spec
                operation_items[str(operation)] = {
                    "operation": str(operation),
                    "enabled": bool(tool_name),
                    "tool_name": str(tool_name or ""),
                }
        provider_items[str(source)] = {
            "source": str(source),
            "exists": True,
            "enabled": enabled,
            "health": health,
            "healthy": health in HEALTHY_PROVIDER_STATES,
            "operation_count": len(operation_items),
            "enabled_operation_count": len([item for item in operation_items.values() if item.get("enabled")]),
            "operations": operation_items,
        }

    declared_operations = _declared_runtime_operations()
    coverage_items = []
    for key, declared in sorted(declared_operations.items()):
        source = declared["source"]
        operation = declared["operation"]
        provider = provider_items.get(source)
        operation_payload = (
            provider.get("operations", {}).get(operation)
            if isinstance(provider, dict) and isinstance(provider.get("operations"), dict)
            else None
        )
        status = "ready"
        if not provider:
            status = "provider_missing"
        elif not provider.get("enabled"):
            status = "provider_disabled"
        elif not provider.get("healthy"):
            status = "provider_unhealthy"
        elif not operation_payload:
            status = "operation_not_covered"
        elif not operation_payload.get("enabled"):
            status = "operation_disabled"
        coverage_items.append(
            {
                **declared,
                "status": status,
                "ready": status == "ready",
            }
        )

    missing_count = len([item for item in coverage_items if item["status"] == "provider_missing"])
    disabled_count = len([item for item in coverage_items if item["status"] in {"provider_disabled", "operation_disabled"}])
    unhealthy_count = len([item for item in coverage_items if item["status"] == "provider_unhealthy"])
    uncovered_count = len([item for item in coverage_items if item["status"] == "operation_not_covered"])
    ready_count = len([item for item in coverage_items if item["ready"]])
    issue_count = len(coverage_items) - ready_count
    status = "healthy" if issue_count == 0 else "needs_attention"
    return {
        "status": status,
        "label": "Runtime Provider Snapshot 可执行" if status == "healthy" else "Runtime Provider Snapshot 需要补齐",
        "provider_count": len(provider_items),
        "declared_operation_count": len(coverage_items),
        "ready_operation_count": ready_count,
        "issue_count": issue_count,
        "provider_missing_count": missing_count,
        "disabled_count": disabled_count,
        "unhealthy_count": unhealthy_count,
        "operation_uncovered_count": uncovered_count,
        "providers": provider_items,
        "coverage": coverage_items,
        "top_issue": next((item for item in coverage_items if not item["ready"]), {}),
    }


def provider_snapshot_operation(
    snapshot: dict[str, Any],
    *,
    source: str,
    operation: str,
) -> dict[str, Any]:
    providers = snapshot.get("providers") if isinstance(snapshot.get("providers"), dict) else {}
    provider = providers.get(source) if isinstance(providers.get(source), dict) else {}
    operations = provider.get("operations") if isinstance(provider.get("operations"), dict) else {}
    operation_payload = operations.get(operation) if isinstance(operations.get(operation), dict) else {}
    if not provider:
        return {"status": "provider_missing", "ready": False, "source": source, "operation": operation}
    if not provider.get("enabled"):
        return {"status": "provider_disabled", "ready": False, "source": source, "operation": operation}
    if not provider.get("healthy"):
        return {
            "status": "provider_unhealthy",
            "ready": False,
            "source": source,
            "operation": operation,
            "health": provider.get("health", ""),
        }
    if not operation_payload:
        return {
            "status": "operation_not_covered",
            "ready": False,
            "source": source,
            "operation": operation,
            "available_operations": sorted(operations),
        }
    if not operation_payload.get("enabled"):
        return {"status": "operation_disabled", "ready": False, "source": source, "operation": operation}
    return {
        "status": "ready",
        "ready": True,
        "source": source,
        "operation": operation,
        "tool_name": operation_payload.get("tool_name", ""),
    }


def _declared_runtime_operations() -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for item in RUNTIME_CAPABILITIES:
        if not item.installed:
            continue
        key = f"{item.source}:{item.operation}"
        rows[key] = {
            "source": item.source,
            "operation": item.operation,
            "label": item.label,
            "strategy": item.strategy,
            "installed": item.installed,
        }
    return rows
