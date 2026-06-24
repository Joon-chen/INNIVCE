from app.services.conversation_context import conversation_context_text, load_conversation_context, record_conversation_turn
from app.services.runtime_v5.models import ResultContext


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
