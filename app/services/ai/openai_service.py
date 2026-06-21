import json
from hashlib import sha256
from typing import Any

from app.core.config import settings
from app.services.llm import LLMGateway


class AIService:
    def __init__(self) -> None:
        self.llm = LLMGateway()
        self.provider = self.llm.provider
        self.model = self.llm.model
        self.client = self.llm.client
        self.openai_client = self.llm.openai_client
        self.deepseek_client = self.llm.deepseek_client
        self.embedding_client = self.llm.embedding_client

    def complete_text(self, prompt: str, *, temperature: float = 0.2) -> str | None:
        return self.llm.complete_text(prompt, temperature=temperature)

    def complete_reasoning_text(self, prompt: str, *, temperature: float = 0.2) -> str | None:
        return self.llm.complete_reasoning_text(prompt, temperature=temperature)

    def complete_openai_text(self, prompt: str, *, temperature: float = 0.2) -> str | None:
        return self.llm.complete_openai_text(prompt, temperature=temperature)

    def complete_deepseek_text(self, prompt: str, *, temperature: float = 0.2) -> str | None:
        return self.llm.complete_deepseek_text(prompt, temperature=temperature)

    def extract_items(self, event_text: str) -> list[dict[str, Any]]:
        if not event_text.strip():
            return []
        if not self.client:
            return heuristic_extract_items(event_text)

        prompt = (
            "从下面工作事件中抽取任务、风险、决策。只返回 JSON 数组。"
            "字段: item_type(task|risk|decision), title, description, owner, due_at, priority。\n\n"
            f"{event_text[:8000]}"
        )
        try:
            if self.provider == "hybrid" and settings.deepseek_use_for_extraction:
                text = self.complete_deepseek_text(prompt, temperature=0.1) or ""
            elif settings.openai_use_for_extraction:
                text = self.complete_text(prompt, temperature=0.1) or ""
            else:
                return heuristic_extract_items(event_text)
            parsed = json.loads(text)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return heuristic_extract_items(event_text)

    def daily_report(self, events: list[dict[str, Any]]) -> str:
        if not events:
            return "## 今日工作日报\n\n暂无可汇总的工作事件。"

        compact = "\n".join(
            f"- [{event['source']}/{event['event_type']}] {event.get('title') or ''}: "
            f"{(event.get('content_text') or '')[:500]}"
            for event in events
        )
        use_deepseek_reports = self.provider == "hybrid" and settings.deepseek_use_for_reports
        if not self.client or not (settings.openai_use_for_reports or use_deepseek_reports):
            return fallback_daily_report(events)

        prompt = f"""
你是个人数字参谋。请基于工作事件生成中文日报，结构必须包含：
1. 今日重点
2. 任务进展
3. 风险与阻塞
4. 已形成决策
5. 明日建议

工作事件：
{compact[:16000]}
"""
        try:
            if use_deepseek_reports:
                return self.complete_deepseek_text(prompt, temperature=0.2) or fallback_daily_report(events)
            if self.provider == "hybrid":
                return self.complete_openai_text(prompt, temperature=0.2) or fallback_daily_report(events)
            return self.complete_text(prompt, temperature=0.2) or fallback_daily_report(events)
        except Exception:
            return fallback_daily_report(events)

    def embedding(self, text: str) -> list[float] | None:
        return self.llm.embedding(text)


def heuristic_extract_items(text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    keywords = {
        "task": ["todo", "待办", "跟进", "完成", "负责", "安排"],
        "risk": ["风险", "阻塞", "延期", "依赖", "问题", "不确定"],
        "decision": ["决定", "决策", "拍板", "同意", "批准", "审批通过", "审批拒绝"],
    }
    for line in lines[:20]:
        if _is_non_actionable_heading(line):
            continue
        for item_type, words in keywords.items():
            if _is_low_signal_operational_notice(line, item_type=item_type):
                continue
            if any(word.lower() in line.lower() for word in words):
                items.append(
                    {
                        "item_type": item_type,
                        "title": line[:120],
                        "description": line,
                        "owner": None,
                        "due_at": None,
                        "priority": "medium",
                    }
                )
                break
    return items[:10]


def fallback_daily_report(events: list[dict[str, Any]]) -> str:
    by_source: dict[str, int] = {}
    task_like: list[str] = []
    risk_like: list[str] = []
    decision_like: list[str] = []
    for event in events:
        by_source[event["source"]] = by_source.get(event["source"], 0) + 1
        text = f"{event.get('title') or ''} {(event.get('content_text') or '')}".strip()
        low = text.lower()
        if any(k in low for k in ["todo", "待办", "跟进", "完成"]):
            task_like.append(text[:120])
        if any(k in low for k in ["风险", "阻塞", "延期", "问题"]) and not _is_low_signal_operational_notice(
            text, item_type="risk"
        ):
            risk_like.append(text[:120])
        if any(k in low for k in ["决定", "决策", "拍板", "同意", "批准"]) and not _is_low_signal_operational_notice(
            text, item_type="decision"
        ):
            decision_like.append(text[:120])

    source_line = "、".join(f"{source} {count} 条" for source, count in by_source.items())
    digest = sha256("".join(str(event.get("id")) for event in events).encode()).hexdigest()[:8]
    return "\n".join(
        [
            "## 今日工作日报",
            "",
            f"摘要编号: {digest}",
            f"今日共汇总 {len(events)} 条工作事件，来源：{source_line}。",
            "",
            "### 今日重点",
            *[f"- {event.get('title') or event.get('event_type')}" for event in events[:8]],
            "",
            "### 任务进展",
            *(f"- {item}" for item in task_like[:8]),
            "",
            "### 风险与阻塞",
            *(f"- {item}" for item in risk_like[:8]),
            "",
            "### 已形成决策",
            *(f"- {item}" for item in decision_like[:8]),
            "",
            "### 明日建议",
            "- 优先跟进未闭环任务，确认风险负责人，并把关键决策同步到相关群组或邮件线程。",
        ]
    )


def is_finance_document_notice(text: str) -> bool:
    value = str(text or "")
    if not value:
        return False
    normalized = value
    for neutral_phrase in ("有问题随时沟通", "如有问题请", "有问题请", "如有疑问", "若有疑问"):
        normalized = normalized.replace(neutral_phrase, "")
    finance_doc_terms = ["工资明细", "薪资明细", "工资汇总", "薪资汇总", "工资总表", "薪资总表"]
    neutral_terms = ["附件", "汇总表", "请核对", "请查收", "明细", "总表"]
    risk_terms = ["异常", "差异", "错误", "逾期", "未发", "未付", "拖欠", "投诉", "风险", "问题"]
    return (
        any(term in value for term in finance_doc_terms)
        and any(term in value for term in neutral_terms)
        and not any(term in normalized for term in risk_terms)
    )


def _is_finance_document_notice(text: str) -> bool:
    return is_finance_document_notice(text)


def is_low_signal_operational_notice(text: str, *, item_type: str | None = None) -> bool:
    value = str(text or "")
    if not value:
        return False
    if is_finance_document_notice(value):
        return True

    strong_terms = ["异常", "差异", "错误", "逾期", "未付", "拖欠", "投诉", "风险", "阻塞", "延期", "违约", "事故"]
    business_terms = ["付款", "报销", "合同", "采购", "回款", "客户", "订单", "项目", "审批"]
    notice_terms = [
        "通知",
        "提醒",
        "规范",
        "模板",
        "指引",
        "录用通知书",
        "请查阅",
        "请及时",
        "请联系",
        "欢迎使用",
        "使用指南",
        "帮助中心",
    ]
    headline = _notice_headline(value)
    if (
        any(term in headline for term in notice_terms)
        and not any(term in headline for term in strong_terms)
        and not (any(term in headline for term in business_terms) and "录用通知书" not in headline)
    ):
        return item_type in {"risk", "decision", "task"}

    if any(term in value for term in strong_terms):
        return False
    if any(term in value for term in business_terms) and "录用通知书" not in value:
        return False

    weak_trigger_terms = ["问题", "疑问", "确认", "同意", "回复"]
    if not any(term in value for term in notice_terms):
        return False
    if item_type in {"risk", "decision", "task"}:
        return any(term in value for term in weak_trigger_terms) or item_type in {"decision", "task"}
    return False


def _is_low_signal_operational_notice(text: str, *, item_type: str | None = None) -> bool:
    return is_low_signal_operational_notice(text, item_type=item_type)


def _notice_headline(value: str) -> str:
    stripped = value.strip()
    try:
        parsed = json.loads(stripped)
    except Exception:
        return stripped[:240]
    if isinstance(parsed, dict):
        subject = parsed.get("subject") or parsed.get("title")
        if subject:
            return str(subject)[:240]
    return stripped[:240]


def _is_non_actionable_heading(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return True
    if value.lstrip("#").strip() in {"今日重点", "任务进展", "风险与阻塞", "已形成决策", "明日建议"}:
        return True
    return False
