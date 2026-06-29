from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ExtractionResult:
    success: bool
    text: str = ""
    mime_type: str | None = None
    filename: str = ""
    extractor: str = ""
    page_count: int | None = None
    language: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    error: str | None = None
