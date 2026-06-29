from types import SimpleNamespace
from uuid import uuid4

from app.services.feishu import bot_runtime
from app.services.feishu.identity import BotIdentity
from app.services.runtime_v5.models import (
    AnswerEnvelope,
    ComposedAnswer,
    ExecutionResult,
    IntentResult,
    PermissionDecision,
    PlannerResult,
    ResultContext,
    RuntimeContext,
    RuntimeIdentity,
    RuntimeScope,
)


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


def test_conversation_actor_bridge_tolerates_legacy_identity_shape() -> None:
    fallback = SimpleNamespace(
        open_id="ou_1",
        role="member",
        access_scope="personal",
        domains=(),
        display_name="陈俊",
    )
    envelope = SimpleNamespace(context=None)

    actor = bot_runtime._conversation_actor_from_envelope(envelope=envelope, fallback_identity=fallback)

    assert actor.open_id == "ou_1"
    assert actor.display_name == "陈俊"
    assert actor.department_names == ()
    assert actor.job_title == ""


def _fake_runtime_envelope(company_id, answer: str = "ok", intent_entities: dict | None = None) -> AnswerEnvelope:
    intent = IntentResult(
        question_type="query",
        intent="task_query",
        data_scope="self",
        entities=intent_entities or {},
        confidence=0.9,
    )
    plan = PlannerResult(strategy="task_query", sources=("task_qa",))
    return AnswerEnvelope(
        context=RuntimeContext(
            identity=RuntimeIdentity(open_id="ou_1", role="owner"),
            runtime_scope=RuntimeScope(company_ids=(company_id,), active_company_id=company_id),
            current_message="",
        ),
        intent=intent,
        plan=plan,
        permission=PermissionDecision(allowed=True, execution_identity="bot"),
        execution=ExecutionResult(
            strategy="task_query",
            status="success",
            provider_results=(),
            result_context=ResultContext(result_type="task_query", count=1),
        ),
        composed=ComposedAnswer(
            answer=answer,
            result_context=ResultContext(result_type="task_query", count=1),
        ),
    )


def _patch_runtime_v5_dependencies(monkeypatch, company_id, answer: str = "ok") -> None:
    monkeypatch.setattr("app.services.feishu.bot_runtime.settings.feishu_bot_runtime_v5_enabled", True)
    monkeypatch.setattr("app.services.feishu.bot_runtime.build_feishu_provider_registry", lambda **kwargs: {})
    monkeypatch.setattr("app.services.feishu.bot_runtime.run_runtime_v5", lambda **kwargs: _fake_runtime_envelope(company_id, answer))
    monkeypatch.setattr("app.services.feishu.bot_runtime.runtime_trace_summary", lambda envelope: {"strategy": envelope.plan.strategy, "sources": list(envelope.plan.sources)})
    monkeypatch.setattr("app.services.feishu.bot_runtime.capability_summary", lambda: {})
    monkeypatch.setattr("app.services.feishu.bot_runtime.provider_registry_diagnostics", lambda providers: {})
    monkeypatch.setattr("app.services.feishu.bot_runtime.build_runtime_provider_snapshot", lambda providers: {})
    monkeypatch.setattr("app.services.feishu.bot_runtime.runtime_v5_diagnostics_snapshot_base", lambda **kwargs: {})
    monkeypatch.setattr("app.services.feishu.bot_runtime.load_result_context_events", lambda chat_id: [])
    monkeypatch.setattr("app.services.feishu.bot_runtime.load_action_trace", lambda chat_id: None)
    monkeypatch.setattr("app.services.feishu.bot_runtime.load_runtime_decision_trace", lambda chat_id: None)
    monkeypatch.setattr("app.services.feishu.bot_runtime.record_runtime_decision_trace", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.feishu.bot_runtime.save_session_context", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.feishu.bot_runtime.save_result_context", lambda *args, **kwargs: None)


def test_employee_bot_answer_uses_runtime_v5_mainline(monkeypatch) -> None:
    company_id = uuid4()
    captured = {}

    def fake_run_runtime_v5(**kwargs):
        captured["context"] = kwargs["context"]
        captured["providers"] = kwargs["providers"]
        return _fake_runtime_envelope(company_id)

    _patch_runtime_v5_dependencies(monkeypatch, company_id)
    monkeypatch.setattr("app.services.feishu.bot_runtime.run_runtime_v5", fake_run_runtime_v5)

    reply = bot_runtime.employee_bot_answer(
        object(),
        SimpleNamespace(company_id=company_id),
        question="创建任务",
        normalized="创建任务",
        identity=BotIdentity(open_id="ou_1", role="owner", access_scope="company", domains=("all",)),
        chat_id="oc_1",
    )

    assert reply == "ok"
    assert captured["context"].runtime_scope.active_company_id == company_id
    assert captured["context"].identity.open_id == "ou_1"
    assert captured["providers"] == {}


def test_employee_bot_answer_result_exposes_trace_payload(monkeypatch) -> None:
    company_id = uuid4()

    _patch_runtime_v5_dependencies(monkeypatch, company_id, answer="公司摘要")

    result = bot_runtime.employee_bot_answer_result(
        object(),
        SimpleNamespace(company_id=company_id),
        question="公司情况",
        normalized="公司情况",
        identity=BotIdentity(open_id="ou_1", role="owner", access_scope="company", domains=("all",)),
        chat_id="oc_1",
    )

    assert result.answer == "公司摘要"
    assert result.trace_payload["route_path"] == "feishu_task_query"
    assert result.trace_payload["route_label"] == "任务查询"


def test_employee_bot_answer_persists_profile_update_candidate(monkeypatch) -> None:
    company_id = uuid4()
    captured = {}

    _patch_runtime_v5_dependencies(monkeypatch, company_id)
    monkeypatch.setattr(
        "app.services.feishu.bot_runtime.run_runtime_v5",
        lambda **kwargs: _fake_runtime_envelope(
            company_id,
            intent_entities={
                "profile_update_candidate": {
                    "preferred_address": "陈总",
                    "avoid_direct_name": True,
                }
            },
        ),
    )
    monkeypatch.setattr(
        "app.services.profile_context.apply_profile_update",
        lambda open_id, update: captured.update({"open_id": open_id, "update": update}) or dict(update),
    )

    result = bot_runtime.employee_bot_answer_result(
        object(),
        SimpleNamespace(company_id=company_id),
        question="以后叫我陈总",
        normalized="以后叫我陈总",
        identity=BotIdentity(open_id="ou_1", role="owner", access_scope="company", domains=("all",)),
        chat_id="oc_1",
    )

    assert result.answer == "ok"
    assert captured == {
        "open_id": "ou_1",
        "update": {
            "preferred_address": "陈总",
            "avoid_direct_name": True,
        },
    }


def test_reply_scope_and_route_parse_standard_prefixes() -> None:
    reply = "范围：指定公司｜老板驾驶舱\n今日重点"

    assert bot_runtime.reply_scope_value(reply) == "指定公司"
    assert bot_runtime.reply_route_value(reply) == "老板驾驶舱"


def test_reply_scope_and_route_parse_explicit_labels() -> None:
    reply = "回答范围：当前会话\n能力路径：员工助理\n正文"

    assert bot_runtime.reply_scope_value(reply) == "当前会话"
    assert bot_runtime.reply_route_value(reply) == "员工助理"
