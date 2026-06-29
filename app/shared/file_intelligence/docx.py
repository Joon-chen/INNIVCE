from __future__ import annotations

from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZipFile

from app.shared.file_intelligence.models import ExtractionResult


WORD_EXTENSIONS = {".docx", ".doc"}


def extract_docx(data: bytes, *, filename: str, mime_type: str | None, max_chars: int) -> ExtractionResult:
    try:
        with ZipFile(BytesIO(data)) as archive:
            document_xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(document_xml)
        texts = [node.text for node in root.iter() if node.tag.endswith("}t") and node.text]
        text = "\n".join(value.strip() for value in texts if value.strip())[:max_chars]
        return ExtractionResult(
            success=bool(text),
            text=text,
            mime_type=mime_type,
            filename=filename,
            extractor="docx",
        )
    except Exception as exc:
        return ExtractionResult(
            success=False,
            mime_type=mime_type,
            filename=filename,
            extractor="docx",
            error=str(exc)[:300],
        )
