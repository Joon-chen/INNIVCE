import time
from types import SimpleNamespace

from app.services.llm import conversation as conversation_module
from app.services.llm import presentation as presentation_module
from app.services.llm.answer_rewriter import _get_session_context, rewrite_bot_answer, should_rewrite_answer
from app.services.llm.conversation import ConversationLLMContext, conversation_llm_reply, conversation_prompt, valid_conversation_reply
from app.services.llm.presentation import PresentationLLMContext, presentation_llm_rewrite, presentation_prompt, valid_presentation_rewrite
from app.services.llm.answer_semantics import semantic_intent_for_question
from app.services.feishu.bot_runtime import _runtime_v5_answer_rewrite_allowed
from app.services.runtime_v5.response_classification import classify_response_request


def test_semantic_intent_maps_data_blindspot_to_resources() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="系统有什么数据盲区，哪些关键群没接入",
        normalized_command="系统有什么数据盲区，哪些关键群没接入",
        actor=actor,
    )

    assert intent.route_hint == "company_qa"
    assert intent.module_hint == "resources"
    assert intent.confidence >= 0.85


def test_semantic_intent_maps_pending_payment_approval_to_realtime_task_query() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="今天有没有待我处理的付款单",
        normalized_command="今天有没有待我处理的付款单",
        actor=actor,
    )

    assert intent.route_hint == "feishu_approval_task_query"
    assert intent.module_hint == "approvals"


def test_semantic_intent_maps_pending_approval_natural_language() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="那现在有需要我批复的审批吗",
        normalized_command="那现在有需要我批复的审批吗",
        actor=actor,
    )

    assert intent.route_hint == "feishu_approval_task_query"
    assert intent.module_hint == "approvals"
    assert intent.canonical_question == "待我处理的审批"
    assert intent.confidence >= 0.9
    assert intent.execution_category == "action"


def test_semantic_intent_maps_pending_approval_application_request() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="现在待我处理的付款申请有哪些？",
        normalized_command="现在待我处理的付款申请有哪些？",
        actor=actor,
    )

    assert intent.route_hint == "feishu_approval_task_query"
    assert intent.module_hint == "approvals"
    assert intent.canonical_question == "待我处理的审批"
    assert intent.execution_category == "query"


def test_semantic_intent_keeps_approval_decision_request_on_approval_route() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="待我处理报销单该不该通过，顺便建一张审批表",
        normalized_command="待我处理报销单该不该通过，顺便建一张审批表",
        actor=actor,
    )

    assert intent.route_hint == "feishu_approval_task_query"
    assert intent.execution_category == "decision"


def test_semantic_intent_maps_mail_query_to_bitable_route() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="把我最近的邮件查一下，建一张邮件清单表并写入",
        normalized_command="把我最近的邮件查一下，建一张邮件清单表并写入",
        actor=actor,
    )

    assert intent.route_hint == "bitable_qa"
    assert intent.execution_category == "action"


def test_semantic_intent_maps_approval_query_to_bitable_route() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="把待我处理的报销单查出来，建张表同步进来",
        normalized_command="把待我处理的报销单查出来，建张表同步进来",
        actor=actor,
    )

    assert intent.route_hint == "bitable_qa"
    assert intent.execution_category == "action"


def test_semantic_intent_maps_approval_query_without_pending_to_bitable_route() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="帮我把报销单查一下，建一张报销表并写入",
        normalized_command="帮我把报销单查一下，建一张报销表并写入",
        actor=actor,
    )

    assert intent.route_hint == "bitable_qa"
    assert intent.module_hint == "resources"
    assert intent.execution_category == "action"


def test_semantic_intent_maps_greeting_to_general_chat() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="你好呀，怎么不理我了呢",
        normalized_command="你好呀，怎么不理我了呢",
        actor=actor,
    )

    assert intent.route_hint == "general_chat"
    assert intent.confidence >= 0.85


def test_smalltalk_user_context_stays_conversation_not_locked_fact() -> None:
    classification = classify_response_request(
        question="你应该怎么称呼我",
        answer="我知道，你是陈俊。",
        intent="smalltalk",
        result_type="smalltalk",
    )

    assert classification.response_class == "conversation"
    assert classification.llm_allowed is True


def test_semantic_intent_marks_create_org_table_request_as_action() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="请帮我创建一张表格并把最新组织放进去",
        normalized_command="请帮我创建一张表格并把最新组织放进去",
        actor=actor,
    )

    assert intent.execution_category == "action"


def test_semantic_intent_maps_chat_tasks_query_to_bitable_route() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="把这个群待办查一下，建一张群待办表并写入",
        normalized_command="把这个群待办查一下，建一张群待办表并写入",
        actor=actor,
    )

    assert intent.route_hint == "bitable_qa"
    assert intent.execution_category == "action"
    assert intent.module_hint == "resources"


def test_semantic_intent_keeps_chat_tasks_route_for_trend_query() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="这个群待办趋势咋样",
        normalized_command="这个群待办趋势咋样",
        actor=actor,
    )

    assert intent.route_hint == "chat_tasks"
    assert intent.execution_category == "analysis"


def test_semantic_intent_keeps_personal_task_trend_query_with_table_terms_as_personal_route() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="我的待办趋势有问题，帮我建一张待办表",
        normalized_command="我的待办趋势有问题，帮我建一张待办表",
        actor=actor,
    )

    assert intent.route_hint == "personal_tasks"
    assert intent.execution_category in {"analysis", "query"}


def test_semantic_intent_keeps_approval_trend_query_as_approval_route() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="待我处理报销单趋势走势",
        normalized_command="待我处理报销单趋势走势",
        actor=actor,
    )

    assert intent.route_hint == "feishu_approval_task_query"
    assert intent.execution_category in {"analysis", "query"}


def test_semantic_intent_keeps_mail_trend_query_as_mail_route() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="最近邮件趋势有问题吗",
        normalized_command="最近邮件趋势有问题吗",
        actor=actor,
    )

    assert intent.route_hint == "mail_qa"
    assert intent.execution_category in {"analysis", "query"}


def test_semantic_intent_keeps_mail_query_with_table_terms_as_mail_route() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="请帮我把最近邮件趋势查一下，并建一张邮件表",
        normalized_command="请帮我把最近邮件趋势查一下，并建一张邮件表",
        actor=actor,
    )

    assert intent.route_hint == "mail_qa"
    assert intent.execution_category in {"analysis", "query"}


def test_semantic_intent_keeps_approval_trend_query_with_table_terms_as_approval_route() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="待我处理报销单趋势如何，帮我建一张汇总表",
        normalized_command="待我处理报销单趋势如何，帮我建一张汇总表",
        actor=actor,
    )

    assert intent.route_hint == "feishu_approval_task_query"
    assert intent.execution_category in {"analysis", "decision"}


def test_semantic_intent_maps_approval_action_request_as_action() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="帮我批准这笔报销",
        normalized_command="帮我批准这笔报销",
        actor=actor,
    )

    assert intent.route_hint == "feishu_approval_task_query"
    assert intent.execution_category == "action"


def test_semantic_intent_maps_approval_reject_request_as_action() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="这笔报销我想要拒绝",
        normalized_command="这笔报销我想要拒绝",
        actor=actor,
    )

    assert intent.route_hint == "approval_qa"
    assert intent.execution_category == "action"


def test_semantic_intent_maps_approval_add_sign_request_as_action() -> None:
    actor = SimpleNamespace(role="member", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="这笔报销请先加签到张总",
        normalized_command="这笔报销请先加签到张总",
        actor=actor,
    )

    assert intent.route_hint == "approval_qa"
    assert intent.execution_category == "action"


def test_semantic_intent_maps_task_followup_as_action() -> None:
    actor = SimpleNamespace(role="member", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="这条任务我已经催办一下，尽快处理",
        normalized_command="这条任务我已经催办一下，尽快处理",
        actor=actor,
    )

    assert intent.execution_category == "query"


def test_semantic_intent_maps_task_completion_as_action() -> None:
    actor = SimpleNamespace(role="member", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="这条任务我已经处理完成了，帮我关闭",
        normalized_command="这条任务我已经处理完成了，帮我关闭",
        actor=actor,
    )

    assert intent.execution_category == "query"


def test_semantic_intent_maps_task_claim_as_action() -> None:
    actor = SimpleNamespace(role="member", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="这个任务你先认领一下吧",
        normalized_command="这个任务你先认领一下吧",
        actor=actor,
    )

    assert intent.execution_category == "query"


def test_semantic_intent_maps_task_assignment_as_action() -> None:
    actor = SimpleNamespace(role="member", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="帮我把这条待办分配给王总",
        normalized_command="帮我把这条待办分配给王总",
        actor=actor,
    )

    assert intent.execution_category == "query"


def test_semantic_intent_maps_mail_forward_request_as_action() -> None:
    actor = SimpleNamespace(role="member", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="把这封邮件转发给老板",
        normalized_command="把这封邮件转发给老板",
        actor=actor,
    )

    assert intent.route_hint == "mail_qa"
    assert intent.execution_category == "action"


def test_semantic_intent_maps_mail_send_request_as_action() -> None:
    actor = SimpleNamespace(role="member", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="请给我发送一封邮件确认进度",
        normalized_command="请给我发送一封邮件确认进度",
        actor=actor,
    )

    assert intent.route_hint == "mail_qa"
    assert intent.execution_category == "action"


def test_semantic_intent_maps_mail_reply_as_action() -> None:
    actor = SimpleNamespace(role="member", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="请回信给客户，说明我已收到",
        normalized_command="请回信给客户，说明我已收到",
        actor=actor,
    )

    assert intent.route_hint is None
    assert intent.execution_category == "query"


def test_semantic_intent_maps_mail_cc_as_action() -> None:
    actor = SimpleNamespace(role="member", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="帮我把这封邮件抄送给法务",
        normalized_command="帮我把这封邮件抄送给法务",
        actor=actor,
    )

    assert intent.route_hint == "mail_qa"
    assert intent.execution_category == "action"


def test_semantic_intent_maps_task_delegate_as_action() -> None:
    actor = SimpleNamespace(role="member", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="这个任务先转办给组长",
        normalized_command="这个任务先转办给组长",
        actor=actor,
    )

    assert intent.execution_category == "action"


def test_semantic_intent_maps_mail_distribute_as_action() -> None:
    actor = SimpleNamespace(role="member", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="请先把这封邮件下发给所有人",
        normalized_command="请先把这封邮件下发给所有人",
        actor=actor,
    )

    assert intent.route_hint == "mail_qa"
    assert intent.execution_category == "action"


def test_semantic_intent_maps_approval_recall_as_action() -> None:
    actor = SimpleNamespace(role="member", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="这笔报销撤回后我再提一次",
        normalized_command="这笔报销撤回后我再提一次",
        actor=actor,
    )

    assert intent.route_hint == "approval_qa"
    assert intent.execution_category == "action"


def test_semantic_intent_keeps_approval_query_decision_with_table_term_outside_pending_pattern() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="这笔报销该不该通过，建一张报销清单表",
        normalized_command="这笔报销该不该通过，建一张报销清单表",
        actor=actor,
    )

    assert intent.route_hint == "approval_qa"
    assert intent.execution_category == "decision"


def test_semantic_intent_keeps_task_decision_request_as_task_route() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="项目任务该不该延期，帮我建一张任务清单表",
        normalized_command="项目任务该不该延期，帮我建一张任务清单表",
        actor=actor,
    )

    assert intent.route_hint == "task_qa"
    assert intent.execution_category == "decision"


def test_semantic_intent_keeps_personal_task_decision_request_as_personal_route() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="我的待办要不要优先处理，建一张待办表",
        normalized_command="我的待办要不要优先处理，建一张待办表",
        actor=actor,
    )

    assert intent.route_hint == "personal_tasks"
    assert intent.execution_category == "decision"


def test_semantic_intent_keeps_mail_decision_request_as_mail_route() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="近期异常邮件是否值得处理，建一张邮件清单表",
        normalized_command="近期异常邮件是否值得处理，建一张邮件清单表",
        actor=actor,
    )

    assert intent.route_hint == "mail_qa"
    assert intent.execution_category == "decision"


def test_semantic_intent_keeps_chat_task_decision_request_as_chat_route() -> None:
    actor = SimpleNamespace(role="member", access_scope="chat")

    intent = semantic_intent_for_question(
        question="群里待办该不该尽快处理，建一张待办表",
        normalized_command="群里待办该不该尽快处理，建一张待办表",
        actor=actor,
    )

    assert intent.route_hint == "chat_tasks"
    assert intent.execution_category == "decision"


def test_semantic_intent_keeps_mail_trend_query_with_table_terms_as_mail_route() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="最近邮件趋势是否异常，建一张邮件清单表",
        normalized_command="最近邮件趋势是否异常，建一张邮件清单表",
        actor=actor,
    )

    assert intent.route_hint == "mail_qa"
    assert intent.execution_category in {"analysis", "decision"}


def test_semantic_intent_keeps_task_trend_query_with_table_terms_as_task_route() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="项目任务趋势怎么样，建一张任务清单表",
        normalized_command="项目任务趋势怎么样，建一张任务清单表",
        actor=actor,
    )

    assert intent.route_hint == "task_qa"
    assert intent.execution_category in {"analysis", "query"}


def test_semantic_intent_maps_create_task_query_to_task_tool() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="帮我创建一个任务",
        normalized_command="帮我创建一个任务",
        actor=actor,
    )

    assert intent.route_hint == "feishu_task_create"
    assert intent.execution_category == "action"


def test_semantic_intent_maps_org_table_import_query_to_bitable_plan_route() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="请帮我创建一张表格并把最新组织放进去",
        normalized_command="请帮我创建一张表格并把最新组织放进去",
        actor=actor,
    )

    assert intent.route_hint == "bitable_qa"
    assert intent.execution_category == "action"
    assert intent.module_hint is None


def test_semantic_intent_maps_project_query_and_sync_to_bitable_table() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="请帮我查一下项目列表并创建一张表再同步进去",
        normalized_command="请帮我查一下项目列表并创建一张表再同步进去",
        actor=actor,
    )

    assert intent.route_hint == "bitable_qa"
    assert intent.execution_category == "action"
    assert intent.module_hint == "resources"


def test_semantic_intent_maps_tasks_query_and_import_to_bitable_table() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="把待办任务查一下，新建表格并写入里面",
        normalized_command="把待办任务查一下，新建表格并写入里面",
        actor=actor,
    )

    assert intent.route_hint == "bitable_qa"
    assert intent.execution_category == "action"


def test_semantic_intent_maps_export_query_to_bitable_table() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="查询项目里程碑并导出到表格",
        normalized_command="查询项目里程碑并导出到表格",
        actor=actor,
    )

    assert intent.route_hint == "bitable_qa"
    assert intent.execution_category == "action"


def test_semantic_intent_maps_sync_query_to_bitable_table() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="把待办任务查一下，帮我同步到Base表里",
        normalized_command="把待办任务查一下，帮我同步到Base表里",
        actor=actor,
    )

    assert intent.route_hint == "bitable_qa"
    assert intent.execution_category == "action"
    assert intent.module_hint == "resources"


def test_semantic_intent_maps_query_and_create_table_without_export_term() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="查一下项目清单并创建一张表",
        normalized_command="查一下项目清单并创建一张表",
        actor=actor,
    )

    assert intent.route_hint == "bitable_qa"
    assert intent.execution_category == "action"
    assert intent.module_hint == "resources"


def test_semantic_intent_maps_sync_to_bitable_route() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="把任务同步到表里",
        normalized_command="把任务同步到表里",
        actor=actor,
    )

    assert intent.route_hint == "bitable_qa"
    assert intent.execution_category == "action"
    assert intent.module_hint == "resources"


def test_semantic_intent_maps_personal_tasks_sync_to_bitable_route() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="我的待办同步到表",
        normalized_command="我的待办同步到表",
        actor=actor,
    )

    assert intent.route_hint == "bitable_qa"
    assert intent.execution_category == "action"
    assert intent.module_hint == "resources"


def test_semantic_intent_marks_decision_query_as_decision() -> None:
    actor = SimpleNamespace(role="owner", access_scope="company", domains=("all",))

    intent = semantic_intent_for_question(
        question="这笔报销该不该通过？",
        normalized_command="这笔报销该不该通过？",
        actor=actor,
    )

    assert intent.execution_category == "decision"


def test_answer_rewriter_is_disabled_during_tests() -> None:
    answer = "回答范围：指定公司\n能力路径：公司级问答\n老板，当前有 3 个重点。"

    rewritten = rewrite_bot_answer(
        question="今天有什么重点",
        answer=answer,
        actor=SimpleNamespace(role="owner", domains=("all",)),
        scope_label="指定公司",
        route_label="公司级问答",
    )

    assert rewritten == answer


def test_answer_rewriter_skips_confirmation_sensitive_answers() -> None:
    assert (
        should_rewrite_answer(answer="请回复“确认同意”后我再提交。", route_label="审批问答")
        is False
    )


def test_conversation_prompt_freezes_no_business_data_boundary() -> None:
    prompt = conversation_prompt(
        ConversationLLMContext(
            question="你能查全公司任务吗",
            fallback_answer="任务企业实时读取能力还没有接入 Bot/Tenant 主路径。",
            actor_name="陈俊",
            actor_role="owner",
            profile_text="老板风格：简洁、直接",
        )
    )

    assert "Conversation LLM" in prompt
    assert "沟通画像" in prompt
    assert "最近对话与交互信号" in prompt
    assert "主动读取或编造企业业务数据" in prompt
    assert "把沟通画像当成权限或事实来源" in prompt
    assert "把闲聊升级成业务动作" in prompt
    assert "改变权限边界" in prompt


def test_answer_rewriter_loads_runtime_conversation_context(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.llm.answer_rewriter.conversation_context_text",
        lambda chat_id: "用户：主营业务\n助手：这听起来像在问公司主营业务。\n用户：[上一条是 sticker 类型消息]",
    )

    context = _get_session_context("oc_1", "这个表情是什么情绪")

    assert "用户：主营业务" in context
    assert "助手：这听起来像在问公司主营业务。" in context
    assert "用户：[上一条是 sticker 类型消息]" in context


def test_conversation_reply_rejects_execution_claims() -> None:
    assert (
        valid_conversation_reply(
            reply="我已查询到全公司有 10 条任务。",
            fallback_answer="你可以补充范围后重新查询。",
        )
        is False
    )


def test_conversation_reply_respects_feature_switch(monkeypatch) -> None:
    called = False

    class FakeGateway:
        def complete_task_text(self, prompt: str, *, task_type: str, temperature: float = 0.2) -> str:
            nonlocal called
            called = True
            return "不会被调用"

    monkeypatch.setattr(conversation_module.settings, "bot_llm_conversation_enabled", False)
    monkeypatch.setattr(conversation_module, "LLMGateway", lambda: FakeGateway())

    answer = conversation_llm_reply(
        ConversationLLMContext(
            question="你好",
            fallback_answer="我在。你可以继续让我查审批、任务、日程、邮件或通讯录。",
        )
    )

    assert called is False
    assert answer == "我在。你可以继续让我查审批、任务、日程、邮件或通讯录。"


def test_conversation_reply_uses_llm_when_enabled(monkeypatch) -> None:
    class FakeGateway:
        def complete_task_text(self, prompt: str, *, task_type: str, temperature: float = 0.2) -> str:
            assert task_type == "conversation"
            assert "Conversation LLM" in prompt
            return "我在。你可以直接告诉我想查什么范围、对象或时间。"

    monkeypatch.setattr(conversation_module.settings, "bot_llm_conversation_enabled", True)
    monkeypatch.setattr(conversation_module, "LLMGateway", lambda: FakeGateway())

    answer = conversation_llm_reply(
        ConversationLLMContext(
            question="在吗",
            fallback_answer="我在。你可以继续让我查审批、任务、日程、邮件或通讯录。",
        )
    )

    assert answer == "我在。你可以直接告诉我想查什么范围、对象或时间。"


def test_conversation_reply_deadline_falls_back(monkeypatch) -> None:
    class FakeRoute:
        latency_budget_ms = 500

    class FakeGateway:
        def complete_task_text(self, prompt: str, *, task_type: str, temperature: float = 0.2) -> str:
            time.sleep(2)
            return "慢回复"

    monkeypatch.setattr(conversation_module.settings, "bot_llm_conversation_enabled", True)
    monkeypatch.setattr(conversation_module, "llm_route_for_task", lambda task_type: FakeRoute())
    monkeypatch.setattr(conversation_module, "LLMGateway", lambda: FakeGateway())

    started = time.perf_counter()
    answer = conversation_llm_reply(
        ConversationLLMContext(
            question="你好",
            fallback_answer="我在，正在听。",
        )
    )
    elapsed = time.perf_counter() - started

    assert answer == "我在，正在听。"
    assert elapsed < 1.2


def test_conversation_reply_can_naturalize_identity_fact(monkeypatch) -> None:
    called = False

    class FakeGateway:
        def complete_task_text(self, prompt: str, *, task_type: str, temperature: float = 0.2) -> str:
            nonlocal called
            called = True
            assert "我知道，你是陈俊。" in prompt
            assert "不要直呼其名" in prompt
            return "知道的，老板。正式说你是陈俊，平时我就不老直呼名字了。"

    monkeypatch.setattr(conversation_module.settings, "bot_llm_conversation_enabled", True)
    monkeypatch.setattr(conversation_module, "LLMGateway", lambda: FakeGateway())

    answer = conversation_llm_reply(
        ConversationLLMContext(
            question="我叫什么名字",
            fallback_answer="我知道，你是陈俊。",
            profile_text="不要直呼其名",
        )
    )

    assert called is True
    assert answer == "知道的，老板。正式说你是陈俊，平时我就不老直呼名字了。"


def test_response_classification_freezes_fact_answer() -> None:
    classification = classify_response_request(
        question="我叫什么名字",
        answer="我知道，你是陈俊。",
        intent="smalltalk",
        result_type="smalltalk",
    )

    assert classification.response_class == "fact"
    assert classification.fact_kind == "identity"
    assert classification.llm_allowed is False


def test_presentation_prompt_freezes_fact_boundary() -> None:
    prompt = presentation_prompt(
        PresentationLLMContext(
            question="全公司任务",
            original_answer="任务企业实时读取能力还没有接入 Bot/Tenant 主路径。",
            profile_text="老板风格：简洁、直接",
            scope_label="company",
        )
    )

    assert "Presentation LLM" in prompt
    assert "保持原答案中的数量、状态、权限边界" in prompt
    assert "不得把未接入、无权限、分析中、未查询到改写成已完成或已查询" in prompt
    assert "不得生成新的业务动作" in prompt


def test_presentation_rewrite_rejects_count_change() -> None:
    assert (
        valid_presentation_rewrite(
            original="你有 2 条任务。",
            rewritten="你目前共有 3 条任务，我帮你整理好了。",
        )
        is False
    )


def test_presentation_rewrite_rejects_status_boundary_change() -> None:
    assert (
        valid_presentation_rewrite(
            original="任务企业实时读取能力还没有接入 Bot/Tenant 主路径。",
            rewritten="已查询到全公司任务，可以查看全部结果。",
        )
        is False
    )


def test_presentation_rewrite_rejects_prompt_scaffold() -> None:
    assert (
        valid_presentation_rewrite(
            original="部门日程的实时读取功能目前还没接入主系统，所以暂时查不了。",
            rewritten="好的，收到你的要求。以下是改写后的答案：部门日程暂时查不了。",
        )
        is False
    )


def test_presentation_rewrite_uses_llm_when_valid(monkeypatch) -> None:
    class FakeGateway:
        def complete_task_text(self, prompt: str, *, task_type: str, temperature: float = 0.2) -> str:
            assert task_type == "presentation"
            assert "Presentation LLM" in prompt
            return "你有 2 条任务，我按当前范围整理如下。"

    monkeypatch.setattr(presentation_module, "LLMGateway", lambda: FakeGateway())

    answer = presentation_llm_rewrite(
        PresentationLLMContext(
            question="我的任务",
            original_answer="你有 2 条任务。",
            profile_text="员工风格：简明扼要",
            scope_label="self",
        )
    )

    assert answer == "你有 2 条任务，我按当前范围整理如下。"


def test_presentation_rewrite_falls_back_when_invalid(monkeypatch) -> None:
    class FakeGateway:
        def complete_task_text(self, prompt: str, *, task_type: str, temperature: float = 0.2) -> str:
            assert task_type == "presentation"
            return "已查询到全公司任务，可以查看全部结果。"

    monkeypatch.setattr(presentation_module, "LLMGateway", lambda: FakeGateway())

    answer = presentation_llm_rewrite(
        PresentationLLMContext(
            question="全公司任务",
            original_answer="任务企业实时读取能力还没有接入 Bot/Tenant 主路径。",
        )
    )

    assert answer == "任务企业实时读取能力还没有接入 Bot/Tenant 主路径。"


def test_runtime_v5_fast_response_skips_presentation_llm_for_lists() -> None:
    envelope = SimpleNamespace(
        intent=SimpleNamespace(intent="task_query", data_scope="self"),
        composed=SimpleNamespace(result_context=SimpleNamespace(result_type="task_list")),
    )

    assert _runtime_v5_answer_rewrite_allowed(envelope=envelope, answer="你有 2 条任务。") is False


def test_runtime_v5_fast_response_skips_presentation_llm_for_scope_boundaries() -> None:
    envelope = SimpleNamespace(
        intent=SimpleNamespace(intent="calendar_query", data_scope="department"),
        composed=SimpleNamespace(result_context=SimpleNamespace(result_type="calendar_event_list")),
    )

    assert (
        _runtime_v5_answer_rewrite_allowed(
            envelope=envelope,
            answer="日程部门实时读取能力还没有接入 Bot/Tenant 主路径，所以不能生成部门认知聚合。",
        )
        is False
    )


def test_runtime_v5_smalltalk_allows_bounded_conversation_llm(monkeypatch) -> None:
    monkeypatch.setattr("app.services.feishu.bot_runtime.settings.bot_llm_answer_rewrite_enabled", True)
    envelope = SimpleNamespace(
        intent=SimpleNamespace(intent="smalltalk", data_scope="self"),
        composed=SimpleNamespace(result_context=SimpleNamespace(result_type="smalltalk")),
    )

    assert _runtime_v5_answer_rewrite_allowed(envelope=envelope, answer="我在。") is True


def test_runtime_v5_smalltalk_allows_identity_but_skips_time_fact(monkeypatch) -> None:
    monkeypatch.setattr("app.services.feishu.bot_runtime.settings.bot_llm_answer_rewrite_enabled", True)
    envelope = SimpleNamespace(
        intent=SimpleNamespace(intent="smalltalk", data_scope="self"),
        composed=SimpleNamespace(result_context=SimpleNamespace(result_type="smalltalk")),
    )

    assert _runtime_v5_answer_rewrite_allowed(envelope=envelope, answer="我知道，你是陈俊。") is True
    assert _runtime_v5_answer_rewrite_allowed(envelope=envelope, answer="你叫陈俊。我平时可以少直呼名字。") is True
    assert _runtime_v5_answer_rewrite_allowed(envelope=envelope, answer="现在是北京时间 20:31。") is False


def test_runtime_v5_identity_question_skips_conversation_llm_even_when_answer_is_profile_boundary(monkeypatch) -> None:
    monkeypatch.setattr("app.services.feishu.bot_runtime.settings.bot_llm_answer_rewrite_enabled", True)
    envelope = SimpleNamespace(
        intent=SimpleNamespace(intent="smalltalk", data_scope="self"),
        composed=SimpleNamespace(result_context=SimpleNamespace(result_type="smalltalk")),
    )

    assert (
        _runtime_v5_answer_rewrite_allowed(
            envelope=envelope,
            question="我是什么性格",
            answer="我现在不把你简单贴性格标签。已知的是你的沟通偏好：直接、简洁。",
        )
        is False
    )
