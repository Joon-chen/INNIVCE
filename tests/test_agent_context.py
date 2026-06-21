from uuid import uuid4
from types import SimpleNamespace

from app.models.entities import BotUserPreference, BotUserSession, MemoryFact
from app.services.agent.context import (
    bot_answer_style,
    bot_session_agent_identity,
    bot_session_key,
    fallback_answer_style,
    recent_session_style_hint,
    record_bot_session,
)
from app.services.agent.policies import BotActor
from app.services.tools.base import SHARED_TOOL_COUNT


def test_agent_context_models_match_expected_tables() -> None:
    assert BotUserSession.__tablename__ == "bot_user_sessions"
    assert BotUserPreference.__tablename__ == "bot_user_preferences"
    assert MemoryFact.__tablename__ == "memory_facts"
    assert "user_open_id" in MemoryFact.__table__.columns
    assert "scope" in MemoryFact.__table__.columns


def test_bot_session_key_is_isolated_by_user_and_chat() -> None:
    assert bot_session_key(user_open_id="ou_1", chat_id=None) == "ou_1:direct"
    assert bot_session_key(user_open_id="ou_1", chat_id="oc_1") == "ou_1:oc_1"
    assert bot_session_key(user_open_id="ou_2", chat_id="oc_1") != bot_session_key(
        user_open_id="ou_1",
        chat_id="oc_1",
    )


def test_fallback_answer_style_uses_role_and_domain() -> None:
    assert "老板风格" in fallback_answer_style(BotActor(role="owner", access_scope="company"))
    assert "财务" in fallback_answer_style(BotActor(role="manager", access_scope="domain", domains=("finance",)))
    assert "销售" in fallback_answer_style(BotActor(role="manager", access_scope="domain", domains=("sales",)))


def test_record_bot_session_requires_open_id() -> None:
    assert (
        record_bot_session(
            None,
            company_id=uuid4(),
            actor=BotActor(role="member", access_scope="chat"),
            chat_id="oc_1",
            question="你好",
            normalized_command="寒暄",
            answer="你好",
        )
        is None
    )


def test_record_bot_session_stores_employee_agent_identity_per_user() -> None:
    company_id = uuid4()

    class FakeDb:
        def __init__(self):
            self.sessions = {}
            self.added = []

        def scalar(self, query):
            return None

        def add(self, item):
            self.added.append(item)
            self.sessions[item.session_key] = item

    db = FakeDb()
    first = record_bot_session(
        db,
        company_id=company_id,
        actor=BotActor(role="member", access_scope="personal", display_name="王工", open_id="ou_1"),
        chat_id="oc_1",
        question="我的待办是什么",
        normalized_command="我的待办是什么",
        answer="本人待办",
        route_label="个人待办",
        scope_label="本人相关",
    )
    second = record_bot_session(
        db,
        company_id=company_id,
        actor=BotActor(role="member", access_scope="personal", display_name="李工", open_id="ou_2"),
        chat_id="oc_1",
        question="我的待办是什么",
        normalized_command="我的待办是什么",
        answer="本人待办",
        route_label="个人待办",
        scope_label="本人相关",
    )

    assert first.session_key == "ou_1:oc_1"
    assert second.session_key == "ou_2:oc_1"
    assert first.short_context["agent_identity"]["agent_id"] == f"{company_id}:ou_1"
    assert second.short_context["agent_identity"]["agent_id"] == f"{company_id}:ou_2"
    assert first.short_context["agent_identity"]["agent_owner_display_name"] == "王工"
    assert second.short_context["agent_identity"]["agent_owner_display_name"] == "李工"
    assert first.short_context["agent_identity"]["shared_business_tool_count"] == 9
    assert first.short_context["agent_identity"]["data_permission_model"] == "identity_scoped_tighten_only"
    assert first.short_context["agent_identity"]["data_access_scope"] == "personal"
    assert first.short_context["agent_identity"]["user_identity_required"] is True
    assert first.short_context["agent_identity"]["user_resource_boundary"]["owner_open_id"] == "ou_1"


def test_bot_session_agent_identity_uses_shared_tool_contract() -> None:
    company_id = uuid4()
    identity = bot_session_agent_identity(
        company_id=company_id,
        actor=BotActor(role="owner", access_scope="company", display_name="Joon", open_id="ou_owner"),
    )

    assert identity["agent_type"] == "employee_personal_agent"
    assert identity["agent_id"] == f"{company_id}:ou_owner"
    assert identity["tool_access_policy"] == "all_business_tools_shared"
    assert identity["tool_sharing_model"] == "shared_business_tools_per_employee_agent"
    assert identity["shared_business_tools"] == ["ApprovalTool","KnowledgeTool","BitableTool","ChatTool","CalendarTool","MeetingTool","ReportTool","AutomationTool","PeopleTool"]
    assert identity["shared_business_tool_count"] == 9
    assert identity["agent_can_call_all_business_tools"] is True
    assert identity["data_access_scope"] == "company"
    assert identity["identity_permission_contract"]["tool_data_boundary_rule"] == {
        "tools": "global_shared_capabilities",
        "data": "bounded_by_app_identity_and_user_identity",
        "digital_advisor_can_only_tighten": True,
    }
    assert identity["identity_permission_contract"]["user_resource_boundary"]["required"] is False
    assert identity["enterprise_identity_constraints"] == ["app_identity", "company_scope", "role_scope"]
    assert identity["enterprise_resource_boundary"]["resource_owner"] == "feishu_custom_app_da_fei_ge"
    assert identity["enterprise_resource_boundary"]["company_scope"] == str(company_id)
    assert identity["enterprise_resource_boundary"]["role_scope"] == {
        "role": "owner",
        "access_scope": "company",
        "domains": [],
        "company_data_allowed": True,
    }
    assert identity["user_identity"] is None
    assert identity["user_identity_required"] is False
    assert identity["user_identity_constraints"] == []
    assert identity["user_identity_supported_resources"] == [
        "personal_feishu",
        "external_mail",
        "personal_dingtalk",
        "personal_wechat",
    ]
    assert identity["data_boundary_policy"] == "tool_global_data_identity_bounded"
    assert identity["digital_advisor_permission_policy"] == "tighten_only"
    assert identity["cannot_escalate_original_permissions"] is True


def test_recent_session_style_hint_uses_latest_user_context() -> None:
    class FakeDb:
        def scalar(self, query):
            return SimpleNamespace(
                last_intent="驾驶舱风险",
                short_context={"last_question": "系统数据有什么盲区"},
            )

    hint = recent_session_style_hint(FakeDb(), company_id=uuid4(), user_open_id="ou_1")

    assert "最近意图：驾驶舱风险" in hint
    assert "上一轮问题：系统数据有什么盲区" in hint


def test_bot_answer_style_is_auto_adaptive_not_manual_command_style() -> None:
    class FakeDb:
        def __init__(self) -> None:
            self.calls = 0

        def scalar(self, query):
            self.calls += 1
            if self.calls <= 2:
                return None
            return SimpleNamespace(
                last_intent="最近审批",
                short_context={"last_question": "帮我查待我审批的单子"},
            )

    style = bot_answer_style(
        FakeDb(),
        company_id=uuid4(),
        actor=BotActor(role="owner", access_scope="company", open_id="ou_owner"),
    )

    assert "老板风格" in style
    assert "自动判断表达方式" in style
    assert "帮我查待我审批的单子" in style
