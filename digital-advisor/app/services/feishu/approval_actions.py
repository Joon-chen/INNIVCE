from collections.abc import Awaitable, Callable
import hashlib
import json
from typing import Any

ApprovalItemsLoader = Callable[[Any, Any, Any], Awaitable[list[dict[str, Any]]]]
ApprovalSelector = Callable[[list[dict[str, Any]], str], dict[str, Any] | None]
PendingActionStore = Callable[[Any, Any, str | None, dict[str, Any], str], None]
PendingActionLoad = Callable[[Any, Any, str | None], dict[str, Any] | None]
PendingActionClear = Callable[[Any, Any, str | None], None]
ApprovalContextClear = Callable[[Any, Any, str | None], None]
PendingTasksFormatter = Callable[[list[dict[str, Any]]], list[str]]
ApprovalActionAudit = Callable[[Any, Any, str | None, dict[str, Any], str, dict[str, Any]], None]

CONFIRMATION_TOKEN_FIELD = "_confirmation_token"


async def prepare_approval_action_reply(
    db: Any,
    app_config: Any,
    identity: Any,
    *,
    chat_id: str | None,
    action: str,
    selector_text: str | None = None,
    load_items: ApprovalItemsLoader,
    select_item: ApprovalSelector,
    store_pending_action: PendingActionStore,
    format_pending_tasks: PendingTasksFormatter,
) -> str:
    items = await load_items(db, app_config, identity, chat_id=chat_id)
    if not items:
        return "我现在没有可操作的待审批上下文。你先问“帮我查下待我审批的单子”，我会拿到单号和任务 ID 后再处理。"
    selected = select_item(items, selector_text or "")
    if selected is not None:
        items = [selected]
    if len(items) > 1:
        return "你当前有多笔待审批。为了避免误操作，请回复“通过第N条”或“拒绝第N条”；现在我不会批量审批。"

    item = items[0]
    missing = missing_approval_action_fields(item)
    if missing:
        return f"这笔审批还缺少执行字段：{', '.join(missing)}。我能展示明细，但暂时不能代提交。"

    pending_item = dict(item)
    pending_item[CONFIRMATION_TOKEN_FIELD] = approval_action_confirmation_token(identity, item, action)
    store_pending_action(app_config, identity, chat_id, pending_item, action)
    action_text = "同意" if action == "approve" else "拒绝"
    summary = "\n".join(format_pending_tasks([item])[:8])
    return (
        f"我已准备好将下面这笔审批提交为“{action_text}”，但还没有真正提交：\n"
        f"{summary}\n\n"
        f"请回复“确认{action_text}”后我再调用飞书接口提交；回复“取消”则放弃。"
    )


async def execute_pending_approval_action_reply(
    app_config: Any,
    identity: Any,
    *,
    chat_id: str | None,
    expected_action: str,
    load_pending_action: PendingActionLoad,
    execute_action: Callable[..., Awaitable[dict[str, Any]]],
    clear_pending_action: PendingActionClear,
    clear_approval_context: ApprovalContextClear,
    audit_action: ApprovalActionAudit | None = None,
) -> str:
    pending = load_pending_action(app_config, identity, chat_id)
    if not pending:
        return "我没有找到待确认的审批操作。你可以先说“帮我审批通过”，我会准备好后再让你确认。"
    action = pending.get("action")
    if action != expected_action:
        _audit_approval_action(
            audit_action,
            app_config,
            identity,
            chat_id,
            pending.get("item") if isinstance(pending.get("item"), dict) else {},
            str(action or expected_action),
            {"ok": False, "status": "denied", "error": "action_mismatch", "expected_action": expected_action},
        )
        clear_pending_action(app_config, identity, chat_id)
        return "你确认的动作和刚才准备的动作不一致。为避免误操作，我已停止；请重新发起审批操作。"
    item = pending.get("item") if isinstance(pending.get("item"), dict) else {}
    if not approval_action_confirmation_matches(identity, item, action):
        _audit_approval_action(
            audit_action,
            app_config,
            identity,
            chat_id,
            item,
            action,
            {"ok": False, "status": "denied", "error": "confirmation_token_mismatch"},
        )
        clear_pending_action(app_config, identity, chat_id)
        return "待确认审批参数已变化。为避免误操作，我已停止；请重新发起审批操作。"
    result = await execute_action(app_config, identity, item, action=action)
    _audit_approval_action(audit_action, app_config, identity, chat_id, item, action, result)
    if result.get("ok"):
        clear_pending_action(app_config, identity, chat_id)
        clear_approval_context(app_config, identity, chat_id)
        action_text = "同意" if action == "approve" else "拒绝"
        return f"已提交{action_text}。飞书审批状态可能需要几秒刷新。"
    return f"提交失败：{result.get('error') or '飞书接口未返回成功'}"


def missing_approval_action_fields(item: dict[str, Any]) -> list[str]:
    checks = {
        "instance_code": item.get("instance_code") or item.get("process_code"),
        "task_id": item.get("task_id"),
    }
    return [key for key, value in checks.items() if not value]


def approval_action_confirmation_token(identity: Any, item: dict[str, Any], action: str) -> str:
    payload = {
        "action": action,
        "actor": getattr(identity, "open_id", None) or getattr(identity, "email", None) or "",
        "item": {
            "instance_code": item.get("instance_code") or item.get("process_code") or "",
            "task_id": item.get("task_id") or "",
        },
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def approval_action_confirmation_matches(identity: Any, item: dict[str, Any], action: str) -> bool:
    expected = approval_action_confirmation_token(identity, item, action)
    actual = str(item.get(CONFIRMATION_TOKEN_FIELD) or "").strip()
    return bool(actual) and actual == expected


def _approval_action_item_without_internal_fields(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if not str(key).startswith("_")}


def _audit_approval_action(
    audit_action: ApprovalActionAudit | None,
    app_config: Any,
    identity: Any,
    chat_id: str | None,
    item: dict[str, Any],
    action: str,
    result: dict[str, Any],
) -> None:
    if audit_action is None:
        return
    audit_action(app_config, identity, chat_id, _approval_action_item_without_internal_fields(item), action, result)
