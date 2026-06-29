from __future__ import annotations

from app.shared.file_intelligence.models import ExtractionResult
from app.shared.file_intelligence.registry import extractor_for

TEXT_PREVIEW_CHARS = 1200


def extract_file(
    data: bytes,
    *,
    filename: str,
    mime_type: str | None = None,
    content_type: str | None = None,
    max_chars: int = TEXT_PREVIEW_CHARS,
) -> ExtractionResult:
    resolved_mime_type = mime_type or content_type
    extractor = extractor_for(filename=filename, mime_type=resolved_mime_type, max_chars=max_chars)
    if extractor is None:
        return ExtractionResult(
            success=False,
            mime_type=resolved_mime_type,
            filename=filename,
            extractor="unsupported",
            warnings=("unsupported_file_type",),
        )
    result = extractor(data)
    if len(result.text) <= max_chars:
        return result
    return ExtractionResult(
        success=result.success,
        text=result.text[:max_chars],
        mime_type=result.mime_type,
        filename=result.filename,
        extractor=result.extractor,
        page_count=result.page_count,
        language=result.language,
        metadata=result.metadata,
        warnings=result.warnings,
        error=result.error,
    )


def extract_file_text(
    data: bytes,
    *,
    filename: str,
    content_type: str | None = None,
    mime_type: str | None = None,
    max_chars: int = TEXT_PREVIEW_CHARS,
) -> str:
    return extract_file(data, filename=filename, mime_type=mime_type, content_type=content_type, max_chars=max_chars).text
