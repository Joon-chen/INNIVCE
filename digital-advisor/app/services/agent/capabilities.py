from dataclasses import dataclass


@dataclass(frozen=True)
class BotCapability:
    path: str
    name: str
    description: str
    owner_only: bool = False


BOT_CAPABILITIES: dict[str, BotCapability] = {
    "deny": BotCapability("deny", "拒绝回答", "权限不足或越权意图。"),
    "owner_cockpit": BotCapability("owner_cockpit", "老板驾驶舱", "老板专用经营驾驶舱。", owner_only=True),
    "company_qa": BotCapability("company_qa", "公司级问答", "老板或管理员的公司级数据问答。"),
    "chat_summary": BotCapability("chat_summary", "当前群总结", "当前群或当前会话的上下文总结。"),
    "chat_tasks": BotCapability("chat_tasks", "当前群待办", "当前群或当前会话中的待办和跟进事项。"),
    "personal_tasks": BotCapability("personal_tasks", "本人相关事项", "与当前员工本人相关的任务或事项。"),
    "domain_qa": BotCapability("domain_qa", "授权业务域问答", "财务、销售、研发等授权业务域问答。"),
    "approval_qa": BotCapability("approval_qa", "审批问答", "授权范围内的审批问答和审批建议。"),
    "public_knowledge_qa": BotCapability("public_knowledge_qa", "公开知识问答", "公开知识和流程制度问答。"),
    "chat_qa": BotCapability("chat_qa", "当前会话问答", "当前群或当前会话范围内的一般问答。"),
    "calendar_qa": BotCapability("calendar_qa", "日程问答", "授权范围内的日程、会议和安排问答。"),
    "mail_qa": BotCapability("mail_qa", "邮件问答", "老板或授权所有者的邮件摘要和邮件事项问答。", owner_only=True),
    "bitable_qa": BotCapability("bitable_qa", "多维表格问答", "已同步多维表格数据的摘要和查询。"),
    "task_qa": BotCapability("task_qa", "任务问答", "公司级或授权范围内的任务和待办汇总。"),
    "feishu_task_create": BotCapability("feishu_task_create", "创建任务", "在当前任务体系中发起新任务。"),
    "feishu_contact_organization_snapshot": BotCapability(
        "feishu_contact_organization_snapshot",
        "组织架构问答",
        "通过 PeopleTool 查询通讯录、组织架构和管理人员。",
    ),
}


APPROVAL_TERMS = ["审批", "批复", "批准", "审核", "付款", "报销", "请假", "合同", "采购", "用章", "用印", "借款", "单子", "同意", "拒绝", "驳回"]
CHAT_SUMMARY_TERMS = ["这个群", "当前群", "刚才", "聊了什么", "总结一下", "群里说"]
TASK_TERMS = ["待办", "任务", "todo", "跟进", "我负责", "我的事项", "安排"]
TASK_CREATE_TERMS = [
    "创建任务",
    "新建任务",
    "建任务",
    "起条任务",
    "起个任务",
    "建个任务",
    "建一个任务",
    "建下一个任务",
    "建下个任务",
    "新建一条任务",
    "帮我建任务",
    "帮我创建任务",
    "帮我起个任务",
    "帮我新建任务",
    "帮我新建一个任务",
    "建个待办",
    "起个待办",
    "起一条待办",
    "帮我起个待办",
    "建一个待办",
    "建条待办",
    "新建一个待办",
    "新建一条待办",
    "帮我新建一个待办",
    "创建待办",
    "新建待办",
    "新增待办",
    "新增任务",
    "起一个待办",
    "起一个任务",
    "建一条任务",
]
PERSONAL_TERMS = ["我的", "我负责", "跟我有关", "分配给我", "本人"]
KNOWLEDGE_TERMS = ["流程", "制度", "怎么做", "知识库", "规范", "模板"]
CALENDAR_TERMS = ["日程", "会议", "开会", "安排", "时间", "今天会", "明天会", "议程"]
MAIL_TERMS = ["邮件", "邮箱", "来信", "收件箱", "发件人", "最近一封邮件"]
BITABLE_TERMS = ["多维表格", "base", "bitable", "表格", "数据表"]
PEOPLE_TERMS = ["通讯录", "组织架构", "组织结构", "员工", "管理层", "负责人", "总经理", "汇报关系"]
