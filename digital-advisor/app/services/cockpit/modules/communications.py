from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.entities import WorkEvent
from app.services.cockpit.schemas import CockpitItem, CockpitModuleResult
from app.services.cockpit.scope import CockpitScope
from app.services.cockpit.utils import scope_filter, short_text, since_days


COMMUNICATION_TERMS = ("mail", "email", "邮件", "邮箱", "message", "chat", "im", "消息", "群", "飞书")
OWNER_REPLY_TERMS = ("请您确认", "请你确认", "请老板", "请陈总", "陈总确认", "大飞哥确认", "请批复", "是否同意", "怎么看")
BUSINESS_ATTENTION_TERMS = (
    "异常",
    "风险",
    "催",
    "逾期",
    "延期",
    "客户",
    "付款",
    "回款",
    "合同",
    "报价",
    "订单",
    "投诉",
    "验收",
    "附件",
)
STRONG_BUSINESS_TERMS = ("客户", "付款", "回款", "合同", "报价", "订单", "投诉", "验收", "逾期", "延期", "异常", "风险")
LOW_SIGNAL_TERMS = (
    "欢迎使用",
    "使用指南",
    "帮助中心",
    "同步完成",
    "写入或更新",
    "基础配置完成",
    "数字参谋系统启动",
    "tenant_access_token",
    "机器人主动消息发送成功",
    "IMAP 邮箱",
    "邮箱同步完成",
    "安全登录通知",
    "第三方客户端密码提醒",
    "工资明细",
    "薪资汇总",
    "邮件签名",
)


def build_communications_module(db: Session, *, scope: CockpitScope, limit: int) -> CockpitModuleResult:
    events = _communication_events(db, scope=scope, limit=max(limit * 4, 50))
    items = _dedupe_communication_items(
        [_communication_item(event) for event in events if not _is_low_signal_event(event)]
    )
    owner_replies = [item for item in items if item.attention_label == "需要你回应"]
    business_attention = [item for item in items if item.attention_label == "需要关注"]
    records = [item for item in items if item.attention_label == "背景记录"]
    ordered = [*owner_replies, *business_attention, *records][:limit]
    return CockpitModuleResult(
        key="communications",
        name="消息邮件",
        description="飞书消息、群聊、邮件和其他沟通线索。",
        count=len(owner_replies) + len(business_attention),
        summary=(
            f"最近 90 天沟通线索 {len(items)} 条；"
            f"需要你回应 {len(owner_replies)} 条、需要关注 {len(business_attention)} 条、背景记录 {len(records)} 条。"
        ),
        items=ordered,
        next_actions=_communication_next_actions(
            owner_replies=len(owner_replies),
            business_attention=len(business_attention),
            records=len(records),
        ),
        metrics={
            "owner_reply": len(owner_replies),
            "business_attention": len(business_attention),
            "communication_record": len(records),
            "communication_events": len(items),
        },
    )


def _communication_events(db: Session, *, scope: CockpitScope, limit: int) -> list[WorkEvent]:
    query = (
        select(WorkEvent)
        .where(WorkEvent.occurred_at >= since_days(90))
        .order_by(WorkEvent.occurred_at.desc())
        .limit(limit)
    )
    query = scope_filter(query, WorkEvent, scope)
    conditions = []
    for term in COMMUNICATION_TERMS:
        pattern = f"%{term}%"
        conditions.extend(
            [
                WorkEvent.event_type.ilike(pattern),
                WorkEvent.title.ilike(pattern),
                WorkEvent.content_text.ilike(pattern),
            ]
        )
    return list(db.scalars(query.where(or_(*conditions))).all())


def _dedupe_communication_items(items: list[CockpitItem]) -> list[CockpitItem]:
    seen: set[str] = set()
    result: list[CockpitItem] = []
    for item in items:
        day = str(item.occurred_at or "")[:10]
        key = f"{item.attention_label}:{item.title}:{item.source or ''}:{day}"
        if key in seen:
            continue
        seen = seen | {key}
        result.append(item)
    return result


def _communication_item(event: WorkEvent) -> CockpitItem:
    level = _communication_attention_level(event)
    return CockpitItem(
        id=str(event.id),
        title=event.title or event.event_type or "沟通记录",
        attention_label=_communication_attention_label(level),
        subtitle=_communication_subtitle(level),
        description=_communication_next_step(level),
        status=_communication_status(event),
        priority="high" if level == "owner_reply" else None,
        occurred_at=event.occurred_at.isoformat() if event.occurred_at else None,
        source=event.source,
        event_type=event.event_type,
        payload={
            "communication_attention_level": level,
            "communication_attention_label": _communication_attention_label(level),
            "summary": short_text(event.content_text, 180),
            **_communication_payload(event),
        },
    )


def _communication_attention_level(event: WorkEvent) -> str:
    text = _communication_text(event)
    if any(term in text for term in OWNER_REPLY_TERMS):
        return "owner_reply"
    if any(term in text for term in BUSINESS_ATTENTION_TERMS):
        return "business_attention"
    return "communication_record"


def _communication_attention_label(level: str) -> str:
    labels = {
        "owner_reply": "需要你回应",
        "business_attention": "需要关注",
        "communication_record": "背景记录",
    }
    return labels[level]


def _communication_subtitle(level: str) -> str:
    if level == "owner_reply":
        return "沟通内容可能在等老板明确回复、确认或拍板。"
    if level == "business_attention":
        return "涉及客户、资金、合同、订单、延期或异常，需要快速扫一眼。"
    return "普通沟通背景，用于后续日报、周报和上下文追踪。"


def _communication_next_step(level: str) -> str:
    if level == "owner_reply":
        return "确认是否需要你本人回复；如需要，先看原始会话或邮件上下文。"
    if level == "business_attention":
        return "判断是否要转成任务、风险或决策，并指定负责人继续推进。"
    return "默认不处理，系统保留为上下文。"


def _communication_next_actions(*, owner_replies: int, business_attention: int, records: int) -> list[str]:
    if owner_replies:
        actions = [f"先处理 {owner_replies} 条可能需要你本人回应的沟通。"]
        if business_attention:
            actions.append(f"再扫 {business_attention} 条业务关注线索，必要时转成任务或风险。")
        return actions
    if business_attention:
        return [
            f"当前没有明显需要你本人回应的沟通；有 {business_attention} 条业务关注线索。",
            "建议只看客户、资金、合同、订单和异常相关内容。",
        ]
    if records:
        return [f"当前只有 {records} 条背景沟通记录，默认不需要老板处理。"]
    return ["最近 90 天没有需要展示的沟通线索。"]


def _is_low_signal_event(event: WorkEvent) -> bool:
    text = _communication_text(event)
    if not any(term in text for term in LOW_SIGNAL_TERMS):
        return False
    return not any(term in text for term in OWNER_REPLY_TERMS + STRONG_BUSINESS_TERMS)


def _communication_status(event: WorkEvent) -> str | None:
    payload = _communication_payload(event)
    for key in ("status", "state", "message_status", "mail_status"):
        value = payload.get(key)
        if value not in (None, "", [], {}):
            if isinstance(value, dict | list):
                continue
            return str(value)
    return None


def _communication_payload(event: WorkEvent) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    item = payload.get("item") if isinstance(payload.get("item"), dict) else payload
    return dict(item) if isinstance(item, dict) else {}


def _communication_text(event: WorkEvent) -> str:
    return " ".join(
        [
            event.event_type or "",
            event.title or "",
            event.content_text or "",
            str(event.labels or ""),
            str(event.payload or ""),
        ]
    )
