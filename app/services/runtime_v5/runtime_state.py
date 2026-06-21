from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app.services.runtime_v5.context import save_session_context
from app.services.runtime_v5.models import RuntimeActionState, RuntimeTaskState


RUNTIME_STATE_KEY = "runtime_v5_state"
PENDING_ACTION_KEY = "runtime_v5_pending_action"
DEFAULT_ACTION_TTL_SECONDS = 900


def runtime_state_from_session(session_context: dict[str, Any]) -> RuntimeTaskState | None:
    payload = session_context.get(RUNTIME_STATE_KEY)
    if not isinstance(payload, dict):
        return None
    actions = tuple(_action_state_from_payload(item) for item in payload.get("actions", []) if isinstance(item, dict))
    return RuntimeTaskState(
        task_id=str(payload.get("task_id") or ""),
        status=str(payload.get("status") or "pending"),  # type: ignore[arg-type]
        intent=str(payload.get("intent") or ""),
        strategy=str(payload.get("strategy") or ""),
        actions=actions,
        created_at=str(payload.get("created_at") or ""),
        updated_at=str(payload.get("updated_at") or ""),
        error=str(payload.get("error") or ""),
        metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
    )


def pending_action_from_runtime_state(session_context: dict[str, Any]) -> dict[str, Any] | None:
    state = runtime_state_from_session(session_context)
    if state is None:
        return None
    action = _latest_action(state)
    if action is None or action.status not in {"waiting_confirmation", "confirmed"}:
        return None
    return _restore_pending_action_payload(state=state, action=action)


def waiting_input_action_from_runtime_state(session_context: dict[str, Any]) -> dict[str, Any] | None:
    state = runtime_state_from_session(session_context)
    if state is None:
        return None
    action = _latest_action(state)
    if action is None or action.status != "waiting_input":
        return None
    return _restore_pending_action_payload(state=state, action=action)


def _restore_pending_action_payload(*, state: RuntimeTaskState, action: RuntimeActionState) -> dict[str, Any]:
    payload = dict(action.metadata.get("pending_action") if isinstance(action.metadata.get("pending_action"), dict) else {})
    payload.setdefault("id", action.action_id)
    payload.setdefault("message", action.message)
    payload.setdefault("intent", action.intent)
    payload.setdefault("strategy", action.strategy)
    payload.setdefault("sources", list(action.sources))
    payload.setdefault("confirmation_token", action.confirmation_token)
    payload.setdefault("task_state_id", state.task_id)
    payload.setdefault("action_state_status", action.status)
    payload["company_id"] = _company_id_from_restored_pending_action(state=state, action=action, pending_action=payload)
    return payload


def save_waiting_input_state(
    *,
    chat_id: str | None,
    session_context: dict[str, Any],
    pending_action: dict[str, Any],
    ttl_seconds: int = DEFAULT_ACTION_TTL_SECONDS,
) -> RuntimeTaskState:
    now = _now()
    task_id = str(pending_action.get("task_id") or uuid4().hex)
    action_id = str(pending_action.get("id") or uuid4().hex[:12])
    company_id = _company_id_from_pending_action(pending_action)
    action = RuntimeActionState(
        action_id=action_id,
        task_id=task_id,
        status="waiting_input",
        intent=str(pending_action.get("intent") or ""),
        strategy=str(pending_action.get("strategy") or ""),
        message=str(pending_action.get("message") or ""),
        sources=tuple(str(source) for source in pending_action.get("sources", []) if str(source)),
        confirmation_token=str(pending_action.get("confirmation_token") or action_id),
        created_at=now,
        updated_at=now,
        metadata={
            "company_id": company_id,
            "transitions": ["waiting_input"],
            "ttl_seconds": ttl_seconds,
            "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat(),
            "pending_action": pending_action,
            "missing_params": list(pending_action.get("missing_params", [])),
        },
    )
    state = RuntimeTaskState(
        task_id=task_id,
        status="waiting",
        intent=action.intent,
        strategy=action.strategy,
        actions=(action,),
        created_at=now,
        updated_at=now,
        metadata={"storage": "session", "company_id": company_id, "transitions": ["waiting", "waiting_input"]},
    )
    payload = dict(session_context)
    payload[PENDING_ACTION_KEY] = pending_action
    _save_state(chat_id, payload, state)
    return state


def save_waiting_confirmation_state(
    *,
    chat_id: str | None,
    session_context: dict[str, Any],
    pending_action: dict[str, Any],
    ttl_seconds: int = DEFAULT_ACTION_TTL_SECONDS,
) -> RuntimeTaskState:
    now = _now()
    task_id = str(pending_action.get("task_id") or uuid4().hex)
    action_id = str(pending_action.get("id") or uuid4().hex[:12])
    company_id = _company_id_from_pending_action(pending_action)
    action = RuntimeActionState(
        action_id=action_id,
        task_id=task_id,
        status="waiting_confirmation",
        intent=str(pending_action.get("intent") or ""),
        strategy=str(pending_action.get("strategy") or ""),
        message=str(pending_action.get("message") or ""),
        sources=tuple(str(source) for source in pending_action.get("sources", []) if str(source)),
        confirmation_token=str(pending_action.get("confirmation_token") or action_id),
        created_at=now,
        updated_at=now,
        metadata={
            "company_id": company_id,
            "transitions": ["waiting_confirmation"],
            "ttl_seconds": ttl_seconds,
            "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat(),
            "pending_action": pending_action,
        },
    )
    state = RuntimeTaskState(
        task_id=task_id,
        status="waiting",
        intent=action.intent,
        strategy=action.strategy,
        actions=(action,),
        created_at=now,
        updated_at=now,
        metadata={"storage": "session", "company_id": company_id, "transitions": ["waiting", "waiting_confirmation"]},
    )
    payload = dict(session_context)
    payload[PENDING_ACTION_KEY] = pending_action
    _save_state(chat_id, payload, state)
    return state


def mark_runtime_action_waiting_confirmation(
    *,
    chat_id: str | None,
    session_context: dict[str, Any],
    pending_action: dict[str, Any],
) -> RuntimeTaskState | None:
    state = runtime_state_from_session(session_context)
    if state is None:
        return None
    action = _latest_action(state)
    if action is None:
        return state
    updated_action = replace(
        action,
        status="waiting_confirmation",
        message=str(pending_action.get("message") or action.message),
        confirmation_token=str(pending_action.get("confirmation_token") or action.confirmation_token),
        updated_at=_now(),
        metadata={
            **action.metadata,
            "company_id": _company_id_from_pending_action(pending_action) or str(action.metadata.get("company_id") or ""),
            "pending_action": pending_action,
            "missing_params": [],
        },
    )
    updated = _replace_latest_action(state, _action_with_transition(updated_action, "waiting_confirmation"))
    updated = _state_with_transition(replace(updated, status="waiting", updated_at=_now()), "waiting_confirmation")
    payload = dict(session_context)
    payload[PENDING_ACTION_KEY] = pending_action
    _save_state(chat_id, payload, updated)
    return updated


def mark_runtime_action_confirmed(*, chat_id: str | None, session_context: dict[str, Any]) -> RuntimeTaskState | None:
    state = runtime_state_from_session(session_context)
    if state is None:
        return None
    action = _latest_action(state)
    if action is None:
        return state
    if action.status == "confirmed":
        return state
    updated = _replace_latest_action(state, _action_with_transition(replace(action, status="confirmed", updated_at=_now()), "confirmed"))
    updated = _state_with_transition(replace(updated, status="running", updated_at=_now()), "confirmed")
    _save_state(chat_id, session_context, updated)
    return updated


def mark_runtime_action_executing(*, chat_id: str | None, session_context: dict[str, Any]) -> RuntimeTaskState | None:
    state = runtime_state_from_session(session_context)
    if state is None:
        return None
    action = _latest_action(state)
    if action is None:
        return state
    if action.status == "executing":
        return state
    updated = _replace_latest_action(state, _action_with_transition(replace(action, status="executing", updated_at=_now()), "executing"))
    updated = _state_with_transition(replace(updated, status="running", updated_at=_now()), "executing")
    _save_state(chat_id, session_context, updated)
    return updated


def mark_runtime_action_done(
    *,
    chat_id: str | None,
    session_context: dict[str, Any],
    success: bool,
    error: str = "",
) -> RuntimeTaskState | None:
    state = runtime_state_from_session(session_context)
    if state is None:
        return None
    action = _latest_action(state)
    if action is None:
        return state
    status = "done" if success else "failed"
    if action.status == status:
        return state
    updated_action = _action_with_transition(replace(action, status=status, updated_at=_now(), error=error), status)
    updated = _replace_latest_action(state, updated_action)
    updated = _state_with_transition(replace(updated, status=status, updated_at=_now(), error=error), status)
    _save_state(chat_id, session_context, updated, keep_pending=False)
    return updated


def save_runtime_task_execution_state(
    *,
    chat_id: str | None,
    session_context: dict[str, Any],
    task_id: str,
    intent: str,
    strategy: str,
    success: bool,
    error: str = "",
) -> RuntimeTaskState:
    now = _now()
    state = RuntimeTaskState(
        task_id=task_id,
        status="done" if success else "failed",
        intent=intent,
        strategy=strategy,
        actions=(),
        created_at=now,
        updated_at=now,
        error=error,
        metadata={"storage": "session", "transitions": ["done" if success else "failed"]},
    )
    _save_state(chat_id, session_context, state, keep_pending=False)
    return state


def mark_runtime_action_cancelled(*, chat_id: str | None, session_context: dict[str, Any]) -> RuntimeTaskState | None:
    return _mark_terminal(chat_id=chat_id, session_context=session_context, action_status="cancelled", task_status="failed")


def mark_runtime_action_stale(*, chat_id: str | None, session_context: dict[str, Any]) -> RuntimeTaskState | None:
    return _mark_terminal(chat_id=chat_id, session_context=session_context, action_status="stale", task_status="failed")


def clear_runtime_state(*, chat_id: str | None, session_context: dict[str, Any]) -> None:
    payload = dict(session_context)
    payload.pop(RUNTIME_STATE_KEY, None)
    payload.pop(PENDING_ACTION_KEY, None)
    save_session_context(chat_id, payload)


def runtime_state_payload(state: RuntimeTaskState | None) -> dict[str, Any]:
    if state is None:
        return {}
    return asdict(state)


def _mark_terminal(*, chat_id: str | None, session_context: dict[str, Any], action_status: str, task_status: str) -> RuntimeTaskState | None:
    state = runtime_state_from_session(session_context)
    if state is None:
        return None
    action = _latest_action(state)
    if action is None:
        return state
    updated = _replace_latest_action(state, _action_with_transition(replace(action, status=action_status, updated_at=_now()), action_status))  # type: ignore[arg-type]
    updated = _state_with_transition(replace(updated, status=task_status, updated_at=_now()), action_status)  # type: ignore[arg-type]
    _save_state(chat_id, session_context, updated, keep_pending=False)
    return updated


def _save_state(chat_id: str | None, session_context: dict[str, Any], state: RuntimeTaskState, *, keep_pending: bool = True) -> None:
    payload = dict(session_context)
    payload[RUNTIME_STATE_KEY] = asdict(state)
    if not keep_pending:
        payload.pop(PENDING_ACTION_KEY, None)
    save_session_context(chat_id, payload)


def _action_state_from_payload(payload: dict[str, Any]) -> RuntimeActionState:
    return RuntimeActionState(
        action_id=str(payload.get("action_id") or ""),
        task_id=str(payload.get("task_id") or ""),
        status=str(payload.get("status") or "pending"),  # type: ignore[arg-type]
        intent=str(payload.get("intent") or ""),
        strategy=str(payload.get("strategy") or ""),
        message=str(payload.get("message") or ""),
        sources=tuple(str(source) for source in payload.get("sources", []) if str(source)),
        confirmation_token=str(payload.get("confirmation_token") or ""),
        created_at=str(payload.get("created_at") or ""),
        updated_at=str(payload.get("updated_at") or ""),
        error=str(payload.get("error") or ""),
        metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
    )


def _latest_action(state: RuntimeTaskState) -> RuntimeActionState | None:
    return state.actions[-1] if state.actions else None


def _replace_latest_action(state: RuntimeTaskState, action: RuntimeActionState) -> RuntimeTaskState:
    return replace(state, actions=(*state.actions[:-1], action))


def _state_with_transition(state: RuntimeTaskState, transition: str) -> RuntimeTaskState:
    return replace(state, metadata={**state.metadata, "transitions": _append_transition(state.metadata, transition)})


def _action_with_transition(action: RuntimeActionState, transition: str) -> RuntimeActionState:
    return replace(action, metadata={**action.metadata, "transitions": _append_transition(action.metadata, transition)})


def _append_transition(metadata: dict[str, Any], transition: str) -> list[str]:
    items = metadata.get("transitions") if isinstance(metadata.get("transitions"), list) else []
    return [*items, transition]


def _company_id_from_pending_action(pending_action: dict[str, Any]) -> str:
    company_id = str(pending_action.get("company_id") or "").strip()
    if company_id:
        return company_id
    action_input = pending_action.get("runtime_action_input")
    if not isinstance(action_input, dict):
        return ""
    context = action_input.get("context")
    if not isinstance(context, dict):
        return ""
    return str(context.get("company_id") or "").strip()


def _company_id_from_restored_pending_action(
    *,
    state: RuntimeTaskState,
    action: RuntimeActionState,
    pending_action: dict[str, Any],
) -> str:
    return (
        _company_id_from_pending_action(pending_action)
        or str(action.metadata.get("company_id") or "").strip()
        or str(state.metadata.get("company_id") or "").strip()
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
