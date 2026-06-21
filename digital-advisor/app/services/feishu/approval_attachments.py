from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse
from xml.etree import ElementTree
from zipfile import ZipFile

import httpx
from fastapi import HTTPException

from app.core.config import settings
from app.models.entities import FeishuAppConfig
from app.services.feishu.client import FeishuClient


MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
TEXT_PREVIEW_CHARS = 1200
OCR_MIN_TEXT_CHARS = 20


@dataclass(frozen=True)
class ApprovalAttachmentReadResult:
    name: str
    token: str
    content_type: str | None = None
    storage_bucket: str | None = None
    storage_key: str | None = None
    text_preview: str = ""
    error: str | None = None

    @property
    def fetched(self) -> bool:
        return self.error is None

    @property
    def downloaded(self) -> bool:
        return self.error is None and bool(self.storage_key)


class FeishuApprovalAttachmentService:
    """Reads approval attachment references through Feishu APIs.

    This stays in services/feishu because it calls Feishu Raw HTTP. It keeps
    raw bytes in memory only and returns structured summaries for local storage.
    """

    def __init__(
        self,
        app_config: FeishuAppConfig,
        *,
        client: FeishuClient | None = None,
        store: Any | None = None,
    ) -> None:
        self.app_config = app_config
        self.client = client or FeishuClient(app_config)

    async def read_attachment_refs(
        self,
        refs: list[dict[str, str]],
        *,
        instance_code: str | None = None,
        max_files: int = 3,
    ) -> list[ApprovalAttachmentReadResult]:
        results: list[ApprovalAttachmentReadResult] = []
        for ref in refs[:max_files]:
            results.append(await self.read_attachment_ref(ref, instance_code=instance_code))
        return results

    async def read_attachment_ref(
        self,
        ref: dict[str, str],
        *,
        instance_code: str | None = None,
    ) -> ApprovalAttachmentReadResult:
        token = str(ref.get("token") or "").strip()
        url = str(ref.get("url") or "").strip()
        name = str(ref.get("name") or ref.get("field_name") or token or "审批附件").strip()
        if not token and not url:
            return ApprovalAttachmentReadResult(name=name, token="", error="附件没有返回可下载 token 或 url")

        try:
            data, content_type = await self._download_by_token(token) if token else await self._download_by_url(url)
        except HTTPException as exc:
            return ApprovalAttachmentReadResult(name=name, token=token, error=_safe_error(exc.detail))
        except Exception as exc:
            return ApprovalAttachmentReadResult(name=name, token=token, error=str(exc)[:300])

        if len(data) > MAX_ATTACHMENT_BYTES:
            return ApprovalAttachmentReadResult(name=name, token=token, error="附件超过当前自动读取大小限制")

        text_preview = extract_attachment_text(data, filename=name, content_type=content_type)
        return ApprovalAttachmentReadResult(
            name=name,
            token=token,
            content_type=content_type,
            text_preview=text_preview,
        )

    async def _download_by_url(self, url: str) -> tuple[bytes, str | None]:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        allowed_suffixes = ("feishu.cn", "larksuite.com")
        if not parsed.scheme.startswith("http") or not any(host == suffix or host.endswith(f".{suffix}") for suffix in allowed_suffixes):
            raise HTTPException(status_code=400, detail="不允许下载非飞书域名附件")
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds, follow_redirects=True) as client:
            response = await client.get(url)
        if response.status_code >= 400:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"附件 URL 下载失败 HTTP {response.status_code}: {response.text[:200]}",
            )
        return response.content, response.headers.get("content-type")

    async def _download_by_token(self, token: str) -> tuple[bytes, str | None]:
        quoted = quote(token, safe="")
        paths = (
            f"/open-apis/drive/v1/medias/{quoted}/download",
            f"/open-apis/drive/v1/files/{quoted}/download",
            f"/open-apis/approval/v4/files/{quoted}/download",
        )
        last_error: HTTPException | None = None
        for path in paths:
            try:
                return await self.client.download_binary(path)
            except HTTPException as exc:
                last_error = exc
        if last_error:
            raise last_error
        raise RuntimeError("没有可用下载路径")


def extract_attachment_text(data: bytes, *, filename: str, content_type: str | None = None) -> str:
    suffix = Path(filename).suffix.lower()
    content_type = (content_type or "").lower()
    if _looks_like_text(suffix, content_type):
        return _decode_text(data)[:TEXT_PREVIEW_CHARS]
    if suffix == ".pdf" or "pdf" in content_type:
        return _extract_pdf_text(data)[:TEXT_PREVIEW_CHARS]
    if _looks_like_image(suffix, content_type):
        return _extract_image_ocr_text(data)[:TEXT_PREVIEW_CHARS]
    if suffix in {".docx", ".doc"} or "word" in content_type:
        return _extract_docx_text(data)[:TEXT_PREVIEW_CHARS]
    if suffix in {".xlsx", ".xlsm", ".xls"} or "spreadsheet" in content_type or "excel" in content_type:
        return _extract_xlsx_text(data)[:TEXT_PREVIEW_CHARS]
    if suffix == ".csv":
        return _decode_text(data)[:TEXT_PREVIEW_CHARS]
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


def _safe_error(detail: Any) -> str:
    return str(detail)[:300]
