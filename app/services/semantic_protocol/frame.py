from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class SemanticFrame:
    """AI OS semantic protocol runtime object.

    This object is business-neutral. It describes what an input means, not how
    to route, authorize, or execute it.
    """

    speech_act: str = "ask"
    topic: str = ""
    target: dict[str, Any] = field(default_factory=dict)
    operation: str = "ask"
    requested_output: str = "natural_text"
    presentation: dict[str, Any] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    ambiguities: tuple[str, ...] = ()
    source: str = "semantic_protocol_v1"

    def payload(self) -> dict[str, Any]:
        data = asdict(self)
        data["ambiguities"] = list(self.ambiguities)
        return data
