from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.services.tools.calendar import answer_calendar_question


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


def test_answer_calendar_question_summarizes_synced_events() -> None:
    event = SimpleNamespace(
        id=uuid4(),
        company_id=uuid4(),
        event_type="feishu.calendar.event",
        title="客户评审会议",
        content_text="今天 10 点和客户评审项目进展。",
        business_domain="meeting",
        occurred_at=datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    )

    answer = answer_calendar_question(_FakeDb([event]), company_id=uuid4(), question="今天有什么会议")

    assert "已同步日程/会议记录" in answer
    assert "客户评审会议" in answer
    assert "项目进展" in answer


def test_answer_calendar_question_handles_empty_sync() -> None:
    answer = answer_calendar_question(_FakeDb([]), company_id=uuid4(), question="今天有什么会议")

    assert "还没有读到已同步入库的日程或会议记录" in answer

