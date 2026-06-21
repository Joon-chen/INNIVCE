import importlib.util
import json
from pathlib import Path

import pytest


def _load_bootstrap_module():
    path = Path("scripts/bootstrap_feishu_app_config_v5.py")
    spec = importlib.util.spec_from_file_location("bootstrap_feishu_app_config_v5", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bootstrap_infers_app_id_and_profile_from_lark_cli_home(tmp_path, monkeypatch) -> None:
    module = _load_bootstrap_module()
    cli_home = tmp_path / "lark-cli-v5"
    cli_home.mkdir()
    (cli_home / "config.json").write_text(
        json.dumps(
            {
                "apps": [
                    {
                        "name": "v5-local-prod",
                        "brand": "feishu",
                        "appId": "cli_test",
                        "appSecret": {"encrypted": True},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LARK_CLI_HOME", str(cli_home))
    monkeypatch.setenv("V5_COMPANY_NAME", "固势")
    monkeypatch.setenv("V5_COMPANY_CODE", "gaustek")
    monkeypatch.setenv("V5_FEISHU_APP_SECRET", "secret")
    monkeypatch.delenv("V5_FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("V5_FEISHU_CLI_PROFILE", raising=False)

    values = module._required_env()
    settings = module._settings_payload()

    assert values["V5_FEISHU_APP_ID"] == "cli_test"
    assert values["V5_FEISHU_CLI_PROFILE"] == "v5-local-prod"
    assert settings["cli_profile"] == "v5-local-prod"
    assert settings["cli_profile_source"] == "lark_cli_home"


def test_bootstrap_still_requires_api_sync_app_secret(tmp_path, monkeypatch) -> None:
    module = _load_bootstrap_module()
    cli_home = tmp_path / "lark-cli-v5"
    cli_home.mkdir()
    (cli_home / "config.json").write_text(
        json.dumps({"apps": [{"name": "v5-local-prod", "appId": "cli_test", "appSecret": {"encrypted": True}}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("LARK_CLI_HOME", str(cli_home))
    monkeypatch.setenv("V5_COMPANY_NAME", "固势")
    monkeypatch.setenv("V5_COMPANY_CODE", "gaustek")
    monkeypatch.delenv("V5_FEISHU_APP_SECRET", raising=False)
    monkeypatch.delenv("V5_FEISHU_APP_ID", raising=False)

    with pytest.raises(SystemExit, match="Missing required env: V5_FEISHU_APP_SECRET"):
        module._required_env()
