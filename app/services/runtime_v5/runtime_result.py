from __future__ import annotations

from dataclasses import replace
from typing import Any
from urllib.parse import urlencode

from app.core.config import settings
from app.services.runtime_v5.capabilities import label_for_strategy
from app.services.runtime_v5.command_frame import command_frame_payload
from app.services.runtime_v5.models import CommandPlan, ComposedAnswer, ExecutionResult, IntentResult, PermissionDecision, PlannerResult, ResultContext, RuntimeResult, TargetUI
from app.services.runtime_v5.policy_result_filter import apply_policy_result_filter, build_policy_result_filter_payload
from app.services.runtime_v5.response_experience import build_response_experience
from app.services.runtime_v5.response_orchestration import build_response_policy
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
    authorization = _authorization_metadata(
        result_type=result_type,
        result_metadata=result_metadata,
        company_id=company_id,
    )
    policy_result_filter = build_policy_result_filter_payload(
        command_plan=command_plan,
        permission=permission,
        result_context=result_context,
        scope_context=scope_context,
    )
    filtered_items, policy_result_filter = apply_policy_result_filter(
        items=result_context.items if result_context is not None else (),
        filter_payload=policy_result_filter,
    )
    filtered_result_context = replace(result_context, items=filtered_items) if result_context is not None else None
    sidepanel_context = _sidepanel_context_for_result(
        result_type=result_type,
        result_context=filtered_result_context,
        command_plan=command_plan,
    )
    title = label_for_strategy(command_plan.planner_result.strategy) or command_plan.intent
    response_policy = build_response_policy(
        intent=command_plan.intent,
        result_type=result_type,
        data_scope=str(command_plan.intent_result.data_scope or ""),
        answer=_summary_from_composed(composed),
        question_type=str(command_plan.intent_result.question_type or ""),
        requires_confirmation=permission.requires_confirmation or bool(composed.metadata.get("requires_confirmation")),
    )
    response_experience = build_response_experience(
        command_plan=command_plan,
        result_type=result_type,
        result_metadata=result_metadata,
        composed=composed,
    )
    return RuntimeResult(
        result_type=result_type,
        status="waiting_authorization" if authorization else str(execution_status),
        title=title,
        summary=_summary_from_composed(composed),
        contextual_intro=response_experience.contextual_intro,
        followup_suggestions=response_experience.followup_suggestions,
        items=filtered_items,
        actions=_actions_for_result(
            result_type=result_type,
            result_context=filtered_result_context,
            company_id=company_id,
            scope_context=scope_context,
            authorization=authorization,
            sidepanel_context=sidepanel_context,
        ),
        target_ui=_target_ui_for_result(
            result_type=result_type,
            result_context=filtered_result_context,
            fallback=command_plan.target_ui,
            command_plan=command_plan,
            sidepanel_context=sidepanel_context,
        ),
        metadata={
            "company_id": company_id,
            "strategy": command_plan.planner_result.strategy,
            "intent": command_plan.intent,
            "question_type": command_plan.intent_result.question_type,
            "data_scope": command_plan.intent_result.data_scope,
            "command_frame": command_frame_payload(command_plan.command_frame),
            "command_enrichment": _command_enrichment_metadata(command_plan.intent_result),
            "scope_context": scope_context,
            "sources": list(command_plan.planner_result.sources),
            "permission_allowed": permission.allowed,
            "requires_confirmation": permission.requires_confirmation,
            "execution_identity": permission.execution_identity,
            "result_context": result_metadata,
            "authorization": authorization,
            "policy_result_filter": policy_result_filter,
            "sidepanel_context": sidepanel_context or {},
            "response_policy": response_policy,
            "response_experience": {
                "display_mode": response_experience.display_mode,
                "followup_suggestions": list(response_experience.followup_suggestions),
            },
        },
    )


def _command_enrichment_metadata(intent: IntentResult) -> dict[str, Any]:
    entities = intent.entities if isinstance(intent.entities, dict) else {}
    enrichment = entities.get("command_enrichment")
    if not isinstance(enrichment, dict):
        return {}
    allowed = {
        "business_domain",
        "capability",
        "objective",
        "constraints",
        "time_range",
        "output_preferences",
        "semantic_tags",
    }
    return {key: value for key, value in enrichment.items() if key in allowed}


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
        contextual_intro=str(payload.get("contextual_intro") or ""),
        followup_suggestions=_payload_suggestions(payload.get("followup_suggestions")),
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
        "contextual_intro": result.contextual_intro,
        "followup_suggestions": list(result.followup_suggestions),
        "items": list(result.items),
        "actions": list(result.actions),
        "target_ui": result.target_ui,
        "item_count": len(result.items),
        "metadata": result.metadata,
    }


def _payload_suggestions(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        candidates = [part.strip() for part in value.replace(" / ", "/").split("/")]
    elif isinstance(value, (list, tuple)):
        candidates = [str(item).strip() for item in value]
    else:
        return ()
    return tuple(item for item in candidates if item)[:4]


def _target_ui_for_result(
    *,
    result_type: str,
    result_context: ResultContext | None = None,
    fallback: TargetUI,
    command_plan: CommandPlan | None = None,
    sidepanel_context: dict[str, Any] | None = None,
) -> TargetUI:
    if result_type in {"runtime_action", "runtime_pending_confirmation", "runtime_waiting_input", "waiting_authorization"}:
        return "card"
    if result_type == "approval_detail":
        return "sidepanel"
    if result_type in {"approval_list", "approval_query"}:
        return "card"
    if result_type in {"people_search", "department_members", "organization_snapshot"}:
        if sidepanel_context and _command_output_surface(command_plan) == "sidepanel":
            return "card"
        metadata = result_context.metadata if result_context is not None and isinstance(result_context.metadata, dict) else {}
        if metadata.get("result_context_presentation") == "detail":
            return "card"
        return "none"
    if result_type == "company_profile_knowledge":
        return "none"
    return fallback


def _actions_for_result(
    *,
    result_type: str,
    result_context,
    company_id: str = "",
    scope_context: dict[str, Any] | None = None,
    authorization: dict[str, Any] | None = None,
    sidepanel_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], ...]:
    if result_context is None:
        return ()
    if result_type == "waiting_authorization" and authorization:
        return (_authorization_action(authorization),)
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
    if sidepanel_context and _should_expose_generic_sidepanel_action(result_type):
        return (_open_sidepanel_action(sidepanel_context),)
    return ()


def _should_expose_generic_sidepanel_action(result_type: str) -> bool:
    return result_type not in {
        "approval_list",
        "approval_query",
        "approval_detail",
        "runtime_action",
        "runtime_pending_confirmation",
        "runtime_waiting_input",
        "waiting_authorization",
    }


def _sidepanel_context_for_result(*, result_type: str, result_context: ResultContext | None, command_plan: CommandPlan | None = None) -> dict[str, Any] | None:
    if result_context is None or not result_context.items:
        return None
    metadata = result_context.metadata if isinstance(result_context.metadata, dict) else {}
    output_surface = _command_output_surface(command_plan)
    if metadata.get("result_context_presentation") == "summary" and output_surface != "sidepanel":
        return None
    if output_surface == "text":
        return None
    if metadata.get("result_context_presentation") != "detail" and output_surface != "sidepanel" and _command_prefers_text_presentation(command_plan):
        return None
    total = len(result_context.items)
    display_limit = _positive_int(metadata.get("display_limit"), default=_default_sidepanel_display_limit(result_type))
    display_offset = _non_negative_int(metadata.get("display_offset"), default=0)
    display_end = _non_negative_int(metadata.get("display_end"), default=min(display_limit, total))
    display_end = min(max(display_end, display_offset), total)
    has_more = bool(metadata.get("has_more")) or display_end < total or total > display_limit
    field_names = _sidepanel_field_names(result_context.items)
    if output_surface != "sidepanel" and not has_more and total <= 5 and len(field_names) <= 3:
        return None
    entity_domain = str(metadata.get("entity_domain") or _entity_domain_for_result_type(result_type))
    return {
        "available": True,
        "kind": "result_context",
        "route": "/sidepanel",
        "presentation": "table_detail",
        "result_type": result_type,
        "entity_domain": entity_domain,
        "item_count": total,
        "display_offset": display_offset,
        "display_end": display_end,
        "display_limit": display_limit,
        "has_more": has_more,
        "field_projection": str(metadata.get("field_projection") or ""),
        "visible_fields": field_names[:12],
        "title": _sidepanel_title(result_type=result_type, entity_domain=entity_domain),
    }


def _command_prefers_text_presentation(command_plan: CommandPlan | None) -> bool:
    if command_plan is None or command_plan.command_frame is None:
        return False
    params = command_plan.command_frame.params if isinstance(command_plan.command_frame.params, dict) else {}
    domain_query = params.get("domain_query") if isinstance(params.get("domain_query"), dict) else {}
    if domain_query.get("presentation_hint") == "text":
        return True
    response_intent = command_plan.command_frame.response_intent if isinstance(command_plan.command_frame.response_intent, dict) else {}
    return response_intent.get("should_render_card") is False


def _command_output_surface(command_plan: CommandPlan | None) -> str:
    if command_plan is None or command_plan.command_frame is None:
        return ""
    params = command_plan.command_frame.params if isinstance(command_plan.command_frame.params, dict) else {}
    contract = params.get("output_contract") if isinstance(params.get("output_contract"), dict) else {}
    return str(contract.get("surface") or "")


def _open_sidepanel_action(sidepanel_context: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": "open_sidepanel",
        "label": "打开侧边栏",
        "target_ui": "sidepanel",
        "route": str(sidepanel_context.get("route") or "/sidepanel"),
        "result_type": str(sidepanel_context.get("result_type") or ""),
        "entity_domain": str(sidepanel_context.get("entity_domain") or ""),
        "requires_confirmation": False,
    }


def _default_sidepanel_display_limit(result_type: str) -> int:
    if result_type in {"people_search", "department_members", "organization_snapshot"}:
        return 20
    return 20


def _sidepanel_field_names(items: tuple[dict[str, Any], ...]) -> list[str]:
    people_fields = _people_sidepanel_fields(items)
    if people_fields:
        return people_fields
    names: list[str] = []
    for item in items[:10]:
        for key, value in item.items():
            if key in names or _is_internal_sidepanel_field(str(key)):
                continue
            if _empty_sidepanel_value(value):
                continue
            names.append(str(key))
    return names


def _is_internal_sidepanel_field(key: str) -> bool:
    normalized = key.strip().lower()
    return (
        normalized == "raw"
        or normalized.startswith("_")
        or normalized
        in {
            "open_id",
            "union_id",
            "user_id",
            "company_id",
            "allowed_user_ids",
            "source_object_id",
            "source_event_ids",
            "department_id",
            "department_ids",
            "department_id_list",
            "gender_source",
            "title_source",
            "source_system",
            "resource_plane",
            "resource_type",
            "index",
        }
        or normalized.endswith("_open_id")
        or normalized.endswith("_user_id")
        or normalized.endswith("_company_id")
        or normalized.endswith("_source")
    )


def _people_sidepanel_fields(items: tuple[dict[str, Any], ...]) -> list[str]:
    if not any(isinstance(item, dict) and (item.get("resource_type") == "people" or item.get("source_system") == "feishu" or item.get("mobile")) for item in items[:10]):
        return []
    canonical_fields = ("name", "title", "department", "mobile", "email", "gender")
    return [field for field in canonical_fields if any(not _empty_sidepanel_value(_people_sidepanel_value(item, field)) for item in items[:10] if isinstance(item, dict))]


def _people_sidepanel_value(item: dict[str, Any], field: str) -> Any:
    if field == "title":
        return item.get("title") or item.get("job_title")
    if field == "department":
        return item.get("department") or item.get("department_names")
    if field == "gender" and "gender_source" in item and str(item.get("gender_source") or "").strip() != "source":
        return ""
    return item.get(field)


def _empty_sidepanel_value(value: Any) -> bool:
    return value is None or value == "" or value == () or value == [] or value == {}


def _sidepanel_title(*, result_type: str, entity_domain: str) -> str:
    if entity_domain == "people" or result_type in {"people_search", "department_members", "organization_snapshot"}:
        return "人员明细"
    if result_type in {"task_list", "task_query"}:
        return "任务明细"
    if result_type in {"mail_list", "mail_search"}:
        return "邮件明细"
    if result_type in {"knowledge_search", "knowledge"}:
        return "知识资料"
    return "结果明细"


def _entity_domain_for_result_type(result_type: str) -> str:
    if result_type in {"people_search", "department_members", "organization_snapshot"}:
        return "people"
    if result_type in {"task_list", "task_query"}:
        return "workspace"
    if result_type.startswith("mail"):
        return "communication"
    if result_type.startswith("knowledge"):
        return "knowledge"
    return ""


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _non_negative_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(parsed, 0)


def _authorization_metadata(
    *,
    result_type: str,
    result_metadata: dict[str, Any],
    company_id: str,
) -> dict[str, Any] | None:
    if result_type != "waiting_authorization":
        return None
    provider_results = result_metadata.get("provider_results") if isinstance(result_metadata.get("provider_results"), list) else []
    provider = next((item for item in provider_results if isinstance(item, dict) and item.get("waiting_authorization")), {})
    if not provider and result_metadata.get("waiting_authorization"):
        provider = result_metadata
    contract = provider.get("execution_identity_contract") if isinstance(provider.get("execution_identity_contract"), dict) else {}
    owner = contract.get("credential_owner") if isinstance(contract.get("credential_owner"), dict) else {}
    owner_open_id = str(owner.get("open_id") or "").strip()
    owner_company_id = str(owner.get("company_id") or company_id or "").strip()
    if not owner_company_id or not owner_open_id:
        return {
            "required": True,
            "available": False,
            "reason": "missing_authorization_owner",
            "credential_mode": str(provider.get("credential_mode") or "USER_TOKEN"),
            "authorization_status": str(provider.get("authorization_status") or "MISSING_AUTHORIZATION"),
        }
    query = urlencode({"company_id": owner_company_id, "open_id": owner_open_id})
    url = f"{settings.api_base_url.rstrip('/')}/api/user-identity/oauth/feishu/start?{query}"
    return {
        "required": True,
        "available": True,
        "resource_type": "user_identity_bundle",
        "label": "授权个人能力包",
        "channel": "feishu_oauth",
        "authorization_flow": "feishu_in_app_oauth",
        "url": url,
        "start_endpoint": "/api/user-identity/oauth/feishu/start",
        "callback_endpoint": "/api/feishu/oauth/callback",
        "owner_open_id": owner_open_id,
        "company_id": owner_company_id,
        "credential_mode": str(provider.get("credential_mode") or contract.get("credential_mode") or "USER_TOKEN"),
        "authorization_status": str(provider.get("authorization_status") or contract.get("authorization_status") or "MISSING_AUTHORIZATION"),
        "authorization_error": str(provider.get("authorization_error") or ""),
        "provider_boundary": str(provider.get("provider_boundary") or ""),
        "covered_resources": ["personal_feishu"],
        "can_escalate_original_permissions": False,
    }


def _authorization_action(authorization: dict[str, Any]) -> dict[str, Any]:
    action = {
        "action": "authorize_user_identity",
        "label": str(authorization.get("label") or "去授权"),
        "target_ui": "card",
        "requires_confirmation": False,
        "resource_type": str(authorization.get("resource_type") or "user_identity_bundle"),
        "channel": str(authorization.get("channel") or "feishu_oauth"),
        "authorization_status": str(authorization.get("authorization_status") or ""),
        "authorization_flow": str(authorization.get("authorization_flow") or ""),
        "url": str(authorization.get("url") or ""),
    }
    return {key: value for key, value in action.items() if value not in {"", None}}


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
        message="完成任务",
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
