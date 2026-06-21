import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GatewayCardAction:
    kind: str | None
    action: str | None
    value: dict[str, Any]
    message_id: str | None
    chat_id: str | None


def parse_gateway_card_action(payload: dict[str, Any]) -> GatewayCardAction | None:
    value = gateway_card_action_value(payload)
    if not value:
        return None
    event = _event(payload)
    return GatewayCardAction(
        kind=_optional_text(value.get("kind")),
        action=_optional_text(value.get("action")),
        value=value,
        message_id=gateway_card_action_message_id(payload),
        chat_id=_first_text(value.get("chat_id"), _nested_value(event, "message", "chat_id"), event.get("chat_id")),
    )


def gateway_card_action_value(payload: dict[str, Any]) -> dict[str, Any]:
    event = _event(payload)
    action = event.get("action") if isinstance(event.get("action"), dict) else payload.get("action")
    form_value = _form_value(payload)
    if isinstance(action, dict):
        value = _coerce_action_value(action.get("value") or action.get("option") or action.get("data"))
        if value:
            return {**value, **form_value, "form_value": form_value}
    action_info = event.get("action_info") if isinstance(event.get("action_info"), dict) else {}
    value = _coerce_action_value(event.get("value") or payload.get("value") or action_info.get("value"))
    return {**value, **form_value, "form_value": form_value} if value or form_value else {}


def gateway_card_action_message_id(payload: dict[str, Any]) -> str | None:
    event = _event(payload)
    action = event.get("action") if isinstance(event.get("action"), dict) else {}
    context = event.get("context") if isinstance(event.get("context"), dict) else {}
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    value = gateway_card_action_value(payload)
    return _first_text(
        value.get("message_id"),
        value.get("open_message_id"),
        action.get("message_id"),
        action.get("open_message_id"),
        event.get("message_id"),
        event.get("open_message_id"),
        context.get("message_id"),
        context.get("open_message_id"),
        message.get("message_id"),
        message.get("open_message_id"),
    )


def _event(payload: dict[str, Any]) -> dict[str, Any]:
    return payload.get("event") if isinstance(payload.get("event"), dict) else payload


def _coerce_action_value(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _first_text(*values: Any) -> str | None:
    for value in values:
        text = _optional_text(value)
        if text:
            return text
    return None


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _nested_value(value: dict[str, Any], key: str, nested_key: str) -> Any:
    nested = value.get(key) if isinstance(value.get(key), dict) else {}
    return nested.get(nested_key)


def _form_value(payload: dict[str, Any]) -> dict[str, Any]:
    event = _event(payload)
    action = event.get("action") if isinstance(event.get("action"), dict) else payload.get("action")
    candidates = []
    if isinstance(action, dict):
        candidates.extend([action.get("form_value"), action.get("input_values"), action.get("form_values")])
    candidates.extend([event.get("form_value"), event.get("input_values"), payload.get("form_value"), payload.get("input_values")])
    for candidate in candidates:
        if isinstance(candidate, dict):
            return candidate
    return {}
