import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import ExtractedItem, MemoryFact, Report, WorkEvent
from app.services.ai.openai_service import AIService
from app.services.ai.vector_search import WorkEventVectorIndex
from app.services.permissions import keywords_for_domains, question_allowed_by_domains


@dataclass
class AdvisorContext:
    scope: str
    keywords: list[str]
    events: list[WorkEvent]
    items: list[ExtractedItem]
    reports: list[Report]
    memory_facts: list[MemoryFact]
    actor_role: str = "member"
    actor_access_scope: str = "chat"
    actor_domains: list[str] | None = None


def answer_advisor_question(
    db: Session,
    *,
    company_id,
    question: str,
    scope: str = "company",
    chat_id: str | None = None,
    actor_role: str = "member",
    actor_access_scope: str = "chat",
    actor_domains: list[str] | None = None,
) -> str:
    actor_domains = actor_domains or []
    if scope == "chat" and _requires_company_access(question):
        return _limited_scope_reply(actor_role=actor_role, actor_access_scope=actor_access_scope)
    if scope == "domain" and not question_allowed_by_domains(question, actor_domains):
        return _limited_scope_reply(actor_role=actor_role, actor_access_scope=actor_access_scope)
    context = build_advisor_context(
        db,
        company_id=company_id,
        question=question,
        scope=scope,
        chat_id=chat_id,
        actor_role=actor_role,
        actor_access_scope=actor_access_scope,
        actor_domains=actor_domains,
    )
    if settings.openai_use_for_bot and AIService().client:
        return _model_answer(question=question, context=context)
    return _local_answer(question=question, context=context)


def build_advisor_context(
    db: Session,
    *,
    company_id,
    question: str,
    scope: str,
    chat_id: str | None,
    actor_role: str = "member",
    actor_access_scope: str = "chat",
    actor_domains: list[str] | None = None,
) -> AdvisorContext:
    actor_domains = actor_domains or []
    keywords = _extract_keywords(question)
    if scope == "domain":
        keywords = _unique([*keywords, *keywords_for_domains(actor_domains)])[:18]
    vector_events = _semantic_search_events(
        db,
        company_id=company_id,
        question=question,
        scope=scope,
        chat_id=chat_id,
        actor_domains=actor_domains,
        limit=min(settings.feishu_bot_context_events, 30),
    )
    events = _search_events(
        db,
        company_id=company_id,
        question=question,
        keywords=keywords,
        scope=scope,
        chat_id=chat_id,
        actor_domains=actor_domains,
        limit=settings.feishu_bot_context_events,
    )
    events = _merge_events(vector_events, events, limit=settings.feishu_bot_context_events)
    items = [] if scope == "chat" else _search_items(db, company_id=company_id, keywords=keywords, limit=20)
    reports = [] if scope == "chat" else _search_reports(db, company_id=company_id, keywords=keywords, limit=3)
    memory_facts = _search_memory_facts(
        db,
        company_id=company_id,
        keywords=keywords,
        scope=scope,
        chat_id=chat_id,
        actor_domains=actor_domains,
        limit=12,
    )
    return AdvisorContext(
        scope=scope,
        keywords=keywords,
        events=events,
        items=items,
        reports=reports,
        memory_facts=memory_facts,
        actor_role=actor_role,
        actor_access_scope=actor_access_scope,
        actor_domains=actor_domains,
    )


def _semantic_search_events(
    db: Session,
    *,
    company_id,
    question: str,
    scope: str,
    chat_id: str | None,
    actor_domains: list[str],
    limit: int,
) -> list[WorkEvent]:
    if not question.strip():
        return []
    try:
        hits = WorkEventVectorIndex().search(
            query=question,
            company_id=company_id,
            limit=limit,
            chat_id=chat_id if scope == "chat" else None,
            sources={"feishu"} if scope == "chat" else _source_filter(question),
        )
    except Exception:
        return []
    event_ids = [hit["id"] for hit in hits if hit.get("id")]
    if not event_ids:
        return []

    query = select(WorkEvent).where(WorkEvent.company_id == company_id).where(WorkEvent.id.in_(event_ids))
    if scope == "chat":
        if not chat_id:
            return []
        query = query.where(WorkEvent.source == "feishu").where(WorkEvent.thread_id == chat_id)
    elif scope == "domain":
        query = query.where(_domain_condition(actor_domains))
    elif _is_contact_question(question):
        query = query.where(WorkEvent.event_type.ilike("%contacts%"))
    events_by_id = {str(event.id): event for event in db.scalars(query).all()}
    return [events_by_id[event_id] for event_id in event_ids if event_id in events_by_id]


def _search_events(
    db: Session,
    *,
    company_id,
    question: str,
    keywords: list[str],
    scope: str,
    chat_id: str | None,
    actor_domains: list[str],
    limit: int,
) -> list[WorkEvent]:
    base = select(WorkEvent).where(WorkEvent.company_id == company_id)
    if scope == "chat":
        if not chat_id:
            return []
        base = base.where(WorkEvent.source == "feishu").where(WorkEvent.thread_id == chat_id)
    elif scope == "domain":
        base = base.where(_domain_condition(actor_domains))
    elif _is_contact_question(question):
        base = base.where(WorkEvent.event_type.ilike("%contacts%"))

    source_filter = _source_filter(question)
    if source_filter and scope != "chat":
        base = base.where(WorkEvent.source.in_(source_filter))

    start = _time_start(question)
    if start:
        base = base.where(WorkEvent.occurred_at >= start)

    matched: list[WorkEvent] = []
    if keywords:
        conditions = []
        for keyword in keywords[:8]:
            pattern = f"%{keyword}%"
            conditions.extend(
                [
                    WorkEvent.title.ilike(pattern),
                    WorkEvent.content_text.ilike(pattern),
                    WorkEvent.event_type.ilike(pattern),
                ]
            )
        matched = list(
            db.scalars(base.where(or_(*conditions)).order_by(WorkEvent.occurred_at.desc()).limit(limit)).all()
        )

    recent_limit = max(8, min(limit, 30))
    recent = list(db.scalars(base.order_by(WorkEvent.occurred_at.desc()).limit(recent_limit)).all())
    return _merge_events(matched, recent, limit=limit)


def _search_items(db: Session, *, company_id, keywords: list[str], limit: int) -> list[ExtractedItem]:
    base = select(ExtractedItem).where(ExtractedItem.company_id == company_id).where(ExtractedItem.status == "open")
    if keywords:
        conditions = []
        for keyword in keywords[:8]:
            pattern = f"%{keyword}%"
            conditions.extend([ExtractedItem.title.ilike(pattern), ExtractedItem.description.ilike(pattern)])
        query = base.where(or_(*conditions)).order_by(ExtractedItem.created_at.desc()).limit(limit)
    else:
        query = base.order_by(ExtractedItem.created_at.desc()).limit(limit)
    return list(db.scalars(query).all())


def _search_reports(db: Session, *, company_id, keywords: list[str], limit: int) -> list[Report]:
    base = select(Report).where(Report.company_id == company_id)
    if keywords:
        conditions = []
        for keyword in keywords[:6]:
            pattern = f"%{keyword}%"
            conditions.extend([Report.title.ilike(pattern), Report.content_markdown.ilike(pattern)])
        query = base.where(or_(*conditions)).order_by(Report.period_end.desc()).limit(limit)
    else:
        query = base.order_by(Report.period_end.desc()).limit(limit)
    return list(db.scalars(query).all())


def _search_memory_facts(
    db: Session,
    *,
    company_id,
    keywords: list[str],
    scope: str,
    chat_id: str | None,
    actor_domains: list[str],
    limit: int,
) -> list[MemoryFact]:
    if scope == "chat":
        if not chat_id:
            return []
        base = select(MemoryFact).where(MemoryFact.company_id == company_id).where(MemoryFact.scope == "chat").where(
            MemoryFact.chat_id == chat_id
        )
    elif scope == "domain":
        domain_keywords = keywords_for_domains(actor_domains)
        if not domain_keywords:
            return []
        base = select(MemoryFact).where(MemoryFact.company_id == company_id).where(MemoryFact.scope == "domain")
    else:
        base = select(MemoryFact).where(MemoryFact.company_id == company_id).where(MemoryFact.scope == "company")
    if keywords:
        conditions = []
        for keyword in keywords[:8]:
            pattern = f"%{keyword}%"
            conditions.extend(
                [
                    MemoryFact.subject.ilike(pattern),
                    MemoryFact.content.ilike(pattern),
                    MemoryFact.fact_type.ilike(pattern),
                ]
            )
        query = base.where(or_(*conditions)).order_by(MemoryFact.updated_at.desc()).limit(limit)
    else:
        query = base.order_by(MemoryFact.updated_at.desc()).limit(limit)
    return list(db.scalars(query).all())


def _domain_condition(actor_domains: list[str]):
    keywords = keywords_for_domains(actor_domains)
    if not keywords:
        return WorkEvent.id.is_(None)
    conditions = []
    for keyword in keywords[:24]:
        pattern = f"%{keyword}%"
        conditions.extend(
            [
                WorkEvent.title.ilike(pattern),
                WorkEvent.content_text.ilike(pattern),
                WorkEvent.event_type.ilike(pattern),
            ]
        )
    return or_(*conditions)


def _model_answer(*, question: str, context: AdvisorContext) -> str:
    service = AIService()
    if not service.client:
        return _local_answer(question=question, context=context)

    model_event_limit = min(settings.feishu_bot_model_context_events, settings.feishu_bot_context_events)
    item_limit = 12 if context.scope == "company" else 6
    report_limit = 2 if context.scope == "company" else 0
    memory_limit = 8 if context.scope == "company" else 0
    event_context = "\n".join(_format_event(event) for event in context.events[:model_event_limit])
    item_context = "\n".join(_format_item(item) for item in context.items[:item_limit])
    report_context = "\n".join(_format_report(report) for report in context.reports[:report_limit])
    memory_context = "\n".join(_format_memory_fact(fact) for fact in context.memory_facts[:memory_limit])
    scope_instruction = (
        "当前用户不是管理员。你只能基于当前飞书会话上下文回答，"
        "不得提及或推断邮件、薪资、财务、其他群聊、数据库全局数据；"
        "可以回答当前会话及未来接入的公开/授权知识库内容。"
        if context.scope == "chat"
        else (
            f"当前用户不是全公司管理员，但被授权业务域：{', '.join(context.actor_domains or [])}。"
            "你只能基于这些业务域相关证据回答，不得扩展到未授权部门、老板邮箱、薪资财务明细或其他群聊。"
            if context.scope == "domain"
            else "当前用户是老板/管理员，可以基于给定的全公司工作事件、邮件、日报、待办风险和企业知识库上下文回答。"
        )
    )
    attribution_instruction = (
        "非常重要：只有当工作事件来自当前会话时，才可以说“你刚才/你之前提过”。"
        "其他会话、其他群、其他同事的消息只能称为“公司其他会话里有人提到”或“系统资料里出现过”，"
        "绝不能归因给当前提问者。"
    )
    prompt = f"""
你是企业老板的数字助理，也是公司第二大脑入口，不是客服机器人，也不是接口说明员。
说话方式要像一个熟悉公司上下文的中文助理：自然、直接、有判断、有分寸。

回答原则：
- 第一句话先回答用户真正想知道的事，不要先说“当前资料不足”。
- 不要固定使用“结论/依据/来源/建议下一步”四段式，除非问题确实需要正式汇报。
- 有证据时，用 2 到 5 条要点说清楚；没必要展示数据库 id。
- 证据不足时，说“我这边只看到……”并说明缺哪类数据；不要让用户重复解释需求。
- 可以给管理建议，但必须和证据绑定，不要编造事实。
- 不要说“我无法查看数据库”，因为系统已经把可用数据库证据提供给你。
- 回答控制在 800 字以内，优先像飞书聊天里的自然回复。
{scope_instruction}
{attribution_instruction}
当前提问者身份：role={context.actor_role}, access_scope={context.actor_access_scope}
企业第二信息来源：后续会接入“功率半导体测试设备公司”知识库，包括产品、客户、项目、交付、售后、研发、质量、供应链等结构化资料；当前如未提供相关知识库证据，不要假装已经读取。

用户问题：
{question[:1000]}

检索关键词：
{"、".join(context.keywords) or "无"}

开放事项：
{item_context[:4000] or "暂无"}

相关日报：
{report_context[:2500] or "暂无"}

长期记忆：
{memory_context[:2000] or "暂无"}

检索到的工作事件：
{event_context[:6000] or "暂无"}
"""
    try:
        if _should_use_deepseek_for_bot_analysis(question, context, service=service):
            answer = service.complete_deepseek_text(prompt, temperature=0.35)
        else:
            answer = service.complete_text(prompt, temperature=0.35)
        return (answer or "").strip()[:3500] or _local_answer(question=question, context=context)
    except Exception:
        return _local_answer(question=question, context=context)


def _should_use_deepseek_for_bot_analysis(question: str, context: AdvisorContext, *, service: AIService) -> bool:
    if context.scope != "company" or not settings.deepseek_use_for_bot_analysis or not service.deepseek_client:
        return False
    analysis_terms = [
        "日报",
        "周报",
        "总报",
        "分析",
        "建议",
        "风险",
        "决策",
        "经营",
        "管理",
        "审批建议",
        "优先级",
        "复盘",
    ]
    return any(term in question for term in analysis_terms)


def _local_answer(*, question: str, context: AdvisorContext) -> str:
    question_text = question.lower()
    if context.scope == "chat":
        if any(word in question_text for word in ["邮件", "邮箱", "财务", "薪资", "工资", "奖金", "全公司", "数据库"]):
            return _limited_scope_reply(actor_role=context.actor_role, actor_access_scope=context.actor_access_scope)
        return _source_answer(context.events, {"feishu"}, "当前会话相关信息")
    if any(word in question_text for word in ["风险", "问题", "阻塞", "延期"]):
        return _risk_answer(context.events, context.items)
    if any(word in question_text for word in ["待办", "任务", "todo", "安排"]):
        return _task_answer(context.items, context.events)
    if any(word in question_text for word in ["邮件", "邮箱"]):
        return _mail_answer(context.events)
    if any(word in question_text for word in ["飞书", "消息", "聊天", "对话"]):
        return _source_answer(context.events, {"feishu"}, "相关飞书消息")
    return _summary_answer(context)


def _risk_answer(events: list[WorkEvent], items: list[ExtractedItem]) -> str:
    risk_items = [item for item in items if item.item_type == "risk"]
    lines = ["我看到几个需要盯的风险点："]
    for item in risk_items[:8]:
        lines.append(f"- {item.title}")
    if len(lines) == 1:
        risk_events = [
            event for event in events if _contains_any(event, ["风险", "问题", "阻塞", "延期", "不确定"])
        ]
        for event in risk_events[:8]:
            lines.append(f"- {event.title or event.event_type}: {_short(event.content_text)}")
    if len(lines) == 1:
        return "我这边暂时没看到明显风险信号。更稳的做法是继续把重点群、飞书邮箱和审批实例同步进来，再看是否有延期、付款、客户或交付风险。"
    return "\n".join(lines)


def _task_answer(items: list[ExtractedItem], events: list[WorkEvent]) -> str:
    task_items = [item for item in items if item.item_type == "task"]
    lines = ["我先把能看到的待办列出来："]
    for item in task_items[:8]:
        lines.append(f"- {item.title}")
    if len(lines) == 1:
        task_events = [event for event in events if _contains_any(event, ["待办", "跟进", "安排", "完成"])]
        for event in task_events[:8]:
            lines.append(f"- {event.title or event.event_type}: {_short(event.content_text)}")
    if len(lines) == 1:
        return "我这边还没抽取到明确待办。可以继续同步重点群历史消息，或者你直接对我说“帮我记一个待办：……”。"
    return "\n".join(lines)


def _mail_answer(events: list[WorkEvent]) -> str:
    mail_events = [
        event
        for event in events
        if event.source in {"imap", "gmail", "graph"} or "mail" in event.event_type.lower() or "mail" in event.labels
    ]
    return _source_answer(mail_events, None, "相关邮件")


def _source_answer(events: list[WorkEvent], sources: set[str] | None, title: str) -> str:
    filtered = [event for event in events if sources is None or event.source in sources]
    if not filtered:
        return f"我这边没有命中可用的{title}。如果你刚刚配置了权限，可以先跑一次同步，再问我。"
    lines = [f"我查到这些{title}："]
    for event in filtered[:8]:
        occurred = event.occurred_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M")
        summary = _short(event.content_text)
        line = f"- {occurred} {event.title or event.event_type}"
        if summary:
            line += f"：{summary}"
        lines.append(line)
    return "\n".join(lines)


def _summary_answer(context: AdvisorContext) -> str:
    if not context.events:
        return "当前没有检索到足够的工作事件。建议先同步飞书历史消息和邮箱。"

    by_source: dict[str, int] = {}
    for event in context.events:
        by_source[event.source] = by_source.get(event.source, 0) + 1

    lines = [
        "我先按现有资料给你一个简要判断：",
        f"- 我命中了 {len(context.events)} 条工作事件，来源："
        + "、".join(f"{k} {v} 条" for k, v in by_source.items()),
    ]
    if context.items:
        lines.append(f"- 命中开放事项 {len(context.items)} 条。")
    if context.reports:
        lines.append(f"- 命中日报/报告 {len(context.reports)} 条。")
    if context.memory_facts:
        lines.append(f"- 命中长期记忆 {len(context.memory_facts)} 条。")
    lines.append("- 相关重点我先抓这几条：")
    for event in context.events[:6]:
        lines.append(f"  - {event.title or event.event_type}: {_short(event.content_text)}")
    return "\n".join(lines)


def _source_filter(question: str) -> set[str] | None:
    if any(word in question for word in ["邮件", "邮箱", "薪资", "工资", "财务资金", "奖金"]):
        return {"feishu", "imap", "gmail", "graph"}
    if any(word in question for word in ["飞书", "群", "聊天", "对话", "消息"]):
        return {"feishu"}
    return None


def _is_contact_question(question: str) -> bool:
    return any(word in question for word in ["通讯录", "组织架构", "组织结构", "部门", "人员", "员工", "汇报关系", "xmind", "Xmind", "XMind"])


def _requires_company_access(question: str) -> bool:
    sensitive_terms = [
        "邮件",
        "邮箱",
        "薪资",
        "工资",
        "奖金",
        "财务",
        "回款",
        "付款",
        "全公司",
        "数据库",
        "日报",
        "周报",
        "其他群",
        "老板",
    ]
    return any(term in question for term in sensitive_terms)


def _limited_scope_reply(*, actor_role: str, actor_access_scope: str) -> str:
    return (
        f"这部分我不能直接展开。当前识别到你的权限是 {actor_role}/{actor_access_scope}，"
        "我只能回答当前会话或已授权知识库范围内的信息；老板邮箱、全公司数据、薪资、财务和其他群聊信息不会开放给你。"
    )


def _time_start(question: str) -> datetime | None:
    now = datetime.now(UTC)
    if "今天" in question or "今日" in question:
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if "昨天" in question:
        return now - timedelta(days=2)
    match = re.search(r"最近\s*(\d+)\s*天", question)
    if match:
        return now - timedelta(days=min(int(match.group(1)), 90))
    if "最近" in question:
        return now - timedelta(days=14)
    return None


def _extract_keywords(question: str) -> list[str]:
    known_terms = [
        "固势",
        "戴总",
        "销售订单",
        "订单",
        "客户",
        "财务",
        "资金",
        "薪资",
        "工资",
        "奖金",
        "邮件",
        "飞书",
        "风险",
        "待办",
        "任务",
        "审批",
        "日历",
        "会议",
        "业务",
        "异常",
        "延期",
        "跟进",
        "录用",
    ]
    keywords = [term for term in known_terms if term in question]
    ascii_words = re.findall(r"[A-Za-z0-9_@.-]{2,}", question)
    keywords.extend(ascii_words)
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", question):
        cleaned = _strip_stopwords(chunk)
        if 2 <= len(cleaned) <= 12:
            keywords.append(cleaned)
    return _unique(keywords)[:10]


def _strip_stopwords(value: str) -> str:
    stopwords = [
        "最近",
        "今天",
        "今日",
        "昨天",
        "一下",
        "什么",
        "有哪些",
        "有没有",
        "帮我",
        "查看",
        "总结",
        "关于",
        "里面",
    ]
    result = value
    for stopword in stopwords:
        result = result.replace(stopword, "")
    return result.strip()


def _merge_events(primary: list[WorkEvent], secondary: list[WorkEvent], *, limit: int) -> list[WorkEvent]:
    seen = set()
    merged: list[WorkEvent] = []
    for event in [*primary, *secondary]:
        if event.id in seen:
            continue
        seen.add(event.id)
        merged.append(event)
        if len(merged) >= limit:
            break
    return merged


def _format_event(event: WorkEvent) -> str:
    occurred = event.occurred_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M")
    return (
        f"- id={event.id} {occurred} [{event.source}/{event.event_type}] "
        f"{event.title or ''}: {_short(event.content_text, 180)}"
    )


def _format_item(item: ExtractedItem) -> str:
    return f"- [{item.item_type}] {item.title}: {_short(item.description, 180)}"


def _format_report(report: Report) -> str:
    return f"- [{report.report_type}] {report.title}: {_short(report.content_markdown, 260)}"


def _format_memory_fact(fact: MemoryFact) -> str:
    source = f" source_event={fact.source_work_event_id}" if fact.source_work_event_id else ""
    return f"- [{fact.fact_type}/{fact.confidence}] {fact.subject}: {_short(fact.content, 240)}{source}"


def _contains_any(event: WorkEvent, words: list[str]) -> bool:
    text = f"{event.title or ''} {event.content_text or ''}".lower()
    return any(word.lower() in text for word in words)


def _short(value: Any, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _unique(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
