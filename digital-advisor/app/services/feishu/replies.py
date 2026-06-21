from typing import Any

from app.models.entities import FeishuAppConfig
from app.services.feishu.client import FeishuClient
from app.services.gateway.message import GatewayReplyTarget
from app.services.gateway.responder import send_feishu_text_reply, send_feishu_interactive_reply
from app.services.gateway.card_renderer import (
    should_use_interactive_card,
    card_hint_for_route,
    build_interactive_card,
)


async def send_text_reply(
    *,
    app_config: FeishuAppConfig,
    reply_target: GatewayReplyTarget | dict[str, Any],
    text: str,
) -> dict[str, Any]:
    return await send_feishu_text_reply(
        app_config=app_config,
        reply_target=reply_target,
        text=text,
        client_factory=FeishuClient,
    )


async def send_interactive_reply(
    *,
    app_config: FeishuAppConfig,
    reply_target: GatewayReplyTarget | dict[str, Any],
    card: dict[str, Any],
) -> dict[str, Any]:
    return await send_feishu_interactive_reply(
        app_config=app_config,
        reply_target=reply_target,
        card=card,
        client_factory=FeishuClient,
    )


async def send_smart_reply(
    *,
    app_config: FeishuAppConfig,
    reply_target: GatewayReplyTarget | dict[str, Any],
    reply: str,
    route_path: str | None = None,
    chat_id: str | None = None,
) -> dict[str, Any]:
    """Route reply to text or interactive card based on reply content.

    If reply starts with valid JSON that has 'available' and 'departments' keys,
    render as contact card. Otherwise send as text.
    """
    import json as _json
    # Try to detect contact card from reply content
    _send_card = False
    _card_hint = None
    _json_part = reply.strip()
    _brace = _json_part.find("{")
    if _brace >= 0:
        _json_part = _json_part[_brace:]
    try:
        data = _json.loads(_json_part)
        if isinstance(data, dict) and "departments" in data:
            _card_hint = "contact"
            _send_card = True
    except (_json.JSONDecodeError, ValueError):
        pass
    if route_path and should_use_interactive_card(route_path, reply):
        _card_hint = card_hint_for_route(route_path)
        _send_card = True
    if _send_card and _card_hint and getattr(app_config, "app_secret", None):
        _raw_for_card = _json_part if _send_card else reply
        card = build_interactive_card(
            card_hint=_card_hint,
            route_path=route_path or "contact",
            raw_answer=_raw_for_card,
            chat_id=chat_id,
        )
        return await send_interactive_reply(
            app_config=app_config,
            reply_target=reply_target,
            card=card,
        )
    return await send_text_reply(
        app_config=app_config,
        reply_target=reply_target,
        text=reply,
    )
