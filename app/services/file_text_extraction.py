from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

from app.core.config import settings

TEXT_PREVIEW_CHARS = 1200
OCR_MIN_TEXT_CHARS = 20


def extract_file_text(data: bytes, *, filename: str, content_type: str | None = None, max_chars: int = TEXT_PREVIEW_CHARS) -> str:
    suffix = Path(filename).suffix.lower()
    normalized_content_type = (content_type or "").lower()
    if _looks_like_text(suffix, normalized_content_type):
        return _decode_text(data)[:max_chars]
    if suffix == ".pdf" or "pdf" in normalized_content_type:
        return _extract_pdf_text(data)[:max_chars]
    if _looks_like_image(suffix, normalized_content_type):
        return _extract_image_ocr_text(data)[:max_chars]
    if suffix in {".docx", ".doc"} or "word" in normalized_content_type:
        return _extract_docx_text(data)[:max_chars]
    if suffix in {".xlsx", ".xlsm", ".xls"} or "spreadsheet" in normalized_content_type or "excel" in normalized_content_type:
        return _extract_xlsx_text(data)[:max_chars]
    if suffix == ".csv":
        return _decode_text(data)[:max_chars]
    return ""


def _looks_like_text(suffix: str, content_type: str) -> bool:
    return suffix in {".txt", ".md", ".csv", ".json"} or content_type.startswith("text/")


def _looks_like_image(suffix: str, content_type: str) -> bool:
    return suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"} or content_type.startswith("image/")


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding, errors="replace").strip()
        except LookupError:
            continue
    return data.decode("utf-8", errors="replace").strip()


def _extract_pdf_text(data: bytes) -> str:
    embedded = _extract_pdf_embedded_text(data)
    if len(embedded.strip()) >= OCR_MIN_TEXT_CHARS:
        return embedded
    return _extract_pdf_ocr_text(data)


def _extract_pdf_embedded_text(data: bytes) -> str:
    try:
        logging.getLogger("pypdf").setLevel(logging.ERROR)
        from pypdf import PdfReader
    except Exception:
        return ""
    try:
        reader = PdfReader(BytesIO(data))
        parts = [(page.extract_text() or "").strip() for page in reader.pages[:5]]
        return "\n".join(part for part in parts if part)
    except Exception:
        return ""


def _extract_pdf_ocr_text(data: bytes) -> str:
    if not settings.ocr_enabled:
        return "（附件为扫描件或图片，已下载但无法自动提取文字。请展开附件查看发票或凭证详情。）"
    try:
        import fitz
    except Exception:
        return ""
    try:
        document = fitz.open(stream=data, filetype="pdf")
        parts: list[str] = []
        for page_index in range(min(document.page_count, settings.ocr_max_pdf_pages)):
            page = document.load_page(page_index)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            text = _extract_image_ocr_text(pixmap.tobytes("png"))
            if text:
                parts.append(text)
        return "\n".join(parts)
    except Exception:
        return ""


def _extract_image_ocr_text(data: bytes) -> str:
    if not settings.ocr_enabled:
        return "（附件为图片，已下载但无法自动提取文字。请展开附件查看。）"
    try:
        from PIL import Image, ImageOps
        import pytesseract
    except Exception:
        return ""
    try:
        image = Image.open(BytesIO(data))
        image = ImageOps.grayscale(image)
        text = pytesseract.image_to_string(image, lang=settings.ocr_languages)
        return " ".join(text.split())
    except Exception:
        return ""


def _extract_docx_text(data: bytes) -> str:
    try:
        with ZipFile(BytesIO(data)) as archive:
            document_xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(document_xml)
        texts = [node.text for node in root.iter() if node.tag.endswith("}t") and node.text]
        return "\n".join(text.strip() for text in texts if text.strip())
    except Exception:
        return ""


def _extract_xlsx_text(data: bytes) -> str:
    try:
        with ZipFile(BytesIO(data)) as archive:
            shared_strings = _xlsx_shared_strings(archive)
            sheet_names = [
                name
                for name in archive.namelist()
                if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
            ]
            lines: list[str] = []
            for sheet_index, sheet_name in enumerate(sheet_names[:3], start=1):
                lines.append(f"[Sheet{sheet_index}]")
                sheet_root = ElementTree.fromstring(archive.read(sheet_name))
                for row in list(sheet_root.iter())[:600]:
                    if not row.tag.endswith("}row"):
                        continue
                    values = _xlsx_row_values(row, shared_strings)
                    if values:
                        lines.append(" | ".join(values))
                    if len(lines) >= 80:
                        break
            return "\n".join(lines)
    except Exception:
        return ""


def _xlsx_shared_strings(archive: ZipFile) -> list[str]:
    try:
        root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    except Exception:
        return []
    strings: list[str] = []
    for item in root.iter():
        if not item.tag.endswith("}si"):
            continue
        parts = [node.text or "" for node in item.iter() if node.tag.endswith("}t")]
        strings.append("".join(parts))
    return strings


def _xlsx_row_values(row: ElementTree.Element, shared_strings: list[str]) -> list[str]:
    values: list[str] = []
    for cell in row:
        if not cell.tag.endswith("}c"):
            continue
        cell_type = cell.attrib.get("t")
        raw_value = None
        for child in cell:
            if child.tag.endswith("}v"):
                raw_value = child.text
                break
            if child.tag.endswith("}is"):
                raw_value = "".join(node.text or "" for node in child.iter() if node.tag.endswith("}t"))
                break
        if raw_value in (None, ""):
            continue
        if cell_type == "s":
            try:
                value = shared_strings[int(raw_value)]
            except Exception:
                value = raw_value
        else:
            value = raw_value
        if value:
            values.append(str(value).strip())
    return [value for value in values if value]
