from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import FeishuAppConfig, WorkEvent
from app.services.approval_evidence import build_approval_expense_evidence
from app.services.cognitive_foundation import append_cognitive_work_event, get_completed_snapshot, upsert_snapshot
from app.services.evidence import evidence_payload
from app.services.feishu import approval_formatters
from app.services.feishu.approval import FeishuApprovalService, approval_attachment_refs
from app.services.feishu.approval_attachments import FeishuApprovalAttachmentService
from app.services.runtime_v5.feishu_resource_providers import (
    _approval_assessment,
    _approval_llm_decision,
    _approval_snapshot_reasons,
    _approval_snapshot_risk_level,
    _attachment_result_payload,
    _feishu_response_data,
)

APPROVAL_SNAPSHOT_TYPE = "approval_current_judgment"
APPROVAL_TRIGGER_EVENT_TYPES = ("attachment_processed", "approval_form_changed", "approval_attachment_changed")


def build_approval_snapshot(
    db: Session,
    *,
    company_id: str | UUID,
    item: dict[str, Any],
    actor: str = "system",
) -> dict[str, Any]:
    company_uuid = company_id if isinstance(company_id, UUID) else UUID(str(company_id))
    object_id = approval_formatters.approval_instance_code(item)
    if not object_id:
        return {"ok": False, "error": "missing_approval_object_id"}
    completed = get_completed_snapshot(
        db,
        company_id=company_uuid,
        object_type="approval",
        object_id=object_id,
        snapshot_type=APPROVAL_SNAPSHOT_TYPE,
    )
    if completed:
        return {"ok": True, "status": "skipped_completed", "object_id": object_id}

    app_config = _active_feishu_app_config(db, company_uuid)
    if app_config is None:
        return {"ok": False, "error": "missing_feishu_app_config", "object_id": object_id}

    upsert_snapshot(
        db,
        company_id=company_uuid,
        object_type="approval",
        object_id=object_id,
        snapshot_type=APPROVAL_SNAPSHOT_TYPE,
        status="analysis_running",
        summary="审批附件正在分析中。",
        recommendation="分析中",
        risk_level="pending",
        reasons=["附件或 AI 分析尚未完成"],
        source_event_ids=[],
        payload={"cognitive_state": "analysis_running"},
    )
    db.flush()

    raw_item = dict(item)
    detail = raw_item.get("instance_detail") if isinstance(raw_item.get("instance_detail"), dict) else {}
    if not detail:
        detail = _fetch_approval_detail(app_config, object_id)
        if detail:
            raw_item["instance_detail"] = detail
    refs = approval_attachment_refs(detail.get("form") if isinstance(detail, dict) else None)
    attachment_results = _read_approval_attachments(app_config, raw_item, refs=refs) if refs else []
    if refs and not attachment_results:
        return {"ok": True, "status": "pending_attachment", "object_id": object_id}
    attachment_event = append_cognitive_work_event(
        db,
        company_id=company_uuid,
        event_type="attachment_processed",
        object_type="approval",
        object_id=object_id,
        source="approval_snapshot_builder",
        actor="system",
        payload={
            "item": item,
            "attachments": [_attachment_result_payload(result) for result in attachment_results],
        },
    )
    return build_approval_snapshot_from_work_event(db, attachment_event, actor=actor)


def build_approval_snapshot_from_work_event(db: Session, event: WorkEvent, *, actor: str = "system") -> dict[str, Any]:
    if event.object_type != "approval" or event.event_type not in APPROVAL_TRIGGER_EVENT_TYPES:
        return {"ok": True, "status": "skipped_non_trigger", "event_id": str(event.id)}
    snapshot = get_completed_snapshot(
        db,
        company_id=event.company_id,
        object_type="approval",
        object_id=event.object_id,
        snapshot_type=APPROVAL_SNAPSHOT_TYPE,
    )
    if snapshot is not None and _snapshot_has_evidence(snapshot) and str(event.id) in set(snapshot.source_event_ids or []):
        return {"ok": True, "status": "skipped_completed", "object_id": event.object_id, "event_id": str(event.id)}
    if snapshot is not None and getattr(snapshot, "updated_at", None) and getattr(event, "created_at", None):
        if _snapshot_has_evidence(snapshot) and snapshot.updated_at >= event.created_at:
            return {"ok": True, "status": "skipped_current_snapshot", "object_id": event.object_id, "event_id": str(event.id)}
    app_config = _active_feishu_app_config(db, event.company_id)
    if app_config is None:
        return {"ok": False, "error": "missing_feishu_app_config", "object_id": event.object_id}
    payload = event.payload if isinstance(event.payload, dict) else {}
    raw_item = dict(payload.get("item")) if isinstance(payload.get("item"), dict) else {"instance_code": event.object_id}
    raw_item.setdefault("instance_code", event.object_id)
    detail = raw_item.get("instance_detail") if isinstance(raw_item.get("instance_detail"), dict) else {}
    if not detail:
        detail = _fetch_approval_detail(app_config, event.object_id)
        if detail:
            raw_item["instance_detail"] = detail
    attachment_results = _attachment_results_from_payload(payload)
    evidence = build_approval_expense_evidence(
        raw_item,
        attachment_results=attachment_results,
        source_event_ids=[str(event.id)],
    )
    evidence_event = append_cognitive_work_event(
        db,
        company_id=event.company_id,
        event_type="approval_expense_evidence_completed",
        object_type="approval",
        object_id=event.object_id,
        source="approval_evidence_builder",
        actor=actor or "system",
        payload={"evidence": evidence_payload(evidence)},
    )
    llm_decision = _approval_llm_decision(raw_item, attachment_results=attachment_results)
    if llm_decision:
        raw_item["_approval_llm_decision"] = llm_decision
    assessment = _approval_assessment(raw_item, attachment_results=attachment_results)
    assessment = _assessment_from_evidence(assessment, evidence=evidence)
    analysis_event = append_cognitive_work_event(
        db,
        company_id=event.company_id,
        event_type="approval_analysis_completed",
        object_type="approval",
        object_id=event.object_id,
        source="approval_snapshot_builder",
        actor=actor or "system",
        payload={"assessment": assessment, "evidence": evidence_payload(evidence)},
    )
    source_event_ids = [str(event.id), str(evidence_event.id), str(analysis_event.id)]
    upsert_snapshot(
        db,
        company_id=event.company_id,
        object_type="approval",
        object_id=event.object_id,
        snapshot_type=APPROVAL_SNAPSHOT_TYPE,
        status="completed",
        summary=str(assessment.get("reason") or assessment.get("detailed_reason") or ""),
        recommendation=str(assessment.get("suggestion") or ""),
        risk_level=_approval_snapshot_risk_level(assessment),
        reasons=_approval_snapshot_reasons(assessment),
        source_event_ids=source_event_ids,
        payload={"assessment": assessment, "evidence": evidence_payload(evidence)},
    )
    return {"ok": True, "status": "completed", "object_id": event.object_id, "event_id": str(event.id)}


def _snapshot_has_evidence(snapshot) -> bool:
    payload = snapshot.payload if isinstance(getattr(snapshot, "payload", None), dict) else {}
    evidence = payload.get("evidence")
    if not isinstance(evidence, dict) or not evidence:
        return False
    facts = evidence.get("facts") if isinstance(evidence.get("facts"), dict) else {}
    return "expense_row_count" in facts and isinstance(facts.get("attachments"), list)


def build_approval_snapshots_from_work_events(db: Session, *, limit: int = 10) -> dict[str, Any]:
    candidate_limit = max(limit * 10, 50)
    events = list(
        db.scalars(
            select(WorkEvent)
            .where(WorkEvent.object_type == "approval")
            .where(WorkEvent.event_type.in_(APPROVAL_TRIGGER_EVENT_TYPES))
            .order_by(WorkEvent.created_at.asc())
            .limit(candidate_limit)
        ).all()
    )
    completed = 0
    skipped = 0
    errors: list[str] = []
    for event in events:
        if completed >= limit:
            break
        result = build_approval_snapshot_from_work_event(db, event, actor="system")
        if result.get("status") == "completed":
            completed += 1
        elif result.get("ok"):
            skipped += 1
        else:
            errors.append(str(result.get("error") or "approval_snapshot_build_failed")[:300])
    return {"count": len(events), "completed": completed, "skipped": skipped, "errors": errors[:5]}


def _active_feishu_app_config(db: Session, company_id: UUID) -> FeishuAppConfig | None:
    return db.scalar(
        select(FeishuAppConfig)
        .where(FeishuAppConfig.company_id == company_id)
        .where(FeishuAppConfig.is_active.is_(True))
    )


def _fetch_approval_detail(app_config: FeishuAppConfig, instance_code: str) -> dict[str, Any]:
    try:
        payload = _run_async(
            FeishuApprovalService(app_config).get_instance(
                instance_code=instance_code,
                user_id_type="open_id",
            )
        )
    except Exception:
        return {}
    data = _feishu_response_data(payload)
    return data if isinstance(data, dict) else {}


def _read_approval_attachments(app_config: FeishuAppConfig, raw_item: dict[str, Any], *, refs: list[dict[str, Any]]) -> list[Any]:
    try:
        return _run_async(
            FeishuApprovalAttachmentService(app_config).read_attachment_refs(
                refs,
                instance_code=approval_formatters.approval_instance_code(raw_item),
                max_files=3,
            )
        )
    except Exception:
        return []


def _attachment_results_from_payload(payload: dict[str, Any]) -> list[Any]:
    attachments = payload.get("attachments") if isinstance(payload.get("attachments"), list) else []
    results: list[Any] = []
    for item in attachments:
        if not isinstance(item, dict):
            continue
        results.append(
            SimpleNamespace(
                name=str(item.get("name") or ""),
                token=str(item.get("token") or ""),
                text_preview=str(item.get("text_preview") or ""),
                error=str(item.get("error") or ""),
                fetched=bool(item.get("text_preview") or item.get("error")),
            )
        )
    return results


def _assessment_from_evidence(assessment: dict[str, Any], *, evidence) -> dict[str, Any]:
    if not evidence.manager_summary:
        return assessment
    recommendation = str(assessment.get("suggestion") or "").strip()
    if evidence.quality in {"partial", "failed"}:
        recommendation = "补充后再审"
    elif evidence.quality == "complete" and recommendation in {"", "建议先核对", "需补充核对", "补充后再审"}:
        recommendation = "可通过"
    detailed_reason = evidence.manager_summary
    if evidence.missing:
        detailed_reason = f"{detailed_reason}缺失证据：{'、'.join(evidence.missing)}。"
    if evidence.conflicts:
        detailed_reason = f"{detailed_reason}证据冲突：{'、'.join(evidence.conflicts)}。"
    return {
        **assessment,
        "suggestion": recommendation or "需关注",
        "reason": evidence.manager_summary,
        "detailed_reason": detailed_reason,
        "missing_evidence": list(evidence.missing),
        "evidence_quality": evidence.quality,
        "suggested_manager_action": evidence.suggested_next_step,
        "source": "evidence",
    }


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(coro)).result()
