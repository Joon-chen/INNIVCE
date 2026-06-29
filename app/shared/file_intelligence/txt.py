from __future__ import annotations

from app.shared.file_intelligence.models import ExtractionResult


TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".log"}


def decode_text(data: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding, errors="replace").strip()
        except LookupError:
            continue
    return data.decode("utf-8", errors="replace").strip()


def extract_text(data: bytes, *, filename: str, mime_type: str | None, max_chars: int) -> ExtractionResult:
    text = decode_text(data)[:max_chars]
    return ExtractionResult(
        success=bool(text),
        text=text,
        mime_type=mime_type,
        filename=filename,
        extractor="text",
    )
