from app.services.agent.intents import classify_bot_intent


def test_classify_pending_approval_from_owner_natural_language() -> None:
    intent = classify_bot_intent("那现在有需要我批复的审批吗")

    assert intent.name == "pending_approval"
    assert intent.canonical_command == "最近审批"
    assert intent.route_hint == "feishu_approval_task_query"
    assert intent.module_hint == "approvals"


def test_classify_pending_approval_from_generic_document_wording() -> None:
    intent = classify_bot_intent("有没有需要我处理的单子")

    assert intent.name == "pending_approval"
    assert intent.canonical_command == "最近审批"


def test_classify_recent_approval_normalized_command_as_realtime_pending_approval() -> None:
    intent = classify_bot_intent("审批", normalized_command="最近审批")

    assert intent.name == "pending_approval"
    assert intent.canonical_command == "最近审批"
    assert intent.route_hint == "feishu_approval_task_query"


def test_classify_pending_approval_with_explicit_application_subject() -> None:
    intent = classify_bot_intent("请查一下待我处理的报销申请")

    assert intent.name == "pending_approval"
    assert intent.route_hint == "feishu_approval_task_query"
    assert intent.module_hint == "approvals"


def test_classify_pending_approval_with_table_query_as_bitable_task() -> None:
    intent = classify_bot_intent("请查一下待我处理的报销申请，并创建表格写入")

    assert intent.name == "bitable"
    assert intent.route_hint == "bitable_qa"
    assert intent.module_hint == "resources"


def test_classify_approval_advice_question() -> None:
    intent = classify_bot_intent("这笔付款我该不该同意")

    assert intent.name == "approval_advice"
    assert intent.canonical_command == "审批建议"
    assert intent.route_hint == "approval_qa"


def test_classify_knowledge_question_before_approval_wording() -> None:
    intent = classify_bot_intent("报销流程怎么做")

    assert intent.name == "knowledge"
    assert intent.route_hint == "public_knowledge_qa"


def test_classify_data_coverage_question() -> None:
    intent = classify_bot_intent("系统数据有什么盲区")

    assert intent.name == "data_coverage"
    assert intent.module_hint == "resources"
    assert intent.canonical_question == "系统数据覆盖和数据盲区"


def test_classify_calendar_question_before_today_focus() -> None:
    intent = classify_bot_intent("今天有什么会议")

    assert intent.name == "calendar"
    assert intent.route_hint == "calendar_qa"


def test_classify_calendar_create_question_as_write_tool() -> None:
    intent = classify_bot_intent("帮我新建一个日程")

    assert intent.name == "calendar_create"
    assert intent.route_hint == "feishu_calendar_create_event"


def test_classify_meeting_record_question_as_meeting_tool() -> None:
    intent = classify_bot_intent("昨天会议纪要在哪里")

    assert intent.name == "meeting_record"
    assert intent.route_hint == "feishu_vc_meeting_search"


def test_classify_create_task_question_to_task_create_tool() -> None:
    intent = classify_bot_intent("帮我创建一个任务")

    assert intent.name == "create_task"
    assert intent.route_hint == "feishu_task_create"
    assert intent.module_hint == "tasks"
    assert intent.canonical_command == "创建任务"


def test_classify_task_query_with_table_sync_as_bitable_task() -> None:
    intent = classify_bot_intent("把待办任务查一下，做一张表并写入")

    assert intent.name == "bitable"
    assert intent.route_hint == "bitable_qa"


def test_classify_personal_tasks_sync_to_bitable() -> None:
    intent = classify_bot_intent("我的待办同步到表")

    assert intent.name == "bitable"
    assert intent.route_hint == "bitable_qa"


def test_classify_task_sync_as_bitable_task() -> None:
    intent = classify_bot_intent("把待办任务同步到表里")

    assert intent.name == "bitable"
    assert intent.route_hint == "bitable_qa"


def test_classify_colloquial_create_task_to_task_create_tool() -> None:
    intent = classify_bot_intent("帮我建个待办")

    assert intent.name == "create_task"
    assert intent.route_hint == "feishu_task_create"
    assert intent.module_hint == "tasks"


def test_classify_colloquial_create_task_with_task_suffix_to_task_create_tool() -> None:
    intent = classify_bot_intent("帮我起一条待办")

    assert intent.name == "create_task"
    assert intent.route_hint == "feishu_task_create"
    assert intent.module_hint == "tasks"


def test_classify_mail_question() -> None:
    intent = classify_bot_intent("最近邮件有什么")

    assert intent.name == "mail"
    assert intent.route_hint == "mail_qa"


def test_classify_mail_query_with_table_import_as_bitable_task() -> None:
    intent = classify_bot_intent("把最近的邮件查一下，创建一张表格同步进去")

    assert intent.name == "bitable"
    assert intent.route_hint == "bitable_qa"
    assert intent.module_hint == "resources"


def test_classify_mail_sync_to_bitable_task() -> None:
    intent = classify_bot_intent("把最近邮件同步到邮件表")

    assert intent.name == "bitable"
    assert intent.route_hint == "bitable_qa"
    assert intent.module_hint == "resources"


def test_classify_approval_sync_to_bitable_task() -> None:
    intent = classify_bot_intent("把待我处理的报销单同步到审批表")

    assert intent.name == "bitable"
    assert intent.route_hint == "bitable_qa"
    assert intent.module_hint == "resources"


def test_classify_bitable_question() -> None:
    intent = classify_bot_intent("多维表格里客户回款怎么样")

    assert intent.name == "bitable"
    assert intent.route_hint == "bitable_qa"


def test_classify_project_progress_as_bitable_master_data_question() -> None:
    intent = classify_bot_intent("项目进展怎么样")

    assert intent.name == "project"
    assert intent.route_hint == "bitable_qa"
    assert intent.module_hint == "projects"


def test_classify_group_summary_as_chat_summary_before_report() -> None:
    intent = classify_bot_intent("总结一下这个群")

    assert intent.name == "chat_summary"
    assert intent.route_hint == "chat_summary"


def test_classify_group_tasks_before_group_summary() -> None:
    intent = classify_bot_intent("这个群有什么待办任务")

    assert intent.name == "chat_tasks"
    assert intent.route_hint == "chat_tasks"


def test_classify_group_task_query_with_table_import_as_bitable_task() -> None:
    intent = classify_bot_intent("把这个群的待办任务查一下，建一张待办表并写入")

    assert intent.name == "bitable"
    assert intent.route_hint == "bitable_qa"
    assert intent.module_hint == "resources"


def test_classify_group_task_query_without_write_terms_stays_chat_tasks() -> None:
    intent = classify_bot_intent("这个群的待办趋势怎样")

    assert intent.name == "chat_tasks"
    assert intent.route_hint == "chat_tasks"


def test_classify_group_task_query_without_table_write_terms_stays_chat_tasks() -> None:
    intent = classify_bot_intent("群里待办趋势有没有上升风险")

    assert intent.name == "chat_tasks"
    assert intent.route_hint == "chat_tasks"


def test_classify_group_task_trend_with_table_terms_stays_chat_tasks() -> None:
    intent = classify_bot_intent("群里待办趋势查一下，建一张待办表")

    assert intent.name == "chat_tasks"
    assert intent.route_hint == "chat_tasks"


def test_classify_pending_approval_trend_query_stays_pending_approval() -> None:
    intent = classify_bot_intent("待我处理报销单趋势如何")

    assert intent.name == "pending_approval"
    assert intent.route_hint == "feishu_approval_task_query"


def test_classify_pending_approval_trend_with_table_terms_stays_pending_approval() -> None:
    intent = classify_bot_intent("待我处理报销单趋势查一查并建一张清单表")

    assert intent.name == "pending_approval"
    assert intent.route_hint == "feishu_approval_task_query"


def test_classify_mail_trend_query_stays_mail_qa() -> None:
    intent = classify_bot_intent("最近邮件趋势怎么样")

    assert intent.name == "mail"
    assert intent.route_hint == "mail_qa"


def test_classify_mail_trend_with_table_terms_stays_mail_qa() -> None:
    intent = classify_bot_intent("请帮我把最近邮件趋势查一下，并建一张邮件表")

    assert intent.name == "mail"
    assert intent.route_hint == "mail_qa"


def test_classify_people_question() -> None:
    intent = classify_bot_intent("组织架构怎么看")

    assert intent.name == "people"
    assert intent.route_hint == "feishu_contact_organization_snapshot"
