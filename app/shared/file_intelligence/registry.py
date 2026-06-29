from __future__ import annotations

from pathlib import Path
from typing import Callable

from app.shared.file_intelligence.docx import WORD_EXTENSIONS, extract_docx
from app.shared.file_intelligence.html import extract_html
from app.shared.file_intelligence.image_ocr import IMAGE_EXTENSIONS, extract_image
from app.shared.file_intelligence.models import ExtractionResult
from app.shared.file_intelligence.pdf import extract_pdf
from app.shared.file_intelligence.pptx import PRESENTATION_EXTENSIONS, extract_pptx
from app.shared.file_intelligence.txt import TEXT_EXTENSIONS, extract_text
from app.shared.file_intelligence.xlsx import SPREADSHEET_EXTENSIONS, extract_xlsx

Extractor = Callable[[bytes], ExtractionResult]


def extractor_for(*, filename: str, mime_type: str | None, max_chars: int) -> Extractor | None:
    suffix = Path(filename).suffix.lower()
    normalized_mime_type = (mime_type or "").lower()
    if suffix in TEXT_EXTENSIONS or normalized_mime_type.startswith("text/"):
        return lambda data: extract_text(data, filename=filename, mime_type=mime_type, max_chars=max_chars)
    if suffix in {".html", ".htm"} or "html" in normalized_mime_type:
        return lambda data: extract_html(data, filename=filename, mime_type=mime_type, max_chars=max_chars)
    if suffix == ".pdf" or "pdf" in normalized_mime_type:
        return lambda data: extract_pdf(data, filename=filename, mime_type=mime_type, max_chars=max_chars)
    if suffix in IMAGE_EXTENSIONS or normalized_mime_type.startswith("image/"):
        return lambda data: extract_image(data, filename=filename, mime_type=mime_type, max_chars=max_chars)
    if suffix in WORD_EXTENSIONS or "word" in normalized_mime_type:
        return lambda data: extract_docx(data, filename=filename, mime_type=mime_type, max_chars=max_chars)
    if suffix in SPREADSHEET_EXTENSIONS or "spreadsheet" in normalized_mime_type or "excel" in normalized_mime_type:
        return lambda data: extract_xlsx(data, filename=filename, mime_type=mime_type, max_chars=max_chars)
    if suffix in PRESENTATION_EXTENSIONS or "presentation" in normalized_mime_type or "powerpoint" in normalized_mime_type:
        return lambda data: extract_pptx(data, filename=filename, mime_type=mime_type, max_chars=max_chars)
    return None
