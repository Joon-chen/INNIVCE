from app.services.feishu.bot_runtime import _foreground_llm_call_audit, _runtime_status_answer_from_snapshot
from app.services.runtime_v5.bot_diagnostics import runtime_v5_diagnostics_snapshot_base
from app.services.runtime_v5.response_latency_trace import merge_gateway_latency_trace


def _llm_trace_summary() -> dict:
    return {
        "available": True,
        "call_count": 1,
        "total_duration_ms": 42,
        "fallback_used": False,
        "providers": ["deepseek_api"],
        "lanes": ["foreground_fast"],
        "task_types": ["command_intent"],
        "latest": {
            "task_type": "command_intent",
            "lane": "foreground_fast",
            "provider": "deepseek_api",
            "model": "deepseek-chat",
            "duration_ms": 42,
            "fallback_used": False,
            "status": "success",
            "prompt_audit": {
                "prompt_chars": 880,
                "line_count": 24,
                "profile_plane": "intent",
                "has_profile_context": False,
                "has_intent_profile_context": True,
                "has_presentation_profile_context": False,
                "has_session_context": False,
                "has_original_answer": False,
                "risk_count": 0,
                "risks": [],
            },
        },
    }


def test_runtime_diagnostics_snapshot_base_carries_llm_trace_summary() -> None:
    snapshot = runtime_v5_diagnostics_snapshot_base(
        runtime_summary={"llm_trace_summary": _llm_trace_summary()},
        current_capability_readiness={},
        pending_action={},
        pending_approval={},
        pending_cleanup={},
        current_approval={},
        result_context_events=[],
        capability_summary={},
        provider_registry={},
        runtime_provider_snapshot={},
        action_trace=[],
        decision_trace=[],
    )

    assert snapshot["llm_trace_summary"]["latest"]["provider"] == "deepseek_api"


def test_runtime_status_answer_includes_llm_route_summary() -> None:
    answer = _runtime_status_answer_from_snapshot(
        {
            "llm_trace_summary": _llm_trace_summary(),
            "llm_call_budget": {
                "available": True,
                "max_calls": 1,
                "used_calls": 1,
                "denied_calls": 0,
                "calls": [{"task_type": "command_intent"}],
                "denied": [],
            },
            "bot_response_timing": {
                "available": True,
                "total_ms": 520,
                "phases": [
                    {"phase": "runtime", "duration_ms": 120},
                    {"phase": "diagnostics", "duration_ms": 20},
                    {"phase": "answer", "duration_ms": 380},
                ],
                "slowest_phase": "answer",
                "slowest_phase_ms": 380,
                "llm_total_ms": 42,
                "llm_call_count": 1,
                "provider_total_ms": 15,
            },
            "runtime_health_grade": {"label": "可用", "usable": True},
        }
    )

    assert "LLM 路由：deepseek_api/deepseek-chat" in answer
    assert "lane foreground_fast" in answer
    assert "任务 command_intent" in answer
    assert "fallback 未发生" in answer
    assert "预算 1/1，拒绝 0" in answer
    assert "前台 LLM 调用：正常" in answer
    assert "已执行：command_intent" in answer
    assert "Prompt 审计：880 字" in answer
    assert "画像意图、会话无、原答案无" in answer
    assert "响应耗时：总 520ms" in answer
    assert "最慢 答案组织 380ms" in answer
    assert "LLM 42ms" in answer


def test_response_latency_trace_merges_runtime_and_gateway_timing() -> None:
    merged = merge_gateway_latency_trace(
        diagnostics_snapshot={
            "bot_response_timing": {
                "available": True,
                "total_ms": 800,
                "phases": [
                    {"phase": "runtime", "duration_ms": 500},
                    {"phase": "answer", "duration_ms": 100},
                ],
            }
        },
        gateway_trace={
            "available": True,
            "total_ms": 1200,
            "phases": [
                {"phase": "dispatch", "duration_ms": 900},
                {"phase": "reply_send", "duration_ms": 200},
            ],
            "route_path": "smalltalk",
            "reply_channel": "smart",
        },
    )

    trace = merged["response_latency_trace"]
    assert trace["available"] is True
    assert trace["total_ms"] == 1200
    assert trace["runtime_total_ms"] == 800
    assert trace["gateway_total_ms"] == 1200
    assert trace["slowest_phase"] == "dispatch"
    assert trace["route_path"] == "smalltalk"


def test_runtime_status_answer_prefers_response_latency_trace() -> None:
    answer = _runtime_status_answer_from_snapshot(
        {
            "response_latency_trace": {
                "available": True,
                "total_ms": 1300,
                "runtime_total_ms": 700,
                "gateway_total_ms": 1300,
                "phases": [
                    {"phase": "dispatch", "duration_ms": 900},
                    {"phase": "reply_send", "duration_ms": 200},
                ],
                "slowest_phase": "dispatch",
                "slowest_phase_ms": 900,
            },
            "runtime_health_grade": {"label": "可用", "usable": True},
        }
    )

    assert "响应耗时：总 1300ms" in answer
    assert "最慢 网关调度 900ms" in answer
    assert "网关 1300ms" in answer
    assert "发送 200ms" in answer


def test_runtime_status_detail_includes_llm_route_summary() -> None:
    answer = _runtime_status_answer_from_snapshot(
        {
            "llm_trace_summary": _llm_trace_summary(),
            "runtime_health_grade": {"label": "可用", "usable": True},
        },
        detail=True,
    )

    assert "LLM 路由：deepseek_api/deepseek-chat" in answer
    assert "lane foreground_fast" in answer
    assert "耗时 42ms" in answer


def test_runtime_status_answer_surfaces_foreground_llm_denied_attempt() -> None:
    answer = _runtime_status_answer_from_snapshot(
        {
            "llm_trace_summary": _llm_trace_summary(),
            "llm_call_budget": {
                "available": True,
                "max_calls": 1,
                "used_calls": 1,
                "denied_calls": 1,
                "calls": [{"task_type": "command_intent"}],
                "denied": [{"task_type": "presentation"}],
            },
            "runtime_health_grade": {"label": "可用", "usable": True},
        }
    )

    assert "前台 LLM 调用：有调用被预算拒绝" in answer
    assert "尝试 2" in answer
    assert "被拒绝：presentation" in answer


def test_foreground_llm_call_audit_marks_budget_denied() -> None:
    audit = _foreground_llm_call_audit(
        {
            "available": True,
            "max_calls": 1,
            "used_calls": 1,
            "denied_calls": 1,
            "calls": [{"task_type": "command_intent"}],
            "denied": [{"task_type": "conversation"}],
        }
    )

    assert audit["status"] == "budget_denied"
    assert audit["attempted_calls"] == 2
    assert audit["call_tasks"] == ["command_intent"]
    assert audit["denied_tasks"] == ["conversation"]
