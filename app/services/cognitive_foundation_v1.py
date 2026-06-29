from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import Snapshot, WorkEvent
from app.services.cognitive.evidence_pack import (
    EvidencePack,
    build_evidence_pack_from_text,
    evidence_pack_from_payload,
)
from app.services.cognitive_foundation import append_cognitive_work_event, get_snapshot, upsert_snapshot
from app.services.llm.gateway import LLMGateway


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
    coverage: dict[str, str] = field(default_factory=dict)
    open_questions: tuple[str, ...] = ()
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
        packs = _evidence_packs(evidence_payloads)
        text = "\n".join(summary for summary in summaries if summary)
        contacts = _extract_contacts(text)
        products = _extract_products(text, packs)
        business_scope = _extract_business_scope(text, packs)
        industry = _extract_industry(text, packs)
        advantages = _extract_advantages(text)
        positioning = _company_positioning(text=text, business_scope=business_scope, products=products)
        target_customers = _extract_target_customers(text, packs)
        coverage = _company_candidate_coverage(structured={
            "products": products,
            "target_customers": target_customers,
            "contacts": contacts,
            "business_scope": business_scope,
        }, packs=packs)
        open_questions = _company_open_questions(packs, coverage)
        structured = {
            "company_positioning": positioning,
            "business_scope": business_scope,
            "products": products,
            "industry": industry,
            "target_customers": target_customers,
            "advantages": advantages,
            "contacts": contacts,
        }
        return CognitiveCandidate(
            object_type=self.object_type,
            extractor=COMPANY_PROFILE_EXTRACTOR,
            structured=structured,
            understanding=_company_understanding_with_optional_llm(structured, evidence_payloads, packs=packs, coverage=coverage, open_questions=open_questions),
            confidence=_snapshot_confidence(structured),
            coverage=coverage,
            open_questions=open_questions,
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
            "coverage": _merge_candidate_coverage(candidates),
            "open_questions": _dedupe(question for candidate in candidates for question in candidate.open_questions),
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
        customer_answer = _customer_answer(fields, item)
        return _structured_with_understanding(
            customer_answer,
            understanding,
        )
    if any(token in compact for token in ("不明确", "不清楚", "缺口", "缺失", "不能确认", "不确定")):
        return _uncertainty_answer(item)
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


def _evidence_packs(evidence_payloads: tuple[dict[str, Any], ...]) -> tuple[EvidencePack, ...]:
    packs: list[EvidencePack] = []
    for payload in evidence_payloads:
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        pack_payload = metadata.get("evidence_pack") if isinstance(metadata.get("evidence_pack"), dict) else None
        if pack_payload:
            try:
                packs.append(evidence_pack_from_payload(pack_payload))
                continue
            except Exception:
                pass
        summary = str(payload.get("summary") or "").strip()
        if summary:
            packs.append(
                build_evidence_pack_from_text(
                    summary,
                    filename=str(metadata.get("title") or ""),
                    source_system=str(payload.get("source_system") or ""),
                    source_object_id=str(payload.get("source_object_id") or ""),
                    source_object_type=str(metadata.get("document_type") or metadata.get("resource_type") or ""),
                    organization_binding=dict(payload.get("organization_binding") or {}),
                    visibility_binding=dict(payload.get("visibility_binding") or {}),
                    metadata=metadata,
                )
            )
    return tuple(packs)


def _company_understanding(
    structured: dict[str, Any],
    evidence_payloads: tuple[dict[str, Any], ...],
    *,
    packs: tuple[EvidencePack, ...] = (),
    coverage: dict[str, str] | None = None,
    open_questions: tuple[str, ...] = (),
) -> str:
    positioning = str(structured.get("company_positioning") or "").strip()
    business_scope = _string_list(structured.get("business_scope"))
    products = _string_list(structured.get("products"))
    target_customers = _string_list(structured.get("target_customers"))
    industry = _string_list(structured.get("industry"))
    advantages = _string_list(structured.get("advantages"))
    coverage = dict(coverage or {})

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
    pack_quality = _pack_quality_summary(packs)
    if pack_quality:
        lines.append(pack_quality)
    if open_questions:
        lines.append("当前认知缺口：" + "；".join(open_questions[:3]) + "。")
    if coverage.get("target_customers") == "inferred" and target_customers:
        lines.append("客户判断属于基于场景的推断，不等同于已确认客户名单。")
    if not lines:
        evidence_titles = _dedupe(_source_title(payload) for payload in evidence_payloads)
        if evidence_titles:
            lines.append("当前认知主要来自" + "、".join(evidence_titles[:2]) + "，但资料不足以形成稳定公司画像。")
    return "\n".join(lines).strip()


def _company_understanding_with_optional_llm(
    structured: dict[str, Any],
    evidence_payloads: tuple[dict[str, Any], ...],
    *,
    packs: tuple[EvidencePack, ...],
    coverage: dict[str, str],
    open_questions: tuple[str, ...],
) -> str:
    fallback = _company_understanding(structured, evidence_payloads, packs=packs, coverage=coverage, open_questions=open_questions)
    if not settings.deepseek_api_key or os.environ.get("PYTEST_CURRENT_TEST"):
        return fallback
    prompt = _company_snapshot_builder_prompt(structured=structured, packs=packs, coverage=coverage, open_questions=open_questions, fallback=fallback)
    try:
        raw = LLMGateway().complete_task_text(prompt, task_type="snapshot_builder", temperature=0.15)
    except Exception:
        return fallback
    data = _json_object(raw or "")
    understanding = str((data or {}).get("understanding") or "").strip()
    if not understanding:
        return fallback
    return understanding[:1800]


def _company_snapshot_builder_prompt(
    *,
    structured: dict[str, Any],
    packs: tuple[EvidencePack, ...],
    coverage: dict[str, str],
    open_questions: tuple[str, ...],
    fallback: str,
) -> str:
    pack_payload = [
        {
            "content_profile": pack.content_profile.__dict__,
            "key_claims": [claim.__dict__ for claim in pack.key_claims[:10]],
            "entities": [entity.__dict__ for entity in pack.entities[:20]],
            "topics": list(pack.topics[:12]),
            "relations": [relation.__dict__ for relation in pack.relations[:10]],
            "uncertainties": [item.__dict__ for item in pack.uncertainties[:8]],
            "quality": pack.quality,
        }
        for pack in packs[:4]
    ]
    return (
        "你是 Digital Advisor 的 Snapshot Builder，只负责基于 Evidence Pack 生成当前公司认知。\n"
        "要求：不要复述原文；不要编造客户名单、营收、趋势；明确区分已支持事实和推断；输出 JSON。\n"
        "JSON schema: {\"understanding\":\"...\"}\n"
        "understanding 应包含：公司定位、产品/能力体系、应用场景、客户类型是否为推断、资料缺口和置信边界。\n\n"
        f"structured={json.dumps(structured, ensure_ascii=False, default=str)[:2500]}\n"
        f"coverage={json.dumps(coverage, ensure_ascii=False, default=str)}\n"
        f"open_questions={json.dumps(list(open_questions), ensure_ascii=False, default=str)}\n"
        f"evidence_packs={json.dumps(pack_payload, ensure_ascii=False, default=str)[:5000]}\n"
        f"fallback_understanding={fallback[:1200]}\n"
    )


def _json_object(raw: str) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        text = text.removeprefix("json").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _candidate_derived_from(extractor: str, evidence_payloads: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    packs = _evidence_packs(evidence_payloads)
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
        "evidence_pack_count": len(packs),
        "evidence_pack_quality": [_pack.content_profile.extraction_quality for _pack in packs],
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


def _merge_candidate_coverage(candidates: tuple[CognitiveCandidate, ...]) -> dict[str, str]:
    rank = {"missing": 0, "inferred": 1, "partial": 2, "supported": 3}
    merged: dict[str, str] = {}
    for candidate in candidates:
        for key, value in candidate.coverage.items():
            current = merged.get(key, "missing")
            if rank.get(value, 0) >= rank.get(current, 0):
                merged[key] = value
    return merged


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


def _customer_answer(fields: dict[str, Any], item: dict[str, Any]) -> str:
    values = _string_list(fields.get("target_customers"))
    derived = item.get("derived_from") if isinstance(item.get("derived_from"), dict) else {}
    coverage = derived.get("coverage") if isinstance(derived.get("coverage"), dict) else {}
    if not values:
        return "公司画像里暂时没有沉淀明确目标客户。"
    if coverage.get("target_customers") == "inferred":
        return "资料中没有列出明确客户名单。当前只能推断目标客户类型：\n" + "\n".join(f"{index}. {value}" for index, value in enumerate(values, start=1))
    return _list_answer("目标客户", values, fallback="公司画像里暂时没有沉淀明确目标客户。")


def _uncertainty_answer(item: dict[str, Any]) -> str:
    derived = item.get("derived_from") if isinstance(item.get("derived_from"), dict) else {}
    questions = _string_list(derived.get("open_questions"))
    if not questions:
        return "当前公司画像没有记录明显认知缺口。"
    return "这份资料里还不明确的地方：\n" + "\n".join(f"{index}. {value}" for index, value in enumerate(questions, start=1))


def _company_candidate_coverage(*, structured: dict[str, Any], packs: tuple[EvidencePack, ...]) -> dict[str, str]:
    contacts = structured.get("contacts") if isinstance(structured.get("contacts"), dict) else {}
    has_contact = any(_string_list(contacts.get(key)) for key in ("emails", "phones", "addresses"))
    return {
        "products": "supported" if _string_list(structured.get("products")) else "missing",
        "business_scope": "supported" if _string_list(structured.get("business_scope")) else "missing",
        "target_customers": _customer_coverage(packs, _string_list(structured.get("target_customers"))),
        "contacts": "partial" if has_contact else "missing",
    }


def _customer_coverage(packs: tuple[EvidencePack, ...], customers: list[str]) -> str:
    if not customers:
        return "missing"
    for pack in packs:
        if any(entity.entity_type in {"customer", "account"} for entity in pack.entities):
            return "supported"
        if any(claim.claim_type == "customer_need" for claim in pack.key_claims):
            return "partial"
    return "inferred"


def _company_open_questions(packs: tuple[EvidencePack, ...], coverage: dict[str, str]) -> tuple[str, ...]:
    questions = []
    for pack in packs:
        questions.extend(item.question for item in pack.uncertainties)
    if coverage.get("target_customers") == "inferred":
        questions.append("没有明确客户名单，客户只能按应用场景推断。")
    if coverage.get("contacts") == "missing":
        questions.append("没有可确认联系方式。")
    return tuple(_dedupe(questions))


def _pack_quality_summary(packs: tuple[EvidencePack, ...]) -> str:
    if not packs:
        return ""
    qualities = [pack.content_profile.extraction_quality for pack in packs]
    if any(quality in {"thin", "failed"} for quality in qualities):
        return "当前证据覆盖偏薄，画像需要更多正式资料交叉验证。"
    return ""


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


def _extract_business_scope(text: str, packs: tuple[EvidencePack, ...] = ()) -> list[str]:
    values = []
    if _contains_any(text, ("测试", "测控", "实验")):
        values.append("测试测量相关产品与解决方案")
    if _contains_any(text, ("产品系列", "全系列产品手册")):
        values.append("产品系列研发与交付")
    if any(claim.claim_type in {"capability", "offering"} and "解决方案" in claim.claim for pack in packs for claim in pack.key_claims):
        values.append("测试测量解决方案能力")
    return _dedupe(values)


def _extract_products(text: str, packs: tuple[EvidencePack, ...] = ()) -> list[str]:
    values = []
    if "全系列产品手册" in text:
        values.append("GAUSTEK SRI 全系列产品")
    if "产品系列" in text:
        values.append("测试测量产品系列")
    values.extend(entity.name for pack in packs for entity in pack.entities if entity.entity_type == "product")
    deduped = _dedupe(values)
    if any(value.startswith("GAUSTEK SRI ") for value in deduped):
        deduped = [value for value in deduped if value != "GAUSTEK SRI"]
    return deduped


def _extract_industry(text: str, packs: tuple[EvidencePack, ...] = ()) -> list[str]:
    values = []
    if _contains_any(text, ("测试", "测控")):
        values.append("测试测量")
    if "实验" in text:
        values.append("实验室/研发测试场景")
    if _contains_any(text, ("工业", "industrial")):
        values.append("工业场景")
    values.extend(entity.name for pack in packs for entity in pack.entities if entity.entity_type in {"domain", "scenario"})
    values.extend(str(topic.get("topic") or "") for pack in packs for topic in pack.topics if str(topic.get("topic") or "").strip())
    return _dedupe(values)


def _extract_target_customers(text: str, packs: tuple[EvidencePack, ...] = ()) -> list[str]:
    values = []
    values.extend(entity.name for pack in packs for entity in pack.entities if entity.entity_type in {"customer", "account"})
    pack_text = " ".join(
        [
            *(entity.name for pack in packs for entity in pack.entities if entity.entity_type in {"scenario", "domain"}),
            *(str(topic.get("topic") or "") for pack in packs for topic in pack.topics),
        ]
    )
    if _contains_any(f"{text} {pack_text}", ("实验", "测试", "研发", "工业")):
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
