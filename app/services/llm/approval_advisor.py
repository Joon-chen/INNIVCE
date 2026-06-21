import json
import os
from typing import Any

from app.core.config import settings
from app.services.llm.gateway import LLMGateway


VALID_APPROVAL_CONCLUSIONS = {"可通过", "谨慎通过", "先展开附件", "补充后再审", "拒绝"}


def generate_approval_llm_advice(
    *,
    approval_name: str,
    fields: dict[str, str],
    amount: float | None,
    attachment_summary: str,
    history: list[str],
    rule_conclusion: str,
    rule_reason: str,
) -> dict[str, str] | None:
    if not settings.approval_llm_advice_enabled or _running_tests():
        return None
    prompt = _approval_advice_prompt(
        approval_name=approval_name,
        fields=fields,
        amount=amount,
        attachment_summary=attachment_summary,
        history=history,
        rule_conclusion=rule_conclusion,
        rule_reason=rule_reason,
    )
    try:
        raw = LLMGateway().complete_reasoning_text(prompt, temperature=0.15)
    except Exception:
        return None
    return parse_approval_llm_advice(raw or "")


def parse_approval_llm_advice(raw: str) -> dict[str, str] | None:
    data = _json_object(raw)
    if not data:
        return None
    conclusion = str(data.get("conclusion") or "").strip()
    concise_reason = str(data.get("concise_reason") or "").strip()
    detailed_reason = str(data.get("detailed_reason") or "").strip()
    if conclusion not in VALID_APPROVAL_CONCLUSIONS or not concise_reason or not detailed_reason:
        return None
    return {
        "conclusion": conclusion,
        "concise_reason": concise_reason[:120],
        "detailed_reason": detailed_reason[:700],
        "source": "llm",
    }


def _approval_advice_prompt(
    *,
    approval_name: str,
    fields: dict[str, str],
    amount: float | None,
    attachment_summary: str,
    history: list[str],
    rule_conclusion: str,
    rule_reason: str,
) -> str:
    return f"""你是企业老板的数字参谋，负责审批前的经营判断。

硬性规则：
- 只能基于给定表单、附件/OCR摘要、同类历史和规则底线判断。
- 不得编造不存在的合同条款、发票号码、供应商背景、预算余额或历史记录。
- 不要把系统 OCR 不完整等同于申请人缺材料。
- 无附件不等于缺材料；借款、请假等不强依赖附件的审批，不得因为没有附件/OCR摘要提出附件缺失风险。
- 如果附件摘要足以支持判断，要给明确建议；如果摘要不足，要说明需要确认什么。
- 风险点和二次确认问题都写入 detailed_reason，不要单独输出字段。
- 只能输出 JSON，不要 Markdown。

允许的 conclusion 只能是：
可通过 / 谨慎通过 / 先展开附件 / 补充后再审 / 拒绝

规则底线：
conclusion={rule_conclusion}
reason={rule_reason}

审批类型：{approval_name}
金额：{amount if amount is not None else "未识别"}
表单字段：
{json.dumps(fields, ensure_ascii=False, default=str)[:2500]}

附件/OCR摘要：
{attachment_summary or "无可读附件摘要"}

同类历史审批经验：
{json.dumps(history[:6], ensure_ascii=False, default=str)}

请输出 JSON：
{{
  "conclusion": "可通过|谨慎通过|先展开附件|补充后再审|拒绝",
  "concise_reason": "一句话，给老板列表里看，40字以内",
  "detailed_reason": "详细理由，必须包含主要依据、风险点、二次确认问题，200字以内"
}}"""


def _json_object(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if not text:
        return None
    if text.startswith("```"):
        text = text.strip("`")
        text = text.removeprefix("json").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _running_tests() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))
