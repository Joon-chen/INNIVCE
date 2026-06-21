from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.services.tools.domain import answer_domain_question
from app.services.tools.evidence import evidence_line, wants_evidence_detail
from app.services.agent.policies import BotActor


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


def test_evidence_line_is_short_by_default() -> None:
    line = evidence_line("已同步审批记录", 1, latest_at=datetime(2026, 6, 10, 15, 42, tzinfo=UTC))

    assert line == "依据：已同步审批记录 1 条，最新时间 2026-06-10 15:42；回复“展开依据”可查看明细。"


def test_wants_evidence_detail_detects_expand_requests() -> None:
    assert wants_evidence_detail("展开依据给我看看") is True
    assert wants_evidence_detail("最近财务有什么风险") is False


def test_domain_answer_keeps_evidence_compact_until_expanded() -> None:
    event = SimpleNamespace(
        id=uuid4(),
        title="财务付款审批同步",
        content_text="供应商付款审批金额较高，需要确认预算归属。",
        event_type="feishu.approvals.instance",
        occurred_at=datetime(2026, 6, 10, 9, 0, tzinfo=UTC),
    )
    actor = BotActor(role="manager", access_scope="domain", domains=("finance", "approval"))

    compact = answer_domain_question(
        _FakeDb([[event], [], []]),
        company_id=uuid4(),
        actor=actor,
        question="最近财务资金有什么风险",
    )
    expanded = answer_domain_question(
        _FakeDb([[event], [], []]),
        company_id=uuid4(),
        actor=actor,
        question="最近财务资金有什么风险，展开依据",
    )

    assert "依据：" in compact
    assert "证据明细" not in compact
    assert "证据明细" in expanded
