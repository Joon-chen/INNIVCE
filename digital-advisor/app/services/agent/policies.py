"""Bot interaction routing.

This module is intentionally small. It only resolves actor identity shape,
checks permissions, and selects the answer route/scope. It must not call Feishu,
HTTP clients, databases, cockpit services, knowledge services, or LLM clients.
"""

from dataclasses import dataclass, field

from app.services.agent.capabilities import (
    APPROVAL_TERMS,
    BITABLE_TERMS,
    BOT_CAPABILITIES,
    CALENDAR_TERMS,
    CHAT_SUMMARY_TERMS,
    KNOWLEDGE_TERMS,
    MAIL_TERMS,
    PERSONAL_TERMS,
    PEOPLE_TERMS,
    TASK_TERMS,
    TASK_CREATE_TERMS,
)
from app.services.permissions import question_allowed_by_domains


COCKPIT_COMMAND_PREFIX = "驾驶舱"
BOT_META_COMMANDS = {"机器人身份", "身份确认", "在线状态", "寒暄"}
GENERAL_CHAT_TERMS = [
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
    "你是谁",
    "你能做什么",
]
CALENDAR_WRITE_TERMS = [
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
]
MEETING_RECORD_TERMS = [
    "会议纪要",
    "会议记录",
    "会议总结",
    "会后总结",
    "会议产物",
    "历史会议",
    "妙记",
    "逐字稿",
    "参会人",
]
COMPANY_REPORT_TERMS = [
    "经营",
    "公司",
    "风险",
    "预警",
    "隐患",
    "异常",
    "逾期",
    "项目",
    "进度",
    "交付",
    "里程碑",
    "日报",
    "周报",
    "月报",
    "报告",
]
OWNER_ONLY_TOPICS = [
    "驾驶舱",
    "经营概览",
    "老板驾驶舱",
    "全公司",
    "跨公司",
    "所有公司",
    "老板邮箱",
    "全局日报",
    "跨部门风险",
]


@dataclass(frozen=True)
class BotActor:
    role: str
    access_scope: str
    domains: tuple[str, ...] = field(default_factory=tuple)
    display_name: str | None = None
    open_id: str | None = None
    email: str | None = None

    @property
    def can_query_company(self) -> bool:
        return self.role in {"owner", "admin"} and self.access_scope in {"company", "all"}


@dataclass(frozen=True)
class BotAnswerRoute:
    path: str
    scope: str
    message: str | None = None
    reason: str | None = None

    @property
    def denied(self) -> bool:
        return self.path == "deny"

    @property
    def capability_name(self) -> str:
        capability = BOT_CAPABILITIES.get(self.path)
        return capability.name if capability else self.path


def resolve_bot_route(*, question: str, normalized_command: str, actor: BotActor) -> BotAnswerRoute:
    if is_owner_only_intent(question, normalized_command) and not actor.can_query_company:
        return BotAnswerRoute(path="deny", scope=answer_scope_for_actor(actor, question), reason="owner_only_intent")
    scope = answer_scope_for_actor(actor, question)
    path = capability_path_for_question(question=question, normalized_command=normalized_command, actor=actor, scope=scope)
    return BotAnswerRoute(path=path, scope=scope, reason="permission_scope")


def capability_path_for_question(
    *,
    question: str,
    normalized_command: str,
    actor: BotActor,
    scope: str,
) -> str:
    if normalized_command.startswith(COCKPIT_COMMAND_PREFIX):
        return "owner_cockpit"
    if normalized_command in BOT_META_COMMANDS:
        return "general_chat"
    if _is_general_chat(question, normalized_command):
        return "general_chat"
    if _contains_any(question, MAIL_TERMS):
        return "mail_qa"
    if _contains_any(question, TASK_CREATE_TERMS):
        return "feishu_task_create"
    if _contains_any(question, BITABLE_TERMS):
        return "bitable_qa"
    if _contains_any(question, MEETING_RECORD_TERMS):
        return "feishu_vc_meeting_search"
    if _contains_any(question, APPROVAL_TERMS) and _contains_any(question, ["待", "我的", "本人", "我"]):
        return "feishu_approval_task_query"
    if _contains_any(question, CALENDAR_TERMS) and _contains_any(question, CALENDAR_WRITE_TERMS):
        return "feishu_calendar_create_event"
    if _contains_any(question, CALENDAR_TERMS):
        return "calendar_qa"
    if _contains_any(question, PEOPLE_TERMS):
        return "feishu_contact_organization_snapshot"
    if _contains_any(question, KNOWLEDGE_TERMS):
        return "public_knowledge_qa"
    if _contains_any(question, TASK_TERMS) and _contains_any(question, CHAT_SUMMARY_TERMS):
        return "chat_tasks"
    if _contains_any(question, CHAT_SUMMARY_TERMS):
        return "chat_summary"
    if _contains_any(question, APPROVAL_TERMS):
        return "approval_qa"
    if scope == "company":
        if _contains_any(question, TASK_TERMS):
            return "task_qa"
        if _contains_any(question, COMPANY_REPORT_TERMS):
            return "company_qa"
        return "company_qa"
    if scope == "domain":
        return "domain_qa"
    if _contains_any(question, COMPANY_REPORT_TERMS):
        return "company_qa"
    if actor.access_scope in {"personal", "self", "user"} or _contains_any(question, PERSONAL_TERMS):
        return "personal_tasks"
    return "chat_qa"


def answer_scope_for_actor(actor: BotActor, question: str) -> str:
    if actor.can_query_company:
        return "company"
    if actor.access_scope in {"personal", "self", "user"}:
        return "personal"
    if actor.access_scope in {"domain", "department", "project"} and question_allowed_by_domains(
        question,
        list(actor.domains),
    ):
        return "domain"
    return "chat"


def is_owner_only_intent(question: str, normalized_command: str) -> bool:
    if normalized_command.startswith(COCKPIT_COMMAND_PREFIX):
        return True
    return any(topic in question for topic in OWNER_ONLY_TOPICS)


def owner_only_reply(actor: BotActor) -> str:
    domain_line = f"\n你当前授权业务域：{', '.join(actor.domains)}。" if actor.domains else ""
    return (
        "这个入口是老板驾驶舱能力，只对系统所有者开放。\n"
        f"我识别到你的权限是 {actor.role}/{actor.access_scope}。{domain_line}\n"
        "你可以继续问当前会话、公开知识或你被授权业务域内的信息；我不会向员工开放全公司驾驶舱、老板邮箱、跨公司报告或其他群聊数据。"
    )


def _contains_any(text: str, terms: list[str]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def _is_general_chat(question: str, normalized_command: str) -> bool:
    text = f"{question} {normalized_command}".lower()
    if _contains_any(text, GENERAL_CHAT_TERMS):
        business_terms = (
            APPROVAL_TERMS
            + CHAT_SUMMARY_TERMS
            + TASK_TERMS
            + KNOWLEDGE_TERMS
            + CALENDAR_TERMS
            + MAIL_TERMS
            + BITABLE_TERMS
            + PEOPLE_TERMS
            + MEETING_RECORD_TERMS
            + COMPANY_REPORT_TERMS
        )
        return not _contains_any(text, business_terms)
    return False
