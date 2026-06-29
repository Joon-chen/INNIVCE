from __future__ import annotations

import re
from html import unescape

from app.shared.file_intelligence.models import ExtractionResult
from app.shared.file_intelligence.txt import decode_text


def extract_html(data: bytes, *, filename: str, mime_type: str | None, max_chars: int) -> ExtractionResult:
    raw = decode_text(data)
    raw = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
    text = re.sub(r"(?s)<[^>]+>", " ", raw)
    text = " ".join(unescape(text).split())[:max_chars]
    return ExtractionResult(
        success=bool(text),
        text=text,
        mime_type=mime_type,
        filename=filename,
        extractor="html",
    )
