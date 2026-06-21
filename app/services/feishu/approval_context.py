import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.models.entities import FeishuAppConfig
from app.services.feishu.client import FeishuClient

ClientFactory = Callable[[FeishuAppConfig], Any]


def approval_context_key(app_config: FeishuAppConfig, identity: Any, chat_id: str | None, suffix: str) -> str:
    actor = getattr(identity, "open_id", None) or "unknown"
    scope = chat_id or "direct"
    return f"feishu:bot:approval:{app_config.id}:{actor}:{scope}:{suffix}"


def store_approval_context(
    app_config: FeishuAppConfig,
    identity: Any,
    chat_id: str | None,
    items: list[dict[str, Any]],
    *,
    serialize_item: Callable[[dict[str, Any]], dict[str, Any]],
    client_factory: ClientFactory = FeishuClient,
) -> None:
    key = approval_context_key(app_config, identity, chat_id, "last")
    payload = {"items": [serialize_item(item) for item in items[:10]], "stored_at": datetime.now(UTC).isoformat()}
    client_factory(app_config).redis.setex(key, 900, json.dumps(payload, ensure_ascii=False, default=str))


def load_approval_context(
    app_config: FeishuAppConfig,
    identity: Any,
    chat_id: str | None,
    *,
    load_item: Callable[[dict[str, Any]], dict[str, Any]],
    client_factory: ClientFactory = FeishuClient,
) -> list[dict[str, Any]]:
    raw = client_factory(app_config).redis.get(approval_context_key(app_config, identity, chat_id, "last"))
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    return [load_item(item) for item in items if isinstance(item, dict)]


def clear_approval_context(
    app_config: FeishuAppConfig,
    identity: Any,
    chat_id: str | None,
    *,
    client_factory: ClientFactory = FeishuClient,
) -> None:
    client_factory(app_config).redis.delete(approval_context_key(app_config, identity, chat_id, "last"))


def store_pending_approval_action(
    app_config: FeishuAppConfig,
    identity: Any,
    chat_id: str | None,
    item: dict[str, Any],
    action: str,
    *,
    client_factory: ClientFactory = FeishuClient,
) -> None:
    key = approval_context_key(app_config, identity, chat_id, "pending_action")
    payload = {"action": action, "item": item, "stored_at": datetime.now(UTC).isoformat()}
    client_factory(app_config).redis.setex(key, 300, json.dumps(payload, ensure_ascii=False, default=str))


def load_pending_approval_action(
    app_config: FeishuAppConfig,
    identity: Any,
    chat_id: str | None,
    *,
    client_factory: ClientFactory = FeishuClient,
) -> dict[str, Any] | None:
    raw = client_factory(app_config).redis.get(approval_context_key(app_config, identity, chat_id, "pending_action"))
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def clear_pending_approval_action(
    app_config: FeishuAppConfig,
    identity: Any,
    chat_id: str | None,
    *,
    client_factory: ClientFactory = FeishuClient,
) -> None:
    client_factory(app_config).redis.delete(approval_context_key(app_config, identity, chat_id, "pending_action"))
