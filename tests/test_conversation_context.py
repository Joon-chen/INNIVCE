from app.services.conversation_context import (
    conversation_context_pack,
    conversation_context_text,
    load_conversation_context,
    record_conversation_turn,
)
from app.services.runtime_v5.models import ResultContext
from app.services.user_context_pack import build_user_context_pack


def test_record_conversation_turn_saves_recent_runtime_session(monkeypatch) -> None:
    saved = {}

    monkeypatch.setattr("app.services.conversation_context.load_session_context", lambda chat_id: {"portal": {"company_id": "c1"}})
    monkeypatch.setattr("app.services.conversation_context.save_session_context", lambda chat_id, payload: saved.update({"chat_id": chat_id, "payload": payload}))

    record_conversation_turn(
        chat_id="oc_1",
        user_text="主营业务",
        assistant_text="你是想了解公司主营业务吗？",
        route_path="smalltalk",
        route_label="闲聊",
        message_type="text",
    )

    assert saved["chat_id"] == "oc_1"
    assert saved["payload"]["portal"] == {"company_id": "c1"}
    assert saved["payload"]["conversation_turns"] == [
        {
            "user": "主营业务",
            "assistant": "你是想了解公司主营业务吗？",
            "route_path": "smalltalk",
            "route_label": "闲聊",
            "message_type": "text",
        }
    ]


def test_conversation_context_renders_recent_turns_and_non_text_message(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.conversation_context.load_session_context",
        lambda chat_id: {
            "conversation_turns": [
                {
                    "user": "主营业务",
                    "assistant": "这听起来像在问公司主营业务。",
                    "message_type": "text",
                },
                {
                    "user": "",
                    "assistant": "我看到你发了一条表情消息。",
                    "message_type": "sticker",
                },
            ]
        },
    )
    monkeypatch.setattr("app.services.conversation_context.load_result_context", lambda chat_id: None)

    context = conversation_context_text("oc_1")

    assert "用户：主营业务" in context
    assert "助手：这听起来像在问公司主营业务。" in context
    assert "用户：[上一条是 sticker 类型消息]" in context


def test_conversation_context_includes_latest_runtime_result(monkeypatch) -> None:
    monkeypatch.setattr("app.services.conversation_context.load_session_context", lambda chat_id: {"conversation_turns": []})
    monkeypatch.setattr(
        "app.services.conversation_context.load_result_context",
        lambda chat_id: ResultContext(result_type="approval_list", count=3, answer="待审批 3 条。"),
    )

    context = load_conversation_context("oc_1")
    rendered = context.render_for_llm()

    assert context.last_result_type == "approval_list"
    assert "最近业务结果：approval_list，数量 3。" in rendered
    assert "最近业务摘要：待审批 3 条。" in rendered


def test_context_pack_omits_history_for_non_referral_command(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.conversation_context.load_session_context",
        lambda chat_id: {
            "conversation_turns": [
                {"user": "全公司任务", "assistant": "当前只能展示聚合。", "message_type": "text"}
            ]
        },
    )
    monkeypatch.setattr(
        "app.services.conversation_context.load_result_context",
        lambda chat_id: ResultContext(result_type="task_list", count=1, answer="任务 1 条。"),
    )

    pack = conversation_context_pack("oc_1", question="你好", purpose="command")

    assert pack.text == ""
    assert pack.included_turn_count == 0
    assert pack.result_included is False


def test_context_pack_includes_result_for_referral_question(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.conversation_context.load_session_context",
        lambda chat_id: {
            "conversation_turns": [
                {"user": "全公司任务", "assistant": "当前只能展示聚合。", "message_type": "text"}
            ]
        },
    )
    monkeypatch.setattr(
        "app.services.conversation_context.load_result_context",
        lambda chat_id: ResultContext(result_type="task_list", count=1, answer="任务 1 条。"),
    )

    pack = conversation_context_pack("oc_1", question="展开这个", purpose="command")

    assert "用户：全公司任务" in pack.text
    assert "最近业务结果：task_list，数量 1。" in pack.text
    assert pack.included_turn_count == 1
    assert pack.result_included is True


def test_conversation_context_pack_extracts_interaction_signals(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.conversation_context.load_session_context",
        lambda chat_id: {
            "conversation_turns": [
                {"user": "你回答太机械了，不要老列菜单", "assistant": "收到。", "message_type": "text"}
            ]
        },
    )
    monkeypatch.setattr("app.services.conversation_context.load_result_context", lambda chat_id: None)

    pack = conversation_context_pack("oc_1", question="不要直呼我名字", purpose="conversation")

    assert "对话信号：" in pack.text
    assert "tone=warmer_and_less_mechanical" in pack.text
    assert "avoid_menu_repetition=true" in pack.text
    assert "addressing_style=less_formal" in pack.text
    assert pack.signal_count == 3


def test_conversation_context_pack_includes_recent_result_for_conversation(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.conversation_context.load_session_context",
        lambda chat_id: {
            "conversation_turns": [
                {"user": "查看我的任务", "assistant": "你有 2 条任务。", "message_type": "text"}
            ]
        },
    )
    monkeypatch.setattr(
        "app.services.conversation_context.load_result_context",
        lambda chat_id: ResultContext(result_type="task_list", count=2, answer="你有 2 条任务。"),
    )

    pack = conversation_context_pack("oc_1", question="那我接下来先做什么", purpose="conversation")

    assert pack.result_included is True
    assert "可用最近业务结果：task_list，数量 2。" in pack.text
    assert pack.quality()["signal_count"] == 0


def test_user_context_pack_unifies_facts_profile_conversation_and_result(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.conversation_context.load_session_context",
        lambda chat_id: {
            "conversation_turns": [
                {"user": "不要直呼我名字", "assistant": "收到。", "message_type": "text"}
            ]
        },
    )
    monkeypatch.setattr(
        "app.services.conversation_context.load_result_context",
        lambda chat_id: ResultContext(result_type="task_list", count=2, answer="你有 2 条任务。"),
    )
    monkeypatch.setattr(
        "app.services.profile_context.load_profile_payload",
        lambda open_id: {
            "preferred_address": "陈总",
            "avoid_direct_name": True,
            "tone_tips": "更直接一点",
        },
    )
    runtime_context = type(
        "RuntimeContext",
        (),
        {
            "identity": type(
                "Identity",
                (),
                {
                    "open_id": "ou_1",
                    "display_name": "陈俊",
                    "role": "owner",
                    "job_title": "CEO",
                    "department_names": ("管理层",),
                    "domains": ("all",),
                },
            )(),
            "runtime_scope": type("Scope", (), {"active_company_id": "company_1"})(),
            "chat_id": "oc_1",
            "result_context": ResultContext(result_type="task_list", count=2, answer="你有 2 条任务。"),
        },
    )()

    pack = build_user_context_pack(runtime_context=runtime_context, question="展开这个", purpose="command")
    text = pack.prompt_sections()

    assert "actor_display_name=陈俊" in text
    assert "用户称呼：陈俊" in text
    assert "称呼用户为「陈总」" in text
    assert "不要直呼用户真实姓名" in text
    assert "用户：不要直呼我名字" in text
    assert "最近业务结果：task_list，数量 2。" in text
    assert pack.references_previous is True
