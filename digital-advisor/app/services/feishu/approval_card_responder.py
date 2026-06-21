import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.services.feishu.approval_cards import approval_card_action_value, approval_card_message_id, approval_card_toast


logger = logging.getLogger(__name__)

IdentityGetter = Callable[[Any, Any, dict[str, Any]], Any]
PermissionReply = Callable[[Any, str], str]
ChatIdGetter = Callable[[dict[str, Any]], str | None]
TextShortener = Callable[[str, int], str]
ApprovalDetailReply = Callable[[Any, Any, Any], Awaitable[str]]
ApprovalActionReply = Callable[[Any, Any, Any], Awaitable[str]]
ApprovalContextLoader = Callable[[Any, Any, str | None], list[dict[str, Any]]]
ApprovalCardBuilder = Callable[..., dict[str, Any]]
TextReplySender = Callable[..., Awaitable[Any]]
MessageContentUpdater = Callable[..., Awaitable[Any]]


def is_approval_card_action(payload: dict[str, Any]) -> bool:
    value = approval_card_action_value(payload)
    return value.get("kind") == "approval_action"


async def handle_card_action_response(
    db: Any,
    app_config: Any,
    payload: dict[str, Any],
    *,
    get_sender_identity: IdentityGetter,
    permission_denied_reply: PermissionReply,
    get_chat_id: ChatIdGetter,
    short_text: TextShortener,
    load_approval_context: ApprovalContextLoader,
    build_approval_action_card: ApprovalCardBuilder,
    approval_detail_reply: ApprovalDetailReply,
    prepare_approval_action_reply: ApprovalActionReply,
    send_text_reply: TextReplySender,
    update_message_content: MessageContentUpdater,
    client_factory: Callable[..., Any],
) -> dict[str, Any] | None:
    if not is_approval_card_action(payload):
        return None
    value = approval_card_action_value(payload)
    identity = get_sender_identity(db, app_config, payload)
    if not identity.can_query_approvals():
        return approval_card_toast(permission_denied_reply(identity, "审批操作"), toast_type="warning", shorten=short_text)

    chat_id = str(value.get("chat_id") or get_chat_id(payload) or "") or None
    index = int(value.get("index") or 0)
    action = str(value.get("action") or "")
    logger.info("Approval card callback response: action=%s index=%s chat_id=%s", action, index, chat_id)

    if action in {"detail", "collapse"}:
        updated = await update_approval_card_detail(
            app_config,
            identity,
            payload,
            value,
            chat_id=chat_id,
            expanded_index=index if action == "detail" else None,
            load_approval_context=load_approval_context,
            build_approval_action_card=build_approval_action_card,
            update_message_content=update_message_content,
            client_factory=client_factory,
        )
        if not updated:
            return approval_card_toast("审批上下文已过期，请重新发送“待审批”。", toast_type="warning", shorten=short_text)
        return {}

    handled = await handle_approval_card_action(
        db,
        app_config,
        payload,
        get_sender_identity=get_sender_identity,
        permission_denied_reply=permission_denied_reply,
        get_chat_id=get_chat_id,
        approval_detail_reply=approval_detail_reply,
        prepare_approval_action_reply=prepare_approval_action_reply,
        send_text_reply=send_text_reply,
        client_factory=client_factory,
    )
    if handled:
        return approval_card_toast("已收到，正在处理。", shorten=short_text)
    return approval_card_toast("这个审批按钮动作我暂时无法识别。", toast_type="warning", shorten=short_text)


async def handle_approval_card_action(
    db: Any,
    app_config: Any,
    payload: dict[str, Any],
    *,
    get_sender_identity: IdentityGetter,
    permission_denied_reply: PermissionReply,
    get_chat_id: ChatIdGetter,
    approval_detail_reply: ApprovalDetailReply,
    prepare_approval_action_reply: ApprovalActionReply,
    send_text_reply: TextReplySender,
    client_factory: Callable[..., Any],
) -> bool:
    value = approval_card_action_value(payload)
    identity = get_sender_identity(db, app_config, payload)
    if not identity.can_query_approvals():
        reply = permission_denied_reply(identity, "审批操作")
    else:
        chat_id = str(value.get("chat_id") or get_chat_id(payload) or "") or None
        index = int(value.get("index") or 0)
        selector = f"第{index}条" if index > 0 else ""
        action = str(value.get("action") or "")
        logger.info("Approval card action received: action=%s index=%s chat_id=%s", action, index, chat_id)
        if action == "detail":
            reply = await approval_detail_reply(db, app_config, identity, chat_id=chat_id, selector_text=selector)
        elif action == "collapse":
            return True
        elif action == "approve":
            reply = await prepare_approval_action_reply(
                db,
                app_config,
                identity,
                chat_id=chat_id,
                action="approve",
                selector_text=selector,
            )
        elif action == "reject":
            reply = await prepare_approval_action_reply(
                db,
                app_config,
                identity,
                chat_id=chat_id,
                action="reject",
                selector_text=selector,
            )
        else:
            reply = "这个审批按钮动作我暂时无法识别。"
    receive_id_type = str(value.get("receive_id_type") or "chat_id")
    receive_id = str(value.get("receive_id") or value.get("chat_id") or identity.open_id or "")
    if not receive_id:
        return False
    await send_text_reply(
        app_config=app_config,
        reply_target={"receive_id_type": receive_id_type, "receive_id": receive_id},
        text=reply,
        client_factory=client_factory,
    )
    return True


async def update_approval_card_detail(
    app_config: Any,
    identity: Any,
    payload: dict[str, Any],
    value: dict[str, Any],
    *,
    chat_id: str | None,
    expanded_index: int | None,
    load_approval_context: ApprovalContextLoader,
    build_approval_action_card: ApprovalCardBuilder,
    update_message_content: MessageContentUpdater,
    client_factory: Callable[..., Any],
) -> bool:
    message_id = approval_card_message_id(payload)
    if not message_id:
        return False
    items = load_approval_context(app_config, identity, chat_id)
    if not items:
        return False
    try:
        await update_message_content(
            app_config=app_config,
            message_id=message_id,
            content=build_approval_action_card(
                items,
                chat_id=chat_id,
                receive_id_type=str(value.get("receive_id_type") or "chat_id"),
                receive_id=str(value.get("receive_id") or value.get("chat_id") or identity.open_id or ""),
                actor_open_id=identity.open_id,
                expanded_index=expanded_index,
            ),
            client_factory=client_factory,
        )
    except Exception:
        return False
    return True
