from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import ExtractedItem

def extract_business_items(text: str) -> list[dict[str, Any]]:
    value = _plain_text(text)
    if not value:
        return []
    items: list[dict[str, Any]] = []
    compensation = _extract_compensation_decision(value)
    if compensation:
        items.append(compensation)
    payment = _extract_payment_decision(value)
    if payment:
        items.append(payment)
    return items


def enrich_extracted_item(raw: dict[str, Any], event_text: str) -> dict[str, Any]:
    payload = dict(raw)
    value = _plain_text(" ".join([str(raw.get("title") or ""), str(raw.get("description") or ""), event_text]))
    if _is_compensation_text(value) and _has_compensation_decision_signal(value):
        payload.setdefault("business_domain", "finance_hr")
        payload.setdefault("business_object", "compensation")
        payload.setdefault("decision_points", _compensation_decision_points(value))
        payload.setdefault("suggested_action", "确认发放时间、顺延条件、税务处理和通知责任人。")
        payload.setdefault("amount", _extract_amount(value) or None)
        payload.setdefault("business_date", _extract_date(value))
        if any(term in value for term in ("现金流", "顺延", "离职不予发放", "报税")):
            payload["priority"] = "high"
    elif _is_payment_text(value):
        payload.setdefault("business_domain", "finance")
        payload.setdefault("business_object", "payment")
        payload.setdefault("decision_points", _payment_decision_points(value))
        payload.setdefault("suggested_action", "核对金额、申请人、项目归属和付款依据后再审批。")
        amount = _extract_amount(value)
        payload.setdefault("amount", amount or None)
        payload.setdefault("business_date", _extract_date(value))
        if amount >= 30000:
            payload["priority"] = "high"
    return payload


def enrich_open_business_items(db: Session, *, company_id: UUID | None = None, limit: int = 500) -> dict[str, int]:
    query = (
        select(ExtractedItem)
        .where(ExtractedItem.item_type.in_(["task", "risk", "decision"]))
        .where(ExtractedItem.status.notin_({"closed", "done", "resolved", "completed", "noise"}))
        .order_by(ExtractedItem.created_at.desc())
        .limit(limit)
    )
    if company_id:
        query = query.where(ExtractedItem.company_id == company_id)
    items = list(db.scalars(query).all())
    enriched = 0
    for item in items:
        event_text = item.work_event.content_text if item.work_event and item.work_event.content_text else ""
        payload = enrich_extracted_item(
            {
                **(item.payload or {}),
                "item_type": item.item_type,
                "title": item.title,
                "description": item.description,
                "priority": item.priority,
            },
            event_text,
        )
        if payload != (item.payload or {}):
            item.payload = payload
            item.priority = payload.get("priority") or item.priority
            enriched += 1
    return {"checked": len(items), "enriched": enriched}


def _extract_compensation_decision(value: str) -> dict[str, Any] | None:
    if not _is_compensation_text(value):
        return None
    if not _has_compensation_decision_signal(value):
        return None
    return {
        "item_type": "decision",
        "title": _subject_or_title(value) or "薪酬/奖金发放事项",
        "description": _short(value, 500),
        "owner": None,
        "due_at": None,
        "priority": "high" if any(term in value for term in ("现金流", "顺延", "报税")) else "medium",
        "business_domain": "finance_hr",
        "business_object": "compensation",
        "decision_points": _compensation_decision_points(value),
        "suggested_action": "确认发放时间、顺延条件、税务处理和通知责任人。",
        "amount": _extract_amount(value) or None,
        "business_date": _extract_date(value),
    }


def _extract_payment_decision(value: str) -> dict[str, Any] | None:
    if not _is_payment_text(value):
        return None
    amount = _extract_amount(value)
    return {
        "item_type": "decision",
        "title": _subject_or_title(value) or "付款审批事项",
        "description": _short(value, 500),
        "owner": None,
        "due_at": None,
        "priority": "high" if amount >= 30000 else "medium",
        "business_domain": "finance",
        "business_object": "payment",
        "decision_points": _payment_decision_points(value),
        "suggested_action": "核对金额、申请人、项目归属和付款依据后再审批。",
        "amount": amount or None,
        "business_date": _extract_date(value),
    }


def _is_compensation_text(value: str) -> bool:
    return any(term in value for term in ("奖金", "薪资", "薪酬", "工资", "绩效"))


def _has_compensation_decision_signal(value: str) -> bool:
    return any(term in value for term in ("奖金", "发放规则", "发放时间", "顺延", "报税", "确认版", "签核版", "现金流", "离职不予发放"))


def _is_payment_text(value: str) -> bool:
    return any(term in value for term in ("付款", "借款", "报销")) and any(term in value for term in ("审批", "申请", "金额"))


def _compensation_decision_points(value: str) -> list[str]:
    points: list[str] = []
    if "发放时间" in value:
        points.append("发放时间")
    if "发放规则" in value or "离职不予发放" in value:
        points.append("发放规则")
    if "现金流" in value or "顺延" in value:
        points.append("现金流顺延安排")
    if "报税" in value:
        points.append("税务处理")
    return points or ["发放口径"]


def _payment_decision_points(value: str) -> list[str]:
    points: list[str] = []
    if "金额" in value or _extract_amount(value):
        points.append("金额")
    if "项目" in value:
        points.append("项目归属")
    if "申请人" in value:
        points.append("申请人")
    if "付款依据" in value or "合同" in value:
        points.append("付款依据")
    return points or ["付款必要性"]


def _plain_text(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    try:
        parsed = json.loads(value)
    except Exception:
        return re.sub(r"\s+", " ", value).strip()
    if not isinstance(parsed, dict):
        return re.sub(r"\s+", " ", value).strip()
    parts: list[str] = []
    for key in ("subject", "title", "body", "content"):
        if parsed.get(key):
            parts.append(str(parsed[key]))
    attachments = parsed.get("attachments")
    if isinstance(attachments, list):
        parts.extend(str(item) for item in attachments)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _subject_or_title(value: str) -> str | None:
    match = re.search(r'"subject"\s*:\s*"([^"]+)"', value)
    if match:
        return match.group(1)[:120]
    for marker in ("奖金", "付款", "报销", "借款"):
        index = value.find(marker)
        if index >= 0:
            start = max(0, index - 30)
            end = min(len(value), index + 60)
            return _short(value[start:end], 120)
    return None


def _extract_amount(value: str) -> float:
    amount_terms = re.findall(r"(?:金额|合计|总额|申请金额)[：:\s]*([0-9][0-9,]*(?:\.[0-9]+)?)", value)
    if not amount_terms:
        amount_terms = re.findall(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:元|人民币)", value)
    for raw in amount_terms:
        try:
            return float(raw.replace(",", ""))
        except ValueError:
            continue
    return 0.0


def _extract_date(value: str) -> str | None:
    match = re.search(r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})", value)
    if not match:
        return None
    year, month, day = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


def _short(value: str, max_length: int) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "")).strip()
    return normalized if len(normalized) <= max_length else f"{normalized[:max_length]}..."
