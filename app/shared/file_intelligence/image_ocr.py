from __future__ import annotations

from io import BytesIO

from app.core.config import settings
from app.shared.file_intelligence.models import ExtractionResult


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def extract_image(data: bytes, *, filename: str, mime_type: str | None, max_chars: int) -> ExtractionResult:
    if not settings.ocr_enabled:
        text = "（附件为图片，已下载但无法自动提取文字。请展开附件查看。）"
        return ExtractionResult(
            success=True,
            text=text[:max_chars],
            mime_type=mime_type,
            filename=filename,
            extractor="image_ocr",
            language=settings.ocr_languages,
            warnings=("ocr_disabled",),
        )
    try:
        from PIL import Image, ImageOps
        import pytesseract
    except Exception as exc:
        return ExtractionResult(
            success=False,
            mime_type=mime_type,
            filename=filename,
            extractor="image_ocr",
            language=settings.ocr_languages,
            error=str(exc)[:300],
        )
    try:
        image = Image.open(BytesIO(data))
        image = ImageOps.grayscale(image)
        text = " ".join(pytesseract.image_to_string(image, lang=settings.ocr_languages).split())[:max_chars]
        return ExtractionResult(
            success=bool(text),
            text=text,
            mime_type=mime_type,
            filename=filename,
            extractor="image_ocr",
            language=settings.ocr_languages,
        )
    except Exception as exc:
        return ExtractionResult(
            success=False,
            mime_type=mime_type,
            filename=filename,
            extractor="image_ocr",
            language=settings.ocr_languages,
            error=str(exc)[:300],
        )
