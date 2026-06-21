from types import SimpleNamespace
from uuid import uuid4

from app.services.feishu import bot_runtime
from app.services.feishu.identity import BotIdentity


def test_actor_from_identity_preserves_permission_shape() -> None:
    identity = BotIdentity(
        open_id="ou_1",
        role="manager",
        access_scope="domain",
        display_name="销售负责人",
        domains=("sales",),
        allowed_resources=("CRM",),
        email="sales@example.com",
    )

    actor = bot_runtime.actor_from_identity(identity)

    assert actor.role == "manager"
    assert actor.access_scope == "domain"
    assert actor.domains == ("sales",)
    assert actor.email == "sales@example.com"


def test_employee_bot_answer_uses_company_agent_write_policy(monkeypatch) -> None:
    company_id = uuid4()
    captured = {}

    def fake_get_settings(db, received_company_id):
        assert received_company_id == company_id
        return {
            "settings": {
                "planner_enabled": True,
                "max_planner_steps": 4,
                "allow_write_tools": False,
                "require_write_confirmation": True,
            }
        }

    def fake_answer_agent_message_with_trace(*args, **kwargs):
        captured["kwargs"] = kwargs
        return SimpleNamespace(answer="ok", trace=SimpleNamespace())

    monkeypatch.setattr("app.services.feishu.bot_runtime.get_company_agent_settings", fake_get_settings)
    monkeypatch.setattr("app.services.feishu.bot_runtime.answer_agent_message_with_trace", fake_answer_agent_message_with_trace)
    monkeypatch.setattr(
        "app.services.feishu.bot_runtime.agent_runtime_result_payload",
        lambda result: {"answer": result.answer, "trace": {"route_path": "company_qa", "steps": []}},
    )

    reply = bot_runtime.employee_bot_answer(
        object(),
        SimpleNamespace(company_id=company_id),
        question="创建任务",
        normalized="创建任务",
        identity=BotIdentity(open_id="ou_1", role="owner", access_scope="company", domains=("all",)),
        chat_id="oc_1",
    )

    assert reply == "ok"
    assert captured["kwargs"]["company_id"] == company_id
    assert captured["kwargs"]["planner_enabled"] is True
    assert captured["kwargs"]["max_planner_steps"] == 4
    assert captured["kwargs"]["allow_write_tools"] is False
    assert captured["kwargs"]["require_write_confirmation"] is True


def test_employee_bot_answer_result_exposes_trace_payload(monkeypatch) -> None:
    company_id = uuid4()

    monkeypatch.setattr(
        "app.services.feishu.bot_runtime.get_company_agent_settings",
        lambda db, received_company_id: {
            "settings": {
                "planner_enabled": False,
                "max_planner_steps": 3,
                "allow_write_tools": True,
                "require_write_confirmation": True,
            }
        },
    )
    monkeypatch.setattr(
        "app.services.feishu.bot_runtime.answer_agent_message_with_trace",
        lambda *args, **kwargs: SimpleNamespace(answer="公司摘要", trace=SimpleNamespace()),
    )
    monkeypatch.setattr(
        "app.services.feishu.bot_runtime.agent_runtime_result_payload",
        lambda result: {"answer": result.answer, "trace": {"route_path": "company_qa", "route_label": "公司级问答", "steps": []}},
    )

    result = bot_runtime.employee_bot_answer_result(
        object(),
        SimpleNamespace(company_id=company_id),
        question="公司情况",
        normalized="公司情况",
        identity=BotIdentity(open_id="ou_1", role="owner", access_scope="company", domains=("all",)),
        chat_id="oc_1",
    )

    assert result.answer == "公司摘要"
    assert result.trace_payload["route_path"] == "company_qa"
    assert result.trace_payload["route_label"] == "公司级问答"


def test_reply_scope_and_route_parse_standard_prefixes() -> None:
    reply = "范围：指定公司｜老板驾驶舱\n今日重点"

    assert bot_runtime.reply_scope_value(reply) == "指定公司"
    assert bot_runtime.reply_route_value(reply) == "老板驾驶舱"


def test_reply_scope_and_route_parse_explicit_labels() -> None:
    reply = "回答范围：当前会话\n能力路径：员工助理\n正文"

    assert bot_runtime.reply_scope_value(reply) == "当前会话"
    assert bot_runtime.reply_route_value(reply) == "员工助理"

