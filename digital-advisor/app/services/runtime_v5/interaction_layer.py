from __future__ import annotations

from typing import Any

from app.services.runtime_v5.models import InteractionPayload, RuntimeResult


def interaction_payload_from_runtime_result(result: RuntimeResult) -> InteractionPayload:
    """Translate Runtime output into display input.

    Interaction Layer is display-only. It receives RuntimeResult and turns it
    into a renderer payload without planning, policy checks or tool calls.
    """

    return InteractionPayload(
        payload_type=_payload_type_for_result(result),
        title=result.title,
        summary=result.summary,
        recommendation=str(result.metadata.get("recommendation") or ""),
        status=result.status,
        items=result.items,
        actions=result.actions,
        metadata={
            **result.metadata,
            "target_ui": result.target_ui,
            "result_type": result.result_type,
        },
    )


def interaction_payload_payload(payload: InteractionPayload) -> dict[str, Any]:
    """Serialize renderer input without exposing business-state decisions."""

    return {
        "payload_type": payload.payload_type,
        "title": payload.title,
        "summary": payload.summary,
        "recommendation": payload.recommendation,
        "status": payload.status,
        "items": list(payload.items),
        "actions": list(payload.actions),
        "metadata": payload.metadata,
    }


def _payload_type_for_result(result: RuntimeResult) -> str:
    if result.result_type in {"runtime_action", "approval_approve", "approval_reject"}:
        return "feedback"
    if result.result_type in {"base_export", "file_delivery", "document_delivery"}:
        return "delivery"
    if result.status in {"waiting", "pending_confirmation"}:
        return "action"
    return "summary"
