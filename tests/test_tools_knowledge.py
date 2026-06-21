from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.services.tools.knowledge import answer_public_knowledge_question


class _ScalarResult:
    def __init__(self, items):
        self.items = items

    def all(self):
        return self.items


class _FakeDb:
    def __init__(self, results):
        self.results = list(results)

    def scalars(self, query):
        return _ScalarResult(self.results.pop(0))


def test_answer_public_knowledge_question_uses_public_facts_and_documents() -> None:
    fact = SimpleNamespace(
        id=uuid4(),
        fact_type="people_or_policy",
        subject="报销流程",
        content="员工报销需提交发票、事由、项目归属，并按审批流程提交。",
    )
    event = SimpleNamespace(
        id=uuid4(),
        event_type="feishu.docx.content",
        title="报销制度文档",
        content_text="报销前需确认预算、发票和项目编码。",
        sensitivity="normal",
        occurred_at=datetime.now(UTC),
    )

    answer = answer_public_knowledge_question(
        _FakeDb([[fact], [event]]),
        company_id=uuid4(),
        question="报销流程怎么做",
    )

    assert "公开知识/流程制度范围" in answer
    assert "报销流程" in answer
    assert "报销制度文档" in answer
    assert "不包含老板私有数据" in answer


def test_answer_public_knowledge_question_filters_sensitive_facts() -> None:
    fact = SimpleNamespace(
        id=uuid4(),
        fact_type="compensation",
        subject="薪资制度",
        content="工资奖金明细。",
    )

    answer = answer_public_knowledge_question(
        _FakeDb([[fact], []]),
        company_id=uuid4(),
        question="薪资制度怎么做",
    )

    assert "没有在已同步的公开知识" in answer
    assert "工资奖金明细" not in answer


def test_answer_public_knowledge_question_filters_sensitive_events() -> None:
    event = SimpleNamespace(
        id=uuid4(),
        event_type="feishu.docx.content",
        title="客户订单模板",
        content_text="客户订单和回款记录。",
        sensitivity="normal",
        occurred_at=datetime.now(UTC),
    )

    answer = answer_public_knowledge_question(
        _FakeDb([[], [event]]),
        company_id=uuid4(),
        question="客户订单模板",
    )

    assert "没有在已同步的公开知识" in answer
    assert "客户订单和回款记录" not in answer
