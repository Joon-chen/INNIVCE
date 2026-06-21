from types import SimpleNamespace

from app.services.approval_evidence import build_approval_expense_evidence
from app.services.approval_snapshot_builder import _assessment_from_evidence
from app.services.evidence import EVIDENCE_QUALITY_COMPLETE, EVIDENCE_QUALITY_PARTIAL, evidence_from_payload, evidence_payload
from app.services.runtime_v5.feishu_resource_providers import _snapshot_payload


def test_evidence_payload_round_trip_freezes_contract() -> None:
    evidence = build_approval_expense_evidence(
        {
            "instance_code": "approval-1",
            "approval_name": "差旅费报销",
            "instance_detail": {
                "form": (
                    '[{"name":"费用明细","value":[['
                    '{"name":"费用项目","value":"打车"},'
                    '{"name":"金额","value":8902.33}'
                    ']]},{"name":"费用汇总","value":8902.33},{"name":"报销事由","value":"出差打车"}]'
                )
            },
        },
        attachment_results=[
            SimpleNamespace(name="滴滴电子发票.pdf", text_preview="价税合计 8902.33", error=""),
        ],
        source_event_ids=["event-1"],
    )

    payload = evidence_payload(evidence)
    restored = evidence_from_payload(payload)

    assert payload["evidence_type"] == "approval_expense"
    assert payload["quality"] == EVIDENCE_QUALITY_COMPLETE
    assert payload["facts"]["approval_amount"] == 8902.33
    assert restored.source_event_ids == ("event-1",)


def test_approval_expense_evidence_translates_widget_noise_for_manager() -> None:
    evidence = build_approval_expense_evidence(
        {
            "instance_code": "approval-1",
            "approval_name": "差旅费报销",
            "instance_detail": {
                "form": (
                    '[{"name":"费用明细","value":[{"id":"widget17602323038220001","name":"费用项目",'
                    '"value":"widget17602323038220001"}]},'
                    '{"name":"费用汇总","value":8902.33}]'
                )
            },
        },
        attachment_results=[
            SimpleNamespace(name="发票.pdf", text_preview="发票监制章 购买方信息", error=""),
        ],
    )

    assert evidence.quality == EVIDENCE_QUALITY_PARTIAL
    assert "费用明细行" in evidence.missing
    assert "费用明细表组件未成功还原" in evidence.technical_notes
    assert "widget" not in evidence.manager_summary
    assert "费用明细未成功还原" in evidence.manager_summary
    assert "补充清晰费用明细" in evidence.suggested_next_step


def test_snapshot_assessment_consumes_evidence_summary_not_widget_noise() -> None:
    evidence = build_approval_expense_evidence(
        {
            "instance_code": "approval-1",
            "approval_name": "差旅费报销",
            "instance_detail": {
                "form": (
                    '[{"name":"费用明细","value":[{"id":"widget17602323038220001","name":"费用项目",'
                    '"value":"widget17602323038220001"}]},'
                    '{"name":"费用汇总","value":8902.33}]'
                )
            },
        },
        attachment_results=[],
    )

    assessment = _assessment_from_evidence({"suggestion": "补充后再审"}, evidence=evidence)

    assert assessment["source"] == "evidence"
    assert assessment["suggestion"] == "补充后再审"
    assert assessment["evidence_quality"] == "partial"
    assert "widget" not in assessment["reason"]
    assert "费用明细未成功还原" in assessment["reason"]
    assert "费用明细行" in assessment["missing_evidence"]


def test_snapshot_payload_exposes_evidence_for_interaction_rendering() -> None:
    snapshot = SimpleNamespace(
        id="snapshot-1",
        company_id="company-1",
        object_type="approval",
        object_id="approval-1",
        snapshot_type="approval_current_judgment",
        status="completed",
        summary="需补充清晰费用明细。",
        recommendation="补充后再审",
        risk_level="review",
        reasons=["费用明细未成功还原。"],
        source_event_ids=["event-1"],
        payload={
            "evidence": {
                "evidence_type": "approval_expense",
                "quality": "partial",
                "facts": {"approval_amount": 8902.33},
                "missing": ["费用明细行"],
                "conflicts": [],
                "manager_summary": "费用明细未成功还原。",
                "suggested_next_step": "请申请人补充清晰费用明细。",
            }
        },
    )

    payload = _snapshot_payload(snapshot)

    assert payload["payload"]["evidence"]["quality"] == "partial"
    assert payload["payload"]["evidence"]["missing"] == ["费用明细行"]
