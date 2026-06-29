from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.entities import Snapshot, WorkEvent
from app.services.cognitive_foundation import append_cognitive_work_event, upsert_snapshot


EVIDENCE_PAYLOAD_VERSION = "evidence_v1"
COMPANY_PROFILE_SNAPSHOT_TYPE = "company_profile_v1"
COMPANY_PROFILE_EXTRACTOR = "CompanyProfileExtractor"


@dataclass(frozen=True)
class EvidenceInput:
    source_system: str
    source_object_id: str
    organization_binding: dict[str, Any]
    visibility_binding: dict[str, Any]
    timestamp: datetime
    extractor: str
    summary: str
    metadata: dict[str, Any]


class CognitiveExtractor(Protocol):
    object_type: str

    def extract(self, evidence_payloads: tuple[dict[str, Any], ...]) -> dict[str, Any]:
        ...


class ExtractorRegistry:
    def __init__(self, extractors: tuple[CognitiveExtractor, ...]) -> None:
        self._extractors = {extractor.object_type: extractor for extractor in extractors}

    def get(self, object_type: str) -> CognitiveExtractor:
        extractor = self._extractors.get(object_type)
        if extractor is None:
            raise KeyError(f"unknown cognitive extractor: {object_type}")
        return extractor


def default_extractor_registry() -> ExtractorRegistry:
    return ExtractorRegistry((CompanyProfileExtractor(),))


def build_evidence_payload(evidence: EvidenceInput) -> dict[str, Any]:
    return {
        "payload_version": EVIDENCE_PAYLOAD_VERSION,
        "source_system": evidence.source_system,
        "source_object_id": evidence.source_object_id,
        "organization_binding": dict(evidence.organization_binding),
        "visibility_binding": dict(evidence.visibility_binding),
        "timestamp": evidence.timestamp.isoformat(),
        "extractor": evidence.extractor,
        "summary": evidence.summary.strip(),
        "metadata": dict(evidence.metadata),
    }


def append_evidence_work_event(
    db: Session,
    *,
    company_id: UUID,
    evidence: EvidenceInput,
    object_type: str,
    object_id: str,
    actor: str = "system",
) -> WorkEvent:
    payload = build_evidence_payload(evidence)
    return append_cognitive_work_event(
        db,
        company_id=company_id,
        event_type=f"evidence.{object_type}.observed",
        object_type=object_type,
        object_id=object_id,
        source=evidence.source_system,
        actor=actor,
        payload=payload,
        visibility_scope=str(evidence.visibility_binding.get("scope") or "company"),
        data_classification=str(evidence.visibility_binding.get("data_classification") or "company"),
        allowed_user_ids=_string_list(evidence.visibility_binding.get("allowed_user_ids")),
        allowed_departments=_string_list(evidence.visibility_binding.get("allowed_departments")),
        allowed_roles=_string_list(evidence.visibility_binding.get("allowed_roles")),
    )


class CompanyProfileExtractor:
    object_type = "company_profile"

    def extract(self, evidence_payloads: tuple[dict[str, Any], ...]) -> dict[str, Any]:
        summaries = [str(payload.get("summary") or "").strip() for payload in evidence_payloads if _is_evidence_payload(payload)]
        text = "\n".join(summary for summary in summaries if summary)
        contacts = _extract_contacts(text)
        products = _extract_products(text)
        business_scope = _extract_business_scope(text)
        industry = _extract_industry(text)
        advantages = _extract_advantages(text)
        positioning = _company_positioning(text=text, business_scope=business_scope, products=products)
        return {
            "company_positioning": positioning,
            "business_scope": business_scope,
            "products": products,
            "industry": industry,
            "target_customers": _extract_target_customers(text),
            "advantages": advantages,
            "contacts": contacts,
        }


def build_company_profile_snapshot(
    db: Session,
    *,
    company_id: UUID,
    evidence_events: tuple[WorkEvent, ...],
    object_id: str | None = None,
    registry: ExtractorRegistry | None = None,
) -> Snapshot | None:
    evidence_payloads = tuple(
        event.payload for event in evidence_events if isinstance(getattr(event, "payload", None), dict) and _is_evidence_payload(event.payload)
    )
    if not evidence_payloads:
        return None
    extractor = (registry or default_extractor_registry()).get("company_profile")
    structured_fields = extractor.extract(evidence_payloads)
    summary = _company_profile_summary(structured_fields)
    evidence_refs = [{"work_event_id": str(event.id)} for event in evidence_events]
    snapshot = upsert_snapshot(
        db,
        company_id=company_id,
        object_type="company",
        object_id=object_id or str(company_id),
        snapshot_type=COMPANY_PROFILE_SNAPSHOT_TYPE,
        status="completed",
        summary=summary,
        recommendation="",
        risk_level="unknown",
        source_event_ids=[str(event.id) for event in evidence_events],
        payload={
            "snapshot_version": COMPANY_PROFILE_SNAPSHOT_TYPE,
            "structured_fields": structured_fields,
            "confidence": _snapshot_confidence(structured_fields),
            "evidence_refs": evidence_refs,
            "last_updated": datetime.now(UTC).isoformat(),
        },
    )
    return snapshot


def company_profile_snapshot_item(snapshot: Snapshot) -> dict[str, Any]:
    payload = snapshot.payload if isinstance(snapshot.payload, dict) else {}
    fields = payload.get("structured_fields") if isinstance(payload.get("structured_fields"), dict) else {}
    return {
        "kind": "company_snapshot",
        "title": "公司画像",
        "summary": snapshot.summary,
        "source": "snapshot",
        "snapshot_type": snapshot.snapshot_type,
        "version": payload.get("snapshot_version") or snapshot.snapshot_type,
        "confidence": payload.get("confidence") or "medium",
        "structured_fields": fields,
        "evidence_refs": _evidence_refs_from_snapshot_payload(payload, snapshot),
    }


def company_profile_snapshot_answer(item: dict[str, Any], *, query: str) -> str:
    fields = item.get("structured_fields") if isinstance(item.get("structured_fields"), dict) else {}
    compact = re.sub(r"\s+", "", query or "")
    if any(token in compact for token in ("产品", "产品线", "有哪些产品")):
        return _list_answer("公司产品", _string_list(fields.get("products")), fallback="公司画像里暂时没有沉淀明确产品清单。")
    if any(token in compact for token in ("客户", "服务谁", "面向谁")):
        return _list_answer("目标客户", _string_list(fields.get("target_customers")), fallback="公司画像里暂时没有沉淀明确目标客户。")
    if any(token in compact for token in ("联系", "电话", "邮箱", "地址")):
        contacts = fields.get("contacts") if isinstance(fields.get("contacts"), dict) else {}
        parts = []
        for label, key in (("电话", "phones"), ("邮箱", "emails"), ("地址", "addresses")):
            values = _string_list(contacts.get(key))
            if values:
                parts.append(f"{label}：" + "、".join(values))
        return "\n".join(parts) if parts else "公司画像里暂时没有沉淀联系方式。"
    positioning = str(fields.get("company_positioning") or item.get("summary") or "").strip()
    lines = [positioning] if positioning else []
    business_scope = _string_list(fields.get("business_scope"))
    industry = _string_list(fields.get("industry"))
    advantages = _string_list(fields.get("advantages"))
    if business_scope:
        lines.append("业务范围：" + "、".join(business_scope))
    if industry:
        lines.append("行业/场景：" + "、".join(industry))
    if advantages:
        lines.append("特点：" + "、".join(advantages[:3]))
    return "\n".join(lines) if lines else "公司画像里暂时没有沉淀主营业务或公司简介。"


def _is_evidence_payload(payload: dict[str, Any]) -> bool:
    return payload.get("payload_version") == EVIDENCE_PAYLOAD_VERSION and bool(str(payload.get("summary") or "").strip())


def _evidence_refs_from_snapshot_payload(payload: dict[str, Any], snapshot: Snapshot) -> list[str]:
    refs = payload.get("evidence_refs")
    if isinstance(refs, list):
        values = []
        for ref in refs:
            if isinstance(ref, dict):
                values.append(str(ref.get("work_event_id") or ref.get("source_event_id") or "").strip())
            else:
                values.append(str(ref or "").strip())
        compact_values = [value for value in values if value]
        if compact_values:
            return compact_values
    return [str(item) for item in (snapshot.source_event_ids or []) if str(item).strip()]


def _company_profile_summary(fields: dict[str, Any]) -> str:
    positioning = str(fields.get("company_positioning") or "").strip()
    if positioning:
        return positioning
    business_scope = _string_list(fields.get("business_scope"))
    if business_scope:
        return "公司业务范围：" + "、".join(business_scope)
    products = _string_list(fields.get("products"))
    if products:
        return "公司产品：" + "、".join(products)
    return "公司画像已生成，但可用资料仍不足，需要补充更多正式证据。"


def _snapshot_confidence(fields: dict[str, Any]) -> str:
    populated = sum(1 for key in ("company_positioning", "business_scope", "products", "industry", "contacts") if fields.get(key))
    if populated >= 4:
        return "high"
    if populated >= 2:
        return "medium"
    return "low"


def _company_positioning(*, text: str, business_scope: list[str], products: list[str]) -> str:
    if "固势" in text or "gaustek" in text.lower():
        if _contains_any(text, ("测试", "测控", "实验")):
            return "固势（苏州）科技有限公司主要面向测试测量和实验场景，提供相关产品与解决方案。"
        return "固势（苏州）科技有限公司是一家提供专业产品与服务的企业。"
    if business_scope:
        return "公司主要从事" + "、".join(business_scope[:2]) + "。"
    if products:
        return "公司提供" + "、".join(products[:2]) + "等产品。"
    return ""


def _extract_business_scope(text: str) -> list[str]:
    values = []
    if _contains_any(text, ("测试", "测控", "实验")):
        values.append("测试测量相关产品与解决方案")
    if _contains_any(text, ("产品系列", "全系列产品手册")):
        values.append("产品系列研发与交付")
    return _dedupe(values)


def _extract_products(text: str) -> list[str]:
    values = []
    if "全系列产品手册" in text:
        values.append("GAUSTEK SRI 全系列产品")
    if "产品系列" in text:
        values.append("测试测量产品系列")
    return _dedupe(values)


def _extract_industry(text: str) -> list[str]:
    values = []
    if _contains_any(text, ("测试", "测控")):
        values.append("测试测量")
    if "实验" in text:
        values.append("实验室/研发测试场景")
    if _contains_any(text, ("工业", "industrial")):
        values.append("工业场景")
    return _dedupe(values)


def _extract_target_customers(text: str) -> list[str]:
    values = []
    if _contains_any(text, ("实验", "测试")):
        values.append("需要测试测量能力的研发、实验和生产团队")
    return _dedupe(values)


def _extract_advantages(text: str) -> list[str]:
    values = []
    if "让测试更简单" in text:
        values.append("让测试更简单")
    if "让实验更高效" in text:
        values.append("让实验更高效")
    return _dedupe(values)


def _extract_contacts(text: str) -> dict[str, list[str]]:
    emails = re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)
    phones = re.findall(r"(?:\+?\d[\d\s-]{6,}\d)", text)
    addresses = []
    address_match = re.search(r"(\d{3,5}\s+[A-Za-z0-9 ,.-]*Suzhou[^,\n]*(?:Park)?)", text)
    if address_match:
        addresses.append(address_match.group(1).strip())
    return {
        "emails": _dedupe(emails),
        "phones": _dedupe(" ".join(phone.split()) for phone in phones),
        "addresses": _dedupe(addresses),
    }


def _list_answer(title: str, values: list[str], *, fallback: str) -> str:
    if not values:
        return fallback
    return f"{title}：\n" + "\n".join(f"{index}. {value}" for index, value in enumerate(values, start=1))


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple, set)):
        values = [str(item) for item in value]
    else:
        values = [str(value)]
    return [item.strip() for item in values if item and item.strip()]


def _dedupe(values) -> list[str]:
    seen = set()
    result = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
