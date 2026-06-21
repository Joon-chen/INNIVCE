import json
from typing import Any

from app.services.gateway.card_actions import parse_gateway_card_action
from app.services.gateway.commands import clean_command_text
from app.services.gateway.message import (
    GatewayActor,
    GatewayContext,
    GatewayMessage,
    GatewayMessageKind,
    GatewayPlatform,
    GatewayReplyTarget,
)


def build_feishu_gateway_message(payload: dict[str, Any]) -> GatewayMessage:
    """Convert Feishu callback or lark-cli event output into the V5 gateway shape.

    Field names are checked against `lark-cli event schema im.message.receive_v1`
    and Feishu Open Platform event callback documentation.
    """

    header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
    event = payload.get("event") if isinstance(payload.get("event"), dict) else payload
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    action = event.get("action") if isinstance(event.get("action"), dict) else payload.get("action")
    card_action = parse_gateway_card_action(payload)
    kind = GatewayMessageKind.CARD_ACTION if isinstance(action, dict) or card_action else _message_kind(header, event)
    actor = _actor_from_event(event, payload)
    context = GatewayContext(
        event_id=_first_string(header, event, payload, keys=("event_id",)),
        event_type=str(header.get("event_type") or event.get("type") or payload.get("type") or ""),
        app_id=str(header.get("app_id") or payload.get("app_id") or ""),
        tenant_key=str(header.get("tenant_key") or payload.get("tenant_key") or ""),
        chat_id=card_action.chat_id if card_action else _first_string(message, event, payload, keys=("chat_id",)),
        chat_type=_first_string(message, event, payload, keys=("chat_type",)),
        message_id=card_action.message_id if card_action else _first_string(message, event, payload, keys=("message_id", "id")),
        message_type=_first_string(message, event, payload, keys=("message_type",)),
        mentions=tuple(_mentions(message, event)),
    )
    return GatewayMessage(
        platform=GatewayPlatform.FEISHU,
        kind=kind,
        text=clean_command_text(_message_text(message, event, payload)),
        actor=actor,
        context=context,
        reply_target=_reply_target(actor, context),
        raw_payload=payload,
        is_from_app=_is_from_app(event, message),
    )


def should_reply_to_feishu_message(message: GatewayMessage, *, app_id: str, bot_names: set[str]) -> bool:
    if message.kind != GatewayMessageKind.MESSAGE or message.is_from_app:
        return False
    if not message.context.is_group_chat:
        return True
    return _mentions_bot(message.context.mentions, app_id=app_id, bot_names=bot_names)


def feishu_url_verification_challenge(payload: dict[str, Any]) -> str | None:
    event_type = (payload.get("header") or {}).get("event_type") or payload.get("type")
    challenge = payload.get("challenge")
    if event_type == "url_verification" or challenge:
        return str(challenge or "")
    return None


def _message_kind(header: dict[str, Any], event: dict[str, Any]) -> GatewayMessageKind:
    event_type = str(header.get("event_type") or event.get("type") or "")
    if event_type.startswith("im.message.") or event.get("message") or event.get("message_id"):
        return GatewayMessageKind.MESSAGE
    return GatewayMessageKind.EVENT


def _actor_from_event(event: dict[str, Any], payload: dict[str, Any]) -> GatewayActor:
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    sender_id = sender.get("sender_id") if isinstance(sender.get("sender_id"), dict) else sender.get("id")
    if not isinstance(sender_id, dict):
        sender_id = {}
    operator = event.get("operator") if isinstance(event.get("operator"), dict) else {}
    operator_id = operator.get("operator_id") if isinstance(operator.get("operator_id"), dict) else operator.get("id")
    if not isinstance(operator_id, dict):
        operator_id = {}
    user_id = event.get("user_id") if isinstance(event.get("user_id"), dict) else {}
    flat_sender_id = payload.get("sender_id") or event.get("sender_id")
    return GatewayActor(
        open_id=_first_nonempty(
            sender_id.get("open_id"),
            operator_id.get("open_id"),
            operator.get("open_id"),
            user_id.get("open_id"),
            flat_sender_id,
        ),
        user_id=_first_nonempty(
            sender_id.get("user_id"),
            operator_id.get("user_id"),
            operator.get("user_id"),
            user_id.get("user_id"),
        ),
        union_id=_first_nonempty(sender_id.get("union_id"), operator_id.get("union_id"), user_id.get("union_id")),
        sender_type=str(sender.get("sender_type") or event.get("sender_type") or ""),
    )


def _message_text(message: dict[str, Any], event: dict[str, Any], payload: dict[str, Any]) -> str | None:
    raw_text = _extract_message_text(message) or event.get("text") or event.get("content") or payload.get("content")
    if raw_text is None:
        return None
    if not isinstance(raw_text, str):
        return str(raw_text).strip()
    stripped = raw_text.strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped
    if isinstance(parsed, dict) and isinstance(parsed.get("text"), str):
        return parsed["text"].strip()
    return stripped


def _reply_target(actor: GatewayActor, context: GatewayContext) -> GatewayReplyTarget | None:
    if context.chat_id:
        return GatewayReplyTarget(receive_id_type="chat_id", receive_id=context.chat_id)
    if actor.open_id:
        return GatewayReplyTarget(receive_id_type="open_id", receive_id=actor.open_id)
    return None


def _mentions(message: dict[str, Any], event: dict[str, Any]) -> list[dict[str, Any]]:
    mentions = message.get("mentions") or event.get("mentions") or []
    return [item for item in mentions if isinstance(item, dict)] if isinstance(mentions, list) else []


def _mentions_bot(mentions: tuple[dict[str, Any], ...], *, app_id: str, bot_names: set[str]) -> bool:
    app_ids = {app_id}
    for mention in mentions:
        mention_id = mention.get("id") if isinstance(mention.get("id"), dict) else {}
        candidates = {
            str(mention.get("app_id") or ""),
            str(mention.get("bot_id") or ""),
            str(mention.get("name") or ""),
            str(mention.get("key") or ""),
            str(mention_id.get("app_id") or ""),
            str(mention_id.get("open_id") or ""),
            str((mention.get("name") or "").replace("@", "")),
        }
        if app_ids & candidates or bot_names & candidates:
            return True
    return False


def _is_from_app(event: dict[str, Any], message: dict[str, Any]) -> bool:
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    message_sender = message.get("sender") if isinstance(message.get("sender"), dict) else {}
    return sender.get("sender_type") == "app" or message_sender.get("sender_type") == "app"


def _extract_message_text(message: dict[str, Any]) -> str | None:
    content = message.get("content")
    if content is None and isinstance(message.get("body"), dict):
        content = message["body"].get("content")
    if isinstance(content, str):
        stripped = content.strip()
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return stripped
        if isinstance(parsed, dict) and isinstance(parsed.get("text"), str):
            return parsed["text"]
        return stripped
    if isinstance(content, dict):
        return content.get("text") or str(content)
    return None


def _first_string(*objects: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for obj in objects:
        for key in keys:
            value = obj.get(key)
            if value is not None and str(value):
                return str(value)
    return None


def _first_nonempty(*values: Any) -> str | None:
    for value in values:
        if value is not None and str(value):
            return str(value)
    return None
