from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.services.tools.approval import answer_approval_question


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


def _approval_event(*, status: str, title: str = "付款审批", amount: int | None = None):
    payload = {"item": {"status": status}}
    if amount is not None:
        payload["item"]["amount"] = amount
    return SimpleNamespace(
        id=uuid4(),
        company_id=uuid4(),
        source="feishu",
        event_type="feishu.approvals.instance",
        title=title,
        content_text=f"{title} 金额 {amount}" if amount else title,
        occurred_at=datetime(2026, 6, 10, 8, 30, tzinfo=UTC),
        payload=payload,
    )


def test_approval_question_summarizes_local_approval_events() -> None:
    answer = answer_approval_question(
        _FakeDb([_approval_event(status="approved"), _approval_event(status="pending")]),
        company_id=uuid4(),
        question="最近审批怎么样",
    )

    assert "已同步审批记录" in answer
    assert "approved 1 条" in answer
    assert "pending 1 条" in answer


def test_approval_question_lists_pending_events() -> None:
    answer = answer_approval_question(
        _FakeDb([_approval_event(status="pending", title="付款审批", amount=50000)]),
        company_id=uuid4(),
        question="有哪些待审批",
    )

    assert "未完成审批实例" in answer
    assert "不保证全都是待你本人审批" in answer
    assert "金额：50000" in answer


def test_approval_advice_is_read_only_and_amount_aware() -> None:
    answer = answer_approval_question(
        _FakeDb([_approval_event(status="pending", title="付款审批", amount=50000)]),
        company_id=uuid4(),
        question="这个审批建议同意还是拒绝",
    )

    assert "只读建议" in answer
    assert "金额较高" in answer
    assert "不会在这里直接提交审批动作" in answer


def test_approval_question_extracts_amount_from_feishu_form_json() -> None:
    event = _approval_event(status="pending", title="付款审批")
    event.payload = {
        "item": {
            "status": "pending",
            "form": '[{"name":"付款金额","value":22000},{"name":"付款事由","value":"投标保证金"}]',
        }
    }

    answer = answer_approval_question(
        _FakeDb([event]),
        company_id=uuid4(),
        question="有哪些待审批",
    )

    assert "金额：22000" in answer
