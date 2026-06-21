from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from app.services.runtime_v5.capabilities import RUNTIME_CAPABILITIES, SKILL_ATOMIC_CAPABILITIES, path_maturity_for_strategy
from app.services.runtime_v5.planner import strategy_registry


DIAGNOSTICS_VERSION = "v5.diagnostics.1"


def runtime_trace_summary(envelope: Any) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat()
    execution = getattr(envelope, "execution", None)
    composed = getattr(envelope, "composed", None)
    provider_results = list(getattr(execution, "provider_results", ()) or ()) if execution is not None else []
    current_result_context = getattr(composed, "result_context", None) or getattr(getattr(envelope, "context", None), "result_context", None)
    context_health = _context_health(envelope)
    profile_health = _profile_health(envelope)
    followup_health = _followup_health(envelope, current_result_context)
    decision_health = _decision_health(envelope)
    answer_health = _answer_health(envelope, current_result_context)
    permission_summary = _permission_summary(getattr(envelope, "permission", None))
    permission_health = _permission_health(envelope, permission_summary)
    provider_summary = _provider_results_summary(provider_results)
    source_execution_contract = _source_execution_contract(envelope, provider_summary)
    planner_runtime_snapshot_contract = _planner_runtime_snapshot_contract(envelope)
    router_health = _router_health(envelope, provider_summary)
    execution_health = _execution_health(envelope, provider_summary, current_result_context)
    action_trace = _session_action_trace(getattr(envelope, "context", None))
    result_context_events = _session_result_context_events(getattr(envelope, "context", None))
    result_context_event_summary = _result_context_event_summary(result_context_events)
    action_timeline = _action_timeline_summary(action_trace, result_context_events)
    action_closure = _action_closure_summary(action_trace, result_context_events, action_timeline)
    result_context_quality = _result_context_quality(current_result_context)
    followup_consume_contract = _followup_consume_contract(followup_health, current_result_context)
    pipeline_frames = _pipeline_frames(envelope)
    pipeline_health = _pipeline_health(pipeline_frames)
    pipeline_constitution_contract = _pipeline_constitution_contract(
        envelope=envelope,
        pipeline_frames=pipeline_frames,
        pipeline_health=pipeline_health,
        source_execution_contract=source_execution_contract,
        followup_consume_contract=followup_consume_contract,
        permission_summary=permission_summary,
    )
    timing_health = _timing_health(envelope, provider_summary)
    fast_response_contract = _fast_response_contract(envelope, provider_summary, action_closure, timing_health)
    path_maturity = _current_path_maturity(envelope)
    constitution_guard = _constitution_guard(envelope, current_result_context, permission_summary, provider_summary, path_maturity)
    health_matrix = _health_matrix(
        pipeline_health=pipeline_health,
        timing_health=timing_health,
        context_health=context_health,
        profile_health=profile_health,
        followup_health=followup_health,
        decision_health=decision_health,
        router_health=router_health,
        execution_health=execution_health,
        permission_health=permission_health,
        answer_health=answer_health,
        result_context_quality=result_context_quality,
        action_closure=action_closure,
        provider_summary=provider_summary,
        constitution_guard=constitution_guard,
        source_execution_contract=source_execution_contract,
    )
    runtime_gate = _runtime_gate(
        envelope,
        current_result_context,
        context_health,
        profile_health,
        followup_health,
        decision_health,
        router_health,
        execution_health,
        answer_health,
        permission_summary,
        provider_summary,
        permission_health,
        constitution_guard,
        action_closure,
        result_context_quality,
        pipeline_health,
        timing_health,
        path_maturity,
        source_execution_contract,
    )
    repair_plan = _runtime_repair_plan(
        runtime_gate=runtime_gate,
        action_closure=action_closure,
        result_context_quality=result_context_quality,
        pipeline_health=pipeline_health,
        provider_summary=provider_summary,
    )
    runtime_health_grade = _runtime_health_grade(
        runtime_gate=runtime_gate,
        health_matrix=health_matrix,
        pipeline_health=pipeline_health,
        provider_summary=provider_summary,
        action_closure=action_closure,
        result_context_quality=result_context_quality,
        source_execution_contract=source_execution_contract,
        fast_response_contract=fast_response_contract,
        timing_health=timing_health,
    )
    snapshot_summary = _snapshot_summary(
        envelope=envelope,
        runtime_gate=runtime_gate,
        repair_plan=repair_plan,
        health_matrix=health_matrix,
        timing_health=timing_health,
        provider_summary=provider_summary,
        action_closure=action_closure,
        result_context=current_result_context,
        generated_at=generated_at,
    )
    snapshot_lifecycle = _snapshot_lifecycle(
        generated_at=generated_at,
        envelope=envelope,
        runtime_gate=runtime_gate,
        repair_plan=repair_plan,
        health_matrix=health_matrix,
    )
    evidence_summary = _evidence_summary(
        pipeline_frames=pipeline_frames,
        provider_summary=provider_summary,
        action_trace=action_trace,
        result_context_events=result_context_events,
        result_context=current_result_context,
        permission=permission_summary,
    )
    correlation = _correlation_summary(
        generated_at=generated_at,
        envelope=envelope,
        result_context=current_result_context,
        action_trace=action_trace,
        provider_summary=provider_summary,
    )
    debug_locator = _debug_locator(
        correlation=correlation,
        snapshot_summary=snapshot_summary,
        runtime_gate=runtime_gate,
        repair_plan=repair_plan,
        timing_health=timing_health,
        provider_summary=provider_summary,
        action_closure=action_closure,
        result_context=current_result_context,
        result_context_quality=result_context_quality,
    )
    return {
        "diagnostics_version": DIAGNOSTICS_VERSION,
        "diagnostics_capabilities": _diagnostics_capabilities(),
        "generated_at": generated_at,
        "runtime_mode": "full_runtime",
        "runtime_path_kind": "v5_pipeline",
        "bypass_reason": "",
        "trace_id": correlation.get("trace_id", ""),
        "correlation": correlation,
        "debug_locator": debug_locator,
        "current_path_maturity": path_maturity,
        "snapshot_summary": snapshot_summary,
        "snapshot_lifecycle": snapshot_lifecycle,
        "evidence_summary": evidence_summary,
        "health": _runtime_health(envelope),
        "runtime_health_grade": runtime_health_grade,
        "runtime_gate": runtime_gate,
        "repair_plan": repair_plan,
        "health_matrix": health_matrix,
        "constitution_guard": constitution_guard,
        "intent": getattr(getattr(envelope, "intent", None), "intent", ""),
        "question_type": getattr(getattr(envelope, "intent", None), "question_type", ""),
        "data_scope": getattr(getattr(envelope, "intent", None), "data_scope", ""),
        "context_health": context_health,
        "profile_health": profile_health,
        "followup_health": followup_health,
        "followup_consume_contract": followup_consume_contract,
        "decision_health": decision_health,
        "router_health": router_health,
        "execution_health": execution_health,
        "answer_health": answer_health,
        "uses_previous_result": bool(
            getattr(getattr(envelope, "intent", None), "entities", {}).get("use_previous_result")
        ),
        "result_context": _result_context_summary(current_result_context),
        "result_context_event_summary": result_context_event_summary,
        "result_context_quality": result_context_quality,
        "strategy": getattr(getattr(envelope, "plan", None), "strategy", ""),
        "sources": list(getattr(getattr(envelope, "plan", None), "sources", ()) or ()),
        "permission_allowed": bool(getattr(getattr(envelope, "permission", None), "allowed", False)),
        "requires_confirmation": bool(getattr(getattr(envelope, "permission", None), "requires_confirmation", False)),
        "execution_identity": getattr(getattr(envelope, "permission", None), "execution_identity", ""),
        "permission": permission_summary,
        "permission_health": permission_health,
        "decision_summary": _decision_summary(envelope, permission_summary, provider_summary),
        "execution_status": getattr(execution, "status", "not_executed") if execution is not None else "not_executed",
        "result_type": getattr(getattr(composed, "result_context", None), "result_type", ""),
        "answer_metadata": getattr(composed, "metadata", {}) if isinstance(getattr(composed, "metadata", {}), dict) else {},
        "command_plan": _composed_metadata_value(composed, "command_plan", {}),
        "runtime_task": _composed_metadata_value(composed, "runtime_task", {}),
        "runtime_result": _composed_metadata_value(composed, "runtime_result", {}),
        "pipeline_timing": _composed_metadata_value(composed, "pipeline_timing", {}),
        "action_trace": action_trace,
        "action_timeline": action_timeline,
        "action_closure": action_closure,
        "provider_results": provider_summary["items"],
        "provider_summary": provider_summary,
        "source_execution_contract": source_execution_contract,
        "planner_runtime_snapshot_contract": planner_runtime_snapshot_contract,
        "pipeline_frames": pipeline_frames,
        "pipeline_health": pipeline_health,
        "pipeline_constitution_contract": pipeline_constitution_contract,
        "timing_health": timing_health,
        "fast_response_contract": fast_response_contract,
    }


def _diagnostics_capabilities() -> dict[str, Any]:
    modules = (
        "snapshot_summary",
        "runtime_state",
        "health_matrix",
        "runtime_gate",
        "repair_plan",
        "issue_fingerprint",
        "debug_locator",
        "runtime_mode",
        "current_path_maturity",
        "pipeline_health",
        "timing_health",
        "fast_response_contract",
        "runtime_health_grade",
        "provider_evidence",
        "provider_quality",
        "capability_contract",
        "source_execution_contract",
        "source_order_contract",
        "provider_registry_readiness",
        "result_context_quality",
        "result_context_consume_policy",
        "followup_consume_contract",
        "action_timeline",
        "profile_isolation",
        "permission_identity_policy",
        "planner_capability_contract",
    )
    return {
        "version": DIAGNOSTICS_VERSION,
        "module_count": len(modules),
        "modules": list(modules),
    }


def _current_path_maturity(envelope: Any) -> dict[str, Any]:
    plan = getattr(envelope, "plan", None)
    strategy = str(getattr(plan, "strategy", "") or "")
    sources = tuple(str(source) for source in (getattr(plan, "sources", ()) or ()) if str(source))
    if not strategy:
        return {
            "strategy": "",
            "sources": list(sources),
            "available": False,
            "status": "needs_attention",
            "label": "当前策略为空，无法判断路径成熟度",
            "counts": {},
            "items": [],
            "migration_items": [],
            "migration_count": 0,
        }
    return path_maturity_for_strategy(strategy, sources)


def _snapshot_summary(
    *,
    envelope: Any,
    runtime_gate: dict[str, Any],
    repair_plan: dict[str, Any],
    health_matrix: dict[str, Any],
    timing_health: dict[str, Any],
    provider_summary: dict[str, Any],
    action_closure: dict[str, Any],
    result_context: Any,
    generated_at: str,
) -> dict[str, Any]:
    plan = getattr(envelope, "plan", None)
    intent = getattr(envelope, "intent", None)
    execution = getattr(envelope, "execution", None)
    composed = getattr(envelope, "composed", None)
    result_metadata = getattr(result_context, "metadata", None) if result_context is not None else {}
    result_metadata = result_metadata if isinstance(result_metadata, dict) else {}
    runtime_state = _runtime_state_summary(
        runtime_gate=runtime_gate,
        health_matrix=health_matrix,
        timing_health=timing_health,
        provider_summary=provider_summary,
        action_closure=action_closure,
    )
    return {
        "version": DIAGNOSTICS_VERSION,
        "generated_at": generated_at,
        "runtime_mode": "full_runtime",
        "runtime_path_kind": "v5_pipeline",
        "bypass_reason": "",
        "runtime_state": runtime_state,
        "runtime_state_code": runtime_state.get("code", ""),
        "runtime_state_label": runtime_state.get("label", ""),
        "gate_status": runtime_gate.get("status", ""),
        "gate_label": runtime_gate.get("label", ""),
        "health_status": health_matrix.get("status", ""),
        "health_label": health_matrix.get("label", ""),
        "health_issue_count": int(health_matrix.get("issue_count") or 0),
        "repair_item_count": int(repair_plan.get("item_count") or 0),
        "primary_issue_fingerprint": runtime_gate.get("primary_issue_fingerprint") or repair_plan.get("primary_issue_fingerprint") or "",
        "primary_issue_key": runtime_gate.get("primary_issue_key") or repair_plan.get("primary_issue_source") or "",
        "next_step": repair_plan.get("next_step", "") or runtime_gate.get("next_step", ""),
        "question_type": getattr(intent, "question_type", ""),
        "data_scope": getattr(intent, "data_scope", ""),
        "strategy": getattr(plan, "strategy", ""),
        "sources": list(getattr(plan, "sources", ()) or ()),
        "execution_status": getattr(execution, "status", "not_executed") if execution is not None else "not_executed",
        "answer_chars": len(str(getattr(composed, "answer", "") or "").strip()) if composed is not None else 0,
        "total_ms": int(timing_health.get("total_ms") or 0),
        "slowest_stage": timing_health.get("slowest_stage", ""),
        "slowest_ms": int(timing_health.get("slowest_ms") or 0),
        "provider_quality_issue_count": int(provider_summary.get("quality_issue_count") or 0),
        "provider_total_duration_ms": int(provider_summary.get("total_duration_ms") or 0),
        "latest_action_status": action_closure.get("latest_status", ""),
        "latest_action_age_seconds": int(action_closure.get("latest_age_seconds") or 0),
        "latest_action_stuck": bool(action_closure.get("latest_stuck")),
        "result_context_kind": result_metadata.get("context_kind", ""),
        "result_type": getattr(result_context, "result_type", "") if result_context is not None else "",
        "result_count": getattr(result_context, "count", 0) if result_context is not None else 0,
    }


def _runtime_state_summary(
    *,
    runtime_gate: dict[str, Any],
    health_matrix: dict[str, Any],
    timing_health: dict[str, Any],
    provider_summary: dict[str, Any],
    action_closure: dict[str, Any],
) -> dict[str, Any]:
    if runtime_gate.get("blocking"):
        code = "blocked"
        severity = "critical"
        reason = "运行门禁存在阻断项"
    elif action_closure.get("latest_stuck"):
        code = "action_stuck"
        severity = action_closure.get("latest_stuck_severity") or "high"
        reason = "最近动作处理中时间过长"
    elif action_closure.get("latest_pending"):
        code = "waiting_action"
        severity = "medium"
        reason = "最近动作仍在处理中"
    elif int(timing_health.get("issue_count") or 0) > 0:
        code = "slow"
        severity = "medium"
        reason = "本轮链路耗时需要关注"
    elif int(provider_summary.get("quality_issue_count") or 0) > 0:
        code = "provider_quality"
        severity = "medium"
        reason = "Provider 结果质量需要修复"
    elif health_matrix.get("status") == "needs_attention":
        code = "needs_attention"
        severity = "medium"
        reason = "健康矩阵存在需要关注的层"
    else:
        code = "healthy"
        severity = "low"
        reason = "主链路暂无明显风险"
    label = {
        "blocked": "存在阻断",
        "action_stuck": "动作疑似卡住",
        "waiting_action": "等待动作完成",
        "slow": "链路偏慢",
        "provider_quality": "Provider 质量待修",
        "needs_attention": "需要关注",
        "healthy": "运行健康",
    }.get(code, code)
    return {
        "code": code,
        "label": label,
        "severity": severity,
        "reason": reason,
        "latest_action_status": action_closure.get("latest_status", ""),
        "latest_action_age_seconds": int(action_closure.get("latest_age_seconds") or 0),
        "total_ms": int(timing_health.get("total_ms") or 0),
        "provider_quality_issue_count": int(provider_summary.get("quality_issue_count") or 0),
        "health_issue_count": int(health_matrix.get("issue_count") or 0),
    }


def _runtime_health_grade(
    *,
    runtime_gate: dict[str, Any],
    health_matrix: dict[str, Any],
    pipeline_health: dict[str, Any],
    provider_summary: dict[str, Any],
    action_closure: dict[str, Any],
    result_context_quality: dict[str, Any],
    source_execution_contract: dict[str, Any],
    fast_response_contract: dict[str, Any],
    timing_health: dict[str, Any],
) -> dict[str, Any]:
    issue_count = 0
    issue_count += int(health_matrix.get("issue_count") or 0)
    issue_count += int(provider_summary.get("quality_issue_count") or 0)
    issue_count += int(source_execution_contract.get("issue_count") or 0)
    issue_count += int(result_context_quality.get("issue_count") or 0)
    issue_count += int(fast_response_contract.get("issue_count") or 0)
    issue_count += int(timing_health.get("issue_count") or 0)

    provider_failure_summary = (
        provider_summary.get("failure_summary") if isinstance(provider_summary.get("failure_summary"), dict) else {}
    )
    blocking_provider_count = int(provider_failure_summary.get("blocking_count") or 0)
    source_issue_count = int(source_execution_contract.get("issue_count") or 0)
    action_contract_status = str(action_closure.get("contract_status") or "")
    action_contract_severity = str(action_closure.get("contract_severity") or "")
    action_abnormal = bool(action_closure.get("latest_stuck")) or action_contract_status in {
        "pending_timeout",
        "terminal_without_receipt",
        "receipt_uncorrelated",
    }
    action_high_risk = action_contract_severity in {"high", "critical"}
    response_issue_count = int(fast_response_contract.get("issue_count") or 0) + int(timing_health.get("issue_count") or 0)
    result_context_issue_count = int(result_context_quality.get("issue_count") or 0)

    if runtime_gate.get("blocking") or pipeline_health.get("status") == "blocked" or health_matrix.get("status") == "blocked":
        code = "blocked"
        label = "主链路阻断"
        severity = "critical"
        usable = False
        reason = runtime_gate.get("label") or "主流程存在阻断项"
        primary_next_step = "先修复主流程阻断，再继续扩展能力。"
    elif action_abnormal or action_high_risk:
        code = "action_closure_abnormal"
        label = "动作闭环异常"
        severity = "high"
        usable = False
        reason = action_closure.get("contract_label") or "写操作确认、执行或回执链路异常"
        primary_next_step = "先修复确认、按钮回调和最终结果通知。"
    elif blocking_provider_count > 0 or source_issue_count > 0 or int(provider_summary.get("error_count") or 0) > 0:
        code = "partial_unavailable"
        label = "部分能力不可用"
        severity = "medium"
        usable = True
        reason = source_execution_contract.get("label") or "部分飞书能力未接通或执行失败"
        primary_next_step = "先补齐缺失或不可执行的飞书能力。"
    elif response_issue_count > 0 or result_context_issue_count > 0 or int(provider_summary.get("quality_issue_count") or 0) > 0:
        code = "usable_needs_optimization"
        label = "可用但需优化"
        severity = "medium"
        usable = True
        reason = "链路可用，但响应速度、结果缓存或返回质量还需要优化"
        primary_next_step = "先优化慢步骤、快速回复和结果缓存。"
    else:
        code = "usable"
        label = "可用"
        severity = "low"
        usable = True
        reason = "主链路暂无明显风险"
        primary_next_step = "可以继续做能力扩展和体验优化。"

    return {
        "code": code,
        "label": label,
        "severity": severity,
        "usable": usable,
        "reason": str(reason or ""),
        "issue_count": issue_count,
        "primary_next_step": primary_next_step,
        "signals": {
            "runtime_blocking": bool(runtime_gate.get("blocking")),
            "pipeline_status": pipeline_health.get("status", ""),
            "health_status": health_matrix.get("status", ""),
            "provider_error_count": int(provider_summary.get("error_count") or 0),
            "provider_blocking_count": blocking_provider_count,
            "source_issue_count": source_issue_count,
            "action_contract_status": action_contract_status,
            "action_contract_severity": action_contract_severity,
            "response_issue_count": response_issue_count,
            "result_context_issue_count": result_context_issue_count,
        },
    }


def _snapshot_lifecycle(
    *,
    generated_at: str,
    envelope: Any,
    runtime_gate: dict[str, Any],
    repair_plan: dict[str, Any],
    health_matrix: dict[str, Any],
) -> dict[str, Any]:
    execution = getattr(envelope, "execution", None)
    composed = getattr(envelope, "composed", None)
    answer = str(getattr(composed, "answer", "") or "").strip() if composed is not None else ""
    status = "actionable" if repair_plan.get("item_count") else "stable"
    if runtime_gate.get("blocking"):
        status = "blocked"
    elif health_matrix.get("status") == "needs_attention":
        status = "needs_attention"
    return {
        "generated_at": generated_at,
        "status": status,
        "label": {
            "stable": "快照稳定",
            "actionable": "有可执行修复项",
            "needs_attention": "需要关注",
            "blocked": "存在阻断",
        }.get(status, status),
        "has_execution": execution is not None,
        "has_answer": bool(answer),
        "has_repair_plan": bool(repair_plan.get("item_count")),
        "is_current_turn_snapshot": True,
    }


def _evidence_summary(
    *,
    pipeline_frames: list[dict[str, Any]],
    provider_summary: dict[str, Any],
    action_trace: list[dict[str, Any]],
    result_context_events: list[dict[str, Any]],
    result_context: Any,
    permission: dict[str, Any],
) -> dict[str, Any]:
    provider_items = provider_summary.get("items") if isinstance(provider_summary.get("items"), list) else []
    result_metadata = getattr(result_context, "metadata", None) if result_context is not None else {}
    result_metadata = result_metadata if isinstance(result_metadata, dict) else {}
    evidence_items = [
        {
            "key": "pipeline_frames",
            "label": "Pipeline 帧",
            "count": len([item for item in pipeline_frames if isinstance(item, dict)]),
            "available": bool(pipeline_frames),
        },
        {
            "key": "provider_results",
            "label": "Provider 结果",
            "count": len([item for item in provider_items if isinstance(item, dict)]),
            "available": bool(provider_items),
        },
        {
            "key": "action_trace",
            "label": "Action Trace",
            "count": len([item for item in action_trace if isinstance(item, dict)]),
            "available": bool(action_trace),
        },
        {
            "key": "result_context_events",
            "label": "Result Context 事件",
            "count": len([item for item in result_context_events if isinstance(item, dict)]),
            "available": bool(result_context_events),
        },
        {
            "key": "result_context",
            "label": "当前 Result Context",
            "count": int(getattr(result_context, "count", 0) or 0) if result_context is not None else 0,
            "available": result_context is not None,
            "context_kind": result_metadata.get("context_kind", ""),
            "result_type": getattr(result_context, "result_type", "") if result_context is not None else "",
        },
        {
            "key": "permission",
            "label": "权限决策",
            "count": 1 if permission.get("available") else 0,
            "available": bool(permission.get("available")),
            "allowed": bool(permission.get("allowed")),
            "requires_confirmation": bool(permission.get("requires_confirmation")),
        },
    ]
    missing = [item for item in evidence_items if not item.get("available")]
    return {
        "status": "complete" if not missing else "partial",
        "label": "证据链完整" if not missing else "证据链不完整",
        "available_count": len(evidence_items) - len(missing),
        "missing_count": len(missing),
        "items": evidence_items,
    }


def _correlation_summary(
    *,
    generated_at: str,
    envelope: Any,
    result_context: Any,
    action_trace: list[dict[str, Any]],
    provider_summary: dict[str, Any],
) -> dict[str, Any]:
    context = getattr(envelope, "context", None)
    plan = getattr(envelope, "plan", None)
    intent = getattr(envelope, "intent", None)
    chat_id = str(getattr(context, "chat_id", "") or "")
    query_id = str(getattr(result_context, "query_id", "") or "") if result_context is not None else ""
    strategy = str(getattr(plan, "strategy", "") or "")
    generated_token = generated_at.replace("-", "").replace(":", "").replace(".", "")[-12:]
    trace_seed = query_id or f"{chat_id[-8:]}-{strategy}-{generated_token}"
    trace_id = trace_seed[-24:] if len(trace_seed) > 24 else trace_seed
    latest_action = action_trace[-1] if action_trace and isinstance(action_trace[-1], dict) else {}
    provider_items = provider_summary.get("items") if isinstance(provider_summary.get("items"), list) else []
    return {
        "trace_id": trace_id,
        "chat_id_tail": chat_id[-8:] if chat_id else "",
        "query_id_tail": query_id[-12:] if query_id else "",
        "strategy": strategy,
        "intent": str(getattr(intent, "intent", "") or ""),
        "latest_action": str(latest_action.get("action") or ""),
        "latest_action_status": str(latest_action.get("status") or ""),
        "provider_sources": sorted({str(item.get("source") or "") for item in provider_items if isinstance(item, dict) and item.get("source")}),
        "provider_count": len([item for item in provider_items if isinstance(item, dict)]),
        "result_type": getattr(result_context, "result_type", "") if result_context is not None else "",
    }


def _debug_locator(
    *,
    correlation: dict[str, Any],
    snapshot_summary: dict[str, Any],
    runtime_gate: dict[str, Any],
    repair_plan: dict[str, Any],
    timing_health: dict[str, Any],
    provider_summary: dict[str, Any],
    action_closure: dict[str, Any],
    result_context: Any,
    result_context_quality: dict[str, Any],
) -> dict[str, Any]:
    slowest_provider = provider_summary.get("slowest_provider") if isinstance(provider_summary.get("slowest_provider"), dict) else {}
    query_id = str(getattr(result_context, "query_id", "") or "") if result_context is not None else ""
    primary_issue = (
        runtime_gate.get("primary_issue_fingerprint")
        or repair_plan.get("primary_issue_fingerprint")
        or snapshot_summary.get("primary_issue_fingerprint")
        or ""
    )
    parts = [
        f"trace={correlation.get('trace_id') or ''}",
        f"issue={primary_issue or 'none'}",
        f"strategy={snapshot_summary.get('strategy') or correlation.get('strategy') or ''}",
        f"state={snapshot_summary.get('runtime_state_code') or snapshot_summary.get('gate_status') or ''}",
    ]
    if timing_health.get("slowest_stage"):
        parts.append(f"slow={timing_health.get('slowest_stage')}:{timing_health.get('slowest_ms', 0)}ms")
    if slowest_provider.get("source"):
        parts.append(f"provider={slowest_provider.get('source')}:{slowest_provider.get('duration_ms', 0)}ms")
    if action_closure.get("latest_action_id"):
        parts.append(f"action={str(action_closure.get('latest_action_id') or '')[-8:]}")
    if query_id:
        parts.append(f"result={query_id[-8:]}")
    return {
        "trace_id": correlation.get("trace_id", ""),
        "chat_id_tail": correlation.get("chat_id_tail", ""),
        "query_id_tail": query_id[-12:] if query_id else correlation.get("query_id_tail", ""),
        "primary_issue_fingerprint": primary_issue,
        "primary_issue_key": runtime_gate.get("primary_issue_key") or snapshot_summary.get("primary_issue_key") or "",
        "strategy": snapshot_summary.get("strategy") or correlation.get("strategy") or "",
        "runtime_state": snapshot_summary.get("runtime_state_code", ""),
        "gate_status": runtime_gate.get("status", ""),
        "repair_item_count": int(repair_plan.get("item_count") or 0),
        "slowest_stage": timing_health.get("slowest_stage", ""),
        "slowest_ms": int(timing_health.get("slowest_ms") or 0),
        "slowest_provider_source": slowest_provider.get("source", ""),
        "slowest_provider_ms": int(slowest_provider.get("duration_ms") or 0) if slowest_provider else 0,
        "latest_action_id_tail": str(action_closure.get("latest_action_id") or "")[-8:] if action_closure.get("latest_action_id") else "",
        "latest_action_status": action_closure.get("latest_status", ""),
        "has_correlated_receipt": bool(action_closure.get("has_correlated_receipt")),
        "result_context_kind": result_context_quality.get("context_kind", ""),
        "result_type": result_context_quality.get("result_type", ""),
        "result_count": int(result_context_quality.get("count") or 0),
        "result_expires_in_seconds": int(result_context_quality.get("expires_in_seconds") or 0),
        "locator": "｜".join(part for part in parts if part and not part.endswith("=")),
    }


def _health_matrix(
    *,
    pipeline_health: dict[str, Any],
    timing_health: dict[str, Any],
    context_health: dict[str, Any],
    profile_health: dict[str, Any],
    followup_health: dict[str, Any],
    decision_health: dict[str, Any],
    router_health: dict[str, Any],
    execution_health: dict[str, Any],
    permission_health: dict[str, Any],
    answer_health: dict[str, Any],
    result_context_quality: dict[str, Any],
    action_closure: dict[str, Any],
    provider_summary: dict[str, Any],
    constitution_guard: dict[str, Any],
    source_execution_contract: dict[str, Any],
) -> dict[str, Any]:
    action_status = "healthy"
    action_issues = 0
    if action_closure.get("missing_field_count"):
        action_status = "needs_attention"
        action_issues += int(action_closure.get("missing_field_count") or 0)
    if action_closure.get("latest_pending") and not action_closure.get("has_action_receipt_event"):
        action_status = "needs_attention"
        action_issues += 1
    if action_closure.get("latest_terminal") and not action_closure.get("has_action_receipt_event"):
        action_status = "needs_attention"
        action_issues += 1
    provider_issue_count = (
        int(provider_summary.get("error_count") or 0)
        + int(provider_summary.get("denied_count") or 0)
        + int(provider_summary.get("skipped_count") or 0)
    )
    source_contract_issue_count = int(source_execution_contract.get("missing_count") or 0)
    layers = [
        _health_layer("pipeline", "Pipeline", pipeline_health.get("status"), int(pipeline_health.get("issue_count") or 0)),
        _health_layer("timing", "Timing", timing_health.get("status"), int(timing_health.get("issue_count") or 0)),
        _health_layer("context", "Runtime Context", context_health.get("status"), int(context_health.get("issue_count") or 0)),
        _health_layer("profile", "Profile Isolation", profile_health.get("status"), int(profile_health.get("issue_count") or 0)),
        _health_layer("followup", "Result Follow-up", followup_health.get("status"), int(followup_health.get("issue_count") or 0)),
        _health_layer("decision", "Intent/Planner", decision_health.get("status"), int(decision_health.get("issue_count") or 0)),
        _health_layer("router", "Capability Router", router_health.get("status"), int(router_health.get("issue_count") or 0)),
        _health_layer("execution", "Execution", execution_health.get("status"), int(execution_health.get("issue_count") or 0)),
        _health_layer("permission", "Permission", permission_health.get("status"), int(permission_health.get("issue_count") or 0)),
        _health_layer("answer", "Answer", answer_health.get("status"), int(answer_health.get("issue_count") or 0)),
        _health_layer(
            "result_context",
            "Result Context",
            result_context_quality.get("status") if result_context_quality.get("available") else "none",
            int(result_context_quality.get("issue_count") or 0),
        ),
        _health_layer("action", "Action Closure", action_status, action_issues),
        _health_layer("provider", "Provider Runtime", "healthy" if provider_issue_count == 0 else "needs_attention", provider_issue_count),
        _health_layer("source_contract", "Source Execution", "healthy" if source_contract_issue_count == 0 else "needs_attention", source_contract_issue_count),
        _health_layer("constitution", "Constitution", "healthy" if constitution_guard.get("healthy") else "needs_attention", int(constitution_guard.get("failed_count") or 0)),
    ]
    issue_count = sum(int(item.get("issue_count") or 0) for item in layers)
    unhealthy_layers = [item for item in layers if item.get("status") not in {"healthy", "none"}]
    return {
        "status": "healthy" if not unhealthy_layers else "needs_attention",
        "label": "各层健康" if not unhealthy_layers else f"{len(unhealthy_layers)} 层需要关注",
        "issue_count": issue_count,
        "status_counts": _status_counts(layers),
        "layers": layers,
    }


def _health_layer(key: str, label: str, status: Any, issue_count: int) -> dict[str, Any]:
    normalized = str(status or "unknown")
    return {
        "key": key,
        "label": label,
        "status": normalized,
        "status_label": {
            "healthy": "正常",
            "needs_attention": "需关注",
            "blocked": "阻断",
            "none": "无数据",
            "unknown": "未知",
        }.get(normalized, normalized),
        "issue_count": issue_count,
    }


def _constitution_guard(
    envelope: Any,
    result_context: Any,
    permission: dict[str, Any],
    provider_summary: dict[str, Any],
    path_maturity: dict[str, Any],
) -> dict[str, Any]:
    intent = getattr(envelope, "intent", None)
    plan = getattr(envelope, "plan", None)
    execution = getattr(envelope, "execution", None)
    metadata = getattr(result_context, "metadata", None) if result_context is not None else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    question_type = str(getattr(intent, "question_type", "") or "")
    strategy = str(getattr(plan, "strategy", "") or "")
    sources = tuple(getattr(plan, "sources", ()) or ())
    context_kind = str(metadata.get("context_kind") or "")
    item_count = int(metadata.get("item_count") or getattr(result_context, "count", 0) or 0) if result_context is not None else 0
    provider_count = int(provider_summary.get("count") or 0)
    declared_pairs = {(item.strategy, item.source) for item in RUNTIME_CAPABILITIES}
    missing_capability_pairs = [
        f"{strategy}:{source}"
        for source in sources
        if strategy and (strategy, str(source)) not in declared_pairs
    ]
    action_confirmation_ok = True
    if question_type == "action":
        action_confirmation_ok = bool(permission.get("requires_confirmation")) or str(getattr(execution, "status", "")) in {"denied", "skipped"}
    checks = [
        {
            "key": "structured_result_context",
            "label": "结构化结果上下文",
            "ok": result_context is None or context_kind in {"query_result", "action_receipt", "no_result"},
            "detail": context_kind or "无上下文",
            "severity": "high",
            "recommendation": "确保 Answer Composer 或 Runtime 兜底生成 ResultContext，禁止只返回纯文本答案。",
        },
        {
            "key": "followup_uses_items",
            "label": "追问优先使用结构化 items",
            "ok": result_context is None or context_kind == "no_result" or item_count > 0,
            "detail": f"{item_count} 条结构化条目",
            "severity": "high",
            "recommendation": "Provider 或 Action Receipt 必须写入 items，Follow-up 不应依赖 answer 文本。",
        },
        {
            "key": "action_confirmation",
            "label": "Action 二次确认",
            "ok": action_confirmation_ok,
            "detail": "需要确认" if permission.get("requires_confirmation") else "无需确认/未执行",
            "severity": "critical",
            "recommendation": "把该 Action 纳入 Capability 的 requires_confirmation，并确保 Permission Check 返回需要确认。",
        },
        {
            "key": "planner_router_separation",
            "label": "Planner 只给策略和来源",
            "ok": bool(strategy) and isinstance(getattr(plan, "sources", ()), tuple),
            "detail": f"{strategy} -> {', '.join(sources)}",
            "severity": "medium",
            "recommendation": "Planner 只应输出 strategy/sources，函数选择和执行细节必须留给 Capability Router。",
        },
        {
            "key": "planner_capability_contract",
            "label": "Planner 策略已纳入 Capability",
            "ok": not missing_capability_pairs,
            "detail": "、".join(missing_capability_pairs) or "已声明",
            "severity": "high",
            "recommendation": "Planner 新增 strategy/source 时，必须同步补 RUNTIME_CAPABILITIES，否则 Router 会进入隐性降级或空执行。",
        },
        {
            "key": "provider_execution_visible",
            "label": "Provider 执行可观测",
            "ok": execution is None or provider_count > 0,
            "detail": f"{provider_count} 个 Provider 结果",
            "severity": "medium",
            "recommendation": "Capability Router 必须记录每个 ProviderResult 的 source/status/operation/duration/error。",
        },
        {
            "key": "current_path_full_runtime",
            "label": "当前策略路径完整 V5",
            "ok": path_maturity.get("status") == "healthy",
            "detail": path_maturity.get("label") or "",
            "severity": "medium",
            "recommendation": "把当前策略从快速路径或旧适配入口迁移到 V5 Planner/Router/Provider 主链路。",
        },
    ]
    failed = [item for item in checks if not item.get("ok")]
    return {
        "healthy": not failed,
        "failed_count": len(failed),
        "checks": checks,
    }


def _runtime_gate(
    envelope: Any,
    result_context: Any,
    context_health: dict[str, Any],
    profile_health: dict[str, Any],
    followup_health: dict[str, Any],
    decision_health: dict[str, Any],
    router_health: dict[str, Any],
    execution_health: dict[str, Any],
    answer_health: dict[str, Any],
    permission: dict[str, Any],
    provider_summary: dict[str, Any],
    permission_health: dict[str, Any],
    constitution_guard: dict[str, Any],
    action_closure: dict[str, Any],
    result_context_quality: dict[str, Any],
    pipeline_health: dict[str, Any],
    timing_health: dict[str, Any],
    path_maturity: dict[str, Any],
    source_execution_contract: dict[str, Any],
) -> dict[str, Any]:
    intent = getattr(envelope, "intent", None)
    execution = getattr(envelope, "execution", None)
    composed = getattr(envelope, "composed", None)
    question_type = str(getattr(intent, "question_type", "") or "")
    execution_status = str(getattr(execution, "status", "not_executed") if execution is not None else "not_executed")
    context_summary = _result_context_summary(result_context)
    pipeline_timing = _composed_metadata_value(composed, "pipeline_timing", {})
    items: list[dict[str, Any]] = []

    for issue in context_health.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        items.append(
            {
                "key": f"context:{issue.get('kind') or 'unknown'}",
                "severity": issue.get("severity") or "medium",
                "label": issue.get("label") or "Runtime Context 健康问题",
                "detail": issue.get("detail") or "",
                "recommendation": issue.get("recommendation") or "",
            }
        )

    for issue in profile_health.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        items.append(
            {
                "key": f"profile:{issue.get('kind') or 'unknown'}",
                "severity": issue.get("severity") or "medium",
                "label": issue.get("label") or "Profile 隔离问题",
                "detail": issue.get("detail") or "",
                "recommendation": issue.get("recommendation") or "",
            }
        )

    for issue in followup_health.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        items.append(
            {
                "key": f"followup:{issue.get('kind') or 'unknown'}",
                "severity": issue.get("severity") or "medium",
                "label": issue.get("label") or "追问健康问题",
                "detail": issue.get("detail") or "",
                "recommendation": issue.get("recommendation") or "",
            }
        )

    for issue in decision_health.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        items.append(
            {
                "key": f"decision:{issue.get('kind') or 'unknown'}",
                "severity": issue.get("severity") or "medium",
                "label": issue.get("label") or "意图/规划健康问题",
                "detail": issue.get("detail") or "",
                "recommendation": issue.get("recommendation") or "",
            }
        )

    for issue in router_health.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        items.append(
            {
                "key": f"router:{issue.get('kind') or 'unknown'}",
                "severity": issue.get("severity") or "medium",
                "label": issue.get("label") or "Router 健康问题",
                "detail": issue.get("detail") or "",
                "recommendation": issue.get("recommendation") or "",
            }
        )

    for issue in timing_health.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        items.append(
            {
                "key": f"timing:{issue.get('kind') or 'unknown'}",
                "severity": issue.get("severity") or "medium",
                "label": issue.get("label") or "耗时健康问题",
                "detail": issue.get("detail") or "",
                "recommendation": issue.get("recommendation") or "",
            }
        )

    for issue in execution_health.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        items.append(
            {
                "key": f"execution:{issue.get('kind') or 'unknown'}",
                "severity": issue.get("severity") or "medium",
                "label": issue.get("label") or "Execution 健康问题",
                "detail": issue.get("detail") or "",
                "recommendation": issue.get("recommendation") or "",
            }
        )

    for issue in answer_health.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        items.append(
            {
                "key": f"answer:{issue.get('kind') or 'unknown'}",
                "severity": issue.get("severity") or "medium",
                "label": issue.get("label") or "答案组织健康问题",
                "detail": issue.get("detail") or "",
                "recommendation": issue.get("recommendation") or "",
            }
        )

    for check in constitution_guard.get("checks") or []:
        if not isinstance(check, dict) or check.get("ok"):
            continue
        items.append(
            {
                "key": f"constitution:{check.get('key') or 'unknown'}",
                "severity": check.get("severity") or "medium",
                "label": check.get("label") or "架构守卫风险",
                "detail": check.get("detail") or "",
                "recommendation": check.get("recommendation") or "",
            }
        )

    for issue in permission_health.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        items.append(
            {
                "key": f"permission:{issue.get('kind') or 'unknown'}",
                "severity": issue.get("severity") or "medium",
                "label": issue.get("label") or "权限健康问题",
                "detail": issue.get("detail") or "",
                "recommendation": issue.get("recommendation") or "",
            }
        )

    if int(provider_summary.get("error_count") or 0) > 0:
        slowest = provider_summary.get("slowest_provider") if isinstance(provider_summary.get("slowest_provider"), dict) else {}
        items.append(
            {
                "key": "provider:error",
                "severity": "high",
                "label": "Provider 执行失败",
                "detail": f"{provider_summary.get('error_count', 0)} 个失败"
                + (f"，最慢来源 {slowest.get('source')}" if slowest.get("source") else ""),
                "recommendation": "优先查看 Provider 修复建议和错误类型，避免 Router 返回原始 JSON 或空结果。",
            }
        )
    if int(provider_summary.get("denied_count") or 0) > 0:
        items.append(
            {
                "key": "provider:denied",
                "severity": "high",
                "label": "Provider 权限受限",
                "detail": f"{provider_summary.get('denied_count', 0)} 个无权限",
                "recommendation": "确认执行身份、飞书授权范围和 Provider 所需 scope 是否一致。",
            }
        )
    if int(source_execution_contract.get("missing_count") or 0) > 0:
        missing_sources = source_execution_contract.get("missing_sources") if isinstance(source_execution_contract.get("missing_sources"), list) else []
        items.append(
            {
                "key": "source_execution:missing_sources",
                "severity": "high",
                "label": "Planner 来源没有实际执行结果",
                "detail": "、".join(str(source) for source in missing_sources[:6]),
                "recommendation": "检查 Capability Router 是否按 Planner sources 顺序执行 Provider，并确保每个来源记录 ProviderResult。",
            }
        )
    for issue in provider_summary.get("quality_issues") or []:
        if not isinstance(issue, dict):
            continue
        items.append(
            {
                "key": f"provider_quality:{issue.get('kind') or 'unknown'}",
                "severity": issue.get("severity") or "medium",
                "label": issue.get("label") or "Provider 结果质量问题",
                "detail": issue.get("detail") or "",
                "recommendation": issue.get("recommendation") or "",
            }
        )
    if question_type == "action" and permission.get("allowed") and not permission.get("requires_confirmation"):
        items.append(
            {
                "key": "action:confirmation_missing",
                "severity": "critical",
                "label": "动作缺少确认门禁",
                "detail": str(getattr(getattr(envelope, "plan", None), "strategy", "") or ""),
                "recommendation": "所有写入、发送、审批、会议、任务等 Action 必须先生成确认卡。",
            }
        )
    if execution_status == "success" and not context_summary.get("available"):
        items.append(
            {
                "key": "result_context:missing_after_success",
                "severity": "high",
                "label": "成功执行后缺少 Result Context",
                "detail": str(getattr(getattr(envelope, "plan", None), "strategy", "") or ""),
                "recommendation": "成功查询或动作完成后必须写入结构化 Result Context，支撑追问和动作回执。",
            }
        )
    if context_summary.get("available") and context_summary.get("context_kind") not in {"query_result", "action_receipt", "no_result"}:
        items.append(
            {
                "key": "result_context:unknown_kind",
                "severity": "medium",
                "label": "Result Context 类型不规范",
                "detail": str(context_summary.get("context_kind") or ""),
                "recommendation": "统一使用 query_result、action_receipt、no_result 三类上下文。",
            }
        )
    for issue in result_context_quality.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        items.append(
            {
                "key": f"result_context_quality:{issue.get('kind') or 'unknown'}",
                "severity": issue.get("severity") or "medium",
                "label": issue.get("label") or "Result Context 质量问题",
                "detail": issue.get("detail") or "",
                "recommendation": issue.get("recommendation") or "",
            }
        )
    for issue in pipeline_health.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        items.append(
            {
                "key": f"pipeline:{issue.get('stage') or issue.get('kind') or 'unknown'}",
                "severity": issue.get("severity") or "medium",
                "label": issue.get("label") or "Pipeline 阶段风险",
                "detail": issue.get("detail") or "",
                "recommendation": issue.get("recommendation") or "",
            }
        )
    if isinstance(pipeline_timing, dict):
        try:
            total_ms = int(pipeline_timing.get("total_ms") or 0)
            slowest_ms = int(pipeline_timing.get("slowest_ms") or 0)
        except (TypeError, ValueError):
            total_ms = 0
            slowest_ms = 0
        if total_ms >= 8000 or slowest_ms >= 5000:
            items.append(
                {
                    "key": "runtime:slow_pipeline",
                    "severity": "medium",
                    "label": "本轮链路耗时偏高",
                    "detail": f"总耗时 {total_ms}ms，最慢阶段 {pipeline_timing.get('slowest_stage') or '未知'} {slowest_ms}ms",
                    "recommendation": "优先定位最慢阶段；如果在 execution/capability_router，继续拆 Provider 耗时和附件/OCR/批量写入。",
            }
        )
    if path_maturity.get("status") != "healthy" and path_maturity.get("available"):
        migration_items = path_maturity.get("migration_items") if isinstance(path_maturity.get("migration_items"), list) else []
        detail = "、".join(
            f"{item.get('source')}.{item.get('operation')}"
            for item in migration_items
            if isinstance(item, dict)
        )
        items.append(
            {
                "key": "runtime_path:not_full_v5",
                "severity": "medium",
                "label": "当前策略未完全进入 V5 主链路",
                "detail": detail or str(path_maturity.get("label") or ""),
                "recommendation": "优先迁移当前策略涉及的快速路径或旧适配入口，让请求统一进入 V5 Planner/Router/Provider。",
            }
        )

    if action_closure.get("latest_available"):
        if action_closure.get("missing_field_count"):
            items.append(
                {
                    "key": "action_trace:missing_fields",
                    "severity": "medium",
                    "label": "动作追踪字段不完整",
                    "detail": "、".join(action_closure.get("missing_fields") or []),
                    "recommendation": "所有 Action Trace 必须写入 kind/action/status/strategy/sources/execution_identity，便于定位按钮和异步执行问题。",
                }
            )
        if action_closure.get("latest_pending") and not action_closure.get("has_action_receipt_event"):
            items.append(
                {
                    "key": "action_closure:pending_without_receipt",
                    "severity": "medium",
                    "label": "动作已入队但还没有回执",
                    "detail": action_closure.get("latest_label") or "",
                    "recommendation": "如果长时间停在处理中，优先检查异步任务是否执行、是否发送最终回复、是否保存 action_receipt Result Context。",
                }
            )
        if action_closure.get("latest_stuck"):
            items.append(
                {
                    "key": "action_closure:stuck_pending",
                    "severity": action_closure.get("latest_stuck_severity") or "medium",
                    "label": "动作处理中时间过长",
                    "detail": f"{action_closure.get('latest_label') or ''}｜{action_closure.get('latest_age_seconds', 0)} 秒",
                    "recommendation": "优先检查 worker 是否消费任务、按钮回调是否重复执行、最终回复和 action_receipt 是否成功写入。",
                }
            )
        if action_closure.get("latest_terminal") and not action_closure.get("has_action_receipt_event"):
            items.append(
                {
                    "key": "action_closure:terminal_without_receipt",
                    "severity": "high",
                    "label": "动作已有最终状态但缺少回执上下文",
                    "detail": action_closure.get("latest_label") or "",
                    "recommendation": "动作成功、失败、取消或过期后都必须保存 action_receipt Result Context，避免用户追问时丢上下文。",
                }
            )

    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    items = [_with_issue_fingerprint(item) for item in sorted(items, key=lambda item: severity_order.get(str(item.get("severity") or "medium"), 2))[:8]]
    blocking = any(str(item.get("severity") or "") == "critical" for item in items)
    if blocking:
        status = "blocked"
        label = "存在阻断风险"
    elif items:
        status = "needs_attention"
        label = "需要继续加固"
    else:
        status = "healthy"
        label = "本轮链路健康"
    return {
        "status": status,
        "label": label,
        "blocking": blocking,
        "issue_count": len(items),
        "primary_issue_fingerprint": items[0].get("fingerprint", "") if items else "",
        "primary_issue_key": items[0].get("key", "") if items else "",
        "severity_counts": _severity_counts(items),
        "priority_items": items,
        "next_step": items[0].get("recommendation", "") if items else "可以继续扩展业务 Provider 或做端到端验证。",
    }


def _execution_health(envelope: Any, provider_summary: dict[str, Any], result_context: Any) -> dict[str, Any]:
    execution = getattr(envelope, "execution", None)
    intent = getattr(envelope, "intent", None)
    question_type = str(getattr(intent, "question_type", "") or "")
    execution_status = str(getattr(execution, "status", "not_executed") if execution is not None else "not_executed")
    provider_count = int(provider_summary.get("count") or 0)
    success_count = int(provider_summary.get("success_count") or 0)
    error_count = int(provider_summary.get("error_count") or 0)
    denied_count = int(provider_summary.get("denied_count") or 0)
    skipped_count = int(provider_summary.get("skipped_count") or 0)
    issues: list[dict[str, Any]] = []
    if execution is None and question_type not in {"", "query"}:
        issues.append(
            {
                "kind": "missing_execution",
                "severity": "high",
                "label": "缺少 Execution 结果",
                "detail": question_type,
                "recommendation": "除澄清/小聊外，Planner 和 Permission 之后应进入 Execution，并记录 ExecutionResult。",
            }
        )
    if execution is not None and provider_count == 0 and execution_status not in {"denied", "skipped"}:
        issues.append(
            {
                "kind": "execution_without_provider_results",
                "severity": "high",
                "label": "Execution 缺少 ProviderResult",
                "detail": execution_status,
                "recommendation": "Execution 必须保留 ProviderResult 明细，包括 success/error/skipped/denied，便于追踪来源执行情况。",
            }
        )
    if execution_status == "success" and error_count:
        issues.append(
            {
                "kind": "success_with_provider_errors",
                "severity": "medium",
                "label": "执行标记成功但 Provider 有失败",
                "detail": f"失败 {error_count} 个",
                "recommendation": "存在 Provider 失败时应考虑 partial 或在答案中明确降级来源，避免整体成功掩盖局部失败。",
            }
        )
    if execution_status == "success" and result_context is None and question_type in {"query", "analysis", "insight", "decision", "action"}:
        issues.append(
            {
                "kind": "success_without_result_context",
                "severity": "high",
                "label": "执行成功但缺少 Result Context",
                "detail": question_type,
                "recommendation": "成功执行后应保存 query_result、action_receipt 或 no_result，支撑追问和动作闭环。",
            }
        )
    if provider_count and success_count == 0 and execution_status == "success":
        issues.append(
            {
                "kind": "success_without_success_provider",
                "severity": "high",
                "label": "无成功 Provider 但执行标记成功",
                "detail": f"provider={provider_count}",
                "recommendation": "没有任何 Provider 成功时，Execution 不应标记 success，应返回 partial/error/no_result。",
            }
        )
    return {
        "status": "healthy" if not issues else "needs_attention",
        "label": "Execution 正常" if not issues else "Execution 需要关注",
        "issue_count": len(issues),
        "execution_status": execution_status,
        "provider_count": provider_count,
        "success_count": success_count,
        "error_count": error_count,
        "denied_count": denied_count,
        "skipped_count": skipped_count,
        "has_result_context": result_context is not None,
        "issues": issues[:8],
    }


def _router_health(envelope: Any, provider_summary: dict[str, Any]) -> dict[str, Any]:
    plan = getattr(envelope, "plan", None)
    execution = getattr(envelope, "execution", None)
    planned_sources = [str(source) for source in (getattr(plan, "sources", ()) or ()) if str(source)]
    provider_items = provider_summary.get("items") if isinstance(provider_summary.get("items"), list) else []
    executed_sources = [str(item.get("source") or "") for item in provider_items if isinstance(item, dict) and item.get("source")]
    planned_set = set(planned_sources)
    executed_set = set(executed_sources)
    missing_sources = sorted(planned_set - executed_set)
    extra_sources = sorted(executed_set - planned_set)
    covered_sources = sorted(planned_set & executed_set)
    coverage_percent = int(round((len(covered_sources) / len(planned_set)) * 100)) if planned_set else 100
    issues: list[dict[str, Any]] = []
    if execution is not None and planned_sources and not executed_sources:
        issues.append(
            {
                "kind": "no_provider_execution",
                "severity": "high",
                "label": "Router 未产生 Provider 执行结果",
                "detail": "、".join(planned_sources),
                "recommendation": "Capability Router 必须按 Planner sources 生成 ProviderResult，禁止静默跳过执行层。",
            }
        )
    if missing_sources:
        issues.append(
            {
                "kind": "planned_source_not_executed",
                "severity": "high",
                "label": "计划来源未执行",
                "detail": "、".join(missing_sources),
                "recommendation": "Router 应按 Planner sources 顺序执行；如果来源不可用，应返回 skipped/error ProviderResult。",
            }
        )
    if extra_sources:
        issues.append(
            {
                "kind": "unplanned_source_executed",
                "severity": "medium",
                "label": "执行了未规划来源",
                "detail": "、".join(extra_sources),
                "recommendation": "Router 不应自行规划来源；若确有必要，应回到 Planner/Capability 声明层补齐。",
            }
        )
    return {
        "status": "healthy" if not issues else "needs_attention",
        "label": "Router 来源自洽" if not issues else "Router 来源需要关注",
        "issue_count": len(issues),
        "planned_sources": planned_sources,
        "executed_sources": executed_sources,
        "executed_unique_sources": sorted(executed_set),
        "covered_sources": covered_sources,
        "missing_sources": missing_sources,
        "extra_sources": extra_sources,
        "coverage_percent": coverage_percent,
        "provider_count": len(executed_sources),
        "issues": issues[:8],
    }


def _followup_health(envelope: Any, result_context: Any) -> dict[str, Any]:
    intent = getattr(envelope, "intent", None)
    entities = getattr(intent, "entities", {}) if intent is not None else {}
    entities = entities if isinstance(entities, dict) else {}
    answer_metadata = getattr(getattr(envelope, "composed", None), "metadata", {})
    answer_metadata = answer_metadata if isinstance(answer_metadata, dict) else {}
    uses_previous_result = bool(entities.get("use_previous_result"))
    followup_type = str(answer_metadata.get("followup_type") or "")
    metadata = getattr(result_context, "metadata", None) if result_context is not None else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    item_count = int(metadata.get("item_count") or getattr(result_context, "count", 0) or 0) if result_context is not None else 0
    context_kind = str(metadata.get("context_kind") or "")
    issues: list[dict[str, Any]] = []
    if uses_previous_result and result_context is None:
        issues.append(
            {
                "kind": "followup_without_result_context",
                "severity": "high",
                "label": "追问缺少上一轮结果",
                "detail": followup_type or "unknown",
                "recommendation": "Follow-up 必须依赖 Result Context；没有结构化结果时应重新查询或澄清，而不是消费 answer 文本。",
            }
        )
    if uses_previous_result and result_context is not None and context_kind != "no_result" and item_count <= 0:
        issues.append(
            {
                "kind": "followup_without_items",
                "severity": "high",
                "label": "追问缺少结构化 items",
                "detail": context_kind or "unknown",
                "recommendation": "追问必须优先消费 Result Context.items，禁止优先消费 answer 文本。",
            }
        )
    if followup_type and not uses_previous_result:
        issues.append(
            {
                "kind": "followup_metadata_without_flag",
                "severity": "medium",
                "label": "追问元数据和意图标记不一致",
                "detail": followup_type,
                "recommendation": "Result Follow-up Detector 与 Intent entities.use_previous_result 应保持一致。",
            }
        )
    if uses_previous_result and context_kind == "action_receipt" and followup_type and followup_type != "receipt_detail":
        issues.append(
            {
                "kind": "action_receipt_followup_type_mismatch",
                "severity": "medium",
                "label": "动作回执追问类型不匹配",
                "detail": followup_type,
                "recommendation": "动作回执类追问应优先走 receipt_detail，回答链接、状态、目标和失败原因。",
            }
        )
    return {
        "status": "healthy" if not issues else "needs_attention",
        "label": "追问检测正常" if not issues else "追问检测需要关注",
        "issue_count": len(issues),
        "uses_previous_result": uses_previous_result,
        "followup_type": followup_type,
        "context_kind": context_kind,
        "item_count": item_count,
        "issues": issues[:8],
    }


def _followup_consume_contract(followup_health: dict[str, Any], result_context: Any) -> dict[str, Any]:
    metadata = getattr(result_context, "metadata", None) if result_context is not None else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    followup_fields = metadata.get("followup_fields") if isinstance(metadata.get("followup_fields"), list) else []
    item_identity_fields = metadata.get("item_identity_fields") if isinstance(metadata.get("item_identity_fields"), list) else []
    consume_policy = metadata.get("consume_policy") if isinstance(metadata.get("consume_policy"), dict) else {}
    uses_previous_result = bool(followup_health.get("uses_previous_result"))
    item_count = int(metadata.get("item_count") or getattr(result_context, "count", 0) or 0) if result_context is not None else 0
    prefer_items = bool(consume_policy.get("prefer_items", False))
    allow_answer_fallback = bool(consume_policy.get("allow_answer_fallback", False))
    context_kind = str(followup_health.get("context_kind") or metadata.get("context_kind") or "")
    issues: list[dict[str, Any]] = []
    if uses_previous_result and result_context is None:
        issues.append(_followup_contract_issue("missing_result_context"))
    if uses_previous_result and item_count <= 0:
        issues.append(_followup_contract_issue("missing_items"))
    if uses_previous_result and not prefer_items:
        issues.append(_followup_contract_issue("not_items_first"))
    if uses_previous_result and not followup_fields:
        issues.append(_followup_contract_issue("missing_followup_fields"))
    if uses_previous_result and not item_identity_fields:
        issues.append(_followup_contract_issue("missing_identity_fields"))
    if uses_previous_result and context_kind == "query_result" and allow_answer_fallback:
        issues.append(_followup_contract_issue("query_result_answer_fallback_enabled"))
    if uses_previous_result and consume_policy and not bool(consume_policy.get("requires_refresh_when_expired", False)):
        issues.append(_followup_contract_issue("missing_refresh_on_expiry"))
    issue_keys = [str(item.get("kind") or "") for item in issues if isinstance(item, dict)]
    status = "healthy" if not issues else "needs_attention"
    if not uses_previous_result:
        label = "非追问，不消费上一轮结果"
    elif status == "healthy":
        label = "追问按 items-first 消费"
    else:
        label = "追问消费契约需要关注"
    consume_priority = _result_context_consume_priority(
        context_kind=context_kind,
        item_count=item_count,
        followup_fields=followup_fields,
        item_identity_fields=item_identity_fields,
        consume_policy=consume_policy,
        raw_answer_detected=False,
        uses_previous_result=uses_previous_result,
        issues=issues,
    )
    return {
        "status": status,
        "label": label,
        "uses_previous_result": uses_previous_result,
        "followup_type": followup_health.get("followup_type", ""),
        "context_kind": context_kind,
        "item_count": item_count,
        "prefer_items": prefer_items,
        "allow_answer_fallback": allow_answer_fallback,
        "requires_refresh_when_expired": bool(consume_policy.get("requires_refresh_when_expired", False)) if consume_policy else False,
        "supports_index_followup": bool(consume_policy.get("supports_index_followup", False)) if consume_policy else False,
        "supports_detail_followup": bool(consume_policy.get("supports_detail_followup", False)) if consume_policy else False,
        "followup_fields": followup_fields,
        "followup_field_count": len(followup_fields),
        "item_identity_fields": item_identity_fields,
        "item_identity_field_count": len(item_identity_fields),
        "issue_count": len(issues),
        "issue_keys": issue_keys,
        "issues": issues[:8],
        "consume_order": consume_priority["consume_order"],
        "primary_consume_source": consume_priority["primary_consume_source"],
        "answer_fallback_status": consume_priority["answer_fallback_status"],
        "answer_fallback_label": consume_priority["answer_fallback_label"],
        "repair_queue_count": consume_priority["repair_queue_count"],
        "repair_queue": consume_priority["repair_queue"],
        "top_repair_item": consume_priority["top_repair_item"],
    }


def _followup_contract_issue(kind: str) -> dict[str, Any]:
    payload = {
        "missing_result_context": {
            "severity": "high",
            "label": "追问缺少 Result Context",
            "recommendation": "没有结构化结果时应重新查询或引导补充，不能消费上一轮 answer 文本。",
        },
        "missing_items": {
            "severity": "high",
            "label": "追问缺少结构化条目",
            "recommendation": "补齐 Result Context.items，详情、序号、批量动作都必须从 items 取数。",
        },
        "not_items_first": {
            "severity": "high",
            "label": "追问未声明 items 优先",
            "recommendation": "Result Context.consume_policy.prefer_items 必须为 true。",
        },
        "missing_followup_fields": {
            "severity": "medium",
            "label": "缺少可追问字段",
            "recommendation": "保存 Result Context 时写入 followup_fields，说明可按哪些字段展开或追问。",
        },
        "missing_identity_fields": {
            "severity": "medium",
            "label": "缺少条目标识字段",
            "recommendation": "保存 item_identity_fields，保证“第一个详情/通过第一个/发给某人”能定位原始条目。",
        },
        "query_result_answer_fallback_enabled": {
            "severity": "high",
            "label": "查询结果允许 answer 兜底",
            "recommendation": "query_result 追问必须优先消费 items，禁止回退到 answer 文本导致 JSON 或摘要误用。",
        },
        "missing_refresh_on_expiry": {
            "severity": "medium",
            "label": "过期刷新策略缺失",
            "recommendation": "consume_policy.requires_refresh_when_expired 应为 true，过期后重新查而不是使用旧结果。",
        },
    }.get(kind, {"severity": "medium", "label": kind or "追问消费问题", "recommendation": "检查 Result Context 消费策略。"})
    return {"kind": kind, **payload}


def _result_context_consume_priority(
    *,
    context_kind: str,
    item_count: int,
    followup_fields: list[Any],
    item_identity_fields: list[Any],
    consume_policy: dict[str, Any],
    raw_answer_detected: bool,
    uses_previous_result: bool,
    issues: list[dict[str, Any]],
) -> dict[str, Any]:
    prefer_items = bool(consume_policy.get("prefer_items", False)) if consume_policy else False
    allow_answer_fallback = bool(consume_policy.get("allow_answer_fallback", False)) if consume_policy else False
    items_ready = item_count > 0 and prefer_items
    metadata_ready = bool(followup_fields or item_identity_fields or consume_policy)
    if items_ready:
        primary_source = "items"
    elif metadata_ready:
        primary_source = "metadata"
    elif allow_answer_fallback:
        primary_source = "answer"
    else:
        primary_source = "none"
    if raw_answer_detected:
        answer_status = "blocked_raw_payload"
        answer_label = "禁止使用 answer：疑似技术载荷"
    elif context_kind == "query_result":
        answer_status = "blocked_for_query_result"
        answer_label = "查询结果禁止 answer 兜底"
    elif allow_answer_fallback:
        answer_status = "allowed_for_empty_or_receipt"
        answer_label = "允许 answer 兜底"
    else:
        answer_status = "blocked_by_policy"
        answer_label = "策略未允许 answer 兜底"
    consume_order = [
        {
            "source": "items",
            "label": "结构化条目",
            "priority": 1,
            "status": "ready" if items_ready else "missing",
            "reason": "追问、序号、详情和动作定位必须优先消费 items。",
        },
        {
            "source": "metadata",
            "label": "上下文元数据",
            "priority": 2,
            "status": "ready" if metadata_ready else "missing",
            "reason": "metadata 提供 followup_fields、item_identity_fields、consume_policy 和过期策略。",
        },
        {
            "source": "answer",
            "label": "展示摘要",
            "priority": 3,
            "status": "allowed" if allow_answer_fallback and answer_status.startswith("allowed") else "blocked",
            "reason": answer_label,
        },
    ]
    repair_queue = _result_context_consume_repair_queue(issues, uses_previous_result=uses_previous_result)
    return {
        "consume_order": consume_order,
        "primary_consume_source": primary_source,
        "answer_fallback_status": answer_status,
        "answer_fallback_label": answer_label,
        "repair_queue_count": len(repair_queue),
        "repair_queue": repair_queue[:8],
        "top_repair_item": repair_queue[0] if repair_queue else {},
    }


def _result_context_consume_repair_queue(issues: list[dict[str, Any]], *, uses_previous_result: bool) -> list[dict[str, Any]]:
    if not uses_previous_result and not issues:
        return []
    score_by_kind = {
        "missing_result_context": 95,
        "missing_items": 92,
        "count_without_items": 90,
        "not_items_first": 88,
        "consume_policy_not_items_first": 88,
        "query_result_answer_fallback_enabled": 84,
        "query_result_allows_answer_fallback": 82,
        "raw_answer_payload": 80,
        "missing_identity_fields": 62,
        "missing_item_identity_fields": 62,
        "missing_followup_fields": 58,
        "missing_consume_policy": 56,
        "missing_refresh_on_expiry": 48,
        "result_context_expiring_soon": 32,
    }
    rows: list[dict[str, Any]] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        kind = str(issue.get("kind") or "")
        score = score_by_kind.get(kind, 45)
        rows.append(
            {
                "kind": kind,
                "severity": str(issue.get("severity") or "medium"),
                "label": str(issue.get("label") or kind or "追问消费问题"),
                "detail": str(issue.get("detail") or ""),
                "next_step": str(issue.get("recommendation") or ""),
                "priority_score": score,
                "priority_label": "高" if score >= 80 else "中" if score >= 45 else "低",
            }
        )
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return sorted(
        rows,
        key=lambda item: (
            -int(item.get("priority_score") or 0),
            severity_order.get(str(item.get("severity") or "medium"), 2),
            str(item.get("kind") or ""),
        ),
    )


def _profile_health(envelope: Any) -> dict[str, Any]:
    context = getattr(envelope, "context", None)
    intent = getattr(envelope, "intent", None)
    plan = getattr(envelope, "plan", None)
    permission = getattr(envelope, "permission", None)
    profile = getattr(context, "profile", None)
    issues: list[dict[str, Any]] = []
    profile_keys = {"profile", "style", "verbosity", "use_formatting", "tone", "tone_tips"}
    control_fields = {
        "data_scope": getattr(intent, "data_scope", ""),
        "strategy": getattr(plan, "strategy", ""),
        "sources": ",".join(str(item) for item in (getattr(plan, "sources", ()) or ())),
        "execution_identity": getattr(permission, "execution_identity", ""),
    }
    entities = getattr(intent, "entities", {}) if intent is not None else {}
    if not isinstance(entities, dict):
        entities = {}
    plan_metadata = getattr(plan, "metadata", {}) if plan is not None else {}
    if not isinstance(plan_metadata, dict):
        plan_metadata = {}
    permission_metadata = getattr(permission, "metadata", {}) if permission is not None else {}
    if not isinstance(permission_metadata, dict):
        permission_metadata = {}

    if profile is None:
        issues.append(
            {
                "kind": "missing_profile",
                "severity": "low",
                "label": "Profile 未加载",
                "detail": "使用默认沟通偏好",
                "recommendation": "Pre Gateway 应加载 Runtime Profile；缺失时只能回退默认风格，不能影响权限、数据或路由。",
            }
        )

    leaked_entities = sorted(profile_keys.intersection(str(key) for key in entities.keys()))
    if leaked_entities:
        issues.append(
            {
                "kind": "profile_in_intent_entities",
                "severity": "medium",
                "label": "Profile 渗入 Intent 实体",
                "detail": "、".join(leaked_entities),
                "recommendation": "Intent Recognition 只应输出问题类型、意图、数据范围、实体和缺参；Profile 只能留给 Answer Composer。",
            }
        )

    leaked_plan = sorted(profile_keys.intersection(str(key) for key in plan_metadata.keys()))
    if leaked_plan:
        issues.append(
            {
                "kind": "profile_in_planner_metadata",
                "severity": "high",
                "label": "Profile 渗入 Planner",
                "detail": "、".join(leaked_plan),
                "recommendation": "Task Planner 只能决定 strategy/sources，禁止根据沟通偏好改变知识来源或路由策略。",
            }
        )

    leaked_permission = sorted(profile_keys.intersection(str(key) for key in permission_metadata.keys()))
    if leaked_permission:
        issues.append(
            {
                "kind": "profile_in_permission_metadata",
                "severity": "critical",
                "label": "Profile 渗入权限决策",
                "detail": "、".join(leaked_permission),
                "recommendation": "Permission Check 只能依据身份、权限和目标资源，禁止读取 Profile 影响 allowed、scope 或 execution_identity。",
            }
        )

    suspicious_control = [
        key
        for key, value in control_fields.items()
        if any(token in str(value).lower() for token in ("profile", "style", "verbosity", "tone"))
    ]
    if suspicious_control:
        issues.append(
            {
                "kind": "profile_in_control_plane",
                "severity": "high",
                "label": "Profile 疑似影响控制面",
                "detail": "、".join(suspicious_control),
                "recommendation": "数据范围、策略、来源和执行身份必须由问题语义、权限和能力契约决定，不能由沟通偏好决定。",
            }
        )

    return {
        "status": "healthy" if not issues else "needs_attention",
        "label": "Profile 隔离正常" if not issues else "Profile 隔离需关注",
        "issue_count": len(issues),
        "profile_available": profile is not None,
        "style": str(getattr(profile, "style", "") or ""),
        "verbosity": str(getattr(profile, "verbosity", "") or ""),
        "use_formatting": bool(getattr(profile, "use_formatting", False)) if profile is not None else False,
        "has_tone_tips": bool(str(getattr(profile, "tone_tips", "") or "").strip()) if profile is not None else False,
        "allowed_effects": ["回答风格", "答案结构", "详细程度"],
        "forbidden_effects": ["权限", "数据范围", "路由来源", "Tool/Skill 选择", "执行身份"],
        "issues": issues,
    }


def _context_health(envelope: Any) -> dict[str, Any]:
    context = getattr(envelope, "context", None)
    issues: list[dict[str, Any]] = []
    if context is None:
        issues.append(
            {
                "kind": "missing_runtime_context",
                "severity": "critical",
                "label": "缺少 Runtime Context",
                "detail": "",
                "recommendation": "V5 必须先由 Pre Gateway 构建 Runtime Context，再进入 Follow-up、Intent 和 Planner。",
            }
        )
        return {
            "status": "needs_attention",
            "label": "Runtime Context 缺失",
            "issue_count": len(issues),
            "identity_available": False,
            "session_available": False,
            "profile_available": False,
            "current_message_available": False,
            "result_context_available": False,
            "issues": issues,
        }
    identity = getattr(context, "identity", None)
    session_context = getattr(context, "session_context", None)
    profile = getattr(context, "profile", None)
    current_message = str(getattr(context, "current_message", "") or "").strip()
    result_context = getattr(context, "result_context", None)
    identity_available = bool(str(getattr(identity, "open_id", "") or getattr(identity, "user_id", "") or "").strip()) if identity is not None else False
    if not identity_available:
        issues.append(
            {
                "kind": "missing_identity",
                "severity": "high",
                "label": "身份上下文缺失",
                "detail": "",
                "recommendation": "Pre Gateway 必须加载 Feishu Identity，权限、视图和执行身份都依赖它。",
            }
        )
    if not isinstance(session_context, dict):
        issues.append(
            {
                "kind": "invalid_session_context",
                "severity": "medium",
                "label": "Session Context 不可用",
                "detail": type(session_context).__name__,
                "recommendation": "Session Context 应为 dict，并从 Redis 会话键加载。",
            }
        )
    if profile is None:
        issues.append(
            {
                "kind": "missing_profile",
                "severity": "low",
                "label": "Profile 缺失",
                "detail": "",
                "recommendation": "Profile 只影响表达风格，但仍应通过默认 Profile 兜底。",
            }
        )
    if not current_message:
        issues.append(
            {
                "kind": "empty_current_message",
                "severity": "high",
                "label": "当前消息为空",
                "detail": "",
                "recommendation": "Current Message 是本轮理解入口，不能为空。",
            }
        )
    return {
        "status": "healthy" if not issues else "needs_attention",
        "label": "Runtime Context 正常" if not issues else "Runtime Context 需要关注",
        "issue_count": len(issues),
        "identity_available": identity_available,
        "session_available": isinstance(session_context, dict),
        "profile_available": profile is not None,
        "current_message_available": bool(current_message),
        "result_context_available": result_context is not None,
        "chat_id_available": bool(str(getattr(context, "chat_id", "") or "").strip()),
        "issues": issues[:8],
    }


def _answer_health(envelope: Any, result_context: Any) -> dict[str, Any]:
    composed = getattr(envelope, "composed", None)
    intent = getattr(envelope, "intent", None)
    question_type = str(getattr(intent, "question_type", "") or "")
    answer = str(getattr(composed, "answer", "") or "") if composed is not None else ""
    answer_stripped = answer.strip()
    metadata = getattr(composed, "metadata", None) if composed is not None else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    issues: list[dict[str, Any]] = []
    if composed is None:
        issues.append(
            {
                "kind": "missing_composed_answer",
                "severity": "high",
                "label": "缺少 Answer Composer 输出",
                "detail": "",
                "recommendation": "V5 主链路必须由 Answer Composer 统一组织最终回复，禁止 Provider 直接裸回。",
            }
        )
    elif not answer_stripped:
        issues.append(
            {
                "kind": "empty_answer",
                "severity": "high",
                "label": "答案为空",
                "detail": question_type,
                "recommendation": "Answer Composer 必须给出面向用户的结论、依据或下一步，而不是空回复。",
            }
        )
    raw_payload_detected = (
        answer_stripped.startswith("{")
        or answer_stripped.startswith("[")
        or "HTTP error" in answer_stripped
        or "status_code" in answer_stripped
    )
    if raw_payload_detected:
        issues.append(
            {
                "kind": "raw_json_answer",
                "severity": "high",
                "label": "疑似原始技术载荷回复",
                "detail": answer_stripped[:60],
                "recommendation": "最终回复必须经过 Answer Composer 转成人话摘要；原始结构只能进入 Result Context 或诊断，不应直接发给用户。",
            }
        )
    if len(answer_stripped) > 3500:
        issues.append(
            {
                "kind": "answer_too_long",
                "severity": "medium",
                "label": "答案过长",
                "detail": f"{len(answer_stripped)} 字符",
                "recommendation": "飞书卡片内应先给结论和关键依据，长明细放入文档、表格或 Result Context 追问。",
            }
        )
    if question_type in {"query", "analysis", "insight", "decision"} and result_context is None and not metadata.get("requires_confirmation"):
        issues.append(
            {
                "kind": "answer_without_result_context",
                "severity": "medium",
                "label": "答案缺少结构化上下文",
                "detail": question_type,
                "recommendation": "查询、分析、洞察和决策类回答应尽量保存 Result Context，支撑追问和解释依据。",
            }
        )
    if metadata.get("requires_confirmation") and "确认" not in answer_stripped:
        issues.append(
            {
                "kind": "confirmation_answer_unclear",
                "severity": "medium",
                "label": "确认提示不够明确",
                "detail": question_type,
                "recommendation": "需要确认的 Action 回复应清楚说明动作、影响、确认原因和确认方式。",
            }
        )
    return {
        "status": "healthy" if not issues else "needs_attention",
        "label": "答案组织正常" if not issues else "答案组织需要关注",
        "issue_count": len(issues),
        "answer_chars": len(answer_stripped),
        "requires_confirmation": bool(metadata.get("requires_confirmation")),
        "raw_payload_detected": raw_payload_detected,
        "issues": issues[:8],
    }


def _decision_health(envelope: Any) -> dict[str, Any]:
    intent = getattr(envelope, "intent", None)
    plan = getattr(envelope, "plan", None)
    execution = getattr(envelope, "execution", None)
    issues: list[dict[str, Any]] = []
    question_type = str(getattr(intent, "question_type", "") or "")
    intent_name = str(getattr(intent, "intent", "") or "")
    data_scope = str(getattr(intent, "data_scope", "") or "")
    strategy = str(getattr(plan, "strategy", "") or "")
    sources = tuple(getattr(plan, "sources", ()) or ())
    confidence = float(getattr(intent, "confidence", 0.0) or 0.0) if intent is not None else 0.0
    missing_params = tuple(getattr(intent, "missing_params", ()) or ()) if intent is not None else ()
    should_clarify = confidence < 0.6 or bool(missing_params)
    needs_clarification = bool(getattr(intent, "needs_clarification", False)) if intent is not None else False
    execution_status = getattr(execution, "status", "") if execution is not None else ""
    if intent is None:
        issues.append(
            {
                "kind": "missing_intent",
                "severity": "high",
                "label": "缺少意图识别结果",
                "detail": "",
                "recommendation": "Intent Recognition 必须输出 question_type、intent、data_scope、confidence 和 missing_params。",
            }
        )
    if plan is None:
        issues.append(
            {
                "kind": "missing_plan",
                "severity": "high",
                "label": "缺少任务规划结果",
                "detail": "",
                "recommendation": "Task Planner 必须输出 strategy 和 sources，Router 不应自己规划。",
            }
        )
    if intent is not None and confidence < 0.6:
        issues.append(
            {
                "kind": "low_confidence",
                "severity": "medium",
                "label": "意图置信度偏低",
                "detail": f"{confidence:.2f}",
                "recommendation": "低置信度应进入澄清，禁止在不确定时继续执行高风险动作。",
            }
        )
    if missing_params:
        issues.append(
            {
                "kind": "missing_params",
                "severity": "medium",
                "label": "缺少必要参数",
                "detail": "、".join(str(item) for item in missing_params),
                "recommendation": "missing_params 非空时应进入引导式澄清，避免 Planner 伪造参数。",
            }
        )
    if intent is not None and needs_clarification != should_clarify:
        issues.append(
            {
                "kind": "clarification_rule_mismatch",
                "severity": "high",
                "label": "澄清规则与 V5 宪法不一致",
                "detail": f"needs={needs_clarification} expected={should_clarify}",
                "recommendation": "只允许 confidence < 0.6 或 missing_params 非空时进入澄清。",
            }
        )
    if should_clarify and execution is not None:
        issues.append(
            {
                "kind": "executed_before_clarification",
                "severity": "critical",
                "label": "缺参或低置信度时仍进入执行",
                "detail": execution_status or "executed",
                "recommendation": "缺少必要参数或低置信度时必须先澄清，禁止 Router 执行。",
            }
        )
    if plan is not None and not strategy:
        issues.append(
            {
                "kind": "empty_strategy",
                "severity": "high",
                "label": "Planner 策略为空",
                "detail": intent_name,
                "recommendation": "Planner 必须给出 strategy；Capability Router 只执行策略，不负责猜测策略。",
            }
        )
    if plan is not None and not sources and strategy not in {"smalltalk", "action_trace"}:
        issues.append(
            {
                "kind": "empty_sources",
                "severity": "high",
                "label": "Planner 来源为空",
                "detail": strategy,
                "recommendation": "Planner 必须声明 sources，体现问题到知识来源的映射。",
            }
        )
    if question_type == "action" and not strategy:
        issues.append(
            {
                "kind": "action_without_strategy",
                "severity": "critical",
                "label": "Action 缺少执行策略",
                "detail": intent_name,
                "recommendation": "Action 必须明确 strategy，再进入权限检查和确认卡流程。",
            }
        )
    if question_type and question_type not in {"query", "analysis", "insight", "decision", "action"}:
        issues.append(
            {
                "kind": "invalid_question_type",
                "severity": "high",
                "label": "问题类型不在 V5 枚举内",
                "detail": question_type,
                "recommendation": "question_type 只能是 query、analysis、insight、decision、action。",
            }
        )
    if data_scope and data_scope not in {"self", "person", "department", "company", "project", "organization", "external"}:
        issues.append(
            {
                "kind": "invalid_data_scope",
                "severity": "high",
                "label": "数据范围不在 V5 枚举内",
                "detail": data_scope,
                "recommendation": "data_scope 必须使用 V5 定义的范围枚举，避免权限和视图生成失真。",
            }
        )
    return {
        "status": "healthy" if not issues else "needs_attention",
        "label": "意图与规划自洽" if not issues else "意图与规划需要关注",
        "issue_count": len(issues),
        "question_type": question_type,
        "intent": intent_name,
        "data_scope": data_scope,
        "strategy": strategy,
        "sources": list(sources),
        "confidence": confidence,
        "needs_clarification": needs_clarification,
        "should_clarify": should_clarify,
        "missing_params": list(missing_params),
        "execution_status": execution_status,
        "issues": issues[:8],
    }


def _permission_health(envelope: Any, permission: dict[str, Any]) -> dict[str, Any]:
    intent = getattr(envelope, "intent", None)
    question_type = str(getattr(intent, "question_type", "") or "")
    issues: list[dict[str, Any]] = []
    if not permission.get("available"):
        issues.append(
            {
                "kind": "missing_permission_decision",
                "severity": "high",
                "label": "缺少权限决策",
                "detail": question_type or "unknown",
                "recommendation": "V5 主链路必须经过 Permission Check，禁止 Router 或 Provider 自行决定权限。",
            }
        )
    execution_identity = str(permission.get("execution_identity") or "")
    default_identity = str(permission.get("default_identity") or "")
    requested_identity = str(permission.get("requested_identity") or "")
    identity_override = bool(permission.get("identity_override"))
    if execution_identity and execution_identity not in {"bot", "user"}:
        issues.append(
            {
                "kind": "invalid_execution_identity",
                "severity": "high",
                "label": "执行身份不合法",
                "detail": execution_identity,
                "recommendation": "执行身份只能是 bot 或 user。",
            }
        )
    if default_identity and execution_identity and execution_identity != default_identity and not identity_override:
        issues.append(
            {
                "kind": "identity_mismatch_without_override",
                "severity": "high",
                "label": "执行身份与默认策略不一致",
                "detail": f"default={default_identity}, actual={execution_identity}",
                "recommendation": "如果需要覆盖默认身份，必须在 Intent entities 中显式记录 requested execution_identity。",
            }
        )
    if identity_override and requested_identity not in {"bot", "user"}:
        issues.append(
            {
                "kind": "invalid_identity_override",
                "severity": "high",
                "label": "执行身份覆盖值不合法",
                "detail": requested_identity,
                "recommendation": "requested execution_identity 只能是 bot 或 user。",
            }
        )
    if question_type == "action" and execution_identity != "user":
        issues.append(
            {
                "kind": "action_identity_not_user",
                "severity": "critical",
                "label": "Action 执行身份不是用户",
                "detail": execution_identity or "空",
                "recommendation": "Action 默认必须 As User 执行，除非显式定义为后台系统动作。",
            }
        )
    if question_type in {"query", "analysis", "insight", "decision"} and execution_identity not in {"bot", ""}:
        issues.append(
            {
                "kind": "read_identity_not_bot",
                "severity": "medium",
                "label": "只读类问题执行身份异常",
                "detail": execution_identity,
                "recommendation": "Query/Analysis/Insight/Decision 默认 As Bot，避免不必要地使用用户身份读取。",
            }
        )
    if question_type == "action" and permission.get("allowed") and not permission.get("requires_confirmation"):
        issues.append(
            {
                "kind": "action_confirmation_missing",
                "severity": "critical",
                "label": "Action 缺少二次确认",
                "detail": str(getattr(getattr(envelope, "plan", None), "strategy", "") or ""),
                "recommendation": "所有写入/发送/审批/会议/任务/修改数据动作都必须先确认。",
            }
        )
    if permission.get("requires_confirmation") and not permission.get("confirmation_reasons"):
        issues.append(
            {
                "kind": "confirmation_reason_missing",
                "severity": "medium",
                "label": "确认缺少原因",
                "detail": str(getattr(getattr(envelope, "plan", None), "strategy", "") or ""),
                "recommendation": "确认卡应说明为什么需要确认，例如写入、高风险动作、替用户执行。",
            }
        )
    return {
        "status": "healthy" if not issues else "needs_attention",
        "label": "权限与执行身份正常" if not issues else "权限与执行身份需要关注",
        "issue_count": len(issues),
        "execution_identity": execution_identity,
        "default_identity": default_identity,
        "requested_identity": requested_identity,
        "identity_policy": permission.get("identity_policy", ""),
        "identity_override": identity_override,
        "issues": issues[:8],
    }


def _runtime_repair_plan(
    *,
    runtime_gate: dict[str, Any],
    action_closure: dict[str, Any],
    result_context_quality: dict[str, Any],
    pipeline_health: dict[str, Any],
    provider_summary: dict[str, Any],
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for item in runtime_gate.get("priority_items") or []:
        if isinstance(item, dict):
            candidates.append(
                {
                    "source": "runtime_gate",
                    "severity": item.get("severity") or "medium",
                    "label": item.get("label") or item.get("key") or "运行门禁问题",
                    "detail": item.get("detail") or "",
                    "next_step": item.get("recommendation") or "",
                }
            )
    if action_closure.get("latest_pending"):
        candidates.append(
            {
                "source": "action_closure",
                "severity": "medium",
                "label": "最近动作仍在处理中",
                "detail": action_closure.get("latest_label") or "",
                "next_step": "如果持续无结果，优先检查 worker 队列、确认卡回调和 action_receipt 保存。",
            }
        )
    if action_closure.get("contract_issue"):
        candidates.append(
            {
                "source": "action_closure_contract",
                "severity": action_closure.get("contract_severity") or "medium",
                "label": action_closure.get("contract_label") or "动作闭环契约异常",
                "detail": action_closure.get("contract_issue") or "",
                "next_step": action_closure.get("contract_next_step") or "检查动作追踪、终态写入和 Result Context 回执关联。",
            }
        )
    if int(result_context_quality.get("issue_count") or 0) > 0:
        candidates.append(
            {
                "source": "result_context_quality",
                "severity": "high",
                "label": "先修 Result Context 质量",
                "detail": f"{result_context_quality.get('issue_count', 0)} 项问题",
                "next_step": "优先保证 query_id、context_kind、items/count、sources、execution_status 完整。",
            }
        )
    if int(pipeline_health.get("issue_count") or 0) > 0:
        candidates.append(
            {
                "source": "pipeline_health",
                "severity": "high",
                "label": "先修 Pipeline 阶段完整性",
                "detail": f"{pipeline_health.get('issue_count', 0)} 项问题",
                "next_step": "优先保证 V5 主链路八个阶段都有可观测输入输出。",
            }
        )
    recommendations = provider_summary.get("recommendations") if isinstance(provider_summary.get("recommendations"), list) else []
    provider_quality_issues = provider_summary.get("quality_issues") if isinstance(provider_summary.get("quality_issues"), list) else []
    provider_failure_summary = provider_summary.get("failure_summary") if isinstance(provider_summary.get("failure_summary"), dict) else {}
    provider_failures = provider_failure_summary.get("items") if isinstance(provider_failure_summary.get("items"), list) else []
    for item in provider_failures[:3]:
        if not isinstance(item, dict):
            continue
        candidates.append(
            {
                "source": "provider_failure",
                "severity": "high" if item.get("category") in {"permission_or_auth", "execution_failed", "provider_missing"} else "medium",
                "label": f"Provider 失败定位：{item.get('source') or 'unknown'}",
                "detail": str(item.get("operation") or item.get("category_label") or "").strip(),
                "next_step": str(item.get("next_step") or "").strip() or "按失败分类补齐 Provider 能力、权限或参数。",
            }
        )
    for item in provider_quality_issues[:3]:
        if not isinstance(item, dict):
            continue
        candidates.append(
            {
                "source": "provider_quality",
                "severity": item.get("severity") or "medium",
                "label": item.get("label") or "Provider 结果质量问题",
                "detail": item.get("detail") or "",
                "next_step": item.get("recommendation") or "补齐 ProviderResult 的结构化字段和回执信息。",
            }
        )
    for item in recommendations[:3]:
        if not isinstance(item, dict):
            continue
        next_step = str(item.get("recommended_next_step") or "").strip()
        if not next_step:
            continue
        candidates.append(
            {
                "source": "provider",
                "severity": "medium",
                "label": f"Provider 修复：{item.get('source') or 'unknown'}",
                "detail": str(item.get("operation") or item.get("error_type") or "").strip(),
                "next_step": next_step,
            }
        )
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in sorted(candidates, key=lambda payload: severity_order.get(str(payload.get("severity") or "medium"), 2)):
        key = (str(item.get("source") or ""), str(item.get("label") or ""), str(item.get("detail") or ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(_with_issue_fingerprint(item))
    return {
        "status": "healthy" if not deduped else "needs_attention",
        "item_count": len(deduped),
        "primary_issue_fingerprint": deduped[0].get("fingerprint", "") if deduped else "",
        "primary_issue_source": deduped[0].get("source", "") if deduped else "",
        "severity_counts": _severity_counts(deduped),
        "items": deduped[:6],
        "next_step": deduped[0].get("next_step", "") if deduped else "主链路暂无明显修复项，可以继续接业务 Provider 或做端到端验证。",
    }


def _severity_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for item in items:
        severity = str(item.get("severity") or "medium")
        if severity not in counts:
            severity = "medium"
        counts[severity] += 1
    return counts


def _with_issue_fingerprint(item: dict[str, Any]) -> dict[str, Any]:
    payload = dict(item)
    if payload.get("fingerprint"):
        return payload
    basis = "|".join(
        str(payload.get(key) or "")
        for key in ("key", "source", "severity", "label", "detail")
    )
    payload["fingerprint"] = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:10]
    return payload


def _status_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"healthy": 0, "needs_attention": 0, "blocked": 0, "none": 0, "unknown": 0}
    for item in items:
        status = str(item.get("status") or "unknown")
        if status not in counts:
            status = "unknown"
        counts[status] += 1
    return counts


def _result_context_quality(result_context: Any) -> dict[str, Any]:
    if result_context is None:
        return {
            "available": False,
            "status": "none",
            "label": "无 Result Context",
            "issue_count": 0,
            "issues": [],
        }
    metadata = getattr(result_context, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    items = tuple(item for item in (getattr(result_context, "items", ()) or ()) if isinstance(item, dict))
    count = int(getattr(result_context, "count", 0) or 0)
    answer = str(getattr(result_context, "answer", "") or "")
    raw_answer_detected = _looks_like_raw_payload(answer)
    context_kind = str(metadata.get("context_kind") or "")
    saved_at = str(metadata.get("saved_at") or "")
    expires_at = str(metadata.get("expires_at") or "")
    ttl_seconds = int(metadata.get("ttl_seconds") or 0)
    age_seconds = _iso_age_seconds(saved_at)
    expires_in_seconds = _iso_seconds_until(expires_at)
    followup_fields = metadata.get("followup_fields") if isinstance(metadata.get("followup_fields"), list) else []
    item_identity_fields = metadata.get("item_identity_fields") if isinstance(metadata.get("item_identity_fields"), list) else []
    consume_policy = metadata.get("consume_policy") if isinstance(metadata.get("consume_policy"), dict) else {}
    issues: list[dict[str, Any]] = []
    if not str(getattr(result_context, "query_id", "") or "").strip():
        issues.append(
            {
                "kind": "missing_query_id",
                "severity": "medium",
                "label": "缺少上下文编号",
                "detail": str(getattr(result_context, "result_type", "") or ""),
                "recommendation": "保存 Result Context 时必须写入 query_id，便于追踪和失效判断。",
            }
        )
    if context_kind not in {"query_result", "action_receipt", "pending_confirmation", "no_result"}:
        issues.append(
            {
                "kind": "invalid_context_kind",
                "severity": "high",
                "label": "上下文类型不标准",
                "detail": context_kind or "空",
                "recommendation": "Result Context 只允许 query_result、action_receipt、pending_confirmation、no_result 四类。",
            }
        )
    if context_kind != "no_result" and count > 0 and not items:
        issues.append(
            {
                "kind": "count_without_items",
                "severity": "high",
                "label": "有数量但缺少结构化条目",
                "detail": f"count={count}, items=0",
                "recommendation": "Follow-up 必须消费 items，禁止只保存 answer 或 count。",
            }
        )
    if raw_answer_detected:
        issues.append(
            {
                "kind": "raw_answer_payload",
                "severity": "high",
                "label": "Result Context answer 疑似原始技术载荷",
                "detail": str(getattr(result_context, "result_type", "") or ""),
                "recommendation": "Result Context 的 answer 只用于展示摘要；原始 JSON、HTTP error、接口返回体必须放入 items/metadata，并确保追问优先消费 items。",
            }
        )
    if context_kind != "no_result" and items and count == 0:
        issues.append(
            {
                "kind": "items_without_count",
                "severity": "medium",
                "label": "有条目但数量为 0",
                "detail": f"items={len(items)}, count={count}",
                "recommendation": "count 应与结构化 items 的可展示数量一致，避免状态页和追问判断失真。",
            }
        )
    if context_kind == "query_result" and not (metadata.get("result_sources") or metadata.get("sources")):
        issues.append(
            {
                "kind": "missing_sources",
                "severity": "medium",
                "label": "查询结果缺少来源",
                "detail": str(getattr(result_context, "result_type", "") or ""),
                "recommendation": "查询结果应记录 result_sources，便于解释 Planner 到 Provider 的执行路径。",
            }
        )
    if context_kind in {"query_result", "action_receipt", "pending_confirmation"} and items and not followup_fields:
        issues.append(
            {
                "kind": "missing_followup_fields",
                "severity": "medium",
                "label": "缺少可追问字段",
                "detail": str(getattr(result_context, "result_type", "") or ""),
                "recommendation": "Result Context 应记录 followup_fields，明确后续能按序号、姓名、标题、金额、状态等字段追问。",
            }
        )
    if context_kind in {"query_result", "action_receipt", "pending_confirmation"} and items and not item_identity_fields:
        issues.append(
            {
                "kind": "missing_item_identity_fields",
                "severity": "low",
                "label": "缺少条目标识字段",
                "detail": str(getattr(result_context, "result_type", "") or ""),
                "recommendation": "结构化条目最好带 id、task_id、instance_code、open_id、url 等标识，便于后续详情和动作精确定位。",
            }
        )
    if not consume_policy:
        issues.append(
            {
                "kind": "missing_consume_policy",
                "severity": "medium",
                "label": "缺少消费策略",
                "detail": str(getattr(result_context, "result_type", "") or ""),
                "recommendation": "Result Context 应记录 consume_policy，明确追问优先消费 items、过期需刷新、是否允许 answer 兜底。",
            }
        )
    elif not bool(consume_policy.get("prefer_items", False)):
        issues.append(
            {
                "kind": "consume_policy_not_items_first",
                "severity": "high",
                "label": "消费策略未优先使用 items",
                "detail": str(getattr(result_context, "result_type", "") or ""),
                "recommendation": "V5 追问必须优先消费结构化 items，禁止优先消费 answer 文本。",
            }
        )
    elif context_kind == "query_result" and bool(consume_policy.get("allow_answer_fallback")):
        issues.append(
            {
                "kind": "query_result_allows_answer_fallback",
                "severity": "medium",
                "label": "查询结果允许 answer 兜底",
                "detail": str(getattr(result_context, "result_type", "") or ""),
                "recommendation": "query_result 追问应依赖 items；answer 只用于展示，不应作为结构化追问依据。",
            }
        )
    provider_evidence = metadata.get("provider_evidence") if isinstance(metadata.get("provider_evidence"), dict) else {}
    source_execution_status = metadata.get("source_execution_status") if isinstance(metadata.get("source_execution_status"), dict) else {}
    source_execution_steps = metadata.get("source_execution_steps") if isinstance(metadata.get("source_execution_steps"), list) else []
    execution_status = str(metadata.get("execution_status") or "")
    action_receipt_executed = context_kind == "action_receipt" and execution_status not in {
        "queued",
        "started",
        "processing",
        "pending",
        "pending_confirmation",
        "confirmation_card_started",
        "confirmation_card_sent",
        "cancelled",
        "stale",
        "stale_cleanup",
        "expired",
    }
    if (context_kind == "query_result" or action_receipt_executed) and metadata.get("source") != "runtime":
        if not provider_evidence:
            issues.append(
                {
                    "kind": "missing_provider_evidence",
                    "severity": "medium",
                    "label": "缺少 Provider 执行证据摘要",
                    "detail": str(getattr(result_context, "result_type", "") or ""),
                    "recommendation": "Result Context 应写入 provider_evidence，记录来源、操作、成功/失败数量和耗时。",
                }
            )
        elif int(provider_evidence.get("provider_count") or 0) <= 0:
            issues.append(
                {
                    "kind": "empty_provider_evidence",
                    "severity": "medium",
                    "label": "Provider 证据摘要为空",
                    "detail": str(getattr(result_context, "result_type", "") or ""),
                    "recommendation": "非 runtime 兜底结果应至少记录一个 Provider 执行证据。",
                }
            )
    if context_kind == "action_receipt" and not str(metadata.get("execution_status") or "").strip():
        issues.append(
            {
                "kind": "action_receipt_missing_status",
                "severity": "high",
                "label": "动作回执缺少执行状态",
                "detail": str(getattr(result_context, "result_type", "") or ""),
                "recommendation": "Action Receipt 必须写入 execution_status，至少区分 success/error/cancelled/stale。",
            }
        )
    if context_kind == "action_receipt" and not str(metadata.get("action_id") or "").strip():
        issues.append(
            {
                "kind": "action_receipt_missing_action_id",
                "severity": "high",
                "label": "动作回执缺少动作编号",
                "detail": str(getattr(result_context, "result_type", "") or ""),
                "recommendation": "Action Receipt 必须写入 action_id，确保按钮确认、后台执行和用户追问能够关联。",
            }
        )
    if context_kind == "action_receipt" and not str(metadata.get("action_status_group") or "").strip():
        issues.append(
            {
                "kind": "action_receipt_missing_status_group",
                "severity": "medium",
                "label": "动作回执缺少状态分组",
                "detail": str(getattr(result_context, "result_type", "") or ""),
                "recommendation": "Action Receipt 应写入 action_status_group，至少区分 terminal、pending、unknown。",
            }
        )
    if context_kind == "pending_confirmation" and not str(metadata.get("action_id") or "").strip():
        issues.append(
            {
                "kind": "pending_confirmation_missing_action_id",
                "severity": "high",
                "label": "待确认上下文缺少确认编号",
                "detail": str(getattr(result_context, "result_type", "") or ""),
                "recommendation": "待确认 Result Context 必须写入 action_id，确保按钮确认、文字确认和后续回执能关联。",
            }
        )
    write_contract = metadata.get("write_confirmation_contract") if isinstance(metadata.get("write_confirmation_contract"), dict) else {}
    if (
        context_kind == "pending_confirmation"
        and bool(write_contract.get("requires_confirmation_token"))
        and not str(metadata.get("confirmation_token") or "").strip()
    ):
        issues.append(
            {
                "kind": "pending_confirmation_missing_confirmation_token",
                "severity": "high",
                "label": "待确认上下文缺少确认令牌",
                "detail": str(getattr(result_context, "result_type", "") or ""),
                "recommendation": "写操作待确认上下文必须写入 confirmation_token，并与 action_id 或后台确认记录保持一致。",
            }
        )
    if context_kind == "no_result" and not str(metadata.get("empty_reason") or "").strip():
        issues.append(
            {
                "kind": "no_result_missing_reason",
                "severity": "medium",
                "label": "空结果缺少原因",
                "detail": str(getattr(result_context, "result_type", "") or ""),
                "recommendation": "no_result 必须写入 empty_reason 和 recommended_next_step，避免用户只看到空回复。",
            }
        )
    if not saved_at or not expires_at or ttl_seconds <= 0:
        issues.append(
            {
                "kind": "missing_ttl_metadata",
                "severity": "medium",
                "label": "Result Context 缺少 TTL 元数据",
                "detail": str(getattr(result_context, "result_type", "") or ""),
                "recommendation": "Result Context 必须写入 saved_at、expires_at、ttl_seconds，明确它是短期结构化结果缓存。",
            }
        )
    elif expires_in_seconds <= 60:
        issues.append(
            {
                "kind": "result_context_expiring_soon",
                "severity": "low",
                "label": "Result Context 即将过期",
                "detail": f"{expires_in_seconds} 秒",
                "recommendation": "如果用户继续追问但上下文已过期，应重新查询而不是消费旧 answer。",
            }
        )
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    issues = sorted(issues, key=lambda item: severity_order.get(str(item.get("severity") or "medium"), 2))
    consume_priority = _result_context_consume_priority(
        context_kind=context_kind,
        item_count=len(items),
        followup_fields=followup_fields,
        item_identity_fields=item_identity_fields,
        consume_policy=consume_policy,
        raw_answer_detected=raw_answer_detected,
        uses_previous_result=True,
        issues=issues,
    )
    return {
        "available": True,
        "status": "healthy" if not issues else "needs_attention",
        "label": "Result Context 质量正常" if not issues else "Result Context 需要修复",
        "issue_count": len(issues),
        "context_kind": context_kind,
        "result_type": getattr(result_context, "result_type", ""),
        "count": count,
        "item_count": len(items),
        "items_normalized": bool(metadata.get("items_normalized")),
        "index_base": int(metadata.get("index_base") or 0),
        "answer_chars": len(answer.strip()),
        "raw_answer_detected": raw_answer_detected,
        "provider_evidence_available": bool(provider_evidence),
        "provider_evidence_count": int(provider_evidence.get("provider_count") or 0) if provider_evidence else 0,
        "followup_fields": followup_fields,
        "followup_field_count": len(followup_fields),
        "item_identity_fields": item_identity_fields,
        "item_identity_field_count": len(item_identity_fields),
        "consume_policy": consume_policy,
        "prefer_items": bool(consume_policy.get("prefer_items", False)) if consume_policy else False,
        "allow_answer_fallback": bool(consume_policy.get("allow_answer_fallback", False)) if consume_policy else False,
        "consume_order": consume_priority["consume_order"],
        "primary_consume_source": consume_priority["primary_consume_source"],
        "answer_fallback_status": consume_priority["answer_fallback_status"],
        "answer_fallback_label": consume_priority["answer_fallback_label"],
        "repair_queue_count": consume_priority["repair_queue_count"],
        "repair_queue": consume_priority["repair_queue"],
        "top_repair_item": consume_priority["top_repair_item"],
        "saved_at": saved_at,
        "expires_at": expires_at,
        "ttl_seconds": ttl_seconds,
        "age_seconds": age_seconds,
        "expires_in_seconds": expires_in_seconds,
        "issues": issues[:8],
    }


def _pipeline_health(pipeline_frames: list[dict[str, Any]]) -> dict[str, Any]:
    frames = [frame for frame in pipeline_frames if isinstance(frame, dict)]
    by_name = {str(frame.get("name") or ""): frame for frame in frames}
    required = (
        "pre_gateway",
        "result_followup_detector",
        "intent_recognition",
        "task_planner",
        "permission_check",
        "capability_router",
        "execution",
        "answer_composer",
    )
    issues: list[dict[str, Any]] = []
    for stage in required:
        if stage not in by_name:
            issues.append(
                {
                    "kind": "missing_stage",
                    "stage": stage,
                    "severity": "high",
                    "label": "Pipeline 阶段缺失",
                    "detail": stage,
                    "recommendation": "V5 主链路必须保留完整阶段快照，便于定位入口、规划、权限、执行和答案组织问题。",
                }
            )
            continue
        status = str(by_name[stage].get("status") or "")
        if status in {"missing", "empty", "not_routed"}:
            severity = "high" if stage in {"intent_recognition", "task_planner", "answer_composer"} else "medium"
            issues.append(
                {
                    "kind": "bad_stage_status",
                    "stage": stage,
                    "severity": severity,
                    "label": "Pipeline 阶段未完成",
                    "detail": f"{stage}:{status}",
                    "recommendation": "优先检查该阶段输入输出是否符合 V5 Constitution，禁止跳过规划、权限、执行或 Composer。",
                }
            )
    return {
        "status": "healthy" if not issues else "needs_attention",
        "label": "Pipeline 阶段完整" if not issues else "Pipeline 阶段需要关注",
        "stage_count": len(frames),
        "issue_count": len(issues),
        "issues": issues[:8],
    }


def _pipeline_constitution_contract(
    *,
    envelope: Any,
    pipeline_frames: list[dict[str, Any]],
    pipeline_health: dict[str, Any],
    source_execution_contract: dict[str, Any],
    followup_consume_contract: dict[str, Any],
    permission_summary: dict[str, Any],
) -> dict[str, Any]:
    frames = [frame for frame in pipeline_frames if isinstance(frame, dict)]
    names = [str(frame.get("name") or "") for frame in frames]
    expected = [
        "pre_gateway",
        "result_followup_detector",
        "intent_recognition",
        "task_planner",
        "permission_check",
        "capability_router",
        "execution",
        "answer_composer",
    ]
    issues: list[dict[str, Any]] = []
    missing = [stage for stage in expected if stage not in names]
    if missing:
        issues.append(
            {
                "kind": "missing_required_stage",
                "severity": "high",
                "label": "主链路阶段缺失",
                "detail": "、".join(missing),
                "recommendation": "V5 必须保留 Pre Gateway、追问识别、意图、规划、权限、路由执行和答案组织全链路。",
            }
        )
    observed_order = [name for name in names if name in expected]
    if observed_order != [stage for stage in expected if stage in observed_order]:
        issues.append(
            {
                "kind": "stage_order_drift",
                "severity": "high",
                "label": "主链路阶段顺序漂移",
                "detail": " -> ".join(observed_order),
                "recommendation": "Runtime 必须按 V5 Constitution 顺序执行，禁止前置 Tool/Skill 或绕过 Planner/Permission。",
            }
        )
    if int(pipeline_health.get("issue_count") or 0) > 0:
        issues.append(
            {
                "kind": "pipeline_health_issue",
                "severity": "medium",
                "label": "Pipeline 健康检查存在问题",
                "detail": f"{pipeline_health.get('issue_count', 0)} 项",
                "recommendation": "先修复 Pipeline 阶段缺失、空输出或未路由问题。",
            }
        )
    if source_execution_contract.get("status") == "needs_attention":
        missing_sources = source_execution_contract.get("missing_sources") if isinstance(source_execution_contract.get("missing_sources"), list) else []
        issues.append(
            {
                "kind": "planner_sources_not_executed",
                "severity": "high",
                "label": "Planner 来源未被 Router 完整执行",
                "detail": "、".join(str(source) for source in missing_sources[:6]),
                "recommendation": "Planner 只决定 sources；Capability Router 必须按 sources 记录 ProviderResult，不能静默跳过。",
            }
        )
    if followup_consume_contract.get("status") == "needs_attention":
        issues.append(
            {
                "kind": "followup_consume_contract_issue",
                "severity": "medium",
                "label": "Result Follow-up 消费契约异常",
                "detail": f"{followup_consume_contract.get('issue_count', 0)} 项",
                "recommendation": "追问必须优先消费 Result Context.items，禁止把 answer 当缓存使用。",
            }
        )
    intent = getattr(envelope, "intent", None)
    question_type = str(getattr(intent, "question_type", "") or "")
    if question_type == "action" and bool(permission_summary.get("allowed")) and not bool(permission_summary.get("requires_confirmation")):
        issues.append(
            {
                "kind": "action_without_confirmation",
                "severity": "critical",
                "label": "Action 缺少确认门禁",
                "detail": str(getattr(getattr(envelope, "plan", None), "strategy", "") or ""),
                "recommendation": "所有发送、写入、审批、会议、任务修改都必须先经过 Permission Check 和确认卡。",
            }
        )
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    issues = sorted(issues, key=lambda item: severity_order.get(str(item.get("severity") or "medium"), 2))
    return {
        "status": "healthy" if not issues else "needs_attention",
        "label": "V5 主链路契约完整" if not issues else "V5 主链路契约需要关注",
        "expected_stages": expected,
        "observed_stages": names,
        "missing_stages": missing,
        "stage_count": len(frames),
        "issue_count": len(issues),
        "issues": issues[:8],
    }


def _timing_health(envelope: Any, provider_summary: dict[str, Any]) -> dict[str, Any]:
    composed = getattr(envelope, "composed", None)
    pipeline_timing = _composed_metadata_value(composed, "pipeline_timing", {})
    pipeline_timing = pipeline_timing if isinstance(pipeline_timing, dict) else {}
    try:
        total_ms = int(pipeline_timing.get("total_ms") or 0)
        slowest_ms = int(pipeline_timing.get("slowest_ms") or 0)
    except (TypeError, ValueError):
        total_ms = 0
        slowest_ms = 0
    slowest_stage = str(pipeline_timing.get("slowest_stage") or "")
    provider_total_ms = int(provider_summary.get("total_duration_ms") or 0)
    slowest_provider = provider_summary.get("slowest_provider") if isinstance(provider_summary.get("slowest_provider"), dict) else {}
    try:
        slowest_provider_ms = int(slowest_provider.get("duration_ms") or 0)
    except (TypeError, ValueError):
        slowest_provider_ms = 0
    issues: list[dict[str, Any]] = []
    if total_ms >= 30000:
        issues.append(
            {
                "kind": "runtime_very_slow",
                "severity": "high",
                "label": "本轮总耗时过高",
                "detail": f"{total_ms}ms",
                "recommendation": "优先拆分慢阶段和慢 Provider；超过 30 秒的链路应考虑先快速回复，再异步补充详情。",
            }
        )
    elif total_ms >= 8000:
        issues.append(
            {
                "kind": "runtime_slow",
                "severity": "medium",
                "label": "本轮总耗时偏高",
                "detail": f"{total_ms}ms",
                "recommendation": "优先定位最慢阶段；如果是 execution/capability_router，继续看 Provider 耗时和外部 API。",
            }
        )
    if slowest_ms >= 10000:
        issues.append(
            {
                "kind": "slow_stage",
                "severity": "high",
                "label": "单阶段耗时过高",
                "detail": f"{slowest_stage or 'unknown'} {slowest_ms}ms",
                "recommendation": _timing_stage_recommendation(slowest_stage),
            }
        )
    if provider_total_ms >= 8000:
        issues.append(
            {
                "kind": "provider_total_slow",
                "severity": "medium",
                "label": "Provider 总耗时偏高",
                "detail": f"{provider_total_ms}ms",
                "recommendation": "优先查看 Provider 来源耗时、飞书 API 响应、附件/OCR、批量写入和缓存命中情况。",
            }
        )
    if slowest_provider_ms >= 5000:
        issues.append(
            {
                "kind": "slow_provider",
                "severity": "medium",
                "label": "单个 Provider 偏慢",
                "detail": f"{slowest_provider.get('source') or 'unknown'} {slowest_provider_ms}ms",
                "recommendation": "慢 Provider 应补缓存、分页/批量优化，或改为先返回快照再异步补齐。",
            }
        )
    return {
        "status": "healthy" if not issues else "needs_attention",
        "label": "耗时正常" if not issues else "耗时需要关注",
        "issue_count": len(issues),
        "total_ms": total_ms,
        "slowest_stage": slowest_stage,
        "slowest_ms": slowest_ms,
        "provider_total_ms": provider_total_ms,
        "slowest_provider_source": str(slowest_provider.get("source") or ""),
        "slowest_provider_ms": slowest_provider_ms,
        "issues": issues[:8],
    }


def _timing_stage_recommendation(stage: str) -> str:
    if stage in {"intent_recognition", "task_planner", "answer_composer"}:
        return "慢点在 LLM/答案组织，优先看提示词长度、重复生成和模型调用耗时。"
    if stage in {"capability_router", "execution"}:
        return "慢点在能力执行，优先看 Provider 耗时、飞书 API、附件/OCR 和批量写入。"
    if stage == "pre_gateway":
        return "慢点在入口上下文加载，优先看 Redis、Profile、Identity 和 Result Context 读取。"
    if stage == "permission_check":
        return "慢点在权限检查，优先看身份解析、公司范围和权限来源聚合。"
    if stage == "result_followup_detector":
        return "慢点在追问识别，优先看 Result Context 体积和序号/指代解析。"
    return "优先结合 Pipeline 帧、Provider 耗时和 trace_id 继续定位。"


def _action_timeline_summary(action_trace: list[dict[str, Any]], result_context_events: list[dict[str, Any]]) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for item in action_trace[-10:]:
        if not isinstance(item, dict):
            continue
        events.append(
            {
                "kind": "action_trace",
                "created_at": str(item.get("created_at") or ""),
                "label": "动作追踪",
                "action_id": str(item.get("action_id") or item.get("correlation_id") or ""),
                "correlation_id": str(item.get("correlation_id") or item.get("action_id") or ""),
                "status": str(item.get("status") or ""),
                "status_group": str(item.get("status_group") or ""),
                "action": str(item.get("action") or ""),
                "strategy": str(item.get("strategy") or ""),
                "source": str(item.get("source") or ""),
                "detail": "｜".join(
                    part
                    for part in (
                        str(item.get("kind") or ""),
                        str(item.get("action") or ""),
                        str(item.get("status") or ""),
                    )
                    if part
                ),
            }
        )
    for item in result_context_events[-10:]:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "")
        context_kind = str(item.get("context_kind") or "")
        operation = str(item.get("operation") or item.get("result_type") or "")
        execution_status = str(item.get("execution_status") or "")
        source = str(item.get("source") or "")
        action_id = str(item.get("action_id") or "")
        events.append(
            {
                "kind": "result_context",
                "created_at": str(item.get("created_at") or ""),
                "label": "结果上下文",
                "action_id": action_id,
                "correlation_id": action_id,
                "status": execution_status or action,
                "status_group": (
                    "terminal"
                    if context_kind == "action_receipt" and execution_status not in {"queued", "started", "processing", "confirmation_card_started", "confirmation_card_sent"}
                    else ("pending" if context_kind == "action_receipt" else "")
                ),
                "action": action,
                "context_kind": context_kind,
                "strategy": operation,
                "source": source,
                "detail": "｜".join(
                    part
                    for part in (
                        action,
                        context_kind,
                        operation,
                        execution_status,
                        source,
                        str(item.get("reason") or ""),
                    )
                    if part
                ),
            }
        )
    events = sorted(events, key=lambda item: str(item.get("created_at") or ""))
    latest = events[-1] if events else {}
    action_events = [item for item in events if item.get("kind") == "action_trace"]
    receipt_events = [
        item
        for item in events
        if item.get("kind") == "result_context" and item.get("context_kind") == "action_receipt"
    ]
    save_events = [item for item in events if item.get("kind") == "result_context" and item.get("action") == "save"]
    clear_events = [item for item in events if item.get("kind") == "result_context" and item.get("action") == "clear"]
    return {
        "available": bool(events),
        "event_count": len(events),
        "action_event_count": len(action_events),
        "result_context_event_count": len(events) - len(action_events),
        "action_receipt_event_count": len(receipt_events),
        "save_event_count": len(save_events),
        "clear_event_count": len(clear_events),
        "latest_kind": latest.get("kind", ""),
        "latest_status": latest.get("status", ""),
        "latest_detail": latest.get("detail", ""),
        "events": events[-12:],
    }


def _action_closure_summary(
    action_trace: list[dict[str, Any]],
    result_context_events: list[dict[str, Any]],
    action_timeline: dict[str, Any],
) -> dict[str, Any]:
    traces = [item for item in action_trace if isinstance(item, dict)]
    latest = traces[-1] if traces else {}
    latest_action_id = str(latest.get("action_id") or latest.get("correlation_id") or "")
    latest_status = str(latest.get("status") or "")
    latest_kind = str(latest.get("kind") or "")
    latest_action = str(latest.get("action") or "")
    pending_statuses = {"queued", "started", "processing", "confirmation_card_started", "confirmation_card_sent"}
    terminal_statuses = {"success", "partial", "error", "failed", "cancelled", "stale", "denied", "confirmation_card_failed"}
    has_action_receipt_event = any(
        isinstance(event, dict)
        and event.get("action") == "save"
        and str(event.get("context_kind") or "") == "action_receipt"
        and (not latest_action_id or str(event.get("action_id") or "") == latest_action_id)
        for event in result_context_events[-8:]
    ) or any(
        isinstance(event, dict)
        and event.get("kind") == "result_context"
        and str(event.get("context_kind") or "") == "action_receipt"
        and (not latest_action_id or str(event.get("action_id") or "") == latest_action_id)
        for event in action_timeline.get("events", [])
    )
    correlated_receipts = [
        event
        for event in result_context_events[-8:]
        if isinstance(event, dict)
        and event.get("action") == "save"
        and str(event.get("context_kind") or "") == "action_receipt"
        and (not latest_action_id or str(event.get("action_id") or "") == latest_action_id)
    ]
    pending_confirmations = [
        event
        for event in result_context_events[-8:]
        if isinstance(event, dict)
        and event.get("action") == "save"
        and str(event.get("context_kind") or "") == "pending_confirmation"
        and (not latest_action_id or str(event.get("action_id") or "") == latest_action_id)
    ]
    latest_receipt = correlated_receipts[-1] if correlated_receipts else {}
    missing_fields = []
    if latest:
        for field in ("kind", "action", "status", "strategy", "sources", "execution_identity", "action_id", "correlation_id", "status_group"):
            value = latest.get(field)
            if value in (None, "", []) or value == ():
                missing_fields.append(field)
    latest_age_seconds = _action_trace_age_seconds(str(latest.get("created_at") or "")) if latest else 0
    contract = _action_closure_contract(
        latest=latest,
        latest_status=latest_status,
        latest_age_seconds=latest_age_seconds,
        latest_action_id=latest_action_id,
        has_action_receipt_event=has_action_receipt_event,
        has_correlated_receipt=bool(has_action_receipt_event and latest_action_id),
        missing_fields=missing_fields,
        pending_statuses=pending_statuses,
        terminal_statuses=terminal_statuses,
    )
    repair_queue = _action_closure_repair_queue(
        latest=latest,
        latest_status=latest_status,
        latest_age_seconds=latest_age_seconds,
        latest_action_id=latest_action_id,
        has_action_receipt_event=has_action_receipt_event,
        has_correlated_receipt=bool(has_action_receipt_event and latest_action_id),
        missing_fields=missing_fields,
        pending_statuses=pending_statuses,
        terminal_statuses=terminal_statuses,
        pending_confirmation_count=len(pending_confirmations),
        latest_receipt=latest_receipt,
    )
    return {
        "latest_available": bool(latest),
        "latest_kind": latest_kind,
        "latest_action_id": latest_action_id,
        "latest_correlation_id": str(latest.get("correlation_id") or latest_action_id),
        "latest_action": latest_action,
        "latest_status": latest_status,
        "latest_status_group": str(latest.get("status_group") or ""),
        "latest_label": "｜".join(part for part in (latest_kind, latest_action, latest_status) if part),
        "latest_age_seconds": latest_age_seconds,
        "latest_stuck": latest_status in pending_statuses and latest_age_seconds >= 60,
        "latest_stuck_severity": "high" if latest_status in pending_statuses and latest_age_seconds >= 180 else "medium",
        "latest_pending": latest_status in pending_statuses,
        "latest_terminal": latest_status in terminal_statuses,
        "pending_count": len([item for item in traces if str(item.get("status") or "") in pending_statuses]),
        "terminal_count": len([item for item in traces if str(item.get("status") or "") in terminal_statuses]),
        "has_action_receipt_event": has_action_receipt_event,
        "has_correlated_receipt": bool(has_action_receipt_event and latest_action_id),
        "correlated_receipt_count": len(correlated_receipts),
        "pending_confirmation_event_count": len(pending_confirmations),
        "latest_receipt_status": str(latest_receipt.get("execution_status") or ""),
        "latest_receipt_status_group": str(latest_receipt.get("action_status_group") or ""),
        "latest_receipt_terminal": bool(latest_receipt.get("is_terminal_action", False)),
        "latest_receipt_pending": bool(latest_receipt.get("is_pending_action", False)),
        "timeline_event_count": int(action_timeline.get("event_count") or 0),
        "timeline_latest_detail": action_timeline.get("latest_detail", ""),
        "missing_fields": missing_fields,
        "missing_field_count": len(missing_fields),
        "contract_status": contract["status"],
        "contract_label": contract["label"],
        "contract_issue": contract["issue"],
        "contract_severity": contract["severity"],
        "contract_next_step": contract["next_step"],
        "repair_queue_count": len(repair_queue),
        "repair_queue": repair_queue[:8],
        "top_repair_item": repair_queue[0] if repair_queue else {},
    }


def _action_closure_repair_queue(
    *,
    latest: dict[str, Any],
    latest_status: str,
    latest_age_seconds: int,
    latest_action_id: str,
    has_action_receipt_event: bool,
    has_correlated_receipt: bool,
    missing_fields: list[str],
    pending_statuses: set[str],
    terminal_statuses: set[str],
    pending_confirmation_count: int,
    latest_receipt: dict[str, Any],
) -> list[dict[str, Any]]:
    if not latest:
        return []
    items: list[dict[str, Any]] = []

    def add(
        *,
        kind: str,
        severity: str,
        label: str,
        detail: str,
        next_step: str,
        score: int,
    ) -> None:
        items.append(
            {
                "kind": kind,
                "severity": severity,
                "label": label,
                "detail": detail,
                "next_step": next_step,
                "priority_score": score,
                "priority_label": "高" if score >= 80 else "中" if score >= 45 else "低",
            }
        )

    if missing_fields:
        add(
            kind="trace_missing_fields",
            severity="medium",
            label="动作追踪字段缺失",
            detail="、".join(missing_fields[:6]),
            next_step="补齐 action_trace 的 action_id、correlation_id、status_group、execution_identity 等字段。",
            score=55,
        )
    if latest_status in {"prepared", "prepare_confirm", "confirmation_card_started", "confirmation_card_sent"} and pending_confirmation_count == 0:
        add(
            kind="pending_confirmation_missing",
            severity="high",
            label="确认卡缺少待确认上下文",
            detail=str(latest.get("action") or latest_status),
            next_step="发送确认卡时同步保存 context_kind=pending_confirmation，并写入 action_id 与 confirmation_token。",
            score=88,
        )
    if latest_status == "confirmation_card_started" and latest_age_seconds >= 30:
        add(
            kind="confirmation_card_not_sent",
            severity="high",
            label="确认卡生成后疑似未发送",
            detail=f"已等待 {latest_age_seconds} 秒",
            next_step="检查飞书卡片发送接口、按钮 action value 和异常回执。",
            score=84,
        )
    if latest_status in pending_statuses and latest_age_seconds >= 180:
        add(
            kind="pending_timeout",
            severity="high",
            label="动作处理中超时",
            detail=f"{latest_status}｜{latest_age_seconds} 秒",
            next_step="检查按钮回调是否入队、worker 是否消费、Provider 是否写入终态。",
            score=92,
        )
    elif latest_status in pending_statuses and latest_age_seconds >= 60:
        add(
            kind="pending_slow",
            severity="medium",
            label="动作处理中偏慢",
            detail=f"{latest_status}｜{latest_age_seconds} 秒",
            next_step="优先看 Provider 耗时、外部 API 响应和 action_receipt 是否延迟保存。",
            score=58,
        )
    if latest_status in terminal_statuses and not has_action_receipt_event:
        add(
            kind="terminal_without_receipt",
            severity="high",
            label="终态缺少动作回执",
            detail=str(latest.get("action") or latest_status),
            next_step="执行成功、失败、取消或过期后都必须保存 context_kind=action_receipt。",
            score=90,
        )
    if latest_status in terminal_statuses and latest_action_id and has_action_receipt_event and not has_correlated_receipt:
        add(
            kind="receipt_uncorrelated",
            severity="medium",
            label="动作回执未关联",
            detail=latest_action_id[-8:],
            next_step="确保 action_trace.action_id、confirmation_token、Result Context action_id 使用同一关联编号。",
            score=62,
        )
    receipt_status = str(latest_receipt.get("execution_status") or "")
    if latest_status in terminal_statuses and bool(latest_receipt.get("is_pending_action")):
        add(
            kind="receipt_pending_but_action_terminal",
            severity="medium",
            label="回执状态仍是处理中",
            detail=receipt_status,
            next_step="保存 action_receipt 时同步写入最终 execution_status 和 action_status_group。",
            score=52,
        )
    severity_order = {"high": 0, "medium": 1, "low": 2}
    return sorted(
        items,
        key=lambda item: (
            -int(item.get("priority_score") or 0),
            severity_order.get(str(item.get("severity") or "medium"), 1),
            str(item.get("kind") or ""),
        ),
    )


def _action_closure_contract(
    *,
    latest: dict[str, Any],
    latest_status: str,
    latest_age_seconds: int,
    latest_action_id: str,
    has_action_receipt_event: bool,
    has_correlated_receipt: bool,
    missing_fields: list[str],
    pending_statuses: set[str],
    terminal_statuses: set[str],
) -> dict[str, str]:
    if not latest:
        return {
            "status": "none",
            "label": "暂无动作",
            "issue": "",
            "severity": "low",
            "next_step": "当前没有动作追踪记录。",
        }
    if missing_fields:
        return {
            "status": "incomplete_trace",
            "label": "动作追踪字段不完整",
            "issue": "missing_fields",
            "severity": "medium",
            "next_step": "补齐 action_trace 的动作、状态、执行身份、关联编号等字段。",
        }
    if latest_status in pending_statuses and latest_age_seconds >= 180:
        return {
            "status": "pending_timeout",
            "label": "动作处理中超时",
            "issue": "pending_without_terminal_status",
            "severity": "high",
            "next_step": "优先检查 worker 队列、飞书回调处理和 Provider 是否没有写入终态。",
        }
    if latest_status in pending_statuses:
        return {
            "status": "pending",
            "label": "动作处理中",
            "issue": "",
            "severity": "low",
            "next_step": "等待 Provider 写入终态和 Result Context 回执。",
        }
    if latest_status in terminal_statuses and not has_action_receipt_event:
        return {
            "status": "terminal_without_receipt",
            "label": "终态缺少动作回执",
            "issue": "result_context_receipt_missing",
            "severity": "high",
            "next_step": "检查执行完成后是否调用 save_result_context，并保存 context_kind=action_receipt。",
        }
    if latest_status in terminal_statuses and latest_action_id and not has_correlated_receipt:
        return {
            "status": "receipt_uncorrelated",
            "label": "动作回执未关联",
            "issue": "action_id_mismatch",
            "severity": "medium",
            "next_step": "确保 action_trace.action_id 与 Result Context 事件中的 action_id 一致。",
        }
    if latest_status in terminal_statuses:
        return {
            "status": "closed",
            "label": "动作已闭环",
            "issue": "",
            "severity": "low",
            "next_step": "动作链路正常，可以继续检查业务结果质量。",
        }
    return {
        "status": "unknown",
        "label": "动作状态未知",
        "issue": "unknown_action_status",
        "severity": "medium",
        "next_step": "规范 action_trace.status，确保能归入处理中或终态。",
    }


def _action_trace_age_seconds(created_at: str) -> int:
    if not created_at:
        return 0
    try:
        value = created_at.replace("Z", "+00:00")
        created = datetime.fromisoformat(value)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return max(int((datetime.now(timezone.utc) - created.astimezone(timezone.utc)).total_seconds()), 0)
    except (TypeError, ValueError):
        return 0


def _iso_age_seconds(value: str) -> int:
    if not value:
        return 0
    try:
        created = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return max(int((datetime.now(timezone.utc) - created.astimezone(timezone.utc)).total_seconds()), 0)
    except (TypeError, ValueError):
        return 0


def _iso_seconds_until(value: str) -> int:
    if not value:
        return 0
    try:
        target = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        return int((target.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return 0


def _permission_summary(permission: Any) -> dict[str, Any]:
    if permission is None:
        return {"available": False}
    metadata = getattr(permission, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    execution_identity = getattr(permission, "execution_identity", "")
    requested_identity = str(metadata.get("requested_identity") or "")
    default_identity = str(metadata.get("default_identity") or "")
    return {
        "available": True,
        "allowed": bool(getattr(permission, "allowed", False)),
        "reason": getattr(permission, "reason", ""),
        "requires_confirmation": bool(getattr(permission, "requires_confirmation", False)),
        "execution_identity": execution_identity,
        "confirmation_reasons": metadata.get("confirmation_reasons", []),
        "source_capabilities": metadata.get("source_capabilities", []),
        "requested_identity": requested_identity,
        "default_identity": default_identity,
        "identity_override": bool(requested_identity and requested_identity != default_identity),
        "identity_policy": "override" if requested_identity and requested_identity != default_identity else "default",
    }


def _decision_summary(envelope: Any, permission: dict[str, Any], provider_summary: dict[str, Any]) -> dict[str, Any]:
    intent = getattr(envelope, "intent", None)
    plan = getattr(envelope, "plan", None)
    execution = getattr(envelope, "execution", None)
    composed = getattr(envelope, "composed", None)
    pipeline_timing = _composed_metadata_value(composed, "pipeline_timing", {})
    return {
        "intent": getattr(intent, "intent", ""),
        "question_type": getattr(intent, "question_type", ""),
        "data_scope": getattr(intent, "data_scope", ""),
        "strategy": getattr(plan, "strategy", ""),
        "sources": list(getattr(plan, "sources", ()) or ()),
        "allowed": bool(permission.get("allowed")),
        "requires_confirmation": bool(permission.get("requires_confirmation")),
        "execution_identity": permission.get("execution_identity", ""),
        "confirmation_reasons": permission.get("confirmation_reasons", []),
        "execution_status": getattr(execution, "status", "not_executed") if execution is not None else "not_executed",
        "provider_success_count": provider_summary.get("success_count", 0),
        "provider_error_count": provider_summary.get("error_count", 0),
        "provider_denied_count": provider_summary.get("denied_count", 0),
        "provider_total_duration_ms": provider_summary.get("total_duration_ms", 0),
        "slowest_provider": provider_summary.get("slowest_provider", {}),
        "pipeline_total_ms": pipeline_timing.get("total_ms", 0) if isinstance(pipeline_timing, dict) else 0,
        "slowest_stage": pipeline_timing.get("slowest_stage", "") if isinstance(pipeline_timing, dict) else "",
        "slowest_ms": pipeline_timing.get("slowest_ms", 0) if isinstance(pipeline_timing, dict) else 0,
    }


def _provider_results_summary(provider_results: list[Any]) -> dict[str, Any]:
    items = []
    for item in provider_results:
        answer = str(getattr(item, "answer", "") or "")
        result_items = tuple(value for value in (getattr(item, "items", ()) or ()) if isinstance(value, dict))
        items.append(
            {
                "source": getattr(item, "source", ""),
                "status": getattr(item, "status", ""),
                "result_type": getattr(item, "result_type", ""),
                "count": getattr(item, "count", 0),
                "item_count": len(result_items),
                "answer_chars": len(answer.strip()),
                "raw_answer_detected": _looks_like_raw_payload(answer),
                "error": getattr(item, "error", ""),
                "operation": _provider_metadata_value(item, "operation", ""),
                "tool_name": _provider_metadata_value(item, "tool_name", ""),
                "duration_ms": int(_provider_metadata_value(item, "duration_ms", 0) or 0),
                "empty_reason": _provider_metadata_value(item, "empty_reason", ""),
                "substeps": _provider_metadata_value(item, "substeps", []),
                "cache_hit": _provider_metadata_value(item, "cache_hit", None),
                "fetch_ms": _provider_metadata_value(item, "fetch_ms", 0),
                "error_type": _provider_metadata_value(item, "error_type", ""),
                "pending_reason": _provider_metadata_value(item, "pending_reason", ""),
                "recommended_next_step": _provider_metadata_value(item, "recommended_next_step", ""),
                "missing_params": _provider_metadata_value(item, "missing_params", []),
                "capability_label": _provider_metadata_value(item, "capability_label", ""),
                "capability_installed": _provider_metadata_value(item, "capability_installed", None),
                "target": _provider_metadata_value(item, "target", ""),
                "target_type": _provider_metadata_value(item, "target_type", ""),
                "target_query": _provider_metadata_value(item, "target_query", ""),
                "resolved_user_id": _provider_metadata_value(item, "resolved_user_id", ""),
                "resolved_chat_id": _provider_metadata_value(item, "resolved_chat_id", ""),
                "resolved_target_name": _provider_metadata_value(item, "resolved_target_name", ""),
            }
        )
    slowest_provider = max(items, key=lambda item: int(item.get("duration_ms") or 0), default={})
    by_source: dict[str, dict[str, Any]] = {}
    recommendations: list[dict[str, Any]] = []
    for item in items:
        source = str(item.get("source") or "unknown")
        payload = by_source.setdefault(
            source,
            {
                "source": source,
                "count": 0,
                "success_count": 0,
                "error_count": 0,
                "denied_count": 0,
                "skipped_count": 0,
                "total_duration_ms": 0,
                "operations": [],
            },
        )
        status = str(item.get("status") or "")
        payload["count"] += 1
        if status == "success":
            payload["success_count"] += 1
        elif status == "error":
            payload["error_count"] += 1
        elif status == "denied":
            payload["denied_count"] += 1
        elif status == "skipped":
            payload["skipped_count"] += 1
        payload["total_duration_ms"] += int(item.get("duration_ms") or 0)
        operation = str(item.get("operation") or "").strip()
        if operation and operation not in payload["operations"]:
            payload["operations"].append(operation)
        recommended_next_step = str(item.get("recommended_next_step") or "").strip()
        pending_reason = str(item.get("pending_reason") or "").strip()
        error_type = str(item.get("error_type") or "").strip()
        if recommended_next_step or pending_reason or error_type:
            recommendations.append(
                {
                    "source": source,
                    "operation": operation,
                    "status": item.get("status", ""),
                    "error_type": error_type,
                    "pending_reason": pending_reason,
                    "recommended_next_step": recommended_next_step,
                }
            )
    quality_issues = _provider_result_quality_issues(items)
    failure_summary = _provider_failure_summary(items)
    evidence_contract = _provider_evidence_contract(items, quality_issues, failure_summary)
    latency_diagnostics = _provider_latency_diagnostics(items)
    return {
        "items": items,
        "count": len(items),
        "success_count": len([item for item in items if item.get("status") == "success"]),
        "error_count": len([item for item in items if item.get("status") == "error"]),
        "denied_count": len([item for item in items if item.get("status") == "denied"]),
        "skipped_count": len([item for item in items if item.get("status") == "skipped"]),
        "total_duration_ms": sum(int(item.get("duration_ms") or 0) for item in items),
        "slowest_provider": slowest_provider,
        "by_source": sorted(by_source.values(), key=lambda item: int(item.get("total_duration_ms") or 0), reverse=True),
        "recommendations": recommendations[:8],
        "quality_issues": quality_issues,
        "quality_issue_count": len(quality_issues),
        "failure_summary": failure_summary,
        "evidence_contract": evidence_contract,
        "latency_diagnostics": latency_diagnostics,
    }


def _provider_failure_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    categories = {
        "missing_params": 0,
        "permission_or_auth": 0,
        "not_open": 0,
        "execution_failed": 0,
        "provider_missing": 0,
        "empty_result": 0,
        "unknown": 0,
    }
    details: list[dict[str, Any]] = []
    for item in items:
        category = _provider_failure_category(item)
        if not category:
            continue
        categories[category] = categories.get(category, 0) + 1
        detail = {
            "source": item.get("source", ""),
            "operation": item.get("operation", ""),
            "status": item.get("status", ""),
            "category": category,
            "category_label": _provider_failure_category_label(category),
            "error_type": item.get("error_type", ""),
            "reason": _provider_failure_reason(item, category),
            "next_step": _provider_failure_next_step(item, category),
        }
        details.append(detail)
    blocking_count = len(
        [
            item
            for item in details
            if str(item.get("status") or "") in {"error", "denied"}
            or str(item.get("category") or "") in {"missing_params", "permission_or_auth", "not_open", "execution_failed", "provider_missing"}
        ]
    )
    return {
        "issue_count": len(details),
        "blocking_count": blocking_count,
        "categories": {key: value for key, value in categories.items() if value},
        "items": details[:8],
    }


def _provider_latency_diagnostics(items: list[dict[str, Any]]) -> dict[str, Any]:
    provider_threshold_ms = 1500
    substep_threshold_ms = 800
    fetch_threshold_ms = 1000
    slow_items: list[dict[str, Any]] = []
    slow_substeps: list[dict[str, Any]] = []
    cache_rows: list[dict[str, Any]] = []
    for item in items:
        source = str(item.get("source") or "")
        operation = str(item.get("operation") or "")
        duration_ms = int(item.get("duration_ms") or 0)
        fetch_ms = int(item.get("fetch_ms") or 0)
        cache_hit = item.get("cache_hit")
        if cache_hit is not None:
            cache_rows.append({"source": source, "operation": operation, "cache_hit": bool(cache_hit)})
        if duration_ms >= provider_threshold_ms or fetch_ms >= fetch_threshold_ms:
            slow_items.append(
                {
                    "kind": "slow_provider",
                    "source": source,
                    "operation": operation,
                    "duration_ms": duration_ms,
                    "fetch_ms": fetch_ms,
                    "cache_hit": cache_hit,
                    "status": item.get("status", ""),
                    "label": "Provider 调用较慢",
                    "reason": _provider_latency_reason(source, operation, duration_ms, fetch_ms, cache_hit),
                    "next_step": _provider_latency_next_step(source, operation, cache_hit),
                    "priority_score": duration_ms + fetch_ms,
                }
            )
        substeps = item.get("substeps") if isinstance(item.get("substeps"), list) else []
        for substep in substeps:
            if not isinstance(substep, dict):
                continue
            step_ms = int(substep.get("duration_ms") or 0)
            if step_ms < substep_threshold_ms:
                continue
            slow_substeps.append(
                {
                    "kind": "slow_substep",
                    "source": source,
                    "operation": operation,
                    "step": str(substep.get("step") or ""),
                    "duration_ms": step_ms,
                    "status": str(substep.get("status") or ""),
                    "count": int(substep.get("count") or substep.get("row_count") or 0),
                    "label": "Provider 子步骤较慢",
                    "reason": f"{substep.get('step') or 'substep'} 耗时 {step_ms}ms",
                    "next_step": _provider_substep_latency_next_step(source, operation, str(substep.get("step") or "")),
                    "priority_score": step_ms,
                }
            )
    queue = sorted(
        [*slow_items, *slow_substeps],
        key=lambda row: int(row.get("priority_score") or 0),
        reverse=True,
    )
    cache_known_count = len(cache_rows)
    cache_hit_count = len([row for row in cache_rows if row.get("cache_hit")])
    total_duration_ms = sum(int(item.get("duration_ms") or 0) for item in items)
    return {
        "status": "healthy" if not queue else "needs_attention",
        "label": "Provider 耗时正常" if not queue else "Provider 存在慢调用",
        "total_duration_ms": total_duration_ms,
        "provider_threshold_ms": provider_threshold_ms,
        "substep_threshold_ms": substep_threshold_ms,
        "fetch_threshold_ms": fetch_threshold_ms,
        "slow_provider_count": len(slow_items),
        "slow_substep_count": len(slow_substeps),
        "slow_count": len(queue),
        "cache_known_count": cache_known_count,
        "cache_hit_count": cache_hit_count,
        "cache_miss_count": cache_known_count - cache_hit_count,
        "top_slow_item": queue[0] if queue else {},
        "queue": queue[:10],
    }


def _provider_latency_reason(source: str, operation: str, duration_ms: int, fetch_ms: int, cache_hit: Any) -> str:
    if cache_hit is False:
        return f"缓存未命中，Provider 耗时 {duration_ms}ms"
    if fetch_ms >= 1000:
        return f"外部拉取耗时 {fetch_ms}ms"
    if source == "approval":
        return "审批详情、附件或 AI 判断可能拖慢响应"
    if source == "people":
        return "组织架构或人员快照读取可能拖慢响应"
    if source in {"base", "sheets"}:
        return "表格创建、字段写入或批量写入可能拖慢响应"
    return f"{source}:{operation} 耗时 {duration_ms}ms"


def _provider_latency_next_step(source: str, operation: str, cache_hit: Any) -> str:
    if cache_hit is False:
        return "优先检查是否可以复用 Result Context、people snapshot 或短 TTL 缓存。"
    if source == "approval":
        return "列表先快速返回摘要，详情/附件/LLM 建议异步或按需展开。"
    if source == "people":
        return "优先使用组织架构快照缓存，避免每轮全量拉取。"
    if source in {"base", "sheets"}:
        return "把创建文件、创建表、批量写入拆成可观测 substep，并考虑后台执行加回执。"
    return "检查 Provider substeps、外部 API 和是否可缓存。"


def _provider_substep_latency_next_step(source: str, operation: str, step: str) -> str:
    if "attachment" in step:
        return "附件读取建议按详情触发，列表只保留摘要或风险等级。"
    if "ai" in step or "llm" in step:
        return "LLM 判断建议批量一次调用或后台异步生成。"
    if "record" in step or "batch" in step:
        return "批量写入建议后台执行并保存 action_receipt。"
    if "detail" in step:
        return "详情读取建议按需展开，列表阶段避免逐条深读。"
    return "检查该子步骤是否可缓存、分页或异步化。"


def _fast_response_contract(
    envelope: Any,
    provider_summary: dict[str, Any],
    action_closure: dict[str, Any],
    timing_health: dict[str, Any],
) -> dict[str, Any]:
    intent = getattr(envelope, "intent", None)
    plan = getattr(envelope, "plan", None)
    permission = getattr(envelope, "permission", None)
    strategy = str(getattr(plan, "strategy", "") or "")
    question_type = str(getattr(intent, "question_type", "") or "")
    sources = [str(source) for source in (getattr(plan, "sources", ()) or ()) if str(source)]
    total_ms = int(timing_health.get("total_ms") or provider_summary.get("total_duration_ms") or 0)
    provider_total_ms = int(provider_summary.get("total_duration_ms") or 0)
    latency = provider_summary.get("latency_diagnostics") if isinstance(provider_summary.get("latency_diagnostics"), dict) else {}
    slow_count = int(latency.get("slow_count") or 0)
    requires_confirmation = bool(getattr(permission, "requires_confirmation", False))
    is_action = question_type == "action" or requires_confirmation
    long_running_strategy = strategy in {
        "organization_export",
        "approval_query",
        "approval_detail",
        "base_create",
        "sheets_create",
        "sheets_write",
        "task_upload_attachment",
    }
    long_running_source = bool({"approval", "people", "base", "sheets", "drive"}.intersection(sources))
    fast_ack_threshold_ms = 1500
    async_threshold_ms = 5000
    fast_ack_required = total_ms >= fast_ack_threshold_ms or slow_count > 0 or long_running_strategy
    async_recommended = total_ms >= async_threshold_ms or (is_action and (long_running_strategy or slow_count > 0)) or strategy == "organization_export"
    progress_receipt_required = async_recommended or is_action
    has_receipt = bool(action_closure.get("has_action_receipt_event"))
    issues: list[dict[str, Any]] = []
    if fast_ack_required:
        issues.append(
            {
                "kind": "fast_ack_required",
                "severity": "medium" if total_ms < async_threshold_ms else "high",
                "label": "需要快速首包",
                "detail": f"总耗时 {total_ms}ms，Provider {provider_total_ms}ms",
                "next_step": "进入慢任务前先发送“已收到，正在处理”，后续用结果卡片或回执更新。",
            }
        )
    if async_recommended:
        issues.append(
            {
                "kind": "async_recommended",
                "severity": "high",
                "label": "建议后台异步执行",
                "detail": strategy,
                "next_step": "把慢 Provider、附件读取、批量写入或 LLM 判断放入后台任务，并保存 action_receipt。",
            }
        )
    if progress_receipt_required and is_action and not has_receipt:
        issues.append(
            {
                "kind": "missing_action_receipt_for_slow_action",
                "severity": "high",
                "label": "慢动作缺少最终回执",
                "detail": strategy,
                "next_step": "慢动作完成、失败或取消后必须发送最终结果，并保存 context_kind=action_receipt。",
            }
        )
    if long_running_source and not latency.get("cache_known_count"):
        issues.append(
            {
                "kind": "cache_visibility_missing",
                "severity": "low",
                "label": "慢来源缺少缓存可见性",
                "detail": "、".join(sources),
                "next_step": "Provider metadata 应记录 cache_hit/fetch_ms，便于判断是缓存未命中还是外部 API 慢。",
            }
        )
    issue_order = {"high": 0, "medium": 1, "low": 2}
    issues = sorted(issues, key=lambda item: issue_order.get(str(item.get("severity") or "medium"), 1))
    top_issue = issues[0] if issues else {}
    return {
        "status": "healthy" if not issues else "needs_attention",
        "label": "首包与异步契约正常" if not issues else "需要快速首包或异步化",
        "strategy": strategy,
        "question_type": question_type,
        "sources": sources,
        "total_ms": total_ms,
        "provider_total_ms": provider_total_ms,
        "fast_ack_threshold_ms": fast_ack_threshold_ms,
        "async_threshold_ms": async_threshold_ms,
        "fast_ack_required": fast_ack_required,
        "async_recommended": async_recommended,
        "progress_receipt_required": progress_receipt_required,
        "has_action_receipt": has_receipt,
        "slow_provider_count": int(latency.get("slow_provider_count") or 0),
        "slow_substep_count": int(latency.get("slow_substep_count") or 0),
        "issue_count": len(issues),
        "top_issue": top_issue,
        "issues": issues[:8],
    }


def _provider_failure_category(item: dict[str, Any]) -> str:
    status = str(item.get("status") or "").strip()
    error_type = str(item.get("error_type") or item.get("error") or "").strip()
    result_type = str(item.get("result_type") or "").strip()
    if status == "denied" or error_type in {"permission_denied", "no_company_permission", "auth_failed", "token_expired"}:
        return "permission_or_auth"
    if error_type == "missing_params" or item.get("missing_params"):
        return "missing_params"
    if error_type in {"provider_not_registered"}:
        return "provider_missing"
    if error_type in {
        "tool_not_installed",
        "capability_not_installed",
        "operation_not_installed",
        "not_installed",
        "unsupported_operation",
        "operation_not_supported",
        "not_supported",
    }:
        return "not_open"
    if error_type in {"tool_execution_failed", "provider_error", "api_error", "http_error"} or status == "error":
        return "execution_failed"
    if status == "skipped":
        return "not_open"
    if status == "success" and int(item.get("count") or 0) == 0 and (result_type.endswith("_empty") or "empty" in result_type):
        return "empty_result"
    return ""


def _provider_failure_category_label(category: str) -> str:
    return {
        "missing_params": "缺少参数",
        "permission_or_auth": "权限或授权问题",
        "not_open": "能力未开放",
        "execution_failed": "执行失败",
        "provider_missing": "Provider 未注册",
        "empty_result": "没有查到数据",
        "unknown": "未知问题",
    }.get(category, category or "未知问题")


def _provider_failure_reason(item: dict[str, Any], category: str) -> str:
    missing_params = item.get("missing_params") if isinstance(item.get("missing_params"), list) else []
    if category == "missing_params" and missing_params:
        return "缺少：" + "、".join(str(value) for value in missing_params if str(value))
    error_type = str(item.get("error_type") or item.get("error") or "").strip()
    if error_type:
        return error_type
    result_type = str(item.get("result_type") or "").strip()
    if category == "empty_result" and result_type:
        return result_type
    return _provider_failure_category_label(category)


def _provider_failure_next_step(item: dict[str, Any], category: str) -> str:
    recommended = str(item.get("recommended_next_step") or "").strip()
    if recommended:
        return recommended
    if category == "missing_params":
        return "补齐必要参数后重新进入 Planner。"
    if category == "permission_or_auth":
        return "检查飞书授权范围、执行身份和用户权限。"
    if category == "not_open":
        return "补 Provider 实现或把该原子能力标记为暂不开放。"
    if category == "provider_missing":
        return "在 Resource Provider 注册表中接入对应 Provider。"
    if category == "execution_failed":
        return "查看 Provider 原始错误、飞书 API 返回和 worker 日志。"
    if category == "empty_result":
        return "确认查询范围、对象和当前账号是否确实有数据。"
    return "查看 ProviderResult metadata 定位具体原因。"


def _source_execution_contract(envelope: Any, provider_summary: dict[str, Any]) -> dict[str, Any]:
    plan = getattr(envelope, "plan", None)
    planned_sources = [str(source) for source in (getattr(plan, "sources", ()) or ()) if str(source)]
    by_source = provider_summary.get("by_source") if isinstance(provider_summary.get("by_source"), list) else []
    provider_items = provider_summary.get("items") if isinstance(provider_summary.get("items"), list) else []
    executed_sequence = [
        str(item.get("source") or "")
        for item in provider_items
        if isinstance(item, dict) and str(item.get("source") or "").strip()
    ]
    executed_sources = {str(item.get("source") or "") for item in by_source if isinstance(item, dict)}
    if not executed_sources:
        executed_sources = set(executed_sequence)
    planned_set = set(planned_sources)
    missing_sources = [source for source in planned_sources if source not in executed_sources]
    extra_sources = sorted(source for source in executed_sources if source and source not in planned_set)
    executed_planned_sequence: list[str] = []
    for source in executed_sequence:
        if source in planned_set and source not in executed_planned_sequence:
            executed_planned_sequence.append(source)
    order_mismatch = bool(planned_sources and not missing_sources and executed_planned_sequence != planned_sources)
    duplicate_sources = sorted({source for source in executed_sequence if source and executed_sequence.count(source) > 1})
    issues: list[dict[str, Any]] = []
    if missing_sources:
        issues.append(
            {
                "kind": "planned_source_not_executed",
                "severity": "high",
                "label": "计划来源未执行",
                "detail": "、".join(missing_sources),
                "recommendation": "Router 必须按 Planner sources 产生 ProviderResult；来源不可用也要返回 skipped/error，而不是静默跳过。",
            }
        )
    if extra_sources:
        issues.append(
            {
                "kind": "unplanned_source_executed",
                "severity": "medium",
                "label": "执行了未规划来源",
                "detail": "、".join(extra_sources),
                "recommendation": "Router 不应自行规划来源；如确需补充来源，应回到 Planner/Capability 声明层。",
            }
        )
    if order_mismatch:
        issues.append(
            {
                "kind": "source_order_mismatch",
                "severity": "medium",
                "label": "来源执行顺序与 Planner 不一致",
                "detail": "计划：" + "->".join(planned_sources) + "；执行：" + "->".join(executed_planned_sequence),
                "recommendation": "Capability Router 应按 Planner sources 顺序执行，保证证据链和结果合成稳定。",
            }
        )
    if duplicate_sources:
        issues.append(
            {
                "kind": "duplicate_source_execution",
                "severity": "low",
                "label": "同一来源重复执行",
                "detail": "、".join(duplicate_sources),
                "recommendation": "如果不是多操作复合任务，应检查 Router 是否重复调用 Provider。",
            }
        )
    source_items: list[dict[str, Any]] = []
    by_source_map = {str(item.get("source") or ""): item for item in by_source if isinstance(item, dict)}
    for source in planned_sources:
        item = by_source_map.get(source, {})
        issue_count = 0 if source not in missing_sources else 1
        source_items.append(
            {
                "source": source,
                "planned": True,
                "executed": source in executed_sources,
                "result_count": int(item.get("count") or 0),
                "success_count": int(item.get("success_count") or 0),
                "error_count": int(item.get("error_count") or 0),
                "denied_count": int(item.get("denied_count") or 0),
                "skipped_count": int(item.get("skipped_count") or 0),
                "operations": item.get("operations") if isinstance(item.get("operations"), list) else [],
                "issue_count": issue_count,
            }
        )
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    issues = sorted(issues, key=lambda item: severity_order.get(str(item.get("severity") or "medium"), 2))
    return {
        "status": "healthy" if not issues else "needs_attention",
        "label": "Planner 来源与 Provider 执行一致" if not issues else "Planner/Provider 来源需要对齐",
        "planned_sources": planned_sources,
        "executed_sources": sorted(source for source in executed_sources if source),
        "executed_sequence": executed_sequence,
        "executed_planned_sequence": executed_planned_sequence,
        "missing_sources": missing_sources,
        "extra_sources": extra_sources,
        "duplicate_sources": duplicate_sources,
        "order_mismatch": order_mismatch,
        "planned_count": len(planned_sources),
        "executed_planned_count": len([source for source in planned_sources if source in executed_sources]),
        "missing_count": len(missing_sources),
        "extra_count": len(extra_sources),
        "duplicate_count": len(duplicate_sources),
        "issue_count": len(issues),
        "issues": issues[:8],
        "items": source_items,
    }


def _planner_runtime_snapshot_contract(envelope: Any) -> dict[str, Any]:
    plan = getattr(envelope, "plan", None)
    metadata = getattr(plan, "metadata", {}) if plan is not None else {}
    if not isinstance(metadata, dict):
        metadata = {}
    snapshot_meta = metadata.get("runtime_provider_snapshot") if isinstance(metadata.get("runtime_provider_snapshot"), dict) else {}
    planned_sources = [
        str(source)
        for source in (snapshot_meta.get("planned_sources") if isinstance(snapshot_meta.get("planned_sources"), list) else getattr(plan, "sources", ()) or ())
        if str(source)
    ]
    source_status = snapshot_meta.get("source_status") if isinstance(snapshot_meta.get("source_status"), list) else []
    operation_status = snapshot_meta.get("operation_status") if isinstance(snapshot_meta.get("operation_status"), list) else []
    issues: list[dict[str, Any]] = []
    for item in source_status:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "")
        if not source:
            continue
        if not item.get("exists"):
            issues.append({"source": source, "kind": "provider_missing", "label": "Provider 缺失"})
        elif not item.get("enabled"):
            issues.append({"source": source, "kind": "provider_disabled", "label": "Provider 未启用"})
        elif not item.get("healthy"):
            issues.append({"source": source, "kind": "provider_unhealthy", "label": "Provider 不健康"})
        elif int(item.get("issue_count") or 0) > 0:
            issues.append({"source": source, "kind": "operation_issue", "label": "存在未就绪操作"})
    for item in operation_status:
        if not isinstance(item, dict) or item.get("ready"):
            continue
        source = str(item.get("source") or "")
        operation = str(item.get("operation") or "")
        if not source or not operation:
            continue
        issues.append(
            {
                "source": source,
                "operation": operation,
                "kind": str(item.get("status") or "operation_not_ready"),
                "label": str(item.get("label") or "操作未就绪"),
            }
        )
    consumed = bool(snapshot_meta.get("consumed"))
    status = "healthy" if consumed and not issues else "needs_attention" if consumed else "missing"
    return {
        "status": status,
        "label": (
            "Planner 已消费 Runtime Provider Snapshot"
            if status == "healthy"
            else "Planner 消费的 Runtime Provider Snapshot 需要关注"
            if consumed
            else "Planner 未记录 Runtime Provider Snapshot"
        ),
        "consumed": consumed,
        "snapshot_status": snapshot_meta.get("status") or "",
        "snapshot_label": snapshot_meta.get("label") or "",
        "snapshot_issue_count": int(snapshot_meta.get("issue_count") or 0),
        "planned_sources": planned_sources,
        "planned_operation_count": int(snapshot_meta.get("planned_operation_count") or 0),
        "ready_operation_count": int(snapshot_meta.get("ready_operation_count") or 0),
        "blocked_operation_count": int(snapshot_meta.get("blocked_operation_count") or 0),
        "operation_status": [item for item in operation_status if isinstance(item, dict)][:12],
        "top_blocked_operation": snapshot_meta.get("top_blocked_operation") if isinstance(snapshot_meta.get("top_blocked_operation"), dict) else {},
        "source_status": [item for item in source_status if isinstance(item, dict)][:8],
        "issue_count": len(issues),
        "issues": issues[:8],
    }


def _provider_result_quality_issues(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for item in items:
        source = str(item.get("source") or "")
        operation = str(item.get("operation") or "")
        status = str(item.get("status") or "")
        count = int(item.get("count") or 0)
        if status == "success" and count <= 0:
            issues.append(
                {
                    "kind": "success_without_items",
                    "severity": "medium",
                    "source": source,
                    "operation": operation,
                    "label": "Provider 成功但没有结构化条目",
                    "detail": f"{source}:{operation}",
                    "recommendation": "成功结果应尽量返回 items，支撑追问、回执和审计。",
                }
            )
        if status == "success" and not operation:
            issues.append(
                {
                    "kind": "missing_operation",
                    "severity": "medium",
                    "source": source,
                    "operation": operation,
                    "label": "Provider 缺少 operation",
                    "detail": source,
                    "recommendation": "ProviderResult metadata 必须写入 operation，便于 Router、诊断和动作闭环定位。",
                    }
                )
        if bool(item.get("raw_answer_detected")):
            issues.append(
                {
                    "kind": "provider_raw_answer",
                    "severity": "high",
                    "source": source,
                    "operation": operation,
                    "label": "Provider answer 疑似原始技术载荷",
                    "detail": f"{source}:{operation}",
                    "recommendation": "ProviderResult.answer 只放用户可读摘要；原始 JSON、HTTP error、接口返回体必须放 metadata/items 或错误字段，由 Composer 统一转译。",
                }
            )
        if source == "im" and operation in {"send_message", "send_result"} and status == "success":
            if not item.get("resolved_user_id") and not item.get("resolved_chat_id"):
                issues.append(
                    {
                        "kind": "message_send_missing_resolved_target",
                        "severity": "high",
                        "source": source,
                        "operation": operation,
                        "label": "消息发送缺少解析目标",
                        "detail": str(item.get("target_query") or item.get("target") or ""),
                        "recommendation": "发送消息成功回执必须包含 resolved_user_id 或 resolved_chat_id，避免误判已发送。",
                    }
                )
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return sorted(issues, key=lambda item: severity_order.get(str(item.get("severity") or "medium"), 2))[:8]


def _provider_evidence_contract(
    items: list[dict[str, Any]],
    quality_issues: list[dict[str, Any]],
    failure_summary: dict[str, Any],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    issue_rows: list[dict[str, Any]] = []
    for item in items:
        source = str(item.get("source") or "")
        operation = str(item.get("operation") or "")
        status = str(item.get("status") or "")
        duration_ms = int(item.get("duration_ms") or 0)
        count = int(item.get("count") or 0)
        item_count = int(item.get("item_count") or 0)
        error_type = str(item.get("error_type") or "")
        empty_reason = str(item.get("empty_reason") or "")
        issues: list[dict[str, Any]] = []
        if not source:
            issues.append(_provider_evidence_issue("missing_source", source, operation))
        if not operation:
            issues.append(_provider_evidence_issue("missing_operation", source, operation))
        if duration_ms <= 0:
            issues.append(_provider_evidence_issue("missing_duration", source, operation))
        if status in {"error", "denied"} and not error_type and not str(item.get("error") or "").strip():
            issues.append(_provider_evidence_issue("missing_error_type", source, operation))
        if status == "success" and count > 0 and item_count <= 0:
            issues.append(_provider_evidence_issue("missing_items", source, operation))
        if status == "success" and count <= 0 and not empty_reason and str(item.get("result_type") or "").endswith("_empty"):
            issues.append(_provider_evidence_issue("missing_empty_reason", source, operation))
        if bool(item.get("raw_answer_detected")):
            issues.append(_provider_evidence_issue("raw_answer_payload", source, operation))
        issue_rows.extend(issues)
        rows.append(
            {
                "source": source,
                "operation": operation,
                "status": status,
                "duration_ms": duration_ms,
                "count": count,
                "item_count": item_count,
                "error_type": error_type,
                "empty_reason": empty_reason,
                "issue_count": len(issues),
                "issues": issues[:4],
                "standard_fields": {
                    "source": bool(source),
                    "operation": bool(operation),
                    "status": bool(status),
                    "duration_ms": duration_ms > 0,
                    "count": True,
                    "item_count": True,
                    "empty_reason": bool(empty_reason) or count > 0,
                    "error_type": bool(error_type) or status not in {"error", "denied"},
                },
            }
        )
    blocking_count = len([item for item in issue_rows if item.get("severity") in {"critical", "high"}])
    top_issue = issue_rows[0] if issue_rows else {}
    return {
        "status": "healthy" if not issue_rows else "needs_attention",
        "label": "Provider 执行证据完整" if not issue_rows else "Provider 执行证据需要补齐",
        "provider_count": len(items),
        "issue_count": len(issue_rows),
        "blocking_count": blocking_count,
        "quality_issue_count": len(quality_issues),
        "failure_issue_count": int(failure_summary.get("issue_count") or 0) if isinstance(failure_summary, dict) else 0,
        "top_issue": top_issue,
        "issues": issue_rows[:10],
        "items": rows[:12],
    }


def _provider_evidence_issue(kind: str, source: str, operation: str) -> dict[str, Any]:
    payload = {
        "missing_source": ("critical", "ProviderResult 缺少来源", "ProviderResult.source 必须记录 Resource Provider 来源。"),
        "missing_operation": ("high", "ProviderResult 缺少操作", "metadata.operation 必须记录实际原子操作。"),
        "missing_duration": ("medium", "ProviderResult 缺少耗时", "Capability Router 应写入 duration_ms，便于定位慢 Provider。"),
        "missing_error_type": ("high", "错误结果缺少错误类型", "错误或拒绝结果必须写入 error_type 或 error。"),
        "missing_items": ("high", "有数量但缺结构化条目", "count > 0 时应保存 items，支撑追问和 Result Context。"),
        "missing_empty_reason": ("medium", "空结果缺少原因", "空结果应写入 empty_reason 和 recommended_next_step。"),
        "raw_answer_payload": ("high", "answer 疑似技术载荷", "原始 JSON、HTTP error、接口返回体必须放 metadata/items/error，不应直接进入 answer。"),
    }.get(kind, ("medium", kind or "Provider 证据问题", "补齐 ProviderResult 标准字段。"))
    severity, label, next_step = payload
    return {
        "kind": kind,
        "severity": severity,
        "source": source,
        "operation": operation,
        "label": label,
        "detail": f"{source}:{operation}".strip(":"),
        "next_step": next_step,
        "priority_label": "高" if severity in {"critical", "high"} else "中" if severity == "medium" else "低",
    }


def _looks_like_raw_payload(text: str) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return False
    return stripped.startswith("{") or stripped.startswith("[") or "HTTP error" in stripped or "status_code" in stripped


def _runtime_health(envelope: Any) -> dict[str, Any]:
    intent = getattr(envelope, "intent", None)
    permission = getattr(envelope, "permission", None)
    execution = getattr(envelope, "execution", None)
    composed = getattr(envelope, "composed", None)
    provider_results = list(getattr(execution, "provider_results", ()) or ()) if execution is not None else []
    provider_errors = [
        {
            "source": getattr(item, "source", ""),
            "error_type": _provider_metadata_value(item, "error_type", "") or getattr(item, "error", ""),
        }
        for item in provider_results
        if getattr(item, "status", "") in {"error", "denied"}
    ]
    if intent is not None and bool(getattr(intent, "missing_params", ())):
        stage = "clarification"
    elif permission is not None and not bool(getattr(permission, "allowed", False)):
        stage = "permission_denied"
    elif composed is not None and bool(getattr(getattr(composed, "metadata", {}), "get", lambda *_: False)("requires_confirmation")):
        stage = "pending_confirmation"
    elif provider_errors:
        stage = "provider_error"
    elif execution is not None and getattr(execution, "status", "") == "success":
        stage = "success"
    elif execution is None:
        stage = "not_executed"
    else:
        stage = getattr(execution, "status", "unknown")
    return {
        "stage": stage,
        "has_answer": bool(getattr(composed, "answer", "") if composed is not None else ""),
        "has_result_context": bool(getattr(execution, "result_context", None) if execution is not None else None),
        "provider_error_count": len(provider_errors),
        "provider_errors": provider_errors[:5],
    }


def _pipeline_frames(envelope: Any) -> list[dict[str, Any]]:
    intent = getattr(envelope, "intent", None)
    plan = getattr(envelope, "plan", None)
    permission = getattr(envelope, "permission", None)
    execution = getattr(envelope, "execution", None)
    composed = getattr(envelope, "composed", None)
    previous_result_context = getattr(getattr(envelope, "context", None), "result_context", None)
    return [
        {"name": "pre_gateway", "label": "Pre Gateway", "status": "loaded"},
        {
            "name": "result_followup_detector",
            "label": "Result Follow-up Detector",
            "status": "used_previous_result"
            if bool(getattr(getattr(envelope, "intent", None), "entities", {}).get("use_previous_result"))
            else "checked",
            "has_result_context": bool(previous_result_context),
            "result_type": getattr(previous_result_context, "result_type", ""),
        },
        {
            "name": "intent_recognition",
            "label": "Intent Recognition",
            "status": "success" if intent is not None else "missing",
            "intent": getattr(intent, "intent", ""),
            "confidence": getattr(intent, "confidence", 0.0),
            "missing_params": list(getattr(intent, "missing_params", ()) or ()),
        },
        {
            "name": "task_planner",
            "label": "Task Planner",
            "status": "success" if plan is not None else "missing",
            "strategy": getattr(plan, "strategy", ""),
            "sources": list(getattr(plan, "sources", ()) or ()),
        },
        {
            "name": "permission_check",
            "label": "Permission Check",
            "status": "allowed" if bool(getattr(permission, "allowed", False)) else "denied",
            "requires_confirmation": bool(getattr(permission, "requires_confirmation", False)),
            "execution_identity": getattr(permission, "execution_identity", ""),
            "reason": getattr(permission, "reason", ""),
            "metadata": getattr(permission, "metadata", {}) if isinstance(getattr(permission, "metadata", {}), dict) else {},
        },
        {
            "name": "capability_router",
            "label": "Capability Router",
            "status": "routed" if execution is not None else "not_routed",
            "sources": list(getattr(plan, "sources", ()) or ()),
        },
        {
            "name": "execution",
            "label": "Execution",
            "status": getattr(execution, "status", "not_executed") if execution is not None else "not_executed",
        },
        {
            "name": "answer_composer",
            "label": "Answer Composer",
            "status": "success" if composed is not None and getattr(composed, "answer", "") else "empty",
            "result_type": getattr(getattr(composed, "result_context", None), "result_type", ""),
        },
    ]


def _provider_metadata_value(item: Any, key: str, default: Any) -> Any:
    metadata = getattr(item, "metadata", None)
    if not isinstance(metadata, dict):
        return default
    return metadata.get(key, default)


def _composed_metadata_value(composed: Any, key: str, default: Any) -> Any:
    metadata = getattr(composed, "metadata", None)
    if not isinstance(metadata, dict):
        return default
    return metadata.get(key, default)


def _session_action_trace(context: Any) -> list[dict[str, Any]]:
    session_context = getattr(context, "session_context", None)
    if not isinstance(session_context, dict):
        return []
    traces = session_context.get("runtime_v5_action_trace")
    if not isinstance(traces, list):
        return []
    return [item for item in traces[-10:] if isinstance(item, dict)]


def _session_result_context_events(context: Any) -> list[dict[str, Any]]:
    session_context = getattr(context, "session_context", None)
    if not isinstance(session_context, dict):
        return []
    events = session_context.get("runtime_v5_result_context_events")
    if not isinstance(events, list):
        return []
    return [item for item in events[-10:] if isinstance(item, dict)]


def _result_context_event_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    valid_events = [event for event in events if isinstance(event, dict)]
    latest = valid_events[-1] if valid_events else {}
    save_events = [event for event in valid_events if event.get("action") == "save"]
    clear_events = [event for event in valid_events if event.get("action") == "clear"]
    latest_policy = latest.get("consume_policy") if isinstance(latest.get("consume_policy"), dict) else {}
    latest_followup_fields = latest.get("followup_fields") if isinstance(latest.get("followup_fields"), list) else []
    latest_identity_fields = latest.get("item_identity_fields") if isinstance(latest.get("item_identity_fields"), list) else []
    action_receipts = [
        event
        for event in valid_events
        if event.get("action") == "save" and str(event.get("context_kind") or "") == "action_receipt"
    ]
    pending_confirmations = [
        event
        for event in valid_events
        if event.get("action") == "save" and str(event.get("context_kind") or "") == "pending_confirmation"
    ]
    latest_receipt = action_receipts[-1] if action_receipts else {}
    latest_pending_confirmation = pending_confirmations[-1] if pending_confirmations else {}
    return {
        "available": bool(valid_events),
        "event_count": len(valid_events),
        "save_count": len(save_events),
        "clear_count": len(clear_events),
        "action_receipt_count": len(action_receipts),
        "pending_confirmation_count": len(pending_confirmations),
        "latest_action": str(latest.get("action") or ""),
        "latest_result_type": str(latest.get("result_type") or ""),
        "latest_context_kind": str(latest.get("context_kind") or ""),
        "latest_item_count": int(latest.get("item_count") or latest.get("count") or 0) if latest else 0,
        "latest_action_id": str(latest.get("action_id") or ""),
        "latest_confirmation_token": str(latest.get("confirmation_token") or ""),
        "latest_route_path": str(latest.get("route_path") or ""),
        "latest_result_sources": latest.get("result_sources") if isinstance(latest.get("result_sources"), list) else [],
        "latest_action_status_group": str(latest.get("action_status_group") or ""),
        "latest_is_terminal_action": bool(latest.get("is_terminal_action", False)),
        "latest_is_pending_action": bool(latest.get("is_pending_action", False)),
        "latest_followup_field_count": len(latest_followup_fields),
        "latest_identity_field_count": len(latest_identity_fields),
        "latest_prefer_items": bool(latest_policy.get("prefer_items", False)),
        "latest_allow_answer_fallback": bool(latest_policy.get("allow_answer_fallback", False)),
        "latest_source_execution_step_count": int(latest.get("source_execution_step_count") or 0),
        "latest_source_execution_status": latest.get("source_execution_status") if isinstance(latest.get("source_execution_status"), dict) else {},
        "latest_saved_at": str(latest.get("saved_at") or ""),
        "latest_expires_at": str(latest.get("expires_at") or ""),
        "latest_receipt_action_id": str(latest_receipt.get("action_id") or ""),
        "latest_receipt_execution_status": str(latest_receipt.get("execution_status") or ""),
        "latest_receipt_status_group": str(latest_receipt.get("action_status_group") or ""),
        "latest_pending_confirmation_action_id": str(latest_pending_confirmation.get("action_id") or ""),
        "latest_pending_confirmation_token": str(latest_pending_confirmation.get("confirmation_token") or ""),
    }


def _result_context_summary(result_context: Any) -> dict[str, Any]:
    if result_context is None:
        return {"available": False}
    metadata = getattr(result_context, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    provider_evidence = metadata.get("provider_evidence") if isinstance(metadata.get("provider_evidence"), dict) else {}
    followup_fields = metadata.get("followup_fields") if isinstance(metadata.get("followup_fields"), list) else []
    item_identity_fields = metadata.get("item_identity_fields") if isinstance(metadata.get("item_identity_fields"), list) else []
    consume_policy = metadata.get("consume_policy") if isinstance(metadata.get("consume_policy"), dict) else {}
    source_execution_status = metadata.get("source_execution_status") if isinstance(metadata.get("source_execution_status"), dict) else {}
    source_execution_steps = metadata.get("source_execution_steps") if isinstance(metadata.get("source_execution_steps"), list) else []
    saved_at = str(metadata.get("saved_at") or "")
    expires_at = str(metadata.get("expires_at") or "")
    return {
        "available": True,
        "result_type": getattr(result_context, "result_type", ""),
        "query_id": getattr(result_context, "query_id", ""),
        "count": getattr(result_context, "count", 0),
        "display_count": metadata.get("display_count", getattr(result_context, "count", 0)),
        "item_count": metadata.get("item_count", getattr(result_context, "count", 0)),
        "actionable": bool(metadata.get("actionable", False)),
        "context_kind": metadata.get("context_kind") or "query_result",
        "question_type": metadata.get("question_type", ""),
        "data_scope": metadata.get("data_scope", ""),
        "empty_result": bool(metadata.get("empty_result", False)),
        "empty_reason": metadata.get("empty_reason", ""),
        "recommended_next_step": metadata.get("recommended_next_step", ""),
        "execution_status": metadata.get("execution_status", ""),
        "sources": metadata.get("result_sources") or metadata.get("sources") or [],
        "provider_evidence_count": int(provider_evidence.get("provider_count") or 0) if provider_evidence else 0,
        "provider_evidence_duration_ms": int(provider_evidence.get("total_duration_ms") or 0) if provider_evidence else 0,
        "provider_evidence_operations": provider_evidence.get("operations", []) if provider_evidence else [],
        "source_execution_status": source_execution_status,
        "source_execution_steps": source_execution_steps,
        "source_execution_step_count": len(source_execution_steps),
        "followup_fields": followup_fields,
        "followup_field_count": len(followup_fields),
        "item_identity_fields": item_identity_fields,
        "item_identity_field_count": len(item_identity_fields),
        "consume_policy": consume_policy,
        "prefer_items": bool(consume_policy.get("prefer_items", False)) if consume_policy else False,
        "allow_answer_fallback": bool(consume_policy.get("allow_answer_fallback", False)) if consume_policy else False,
        "saved_at": saved_at,
        "expires_at": expires_at,
        "ttl_seconds": int(metadata.get("ttl_seconds") or 0),
        "age_seconds": _iso_age_seconds(saved_at),
        "expires_in_seconds": _iso_seconds_until(expires_at),
        "item_summaries": _result_item_summaries(getattr(result_context, "items", ()) or ()),
    }


def _result_item_summaries(items: Any) -> list[str]:
    summaries: list[str] = []
    for item in list(items)[:3]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("summary") or item.get("title") or item.get("name") or item.get("subject") or "").strip()
        applicant = str(item.get("applicant") or "").strip()
        amount = str(item.get("amount") or "").strip()
        row_count = str(item.get("row_count") or "").strip()
        target = str(item.get("target") or "").strip()
        start = str(item.get("start") or "").strip()
        url = str(item.get("url") or "").strip()
        parts = [value for value in (title, applicant, amount, f"{row_count} 行" if row_count else "", target, start, url) if value]
        if parts:
            summaries.append("｜".join(parts))
    return summaries


def provider_registry_diagnostics(providers: dict[str, Any] | None) -> dict[str, Any]:
    if not providers:
        return {"registered_sources": [], "providers": {}}
    items: dict[str, Any] = {}
    for source, provider in providers.items():
        operations = getattr(provider, "_OPERATIONS", None)
        if isinstance(operations, dict):
            installed = []
            local_readonly = []
            pending = []
            operation_contracts = {}
            for operation, spec in operations.items():
                tool_name = spec[0] if isinstance(spec, tuple) and spec else spec
                is_write = bool(spec[1]) if isinstance(spec, tuple) and len(spec) > 1 else False
                contract = _provider_operation_contract(source, str(operation), tool_name, is_write)
                operation_contracts[str(operation)] = contract
                if isinstance(tool_name, str) and tool_name.startswith("local_"):
                    local_readonly.append(str(operation))
                elif tool_name:
                    installed.append(str(operation))
                else:
                    pending.append(str(operation))
            items[source] = {
                "class": provider.__class__.__name__,
                "installed_operations": sorted(installed),
                "local_readonly_operations": sorted(local_readonly),
                "pending_operations": sorted(pending),
                "operation_contracts": operation_contracts,
                "read_operations": sorted(operation for operation, payload in operation_contracts.items() if not payload.get("is_write")),
                "write_operations": sorted(operation for operation, payload in operation_contracts.items() if payload.get("is_write")),
                "declared_operations": sorted(operation for operation, payload in operation_contracts.items() if payload.get("declared_by_capability")),
                "undeclared_operations": sorted(operation for operation, payload in operation_contracts.items() if not payload.get("declared_by_capability")),
                "requires_confirmation_operations": sorted(operation for operation, payload in operation_contracts.items() if payload.get("requires_confirmation")),
                "installed_count": len(installed),
                "local_readonly_count": len(local_readonly),
                "pending_count": len(pending),
                "read_count": len([payload for payload in operation_contracts.values() if not payload.get("is_write")]),
                "write_count": len([payload for payload in operation_contracts.values() if payload.get("is_write")]),
                "requires_confirmation_count": len([payload for payload in operation_contracts.values() if payload.get("requires_confirmation")]),
            }
        else:
            items[source] = {
                "class": provider.__class__.__name__,
                "installed_operations": [],
                "local_readonly_operations": [],
                "pending_operations": [],
                "operation_contracts": {},
                "read_operations": [],
                "write_operations": [],
                "declared_operations": [],
                "undeclared_operations": [],
                "requires_confirmation_operations": [],
                "installed_count": 0,
                "local_readonly_count": 0,
                "pending_count": 0,
                "read_count": 0,
                "write_count": 0,
                "requires_confirmation_count": 0,
            }
    contract_health = _provider_contract_health(items)
    drift = _capability_provider_drift(items)
    planner_drift = _planner_capability_drift()
    skill_registry = _skill_provider_registry_gaps(items)
    capability_contract = _provider_capability_contract(items, drift, planner_drift, contract_health)
    write_confirmation_contract = _provider_write_confirmation_contract(items)
    retired_registered_sources = sorted(source for source in ("message",) if source in items)
    return {
        "registered_sources": sorted(providers.keys()),
        "retired_registered_sources": retired_registered_sources,
        "retired_registered_source_count": len(retired_registered_sources),
        "installed_operation_count": sum(item["installed_count"] for item in items.values()),
        "local_readonly_operation_count": sum(item["local_readonly_count"] for item in items.values()),
        "pending_operation_count": sum(item["pending_count"] for item in items.values()),
        "read_operation_count": sum(item.get("read_count", 0) for item in items.values()),
        "write_operation_count": sum(item.get("write_count", 0) for item in items.values()),
        "requires_confirmation_operation_count": sum(item.get("requires_confirmation_count", 0) for item in items.values()),
        "capability_contract": capability_contract,
        "write_confirmation_contract": write_confirmation_contract,
        "skill_registry": skill_registry,
        "contract_health": contract_health,
        "capability_drift": drift,
        "planner_drift": planner_drift,
        "status_summary": _provider_status_summary(items, drift, planner_drift, contract_health, capability_contract, skill_registry),
        "providers": items,
    }


def _skill_provider_registry_gaps(provider_items: dict[str, Any]) -> dict[str, Any]:
    registered = [item for item in SKILL_ATOMIC_CAPABILITIES if item.registered]
    exposed = [item for item in registered if item.exposed]
    pending = [item for item in registered if not item.exposed]
    provider_sources = set(provider_items)
    missing_provider_sources = sorted({item.source for item in registered if item.source not in provider_sources})
    missing_provider_operations = []
    registered_provider_operations = []
    exposed_unexecutable_operations = []
    connected_operations = []
    registered_not_exposed_operations = []
    status_matrix = []
    by_source_status: dict[str, dict[str, Any]] = {}
    for item in registered:
        payload = provider_items.get(item.source) if isinstance(provider_items.get(item.source), dict) else {}
        operations = payload.get("operation_contracts") if isinstance(payload.get("operation_contracts"), dict) else {}
        row = {
            "source": item.source,
            "operation": item.operation,
            "skill": item.skill,
            "label": item.label,
            "question_type": item.question_type,
            "execution_identity": item.execution_identity,
            "risk_level": item.risk_level,
            "exposed": item.exposed,
            "requires_confirmation": item.requires_confirmation,
            "reason": item.reason,
        }
        source_row = by_source_status.setdefault(
            item.source,
            {
                "source": item.source,
                "registered_count": 0,
                "connected_count": 0,
                "registered_not_exposed_count": 0,
                "exposed_unexecutable_count": 0,
                "missing_provider_count": 0,
            },
        )
        source_row["registered_count"] += 1
        if item.source not in provider_sources or item.operation not in operations:
            missing_provider_operations.append(row)
            source_row["missing_provider_count"] += 1
            status_matrix.append(
                {
                    **row,
                    "status": "missing_provider",
                    "status_label": "缺 Provider",
                    "next_step": "补 Provider 来源和操作契约，接入前不要开放给用户。",
                }
            )
        else:
            registered_provider_operations.append(row)
            contract = operations.get(item.operation) if isinstance(operations.get(item.operation), dict) else {}
            if item.exposed and (bool(contract.get("pending")) or bool(contract.get("local_readonly")) or not bool(contract.get("installed"))):
                matrix_row = {
                    **row,
                    "status": "exposed_unexecutable",
                    "status_label": "已开放不可执行",
                    "pending": bool(contract.get("pending")),
                    "local_readonly": bool(contract.get("local_readonly")),
                    "installed": bool(contract.get("installed")),
                    "tool_name": contract.get("tool_name", ""),
                    "next_step": "补真实执行实现，或先关闭 exposed。",
                }
                exposed_unexecutable_operations.append(matrix_row)
                status_matrix.append(matrix_row)
                source_row["exposed_unexecutable_count"] += 1
            elif not item.exposed:
                matrix_row = {
                    **row,
                    "status": "registered_not_exposed",
                    "status_label": "已登记未开放",
                    "tool_name": contract.get("tool_name", ""),
                    "next_step": item.reason or "完成权限、确认和回执契约后再开放。",
                }
                registered_not_exposed_operations.append(matrix_row)
                status_matrix.append(matrix_row)
                source_row["registered_not_exposed_count"] += 1
            else:
                matrix_row = {
                    **row,
                    "status": "connected",
                    "status_label": "已接通",
                    "tool_name": contract.get("tool_name", ""),
                    "next_step": "",
                }
                connected_operations.append(matrix_row)
                status_matrix.append(matrix_row)
                source_row["connected_count"] += 1
    status_matrix = [_skill_provider_priority_row(row) for row in status_matrix]
    priority_queue = _skill_provider_priority_queue(status_matrix)
    rollout_status = "ready"
    rollout_label = "Skill 原子能力已登记、已接 Provider，开放状态安全"
    if exposed_unexecutable_operations:
        rollout_status = "unsafe_exposed"
        rollout_label = "存在已开放但不可执行的 Skill 原子能力"
    elif missing_provider_operations:
        rollout_status = "provider_incomplete"
        rollout_label = "Skill 原子能力已登记，但仍有 Provider 未补齐"
    elif registered_not_exposed_operations:
        rollout_status = "registered_gated"
        rollout_label = "Skill 原子能力已接 Provider，部分按风险受控待开放"
    return {
        "rollout_status": rollout_status,
        "rollout_label": rollout_label,
        "registered_count": len(registered),
        "exposed_count": len(exposed),
        "pending_count": len(pending),
        "gated_count": len(registered_not_exposed_operations),
        "missing_provider_source_count": len(missing_provider_sources),
        "missing_provider_operation_count": len(missing_provider_operations),
        "connected_count": len(connected_operations),
        "registered_not_exposed_count": len(registered_not_exposed_operations),
        "exposed_unexecutable_count": len(exposed_unexecutable_operations),
        "provider_backed_count": len(registered_provider_operations),
        "provider_backed_ratio": round(len(registered_provider_operations) / len(registered), 4) if registered else 1.0,
        "safe_exposed_count": len(exposed) - len(exposed_unexecutable_operations),
        "missing_provider_sources": missing_provider_sources,
        "missing_provider_operations": missing_provider_operations[:20],
        "connected_operations": connected_operations[:20],
        "registered_not_exposed_operations": registered_not_exposed_operations[:20],
        "exposed_unexecutable_operations": exposed_unexecutable_operations[:20],
        "status_matrix": status_matrix[:80],
        "priority_queue_count": len(priority_queue),
        "priority_queue": priority_queue[:20],
        "top_priority_operation": priority_queue[0] if priority_queue else {},
        "by_source_status": sorted(by_source_status.values(), key=lambda payload: str(payload.get("source") or "")),
        "pending_operations": [
            {
                "source": item.source,
                "operation": item.operation,
                "skill": item.skill,
                "label": item.label,
                "risk_level": item.risk_level,
                "reason": item.reason,
            }
            for item in pending[:20]
        ],
        "status": "needs_attention" if missing_provider_operations or exposed_unexecutable_operations else "healthy",
        "label": rollout_label,
    }


def _skill_provider_priority_row(row: dict[str, Any]) -> dict[str, Any]:
    status = str(row.get("status") or "")
    source = str(row.get("source") or "")
    risk_level = str(row.get("risk_level") or "low")
    question_type = str(row.get("question_type") or "")
    score = 0
    reasons: list[str] = []
    if status == "exposed_unexecutable":
        score += 80
        reasons.append("已开放给用户但不可执行")
    elif status == "missing_provider":
        score += 55
        reasons.append("Skill 已登记但缺 Provider")
    elif status == "registered_not_exposed":
        score += 25
        reasons.append("已登记但未开放")
    if risk_level == "high":
        score += 20
        reasons.append("高风险能力")
    elif risk_level == "medium":
        score += 10
        reasons.append("中风险能力")
    if question_type == "action" or bool(row.get("requires_confirmation")):
        score += 15
        reasons.append("动作类能力需要确认闭环")
    source_weight = {
        "approval": 18,
        "im": 16,
        "people": 15,
        "calendar": 14,
        "task": 13,
        "mail": 12,
        "base": 11,
        "sheets": 10,
        "docs": 8,
        "drive": 8,
        "wiki": 7,
    }.get(source, 3)
    if status != "connected":
        score += source_weight
    if score >= 95:
        priority_label = "高"
    elif score >= 55:
        priority_label = "中"
    elif score > 0:
        priority_label = "低"
    else:
        priority_label = "无"
    return {
        **row,
        "priority_score": score,
        "priority_label": priority_label,
        "priority_reasons": reasons,
    }


def _skill_provider_priority_queue(status_matrix: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [
        row
        for row in status_matrix
        if isinstance(row, dict)
        and str(row.get("status") or "") != "connected"
        and int(row.get("priority_score") or 0) > 0
    ]
    return sorted(
        rows,
        key=lambda row: (
            -int(row.get("priority_score") or 0),
            str(row.get("source") or ""),
            str(row.get("operation") or ""),
        ),
    )


def _provider_operation_contract(source: str, operation: str, tool_name: Any, is_write: bool) -> dict[str, Any]:
    capability = next((item for item in RUNTIME_CAPABILITIES if item.source == source and item.operation == operation), None)
    installed = bool(tool_name)
    local_readonly = isinstance(tool_name, str) and tool_name.startswith("local_")
    requires_confirmation = bool(capability.requires_confirmation) if capability is not None else bool(is_write)
    execution_identity = capability.execution_identity if capability is not None else ("user" if is_write else "bot")
    write_confirmation_contract_ok = (not is_write) or (
        installed and not local_readonly and requires_confirmation and execution_identity == "user"
    )
    return {
        "operation": operation,
        "tool_name": tool_name or "",
        "installed": installed and not local_readonly,
        "local_readonly": local_readonly,
        "pending": not installed,
        "is_write": is_write,
        "mode": "write" if is_write else "read",
        "declared_by_capability": capability is not None,
        "capability_installed": bool(capability.installed) if capability is not None else False,
        "question_type": capability.question_type if capability is not None else ("action" if is_write else "query"),
        "data_scope": capability.data_scope if capability is not None else "",
        "strategy": capability.strategy if capability is not None else "",
        "label": capability.label if capability is not None else "",
        "requires_confirmation": requires_confirmation,
        "execution_identity": execution_identity,
        "write_confirmation_contract": "dry_run_then_confirmation_token" if is_write else "readonly",
        "requires_dry_run": bool(is_write),
        "requires_confirmation_token": bool(is_write),
        "write_confirmation_contract_ok": write_confirmation_contract_ok,
    }


def _provider_contract_health(provider_items: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    for source, payload in sorted(provider_items.items()):
        contracts = payload.get("operation_contracts") if isinstance(payload.get("operation_contracts"), dict) else {}
        for operation, contract in sorted(contracts.items()):
            if not isinstance(contract, dict):
                continue
            is_write = bool(contract.get("is_write"))
            declared = bool(contract.get("declared_by_capability"))
            pending = bool(contract.get("pending"))
            local_readonly = bool(contract.get("local_readonly"))
            question_type = str(contract.get("question_type") or "")
            if is_write and not declared:
                issues.append(
                    {
                        "kind": "write_missing_capability",
                        "severity": "critical",
                        "source": source,
                        "operation": operation,
                        "label": "写操作未纳入 Capability",
                        "recommendation": "把该写操作补进 RUNTIME_CAPABILITIES，并声明 question_type=action、execution_identity=user、requires_confirmation=True。",
                    }
                )
            if is_write and not bool(contract.get("requires_confirmation")):
                issues.append(
                    {
                        "kind": "write_missing_confirmation",
                        "severity": "critical",
                        "source": source,
                        "operation": operation,
                        "label": "写操作缺少二次确认",
                        "recommendation": "写入、发送、审批、会议、任务等动作必须 requires_confirmation=True。",
                    }
                )
            if is_write and not bool(contract.get("write_confirmation_contract_ok")):
                issues.append(
                    {
                        "kind": "write_confirmation_contract_gap",
                        "severity": "critical",
                        "source": source,
                        "operation": operation,
                        "label": "写操作确认契约不完整",
                        "recommendation": "写操作必须先 dry-run，并在确认写目标后带 confirmation_token 执行；同时要求真实 Provider、requires_confirmation=True、execution_identity=user。",
                    }
                )
            if is_write and str(contract.get("execution_identity") or "") != "user":
                issues.append(
                    {
                        "kind": "write_identity_not_user",
                        "severity": "high",
                        "source": source,
                        "operation": operation,
                        "label": "写操作执行身份不是用户",
                        "recommendation": "Action 默认以用户身份执行；除非明确是系统后台动作，否则 execution_identity 应为 user。",
                    }
                )
            if pending and declared and bool(contract.get("capability_installed")):
                issues.append(
                    {
                        "kind": "declared_installed_but_provider_pending",
                        "severity": "high",
                        "source": source,
                        "operation": operation,
                        "label": "Capability 标记可用但 Provider 未接通",
                        "recommendation": "要么接入真实飞书原子能力，要么把 Capability installed 设为 False，避免 Planner 规划到不可执行能力。",
                    }
                )
            if local_readonly and question_type == "action":
                issues.append(
                    {
                        "kind": "action_local_readonly",
                        "severity": "medium",
                        "source": source,
                        "operation": operation,
                        "label": "Action 只有本地只读实现",
                        "recommendation": "Action 不应停留在 local_* 只读占位；需要接真实写入 Provider 或降级为 query/analysis。",
                    }
                )
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    issues = sorted(issues, key=lambda item: severity_order.get(str(item.get("severity") or "medium"), 2))
    blocking_count = len([item for item in issues if item.get("severity") == "critical"])
    return {
        "status": "healthy" if not issues else "blocked" if blocking_count else "needs_attention",
        "label": "Provider 契约一致" if not issues else "Provider 契约需要修复",
        "issue_count": len(issues),
        "blocking_count": blocking_count,
        "issues": issues[:12],
    }


def _provider_write_confirmation_contract(provider_items: dict[str, Any]) -> dict[str, Any]:
    write_operations: list[dict[str, Any]] = []
    gap_operations: list[dict[str, Any]] = []
    dry_run_required_count = 0
    token_required_count = 0
    ok_count = 0
    for source, payload in sorted(provider_items.items()):
        contracts = payload.get("operation_contracts") if isinstance(payload.get("operation_contracts"), dict) else {}
        for operation, contract in sorted(contracts.items()):
            if not isinstance(contract, dict) or not bool(contract.get("is_write")):
                continue
            row = {
                "source": source,
                "operation": operation,
                "tool_name": contract.get("tool_name") or "",
                "contract": contract.get("write_confirmation_contract") or "",
                "requires_dry_run": bool(contract.get("requires_dry_run")),
                "requires_confirmation_token": bool(contract.get("requires_confirmation_token")),
                "requires_confirmation": bool(contract.get("requires_confirmation")),
                "execution_identity": contract.get("execution_identity") or "",
                "ok": bool(contract.get("write_confirmation_contract_ok")),
            }
            write_operations.append(row)
            if row["requires_dry_run"]:
                dry_run_required_count += 1
            if row["requires_confirmation_token"]:
                token_required_count += 1
            if row["ok"]:
                ok_count += 1
            else:
                gap_operations.append(row)
    return {
        "status": "healthy" if not gap_operations else "blocked",
        "label": "写操作确认契约完整" if not gap_operations else "写操作确认契约存在缺口",
        "write_operation_count": len(write_operations),
        "dry_run_required_count": dry_run_required_count,
        "confirmation_token_required_count": token_required_count,
        "ok_count": ok_count,
        "gap_count": len(gap_operations),
        "gap_operations": gap_operations[:12],
    }


def _provider_status_summary(
    provider_items: dict[str, Any],
    drift: dict[str, Any],
    planner_drift: dict[str, Any],
    contract_health: dict[str, Any],
    capability_contract: dict[str, Any],
    skill_registry: dict[str, Any],
) -> dict[str, Any]:
    usable_sources = []
    readonly_sources = []
    pending_sources = []
    for source, payload in sorted(provider_items.items()):
        installed_count = int(payload.get("installed_count") or 0)
        readonly_count = int(payload.get("local_readonly_count") or 0)
        pending_count = int(payload.get("pending_count") or 0)
        if installed_count:
            usable_sources.append(source)
        elif readonly_count:
            readonly_sources.append(source)
        if pending_count:
            pending_sources.append(
                {
                    "source": source,
                    "operations": list(payload.get("pending_operations") or [])[:8],
                    "count": pending_count,
                }
            )
    drift_count = int(drift.get("declared_missing_provider_count") or 0) + int(drift.get("provider_missing_declaration_count") or 0)
    planner_drift_count = int(planner_drift.get("planner_missing_capability_count") or 0) + int(planner_drift.get("capability_missing_planner_count") or 0)
    pending_count = sum(int(item.get("count") or 0) for item in pending_sources)
    drift_categories = _runtime_drift_categories(drift, planner_drift, pending_sources)
    contract_issue_count = int(contract_health.get("issue_count") or 0)
    write_confirmation_contract = _provider_write_confirmation_contract(provider_items)
    source_readiness = _provider_source_readiness(provider_items, contract_health)
    priority_gaps = _provider_priority_gaps(
        contract_health=contract_health,
        drift=drift,
        planner_drift=planner_drift,
        pending_sources=pending_sources,
    )
    is_healthy = (
        drift_count == 0
        and planner_drift_count == 0
        and pending_count == 0
        and contract_issue_count == 0
    )
    readiness = _runtime_readiness_score(
        drift_count=drift_count,
        planner_drift_count=planner_drift_count,
        pending_count=pending_count,
        contract_issue_count=contract_issue_count,
        contract_blocking_count=int(contract_health.get("blocking_count") or 0),
    )
    return {
        "health": "healthy" if is_healthy else "needs_attention",
        "health_label": "主链路健康" if is_healthy else "需要继续补齐",
        "readiness": readiness,
        "drift_count": drift_count,
        "planner_drift_count": planner_drift_count,
        "contract_issue_count": contract_issue_count,
        "contract_blocking_count": int(contract_health.get("blocking_count") or 0),
        "pending_operation_count": pending_count,
        "source_readiness": source_readiness,
        "priority_gaps": priority_gaps,
        "capability_contract": {
            "status": capability_contract.get("status", ""),
            "label": capability_contract.get("label", ""),
            "declared_count": capability_contract.get("declared_count", 0),
            "provider_operation_count": capability_contract.get("provider_operation_count", 0),
            "aligned_count": capability_contract.get("aligned_count", 0),
            "ungoverned_write_count": capability_contract.get("ungoverned_write_count", 0),
            "confirmation_gap_count": capability_contract.get("confirmation_gap_count", 0),
            "identity_gap_count": capability_contract.get("identity_gap_count", 0),
        },
        "write_confirmation_contract": write_confirmation_contract,
        "skill_registry": {
            "status": skill_registry.get("status", ""),
            "label": skill_registry.get("label", ""),
            "rollout_status": skill_registry.get("rollout_status", ""),
            "rollout_label": skill_registry.get("rollout_label", ""),
            "registered_count": skill_registry.get("registered_count", 0),
            "exposed_count": skill_registry.get("exposed_count", 0),
            "pending_count": skill_registry.get("pending_count", 0),
            "gated_count": skill_registry.get("gated_count", 0),
            "missing_provider_source_count": skill_registry.get("missing_provider_source_count", 0),
            "missing_provider_operation_count": skill_registry.get("missing_provider_operation_count", 0),
            "connected_count": skill_registry.get("connected_count", 0),
            "registered_not_exposed_count": skill_registry.get("registered_not_exposed_count", 0),
            "exposed_unexecutable_count": skill_registry.get("exposed_unexecutable_count", 0),
            "provider_backed_count": skill_registry.get("provider_backed_count", 0),
            "provider_backed_ratio": skill_registry.get("provider_backed_ratio", 0),
            "safe_exposed_count": skill_registry.get("safe_exposed_count", 0),
            "exposed_unexecutable_operations": skill_registry.get("exposed_unexecutable_operations", []),
            "by_source_status": skill_registry.get("by_source_status", []),
            "status_matrix": skill_registry.get("status_matrix", []),
            "priority_queue_count": skill_registry.get("priority_queue_count", 0),
            "priority_queue": skill_registry.get("priority_queue", []),
            "top_priority_operation": skill_registry.get("top_priority_operation", {}),
        },
        "drift_categories": drift_categories,
        "usable_sources": usable_sources,
        "readonly_sources": readonly_sources,
        "pending_sources": pending_sources,
        "declared_missing_provider": list(drift.get("declared_missing_provider") or [])[:8],
        "provider_missing_declaration": list(drift.get("provider_missing_declaration") or [])[:8],
        "planner_missing_capability": list(planner_drift.get("planner_missing_capability") or [])[:8],
        "capability_missing_planner": list(planner_drift.get("capability_missing_planner") or [])[:8],
    }


def _provider_capability_contract(
    provider_items: dict[str, Any],
    drift: dict[str, Any],
    planner_drift: dict[str, Any],
    contract_health: dict[str, Any],
) -> dict[str, Any]:
    declared = {(item.source, item.operation): item for item in RUNTIME_CAPABILITIES}
    provider_contracts: dict[tuple[str, str], dict[str, Any]] = {}
    source_rows: list[dict[str, Any]] = []
    ungoverned_writes: list[dict[str, Any]] = []
    confirmation_gaps: list[dict[str, Any]] = []
    identity_gaps: list[dict[str, Any]] = []
    pending_declared: list[dict[str, Any]] = []

    for source, payload in sorted(provider_items.items()):
        contracts = payload.get("operation_contracts") if isinstance(payload.get("operation_contracts"), dict) else {}
        declared_count = 0
        provider_count = 0
        aligned_count = 0
        write_count = 0
        ungoverned_write_count = 0
        for operation, contract in sorted(contracts.items()):
            if not isinstance(contract, dict):
                continue
            key = (source, str(operation))
            provider_contracts[key] = contract
            provider_count += 1
            if key in declared:
                declared_count += 1
                aligned_count += 1
            is_write = bool(contract.get("is_write"))
            if is_write:
                write_count += 1
            if is_write and key not in declared:
                ungoverned_write_count += 1
                ungoverned_writes.append({"source": source, "operation": operation})
            if is_write and not bool(contract.get("requires_confirmation")):
                confirmation_gaps.append({"source": source, "operation": operation})
            if is_write and str(contract.get("execution_identity") or "") != "user":
                identity_gaps.append({"source": source, "operation": operation, "execution_identity": contract.get("execution_identity") or ""})
            if key in declared and bool(contract.get("pending")):
                pending_declared.append({"source": source, "operation": operation, "strategy": declared[key].strategy})
        source_rows.append(
            {
                "source": source,
                "declared_count": declared_count,
                "provider_operation_count": provider_count,
                "aligned_count": aligned_count,
                "write_count": write_count,
                "ungoverned_write_count": ungoverned_write_count,
                "pending_count": int(payload.get("pending_count") or 0),
                "contract_issue_count": len(
                    [
                        issue
                        for issue in contract_health.get("issues") or []
                        if isinstance(issue, dict) and str(issue.get("source") or "") == source
                    ]
                ),
            }
        )

    declared_missing_count = int(drift.get("declared_missing_provider_count") or 0)
    provider_missing_count = int(drift.get("provider_missing_declaration_count") or 0)
    planner_missing_count = int(planner_drift.get("planner_missing_capability_count") or 0)
    capability_unused_count = int(planner_drift.get("capability_missing_planner_count") or 0)
    issue_count = (
        declared_missing_count
        + provider_missing_count
        + planner_missing_count
        + len(ungoverned_writes)
        + len(confirmation_gaps)
        + len(identity_gaps)
        + len(pending_declared)
    )
    blocking_count = len(ungoverned_writes) + len(confirmation_gaps)
    status = "healthy"
    label = "Capability / Provider / Planner 已对齐"
    if blocking_count:
        status = "blocked"
        label = "存在未纳管写操作或确认缺口"
    elif issue_count:
        status = "needs_attention"
        label = "Capability / Provider / Planner 仍有缺口"
    return {
        "status": status,
        "label": label,
        "declared_count": len(declared),
        "provider_operation_count": len(provider_contracts),
        "aligned_count": len([key for key in provider_contracts if key in declared]),
        "declared_missing_provider_count": declared_missing_count,
        "provider_missing_declaration_count": provider_missing_count,
        "planner_missing_capability_count": planner_missing_count,
        "capability_missing_planner_count": capability_unused_count,
        "ungoverned_write_count": len(ungoverned_writes),
        "confirmation_gap_count": len(confirmation_gaps),
        "identity_gap_count": len(identity_gaps),
        "pending_declared_count": len(pending_declared),
        "issue_count": issue_count,
        "blocking_count": blocking_count,
        "source_rows": source_rows,
        "ungoverned_writes": ungoverned_writes[:8],
        "confirmation_gaps": confirmation_gaps[:8],
        "identity_gaps": identity_gaps[:8],
        "pending_declared": pending_declared[:8],
    }


def _provider_source_readiness(provider_items: dict[str, Any], contract_health: dict[str, Any]) -> list[dict[str, Any]]:
    issues = contract_health.get("issues") if isinstance(contract_health.get("issues"), list) else []
    by_source_issue_count: dict[str, int] = {}
    by_source_blocking_count: dict[str, int] = {}
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        source = str(issue.get("source") or "unknown")
        by_source_issue_count[source] = by_source_issue_count.get(source, 0) + 1
        if issue.get("severity") == "critical":
            by_source_blocking_count[source] = by_source_blocking_count.get(source, 0) + 1
    rows: list[dict[str, Any]] = []
    for source, payload in sorted(provider_items.items()):
        installed = int(payload.get("installed_count") or 0)
        pending = int(payload.get("pending_count") or 0)
        readonly = int(payload.get("local_readonly_count") or 0)
        issue_count = by_source_issue_count.get(source, 0)
        blocking_count = by_source_blocking_count.get(source, 0)
        if blocking_count:
            status = "blocked"
            label = "存在阻断"
        elif issue_count or pending:
            status = "needs_attention"
            label = "需要补齐"
        elif installed:
            status = "ready"
            label = "可用"
        elif readonly:
            status = "readonly"
            label = "只读占位"
        else:
            status = "empty"
            label = "未接入"
        rows.append(
            {
                "source": source,
                "status": status,
                "label": label,
                "installed": installed,
                "pending": pending,
                "readonly": readonly,
                "issue_count": issue_count,
                "blocking_count": blocking_count,
            }
        )
    status_order = {"blocked": 0, "needs_attention": 1, "empty": 2, "readonly": 3, "ready": 4}
    return sorted(rows, key=lambda item: (status_order.get(str(item.get("status")), 9), str(item.get("source") or "")))


def _provider_priority_gaps(
    *,
    contract_health: dict[str, Any],
    drift: dict[str, Any],
    planner_drift: dict[str, Any],
    pending_sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for issue in contract_health.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        gaps.append(
            {
                "source": issue.get("source") or "",
                "operation": issue.get("operation") or "",
                "severity": issue.get("severity") or "medium",
                "label": issue.get("label") or issue.get("kind") or "Provider 契约问题",
                "next_step": issue.get("recommendation") or "",
            }
        )
    for item in pending_sources:
        source = str(item.get("source") or "")
        operations = item.get("operations") if isinstance(item.get("operations"), list) else []
        if operations:
            gaps.append(
                {
                    "source": source,
                    "operation": "、".join(str(op) for op in operations[:3]),
                    "severity": "medium",
                    "label": "Provider 操作待接入",
                    "next_step": "接真实飞书原子能力，或将 Capability 标记为未安装。",
                }
            )
    for key, label in (
        ("planner_missing_capability", "Planner 策略缺少 Capability"),
        ("declared_missing_provider", "Capability 缺少 Provider 实现"),
    ):
        rows = planner_drift.get(key) if key.startswith("planner") else drift.get(key)
        if isinstance(rows, list):
            for row in rows[:3]:
                if not isinstance(row, dict):
                    continue
                gaps.append(
                    {
                        "source": row.get("source") or "",
                        "operation": row.get("operation") or row.get("strategy") or "",
                        "severity": "high",
                        "label": label,
                        "next_step": "同步 Planner、Capability Registry 和 Provider Registry。",
                    }
                )
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return sorted(gaps, key=lambda item: severity_order.get(str(item.get("severity") or "medium"), 2))[:10]


def _runtime_readiness_score(
    *,
    drift_count: int,
    planner_drift_count: int,
    pending_count: int,
    contract_issue_count: int,
    contract_blocking_count: int,
) -> dict[str, Any]:
    penalties = [
        {
            "kind": "contract_blocking",
            "label": "契约阻断",
            "count": contract_blocking_count,
            "points": contract_blocking_count * 18,
            "reason": "写操作确认、执行身份或 Capability 治理存在阻断风险。",
        },
        {
            "kind": "contract_issue",
            "label": "契约问题",
            "count": max(contract_issue_count - contract_blocking_count, 0),
            "points": max(contract_issue_count - contract_blocking_count, 0) * 8,
            "reason": "Provider 原子能力和 Runtime 契约存在不一致。",
        },
        {
            "kind": "provider_drift",
            "label": "Provider 漂移",
            "count": drift_count,
            "points": drift_count * 6,
            "reason": "Capability 与 Provider 实现不一致。",
        },
        {
            "kind": "planner_drift",
            "label": "Planner 漂移",
            "count": planner_drift_count,
            "points": planner_drift_count * 5,
            "reason": "Planner 策略与 Capability 清单不一致。",
        },
        {
            "kind": "pending_operation",
            "label": "待接原子能力",
            "count": pending_count,
            "points": pending_count * 3,
            "reason": "Provider 已预留但真实原子能力未接通。",
        },
    ]
    active_penalties = [item for item in penalties if int(item.get("count") or 0) > 0]
    score = max(0, 100 - sum(int(item.get("points") or 0) for item in active_penalties))
    if score >= 90:
        label = "可进入端到端强化"
    elif score >= 75:
        label = "基本可用，仍需补齐"
    elif score >= 55:
        label = "可调试，但不稳定"
    else:
        label = "架构仍需集中修复"
    return {
        "score": score,
        "label": label,
        "penalty_count": len(active_penalties),
        "penalties": active_penalties[:8],
    }


def _runtime_drift_categories(
    drift: dict[str, Any],
    planner_drift: dict[str, Any],
    pending_sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    provider_missing_declaration = [
        item for item in (drift.get("provider_missing_declaration") or []) if isinstance(item, dict)
    ]
    provider_missing_write_count = len([item for item in provider_missing_declaration if item.get("is_write")])
    provider_missing_read_count = len(provider_missing_declaration) - provider_missing_write_count
    pending_count = sum(int(item.get("count") or 0) for item in pending_sources if isinstance(item, dict))
    categories = [
        {
            "kind": "declared_missing_provider",
            "label": "已声明但 Provider 未实现",
            "count": int(drift.get("declared_missing_provider_count") or 0),
            "priority": 1,
            "reason": "Planner 可能规划到这些能力，但执行层接不住。",
        },
        {
            "kind": "provider_write_missing_declaration",
            "label": "写入能力已实现但未纳入 Capability",
            "count": provider_missing_write_count,
            "priority": 2,
            "reason": "动作能力存在但未被 Runtime 治理，容易绕过确认和权限语义。",
        },
        {
            "kind": "planner_missing_capability",
            "label": "Planner 会规划但 Capability 未声明",
            "count": int(planner_drift.get("planner_missing_capability_count") or 0),
            "priority": 3,
            "reason": "策略层和能力清单不一致，状态页和权限决策会失真。",
        },
        {
            "kind": "pending_operations",
            "label": "待安装原子能力",
            "count": pending_count,
            "priority": 4,
            "reason": "Provider 已预留操作，但真实飞书原子能力还没接通。",
        },
        {
            "kind": "provider_read_missing_declaration",
            "label": "读取能力已实现但未纳入 Capability",
            "count": provider_missing_read_count,
            "priority": 5,
            "reason": "查询能力存在但未被 Runtime 能力地图管理。",
        },
        {
            "kind": "capability_missing_planner",
            "label": "Capability 已声明但 Planner 未使用",
            "count": int(planner_drift.get("capability_missing_planner_count") or 0),
            "priority": 6,
            "reason": "能力可用但自然语言入口还没有规划到。",
        },
    ]
    return [item for item in categories if item["count"] > 0]


def _capability_provider_drift(provider_items: dict[str, Any]) -> dict[str, Any]:
    declared = {(item.source, item.operation): item for item in RUNTIME_CAPABILITIES}
    provider_ops: set[tuple[str, str]] = set()
    for source, payload in provider_items.items():
        for operation in payload.get("installed_operations") or []:
            provider_ops.add((source, str(operation)))
        for operation in payload.get("local_readonly_operations") or []:
            provider_ops.add((source, str(operation)))
        for operation in payload.get("pending_operations") or []:
            provider_ops.add((source, str(operation)))

    declared_missing_provider = [
        {
            "strategy": capability.strategy,
            "source": source,
            "operation": operation,
            "label": capability.label,
        }
        for (source, operation), capability in declared.items()
        if (source, operation) not in provider_ops
    ]
    provider_missing_declaration = []
    for source, operation in sorted(provider_ops):
        if (source, operation) in declared:
            continue
        contract = (
            provider_items.get(source, {}).get("operation_contracts", {}).get(operation, {})
            if isinstance(provider_items.get(source, {}).get("operation_contracts"), dict)
            else {}
        )
        provider_missing_declaration.append(
            {
                "source": source,
                "operation": operation,
                "is_write": bool(contract.get("is_write")),
                "requires_confirmation": bool(contract.get("requires_confirmation")),
            }
        )
    return {
        "declared_missing_provider_count": len(declared_missing_provider),
        "provider_missing_declaration_count": len(provider_missing_declaration),
        "declared_missing_provider": declared_missing_provider,
        "provider_missing_declaration": provider_missing_declaration,
    }


def _planner_capability_drift() -> dict[str, Any]:
    strategies = strategy_registry()
    declared_pairs = {(item.strategy, item.source) for item in RUNTIME_CAPABILITIES}
    planner_pairs = {
        (strategy, source)
        for strategy, sources in strategies.items()
        for source in sources
    }
    planner_missing_capability = [
        {"strategy": strategy, "source": source}
        for strategy, source in sorted(planner_pairs)
        if strategy not in {"smalltalk", "action_trace"} and (strategy, source) not in declared_pairs
    ]
    capability_missing_planner = [
        {"strategy": strategy, "source": source}
        for strategy, source in sorted(declared_pairs)
        if (strategy, source) not in planner_pairs
    ]
    return {
        "planner_missing_capability_count": len(planner_missing_capability),
        "capability_missing_planner_count": len(capability_missing_planner),
        "planner_missing_capability": planner_missing_capability,
        "capability_missing_planner": capability_missing_planner,
    }
