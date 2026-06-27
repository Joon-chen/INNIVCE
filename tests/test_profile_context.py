from types import SimpleNamespace
from uuid import uuid4

from app.services.profile_context import (
    apply_profile_update,
    IntentProfile,
    PresentationProfile,
    ProfileContext,
    intent_profile_text,
    load_profile_context,
    presentation_profile_text,
)
from app.services.llm.answer_rewriter import _profile_text
from app.services.runtime_v5.context import build_runtime_context
from app.services.runtime_v5.llm_intent import _prompt
from app.services.runtime_v5.models import IntentResult, RuntimeContext, RuntimeIdentity, RuntimeScope


def test_profile_context_splits_intent_and_presentation() -> None:
    context = RuntimeContext(
        identity=RuntimeIdentity(
            open_id="ou_1",
            role="owner",
            display_name="陈俊",
            department_id="dept_1",
            domains=("Workspace", "Process"),
        ),
        runtime_scope=RuntimeScope(active_company_id=uuid4()),
        current_message="查看公司任务",
    )

    profile = load_profile_context(
        runtime_context=context,
        actor_style="老板风格：先结论",
        profile_payload={
            "style": "professional",
            "verbosity": "concise",
            "tone_tips": "不要机械列菜单",
            "preferred_address": "陈总",
            "avoid_direct_name": True,
            "preferred_scope": "company",
            "business_aliases": {"待办": "任务"},
        },
    )

    intent_text = intent_profile_text(profile)
    presentation_text = presentation_profile_text(profile)

    assert "用户角色：owner" in intent_text
    assert "常用查询范围：company" in intent_text
    assert "业务别名：待办=任务" in intent_text
    assert "不授予权限" in intent_text
    assert "详细程度：concise" in presentation_text
    assert "不要机械列菜单" in presentation_text
    assert "称呼用户为「陈总」" in presentation_text
    assert "不要直呼用户真实姓名" in presentation_text
    assert "不改变事实、权限、数量或动作" in presentation_text
    assert "用户角色：owner" not in presentation_text
    assert "常用查询范围：company" not in presentation_text


def test_command_llm_prompt_reads_intent_profile_without_authorizing(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.user_context_pack.load_profile_context",
        lambda *, runtime_context: ProfileContext(
            intent=IntentProfile(
                display_name="陈俊",
                role="owner",
                domains=("Workspace",),
                common_scope="company",
                business_aliases={"待办": "任务"},
            ),
            presentation=PresentationProfile(style="casual", preferred_address="陈总", avoid_direct_name=True),
            source="test",
        ),
    )
    monkeypatch.setattr(
        "app.services.conversation_context.conversation_context_pack",
        lambda chat_id, *, question, purpose: SimpleNamespace(
            text="用户：查看公司任务\n助手：公司范围目前只能展示聚合。",
            result_included=False,
            quality=lambda: {"included_turn_count": 2, "result_included": False},
        ),
    )
    company_id = uuid4()
    context = RuntimeContext(
        identity=RuntimeIdentity(open_id="ou_1", role="owner", display_name="陈俊", domains=("Workspace",)),
        runtime_scope=RuntimeScope(company_ids=(company_id,), active_company_id=company_id),
        current_message="企业工作负荷",
        chat_id="oc_1",
    )

    prompt = _prompt(
        question="企业工作负荷",
        context=context,
        rule_intent=IntentResult(
            question_type="query",
            intent="general_query",
            data_scope="company",
            confidence=0.4,
        ),
    )

    assert "Intent Profile" in prompt
    assert "用户称呼：陈俊" in prompt
    assert "业务别名：待办=任务" in prompt
    assert "不授予权限，不改变执行身份" in prompt
    assert "Context:" in prompt
    assert "references_previous=no" in prompt
    assert "result_context_available=no" in prompt
    assert "Expression Profile" in prompt
    assert "说话风格：casual" in prompt
    assert "称呼用户为「陈总」" in prompt
    assert "不要直呼用户真实姓名" in prompt
    assert "边界：只调整表达方式，不改变事实、权限、数量或动作" in prompt


def test_profile_update_persists_expression_preferences(monkeypatch) -> None:
    saved = {}

    monkeypatch.setattr("app.services.profile_context._ensure_profile_table", lambda: None)
    monkeypatch.setattr("app.services.profile_context.load_profile_payload", lambda open_id: {"style": "professional"})
    monkeypatch.setattr("app.services.profile_context.save_profile_payload", lambda open_id, payload: saved.update({"open_id": open_id, "payload": payload}))

    changed = apply_profile_update(
        "ou_1",
        {
            "preferred_address": "陈总",
            "avoid_direct_name": True,
            "tone_tips": "更直接一点",
            "role": "owner",
        },
    )

    assert changed == {
        "preferred_address": "陈总",
        "avoid_direct_name": True,
        "tone_tips": "更直接一点",
    }
    assert saved["open_id"] == "ou_1"
    assert saved["payload"]["preferred_address"] == "陈总"
    assert saved["payload"]["avoid_direct_name"] is True
    assert "role" not in saved["payload"]


def test_profile_context_uses_identity_fact_before_profile_payload() -> None:
    context = RuntimeContext(
        identity=RuntimeIdentity(
            open_id="ou_identity",
            role="owner",
            display_name="陈俊",
            department_id="dept_1",
            department_names=("管理层",),
            job_title="CEO",
            domains=("Workspace",),
        ),
        runtime_scope=RuntimeScope(active_company_id=uuid4()),
        current_message="查看部门任务",
    )

    profile = load_profile_context(
        runtime_context=context,
        profile_payload={
            "display_name": "错误名字",
            "role": "member",
            "department_name": "错误部门",
            "domains": ["Process"],
            "preferred_scope": "department",
        },
    )

    assert profile.intent.display_name == "陈俊"
    assert profile.intent.role == "owner"
    assert profile.intent.departments == ("管理层",)
    assert profile.intent.domains == ("Workspace",)


def test_runtime_context_enriches_identity_from_people_snapshot(monkeypatch) -> None:
    company_id = uuid4()
    monkeypatch.setattr(
        "app.services.runtime_v5.context.load_people_snapshot",
        lambda cid: {
            "users": [
                {
                    "open_id": "ou_1",
                    "name": "陈俊",
                    "title": "CEO",
                    "email": "jun@example.com",
                    "department_ids": ["dept_1"],
                    "department_names": ["管理层"],
                }
            ]
        },
    )

    context = build_runtime_context(
        message="你好",
        identity=SimpleNamespace(open_id="ou_1", role="owner", access_scope="company", domains=("all",)),
        company_id=company_id,
        chat_id=None,
    )

    assert context.identity.display_name == "陈俊"
    assert context.identity.job_title == "CEO"
    assert context.identity.email == "jun@example.com"
    assert context.identity.department_names == ("管理层",)


def test_conversation_profile_text_uses_contact_facts_as_expression_context_only() -> None:
    text = _profile_text(
        "ou_1",
        "老板风格：先结论",
        actor=SimpleNamespace(
            open_id="ou_1",
            role="owner",
            display_name="陈俊",
            job_title="CEO",
            department_names=("管理层",),
            email="jun@example.com",
        ),
    )

    assert "事实上下文" in text
    assert "姓名/称呼=陈俊" in text
    assert "职位=CEO" in text
    assert "部门=管理层" in text
    assert "仅用于称呼和表达贴合，不作为权限依据" in text
