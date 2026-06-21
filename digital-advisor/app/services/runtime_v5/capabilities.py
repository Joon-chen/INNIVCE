from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeCapability:
    strategy: str
    source: str
    operation: str
    question_type: str
    data_scope: str
    execution_identity: str
    requires_confirmation: bool = False
    installed: bool = True
    label: str = ""
    route_path: str = ""


@dataclass(frozen=True)
class SkillAtomicCapability:
    source: str
    operation: str
    skill: str
    question_type: str
    execution_identity: str
    risk_level: str
    registered: bool = True
    exposed: bool = False
    requires_confirmation: bool = False
    label: str = ""
    reason: str = ""


RUNTIME_CAPABILITIES: tuple[RuntimeCapability, ...] = (
    RuntimeCapability("approval_query", "approval", "list_pending", "query", "self", "bot", label="待审批查询", route_path="feishu_approval_task_query"),
    RuntimeCapability("approval_detail", "approval", "get_detail", "query", "self", "bot", label="审批详情", route_path="feishu_approval_instance_get"),
    RuntimeCapability("approval_approve", "approval", "approve", "action", "self", "user", True, label="审批通过", route_path="feishu_approval_task_approve"),
    RuntimeCapability("approval_reject", "approval", "reject", "action", "self", "user", True, label="审批拒绝", route_path="feishu_approval_task_reject"),
    RuntimeCapability("approval_transfer", "approval", "transfer", "action", "self", "user", True, label="审批转交", route_path="feishu_approval_task_transfer"),
    RuntimeCapability("approval_add_sign", "approval", "add_sign", "action", "self", "user", True, label="审批加签", route_path="feishu_approval_task_add_sign"),
    RuntimeCapability("approval_rollback", "approval", "rollback", "action", "self", "user", True, label="审批退回", route_path="feishu_approval_task_rollback"),
    RuntimeCapability("approval_remind", "approval", "remind", "action", "self", "user", True, label="审批催办", route_path="feishu_approval_instance_remind"),
    RuntimeCapability("approval_cancel", "approval", "cancel", "action", "self", "user", True, label="审批撤回", route_path="feishu_approval_instance_cancel"),
    RuntimeCapability("approval_cc", "approval", "cc", "action", "self", "user", True, label="审批抄送", route_path="feishu_approval_instance_cc"),
    RuntimeCapability("approval_initiated", "approval", "list_initiated", "query", "self", "bot", label="我发起的审批", route_path="feishu_approval_instance_initiated"),
    RuntimeCapability("people_lookup", "people", "search_person", "query", "person", "bot", label="人员查询", route_path="feishu_contact_organization_snapshot"),
    RuntimeCapability("department_members", "people", "list_department_members", "query", "department", "bot", label="部门成员", route_path="feishu_contact_organization_snapshot"),
    RuntimeCapability("organization_snapshot", "people", "get_org_snapshot", "query", "organization", "bot", label="组织架构", route_path="feishu_contact_organization_snapshot"),
    RuntimeCapability("organization_export", "people", "get_org_snapshot", "action", "organization", "user", True, label="组织架构导出", route_path="bitable_qa"),
    RuntimeCapability("organization_export", "base", "write_records", "action", "organization", "user", True, label="组织架构写表", route_path="bitable_qa"),
    RuntimeCapability("organization_export", "im", "send_result", "action", "organization", "user", True, label="导出结果发送", route_path="feishu_im_send_message"),
    RuntimeCapability("task_query", "task", "list_my_tasks", "query", "self", "bot", label="任务查询", route_path="feishu_task_query"),
    RuntimeCapability("task_search", "task", "search_tasks", "query", "self", "bot", label="任务搜索", route_path="feishu_task_query"),
    RuntimeCapability("task_create", "task", "create_task", "action", "self", "user", True, label="创建任务", route_path="feishu_task_create"),
    RuntimeCapability("task_complete", "task", "complete_task", "action", "self", "user", True, label="完成任务", route_path="feishu_task_complete"),
    RuntimeCapability("task_update", "task", "update_task", "action", "self", "user", True, label="更新任务", route_path="feishu_task_update"),
    RuntimeCapability("task_reopen", "task", "reopen_task", "action", "self", "user", True, label="重新打开任务", route_path="feishu_task_reopen"),
    RuntimeCapability("task_delete", "task", "delete_task", "action", "self", "user", True, label="删除任务", route_path="feishu_task_delete"),
    RuntimeCapability("task_subtask_create", "task", "create_subtask", "action", "self", "user", True, label="创建子任务", route_path="feishu_task_subtask_create"),
    RuntimeCapability("task_comment", "task", "comment_task", "action", "self", "user", True, label="评论任务", route_path="feishu_task_comment"),
    RuntimeCapability("task_assign_members", "task", "assign_members", "action", "self", "user", True, label="分配任务成员", route_path="feishu_task_assign_members"),
    RuntimeCapability("task_update_followers", "task", "update_followers", "action", "self", "user", True, label="更新任务关注人", route_path="feishu_task_update_followers"),
    RuntimeCapability("task_update_reminders", "task", "update_reminders", "action", "self", "user", True, label="更新任务提醒", route_path="feishu_task_update_reminders"),
    RuntimeCapability("task_upload_attachment", "task", "upload_attachment", "action", "self", "user", True, label="上传任务附件", route_path="feishu_task_upload_attachment"),
    RuntimeCapability("task_add_to_tasklist", "task", "add_to_tasklist", "action", "self", "user", True, label="加入任务清单", route_path="feishu_task_add_to_tasklist"),
    RuntimeCapability("task_set_ancestor", "task", "set_ancestor", "action", "self", "user", True, label="设置父任务", route_path="feishu_task_set_ancestor"),
    RuntimeCapability("task_clear_ancestor", "task", "clear_ancestor", "action", "self", "user", True, label="清除父任务", route_path="feishu_task_clear_ancestor"),
    RuntimeCapability("tasklist_create", "task", "tasklist_create", "action", "self", "user", True, label="创建任务清单", route_path="feishu_tasklist_create"),
    RuntimeCapability("tasklist_update", "task", "tasklist_update", "action", "self", "user", True, label="更新任务清单", route_path="feishu_tasklist_update"),
    RuntimeCapability("tasklist_delete", "task", "tasklist_delete", "action", "self", "user", True, label="删除任务清单", route_path="feishu_tasklist_delete"),
    RuntimeCapability("tasklist_update_members", "task", "tasklist_update_members", "action", "self", "user", True, label="更新任务清单成员", route_path="feishu_tasklist_update_members"),
    RuntimeCapability("tasklist_set_members", "task", "tasklist_set_members", "action", "self", "user", True, label="设置任务清单成员", route_path="feishu_tasklist_set_members"),
    RuntimeCapability("task_section_create", "task", "section_create", "action", "self", "user", True, label="创建任务分组", route_path="feishu_task_section_create"),
    RuntimeCapability("task_section_update", "task", "section_update", "action", "self", "user", True, label="更新任务分组", route_path="feishu_task_section_update"),
    RuntimeCapability("task_section_delete", "task", "section_delete", "action", "self", "user", True, label="删除任务分组", route_path="feishu_task_section_delete"),
    RuntimeCapability("calendar_query", "calendar", "list_events", "query", "self", "bot", label="日程查询", route_path="feishu_calendar_query"),
    RuntimeCapability("calendar_create", "calendar", "create_event", "action", "self", "user", True, label="创建日程", route_path="feishu_calendar_create"),
    RuntimeCapability("mail_query", "mail", "list_recent", "query", "self", "bot", label="最近邮件", route_path="mail_qa"),
    RuntimeCapability("mail_search", "mail", "search_messages", "query", "self", "bot", label="邮件搜索", route_path="mail_qa"),
    RuntimeCapability("mail_get_message", "mail", "get_message", "query", "self", "bot", label="邮件详情", route_path="feishu_mail_message_get"),
    RuntimeCapability("mail_draft_create", "mail", "create_draft", "action", "self", "user", True, label="邮件草稿", route_path="feishu_mail_drafts_create"),
    RuntimeCapability("message_send", "im", "send_message", "action", "self", "user", True, label="发送消息", route_path="feishu_im_send_message"),
    RuntimeCapability("chat_search", "im", "search_chats", "query", "self", "bot", label="群聊搜索", route_path="feishu_im_chat_search"),
    RuntimeCapability("message_query", "im", "list_messages", "query", "self", "bot", label="消息查询", route_path="feishu_im_message_list"),
    RuntimeCapability("chat_create", "im", "create_chat", "action", "self", "user", True, label="创建群聊", route_path="feishu_im_create_chat"),
    RuntimeCapability("chat_auto_join_public", "im", "auto_join_public_chats", "action", "self", "user", True, label="自动加入公开群", route_path="feishu_im_auto_join_public_chats"),
    RuntimeCapability("docs_read", "docs", "read_doc", "query", "company", "bot", label="读取飞书文档", route_path="feishu_doc_read"),
    RuntimeCapability("docs_edit", "docs", "edit_doc", "action", "company", "user", True, installed=False, label="编辑飞书文档", route_path="feishu_doc_edit"),
    RuntimeCapability("wiki_search", "wiki", "search_wiki", "query", "company", "bot", label="查询飞书知识库", route_path="feishu_wiki_search"),
    RuntimeCapability("drive_list", "drive", "list_files", "query", "company", "bot", label="查询飞书云盘文件", route_path="feishu_drive_file_list"),
    RuntimeCapability("drive_upload", "drive", "upload_file", "action", "company", "user", True, installed=False, label="上传飞书云盘文件", route_path="feishu_drive_upload"),
    RuntimeCapability("base_query", "base", "query_records", "query", "company", "bot", installed=False, label="查询多维表格", route_path="feishu_base_query"),
    RuntimeCapability("base_create", "base", "create_table", "action", "company", "user", True, installed=False, label="创建多维表格", route_path="feishu_base_create"),
    RuntimeCapability("sheets_read", "sheets", "read_cells", "query", "company", "bot", installed=False, label="读取飞书电子表格", route_path="feishu_sheets_read"),
    RuntimeCapability("sheets_create", "sheets", "create_sheet", "action", "company", "user", True, installed=False, label="创建飞书电子表格", route_path="feishu_sheets_create"),
    RuntimeCapability("sheets_write", "sheets", "write_cells", "action", "company", "user", True, installed=False, label="写入飞书电子表格", route_path="feishu_sheets_write"),
    RuntimeCapability("vc_meeting_search", "vc", "search_meetings", "query", "company", "bot", label="查询飞书历史会议", route_path="feishu_vc_meeting_search"),
    RuntimeCapability("minutes_read", "minutes", "read_minutes", "query", "company", "bot", installed=False, label="读取飞书妙记", route_path="feishu_minutes_read"),
    RuntimeCapability("note_read", "note", "read_note", "query", "company", "bot", installed=False, label="读取会议纪要", route_path="feishu_note_read"),
    RuntimeCapability("markdown_read", "markdown", "read_markdown", "query", "company", "bot", installed=False, label="读取 Markdown 文件", route_path="feishu_markdown_read"),
    RuntimeCapability("markdown_write", "markdown", "write_markdown", "action", "company", "user", True, installed=False, label="写入 Markdown 文件", route_path="feishu_markdown_write"),
    RuntimeCapability("apps_deploy", "apps", "deploy_app", "action", "company", "user", True, installed=False, label="发布妙搭应用", route_path="feishu_apps_deploy"),
    RuntimeCapability("openapi_explore", "openapi", "explore_api", "query", "company", "bot", installed=False, label="探索飞书原生接口", route_path="feishu_openapi_explore"),
    RuntimeCapability("attendance_query", "attendance", "query_records", "query", "self", "user", label="查询飞书考勤", route_path="feishu_attendance_query"),
    RuntimeCapability("okr_query", "okr", "list_objectives", "query", "self", "bot", label="查询飞书 OKR", route_path="feishu_okr_query"),
    RuntimeCapability("okr_update", "okr", "update_progress", "action", "self", "user", True, installed=False, label="更新飞书 OKR", route_path="feishu_okr_update"),
    RuntimeCapability("slides_read", "slides", "read_slides", "query", "company", "user", label="读取飞书幻灯片", route_path="feishu_slides_read"),
    RuntimeCapability("slides_write", "slides", "write_slides", "action", "company", "user", True, installed=False, label="编辑飞书幻灯片", route_path="feishu_slides_write"),
    RuntimeCapability("whiteboard_read", "whiteboard", "read_whiteboard", "query", "company", "user", label="读取飞书画板", route_path="feishu_whiteboard_read"),
    RuntimeCapability("whiteboard_write", "whiteboard", "write_whiteboard", "action", "company", "user", True, installed=False, label="编辑飞书画板", route_path="feishu_whiteboard_write"),
    RuntimeCapability("vc_agent_read", "vc_agent", "read_live_events", "query", "company", "bot", installed=False, label="读取会中事件", route_path="feishu_vc_agent_read"),
    RuntimeCapability("vc_agent_join", "vc_agent", "join_meeting", "action", "company", "user", True, installed=False, label="机器人加入会议", route_path="feishu_vc_agent_join"),
    RuntimeCapability("company_intro", "company_profile", "read_profile", "query", "company", "bot", label="公司档案", route_path="company_profile"),
    RuntimeCapability("company_intro", "knowledge", "search", "query", "company", "bot", label="企业知识库", route_path="knowledge"),
    RuntimeCapability("company_intro", "workevent", "summarize", "query", "company", "bot", label="工作事件", route_path="workevent"),
    RuntimeCapability("company_intro", "web", "search", "query", "external", "bot", label="网页资料", route_path="web_search"),
    RuntimeCapability("risk_analysis", "workevent", "risk_events", "insight", "company", "bot", label="风险事件", route_path="workevent"),
    RuntimeCapability("risk_analysis", "memory", "related_memory", "insight", "company", "bot", label="长期记忆", route_path="memory"),
    RuntimeCapability("risk_analysis", "knowledge", "risk_policy", "insight", "company", "bot", label="风险知识", route_path="knowledge"),
    RuntimeCapability("general_analysis", "workevent", "summarize", "analysis", "company", "bot", label="现状分析", route_path="workevent"),
    RuntimeCapability("general_analysis", "memory", "related_memory", "analysis", "company", "bot", label="历史参考", route_path="memory"),
    RuntimeCapability("general_analysis", "knowledge", "search", "analysis", "company", "bot", label="知识依据", route_path="knowledge"),
    RuntimeCapability("decision_advice", "workevent", "summarize", "decision", "company", "bot", label="决策背景", route_path="workevent"),
    RuntimeCapability("decision_advice", "memory", "related_memory", "decision", "company", "bot", label="历史参考", route_path="memory"),
    RuntimeCapability("decision_advice", "knowledge", "search", "decision", "company", "bot", label="决策依据", route_path="knowledge"),
    RuntimeCapability("general_query", "knowledge", "search", "query", "company", "bot", label="企业知识库", route_path="knowledge"),
)


SKILL_ATOMIC_CAPABILITIES: tuple[SkillAtomicCapability, ...] = (
    SkillAtomicCapability("approval", "list_pending", "lark-approval", "query", "bot", "low", exposed=True, label="查询待审批"),
    SkillAtomicCapability("approval", "get_detail", "lark-approval", "query", "bot", "low", exposed=True, label="审批详情"),
    SkillAtomicCapability("approval", "approve", "lark-approval", "action", "user", "high", exposed=True, requires_confirmation=True, label="审批通过"),
    SkillAtomicCapability("approval", "reject", "lark-approval", "action", "user", "high", exposed=True, requires_confirmation=True, label="审批拒绝"),
    SkillAtomicCapability("approval", "transfer", "lark-approval", "action", "user", "high", exposed=True, requires_confirmation=True, label="审批转交"),
    SkillAtomicCapability("approval", "add_sign", "lark-approval", "action", "user", "high", exposed=True, requires_confirmation=True, label="审批加签"),
    SkillAtomicCapability("approval", "rollback", "lark-approval", "action", "user", "high", exposed=True, requires_confirmation=True, label="审批退回"),
    SkillAtomicCapability("approval", "remind", "lark-approval", "action", "user", "medium", exposed=True, requires_confirmation=True, label="审批催办"),
    SkillAtomicCapability("approval", "cancel", "lark-approval", "action", "user", "high", exposed=True, requires_confirmation=True, label="审批撤回"),
    SkillAtomicCapability("approval", "cc", "lark-approval", "action", "user", "medium", exposed=True, requires_confirmation=True, label="审批抄送"),
    SkillAtomicCapability("approval", "list_initiated", "lark-approval", "query", "bot", "low", exposed=True, label="查询我发起的审批"),
    SkillAtomicCapability("people", "search_person", "lark-contact", "query", "bot", "low", exposed=True, label="人员查询"),
    SkillAtomicCapability("people", "list_department_members", "lark-contact/openapi", "query", "bot", "low", exposed=True, label="部门成员"),
    SkillAtomicCapability("people", "get_org_snapshot", "lark-contact/openapi", "query", "bot", "low", exposed=True, label="组织架构"),
    SkillAtomicCapability("calendar", "list_events", "lark-calendar", "query", "bot", "low", exposed=True, label="查询日程"),
    SkillAtomicCapability("calendar", "create_event", "lark-calendar", "action", "user", "high", exposed=True, requires_confirmation=True, label="创建日程"),
    SkillAtomicCapability("task", "list_my_tasks", "lark-task", "query", "bot", "low", exposed=True, label="查询任务"),
    SkillAtomicCapability("task", "search_tasks", "lark-task", "query", "bot", "low", exposed=True, label="搜索任务"),
    SkillAtomicCapability("task", "create_task", "lark-task", "action", "user", "medium", exposed=True, requires_confirmation=True, label="创建任务"),
    SkillAtomicCapability("task", "complete_task", "lark-task", "action", "user", "medium", exposed=True, requires_confirmation=True, label="完成任务"),
    SkillAtomicCapability("task", "update_task", "lark-task", "action", "user", "medium", exposed=True, requires_confirmation=True, label="更新任务"),
    SkillAtomicCapability("task", "reopen_task", "lark-task", "action", "user", "medium", exposed=False, requires_confirmation=True, label="重新打开任务", reason="已登记，待作为 Task 样板验证后开放"),
    SkillAtomicCapability("task", "delete_task", "lark-task", "action", "user", "high", exposed=True, requires_confirmation=True, label="删除任务"),
    SkillAtomicCapability("task", "create_subtask", "lark-task", "action", "user", "medium", exposed=False, requires_confirmation=True, label="创建子任务", reason="已登记，待 Task 样板验证多对象输入"),
    SkillAtomicCapability("task", "comment_task", "lark-task", "action", "user", "medium", exposed=False, requires_confirmation=True, label="评论任务", reason="已登记，待 RuntimeActionInput 文本参数复用验证"),
    SkillAtomicCapability("task", "assign_members", "lark-task", "action", "user", "high", exposed=False, requires_confirmation=True, label="分配任务成员", reason="已登记，待 USER 参数解析后开放"),
    SkillAtomicCapability("task", "update_followers", "lark-task", "action", "user", "medium", exposed=False, requires_confirmation=True, label="更新任务关注人", reason="已登记，待 USER 参数解析后开放"),
    SkillAtomicCapability("task", "update_reminders", "lark-task", "action", "user", "medium", exposed=False, requires_confirmation=True, label="更新任务提醒", reason="已登记，待 DATE/TIME 参数解析后开放"),
    SkillAtomicCapability("task", "upload_attachment", "lark-task", "action", "user", "high", exposed=False, requires_confirmation=True, label="上传任务附件", reason="已登记，待文件上传和权限边界验证"),
    SkillAtomicCapability("task", "add_to_tasklist", "lark-task", "action", "user", "medium", exposed=False, requires_confirmation=True, label="加入任务清单", reason="已登记，待 Task 样板验证清单对象输入"),
    SkillAtomicCapability("task", "set_ancestor", "lark-task", "action", "user", "high", exposed=False, requires_confirmation=True, label="设置父任务", reason="已登记，待任务层级对象输入验证"),
    SkillAtomicCapability("task", "clear_ancestor", "lark-task", "action", "user", "high", exposed=False, requires_confirmation=True, label="清除父任务", reason="已登记，待任务层级对象输入验证"),
    SkillAtomicCapability("task", "tasklist_create", "lark-task", "action", "user", "medium", exposed=False, requires_confirmation=True, label="创建任务清单", reason="已登记，待 Task 样板验证清单管理"),
    SkillAtomicCapability("task", "tasklist_update", "lark-task", "action", "user", "medium", exposed=False, requires_confirmation=True, label="更新任务清单", reason="已登记，待 Task 样板验证清单管理"),
    SkillAtomicCapability("task", "tasklist_delete", "lark-task", "action", "user", "high", exposed=False, requires_confirmation=True, label="删除任务清单", reason="已登记，待 Task 样板验证清单管理"),
    SkillAtomicCapability("task", "tasklist_update_members", "lark-task", "action", "user", "high", exposed=False, requires_confirmation=True, label="更新任务清单成员", reason="已登记，待 USER 参数解析后开放"),
    SkillAtomicCapability("task", "tasklist_set_members", "lark-task", "action", "user", "high", exposed=False, requires_confirmation=True, label="设置任务清单成员", reason="已登记，待 USER 参数解析后开放"),
    SkillAtomicCapability("task", "section_create", "lark-task", "action", "user", "medium", exposed=False, requires_confirmation=True, label="创建任务分组", reason="已登记，待 Task 样板验证分组管理"),
    SkillAtomicCapability("task", "section_update", "lark-task", "action", "user", "medium", exposed=False, requires_confirmation=True, label="更新任务分组", reason="已登记，待 Task 样板验证分组管理"),
    SkillAtomicCapability("task", "section_delete", "lark-task", "action", "user", "high", exposed=False, requires_confirmation=True, label="删除任务分组", reason="已登记，待 Task 样板验证分组管理"),
    SkillAtomicCapability("mail", "list_recent", "lark-mail", "query", "bot", "low", exposed=True, label="最近邮件"),
    SkillAtomicCapability("mail", "search_messages", "lark-mail", "query", "bot", "low", exposed=True, label="搜索邮件"),
    SkillAtomicCapability("mail", "get_message", "lark-mail", "query", "bot", "low", exposed=True, label="邮件详情"),
    SkillAtomicCapability("mail", "create_draft", "lark-mail", "action", "user", "medium", exposed=True, requires_confirmation=True, label="创建邮件草稿"),
    SkillAtomicCapability("im", "send_message", "lark-im", "action", "user", "high", exposed=True, requires_confirmation=True, label="发送消息"),
    SkillAtomicCapability("im", "send_result", "lark-im", "action", "user", "high", exposed=True, requires_confirmation=True, label="发送执行结果"),
    SkillAtomicCapability("im", "search_chats", "lark-im", "query", "bot", "low", exposed=True, label="搜索群聊"),
    SkillAtomicCapability("im", "list_messages", "lark-im", "query", "bot", "low", exposed=True, label="查询消息"),
    SkillAtomicCapability("im", "create_chat", "lark-im", "action", "user", "high", exposed=True, requires_confirmation=True, label="创建群聊"),
    SkillAtomicCapability("im", "auto_join_public_chats", "lark-im", "action", "user", "high", exposed=False, requires_confirmation=True, label="自动加入公开群", reason="已登记，保留为治理/管理员能力，暂不开放普通对话执行"),
    SkillAtomicCapability("base", "write_records", "lark-base", "action", "user", "high", exposed=True, requires_confirmation=True, label="写入多维表格"),
    SkillAtomicCapability("base", "query_records", "lark-base", "query", "bot", "low", exposed=False, label="查询多维表格", reason="已登记，待接入查询 Provider"),
    SkillAtomicCapability("base", "create_table", "lark-base", "action", "user", "high", exposed=False, requires_confirmation=True, label="创建多维表格", reason="已登记，待接入 dry-run 和确认闭环"),
    SkillAtomicCapability("docs", "read_doc", "lark-doc", "query", "bot", "low", exposed=True, label="读取文档"),
    SkillAtomicCapability("docs", "edit_doc", "lark-doc", "action", "user", "high", exposed=False, requires_confirmation=True, label="编辑文档", reason="已登记，待接入 dry-run 和确认闭环"),
    SkillAtomicCapability("wiki", "search_wiki", "lark-wiki", "query", "bot", "low", exposed=True, label="查询知识库节点"),
    SkillAtomicCapability("drive", "list_files", "lark-drive", "query", "bot", "low", exposed=True, label="查询云盘文件"),
    SkillAtomicCapability("drive", "upload_file", "lark-drive", "action", "user", "medium", exposed=False, requires_confirmation=True, label="上传文件", reason="已登记，待接入文件权限和回执"),
    SkillAtomicCapability("sheets", "read_cells", "lark-sheets", "query", "bot", "low", exposed=False, label="读取电子表格", reason="已登记，待接入查询 Provider"),
    SkillAtomicCapability("sheets", "create_sheet", "lark-sheets", "action", "user", "high", exposed=False, requires_confirmation=True, label="创建电子表格", reason="已登记，待接入 dry-run 和确认闭环"),
    SkillAtomicCapability("sheets", "write_cells", "lark-sheets", "action", "user", "high", exposed=False, requires_confirmation=True, label="写入电子表格", reason="已登记，待接入 dry-run 和确认闭环"),
    SkillAtomicCapability("vc", "search_meetings", "lark-vc", "query", "bot", "low", exposed=True, label="查询历史会议"),
    SkillAtomicCapability("minutes", "read_minutes", "lark-minutes", "query", "bot", "low", exposed=False, label="读取妙记", reason="已登记，待接入会议知识 Provider"),
    SkillAtomicCapability("note", "read_note", "lark-note", "query", "bot", "low", exposed=False, label="读取会议纪要", reason="已登记，待接入会议纪要 Provider"),
    SkillAtomicCapability("markdown", "read_markdown", "lark-markdown", "query", "bot", "low", exposed=False, label="读取 Markdown 文件", reason="已登记，待接入文件来源和权限判断"),
    SkillAtomicCapability("markdown", "write_markdown", "lark-markdown", "action", "user", "medium", exposed=False, requires_confirmation=True, label="写入 Markdown 文件", reason="已登记，待接入 dry-run 和确认闭环"),
    SkillAtomicCapability("apps", "deploy_app", "lark-apps", "action", "user", "high", exposed=False, requires_confirmation=True, label="发布妙搭应用", reason="开发/发布类动作暂不在飞书机器人直接开放"),
    SkillAtomicCapability("openapi", "explore_api", "lark-openapi-explorer", "query", "bot", "medium", exposed=False, label="探索飞书原生接口", reason="开发者诊断能力，暂不对普通对话开放"),
    SkillAtomicCapability("attendance", "query_records", "lark-attendance", "query", "user", "low", exposed=True, label="查询考勤记录"),
    SkillAtomicCapability("okr", "list_objectives", "lark-okr", "query", "bot", "low", exposed=True, label="查询 OKR"),
    SkillAtomicCapability("okr", "update_progress", "lark-okr", "action", "user", "medium", exposed=False, requires_confirmation=True, label="更新 OKR 进展", reason="已登记，待接入确认闭环"),
    SkillAtomicCapability("slides", "read_slides", "lark-slides", "query", "user", "low", exposed=True, label="读取幻灯片"),
    SkillAtomicCapability("slides", "write_slides", "lark-slides", "action", "user", "high", exposed=False, requires_confirmation=True, label="编辑幻灯片", reason="已登记，待接入 dry-run 和确认闭环"),
    SkillAtomicCapability("whiteboard", "read_whiteboard", "lark-whiteboard", "query", "user", "low", exposed=True, label="读取画板"),
    SkillAtomicCapability("whiteboard", "write_whiteboard", "lark-whiteboard", "action", "user", "high", exposed=False, requires_confirmation=True, label="编辑画板", reason="已登记，待接入 dry-run 和确认闭环"),
    SkillAtomicCapability("vc_agent", "read_live_events", "lark-vc-agent", "query", "bot", "medium", exposed=False, label="读取会中事件", reason="会中能力高敏，待接权限和提示"),
    SkillAtomicCapability("vc_agent", "join_meeting", "lark-vc-agent", "action", "user", "high", exposed=False, requires_confirmation=True, label="机器人加入会议", reason="高风险动作，待接入确认闭环"),
    SkillAtomicCapability("company_profile", "read_profile", "internal-ai", "query", "bot", "low", exposed=True, label="读取公司档案"),
    SkillAtomicCapability("knowledge", "search", "internal-ai", "query", "bot", "low", exposed=True, label="搜索企业知识"),
    SkillAtomicCapability("knowledge", "risk_policy", "internal-ai", "insight", "bot", "medium", exposed=True, label="读取风险政策"),
    SkillAtomicCapability("workevent", "summarize", "internal-ai", "analysis", "bot", "low", exposed=True, label="总结工作事件"),
    SkillAtomicCapability("workevent", "risk_events", "internal-ai", "insight", "bot", "medium", exposed=True, label="识别风险事件"),
    SkillAtomicCapability("memory", "related_memory", "internal-ai", "analysis", "bot", "medium", exposed=True, label="读取相关记忆"),
    SkillAtomicCapability("web", "search", "web-search", "query", "bot", "medium", exposed=True, label="搜索公开网页"),
)


def capability_for(strategy: str, source: str) -> RuntimeCapability | None:
    for capability in RUNTIME_CAPABILITIES:
        if capability.strategy == strategy and capability.source == source:
            return capability
    return None


def primary_capability_for_strategy(strategy: str) -> RuntimeCapability | None:
    for capability in RUNTIME_CAPABILITIES:
        if capability.strategy == strategy:
            return capability
    return None


def capabilities_for_strategy(strategy: str, sources: tuple[str, ...] = ()) -> tuple[RuntimeCapability, ...]:
    capabilities = tuple(
        item
        for item in RUNTIME_CAPABILITIES
        if item.strategy == strategy and (not sources or item.source in sources)
    )
    return capabilities


def route_path_for_strategy(strategy: str) -> str:
    capability = primary_capability_for_strategy(strategy)
    return capability.route_path if capability and capability.route_path else strategy


def route_path_for_result(strategy: str, result_type: str) -> str:
    if result_type in {"runtime_action", "approval_action"}:
        return route_path_for_strategy(strategy)
    if result_type == "base_export":
        return "bitable_qa"
    if result_type == "message_send":
        return "feishu_im_send_message"
    if result_type in {"people_search", "department_members", "organization_snapshot"}:
        return "feishu_contact_organization_snapshot"
    if result_type == "approval_list":
        return "feishu_approval_task_query"
    if result_type == "task_list":
        return "feishu_task_query"
    if result_type == "mail_list":
        return "mail_qa"
    if result_type == "calendar_event_list":
        return "feishu_calendar_query"
    if result_type == "docs_read":
        return "feishu_doc_read"
    if result_type in {"wiki_space_list", "wiki_node_list"}:
        return "feishu_wiki_search"
    if result_type == "drive_file_list":
        return "feishu_drive_file_list"
    if result_type == "vc_meeting_list":
        return "feishu_vc_meeting_search"
    if result_type == "slides_read":
        return "feishu_slides_read"
    if result_type == "whiteboard_read":
        return "feishu_whiteboard_read"
    if result_type == "chat_list":
        return "feishu_im_chat_search"
    if result_type == "im_message_list":
        return "feishu_im_message_list"
    if result_type in {"knowledge_list", "risk_policy_list"}:
        return "knowledge"
    if result_type == "web_search_list":
        return "web_search"
    return route_path_for_strategy(strategy)


def label_for_strategy(strategy: str) -> str:
    capability = primary_capability_for_strategy(strategy)
    return capability.label if capability and capability.label else strategy


def action_requires_confirmation(intent: str) -> bool:
    capability = primary_capability_for_strategy(intent)
    return bool(capability and capability.requires_confirmation)


def strategy_requires_confirmation(strategy: str, sources: tuple[str, ...] = ()) -> bool:
    return any(item.requires_confirmation for item in capabilities_for_strategy(strategy, sources))


def execution_identity_for_intent(intent: str, question_type: str) -> str:
    capability = primary_capability_for_strategy(intent)
    if capability:
        return capability.execution_identity
    return "user" if question_type == "action" else "bot"


def execution_identity_for_strategy(strategy: str, question_type: str, sources: tuple[str, ...] = ()) -> str:
    capabilities = capabilities_for_strategy(strategy, sources)
    if any(item.execution_identity == "user" for item in capabilities):
        return "user"
    if capabilities:
        return "bot"
    return execution_identity_for_intent(strategy, question_type)


def capability_summary() -> dict[str, object]:
    installed = [item for item in RUNTIME_CAPABILITIES if item.installed]
    pending = [item for item in RUNTIME_CAPABILITIES if not item.installed]
    sources = sorted({item.source for item in RUNTIME_CAPABILITIES})
    query_items = [item for item in RUNTIME_CAPABILITIES if item.question_type == "query"]
    action_items = [item for item in RUNTIME_CAPABILITIES if item.question_type == "action"]
    confirmation_items = [item for item in action_items if item.requires_confirmation]
    atomic_items = [item for item in RUNTIME_CAPABILITIES if _is_atomic_capability(item.strategy)]
    path_maturity_items = sorted(
        [_capability_path_maturity(item) for item in RUNTIME_CAPABILITIES],
        key=lambda item: int(item.get("migration_priority") or 0),
        reverse=True,
    )
    migration_items = [item for item in path_maturity_items if int(item.get("migration_priority") or 0) > 0]
    path_maturity_counts = {
        key: len([item for item in path_maturity_items if item["kind"] == key])
        for key in sorted({str(item["kind"]) for item in path_maturity_items})
    }
    migration_priority_counts = {
        "high": len([item for item in migration_items if item.get("migration_priority_label") == "高"]),
        "medium": len([item for item in migration_items if item.get("migration_priority_label") == "中"]),
        "low": len([item for item in migration_items if item.get("migration_priority_label") == "低"]),
    }
    skill_inventory = skill_atomic_inventory_summary()
    return {
        "count": len(RUNTIME_CAPABILITIES),
        "installed_count": len(installed),
        "pending_count": len(pending),
        "query_count": len(query_items),
        "action_count": len(action_items),
        "confirmation_action_count": len(confirmation_items),
        "atomic_count": len(atomic_items),
        "runtime_strategy_count": len({item.strategy for item in RUNTIME_CAPABILITIES}) - len({item.strategy for item in atomic_items}),
        "path_maturity_counts": path_maturity_counts,
        "path_maturity": path_maturity_items,
        "migration_queue_summary": {
            "count": len(migration_items),
            "high_count": migration_priority_counts["high"],
            "medium_count": migration_priority_counts["medium"],
            "low_count": migration_priority_counts["low"],
            "top_item": migration_items[0] if migration_items else {},
        },
        "skill_atomic_inventory": skill_inventory,
        "retired_sources": ["message"],
        "source_aliases": {"message": "im"},
        "strategies": sorted({item.strategy for item in RUNTIME_CAPABILITIES}),
        "sources": sources,
        "by_source": {
            source: {
                "count": len([item for item in RUNTIME_CAPABILITIES if item.source == source]),
                "installed": len([item for item in RUNTIME_CAPABILITIES if item.source == source and item.installed]),
                "pending": len([item for item in RUNTIME_CAPABILITIES if item.source == source and not item.installed]),
                "query": len([item for item in RUNTIME_CAPABILITIES if item.source == source and item.question_type == "query"]),
                "action": len([item for item in RUNTIME_CAPABILITIES if item.source == source and item.question_type == "action"]),
                "confirmation": len([item for item in RUNTIME_CAPABILITIES if item.source == source and item.requires_confirmation]),
                "full_runtime": len([item for item in path_maturity_items if item["source"] == source and item["kind"] == "full_runtime"]),
                "manual_fast_path": len([item for item in path_maturity_items if item["source"] == source and item["kind"] == "manual_fast_path"]),
                "legacy_adapter": len([item for item in path_maturity_items if item["source"] == source and item["kind"] == "legacy_adapter"]),
            }
            for source in sources
        },
        "actions": sorted({item.strategy for item in RUNTIME_CAPABILITIES if item.question_type == "action"}),
        "confirmation_actions": sorted({item.strategy for item in confirmation_items}),
        "pending": [
            {
                "strategy": item.strategy,
                "source": item.source,
                "operation": item.operation,
                "label": item.label,
            }
            for item in pending
        ],
    }


def skill_atomic_inventory_summary() -> dict[str, object]:
    registered = [item for item in SKILL_ATOMIC_CAPABILITIES if item.registered]
    exposed = [item for item in registered if item.exposed]
    pending = [item for item in registered if not item.exposed]
    write_items = [item for item in registered if item.question_type == "action"]
    confirmation_items = [item for item in write_items if item.requires_confirmation]
    high_risk = [item for item in registered if item.risk_level == "high"]
    sources = sorted({item.source for item in registered})
    skills = sorted({item.skill for item in registered})
    rollout_status = "fully_exposed" if not pending else "registered_gated"
    rollout_label = "Skill 原子能力已全部开放" if not pending else "Skill 原子能力已全量登记，部分按风险受控待开放"
    return {
        "rollout_status": rollout_status,
        "rollout_label": rollout_label,
        "registered_count": len(registered),
        "exposed_count": len(exposed),
        "pending_count": len(pending),
        "gated_count": len(pending),
        "exposure_ratio": round(len(exposed) / len(registered), 4) if registered else 1.0,
        "query_count": len([item for item in registered if item.question_type == "query"]),
        "action_count": len(write_items),
        "confirmation_action_count": len(confirmation_items),
        "high_risk_count": len(high_risk),
        "source_count": len(sources),
        "skill_count": len(skills),
        "sources": sources,
        "skills": skills,
        "by_source": {
            source: {
                "registered": len([item for item in registered if item.source == source]),
                "exposed": len([item for item in exposed if item.source == source]),
                "pending": len([item for item in pending if item.source == source]),
                "query": len([item for item in registered if item.source == source and item.question_type == "query"]),
                "action": len([item for item in registered if item.source == source and item.question_type == "action"]),
                "high_risk": len([item for item in registered if item.source == source and item.risk_level == "high"]),
            }
            for source in sources
        },
        "pending": [
            {
                "source": item.source,
                "operation": item.operation,
                "skill": item.skill,
                "label": item.label,
                "risk_level": item.risk_level,
                "reason": item.reason,
            }
            for item in pending
        ],
    }


def path_maturity_for_strategy(strategy: str, sources: tuple[str, ...] = ()) -> dict[str, object]:
    capabilities = capabilities_for_strategy(strategy, sources)
    items = [_capability_path_maturity(item) for item in capabilities]
    counts = {
        key: len([item for item in items if item["kind"] == key])
        for key in sorted({str(item["kind"]) for item in items})
    }
    non_full = sorted(
        [item for item in items if item["kind"] != "full_runtime"],
        key=lambda item: int(item.get("migration_priority") or 0),
        reverse=True,
    )
    return {
        "strategy": strategy,
        "sources": list(sources),
        "available": bool(items),
        "status": "healthy" if not non_full else "needs_attention",
        "label": "当前策略已走完整 V5 主链路" if not non_full else "当前策略仍包含快速路径或旧适配",
        "counts": counts,
        "items": items,
        "migration_items": non_full,
        "migration_count": len(non_full),
    }


def _capability_path_maturity(item: RuntimeCapability) -> dict[str, object]:
    if item.strategy == "approval_query":
        kind = "full_runtime"
        label = "完整 V5 主链路"
        reason = "审批查询已由 V5 Intent/Planner/Permission/Router/Provider/Composer 主链路承接。"
        priority = 0
        priority_label = "无"
        next_step = "继续优化审批 Provider 性能和卡片体验，不再作为快速路径迁移项处理。"
    elif item.route_path in {"mail_qa", "bitable_qa"}:
        kind = "legacy_adapter"
        label = "旧适配入口"
        reason = "该能力仍复用旧 route_path，后续应迁移为 V5 Provider 原生路径。"
        priority = 60 if item.question_type == "action" else 45
        priority_label = "高" if item.question_type == "action" else "中"
        next_step = "把旧 route_path 后面的能力拆成 V5 Resource Provider 原子操作，并补 Capability 契约。"
    else:
        kind = "full_runtime"
        label = "完整 V5 主链路"
        reason = "能力声明可由 V5 Planner/Router/Provider 链路承接。"
        priority = 0
        priority_label = "无"
        next_step = ""
    return {
        "strategy": item.strategy,
        "source": item.source,
        "operation": item.operation,
        "label": item.label,
        "route_path": item.route_path,
        "question_type": item.question_type,
        "requires_confirmation": item.requires_confirmation,
        "execution_identity": item.execution_identity,
        "kind": kind,
        "kind_label": label,
        "reason": reason,
        "migration_priority": priority,
        "migration_priority_label": priority_label,
        "migration_next_step": next_step,
    }


def _is_atomic_capability(strategy: str) -> bool:
    prefixes = (
        "task_update",
        "task_reopen",
        "task_delete",
        "task_subtask_",
        "task_comment",
        "task_assign_",
        "task_upload_",
        "task_add_",
        "task_set_",
        "task_clear_",
        "tasklist_",
        "task_section_",
        "mail_get_message",
        "chat_create",
        "chat_auto_join_",
    )
    return strategy.startswith(prefixes)
