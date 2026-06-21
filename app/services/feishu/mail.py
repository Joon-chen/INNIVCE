import base64
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException

from app.models.entities import FeishuAppConfig
from app.services.feishu.client import FeishuClient


class FeishuMailService:
    """Native Feishu Mail capability for V5 communication resources."""

    def __init__(self, app_config: FeishuAppConfig | None, *, client: FeishuClient | None = None) -> None:
        self.app_config = app_config
        if client is not None:
            self.client = client
        elif app_config is not None:
            self.client = FeishuClient(app_config)
        else:
            raise ValueError("app_config or client is required")

    async def list_folders(self, *, user_mailbox_id: str) -> dict[str, Any]:
        mailbox = quote(user_mailbox_id, safe="")
        return await self.client.api_get(f"/open-apis/mail/v1/user_mailboxes/{mailbox}/folders")

    async def list_messages(
        self,
        *,
        user_mailbox_id: str,
        folder_id: str,
        page_size: int = 20,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        mailbox = quote(user_mailbox_id, safe="")
        params: dict[str, Any] = {
            "folder_id": folder_id,
            "page_size": min(max(page_size, 1), 20),
        }
        if page_token:
            params["page_token"] = page_token
        return await self.client.api_get(f"/open-apis/mail/v1/user_mailboxes/{mailbox}/messages", params=params)

    async def get_message_detail(
        self,
        *,
        user_mailbox_id: str,
        message_id: str,
        fmt: str = "plain_text_full",
    ) -> dict[str, Any]:
        mailbox = quote(user_mailbox_id, safe="")
        message = quote(message_id, safe="")
        return await self.client.api_get(
            f"/open-apis/mail/v1/user_mailboxes/{mailbox}/messages/{message}",
            params={"format": fmt},
        )

    async def get_message_detail_safe(
        self,
        *,
        user_mailbox_id: str,
        item: Any,
    ) -> dict[str, Any]:
        if isinstance(item, dict):
            message_id = first_present(item, ["message_id", "id"])
            base = item
        else:
            message_id = str(item)
            base = {"message_id": message_id}
        if not message_id:
            return base
        try:
            body = await self.get_message_detail(user_mailbox_id=user_mailbox_id, message_id=message_id)
        except HTTPException:
            return base
        data = body.get("data") or {}
        if isinstance(data, dict):
            return {**base, **data, "message_id": message_id}
        return base


def extract_mail_items(data: dict[str, Any]) -> list[Any]:
    value = data.get("items") or data.get("messages") or []
    return list(value) if isinstance(value, list) else []


def decode_mail_body(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return ""
    try:
        decoded = base64.urlsafe_b64decode(_pad_base64(value)).decode("utf-8", errors="replace")
        if decoded.strip():
            return decoded.strip()
    except Exception:
        return value
    return value


def first_present(data: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _pad_base64(value: str) -> bytes:
    cleaned = value.strip().replace("\n", "")
    return (cleaned + "=" * (-len(cleaned) % 4)).encode()
