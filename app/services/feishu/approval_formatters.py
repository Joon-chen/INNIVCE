import json
import re
from datetime import UTC, datetime
from typing import Any

from app.services.feishu.approval import approval_amount, approval_attachment_refs, approval_form_fields, first_matching_field
from app.services.feishu.approval_advice import approval_detailed_decision_reason, approval_text_decision_advice


def select_approval_item(items: list[dict[str, Any]], selector_text: str) -> dict[str, Any] | None:
    text = selector_text.strip()
    match = re.search(r"第\s*(\d+)\s*(?:条|个|笔|项)?", text)
    if match:
        index = int(match.group(1)) - 1
        if 0 <= index < len(items):
            return items[index]
        return None
    compact = re.sub(r"\s+", "", text)
    for item in items:
        values = [
            approval_instance_code(item),
            first_task_value(item, ["task_id"]),
            approval_applicant_name(item),
            readable_approval_name(item),
        ]
        if any(value and str(value).replace(" ", "") in compact for value in values):
            return item
    return None


def format_approval_detail_lines(
    item: dict[str, Any],
    *,
    attachment_results: list[Any] | None = None,
) -> list[str]:
    form = (item.get("instance_detail") or {}).get("form")
    fields = approval_form_fields(form, max_fields=40)
    attachments = approval_attachment_refs(form)
    approval_name = readable_approval_name(item)
    instance_code = approval_instance_code(item)
    applicant = approval_applicant_name(item)
    started_at = format_task_time(first_task_value(item, ["task_start_time", "start_time", "create_time"]))

    title = approval_name
    if applicant:
        title = f"{title}｜{applicant}"
    lines = [f"这笔审批的关键信息：{title}"]
    meta = [value for value in [f"单号：{instance_code}" if instance_code else None, f"时间：{started_at}" if started_at else None] if value]
    if meta:
        lines.append(f"- {'；'.join(meta)}")
    for name, value in prioritized_approval_fields(fields, max_fields=8):
        lines.append(f"- {name}：{short_approval_text(value, 70)}")
    if attachments:
        lines.append(f"- 附件：{len(attachments)} 个")
        for ref in attachments[:5]:
            name = ref.get("name") or ref.get("field_name") or "附件"
            file_type = f" / {ref['type']}" if ref.get("type") else ""
            token_state = "有token" if ref.get("token") else "无token"
            lines.append(f"  - {name}{file_type}（{token_state}）")
        if attachment_results:
            lines.extend(format_attachment_read_results(attachment_results))
        else:
            lines.append("- 附件正文：已识别附件引用；尚未执行读取解析。")
    else:
        lines.append("- 附件：未在审批表单里识别到附件字段。")
    lines.extend(
        approval_detailed_decision_reason(
            item,
            approval_name,
            dict(fields),
            amount=approval_amount(dict(fields)),
            attachments=attachments,
            attachment_results=attachment_results or [],
        )
    )
    lines.append("可继续回复：通过第N条 / 拒绝第N条。")
    return lines


def format_attachment_read_results(results: list[Any]) -> list[str]:
    lines = ["- 附件读取："]
    for result in results:
        error = getattr(result, "error", None)
        name = getattr(result, "name", "审批附件")
        if error:
            lines.append(f"  - {name}：读取失败，{error}")
            continue
        text_preview = getattr(result, "text_preview", "")
        if text_preview:
            preview = short_approval_text(text_preview, 180)
            lines.append(f"  - {name}：已读取摘要：{preview}")
        else:
            lines.append(f"  - {name}：已临时读取；暂未解析出可读正文。")
    return lines


def format_pending_approval_tasks(items: list[dict[str, Any]], *, limit: int) -> list[str]:
    if not items:
        return ["老板，我按飞书审批任务接口查了，当前没有待你本人审批的任务。"]

    display_items = items[:limit]
    lines = [f"老板，待你审批 {len(items)} 个："]
    for index, item in enumerate(display_items, start=1):
        lines.extend(format_pending_approval_task_card(item, index=index))
        if item.get("detail_error"):
            lines.append(f"  明细读取提示：{item['detail_error']}")
    lines.append("")
    lines.append("回复：通过第N条 / 拒绝第N条 / 展开第N条。提交前仍会二次确认。")
    return lines


def format_pending_approval_task_card(item: dict[str, Any], *, index: int) -> list[str]:
    form = (item.get("instance_detail") or {}).get("form")
    fields = dict(approval_form_fields(form, max_fields=30))
    attachments = approval_attachment_refs(form)
    attachment_results = approval_attachment_results(item)
    approval_name = readable_approval_name(item)
    applicant = approval_applicant_name(item)
    amount = approval_amount(fields)
    reason = first_matching_field(fields, ["付款事由", "借款事由", "用章事由", "报销事由", "申请事由", "事由", "用途"])
    counterparty = first_matching_field(fields, ["供应商名称", "付款对象", "收款方", "客户名称", "对方单位"])
    project = first_matching_field(fields, ["项目名称", "项目编码", "项目"])
    title_parts = [f"{index}. {approval_name}"]
    if amount is not None:
        title_parts.append(f"{amount:g}元")
    if applicant:
        title_parts.append(applicant)
    if reason:
        title_parts.append(short_approval_text(reason, 20))
    if attachments:
        title_parts.append(approval_attachment_title(len(attachments), attachment_results))
    lines = [f"- {'｜'.join(title_parts)}"]
    brief = [value for value in [f"对象：{counterparty}" if counterparty else None, f"项目：{project}" if project else None] if value]
    if brief:
        lines[-1] = f"{lines[-1]}（{'；'.join(brief[:2])}）"
    attachment_status = approval_attachment_status_line(attachments, attachment_results)
    if attachment_status:
        lines.append(f"  附件：{attachment_status}")
    lines.append(
        "  建议："
        + approval_text_decision_advice(
            item,
            approval_name,
            fields,
            amount=amount,
            attachments=attachments,
            attachment_results=attachment_results,
        )
    )
    return lines


def approval_attachment_results(item: dict[str, Any]) -> list[Any]:
    value = item.get("_attachment_results")
    if isinstance(value, list):
        return value
    return []


def approval_attachment_title(total: int, results: list[Any]) -> str:
    if not results:
        return f"附件{total}"
    readable = sum(1 for result in results if getattr(result, "text_preview", None))
    failed = sum(1 for result in results if getattr(result, "error", None))
    if readable:
        return f"附件已读{readable}/{total}"
    if failed:
        return f"附件读取失败{failed}/{total}"
    return f"附件已处理{len(results)}/{total}"


def approval_attachment_status_line(
    attachments: list[dict[str, str]],
    results: list[Any],
) -> str | None:
    if not attachments:
        return None
    total = len(attachments)
    if not results:
        return f"识别到 {total} 个，待展开读取。"
    readable = [result for result in results if getattr(result, "text_preview", None)]
    failed = [result for result in results if getattr(result, "error", None)]
    fetched_without_text = [
        result for result in results if getattr(result, "fetched", False) and not getattr(result, "text_preview", None)
    ]
    parts = []
    if readable:
        parts.append(f"已读摘要 {len(readable)} 个")
    if fetched_without_text:
        parts.append(f"已临时读取但无文本 {len(fetched_without_text)} 个")
    if failed:
        parts.append(f"失败 {len(failed)} 个")
    return f"{'，'.join(parts) or '已处理'}；详细内容回复“展开第N条”。"


def readable_approval_name(item: dict[str, Any]) -> str:
    for key in ("approval_name", "definition_name", "title", "name"):
        value = str(item.get(key) or "").strip()
        if value and not looks_like_business_identifier(value):
            return localized_approval_name(value)
    return localized_approval_name(str(first_task_value(item, ["definition_name", "title", "approval_name"]) or "审批"))


def localized_approval_name(value: str) -> str:
    normalized = " ".join(str(value or "").split())
    lower = normalized.lower()
    mappings = {
        "reserve fund": "借款申请",
        "payment request": "付款审批",
        "reimbursement - copy": "报销审批",
        "reimbursement": "报销审批",
    }
    return mappings.get(lower, normalized)


def looks_like_business_identifier(value: str) -> bool:
    normalized = value.replace("-", "")
    if len(normalized) >= 24 and all(char.isalnum() for char in normalized):
        return True
    return value.startswith(("cli_", "ou_", "oc_", "approval_"))


def approval_applicant_name(item: dict[str, Any]) -> str | None:
    initiator_names = item.get("initiator_names")
    if isinstance(initiator_names, list) and initiator_names:
        return "、".join(str(name) for name in initiator_names[:3] if name)
    detail = item.get("instance_detail") if isinstance(item.get("instance_detail"), dict) else {}
    # Try instance detail applicant name first
    detail_applicant = detail.get("applicant") if isinstance(detail, dict) else None
    if isinstance(detail_applicant, dict) and detail_applicant.get("name"):
        return str(detail_applicant["name"])
    if isinstance(detail_applicant, str):
        return detail_applicant
    obj_name = first_task_value(item, ["applicant_name", "starter_name", "user_name"])
    if obj_name:
        return obj_name
    detail_name = first_task_value(detail, ["starter_name", "user_name", "applicant_name"])
    if detail_name:
        return detail_name
    return None


def approval_instance_code(item: dict[str, Any]) -> str | None:
    value = first_task_value(
        item,
        ["instance_code", "process_code", "approval_instance_id", "process_external_id", "task_external_id", "serial_number"],
    )
    if value:
        return str(value)
    instance = item.get("instance") if isinstance(item.get("instance"), dict) else {}
    return first_task_value(instance, ["code", "instance_code", "process_code", "approval_instance_id"])


def format_approval_form_summary(item: dict[str, Any], *, max_fields: int) -> list[str]:
    detail = item.get("instance_detail") if isinstance(item.get("instance_detail"), dict) else {}
    fields = approval_form_fields(detail.get("form"), max_fields=max(max_fields * 4, max_fields + 8))
    if not fields:
        return []
    return [f"{name}：{short_approval_text(value, 80)}" for name, value in prioritized_approval_fields(fields, max_fields=max_fields)]


def prioritized_approval_fields(fields: list[tuple[str, str]], *, max_fields: int) -> list[tuple[str, str]]:
    priority_terms = [
        "付款金额",
        "申请金额",
        "费用汇总",
        "报销金额",
        "合同金额",
        "供应商名称",
        "付款对象",
        "收款方",
        "付款事由",
        "借款事由",
        "用章事由",
        "报销事由",
        "项目名称",
        "项目编码",
        "公司抬头",
        "付款抬头",
        "费用承担公司",
        "付款日期",
        "归还日期",
        "用章日期",
    ]
    selected: list[tuple[str, str]] = []
    for term in priority_terms:
        for name, value in fields:
            if term in name and (name, value) not in selected:
                selected.append((name, value))
                break
            if len(selected) >= max_fields:
                return selected
    for name, value in fields:
        if (name, value) not in selected and not is_noisy_approval_field(name, value):
            selected.append((name, value))
        if len(selected) >= max_fields:
            break
    return selected


def is_noisy_approval_field(name: str, value: str) -> bool:
    if name in {"说明 1", "盖章详情", "费用明细"}:
        return True
    return len(str(value)) > 120 and any(char in str(value) for char in ["{", "[", "widget"])


def short_approval_text(value: Any, max_length: int = 80) -> str:
    text = " ".join(str(value or "").replace("\n", " ").split())
    if not text:
        return ""
    return text if len(text) <= max_length else f"{text[:max_length]}..."


def first_task_value(item: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def format_task_time(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return str(value)
    if timestamp > 10_000_000_000:
        timestamp //= 1000
    return datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%d %H:%M")


def stringify_approval_field_value(value: Any) -> str | None:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, dict):
        for key in ("text", "name", "label", "value", "display_name", "amount"):
            inner = value.get(key)
            if inner not in (None, "", [], {}):
                return stringify_approval_field_value(inner)
        return json.dumps(value, ensure_ascii=False, default=str)[:120]
    return str(value)[:120]


def approval_field_display_value(field: dict[str, Any]) -> str | None:
    value = field.get("value")
    mapped = approval_option_display_value(field, value)
    if mapped is not None:
        return mapped
    if isinstance(value, list):
        parts = [stringify_approval_field_value(item) for item in value[:5]]
        return "、".join(part for part in parts if part)
    return stringify_approval_field_value(value)


def approval_option_display_value(field: dict[str, Any], value: Any) -> str | None:
    options = field.get("option")
    if not options:
        return None
    option_items: list[dict[str, Any]] = []
    if isinstance(options, list):
        option_items = [item for item in options if isinstance(item, dict)]
    elif isinstance(options, dict):
        nested = options.get("options") or options.get("items") or options.get("children")
        if isinstance(nested, list):
            option_items = [item for item in nested if isinstance(item, dict)]
    selected_values = {str(item) for item in value} if isinstance(value, list) else {str(value)}
    labels: list[str] = []
    for option in option_items:
        option_id = str(
            option.get("id")
            or option.get("value")
            or option.get("key")
            or option.get("option_id")
            or option.get("text")
            or ""
        )
        if option_id not in selected_values:
            continue
        label = option.get("text") or option.get("name") or option.get("label") or option.get("value")
        if isinstance(label, dict):
            label = label.get("zh_cn") or label.get("zh-CN") or label.get("en_us") or label.get("text")
        if label:
            labels.append(str(label))
    return "、".join(labels) if labels else None
