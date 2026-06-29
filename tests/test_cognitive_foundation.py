from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.models.entities import Snapshot, WorkEvent
from app.services.approval_snapshot_builder import build_approval_snapshot_from_work_event
from app.services.cognitive_foundation import (
    MEMORY_CANDIDATE_STATUS,
    append_workspace_cognitive_event,
    append_workspace_cognitive_observations,
    build_workspace_aggregation_from_visible_events,
    append_cognitive_work_event,
    build_workspace_aggregation_summary,
    build_workspace_cognitive_projection,
    get_completed_snapshot,
    upsert_snapshot,
    write_memory_candidate,
)
from app.services.cognitive_foundation_v1 import (
    COMPANY_PROFILE_EXTRACTOR,
    COMPANY_PROFILE_SNAPSHOT_TYPE,
    EVIDENCE_PAYLOAD_VERSION,
    EvidenceInput,
    SNAPSHOT_STATUS_ACTIVE,
    append_evidence_work_event,
    build_company_profile_snapshot,
    build_evidence_payload,
    company_profile_snapshot_answer,
    company_profile_snapshot_item,
    default_extractor_registry,
)
from app.services.runtime_v5 import feishu_resource_providers
from app.services.runtime_v5.feishu_resource_providers import (
    FeishuApprovalProvider,
    _approval_assessment,
    _approval_item,
    _approval_snapshot_risk_level,
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


def test_workspace_cognitive_projection_keeps_aggregate_fields_not_raw_detail() -> None:
    projection = build_workspace_cognitive_projection(
        object_type="task",
        raw_payload={
            "task_id": "task-1",
            "title": "出差西安",
            "status": "todo",
            "due_at": "2026-06-24T10:00:00+08:00",
            "description": "客户和报价细节",
            "attachments": [{"name": "报价单.pdf"}],
        },
        owner_user_id="user-1",
        owner_open_id="ou_1",
        owner_department_id="dept-1",
    )

    assert projection["operational_detail_stored"] is False
    assert projection["raw_detail_allowed"] is False
    assert projection["cognitive_fields"]["task_id"] == "task-1"
    assert projection["cognitive_fields"]["title"] == "出差西安"
    assert projection["cognitive_fields"]["owner_user_id"] == "user-1"
    assert "description" not in projection["cognitive_fields"]
    assert "attachments" not in projection["cognitive_fields"]
    assert projection["redacted_fields"] == ["attachments", "description"]
    assert "overdue_count" in projection["aggregate_surfaces"]["department"]


def test_append_workspace_cognitive_event_inherits_owner_visibility() -> None:
    db = _WriteDb()
    company_id = uuid4()

    event = append_workspace_cognitive_event(
        db,
        company_id=company_id,
        object_type="calendar",
        object_id="event-1",
        source="feishu_user_sync",
        actor="ou_1",
        raw_payload={
            "event_id": "event-1",
            "title": "销售会",
            "start_at": "2026-06-23T14:00:00+08:00",
            "end_at": "2026-06-23T15:00:00+08:00",
            "description": "客户细节",
            "attendees": ["ou_2", "ou_3"],
        },
        owner_user_id="user-1",
        owner_open_id="ou_1",
        owner_department_id="dept-1",
        visibility_scope="self",
    )

    assert db.added == [event]
    assert event.event_type == "workspace_calendar_observed"
    assert event.visibility_scope == "self"
    assert event.data_classification == "workspace_cognitive"
    assert event.allowed_user_ids == ["user-1"]
    assert event.allowed_departments == ["dept-1"]
    assert event.payload["cognitive_fields"]["event_id"] == "event-1"
    assert "description" not in event.payload["cognitive_fields"]
    assert "attendees" in event.payload["redacted_fields"]


def test_workspace_aggregation_summary_outputs_v0_management_metrics_only() -> None:
    db = _WriteDb()
    company_id = uuid4()

    events = [
        append_workspace_cognitive_event(
            db,
            company_id=company_id,
            object_type="task",
            object_id="task-1",
            source="feishu_user_sync",
            actor="ou_1",
            raw_payload={"task_id": "task-1", "title": "逾期任务", "status": "todo", "due_at": "2026-06-20T10:00:00+00:00"},
            owner_user_id="user-1",
            owner_open_id="ou_1",
            owner_department_id="dept-1",
        ),
        append_workspace_cognitive_event(
            db,
            company_id=company_id,
            object_type="task",
            object_id="task-2",
            source="feishu_user_sync",
            actor="ou_2",
            raw_payload={"task_id": "task-2", "title": "本周到期", "status": "todo", "due_at": "2026-06-25T10:00:00+00:00"},
            owner_user_id="user-2",
            owner_open_id="ou_2",
            owner_department_id="dept-1",
        ),
        append_workspace_cognitive_event(
            db,
            company_id=company_id,
            object_type="calendar",
            object_id="event-1",
            source="feishu_user_sync",
            actor="ou_1",
            raw_payload={
                "event_id": "event-1",
                "title": "冲突会议",
                "start_at": "2026-06-23T02:00:00+00:00",
                "end_at": "2026-06-23T03:00:00+00:00",
                "is_conflict": True,
                "is_busy": True,
            },
            owner_user_id="user-1",
            owner_open_id="ou_1",
            owner_department_id="dept-1",
        ),
        append_workspace_cognitive_event(
            db,
            company_id=company_id,
            object_type="calendar",
            object_id="event-2",
            source="feishu_user_sync",
            actor="ou_2",
            raw_payload={
                "event_id": "event-2",
                "title": "普通会议",
                "start_at": "2026-06-23T04:00:00+00:00",
                "end_at": "2026-06-23T05:30:00+00:00",
                "is_busy": True,
            },
            owner_user_id="user-2",
            owner_open_id="ou_2",
            owner_department_id="dept-1",
        ),
    ]

    summary = build_workspace_aggregation_summary(
        events,
        scope="department",
        now=events[0].occurred_at.replace(year=2026, month=6, day=23, hour=0, minute=0),
    )

    assert summary["detail_available"] is False
    assert summary["scope"] == "department"
    assert summary["metrics"]["task_total"] == 2
    assert summary["metrics"]["overdue_task_count"] == 1
    assert summary["metrics"]["due_soon_task_count"] == 1
    assert summary["metrics"]["calendar_conflict_count"] == 1
    assert summary["metrics"]["meeting_occupied_minutes"] == 150
    assert summary["metrics"]["workload_buckets"]["at_risk"] == 1
    assert summary["metrics"]["workload_buckets"]["normal"] == 1
    assert summary["policy_notes"] == [
        "aggregation_only",
        "operational_detail_not_included",
        "source_visibility_must_be_filtered_before_display",
    ]


def test_workspace_aggregation_uses_latest_projection_per_object() -> None:
    db = _WriteDb()
    company_id = uuid4()

    append_workspace_cognitive_event(
        db,
        company_id=company_id,
        object_type="task",
        object_id="task-1",
        source="feishu_user_sync",
        actor="ou_1",
        raw_payload={"task_guid": "task-1", "title": "旧状态", "status": "todo", "due_at": "2026-06-20T10:00:00+00:00"},
        owner_user_id="user-1",
        owner_open_id="ou_1",
        owner_department_id="dept-1",
    )
    latest = append_workspace_cognitive_event(
        db,
        company_id=company_id,
        object_type="task",
        object_id="task-1",
        source="feishu_user_sync",
        actor="ou_1",
        raw_payload={"task_guid": "task-1", "title": "新状态", "status": "completed", "completed_at": "2026-06-23T10:00:00+00:00"},
        owner_user_id="user-1",
        owner_open_id="ou_1",
        owner_department_id="dept-1",
    )
    latest.occurred_at = latest.occurred_at.replace(year=2026, month=6, day=23, hour=10)

    summary = build_workspace_aggregation_summary(
        tuple(db.added),
        scope="department",
        now=latest.occurred_at.replace(hour=12),
    )

    assert summary["source_event_count"] == 2
    assert summary["metrics"]["task_total"] == 0
    assert summary["metrics"]["overdue_task_count"] == 0


def test_workspace_aggregation_treats_epoch_completed_at_as_unfinished() -> None:
    db = _WriteDb()
    company_id = uuid4()

    event = append_workspace_cognitive_event(
        db,
        company_id=company_id,
        object_type="task",
        object_id="task-1",
        source="feishu_user_sync",
        actor="ou_1",
        raw_payload={
            "task_guid": "task-1",
            "title": "待完成任务",
            "status": "todo",
            "completed_at": "1970-01-01T00:00:00+00:00",
        },
        owner_user_id="user-1",
        owner_open_id="ou_1",
        owner_department_id="dept-1",
    )

    summary = build_workspace_aggregation_summary(
        tuple(db.added),
        scope="department",
        now=event.occurred_at.replace(year=2026, month=6, day=23, hour=12),
    )

    assert summary["metrics"]["task_total"] == 1
    assert summary["metrics"]["overdue_task_count"] == 0


def test_workspace_cognitive_sync_acceptance_writes_projection_then_aggregates() -> None:
    db = _WriteDb()
    company_id = uuid4()

    task_events = append_workspace_cognitive_observations(
        db,
        company_id=company_id,
        object_type="task",
        actor="ou_1",
        owner_user_id="user-1",
        owner_open_id="ou_1",
        owner_department_id="dept-1",
        raw_items=[
            {
                "task_guid": "task-guid-1",
                "title": "跟进合同",
                "status": "todo",
                "due_at": "2026-06-20T10:00:00+00:00",
                "description": "完整客户合同细节不应进入认知投影",
            },
        ],
    )
    calendar_events = append_workspace_cognitive_observations(
        db,
        company_id=company_id,
        object_type="calendar",
        actor="ou_1",
        owner_user_id="user-1",
        owner_open_id="ou_1",
        owner_department_id="dept-1",
        raw_items=[
            {
                "event_id": "event-1",
                "title": "客户会议",
                "start_at": "2026-06-23T02:00:00+00:00",
                "end_at": "2026-06-23T03:00:00+00:00",
                "is_conflict": True,
                "attendees": ["ou_2", "ou_3"],
            },
        ],
    )

    events = task_events + calendar_events
    summary = build_workspace_aggregation_from_visible_events(
        events,
        scope="department",
        now=events[0].occurred_at.replace(year=2026, month=6, day=23, hour=0, minute=0),
    )

    assert len(events) == 2
    assert events[0].event_type == "workspace_task_observed"
    assert events[1].event_type == "workspace_calendar_observed"
    assert events[0].data_classification == "workspace_cognitive"
    assert events[0].allowed_user_ids == ["user-1"]
    assert events[0].allowed_departments == ["dept-1"]
    assert events[0].payload["operational_detail_stored"] is False
    assert "description" not in events[0].payload["cognitive_fields"]
    assert "attendees" not in events[1].payload["cognitive_fields"]
    assert summary["source"] == "workspace_cognitive_projection"
    assert summary["detail_available"] is False
    assert summary["operational_detail_available"] is False
    assert summary["workevent_as_realtime_source"] is False
    assert summary["metrics"]["task_total"] == 1
    assert summary["metrics"]["overdue_task_count"] == 1
    assert summary["metrics"]["calendar_conflict_count"] == 1


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


def test_cognitive_v1_evidence_payload_is_traceable_contract() -> None:
    company_id = uuid4()
    events_now = datetime.now(UTC)
    evidence = EvidenceInput(
        source_system="registered_resource",
        source_object_id="file_pdf",
        organization_binding={"company_id": str(company_id), "object_type": "company", "object_id": str(company_id)},
        visibility_binding={"scope": "company", "data_classification": "company"},
        timestamp=events_now,
        extractor=COMPANY_PROFILE_EXTRACTOR,
        summary="固势宣传册 公司介绍 全系列产品手册 让测试更简单 让实验更高效 Business@gaustek.com",
        metadata={"title": "固势宣传册26--中文.pdf", "document_type": "file"},
    )

    payload = build_evidence_payload(evidence)

    assert payload["payload_version"] == EVIDENCE_PAYLOAD_VERSION
    assert payload["source_system"] == "registered_resource"
    assert payload["source_object_id"] == "file_pdf"
    assert payload["organization_binding"]["company_id"] == str(company_id)
    assert payload["visibility_binding"]["scope"] == "company"
    assert payload["timestamp"] == events_now.isoformat()
    assert payload["extractor"] == COMPANY_PROFILE_EXTRACTOR
    assert "固势宣传册" in payload["summary"]


def test_cognitive_v1_company_profile_snapshot_builder_uses_evidence_carrier() -> None:
    db = _WriteDb()
    company_id = uuid4()
    evidence = EvidenceInput(
        source_system="registered_resource",
        source_object_id="file_pdf",
        organization_binding={"company_id": str(company_id), "object_type": "company", "object_id": str(company_id)},
        visibility_binding={"scope": "company", "data_classification": "company"},
        timestamp=datetime.now(UTC),
        extractor=COMPANY_PROFILE_EXTRACTOR,
        summary=(
            "固势（苏州）科技有限公司 GAUSTEK SRI 全系列产品手册 "
            "让测试更简单 让实验更高效 PRODUCT SERIES 产品系列 Business@gaustek.com"
        ),
        metadata={"title": "固势宣传册26--中文.pdf"},
    )

    event = append_evidence_work_event(
        db,
        company_id=company_id,
        evidence=evidence,
        object_type="company_profile",
        object_id=str(company_id),
        actor="ou_test",
    )
    snapshot = build_company_profile_snapshot(db, company_id=company_id, evidence_events=(event,), object_id=str(company_id))

    assert event.event_type == "evidence.company_profile.observed"
    assert event.payload["payload_version"] == EVIDENCE_PAYLOAD_VERSION
    assert snapshot is not None
    assert snapshot.snapshot_type == COMPANY_PROFILE_SNAPSHOT_TYPE
    assert snapshot.payload["snapshot_version"] == COMPANY_PROFILE_SNAPSHOT_TYPE
    assert snapshot.payload["version"] == 1
    assert snapshot.payload["schema_version"] == "snapshot_v1_1"
    assert snapshot.payload["snapshot_status"] == SNAPSHOT_STATUS_ACTIVE
    assert snapshot.payload["structured"]["products"] == ["GAUSTEK SRI 全系列产品", "测试测量产品系列"]
    assert "产品体系" in snapshot.payload["understanding"]
    assert snapshot.payload["evidence_refs"] == [{"work_event_id": str(event.id)}]
    assert snapshot.payload["derived_from"]["extractors"] == [COMPANY_PROFILE_EXTRACTOR]
    assert snapshot.payload["derived_from"]["evidence_refs"] == [{"work_event_id": str(event.id)}]
    assert snapshot.payload["derived_from"]["previous_snapshot_version"] is None
    item = company_profile_snapshot_item(snapshot)
    assert item["evidence_refs"] == [str(event.id)]
    assert item["snapshot_status"] == SNAPSHOT_STATUS_ACTIVE
    assert item["version"] == 1
    assert item["structured"]["products"] == ["GAUSTEK SRI 全系列产品", "测试测量产品系列"]
    assert "测试测量" in company_profile_snapshot_answer(item, query="公司是做什么的")
    assert "GAUSTEK SRI 全系列产品" in company_profile_snapshot_answer(item, query="公司有哪些产品")


def test_cognitive_v1_extractor_registry_is_by_cognitive_object() -> None:
    extractor = default_extractor_registry().get("company_profile")

    assert extractor.object_type == "company_profile"


def test_cognitive_v1_company_profile_contact_extraction_avoids_address_as_phone() -> None:
    db = _WriteDb()
    company_id = uuid4()
    evidence = EvidenceInput(
        source_system="registered_resource",
        source_object_id="file_pdf",
        organization_binding={"company_id": str(company_id), "object_type": "company", "object_id": str(company_id)},
        visibility_binding={"scope": "company", "data_classification": "company"},
        timestamp=datetime.now(UTC),
        extractor=COMPANY_PROFILE_EXTRACTOR,
        summary=(
            "固势（苏州）科技有限公司 & 0512 - 69176883 "
            "QBN LAUER 2073 207 Xingpu Road, Suzhou Industrial Park CONTENTS 目录"
        ),
        metadata={"title": "固势宣传册26--中文.pdf"},
    )
    event = append_evidence_work_event(
        db,
        company_id=company_id,
        evidence=evidence,
        object_type="company_profile",
        object_id=str(company_id),
        actor="ou_test",
    )
    snapshot = build_company_profile_snapshot(db, company_id=company_id, evidence_events=(event,), object_id=str(company_id))
    item = company_profile_snapshot_item(snapshot)

    answer = company_profile_snapshot_answer(item, query="联系方式")

    assert "2073 207" not in answer
    assert "207 Xingpu Road, Suzhou Industrial Park" in answer
    assert "CONTENTS" not in answer


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


def test_approval_item_preserves_portal_detail_identity_fields() -> None:
    item = _approval_item(
        {
            "task_id": "task-1",
            "instance_code": "instance-1",
            "approval_code": "approval-1",
            "approval_name": "费用报销",
            "applicant_name": "任佳乐",
            "instance_detail": {
                "serial_number": "202606150009",
                "form": '[{"name":"费用汇总","value":8902.33},{"name":"报销事由","value":"出差打车"}]',
            },
            "_approval_snapshot": {
                "status": "completed",
                "recommendation": "需关注",
                "risk_level": "review",
                "reasons": ["费用明细需核对"],
            },
        }
    )

    assert item["title"] == "费用报销"
    assert item["applicant_name"] == "任佳乐"
    assert item["amount"] == 8902.33
    assert item["form_amount"] == 8902.33
    assert item["serial_number"] == "202606150009"
    assert item["instance_code"] == "instance-1"
    assert item["process_code"] == "instance-1"
    assert item["approval_code"] == "approval-1"
    assert item["task_id"] == "task-1"
    assert item["raw"]["instance_detail"]["serial_number"] == "202606150009"


def test_approval_item_does_not_use_open_id_as_applicant_display_name() -> None:
    item = _approval_item(
        {
            "instance_code": "instance-1",
            "approval_name": "费用报销",
            "instance_detail": {
                "applicant": {"open_id": "ou_6d06c92f4dab6f8626735b94f2d55bd2"},
                "form": '[{"name":"费用汇总","value":8902.33}]',
            },
        }
    )

    assert item["applicant_name"] == ""
    assert item["applicant"] == ""


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


def test_approval_snapshot_risk_level_treats_missing_parse_as_review() -> None:
    risk_level = _approval_snapshot_risk_level(
        {
            "suggestion": "补充后再审",
            "detailed_reason": "费用明细字段格式异常，无法准确判断费用项目；建议申请人补充清晰完整的费用明细。",
        }
    )

    assert risk_level == "review"


def test_approval_snapshot_risk_level_keeps_strong_risk_as_high() -> None:
    risk_level = _approval_snapshot_risk_level(
        {
            "suggestion": "补充后再审",
            "detailed_reason": "无票据支撑，存在虚假报销或费用归属不清风险。",
        }
    )

    assert risk_level == "high"


def test_approval_assessment_normalizes_legacy_high_snapshot_without_strong_signal() -> None:
    assessment = _approval_assessment(
        {
            "_approval_snapshot": {
                "status": "completed",
                "recommendation": "补充后再审",
                "risk_level": "high",
                "reasons": ["费用明细字段格式异常，建议申请人补充清晰完整的费用明细。"],
            }
        },
        attachment_results=[],
    )

    assert assessment["risk_level"] == "review"


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
