from __future__ import annotations

from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZipFile

from app.shared.file_intelligence.models import ExtractionResult


PRESENTATION_EXTENSIONS = {".pptx"}


def extract_pptx(data: bytes, *, filename: str, mime_type: str | None, max_chars: int) -> ExtractionResult:
    try:
        with ZipFile(BytesIO(data)) as archive:
            slide_names = sorted(
                name
                for name in archive.namelist()
                if name.startswith("ppt/slides/slide") and name.endswith(".xml")
            )
            parts: list[str] = []
            for slide_index, slide_name in enumerate(slide_names[:20], start=1):
                root = ElementTree.fromstring(archive.read(slide_name))
                texts = [node.text for node in root.iter() if node.tag.endswith("}t") and node.text]
                if texts:
                    parts.append(f"[Slide{slide_index}] " + " ".join(value.strip() for value in texts if value.strip()))
            text = "\n".join(parts)[:max_chars]
            return ExtractionResult(
                success=bool(text),
                text=text,
                mime_type=mime_type,
                filename=filename,
                extractor="pptx",
            )
    except Exception as exc:
        return ExtractionResult(
            success=False,
            mime_type=mime_type,
            filename=filename,
            extractor="pptx",
            error=str(exc)[:300],
        )
