from __future__ import annotations

from app.services.runtime_v5.capabilities import label_for_strategy
from typing import Any

from app.services.runtime_v5.models import CommandPlan, ComposedAnswer, ExecutionResult, IntentResult, PermissionDecision, PlannerResult, ResultContext, RuntimeResult, TargetUI
from app.services.runtime_v5.runtime_action_input import build_runtime_action_input_payload


def build_runtime_result(
    *,
    command_plan: CommandPlan,
    permission: PermissionDecision,
    execution: ExecutionResult | None,
    composed: ComposedAnswer,
) -> RuntimeResult:
    """Build the V5 Runtime output contract.

    RuntimeResult is the only shape the Interaction Layer should need.
    It intentionally contains display-neutral fields and no tool logic.
    """

    result_context = composed.result_context
    result_type = result_context.result_type if result_context is not None else command_plan.planner_result.strategy
    execution_status = execution.status if execution is not None else (
        "waiting" if composed.metadata.get("requires_confirmation") or composed.metadata.get("waiting_input") else "skipped"
    )
    result_metadata = result_context.metadata if result_context is not None else {}
    company_id = str(result_metadata.get("company_id") or command_plan.context_scope.get("company_id") or "")
    scope_context = _scope_context(
        company_id=company_id,
        data_scope=str(command_plan.intent_result.data_scope or ""),
        result_metadata=result_metadata,
    )
    title = label_for_strategy(command_plan.planner_result.strategy) or command_plan.intent
    return RuntimeResult(
        result_type=result_type,
        status=str(execution_status),
        title=title,
        summary=_summary_from_composed(composed),
        items=result_context.items if result_context is not None else (),
        actions=_actions_for_result(
            result_type=result_type,
            result_context=result_context,
            company_id=company_id,
            scope_context=scope_context,
        ),
        target_ui=_target_ui_for_result(result_type=result_type, fallback=command_plan.target_ui),
        metadata={
            "company_id": company_id,
            "strategy": command_plan.planner_result.strategy,
            "intent": command_plan.intent,
            "question_type": command_plan.intent_result.question_type,
            "data_scope": command_plan.intent_result.data_scope,
            "scope_context": scope_context,
            "sources": list(command_plan.planner_result.sources),
            "permission_allowed": permission.allowed,
            "requires_confirmation": permission.requires_confirmation,
            "execution_identity": permission.execution_identity,
            "result_context": result_metadata,
        },
    )


def _scope_context(*, company_id: str, data_scope: str, result_metadata: dict[str, Any]) -> dict[str, Any]:
    existing = result_metadata.get("scope_context")
    if isinstance(existing, dict) and existing.get("scope"):
        return {**existing, "company_id": str(existing.get("company_id") or company_id)}
    return {
        "scope": _enterprise_scope(data_scope),
        "company_id": company_id,
        "filters": {},
    }


def _enterprise_scope(data_scope: str) -> str:
    normalized = data_scope.strip().lower()
    if normalized == "self":
        return "SELF"
    if normalized in {"person", "user"}:
        return "USER"
    if normalized == "department":
        return "DEPARTMENT"
    if normalized == "company":
        return "COMPANY"
    if normalized in {"project", "team"}:
        return "TEAM"
    return "COMPANY" if normalized == "organization" else "SELF"


def runtime_result_from_payload(payload: dict[str, Any]) -> RuntimeResult:
    """Rehydrate a serialized RuntimeResult without letting consumers rebuild it."""

    target_ui = payload.get("target_ui") if payload.get("target_ui") in {"card", "sidepanel", "webview", "push", "ios", "none"} else "card"
    items = payload.get("items") if isinstance(payload.get("items"), (list, tuple)) else ()
    actions = payload.get("actions") if isinstance(payload.get("actions"), (list, tuple)) else ()
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return RuntimeResult(
        result_type=str(payload.get("result_type") or ""),
        status=str(payload.get("status") or ""),
        title=str(payload.get("title") or ""),
        summary=str(payload.get("summary") or ""),
        items=tuple(item for item in items if isinstance(item, dict)),
        actions=tuple(action for action in actions if isinstance(action, dict)),
        target_ui=target_ui,
        metadata=metadata,
    )


def cached_result_context_runtime_result_payload(result_context: ResultContext) -> dict[str, Any]:
    command_plan = CommandPlan(
        intent="approval_query",
        steps=(),
        target_ui="card",
        tool_candidates=(),
        context_scope={"company_id": str(result_context.metadata.get("company_id") or ""), "mode": "single_company"},
        intent_result=IntentResult(
            question_type="query",
            intent="approval_query",
            data_scope="self",
            confidence=1.0,
            canonical_question="cached approvals",
        ),
        planner_result=PlannerResult(strategy=result_context.result_type, sources=("approval",)),
    )
    runtime_result = build_runtime_result(
        command_plan=command_plan,
        permission=PermissionDecision(allowed=True, requires_confirmation=False, execution_identity="bot"),
        execution=None,
        composed=ComposedAnswer(answer=result_context.answer, result_context=result_context),
    )
    return runtime_result_payload(runtime_result)


def runtime_result_payload(result: RuntimeResult) -> dict[str, Any]:
    """Serialize the RuntimeResult contract for metadata handoff."""

    return {
        "result_type": result.result_type,
        "status": result.status,
        "title": result.title,
        "summary": result.summary,
        "items": list(result.items),
        "actions": list(result.actions),
        "target_ui": result.target_ui,
        "item_count": len(result.items),
        "metadata": result.metadata,
    }


def _target_ui_for_result(*, result_type: str, fallback: TargetUI) -> TargetUI:
    if result_type in {"runtime_action", "runtime_pending_confirmation", "runtime_waiting_input"}:
        return "card"
    if result_type == "approval_detail":
        return "sidepanel"
    if result_type in {"approval_list", "approval_query"}:
        return "card"
    return fallback


def _actions_for_result(
    *,
    result_type: str,
    result_context,
    company_id: str = "",
    scope_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], ...]:
    if result_context is None:
        return ()
    if result_type in {"approval_list", "approval_query"}:
        return tuple(_approval_detail_action(item, index=index) for index, item in enumerate(result_context.items))
    if result_type == "task_list":
        return tuple(
            action
            for index, item in enumerate(result_context.items)
            if (action := _task_complete_action(item, index=index, company_id=company_id, scope_context=scope_context)) is not None
        )
    if result_type == "approval_detail" and result_context.items:
        item = result_context.items[0]
        return (
            _approval_mutation_action(item, action="approve", label="同意"),
            _approval_mutation_action(item, action="reject", label="拒绝"),
        )
    if result_type == "runtime_pending_confirmation":
        metadata = result_context.metadata if isinstance(result_context.metadata, dict) else {}
        token = str(metadata.get("confirmation_token") or metadata.get("action_id") or "").strip()
        return (
            {
                "action": "confirm",
                "label": "确认",
                "target_ui": "card",
                "confirmation_token": token,
                "requires_confirmation": False,
            },
            {
                "action": "cancel",
                "label": "取消",
                "target_ui": "card",
                "confirmation_token": token,
                "requires_confirmation": False,
            },
        )
    return ()


def _approval_detail_action(item: dict[str, Any], *, index: int) -> dict[str, Any]:
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else item
    return {
        "action": "open_detail",
        "label": "查看详情",
        "target_ui": "sidepanel",
        "index": index,
        "approval_code": str(raw.get("approval_code") or raw.get("process_code") or ""),
        "instance_code": str(raw.get("instance_code") or raw.get("process_code") or ""),
        "task_id": str(raw.get("task_id") or ""),
    }


def _approval_mutation_action(item: dict[str, Any], *, action: str, label: str) -> dict[str, Any]:
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else item
    return {
        "action": action,
        "label": label,
        "target_ui": "card",
        "approval_code": str(raw.get("approval_code") or raw.get("process_code") or ""),
        "instance_code": str(raw.get("instance_code") or raw.get("process_code") or ""),
        "task_id": str(raw.get("task_id") or ""),
        "requires_confirmation": True,
    }


def _task_complete_action(
    item: dict[str, Any],
    *,
    index: int,
    company_id: str,
    scope_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else item
    task_guid = str(raw.get("task_guid") or raw.get("guid") or raw.get("id") or "").strip()
    if not task_guid or not company_id:
        return None
    title = str(raw.get("title") or raw.get("summary") or raw.get("name") or "").strip()
    action_id = f"task_complete_{index}_{task_guid.replace('/', '_')}"
    runtime_action_input = build_runtime_action_input_payload(
        action_id=action_id,
        action_type="execute",
        intent="task_complete",
        strategy="task_complete",
        company_id=company_id,
        target={"task_guid": task_guid, "title": title},
        confirmed=False,
        confirmation_token=action_id,
        source_ui="card",
        message=f"完成任务：{title}" if title else "完成任务",
        sources=("task",),
        metadata={"scope_context": scope_context or {"scope": "SELF", "company_id": company_id, "filters": {}}},
    )
    return {
        "action": "task_complete",
        "label": "完成",
        "target_ui": "card",
        "index": index,
        "task_guid": task_guid,
        "requires_confirmation": True,
        "runtime_action_input": runtime_action_input,
    }


def _summary_from_composed(composed: ComposedAnswer) -> str:
    answer = str(composed.answer or "").strip()
    if not answer:
        return ""
    lines = [line.strip() for line in answer.splitlines() if line.strip()]
    return lines[0][:180] if lines else answer[:180]
