from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import ExtractedItem, WorkEvent
from app.services.tools.evidence import combined_evidence_line, evidence_ref, evidence_line, latest_datetime, wants_evidence_detail
from app.services.agent.policies import BotActor


IDENTITY_COMMANDS = {"机器人身份", "身份确认"}
ONLINE_COMMANDS = {"在线状态"}
GREETING_COMMANDS = {"寒暄"}


def answer_current_chat_summary(
    db: Session,
    *,
    company_id: UUID,
    chat_id: str | None,
    limit: int = 12,
) -> str:
    if not chat_id:
        return "我需要当前会话 ID 才能只总结当前群；现在没有拿到 chat_id。"
    events = _current_chat_events(db, company_id=company_id, chat_id=chat_id, limit=limit)
    if not events:
        return "当前群还没有同步到可总结的消息。请先确认机器人已在群里，并且群消息事件已经入库。"

    by_type: dict[str, int] = {}
    for event in events:
        by_type[event.event_type] = by_type.get(event.event_type, 0) + 1

    lines = [
        f"我按当前群最近 {len(events)} 条已入库消息做了简要总结：",
        "- 消息类型：" + "、".join(f"{key} {value} 条" for key, value in by_type.items()),
        "- 重点内容：",
    ]
    for event in events[:6]:
        lines.append(f"  - {event.title or event.event_type}: {_short(event.content_text)}")
    lines.append(evidence_line("当前群已入库消息", len(events), latest_at=latest_datetime(events)))
    return "\n".join(lines)


def answer_current_chat_tasks(
    db: Session,
    *,
    company_id: UUID,
    chat_id: str | None,
    limit: int = 12,
) -> str:
    if not chat_id:
        return "我需要当前会话 ID 才能只查看当前群待办；现在没有拿到 chat_id。"
    events = _current_chat_events(db, company_id=company_id, chat_id=chat_id, limit=limit * 2)
    if not events:
        return "当前群还没有同步到可抽取待办的消息。"

    event_ids = [event.id for event in events]
    tasks = list(
        db.scalars(
            select(ExtractedItem)
            .where(ExtractedItem.company_id == company_id)
            .where(ExtractedItem.work_event_id.in_(event_ids))
            .where(ExtractedItem.item_type == "task")
            .where(ExtractedItem.status.notin_({"closed", "done", "resolved", "completed"}))
            .order_by(ExtractedItem.created_at.desc())
            .limit(limit)
        ).all()
    )
    if tasks:
        lines = [f"当前群我看到 {len(tasks)} 条开放待办："]
        for task in tasks[:limit]:
            owner = f" / 负责人：{task.owner}" if task.owner else ""
            due = f" / 截止：{task.due_at.isoformat()}" if task.due_at else ""
            lines.append(f"- {task.title}{owner}{due}")
        lines.append(
            combined_evidence_line(
                [("当前群消息", len(events)), ("抽取待办", len(tasks))],
                latest_at=latest_datetime([*events, *tasks]),
            )
        )
        return "\n".join(lines)

    candidates = [
        event
        for event in events
        if _contains_any(event.content_text or event.title or "", ["待办", "跟进", "安排", "完成", "负责", "todo"])
    ]
    if not candidates:
        return "当前群暂时没有抽取到明确待办。"
    lines = ["当前群还没有正式抽取出的待办，但这些消息像是需要跟进："]
    for event in candidates[:limit]:
        lines.append(f"- {event.title or event.event_type}: {_short(event.content_text)}")
    lines.append(evidence_line("当前群候选消息", len(candidates), latest_at=latest_datetime(candidates)))
    return "\n".join(lines)


def answer_current_chat_question(
    db: Session,
    *,
    company_id: UUID,
    chat_id: str | None,
    question: str,
    normalized_command: str,
    actor: BotActor,
    limit: int = 8,
) -> str:
    if normalized_command in IDENTITY_COMMANDS or _is_identity_question(question):
        return _identity_answer(actor)
    if normalized_command in ONLINE_COMMANDS or _is_online_question(question):
        return _online_answer(actor)
    if normalized_command in GREETING_COMMANDS:
        return _greeting_answer(actor)
    if not chat_id:
        return "我可以回答当前会话范围的问题，但现在没有拿到 chat_id，所以不会去查全库或其他群聊。"

    events = _current_chat_events(db, company_id=company_id, chat_id=chat_id, limit=limit)
    if not events:
        return "当前会话还没有同步到可引用的消息。我会只在当前会话范围内回答，不会串到其他同事或其他群。"

    keywords = _question_keywords(question)
    detail = wants_evidence_detail(question)
    relevant = _filter_events(events, keywords) if keywords else events
    lines = [
        f"我只按当前会话最近 {len(relevant)} 条已入库消息回答：",
    ]
    for event in relevant[: limit if detail else 3]:
        lines.append(f"- {event.title or event.event_type}: {_short(event.content_text)}")
    if len(relevant) < len(events):
        lines.append("说明：我过滤掉了当前会话里与问题关键词不匹配的消息。")
    if detail:
        lines.extend(evidence_ref(event, prefix="当前会话消息") for event in relevant[:limit])
    lines.append(evidence_line("当前会话已入库消息", len(relevant), latest_at=latest_datetime(relevant)))
    return "\n".join(lines)


def _current_chat_events(db: Session, *, company_id: UUID, chat_id: str, limit: int) -> list[WorkEvent]:
    return list(
        db.scalars(
            select(WorkEvent)
            .where(WorkEvent.company_id == company_id)
            .where(WorkEvent.source == "feishu")
            .where(WorkEvent.thread_id == chat_id)
            .order_by(WorkEvent.occurred_at.desc())
            .limit(limit)
        ).all()
    )


def _short(text: str | None, max_length: int = 90) -> str:
    value = " ".join(str(text or "").split())
    if not value:
        return ""
    return value if len(value) <= max_length else f"{value[:max_length]}..."


def _contains_any(text: str, terms: list[str]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def _identity_answer(actor: BotActor) -> str:
    if actor.role == "owner":
        return (
            "老板，我是你的企业数字助理，负责把飞书、审批、邮箱、任务、报告和知识库里的已授权信息整理成可查询的工作系统。"
            "你是系统所有者，我会按老板权限服务你。"
        )
    if actor.role in {"admin", "manager", "lead"}:
        domains = f"你的授权业务域：{', '.join(actor.domains)}。" if actor.domains else "我还没有识别到具体授权业务域。"
        return f"我是企业数字助理。{domains}我会按你的部门、角色和业务域权限回答，不会开放老板私有或其他未授权数据。"
    return "我是企业数字助理。我可以回答当前会话、本人相关事项和已授权公开知识，不会查询其他群或公司级敏感数据。"


def _online_answer(actor: BotActor) -> str:
    if actor.role == "owner":
        return "老板，我在线。你收到这条回复，说明飞书机器人到本地服务的回复链路是通的。"
    return "我在线。你可以问当前会话、本人相关事项，或你已授权业务域内的问题。"


def _greeting_answer(actor: BotActor) -> str:
    if actor.role == "owner":
        return "老板，我在。你可以直接问今日重点、审批、风险、项目、邮件或报告。"
    return "我在。你可以问当前会话重点、待办，或你已授权业务域内的信息。"


def _is_identity_question(question: str) -> bool:
    return any(term in question for term in ["你是谁", "你是什么", "你能做什么", "你的身份", "我是谁"])


def _is_online_question(question: str) -> bool:
    return any(term in question for term in ["在线", "离线", "掉线", "在不在", "还在吗"])


def _question_keywords(question: str) -> list[str]:
    stop_words = {"我", "你", "的", "了", "吗", "呢", "一下", "帮我", "看看", "这个", "当前", "群里", "什么"}
    compact = question.replace("，", " ").replace("。", " ").replace("？", " ").replace("?", " ")
    keywords: list[str] = []
    for token in compact.split():
        value = token.strip()
        if len(value) < 2 or value in stop_words:
            continue
        keywords.append(value)
    if keywords:
        return keywords[:6]
    return [term for term in ["项目", "客户", "审批", "风险", "待办", "会议", "文件"] if term in question]


def _filter_events(events: list[WorkEvent], keywords: list[str]) -> list[WorkEvent]:
    matched = [
        event
        for event in events
        if _contains_any(" ".join([event.title or "", event.content_text or "", event.event_type or ""]), keywords)
    ]
    return matched or events
