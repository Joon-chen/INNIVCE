from types import SimpleNamespace
from uuid import uuid4

from app.services.feishu.calendar import FeishuCalendarService
from app.services.feishu.task import FeishuTaskService
from app.services.runtime_v5 import feishu_resource_providers
from app.services.runtime_v5.feishu_resource_providers import FeishuAttendanceProvider, FeishuCalendarProvider, FeishuTaskProvider
from app.services.runtime_v5.feishu_user_token import resolve_feishu_user_access_token
from app.services.runtime_v5.models import (
    CredentialOwner,
    ExecutionIdentityContract,
    IntentResult,
    PlannerResult,
    ProviderRequest,
    RuntimeContext,
    RuntimeIdentity,
    RuntimeScope,
)


class _ScalarResult:
    def __init__(self, values=None):
        self._values = values or []

    def all(self):
        return list(self._values)


class _FakeDb:
    def __init__(self, *, app_config=None, accounts=None):
        self.app_config = app_config
        self.accounts = accounts or []
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    def add(self, item):
        self.added.append(item)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def scalar(self, _query):
        return self.app_config

    def scalars(self, _query):
        return _ScalarResult(self.accounts)


def _request(*, company_id, open_id="ou_user", params=None, operation="complete_task", data_scope="self") -> ProviderRequest:
    if operation in {"create_event", "list_events"}:
        source = "calendar"
        intent_name = "calendar_create" if operation == "create_event" else "calendar_query"
        if operation == "create_event":
            message = "创建一个会议：明天下午5点开会"
            default_params = {"summary": "明天下午5点开会", "start": "2026-06-23T17:00:00+08:00", "end": "2026-06-23T18:00:00+08:00"}
        else:
            message = "查看我的日程"
            default_params = {}
    else:
        source = "task"
        if operation == "create_task":
            intent_name = "task_create"
            message = "创建任务"
            default_params = {"summary": "明天4点开会"}
        elif operation == "list_my_tasks":
            intent_name = "task_query"
            message = "查看我的任务"
            default_params = {}
        else:
            intent_name = "task_complete"
            message = "完成任务"
            default_params = {"task_guid": "task-guid-1"}
    is_query = operation in {"list_events", "list_my_tasks"}
    return ProviderRequest(
        source=source,
        operation=operation,
        intent=IntentResult(
            question_type="query" if is_query else "action",
            intent=intent_name,
            data_scope=data_scope,
            confidence=0.9,
            canonical_question=message,
        ),
        planner=PlannerResult(strategy=intent_name, sources=(source,)),
        context=RuntimeContext(
            identity=RuntimeIdentity(open_id=open_id, role="owner"),
            runtime_scope=RuntimeScope(company_ids=(company_id,), active_company_id=company_id),
            current_message=message,
        ),
        execution_identity="bot" if is_query else "user",
        params=params or default_params,
        execution_identity_contract=ExecutionIdentityContract(
            actor_identity="BOT" if is_query else "USER",
            credential_mode="TENANT_TOKEN" if is_query else "USER_TOKEN",
            credential_owner=CredentialOwner(company_id=str(company_id), open_id=open_id),
            requires_authorization=not is_query,
        ),
    )


def _bot_query_request(*, company_id, source, operation, strategy, params=None, data_scope="self") -> ProviderRequest:
    return ProviderRequest(
        source=source,
        operation=operation,
        intent=IntentResult(
            question_type="query",
            intent=strategy,
            data_scope=data_scope,
            confidence=0.9,
            canonical_question=strategy,
        ),
        planner=PlannerResult(strategy=strategy, sources=(source,)),
        context=RuntimeContext(
            identity=RuntimeIdentity(open_id="ou_user", role="owner"),
            runtime_scope=RuntimeScope(company_ids=(company_id,), active_company_id=company_id),
            current_message=strategy,
        ),
        execution_identity="bot",
        params=params or {},
        execution_identity_contract=ExecutionIdentityContract(
            actor_identity="BOT",
            credential_mode="TENANT_TOKEN",
            credential_owner=CredentialOwner(company_id=str(company_id), open_id="ou_user"),
            requires_authorization=False,
        ),
    )


def test_task_query_bot_first_does_not_use_cli_or_local_realtime_sources():
    company_id = uuid4()

    class Provider(FeishuTaskProvider):
        def _execute_tool(self, *_args, **_kwargs):
            raise AssertionError("bot-first task query must not fall through to task_qa CLI")

    result = Provider(db=_FakeDb()).execute(
        _bot_query_request(company_id=company_id, source="task", operation="list_my_tasks", strategy="task_query")
    )

    assert result.status == "denied"
    assert result.result_type == "task_list"
    assert result.error == "enterprise_realtime_not_integrated"
    assert result.metadata["provider_boundary"] == "enterprise_realtime_not_integrated"
    assert result.metadata["credential_mode"] == "TENANT_TOKEN"
    assert result.metadata["legacy_cli_fallback_used"] is False
    assert result.metadata["workevent_as_realtime_source"] is False
    assert result.metadata["extracted_item_as_realtime_source"] is False
    assert result.metadata["user_fallback_allowed"] is True


def test_calendar_query_bot_first_does_not_use_cli_or_local_realtime_sources():
    company_id = uuid4()

    class Provider(FeishuCalendarProvider):
        def _execute_tool(self, *_args, **_kwargs):
            raise AssertionError("bot-first calendar query must not fall through to calendar_qa CLI")

    result = Provider(db=_FakeDb()).execute(
        _bot_query_request(company_id=company_id, source="calendar", operation="list_events", strategy="calendar_query")
    )

    assert result.status == "denied"
    assert result.result_type == "calendar_event_list"
    assert result.error == "enterprise_realtime_not_integrated"
    assert result.metadata["provider_boundary"] == "enterprise_realtime_not_integrated"
    assert result.metadata["legacy_cli_fallback_used"] is False
    assert result.metadata["workevent_as_realtime_source"] is False
    assert result.metadata["extracted_item_as_realtime_source"] is False
    assert result.metadata["user_fallback_allowed"] is True


def test_attendance_query_bot_first_does_not_use_user_cli():
    company_id = uuid4()

    result = FeishuAttendanceProvider(db=_FakeDb()).execute(
        _bot_query_request(company_id=company_id, source="attendance", operation="query_records", strategy="attendance_query")
    )

    assert result.status == "denied"
    assert result.result_type == "attendance_record_list"
    assert result.metadata["provider_boundary"] == "enterprise_realtime_not_integrated"
    assert result.metadata["current_provider"] == "lark-cli attendance user_tasks query"
    assert result.metadata["legacy_cli_fallback_used"] is False


def test_task_query_authorized_self_user_fallback_executes_provider(monkeypatch):
    company_id = uuid4()
    app_config = SimpleNamespace(id=uuid4(), company_id=company_id)
    calls = []

    class TokenResolution:
        user_access_token = "user-token"
        authorization_status = "AUTHORIZED"
        account_id = "account-1"
        error = ""
        authorized = True

    async def fake_resolve_user_token(*_args, **_kwargs):
        return TokenResolution()

    class FakeTaskService:
        def __init__(self, app_config):
            self.app_config = app_config

        async def list_tasks(self, **kwargs):
            calls.append(kwargs)
            return {"code": 0, "data": {"items": [{"guid": "task-guid-1", "summary": "跟进客户"}]}}

    monkeypatch.setattr(feishu_resource_providers, "resolve_feishu_user_access_token", fake_resolve_user_token)
    monkeypatch.setattr(feishu_resource_providers, "FeishuTaskService", FakeTaskService)

    class Provider(FeishuTaskProvider):
        def _execute_tool(self, *_args, **_kwargs):
            raise AssertionError("authorized SELF query fallback must not use CLI")

    result = Provider(db=_FakeDb(app_config=app_config)).execute(
        _bot_query_request(company_id=company_id, source="task", operation="list_my_tasks", strategy="task_query")
    )

    assert result.status == "success"
    assert result.result_type == "task_list"
    assert result.items[0]["title"] == "跟进客户"
    assert result.metadata["credential_mode"] == "USER_TOKEN"
    assert result.metadata["fallback_used"] is True
    assert result.metadata["fallback_scope"] == "SELF"
    assert result.metadata["cannot_escalate_to_company"] is True
    assert calls[0]["user_access_token"] == "user-token"


def test_calendar_query_authorized_self_user_fallback_executes_provider(monkeypatch):
    company_id = uuid4()
    app_config = SimpleNamespace(id=uuid4(), company_id=company_id)
    calls = []

    class TokenResolution:
        user_access_token = "user-token"
        authorization_status = "AUTHORIZED"
        account_id = "account-1"
        error = ""
        authorized = True

    async def fake_resolve_user_token(*_args, **_kwargs):
        return TokenResolution()

    class FakeCalendarService:
        def __init__(self, app_config):
            self.app_config = app_config

        async def list_primary_events(self, **kwargs):
            calls.append(kwargs)
            return {"code": 0, "data": {"items": [{"event_id": "event-1", "summary": "会议"}]}}

    monkeypatch.setattr(feishu_resource_providers, "resolve_feishu_user_access_token", fake_resolve_user_token)
    monkeypatch.setattr(feishu_resource_providers, "FeishuCalendarService", FakeCalendarService)

    class Provider(FeishuCalendarProvider):
        def _execute_tool(self, *_args, **_kwargs):
            raise AssertionError("authorized SELF query fallback must not use CLI")

    result = Provider(db=_FakeDb(app_config=app_config)).execute(
        _bot_query_request(company_id=company_id, source="calendar", operation="list_events", strategy="calendar_query")
    )

    assert result.status == "success"
    assert result.result_type == "calendar_event_list"
    assert result.items[0]["title"] == "会议"
    assert result.metadata["credential_mode"] == "USER_TOKEN"
    assert result.metadata["fallback_used"] is True
    assert result.metadata["fallback_scope"] == "SELF"
    assert result.metadata["cannot_escalate_to_company"] is True
    assert calls[0]["user_access_token"] == "user-token"


def test_company_task_query_does_not_use_user_fallback_even_when_authorized(monkeypatch):
    company_id = uuid4()
    app_config = SimpleNamespace(id=uuid4(), company_id=company_id)

    async def fake_resolve_user_token(*_args, **_kwargs):
        raise AssertionError("company query must not attempt USER fallback")

    monkeypatch.setattr(feishu_resource_providers, "resolve_feishu_user_access_token", fake_resolve_user_token)

    result = FeishuTaskProvider(db=_FakeDb(app_config=app_config)).execute(
        _bot_query_request(
            company_id=company_id,
            source="task",
            operation="list_my_tasks",
            strategy="task_query",
            data_scope="company",
        )
    )

    assert result.status == "denied"
    assert result.metadata["provider_boundary"] == "enterprise_realtime_not_integrated"
    assert "fallback_used" not in result.metadata


def test_resolve_feishu_user_access_token_missing_account_returns_missing_authorization():
    company_id = uuid4()
    app_config = SimpleNamespace(id=uuid4(), company_id=company_id)

    result = feishu_resource_providers._run_async(
        resolve_feishu_user_access_token(
            _FakeDb(app_config=app_config),
            company_id=company_id,
            open_id="ou_missing",
            app_config=app_config,
        )
    )

    assert result.user_access_token is None
    assert result.authorization_status == "MISSING_AUTHORIZATION"
    assert result.error == "missing_feishu_user_account"


def test_task_service_complete_task_uses_user_token_patch():
    calls = []

    class FakeClient:
        async def api_patch_user(self, path, *, user_access_token, payload=None):
            calls.append((path, user_access_token, payload))
            return {"code": 0, "data": {"task": {"guid": "task-guid-1", "summary": "跟进客户"}}}

        async def api_patch(self, _path, _payload=None):
            raise AssertionError("tenant token patch must not be used when user token is provided")

    result = feishu_resource_providers._run_async(
        FeishuTaskService(SimpleNamespace(), client=FakeClient()).complete_task(
            task_guid="task-guid-1",
            completed_at="1760000000000",
            user_access_token="user-token",
        )
    )

    assert result["data"]["task"]["summary"] == "跟进客户"
    assert calls == [
        (
            "/open-apis/task/v2/tasks/task-guid-1?user_id_type=open_id",
            "user-token",
            {"task": {"completed_at": "1760000000000"}, "update_fields": ["completed_at"]},
        )
    ]


def test_task_service_create_task_uses_user_token_post():
    calls = []

    class FakeClient:
        async def api_post_user(self, path, *, user_access_token, payload=None):
            calls.append((path, user_access_token, payload))
            return {"code": 0, "data": {"task": {"guid": "task-guid-1", "summary": "明天4点开会"}}}

        async def api_post(self, _path, _payload=None):
            raise AssertionError("tenant token post must not be used when user token is provided")

    result = feishu_resource_providers._run_async(
        FeishuTaskService(SimpleNamespace(), client=FakeClient()).create_task(
            summary="明天4点开会",
            user_access_token="user-token",
        )
    )

    assert result["data"]["task"]["summary"] == "明天4点开会"
    assert calls == [
        (
            "/open-apis/task/v2/tasks?user_id_type=open_id",
            "user-token",
            {"summary": "明天4点开会"},
        )
    ]


def test_task_service_list_tasks_uses_user_token_get():
    calls = []

    class FakeClient:
        async def api_get_user(self, path, *, user_access_token, params=None):
            calls.append((path, user_access_token, params))
            return {"code": 0, "data": {"items": [{"guid": "task-guid-1", "summary": "跟进客户"}]}}

        async def api_get(self, _path, params=None):
            raise AssertionError("tenant token get must not be used when user token is provided")

    result = feishu_resource_providers._run_async(
        FeishuTaskService(SimpleNamespace(), client=FakeClient()).list_tasks(
            user_access_token="user-token",
            page_size=20,
        )
    )

    assert result["data"]["items"][0]["summary"] == "跟进客户"
    assert calls == [
        (
            "/open-apis/task/v2/tasks",
            "user-token",
            {"page_size": 20},
        )
    ]


def test_calendar_service_create_event_uses_user_token_post():
    calls = []

    class FakeClient:
        async def api_post_user(self, path, *, user_access_token, payload=None):
            calls.append((path, user_access_token, payload))
            return {"code": 0, "data": {"event": {"event_id": "event-1", "summary": "明天下午5点开会"}}}

        async def api_post(self, _path, _payload=None):
            raise AssertionError("tenant token post must not be used when user token is provided")

    result = feishu_resource_providers._run_async(
        FeishuCalendarService(SimpleNamespace(), client=FakeClient()).create_event(
            calendar_id="primary",
            summary="明天下午5点开会",
            start_time={"timestamp": "1782205200"},
            end_time={"timestamp": "1782208800"},
            user_access_token="user-token",
        )
    )

    assert result["data"]["event"]["summary"] == "明天下午5点开会"
    assert calls == [
        (
            "/open-apis/calendar/v4/calendars/primary/events",
            "user-token",
            {
                "attendee_ability": "can_modify_event",
                "description": "",
                "end_time": {"timestamp": "1782208800"},
                "free_busy_status": "busy",
                "reminders": [{"minutes": 5}],
                "start_time": {"timestamp": "1782205200"},
                "summary": "明天下午5点开会",
                "vchat": {"vc_type": "vc"},
            },
        )
    ]


def test_calendar_service_list_events_uses_user_token_get():
    calls = []

    class FakeClient:
        async def api_get_user(self, path, *, user_access_token, params=None):
            calls.append((path, user_access_token, params))
            return {"code": 0, "data": {"items": [{"event_id": "event-1", "summary": "会议"}]}}

        async def api_get(self, _path, params=None):
            raise AssertionError("tenant token get must not be used when user token is provided")

    result = feishu_resource_providers._run_async(
        FeishuCalendarService(SimpleNamespace(), client=FakeClient()).list_primary_events(
            user_access_token="user-token",
            page_size=20,
        )
    )

    assert result["data"]["items"][0]["summary"] == "会议"
    assert calls[0][0] == "/open-apis/calendar/v4/calendars/primary/events"
    assert calls[0][1] == "user-token"
    assert calls[0][2]["page_size"] == 50


def test_calendar_create_missing_user_token_returns_waiting_authorization():
    company_id = uuid4()
    app_config = SimpleNamespace(id=uuid4(), company_id=company_id)
    provider = FeishuCalendarProvider(db=_FakeDb(app_config=app_config))

    result = provider.execute(_request(company_id=company_id, operation="create_event"))

    assert result.status == "denied"
    assert result.result_type == "waiting_authorization"
    assert result.error == "missing_feishu_user_account"
    assert result.metadata["waiting_authorization"] is True
    assert result.metadata["authorization_status"] == "MISSING_AUTHORIZATION"
    assert result.metadata["execution_identity_contract"]["credential_mode"] == "USER_TOKEN"


def test_task_create_missing_user_token_returns_waiting_authorization():
    company_id = uuid4()
    app_config = SimpleNamespace(id=uuid4(), company_id=company_id)
    provider = FeishuTaskProvider(db=_FakeDb(app_config=app_config))

    result = provider.execute(_request(company_id=company_id, operation="create_task"))

    assert result.status == "denied"
    assert result.result_type == "waiting_authorization"
    assert result.error == "missing_feishu_user_account"
    assert result.metadata["waiting_authorization"] is True
    assert result.metadata["authorization_status"] == "MISSING_AUTHORIZATION"
    assert result.metadata["execution_identity_contract"]["credential_mode"] == "USER_TOKEN"


def test_task_complete_missing_user_token_returns_waiting_authorization():
    company_id = uuid4()
    app_config = SimpleNamespace(id=uuid4(), company_id=company_id)
    provider = FeishuTaskProvider(db=_FakeDb(app_config=app_config))

    result = provider.execute(_request(company_id=company_id))

    assert result.status == "denied"
    assert result.result_type == "waiting_authorization"
    assert result.error == "missing_feishu_user_account"
    assert result.metadata["waiting_authorization"] is True
    assert result.metadata["authorization_status"] == "MISSING_AUTHORIZATION"
    assert result.metadata["execution_identity_contract"]["credential_mode"] == "USER_TOKEN"


def test_task_complete_authorized_user_token_executes_task_provider(monkeypatch):
    company_id = uuid4()
    app_config = SimpleNamespace(id=uuid4(), company_id=company_id)
    calls = []

    class TokenResolution:
        user_access_token = "user-token"
        authorization_status = "AUTHORIZED"
        account_id = "account-1"
        error = ""
        authorized = True

    async def fake_resolve_user_token(*_args, **_kwargs):
        return TokenResolution()

    class FakeTaskService:
        def __init__(self, app_config):
            self.app_config = app_config

        async def complete_task(self, **kwargs):
            calls.append(kwargs)
            return {"code": 0, "data": {"task": {"guid": "task-guid-1", "summary": "跟进客户"}}}

    monkeypatch.setattr(feishu_resource_providers, "resolve_feishu_user_access_token", fake_resolve_user_token)
    monkeypatch.setattr(feishu_resource_providers, "FeishuTaskService", FakeTaskService)

    provider = FeishuTaskProvider(db=_FakeDb(app_config=app_config))
    result = provider.execute(_request(company_id=company_id))

    assert result.status == "success"
    assert result.result_type == "task_complete"
    assert result.items[0]["title"] == "跟进客户"
    assert result.metadata["credential_mode"] == "USER_TOKEN"
    assert result.metadata["authorization_status"] == "AUTHORIZED"
    assert calls[0]["user_access_token"] == "user-token"


def test_task_create_authorized_user_token_executes_task_provider(monkeypatch):
    company_id = uuid4()
    app_config = SimpleNamespace(id=uuid4(), company_id=company_id)
    calls = []

    class TokenResolution:
        user_access_token = "user-token"
        authorization_status = "AUTHORIZED"
        account_id = "account-1"
        error = ""
        authorized = True

    async def fake_resolve_user_token(*_args, **_kwargs):
        return TokenResolution()

    class FakeTaskService:
        def __init__(self, app_config):
            self.app_config = app_config

        async def create_task(self, **kwargs):
            calls.append(kwargs)
            return {"code": 0, "data": {"task": {"guid": "task-guid-1", "summary": "明天4点开会"}}}

    monkeypatch.setattr(feishu_resource_providers, "resolve_feishu_user_access_token", fake_resolve_user_token)
    monkeypatch.setattr(feishu_resource_providers, "FeishuTaskService", FakeTaskService)

    db = _FakeDb(app_config=app_config)
    provider = FeishuTaskProvider(db=db)
    result = provider.execute(_request(company_id=company_id, operation="create_task"))

    assert result.status == "success"
    assert result.result_type == "task_create"
    assert result.items[0]["title"] == "明天4点开会"
    assert result.metadata["credential_mode"] == "USER_TOKEN"
    assert result.metadata["authorization_status"] == "AUTHORIZED"
    assert calls[0]["user_access_token"] == "user-token"
    assert result.metadata["enterprise_write_through"] == "disabled"
    assert result.metadata["operational_source"] == "feishu_realtime"
    assert db.added == []
    assert db.commits == 0


def test_calendar_create_authorized_user_token_executes_calendar_provider(monkeypatch):
    company_id = uuid4()
    app_config = SimpleNamespace(id=uuid4(), company_id=company_id)
    calls = []

    class TokenResolution:
        user_access_token = "user-token"
        authorization_status = "AUTHORIZED"
        account_id = "account-1"
        error = ""
        authorized = True

    async def fake_resolve_user_token(*_args, **_kwargs):
        return TokenResolution()

    class FakeCalendarService:
        def __init__(self, app_config):
            self.app_config = app_config

        async def create_event(self, **kwargs):
            calls.append(kwargs)
            return {"code": 0, "data": {"event": {"event_id": "event-1", "summary": "明天下午5点开会"}}}

    monkeypatch.setattr(feishu_resource_providers, "resolve_feishu_user_access_token", fake_resolve_user_token)
    monkeypatch.setattr(feishu_resource_providers, "FeishuCalendarService", FakeCalendarService)

    db = _FakeDb(app_config=app_config)
    provider = FeishuCalendarProvider(db=db)
    result = provider.execute(_request(company_id=company_id, operation="create_event"))

    assert result.status == "success"
    assert result.result_type == "calendar_create"
    assert result.items[0]["title"] == "明天下午5点开会"
    assert result.metadata["credential_mode"] == "USER_TOKEN"
    assert result.metadata["authorization_status"] == "AUTHORIZED"
    assert calls[0]["user_access_token"] == "user-token"
    assert result.metadata["enterprise_write_through"] == "disabled"
    assert result.metadata["operational_source"] == "feishu_realtime"
    assert db.added == []
    assert db.commits == 0


def test_task_query_without_authorized_user_token_blocks_legacy_tool_path():
    company_id = uuid4()
    app_config = SimpleNamespace(id=uuid4(), company_id=company_id)

    class ToolTaskProvider(FeishuTaskProvider):
        def _execute_tool(self, request, *, tool_name, params=None, confirm_write=False):
            raise AssertionError("bot-first task query must not fall through to task_qa CLI")

    provider = ToolTaskProvider(db=_FakeDb(app_config=app_config))
    result = provider.execute(_request(company_id=company_id, operation="list_my_tasks"))

    assert result.status == "denied"
    assert result.result_type == "task_list"
    assert result.error == "enterprise_realtime_not_integrated"
    assert result.metadata["provider_boundary"] == "enterprise_realtime_not_integrated"
    assert result.metadata["credential_mode"] == "TENANT_TOKEN"
    assert result.metadata["legacy_cli_fallback_used"] is False
    assert result.metadata["user_fallback_allowed"] is True


def test_calendar_query_without_authorized_user_token_blocks_legacy_tool_path():
    company_id = uuid4()
    app_config = SimpleNamespace(id=uuid4(), company_id=company_id)

    class ToolCalendarProvider(FeishuCalendarProvider):
        def _execute_tool(self, request, *, tool_name, params=None, confirm_write=False):
            raise AssertionError("bot-first calendar query must not fall through to calendar_qa CLI")

    provider = ToolCalendarProvider(db=_FakeDb(app_config=app_config))
    result = provider.execute(_request(company_id=company_id, operation="list_events"))

    assert result.status == "denied"
    assert result.result_type == "calendar_event_list"
    assert result.error == "enterprise_realtime_not_integrated"
    assert result.metadata["provider_boundary"] == "enterprise_realtime_not_integrated"
    assert result.metadata["credential_mode"] == "TENANT_TOKEN"
    assert result.metadata["legacy_cli_fallback_used"] is False
    assert result.metadata["user_fallback_allowed"] is True


def test_task_company_query_passes_scope_filter_without_owner_filter(monkeypatch):
    company_id = uuid4()
    app_config = SimpleNamespace(id=uuid4(), company_id=company_id)
    async def fail_resolve_user_token(*_args, **_kwargs):
        raise AssertionError("query must not resolve user token")

    monkeypatch.setattr(feishu_resource_providers, "resolve_feishu_user_access_token", fail_resolve_user_token)

    class ToolTaskProvider(FeishuTaskProvider):
        def _execute_tool(self, request, *, tool_name, params=None, confirm_write=False):
            raise AssertionError("company task query must not fall through to task_qa CLI")

    provider = ToolTaskProvider(db=_FakeDb(app_config=app_config))
    result = provider.execute(_request(company_id=company_id, operation="list_my_tasks", data_scope="company"))

    assert result.status == "denied"
    assert result.error == "enterprise_realtime_not_integrated"
    assert result.metadata["provider_boundary"] == "enterprise_realtime_not_integrated"
    assert result.metadata["legacy_cli_fallback_used"] is False
