from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from app.core.config import settings
from app.services.runtime_v5.models import (
    ResultContext,
    RuntimeContext,
    RuntimeIdentity,
    RuntimeProfile,
    RuntimeScope,
)


DEFAULT_PROFILE = RuntimeProfile()
RESULT_CONTEXT_TTL_SECONDS = 1800
SESSION_CONTEXT_TTL_SECONDS = 1800
PORTAL_SESSION_TTL_SECONDS = 86400
PEOPLE_SNAPSHOT_TTL_SECONDS = 600


def build_runtime_context(
    *,
    message: str,
    identity: Any,
    company_id: UUID,
    chat_id: str | None = None,
    runtime_scope: RuntimeScope | None = None,
) -> RuntimeContext:
    scope = runtime_scope or RuntimeScope(
        scope_type="single_company",
        company_ids=(company_id,),
        active_company_id=company_id,
    )
    return RuntimeContext(
        identity=runtime_identity_from_feishu(identity),
        runtime_scope=scope,
        current_message=message,
        session_context=load_session_context(chat_id),
        profile=load_runtime_profile(getattr(identity, "open_id", "") or ""),
        result_context=load_result_context(chat_id),
        chat_id=chat_id,
    )


def runtime_identity_from_feishu(identity: Any) -> RuntimeIdentity:
    return RuntimeIdentity(
        user_id=str(getattr(identity, "user_id", "") or getattr(identity, "open_id", "") or ""),
        open_id=str(getattr(identity, "open_id", "") or ""),
        role=str(getattr(identity, "role", "") or ""),
        display_name=str(getattr(identity, "display_name", "") or ""),
        department_id=str(getattr(identity, "department_id", "") or ""),
        domains=tuple(getattr(identity, "domains", ()) or ()),
    )


def load_session_context(chat_id: str | None) -> dict[str, Any]:
    if not chat_id:
        return {}
    return _load_json(f"feishu:session:{chat_id}") or {}


def save_session_context(chat_id: str | None, payload: dict[str, Any]) -> None:
    if not chat_id:
        return
    _save_json(f"feishu:session:{chat_id}", payload, ttl=SESSION_CONTEXT_TTL_SECONDS)


def load_portal_session_context(chat_id: str | None) -> dict[str, Any]:
    if not chat_id:
        return {}
    return _load_json(f"feishu:portal_session:{chat_id}") or {}


def save_portal_session_context(chat_id: str | None, payload: dict[str, Any]) -> None:
    if not chat_id:
        return
    _save_json(f"feishu:portal_session:{chat_id}", payload, ttl=PORTAL_SESSION_TTL_SECONDS)


def load_result_context(chat_id: str | None) -> ResultContext | None:
    if not chat_id:
        return None
    payload = _load_json(f"feishu:result:{chat_id}")
    if not isinstance(payload, dict):
        return None
    if payload.get("runtime_version") != "v5":
        return None
    items = payload.get("items")
    if not isinstance(items, list):
        return None
    return ResultContext(
        result_type=str(payload.get("result_type") or ""),
        query_id=str(payload.get("query_id") or ""),
        count=int(payload.get("count") or 0),
        items=tuple(item for item in items if isinstance(item, dict)),
        metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        answer=str(payload.get("answer") or ""),
    )


def save_result_context(chat_id: str | None, result_context: ResultContext | None) -> None:
    if not chat_id or result_context is None:
        return
    saved_at = datetime.now(timezone.utc)
    expires_at = saved_at + timedelta(seconds=RESULT_CONTEXT_TTL_SECONDS)
    metadata = dict(result_context.metadata or {})
    normalized_items = _normalize_result_context_items(result_context.items)
    metadata.setdefault("saved_at", saved_at.isoformat())
    metadata.setdefault("expires_at", expires_at.isoformat())
    metadata.setdefault("ttl_seconds", RESULT_CONTEXT_TTL_SECONDS)
    metadata.setdefault("item_count", len(normalized_items))
    metadata.setdefault("display_count", len(normalized_items))
    metadata.setdefault("items_normalized", True)
    metadata.setdefault("index_base", 1)
    metadata.setdefault("followup_fields", _infer_result_context_followup_fields(normalized_items))
    metadata.setdefault("item_identity_fields", _infer_result_context_identity_fields(normalized_items))
    metadata.setdefault("consume_policy", _result_context_consume_policy(metadata, result_context, item_count=len(normalized_items)))
    payload = {
        "runtime_version": "v5",
        "result_type": result_context.result_type,
        "query_id": result_context.query_id,
        "count": result_context.count,
        "items": list(normalized_items),
        "metadata": metadata,
        "answer": result_context.answer,
    }
    _save_json(f"feishu:result:{chat_id}", payload, ttl=RESULT_CONTEXT_TTL_SECONDS)
    _record_result_context_event(
        chat_id,
        {
            "action": "save",
            "result_type": result_context.result_type,
            "count": result_context.count,
            "query_id": result_context.query_id,
            "context_kind": metadata.get("context_kind"),
            "source": metadata.get("source"),
            "operation": metadata.get("operation"),
            "route_path": metadata.get("route_path"),
            "result_sources": metadata.get("result_sources") or metadata.get("sources"),
            "execution_status": metadata.get("execution_status"),
            "action_id": metadata.get("action_id"),
            "confirmation_token": metadata.get("confirmation_token"),
            "confirmation_token_available": metadata.get("confirmation_token_available"),
            "action_status_group": metadata.get("action_status_group"),
            "is_terminal_action": metadata.get("is_terminal_action"),
            "is_pending_action": metadata.get("is_pending_action"),
            "item_count": metadata.get("item_count", result_context.count),
            "items_normalized": metadata.get("items_normalized"),
            "index_base": metadata.get("index_base"),
            "followup_fields": metadata.get("followup_fields", []),
            "item_identity_fields": metadata.get("item_identity_fields", []),
            "consume_policy": metadata.get("consume_policy", {}),
            "source_execution_step_count": len(metadata.get("source_execution_steps", [])) if isinstance(metadata.get("source_execution_steps"), list) else 0,
            "source_execution_status": metadata.get("source_execution_status", {}),
            "saved_at": metadata.get("saved_at"),
            "expires_at": metadata.get("expires_at"),
            "ttl_seconds": metadata.get("ttl_seconds"),
        },
    )


def _normalize_result_context_items(items: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    normalized: list[dict[str, Any]] = []
    for position, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        payload = dict(item)
        payload.setdefault("index", position)
        payload.setdefault("_result_index", position)
        normalized.append(payload)
    return tuple(normalized)


def clear_result_context(chat_id: str | None, *, reason: str = "", source: str = "") -> None:
    if not chat_id:
        return
    _delete_key(f"feishu:result:{chat_id}")
    _record_result_context_event(chat_id, {"action": "clear", "reason": reason, "source": source})


def load_result_context_events(chat_id: str | None, *, limit: int = 8) -> list[dict[str, Any]]:
    if not chat_id:
        return []
    session_context = load_session_context(chat_id)
    events = session_context.get("runtime_v5_result_context_events")
    if not isinstance(events, list):
        return []
    return [item for item in events[-limit:] if isinstance(item, dict)]


def _record_result_context_event(chat_id: str | None, event: dict[str, Any]) -> None:
    if not chat_id:
        return
    session_context = _load_json(f"feishu:session:{chat_id}") or {}
    if not isinstance(session_context, dict):
        session_context = {}
    events = session_context.get("runtime_v5_result_context_events")
    if not isinstance(events, list):
        events = []
    events.append({**event, "created_at": datetime.now(timezone.utc).isoformat(), "ttl_seconds": RESULT_CONTEXT_TTL_SECONDS})
    session_context["runtime_v5_result_context_events"] = events[-20:]
    _save_json(f"feishu:session:{chat_id}", session_context, ttl=SESSION_CONTEXT_TTL_SECONDS)


def load_people_snapshot(company_id: UUID | str | None) -> dict[str, Any] | None:
    if not company_id:
        return None
    return _load_json(f"runtime_v5:people_snapshot:{company_id}")


def save_people_snapshot(company_id: UUID | str | None, payload: dict[str, Any]) -> None:
    if not company_id or not payload:
        return
    _save_json(f"runtime_v5:people_snapshot:{company_id}", payload, ttl=PEOPLE_SNAPSHOT_TTL_SECONDS)


def _infer_result_context_followup_fields(items: tuple[dict[str, Any], ...]) -> list[str]:
    fields: set[str] = set()
    blocked = {"raw", "metadata", "payload", "content", "body", "answer"}
    for item in items[:20]:
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            if key in blocked or str(key).startswith("_"):
                continue
            if value not in (None, "", [], {}):
                fields.add(str(key))
    priority = (
        "index",
        "id",
        "name",
        "title",
        "summary",
        "action_id",
        "confirmation_token",
        "status_group",
        "operation",
        "route_path",
        "applicant",
        "amount",
        "target",
        "resolved_target_name",
        "resolved_user_id",
        "resolved_chat_id",
        "department",
        "email",
        "mobile",
        "status",
        "url",
        "created_at",
    )
    ordered = [field for field in priority if field in fields]
    ordered.extend(sorted(fields.difference(ordered)))
    return ordered[:16]


def _infer_result_context_identity_fields(items: tuple[dict[str, Any], ...]) -> list[str]:
    identity_candidates = (
        "id",
        "user_id",
        "open_id",
        "department_id",
        "task_id",
        "instance_code",
        "approval_code",
        "message_id",
        "chat_id",
        "action_id",
        "correlation_id",
        "confirmation_token",
        "resolved_user_id",
        "resolved_chat_id",
        "app_token",
        "table_id",
        "url",
    )
    fields: list[str] = []
    for key in identity_candidates:
        if any(isinstance(item, dict) and item.get(key) not in (None, "") for item in items[:20]):
            fields.append(key)
    return fields


def _result_context_consume_policy(
    metadata: dict[str, Any],
    result_context: ResultContext,
    *,
    item_count: int | None = None,
) -> dict[str, Any]:
    context_kind = str(metadata.get("context_kind") or "")
    actual_item_count = int(item_count if item_count is not None else metadata.get("item_count") or len(result_context.items))
    return {
        "prefer_items": True,
        "allow_answer_fallback": context_kind in {"no_result", "action_receipt"} and actual_item_count == 0,
        "requires_refresh_when_expired": True,
        "supports_index_followup": actual_item_count > 0,
        "supports_detail_followup": actual_item_count > 0 and context_kind != "no_result",
    }


def load_runtime_profile(open_id: str) -> RuntimeProfile:
    if not open_id:
        return DEFAULT_PROFILE
    payload = _load_json(f"feishu:profile:{open_id}") or {}
    if not isinstance(payload, dict):
        return DEFAULT_PROFILE
    return RuntimeProfile(
        style=str(payload.get("style") or DEFAULT_PROFILE.style),
        verbosity=str(payload.get("verbosity") or DEFAULT_PROFILE.verbosity),
        use_formatting=bool(payload.get("use_formatting", DEFAULT_PROFILE.use_formatting)),
        tone_tips=str(payload.get("tone_tips") or ""),
    )


def _load_json(key: str) -> dict[str, Any] | None:
    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        raw = client.get(key)
        if not raw:
            return None
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _save_json(key: str, payload: dict[str, Any], *, ttl: int) -> None:
    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        client.setex(key, ttl, json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:
        return


def _delete_key(key: str) -> None:
    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        client.delete(key)
    except Exception:
        return
