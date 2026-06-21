import asyncio
import json
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.routes.user_identity_oauth_helpers import _mail_user_identity_oauth_callback
from app.services.feishu_cli_user_auth import complete_lark_cli_user_authorization, start_lark_cli_user_authorization
from app.services.feishu_oauth_helpers import feishu_oauth_callback_payload
from app.services.user_identity_authorizations import (
    build_user_identity_oauth_state,
    mark_user_identity_authorization,
    parse_user_identity_oauth_state,
)


def test_user_identity_oauth_state_round_trips() -> None:
    company_id = uuid4()
    app_config_id = uuid4()

    state = build_user_identity_oauth_state(
        company_id=company_id,
        open_id="ou_1",
        resource_type="personal_feishu",
        provider="feishu",
        app_config_id=app_config_id,
    )
    parsed = parse_user_identity_oauth_state(state)

    assert parsed is not None
    assert parsed.company_id == company_id
    assert parsed.open_id == "ou_1"
    assert parsed.resource_type == "personal_feishu"
    assert parsed.provider == "feishu"
    assert parsed.app_config_id == app_config_id


def test_mark_user_identity_authorization_updates_owner_scope() -> None:
    company_id = uuid4()
    access = SimpleNamespace(company_id=company_id, open_id="ou_1", settings={})

    class FakeDb:
        def scalar(self, query):
            return access

        def add(self, item):
            raise AssertionError("existing access should be updated")

        def commit(self):
            self.committed = True

        def refresh(self, item):
            self.refreshed = item

    db = FakeDb()
    mark_user_identity_authorization(
        db,  # type: ignore[arg-type]
        company_id=company_id,
        open_id="ou_1",
        resource_type="external_mail",
        status="authorized",
        provider="gmail",
        owner_open_id="ou_1",
    )

    authorization = access.settings["user_identity_authorizations"]["user_identity_bundle"]
    assert authorization["status"] == "authorized"
    assert authorization["provider"] == "gmail"
    assert authorization["requested_resource_type"] == "external_mail"
    assert authorization["authorization_model"] == "bundle_authorization"
    assert authorization["covered_resources"] == [
        "personal_feishu",
        "external_mail",
        "personal_dingtalk",
        "personal_wechat",
    ]
    assert authorization["owner_open_id"] == "ou_1"
    assert authorization["can_escalate_original_permissions"] is False
    assert db.committed is True
    assert db.refreshed is access


def test_lark_cli_user_authorization_split_flow_generates_qr_code(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if "qrcode" in args:
            output_path = tmp_path / args[-1]
            output_path.parent.mkdir(exist_ok=True)
            output_path.write_bytes(b"png-data")
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps({"verification_url": "https://verify.example/abc", "device_code": "dev_1"}),
            stderr="",
        )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("app.services.feishu_cli_user_auth.shutil.which", lambda name: "/usr/local/bin/lark-cli")
    monkeypatch.setattr("app.services.feishu_cli_user_auth.subprocess.run", fake_run)

    payload = start_lark_cli_user_authorization(
        SimpleNamespace(settings={"cli_profile": "v5-local-prod"}),
        domains="approval,calendar",
    )

    assert payload["status"] == "authorization_started"
    assert payload["verification_url"] == "https://verify.example/abc"
    assert payload["device_code"] == "dev_1"
    assert payload["qr_code_data_url"] == "data:image/png;base64,cG5nLWRhdGE="
    assert calls[0][:4] == ["/usr/local/bin/lark-cli", "--profile", "v5-local-prod", "auth"]
    assert "--no-wait" in calls[0]
    assert calls[1][:4] == ["/usr/local/bin/lark-cli", "auth", "qrcode", "https://verify.example/abc"]


def test_lark_cli_user_authorization_complete_uses_device_code(monkeypatch) -> None:
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps({"identity": "user", "message": "authorized"}),
            stderr="",
        )

    monkeypatch.setattr("app.services.feishu_cli_user_auth.shutil.which", lambda name: "/usr/local/bin/lark-cli")
    monkeypatch.setattr("app.services.feishu_cli_user_auth.subprocess.run", fake_run)

    payload = complete_lark_cli_user_authorization(
        SimpleNamespace(settings={"cli_profile": "v5-local-prod"}),
        device_code="dev_1",
        expected_owner_open_id="ou_1",
    )

    assert payload == {
        "status": "authorized",
        "provider": "feishu_cli",
        "cli_profile": "v5-local-prod",
        "identity": "user",
        "identity_open_id": None,
        "identity_match": None,
        "message": "authorized",
    }
    assert captured["args"] == [
        "/usr/local/bin/lark-cli",
        "--profile",
        "v5-local-prod",
        "auth",
        "login",
        "--device-code",
        "dev_1",
        "--json",
    ]


def test_lark_cli_user_authorization_complete_accepts_matching_open_id(monkeypatch) -> None:
    monkeypatch.setattr("app.services.feishu_cli_user_auth.shutil.which", lambda name: "/usr/local/bin/lark-cli")
    monkeypatch.setattr(
        "app.services.feishu_cli_user_auth.subprocess.run",
        lambda args, **kwargs: subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps({"identity": {"open_id": "ou_1"}, "message": "authorized"}),
            stderr="",
        ),
    )

    payload = complete_lark_cli_user_authorization(
        SimpleNamespace(settings={"cli_profile": "v5-local-prod"}),
        device_code="dev_1",
        expected_owner_open_id="ou_1",
    )

    assert payload["identity_open_id"] == "ou_1"
    assert payload["identity_match"] is True


def test_lark_cli_user_authorization_complete_rejects_mismatched_open_id(monkeypatch) -> None:
    monkeypatch.setattr("app.services.feishu_cli_user_auth.shutil.which", lambda name: "/usr/local/bin/lark-cli")
    monkeypatch.setattr(
        "app.services.feishu_cli_user_auth.subprocess.run",
        lambda args, **kwargs: subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps({"identity": {"open_id": "ou_other"}, "message": "authorized"}),
            stderr="",
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        complete_lark_cli_user_authorization(
            SimpleNamespace(settings={"cli_profile": "v5-local-prod"}),
            device_code="dev_1",
            expected_owner_open_id="ou_1",
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"] == "feishu_cli_user_identity_mismatch"
    assert exc_info.value.detail["expected_owner_open_id"] == "ou_1"
    assert exc_info.value.detail["authorized_open_id"] == "ou_other"


def test_mail_user_identity_callback_records_pending_token_exchange(monkeypatch) -> None:
    company_id = uuid4()
    state = build_user_identity_oauth_state(
        company_id=company_id,
        open_id="ou_1",
        resource_type="external_mail",
        provider="gmail",
    )
    captured = {}

    def fake_mark(*args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("app.api.routes.user_identity_oauth_helpers.mark_user_identity_authorization", fake_mark)

    response = _mail_user_identity_oauth_callback(
        SimpleNamespace(),  # type: ignore[arg-type]
        code="code_1",
        state=state,
        expected_provider="gmail",
    )

    assert response.status_code == 200
    assert captured["company_id"] == company_id
    assert captured["open_id"] == "ou_1"
    assert captured["resource_type"] == "external_mail"
    assert captured["status"] == "authorization_pending_token_exchange"
    assert captured["provider"] == "gmail"


def test_feishu_user_identity_callback_rejects_open_id_mismatch(monkeypatch) -> None:
    company_id = uuid4()
    app_config_id = uuid4()
    state = build_user_identity_oauth_state(
        company_id=company_id,
        open_id="ou_1",
        resource_type="personal_feishu",
        provider="feishu",
        app_config_id=app_config_id,
    )

    class FakeDb:
        def get(self, model, key):
            return SimpleNamespace(id=key, company_id=company_id)

    async def fake_exchange(*args, **kwargs):
        return {
            "account": {
                "id": str(uuid4()),
                "display_name": "员工A",
                "settings": {"open_id": "ou_other"},
            }
        }

    def fake_mark(*args, **kwargs):
        raise AssertionError("mismatched open_id must not be marked as authorized")

    monkeypatch.setattr("app.services.feishu_oauth_helpers.exchange_and_store_feishu_user_token", fake_exchange)
    monkeypatch.setattr("app.services.feishu_oauth_helpers.mark_user_identity_authorization", fake_mark)

    response = asyncio.run(feishu_oauth_callback_payload(FakeDb(), code="code_1", state=state))  # type: ignore[arg-type]

    assert response.status_code == 400
    assert "授权账号与发起授权的飞书用户不一致".encode() in response.body
