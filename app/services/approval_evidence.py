from __future__ import annotations

import json
import re
from typing import Any

from app.services.evidence import EVIDENCE_QUALITY_COMPLETE, EVIDENCE_QUALITY_FAILED, EVIDENCE_QUALITY_PARTIAL, Evidence
from app.services.feishu.approval import approval_amount, approval_form_fields, first_matching_field
from app.services.feishu import approval_formatters


APPROVAL_EXPENSE_EVIDENCE_TYPE = "approval_expense"


def build_approval_expense_evidence(
    raw_item: dict[str, Any],
    *,
    attachment_results: list[Any],
    source_event_ids: list[str] | None = None,
) -> Evidence:
    object_id = approval_formatters.approval_instance_code(raw_item) or str(raw_item.get("id") or "")
    detail = raw_item.get("instance_detail") if isinstance(raw_item.get("instance_detail"), dict) else {}
    fields = dict(approval_form_fields(detail.get("form"), max_fields=80))
    form_nodes = _approval_form_nodes(detail.get("form"))
    amount = approval_amount(fields)
    expense_rows = _extract_expense_rows(form_nodes)
    attachment_facts = _attachment_facts(attachment_results)
    verified_amount = _round_money(sum(item["amount"] for item in attachment_facts if item.get("amount")))

    missing: list[str] = []
    technical_notes: list[str] = []
    conflicts: list[str] = []
    if amount is None:
        missing.append("报销总额")
    if not expense_rows:
        missing.append("费用明细行")
    if not attachment_facts:
        missing.append("可核对附件摘要")
    if attachment_facts and verified_amount <= 0:
        missing.append("发票号码、发票金额或票据明细")
    if _form_contains_internal_widget_noise(form_nodes):
        technical_notes.append("费用明细表组件未成功还原")
    if amount is not None and verified_amount > amount + 1:
        conflicts.append("附件可识别金额高于申请金额")

    if amount is None and not expense_rows and not attachment_facts:
        quality = EVIDENCE_QUALITY_FAILED
    elif missing or technical_notes or conflicts:
        quality = EVIDENCE_QUALITY_PARTIAL
    else:
        quality = EVIDENCE_QUALITY_COMPLETE

    manager_summary = _manager_summary(
        amount=amount,
        expense_rows=expense_rows,
        attachment_facts=attachment_facts,
        verified_amount=verified_amount,
        missing=missing,
        conflicts=conflicts,
        technical_notes=technical_notes,
    )
    return Evidence(
        evidence_type=APPROVAL_EXPENSE_EVIDENCE_TYPE,
        quality=quality,
        object_type="approval",
        object_id=object_id,
        facts={
            "approval_amount": amount,
            "expense_row_count": len(expense_rows),
            "expense_rows": expense_rows,
            "attachment_count": len(attachment_facts),
            "readable_attachment_count": len([item for item in attachment_facts if item.get("has_text")]),
            "attachment_amount": verified_amount,
            "verified_invoice_amount": verified_amount,
            "applicant": approval_formatters.approval_applicant_name(raw_item) or "",
            "approval_name": approval_formatters.readable_approval_name(raw_item),
            "reason": first_matching_field(fields, ["报销事由", "申请事由", "付款事由", "借款事由", "事由", "用途"]) or "",
        },
        missing=tuple(_dedupe(missing)),
        conflicts=tuple(_dedupe(conflicts)),
        technical_notes=tuple(_dedupe(technical_notes)),
        manager_summary=manager_summary,
        suggested_next_step=_suggested_next_step(missing=missing, conflicts=conflicts, technical_notes=technical_notes),
        source_event_ids=tuple(source_event_ids or []),
    )


def _approval_form_nodes(form: Any) -> list[dict[str, Any]]:
    if isinstance(form, str):
        try:
            form = json.loads(form)
        except json.JSONDecodeError:
            return []
    if isinstance(form, list):
        return [item for item in form if isinstance(item, dict)]
    return []


def _extract_expense_rows(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for node in nodes:
        name = str(node.get("name") or node.get("title") or "")
        if "费用明细" not in name:
            continue
        rows.extend(_expense_rows_from_value(node.get("value")))
    return rows[:20]


def _expense_rows_from_value(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for row in value:
        fields = row if isinstance(row, list) else [row]
        if not all(isinstance(field, dict) for field in fields):
            continue
        row_payload: dict[str, Any] = {}
        for field in fields:
            key = str(field.get("name") or field.get("title") or field.get("label") or "").strip()
            if not key:
                continue
            raw_value = field.get("value")
            display = _business_value(raw_value)
            if display:
                row_payload[key] = display
        if row_payload and not _row_is_only_internal_widgets(row_payload):
            rows.append(row_payload)
    return rows


def _business_value(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, str):
        return "" if _looks_like_internal_widget(value) else value[:120]
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, dict):
        for key in ("text", "name", "label", "display_value", "amount", "date"):
            nested = _business_value(value.get(key))
            if nested:
                return nested
        return ""
    if isinstance(value, list):
        parts = [_business_value(item) for item in value]
        return "、".join(part for part in parts if part)[:120]
    return ""


def _attachment_facts(attachment_results: list[Any]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for result in attachment_results:
        name = str(getattr(result, "name", "") or "")
        text = str(getattr(result, "text_preview", "") or "")
        error = str(getattr(result, "error", "") or "")
        if not name and not text and not error:
            continue
        facts.append(
            {
                "name": name,
                "amount": _amount_from_text(f"{name} {text}"),
                "has_text": bool(text),
                "error": error,
            }
        )
    return facts


def _amount_from_text(text: str) -> float:
    matches = re.findall(r"(?:金额|价税合计|合计|小写)[:：|\s]*[¥￥]?\s*([0-9]+(?:\.[0-9]+)?)", text)
    if not matches:
        matches = re.findall(r"[¥￥]\s*([0-9]+(?:\.[0-9]+)?)", text)
    amounts = []
    for item in matches:
        try:
            amounts.append(float(item))
        except ValueError:
            pass
    return max(amounts) if amounts else 0.0


def _manager_summary(
    *,
    amount: float | None,
    expense_rows: list[dict[str, Any]],
    attachment_facts: list[dict[str, Any]],
    verified_amount: float,
    missing: list[str],
    conflicts: list[str],
    technical_notes: list[str],
) -> str:
    parts: list[str] = []
    if amount is not None:
        parts.append(f"申请金额为 {amount:g} 元")
    if expense_rows:
        parts.append(f"已还原 {len(expense_rows)} 条费用明细")
    elif "费用明细行" in missing:
        parts.append("费用明细未成功还原，无法核对每笔费用")
    if attachment_facts:
        if verified_amount > 0:
            parts.append(f"附件中可识别金额约 {verified_amount:g} 元")
        else:
            parts.append("附件已读取，但未识别到可核对金额")
    else:
        parts.append("缺少可核对附件摘要")
    if conflicts:
        parts.append("存在证据冲突：" + "、".join(conflicts))
    if technical_notes and not expense_rows:
        parts.append("系统未能还原复杂明细表，需要补充清晰明细或重新处理附件")
    return "；".join(parts) + "。"


def _suggested_next_step(*, missing: list[str], conflicts: list[str], technical_notes: list[str]) -> str:
    if conflicts:
        return "请先核对冲突证据，再决定是否审批。"
    if missing or technical_notes:
        return "请申请人补充清晰费用明细及对应票据/行程凭证后再审。"
    return "证据基本完整，可结合业务背景审批。"


def _form_contains_internal_widget_noise(nodes: list[dict[str, Any]]) -> bool:
    return any(_contains_internal_widget(node) for node in nodes)


def _contains_internal_widget(value: Any) -> bool:
    if isinstance(value, str):
        return _looks_like_internal_widget(value)
    if isinstance(value, list):
        return any(_contains_internal_widget(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_internal_widget(item) for item in value.values())
    return False


def _looks_like_internal_widget(value: str) -> bool:
    return bool(re.search(r"widget\d{8,}", str(value)))


def _row_is_only_internal_widgets(row: dict[str, Any]) -> bool:
    return bool(row) and all(_looks_like_internal_widget(str(value)) for value in row.values())


def _round_money(value: float) -> float:
    return round(float(value or 0), 2)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            output.append(item)
    return output
