import asyncio
import json
import logging
import os
import signal
import threading
import time
from typing import Any
from uuid import UUID

import redis
from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.entities import FeishuAppConfig
from app.services.feishu import handle_feishu_command_result, ingest_feishu_event
from app.services.feishu.approval_card_entrypoint import handle_feishu_gateway_card_action_response
from app.services.gateway.audit import write_gateway_message_audit
from app.services.gateway.feishu import build_feishu_gateway_message
from app.services.gateway.message import GatewayMessageKind


logger = logging.getLogger(__name__)
stop_event = threading.Event()
redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
_last_event_time: float = time.time()
_ws_watchdog_lock = threading.Lock()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not settings.feishu_ws_enabled:
        logger.info("Feishu WebSocket worker disabled.")
        _sleep_until_stopped()
        return

    try:
        import lark_oapi as lark
    except ImportError as exc:
        logger.error("lark-oapi is not installed.")
        raise SystemExit(1) from exc

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    app_configs = _load_app_configs()
    if not app_configs:
        logger.warning("No active Feishu app config found.")
        _sleep_until_stopped()
        return

    threads = []
    for app_config in app_configs:
        thread = threading.Thread(
            target=_run_client,
            args=(lark, app_config.id, app_config.app_id, app_config.app_secret),
            daemon=True,
        )
        thread.start()
        threads.append(thread)

    if threads:
        watchdog = threading.Thread(target=_ws_watchdog_loop, daemon=True)
        watchdog.start()
        logger.info("Started Feishu WebSocket watchdog.")

    logger.info("Started %s Feishu WebSocket client(s).", len(threads))
    _sleep_until_stopped()


def _run_client(lark, app_config_id, app_id, app_secret):
    while not stop_event.is_set():
        try:
            _run_client_once(lark, app_config_id, app_id, app_secret)
        except Exception:
            logger.exception("Feishu WebSocket error, will reconnect...")
        if not stop_event.is_set():
            time.sleep(3)


def _run_client_once(lark, app_config_id, app_id, app_secret):
    logger.info("Starting Feishu WebSocket client for app_config_id=%s", app_config_id)

    def on_message(data):
        global _last_event_time
        with _ws_watchdog_lock:
            _last_event_time = time.time()
        payload = _event_to_payload(lark, data)
        logger.info("Feishu event received: %s", _event_summary(payload))
        threading.Thread(target=_ingest_and_handle, args=(app_config_id, payload), daemon=True).start()

    def on_card_action(data):
        payload = _event_to_payload(lark, data)
        threading.Thread(target=_run_card_action_background, args=(app_config_id, payload), daemon=True).start()
        try:
            from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse
            return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "已收到，正在处理。"}})
        except Exception:
            logger.exception("Failed to build Feishu card action response.")
            return None

    builder = lark.EventDispatcherHandler.builder("", "")
    builder.register_p2_im_message_receive_v1(on_message)
    builder.register_p2_im_message_message_read_v1(_ignore_event)
    if hasattr(builder, "register_p2_card_action_trigger"):
        builder.register_p2_card_action_trigger(on_card_action)
    _register_optional_events(builder, on_message)
    event_handler = builder.build()

    client = lark.ws.Client(
        app_id, app_secret, event_handler=event_handler,
        log_level=getattr(lark.LogLevel, "WARNING", None),
    )
    client.start()
    logger.info("Feishu WebSocket client start returned -- connection lost.")


def _run_card_action_background(app_config_id, payload):
    try:
        _ingest_and_handle_card_action(app_config_id, payload)
    except Exception:
        logger.exception("Failed to handle Feishu card action in background.")


def _ws_watchdog_loop():
    global _last_event_time
    while not stop_event.is_set():
        threading.Event().wait(30)
        if stop_event.is_set():
            break
        with _ws_watchdog_lock:
            elapsed = time.time() - _last_event_time
        if elapsed > 300:
            logger.warning("Watchdog: no events for %.0f seconds, restarting worker.", elapsed)
            os._exit(1)
        if elapsed > 90:
            logger.warning("Watchdog: no events for %.0f seconds.", elapsed)


def _register_optional_events(builder, handler):
    methods = [
        "register_p2_im_chat_updated_v1", "register_p2_im_chat_member_user_added_v1",
        "register_p2_im_chat_member_user_deleted_v1", "register_p2_contact_user_created_v3",
        "register_p2_contact_user_updated_v3", "register_p2_contact_user_deleted_v3",
        "register_p2_contact_department_created_v3", "register_p2_contact_department_updated_v3",
        "register_p2_contact_department_deleted_v3", "register_p2_calendar_calendar_event_changed_v4",
        "register_p2_approval_approval_updated_v4", "register_p2_drive_file_created_in_folder_v1",
        "register_p2_drive_file_edit_v1", "register_p2_drive_file_title_updated_v1",
        "register_p2_drive_file_deleted_v1", "register_p2_drive_file_bitable_record_changed_v1",
        "register_p2_drive_file_bitable_field_changed_v1", "register_p2_task_task_updated_v1",
        "register_p2_task_task_update_tenant_v1", "register_p2_task_task_update_user_access_v2",
        "register_p2_mail_user_mailbox_event_message_received_v1", "register_p2_vc_meeting_meeting_started_v1",
        "register_p2_vc_meeting_meeting_ended_v1", "register_p2_vc_meeting_all_meeting_started_v1",
        "register_p2_vc_meeting_all_meeting_ended_v1", "register_p2_vc_meeting_recording_ready_v1",
    ]
    count = 0
    for name in methods:
        fn = getattr(builder, name, None)
        if not fn:
            continue
        try:
            fn(handler)
            count += 1
        except Exception:
            logger.exception("Failed to register event: %s", name)
    logger.info("Registered %s optional Feishu event handler(s).", count)


def _ignore_event(data):
    logger.info("Ignored Feishu event: %s", type(data).__name__)


def _event_to_payload(lark, data):
    marshalled = lark.JSON.marshal(data)
    if isinstance(marshalled, str):
        return json.loads(marshalled)
    return marshalled


def _sleep_until_stopped():
    stop_event.wait()


def _handle_stop(signum, frame):
    logger.info("Stopping Feishu WebSocket worker.")
    stop_event.set()


def _load_app_configs():
    db = SessionLocal()
    try:
        stmt = select(FeishuAppConfig).where(FeishuAppConfig.is_active == True)
        return list(db.scalars(stmt).all())
    finally:
        db.close()


def _ingest_and_handle(app_config_id, payload):
    try:
        _handle_command(app_config_id, payload)
    except Exception:
        logger.exception("Feishu _ingest_and_handle failed")


def _handle_command(app_config_id, payload):
    db = SessionLocal()
    try:
        app_config = db.get(FeishuAppConfig, app_config_id)
        if app_config is None:
            return False
        result = asyncio.run(handle_feishu_command_result(db, app_config, payload))
        logger.info(
            "Feishu command handled: handled=%s status=%s route=%s reason=%s",
            result.handled,
            result.status,
            result.route_path,
            result.reason,
        )
        return result.handled
    finally:
        db.close()


def _event_summary(payload: dict[str, Any]) -> dict[str, Any]:
    header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    return {
        "event_type": header.get("event_type") or payload.get("type"),
        "message_id": message.get("message_id"),
        "chat_id": message.get("chat_id"),
        "sender_type": (sender.get("sender_id") or {}).get("user_id_type") if isinstance(sender.get("sender_id"), dict) else None,
    }


def _ingest_and_handle_card_action(app_config_id, payload):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _ingest_and_handle_card_action_sync(app_config_id, payload)

    result: dict[str, Any] = {}

    def _runner():
        try:
            result["value"] = _ingest_and_handle_card_action_sync(app_config_id, payload)
        except Exception as exc:
            result["error"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


def _ingest_and_handle_card_action_sync(app_config_id, payload):
    db = SessionLocal()
    try:
        app_config = db.get(FeishuAppConfig, app_config_id)
        if app_config is None:
            return None
        ingest_feishu_event(db, app_config, payload)
        return asyncio.run(handle_feishu_gateway_card_action_response(db, app_config, payload))
    finally:
        db.close()


if __name__ == "__main__":
    main()
