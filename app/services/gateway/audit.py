from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.entities import AuditLog
from app.services.audit import write_audit_log
from app.services.gateway.message import GatewayMessage


def write_gateway_message_audit(
    db: Session,
    *,
    company_id: UUID | None,
    message: GatewayMessage,
    status: str,
    reason: str | None = None,
    handled: bool | None = None,
    extra: dict[str, Any] | None = None,
) -> AuditLog:
    reply_target = message.reply_target.as_dict() if message.reply_target else None
    return write_audit_log(
        db,
        action=f"gateway.{message.platform.value}.{message.kind.value}",
        company_id=company_id,
        actor=message.actor.open_id or message.actor.user_id or message.actor.sender_type,
        target_type="gateway_message",
        target_id=message.context.message_id or message.context.event_id or message.context.chat_id,
        payload=_gateway_message_audit_payload(
            message,
            status=status,
            reason=reason,
            handled=handled,
            reply_target=reply_target,
            extra=extra,
        ),
    )


def _gateway_message_audit_payload(
    message: GatewayMessage,
    *,
    status: str,
    reason: str | None,
    handled: bool | None,
    reply_target: dict[str, Any] | None,
    extra: dict[str, Any] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "handled": handled,
        "platform": message.platform.value,
        "kind": message.kind.value,
        "event_id": message.context.event_id,
        "event_type": message.context.event_type,
        "app_id": message.context.app_id,
        "tenant_key": message.context.tenant_key,
        "chat_id": message.context.chat_id,
        "chat_type": message.context.chat_type,
        "message_id": message.context.message_id,
        "message_type": message.context.message_type,
        "is_group_chat": message.context.is_group_chat,
        "is_from_app": message.is_from_app,
        "has_text": bool(message.text),
        "reply_target": reply_target,
    }
    if extra:
        payload.update({key: value for key, value in extra.items() if value is not None})
    return {key: value for key, value in payload.items() if value is not None}
