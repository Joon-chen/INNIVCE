from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.services.tools.personal import answer_personal_tasks, personal_task_access_boundary
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


def test_answer_personal_tasks_uses_extracted_task_owner() -> None:
    task = SimpleNamespace(
        id=uuid4(),
        title="完成测试报告",
        owner="王工",
        due_at=None,
        status="open",
        created_at=datetime.now(UTC),
    )
    actor = BotActor(role="member", access_scope="chat", display_name="王工", open_id="ou_1")

    answer = answer_personal_tasks(_FakeDb([[task]]), company_id=uuid4(), actor=actor)

    assert "本人相关开放待办" in answer
    assert "完成测试报告" in answer


def test_answer_personal_tasks_falls_back_to_related_events() -> None:
    event = SimpleNamespace(
        id=uuid4(),
        title="王工跟进客户现场",
        content_text="请王工明天跟进客户现场调试问题",
        event_type="im.message",
        occurred_at=datetime.now(UTC),
    )
    actor = BotActor(role="member", access_scope="chat", display_name="王工", open_id="ou_1")

    answer = answer_personal_tasks(_FakeDb([[], [event]]), company_id=uuid4(), actor=actor)

    assert "这些工作事件和你有关" in answer
    assert "王工跟进客户现场" in answer


def test_answer_personal_tasks_requires_identity() -> None:
    actor = BotActor(role="member", access_scope="chat")

    answer = answer_personal_tasks(_FakeDb([]), company_id=uuid4(), actor=actor)

    assert "没有拿到你的 Open ID 或邮箱" in answer


def test_personal_task_access_boundary_requires_strong_identity() -> None:
    weak = personal_task_access_boundary(BotActor(role="member", access_scope="personal", display_name="王工"))
    strong = personal_task_access_boundary(
        BotActor(role="member", access_scope="personal", display_name="王工", open_id="ou_1")
    )

    assert weak["has_strong_identity"] is False
    assert strong["has_strong_identity"] is True
    assert strong["data_access_scope"] == "personal"
    assert strong["personal_owner_open_id"] == "ou_1"
    assert strong["cross_user_data_allowed"] is False
