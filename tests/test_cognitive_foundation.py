from uuid import uuid4

import pytest

from app.models.entities import Snapshot
from app.services.cognitive_foundation import (
    MEMORY_CANDIDATE_STATUS,
    append_cognitive_work_event,
    get_completed_snapshot,
    upsert_snapshot,
    write_memory_candidate,
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
