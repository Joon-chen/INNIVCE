from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.routes.work_events_detail_routes import get_work_event_detail
from app.schemas.common import WorkEventCreate
from app.services.work_events import upsert_work_event


class _ScalarResult:
    def all(self) -> list:
        return []


class _FakeDb:
    def __init__(self, event=None) -> None:
        self.event = event

    def get(self, model, event_id):
        return self.event if self.event and self.event.id == event_id else None

    def scalars(self, query):
        return _ScalarResult()


class _WriteDb:
    def __init__(self, scalar_result=None) -> None:
        self.added = []
        self.scalar_result = scalar_result

    def scalar(self, query):
        return self.scalar_result

    def add(self, item):
        self.added.append(item)

    def flush(self):
        pass


def test_upsert_work_event_normalizes_legacy_source_type(monkeypatch) -> None:
    monkeypatch.setattr("app.services.work_events.write_audit_log", lambda *args, **kwargs: None)
    queued = []
    monkeypatch.setattr("app.services.work_events._enqueue_vectorize_event", lambda event_id: queued.append(event_id))
    db = _WriteDb()

    event = upsert_work_event(
        db,
        WorkEventCreate(
            company_id=uuid4(),
            source="imap",
            source_type="mail_account",
            event_type="mail.message",
            external_id="message-1",
            title="客户邮件",
        ),
    )

    assert db.added == [event]
    assert event.source_type == "external_mail_account"
    assert event.vector_status == "skipped"
    assert queued == []


def test_upsert_work_event_binds_feishu_chat_resource_from_thread(monkeypatch) -> None:
    monkeypatch.setattr("app.services.work_events.write_audit_log", lambda *args, **kwargs: None)
    resource_id = uuid4()
    db = _WriteDb(scalar_result=resource_id)

    event = upsert_work_event(
        db,
        WorkEventCreate(
            company_id=uuid4(),
            source="feishu",
            event_type="im.message.receive_v1",
            thread_id="oc_chat_1",
            title="业务群",
            content_text="需要跟进客户交付",
        ),
    )

    assert db.added == [event]
    assert event.resource_id == resource_id


def test_upsert_work_event_enqueues_only_l1_hot_knowledge(monkeypatch) -> None:
    monkeypatch.setattr("app.services.work_events.write_audit_log", lambda *args, **kwargs: None)
    queued = []
    monkeypatch.setattr("app.services.work_events._enqueue_vectorize_event", lambda event_id: queued.append(event_id))
    db = _WriteDb()

    event = upsert_work_event(
        db,
        WorkEventCreate(
            company_id=uuid4(),
            source="feishu",
            business_domain="knowledge",
            event_type="feishu.docx.content",
            external_id="docx:doccn_hot",
            title="高频制度",
            payload={
                "data_layer": "knowledge_hot",
                "query_path": "local_rag",
                "document_chunks": [{"index": 0, "chunk_id": "chunk-1", "text": "制度正文"}],
                "rag_indexing": {
                    "document_store": "work_events",
                    "vector_db": "qdrant_vectors",
                },
            },
        ),
    )

    assert event.vector_status == "pending"
    assert queued == [str(event.id)]


def test_get_work_event_detail_returns_payload_and_status() -> None:
    event_id = uuid4()
    company_id = uuid4()
    now = datetime.now(UTC)
    event = SimpleNamespace(
        id=event_id,
        company_id=company_id,
        account_id=None,
        source="feishu",
        event_type="mail.message",
        external_id="message-1",
        thread_id="thread-1",
        title="客户邮件",
        content_text="摘要",
        occurred_at=now,
        actors=[],
        labels=["mail"],
        payload={"folder_id": "INBOX"},
        sensitivity="normal",
        vector_status="indexed",
        created_at=now,
        updated_at=now,
    )

    detail = get_work_event_detail(event_id, db=_FakeDb(event))

    assert detail["id"] == str(event_id)
    assert detail["company_id"] == str(company_id)
    assert detail["payload"] == {"folder_id": "INBOX"}
    assert detail["vector_status"] == "indexed"
    assert detail["extracted_items"] == []


def test_get_work_event_detail_raises_404_for_missing_event() -> None:
    with pytest.raises(HTTPException) as exc:
        get_work_event_detail(uuid4(), db=_FakeDb())

    assert exc.value.status_code == 404
