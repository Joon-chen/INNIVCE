import asyncio
from types import SimpleNamespace

from app.services.feishu import approval_actions


def test_missing_approval_action_fields_accepts_alternate_codes() -> None:
    item = {"process_code": "instance_1", "task_id": "task_1"}

    assert approval_actions.missing_approval_action_fields(item) == []


def test_prepare_approval_action_reply_requires_single_selected_item() -> None:
    async def load_items(*args, **kwargs):
        return [
            {"approval_code": "approval_1", "instance_code": "instance_1", "task_id": "task_1"},
            {"approval_code": "approval_2", "instance_code": "instance_2", "task_id": "task_2"},
        ]

    reply = asyncio.run(
        approval_actions.prepare_approval_action_reply(
            None,
            SimpleNamespace(),
            SimpleNamespace(),
            chat_id="oc_1",
            action="approve",
            load_items=load_items,
            select_item=lambda items, selector: None,
            store_pending_action=lambda *args: None,
            format_pending_tasks=lambda items: ["审批摘要"],
        )
    )

    assert "不会批量审批" in reply


def test_prepare_approval_action_reply_stores_internal_confirmation_token() -> None:
    stored = {}
    identity = SimpleNamespace(open_id="ou_owner")
    item = {"approval_code": "approval_1", "instance_code": "instance_1", "task_id": "task_1"}

    async def load_items(*args, **kwargs):
        return [item]

    def store_pending_action(app_config, actor, chat_id, pending_item, action):
        stored["item"] = pending_item
        stored["action"] = action
        stored["chat_id"] = chat_id

    reply = asyncio.run(
        approval_actions.prepare_approval_action_reply(
            None,
            SimpleNamespace(),
            identity,
            chat_id="oc_1",
            action="approve",
            load_items=load_items,
            select_item=lambda items, selector: items[0],
            store_pending_action=store_pending_action,
            format_pending_tasks=lambda items: ["审批摘要"],
        )
    )

    assert "还没有真正提交" in reply
    assert stored["action"] == "approve"
    assert stored["chat_id"] == "oc_1"
    assert stored["item"]["_confirmation_token"] == approval_actions.approval_action_confirmation_token(
        identity,
        item,
        "approve",
    )


def test_execute_pending_approval_action_reply_clears_context_after_success() -> None:
    calls = []
    audits = []
    identity = SimpleNamespace(open_id="ou_owner")
    item = {"approval_code": "approval_1", "instance_code": "instance_1", "task_id": "task_1"}
    item["_confirmation_token"] = approval_actions.approval_action_confirmation_token(identity, item, "reject")

    async def execute_action(*args, **kwargs):
        calls.append(("execute", kwargs["action"]))
        return {"ok": True}

    reply = asyncio.run(
        approval_actions.execute_pending_approval_action_reply(
            SimpleNamespace(),
            identity,
            chat_id="oc_1",
            expected_action="reject",
            load_pending_action=lambda *args: {
                "action": "reject",
                "item": item,
            },
            execute_action=execute_action,
            clear_pending_action=lambda *args: calls.append(("clear_pending", args[2])),
            clear_approval_context=lambda *args: calls.append(("clear_context", args[2])),
            audit_action=lambda *args: audits.append(args),
        )
    )

    assert reply.startswith("已提交拒绝")
    assert calls == [("execute", "reject"), ("clear_pending", "oc_1"), ("clear_context", "oc_1")]
    assert len(audits) == 1
    assert audits[0][2] == "oc_1"
    assert audits[0][4] == "reject"
    assert audits[0][5] == {"ok": True}
    assert "_confirmation_token" not in audits[0][3]


def test_execute_pending_approval_action_reply_rejects_changed_confirmation_params() -> None:
    calls = []
    audits = []
    identity = SimpleNamespace(open_id="ou_owner")
    item = {"approval_code": "approval_1", "instance_code": "instance_1", "task_id": "task_1"}
    item["_confirmation_token"] = approval_actions.approval_action_confirmation_token(identity, item, "approve")
    item["task_id"] = "task_2"

    async def execute_action(*args, **kwargs):
        calls.append(("execute", kwargs["action"]))
        return {"ok": True}

    reply = asyncio.run(
        approval_actions.execute_pending_approval_action_reply(
            SimpleNamespace(),
            identity,
            chat_id="oc_1",
            expected_action="approve",
            load_pending_action=lambda *args: {"action": "approve", "item": item},
            execute_action=execute_action,
            clear_pending_action=lambda *args: calls.append(("clear_pending", args[2])),
            clear_approval_context=lambda *args: calls.append(("clear_context", args[2])),
            audit_action=lambda *args: audits.append(args),
        )
    )

    assert "参数已变化" in reply
    assert calls == [("clear_pending", "oc_1")]
    assert len(audits) == 1
    assert audits[0][4] == "approve"
    assert audits[0][5]["status"] == "denied"
    assert audits[0][5]["error"] == "confirmation_token_mismatch"
