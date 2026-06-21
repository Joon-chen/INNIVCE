from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import ExtractedItem
from app.services.ai.openai_service import is_finance_document_notice, is_low_signal_operational_notice


def close_finance_document_noise_risks(db: Session, *, company_id: UUID | None = None, limit: int = 500) -> dict:
    query = (
        select(ExtractedItem)
        .where(ExtractedItem.item_type == "risk")
        .where(ExtractedItem.status.notin_({"closed", "done", "resolved", "completed", "noise"}))
        .order_by(ExtractedItem.created_at.desc())
        .limit(limit)
    )
    if company_id:
        query = query.where(ExtractedItem.company_id == company_id)
    items = list(db.scalars(query).all())
    matched = [item for item in items if is_noise_risk(item)]
    for item in matched:
        item.status = "closed"
        payload = dict(item.payload or {})
        payload["noise_filter"] = "finance_document_notice"
        payload["closed_reason"] = "普通薪资/工资资料邮件，不作为经营风险。"
        item.payload = payload
    return {"checked": len(items), "closed": len(matched)}


def close_low_signal_extraction_noise(db: Session, *, company_id: UUID | None = None, limit: int = 500) -> dict:
    query = (
        select(ExtractedItem)
        .where(ExtractedItem.item_type.in_(["risk", "decision", "task"]))
        .where(ExtractedItem.status.notin_({"closed", "done", "resolved", "completed", "noise"}))
        .order_by(ExtractedItem.created_at.desc())
        .limit(limit)
    )
    if company_id:
        query = query.where(ExtractedItem.company_id == company_id)
    items = list(db.scalars(query).all())
    matched = [item for item in items if is_noise_risk(item) or is_low_signal_notice_noise(item)]
    for item in matched:
        item.status = "closed"
        payload = dict(item.payload or {})
        payload["noise_filter"] = "low_signal_operational_notice"
        payload["closed_reason"] = "普通通知/提醒/资料邮件，不作为老板驾驶舱风险或决策事项。"
        item.payload = payload
    return {"checked": len(items), "closed": len(matched)}


def is_noise_risk(item: ExtractedItem) -> bool:
    text = " ".join([item.title or "", item.description or "", str(item.payload or "")])
    return is_finance_document_notice(text)


def is_low_signal_notice_noise(item: ExtractedItem) -> bool:
    if _is_non_actionable_heading(item.title) or _is_non_actionable_heading(item.description):
        return True
    if is_low_signal_operational_notice(item.title or "", item_type=item.item_type):
        return True
    text = " ".join([item.title or "", item.description or "", str(item.payload or "")])
    return is_low_signal_operational_notice(text, item_type=item.item_type)


def _is_non_actionable_heading(text: str | None) -> bool:
    value = str(text or "").strip()
    return value.lstrip("#").strip() in {"今日重点", "任务进展", "风险与阻塞", "已形成决策", "明日建议"}
