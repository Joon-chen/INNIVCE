from typing import Any

from app.models.entities import FeishuAppConfig
from app.services.feishu.authorization_cards import build_user_identity_authorization_card
from app.services.feishu.client import FeishuClient
from app.services.gateway.responder import send_feishu_interactive_reply


async def send_user_identity_authorization_card(
    app_config: FeishuAppConfig,
    identity: Any,
    reply_target: dict[str, str],
    *,
    answer: str,
    actions: list[dict[str, Any]],
) -> bool:
    if not actions:
        return False
    card = build_user_identity_authorization_card(
        answer=answer,
        actions=actions,
        actor_open_id=getattr(identity, "open_id", None),
    )
    try:
        await send_feishu_interactive_reply(
            app_config=app_config,
            reply_target=reply_target,
            card=card,
            client_factory=FeishuClient,
        )
    except Exception:
        return False
    return True


def authorization_actions_from_trace(trace_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(trace_payload, dict):
        return []
    steps = trace_payload.get("steps")
    if not isinstance(steps, list):
        return []
    for step in steps:
        if not isinstance(step, dict) or step.get("kind") != "tool":
            continue
        metadata = step.get("metadata") if isinstance(step.get("metadata"), dict) else {}
        structured = metadata.get("structured_result") if isinstance(metadata.get("structured_result"), dict) else {}
        actions = structured.get("authorization_actions")
        if isinstance(actions, list) and actions:
            return [item for item in actions if isinstance(item, dict)]
    return []
