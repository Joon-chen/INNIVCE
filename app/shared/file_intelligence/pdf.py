from __future__ import annotations

import logging
from io import BytesIO

from app.core.config import settings
from app.shared.file_intelligence.image_ocr import extract_image
from app.shared.file_intelligence.models import ExtractionResult

OCR_MIN_TEXT_CHARS = 20


def extract_pdf(data: bytes, *, filename: str, mime_type: str | None, max_chars: int) -> ExtractionResult:
    embedded = extract_pdf_embedded_text(data, filename=filename, mime_type=mime_type, max_chars=max_chars)
    if len(embedded.text.strip()) >= OCR_MIN_TEXT_CHARS:
        return embedded
    ocr = extract_pdf_ocr_text(data, filename=filename, mime_type=mime_type, max_chars=max_chars)
    warnings = embedded.warnings + ocr.warnings
    if ocr.success or ocr.text:
        return ExtractionResult(
            success=ocr.success,
            text=ocr.text,
            mime_type=mime_type,
            filename=filename,
            extractor="pdf_ocr",
            page_count=ocr.page_count or embedded.page_count,
            language=ocr.language,
            metadata=ocr.metadata,
            warnings=warnings,
            error=ocr.error,
        )
    return ExtractionResult(
        success=False,
        text=embedded.text,
        mime_type=mime_type,
        filename=filename,
        extractor="pdf",
        page_count=embedded.page_count,
        warnings=warnings or ("no_text_extracted",),
        error=ocr.error or embedded.error,
    )


def extract_pdf_embedded_text(data: bytes, *, filename: str, mime_type: str | None, max_chars: int) -> ExtractionResult:
    try:
        logging.getLogger("pypdf").setLevel(logging.ERROR)
        logging.getLogger("pypdf._reader").setLevel(logging.ERROR)
        from pypdf import PdfReader
    except Exception as exc:
        return ExtractionResult(
            success=False,
            mime_type=mime_type,
            filename=filename,
            extractor="pdf_embedded",
            error=str(exc)[:300],
        )
    try:
        reader = PdfReader(BytesIO(data))
        parts = [(page.extract_text() or "").strip() for page in reader.pages[:5]]
        text = "\n".join(part for part in parts if part)[:max_chars]
        return ExtractionResult(
            success=bool(text),
            text=text,
            mime_type=mime_type,
            filename=filename,
            extractor="pdf_embedded",
            page_count=len(reader.pages),
        )
    except Exception as exc:
        return ExtractionResult(
            success=False,
            mime_type=mime_type,
            filename=filename,
            extractor="pdf_embedded",
            error=str(exc)[:300],
        )


def extract_pdf_ocr_text(data: bytes, *, filename: str, mime_type: str | None, max_chars: int) -> ExtractionResult:
    if not settings.ocr_enabled:
        text = "（附件为扫描件或图片，已下载但无法自动提取文字。请展开附件查看发票或凭证详情。）"
        return ExtractionResult(
            success=True,
            text=text[:max_chars],
            mime_type=mime_type,
            filename=filename,
            extractor="pdf_ocr",
            language=settings.ocr_languages,
            warnings=("ocr_disabled",),
        )
    try:
        import fitz
    except Exception as exc:
        return ExtractionResult(
            success=False,
            mime_type=mime_type,
            filename=filename,
            extractor="pdf_ocr",
            language=settings.ocr_languages,
            error=str(exc)[:300],
        )
    try:
        document = fitz.open(stream=data, filetype="pdf")
        parts: list[str] = []
        for page_index in range(min(document.page_count, settings.ocr_max_pdf_pages)):
            page = document.load_page(page_index)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            result = extract_image(pixmap.tobytes("png"), filename=f"{filename}#page={page_index + 1}", mime_type="image/png", max_chars=max_chars)
            if result.text:
                parts.append(result.text)
        text = "\n".join(parts)[:max_chars]
        return ExtractionResult(
            success=bool(text),
            text=text,
            mime_type=mime_type,
            filename=filename,
            extractor="pdf_ocr",
            page_count=document.page_count,
            language=settings.ocr_languages,
        )
    except Exception as exc:
        return ExtractionResult(
            success=False,
            mime_type=mime_type,
            filename=filename,
            extractor="pdf_ocr",
            language=settings.ocr_languages,
            error=str(exc)[:300],
        )
