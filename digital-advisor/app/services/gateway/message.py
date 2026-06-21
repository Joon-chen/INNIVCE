from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class GatewayPlatform(StrEnum):
    FEISHU = "feishu"


class GatewayMessageKind(StrEnum):
    MESSAGE = "message"
    CARD_ACTION = "card_action"
    EVENT = "event"


@dataclass(frozen=True)
class GatewayActor:
    open_id: str | None = None
    user_id: str | None = None
    union_id: str | None = None
    sender_type: str | None = None


@dataclass(frozen=True)
class GatewayReplyTarget:
    receive_id_type: str
    receive_id: str

    def as_dict(self) -> dict[str, str]:
        return {"receive_id_type": self.receive_id_type, "receive_id": self.receive_id}


@dataclass(frozen=True)
class GatewayContext:
    event_id: str | None = None
    event_type: str | None = None
    app_id: str | None = None
    tenant_key: str | None = None
    chat_id: str | None = None
    chat_type: str | None = None
    message_id: str | None = None
    message_type: str | None = None
    mentions: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def is_group_chat(self) -> bool:
        if self.chat_type:
            return self.chat_type.lower() not in {"p2p", "private", "single", "direct"}
        return bool(self.chat_id and self.chat_id.startswith("oc_"))


@dataclass(frozen=True)
class GatewayMessage:
    platform: GatewayPlatform
    kind: GatewayMessageKind
    text: str | None
    actor: GatewayActor
    context: GatewayContext
    reply_target: GatewayReplyTarget | None
    raw_payload: dict[str, Any]
    is_from_app: bool = False

