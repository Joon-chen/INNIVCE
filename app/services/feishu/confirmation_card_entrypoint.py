from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models.entities import FeishuAppConfig
from app.services.feishu import command_parser
from app.services.feishu import identity as feishu_identity
from app.services.feishu import replies as feishu_replies
from app.services.feishu.cli_profile import feishu_app_cli_profile
from app.services.feishu.client import FeishuClient
from app.services.gateway.card_actions import parse_gateway_card_action
from app.services.gateway.card_renderer import build_interactive_card
from app.services.gateway.card_responder import GatewayCardResponder
from app.services.runtime_v5.action_observer import record_action_trace
from app.services.runtime_v5.context import build_runtime_context, load_result_context, load_session_context, save_result_context, save_session_context
from app.services.runtime_v5.feishu_resource_providers import build_feishu_provider_registry
from app.services.runtime_v5.models import ResultContext
from app.services.runtime_v5.runtime import run_runtime_v5
from app.services.runtime_v5.runtime_action_input import build_runtime_action_input_payload


_BATCH_SELECTION_KEY = "runtime_v5_approval_batch_selection"
_APPROVAL_WORKBENCH_COLLAPSED_KEY = "runtime_v5_approval_workbench_collapsed"
_APPROVAL_WORKBENCH_EXPANDED_GROUPS_KEY = "runtime_v5_approval_workbench_expanded_groups"
_PENDING_BATCH_KEY = "runtime_v5_pending_approval_batch"
_PENDING_SINGLE_KEY = "runtime_v5_pending_approval_single"
_CURRENT_APPROVAL_KEY = "runtime_v5_current_approval_item"
_APPROVAL_PENDING_TTL_SECONDS = 900


def feishu_runtime_confirmation_responder() -> GatewayCardResponder:
    return GatewayCardResponder(
        kind="runtime_confirmation",
        handle_message=handle_feishu_runtime_confirmation_message,
        handle_response=handle_feishu_runtime_confirmation_response,
    )


def feishu_runtime_approval_workbench_responder() -> GatewayCardResponder:
    return GatewayCardResponder(
        kind="runtime_approval_workbench",
        handle_message=handle_feishu_runtime_approval_workbench_message,
        handle_response=handle_feishu_runtime_approval_workbench_response,
    )


def feishu_runtime_approval_batch_confirm_responder() -> GatewayCardResponder:
    return GatewayCardResponder(
        kind="runtime_approval_batch_confirm",
        handle_message=handle_feishu_runtime_approval_batch_confirm_message,
        handle_response=handle_feishu_runtime_approval_batch_confirm_response,
    )


def feishu_runtime_approval_single_confirm_responder() -> GatewayCardResponder:
    return GatewayCardResponder(
        kind="runtime_approval_single_confirm",
        handle_message=handle_feishu_runtime_approval_single_confirm_message,
        handle_response=handle_feishu_runtime_approval_single_confirm_response,
    )


def feishu_runtime_approval_detail_action_responder() -> GatewayCardResponder:
    return GatewayCardResponder(
        kind="runtime_approval_detail_action",
        handle_message=handle_feishu_runtime_approval_detail_action_message,
        handle_response=handle_feishu_runtime_approval_detail_action_response,
    )


async def handle_feishu_runtime_confirmation_response(
    db: Session,
    app_config: FeishuAppConfig,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    handled, action = await _handle_runtime_confirmation(db, app_config, payload)
    if not handled:
        return {"toast": {"type": "warning", "content": "这个确认按钮已经失效，请重新发起操作。"}}
    if action == "cancel":
        return {"toast": {"type": "info", "content": "已取消，不会执行。"}}
    return {"toast": {"type": "info", "content": "已确认，正在执行。"}}


async def handle_feishu_runtime_confirmation_message(
    db: Session,
    app_config: FeishuAppConfig,
    payload: dict[str, Any],
) -> bool:
    handled, _action = await _handle_runtime_confirmation(db, app_config, payload)
    return handled


async def handle_feishu_runtime_approval_workbench_response(
    db: Session,
    app_config: FeishuAppConfig,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    chat_id = _card_action_chat_id(payload)
    handled, action = await _handle_runtime_approval_workbench(db, app_config, payload)
    if not handled:
        return {"toast": {"type": "warning", "content": "这个审批按钮已经失效，请重新查询待审批。"}}
    if action == "expired":
        return {"toast": {"type": "warning", "content": "这张卡片已失效，请重新查询待审批。"}}
    if action == "toggle_group":
        response: dict[str, Any] = {}
        card = _approval_workbench_update_card(chat_id)
        if card:
            response["card"] = card
        return response
    if action == "batch_approve_group":
        return {"toast": {"type": "info", "content": "正在生成低风险批量确认。"}}
    if action == "select":
        response: dict[str, Any] = {}
        card = _approval_workbench_update_card(chat_id)
        if card:
            response["card"] = card
        return response
    if action in {"clear_selection", "toggle_collapse"}:
        response = {}
        card = _approval_workbench_update_card(chat_id)
        if card:
            response["card"] = card
        return response
    if action == "batch_approve":
        return {"toast": {"type": "info", "content": "正在生成批量确认。"}}
    if action == "detail":
        return {"toast": {"type": "info", "content": "正在展开详情。"}}
    return {"toast": {"type": "info", "content": "已收到，等待你确认。"}}


async def handle_feishu_runtime_approval_workbench_message(
    db: Session,
    app_config: FeishuAppConfig,
    payload: dict[str, Any],
) -> bool:
    handled, _action = await _handle_runtime_approval_workbench(db, app_config, payload)
    return handled


async def handle_feishu_runtime_approval_batch_confirm_response(
    db: Session,
    app_config: FeishuAppConfig,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    handled, _action = await _handle_runtime_approval_batch_confirm(db, app_config, payload)
    if not handled:
        return {"toast": {"type": "warning", "content": "批量确认已过期，请重新选择。"}}
    return {"toast": {"type": "info", "content": "已收到，正在处理。"}}


async def handle_feishu_runtime_approval_batch_confirm_message(
    db: Session,
    app_config: FeishuAppConfig,
    payload: dict[str, Any],
) -> bool:
    handled, _action = await _handle_runtime_approval_batch_confirm(db, app_config, payload)
    return handled


async def handle_feishu_runtime_approval_single_confirm_response(
    db: Session,
    app_config: FeishuAppConfig,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    handled, action = await _handle_runtime_approval_single_confirm(db, app_config, payload)
    if not handled:
        return {"toast": {"type": "warning", "content": "审批确认已过期，请重新操作。"}}
    if action == "cancel":
        return {"toast": {"type": "info", "content": "已取消，不会提交。"}}
    return {"toast": {"type": "info", "content": "已确认，正在提交。"}}


async def handle_feishu_runtime_approval_single_confirm_message(
    db: Session,
    app_config: FeishuAppConfig,
    payload: dict[str, Any],
) -> bool:
    handled, _action = await _handle_runtime_approval_single_confirm(db, app_config, payload)
    return handled


async def handle_feishu_runtime_approval_detail_action_response(
    db: Session,
    app_config: FeishuAppConfig,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    handled, action = await _handle_runtime_approval_detail_action(db, app_config, payload)
    if not handled:
        return {"toast": {"type": "warning", "content": "这张详情卡已过期，请重新展开。"}}
    if action == "missing_reason":
        return {"toast": {"type": "warning", "content": "请直接回复：拒绝第几个，原因是..."}}
    if action == "missing_transfer":
        return {"toast": {"type": "warning", "content": "请直接回复：转交第几个给谁。"}}
    if action == "missing_add_sign":
        return {"toast": {"type": "warning", "content": "请直接回复：加签第几个给谁。"}}
    if action == "dismissed":
        return {"toast": {"type": "info", "content": "已收起，未处理。"}}
    return {"toast": {"type": "info", "content": "已收到，正在处理。"}}


async def handle_feishu_runtime_approval_detail_action_message(
    db: Session,
    app_config: FeishuAppConfig,
    payload: dict[str, Any],
) -> bool:
    handled, _action = await _handle_runtime_approval_detail_action(db, app_config, payload)
    return handled


async def _handle_runtime_confirmation(
    db: Session,
    app_config: FeishuAppConfig,
    payload: dict[str, Any],
) -> tuple[bool, str]:
    card_action = parse_gateway_card_action(payload)
    if card_action is None or card_action.kind != "runtime_confirmation":
        return False, ""
    action = str(card_action.action or card_action.value.get("action") or "").strip()
    if action not in {"confirm", "cancel"}:
        return False, action

    identity = feishu_identity.get_sender_identity(db, app_config, payload)
    chat_id = card_action.chat_id or str(card_action.value.get("chat_id") or "").strip() or None
    reply_target = _reply_target(chat_id=chat_id, open_id=identity.open_id)
    if not reply_target:
        return False, action
    pending = load_session_context(chat_id).get("runtime_v5_pending_action") if chat_id else None
    expected_id = str(pending.get("id") or "").strip() if isinstance(pending, dict) else ""
    action_id = str(card_action.value.get("pending_action_id") or card_action.value.get("confirmation_token") or "").strip()
    if not expected_id or action_id != expected_id:
        _record_action_trace(
            chat_id,
            {
                "kind": "runtime_confirmation",
                "action": action,
                "status": "stale",
                "action_id": action_id or expected_id,
                "expected_action_id": expected_id,
                "card_action_id": action_id,
                **_pending_action_trace_fields(pending if isinstance(pending, dict) else {}),
            },
        )
        _save_runtime_confirmation_receipt(
            chat_id=chat_id,
            status="stale",
            pending=pending if isinstance(pending, dict) else {},
            action_id=action_id or expected_id,
            answer="这个确认按钮已经失效，请重新发起操作。",
        )
        return False, action
    if isinstance(pending, dict) and _pending_action_expired(pending):
        _record_action_trace(
            chat_id,
            {
                "kind": "runtime_confirmation",
                "action": action,
                "status": "stale",
                "action_id": expected_id,
                "expected_action_id": expected_id,
                "card_action_id": action_id,
                **_pending_action_trace_fields(pending),
            },
        )
        _save_runtime_confirmation_receipt(
            chat_id=chat_id,
            status="stale",
            pending=pending,
            action_id=expected_id,
            answer="这个确认按钮已经过期，我没有继续执行。请重新发起操作。",
        )
        save_session_context(chat_id, _session_without_runtime_pending_action(load_session_context(chat_id)))
        await feishu_replies.send_text_reply(
            app_config=app_config,
            reply_target=reply_target,
            text="这个确认按钮已经过期，我没有继续执行。请重新发起操作。",
        )
        return False, action

    command = "取消" if action == "cancel" else "确认执行"
    _record_action_trace(
        chat_id,
        {
            "kind": "runtime_confirmation",
            "action": action,
            "status": "cancelled" if action == "cancel" else "queued",
            "action_id": expected_id,
            **_pending_action_trace_fields(pending if isinstance(pending, dict) else {}),
        },
    )
    if action == "cancel":
        _save_runtime_confirmation_receipt(
            chat_id=chat_id,
            status="cancelled",
            pending=pending if isinstance(pending, dict) else {},
            action_id=expected_id,
            answer="已取消，不会执行。",
        )
    else:
        _save_runtime_confirmation_receipt(
            chat_id=chat_id,
            status="queued",
            pending=pending if isinstance(pending, dict) else {},
            action_id=expected_id,
            answer="已确认，操作已进入执行队列。",
        )
    _save_runtime_confirmation_action_input(
        chat_id=chat_id,
        action=action,
        pending=pending if isinstance(pending, dict) else {},
        identity=identity,
        app_config=app_config,
    )
    _enqueue_runtime_card_reply(
        app_config=app_config,
        command=command,
        identity=identity,
        chat_id=chat_id,
        reply_target=reply_target,
    )
    return True, action


async def _handle_runtime_approval_detail_action(
    db: Session,
    app_config: FeishuAppConfig,
    payload: dict[str, Any],
) -> tuple[bool, str]:
    card_action = parse_gateway_card_action(payload)
    if card_action is None or card_action.kind != "runtime_approval_detail_action":
        return False, ""
    action = str(card_action.action or card_action.value.get("action") or "").strip()
    index = int(card_action.value.get("index") or 0)
    if action not in {"approve", "request_reject", "request_add_sign", "request_transfer", "dismiss"} or index <= 0:
        return False, action

    identity = feishu_identity.get_sender_identity(db, app_config, payload)
    chat_id = card_action.chat_id or str(card_action.value.get("chat_id") or "").strip() or None
    reply_target = _reply_target(chat_id=chat_id, open_id=identity.open_id)
    if not reply_target:
        return False, action
    if action == "dismiss":
        item = _approval_item_by_index(chat_id, index) or _approval_card_item_snapshot(card_action.value)
        if not item:
            return False, action
        if card_action.message_id:
            await FeishuClient(app_config).update_message_content(
                message_id=card_action.message_id,
                content=_single_approval_dismissed_card(item=item),
            )
        return True, "dismissed"
    if action == "request_reject":
        item = _approval_item_by_index(chat_id, index)
        if not item:
            return False, action
        action_id = uuid4().hex
        _save_current_approval_item(chat_id, index=index, item=item)
        _save_single_approval_runtime_action_input(
            chat_id=chat_id,
            action="reject",
            pending={
                "id": action_id,
                "action": "reject",
                "index": index,
                "item": item,
                "comment": "",
                "detail_message_id": card_action.message_id,
                "workbench_message_id": str(card_action.value.get("workbench_message_id") or ""),
            },
            identity=identity,
            app_config=app_config,
            entrypoint="runtime_approval_detail_action",
            confirmed=False,
            missing_params=("comment",),
        )
        _record_action_trace(chat_id, _approval_trace_fields(kind="approval_detail", action="reject", status="waiting_input", action_id=action_id))
        _enqueue_runtime_card_reply(
            app_config=app_config,
            command="拒绝这个审批",
            identity=identity,
            chat_id=chat_id,
            reply_target=reply_target,
        )
        return True, "missing_reason"
    if action == "request_transfer":
        item = _approval_item_by_index(chat_id, index)
        if not item:
            return False, action
        action_id = uuid4().hex
        _save_current_approval_item(chat_id, index=index, item=item)
        _save_single_approval_runtime_action_input(
            chat_id=chat_id,
            action="transfer",
            pending={
                "id": action_id,
                "action": "transfer",
                "index": index,
                "item": item,
                "comment": "",
                "detail_message_id": card_action.message_id,
                "workbench_message_id": str(card_action.value.get("workbench_message_id") or ""),
            },
            identity=identity,
            app_config=app_config,
            entrypoint="runtime_approval_detail_action",
            confirmed=False,
            missing_params=("target_user",),
        )
        _record_action_trace(chat_id, _approval_trace_fields(kind="approval_detail", action="transfer", status="waiting_input", action_id=action_id))
        _enqueue_runtime_card_reply(
            app_config=app_config,
            command="转交这个审批",
            identity=identity,
            chat_id=chat_id,
            reply_target=reply_target,
        )
        return True, "missing_transfer"
    if action == "request_add_sign":
        item = _approval_item_by_index(chat_id, index)
        if not item:
            return False, action
        action_id = uuid4().hex
        _save_current_approval_item(chat_id, index=index, item=item)
        _save_single_approval_runtime_action_input(
            chat_id=chat_id,
            action="add_sign",
            pending={
                "id": action_id,
                "action": "add_sign",
                "index": index,
                "item": item,
                "comment": "",
                "detail_message_id": card_action.message_id,
                "workbench_message_id": str(card_action.value.get("workbench_message_id") or ""),
            },
            identity=identity,
            app_config=app_config,
            entrypoint="runtime_approval_detail_action",
            confirmed=False,
            missing_params=("target_user",),
        )
        _record_action_trace(chat_id, _approval_trace_fields(kind="approval_detail", action="add_sign", status="waiting_input", action_id=action_id))
        _enqueue_runtime_card_reply(
            app_config=app_config,
            command="加签这个审批",
            identity=identity,
            chat_id=chat_id,
            reply_target=reply_target,
        )
        return True, "missing_add_sign"
    item = _approval_item_by_index(chat_id, index)
    if not item:
        return False, action
    action_id = uuid4().hex
    _save_current_approval_item(chat_id, index=index, item=item)
    _save_single_approval_runtime_action_input(
        chat_id=chat_id,
        action=action,
        pending={
            "id": action_id,
            "action": action,
            "index": index,
            "item": item,
            "comment": _approval_action_comment(card_action.value),
            "detail_message_id": card_action.message_id,
            "workbench_message_id": str(card_action.value.get("workbench_message_id") or ""),
        },
        identity=identity,
        app_config=app_config,
        entrypoint="runtime_approval_detail_action",
    )
    _record_action_trace(chat_id, _approval_trace_fields(kind="approval_detail", action=action, status="queued", action_id=action_id))
    _enqueue_runtime_card_reply(
        app_config=app_config,
        command="确认执行",
        identity=identity,
        chat_id=chat_id,
        reply_target=reply_target,
    )
    return True, "submitted"


async def _handle_runtime_approval_workbench(
    db: Session,
    app_config: FeishuAppConfig,
    payload: dict[str, Any],
) -> tuple[bool, str]:
    card_action = parse_gateway_card_action(payload)
    if card_action is None or card_action.kind != "runtime_approval_workbench":
        return False, ""
    action = str(card_action.action or card_action.value.get("action") or "").strip()
    index = int(card_action.value.get("index") or 0)
    if action not in {
        "select",
        "clear_selection",
        "batch_approve",
        "detail",
        "approve",
        "reject",
        "toggle_collapse",
        "toggle_group",
        "batch_approve_group",
    }:
        return False, action
    if action in {"select", "detail", "approve", "reject"} and index <= 0:
        return False, action

    identity = feishu_identity.get_sender_identity(db, app_config, payload)
    chat_id = card_action.chat_id or str(card_action.value.get("chat_id") or "").strip() or None
    reply_target = _reply_target(chat_id=chat_id, open_id=identity.open_id)
    if not reply_target:
        return False, action

    if action in {"toggle_group", "batch_approve_group", "batch_approve", "select", "detail", "approve", "reject"} and load_result_context(chat_id) is None:
        await _update_approval_workbench_expired_message(app_config=app_config, message_id=card_action.message_id)
        return True, "expired"

    if action == "toggle_group":
        group = str(card_action.value.get("group") or "").strip()
        if group not in {"hold", "review", "pass"}:
            return False, action
        _toggle_approval_workbench_group(chat_id, group)
        await _update_approval_workbench_message(app_config=app_config, message_id=card_action.message_id, chat_id=chat_id)
        return True, action

    if action == "batch_approve_group":
        group = str(card_action.value.get("group") or "").strip()
        if group != "pass":
            return False, action
        _save_batch_selection(chat_id, _approval_indexes_for_group(chat_id, group))
        await _prepare_batch_approve_confirmation(
            app_config=app_config,
            chat_id=chat_id,
            reply_target=reply_target,
        )
        return True, action

    if action == "toggle_collapse":
        session_context = load_session_context(chat_id)
        session_context[_APPROVAL_WORKBENCH_COLLAPSED_KEY] = not bool(session_context.get(_APPROVAL_WORKBENCH_COLLAPSED_KEY))
        save_session_context(chat_id, session_context)
        await _update_approval_workbench_message(app_config=app_config, message_id=card_action.message_id, chat_id=chat_id)
        return True, action

    if action == "select":
        await _toggle_batch_selection(
            app_config=app_config,
            chat_id=chat_id,
            reply_target=reply_target,
            index=index,
        )
        await _update_approval_workbench_message(app_config=app_config, message_id=card_action.message_id, chat_id=chat_id)
        return True, action
    if action == "clear_selection":
        _save_batch_selection(chat_id, [])
        await _update_approval_workbench_message(app_config=app_config, message_id=card_action.message_id, chat_id=chat_id)
        return True, action
    if action == "batch_approve":
        _record_action_trace(
            chat_id,
            {
                "kind": "approval_batch",
                "action": "prepare_confirm",
                "status": "started",
                "selected_indexes": _load_batch_selection(chat_id),
            },
        )
        await _prepare_batch_approve_confirmation(
            app_config=app_config,
            chat_id=chat_id,
            reply_target=reply_target,
        )
        return True, action

    if action == "detail":
        await _send_single_approval_detail(
            db=db,
            app_config=app_config,
            identity=identity,
            chat_id=chat_id,
            reply_target=reply_target,
            index=index,
            workbench_message_id=card_action.message_id,
        )
        return True, action
    await _send_single_approval_confirmation(
        app_config=app_config,
        chat_id=chat_id,
        reply_target=reply_target,
        index=index,
        action=action,
        workbench_message_id=card_action.message_id,
    )
    return True, action


async def _handle_runtime_approval_batch_confirm(
    db: Session,
    app_config: FeishuAppConfig,
    payload: dict[str, Any],
) -> tuple[bool, str]:
    card_action = parse_gateway_card_action(payload)
    if card_action is None or card_action.kind != "runtime_approval_batch_confirm":
        return False, ""
    action = str(card_action.action or card_action.value.get("action") or "").strip()
    if action not in {"confirm", "cancel"}:
        return False, action

    identity = feishu_identity.get_sender_identity(db, app_config, payload)
    chat_id = card_action.chat_id or str(card_action.value.get("chat_id") or "").strip() or None
    reply_target = _reply_target(chat_id=chat_id, open_id=identity.open_id)
    if not reply_target:
        return False, action
    pending = load_session_context(chat_id).get(_PENDING_BATCH_KEY)
    expected_id = str(pending.get("id") or "").strip() if isinstance(pending, dict) else ""
    action_id = str(card_action.value.get("pending_action_id") or card_action.value.get("confirmation_token") or "").strip()
    if not expected_id or action_id != expected_id:
        _record_action_trace(chat_id, _approval_trace_fields(kind="approval_batch", action=action, status="stale", action_id=action_id or expected_id))
        _save_approval_batch_receipt(
            chat_id=chat_id,
            status="stale",
            answer="这张批量审批确认卡已过期，请重新勾选后再提交。",
            pending=pending if isinstance(pending, dict) else {},
            action_id=action_id or expected_id,
        )
        await feishu_replies.send_text_reply(
            app_config=app_config,
            reply_target=reply_target,
            text="这张批量审批确认卡已过期，请重新勾选后再提交。",
        )
        return True, action
    if isinstance(pending, dict) and _pending_action_expired(pending):
        _clear_batch_state(chat_id)
        _record_action_trace(chat_id, _approval_trace_fields(kind="approval_batch", action=action, status="stale", action_id=expected_id))
        _save_approval_batch_receipt(
            chat_id=chat_id,
            status="stale",
            answer="这张批量审批确认卡已过期，请重新勾选后再提交。",
            pending=pending,
            action_id=expected_id,
        )
        await feishu_replies.send_text_reply(
            app_config=app_config,
            reply_target=reply_target,
            text="这张批量审批确认卡已过期，请重新勾选后再提交。",
        )
        return True, action
    if action == "cancel":
        _clear_batch_state(chat_id)
        _record_action_trace(chat_id, _approval_trace_fields(kind="approval_batch", action="approve", status="cancelled", action_id=expected_id))
        _save_approval_batch_receipt(
            chat_id=chat_id,
            status="cancelled",
            answer="已取消批量审批，不会提交。",
            pending=pending if isinstance(pending, dict) else {},
            action_id=expected_id,
        )
        await feishu_replies.send_text_reply(
            app_config=app_config,
            reply_target=reply_target,
            text="已取消批量审批，不会提交。",
        )
        return True, action

    _record_action_trace(chat_id, _approval_trace_fields(kind="approval_batch", action="approve", status="queued", action_id=expected_id))
    _save_approval_batch_receipt(
        chat_id=chat_id,
        status="queued",
        answer="已确认，批量审批已进入执行队列。",
        pending=pending if isinstance(pending, dict) else {},
        action_id=expected_id,
    )
    _enqueue_batch_approve(
        app_config=app_config,
        identity=identity,
        chat_id=chat_id,
        reply_target=reply_target,
    )
    await feishu_replies.send_text_reply(
        app_config=app_config,
        reply_target=reply_target,
        text="已确认，开始批量提交审批。完成后我会发处理结果。",
    )
    return True, action


async def _handle_runtime_approval_single_confirm(
    db: Session,
    app_config: FeishuAppConfig,
    payload: dict[str, Any],
) -> tuple[bool, str]:
    card_action = parse_gateway_card_action(payload)
    if card_action is None or card_action.kind != "runtime_approval_single_confirm":
        return False, ""
    action = str(card_action.action or card_action.value.get("action") or "").strip()
    if action not in {"confirm", "cancel"}:
        return False, action

    identity = feishu_identity.get_sender_identity(db, app_config, payload)
    chat_id = card_action.chat_id or str(card_action.value.get("chat_id") or "").strip() or None
    reply_target = _reply_target(chat_id=chat_id, open_id=identity.open_id)
    if not reply_target:
        return False, action
    pending = _load_pending_single(chat_id)
    if not pending:
        await feishu_replies.send_text_reply(
            app_config=app_config,
            reply_target=reply_target,
            text="这笔审批确认已过期，请重新点击通过或拒绝。",
        )
        return True, action
    expected_id = str(pending.get("id") or "").strip()
    action_id = str(card_action.value.get("pending_action_id") or "").strip()
    if not expected_id or action_id != expected_id:
        _record_action_trace(chat_id, _approval_trace_fields(kind="approval_single", action=action, status="stale", action_id=action_id or expected_id))
        pending_item = pending.get("item") if isinstance(pending.get("item"), dict) else {}
        if pending_item:
            _save_approval_action_receipt(
                chat_id=chat_id,
                action=str(pending.get("action") or action),
                status="stale",
                item=pending_item,
                action_id=action_id or expected_id,
                answer="这张审批确认卡已过期，请重新点击通过或拒绝。",
            )
        await feishu_replies.send_text_reply(
            app_config=app_config,
            reply_target=reply_target,
            text="这张审批确认卡已过期，请重新点击通过或拒绝。",
        )
        return True, action
    if _pending_action_expired(pending):
        _clear_pending_single(chat_id)
        _record_action_trace(chat_id, _approval_trace_fields(kind="approval_single", action=action, status="stale", action_id=expected_id))
        pending_item = pending.get("item") if isinstance(pending.get("item"), dict) else {}
        if pending_item:
            _save_approval_action_receipt(
                chat_id=chat_id,
                action=str(pending.get("action") or action),
                status="stale",
                item=pending_item,
                action_id=expected_id,
                answer="这张审批确认卡已过期，请重新点击通过或拒绝。",
            )
        await feishu_replies.send_text_reply(
            app_config=app_config,
            reply_target=reply_target,
            text="这张审批确认卡已过期，请重新点击通过或拒绝。",
        )
        return True, action
    if action == "cancel":
        _save_single_approval_runtime_action_input(
            chat_id=chat_id,
            action="cancel",
            pending=pending,
            identity=identity,
            app_config=app_config,
        )
        _clear_pending_single(chat_id)
        _record_action_trace(chat_id, _approval_trace_fields(kind="approval_single", action=str(pending.get("action") or ""), status="queued_cancel", action_id=expected_id))
        _enqueue_runtime_card_reply(
            app_config=app_config,
            command="取消",
            identity=identity,
            chat_id=chat_id,
            reply_target=reply_target,
        )
        return True, action
    _save_single_approval_runtime_action_input(
        chat_id=chat_id,
        action=str(pending.get("action") or ""),
        pending=pending,
        identity=identity,
        app_config=app_config,
    )
    _record_action_trace(chat_id, _approval_trace_fields(kind="approval_single", action=str(pending.get("action") or ""), status="queued", action_id=expected_id))
    _enqueue_runtime_card_reply(
        app_config=app_config,
        command="确认执行",
        identity=identity,
        chat_id=chat_id,
        reply_target=reply_target,
    )
    _clear_pending_single(chat_id)
    return True, action


async def _toggle_batch_selection(
    *,
    app_config: FeishuAppConfig,
    chat_id: str | None,
    reply_target: dict[str, str],
    index: int,
) -> None:
    selected = set(_load_batch_selection(chat_id))
    if index in selected:
        selected.remove(index)
    else:
        selected.add(index)
    saved = sorted(selected)
    _save_batch_selection(chat_id, saved)


async def _prepare_batch_approve_confirmation(
    *,
    app_config: FeishuAppConfig,
    chat_id: str | None,
    reply_target: dict[str, str],
) -> None:
    indexes = _load_batch_selection(chat_id)
    result_context = load_result_context(chat_id)
    if not indexes or result_context is None or not result_context.items:
        await feishu_replies.send_text_reply(
            app_config=app_config,
            reply_target=reply_target,
            text="还没有勾选要批量通过的审批，请先在工作台里勾选单据。",
        )
        return
    selected_items: list[dict[str, Any]] = []
    for index in indexes:
        position = index - 1
        if 0 <= position < len(result_context.items):
            selected_items.append({"index": index, "item": result_context.items[position]})
    if not selected_items:
        await feishu_replies.send_text_reply(
            app_config=app_config,
            reply_target=reply_target,
            text="选择的审批已过期，请重新查询待审批。",
        )
        return
    pending_id = uuid4().hex
    session_context = load_session_context(chat_id)
    session_context[_PENDING_BATCH_KEY] = {
        **_pending_expiry_payload(),
        "id": pending_id,
        "action": "approve",
        "items": selected_items,
    }
    save_session_context(chat_id, session_context)
    _save_approval_pending_confirmation(
        chat_id=chat_id,
        action_id=pending_id,
        operation="batch_approve",
        items=selected_items,
        answer=f"批量审批等待确认：{len(selected_items)} 笔。",
    )
    _record_action_trace(chat_id, _approval_trace_fields(kind="approval_batch", action="approve", status="confirmation_card_started", action_id=pending_id))
    await feishu_replies.send_interactive_reply(
        app_config=app_config,
        reply_target=reply_target,
        card=_batch_approval_confirmation_card(selected_items, chat_id=chat_id, pending_id=pending_id),
    )
    _record_action_trace(chat_id, _approval_trace_fields(kind="approval_batch", action="approve", status="confirmation_card_sent", action_id=pending_id))


async def _send_single_approval_detail(
    *,
    db: Session,
    app_config: FeishuAppConfig,
    identity: Any,
    chat_id: str | None,
    reply_target: dict[str, str],
    index: int,
    workbench_message_id: str | None = None,
) -> None:
    item = _approval_item_by_index(chat_id, index)
    if not item:
        await feishu_replies.send_text_reply(
            app_config=app_config,
            reply_target=reply_target,
            text="这条审批上下文已过期，请重新查询待审批。",
        )
        return
    _save_current_approval_item(chat_id, index=index, item=item)
    _record_action_trace(chat_id, _approval_trace_fields(kind="approval_detail", action="get_detail", status="started"))
    detail_answer = ""
    try:
        detail_answer = _runtime_approval_detail_answer(
            db=db,
            app_config=app_config,
            identity=identity,
            chat_id=chat_id,
            item=item,
        )
    except Exception as exc:
        _record_action_trace(chat_id, _approval_trace_fields(kind="approval_detail", action="get_detail", status="failed", error=str(exc)))
        detail_answer = _approval_detail_fallback(item)
    try:
        await feishu_replies.send_interactive_reply(
            app_config=app_config,
            reply_target=reply_target,
            card=_single_approval_detail_card(
                index=index,
                item=item,
                detail=detail_answer,
                chat_id=chat_id,
                workbench_message_id=workbench_message_id,
            ),
        )
        _record_action_trace(chat_id, _approval_trace_fields(kind="approval_detail", action="get_detail", status="success"))
    except Exception as exc:
        _record_action_trace(chat_id, _approval_trace_fields(kind="approval_detail", action="get_detail", status="failed", error=str(exc)))
        await feishu_replies.send_text_reply(
            app_config=app_config,
            reply_target=reply_target,
            text="审批详情卡片发送失败，我已经记录错误，请稍后重试。",
        )


def _runtime_approval_detail_answer(
    *,
    db: Session,
    app_config: FeishuAppConfig,
    identity: Any,
    chat_id: str | None,
    item: dict[str, Any],
) -> str:
    context = build_runtime_context(
        message="看审批详情",
        identity=identity,
        company_id=app_config.company_id,
        chat_id=chat_id,
    )
    if context.result_context is None:
        context = replace(
            context,
            result_context=ResultContext(
                result_type="approval_list",
                query_id=f"approval_detail:card:{uuid4().hex[:12]}",
                count=1,
                items=(item,),
                metadata={"source": "card", "item_count": 1},
            ),
        )
    envelope = run_runtime_v5(
        context=context,
        providers=build_feishu_provider_registry(db=db, cli_profile=feishu_app_cli_profile(app_config)),
    )
    if envelope.composed.answer:
        return envelope.composed.answer
    if envelope.composed.result_context and envelope.composed.result_context.answer:
        return envelope.composed.result_context.answer
    return "没有查到审批详情。"


async def _send_single_approval_confirmation(
    *,
    app_config: FeishuAppConfig,
    chat_id: str | None,
    reply_target: dict[str, str],
    index: int,
    action: str,
    comment: str = "",
    workbench_message_id: str | None = None,
) -> None:
    item = _approval_item_by_index(chat_id, index)
    if not item:
        await feishu_replies.send_text_reply(
            app_config=app_config,
            reply_target=reply_target,
            text="这条审批上下文已过期，请重新查询待审批。",
        )
        return
    pending_id = uuid4().hex
    _save_current_approval_item(chat_id, index=index, item=item)
    _save_pending_single(
        chat_id,
        {
            "id": pending_id,
            "action": action,
            "index": index,
            "item": item,
            "comment": comment,
            "workbench_message_id": workbench_message_id or "",
        },
    )
    _save_approval_pending_confirmation(
        chat_id=chat_id,
        action_id=pending_id,
        operation=action,
        items=[{"index": index, "item": item}],
        answer=f"审批{_approval_action_label(action)}等待确认。",
    )
    _record_action_trace(chat_id, _approval_trace_fields(kind="approval_single", action=action, status="confirmation_card_started", action_id=pending_id))
    try:
        await feishu_replies.send_interactive_reply(
            app_config=app_config,
            reply_target=reply_target,
            card=_single_approval_confirmation_card(index=index, item=item, action=action, chat_id=chat_id, comment=comment, pending_id=pending_id),
        )
        _record_action_trace(chat_id, _approval_trace_fields(kind="approval_single", action=action, status="confirmation_card_sent", action_id=pending_id))
    except Exception as exc:
        _record_action_trace(chat_id, _approval_trace_fields(kind="approval_single", action=action, status="confirmation_card_failed", error=str(exc), action_id=pending_id))
        await feishu_replies.send_text_reply(
            app_config=app_config,
            reply_target=reply_target,
            text="审批确认卡片发送失败，我已经记录错误，请稍后重试。",
        )


def _save_runtime_confirmation_receipt(
    *,
    chat_id: str | None,
    status: str,
    pending: dict[str, Any],
    answer: str,
    action_id: str = "",
) -> None:
    if not chat_id:
        return
    strategy = str(pending.get("strategy") or pending.get("intent") or "runtime_action").strip()
    message = str(pending.get("message") or "").strip()
    label = str(pending.get("intent_label") or strategy or "动作").strip()
    sources = pending.get("sources") if isinstance(pending.get("sources"), list) else []
    summary = _runtime_confirmation_receipt_summary(status=status, label=label, message=message)
    status_group = _action_status_group(status)
    save_result_context(
        chat_id,
        ResultContext(
            result_type="runtime_action",
            query_id=uuid4().hex,
            count=1,
            items=(
                {
                    "source": ",".join(str(source) for source in sources) or "runtime",
                    "operation": strategy,
                    "status": status,
                    "status_group": status_group,
                    "is_terminal": status_group == "terminal",
                    "is_pending": status_group == "pending",
                    "action_id": action_id,
                    "title": label,
                    "message": message,
                    "summary": summary,
                },
            ),
            metadata={
                "context_kind": "action_receipt",
                "question_type": "action",
                "data_scope": str(pending.get("data_scope") or "self"),
                "actionable": False,
                "execution_status": status,
                "source": "runtime",
                "operation": strategy,
                "action_id": action_id,
                "action_status_group": status_group,
                "is_terminal_action": status_group == "terminal",
                "is_pending_action": status_group == "pending",
                "result_sources": sources,
                "consume_policy": {
                    "prefer_items": True,
                    "allow_answer_fallback": False,
                    "requires_refresh_when_expired": True,
                    "supports_index_followup": True,
                    "supports_detail_followup": True,
                },
                "followup_fields": ["action_id", "title", "message", "status", "summary"],
                "item_identity_fields": ["action_id"],
                "item_count": 1,
                "display_count": 1,
            },
            answer=answer,
        ),
    )


def _save_runtime_confirmation_action_input(
    *,
    chat_id: str | None,
    action: str,
    pending: dict[str, Any],
    identity: Any,
    app_config: FeishuAppConfig,
) -> None:
    if not chat_id:
        return
    session_context = load_session_context(chat_id)
    strategy = str(pending.get("strategy") or pending.get("intent") or "runtime_action").strip()
    session_context["runtime_v5_action_input"] = build_runtime_action_input_payload(
        action_id=str(pending.get("id") or ""),
        action_type=action,
        intent=str(pending.get("intent") or strategy),
        strategy=strategy,
        target=pending.get("entities") if isinstance(pending.get("entities"), dict) else {},
        confirmed=action == "confirm",
        confirmation_token=str(pending.get("confirmation_token") or pending.get("id") or ""),
        company_id=str(app_config.company_id),
        chat_id=chat_id,
        user_id=str(getattr(identity, "user_id", "") or getattr(identity, "open_id", "") or ""),
        open_id=str(getattr(identity, "open_id", "") or ""),
        source_ui="card",
        message="取消" if action == "cancel" else str(pending.get("message") or ""),
        sources=pending.get("sources") if isinstance(pending.get("sources"), list) else [],
    )
    save_session_context(chat_id, session_context)


def _save_single_approval_runtime_action_input(
    *,
    chat_id: str | None,
    action: str,
    pending: dict[str, Any],
    identity: Any,
    app_config: FeishuAppConfig,
    entrypoint: str = "runtime_approval_single_confirm",
    confirmed: bool | None = None,
    missing_params: tuple[str, ...] = (),
) -> None:
    if not chat_id:
        return
    approval_action = str(pending.get("action") or "").strip()
    action_type = "cancel" if action == "cancel" else approval_action
    if action_type not in {"approve", "reject", "transfer", "add_sign", "cancel"}:
        return
    item = pending.get("item") if isinstance(pending.get("item"), dict) else {}
    strategy_action = approval_action if approval_action in {"approve", "reject", "transfer", "add_sign"} else "approve"
    strategy = f"approval_{strategy_action}"
    action_message = {
        "approve": "同意这个审批",
        "reject": "拒绝这个审批",
        "transfer": "转交这个审批",
        "add_sign": "加签这个审批",
    }.get(action_type, "处理这个审批")
    session_context = load_session_context(chat_id)
    session_context["runtime_v5_action_input"] = build_runtime_action_input_payload(
        action_id=str(pending.get("id") or ""),
        action_type=action_type,
        intent=strategy,
        strategy=strategy,
        target={
            "approval_code": str(item.get("approval_code") or ""),
            "instance_code": str(item.get("instance_code") or ""),
            "task_id": str(item.get("task_id") or ""),
            "index": int(pending.get("index") or 0),
            "comment": str(pending.get("comment") or "").strip(),
            "item": item,
        },
        confirmed=action_type != "cancel" if confirmed is None else confirmed,
        confirmation_token=str(pending.get("id") or ""),
        company_id=str(app_config.company_id),
        chat_id=chat_id,
        user_id=str(getattr(identity, "user_id", "") or getattr(identity, "open_id", "") or ""),
        open_id=str(getattr(identity, "open_id", "") or ""),
        source_ui="card",
        message="取消" if action_type == "cancel" else action_message,
        sources=["approval"],
        metadata={
            "entrypoint": entrypoint,
            "workbench_message_id": str(pending.get("workbench_message_id") or ""),
            "detail_message_id": str(pending.get("detail_message_id") or ""),
            "missing_params": list(missing_params),
        },
    )
    save_session_context(chat_id, session_context)


def _pending_action_trace_fields(pending: dict[str, Any]) -> dict[str, Any]:
    strategy = str(pending.get("strategy") or pending.get("intent") or "").strip()
    sources = pending.get("sources") if isinstance(pending.get("sources"), list) else []
    confirmation_reasons = pending.get("confirmation_reasons") if isinstance(pending.get("confirmation_reasons"), list) else []
    payload: dict[str, Any] = {
        "strategy": strategy,
        "sources": sources,
        "confirmation_reasons": confirmation_reasons,
        "requires_confirmation": bool(confirmation_reasons),
        "execution_identity": "user",
    }
    if strategy:
        payload["route_path"] = str(pending.get("route_path") or "")
    return payload


def _pending_action_expired(pending: dict[str, Any]) -> bool:
    expires_at = str(pending.get("expires_at") or "").strip()
    if not expires_at:
        return False
    try:
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed <= datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return False


def _pending_expiry_payload() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    expires_at = now.timestamp() + _APPROVAL_PENDING_TTL_SECONDS
    return {
        "created_at": now.isoformat(),
        "expires_at": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(),
        "ttl_seconds": _APPROVAL_PENDING_TTL_SECONDS,
    }


def _session_without_runtime_pending_action(session_context: dict[str, Any]) -> dict[str, Any]:
    payload = dict(session_context)
    payload.pop("runtime_v5_pending_action", None)
    return payload


def _approval_trace_fields(*, kind: str, action: str, status: str, error: str = "", action_id: str = "") -> dict[str, Any]:
    strategy = {
        "approve": "approval_approve",
        "reject": "approval_reject",
        "transfer": "approval_transfer",
        "add_sign": "approval_add_sign",
        "rollback": "approval_rollback",
        "remind": "approval_remind",
        "cancel": "approval_cancel",
        "cc": "approval_cc",
        "get_detail": "approval_detail",
    }.get(action, action or "approval_action")
    payload: dict[str, Any] = {
        "kind": kind,
        "action": action,
        "strategy": strategy,
        "sources": ["approval"],
        "source": "approval",
        "status": status,
        "action_id": action_id,
        "execution_identity": "bot" if action == "get_detail" else "user",
        "requires_confirmation": action != "get_detail",
        "confirmation_reasons": [] if action == "get_detail" else ["capability_requires_confirmation", "high_risk_action", "action_question"],
    }
    if error:
        payload["error"] = error
    return payload


def _runtime_confirmation_receipt_summary(*, status: str, label: str, message: str) -> str:
    status_label = {
        "cancelled": "已取消",
        "stale": "确认已失效",
        "error": "执行失败",
        "success": "已完成",
    }.get(status, status or "已处理")
    parts = [label, status_label]
    if message:
        parts.append(message[:120])
    return "｜".join(part for part in parts if part)


def _action_status_group(status: str) -> str:
    value = str(status or "")
    if value in {"queued", "started", "processing", "pending", "confirmation_card_started", "confirmation_card_sent"}:
        return "pending"
    if value in {"success", "error", "failed", "cancelled", "stale", "stale_cleanup", "expired", "denied", "skipped", "confirmation_card_failed"}:
        return "terminal"
    return "unknown"


def _save_approval_action_receipt(
    *,
    chat_id: str | None,
    action: str,
    status: str,
    item: dict[str, Any],
    answer: str,
    action_id: str = "",
    error: str | None = None,
    duration_ms: int = 0,
) -> None:
    if not chat_id:
        return
    title = str(item.get("title") or "未命名审批")
    applicant = str(item.get("applicant") or "")
    amount = item.get("amount")
    status_group = _action_status_group(status)
    provider_executed = status not in {"queued", "cancelled", "stale", "stale_cleanup", "expired"}
    save_result_context(
        chat_id,
        ResultContext(
            result_type="approval_action",
            query_id=uuid4().hex,
            count=1,
            items=(
                {
                    "source": "approval",
                    "operation": action,
                    "status": status,
                    "status_group": status_group,
                "is_terminal": status_group == "terminal",
                "is_pending": status_group == "pending",
                "action_id": action_id,
                "route_path": "feishu_approval_task_query",
                "title": title,
                "applicant": applicant,
                "amount": amount,
                    "error": str(error or ""),
                    "summary": _approval_receipt_summary(action=action, status=status, title=title, applicant=applicant, amount=amount),
                },
            ),
            metadata={
                "context_kind": "action_receipt",
                "question_type": "action",
                "data_scope": "self",
                "actionable": False,
                "execution_status": status,
                "source": "approval",
                "operation": action,
                "route_path": "feishu_approval_task_query",
                "result_sources": ["approval"],
                "provider_evidence": {
                    "provider_count": 1,
                    "sources": ["approval"],
                    "operations": [action],
                    "success_count": 1 if provider_executed and status == "success" else 0,
                    "error_count": 1 if provider_executed and status not in {"success", "denied"} else 0,
                    "denied_count": 1 if provider_executed and status == "denied" else 0,
                    "skipped_count": 0,
                },
                "source_execution_status": {
                    "planned_sources": ["approval"],
                    "executed_sources": ["approval"] if provider_executed else [],
                    "missing_sources": [] if provider_executed else ["approval"],
                    "extra_sources": [],
                    "success_count": 1 if provider_executed and status == "success" else 0,
                    "error_count": 1 if provider_executed and status not in {"success", "denied"} else 0,
                },
                "action_id": action_id,
                "action_status_group": status_group,
                "is_terminal_action": status_group == "terminal",
                "is_pending_action": status_group == "pending",
                "duration_ms": duration_ms,
                "consume_policy": {
                    "prefer_items": True,
                    "allow_answer_fallback": False,
                    "requires_refresh_when_expired": True,
                    "supports_index_followup": True,
                    "supports_detail_followup": True,
                },
                "followup_fields": ["action_id", "title", "applicant", "amount", "status", "summary", "error", "route_path"],
                "item_identity_fields": ["action_id"],
                "item_count": 1,
                "display_count": 1,
            },
            answer=answer,
        ),
    )


def _approval_receipt_summary(*, action: str, status: str, title: str, applicant: str, amount: Any) -> str:
    action_label = {
        "approve": "通过",
        "reject": "拒绝",
        "transfer": "转交",
        "add_sign": "加签",
        "rollback": "退回",
        "remind": "催办",
        "cancel": "撤回",
        "cc": "抄送",
    }.get(action, action)
    status_label = {
        "success": "已完成",
        "partial": "部分完成",
        "error": "执行失败",
        "failed": "执行失败",
        "denied": "无权限",
    }.get(status, status or "未知")
    parts = [f"{action_label}{status_label}", title]
    if applicant:
        parts.append(applicant)
    if amount not in (None, ""):
        parts.append(f"{amount}元")
    return "｜".join(str(part) for part in parts if str(part or "").strip())


def _save_approval_pending_confirmation(
    *,
    chat_id: str | None,
    action_id: str,
    operation: str,
    items: list[dict[str, Any]],
    answer: str,
) -> None:
    if not chat_id:
        return
    normalized_items: list[dict[str, Any]] = []
    for entry in items[:20]:
        item = entry.get("item") if isinstance(entry, dict) and isinstance(entry.get("item"), dict) else {}
        index = entry.get("index") if isinstance(entry, dict) else ""
        title = str(item.get("title") or item.get("approval_name") or "未命名审批")
        applicant = str(item.get("applicant") or "")
        amount = item.get("amount")
        summary_parts = [f"{index}. {title}" if index else title]
        if applicant:
            summary_parts.append(applicant)
        if amount not in (None, ""):
            summary_parts.append(f"{amount}元")
        normalized_items.append(
            {
                "source": "approval",
                "operation": operation,
                "status": "pending_confirmation",
                "status_group": "prepared",
                "is_terminal": False,
                "is_pending": True,
                "action_id": action_id,
                "correlation_id": action_id,
                "confirmation_token": action_id,
                "confirmation_token_type": "runtime_action_id",
                "index": index,
                "title": title,
                "applicant": applicant,
                "amount": amount,
                "summary": "｜".join(str(part) for part in summary_parts if part not in (None, "")),
            }
        )
    if not normalized_items:
        normalized_items.append(
            {
                "source": "approval",
                "operation": operation,
                "status": "pending_confirmation",
                "status_group": "prepared",
                "is_terminal": False,
                "is_pending": True,
                "action_id": action_id,
                "correlation_id": action_id,
                "confirmation_token": action_id,
                "confirmation_token_type": "runtime_action_id",
                "summary": answer,
            }
        )
    save_result_context(
        chat_id,
        ResultContext(
            result_type="approval_pending_confirmation",
            query_id=f"approval:{operation}:pending_confirmation:{action_id}",
            count=len(normalized_items),
            items=tuple(normalized_items),
            metadata={
                "context_kind": "pending_confirmation",
                "question_type": "action",
                "data_scope": "self",
                "actionable": True,
                "execution_status": "pending_confirmation",
                "source": "approval",
                "operation": operation,
                "route_path": "feishu_approval_task_query",
                "result_sources": ["approval"],
                "action_id": action_id,
                "confirmation_token": action_id,
                "confirmation_token_type": "runtime_action_id",
                "confirmation_token_available": True,
                "action_status_group": "prepared",
                "is_terminal_action": False,
                "is_pending_action": True,
                "requires_confirmation": True,
                "execution_identity": "user",
                "write_confirmation_contract": {
                    "status": "ready",
                    "contract": "dry_run_then_confirmation_token",
                    "requires_dry_run": True,
                    "requires_confirmation_token": True,
                    "requires_confirmation": True,
                    "execution_identity": "user",
                    "strategy": f"approval_{operation}",
                    "sources": ["approval"],
                },
                "consume_policy": {
                    "prefer_items": True,
                    "allow_answer_fallback": False,
                    "requires_refresh_when_expired": True,
                    "supports_index_followup": bool(normalized_items),
                    "supports_detail_followup": bool(normalized_items),
                },
                "followup_fields": ["action_id", "confirmation_token", "title", "applicant", "amount", "status", "summary"],
                "item_identity_fields": ["action_id", "correlation_id", "confirmation_token"],
                "item_count": len(normalized_items),
                "display_count": len(normalized_items),
            },
            answer=answer,
        ),
    )


def _approval_action_label(action: str) -> str:
    return {
        "approve": "通过",
        "reject": "拒绝",
        "transfer": "转交",
        "add_sign": "加签",
        "rollback": "退回",
        "remind": "催办",
        "cancel": "撤回",
        "cc": "抄送",
        "batch_approve": "批量通过",
    }.get(str(action or ""), "操作")


def _save_approval_batch_receipt(
    *,
    chat_id: str | None,
    status: str,
    answer: str,
    pending: dict[str, Any],
    action_id: str = "",
) -> None:
    if not chat_id:
        return
    entries = pending.get("items") if isinstance(pending.get("items"), list) else []
    receipt_items: list[dict[str, Any]] = []
    status_group = _action_status_group(status)
    provider_executed = status not in {"queued", "cancelled", "stale", "stale_cleanup", "expired"}
    for entry in entries[:20]:
        item = entry.get("item") if isinstance(entry, dict) and isinstance(entry.get("item"), dict) else {}
        index = entry.get("index") if isinstance(entry, dict) else ""
        title = str(item.get("title") or "未命名审批")
        applicant = str(item.get("applicant") or "")
        amount = item.get("amount")
        label = f"{index}. {title}" if index else title
        if applicant:
            label = f"{label}｜{applicant}"
        if amount not in (None, ""):
            label = f"{label}｜{amount}元"
        receipt_items.append(
            {
                "source": "approval",
                "operation": "batch_approve",
                "status": status,
                "status_group": status_group,
                "is_terminal": status_group == "terminal",
                "is_pending": status_group == "pending",
                "action_id": action_id,
                "route_path": "feishu_approval_task_query",
                "title": title,
                "applicant": applicant,
                "amount": amount,
                "summary": label,
            }
        )
    if not receipt_items:
        receipt_items.append({"source": "approval", "operation": "batch_approve", "status": status, "status_group": status_group, "is_terminal": status_group == "terminal", "is_pending": status_group == "pending", "action_id": action_id, "summary": answer})
    save_result_context(
        chat_id,
        ResultContext(
            result_type="approval_action",
            query_id=uuid4().hex,
            count=len(receipt_items),
            items=tuple(receipt_items),
            metadata={
                "context_kind": "action_receipt",
                "question_type": "action",
                "data_scope": "self",
                "actionable": False,
                "execution_status": status,
                "source": "approval",
                "operation": "batch_approve",
                "route_path": "feishu_approval_task_query",
                "result_sources": ["approval"],
                "provider_evidence": {
                    "provider_count": 1,
                    "sources": ["approval"],
                    "operations": ["batch_approve"],
                    "success_count": len(receipt_items) if provider_executed and status == "success" else 0,
                    "error_count": len(receipt_items) if provider_executed and status not in {"success", "denied"} else 0,
                    "denied_count": len(receipt_items) if provider_executed and status == "denied" else 0,
                    "skipped_count": 0,
                },
                "source_execution_status": {
                    "planned_sources": ["approval"],
                    "executed_sources": ["approval"] if provider_executed else [],
                    "missing_sources": [] if provider_executed else ["approval"],
                    "extra_sources": [],
                    "success_count": len(receipt_items) if provider_executed and status == "success" else 0,
                    "error_count": len(receipt_items) if provider_executed and status not in {"success", "denied"} else 0,
                },
                "action_id": action_id,
                "action_status_group": status_group,
                "is_terminal_action": status_group == "terminal",
                "is_pending_action": status_group == "pending",
                "consume_policy": {
                    "prefer_items": True,
                    "allow_answer_fallback": False,
                    "requires_refresh_when_expired": True,
                    "supports_index_followup": bool(receipt_items),
                    "supports_detail_followup": bool(receipt_items),
                },
                "followup_fields": ["action_id", "title", "applicant", "amount", "status", "summary", "route_path"],
                "item_identity_fields": ["action_id"],
                "item_count": len(receipt_items),
                "display_count": len(receipt_items),
            },
            answer=answer,
        ),
    )


def _approval_item_by_index(chat_id: str | None, index: int) -> dict[str, Any] | None:
    result_context = load_result_context(chat_id)
    if result_context is None or not result_context.items:
        return None
    position = index - 1
    if position < 0 or position >= len(result_context.items):
        return None
    return dict(result_context.items[position])


def _single_approval_confirmation_card(
    *,
    index: int,
    item: dict[str, Any],
    action: str,
    chat_id: str | None,
    comment: str = "",
    pending_id: str = "",
) -> dict[str, Any]:
    action_text = {
        "approve": "通过",
        "reject": "拒绝",
        "transfer": "转交",
        "add_sign": "加签",
        "rollback": "退回",
        "remind": "催办",
        "cancel": "撤回",
        "cc": "抄送",
    }.get(action, "处理")
    lines = [f"**确认{action_text}**", f"审批单：{item.get('title') or '未命名审批'}"]
    applicant = str(item.get("applicant") or "").strip()
    amount = str(item.get("amount") or "").strip()
    if applicant:
        lines.append(f"申请人：{applicant}")
    if amount:
        lines.append(f"金额：{amount} 元")
    if comment:
        lines.append(f"处理意见：{comment[:120]}")
    lines.append(f"序号：{index}")
    value_base = {
        "kind": "runtime_approval_single_confirm",
        "chat_id": chat_id or "",
        "pending_action_id": pending_id,
        "confirmation_token": pending_id,
    }
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue", "title": {"tag": "plain_text", "content": f"确认{action_text}"}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": f"点「确认」后会提交{action_text}；点「取消」不会执行。"}},
            {
                "tag": "action",
                "layout": "flow",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "确认"},
                        "type": "primary" if action == "approve" else "danger",
                        "value": {**value_base, "action": "confirm"},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "取消"},
                        "type": "default",
                        "value": {**value_base, "action": "cancel"},
                    },
                ],
            },
        ],
    }


def _single_approval_detail_card(
    *,
    index: int,
    item: dict[str, Any],
    detail: str,
    chat_id: str | None,
    workbench_message_id: str | None = None,
) -> dict[str, Any]:
    value_base = {
        "kind": "runtime_approval_detail_action",
        "chat_id": chat_id or "",
        "index": index,
        "workbench_message_id": workbench_message_id or "",
        "snapshot_title": str(item.get("title") or item.get("name") or "审批").strip(),
        "snapshot_applicant": str(item.get("applicant") or item.get("applicant_name") or "").strip(),
        "snapshot_amount": str(item.get("amount") or "").strip(),
    }
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue", "title": {"tag": "plain_text", "content": "审批详情"}},
        "elements": [
            {
                "tag": "action",
                "layout": "flow",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "收起 ×"},
                        "type": "default",
                        "value": {**value_base, "action": "dismiss"},
                    }
                ],
            },
            {"tag": "div", "text": {"tag": "lark_md", "content": detail[:2600]}},
            {"tag": "hr"},
            {
                "tag": "action",
                "layout": "flow",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "通过"},
                        "type": "primary",
                        "value": {**value_base, "action": "approve"},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "拒绝"},
                        "type": "danger",
                        "value": {**value_base, "action": "request_reject"},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "加签"},
                        "type": "default",
                        "value": {**value_base, "action": "request_add_sign"},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "转交"},
                        "type": "default",
                        "value": {**value_base, "action": "request_transfer"},
                    },
                ],
            },
        ],
    }


def _approval_card_item_snapshot(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(value.get("snapshot_title") or value.get("title") or value.get("name") or "审批").strip(),
        "applicant": str(value.get("snapshot_applicant") or value.get("applicant") or value.get("applicant_name") or "").strip(),
        "amount": value.get("snapshot_amount") if value.get("snapshot_amount") is not None else "",
    }


def _approval_detail_fallback(item: dict[str, Any]) -> str:
    lines = [
        "**审批详情暂时读取失败，先展示列表摘要。**",
        _approval_item_summary(item),
        "",
        "你仍可以返回待审批工作台继续查看，或稍后重新展开详情。",
    ]
    return "\n".join(lines)


def _single_approval_status_card(
    *,
    item: dict[str, Any],
    action: str,
    status: str,
    comment: str = "",
    error: str = "",
) -> dict[str, Any]:
    success = status == "success"
    action_text = {
        "approve": "通过",
        "reject": "拒绝",
        "transfer": "转交",
        "add_sign": "加签",
    }.get(action, "处理")
    lines = [
        f"**{'审批已处理' if success else '处理失败'}**",
        _approval_item_summary(item),
        f"操作：{action_text}",
    ]
    if comment:
        lines.append(f"说明：{comment[:160]}")
    if not success:
        lines.append(f"原因：{error[:180] or '飞书审批接口返回失败'}")
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green" if success else "red",
            "title": {"tag": "plain_text", "content": "审批已处理" if success else "处理失败"},
        },
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}}],
    }


def _single_approval_dismissed_card(*, item: dict[str, Any]) -> dict[str, Any]:
    lines = [
        "**暂不处理**",
        _approval_item_summary(item),
        "状态：仍在待审批列表中，可稍后从工作台继续处理。",
    ]
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "grey", "title": {"tag": "plain_text", "content": "暂不处理"}},
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}}],
    }


def _approval_action_comment(value: dict[str, Any]) -> str:
    form_value = value.get("form_value") if isinstance(value.get("form_value"), dict) else {}
    for key in ("approval_reason", "reason", "comment"):
        text = str(form_value.get(key) or "").strip()
        if text:
            return text
    return ""


def _approval_detail_action_target_hint(action: str, index: int) -> str:
    if action == "transfer":
        return f"转交需要指定接收人。你可以回复：转交第{index}个给王云飞，理由是请他处理。"
    if action == "add_sign":
        return f"加签需要指定加签人。你可以回复：加签第{index}个给王云飞，理由是请他一起审批。"
    return "这个操作还需要补充对象。"


def _append_approval_duration_hint(answer: str, *, duration_ms: int) -> str:
    if duration_ms < 3000:
        return answer
    label = "很慢" if duration_ms >= 8000 else "较慢"
    hint = f"慢点提示：审批接口耗时 {duration_ms}ms，{label}。建议检查审批详情接口、附件读取或飞书审批权限返回。"
    return f"{answer}\n\n{hint}" if answer else hint


def _batch_approval_confirmation_card(selected_items: list[dict[str, Any]], *, chat_id: str | None, pending_id: str = "") -> dict[str, Any]:
    total_amount = _selected_total_amount(selected_items)
    lines = [f"**确认批量通过 {len(selected_items)} 笔审批**"]
    if total_amount is not None:
        lines.append(f"总金额：{total_amount:g} 元")
    lines.append("")
    lines.append("审批清单：")
    for entry in selected_items[:10]:
        lines.append(f"{entry.get('index')}. {_approval_item_summary(entry.get('item') if isinstance(entry.get('item'), dict) else {})}")
    if len(selected_items) > 10:
        lines.append(f"还有 {len(selected_items) - 10} 笔未展示。")
    value_base = {
        "kind": "runtime_approval_batch_confirm",
        "chat_id": chat_id or "",
        "pending_action_id": pending_id,
        "confirmation_token": pending_id,
    }
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue", "title": {"tag": "plain_text", "content": "批量审批确认"}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": "点「确认」后会逐笔提交通过；点「取消」不会执行。"}},
            {
                "tag": "action",
                "layout": "flow",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "确认"},
                        "type": "primary",
                        "value": {**value_base, "action": "confirm"},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "取消"},
                        "type": "default",
                        "value": {**value_base, "action": "cancel"},
                    },
                ],
            },
        ],
    }


def _approval_item_summary(item: dict[str, Any]) -> str:
    title = _approval_display_title(item)
    applicant = str(item.get("applicant") or "").strip()
    amount = str(item.get("amount") or "").strip()
    parts = [title]
    if applicant:
        parts.append(applicant)
    if amount:
        parts.append(f"{amount}元")
    return "｜".join(parts)


def _approval_display_title(item: dict[str, Any]) -> str:
    title = str(item.get("title") or item.get("name") or "未命名审批").strip()
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    candidates = [
        title,
        str(raw.get("title") or ""),
        str(raw.get("name") or ""),
        str(raw.get("approval_name") or ""),
        str(raw.get("approval_definition_name") or ""),
    ]
    normalized = " ".join(candidates).lower()
    if "reserve fund" in normalized:
        return "借款申请"
    if "reimbursement" in normalized:
        return "费用报销"
    return title or "未命名审批"


def _selected_total_amount(selected_items: list[dict[str, Any]]) -> float | None:
    total = 0.0
    found = False
    for entry in selected_items:
        item = entry.get("item") if isinstance(entry.get("item"), dict) else {}
        amount = item.get("amount")
        try:
            total += float(amount)
            found = True
        except (TypeError, ValueError):
            continue
    return total if found else None


def _load_batch_selection(chat_id: str | None) -> list[int]:
    session_context = load_session_context(chat_id)
    raw = session_context.get(_BATCH_SELECTION_KEY)
    if not isinstance(raw, list):
        return []
    return sorted({int(item) for item in raw if str(item).isdigit() and int(item) > 0})


def _save_batch_selection(chat_id: str | None, indexes: list[int]) -> None:
    session_context = load_session_context(chat_id)
    session_context[_BATCH_SELECTION_KEY] = sorted({int(item) for item in indexes if int(item) > 0})
    save_session_context(chat_id, session_context)


def _toggle_approval_workbench_group(chat_id: str | None, group: str) -> None:
    session_context = load_session_context(chat_id)
    raw_groups = session_context.get(_APPROVAL_WORKBENCH_EXPANDED_GROUPS_KEY)
    expanded = {str(item) for item in raw_groups if str(item) in {"hold", "review", "pass"}} if isinstance(raw_groups, list) else set()
    if group in expanded:
        expanded.remove(group)
    else:
        expanded.add(group)
    session_context[_APPROVAL_WORKBENCH_EXPANDED_GROUPS_KEY] = sorted(expanded)
    save_session_context(chat_id, session_context)


def _approval_indexes_for_group(chat_id: str | None, group: str) -> list[int]:
    result_context = load_result_context(chat_id)
    if result_context is None or not result_context.items:
        return []
    return [
        index
        for index, item in enumerate(result_context.items, start=1)
        if isinstance(item, dict) and _approval_workbench_group_key(item) == group
    ]


def _approval_workbench_group_key(item: dict[str, Any]) -> str:
    assessment = item.get("assessment") if isinstance(item.get("assessment"), dict) else {}
    suggestion = str(assessment.get("suggestion") or "")
    if suggestion in {"可通过", "可初步通过"}:
        return "pass"
    if suggestion in {"拒绝", "补充后再审"}:
        return "hold"
    return "review"


def _clear_pending_batch(chat_id: str | None) -> None:
    session_context = load_session_context(chat_id)
    session_context.pop(_PENDING_BATCH_KEY, None)
    save_session_context(chat_id, session_context)


def _clear_batch_state(chat_id: str | None) -> None:
    session_context = load_session_context(chat_id)
    session_context.pop(_PENDING_BATCH_KEY, None)
    session_context.pop(_BATCH_SELECTION_KEY, None)
    save_session_context(chat_id, session_context)


def _save_current_approval_item(chat_id: str | None, *, index: int, item: dict[str, Any]) -> None:
    if not chat_id:
        return
    session_context = load_session_context(chat_id)
    session_context[_CURRENT_APPROVAL_KEY] = {"index": max(index - 1, 0), "item": item}
    save_session_context(chat_id, session_context)


def _clear_current_approval_item(chat_id: str | None) -> None:
    if not chat_id:
        return
    session_context = load_session_context(chat_id)
    session_context.pop(_CURRENT_APPROVAL_KEY, None)
    save_session_context(chat_id, session_context)


def _load_pending_single(chat_id: str | None) -> dict[str, Any] | None:
    session_context = load_session_context(chat_id)
    pending = session_context.get(_PENDING_SINGLE_KEY)
    return pending if isinstance(pending, dict) else None


def _save_pending_single(chat_id: str | None, pending: dict[str, Any]) -> None:
    session_context = load_session_context(chat_id)
    session_context[_PENDING_SINGLE_KEY] = {**_pending_expiry_payload(), **pending}
    save_session_context(chat_id, session_context)


def _clear_pending_single(chat_id: str | None) -> None:
    session_context = load_session_context(chat_id)
    session_context.pop(_PENDING_SINGLE_KEY, None)
    save_session_context(chat_id, session_context)


def _record_action_trace(chat_id: str | None, entry: dict[str, Any]) -> None:
    record_action_trace(chat_id, entry)


def _reply_target(*, chat_id: str | None, open_id: str | None) -> dict[str, str] | None:
    if chat_id:
        return {"receive_id_type": "chat_id", "receive_id": chat_id}
    if open_id:
        return {"receive_id_type": "open_id", "receive_id": open_id}
    return None


def _enqueue_runtime_card_reply(
    *,
    app_config: FeishuAppConfig,
    command: str,
    identity: Any,
    chat_id: str | None,
    reply_target: dict[str, str],
) -> None:
    from app.tasks.celery_app import celery_app

    celery_app.send_task(
        "bot.runtime.card_reply",
        args=[
            str(app_config.id),
            command,
            command_parser.normalize_command(command),
            {
                "open_id": getattr(identity, "open_id", None),
                "role": getattr(identity, "role", None),
                "access_scope": getattr(identity, "access_scope", None),
                "display_name": getattr(identity, "display_name", None),
                "source": getattr(identity, "source", None),
                "domains": list(getattr(identity, "domains", ()) or ()),
                "allowed_resources": list(getattr(identity, "allowed_resources", ()) or ()),
                "email": getattr(identity, "email", None),
            },
            chat_id,
            reply_target,
        ],
    )


def _enqueue_batch_approve(
    *,
    app_config: FeishuAppConfig,
    identity: Any,
    chat_id: str | None,
    reply_target: dict[str, str],
) -> None:
    from app.tasks.celery_app import celery_app

    celery_app.send_task(
        "bot.approvals.batch_approve",
        args=[
            str(app_config.id),
            {
                "open_id": getattr(identity, "open_id", None),
                "role": getattr(identity, "role", None),
                "access_scope": getattr(identity, "access_scope", None),
                "display_name": getattr(identity, "display_name", None),
                "source": getattr(identity, "source", None),
                "domains": list(getattr(identity, "domains", ()) or ()),
                "allowed_resources": list(getattr(identity, "allowed_resources", ()) or ()),
                "email": getattr(identity, "email", None),
            },
            chat_id,
            reply_target,
        ],
    )


def _format_index_list(indexes: list[int]) -> str:
    if not indexes:
        return "无"
    return "、".join(str(item) for item in indexes)


def _approval_workbench_update_card(chat_id: str | None) -> dict[str, Any] | None:
    result_context = load_result_context(chat_id)
    if result_context is None or not result_context.items:
        return build_interactive_card(
            card_hint="approval_workbench",
            route_path="feishu_approval_task_query",
            raw_answer="当前没有待审批任务。",
            chat_id=chat_id,
            title_override="待审批",
        )
    raw_answer = result_context.answer or _approval_workbench_answer_from_items(result_context.items)
    return build_interactive_card(
        card_hint="approval_workbench",
        route_path="feishu_approval_task_query",
        raw_answer=raw_answer,
        chat_id=chat_id,
        title_override=f"待审批 {len(result_context.items)} 条",
    )


def _approval_workbench_answer_from_items(items: tuple[dict[str, Any], ...]) -> str:
    lines = [f"你有 {len(items)} 条待审批："]
    for index, item in enumerate(items[:10], start=1):
        title = str(item.get("title") or item.get("name") or "审批")
        applicant = str(item.get("applicant") or item.get("applicant_name") or "").strip()
        amount = str(item.get("amount") or "").strip()
        parts = [f"{index}. {title}"]
        if amount:
            parts.append(f"{amount}元")
        if applicant:
            parts.append(applicant)
        lines.append("｜".join(parts))
    if len(items) > 10:
        lines.append(f"还有 {len(items) - 10} 条未展示。")
    return "\n".join(lines)


def _remove_approval_workbench_item(*, chat_id: str | None, index: int, item: dict[str, Any]) -> None:
    result_context = load_result_context(chat_id)
    if result_context is None or not result_context.items:
        return
    items = [dict(existing) for existing in result_context.items]
    position = index - 1
    if position < 0 or position >= len(items):
        target_id = str(item.get("id") or item.get("task_id") or item.get("instance_code") or "").strip()
        position = -1
        if target_id:
            for candidate_index, candidate in enumerate(items):
                candidate_id = str(candidate.get("id") or candidate.get("task_id") or candidate.get("instance_code") or "").strip()
                if candidate_id == target_id:
                    position = candidate_index
                    break
    if position < 0 or position >= len(items):
        return
    removed_index = position + 1
    del items[position]
    _save_batch_selection(
        chat_id,
        [selected - 1 if selected > removed_index else selected for selected in _load_batch_selection(chat_id) if selected != removed_index],
    )
    new_items = tuple(items)
    metadata = dict(result_context.metadata or {})
    metadata.update({"item_count": len(new_items), "display_count": len(new_items), "count": len(new_items)})
    save_result_context(
        chat_id,
        ResultContext(
            result_type=result_context.result_type,
            query_id=result_context.query_id,
            count=len(new_items),
            items=new_items,
            metadata=metadata,
            answer=_approval_workbench_answer_from_items(new_items) if new_items else "当前没有待审批任务。",
        ),
    )


async def _update_approval_workbench_message(
    *,
    app_config: FeishuAppConfig,
    message_id: str | None,
    chat_id: str | None,
) -> None:
    if not message_id:
        return
    card = _approval_workbench_update_card(chat_id)
    if not card:
        return
    await FeishuClient(app_config).update_message_content(message_id=message_id, content=card)


async def _update_approval_workbench_expired_message(
    *,
    app_config: FeishuAppConfig,
    message_id: str | None,
) -> None:
    if not message_id:
        return
    card = {
        "config": {"wide_screen_mode": False},
        "header": {
            "title": {"tag": "plain_text", "content": "待审批"},
            "template": "grey",
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "**这张审批卡片已失效**\n为避免误操作，请重新发送「等待我审批的单子」获取最新列表。",
                },
            }
        ],
    }
    await FeishuClient(app_config).update_message_content(message_id=message_id, content=card)


def _card_action_chat_id(payload: dict[str, Any]) -> str | None:
    card_action = parse_gateway_card_action(payload)
    if card_action is None:
        return None
    return card_action.chat_id or str(card_action.value.get("chat_id") or "").strip() or None
