from __future__ import annotations

from dataclasses import asdict, replace
from typing import Any
from uuid import uuid4

from app.services.runtime_v5.capabilities import label_for_strategy, route_path_for_strategy
from app.services.runtime_v5.models import RuntimeActionInput, RuntimePendingAction
from app.services.runtime_v5.runtime_action_input import runtime_action_input_payload


def pending_action_from_runtime_action_input(
    action_input: RuntimeActionInput,
    *,
    message: str,
    missing_params: tuple[str, ...] | list[str] = (),
    input_contract: dict[str, Any] | None = None,
) -> RuntimePendingAction:
    strategy = str(action_input.strategy or action_input.intent or "runtime_action").strip()
    action_id = str(action_input.confirmation.token or action_input.action_id or uuid4().hex[:12]).strip()
    company_id = str(action_input.context.company_id or "").strip()
    if not company_id:
        raise ValueError("RuntimePendingAction requires company_id")
    return RuntimePendingAction(
        action_id=action_id,
        message=message,
        intent=str(action_input.intent or strategy).strip(),
        intent_label=str(label_for_strategy(strategy) or strategy).strip(),
        strategy=strategy,
        sources=tuple(action_input.sources),
        company_id=company_id,
        entities=dict(action_input.target),
        route_path=route_path_for_strategy(strategy),
        confirmation_token=action_id,
        runtime_action_input=runtime_action_input_payload(action_input),
        missing_params=tuple(str(param) for param in missing_params if str(param)),
        input_contract=input_contract or {},
    )


def runtime_pending_action_payload(pending_action: RuntimePendingAction) -> dict[str, Any]:
    payload = asdict(pending_action)
    payload["id"] = pending_action.action_id
    payload["sources"] = list(pending_action.sources)
    payload["missing_params"] = list(pending_action.missing_params)
    return payload


def runtime_pending_action_with_missing_input(
    pending_action: RuntimePendingAction,
    *,
    missing_params: tuple[str, ...] | list[str],
) -> RuntimePendingAction:
    missing = tuple(str(param) for param in missing_params if str(param))
    return replace(
        pending_action,
        missing_params=missing,
        input_contract={
            "status": "waiting_input",
            "missing_params": list(missing),
        },
    )
