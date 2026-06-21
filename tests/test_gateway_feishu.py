import asyncio
from types import SimpleNamespace
from uuid import uuid4

from app.api.routes.feishu_event_routes import receive_event
from app.services.feishu.commands import (
    _agent_runtime_tool_steps,
    _agent_runtime_trace_summary,
    _agent_runtime_workflow_steps,
    _should_send_thinking_notice,
    _thinking_notice_text,
    handle_feishu_command,
    handle_feishu_command_result,
)
from app.services.feishu_event_entrypoint import receive_feishu_event_payload
from app.services.feishu.identity import BotIdentity
from app.services.gateway.audit import write_gateway_message_audit
from app.services.gateway.card_actions import gateway_card_action_message_id, gateway_card_action_value, parse_gateway_card_action
from app.services.gateway.card_responder import (
    GatewayCardResponder,
    dispatch_gateway_card_action_message,
    dispatch_gateway_card_action_response,
)
from app.services.gateway.feishu import build_feishu_gateway_message, feishu_url_verification_challenge, should_reply_to_feishu_message
from app.services.gateway.message import GatewayMessageKind
from app.services.gateway.responder import send_feishu_interactive_reply, send_feishu_text_reply, update_feishu_message_content


def test_build_feishu_gateway_message_from_official_nested_event() -> None:
    payload = {
        "schema": "2.0",
        "header": {
            "event_id": "evt_1",
            "event_type": "im.message.receive_v1",
            "app_id": "cli_1",
            "tenant_key": "tenant_1",
        },
        "event": {
            "sender": {"sender_id": {"open_id": "ou_1"}, "sender_type": "user"},
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_1",
                "chat_type": "group",
                "message_type": "text",
                "content": '{"text":"大飞哥 总结一下"}',
                "mentions": [{"name": "大飞哥"}],
            },
        },
    }

    message = build_feishu_gateway_message(payload)

    assert message.kind == GatewayMessageKind.MESSAGE
    assert message.actor.open_id == "ou_1"
    assert message.context.event_id == "evt_1"
    assert message.context.event_type == "im.message.receive_v1"
    assert message.context.chat_id == "oc_1"
    assert message.context.message_id == "om_1"
    assert message.text == "大飞哥 总结一下"
    assert message.reply_target and message.reply_target.as_dict() == {
        "receive_id_type": "chat_id",
        "receive_id": "oc_1",
    }
    assert should_reply_to_feishu_message(message, app_id="cli_1", bot_names={"大飞哥"})


def test_write_gateway_message_audit_records_safe_message_summary() -> None:
    message = build_feishu_gateway_message(
        {
            "schema": "2.0",
            "header": {
                "event_id": "evt_1",
                "event_type": "im.message.receive_v1",
                "app_id": "cli_1",
                "tenant_key": "tenant_1",
            },
            "event": {
                "sender": {"sender_id": {"open_id": "ou_1"}, "sender_type": "user"},
                "message": {
                    "message_id": "om_1",
                    "chat_id": "oc_1",
                    "chat_type": "group",
                    "message_type": "text",
                    "content": '{"text":"大飞哥 查一下敏感审批"}',
                    "mentions": [{"name": "大飞哥"}],
                },
            },
        }
    )

    class FakeDb:
        def __init__(self):
            self.added = []

        def add(self, item):
            self.added.append(item)

    db = FakeDb()
    audit = write_gateway_message_audit(db, company_id=uuid4(), message=message, status="handled", handled=True)

    assert db.added == [audit]
    assert audit.action == "gateway.feishu.message"
    assert audit.actor == "ou_1"
    assert audit.target_type == "gateway_message"
    assert audit.target_id == "om_1"
    assert audit.payload["status"] == "handled"
    assert audit.payload["handled"] is True
    assert audit.payload["chat_id"] == "oc_1"
    assert audit.payload["reply_target"] == {"receive_id_type": "chat_id", "receive_id": "oc_1"}
    assert audit.payload["has_text"] is True
    assert "raw_payload" not in audit.payload
    assert "查一下敏感审批" not in str(audit.payload)


def test_build_feishu_gateway_message_from_lark_cli_flat_event() -> None:
    payload = {
        "type": "im.message.receive_v1",
        "event_id": "evt_2",
        "message_id": "om_2",
        "sender_id": "ou_2",
        "chat_id": "oc_2",
        "chat_type": "p2p",
        "message_type": "text",
        "content": "最近审批",
    }

    message = build_feishu_gateway_message(payload)

    assert message.actor.open_id == "ou_2"
    assert message.context.chat_id == "oc_2"
    assert message.context.message_id == "om_2"
    assert message.text == "最近审批"
    assert should_reply_to_feishu_message(message, app_id="cli_1", bot_names={"大飞哥"})


def test_parse_gateway_card_action_accepts_action_info_and_json_value() -> None:
    payload = {
        "event": {
            "action_info": {
                "value": '{"kind":"approval_action","action":"detail","chat_id":"oc_1","actor_open_id":"ou_1"}'
            },
            "context": {"open_message_id": "om_1"},
        }
    }

    action = parse_gateway_card_action(payload)

    assert action is not None
    assert action.kind == "approval_action"
    assert action.action == "detail"
    assert action.chat_id == "oc_1"
    assert action.message_id == "om_1"
    assert action.value["actor_open_id"] == "ou_1"
    assert gateway_card_action_value(payload)["kind"] == "approval_action"
    assert gateway_card_action_message_id(payload) == "om_1"


def test_build_feishu_gateway_message_marks_action_info_callback_as_card_action() -> None:
    message = build_feishu_gateway_message(
        {
            "header": {"event_id": "evt_card", "event_type": "card.action.trigger", "app_id": "cli_1"},
            "event": {
                "operator": {"operator_id": {"open_id": "ou_1"}},
                "action_info": {"value": {"kind": "approval_action", "action": "detail", "chat_id": "oc_1"}},
                "context": {"open_message_id": "om_card"},
            },
        }
    )

    assert message.kind == GatewayMessageKind.CARD_ACTION
    assert message.actor.open_id == "ou_1"
    assert message.context.chat_id == "oc_1"
    assert message.context.message_id == "om_card"


def test_gateway_card_responder_dispatches_by_action_kind() -> None:
    calls = []

    async def handle_message(db, app_config, payload):
        calls.append(("message", payload["event"]["action"]["value"]["action"]))
        return True

    async def handle_response(db, app_config, payload):
        calls.append(("response", payload["event"]["action"]["value"]["action"]))
        return {"toast": {"type": "info", "content": "ok"}}

    responder = GatewayCardResponder(
        kind="approval_action",
        handle_message=handle_message,
        handle_response=handle_response,
    )
    payload = {"event": {"action": {"value": {"kind": "approval_action", "action": "detail"}}}}

    handled = asyncio.run(dispatch_gateway_card_action_message(None, None, payload, responders=(responder,)))
    response = asyncio.run(dispatch_gateway_card_action_response(None, None, payload, responders=(responder,)))

    assert handled is True
    assert response == {"toast": {"type": "info", "content": "ok"}}
    assert calls == [("message", "detail"), ("response", "detail")]


def test_gateway_card_responder_ignores_unknown_action_kind() -> None:
    async def handle_message(db, app_config, payload):
        raise AssertionError("unknown card action must not dispatch")

    async def handle_response(db, app_config, payload):
        raise AssertionError("unknown card action must not dispatch")

    responder = GatewayCardResponder(
        kind="approval_action",
        handle_message=handle_message,
        handle_response=handle_response,
    )
    payload = {"event": {"action": {"value": {"kind": "other_action", "action": "open"}}}}

    assert asyncio.run(dispatch_gateway_card_action_message(None, None, payload, responders=(responder,))) is False
    assert asyncio.run(dispatch_gateway_card_action_response(None, None, payload, responders=(responder,))) is None


def test_handle_feishu_command_audits_unhandled_card_action() -> None:
    class FakeDb:
        def __init__(self):
            self.added = []
            self.commits = 0

        def add(self, item):
            self.added.append(item)

        def commit(self):
            self.commits += 1

    db = FakeDb()
    app_config = SimpleNamespace(company_id=uuid4(), app_id="cli_1", name="大飞哥")
    payload = {
        "header": {"event_id": "evt_card", "event_type": "card.action.trigger", "app_id": "cli_1"},
        "event": {
            "operator": {"operator_id": {"open_id": "ou_1"}},
            "action": {"value": {"kind": "other_action", "action": "open", "chat_id": "oc_1"}},
            "context": {"open_message_id": "om_card"},
        },
    }

    handled = asyncio.run(handle_feishu_command(db, app_config, payload))

    assert handled is False
    assert db.commits == 1
    assert len(db.added) == 1
    audit = db.added[0]
    assert audit.action == "gateway.feishu.card_action"
    assert audit.target_id == "om_card"
    assert audit.payload["reason"] == "unhandled_card_action"
    assert audit.payload["handled"] is False


def test_handle_feishu_command_routes_natural_language_to_ai_fallback(monkeypatch) -> None:
    class FakeDb:
        def __init__(self):
            self.added = []
            self.commits = 0

        def add(self, item):
            self.added.append(item)

        def commit(self):
            self.commits += 1

    db = FakeDb()
    company_id = uuid4()
    app_config = SimpleNamespace(id=uuid4(), company_id=company_id, app_id="cli_1", name="大飞哥")
    identity = BotIdentity(open_id="ou_1", role="owner", access_scope="company", domains=("all",))
    captured = {}

    def fake_identity(*args, **kwargs):
        return identity

    def fake_employee_answer(db_arg, app_config_arg, question, normalized, identity_arg, chat_id):
        captured["employee_answer"] = {
            "db": db_arg,
            "company_id": app_config_arg.company_id,
            "question": question,
            "normalized": normalized,
            "identity": identity_arg,
            "chat_id": chat_id,
        }
        return "AI 兜底回答"

    def fake_employee_answer_result(db_arg, app_config_arg, question, normalized, identity_arg, chat_id):
        answer = fake_employee_answer(db_arg, app_config_arg, question, normalized, identity_arg, chat_id)
        return SimpleNamespace(
            answer=answer,
            trace_payload={
                "route_path": "company_qa",
                "route_label": "公司级问答",
                "steps": [
                    {"kind": "semantic", "name": "company", "status": "success", "metadata": {}},
                    {"kind": "route", "name": "company_qa", "status": "success", "metadata": {}},
                    {
                        "kind": "tool",
                        "name": "company_qa",
                        "status": "success",
                        "metadata": {
                            "provider": "local",
                            "data_source": "PostgreSQL",
                            "structured_result": {"response_text": "不要写入审计的工具正文"},
                        },
                    }
                ],
            },
        )

    async def fake_quick_sync(db_arg, app_config_arg, question):
        captured["quick_sync"] = question
        return {}

    async def fake_send_text_reply(*, app_config, reply_target, text):
        captured["reply"] = {
            "app_config": app_config,
            "reply_target": reply_target,
            "text": text,
        }

    def fake_record_context(db_arg, app_config_arg, identity_arg, chat_id, question, normalized, reply):
        captured["record_context"] = (question, normalized, reply)
        db_arg.commit()

    monkeypatch.setattr("app.services.feishu.commands.feishu_identity.get_sender_identity", fake_identity)
    monkeypatch.setattr("app.services.feishu.command_handlers.bot_runtime.employee_bot_answer", fake_employee_answer)
    monkeypatch.setattr("app.services.feishu.command_handlers.bot_runtime.employee_bot_answer_result", fake_employee_answer_result)
    monkeypatch.setattr("app.services.feishu.command_handlers.sync_commands.quick_sync_for_question", fake_quick_sync)
    monkeypatch.setattr("app.services.feishu.commands.feishu_replies.send_text_reply", fake_send_text_reply)
    monkeypatch.setattr("app.services.feishu.commands.command_handlers.load_approval_context", lambda *args, **kwargs: [])
    monkeypatch.setattr("app.services.feishu.commands.command_handlers.record_command_context", fake_record_context)

    handled = asyncio.run(
        handle_feishu_command(
            db,
            app_config,
            {
                "header": {"event_id": "evt_1", "event_type": "im.message.receive_v1", "app_id": "cli_1"},
                "event": {
                    "sender": {"sender_id": {"open_id": "ou_1"}, "sender_type": "user"},
                    "message": {
                        "message_id": "om_1",
                        "chat_id": "oc_1",
                        "chat_type": "p2p",
                        "message_type": "text",
                        "content": '{"text":"今天公司有什么风险？"}',
                    },
                },
            },
        )
    )

    assert handled is True
    assert captured["employee_answer"]["company_id"] == company_id
    assert captured["employee_answer"]["question"] == "今天公司有什么风险？"
    assert captured["employee_answer"]["chat_id"] == "oc_1"
    assert captured["reply"]["reply_target"] == {"receive_id_type": "chat_id", "receive_id": "oc_1"}
    assert captured["reply"]["text"] == "AI 兜底回答"
    assert captured["record_context"] == ("今天公司有什么风险？", "今天公司有什么风险？", "AI 兜底回答")
    assert db.added[-1].action == "gateway.feishu.message"
    assert db.added[-1].payload["handled"] is True
    assert db.added[-1].payload["used_agent_runtime"] is True
    assert db.added[-1].payload["final_answer_owner"] == "runtime_v5"
    assert db.added[-1].payload["route_path"] == "company_qa"
    assert db.added[-1].payload["agent_runtime_step_count"] == 3
    assert db.added[-1].payload["agent_runtime_tool_steps"][0]["name"] == "company_qa"
    assert "AI 兜底回答" not in str(db.added[-1].payload)
    assert "不要写入审计" not in str(db.added[-1].payload)
    assert db.added[-1].payload["gateway_chain"] == [
        "feishu_message_entrypoint",
        "pre_gateway",
        "result_followup_detector",
        "intent_recognition",
        "task_planner",
        "permission_check",
        "capability_router",
        "execution",
        "answer_composer",
        "message_gateway_reply",
    ]


def test_handle_feishu_command_result_exposes_gateway_contract(monkeypatch) -> None:
    class FakeDb:
        def __init__(self):
            self.added = []
            self.commits = 0

        def add(self, item):
            self.added.append(item)

        def commit(self):
            self.commits += 1

    db = FakeDb()
    company_id = uuid4()
    app_config = SimpleNamespace(id=uuid4(), company_id=company_id, app_id="cli_1", name="大飞哥")
    identity = BotIdentity(open_id="ou_1", role="owner", access_scope="company", domains=("all",))

    monkeypatch.setattr("app.services.feishu.commands.feishu_identity.get_sender_identity", lambda *args, **kwargs: identity)
    monkeypatch.setattr(
        "app.services.feishu.command_handlers.bot_runtime.employee_bot_answer",
        lambda *args, **kwargs: "AI 兜底回答",
    )
    send_calls = []

    monkeypatch.setattr(
        "app.services.feishu.command_handlers.bot_runtime.employee_bot_answer_result",
        lambda *args, **kwargs: SimpleNamespace(
            answer="AI 兜底回答",
            trace_payload={
                "route_path": "company_qa",
                "route_label": "公司级问答",
                "agent_identity": {
                    "agent_type": "employee_personal_agent",
                    "agent_id": f"{company_id}:ou_1",
                    "agent_owner_open_id": "ou_1",
                    "shared_business_tool_count": 9,
                    "data_permission_model": "identity_scoped_tighten_only",
                },
                "reply_mode": {
                    "mode_id": "thinking",
                    "label": "Thinking（需要分析数据）",
                    "data_requirement": "WorkEvent / Knowledge / Memory / 多 Tool 分析",
                    "enterprise_data_required": True,
                    "tool_strategy": "work_event_knowledge_memory_multi_tool",
                    "show_thinking_map": True,
                    "pre_reply_required": True,
                },
                "thinking_preview": {
                    "kind": "thinking_flow_animation",
                    "presentation": "animated_thinking_map",
                    "send_before_final_answer": True,
                    "frames": [
                        {"label": "识别范围", "text": "问题范围：指定公司"},
                        {"label": "选择路径", "text": "能力路径：公司级问答"},
                        {"label": "调用数据", "text": "数据/工具：WorkEvent / Knowledge / Memory / 多 Tool 分析"},
                        {"label": "生成回复", "text": "输出结论、依据和下一步"},
                    ],
                },
                "steps": [
                    {"kind": "semantic", "name": "company", "status": "success", "metadata": {}},
                    {"kind": "route", "name": "company_qa", "status": "success", "metadata": {}},
                    {
                        "kind": "tool",
                        "name": "company_qa",
                        "status": "success",
                        "metadata": {"provider": "local", "data_source": "PostgreSQL"},
                    },
                ],
            },
        ),
    )
    monkeypatch.setattr("app.services.feishu.commands.command_handlers.load_approval_context", lambda *args, **kwargs: [])
    monkeypatch.setattr("app.services.feishu.commands.command_handlers.record_command_context", lambda db_arg, *args: db_arg.commit())

    async def fake_quick_sync(*args, **kwargs):
        return {}

    async def fake_send_text_reply(*args, **kwargs):
        send_calls.append(kwargs)
        return {"code": 0}

    monkeypatch.setattr("app.services.feishu.command_handlers.sync_commands.quick_sync_for_question", fake_quick_sync)
    monkeypatch.setattr("app.services.feishu.commands.feishu_replies.send_text_reply", fake_send_text_reply)

    result = asyncio.run(
        handle_feishu_command_result(
            db,
            app_config,
            {
                "header": {"event_id": "evt_1", "event_type": "im.message.receive_v1", "app_id": "cli_1"},
                "event": {
                    "sender": {"sender_id": {"open_id": "ou_1"}, "sender_type": "user"},
                    "message": {
                        "message_id": "om_1",
                        "chat_id": "oc_1",
                        "chat_type": "p2p",
                        "message_type": "text",
                        "content": '{"text":"今天公司有什么风险？"}',
                    },
                },
            },
        )
    )

    payload = result.as_payload()
    assert payload["handled"] is True
    assert payload["status"] == "handled"
    assert payload["reply_sent"] is True
    assert payload["thinking_notice_sent"] is True
    assert payload["used_agent_runtime"] is True
    assert payload["final_answer_owner"] == "runtime_v5"
    assert payload["route_path"] == "company_qa"
    assert payload["route_label"] == "公司级问答"
    assert payload["reply_mode_id"] == "thinking"
    assert payload["reply_mode_label"] == "Thinking（需要分析数据）"
    assert payload["reply_mode_data_requirement"] == "WorkEvent / Knowledge / Memory / 多 Tool 分析"
    assert payload["reply_mode_enterprise_data_required"] is True
    assert payload["reply_mode_pre_reply_required"] is True
    assert payload["reply_mode_tool_strategy"] == "work_event_knowledge_memory_multi_tool"
    assert payload["agent_identity"]["agent_id"] == f"{company_id}:ou_1"
    assert payload["agent_identity"]["agent_type"] == "employee_personal_agent"
    assert payload["agent_identity"]["shared_business_tool_count"] == 9
    assert payload["normalized_command"] == "今天公司有什么风险？"
    assert payload["chat_id"] == "oc_1"
    assert payload["message_id"] == "om_1"
    assert payload["agent_runtime_trace"]["route_path"] == "company_qa"
    assert payload["agent_runtime_trace"]["reply_mode"]["mode_id"] == "thinking"
    assert payload["agent_runtime_trace"]["thinking_preview"]["kind"] == "thinking_flow_animation"
    assert payload["agent_runtime_step_count"] == 3
    assert payload["agent_runtime_tool_steps"][0]["name"] == "company_qa"
    assert len(send_calls) == 2
    assert send_calls[0]["text"].startswith("思考中：正在按")
    assert "动态思考图：识别范围 -> 选择路径 -> 调用数据 -> 生成回复" in send_calls[0]["text"]
    assert send_calls[1]["text"] == "AI 兜底回答"
    assert db.added[-1].payload["agent_identity"]["agent_id"].endswith(":ou_1")
    assert db.added[-1].payload["agent_identity"]["data_permission_model"] == "identity_scoped_tighten_only"
    assert db.added[-1].payload["reply_mode_id"] == "thinking"
    assert db.added[-1].payload["reply_mode_data_requirement"] == "WorkEvent / Knowledge / Memory / 多 Tool 分析"
    assert db.added[-1].payload["reply_mode_enterprise_data_required"] is True
    assert db.added[-1].payload["reply_mode_pre_reply_required"] is True
    assert db.added[-1].payload["reply_mode_tool_strategy"] == "work_event_knowledge_memory_multi_tool"
    assert db.added[-1].payload["thinking_preview"]["presentation"] == "animated_thinking_map"


def test_handle_feishu_command_sends_authorization_card_for_user_identity_actions(monkeypatch) -> None:
    class FakeDb:
        def __init__(self):
            self.added = []
            self.commits = 0

        def add(self, item):
            self.added.append(item)

        def commit(self):
            self.commits += 1

        def scalar(self, query):
            return None

    db = FakeDb()
    app_config = SimpleNamespace(id=uuid4(), company_id=uuid4(), app_id="cli_1", name="大飞哥")
    identity = BotIdentity(open_id="ou_1", role="member", access_scope="personal")
    send_calls = {"text": [], "card": []}

    monkeypatch.setattr("app.services.feishu.commands.feishu_identity.get_sender_identity", lambda *args, **kwargs: identity)
    monkeypatch.setattr("app.services.feishu.commands.command_handlers.load_approval_context", lambda *args, **kwargs: [])
    monkeypatch.setattr("app.services.feishu.commands.command_handlers.record_command_context", lambda db_arg, *args: db_arg.commit())
    monkeypatch.setattr(
        "app.services.feishu.command_handlers.bot_runtime.employee_bot_answer_result",
        lambda *args, **kwargs: SimpleNamespace(
            answer=(
                "范围：本人相关｜邮件问答\n授权入口：\n"
                "- 授权个人能力包: http://127.0.0.1:8000/api/user-identity/oauth/feishu/start"
            ),
            trace_payload={
                "route_path": "mail_qa",
                "route_label": "邮件问答",
                "steps": [
                    {"kind": "semantic", "name": "mail", "status": "success", "metadata": {}},
                    {"kind": "route", "name": "mail_qa", "status": "success", "metadata": {}},
                    {
                        "kind": "tool",
                        "name": "mail_qa",
                        "status": "denied",
                        "metadata": {
                            "provider": "feishu_mcp",
                            "structured_result": {
                                "user_identity_authorization_required": True,
                                "authorization_actions": [
                                    {
                                        "resource_type": "user_identity_bundle",
                                        "label": "授权个人能力包",
                                        "url": (
                                            "http://127.0.0.1:8000/api/user-identity/oauth/feishu/start"
                                            "?company_id=00000000-0000-4000-8000-000000000001&open_id=ou_1"
                                        ),
                                    }
                                ],
                            },
                        },
                    },
                ],
            },
        ),
    )

    async def fake_send_text_reply(*args, **kwargs):
        send_calls["text"].append(kwargs)
        return {"code": 0}

    async def fake_send_authorization_card(*args, **kwargs):
        send_calls["card"].append(kwargs)
        return True

    async def fake_quick_sync(*args, **kwargs):
        return {}

    monkeypatch.setattr("app.services.feishu.commands.feishu_replies.send_text_reply", fake_send_text_reply)
    monkeypatch.setattr("app.services.feishu.command_handlers.sync_commands.quick_sync_for_question", fake_quick_sync)
    monkeypatch.setattr(
        "app.services.feishu.commands.authorization_card_entrypoint.send_user_identity_authorization_card",
        fake_send_authorization_card,
    )

    result = asyncio.run(
        handle_feishu_command_result(
            db,
            app_config,
            {
                "header": {"event_id": "evt_1", "event_type": "im.message.receive_v1", "app_id": "cli_1"},
                "event": {
                    "sender": {"sender_id": {"open_id": "ou_1"}, "sender_type": "user"},
                    "message": {
                        "message_id": "om_1",
                        "chat_id": "oc_1",
                        "chat_type": "p2p",
                        "message_type": "text",
                        "content": '{"text":"最近邮件有什么"}',
                    },
                },
            },
        )
    )

    assert result.handled is True
    # user identity auth now handled inside Runtime V5
    assert result.route_path == "mail_qa"
    assert result.authorization_owner_open_id == "ou_1"
    assert result.authorization_action_count == 1
    assert result.user_identity_authorization_actions[0]["label"] == "授权个人能力包"
    assert len(send_calls["card"]) == 1
    assert send_calls["card"][0]["actions"][0]["resource_type"] == "user_identity_bundle"
    assert send_calls["text"] == []
    assert db.added[-1].payload["user_identity_card_sent"] is True
    assert db.added[-1].payload["user_identity_required"] is True
    assert db.added[-1].payload["user_identity_required_resources"] == ["user_identity_bundle"]
    assert db.added[-1].payload["user_identity_owner_open_id"] == "ou_1"
    assert db.added[-1].payload["user_identity_action_count"] == 1
    assert db.added[-1].payload["user_identity_action_summaries"] == [
        {"resource_type": "user_identity_bundle", "label": "授权个人能力包", "channel": None}
    ]
    assert "user_identity_authorization_actions" not in db.added[-1].payload


def test_handle_feishu_command_routes_employee_calendar_create_to_runtime_auth_card(monkeypatch) -> None:
    class EmptyScalars:
        def all(self):
            return []

    class FakeDb:
        def __init__(self):
            self.added = []
            self.commits = 0
            self.flushes = 0

        def add(self, item):
            self.added.append(item)

        def commit(self):
            self.commits += 1

        def flush(self):
            self.flushes += 1

        def scalar(self, query):
            return None

        def scalars(self, query):
            return EmptyScalars()

        def get(self, model, item_id):
            return SimpleNamespace(status="active")

    db = FakeDb()
    company_id = uuid4()
    app_config = SimpleNamespace(id=uuid4(), company_id=company_id, app_id="cli_1", name="大飞哥", settings={})
    send_calls = {"text": [], "card": []}

    monkeypatch.setattr("app.services.feishu.commands.settings.feishu_bot_ai_mode_enabled", True)
    monkeypatch.setattr("app.services.feishu.commands.command_handlers.load_approval_context", lambda *args, **kwargs: [])
    monkeypatch.setattr("app.services.feishu.commands.command_handlers.record_command_context", lambda db_arg, *args: db_arg.commit())

    async def fake_quick_sync(*args, **kwargs):
        return {}

    async def fake_send_text_reply(*args, **kwargs):
        send_calls["text"].append(kwargs)
        return {"code": 0}

    async def fake_send_authorization_card(*args, **kwargs):
        send_calls["card"].append(kwargs)
        return True

    monkeypatch.setattr("app.services.feishu.command_handlers.sync_commands.quick_sync_for_question", fake_quick_sync)
    monkeypatch.setattr("app.services.feishu.commands.feishu_replies.send_text_reply", fake_send_text_reply)
    monkeypatch.setattr(
        "app.services.feishu.commands.authorization_card_entrypoint.send_user_identity_authorization_card",
        fake_send_authorization_card,
    )

    result = asyncio.run(
        handle_feishu_command_result(
            db,
            app_config,
            {
                "header": {"event_id": "evt_1", "event_type": "im.message.receive_v1", "app_id": "cli_1"},
                "event": {
                    "sender": {"sender_id": {"open_id": "ou_employee"}, "sender_type": "user"},
                    "message": {
                        "message_id": "om_1",
                        "chat_id": "oc_1",
                        "chat_type": "p2p",
                        "message_type": "text",
                        "content": '{"text":"帮我新建一个日程"}',
                    },
                },
            },
        )
    )

    assert result.handled is True
    assert result.used_agent_runtime is True
    assert result.final_answer_owner == "runtime_v5"
    # user identity auth now handled inside Runtime V5
    assert result.route_path == "feishu_calendar_create"
    assert result.route_label == "创建日程"
    assert send_calls["text"]
    assert send_calls["card"] == []
    assert any(getattr(item, "action", None) == "agent.employee.activate" for item in db.added)
    gateway_events = [item for item in db.added if getattr(item, "action", None) == "gateway.feishu.message"]
    assert gateway_events
    gateway_payload = gateway_events[-1].payload
    assert gateway_payload["user_identity_card_sent"] is False
    assert gateway_payload["route_path"] == "feishu_calendar_create"
    assert gateway_payload["final_answer_owner"] == "runtime_v5"


def test_handle_feishu_command_routes_authorized_employee_personal_calendar_to_tool(monkeypatch) -> None:
    class EmptyScalars:
        def all(self):
            return []

    class FakeDb:
        def __init__(self):
            self.added = []
            self.commits = 0
            self.flushes = 0

        def add(self, item):
            self.added.append(item)

        def commit(self):
            self.commits += 1

        def flush(self):
            self.flushes += 1

        def scalar(self, query):
            if "bot_user_access" in str(query):
                return SimpleNamespace(
                    role="member",
                    access_scope="personal",
                    display_name="员工A",
                    settings={
                        "permission_domains": [],
                        "allowed_resources": [],
                        "user_identity_authorizations": {
                            "user_identity_bundle": {"status": "authorized", "owner_open_id": "ou_employee"},
                        },
                    },
                )
            return None

        def scalars(self, query):
            return EmptyScalars()

        def get(self, model, item_id):
            return SimpleNamespace(status="active")

    db = FakeDb()
    company_id = uuid4()
    app_config = SimpleNamespace(id=uuid4(), company_id=company_id, app_id="cli_1", name="大飞哥", settings={})
    send_calls = {"text": [], "card": []}

    monkeypatch.setattr("app.services.feishu.commands.settings.feishu_bot_ai_mode_enabled", True)
    monkeypatch.setattr("app.services.tools.config.get_tool_config", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.tools.router.execute_feishu_mcp_tool", lambda context, request: "本人今日日程：10点项目会")
    monkeypatch.setattr("app.services.feishu.commands.command_handlers.load_approval_context", lambda *args, **kwargs: [])
    monkeypatch.setattr("app.services.feishu.commands.command_handlers.record_command_context", lambda db_arg, *args: db_arg.commit())

    async def fake_quick_sync(*args, **kwargs):
        return {}

    async def fake_send_text_reply(*args, **kwargs):
        send_calls["text"].append(kwargs)
        return {"code": 0}

    async def fake_send_authorization_card(*args, **kwargs):
        send_calls["card"].append(kwargs)
        return True

    monkeypatch.setattr("app.services.feishu.command_handlers.sync_commands.quick_sync_for_question", fake_quick_sync)
    monkeypatch.setattr("app.services.feishu.commands.feishu_replies.send_text_reply", fake_send_text_reply)
    monkeypatch.setattr(
        "app.services.feishu.commands.authorization_card_entrypoint.send_user_identity_authorization_card",
        fake_send_authorization_card,
    )

    result = asyncio.run(
        handle_feishu_command_result(
            db,
            app_config,
            {
                "header": {"event_id": "evt_1", "event_type": "im.message.receive_v1", "app_id": "cli_1"},
                "event": {
                    "sender": {"sender_id": {"open_id": "ou_employee"}, "sender_type": "user"},
                    "message": {
                        "message_id": "om_1",
                        "chat_id": "oc_1",
                        "chat_type": "p2p",
                        "message_type": "text",
                        "content": '{"text":"我的日程有哪些"}',
                    },
                },
            },
        )
    )

    assert result.handled is True
    assert result.used_agent_runtime is True
    assert result.final_answer_owner == "runtime_v5"
    assert result.authorization_card_sent is False
    assert result.route_path == "feishu_calendar_query"
    assert send_calls["card"] == []
    assert len(send_calls["text"]) == 1
    assert send_calls["text"][0]["text"]
    gateway_events = [item for item in db.added if getattr(item, "action", None) == "gateway.feishu.message"]
    assert gateway_events
    gateway_payload = gateway_events[-1].payload
    assert gateway_payload["user_identity_card_sent"] is False
    assert gateway_payload["route_path"] == "feishu_calendar_query"


def test_handle_feishu_command_passes_app_cli_profile_to_tool_router(monkeypatch) -> None:
    class EmptyScalars:
        def all(self):
            return []

    class FakeDb:
        def __init__(self):
            self.added = []
            self.commits = 0

        def add(self, item):
            self.added.append(item)

        def commit(self):
            self.commits += 1

        def flush(self):
            pass

        def scalar(self, query):
            return None

        def scalars(self, query):
            return EmptyScalars()

        def get(self, model, item_id):
            return SimpleNamespace(status="active")

    db = FakeDb()
    company_id = uuid4()
    app_config = SimpleNamespace(
        id=uuid4(),
        company_id=company_id,
        app_id="cli_1",
        name="大飞哥",
        settings={"cli_profile": "company-gaustek"},
    )
    identity = BotIdentity(open_id="ou_owner", role="owner", access_scope="company", domains=("all",))
    captured = {}
    send_calls = []

    def fake_execute(context, request):
        captured["context_cli_profile"] = context.cli_profile
        captured["request_params"] = dict(request.params)
        return "公司日程"

    async def fake_quick_sync(*args, **kwargs):
        return {}

    async def fake_send_text_reply(*args, **kwargs):
        send_calls.append(kwargs)
        return {"code": 0}

    monkeypatch.setattr("app.services.feishu.commands.settings.feishu_bot_ai_mode_enabled", True)
    monkeypatch.setattr("app.services.feishu.commands.feishu_identity.get_sender_identity", lambda *args, **kwargs: identity)
    monkeypatch.setattr("app.services.feishu.commands.command_handlers.load_approval_context", lambda *args, **kwargs: [])
    monkeypatch.setattr("app.services.feishu.commands.command_handlers.record_command_context", lambda db_arg, *args: db_arg.commit())
    monkeypatch.setattr("app.services.feishu.command_handlers.sync_commands.quick_sync_for_question", fake_quick_sync)
    monkeypatch.setattr("app.services.feishu.commands.feishu_replies.send_text_reply", fake_send_text_reply)
    monkeypatch.setattr("app.services.tools.config.get_tool_config", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.tools.router.execute_feishu_mcp_tool", fake_execute)

    result = asyncio.run(
        handle_feishu_command_result(
            db,
            app_config,
            {
                "header": {"event_id": "evt_1", "event_type": "im.message.receive_v1", "app_id": "cli_1"},
                "event": {
                    "sender": {"sender_id": {"open_id": "ou_owner"}, "sender_type": "user"},
                    "message": {
                        "message_id": "om_1",
                        "chat_id": "oc_1",
                        "chat_type": "p2p",
                        "message_type": "text",
                        "content": '{"text":"今天有什么会议"}',
                    },
                },
            },
        )
    )

    assert result.handled is True
    assert result.used_agent_runtime is True
    assert result.route_path == "feishu_calendar_query"
    assert captured["context_cli_profile"] == "company-gaustek"
    assert captured["request_params"]["cli_profile"] == "company-gaustek"
    assert send_calls[0]["text"]


def test_feishu_gateway_routes_employee_agent_to_all_shared_business_tools(monkeypatch) -> None:
    class EmptyScalars:
        def all(self):
            return []

    class FakeDb:
        def __init__(self):
            self.added = []
            self.commits = 0
            self.flushes = 0

        def add(self, item):
            self.added.append(item)

        def commit(self):
            self.commits += 1

        def flush(self):
            self.flushes += 1

        def scalar(self, query):
            return None

        def scalars(self, query):
            return EmptyScalars()

        def get(self, model, item_id):
            return SimpleNamespace(status="active")

    db = FakeDb()
    company_id = uuid4()
    app_config = SimpleNamespace(id=uuid4(), company_id=company_id, app_id="cli_1", name="大飞哥", settings={})
    identity = BotIdentity(open_id="ou_owner", role="owner", access_scope="company", domains=("all",))
    send_calls = {"text": [], "card": []}

    monkeypatch.setattr("app.services.feishu.commands.settings.feishu_bot_ai_mode_enabled", True)
    monkeypatch.setattr("app.services.feishu.commands.feishu_identity.get_sender_identity", lambda *args, **kwargs: identity)
    monkeypatch.setattr("app.services.feishu.commands.command_handlers.load_approval_context", lambda *args, **kwargs: [])
    monkeypatch.setattr("app.services.feishu.commands.command_handlers.record_command_context", lambda db_arg, *args: db_arg.commit())

    async def fake_send_text_reply(*args, **kwargs):
        send_calls["text"].append(kwargs)
        return {"code": 0}

    async def fake_send_authorization_card(*args, **kwargs):
        send_calls["card"].append(kwargs)
        return True

    async def fake_quick_sync(*args, **kwargs):
        return {}

    monkeypatch.setattr("app.services.feishu.commands.feishu_replies.send_text_reply", fake_send_text_reply)
    monkeypatch.setattr("app.services.feishu.command_handlers.sync_commands.quick_sync_for_question", fake_quick_sync)
    monkeypatch.setattr(
        "app.services.feishu.commands.authorization_card_entrypoint.send_user_identity_authorization_card",
        fake_send_authorization_card,
    )

    cases = [
        ("审批", "有没有需要我处理的审批", "feishu_approval_task_query", "ApprovalTool"),
        ("知识", "报销流程怎么做", "public_knowledge_qa", "KnowledgeTool"),
        ("多维表格", "多维表格里客户回款怎么样", "bitable_qa", "BitableTool"),
        ("项目主数据", "项目进展怎么样", "bitable_qa", "BitableTool"),
        ("聊天", "总结一下这个群", "chat_summary", "ChatTool"),
        ("日程", "今天有什么会议", "calendar_qa", "CalendarTool"),
        ("会议", "昨天会议纪要在哪里", "feishu_vc_meeting_search", "MeetingTool"),
        ("报告", "公司风险有什么", "company_qa", "ReportTool"),
        ("自动化", "我的待办事项", "personal_tasks", "AutomationTool"),
        ("人员", "组织架构怎么看", "feishu_contact_organization_snapshot", "PeopleTool"),
    ]

    for index, (label, text, expected_route, expected_business_tool) in enumerate(cases, start=1):
        result = asyncio.run(
            handle_feishu_command_result(
                db,
                app_config,
                {
                    "header": {
                        "event_id": f"evt_{index}",
                        "event_type": "im.message.receive_v1",
                        "app_id": "cli_1",
                    },
                    "event": {
                        "sender": {"sender_id": {"open_id": f"ou_employee_{index}"}, "sender_type": "user"},
                        "message": {
                            "message_id": f"om_{index}",
                            "chat_id": f"oc_{index}",
                            "chat_type": "p2p",
                            "message_type": "text",
                            "content": f'{{"text":"{text}"}}',
                        },
                    },
                },
            )
        )

        assert result.handled is True, label
        assert result.used_agent_runtime is True, label
        assert result.final_answer_owner == "runtime_v5", label
        assert result.route_path, label


def test_thinking_notice_only_for_thinking_reply_mode() -> None:
    thinking_trace = {
        "route_label": "公司级问答",
        "reply_mode": {
            "mode_id": "thinking",
            "data_requirement": "WorkEvent / Knowledge / Memory / 多 Tool 分析",
            "show_thinking_map": True,
        },
    }
    normal_trace = {"route_label": "日程问答", "reply_mode": {"mode_id": "normal", "show_thinking_map": False}}

    assert _should_send_thinking_notice(thinking_trace) is True
    assert _should_send_thinking_notice(normal_trace) is False
    assert _should_send_thinking_notice(None) is False
    assert "公司级问答" in _thinking_notice_text(thinking_trace)


def test_agent_runtime_trace_gateway_summary_excludes_answer_text() -> None:
    trace = {
        "answer": "不要写入审计的完整回答",
        "semantic_intent": "business_question",
        "route_path": "company_qa",
        "route_label": "公司级问答",
        "agent_identity": {
            "agent_type": "employee_personal_agent",
            "agent_id": "company:ou_owner",
            "agent_owner_open_id": "ou_owner",
            "shared_business_tool_count": 9,
            "data_permission_model": "identity_scoped_tighten_only",
        },
        "runtime_contract": {"final_answer_owner": "agent_runtime"},
        "steps": [
            {
                "kind": "tool",
                "name": "company_qa",
                "status": "success",
                "metadata": {
                    "provider": "local",
                    "data_source": "PostgreSQL",
                    "enterprise_identity_boundary": "App Identity + Company Scope + Role Scope",
                    "user_identity_boundary": None,
                    "cannot_escalate_original_permissions": True,
                    "structured_result": {"response_text": "不要写入审计的工具正文"},
                    "tool_returns_structured_result": True,
                    "final_answer_owner": "agent_runtime",
                },
            },
            {
                "kind": "workflow",
                "name": "sync_mail",
                "status": "success",
                "metadata": {
                    "workflow": "sync_mail",
                    "route_path": "sync_mail",
                    "route_label": "同步邮箱",
                    "reply_mode_id": "normal",
                    "final_answer_owner": "agent_runtime",
                    "raw_answer": "不要写入审计的 workflow 正文",
                },
            },
        ],
    }

    summary = _agent_runtime_trace_summary(trace)
    tool_steps = _agent_runtime_tool_steps(trace)
    workflow_steps = _agent_runtime_workflow_steps(trace)

    assert summary == {
        "semantic_intent": "business_question",
        "route_path": "company_qa",
        "route_label": "公司级问答",
        "agent_identity": {
            "agent_type": "employee_personal_agent",
            "agent_id": "company:ou_owner",
            "agent_owner_open_id": "ou_owner",
            "shared_business_tool_count": 9,
            "data_permission_model": "identity_scoped_tighten_only",
        },
        "runtime_contract": {"final_answer_owner": "agent_runtime"},
    }
    assert tool_steps == [
        {
            "name": "company_qa",
            "status": "success",
            "provider": "local",
            "business_tool": None,
            "data_source": "PostgreSQL",
            "execution_source": None,
            "source_chain": None,
            "data_permission_model": None,
            "enterprise_identity_boundary": "App Identity + Company Scope + Role Scope",
            "user_identity_boundary": None,
            "cannot_escalate_original_permissions": True,
            "tool_returns_structured_result": True,
            "final_answer_owner": "agent_runtime",
            "error": None,
        }
    ]
    assert workflow_steps == [
        {
            "name": "sync_mail",
            "status": "success",
            "workflow": "sync_mail",
            "route_path": "sync_mail",
            "route_label": "同步邮箱",
            "reply_mode_id": "normal",
            "final_answer_owner": "agent_runtime",
            "error": None,
        }
    ]
    assert "不要写入审计" not in str(summary)
    assert "不要写入审计" not in str(tool_steps)
    assert "不要写入审计" not in str(workflow_steps)


def test_group_message_without_bot_mention_is_not_reply_target() -> None:
    message = build_feishu_gateway_message(
        {
            "header": {"event_type": "im.message.receive_v1", "app_id": "cli_1"},
            "event": {
                "sender": {"sender_id": {"open_id": "ou_1"}, "sender_type": "user"},
                "message": {
                    "message_id": "om_1",
                    "chat_id": "oc_1",
                    "chat_type": "group",
                    "content": '{"text":"总结一下"}',
                },
            },
        }
    )

    assert not should_reply_to_feishu_message(message, app_id="cli_1", bot_names={"大飞哥"})


def test_app_message_is_not_reply_target() -> None:
    message = build_feishu_gateway_message(
        {
            "header": {"event_type": "im.message.receive_v1", "app_id": "cli_1"},
            "event": {
                "sender": {"sender_id": {"open_id": "ou_bot"}, "sender_type": "app"},
                "message": {
                    "message_id": "om_1",
                    "chat_id": "oc_1",
                    "chat_type": "p2p",
                    "content": '{"text":"hello"}',
                },
            },
        }
    )

    assert message.is_from_app
    assert not should_reply_to_feishu_message(message, app_id="cli_1", bot_names={"大飞哥"})


def test_feishu_url_verification_challenge() -> None:
    assert feishu_url_verification_challenge({"type": "url_verification", "challenge": "abc"}) == "abc"


def test_receive_feishu_event_payload_returns_challenge_without_ingest_or_command(monkeypatch) -> None:
    calls = []

    def fake_verify(app_config, payload):
        calls.append(("verify", payload["challenge"]))

    def fail_ingest(*args, **kwargs):
        raise AssertionError("url verification must not ingest work event")

    async def fail_handle(*args, **kwargs):
        raise AssertionError("url verification must not enter bot command handler")

    monkeypatch.setattr("app.services.feishu_event_entrypoint.verify_feishu_token", fake_verify)
    monkeypatch.setattr("app.services.feishu_event_entrypoint.ingest_feishu_event", fail_ingest)
    monkeypatch.setattr("app.services.feishu_event_entrypoint.handle_feishu_command_result", fail_handle)

    result = asyncio.run(
        receive_feishu_event_payload(
            object(),
            object(),
            {"type": "url_verification", "challenge": "launch-check"},
        )
    )

    assert result == {"challenge": "launch-check"}
    assert calls == [("verify", "launch-check")]


def test_receive_feishu_event_payload_ingests_before_command_handler(monkeypatch) -> None:
    event_id = uuid4()
    calls = []

    def fake_verify(app_config, payload):
        calls.append("verify")

    def fake_ingest(db, app_config, payload):
        calls.append("ingest")
        return SimpleNamespace(id=event_id)

    async def fake_handle(db, app_config, payload):
        calls.append("handle")
        return SimpleNamespace(
            handled=True,
            as_payload=lambda: {
                "handled": True,
                "status": "handled",
                "used_agent_runtime": True,
                "final_answer_owner": "agent_runtime",
            },
        )

    monkeypatch.setattr("app.services.feishu_event_entrypoint.verify_feishu_token", fake_verify)
    monkeypatch.setattr("app.services.feishu_event_entrypoint.ingest_feishu_event", fake_ingest)
    monkeypatch.setattr("app.services.feishu_event_entrypoint.handle_feishu_command_result", fake_handle)

    result = asyncio.run(
        receive_feishu_event_payload(
            object(),
            object(),
            {"header": {"event_type": "im.message.receive_v1"}, "event": {"message": {"content": '{"text":"大飞哥"}'}}},
        )
    )

    assert result == {
        "ok": True,
        "work_event_id": str(event_id),
        "command_handled": True,
        "gateway_result": {
            "handled": True,
            "status": "handled",
            "used_agent_runtime": True,
            "final_answer_owner": "agent_runtime",
        },
    }
    assert calls == ["verify", "ingest", "handle"]


def test_feishu_event_route_delegates_to_entrypoint(monkeypatch) -> None:
    app_config_id = uuid4()
    db = object()
    app_config = object()
    payload = {"type": "url_verification", "challenge": "route-check"}
    calls = []

    def fake_get_app(route_db, route_app_config_id):
        calls.append(("get_app", route_db, route_app_config_id))
        return app_config

    async def fake_receive(route_db, route_app_config, route_payload):
        calls.append(("receive", route_db, route_app_config, route_payload))
        return {"challenge": "route-check"}

    monkeypatch.setattr("app.api.routes.feishu_event_routes.get_feishu_app_or_404", fake_get_app)
    monkeypatch.setattr("app.api.routes.feishu_event_routes.receive_feishu_event_payload", fake_receive)

    result = asyncio.run(receive_event(app_config_id, payload, db=db))

    assert result == {"challenge": "route-check"}
    assert calls == [
        ("get_app", db, app_config_id),
        ("receive", db, app_config, payload),
    ]


def test_send_feishu_text_reply_uses_standard_target() -> None:
    calls = {}

    class FakeClient:
        def __init__(self, app_config):
            calls["app_config"] = app_config

        async def send_message(self, **kwargs):
            calls["message"] = kwargs
            return {"code": 0}

    app_config = object()
    result = asyncio.run(
        send_feishu_text_reply(
            app_config=app_config,
            reply_target={"receive_id_type": "chat_id", "receive_id": "oc_1"},
            text="x" * 4000,
            client_factory=FakeClient,
        )
    )

    assert result == {"code": 0}
    assert calls["app_config"] is app_config
    assert calls["message"]["receive_id_type"] == "chat_id"
    assert calls["message"]["receive_id"] == "oc_1"
    assert calls["message"]["msg_type"] == "text"
    assert len(calls["message"]["content"]["text"]) == 3500


def test_send_feishu_interactive_reply_uses_gateway_responder() -> None:
    calls = {}

    class FakeClient:
        def __init__(self, app_config):
            calls["app_config"] = app_config

        async def send_message(self, **kwargs):
            calls["message"] = kwargs
            return {"code": 0}

    card = {"elements": []}
    result = asyncio.run(
        send_feishu_interactive_reply(
            app_config=object(),
            reply_target={"receive_id_type": "chat_id", "receive_id": "oc_1"},
            card=card,
            client_factory=FakeClient,
        )
    )

    assert result == {"code": 0}
    assert calls["message"]["msg_type"] == "interactive"
    assert calls["message"]["content"] == card


def test_update_feishu_message_content_uses_gateway_responder() -> None:
    calls = {}

    class FakeClient:
        def __init__(self, app_config):
            calls["app_config"] = app_config

        async def update_message_content(self, **kwargs):
            calls["update"] = kwargs
            return {"code": 0}

    result = asyncio.run(
        update_feishu_message_content(
            app_config=object(),
            message_id="om_1",
            content={"elements": []},
            client_factory=FakeClient,
        )
    )

    assert result == {"code": 0}
    assert calls["update"]["message_id"] == "om_1"
    assert calls["update"]["content"] == {"elements": []}
