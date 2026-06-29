from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.entities import Snapshot, WorkEvent
from app.services.cognitive_foundation import append_cognitive_work_event, get_snapshot, upsert_snapshot


EVIDENCE_PAYLOAD_VERSION = "evidence_v1"
COMPANY_PROFILE_SNAPSHOT_TYPE = "company_profile_v1_1"
COMPANY_PROFILE_LEGACY_SNAPSHOT_TYPE = "company_profile_v1"
COMPANY_PROFILE_EXTRACTOR = "CompanyProfileExtractor"
SNAPSHOT_SCHEMA_VERSION = "snapshot_v1_1"
SNAPSHOT_STATUS_BUILDING = "building"
SNAPSHOT_STATUS_ACTIVE = "active"
SNAPSHOT_STATUS_STALE = "stale"
SNAPSHOT_STATUS_FAILED = "failed"


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


@dataclass(frozen=True)
class CognitiveCandidate:
    object_type: str
    extractor: str
    structured: dict[str, Any] = field(default_factory=dict)
    understanding: str = ""
    confidence: str = "low"
    derived_from: dict[str, Any] = field(default_factory=dict)


class CognitiveExtractor(Protocol):
    object_type: str

    def extract(self, evidence_payloads: tuple[dict[str, Any], ...]) -> CognitiveCandidate:
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

    def extract(self, evidence_payloads: tuple[dict[str, Any], ...]) -> CognitiveCandidate:
        summaries = [str(payload.get("summary") or "").strip() for payload in evidence_payloads if _is_evidence_payload(payload)]
        text = "\n".join(summary for summary in summaries if summary)
        contacts = _extract_contacts(text)
        products = _extract_products(text)
        business_scope = _extract_business_scope(text)
        industry = _extract_industry(text)
        advantages = _extract_advantages(text)
        positioning = _company_positioning(text=text, business_scope=business_scope, products=products)
        structured = {
            "company_positioning": positioning,
            "business_scope": business_scope,
            "products": products,
            "industry": industry,
            "target_customers": _extract_target_customers(text),
            "advantages": advantages,
            "contacts": contacts,
        }
        return CognitiveCandidate(
            object_type=self.object_type,
            extractor=COMPANY_PROFILE_EXTRACTOR,
            structured=structured,
            understanding=_company_understanding(structured, evidence_payloads),
            confidence=_snapshot_confidence(structured),
            derived_from=_candidate_derived_from(COMPANY_PROFILE_EXTRACTOR, evidence_payloads),
        )


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
    candidate = extractor.extract(evidence_payloads)
    snapshot_payload = build_snapshot_payload(
        db=db,
        company_id=company_id,
        object_type="company",
        object_id=object_id or str(company_id),
        snapshot_type=COMPANY_PROFILE_SNAPSHOT_TYPE,
        candidates=(candidate,),
        evidence_events=evidence_events,
    )
    summary = _company_profile_summary(snapshot_payload["structured"], snapshot_payload["understanding"])
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
        payload=snapshot_payload,
    )
    return snapshot


def build_snapshot_payload(
    *,
    db: Session,
    company_id: UUID,
    object_type: str,
    object_id: str,
    snapshot_type: str,
    candidates: tuple[CognitiveCandidate, ...],
    evidence_events: tuple[WorkEvent, ...],
) -> dict[str, Any]:
    structured = _merge_structured(candidate.structured for candidate in candidates)
    understanding = _merge_understanding(candidates)
    confidence = _merged_confidence(candidates, structured)
    evidence_refs = [{"work_event_id": str(event.id)} for event in evidence_events]
    version = _next_snapshot_version(db=db, company_id=company_id, object_type=object_type, object_id=object_id, snapshot_type=snapshot_type)
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "snapshot_version": snapshot_type,
        "version": version,
        "snapshot_status": SNAPSHOT_STATUS_ACTIVE,
        "identity": {
            "object_type": object_type,
            "object_id": object_id,
        },
        "structured": structured,
        "understanding": understanding,
        "evidence_refs": evidence_refs,
        "confidence": confidence,
        "derived_from": {
            "candidate_count": len(candidates),
            "extractors": _dedupe(candidate.extractor for candidate in candidates),
            "candidate_sources": [candidate.derived_from for candidate in candidates if candidate.derived_from],
            "evidence_refs": [{"work_event_id": str(event.id)} for event in evidence_events],
            "previous_snapshot_version": version - 1 if version > 1 else None,
        },
        "last_updated": datetime.now(UTC).isoformat(),
    }


def company_profile_snapshot_item(snapshot: Snapshot) -> dict[str, Any]:
    payload = snapshot.payload if isinstance(snapshot.payload, dict) else {}
    fields = _snapshot_structured(payload)
    understanding = _snapshot_understanding(payload, snapshot.summary)
    return {
        "kind": "company_snapshot",
        "title": "公司画像",
        "summary": snapshot.summary,
        "source": "snapshot",
        "snapshot_type": snapshot.snapshot_type,
        "version": payload.get("version") or payload.get("snapshot_version") or snapshot.snapshot_type,
        "schema_snapshot_version": payload.get("snapshot_version") or snapshot.snapshot_type,
        "schema_version": payload.get("schema_version") or "",
        "snapshot_status": payload.get("snapshot_status") or snapshot.status,
        "confidence": payload.get("confidence") or "medium",
        "identity": payload.get("identity") if isinstance(payload.get("identity"), dict) else {},
        "structured": fields,
        "structured_fields": fields,
        "understanding": understanding,
        "evidence_refs": _evidence_refs_from_snapshot_payload(payload, snapshot),
        "derived_from": payload.get("derived_from") if isinstance(payload.get("derived_from"), dict) else {},
    }


def company_profile_snapshot_answer(item: dict[str, Any], *, query: str) -> str:
    fields = item.get("structured") if isinstance(item.get("structured"), dict) else {}
    if not fields:
        fields = item.get("structured_fields") if isinstance(item.get("structured_fields"), dict) else {}
    understanding = str(item.get("understanding") or item.get("summary") or "").strip()
    compact = re.sub(r"\s+", "", query or "")
    if any(token in compact for token in ("产品", "产品线", "有哪些产品")):
        return _structured_with_understanding(
            _list_answer("公司产品", _string_list(fields.get("products")), fallback="公司画像里暂时没有沉淀明确产品清单。"),
            understanding,
        )
    if any(token in compact for token in ("客户", "服务谁", "面向谁")):
        return _structured_with_understanding(
            _list_answer("目标客户", _string_list(fields.get("target_customers")), fallback="公司画像里暂时没有沉淀明确目标客户。"),
            understanding,
        )
    if any(token in compact for token in ("联系", "电话", "邮箱", "地址")):
        contacts = fields.get("contacts") if isinstance(fields.get("contacts"), dict) else {}
        parts = []
        for label, key in (("电话", "phones"), ("邮箱", "emails"), ("地址", "addresses")):
            values = _string_list(contacts.get(key))
            if values:
                parts.append(f"{label}：" + "、".join(values))
        return "\n".join(parts) if parts else "公司画像里暂时没有沉淀联系方式。"
    if understanding:
        return understanding
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


def _snapshot_structured(payload: dict[str, Any]) -> dict[str, Any]:
    fields = payload.get("structured")
    if isinstance(fields, dict):
        return fields
    legacy_fields = payload.get("structured_fields")
    return legacy_fields if isinstance(legacy_fields, dict) else {}


def _snapshot_understanding(payload: dict[str, Any], fallback: str) -> str:
    understanding = str(payload.get("understanding") or "").strip()
    return understanding or str(fallback or "").strip()


def _company_profile_summary(fields: dict[str, Any], understanding: str = "") -> str:
    positioning = str(fields.get("company_positioning") or "").strip()
    if positioning:
        return positioning
    if understanding:
        return understanding
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


def _company_understanding(structured: dict[str, Any], evidence_payloads: tuple[dict[str, Any], ...]) -> str:
    positioning = str(structured.get("company_positioning") or "").strip()
    business_scope = _string_list(structured.get("business_scope"))
    products = _string_list(structured.get("products"))
    target_customers = _string_list(structured.get("target_customers"))
    industry = _string_list(structured.get("industry"))
    advantages = _string_list(structured.get("advantages"))

    lines = []
    if positioning:
        lines.append(positioning)
    elif business_scope:
        lines.append("公司当前可确认的业务重点是" + "、".join(business_scope[:2]) + "。")

    detail_parts = []
    if products:
        detail_parts.append("产品资料显示其产品体系包括" + "、".join(products[:3]))
    if industry:
        detail_parts.append("主要关联" + "、".join(industry[:3]) + "等场景")
    if target_customers:
        detail_parts.append("服务对象偏向" + "、".join(target_customers[:2]))
    if detail_parts:
        lines.append("；".join(detail_parts) + "。")
    if advantages:
        lines.append("资料中强调的特点包括" + "、".join(advantages[:3]) + "。")
    if not lines:
        evidence_titles = _dedupe(_source_title(payload) for payload in evidence_payloads)
        if evidence_titles:
            lines.append("当前认知主要来自" + "、".join(evidence_titles[:2]) + "，但资料不足以形成稳定公司画像。")
    return "\n".join(lines).strip()


def _candidate_derived_from(extractor: str, evidence_payloads: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    return {
        "extractor": extractor,
        "sources": [
            {
                "source_system": str(payload.get("source_system") or ""),
                "source_object_id": str(payload.get("source_object_id") or ""),
                "summary_chars": len(str(payload.get("summary") or "")),
                "title": _source_title(payload),
            }
            for payload in evidence_payloads
            if _is_evidence_payload(payload)
        ],
    }


def _source_title(payload: dict[str, Any]) -> str:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return str(metadata.get("title") or payload.get("source_object_id") or "").strip()


def _merge_structured(values: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for fields in values:
        if not isinstance(fields, dict):
            continue
        for key, value in fields.items():
            if isinstance(value, dict):
                current = merged.get(key) if isinstance(merged.get(key), dict) else {}
                merged[key] = {
                    subkey: _dedupe([*_string_list(current.get(subkey)), *_string_list(subvalue)])
                    for subkey, subvalue in value.items()
                }
            elif isinstance(value, (list, tuple, set)):
                merged[key] = _dedupe([*_string_list(merged.get(key)), *_string_list(value)])
            elif value not in (None, "") and not merged.get(key):
                merged[key] = value
    return merged


def _merge_understanding(candidates: tuple[CognitiveCandidate, ...]) -> str:
    return "\n".join(_dedupe(candidate.understanding for candidate in candidates if candidate.understanding))


def _merged_confidence(candidates: tuple[CognitiveCandidate, ...], structured: dict[str, Any]) -> str:
    if any(candidate.confidence == "high" for candidate in candidates):
        return "high"
    if any(candidate.confidence == "medium" for candidate in candidates):
        return "medium"
    return _snapshot_confidence(structured)


def _next_snapshot_version(
    *,
    db: Session,
    company_id: UUID,
    object_type: str,
    object_id: str,
    snapshot_type: str,
) -> int:
    try:
        current = get_snapshot(db, company_id=company_id, object_type=object_type, object_id=object_id, snapshot_type=snapshot_type)
    except Exception:
        return 1
    payload = current.payload if current is not None and isinstance(current.payload, dict) else {}
    version = payload.get("version")
    try:
        return int(version) + 1
    except (TypeError, ValueError):
        return 1


def _structured_with_understanding(answer: str, understanding: str) -> str:
    if not understanding or understanding in answer:
        return answer
    if answer.startswith("公司画像里暂时没有沉淀"):
        return answer + "\n" + understanding
    return answer + "\n\n理解：" + understanding


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
    phone_candidates = re.findall(r"(?:\+?\d[\d\s-]{6,}\d)", text)
    phones = [phone for phone in phone_candidates if len(re.sub(r"\D", "", phone)) >= 8]
    addresses = []
    address_match = re.search(r"\b(\d{1,5}\s+Xingpu Road,\s*Suzhou Industrial Park)\b", text)
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
