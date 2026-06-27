from __future__ import annotations

from typing import Any

from app.services.llm.call_budget import background_cognitive_job_budget
from app.services.llm.gateway import LLMGateway


def should_schedule_async_followup(runtime_result: dict[str, Any] | None) -> bool:
    if not isinstance(runtime_result, dict):
        return False
    metadata = runtime_result.get("metadata") if isinstance(runtime_result.get("metadata"), dict) else {}
    response_policy = metadata.get("response_policy") if isinstance(metadata.get("response_policy"), dict) else {}
    return bool(response_policy.get("async_followup_allowed"))


def build_async_followup_text(payload: dict[str, Any]) -> str:
    answer = str(payload.get("answer") or "").strip()
    question = str(payload.get("question") or "").strip()
    if not answer:
        return ""
    prompt = _async_followup_prompt(question=question, answer=answer, payload=payload)
    try:
        with background_cognitive_job_budget(max_calls=2):
            text = LLMGateway().complete_task_text(prompt, task_type="insight_generation", temperature=0.2)
    except Exception:
        text = ""
    cleaned = _clean_followup_text(text, answer=answer)
    if not cleaned:
        cleaned = _deterministic_followup(answer)
    if not cleaned.startswith("补充分析"):
        cleaned = f"补充分析：{cleaned}"
    return cleaned[:1000]


def _async_followup_prompt(*, question: str, answer: str, payload: dict[str, Any]) -> str:
    return f"""Async Follow-up V0

你是 Digital Advisor 的异步补充分析层。

边界：
- 只基于首条回答和已授权结果做补充。
- 不新增事实、数量、对象、权限结论。
- 不举首条回答以外的业务类别示例。
- 不生成动作，不要求用户回到飞书原生页面。
- 如果信息不足，只说明可继续追问的通用方向，例如具体对象、负责人、时间范围、影响范围。
- 输出中文，最多 300 字，先结论后依据。

用户问题：
{question}

首条回答：
{answer[:1800]}

Runtime 信息：
strategy={payload.get("strategy") or ""}
result_type={payload.get("result_type") or ""}
data_scope={payload.get("data_scope") or ""}
"""


def _clean_followup_text(text: str | None, *, answer: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    blocked = ("以下是改写", "好的，收到", "作为一个AI", "我无法访问")
    if any(item in cleaned for item in blocked):
        return ""
    if _contains_unseen_specific_terms(cleaned, answer=answer):
        return ""
    return cleaned


def _contains_unseen_specific_terms(text: str, *, answer: str) -> bool:
    watched_terms = (
        "合同",
        "付款",
        "合规",
        "罚金",
        "违约",
        "供应商",
        "客户",
        "金额",
        "监管",
        "法律",
        "税务",
        "贷款",
    )
    return any(term in text and term not in answer for term in watched_terms)


def _deterministic_followup(answer: str) -> str:
    if "未接入" in answer or "没有接入" in answer:
        return "补充分析：当前只能先返回能力边界。下一步应补齐对应 Bot/Tenant 实时读取能力，或用已授权同步数据生成聚合认知，不能用个人身份或本地缓存代查。"
    return "补充分析：当前先返回已可确认的结果。需要更深入判断时，可以继续追问风险原因、影响范围或下一步建议。"
