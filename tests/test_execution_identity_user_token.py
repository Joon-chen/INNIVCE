from types import SimpleNamespace
from uuid import uuid4

from app.services.feishu.task import FeishuTaskService
from app.services.runtime_v5 import feishu_resource_providers
from app.services.runtime_v5.feishu_resource_providers import FeishuTaskProvider
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

    def scalar(self, _query):
        return self.app_config

    def scalars(self, _query):
        return _ScalarResult(self.accounts)


def _request(*, company_id, open_id="ou_user", params=None) -> ProviderRequest:
    return ProviderRequest(
        source="task",
        operation="complete_task",
        intent=IntentResult(
            question_type="action",
            intent="task_complete",
            data_scope="self",
            confidence=0.9,
            canonical_question="完成任务",
        ),
        planner=PlannerResult(strategy="task_complete", sources=("task",)),
        context=RuntimeContext(
            identity=RuntimeIdentity(open_id=open_id, role="owner"),
            runtime_scope=RuntimeScope(company_ids=(company_id,), active_company_id=company_id),
            current_message="完成任务",
        ),
        execution_identity="user",
        params=params or {"task_guid": "task-guid-1"},
        execution_identity_contract=ExecutionIdentityContract(
            actor_identity="USER",
            credential_mode="USER_TOKEN",
            credential_owner=CredentialOwner(company_id=str(company_id), open_id=open_id),
            requires_authorization=True,
        ),
    )


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
