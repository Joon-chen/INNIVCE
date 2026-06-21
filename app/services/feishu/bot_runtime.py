import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import FeishuAppConfig
from app.core.config import settings
from app.services.agent.policies import BotActor
from app.services.feishu.cli_profile import feishu_app_cli_profile
from app.services.feishu.identity import BotIdentity
from app.services.runtime_v5.action_observer import load_action_trace, load_runtime_decision_trace, record_action_trace, record_runtime_decision_trace
from app.services.runtime_v5.capabilities import capabilities_for_strategy, capability_summary, label_for_strategy, route_path_for_result
from app.services.runtime_v5.context import build_runtime_context, load_result_context_events, load_session_context, save_result_context, save_session_context
from app.services.runtime_v5.diagnostics import provider_registry_diagnostics, runtime_trace_summary
from app.services.runtime_v5.provider_snapshot import build_runtime_provider_snapshot
from app.services.runtime_v5.bot_diagnostics import runtime_v5_diagnostics_snapshot_base
from app.services.runtime_v5.bot_trace import runtime_v5_bot_trace_payload, runtime_v5_disabled_trace_payload
from app.services.runtime_v5.feishu_resource_providers import build_feishu_provider_registry
from app.services.runtime_v5.models import ResultContext
from app.services.runtime_v5.runtime import run_runtime_v5

_APPROVAL_BATCH_SELECTION_KEY = "runtime_v5_approval_batch_selection"


def _manual_runtime_path_for_strategy(strategy: str) -> dict[str, str]:
    if strategy == "approval_workbench":
        return {
            "runtime_mode": "manual_fast_path",
            "runtime_path_kind": "approval_workbench_path",
            "bypass_reason": "用户显式请求审批增强工作台，暂由快速工作台路径承接，并补齐 V5 诊断快照。",
        }
    if strategy == "approval_query_fast":
        return {
            "runtime_mode": "manual_fast_path",
            "runtime_path_kind": "approval_fast_path",
            "bypass_reason": "审批列表采用快速回复路径，先返回摘要，再补齐 V5 诊断快照。",
        }
    return {
        "runtime_mode": "manual_fast_path",
        "runtime_path_kind": "manual_provider_path",
        "bypass_reason": "该路径由手工 Provider 编排承接，已纳入 V5 诊断快照。",
    }


@dataclass(frozen=True)
class BotRuntimeAnswer:
    answer: str
    trace_payload: dict[str, Any]


def employee_bot_answer(
    db: Session,
    app_config: FeishuAppConfig,
    question: str,
    normalized: str,
    identity: BotIdentity,
    chat_id: str | None,
) -> str:
    return employee_bot_answer_result(db, app_config, question, normalized, identity, chat_id).answer


def employee_bot_answer_result(
    db: Session,
    app_config: FeishuAppConfig,
    question: str,
    normalized: str,
    identity: BotIdentity,
    chat_id: str | None,
) -> BotRuntimeAnswer:
    if settings.feishu_bot_runtime_v5_enabled:
        runtime_context = build_runtime_context(
            message=question,
            identity=identity,
            company_id=app_config.company_id,
            chat_id=chat_id,
        )
        providers = build_feishu_provider_registry(
            db=db,
            cli_profile=feishu_app_cli_profile(app_config),
        )
        envelope = run_runtime_v5(
            context=runtime_context,
            providers=providers,
        )
        route_path = _runtime_v5_route_path(envelope)
        route_label = _runtime_v5_route_label(envelope)
        requires_confirmation = bool(envelope.composed.metadata.get("requires_confirmation"))
        execution_status = envelope.execution.status if envelope.execution else (
            "pending_confirmation" if requires_confirmation else "not_executed"
        )
        runtime_summary = runtime_trace_summary(envelope)
        capability = capability_summary()
        provider_registry = provider_registry_diagnostics(providers)
        runtime_provider_snapshot = build_runtime_provider_snapshot(providers)
        current_capability_readiness = _current_capability_readiness(
            strategy=str(runtime_summary.get("strategy") or ""),
            sources=tuple(str(source) for source in (runtime_summary.get("sources") if isinstance(runtime_summary.get("sources"), list) else []) if str(source)),
            provider_registry=provider_registry,
        )
        pending_cleanup = _cleanup_expired_pending_confirmations(chat_id)
        diagnostics_snapshot = runtime_v5_diagnostics_snapshot_base(
            runtime_summary=runtime_summary,
            current_capability_readiness=current_capability_readiness,
            pending_action=_runtime_pending_action_summary(chat_id),
            pending_approval=_runtime_pending_approval_summary(chat_id),
            pending_cleanup=pending_cleanup,
            current_approval=_runtime_current_approval_summary(chat_id),
            result_context_events=load_result_context_events(chat_id),
            capability_summary=capability,
            provider_registry=provider_registry,
            runtime_provider_snapshot=runtime_provider_snapshot,
            action_trace=_runtime_action_trace(chat_id),
            decision_trace=_runtime_decision_trace(chat_id),
        )
        _apply_current_capability_readiness_to_snapshot(diagnostics_snapshot)
        _apply_source_execution_contract_to_snapshot(diagnostics_snapshot)
        _apply_result_context_event_summary_to_snapshot(diagnostics_snapshot)
        _apply_result_context_quality_to_snapshot(diagnostics_snapshot)
        _apply_provider_registry_contract_to_snapshot(diagnostics_snapshot)
        _apply_pending_confirmation_to_snapshot(diagnostics_snapshot)
        _apply_migration_queue_to_snapshot(diagnostics_snapshot)
        _apply_retired_source_contract_to_snapshot(diagnostics_snapshot)
        _apply_snapshot_integrity_to_snapshot(diagnostics_snapshot)
        _record_runtime_decision_trace(
            chat_id=chat_id,
            route_path=route_path,
            route_label=route_label,
            execution_status=execution_status,
            runtime_summary=runtime_summary,
            diagnostics_snapshot=diagnostics_snapshot,
        )
        diagnostics_snapshot["decision_trace"] = _runtime_decision_trace(chat_id)
        _save_runtime_diagnostics_snapshot(
            chat_id,
            diagnostics_snapshot,
        )
        answer = envelope.composed.answer
        if envelope.intent.intent == "runtime_status":
            answer = _runtime_status_answer_from_snapshot(
                diagnostics_snapshot,
                detail=_runtime_status_detail_requested(question),
            )
        elif envelope.intent.intent == "governance_view":
            answer = _governance_view_answer_from_snapshot(diagnostics_snapshot, message=question)
        return BotRuntimeAnswer(
            answer=answer,
            trace_payload=runtime_v5_bot_trace_payload(
                envelope=envelope,
                route_path=route_path,
                route_label=route_label,
                requires_confirmation=requires_confirmation,
                execution_status=execution_status,
                runtime_summary=runtime_summary,
                capability_summary=capability,
                provider_registry=provider_registry,
                runtime_provider_snapshot=runtime_provider_snapshot,
            ),
        )

    return BotRuntimeAnswer(
        answer="V5 Runtime 当前未启用，已停止回退旧运行链路。请先开启 V5 后再继续。",
        trace_payload=runtime_v5_disabled_trace_payload(),
    )


def employee_bot_approval_fast_answer_result(
    db: Session,
    app_config: FeishuAppConfig,
    question: str,
    normalized: str,
    identity: BotIdentity,
    chat_id: str | None,
) -> BotRuntimeAnswer:
    runtime_answer = employee_bot_answer_result(
        db,
        app_config,
        question,
        normalized,
        identity,
        chat_id,
    )
    if chat_id:
        _clear_approval_batch_selection(chat_id)
    return runtime_answer


def employee_bot_approval_workbench_answer_result(
    db: Session,
    app_config: FeishuAppConfig,
    question: str,
    normalized: str,
    identity: BotIdentity,
    chat_id: str | None,
) -> BotRuntimeAnswer:
    runtime_answer = employee_bot_answer_result(
        db,
        app_config,
        question,
        normalized,
        identity,
        chat_id,
    )
    if chat_id:
        _clear_approval_batch_selection(chat_id)
    return runtime_answer


def _runtime_v5_route_path(envelope) -> str:
    if envelope.composed.metadata.get("requires_confirmation"):
        return "runtime_v5_confirmation"
    result_type = envelope.composed.result_context.result_type if envelope.composed.result_context else ""
    if envelope.plan.strategy == "approval_query" or result_type == "approval_list":
        return "feishu_approval_task_query"
    if result_type in {"people_search", "department_members", "organization_snapshot"}:
        return "feishu_contact_organization_snapshot"
    if result_type == "base_export":
        return "bitable_qa"
    if result_type == "task_list":
        return "feishu_task_query"
    if envelope.execution and envelope.execution.status in {"error", "partial", "denied"}:
        return "runtime_v5"
    if envelope.plan.strategy == "company_intro":
        return "company_qa"
    if envelope.plan.strategy == "risk_analysis":
        return "owner_cockpit"
    return route_path_for_result(envelope.plan.strategy, result_type) or "runtime_v5"


def _clear_approval_batch_selection(chat_id: str | None) -> None:
    if not chat_id:
        return
    session_context = load_session_context(chat_id)
    changed = False
    if _APPROVAL_BATCH_SELECTION_KEY in session_context:
        session_context.pop(_APPROVAL_BATCH_SELECTION_KEY, None)
        changed = True
    if "runtime_v5_current_approval_item" in session_context:
        session_context.pop("runtime_v5_current_approval_item", None)
        changed = True
    if not changed:
        return
    save_session_context(chat_id, session_context)


def _tag_runtime_items(items: tuple[dict[str, Any], ...], source: str) -> tuple[dict[str, Any], ...]:
    return tuple({**item, "_runtime_v5_source": source} for item in items)


def _save_runtime_diagnostics_snapshot(chat_id: str | None, payload: dict[str, Any]) -> None:
    if not chat_id:
        return
    session_context = load_session_context(chat_id)
    session_context["runtime_v5_last_diagnostics"] = payload
    save_session_context(chat_id, session_context)


def _save_manual_runtime_diagnostics_snapshot(
    *,
    chat_id: str | None,
    runtime_context: Any,
    providers: dict[str, Any],
    strategy: str,
    sources: list[str],
    result_context: ResultContext,
    provider_result: Any,
) -> None:
    status = str(getattr(provider_result, "status", "") or "unknown")
    provider_registry = provider_registry_diagnostics(providers)
    runtime_provider_snapshot = build_runtime_provider_snapshot(providers)
    generated_at = datetime.now(timezone.utc).isoformat()
    runtime_path = _manual_runtime_path_for_strategy(strategy)
    runtime_mode = runtime_path["runtime_mode"]
    runtime_path_kind = runtime_path["runtime_path_kind"]
    bypass_reason = runtime_path["bypass_reason"]
    explicit_workbench_path = strategy == "approval_workbench"
    provider_results = result_context.metadata.get("provider_results", [])
    manual_provider_summary = _manual_provider_summary(provider_results)
    source_execution_contract = _manual_source_execution_contract(sources, manual_provider_summary)
    result_context_events = load_result_context_events(chat_id)
    pipeline_frames = _manual_pipeline_frames(strategy=strategy, sources=sources, status=status, result_context=result_context)
    profile_health = _manual_profile_health(runtime_context)
    path_requires_migration = runtime_mode == "manual_fast_path"
    health_matrix = _manual_health_matrix(status=status, profile_health=profile_health, path_requires_migration=path_requires_migration)
    runtime_gate = _manual_runtime_gate(status=status, profile_health=profile_health, path_requires_migration=path_requires_migration)
    repair_plan = _manual_repair_plan(runtime_gate)
    manual_migration_item = _manual_fast_path_migration_item(
        strategy=strategy,
        bypass_reason=bypass_reason,
    )
    followup_fields = result_context.metadata.get("followup_fields")
    if not isinstance(followup_fields, list):
        followup_fields = _manual_result_context_followup_fields(result_context.items)
    item_identity_fields = result_context.metadata.get("item_identity_fields")
    if not isinstance(item_identity_fields, list):
        item_identity_fields = _manual_result_context_identity_fields(result_context.items)
    consume_policy = result_context.metadata.get("consume_policy")
    if not isinstance(consume_policy, dict):
        consume_policy = {
            "prefer_items": True,
            "allow_answer_fallback": False,
            "requires_refresh_when_expired": True,
            "supports_index_followup": bool(result_context.items),
            "supports_detail_followup": bool(result_context.items),
        }
    snapshot_summary = _manual_snapshot_summary(
        generated_at=generated_at,
        runtime_mode=runtime_mode,
        runtime_path_kind=runtime_path_kind,
        bypass_reason=bypass_reason,
        strategy=strategy,
        sources=sources,
        status=status,
        result_context=result_context,
        health_matrix=health_matrix,
        runtime_gate=runtime_gate,
        repair_plan=repair_plan,
    )
    pending_cleanup = _cleanup_expired_pending_confirmations(chat_id)
    snapshot = {
        "diagnostics_version": "v5.diagnostics.manual.1",
        "diagnostics_capabilities": {
            "version": "v5.diagnostics.manual.1",
            "module_count": 16,
            "modules": [
                "snapshot_summary",
                "runtime_mode",
                "current_path_maturity",
                "current_capability_readiness",
                "health_matrix",
                "runtime_gate",
                "repair_plan",
                "provider_registry_readiness",
                "source_execution_contract",
                "result_context_quality",
                "result_context_consume_policy",
                "result_context_event_summary",
                "followup_consume_contract",
                "action_closure",
                "profile_isolation",
                "snapshot_integrity",
            ],
        },
        "generated_at": generated_at,
        "runtime_mode": runtime_mode,
        "runtime_path_kind": runtime_path_kind,
        "bypass_reason": bypass_reason,
        "current_path_maturity": {
            "strategy": strategy,
            "sources": sources,
            "available": True,
            "status": "blocked",
            "label": "显式审批增强工作台路径，未进入完整 V5 主链路" if explicit_workbench_path else "手工快速路径阻断完整 V5 主链路",
            "explicit_user_requested": explicit_workbench_path,
            "bypass_class": "explicit_approval_workbench_enhancement" if explicit_workbench_path else "manual_fast_path",
            "counts": {"manual_fast_path": 1},
            "items": [manual_migration_item],
            "migration_items": [manual_migration_item],
            "migration_count": 1,
        },
        "current_capability_readiness": _current_capability_readiness(
            strategy="approval_query",
            sources=("approval",),
            provider_registry=provider_registry,
        ),
        "debug_locator": _manual_debug_locator(
            generated_at=generated_at,
            strategy=strategy,
            status=status,
            result_context=result_context,
            runtime_gate=runtime_gate,
            repair_plan=repair_plan,
            provider_results=provider_results,
        ),
        "snapshot_summary": snapshot_summary,
        "snapshot_lifecycle": {
            "generated_at": generated_at,
            "status": "stable" if status == "success" and not repair_plan.get("item_count") else "needs_attention",
            "label": "快照稳定" if status == "success" and not repair_plan.get("item_count") else "需要关注",
            "has_execution": True,
            "has_answer": bool(getattr(provider_result, "answer", "")),
            "has_repair_plan": bool(repair_plan.get("item_count")),
            "is_current_turn_snapshot": True,
        },
        "health_matrix": health_matrix,
        "runtime_gate": runtime_gate,
        "repair_plan": repair_plan,
        "health": {
            "stage": "success" if status == "success" else status,
            "has_answer": bool(getattr(provider_result, "answer", "")),
            "has_result_context": True,
            "provider_error_count": 0 if status == "success" else 1,
            "provider_errors": [] if status == "success" else [{"source": getattr(provider_result, "source", ""), "error_type": getattr(provider_result, "error", "")}],
        },
        "intent": "approval_query",
        "question_type": "query",
        "data_scope": "self",
        "strategy": strategy,
        "sources": sources,
        "result_context": {
            "available": True,
            "result_type": result_context.result_type,
            "query_id": result_context.query_id,
            "count": result_context.count,
            "display_count": result_context.metadata.get("display_count", result_context.count),
            "item_count": result_context.metadata.get("item_count", result_context.count),
            "actionable": bool(result_context.metadata.get("actionable", False)),
            "context_kind": result_context.metadata.get("context_kind", "query_result"),
            "sources": result_context.metadata.get("result_sources") or sources,
            "item_summaries": _runtime_result_item_summaries(result_context.items),
        },
        "provider_results": provider_results,
        "provider_summary": manual_provider_summary,
        "source_execution_contract": source_execution_contract,
        "pipeline_frames": pipeline_frames,
        "pipeline_health": {
            "status": "blocked",
            "label": "Pipeline 存在手工旁路，未按完整 V5 主链路执行",
            "issue_count": 1,
            "stage_count": len(pipeline_frames),
            "issues": [
                {
                    "kind": "manual_fast_path_observed",
                    "severity": "critical",
                    "label": "快速路径阶段为旁路观测",
                    "detail": strategy,
                    "recommendation": "把该路径迁入完整 V5 主链路，Pipeline 阶段必须由真实 Runtime 顺序执行。",
                }
            ],
        },
        "pipeline_constitution_contract": {
            "status": "needs_attention",
            "label": "V5 主链路契约需要关注",
            "expected_stages": [
                "pre_gateway",
                "result_followup_detector",
                "intent_recognition",
                "task_planner",
                "permission_check",
                "capability_router",
                "execution",
                "answer_composer",
            ],
            "observed_stages": [str(frame.get("name") or "") for frame in pipeline_frames if isinstance(frame, dict)],
            "missing_stages": [],
            "stage_count": len(pipeline_frames),
            "issue_count": 1,
            "issues": [
                {
                    "kind": "manual_fast_path_bypasses_runtime",
                    "severity": "critical",
                    "label": "手工快速路径绕过完整 Runtime",
                    "detail": strategy,
                    "recommendation": "迁入完整 V5 Runtime，由 Planner 决定来源、Permission 检查动作、Router 执行 Provider、Composer 统一出答案。",
                }
            ],
        },
        "timing_health": _manual_timing_health(provider_results),
        "result_context_events": result_context_events,
        "result_context_event_summary": _manual_result_context_event_summary(result_context_events),
        "profile_health": profile_health,
        "context_health": {
            "status": "healthy",
            "label": "Runtime Context 已加载",
            "issue_count": 0,
            "identity_available": True,
            "session_available": True,
            "profile_available": True,
            "current_message_available": True,
            "result_context_available": True,
            "issues": [],
        },
        "followup_health": {
            "status": "healthy",
            "label": "非追问路径",
            "issue_count": 0,
            "uses_previous_result": False,
            "followup_type": "",
            "context_kind": "query_result",
            "item_count": result_context.count,
            "issues": [],
        },
        "followup_consume_contract": {
            "status": "healthy",
            "label": "非追问，不消费上一轮结果",
            "uses_previous_result": False,
            "followup_type": "",
            "context_kind": "query_result",
            "item_count": result_context.count,
            "prefer_items": bool(consume_policy.get("prefer_items", False)),
            "allow_answer_fallback": bool(consume_policy.get("allow_answer_fallback", False)),
            "followup_fields": followup_fields,
            "followup_field_count": len(followup_fields),
            "item_identity_fields": item_identity_fields,
            "item_identity_field_count": len(item_identity_fields),
            "issue_count": 0,
            "issues": [],
        },
        "decision_health": {
            "status": "healthy",
            "label": "Intent/Planner 手工路径正常",
            "issue_count": 0,
            "question_type": "query",
            "data_scope": "self",
            "strategy": strategy,
            "sources": sources,
            "confidence": 0.95,
            "should_clarify": False,
            "missing_params": [],
            "issues": [],
        },
        "router_health": {
            "status": "healthy",
            "label": "Router 手工路径已覆盖计划来源",
            "issue_count": 0,
            "planned_sources": sources,
            "executed_unique_sources": sources,
            "coverage_percent": 100,
            "issues": [],
        },
        "execution_health": {
            "status": "healthy" if status == "success" else "needs_attention",
            "label": "Execution 正常" if status == "success" else "Execution 需要关注",
            "issue_count": 0 if status == "success" else 1,
            "execution_status": status,
            "provider_count": len(provider_results) if isinstance(provider_results, list) else 0,
            "success_count": 1 if status == "success" else 0,
            "error_count": 0 if status == "success" else 1,
            "denied_count": 0,
            "skipped_count": 0,
            "has_result_context": True,
            "issues": [] if status == "success" else [{"kind": "provider_status", "severity": "high", "label": "Provider 未成功", "detail": status, "recommendation": "优先检查审批 Provider 返回状态和错误信息。"}],
        },
        "answer_health": {
            "status": "healthy" if getattr(provider_result, "answer", "") else "needs_attention",
            "label": "答案已生成" if getattr(provider_result, "answer", "") else "答案为空",
            "issue_count": 0 if getattr(provider_result, "answer", "") else 1,
            "answer_chars": len(str(getattr(provider_result, "answer", "") or "")),
            "requires_confirmation": False,
            "issues": [],
        },
        "result_context_quality": {
            "available": True,
            "status": "healthy",
            "label": "Result Context 可追问",
            "issue_count": 0,
            "context_kind": "query_result",
            "result_type": result_context.result_type,
            "count": result_context.count,
            "item_count": result_context.metadata.get("item_count", result_context.count),
            "provider_evidence_count": len(provider_results) if isinstance(provider_results, list) else 0,
            "followup_fields": followup_fields,
            "followup_field_count": len(followup_fields),
            "item_identity_fields": item_identity_fields,
            "item_identity_field_count": len(item_identity_fields),
            "consume_policy": consume_policy,
            "prefer_items": bool(consume_policy.get("prefer_items", False)),
            "allow_answer_fallback": bool(consume_policy.get("allow_answer_fallback", False)),
            "age_seconds": 0,
            "expires_in_seconds": 0,
            "issues": [],
        },
        "action_timeline": {"available": False, "event_count": 0, "events": []},
        "action_closure": {"latest_available": False},
        "constitution_guard": {
            "healthy": False,
            "failed_count": 1,
            "checks": [
                {"key": "manual_fast_path_visible", "label": "手工快速路径可观测", "ok": True, "detail": strategy, "severity": "medium", "recommendation": ""},
                {"key": "current_path_full_runtime", "label": "当前策略路径完整 V5", "ok": False, "detail": "手工快速路径阻断完整 V5 主链路", "severity": "critical", "recommendation": "把审批快速摘要/工作台迁移到 V5 Planner/Router/Provider/Composer 主链路。"},
                {"key": "profile_isolated", "label": "Profile 只影响回答呈现", "ok": profile_health.get("status") == "healthy", "detail": profile_health.get("label", ""), "severity": "high", "recommendation": "Profile 不得影响权限、数据范围、路由来源或执行身份。"},
            ],
        },
        "permission": {
            "available": True,
            "allowed": True,
            "reason": "",
            "requires_confirmation": False,
            "execution_identity": "bot",
            "confirmation_reasons": [],
            "source_capabilities": [],
            "requested_identity": "",
            "default_identity": "bot",
        },
        "pending_action": _runtime_pending_action_summary(chat_id),
        "pending_approval": _runtime_pending_approval_summary(chat_id),
        "pending_cleanup": pending_cleanup,
        "current_approval": _runtime_current_approval_summary(chat_id),
        "capability_summary": capability_summary(),
        "provider_registry": provider_registry,
        "runtime_provider_snapshot": runtime_provider_snapshot,
        "action_trace": _runtime_action_trace(chat_id),
        "decision_trace": _runtime_decision_trace(chat_id),
    }
    _apply_current_capability_readiness_to_snapshot(snapshot)
    _apply_source_execution_contract_to_snapshot(snapshot)
    _apply_result_context_event_summary_to_snapshot(snapshot)
    _apply_result_context_quality_to_snapshot(snapshot)
    _apply_provider_registry_contract_to_snapshot(snapshot)
    _apply_pending_confirmation_to_snapshot(snapshot)
    _apply_migration_queue_to_snapshot(snapshot)
    _apply_retired_source_contract_to_snapshot(snapshot)
    _apply_snapshot_integrity_to_snapshot(snapshot)
    _record_runtime_decision_trace(
        chat_id=chat_id,
        route_path="feishu_approval_task_query",
        route_label="待审批",
        execution_status=status,
        runtime_summary={
            "intent": "approval_query",
            "question_type": "query",
            "data_scope": "self",
            "strategy": strategy,
            "sources": sources,
            "runtime_mode": runtime_mode,
            "runtime_path_kind": runtime_path_kind,
            "bypass_reason": bypass_reason,
        },
        diagnostics_snapshot=snapshot,
    )
    snapshot["decision_trace"] = _runtime_decision_trace(chat_id)
    _save_runtime_diagnostics_snapshot(chat_id, snapshot)


def _runtime_result_item_summaries(items: tuple[dict[str, Any], ...]) -> list[str]:
    summaries: list[str] = []
    for item in list(items)[:3]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("summary") or item.get("title") or item.get("name") or item.get("subject") or "").strip()
        applicant = str(item.get("applicant") or "").strip()
        amount = str(item.get("amount") or "").strip()
        parts = [value for value in (title, applicant, amount) if value]
        if parts:
            summaries.append("｜".join(parts))
    return summaries


def _manual_pipeline_frames(*, strategy: str, sources: list[str], status: str, result_context: ResultContext) -> list[dict[str, Any]]:
    observed = {"execution_mode": "manual_fast_path_observed", "bypassed": True, "status": "observed"}
    return [
        {"name": "pre_gateway", "label": "Pre Gateway", **observed},
        {"name": "result_followup_detector", "label": "Result Follow-up Detector", **observed},
        {"name": "intent_recognition", "label": "Intent Recognition", "intent": "approval_query", **observed},
        {"name": "task_planner", "label": "Task Planner", "strategy": strategy, "sources": sources, **observed},
        {"name": "permission_check", "label": "Permission Check", "execution_identity": "bot", **observed},
        {"name": "capability_router", "label": "Capability Router", "sources": sources, **observed},
        {"name": "execution", "label": "Execution", "status": status, "execution_mode": "manual_fast_path_executed", "bypassed": False},
        {"name": "answer_composer", "label": "Answer Composer", "result_type": result_context.result_type, **observed},
    ]


def _manual_profile_health(runtime_context: Any) -> dict[str, Any]:
    profile = getattr(runtime_context, "profile", None)
    return {
        "status": "healthy",
        "label": "Profile 隔离正常",
        "issue_count": 0,
        "profile_available": profile is not None,
        "style": str(getattr(profile, "style", "") or ""),
        "verbosity": str(getattr(profile, "verbosity", "") or ""),
        "use_formatting": bool(getattr(profile, "use_formatting", False)) if profile is not None else False,
        "has_tone_tips": bool(str(getattr(profile, "tone_tips", "") or "").strip()) if profile is not None else False,
        "allowed_effects": ["回答风格", "答案结构", "详细程度"],
        "forbidden_effects": ["权限", "数据范围", "路由来源", "Tool/Skill 选择", "执行身份"],
        "issues": [],
    }


def _manual_health_matrix(*, status: str, profile_health: dict[str, Any], path_requires_migration: bool = False) -> dict[str, Any]:
    execution_status = "healthy" if status == "success" else "needs_attention"
    constitution_status = "needs_attention" if path_requires_migration else "healthy"
    layers = [
        _manual_health_layer("pipeline", "Pipeline", "healthy", 0),
        _manual_health_layer("context", "Runtime Context", "healthy", 0),
        _manual_health_layer("profile", "Profile Isolation", profile_health.get("status"), int(profile_health.get("issue_count") or 0)),
        _manual_health_layer("decision", "Intent/Planner", "healthy", 0),
        _manual_health_layer("router", "Capability Router", "healthy", 0),
        _manual_health_layer("execution", "Execution", execution_status, 0 if status == "success" else 1),
        _manual_health_layer("permission", "Permission", "healthy", 0),
        _manual_health_layer("answer", "Answer", "healthy", 0),
        _manual_health_layer("result_context", "Result Context", "healthy", 0),
        _manual_health_layer("constitution", "Constitution", constitution_status, 1 if path_requires_migration else 0),
    ]
    issue_count = sum(int(item.get("issue_count") or 0) for item in layers)
    unhealthy = [item for item in layers if item.get("status") not in {"healthy", "none"}]
    return {
        "status": "healthy" if not unhealthy else "needs_attention",
        "label": "各层健康" if not unhealthy else f"{len(unhealthy)} 层需要关注",
        "issue_count": issue_count,
        "status_counts": _manual_status_counts(layers),
        "layers": layers,
    }


def _manual_health_layer(key: str, label: str, status: Any, issue_count: int) -> dict[str, Any]:
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


def _manual_status_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _manual_runtime_gate(*, status: str, profile_health: dict[str, Any], path_requires_migration: bool = False) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    if status != "success":
        items.append(
            {
                "key": "manual_provider:status",
                "severity": "high",
                "label": "快速路径 Provider 未成功",
                "detail": status,
                "recommendation": "优先检查审批 Provider 返回和飞书接口错误。",
            }
        )
    if path_requires_migration:
        items.append(
            {
                "key": "runtime_path:manual_fast_path",
                "severity": "critical",
                "label": "手工快速路径阻断完整 V5 主链路",
                "detail": "approval fast/workbench",
                "recommendation": "把审批快速摘要/工作台迁入 V5 Planner/Router/Provider/Composer 主链路，避免继续依赖旁路捷径。",
            }
        )
    for issue in profile_health.get("issues") or []:
        if isinstance(issue, dict):
            items.append(
                {
                    "key": f"profile:{issue.get('kind') or 'unknown'}",
                    "severity": issue.get("severity") or "medium",
                    "label": issue.get("label") or "Profile 隔离问题",
                    "detail": issue.get("detail") or "",
                    "recommendation": issue.get("recommendation") or "",
            }
        )
    fingerprinted_items = [_manual_issue_fingerprint(item) for item in items[:8]]
    return {
        "status": "healthy" if not items else ("blocked" if any(str(item.get("severity") or "") == "critical" for item in items) else "needs_attention"),
        "label": "本轮链路健康" if not items else ("完整 V5 主链路被阻断" if any(str(item.get("severity") or "") == "critical" for item in items) else "需要继续加固"),
        "blocking": any(str(item.get("severity") or "") == "critical" for item in items),
        "issue_count": len(items),
        "primary_issue_fingerprint": fingerprinted_items[0].get("fingerprint", "") if fingerprinted_items else "",
        "primary_issue_key": items[0].get("key", "") if items else "",
        "severity_counts": _manual_severity_counts(items),
        "priority_items": fingerprinted_items,
        "next_step": items[0].get("recommendation", "") if items else "可以继续扩展业务 Provider 或做端到端验证。",
    }

def _manual_fast_path_migration_item(*, strategy: str, bypass_reason: str) -> dict[str, Any]:
    is_workbench = strategy == "approval_workbench"
    stage_plan = [
        {
            "stage": "intent_recognition",
            "label": "意图识别",
            "target": "识别为 approval_query / approval_workbench，并写入 question_type=query、data_scope=self。",
        },
        {
            "stage": "task_planner",
            "label": "任务规划",
            "target": "Planner 输出 strategy=approval_query，sources=[approval]，不直接调用审批接口。",
        },
        {
            "stage": "permission_check",
            "label": "权限检查",
            "target": "查询类按 bot/user 可读身份检查；审批通过/拒绝等 Action 仍进入二次确认。",
        },
        {
            "stage": "capability_router",
            "label": "能力路由",
            "target": "Router 调用 Approval Provider，写入 ProviderResult、耗时、错误分类和来源执行状态。",
        },
        {
            "stage": "answer_composer",
            "label": "答案组织",
            "target": "Composer 根据 Result Context 生成审批工作台/列表卡片，不再由手工路径拼装答案。",
        },
    ]
    return {
        "strategy": strategy,
        "source": "approval",
        "operation": "list_pending" if is_workbench else "list_pending_fast",
        "label": "审批工作台" if is_workbench else "审批快速摘要",
        "route_path": "feishu_approval_task_query",
        "kind": "manual_fast_path",
        "kind_label": "显式审批增强路径" if is_workbench else "显式快速路径",
        "explicit_user_requested": is_workbench,
        "bypass_class": "explicit_approval_workbench_enhancement" if is_workbench else "manual_fast_path",
        "reason": bypass_reason,
        "bypassed_stages": ["intent_recognition", "task_planner", "permission_check", "capability_router", "answer_composer"],
        "migration_stage_plan": stage_plan,
        "target_runtime_path": "完整 V5 主链路：Intent Recognition -> Task Planner -> Permission Check -> Capability Router -> Execution -> Answer Composer",
        "migration_priority": 80 if is_workbench else 70,
        "migration_priority_label": "高" if is_workbench else "中",
        "migration_next_step": "把审批列表/工作台从手工快速回复迁入 approval_query 策略，由 Planner 输出 approval source，再由 Capability Router 调用 Approval Provider 并统一经 Answer Composer 出卡。",
    }


def _manual_repair_plan(runtime_gate: dict[str, Any]) -> dict[str, Any]:
    priority_items = runtime_gate.get("priority_items") if isinstance(runtime_gate.get("priority_items"), list) else []
    items = [
        _manual_issue_fingerprint({
            "severity": item.get("severity") or "medium",
            "label": item.get("label") or "修复项",
            "detail": item.get("detail") or "",
            "next_step": item.get("recommendation") or "",
        })
        for item in priority_items
        if isinstance(item, dict)
    ]
    return {
        "status": "healthy" if not items else "needs_attention",
        "item_count": len(items),
        "primary_issue_fingerprint": items[0].get("fingerprint", "") if items else "",
        "primary_issue_source": items[0].get("source", "") if items else "",
        "severity_counts": _manual_severity_counts(items),
        "items": items,
        "next_step": items[0].get("next_step", "") if items else "",
    }


def _manual_snapshot_summary(
    *,
    generated_at: str,
    runtime_mode: str,
    runtime_path_kind: str,
    bypass_reason: str,
    strategy: str,
    sources: list[str],
    status: str,
    result_context: ResultContext,
    health_matrix: dict[str, Any],
    runtime_gate: dict[str, Any],
    repair_plan: dict[str, Any],
) -> dict[str, Any]:
    runtime_state = {
        "code": "healthy" if runtime_gate.get("status") == "healthy" else "needs_attention",
        "label": "运行健康" if runtime_gate.get("status") == "healthy" else "需要关注",
        "severity": "low" if runtime_gate.get("status") == "healthy" else "medium",
        "reason": "快速路径快照已纳入 V5 诊断口径",
        "latest_action_status": "",
        "latest_action_age_seconds": 0,
        "total_ms": 0,
        "provider_quality_issue_count": 0,
        "health_issue_count": int(health_matrix.get("issue_count") or 0),
    }
    return {
        "version": "v5.diagnostics.manual.1",
        "generated_at": generated_at,
        "runtime_mode": runtime_mode,
        "runtime_path_kind": runtime_path_kind,
        "bypass_reason": bypass_reason,
        "runtime_state": runtime_state,
        "runtime_state_code": runtime_state["code"],
        "runtime_state_label": runtime_state["label"],
        "gate_status": runtime_gate.get("status", ""),
        "gate_label": runtime_gate.get("label", ""),
        "health_status": health_matrix.get("status", ""),
        "health_label": health_matrix.get("label", ""),
        "health_issue_count": int(health_matrix.get("issue_count") or 0),
        "repair_item_count": int(repair_plan.get("item_count") or 0),
        "primary_issue_fingerprint": runtime_gate.get("primary_issue_fingerprint") or repair_plan.get("primary_issue_fingerprint") or "",
        "primary_issue_key": runtime_gate.get("primary_issue_key") or repair_plan.get("primary_issue_source") or "",
        "next_step": repair_plan.get("next_step", "") or runtime_gate.get("next_step", ""),
        "question_type": "query",
        "data_scope": "self",
        "strategy": strategy,
        "sources": sources,
        "execution_status": status,
        "answer_chars": len(str(result_context.answer or "")),
        "total_ms": 0,
        "slowest_stage": "",
        "slowest_ms": 0,
        "provider_quality_issue_count": 0,
        "provider_total_duration_ms": 0,
        "latest_action_status": "",
        "latest_action_age_seconds": 0,
        "latest_action_stuck": False,
        "result_context_kind": result_context.metadata.get("context_kind", ""),
        "result_type": result_context.result_type,
        "result_count": result_context.count,
    }


def _manual_debug_locator(
    *,
    generated_at: str,
    strategy: str,
    status: str,
    result_context: ResultContext,
    runtime_gate: dict[str, Any],
    repair_plan: dict[str, Any],
    provider_results: Any,
) -> dict[str, Any]:
    query_id = str(result_context.query_id or "")
    trace_seed = query_id or f"{strategy}-{generated_at.replace('-', '').replace(':', '').replace('.', '')[-12:]}"
    trace_id = trace_seed[-24:] if len(trace_seed) > 24 else trace_seed
    provider_items = provider_results if isinstance(provider_results, list) else []
    slowest = max(
        [item for item in provider_items if isinstance(item, dict)],
        key=lambda item: int(item.get("duration_ms") or 0),
        default={},
    )
    primary_issue = runtime_gate.get("primary_issue_fingerprint") or repair_plan.get("primary_issue_fingerprint") or ""
    parts = [
        f"trace={trace_id}",
        f"issue={primary_issue or 'none'}",
        f"strategy={strategy}",
        f"state={status}",
    ]
    if slowest.get("source"):
        parts.append(f"provider={slowest.get('source')}:{slowest.get('duration_ms', 0)}ms")
    if query_id:
        parts.append(f"result={query_id[-8:]}")
    return {
        "trace_id": trace_id,
        "query_id_tail": query_id[-12:] if query_id else "",
        "primary_issue_fingerprint": primary_issue,
        "primary_issue_key": runtime_gate.get("primary_issue_key") or repair_plan.get("primary_issue_source") or "",
        "strategy": strategy,
        "runtime_state": "healthy" if status == "success" else "needs_attention",
        "gate_status": runtime_gate.get("status", ""),
        "repair_item_count": int(repair_plan.get("item_count") or 0),
        "slowest_provider_source": slowest.get("source", "") if isinstance(slowest, dict) else "",
        "slowest_provider_ms": int(slowest.get("duration_ms") or 0) if isinstance(slowest, dict) else 0,
        "result_context_kind": result_context.metadata.get("context_kind", ""),
        "result_type": result_context.result_type,
        "result_count": result_context.count,
        "locator": "｜".join(part for part in parts if part),
    }


def _manual_provider_summary(provider_results: Any) -> dict[str, Any]:
    items = provider_results if isinstance(provider_results, list) else []
    success_count = len([item for item in items if isinstance(item, dict) and item.get("status") == "success"])
    error_count = len([item for item in items if isinstance(item, dict) and item.get("status") not in {"success", "", None}])
    total_duration = sum(int(item.get("duration_ms") or 0) for item in items if isinstance(item, dict))
    return {
        "count": len(items),
        "success_count": success_count,
        "error_count": error_count,
        "denied_count": 0,
        "skipped_count": 0,
        "total_duration_ms": total_duration,
        "quality_issue_count": 0,
        "quality_issues": [],
        "items": items,
        "by_source": _manual_provider_by_source(items),
        "recommendations": [],
    }


def _manual_source_execution_contract(sources: list[str], provider_summary: dict[str, Any]) -> dict[str, Any]:
    planned_sources = [str(source) for source in sources if str(source)]
    by_source = provider_summary.get("by_source") if isinstance(provider_summary.get("by_source"), list) else []
    by_source_map = {str(item.get("source") or ""): item for item in by_source if isinstance(item, dict)}
    executed_sources = {source for source in by_source_map if source}
    missing_sources = [source for source in planned_sources if source not in executed_sources]
    return {
        "status": "healthy" if not missing_sources else "needs_attention",
        "label": "Planner 来源均已执行" if not missing_sources else "Planner 来源存在未执行项",
        "planned_sources": planned_sources,
        "executed_sources": sorted(executed_sources),
        "missing_sources": missing_sources,
        "planned_count": len(planned_sources),
        "executed_planned_count": len([source for source in planned_sources if source in executed_sources]),
        "missing_count": len(missing_sources),
        "items": [
            {
                "source": source,
                "planned": True,
                "executed": source in executed_sources,
                "result_count": int(by_source_map.get(source, {}).get("count") or 0),
                "success_count": int(by_source_map.get(source, {}).get("success_count") or 0),
                "error_count": int(by_source_map.get(source, {}).get("error_count") or 0),
                "denied_count": int(by_source_map.get(source, {}).get("denied_count") or 0),
                "skipped_count": int(by_source_map.get(source, {}).get("skipped_count") or 0),
                "operations": by_source_map.get(source, {}).get("operations") if isinstance(by_source_map.get(source, {}).get("operations"), list) else [],
            }
            for source in planned_sources
        ],
    }


def _manual_provider_by_source(items: list[Any]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "unknown")
        entry = grouped.setdefault(source, {"source": source, "count": 0, "success_count": 0, "error_count": 0, "total_duration_ms": 0, "operations": []})
        entry["count"] += 1
        if item.get("status") == "success":
            entry["success_count"] += 1
        elif item.get("status"):
            entry["error_count"] += 1
        entry["total_duration_ms"] += int(item.get("duration_ms") or 0)
        operation = str(item.get("operation") or "")
        if operation and operation not in entry["operations"]:
            entry["operations"].append(operation)
    return list(grouped.values())


def _manual_timing_health(provider_results: Any) -> dict[str, Any]:
    items = provider_results if isinstance(provider_results, list) else []
    slowest = max([item for item in items if isinstance(item, dict)], key=lambda item: int(item.get("duration_ms") or 0), default={})
    total_ms = sum(int(item.get("duration_ms") or 0) for item in items if isinstance(item, dict))
    return {
        "status": "healthy" if total_ms < 5000 else "needs_attention",
        "label": "耗时正常" if total_ms < 5000 else "耗时需关注",
        "issue_count": 0 if total_ms < 5000 else 1,
        "total_ms": total_ms,
        "slowest_stage": "execution" if slowest else "",
        "slowest_ms": int(slowest.get("duration_ms") or 0) if isinstance(slowest, dict) else 0,
        "provider_total_ms": total_ms,
        "slowest_provider_source": slowest.get("source", "") if isinstance(slowest, dict) else "",
        "slowest_provider_ms": int(slowest.get("duration_ms") or 0) if isinstance(slowest, dict) else 0,
        "issues": [] if total_ms < 5000 else [{"kind": "manual_provider_slow", "severity": "medium", "label": "快速路径 Provider 较慢", "detail": f"{total_ms}ms", "recommendation": "继续拆解审批列表、附件摘要和 LLM 建议生成耗时。"}],
    }


def _manual_result_context_followup_fields(items: tuple[dict[str, Any], ...]) -> list[str]:
    fields: set[str] = set()
    blocked = {"raw", "metadata", "payload", "content", "body", "answer"}
    for item in items[:20]:
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            if key in blocked or str(key).startswith("_"):
                continue
            if value not in (None, "", [], {}):
                fields.add(str(key))
    priority = ("index", "id", "name", "title", "summary", "applicant", "amount", "department", "email", "mobile", "status", "url", "created_at")
    ordered = [field for field in priority if field in fields]
    ordered.extend(sorted(fields.difference(ordered)))
    return ordered[:16]


def _manual_result_context_identity_fields(items: tuple[dict[str, Any], ...]) -> list[str]:
    identity_candidates = ("id", "user_id", "open_id", "department_id", "task_id", "instance_code", "approval_code", "message_id", "chat_id", "app_token", "table_id", "url")
    fields: list[str] = []
    for key in identity_candidates:
        if any(isinstance(item, dict) and item.get(key) not in (None, "") for item in items[:20]):
            fields.append(key)
    return fields


def _manual_result_context_event_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
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
    latest_receipt = action_receipts[-1] if action_receipts else {}
    return {
        "available": bool(valid_events),
        "event_count": len(valid_events),
        "save_count": len(save_events),
        "clear_count": len(clear_events),
        "action_receipt_count": len(action_receipts),
        "latest_action": str(latest.get("action") or ""),
        "latest_result_type": str(latest.get("result_type") or ""),
        "latest_context_kind": str(latest.get("context_kind") or ""),
        "latest_item_count": int(latest.get("item_count") or latest.get("count") or 0) if latest else 0,
        "latest_action_id": str(latest.get("action_id") or ""),
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
    }


def _manual_severity_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for item in items:
        severity = str(item.get("severity") or "medium")
        counts[severity] = counts.get(severity, 0) + 1
    return counts


def _manual_issue_fingerprint(item: dict[str, Any]) -> dict[str, Any]:
    payload = dict(item)
    if payload.get("fingerprint"):
        return payload
    basis = "|".join(str(payload.get(key) or "") for key in ("key", "source", "severity", "label", "detail"))
    payload["fingerprint"] = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:10]
    return payload


def _runtime_status_detail_requested(message: str) -> bool:
    text = str(message or "").lower()
    return any(token in text for token in ("诊断详情", "详情", "详细", "完整", "全部", "展开", "debug", "full"))


def _runtime_status_summary_answer_from_snapshot(snapshot: dict[str, Any]) -> str:
    snapshot_summary = snapshot.get("snapshot_summary") if isinstance(snapshot.get("snapshot_summary"), dict) else {}
    pipeline_contract = snapshot.get("pipeline_constitution_contract") if isinstance(snapshot.get("pipeline_constitution_contract"), dict) else {}
    runtime_provider_snapshot = snapshot.get("runtime_provider_snapshot") if isinstance(snapshot.get("runtime_provider_snapshot"), dict) else {}
    result_context_quality = snapshot.get("result_context_quality") if isinstance(snapshot.get("result_context_quality"), dict) else {}
    action_closure = snapshot.get("action_closure") if isinstance(snapshot.get("action_closure"), dict) else {}
    runtime_grade = snapshot.get("runtime_health_grade") if isinstance(snapshot.get("runtime_health_grade"), dict) else {}

    provider_labels = _runtime_status_provider_labels(runtime_provider_snapshot)
    provider_issue_count = int(runtime_provider_snapshot.get("issue_count") or 0)
    provider_available_count = len(provider_labels.get("available", []))
    provider_abnormal = "、".join(provider_labels.get("abnormal", [])[:3]) or "无"
    pipeline_issue_count = int(pipeline_contract.get("issue_count") or 0)
    context_issue_count = int(result_context_quality.get("issue_count") or 0)
    action_status = "正常"
    if action_closure.get("latest_stuck"):
        action_status = "疑似卡住"
    elif action_closure.get("latest_pending"):
        action_status = "处理中"
    elif action_closure.get("latest_available") and not action_closure.get("has_action_receipt_event"):
        action_status = "缺少回执"
    impact = (
        "可能影响当前使用"
        if (not runtime_grade.get("usable") or provider_issue_count > 0 or pipeline_issue_count > 0 or context_issue_count > 0)
        else "不影响当前已接通能力使用"
    )
    lines = [
        "系统诊断：",
        f"结论：{runtime_grade.get('label') or snapshot_summary.get('gate_label') or '未生成'}",
        f"影响：{impact}",
        "",
        "关键状态",
        f"- 主链路：{pipeline_issue_count} 个问题",
        f"- 能力源：{provider_available_count} 类可用，异常：{provider_abnormal}",
        f"- 上下文：{result_context_quality.get('label') or result_context_quality.get('status') or '未生成'}",
        f"- 动作闭环：{action_status}",
    ]
    next_step = _runtime_status_summary_next_step(snapshot)
    if next_step:
        lines.extend(["", f"下一步：{next_step}"])
    lines.append("回复「诊断详情」查看完整技术诊断。")
    return "\n".join(lines)


def _runtime_status_latency_line(
    provider_summary: dict[str, Any],
    latency: dict[str, Any],
    fast_response: dict[str, Any],
    timing_health: dict[str, Any],
) -> str:
    def _int_value(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    total_ms = _int_value(provider_summary.get("total_duration_ms") or timing_health.get("total_ms"))
    slow_provider_source = str(
        latency.get("slowest_provider_source")
        or timing_health.get("slowest_provider_source")
        or ""
    )
    slow_provider_ms = _int_value(
        latency.get("slowest_provider_ms")
        or timing_health.get("slowest_provider_ms")
    )
    top_slow = latency.get("top_slow_item") if isinstance(latency.get("top_slow_item"), dict) else {}
    top_issue = (
        fast_response.get("top_issue")
        if isinstance(fast_response.get("top_issue"), dict)
        else {}
    )

    if top_slow:
        source = str(top_slow.get("source") or top_slow.get("provider") or slow_provider_source)
        operation = str(top_slow.get("operation") or top_slow.get("step") or top_slow.get("name") or "")
        duration_ms = _int_value(
            top_slow.get("duration_ms")
            or top_slow.get("elapsed_ms")
            or top_slow.get("ms")
            or slow_provider_ms
        )
        slowest = _runtime_source_label(source) if source else "未知步骤"
        if operation:
            slowest = f"{slowest}/{operation}"
        if duration_ms:
            slowest = f"{slowest} {duration_ms}ms"
    elif slow_provider_source:
        slowest = (
            f"{_runtime_source_label(slow_provider_source)} {slow_provider_ms}ms"
            if slow_provider_ms
            else _runtime_source_label(slow_provider_source)
        )
    else:
        slowest = "无"

    suggestion = str(
        top_issue.get("next_step")
        or top_slow.get("next_step")
        or top_slow.get("recommendation")
        or ""
    ).strip()
    if not suggestion and fast_response.get("fast_ack_required"):
        suggestion = "先快速回复，再异步完成慢步骤"

    line = (
        "响应体验："
        f"{latency.get('label') or timing_health.get('label') or '未生成'}"
        f"｜总耗时 {total_ms}ms"
        f"｜最慢：{slowest}"
        f"｜慢能力 {latency.get('slow_provider_count', 0)}"
        f"｜慢步骤 {latency.get('slow_substep_count', 0)}"
        f"｜快速回复：{'需要' if fast_response.get('fast_ack_required') else '非必需'}"
        f"｜后台处理：{'建议' if fast_response.get('async_recommended') else '非必需'}"
    )
    if suggestion:
        line += f"｜建议：{suggestion}"
    return f"{line}。"


def _runtime_status_detail_answer_from_snapshot(snapshot: dict[str, Any]) -> str:
    snapshot_summary = snapshot.get("snapshot_summary") if isinstance(snapshot.get("snapshot_summary"), dict) else {}
    pipeline_contract = snapshot.get("pipeline_constitution_contract") if isinstance(snapshot.get("pipeline_constitution_contract"), dict) else {}
    runtime_provider_snapshot = snapshot.get("runtime_provider_snapshot") if isinstance(snapshot.get("runtime_provider_snapshot"), dict) else {}
    planner_snapshot_contract = snapshot.get("planner_runtime_snapshot_contract") if isinstance(snapshot.get("planner_runtime_snapshot_contract"), dict) else {}
    provider_summary = snapshot.get("provider_summary") if isinstance(snapshot.get("provider_summary"), dict) else {}
    latency = provider_summary.get("latency_diagnostics") if isinstance(provider_summary.get("latency_diagnostics"), dict) else {}
    fast_response = snapshot.get("fast_response_contract") if isinstance(snapshot.get("fast_response_contract"), dict) else {}
    timing_health = snapshot.get("timing_health") if isinstance(snapshot.get("timing_health"), dict) else {}
    result_context_quality = snapshot.get("result_context_quality") if isinstance(snapshot.get("result_context_quality"), dict) else {}
    source_contract = snapshot.get("source_execution_contract") if isinstance(snapshot.get("source_execution_contract"), dict) else {}
    followup_health = snapshot.get("followup_health") if isinstance(snapshot.get("followup_health"), dict) else {}
    followup_contract = snapshot.get("followup_consume_contract") if isinstance(snapshot.get("followup_consume_contract"), dict) else {}
    permission = snapshot.get("permission") if isinstance(snapshot.get("permission"), dict) else {}
    permission_health = snapshot.get("permission_health") if isinstance(snapshot.get("permission_health"), dict) else {}
    runtime_grade = snapshot.get("runtime_health_grade") if isinstance(snapshot.get("runtime_health_grade"), dict) else {}
    provider_labels = _runtime_status_provider_labels(runtime_provider_snapshot)
    sources = snapshot_summary.get("sources") if isinstance(snapshot_summary.get("sources"), list) else []
    action_closure = snapshot.get("action_closure") if isinstance(snapshot.get("action_closure"), dict) else {}
    action_status = "正常"
    if action_closure.get("latest_stuck"):
        action_status = "疑似卡住"
    elif action_closure.get("latest_pending"):
        action_status = "处理中"
    elif action_closure.get("latest_available") and not action_closure.get("has_action_receipt_event"):
        action_status = "缺少回执"
    followup_uses_previous = bool(followup_health.get("uses_previous_result") or followup_contract.get("uses_previous_result"))
    permission_decision = "允许" if permission.get("allowed") else "拒绝" if permission.get("available") else "未知"
    permission_identity = _runtime_execution_identity_label(str(permission.get("execution_identity") or permission_health.get("execution_identity") or ""))
    detail_lines = [
        "诊断详情：",
        "",
        "一、当前请求",
        f"- 问题类型：{_runtime_question_type_label(str(snapshot_summary.get('question_type') or ''))}",
        f"- 执行策略：{_runtime_strategy_display_label(snapshot)}",
        f"- 数据来源：{'、'.join(_runtime_source_label(str(source)) for source in sources) or '无'}",
        f"- 执行状态：{_runtime_stage_label(str(snapshot_summary.get('execution_status') or snapshot.get('execution_status') or 'unknown'))}",
        "",
        "二、主链路",
        f"- 状态：{_human_runtime_label(pipeline_contract.get('label') or pipeline_contract.get('status') or '未生成')}",
        f"- 阶段：{pipeline_contract.get('stage_count', 0)} 个",
        f"- 问题：{pipeline_contract.get('issue_count', 0)} 个",
        "",
        "三、能力源",
        f"- 状态：{_human_runtime_label(runtime_provider_snapshot.get('label') or runtime_provider_snapshot.get('status') or '未生成')}",
        f"- 可用：{'、'.join(provider_labels.get('available', [])) or '无'}",
        f"- 异常：{'、'.join(provider_labels.get('abnormal', [])) or '无'}",
        f"- 规划快照：{_human_runtime_label(planner_snapshot_contract.get('label') or planner_snapshot_contract.get('status') or '未生成')}",
        "",
        "四、执行与上下文",
        f"- 能力执行：成功 {provider_summary.get('success_count', 0)}，失败 {provider_summary.get('error_count', 0)}，无权限 {provider_summary.get('denied_count', 0)}，总耗时 {provider_summary.get('total_duration_ms', 0)}ms",
        f"- 动作闭环：{action_status}",
        f"- 结果上下文：{_human_runtime_label(result_context_quality.get('label') or result_context_quality.get('status') or '未生成')}；结构化结果 {result_context_quality.get('item_count', 0)} 条",
        f"- 追问：{followup_health.get('label') or followup_health.get('status') or '未生成'}；{'命中上一轮结果' if followup_uses_previous else '本轮未触发'}",
        f"- 权限：{permission_health.get('label') or permission_health.get('status') or '未生成'}；决策 {permission_decision}；身份 {permission_identity or '未知'}",
        f"- 长期记忆：{_runtime_status_memory_brief(snapshot)}",
        "",
        "五、数据来源契约",
        (
            f"- {_human_runtime_label(source_contract.get('label') or source_contract.get('status') or '未生成')}"
            f"；计划 {source_contract.get('planned_count', 0)}，已执行 {source_contract.get('executed_planned_count', 0)}，缺失 {source_contract.get('missing_count', 0)}，额外 {source_contract.get('extra_count', 0)}"
        ),
    ]
    next_step = _runtime_status_summary_next_step(snapshot)
    if next_step:
        detail_lines.extend(["", f"下一步：{next_step}"])
    detail_lines.append("治理信息请单独回复「能力清册」「能力目录」或「治理视图」。")
    return "\n".join(detail_lines)
    lines = [
        "诊断详情：",
        (
            "系统："
            f"{runtime_grade.get('label') or snapshot_summary.get('gate_label') or '未生成'}"
            f"｜{runtime_grade.get('reason') or '暂无补充说明'}。"
        ),
        (
            "主链路："
            f"{pipeline_contract.get('label') or pipeline_contract.get('status') or '未生成'}"
            f"｜阶段 {pipeline_contract.get('stage_count', 0)}"
            f"｜问题 {pipeline_contract.get('issue_count', 0)}"
            f"｜本轮 {_runtime_question_type_label(str(snapshot_summary.get('question_type') or ''))}"
            f"｜策略 {_runtime_action_label(str(snapshot_summary.get('strategy') or snapshot.get('strategy') or ''))}"
            f"｜来源：{'、'.join(_runtime_source_label(str(source)) for source in sources) or '无'}。"
        ),
        (
            "Provider Snapshot："
            f"{runtime_provider_snapshot.get('label') or runtime_provider_snapshot.get('status') or '未生成'}"
            f"｜问题 {runtime_provider_snapshot.get('issue_count', 0)}。"
        ),
        (
            "Provider 可用性："
            f"可用：{'、'.join(provider_labels.get('available', [])) or '无'}"
            f"｜异常：{'、'.join(provider_labels.get('abnormal', [])) or '无'}。"
        ),
        _runtime_status_planner_snapshot_line(planner_snapshot_contract),
        (
            "Provider 执行："
            f"成功 {provider_summary.get('success_count', 0)}"
            f"｜失败 {provider_summary.get('error_count', 0)}"
            f"｜无权限 {provider_summary.get('denied_count', 0)}"
            f"｜总耗时 {provider_summary.get('total_duration_ms', 0)}ms。"
        ),
        _runtime_status_latency_line(provider_summary, latency, fast_response, timing_health),
        _runtime_status_action_line(snapshot),
        _runtime_status_result_context_line(result_context_quality),
        _runtime_status_followup_line(followup_health, followup_contract),
        _runtime_status_permission_line(permission, permission_health),
        _runtime_status_memory_line(snapshot),
        (
            "数据来源："
            f"{source_contract.get('label') or source_contract.get('status') or '未生成'}"
            f"｜计划 {source_contract.get('planned_count', 0)}"
            f"｜已执行 {source_contract.get('executed_planned_count', 0)}"
            f"｜缺失 {source_contract.get('missing_count', 0)}"
            f"｜额外 {source_contract.get('extra_count', 0)}"
            f"｜顺序：{'异常' if source_contract.get('order_mismatch') else '正常'}。"
        ),
    ]
    correlation = snapshot.get("correlation") if isinstance(snapshot.get("correlation"), dict) else {}
    if correlation:
        lines.append(
            "追踪："
            f"trace_id={correlation.get('trace_id') or snapshot.get('trace_id') or '无'}"
            + (f"｜query={correlation.get('query_id_tail')}" if correlation.get("query_id_tail") else "")
            + (f"｜chat={correlation.get('chat_id_tail')}" if correlation.get("chat_id_tail") else "")
            + "。"
        )
    next_step = _runtime_status_summary_next_step(snapshot)
    if next_step:
        lines.append(f"下一步：{next_step}")
    lines.append("治理信息请单独回复「能力清册」「能力目录」或「治理视图」。")
    return "\n".join(lines)


def _runtime_status_provider_labels(snapshot: dict[str, Any]) -> dict[str, list[str]]:
    providers = snapshot.get("providers") if isinstance(snapshot.get("providers"), dict) else {}
    available: list[str] = []
    abnormal: list[str] = []
    for source, payload in sorted(providers.items()):
        if not isinstance(payload, dict):
            continue
        label = _runtime_source_label(str(source))
        if payload.get("enabled") and payload.get("healthy"):
            available.append(label)
        else:
            abnormal.append(label)
    coverage = snapshot.get("coverage") if isinstance(snapshot.get("coverage"), list) else []
    for item in coverage:
        if not isinstance(item, dict) or item.get("ready"):
            continue
        source = str(item.get("source") or "")
        status = str(item.get("status") or "")
        reason = {
            "provider_missing": "缺失",
            "provider_disabled": "未启用",
            "provider_unhealthy": "不健康",
            "operation_not_covered": "缺操作",
            "operation_disabled": "操作未启用",
        }.get(status, "异常")
        label = f"{_runtime_source_label(source)}({reason})"
        if label not in abnormal:
            abnormal.append(label)
    return {"available": available[:8], "abnormal": abnormal[:8]}


def _runtime_status_planner_snapshot_line(contract: dict[str, Any]) -> str:
    planned_sources = contract.get("planned_sources") if isinstance(contract.get("planned_sources"), list) else []
    issues = contract.get("issues") if isinstance(contract.get("issues"), list) else []
    if not contract:
        return "Planner Snapshot：未生成。"
    issue_text = "无"
    if issues:
        parts = []
        for item in [entry for entry in issues if isinstance(entry, dict)][:4]:
            source = _runtime_source_label(str(item.get("source") or ""))
            label = str(item.get("label") or item.get("kind") or "异常")
            parts.append(f"{source}({label})")
        issue_text = "、".join(parts) or "有异常"
    return (
        "Planner Snapshot："
        f"{contract.get('label') or contract.get('status') or '未生成'}"
        f"｜已消费：{'是' if contract.get('consumed') else '否'}"
        f"｜规划来源：{'、'.join(_runtime_source_label(str(source)) for source in planned_sources) or '无'}"
        f"｜操作就绪 {contract.get('ready_operation_count', 0)}/{contract.get('planned_operation_count', 0)}"
        f"｜阻断 {contract.get('blocked_operation_count', 0)}"
        f"｜异常：{issue_text}。"
    )


def _runtime_status_path_line(snapshot: dict[str, Any], current_path_maturity: dict[str, Any]) -> str:
    runtime_mode = str(snapshot.get("runtime_mode") or "unknown")
    runtime_path_kind = str(snapshot.get("runtime_path_kind") or "")
    bypass_reason = str(snapshot.get("bypass_reason") or "").strip()
    explicit_user_requested = bool(current_path_maturity.get("explicit_user_requested"))
    migration_items = (
        current_path_maturity.get("migration_items")
        if isinstance(current_path_maturity.get("migration_items"), list)
        else []
    )
    bypassed_stages: list[str] = []
    for item in migration_items:
        if not isinstance(item, dict):
            continue
        stages = item.get("bypassed_stages") if isinstance(item.get("bypassed_stages"), list) else []
        for stage in stages:
            if str(stage) not in bypassed_stages:
                bypassed_stages.append(str(stage))
    mode_label = {
        "full_runtime": "完整 V5 主链路",
        "manual_fast_path": "手工快速路径",
        "v5_disabled": "V5 已禁用",
    }.get(runtime_mode, runtime_mode or "未知")
    path_label = {
        "v5_pipeline": "V5 Pipeline",
        "approval_fast_path": "审批快速摘要",
        "approval_workbench_path": "审批工作台",
        "manual_provider_path": "手工 Provider 编排",
        "legacy_fallback_blocked": "旧链路已阻断",
    }.get(runtime_path_kind, runtime_path_kind or "未知")
    full_path = runtime_mode == "full_runtime" and not bypassed_stages
    line = (
        "当前路径："
        f"{mode_label}｜{path_label}"
        f"｜完整主链路：{'是' if full_path else '否'}"
        f"｜显式请求：{'是' if explicit_user_requested else '否'}"
        f"｜绕过阶段 {len(bypassed_stages)} 个"
    )
    if bypass_reason:
        line += f"｜原因：{bypass_reason}"
    return f"{line}。"


def _runtime_status_memory_line(snapshot: dict[str, Any]) -> str:
    provider_summary = snapshot.get("provider_summary") if isinstance(snapshot.get("provider_summary"), dict) else {}
    source_contract = snapshot.get("source_execution_contract") if isinstance(snapshot.get("source_execution_contract"), dict) else {}
    runtime_provider_snapshot = snapshot.get("runtime_provider_snapshot") if isinstance(snapshot.get("runtime_provider_snapshot"), dict) else {}
    planned_sources = source_contract.get("planned_sources") if isinstance(source_contract.get("planned_sources"), list) else []
    providers = runtime_provider_snapshot.get("providers") if isinstance(runtime_provider_snapshot.get("providers"), dict) else {}
    memory_provider = providers.get("memory") if isinstance(providers.get("memory"), dict) else {}
    by_source = provider_summary.get("by_source") if isinstance(provider_summary.get("by_source"), list) else []
    memory_result = next((item for item in by_source if isinstance(item, dict) and str(item.get("source") or "") == "memory"), {})
    memory_planned = "memory" in {str(source) for source in planned_sources}
    if memory_result:
        success = int(memory_result.get("success_count") or 0)
        error = int(memory_result.get("error_count") or 0)
        denied = int(memory_result.get("denied_count") or 0)
        duration_ms = int(memory_result.get("duration_ms") or memory_result.get("total_duration_ms") or 0)
        if error or denied:
            return f"Memory：本轮已调用｜成功 {success}｜失败 {error}｜无权限 {denied}｜影响：可能影响长期知识参考。"
        return f"Memory：本轮已调用｜成功 {success}｜耗时 {duration_ms}ms｜状态正常。"
    if memory_planned:
        return "Memory：本轮计划使用，但未看到执行结果｜影响：可能影响长期知识参考。"
    if memory_provider:
        status = "可用" if memory_provider.get("enabled") and memory_provider.get("healthy") else "异常"
        return f"Memory：本轮未调用｜Provider {status}。"
    return "Memory：本轮未调用｜未注册 Memory Provider。"


def _runtime_status_action_line(snapshot: dict[str, Any]) -> str:
    action_closure = snapshot.get("action_closure") if isinstance(snapshot.get("action_closure"), dict) else {}
    pending_action = snapshot.get("pending_action") if isinstance(snapshot.get("pending_action"), dict) else {}
    pending_approval = snapshot.get("pending_approval") if isinstance(snapshot.get("pending_approval"), dict) else {}
    pending_bits: list[str] = []
    if pending_action.get("available"):
        pending_bits.append("通用待确认已过期" if pending_action.get("expired") else "通用待确认")
    if pending_approval.get("available"):
        pending_bits.append("审批待确认已过期" if pending_approval.get("expired") else "审批待确认")
    pending_text = "、".join(pending_bits) if pending_bits else "无待确认"
    if not action_closure.get("latest_available"):
        return f"动作闭环：无最近动作｜{pending_text}。"
    status = str(action_closure.get("contract_label") or action_closure.get("latest_status") or "未知")
    receipt = "已关联回执" if action_closure.get("has_correlated_receipt") else "有回执未关联" if action_closure.get("has_action_receipt_event") else "无回执"
    terminal = "终态" if action_closure.get("latest_terminal") else "处理中" if action_closure.get("latest_pending") else "非终态"
    stuck = "疑似卡住" if action_closure.get("latest_stuck") else "未超时"
    age = int(action_closure.get("latest_age_seconds") or 0)
    action_id = str(action_closure.get("latest_action_id") or "").strip()
    receipt_status = str(action_closure.get("latest_receipt_status") or "").strip()
    receipt_bits = []
    if action_id:
        receipt_bits.append(f"编号 {action_id[-8:]}")
    if receipt_status:
        receipt_bits.append(f"回执状态 {_runtime_stage_label(receipt_status)}")
    receipt_detail = "｜" + "｜".join(receipt_bits) if receipt_bits else ""
    return f"动作闭环：{status}｜{terminal}｜{receipt}{receipt_detail}｜{stuck}｜等待 {age}s｜{pending_text}。"


def _runtime_status_result_context_line(result_context_quality: dict[str, Any]) -> str:
    followup_field_count = int(result_context_quality.get("followup_field_count") or 0)
    identity_field_count = int(result_context_quality.get("item_identity_field_count") or 0)
    prefer_items = bool(result_context_quality.get("prefer_items"))
    supports_index = bool(result_context_quality.get("supports_index_followup"))
    supports_detail = bool(result_context_quality.get("supports_detail_followup"))
    answer_fallback = bool(result_context_quality.get("allow_answer_fallback"))
    items_normalized = bool(result_context_quality.get("items_normalized"))
    index_base = int(result_context_quality.get("index_base") or 0)
    return (
        "Result Context："
        f"{result_context_quality.get('label') or result_context_quality.get('status') or '未生成'}"
        f"｜结构化结果 {result_context_quality.get('item_count', 0)}/{result_context_quality.get('count', 0)}"
        f"｜规范化：{'是' if items_normalized else '否'}"
        f"｜索引基准：{index_base or '未知'}"
        f"｜优先读取 {_runtime_summary_consume_source_label(str(result_context_quality.get('primary_consume_source') or ''))}"
        f"｜追问字段 {followup_field_count}"
        f"｜身份字段 {identity_field_count}"
        f"｜索引追问：{'支持' if supports_index else '未知/不支持'}"
        f"｜详情追问：{'支持' if supports_detail else '未知/不支持'}"
        f"｜answer兜底：{'允许' if answer_fallback else '禁用'}"
        f"｜items优先：{'是' if prefer_items else '否'}。"
    )


def _runtime_status_followup_line(
    followup_health: dict[str, Any],
    followup_contract: dict[str, Any],
) -> str:
    uses_previous = bool(followup_health.get("uses_previous_result") or followup_contract.get("uses_previous_result"))
    followup_type = str(followup_health.get("followup_type") or followup_contract.get("followup_type") or "")
    prefer_items = bool(followup_contract.get("prefer_items"))
    allow_answer_fallback = bool(followup_contract.get("allow_answer_fallback"))
    item_count = int(followup_contract.get("item_count") or followup_health.get("item_count") or 0)
    issue_count = int(followup_health.get("issue_count") or 0) + int(followup_contract.get("issue_count") or 0)
    context_kind = str(followup_health.get("context_kind") or followup_contract.get("context_kind") or "")
    hit_text = "命中动作回执" if uses_previous and context_kind == "action_receipt" else "命中结构化结果" if uses_previous and item_count > 0 else "未命中上一轮结果" if uses_previous else "未触发"
    return (
        "Follow-up："
        f"{followup_health.get('label') or followup_health.get('status') or '未生成'}"
        f"｜{'追问' if uses_previous else '非追问'}"
        f"｜类型：{_runtime_followup_type_label(followup_type)}"
        f"｜{hit_text}"
        f"｜items {item_count}"
        f"｜items优先：{'是' if prefer_items else '否'}"
        f"｜answer兜底：{'允许' if allow_answer_fallback else '禁用'}"
        f"｜问题 {issue_count} 个。"
    )


def _runtime_status_permission_line(permission: dict[str, Any], permission_health: dict[str, Any]) -> str:
    if not permission and not permission_health:
        return "Permission：未生成。"
    available = bool(permission.get("available"))
    allowed = "允许" if permission.get("allowed") else "拒绝" if available else "未知"
    identity = _runtime_execution_identity_label(str(permission.get("execution_identity") or permission_health.get("execution_identity") or ""))
    requires_confirmation = "需确认" if permission.get("requires_confirmation") else "无需确认"
    confirmation_reasons = permission.get("confirmation_reasons") if isinstance(permission.get("confirmation_reasons"), list) else []
    reason_text = "、".join(_runtime_confirmation_reason_label(str(item)) for item in confirmation_reasons) if confirmation_reasons else ""
    source_capabilities = permission.get("source_capabilities") if isinstance(permission.get("source_capabilities"), list) else []
    issue_count = int(permission_health.get("issue_count") or 0)
    denied_reason = str(permission.get("reason") or "").strip()
    return (
        "Permission："
        f"{permission_health.get('label') or permission_health.get('status') or '未生成'}"
        f"｜决策：{allowed}"
        f"｜身份：{identity or '未知'}"
        f"｜{requires_confirmation}"
        f"｜来源能力 {len(source_capabilities)}"
        f"｜问题 {issue_count} 个"
        + (f"｜确认原因：{reason_text}" if reason_text else "")
        + (f"｜拒绝原因：{denied_reason}" if denied_reason else "")
        + "。"
    )


def _governance_view_answer_from_snapshot(snapshot: dict[str, Any], *, message: str = "") -> str:
    view_kind = _governance_view_kind(message)
    if view_kind == "directory":
        return _capability_directory_answer_from_snapshot(snapshot)
    if view_kind == "governance":
        return _governance_health_answer_from_snapshot(snapshot)

    capability = snapshot.get("capability_summary") if isinstance(snapshot.get("capability_summary"), dict) else {}
    provider_registry = snapshot.get("provider_registry") if isinstance(snapshot.get("provider_registry"), dict) else {}
    status_summary = provider_registry.get("status_summary") if isinstance(provider_registry.get("status_summary"), dict) else {}
    skill_inventory = capability.get("skill_atomic_inventory") if isinstance(capability.get("skill_atomic_inventory"), dict) else {}
    skill_registry = provider_registry.get("skill_registry")
    if not isinstance(skill_registry, dict):
        skill_registry = status_summary.get("skill_registry") if isinstance(status_summary.get("skill_registry"), dict) else {}
    write_contract = provider_registry.get("write_confirmation_contract")
    if not isinstance(write_contract, dict):
        write_contract = status_summary.get("write_confirmation_contract") if isinstance(status_summary.get("write_confirmation_contract"), dict) else {}
    top_skill_gap = skill_registry.get("top_priority_operation") if isinstance(skill_registry.get("top_priority_operation"), dict) else {}
    pending_items = capability.get("pending") if isinstance(capability.get("pending"), list) else []
    lines = [
        "能力清册：",
        "用途：查看已登记、已开放、待开放的原子能力。",
        (
            "原子能力："
            f"登记 {skill_inventory.get('registered_count', 0)} 个"
            f"｜开放 {skill_inventory.get('exposed_count', 0)} 个"
            f"｜待开放 {skill_inventory.get('pending_count', 0)} 个"
            f"｜高风险 {skill_inventory.get('high_risk_count', 0)} 个。"
            if skill_inventory
            else "原子能力：未生成。"
        ),
        (
            "执行源覆盖："
            f"已覆盖 {skill_registry.get('provider_backed_count', 0)} 个"
            f"｜缺来源 {skill_registry.get('missing_provider_source_count', 0)} 个"
            f"｜缺操作 {skill_registry.get('missing_provider_operation_count', 0)} 个"
            f"｜已开放不可执行 {skill_registry.get('exposed_unexecutable_count', 0)} 个。"
            if skill_registry
            else "Provider 覆盖：未生成。"
        ),
        (
            "写操作治理："
            f"写操作 {write_contract.get('write_operation_count', 0)} 个"
            f"｜确认契约缺口 {write_contract.get('gap_count', 0)} 个。"
        ),
    ]
    if top_skill_gap:
        lines.append(
            "治理优先级："
            f"{top_skill_gap.get('label') or top_skill_gap.get('operation') or '未知'}"
            f"｜优先级 {top_skill_gap.get('priority_label', '未知')}"
            f"｜原因：{_compact_governance_reasons(top_skill_gap.get('priority_reasons') or [], top_skill_gap.get('status_label', ''))}。"
        )
    if pending_items:
        lines.append(f"待接能力：{len(pending_items)} 个。回复「治理视图」看优先级。")
    lines.append("回复「能力目录」看业务入口；回复「系统诊断」看运行状态。")
    return "\n".join(lines)


def _governance_view_kind(message: str) -> str:
    text = str(message or "").strip()
    if "能力目录" in text:
        return "directory"
    if "治理视图" in text or "治理状态" in text or "能力接入情况" in text or "还缺什么能力" in text or "还有哪些没接" in text or "能力状态" in text:
        return "governance"
    return "inventory"


def _runtime_strategy_display_label(snapshot: dict[str, Any]) -> str:
    snapshot_summary = snapshot.get("snapshot_summary") if isinstance(snapshot.get("snapshot_summary"), dict) else {}
    strategy = str(snapshot_summary.get("strategy") or snapshot.get("strategy") or "")
    if strategy == "runtime_status":
        return "系统诊断"
    if strategy == "governance_view":
        return "治理视图"
    return _runtime_action_label(strategy)


def _human_runtime_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "未生成"
    for old, new in (
        ("Runtime Provider Snapshot", "运行能力快照"),
        ("Planner Snapshot", "规划快照"),
        ("Planner", "规划器"),
        ("Provider", "执行源"),
        ("Result Context", "结果上下文"),
        ("Memory", "长期记忆"),
        ("V5 主链路契约完整", "主链路契约完整"),
    ):
        text = text.replace(old, new)
    return text


def _compact_governance_reasons(reasons: Any, fallback: Any = "") -> str:
    items = [str(item) for item in reasons if str(item).strip()] if isinstance(reasons, list) else []
    text = "、".join(items) or str(fallback or "")
    text = text.replace("Skill 已登记但缺 Provider", "已登记但未接执行源")
    text = text.replace("高风险能力", "高风险")
    text = text.replace("动作类能力需要确认闭环", "动作需确认闭环")
    return text or "存在治理缺口"


def _capability_directory_answer_from_snapshot(snapshot: dict[str, Any]) -> str:
    capability = snapshot.get("capability_summary") if isinstance(snapshot.get("capability_summary"), dict) else {}
    provider_registry = snapshot.get("provider_registry") if isinstance(snapshot.get("provider_registry"), dict) else {}
    runtime_provider_snapshot = snapshot.get("runtime_provider_snapshot") if isinstance(snapshot.get("runtime_provider_snapshot"), dict) else {}
    providers = runtime_provider_snapshot.get("providers") if isinstance(runtime_provider_snapshot.get("providers"), dict) else {}
    if not providers:
        providers = provider_registry.get("providers") if isinstance(provider_registry.get("providers"), dict) else {}
    pending_items = capability.get("pending") if isinstance(capability.get("pending"), list) else []
    directory_items = _capability_directory_items(providers)
    lines = [
        "能力目录：",
        "用途：按业务入口查看现在能让机器人做什么。",
        "",
        "可用入口：",
    ]
    if directory_items:
        lines.extend(directory_items[:12])
    else:
        lines.append("- 暂未生成可用入口")
    lines.append("")
    lines.append("待补入口：")
    if pending_items:
        for item in [entry for entry in pending_items if isinstance(entry, dict)][:8]:
            label = str(item.get("label") or item.get("strategy") or "未命名能力")
            lines.append(f"- {label}")
    else:
        lines.append("- 暂无待补入口")
    lines.append("")
    lines.append("回复「能力清册」看原子能力；回复「治理视图」看治理缺口。")
    return "\n".join(lines)


def _runtime_status_memory_brief(snapshot: dict[str, Any]) -> str:
    runtime_provider_snapshot = snapshot.get("runtime_provider_snapshot") if isinstance(snapshot.get("runtime_provider_snapshot"), dict) else {}
    providers = runtime_provider_snapshot.get("providers") if isinstance(runtime_provider_snapshot.get("providers"), dict) else {}
    memory_provider = providers.get("memory") if isinstance(providers.get("memory"), dict) else {}
    if memory_provider:
        return "执行源可用" if memory_provider.get("enabled") and memory_provider.get("healthy") else "执行源异常"
    return "本轮未调用"


def _capability_directory_items(providers: dict[str, Any]) -> list[str]:
    available_sources = {
        str(source)
        for source, payload in providers.items()
        if isinstance(payload, dict) and payload.get("enabled") and payload.get("healthy")
    }
    catalog = {
        "approval": ("审批", "查待审批、看详情、通过、拒绝、转交、加签"),
        "people": ("通讯录", "查人、查部门、看组织架构"),
        "calendar": ("日程", "查日程、建会议、找空闲时间"),
        "task": ("任务", "查任务、建待办、跟进事项"),
        "mail": ("邮件", "查邮件、按关键词搜索、总结邮件"),
        "im": ("消息", "发消息、查会话、转发结果"),
        "base": ("多维表格", "建表、写入记录、查询记录"),
        "docs": ("飞书文档", "读文档、写文档、整理内容"),
        "drive": ("飞书云盘", "上传文件、查文件、发送文件链接"),
        "knowledge": ("知识库", "查企业知识、汇总资料"),
        "workevent": ("工作事件", "沉淀事实、支持分析和复盘"),
        "memory": ("长期记忆", "读取可追溯的长期信息"),
        "web": ("网页搜索", "补充外部公开信息"),
    }
    items: list[str] = []
    for source, (label, description) in catalog.items():
        if source in available_sources:
            items.append(f"- {label}：{description}")
    return items


def _governance_health_answer_from_snapshot(snapshot: dict[str, Any]) -> str:
    capability = snapshot.get("capability_summary") if isinstance(snapshot.get("capability_summary"), dict) else {}
    provider_registry = snapshot.get("provider_registry") if isinstance(snapshot.get("provider_registry"), dict) else {}
    status_summary = provider_registry.get("status_summary") if isinstance(provider_registry.get("status_summary"), dict) else {}
    skill_registry = provider_registry.get("skill_registry")
    if not isinstance(skill_registry, dict):
        skill_registry = status_summary.get("skill_registry") if isinstance(status_summary.get("skill_registry"), dict) else {}
    write_contract = provider_registry.get("write_confirmation_contract")
    if not isinstance(write_contract, dict):
        write_contract = status_summary.get("write_confirmation_contract") if isinstance(status_summary.get("write_confirmation_contract"), dict) else {}
    top_skill_gap = skill_registry.get("top_priority_operation") if isinstance(skill_registry.get("top_priority_operation"), dict) else {}
    pending_items = capability.get("pending") if isinstance(capability.get("pending"), list) else []
    lines = [
        "治理视图：",
        "用途：看能力治理是否健康，以及下一步优先补哪里。",
        "",
        "治理概况：",
        f"- Provider 缺口：来源 {skill_registry.get('missing_provider_source_count', 0)} 个，操作 {skill_registry.get('missing_provider_operation_count', 0)} 个",
        f"- 写操作确认缺口：{write_contract.get('gap_count', 0)} 个",
        f"- 已开放不可执行：{skill_registry.get('exposed_unexecutable_count', 0)} 个",
    ]
    if top_skill_gap:
        lines.extend([
            "",
            "优先处理：",
            f"- {top_skill_gap.get('label') or top_skill_gap.get('operation') or '未知能力'}",
            f"- 原因：{'、'.join(top_skill_gap.get('priority_reasons') or []) or top_skill_gap.get('status_label', '存在治理缺口')}",
        ])
    elif pending_items:
        first = next((entry for entry in pending_items if isinstance(entry, dict)), {})
        if first:
            lines.extend(["", "优先处理：", f"- {first.get('label') or first.get('strategy') or '待补能力'}"])
    lines.append("")
    lines.append("回复「系统诊断」看运行状态；回复「能力目录」看业务入口。")
    return "\n".join(lines)


def _runtime_summary_consume_source_label(source: str) -> str:
    return {
        "items": "结构化结果",
        "metadata": "上下文信息",
        "answer": "展示摘要",
        "none": "无可用缓存",
    }.get(source, source or "未知")


def _runtime_status_summary_next_step(snapshot: dict[str, Any]) -> str:
    candidates: list[str] = []
    path_next_step = _runtime_path_next_step(snapshot)
    if path_next_step:
        candidates.append(path_next_step)
    runtime_grade = snapshot.get("runtime_health_grade") if isinstance(snapshot.get("runtime_health_grade"), dict) else {}
    if runtime_grade.get("primary_next_step"):
        candidates.append(str(runtime_grade.get("primary_next_step") or ""))
    provider_snapshot_next_step = _runtime_provider_snapshot_next_step(snapshot)
    if provider_snapshot_next_step:
        candidates.append(provider_snapshot_next_step)
    for container_key, item_key, next_key in (
        ("fast_response_contract", "top_issue", "next_step"),
        ("action_closure", "top_repair_item", "next_step"),
        ("provider_summary", "evidence_contract", "top_issue"),
        ("provider_summary", "latency_diagnostics", "top_slow_item"),
        ("result_context_quality", "top_repair_item", "next_step"),
        ("source_execution_contract", "issues", "recommendation"),
    ):
        container = snapshot.get(container_key) if isinstance(snapshot.get(container_key), dict) else {}
        if container_key == "provider_summary":
            nested = container.get(item_key) if isinstance(container.get(item_key), dict) else {}
            item = nested.get(next_key) if isinstance(nested.get(next_key), dict) else {}
            value = str(item.get("next_step") or "").strip()
        elif item_key == "issues":
            issues = container.get("issues") if isinstance(container.get("issues"), list) else []
            item = issues[0] if issues and isinstance(issues[0], dict) else {}
            value = str(item.get(next_key) or "").strip()
        else:
            item = container.get(item_key) if isinstance(container.get(item_key), dict) else {}
            value = str(item.get(next_key) or "").strip()
        if value:
            candidates.append(value)
    snapshot_summary = snapshot.get("snapshot_summary") if isinstance(snapshot.get("snapshot_summary"), dict) else {}
    if snapshot_summary.get("next_step"):
        candidates.append(str(snapshot_summary.get("next_step") or ""))
    return _runtime_status_product_next_step(candidates[0]) if candidates else ""


def _runtime_path_next_step(snapshot: dict[str, Any]) -> str:
    current_path = snapshot.get("current_path_maturity") if isinstance(snapshot.get("current_path_maturity"), dict) else {}
    runtime_mode = str(snapshot.get("runtime_mode") or "")
    if runtime_mode == "full_runtime":
        return ""
    if current_path.get("explicit_user_requested"):
        return "把显式工作台体验迁入 V5 Composer/Card Renderer，保留交互体验，但由 Planner/Router/Provider 提供数据。"
    if runtime_mode == "manual_fast_path":
        return "先把该手工快速路径切回完整 V5 Pipeline，避免绕过 Intent、Planner、Permission 和 Router。"
    if runtime_mode == "v5_disabled":
        return "先恢复 V5 Runtime 开关，禁止静默回退到旧链路。"
    return ""


def _runtime_provider_snapshot_next_step(snapshot: dict[str, Any]) -> str:
    runtime_provider_snapshot = snapshot.get("runtime_provider_snapshot") if isinstance(snapshot.get("runtime_provider_snapshot"), dict) else {}
    if int(runtime_provider_snapshot.get("issue_count") or 0) <= 0:
        return ""
    reason_map = {
        "provider_missing": "Provider 缺失",
        "provider_disabled": "Provider 未启用",
        "provider_unhealthy": "Provider 不健康",
        "operation_not_covered": "能力未覆盖",
        "operation_disabled": "能力未启用",
    }
    coverage = runtime_provider_snapshot.get("coverage") if isinstance(runtime_provider_snapshot.get("coverage"), list) else []
    for item in coverage:
        if not isinstance(item, dict) or item.get("ready"):
            continue
        source = str(item.get("source") or "")
        operation = str(item.get("operation") or "")
        status = str(item.get("status") or "")
        reason = reason_map.get(status, status or "异常")
        target = _runtime_source_label(source) if source else "未知 Provider"
        if operation:
            target = f"{target}/{operation}"
        return f"先处理 {target}：{reason}，避免 Planner 选择到不可执行来源。"
    providers = runtime_provider_snapshot.get("providers") if isinstance(runtime_provider_snapshot.get("providers"), dict) else {}
    for source, payload in sorted(providers.items()):
        if not isinstance(payload, dict):
            continue
        if payload.get("enabled") and payload.get("healthy"):
            continue
        if not payload.get("enabled"):
            reason = "Provider 未启用"
        elif not payload.get("healthy"):
            reason = "Provider 不健康"
        else:
            reason = "Provider 异常"
        return f"先处理 {_runtime_source_label(str(source))}：{reason}，恢复运行时可用状态。"
    return "先处理 Runtime Provider Snapshot 中的异常项。"


def _runtime_status_product_next_step(next_step: str) -> str:
    text = str(next_step or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if "快速首包" in text or "已收到" in text or "fast" in lowered:
        return "先给用户快速回复“已收到，正在处理”，再继续执行慢步骤。"
    if "后台" in text or "异步" in text or "worker" in lowered or "queue" in lowered:
        return "把耗时步骤放到后台处理，完成后再发结果通知。"
    if "action_receipt" in text or "回执" in text or "最终结果" in text:
        return "动作完成后补发清晰的结果通知，说明成功、失败或取消原因。"
    if "Result Context" in text or "items" in text or "结构化" in text:
        return "把本轮结果保存成结构化数据，方便继续问“第一个详情”“这些人是谁”。"
    if "Provider" in text or "provider" in lowered or "飞书 api" in lowered or "API" in text:
        return "检查对应飞书能力是否接通，并确认返回了可读结果和错误原因。"
    if "Planner" in text or "Router" in text or "Capability" in text:
        return "对齐问题规划、能力清单和实际执行来源，避免漏执行或走错能力。"
    if "缓存" in text or "cache" in lowered:
        return "优先复用最近结果或短期缓存，避免每次都重新全量读取。"
    if "附件" in text:
        return "列表先展示摘要，附件内容改为点详情时再读取。"
    if "LLM" in text or "AI" in text:
        return "把 AI 判断合并成批量处理，必要时放到后台生成。"
    return text


def _runtime_status_answer_from_snapshot(snapshot: dict[str, Any], *, detail: bool = False) -> str:
    if not detail:
        return _runtime_status_summary_answer_from_snapshot(snapshot)
    return _runtime_status_detail_answer_from_snapshot(snapshot)
    capability = snapshot.get("capability_summary") if isinstance(snapshot.get("capability_summary"), dict) else {}
    provider_registry = snapshot.get("provider_registry") if isinstance(snapshot.get("provider_registry"), dict) else {}
    health = snapshot.get("health") if isinstance(snapshot.get("health"), dict) else {}
    status_summary = provider_registry.get("status_summary") if isinstance(provider_registry.get("status_summary"), dict) else {}
    migration_queue = capability.get("migration_queue_summary") if isinstance(capability.get("migration_queue_summary"), dict) else {}
    provider_count = len(provider_registry.get("registered_sources") or [])
    pending_ops = provider_registry.get("pending_operation_count", 0)
    drift = provider_registry.get("capability_drift") if isinstance(provider_registry.get("capability_drift"), dict) else {}
    planner_drift = provider_registry.get("planner_drift") if isinstance(provider_registry.get("planner_drift"), dict) else {}
    contract_health = provider_registry.get("contract_health") if isinstance(provider_registry.get("contract_health"), dict) else {}
    write_confirmation_contract = provider_registry.get("write_confirmation_contract")
    if not isinstance(write_confirmation_contract, dict):
        write_confirmation_contract = (
            status_summary.get("write_confirmation_contract")
            if isinstance(status_summary.get("write_confirmation_contract"), dict)
            else {}
        )
    skill_registry = provider_registry.get("skill_registry")
    if not isinstance(skill_registry, dict):
        skill_registry = status_summary.get("skill_registry") if isinstance(status_summary.get("skill_registry"), dict) else {}
    snapshot_summary = snapshot.get("snapshot_summary") if isinstance(snapshot.get("snapshot_summary"), dict) else {}
    runtime_mode = str(snapshot.get("runtime_mode") or snapshot_summary.get("runtime_mode") or "unknown")
    runtime_path_kind = str(snapshot.get("runtime_path_kind") or snapshot_summary.get("runtime_path_kind") or "")
    bypass_reason = str(snapshot.get("bypass_reason") or snapshot_summary.get("bypass_reason") or "")
    runtime_mode_label = {
        "full_runtime": "完整 V5 主链路",
        "manual_fast_path": "手工快速路径",
    }.get(runtime_mode, runtime_mode or "未知")
    runtime_path_label = {
        "v5_pipeline": "V5 Pipeline",
        "approval_fast_path": "审批快速摘要",
        "approval_workbench_path": "审批工作台",
    }.get(runtime_path_kind, runtime_path_kind or "未知")
    source_contract_for_summary = snapshot.get("source_execution_contract") if isinstance(snapshot.get("source_execution_contract"), dict) else {}
    followup_contract_for_summary = snapshot.get("followup_consume_contract") if isinstance(snapshot.get("followup_consume_contract"), dict) else {}
    action_closure_for_summary = snapshot.get("action_closure") if isinstance(snapshot.get("action_closure"), dict) else {}
    current_path_maturity = snapshot.get("current_path_maturity") if isinstance(snapshot.get("current_path_maturity"), dict) else {}
    snapshot_integrity = snapshot.get("snapshot_integrity") if isinstance(snapshot.get("snapshot_integrity"), dict) else {}
    pipeline_constitution_contract = snapshot.get("pipeline_constitution_contract") if isinstance(snapshot.get("pipeline_constitution_contract"), dict) else {}
    guardrail_parts = [
        "来源已对齐" if source_contract_for_summary.get("status") == "healthy" else "来源需关注",
        "追问已守护" if followup_contract_for_summary.get("status") == "healthy" else "追问需关注",
    ]
    if action_closure_for_summary.get("latest_available"):
        guardrail_parts.append("动作已闭环" if action_closure_for_summary.get("has_action_receipt_event") else "动作待回执")
    else:
        guardrail_parts.append("无动作待闭环")
    lines = [
        "V5 Runtime 状态：",
        f"整体结论：{status_summary.get('health_label') or '未生成'}。",
        f"运行模式：{runtime_mode_label}｜路径：{runtime_path_label}" + (f"｜原因：{bypass_reason}" if bypass_reason else "") + "。",
        "当前路径：" + f"{current_path_maturity.get('label') or current_path_maturity.get('status') or '未知'}。"
        if current_path_maturity
        else "当前路径：未生成路径成熟度。",
        "主链路契约："
        + (
            f"{pipeline_constitution_contract.get('label') or pipeline_constitution_contract.get('status') or '未知'}"
            f"｜阶段 {pipeline_constitution_contract.get('stage_count', 0)} 个"
            f"｜问题 {pipeline_constitution_contract.get('issue_count', 0)} 个。"
        )
        if pipeline_constitution_contract
        else "主链路契约：未生成。",
        "诊断完整性："
        + (
            f"{snapshot_integrity.get('label') or snapshot_integrity.get('status') or '未知'}"
            f"｜缺失 {snapshot_integrity.get('missing_count', 0)}"
            f"｜空模块 {snapshot_integrity.get('empty_count', 0)}。"
        )
        if snapshot_integrity
        else "诊断完整性：未生成。",
        "V5 护栏：" + "｜".join(guardrail_parts) + "。",
        f"Provider：已注册 {provider_count} 个，待接运行操作 {pending_ops} 个。",
        "写操作确认契约："
        f"写入 {write_confirmation_contract.get('write_operation_count', 0)} 个，"
        f"需 dry-run {write_confirmation_contract.get('dry_run_required_count', 0)} 个，"
        f"需确认令牌 {write_confirmation_contract.get('confirmation_token_required_count', 0)} 个，"
        f"缺口 {write_confirmation_contract.get('gap_count', 0)} 个。",
        f"架构漂移：Provider {status_summary.get('drift_count', 0)} 项，Planner {status_summary.get('planner_drift_count', 0)} 项，契约 {status_summary.get('contract_issue_count', 0)} 项，退役源 {provider_registry.get('retired_registered_source_count', 0)} 项。",
        f"最近链路阶段：{_runtime_stage_label(str(health.get('stage') or 'unknown'))}。",
        f"最近策略：{snapshot.get('strategy') or '无'}。",
        f"Capability/Provider 漂移：缺 Provider {drift.get('declared_missing_provider_count', 0)} 项，未声明 Provider 操作 {drift.get('provider_missing_declaration_count', 0)} 项。",
        f"Planner/Capability 漂移：Planner 缺能力声明 {planner_drift.get('planner_missing_capability_count', 0)} 项，Capability 未被 Planner 使用 {planner_drift.get('capability_missing_planner_count', 0)} 项。",
    ]
    main_pipeline_timing = snapshot.get("pipeline_timing") if isinstance(snapshot.get("pipeline_timing"), dict) else {}
    main_debug_locator = snapshot.get("debug_locator") if isinstance(snapshot.get("debug_locator"), dict) else {}
    if main_pipeline_timing or main_debug_locator:
        lines.append(
            "耗时定位："
            f"总耗时 {main_pipeline_timing.get('total_ms', 0)}ms"
            f"｜最慢阶段 {_runtime_stage_name_label(str(main_pipeline_timing.get('slowest_stage') or ''))} {main_pipeline_timing.get('slowest_ms', 0)}ms"
            f"｜最慢 Provider {_runtime_source_label(str(main_debug_locator.get('slowest_provider_source') or ''))} {main_debug_locator.get('slowest_provider_ms', 0)}ms。"
        )
        timing_advice = _runtime_pipeline_timing_advice(main_pipeline_timing)
        if timing_advice:
            lines.append(f"耗时建议：{timing_advice}")
    if snapshot_summary:
        sources = snapshot_summary.get("sources") if isinstance(snapshot_summary.get("sources"), list) else []
        lines.append(
            "快照摘要："
            f"{snapshot_summary.get('version') or snapshot.get('diagnostics_version') or '未知版本'}"
            f"｜{snapshot_summary.get('gate_label') or snapshot_summary.get('gate_status') or '未知门禁'}"
            f"｜健康问题 {snapshot_summary.get('health_issue_count', 0)} 个"
            f"｜修复项 {snapshot_summary.get('repair_item_count', 0)} 个"
            + (f"｜主问题 {snapshot_summary.get('primary_issue_fingerprint')}" if snapshot_summary.get("primary_issue_fingerprint") else "")
            + "。"
        )
        lines.append(
            "本轮摘要："
            f"{_runtime_question_type_label(str(snapshot_summary.get('question_type') or ''))}"
            f"｜{_runtime_data_scope_label(str(snapshot_summary.get('data_scope') or ''))}"
            f"｜{_runtime_action_label(str(snapshot_summary.get('strategy') or ''))}"
            f"｜来源：{'、'.join(_runtime_source_label(str(source)) for source in sources) or '无'}"
            f"｜{_runtime_stage_label(str(snapshot_summary.get('execution_status') or 'unknown'))}"
            f"｜{snapshot_summary.get('result_type') or '无结果'} {snapshot_summary.get('result_count', 0)} 条。"
        )
        if snapshot_summary.get("next_step"):
            lines.append(f"摘要建议：{snapshot_summary.get('next_step')}")
    source_contract = snapshot.get("source_execution_contract") if isinstance(snapshot.get("source_execution_contract"), dict) else {}
    if source_contract:
        missing_sources = source_contract.get("missing_sources") if isinstance(source_contract.get("missing_sources"), list) else []
        executed_sources = source_contract.get("executed_sources") if isinstance(source_contract.get("executed_sources"), list) else []
        lines.append(
            "来源执行契约："
            f"{source_contract.get('label') or source_contract.get('status') or '未知'}"
            f"｜计划 {source_contract.get('planned_count', 0)} 个"
            f"｜已执行 {source_contract.get('executed_planned_count', len(executed_sources))} 个"
            f"｜缺失 {source_contract.get('missing_count', len(missing_sources))} 个"
            f"｜额外 {source_contract.get('extra_count', 0)} 个"
            f"｜重复 {source_contract.get('duplicate_count', 0)} 个"
            f"｜顺序：{'异常' if source_contract.get('order_mismatch') else '正常'}。"
        )
        if missing_sources:
            lines.append("缺失来源：" + "、".join(_runtime_source_label(str(source)) for source in missing_sources[:6]))
        source_issues = source_contract.get("issues") if isinstance(source_contract.get("issues"), list) else []
        if source_issues:
            first_issue = source_issues[0] if isinstance(source_issues[0], dict) else {}
            if first_issue:
                lines.append(
                    "来源执行下一修复："
                    f"{first_issue.get('label') or first_issue.get('kind')}"
                    + (f"｜{first_issue.get('detail')}" if first_issue.get("detail") else "")
                    + (f"｜建议：{first_issue.get('recommendation')}" if first_issue.get("recommendation") else "")
                )
    followup_contract = snapshot.get("followup_consume_contract") if isinstance(snapshot.get("followup_consume_contract"), dict) else {}
    if followup_contract:
        lines.append(
            "追问消费："
            f"{followup_contract.get('label') or followup_contract.get('status') or '未知'}"
            f"｜items {followup_contract.get('item_count', 0)} 条"
            f"｜字段 {followup_contract.get('followup_field_count', 0)} 个"
            f"｜身份字段 {followup_contract.get('item_identity_field_count', 0)} 个"
            f"｜{'优先使用结构化条目' if followup_contract.get('prefer_items') else '未声明结构化条目优先'}"
            f"｜主消费：{followup_contract.get('primary_consume_source') or '未知'}。"
        )
        top_followup_repair = followup_contract.get("top_repair_item") if isinstance(followup_contract.get("top_repair_item"), dict) else {}
        if top_followup_repair:
            lines.append(
                "追问消费下一修复："
                f"{top_followup_repair.get('label') or top_followup_repair.get('kind')}"
                f"｜优先级 {top_followup_repair.get('priority_label', '未知')}"
                f"｜下一步：{top_followup_repair.get('next_step', '')}"
            )
    correlation = snapshot.get("correlation") if isinstance(snapshot.get("correlation"), dict) else {}
    if correlation:
        provider_sources = correlation.get("provider_sources") if isinstance(correlation.get("provider_sources"), list) else []
        lines.append(
            "追踪关联："
            f"trace_id={correlation.get('trace_id') or snapshot.get('trace_id') or '无'}"
            + (f"｜chat={correlation.get('chat_id_tail')}" if correlation.get("chat_id_tail") else "")
            + (f"｜query={correlation.get('query_id_tail')}" if correlation.get("query_id_tail") else "")
            + (f"｜策略：{_runtime_action_label(str(correlation.get('strategy') or ''))}" if correlation.get("strategy") else "")
            + (f"｜Provider：{'、'.join(_runtime_source_label(str(source)) for source in provider_sources)}" if provider_sources else "")
        )
    snapshot_lifecycle = snapshot.get("snapshot_lifecycle") if isinstance(snapshot.get("snapshot_lifecycle"), dict) else {}
    if snapshot_lifecycle:
        lines.append(
            "快照生命周期："
            f"{snapshot_lifecycle.get('label') or snapshot_lifecycle.get('status') or '未知'}"
            f"｜生成时间：{snapshot_lifecycle.get('generated_at') or snapshot.get('generated_at') or '未知'}"
            f"｜{'有执行' if snapshot_lifecycle.get('has_execution') else '未执行'}"
            f"｜{'有答案' if snapshot_lifecycle.get('has_answer') else '无答案'}"
            f"｜{'有修复计划' if snapshot_lifecycle.get('has_repair_plan') else '无修复计划'}。"
        )
    debug_locator = snapshot.get("debug_locator") if isinstance(snapshot.get("debug_locator"), dict) else {}
    if debug_locator:
        lines.append(
            "调试定位："
            f"{debug_locator.get('locator') or debug_locator.get('trace_id') or '无'}"
            + (f"｜回执：{'已关联' if debug_locator.get('has_correlated_receipt') else '未关联'}" if debug_locator.get("latest_action_id_tail") else "")
            + (f"｜Result 剩余 {debug_locator.get('result_expires_in_seconds', 0)} 秒" if "result_expires_in_seconds" in debug_locator else "")
            + "。"
        )
    diagnostics_capabilities = snapshot.get("diagnostics_capabilities") if isinstance(snapshot.get("diagnostics_capabilities"), dict) else {}
    if diagnostics_capabilities:
        modules = diagnostics_capabilities.get("modules") if isinstance(diagnostics_capabilities.get("modules"), list) else []
        lines.append(
            "诊断能力："
            f"{diagnostics_capabilities.get('module_count', len(modules))} 个模块"
            f"｜版本：{diagnostics_capabilities.get('version') or snapshot.get('diagnostics_version') or '未知'}。"
        )
    evidence_summary = snapshot.get("evidence_summary") if isinstance(snapshot.get("evidence_summary"), dict) else {}
    if evidence_summary:
        lines.append(
            "证据链："
            f"{evidence_summary.get('label') or evidence_summary.get('status') or '未知'}"
            f"｜可用 {evidence_summary.get('available_count', 0)} 类"
            f"｜缺失 {evidence_summary.get('missing_count', 0)} 类。"
        )
        evidence_items = evidence_summary.get("items") if isinstance(evidence_summary.get("items"), list) else []
        evidence_parts = []
        for item in [entry for entry in evidence_items if isinstance(entry, dict)]:
            label = str(item.get("label") or item.get("key") or "")
            count = item.get("count", 0)
            evidence_parts.append(f"{label}:{count}" if item.get("available") else f"{label}:缺失")
        if evidence_parts:
            lines.append("证据：" + "｜".join(evidence_parts[:6]))
    readiness = status_summary.get("readiness") if isinstance(status_summary.get("readiness"), dict) else {}
    if readiness:
        lines.append(f"V5 就绪度：{readiness.get('score', 0)}/100｜{readiness.get('label') or '未评估'}。")
        penalties = readiness.get("penalties") if isinstance(readiness.get("penalties"), list) else []
        if penalties:
            lines.append("主要扣分：")
            for item in [entry for entry in penalties if isinstance(entry, dict)][:4]:
                label = str(item.get("label") or item.get("kind") or "未分类")
                reason = str(item.get("reason") or "").strip()
                lines.append(f"- {label}：{item.get('count', 0)} 项，-{item.get('points', 0)} 分" + (f"｜{reason}" if reason else ""))
    health_matrix = snapshot.get("health_matrix") if isinstance(snapshot.get("health_matrix"), dict) else {}
    if health_matrix:
        lines.append(f"健康矩阵：{health_matrix.get('label') or health_matrix.get('status') or '未知'}｜问题 {health_matrix.get('issue_count', 0)} 个。")
        status_counts = health_matrix.get("status_counts") if isinstance(health_matrix.get("status_counts"), dict) else {}
        if status_counts:
            lines.append(
                "层级统计："
                f"正常 {status_counts.get('healthy', 0)}，"
                f"需关注 {status_counts.get('needs_attention', 0)}，"
                f"阻断 {status_counts.get('blocked', 0)}，"
                f"无数据 {status_counts.get('none', 0)}。"
            )
        layers = health_matrix.get("layers") if isinstance(health_matrix.get("layers"), list) else []
        layer_parts = []
        for item in [entry for entry in layers if isinstance(entry, dict)]:
            label = str(item.get("label") or item.get("key") or "")
            status = str(item.get("status_label") or item.get("status") or "")
            count = int(item.get("issue_count") or 0)
            layer_parts.append(f"{label}:{status}" + (f"({count})" if count else ""))
        if layer_parts:
            lines.append("层级：" + "｜".join(layer_parts[:8]))
    repair_plan = snapshot.get("repair_plan") if isinstance(snapshot.get("repair_plan"), dict) else {}
    if repair_plan:
        contract_issues_for_plan = contract_health.get("issues") if isinstance(contract_health.get("issues"), list) else []
        drift_categories_for_plan = status_summary.get("drift_categories") if isinstance(status_summary.get("drift_categories"), list) else []
        repair_summary_count = int(repair_plan.get("item_count") or 0) + len(contract_issues_for_plan) + len(drift_categories_for_plan)
        repair_summary = "暂无明显问题" if repair_plan.get("status") == "healthy" and repair_summary_count == 0 else f"{repair_summary_count} 项待处理"
        lines.append(f"修复计划：{repair_summary}。")
        severity_counts = repair_plan.get("severity_counts") if isinstance(repair_plan.get("severity_counts"), dict) else {}
        if severity_counts:
            lines.append(
                "风险统计："
                f"严重 {severity_counts.get('critical', 0)}，"
                f"高 {severity_counts.get('high', 0)}，"
                f"中 {severity_counts.get('medium', 0)}，"
                f"低 {severity_counts.get('low', 0)}。"
            )
        repair_items = repair_plan.get("items") if isinstance(repair_plan.get("items"), list) else []
        for item in [entry for entry in repair_items if isinstance(entry, dict)][:4]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            label = str(item.get("label") or "修复项")
            detail = str(item.get("detail") or "").strip()
            next_step_item = str(item.get("next_step") or "").strip()
            fingerprint = str(item.get("fingerprint") or "").strip()
            lines.append(f"- {severity_label}" + (f"｜#{fingerprint}" if fingerprint else "") + f"｜{label}" + (f"｜{detail}" if detail else ""))
            if next_step_item:
                lines.append(f"  下一步：{next_step_item}")
        for item in [entry for entry in contract_issues_for_plan if isinstance(entry, dict)][:3]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            source = _runtime_source_label(str(item.get("source") or ""))
            operation = _runtime_action_label(str(item.get("operation") or ""))
            label = str(item.get("label") or item.get("kind") or "Provider 契约问题")
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- {severity_label}｜Provider 契约｜{source}.{operation}｜{label}")
            if recommendation:
                lines.append(f"  下一步：{recommendation}")
        if not repair_items and not contract_issues_for_plan:
            for item in [entry for entry in drift_categories_for_plan if isinstance(entry, dict)][:3]:
                label = str(item.get("label") or item.get("kind") or "架构漂移")
                reason = str(item.get("reason") or "").strip()
                lines.append(f"- 中｜架构漂移｜{label}：{item.get('count', 0)} 项")
                if reason:
                    lines.append(f"  下一步：{reason}")
    source_readiness = status_summary.get("source_readiness") if isinstance(status_summary.get("source_readiness"), list) else []
    if source_readiness:
        lines.append("Provider 就绪度：")
        for item in [entry for entry in source_readiness if isinstance(entry, dict)][:8]:
            lines.append(
                f"- {_runtime_source_label(str(item.get('source') or ''))}"
                f"｜{item.get('label') or item.get('status') or '未知'}"
                f"｜已接 {item.get('installed', 0)}"
                f"｜待接 {item.get('pending', 0)}"
                f"｜问题 {item.get('issue_count', 0)}"
            )
    capability_contract = provider_registry.get("capability_contract") if isinstance(provider_registry.get("capability_contract"), dict) else {}
    if capability_contract:
        lines.append(
            "能力契约："
            f"{capability_contract.get('label') or capability_contract.get('status') or '未知'}"
            f"｜声明 {capability_contract.get('declared_count', 0)}"
            f"｜Provider {capability_contract.get('provider_operation_count', 0)}"
            f"｜已对齐 {capability_contract.get('aligned_count', 0)}"
            f"｜未纳管写 {capability_contract.get('ungoverned_write_count', 0)}"
            f"｜确认缺口 {capability_contract.get('confirmation_gap_count', 0)}"
            f"｜身份缺口 {capability_contract.get('identity_gap_count', 0)}。"
        )
        source_rows = capability_contract.get("source_rows") if isinstance(capability_contract.get("source_rows"), list) else []
        if source_rows:
            lines.append("契约来源：" + "｜".join(
                f"{_runtime_source_label(str(item.get('source') or ''))}:{item.get('aligned_count', 0)}/{item.get('provider_operation_count', 0)}"
                for item in [entry for entry in source_rows if isinstance(entry, dict)][:8]
            ))
        gaps = []
        for key, label in (
            ("ungoverned_writes", "未纳管写"),
            ("confirmation_gaps", "确认缺口"),
            ("identity_gaps", "身份缺口"),
            ("pending_declared", "声明待接"),
        ):
            rows = capability_contract.get(key) if isinstance(capability_contract.get(key), list) else []
            for row in [entry for entry in rows if isinstance(entry, dict)][:3]:
                gaps.append(f"{label}:{_runtime_source_label(str(row.get('source') or ''))}.{_runtime_action_label(str(row.get('operation') or ''))}")
        if gaps:
            lines.append("契约缺口：" + "｜".join(gaps[:6]))
    path_maturity = capability.get("path_maturity") if isinstance(capability.get("path_maturity"), list) else []
    migration_items = [
        item
        for item in path_maturity
        if isinstance(item, dict) and str(item.get("kind") or "") in {"manual_fast_path", "legacy_adapter"}
    ]
    if migration_items:
        lines.append("路径迁移：")
        top_item = migration_queue.get("top_item") if isinstance(migration_queue.get("top_item"), dict) else {}
        if top_item:
            lines.append(
                "最高优先："
                f"{top_item.get('migration_priority_label') or '中'}｜"
                f"{_runtime_source_label(str(top_item.get('source') or ''))}."
                f"{_runtime_action_label(str(top_item.get('operation') or ''))}"
                f"｜{top_item.get('label') or top_item.get('strategy') or ''}"
            )
        for item in migration_items[:6]:
            lines.append(
                f"- {item.get('migration_priority_label') or '中'}｜"
                f"{_runtime_source_label(str(item.get('source') or ''))}."
                f"{_runtime_action_label(str(item.get('operation') or ''))}"
                f"｜{item.get('kind_label') or item.get('kind') or '未知'}"
                f"｜{item.get('label') or item.get('strategy') or ''}"
            )
            bypassed = item.get("bypassed_stages") if isinstance(item.get("bypassed_stages"), list) else []
            if bypassed:
                lines.append("  绕过阶段：" + "、".join(_runtime_pipeline_label(str(stage)) for stage in bypassed[:8]))
            if item.get("target_runtime_path"):
                lines.append(f"  目标路径：{item.get('target_runtime_path')}")
            if item.get("migration_next_step"):
                lines.append(f"  下一步：{item.get('migration_next_step')}")
            stage_plan = item.get("migration_stage_plan") if isinstance(item.get("migration_stage_plan"), list) else []
            for stage in [entry for entry in stage_plan if isinstance(entry, dict)][:3]:
                label = str(stage.get("label") or stage.get("stage") or "迁移阶段")
                target = str(stage.get("target") or "").strip()
                lines.append(f"  - {label}：{target}")
    current_migration_items = current_path_maturity.get("migration_items") if isinstance(current_path_maturity.get("migration_items"), list) else []
    if current_migration_items:
        lines.append("当前策略迁移：")
        for item in [entry for entry in current_migration_items if isinstance(entry, dict)][:4]:
            lines.append(
                f"- {item.get('migration_priority_label') or '中'}｜"
                f"{_runtime_source_label(str(item.get('source') or ''))}."
                f"{_runtime_action_label(str(item.get('operation') or ''))}"
                f"｜{item.get('kind_label') or item.get('kind') or '未知'}"
                f"｜{item.get('reason') or ''}"
            )
            bypassed = item.get("bypassed_stages") if isinstance(item.get("bypassed_stages"), list) else []
            if bypassed:
                lines.append("  绕过阶段：" + "、".join(_runtime_pipeline_label(str(stage)) for stage in bypassed[:8]))
            stage_plan = item.get("migration_stage_plan") if isinstance(item.get("migration_stage_plan"), list) else []
            for stage in [entry for entry in stage_plan if isinstance(entry, dict)][:3]:
                label = str(stage.get("label") or stage.get("stage") or "迁移阶段")
                target = str(stage.get("target") or "").strip()
                lines.append(f"  - {label}：{target}")
            if item.get("target_runtime_path"):
                lines.append(f"  目标路径：{item.get('target_runtime_path')}")
            if item.get("migration_next_step"):
                lines.append(f"  下一步：{item.get('migration_next_step')}")
    priority_gaps = status_summary.get("priority_gaps") if isinstance(status_summary.get("priority_gaps"), list) else []
    if priority_gaps:
        lines.append("优先能力缺口：")
        for item in [entry for entry in priority_gaps if isinstance(entry, dict)][:5]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            source = _runtime_source_label(str(item.get("source") or ""))
            operation = _runtime_action_label(str(item.get("operation") or ""))
            label = str(item.get("label") or "能力缺口")
            next_step_item = str(item.get("next_step") or "").strip()
            lines.append(f"- {severity_label}｜{source}.{operation}｜{label}")
            if next_step_item:
                lines.append(f"  下一步：{next_step_item}")
    contract_issues = contract_health.get("issues") if isinstance(contract_health.get("issues"), list) else []
    if contract_issues:
        lines.append(f"Provider 契约：{contract_health.get('label') or '需要修复'}，阻断 {contract_health.get('blocking_count', 0)} 项。")
        for item in [entry for entry in contract_issues if isinstance(entry, dict)][:5]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            source = _runtime_source_label(str(item.get("source") or ""))
            operation = _runtime_action_label(str(item.get("operation") or ""))
            label = str(item.get("label") or item.get("kind") or "契约问题")
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- {severity_label}｜{source}.{operation}｜{label}")
            if recommendation:
                lines.append(f"  建议：{recommendation}")
    snapshot_summary = snapshot.get("snapshot_summary") if isinstance(snapshot.get("snapshot_summary"), dict) else {}
    runtime_state = snapshot_summary.get("runtime_state") if isinstance(snapshot_summary.get("runtime_state"), dict) else {}
    if runtime_state:
        lines.append(
            "运行状态："
            f"{runtime_state.get('label') or runtime_state.get('code') or '未知'}"
            f"｜级别：{runtime_state.get('severity') or '未知'}"
            f"｜原因：{runtime_state.get('reason') or '无'}"
            f"｜耗时 {runtime_state.get('total_ms', 0)}ms"
            f"｜动作等待 {runtime_state.get('latest_action_age_seconds', 0)} 秒。"
        )
    runtime_gate = snapshot.get("runtime_gate") if isinstance(snapshot.get("runtime_gate"), dict) else {}
    if runtime_gate:
        lines.append(f"运行门禁：{runtime_gate.get('label') or runtime_gate.get('status') or '未知'}。")
        severity_counts = runtime_gate.get("severity_counts") if isinstance(runtime_gate.get("severity_counts"), dict) else {}
        if severity_counts:
            lines.append(
                "门禁风险："
                f"严重 {severity_counts.get('critical', 0)}，"
                f"高 {severity_counts.get('high', 0)}，"
                f"中 {severity_counts.get('medium', 0)}，"
                f"低 {severity_counts.get('low', 0)}。"
            )
        priority_items = runtime_gate.get("priority_items") if isinstance(runtime_gate.get("priority_items"), list) else []
        if priority_items:
            lines.append("优先修复：")
            for item in [entry for entry in priority_items if isinstance(entry, dict)][:4]:
                severity = str(item.get("severity") or "medium")
                severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
                label = str(item.get("label") or item.get("key") or "未命名风险")
                detail = str(item.get("detail") or "").strip()
                recommendation = str(item.get("recommendation") or "").strip()
                fingerprint = str(item.get("fingerprint") or "").strip()
                lines.append(f"- {severity_label}" + (f"｜#{fingerprint}" if fingerprint else "") + f"｜{label}" + (f"｜{detail}" if detail else ""))
                if recommendation:
                    lines.append(f"  建议：{recommendation}")
        elif runtime_gate.get("next_step"):
            lines.append(f"门禁建议：{runtime_gate.get('next_step')}")
    context_health = snapshot.get("context_health") if isinstance(snapshot.get("context_health"), dict) else {}
    if context_health:
        lines.append(
            "上下文健康："
            f"{context_health.get('label') or context_health.get('status') or '未知'}"
            f"｜Identity:{'有' if context_health.get('identity_available') else '缺'}"
            f"｜Session:{'有' if context_health.get('session_available') else '缺'}"
            f"｜Profile:{'有' if context_health.get('profile_available') else '缺'}"
            f"｜Message:{'有' if context_health.get('current_message_available') else '缺'}"
            f"｜Result:{'有' if context_health.get('result_context_available') else '无'}"
            f"｜问题 {context_health.get('issue_count', 0)} 个。"
        )
        context_issues = context_health.get("issues") if isinstance(context_health.get("issues"), list) else []
        for item in [entry for entry in context_issues if isinstance(entry, dict)][:4]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            label = str(item.get("label") or item.get("kind") or "上下文问题")
            detail = str(item.get("detail") or "").strip()
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- {severity_label}｜{label}" + (f"｜{detail}" if detail else ""))
            if recommendation:
                lines.append(f"  建议：{recommendation}")
    profile_health = snapshot.get("profile_health") if isinstance(snapshot.get("profile_health"), dict) else {}
    if profile_health:
        lines.append(
            "Profile 隔离："
            f"{profile_health.get('label') or profile_health.get('status') or '未知'}"
            f"｜风格：{profile_health.get('style') or '默认'}"
            f"｜详细度：{profile_health.get('verbosity') or '默认'}"
            f"｜{'使用格式化' if profile_health.get('use_formatting') else '不强制格式化'}"
            f"｜问题 {profile_health.get('issue_count', 0)} 个。"
        )
        allowed_effects = profile_health.get("allowed_effects") if isinstance(profile_health.get("allowed_effects"), list) else []
        forbidden_effects = profile_health.get("forbidden_effects") if isinstance(profile_health.get("forbidden_effects"), list) else []
        if allowed_effects or forbidden_effects:
            lines.append(
                "Profile 边界："
                f"只影响 {'、'.join(str(item) for item in allowed_effects[:4]) or '回答呈现'}"
                f"；禁止影响 {'、'.join(str(item) for item in forbidden_effects[:6]) or '控制面'}。"
            )
        profile_issues = profile_health.get("issues") if isinstance(profile_health.get("issues"), list) else []
        for item in [entry for entry in profile_issues if isinstance(entry, dict)][:4]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            label = str(item.get("label") or item.get("kind") or "Profile 问题")
            detail = str(item.get("detail") or "").strip()
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- {severity_label}｜{label}" + (f"｜{detail}" if detail else ""))
            if recommendation:
                lines.append(f"  建议：{recommendation}")
    followup_health = snapshot.get("followup_health") if isinstance(snapshot.get("followup_health"), dict) else {}
    if followup_health:
        lines.append(
            "追问健康："
            f"{followup_health.get('label') or followup_health.get('status') or '未知'}"
            f"｜{'使用上一轮结果' if followup_health.get('uses_previous_result') else '非追问'}"
            f"｜类型：{_runtime_followup_type_label(str(followup_health.get('followup_type') or ''))}"
            f"｜{_runtime_result_context_kind_label(str(followup_health.get('context_kind') or ''))}"
            f"｜items {followup_health.get('item_count', 0)}"
            f"｜问题 {followup_health.get('issue_count', 0)} 个。"
        )
        followup_issues = followup_health.get("issues") if isinstance(followup_health.get("issues"), list) else []
        for item in [entry for entry in followup_issues if isinstance(entry, dict)][:4]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            label = str(item.get("label") or item.get("kind") or "追问问题")
            detail = str(item.get("detail") or "").strip()
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- {severity_label}｜{label}" + (f"｜{detail}" if detail else ""))
            if recommendation:
                lines.append(f"  建议：{recommendation}")
    followup_contract = snapshot.get("followup_consume_contract") if isinstance(snapshot.get("followup_consume_contract"), dict) else {}
    if followup_contract:
        lines.append(
            "追问消费定位："
            f"{followup_contract.get('label') or followup_contract.get('status') or '未知'}"
            f"｜{'结构化条目优先' if followup_contract.get('prefer_items') else '未声明 items 优先'}"
            f"｜可按序号：{'是' if followup_contract.get('supports_index_followup') else '否'}"
            f"｜可看详情：{'是' if followup_contract.get('supports_detail_followup') else '否'}"
            f"｜过期刷新：{'是' if followup_contract.get('requires_refresh_when_expired') else '否'}"
            f"｜问题 {followup_contract.get('issue_count', 0)} 个。"
        )
        contract_issues = followup_contract.get("issues") if isinstance(followup_contract.get("issues"), list) else []
        for item in [entry for entry in contract_issues if isinstance(entry, dict)][:4]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            label = str(item.get("label") or item.get("kind") or "追问消费问题")
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- {severity_label}｜{label}")
            if recommendation:
                lines.append(f"  建议：{recommendation}")
    decision_health = snapshot.get("decision_health") if isinstance(snapshot.get("decision_health"), dict) else {}
    if decision_health:
        sources = decision_health.get("sources") if isinstance(decision_health.get("sources"), list) else []
        lines.append(
            "决策健康："
            f"{decision_health.get('label') or decision_health.get('status') or '未知'}"
            f"｜{_runtime_question_type_label(str(decision_health.get('question_type') or ''))}"
            f"｜{_runtime_data_scope_label(str(decision_health.get('data_scope') or ''))}"
            f"｜{_runtime_action_label(str(decision_health.get('strategy') or ''))}"
            f"｜来源：{'、'.join(_runtime_source_label(str(source)) for source in sources) or '无'}"
            f"｜置信度 {decision_health.get('confidence', 0)}"
            f"｜{'需澄清' if decision_health.get('should_clarify') else '可执行'}。"
        )
        missing_params = decision_health.get("missing_params") if isinstance(decision_health.get("missing_params"), list) else []
        if missing_params:
            lines.append("缺少参数：" + "、".join(str(item) for item in missing_params[:6]))
        decision_issues = decision_health.get("issues") if isinstance(decision_health.get("issues"), list) else []
        for item in [entry for entry in decision_issues if isinstance(entry, dict)][:4]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            label = str(item.get("label") or item.get("kind") or "决策问题")
            detail = str(item.get("detail") or "").strip()
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- {severity_label}｜{label}" + (f"｜{detail}" if detail else ""))
            if recommendation:
                lines.append(f"  建议：{recommendation}")
    router_health = snapshot.get("router_health") if isinstance(snapshot.get("router_health"), dict) else {}
    if router_health:
        planned_sources = router_health.get("planned_sources") if isinstance(router_health.get("planned_sources"), list) else []
        executed_sources = router_health.get("executed_unique_sources") if isinstance(router_health.get("executed_unique_sources"), list) else []
        lines.append(
            "Router 健康："
            f"{router_health.get('label') or router_health.get('status') or '未知'}"
            f"｜计划：{'、'.join(_runtime_source_label(str(source)) for source in planned_sources) or '无'}"
            f"｜实际：{'、'.join(_runtime_source_label(str(source)) for source in executed_sources) or '无'}"
            f"｜覆盖率 {router_health.get('coverage_percent', 0)}%"
            f"｜问题 {router_health.get('issue_count', 0)} 个。"
        )
        router_issues = router_health.get("issues") if isinstance(router_health.get("issues"), list) else []
        for item in [entry for entry in router_issues if isinstance(entry, dict)][:4]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            label = str(item.get("label") or item.get("kind") or "Router 问题")
            detail = str(item.get("detail") or "").strip()
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- {severity_label}｜{label}" + (f"｜{detail}" if detail else ""))
            if recommendation:
                lines.append(f"  建议：{recommendation}")
    execution_health = snapshot.get("execution_health") if isinstance(snapshot.get("execution_health"), dict) else {}
    if execution_health:
        lines.append(
            "执行健康："
            f"{execution_health.get('label') or execution_health.get('status') or '未知'}"
            f"｜{_runtime_stage_label(str(execution_health.get('execution_status') or 'unknown'))}"
            f"｜Provider {execution_health.get('provider_count', 0)}"
            f"｜成功 {execution_health.get('success_count', 0)}"
            f"｜失败 {execution_health.get('error_count', 0)}"
            f"｜无权限 {execution_health.get('denied_count', 0)}"
            f"｜跳过 {execution_health.get('skipped_count', 0)}"
            f"｜{'有上下文' if execution_health.get('has_result_context') else '无上下文'}"
            f"｜问题 {execution_health.get('issue_count', 0)} 个。"
        )
        execution_issues = execution_health.get("issues") if isinstance(execution_health.get("issues"), list) else []
        for item in [entry for entry in execution_issues if isinstance(entry, dict)][:4]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            label = str(item.get("label") or item.get("kind") or "执行问题")
            detail = str(item.get("detail") or "").strip()
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- {severity_label}｜{label}" + (f"｜{detail}" if detail else ""))
            if recommendation:
                lines.append(f"  建议：{recommendation}")
    answer_health = snapshot.get("answer_health") if isinstance(snapshot.get("answer_health"), dict) else {}
    if answer_health:
        lines.append(
            "答案健康："
            f"{answer_health.get('label') or answer_health.get('status') or '未知'}"
            f"｜{answer_health.get('answer_chars', 0)} 字"
            f"｜{'需确认' if answer_health.get('requires_confirmation') else '无需确认'}"
            f"｜{'发现技术载荷' if answer_health.get('raw_payload_detected') else '未见技术载荷'}"
            f"｜问题 {answer_health.get('issue_count', 0)} 个。"
        )
        answer_issues = answer_health.get("issues") if isinstance(answer_health.get("issues"), list) else []
        for item in [entry for entry in answer_issues if isinstance(entry, dict)][:4]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            label = str(item.get("label") or item.get("kind") or "答案问题")
            detail = str(item.get("detail") or "").strip()
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- {severity_label}｜{label}" + (f"｜{detail}" if detail else ""))
            if recommendation:
                lines.append(f"  建议：{recommendation}")
    action_closure = snapshot.get("action_closure") if isinstance(snapshot.get("action_closure"), dict) else {}
    if action_closure.get("latest_available"):
        action_id_tail = str(action_closure.get("latest_action_id") or "")[-8:]
        receipt_label = (
            "已关联回执"
            if action_closure.get("has_correlated_receipt")
            else "已有动作回执"
            if action_closure.get("has_action_receipt_event")
            else "暂无动作回执"
        )
        action_parts = [
            _runtime_action_kind_label(str(action_closure.get("latest_kind") or "")),
            _runtime_action_label(str(action_closure.get("latest_action") or "")),
            _runtime_stage_label(str(action_closure.get("latest_status") or "unknown")),
        ]
        if action_id_tail:
            action_parts.append(f"动作ID {action_id_tail}")
        action_parts.extend(
            [
                f"已等待 {action_closure.get('latest_age_seconds', 0)} 秒",
                "疑似卡住" if action_closure.get("latest_stuck") else "未超时",
                receipt_label,
            ]
        )
        lines.append("动作闭环：" + "｜".join(part for part in action_parts if part) + "。")
        contract_label = str(action_closure.get("contract_label") or "").strip()
        contract_next_step = str(action_closure.get("contract_next_step") or "").strip()
        contract_severity = str(action_closure.get("contract_severity") or "").strip()
        if contract_label:
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(contract_severity, contract_severity or "低")
            lines.append(
                "动作闭环定位："
                f"{contract_label}"
                f"｜级别：{severity_label}"
                + (f"｜下一步：{contract_next_step}" if contract_next_step else "")
            )
        receipt_contract_label = "回执已闭环" if action_closure.get("has_action_receipt_event") else "等待动作回执"
        if action_closure.get("latest_terminal") and not action_closure.get("has_action_receipt_event"):
            receipt_contract_label = "最终状态缺少回执"
        elif action_closure.get("latest_pending") and not action_closure.get("has_action_receipt_event"):
            receipt_contract_label = "处理中，尚未产生回执"
        receipt_status = str(action_closure.get("latest_receipt_status") or "").strip()
        receipt_group = str(action_closure.get("latest_receipt_status_group") or "").strip()
        lines.append(
            "动作回执契约："
            f"{receipt_contract_label}"
            f"｜关联：{'是' if action_closure.get('has_correlated_receipt') else '否'}"
            f"｜终态：{'是' if action_closure.get('latest_terminal') else '否'}"
            f"｜等待：{'是' if action_closure.get('latest_pending') else '否'}"
            + (f"｜回执状态：{_runtime_stage_label(receipt_status)}" if receipt_status else "")
            + (f"｜回执分组：{_runtime_action_status_group_label(receipt_group)}" if receipt_group else "")
            + "。"
        )
        missing_fields = action_closure.get("missing_fields") if isinstance(action_closure.get("missing_fields"), list) else []
        if missing_fields:
            lines.append("动作追踪缺字段：" + "、".join(str(item) for item in missing_fields[:6]))
        top_repair_item = action_closure.get("top_repair_item") if isinstance(action_closure.get("top_repair_item"), dict) else {}
        repair_queue = action_closure.get("repair_queue") if isinstance(action_closure.get("repair_queue"), list) else []
        if top_repair_item:
            lines.append(
                "动作闭环下一修复："
                f"{top_repair_item.get('label') or top_repair_item.get('kind')}"
                f"｜优先级 {top_repair_item.get('priority_label', '未知')}"
                f"｜{top_repair_item.get('detail', '')}"
                f"｜下一步：{top_repair_item.get('next_step', '')}"
            )
        if repair_queue:
            lines.append(f"动作闭环修复队列：{len(repair_queue)} 项。")
    action_timeline = snapshot.get("action_timeline") if isinstance(snapshot.get("action_timeline"), dict) else {}
    if action_timeline.get("available"):
        lines.append(
            "动作时间线："
            f"{action_timeline.get('event_count', 0)} 个事件"
            f"｜动作 {action_timeline.get('action_event_count', 0)}"
            f"｜上下文 {action_timeline.get('result_context_event_count', 0)}"
            f"｜回执 {action_timeline.get('action_receipt_event_count', 0)}。"
        )
        timeline_events = action_timeline.get("events") if isinstance(action_timeline.get("events"), list) else []
        for item in [entry for entry in timeline_events if isinstance(entry, dict)][-4:]:
            label = str(item.get("label") or item.get("kind") or "")
            detail = str(item.get("detail") or item.get("status") or "").strip()
            lines.append(f"- {label}" + (f"｜{detail}" if detail else ""))
    permission_health = snapshot.get("permission_health") if isinstance(snapshot.get("permission_health"), dict) else {}
    if permission_health:
        lines.append(
            f"权限健康：{permission_health.get('label') or permission_health.get('status') or '未知'}"
            f"｜实际身份：{_runtime_execution_identity_label(str(permission_health.get('execution_identity') or ''))}"
            f"｜默认身份：{_runtime_execution_identity_label(str(permission_health.get('default_identity') or ''))}"
            f"｜策略：{'显式覆盖' if permission_health.get('identity_override') else '默认'}"
            f"｜问题 {permission_health.get('issue_count', 0)} 个。"
        )
        permission_issues = permission_health.get("issues") if isinstance(permission_health.get("issues"), list) else []
        for item in [entry for entry in permission_issues if isinstance(entry, dict)][:4]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            label = str(item.get("label") or item.get("kind") or "权限问题")
            detail = str(item.get("detail") or "").strip()
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- {severity_label}｜{label}" + (f"｜{detail}" if detail else ""))
            if recommendation:
                lines.append(f"  建议：{recommendation}")
    result_context_quality = snapshot.get("result_context_quality") if isinstance(snapshot.get("result_context_quality"), dict) else {}
    if result_context_quality.get("available"):
        lines.append(
            "结果上下文质量："
            f"{result_context_quality.get('label') or result_context_quality.get('status') or '未知'}"
            f"｜{_runtime_result_context_kind_label(str(result_context_quality.get('context_kind') or ''))}"
            f"｜{result_context_quality.get('result_type') or '未知类型'}"
            f"｜{result_context_quality.get('item_count', 0)}/{result_context_quality.get('count', 0)} 条"
            f"｜证据 {result_context_quality.get('provider_evidence_count', 0)} 个"
            f"｜可追问字段 {result_context_quality.get('followup_field_count', 0)} 个"
            f"｜标识字段 {result_context_quality.get('item_identity_field_count', 0)} 个"
            f"｜{'items优先' if result_context_quality.get('prefer_items') else '未声明items优先'}"
            f"｜主消费：{result_context_quality.get('primary_consume_source') or '未知'}"
            f"｜{'answer含技术载荷' if result_context_quality.get('raw_answer_detected') else 'answer正常'}"
            f"｜已存活 {result_context_quality.get('age_seconds', 0)} 秒"
            f"｜剩余 {result_context_quality.get('expires_in_seconds', 0)} 秒。"
        )
        consume_order = result_context_quality.get("consume_order") if isinstance(result_context_quality.get("consume_order"), list) else []
        if consume_order:
            lines.append(
                "上下文消费顺序："
                + " -> ".join(str(item.get("source") or "") for item in consume_order if isinstance(item, dict))
                + f"｜answer：{result_context_quality.get('answer_fallback_label') or '未知'}。"
            )
        followup_fields = result_context_quality.get("followup_fields") if isinstance(result_context_quality.get("followup_fields"), list) else []
        if followup_fields:
            lines.append("可追问字段：" + "、".join(str(item) for item in followup_fields[:10]))
        quality_issues = result_context_quality.get("issues") if isinstance(result_context_quality.get("issues"), list) else []
        for item in [entry for entry in quality_issues if isinstance(entry, dict)][:4]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            label = str(item.get("label") or item.get("kind") or "上下文问题")
            detail = str(item.get("detail") or "").strip()
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- {severity_label}｜{label}" + (f"｜{detail}" if detail else ""))
            if recommendation:
                lines.append(f"  建议：{recommendation}")
    pipeline_health = snapshot.get("pipeline_health") if isinstance(snapshot.get("pipeline_health"), dict) else {}
    if pipeline_health:
        lines.append(
            "Pipeline 健康："
            f"{pipeline_health.get('label') or pipeline_health.get('status') or '未知'}"
            f"｜阶段 {pipeline_health.get('stage_count', 0)} 个"
            f"｜问题 {pipeline_health.get('issue_count', 0)} 个。"
        )
        pipeline_issues = pipeline_health.get("issues") if isinstance(pipeline_health.get("issues"), list) else []
        for item in [entry for entry in pipeline_issues if isinstance(entry, dict)][:4]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            label = str(item.get("label") or item.get("kind") or "阶段问题")
            detail = str(item.get("detail") or item.get("stage") or "").strip()
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- {severity_label}｜{label}" + (f"｜{detail}" if detail else ""))
            if recommendation:
                lines.append(f"  建议：{recommendation}")
    if pipeline_constitution_contract:
        contract_issues = pipeline_constitution_contract.get("issues") if isinstance(pipeline_constitution_contract.get("issues"), list) else []
        if contract_issues:
            lines.append("主链路契约问题：")
            for item in [entry for entry in contract_issues if isinstance(entry, dict)][:5]:
                severity = str(item.get("severity") or "medium")
                severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
                label = str(item.get("label") or item.get("kind") or "主链路问题")
                detail = str(item.get("detail") or "").strip()
                recommendation = str(item.get("recommendation") or "").strip()
                lines.append(f"- {severity_label}｜{label}" + (f"｜{detail}" if detail else ""))
                if recommendation:
                    lines.append(f"  建议：{recommendation}")
    if skill_registry:
        unexecutable_operations = (
            skill_registry.get("exposed_unexecutable_operations")
            if isinstance(skill_registry.get("exposed_unexecutable_operations"), list)
            else []
        )
        if unexecutable_operations:
            lines.append("已开放但不可执行的原子能力：")
            for item in [entry for entry in unexecutable_operations if isinstance(entry, dict)][:6]:
                source = _runtime_source_label(str(item.get("source") or ""))
                operation = _runtime_action_label(str(item.get("operation") or ""))
                label = str(item.get("label") or "").strip()
                lines.append(f"- {source}｜{operation}" + (f"｜{label}" if label else "") + "｜建议：补 Provider 或先关闭开放。")
    timing_health = snapshot.get("timing_health") if isinstance(snapshot.get("timing_health"), dict) else {}
    if timing_health:
        lines.append(
            "耗时健康："
            f"{timing_health.get('label') or timing_health.get('status') or '未知'}"
            f"｜总耗时 {timing_health.get('total_ms', 0)}ms"
            f"｜最慢阶段：{_runtime_stage_name_label(str(timing_health.get('slowest_stage') or ''))}"
            f" {timing_health.get('slowest_ms', 0)}ms"
            f"｜Provider {timing_health.get('provider_total_ms', 0)}ms"
            f"｜最慢 Provider：{_runtime_source_label(str(timing_health.get('slowest_provider_source') or ''))}"
            f" {timing_health.get('slowest_provider_ms', 0)}ms"
            f"｜问题 {timing_health.get('issue_count', 0)} 个。"
        )
        timing_issues = timing_health.get("issues") if isinstance(timing_health.get("issues"), list) else []
        for item in [entry for entry in timing_issues if isinstance(entry, dict)][:4]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            label = str(item.get("label") or item.get("kind") or "耗时问题")
            detail = str(item.get("detail") or "").strip()
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- {severity_label}｜{label}" + (f"｜{detail}" if detail else ""))
            if recommendation:
                lines.append(f"  建议：{recommendation}")
    constitution_guard = snapshot.get("constitution_guard") if isinstance(snapshot.get("constitution_guard"), dict) else {}
    if constitution_guard:
        lines.append(
            "架构守卫："
            + ("通过" if constitution_guard.get("healthy") else f"发现 {constitution_guard.get('failed_count', 0)} 项风险")
        )
        checks = constitution_guard.get("checks") if isinstance(constitution_guard.get("checks"), list) else []
        for item in [entry for entry in checks if isinstance(entry, dict)][:5]:
            label = str(item.get("label") or item.get("key") or "")
            detail = str(item.get("detail") or "").strip()
            if item.get("ok"):
                lines.append(f"- 通过｜{label}" + (f"｜{detail}" if detail else ""))
                continue
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- 风险｜{severity_label}｜{label}" + (f"｜{detail}" if detail else ""))
            if recommendation:
                lines.append(f"  建议：{recommendation}")
    drift_categories = status_summary.get("drift_categories") if isinstance(status_summary.get("drift_categories"), list) else []
    if drift_categories:
        lines.append("漂移分类：")
        for item in [entry for entry in drift_categories if isinstance(entry, dict)][:5]:
            label = str(item.get("label") or item.get("kind") or "未分类")
            count = int(item.get("count") or 0)
            reason = str(item.get("reason") or "").strip()
            lines.append(f"- {label}：{count} 项" + (f"｜{reason}" if reason else ""))
    next_step = _runtime_next_step(drift, planner_drift, pending_ops)
    if next_step:
        lines.append(f"建议下一步：{next_step}")
    pipeline_timing = snapshot.get("pipeline_timing") if isinstance(snapshot.get("pipeline_timing"), dict) else {}
    if pipeline_timing:
        lines.append(
            "本轮耗时："
            f"{pipeline_timing.get('total_ms', 0)}ms，"
            f"最慢阶段：{_runtime_stage_name_label(str(pipeline_timing.get('slowest_stage') or ''))}"
            f" {pipeline_timing.get('slowest_ms', 0)}ms。"
        )
        timing_advice = _runtime_pipeline_timing_advice(pipeline_timing)
        if timing_advice:
            lines.append(f"耗时诊断：{timing_advice}")
    decision_summary = snapshot.get("decision_summary") if isinstance(snapshot.get("decision_summary"), dict) else {}
    if decision_summary:
        sources = decision_summary.get("sources") if isinstance(decision_summary.get("sources"), list) else []
        confirmation_reasons = decision_summary.get("confirmation_reasons") if isinstance(decision_summary.get("confirmation_reasons"), list) else []
        lines.append(
            "本轮决策摘要："
            f"{_runtime_question_type_label(str(decision_summary.get('question_type') or ''))}"
            f"｜{_runtime_action_label(str(decision_summary.get('strategy') or ''))}"
            f"｜来源：{'、'.join(_runtime_source_label(str(source)) for source in sources) or '无'}"
            f"｜{_runtime_execution_identity_label(str(decision_summary.get('execution_identity') or ''))}"
            f"｜{'需确认' if decision_summary.get('requires_confirmation') else '无需确认'}"
            f"｜{_runtime_stage_label(str(decision_summary.get('execution_status') or 'unknown'))}"
        )
        if confirmation_reasons:
            lines.append("确认原因：" + "、".join(_runtime_confirmation_reason_label(str(item)) for item in confirmation_reasons))
        provider_count = int(decision_summary.get("provider_success_count") or 0) + int(decision_summary.get("provider_error_count") or 0) + int(decision_summary.get("provider_denied_count") or 0)
        if provider_count:
            lines.append(
                "Provider 摘要："
                f"成功 {decision_summary.get('provider_success_count', 0)}，"
                f"失败 {decision_summary.get('provider_error_count', 0)}，"
                f"无权限 {decision_summary.get('provider_denied_count', 0)}，"
                f"总耗时 {decision_summary.get('provider_total_duration_ms', 0)}ms。"
            )
    provider_summary = snapshot.get("provider_summary") if isinstance(snapshot.get("provider_summary"), dict) else {}
    provider_by_source = provider_summary.get("by_source") if isinstance(provider_summary.get("by_source"), list) else []
    if provider_by_source:
        lines.append("本轮 Provider 来源：")
        for item in [entry for entry in provider_by_source if isinstance(entry, dict)][:5]:
            operations = item.get("operations") if isinstance(item.get("operations"), list) else []
            lines.append(
                f"- {_runtime_source_label(str(item.get('source') or ''))}"
                f"｜调用 {item.get('count', 0)} 次"
                f"｜成功 {item.get('success_count', 0)}"
                f"｜失败 {item.get('error_count', 0)}"
                f"｜耗时 {item.get('total_duration_ms', 0)}ms"
                + (f"｜操作：{', '.join(_runtime_action_label(str(op)) for op in operations[:3])}" if operations else "")
            )
    evidence_contract = provider_summary.get("evidence_contract") if isinstance(provider_summary.get("evidence_contract"), dict) else {}
    if evidence_contract:
        lines.append(
            "Provider 证据契约："
            f"{evidence_contract.get('label') or evidence_contract.get('status') or '未知'}"
            f"｜Provider {evidence_contract.get('provider_count', 0)} 个"
            f"｜问题 {evidence_contract.get('issue_count', 0)} 个"
            f"｜阻断 {evidence_contract.get('blocking_count', 0)} 个。"
        )
        top_issue = evidence_contract.get("top_issue") if isinstance(evidence_contract.get("top_issue"), dict) else {}
        if top_issue:
            lines.append(
                "Provider 证据下一修复："
                f"{top_issue.get('label') or top_issue.get('kind')}"
                f"｜{_runtime_source_label(str(top_issue.get('source') or ''))}"
                + (f"｜{_runtime_action_label(str(top_issue.get('operation') or ''))}" if top_issue.get("operation") else "")
                + (f"｜下一步：{top_issue.get('next_step')}" if top_issue.get("next_step") else "")
            )
    latency = provider_summary.get("latency_diagnostics") if isinstance(provider_summary.get("latency_diagnostics"), dict) else {}
    if latency:
        lines.append(
            "Provider 慢调用："
            f"{latency.get('label') or latency.get('status') or '未知'}"
            f"｜总耗时 {latency.get('total_duration_ms', 0)}ms"
            f"｜慢 Provider {latency.get('slow_provider_count', 0)} 个"
            f"｜慢子步骤 {latency.get('slow_substep_count', 0)} 个"
            f"｜缓存命中 {latency.get('cache_hit_count', 0)}/{latency.get('cache_known_count', 0)}。"
        )
        top_slow = latency.get("top_slow_item") if isinstance(latency.get("top_slow_item"), dict) else {}
        if top_slow:
            lines.append(
                "Provider 慢调用下一优化："
                f"{top_slow.get('label') or top_slow.get('kind')}"
                f"｜{_runtime_source_label(str(top_slow.get('source') or ''))}"
                + (f"｜{_runtime_action_label(str(top_slow.get('operation') or ''))}" if top_slow.get("operation") else "")
                + (f"｜{top_slow.get('step')}" if top_slow.get("step") else "")
                + f"｜耗时 {top_slow.get('duration_ms', 0)}ms"
                + (f"｜原因：{top_slow.get('reason')}" if top_slow.get("reason") else "")
                + (f"｜下一步：{top_slow.get('next_step')}" if top_slow.get("next_step") else "")
            )
    fast_response = snapshot.get("fast_response_contract") if isinstance(snapshot.get("fast_response_contract"), dict) else {}
    if fast_response:
        lines.append(
            "首包/异步契约："
            f"{fast_response.get('label') or fast_response.get('status') or '未知'}"
            f"｜总耗时 {fast_response.get('total_ms', 0)}ms"
            f"｜首包：{'需要' if fast_response.get('fast_ack_required') else '非必需'}"
            f"｜异步：{'建议' if fast_response.get('async_recommended') else '非必需'}"
            f"｜回执：{'需要' if fast_response.get('progress_receipt_required') else '非必需'}"
            f"｜已有回执：{'是' if fast_response.get('has_action_receipt') else '否'}。"
        )
        top_fast_issue = fast_response.get("top_issue") if isinstance(fast_response.get("top_issue"), dict) else {}
        if top_fast_issue:
            lines.append(
                "首包/异步下一修复："
                f"{top_fast_issue.get('label') or top_fast_issue.get('kind')}"
                + (f"｜{top_fast_issue.get('detail')}" if top_fast_issue.get("detail") else "")
                + (f"｜下一步：{top_fast_issue.get('next_step')}" if top_fast_issue.get("next_step") else "")
            )
    provider_failure_summary = provider_summary.get("failure_summary") if isinstance(provider_summary.get("failure_summary"), dict) else {}
    if int(provider_failure_summary.get("issue_count") or 0) > 0:
        categories = provider_failure_summary.get("categories") if isinstance(provider_failure_summary.get("categories"), dict) else {}
        category_labels = {
            "missing_params": "缺参数",
            "permission_or_auth": "权限/授权",
            "not_open": "未开放",
            "execution_failed": "执行失败",
            "provider_missing": "未注册",
            "empty_result": "空结果",
            "unknown": "未知",
        }
        category_text = "｜".join(
            f"{category_labels.get(str(key), str(key))} {value}"
            for key, value in categories.items()
            if int(value or 0) > 0
        )
        lines.append(
            f"Provider 失败定位：阻断 {provider_failure_summary.get('blocking_count', 0)} 项"
            + (f"｜{category_text}" if category_text else "")
            + "。"
        )
        failure_items = provider_failure_summary.get("items") if isinstance(provider_failure_summary.get("items"), list) else []
        for item in [entry for entry in failure_items if isinstance(entry, dict)][:3]:
            source = _runtime_source_label(str(item.get("source") or ""))
            operation = _runtime_action_label(str(item.get("operation") or ""))
            reason = _runtime_error_type_label(str(item.get("reason") or item.get("error_type") or ""))
            category = str(item.get("category_label") or "").strip()
            next_step = str(item.get("next_step") or "").strip()
            lines.append(
                f"- {source}"
                + (f"｜{operation}" if operation else "")
                + (f"｜{category}" if category else "")
                + (f"｜{reason}" if reason else "")
            )
            if next_step:
                lines.append(f"  下一步：{next_step}")
    provider_quality_issues = provider_summary.get("quality_issues") if isinstance(provider_summary.get("quality_issues"), list) else []
    if provider_quality_issues:
        lines.append("Provider 质量问题：")
        for item in [entry for entry in provider_quality_issues if isinstance(entry, dict)][:5]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            label = str(item.get("label") or item.get("kind") or "结果质量问题")
            detail = str(item.get("detail") or "").strip()
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- {severity_label}｜{label}" + (f"｜{detail}" if detail else ""))
            if recommendation:
                lines.append(f"  建议：{recommendation}")
    provider_recommendations = provider_summary.get("recommendations") if isinstance(provider_summary.get("recommendations"), list) else []
    if provider_recommendations:
        lines.append("Provider 修复建议：")
        for item in [entry for entry in provider_recommendations if isinstance(entry, dict)][:5]:
            source = _runtime_source_label(str(item.get("source") or ""))
            operation = _runtime_action_label(str(item.get("operation") or ""))
            error_type = _runtime_error_type_label(str(item.get("error_type") or ""))
            pending_reason = str(item.get("pending_reason") or "").strip()
            next_step = str(item.get("recommended_next_step") or "").strip()
            lines.append(
                f"- {source}"
                + (f"｜{operation}" if operation else "")
                + (f"｜{error_type}" if error_type else "")
                + (f"｜{pending_reason}" if pending_reason else "")
                + (f"｜建议：{next_step}" if next_step else "")
            )
    result_context = snapshot.get("result_context") if isinstance(snapshot.get("result_context"), dict) else {}
    if result_context.get("available"):
        sources = result_context.get("sources") if isinstance(result_context.get("sources"), list) else []
        context_kind = _runtime_result_context_kind_label(str(result_context.get("context_kind") or "query_result"))
        lines.append(
            "当前结果上下文："
            f"{context_kind}，{result_context.get('result_type') or '未知类型'}，"
            f"{_runtime_question_type_label(str(result_context.get('question_type') or ''))}，"
            f"{_runtime_data_scope_label(str(result_context.get('data_scope') or ''))}，"
            f"{result_context.get('display_count', result_context.get('count', 0))} 条，"
            f"来源：{'、'.join(_runtime_source_label(str(source)) for source in sources) or '未知'}，"
            f"证据 {result_context.get('provider_evidence_count', 0)} 个，"
            f"证据耗时 {result_context.get('provider_evidence_duration_ms', 0)}ms，"
            f"剩余 {result_context.get('expires_in_seconds', 0)} 秒，"
            f"{'可继续动作' if result_context.get('actionable') else '仅可追问'}。"
        )
        if result_context.get("execution_status"):
            lines.append(f"上下文执行状态：{_runtime_stage_label(str(result_context.get('execution_status') or 'unknown'))}")
        source_execution_status = result_context.get("source_execution_status") if isinstance(result_context.get("source_execution_status"), dict) else {}
        if source_execution_status:
            planned_sources = source_execution_status.get("planned_sources") if isinstance(source_execution_status.get("planned_sources"), list) else []
            executed_sources = source_execution_status.get("executed_sources") if isinstance(source_execution_status.get("executed_sources"), list) else []
            error_sources = source_execution_status.get("error_sources") if isinstance(source_execution_status.get("error_sources"), list) else []
            lines.append(
                "上下文执行链："
                f"计划 {'、'.join(_runtime_source_label(str(source)) for source in planned_sources) or '无'}"
                f"｜已执行 {'、'.join(_runtime_source_label(str(source)) for source in executed_sources) or '无'}"
                + (f"｜异常 {'、'.join(_runtime_source_label(str(source)) for source in error_sources)}" if error_sources else "")
                + f"｜步骤 {result_context.get('source_execution_step_count', 0)} 个。"
            )
        followup_fields = _runtime_result_context_followup_fields(result_context)
        if followup_fields:
            lines.append("可追问字段：" + "、".join(followup_fields))
        if result_context.get("empty_result"):
            reason = _runtime_empty_reason_label(str(result_context.get("empty_reason") or ""))
            next_step = str(result_context.get("recommended_next_step") or "").strip()
            lines.append(f"空结果原因：{reason}" + (f"｜建议：{next_step}" if next_step else ""))
        query_id = str(result_context.get("query_id") or "").strip()
        if query_id:
            lines.append(f"上下文编号：{query_id[-12:]}")
        item_summaries = result_context.get("item_summaries") if isinstance(result_context.get("item_summaries"), list) else []
        if item_summaries:
            lines.append("动作回执摘要：" if str(result_context.get("context_kind") or "") == "action_receipt" else "上下文摘要：")
            lines.extend(f"- {summary}" for summary in item_summaries[:3])
    else:
        lines.append("当前结果上下文：无。")
    answer_metadata = snapshot.get("answer_metadata") if isinstance(snapshot.get("answer_metadata"), dict) else {}
    if answer_metadata.get("followup_type"):
        lines.append(
            "本轮追问："
            f"{_runtime_followup_type_label(str(answer_metadata.get('followup_type') or ''))}"
            + (f"｜{_runtime_result_context_kind_label(str(answer_metadata.get('context_kind') or ''))}" if answer_metadata.get("context_kind") else "")
            + (f"｜{answer_metadata.get('item_count')} 条" if answer_metadata.get("item_count") not in (None, "") else "")
        )
    context_event_summary = snapshot.get("result_context_event_summary") if isinstance(snapshot.get("result_context_event_summary"), dict) else {}
    if context_event_summary.get("available"):
        lines.append(
            "结果上下文事件摘要："
            f"事件 {context_event_summary.get('event_count', 0)} 个"
            f"｜保存 {context_event_summary.get('save_count', 0)} 次"
            f"｜清除 {context_event_summary.get('clear_count', 0)} 次"
            f"｜待确认 {context_event_summary.get('pending_confirmation_count', 0)} 次"
            f"｜回执 {context_event_summary.get('action_receipt_count', 0)} 次"
            f"｜最近：{_runtime_result_context_event_label(str(context_event_summary.get('latest_action') or ''))}"
            f"｜{context_event_summary.get('latest_result_type') or '无类型'}"
            f"｜items {context_event_summary.get('latest_item_count', 0)} 条"
            f"｜字段 {context_event_summary.get('latest_followup_field_count', 0)} 个"
            f"｜标识 {context_event_summary.get('latest_identity_field_count', 0)} 个"
            f"｜执行步骤 {context_event_summary.get('latest_source_execution_step_count', 0)} 个"
            f"｜{'结构化条目优先' if context_event_summary.get('latest_prefer_items') else '未声明结构化条目优先'}"
            + (f"｜动作:{context_event_summary.get('latest_action_status_group')}" if context_event_summary.get("latest_action_status_group") else "")
            + (f"｜ID {str(context_event_summary.get('latest_action_id') or '')[-8:]}" if context_event_summary.get("latest_action_id") else "")
            + (f"｜确认 {str(context_event_summary.get('latest_confirmation_token') or '')[-8:]}" if context_event_summary.get("latest_confirmation_token") else "")
            + (f"｜路由 {context_event_summary.get('latest_route_path')}" if context_event_summary.get("latest_route_path") else "")
            + "。"
        )
    context_events = snapshot.get("result_context_events") if isinstance(snapshot.get("result_context_events"), list) else []
    if context_events:
        lines.append("结果上下文事件：")
        for event in [item for item in context_events if isinstance(item, dict)][-3:]:
            action = _runtime_result_context_event_label(str(event.get("action") or ""))
            result_type = str(event.get("result_type") or "").strip()
            count = event.get("count")
            reason = _runtime_result_context_clear_reason_label(str(event.get("reason") or ""))
            source = str(event.get("source") or "").strip()
            token = str(event.get("confirmation_token") or "").strip()
            route_path = str(event.get("route_path") or "").strip()
            lines.append(
                f"- {action}"
                + (f"｜{result_type}" if result_type else "")
                + (f"｜{count} 条" if count not in (None, "") else "")
                + (f"｜原因：{reason}" if reason else "")
                + (f"｜来源：{_runtime_action_label(source)}" if source else "")
                + (f"｜确认 {token[-8:]}" if token else "")
                + (f"｜路由 {route_path}" if route_path else "")
            )
    pending_action = snapshot.get("pending_action") if isinstance(snapshot.get("pending_action"), dict) else {}
    if pending_action.get("available"):
        result_context = snapshot.get("result_context") if isinstance(snapshot.get("result_context"), dict) else {}
        pending_context_linked = (
            result_context.get("available")
            and str(result_context.get("context_kind") or "") == "pending_confirmation"
            and str(result_context.get("query_id") or "").endswith(str(pending_action.get("id") or ""))
        )
        pending_strategy = str(pending_action.get("strategy") or pending_action.get("intent") or "").strip()
        confirmation_reasons = pending_action.get("confirmation_reasons") if isinstance(pending_action.get("confirmation_reasons"), list) else []
        pending_sources = pending_action.get("sources") if isinstance(pending_action.get("sources"), list) else []
        pending_identity = str(pending_action.get("execution_identity") or "").strip()
        write_contract = (
            pending_action.get("write_confirmation_contract")
            if isinstance(pending_action.get("write_confirmation_contract"), dict)
            else {}
        )
        lines.append(
            "当前待确认动作："
            f"{_runtime_action_label(pending_strategy) if pending_strategy else '未知动作'}，"
            + (f"摘要：{pending_action.get('summary')}，" if pending_action.get("summary") else "")
            + f"确认编号：{pending_action.get('id') or '无'}"
            + (f"，身份：{_runtime_execution_identity_label(pending_identity)}" if pending_identity else "")
            + (f"，来源：{'、'.join(_runtime_source_label(str(source)) for source in pending_sources)}" if pending_sources else "")
            + f"，上下文：{'已关联' if pending_context_linked else '未关联'}"
            + (
                "，写入确认："
                + ("dry-run + 确认令牌" if write_contract.get("requires_confirmation_token") else "只读")
                if write_contract
                else ""
            )
            + (
                f"，有效期：{'已过期' if pending_action.get('expired') else str(pending_action.get('expires_in_seconds', 0)) + '秒'}"
                if pending_action.get("expires_at")
                else ""
            )
            + (f"，原因：{'、'.join(_runtime_confirmation_reason_label(str(item)) for item in confirmation_reasons)}" if confirmation_reasons else "")
            + "。"
        )
    pending_approval = snapshot.get("pending_approval") if isinstance(snapshot.get("pending_approval"), dict) else {}
    if pending_approval.get("available"):
        lines.append(
            "当前待确认审批："
            f"{pending_approval.get('type_label') or '审批确认'}，"
            f"动作：{_runtime_action_label(str(pending_approval.get('action') or '')) or pending_approval.get('action') or '未知'}，"
            f"数量：{pending_approval.get('count', 0)}，"
            + (f"审批单：{pending_approval.get('title')}，" if pending_approval.get("title") else "")
            + f"确认编号：{pending_approval.get('id') or '无'}"
            + (
                f"，有效期：{'已过期' if pending_approval.get('expired') else str(pending_approval.get('expires_in_seconds', 0)) + '秒'}"
                if pending_approval.get("expires_at")
                else ""
            )
            + "。"
        )
    pending_cleanup = snapshot.get("pending_cleanup") if isinstance(snapshot.get("pending_cleanup"), dict) else {}
    if int(pending_cleanup.get("cleaned_count") or 0) > 0:
        cleaned = pending_cleanup.get("cleaned") if isinstance(pending_cleanup.get("cleaned"), list) else []
        lines.append("待确认清理：已清理 " + "、".join(str(item) for item in cleaned) + "，并生成终态回执。")
    permission = snapshot.get("permission") if isinstance(snapshot.get("permission"), dict) else {}
    if permission.get("available"):
        confirmation_reasons = permission.get("confirmation_reasons") if isinstance(permission.get("confirmation_reasons"), list) else []
        lines.append(
            "权限决策："
            f"{'允许' if permission.get('allowed') else '拒绝'}，"
            f"执行身份：{_runtime_execution_identity_label(str(permission.get('execution_identity') or ''))}，"
            f"{'需要确认' if permission.get('requires_confirmation') else '无需确认'}"
            + (f"｜原因：{'、'.join(_runtime_confirmation_reason_label(str(item)) for item in confirmation_reasons)}" if confirmation_reasons else "")
            + (f"｜拒绝原因：{permission.get('reason')}" if permission.get("reason") else "")
        )
        source_capabilities = permission.get("source_capabilities") if isinstance(permission.get("source_capabilities"), list) else []
        if source_capabilities:
            lines.append("权限来源：")
            for item in [entry for entry in source_capabilities if isinstance(entry, dict)][:5]:
                lines.append(
                    f"- {_runtime_source_label(str(item.get('source') or ''))}"
                    f"｜{item.get('operation') or ''}"
                    f"｜{_runtime_execution_identity_label(str(item.get('execution_identity') or ''))}"
                    f"｜{'需确认' if item.get('requires_confirmation') else '只读/无需确认'}"
                )
    current_approval = snapshot.get("current_approval") if isinstance(snapshot.get("current_approval"), dict) else {}
    if current_approval.get("available"):
        lines.append(
            "当前审批锚点："
            f"{current_approval.get('title') or '未命名审批'}"
            + (f"｜{current_approval.get('applicant')}" if current_approval.get("applicant") else "")
            + (f"｜{current_approval.get('amount')} 元" if current_approval.get("amount") else "")
            + f"｜序号 {current_approval.get('index', 0) + 1}。"
        )
    usable = status_summary.get("usable_sources") if isinstance(status_summary.get("usable_sources"), list) else []
    readonly = status_summary.get("readonly_sources") if isinstance(status_summary.get("readonly_sources"), list) else []
    if usable:
        lines.append("可执行来源：" + "、".join(_runtime_source_label(str(source)) for source in usable[:12]))
    if readonly:
        lines.append("只读降级来源：" + "、".join(_runtime_source_label(str(source)) for source in readonly[:12]))
    pipeline_frames = snapshot.get("pipeline_frames") if isinstance(snapshot.get("pipeline_frames"), list) else []
    if pipeline_frames:
        lines.append("最近管线：")
        for frame in pipeline_frames[:8]:
            if isinstance(frame, dict):
                frame_name = str(frame.get("label") or _runtime_pipeline_label(str(frame.get("name") or "")))
                mode = "旁路观测" if frame.get("bypassed") else "实际执行"
                lines.append(f"- {frame_name}：{_runtime_stage_label(str(frame.get('status') or 'unknown'))}｜{mode}")
    action_trace = snapshot.get("action_trace") if isinstance(snapshot.get("action_trace"), list) else []
    if action_trace:
        lines.append("最近动作：")
        for item in [entry for entry in action_trace if isinstance(entry, dict)][-5:]:
            extra = "｜".join(
                str(value)
                for value in (
                    item.get("title"),
                    item.get("applicant"),
                    item.get("amount"),
                    f"{int(item.get('duration_ms') or 0)}ms" if int(item.get("duration_ms") or 0) > 0 else "",
                )
                if str(value or "").strip()
            )
            lines.append(
                f"- {_runtime_action_kind_label(str(item.get('kind') or ''))}｜{_runtime_action_label(str(item.get('action') or ''))}｜{_runtime_stage_label(str(item.get('status') or 'unknown'))}"
                + (f"｜{extra}" if extra else "")
            )
            action_context_parts = []
            strategy = str(item.get("strategy") or "").strip()
            sources = item.get("sources") if isinstance(item.get("sources"), list) else []
            execution_identity = str(item.get("execution_identity") or "").strip()
            confirmation_token = str(item.get("confirmation_token") or "").strip()
            if strategy:
                action_context_parts.append(f"策略：{_runtime_action_label(strategy)}")
            if sources:
                action_context_parts.append("来源：" + "、".join(_runtime_source_label(str(source)) for source in sources))
            if execution_identity:
                action_context_parts.append(f"身份：{_runtime_execution_identity_label(execution_identity)}")
            if confirmation_token:
                action_context_parts.append(f"确认令牌：{confirmation_token[-8:]}")
            if item.get("requires_confirmation"):
                reasons = item.get("confirmation_reasons") if isinstance(item.get("confirmation_reasons"), list) else []
                reason_text = "、".join(_runtime_confirmation_reason_label(str(reason)) for reason in reasons) if reasons else "需要确认"
                action_context_parts.append(f"确认：{reason_text}")
            if action_context_parts:
                lines.append("  " + "｜".join(action_context_parts))
            error = str(item.get("error") or "").strip()
            if error:
                lines.append(f"  错误：{_compact_runtime_text(error, 120)}")
            recommended_next_step = str(item.get("recommended_next_step") or "").strip()
            if recommended_next_step:
                lines.append(f"  建议：{recommended_next_step}")
    decision_trace = snapshot.get("decision_trace") if isinstance(snapshot.get("decision_trace"), list) else []
    if decision_trace:
        lines.append("最近决策链路：")
        for item in [entry for entry in decision_trace if isinstance(entry, dict)][-5:]:
            lines.append(
                f"- {_runtime_question_type_label(str(item.get('question_type') or ''))}"
                f"｜{item.get('strategy') or item.get('intent') or '未知策略'}"
                f"｜{_runtime_stage_label(str(item.get('execution_status') or 'unknown'))}"
                f"｜{_runtime_execution_identity_label(str(item.get('execution_identity') or ''))}"
                f"｜{'需确认' if item.get('requires_confirmation') else '无需确认'}"
                + (f"｜{item.get('result_type')}" if item.get("result_type") else "")
                + (f"｜{int(item.get('pipeline_total_ms') or 0)}ms" if int(item.get("pipeline_total_ms") or 0) > 0 else "")
            )
            slowest_stage = str(item.get("slowest_stage") or "").strip()
            slowest_ms = int(item.get("slowest_ms") or 0)
            if slowest_stage and slowest_ms > 0:
                lines.append(f"  最慢阶段：{_runtime_stage_name_label(slowest_stage)} {slowest_ms}ms")
            if item.get("empty_result"):
                reason = _runtime_empty_reason_label(str(item.get("empty_reason") or ""))
                next_step = str(item.get("recommended_next_step") or "").strip()
                lines.append(f"  空结果原因：{reason}" + (f"｜建议：{next_step}" if next_step else ""))
            path_label = str(item.get("path_maturity_label") or item.get("path_maturity_status") or "").strip()
            if path_label:
                lines.append(
                    f"  路径：{path_label}"
                    f"｜迁移 {item.get('path_migration_count', 0)} 项"
                    f"｜来源缺失 {item.get('source_contract_missing_count', 0)} 项"
                )
            capability_label = str(item.get("capability_readiness_label") or item.get("capability_readiness_status") or "").strip()
            if capability_label:
                lines.append(
                    f"  能力：{capability_label}"
                    f"｜就绪 {item.get('capability_ready_count', 0)}/{item.get('capability_declared_count', 0)}"
                    f"｜问题 {item.get('capability_issue_count', 0)} 个"
                )
            integrity_label = str(item.get("snapshot_integrity_label") or item.get("snapshot_integrity_status") or "").strip()
            if integrity_label:
                lines.append(
                    f"  诊断：{integrity_label}"
                    f"｜缺失 {item.get('snapshot_missing_count', 0)}"
                    f"｜空模块 {item.get('snapshot_empty_count', 0)}"
                )
            event_count = int(item.get("result_context_event_count") or 0)
            if event_count:
                latest_action = _runtime_result_context_event_label(str(item.get("result_context_latest_action") or ""))
                lines.append(
                    f"  上下文事件：{event_count} 个"
                    f"｜最近 {latest_action or '未知'}"
                    f"｜{'结构化条目优先' if item.get('result_context_latest_items_first') else '未声明结构化条目优先'}"
                    + (f"｜动作 {item.get('result_context_latest_action_status_group')}" if item.get("result_context_latest_action_status_group") else "")
                    + (f"｜ID {str(item.get('result_context_latest_action_id') or '')[-8:]}" if item.get("result_context_latest_action_id") else "")
                )
            action_status = str(item.get("action_latest_status") or "").strip()
            if action_status:
                lines.append(
                    f"  动作：{_runtime_stage_label(action_status)}"
                    f"｜回执：{'已关联' if item.get('action_has_correlated_receipt') else '已有' if item.get('action_has_receipt') else '无'}"
                )
            provider_total = int(item.get("provider_success_count") or 0) + int(item.get("provider_error_count") or 0) + int(item.get("provider_denied_count") or 0)
            if provider_total:
                lines.append(
                    f"  Provider：成功 {item.get('provider_success_count', 0)}，"
                    f"失败 {item.get('provider_error_count', 0)}，"
                    f"无权限 {item.get('provider_denied_count', 0)}，"
                    f"耗时 {item.get('provider_total_duration_ms', 0)}ms"
                )
            locator = str(item.get("debug_locator") or "").strip()
            primary_issue = str(item.get("primary_issue_fingerprint") or "").strip()
            if locator or primary_issue:
                lines.append("  定位：" + (locator or f"主问题 #{primary_issue}"))
    provider_results = snapshot.get("provider_results") if isinstance(snapshot.get("provider_results"), list) else []
    raw_provider_results = [item for item in provider_results if isinstance(item, dict) and item.get("raw_answer_detected")]
    if raw_provider_results:
        lines.append("Provider 输出质量：发现原始技术载荷风险。")
        for item in raw_provider_results[:3]:
            source = _runtime_source_label(str(item.get("source") or ""))
            operation = str(item.get("operation") or "").strip()
            lines.append(
                f"- {source}"
                + (f"｜{operation}" if operation else "")
                + f"｜answer {item.get('answer_chars', 0)} 字｜建议：Provider 只返回用户可读摘要，原始结构放入 items/metadata。"
            )
    provider_errors = [item for item in provider_results if isinstance(item, dict) and str(item.get("status") or "") in {"error", "denied"}]
    if provider_errors:
        lines.append("最近 Provider 错误：")
        for item in provider_errors[:3]:
            source = _runtime_source_label(str(item.get("source") or ""))
            error_type = str(item.get("error_type") or item.get("error") or "未知错误")
            operation = str(item.get("operation") or "").strip()
            lines.append(f"- {source}" + (f"｜{operation}" if operation else "") + f"｜{_runtime_error_type_label(error_type)}")
    slow_provider_results = sorted(
        [item for item in provider_results if isinstance(item, dict) and int(item.get("duration_ms") or 0) > 0],
        key=lambda item: int(item.get("duration_ms") or 0),
        reverse=True,
    )
    if slow_provider_results:
        lines.append("Provider 耗时：")
        for item in slow_provider_results[:3]:
            source = _runtime_source_label(str(item.get("source") or ""))
            source_key = str(item.get("source") or "")
            operation = str(item.get("operation") or "").strip()
            status = _runtime_stage_label(str(item.get("status") or "unknown"))
            duration_ms = int(item.get("duration_ms") or 0)
            speed_label = _runtime_provider_speed_label(duration_ms)
            suggestion = _runtime_provider_speed_suggestion(source_key, operation, duration_ms)
            lines.append(
                f"- {source}"
                + (f"｜{operation}" if operation else "")
                + f"｜{status}｜{duration_ms}ms"
                + (f"｜{speed_label}" if speed_label else "")
                + (f"｜建议：{suggestion}" if suggestion else "")
            )
            substep_lines = _runtime_provider_substep_lines(item)
            lines.extend(f"  {line}" for line in substep_lines)
    return "\n".join(lines)


def _runtime_probe_suggestions(snapshot: dict[str, Any]) -> list[str]:
    provider_registry = snapshot.get("provider_registry") if isinstance(snapshot.get("provider_registry"), dict) else {}
    registered = set(provider_registry.get("registered_sources") or [])
    probes: list[str] = []
    if {"people", "base", "im"}.issubset(registered):
        probes.append("帮我创建一个表，把组织架构放进去，并把文件发给我")
    if "approval" in registered:
        probes.append("待我审批有哪些")
    if "im" in registered:
        probes.append("发条消息给王云飞：大飞哥的测试进度 70%")
    if "task" in registered:
        probes.append("帮我创建一个待办：明天提醒我跟进客户合同")
    if "mail" in registered:
        probes.append("帮我查一下最近邮件")
    return probes[:5]


def _runtime_next_step(drift: dict[str, Any], planner_drift: dict[str, Any], pending_ops: int | str) -> str:
    declared_missing = int(drift.get("declared_missing_provider_count") or 0)
    provider_extra = int(drift.get("provider_missing_declaration_count") or 0)
    provider_extra_items = drift.get("provider_missing_declaration") if isinstance(drift.get("provider_missing_declaration"), list) else []
    provider_extra_write_count = len([item for item in provider_extra_items if isinstance(item, dict) and item.get("is_write")])
    planner_missing = int(planner_drift.get("planner_missing_capability_count") or 0)
    capability_unused = int(planner_drift.get("capability_missing_planner_count") or 0)
    pending_operation_count = int(pending_ops or 0)
    if declared_missing:
        return "先补已声明但 Provider 未实现的能力，避免规划后无法执行。"
    if provider_extra_write_count:
        return "先把 Provider 已有但 Capability 未声明的写操作补进能力清单，确保权限和二次确认不被绕过。"
    if pending_operation_count:
        return "先接入待补原子操作，优先动作类和高频查询类。"
    if planner_missing:
        return "先补 Planner 到 Capability 的声明，避免策略绕过能力清单。"
    if provider_extra:
        return "把 Provider 已支持但未声明的操作补进 Capability 清单。"
    if capability_unused:
        return "清理或接入 Capability 已声明但 Planner 暂未使用的能力。"
    return "主链路映射暂时一致，可以继续扩业务场景或做端到端验证。"


def _runtime_result_context_followup_fields(result_context: dict[str, Any]) -> list[str]:
    context_kind = str(result_context.get("context_kind") or "")
    result_type = str(result_context.get("result_type") or "")
    fields: list[str] = []
    if result_context.get("empty_result"):
        fields.extend(["空结果原因", "下一步建议"])
    elif context_kind == "action_receipt":
        fields.extend(["状态", "对象", "链接", "编号", "错误原因", "权限原因", "摘要"])
    elif result_type in {"approval_list", "approval_detail"}:
        fields.extend(["序号", "详情", "申请人", "金额", "建议", "通过/拒绝"])
    elif result_type in {"people_search", "department_members", "organization_snapshot"}:
        fields.extend(["人员", "部门", "电话", "邮箱", "负责人"])
    elif result_type in {"mail_list", "im_message_list", "chat_list"}:
        fields.extend(["序号", "主题", "发件人", "时间", "详情"])
    elif result_type in {"task_list", "calendar_event_list"}:
        fields.extend(["序号", "标题", "时间", "负责人", "详情"])
    else:
        fields.extend(["序号", "展开", "详情"])
    return fields


def _runtime_pipeline_timing_advice(pipeline_timing: dict[str, Any]) -> str:
    try:
        slowest_ms = int(pipeline_timing.get("slowest_ms") or 0)
        total_ms = int(pipeline_timing.get("total_ms") or 0)
    except (TypeError, ValueError):
        return ""
    if slowest_ms < 2000 and total_ms < 4000:
        return ""
    slowest_stage = str(pipeline_timing.get("slowest_stage") or "").strip()
    if slowest_stage in {"intent_recognition", "task_planner", "answer_composer"}:
        return "慢点在语义理解或答案组织，优先看 LLM 调用、提示词长度和是否发生重复生成。"
    if slowest_stage in {"capability_router", "execution"}:
        return "慢点在能力执行，优先看 Provider 耗时、飞书 API 响应、附件/OCR 或批量写入。"
    if slowest_stage == "permission_check":
        return "慢点在权限检查，优先看身份加载、公司范围和权限来源聚合。"
    if slowest_stage == "result_followup_detector":
        return "慢点在追问识别，优先看 Result Context 体积和序号/指代解析。"
    if slowest_stage == "pre_gateway":
        return "慢点在入口上下文加载，优先看 Redis、Profile 和会话上下文读取。"
    return "本轮耗时偏高，优先结合 Provider 耗时和最近决策链路定位。"


def _runtime_action_trace(chat_id: str | None) -> list[dict[str, Any]]:
    return load_action_trace(chat_id)


def _runtime_decision_trace(chat_id: str | None) -> list[dict[str, Any]]:
    return load_runtime_decision_trace(chat_id)


def _record_runtime_decision_trace(
    *,
    chat_id: str | None,
    route_path: str,
    route_label: str,
    execution_status: str,
    runtime_summary: dict[str, Any],
    diagnostics_snapshot: dict[str, Any],
) -> None:
    if not chat_id:
        return
    result_context = diagnostics_snapshot.get("result_context") if isinstance(diagnostics_snapshot.get("result_context"), dict) else {}
    permission = diagnostics_snapshot.get("permission") if isinstance(diagnostics_snapshot.get("permission"), dict) else {}
    pipeline_timing = diagnostics_snapshot.get("pipeline_timing") if isinstance(diagnostics_snapshot.get("pipeline_timing"), dict) else {}
    provider_summary = diagnostics_snapshot.get("provider_summary") if isinstance(diagnostics_snapshot.get("provider_summary"), dict) else {}
    debug_locator = diagnostics_snapshot.get("debug_locator") if isinstance(diagnostics_snapshot.get("debug_locator"), dict) else {}
    snapshot_summary = diagnostics_snapshot.get("snapshot_summary") if isinstance(diagnostics_snapshot.get("snapshot_summary"), dict) else {}
    current_path_maturity = diagnostics_snapshot.get("current_path_maturity") if isinstance(diagnostics_snapshot.get("current_path_maturity"), dict) else {}
    current_capability_readiness = diagnostics_snapshot.get("current_capability_readiness") if isinstance(diagnostics_snapshot.get("current_capability_readiness"), dict) else {}
    result_context_event_summary = diagnostics_snapshot.get("result_context_event_summary") if isinstance(diagnostics_snapshot.get("result_context_event_summary"), dict) else {}
    action_closure = diagnostics_snapshot.get("action_closure") if isinstance(diagnostics_snapshot.get("action_closure"), dict) else {}
    source_execution_contract = diagnostics_snapshot.get("source_execution_contract") if isinstance(diagnostics_snapshot.get("source_execution_contract"), dict) else {}
    snapshot_integrity = diagnostics_snapshot.get("snapshot_integrity") if isinstance(diagnostics_snapshot.get("snapshot_integrity"), dict) else {}
    record_runtime_decision_trace(
        chat_id,
        {
            "trace_id": debug_locator.get("trace_id") or diagnostics_snapshot.get("trace_id") or "",
            "debug_locator": debug_locator.get("locator", ""),
            "primary_issue_fingerprint": debug_locator.get("primary_issue_fingerprint") or snapshot_summary.get("primary_issue_fingerprint") or "",
            "primary_issue_key": debug_locator.get("primary_issue_key") or snapshot_summary.get("primary_issue_key") or "",
            "intent": runtime_summary.get("intent", ""),
            "question_type": runtime_summary.get("question_type", ""),
            "data_scope": runtime_summary.get("data_scope", ""),
            "strategy": runtime_summary.get("strategy", ""),
            "sources": runtime_summary.get("sources", []),
            "route_path": route_path,
            "route_label": route_label,
            "execution_status": execution_status,
            "path_maturity_status": current_path_maturity.get("status", ""),
            "path_maturity_label": current_path_maturity.get("label", ""),
            "path_migration_count": current_path_maturity.get("migration_count", 0),
            "capability_readiness_status": current_capability_readiness.get("status", ""),
            "capability_readiness_label": current_capability_readiness.get("label", ""),
            "capability_ready_count": current_capability_readiness.get("ready_count", 0),
            "capability_declared_count": current_capability_readiness.get("declared_count", 0),
            "capability_issue_count": current_capability_readiness.get("issue_count", 0),
            "snapshot_integrity_status": snapshot_integrity.get("status", ""),
            "snapshot_integrity_label": snapshot_integrity.get("label", ""),
            "snapshot_missing_count": snapshot_integrity.get("missing_count", 0),
            "snapshot_empty_count": snapshot_integrity.get("empty_count", 0),
            "source_contract_status": source_execution_contract.get("status", ""),
            "source_contract_missing_count": source_execution_contract.get("missing_count", 0),
            "requires_confirmation": bool(permission.get("requires_confirmation", False)),
            "execution_identity": permission.get("execution_identity", ""),
            "result_type": result_context.get("result_type", ""),
            "context_kind": result_context.get("context_kind", ""),
            "item_count": result_context.get("item_count", result_context.get("count", 0)),
            "result_context_event_count": result_context_event_summary.get("event_count", 0),
            "result_context_latest_action": result_context_event_summary.get("latest_action", ""),
            "result_context_latest_items_first": bool(result_context_event_summary.get("latest_prefer_items", False)),
            "result_context_latest_action_status_group": result_context_event_summary.get("latest_action_status_group", ""),
            "result_context_latest_action_id": result_context_event_summary.get("latest_action_id", ""),
            "action_latest_status": action_closure.get("latest_status", ""),
            "action_latest_status_group": action_closure.get("latest_status_group", ""),
            "action_has_receipt": bool(action_closure.get("has_action_receipt_event", False)),
            "action_has_correlated_receipt": bool(action_closure.get("has_correlated_receipt", False)),
            "empty_result": bool(result_context.get("empty_result", False)),
            "empty_reason": result_context.get("empty_reason", ""),
            "recommended_next_step": result_context.get("recommended_next_step", ""),
            "pipeline_total_ms": pipeline_timing.get("total_ms", 0),
            "slowest_stage": pipeline_timing.get("slowest_stage", ""),
            "slowest_ms": pipeline_timing.get("slowest_ms", 0),
            "provider_success_count": provider_summary.get("success_count", 0),
            "provider_error_count": provider_summary.get("error_count", 0),
            "provider_denied_count": provider_summary.get("denied_count", 0),
            "provider_total_duration_ms": provider_summary.get("total_duration_ms", 0),
            "slowest_provider_source": debug_locator.get("slowest_provider_source", ""),
            "slowest_provider_ms": debug_locator.get("slowest_provider_ms", 0),
        },
    )


def _runtime_pending_action_summary(chat_id: str | None) -> dict[str, Any]:
    if not chat_id:
        return {"available": False}
    pending = load_session_context(chat_id).get("runtime_v5_pending_action")
    if not isinstance(pending, dict):
        return {"available": False}
    expires_at = str(pending.get("expires_at") or "")
    expires_in_seconds = _iso_seconds_until(expires_at)
    return {
        "available": True,
        "id": pending.get("id") or "",
        "created_at": pending.get("created_at") or "",
        "expires_at": expires_at,
        "ttl_seconds": int(pending.get("ttl_seconds") or 0),
        "expires_in_seconds": expires_in_seconds,
        "expired": bool(expires_at and expires_in_seconds <= 0),
        "intent": pending.get("intent") or "",
        "strategy": pending.get("strategy") or "",
        "summary": pending.get("summary") or "",
        "sources": pending.get("sources") if isinstance(pending.get("sources"), list) else [],
        "source_execution_plan": pending.get("source_execution_plan") if isinstance(pending.get("source_execution_plan"), list) else [],
        "execution_identity": pending.get("execution_identity") or "",
        "permission_reason": pending.get("permission_reason") or "",
        "requires_confirmation": bool(pending.get("requires_confirmation", False)),
        "write_confirmation_contract": (
            pending.get("write_confirmation_contract")
            if isinstance(pending.get("write_confirmation_contract"), dict)
            else {}
        ),
        "route_path": pending.get("route_path") or "",
        "confirmation_reasons": pending.get("confirmation_reasons") if isinstance(pending.get("confirmation_reasons"), list) else [],
    }


def _runtime_pending_approval_summary(chat_id: str | None) -> dict[str, Any]:
    if not chat_id:
        return {"available": False}
    session_context = load_session_context(chat_id)
    single = session_context.get("runtime_v5_pending_approval_single")
    batch = session_context.get("runtime_v5_pending_approval_batch")
    pending_type = ""
    pending: dict[str, Any] = {}
    if isinstance(single, dict):
        pending_type = "single"
        pending = single
    elif isinstance(batch, dict):
        pending_type = "batch"
        pending = batch
    if not pending:
        return {"available": False}
    expires_at = str(pending.get("expires_at") or "")
    expires_in_seconds = _iso_seconds_until(expires_at)
    item = pending.get("item") if isinstance(pending.get("item"), dict) else {}
    items = pending.get("items") if isinstance(pending.get("items"), list) else []
    title = str(item.get("title") or item.get("approval_name") or item.get("definition_name") or "").strip()
    return {
        "available": True,
        "type": pending_type,
        "type_label": "单笔审批确认" if pending_type == "single" else "批量审批确认",
        "id": pending.get("id") or "",
        "action": pending.get("action") or "",
        "title": title,
        "count": len(items) if pending_type == "batch" else 1,
        "created_at": pending.get("created_at") or "",
        "expires_at": expires_at,
        "ttl_seconds": int(pending.get("ttl_seconds") or 0),
        "expires_in_seconds": expires_in_seconds,
        "expired": bool(expires_at and expires_in_seconds <= 0),
    }


def _cleanup_expired_pending_confirmations(chat_id: str | None) -> dict[str, Any]:
    if not chat_id:
        return {"available": False, "cleaned_count": 0, "cleaned": []}
    session_context = load_session_context(chat_id)
    cleaned: list[str] = []
    receipt_items: list[dict[str, Any]] = []
    keys = (
        ("runtime_v5_pending_action", "通用待确认动作", "runtime_confirmation"),
        ("runtime_v5_pending_approval_single", "单笔审批确认", "approval_single"),
        ("runtime_v5_pending_approval_batch", "批量审批确认", "approval_batch"),
    )
    for key, label, kind in keys:
        pending = session_context.get(key)
        if isinstance(pending, dict) and _pending_summary_expired(pending):
            session_context.pop(key, None)
            cleaned.append(label)
            action = str(pending.get("action") or pending.get("strategy") or pending.get("intent") or "cleanup")
            action_id = str(pending.get("id") or "")
            record_action_trace(
                chat_id,
                {
                    "kind": kind,
                    "action": action,
                    "status": "stale_cleanup",
                    "action_id": action_id,
                    "title": label,
                    "expires_at": str(pending.get("expires_at") or ""),
                },
            )
            receipt_items.append(
                {
                    "source": "runtime",
                    "operation": action,
                    "status": "stale_cleanup",
                    "status_group": "terminal",
                    "is_terminal": True,
                    "is_pending": False,
                    "action_id": action_id,
                    "title": label,
                    "summary": f"{label}已过期并自动清理。",
                    "expires_at": str(pending.get("expires_at") or ""),
                }
            )
    if cleaned:
        save_session_context(chat_id, session_context)
        _save_pending_cleanup_receipt(chat_id=chat_id, items=receipt_items)
    return {
        "available": True,
        "cleaned_count": len(cleaned),
        "cleaned": cleaned,
    }


def _save_pending_cleanup_receipt(*, chat_id: str | None, items: list[dict[str, Any]]) -> None:
    if not chat_id or not items:
        return
    save_result_context(
        chat_id,
        ResultContext(
            result_type="runtime_action",
            query_id=f"pending_cleanup:runtime_action:{datetime.now(timezone.utc).timestamp():.0f}",
            count=len(items),
            items=tuple(items),
            metadata={
                "context_kind": "action_receipt",
                "question_type": "action",
                "data_scope": "self",
                "actionable": False,
                "execution_status": "stale_cleanup",
                "action_status_group": "terminal",
                "is_terminal_action": True,
                "is_pending_action": False,
                "source": "runtime",
                "operation": "pending_cleanup",
                "result_sources": ["runtime"],
                "item_count": len(items),
                "display_count": len(items),
                "followup_fields": ["title", "status", "action_id", "expires_at"],
                "item_identity_fields": ["action_id"],
                "consume_policy": {"prefer_items": True, "allow_answer_fallback": False},
            },
            answer=f"已自动清理 {len(items)} 条过期待确认操作。",
        ),
    )


def _pending_summary_expired(pending: dict[str, Any]) -> bool:
    expires_at = str(pending.get("expires_at") or "").strip()
    return bool(expires_at and _iso_seconds_until(expires_at) <= 0)


def _iso_seconds_until(value: str) -> int:
    if not value:
        return 0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int((parsed - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return 0


def _runtime_current_approval_summary(chat_id: str | None) -> dict[str, Any]:
    if not chat_id:
        return {"available": False}
    current = load_session_context(chat_id).get("runtime_v5_current_approval_item")
    if not isinstance(current, dict):
        return {"available": False}
    item = current.get("item") if isinstance(current.get("item"), dict) else {}
    if not item:
        return {"available": False}
    try:
        index = int(current.get("index") or 0)
    except (TypeError, ValueError):
        index = 0
    return {
        "available": True,
        "index": index,
        "title": item.get("title") or item.get("approval_name") or item.get("definition_name") or "",
        "applicant": item.get("applicant") or "",
        "amount": item.get("amount") or "",
    }


def _current_capability_readiness(*, strategy: str, sources: tuple[str, ...], provider_registry: dict[str, Any]) -> dict[str, Any]:
    capabilities = capabilities_for_strategy(strategy, sources)
    providers = provider_registry.get("providers") if isinstance(provider_registry.get("providers"), dict) else {}
    items: list[dict[str, Any]] = []
    issue_count = 0
    blocking_count = 0
    for capability in capabilities:
        provider = providers.get(capability.source) if isinstance(providers.get(capability.source), dict) else {}
        contracts = provider.get("operation_contracts") if isinstance(provider.get("operation_contracts"), dict) else {}
        contract = contracts.get(capability.operation) if isinstance(contracts.get(capability.operation), dict) else {}
        provider_available = bool(contract)
        pending = bool(contract.get("pending")) if contract else True
        local_readonly = bool(contract.get("local_readonly")) if contract else False
        confirmation_ok = (not capability.requires_confirmation) or bool(contract.get("requires_confirmation")) if contract else not capability.requires_confirmation
        identity_ok = str(contract.get("execution_identity") or capability.execution_identity) == capability.execution_identity if contract else True
        write_confirmation_contract_ok = bool(contract.get("write_confirmation_contract_ok")) if contract else not capability.requires_confirmation
        ready = bool(
            capability.installed
            and provider_available
            and not pending
            and confirmation_ok
            and identity_ok
            and write_confirmation_contract_ok
        )
        issues: list[str] = []
        if not provider_available:
            issues.append("provider_missing")
        if pending:
            issues.append("provider_pending")
        if local_readonly and capability.question_type == "action":
            issues.append("local_readonly_action")
        if not confirmation_ok:
            issues.append("confirmation_gap")
        if not identity_ok:
            issues.append("identity_gap")
        if not write_confirmation_contract_ok:
            issues.append("write_confirmation_contract_gap")
        if issues:
            issue_count += len(issues)
        if "provider_missing" in issues or "confirmation_gap" in issues or "write_confirmation_contract_gap" in issues:
            blocking_count += 1
        items.append(
            {
                "strategy": capability.strategy,
                "source": capability.source,
                "operation": capability.operation,
                "label": capability.label,
                "question_type": capability.question_type,
                "execution_identity": capability.execution_identity,
                "requires_confirmation": capability.requires_confirmation,
                "provider_available": provider_available,
                "pending": pending,
                "local_readonly": local_readonly,
                "confirmation_ok": confirmation_ok,
                "identity_ok": identity_ok,
                "write_confirmation_contract": contract.get("write_confirmation_contract") or ("dry_run_then_confirmation_token" if capability.requires_confirmation else "readonly"),
                "requires_dry_run": bool(contract.get("requires_dry_run")) if contract else capability.requires_confirmation,
                "requires_confirmation_token": bool(contract.get("requires_confirmation_token")) if contract else capability.requires_confirmation,
                "write_confirmation_contract_ok": write_confirmation_contract_ok,
                "ready": ready,
                "issues": issues,
            }
        )
    ready_count = len([item for item in items if item.get("ready")])
    status = "healthy" if items and ready_count == len(items) else "blocked" if blocking_count else "needs_attention"
    if not items:
        status = "needs_attention"
    return {
        "available": bool(items),
        "status": status,
        "label": "当前策略能力可执行" if status == "healthy" else "当前策略能力仍有缺口",
        "strategy": strategy,
        "sources": list(sources),
        "declared_count": len(items),
        "ready_count": ready_count,
        "issue_count": issue_count,
        "blocking_count": blocking_count,
        "items": items,
    }


def _apply_current_capability_readiness_to_snapshot(snapshot: dict[str, Any]) -> None:
    readiness = snapshot.get("current_capability_readiness")
    if not isinstance(readiness, dict) or readiness.get("status") == "healthy":
        return
    issue_count = int(readiness.get("issue_count") or 0) or 1
    label = str(readiness.get("label") or "当前策略能力仍有缺口")
    detail = f"就绪 {readiness.get('ready_count', 0)}/{readiness.get('declared_count', 0)}，问题 {issue_count} 个"
    recommendation = "优先补齐当前策略缺失的 Provider operation、确认声明、执行身份或 pending 原子能力。"

    health_matrix = snapshot.get("health_matrix") if isinstance(snapshot.get("health_matrix"), dict) else {}
    layers = health_matrix.get("layers") if isinstance(health_matrix.get("layers"), list) else []
    if health_matrix:
        layers.append(
            {
                "key": "capability",
                "label": "Capability Readiness",
                "status": "blocked" if readiness.get("status") == "blocked" else "needs_attention",
                "status_label": "阻断" if readiness.get("status") == "blocked" else "需关注",
                "issue_count": issue_count,
            }
        )
        health_matrix["layers"] = layers
        health_matrix["issue_count"] = int(health_matrix.get("issue_count") or 0) + issue_count
        unhealthy = [item for item in layers if isinstance(item, dict) and item.get("status") not in {"healthy", "none"}]
        health_matrix["status"] = "healthy" if not unhealthy else "needs_attention"
        health_matrix["label"] = "各层健康" if not unhealthy else f"{len(unhealthy)} 层需要关注"
        health_matrix["status_counts"] = _manual_status_counts(layers)

    gate = snapshot.get("runtime_gate") if isinstance(snapshot.get("runtime_gate"), dict) else {}
    if gate:
        item = _manual_issue_fingerprint(
            {
                "key": "capability:current_readiness",
                "severity": "high" if readiness.get("status") == "blocked" else "medium",
                "label": label,
                "detail": detail,
                "recommendation": recommendation,
            }
        )
        priority_items = gate.get("priority_items") if isinstance(gate.get("priority_items"), list) else []
        priority_items.insert(0, item)
        gate["priority_items"] = priority_items[:8]
        gate["status"] = "blocked" if readiness.get("status") == "blocked" else "needs_attention"
        gate["label"] = "存在阻断风险" if readiness.get("status") == "blocked" else "需要继续加固"
        gate["issue_count"] = int(gate.get("issue_count") or 0) + 1
        gate["primary_issue_fingerprint"] = item.get("fingerprint", "")
        gate["primary_issue_key"] = item.get("key", "")
        gate["severity_counts"] = _manual_severity_counts(priority_items)
        gate["next_step"] = recommendation

    repair_plan = snapshot.get("repair_plan") if isinstance(snapshot.get("repair_plan"), dict) else {}
    if repair_plan:
        repair_item = _manual_issue_fingerprint(
            {
                "source": "current_capability_readiness",
                "severity": "high" if readiness.get("status") == "blocked" else "medium",
                "label": label,
                "detail": detail,
                "next_step": recommendation,
            }
        )
        repair_items = repair_plan.get("items") if isinstance(repair_plan.get("items"), list) else []
        repair_items.insert(0, repair_item)
        repair_plan["items"] = repair_items[:8]
        repair_plan["status"] = "needs_attention"
        repair_plan["item_count"] = len(repair_plan["items"])
        repair_plan["primary_issue_fingerprint"] = repair_item.get("fingerprint", "")
        repair_plan["primary_issue_source"] = "current_capability_readiness"
        repair_plan["severity_counts"] = _manual_severity_counts(repair_plan["items"])
        repair_plan["next_step"] = recommendation


def _apply_source_execution_contract_to_snapshot(snapshot: dict[str, Any]) -> None:
    contract = snapshot.get("source_execution_contract")
    if not isinstance(contract, dict) or int(contract.get("missing_count") or 0) <= 0:
        return
    missing_sources = contract.get("missing_sources") if isinstance(contract.get("missing_sources"), list) else []
    _append_snapshot_gate_issues(
        snapshot,
        [
            {
                "key": "source_execution:missing_sources",
                "severity": "high",
                "label": "Planner 来源没有实际执行结果",
                "detail": "、".join(str(source) for source in missing_sources[:6]),
                "recommendation": "检查 Capability Router 是否按 Planner sources 顺序执行 Provider，并确保每个来源记录 ProviderResult。",
            }
        ],
    )


def _apply_result_context_event_summary_to_snapshot(snapshot: dict[str, Any]) -> None:
    summary = snapshot.get("result_context_event_summary")
    if not isinstance(summary, dict) or not summary.get("available"):
        return
    issues: list[dict[str, Any]] = []
    latest_action = str(summary.get("latest_action") or "")
    latest_context_kind = str(summary.get("latest_context_kind") or "")
    if latest_action == "save" and not bool(summary.get("latest_prefer_items")):
        issues.append(
            {
                "key": "result_context_event:not_items_first",
                "severity": "medium",
                "label": "最近保存的结果上下文未声明结构化条目优先",
                "detail": str(summary.get("latest_result_type") or ""),
                "recommendation": "保存结果上下文时必须声明结构化条目优先，避免追问退回展示文本。",
            }
        )
    if latest_context_kind == "action_receipt" and not str(summary.get("latest_action_status_group") or ""):
        issues.append(
            {
                "key": "result_context_event:missing_action_status_group",
                "severity": "medium",
                "label": "动作回执事件缺少 action 状态分组",
                "detail": str(summary.get("latest_result_type") or ""),
                "recommendation": "动作回执 Result Context 事件必须带 action_status_group，便于识别 pending/terminal。",
            }
        )
    if not issues:
        return
    _append_snapshot_gate_issues(snapshot, issues)


def _apply_result_context_quality_to_snapshot(snapshot: dict[str, Any]) -> None:
    quality = snapshot.get("result_context_quality")
    if not isinstance(quality, dict) or not quality.get("available"):
        return
    issue_count = int(quality.get("issue_count") or 0)
    if issue_count <= 0:
        return
    issues = quality.get("issues") if isinstance(quality.get("issues"), list) else []
    first_issue = issues[0] if issues and isinstance(issues[0], dict) else {}
    _append_snapshot_gate_issues(
        snapshot,
        [
            {
                "key": f"result_context_quality:{first_issue.get('kind') or 'issue'}",
                "severity": str(first_issue.get("severity") or "medium"),
                "label": "Result Context 质量需要修复",
                "detail": f"{quality.get('result_type') or '未知类型'}｜{issue_count} 项问题",
                "recommendation": str(first_issue.get("recommendation") or "补齐 Result Context 的 query_id、context_kind、items/count、sources、consume_policy 和 followup_fields。"),
            }
        ],
    )


def _apply_provider_registry_contract_to_snapshot(snapshot: dict[str, Any]) -> None:
    provider_registry = snapshot.get("provider_registry") if isinstance(snapshot.get("provider_registry"), dict) else {}
    status_summary = provider_registry.get("status_summary") if isinstance(provider_registry.get("status_summary"), dict) else {}
    planner_missing = int(status_summary.get("planner_drift_count") or 0)
    contract_blocking = int(status_summary.get("contract_blocking_count") or 0)
    drift_count = int(status_summary.get("drift_count") or 0)
    write_contract = provider_registry.get("write_confirmation_contract")
    if not isinstance(write_contract, dict):
        write_contract = status_summary.get("write_confirmation_contract") if isinstance(status_summary.get("write_confirmation_contract"), dict) else {}
    write_contract_gap = int(write_contract.get("gap_count") or 0)
    issues: list[dict[str, Any]] = []
    if planner_missing > 0:
        detail_items = status_summary.get("planner_missing_capability") if isinstance(status_summary.get("planner_missing_capability"), list) else []
        detail = "、".join(f"{item.get('strategy')}.{item.get('source')}" for item in detail_items[:4] if isinstance(item, dict))
        issues.append(
            {
                "key": "provider_contract:planner_missing_capability",
                "severity": "high",
                "label": "Planner 策略缺少 Capability 声明",
                "detail": detail or f"{planner_missing} 项",
                "recommendation": "Planner 每个 strategy/source 必须在 RUNTIME_CAPABILITIES 中声明，避免规划到不可治理路径。",
            }
        )
    if contract_blocking > 0:
        issues.append(
            {
                "key": "provider_contract:blocking_contract",
                "severity": "high",
                "label": "Provider 契约存在阻断问题",
                "detail": f"{contract_blocking} 项阻断",
                "recommendation": "优先修复写操作确认、执行身份、Provider pending 与 Capability installed 的不一致。",
            }
        )
    if write_contract_gap > 0:
        gap_items = write_contract.get("gap_operations") if isinstance(write_contract.get("gap_operations"), list) else []
        detail = "、".join(f"{item.get('source')}.{item.get('operation')}" for item in gap_items[:4] if isinstance(item, dict))
        issues.append(
            {
                "key": "provider_contract:write_confirmation_contract_gap",
                "severity": "high",
                "label": "写操作确认契约存在缺口",
                "detail": detail or f"{write_contract_gap} 项",
                "recommendation": "所有写入、审批、发送、会议、任务等 Action 必须先 dry-run，并在确认写目标后带 confirmation_token 执行。",
            }
        )
    if drift_count > 0:
        detail_items = status_summary.get("declared_missing_provider") if isinstance(status_summary.get("declared_missing_provider"), list) else []
        detail = "、".join(f"{item.get('source')}.{item.get('operation')}" for item in detail_items[:4] if isinstance(item, dict))
        issues.append(
            {
                "key": "provider_contract:capability_provider_drift",
                "severity": "medium",
                "label": "Capability 与 Provider 注册不一致",
                "detail": detail or f"{drift_count} 项",
                "recommendation": "补齐 Provider operation，或移除/降级不应暴露的 Capability。",
            }
        )
    if issues:
        _append_snapshot_gate_issues(snapshot, issues)


def _apply_pending_confirmation_to_snapshot(snapshot: dict[str, Any]) -> None:
    issues: list[dict[str, Any]] = []
    pending_action = snapshot.get("pending_action") if isinstance(snapshot.get("pending_action"), dict) else {}
    if pending_action.get("available") and pending_action.get("expired"):
        issues.append(
            {
                "key": "action_closure:expired_pending_action",
                "severity": "medium",
                "label": "存在已过期的待确认动作",
                "detail": str(pending_action.get("id") or ""),
                "recommendation": "清理过期 pending action，用户需要重新发起操作，避免旧确认继续执行。",
            }
        )
    pending_approval = snapshot.get("pending_approval") if isinstance(snapshot.get("pending_approval"), dict) else {}
    if pending_approval.get("available") and pending_approval.get("expired"):
        issues.append(
            {
                "key": "action_closure:expired_pending_approval",
                "severity": "medium",
                "label": "存在已过期的待确认审批",
                "detail": str(pending_approval.get("id") or ""),
                "recommendation": "清理过期审批确认卡，用户需要重新点击通过/拒绝或重新勾选批量审批。",
            }
        )
    pending_cleanup = snapshot.get("pending_cleanup") if isinstance(snapshot.get("pending_cleanup"), dict) else {}
    cleaned_count = int(pending_cleanup.get("cleaned_count") or 0)
    if cleaned_count > 0:
        cleaned = pending_cleanup.get("cleaned") if isinstance(pending_cleanup.get("cleaned"), list) else []
        issues.append(
            {
                "key": "action_closure:pending_cleanup",
                "severity": "low",
                "label": "已自动清理过期待确认项",
                "detail": "、".join(str(item) for item in cleaned[:6]) or f"{cleaned_count} 项",
                "recommendation": "这是已处理的动作闭环事件；如用户仍需执行，应重新发起操作并重新确认。",
            }
        )
    if issues:
        _append_snapshot_gate_issues(snapshot, issues)


def _apply_migration_queue_to_snapshot(snapshot: dict[str, Any]) -> None:
    capability = snapshot.get("capability_summary") if isinstance(snapshot.get("capability_summary"), dict) else {}
    queue = capability.get("migration_queue_summary") if isinstance(capability.get("migration_queue_summary"), dict) else {}
    if int(queue.get("count") or 0) <= 0:
        return
    top_item = queue.get("top_item") if isinstance(queue.get("top_item"), dict) else {}
    if not top_item:
        return
    source = str(top_item.get("source") or "")
    operation = str(top_item.get("operation") or "")
    label = str(top_item.get("label") or top_item.get("strategy") or "")
    priority = str(top_item.get("migration_priority_label") or "中")
    next_step = str(top_item.get("migration_next_step") or "按迁移队列优先把该能力迁入完整 V5 主链路。")
    stage_plan = top_item.get("migration_stage_plan") if isinstance(top_item.get("migration_stage_plan"), list) else []
    stage_summary = "；".join(
        f"{stage.get('label') or stage.get('stage')}：{stage.get('target')}"
        for stage in [entry for entry in stage_plan if isinstance(entry, dict)][:3]
    )
    _append_snapshot_gate_issues(
        snapshot,
        [
            {
                "key": "migration_queue:top_item",
                "severity": "high" if priority == "高" else "medium",
                "label": "存在待迁移的高优先级运行路径",
                "detail": f"{priority}｜{source}.{operation}｜{label}",
                "recommendation": next_step + (f" 阶段计划：{stage_summary}" if stage_summary else ""),
            }
        ],
    )


def _apply_retired_source_contract_to_snapshot(snapshot: dict[str, Any]) -> None:
    provider_registry = snapshot.get("provider_registry") if isinstance(snapshot.get("provider_registry"), dict) else {}
    retired_sources = provider_registry.get("retired_registered_sources") if isinstance(provider_registry.get("retired_registered_sources"), list) else []
    if not retired_sources:
        return
    _append_snapshot_gate_issues(
        snapshot,
        [
            {
                "key": "retired_source:registered",
                "severity": "high",
                "label": "退役来源仍在 V5 Provider 中注册",
                "detail": "、".join(str(source) for source in retired_sources[:6]),
                "recommendation": "移除退役 source 注册，按 source_aliases 迁移到新 Provider；当前 message 应统一走 im。",
            }
        ],
    )


def _apply_snapshot_integrity_to_snapshot(snapshot: dict[str, Any]) -> None:
    required_sections = {
        "health": dict,
        "runtime_gate": dict,
        "repair_plan": dict,
        "health_matrix": dict,
        "constitution_guard": dict,
        "capability_summary": dict,
        "provider_registry": dict,
        "current_path_maturity": dict,
        "current_capability_readiness": dict,
        "source_execution_contract": dict,
        "result_context": dict,
        "result_context_event_summary": dict,
        "action_closure": dict,
    }
    missing: list[str] = []
    empty: list[str] = []
    for key, expected_type in required_sections.items():
        if key not in snapshot:
            missing.append(key)
            continue
        value = snapshot.get(key)
        if not isinstance(value, expected_type):
            missing.append(key)
            continue
        if value in ({}, []):
            empty.append(key)
    snapshot["snapshot_integrity"] = {
        "status": "healthy" if not missing and not empty else "needs_attention",
        "label": "诊断快照完整" if not missing and not empty else "诊断快照存在盲区",
        "required_count": len(required_sections),
        "missing_count": len(missing),
        "empty_count": len(empty),
        "missing_sections": missing,
        "empty_sections": empty,
    }
    if missing or empty:
        _append_snapshot_gate_issues(
            snapshot,
            [
                {
                    "key": "snapshot_integrity:missing_sections",
                    "severity": "medium",
                    "label": "诊断快照存在缺失或空模块",
                    "detail": "缺失：" + "、".join(missing[:6]) + ("；空：" + "、".join(empty[:6]) if empty else ""),
                    "recommendation": "补齐 bot 快照组装字段，确保状态页、门禁、修复计划都基于完整诊断段。",
                }
            ],
        )


def _append_snapshot_gate_issues(snapshot: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    if not issues:
        return
    layer_key, layer_label, repair_source = _snapshot_issue_group(issues)
    health_matrix = snapshot.get("health_matrix") if isinstance(snapshot.get("health_matrix"), dict) else {}
    if health_matrix:
        layers = health_matrix.get("layers") if isinstance(health_matrix.get("layers"), list) else []
        layers.append(
            {
                "key": layer_key,
                "label": layer_label,
                "status": "needs_attention",
                "status_label": "需关注",
                "issue_count": len(issues),
            }
        )
        health_matrix["layers"] = layers
        health_matrix["issue_count"] = int(health_matrix.get("issue_count") or 0) + len(issues)
        unhealthy = [item for item in layers if isinstance(item, dict) and item.get("status") not in {"healthy", "none"}]
        health_matrix["status"] = "healthy" if not unhealthy else "needs_attention"
        health_matrix["label"] = "各层健康" if not unhealthy else f"{len(unhealthy)} 层需要关注"
        health_matrix["status_counts"] = _manual_status_counts(layers)
    gate = snapshot.get("runtime_gate") if isinstance(snapshot.get("runtime_gate"), dict) else {}
    repair_plan = snapshot.get("repair_plan") if isinstance(snapshot.get("repair_plan"), dict) else {}
    fingerprinted = [_manual_issue_fingerprint(issue) for issue in issues]
    if gate:
        priority_items = gate.get("priority_items") if isinstance(gate.get("priority_items"), list) else []
        priority_items = fingerprinted + priority_items
        gate["priority_items"] = priority_items[:8]
        gate["status"] = "needs_attention"
        gate["label"] = "需要继续加固"
        gate["issue_count"] = int(gate.get("issue_count") or 0) + len(fingerprinted)
        gate["primary_issue_fingerprint"] = priority_items[0].get("fingerprint", "") if priority_items else ""
        gate["primary_issue_key"] = priority_items[0].get("key", "") if priority_items else ""
        gate["severity_counts"] = _manual_severity_counts(priority_items)
        gate["next_step"] = priority_items[0].get("recommendation", "") if priority_items else gate.get("next_step", "")
    if repair_plan:
        repair_items = repair_plan.get("items") if isinstance(repair_plan.get("items"), list) else []
        repair_candidates = [
            _manual_issue_fingerprint(
                {
                    "source": repair_source,
                    "severity": item.get("severity") or "medium",
                    "label": item.get("label") or layer_label,
                    "detail": item.get("detail") or "",
                    "next_step": item.get("recommendation") or "",
                }
            )
            for item in issues
        ]
        repair_plan["items"] = (repair_candidates + repair_items)[:8]
        repair_plan["status"] = "needs_attention"
        repair_plan["item_count"] = len(repair_plan["items"])
        repair_plan["primary_issue_fingerprint"] = repair_plan["items"][0].get("fingerprint", "") if repair_plan["items"] else ""
        repair_plan["primary_issue_source"] = repair_plan["items"][0].get("source", "") if repair_plan["items"] else ""
        repair_plan["severity_counts"] = _manual_severity_counts(repair_plan["items"])
        repair_plan["next_step"] = repair_plan["items"][0].get("next_step", "") if repair_plan["items"] else repair_plan.get("next_step", "")


def _snapshot_issue_group(issues: list[dict[str, Any]]) -> tuple[str, str, str]:
    first_key = str(issues[0].get("key") or "") if issues and isinstance(issues[0], dict) else ""
    if first_key.startswith("source_execution:"):
        return "source_contract", "来源执行", "source_execution_contract"
    if first_key.startswith("snapshot_integrity:"):
        return "snapshot_integrity", "快照完整性", "snapshot_integrity"
    if first_key.startswith("capability:"):
        return "capability", "能力就绪", "current_capability_readiness"
    if first_key.startswith("migration_queue:"):
        return "migration_queue", "迁移队列", "migration_queue"
    if first_key.startswith("retired_source:"):
        return "retired_source", "退役来源", "retired_source_contract"
    if first_key.startswith("result_context_quality:"):
        return "result_context_quality", "结果上下文质量", "result_context_quality"
    if first_key.startswith("provider_contract:"):
        return "provider_contract", "Provider 契约", "provider_registry"
    if first_key.startswith("action_closure:"):
        return "action_closure", "动作闭环", "action_closure"
    return "result_context_event", "结果上下文事件", "result_context_event_summary"


def _runtime_stage_label(stage: str) -> str:
    return {
        "loaded": "已加载",
        "checked": "已检查",
        "observed": "旁路观测",
        "used_previous_result": "使用上一轮结果",
        "routed": "已路由",
        "not_routed": "未路由",
        "allowed": "已允许",
        "missing": "缺失",
        "empty": "空结果",
        "queued": "已入队",
        "started": "处理中",
        "confirmation_card_started": "正在发送确认卡",
        "confirmation_card_sent": "确认卡已发送",
        "confirmation_card_failed": "确认卡发送失败",
        "clarification": "等待补充信息",
        "permission_denied": "权限不足",
        "pending_confirmation": "等待确认",
        "provider_error": "Provider 执行异常",
        "success": "执行成功",
        "not_executed": "未执行",
        "partial": "部分成功",
        "error": "执行失败",
        "failed": "执行失败",
        "unknown": "未知",
        "skipped": "已跳过",
        "denied": "无权限",
        "cancelled": "已取消",
        "stale": "已失效",
        "stale_cleanup": "过期已清理",
    }.get(stage, stage)


def _runtime_question_type_label(question_type: str) -> str:
    if not question_type:
        return "类型未知"
    return {
        "query": "查询",
        "analysis": "分析",
        "insight": "洞察",
        "decision": "决策",
        "action": "行动",
    }.get(question_type, question_type)


def _runtime_data_scope_label(data_scope: str) -> str:
    if not data_scope:
        return "范围未知"
    return {
        "self": "本人范围",
        "person": "人员范围",
        "department": "部门范围",
        "company": "公司范围",
        "project": "项目范围",
        "organization": "组织范围",
        "external": "外部范围",
    }.get(data_scope, data_scope)


def _runtime_execution_identity_label(identity: str) -> str:
    return {
        "bot": "机器人",
        "user": "用户",
    }.get(identity, identity or "未知")


def _runtime_action_status_group_label(group: str) -> str:
    return {
        "terminal": "已结束",
        "pending": "处理中",
        "prepared": "待确认",
        "unknown": "未知",
    }.get(group, group or "未知")


def _runtime_confirmation_reason_label(reason: str) -> str:
    return {
        "capability_requires_confirmation": "能力要求",
        "high_risk_action": "高风险动作",
        "action_question": "行动类问题",
    }.get(reason, reason or "未知")


def _runtime_pipeline_label(name: str) -> str:
    return {
        "pre_gateway": "Pre Gateway",
        "result_followup_detector": "Result Follow-up Detector",
        "intent_recognition": "Intent Recognition",
        "task_planner": "Task Planner",
        "permission_check": "Permission Check",
        "capability_router": "Capability Router",
        "execution": "Execution",
        "answer_composer": "Answer Composer",
    }.get(name, name)


def _runtime_stage_name_label(name: str) -> str:
    return {
        "result_followup_detector": "结果追问识别",
        "intent_recognition": "意图识别",
        "task_planner": "任务规划",
        "permission_check": "权限检查",
        "capability_router": "能力路由与执行",
        "answer_composer": "答案组织",
    }.get(name, name or "未知")


def _runtime_provider_speed_label(duration_ms: int) -> str:
    if duration_ms >= 8000:
        return "很慢"
    if duration_ms >= 3000:
        return "较慢"
    return ""


def _runtime_provider_speed_suggestion(source: str, operation: str, duration_ms: int) -> str:
    if duration_ms < 3000:
        return ""
    if source == "approval":
        return "优先检查附件读取、审批详情接口和历史记录生成。"
    if source == "people":
        return "优先使用组织快照缓存，避免每次全量拉通讯录。"
    if source == "base":
        return "优先批量写入并减少字段/记录重复创建。"
    if source in {"im", "message"}:
        return "优先检查目标解析和消息发送权限。"
    if source == "calendar":
        return "优先检查时间解析、参会人解析和日程写入接口。"
    if source == "task":
        return "优先检查任务列表分页和写入确认。"
    if source == "mail":
        return "优先检查邮件搜索范围和附件/正文读取。"
    return "优先查看该 Provider 的飞书接口耗时和权限返回。"


def _runtime_provider_substep_lines(item: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if item.get("cache_hit") is True:
        lines.append("- 子步骤：使用缓存。")
    elif item.get("cache_hit") is False and int(item.get("fetch_ms") or 0) > 0:
        lines.append(f"- 子步骤：接口读取 {int(item.get('fetch_ms') or 0)}ms。")
    substeps = item.get("substeps") if isinstance(item.get("substeps"), list) else []
    for substep in substeps[:4]:
        if not isinstance(substep, dict):
            continue
        name = _runtime_provider_substep_label(str(substep.get("step") or ""))
        duration_ms = int(substep.get("duration_ms") or 0)
        status = _runtime_stage_label(str(substep.get("status") or "unknown"))
        row_count = substep.get("row_count")
        lines.append(f"- 子步骤：{name}｜{status}｜{duration_ms}ms" + (f"｜{row_count} 行" if row_count not in (None, "") else ""))
    return lines


def _runtime_provider_substep_label(step: str) -> str:
    return {
        "base_create": "创建多维表格文件",
        "table_create": "创建数据表",
        "record_batch_create": "批量写入记录",
        "approval_task_query": "查询待审批列表",
        "approval_detail": "读取审批详情",
        "approval_detail_batch": "批量读取审批详情",
        "approval_history": "读取历史审批",
        "approval_attachments": "读取审批附件",
        "approval_ai_judgement": "AI 审批判断",
    }.get(step, step or "未知")


def _runtime_source_label(source: str) -> str:
    return {
        "task": "任务",
        "calendar": "日程",
        "mail": "邮件",
        "message": "结果发送",
        "approval": "审批",
        "im": "飞书消息",
        "people": "通讯录",
        "base": "多维表格",
        "docs": "飞书文档",
        "wiki": "飞书知识库",
        "drive": "飞书云盘",
        "sheets": "飞书电子表格",
        "vc": "飞书会议",
        "minutes": "飞书妙记",
        "note": "飞书会议纪要",
        "markdown": "飞书 Markdown",
        "apps": "飞书妙搭应用",
        "openapi": "飞书原生接口",
        "attendance": "飞书考勤",
        "okr": "飞书 OKR",
        "slides": "飞书幻灯片",
        "whiteboard": "飞书画板",
        "vc_agent": "飞书会中能力",
        "company_profile": "公司档案",
        "knowledge": "企业知识库",
        "workevent": "工作事件",
        "memory": "长期记忆",
        "web": "网页资料",
        "runtime": "运行时",
    }.get(source, source)


def _runtime_result_context_kind_label(kind: str) -> str:
    return {
        "query_result": "查询结果",
        "action_receipt": "动作完成结果",
        "pending_confirmation": "待确认操作",
        "no_result": "空结果",
    }.get(kind, kind)


def _runtime_empty_reason_label(reason: str) -> str:
    return {
        "no_provider_executed": "没有 Provider 被执行",
        "all_providers_skipped": "全部 Provider 已跳过",
        "missing_params": "缺少必要参数",
        "low_confidence": "语义置信度不足",
        "clarification": "等待补充信息",
        "tool_not_installed": "原子能力未接入",
        "capability_not_installed": "能力已规划但原子能力未启用",
        "operation_not_installed": "Provider 操作未接入",
        "not_installed": "能力未安装",
        "unsupported_operation": "Provider 暂不支持该操作",
        "operation_not_supported": "Provider 暂不支持该操作",
        "not_supported": "Provider 暂不支持该能力",
        "provider_error": "Provider 执行失败",
        "permission_denied": "权限不足",
        "no_company_permission": "没有公司范围权限",
        "empty_items": "没有结构化条目",
        "missing_dependency_result": "缺少上一步结果",
    }.get(reason, reason or "未知")


def _runtime_followup_type_label(followup_type: str) -> str:
    return {
        "receipt_detail": "动作结果追问",
        "position": "按序号追问",
        "detail": "详情追问",
        "pronoun": "指代追问",
        "expand": "展开追问",
    }.get(followup_type, followup_type or "追问")


def _runtime_result_context_event_label(action: str) -> str:
    return {
        "save": "保存",
        "clear": "清除",
    }.get(action, action or "未知")


def _runtime_result_context_clear_reason_label(reason: str) -> str:
    return {
        "query_without_result_context": "本轮查询没有生成结构化结果",
        "runtime_action_success": "动作成功后清除旧结果",
        "approval_action_success": "审批动作成功后清除旧审批列表",
        "approval_batch_success": "批量审批成功后清除旧审批列表",
    }.get(reason, reason)


def _runtime_error_type_label(error_type: str) -> str:
    return {
        "missing_params": "缺少必要参数",
        "tool_not_installed": "原子能力未接入",
        "capability_not_installed": "能力已规划但原子能力未启用",
        "operation_not_installed": "Provider 操作未接入",
        "not_installed": "能力未安装",
        "unsupported_operation": "Provider 暂不支持该操作",
        "operation_not_supported": "Provider 暂不支持该操作",
        "not_supported": "Provider 暂不支持该能力",
        "tool_execution_failed": "飞书原子能力执行失败",
        "ambiguous_target": "目标不唯一",
        "provider_not_registered": "Provider 未注册",
        "missing_dependency_result": "缺少上一步结果",
        "missing_company": "缺少公司上下文",
    }.get(error_type, error_type or "未知错误")


def _compact_runtime_text(text: str, limit: int) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: max(limit - 1, 0)] + "…"


def _runtime_action_kind_label(kind: str) -> str:
    return {
        "runtime_confirmation": "确认操作",
        "approval_single": "单笔审批",
        "approval_batch": "批量审批",
        "approval_workbench": "审批工作台",
        "runtime_action": "运行操作",
    }.get(kind, kind or "操作")


def _runtime_action_label(action: str) -> str:
    return {
        "confirm": "确认",
        "cancel": "取消",
        "prepare_confirm": "准备确认",
        "confirmation_without_pending_action": "无待确认操作",
        "approve": "通过",
        "reject": "拒绝",
        "transfer": "转交",
        "add_sign": "加签",
        "rollback": "退回",
        "remind": "催办",
        "cc": "抄送",
        "approval_workbench": "生成审批工作台",
        "approval_query": "审批查询",
        "approval_detail": "审批详情",
        "approval_approve": "审批通过",
        "approval_reject": "审批拒绝",
        "approval_transfer": "审批转交",
        "approval_add_sign": "审批加签",
        "approval_rollback": "审批退回",
        "approval_remind": "审批催办",
        "approval_cancel": "审批撤回",
        "approval_cc": "审批抄送",
        "approval_single": "单笔审批",
        "approval_batch": "批量审批",
        "approval_initiated": "我发起的审批",
        "people_lookup": "人员查询",
        "department_members": "部门成员查询",
        "organization_snapshot": "组织架构查询",
        "organization_export": "导出组织架构",
        "task_query": "任务查询",
        "task_search": "任务搜索",
        "send_result": "发送结果",
        "message_send": "发送消息",
        "message_query": "消息查询",
        "chat_search": "群聊搜索",
        "chat_create": "创建群聊",
        "task_create": "创建任务",
        "task_complete": "完成任务",
        "calendar_query": "日程查询",
        "calendar_create": "创建日程",
        "mail_query": "最近邮件",
        "mail_search": "邮件搜索",
        "mail_get_message": "邮件详情",
        "mail_draft_create": "创建邮件草稿",
        "company_intro": "公司介绍",
        "risk_analysis": "风险分析",
        "general_analysis": "综合分析",
        "decision_advice": "决策建议",
        "general_query": "通用查询",
        "list_pending": "查询待审批",
        "get_detail": "读取详情",
        "list_initiated": "查询我发起的审批",
        "search_person": "查找人员",
        "list_department_members": "查询部门成员",
        "get_org_snapshot": "读取组织架构",
        "write_records": "写入表格",
        "list_my_tasks": "查询我的任务",
        "search_tasks": "搜索任务",
        "create_task": "创建任务",
        "complete_task": "完成任务",
        "list_events": "查询日程",
        "create_event": "创建日程",
        "list_recent": "查询最近邮件",
        "search_messages": "搜索消息",
        "get_message": "读取邮件详情",
        "create_draft": "创建草稿",
        "send_message": "发送消息",
        "search_chats": "搜索群聊",
        "list_messages": "查询消息",
        "create_chat": "创建群聊",
    }.get(action, action or "操作")


def _runtime_v5_route_label(envelope) -> str:
    if envelope.composed.metadata.get("requires_confirmation"):
        return "操作确认"
    if envelope.plan.strategy == "company_intro":
        return "公司介绍"
    if envelope.plan.strategy == "risk_analysis":
        return "风险洞察"
    if envelope.plan.strategy == "general_query":
        return "企业知识问答"
    return label_for_strategy(envelope.plan.strategy) or "Runtime V5"


def record_command_context(
    db: Session,
    app_config: FeishuAppConfig,
    identity: BotIdentity,
    chat_id: str | None,
    question: str,
    normalized: str,
    reply: str,
) -> None:
    from app.services.agent.context import record_bot_session

    actor = actor_from_identity(identity)
    record_bot_session(
        db,
        company_id=app_config.company_id,
        actor=actor,
        chat_id=chat_id,
        question=question,
        normalized_command=normalized,
        answer=reply,
        route_label=reply_route_value(reply),
        scope_label=reply_scope_value(reply),
    )
    db.commit()


def actor_from_identity(identity: BotIdentity) -> BotActor:
    return BotActor(
        role=identity.role,
        access_scope=identity.access_scope,
        domains=identity.domains,
        display_name=identity.display_name,
        open_id=identity.open_id,
    )


def reply_line_value(reply: str, label: str) -> str | None:
    prefix = f"{label}："
    for line in reply.splitlines()[:4]:
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    return None


def reply_scope_value(reply: str) -> str | None:
    value = reply_line_value(reply, "回答范围")
    if value:
        return value
    for line in reply.splitlines()[:4]:
        if line.startswith("范围："):
            return line.removeprefix("范围：").split("｜", 1)[0].strip()
    return None


def reply_route_value(reply: str) -> str | None:
    value = reply_line_value(reply, "能力路径")
    if value:
        return value
    for line in reply.splitlines()[:4]:
        if line.startswith("范围：") and "｜" in line:
            return line.split("｜", 1)[1].strip()
    return None
