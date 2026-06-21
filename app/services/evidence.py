from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


EVIDENCE_QUALITY_COMPLETE = "complete"
EVIDENCE_QUALITY_PARTIAL = "partial"
EVIDENCE_QUALITY_FAILED = "failed"
EVIDENCE_QUALITY_PENDING = "pending"


@dataclass(frozen=True)
class Evidence:
    evidence_type: str
    quality: str
    object_type: str
    object_id: str
    facts: dict[str, Any] = field(default_factory=dict)
    missing: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    technical_notes: tuple[str, ...] = ()
    manager_summary: str = ""
    suggested_next_step: str = ""
    source_event_ids: tuple[str, ...] = ()


def evidence_payload(evidence: Evidence) -> dict[str, Any]:
    return {
        "evidence_type": evidence.evidence_type,
        "quality": evidence.quality,
        "object_type": evidence.object_type,
        "object_id": evidence.object_id,
        "facts": dict(evidence.facts),
        "missing": list(evidence.missing),
        "conflicts": list(evidence.conflicts),
        "technical_notes": list(evidence.technical_notes),
        "manager_summary": evidence.manager_summary,
        "suggested_next_step": evidence.suggested_next_step,
        "source_event_ids": list(evidence.source_event_ids),
    }


def evidence_from_payload(payload: dict[str, Any]) -> Evidence:
    return Evidence(
        evidence_type=str(payload.get("evidence_type") or ""),
        quality=str(payload.get("quality") or EVIDENCE_QUALITY_PENDING),
        object_type=str(payload.get("object_type") or ""),
        object_id=str(payload.get("object_id") or ""),
        facts=dict(payload.get("facts") or {}),
        missing=tuple(str(item) for item in payload.get("missing") or [] if str(item).strip()),
        conflicts=tuple(str(item) for item in payload.get("conflicts") or [] if str(item).strip()),
        technical_notes=tuple(str(item) for item in payload.get("technical_notes") or [] if str(item).strip()),
        manager_summary=str(payload.get("manager_summary") or ""),
        suggested_next_step=str(payload.get("suggested_next_step") or ""),
        source_event_ids=tuple(str(item) for item in payload.get("source_event_ids") or [] if str(item).strip()),
    )
