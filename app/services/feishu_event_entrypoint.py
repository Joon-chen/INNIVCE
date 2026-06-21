from typing import Any
import logging

from sqlalchemy.orm import Session

from app.models.entities import FeishuAppConfig
from app.services.feishu import handle_feishu_command_result, ingest_feishu_event, verify_feishu_token
from app.services.gateway.feishu import feishu_url_verification_challenge


logger = logging.getLogger(__name__)


async def receive_feishu_event_payload(
    db: Session,
    app_config: FeishuAppConfig,
    payload: dict[str, Any],
) -> dict[str, Any]:
    verify_feishu_token(app_config, payload)

    challenge = feishu_url_verification_challenge(payload)
    if challenge is not None:
        return {"challenge": challenge}

    event = ingest_feishu_event(db, app_config, payload)
    command_result = await handle_feishu_command_result(db, app_config, payload)
    logger.info(
        "Feishu HTTP event handled: event_id=%s handled=%s status=%s route=%s reason=%s",
        getattr(event, "external_event_id", None),
        getattr(command_result, "handled", False),
        getattr(command_result, "status", ""),
        getattr(command_result, "route_path", None),
        getattr(command_result, "reason", None),
    )
    return {
        "ok": True,
        "work_event_id": str(event.id),
        "command_handled": command_result.handled,
        "gateway_result": command_result.as_payload(),
    }
