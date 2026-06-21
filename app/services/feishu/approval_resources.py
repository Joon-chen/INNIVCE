from typing import Any

from sqlalchemy import select

from app.models.entities import FeishuAppConfig, WorkEvent
from app.services.feishu.approval import approval_amount, approval_form_fields, first_matching_field
from app.services.feishu.approval_attachments import ApprovalAttachmentReadResult
from app.services.feishu.approval_formatters import (
    approval_instance_code,
    first_task_value,
    readable_approval_name,
    short_approval_text,
)
from app.services.resource_registry import upsert_feishu_discovered_resource


def register_pending_approval_resources(db: Any, app_config: FeishuAppConfig, items: list[dict[str, Any]]) -> None:
    changed = False
    for item in items:
        approval_code = str(item.get("approval_code") or item.get("definition_code") or "").strip()
        if not approval_code:
            continue
        resource_item = {
            "resource_type": "approval_code",
            "external_id": approval_code,
            "name": readable_approval_name(item),
            "sync_enabled": True,
            "settings": {"source": "pending_approval_task", "sync_mode": "scheduled"},
        }
        upsert_feishu_discovered_resource(
            db,
            app_config=app_config,
            item=resource_item,
        )
        changed = True
    if changed:
        db.commit()


def attach_synced_approval_attachments(db: Any, app_config: FeishuAppConfig, items: list[dict[str, Any]]) -> None:
    if not items:
        return
    synced_items = _approval_snapshot_items(db, app_config, limit=300)
    by_key: dict[str, dict[str, Any]] = {}
    for synced_item in synced_items:
        for key in approval_match_keys(synced_item):
            by_key.setdefault(key, synced_item)
    for item in items:
        for key in approval_match_keys(item):
            synced_item = by_key.get(key)
            if not synced_item:
                continue
            results = approval_attachment_results_from_payload(synced_item.get("attachment_read_results"))
            if results:
                item["_attachment_results"] = results
                item["_attachment_total"] = (synced_item.get("attachment_ingest") or {}).get("total") or len(results)
            advice = synced_item.get("approval_ai_advice")
            if isinstance(advice, dict):
                item["_approval_llm_decision"] = advice
            break


def attach_approval_history_context(db: Any, app_config: FeishuAppConfig, items: list[dict[str, Any]]) -> None:
    if not items:
        return
    history_items = _approval_snapshot_items(db, app_config, limit=400)
    for item in items:
        item["_approval_history"] = similar_approval_history(item, history_items)


def similar_approval_history(item: dict[str, Any], history_items: list[dict[str, Any]]) -> list[str]:
    current_code = approval_instance_code(item)
    current_name = readable_approval_name(item)
    form = (item.get("instance_detail") or {}).get("form")
    fields = dict(approval_form_fields(form, max_fields=30))
    current_counterparty = first_matching_field(fields, ["供应商名称", "付款对象", "收款方", "客户名称", "对方单位"])
    current_project = first_matching_field(fields, ["项目名称", "项目编码", "项目"])
    results: list[str] = []
    for history in history_items:
        if approval_instance_code(history) == current_code:
            continue
        detail = history.get("instance_detail") if isinstance(history.get("instance_detail"), dict) else {}
        history_fields = dict(approval_form_fields(history.get("form") or detail.get("form"), max_fields=30))
        history_name = readable_approval_name(history)
        if history_name != current_name:
            continue
        counterparty = first_matching_field(history_fields, ["供应商名称", "付款对象", "收款方", "客户名称", "对方单位"])
        project = first_matching_field(history_fields, ["项目名称", "项目编码", "项目"])
        if current_counterparty and counterparty and current_counterparty != counterparty:
            continue
        if current_project and project and current_project != project:
            continue
        amount = approval_amount(history_fields)
        reason = first_matching_field(history_fields, ["付款事由", "借款事由", "用章事由", "报销事由", "申请事由", "事由", "用途"])
        status = approval_instance_status(history) or "unknown"
        parts = [history_name, f"状态:{status}"]
        if amount is not None:
            parts.append(f"金额:{amount:g}")
        if counterparty:
            parts.append(f"对象:{short_approval_text(counterparty, 30)}")
        if project:
            parts.append(f"项目:{short_approval_text(project, 30)}")
        if reason:
            parts.append(f"事由:{short_approval_text(reason, 50)}")
        results.append(" / ".join(parts))
        if len(results) >= 5:
            break
    return results


def approval_match_keys(item: dict[str, Any]) -> list[str]:
    values = [
        approval_instance_code(item),
        first_task_value(item, ["process_code", "instance_code", "approval_instance_id", "serial_number"]),
    ]
    keys = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in keys:
            keys.append(text)
    return keys


def approval_attachment_results_from_payload(value: Any) -> list[ApprovalAttachmentReadResult]:
    if not isinstance(value, list):
        return []
    results: list[ApprovalAttachmentReadResult] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        results.append(
            ApprovalAttachmentReadResult(
                name=str(raw.get("name") or "审批附件"),
                token=str(raw.get("token") or ""),
                content_type=raw.get("content_type"),
                storage_bucket=raw.get("storage_bucket"),
                storage_key=raw.get("storage_key"),
                text_preview=str(raw.get("text_preview") or ""),
                error=raw.get("error"),
            )
        )
    return results


def approval_attachment_result_payload(result: ApprovalAttachmentReadResult) -> dict[str, Any]:
    return {
        "name": result.name,
        "token": result.token,
        "content_type": result.content_type,
        "storage_bucket": result.storage_bucket,
        "storage_key": result.storage_key,
        "text_preview": result.text_preview,
        "error": result.error,
        "fetched": result.fetched,
        "downloaded": result.downloaded,
    }


def approval_instance_status(event: WorkEvent | dict[str, Any]) -> str | None:
    if isinstance(event, dict):
        payload = event
    else:
        payload = event.payload if isinstance(event.payload, dict) else {}
    item = payload.get("item") if isinstance(payload.get("item"), dict) else payload
    raw_status = first_task_value(
        item,
        ["status", "instance_status", "approval_status", "task_status"],
    )
    return str(raw_status).strip().lower() if raw_status else None


def _approval_snapshot_items(db: Any, app_config: FeishuAppConfig, *, limit: int) -> list[dict[str, Any]]:
    events = list(
        db.scalars(
            select(WorkEvent)
            .where(WorkEvent.company_id == app_config.company_id)
            .where(WorkEvent.event_type == "feishu.approvals.snapshot")
            .order_by(WorkEvent.occurred_at.desc())
            .limit(limit)
        ).all()
    )
    items: list[dict[str, Any]] = []
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        synced_item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
        if synced_item:
            items.append(synced_item)
    return items
