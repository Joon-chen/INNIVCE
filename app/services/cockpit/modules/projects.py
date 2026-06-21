from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.entities import WorkEvent
from app.services.cockpit.schemas import CockpitItem, CockpitModuleResult
from app.services.cockpit.scope import CockpitScope
from app.services.cockpit.utils import scope_filter, short_text, since_days


PROJECT_TERMS = ("project", "项目", "交付", "研发", "测试", "客户", "订单", "现场", "验收", "设备")
PROJECT_RISK_TERMS = ("延期", "逾期", "异常", "风险", "阻塞", "投诉", "验收失败", "交付失败", "客户现场")
RD_PROGRESS_TERMS = ("研发", "测试", "验证", "调试", "实验", "模块", "软件", "硬件", "方案", "样机")
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
    "邮件系统迁移",
    "统一邮件签名",
)


def build_projects_module(db: Session, *, scope: CockpitScope, limit: int) -> CockpitModuleResult:
    events = _project_events(db, scope=scope, limit=max(limit * 4, 50))
    items = _dedupe_project_items([_project_item(event) for event in events if not _is_low_signal_event(event)])
    risks = [item for item in items if item.attention_label == "客户/交付风险"]
    rd_progress = [item for item in items if item.attention_label == "研发测试进展"]
    records = [item for item in items if item.attention_label == "普通项目记录"]
    ordered = [*risks, *rd_progress, *records][:limit]
    return CockpitModuleResult(
        key="projects",
        name="项目动态",
        description="客户交付、研发测试、现场验收和项目节点变化。",
        count=len(risks) + len(rd_progress),
        summary=(
            f"最近 90 天项目动态 {len(items)} 条；"
            f"客户/交付风险 {len(risks)} 条、研发测试进展 {len(rd_progress)} 条、普通项目记录 {len(records)} 条。"
        ),
        items=ordered,
        next_actions=_project_next_actions(risks=len(risks), rd_progress=len(rd_progress), records=len(records)),
        metrics={
            "project_risk": len(risks),
            "rd_progress": len(rd_progress),
            "project_record": len(records),
            "project_events": len(items),
        },
    )


def _project_events(db: Session, *, scope: CockpitScope, limit: int) -> list[WorkEvent]:
    query = (
        select(WorkEvent)
        .where(WorkEvent.occurred_at >= since_days(90))
        .order_by(WorkEvent.occurred_at.desc())
        .limit(limit)
    )
    query = scope_filter(query, WorkEvent, scope)
    conditions = []
    for term in PROJECT_TERMS:
        pattern = f"%{term}%"
        conditions.extend(
            [
                WorkEvent.event_type.ilike(pattern),
                WorkEvent.title.ilike(pattern),
                WorkEvent.content_text.ilike(pattern),
            ]
        )
    return list(db.scalars(query.where(or_(*conditions))).all())


def _dedupe_project_items(items: list[CockpitItem]) -> list[CockpitItem]:
    seen: set[str] = set()
    result: list[CockpitItem] = []
    for item in items:
        day = str(item.occurred_at or "")[:10]
        key = f"{item.attention_label}:{item.title}:{item.event_type or ''}:{day}"
        if key in seen:
            continue
        seen = seen | {key}
        result.append(item)
    return result


def _project_item(event: WorkEvent) -> CockpitItem:
    level = _project_attention_level(event)
    return CockpitItem(
        id=str(event.id),
        title=event.title or event.event_type or "项目动态",
        attention_label=_project_attention_label(level),
        subtitle=_project_subtitle(level),
        description=_project_next_step(level),
        status=_project_status(event),
        priority="high" if level == "project_risk" else None,
        occurred_at=event.occurred_at.isoformat() if event.occurred_at else None,
        source=event.source,
        event_type=event.event_type,
        payload={
            "project_attention_level": level,
            "project_attention_label": _project_attention_label(level),
            "summary": short_text(event.content_text, 180),
            **_project_payload(event),
        },
    )


def _project_attention_level(event: WorkEvent) -> str:
    text = _project_text(event)
    if any(term in text for term in PROJECT_RISK_TERMS):
        return "project_risk"
    if any(term in text for term in RD_PROGRESS_TERMS):
        return "rd_progress"
    return "project_record"


def _project_attention_label(level: str) -> str:
    labels = {
        "project_risk": "客户/交付风险",
        "rd_progress": "研发测试进展",
        "project_record": "普通项目记录",
    }
    return labels[level]


def _project_subtitle(level: str) -> str:
    if level == "project_risk":
        return "可能影响客户承诺、交付节点、验收或回款，需要老板关注。"
    if level == "rd_progress":
        return "研发、测试或验证进展，可用于判断项目节点是否正常。"
    return "普通项目背景记录，用于形成长期项目脉络。"


def _project_next_step(level: str) -> str:
    if level == "project_risk":
        return "确认影响客户、责任人、恢复时间和是否需要升级协调。"
    if level == "rd_progress":
        return "检查是否存在阻塞；如无异常，继续跟踪下一节点。"
    return "默认不占用老板注意力，后续与项目主数据合并分析。"


def _project_next_actions(*, risks: int, rd_progress: int, records: int) -> list[str]:
    if risks:
        actions = [f"优先处理 {risks} 条客户/交付风险，确认影响、责任人和恢复时间。"]
        if rd_progress:
            actions.append(f"{rd_progress} 条研发测试进展用于跟踪节点，异常再升级。")
        return actions
    if rd_progress:
        return [
            f"当前没有客户/交付风险；有 {rd_progress} 条研发测试进展可快速扫一遍。",
            "建议关注是否出现延期、验收异常或客户现场问题。",
        ]
    if records:
        return [f"当前只有 {records} 条普通项目记录，默认不需要老板处理。"]
    return ["最近 90 天没有需要展示的项目动态。"]


def _is_low_signal_event(event: WorkEvent) -> bool:
    text = _project_text(event)
    if any(term in text for term in PROJECT_RISK_TERMS):
        return False
    return any(term in text for term in LOW_SIGNAL_TERMS)


def _project_status(event: WorkEvent) -> str | None:
    payload = _project_payload(event)
    for key in ("status", "state", "project_status", "task_status"):
        value = payload.get(key)
        if value not in (None, "", [], {}):
            if isinstance(value, dict | list):
                continue
            return str(value)
    return None


def _project_payload(event: WorkEvent) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    item = payload.get("item") if isinstance(payload.get("item"), dict) else payload
    return dict(item) if isinstance(item, dict) else {}


def _project_text(event: WorkEvent) -> str:
    return " ".join(
        [
            event.event_type or "",
            event.title or "",
            event.content_text or "",
            str(event.labels or ""),
            str(event.payload or ""),
        ]
    )
