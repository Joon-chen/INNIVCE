import ast
from pathlib import Path

from app.services.agent.policies import (
    BotActor,
    answer_scope_for_actor,
    capability_path_for_question,
    is_owner_only_intent,
    owner_only_reply,
    resolve_bot_route,
)


def test_owner_only_intents_include_cockpit_and_cross_company() -> None:
    assert is_owner_only_intent("打开驾驶舱", "驾驶舱概览")
    assert is_owner_only_intent("帮我看全公司风险", "帮我看全公司风险")
    assert is_owner_only_intent("跨公司总报", "跨公司总报")


def test_employee_cannot_use_owner_cockpit_reply() -> None:
    actor = BotActor(role="member", access_scope="chat")

    reply = owner_only_reply(actor)

    assert "老板驾驶舱能力" in reply
    assert "不会向员工开放" in reply


def test_answer_scope_for_actor_uses_company_domain_or_chat() -> None:
    owner = BotActor(role="owner", access_scope="company", domains=("all",))
    finance = BotActor(role="manager", access_scope="domain", domains=("finance", "approval"))
    member = BotActor(role="member", access_scope="chat")
    personal = BotActor(role="member", access_scope="personal")

    assert answer_scope_for_actor(owner, "今天公司有什么重点") == "company"
    assert answer_scope_for_actor(finance, "最近付款审批有什么风险") == "domain"
    assert answer_scope_for_actor(finance, "研发项目有什么风险") == "chat"
    assert answer_scope_for_actor(member, "这个群刚才说了什么") == "chat"
    assert answer_scope_for_actor(personal, "我的待办是什么") == "personal"


def test_resolve_bot_route_returns_path_without_answering() -> None:
    owner = BotActor(role="owner", access_scope="company", domains=("all",))
    member = BotActor(role="member", access_scope="chat")

    owner_route = resolve_bot_route(question="打开驾驶舱", normalized_command="驾驶舱概览", actor=owner)
    member_route = resolve_bot_route(question="打开驾驶舱", normalized_command="驾驶舱概览", actor=member)

    assert owner_route.path == "owner_cockpit"
    assert owner_route.scope == "company"
    assert member_route.path == "deny"
    assert member_route.reason == "owner_only_intent"


def test_capability_path_for_common_employee_questions() -> None:
    member = BotActor(role="member", access_scope="chat")
    finance = BotActor(role="manager", access_scope="domain", domains=("finance", "approval"))

    assert (
        capability_path_for_question(
            question="这个群刚才说了什么",
            normalized_command="这个群刚才说了什么",
            actor=member,
            scope="chat",
        )
        == "chat_summary"
    )
    assert (
        capability_path_for_question(
            question="这个群有什么待办任务",
            normalized_command="这个群有什么待办任务",
            actor=member,
            scope="chat",
        )
        == "chat_tasks"
    )
    assert (
        capability_path_for_question(
            question="我的待办是什么",
            normalized_command="我的待办是什么",
            actor=member,
            scope="chat",
        )
        == "personal_tasks"
    )
    assert (
        capability_path_for_question(
            question="最近付款审批有什么风险",
            normalized_command="最近付款审批有什么风险",
            actor=finance,
            scope="domain",
        )
        == "approval_qa"
    )
    assert (
        capability_path_for_question(
            question="你是谁",
            normalized_command="机器人身份",
            actor=finance,
            scope="domain",
        )
        == "general_chat"
    )


def test_agent_policies_layer_stays_lightweight() -> None:
    path = Path("app/services/agent/policies.py")
    text = path.read_text()
    tree = ast.parse(text)
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)

    forbidden_imports = [
        "app.services.ai",
        "app.services.feishu",
        "app.services.integrations",
        "app.services.cockpit",
        "app.services.llm",
        "sqlalchemy",
        "httpx",
        "lark_oapi",
    ]
    forbidden_text = [
        "Session",
        "requests",
        "OpenAI",
        "DeepSeek",
        "Qwen",
        "qwen",
        "deepseek",
        "complete_text",
        "complete_deepseek_text",
    ]

    assert [marker for marker in forbidden_imports if any(item.startswith(marker) for item in imported_modules)] == []
    assert [marker for marker in forbidden_text if marker in text] == []
