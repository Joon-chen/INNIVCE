from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.services.runtime_v5.models import RuntimeActionConfirmation, RuntimeActionContext, RuntimeActionInput


RUNTIME_ACTION_INPUT_KEY = "runtime_v5_action_input"
LEGACY_ACTION_REQUEST_KEY = "runtime_v5_action_request"


def build_runtime_action_input(
    *,
    action_id: str,
    action_type: str,
    intent: str,
    strategy: str,
    company_id: str,
    target: dict[str, Any] | None = None,
    confirmed: bool = False,
    confirmation_token: str = "",
    chat_id: str = "",
    user_id: str = "",
    open_id: str = "",
    source_ui: str = "unknown",
    message: str = "",
    sources: tuple[str, ...] | list[str] = (),
    metadata: dict[str, Any] | None = None,
) -> RuntimeActionInput:
    """Build the frozen RuntimeActionInput contract for UI producers."""

    normalized_company_id = str(company_id or "").strip()
    if not normalized_company_id:
        raise ValueError("RuntimeActionInput requires context.company_id")
    return RuntimeActionInput(
        action_id=str(action_id or confirmation_token or uuid4().hex[:12]),
        action_type=_normalize_action_type(action_type),
        intent=str(intent or strategy).strip(),
        strategy=str(strategy or intent).strip(),
        target=target or {},
        confirmation=RuntimeActionConfirmation(
            confirmed=bool(confirmed),
            token=str(confirmation_token or ""),
        ),
        context=RuntimeActionContext(
            company_id=normalized_company_id,
            chat_id=str(chat_id or ""),
            user_id=str(user_id or ""),
            open_id=str(open_id or ""),
            source_ui=_source_ui(source_ui),
        ),
        message=str(message or ""),
        sources=tuple(str(source) for source in sources if str(source)),
        metadata=metadata or {},
    )


def build_runtime_action_input_payload(**kwargs: Any) -> dict[str, Any]:
    return runtime_action_input_payload(build_runtime_action_input(**kwargs))


def runtime_action_input_from_session(session_context: dict[str, Any]) -> RuntimeActionInput | None:
    payload = session_context.get(RUNTIME_ACTION_INPUT_KEY)
    if isinstance(payload, dict):
        return runtime_action_input_from_payload(payload)
    legacy = session_context.get(LEGACY_ACTION_REQUEST_KEY)
    if isinstance(legacy, dict):
        return runtime_action_input_from_legacy_request(legacy)
    return None


def runtime_action_input_from_payload(payload: dict[str, Any]) -> RuntimeActionInput:
    confirmation = payload.get("confirmation") if isinstance(payload.get("confirmation"), dict) else {}
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    return RuntimeActionInput(
        action_id=str(payload.get("action_id") or confirmation.get("token") or uuid4().hex[:12]),
        action_type=_action_type(payload),
        intent=str(payload.get("intent") or ""),
        strategy=str(payload.get("strategy") or ""),
        target=payload.get("target") if isinstance(payload.get("target"), dict) else {},
        confirmation=RuntimeActionConfirmation(
            confirmed=bool(confirmation.get("confirmed")),
            token=str(confirmation.get("token") or ""),
        ),
        context=RuntimeActionContext(
            company_id=str(context.get("company_id") or ""),
            chat_id=str(context.get("chat_id") or ""),
            user_id=str(context.get("user_id") or ""),
            open_id=str(context.get("open_id") or ""),
            source_ui=_source_ui(context.get("source_ui")),
        ),
        message=str(payload.get("message") or ""),
        sources=tuple(str(source) for source in payload.get("sources", []) if str(source)),
        metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
    )


def runtime_action_input_from_legacy_request(payload: dict[str, Any]) -> RuntimeActionInput:
    token = str(payload.get("confirmation_token") or payload.get("action_id") or "").strip()
    strategy = str(payload.get("strategy") or payload.get("intent") or "").strip()
    action_type = str(payload.get("action_type") or strategy.removeprefix("approval_") or "execute").strip()
    return RuntimeActionInput(
        action_id=token or uuid4().hex[:12],
        action_type=_normalize_action_type(action_type),
        intent=str(payload.get("intent") or strategy).strip(),
        strategy=strategy,
        target=payload.get("target") if isinstance(payload.get("target"), dict) else {},
        confirmation=RuntimeActionConfirmation(
            confirmed=bool(payload.get("confirmed")),
            token=token,
        ),
        context=RuntimeActionContext(
            company_id=str(payload.get("company_id") or ""),
            chat_id=str(payload.get("chat_id") or ""),
            user_id=str(payload.get("user_id") or ""),
            open_id=str(payload.get("open_id") or ""),
            source_ui=_source_ui(payload.get("source_ui") or "portal"),
        ),
        message=str(payload.get("message") or ""),
        sources=tuple(str(source) for source in payload.get("sources", []) if str(source)),
        metadata={"legacy_runtime_v5_action_request": True},
    )


def runtime_action_input_payload(action_input: RuntimeActionInput) -> dict[str, Any]:
    return {
        "action_id": action_input.action_id,
        "action_type": action_input.action_type,
        "intent": action_input.intent,
        "strategy": action_input.strategy,
        "target": dict(action_input.target),
        "confirmation": {
            "confirmed": action_input.confirmation.confirmed,
            "token": action_input.confirmation.token,
        },
        "context": {
            "company_id": action_input.context.company_id,
            "chat_id": action_input.context.chat_id,
            "user_id": action_input.context.user_id,
            "open_id": action_input.context.open_id,
            "source_ui": action_input.context.source_ui,
        },
        "message": action_input.message,
        "sources": list(action_input.sources),
        "metadata": dict(action_input.metadata),
    }


def _action_type(payload: dict[str, Any]) -> str:
    return _normalize_action_type(str(payload.get("action_type") or payload.get("action") or "execute"))


def _normalize_action_type(value: str) -> str:
    normalized = value.strip()
    if normalized in {"approve", "reject", "transfer", "add_sign", "confirm", "cancel", "open_detail", "execute"}:
        return normalized
    return "execute"


def _source_ui(value: Any) -> str:
    source = str(value or "unknown").strip()
    if source in {"portal", "card", "sidepanel", "webview", "bot", "unknown"}:
        return source
    return "unknown"
