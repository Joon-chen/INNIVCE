from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import FeishuAppConfig
from app.services.cognitive_foundation import append_cognitive_work_event, get_completed_snapshot, upsert_snapshot
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
    if get_completed_snapshot(
        db,
        company_id=company_uuid,
        object_type="approval",
        object_id=object_id,
        snapshot_type=APPROVAL_SNAPSHOT_TYPE,
    ):
        return {"ok": True, "status": "skipped_completed", "object_id": object_id}

    app_config = _active_feishu_app_config(db, company_uuid)
    if app_config is None:
        return {"ok": False, "error": "missing_feishu_app_config", "object_id": object_id}

    running_snapshot = upsert_snapshot(
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
        upsert_snapshot(
            db,
            company_id=company_uuid,
            object_type="approval",
            object_id=object_id,
            snapshot_type=APPROVAL_SNAPSHOT_TYPE,
            status="pending_analysis",
            summary="审批附件仍在分析中。",
            recommendation="分析中",
            risk_level="pending",
            reasons=["附件或 AI 分析尚未完成"],
            source_event_ids=[],
            payload={"cognitive_state": "pending_analysis"},
        )
        return {"ok": True, "status": "pending_attachment", "object_id": object_id}
    source_event_ids: list[str] = []
    if refs:
        attachment_event = append_cognitive_work_event(
            db,
            company_id=company_uuid,
            event_type="attachment_processed",
            object_type="approval",
            object_id=object_id,
            source="approval_snapshot_builder",
            actor="system",
            payload={"attachments": [_attachment_result_payload(result) for result in attachment_results]},
        )
        source_event_ids.append(str(attachment_event.id))

    llm_decision = _approval_llm_decision(raw_item, attachment_results=attachment_results)
    if llm_decision:
        raw_item["_approval_llm_decision"] = llm_decision
    assessment = _approval_assessment(raw_item, attachment_results=attachment_results)
    analysis_event = append_cognitive_work_event(
        db,
        company_id=company_uuid,
        event_type="approval_analysis_completed",
        object_type="approval",
        object_id=object_id,
        source="approval_snapshot_builder",
        actor=actor or "system",
        payload={"assessment": assessment},
    )
    source_event_ids.append(str(analysis_event.id))
    upsert_snapshot(
        db,
        company_id=company_uuid,
        object_type="approval",
        object_id=object_id,
        snapshot_type=APPROVAL_SNAPSHOT_TYPE,
        status="completed",
        summary=str(assessment.get("reason") or assessment.get("detailed_reason") or ""),
        recommendation=str(assessment.get("suggestion") or ""),
        risk_level=_approval_snapshot_risk_level(assessment),
        reasons=_approval_snapshot_reasons(assessment),
        source_event_ids=source_event_ids,
        payload={"assessment": assessment},
    )
    return {"ok": True, "status": "completed", "object_id": object_id, "previous_snapshot_id": str(running_snapshot.id)}


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


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(coro)).result()
