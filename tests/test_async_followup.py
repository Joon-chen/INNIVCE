from app.services.runtime_v5 import async_followup as async_followup_module
from app.services.llm.call_budget import current_llm_call_budget_summary
from app.services.runtime_v5.async_followup import build_async_followup_text, should_schedule_async_followup


def test_should_schedule_async_followup_only_when_policy_allows() -> None:
    assert should_schedule_async_followup({}) is False
    assert (
        should_schedule_async_followup(
            {
                "metadata": {
                    "response_policy": {
                        "response_mode": "async_followup",
                        "async_followup_allowed": True,
                    }
                }
            }
        )
        is True
    )


def test_async_followup_uses_insight_generation_route(monkeypatch) -> None:
    class FakeGateway:
        def complete_task_text(self, prompt: str, *, task_type: str, temperature: float = 0.2) -> str:
            assert task_type == "insight_generation"
            assert "Async Follow-up V0" in prompt
            assert "公司风险" in prompt
            return "基于刚才结果，建议先看逾期和负责人分布。"

    monkeypatch.setattr(async_followup_module, "LLMGateway", lambda: FakeGateway())

    text = build_async_followup_text(
        {
            "question": "分析公司风险",
            "answer": "公司风险：有 2 个逾期事项。",
            "strategy": "general_analysis",
            "result_type": "general_analysis",
            "data_scope": "company",
        }
    )

    assert text == "补充分析：基于刚才结果，建议先看逾期和负责人分布。"


def test_async_followup_uses_background_budget(monkeypatch) -> None:
    captured = {}

    class FakeGateway:
        def complete_task_text(self, prompt: str, *, task_type: str, temperature: float = 0.2) -> str:
            captured.update(current_llm_call_budget_summary())
            return "可以稍后补充更完整的趋势分析。"

    monkeypatch.setattr(async_followup_module, "LLMGateway", lambda: FakeGateway())

    text = build_async_followup_text(
        {
            "question": "分析公司风险",
            "answer": "公司风险：有 2 个逾期事项。",
        }
    )

    assert "补充分析" in text
    assert captured["available"] is True
    assert captured["turn_type"] == "background_cognitive_job"
    assert captured["max_calls"] == 2


def test_async_followup_falls_back_without_new_facts(monkeypatch) -> None:
    class FakeGateway:
        def complete_task_text(self, prompt: str, *, task_type: str, temperature: float = 0.2) -> str:
            raise RuntimeError("llm unavailable")

    monkeypatch.setattr(async_followup_module, "LLMGateway", lambda: FakeGateway())

    text = build_async_followup_text(
        {
            "question": "查看公司任务",
            "answer": "任务企业实时读取能力还没有接入 Bot/Tenant 主路径。",
        }
    )

    assert "补充分析：" in text
    assert "不能用个人身份或本地缓存代查" in text


def test_async_followup_rejects_unseen_specific_terms(monkeypatch) -> None:
    class FakeGateway:
        def complete_task_text(self, prompt: str, *, task_type: str, temperature: float = 0.2) -> str:
            return "可以继续追问合同、付款、税务等风险类别。"

    monkeypatch.setattr(async_followup_module, "LLMGateway", lambda: FakeGateway())

    text = build_async_followup_text(
        {
            "question": "分析公司风险",
            "answer": "公司风险：有 2 个逾期事项。",
        }
    )

    assert "合同" not in text
    assert "付款" not in text
    assert "税务" not in text
    assert "当前先返回已可确认的结果" in text
