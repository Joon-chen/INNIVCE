from __future__ import annotations

from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZipFile

from app.shared.file_intelligence.models import ExtractionResult


SPREADSHEET_EXTENSIONS = {".xlsx", ".xlsm", ".xls"}


def extract_xlsx(data: bytes, *, filename: str, mime_type: str | None, max_chars: int) -> ExtractionResult:
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
            text = "\n".join(lines)[:max_chars]
            return ExtractionResult(
                success=bool(text),
                text=text,
                mime_type=mime_type,
                filename=filename,
                extractor="xlsx",
            )
    except Exception as exc:
        return ExtractionResult(
            success=False,
            mime_type=mime_type,
            filename=filename,
            extractor="xlsx",
            error=str(exc)[:300],
        )


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
