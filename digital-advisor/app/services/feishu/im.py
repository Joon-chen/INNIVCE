import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException

from app.models.entities import FeishuAppConfig
from app.services.feishu.client import FeishuClient


class FeishuImService:
    """Native Feishu IM capability for V5 Resources and Work Events."""

    def __init__(self, app_config: FeishuAppConfig, *, client: FeishuClient | None = None) -> None:
        self.app_config = app_config
        self.client = client or FeishuClient(app_config)

    async def search_chats(
        self,
        *,
        query: str,
        limit: int = 20,
        max_pages: int = 2,
    ) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        page_token: str | None = None
        for _ in range(max_pages):
            params = {"query": query, "page_size": min(max(limit - len(items), 1), 50)}
            if page_token:
                params["page_token"] = page_token
            try:
                body = await self.client.api_get("/open-apis/im/v1/chats/search", params=params)
            except HTTPException as exc:
                errors.append({"kind": "chat_search", "error": _safe_error(exc.detail)})
                break
            data = body.get("data") or {}
            items.extend(extract_chat_items(data))
            page_token = data.get("page_token")
            if len(items) >= limit or not data.get("has_more") or not page_token:
                break
        return {"items": dedupe_chat_candidates(items)[:limit], "errors": errors}

    async def join_chat_as_bot(self, chat_id: str) -> dict[str, Any]:
        path = f"/open-apis/im/v1/chats/{quote(chat_id, safe='')}/members/me_join"
        try:
            return {"ok": True, "body": await self.client.api_post(path, {})}
        except HTTPException as post_exc:
            status_code = _error_status_code(post_exc.detail)
            if status_code not in {404, 405}:
                return {"ok": False, "error": _safe_error(post_exc.detail)}
        try:
            return {"ok": True, "body": await self.client.api_patch(path, {})}
        except HTTPException as patch_exc:
            return {"ok": False, "error": _safe_error(patch_exc.detail)}

    async def create_chat(
        self,
        *,
        name: str,
        description: str | None = None,
        user_ids: list[str] | None = None,
        bot_ids: list[str] | None = None,
        user_id_type: str = "open_id",
        chat_mode: str = "group",
        chat_type: str = "private",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": name,
            "user_id_list": [item for item in user_ids or [] if item],
            "bot_id_list": [item for item in bot_ids or [] if item],
            "chat_mode": chat_mode,
            "chat_type": chat_type,
        }
        if description:
            payload["description"] = description
        return await self.client.api_post(f"/open-apis/im/v1/chats?user_id_type={quote(user_id_type, safe='')}", payload)

    async def send_text_message(
        self,
        *,
        receive_id_type: str,
        receive_id: str,
        text: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "content": json.dumps({"text": text}, ensure_ascii=False, separators=(",", ":")),
            "msg_type": "text",
            "receive_id": receive_id,
        }
        if idempotency_key:
            payload["uuid"] = idempotency_key
        return await self.client.api_post(f"/open-apis/im/v1/messages?receive_id_type={receive_id_type}", payload)

    async def preview_public_chats(
        self,
        *,
        query: str | None = None,
        chat_ids: list[str] | None = None,
        limit: int = 20,
        max_pages: int = 2,
    ) -> dict[str, Any]:
        candidates: list[dict[str, Any]] = [{"chat_id": chat_id, "name": chat_id} for chat_id in chat_ids or []]
        search_errors: list[dict[str, str]] = []
        if query and query.strip():
            search_result = await self.search_chats(query=query.strip(), limit=limit, max_pages=max_pages)
            candidates.extend(search_result["items"])
            search_errors = search_result["errors"]

        deduped = dedupe_chat_candidates(candidates)[:limit]
        return {
            "ok": True,
            "dry_run": True,
            "candidate_count": len(deduped),
            "candidates": deduped,
            "joined": [],
            "errors": search_errors,
            "next_step": "确认候选群无误后，通过 Tool Router 的 confirmed 路径执行加入。",
        }

    async def auto_join_public_chats(
        self,
        *,
        query: str | None = None,
        chat_ids: list[str] | None = None,
        limit: int = 20,
        max_pages: int = 2,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        preview = await self.preview_public_chats(query=query, chat_ids=chat_ids, limit=limit, max_pages=max_pages)
        if dry_run:
            return preview

        deduped = preview["candidates"]
        joined: list[dict[str, Any]] = []
        errors = list(preview["errors"])
        for chat in deduped:
            chat_id = str(chat.get("chat_id") or "").strip()
            if not chat_id:
                continue
            result = await self.join_chat_as_bot(chat_id)
            if result.get("ok"):
                joined.append({"chat_id": chat_id, "name": chat.get("name"), "chat": chat, "body": result.get("body")})
            else:
                errors.append({"chat_id": chat_id, "name": chat.get("name"), "error": result.get("error")})
        return {
            "ok": len(errors) == 0,
            "dry_run": False,
            "candidate_count": len(deduped),
            "joined_count": len(joined),
            "joined": joined,
            "errors": errors,
        }

    async def list_messages(
        self,
        *,
        chat_id: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        page_size: int = 20,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        end = end_time or datetime.now(UTC)
        start = start_time or (end - timedelta(days=1))
        return await self.client.list_messages(
            container_id_type="chat",
            container_id=chat_id,
            start_time=int(start.timestamp()),
            end_time=int(end.timestamp()),
            page_size=min(max(page_size, 1), 50),
            page_token=page_token,
        )


def extract_chat_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("items", "chats", "chat_list"):
        value = data.get(key)
        if isinstance(value, list):
            return [normalize_chat_item(item) for item in value if isinstance(item, dict)]
    return []


def normalize_chat_item(item: dict[str, Any]) -> dict[str, Any]:
    chat_id = str(item.get("chat_id") or item.get("open_chat_id") or item.get("id") or "")
    return {
        "chat_id": chat_id,
        "name": item.get("name") or item.get("chat_name") or chat_id,
        "description": item.get("description"),
        "chat": item,
    }


def dedupe_chat_candidates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    results: list[dict[str, Any]] = []
    for item in items:
        chat_id = str(item.get("chat_id") or "").strip()
        if not chat_id or chat_id in seen:
            continue
        seen.add(chat_id)
        results.append(item)
    return results


def _safe_error(detail: Any) -> str:
    return str(detail)[:500]


def _error_status_code(detail: Any) -> int | None:
    if isinstance(detail, dict):
        value = detail.get("status_code")
        if isinstance(value, int):
            return value
    return None
