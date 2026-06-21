from dataclasses import dataclass, field, replace
import json
import re
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.ai.advisor import answer_advisor_question
from app.services.agent.context import bot_answer_style, load_bot_user_context
from app.services.agent.planner import (
    AgentPlan,
    AgentPlanStep,
    agent_plan_payload,
    build_agent_plan,
    _DEFAULT_QUERY_BI_TABLE_FIELDS,
    resolve_execution_category_with_source,
)
from app.services.agent.permission import check_plan_permissions
from app.services.agent.policies import BotActor, BotAnswerRoute, resolve_bot_route
from app.services.agent.reply_modes import (
    AgentReplyMode,
    reply_mode_payload,
    resolve_agent_reply_mode,
    thinking_map_text,
    thinking_preview_payload,
)
from app.services.llm.answer_rewriter import rewrite_bot_answer
# Lazy import to avoid bytecode cache issues in Docker
import importlib as _importlib
def _get_semantic_intent_for_question():
    return _importlib.import_module("app.services.llm.answer_semantics").semantic_intent_for_question
semantic_intent_for_question = _get_semantic_intent_for_question()
from app.services.tools.base import (
    DATA_BOUNDARY_POLICY,
    DATA_PERMISSION_MODEL,
    DIGITAL_ADVISOR_PERMISSION_POLICY,
    ENTERPRISE_IDENTITY_CONSTRAINTS,
    ENTERPRISE_IDENTITY_RESOURCE_OWNER,
    TOOL_ACCESS_POLICY,
    TOOL_SHARING_MODEL,
    USER_IDENTITY_CONSTRAINTS,
    USER_IDENTITY_RESOURCES,
    ToolContext,
    ToolExecutionStatus,
    ToolRequest,
    SHARED_TOOL_COUNT,
    identity_permission_contract,
)
from app.services.tools.router import TOOL_REGISTRY, execute_agent_tool
from app.services.tools.router import AGENT_RUNTIME_CHAIN, preflight_agent_tool_data_permission


@dataclass(frozen=True)
class AgentExecutionStep:
    kind: str
    name: str
    status: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class AgentRuntimeTrace:
    semantic_intent: str
    route_path: str
    route_scope: str
    route_reason: str | None
    execution_category: str
    execution_category_source: str
    scope_label: str
    route_label: str
    agent_identity: dict[str, Any]
    actor_context: dict[str, Any]
    reply_mode: dict[str, Any]
    thinking_preview: dict[str, Any] | None
    steps: tuple[AgentExecutionStep, ...]
    memory_context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentRuntimeResult:
    answer: str
    trace: AgentRuntimeTrace


TOOL_AGENT_ROUTES = {
    "bitable_qa",
    "calendar_qa",
    "chat_qa",
    "chat_summary",
    "chat_tasks",
    "company_qa",
   "domain_qa",
   "feishu_calendar_create_event",
   "feishu_approval_task_query",
   "feishu_contact_organization_snapshot",
   "feishu_contact_department_users",
   "feishu_contact_user_search",
   "feishu_contact_department_users",
   "feishu_task_create",
    "feishu_vc_meeting_search",
    "general_chat",
    "mail_qa",
    "owner_cockpit",
    "personal_tasks",
    "public_knowledge_qa",
    "task_qa",
}


def agent_execution_step_payload(step: AgentExecutionStep) -> dict[str, Any]:
    return {
        "kind": step.kind,
        "name": step.name,
        "status": step.status,
        "metadata": step.metadata,
    }


def agent_runtime_trace_payload(trace: AgentRuntimeTrace) -> dict[str, Any]:
    return {
        "runtime_contract": {
            "chain": list(AGENT_RUNTIME_CHAIN),
            "tool_decides_data_or_execution_source": True,
            "tool_returns_structured_result": True,
            "final_answer_owner": "agent_runtime",
        },
        "semantic_intent": trace.semantic_intent,
        "route_path": trace.route_path,
        "route_scope": trace.route_scope,
        "route_reason": trace.route_reason,
        "execution_category": trace.execution_category,
        "execution_category_source": trace.execution_category_source,
        "scope_label": trace.scope_label,
        "route_label": trace.route_label,
        "agent_identity": trace.agent_identity,
        "actor_context": trace.actor_context,
        "memory_context": trace.memory_context,
        "reply_mode": trace.reply_mode,
        "thinking_preview": trace.thinking_preview,
        "steps": [agent_execution_step_payload(step) for step in trace.steps],
    }


def agent_runtime_result_payload(result: AgentRuntimeResult) -> dict[str, Any]:
    return {"answer": result.answer, "trace": agent_runtime_trace_payload(result.trace)}


def answer_agent_message(
    db: Session,
    *,
    company_id: UUID,
    question: str,
    normalized_command: str,
    chat_id: str | None,
    actor: BotActor,
    cli_profile: str | None = None,
    planner_enabled: bool = False,
    max_planner_steps: int = 3,
    allow_write_tools: bool = True,
    require_write_confirmation: bool = True,
) -> str:
    return answer_agent_message_with_trace(
        db,
        company_id=company_id,
        question=question,
        normalized_command=normalized_command,
        chat_id=chat_id,
        actor=actor,
        cli_profile=cli_profile,
        planner_enabled=planner_enabled,
        max_planner_steps=max_planner_steps,
        allow_write_tools=allow_write_tools,
        require_write_confirmation=require_write_confirmation,
    ).answer


def answer_agent_message_with_trace(
    db: Session,
    *,
    company_id: UUID,
    question: str,
    normalized_command: str,
    chat_id: str | None,
    actor: BotActor,
    cli_profile: str | None = None,
    planner_enabled: bool = False,
    max_planner_steps: int = 3,
    allow_write_tools: bool = True,
    require_write_confirmation: bool = True,
) -> AgentRuntimeResult:
    steps: list[AgentExecutionStep] = []
    plan: AgentPlan | None = None
    semantic = semantic_intent_for_question(question=question, normalized_command=normalized_command, actor=actor, chat_id=chat_id)
    if getattr(semantic, "clarification", None):
        return AgentRuntimeResult(answer=semantic.clarification, trace=_trace(semantic_intent=semantic, route_path="clarification", route_scope="chat", scope_label="chat", route_label="clarification", execution_category="query", execution_category_source="clarification", reply_mode=reply_mode_payload(AgentReplyMode.FAST), thinking_preview=None, actor_context=_actor_context(actor), agent_identity=_agent_identity(company_id, actor), memory_context=_empty_memory_context(route_scope="chat", route_path="clarification")))
    effective_question = semantic.canonical_question or question
    effective_normalized = _semantic_normalized_command(semantic.module_hint, normalized_command)
    steps.append(
        AgentExecutionStep(
            kind="semantic",
            name=_semantic_trace_name(semantic),
            status="success",
            metadata={
                "confidence": getattr(semantic, "confidence", 0.0),
                "route_hint": getattr(semantic, "route_hint", None),
                "module_hint": getattr(semantic, "module_hint", None),
                "canonical_question": effective_question,
            },
        )
    )
    route = resolve_bot_route(question=effective_question, normalized_command=effective_normalized, actor=actor)
    route = _route_with_semantic_hint(route, semantic.route_hint)
    execution_category, execution_category_source = resolve_execution_category_with_source(
        route=route,
        semantic=semantic,
    )
    scope_label = answer_scope_label(actor=actor, route_scope=route.scope, chat_id=chat_id)
    route_label = answer_route_label(route.path)
    reply_mode = resolve_agent_reply_mode(
        route_path=route.path,
        route_label=route_label,
        execution_category=execution_category,
    )
    memory_context = _agent_memory_context(
        db=db,
        company_id=company_id,
        actor=actor,
        chat_id=chat_id,
        route_scope=route.scope,
        route_path=route.path,
    )
    memory_context_hint = _agent_memory_context_hint(memory_context)
    steps.append(
        AgentExecutionStep(
            kind="route",
            name=route.path,
            status="denied" if route.denied else "success",
            metadata={
                "scope": route.scope,
                "reason": route.reason,
                "message": route.message,
                "reply_mode": reply_mode_payload(reply_mode),
            },
        )
    )

    plan: AgentPlan | None = None
    if planner_enabled or _is_tool_route(route.path):
        candidate_plan = build_agent_plan(
            route=route,
            semantic=semantic,
            max_steps=max_planner_steps,
            allow_write_tools=allow_write_tools,
            require_write_confirmation=require_write_confirmation,
        )
        if planner_enabled or sum(1 for item in candidate_plan.steps if item.kind == "tool") > 1:
            plan = candidate_plan
            execution_category = candidate_plan.execution_category
            execution_category_source = candidate_plan.execution_category_source
            planned_tool_count = sum(1 for item in candidate_plan.steps if item.kind == "tool")
            if planned_tool_count > 1:
                reply_mode = resolve_agent_reply_mode(
                    route_path=route.path,
                    route_label=route_label,
                    execution_category=execution_category,
                    planned_tool_count=planned_tool_count,
                )
                steps[-1].metadata["reply_mode"] = reply_mode_payload(reply_mode)

    if route.denied:
        answer = _finalize_answer(
            route.message or "这部分信息未向你开放。",
            db=db,
            company_id=company_id,
            question=question,
            actor=actor,
            scope_label=scope_label,
            route_label=route_label,
            reply_mode=reply_mode,
            memory_context_hint=memory_context_hint,
        )
        steps.append(AgentExecutionStep(kind="answer", name="permission_denied", status="success", metadata={}))
        return AgentRuntimeResult(
            answer=answer,
            trace=_trace(
                semantic,
                route,
                company_id,
                actor,
                scope_label,
                route_label,
                execution_category,
                execution_category_source,
                reply_mode,
                steps,
                memory_context=memory_context,
            ),
        )
    if plan is not None:
        steps.append(
            AgentExecutionStep(
                kind="planner",
                name="agent_plan",
                status="success",
                metadata=agent_plan_payload(plan),
            )
        )
        # Permission Check before Capability Router
        _permit_tc = ToolContext(db=db, company_id=company_id, actor=actor, chat_id=chat_id, cli_profile=cli_profile)
        plan = check_plan_permissions(plan, _permit_tc)
        planned_answer, executed_planned_tools = _execute_planned_tool_steps(
            plan=plan,
            db=db,
            company_id=company_id,
            actor=actor,
            chat_id=chat_id,
            cli_profile=cli_profile,
            question=effective_question,
            normalized_command=effective_normalized,
            allow_write_tools=allow_write_tools,
            require_write_confirmation=require_write_confirmation,
            steps=steps,
        )
        if executed_planned_tools:
            answer = _finalize_answer(
                planned_answer,
                db=db,
                company_id=company_id,
                question=question,
                actor=actor,
                scope_label=scope_label,
                route_label=route_label,
                reply_mode=reply_mode,
                memory_context_hint=memory_context_hint,
            )
            return AgentRuntimeResult(
                answer=answer,
                trace=_trace(
                    semantic,
                    route,
                    company_id,
                    actor,
                    scope_label,
                    route_label,
                    execution_category,
                    execution_category_source,
                    reply_mode,
                    steps,
                    memory_context=memory_context,
                ),
            )
    if _is_tool_route(route.path):
        tool_context = ToolContext(db=db, company_id=company_id, actor=actor, chat_id=chat_id, cli_profile=cli_profile)
        tool_request = ToolRequest(
            tool_name=route.path,
            question=effective_question,
            normalized_command=effective_normalized,
        )
        preflight_result = _preflight_write_tool_data_permission(tool_context, tool_request)
        if preflight_result is not None:
            steps.append(_tool_result_execution_step(preflight_result))
            answer = _finalize_answer(
                _tool_result_response_text(preflight_result),
                db=db,
                company_id=company_id,
                question=question,
                actor=actor,
                scope_label=scope_label,
                route_label=route_label,
                reply_mode=reply_mode,
                memory_context_hint=memory_context_hint,
            )
            return AgentRuntimeResult(
                answer=answer,
                trace=_trace(
                    semantic,
                    route,
                    company_id,
                    actor,
                    scope_label,
                    route_label,
                    execution_category,
                    execution_category_source,
                    reply_mode,
                    steps,
                    memory_context=memory_context,
                ),
            )
        write_policy = _write_tool_policy_result(
            route.path,
            allow_write_tools=allow_write_tools,
            require_write_confirmation=require_write_confirmation,
        )
        if write_policy is not None:
            steps.append(
                AgentExecutionStep(
                    kind="tool",
                    name=route.path,
                    status=write_policy["status"],
                    metadata=write_policy["metadata"],
                )
            )
            answer = _finalize_answer(
                str(write_policy["answer"]),
                db=db,
                company_id=company_id,
                question=question,
                actor=actor,
                scope_label=scope_label,
                route_label=route_label,
                reply_mode=reply_mode,
                memory_context_hint=memory_context_hint,
            )
            return AgentRuntimeResult(
                answer=answer,
                trace=_trace(
                    semantic,
                    route,
                    company_id,
                    actor,
                    scope_label,
                    route_label,
                execution_category,
                execution_category_source,
                reply_mode,
                    steps,
                    memory_context=memory_context,
                ),
            )
        tool_result = execute_agent_tool(
            tool_context,
            tool_request,
        )
        steps.append(_tool_result_execution_step(tool_result))
        answer = _finalize_answer(
            _tool_result_response_text(tool_result),
            db=db,
            company_id=company_id,
            question=question,
            actor=actor,
            scope_label=scope_label,
            route_label=route_label,
            reply_mode=reply_mode,
            memory_context_hint=memory_context_hint,
        )
        return AgentRuntimeResult(
            answer=answer,
            trace=_trace(
                semantic,
                route,
                company_id,
                actor,
                scope_label,
                route_label,
                execution_category,
                execution_category_source,
                reply_mode,
                steps,
                memory_context=memory_context,
            ),
        )
    answer = answer_advisor_question(
        db,
        company_id=company_id,
        question=effective_question,
        scope=route.scope,
        chat_id=chat_id,
        actor_role=actor.role,
        actor_access_scope=actor.access_scope,
        actor_domains=list(actor.domains),
    )
    steps.append(
        AgentExecutionStep(
            kind="advisor",
            name="answer_advisor_question",
            status="success",
            metadata={"scope": route.scope},
        )
    )
    final_answer = _finalize_answer(
        answer,
        db=db,
        company_id=company_id,
        question=question,
        actor=actor,
        scope_label=scope_label,
        route_label=route_label,
        reply_mode=reply_mode,
        memory_context_hint=memory_context_hint,
    )
    return AgentRuntimeResult(
        answer=final_answer,
        trace=_trace(
            semantic,
            route,
            company_id,
            actor,
            scope_label,
            route_label,
            execution_category,
            execution_category_source,
            reply_mode,
            steps,
            memory_context=memory_context,
        ),
    )


def finalize_agent_workflow_reply_with_trace(
    db: Session | None,
    *,
    company_id: UUID,
    question: str,
    normalized_command: str,
    chat_id: str | None,
    actor: BotActor,
    route_path: str,
    raw_answer: str,
    workflow_name: str,
    workflow_status: str = "success",
    workflow_metadata: dict[str, Any] | None = None,
) -> AgentRuntimeResult:
    semantic = semantic_intent_for_question(question=question, normalized_command=normalized_command, actor=actor)
    route_scope = _trace_data_access_scope(route_scope=answer_scope_for_workflow_actor(actor), route_path=route_path)
    route = BotAnswerRoute(path=route_path, scope=route_scope, reason="workflow_result")
    scope_label = answer_scope_label(actor=actor, route_scope=route.scope, chat_id=chat_id)
    route_label = answer_route_label(route.path)
    execution_category, execution_category_source = resolve_execution_category_with_source(
        route=route,
        semantic=semantic,
    )
    reply_mode = resolve_agent_reply_mode(
        route_path=route.path,
        route_label=route_label,
        execution_category=execution_category,
    )
    memory_context = _agent_memory_context(
        db=db,
        company_id=company_id,
        actor=actor,
        chat_id=chat_id,
        route_scope=route.scope,
        route_path=route.path,
    )
    memory_context_hint = _agent_memory_context_hint(memory_context)
    steps = [
        AgentExecutionStep(
            kind="semantic",
            name=_semantic_trace_name(semantic),
            status="success",
            metadata={
                "confidence": getattr(semantic, "confidence", 0.0),
                "route_hint": getattr(semantic, "route_hint", None),
                "module_hint": getattr(semantic, "module_hint", None),
                "canonical_question": getattr(semantic, "canonical_question", None) or question,
            },
        ),
        AgentExecutionStep(
            kind="workflow",
            name=workflow_name,
            status=workflow_status,
            metadata={
                "route_path": route.path,
                "route_label": route_label,
                "reply_mode_id": reply_mode.mode_id,
                "reply_mode_label": reply_mode.label,
                "final_answer_owner": "agent_runtime",
                **(workflow_metadata or {}),
            },
        ),
    ]
    answer = _finalize_answer(
        raw_answer,
        db=db,
        company_id=company_id,
        question=question,
        actor=actor,
        scope_label=scope_label,
        route_label=route_label,
        reply_mode=reply_mode,
        memory_context_hint=memory_context_hint,
        chat_id=chat_id,
    )
    steps.append(AgentExecutionStep(kind="answer", name="agent_runtime_final_answer", status="success", metadata={}))
    return AgentRuntimeResult(
        answer=answer,
        trace=_trace(
            semantic,
            route,
            company_id,
            actor,
            scope_label,
            route_label,
            execution_category,
            execution_category_source,
            reply_mode,
            steps,
            memory_context=memory_context,
        ),
    )


def answer_scope_for_workflow_actor(actor: BotActor) -> str:
    if actor.can_query_company:
        return "company"
    if actor.access_scope in {"personal", "self", "user", "domain", "department", "project"}:
        return actor.access_scope
    return "chat"


def _is_tool_route(path: str) -> bool:
    return path in TOOL_AGENT_ROUTES or path in TOOL_REGISTRY


def _execute_planned_tool_steps(
    *,
    plan: AgentPlan,
    db: Session,
    company_id: UUID,
    actor: BotActor,
    chat_id: str | None,
    cli_profile: str | None,
    question: str,
    normalized_command: str,
    allow_write_tools: bool,
    require_write_confirmation: bool,
    steps: list[AgentExecutionStep],
) -> tuple[str, bool]:
    answers: list[tuple[str, str, str, str | None]] = []
    executed_any_tool = False
    plan_step_statuses: dict[str, str] = {}
    plan_context: dict[str, Any] = {
        "shared": {
            "query_fields": [dict(item) for item in _DEFAULT_QUERY_BI_TABLE_FIELDS],
            "chat_id": chat_id,
            "open_id": actor.open_id or "",
            "user_id": actor.open_id or "",
        },
        "steps": [],
    }

    for plan_step in plan.steps:
        if plan_step.kind != "tool" or not _is_tool_route(plan_step.name):
            continue

        unmet_dependencies = _plan_step_unmet_dependencies(
            plan_step=plan_step,
            plan_step_statuses=plan_step_statuses,
        )
        if unmet_dependencies:
            executed_any_tool = True
            plan_stop_reason = f"dependency_unsatisfied:{','.join(unmet_dependencies)}"
            plan_step_status = "skipped"
            plan_step_statuses[plan_step.name] = plan_step_status
            plan_metadata = _plan_context_payload(
                plan_context=plan_context,
                plan_retry_count=0,
                plan_step_status=plan_step_status,
                execution_category=plan.execution_category,
                execution_category_source=plan.execution_category_source,
            )
            steps.append(
                _skipped_tool_result_execution_step(
                    plan_step,
                    plan_stop_reason=plan_stop_reason,
                    plan_metadata=plan_metadata,
                )
            )
            answers.append(
                (
                    plan_step.name,
                    _planned_tool_skip_answer(plan_step.name, unmet_dependencies),
                    plan_step_status,
                    plan_stop_reason,
                )
            )
            if _plan_step_should_stop_on_error(plan_step, plan_step_status):
                return _compose_planned_tool_answer(answers), executed_any_tool
            continue

        tool_context = ToolContext(db=db, company_id=company_id, actor=actor, chat_id=chat_id, cli_profile=cli_profile)
        tool_params = plan_step.metadata.get("tool_params")
        if not isinstance(tool_params, dict):
            tool_params = {}
        resolved_tool_params = _resolve_plan_tool_params(tool_params, plan_context=plan_context)
        if plan_step.name == "feishu_contact_user_get" and getattr(actor, "open_id", ""):
            resolved_tool_params.setdefault("open_id", actor.open_id)

        validation_error = _validate_bitable_plan_step_requirements(plan_step, resolved_tool_params)
        if validation_error is not None:
            executed_any_tool = True
            plan_step_status = ToolExecutionStatus.ERROR.value
            plan_step_statuses[plan_step.name] = plan_step_status
            plan_stop_reason = "tool_request_invalid"
            plan_metadata = _plan_context_payload(
                plan_context=plan_context,
                plan_retry_count=0,
                plan_step_status=plan_step_status,
                execution_category=plan.execution_category,
                execution_category_source=plan.execution_category_source,
            )
            steps.append(
                _validation_error_tool_result_execution_step(
                    plan_step,
                    validation_error,
                    plan_metadata=plan_metadata,
                    plan_step_status=plan_step_status,
                    plan_stop_reason=plan_stop_reason,
                )
            )
            answers.append((plan_step.name, validation_error, plan_step_status, plan_stop_reason))
            if _plan_step_should_stop_on_error(plan_step, plan_step_status):
                return _compose_planned_tool_answer(answers), executed_any_tool
            continue

        tool_request = ToolRequest(
            tool_name=plan_step.name,
            question=question,
            normalized_command=normalized_command,
            params=resolved_tool_params,
        )
        if not plan_step.permitted:
            executed_any_tool = True
            plan_step_status = "denied"
            plan_step_statuses[plan_step.name] = plan_step_status
            plan_step_metadata = _plan_context_payload(
                plan_context=plan_context,
                plan_retry_count=0,
                plan_step_status=plan_step_status,
                execution_category=plan.execution_category,
                execution_category_source=plan.execution_category_source,
            )
            plan_stop_reason = plan_step.deny_reason or "permission_denied"
            steps.append(
                _skipped_tool_result_execution_step(
                    plan_step,
                    plan_stop_reason=plan_stop_reason,
                    plan_metadata=plan_step_metadata,
                )
            )
            answers.append(
                (
                    plan_step.name,
                    _planned_tool_skip_answer(plan_step.name, [plan_stop_reason]),
                    plan_step_status,
                    plan_stop_reason,
                )
            )
            if _plan_step_should_stop_on_error(plan_step, plan_step_status):
                return _compose_planned_tool_answer(answers), executed_any_tool
            continue
        write_policy = _write_tool_policy_result(
            plan_step.name,
            allow_write_tools=allow_write_tools,
            require_write_confirmation=require_write_confirmation,
        )
        if write_policy is not None:
            executed_any_tool = True
            plan_step_status = write_policy["status"]
            plan_step_statuses[plan_step.name] = plan_step_status
            plan_step_metadata = _plan_context_payload(
                plan_context=plan_context,
                plan_retry_count=0,
                plan_step_status=plan_step_status,
                execution_category=plan.execution_category,
                execution_category_source=plan.execution_category_source,
            )
            plan_stop_reason = write_policy["metadata"].get("policy_reason")
            steps.append(
                AgentExecutionStep(
                    kind="tool",
                    name=plan_step.name,
                    status=write_policy["status"],
                    metadata={
                        **write_policy["metadata"],
                        "required": plan_step.required,
                        "on_error": plan_step.on_error,
                        "depends_on": list(plan_step.depends_on),
                        "planned": True,
                        "plan_stop_reason": plan_stop_reason,
                        **plan_step_metadata,
                    },
                )
            )
            answers.append((plan_step.name, str(write_policy["answer"]), plan_step_status, plan_stop_reason))
            if _plan_step_should_stop_on_error(plan_step, plan_step_status):
                return _compose_planned_tool_answer(answers), executed_any_tool
            continue

        tool_result = execute_agent_tool(tool_context, tool_request)
        retry_count = 0
        if _should_retry_planned_tool(tool_result):
            retry_count = 1
            resolved_tool_params = _resolve_plan_tool_params(tool_params, plan_context=plan_context)
            tool_request = ToolRequest(
                tool_name=plan_step.name,
                question=question,
                normalized_command=normalized_command,
                params=resolved_tool_params,
            )
            tool_result = execute_agent_tool(tool_context, tool_request)

        plan_step_status = tool_result.status.value
        plan_step_statuses[plan_step.name] = plan_step_status
        plan_stop_reason = None if tool_result.status == ToolExecutionStatus.SUCCESS else f"tool_{tool_result.status.value}"
        plan_metadata = _plan_context_payload(
            plan_context=plan_context,
            plan_retry_count=retry_count,
            plan_step_status=plan_step_status,
            execution_category=plan.execution_category,
            execution_category_source=plan.execution_category_source,
        )
        if tool_result.status == ToolExecutionStatus.SUCCESS:
            _update_plan_context_with_tool_result(
                plan_step,
                tool_request,
                tool_result,
                plan_context=plan_context,
            )
        steps.append(
            _tool_result_execution_step(
                tool_result,
                planned=True,
                plan_step=plan_step,
                plan_stop_reason=plan_stop_reason,
                plan_metadata=plan_metadata,
            )
        )
        answers.append((tool_result.tool_name, _tool_result_response_text(tool_result), plan_step_status, plan_stop_reason))
        executed_any_tool = True
        if plan_stop_reason is not None and _plan_step_should_stop_on_error(plan_step, plan_step_status):
            return _compose_planned_tool_answer(answers), executed_any_tool
    return _compose_planned_tool_answer(answers), executed_any_tool


def _plan_step_unmet_dependencies(*, plan_step: AgentPlanStep, plan_step_statuses: dict[str, str]) -> list[str]:
    if not plan_step.depends_on:
        return []
    return [name for name in plan_step.depends_on if plan_step_statuses.get(name) != ToolExecutionStatus.SUCCESS.value]


def _plan_step_on_error_mode(*, plan_step: AgentPlanStep) -> str:
    if plan_step.on_error == "continue":
        return "continue"
    return "stop"


def _plan_step_should_stop_on_error(plan_step: AgentPlanStep, plan_step_status: str) -> bool:
    if plan_step_status == ToolExecutionStatus.SUCCESS.value:
        return False
    if not plan_step.required:
        return False
    return _plan_step_on_error_mode(plan_step=plan_step) != "continue"


def _planned_tool_skip_answer(tool_name: str, unmet_dependencies: list[str]) -> str:
    if not unmet_dependencies:
        return f"{tool_name}未执行（依赖未满足）"
    if len(unmet_dependencies) == 1:
        return f"{tool_name}未执行（依赖未满足：{unmet_dependencies[0]}）"
    return f"{tool_name}未执行（依赖未满足：{','.join(unmet_dependencies)}）"


def _skipped_tool_result_execution_step(
    plan_step: AgentPlanStep,
    *,
    plan_stop_reason: str | None,
    plan_metadata: dict[str, Any] | None = None,
) -> AgentExecutionStep:
    metadata = {
        "provider": plan_step.metadata.get("provider"),
        "required": plan_step.required,
        "on_error": plan_step.on_error,
        "depends_on": list(plan_step.depends_on),
        "data_source": None,
        "execution_source": None,
        "structured_result": None,
        "tool_returns_structured_result": False,
        "final_answer_owner": "agent_runtime",
        "error": None,
        "plan_stop_reason": plan_stop_reason,
    }
    if plan_metadata is not None:
        metadata.update(plan_metadata)
    return AgentExecutionStep(
        kind="tool",
        name=plan_step.name,
        status="skipped",
        metadata=metadata,
    )


def _preflight_write_tool_data_permission(context: ToolContext, request: ToolRequest):
    definition = TOOL_REGISTRY.get(request.tool_name)
    if definition is None or not definition.supports_write:
        return None
    return preflight_agent_tool_data_permission(context, request)


def _tool_result_execution_step(
    tool_result,
    *,
    planned: bool = False,
    plan_step: AgentPlanStep | None = None,
    plan_stop_reason: str | None = None,
    plan_metadata: dict[str, Any] | None = None,
) -> AgentExecutionStep:
    metadata = {
        "provider": tool_result.provider.value,
        "data_source": tool_result.data_source,
        "execution_source": tool_result.execution_source,
        "structured_result": tool_result.structured_result,
        "tool_returns_structured_result": bool(tool_result.structured_result),
        "final_answer_owner": "agent_runtime",
        "error": tool_result.error,
        **tool_result.metadata,
    }
    if planned:
        metadata["planned"] = True
        metadata["required"] = plan_step.required if plan_step is not None else True
        metadata["on_error"] = plan_step.on_error if plan_step is not None else "stop"
        metadata["depends_on"] = list(plan_step.depends_on) if plan_step is not None else []
        metadata["plan_stop_reason"] = plan_stop_reason
        if plan_metadata is not None:
            metadata.update(plan_metadata)
    return AgentExecutionStep(
        kind="tool",
        name=tool_result.tool_name,
        status=tool_result.status.value,
        metadata=metadata,
    )


def _resolve_plan_tool_params(params: dict[str, Any], *, plan_context: dict[str, Any]) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for key, value in params.items():
        resolved[key] = _resolve_plan_value(value, plan_context=plan_context)
    return resolved


_PLAN_TOOL_PARAM_PLACEHOLDER = re.compile(r"\$\{([^{}]+)\}")
_FULL_PLACEHOLDER = re.compile(r"^\$\{([^{}]+)\}$")


def _resolve_plan_value(value: Any, *, plan_context: dict[str, Any]) -> Any:
    if isinstance(value, str):
        if _FULL_PLACEHOLDER.match(value):
            replacement = _resolve_plan_context_value(value[2:-1], plan_context=plan_context)
            return replacement or ""
        def _replace(match: re.Match[str]) -> str:
            replacement = _resolve_plan_context_value(match.group(1), plan_context=plan_context)
            return "" if replacement is None else str(replacement)
        return _PLAN_TOOL_PARAM_PLACEHOLDER.sub(_replace, value)
    if isinstance(value, list):
        return [_resolve_plan_value(item, plan_context=plan_context) for item in value]
    if isinstance(value, tuple):
        return [_resolve_plan_value(item, plan_context=plan_context) for item in value]
    if isinstance(value, dict):
        return {k: _resolve_plan_value(v, plan_context=plan_context) for k, v in value.items()}
    return value


def _resolve_plan_context_value(path: str, *, plan_context: dict[str, Any]) -> Any:
    value: Any = plan_context
    for segment in path.split("."):
        if not isinstance(value, dict) or segment not in value:
            return None
        value = value[segment]
    return value


def _plan_context_payload(
    *,
    plan_context: dict[str, Any],
    plan_retry_count: int,
    plan_step_status: str,
    execution_category: str = "query",
    execution_category_source: str = "route_fallback",
) -> dict[str, Any]:
    # Execution category source is intentionally explicit here to prevent silent drift to defaults.
    return {
        "plan_retry_count": plan_retry_count,
        "plan_step_status": plan_step_status,
        "execution_category": execution_category,
        "execution_category_source": execution_category_source,
        "plan_context": {
            "shared": dict(plan_context.get("shared", {})),
            "steps": list(plan_context.get("steps", [])),
        },
    }


def _should_retry_planned_tool(tool_result) -> bool:
    if tool_result.status != ToolExecutionStatus.ERROR:
        return False
    error = str(tool_result.error or tool_result.answer or "").lower()
    if not error:
        return False
    return any(
        token in error
        for token in ("timeout", "timed out", "连接超时", "网络", "connection", "temporar", "retry", "429", "502", "503", "504")
    )


def _update_plan_context_with_tool_result(
    plan_step,
    tool_request,
    tool_result,
    *,
    plan_context: dict[str, Any],
) -> None:
    result_payload = _extract_tool_result_payload(tool_result)
    request_params = getattr(tool_request, "params", {})
    if not isinstance(request_params, dict):
        request_params = {}
    if not isinstance(plan_step, type) and not hasattr(plan_step, "metadata"):
        return
    keys = set()
    try:
        keys = set(plan_step.metadata.get("plan_context_keys", []))
    except Exception:
        keys = set()
    if not keys:
        return

    update: dict[str, Any] = {}
    if "organization_rows" in keys:
        update["organization_rows"] = _organization_rows_from_snapshot(result_payload)
    if "query_rows" in keys:
        update["query_rows"] = _query_rows_from_payload(result_payload)
    if "query_fields" in keys:
        extracted_fields = _query_fields_from_payload(result_payload)
        if extracted_fields:
            update["query_fields"] = extracted_fields
    if "app_token" in keys:
        request_app_token = _first_non_empty_string(
            _extract_from_dict(request_params, ("app_token", "base_token")),
            _extract_from_dict(request_params, ("app_token",)),
        )
        update["app_token"] = (
            _first_non_empty_string(
                _extract_from_dict(result_payload, ("app_token", "base_token")),
                request_app_token,
            )
        )
    if "table_id" in keys:
        update["table_id"] = _first_non_empty_string(
            _extract_bitable_table_id(result_payload),
            request_params.get("table_id"),
        )
    
    for name in keys - set(update):
        if name in request_params and isinstance(request_params[name], (str, int, float, list, dict, bool)):
            update[name] = request_params[name]
            continue
        if name in result_payload and isinstance(result_payload[name], (str, int, float, list, dict, bool)):
            update[name] = result_payload[name]

    if update:
        shared = dict(plan_context.get("shared", {}))
        shared.update(update)
        plan_context["shared"] = shared
        plan_context["steps"] = [
            *plan_context.get("steps", []),
            {
                "tool": tool_result.tool_name,
                "extracted": update,
                "status": tool_result.status.value,
            },
        ]


def _validate_bitable_plan_step_requirements(
    plan_step: AgentPlanStep,
    resolved_tool_params: dict[str, Any],
) -> str | None:
    if not plan_step.name.startswith("feishu_bitable_"):
        return None

    app_token = _first_non_empty_string(
        resolved_tool_params.get("app_token"),
        resolved_tool_params.get("base_token"),
    )
    if not app_token:
        if plan_step.name == "feishu_bitable_table_create":
            return "缺少 app_token：创建多维表格需要先指定多维表格应用 token（如 bascn-xxxx）。可在问题里补充“使用 bascn-xxxx”。"
        return f"缺少 app_token：{plan_step.name} 需要多维表格应用 token，才能执行。"

    if plan_step.name != "feishu_bitable_table_create":
        table_id = _first_non_empty_string(resolved_tool_params.get("table_id"))
        if not table_id:
            return f"缺少 table_id：{plan_step.name} 需要表 ID，请先成功创建并返回表信息。"
    return None


def _validation_error_tool_result_execution_step(
    plan_step: AgentPlanStep,
    validation_error: str,
    *,
    plan_metadata: dict[str, Any] | None = None,
    plan_step_status: str,
    plan_stop_reason: str | None,
) -> AgentExecutionStep:
    metadata = {
        "provider": plan_step.metadata.get("provider"),
        "required": plan_step.required,
        "on_error": plan_step.on_error,
        "depends_on": list(plan_step.depends_on),
        "data_source": "agent_runtime",
        "execution_source": "agent_runtime",
        "structured_result": None,
        "tool_returns_structured_result": False,
        "final_answer_owner": "agent_runtime",
        "error": validation_error,
        "plan_stop_reason": plan_stop_reason,
    }
    if plan_metadata is not None:
        metadata.update(plan_metadata)
    return AgentExecutionStep(
        kind="tool",
        name=plan_step.name,
        status=plan_step_status,
        metadata=metadata,
    )


def _extract_tool_result_payload(tool_result) -> dict[str, Any]:
    structured = tool_result.structured_result
    if isinstance(structured, dict):
        if isinstance(structured.get("response_payload"), dict):
            payload = structured["response_payload"]
            if isinstance(payload, dict):
                return payload
        response_text = structured.get("response_text")
        if isinstance(response_text, str):
            parsed = _parse_json_if_any(response_text)
            if parsed:
                return parsed
    if isinstance(tool_result.answer, str):
        parsed = _parse_json_if_any(tool_result.answer)
        if parsed:
            return parsed
    return {}


def _extract_bitable_table_id(payload: dict[str, Any]) -> str | None:
    candidates: list[Any] = [
        payload.get("table_id"),
        payload.get("tableId"),
        payload.get("id"),
        _extract_from_dict(payload, ("data", "table_id")),
        _extract_from_dict(payload, ("data", "table", "table_id")),
        _extract_from_dict(payload, ("data", "table", "id")),
    ]
    for candidate in candidates:
        value = _first_non_empty_string(candidate)
        if value:
            return value
    table_list = _extract_from_dict(payload, ("data", "items")) or _extract_from_dict(payload, ("items",))
    if isinstance(table_list, list):
        for item in table_list:
            item_table_id = _first_non_empty_string(_extract_from_dict(item, ("table_id", "tableId", "id")))
            if item_table_id:
                return item_table_id
    return None


def _organization_rows_from_snapshot(payload: dict[str, Any]) -> list[list[str]]:
    users = payload.get("users")
    if not isinstance(users, list):
        return []
    rows: list[list[str]] = []
    for user in users:
        if not isinstance(user, dict):
            continue
        department = _first_non_empty_string(
            ", ".join(_string_list(user.get("department_names"))),
            _first_non_empty_string(_department_name_from_ids(user.get("department_id"))),
        )
        user_name = _first_non_empty_string(
            user.get("name"),
            user.get("english_name"),
            user.get("en_name"),
            user.get("open_id"),
        )
        leader = _first_non_empty_string(user.get("leader"), user.get("manager"), user.get("leader_name"), user.get("manager_name"))
        if user_name:
            rows.append([department, user_name, leader or ""])
    return rows


def _query_rows_from_payload(payload: dict[str, Any]) -> list[list[str]]:
    rows_source = _query_rows_source(payload)
    if not isinstance(rows_source, list):
        return []

    query_fields = _query_fields_from_payload(payload)
    field_names = [field["name"] for field in query_fields] if query_fields else []
    single_column_mode = len(field_names) <= 1

    rows: list[list[str]] = []
    for item in rows_source:
        if isinstance(item, list):
            row_values = [_query_cell_to_text(value) for value in item]
            row_values = [value for value in row_values if value]
            if not row_values:
                continue
            if single_column_mode:
                rows.append([", ".join(row_values)])
            else:
                rows.append(row_values)
            continue
        if isinstance(item, dict):
            fields = item.get("fields")
            if isinstance(fields, dict):
                if field_names:
                    row_values = [_query_cell_to_text(fields.get(name)) for name in field_names]
                else:
                    row_values = [_query_cell_to_text(fields.get(name)) for name in fields]
                row_values = [value for value in row_values if value]
                if row_values:
                    rows.append([", ".join(row_values)] if single_column_mode else row_values)
                continue
            if isinstance(item.get("name"), (str, int, float, bool)):
                row_text = _query_cell_to_text(item.get("name"))
                if row_text:
                    rows.append([row_text])
                continue
            row_text = ", ".join(
                f"{key}:{_query_cell_to_text(value)}" for key, value in item.items() if _query_cell_to_text(value)
            )
            if row_text:
                rows.append([row_text] if single_column_mode else [_query_cell_to_text(v) for v in item.values() if _query_cell_to_text(v)])
            continue
        row_text = _query_cell_to_text(item)
        if row_text:
            rows.append([row_text])

    return rows


def _query_rows_source(payload: dict[str, Any]) -> Any:
    candidates: list[Any] = [
        _extract_from_dict(payload, ("data", "items")),
        _extract_from_dict(payload, ("items",)),
        payload.get("records"),
        _extract_from_dict(payload, ("data", "records")),
        payload.get("rows"),
        _extract_from_dict(payload, ("data", "rows")),
    ]
    return next((item for item in candidates if isinstance(item, list)), None)


def _query_fields_from_payload(payload: dict[str, Any]) -> list[dict[str, str]] | None:
    rows_source = _query_rows_source(payload)
    if not isinstance(rows_source, list):
        return None
    for item in rows_source:
        if not isinstance(item, dict):
            continue
        fields = item.get("fields")
        if not isinstance(fields, dict):
            continue
        names = [name for name in fields.keys() if isinstance(name, str) and name.strip()]
        if names:
            return [{"name": name.strip(), "type": "text"} for name in names]
    return None


def _query_cell_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return ", ".join(_query_cell_to_text(item) for item in value if _query_cell_to_text(item))
    if isinstance(value, dict):
        if isinstance(value.get("name"), (str, int, float, bool)):
            return _query_cell_to_text(value.get("name"))
        if isinstance(value.get("text"), (str, int, float, bool)):
            return _query_cell_to_text(value.get("text"))
    return str(value)


def _department_name_from_ids(value: Any) -> str:
    if isinstance(value, list):
        names = [str(item) for item in value if str(item).strip()]
        return ", ".join(names)
    if value is None:
        return ""
    return str(value)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _first_non_empty_string(*candidates: Any) -> str | None:
    for value in candidates:
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
        elif value is not None:
            text = str(value).strip()
            if text:
                return text
    return None


def _extract_from_dict(payload: Any, path: tuple[str, ...]) -> Any:
    value: Any = payload
    for segment in path:
        if isinstance(value, dict) and segment in value:
            value = value[segment]
        else:
            return None
    return value


def _parse_json_if_any(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text or (not text.startswith("{") and not text.startswith("[")):
        return None
    try:
        payload = json.loads(text)
    except Exception:
        return None
    if isinstance(payload, dict):
        return payload
    return None




def _tool_result_response_text(tool_result) -> str:
    structured = tool_result.structured_result if isinstance(tool_result.structured_result, dict) else {}
    response_text = str(structured.get("response_text") or tool_result.answer or "")
    actions = structured.get("authorization_actions")
    if structured.get("user_identity_authorization_required") is True and isinstance(actions, list):
        return _authorization_required_response(response_text, actions)
    return response_text


def _authorization_required_response(response_text: str, actions: list[Any]) -> str:
    lines = [response_text.strip()] if response_text.strip() else []
    lines.extend(
        [
            "",
            "授权入口：",
        ]
    )
    for item in actions:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "授权入口")
        url = str(item.get("url") or "").strip()
        instruction = str(item.get("instruction") or "").strip()
        if url and instruction:
            lines.append(f"- {label}: {url}\n  {instruction}")
        elif url:
            lines.append(f"- {label}: {url}")
        elif instruction:
            lines.append(f"- {label}: {instruction}")
    lines.append("授权完成后，再问同一个问题，我会只按你本人授权的资源范围读取。")
    return "\n".join(lines).strip()


def _compose_planned_tool_answer(tool_answers: list[tuple[str, str, str, str | None]]) -> str:
    answered = [(tool_name, answer) for tool_name, answer, _status, _plan_stop_reason in tool_answers if answer]
    if not answered:
        return ""
    if len(answered) == 1:
        return answered[0][1]
    return "\n\n".join(f"{answer_route_label(tool_name)}\n{answer}" for tool_name, answer in answered)


def _write_tool_policy_result(
    tool_name: str,
    *,
    allow_write_tools: bool,
    require_write_confirmation: bool,
) -> dict[str, Any] | None:
    definition = TOOL_REGISTRY.get(tool_name)
    if definition is None or not definition.supports_write:
        return None
    metadata = {
        "provider": definition.provider.value,
        "required_permissions": list(definition.required_permissions),
        "supports_write": True,
        "allow_write_tools": allow_write_tools,
        "require_write_confirmation": require_write_confirmation,
        "requires_dry_run": allow_write_tools,
        "confirmed_execution_requires": _write_confirmation_requirements(allow_write_tools=allow_write_tools),
    }
    if not allow_write_tools:
        return {
            "status": "denied",
            "answer": "公司级 Agent 设置当前禁止执行写工具。请在后台开启写工具后，再通过 dry-run 和确认令牌执行。",
            "metadata": {**metadata, "policy_reason": "write_tools_disabled"},
        }
    if require_write_confirmation:
        return {
            "status": "pending_confirmation",
            "answer": "这是写操作，必须先执行 dry-run，确认写目标和参数后，再带回 confirmation_token 执行。",
            "metadata": {**metadata, "policy_reason": "write_confirmation_required"},
        }
    return None


def _write_confirmation_requirements(*, allow_write_tools: bool) -> list[str]:
    if not allow_write_tools:
        return []
    return ["dry_run=true", "confirmed=true", "confirmation_token"]


def agent_actor_context(*, company_id: UUID, actor: BotActor, route_scope: str, route_path: str) -> dict[str, Any]:
    identity_keys = [
        key
        for key, value in {
            "display_name": actor.display_name,
            "open_id": actor.open_id,
            "email": actor.email,
        }.items()
        if value
    ]
    data_access_scope = _trace_data_access_scope(route_scope=route_scope, route_path=route_path)
    role_scope = {
        "role": actor.role,
        "access_scope": actor.access_scope,
        "domains": list(actor.domains),
        "company_data_allowed": actor.can_query_company,
    }
    return {
        "role": actor.role,
        "access_scope": actor.access_scope,
        "domains": list(actor.domains),
        "display_name": actor.display_name,
        "open_id": actor.open_id,
        "identity_keys": identity_keys,
        "has_strong_identity": bool(actor.open_id or actor.email),
        "data_access_scope": data_access_scope,
        "personal_owner_open_id": actor.open_id if data_access_scope == "personal" else None,
        "enterprise_identity": "app_identity",
        "enterprise_identity_constraints": list(ENTERPRISE_IDENTITY_CONSTRAINTS),
        "enterprise_resource_boundary": {
            "identity": "app_identity",
            "resource_owner": ENTERPRISE_IDENTITY_RESOURCE_OWNER,
            "constraints": list(ENTERPRISE_IDENTITY_CONSTRAINTS),
            "company_scope": str(company_id),
            "role_scope": role_scope,
            "can_exceed_feishu_app_permissions": False,
        },
        "user_identity": "resource_owner_identity" if data_access_scope == "personal" else None,
        "user_identity_constraints": list(USER_IDENTITY_CONSTRAINTS) if data_access_scope == "personal" else [],
        "user_identity_supported_resources": list(USER_IDENTITY_RESOURCES),
        "data_boundary_policy": DATA_BOUNDARY_POLICY,
        "digital_advisor_permission_policy": DIGITAL_ADVISOR_PERMISSION_POLICY,
        "cannot_escalate_original_permissions": True,
        "company_data_allowed": actor.can_query_company,
        "cross_user_data_allowed": actor.can_query_company and data_access_scope == "company",
        "final_answer_owner": "agent_runtime",
    }


def agent_identity_context(
    *,
    company_id: UUID,
    actor: BotActor,
    route_scope: str,
    route_path: str,
) -> dict[str, Any]:
    owner_key = actor.open_id or actor.email or actor.display_name or actor.role or "unknown"
    data_access_scope = _trace_data_access_scope(route_scope=route_scope, route_path=route_path)
    user_identity_required = data_access_scope == "personal"
    role_scope = {
        "role": actor.role,
        "access_scope": actor.access_scope,
        "domains": list(actor.domains),
        "company_data_allowed": actor.can_query_company,
    }
    return {
        "agent_type": "employee_personal_agent",
        "agent_id": f"{company_id}:{owner_key}",
        "company_id": str(company_id),
        "agent_owner_open_id": actor.open_id,
        "agent_owner_email": actor.email,
        "agent_owner_display_name": actor.display_name,
        "entrypoint": "feishu_bot",
        "tool_access_policy": TOOL_ACCESS_POLICY,
        "tool_sharing_model": TOOL_SHARING_MODEL,
        "shared_business_tools": ["ApprovalTool","KnowledgeTool","BitableTool","ChatTool","CalendarTool","MeetingTool","ReportTool","AutomationTool","PeopleTool"],
        "shared_business_tool_count": SHARED_TOOL_COUNT,
        "agent_can_call_all_business_tools": True,
        "data_permission_model": DATA_PERMISSION_MODEL,
        "data_access_scope": data_access_scope,
        "identity_permission_contract": identity_permission_contract(user_identity_required=user_identity_required),
        "enterprise_identity": "app_identity",
        "enterprise_identity_constraints": list(ENTERPRISE_IDENTITY_CONSTRAINTS),
        "enterprise_resource_boundary": {
            "identity": "app_identity",
            "resource_owner": ENTERPRISE_IDENTITY_RESOURCE_OWNER,
            "constraints": list(ENTERPRISE_IDENTITY_CONSTRAINTS),
            "company_scope": str(company_id),
            "role_scope": role_scope,
            "can_exceed_feishu_app_permissions": False,
        },
        "user_identity": "resource_owner_identity" if user_identity_required else None,
        "user_identity_constraints": list(USER_IDENTITY_CONSTRAINTS) if user_identity_required else [],
        "user_identity_supported_resources": list(USER_IDENTITY_RESOURCES),
        "user_resource_boundary": {
            "identity": "resource_owner_identity",
            "supported_resources": list(USER_IDENTITY_RESOURCES),
            "constraints": list(USER_IDENTITY_CONSTRAINTS),
            "can_exceed_original_authorization": False,
        },
        "data_boundary_policy": DATA_BOUNDARY_POLICY,
        "digital_advisor_permission_policy": DIGITAL_ADVISOR_PERMISSION_POLICY,
        "cannot_escalate_original_permissions": True,
        "final_answer_owner": "agent_runtime",
    }


def _trace_data_access_scope(*, route_scope: str, route_path: str) -> str:
    # Only mail fundamentally requires user authorization (Bot cannot access directly)
    if route_path in {"mail_qa"} or route_path.startswith("feishu_mail_"):
        return "personal"
    # Everything else is enterprise-level data accessible via bot identity
    if route_scope in {"company", "domain", "chat", "none", "personal", "self", "user"}:
        return route_scope
    return "chat"


def _trace(
    semantic,
    route,
    company_id: UUID,
    actor: BotActor,
    scope_label: str,
    route_label: str,
    execution_category: str,
    execution_category_source: str,
    reply_mode: AgentReplyMode,
    steps: list[AgentExecutionStep],
    *,
    memory_context: dict[str, Any] | None = None,
) -> AgentRuntimeTrace:
    return AgentRuntimeTrace(
        semantic_intent=_semantic_trace_name(semantic),
        route_path=route.path,
        route_scope=route.scope,
        route_reason=route.reason,
        execution_category=execution_category,
        execution_category_source=execution_category_source,
        scope_label=scope_label,
        route_label=route_label,
        agent_identity=agent_identity_context(
            company_id=company_id,
            actor=actor,
            route_scope=route.scope,
            route_path=route.path,
        ),
        actor_context=agent_actor_context(company_id=company_id, actor=actor, route_scope=route.scope, route_path=route.path),
        memory_context=_trace_memory_context(
            memory_context or _empty_memory_context(route_scope=route.scope, route_path=route.path),
        ),
        reply_mode=reply_mode_payload(reply_mode),
        thinking_preview=thinking_preview_payload(
            route_label=route_label,
            scope_label=scope_label,
            reply_mode=reply_mode,
        ),
        steps=tuple(steps),
    )


def _semantic_trace_name(semantic) -> str:
    return getattr(semantic, "name", None) or getattr(semantic, "source", None) or "semantic"


def _semantic_normalized_command(module_hint: str | None, normalized_command: str) -> str:
    return f"module:{module_hint} {normalized_command}" if module_hint else normalized_command


def _route_with_semantic_hint(route, route_hint: str | None):
    if not route_hint or route.denied:
        return route
    route_definition = TOOL_REGISTRY.get(route.path)
    hint_definition = TOOL_REGISTRY.get(route_hint)
    if route_definition and route_definition.supports_write and not (hint_definition and hint_definition.supports_write):
        return route
    allowed_by_scope = {
        "company": {
            "company_qa",
                  "feishu_approval_task_query",
           "calendar_qa",
           "feishu_calendar_create_event",
           "feishu_vc_meeting_search",
           "feishu_contact_organization_snapshot",
   "feishu_contact_department_users",
   "feishu_contact_user_search",
           "mail_qa",
           "bitable_qa",
           "personal_tasks",
           "general_chat",
           "feishu_task_create",
       },
       "domain": {
           "domain_qa",
                  "feishu_approval_task_query",
           "calendar_qa",
           "feishu_calendar_create_event",
           "feishu_vc_meeting_search",
           "feishu_contact_organization_snapshot",
   "feishu_contact_department_users",
   "feishu_contact_user_search",
           "bitable_qa",
           "personal_tasks",
           "general_chat",
            "feishu_task_create",
        },
        "personal": {
            "calendar_qa",
            "feishu_calendar_create_event",
            "feishu_vc_meeting_search",
            "feishu_contact_organization_snapshot",
   "feishu_contact_department_users",
   "feishu_contact_user_search",
            "mail_qa",
            "personal_tasks",
            "general_chat",
            "feishu_task_create",
        },
        "chat": {"chat_qa", "chat_summary", "chat_tasks", "personal_tasks", "public_knowledge_qa", "general_chat"},
    }
    if route_hint in allowed_by_scope.get(route.scope, set()):
        return type(route)(path=route_hint, scope=route.scope, message=route.message, reason="semantic_hint")
    return route


def _agent_memory_context(
    *,
    db: Session | None,
    company_id: UUID,
    actor: BotActor,
    chat_id: str | None,
    route_scope: str,
    route_path: str,
) -> dict[str, Any]:
    data_access_scope = _trace_data_access_scope(route_scope=route_scope, route_path=route_path)
    if db is None or not callable(getattr(db, "scalars", None)):
        return _empty_memory_context(route_scope=route_scope, route_path=route_path)
    context = load_bot_user_context(
        db,
        company_id=company_id,
        actor=actor,
        chat_id=chat_id,
        scope=data_access_scope,
    )
    facts = [
        {
            "fact_type": getattr(fact, "fact_type", None),
            "subject": getattr(fact, "subject", None),
            "confidence": getattr(fact, "confidence", None),
            "scope": getattr(fact, "scope", None),
            "content": getattr(fact, "content", None),
        }
        for fact in context.memory_facts
    ]
    return {
        "source": "memory",
        "scope": data_access_scope,
        "company_id": str(company_id),
        "user_open_id": actor.open_id,
        "chat_id": chat_id if data_access_scope == "chat" else None,
        "pre_llm_filtered": True,
        "data_permission_model": DATA_PERMISSION_MODEL,
        "fact_count": len(facts),
        "fact_types": sorted({str(item["fact_type"]) for item in facts if item.get("fact_type")}),
        "subjects": [str(item["subject"]) for item in facts if item.get("subject")][:6],
        "favorite_modules": list(context.favorite_modules or [])[:6],
        "content_in_trace": False,
        "_facts": facts,
    }


def _empty_memory_context(*, route_scope: str, route_path: str) -> dict[str, Any]:
    return {
        "source": "memory",
        "scope": _trace_data_access_scope(route_scope=route_scope, route_path=route_path),
        "pre_llm_filtered": True,
        "data_permission_model": DATA_PERMISSION_MODEL,
        "fact_count": 0,
        "fact_types": [],
        "subjects": [],
        "favorite_modules": [],
        "content_in_trace": False,
    }


def _agent_memory_context_hint(memory_context: dict[str, Any]) -> str:
    facts = memory_context.get("_facts") if isinstance(memory_context.get("_facts"), list) else []
    visible_facts = [
        item
        for item in facts
        if isinstance(item, dict) and str(item.get("content") or "").strip()
    ][:4]
    favorite_modules = memory_context.get("favorite_modules") if isinstance(memory_context.get("favorite_modules"), list) else []
    parts = []
    if favorite_modules:
        parts.append(f"用户常用模块：{'、'.join(str(item) for item in favorite_modules[:4])}。")
    if visible_facts:
        fact_text = "；".join(
            f"{item.get('fact_type') or 'memory'}:{str(item.get('content') or '').strip()[:160]}"
            for item in visible_facts
        )
        parts.append(f"长期记忆（已按 company_id/open_id/chat_id 权限过滤）：{fact_text}。")
    if not parts:
        return ""
    return " ".join(parts)


def _trace_memory_context(memory_context: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in memory_context.items() if key != "_facts"}


def _finalize_answer(
    answer: str,
    *,
    db: Session | None,
    company_id: UUID,
    question: str,
    actor: BotActor,
    scope_label: str,
    route_label: str,
    reply_mode: AgentReplyMode,
    memory_context_hint: str | None = None,
    chat_id: str | None = None,
) -> str:
    scoped = with_scope_label(answer, scope_label, route_label=route_label, reply_mode=reply_mode)
    style_hint = " ".join(
        item
        for item in (
            bot_answer_style(db, company_id=company_id, actor=actor),
            reply_mode.style_hint,
            memory_context_hint,
        )
        if item
    )
    result = rewrite_bot_answer(
        question=question,
        answer=scoped,
        actor=actor,
        scope_label=scope_label,
        route_label=route_label,
        style_override=style_hint,
        chat_id=chat_id,
        is_casual=route_label in {"general_chat", "基础沟通", "引导式对话", "clarification"},
    )
    # Save answer to session context (Redis) so follow-up queries can reference actual data
    if chat_id:
        import json as _rj
        import re as _re
        from app.core.config import settings as _settings
        import redis as _redis
        if _settings.feishu_bot_runtime_v5_enabled:
            return result
        try:
            _rc = _redis.Redis.from_url(_settings.redis_url, decode_responses=True)
            _psection = scoped.split('人员列表')[-1] if '人员列表' in scoped else scoped
            _name_list = None
            if _psection != scoped:
                _all_names = _re.findall(r'^\s{2,}(?:\U0001f464\s*)?([\u4e00-\u9fff\w]+)', _psection, _re.MULTILINE)
                if _all_names:
                    _name_list = '、'.join(_all_names)
            _data = {'question': question, 'answer': scoped[:3000]}
            if _name_list:
                _data['name_list'] = _name_list
            _rc.setex(f'feishu:result:{chat_id}', 600, _rj.dumps(_data, ensure_ascii=False))
        except Exception:
            pass
    return result


def answer_scope_label(*, actor: BotActor, route_scope: str, chat_id: str | None = None) -> str:
    if actor.role == "owner":
        return "全部公司" if actor.access_scope == "all" else "指定公司"

    if actor.role == "admin":
        if actor.access_scope in {"company", "all"} or route_scope == "company":
            return "授权公司"
        if actor.access_scope in {"department", "project"}:
            return "授权部门"
        if actor.access_scope == "domain" or route_scope == "domain":
            return "授权业务域"

    if actor.role in {"manager", "lead"}:
        if actor.access_scope in {"department", "project"}:
            return "授权部门"
        if actor.access_scope == "domain" or route_scope == "domain":
            return "授权业务域"
        if actor.access_scope in {"company", "all"} or route_scope == "company":
            return "授权公司"

    if actor.access_scope in {"personal", "self", "user"}:
        return "本人相关"
    if actor.access_scope == "domain" or route_scope == "domain":
        return "授权业务域"
    return "当前群" if chat_id else "当前会话"


def answer_route_label(path: str) -> str:
    labels = {
        "owner_cockpit": "老板驾驶舱",
        "company_qa": "公司级问答",
        "calendar_qa": "日程问答",
        "feishu_calendar_create_event": "创建日程",
        "feishu_vc_meeting_search": "会议记录查询",
        "chat_summary": "当前群总结",
        "chat_tasks": "当前群待办",
       "personal_tasks": "本人相关事项",
       "domain_qa": "授权业务域问答",
               "feishu_approval_task_query": "审批实时待办",
       "feishu_approval_task_approve": "审批通过",
       "feishu_approval_task_reject": "审批拒绝",
       "feishu_contact_organization_snapshot": "组织架构问答",
        "feishu_task_create": "创建任务",
        "bitable_qa": "多维表格问答",
        "public_knowledge_qa": "公开知识问答",
        "chat_qa": "当前会话问答",
        "general_chat": "基础沟通",
        "mail_qa": "邮件问答",
        "task_qa": "任务问答",
        "deny": "权限拦截",
    }
    return labels.get(path, path)


def with_scope_label(
    answer: str,
    scope_label: str,
    *,
    route_label: str | None = None,
    reply_mode: AgentReplyMode | None = None,
) -> str:
    if route_label == "基础沟通":
        return answer
    old_prefix = f"回答范围：{scope_label}"
    new_prefix = f"范围：{scope_label}"
    if answer.startswith(old_prefix) or answer.startswith(new_prefix):
        return answer
    capability = f"｜{route_label}" if route_label else ""
    header = f"{new_prefix}{capability}"
    thinking_map = thinking_map_text(route_label=route_label or "", scope_label=scope_label, reply_mode=reply_mode) if reply_mode else ""
    if thinking_map:
        return f"{header}\n{thinking_map}\n\n{answer}"
    return f"{header}\n{answer}"


def _resolve_skill_hint(*, semantic: Any) -> str | None:
    route = getattr(semantic, "route_hint", None)
    if not route: return None
    if route.startswith("feishu_im_"): return "im"
    if route.startswith("feishu_mail_") or route == "mail_qa": return "mail"
    if route.startswith("feishu_calendar_") or route == "calendar_qa": return "calendar"
    if route.startswith("feishu_approval_") : return "approval"
    if route.startswith("feishu_task_") or route in ("task_qa","personal_tasks"): return "task"
    if route.startswith("feishu_bitable_") or route == "bitable_qa": return "bitable"
    if route.startswith("feishu_contact_"): return "contact"
    if route.startswith("feishu_drive_"): return "drive"
    if route.startswith("feishu_vc_"): return "meeting"
    if route.startswith("feishu_okr_"): return "okr"
    if route.startswith("feishu_wiki_"): return "wiki"
    if route.startswith("feishu_doc_"): return "doc"
    if route.startswith("feishu_cli_"): return "cli_diagnose"
    if route.startswith("chat_") or route == "general_chat": return "chat"
    if route in ("company_qa","owner_cockpit"): return "report"
    if route == "public_knowledge_qa": return "knowledge"
    return None
