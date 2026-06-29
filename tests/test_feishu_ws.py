import asyncio
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

from app.workers import feishu_ws


def test_command_result_runs_inside_existing_event_loop(monkeypatch) -> None:
    expected = {
        "handled": True,
        "status": "handled",
        "used_agent_runtime": True,
        "final_answer_owner": "agent_runtime",
    }

    def fake_in_new_session(app_config_id, payload):
        return expected

    monkeypatch.setattr(feishu_ws, "_handle_command_result_in_new_session", fake_in_new_session)

    async def run():
        return feishu_ws._handle_command_result(uuid4(), {"event": {}})

    assert asyncio.run(run()) == expected


def test_command_bool_wrapper_uses_structured_result(monkeypatch) -> None:
    monkeypatch.setattr(feishu_ws, "_handle_command_result", lambda *args, **kwargs: {"handled": True})

    assert feishu_ws._handle_command(uuid4(), {"event": {}}) is True


def test_message_queue_serializes_same_chat(monkeypatch) -> None:
    app_config_id = uuid4()
    first_started = threading.Event()
    release_first = threading.Event()
    done = threading.Event()
    active = 0
    max_active = 0
    calls: list[str] = []
    lock = threading.Lock()

    def fake_ingest(app_config_id_arg, payload):
        nonlocal active, max_active
        message_id = payload["event"]["message"]["message_id"]
        with lock:
            active += 1
            max_active = max(max_active, active)
            calls.append(message_id)
        if message_id == "om_1":
            first_started.set()
            assert release_first.wait(2)
        with lock:
            active -= 1
            if len(calls) == 2:
                done.set()

    monkeypatch.setattr(feishu_ws, "_ingest_and_handle", fake_ingest)
    feishu_ws._reset_conversation_queues_for_tests()

    feishu_ws._enqueue_ingest_and_handle(app_config_id, _message_payload("om_1", chat_id="oc_1"))
    assert first_started.wait(2)
    feishu_ws._enqueue_ingest_and_handle(app_config_id, _message_payload("om_2", chat_id="oc_1"))
    time.sleep(0.05)
    release_first.set()

    assert done.wait(2)
    assert calls == ["om_1", "om_2"]
    assert max_active == 1
    feishu_ws._reset_conversation_queues_for_tests()


def test_card_action_response_runs_inside_existing_event_loop(monkeypatch) -> None:
    expected = {"card": {"type": "card_json", "data": "{}"}}

    def fake_in_new_session(app_config_id, payload):
        return expected

    monkeypatch.setattr(feishu_ws, "_handle_card_action_response_in_new_session", fake_in_new_session)

    async def run():
        return feishu_ws._handle_card_action_response(uuid4(), {"event": {}})

    assert asyncio.run(run()) == expected


def test_card_action_response_audits_unhandled_action(monkeypatch) -> None:
    app_config_id = uuid4()
    company_id = uuid4()
    payload = {
        "header": {"event_id": "evt_card", "event_type": "card.action.trigger", "app_id": "cli_1"},
        "event": {
            "operator": {"operator_id": {"open_id": "ou_1"}},
            "action": {"value": {"kind": "other_action", "action": "open", "chat_id": "oc_1"}},
            "context": {"open_message_id": "om_card"},
        },
    }

    class FakeDb:
        def __init__(self):
            self.added = []
            self.commits = 0
            self.closed = False

        def get(self, model, item_id):
            assert item_id == app_config_id
            return SimpleNamespace(id=item_id, is_active=True, company_id=company_id, app_id="cli_1", name="大飞哥")

        def add(self, item):
            self.added.append(item)

        def commit(self):
            self.commits += 1

        def close(self):
            self.closed = True

    db = FakeDb()

    async def fake_response(db_arg, app_config, payload_arg):
        assert db_arg is db
        assert payload_arg is payload
        return None

    monkeypatch.setattr(feishu_ws, "SessionLocal", lambda: db)
    monkeypatch.setattr(feishu_ws, "handle_feishu_gateway_card_action_response", fake_response)

    response = feishu_ws._handle_card_action_response_in_new_session(app_config_id, payload)

    assert response is None
    assert db.closed is True
    assert db.commits == 1
    assert len(db.added) == 1
    audit = db.added[0]
    assert audit.action == "gateway.feishu.card_action"
    assert audit.target_id == "om_card"
    assert audit.payload["reason"] == "unhandled_card_action"


def _message_payload(message_id: str, *, chat_id: str) -> dict:
    return {
        "header": {"event_id": f"evt_{message_id}", "event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_1"}},
            "message": {
                "message_id": message_id,
                "chat_id": chat_id,
                "message_type": "text",
                "content": "{\"text\":\"test\"}",
            },
        },
    }
