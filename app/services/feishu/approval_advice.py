from typing import Any

from app.services.feishu.approval import first_matching_field
from app.services.feishu.approval_attachments import ApprovalAttachmentReadResult


def approval_decision_for_item(
    item: dict[str, Any],
    approval_name: str,
    fields: dict[str, str],
    *,
    amount: float | None,
    attachments: list[dict[str, str]],
    attachment_results: list[ApprovalAttachmentReadResult],
) -> tuple[str, str]:
    decision = item.get("_approval_llm_decision") if isinstance(item.get("_approval_llm_decision"), dict) else {}
    conclusion = str(decision.get("conclusion") or "").strip()
    concise_reason = str(decision.get("concise_reason") or "").strip()
    if conclusion and concise_reason:
        return conclusion, concise_reason
    return rule_approval_decision_recommendation(
        approval_name,
        fields,
        amount=amount,
        attachments=attachments,
        attachment_results=attachment_results,
    )


def rule_approval_decision_recommendation(
    approval_name: str,
    fields: dict[str, str],
    *,
    amount: float | None,
    attachments: list[dict[str, str]],
    attachment_results: list[ApprovalAttachmentReadResult],
) -> tuple[str, str]:
    text = f"{_localized_approval_name(approval_name)} {' '.join(fields.keys())} {' '.join(fields.values())}".lower()
    attachment_summary = approval_attachment_basis(attachment_results).removeprefix("附件要点：").strip("；")
    fetched_attachments = sum(1 for result in attachment_results if result.fetched)
    reason = first_matching_field(fields, ["付款事由", "借款事由", "用章事由", "报销事由", "申请事由", "事由", "用途"])
    project = first_matching_field(fields, ["项目名称", "项目编码", "项目"])
    counterparty = first_matching_field(fields, ["供应商名称", "付款对象", "收款方", "客户名称", "对方单位"])

    if "借款" in text or "reserve fund" in text:
        if project or reason:
            return "可通过", _short_text(f"借款用途为{reason or '未写明'}；项目为{project or '未写明'}；需关注后续抵扣/归还闭环。", 96)
        return "补充后再审", "借款用途、项目或归还/抵扣闭环信息不足。"

    if ("用章" in text or "盖章" in text) and ("合同" in text or "协议" in text):
        if attachment_summary:
            return "可通过", _short_text(f"附件显示合同/协议材料已读取；申请事由为{reason or '用章'}，主体和用途未见明显冲突。", 96)
        if fetched_attachments:
            return "先展开附件", "附件已临时读取但未抽出可读文字，可能是图片或扫描件；这不是材料缺失，需看原件后再定。"
        if attachments:
            return "先展开附件", "有附件但本地摘要不足，无法判断合同主体、版本和用章材料是否匹配。"
        return "补充后再审", "用章/协议类审批缺少可读附件摘要，无法判断文件内容与申请是否一致。"

    if "报销" in text:
        if attachment_summary:
            conclusion = "可通过" if amount is not None and amount <= 3000 else "谨慎通过"
            basis = f"附件摘要已读取；金额{amount:g}元，费用归属/票据线索可结合摘要判断。" if amount is not None else "附件摘要已读取，可结合票据和费用说明判断。"
            return conclusion, _short_text(basis, 96)
        if fetched_attachments:
            return "先展开附件", "附件已临时读取但未抽出可读文字，可能是图片或扫描件；这不是材料缺失，需看原件后再定。"
        if attachments:
            return "先展开附件", "报销存在附件但摘要不足，无法确认发票、行程或费用归属是否匹配。"
        return "补充后再审", "报销未见可读票据/行程附件摘要，缺少判断依据。"

    if "付款" in text or "payment" in text:
        if amount is not None and amount >= 30000 and not attachment_summary:
            if fetched_attachments:
                return "先展开附件", "付款附件已临时读取但未抽出可读文字，不能自动判断合同/收款依据；需看原件后再定。"
            return "先展开附件", "付款金额较高，当前缺少合同/收款信息摘要，不能直接判断。"
        if counterparty or attachment_summary:
            return "可通过", _short_text(f"付款对象{counterparty or '见附件'}，事由{reason or '见申请'}；未见明显冲突。", 96)
        return "补充后再审", "付款对象、合同/收款信息或业务依据不足。"

    if amount is not None and amount >= 30000:
        return "先展开附件", "金额较高，当前摘要不足以判断预算归属和业务依据。"
    return "可通过", _short_text(f"申请事由{reason or '已填写'}；未见明显异常。", 96)


def approval_text_decision_advice(
    item: dict[str, Any] | None,
    approval_name: str,
    fields: dict[str, str],
    *,
    amount: float | None,
    attachments: list[dict[str, str]],
    attachment_results: list[ApprovalAttachmentReadResult],
) -> str:
    item_data = item or {}
    assessment = item_data.get("_approval_assessment") if isinstance(item_data.get("_approval_assessment"), dict) else {}
    if assessment:
        conclusion = str(assessment.get("suggestion") or "需关注").strip() or "需关注"
        reason = str(assessment.get("reason") or assessment.get("detailed_reason") or "已读取审批认知快照").strip()
        return f"{conclusion}。理由：{reason}"
    conclusion, reason = approval_decision_for_item(
        item_data,
        approval_name,
        fields,
        amount=amount,
        attachments=attachments,
        attachment_results=attachment_results,
    )
    return f"{conclusion}。理由：{reason}"


def approval_detailed_decision_reason(
    item: dict[str, Any] | None,
    approval_name: str,
    fields: dict[str, str],
    *,
    amount: float | None,
    attachments: list[dict[str, str]],
    attachment_results: list[ApprovalAttachmentReadResult],
) -> list[str]:
    conclusion, concise_reason = approval_decision_for_item(
        item or {},
        approval_name,
        fields,
        amount=amount,
        attachments=attachments,
        attachment_results=attachment_results,
    )
    lines = ["- 详细理由："]
    lines.append(f"  - 结论：{conclusion}。{concise_reason}")
    decision = (item or {}).get("_approval_llm_decision") if isinstance((item or {}).get("_approval_llm_decision"), dict) else {}
    detailed_reason = str(decision.get("detailed_reason") or "").strip()
    if detailed_reason:
        lines.append(f"  - 综合研判：{_short_text(detailed_reason, 260)}")
    reason = first_matching_field(fields, ["付款事由", "借款事由", "用章事由", "报销事由", "申请事由", "事由", "用途"])
    project = first_matching_field(fields, ["项目名称", "项目编码", "项目"])
    counterparty = first_matching_field(fields, ["供应商名称", "付款对象", "收款方", "客户名称", "对方单位"])
    if amount is not None:
        lines.append(f"  - 金额依据：表单金额 {amount:g}。")
    if counterparty:
        lines.append(f"  - 对象依据：{_short_text(counterparty, 80)}。")
    if project:
        lines.append(f"  - 项目依据：{_short_text(project, 80)}。")
    if reason:
        lines.append(f"  - 事由依据：{_short_text(reason, 100)}。")
    attachment_summary = approval_attachment_basis(attachment_results).removeprefix("附件要点：").strip("；")
    if attachment_summary:
        lines.append(f"  - 附件/OCR依据：{_short_text(attachment_summary, 180)}。")
    elif attachments and any(result.fetched for result in attachment_results):
        lines.append("  - 附件依据：附件已临时读取，但未解析出可读文字；可能是图片或扫描件，需要 OCR 或人工查看原件。")
    elif attachments:
        lines.append("  - 附件依据：识别到附件引用，但本地尚未完成读取解析。")
    return lines


def approval_task_advice(
    approval_name: str,
    fields: dict[str, str],
    *,
    amount: float | None,
    reason: str | None,
    attachments: list[dict[str, str]] | None = None,
    attachment_results: list[ApprovalAttachmentReadResult] | None = None,
) -> str:
    attachment_basis = approval_attachment_basis(attachment_results)
    manual_attachment_check = "如涉及附件，需先展开核对原件；"
    readable_name = _localized_approval_name(approval_name)
    text = f"{readable_name} {' '.join(fields.keys())} {' '.join(fields.values())} {reason or ''}"
    if "用章" in text or "盖章" in text:
        if "合同" in text or "协议" in text:
            return f"{attachment_basis or manual_attachment_check}合同/协议与用章事由匹配时可通过；如主体、版本或协议类型不一致则退回补正。"
        return f"{attachment_basis or manual_attachment_check}用章材料与申请用途匹配时可通过；如主体或用途不一致则退回补正。"
    if "借款" in text or "reserve fund" in text.lower():
        return "借款用途、项目归属和归还/抵扣闭环清楚，可通过；如存在历史未清借款或归属不清，先补充。"
    if "报销" in text:
        if amount is not None and amount <= 3000:
            return f"{attachment_basis or manual_attachment_check}金额较低，票据/行程/费用归属匹配即可通过；归属不清再补充。"
        return f"{attachment_basis or manual_attachment_check}费用明细、发票和关联单匹配可通过；金额或归属不清再补充。"
    if "付款" in text or "payment" in text.lower():
        if amount is not None and amount >= 30000:
            return f"{attachment_basis or manual_attachment_check}金额较高，预算/项目/供应商/合同或收款信息一致后通过；任一项不清先补充。"
        return f"{attachment_basis or manual_attachment_check}供应商、事由、付款日期和收款信息一致可通过；不一致先补充。"
    if amount is not None and amount >= 30000:
        return f"{attachment_basis or manual_attachment_check}金额较高，预算归属和业务依据清楚再通过；不清先补充。"
    return f"{attachment_basis or manual_attachment_check}申请人、事由和业务归属一致可通过；不一致先补充。"


def approval_attachment_basis(attachment_results: list[ApprovalAttachmentReadResult] | None = None) -> str:
    if not attachment_results:
        return ""
    previews = [result.text_preview for result in attachment_results if result.text_preview]
    if not previews:
        return ""
    summary = _short_text("；".join(" ".join(preview.split()) for preview in previews), 90)
    return f"附件要点：{summary}；"


def _localized_approval_name(value: str) -> str:
    normalized = " ".join(str(value or "").split())
    lower = normalized.lower()
    mappings = {
        "reserve fund": "借款申请",
        "payment request": "付款审批",
        "reimbursement - copy": "报销审批",
        "reimbursement": "报销审批",
    }
    return mappings.get(lower, normalized)


def _short_text(value: Any, max_length: int = 80) -> str:
    text = " ".join(str(value or "").replace("\n", " ").split())
    if not text:
        return ""
    return text if len(text) <= max_length else f"{text[:max_length]}..."
