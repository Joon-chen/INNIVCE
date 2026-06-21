from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.services.tools.domain import answer_domain_question
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


def test_answer_domain_question_summarizes_authorized_domain_data() -> None:
    event = SimpleNamespace(
        id=uuid4(),
        title="财务付款审批同步",
        content_text="供应商付款审批金额较高，需要确认预算归属。",
        event_type="feishu.approvals.instance",
        occurred_at=datetime(2026, 6, 10, 9, 0, tzinfo=UTC),
    )
    risk = SimpleNamespace(
        id=uuid4(),
        title="付款审批金额风险",
        description="单笔付款金额较高，凭证需要复核。",
        owner="财务负责人",
        due_at=None,
        priority="high",
    )
    task = SimpleNamespace(
        id=uuid4(),
        title="复核付款凭证",
        description="确认供应商合同、发票和项目归属。",
        owner="财务负责人",
        due_at=None,
        priority="medium",
    )
    actor = BotActor(role="manager", access_scope="domain", domains=("finance", "approval"))

    answer = answer_domain_question(
        _FakeDb([[event], [risk], [task]]),
        company_id=uuid4(),
        actor=actor,
        question="最近财务资金有什么风险",
    )

    assert "授权业务域（finance、approval）" in answer
    assert "命中工作事件 1 条、开放风险 1 条、开放待办 1 条" in answer
    assert "付款审批金额风险" in answer
    assert "复核付款凭证" in answer
    assert "财务付款审批同步" in answer


def test_answer_domain_question_requires_authorized_domains() -> None:
    actor = BotActor(role="member", access_scope="chat")

    answer = answer_domain_question(
        _FakeDb([]),
        company_id=uuid4(),
        actor=actor,
        question="最近财务有什么风险",
    )

    assert "还没有拿到你的授权业务域" in answer


def test_answer_domain_question_handles_no_matches() -> None:
    actor = BotActor(role="manager", access_scope="domain", domains=("sales",))

    answer = answer_domain_question(
        _FakeDb([[], [], []]),
        company_id=uuid4(),
        actor=actor,
        question="最近销售有什么重点",
    )

    assert "授权业务域（sales）" in answer
    assert "暂时没有命中" in answer
