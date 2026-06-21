from typing import Any, Protocol

from app.services.gateway.message import GatewayReplyTarget


class FeishuMessageClient(Protocol):
    async def send_message(
        self,
        *,
        receive_id_type: str,
        receive_id: str,
        msg_type: str,
        content: dict[str, Any],
    ) -> dict[str, Any]: ...

    async def update_message_content(self, *, message_id: str, content: dict[str, Any] | str) -> dict[str, Any]: ...


def reply_target_payload(reply_target: GatewayReplyTarget | dict[str, Any]) -> dict[str, str]:
    if isinstance(reply_target, GatewayReplyTarget):
        return reply_target.as_dict()
    return {
        "receive_id_type": str(reply_target.get("receive_id_type") or ""),
        "receive_id": str(reply_target.get("receive_id") or ""),
    }


async def send_feishu_text_reply(
    *,
    app_config: Any,
    reply_target: GatewayReplyTarget | dict[str, Any],
    text: str,
    client_factory: Any,
    max_chars: int = 3500,
) -> dict[str, Any]:
    target = reply_target_payload(reply_target)
    client: FeishuMessageClient = client_factory(app_config)
    return await client.send_message(
        receive_id_type=target["receive_id_type"],
        receive_id=target["receive_id"],
        msg_type="text",
        content={"text": text[:max_chars]},
    )


async def send_feishu_interactive_reply(
    *,
    app_config: Any,
    reply_target: GatewayReplyTarget | dict[str, Any],
    card: dict[str, Any],
    client_factory: Any,
) -> dict[str, Any]:
    target = reply_target_payload(reply_target)
    client: FeishuMessageClient = client_factory(app_config)
    return await client.send_message(
        receive_id_type=target["receive_id_type"],
        receive_id=target["receive_id"],
        msg_type="interactive",
        content=card,
    )


async def update_feishu_message_content(
    *,
    app_config: Any,
    message_id: str,
    content: dict[str, Any] | str,
    client_factory: Any,
) -> dict[str, Any]:
    client: FeishuMessageClient = client_factory(app_config)
    return await client.update_message_content(message_id=message_id, content=content)
