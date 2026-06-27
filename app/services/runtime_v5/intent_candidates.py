from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from app.services.runtime_v5.models import IntentResult


@dataclass(frozen=True)
class IntentCandidate:
    intent: IntentResult
    domain: str
    source: str
    confidence: float
    evidence: tuple[str, ...] = ()
    route_reason: str = ""
    missing_slots: tuple[str, ...] = ()


@dataclass(frozen=True)
class IntentArbitration:
    selected: IntentResult | None
    candidates: tuple[IntentCandidate, ...] = ()
    reason: str = ""
    needs_clarification: bool = False


def arbitrate_intent_candidates(candidates: tuple[IntentCandidate, ...]) -> IntentArbitration:
    if not candidates:
        return IntentArbitration(selected=None)
    ordered = tuple(sorted(candidates, key=_candidate_rank, reverse=True))
    selected = ordered[0]
    competing = ordered[1] if len(ordered) > 1 else None
    if competing and _should_clarify(selected, competing):
        return IntentArbitration(
            selected=_clarification_intent(selected, competing, ordered),
            candidates=ordered,
            reason="candidate_conflict_requires_clarification",
            needs_clarification=True,
        )
    return IntentArbitration(
        selected=_with_arbitration_trace(
            selected.intent,
            candidates=ordered,
            reason=selected.route_reason or "candidate_selected",
        ),
        candidates=ordered,
        reason=selected.route_reason or "candidate_selected",
    )


def candidate_trace(candidates: tuple[IntentCandidate, ...], *, reason: str) -> dict[str, Any]:
    return {
        "source": "candidate_arbiter",
        "mode": "deterministic_candidates",
        "reason": reason,
        "candidates": [
            {
                "intent": candidate.intent.intent,
                "domain": candidate.domain,
                "source": candidate.source,
                "confidence": candidate.confidence,
                "evidence": list(candidate.evidence),
                "route_reason": candidate.route_reason,
                "missing_slots": list(candidate.missing_slots or candidate.intent.missing_params),
            }
            for candidate in candidates
        ],
    }


def _candidate_rank(candidate: IntentCandidate) -> tuple[int, int, float]:
    intent = candidate.intent
    business_candidate = int(candidate.domain != "Conversation")
    export_action = int(intent.intent == "organization_export")
    exact_field_query = int(candidate.domain == "People" and bool(intent.entities.get("people_query_field")))
    complete_candidate = int(not candidate.missing_slots and not intent.missing_params)
    explicit_action = int(intent.question_type == "action")
    return business_candidate, export_action, exact_field_query, complete_candidate, explicit_action, candidate.confidence


def _should_clarify(selected: IntentCandidate, competing: IntentCandidate) -> bool:
    if selected.intent.intent == "organization_export" or competing.intent.intent == "organization_export":
        return False
    if selected.confidence - competing.confidence > 0.08:
        return False
    if selected.domain == competing.domain:
        return False
    if selected.intent.intent == "smalltalk" or competing.intent.intent == "smalltalk":
        return False
    return True


def _clarification_intent(
    selected: IntentCandidate,
    competing: IntentCandidate,
    candidates: tuple[IntentCandidate, ...],
) -> IntentResult:
    domains = " / ".join(dict.fromkeys(candidate.domain for candidate in (selected, competing) if candidate.domain))
    answer = f"这句话可能指向 {domains} 两类事。我先不调用业务数据；请补一句你要查资料、查人，还是执行动作。"
    return IntentResult(
        question_type="query",
        intent="smalltalk",
        data_scope="self",
        entities={
            "fallback_answer": answer,
            "command_intent_trace": candidate_trace(candidates, reason="candidate_conflict_requires_clarification"),
        },
        missing_params=("intent_choice",),
        confidence=0.55,
        canonical_question=selected.intent.canonical_question,
    )


def _with_arbitration_trace(
    intent: IntentResult,
    *,
    candidates: tuple[IntentCandidate, ...],
    reason: str,
) -> IntentResult:
    entities = dict(intent.entities)
    entities["command_intent_trace"] = candidate_trace(candidates, reason=reason)
    return replace(intent, entities=entities)
