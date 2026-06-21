from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.services.ai import vector_search
from app.services.ai.vector_search import (
    WorkEventVectorIndex,
    _event_vector_segments,
    mark_event_vector_indexing_result,
    should_vectorize_work_event,
)


def test_event_vector_segments_use_document_chunks() -> None:
    event_id = uuid4()
    event = SimpleNamespace(
        id=event_id,
        title="制度文档",
        content_text="整篇正文",
        payload={
            "document_chunks": [
                {"index": 0, "chunk_id": "chunk-1", "text": "第一段"},
                {"index": 1, "chunk_id": "chunk-2", "text": "第二段"},
            ]
        },
    )

    segments = _event_vector_segments(event)

    assert [segment["chunk_id"] for segment in segments] == ["chunk-1", "chunk-2"]
    assert segments[0]["point_id"] == str(event_id)
    assert segments[1]["point_id"] != str(event_id)
    assert segments[0]["text"] == "制度文档\n第一段"


def test_work_event_vector_index_upserts_chunk_points_and_maps_search_to_event_id(monkeypatch) -> None:
    event_id = uuid4()
    company_id = uuid4()
    captured = {}

    event = SimpleNamespace(
        id=event_id,
        company_id=company_id,
        thread_id=None,
        source="feishu",
        event_type="feishu.docx.content",
        title="制度文档",
        content_text="整篇正文",
        payload={
            "data_layer": "knowledge_hot",
            "query_path": "local_rag",
            "document_chunks": [
                {"index": 0, "chunk_id": "chunk-1", "text": "第一段"},
                {"index": 1, "chunk_id": "chunk-2", "text": "第二段"},
            ],
            "rag_indexing": {
                "document_store": "work_events",
                "vector_db": "qdrant_vectors",
            },
        },
        occurred_at=datetime.now(UTC),
        sensitivity="normal",
    )

    class FakeAI:
        def embedding(self, text):
            return [float(len(text)), 1.0]

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "result": [
                    {
                        "id": "chunk-point",
                        "score": 0.9,
                        "payload": {"work_event_id": str(event_id), "source": "feishu"},
                    }
                ]
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url):
            return FakeResponse()

        def put(self, url, json):
            captured["points"] = json["points"]
            return FakeResponse()

        def post(self, url, json):
            captured["search"] = json
            return FakeResponse()

    monkeypatch.setattr(vector_search.settings, "qdrant_enabled", True)
    monkeypatch.setattr(vector_search, "httpx", SimpleNamespace(Client=FakeClient))
    monkeypatch.setattr("app.services.ai.vector_search.AIService", lambda: FakeAI())

    index = WorkEventVectorIndex()

    assert index.upsert_event(event) is True
    assert len(captured["points"]) == 2
    assert captured["points"][0]["payload"]["work_event_id"] == str(event_id)
    assert captured["points"][1]["payload"]["chunk_id"] == "chunk-2"

    hits = index.search("制度", company_id=company_id)

    assert hits[0]["id"] == str(event_id)
    assert captured["search"]["filter"]["must"][0]["key"] == "company_id"


def test_work_event_vector_index_skips_non_l1_hot_knowledge(monkeypatch) -> None:
    captured = {"embedding_calls": 0}
    event = SimpleNamespace(
        id=uuid4(),
        company_id=uuid4(),
        thread_id=None,
        source="feishu",
        event_type="feishu.resource.index",
        title="历史制度索引",
        content_text="只登记标题和链接",
        payload={
            "data_layer": "knowledge_cold",
            "query_path": "lark_cli_realtime",
            "rag_indexing": {
                "document_store": "work_events",
                "vector_db": "qdrant_vectors",
            },
        },
        occurred_at=datetime.now(UTC),
        sensitivity="normal",
    )

    class FakeAI:
        def embedding(self, text):
            captured["embedding_calls"] += 1
            return [1.0, 1.0]

    monkeypatch.setattr(vector_search.settings, "qdrant_enabled", True)
    monkeypatch.setattr("app.services.ai.vector_search.AIService", lambda: FakeAI())

    assert should_vectorize_work_event(event) is False
    assert WorkEventVectorIndex().upsert_event(event) is False
    assert captured["embedding_calls"] == 0


def test_mark_event_vector_indexing_result_updates_l1_rag_payload_status() -> None:
    event = SimpleNamespace(
        id=uuid4(),
        vector_status="pending",
        title="制度文档",
        content_text="整篇正文",
        payload={
            "document_chunks": [
                {"index": 0, "chunk_id": "chunk-1", "text": "第一段"},
                {"index": 1, "chunk_id": "chunk-2", "text": "第二段"},
            ],
            "rag_indexing": {
                "document_store": "work_events",
                "chunking": "queued",
                "chunk_count": "2",
                "vector_db": "qdrant_vectors",
                "rag_index": "pending",
                "vector_status": "pending",
            },
        },
    )

    mark_event_vector_indexing_result(event, indexed=True)

    assert event.vector_status == "indexed"
    assert event.payload["rag_indexing"]["vector_status"] == "indexed"
    assert event.payload["rag_indexing"]["rag_index"] == "indexed"
    assert event.payload["rag_indexing"]["indexed_point_count"] == "2"


def test_mark_event_vector_indexing_result_marks_l1_rag_payload_skipped() -> None:
    event = SimpleNamespace(
        id=uuid4(),
        vector_status="pending",
        title="空文档",
        content_text="",
        payload={
            "document_chunks": [],
            "rag_indexing": {
                "document_store": "work_events",
                "chunking": "skipped_empty",
                "chunk_count": "0",
                "vector_db": "qdrant_vectors",
                "rag_index": "pending",
                "vector_status": "pending",
            },
        },
    )

    mark_event_vector_indexing_result(event, indexed=False)

    assert event.vector_status == "skipped"
    assert event.payload["rag_indexing"]["vector_status"] == "skipped"
    assert event.payload["rag_indexing"]["rag_index"] == "skipped"
    assert event.payload["rag_indexing"]["indexed_point_count"] == "0"
