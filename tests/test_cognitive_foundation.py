from uuid import uuid4

import pytest

from app.models.entities import Snapshot, WorkEvent
from app.services.approval_snapshot_builder import build_approval_snapshot_from_work_event
from app.services.cognitive_foundation import (
    MEMORY_CANDIDATE_STATUS,
    append_cognitive_work_event,
    get_completed_snapshot,
    upsert_snapshot,
    write_memory_candidate,
)
from app.services.runtime_v5 import feishu_resource_providers
from app.services.runtime_v5.feishu_resource_providers import (
    FeishuApprovalProvider,
    _approval_assessment,
    _approval_item,
)


class _WriteDb:
    def __init__(self, scalar_result=None) -> None:
        self.added = []
        self.scalar_result = scalar_result
        self.flush_count = 0

    def add(self, item) -> None:
        self.added.append(item)

    def flush(self) -> None:
        self.flush_count += 1

    def scalar(self, query):
        return self.scalar_result


def test_append_cognitive_work_event_is_append_only() -> None:
    db = _WriteDb()
    company_id = uuid4()

    first = append_cognitive_work_event(
        db,
        company_id=company_id,
        event_type="approval_created",
        object_type="approval",
        object_id="approval-1",
        source="bot_query_discovered",
        actor="ou_user",
        payload={"amount": 100},
    )
    second = append_cognitive_work_event(
        db,
        company_id=company_id,
        event_type="approval_created",
        object_type="approval",
        object_id="approval-1",
        source="bot_query_discovered",
        actor="ou_user",
        payload={"amount": 100},
    )

    assert db.added == [first, second]
    assert first.id != second.id
    assert first.company_id == company_id
    assert first.object_type == "approval"
    assert first.object_id == "approval-1"
    assert first.actor == "ou_user"
    assert first.vector_status == "skipped"


def test_get_completed_snapshot_hides_incomplete_snapshot() -> None:
    pending = Snapshot(
        id=uuid4(),
        company_id=uuid4(),
        object_type="approval",
        object_id="approval-1",
        snapshot_type="approval_current_judgment",
        status="pending_analysis",
    )

    assert get_completed_snapshot(
        _WriteDb(scalar_result=pending),
        company_id=pending.company_id,
        object_type=pending.object_type,
        object_id=pending.object_id,
        snapshot_type=pending.snapshot_type,
    ) is None

    pending.status = "completed"

    assert get_completed_snapshot(
        _WriteDb(scalar_result=pending),
        company_id=pending.company_id,
        object_type=pending.object_type,
        object_id=pending.object_id,
        snapshot_type=pending.snapshot_type,
    ) is pending


def test_upsert_snapshot_creates_and_updates_standard_snapshot() -> None:
    company_id = uuid4()
    create_db = _WriteDb()

    created = upsert_snapshot(
        create_db,
        company_id=company_id,
        object_type="approval",
        object_id="approval-1",
        snapshot_type="approval_current_judgment",
        status="completed",
        summary="差旅报销",
        recommendation="可通过",
        risk_level="pass",
        reasons=["附件完整"],
        source_event_ids=["event-1"],
    )

    assert create_db.added == [created]
    assert created.status == "completed"
    assert created.recommendation == "可通过"
    assert created.risk_level == "pass"
    assert created.reasons == ["附件完整"]
    assert created.source_event_ids == ["event-1"]

    update_db = _WriteDb(scalar_result=created)
    updated = upsert_snapshot(
        update_db,
        company_id=company_id,
        object_type="approval",
        object_id="approval-1",
        snapshot_type="approval_current_judgment",
        status="completed",
        summary="付款申请",
        recommendation="需关注",
        risk_level="review",
        reasons=["供应商名称需核对"],
        source_event_ids=["event-2"],
    )

    assert update_db.added == []
    assert updated is created
    assert updated.summary == "付款申请"
    assert updated.recommendation == "需关注"
    assert updated.source_event_ids == ["event-2"]


def test_write_memory_candidate_only_writes_candidate() -> None:
    db = _WriteDb()
    company_id = uuid4()

    candidate = write_memory_candidate(
        db,
        company_id=company_id,
        memory_type="approval_pattern",
        object_type="approval",
        object_id="approval-1",
        evidence_event_ids=["event-1", "event-2"],
        candidate_text="某供应商多次付款异常",
        confidence=0.72,
    )

    assert db.added == [candidate]
    assert candidate.status == MEMORY_CANDIDATE_STATUS
    assert candidate.company_id == company_id
    assert candidate.evidence_event_ids == ["event-1", "event-2"]
    assert candidate.confidence == 0.72


def test_write_memory_candidate_rejects_invalid_confidence() -> None:
    with pytest.raises(ValueError, match="confidence"):
        write_memory_candidate(
            _WriteDb(),
            company_id=uuid4(),
            memory_type="approval_pattern",
            object_type="approval",
            object_id="approval-1",
            evidence_event_ids=[],
            candidate_text="某员工多次缺附件",
            confidence=1.2,
        )


def test_approval_pending_snapshot_displays_analysis_in_progress() -> None:
    item = _approval_item(
        {
            "instance_code": "approval-1",
            "approval_name": "报销审批",
            "_approval_snapshot": {
                "status": "pending_analysis",
                "recommendation": "分析中",
                "risk_level": "pending",
                "reasons": ["附件或 AI 分析尚未完成"],
            },
        }
    )

    assert item["assessment"]["suggestion"] == "分析中"
    assert item["assessment"]["risk_level"] == "pending"
    assert "尚未完成" in item["assessment"]["reason"]


def test_approval_completed_snapshot_displays_recommendation_and_reasons() -> None:
    assessment = _approval_assessment(
        {
            "_approval_snapshot": {
                "status": "completed",
                "recommendation": "可通过",
                "risk_level": "pass",
                "summary": "附件完整",
                "reasons": ["发票金额与表单一致", "供应商名称匹配"],
            }
        },
        attachment_results=[],
    )

    assert assessment["suggestion"] == "可通过"
    assert assessment["risk_level"] == "pass"
    assert assessment["source"] == "snapshot"
    assert "发票金额与表单一致" in assessment["reason"]


def test_approval_query_completed_snapshot_skips_realtime_analysis(monkeypatch) -> None:
    company_id = uuid4()
    snapshot = Snapshot(
        id=uuid4(),
        company_id=company_id,
        object_type="approval",
        object_id="approval-1",
        snapshot_type="approval_current_judgment",
        status="completed",
        recommendation="可通过",
        risk_level="pass",
        reasons=["已有完成快照"],
    )
    db = _WriteDb(scalar_result=snapshot)
    provider = FeishuApprovalProvider(db=db)
    request = _approval_request(company_id)
    raw_item = {"instance_code": "approval-1", "approval_name": "报销审批"}
    monkeypatch.setattr(feishu_resource_providers, "_active_feishu_app_config", lambda *args, **kwargs: None)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("completed snapshot should skip realtime analysis")

    provider._fetch_approval_instance_detail = fail_if_called
    provider._read_approval_attachments = fail_if_called

    provider._enrich_approval_list_items(request, [raw_item])

    assert raw_item["_approval_assessment"]["source"] == "snapshot"
    assert raw_item["_approval_assessment"]["suggestion"] == "可通过"
    assert raw_item["_approval_llm_ms"] == 0


def test_approval_query_missing_snapshot_does_not_enqueue_builder_or_realtime_analysis(monkeypatch) -> None:
    company_id = uuid4()
    db = _WriteDb()
    provider = FeishuApprovalProvider(db=db)
    request = _approval_request(company_id)
    raw_item = {"instance_code": "approval-1", "approval_name": "报销审批"}
    monkeypatch.setattr(feishu_resource_providers, "_active_feishu_app_config", lambda *args, **kwargs: None)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("bot query must not run realtime approval analysis")

    provider._fetch_approval_instance_detail = fail_if_called
    provider._read_approval_attachments = fail_if_called
    provider._write_approval_analysis_snapshot = fail_if_called

    provider._enrich_approval_list_items(request, [raw_item])

    assert db.added == []
    assert raw_item["_approval_snapshot"]["status"] == "pending_analysis"
    assert raw_item["_approval_assessment"]["suggestion"] == "分析中"
    assert raw_item["_approval_llm_ms"] == 0
    assert not raw_item.get("_approval_snapshot_builder_enqueued")


def test_approval_snapshot_builder_skips_approval_created_event() -> None:
    event = WorkEvent(
        id=uuid4(),
        company_id=uuid4(),
        event_type="approval_created",
        object_type="approval",
        object_id="approval-1",
        payload={"item": {"instance_code": "approval-1"}},
    )

    assert build_approval_snapshot_from_work_event(_WriteDb(), event) == {
        "ok": True,
        "status": "skipped_non_trigger",
        "event_id": str(event.id),
    }


def test_approval_analysis_completed_writes_snapshot() -> None:
    company_id = uuid4()
    db = _WriteDb()
    provider = FeishuApprovalProvider(db=db)
    request = _approval_request(company_id)
    raw_item = {
        "instance_code": "approval-1",
        "approval_name": "报销审批",
        "_approval_event_ids": ["event-created", "event-attachment"],
    }

    provider._write_approval_analysis_snapshot(
        request,
        raw_item,
        assessment={
            "suggestion": "需关注",
            "reason": "供应商名称需核对",
            "detailed_reason": "供应商名称和发票抬头不完全一致",
        },
    )

    event = db.added[0]
    snapshot = db.added[1]
    assert event.event_type == "approval_analysis_completed"
    assert event.object_type == "approval"
    assert event.object_id == "approval-1"
    assert snapshot.status == "completed"
    assert snapshot.snapshot_type == "approval_current_judgment"
    assert snapshot.recommendation == "需关注"
    assert snapshot.risk_level == "review"
    assert snapshot.payload == {
        "assessment": {
            "suggestion": "需关注",
            "reason": "供应商名称需核对",
            "detailed_reason": "供应商名称和发票抬头不完全一致",
        }
    }
    assert raw_item["_approval_snapshot"]["status"] == "completed"


def test_approval_pending_snapshot_only_stores_cognitive_state() -> None:
    company_id = uuid4()
    db = _WriteDb()
    provider = FeishuApprovalProvider(db=db)
    request = _approval_request(company_id)
    raw_item = {
        "instance_code": "approval-1",
        "approval_name": "报销审批",
        "status": "PENDING",
        "serial_number": "202606210001",
    }

    provider._write_approval_pending_snapshot(request, raw_item)

    snapshot = db.added[0]
    assert snapshot.status == "pending_analysis"
    assert snapshot.recommendation == "分析中"
    assert snapshot.payload == {"cognitive_state": "pending_analysis"}
    assert "status" not in snapshot.payload
    assert "serial_number" not in snapshot.payload


def _approval_request(company_id):
    class _Identity:
        open_id = "ou_user"

    class _Scope:
        active_company_id = company_id

    class _Context:
        identity = _Identity()
        runtime_scope = _Scope()
        user_id = "user_1"

    return type("Request", (), {"context": _Context()})()
