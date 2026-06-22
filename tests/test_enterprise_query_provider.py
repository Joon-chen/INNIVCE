import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.services.agent.policies import BotActor
from app.services.tools.base import ToolContext, ToolRequest
from app.services.tools.providers.feishu_mcp import execute_feishu_mcp_tool


class _ScalarResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return list(self._values)


class _FakeDb:
    def __init__(self, values):
        self.values = values

    def scalars(self, _query):
        return _ScalarResult(self.values)


def _context(company_id):
    return ToolContext(
        db=_FakeDb([]),
        company_id=company_id,
        actor=BotActor(role="owner", access_scope="company", open_id="ou_user", display_name="员工A"),
    )


def test_task_qa_consumes_enterprise_scope_filter_from_db(monkeypatch):
    company_id = uuid4()
    task = SimpleNamespace(
        id=uuid4(),
        title="明天4点开会",
        owner="员工A",
        status="open",
        due_at=None,
        payload={"task_guid": "task-1", "owner_open_id": "ou_user", "summary": "明天4点开会"},
        created_at=datetime(2026, 6, 22, tzinfo=UTC),
    )
    context = _context(company_id)
    context = ToolContext(db=_FakeDb([task]), company_id=context.company_id, actor=context.actor)

    def fail_cli(*_args, **_kwargs):
        raise AssertionError("enterprise scope query must not fall back to CLI")

    monkeypatch.setattr("app.services.tools.providers.feishu_mcp._run_lark_cli_json", fail_cli)

    response = execute_feishu_mcp_tool(
        context,
        ToolRequest(
            tool_name="task_qa",
            question="查看我的任务",
            normalized_command="查看我的任务",
            params={"response_format": "raw_json", "scope_filter": {"scope": "self", "actor_open_id": "ou_user"}},
        ),
    )

    payload = json.loads(response)
    assert payload["source"] == "enterprise_query"
    assert payload["query_boundary"] == "bot_enterprise_scope_filter"
    assert payload["items"][0]["summary"] == "明天4点开会"


def test_calendar_qa_consumes_enterprise_scope_filter_from_db(monkeypatch):
    company_id = uuid4()
    event = SimpleNamespace(
        id=uuid4(),
        event_type="feishu.calendar.event",
        title="明天下午5点开会",
        content_text="明天下午5点开会",
        business_domain="calendar",
        object_id="event-1",
        external_id="event-1",
        actor="ou_user",
        payload={"event_id": "event-1", "owner_open_id": "ou_user", "summary": "明天下午5点开会"},
        raw_json={},
        occurred_at=datetime(2026, 6, 23, 17, 0, tzinfo=UTC),
    )
    context = _context(company_id)
    context = ToolContext(db=_FakeDb([event]), company_id=context.company_id, actor=context.actor)

    def fail_cli(*_args, **_kwargs):
        raise AssertionError("enterprise scope query must not fall back to CLI")

    monkeypatch.setattr("app.services.tools.providers.feishu_mcp._run_lark_cli_json", fail_cli)

    response = execute_feishu_mcp_tool(
        context,
        ToolRequest(
            tool_name="calendar_qa",
            question="查看我的日程",
            normalized_command="查看我的日程",
            params={"response_format": "raw_json", "scope_filter": {"scope": "self", "actor_open_id": "ou_user"}},
        ),
    )

    payload = json.loads(response)
    assert payload["source"] == "enterprise_query"
    assert payload["query_boundary"] == "bot_enterprise_scope_filter"
    assert payload["items"][0]["summary"] == "明天下午5点开会"
