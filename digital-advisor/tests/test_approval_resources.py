from types import SimpleNamespace
from uuid import uuid4

from app.services.feishu import approval_resources
from app.services.feishu.approval_attachments import ApprovalAttachmentReadResult


def test_approval_attachment_payload_round_trip() -> None:
    result = ApprovalAttachmentReadResult(
        name="发票.pdf",
        token="file_1",
        storage_key="approval_attachments/demo.pdf",
        text_preview="发票金额：1309.2元",
    )

    payload = approval_resources.approval_attachment_result_payload(result)
    restored = approval_resources.approval_attachment_results_from_payload([payload])

    assert restored[0].name == "发票.pdf"
    assert restored[0].text_preview == "发票金额：1309.2元"


def test_similar_approval_history_matches_same_name_and_counterparty() -> None:
    item = {
        "approval_name": "付款审批",
        "instance_code": "APP-002",
        "instance_detail": {
            "form": '[{"name":"供应商名称","value":"测试供应商"},{"name":"付款金额","value":3000}]'
        },
    }
    history = [
        {
            "approval_name": "付款审批",
            "instance_code": "APP-001",
            "status": "APPROVED",
            "instance_detail": {
                "form": '[{"name":"供应商名称","value":"测试供应商"},{"name":"付款金额","value":2000},{"name":"付款事由","value":"采购"}]'
            },
        },
        {
            "approval_name": "付款审批",
            "instance_code": "APP-003",
            "instance_detail": {"form": '[{"name":"供应商名称","value":"其他供应商"}]'},
        },
    ]

    result = approval_resources.similar_approval_history(item, history)

    assert result == ["付款审批 / 状态:approved / 金额:2000 / 对象:测试供应商 / 事由:采购"]


def test_approval_instance_status_reads_nested_item() -> None:
    event = SimpleNamespace(payload={"item": {"instance_status": "PENDING"}})

    assert approval_resources.approval_instance_status(event) == "pending"


def test_register_pending_approval_resources_writes_v5_resource_only(monkeypatch) -> None:
    calls = []

    def fail_legacy_write(*args, **kwargs):
        raise AssertionError("pending approval resources must not write legacy FeishuResource")

    def fake_upsert_discovered_resource(db, **kwargs):
        calls.append({"db": db, "kwargs": kwargs})
        return SimpleNamespace(id=uuid4())

    monkeypatch.setattr(approval_resources, "upsert_feishu_discovered_resource", fake_upsert_discovered_resource)
    monkeypatch.setattr(approval_resources, "upsert_feishu_resource", fail_legacy_write, raising=False)
    db = SimpleNamespace(commits=0, commit=lambda: setattr(db, "commits", db.commits + 1))
    app_config = SimpleNamespace(id=uuid4(), company_id=uuid4())

    approval_resources.register_pending_approval_resources(
        db,
        app_config,
        [
            {"approval_code": "approval_xxx", "approval_name": "付款审批"},
            {"approval_code": " ", "approval_name": "空编码忽略"},
        ],
    )

    assert db.commits == 1
    assert len(calls) == 1
    assert calls[0]["db"] is db
    assert calls[0]["kwargs"]["app_config"] is app_config
    assert calls[0]["kwargs"].get("legacy_feishu_resource_id") is None
    assert calls[0]["kwargs"]["item"] == {
        "resource_type": "approval_code",
        "external_id": "approval_xxx",
        "name": "付款审批",
        "sync_enabled": True,
        "settings": {"source": "pending_approval_task", "sync_mode": "scheduled"},
    }
