import asyncio
from types import SimpleNamespace

from app.services.feishu import approval_runtime


def test_approval_items_from_context_returns_cached_items_without_live_fetch() -> None:
    calls = []

    async def fetch_pending(*args, **kwargs):
        calls.append("fetch")
        return {"available": True, "items": [{"approval_name": "付款审批"}]}

    items = asyncio.run(
        approval_runtime.approval_items_from_context_or_live(
            None,
            SimpleNamespace(),
            SimpleNamespace(),
            chat_id="oc_1",
            load_context=lambda *args: [{"approval_name": "缓存审批"}],
            fetch_pending_tasks=fetch_pending,
            register_resources=lambda *args: calls.append("register"),
            attach_synced_attachments=lambda *args: calls.append("synced"),
            ensure_attachment_summaries=lambda *args: _async_call(calls, "attachments"),
            attach_history_context=lambda *args: calls.append("history"),
            attach_llm_advice=lambda *args: _async_call(calls, "llm"),
            store_context=lambda *args: calls.append("store"),
        )
    )

    assert items == [{"approval_name": "缓存审批"}]
    assert calls == []


def test_approval_items_from_live_runs_enrichment_pipeline() -> None:
    calls = []
    live_items = [{"approval_name": "付款审批"}]

    async def fetch_pending(*args, **kwargs):
        calls.append(("fetch", kwargs["limit"]))
        return {"available": True, "items": live_items}

    items = asyncio.run(
        approval_runtime.approval_items_from_context_or_live(
            None,
            SimpleNamespace(),
            SimpleNamespace(),
            chat_id="oc_1",
            load_context=lambda *args: [],
            fetch_pending_tasks=fetch_pending,
            register_resources=lambda *args: calls.append("register"),
            attach_synced_attachments=lambda *args: calls.append("synced"),
            ensure_attachment_summaries=lambda *args: _async_call(calls, "attachments"),
            attach_history_context=lambda *args: calls.append("history"),
            attach_llm_advice=lambda *args: _async_call(calls, "llm"),
            store_context=lambda *args: calls.append("store"),
        )
    )

    assert items is live_items
    assert calls == [("fetch", 8), "register", "synced", "attachments", "history", "llm", "store"]


def test_recent_approvals_reply_runs_live_enrichment_and_completed_hint() -> None:
    calls = []
    live_items = [{"approval_name": "付款审批"}]
    completed_events = [SimpleNamespace(title="已通过审批")]

    async def fetch_pending(*args, **kwargs):
        calls.append(("fetch", kwargs["limit"]))
        return {"available": True, "items": live_items}

    def approval_events_by_status(*args, **kwargs):
        calls.append(("events", kwargs["statuses"], kwargs["limit"]))
        return completed_events

    reply = asyncio.run(
        approval_runtime.recent_approvals_reply(
            None,
            SimpleNamespace(),
            SimpleNamespace(),
            chat_id="oc_1",
            limit=8,
            fetch_pending_tasks=fetch_pending,
            register_resources=lambda *args: calls.append("register"),
            attach_synced_attachments=lambda *args: calls.append("synced"),
            ensure_attachment_summaries=lambda *args: _async_call(calls, "attachments"),
            attach_history_context=lambda *args: calls.append("history"),
            attach_llm_advice=lambda *args: _async_call(calls, "llm"),
            store_context=lambda *args: calls.append("store"),
            format_pending_tasks=lambda items: ["待审批：付款审批"],
            approval_events_by_status=approval_events_by_status,
            completed_statuses={"approved"},
            pending_statuses={"pending"},
            format_event_lines=lambda events: [event.title for event in events],
            recent_approval_events=lambda *args, **kwargs: [],
        )
    )

    assert "待审批：付款审批" in reply
    assert "最近完成的审批记录" in reply
    assert "已通过审批" in reply
    assert calls == [
        ("fetch", 8),
        "register",
        "synced",
        "attachments",
        "history",
        "llm",
        "store",
        ("events", {"approved"}, 3),
    ]


def test_recent_approvals_reply_falls_back_to_local_pending_events() -> None:
    pending_events = [SimpleNamespace(title="本地未完成审批")]

    async def fetch_pending(*args, **kwargs):
        return {"available": False, "items": [], "error": "approval api unavailable"}

    reply = asyncio.run(
        approval_runtime.recent_approvals_reply(
            None,
            SimpleNamespace(),
            SimpleNamespace(),
            chat_id="oc_1",
            limit=5,
            fetch_pending_tasks=fetch_pending,
            register_resources=lambda *args: None,
            attach_synced_attachments=lambda *args: None,
            ensure_attachment_summaries=lambda *args: _async_call([], "attachments"),
            attach_history_context=lambda *args: None,
            attach_llm_advice=lambda *args: _async_call([], "llm"),
            store_context=lambda *args: None,
            format_pending_tasks=lambda items: [],
            approval_events_by_status=lambda *args, **kwargs: pending_events,
            completed_statuses={"approved"},
            pending_statuses={"pending"},
            format_event_lines=lambda events: [event.title for event in events],
            recent_approval_events=lambda *args, **kwargs: [],
        )
    )

    assert "本地审批实例状态兜底" in reply
    assert "本地未完成审批" in reply
    assert "approval api unavailable" in reply


def test_approval_detail_reply_requires_selector_when_multiple_items() -> None:
    async def load_items(*args, **kwargs):
        return [{"approval_name": "付款审批"}, {"approval_name": "报销审批"}]

    reply = asyncio.run(
        approval_runtime.approval_detail_reply(
            None,
            SimpleNamespace(),
            SimpleNamespace(),
            chat_id="oc_1",
            selector_text="",
            load_items=load_items,
            select_item=lambda items, selector: None,
            attachment_refs=lambda form: [],
            attachment_results=lambda item: [],
            format_detail_lines=lambda item, **kwargs: ["详情"],
        )
    )

    assert "展开第N条" in reply


def test_approval_advice_reply_builds_single_item_advice() -> None:
    async def load_items(*args, **kwargs):
        return [{"approval_name": "付款审批", "instance_detail": {"form": "[]"}}]

    reply = asyncio.run(
        approval_runtime.approval_advice_reply(
            None,
            SimpleNamespace(),
            SimpleNamespace(),
            chat_id="oc_1",
            load_items=load_items,
            form_fields=lambda form: [("付款金额", "3000"), ("付款事由", "采购")],
            amount_value=lambda fields: 3000,
            first_matching=lambda fields, names: fields.get(names[0]),
            applicant_name=lambda item: "王东升",
            approval_name=lambda item: "付款审批",
            attachment_refs=lambda form: [],
            attachment_results=lambda item: [],
            decision_for_item=lambda *args, **kwargs: ("可通过", "金额和事由清晰"),
            attachment_basis=lambda results: "",
        )
    )

    assert "我基于上一笔待审批看了一下：付款审批。" in reply
    assert "申请人：王东升" in reply
    assert "建议：可通过。理由：金额和事由清晰" in reply


async def _async_call(calls, value):
    calls.append(value)
