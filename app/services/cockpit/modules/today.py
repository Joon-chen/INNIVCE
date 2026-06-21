from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.entities import ExtractedItem, WorkEvent
from app.services.cockpit.schemas import CockpitItem, CockpitModuleResult
from app.services.cockpit.scope import CockpitScope
from app.services.cockpit.utils import CLOSED_STATUSES, display_extracted_title, scope_filter, since_today


FOCUS_ITEM_TYPES = ("risk", "task", "decision")
HIGH_PRIORITIES = {"high", "urgent", "p0", "p1"}
KEY_EVENT_TERMS = ("审批", "付款", "报销", "合同", "采购", "风险", "延期", "客户", "项目", "工资", "回款")
ATTENTION_LABELS = {
    "must_handle": "必须处理",
    "monitor": "可关注",
    "system_managed": "系统自动处理",
}


def build_today_module(db: Session, *, scope: CockpitScope, limit: int) -> CockpitModuleResult:
    today_start = since_today()
    event_query = (
        select(WorkEvent)
        .where(WorkEvent.occurred_at >= today_start)
        .order_by(WorkEvent.occurred_at.desc())
        .limit(limit)
    )
    event_query = scope_filter(event_query, WorkEvent, scope)
    events = list(db.scalars(event_query).all())

    open_focus_items = _open_focus_items(db, scope, limit=max(limit * 3, 12))
    risk_count = _open_item_count(db, scope, "risk")
    task_count = _open_item_count(db, scope, "task")
    decision_count = _open_item_count(db, scope, "decision")
    items = _build_today_focus_items(events, open_focus_items, limit=limit)
    summary = _today_summary(
        today_events=len(events),
        risk_count=risk_count,
        task_count=task_count,
        decision_count=decision_count,
        focus_count=len(items),
        must_handle_count=_attention_count(items, "must_handle"),
        monitor_count=_attention_count(items, "monitor"),
        system_managed_count=_attention_count(items, "system_managed"),
    )
    return CockpitModuleResult(
        key="today-focus",
        name="今日重点",
        description="面向老板的当日经营重点、风险、待办和待决策摘要。",
        count=len(events),
        summary=summary,
        items=items,
        next_actions=_today_next_actions(
            focus_count=len(items),
            risk_count=risk_count,
            task_count=task_count,
            decision_count=decision_count,
            today_events=len(events),
            must_handle_count=_attention_count(items, "must_handle"),
            monitor_count=_attention_count(items, "monitor"),
            system_managed_count=_attention_count(items, "system_managed"),
        ),
        metrics={
            "today_events": len(events),
            "open_risks": risk_count,
            "open_tasks": task_count,
            "open_decisions": decision_count,
            "focus_items": len(items),
            "must_handle": _attention_count(items, "must_handle"),
            "monitor": _attention_count(items, "monitor"),
            "system_managed": _attention_count(items, "system_managed"),
        },
    )


def _open_item_count(db: Session, scope: CockpitScope, item_type: str) -> int:
    query = (
        select(func.count())
        .select_from(ExtractedItem)
        .where(ExtractedItem.item_type == item_type)
        .where(ExtractedItem.status.notin_({"closed", "done", "resolved", "completed"}))
    )
    query = scope_filter(query, ExtractedItem, scope)
    if item_type != "risk":
        return int(db.scalar(query) or 0)

    risk_query = (
        select(ExtractedItem)
        .where(ExtractedItem.item_type == "risk")
        .where(ExtractedItem.status.notin_({"closed", "done", "resolved", "completed"}))
    )
    risk_query = scope_filter(risk_query, ExtractedItem, scope)
    return sum(1 for item in db.scalars(risk_query).all() if not _is_low_signal_extracted_item(item))


def _today_summary(
    *,
    today_events: int,
    risk_count: int,
    task_count: int,
    decision_count: int,
    focus_count: int,
    must_handle_count: int = 0,
    monitor_count: int = 0,
    system_managed_count: int = 0,
) -> str:
    base = (
        f"今天新增 {today_events} 条工作事件；当前开放风险 {risk_count} 条、"
        f"待办 {task_count} 条、待决策 {decision_count} 条；已提炼今日重点 {focus_count} 项。"
    )
    if focus_count:
        return (
            f"{base} 其中必须处理 {must_handle_count} 项、可关注 {monitor_count} 项、"
            f"系统自动处理 {system_managed_count} 项。"
        )
    if risk_count == 0:
        return f"{base} 当前没有必须老板立即处理的高优先级事项。"
    return f"{base} 当前没有进入今日重点的高优先级事项，可进入风险中心查看开放风险。"


def _today_next_actions(
    *,
    focus_count: int,
    risk_count: int,
    task_count: int,
    decision_count: int,
    today_events: int,
    must_handle_count: int = 0,
    monitor_count: int = 0,
    system_managed_count: int = 0,
) -> list[str]:
    if focus_count:
        if must_handle_count:
            return [
                f"先处理 {must_handle_count} 项必须处理事项，再查看可关注事项。",
                "处理完成后回到对应工作台关闭事项，便于日报和周报追踪。",
            ]
        if monitor_count:
            return [
                f"今天暂无必须老板立即处理的事项；有 {monitor_count} 项可关注事项，建议快速扫一遍。",
                "系统自动处理事项不需要你操作，只用于记录当天动态。",
            ]
        return [
            f"今天没有需要你处理或关注的重点；系统已自动记录 {system_managed_count} 项动态。",
        ]
    actions = ["今天暂无必须老板立即处理的事项。"]
    if decision_count:
        actions.append(f"仍有 {decision_count} 条开放决策事项，可进入决策事项工作台按业务域查看。")
    if task_count:
        actions.append(f"仍有 {task_count} 条开放待办，可进入待办事项工作台确认负责人和截止时间。")
    if risk_count:
        actions.append(f"仍有 {risk_count} 条开放风险，可进入风险中心核实是否需要升级。")
    if today_events == 0:
        actions.append("今天尚未看到新增工作事件，请检查数据覆盖模块里的自动同步状态。")
    return actions


def _open_focus_items(db: Session, scope: CockpitScope, *, limit: int) -> list[ExtractedItem]:
    today_start = since_today()
    query = (
        select(ExtractedItem)
        .outerjoin(WorkEvent, ExtractedItem.work_event_id == WorkEvent.id)
        .where(ExtractedItem.item_type.in_(FOCUS_ITEM_TYPES))
        .where(ExtractedItem.status.notin_(CLOSED_STATUSES))
        .where(
            or_(
                ExtractedItem.due_at >= today_start,
                WorkEvent.occurred_at >= today_start,
                ExtractedItem.priority.in_(HIGH_PRIORITIES),
                and_(ExtractedItem.work_event_id.is_(None), ExtractedItem.created_at >= today_start),
            )
        )
        .order_by(ExtractedItem.created_at.desc())
        .limit(limit)
    )
    query = scope_filter(query, ExtractedItem, scope)
    return [item for item in db.scalars(query).all() if not _is_low_signal_extracted_item(item)]


def _build_today_focus_items(
    events: list[WorkEvent],
    focus_items: list[ExtractedItem],
    *,
    limit: int,
) -> list[CockpitItem]:
    candidates: list[tuple[float, CockpitItem]] = []
    for item in focus_items:
        if _is_low_signal_extracted_item(item):
            continue
        candidates.append((_score_extracted_item(item), _focus_item_from_extracted(item)))
    for event in events:
        score = _score_work_event(event)
        if score >= 2:
            candidates.append((score, _focus_item_from_event(event)))
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return _dedupe_focus_items([item for _, item in candidates], limit=limit)


def _attention_count(items: list[CockpitItem], level: str) -> int:
    return sum(1 for item in items if (item.payload or {}).get("attention_level") == level)


def _dedupe_focus_items(items: list[CockpitItem], *, limit: int) -> list[CockpitItem]:
    seen: set[tuple[str, str]] = set()
    result: list[CockpitItem] = []
    for item in items:
        key = (str((item.payload or {}).get("focus_type") or item.event_type or ""), item.title.strip())
        if key in seen:
            continue
        seen = seen | {key}
        result.append(item)
        if len(result) >= limit:
            break
    return result


def _focus_item_from_extracted(item: ExtractedItem) -> CockpitItem:
    attention_level = _attention_level_for_extracted(item)
    return CockpitItem(
        id=str(item.id),
        title=display_extracted_title(item),
        attention_label=ATTENTION_LABELS[attention_level],
        subtitle=_why_extracted_item_matters(item),
        description=_suggested_action_for_extracted_item(item),
        status=item.status,
        priority=item.priority,
        owner=item.owner,
        occurred_at=item.due_at.isoformat() if item.due_at else item.created_at.isoformat() if item.created_at else None,
        payload={
            "focus_type": item.item_type,
            "attention_level": attention_level,
            "attention_label": ATTENTION_LABELS[attention_level],
            "why": _why_extracted_item_matters(item),
            "suggested_action": _suggested_action_for_extracted_item(item),
            **(item.payload or {}),
        },
    )


def _focus_item_from_event(event: WorkEvent) -> CockpitItem:
    attention_level = _attention_level_for_event(event)
    return CockpitItem(
        id=str(event.id),
        title=event.title or event.event_type,
        attention_label=ATTENTION_LABELS[attention_level],
        subtitle=_why_event_matters(event),
        description=_suggested_action_for_event(event),
        status=_event_status(event),
        priority=_event_priority(event),
        occurred_at=event.occurred_at.isoformat() if event.occurred_at else None,
        source=event.source,
        event_type=event.event_type,
        payload={
            "focus_type": "work_event",
            "attention_level": attention_level,
            "attention_label": ATTENTION_LABELS[attention_level],
            "why": _why_event_matters(event),
            "suggested_action": _suggested_action_for_event(event),
            "amount": _payload_amount(event.payload),
            "labels": event.labels or [],
        },
    )


def _attention_level_for_extracted(item: ExtractedItem) -> str:
    if str(item.priority or "").lower() in HIGH_PRIORITIES:
        return "must_handle"
    if _payload_amount(item.payload) >= 30000:
        return "must_handle"
    if item.item_type in {"risk", "decision", "task"}:
        return "monitor"
    return "system_managed"


def _attention_level_for_event(event: WorkEvent) -> str:
    if _payload_amount(event.payload) >= 30000:
        return "must_handle"
    status = str(_event_status(event) or "").lower()
    if status in {"approved", "completed", "done", "success", "通过", "已完成", "已通过"}:
        return "system_managed"
    text = f"{event.event_type} {event.title or ''} {event.content_text or ''}"
    if "审批" in text or "approval" in event.event_type:
        return "must_handle" if status in {"pending", "待处理", "待审批"} else "monitor"
    return "monitor"


def _is_low_signal_extracted_item(item: ExtractedItem) -> bool:
    text = " ".join([item.title or "", item.description or "", str(item.payload or "")])
    notice_text = " ".join([item.title or "", item.description or ""])
    return _is_finance_document_notice(text) or _is_low_signal_notice(notice_text)


def _is_finance_document_notice(text: str) -> bool:
    normalized = text
    for neutral_phrase in ("有问题随时沟通", "如有问题请", "有问题请", "如有疑问", "若有疑问"):
        normalized = normalized.replace(neutral_phrase, "")
    finance_doc_terms = ("工资明细", "薪资明细", "工资汇总", "薪资汇总", "工资总表", "薪资总表")
    neutral_terms = ("附件", "汇总表", "请核对", "请查收", "明细", "总表")
    risk_terms = ("异常", "差异", "错误", "逾期", "未发", "未付", "拖欠", "投诉", "风险", "问题")
    return (
        any(term in text for term in finance_doc_terms)
        and any(term in text for term in neutral_terms)
        and not any(term in normalized for term in risk_terms)
    )


def _is_low_signal_notice(text: str) -> bool:
    headline = text.strip()[:240]
    notice_terms = ("通知", "提醒", "规范", "模板", "指引", "录用通知书", "请查阅", "欢迎使用", "使用指南", "帮助中心")
    strong_terms = ("异常", "差异", "错误", "逾期", "未付", "拖欠", "投诉", "风险", "阻塞", "延期", "违约", "事故")
    business_terms = ("付款", "报销", "合同", "采购", "回款", "客户", "订单", "项目", "审批")
    if not any(term in headline for term in notice_terms):
        return False
    if any(term in headline for term in strong_terms):
        return False
    return not (any(term in headline for term in business_terms) and "录用通知书" not in headline)


def _score_extracted_item(item: ExtractedItem) -> float:
    score = 3.0
    if item.item_type == "risk":
        score += 4
    elif item.item_type == "decision":
        score += 3
    elif item.item_type == "task":
        score += 2
    if str(item.priority or "").lower() in HIGH_PRIORITIES:
        score += 3
    if item.due_at:
        score += 2
    score += min(_payload_amount(item.payload) / 10000, 5)
    return score


def _score_work_event(event: WorkEvent) -> float:
    text = " ".join([event.event_type or "", event.title or "", event.content_text or "", " ".join(event.labels or [])])
    score = float(event.importance_score or 0) * 5
    if any(term in text for term in KEY_EVENT_TERMS):
        score += 3
    if _payload_amount(event.payload) >= 30000:
        score += 4
    if "approval" in (event.event_type or "") or "审批" in text:
        score += 2
    return score


def _why_extracted_item_matters(item: ExtractedItem) -> str:
    payload = item.payload if isinstance(item.payload, dict) else {}
    if payload.get("business_object") == "compensation":
        return "涉及薪酬/奖金发放规则或现金流安排，需要老板确认口径。"
    if payload.get("business_object") == "payment":
        return "涉及付款或报销审批，需要确认金额、依据和项目归属。"
    if item.item_type == "risk":
        return "这是开放风险，可能影响经营结果或执行进度。"
    if item.item_type == "decision":
        return "这是待决策事项，需要老板明确方向或取舍。"
    if item.due_at:
        return "这是有截止时间的待办，需要确认负责人和时限。"
    return "这是开放待办，需要确认是否推进。"


def _suggested_action_for_extracted_item(item: ExtractedItem) -> str:
    payload = item.payload if isinstance(item.payload, dict) else {}
    suggested_action = payload.get("suggested_action")
    if suggested_action:
        return str(suggested_action)
    if item.item_type == "risk":
        return "确认负责人、处理时限和是否需要升级处理。"
    if item.item_type == "decision":
        return "确认决策选项、依据和下一步动作。"
    return "确认是否今天处理，必要时指定负责人。"


def _why_event_matters(event: WorkEvent) -> str:
    amount = _payload_amount(event.payload)
    if amount >= 30000:
        return f"涉及较高金额 {amount:g}，需要优先确认。"
    text = f"{event.event_type} {event.title or ''} {event.content_text or ''}"
    if "审批" in text or "approval" in event.event_type:
        return "这是审批相关事件，可能需要你判断或跟进。"
    if any(term in text for term in ("风险", "延期", "异常")):
        return "内容包含风险或异常信号，需要关注。"
    return "这是今天新增的关键工作事件。"


def _suggested_action_for_event(event: WorkEvent) -> str:
    text = f"{event.event_type} {event.title or ''} {event.content_text or ''}"
    if "审批" in text or "approval" in event.event_type:
        return "查看审批明细，确认同意、拒绝或补充资料。"
    if any(term in text for term in ("风险", "延期", "异常")):
        return "要求相关负责人给出原因、影响和解决时间。"
    return "需要时点开来源查看上下文。"


def _event_priority(event: WorkEvent) -> str | None:
    if _payload_amount(event.payload) >= 30000 or (event.importance_score or 0) >= 0.8:
        return "high"
    return None


def _event_status(event: WorkEvent) -> str | None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    for key in ("status", "approval_status", "task_status"):
        value = payload.get(key)
        if value:
            return str(value)
    return None


def _payload_amount(payload: dict | None) -> float:
    if not isinstance(payload, dict):
        return 0.0
    values = [
        payload.get("amount"),
        payload.get("total_amount"),
        payload.get("money"),
        (payload.get("item") or {}).get("amount") if isinstance(payload.get("item"), dict) else None,
    ]
    for value in values:
        try:
            if value is not None and str(value).strip():
                return float(str(value).replace(",", ""))
        except ValueError:
            continue
    return 0.0
