from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Iterator


@dataclass
class LLMCallBudget:
    max_calls: int = 1
    turn_type: str = "default"
    calls: list[dict[str, Any]] = field(default_factory=list)
    denied_calls: list[dict[str, Any]] = field(default_factory=list)
    started_at: float = field(default_factory=perf_counter)


_CURRENT_BUDGET: ContextVar[LLMCallBudget | None] = ContextVar("llm_call_budget", default=None)


@contextmanager
def llm_call_budget(*, max_calls: int = 1, turn_type: str = "default") -> Iterator[LLMCallBudget]:
    budget = LLMCallBudget(max_calls=max(0, max_calls), turn_type=turn_type)
    token = _CURRENT_BUDGET.set(budget)
    try:
        yield budget
    finally:
        _CURRENT_BUDGET.reset(token)


def foreground_user_turn_budget() -> Iterator[LLMCallBudget]:
    return llm_call_budget(max_calls=1, turn_type="foreground_user_turn")


def background_cognitive_job_budget(*, max_calls: int = 3) -> Iterator[LLMCallBudget]:
    return llm_call_budget(max_calls=max_calls, turn_type="background_cognitive_job")


def authorize_llm_call(*, task_type: str, provider: str, model: str, lane: str = "") -> tuple[bool, dict[str, Any]]:
    budget = _CURRENT_BUDGET.get()
    if budget is None:
        return True, {"budget_active": False}
    payload = {
        "budget_active": True,
        "turn_type": budget.turn_type,
        "max_calls": budget.max_calls,
        "call_index": len(budget.calls) + len(budget.denied_calls) + 1,
        "task_type": task_type,
        "lane": lane,
        "provider": provider,
        "model": model,
    }
    if len(budget.calls) >= budget.max_calls:
        budget.denied_calls.append({**payload, "status": "budget_denied"})
        return False, payload
    budget.calls.append({**payload, "status": "authorized"})
    return True, payload


def current_llm_call_budget_summary() -> dict[str, Any]:
    budget = _CURRENT_BUDGET.get()
    if budget is None:
        return {"available": False}
    return {
        "available": True,
        "turn_type": budget.turn_type,
        "max_calls": budget.max_calls,
        "used_calls": len(budget.calls),
        "denied_calls": len(budget.denied_calls),
        "duration_ms": int((perf_counter() - budget.started_at) * 1000),
        "calls": list(budget.calls),
        "denied": list(budget.denied_calls),
    }
