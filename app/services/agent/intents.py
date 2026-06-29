from dataclasses import dataclass


from app.services.agent.query_bitable_lexicon import (
    TABLE_ACTION_TERMS,
    is_ambiguous_bitable_query,
    is_strong_bitable_query,
    TABLE_QUERY_TERMS,
    TABLE_TARGET_TERMS,
)


@dataclass(frozen=True)
class BotIntent:
    name: str
    canonical_command: str | None = None
    route_hint: str | None = None
    module_hint: str | None = None
    canonical_question: str | None = None
    confidence: float = 0.0


def classify_bot_intent(text: str, *, normalized_command: str | None = None) -> BotIntent:
    compact = _compact(f"{text} {normalized_command or ''}")
    if not compact:
        return BotIntent(name="unknown")

    if _is_general_chat(compact):
        return BotIntent(
            name="general_chat",
            canonical_command="寒暄" if _contains(compact, ("你好", "好呀")) else "在线状态",
            route_hint="general_chat",
            canonical_question=text,
            confidence=0.9,
        )
    if _is_identity_question(compact):
        return BotIntent(
            name="identity",
            canonical_command="身份确认" if "我" in compact else "机器人身份",
            route_hint="general_chat",
            canonical_question=text,
            confidence=0.9,
        )
    if normalized_command == "最近审批":
        return BotIntent(
            name="pending_approval",
            canonical_command="最近审批",
            route_hint="feishu_approval_task_query",
            module_hint="approvals",
            canonical_question="待我处理的审批",
            confidence=0.95,
        )
    if _contains(compact, KNOWLEDGE_TERMS):
        return BotIntent(
            name="knowledge",
            route_hint="public_knowledge_qa",
            canonical_question=text,
            confidence=0.9,
        )
    if _contains_query_to_bitable_table(compact, normalized_command=normalized_command):
        return BotIntent(
            name="bitable",
            route_hint="bitable_qa",
            module_hint="resources",
            canonical_question=text,
            confidence=0.95,
        )
    if _is_pending_approval(compact):
        return BotIntent(
            name="pending_approval",
            canonical_command="最近审批",
            route_hint="feishu_approval_task_query",
            module_hint="approvals",
            canonical_question="待我处理的审批",
            confidence=0.95,
        )
    if _is_approval_action_advice(compact):
        return BotIntent(
            name="approval_advice",
            canonical_command="审批建议",
            route_hint="approval_qa",
            module_hint="approvals",
            canonical_question=text,
            confidence=0.9,
        )
    if _contains(compact, APPROVAL_SUBJECT_TERMS):
        return BotIntent(
            name="approval",
            module_hint="approvals",
            canonical_question=text,
            confidence=0.88,
        )
    if _contains(compact, MAIL_TERMS):
        return BotIntent(
            name="mail",
            route_hint="mail_qa",
            canonical_question=text,
            confidence=0.9,
        )
    if _contains(compact, TASK_CREATE_TERMS):
        return BotIntent(
            name="create_task",
            canonical_command="创建任务",
            route_hint="feishu_task_create",
            module_hint="tasks",
            canonical_question="创建一个任务",
            confidence=0.95,
        )
    if _contains(compact, BITABLE_TERMS):
        return BotIntent(
            name="bitable",
            route_hint="bitable_qa",
            canonical_question=text,
            confidence=0.9,
        )
    if _contains(compact, ("是谁", "谁是", "谁担任", "哪个是")) and _contains(compact, PEOPLE_TERMS):
        keyword = compact.replace("公司", "").replace("的", "").replace("是谁", "").replace("谁是", "").strip() or "负责人"
        return BotIntent(
            name="people_search",
            route_hint="feishu_contact_user_search",
            canonical_question=keyword,
            confidence=0.92,
        )
    if _contains(compact, PEOPLE_TERMS):
        return BotIntent(
            name="people",
            route_hint="feishu_contact_organization_snapshot",
            canonical_question=text,
            confidence=0.9,
        )
    if _contains(compact, MEETING_RECORD_TERMS):
        return BotIntent(
            name="meeting_record",
            route_hint="feishu_vc_meeting_search",
            canonical_question=text,
            confidence=0.92,
        )
    if _contains(compact, CALENDAR_TERMS) and _contains(compact, CALENDAR_WRITE_TERMS):
        return BotIntent(
            name="calendar_create",
            route_hint="feishu_calendar_create_event",
            canonical_question=text,
            confidence=0.92,
        )
    if _contains(compact, CALENDAR_TERMS):
        return BotIntent(
            name="calendar",
            route_hint="calendar_qa",
            canonical_question=text,
            confidence=0.9,
        )
    if _contains(compact, TASK_TERMS) and _contains(compact, CHAT_SUMMARY_TERMS):
        return BotIntent(
            name="chat_tasks",
            route_hint="chat_tasks",
            canonical_question=text,
            confidence=0.9,
        )
    if _contains(compact, ("群", "群里", "群聊")) and _contains(
        compact,
        TASK_TERMS + ("清单", "列表", "表", "表格"),
    ):
        return BotIntent(
            name="chat_tasks",
            route_hint="chat_tasks",
            module_hint="tasks",
            canonical_question=text,
            confidence=0.9,
        )
    if _contains(compact, CHAT_SUMMARY_TERMS):
        return BotIntent(
            name="chat_summary",
            route_hint="chat_summary",
            canonical_question=text,
            confidence=0.9,
        )
    if _contains(compact, DATA_COVERAGE_TERMS):
        return BotIntent(
            name="data_coverage",
            route_hint="company_qa",
            module_hint="resources",
            canonical_question="系统数据覆盖和数据盲区",
            confidence=0.95,
        )
    if _contains(compact, RISK_TERMS):
        return BotIntent(
            name="risk",
            route_hint="company_qa",
            module_hint="risks",
            canonical_question=text,
            confidence=0.88,
        )
    if _contains(compact, PROJECT_TERMS):
        return BotIntent(
            name="project",
            route_hint="bitable_qa",
            module_hint="projects",
            canonical_question=text,
            confidence=0.88,
        )
    if _contains(compact, REPORT_TERMS):
        return BotIntent(
            name="report",
            route_hint="company_qa",
            module_hint="reports",
            canonical_question=text,
            confidence=0.88,
        )
    if _contains(compact, TODAY_TERMS):
        return BotIntent(
            name="today_focus",
            route_hint="company_qa",
            module_hint="today-focus",
            canonical_question="今日经营重点",
            confidence=0.78,
        )
    return BotIntent(name="unknown", canonical_question=text)


APPROVAL_SUBJECT_TERMS = (
    "审批",
    "批复",
    "批准",
    "审核",
    "审的单",
    "单子",
    "申请",
    "付款单",
    "报销单",
    "付款申请",
    "报销申请",
    "审批申请",
    "用章",
    "用印",
    "合同",
    "采购",
    "借款",
    "reservefund",
)
PENDING_APPROVAL_TERMS = (
    "未审批",
    "待审批",
    "待审",
    "待批",
    "未审",
    "未处理",
    "待处理",
    "待我",
    "待你",
    "待办",
    "待我同意",
    "待我批复",
    "待我审批",
    "待我审核",
    "待我处理",
    "需要我",
    "要我",
    "我批",
    "我审",
    "我同意",
    "我处理",
    "给我批",
    "给我审",
    "有没有",
    "是否有",
    "哪些",
    "当前",
    "现在",
)
APPROVAL_ADVICE_TERMS = ("建议", "该不该", "是否通过", "能不能通过", "同意还是拒绝", "通过还是拒绝")
GENERAL_CHAT_TERMS = (
    "你好",
    "好呀",
    "在吗",
    "还在吗",
    "不理我",
    "没回复",
    "没有回复",
    "什么情况",
    "卡住",
    "掉线",
    "离线",
)
IDENTITY_TERMS = ("我是谁", "你是谁", "你是什么身份", "我是老板", "我是企业老板", "忘记我是老板")
DATA_COVERAGE_TERMS = ("数据盲区", "盲区", "未接入", "关键群", "机器人入群", "数据覆盖", "资源覆盖")
CALENDAR_TERMS = ("日程", "会议", "开会", "安排", "议程", "今天会", "明天会")
CALENDAR_WRITE_TERMS = (
    "创建日程",
    "新建日程",
    "添加日程",
    "创建我的日程",
    "新建我的日程",
    "添加我的日程",
    "创建一个日程",
    "新建一个日程",
    "添加一个日程",
    "预约会议",
    "发起会议",
    "帮我约",
    "约一个会",
    "安排一个会",
    "安排一场会",
)
MEETING_RECORD_TERMS = (
    "会议纪要",
    "会议记录",
    "会议总结",
    "会后总结",
    "会议产物",
    "历史会议",
    "最近会议",
    "妙记",
    "逐字稿",
    "参会人",
    "开的会",
    "开会了",
    "会上说了",
)
MAIL_TERMS = ("邮件", "邮箱", "来信", "收件箱", "最近一封邮件")
BITABLE_TERMS = ("多维表格", "base", "bitable", "表格", "数据表")
PEOPLE_TERMS = ("通讯录", "组织架构", "组织结构", "员工", "管理层", "负责人", "总经理", "汇报关系")
KNOWLEDGE_TERMS = ("流程", "制度", "怎么做", "知识库", "规范", "模板")
RISK_TERMS = ("风险", "预警", "隐患", "异常", "逾期")
PROJECT_TERMS = ("项目", "进度", "交付", "里程碑", "测试验证")
CHAT_SUMMARY_TERMS = ("这个群", "当前群", "群里", "总结一下这个群", "总结这个群", "刚才聊")
TASK_TERMS = ("待办", "任务", "todo", "跟进", "事项")
TASK_CREATE_TERMS = (
    "创建任务",
    "新建任务",
    "建个任务",
    "建一个任务",
    "建下一个任务",
    "建下个任务",
    "起个任务",
    "起条任务",
    "帮我创建任务",
    "帮我起个任务",
    "帮我新建任务",
    "帮我新建一个任务",
    "新建一条任务",
    "建个待办",
    "起个待办",
    "帮我起个待办",
    "新建一个待办",
    "新建一条待办",
    "创建待办",
    "新建待办",
    "帮我新建一个待办",
    "建一个待办",
    "建条待办",
    "新增待办",
    "新增任务",
    "创建一个任务",
    "新建一个任务",
    "起一个待办",
    "起一条待办",
    "建一条任务",
)
REPORT_TERMS = ("日报", "周报", "月报", "报告", "总结")
TODAY_TERMS = ("今天", "今日", "重点", "盯一下", "关注什么", "现在怎么样", "公司怎么样")
TABLE_QUERY_TERMS = TABLE_QUERY_TERMS
TABLE_ACTION_TERMS = TABLE_ACTION_TERMS
TABLE_TARGET_TERMS = TABLE_TARGET_TERMS


def _compact(text: str) -> str:
    return "".join(text.strip().lower().split())


def _contains_query_to_bitable_table(text: str, *, normalized_command: str | None = None) -> bool:
    compact = f"{text} {normalized_command or ''}"
    if is_ambiguous_bitable_query(compact):
        return False
    return is_strong_bitable_query(compact)


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _is_general_chat(text: str) -> bool:
    if not _contains(text, GENERAL_CHAT_TERMS):
        return False
    return not _contains(text, APPROVAL_SUBJECT_TERMS + RISK_TERMS + PROJECT_TERMS + REPORT_TERMS)


def _is_identity_question(text: str) -> bool:
    return _contains(text, IDENTITY_TERMS)


def _is_pending_approval(text: str) -> bool:
    return _contains(text, APPROVAL_SUBJECT_TERMS) and _contains(text, PENDING_APPROVAL_TERMS)


def _is_approval_action_advice(text: str) -> bool:
    if not _contains(text, APPROVAL_SUBJECT_TERMS + ("这笔", "这个")):
        return False
    return _contains(text, APPROVAL_ADVICE_TERMS) or ("同意" in text and "拒绝" in text)
