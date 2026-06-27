from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.services.audit import write_audit_log
from app.services.runtime_v5.context import load_session_context, save_session_context


ACTION_TRACE_KEY = "runtime_v5_action_trace"
DECISION_TRACE_KEY = "runtime_v5_decision_trace"
ROUTE_OBSERVATION_TRACE_KEY = "runtime_v5_route_observation_trace"
PENDING_ACTION_STATUSES = {
    "queued",
    "started",
    "processing",
    "pending",
    "pending_confirmation",
    "confirmation_card_started",
    "confirmation_card_sent",
}
TERMINAL_ACTION_STATUSES = {
    "success",
    "partial",
    "error",
    "failed",
    "cancelled",
    "stale",
    "stale_cleanup",
    "expired",
    "denied",
    "skipped",
    "confirmation_card_failed",
}


def record_action_trace(chat_id: str | None, entry: dict[str, Any]) -> None:
    if not chat_id:
        return
    normalized = _normalize_action_trace_entry(entry)
    session_context = load_session_context(chat_id)
    traces = session_context.get(ACTION_TRACE_KEY)
    if not isinstance(traces, list):
        traces = []
    traces.append(normalized)
    session_context[ACTION_TRACE_KEY] = traces[-20:]
    save_session_context(chat_id, session_context)


def _normalize_action_trace_entry(entry: dict[str, Any]) -> dict[str, Any]:
    payload = dict(entry or {})
    status = str(payload.get("status") or "unknown")
    action_id = str(
        payload.get("action_id")
        or payload.get("pending_action_id")
        or payload.get("expected_action_id")
        or payload.get("card_action_id")
        or ""
    ).strip()
    if not action_id:
        action_id = uuid4().hex
    payload.setdefault("kind", "runtime_action")
    payload.setdefault("action", "")
    payload["status"] = status
    payload.setdefault("action_id", action_id)
    payload.setdefault("correlation_id", action_id)
    payload.setdefault("confirmation_token", action_id)
    payload.setdefault("confirmation_token_type", "runtime_action_id")
    payload.setdefault("strategy", payload.get("action") or "")
    payload.setdefault("source", _source_from_action(str(payload.get("action") or "")))
    payload.setdefault("sources", _sources_from_source(str(payload.get("source") or "")))
    payload.setdefault("execution_identity", _execution_identity_from_action(str(payload.get("action") or "")))
    payload.setdefault("confirmation_reasons", [])
    payload.setdefault("requires_confirmation", bool(payload.get("confirmation_reasons")))
    payload.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    payload.setdefault("updated_at", payload.get("created_at"))
    payload.setdefault("status_group", _action_status_group(status))
    payload.setdefault("is_terminal", status in TERMINAL_ACTION_STATUSES)
    payload.setdefault("is_pending", status in PENDING_ACTION_STATUSES)
    payload.setdefault("recommended_next_step", _recommended_next_step(payload))
    return payload


def _action_status_group(status: str) -> str:
    if status in TERMINAL_ACTION_STATUSES:
        return "terminal"
    if status in PENDING_ACTION_STATUSES:
        return "pending"
    if status in {"prepared", "prepare_confirm"}:
        return "prepared"
    return "unknown"


def _recommended_next_step(payload: dict[str, Any]) -> str:
    status = str(payload.get("status") or "")
    if status not in {"error", "failed", "partial", "stale"}:
        if status == "confirmation_card_failed":
            return "检查确认卡渲染、飞书卡片发送权限和按钮 payload。"
        return ""
    if status in {"stale", "stale_cleanup", "expired"}:
        return "重新发起操作，使用最新确认卡。"
    if status == "cancelled":
        return "如需继续，请重新发起操作并再次确认。"
    kind = str(payload.get("kind") or "")
    action = str(payload.get("action") or "")
    if "approval" in kind or action in {"approve", "reject", "transfer", "add_sign", "rollback", "remind", "cancel", "cc"}:
        return "重新查询待审批，确认单据仍待处理后再操作。"
    if action in {"message_send", "send_result"}:
        return "确认接收人或会话后重新发送。"
    if action in {"organization_export"}:
        return "先确认组织架构可读取，再重新创建表格。"
    return "查看错误摘要后重新发起操作。"


def _source_from_action(action: str) -> str:
    if action.startswith("approval_") or action in {"approve", "reject", "transfer", "add_sign", "rollback", "remind", "cancel", "cc"}:
        return "approval"
    if action in {"organization_export"}:
        return "people/base/im"
    if action.startswith("calendar_"):
        return "calendar"
    if action.startswith("task_"):
        return "task"
    if action.startswith("mail_"):
        return "mail"
    if action in {"message_send", "send_result"}:
        return "im"
    return ""


def _sources_from_source(source: str) -> list[str]:
    if not source:
        return []
    if "/" in source:
        return [part for part in source.split("/") if part]
    if "," in source:
        return [part.strip() for part in source.split(",") if part.strip()]
    return [source]


def _execution_identity_from_action(action: str) -> str:
    if action in {"", "approval_workbench"}:
        return "bot"
    if action.startswith("approval_") or action in {
        "approve",
        "reject",
        "transfer",
        "add_sign",
        "rollback",
        "remind",
        "cancel",
        "cc",
        "message_send",
        "send_result",
        "organization_export",
    }:
        return "user"
    if action.startswith(("task_", "calendar_", "mail_")):
        return "user"
    return "bot"


def load_action_trace(chat_id: str | None, *, limit: int = 10) -> list[dict[str, Any]]:
    if not chat_id:
        return []
    session_context = load_session_context(chat_id)
    traces = session_context.get(ACTION_TRACE_KEY)
    if not isinstance(traces, list):
        return []
    return [item for item in traces[-limit:] if isinstance(item, dict)]


def record_runtime_decision_trace(chat_id: str | None, entry: dict[str, Any]) -> None:
    if not chat_id:
        return
    payload = dict(entry or {})
    payload.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    session_context = load_session_context(chat_id)
    traces = session_context.get(DECISION_TRACE_KEY)
    if not isinstance(traces, list):
        traces = []
    traces.append(payload)
    session_context[DECISION_TRACE_KEY] = traces[-12:]
    save_session_context(chat_id, session_context)


def load_runtime_decision_trace(chat_id: str | None, *, limit: int = 5) -> list[dict[str, Any]]:
    if not chat_id:
        return []
    session_context = load_session_context(chat_id)
    traces = session_context.get(DECISION_TRACE_KEY)
    if not isinstance(traces, list):
        return []
    return [item for item in traces[-limit:] if isinstance(item, dict)]


def record_route_observation_trace(chat_id: str | None, entry: dict[str, Any]) -> None:
    if not chat_id:
        return
    payload = _normalize_route_observation_entry(entry)
    session_context = load_session_context(chat_id)
    traces = session_context.get(ROUTE_OBSERVATION_TRACE_KEY)
    if not isinstance(traces, list):
        traces = []
    traces.append(payload)
    session_context[ROUTE_OBSERVATION_TRACE_KEY] = traces[-20:]
    save_session_context(chat_id, session_context)


def load_route_observation_trace(chat_id: str | None, *, limit: int = 12) -> list[dict[str, Any]]:
    if not chat_id:
        return []
    session_context = load_session_context(chat_id)
    traces = session_context.get(ROUTE_OBSERVATION_TRACE_KEY)
    if not isinstance(traces, list):
        return []
    return [item for item in traces[-limit:] if isinstance(item, dict)]


def route_observation_summary(traces: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> dict[str, Any]:
    items = [item for item in traces if isinstance(item, dict)]
    risk_reasons: dict[str, int] = {}
    denoise_actions: dict[str, int] = {}
    risky_count = 0
    for item in items:
        if item.get("misroute_risk"):
            risky_count += 1
        for reason in item.get("risk_reasons") or []:
            key = str(reason or "").strip()
            if key:
                risk_reasons[key] = risk_reasons.get(key, 0) + 1
        action = str(item.get("denoise_action") or "").strip()
        if action:
            denoise_actions[action] = denoise_actions.get(action, 0) + 1
    latest = items[-1] if items else {}
    return {
        "available": bool(items),
        "total_count": len(items),
        "risky_count": risky_count,
        "risk_rate": round(risky_count / len(items), 3) if items else 0.0,
        "risk_reasons": risk_reasons,
        "denoise_actions": denoise_actions,
        "latest": latest,
        "status": "needs_attention" if risky_count else ("healthy" if items else "none"),
    }


def _normalize_route_observation_entry(entry: dict[str, Any]) -> dict[str, Any]:
    payload = dict(entry or {})
    observation = payload.get("route_observation")
    if isinstance(observation, dict):
        payload = {**payload, **observation}
        payload["route_observation"] = observation
    payload.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    payload["intent"] = str(payload.get("intent") or "")
    payload["question_type"] = str(payload.get("question_type") or "")
    payload["data_scope"] = str(payload.get("data_scope") or payload.get("scope") or "")
    payload["route_source"] = str(payload.get("route_source") or payload.get("route_path") or "")
    payload["denoise_action"] = str(payload.get("denoise_action") or "none")
    payload["risk_reasons"] = [str(item) for item in (payload.get("risk_reasons") or []) if str(item)]
    payload["misroute_risk"] = bool(payload.get("misroute_risk"))
    payload["confidence"] = float(payload.get("confidence") or 0.0)
    question = str(payload.get("question") or "").strip()
    if question:
        payload["question"] = question[:160]
    return payload


def write_runtime_action_audit(
    db: Session,
    *,
    company_id: UUID | None,
    actor: str | None,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    write_audit_log(
        db,
        action=action,
        company_id=company_id,
        actor=actor,
        target_type=target_type,
        target_id=target_id,
        payload=payload or {},
    )
