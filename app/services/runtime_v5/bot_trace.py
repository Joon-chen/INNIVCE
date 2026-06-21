from __future__ import annotations

from typing import Any

from app.services.runtime_v5.payload import runtime_v5_payload


def runtime_contract_payload() -> dict[str, Any]:
    return {
        "pipeline": [
            "pre_gateway",
            "result_followup_detector",
            "intent_recognition",
            "task_planner",
            "permission_check",
            "capability_router",
            "execution",
            "answer_composer",
            "smart_reply",
        ],
        "tool_centered": False,
        "skill_centered": False,
        "legacy_fallback": False,
    }


def runtime_v5_bot_trace_payload(
    *,
    envelope,
    route_path: str,
    route_label: str,
    requires_confirmation: bool,
    execution_status: str,
    runtime_summary: dict[str, Any],
    capability_summary: dict[str, Any],
    provider_registry: dict[str, Any],
    runtime_provider_snapshot: dict[str, Any],
) -> dict[str, Any]:
    return {
        "runtime_version": "v5",
        "route_path": route_path,
        "route_label": route_label,
        "strategy": envelope.plan.strategy,
        "sources": list(envelope.plan.sources),
        "question_type": envelope.intent.question_type,
        "data_scope": envelope.intent.data_scope,
        "requires_confirmation": requires_confirmation,
        "execution_identity": envelope.permission.execution_identity,
        "execution_status": execution_status,
        "result_type": envelope.composed.result_context.result_type if envelope.composed.result_context else "",
        "runtime_contract": runtime_contract_payload(),
        "runtime_trace_summary": runtime_summary,
        "capability_summary": capability_summary,
        "provider_registry": provider_registry,
        "runtime_provider_snapshot": runtime_provider_snapshot,
        "source_execution_contract": runtime_summary.get("source_execution_contract") if isinstance(runtime_summary, dict) else {},
        **runtime_v5_payload(envelope),
    }


def runtime_v5_disabled_trace_payload() -> dict[str, Any]:
    return {
        "runtime_version": "v5",
        "runtime_mode": "v5_disabled",
        "runtime_path_kind": "legacy_fallback_blocked",
        "bypass_reason": "旧 Agent Runtime 已按 V5 架构原则切断，避免静默回退污染运行结果。",
        "route_path": "runtime_disabled",
        "route_label": "V5 未启用",
        "strategy": "runtime_disabled",
        "sources": [],
        "question_type": "",
        "data_scope": "",
        "requires_confirmation": False,
        "execution_identity": "",
        "execution_status": "blocked",
        "result_type": "",
        "runtime_contract": runtime_contract_payload(),
    }
