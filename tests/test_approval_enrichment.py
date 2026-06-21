import asyncio
from types import SimpleNamespace

from app.services.feishu import approval_enrichment
from app.services.feishu.approval_attachments import ApprovalAttachmentReadResult


def test_ensure_pending_approval_attachment_summaries_reads_missing_refs() -> None:
    result = ApprovalAttachmentReadResult(name="合同.pdf", token="file_1", text_preview="合同金额：50000")

    class FakeService:
        async def read_attachment_refs(self, refs, *, instance_code, max_files):
            assert refs == [{"file_token": "file_1", "name": "合同.pdf"}]
            assert instance_code == "JKSQ001"
            assert max_files == 1
            return [result]

    item = {"form": [{"file_token": "file_1", "name": "合同.pdf"}], "instance_code": "JKSQ001"}

    asyncio.run(
        approval_enrichment.ensure_pending_approval_attachment_summaries(
            SimpleNamespace(),
            [item],
            attachment_results=lambda value: value.get("_attachment_results") or [],
            attachment_refs=lambda form: form or [],
            instance_code=lambda value: value.get("instance_code"),
            service_factory=lambda app_config: FakeService(),
        )
    )

    assert item["_attachment_results"] == [result]
    assert item["_attachment_total"] == 1


def test_attach_approval_llm_advice_skips_existing_decision() -> None:
    calls = []
    items = [{"_approval_llm_decision": {"conclusion": "同意"}}, {"approval_name": "付款"}]

    approval_enrichment.attach_approval_llm_advice(
        items,
        advice_for_item=lambda item: calls.append(item) or {"conclusion": "拒绝"},
    )

    assert calls == [items[1]]
    assert items[0]["_approval_llm_decision"]["conclusion"] == "同意"
    assert items[1]["_approval_llm_decision"]["conclusion"] == "拒绝"


def test_approval_llm_advice_for_item_passes_attachment_summary_and_history() -> None:
    attachment = ApprovalAttachmentReadResult(name="合同.pdf", token="file_1", text_preview="合同金额：50000")
    item = {
        "approval_name": "付款审批",
        "instance_detail": {"form": [{"name": "申请金额", "value": "50000"}]},
        "_attachment_results": [attachment],
        "_approval_history": ["历史审批：同供应商通过"],
    }
    captured = {}

    def fake_generate_advice(**kwargs):
        captured.update(kwargs)
        return {"conclusion": "同意", "reason": "资料完整"}

    advice = approval_enrichment.approval_llm_advice_for_item(
        item,
        form_fields=lambda form: [("申请金额", "50000")],
        attachment_refs=lambda form: [{"file_token": "file_1"}],
        attachment_results=lambda value: value["_attachment_results"],
        rule_recommendation=lambda *args, **kwargs: ("同意", "金额可控"),
        readable_name=lambda value: value["approval_name"],
        approval_amount=lambda fields: float(fields["申请金额"]),
        attachment_basis=lambda results: f"附件要点：{results[0].text_preview}；",
        generate_advice=fake_generate_advice,
    )

    assert advice == {"conclusion": "同意", "reason": "资料完整"}
    assert captured["approval_name"] == "付款审批"
    assert captured["amount"] == 50000
    assert captured["attachment_summary"] == "合同金额：50000"
    assert captured["history"] == ["历史审批：同供应商通过"]
