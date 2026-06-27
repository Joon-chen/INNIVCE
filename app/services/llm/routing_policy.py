from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.config import settings


LLMTaskType = Literal[
    "command_intent",
    "conversation",
    "presentation",
    "evidence_analysis",
    "snapshot_builder",
    "insight_generation",
    "long_summary",
    "reasoning",
    "default",
]
LLMProvider = Literal["deepseek_api", "openai_api", "local_fast", "local_reasoning", "disabled"]
LLMLane = Literal[
    "foreground_fast",
    "foreground_grounded",
    "background_extraction",
    "background_reasoning",
    "background_summary",
    "embedding",
]


@dataclass(frozen=True)
class LLMRoute:
    task_type: LLMTaskType
    lane: LLMLane
    provider: LLMProvider
    model: str
    latency_budget_ms: int
    allow_fallback: bool = False
    fallback_provider: LLMProvider = "disabled"


_LATENCY_BUDGETS = {
    "command_intent": 6000,
    "conversation": 6000,
    "presentation": 4500,
    "evidence_analysis": 30000,
    "snapshot_builder": 30000,
    "insight_generation": 45000,
    "long_summary": 60000,
    "reasoning": 30000,
    "default": 6000,
}

_TASK_LANES: dict[str, LLMLane] = {
    "command_intent": "foreground_fast",
    "conversation": "foreground_grounded",
    "presentation": "foreground_grounded",
    "evidence_analysis": "background_extraction",
    "snapshot_builder": "background_reasoning",
    "insight_generation": "background_reasoning",
    "long_summary": "background_summary",
    "reasoning": "background_reasoning",
    "default": "foreground_grounded",
}


def llm_route_for_task(task_type: LLMTaskType) -> LLMRoute:
    """Resolve the model route for an LLM task.

    V1 routes foreground and background language tasks to DeepSeek API for
    answer quality. Local models remain deployable infrastructure, but they are
    not used as the foreground default until a GPU-backed model is available.
    """

    normalized = task_type if task_type in _LATENCY_BUDGETS else "default"
    provider: LLMProvider = "deepseek_api"
    model = settings.deepseek_model
    return LLMRoute(
        task_type=normalized,  # type: ignore[arg-type]
        lane=_TASK_LANES[normalized],
        provider=provider,
        model=model,
        latency_budget_ms=_LATENCY_BUDGETS[normalized],
        allow_fallback=False,
    )


def llm_routing_policy_payload() -> dict[str, dict[str, object]]:
    return {
        task_type: {
            "lane": route.lane,
            "provider": route.provider,
            "model": route.model,
            "latency_budget_ms": route.latency_budget_ms,
            "allow_fallback": route.allow_fallback,
            "fallback_provider": route.fallback_provider,
        }
        for task_type in _LATENCY_BUDGETS
        for route in (llm_route_for_task(task_type),)
    }
