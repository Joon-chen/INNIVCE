import asyncio
from types import SimpleNamespace
from uuid import uuid4

from app.services import feishu_admin_apps
from app.services.feishu_admin_apps import (
    FEISHU_APP_CREDENTIAL_STATUS_PENDING,
    FEISHU_APP_CREDENTIAL_STATUS_VALID,
    pending_credential_settings,
    refresh_tenant_access_token,
)


def test_pending_credential_settings_marks_app_config_unvalidated() -> None:
    settings = pending_credential_settings({"cli_profile": "gaustek", "credential_validated_at": "old"})

    assert settings == {
        "cli_profile": "gaustek",
        "credential_status": FEISHU_APP_CREDENTIAL_STATUS_PENDING,
    }


def test_refresh_tenant_access_token_marks_app_credentials_valid(monkeypatch) -> None:
    company_id = uuid4()
    app_config = SimpleNamespace(
        id=uuid4(),
        company_id=company_id,
        app_id="cli_gaustek",
        settings={"cli_profile": "gaustek", "credential_status": FEISHU_APP_CREDENTIAL_STATUS_PENDING},
    )
    audit_payloads = []

    class FakeDb:
        committed = False

        def commit(self):
            self.committed = True

    class FakeClient:
        def __init__(self, app_config_arg):
            self.app_config_arg = app_config_arg

        async def get_tenant_access_token(self, *, force_refresh: bool) -> str:
            assert self.app_config_arg is app_config
            assert force_refresh is True
            return "tenant-token"

    def fake_write_audit_log(*args, **kwargs):
        audit_payloads.append(kwargs["payload"])

    db = FakeDb()
    monkeypatch.setattr(feishu_admin_apps, "FeishuClient", FakeClient)
    monkeypatch.setattr(feishu_admin_apps, "write_audit_log", fake_write_audit_log)

    result = asyncio.run(refresh_tenant_access_token(db, app_config))

    assert result == {
        "ok": True,
        "cached": True,
        "credential_status": FEISHU_APP_CREDENTIAL_STATUS_VALID,
        "token_redacted": True,
    }
    assert app_config.settings["credential_status"] == FEISHU_APP_CREDENTIAL_STATUS_VALID
    assert app_config.settings["credential_validation_method"] == "tenant_access_token"
    assert "credential_validated_at" in app_config.settings
    assert audit_payloads == [
        {
            "cached": True,
            "refreshed": True,
            "credential_status": FEISHU_APP_CREDENTIAL_STATUS_VALID,
            "credential_validation_method": "tenant_access_token",
        }
    ]
    assert db.committed is True
