from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any
from uuid import uuid4

from app.services.runtime_v5.capabilities import label_for_strategy, route_path_for_strategy
from app.services.runtime_v5.agent_runtime_core import execute_runtime_task
from app.services.runtime_v5.action_observer import record_route_observation_trace
from app.services.runtime_v5.capability_router import ResourceProvider
from app.services.runtime_v5.clarification import build_clarification_guide
from app.services.runtime_v5.clarification_reply import resolve_clarification_reply
from app.services.runtime_v5.composer import compose_answer
from app.services.runtime_v5.command_layer import build_command_plan
from app.services.runtime_v5.context import clear_result_context, save_result_context, save_session_context
from app.services.runtime_v5.intent import recognize_intent
from app.services.runtime_v5.intent_layers import should_start_new_question_over_result_context
from app.services.runtime_v5.models import AnswerEnvelope, ComposedAnswer, ExecutionResult, PermissionDecision, ProviderResult, ResultContext, RuntimeContext
from app.services.runtime_v5.policy_layer import evaluate_policy
from app.services.runtime_v5.permission import check_runtime_permission
from app.services.runtime_v5.planner import plan_task
from app.services.runtime_v5.provider_snapshot import build_runtime_provider_snapshot
from app.services.runtime_v5.result_followup import detect_result_followup
from app.services.runtime_v5.runtime_action_input import (
    LEGACY_ACTION_REQUEST_KEY,
    RUNTIME_ACTION_INPUT_KEY,
    runtime_action_input_from_session,
    runtime_action_input_payload,
)
from app.services.runtime_v5.runtime_missing_params import (
    action_input_missing_params,
    pending_action_with_user_input,
    waiting_input_still_missing,
)
from app.services.runtime_v5.runtime_pending_action import (
    pending_action_from_runtime_action_input,
    runtime_pending_action_payload,
    runtime_pending_action_with_missing_input,
)
from app.services.runtime_v5.runtime_result import build_runtime_result, runtime_result_payload
from app.services.runtime_v5.runtime_state import (
    clear_runtime_state,
    mark_runtime_action_cancelled,
    mark_runtime_action_confirmed,
    mark_runtime_action_done,
    mark_runtime_action_executing,
    mark_runtime_action_stale,
    mark_runtime_action_waiting_confirmation,
    pending_action_from_runtime_state,
    runtime_state_from_session,
    runtime_state_payload,
    save_runtime_task_execution_state,
    save_waiting_confirmation_state,
    save_waiting_input_state,
    waiting_input_action_from_runtime_state,
)


_PENDING_ACTION_KEY = "runtime_v5_pending_action"
_WAITING_INPUT_STARTED_KEY = "runtime_v5_waiting_input_started"
_PENDING_ACTION_TTL_SECONDS = 900
_CONFIRM_TERMS = ("确认", "确认执行", "可以执行", "继续执行", "执行吧", "同意", "是", "对", "可以", "好的", "好")
_CANCEL_TERMS = ("取消", "不要执行", "别执行", "算了")


def run_runtime_v5(
    *,
    context: RuntimeContext,
    providers: dict[str, ResourceProvider] | None = None,
) -> AnswerEnvelope:
    timer = _RuntimeTimer()
    runtime_provider_snapshot = build_runtime_provider_snapshot(providers)
    action_input = runtime_action_input_from_session(context.session_context)
    if action_input is not None and action_input.metadata.get("legacy_runtime_v5_action_request"):
        command_plan, intent, plan, _permission = _build_command_and_policy(
            context=context,
            runtime_provider_snapshot=runtime_provider_snapshot,
            timer=timer,
        )
        permission = PermissionDecision(
            allowed=False,
            reason="legacy_runtime_action_request_unsupported",
            metadata={
                "policy_layer": "v5",
                "runtime_action_input_contract": {
                    "legacy_runtime_v5_action_request": True,
                    "violation": "legacy_action_request_not_allowed",
                },
            },
        )
        answer = "旧版动作入口已停用，请从 RuntimeActionInput 入口重新发起操作。"
        session_context = dict(context.session_context)
        session_context.pop(RUNTIME_ACTION_INPUT_KEY, None)
        session_context.pop(LEGACY_ACTION_REQUEST_KEY, None)
        session_context["runtime_v5_last_action_input"] = runtime_action_input_payload(action_input)
        context = replace(context, session_context=session_context)
        save_session_context(context.chat_id, session_context)
        result_context = _result_context_with_scope(
            context,
            _runtime_action_input_contract_error_context(
                intent=intent,
                plan=plan,
                action_input=action_input,
                answer=answer,
                reason="legacy_runtime_action_request_unsupported",
            ),
        )
        _save_runtime_result_context(context, result_context)
        composed = ComposedAnswer(answer=answer, result_context=result_context)
        composed = _with_pipeline_timing(composed, timer)
        composed = _with_runtime_result_metadata(
            command_plan=command_plan,
            permission=permission,
            execution=None,
            composed=composed,
        )
        return AnswerEnvelope(
            context=context,
            intent=intent,
            plan=plan,
            permission=permission,
            execution=None,
            composed=composed,
        )
    if action_input is not None and not action_input.context.company_id:
        command_plan, intent, plan, _permission = _build_command_and_policy(
            context=context,
            runtime_provider_snapshot=runtime_provider_snapshot,
            timer=timer,
        )
        permission = PermissionDecision(
            allowed=False,
            reason="missing_action_company_id",
            metadata={
                "policy_layer": "v5",
                "tenant_boundary": {
                    "company_id_required": True,
                    "violation": "missing_runtime_action_input_company_id",
                },
            },
        )
        answer = "缺少公司上下文，无法执行该操作。"
        session_context = dict(context.session_context)
        session_context.pop(RUNTIME_ACTION_INPUT_KEY, None)
        session_context.pop(LEGACY_ACTION_REQUEST_KEY, None)
        session_context["runtime_v5_last_action_input"] = runtime_action_input_payload(action_input)
        context = replace(context, session_context=session_context)
        save_session_context(context.chat_id, session_context)
        result_context = _result_context_with_scope(
            context,
            _runtime_action_input_contract_error_context(
                intent=intent,
                plan=plan,
                action_input=action_input,
                answer=answer,
                reason="missing_action_company_id",
            ),
        )
        _save_runtime_result_context(context, result_context)
        composed = ComposedAnswer(answer=answer, result_context=result_context)
        composed = _with_pipeline_timing(composed, timer)
        composed = _with_runtime_result_metadata(
            command_plan=command_plan,
            permission=permission,
            execution=None,
            composed=composed,
        )
        return AnswerEnvelope(
            context=context,
            intent=intent,
            plan=plan,
            permission=permission,
            execution=None,
            composed=composed,
        )
    context = _context_from_action_request(context)
    waiting_input_action = waiting_input_action_from_runtime_state(context.session_context)
    if waiting_input_action is not None:
        if _is_cancel_message(context.current_message):
            state = mark_runtime_action_cancelled(chat_id=context.chat_id, session_context=context.session_context)
            command_plan, intent, plan, permission = _build_command_and_policy(
                context=context,
                runtime_provider_snapshot=runtime_provider_snapshot,
                timer=timer,
            )
            answer = "已取消上一条待补充信息的操作。"
            receipt_context = _pending_action_receipt_context(
                pending_action=waiting_input_action,
                status="cancelled",
                answer=answer,
            )
            receipt_context = _with_runtime_state_metadata(receipt_context, state)
            _save_runtime_result_context(context, receipt_context)
            return AnswerEnvelope(
                context=context,
                intent=intent,
                plan=plan,
                permission=permission,
                execution=None,
                composed=_with_pipeline_timing(ComposedAnswer(answer=answer, result_context=receipt_context), timer),
            )
        if context.session_context.get(_WAITING_INPUT_STARTED_KEY):
            session_context = dict(context.session_context)
            session_context.pop(_WAITING_INPUT_STARTED_KEY, None)
            save_session_context(context.chat_id, session_context)
            context = replace(context, session_context=session_context)
            command_plan, intent, plan, permission = _build_command_and_policy(
                context=context,
                runtime_provider_snapshot=runtime_provider_snapshot,
                timer=timer,
            )
            state = runtime_state_from_session(context.session_context)
            result_context = _waiting_input_result_context(pending_action=waiting_input_action, state=state)
            _save_runtime_result_context(context, result_context)
            composed = ComposedAnswer(
                answer=result_context.answer,
                result_context=result_context,
                metadata={
                    "waiting_input": True,
                    "strategy": plan.strategy,
                    "sources": list(plan.sources),
                    "context_kind": "waiting_input",
                },
            )
            composed = _with_pipeline_timing(composed, timer)
            composed = _with_runtime_result_metadata(
                command_plan=command_plan,
                permission=permission,
                execution=None,
                composed=composed,
            )
            return AnswerEnvelope(
                context=context,
                intent=intent,
                plan=plan,
                permission=permission,
                execution=None,
                composed=composed,
            )
        if waiting_input_still_missing(waiting_input_action, context.current_message):
            command_plan, intent, plan, permission = _build_command_and_policy(
                context=context,
                runtime_provider_snapshot=runtime_provider_snapshot,
                timer=timer,
            )
            state = runtime_state_from_session(context.session_context)
            result_context = _waiting_input_result_context(pending_action=waiting_input_action, state=state)
            _save_runtime_result_context(context, result_context)
            composed = ComposedAnswer(
                answer=result_context.answer,
                result_context=result_context,
                metadata={
                    "waiting_input": True,
                    "strategy": plan.strategy,
                    "sources": list(plan.sources),
                    "context_kind": "waiting_input",
                },
            )
            composed = _with_pipeline_timing(composed, timer)
            composed = _with_runtime_result_metadata(
                command_plan=command_plan,
                permission=permission,
                execution=None,
                composed=composed,
            )
            return AnswerEnvelope(
                context=context,
                intent=intent,
                plan=plan,
                permission=permission,
                execution=None,
                composed=composed,
            )
        filled_action = pending_action_with_user_input(waiting_input_action, context.current_message)
        state = mark_runtime_action_waiting_confirmation(
            chat_id=context.chat_id,
            session_context=context.session_context,
            pending_action=filled_action,
        )
        session_context = _session_without_pending_action(context.session_context)
        session_context[_PENDING_ACTION_KEY] = filled_action
        if state is not None:
            session_context["runtime_v5_state"] = runtime_state_payload(state)
        context = replace(
            context,
            current_message=str(filled_action.get("message") or context.current_message),
            session_context=session_context,
        )
        command_plan, intent, plan, permission = _build_command_and_policy(
            context=context,
            runtime_provider_snapshot=runtime_provider_snapshot,
            timer=timer,
        )
        pending_context = _pending_confirmation_result_context(
            context=context,
            intent=intent,
            plan=plan,
            permission=permission,
            pending_action_id=str(filled_action.get("id") or ""),
        )
        pending_context = _with_runtime_state_metadata(pending_context, state)
        _save_runtime_result_context(context, pending_context)
        composed = ComposedAnswer(
            answer=_confirmation_text(intent=intent, plan=plan, permission=permission, pending_action_id=str(filled_action.get("id") or "")),
            result_context=pending_context,
            metadata={
                "requires_confirmation": True,
                "strategy": plan.strategy,
                "sources": list(plan.sources),
                "pending_action_id": str(filled_action.get("id") or ""),
                "context_kind": "pending_confirmation",
            },
        )
        composed = _with_pipeline_timing(composed, timer)
        composed = _with_runtime_result_metadata(
            command_plan=command_plan,
            permission=permission,
            execution=None,
            composed=composed,
        )
        return AnswerEnvelope(
            context=context,
            intent=intent,
            plan=plan,
            permission=permission,
            execution=None,
            composed=composed,
        )
    pending_action = _pending_action(context.session_context)
    if not pending_action and _is_standalone_confirmation_message(context.current_message):
        command_plan, intent, plan, permission = _build_command_and_policy(
            context=context,
            runtime_provider_snapshot=runtime_provider_snapshot,
            timer=timer,
        )
        answer = "当前没有待确认的操作。请先发起需要执行的任务。"
        receipt_context = _runtime_noop_receipt_context(
            status="skipped",
            answer=answer,
            operation="confirmation_without_pending_action",
        )
        _save_runtime_result_context(context, receipt_context)
        return AnswerEnvelope(
            context=context,
            intent=intent,
            plan=plan,
            permission=permission,
            execution=None,
            composed=_with_pipeline_timing(
                ComposedAnswer(answer=answer, result_context=receipt_context),
                timer,
            ),
        )

    if pending_action and _is_cancel_message(context.current_message):
        state = mark_runtime_action_cancelled(chat_id=context.chat_id, session_context=context.session_context)
        command_plan, intent, plan, permission = _build_command_and_policy(
            context=context,
            runtime_provider_snapshot=runtime_provider_snapshot,
            timer=timer,
        )
        answer = "已取消上一条待确认操作。"
        receipt_context = _pending_action_receipt_context(
            pending_action=pending_action,
            status="cancelled",
            answer=answer,
        )
        receipt_context = _with_runtime_state_metadata(receipt_context, state)
        _save_runtime_result_context(context, receipt_context)
        return AnswerEnvelope(
            context=context,
            intent=intent,
            plan=plan,
            permission=permission,
            execution=None,
            composed=_with_pipeline_timing(ComposedAnswer(answer=answer, result_context=receipt_context), timer),
        )

    confirmation_granted = False
    if pending_action and _is_confirmation_message(context.current_message):
        if _pending_action_expired(pending_action):
            state = mark_runtime_action_stale(chat_id=context.chat_id, session_context=context.session_context)
            intent = recognize_intent(context.current_message, context)
            timer.mark("intent_recognition")
            plan = plan_task(intent, runtime_provider_snapshot=runtime_provider_snapshot)
            timer.mark("task_planner")
            permission = check_runtime_permission(context=context, intent=intent, plan=plan)
            timer.mark("permission_check")
            answer = "这条待确认操作已经过期，我没有继续执行。请重新发起需要执行的任务。"
            receipt_context = _pending_action_receipt_context(
                pending_action=pending_action,
                status="stale",
                answer=answer,
            )
            receipt_context = _with_runtime_state_metadata(receipt_context, state)
            _save_runtime_result_context(context, receipt_context)
            return AnswerEnvelope(
                context=context,
                intent=intent,
                plan=plan,
                permission=permission,
                execution=None,
                composed=_with_pipeline_timing(ComposedAnswer(answer=answer, result_context=receipt_context), timer),
            )
        if _confirmation_execution_guard_reason(pending_action):
            guard_reason = _confirmation_execution_guard_reason(pending_action)
            state = mark_runtime_action_done(
                chat_id=context.chat_id,
                session_context=context.session_context,
                success=False,
                error=guard_reason,
            )
            command_plan, intent, plan, permission = _build_command_and_policy(
                context=context,
                runtime_provider_snapshot=runtime_provider_snapshot,
                timer=timer,
            )
            answer = _confirmation_guard_answer(guard_reason)
            receipt_context = _pending_action_receipt_context(
                pending_action=pending_action,
                status="failed",
                answer=answer,
            )
            receipt_context = _with_runtime_state_metadata(receipt_context, state)
            execution = ExecutionResult(
                strategy=str(pending_action.get("strategy") or plan.strategy),
                status="error",
                provider_results=(
                    ProviderResult(
                        source="runtime",
                        status="error",
                        result_type="runtime_action",
                        error=guard_reason,
                        answer=answer,
                    ),
                ),
                result_context=receipt_context,
            )
            composed = ComposedAnswer(
                answer=answer,
                result_context=receipt_context,
                metadata={"strategy": plan.strategy, "sources": list(plan.sources), "context_kind": "runtime_action"},
            )
            composed = _with_pipeline_timing(composed, timer)
            composed = _with_runtime_result_metadata(
                command_plan=command_plan,
                permission=permission,
                execution=execution,
                composed=composed,
            )
            return AnswerEnvelope(
                context=replace(context, session_context=_session_without_pending_action(context.session_context)),
                intent=intent,
                plan=plan,
                permission=permission,
                execution=execution,
                composed=composed,
            )
        restored_message = str(pending_action.get("message") or "").strip()
        if restored_message:
            state = mark_runtime_action_confirmed(chat_id=context.chat_id, session_context=context.session_context)
            session_context = _session_without_pending_action(context.session_context)
            session_context["runtime_v5_confirmed_action_entities"] = pending_action.get("entities") if isinstance(pending_action.get("entities"), dict) else {}
            if state is not None:
                session_context["runtime_v5_state"] = runtime_state_payload(state)
            context = replace(
                context,
                current_message=restored_message,
                session_context=session_context,
            )
            confirmation_granted = True

    clarification_reply = resolve_clarification_reply(context.current_message, context.result_context)
    if clarification_reply.is_reply:
        context = replace(
            context,
            current_message=clarification_reply.resolved_message,
            session_context={
                **context.session_context,
                "runtime_v5_last_clarification_reply": {
                    "original_message": context.current_message,
                    "resolved_message": clarification_reply.resolved_message,
                    "filled_params": clarification_reply.filled_params,
                },
            },
        )

    followup = detect_result_followup(context.current_message, context.result_context)
    timer.mark("result_followup_detector")
    if (
        _looks_like_new_question(context.current_message, context.result_context, followup)
        or _looks_like_result_action(context.current_message)
        or _looks_like_approval_detail_action(context.current_message, context.result_context)
        or _conversation_first_should_own_followup(context)
    ):
        followup = replace(followup, is_result_followup=False)
    if followup.is_result_followup:
        command_plan, intent, plan, permission = _build_command_and_policy(
            context=context,
            runtime_provider_snapshot=runtime_provider_snapshot,
            timer=timer,
        )
        composed = compose_answer(
            context=context,
            intent=intent,
            permission=permission,
            execution=None,
            followup=followup,
        )
        timer.mark("answer_composer")
        composed = _with_pipeline_timing(composed, timer)
        return AnswerEnvelope(
            context=context,
            intent=intent,
            plan=plan,
            permission=permission,
            execution=None,
            composed=composed,
        )

    command_plan = build_command_plan(
        context=context,
        runtime_provider_snapshot=runtime_provider_snapshot,
    )
    _record_command_route_observation(context=context, command_plan=command_plan)
    intent = command_plan.intent_result
    timer.mark("intent_recognition")
    plan = command_plan.planner_result
    timer.mark("task_planner")
    permission = evaluate_policy(context=context, command_plan=command_plan)
    timer.mark("permission_check")

    if _should_wait_for_action_input(context=context, intent=intent, permission=permission):
        pending_action_id, state = _save_missing_action_input(context=context, intent=intent, plan=plan, permission=permission)
        pending_action = _pending_action({**context.session_context, "runtime_v5_state": runtime_state_payload(state)})
        if pending_action is None:
            pending_action = {
                "id": pending_action_id,
                "intent": intent.intent,
                "strategy": plan.strategy,
                "message": context.current_message,
                "missing_params": list(intent.missing_params),
                "sources": list(plan.sources),
            }
        result_context = _waiting_input_result_context(pending_action=pending_action, state=state)
        _save_runtime_result_context(context, result_context)
        composed = ComposedAnswer(
            answer=result_context.answer,
            result_context=result_context,
            metadata={
                "waiting_input": True,
                "strategy": plan.strategy,
                "sources": list(plan.sources),
                "pending_action_id": pending_action_id,
                "context_kind": "waiting_input",
            },
        )
        timer.mark("answer_composer")
        composed = _with_pipeline_timing(composed, timer)
        composed = _with_runtime_result_metadata(
            command_plan=command_plan,
            permission=permission,
            execution=None,
            composed=composed,
        )
        return AnswerEnvelope(
            context=context,
            intent=intent,
            plan=plan,
            permission=permission,
            execution=None,
            composed=composed,
        )

    if _should_request_confirmation(
        context=context,
        intent=intent,
        permission=permission,
        confirmation_granted=confirmation_granted,
    ):
        pending_action_id, state = _save_pending_action(context=context, intent=intent, plan=plan, permission=permission)
        pending_context = _pending_confirmation_result_context(
            context=context,
            intent=intent,
            plan=plan,
            permission=permission,
            pending_action_id=pending_action_id,
        )
        pending_context = _with_runtime_state_metadata(pending_context, state)
        _save_runtime_result_context(context, pending_context)
        composed = ComposedAnswer(
            answer=_confirmation_text(intent=intent, plan=plan, permission=permission, pending_action_id=pending_action_id),
            result_context=pending_context,
            metadata={
                "requires_confirmation": True,
                "strategy": plan.strategy,
                "sources": list(plan.sources),
                "pending_action_id": pending_action_id,
                "context_kind": "pending_confirmation",
            },
        )
        timer.mark("answer_composer")
        composed = _with_pipeline_timing(composed, timer)
        composed = _with_runtime_result_metadata(
            command_plan=command_plan,
            permission=permission,
            execution=None,
            composed=composed,
        )
        return AnswerEnvelope(
            context=context,
            intent=intent,
            plan=plan,
            permission=permission,
            execution=None,
            composed=composed,
        )

    execution = None
    runtime_task = None
    runtime_state = None
    if not intent.needs_clarification and permission.allowed:
        if confirmation_granted:
            state = mark_runtime_action_executing(chat_id=context.chat_id, session_context=context.session_context)
            if state is not None:
                runtime_state = state
                context = replace(context, session_context={**context.session_context, "runtime_v5_state": runtime_state_payload(state)})
        runtime_task, execution = execute_runtime_task(
            context=context,
            command_plan=command_plan,
            permission=permission,
            providers=providers,
        )
        timer.mark("agent_runtime_core")
        if confirmation_granted and execution is not None:
            state = mark_runtime_action_done(
                chat_id=context.chat_id,
                session_context=context.session_context,
                success=execution.status == "success",
                error=_execution_error(execution),
            )
            if state is not None:
                runtime_state = state
                context = replace(context, session_context={**context.session_context, "runtime_v5_state": runtime_state_payload(state)})
        elif runtime_task is not None and execution is not None:
            runtime_state = save_runtime_task_execution_state(
                chat_id=context.chat_id,
                session_context=context.session_context,
                task_id=runtime_task.task_id,
                intent=command_plan.intent,
                strategy=plan.strategy,
                success=execution.status == "success",
                error=_execution_error(execution),
            )
            context = replace(context, session_context={**context.session_context, "runtime_v5_state": runtime_state_payload(runtime_state)})

    composed = compose_answer(
        context=context,
        intent=intent,
        permission=permission,
        execution=execution,
        followup=followup,
    )
    timer.mark("answer_composer")
    composed = _with_pipeline_timing(composed, timer)
    if runtime_task is not None:
        composed = replace(
            composed,
            metadata={
                **composed.metadata,
                "runtime_task": {
                    "task_id": runtime_task.task_id,
                    "status": runtime_task.status,
                    "strategy": runtime_task.strategy,
                    "step_count": len(runtime_task.steps),
                    "target_ui": runtime_task.metadata.get("target_ui"),
                    "context_scope": runtime_task.metadata.get("context_scope"),
                },
                "command_plan": {
                    "intent": command_plan.intent,
                    "target_ui": command_plan.target_ui,
                    "tool_candidates": list(command_plan.tool_candidates),
                    "context_scope": command_plan.context_scope,
                },
            },
        )
    if composed.result_context is not None and not (execution is not None and intent.question_type == "action"):
        if runtime_state is not None:
            composed = replace(composed, result_context=_with_runtime_state_metadata(composed.result_context, runtime_state))
        scoped_result_context = _result_context_with_scope(context, composed.result_context)
        composed = replace(composed, result_context=scoped_result_context)
        _save_runtime_result_context(context, scoped_result_context)
        _sync_current_approval_item(context, scoped_result_context)
    elif intent.needs_clarification:
        clarification_context = _clarification_result_context(
            context=context,
            intent=intent,
            plan=plan,
            answer=composed.answer,
        )
        composed = ComposedAnswer(
            answer=composed.answer,
            result_context=clarification_context,
            metadata=dict(composed.metadata),
        )
        _save_runtime_result_context(context, clarification_context)
    elif not permission.allowed:
        denied_context = _permission_denied_result_context(
            intent=intent,
            plan=plan,
            permission=permission,
            answer=composed.answer,
        )
        composed = ComposedAnswer(
            answer=composed.answer,
            result_context=denied_context,
            metadata=dict(composed.metadata),
        )
        _save_runtime_result_context(context, denied_context)
    elif execution is not None and intent.question_type == "action":
        receipt_context = _runtime_action_receipt_context(
            context=context,
            intent=intent,
            plan=plan,
            execution=execution,
            answer=composed.answer,
        )
        receipt_context = _with_runtime_state_metadata(receipt_context, runtime_state_from_session(context.session_context))
        composed = ComposedAnswer(
            answer=composed.answer,
            result_context=receipt_context,
            metadata=dict(composed.metadata),
        )
        _save_runtime_result_context(context, receipt_context)
    elif execution is not None and intent.question_type == "query":
        clear_result_context(context.chat_id, reason="query_without_result_context", source="runtime_v5")
    if runtime_task is not None:
        composed = _with_runtime_result_metadata(
            command_plan=command_plan,
            permission=permission,
            execution=execution,
            composed=composed,
        )
    return AnswerEnvelope(
        context=context,
        intent=intent,
        plan=plan,
        permission=permission,
        execution=execution,
        composed=composed,
    )


def _with_runtime_result_metadata(
    *,
    command_plan,
    permission,
    execution,
    composed: ComposedAnswer,
) -> ComposedAnswer:
    runtime_result = build_runtime_result(
        command_plan=command_plan,
        permission=permission,
        execution=execution,
        composed=composed,
    )
    return replace(
        composed,
        metadata={
            **composed.metadata,
            "runtime_result": runtime_result_payload(runtime_result),
        },
    )


def _execution_error(execution) -> str:
    if execution is None:
        return ""
    for result in getattr(execution, "provider_results", ()) or ():
        error = str(getattr(result, "error", "") or "").strip()
        if error:
            return error
    return "" if getattr(execution, "status", "") == "success" else str(getattr(execution, "status", "") or "")


def _build_command_and_policy(
    *,
    context: RuntimeContext,
    runtime_provider_snapshot: dict[str, Any] | None,
    timer: "_RuntimeTimer",
):
    command_plan = build_command_plan(
        context=context,
        runtime_provider_snapshot=runtime_provider_snapshot,
    )
    intent = command_plan.intent_result
    timer.mark("intent_recognition")
    plan = command_plan.planner_result
    timer.mark("task_planner")
    permission = evaluate_policy(context=context, command_plan=command_plan)
    timer.mark("permission_check")
    return command_plan, intent, plan, permission


def _conversation_first_should_own_followup(context: RuntimeContext) -> bool:
    result_context = context.result_context
    if result_context is None:
        return False
    result_type = str(result_context.result_type or "")
    return result_type in {"people_search", "department_members", "organization_snapshot", "company_profile_knowledge", "knowledge_search", "docs_read"}


def _record_command_route_observation(*, context: RuntimeContext, command_plan) -> None:
    observation = _command_route_observation(command_plan)
    if not observation:
        return
    record_route_observation_trace(
        context.chat_id,
        {
            "question": context.current_message,
            "intent": command_plan.intent,
            "question_type": command_plan.intent_result.question_type,
            "data_scope": command_plan.intent_result.data_scope,
            "strategy": command_plan.planner_result.strategy,
            "sources": list(command_plan.planner_result.sources),
            "route_observation": observation,
        },
    )


def _command_route_observation(command_plan) -> dict[str, Any]:
    intent_result = getattr(command_plan, "intent_result", None)
    entities = getattr(intent_result, "entities", {}) if intent_result is not None else {}
    if isinstance(entities, dict):
        trace = entities.get("command_intent_trace") if isinstance(entities.get("command_intent_trace"), dict) else {}
        observation = trace.get("route_observation") if isinstance(trace, dict) else None
        if isinstance(observation, dict):
            return observation
    frame = getattr(command_plan, "command_frame", None)
    if frame is None:
        return {}
    rule_candidate = getattr(frame, "rule_candidate", {}) or {}
    if not isinstance(rule_candidate, dict):
        rule_candidate = {}
    observation = rule_candidate.get("route_observation")
    if isinstance(observation, dict):
        return observation
    return {}


class _RuntimeTimer:
    def __init__(self) -> None:
        self._start = perf_counter()
        self._last = self._start
        self._items: list[dict[str, Any]] = []

    def mark(self, stage: str) -> None:
        now = perf_counter()
        self._items.append(
            {
                "stage": stage,
                "duration_ms": int((now - self._last) * 1000),
                "elapsed_ms": int((now - self._start) * 1000),
            }
        )
        self._last = now

    def payload(self) -> dict[str, Any]:
        total_ms = int((perf_counter() - self._start) * 1000)
        slowest = max(self._items, key=lambda item: int(item.get("duration_ms") or 0), default={})
        return {
            "total_ms": total_ms,
            "stages": list(self._items),
            "slowest_stage": slowest.get("stage", ""),
            "slowest_ms": slowest.get("duration_ms", 0),
        }


def _with_pipeline_timing(composed: ComposedAnswer, timer: _RuntimeTimer) -> ComposedAnswer:
    return replace(composed, metadata={**composed.metadata, "pipeline_timing": timer.payload()})


def _save_runtime_result_context(context: RuntimeContext, result_context: ResultContext | None) -> None:
    if result_context is None:
        return
    save_result_context(context.chat_id, _result_context_with_scope(context, result_context))


def _with_runtime_state_metadata(result_context: ResultContext, state) -> ResultContext:
    if state is None:
        return result_context
    return replace(
        result_context,
        metadata={
            **result_context.metadata,
            "runtime_state": runtime_state_payload(state),
        },
    )


def _result_context_with_scope(context: RuntimeContext, result_context: ResultContext) -> ResultContext:
    company_id = str(context.runtime_scope.active_company_id or "")
    company_ids = [str(item) for item in context.runtime_scope.company_ids]
    scope_context = _scope_context_from_runtime(context=context, result_context=result_context, company_id=company_id)
    return replace(
        result_context,
        metadata={
            **result_context.metadata,
            "company_id": company_id,
            "company_ids": company_ids,
            "scope_type": context.runtime_scope.scope_type,
            "scope_context": scope_context,
            "tenant_isolated": True,
        },
        items=tuple(
            {
                **item,
                "company_id": str(item.get("company_id") or company_id),
                "scope_type": str(item.get("scope_type") or context.runtime_scope.scope_type),
            }
            for item in result_context.items
        ),
    )


def _scope_context_from_runtime(*, context: RuntimeContext, result_context: ResultContext, company_id: str) -> dict[str, Any]:
    existing = result_context.metadata.get("scope_context")
    if isinstance(existing, dict) and existing.get("scope"):
        return {**existing, "company_id": str(existing.get("company_id") or company_id)}
    data_scope = str(result_context.metadata.get("data_scope") or "")
    return {
        "scope": _enterprise_scope(data_scope),
        "company_id": company_id,
        "department_id": context.runtime_scope.active_department_id,
        "object_type": "project" if context.runtime_scope.active_project_id else "",
        "object_id": context.runtime_scope.active_project_id,
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


def _sync_current_approval_item(context: RuntimeContext, result_context) -> None:
    if not context.chat_id:
        return
    session_context = dict(context.session_context)
    if result_context.result_type == "approval_detail" and result_context.items:
        session_context["runtime_v5_current_approval_item"] = {"index": 0, "item": result_context.items[0]}
    else:
        session_context.pop("runtime_v5_current_approval_item", None)
    save_session_context(context.chat_id, session_context)


def _should_request_confirmation(
    *,
    context: RuntimeContext,
    intent,
    permission,
    confirmation_granted: bool,
) -> bool:
    return bool(
        context.chat_id
        and not intent.needs_clarification
        and permission.allowed
        and permission.requires_confirmation
        and not confirmation_granted
    )


def _should_wait_for_action_input(*, context: RuntimeContext, intent, permission) -> bool:
    return bool(
        context.chat_id
        and intent.question_type == "action"
        and intent.needs_clarification
        and intent.missing_params
        and permission.allowed
        and any(param in {"text", "target_type", "delivery_mode"} for param in intent.missing_params)
    )


def _looks_like_new_question(message: str, result_context=None, followup=None) -> bool:
    return should_start_new_question_over_result_context(message, result_context, followup=followup)

def _pending_action(session_context: dict[str, Any]) -> dict[str, Any] | None:
    state_pending = pending_action_from_runtime_state(session_context)
    if state_pending is not None:
        return state_pending
    pending = session_context.get(_PENDING_ACTION_KEY)
    return pending if isinstance(pending, dict) else None


def _confirmation_execution_guard_reason(pending_action: dict[str, Any]) -> str:
    strategy = str(pending_action.get("strategy") or pending_action.get("intent") or "").strip()
    if strategy in {"approval_transfer", "approval_add_sign"}:
        return "guarded_pending_user_resolution"
    entities = pending_action.get("entities") if isinstance(pending_action.get("entities"), dict) else {}
    if strategy == "message_send" and entities.get("target_type") == "people_context":
        delivery_mode = _normalized_delivery_mode(entities.get("delivery_mode"))
        if delivery_mode == "bot_multi_notify":
            return "guarded_people_context_bot_multi_notify"
        if delivery_mode == "user_multi_private":
            return "guarded_people_context_user_multi_private"
        if delivery_mode == "create_group_then_send":
            return ""
        return "guarded_people_context_batch_send"
    return ""


def _confirmation_guard_answer(reason: str) -> str:
    if reason == "guarded_people_context_bot_multi_notify":
        return "已确认用机器人通知这些人的意图，但 Bot 多人通知执行器还没有开放。我不会自动群发；等批量通知执行器接入后再执行。"
    if reason == "guarded_people_context_user_multi_private":
        return "已确认以本人身份分别发送的意图，但本人代发多人私信执行器还没有开放。我不会自动逐个私发；等用户授权和批量私信执行器接入后再执行。"
    if reason == "guarded_people_context_create_group_then_send":
        return "已确认拉群后发送的意图，但自动建群并发送执行器还没有开放。我不会自动建群；等群聊创建和群内发送执行器接入后再执行。"
    if reason == "guarded_people_context_batch_send":
        return "已确认发送意图，但多人目标发送执行器还没有开放。我不会自动逐个私发或用机器人群发；请改为发到一个明确群聊，或等批量发送执行器接入后再执行。"
    return "该操作目前只支持补齐参数并等待确认，暂不执行。"


def _context_from_action_request(context: RuntimeContext) -> RuntimeContext:
    action_input = runtime_action_input_from_session(context.session_context)
    if action_input is None:
        return context

    message = str(action_input.message or context.current_message or "").strip()
    if not message:
        return context

    session_context = dict(context.session_context)
    session_context.pop(RUNTIME_ACTION_INPUT_KEY, None)
    session_context.pop(LEGACY_ACTION_REQUEST_KEY, None)
    session_context["runtime_v5_last_action_input"] = runtime_action_input_payload(action_input)
    missing_params = action_input_missing_params(action_input)
    if missing_params and not action_input.confirmation.confirmed:
        pending_action = runtime_pending_action_payload(
            runtime_pending_action_with_missing_input(
                pending_action_from_runtime_action_input(action_input, message=message),
                missing_params=missing_params,
            )
        )
        session_context[_PENDING_ACTION_KEY] = pending_action
        state = save_waiting_input_state(
            chat_id=context.chat_id,
            session_context=session_context,
            pending_action=pending_action,
            ttl_seconds=_PENDING_ACTION_TTL_SECONDS,
        )
        session_context["runtime_v5_state"] = runtime_state_payload(state)
        session_context[_WAITING_INPUT_STARTED_KEY] = str(pending_action.get("id") or "")
        return replace(context, current_message=message, session_context=session_context)
    if action_input.action_type == "cancel":
        pending_action = _pending_action_from_action_input(action_input, message=message)
        session_context[_PENDING_ACTION_KEY] = pending_action
        state = save_waiting_confirmation_state(
            chat_id=context.chat_id,
            session_context=session_context,
            pending_action=pending_action,
            ttl_seconds=_PENDING_ACTION_TTL_SECONDS,
        )
        session_context["runtime_v5_state"] = runtime_state_payload(state)
        return replace(context, current_message="取消", session_context=session_context)
    if action_input.confirmation.confirmed:
        pending_action = _pending_action_from_action_input(action_input, message=message)
        session_context[_PENDING_ACTION_KEY] = pending_action
        session_context["runtime_v5_confirmed_action_entities"] = pending_action.get("entities") if isinstance(pending_action.get("entities"), dict) else {}
        state = save_waiting_confirmation_state(
            chat_id=context.chat_id,
            session_context=session_context,
            pending_action=pending_action,
            ttl_seconds=_PENDING_ACTION_TTL_SECONDS,
        )
        confirmed_state = mark_runtime_action_confirmed(
            chat_id=context.chat_id,
            session_context={
                **session_context,
                "runtime_v5_state": runtime_state_payload(state),
            },
        )
        if confirmed_state is not None:
            session_context["runtime_v5_state"] = runtime_state_payload(confirmed_state)
        return replace(context, current_message="确认执行", session_context=session_context)
    pending_action = _pending_action_from_action_input(action_input, message=message)
    session_context[_PENDING_ACTION_KEY] = pending_action
    state = save_waiting_confirmation_state(
        chat_id=context.chat_id,
        session_context=session_context,
        pending_action=pending_action,
        ttl_seconds=_PENDING_ACTION_TTL_SECONDS,
    )
    session_context["runtime_v5_state"] = runtime_state_payload(state)
    return replace(context, current_message=message, session_context=session_context)


def _pending_action_from_action_input(action_input, *, message: str) -> dict[str, Any]:
    return runtime_pending_action_payload(pending_action_from_runtime_action_input(action_input, message=message))


def _clarification_result_context(*, context: RuntimeContext, intent, plan, answer: str) -> ResultContext:
    guide = build_clarification_guide(context=context, intent=intent, fallback=answer)
    missing_params = list(guide.missing_params)
    confidence = float(getattr(intent, "confidence", 0.0) or 0.0)
    clarification_options = guide.option_payloads()
    return ResultContext(
        result_type=f"{plan.strategy}_clarification",
        query_id=f"{plan.strategy}:clarification:{uuid4().hex[:12]}",
        count=1,
        items=(
            {
                "source": "runtime",
                "operation": plan.strategy,
                "status": "clarification",
                "title": "需要补充信息",
                "reason": guide.reason,
                "missing_params": missing_params,
                "confidence": confidence,
                "clarification_prompt": guide.prompt,
                "clarification_options": clarification_options,
                "summary": guide.prompt or guide.next_step,
            },
        ),
        metadata={
            "context_kind": "no_result",
            "question_type": intent.question_type,
            "data_scope": intent.data_scope,
            "actionable": False,
            "execution_status": "clarification",
            "empty_result": True,
            "empty_reason": guide.reason,
            "recommended_next_step": guide.next_step,
            "source": "runtime",
            "operation": plan.strategy,
            "result_sources": list(plan.sources),
            "missing_params": missing_params,
            "clarification_prompt": guide.prompt,
            "clarification_options": clarification_options,
            "confidence": confidence,
            "item_count": 1,
            "display_count": 1,
        },
        answer=guide.prompt or guide.next_step,
    )

def _permission_denied_result_context(*, intent, plan, permission, answer: str) -> ResultContext:
    is_action = intent.question_type == "action"
    result_type = "runtime_action" if is_action else f"{plan.strategy}_denied"
    context_kind = "action_receipt" if is_action else "no_result"
    reason = str(permission.reason or "permission_denied")
    title = label_for_strategy(plan.strategy)
    summary = answer or "这部分数据或操作当前没有权限访问。"
    return ResultContext(
        result_type=result_type,
        query_id=f"{plan.strategy}:{result_type}:{uuid4().hex[:12]}",
        count=1,
        items=(
            {
                "source": "runtime",
                "operation": plan.strategy,
                "status": "denied",
                "title": title,
                "reason": reason,
                "summary": summary,
            },
        ),
        metadata={
            "context_kind": context_kind,
            "question_type": intent.question_type,
            "data_scope": intent.data_scope,
            "actionable": False,
            "execution_status": "denied",
            "empty_result": not is_action,
            "empty_reason": reason,
            "recommended_next_step": "请确认身份权限、数据范围或联系管理员授权后再试。",
            "source": "runtime",
            "operation": plan.strategy,
            "result_sources": list(plan.sources),
            "permission_reason": reason,
            "item_count": 1,
            "display_count": 1,
        },
        answer=summary,
    )


def _runtime_action_input_contract_error_context(*, intent, plan, action_input, answer: str, reason: str) -> ResultContext:
    strategy = str(action_input.strategy or plan.strategy or intent.intent or "runtime_action").strip()
    title = label_for_strategy(strategy)
    sources = list(getattr(plan, "sources", ()) or ())
    return ResultContext(
        result_type="runtime_action",
        query_id=f"{strategy}:contract_error:{uuid4().hex[:12]}",
        count=1,
        items=(
            {
                "source": "runtime",
                "operation": strategy,
                "status": "failed",
                "title": title,
                "reason": reason,
                "summary": answer,
            },
        ),
        metadata={
            "context_kind": "action_receipt",
            "question_type": "action",
            "data_scope": getattr(intent, "data_scope", "self"),
            "actionable": False,
            "execution_status": "failed",
            "empty_result": False,
            "empty_reason": reason,
            "error": reason,
            "recommended_next_step": "请从绑定公司上下文的入口重新发起操作。",
            "source": "runtime",
            "operation": strategy,
            "result_sources": sources,
            "runtime_action_input_contract": {
                "status": "failed",
                "company_id_required": True,
                "legacy_runtime_v5_action_request": bool(action_input.metadata.get("legacy_runtime_v5_action_request")),
                "violation": "legacy_action_request_not_allowed"
                if action_input.metadata.get("legacy_runtime_v5_action_request")
                else "missing_context_company_id",
            },
            "item_count": 1,
            "display_count": 1,
        },
        answer=answer,
    )


def _runtime_action_receipt_context(
    *,
    context: RuntimeContext,
    intent,
    plan,
    execution,
    answer: str,
) -> ResultContext:
    provider_items = []
    authorization_metadata: dict[str, Any] = {}
    receipt_result_type = _runtime_action_result_type(plan.strategy)
    for result in execution.provider_results:
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        if result.result_type == "waiting_authorization" or metadata.get("waiting_authorization"):
            receipt_result_type = "waiting_authorization"
            authorization_metadata = {
                "credential_mode": metadata.get("credential_mode", ""),
                "authorization_status": metadata.get("authorization_status", ""),
                "authorization_error": metadata.get("authorization_error", ""),
                "waiting_authorization": bool(metadata.get("waiting_authorization")),
                "provider_boundary": metadata.get("provider_boundary", ""),
                "execution_identity_contract": metadata.get("execution_identity_contract", {}),
            }
        operation = str(metadata.get("operation") or result.result_type or plan.strategy or intent.intent).strip()
        summary = str(result.answer or result.error or answer or label_for_strategy(plan.strategy) or operation).strip()
        status_group = _runtime_action_status_group(result.status)
        provider_items.append(
            {
                "source": result.source,
                "operation": operation,
                "status": result.status,
                "status_group": status_group,
                "is_terminal": status_group == "terminal",
                "is_pending": status_group == "pending",
                "title": label_for_strategy(plan.strategy),
                "summary": summary[:240],
                "error": result.error,
                "count": result.count,
                **_runtime_action_receipt_metadata(metadata),
            }
        )
    if not provider_items:
        status_group = _runtime_action_status_group(execution.status)
        provider_items.append(
            {
                "source": "runtime",
                "operation": plan.strategy,
                "status": execution.status,
                "status_group": status_group,
                "is_terminal": status_group == "terminal",
                "is_pending": status_group == "pending",
                "title": label_for_strategy(plan.strategy),
                "summary": str(answer or label_for_strategy(plan.strategy) or plan.strategy)[:240],
            }
        )
    action_status_group = _runtime_action_status_group(execution.status)
    return ResultContext(
        result_type=receipt_result_type,
        query_id=f"{plan.strategy}:runtime_action:{uuid4().hex[:12]}",
        count=len(provider_items),
        items=tuple(provider_items),
        metadata={
            "context_kind": "action_receipt",
            "question_type": "action",
            "data_scope": intent.data_scope,
            "actionable": False,
            "execution_status": execution.status,
            "source": "runtime",
            "operation": plan.strategy,
            "result_sources": list(plan.sources),
            "action_status_group": action_status_group,
            "is_terminal_action": action_status_group == "terminal",
            "is_pending_action": action_status_group == "pending",
            "consume_policy": {
                "prefer_items": True,
                "allow_answer_fallback": False,
                "requires_refresh_when_expired": True,
                "supports_index_followup": bool(provider_items),
                "supports_detail_followup": bool(provider_items),
            },
            "followup_fields": ["source", "operation", "title", "status", "summary", "error"],
            "item_identity_fields": ["source", "operation"],
            "item_count": len(provider_items),
            "display_count": len(provider_items),
            **authorization_metadata,
        },
        answer=answer or f"{label_for_strategy(plan.strategy)}已处理。",
    )


def _runtime_action_result_type(strategy: str) -> str:
    normalized = str(strategy or "").strip()
    if normalized in {"task_complete"}:
        return normalized
    return "runtime_action"


def _runtime_action_receipt_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "target",
        "target_type",
        "target_query",
        "resolved_user_id",
        "resolved_chat_id",
        "resolved_target_name",
        "error_type",
        "provider_boundary",
        "recommended_next_step",
    )
    return {field: metadata.get(field) for field in fields if metadata.get(field) not in (None, "", [], {})}


def _pending_action_receipt_context(*, pending_action: dict[str, Any], status: str, answer: str) -> ResultContext:
    strategy = str(pending_action.get("strategy") or pending_action.get("intent") or "runtime_action").strip()
    label = str(pending_action.get("intent_label") or label_for_strategy(strategy) or strategy).strip()
    message = str(pending_action.get("message") or "").strip()
    sources = pending_action.get("sources") if isinstance(pending_action.get("sources"), list) else []
    action_id = str(pending_action.get("id") or "").strip()
    status_label = {
        "cancelled": "已取消",
        "stale": "确认已失效",
        "error": "执行失败",
        "success": "已完成",
    }.get(status, status or "已处理")
    entities = pending_action.get("entities") if isinstance(pending_action.get("entities"), dict) else {}
    people_targets = entities.get("people_targets") if isinstance(entities.get("people_targets"), list) else []
    delivery_mode = _normalized_delivery_mode(entities.get("delivery_mode"))
    target_type = str(entities.get("target_type") or "").strip()
    people_target_count = _safe_int(entities.get("people_target_count"), fallback=len(people_targets))
    summary = "｜".join(part for part in (label, status_label, message[:120]) if part)
    status_group = _runtime_action_status_group(status)
    return ResultContext(
        result_type="runtime_action",
        query_id=f"{strategy}:runtime_action:{uuid4().hex[:12]}",
        count=1,
        items=(
            {
                "source": "runtime",
                "operation": strategy,
                "status": status,
                "status_group": status_group,
                "is_terminal": status_group == "terminal",
                "is_pending": status_group == "pending",
                "action_id": action_id,
                "correlation_id": action_id,
                "title": label,
                "message": message,
                "summary": summary,
                "target_type": target_type,
                "delivery_mode": delivery_mode,
                "people_target_count": people_target_count,
            },
        ),
        metadata={
            "context_kind": "action_receipt",
            "question_type": "action",
            "data_scope": str(pending_action.get("data_scope") or "self"),
            "actionable": False,
            "execution_status": status,
            "source": "runtime",
            "operation": strategy,
            "result_sources": sources,
            "action_id": action_id,
            "target_type": target_type,
            "delivery_mode": delivery_mode,
            "people_target_count": people_target_count,
            "action_status_group": status_group,
            "is_terminal_action": status_group == "terminal",
            "is_pending_action": status_group == "pending",
            "consume_policy": {
                "prefer_items": True,
                "allow_answer_fallback": False,
                "requires_refresh_when_expired": True,
                "supports_index_followup": True,
                "supports_detail_followup": True,
            },
            "followup_fields": ["source", "operation", "title", "message", "status", "summary", "action_id"],
            "item_identity_fields": ["action_id", "correlation_id", "source", "operation"],
            "item_count": 1,
            "display_count": 1,
        },
        answer=answer,
    )


def _waiting_input_result_context(*, pending_action: dict[str, Any], state) -> ResultContext:
    strategy = str(pending_action.get("strategy") or pending_action.get("intent") or "runtime_action").strip()
    label = str(pending_action.get("intent_label") or label_for_strategy(strategy) or strategy).strip()
    action_id = str(pending_action.get("id") or "").strip()
    missing_params = [str(item) for item in pending_action.get("missing_params", []) if str(item)]
    answer = _waiting_input_prompt(missing_params)
    context = ResultContext(
        result_type="runtime_waiting_input",
        query_id=f"{strategy}:waiting_input:{action_id or uuid4().hex[:12]}",
        count=1,
        items=(
            {
                "source": "runtime",
                "operation": strategy,
                "status": "waiting_input",
                "status_group": "pending",
                "is_terminal": False,
                "is_pending": True,
                "action_id": action_id,
                "correlation_id": action_id,
                "title": label,
                "missing_params": missing_params,
                "summary": answer,
            },
        ),
        metadata={
            "context_kind": "waiting_input",
            "question_type": "action",
            "data_scope": str(pending_action.get("data_scope") or "self"),
            "actionable": True,
            "execution_status": "waiting_input",
            "source": "runtime",
            "operation": strategy,
            "result_sources": pending_action.get("sources") if isinstance(pending_action.get("sources"), list) else [],
            "action_id": action_id,
            "missing_params": missing_params,
            "input_contract": {
                "status": "waiting_input",
                "missing_params": missing_params,
                "next_state": "waiting_confirmation",
            },
            "consume_policy": {
                "prefer_items": True,
                "allow_answer_fallback": False,
                "requires_refresh_when_expired": True,
                "supports_index_followup": False,
                "supports_detail_followup": False,
            },
            "item_count": 1,
            "display_count": 1,
        },
        answer=answer,
    )
    return _with_runtime_state_metadata(context, state)


def _waiting_input_prompt(missing_params: list[str]) -> str:
    if "text" in missing_params:
        return "要发送什么内容？你可以直接回复消息正文。"
    if "target_type" in missing_params or "target" in missing_params:
        return "要发给谁？你可以直接回复人名、群名，或说「发到当前会话」。"
    if "delivery_mode" in missing_params:
        return "你想怎么发给这些人？可以说「用机器人通知这些人」「替我分别发给这些人」，或「拉群后发到群里」。"
    if "comment" in missing_params:
        return "请补充拒绝原因。你可以直接回复：原因：资料不完整。"
    if "target_user" in missing_params:
        return "请补充处理人。你可以直接回复：转交给张三。"
    return "请补充必要信息后继续。"


def _runtime_noop_receipt_context(*, status: str, answer: str, operation: str) -> ResultContext:
    status_group = _runtime_action_status_group(status)
    action_id = uuid4().hex[:12]
    return ResultContext(
        result_type="runtime_action",
        query_id=f"{operation}:runtime_action:{action_id}",
        count=1,
        items=(
            {
                "source": "runtime",
                "operation": operation,
                "status": status,
                "status_group": status_group,
                "is_terminal": status_group == "terminal",
                "is_pending": status_group == "pending",
                "action_id": action_id,
                "correlation_id": action_id,
                "title": "确认操作",
                "summary": answer,
            },
        ),
        metadata={
            "context_kind": "action_receipt",
            "question_type": "action",
            "data_scope": "self",
            "actionable": False,
            "execution_status": status,
            "source": "runtime",
            "operation": operation,
            "result_sources": ["runtime"],
            "action_id": action_id,
            "action_status_group": status_group,
            "is_terminal_action": status_group == "terminal",
            "is_pending_action": status_group == "pending",
            "consume_policy": {
                "prefer_items": True,
                "allow_answer_fallback": False,
                "requires_refresh_when_expired": True,
                "supports_index_followup": True,
                "supports_detail_followup": True,
            },
            "followup_fields": ["source", "operation", "title", "status", "summary", "action_id"],
            "item_identity_fields": ["action_id", "correlation_id", "source", "operation"],
            "item_count": 1,
            "display_count": 1,
        },
        answer=answer,
    )


def _runtime_action_status_group(status: str) -> str:
    value = str(status or "")
    if value in {"queued", "started", "processing", "pending", "confirmation_card_started", "confirmation_card_sent"}:
        return "pending"
    if value in {"success", "partial", "error", "failed", "cancelled", "stale", "stale_cleanup", "expired", "denied", "skipped", "confirmation_card_failed"}:
        return "terminal"
    return "unknown"


def _safe_int(value: Any, *, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _normalized_delivery_mode(value: Any) -> str:
    text = str(value or "").strip()
    compact = "".join(text.split()).lower()
    if compact in {"bot_multi_notify", "user_multi_private", "create_group_then_send"}:
        return compact
    if any(token in compact for token in ("机器人通知", "用机器人", "机器人发", "系统通知", "自动通知", "大飞哥通知")):
        return "bot_multi_notify"
    if any(token in compact for token in ("替我发", "用我", "以我的名义", "我发给", "分别发", "单独发", "单独发送", "私聊发")):
        return "user_multi_private"
    if any(token in compact for token in ("拉群", "建群", "建个群", "创建群", "群里发", "发到群")):
        return "create_group_then_send"
    return text


def _save_pending_action(*, context: RuntimeContext, intent, plan, permission) -> tuple[str, Any]:
    pending_action = _pending_action_payload(context=context, intent=intent, plan=plan, permission=permission)
    state = save_waiting_confirmation_state(
        chat_id=context.chat_id,
        session_context=context.session_context,
        pending_action=pending_action,
        ttl_seconds=_PENDING_ACTION_TTL_SECONDS,
    )
    return str(pending_action.get("id") or ""), state


def _save_missing_action_input(*, context: RuntimeContext, intent, plan, permission) -> tuple[str, Any]:
    pending_action = {
        **_pending_action_payload(context=context, intent=intent, plan=plan, permission=permission),
        "missing_params": list(intent.missing_params),
        "input_contract": {
            "status": "waiting_input",
            "missing_params": list(intent.missing_params),
        },
    }
    state = save_waiting_input_state(
        chat_id=context.chat_id,
        session_context=context.session_context,
        pending_action=pending_action,
        ttl_seconds=_PENDING_ACTION_TTL_SECONDS,
    )
    return str(pending_action.get("id") or ""), state


def _pending_action_payload(*, context: RuntimeContext, intent, plan, permission) -> dict[str, Any]:
    action_id = uuid4().hex[:12]
    task_id = uuid4().hex
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=_PENDING_ACTION_TTL_SECONDS)
    return {
            "id": action_id,
            "task_id": task_id,
            "created_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "ttl_seconds": _PENDING_ACTION_TTL_SECONDS,
            "message": context.current_message,
            "intent": intent.intent,
            "intent_label": label_for_strategy(intent.intent),
            "summary": _pending_action_summary(intent=intent, plan=plan),
            "route_path": route_path_for_strategy(plan.strategy),
            "question_type": intent.question_type,
            "data_scope": intent.data_scope,
            "entities": intent.entities,
            "strategy": plan.strategy,
            "sources": list(plan.sources),
            "source_execution_plan": [{"source": source, "strategy": plan.strategy} for source in plan.sources],
            "execution_identity": permission.execution_identity,
            "permission_reason": permission.reason,
            "requires_confirmation": permission.requires_confirmation,
            "write_confirmation_contract": _pending_action_write_confirmation_contract(plan=plan, permission=permission),
            "confirmation_reasons": (
                permission.metadata.get("confirmation_reasons", [])
                if hasattr(permission, "metadata") and isinstance(permission.metadata, dict)
                else []
            ),
    }


def _pending_action_summary(*, intent, plan) -> str:
    return "｜".join(
        part
        for part in (
            label_for_strategy(plan.strategy),
            _confirmation_impact_text(intent, plan).removeprefix("影响：").strip(),
            _people_targets_summary(intent),
        )
        if part
    )


def _pending_confirmation_result_context(*, context: RuntimeContext, intent, plan, permission, pending_action_id: str) -> ResultContext:
    summary = _pending_action_summary(intent=intent, plan=plan)
    label = label_for_strategy(plan.strategy) or plan.strategy
    item = {
        "source": "runtime",
        "operation": plan.strategy,
        "status": "pending_confirmation",
        "status_group": "prepared",
        "is_terminal": False,
        "is_pending": True,
        "action_id": pending_action_id,
        "correlation_id": pending_action_id,
        "confirmation_token": pending_action_id,
        "confirmation_token_type": "runtime_action_id",
        "title": label,
        "summary": summary,
        "message": context.current_message,
        "execution_identity": permission.execution_identity,
        "requires_confirmation": permission.requires_confirmation,
    }
    people_target_payload = _people_targets_payload(intent)
    if people_target_payload:
        item["people_targets"] = people_target_payload
        item["people_target_count"] = len(people_target_payload)
    return ResultContext(
        result_type="runtime_pending_confirmation",
        query_id=f"{plan.strategy}:pending_confirmation:{pending_action_id}",
        count=1,
        items=(item,),
        metadata={
            "context_kind": "pending_confirmation",
            "question_type": intent.question_type,
            "data_scope": intent.data_scope,
            "actionable": True,
            "execution_status": "pending_confirmation",
            "source": "runtime",
            "operation": plan.strategy,
            "result_sources": list(plan.sources),
            "action_id": pending_action_id,
            "confirmation_token": pending_action_id,
            "confirmation_token_type": "runtime_action_id",
            "confirmation_token_available": True,
            "action_status_group": "prepared",
            "is_terminal_action": False,
            "is_pending_action": True,
            "requires_confirmation": permission.requires_confirmation,
            "people_targets": people_target_payload,
            "people_target_count": len(people_target_payload),
            "execution_identity": permission.execution_identity,
            "confirmation_reasons": (
                permission.metadata.get("confirmation_reasons", [])
                if hasattr(permission, "metadata") and isinstance(permission.metadata, dict)
                else []
            ),
            "write_confirmation_contract": _pending_action_write_confirmation_contract(plan=plan, permission=permission),
            "consume_policy": {
                "prefer_items": True,
                "allow_answer_fallback": False,
                "requires_refresh_when_expired": True,
                "supports_index_followup": True,
                "supports_detail_followup": True,
            },
            "followup_fields": ["source", "operation", "title", "status", "summary", "action_id", "confirmation_token", "execution_identity"],
            "item_identity_fields": ["action_id", "correlation_id", "confirmation_token", "operation"],
            "item_count": 1,
            "display_count": 1,
        },
        answer=f"{label}等待确认。",
    )


def _pending_action_write_confirmation_contract(*, plan, permission) -> dict:
    requires_confirmation = bool(getattr(permission, "requires_confirmation", False))
    execution_identity = str(getattr(permission, "execution_identity", "") or "")
    is_write_action = requires_confirmation or execution_identity == "user"
    return {
        "status": "ready" if (not is_write_action or (requires_confirmation and execution_identity == "user")) else "incomplete",
        "contract": "dry_run_then_confirmation_token" if is_write_action else "readonly",
        "requires_dry_run": bool(is_write_action),
        "requires_confirmation_token": bool(is_write_action),
        "requires_confirmation": requires_confirmation,
        "execution_identity": execution_identity,
        "strategy": getattr(plan, "strategy", ""),
        "sources": list(getattr(plan, "sources", ()) or ()),
    }


def _clear_pending_action(context: RuntimeContext) -> None:
    clear_runtime_state(chat_id=context.chat_id, session_context=context.session_context)


def _pending_action_expired(pending_action: dict[str, Any]) -> bool:
    expires_at = str(pending_action.get("expires_at") or "").strip()
    if not expires_at:
        return False
    try:
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed <= datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return False


def _session_without_pending_action(session_context: dict[str, Any]) -> dict[str, Any]:
    payload = dict(session_context)
    payload.pop(_PENDING_ACTION_KEY, None)
    return payload


def _is_confirmation_message(message: str) -> bool:
    text = message.strip().lower()
    return bool(text) and any(term in text for term in _CONFIRM_TERMS)


def _is_standalone_confirmation_message(message: str) -> bool:
    text = message.strip().lower().strip("。.!！ ")
    return text in _CONFIRM_TERMS


def _is_cancel_message(message: str) -> bool:
    text = message.strip().lower()
    return bool(text) and any(term in text for term in _CANCEL_TERMS)


def _looks_like_result_action(message: str) -> bool:
    return any(
        term in message
        for term in (
            "完成",
            "标记完成",
            "设为完成",
            "关闭任务",
            "删除",
            "更新",
            "通过",
            "同意",
            "拒绝",
            "驳回",
            "审批详情",
            "发给",
            "发消息",
            "发邮件",
            "发送",
            "转发",
            "创建",
            "新建",
            "安排",
            "开会",
            "写封邮件",
            "写一封邮件",
            "写入",
            "导出",
            "放进去",
        )
    )


def _looks_like_approval_detail_action(message: str, result_context) -> bool:
    return bool(
        result_context
        and result_context.items
        and (
            result_context.result_type in {"approval_list", "approval_detail", "approval_query"}
            or any(_looks_like_approval_item(item) for item in result_context.items[:3])
        )
        and any(term in message for term in ("详情", "明细", "展开", "看看第", "看第"))
    )


def _looks_like_approval_item(item: dict[str, Any]) -> bool:
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else item
    return any(
        str(raw.get(key) or "").strip()
        for key in (
            "approval_name",
            "definition_name",
            "instance_code",
            "process_code",
            "task_id",
            "serial_number",
        )
    )


def _confirmation_text(*, intent, plan, permission, pending_action_id: str = "") -> str:
    target_text = _confirmation_target_text(intent)
    reason_text = _confirmation_reason_text(permission)
    impact_text = _confirmation_impact_text(intent, plan)
    return (
        f"确认编号：{pending_action_id}\n"
        f"请求：{intent.canonical_question or ''}\n"
        f"动作：{_intent_label(intent.intent)}\n"
        f"{impact_text}"
        f"{reason_text}"
        f"{target_text}"
        "你也可以回复「确认」继续，或回复「取消」放弃。"
    )


def _confirmation_reason_text(permission) -> str:
    metadata = getattr(permission, "metadata", {}) if permission is not None else {}
    reasons = metadata.get("confirmation_reasons") if isinstance(metadata, dict) else []
    labels = [_confirmation_reason_label(str(item)) for item in reasons if str(item or "").strip()]
    if not labels:
        return ""
    return f"确认原因：{'、'.join(labels)}\n"


def _confirmation_reason_label(reason: str) -> str:
    return {
        "capability_requires_confirmation": "该能力涉及写入或发送",
        "high_risk_action": "执行后会影响真实业务数据",
        "action_question": "这是需要代你执行的操作",
    }.get(reason, reason)


def _confirmation_impact_text(intent, plan) -> str:
    sources = "、".join(_source_label(source) for source in getattr(plan, "sources", ()) or ())
    if intent.intent.startswith("approval_"):
        return "影响：会提交审批处理动作，提交后可能无法撤回。\n"
    if intent.intent == "message_send":
        return "影响：会发送飞书消息，对方可能立即收到。\n"
    if intent.intent == "organization_export":
        return "影响：会读取组织架构、创建/写入表格，并发送结果链接。\n"
    if intent.intent == "calendar_create":
        return "影响：会创建日程，并可能通知参会人。\n"
    if intent.intent.startswith("task_"):
        return "影响：会创建或修改飞书任务。\n"
    if intent.intent.startswith("mail_"):
        return "影响：会创建或修改邮件草稿/邮件内容。\n"
    return f"影响：会调用{sources or '相关'}能力执行实际写入或发送动作。\n"


def _source_label(source: str) -> str:
    return {
        "approval": "审批",
        "people": "通讯录",
        "base": "多维表格",
        "message": "消息发送",
        "im": "飞书消息",
        "calendar": "日程",
        "task": "任务",
        "mail": "邮件",
    }.get(str(source), str(source))


def _confirmation_target_text(intent) -> str:
    entities = getattr(intent, "entities", {}) or {}
    lines = []
    people_summary = _people_targets_summary(intent)
    if people_summary:
        lines.append(people_summary)
    delivery_mode = str(entities.get("delivery_mode") or "").strip()
    if delivery_mode:
        lines.append(f"发送方式：{_delivery_mode_label(delivery_mode)}")
    if intent.intent in {"message_send", "organization_export"}:
        target_type = str(entities.get("target_type") or "").strip()
        target = str(entities.get("target") or "").strip()
        if target_type in {"person", "chat"} and target:
            lines.append(f"发送对象：{_message_target_type_label(target_type)}：{target}")
        elif target_type:
            lines.append(f"发送对象：{_message_target_type_label(target_type)}")
        text = str(entities.get("text") or "").strip()
        if text:
            lines.append(f"消息摘要：{text[:120]}")
    if intent.intent.startswith("approval_") and intent.intent not in {"approval_query", "approval_detail", "approval_initiated"}:
        item = entities.get("item") if isinstance(entities.get("item"), dict) else {}
        if item:
            lines.append(f"审批对象：{item.get('title') or '未命名审批'}")
            applicant = str(item.get("applicant") or "").strip()
            amount = str(item.get("amount") or "").strip()
            if applicant:
                lines.append(f"申请人：{applicant}")
            if amount:
                lines.append(f"金额：{amount}")
        comment = str(entities.get("comment") or "").strip()
        if comment:
            lines.append(f"处理意见：{comment[:80]}")
        target = str(entities.get("target_keyword") or entities.get("transfer_user_id") or "").strip()
        if target:
            lines.append(f"目标人员：{target}")
    return "\n".join(lines) + ("\n" if lines else "")


def _intent_label(intent: str) -> str:
    labels = {
        "approval_approve": "通过审批",
        "approval_reject": "拒绝审批",
        "approval_transfer": "转交审批",
        "approval_add_sign": "加签审批",
        "approval_rollback": "退回审批",
        "approval_remind": "催办审批",
        "approval_cancel": "撤回审批",
        "approval_cc": "抄送审批",
        "message_send": "发送飞书消息",
        "organization_export": "创建并写入组织架构表",
        "task_create": "创建任务",
        "task_complete": "完成任务",
        "calendar_create": "创建日程",
        "mail_draft_create": "创建邮件草稿",
    }
    return labels.get(intent, intent)


def _message_target_type_label(target_type: str) -> str:
    labels = {"person": "人员", "chat": "群聊", "self": "本人", "current_chat": "当前会话", "people_context": "上一轮人员结果"}
    return labels.get(target_type, target_type)


def _delivery_mode_label(mode: str) -> str:
    return {
        "bot_multi_notify": "用机器人通知多人",
        "user_multi_private": "以本人身份分别发送",
        "create_group_then_send": "建群后在群里发送",
    }.get(str(mode), str(mode))


def _people_targets_payload(intent) -> list[dict[str, Any]]:
    entities = getattr(intent, "entities", {}) or {}
    targets = entities.get("people_targets") if isinstance(entities, dict) else None
    if not isinstance(targets, list):
        return []
    payload: list[dict[str, Any]] = []
    for item in targets:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        open_id = str(item.get("open_id") or item.get("user_id") or "").strip()
        email = str(item.get("email") or "").strip()
        if not (name or open_id or email):
            continue
        payload.append(
            {
                "name": name,
                "open_id": open_id,
                "email": email,
                "department": str(item.get("department") or "").strip(),
                "title": str(item.get("title") or "").strip(),
            }
        )
    return payload


def _people_targets_summary(intent) -> str:
    targets = _people_targets_payload(intent)
    if not targets:
        return ""
    names = [str(item.get("name") or item.get("email") or item.get("open_id") or "").strip() for item in targets[:5]]
    names = [item for item in names if item]
    suffix = f"：{'、'.join(names)}" if names else ""
    if len(targets) > 5:
        suffix += f"等 {len(targets)} 人"
    return f"人员目标：{len(targets)} 人{suffix}"
