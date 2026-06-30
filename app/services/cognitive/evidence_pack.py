from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from app.shared.file_intelligence.models import ExtractionResult


EVIDENCE_PACK_VERSION = "evidence_pack_v1"


@dataclass(frozen=True)
class SourceRef:
    source_system: str
    source_object_id: str
    source_object_type: str = ""
    filename: str = ""
    mime_type: str | None = None


@dataclass(frozen=True)
class ContentProfile:
    document_type: str = ""
    content_type: str = ""
    language: str = ""
    page_count: int | None = None
    extraction_quality: str = "partial"


@dataclass(frozen=True)
class EvidenceClaim:
    claim: str
    claim_type: str
    confidence: str = "medium"
    evidence_refs: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class EvidenceEntity:
    name: str
    entity_type: str
    confidence: str = "medium"
    evidence_refs: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class EvidenceRelation:
    subject: str
    predicate: str
    object: str
    confidence: str = "medium"
    evidence_refs: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class EvidenceMetric:
    name: str
    value: str
    unit: str = ""
    evidence_refs: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class EvidenceUncertainty:
    question: str
    missing_type: str
    impact: str


@dataclass(frozen=True)
class EvidencePack:
    pack_version: str
    source_ref: SourceRef
    organization_binding: dict[str, Any]
    visibility_binding: dict[str, Any]
    content_profile: ContentProfile
    outline: tuple[dict[str, Any], ...] = ()
    key_claims: tuple[EvidenceClaim, ...] = ()
    entities: tuple[EvidenceEntity, ...] = ()
    topics: tuple[dict[str, Any], ...] = ()
    relations: tuple[EvidenceRelation, ...] = ()
    metrics: tuple[EvidenceMetric, ...] = ()
    time_refs: tuple[dict[str, Any], ...] = ()
    evidence_spans: tuple[dict[str, Any], ...] = ()
    uncertainties: tuple[EvidenceUncertainty, ...] = ()
    quality: dict[str, Any] = field(default_factory=dict)
    derived_from: dict[str, Any] = field(default_factory=dict)


def evidence_pack_payload(pack: EvidencePack) -> dict[str, Any]:
    return asdict(pack)


def evidence_pack_from_payload(payload: dict[str, Any]) -> EvidencePack:
    return EvidencePack(
        pack_version=str(payload.get("pack_version") or EVIDENCE_PACK_VERSION),
        source_ref=SourceRef(**dict(payload.get("source_ref") or {})),
        organization_binding=dict(payload.get("organization_binding") or {}),
        visibility_binding=dict(payload.get("visibility_binding") or {}),
        content_profile=ContentProfile(**dict(payload.get("content_profile") or {})),
        outline=tuple(dict(item) for item in payload.get("outline") or [] if isinstance(item, dict)),
        key_claims=tuple(EvidenceClaim(**dict(item)) for item in payload.get("key_claims") or [] if isinstance(item, dict)),
        entities=tuple(EvidenceEntity(**dict(item)) for item in payload.get("entities") or [] if isinstance(item, dict)),
        topics=tuple(dict(item) for item in payload.get("topics") or [] if isinstance(item, dict)),
        relations=tuple(EvidenceRelation(**dict(item)) for item in payload.get("relations") or [] if isinstance(item, dict)),
        metrics=tuple(EvidenceMetric(**dict(item)) for item in payload.get("metrics") or [] if isinstance(item, dict)),
        time_refs=tuple(dict(item) for item in payload.get("time_refs") or [] if isinstance(item, dict)),
        evidence_spans=tuple(dict(item) for item in payload.get("evidence_spans") or [] if isinstance(item, dict)),
        uncertainties=tuple(EvidenceUncertainty(**dict(item)) for item in payload.get("uncertainties") or [] if isinstance(item, dict)),
        quality=dict(payload.get("quality") or {}),
        derived_from=dict(payload.get("derived_from") or {}),
    )


def build_evidence_pack_from_extraction(
    extraction: ExtractionResult,
    *,
    source_system: str,
    source_object_id: str,
    source_object_type: str = "",
    organization_binding: dict[str, Any] | None = None,
    visibility_binding: dict[str, Any] | None = None,
) -> EvidencePack:
    text = str(extraction.text or "").strip()
    spans = _evidence_spans_from_extraction(extraction, text)
    return EvidencePack(
        pack_version=EVIDENCE_PACK_VERSION,
        source_ref=SourceRef(
            source_system=source_system,
            source_object_id=source_object_id,
            source_object_type=source_object_type,
            filename=extraction.filename,
            mime_type=extraction.mime_type,
        ),
        organization_binding=dict(organization_binding or {}),
        visibility_binding=dict(visibility_binding or {}),
        content_profile=ContentProfile(
            document_type=_document_type(extraction.filename, source_object_type),
            content_type=_content_type(text),
            language=extraction.language or _language(text),
            page_count=extraction.page_count,
            extraction_quality=_extraction_quality(extraction, text),
        ),
        outline=_outline(text, spans),
        key_claims=_key_claims(text, spans),
        entities=_entities(text, spans),
        topics=_topics(text),
        relations=_relations(text, spans),
        metrics=_metrics(text, spans),
        time_refs=_time_refs(text),
        evidence_spans=spans,
        uncertainties=_uncertainties(text),
        quality={
            "success": extraction.success,
            "text_chars": len(text),
            "warnings": list(extraction.warnings or ()),
            "error": extraction.error or "",
        },
        derived_from={
            "extractor": extraction.extractor,
            "metadata": dict(extraction.metadata or {}),
        },
    )


def build_evidence_pack_from_text(
    text: str,
    *,
    filename: str = "",
    mime_type: str | None = None,
    source_system: str,
    source_object_id: str,
    source_object_type: str = "",
    organization_binding: dict[str, Any] | None = None,
    visibility_binding: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> EvidencePack:
    return build_evidence_pack_from_extraction(
        ExtractionResult(
            success=bool(str(text or "").strip()),
            text=str(text or ""),
            filename=filename,
            mime_type=mime_type,
            extractor="text_evidence_pack_builder",
            metadata=dict(metadata or {}),
        ),
        source_system=source_system,
        source_object_id=source_object_id,
        source_object_type=source_object_type,
        organization_binding=organization_binding,
        visibility_binding=visibility_binding,
    )


def _evidence_ref(span_id: str) -> tuple[dict[str, Any], ...]:
    return ({"span_id": span_id},)


def _evidence_spans_from_extraction(extraction: ExtractionResult, text: str) -> tuple[dict[str, Any], ...]:
    metadata = extraction.metadata if isinstance(extraction.metadata, dict) else {}
    page_texts = metadata.get("page_texts")
    if not isinstance(page_texts, list):
        return _evidence_spans(text)
    spans: list[dict[str, Any]] = []
    for page_item in page_texts:
        if not isinstance(page_item, dict):
            continue
        try:
            page = int(page_item.get("page") or 0)
        except (TypeError, ValueError):
            page = 0
        page_text = str(page_item.get("text") or "").strip()
        if page <= 0 or not page_text:
            continue
        source = str(page_item.get("source") or "").strip()
        for index, chunk in enumerate(_span_chunks(page_text), start=1):
            spans.append({"span_id": f"p{page}s{index}", "page": page, "text": chunk[:280], "source": source})
            if len(spans) >= 40:
                return tuple(spans)
    return tuple(spans) or _evidence_spans(text)


def _evidence_spans(text: str) -> tuple[dict[str, Any], ...]:
    chunks = [item.strip() for item in re.split(r"[\n。；;]+", text) if item.strip()]
    spans = []
    for index, chunk in enumerate(chunks[:40], start=1):
        spans.append({"span_id": f"s{index}", "text": chunk[:280]})
    return tuple(spans)


def _span_chunks(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"[\n。；;]+", text) if item.strip()]


def _span_for(spans: tuple[dict[str, Any], ...], term: str) -> str:
    term_lower = term.lower()
    for span in spans:
        if term_lower in str(span.get("text") or "").lower():
            return str(span.get("span_id") or "")
    return ""


def _outline(text: str, spans: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    values = []
    for title in ("公司介绍", "企业介绍", "产品系列", "产品介绍", "应用场景", "解决方案", "联系方式", "目录"):
        if title.lower() in text.lower():
            values.append({"title": title, "page_refs": _page_refs_for(spans, title)})
    return tuple(values)


def _page_refs_for(spans: tuple[dict[str, Any], ...], term: str) -> list[int]:
    refs: list[int] = []
    term_lower = term.lower()
    for span in spans:
        if term_lower not in str(span.get("text") or "").lower():
            continue
        page = span.get("page")
        if isinstance(page, int) and page > 0 and page not in refs:
            refs.append(page)
    return refs


def _key_claims(text: str, spans: tuple[dict[str, Any], ...]) -> tuple[EvidenceClaim, ...]:
    claims: list[EvidenceClaim] = []
    if _contains_any(text, ("测试测量", "测控", "实验")):
        claims.append(EvidenceClaim("资料显示其内容与测试测量、实验或测控场景相关。", "market", evidence_refs=_evidence_ref(_span_for(spans, "测试"))))
    if _contains_any(text, ("产品", "产品系列", "product")):
        claims.append(EvidenceClaim("资料包含产品或产品系列信息。", "offering", evidence_refs=_evidence_ref(_span_for(spans, "产品"))))
    if _contains_any(text, ("解决方案", "方案")):
        claims.append(EvidenceClaim("资料包含解决方案相关描述。", "capability", evidence_refs=_evidence_ref(_span_for(spans, "方案"))))
    if _contains_any(text, ("让测试更简单", "让实验更高效")):
        claims.append(EvidenceClaim("资料强调让测试更简单、让实验更高效。", "capability", evidence_refs=_evidence_ref(_span_for(spans, "让测试更简单"))))
    if _contains_any(text, ("简单", "高效", "效率", "自动化", "稳定", "可靠", "精度", "快速")):
        claims.append(EvidenceClaim("资料出现效率、易用性、可靠性或精度相关价值主张。", "value_proposition", evidence_refs=_evidence_ref(_span_for(spans, "高效"))))
    if _contains_any(text, ("研发", "实验室", "生产", "工业", "客户", "用户")):
        claims.append(EvidenceClaim("资料提供了可用于推断目标使用场景或客户类型的线索。", "customer_need", confidence="medium", evidence_refs=_evidence_ref(_span_for(spans, "研发"))))
    return tuple(claims)


def _entities(text: str, spans: tuple[dict[str, Any], ...]) -> tuple[EvidenceEntity, ...]:
    entities: list[EvidenceEntity] = []
    for name, entity_type in (
        ("固势（苏州）科技有限公司", "organization"),
        ("固势", "organization"),
        ("GAUSTEK SRI", "product"),
        ("测试测量", "domain"),
        ("测试", "domain"),
        ("测量", "domain"),
        ("实验室", "scenario"),
        ("研发测试", "scenario"),
        ("研发", "scenario"),
        ("生产", "scenario"),
        ("工业场景", "scenario"),
        ("工业", "scenario"),
    ):
        if name.lower() in text.lower():
            entities.append(EvidenceEntity(name=name, entity_type=entity_type, evidence_refs=_evidence_ref(_span_for(spans, name))))
    emails = re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)
    entities.extend(EvidenceEntity(name=email, entity_type="contact", confidence="high", evidence_refs=_evidence_ref(_span_for(spans, email))) for email in emails)
    return tuple(_dedupe_entities(entities))


def _topics(text: str) -> tuple[dict[str, Any], ...]:
    values = []
    for topic in (
        "测试测量",
        "产品系列",
        "实验室场景",
        "研发测试",
        "生产测试",
        "工业场景",
        "解决方案",
        "效率提升",
        "易用性",
        "联系方式",
    ):
        if topic.lower() in text.lower():
            values.append({"topic": topic, "weight": 0.8})
    return tuple(values)


def _relations(text: str, spans: tuple[dict[str, Any], ...]) -> tuple[EvidenceRelation, ...]:
    relations = []
    if "GAUSTEK SRI" in text and _contains_any(text, ("产品", "产品系列")):
        relations.append(EvidenceRelation("GAUSTEK SRI", "is_offering_in", "产品体系", evidence_refs=_evidence_ref(_span_for(spans, "GAUSTEK SRI"))))
    if _contains_any(text, ("测试测量", "实验室", "研发测试", "工业场景")):
        relations.append(EvidenceRelation("资料内容", "mentions_scenario", "测试测量/实验/工业场景", evidence_refs=_evidence_ref(_span_for(spans, "测试"))))
    if _contains_any(text, ("让测试更简单", "让实验更高效")):
        relations.append(EvidenceRelation("产品能力", "supports_value", "降低测试复杂度/提升实验效率", evidence_refs=_evidence_ref(_span_for(spans, "让测试更简单"))))
    return tuple(relations)


def _metrics(text: str, spans: tuple[dict[str, Any], ...]) -> tuple[EvidenceMetric, ...]:
    metrics = []
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*(人|年|项|个|台|套|%)", text):
        metrics.append(EvidenceMetric(name="number_mention", value=match.group(1), unit=match.group(2), evidence_refs=_evidence_ref(_span_for(spans, match.group(0)))))
    return tuple(metrics[:20])


def _time_refs(text: str) -> tuple[dict[str, Any], ...]:
    values = []
    for match in re.finditer(r"\b(20\d{2})\b", text):
        values.append({"text": match.group(1), "normalized": match.group(1)})
    return tuple(values[:10])


def _uncertainties(text: str) -> tuple[EvidenceUncertainty, ...]:
    uncertainties = []
    if not _contains_any(text, ("客户", "用户", "案例", "合作")):
        uncertainties.append(EvidenceUncertainty("资料中没有明确客户名单。", "customer_list", "只能推断客户类型，不能回答具体客户名称。"))
    if not _contains_any(text, ("营收", "收入", "订单", "合同")):
        uncertainties.append(EvidenceUncertainty("资料中没有经营规模或收入订单信息。", "business_scale", "无法判断经营规模、增长趋势或商业转化。"))
    if len(text) < 800:
        uncertainties.append(EvidenceUncertainty("当前可用文本较短。", "source_coverage", "认知只适合作为初步画像，需要更多资料交叉验证。"))
    return tuple(uncertainties)


def _document_type(filename: str, source_object_type: str) -> str:
    lower = str(filename or source_object_type or "").lower()
    if lower.endswith(".pdf") or "pdf" in lower:
        return "pdf"
    if lower.endswith(".docx") or "doc" in lower:
        return "document"
    if lower.endswith(".xlsx") or "sheet" in lower:
        return "spreadsheet"
    return source_object_type or "text"


def _content_type(text: str) -> str:
    if _contains_any(text, ("宣传册", "产品手册", "brochure", "product series")):
        return "marketing_or_product_material"
    if _contains_any(text, ("会议", "纪要")):
        return "meeting_note"
    if _contains_any(text, ("审批", "报销", "申请")):
        return "approval_or_process"
    return "general_document"


def _language(text: str) -> str:
    return "zh" if re.search(r"[\u4e00-\u9fff]", text) else "en"


def _extraction_quality(extraction: ExtractionResult, text: str) -> str:
    if not extraction.success and not text:
        return "failed"
    if len(text) >= 2500:
        return "good"
    if len(text) >= 800:
        return "partial"
    return "thin"


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def _dedupe_entities(values: list[EvidenceEntity]) -> list[EvidenceEntity]:
    seen = set()
    result = []
    for item in values:
        key = (item.name, item.entity_type)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
