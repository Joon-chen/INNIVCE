from uuid import uuid4

from app.services.tools.company import answer_company_question
from app.services.agent.policies import BotActor
from app.services.cockpit.schemas import CockpitModuleResult, CockpitOverview


def test_answer_company_question_uses_matching_cockpit_module(monkeypatch) -> None:
    captured = {}

    def fake_build_module(db, *, key, scope, limit):
        captured["key"] = key
        captured["scope"] = scope
        captured["limit"] = limit
        return CockpitModuleResult(
            key=key,
            name="风险预警",
            description="风险",
            count=1,
            summary="开放风险 1 条。",
            items=[],
            next_actions=["先确认负责人。"],
        )

    monkeypatch.setattr("app.services.tools.company.build_cockpit_module", fake_build_module)

    company_id = uuid4()
    actor = BotActor(role="owner", access_scope="company", domains=("all",))
    answer = answer_company_question(
        None,
        company_id=company_id,
        actor=actor,
        question="固势现在有什么风险",
        normalized_command="固势现在有什么风险",
    )

    assert captured["key"] == "risks"
    assert captured["scope"].company_id == company_id
    assert "风险预警" in answer
    assert "开放风险 1 条" in answer


def test_answer_company_question_falls_back_to_overview(monkeypatch) -> None:
    captured = {}

    def fake_build_overview(db, *, scope, limit):
        captured["scope"] = scope
        captured["limit"] = limit
        return CockpitOverview(
            company_id=None,
            modules=[
                CockpitModuleResult(
                    key="today-focus",
                    name="今日重点",
                    description="今日",
                    count=2,
                    summary="今天新增 2 条工作事件。",
                )
            ],
            metrics={},
        )

    monkeypatch.setattr("app.services.tools.company.build_cockpit_overview", fake_build_overview)

    actor = BotActor(role="owner", access_scope="all", domains=("all",))
    answer = answer_company_question(
        None,
        company_id=uuid4(),
        actor=actor,
        question="公司现在怎么样",
        normalized_command="公司现在怎么样",
    )

    assert captured["scope"].all_companies is True
    assert "驾驶舱概览" in answer
    assert "今日重点" in answer


def test_answer_company_question_reports_high_value_group_access_blindspots(monkeypatch) -> None:
    captured = {}

    def fake_build_module(db, *, key, scope, limit):
        captured["key"] = key
        return CockpitModuleResult(
            key=key,
            name="数据覆盖",
            description="数据覆盖",
            count=2,
            summary="当前启用 10 项数据来源；数据盲区 2 项。",
            metrics={
                "owner_actions": [
                    {
                        "action_code": "invite_bot_to_chats",
                        "responsible_role": "销售负责人",
                        "business_impact": "关键群暂未接入。",
                        "resources": [
                            {
                                "resource_name": "固势销售客户群",
                                "access_recommendation": {
                                    "label": "建议接入",
                                    "recommended_notify_target": "销售负责人",
                                    "reason": "归属销售/客户经营域",
                                },
                            },
                            {
                                "resource_name": "茶水闲聊群",
                                "access_recommendation": {
                                    "label": "不建议接入",
                                    "recommended_notify_target": "不通知",
                                    "reason": "疑似低经营价值或临时群",
                                },
                            },
                        ],
                    }
                ]
            },
        )

    monkeypatch.setattr("app.services.tools.company.build_cockpit_module", fake_build_module)

    answer = answer_company_question(
        None,
        company_id=uuid4(),
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        question="今天系统数据有什么盲区，哪些关键群未接入",
        normalized_command="今天系统数据有什么盲区，哪些关键群未接入",
    )

    assert captured["key"] == "resources"
    assert "关键群接入建议" in answer
    assert "固势销售客户群" in answer
    assert "销售负责人" in answer
    assert "茶水闲聊群" not in answer
    assert "重试并恢复" in answer
    assert "驾驶舱 数据覆盖 模块" in answer


def test_answer_company_question_routes_exact_data_blindspot_to_resources(monkeypatch) -> None:
    captured = {}

    def fake_build_module(db, *, key, scope, limit):
        captured["key"] = key
        return CockpitModuleResult(
            key=key,
            name="数据覆盖",
            description="数据覆盖",
            count=1,
            summary="当前启用 10 项数据来源；数据盲区 1 项。",
            metrics={},
            items=[{"title": "固势销售客户群", "kind": "bot_not_in_chat"}],
        )

    monkeypatch.setattr("app.services.tools.company.build_cockpit_module", fake_build_module)

    answer = answer_company_question(
        None,
        company_id=uuid4(),
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        question="系统数据有什么盲区",
        normalized_command="系统数据有什么盲区",
    )

    assert captured["key"] == "resources"
    assert "当前数据盲区" in answer
    assert "高价值关键群接入建议" in answer
    assert "驾驶舱概览" not in answer


def test_answer_company_question_requires_company_permission() -> None:
    actor = BotActor(role="member", access_scope="chat")

    answer = answer_company_question(
        None,
        company_id=uuid4(),
        actor=actor,
        question="公司风险",
        normalized_command="公司风险",
    )

    assert "需要公司级权限" in answer
