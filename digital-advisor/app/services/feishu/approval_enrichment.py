import asyncio
from collections.abc import Callable
from typing import Any

from app.models.entities import FeishuAppConfig
from app.services.feishu.approval_attachments import ApprovalAttachmentReadResult, FeishuApprovalAttachmentService


ApprovalAdviceFn = Callable[[dict[str, Any]], dict[str, str] | None]


async def ensure_pending_approval_attachment_summaries(
    app_config: FeishuAppConfig,
    items: list[dict[str, Any]],
    *,
    attachment_results: Callable[[dict[str, Any]], list[ApprovalAttachmentReadResult]],
    attachment_refs: Callable[[Any], list[dict[str, str]]],
    instance_code: Callable[[dict[str, Any]], str | None],
    service_factory: Callable[[FeishuAppConfig], FeishuApprovalAttachmentService] = FeishuApprovalAttachmentService,
) -> None:
    service: FeishuApprovalAttachmentService | None = None
    for item in items:
        if attachment_results(item):
            continue
        detail = item.get("instance_detail") if isinstance(item.get("instance_detail"), dict) else {}
        refs = attachment_refs(item.get("form") or detail.get("form"))
        if not refs:
            continue
        service = service or service_factory(app_config)
        results = await service.read_attachment_refs(
            refs,
            instance_code=str(instance_code(item) or ""),
            max_files=min(len(refs), 8),
        )
        if results:
            item["_attachment_results"] = results
            item["_attachment_total"] = len(refs)


def attach_approval_llm_advice(items: list[dict[str, Any]], *, advice_for_item: ApprovalAdviceFn) -> None:
    for item in items[:6]:
        if isinstance(item.get("_approval_llm_decision"), dict):
            continue
        advice = advice_for_item(item)
        if advice:
            item["_approval_llm_decision"] = advice


async def attach_approval_llm_advice_async(items: list[dict[str, Any]], *, advice_for_item: ApprovalAdviceFn) -> None:
    for item in items[:6]:
        if isinstance(item.get("_approval_llm_decision"), dict):
            continue
        advice = await asyncio.to_thread(advice_for_item, item)
        if advice:
            item["_approval_llm_decision"] = advice


def approval_llm_advice_for_item(
    item: dict[str, Any],
    *,
    form_fields: Callable[[Any], list[tuple[str, str]]],
    attachment_refs: Callable[[Any], list[dict[str, str]]],
    attachment_results: Callable[[dict[str, Any]], list[ApprovalAttachmentReadResult]],
    rule_recommendation: Callable[..., tuple[str, str]],
    readable_name: Callable[[dict[str, Any]], str],
    approval_amount: Callable[[dict[str, str]], float | None],
    attachment_basis: Callable[[list[ApprovalAttachmentReadResult]], str],
    generate_advice: Callable[..., dict[str, str] | None],
) -> dict[str, str] | None:
    form = (item.get("instance_detail") or {}).get("form")
    fields = dict(form_fields(form))
    attachments = attachment_refs(form)
    results = attachment_results(item)
    name = readable_name(item)
    amount = approval_amount(fields)
    rule_conclusion, rule_reason = rule_recommendation(
        name,
        fields,
        amount=amount,
        attachments=attachments,
        attachment_results=results,
    )
    return generate_advice(
        approval_name=name,
        fields=fields,
        amount=amount,
        attachment_summary=attachment_basis(results).removeprefix("附件要点：").strip("；"),
        history=item.get("_approval_history") if isinstance(item.get("_approval_history"), list) else [],
        rule_conclusion=rule_conclusion,
        rule_reason=rule_reason,
    )
