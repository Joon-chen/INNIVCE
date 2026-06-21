from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from app.services.gateway.card_actions import parse_gateway_card_action


CardActionMessageHandler = Callable[[Any, Any, dict[str, Any]], Awaitable[bool]]
CardActionResponseHandler = Callable[[Any, Any, dict[str, Any]], Awaitable[dict[str, Any] | None]]


@dataclass(frozen=True)
class GatewayCardResponder:
    kind: str
    handle_message: CardActionMessageHandler
    handle_response: CardActionResponseHandler


async def dispatch_gateway_card_action_message(
    db: Any,
    app_config: Any,
    payload: dict[str, Any],
    *,
    responders: Iterable[GatewayCardResponder],
) -> bool:
    responder = _responder_for_payload(payload, responders)
    if responder is None:
        return False
    return await responder.handle_message(db, app_config, payload)


async def dispatch_gateway_card_action_response(
    db: Any,
    app_config: Any,
    payload: dict[str, Any],
    *,
    responders: Iterable[GatewayCardResponder],
) -> dict[str, Any] | None:
    responder = _responder_for_payload(payload, responders)
    if responder is None:
        return None
    return await responder.handle_response(db, app_config, payload)


def _responder_for_payload(
    payload: dict[str, Any],
    responders: Iterable[GatewayCardResponder],
) -> GatewayCardResponder | None:
    action = parse_gateway_card_action(payload)
    if action is None or not action.kind:
        return None
    for responder in responders:
        if responder.kind == action.kind:
            return responder
    return None
