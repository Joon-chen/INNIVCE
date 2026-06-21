from app.services.agent.planner import agent_plan_payload, build_agent_plan, resolve_execution_category_with_source
from app.services.agent.policies import BotAnswerRoute
from types import SimpleNamespace


def test_agent_planner_builds_read_tool_plan() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    plan = build_agent_plan(route=route, semantic=None, max_steps=3)
    payload = agent_plan_payload(plan)

    assert payload["route_path"] == "approval_qa"
    assert payload["requires_confirmation"] is False
    assert [step["kind"] for step in payload["steps"]] == ["guardrail", "tool", "answer"]
    assert payload["steps"][1]["required"] is True
    assert payload["steps"][1]["on_error"] == "stop"
    assert payload["steps"][1]["depends_on"] == []
    assert payload["steps"][1]["metadata"]["provider"] == "local"
    assert payload["steps"][1]["metadata"]["required_permissions"] == ["approval:read"]


def test_resolve_execution_category_with_source_handles_non_string_values() -> None:
    route = BotAnswerRoute(path="feishu_task_create", scope="company", reason="permission_scope")
    for raw_category in [None, 0, 1.2, True, False, ["analysis"], {"execution_category": "analysis"}, object()]:
        semantic = SimpleNamespace(
            route_hint="approval_qa",
            module_hint="tasks",
            canonical_question="创建一个任务",
            confidence=0.99,
            source="test",
            execution_category=raw_category,
        )

        execution_category, source = resolve_execution_category_with_source(route=route, semantic=semantic)
        assert source == "route_fallback"
        assert execution_category == "action"


def test_agent_planner_builds_with_route_fallback_when_semantic_category_is_dirty() -> None:
    route = BotAnswerRoute(path="feishu_task_create", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="tasks",
        canonical_question="创建一个任务",
        confidence=0.99,
        source="test",
        execution_category="__invalid__",
    )

    payload = agent_plan_payload(build_agent_plan(route=route, semantic=semantic, max_steps=5))

    assert payload["execution_category"] == "action"
    assert payload["execution_category_source"] == "route_fallback"


def test_resolve_execution_category_handles_approval_action_terms_as_action() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="帮我批准这笔报销",
        confidence=0.99,
        source="test",
    )

    execution_category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert source == "route_fallback"
    assert execution_category == "action"


def test_agent_planner_marks_write_tool_confirmation_requirement() -> None:
    route = BotAnswerRoute(path="feishu_task_create", scope="company", reason="permission_scope")

    payload = agent_plan_payload(build_agent_plan(route=route, semantic=None, max_steps=5))

    assert payload["requires_confirmation"] is True
    assert payload["steps"][1]["name"] == "feishu_task_create"
    assert payload["steps"][1]["metadata"]["supports_write"] is True
    assert payload["steps"][1]["metadata"]["requires_confirmation"] is True
    assert payload["steps"][1]["metadata"]["requires_dry_run"] is True
    assert payload["steps"][1]["metadata"]["confirmed_execution_requires"] == [
        "dry_run=true",
        "confirmed=true",
        "confirmation_token",
    ]
    assert payload["steps"][1]["metadata"]["allow_write_tools"] is True
    assert payload["steps"][1]["metadata"]["write_policy"] == "confirmation_required"


def test_agent_planner_marks_disabled_write_tools() -> None:
    route = BotAnswerRoute(path="feishu_task_create", scope="company", reason="permission_scope")

    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=None, max_steps=5, allow_write_tools=False)
    )

    assert payload["requires_confirmation"] is False
    assert payload["steps"][1]["metadata"]["supports_write"] is True
    assert payload["steps"][1]["metadata"]["requires_confirmation"] is False
    assert payload["steps"][1]["metadata"]["requires_dry_run"] is False
    assert payload["steps"][1]["metadata"]["confirmed_execution_requires"] == []
    assert payload["steps"][1]["metadata"]["allow_write_tools"] is False
    assert payload["steps"][1]["metadata"]["write_policy"] == "disabled"


def test_agent_planner_builds_calendar_create_as_single_write_tool() -> None:
    route = BotAnswerRoute(path="feishu_calendar_create_event", scope="personal", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="帮我安排一个日程")

    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=5, require_write_confirmation=True)
    )

    assert [step["kind"] for step in payload["steps"]] == ["guardrail", "tool", "answer"]
    assert [step["name"] for step in payload["steps"] if step["kind"] == "tool"] == ["feishu_calendar_create_event"]
    assert payload["steps"][1]["metadata"]["supports_write"] is True
    assert payload["steps"][1]["metadata"]["requires_confirmation"] is True
    assert payload["steps"][1]["metadata"]["requires_dry_run"] is True
    assert payload["steps"][1]["metadata"]["write_policy"] == "confirmation_required"
    assert payload["execution_category"] == "action"


def test_agent_planner_does_not_apply_bitable_template_to_calendar_route() -> None:
    route = BotAnswerRoute(path="feishu_calendar_create_event", scope="personal", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="查询日程并创建一张日程表")

    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=5, require_write_confirmation=True)
    )

    assert [step["name"] for step in payload["steps"] if step["kind"] == "tool"] == [
        "feishu_calendar_create_event",
    ]
    assert payload["requires_confirmation"] is True
    assert payload["execution_category"] in {"query", "action"}


def test_agent_planner_classifies_calendar_create_as_action() -> None:
    route = BotAnswerRoute(path="feishu_calendar_create_event", scope="personal", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="帮我新建一个日程")

    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=5, require_write_confirmation=False)
    )

    assert payload["execution_category"] == "action"
    assert payload["steps"][1]["metadata"]["supports_write"] is True


def test_agent_planner_selects_organization_snapshot_template_for_snapshot_only_request() -> None:
    route = BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="请帮我查看最新组织信息")

    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=4, allow_write_tools=True)
    )

    assert [step["kind"] for step in payload["steps"]] == ["guardrail", "tool", "answer"]
    assert [step["name"] for step in payload["steps"] if step["kind"] == "tool"] == [
        "feishu_contact_organization_snapshot",
    ]
    snapshot_step = next(step for step in payload["steps"] if step["name"] == "feishu_contact_organization_snapshot")
    assert snapshot_step["required"] is True
    assert snapshot_step["on_error"] == "stop"
    assert snapshot_step["depends_on"] == []


def test_agent_planner_does_not_build_organization_to_bitable_plan_without_write_terms() -> None:
    route = BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="请看下组织架构并告诉我有哪些表")

    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=4, allow_write_tools=True)
    )

    assert [step["kind"] for step in payload["steps"]] == ["guardrail", "tool", "answer"]
    assert [step["name"] for step in payload["steps"] if step["kind"] == "tool"] == [
        "feishu_contact_organization_snapshot",
    ]
    assert payload["requires_confirmation"] is False


def test_agent_planner_categorizes_action_query() -> None:
    route = BotAnswerRoute(path="feishu_task_create", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="请帮我创建一张日程并同步给我")

    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=4)
    )

    assert payload["execution_category"] == "action"


def test_agent_planner_prefers_semantic_execution_category() -> None:
    route = BotAnswerRoute(path="company_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这笔报销该不该通过？")
    setattr(semantic, "execution_category", "decision")

    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=3)
    )

    assert payload["execution_category"] == "decision"


def test_agent_planner_falls_back_to_route_classification_for_invalid_semantic_category() -> None:
    route = BotAnswerRoute(path="feishu_task_create", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="随便发起一个动作")
    setattr(semantic, "execution_category", "unknown")

    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=3)
    )

    _, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert payload["execution_category"] == "action"
    assert source == "route_fallback"
    assert payload["execution_category_source"] == "route_fallback"


def test_agent_planner_falls_back_to_route_classification_for_blank_semantic_category() -> None:
    route = BotAnswerRoute(path="company_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="公司风险有哪些？")
    setattr(semantic, "execution_category", "   ")

    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=3)
    )
    _, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert payload["execution_category"] == "analysis"
    assert source == "route_fallback"
    assert payload["execution_category_source"] == "route_fallback"


def test_agent_planner_normalizes_semantic_execution_category() -> None:
    route = BotAnswerRoute(path="company_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这项提案是不是可做？")
    setattr(semantic, "execution_category", "  Decision ")

    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=3)
    )

    assert payload["execution_category"] == "decision"


def test_planner_resolve_execution_category_with_source() -> None:
    route = BotAnswerRoute(path="feishu_task_create", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="请帮我创建一个任务", execution_category="  Action ")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "semantic"


def test_planner_resolve_execution_category_with_source_task_route_action_keywords() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这条任务催办一下")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_approval_route_add_sign_keywords() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这笔报销先加签一下")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_claim_keywords() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="把这个任务认领下来")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_mail_route_action_keywords() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这封邮件我先给老板回信")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_mail_route_forward_keywords() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="把这封邮件转发给财务")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_mail_route_cc_keywords() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这封邮件抄送给法务")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_assign_keywords() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这个任务请你指派给王总")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_archive_keywords() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这个任务我先归档")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_arrange_keywords() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这件事先安排一下")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_complete_keywords() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="把这个任务先关闭")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_approval_route_approve_keywords() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这笔报销我先批复一下")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_approval_route_agree_keywords() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这笔报销我先同意")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_mail_route_reply_keywords() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="请给客户回复一封邮件")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_mail_route_send_keywords() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="请先给客户发送一封邮件")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_delegate_keywords() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这个任务先转办给我经理")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_reassign_keywords() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="任务我先转派给王总")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_modify_keywords() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这个任务我先修改一下")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_remove_keywords() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这个任务先删除")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_copy_keywords() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="把这个任务先复制一份")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_comment_keywords() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="给这个任务先加个评论")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_suspend_keywords() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这个任务先挂起")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_resume_keywords() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这个任务先恢复")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_accept_keywords() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这个任务先验收")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_cancel_keywords() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这个任务先取消")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_start_keywords() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这个任务先开始")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_urgent_keywords() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这个任务先加急")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_reopen_keywords() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这个任务我先重开")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_mail_route_distribute_keywords() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这封邮件先下发给财务")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_approval_route_pass_keywords() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这笔报销先审批通过")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_approval_route_veto_keywords() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这笔报销先否决")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_approval_route_resubmit_keywords() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这笔报销先重提")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_approval_route_terminate_keywords() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这笔报销先终止")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_mail_route_compose_keywords() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="先帮我起草一封邮件")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_mail_route_relay_keywords() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="请先转寄这封邮件")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_mail_route_archive_keywords() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="请先把这封邮件归档")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_mail_route_mark_read_keywords() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="把这封邮件标记为已读")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_mail_route_sender_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这封邮件的发件人")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_mail_route_subject_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这封邮件主题")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_mail_route_subject_alias_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这封邮件标题")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_mail_route_recipient_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这封邮件收件人")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"




def test_planner_resolve_execution_category_with_source_mail_route_attachment_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这封邮件附件")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_query_start_time_noise() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="请查一下这个任务开始时间")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_mail_route_query_read_status_noise() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="查询下邮件读取状态")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_mail_route_query_read_status_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="邮件是否已读")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_approval_route_query_status_noise() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="先查下报销状态")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_personal_task_route_query_start_time_noise() -> None:
    route = BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="请查一下我的待办开始时间")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_personal_task_route_query_completion_noise() -> None:
    route = BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="请查下我的待办是否完成")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_personal_task_route_completion_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="我的待办是否完成")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_personal_task_route_deadline_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="我的待办截止日期")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_personal_task_route_progress_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="我的待办进度")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_personal_task_route_deadline_expected_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="我的待办预计完成")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_personal_task_route_reopen_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="我的待办是否重开")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_personal_task_route_overdue_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="我的待办是否逾期")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_personal_task_route_start_time_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="我的待办开始时间")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_personal_task_route_priority_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="我的待办优先级")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_personal_task_route_assignee_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="我的待办负责人")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_personal_task_route_submitter_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="我的待办提交人")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_personal_task_route_processor_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="我的待办处理人")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_status_completion_noise() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="请查下任务是否完成")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_status_completion_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="任务是否完成")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_priority_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="任务优先级")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_assignee_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="任务负责人")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_requestor_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="任务发起人")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_creator_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="任务创建者")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_processor_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="任务处理人")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_submitter_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="任务提交人")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_start_time_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="任务开始时间")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_deadline_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="任务截止日期")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_progress_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="任务当前进度")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_expected_completion_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="任务预计完成")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_reopen_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="任务是否重开")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_overdue_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="任务是否超期")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_task_route_overdue_with_different_term_as_analysis() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="任务是否逾期")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_approval_task_query_status_noise() -> None:
    route = BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="先查下这笔报销提交状态")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_approval_task_query_passed_flag_noise() -> None:
    route = BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这笔报销是否通过")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_chat_task_route_query_start_time_noise() -> None:
    route = BotAnswerRoute(path="chat_tasks", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="请查一下这条聊天任务开始时间")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_chat_task_route_completion_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="chat_tasks", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这条聊天任务是否完成")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_chat_task_route_deadline_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="chat_tasks", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="聊天任务截止日期")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_chat_task_route_progress_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="chat_tasks", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="聊天任务进度")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_chat_task_route_expected_completion_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="chat_tasks", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="聊天任务预计完成")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_chat_task_route_reopen_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="chat_tasks", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="聊天任务是否重开")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_chat_task_route_overdue_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="chat_tasks", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这条聊天任务是否超期")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_chat_task_route_overdue_with_different_term_as_query() -> None:
    route = BotAnswerRoute(path="chat_tasks", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这条聊天任务是否逾期")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_chat_task_route_start_time_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="chat_tasks", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="聊天任务开始时间")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_chat_task_route_priority_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="chat_tasks", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="聊天任务优先级")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_chat_task_route_assignee_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="chat_tasks", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="聊天任务负责人")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_chat_task_route_requestor_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="chat_tasks", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="聊天任务发起人")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_chat_task_route_creator_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="chat_tasks", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="聊天任务创建者")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_chat_task_route_processor_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="chat_tasks", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="聊天任务处理人")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_approval_route_passed_flag_noise() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这笔报销是否通过")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_approval_route_completion_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="报销是否完成")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_approval_route_status_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="报销状态")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_approval_route_result_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="报销结果")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_approval_route_current_status_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="报销当前状态")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_approval_route_requestor_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="报销发起人")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_approval_route_overdue_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="报销超期")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_approval_route_overdue_with_different_term_as_analysis() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="报销逾期")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_approval_route_overdue_with_different_term_and_bitable_create_terms_as_analysis() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="报销逾期，帮我建一张审批清单表")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_approval_route_overdue_with_bitable_create_terms_as_analysis() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="报销超期，帮我建一张审批清单表")

    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, allow_write_tools=False)
    )

    tool_steps = [step["name"] for step in payload["steps"] if step["kind"] == "tool"]
    assert tool_steps == ["approval_qa"]
    assert payload["execution_category"] == "action"
    assert payload["execution_category_source"] == "route_fallback"


def test_planner_resolve_execution_category_with_source_approval_route_submitter_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="报销提交人")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_approval_route_creator_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="报销创建者")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_approval_route_processor_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="报销处理人")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_approval_route_deadline_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="报销截止日期")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_approval_route_progress_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="报销当前进度")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_feishu_approval_task_query_completion_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="报销是否完成")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_feishu_approval_task_query_detail_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="报销详情")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_feishu_approval_task_query_current_status_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="报销当前状态")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_feishu_approval_task_query_deadline_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="报销截止日期")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_feishu_approval_task_query_overdue_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="报销超期")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_feishu_approval_task_query_overdue_with_different_term_as_query() -> None:
    route = BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="报销逾期")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_feishu_approval_task_query_overdue_with_bitable_create_terms_as_query() -> None:
    route = BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="报销逾期，帮我建一张审批清单表")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_feishu_approval_task_query_overdue_with_bitable_terms_forced_analysis() -> None:
    route = BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(
        canonical_question="报销逾期，先帮我建一张审批清单表",
        execution_category="analysis",
    )

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "semantic"


def test_planner_resolve_execution_category_with_source_feishu_approval_task_query_progress_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="报销当前进度")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_feishu_approval_task_query_requestor_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="报销发起人")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_feishu_approval_task_query_submitter_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="报销提交人")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_feishu_approval_task_query_creator_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="报销创建者")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_feishu_approval_task_query_processor_without_query_verb_noise() -> None:
    route = BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="报销处理人")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_mail_route_broadcast_keywords() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这封邮件先群发给法务")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_mail_route_overdue_without_query_verb_as_query() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="邮件是否超期")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_mail_route_overdue_with_different_term_as_query() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="邮件逾期")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_mail_route_overdue_with_bitable_create_terms_as_query() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="邮件超期，帮我建一张邮件清单表")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_approval_route_recall_keywords() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这笔报销先撤回")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_approval_route_revoke_keywords() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这笔报销我先撤销")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_approval_route_return_keywords() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这笔报销先退回给发起人")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "analysis"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_approval_route_submit_keywords() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这笔报销我先提交")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_mail_route_upload_keywords() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="把这封邮件先上传到云盘")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "action"
    assert source == "route_fallback"


def test_planner_resolve_execution_category_with_source_mail_route_download_keywords() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="请先下载这封邮件附件")

    category, source = resolve_execution_category_with_source(route=route, semantic=semantic)

    assert category == "query"
    assert source == "route_fallback"


def test_agent_planner_stops_denied_route_before_tools() -> None:
    route = BotAnswerRoute(path="deny", scope="none", reason="owner_only_intent", message="权限不足")

    payload = agent_plan_payload(build_agent_plan(route=route, semantic=None, max_steps=3))

    assert payload["requires_confirmation"] is False
    assert [step["kind"] for step in payload["steps"]] == ["guardrail", "stop"]
    assert payload["steps"][1]["name"] == "permission_denied"


def test_agent_planner_builds_organization_to_bitable_plan_when_query_asks_for_table_and_latest_org() -> None:
    route = BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="请帮我创建一张表格并把最新组织放进去，base= bascn-test-xxxx")
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=True)
    )

    assert [step["kind"] for step in payload["steps"]] == ["guardrail", "tool", "tool", "tool", "answer"]
    assert [step["name"] for step in payload["steps"] if step["kind"] == "tool"] == [
        "feishu_contact_organization_snapshot",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    snapshot_params = next(
        step["metadata"]["tool_params"] for step in payload["steps"] if step["name"] == "feishu_contact_organization_snapshot"
    )
    table_step = next(step for step in payload["steps"] if step["name"] == "feishu_bitable_table_create")
    record_step = next(step for step in payload["steps"] if step["name"] == "feishu_bitable_record_batch_create")
    table_params = next(
        step["metadata"]["tool_params"] for step in payload["steps"] if step["name"] == "feishu_bitable_table_create"
    )
    assert snapshot_params["response_format"] == "raw_json"
    assert table_step["depends_on"] == ["feishu_contact_organization_snapshot"]
    assert table_step["required"] is True
    assert table_step["on_error"] == "stop"
    assert record_step["depends_on"] == ["feishu_bitable_table_create"]
    assert record_step["required"] is True
    assert record_step["on_error"] == "continue"
    assert table_params["name"] == "最新组织快照"
    assert table_params["app_token"] == "bascn-test-xxxx"
    assert payload["requires_confirmation"] is True
    assert payload["execution_category"] == "action"


def test_agent_planner_builds_single_tool_for_task_action_query() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="帮我催办这条任务")
    payload = agent_plan_payload(build_agent_plan(route=route, semantic=semantic, max_steps=4))

    assert [step["name"] for step in payload["steps"] if step["kind"] == "tool"] == ["task_qa"]
    assert payload["execution_category"] == "action"
    assert payload["execution_category_source"] == "route_fallback"


def test_agent_planner_builds_single_tool_for_mail_action_query() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="请先回信给客户说明情况")
    payload = agent_plan_payload(build_agent_plan(route=route, semantic=semantic, max_steps=4))

    assert [step["name"] for step in payload["steps"] if step["kind"] == "tool"] == ["mail_qa"]
    assert payload["execution_category"] == "action"
    assert payload["execution_category_source"] == "route_fallback"


def test_agent_planner_builds_single_tool_for_approval_add_sign_query() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这笔报销先加签")
    payload = agent_plan_payload(build_agent_plan(route=route, semantic=semantic, max_steps=4))

    assert [step["name"] for step in payload["steps"] if step["kind"] == "tool"] == ["approval_qa"]
    assert payload["execution_category"] == "action"
    assert payload["execution_category_source"] == "route_fallback"


def test_agent_planner_builds_single_tool_for_task_claim_query() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这个任务我先认领一下")
    payload = agent_plan_payload(build_agent_plan(route=route, semantic=semantic, max_steps=4))

    assert [step["name"] for step in payload["steps"] if step["kind"] == "tool"] == ["task_qa"]
    assert payload["execution_category"] == "action"
    assert payload["execution_category_source"] == "route_fallback"


def test_agent_planner_builds_single_tool_for_mail_forward_query() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这封邮件我先转发给总监")
    payload = agent_plan_payload(build_agent_plan(route=route, semantic=semantic, max_steps=4))

    assert [step["name"] for step in payload["steps"] if step["kind"] == "tool"] == ["mail_qa"]
    assert payload["execution_category"] == "action"
    assert payload["execution_category_source"] == "route_fallback"


def test_agent_planner_builds_single_tool_for_mail_distribute_query() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这封邮件先下发给财务")
    payload = agent_plan_payload(build_agent_plan(route=route, semantic=semantic, max_steps=4))

    assert [step["name"] for step in payload["steps"] if step["kind"] == "tool"] == ["mail_qa"]
    assert payload["execution_category"] == "action"
    assert payload["execution_category_source"] == "route_fallback"


def test_agent_planner_builds_single_tool_for_task_delegate_query() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这个任务先转办给我经理")
    payload = agent_plan_payload(build_agent_plan(route=route, semantic=semantic, max_steps=4))

    assert [step["name"] for step in payload["steps"] if step["kind"] == "tool"] == ["task_qa"]
    assert payload["execution_category"] == "action"
    assert payload["execution_category_source"] == "route_fallback"


def test_agent_planner_builds_single_tool_for_approval_recall_query() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这笔报销我先撤回")
    payload = agent_plan_payload(build_agent_plan(route=route, semantic=semantic, max_steps=4))

    assert [step["name"] for step in payload["steps"] if step["kind"] == "tool"] == ["approval_qa"]
    assert payload["execution_category"] == "action"
    assert payload["execution_category_source"] == "route_fallback"


def test_agent_planner_builds_single_tool_for_task_assign_query() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这个任务请你指派给王总")
    payload = agent_plan_payload(build_agent_plan(route=route, semantic=semantic, max_steps=4))

    assert [step["name"] for step in payload["steps"] if step["kind"] == "tool"] == ["task_qa"]
    assert payload["execution_category"] == "action"
    assert payload["execution_category_source"] == "route_fallback"


def test_agent_planner_builds_single_tool_for_task_complete_query() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="把这个任务先关闭")
    payload = agent_plan_payload(build_agent_plan(route=route, semantic=semantic, max_steps=4))

    assert [step["name"] for step in payload["steps"] if step["kind"] == "tool"] == ["task_qa"]
    assert payload["execution_category"] == "action"
    assert payload["execution_category_source"] == "route_fallback"


def test_agent_planner_builds_single_tool_for_task_archive_query() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这个任务先归档")
    payload = agent_plan_payload(build_agent_plan(route=route, semantic=semantic, max_steps=4))

    assert [step["name"] for step in payload["steps"] if step["kind"] == "tool"] == ["task_qa"]
    assert payload["execution_category"] == "action"
    assert payload["execution_category_source"] == "route_fallback"


def test_agent_planner_builds_single_tool_for_task_arrange_query() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这件事先安排一下")
    payload = agent_plan_payload(build_agent_plan(route=route, semantic=semantic, max_steps=4))

    assert [step["name"] for step in payload["steps"] if step["kind"] == "tool"] == ["task_qa"]
    assert payload["execution_category"] == "action"
    assert payload["execution_category_source"] == "route_fallback"


def test_agent_planner_builds_single_tool_for_approval_approve_query() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这笔报销我先批复")
    payload = agent_plan_payload(build_agent_plan(route=route, semantic=semantic, max_steps=4))

    assert [step["name"] for step in payload["steps"] if step["kind"] == "tool"] == ["approval_qa"]
    assert payload["execution_category"] == "action"
    assert payload["execution_category_source"] == "route_fallback"


def test_agent_planner_builds_single_tool_for_approval_agree_query() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这笔报销我先同意")
    payload = agent_plan_payload(build_agent_plan(route=route, semantic=semantic, max_steps=4))

    assert [step["name"] for step in payload["steps"] if step["kind"] == "tool"] == ["approval_qa"]
    assert payload["execution_category"] == "action"
    assert payload["execution_category_source"] == "route_fallback"


def test_agent_planner_builds_single_tool_for_mail_reply_query() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="给客户回复一封邮件")
    payload = agent_plan_payload(build_agent_plan(route=route, semantic=semantic, max_steps=4))

    assert [step["name"] for step in payload["steps"] if step["kind"] == "tool"] == ["mail_qa"]
    assert payload["execution_category"] == "action"
    assert payload["execution_category_source"] == "route_fallback"


def test_agent_planner_builds_single_tool_for_mail_send_query() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="先给客户发送一封邮件")
    payload = agent_plan_payload(build_agent_plan(route=route, semantic=semantic, max_steps=4))

    assert [step["name"] for step in payload["steps"] if step["kind"] == "tool"] == ["mail_qa"]
    assert payload["execution_category"] == "action"
    assert payload["execution_category_source"] == "route_fallback"


def test_agent_planner_builds_organization_to_bitable_plan_for_synonym_query() -> None:
    route = BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(
        canonical_question="建张组织结构表，帮我同步一下最新员工并写入",
    )
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=False)
    )

    tool_steps = [step for step in payload["steps"] if step["kind"] == "tool"]
    assert [step["name"] for step in tool_steps] == [
        "feishu_contact_organization_snapshot",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    snapshot_step = next(step for step in payload["steps"] if step["name"] == "feishu_contact_organization_snapshot")
    assert snapshot_step["metadata"]["tool_params"]["max_departments"] == 100
    assert payload["requires_confirmation"] is False
    assert payload["execution_category"] == "action"


def test_agent_planner_builds_organization_to_bitable_plan_for_do_table_term() -> None:
    route = BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(
        canonical_question="请做一张组织表，按最近的组织结构写入",
    )
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=False)
    )

    tool_steps = [step for step in payload["steps"] if step["kind"] == "tool"]
    assert [step["name"] for step in tool_steps] == [
        "feishu_contact_organization_snapshot",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    assert payload["requires_confirmation"] is False


def test_agent_planner_builds_organization_to_bitable_plan_for_make_table_term() -> None:
    route = BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(
        canonical_question="帮我弄一张组织表，把最新组织同步进来",
    )
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=False)
    )

    tool_steps = [step for step in payload["steps"] if step["kind"] == "tool"]
    assert [step["name"] for step in tool_steps] == [
        "feishu_contact_organization_snapshot",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    assert payload["execution_category"] == "action"


def test_agent_planner_builds_organization_to_bitable_plan_for_mixed_table_term() -> None:
    route = BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(
        canonical_question="整一张组织表，顺便把最新组织写进表里",
    )
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=False)
    )

    tool_steps = [step for step in payload["steps"] if step["kind"] == "tool"]
    assert [step["name"] for step in tool_steps] == [
        "feishu_contact_organization_snapshot",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    assert payload["execution_category"] == "query"


def test_agent_planner_builds_organization_to_bitable_plan_for_set_table_term_with_base_token() -> None:
    route = BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(
        canonical_question="起一张组织表，base=bascn-settable，把最新组织同步过去",
    )
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=False)
    )

    tool_steps = [step for step in payload["steps"] if step["kind"] == "tool"]
    assert [step["name"] for step in tool_steps] == [
        "feishu_contact_organization_snapshot",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    table_step = next(step for step in payload["steps"] if step["name"] == "feishu_bitable_table_create")
    assert table_step["metadata"]["tool_params"]["app_token"] == "bascn-settable"


def test_agent_planner_does_not_build_organization_to_bitable_plan_for_create_without_import() -> None:
    route = BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="我想起一张组织表，先不要写入")
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=4, require_write_confirmation=False)
    )

    assert [step["kind"] for step in payload["steps"]] == ["guardrail", "tool", "answer"]
    assert [step["name"] for step in payload["steps"] if step["kind"] == "tool"] == [
        "feishu_contact_organization_snapshot",
    ]
    assert payload["requires_confirmation"] is False


def test_agent_planner_builds_organization_to_bitable_plan_for_ji_biao_term() -> None:
    route = BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(
        canonical_question="帮我把组织建表，并且把最新组织写入，base=bascn-base-xyz",
        execution_category="query",
    )
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=False)
    )

    plan_steps = [step for step in payload["steps"] if step["kind"] == "tool"]
    assert [step["name"] for step in plan_steps] == [
        "feishu_contact_organization_snapshot",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    table_step = next(step for step in payload["steps"] if step["name"] == "feishu_bitable_table_create")
    assert table_step["metadata"]["tool_params"]["name"] == "最新组织快照"
    assert table_step["metadata"]["tool_params"]["app_token"] == "bascn-base-xyz"
    assert payload["execution_category"] == "query"


def test_agent_planner_builds_organization_to_bitable_plan_for_maintain_term() -> None:
    route = BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(
        canonical_question="请建立一张组织表并同步最新组织进去",
        execution_category="analysis",
    )
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=True)
    )

    tool_names = [step["name"] for step in payload["steps"] if step["kind"] == "tool"]
    assert tool_names == [
        "feishu_contact_organization_snapshot",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    assert payload["execution_category"] == "analysis"
    assert payload["requires_confirmation"] is True


def test_agent_planner_builds_organization_to_bitable_plan_with_explicit_decision_category() -> None:
    route = BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(
        canonical_question="帮我判断是否要建立组织表并同步最新组织",
        execution_category="decision",
    )
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=False)
    )

    tool_names = [step["name"] for step in payload["steps"] if step["kind"] == "tool"]
    assert tool_names == [
        "feishu_contact_organization_snapshot",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    assert payload["execution_category"] == "decision"
    assert payload["execution_category_source"] == "semantic"


def test_agent_planner_builds_generic_query_to_bitable_table_plan() -> None:
    route = BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(
        canonical_question="帮我查询一下待办列表，并创建一张待办清单表，把结果写入",
    )
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=True)
    )

    tool_steps = [step for step in payload["steps"] if step["kind"] == "tool"]
    assert [step["name"] for step in tool_steps] == [
        "bitable_qa",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    assert [step["kind"] for step in payload["steps"]] == ["guardrail", "tool", "tool", "tool", "answer"]
    bitable_step = next(step for step in payload["steps"] if step["name"] == "bitable_qa")
    table_step = next(step for step in payload["steps"] if step["name"] == "feishu_bitable_table_create")
    record_step = next(step for step in payload["steps"] if step["name"] == "feishu_bitable_record_batch_create")
    assert bitable_step["metadata"]["tool_params"]["response_format"] == "raw_json"
    assert "query_fields" in bitable_step["metadata"]["plan_context_keys"]
    assert table_step["metadata"]["tool_params"]["fields"] == "${shared.query_fields}"
    assert record_step["depends_on"] == ["bitable_qa", "feishu_bitable_table_create"]
    assert record_step["required"] is False
    assert record_step["on_error"] == "continue"
    assert "query_rows" in record_step["metadata"]["plan_context_keys"]
    assert payload["requires_confirmation"] is True
    assert payload["execution_category"] == "action"


def test_agent_planner_keeps_query_to_bitable_app_token_when_provided() -> None:
    route = BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(
        canonical_question="查一下项目，创建一张项目汇总表并同步，把它写到base=bascn-query-abc",
    )
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=False)
    )

    table_step = next(step for step in payload["steps"] if step["name"] == "feishu_bitable_table_create")
    assert table_step["metadata"]["tool_params"]["app_token"] == "bascn-query-abc"
    assert table_step["required"] is True
    assert table_step["on_error"] == "stop"
    assert payload["requires_confirmation"] is False


def test_agent_planner_builds_generic_query_to_bitable_plan_when_using_query_create_term() -> None:
    route = BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(
        canonical_question="帮我查一下项目清单并创建一张汇总表",
    )
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=False)
    )

    assert [step["kind"] for step in payload["steps"]] == ["guardrail", "tool", "tool", "tool", "answer"]
    tool_steps = [step["name"] for step in payload["steps"] if step["kind"] == "tool"]
    assert tool_steps == [
        "bitable_qa",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    assert payload["requires_confirmation"] is False


def test_agent_planner_builds_generic_query_to_bitable_table_plan_when_using_export_term() -> None:
    route = BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(
        canonical_question="查询项目清单并导出到一张表里",
    )
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=False)
    )

    assert [step["kind"] for step in payload["steps"]] == ["guardrail", "tool", "tool", "tool", "answer"]
    tool_names = [step["name"] for step in payload["steps"] if step["kind"] == "tool"]
    assert tool_names == [
        "bitable_qa",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]


def test_agent_planner_builds_generic_query_to_bitable_table_plan_when_using_sync_term() -> None:
    route = BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="查询任务清单并同步到表里")
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=False)
    )

    tool_steps = [step for step in payload["steps"] if step["kind"] == "tool"]
    assert [step["name"] for step in tool_steps] == [
        "bitable_qa",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]


def test_agent_planner_builds_generic_query_to_bitable_table_plan_for_task_route() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="查询任务清单并创建一张任务同步表")
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=True)
    )

    tool_steps = [step for step in payload["steps"] if step["kind"] == "tool"]
    assert [step["name"] for step in tool_steps] == [
        "task_qa",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]


def test_agent_planner_does_not_build_bitable_plan_when_task_route_is_decision_category() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(
        canonical_question="项目任务该不该延期，建一张任务风险表",
        execution_category="decision",
    )
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=True)
    )

    tool_steps = [step["name"] for step in payload["steps"] if step["kind"] == "tool"]
    assert tool_steps == ["task_qa"]
    assert payload["execution_category"] == "decision"


def test_agent_planner_does_not_build_bitable_plan_when_task_route_is_analysis_category() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(
        canonical_question="项目任务趋势如何，帮我建一张风险表",
        execution_category="analysis",
    )
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=False)
    )

    tool_steps = [step["name"] for step in payload["steps"] if step["kind"] == "tool"]
    assert tool_steps == ["task_qa"]
    assert payload["execution_category"] == "analysis"


def test_agent_planner_builds_generic_query_to_bitable_table_plan_for_approval_route() -> None:
    route = BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="查一下待我处理的报销单并创建审批清单表")
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=True)
    )

    tool_steps = [step for step in payload["steps"] if step["kind"] == "tool"]
    assert [step["name"] for step in tool_steps] == [
        "feishu_approval_task_query",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]


def test_agent_planner_builds_generic_query_to_bitable_table_plan_for_approval_tool_route() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="把待我处理的报销单同步到审批清单表")
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=True)
    )

    tool_steps = [step for step in payload["steps"] if step["kind"] == "tool"]
    assert [step["name"] for step in tool_steps] == [
        "approval_qa",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]


def test_agent_planner_does_not_build_bitable_plan_when_approval_tool_route_is_decision_category() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(
        canonical_question="报销单是否异常，帮我建一张审批报表",
        execution_category="decision",
    )
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=True)
    )

    tool_steps = [step["name"] for step in payload["steps"] if step["kind"] == "tool"]
    assert tool_steps == ["approval_qa"]
    assert payload["execution_category"] == "decision"


def test_agent_planner_does_not_build_bitable_plan_when_approval_tool_route_decision_text_without_explicit_category() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="报销申请是否合理，帮我建一张审批清单表")
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=True)
    )

    tool_steps = [step["name"] for step in payload["steps"] if step["kind"] == "tool"]
    assert tool_steps == ["approval_qa"]
    assert payload["execution_category"] == "decision"


def test_agent_planner_does_not_build_bitable_plan_when_approval_tool_route_is_analysis_category() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(
        canonical_question="审批报表趋势如何，顺便建一张审批清单表",
        execution_category="analysis",
    )
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=False)
    )

    tool_steps = [step["name"] for step in payload["steps"] if step["kind"] == "tool"]
    assert tool_steps == ["approval_qa"]
    assert payload["execution_category"] == "analysis"


def test_agent_planner_does_not_build_bitable_plan_when_approval_tool_route_is_analysis_category_with_overdue_term() -> None:
    route = BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(
        canonical_question="报销超期，先帮我建一张审批清单表",
        execution_category="analysis",
    )
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=True)
    )

    tool_steps = [step["name"] for step in payload["steps"] if step["kind"] == "tool"]
    assert tool_steps == ["approval_qa"]
    assert payload["execution_category"] == "analysis"


def test_agent_planner_builds_generic_query_to_bitable_table_plan_for_personal_tasks_route() -> None:
    route = BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="查一下我的待办事项并创建一张个人待办表")
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=True)
    )

    tool_steps = [step for step in payload["steps"] if step["kind"] == "tool"]
    assert [step["name"] for step in tool_steps] == [
        "personal_tasks",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]


def test_agent_planner_does_not_build_bitable_plan_when_personal_tasks_route_is_analysis_category() -> None:
    route = BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(
        canonical_question="我的待办趋势怎样，顺便做一张待办表",
        execution_category="analysis",
    )
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=True)
    )

    tool_steps = [step["name"] for step in payload["steps"] if step["kind"] == "tool"]
    assert tool_steps == ["personal_tasks"]
    assert payload["execution_category"] == "analysis"


def test_agent_planner_builds_generic_query_to_bitable_table_plan_for_chat_tasks_route() -> None:
    route = BotAnswerRoute(path="chat_tasks", scope="chat", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="把这个群待办查一下，创建一张群待办同步表")
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=True)
    )

    tool_steps = [step for step in payload["steps"] if step["kind"] == "tool"]
    assert [step["name"] for step in tool_steps] == [
        "chat_tasks",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    assert payload["requires_confirmation"] is True
    assert payload["execution_category"] == "query"


def test_agent_planner_does_not_build_query_to_bitable_plan_for_chat_tasks_without_table_terms() -> None:
    route = BotAnswerRoute(path="chat_tasks", scope="chat", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="这个群有什么待办")
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=4, require_write_confirmation=False)
    )

    tool_steps = [step["name"] for step in payload["steps"] if step["kind"] == "tool"]
    assert tool_steps == ["chat_tasks"]


def test_agent_planner_builds_generic_query_to_bitable_table_plan_for_mail_route() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="把最近邮件导出并创建一张邮件清单表")
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=True)
    )

    tool_steps = [step for step in payload["steps"] if step["kind"] == "tool"]
    assert [step["name"] for step in tool_steps] == [
        "mail_qa",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]


def test_agent_planner_builds_generic_query_to_bitable_table_plan_for_mail_route_with_overdue_term() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="邮件逾期，帮我查一下邮件并创建邮件清单表")
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=True)
    )

    tool_steps = [step for step in payload["steps"] if step["kind"] == "tool"]
    assert [step["name"] for step in tool_steps] == [
        "mail_qa",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    assert payload["execution_category"] == "query"
    assert payload["execution_category_source"] == "route_fallback"


def test_agent_planner_does_not_build_generic_query_to_bitable_table_plan_for_mail_trend_route() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="请问最近邮件趋势是否有异常")
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=True)
    )

    tool_steps = [step["name"] for step in payload["steps"] if step["kind"] == "tool"]
    assert tool_steps == ["mail_qa"]


def test_agent_planner_does_not_build_generic_query_to_bitable_table_plan_for_approval_trend_route() -> None:
    route = BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="待我处理的报销单趋势怎样")
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=True)
    )

    tool_steps = [step["name"] for step in payload["steps"] if step["kind"] == "tool"]
    assert tool_steps == ["feishu_approval_task_query"]


def test_agent_planner_does_not_build_bitable_plan_when_approval_route_is_decision_category() -> None:
    route = BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(
        canonical_question="待我处理报销单该不该通过，建一张审批清单表",
        execution_category="decision",
    )
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=True)
    )

    tool_steps = [step["name"] for step in payload["steps"] if step["kind"] == "tool"]
    assert tool_steps == ["feishu_approval_task_query"]
    assert payload["execution_category"] == "decision"
    assert payload["execution_category_source"] == "semantic"


def test_agent_planner_does_not_build_bitable_plan_for_task_route_decision_text_without_explicit_category() -> None:
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="项目任务该不该延期，帮我建一张任务清单表")
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=True)
    )

    tool_steps = [step["name"] for step in payload["steps"] if step["kind"] == "tool"]
    assert tool_steps == ["task_qa"]
    assert payload["execution_category"] == "decision"


def test_agent_planner_does_not_build_bitable_plan_for_personal_route_decision_text_without_explicit_category() -> None:
    route = BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="我的待办要不要优先处理，建一张待办表")
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=True)
    )

    tool_steps = [step["name"] for step in payload["steps"] if step["kind"] == "tool"]
    assert tool_steps == ["personal_tasks"]
    assert payload["execution_category"] == "decision"


def test_agent_planner_does_not_build_bitable_plan_for_mail_route_decision_text_without_explicit_category() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="近期异常邮件是否要关注，建一张邮件表")
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=True)
    )

    tool_steps = [step["name"] for step in payload["steps"] if step["kind"] == "tool"]
    assert tool_steps == ["mail_qa"]
    assert payload["execution_category"] == "decision"


def test_agent_planner_does_not_build_bitable_plan_for_chat_route_decision_text_without_explicit_category() -> None:
    route = BotAnswerRoute(path="chat_tasks", scope="chat", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="群里待办该不该先处理，建一张待办表")
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=True)
    )

    tool_steps = [step["name"] for step in payload["steps"] if step["kind"] == "tool"]
    assert tool_steps == ["chat_tasks"]
    assert payload["execution_category"] == "decision"


def test_agent_planner_does_not_build_generic_query_to_bitable_table_plan_for_chat_task_trend_route() -> None:
    route = BotAnswerRoute(path="chat_tasks", scope="chat", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="查一下群里待办趋势并建一张待办表")
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=True)
    )

    tool_steps = [step["name"] for step in payload["steps"] if step["kind"] == "tool"]
    assert tool_steps == ["chat_tasks"]


def test_agent_planner_does_not_build_bitable_plan_when_chat_task_route_is_analysis_category() -> None:
    route = BotAnswerRoute(path="chat_tasks", scope="chat", reason="permission_scope")
    semantic = SimpleNamespace(
        canonical_question="把群待办按趋势分析并导出到一张表里",
        execution_category="analysis",
    )
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=True)
    )

    tool_steps = [step["name"] for step in payload["steps"] if step["kind"] == "tool"]
    assert tool_steps == ["chat_tasks"]
    assert payload["execution_category"] == "analysis"
    assert payload["execution_category_source"] == "semantic"


def test_agent_planner_does_not_build_bitable_plan_when_mail_route_is_decision_category() -> None:
    route = BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(
        canonical_question="最近邮件趋势是否异常，帮我建一张邮件统计表",
        execution_category="decision",
    )
    payload = agent_plan_payload(
        build_agent_plan(route=route, semantic=semantic, max_steps=6, require_write_confirmation=True)
    )

    tool_steps = [step["name"] for step in payload["steps"] if step["kind"] == "tool"]
    assert tool_steps == ["mail_qa"]
    assert payload["execution_category"] == "decision"
    assert payload["execution_category_source"] == "semantic"


def test_agent_planner_organization_template_applies_to_organization_snapshot_route() -> None:
    route = BotAnswerRoute(path="feishu_contact_organization_snapshot", scope="company", reason="permission_scope")
    payload = agent_plan_payload(
        build_agent_plan(
            route=route,
            semantic=SimpleNamespace(canonical_question="请帮我创建一张表并把最新组织放进去"),
            max_steps=6,
            require_write_confirmation=True,
        )
    )

    assert payload["route_path"] == "feishu_contact_organization_snapshot"
    assert [step["kind"] for step in payload["steps"]] == ["guardrail", "tool", "tool", "tool", "answer"]
    assert [step["name"] for step in payload["steps"] if step["kind"] == "tool"] == [
        "feishu_contact_organization_snapshot",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    snapshot_step = next(step for step in payload["steps"] if step["name"] == "feishu_contact_organization_snapshot")
    table_step = next(step for step in payload["steps"] if step["name"] == "feishu_bitable_table_create")
    record_step = next(step for step in payload["steps"] if step["name"] == "feishu_bitable_record_batch_create")
    assert snapshot_step["required"] is True
    assert snapshot_step["on_error"] == "stop"
    assert snapshot_step["depends_on"] == []
    assert table_step["required"] is True
    assert table_step["depends_on"] == ["feishu_contact_organization_snapshot"]
    assert table_step["on_error"] == "stop"
    assert record_step["required"] is True
    assert record_step["depends_on"] == ["feishu_bitable_table_create"]
    assert record_step["on_error"] == "continue"


def test_agent_planner_meeting_search_template_matches_meeting_intent() -> None:
    """meeting_search_analysis template 应该匹配会议相关的查询。"""
    route = BotAnswerRoute(path="feishu_vc_meeting_search", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="帮我看看最近开了哪些会")

    payload = agent_plan_payload(build_agent_plan(route=route, semantic=semantic, max_steps=5))

    assert payload["route_path"] == "feishu_vc_meeting_search"
    tool_steps = [step for step in payload["steps"] if step["kind"] == "tool"]
    assert len(tool_steps) == 1
    assert tool_steps[0]["name"] == "feishu_vc_meeting_search"


def test_agent_planner_meeting_search_template_matches_meeting_summary_intent() -> None:
    """meeting_search_analysis template 应该匹配会议纪要类的查询。"""
    route = BotAnswerRoute(path="feishu_vc_meeting_search", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="帮我总结一下本周的会议纪要")

    payload = agent_plan_payload(build_agent_plan(route=route, semantic=semantic, max_steps=5))

    tool_steps = [step for step in payload["steps"] if step["kind"] == "tool"]
    assert len(tool_steps) == 1
    assert tool_steps[0]["name"] == "feishu_vc_meeting_search"


def test_agent_planner_task_overview_template_matches_task_intent() -> None:
    """task_overview_analysis template 应该匹配任务概览类的查询。"""
    route = BotAnswerRoute(path="personal_tasks", scope="personal", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="帮我整理一下待办")

    payload = agent_plan_payload(build_agent_plan(route=route, semantic=semantic, max_steps=5))

    tool_steps = [step for step in payload["steps"] if step["kind"] == "tool"]
    assert len(tool_steps) == 1
    assert tool_steps[0]["name"] == "personal_tasks"


def test_agent_planner_task_overview_template_matches_task_classify_intent() -> None:
    """task_overview_analysis template 应该匹配任务分类概览类的查询。"""
    route = BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="帮我按项目分类整理一下任务")

    payload = agent_plan_payload(build_agent_plan(route=route, semantic=semantic, max_steps=5))

    tool_steps = [step for step in payload["steps"] if step["kind"] == "tool"]
    assert len(tool_steps) == 1
    assert tool_steps[0]["name"] == "task_qa"


def test_agent_planner_meeting_search_plan_has_optimized_params() -> None:
    """meeting_search_analysis template 应该包含优化的工具参数。"""
    route = BotAnswerRoute(path="feishu_vc_meeting_search", scope="company", reason="permission_scope")
    semantic = SimpleNamespace(canonical_question="帮我看看最近的会议")

    payload = agent_plan_payload(build_agent_plan(route=route, semantic=semantic, max_steps=5))

    tool_step = next(step for step in payload["steps"] if step["kind"] == "tool")
    assert "plan_context_keys" in tool_step["metadata"]
    assert "meetings" in tool_step["metadata"]["plan_context_keys"]







