from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.services.tools.task import answer_task_question


class _ScalarResult:
    def __init__(self, items):
        self.items = items

    def all(self):
        return self.items


class _FakeDb:
    def __init__(self, items):
        self.items = items

    def scalars(self, query):
        return _ScalarResult(self.items)


def test_answer_task_question_summarizes_open_tasks() -> None:
    task = SimpleNamespace(
        id=uuid4(),
        title="跟进客户报价",
        owner="王工",
        due_at=datetime(2026, 6, 15, 18, 0, tzinfo=UTC),
        priority="high",
        created_at=datetime(2026, 6, 12, 8, 0, tzinfo=UTC),
    )

    answer = answer_task_question(_FakeDb([task]), company_id=uuid4())

    assert "开放待办" in answer
    assert "跟进客户报价" in answer
    assert "王工" in answer


def test_answer_task_question_handles_empty_tasks() -> None:
    answer = answer_task_question(_FakeDb([]), company_id=uuid4())

    assert "没有在已抽取数据里看到开放待办" in answer
