from __future__ import annotations

from dataclasses import dataclass


SEMANTIC_FRAME_REQUIRED_FIELDS = (
    "speech_act",
    "topic",
    "target",
    "operation",
    "parameters",
    "requested_output",
    "presentation",
    "confidence",
    "ambiguities",
)


@dataclass(frozen=True)
class SemanticFrameSchema:
    version: str = "semantic_protocol_v1"
    required_fields: tuple[str, ...] = SEMANTIC_FRAME_REQUIRED_FIELDS
    forbidden_fields: tuple[str, ...] = (
        "capability",
        "provider",
        "permission",
        "identity",
        "credential",
        "runtime_plan",
        "execution_result",
    )
