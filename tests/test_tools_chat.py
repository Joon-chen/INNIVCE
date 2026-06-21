from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.services.tools.chat import (
    answer_current_chat_question,
    answer_current_chat_summary,
    answer_current_chat_tasks,
)
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


def test_answer_current_chat_summary_uses_current_chat_events() -> None:
    company_id = uuid4()
    event = SimpleNamespace(
        id=uuid4(),
        company_id=company_id,
        source="feishu",
        thread_id="oc_group",
        event_type="im.message",
        title="客户现场问题",
        content_text="客户反馈设备联调需要明天继续跟进",
        occurred_at=datetime.now(UTC),
    )

    answer = answer_current_chat_summary(_FakeDb([[event]]), company_id=company_id, chat_id="oc_group")

    assert "最近 1 条已入库消息" in answer
    assert "客户现场问题" in answer


def test_answer_current_chat_tasks_uses_extracted_tasks_first() -> None:
    company_id = uuid4()
    event = SimpleNamespace(
        id=uuid4(),
        company_id=company_id,
        source="feishu",
        thread_id="oc_group",
        event_type="im.message",
        title="项目跟进",
        content_text="请王工明天跟进测试报告",
        occurred_at=datetime.now(UTC),
    )
    task = SimpleNamespace(
        id=uuid4(),
        title="王工跟进测试报告",
        owner="王工",
        due_at=None,
        status="open",
        created_at=datetime.now(UTC),
    )

    answer = answer_current_chat_tasks(_FakeDb([[event], [task]]), company_id=company_id, chat_id="oc_group")

    assert "开放待办" in answer
    assert "王工跟进测试报告" in answer


def test_answer_current_chat_tasks_falls_back_to_task_like_events() -> None:
    company_id = uuid4()
    event = SimpleNamespace(
        id=uuid4(),
        company_id=company_id,
        source="feishu",
        thread_id="oc_group",
        event_type="im.message",
        title="项目跟进",
        content_text="待办：明天完成测试报告",
        occurred_at=datetime.now(UTC),
    )

    answer = answer_current_chat_tasks(_FakeDb([[event], []]), company_id=company_id, chat_id="oc_group")

    assert "像是需要跟进" in answer
    assert "明天完成测试报告" in answer


def test_answer_current_chat_question_handles_identity_without_database() -> None:
    actor = BotActor(role="owner", access_scope="company", domains=("all",))

    answer = answer_current_chat_question(
        _FakeDb([]),
        company_id=uuid4(),
        chat_id="oc_group",
        question="你是谁",
        normalized_command="机器人身份",
        actor=actor,
    )

    assert "企业数字助理" in answer
    assert "系统所有者" in answer


def test_answer_current_chat_question_uses_only_current_chat_events() -> None:
    company_id = uuid4()
    event = SimpleNamespace(
        id=uuid4(),
        company_id=company_id,
        source="feishu",
        thread_id="oc_group",
        event_type="im.message",
        title="客户现场问题",
        content_text="客户现场测试设备需要明天继续跟进",
        occurred_at=datetime.now(UTC),
    )
    actor = BotActor(role="member", access_scope="chat")

    answer = answer_current_chat_question(
        _FakeDb([[event]]),
        company_id=company_id,
        chat_id="oc_group",
        question="客户现场怎么样",
        normalized_command="客户现场怎么样",
        actor=actor,
    )

    assert "只按当前会话" in answer
    assert "客户现场问题" in answer
