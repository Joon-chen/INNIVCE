from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


_LAST_LLM_CALL_TRACE: dict[str, Any] = {}


def record_llm_call_trace(entry: dict[str, Any]) -> dict[str, Any]:
    payload = dict(entry or {})
    payload.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    _LAST_LLM_CALL_TRACE.clear()
    _LAST_LLM_CALL_TRACE.update(payload)
    return dict(_LAST_LLM_CALL_TRACE)


def last_llm_call_trace() -> dict[str, Any]:
    return dict(_LAST_LLM_CALL_TRACE)
