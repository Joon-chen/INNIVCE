from typing import Any
from urllib.parse import quote

from app.models.entities import FeishuAppConfig
from app.services.feishu.client import FeishuClient


class FeishuDriveService:
    """Native Feishu Drive/Docs/Wiki capability for V5 knowledge resources."""

    def __init__(self, app_config: FeishuAppConfig, *, client: FeishuClient | None = None) -> None:
        self.app_config = app_config
        self.client = client or FeishuClient(app_config)

    async def list_files(
        self,
        *,
        page_size: int = 50,
        page_token: str | None = None,
        folder_token: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page_size": min(max(page_size, 1), 100)}
        if page_token:
            params["page_token"] = page_token
        if folder_token:
            params["folder_token"] = folder_token
        return await self.client.api_get("/open-apis/drive/v1/files", params=params)

    async def get_document_content(self, *, document_id: str, document_type: str = "docx") -> dict[str, Any]:
        normalized_type = document_type.lower().strip()
        paths = {
            "docx": f"/open-apis/docx/v1/documents/{quote(document_id, safe='')}/raw_content",
            "document": f"/open-apis/docx/v1/documents/{quote(document_id, safe='')}/raw_content",
            "doc": f"/open-apis/doc/v2/{quote(document_id, safe='')}/content",
        }
        path = paths.get(normalized_type)
        if not path:
            return {"available": False, "error": "document_type must be docx, document, or doc"}
        body = await self.client.api_get(path)
        data = body.get("data") or {}
        return {
            "available": True,
            "document_id": document_id,
            "document_type": normalized_type,
            "content_text": _document_text(data),
            "body": body,
        }

    async def list_wiki_spaces(
        self,
        *,
        page_size: int = 20,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page_size": min(max(page_size, 1), 50)}
        if page_token:
            params["page_token"] = page_token
        return await self.client.api_get("/open-apis/wiki/v2/spaces", params=params)

    async def list_wiki_nodes(
        self,
        *,
        space_id: str,
        parent_node_token: str | None = None,
        page_size: int = 50,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page_size": min(max(page_size, 1), 50)}
        if parent_node_token:
            params["parent_node_token"] = parent_node_token
        if page_token:
            params["page_token"] = page_token
        return await self.client.api_get(
            f"/open-apis/wiki/v2/spaces/{quote(space_id, safe='')}/nodes",
            params=params,
        )


def _document_text(data: dict[str, Any]) -> str:
    text = data.get("content") or data.get("raw_content") or data.get("text")
    if text is None:
        return ""
    return str(text)
