from __future__ import annotations

import logging
from io import BytesIO

from app.core.config import settings
from app.shared.file_intelligence.image_ocr import extract_image
from app.shared.file_intelligence.models import ExtractionResult

OCR_MIN_TEXT_CHARS = 20
OCR_GOOD_TEXT_CHARS = 2500


def extract_pdf(data: bytes, *, filename: str, mime_type: str | None, max_chars: int) -> ExtractionResult:
    embedded = extract_pdf_embedded_text(data, filename=filename, mime_type=mime_type, max_chars=max_chars)
    embedded_text = embedded.text.strip()
    if len(embedded_text) >= min(max_chars, OCR_GOOD_TEXT_CHARS):
        return embedded
    ocr = extract_pdf_ocr_text(data, filename=filename, mime_type=mime_type, max_chars=max_chars)
    warnings = embedded.warnings + ocr.warnings
    merged_text = _merge_pdf_text(embedded.text, ocr.text, max_chars=max_chars)
    if merged_text:
        return ExtractionResult(
            success=embedded.success or ocr.success,
            text=merged_text,
            mime_type=mime_type,
            filename=filename,
            extractor="pdf_embedded_ocr" if embedded_text and ocr.text else (ocr.extractor or embedded.extractor or "pdf"),
            page_count=embedded.page_count or ocr.page_count,
            language=ocr.language or embedded.language,
            metadata={
                "embedded_text_chars": len(embedded_text),
                "ocr_text_chars": len((ocr.text or "").strip()),
                "embedded_extractor": embedded.extractor,
                "ocr_extractor": ocr.extractor,
            },
            warnings=warnings,
            error=ocr.error or embedded.error,
        )
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


def _merge_pdf_text(embedded_text: str, ocr_text: str, *, max_chars: int) -> str:
    chunks: list[str] = []
    seen: set[str] = set()
    for source_text in (embedded_text, ocr_text):
        for raw_line in str(source_text or "").splitlines():
            line = " ".join(raw_line.split()).strip()
            if not line:
                continue
            key = line.lower()
            if key in seen:
                continue
            seen.add(key)
            chunks.append(line)
    return "\n".join(chunks)[:max_chars]


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
