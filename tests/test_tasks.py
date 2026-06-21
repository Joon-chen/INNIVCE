import app.tasks.celery_app as celery_module
from uuid import uuid4

from app.tasks.celery_app import auto_v5_resource_sync_task, celery_app


def test_v5_resource_auto_sync_is_registered_with_beat() -> None:
    assert "auto-v5-resource-sync" in celery_app.conf.beat_schedule
    assert celery_app.conf.beat_schedule["auto-v5-resource-sync"]["task"] == "v5.resources.sync_auto"
    assert "v5.resources.sync_auto" in celery_app.tasks


def test_bot_recent_approvals_reply_task_is_registered() -> None:
    assert "bot.approvals.recent_reply" in celery_app.tasks


def test_v5_resource_auto_sync_is_disabled_without_enabled_policies(monkeypatch) -> None:
    class DummySession:
        def close(self) -> None:
            return None

    monkeypatch.setattr(celery_module, "SessionLocal", lambda: DummySession())
    monkeypatch.setattr(celery_module, "enabled_v5_resource_sync_policies", lambda db: [])

    result = auto_v5_resource_sync_task()

    assert result == {"enabled": False, "count": 0, "saved_count": 0, "policies": 0}


def test_celery_feishu_text_send_uses_gateway_responder(monkeypatch) -> None:
    calls = {}

    async def fake_send_feishu_text_reply(*, app_config, reply_target, text, client_factory, max_chars=3500):
        calls["app_config"] = app_config
        calls["reply_target"] = reply_target
        calls["text"] = text
        calls["client_factory"] = client_factory
        calls["max_chars"] = max_chars
        return {"code": 0}

    monkeypatch.setattr(celery_module, "send_feishu_text_reply", fake_send_feishu_text_reply)

    app_config = object()
    result = celery_module._send_feishu_text_via_gateway(
        app_config,
        {"receive_id_type": "chat_id", "receive_id": "oc_1"},
        "审批查询结果",
    )

    assert result == {"code": 0}
    assert calls["app_config"] is app_config
    assert calls["reply_target"] == {"receive_id_type": "chat_id", "receive_id": "oc_1"}
    assert calls["text"] == "审批查询结果"
    assert calls["client_factory"] is celery_module.FeishuClient
    assert calls["max_chars"] == 3500


def test_v5_resource_auto_sync_skips_manual_review_resources() -> None:
    class DummyResource:
        def __init__(self, resource_type):
            self.id = uuid4()
            self.company_id = uuid4()
            self.platform = "feishu"
            self.resource_type = resource_type
            self.resource_id = "unknown_x"
            self.resource_name = "未知资源"
            self.resource_sub_id = None
            self.sync_mode = "manual"
            self.permission_level = "company"
            self.enabled = True
            self.last_sync_at = None
            self.created_at = None
            self.app_config_id = None
            self.config_json = {}

    class DummyScalarResult:
        def all(self):
            return [DummyResource("unknown")]

    class DummyDb:
        def scalars(self, query):
            return DummyScalarResult()

    policy = {"resource_types": [], "statuses": [], "limit_resources": 10}

    assert celery_module._v5_auto_sync_resources(DummyDb(), company_id="company", policy=policy) == []
