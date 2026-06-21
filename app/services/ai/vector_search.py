from typing import Any
from uuid import NAMESPACE_URL
from uuid import UUID
from uuid import uuid5

import httpx

from app.core.config import settings
from app.models.entities import WorkEvent
from app.services.ai.openai_service import AIService


class WorkEventVectorIndex:
    def __init__(self) -> None:
        self.base_url = settings.qdrant_url.rstrip("/")
        self.collection = settings.qdrant_collection
        self.ai = AIService()

    def ensure_collection(self, size: int) -> None:
        if not settings.qdrant_enabled:
            return
        with httpx.Client(timeout=settings.request_timeout_seconds) as client:
            response = client.get(f"{self.base_url}/collections/{self.collection}")
            if response.status_code == 200:
                return
            response = client.put(
                f"{self.base_url}/collections/{self.collection}",
                json={"vectors": {"size": size, "distance": "Cosine"}},
            )
            response.raise_for_status()

    def upsert_event(self, event: WorkEvent) -> bool:
        if not settings.qdrant_enabled:
            return False
        if not should_vectorize_work_event(event):
            return False
        segments = _event_vector_segments(event)
        if not segments:
            return False
        points = []
        vector_size = 0
        for segment in segments:
            vector = self.ai.embedding(segment["text"])
            if not vector:
                continue
            vector_size = vector_size or len(vector)
            points.append(
                {
                    "id": segment["point_id"],
                    "vector": vector,
                    "payload": {
                        "company_id": str(event.company_id),
                        "work_event_id": str(event.id),
                        "thread_id": event.thread_id,
                        "source": event.source,
                        "event_type": event.event_type,
                        "title": event.title,
                        "chunk_index": segment.get("chunk_index"),
                        "chunk_id": segment.get("chunk_id"),
                        "occurred_at": event.occurred_at.isoformat(),
                        "sensitivity": event.sensitivity,
                    },
                }
            )
        if not points:
            return False
        self.ensure_collection(vector_size)
        with httpx.Client(timeout=settings.request_timeout_seconds) as client:
            response = client.put(
                f"{self.base_url}/collections/{self.collection}/points",
                json={"points": points},
            )
            response.raise_for_status()
        return True

    def search(
        self,
        query: str,
        *,
        company_id: UUID | str,
        limit: int = 10,
        chat_id: str | None = None,
        sources: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not settings.qdrant_enabled:
            return []
        vector = self.ai.embedding(query)
        if not vector:
            return []
        self.ensure_collection(len(vector))
        filters: list[dict[str, Any]] = [
            {"key": "company_id", "match": {"value": str(company_id)}},
        ]
        if chat_id:
            filters.append({"key": "thread_id", "match": {"value": chat_id}})
        if sources:
            filters.append(
                {"key": "source", "match": {"any": sorted(sources)}},
            )
        with httpx.Client(timeout=settings.request_timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/collections/{self.collection}/points/search",
                json={
                    "vector": vector,
                    "limit": limit,
                    "with_payload": True,
                    "score_threshold": settings.qdrant_score_threshold,
                    "filter": {"must": filters},
                },
            )
            response.raise_for_status()
            results = response.json().get("result", [])
        return [
            {
                "id": (result.get("payload") or {}).get("work_event_id") or result.get("id"),
                "score": result.get("score"),
                "payload": result.get("payload"),
            }
            for result in results
        ]


def should_vectorize_work_event(event: WorkEvent) -> bool:
    payload = event.payload if isinstance(event.payload, dict) else {}
    rag_indexing = payload.get("rag_indexing")
    if not isinstance(rag_indexing, dict):
        return False
    return (
        payload.get("data_layer") == "knowledge_hot"
        and payload.get("query_path") == "local_rag"
        and rag_indexing.get("document_store") == "work_events"
        and rag_indexing.get("vector_db") == "qdrant_vectors"
    )


def _event_vector_segments(event: WorkEvent) -> list[dict[str, Any]]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    chunks = payload.get("document_chunks")
    if isinstance(chunks, list):
        segments = []
        for raw in chunks:
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("text") or "").strip()
            if not text:
                continue
            index = int(raw.get("index") or len(segments))
            point_id = str(event.id) if index == 0 else str(uuid5(NAMESPACE_URL, f"work-event:{event.id}:chunk:{index}"))
            segments.append(
                {
                    "point_id": point_id,
                    "text": f"{event.title or ''}\n{text}".strip(),
                    "chunk_index": index,
                    "chunk_id": str(raw.get("chunk_id") or f"chunk-{index + 1}"),
                }
            )
        if segments:
            return segments
    text = f"{event.title or ''}\n{event.content_text or ''}".strip()
    if not text:
        return []
    return [{"point_id": str(event.id), "text": text, "chunk_index": None, "chunk_id": None}]


def mark_event_vector_indexing_result(event: WorkEvent, *, indexed: bool) -> None:
    status = "indexed" if indexed else "skipped"
    event.vector_status = status
    payload = event.payload if isinstance(event.payload, dict) else None
    if not payload or not isinstance(payload.get("rag_indexing"), dict):
        return
    updated_payload = dict(payload)
    rag_indexing = dict(updated_payload["rag_indexing"])
    rag_indexing["vector_status"] = status
    rag_indexing["rag_index"] = status
    segments = _event_vector_segments(event)
    if segments:
        rag_indexing["indexed_point_count"] = str(len(segments) if indexed else 0)
    updated_payload["rag_indexing"] = rag_indexing
    event.payload = updated_payload
