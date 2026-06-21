from __future__ import annotations

from dataclasses import replace
from time import perf_counter
from typing import Protocol
from uuid import uuid4
from uuid import UUID

from app.services.runtime_v5.capabilities import capability_for
from app.services.runtime_v5.models import (
    ExecutionResult,
    IntentResult,
    PermissionDecision,
    PlannerResult,
    ProviderRequest,
    ProviderResult,
    ResultContext,
    RuntimeContext,
)
from app.services.runtime_v5.provider_snapshot import build_runtime_provider_snapshot, provider_snapshot_operation


class ResourceProvider(Protocol):
    source: str

    def execute(self, request: ProviderRequest) -> ProviderResult:
        ...


class CapabilityRouter:
    def __init__(self, providers: dict[str, ResourceProvider] | None = None) -> None:
        self.providers = providers or {}
        self.provider_snapshot = build_runtime_provider_snapshot(self.providers)

    def execute(
        self,
        *,
        context: RuntimeContext,
        intent: IntentResult,
        plan: PlannerResult,
        permission: PermissionDecision,
    ) -> ExecutionResult:
        if not permission.allowed:
            return ExecutionResult(
                strategy=plan.strategy,
                status="denied",
                provider_results=(
                    ProviderResult(source="permission", status="denied", error=permission.reason),
                ),
            )

        provider_results: list[ProviderResult] = []
        for source in plan.sources:
            provider = self.providers.get(source)
            if provider is None:
                provider_results.append(
                    _missing_provider_result(
                        strategy=plan.strategy,
                        source=source,
                        operation=_operation_for_source(plan.strategy, source),
                    )
                )
                continue
            provider_results.extend(
                self._execute_source(
                    provider=provider,
                    source=source,
                    context=context,
                    intent=intent,
                    plan=plan,
                    permission=permission,
                    previous_results=tuple(provider_results),
                )
            )

        result_context = _result_context_from_provider_results(plan, intent, provider_results)
        status = _execution_status(provider_results)
        return ExecutionResult(
            strategy=plan.strategy,
            status=status,
            provider_results=tuple(provider_results),
            result_context=result_context,
        )

    def _execute_source(
        self,
        *,
        provider: ResourceProvider,
        source: str,
        context: RuntimeContext,
        intent: IntentResult,
        plan: PlannerResult,
        permission: PermissionDecision,
        previous_results: tuple[ProviderResult, ...],
    ) -> tuple[ProviderResult, ...]:
        company_ids = _company_ids_for_execution(context)
        if len(company_ids) <= 1:
            request = _provider_request(
                source=source,
                context=context,
                intent=intent,
                plan=plan,
                permission=permission,
                previous_results=previous_results,
            )
            governance_result = _provider_governance_result(snapshot=self.provider_snapshot, request=request)
            if governance_result is not None:
                return (governance_result,)
            return (
                _execute_provider_request(provider=provider, request=request),
            )

        results: list[ProviderResult] = []
        for company_id in company_ids:
            company_context = context.for_company(company_id)
            request = _provider_request(
                source=source,
                context=company_context,
                intent=intent,
                plan=plan,
                permission=permission,
                previous_results=previous_results,
            )
            governance_result = _provider_governance_result(snapshot=self.provider_snapshot, request=request)
            result = governance_result if governance_result is not None else _execute_provider_request(provider=provider, request=request)
            results.append(
                replace(
                    result,
                    items=tuple(
                        {
                            **item,
                            "company_id": str(company_id),
                            "scope_type": context.runtime_scope.scope_type,
                        }
                        for item in result.items
                    ),
                    metadata={
                        **result.metadata,
                        "company_id": str(company_id),
                        "scope_type": context.runtime_scope.scope_type,
                    },
                )
            )
        return tuple(results)


def _provider_governance_result(*, snapshot: dict[str, object], request: ProviderRequest) -> ProviderResult | None:
    operation_state = provider_snapshot_operation(snapshot, source=request.source, operation=request.operation)
    if operation_state.get("ready"):
        return None

    status = str(operation_state.get("status") or "provider_unavailable")
    label = _provider_label(request.source)
    detail = {
        "provider_missing": f"{label} Provider 尚未注册。",
        "provider_disabled": f"{label} Provider 当前未启用。",
        "provider_unhealthy": f"{label} Provider 当前健康状态异常：{operation_state.get('health') or 'unknown'}。",
        "operation_not_covered": f"{label} Provider 暂不覆盖这个操作：{request.operation}。",
        "operation_disabled": f"{label} Provider 已声明「{request.operation}」，但该操作尚未启用。",
    }.get(status, f"{label} Provider 当前不可用。")
    next_step = {
        "provider_missing": "先注册该 Provider，或让 Planner 路由到已注册来源。",
        "provider_disabled": "先启用该 Provider，或让 Planner 路由到已启用来源。",
        "provider_unhealthy": "先修复 Provider 健康状态，再执行该来源能力。",
        "operation_not_covered": "先补 Provider 能力覆盖，或调整 Planner 的来源/操作映射。",
        "operation_disabled": "先接入或启用该 Provider 操作，再开放给 Runtime 调用。",
    }.get(status, "先修复 Runtime Provider Snapshot 中的不可用项。")
    return _provider_governance_blocked_result(
        request=request,
        error_type=status,
        detail=detail,
        next_step=next_step,
        extra_metadata={
            "runtime_provider_snapshot": True,
            "provider_snapshot_status": status,
            "available_operations": operation_state.get("available_operations", []),
        },
    )


def _provider_governance_blocked_result(
    *,
    request: ProviderRequest,
    error_type: str,
    detail: str,
    next_step: str,
    extra_metadata: dict[str, object] | None = None,
) -> ProviderResult:
    return ProviderResult(
        source=request.source,
        status="error",
        result_type=f"{request.planner.strategy}_{error_type}",
        count=0,
        metadata={
            "strategy": request.planner.strategy,
            "source": request.source,
            "operation": request.operation,
            "error_type": error_type,
            "provider_governance": True,
            "v5_only": True,
            "legacy_fallback": False,
            "recommended_next_step": next_step,
            **(extra_metadata or {}),
        },
        answer=f"{detail}\n{next_step}",
        error=error_type,
    )


def _execute_provider_request(*, provider: ResourceProvider, request: ProviderRequest) -> ProviderResult:
    started = perf_counter()
    result = provider.execute(request)
    duration_ms = int((perf_counter() - started) * 1000)
    return replace(
        result,
        metadata={
            **result.metadata,
            "source": request.source,
            "operation": result.metadata.get("operation") if isinstance(result.metadata, dict) and result.metadata.get("operation") else request.operation,
            "duration_ms": duration_ms,
        },
    )


def _provider_request(
    *,
    source: str,
    context: RuntimeContext,
    intent: IntentResult,
    plan: PlannerResult,
    permission: PermissionDecision,
    previous_results: tuple[ProviderResult, ...],
) -> ProviderRequest:
    params = dict(intent.entities)
    params["previous_results"] = previous_results + _result_context_previous_results(context.result_context)
    capability = capability_for(plan.strategy, source)
    if capability is not None:
        params.setdefault("capability_label", capability.label)
        params.setdefault("capability_installed", capability.installed)
    return ProviderRequest(
        source=source,
        operation=_operation_for_source(plan.strategy, source),
        intent=intent,
        planner=plan,
        context=context,
        execution_identity=permission.execution_identity,
        params=params,
    )


def _operation_for_source(strategy: str, source: str) -> str:
    operations = {
        ("approval_query", "approval"): "list_pending",
        ("approval_detail", "approval"): "get_detail",
        ("approval_approve", "approval"): "approve",
        ("approval_reject", "approval"): "reject",
        ("approval_transfer", "approval"): "transfer",
        ("approval_add_sign", "approval"): "add_sign",
        ("approval_rollback", "approval"): "rollback",
        ("approval_remind", "approval"): "remind",
        ("approval_cancel", "approval"): "cancel",
        ("approval_cc", "approval"): "cc",
        ("approval_initiated", "approval"): "list_initiated",
        ("people_lookup", "people"): "search_person",
        ("department_members", "people"): "list_department_members",
        ("organization_snapshot", "people"): "get_org_snapshot",
        ("organization_export", "people"): "get_org_snapshot",
        ("organization_export", "base"): "write_records",
        ("organization_export", "im"): "send_result",
        ("task_query", "task"): "list_my_tasks",
        ("task_search", "task"): "search_tasks",
        ("task_create", "task"): "create_task",
        ("task_complete", "task"): "complete_task",
        ("task_update", "task"): "update_task",
        ("task_reopen", "task"): "reopen_task",
        ("task_delete", "task"): "delete_task",
        ("task_subtask_create", "task"): "create_subtask",
        ("task_comment", "task"): "comment_task",
        ("task_assign_members", "task"): "assign_members",
        ("task_update_followers", "task"): "update_followers",
        ("task_update_reminders", "task"): "update_reminders",
        ("task_upload_attachment", "task"): "upload_attachment",
        ("task_add_to_tasklist", "task"): "add_to_tasklist",
        ("task_set_ancestor", "task"): "set_ancestor",
        ("task_clear_ancestor", "task"): "clear_ancestor",
        ("tasklist_create", "task"): "tasklist_create",
        ("tasklist_update", "task"): "tasklist_update",
        ("tasklist_delete", "task"): "tasklist_delete",
        ("tasklist_update_members", "task"): "tasklist_update_members",
        ("tasklist_set_members", "task"): "tasklist_set_members",
        ("task_section_create", "task"): "section_create",
        ("task_section_update", "task"): "section_update",
        ("task_section_delete", "task"): "section_delete",
        ("calendar_query", "calendar"): "list_events",
        ("calendar_create", "calendar"): "create_event",
        ("mail_query", "mail"): "list_recent",
        ("mail_search", "mail"): "search_messages",
        ("mail_get_message", "mail"): "get_message",
        ("mail_draft_create", "mail"): "create_draft",
        ("message_send", "im"): "send_message",
        ("chat_search", "im"): "search_chats",
        ("message_query", "im"): "list_messages",
        ("chat_create", "im"): "create_chat",
        ("chat_auto_join_public", "im"): "auto_join_public_chats",
        ("docs_read", "docs"): "read_doc",
        ("docs_edit", "docs"): "edit_doc",
        ("wiki_search", "wiki"): "search_wiki",
        ("drive_list", "drive"): "list_files",
        ("drive_upload", "drive"): "upload_file",
        ("sheets_write", "sheets"): "write_cells",
        ("vc_meeting_search", "vc"): "search_meetings",
        ("minutes_read", "minutes"): "read_minutes",
        ("attendance_query", "attendance"): "query_records",
        ("okr_query", "okr"): "list_objectives",
        ("okr_update", "okr"): "update_progress",
        ("slides_read", "slides"): "read_slides",
        ("slides_write", "slides"): "write_slides",
        ("whiteboard_read", "whiteboard"): "read_whiteboard",
        ("whiteboard_write", "whiteboard"): "write_whiteboard",
        ("vc_agent_read", "vc_agent"): "read_live_events",
        ("vc_agent_join", "vc_agent"): "join_meeting",
        ("company_intro", "company_profile"): "read_profile",
        ("company_intro", "knowledge"): "search",
        ("company_intro", "workevent"): "summarize",
        ("company_intro", "web"): "search",
        ("risk_analysis", "workevent"): "risk_events",
        ("risk_analysis", "memory"): "related_memory",
        ("risk_analysis", "knowledge"): "risk_policy",
        ("general_analysis", "workevent"): "summarize",
        ("general_analysis", "memory"): "related_memory",
        ("general_analysis", "knowledge"): "search",
        ("decision_advice", "workevent"): "summarize",
        ("decision_advice", "memory"): "related_memory",
        ("decision_advice", "knowledge"): "search",
        ("general_query", "knowledge"): "search",
    }
    mapped = operations.get((strategy, source))
    if mapped:
        return mapped
    capability = capability_for(strategy, source)
    return capability.operation if capability is not None else "execute"


def _result_context_previous_results(result_context: ResultContext | None) -> tuple[ProviderResult, ...]:
    if result_context is None or not result_context.items:
        return ()
    metadata = result_context.metadata if isinstance(result_context.metadata, dict) else {}
    if str(metadata.get("context_kind") or "") == "action_receipt":
        return ()
    grouped: dict[str, list[dict]] = {}
    for item in result_context.items:
        source = str(item.get("_runtime_v5_source") or "").strip()
        if source:
            grouped.setdefault(source, []).append(item)
    if not grouped:
        source = _source_for_result_context(result_context)
        grouped[source] = list(result_context.items)
    results = []
    for source, items in grouped.items():
        results.append(ProviderResult(
            source=source,
            status="success",
            result_type=result_context.result_type,
            count=len(items),
            items=tuple(items),
            metadata={
                **metadata,
                "from_result_context": True,
                "result_context_source": source,
            },
            answer="",
        ))
    return tuple(results)


def _source_for_result_context(result_context: ResultContext) -> str:
    metadata = result_context.metadata if isinstance(result_context.metadata, dict) else {}
    sources = metadata.get("result_sources") or metadata.get("sources")
    if isinstance(sources, list) and sources:
        return str(sources[0])
    result_type = result_context.result_type
    if result_type in {"people_search", "department_members", "organization_snapshot"}:
        return "people"
    if result_type.startswith("approval"):
        return "approval"
    if result_type == "task_list":
        return "task"
    if result_type == "mail_list":
        return "mail"
    if result_type == "calendar_event_list":
        return "calendar"
    if result_type == "docs_read":
        return "docs"
    if result_type in {"wiki_space_list", "wiki_node_list"}:
        return "wiki"
    if result_type == "drive_file_list":
        return "drive"
    if result_type == "vc_meeting_list":
        return "vc"
    if result_type == "attendance_record_list":
        return "attendance"
    if result_type == "okr_objective_list":
        return "okr"
    if result_type == "slides_read":
        return "slides"
    if result_type == "whiteboard_read":
        return "whiteboard"
    return "result_context"


def _missing_provider_result(*, strategy: str, source: str, operation: str = "execute") -> ProviderResult:
    label = _provider_label(source)
    return ProviderResult(
        source=source,
        status="error",
        result_type=f"{strategy}_provider_missing",
        answer=f"该能力尚未接入 V5 Runtime：{label}（source={source}）。",
        error="provider_not_registered",
        metadata={
            "strategy": strategy,
            "source": source,
            "operation": operation,
            "error_type": "provider_not_registered",
            "provider_governance": True,
            "v5_only": True,
            "legacy_fallback": False,
            "recommended_next_step": f"先注册 {label} Provider，或让 Planner 路由到已注册来源。",
        },
    )


def _provider_label(source: str) -> str:
    labels = {
        "people": "通讯录/人员",
        "approval": "审批",
        "calendar": "日历",
        "task": "任务/待办",
        "mail": "邮件",
        "message": "结果发送",
        "im": "飞书消息/群聊",
        "docs": "文档",
        "wiki": "知识库",
        "drive": "云盘",
        "sheets": "电子表格",
        "vc": "飞书会议",
        "minutes": "飞书妙记",
        "attendance": "飞书考勤",
        "okr": "飞书 OKR",
        "slides": "飞书幻灯片",
        "whiteboard": "飞书画板",
        "vc_agent": "飞书会中能力",
        "base": "多维表格",
        "company_profile": "公司档案",
        "knowledge": "企业知识库",
        "workevent": "工作事件",
        "memory": "长期记忆",
        "web": "网页搜索",
    }
    return labels.get(source, source)


def _company_ids_for_execution(context: RuntimeContext) -> tuple[UUID, ...]:
    if context.runtime_scope.scope_type == "single_company":
        return context.runtime_scope.company_ids[:1]
    return context.runtime_scope.company_ids


def _result_context_from_provider_results(
    plan: PlannerResult,
    intent: IntentResult,
    provider_results: list[ProviderResult],
) -> ResultContext | None:
    strategy = plan.strategy
    items: list[dict] = []
    metadata = {
        "strategy": strategy,
        "question_type": intent.question_type,
        "data_scope": intent.data_scope,
        "sources": [],
        "provider_results": [],
    }
    answer_parts: list[str] = []
    result_type = strategy

    for result in provider_results:
        metadata["sources"].append(result.source)
        metadata["provider_results"].append(_provider_result_summary(result))
        items.extend(_tag_items_with_source(result.items, result.source))
        if result.answer:
            answer_parts.append(result.answer)
        if result.result_type:
            result_type = result.result_type

    if not items:
        result_type = _empty_result_type(strategy, provider_results)

    query_id = f"{strategy}:{result_type}:{uuid4().hex[:12]}"
    metadata["query_id"] = query_id
    metadata["result_type"] = result_type
    metadata["item_count"] = len(items)
    metadata["context_kind"] = _result_context_kind(result_type)
    metadata["display_count"] = _result_context_display_count(result_type, provider_results, len(items))
    metadata["actionable"] = _result_type_actionable(result_type) and metadata["context_kind"] != "action_receipt"
    metadata["followup_supported"] = True
    metadata["execution_status"] = _execution_status(provider_results)
    metadata["provider_evidence"] = _provider_evidence_summary(provider_results)
    metadata["result_sources"] = sorted({str(result.source) for result in provider_results if str(result.source or "").strip()})
    metadata["source_execution_steps"] = metadata["provider_results"]
    metadata["source_execution_status"] = {
        "planned_sources": list(plan.sources),
        "executed_sources": metadata["result_sources"],
        "success_sources": sorted({str(result.source) for result in provider_results if result.status == "success"}),
        "error_sources": sorted({str(result.source) for result in provider_results if result.status in {"error", "denied"}}),
        "skipped_sources": sorted({str(result.source) for result in provider_results if result.status == "skipped"}),
    }
    if not items:
        metadata["empty_result"] = True
        metadata["empty_reason"] = _empty_result_reason(provider_results)
        metadata["recommended_next_step"] = _empty_result_next_step(provider_results)
    answer = _result_context_answer(result_type, answer_parts, metadata)
    return ResultContext(
        result_type=result_type,
        query_id=query_id,
        count=len(items),
        items=tuple(items),
        metadata=metadata,
        answer=answer,
    )


def _empty_result_type(strategy: str, provider_results: list[ProviderResult]) -> str:
    for result in reversed(provider_results):
        if result.result_type:
            return result.result_type
    return f"{strategy}_empty"


def _empty_result_reason(provider_results: list[ProviderResult]) -> str:
    if not provider_results:
        return "no_provider_executed"
    if all(result.status == "skipped" for result in provider_results):
        return "all_providers_skipped"
    for result in provider_results:
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        error_type = str(metadata.get("error_type") or "").strip()
        if error_type:
            return error_type
    if any(result.status == "error" for result in provider_results):
        return "provider_error"
    if any(result.status == "denied" for result in provider_results):
        return "permission_denied"
    return "empty_items"


def _empty_result_next_step(provider_results: list[ProviderResult]) -> str:
    for result in provider_results:
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        next_step = str(metadata.get("recommended_next_step") or "").strip()
        if next_step:
            return next_step
        error_type = str(metadata.get("error_type") or "").strip()
        if error_type in {"capability_not_installed", "operation_not_installed", "not_installed"}:
            capability_label = str(metadata.get("capability_label") or "").strip()
            operation = str(metadata.get("operation") or "").strip()
            tool_name = str(metadata.get("tool_name") or "").strip()
            target = capability_label or operation or tool_name or result.source
            return f"请接入或启用「{target}」原子能力后再执行。"
        if error_type in {"operation_not_supported", "not_supported"}:
            operation = str(metadata.get("operation") or "").strip()
            return f"当前 Provider 暂不支持「{operation or result.source}」，需要先补 Provider 实现。"
        missing_params = metadata.get("missing_params")
        if isinstance(missing_params, list) and missing_params:
            return "补充缺少参数后重新发起。"
    return "请补充对象、范围或时间段后重新查询。"


def _provider_result_summary(result: ProviderResult) -> dict:
    result_metadata = result.metadata if isinstance(result.metadata, dict) else {}
    return {
        "source": result.source,
        "status": result.status,
        "result_type": result.result_type,
        "count": result.count,
        "error": result.error,
        "operation": result_metadata.get("operation", ""),
        "tool_name": result_metadata.get("tool_name", ""),
        "error_type": result_metadata.get("error_type", ""),
        "provider_governance": bool(result_metadata.get("provider_governance")),
        "missing_params": result_metadata.get("missing_params", []),
        "duration_ms": int(result_metadata.get("duration_ms") or 0),
        "substeps": result_metadata.get("substeps", []),
        "cache_hit": result_metadata.get("cache_hit"),
        "fetch_ms": result_metadata.get("fetch_ms"),
        "capability_label": result_metadata.get("capability_label", ""),
        "capability_installed": result_metadata.get("capability_installed"),
        "pending_reason": result_metadata.get("pending_reason", ""),
        "recommended_next_step": result_metadata.get("recommended_next_step", ""),
        "available_operations": result_metadata.get("available_operations", []),
        "target": result_metadata.get("target", ""),
        "target_type": result_metadata.get("target_type", ""),
        "target_query": result_metadata.get("target_query", ""),
        "resolved_user_id": result_metadata.get("resolved_user_id", ""),
        "resolved_chat_id": result_metadata.get("resolved_chat_id", ""),
        "resolved_target_name": result_metadata.get("resolved_target_name", ""),
    }


def _provider_evidence_summary(provider_results: list[ProviderResult]) -> dict:
    operations = []
    sources = []
    errors = []
    total_duration_ms = 0
    for result in provider_results:
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        source = str(result.source or "").strip()
        operation = str(metadata.get("operation") or result.result_type or "").strip()
        if source and source not in sources:
            sources.append(source)
        if operation and operation not in operations:
            operations.append(operation)
        total_duration_ms += int(metadata.get("duration_ms") or 0)
        if result.status in {"error", "denied"} or result.error:
            errors.append(
                {
                    "source": source,
                    "operation": operation,
                    "status": result.status,
                    "error": result.error,
                    "error_type": metadata.get("error_type", ""),
                    "provider_governance": bool(metadata.get("provider_governance")),
                }
            )
    return {
        "provider_count": len(provider_results),
        "sources": sources,
        "operations": operations,
        "success_count": len([result for result in provider_results if result.status == "success"]),
        "error_count": len([result for result in provider_results if result.status == "error"]),
        "denied_count": len([result for result in provider_results if result.status == "denied"]),
        "skipped_count": len([result for result in provider_results if result.status == "skipped"]),
        "total_duration_ms": total_duration_ms,
        "errors": errors[:5],
    }


def _result_context_answer(result_type: str, answer_parts: list[str], metadata: dict) -> str:
    if _result_context_kind(result_type) == "action_receipt":
        display_count = metadata.get("display_count") or metadata.get("item_count") or 0
        status = str(metadata.get("execution_status") or "success")
        status_label = {
            "success": "动作已完成",
            "partial": "动作部分完成",
            "error": "动作执行失败",
            "denied": "动作无权限执行",
            "skipped": "动作已跳过",
            "cancelled": "动作已取消",
            "stale": "动作确认已失效",
        }.get(status, "动作已处理")
        return f"{status_label}，结果类型：{result_type}，数量：{display_count}。"
    return "\n\n".join(answer_parts)[:4000]


def _tag_items_with_source(items: tuple[dict, ...], source: str) -> list[dict]:
    tagged = []
    for item in items:
        tagged.append({**item, "_runtime_v5_source": source})
    return tagged


def _execution_status(provider_results: list[ProviderResult]):
    if not provider_results:
        return "skipped"
    if any(item.status == "success" for item in provider_results) and any(
        item.status in {"error", "denied"} for item in provider_results
    ):
        return "partial"
    if any(item.status == "error" for item in provider_results):
        return "error"
    if any(item.status == "denied" for item in provider_results):
        return "denied"
    if any(item.status == "success" for item in provider_results):
        return "success"
    return "skipped"


def _result_type_actionable(result_type: str) -> bool:
    return result_type in {
        "approval_list",
        "approval_detail",
        "people_search",
        "department_members",
        "organization_snapshot",
        "task_list",
        "mail_list",
        "calendar_event_list",
        "docs_read",
        "wiki_space_list",
        "wiki_node_list",
        "drive_file_list",
        "vc_meeting_list",
        "attendance_record_list",
        "okr_objective_list",
        "slides_read",
        "whiteboard_read",
        "chat_list",
        "im_message_list",
    }


def _result_context_kind(result_type: str) -> str:
    if result_type.endswith("_empty") or result_type.endswith("_not_installed") or result_type.endswith("_not_supported"):
        return "no_result"
    if result_type.startswith("approval_") and result_type not in {"approval_list", "approval_detail", "approval_initiated_list"}:
        return "action_receipt"
    if result_type in {
        "base_export",
        "message_send",
        "mail_draft_create",
        "task_create",
        "task_complete",
        "calendar_create",
        "approval_action",
        "runtime_action",
    }:
        return "action_receipt"
    return "query_result"


def _result_context_display_count(result_type: str, provider_results: list[ProviderResult], item_count: int) -> int:
    if _result_context_kind(result_type) != "action_receipt":
        return item_count
    for result in reversed(provider_results):
        if result.status == "success" and result.result_type == result_type and result.count:
            return int(result.count)
    return item_count
