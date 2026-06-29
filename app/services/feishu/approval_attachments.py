from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from fastapi import HTTPException

from app.core.config import settings
from app.models.entities import FeishuAppConfig
from app.services.feishu.client import FeishuClient
from app.services.file_text_extraction import extract_file_text


MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024


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
    return extract_file_text(data, filename=filename, content_type=content_type)


def _safe_error(detail: Any) -> str:
    return str(detail)[:300]
