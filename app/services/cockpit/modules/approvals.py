from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.entities import WorkEvent
from app.services.cockpit.schemas import CockpitItem, CockpitModuleResult
from app.services.cockpit.scope import CockpitScope
from app.services.cockpit.utils import scope_filter, since_days


PENDING_STATUSES = {"pending", "todo", "doing", "in_progress", "running", "待处理", "待审批"}
COMPLETED_STATUSES = {
    "approved",
    "rejected",
    "cancelled",
    "canceled",
    "completed",
    "done",
    "success",
    "通过",
    "已通过",
    "拒绝",
    "已拒绝",
    "已完成",
    "撤回",
}


def build_approvals_module(db: Session, *, scope: CockpitScope, limit: int) -> CockpitModuleResult:
    events = _approval_events(db, scope=scope, limit=max(limit * 3, 30))
    items = _dedupe_approval_items([_approval_item(event) for event in events])
    pending_for_me = [item for item in items if item.attention_label == "待我审批"]
    completed = [item for item in items if item.attention_label == "已完成"]
    sync_only = [item for item in items if item.attention_label == "仅同步记录"]
    ordered = [*pending_for_me, *sync_only, *completed][:limit]
    return CockpitModuleResult(
        key="approvals",
        name="审批动态",
        description="付款、报销、合同、请假、采购等审批相关动态。",
        count=len(ordered),
        summary=(
            f"最近 90 天审批动态 {len(items)} 条；"
            f"待我审批 {len(pending_for_me)} 条、已完成 {len(completed)} 条、仅同步记录 {len(sync_only)} 条。"
        ),
        items=ordered,
        next_actions=_approval_next_actions(
            pending_for_me=len(pending_for_me),
            sync_only=len(sync_only),
            completed=len(completed),
        ),
        metrics={
            "pending_for_me": len(pending_for_me),
            "completed": len(completed),
            "sync_only": len(sync_only),
            "approval_events": len(items),
        },
    )


def _approval_events(db: Session, *, scope: CockpitScope, limit: int) -> list[WorkEvent]:
    query = (
        select(WorkEvent)
        .where(WorkEvent.occurred_at >= since_days(90))
        .order_by(WorkEvent.occurred_at.desc())
        .limit(limit)
    )
    query = scope_filter(query, WorkEvent, scope)
    query = query.where(
        or_(
            WorkEvent.event_type.ilike("%approval%"),
            WorkEvent.event_type.ilike("%approvals%"),
            WorkEvent.title.ilike("%审批%"),
            WorkEvent.title.ilike("%付款申请%"),
            WorkEvent.title.ilike("%报销申请%"),
            WorkEvent.title.ilike("%请假申请%"),
            WorkEvent.title.ilike("%合同申请%"),
        )
    )
    return list(db.scalars(query).all())


def _dedupe_approval_items(items: list[CockpitItem]) -> list[CockpitItem]:
    seen: set[str] = set()
    result: list[CockpitItem] = []
    for item in items:
        key = _approval_dedupe_key(item)
        if key in seen:
            continue
        seen = seen | {key}
        result.append(item)
    return result


def _approval_dedupe_key(item: CockpitItem) -> str:
    payload = item.payload or {}
    for key in ("task_id", "instance_code"):
        value = payload.get(key)
        if value:
            return f"{key}:{value}"
    approval_code = payload.get("approval_code") or ""
    return f"title:{approval_code}:{item.title}:{item.status}"


def _approval_item(event: WorkEvent) -> CockpitItem:
    raw = _payload_item(event)
    status = _approval_status(raw)
    bucket = _approval_bucket(raw, status)
    amount = _approval_amount(raw, event)
    return CockpitItem(
        id=str(event.id),
        title=event.title or "审批记录",
        attention_label=bucket,
        subtitle=_approval_subtitle(raw, amount),
        description=_approval_description(bucket, raw, status),
        status=status or "unknown",
        priority="high" if bucket == "待我审批" or (amount or 0) >= 30000 else None,
        occurred_at=event.occurred_at.isoformat() if event.occurred_at else None,
        source=event.source,
        event_type=event.event_type,
        payload={
            "approval_bucket": bucket,
            "status": status,
            "amount": amount,
            "approval_code": raw.get("approval_code") or raw.get("definition_code"),
            "instance_code": raw.get("instance_code") or raw.get("process_code"),
            "task_id": raw.get("task_id"),
        },
    )


def _approval_bucket(item: dict[str, Any], status: str | None) -> str:
    normalized = str(status or "").lower()
    if item.get("task_id") and normalized in PENDING_STATUSES:
        return "待我审批"
    if normalized in COMPLETED_STATUSES:
        return "已完成"
    return "仅同步记录"


def _approval_next_actions(*, pending_for_me: int, sync_only: int, completed: int) -> list[str]:
    if pending_for_me:
        return [
            f"优先处理 {pending_for_me} 条明确待你审批的单据。",
            "仅同步记录不等于待你本人审批；需要精确处理时以飞书审批任务为准。",
        ]
    actions = ["当前没有明确待你本人审批的单据。"]
    if sync_only:
        actions.append(f"有 {sync_only} 条审批实例仅作为同步记录，用于日报、风险和趋势分析。")
    if completed:
        actions.append(f"最近已完成 {completed} 条审批，可用于追踪流程闭环。")
    return actions


def _payload_item(event: WorkEvent) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    item = payload.get("item") if isinstance(payload.get("item"), dict) else payload
    if isinstance(item, dict):
        merged = dict(item)
        context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
        for key, value in context.items():
            merged.setdefault(key, value)
        return merged
    return {}


def _approval_status(item: dict[str, Any]) -> str | None:
    for key in ("status", "instance_status", "approval_status", "task_status"):
        value = item.get(key)
        if value not in (None, "", [], {}):
            if isinstance(value, dict | list):
                continue
            return str(value).strip()
    return None


def _approval_subtitle(item: dict[str, Any], amount: float | None) -> str:
    approval_name = item.get("approval_name") or item.get("definition_name") or item.get("name") or "审批"
    instance_code = item.get("instance_code") or item.get("process_code")
    parts = [str(approval_name)]
    if instance_code:
        parts.append(f"单号 {instance_code}")
    if amount:
        parts.append(f"金额 {amount:g}")
    return " / ".join(parts)


def _approval_description(bucket: str, item: dict[str, Any], status: str | None) -> str:
    if bucket == "待我审批":
        return "这是明确的审批任务记录，建议打开审批明细核对金额、事由、项目归属和凭证后处理。"
    if bucket == "已完成":
        return "这是已完成审批，用于追踪流程闭环，不应再当作待处理事项。"
    if str(status or "").lower() in PENDING_STATUSES:
        return "这是未完成审批实例，但本地记录未确认是待你本人审批；仅作为同步记录参考。"
    return "这是审批同步记录，用于日报、风险和流程趋势分析。"


def _approval_amount(item: dict[str, Any], event: WorkEvent) -> float | None:
    candidates = [
        item.get("amount"),
        item.get("申请金额"),
        item.get("付款金额"),
        item.get("报销金额"),
        item.get("合同金额"),
        event.content_text,
    ]
    for candidate in candidates:
        amount = _parse_amount(candidate)
        if amount is not None:
            return amount
    return None


def _parse_amount(value: Any) -> float | None:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).replace(",", "")
    if not any(marker in text for marker in ("金额", "￥", "¥", "元")):
        return None
    digits = ""
    for char in text:
        if char.isdigit() or char == ".":
            digits += char
        elif digits:
            break
    if not digits:
        return None
    try:
        return float(digits)
    except ValueError:
        return None
