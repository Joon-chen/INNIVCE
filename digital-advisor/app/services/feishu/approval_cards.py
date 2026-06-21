from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.services.feishu import approval_formatters
from app.services.feishu.approval import approval_amount, approval_attachment_refs, approval_form_fields, first_matching_field
from app.services.feishu.approval_advice import approval_decision_for_item
from app.services.gateway.card_actions import gateway_card_action_message_id, gateway_card_action_value


@dataclass(frozen=True)
class ApprovalCardRenderers:
    item_title: Callable[[dict[str, Any], int], str]
    suggestion: Callable[[dict[str, Any], int | None], str]
    detail_text: Callable[[dict[str, Any]], str]


def build_approval_action_card(
    items: list[dict[str, Any]],
    *,
    chat_id: str | None,
    receive_id_type: str,
    receive_id: str,
    actor_open_id: str | None = None,
    limit: int = 6,
    expanded_index: int | None = None,
    renderers: ApprovalCardRenderers,
) -> dict[str, Any]:
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"老板，当前待你审批 **{len(items)}** 个。通过/拒绝仍会先二次确认。",
            },
        }
    ]
    for index, item in enumerate(items[:limit], start=1):
        title = renderers.item_title(item, index)
        suggestion = renderers.suggestion(item, index)
        if expanded_index == index:
            suggestion = f"{suggestion}\n\n{renderers.detail_text(item)}"
        value_base = {
            "kind": "approval_action",
            "index": index,
            "chat_id": chat_id or "",
            "receive_id_type": receive_id_type,
            "receive_id": receive_id,
            "actor_open_id": actor_open_id or "",
        }
        elements.extend(
            [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"{title}\n{suggestion}",
                    },
                },
                {
                    "tag": "action",
                    "layout": "flow",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "详情"},
                            "type": "default",
                            "value": {**value_base, "action": "detail"},
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "通过"},
                            "type": "primary",
                            "value": {**value_base, "action": "approve"},
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "拒绝"},
                            "type": "danger",
                            "value": {**value_base, "action": "reject"},
                        },
                    ],
                },
            ]
        )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "待审批"},
        },
        "elements": elements,
    }


def approval_action_card_renderers() -> ApprovalCardRenderers:
    return ApprovalCardRenderers(
        item_title=approval_card_item_title,
        suggestion=approval_card_suggestion,
        detail_text=approval_card_detail_text,
    )


def approval_card_item_title(item: dict[str, Any], index: int) -> str:
    approval_name = approval_formatters.readable_approval_name(item)
    form = (item.get("instance_detail") or {}).get("form")
    fields = dict(approval_form_fields(form, max_fields=20))
    amount = approval_amount(fields)
    applicant = approval_formatters.approval_applicant_name(item)
    reason = first_matching_field(fields, ["付款事由", "借款事由", "用章事由", "报销事由", "申请事由", "事由", "用途"])
    parts = [f"**{index}. {approval_name}**"]
    if amount is not None:
        parts.append(f"{amount:g}元")
    if applicant:
        parts.append(applicant)
    if reason:
        parts.append(approval_formatters.short_approval_text(reason, 24))
    return "｜".join(parts)


def approval_card_suggestion(item: dict[str, Any], index: int | None = None) -> str:
    form = (item.get("instance_detail") or {}).get("form")
    fields = dict(approval_form_fields(form, max_fields=30))
    attachments = approval_attachment_refs(form)
    conclusion, reason = approval_decision_for_item(
        item,
        approval_formatters.readable_approval_name(item),
        fields,
        amount=approval_amount(fields),
        attachments=attachments,
        attachment_results=approval_formatters.approval_attachment_results(item),
    )
    return f"**建议：{conclusion}**\n理由：{reason}"


def approval_card_detail_text(item: dict[str, Any]) -> str:
    lines = approval_formatters.format_approval_detail_lines(
        item,
        attachment_results=approval_formatters.approval_attachment_results(item),
    )
    useful_lines = [line for line in lines if not line.startswith("可继续回复")]
    result = "\n".join(useful_lines)
    if result.strip():
        return approval_formatters.short_approval_text(result, 1800)
    title = item.get("title") or item.get("approval_name") or ""
    amount = approval_formatters.approval_amount_string(item) or ""
    name = approval_formatters.approval_applicant_name(item) or ""
    parts = [f"审批单：{title}"] if title else []
    if amount and amount != "0":
        parts.append(f"金额：{amount}")
    if name:
        parts.append(f"申请人：{name}")
    instance_code = item.get("instance_code") or item.get("serial_number") or ""
    if instance_code:
        parts.append(f"编号：{instance_code}")
    return "\n".join(parts) if parts else "暂无详情"


def approval_card_toast(
    content: str,
    *,
    toast_type: str = "info",
    shorten: Callable[[str, int], str],
) -> dict[str, Any]:
    return {"toast": {"type": toast_type, "content": shorten(content, 240)}}


def approval_card_action_value(payload: dict[str, Any]) -> dict[str, Any]:
    return gateway_card_action_value(payload)


def approval_card_message_id(payload: dict[str, Any]) -> str | None:
    return gateway_card_action_message_id(payload)
