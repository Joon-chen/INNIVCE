from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.services.tools.mail import answer_mail_question


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


def test_answer_mail_question_summarizes_synced_mail() -> None:
    event = SimpleNamespace(
        id=uuid4(),
        company_id=uuid4(),
        event_type="mail.message",
        title="客户付款邮件",
        content_text="客户确认本周安排付款。",
        source_type="external_mail_account",
        occurred_at=datetime(2026, 6, 12, 9, 0, tzinfo=UTC),
    )

    answer = answer_mail_question(_FakeDb([event]), company_id=uuid4(), question="最近邮件有什么")

    assert "已同步邮件记录" in answer
    assert "客户付款邮件" in answer
    assert "只向老板/所有者权限开放" in answer


def test_answer_mail_question_handles_empty_sync() -> None:
    answer = answer_mail_question(_FakeDb([]), company_id=uuid4(), question="最近邮件")

    assert "还没有读到已同步入库的邮件记录" in answer
