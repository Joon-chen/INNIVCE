from __future__ import annotations

import asyncio
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from time import perf_counter
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.entities import BotUserAccess, Company, FeishuAppConfig, MemoryFact, OrganizationUser, Resource, Snapshot, WorkEvent
from app.services.agent.policies import BotActor
from app.services.feishu import approval_formatters
from app.services.feishu import approval_resources
from app.services.feishu.approval import (
    FeishuApprovalService,
    approval_amount,
    approval_attachment_refs,
    approval_form_fields,
    first_matching_field,
)
from app.services.feishu.approval_advice import approval_attachment_basis, rule_approval_decision_recommendation
from app.services.feishu.approval_attachments import FeishuApprovalAttachmentService
from app.services.cognitive_foundation import (
    append_cognitive_work_event,
    append_workspace_cognitive_observations,
    build_workspace_aggregation_from_visible_events,
    get_completed_snapshot,
    get_snapshot,
    upsert_snapshot,
)
from app.services.feishu.calendar import FeishuCalendarService
from app.services.feishu.drive import FeishuDriveService
from app.services.feishu.meeting import FeishuMeetingService
from app.services.feishu.okr import FeishuOkrService
from app.services.feishu.task import FeishuTaskService
from app.services.llm.approval_advisor import generate_approval_llm_advice
from app.services.organization_foundation import resolve_department_members
from app.services.runtime_v5.context import load_people_snapshot, save_people_snapshot
from app.services.runtime_v5.domain_query import domain_query_fields, domain_query_payload
from app.services.runtime_v5.feishu_user_token import resolve_feishu_user_access_token
from app.services.runtime_v5.models import ProviderRequest, ProviderResult, RuntimeContext, RuntimeIdentity
from app.services.runtime_v5.people_resolver import (
    asks_people_list,
    filter_people_by_department,
    filter_people_by_title,
    format_people_brief,
    gender_filter_from_text,
    normalize_gender,
    normalize_people_item,
    normalize_people_items,
    people_context_metadata,
    resolve_people_from_items,
)
from app.services.tools.base import ToolContext, ToolExecutionStatus, ToolRequest
from app.services.tools.providers.feishu_api import execute_feishu_api_tool, feishu_write_confirmation_token
from app.services.tools.providers.feishu_mcp import run_lark_cli_json_via_mcp, run_lark_cli_text_via_mcp
from app.services.tools.router import execute_agent_tool


class FeishuResourceProvider:
    source: str

    def __init__(self, *, db: Session, cli_profile: str | None = None) -> None:
        self.db = db
        self.cli_profile = cli_profile

    def _execute_tool(
        self,
        request: ProviderRequest,
        *,
        tool_name: str,
        params: dict[str, Any] | None = None,
        confirm_write: bool = False,
    ):
        company_id = request.context.runtime_scope.active_company_id
        if company_id is None:
            raise ValueError("Runtime V5 Feishu provider requires active_company_id.")
        tool_context = ToolContext(
            db=self.db,
            company_id=company_id,
            actor=_bot_actor_from_runtime_context(request.context),
            chat_id=request.context.chat_id,
            cli_profile=self.cli_profile,
        )
        tool_params = dict(params or {})
        tool_params.setdefault("as", request.execution_identity)
        tool_request = ToolRequest(
            tool_name=tool_name,
            question=request.intent.canonical_question or request.context.current_message,
            normalized_command=request.intent.canonical_question or request.context.current_message,
            params=tool_params,
        )
        if confirm_write:
            tool_request.params["confirmed"] = True
            tool_request.params["confirmation_token"] = feishu_write_confirmation_token(tool_context, tool_request)
        return execute_agent_tool(tool_context, tool_request)


class FeishuPeopleProvider(FeishuResourceProvider):
    source = "people"

    _OPERATIONS: dict[str, tuple[str, bool]] = {
        "resolve_identity": ("feishu_contact_user_search", False),
        "search_person": ("feishu_contact_user_search", False),
        "get_person": ("feishu_contact_user_get", False),
        "department_children": ("feishu_contact_department_children", False),
        "department_users": ("feishu_contact_department_users", False),
        "scope_list": ("feishu_contact_scope_list", False),
        "get_org_snapshot": ("feishu_contact_organization_snapshot", False),
        "list_department_members": ("feishu_contact_organization_snapshot", False),
    }

    def execute(self, request: ProviderRequest) -> ProviderResult:
        if request.operation in {"resolve_identity", "search_person"}:
            entities = request.intent.entities if isinstance(request.intent.entities, dict) else {}
            keyword = str(request.params.get("keyword") or entities.get("keyword") or request.intent.canonical_question or "").strip()
            query_fields = _people_query_fields_from_request(request)
            query_field = query_fields[-1] if query_fields else _people_query_field_from_request(request)
            cached_result = _people_resolve_from_snapshot(load_people_snapshot(request.context.runtime_scope.active_company_id), keyword)
            cached_items = cached_result.items
            capability = "people.resolve_identity" if request.operation == "resolve_identity" else "people.search_person"
            if cached_items:
                cached_items = self._augment_people_lookup_items(request=request, keyword=keyword, items=cached_items, query_field=query_field)
                cached_items = _enrich_people_items_from_foundation(self.db, cached_items, company_id=request.context.runtime_scope.active_company_id)
                match_type = cached_result.match_type
                return ProviderResult(
                    source="people",
                    status="success",
                    result_type="people_search",
                    count=len(cached_items),
                    items=cached_items,
                    metadata={
                        **people_context_metadata(capability=capability),
                        **_people_lookup_context_metadata(
                            cached_items,
                            keyword=keyword,
                            query_field=query_field,
                            query_fields=query_fields,
                            match_type=match_type,
                            intent_entities=entities,
                        ),
                        "keyword": keyword,
                        "people_query_field": query_field,
                        "people_query_fields": query_fields,
                        "cache_hit": True,
                        "match_type": match_type,
                        "needs_confirmation": match_type == "near_identity_candidate",
                    },
                    answer=_people_search_answer(keyword, cached_items, question=request.context.current_message, match_type=match_type, requested_fields=query_fields),
                    error="",
                )
            result = self._execute_tool(
                request,
                tool_name="feishu_contact_user_search",
                params={"keyword": keyword, "response_format": "raw_json", "as": "bot"},
            )
            payload = _tool_payload(result)
            users = payload.get("users") or payload.get("items") or []
            items = tuple(_user_item(user) for user in users if isinstance(user, dict)) if isinstance(users, list) else ()
            items = self._augment_people_lookup_items(request=request, keyword=keyword, items=items, query_field=query_field)
            items = _enrich_people_items_from_foundation(self.db, items, company_id=request.context.runtime_scope.active_company_id)
            return ProviderResult(
                source="people",
                status=_provider_status(result),
                result_type="people_search",
                count=len(items),
                items=items,
                metadata={
                    **people_context_metadata(capability=capability),
                    **_people_lookup_context_metadata(items, keyword=keyword, query_field=query_field, query_fields=query_fields, intent_entities=entities),
                    "keyword": keyword,
                    "people_query_field": query_field,
                    "people_query_fields": query_fields,
                    "raw": payload,
                },
                answer=_people_search_answer(keyword, items, question=request.context.current_message, requested_fields=query_fields),
                error=result.error or str(payload.get("error") or ""),
            )

        if request.operation == "list_department_members":
            keyword = str(request.params.get("keyword") or request.intent.canonical_question or "").strip()
            relation = _organization_relation_from_request(request)
            foundation_result = resolve_department_members(
                self.db,
                company_id=request.context.runtime_scope.active_company_id,
                query=keyword,
            )
            if foundation_result is not None:
                resolution = foundation_result.resolution
                items = foundation_result.items
                foundation_metadata = getattr(foundation_result, "metadata", {})
                foundation_metadata = foundation_metadata if isinstance(foundation_metadata, dict) else {}
                relation_items, relation_answer, relation_count = _department_relation_result(
                    relation=relation,
                    keyword=keyword,
                    items=items,
                    metadata=foundation_metadata,
                    resolution=resolution,
                )
                result_items = relation_items if relation else items
                return ProviderResult(
                    source="people",
                    status="success",
                    result_type="department_members",
                    count=relation_count if relation else len(items),
                    items=result_items,
                    metadata={
                        **people_context_metadata(capability="people.list_department_members"),
                        "keyword": keyword,
                        "organization_foundation": True,
                        "organization_resolution": _organization_resolution_metadata(resolution),
                        "organization_relation": relation,
                        **_department_membership_result_metadata(foundation_metadata),
                    },
                    answer=(
                        relation_answer
                        if relation_answer
                        else _department_members_answer(keyword, items, resolution=resolution)
                        if resolution.resolved_department_id
                        else _organization_resolution_failure_answer(keyword, resolution)
                    ),
                    error="" if resolution.resolved_department_id else "organization_resolution_not_resolved",
                )
            result, payload = self._organization_snapshot_payload(request)
            users = payload.get("users") if isinstance(payload.get("users"), list) else []
            items = tuple(
                item
                for item in (_user_item(user) for user in users if isinstance(user, dict))
                if _department_item_matches(item, keyword)
            )
            return ProviderResult(
                source="people",
                status=_provider_status(result),
                result_type="department_members",
                count=len(items),
                items=items,
                metadata={**people_context_metadata(capability="people.list_department_members"), "keyword": keyword, "raw": payload},
                answer=_department_members_answer(keyword, items),
                error=result.error or "",
            )

        if request.operation == "get_org_snapshot":
            result, payload = self._organization_snapshot_payload(request)
            departments = payload.get("departments") if isinstance(payload.get("departments"), list) else []
            users = payload.get("users") if isinstance(payload.get("users"), list) else []
            all_items = tuple(_user_item(user) for user in users if isinstance(user, dict))
            domain_query = domain_query_payload(request.intent.entities if isinstance(request.intent.entities, dict) else {})
            items, filter_metadata = _apply_people_domain_filters(
                all_items,
                question=request.context.current_message,
                domain_query=domain_query,
            )
            field_stats = _field_presence_stats(items)
            member_stats = _department_member_stats(departments)
            field_stats = {**field_stats, **member_stats}
            if _provider_status(result) == "success" and not departments and not all_items:
                return ProviderResult(
                    source="people",
                    status="error",
                    result_type="organization_snapshot",
                    metadata={"department_count": 0, "raw": payload},
                    answer="没有读取到组织架构数据，已停止后续建表写入。",
                    error="empty_organization_snapshot",
                )
            output_mode = str(domain_query.get("output_mode") or "")
            query_mode = str(request.intent.entities.get("people_query_mode") or _people_query_mode_from_text(request.context.current_message))
            field_projection = _people_field_projection_for_query_mode(output_mode or query_mode)
            presentation = "detail" if output_mode == "sidepanel" or query_mode in {"list", "gender_list", "title_list"} else "summary"
            return ProviderResult(
                source="people",
                status=_provider_status(result),
                result_type="organization_snapshot",
                count=len(items),
                items=items,
                metadata={
                    **people_context_metadata(capability="people.get_org_snapshot"),
                    "department_count": len(departments),
                    "full_user_count": len(all_items),
                    **filter_metadata,
                    "reported_member_count": member_stats.get("reported_member_count", 0),
                    "reported_member_count_basis": member_stats.get("reported_member_count_basis", ""),
                    "visible_user_count": len(items),
                    "people_query_mode": query_mode,
                    "domain_query": domain_query,
                    "field_projection": field_projection,
                    "result_context_presentation": presentation,
                    "field_stats": field_stats,
                    "fetch_ms": payload.get("_runtime_v5_fetch_ms", 0),
                    "cache_hit": bool(payload.get("_runtime_v5_cached", False)),
                    "raw": payload,
                },
                answer=(
                    _people_aggregate_answer(
                        len(departments),
                        all_items,
                        field_stats,
                        question=request.context.current_message,
                        query_mode=query_mode,
                        domain_query=domain_query,
                    )
                    if request.intent.entities.get("view") == "people_aggregate"
                    else _organization_snapshot_answer(len(departments), items, field_stats)
                ),
                error=result.error or "",
            )

        tool_spec = self._OPERATIONS.get(request.operation)
        if tool_spec is None:
            return _unsupported_operation_result("people", request.operation, sorted(self._OPERATIONS))
        tool_name, _ = tool_spec
        result = self._execute_tool(
            request,
            tool_name=tool_name,
            params=_people_tool_params(request),
        )
        return _provider_result_from_tool_result("people", result, fallback_result_type=request.operation)

    def _organization_snapshot_payload(self, request: ProviderRequest):
        company_id = request.context.runtime_scope.active_company_id
        cached = load_people_snapshot(company_id)
        if _people_snapshot_has_content(cached) and int(cached.get("_runtime_v5_snapshot_version") or 0) >= 3:
            cached = dict(cached)
            cached["_runtime_v5_cached"] = True
            cached["_runtime_v5_fetch_ms"] = 0
            return _cached_tool_result(), cached
        started = perf_counter()
        result = self._execute_tool(
            request,
            tool_name="feishu_contact_organization_snapshot",
            params={
                "max_departments": 100,
                "max_users": 500,
                "response_format": "raw_json",
                "as": "bot",
            },
        )
        payload = _tool_payload(result)
        payload["_runtime_v5_fetch_ms"] = int((perf_counter() - started) * 1000)
        if _provider_status(result) == "success" and _people_snapshot_has_content(payload):
            payload["_runtime_v5_snapshot_version"] = 3
            payload["_runtime_v5_snapshot_source"] = "people_provider_refresh"
            payload["_runtime_v5_saved_at"] = datetime.now(UTC).isoformat()
            save_people_snapshot(company_id, payload)
        return result, payload

    def _augment_people_lookup_items(
        self,
        *,
        request: ProviderRequest,
        keyword: str,
        items: tuple[dict[str, Any], ...],
        query_field: str,
    ) -> tuple[dict[str, Any], ...]:
        if not query_field or _people_items_have_field(items, query_field):
            return items
        _, payload = self._organization_snapshot_payload(request)
        users = payload.get("users") if isinstance(payload.get("users"), list) else []
        snapshot_items = resolve_people_from_items(keyword, normalize_people_items(users)).items
        if not snapshot_items:
            return items
        if not items:
            return snapshot_items
        merged: list[dict[str, Any]] = []
        for item in items:
            match = _matching_people_item(item, snapshot_items)
            merged.append({**match, **item, **{key: value for key, value in match.items() if value and not item.get(key)}} if match else item)
        return tuple(merged)


class FeishuApprovalProvider(FeishuResourceProvider):
    source = "approval"

    _OPERATIONS: dict[str, tuple[str, bool]] = {
        "list_pending": ("feishu_approval_task_query", False),
        "list_pending_fast": ("feishu_approval_task_query", False),
        "list_initiated": ("feishu_approval_instance_initiated", False),
        "get_detail": ("feishu_approval_instance_get", False),
        "approve": ("feishu_approval_task_approve", True),
        "reject": ("feishu_approval_task_reject", True),
        "transfer": ("feishu_approval_task_transfer", True),
        "add_sign": ("feishu_approval_task_add_sign", True),
        "rollback": ("feishu_approval_task_rollback", True),
        "remind": ("feishu_approval_instance_remind", True),
        "cancel": ("feishu_approval_instance_cancel", True),
        "cc": ("feishu_approval_instance_cc", True),
    }

    def execute(self, request: ProviderRequest) -> ProviderResult:
        if request.operation == "get_detail":
            item = request.params.get("item") if isinstance(request.params.get("item"), dict) else {}
            if not item:
                return ProviderResult(
                    source="approval",
                    status="error",
                    result_type="approval_detail",
                    answer="没有找到要展开的审批，请先查询待审批列表。",
                    error="missing_approval_item",
                )
            enriched_item, metadata = self._enrich_approval_detail_item(request, item)
            return ProviderResult(
                source="approval",
                status="success",
                result_type="approval_detail",
                count=1,
                items=(enriched_item,),
                metadata={"index": request.params.get("index"), **metadata},
                answer=_approval_detail_answer(enriched_item),
            )
        if request.operation in {"approve", "reject", "transfer", "add_sign", "rollback", "remind", "cancel", "cc"}:
            item = request.params.get("item") if isinstance(request.params.get("item"), dict) else {}
            if not item:
                return ProviderResult(
                    source="approval",
                    status="error",
                    result_type=f"approval_{request.operation}",
                    answer="没有找到要处理的审批，请先查询待审批列表。",
                    error="missing_approval_item",
                )
            raw_item = item.get("raw") if isinstance(item.get("raw"), dict) else item
            action_params = dict(request.params)
            self._resolve_approval_target_user_ids(request, request.operation, action_params)
            missing = _approval_write_missing_params(request.operation, action_params)
            if missing:
                return ProviderResult(
                    source="approval",
                    status="error",
                    result_type=f"approval_{request.operation}",
                    count=0,
                    metadata={"operation": request.operation, "missing_params": missing},
                    answer=_approval_missing_param_answer(request.operation, missing),
                    error="missing_approval_action_params",
                )
            tool_name = _approval_write_tool_name(request.operation)
            result = self._execute_tool(
                request,
                tool_name=tool_name,
                confirm_write=True,
                params={
                    "item": raw_item,
                    **_approval_write_tool_params(request.operation, action_params),
                    "response_format": "raw_json",
                },
            )
            status = _provider_status(result)
            action_text = _approval_action_text(request.operation)
            title = str(item.get("title") or "未命名审批")
            applicant = str(item.get("applicant") or "").strip()
            amount = str(item.get("amount") or "").strip()
            success_lines = [f"已提交审批{action_text}。", f"审批单：{title}"]
            if applicant:
                success_lines.append(f"申请人：{applicant}")
            if amount:
                success_lines.append(f"金额：{amount} 元")
            success_lines.append("飞书审批状态可能需要几秒刷新。")
            receipt_item = {
                "source": "approval",
                "operation": request.operation,
                "status": status,
                "status_group": "terminal" if status in {"success", "error", "failed", "denied", "partial"} else "unknown",
                "title": title,
                "applicant": applicant,
                "amount": amount,
                "action": request.operation,
                "action_label": action_text,
                "task_id": raw_item.get("task_id") or item.get("task_id") or "",
                "instance_code": raw_item.get("instance_code") or item.get("instance_code") or "",
                "serial_number": raw_item.get("serial_number") or item.get("serial_number") or "",
            }
            return ProviderResult(
                source="approval",
                status=status,
                result_type=f"approval_{request.operation}",
                count=1 if status == "success" else 0,
                items=(receipt_item,) if status == "success" else (),
                metadata={
                    "operation": request.operation,
                    "tool_name": tool_name,
                    "action_status_group": receipt_item["status_group"],
                    "is_terminal_action": receipt_item["status_group"] == "terminal",
                    **_provider_error_metadata(result),
                },
                answer="\n".join(success_lines) if status == "success" else _tool_failure_answer(f"审批{action_text}", result),
                error=result.error or "",
            )
        if request.operation == "list_initiated":
            result = self._execute_tool(
                request,
                tool_name="feishu_approval_instance_initiated",
                params={**_approval_initiated_tool_params(request), "response_format": "raw_json"},
            )
            payload = _tool_payload(result)
            raw_items = _items_from_payload(payload)
            items = tuple(_approval_item(item) for item in raw_items)
            return ProviderResult(
                source="approval",
                status=_provider_status(result),
                result_type="approval_initiated_list",
                count=len(items),
                items=items,
                metadata={"raw": payload},
                answer=_approval_initiated_list_answer(items),
                error=result.error or "",
            )
        if request.operation == "list_pending_fast":
            payload, error = self._fetch_pending_approval_payload(request, limit=20)
            raw_items = _items_from_payload(payload)
            items = tuple(_approval_item(item) for item in raw_items)
            status = "error" if error else "success"
            return ProviderResult(
                source="approval",
                status=status,
                result_type="approval_list",
                count=len(items),
                items=items,
                metadata={"raw": payload, "fast": True},
                answer=f"审批查询失败：{error}" if error else _approval_fast_list_answer(items),
                error=error,
            )
        if request.operation != "list_pending":
            return _unsupported_operation_result("approval", request.operation, sorted(self._OPERATIONS))
        substeps: list[dict[str, Any]] = []
        step_started = perf_counter()
        payload, error = self._fetch_pending_approval_payload(request, limit=20)
        substeps.append({"step": "approval_task_query", "duration_ms": int((perf_counter() - step_started) * 1000), "status": "error" if error else "success"})
        raw_items = _items_from_payload(payload)
        substeps.extend(self._enrich_approval_list_items(request, raw_items))
        items = tuple(_approval_item(item) for item in raw_items)
        return ProviderResult(
            source="approval",
            status="error" if error else "success",
            result_type="approval_list",
            count=len(items),
            items=items,
            metadata={"raw": payload, "substeps": substeps},
            answer=f"审批查询失败：{error}" if error else _approval_list_answer(items),
            error=error,
        )

    def _fetch_pending_approval_payload(self, request: ProviderRequest, *, limit: int) -> tuple[dict[str, Any], str]:
        open_id = str(request.context.identity.open_id or "").strip()
        if not open_id:
            return {}, "缺少当前用户 open_id，无法查询待审批任务。"
        app_config = _active_feishu_app_config(self.db, request.context.runtime_scope.active_company_id)
        if app_config is None:
            return {}, "缺少当前公司的飞书应用配置，无法查询待审批任务。"
        try:
            result = _run_async(
                FeishuApprovalService(app_config).fetch_pending_tasks(
                    self.db,
                    open_id=open_id,
                    limit=limit,
                    enrich_details=False,
                )
            )
        except Exception as exc:
            return {}, _safe_runtime_error(exc)
        if not result.get("available"):
            return {}, str(result.get("error") or "飞书审批任务查询失败。")
        items = result.get("items") if isinstance(result.get("items"), list) else []
        return {"available": True, "data": {"items": items}}, ""

    def _enrich_approval_detail_item(
        self,
        request: ProviderRequest,
        item: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        enriched_item = dict(item)
        raw_item = dict(item.get("raw")) if isinstance(item.get("raw"), dict) else dict(item)
        metadata: dict[str, Any] = {"detail_loaded": False, "attachment_read_count": 0, "substeps": []}
        step_started = perf_counter()
        detail = self._fetch_approval_instance_detail(request, raw_item)
        metadata["substeps"].append({"step": "approval_detail", "duration_ms": int((perf_counter() - step_started) * 1000), "status": "success" if detail else "empty"})
        if detail:
            raw_item["instance_detail"] = detail
            raw_item.pop("detail_error", None)
            raw_item.setdefault("instance_code", detail.get("instance_code") or detail.get("process_code"))
            raw_item.setdefault("serial_number", detail.get("serial_number"))
            detail_name = str(detail.get("approval_name") or detail.get("definition_name") or "").strip()
            if detail_name:
                raw_item.setdefault("approval_name", detail_name)
                enriched_item.setdefault("title", detail_name)
            metadata["detail_loaded"] = True

        metadata["substeps"].append({"step": "approval_attachments", "duration_ms": 0, "status": "skipped_async", "count": 0})

        enriched_item["raw"] = raw_item
        return enriched_item, metadata

    def _fetch_approval_instance_detail(
        self,
        request: ProviderRequest,
        raw_item: dict[str, Any],
    ) -> dict[str, Any]:
        instance_code = approval_formatters.approval_instance_code(raw_item)
        if not instance_code:
            return {}
        app_config = _active_feishu_app_config(self.db, request.context.runtime_scope.active_company_id)
        if app_config is None:
            return {}
        try:
            payload = _run_async(
                FeishuApprovalService(app_config).get_instance(
                    instance_code=instance_code,
                    user_id_type="open_id",
                )
            )
        except Exception as exc:
            raw_item["detail_error"] = _safe_runtime_error(exc)
            return {}
        data = _feishu_response_data(payload)
        return data if isinstance(data, dict) else {}

    def _read_approval_attachments(
        self,
        request: ProviderRequest,
        raw_item: dict[str, Any],
    ) -> list[Any]:
        detail = raw_item.get("instance_detail") if isinstance(raw_item.get("instance_detail"), dict) else {}
        refs = approval_attachment_refs(detail.get("form"))
        if not refs:
            return []
        app_config = _active_feishu_app_config(self.db, request.context.runtime_scope.active_company_id)
        if app_config is None:
            return []
        try:
            return _run_async(
                FeishuApprovalAttachmentService(app_config).read_attachment_refs(
                    refs,
                    instance_code=approval_formatters.approval_instance_code(raw_item),
                    max_files=3,
                )
            )
        except Exception as exc:
            return [
                SimpleNamespace(
                    name=str(ref.get("name") or ref.get("field_name") or "审批附件"),
                    token=str(ref.get("token") or ""),
                    error=str(exc)[:300],
                )
                for ref in refs[:3]
            ]

    def _enrich_approval_list_items(self, request: ProviderRequest, raw_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        substeps: list[dict[str, Any]] = []
        app_config = _active_feishu_app_config(self.db, request.context.runtime_scope.active_company_id)
        step_started = perf_counter()
        substeps.append({"step": "approval_live_data", "duration_ms": int((perf_counter() - step_started) * 1000), "status": "success", "count": len([item for item in raw_items[:10] if isinstance(item, dict)])})
        if app_config is not None:
            step_started = perf_counter()
            approval_resources.attach_approval_history_context(self.db, app_config, raw_items[:10])
            substeps.append({"step": "approval_history", "duration_ms": int((perf_counter() - step_started) * 1000), "status": "success"})
        step_started = perf_counter()
        for raw_item in raw_items[:10]:
            if not isinstance(raw_item, dict):
                continue
            self._attach_approval_snapshot(request, raw_item)
            if not raw_item.get("_approval_snapshot"):
                raw_item["_approval_snapshot"] = _pending_approval_snapshot_payload(
                    company_id=self._approval_company_id(request),
                    object_id=self._approval_object_id(raw_item),
                )
            raw_item["_approval_llm_ms"] = 0
            raw_item["_approval_assessment"] = _approval_assessment(raw_item, attachment_results=[])
        substeps.append({"step": "approval_snapshot_read", "duration_ms": int((perf_counter() - step_started) * 1000), "status": "success", "count": len([item for item in raw_items[:10] if isinstance(item, dict) and item.get("_approval_snapshot")])})
        substeps.append({"step": "approval_snapshot_builder_enqueue", "duration_ms": 0, "status": "skipped_boundary", "count": 0})
        substeps.append({"step": "approval_ai_judgement", "duration_ms": 0, "status": "skipped_async", "count": 0})
        return substeps

    def _approval_object_id(self, raw_item: dict[str, Any]) -> str:
        candidates = _approval_object_id_candidates(raw_item)
        return candidates[0] if candidates else ""

    def _approval_actor(self, request: ProviderRequest) -> str:
        return str(request.context.identity.open_id or request.context.user_id or "system").strip() or "system"

    def _approval_company_id(self, request: ProviderRequest):
        return request.context.runtime_scope.active_company_id

    def _record_approval_created(self, request: ProviderRequest, raw_item: dict[str, Any]) -> None:
        company_id = self._approval_company_id(request)
        object_id = self._approval_object_id(raw_item)
        if company_id is None or not object_id:
            return
        event = append_cognitive_work_event(
            self.db,
            company_id=company_id,
            event_type="approval_created",
            object_type="approval",
            object_id=object_id,
            source="bot_query_discovered",
            actor=self._approval_actor(request),
            payload={"item": _approval_snapshot_safe_item(raw_item)},
        )
        raw_item.setdefault("_approval_event_ids", []).append(str(event.id))

    def _record_approval_attachment_processed(
        self,
        request: ProviderRequest,
        raw_item: dict[str, Any],
        *,
        attachment_results: list[Any],
    ) -> None:
        company_id = self._approval_company_id(request)
        object_id = self._approval_object_id(raw_item)
        if company_id is None or not object_id:
            return
        event = append_cognitive_work_event(
            self.db,
            company_id=company_id,
            event_type="attachment_processed",
            object_type="approval",
            object_id=object_id,
            source="attachment_processor",
            actor="system",
            payload={"attachments": [_attachment_result_payload(result) for result in attachment_results]},
        )
        raw_item.setdefault("_approval_event_ids", []).append(str(event.id))

    def _write_approval_pending_snapshot(self, request: ProviderRequest, raw_item: dict[str, Any]) -> None:
        company_id = self._approval_company_id(request)
        object_id = self._approval_object_id(raw_item)
        if company_id is None or not object_id:
            return
        snapshot = upsert_snapshot(
            self.db,
            company_id=company_id,
            object_type="approval",
            object_id=object_id,
            snapshot_type="approval_current_judgment",
            status="pending_analysis",
            summary="审批附件仍在分析中。",
            recommendation="分析中",
            risk_level="pending",
            reasons=["附件或 AI 分析尚未完成"],
            source_event_ids=list(raw_item.get("_approval_event_ids") or []),
            payload={"cognitive_state": "pending_analysis"},
        )
        raw_item["_approval_snapshot"] = _snapshot_payload(snapshot)

    def _write_approval_analysis_snapshot(
        self,
        request: ProviderRequest,
        raw_item: dict[str, Any],
        *,
        assessment: dict[str, Any],
    ) -> None:
        company_id = self._approval_company_id(request)
        object_id = self._approval_object_id(raw_item)
        if company_id is None or not object_id:
            return
        analysis_event = append_cognitive_work_event(
            self.db,
            company_id=company_id,
            event_type="approval_analysis_completed",
            object_type="approval",
            object_id=object_id,
            source="ai_analysis",
            actor="system",
            payload={"assessment": assessment},
        )
        source_event_ids = list(raw_item.get("_approval_event_ids") or [])
        source_event_ids.append(str(analysis_event.id))
        raw_item["_approval_event_ids"] = source_event_ids
        snapshot = upsert_snapshot(
            self.db,
            company_id=company_id,
            object_type="approval",
            object_id=object_id,
            snapshot_type="approval_current_judgment",
            status="completed",
            summary=str(assessment.get("reason") or assessment.get("detailed_reason") or ""),
            recommendation=str(assessment.get("suggestion") or ""),
            risk_level=_approval_snapshot_risk_level(assessment),
            reasons=_approval_snapshot_reasons(assessment),
            source_event_ids=source_event_ids,
            payload={"assessment": assessment},
        )
        raw_item["_approval_snapshot"] = _snapshot_payload(snapshot)

    def _attach_approval_snapshot(self, request: ProviderRequest, raw_item: dict[str, Any]) -> None:
        company_id = self._approval_company_id(request)
        object_ids = _approval_object_id_candidates(raw_item)
        if company_id is None or not object_ids:
            return
        for object_id in object_ids:
            snapshot = get_completed_snapshot(
                self.db,
                company_id=company_id,
                object_type="approval",
                object_id=object_id,
                snapshot_type="approval_current_judgment",
            ) or get_snapshot(
                self.db,
                company_id=company_id,
                object_type="approval",
                object_id=object_id,
                snapshot_type="approval_current_judgment",
            )
            if snapshot is not None:
                raw_item["_approval_snapshot"] = _snapshot_payload(snapshot)
                return

    def _approval_has_completed_snapshot(self, raw_item: dict[str, Any]) -> bool:
        snapshot = raw_item.get("_approval_snapshot") if isinstance(raw_item.get("_approval_snapshot"), dict) else {}
        return snapshot.get("status") == "completed"

    def _approval_attachments_complete(self, raw_item: dict[str, Any], *, attachment_results: list[Any]) -> bool:
        detail = raw_item.get("instance_detail") if isinstance(raw_item.get("instance_detail"), dict) else {}
        refs = approval_attachment_refs(detail.get("form"))
        return not refs or bool(attachment_results)

    def _resolve_approval_target_user_ids(
        self,
        request: ProviderRequest,
        operation: str,
        action_params: dict[str, Any],
    ) -> None:
        target_keyword = str(action_params.get("target_keyword") or "").strip()
        if not target_keyword:
            return
        if operation == "transfer" and action_params.get("transfer_user_id"):
            return
        if operation == "add_sign" and action_params.get("add_sign_user_ids"):
            return
        if operation == "cc" and action_params.get("cc_user_ids"):
            return
        if operation not in {"transfer", "add_sign", "cc"}:
            return
        result = self._execute_tool(
            request,
            tool_name="feishu_contact_user_search",
            params={"keyword": target_keyword, "response_format": "raw_json", "as": "bot"},
        )
        payload = _tool_payload(result)
        users = payload.get("users") or payload.get("items") or []
        if not isinstance(users, list) or not users:
            return
        user = next((item for item in users if isinstance(item, dict)), None)
        if not user:
            return
        open_id = str(user.get("open_id") or user.get("user_id") or user.get("id") or "").strip()
        if not open_id:
            return
        if operation == "transfer":
            action_params["transfer_user_id"] = open_id
        elif operation == "add_sign":
            action_params["add_sign_user_ids"] = [open_id]
        elif operation == "cc":
            action_params["cc_user_ids"] = [open_id]


class FeishuBaseProvider(FeishuResourceProvider):
    source = "base"

    _OPERATIONS: dict[str, tuple[str, bool]] = {"write_records": ("feishu_bitable_record_batch_create", True)}

    def execute(self, request: ProviderRequest) -> ProviderResult:
        if request.operation != "write_records":
            return _unsupported_operation_result("base", request.operation, ["write_records"])

        app_token = str(request.params.get("app_token") or "").strip()
        rows = _organization_rows_from_previous_results(request.params.get("previous_results"))
        if not rows:
            return ProviderResult(
                source="base",
                status="error",
                result_type="base_export",
                metadata={"operation": request.operation, "error_type": "missing_dependency_result", "missing_source": "people"},
                error="missing_people_rows",
                answer="没有可写入的组织架构人员数据，未创建表格。",
            )

        created_base = False
        base_url = ""
        table_id = ""
        substeps: list[dict[str, Any]] = []
        if not app_token:
            step_started = perf_counter()
            base_result = self._execute_tool(
                request,
                tool_name="feishu_bitable_base_create",
                confirm_write=True,
                params={
                    "name": "最新组织架构",
                    "table_name": "组织架构",
                    "fields": _organization_base_fields(),
                    "time_zone": "Asia/Shanghai",
                    "response_format": "raw_json",
                },
            )
            substeps.append({"step": "base_create", "duration_ms": int((perf_counter() - step_started) * 1000), "status": _provider_status(base_result)})
            base_payload = _tool_payload(base_result)
            app_token = str(base_payload.get("app_token") or base_payload.get("base_token") or "").strip()
            table_id = str(base_payload.get("table_id") or "").strip()
            base_url = str(base_payload.get("url") or "").strip()
            created_base = bool(app_token)
            if not app_token:
                return _provider_result_from_tool_result("base", base_result, fallback_result_type="base_create")

        if not table_id:
            step_started = perf_counter()
            table_result = self._execute_tool(
                request,
                tool_name="feishu_bitable_table_create",
                confirm_write=True,
                params={
                    "app_token": app_token,
                    "name": "组织架构",
                    "fields": _organization_base_fields(),
                    "response_format": "raw_json",
                },
            )
            substeps.append({"step": "table_create", "duration_ms": int((perf_counter() - step_started) * 1000), "status": _provider_status(table_result)})
            table_payload = _tool_payload(table_result)
            table_id = str(table_payload.get("table_id") or "").strip()
            if not table_id:
                return _provider_result_from_tool_result("base", table_result, fallback_result_type="base_table")

        step_started = perf_counter()
        record_result = self._execute_tool(
            request,
            tool_name="feishu_bitable_record_batch_create",
            confirm_write=True,
            params={
                "app_token": app_token,
                "table_id": table_id,
                "fields": _organization_export_field_names(),
                "rows": rows,
                "response_format": "raw_json",
            },
        )
        substeps.append({"step": "record_batch_create", "duration_ms": int((perf_counter() - step_started) * 1000), "status": _provider_status(record_result), "row_count": len(rows)})
        return ProviderResult(
            source="base",
            status=_provider_status(record_result),
            result_type="base_export",
            count=len(rows),
            items=(
                {
                    "title": "最新组织架构",
                    "row_count": len(rows),
                    "app_token": app_token,
                    "table_id": table_id,
                    "url": base_url,
                    "open_hint": "可在飞书多维表格最近文件中打开；如果当前租户返回链接，会优先展示打开链接。",
                    "table_name": "组织架构",
                },
            ),
            metadata={
                "app_token": app_token,
                "table_id": table_id,
                "table_name": "组织架构",
                "created_base": created_base,
                "url": base_url,
                "substeps": substeps,
            },
            answer=_base_export_answer(row_count=len(rows), app_token=app_token, table_id=table_id, url=base_url),
            error=record_result.error or "",
        )


class FeishuIMProvider(FeishuResourceProvider):
    source = "im"

    _OPERATIONS: dict[str, tuple[str | None, bool]] = {
        "send_message": ("feishu_im_send_message", True),
        "send_result": ("feishu_im_send_message", True),
        "search_chats": ("feishu_im_chat_search", False),
        "list_messages": ("feishu_im_message_list", False),
        "create_chat": ("feishu_im_create_chat", True),
        "auto_join_public_chats": ("feishu_im_auto_join_public_chats", True),
        "reply_message": (None, True),
        "message_search": (None, False),
        "chat_members_list": (None, False),
        "pin_create": (None, True),
        "reaction_create": (None, True),
        "flag_create": (None, True),
    }

    def execute(self, request: ProviderRequest) -> ProviderResult:
        tool_spec = self._OPERATIONS.get(request.operation)
        if tool_spec is None:
            return _unsupported_operation_result("im", request.operation, sorted(self._OPERATIONS))
        tool_name, is_write = tool_spec
        if tool_name is None:
            return _tool_not_installed_result("im", request.operation)
        if request.operation == "search_chats":
            result = self._execute_tool(
                request,
                tool_name=tool_name,
                params={**_im_tool_params(request), "response_format": "raw_json"},
            )
            payload = _tool_payload(result)
            items = tuple(_im_chat_item(item) for item in _items_from_payload(payload))
            query = str(request.params.get("query") or "")
            return ProviderResult(
                source="im",
                status=_provider_status(result),
                result_type="chat_list",
                count=len(items),
                items=items,
                metadata={"operation": request.operation, "tool_name": tool_name, "query": query, **_provider_error_metadata(result)},
                answer=_im_chat_list_answer(items, query=query),
                error=result.error or "",
            )
        if request.operation == "list_messages":
            result = self._execute_tool(
                request,
                tool_name=tool_name,
                params={**_im_tool_params(request), "response_format": "raw_json"},
            )
            payload = _tool_payload(result)
            items = tuple(_im_message_item(item) for item in _items_from_payload(payload))
            return ProviderResult(
                source="im",
                status=_provider_status(result),
                result_type="im_message_list",
                count=len(items),
                items=items,
                metadata={"operation": request.operation, "tool_name": tool_name, **_provider_error_metadata(result)},
                answer=_im_message_list_answer(items),
                error=result.error or "",
            )
        if request.operation in {"send_message", "send_result"}:
            if (
                str(request.params.get("target_type") or "").strip() == "people_context"
                and _normalized_im_delivery_mode(request.params.get("delivery_mode")) == "create_group_then_send"
            ):
                return self._execute_people_context_group_send(request)
            params_or_error = self._im_send_params(request)
            if isinstance(params_or_error, ProviderResult):
                return params_or_error
            result = self._execute_tool(
                request,
                tool_name=tool_name,
                confirm_write=is_write,
                params=params_or_error,
            )
            status = _provider_status(result)
            return ProviderResult(
                source="im",
                status=status,
                result_type="message_send",
                count=1 if status == "success" else 0,
                items=(
                    {
                        "target": _im_target_label(params_or_error),
                        "text": params_or_error.get("text", ""),
                        "resolved_user_id": params_or_error.get("user_id", ""),
                        "resolved_chat_id": params_or_error.get("chat_id", ""),
                        "resolved_target_name": params_or_error.get("target_name", ""),
                    },
                )
                if status == "success"
                else (),
                metadata={
                    "operation": request.operation,
                    "tool_name": tool_name,
                    "target": _im_target_label(params_or_error),
                    "target_type": request.params.get("target_type", ""),
                    "target_query": request.params.get("target", ""),
                    "resolved_user_id": params_or_error.get("user_id", ""),
                    "resolved_chat_id": params_or_error.get("chat_id", ""),
                    "resolved_target_name": params_or_error.get("target_name", ""),
                    **_provider_error_metadata(result),
                },
                answer=(
                    f"结果已发送给{_im_target_label(params_or_error)}。"
                    if status == "success" and request.operation == "send_result"
                    else "消息已发送。"
                    if status == "success"
                    else _tool_failure_answer("消息发送", result)
                ),
                error=result.error or "",
            )
        result = self._execute_tool(
            request,
            tool_name=tool_name,
            confirm_write=is_write,
            params=_im_tool_params(request),
        )
        return _provider_result_from_tool_result("im", result, fallback_result_type=request.operation)

    def _im_send_text(self, request: ProviderRequest) -> str:
        text = str(request.params.get("text") or "").strip()
        if not text and request.operation == "send_result":
            text = _message_text_from_previous_results(request.params.get("previous_results"))
        return text.strip()

    def _people_context_items(self, request: ProviderRequest) -> tuple[dict[str, Any], ...]:
        targets = request.params.get("people_targets")
        return tuple(item for item in targets if isinstance(item, dict)) if isinstance(targets, list) else ()

    def _execute_people_context_group_send(self, request: ProviderRequest) -> ProviderResult:
        text = self._im_send_text(request)
        if not text:
            return _missing_params_result(
                source="im",
                result_type="message_send",
                operation=request.operation,
                missing=("text",),
                answer="发送消息还缺少消息内容。",
                error="missing_message_text",
            )
        items = self._people_context_items(request)
        if not items:
            return _missing_params_result(
                source="im",
                result_type="message_send",
                operation=request.operation,
                missing=("people_targets",),
                answer="上一轮人员结果里没有可用于发送的人员目标。",
                error="missing_people_targets",
            )
        open_ids = tuple(
            str(item.get("open_id") or item.get("user_id") or "").strip()
            for item in items
            if str(item.get("open_id") or item.get("user_id") or "").strip()
        )
        if not open_ids:
            return _missing_params_result(
                source="im",
                result_type="message_send_people_context_group",
                operation=request.operation,
                missing=("people_open_ids",),
                answer="这些人员结果里没有可用于建群的飞书 open_id。",
                error="missing_people_open_ids",
            )

        chat_name = _people_context_group_chat_name(request, items)
        create_result = self._execute_tool(
            request,
            tool_name="feishu_im_create_chat",
            confirm_write=True,
            params={
                "name": chat_name,
                "description": "由数字参谋根据上一轮人员结果创建",
                "user_id_list": list(open_ids),
                "chat_type": "private",
                "chat_mode": "group",
                "as": "bot",
            },
        )
        create_status = _provider_status(create_result)
        chat_id = _chat_id_from_tool_result(create_result)
        if create_status != "success" or not chat_id:
            return ProviderResult(
                source="im",
                status="error",
                result_type="message_send_people_context_group",
                count=0,
                items=(),
                metadata={
                    "operation": request.operation,
                    "target_type": "people_context",
                    "delivery_mode": "create_group_then_send",
                    "people_target_count": len(items),
                    "tool_name": "feishu_im_create_chat",
                    "chat_name": chat_name,
                    "created_chat_id": chat_id,
                    "substeps": {"create_chat": create_status},
                    **_provider_error_metadata(create_result),
                },
                answer=_tool_failure_answer("群聊创建", create_result)
                if create_status != "success"
                else "群聊已创建，但没有拿到可继续发送消息的 chat_id。",
                error=create_result.error or "missing_created_chat_id",
            )

        send_result = self._execute_tool(
            request,
            tool_name="feishu_im_send_message",
            confirm_write=True,
            params={"chat_id": chat_id, "text": text, "as": "bot"},
        )
        status = _provider_status(send_result)
        return ProviderResult(
            source="im",
            status=status,
            result_type="message_send_people_context_group",
            count=1 if status == "success" else 0,
            items=(
                {
                    "target": chat_name,
                    "text": text,
                    "resolved_chat_id": chat_id,
                    "resolved_target_name": chat_name,
                    "people_target_count": len(items),
                    "delivery_mode": "create_group_then_send",
                },
            )
            if status == "success"
            else (),
            metadata={
                "operation": request.operation,
                "target_type": "people_context",
                "delivery_mode": "create_group_then_send",
                "people_target_count": len(items),
                "tool_name": "feishu_im_send_message",
                "create_tool_name": "feishu_im_create_chat",
                "chat_name": chat_name,
                "resolved_chat_id": chat_id,
                "execution_identity": "bot",
                "execution_identity_source": "people_context_group_send_bot_identity",
                "substeps": {"create_chat": create_status, "send_message": status},
                **_provider_error_metadata(send_result),
            },
            answer="已创建群聊并发送消息。" if status == "success" else _tool_failure_answer("群内消息发送", send_result),
            error=send_result.error or "",
        )

    def _im_send_params(self, request: ProviderRequest) -> dict[str, Any] | ProviderResult:
        text = self._im_send_text(request)
        if not text:
            return _missing_params_result(
                source="im",
                result_type="message_send",
                operation=request.operation,
                missing=("text",),
                answer="发送消息还缺少消息内容。" if request.operation == "send_message" else "没有可发送的执行结果。",
                error="missing_message_text",
            )
        target_type = str(request.params.get("target_type") or "").strip()
        params: dict[str, Any] = {"text": text, "as": request.execution_identity}
        if target_type == "people_context":
            items = self._people_context_items(request)
            if not items:
                return _missing_params_result(
                    source="im",
                    result_type="message_send",
                    operation=request.operation,
                    missing=("people_targets",),
                    answer="上一轮人员结果里没有可用于发送的人员目标。",
                    error="missing_people_targets",
                )
            return ProviderResult(
                source="im",
                status="partial",
                result_type="message_send_people_context",
                count=len(items),
                items=items,
                metadata={
                    "operation": request.operation,
                    "target_type": "people_context",
                    "people_target_count": len(items),
                    "requires_confirmation": True,
                    "confirmation_reason": "batch_people_message_not_auto_executed",
                },
                answer=f"已识别上一轮人员结果中的 {len(items)} 个发送对象。批量发送消息需要进入确认流程，当前未直接发送。",
                error="requires_batch_send_confirmation",
            )
        if target_type in {"self", "current_chat"}:
            if request.context.chat_id:
                params["chat_id"] = request.context.chat_id
            elif request.context.identity.open_id:
                params["user_id"] = request.context.identity.open_id
            else:
                return _missing_params_result(
                    source="im",
                    result_type="message_send",
                    operation=request.operation,
                    missing=("target",),
                    answer="没有找到可发送的当前会话或用户。",
                    error="missing_im_target",
                )
            return params
        if target_type == "chat":
            chat = self._resolve_chat(request, str(request.params.get("target") or ""))
            if isinstance(chat, ProviderResult):
                return chat
            params["chat_id"] = chat.get("chat_id")
            params["target_name"] = chat.get("name")
            return params
        if target_type == "person":
            direct_open_id = str(request.params.get("target_open_id") or request.params.get("open_id") or request.params.get("user_id") or "").strip()
            if direct_open_id:
                params["user_id"] = direct_open_id
                params["target_name"] = str(request.params.get("target_name") or request.params.get("target") or "").strip()
                return params
            person = self._resolve_person(request, str(request.params.get("target") or ""))
            if isinstance(person, ProviderResult):
                return person
            params["user_id"] = person.get("open_id")
            params["target_name"] = person.get("name")
            return params
        return _missing_params_result(
            source="im",
            result_type="message_send",
            operation=request.operation,
            missing=("target",),
            answer="发送消息还缺少发送对象。",
            error="missing_im_target",
        )

    def _resolve_chat(self, request: ProviderRequest, query: str) -> dict[str, str] | ProviderResult:
        if not query:
            return _missing_params_result(
                source="im",
                result_type="chat_resolve",
                operation="search_chats",
                missing=("query",),
                answer="请提供群名称。",
                error="missing_chat_query",
            )
        result = self._execute_tool(request, tool_name="feishu_im_chat_search", params={"query": query, "page_size": 5, "response_format": "raw_json", "as": "user"})
        items = tuple(_im_chat_item(item) for item in _items_from_payload(_tool_payload(result)))
        if len(items) == 1 and items[0].get("chat_id"):
            return {"chat_id": str(items[0]["chat_id"]), "name": str(items[0].get("name") or query)}
        return ProviderResult(
            source="im",
            status="error",
            result_type="chat_resolve",
            count=len(items),
            items=items,
            metadata={"operation": "search_chats", "error_type": "ambiguous_target", "query": query},
            answer=_im_target_candidates_answer("群聊", query, items),
            error="ambiguous_or_missing_chat",
        )

    def _resolve_person(self, request: ProviderRequest, query: str) -> dict[str, str] | ProviderResult:
        if not query:
            return _missing_params_result(
                source="im",
                result_type="person_resolve",
                operation="search_person",
                missing=("query",),
                answer="请提供收件人姓名。",
                error="missing_person_query",
            )
        cached = _people_items_from_snapshot(load_people_snapshot(request.context.runtime_scope.active_company_id), query)
        if len(cached) == 1 and cached[0].get("open_id"):
            return {"open_id": str(cached[0]["open_id"]), "name": str(cached[0].get("name") or query)}
        result = self._execute_tool(request, tool_name="feishu_contact_user_search", params={"keyword": query, "response_format": "raw_json", "as": "bot"})
        payload = _tool_payload(result)
        users = payload.get("users") or payload.get("items") or []
        items = tuple(_user_item(user) for user in users if isinstance(user, dict)) if isinstance(users, list) else ()
        if len(items) == 1 and items[0].get("open_id"):
            return {"open_id": str(items[0]["open_id"]), "name": str(items[0].get("name") or query)}
        return ProviderResult(
            source="im",
            status="error",
            result_type="person_resolve",
            count=len(items),
            items=items,
            metadata={"operation": "search_person", "error_type": "ambiguous_target", "query": query},
            answer=_im_target_candidates_answer("人员", query, items),
            error="ambiguous_or_missing_person",
        )


class FeishuTaskProvider(FeishuResourceProvider):
    source = "task"

    _OPERATIONS: dict[str, tuple[str, bool]] = {
        "list_my_tasks": ("task_qa", False),
        "search_tasks": ("task_qa", False),
        "create_task": ("feishu_task_create", True),
        "update_task": ("feishu_task_update", True),
        "complete_task": ("feishu_task_complete", True),
        "reopen_task": ("feishu_task_reopen", True),
        "delete_task": ("feishu_task_delete", True),
        "create_subtask": ("feishu_task_subtask_create", True),
        "comment_task": ("feishu_task_comment", True),
        "assign_members": ("feishu_task_assign_members", True),
        "update_followers": ("feishu_task_update_followers", True),
        "update_reminders": ("feishu_task_update_reminders", True),
        "upload_attachment": ("feishu_task_upload_attachment", True),
        "add_to_tasklist": ("feishu_task_add_to_tasklist", True),
        "set_ancestor": ("feishu_task_set_ancestor", True),
        "clear_ancestor": ("feishu_task_clear_ancestor", True),
        "tasklist_create": ("feishu_tasklist_create", True),
        "tasklist_update": ("feishu_tasklist_update", True),
        "tasklist_delete": ("feishu_tasklist_delete", True),
        "tasklist_update_members": ("feishu_tasklist_update_members", True),
        "tasklist_set_members": ("feishu_tasklist_set_members", True),
        "section_create": ("feishu_task_section_create", True),
        "section_update": ("feishu_task_section_update", True),
        "section_delete": ("feishu_task_section_delete", True),
    }

    def execute(self, request: ProviderRequest) -> ProviderResult:
        tool_spec = self._OPERATIONS.get(request.operation)
        if tool_spec is None:
            return _unsupported_operation_result("task", request.operation, sorted(self._OPERATIONS))
        tool_name, is_write = tool_spec
        if request.operation in {"list_my_tasks", "search_tasks"}:
            params = _task_query_tool_params(request)
            if request.operation == "search_tasks":
                keyword = str(request.params.get("keyword") or "").strip()
                if keyword:
                    params["query"] = keyword
            query_path = _operational_query_read_path(request, source="task")
            if query_path == "self_user_token_required":
                user_fallback = self._execute_task_query_with_user_fallback(
                    request,
                    operation=request.operation,
                    params=params,
                )
                if user_fallback is not None:
                    return user_fallback
                boundary = _enterprise_realtime_boundary_result(
                    request,
                    source="task",
                    result_type="task_list",
                    operation=request.operation,
                    current_provider="task_qa",
                    user_fallback_allowed=True,
                )
                if boundary is not None:
                    return boundary
            elif query_path == "tenant_query_not_integrated":
                cognitive_aggregation = _workspace_cognitive_aggregation_result(
                    self.db,
                    request,
                    source="task",
                    operation=request.operation,
                )
                if cognitive_aggregation is not None:
                    return cognitive_aggregation
                boundary = _enterprise_realtime_boundary_result(
                    request,
                    source="task",
                    result_type="task_list",
                    operation=request.operation,
                    current_provider="task_qa",
                    user_fallback_allowed=False,
                )
                if boundary is not None:
                    return boundary
            else:
                tenant_result = self._execute_task_query_with_tenant_token(
                    request,
                    operation=request.operation,
                    params=params,
                )
                if tenant_result is not None:
                    return tenant_result
            user_fallback = self._execute_task_query_with_user_fallback(
                request,
                operation=request.operation,
                params=params,
            )
            if user_fallback is not None:
                return user_fallback
            boundary = _enterprise_realtime_boundary_result(
                request,
                source="task",
                result_type="task_list",
                operation=request.operation,
                current_provider="task_qa",
                user_fallback_allowed=True,
            )
            if boundary is not None:
                return boundary
            return self._execute_task_query(
                request,
                tool_name=tool_name,
                operation=request.operation,
                params=params,
            )

        if request.operation == "complete_task":
            return self._execute_task_complete_with_user_token(request, params=_task_tool_params(request))

        if request.operation == "create_task":
            return self._execute_task_create_with_user_token(request, params=_task_tool_params(request))

        return self._execute_task_tool(
            request,
            tool_name=tool_name,
            operation=request.operation,
            is_write=is_write,
            params=_task_tool_params(request),
        )

    def _execute_task_create_with_user_token(
        self,
        request: ProviderRequest,
        *,
        params: dict[str, Any],
    ) -> ProviderResult:
        summary = str(params.get("summary") or request.intent.canonical_question or request.context.current_message).strip()
        if not summary:
            return _missing_params_result(
                source="task",
                result_type="task_create",
                operation=request.operation,
                missing=("summary",),
                answer="创建任务还缺少任务标题。",
                error="missing_task_summary",
            )

        company_id = request.context.runtime_scope.active_company_id
        app_config = _active_feishu_app_config(self.db, company_id)
        token_resolution = _run_async(
            resolve_feishu_user_access_token(
                self.db,
                company_id=company_id,
                open_id=request.context.identity.open_id,
                app_config=app_config,
            )
        )
        identity_contract = request.execution_identity_contract.payload()
        identity_contract["authorization_status"] = token_resolution.authorization_status
        if not token_resolution.authorized:
            return ProviderResult(
                source="task",
                status="denied",
                result_type="waiting_authorization",
                metadata={
                    "operation": request.operation,
                    "credential_mode": "USER_TOKEN",
                    "authorization_status": token_resolution.authorization_status,
                    "authorization_error": token_resolution.error,
                    "execution_identity_contract": identity_contract,
                    "waiting_authorization": True,
                    "provider_boundary": "user_token_required",
                },
                answer="创建任务需要本人飞书授权。请先完成飞书用户授权后再执行。",
                error=token_resolution.error or "missing_user_token",
            )

        if app_config is None:
            return ProviderResult(
                source="task",
                status="error",
                result_type="task_create",
                metadata={
                    "operation": request.operation,
                    "credential_mode": "USER_TOKEN",
                    "authorization_status": token_resolution.authorization_status,
                    "authorization_error": "missing_feishu_app_config",
                    "execution_identity_contract": identity_contract,
                },
                answer="没有找到当前公司的飞书应用配置，暂时不能创建任务。",
                error="missing_feishu_app_config",
            )

        try:
            payload = _execute_controlled_task_create(
                request,
                db=self.db,
                app_config=app_config,
                summary=summary,
                params=params,
                user_access_token=token_resolution.user_access_token,
            )
        except Exception as exc:
            return ProviderResult(
                source="task",
                status="error",
                result_type="task_create",
                metadata={
                    "operation": request.operation,
                    "credential_mode": "USER_TOKEN",
                    "authorization_status": token_resolution.authorization_status,
                    "authorization_error": "",
                    "execution_identity_contract": identity_contract,
                    "error_type": "provider_execution_failed",
                    "tool_error": str(exc),
                },
                answer="任务创建失败，原始错误已记录到 Runtime Result。",
                error=str(exc),
            )

        data = _feishu_response_data(payload)
        raw_task = data.get("task") if isinstance(data, dict) and isinstance(data.get("task"), dict) else data
        item = _task_item(raw_task if isinstance(raw_task, dict) else {"summary": summary})
        task_id = str(item.get("task_guid") or item.get("guid") or item.get("id") or "")
        url = str(item.get("url") or item.get("app_link") or item.get("link") or "")
        return ProviderResult(
            source="task",
            status="success",
            result_type="task_create",
            count=1,
            items=({"title": str(item.get("title") or summary), "task_id": task_id, "url": url},),
            metadata={
                "operation": request.operation,
                "credential_mode": "USER_TOKEN",
                "authorization_status": token_resolution.authorization_status,
                "account_id": token_resolution.account_id,
                "execution_identity_contract": identity_contract,
                "summary": summary,
                "raw": payload,
                "operational_source": "feishu_realtime",
                "enterprise_write_through": "disabled",
            },
            answer=f"已创建任务：{summary}",
            error="",
        )

    def _execute_task_query(
        self,
        request: ProviderRequest,
        *,
        tool_name: str,
        operation: str,
        params: dict[str, Any],
    ) -> ProviderResult:
        result = self._execute_tool(
            request,
            tool_name=tool_name,
            params=params,
        )
        status = _provider_status(result)
        payload = _tool_payload(result)
        raw_items = _items_from_payload(payload)
        items = tuple(_task_item(item) for item in raw_items)
        return ProviderResult(
            source="task",
            status=status,
            result_type="task_list",
            count=len(items),
            items=items,
            metadata={
                "operation": operation,
                "tool_name": tool_name,
                "query": params.get("query") or params.get("keyword") or "",
                **_provider_error_metadata(result),
            },
            answer=_task_list_answer(items, query=str(params.get("query") or params.get("keyword") or "")),
            error=result.error or "",
        )

    def _execute_task_query_with_tenant_token(
        self,
        request: ProviderRequest,
        *,
        operation: str,
        params: dict[str, Any],
    ) -> ProviderResult | None:
        identity_contract = request.execution_identity_contract.payload()
        if identity_contract.get("actor_identity") != "BOT" or identity_contract.get("credential_mode") != "TENANT_TOKEN":
            return None

        company_id = request.context.runtime_scope.active_company_id
        if self.db is None:
            return None
        app_config = _active_feishu_app_config(self.db, company_id)
        if app_config is None:
            return None

        try:
            payload = _run_async(
                FeishuTaskService(app_config).list_tasks(
                    page_size=int(params.get("page_size") or 50),
                    page_token=str(params.get("page_token") or "") or None,
                )
            )
        except Exception as exc:
            if str(request.intent.data_scope or "").lower() == "self":
                return None
            return ProviderResult(
                source="task",
                status="error",
                result_type="task_list",
                metadata={
                    "operation": operation,
                    "credential_mode": "TENANT_TOKEN",
                    "actor_identity": "BOT",
                    "execution_identity_contract": identity_contract,
                    "provider_boundary": "enterprise_realtime_read_failed",
                    "legacy_cli_fallback_used": False,
                    "fallback_used": False,
                    "workevent_as_realtime_source": False,
                    "extracted_item_as_realtime_source": False,
                    "error_type": "provider_execution_failed",
                    "tool_error": str(exc),
                },
                answer="任务企业实时读取失败。我不会改用本地认知数据或当前用户身份代查。",
                error=str(exc),
            )

        data = _feishu_response_data(payload)
        raw_items = _items_from_payload(data)
        items = tuple(_task_item(item) for item in raw_items)
        query = str(params.get("query") or params.get("keyword") or "").strip()
        if operation == "search_tasks" and query:
            lowered = query.lower()
            items = tuple(
                item
                for item in items
                if lowered in str(item.get("title") or "").lower()
                or lowered in json.dumps(item.get("raw") or {}, ensure_ascii=False).lower()
            )
        _append_workspace_task_observations_from_items(request, self.db, items)
        return ProviderResult(
            source="task",
            status="success",
            result_type="task_list",
            count=len(items),
            items=items,
            metadata={
                "operation": operation,
                "credential_mode": "TENANT_TOKEN",
                "actor_identity": "BOT",
                "execution_identity_contract": identity_contract,
                "provider_boundary": "enterprise_realtime",
                "legacy_cli_fallback_used": False,
                "fallback_used": False,
                "workevent_as_realtime_source": False,
                "extracted_item_as_realtime_source": False,
                "query": query,
                "raw": payload,
            },
            answer=_task_list_answer(items, query=query),
            error="",
        )

    def _execute_task_query_with_user_fallback(
        self,
        request: ProviderRequest,
        *,
        operation: str,
        params: dict[str, Any],
    ) -> ProviderResult | None:
        if not _allows_self_user_query_fallback(request):
            return None

        company_id = request.context.runtime_scope.active_company_id
        app_config = _active_feishu_app_config(self.db, company_id)
        token_resolution = _run_async(
            resolve_feishu_user_access_token(
                self.db,
                company_id=company_id,
                open_id=request.context.identity.open_id,
                app_config=app_config,
            )
        )
        if not token_resolution.authorized:
            return None
        if app_config is None:
            return ProviderResult(
                source="task",
                status="error",
                result_type="task_list",
                metadata={
                    **_user_query_fallback_metadata(request, token_resolution, operation=operation),
                    "authorization_error": "missing_feishu_app_config",
                },
                answer="没有找到当前公司的飞书应用配置，暂时不能读取你的任务。",
                error="missing_feishu_app_config",
            )

        try:
            payload = _run_async(
                FeishuTaskService(app_config).list_tasks(
                    page_size=int(params.get("page_size") or 50),
                    page_token=str(params.get("page_token") or "") or None,
                    user_access_token=token_resolution.user_access_token,
                )
            )
        except Exception as exc:
            return ProviderResult(
                source="task",
                status="error",
                result_type="task_list",
                metadata={
                    **_user_query_fallback_metadata(request, token_resolution, operation=operation),
                    "error_type": "provider_execution_failed",
                    "tool_error": str(exc),
                },
                answer="个人任务读取失败，原始错误已记录到 Runtime Result。",
                error=str(exc),
            )

        data = _feishu_response_data(payload)
        raw_items = _items_from_payload(data)
        items = tuple(_task_item(item) for item in raw_items)
        query = str(params.get("query") or params.get("keyword") or "").strip()
        if operation == "search_tasks" and query:
            lowered = query.lower()
            items = tuple(
                item
                for item in items
                if lowered in str(item.get("title") or "").lower()
                or lowered in json.dumps(item.get("raw") or {}, ensure_ascii=False).lower()
            )
        _append_workspace_task_observations_from_items(request, self.db, items)
        return ProviderResult(
            source="task",
            status="success",
            result_type="task_list",
            count=len(items),
            items=items,
            metadata={
                **_user_query_fallback_metadata(request, token_resolution, operation=operation),
                "query": query,
                "raw": payload,
            },
            answer=_task_list_answer(items, query=query),
            error="",
        )

    def _execute_task_complete_with_user_token(
        self,
        request: ProviderRequest,
        *,
        params: dict[str, Any],
    ) -> ProviderResult:
        task_guid = str(params.get("task_guid") or params.get("guid") or params.get("task_id") or "").strip()
        if not task_guid:
            return _missing_params_result(
                source="task",
                result_type="task_complete",
                operation=request.operation,
                missing=("task_guid",),
                answer="完成任务还缺少任务 ID。",
                error="missing_task_guid",
            )

        company_id = request.context.runtime_scope.active_company_id
        app_config = _active_feishu_app_config(self.db, company_id)
        token_resolution = _run_async(
            resolve_feishu_user_access_token(
                self.db,
                company_id=company_id,
                open_id=request.context.identity.open_id,
                app_config=app_config,
            )
        )
        identity_contract = request.execution_identity_contract.payload()
        identity_contract["authorization_status"] = token_resolution.authorization_status
        if not token_resolution.authorized:
            return ProviderResult(
                source="task",
                status="denied",
                result_type="waiting_authorization",
                metadata={
                    "operation": request.operation,
                    "credential_mode": "USER_TOKEN",
                    "authorization_status": token_resolution.authorization_status,
                    "authorization_error": token_resolution.error,
                    "execution_identity_contract": identity_contract,
                    "waiting_authorization": True,
                    "provider_boundary": "user_token_required",
                },
                answer="完成任务需要本人飞书授权。请先完成飞书用户授权后再执行。",
                error=token_resolution.error or "missing_user_token",
            )

        if app_config is None:
            return ProviderResult(
                source="task",
                status="error",
                result_type="task_complete",
                metadata={
                    "operation": request.operation,
                    "credential_mode": "USER_TOKEN",
                    "authorization_status": token_resolution.authorization_status,
                    "authorization_error": "missing_feishu_app_config",
                    "execution_identity_contract": identity_contract,
                },
                answer="没有找到当前公司的飞书应用配置，暂时不能完成任务。",
                error="missing_feishu_app_config",
            )

        try:
            complete_task = FeishuTaskService(app_config).complete_task
            payload = _run_async(
                complete_task(
                    task_guid=task_guid,
                    completed_at=_task_completed_at(params),
                    user_id_type=str(params.get("user_id_type") or "open_id"),
                    user_access_token=token_resolution.user_access_token,
                )
            )
        except Exception as exc:
            return ProviderResult(
                source="task",
                status="error",
                result_type="task_complete",
                metadata={
                    "operation": request.operation,
                    "credential_mode": "USER_TOKEN",
                    "authorization_status": token_resolution.authorization_status,
                    "authorization_error": "",
                    "execution_identity_contract": identity_contract,
                    "error_type": "provider_execution_failed",
                    "tool_error": str(exc),
                },
                answer="任务完成失败，原始错误已记录到 Runtime Result。",
                error=str(exc),
            )

        data = _feishu_response_data(payload)
        raw_task = data.get("task") if isinstance(data, dict) and isinstance(data.get("task"), dict) else {"guid": task_guid}
        item = _task_item(raw_task)
        title = str(item.get("title") or task_guid)
        return ProviderResult(
            source="task",
            status="success",
            result_type="task_complete",
            count=1,
            items=(item,),
            metadata={
                "operation": request.operation,
                "credential_mode": "USER_TOKEN",
                "authorization_status": token_resolution.authorization_status,
                "account_id": token_resolution.account_id,
                "execution_identity_contract": identity_contract,
                "raw": payload,
            },
            answer=f"任务已完成：{title}",
            error="",
        )

    def _execute_task_tool(
        self,
        request: ProviderRequest,
        *,
        tool_name: str,
        operation: str,
        is_write: bool,
        params: dict[str, Any],
    ) -> ProviderResult:
        result = self._execute_tool(
            request,
            tool_name=tool_name,
            confirm_write=is_write,
            params=params,
        )
        status = _provider_status(result)
        payload = _tool_payload(result)
        items = _items_from_payload(payload)
        return ProviderResult(
            source="task",
            status=status,
            result_type=_task_result_type(operation),
            count=len(items),
            items=tuple(items),
            metadata={
                "operation": operation,
                "tool_name": tool_name,
                "write": is_write,
                "raw": payload,
                **_provider_error_metadata(result),
            },
            answer=result.answer if status == "success" else _tool_failure_answer("任务操作", result),
            error=result.error or "",
        )


def _task_result_type(operation: str) -> str:
    if operation == "complete_task":
        return "task_complete"
    return operation


def _task_completed_at(params: dict[str, Any]) -> str:
    completed_at = str(params.get("completed_at") or "").strip()
    if completed_at:
        return completed_at
    return str(int(datetime.now(UTC).timestamp() * 1000))


def _calendar_time_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict) and value:
        return value
    if value is None:
        raise ValueError("Feishu calendar create requires start/end time.")
    if isinstance(value, (int, float)):
        return {"timestamp": str(int(value))}
    text = str(value).strip()
    if text.isdigit():
        return {"timestamp": text}
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Feishu calendar create requires ISO datetime or timestamp for start/end time.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return {"timestamp": str(int(parsed.timestamp()))}


class FeishuCalendarProvider(FeishuResourceProvider):
    source = "calendar"

    _OPERATIONS: dict[str, tuple[str | None, bool]] = {
        "list_events": ("calendar_qa", False),
        "create_event": ("feishu_calendar_create_event", True),
        "update_event": (None, True),
        "delete_event": (None, True),
        "freebusy": (None, False),
        "find_room": (None, False),
        "rsvp": (None, True),
        "suggest_time": (None, False),
        "attendee_add": (None, True),
        "attendee_remove": (None, True),
    }

    def execute(self, request: ProviderRequest) -> ProviderResult:
        tool_spec = self._OPERATIONS.get(request.operation)
        if tool_spec is None:
            return _unsupported_operation_result("calendar", request.operation, sorted(self._OPERATIONS))
        tool_name, is_write = tool_spec
        if tool_name is None:
            return _tool_not_installed_result("calendar", request.operation)
        if request.operation == "list_events":
            query_path = _operational_query_read_path(request, source="calendar")
            if query_path == "self_user_token_required":
                user_fallback = self._execute_calendar_query_with_user_fallback(request)
                if user_fallback is not None:
                    return user_fallback
                tool_result = self._execute_calendar_query_tool_if_allowed(
                    request,
                    tool_name=tool_name,
                    query_path=query_path,
                )
                if tool_result is not None and tool_result.status == "success":
                    return tool_result
                boundary = _enterprise_realtime_boundary_result(
                    request,
                    source="calendar",
                    result_type="calendar_event_list",
                    operation=request.operation,
                    current_provider="calendar_qa",
                    user_fallback_allowed=True,
                )
                if boundary is not None:
                    return boundary
            elif query_path == "tenant_query_not_integrated":
                cognitive_aggregation = _workspace_cognitive_aggregation_result(
                    self.db,
                    request,
                    source="calendar",
                    operation=request.operation,
                )
                if cognitive_aggregation is not None:
                    return cognitive_aggregation
                tool_result = self._execute_calendar_query_tool_if_allowed(
                    request,
                    tool_name=tool_name,
                    query_path=query_path,
                )
                if tool_result is not None and tool_result.status == "success":
                    return tool_result
                boundary = _enterprise_realtime_boundary_result(
                    request,
                    source="calendar",
                    result_type="calendar_event_list",
                    operation=request.operation,
                    current_provider="calendar_qa",
                    user_fallback_allowed=False,
                )
                if boundary is not None:
                    return boundary
            else:
                tenant_result = self._execute_calendar_query_with_tenant_token(request)
                if tenant_result is not None:
                    return tenant_result
            user_fallback = self._execute_calendar_query_with_user_fallback(request)
            if user_fallback is not None:
                return user_fallback
            tool_result = self._execute_calendar_query_tool_if_allowed(
                request,
                tool_name=tool_name,
                query_path=query_path,
            )
            if tool_result is not None and tool_result.status == "success":
                return tool_result
            boundary = _enterprise_realtime_boundary_result(
                request,
                source="calendar",
                result_type="calendar_event_list",
                operation=request.operation,
                current_provider="calendar_qa",
                user_fallback_allowed=True,
            )
            if boundary is not None:
                return boundary
            return tool_result or self._execute_calendar_query_tool(request, tool_name=tool_name)
        if request.operation == "create_event":
            return self._execute_calendar_create_with_user_token(request, params=_calendar_tool_params(request))
        return ProviderResult(
            source="calendar",
            status="error",
            result_type="calendar_operation_not_supported",
            metadata={
                "operation": request.operation,
                "error_type": "unsupported_operation",
                "available_operations": sorted(self._OPERATIONS),
            },
            answer=f"日程能力已进入 V5，但这个操作还没有接入：{request.operation}。",
            error=f"unsupported_operation:{request.operation}",
        )

    def _execute_calendar_query_tool_if_allowed(
        self,
        request: ProviderRequest,
        *,
        tool_name: str,
        query_path: str,
    ) -> ProviderResult | None:
        if query_path == "self_user_token_required":
            actor = _bot_actor_from_runtime_context(request.context)
            if not _has_authorized_user_identity_bundle(self.db, request) and not (
                actor.can_query_company
                and _runtime_identity_has_company_domain(request.context.identity)
                and not _message_explicit_self_scope(request.context.current_message)
            ):
                return None
        if query_path == "tenant_query_not_integrated":
            actor = _bot_actor_from_runtime_context(request.context)
            if not actor.can_query_company or not _runtime_identity_has_company_domain(request.context.identity):
                return None
        return self._execute_calendar_query_tool(request, tool_name=tool_name)

    def _execute_calendar_query_tool(self, request: ProviderRequest, *, tool_name: str) -> ProviderResult:
        result = self._execute_tool(
            request,
            tool_name=tool_name,
            params={**_calendar_query_tool_params(request), "response_format": "raw_json"},
        )
        status = _provider_status(result)
        payload = _tool_payload(result)
        raw_items = _items_from_payload(payload)
        items = tuple(_calendar_item(item) for item in raw_items)
        answer = _calendar_list_answer(items)
        if not items and status == "success" and str(result.answer or "").strip():
            answer = str(result.answer).strip()
        return ProviderResult(
            source="calendar",
            status=status,
            result_type="calendar_event_list",
            count=len(items),
            items=items,
            metadata={"operation": request.operation, "tool_name": tool_name, **_provider_error_metadata(result)},
            answer=answer,
            error=result.error or "",
        )

    def _execute_calendar_query_with_tenant_token(self, request: ProviderRequest) -> ProviderResult | None:
        identity_contract = request.execution_identity_contract.payload()
        if identity_contract.get("actor_identity") != "BOT" or identity_contract.get("credential_mode") != "TENANT_TOKEN":
            return None

        company_id = request.context.runtime_scope.active_company_id
        if self.db is None:
            return None
        app_config = _active_feishu_app_config(self.db, company_id)
        if app_config is None:
            return None

        params = _calendar_tool_params(request)
        try:
            payload = _run_async(
                FeishuCalendarService(app_config).list_primary_events(
                    start_time=_calendar_datetime(params.get("start_time") or params.get("start")),
                    end_time=_calendar_datetime(params.get("end_time") or params.get("end")),
                    page_size=int(params.get("page_size") or 50),
                    page_token=str(params.get("page_token") or "") or None,
                )
            )
        except Exception as exc:
            if str(request.intent.data_scope or "").lower() == "self":
                return None
            return ProviderResult(
                source="calendar",
                status="error",
                result_type="calendar_event_list",
                metadata={
                    "operation": request.operation,
                    "credential_mode": "TENANT_TOKEN",
                    "actor_identity": "BOT",
                    "execution_identity_contract": identity_contract,
                    "provider_boundary": "enterprise_realtime_read_failed",
                    "legacy_cli_fallback_used": False,
                    "fallback_used": False,
                    "workevent_as_realtime_source": False,
                    "extracted_item_as_realtime_source": False,
                    "error_type": "provider_execution_failed",
                    "tool_error": str(exc),
                },
                answer="日程企业实时读取失败。我不会改用本地认知数据或当前用户身份代查。",
                error=str(exc),
            )

        data = _feishu_response_data(payload)
        raw_items = _items_from_payload(data)
        items = tuple(_calendar_item(item) for item in raw_items)
        _append_workspace_calendar_observations_from_items(request, self.db, items)
        return ProviderResult(
            source="calendar",
            status="success",
            result_type="calendar_event_list",
            count=len(items),
            items=items,
            metadata={
                "operation": request.operation,
                "credential_mode": "TENANT_TOKEN",
                "actor_identity": "BOT",
                "execution_identity_contract": identity_contract,
                "provider_boundary": "enterprise_realtime",
                "legacy_cli_fallback_used": False,
                "fallback_used": False,
                "workevent_as_realtime_source": False,
                "extracted_item_as_realtime_source": False,
                "raw": payload,
            },
            answer=_calendar_list_answer(items),
            error="",
        )

    def _execute_calendar_query_with_user_fallback(self, request: ProviderRequest) -> ProviderResult | None:
        if not _allows_self_user_query_fallback(request):
            return None

        company_id = request.context.runtime_scope.active_company_id
        app_config = _active_feishu_app_config(self.db, company_id)
        token_resolution = _run_async(
            resolve_feishu_user_access_token(
                self.db,
                company_id=company_id,
                open_id=request.context.identity.open_id,
                app_config=app_config,
            )
        )
        if not token_resolution.authorized:
            return None
        if app_config is None:
            return ProviderResult(
                source="calendar",
                status="error",
                result_type="calendar_event_list",
                metadata={
                    **_user_query_fallback_metadata(request, token_resolution, operation=request.operation),
                    "authorization_error": "missing_feishu_app_config",
                },
                answer="没有找到当前公司的飞书应用配置，暂时不能读取你的日程。",
                error="missing_feishu_app_config",
            )

        params = _calendar_tool_params(request)
        try:
            payload = _run_async(
                FeishuCalendarService(app_config).list_primary_events(
                    start_time=_calendar_datetime(params.get("start_time") or params.get("start")),
                    end_time=_calendar_datetime(params.get("end_time") or params.get("end")),
                    page_size=int(params.get("page_size") or 50),
                    page_token=str(params.get("page_token") or "") or None,
                    user_access_token=token_resolution.user_access_token,
                )
            )
        except Exception as exc:
            return ProviderResult(
                source="calendar",
                status="error",
                result_type="calendar_event_list",
                metadata={
                    **_user_query_fallback_metadata(request, token_resolution, operation=request.operation),
                    "error_type": "provider_execution_failed",
                    "tool_error": str(exc),
                },
                answer="个人日程读取失败，原始错误已记录到 Runtime Result。",
                error=str(exc),
            )

        data = _feishu_response_data(payload)
        raw_items = _items_from_payload(data)
        items = tuple(_calendar_item(item) for item in raw_items)
        _append_workspace_calendar_observations_from_items(request, self.db, items)
        return ProviderResult(
            source="calendar",
            status="success",
            result_type="calendar_event_list",
            count=len(items),
            items=items,
            metadata={
                **_user_query_fallback_metadata(request, token_resolution, operation=request.operation),
                "raw": payload,
            },
            answer=_calendar_list_answer(items),
            error="",
        )

    def _execute_calendar_create_with_user_token(
        self,
        request: ProviderRequest,
        *,
        params: dict[str, Any],
    ) -> ProviderResult:
        missing = [key for key in ("summary", "start", "end") if not str(params.get(key) or "").strip()]
        if missing:
            return _missing_params_result(
                source="calendar",
                result_type="calendar_create",
                operation=request.operation,
                missing=missing,
                answer="创建日程还需要主题、开始时间和结束时间。你可以说：明天下午 3 点到 4 点创建一个会议，主题是项目进度。",
                error="missing_calendar_time",
            )

        company_id = request.context.runtime_scope.active_company_id
        app_config = _active_feishu_app_config(self.db, company_id)
        token_resolution = _run_async(
            resolve_feishu_user_access_token(
                self.db,
                company_id=company_id,
                open_id=request.context.identity.open_id,
                app_config=app_config,
            )
        )
        identity_contract = request.execution_identity_contract.payload()
        identity_contract["authorization_status"] = token_resolution.authorization_status
        if not token_resolution.authorized:
            return ProviderResult(
                source="calendar",
                status="denied",
                result_type="waiting_authorization",
                metadata={
                    "operation": request.operation,
                    "credential_mode": "USER_TOKEN",
                    "authorization_status": token_resolution.authorization_status,
                    "authorization_error": token_resolution.error,
                    "execution_identity_contract": identity_contract,
                    "waiting_authorization": True,
                    "provider_boundary": "user_token_required",
                },
                answer="创建日程需要本人飞书授权。请先完成飞书用户授权后再执行。",
                error=token_resolution.error or "missing_user_token",
            )

        if app_config is None:
            return ProviderResult(
                source="calendar",
                status="error",
                result_type="calendar_create",
                metadata={
                    "operation": request.operation,
                    "credential_mode": "USER_TOKEN",
                    "authorization_status": token_resolution.authorization_status,
                    "authorization_error": "missing_feishu_app_config",
                    "execution_identity_contract": identity_contract,
                },
                answer="没有找到当前公司的飞书应用配置，暂时不能创建日程。",
                error="missing_feishu_app_config",
            )

        summary = str(params.get("summary") or "").strip()
        try:
            payload = _run_async(
                FeishuCalendarService(app_config).create_event(
                    calendar_id=str(params.get("calendar_id") or "primary"),
                    summary=summary,
                    description=str(params.get("description") or "") or None,
                    start_time=_calendar_time_payload(params.get("start_time") or params.get("start")),
                    end_time=_calendar_time_payload(params.get("end_time") or params.get("end")),
                    recurrence=str(params.get("recurrence") or params.get("rrule") or "") or None,
                    attendee_ids=[str(item) for item in params.get("attendee_ids") or []] if isinstance(params.get("attendee_ids"), list) else None,
                    attendees=params.get("attendees") if isinstance(params.get("attendees"), list) else None,
                    user_id_type=str(params.get("user_id_type") or "open_id"),
                    need_notification=params.get("need_notification") is not False,
                    user_access_token=token_resolution.user_access_token,
                )
            )
        except Exception as exc:
            return ProviderResult(
                source="calendar",
                status="error",
                result_type="calendar_create",
                metadata={
                    "operation": request.operation,
                    "credential_mode": "USER_TOKEN",
                    "authorization_status": token_resolution.authorization_status,
                    "authorization_error": "",
                    "execution_identity_contract": identity_contract,
                    "error_type": "provider_execution_failed",
                    "tool_error": str(exc),
                },
                answer="日程创建失败，原始错误已记录到 Runtime Result。",
                error=str(exc),
            )

        data = _feishu_response_data(payload)
        event = data.get("event") if isinstance(data, dict) and isinstance(data.get("event"), dict) else data
        event_id = _first_nested_value(event, ("event_id", "id", "calendar_event_id"))
        url = _first_nested_value(event, ("url", "app_link", "link"))
        return ProviderResult(
            source="calendar",
            status="success",
            result_type="calendar_create",
            count=1,
            items=(
                {
                    "title": summary,
                    "start": params.get("start"),
                    "end": params.get("end"),
                    "event_id": event_id,
                    "url": url,
                },
            ),
            metadata={
                "operation": request.operation,
                "credential_mode": "USER_TOKEN",
                "authorization_status": token_resolution.authorization_status,
                "account_id": token_resolution.account_id,
                "execution_identity_contract": identity_contract,
                "summary": summary,
                "raw": payload,
                "operational_source": "feishu_realtime",
                "enterprise_write_through": "disabled",
            },
            answer=f"已创建日程：{summary}",
            error="",
        )


class FeishuMailProvider(FeishuResourceProvider):
    source = "mail"

    _OPERATIONS: dict[str, tuple[str | None, bool]] = {
        "list_recent": ("mail_qa", False),
        "search_messages": ("mail_qa", False),
        "get_message": ("feishu_mail_message_get", False),
        "create_draft": ("feishu_mail_drafts_create", True),
        "send_draft": (None, True),
        "send_message": (None, True),
        "reply": (None, True),
        "reply_all": (None, True),
        "forward": (None, True),
        "delete_message": (None, True),
        "move_message": (None, True),
        "mark_message": (None, True),
        "create_rule": (None, True),
        "update_rule": (None, True),
        "delete_rule": (None, True),
    }

    def execute(self, request: ProviderRequest) -> ProviderResult:
        tool_spec = self._OPERATIONS.get(request.operation)
        if tool_spec is None:
            return _unsupported_operation_result("mail", request.operation, sorted(self._OPERATIONS))
        tool_name, is_write = tool_spec
        if tool_name is None:
            return _tool_not_installed_result("mail", request.operation)
        if request.operation in {"list_recent", "search_messages"}:
            boundary = _enterprise_realtime_boundary_result(
                request,
                source="mail",
                result_type="mail_list",
                operation=request.operation,
                current_provider=str(tool_name),
                user_fallback_allowed=True,
            )
            if boundary is not None:
                return boundary
            params = _mail_tool_params(request)
            if request.operation == "search_messages":
                query = str(request.params.get("query") or request.params.get("keyword") or "").strip()
                if query:
                    params["query"] = query
            result = self._execute_tool(request, tool_name=tool_name, params=params)
            status = _provider_status(result)
            payload = _tool_payload(result)
            raw_items = _items_from_payload(payload)
            items = tuple(_mail_item(item) for item in raw_items)
            query = str(params.get("query") or params.get("keyword") or "")
            return ProviderResult(
                source="mail",
                status=status,
                result_type="mail_list",
                count=len(items),
                items=items,
                metadata={
                    "operation": request.operation,
                    "tool_name": tool_name,
                    "query": query,
                    **_provider_error_metadata(result),
                },
                answer=_mail_list_answer(items, query=query),
                error=result.error or "",
            )
        if request.operation == "create_draft":
            missing = [key for key in ("to", "subject", "body") if not str(request.params.get(key) or "").strip()]
            if missing:
                return _missing_params_result(
                    source="mail",
                    result_type="mail_draft_create",
                    operation=request.operation,
                    missing=missing,
                    answer="创建邮件草稿还需要收件人、主题和正文。我会先创建草稿，不会直接发送。",
                    error="missing_mail_draft_params",
                )
            params = _mail_tool_params(request)
            result = self._execute_tool(
                request,
                tool_name=tool_name,
                confirm_write=is_write,
                params=params,
            )
            status = _provider_status(result)
            payload = _tool_payload(result)
            draft_id = _first_nested_value(payload, ("draft_id", "id"))
            url = _first_nested_value(payload, ("url", "app_link", "link"))
            subject = str(request.params.get("subject") or "")
            return ProviderResult(
                source="mail",
                status=status,
                result_type="mail_draft_create",
                count=1 if status == "success" else 0,
                items=(
                    {
                        "draft_id": draft_id,
                        "subject": subject,
                        "to": request.params.get("to"),
                        "url": url,
                        "raw": payload,
                    },
                )
                if status == "success"
                else (),
                metadata={
                    "operation": request.operation,
                    "tool_name": tool_name,
                    "draft_id": draft_id,
                    "url": url,
                    **_provider_error_metadata(result),
                },
                answer=_mail_draft_answer(subject=subject, url=url) if status == "success" else _tool_failure_answer("邮件草稿创建", result),
                error=result.error or "",
            )
        return ProviderResult(
            source="mail",
            status="error",
            result_type="mail_operation_not_supported",
            metadata={
                "operation": request.operation,
                "error_type": "unsupported_operation",
                "available_operations": sorted(self._OPERATIONS),
            },
            answer=f"邮件能力已进入 V5，但这个操作还没有接入：{request.operation}。",
            error=f"unsupported_operation:{request.operation}",
        )


class PendingRuntimeProvider(FeishuResourceProvider):
    source = "pending"

    _OPERATIONS_BY_SOURCE: dict[str, dict[str, tuple[None, bool]]] = {
        "company_profile": {"read_profile": (None, False)},
        "workevent": {"summarize": (None, False), "risk_events": (None, False)},
        "memory": {"related_memory": (None, False)},
    }

    def __init__(self, *, source: str, db: Session, cli_profile: str | None = None) -> None:
        super().__init__(db=db, cli_profile=cli_profile)
        self.source = source
        self._OPERATIONS = self._OPERATIONS_BY_SOURCE.get(source, {})

    def execute(self, request: ProviderRequest) -> ProviderResult:
        return _tool_not_installed_result(self.source, request.operation)


class FeishuDocsProvider(FeishuResourceProvider):
    source = "docs"

    _OPERATIONS: dict[str, tuple[str, bool]] = {
        "read_doc": ("feishu_doc_raw_content", False),
        "edit_doc": (None, True),
    }

    def execute(self, request: ProviderRequest) -> ProviderResult:
        if request.operation == "edit_doc":
            return _tool_not_installed_result("docs", request.operation)
        if request.operation != "read_doc":
            return _unsupported_operation_result("docs", request.operation, sorted(self._OPERATIONS))
        document_id = _first_text_param(request.params, "document_id", "document_token", "doc_token", "token", "url") or _document_token_from_text(request.context.current_message)
        if "/" in document_id:
            document_id = _document_token_from_text(document_id)
        document_type = str(request.params.get("document_type") or _document_type_for_token(document_id) or "docx").strip()
        if not document_id:
            return _missing_params_result(
                source="docs",
                result_type="docs_read_empty",
                operation=request.operation,
                missing=["document_id"],
                answer="读取飞书文档需要文档 token 或链接。你可以把文档链接发给我。",
                error="missing_document_id",
            )
        app_config = _active_feishu_app_config(self.db, request.context.runtime_scope.active_company_id)
        if app_config is None:
            return _missing_params_result(
                source="docs",
                result_type="docs_read_empty",
                operation=request.operation,
                missing=["app_config"],
                answer="没有找到当前公司的飞书应用配置，暂时不能读取文档。",
                error="missing_feishu_app_config",
            )
        try:
            payload = _run_async(FeishuDriveService(app_config).get_document_content(document_id=document_id, document_type=document_type))
        except Exception as exc:
            return ProviderResult(
                source="docs",
                status="error",
                result_type="docs_read",
                metadata={"operation": request.operation, "tool_name": "feishu_doc_raw_content", "error_type": "tool_execution_failed", "tool_error": str(exc)},
                answer="飞书文档读取失败，原始诊断已记录到 V5 状态里。",
                error=str(exc),
            )
        text = str(payload.get("content_text") or "").strip()
        item = {
            "document_id": document_id,
            "document_type": document_type,
            "title": document_id,
            "content_preview": text[:1200],
            "available": bool(payload.get("available")),
        }
        return ProviderResult(
            source="docs",
            status="success" if payload.get("available") else "error",
            result_type="docs_read",
            count=1 if payload.get("available") else 0,
            items=(item,) if payload.get("available") else (),
            metadata={"operation": request.operation, "tool_name": "feishu_doc_raw_content", "document_type": document_type},
            answer=_docs_read_answer(item) if payload.get("available") else str(payload.get("error") or "文档读取失败。"),
            error="" if payload.get("available") else str(payload.get("error") or "docs_read_failed"),
        )


class FeishuWikiProvider(FeishuResourceProvider):
    source = "wiki"

    _OPERATIONS: dict[str, tuple[str, bool]] = {"search_wiki": ("feishu_wiki_space_or_node_list", False)}

    def execute(self, request: ProviderRequest) -> ProviderResult:
        if request.operation != "search_wiki":
            return _unsupported_operation_result("wiki", request.operation, sorted(self._OPERATIONS))
        app_config = _active_feishu_app_config(self.db, request.context.runtime_scope.active_company_id)
        if app_config is None:
            return _missing_params_result(
                source="wiki",
                result_type="wiki_search_empty",
                operation=request.operation,
                missing=["app_config"],
                answer="没有找到当前公司的飞书应用配置，暂时不能查询知识库。",
                error="missing_feishu_app_config",
            )
        service = FeishuDriveService(app_config)
        space_id = _first_text_param(request.params, "space_id", "wiki_space_id")
        try:
            if space_id:
                payload = _run_async(service.list_wiki_nodes(space_id=space_id, page_size=20))
                items = tuple(_wiki_node_item(item, space_id=space_id) for item in _items_from_payload(payload))
                result_type = "wiki_node_list"
                answer = _wiki_nodes_answer(items, space_id=space_id)
            else:
                payload = _run_async(service.list_wiki_spaces(page_size=20))
                items = tuple(_wiki_space_item(item) for item in _items_from_payload(payload))
                result_type = "wiki_space_list"
                answer = _wiki_spaces_answer(items)
        except Exception as exc:
            return ProviderResult(
                source="wiki",
                status="error",
                result_type="wiki_search",
                metadata={"operation": request.operation, "tool_name": "feishu_wiki_space_or_node_list", "error_type": "tool_execution_failed", "tool_error": str(exc)},
                answer="飞书知识库查询失败，原始诊断已记录到 V5 状态里。",
                error=str(exc),
            )
        return ProviderResult(
            source="wiki",
            status="success",
            result_type=result_type,
            count=len(items),
            items=items,
            metadata={"operation": request.operation, "tool_name": "feishu_wiki_space_or_node_list", "space_id": space_id},
            answer=answer,
        )


class FeishuDriveProvider(FeishuResourceProvider):
    source = "drive"

    _OPERATIONS: dict[str, tuple[Any, bool]] = {
        "list_files": ("feishu_drive_file_list", False),
        "upload_file": (None, True),
    }

    def execute(self, request: ProviderRequest) -> ProviderResult:
        if request.operation == "upload_file":
            return _tool_not_installed_result("drive", request.operation)
        if request.operation != "list_files":
            return _unsupported_operation_result("drive", request.operation, sorted(self._OPERATIONS))
        app_config = _active_feishu_app_config(self.db, request.context.runtime_scope.active_company_id)
        if app_config is None:
            return _missing_params_result(
                source="drive",
                result_type="drive_file_list_empty",
                operation=request.operation,
                missing=["app_config"],
                answer="没有找到当前公司的飞书应用配置，暂时不能查询云盘文件。",
                error="missing_feishu_app_config",
            )
        folder_token = _first_text_param(request.params, "folder_token", "folder_id")
        try:
            payload = _run_async(FeishuDriveService(app_config).list_files(page_size=20, folder_token=folder_token or None))
        except Exception as exc:
            return ProviderResult(
                source="drive",
                status="error",
                result_type="drive_file_list",
                metadata={"operation": request.operation, "tool_name": "feishu_drive_file_list", "error_type": "tool_execution_failed", "tool_error": str(exc)},
                answer="飞书云盘文件查询失败，原始诊断已记录到 V5 状态里。",
                error=str(exc),
            )
        data = _feishu_response_data(payload)
        raw_items = _items_from_payload(data if isinstance(data, dict) else payload)
        items = tuple(_drive_file_item(item) for item in raw_items)
        return ProviderResult(
            source="drive",
            status="success",
            result_type="drive_file_list",
            count=len(items),
            items=items,
            metadata={"operation": request.operation, "tool_name": "feishu_drive_file_list", "folder_token": folder_token},
            answer=_drive_files_answer(items),
        )


class FeishuVcProvider(FeishuResourceProvider):
    source = "vc"

    _OPERATIONS: dict[str, tuple[str, bool]] = {"search_meetings": ("feishu_vc_meeting_search", False)}

    def execute(self, request: ProviderRequest) -> ProviderResult:
        if request.operation != "search_meetings":
            return _unsupported_operation_result("vc", request.operation, sorted(self._OPERATIONS))
        app_config = _active_feishu_app_config(self.db, request.context.runtime_scope.active_company_id)
        if app_config is None:
            return _missing_params_result(
                source="vc",
                result_type="vc_meeting_list_empty",
                operation=request.operation,
                missing=["app_config"],
                answer="没有找到当前公司的飞书应用配置，暂时不能查询历史会议。",
                error="missing_feishu_app_config",
            )
        try:
            payload = _run_async(FeishuMeetingService(app_config).list_meetings(page_size=20))
        except Exception as exc:
            return ProviderResult(
                source="vc",
                status="error",
                result_type="vc_meeting_list",
                metadata={"operation": request.operation, "tool_name": "feishu_vc_meeting_search", "error_type": "tool_execution_failed", "tool_error": str(exc)},
                answer="飞书历史会议查询失败，原始诊断已记录到 V5 状态里。",
                error=str(exc),
            )
        data = _feishu_response_data(payload)
        raw_items = _items_from_payload(data if isinstance(data, dict) else payload)
        items = tuple(_vc_meeting_item(item) for item in raw_items)
        return ProviderResult(
            source="vc",
            status="success",
            result_type="vc_meeting_list",
            count=len(items),
            items=items,
            metadata={"operation": request.operation, "tool_name": "feishu_vc_meeting_search"},
            answer=_vc_meetings_answer(items),
        )


class FeishuAttendanceProvider(FeishuResourceProvider):
    source = "attendance"

    _OPERATIONS: dict[str, tuple[str, bool]] = {"query_records": ("lark-cli attendance user_tasks query", False)}

    def execute(self, request: ProviderRequest) -> ProviderResult:
        if request.operation != "query_records":
            return _unsupported_operation_result("attendance", request.operation, sorted(self._OPERATIONS))
        boundary = _enterprise_realtime_boundary_result(
            request,
            source="attendance",
            result_type="attendance_record_list",
            operation=request.operation,
            current_provider="lark-cli attendance user_tasks query",
            user_fallback_allowed=True,
        )
        if boundary is not None:
            return boundary
        start_date = _first_text_param(request.params, "start_date", "check_date_from") or (date.today() - timedelta(days=7)).isoformat()
        end_date = _first_text_param(request.params, "end_date", "check_date_to") or date.today().isoformat()
        data = {
            "employee_type": "employee_no",
            "user_ids": [],
            "check_date_from": start_date,
            "check_date_to": end_date,
        }
        try:
            payload = _run_lark_cli_json(
                [
                    "lark-cli",
                    "attendance",
                    "user_tasks",
                    "query",
                    "--as",
                    "user",
                    "--format",
                    "json",
                    "--data",
                    json.dumps(data, ensure_ascii=False),
                ],
                cli_profile=self.cli_profile,
                action="飞书考勤查询",
            )
        except Exception as exc:
            return ProviderResult(
                source="attendance",
                status="error",
                result_type="attendance_record_list",
                count=0,
                metadata={
                    "operation": request.operation,
                    "tool_name": "lark-cli attendance user_tasks query",
                    "start_date": start_date,
                    "end_date": end_date,
                    "error_type": "tool_execution_failed",
                    "tool_error": str(exc),
                },
                answer="飞书考勤查询失败，可能需要补充用户授权或考勤只读权限。",
                error=str(exc),
            )
        raw_items = _attendance_items_from_payload(_feishu_response_data(payload))
        items = tuple(_attendance_item(item) for item in raw_items)
        return ProviderResult(
            source="attendance",
            status="success",
            result_type="attendance_record_list",
            count=len(items),
            items=items,
            metadata={
                "operation": request.operation,
                "tool_name": "lark-cli attendance user_tasks query",
                "start_date": start_date,
                "end_date": end_date,
            },
            answer=_attendance_answer(items, start_date=start_date, end_date=end_date),
        )


class FeishuOkrProvider(FeishuResourceProvider):
    source = "okr"

    _OPERATIONS: dict[str, tuple[Any, bool]] = {
        "list_objectives": ("feishu_okr_objective_list", False),
        "update_progress": (None, True),
    }

    def execute(self, request: ProviderRequest) -> ProviderResult:
        if request.operation == "update_progress":
            return _tool_not_installed_result("okr", request.operation)
        if request.operation != "list_objectives":
            return _unsupported_operation_result("okr", request.operation, sorted(self._OPERATIONS))
        app_config = _active_feishu_app_config(self.db, request.context.runtime_scope.active_company_id)
        if app_config is None:
            return _missing_params_result(
                source="okr",
                result_type="okr_objective_list_empty",
                operation=request.operation,
                missing=["app_config"],
                answer="没有找到当前公司的飞书应用配置，暂时不能查询 OKR。",
                error="missing_feishu_app_config",
            )
        open_id = str(request.params.get("user_id") or request.context.identity.open_id or "").strip()
        if not open_id:
            return _missing_params_result(
                source="okr",
                result_type="okr_objective_list_empty",
                operation=request.operation,
                missing=["user_id"],
                answer="查询 OKR 需要先识别当前用户身份。",
                error="missing_user_id",
            )
        service = FeishuOkrService(app_config)
        try:
            cycle_payload = _run_async(service.list_cycles(user_id=open_id, user_id_type="open_id", page_size=20))
            cycles = tuple(_okr_cycle_item(item) for item in _okr_cycles_from_payload(_feishu_response_data(cycle_payload)))
            cycle = _active_okr_cycle(cycles)
            if not cycle:
                return ProviderResult(
                    source="okr",
                    status="success",
                    result_type="okr_objective_list",
                    count=0,
                    items=(),
                    metadata={"operation": request.operation, "tool_name": "feishu_okr_cycle_list", "user_id": open_id},
                    answer="没有查到当前用户可见的 OKR 周期。",
                )
            objective_payload = _run_async(service.list_objectives(cycle_id=str(cycle.get("cycle_id") or ""), page_size=100))
        except Exception as exc:
            return ProviderResult(
                source="okr",
                status="error",
                result_type="okr_objective_list",
                count=0,
                metadata={
                    "operation": request.operation,
                    "tool_name": "feishu_okr_objective_list",
                    "user_id": open_id,
                    "error_type": "tool_execution_failed",
                    "tool_error": str(exc),
                },
                answer="飞书 OKR 查询失败，可能需要补充 OKR 只读权限或用户授权。",
                error=str(exc),
            )
        objectives = tuple(
            _okr_objective_item(item, cycle=cycle)
            for item in _okr_objectives_from_payload(_feishu_response_data(objective_payload))
        )
        return ProviderResult(
            source="okr",
            status="success",
            result_type="okr_objective_list",
            count=len(objectives),
            items=objectives,
            metadata={
                "operation": request.operation,
                "tool_name": "feishu_okr_objective_list",
                "user_id": open_id,
                "cycle": cycle,
                "cycle_count": len(cycles),
            },
            answer=_okr_objectives_answer(objectives, cycle=cycle),
        )


class FeishuSlidesProvider(FeishuResourceProvider):
    source = "slides"

    _OPERATIONS: dict[str, tuple[Any, bool]] = {
        "read_slides": ("lark-cli slides xml_presentations get", False),
        "write_slides": (None, True),
    }

    def execute(self, request: ProviderRequest) -> ProviderResult:
        if request.operation == "write_slides":
            return _tool_not_installed_result("slides", request.operation)
        if request.operation != "read_slides":
            return _unsupported_operation_result("slides", request.operation, sorted(self._OPERATIONS))
        boundary = _enterprise_realtime_boundary_result(
            request,
            source="slides",
            result_type="slides_read",
            operation=request.operation,
            current_provider="lark-cli slides xml_presentations get",
            user_fallback_allowed=False,
        )
        if boundary is not None:
            return boundary
        presentation_id = _first_text_param(request.params, "xml_presentation_id", "presentation_id", "slides_token") or _slides_token_from_text(request.context.current_message)
        if not presentation_id:
            return _missing_params_result(
                source="slides",
                result_type="slides_read_empty",
                operation=request.operation,
                missing=["xml_presentation_id"],
                answer="读取飞书幻灯片需要提供 slides 链接或演示文稿 ID。",
                error="missing_xml_presentation_id",
            )
        try:
            payload = _run_lark_cli_json(
                [
                    "lark-cli",
                    "slides",
                    "xml_presentations",
                    "get",
                    "--as",
                    "user",
                    "--format",
                    "json",
                    "--params",
                    json.dumps({"xml_presentation_id": presentation_id}, ensure_ascii=False),
                ],
                cli_profile=self.cli_profile,
                action="飞书幻灯片读取",
            )
        except Exception as exc:
            return ProviderResult(
                source="slides",
                status="error",
                result_type="slides_read",
                count=0,
                metadata={
                    "operation": request.operation,
                    "tool_name": "lark-cli slides xml_presentations get",
                    "xml_presentation_id": presentation_id,
                    "error_type": "tool_execution_failed",
                    "tool_error": str(exc),
                },
                answer="飞书幻灯片读取失败，可能需要补充用户授权或文档访问权限。",
                error=str(exc),
            )
        item = _slides_item(_feishu_response_data(payload), presentation_id=presentation_id)
        return ProviderResult(
            source="slides",
            status="success",
            result_type="slides_read",
            count=1,
            items=(item,),
            metadata={
                "operation": request.operation,
                "tool_name": "lark-cli slides xml_presentations get",
                "xml_presentation_id": presentation_id,
            },
            answer=_slides_read_answer(item),
        )


class FeishuWhiteboardProvider(FeishuResourceProvider):
    source = "whiteboard"

    _OPERATIONS: dict[str, tuple[Any, bool]] = {
        "read_whiteboard": ("lark-cli whiteboard +query", False),
        "write_whiteboard": (None, True),
    }

    def execute(self, request: ProviderRequest) -> ProviderResult:
        if request.operation == "write_whiteboard":
            return _tool_not_installed_result("whiteboard", request.operation)
        if request.operation != "read_whiteboard":
            return _unsupported_operation_result("whiteboard", request.operation, sorted(self._OPERATIONS))
        boundary = _enterprise_realtime_boundary_result(
            request,
            source="whiteboard",
            result_type="whiteboard_read",
            operation=request.operation,
            current_provider="lark-cli whiteboard +query",
            user_fallback_allowed=False,
        )
        if boundary is not None:
            return boundary
        whiteboard_token = _first_text_param(request.params, "whiteboard_token", "board_token") or _whiteboard_token_from_text(request.context.current_message)
        if not whiteboard_token:
            return _missing_params_result(
                source="whiteboard",
                result_type="whiteboard_read_empty",
                operation=request.operation,
                missing=["whiteboard_token"],
                answer="读取飞书画板需要提供画板 token。",
                error="missing_whiteboard_token",
            )
        try:
            output = _run_lark_cli_text(
                [
                    "lark-cli",
                    "whiteboard",
                    "+query",
                    "--as",
                    "user",
                    "--whiteboard-token",
                    whiteboard_token,
                    "--output_as",
                    "code",
                ],
                cli_profile=self.cli_profile,
                action="飞书画板读取",
            )
        except Exception as exc:
            return ProviderResult(
                source="whiteboard",
                status="error",
                result_type="whiteboard_read",
                count=0,
                metadata={
                    "operation": request.operation,
                    "tool_name": "lark-cli whiteboard +query",
                    "whiteboard_token": whiteboard_token,
                    "error_type": "tool_execution_failed",
                    "tool_error": str(exc),
                },
                answer="飞书画板读取失败，可能需要补充用户授权或画板访问权限。",
                error=str(exc),
            )
        item = _whiteboard_item(output, whiteboard_token=whiteboard_token)
        return ProviderResult(
            source="whiteboard",
            status="success",
            result_type="whiteboard_read",
            count=1,
            items=(item,),
            metadata={
                "operation": request.operation,
                "tool_name": "lark-cli whiteboard +query",
                "whiteboard_token": whiteboard_token,
            },
            answer=_whiteboard_read_answer(item),
        )


class PendingSkillProvider(FeishuResourceProvider):
    _OPERATIONS_BY_SOURCE: dict[str, dict[str, tuple[None, bool]]] = {
        "docs": {"read_doc": (None, False), "edit_doc": (None, True)},
        "wiki": {"search_wiki": (None, False)},
        "drive": {"upload_file": (None, True)},
        "sheets": {"read_cells": (None, False), "create_sheet": (None, True), "write_cells": (None, True)},
        "note": {"read_note": (None, False)},
        "minutes": {"read_minutes": (None, False)},
        "markdown": {"read_markdown": (None, False), "write_markdown": (None, True)},
        "apps": {"deploy_app": (None, True)},
        "openapi": {"explore_api": (None, False)},
        "slides": {"write_slides": (None, True)},
        "whiteboard": {"write_whiteboard": (None, True)},
        "vc_agent": {"read_live_events": (None, False), "join_meeting": (None, True)},
    }

    _LABELS: dict[str, str] = {
        "docs": "飞书文档",
        "wiki": "飞书知识库",
        "drive": "飞书云盘",
        "sheets": "飞书电子表格",
        "note": "飞书会议纪要",
        "minutes": "飞书妙记",
        "markdown": "飞书 Markdown",
        "apps": "飞书妙搭应用",
        "openapi": "飞书原生接口探索",
        "slides": "飞书幻灯片",
        "whiteboard": "飞书画板",
        "vc_agent": "飞书会中能力",
    }

    def __init__(self, *, source: str, db: Session, cli_profile: str | None = None) -> None:
        super().__init__(db=db, cli_profile=cli_profile)
        self.source = source
        self._OPERATIONS = self._OPERATIONS_BY_SOURCE.get(source, {})

    def execute(self, request: ProviderRequest) -> ProviderResult:
        if request.operation not in self._OPERATIONS:
            return _unsupported_operation_result(self.source, request.operation, sorted(self._OPERATIONS))
        return ProviderResult(
            source=self.source,
            status="error",
            result_type=f"{self.source}_operation_not_open",
            count=0,
            metadata={
                "operation": request.operation,
                "error_type": "capability_not_installed",
                "pending_reason": "skill_registered_provider_pending",
                "recommended_next_step": f"把{self._LABELS.get(self.source, self.source)}的 {request.operation} 接成 V5 Provider 真实执行，并补权限、确认和 action_receipt。",
                "v5_only": True,
                "legacy_fallback": False,
            },
            answer=f"{self._LABELS.get(self.source, self.source)}能力已登记到 V5，但还没有开放执行。我不会回退旧系统，以免产生不可控动作。",
            error=f"{self.source}_skill_provider_pending:{request.operation}",
        )


class CompanyProfileProvider(FeishuResourceProvider):
    source = "company_profile"

    _OPERATIONS: dict[str, tuple[str, bool]] = {"read_profile": ("local_company_profile", False)}

    def execute(self, request: ProviderRequest) -> ProviderResult:
        if request.operation != "read_profile":
            return _unsupported_operation_result("company_profile", request.operation, sorted(self._OPERATIONS))
        company_id = request.context.runtime_scope.active_company_id
        company = self.db.get(Company, company_id) if company_id else None
        if company is None:
            return ProviderResult(
                source="company_profile",
                status="error",
                result_type="company_profile",
                metadata={"operation": request.operation, "error_type": "missing_company"},
                answer="没有找到当前公司的档案信息。",
                error="missing_company",
            )
        item = {
            "name": company.name,
            "code": company.code,
            "status": company.status,
            "metadata": company.metadata_json or {},
        }
        return ProviderResult(
            source="company_profile",
            status="success",
            result_type="company_profile",
            count=1,
            items=(item,),
            metadata={"operation": request.operation, "tool_name": "local_company_profile"},
            answer=_company_profile_answer(item),
        )


class WorkEventProvider(FeishuResourceProvider):
    source = "workevent"

    _OPERATIONS: dict[str, tuple[str, bool]] = {
        "summarize": ("local_workevent_recent", False),
        "risk_events": ("local_workevent_risks", False),
    }

    def execute(self, request: ProviderRequest) -> ProviderResult:
        if request.operation not in self._OPERATIONS:
            return _unsupported_operation_result("workevent", request.operation, sorted(self._OPERATIONS))
        company_id = request.context.runtime_scope.active_company_id
        query = (
            select(WorkEvent)
            .where(WorkEvent.company_id == company_id)
            .order_by(WorkEvent.occurred_at.desc())
            .limit(10)
        )
        if request.operation == "risk_events":
            query = query.where(
                or_(
                    WorkEvent.event_type.ilike("%risk%"),
                    WorkEvent.title.ilike("%风险%"),
                    WorkEvent.title.ilike("%异常%"),
                    WorkEvent.title.ilike("%预警%"),
                    WorkEvent.content_text.ilike("%风险%"),
                    WorkEvent.content_text.ilike("%异常%"),
                    WorkEvent.content_text.ilike("%预警%"),
                )
            )
        events = list(self.db.scalars(query).all()) if company_id else []
        items = tuple(_workevent_item(event) for event in events)
        result_type = "risk_event_list" if request.operation == "risk_events" else "workevent_summary"
        return ProviderResult(
            source="workevent",
            status="success",
            result_type=result_type,
            count=len(items),
            items=items,
            metadata={"operation": request.operation, "tool_name": self._OPERATIONS[request.operation][0]},
            answer=_workevent_answer(items, risk_only=request.operation == "risk_events"),
        )


class MemoryProvider(FeishuResourceProvider):
    source = "memory"

    _OPERATIONS: dict[str, tuple[str, bool]] = {"related_memory": ("local_memory_facts", False)}

    def execute(self, request: ProviderRequest) -> ProviderResult:
        if request.operation != "related_memory":
            return _unsupported_operation_result("memory", request.operation, sorted(self._OPERATIONS))
        company_id = request.context.runtime_scope.active_company_id
        query = (
            select(MemoryFact)
            .where(MemoryFact.company_id == company_id)
            .order_by(MemoryFact.updated_at.desc())
            .limit(8)
        )
        facts = list(self.db.scalars(query).all()) if company_id else []
        items = tuple(_memory_item(fact) for fact in facts)
        return ProviderResult(
            source="memory",
            status="success",
            result_type="memory_fact_list",
            count=len(items),
            items=items,
            metadata={"operation": request.operation, "tool_name": "local_memory_facts"},
            answer=_memory_answer(items),
        )


class KnowledgeProvider(FeishuResourceProvider):
    source = "knowledge"

    _OPERATIONS: dict[str, tuple[str, bool]] = {
        "search": ("local_public_knowledge", False),
        "risk_policy": ("local_public_risk_knowledge", False),
    }

    def execute(self, request: ProviderRequest) -> ProviderResult:
        if request.operation not in self._OPERATIONS:
            return _unsupported_operation_result("knowledge", request.operation, sorted(self._OPERATIONS))
        company_id = request.context.runtime_scope.active_company_id
        seed_text = request.context.current_message
        company_profile_mode = _should_include_company_profile_context(seed_text) or request.intent.entities.get("knowledge_context") == "company_profile"
        if company_profile_mode:
            document_items = _knowledge_document_items(self.db, request=request, company_id=company_id, seed_text=seed_text, context="company_profile")
            company_items = _company_profile_knowledge_items(self.db, company_id=company_id, seed_text=seed_text)
            items = (*document_items, *company_items)
            return ProviderResult(
                source="knowledge",
                status="success",
                result_type="company_profile_knowledge",
                count=len(items),
                items=items,
                metadata={
                    "operation": request.operation,
                    "tool_name": self._OPERATIONS[request.operation][0],
                    "knowledge_context": "company_profile",
                    "document_count": len(document_items),
                    "company_profile_count": len(company_items),
                    "evidence_sources": sorted({str(item.get("source") or "") for item in items if str(item.get("source") or "").strip()}),
                    "result_context_presentation": "summary",
                    "fact_count": 0,
                    "event_count": 0,
                },
                answer=_company_profile_knowledge_answer(items),
            )
        if request.operation == "risk_policy":
            seed_text = f"{seed_text} 风险 预警 异常 制度 流程 规范"
        keywords = _knowledge_keywords(seed_text)
        document_items = _knowledge_document_items(self.db, request=request, company_id=company_id, seed_text=seed_text, context="general")
        facts = _knowledge_facts(self.db, company_id=company_id, keywords=keywords, limit=6)
        events = _knowledge_events(self.db, company_id=company_id, keywords=keywords, limit=6)
        company_items = _company_profile_knowledge_items(self.db, company_id=company_id, seed_text=seed_text)
        items = tuple(
            [
                *document_items,
                *company_items,
                *(_knowledge_fact_item(item) for item in facts),
                *(_knowledge_event_item(item) for item in events),
            ]
        )
        result_type = "risk_policy_list" if request.operation == "risk_policy" else "knowledge_list"
        return ProviderResult(
            source="knowledge",
            status="success",
            result_type=result_type,
            count=len(items),
            items=items,
            metadata={
                "operation": request.operation,
                "tool_name": self._OPERATIONS[request.operation][0],
                "keywords": keywords,
                "document_count": len(document_items),
                "company_profile_count": len(company_items),
                "fact_count": len(facts),
                "event_count": len(events),
            },
            answer=_knowledge_answer(items, risk_policy=request.operation == "risk_policy"),
        )


class WebProvider(FeishuResourceProvider):
    source = "web"

    _OPERATIONS: dict[str, tuple[str, bool]] = {"search": ("local_external_web_events", False)}

    def execute(self, request: ProviderRequest) -> ProviderResult:
        if request.operation != "search":
            return _unsupported_operation_result("web", request.operation, sorted(self._OPERATIONS))
        if request.planner.strategy == "external_information_query":
            query = ""
            category = "public_realtime"
            if isinstance(request.intent.entities, dict):
                query = str(request.intent.entities.get("external_query") or request.intent.entities.get("query") or "").strip()
                category = str(request.intent.entities.get("external_category") or category).strip() or category
            answer = _external_realtime_boundary_answer(category)
            return ProviderResult(
                source="web",
                status="error",
                result_type="external_information_unavailable",
                count=0,
                metadata={
                    "operation": request.operation,
                    "tool_name": "external_realtime_search",
                    "error_type": "external_realtime_not_connected",
                    "provider_boundary": "external_realtime_not_connected",
                    "mode": "realtime_external_search_not_connected",
                    "external_query": query,
                    "recommended_next_step": "接入外部实时检索 Provider 后再回答公开实时信息。",
                },
                answer=answer,
                error="external_realtime_not_connected",
            )
        company_id = request.context.runtime_scope.active_company_id
        keywords = _knowledge_keywords(request.context.current_message)
        events = _web_events(self.db, company_id=company_id, keywords=keywords, limit=6)
        items = tuple(_web_event_item(event) for event in events)
        return ProviderResult(
            source="web",
            status="success",
            result_type="web_search_list",
            count=len(items),
            items=items,
            metadata={
                "operation": request.operation,
                "tool_name": "local_external_web_events",
                "keywords": keywords,
                "mode": "synced_external_web_only",
            },
            answer=_web_answer(items),
        )


def build_feishu_provider_registry(*, db: Session, cli_profile: str | None = None) -> dict[str, FeishuResourceProvider]:
    return {
        "people": FeishuPeopleProvider(db=db, cli_profile=cli_profile),
        "approval": FeishuApprovalProvider(db=db, cli_profile=cli_profile),
        "base": FeishuBaseProvider(db=db, cli_profile=cli_profile),
        "im": FeishuIMProvider(db=db, cli_profile=cli_profile),
        "task": FeishuTaskProvider(db=db, cli_profile=cli_profile),
        "calendar": FeishuCalendarProvider(db=db, cli_profile=cli_profile),
        "mail": FeishuMailProvider(db=db, cli_profile=cli_profile),
        "company_profile": CompanyProfileProvider(db=db, cli_profile=cli_profile),
        "knowledge": KnowledgeProvider(db=db, cli_profile=cli_profile),
        "workevent": WorkEventProvider(db=db, cli_profile=cli_profile),
        "memory": MemoryProvider(db=db, cli_profile=cli_profile),
        "web": WebProvider(db=db, cli_profile=cli_profile),
        "docs": FeishuDocsProvider(db=db, cli_profile=cli_profile),
        "wiki": FeishuWikiProvider(db=db, cli_profile=cli_profile),
        "drive": FeishuDriveProvider(db=db, cli_profile=cli_profile),
        "vc": FeishuVcProvider(db=db, cli_profile=cli_profile),
        "attendance": FeishuAttendanceProvider(db=db, cli_profile=cli_profile),
        "okr": FeishuOkrProvider(db=db, cli_profile=cli_profile),
        "slides": FeishuSlidesProvider(db=db, cli_profile=cli_profile),
        "whiteboard": FeishuWhiteboardProvider(db=db, cli_profile=cli_profile),
        "sheets": PendingSkillProvider(source="sheets", db=db, cli_profile=cli_profile),
        "note": PendingSkillProvider(source="note", db=db, cli_profile=cli_profile),
        "minutes": PendingSkillProvider(source="minutes", db=db, cli_profile=cli_profile),
        "markdown": PendingSkillProvider(source="markdown", db=db, cli_profile=cli_profile),
        "apps": PendingSkillProvider(source="apps", db=db, cli_profile=cli_profile),
        "openapi": PendingSkillProvider(source="openapi", db=db, cli_profile=cli_profile),
        "vc_agent": PendingSkillProvider(source="vc_agent", db=db, cli_profile=cli_profile),
    }


def _company_profile_answer(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "当前公司")
    status = str(item.get("status") or "").strip()
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    business = str(metadata.get("business") or metadata.get("main_business") or metadata.get("主营业务") or "").strip()
    intro = str(metadata.get("intro") or metadata.get("description") or metadata.get("summary") or "").strip()
    lines = [f"{name}"]
    if status:
        lines.append(f"状态：{status}")
    if business:
        lines.append(f"主营业务：{business}")
    if intro:
        lines.append(f"简介：{intro}")
    if not business and not intro:
        lines.append("公司档案里暂时还没有沉淀主营业务或公司简介。")
    return "\n".join(lines)


def _external_realtime_boundary_answer(category: str) -> str:
    if category == "local_realtime":
        return "这类问题需要外部实时位置/门店信息能力；当前还没有接入，所以我不能可靠回答。"
    if category == "weather_realtime":
        return "这类问题需要外部实时天气能力；当前还没有接入，所以我不能可靠回答。"
    return "这类问题需要外部实时信息能力；当前还没有接入实时联网查询，所以我不能可靠回答。"


def _workevent_visibility_item(event: WorkEvent) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    raw_json = event.raw_json if isinstance(event.raw_json, dict) else {}
    cognitive_fields = payload.get("cognitive_fields") if isinstance(payload.get("cognitive_fields"), dict) else {}
    visibility_scope = str(getattr(event, "visibility_scope", "") or "company").strip().upper()
    object_type = str(getattr(event, "object_type", "") or "workevent").strip()
    object_id = str(getattr(event, "object_id", "") or getattr(event, "id", "") or "").strip()
    allowed_user_ids = _string_sequence(getattr(event, "allowed_user_ids", None))
    allowed_departments = _string_sequence(getattr(event, "allowed_departments", None))
    event_payloads = (cognitive_fields, payload, raw_json)
    owner_open_id = _first_payload_string(event_payloads, ("owner_open_id", "user_open_id", "actor_open_id"))
    owner_user_id = _first_payload_string(event_payloads, ("owner_user_id", "user_id", "actor_user_id"))
    owner_department_id = _first_payload_string(
        event_payloads,
        ("owner_department_id", "department_id", "owner_department"),
    )
    item: dict[str, Any] = {
        "resource_plane": "cognitive",
        "resource_type": object_type or str(event.business_domain or "workevent"),
        "source_system": event.source or "digital_advisor",
        "source_object_type": object_type,
        "source_object_id": object_id,
        "visibility_scope": visibility_scope,
        "inherited_visibility_scope": visibility_scope,
        "allowed_user_ids": allowed_user_ids,
        "allowed_departments": allowed_departments,
        "allowed_roles": _string_sequence(getattr(event, "allowed_roles", None)),
        "source_event_ids": [str(event.id)],
        "data_classification": event.data_classification,
    }
    if owner_open_id:
        item["owner_open_id"] = owner_open_id
    if owner_user_id:
        item["owner_user_id"] = owner_user_id
    if owner_department_id:
        item["owner_department_id"] = owner_department_id
    return item


def _memory_visibility_item(fact: MemoryFact) -> dict[str, Any]:
    payload = fact.payload if isinstance(fact.payload, dict) else {}
    scope = str(fact.scope or "company").strip().lower()
    if scope in {"personal", "user"}:
        visibility_scope = "SELF"
    elif scope == "chat":
        visibility_scope = "SELF" if fact.user_open_id else "PRIVATE"
    else:
        visibility_scope = "COMPANY"
    item: dict[str, Any] = {
        "resource_plane": "cognitive",
        "resource_type": "memory",
        "source_system": "digital_advisor",
        "source_object_type": "memory_fact",
        "source_object_id": str(fact.id),
        "visibility_scope": visibility_scope,
        "inherited_visibility_scope": visibility_scope,
        "allowed_user_ids": [fact.user_open_id] if visibility_scope == "SELF" and fact.user_open_id else [],
    }
    owner_open_id = str(fact.user_open_id or payload.get("owner_open_id") or payload.get("user_open_id") or "").strip()
    owner_user_id = str(payload.get("owner_user_id") or payload.get("user_id") or "").strip()
    owner_department_id = str(payload.get("owner_department_id") or payload.get("department_id") or "").strip()
    if owner_open_id:
        item["owner_open_id"] = owner_open_id
    if owner_user_id:
        item["owner_user_id"] = owner_user_id
    if owner_department_id:
        item["owner_department_id"] = owner_department_id
    if fact.source_work_event_id:
        item["source_event_ids"] = [str(fact.source_work_event_id)]
    return item


def _first_payload_string(payloads: tuple[dict[str, Any], ...], keys: tuple[str, ...]) -> str:
    for payload in payloads:
        for key in keys:
            value = payload.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    return ""


def _string_sequence(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _workevent_item(event: WorkEvent) -> dict[str, Any]:
    return {
        **_workevent_visibility_item(event),
        "id": str(event.id),
        "title": event.title or event.event_type,
        "event_type": event.event_type,
        "source": event.source,
        "business_domain": event.business_domain,
        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else "",
        "summary": _short_text(event.content_text, 160),
        "importance_score": event.importance_score,
        "labels": event.labels or [],
    }


def _workevent_answer(items: tuple[dict[str, Any], ...], *, risk_only: bool) -> str:
    if not items:
        return "暂时没有查到风险相关工作事件。" if risk_only else "暂时没有查到最近工作事件。"
    header = f"查到 {len(items)} 条风险相关工作事件：" if risk_only else f"最近工作事件 {len(items)} 条："
    lines = [header]
    for index, item in enumerate(items[:8], start=1):
        title = str(item.get("title") or "未命名事件")
        summary = str(item.get("summary") or "").strip()
        lines.append(f"{index}. {title}" + (f"｜{summary}" if summary else ""))
    return "\n".join(lines)


def _memory_item(fact: MemoryFact) -> dict[str, Any]:
    return {
        **_memory_visibility_item(fact),
        "id": str(fact.id),
        "fact_type": fact.fact_type,
        "subject": fact.subject,
        "content": fact.content,
        "confidence": fact.confidence,
        "scope": fact.scope,
        "source_kind": fact.source_kind,
        "source_work_event_id": str(fact.source_work_event_id) if fact.source_work_event_id else "",
    }


def _memory_answer(items: tuple[dict[str, Any], ...]) -> str:
    if not items:
        return "暂时没有查到可用的长期记忆。"
    lines = [f"查到 {len(items)} 条相关长期记忆："]
    for index, item in enumerate(items[:6], start=1):
        subject = str(item.get("subject") or "未命名")
        content = _short_text(str(item.get("content") or ""), 140)
        lines.append(f"{index}. {subject}" + (f"｜{content}" if content else ""))
    return "\n".join(lines)


_PUBLIC_KNOWLEDGE_FACT_TYPES = {"people_or_policy", "policy", "process", "knowledge", "faq", "organization"}
_BLOCKED_KNOWLEDGE_FACT_TYPES = {"compensation", "finance", "customer_or_order"}
_BLOCKED_KNOWLEDGE_TERMS = ("薪资", "工资", "奖金", "现金流", "客户订单", "回款", "老板邮箱", "私密")
_SENSITIVE_KNOWLEDGE_LEVELS = {"sensitive", "confidential", "secret", "private"}
_KNOWLEDGE_EVENT_TERMS = ("wiki", "doc", "docx", "document", "knowledge", "知识", "制度", "流程", "规范", "模板")
_COMPANY_PROFILE_DOCUMENT_TERMS = ("公司介绍", "公司简介", "企业介绍", "企业简介", "主营业务", "主要业务", "业务范围", "产品介绍", "客户类型", "官网")
_GENERAL_KNOWLEDGE_DOCUMENT_TERMS = ("制度", "流程", "规范", "手册", "模板", "SOP", "说明", "指南", "知识", "文档", "资料", "项目")
_READABLE_DOCUMENT_TYPES = {"doc", "docx", "document"}


def _text_contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _knowledge_document_items(
    db: Session,
    *,
    request: ProviderRequest,
    company_id: Any,
    seed_text: str,
    context: str,
    limit: int = 3,
) -> tuple[dict[str, Any], ...]:
    if not company_id:
        return ()
    app_config = _active_feishu_app_config(db, company_id)
    if app_config is None:
        return ()
    service = FeishuDriveService(app_config)
    candidates = _registered_knowledge_resource_candidates(db, company_id=company_id, seed_text=seed_text, context=context, limit=limit * 3)
    if len(candidates) < limit:
        candidates.extend(_drive_knowledge_candidates(service, seed_text=seed_text, context=context, limit=limit * 3))
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        key = (str(candidate.get("document_type") or ""), str(candidate.get("document_id") or ""))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        item = _read_knowledge_document_candidate(service, candidate, seed_text=seed_text, context=context)
        if item:
            items.append(item)
        if len(items) >= limit:
            break
    return tuple(items)


def _registered_knowledge_resource_candidates(
    db: Session,
    *,
    company_id: Any,
    seed_text: str,
    context: str,
    limit: int,
) -> list[dict[str, Any]]:
    query = (
        select(Resource)
        .where(Resource.company_id == company_id)
        .where(Resource.enabled.is_(True))
        .where(Resource.resource_type.in_(["drive_file", "doc", "wiki"]))
        .where(or_(Resource.business_domain == "knowledge", Resource.resource_type.in_(["doc", "wiki"])))
        .order_by(Resource.updated_at.desc())
        .limit(max(limit * 30, 200))
    )
    resources = list(db.scalars(query).all())
    candidates = []
    for resource in resources:
        config = resource.config_json if isinstance(resource.config_json, dict) else {}
        document_type = _registered_resource_document_type(config, str(resource.resource_id or ""))
        candidate = {
            "title": resource.resource_name or resource.resource_id,
            "document_id": resource.resource_id,
            "document_type": document_type,
            "source": "registered_resource",
            "resource_id": str(resource.id),
            "resource_type": resource.resource_type,
            "config": config,
        }
        score = _knowledge_document_score(candidate, seed_text=seed_text, context=context)
        if score > 0 and document_type in _READABLE_DOCUMENT_TYPES:
            candidates.append((score, candidate))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [candidate for _, candidate in candidates[:limit]]


def _drive_knowledge_candidates(
    service: FeishuDriveService,
    *,
    seed_text: str,
    context: str,
    limit: int,
) -> list[dict[str, Any]]:
    try:
        payload = _run_async(service.list_files(page_size=min(max(limit, 1), 50)))
    except Exception:
        return []
    data = _feishu_response_data(payload)
    raw_items = _items_from_payload(data if isinstance(data, dict) else payload)
    candidates = []
    for raw in raw_items:
        item = _drive_file_item(raw)
        document_type = str(item.get("type") or _document_type_for_token(str(item.get("token") or "")) or "").strip().lower()
        candidate = {
            "title": item.get("name") or item.get("token") or "",
            "document_id": item.get("token") or "",
            "document_type": document_type,
            "source": "drive_list",
            "resource_type": "drive_file",
            "raw": raw,
        }
        score = _knowledge_document_score(candidate, seed_text=seed_text, context=context)
        if score > 0 and document_type in _READABLE_DOCUMENT_TYPES:
            candidates.append((score, candidate))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [candidate for _, candidate in candidates[:limit]]


def _knowledge_document_score(candidate: dict[str, Any], *, seed_text: str, context: str) -> int:
    title = str(candidate.get("title") or "").lower()
    seed = str(seed_text or "").lower()
    terms = _COMPANY_PROFILE_DOCUMENT_TERMS if context == "company_profile" else _GENERAL_KNOWLEDGE_DOCUMENT_TERMS
    if context == "company_profile":
        score = sum(3 for term in terms if term.lower() in title)
    else:
        score = sum(3 for term in terms if term.lower() in title and term.lower() in seed)
    keywords = _knowledge_keywords(seed)
    score += sum(1 for keyword in keywords if keyword and keyword.lower() in title)
    if context == "company_profile" and _text_contains_any(title, ("合同", "库存", "采购", "crm", "资产", "订单", "台账")):
        score -= 3
    return score


def _read_knowledge_document_candidate(
    service: FeishuDriveService,
    candidate: dict[str, Any],
    *,
    seed_text: str,
    context: str,
) -> dict[str, Any] | None:
    document_id = str(candidate.get("document_id") or "").strip()
    document_type = str(candidate.get("document_type") or "").strip().lower()
    if not document_id or document_type not in _READABLE_DOCUMENT_TYPES:
        return None
    try:
        payload = _run_async(service.get_document_content(document_id=document_id, document_type=document_type))
    except Exception:
        return None
    if not payload.get("available"):
        return None
    content = str(payload.get("content_text") or "").strip()
    if not content:
        return None
    if context == "company_profile" and not _document_content_matches_company_profile(content, title=str(candidate.get("title") or "")):
        return None
    return {
        "kind": "knowledge_document",
        "title": str(candidate.get("title") or document_id),
        "summary": _short_text(content, 260),
        "source": str(candidate.get("source") or "drive"),
        "resource_type": str(candidate.get("resource_type") or "drive_file"),
        "document_id": document_id,
        "document_type": document_type,
        "content_preview": _short_text(content, 1200),
        "evidence_type": "document_content",
    }


def _document_content_matches_company_profile(content: str, *, title: str) -> bool:
    text = f"{title}\n{content}".lower()
    return _text_contains_any(text, tuple(term.lower() for term in _COMPANY_PROFILE_DOCUMENT_TERMS))


def _company_profile_knowledge_items(db: Session, *, company_id: Any, seed_text: str) -> tuple[dict[str, Any], ...]:
    if not company_id or not _should_include_company_profile_context(seed_text):
        return ()
    company = db.get(Company, company_id)
    if company is None:
        return ()
    metadata = company.metadata_json if isinstance(company.metadata_json, dict) else {}
    business = str(metadata.get("business") or metadata.get("main_business") or metadata.get("主营业务") or "").strip()
    intro = str(metadata.get("intro") or metadata.get("description") or metadata.get("summary") or "").strip()
    summary_parts = []
    if business:
        summary_parts.append(f"主营业务：{business}")
    if intro:
        summary_parts.append(f"简介：{intro}")
    if not summary_parts:
        summary_parts.append("公司档案里暂时还没有沉淀主营业务或公司简介。")
    return (
        {
            "kind": "company_profile",
            "title": company.name,
            "summary": "；".join(summary_parts),
            "source": "company_profile",
            "code": company.code,
            "status": company.status,
        },
    )


def _company_profile_knowledge_answer(items: tuple[dict[str, Any], ...]) -> str:
    if not items:
        return "暂时没有从公司档案、云文档、Wiki 或 Drive 中找到可用的公司介绍资料。"
    document_items = [item for item in items if item.get("kind") == "knowledge_document"]
    if document_items:
        lines = [f"从正式知识资料中找到 {len(document_items)} 条公司介绍依据："]
        for index, item in enumerate(document_items[:3], start=1):
            title = str(item.get("title") or "未命名文档").strip()
            summary = str(item.get("summary") or "").strip()
            lines.append(f"{index}. {title}" + (f"｜{summary}" if summary else ""))
        profile_items = [item for item in items if item.get("kind") == "company_profile"]
        if profile_items:
            profile_summary = str(profile_items[0].get("summary") or "").strip()
            if profile_summary and "暂时还没有沉淀" not in profile_summary:
                lines.append(f"企业画像补充：{profile_summary}")
        lines.append("说明：以上只来自已登记或当前应用可见的正式知识资料，不使用邮件或底层日志拼凑。")
        return "\n".join(lines)
    item = items[0]
    title = str(item.get("title") or "当前公司").strip()
    summary = str(item.get("summary") or "").strip()
    if summary and "暂时还没有沉淀" not in summary:
        return f"{title}：{summary}"
    return (
        f"{title} 的企业画像里暂时还没有沉淀主营业务或公司简介。"
        "我不会用邮件、文档同步事件或底层日志来拼凑公司介绍。"
        "可以先在企业画像里补充主营业务、产品、客户类型、官网或一句话介绍。"
    )


def _should_include_company_profile_context(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "").lower())
    if not compact:
        return False
    if any(token in compact for token in ("我们公司", "咱们公司", "本公司", "公司介绍", "主营业务", "主要业务", "业务范围", "公司业务")):
        return True
    return "公司" in compact and any(token in compact for token in ("做什么", "干什么", "业务", "情况", "介绍", "收入来源"))


def _knowledge_facts(
    db: Session,
    *,
    company_id: Any,
    keywords: list[str],
    limit: int,
) -> list[MemoryFact]:
    if not company_id:
        return []
    conditions = []
    for keyword in keywords[:8]:
        pattern = f"%{keyword}%"
        conditions.extend(
            [
                MemoryFact.subject.ilike(pattern),
                MemoryFact.content.ilike(pattern),
                MemoryFact.fact_type.ilike(pattern),
            ]
        )
    query = (
        select(MemoryFact)
        .where(MemoryFact.company_id == company_id)
        .where(MemoryFact.fact_type.notin_(_BLOCKED_KNOWLEDGE_FACT_TYPES))
        .order_by(MemoryFact.updated_at.desc())
        .limit(limit * 3)
    )
    if conditions:
        query = query.where(or_(*conditions))
    rows = list(db.scalars(query).all())
    return [fact for fact in rows if _is_public_knowledge_fact(fact)][:limit]


def _knowledge_events(
    db: Session,
    *,
    company_id: Any,
    keywords: list[str],
    limit: int,
) -> list[WorkEvent]:
    if not company_id:
        return []
    conditions = []
    for keyword in [*keywords[:8], *_KNOWLEDGE_EVENT_TERMS]:
        pattern = f"%{keyword}%"
        conditions.extend(
            [
                WorkEvent.event_type.ilike(pattern),
                WorkEvent.title.ilike(pattern),
                WorkEvent.content_text.ilike(pattern),
            ]
        )
    query = (
        select(WorkEvent)
        .where(WorkEvent.company_id == company_id)
        .where(WorkEvent.data_classification == "company")
        .where(WorkEvent.business_domain.in_(["knowledge", "doc", "general"]))
        .where(WorkEvent.source_type != "external_mail_account")
        .where(or_(*conditions))
        .order_by(WorkEvent.occurred_at.desc())
        .limit(limit * 3)
    )
    rows = list(db.scalars(query).all())
    return [event for event in rows if _is_public_knowledge_event(event)][:limit]


def _knowledge_fact_item(fact: MemoryFact) -> dict[str, Any]:
    return {
        **_memory_visibility_item(fact),
        "kind": "fact",
        "title": fact.subject,
        "summary": _short_text(fact.content, 180),
        "fact_type": fact.fact_type,
        "confidence": fact.confidence,
        "scope": fact.scope,
        "source_kind": fact.source_kind,
        "source_work_event_id": str(fact.source_work_event_id) if fact.source_work_event_id else "",
    }


def _knowledge_event_item(event: WorkEvent) -> dict[str, Any]:
    return {
        **_workevent_visibility_item(event),
        "kind": "event",
        "title": event.title or event.event_type,
        "summary": _short_text(event.content_text, 180),
        "event_type": event.event_type,
        "source": event.source,
        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else "",
        "business_domain": event.business_domain,
    }


def _knowledge_answer(items: tuple[dict[str, Any], ...], *, risk_policy: bool) -> str:
    if not items:
        return "没有在已同步的公开知识、流程制度或文档内容里找到可靠答案。"
    header = f"查到 {len(items)} 条风险/制度相关知识：" if risk_policy else f"查到 {len(items)} 条公开知识："
    lines = [header]
    for index, item in enumerate(items[:8], start=1):
        title = str(item.get("title") or "未命名知识")
        summary = str(item.get("summary") or "").strip()
        if item.get("kind") == "company_profile":
            kind = "企业画像"
        elif item.get("kind") == "knowledge_document":
            kind = "正式文档"
        else:
            kind = "知识事实" if item.get("kind") == "fact" else "文档事件"
        lines.append(f"{index}. {kind}｜{title}" + (f"｜{summary}" if summary else ""))
    lines.append("说明：这里只使用已同步的公开知识/流程制度，不读取邮件、财务、客户或私密数据。")
    return "\n".join(lines)


def _people_snapshot_has_content(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    users = payload.get("users")
    departments = payload.get("departments")
    return (isinstance(users, list) and bool(users)) or (isinstance(departments, list) and bool(departments))


def _is_public_knowledge_fact(fact: MemoryFact) -> bool:
    fact_type = str(fact.fact_type or "").lower()
    if fact_type in _BLOCKED_KNOWLEDGE_FACT_TYPES:
        return False
    if fact_type not in _PUBLIC_KNOWLEDGE_FACT_TYPES:
        return False
    return not _contains_blocked_knowledge_terms(f"{fact.subject} {fact.content}")


def _is_public_knowledge_event(event: WorkEvent) -> bool:
    if str(event.sensitivity or "normal").lower() in _SENSITIVE_KNOWLEDGE_LEVELS:
        return False
    text = f"{event.event_type or ''} {event.title or ''} {event.content_text or ''}"
    if _looks_like_raw_synced_event_text(text):
        return False
    if _contains_blocked_knowledge_terms(text):
        return False
    lowered = text.lower()
    return any(term.lower() in lowered for term in _KNOWLEDGE_EVENT_TERMS)


def _contains_blocked_knowledge_terms(text: str) -> bool:
    return any(term in text for term in _BLOCKED_KNOWLEDGE_TERMS)


def _looks_like_raw_synced_event_text(text: str) -> bool:
    value = str(text or "")
    raw_markers = ("{'", '{"', "mail_address", "bcc", "cc", "from", "to", "subject")
    marker_count = sum(1 for marker in raw_markers if marker in value)
    return marker_count >= 3


def _knowledge_keywords(text: str) -> list[str]:
    value = (
        str(text or "")
        .replace("，", " ")
        .replace("。", " ")
        .replace("？", " ")
        .replace("?", " ")
        .replace("/", " ")
    )
    stop_words = {"我", "你", "的", "了", "吗", "呢", "怎么", "如何", "一下", "帮我", "看看"}
    tokens = [item.strip() for item in value.split() if item.strip()]
    keywords = [token for token in tokens if token not in stop_words and len(token) >= 2]
    return keywords[:8] if keywords else ["流程", "制度", "知识"]


def _web_events(
    db: Session,
    *,
    company_id: Any,
    keywords: list[str],
    limit: int,
) -> list[WorkEvent]:
    if not company_id:
        return []
    conditions = []
    for keyword in keywords[:8]:
        pattern = f"%{keyword}%"
        conditions.extend(
            [
                WorkEvent.event_type.ilike(pattern),
                WorkEvent.title.ilike(pattern),
                WorkEvent.content_text.ilike(pattern),
            ]
        )
    query = (
        select(WorkEvent)
        .where(WorkEvent.company_id == company_id)
        .where(WorkEvent.source_type == "external_web")
        .where(WorkEvent.data_classification == "company")
        .order_by(WorkEvent.occurred_at.desc())
        .limit(limit * 3)
    )
    if conditions:
        query = query.where(or_(*conditions))
    rows = list(db.scalars(query).all())
    return [event for event in rows if _is_safe_web_event(event)][:limit]


def _web_event_item(event: WorkEvent) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    raw_json = event.raw_json if isinstance(event.raw_json, dict) else {}
    url = str(payload.get("url") or payload.get("link") or raw_json.get("url") or raw_json.get("link") or "").strip()
    return {
        **_workevent_visibility_item(event),
        "kind": "external_web",
        "title": event.title or event.event_type,
        "summary": _short_text(event.content_text, 180),
        "url": url,
        "event_type": event.event_type,
        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else "",
        "source": event.source,
    }


def _web_answer(items: tuple[dict[str, Any], ...]) -> str:
    if not items:
        return "没有在已同步的外部网页资料里找到可靠内容。说明：我没有临时联网搜索，只使用已同步、可追溯的网页资料。"
    lines = [f"查到 {len(items)} 条已同步网页资料："]
    for index, item in enumerate(items[:6], start=1):
        title = str(item.get("title") or "未命名网页")
        summary = str(item.get("summary") or "").strip()
        url = str(item.get("url") or "").strip()
        suffix = "｜".join(part for part in (summary, url) if part)
        lines.append(f"{index}. {title}" + (f"｜{suffix}" if suffix else ""))
    lines.append("说明：这里只使用已同步网页资料，不进行实时外部搜索。")
    return "\n".join(lines)


def _is_safe_web_event(event: WorkEvent) -> bool:
    if str(event.sensitivity or "normal").lower() in _SENSITIVE_KNOWLEDGE_LEVELS:
        return False
    text = f"{event.title or ''} {event.content_text or ''}"
    return not _contains_blocked_knowledge_terms(text)


def _short_text(text: str | None, max_length: int) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= max_length else f"{value[:max_length]}..."


def _bot_actor_from_runtime_context(context: RuntimeContext) -> BotActor:
    return BotActor(
        role=context.identity.role,
        access_scope="company" if context.identity.role in {"owner", "admin"} else "self",
        domains=context.identity.domains,
        display_name=context.identity.display_name,
        open_id=context.identity.open_id,
    )


def _provider_result_from_tool_result(source: str, result, *, fallback_result_type: str) -> ProviderResult:
    payload = _tool_payload(result)
    items = _items_from_payload(payload)
    status = _provider_status(result)
    result_type = str(payload.get("result_type") or fallback_result_type)
    return ProviderResult(
        source=source,
        status=status,
        result_type=result_type,
        count=len(items),
        items=tuple(items),
        metadata={"raw": payload, **_provider_error_metadata(result)} if payload else _provider_error_metadata(result),
        answer=_safe_provider_answer(source=source, status=status, result_type=result_type, raw_answer=result.answer, error=result.error),
        error=result.error or "",
    )


def _safe_provider_answer(*, source: str, status: str, result_type: str, raw_answer: str, error: str) -> str:
    answer = str(raw_answer or "").strip()
    if answer and not _looks_like_raw_tool_answer(answer):
        return answer
    label = _provider_label(source)
    if status == "success":
        return f"{label}能力执行完成。"
    detail = str(error or answer or "").strip()
    if detail:
        return f"{label}能力执行失败：{_compact_runtime_error(detail)}"
    return f"{label}能力执行失败。"


def _tool_failure_answer(action_label: str, result) -> str:
    detail = str(getattr(result, "error", "") or getattr(result, "answer", "") or "").strip()
    if not detail:
        return f"{action_label}失败。"
    if _looks_like_raw_tool_answer(detail):
        return f"{action_label}失败：飞书接口或参数错误，原始诊断已记录到 V5 状态里。"
    return f"{action_label}失败：{_compact_runtime_error(detail)}"


def _looks_like_raw_tool_answer(answer: str) -> bool:
    stripped = str(answer or "").strip()
    return stripped.startswith("{") or stripped.startswith("[") or "HTTP error" in stripped or "status_code" in stripped


def _compact_runtime_error(text: str, *, limit: int = 180) -> str:
    compact = " ".join(str(text or "").split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def _unsupported_operation_result(source: str, operation: str, available_operations: list[str]) -> ProviderResult:
    return ProviderResult(
        source=source,
        status="error",
        result_type=f"{source}_operation_not_supported",
        metadata={
            "operation": operation,
            "error_type": "unsupported_operation",
            "available_operations": available_operations,
            "v5_only": True,
            "legacy_fallback": False,
        },
        answer=f"{source} 能力已进入 V5，但这个操作还没有接入：{operation}。",
        error=f"unsupported_operation:{operation}",
    )


def _tool_not_installed_result(source: str, operation: str) -> ProviderResult:
    return ProviderResult(
        source=source,
        status="error",
        result_type=f"{source}_operation_not_installed",
        metadata={
            "operation": operation,
            "error_type": "tool_not_installed",
            "pending_reason": "provider_operation_not_connected",
            "recommended_next_step": f"接入 {_provider_label(source)} 的 {operation} 只读 Provider 或飞书 Skill/OpenAPI 原子能力。",
            "v5_only": True,
            "legacy_fallback": False,
        },
        answer=f"{_provider_label(source)}能力已在 V5 登记，但底层飞书原子能力还没有接上：{operation}。",
        error=f"{source}_tool_not_installed",
    )


def _enterprise_realtime_boundary_result(
    request: ProviderRequest,
    *,
    source: str,
    result_type: str,
    operation: str,
    current_provider: str,
    user_fallback_allowed: bool,
) -> ProviderResult | None:
    contract = request.execution_identity_contract.payload()
    if contract.get("credential_mode") != "TENANT_TOKEN" or contract.get("actor_identity") != "BOT":
        return None
    effective_user_fallback_allowed = bool(user_fallback_allowed and request.intent.data_scope == "self")
    workspace_user_fallback_allowed = bool(effective_user_fallback_allowed and source in {"task", "calendar"})
    if workspace_user_fallback_allowed:
        fallback_contract = dict(contract)
        owner = fallback_contract.get("credential_owner") if isinstance(fallback_contract.get("credential_owner"), dict) else {}
        fallback_contract["actor_identity"] = "USER"
        fallback_contract["credential_mode"] = "USER_TOKEN"
        fallback_contract["requires_authorization"] = True
        fallback_contract["authorization_status"] = "MISSING_AUTHORIZATION"
        fallback_contract["credential_owner"] = {
            "company_id": str(owner.get("company_id") or request.context.runtime_scope.active_company_id or ""),
            "open_id": str(owner.get("open_id") or request.context.identity.open_id or ""),
            "user_id": str(owner.get("user_id") or request.context.identity.user_id or ""),
            "cli_profile": str(owner.get("cli_profile") or ""),
        }
        return ProviderResult(
            source=source,
            status="denied",
            result_type="waiting_authorization",
            count=0,
            items=(),
            metadata={
                "operation": operation,
                "current_provider": current_provider,
                "credential_mode": "USER_TOKEN",
                "actor_identity": "USER",
                "execution_identity_contract": fallback_contract,
                "error_type": "missing_user_authorization",
                "provider_boundary": "user_token_required",
                "original_provider_boundary": "enterprise_realtime_not_integrated",
                "operational_source": "feishu_realtime",
                "workevent_as_realtime_source": False,
                "extracted_item_as_realtime_source": False,
                "legacy_cli_fallback_used": False,
                "user_fallback_allowed": True,
                "waiting_authorization": True,
                "authorization_status": "MISSING_AUTHORIZATION",
                "authorization_error": "missing_feishu_user_account",
                "recommended_next_step": "请先完成飞书用户授权；授权后 Runtime 才能读取当前用户个人资源。",
            },
            answer=f"{_provider_label(source)}个人实时读取需要本人飞书授权。请先完成授权后再查询。",
            error="missing_feishu_user_account",
        )
    return ProviderResult(
        source=source,
        status="denied",
        result_type=result_type,
        count=0,
        items=(),
        metadata={
            "operation": operation,
            "current_provider": current_provider,
            "credential_mode": "TENANT_TOKEN",
            "actor_identity": "BOT",
            "execution_identity_contract": contract,
            "error_type": "enterprise_realtime_not_integrated",
            "provider_boundary": "enterprise_realtime_not_integrated",
            "operational_source": "feishu_realtime",
            "workevent_as_realtime_source": False,
            "extracted_item_as_realtime_source": False,
            "legacy_cli_fallback_used": False,
            "fallback_used": False,
            "user_fallback_allowed": effective_user_fallback_allowed,
            "recommended_next_step": (
                f"接入 {_provider_label(source)} 的 Bot/Tenant 实时读取 Provider；"
                "如需读取个人私有资源，应由 Policy/Runtime 显式进入 USER fallback。"
            ),
        },
        answer=(
            f"{_provider_label(source)}企业实时读取能力还没有接入 Bot/Tenant 主路径。"
            "我不会改用本地认知数据或当前用户本机身份代查。"
        ),
        error="enterprise_realtime_not_integrated",
    )


def _workspace_cognitive_aggregation_result(
    db: Session | None,
    request: ProviderRequest,
    *,
    source: str,
    operation: str,
) -> ProviderResult | None:
    scope = _resource_scope_for_request(request)
    if scope not in {"DEPARTMENT", "COMPANY", "TEAM"} or db is None:
        return None
    if not _supports_workspace_cognitive_projection_read(db):
        return None
    company_id = request.context.runtime_scope.active_company_id
    if company_id is None:
        return None
    department_id = str(request.context.runtime_scope.active_department_id or request.context.identity.department_id or "").strip()
    if scope == "DEPARTMENT" and not department_id:
        return _workspace_cognitive_gap_result(
            request=request,
            source=source,
            operation=operation,
            scope=scope,
            reason="missing_department_context",
            answer=(
                "我现在还没有对准要看的部门，所以不能给出部门概览。"
                "你可以直接说部门名称，或先完成组织/人员上下文绑定。"
            ),
        )

    query = (
        select(WorkEvent)
        .where(WorkEvent.company_id == company_id)
        .where(WorkEvent.data_classification == "workspace_cognitive")
        .where(WorkEvent.event_type.in_(("workspace_task_observed", "workspace_calendar_observed")))
        .order_by(WorkEvent.occurred_at.desc())
        .limit(1000)
    )
    if scope == "DEPARTMENT" and department_id:
        query = query.where(WorkEvent.allowed_departments.has_any([department_id]))

    queried_events = list(db.scalars(query).all())
    events = _visible_workspace_projection_events(request=request, events=queried_events, scope=scope)
    if not events:
        return _workspace_cognitive_gap_result(
            request=request,
            source=source,
            operation=operation,
            scope=scope,
            reason="no_visible_workspace_projection",
            queried_event_count=len(queried_events),
            answer=(
                f"我现在还没有足够的{_workspace_scope_label(scope)}已授权观察数据，"
                "所以不能给出可靠概览。"
                "等实时读取或认知同步补齐后，我可以继续帮你看任务负荷、逾期和日程冲突。"
            ),
        )

    summary = build_workspace_aggregation_from_visible_events(events, scope=scope.lower())
    metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else {}
    item = {
        "title": f"Workspace {scope.lower()} 认知聚合",
        "summary": _workspace_aggregation_answer(metrics, scope=scope),
        "result_type": "workspace_aggregation_summary",
        "resource_plane": "cognitive",
        "resource_type": "workspace_aggregation",
        "source_system": "digital_advisor",
        "source_object_type": "workspace_aggregation",
        "source_object_id": f"{str(company_id)}:{scope.lower()}",
        "visibility_scope": scope,
        "company_id": str(company_id),
        "data_classification": "workspace_cognitive",
        "detail_available": False,
        "operational_detail_available": False,
        "workevent_as_realtime_source": False,
        "metrics": metrics,
        "metric_order": summary.get("metric_order") or [],
        "policy_notes": summary.get("policy_notes") or [],
        "source_event_count": summary.get("source_event_count", 0),
        "queried_event_count": len(queried_events),
        "visible_event_count": len(events),
    }
    return ProviderResult(
        source=source,
        status="success",
        result_type="workspace_aggregation_summary",
        count=1,
        items=(item,),
        metadata={
            "operation": operation,
            "provider_boundary": "workspace_cognitive_aggregation",
            "operational_source": "workspace_cognitive_projection",
            "realtime_provider_boundary": "enterprise_realtime_not_integrated",
            "workevent_as_realtime_source": False,
            "extracted_item_as_realtime_source": False,
            "legacy_cli_fallback_used": False,
            "fallback_used": False,
            "user_fallback_allowed": False,
            "scope": scope,
            "source_event_count": summary.get("source_event_count", 0),
            "queried_event_count": len(queried_events),
            "visible_event_count": len(events),
        },
        answer=_workspace_cognitive_aggregation_answer(metrics, scope=scope),
        error="",
    )


def _workspace_cognitive_gap_result(
    *,
    request: ProviderRequest,
    source: str,
    operation: str,
    scope: str,
    reason: str,
    answer: str,
    queried_event_count: int = 0,
) -> ProviderResult:
    return ProviderResult(
        source=source,
        status="denied",
        result_type="workspace_aggregation_summary",
        count=0,
        items=(),
        metadata={
            "operation": operation,
            "provider_boundary": "workspace_cognitive_gap",
            "realtime_provider_boundary": "enterprise_realtime_not_integrated",
            "operational_source": "workspace_cognitive_projection",
            "error_type": reason,
            "scope": scope,
            "company_id": str(request.context.runtime_scope.active_company_id or ""),
            "queried_event_count": queried_event_count,
            "visible_event_count": 0,
            "workevent_as_realtime_source": False,
            "extracted_item_as_realtime_source": False,
            "legacy_cli_fallback_used": False,
            "fallback_used": False,
            "user_fallback_allowed": False,
        },
        answer=answer,
        error=reason,
    )


def _supports_workspace_cognitive_projection_read(db: Session) -> bool:
    return callable(getattr(db, "scalars", None)) and callable(getattr(db, "flush", None))


def _supports_workspace_cognitive_projection_write(db: Session) -> bool:
    return callable(getattr(db, "add", None)) and callable(getattr(db, "flush", None))


def _visible_workspace_projection_events(
    *,
    request: ProviderRequest,
    events: list[WorkEvent],
    scope: str,
) -> list[WorkEvent]:
    company_id = request.context.runtime_scope.active_company_id
    role = str(request.context.identity.role or "").strip().lower()
    domains = {str(item).strip().lower() for item in (request.context.identity.domains or ()) if str(item).strip()}
    actor_user_id = str(request.context.identity.user_id or "").strip()
    actor_open_id = str(request.context.identity.open_id or "").strip()
    department_id = str(request.context.runtime_scope.active_department_id or request.context.identity.department_id or "").strip()
    visible: list[WorkEvent] = []
    for event in events:
        if company_id is not None and event.company_id != company_id:
            continue
        if event.data_classification != "workspace_cognitive":
            continue
        if event.event_type not in {"workspace_task_observed", "workspace_calendar_observed"}:
            continue
        if scope == "COMPANY":
            if role in {"owner", "admin"} or "workspace" in domains or "all" in domains:
                visible.append(event)
            continue
        if scope in {"DEPARTMENT", "TEAM"}:
            allowed_departments = {str(item) for item in (event.allowed_departments or []) if str(item).strip()}
            event_department = _workspace_event_owner_department(event)
            if department_id and (department_id in allowed_departments or department_id == event_department):
                visible.append(event)
            continue
        if scope == "SELF":
            allowed_users = {str(item) for item in (event.allowed_user_ids or []) if str(item).strip()}
            event_owner = _workspace_event_owner_user(event)
            if actor_user_id and (actor_user_id in allowed_users or actor_user_id == event_owner):
                visible.append(event)
            elif actor_open_id and actor_open_id == _workspace_event_owner_open_id(event):
                visible.append(event)
    return visible


def _workspace_event_owner_department(event: WorkEvent) -> str:
    payload = event.payload if isinstance(event.payload, dict) else {}
    fields = payload.get("cognitive_fields") if isinstance(payload.get("cognitive_fields"), dict) else {}
    return str(fields.get("owner_department_id") or "").strip()


def _workspace_event_owner_user(event: WorkEvent) -> str:
    payload = event.payload if isinstance(event.payload, dict) else {}
    fields = payload.get("cognitive_fields") if isinstance(payload.get("cognitive_fields"), dict) else {}
    return str(fields.get("owner_user_id") or "").strip()


def _workspace_event_owner_open_id(event: WorkEvent) -> str:
    payload = event.payload if isinstance(event.payload, dict) else {}
    fields = payload.get("cognitive_fields") if isinstance(payload.get("cognitive_fields"), dict) else {}
    return str(fields.get("owner_open_id") or "").strip()


def _workspace_aggregation_answer(metrics: dict[str, Any], *, scope: str) -> str:
    return _workspace_cognitive_aggregation_answer(metrics, scope=scope)


def _workspace_cognitive_aggregation_answer(metrics: dict[str, Any], *, scope: str) -> str:
    workload = _workspace_workload_text(metrics.get("workload_buckets"))
    parts = [
        f"{_workspace_scope_label(scope)}目前可见任务 {int(metrics.get('task_total') or 0)} 个",
        f"逾期 {int(metrics.get('overdue_task_count') or 0)} 个",
        f"7 天内到期 {int(metrics.get('due_soon_task_count') or 0)} 个",
        f"日程冲突 {int(metrics.get('calendar_conflict_count') or 0)} 个",
        f"会议占用 {int(metrics.get('meeting_occupied_minutes') or 0)} 分钟",
    ]
    if workload:
        parts.append(f"负荷{workload}")
    return (
        "我先基于已授权的 Workspace 认知数据给你一个概览：\n"
        + "，".join(parts)
        + "。\n"
        "这只是聚合视角，不展开无权限的任务或日程明细。"
    )


def _workspace_scope_label(scope: str) -> str:
    return {
        "SELF": "你",
        "TEAM": "团队",
        "DEPARTMENT": "部门",
        "COMPANY": "公司",
    }.get(str(scope or "").upper(), "当前范围")


def _workspace_workload_text(value: Any) -> str:
    buckets = value if isinstance(value, dict) else {}
    labels = []
    mapping = (
        ("normal", "正常"),
        ("busy", "偏忙"),
        ("overloaded", "过载"),
        ("at_risk", "有风险"),
    )
    for key, label in mapping:
        count = int(buckets.get(key) or 0)
        if count:
            labels.append(f"{label} {count}")
    return "、".join(labels)


def _append_workspace_task_observations_from_items(
    request: ProviderRequest,
    db: Session | None,
    items: tuple[dict[str, Any], ...],
) -> None:
    if db is None or not items or not _supports_workspace_cognitive_projection_write(db):
        return
    company_id = request.context.runtime_scope.active_company_id
    if company_id is None:
        return
    raw_items = [_workspace_task_observation_payload(item) for item in items]
    append_workspace_cognitive_observations(
        db,
        company_id=company_id,
        object_type="task",
        raw_items=raw_items,
        actor=request.context.identity.open_id or request.context.identity.user_id or "system",
        owner_user_id=request.context.identity.user_id,
        owner_open_id=request.context.identity.open_id,
        owner_department_id=request.context.identity.department_id,
        visibility_scope="self",
    )


def _append_workspace_calendar_observations_from_items(
    request: ProviderRequest,
    db: Session | None,
    items: tuple[dict[str, Any], ...],
) -> None:
    if db is None or not items or not _supports_workspace_cognitive_projection_write(db):
        return
    company_id = request.context.runtime_scope.active_company_id
    if company_id is None:
        return
    raw_items = [_workspace_calendar_observation_payload(item) for item in items]
    append_workspace_cognitive_observations(
        db,
        company_id=company_id,
        object_type="calendar",
        raw_items=raw_items,
        actor=request.context.identity.open_id or request.context.identity.user_id or "system",
        owner_user_id=request.context.identity.user_id,
        owner_open_id=request.context.identity.open_id,
        owner_department_id=request.context.identity.department_id,
        visibility_scope="self",
    )


def _workspace_task_observation_payload(item: dict[str, Any]) -> dict[str, Any]:
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    due_at = _workspace_datetime_from_feishu_value(raw.get("due") or item.get("due") or raw.get("due_time"))
    completed_at = _workspace_datetime_from_feishu_value(raw.get("completed_at"))
    payload = {
        "task_id": raw.get("id") or raw.get("task_id") or item.get("guid"),
        "task_guid": raw.get("guid") or raw.get("task_guid") or item.get("guid"),
        "title": item.get("title") or raw.get("summary") or raw.get("title"),
        "status": item.get("status") or raw.get("status") or raw.get("task_status"),
        "priority": raw.get("priority"),
        "due_at": due_at,
        "completed_at": completed_at,
        "updated_at": _workspace_datetime_from_feishu_value(raw.get("updated_at") or raw.get("update_time")),
        "assignee_count": _workspace_list_count(raw.get("assignees") or raw.get("members")),
        "follower_count": _workspace_list_count(raw.get("followers")),
    }
    if due_at:
        due_dt = _parse_workspace_datetime(due_at)
        payload["is_overdue"] = bool(due_dt and due_dt < datetime.now(UTC) and not completed_at)
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def _workspace_calendar_observation_payload(item: dict[str, Any]) -> dict[str, Any]:
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    start_at = _workspace_datetime_from_feishu_value(raw.get("start_time") or raw.get("start") or item.get("start"))
    end_at = _workspace_datetime_from_feishu_value(raw.get("end_time") or raw.get("end") or item.get("end"))
    payload = {
        "event_id": item.get("event_id") or raw.get("event_id") or raw.get("id"),
        "calendar_id": raw.get("calendar_id"),
        "title": item.get("title") or raw.get("summary") or raw.get("title"),
        "status": raw.get("status"),
        "start_at": start_at,
        "end_at": end_at,
        "updated_at": _workspace_datetime_from_feishu_value(raw.get("updated_at") or raw.get("update_time")),
        "attendee_count": _workspace_list_count(raw.get("attendees")),
        "is_conflict": bool(raw.get("is_conflict")),
        "is_busy": str(raw.get("free_busy_status") or raw.get("visibility") or "").lower() != "free",
    }
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def _workspace_datetime_from_feishu_value(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("timestamp") or value.get("date") or value.get("datetime") or value.get("time")
    if value in (None, ""):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if text.isdigit():
        timestamp = int(text)
        if timestamp > 9_999_999_999:
            timestamp = timestamp // 1000
        return datetime.fromtimestamp(timestamp, UTC).isoformat()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.isoformat()


def _parse_workspace_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _workspace_list_count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _operational_query_read_path(request: ProviderRequest, *, source: str) -> str:
    """Return the realtime read path for an operational source and enterprise scope.

    The policy answers semantic capability, not transport availability. A Bot/Tenant
    API that can return a syntactically valid empty list is still not usable when it
    does not represent the requested enterprise scope.
    """

    contract = request.execution_identity_contract.payload()
    if contract.get("credential_mode") != "TENANT_TOKEN" or contract.get("actor_identity") != "BOT":
        return "tenant_query_supported"

    scope = _resource_scope_for_request(request)
    workspace_primary_resources = {"task", "calendar"}
    if source in workspace_primary_resources:
        if scope == "SELF":
            return "self_user_token_required"
        if scope in {"USER", "TEAM", "DEPARTMENT", "COMPANY"}:
            return "tenant_query_not_integrated"
    return "tenant_query_supported"


def _resource_scope_for_request(request: ProviderRequest) -> str:
    normalized = str(request.intent.data_scope or "").strip().lower()
    if normalized == "self":
        return "SELF"
    if normalized in {"person", "user"}:
        return "USER"
    if normalized == "department":
        return "DEPARTMENT"
    if normalized in {"project", "team"}:
        return "TEAM"
    if normalized in {"company", "organization"}:
        return "COMPANY"
    return normalized.upper() if normalized else "SELF"


def _allows_self_user_query_fallback(request: ProviderRequest) -> bool:
    contract = request.execution_identity_contract.payload()
    return (
        request.intent.question_type == "query"
        and str(request.intent.data_scope or "").lower() == "self"
        and contract.get("credential_mode") == "TENANT_TOKEN"
        and contract.get("actor_identity") == "BOT"
    )


def _has_authorized_user_identity_bundle(db: Session | None, request: ProviderRequest) -> bool:
    if db is None:
        return False
    company_id = request.context.runtime_scope.active_company_id
    open_id = str(request.context.identity.open_id or "").strip()
    if company_id is None or not open_id:
        return False
    access = db.scalar(
        select(BotUserAccess)
        .where(BotUserAccess.company_id == company_id)
        .where(BotUserAccess.open_id == open_id)
        .where(BotUserAccess.is_active.is_(True))
    )
    settings_data = access.settings if access is not None and isinstance(getattr(access, "settings", None), dict) else {}
    raw_authorizations = settings_data.get("user_identity_authorizations")
    authorizations = raw_authorizations if isinstance(raw_authorizations, dict) else {}
    bundle = authorizations.get("user_identity_bundle")
    if not isinstance(bundle, dict):
        return False
    owner_open_id = str(bundle.get("owner_open_id") or open_id).strip()
    status = str(bundle.get("status") or "").strip()
    return owner_open_id == open_id and status in {"authorized", "connected"}


def _runtime_identity_has_company_domain(identity: RuntimeIdentity) -> bool:
    domains = {str(domain).strip().lower() for domain in identity.domains if str(domain).strip()}
    return "all" in domains or bool(domains)


def _message_explicit_self_scope(message: str) -> bool:
    return any(marker in str(message or "") for marker in ("我的", "我 ", "我　", "我想", "我要", "本人", "自己"))


def _user_query_fallback_metadata(
    request: ProviderRequest,
    token_resolution: Any,
    *,
    operation: str,
) -> dict[str, Any]:
    identity_contract = request.execution_identity_contract.payload()
    identity_contract["authorization_status"] = token_resolution.authorization_status
    return {
        "operation": operation,
        "credential_mode": "USER_TOKEN",
        "actor_identity": "USER",
        "authorization_status": token_resolution.authorization_status,
        "authorization_error": token_resolution.error,
        "account_id": token_resolution.account_id,
        "execution_identity_contract": identity_contract,
        "provider_boundary": "user_token_fallback_used",
        "original_provider_boundary": "enterprise_realtime_not_integrated",
        "operational_source": "feishu_realtime",
        "fallback_used": True,
        "fallback_scope": "SELF",
        "cannot_escalate_to_company": True,
        "legacy_cli_fallback_used": False,
        "workevent_as_realtime_source": False,
        "extracted_item_as_realtime_source": False,
    }


def _missing_params_result(
    *,
    source: str,
    result_type: str,
    operation: str,
    missing: list[str] | tuple[str, ...],
    answer: str,
    error: str,
) -> ProviderResult:
    return ProviderResult(
        source=source,
        status="error",
        result_type=result_type,
        metadata={
            "operation": operation,
            "error_type": "missing_params",
            "missing_params": list(missing),
            "v5_only": True,
            "legacy_fallback": False,
        },
        answer=answer,
        error=error,
    )


def _provider_error_metadata(result) -> dict[str, Any]:
    if _provider_status(result) == "success":
        return {}
    return {
        "error_type": "tool_execution_failed",
        "tool_error": result.error or "",
    }


def _provider_label(source: str) -> str:
    return {
        "task": "任务",
        "calendar": "日程",
        "mail": "邮件",
        "approval": "审批",
        "im": "消息",
        "people": "通讯录",
        "base": "多维表格",
        "docs": "飞书文档",
        "wiki": "飞书知识库",
        "drive": "飞书云盘",
        "sheets": "飞书电子表格",
        "company_profile": "公司档案",
        "knowledge": "企业知识库",
        "workevent": "工作事件",
        "memory": "长期记忆",
        "web": "网页搜索",
    }.get(source, source)


def _docs_read_answer(item: dict[str, Any]) -> str:
    preview = str(item.get("content_preview") or "").strip()
    lines = [f"已读取飞书文档：{item.get('document_id') or ''}"]
    if preview:
        lines.append("")
        lines.append(preview[:1000])
    else:
        lines.append("文档没有返回可展示正文。")
    return "\n".join(lines)


def _wiki_space_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "space_id": item.get("space_id") or item.get("id") or "",
        "name": item.get("name") or item.get("title") or item.get("space_id") or "",
        "description": item.get("description") or "",
    }


def _wiki_node_item(item: dict[str, Any], *, space_id: str) -> dict[str, Any]:
    return {
        "space_id": space_id,
        "node_token": item.get("node_token") or item.get("token") or "",
        "obj_token": item.get("obj_token") or "",
        "obj_type": item.get("obj_type") or "",
        "title": item.get("title") or item.get("name") or item.get("node_token") or "",
    }


def _wiki_spaces_answer(items: tuple[dict[str, Any], ...]) -> str:
    if not items:
        return "没有查到可用的飞书知识空间。"
    lines = [f"查询到 {len(items)} 个飞书知识空间："]
    for index, item in enumerate(items[:10], start=1):
        lines.append(f"{index}. {item.get('name') or '未命名知识空间'}｜{item.get('space_id') or ''}")
    return "\n".join(lines)


def _wiki_nodes_answer(items: tuple[dict[str, Any], ...], *, space_id: str) -> str:
    if not items:
        return f"知识空间 {space_id} 下没有查到节点。"
    lines = [f"知识空间 {space_id} 下查询到 {len(items)} 个节点："]
    for index, item in enumerate(items[:10], start=1):
        lines.append(f"{index}. {item.get('title') or '未命名节点'}｜{item.get('obj_type') or ''}｜{item.get('node_token') or item.get('obj_token') or ''}")
    return "\n".join(lines)


def _drive_file_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": item.get("name") or item.get("title") or item.get("token") or "",
        "token": item.get("token") or item.get("file_token") or item.get("document_id") or "",
        "type": item.get("type") or item.get("file_type") or item.get("mime_type") or "",
        "url": item.get("url") or item.get("link") or "",
    }


def _drive_files_answer(items: tuple[dict[str, Any], ...]) -> str:
    if not items:
        return "没有查到可展示的飞书云盘文件。"
    lines = [f"查询到 {len(items)} 个飞书云盘文件："]
    for index, item in enumerate(items[:10], start=1):
        parts = [str(item.get("name") or "未命名文件")]
        if item.get("type"):
            parts.append(str(item.get("type")))
        if item.get("token"):
            parts.append(str(item.get("token")))
        lines.append(f"{index}. {'｜'.join(parts)}")
    return "\n".join(lines)


def _vc_meeting_item(item: dict[str, Any]) -> dict[str, Any]:
    meeting = item.get("meeting") if isinstance(item.get("meeting"), dict) else item
    return {
        "meeting_id": meeting.get("meeting_id") or meeting.get("id") or "",
        "topic": meeting.get("topic") or meeting.get("subject") or meeting.get("title") or meeting.get("meeting_topic") or "",
        "start_time": meeting.get("start_time") or meeting.get("start") or "",
        "end_time": meeting.get("end_time") or meeting.get("end") or "",
        "status": meeting.get("status") or meeting.get("meeting_status") or "",
    }


def _vc_meetings_answer(items: tuple[dict[str, Any], ...]) -> str:
    if not items:
        return "最近 7 天没有查到飞书历史会议。"
    lines = [f"查询到 {len(items)} 个飞书历史会议："]
    for index, item in enumerate(items[:10], start=1):
        parts = [str(item.get("topic") or item.get("meeting_id") or "未命名会议")]
        if item.get("start_time"):
            parts.append(f"开始：{item.get('start_time')}")
        if item.get("meeting_id"):
            parts.append(str(item.get("meeting_id")))
        lines.append(f"{index}. {'｜'.join(parts)}")
    return "\n".join(lines)


def _attendance_items_from_payload(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    for key in ("user_task_results", "user_tasks", "task_results", "records", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item if isinstance(item, dict) else {"value": item} for item in value]
    data = payload.get("data")
    if isinstance(data, dict):
        return _attendance_items_from_payload(data)
    return []


def _attendance_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": item.get("check_date") or item.get("date") or item.get("day") or "",
        "user_id": item.get("user_id") or item.get("employee_no") or "",
        "status": item.get("status") or item.get("check_status") or item.get("attendance_status") or "",
        "result": item.get("result") or item.get("task_result") or item.get("value") or "",
        "raw": item,
    }


def _attendance_answer(items: tuple[dict[str, Any], ...], *, start_date: str, end_date: str) -> str:
    if not items:
        return f"{start_date} 至 {end_date} 没有查到可展示的考勤记录。"
    lines = [f"查询到 {len(items)} 条考勤记录（{start_date} 至 {end_date}）："]
    for index, item in enumerate(items[:10], start=1):
        parts = [str(item.get("date") or "未标明日期")]
        if item.get("status"):
            parts.append(f"状态：{item.get('status')}")
        if item.get("result"):
            parts.append(str(item.get("result"))[:80])
        lines.append(f"{index}. {'｜'.join(parts)}")
    return "\n".join(lines)


def _okr_cycles_from_payload(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    for key in ("cycles", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    data = payload.get("data")
    if isinstance(data, dict):
        return _okr_cycles_from_payload(data)
    return []


def _okr_objectives_from_payload(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    for key in ("objectives", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    data = payload.get("data")
    if isinstance(data, dict):
        return _okr_objectives_from_payload(data)
    return []


def _okr_cycle_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "cycle_id": item.get("id") or item.get("cycle_id") or "",
        "tenant_cycle_id": item.get("tenant_cycle_id") or "",
        "start_time": item.get("start_time") or "",
        "end_time": item.get("end_time") or "",
        "status": item.get("cycle_status") or item.get("status") or "",
        "score": item.get("score"),
    }


def _active_okr_cycle(cycles: tuple[dict[str, Any], ...]) -> dict[str, Any] | None:
    if not cycles:
        return None
    for item in cycles:
        if str(item.get("status") or "").lower() in {"normal", "default", "1", "0"}:
            return item
    return cycles[0]


def _okr_objective_item(item: dict[str, Any], *, cycle: dict[str, Any]) -> dict[str, Any]:
    key_results = item.get("key_results") if isinstance(item.get("key_results"), list) else []
    return {
        "objective_id": item.get("id") or item.get("objective_id") or "",
        "cycle_id": item.get("cycle_id") or cycle.get("cycle_id") or "",
        "cycle_start": cycle.get("start_time") or "",
        "cycle_end": cycle.get("end_time") or "",
        "content": _okr_content_text(item.get("content")),
        "notes": _okr_content_text(item.get("notes")),
        "score": item.get("score"),
        "weight": item.get("weight"),
        "deadline": item.get("deadline") or "",
        "key_result_count": len(key_results),
        "key_results": tuple(_okr_key_result_item(entry) for entry in key_results if isinstance(entry, dict)),
    }


def _okr_key_result_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "key_result_id": item.get("id") or item.get("key_result_id") or "",
        "content": _okr_content_text(item.get("content")),
        "score": item.get("score"),
        "weight": item.get("weight"),
        "deadline": item.get("deadline") or "",
    }


def _okr_content_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return _okr_content_text(json.loads(stripped))
            except Exception:
                return " ".join(stripped.split())[:180]
        return " ".join(stripped.split())[:180]
    if isinstance(value, dict):
        texts = []
        for key in ("text", "content", "plain_text", "title"):
            if value.get(key):
                texts.append(_okr_content_text(value.get(key)))
        children = value.get("children") or value.get("blocks") or value.get("elements")
        if isinstance(children, list):
            texts.extend(_okr_content_text(item) for item in children[:6])
        return " ".join(item for item in texts if item).strip()[:180]
    if isinstance(value, list):
        return " ".join(_okr_content_text(item) for item in value[:8]).strip()[:180]
    return str(value)[:180]


def _okr_objectives_answer(items: tuple[dict[str, Any], ...], *, cycle: dict[str, Any]) -> str:
    cycle_label = _okr_cycle_label(cycle)
    if not items:
        return f"{cycle_label} 没有查到 OKR 目标。"
    lines = [f"{cycle_label} 查询到 {len(items)} 个 OKR 目标："]
    for index, item in enumerate(items[:8], start=1):
        parts = [str(item.get("content") or item.get("objective_id") or "未命名目标")]
        if item.get("score") not in (None, ""):
            parts.append(f"得分：{item.get('score')}")
        if item.get("key_result_count") not in (None, ""):
            parts.append(f"KR：{item.get('key_result_count')} 个")
        lines.append(f"{index}. {'｜'.join(parts)}")
    return "\n".join(lines)


def _okr_cycle_label(cycle: dict[str, Any]) -> str:
    start = str(cycle.get("start_time") or "").strip()
    end = str(cycle.get("end_time") or "").strip()
    if start or end:
        return f"OKR 周期 {start[:10]} 至 {end[:10]}".strip()
    return "当前 OKR 周期"


def _slides_token_from_text(text: str) -> str:
    value = str(text or "")
    match = re.search(r"/slides/([A-Za-z0-9_-]+)", value)
    if match:
        return match.group(1)
    match = re.search(r"\b(slides[A-Za-z0-9_-]{8,})\b", value)
    return match.group(1) if match else ""


def _slides_item(payload: Any, *, presentation_id: str) -> dict[str, Any]:
    presentation = payload.get("xml_presentation") if isinstance(payload, dict) and isinstance(payload.get("xml_presentation"), dict) else payload
    presentation = presentation if isinstance(presentation, dict) else {}
    content = str(presentation.get("content") or payload.get("content") if isinstance(payload, dict) else "").strip()
    title = str(presentation.get("title") or _slides_title_from_xml(content) or presentation_id).strip()
    slide_count = _slides_count(content)
    return {
        "xml_presentation_id": presentation.get("presentation_id") or presentation.get("xml_presentation_id") or presentation_id,
        "title": title,
        "slide_count": slide_count,
        "revision_id": presentation.get("revision_id") or "",
        "content_preview": _slides_text_preview(content),
        "content_chars": len(content),
    }


def _slides_title_from_xml(content: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", content, flags=re.S)
    if not match:
        return ""
    return _strip_xml_text(match.group(1))[:80]


def _slides_count(content: str) -> int:
    return len(re.findall(r"<slide(?:\s|>)", content))


def _slides_text_preview(content: str) -> str:
    if not content:
        return ""
    text = _strip_xml_text(content)
    return text[:600]


def _strip_xml_text(content: str) -> str:
    text = re.sub(r"<[^>]+>", " ", str(content or ""))
    return " ".join(text.split())


def _slides_read_answer(item: dict[str, Any]) -> str:
    lines = [f"已读取飞书幻灯片：{item.get('title') or item.get('xml_presentation_id') or ''}"]
    if item.get("slide_count") not in (None, ""):
        lines.append(f"页数：{item.get('slide_count')}")
    if item.get("revision_id"):
        lines.append(f"版本：{item.get('revision_id')}")
    preview = str(item.get("content_preview") or "").strip()
    if preview:
        lines.append("")
        lines.append(preview[:600])
    else:
        lines.append("没有解析到可展示的文字预览。")
    return "\n".join(lines)


def _whiteboard_token_from_text(text: str) -> str:
    match = re.search(r"\b(wbcn[A-Za-z0-9_-]+)\b", str(text or ""))
    return match.group(1) if match else ""


def _whiteboard_item(output: str, *, whiteboard_token: str) -> dict[str, Any]:
    content = str(output or "").strip()
    code_type = "mermaid" if "graph " in content or "flowchart " in content or "sequenceDiagram" in content else ""
    if not code_type and "@startuml" in content:
        code_type = "plantuml"
    return {
        "whiteboard_token": whiteboard_token,
        "code_type": code_type or "unknown",
        "content_preview": content[:800],
        "content_chars": len(content),
        "has_code": bool(content and "不存在" not in content and "没有" not in content),
    }


def _whiteboard_read_answer(item: dict[str, Any]) -> str:
    token = str(item.get("whiteboard_token") or "").strip()
    lines = [f"已读取飞书画板：{token}"]
    code_type = str(item.get("code_type") or "").strip()
    if code_type and code_type != "unknown":
        lines.append(f"代码类型：{code_type}")
    preview = str(item.get("content_preview") or "").strip()
    if preview:
        lines.append("")
        lines.append(preview[:600])
    else:
        lines.append("没有解析到可展示的画板代码。")
    return "\n".join(lines)


def _run_lark_cli_text(args: list[str], *, cli_profile: str | None, action: str, timeout: int = 30) -> str:
    return run_lark_cli_text_via_mcp(args, cli_profile=cli_profile, action=action, timeout=timeout)


def _run_lark_cli_json(args: list[str], *, cli_profile: str | None, action: str, timeout: int = 30) -> dict[str, Any]:
    return run_lark_cli_json_via_mcp(args, cli_profile=cli_profile, action=action, timeout=timeout)


def _cli_args_with_profile(args: list[str], *, cli_profile: str | None) -> list[str]:
    profile = str(cli_profile or "").strip()
    if not profile or not args or args[0] != "lark-cli" or "--profile" in args:
        return args
    if not all(char.isalnum() or char in {"-", "_", "."} for char in profile):
        raise ValueError("Feishu CLI profile contains unsupported characters.")
    return ["lark-cli", "--profile", profile, *args[1:]]


def _provider_status(result) -> str:
    if result.status == ToolExecutionStatus.SUCCESS:
        return "success"
    if result.status == ToolExecutionStatus.DENIED:
        return "denied"
    return "error"


def _cached_tool_result():
    return SimpleNamespace(
        status=ToolExecutionStatus.SUCCESS,
        error="",
        answer="",
        structured_result={},
    )


def _tool_payload(result) -> dict[str, Any]:
    structured = result.structured_result if isinstance(result.structured_result, dict) else {}
    payload = structured.get("response_payload")
    if isinstance(payload, dict):
        return payload
    response_text = structured.get("response_text")
    if isinstance(response_text, str):
        parsed = _json_object(response_text)
        if parsed:
            return parsed
    if isinstance(result.answer, str):
        parsed = _json_object(result.answer)
        if parsed:
            return parsed
    return {}


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(coro)).result()


def _execute_controlled_task_create(
    request: ProviderRequest,
    *,
    db: Session,
    app_config: Any,
    summary: str,
    params: dict[str, Any],
    user_access_token: str,
) -> dict[str, Any]:
    company_id = request.context.runtime_scope.active_company_id
    if company_id is None:
        raise ValueError("Runtime V5 task create requires active_company_id.")
    tool_context = ToolContext(
        db=db,
        company_id=company_id,
        actor=_bot_actor_from_runtime_context(request.context),
        chat_id=request.context.chat_id,
        cli_profile="",
    )
    tool_params = {
        "api_entrypoint": "runtime_controlled_write",
        "app_config": app_config,
        "summary": summary,
        "description": str(params.get("description") or "") or None,
        "due": params.get("due") if isinstance(params.get("due"), dict) else None,
        "members": params.get("members") if isinstance(params.get("members"), list) else None,
        "tasklists": params.get("tasklists") if isinstance(params.get("tasklists"), list) else None,
        "client_token": str(params.get("client_token") or "") or None,
        "user_id_type": str(params.get("user_id_type") or "open_id"),
        "user_access_token": user_access_token,
        "response_format": "raw_json",
        "confirmed": True,
    }
    tool_request = ToolRequest(
        tool_name="feishu_task_create",
        question=request.intent.canonical_question or request.context.current_message,
        normalized_command=request.intent.canonical_question or request.context.current_message,
        params=tool_params,
    )
    tool_request.params["confirmation_token"] = feishu_write_confirmation_token(tool_context, tool_request)
    raw = execute_feishu_api_tool(tool_context, tool_request)
    payload = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
    return payload if isinstance(payload, dict) else {}


def _active_feishu_app_config(db: Session, company_id: Any) -> FeishuAppConfig | None:
    if company_id is None:
        return None
    return db.scalar(
        select(FeishuAppConfig)
        .where(FeishuAppConfig.company_id == company_id)
        .where(FeishuAppConfig.is_active.is_(True))
    )


def _feishu_response_data(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    return data if data is not None else payload


def _safe_runtime_error(exc: Exception) -> str:
    text = str(exc)
    if "Feishu API HTTP error" in text:
        return "飞书审批实例明细接口返回错误，已先展示待办摘要。"
    return text[:160]


def _json_object(text: str) -> dict[str, Any] | None:
    value = text.strip()
    if not value or not value.startswith("{"):
        return None
    try:
        payload = json.loads(value)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _first_text_param(params: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _document_token_from_text(text: str) -> str:
    match = re.search(r"\b((?:doccn|doxcn|docxcn)[-A-Za-z0-9_]+)\b", str(text or ""))
    return match.group(1) if match else ""


def _document_type_for_token(token: str) -> str:
    value = str(token or "").lower()
    if value.startswith("doccn"):
        return "doc"
    if value.startswith(("doxcn", "docxcn")):
        return "docx"
    return "docx"


def _registered_resource_document_type(config: dict[str, Any], token: str) -> str:
    for value in _registered_resource_document_type_candidates(config):
        normalized = str(value or "").strip().lower()
        if normalized:
            return normalized
    return _document_type_for_known_document_token(token)


def _registered_resource_document_type_candidates(config: dict[str, Any]) -> tuple[Any, ...]:
    settings = config.get("settings") if isinstance(config.get("settings"), dict) else {}
    raw = settings.get("raw") if isinstance(settings.get("raw"), dict) else {}
    return (
        config.get("document_type"),
        settings.get("document_type"),
        raw.get("type"),
        raw.get("docs_type"),
        raw.get("file_type"),
    )


def _document_type_for_known_document_token(token: str) -> str:
    value = str(token or "").lower()
    if value.startswith("doccn"):
        return "doc"
    if value.startswith(("doxcn", "docxcn")):
        return "docx"
    return ""


def _items_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in (
        "items",
        "records",
        "tasks",
        "task_list",
        "approvals",
        "instances",
        "instance_list",
        "users",
        "messages",
        "message_list",
        "threads",
        "mails",
        "files",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            return [item if isinstance(item, dict) else {"value": item} for item in value]
    data = payload.get("data")
    if isinstance(data, list):
        return [item if isinstance(item, dict) else {"value": item} for item in data]
    if isinstance(data, dict):
        return _items_from_payload(data)
    return []


def _user_item(user: dict[str, Any]) -> dict[str, Any]:
    return normalize_people_item(user)


def _people_items_from_snapshot(payload: dict[str, Any] | None, keyword: str) -> tuple[dict[str, Any], ...]:
    return _people_resolve_from_snapshot(payload, keyword).items


def _people_resolve_from_snapshot(payload: dict[str, Any] | None, keyword: str):
    if not isinstance(payload, dict):
        return resolve_people_from_items(keyword, ())
    users = payload.get("users") if isinstance(payload.get("users"), list) else []
    return resolve_people_from_items(keyword, normalize_people_items(users))


def _enrich_people_items_from_foundation(db: Session | None, items: tuple[dict[str, Any], ...], *, company_id: Any) -> tuple[dict[str, Any], ...]:
    if db is None or not items or not company_id:
        return items
    keys = {
        str(value).strip()
        for item in items
        for value in (
            item.get("open_id"),
            item.get("user_id"),
            item.get("name"),
            item.get("leader_user_id"),
        )
        if str(value or "").strip()
    }
    if not keys:
        return items
    users = db.scalars(
        select(OrganizationUser)
        .where(OrganizationUser.company_id == company_id)
        .where(OrganizationUser.status == "active")
        .where(
            or_(
                OrganizationUser.open_id.in_(keys),
                OrganizationUser.source_user_id.in_(keys),
                OrganizationUser.name.in_(keys),
            )
        )
    ).all()
    by_key: dict[str, OrganizationUser] = {}
    for user in users:
        for value in (user.open_id, user.source_user_id, user.name):
            key = str(value or "").strip()
            if key:
                by_key[key] = user
    enriched: list[dict[str, Any]] = []
    for item in items:
        current = by_key.get(str(item.get("open_id") or "").strip()) or by_key.get(str(item.get("user_id") or "").strip()) or by_key.get(str(item.get("name") or "").strip())
        metadata = current.metadata_json if current is not None and isinstance(current.metadata_json, dict) else {}
        leader_id = str(item.get("leader_user_id") or metadata.get("leader_user_id") or "").strip()
        leader = by_key.get(leader_id)
        leader_name = str(item.get("leader") or item.get("leader_name") or "").strip()
        if leader is not None:
            leader_name = leader.name
            next_item = dict(item)
            next_item["leader_title"] = leader.job_title or ""
            leader_metadata = leader.metadata_json if isinstance(leader.metadata_json, dict) else {}
            leader_departments = leader_metadata.get("department_names") if isinstance(leader_metadata.get("department_names"), list) else []
            next_item["leader_department"] = "、".join(str(value) for value in leader_departments if str(value).strip())
            if _looks_like_identifier_name(leader.name):
                next_item["leader_name_is_identifier"] = True
        else:
            next_item = dict(item)
        if current is not None:
            next_item.setdefault("open_id", current.open_id)
            next_item.setdefault("user_id", current.source_user_id or "")
            if not next_item.get("title"):
                next_item["title"] = current.job_title or ""
            if not next_item.get("job_title"):
                next_item["job_title"] = current.job_title or ""
            if not next_item.get("email"):
                next_item["email"] = current.email or ""
            if not next_item.get("mobile"):
                next_item["mobile"] = current.mobile or ""
            if metadata.get("employee_no"):
                next_item["employee_no"] = str(metadata.get("employee_no") or "")
        if leader_id:
            next_item["leader_user_id"] = leader_id
        if leader_name:
            next_item["leader"] = leader_name
            next_item["leader_name"] = leader_name
        enriched.append(next_item)
    return tuple(enriched)


def _looks_like_identifier_name(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and bool(re.fullmatch(r"[A-Za-z0-9._-]{3,}", text))


def _approval_item(item: dict[str, Any]) -> dict[str, Any]:
    detail = item.get("instance_detail") if isinstance(item.get("instance_detail"), dict) else {}
    fields = dict(approval_form_fields(detail.get("form"), max_fields=50))
    amount = approval_amount(fields)
    applicant = _approval_applicant_for_display(item, fields)
    assessment = item.get("_approval_assessment") if isinstance(item.get("_approval_assessment"), dict) else _approval_assessment(item, attachment_results=[])
    instance_code = approval_formatters.approval_instance_code(item)
    serial_number = _approval_serial_number_for_display(item)
    approval_code = str(item.get("approval_code") or item.get("definition_code") or detail.get("approval_code") or detail.get("definition_code") or "").strip()
    task_id = str(item.get("task_id") or item.get("id") or "").strip()
    title = (
        item.get("title")
        or item.get("approval_name")
        or detail.get("approval_name")
        or detail.get("definition_name")
        or item.get("name")
        or item.get("summary")
        or instance_code
        or task_id
        or ""
    )
    return {
        "title": title,
        "applicant": applicant,
        "applicant_name": applicant,
        "status": item.get("status") or item.get("task_status") or "",
        "amount": amount if amount is not None else item.get("amount") or item.get("total_amount") or item.get("form_amount") or "",
        "form_amount": amount if amount is not None else item.get("form_amount") or "",
        "id": task_id or instance_code or item.get("id") or "",
        "approval_code": approval_code,
        "definition_code": approval_code,
        "instance_code": instance_code,
        "process_code": instance_code,
        "serial_number": serial_number,
        "task_id": task_id,
        "assessment": assessment,
        "raw": item,
    }


def _task_item(item: dict[str, Any]) -> dict[str, Any]:
    task = item.get("task") if isinstance(item.get("task"), dict) else item
    return {
        "title": task.get("summary") or task.get("title") or task.get("name") or task.get("guid") or "",
        "guid": task.get("guid") or task.get("task_guid") or task.get("task_id") or task.get("id") or "",
        "url": task.get("url") or task.get("app_link") or task.get("link") or "",
        "status": task.get("status") or task.get("task_status") or "",
        "due": task.get("due") or task.get("due_time") or "",
        "owner": task.get("owner") or task.get("assignee") or "",
        "raw": task,
    }


def _people_search_answer(
    keyword: str,
    items: tuple[dict[str, Any], ...],
    *,
    question: str = "",
    match_type: str = "",
    requested_fields: tuple[str, ...] = (),
) -> str:
    if not items:
        return f"我在当前可读通讯录里没找到「{keyword}」。你可以换成姓名全称、手机号或邮箱再查一次。"
    if match_type == "near_identity_candidate":
        names = "、".join(str(item.get("name") or "").strip() for item in items[:3] if str(item.get("name") or "").strip())
        if names:
            return f"我没有精确找到「{keyword}」，通讯录里相近的是：{names}。你是不是指其中一位？"
        return f"我没有精确找到「{keyword}」，但找到了一些相近人员；请确认姓名后我再查。"
    if len(items) == 1:
        focused = _focused_people_answer(items[0], question=question, requested_fields=requested_fields)
        if focused:
            return focused
    lines = [f"我在通讯录里找到 {len(items)} 位和「{keyword}」相关的人："]
    for item in items[:10]:
        name = str(item.get("name") or "未知")
        title = str(item.get("title") or "").strip()
        department = str(item.get("department") or "").strip()
        email = str(item.get("email") or "").strip()
        mobile = str(item.get("mobile") or "").strip()
        suffix = "，".join(part for part in (title, department, f"邮箱：{email}" if email else "", f"手机：{mobile}" if mobile else "") if part)
        lines.append(f"{name}（{suffix}）" if suffix else name)
    return "\n".join(lines)


def _people_lookup_context_metadata(
    items: tuple[dict[str, Any], ...],
    *,
    keyword: str,
    query_field: str = "",
    query_fields: tuple[str, ...] = (),
    match_type: str = "",
    intent_entities: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolution = _people_identity_resolution(match_type, items)
    fields = query_fields or ((query_field,) if query_field else ())
    query = domain_query_payload(intent_entities)
    output_mode = str(query.get("output_mode") or "")
    presentation = "summary" if len(items) == 1 and fields and resolution == "exact" and output_mode != "detail" else "detail"
    item = items[0] if len(items) == 1 and isinstance(items[0], dict) else {}
    visible_fields = tuple(field for field in ("title", "mobile", "email", "leader", "gender") if _people_item_field_value(item, field))
    sensitive_fields = tuple(field for field in ("mobile", "email") if _people_item_field_value(item, field))
    field_reliability = {
        "title": "source" if str(item.get("title") or item.get("job_title") or "").strip() else "missing",
        "mobile": "source" if str(item.get("mobile") or "").strip() else "missing",
        "email": "source" if str(item.get("email") or "").strip() else "missing",
        "leader": "foundation" if _people_item_field_value(item, "leader") else "missing",
        "gender": "source" if _reliable_people_gender(item) else "missing",
    } if item else {}
    return {
        "result_context_presentation": presentation,
        "identity_resolution": resolution,
        "resource_scope": "organization",
        "domain_query": query,
        "sensitive_fields_present": sensitive_fields,
        "field_reliability": field_reliability,
        "people_context_frame": {
            "current_person": str(item.get("name") or keyword or "").strip(),
            "current_requested_field": query_field,
            "current_requested_fields": fields,
            "identity_resolution": resolution,
            "visible_fields": visible_fields,
        },
    }


def _people_identity_resolution(match_type: str, items: tuple[dict[str, Any], ...]) -> str:
    if match_type in {"exact", "exact_identity", "exact_name", "exact_email", "exact_mobile"}:
        return "exact"
    if match_type in {"near_identity_candidate", "near_candidate", "fuzzy"}:
        return "near_candidate"
    if len(items) == 1:
        return "exact"
    if items:
        return "multiple"
    return "unresolved"


def _people_query_field_from_request(request: ProviderRequest) -> str:
    entities = request.intent.entities if isinstance(request.intent.entities, dict) else {}
    value = str(entities.get("people_query_field") or "").strip()
    if value:
        return value
    return _people_query_field_from_text(request.context.current_message or request.intent.canonical_question)


def _people_query_fields_from_request(request: ProviderRequest) -> tuple[str, ...]:
    entities = request.intent.entities if isinstance(request.intent.entities, dict) else {}
    fields = domain_query_fields(entities)
    if fields:
        return fields
    field = _people_query_field_from_request(request)
    return (field,) if field else ()


def _people_query_field_from_text(text: str) -> str:
    compact = re.sub(r"\s+", "", str(text or "").lower())
    if any(token in compact for token in ("电话", "号码", "手机号", "手机")):
        return "mobile"
    if "邮箱" in compact:
        return "email"
    if any(token in compact for token in ("职位", "岗位", "职务")):
        return "title"
    if any(token in compact for token in ("直属上级", "上级", "领导")):
        return "leader"
    if any(token in compact for token in ("男还是女", "女还是男", "男性还是女性", "性别")):
        return "gender"
    return ""


def _people_items_have_field(items: tuple[dict[str, Any], ...], query_field: str) -> bool:
    if not items:
        return False
    return all(_people_item_field_value(item, query_field) for item in items)


def _people_item_field_value(item: dict[str, Any], query_field: str) -> str:
    if query_field == "mobile":
        return str(item.get("mobile") or "").strip()
    if query_field == "email":
        return str(item.get("email") or "").strip()
    if query_field == "title":
        return str(item.get("title") or item.get("job_title") or "").strip()
    if query_field == "leader":
        return str(item.get("leader") or item.get("leader_name") or "").strip()
    if query_field == "gender":
        return _reliable_people_gender(item)
    return ""


def _reliable_people_gender(item: dict[str, Any]) -> str:
    if str(item.get("gender_source") or "").strip() != "source":
        return ""
    return normalize_gender(item.get("gender_normalized") or item.get("gender"))


def _matching_people_item(item: dict[str, Any], candidates: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    for key in ("open_id", "user_id", "email", "mobile", "name"):
        value = str(item.get(key) or "").strip()
        if not value:
            continue
        for candidate in candidates:
            if str(candidate.get(key) or "").strip() == value:
                return candidate
    return {}


def _focused_people_answer(item: dict[str, Any], *, question: str = "", requested_fields: tuple[str, ...] = ()) -> str:
    name = str(item.get("name") or "这位同事").strip()
    requested_fields = requested_fields or _people_query_fields_from_text(question)
    if not requested_fields:
        return ""
    parts: list[str] = []
    missing: list[str] = []
    if "gender" in requested_fields:
        gender = _reliable_people_gender(item)
        if gender == "male":
            parts.append("性别是男性")
        elif gender == "female":
            parts.append("性别是女性")
        else:
            missing.append("可靠性别字段")
    if "title" in requested_fields:
        title = str(item.get("title") or item.get("job_title") or "").strip()
        department = str(item.get("department") or "").strip()
        if title and department:
            parts.append(f"{'是' if requested_fields == ('title',) else '职位是'}{department}的{title}")
        elif title:
            parts.append(f"{'岗位是' if requested_fields == ('title',) else '职位是'}{title}")
        else:
            missing.append("职位")
    if "leader" in requested_fields:
        leader = str(item.get("leader") or item.get("leader_name") or "").strip()
        if leader:
            if item.get("leader_name_is_identifier"):
                leader_detail = "，".join(
                    part
                    for part in (
                        str(item.get("leader_title") or "").strip(),
                        str(item.get("leader_department") or "").strip(),
                    )
                    if part
                )
                suffix = f"（{leader_detail}）" if leader_detail else ""
                parts.append(f"直属上级在通讯录里的显示名是 {leader}{suffix}，当前没有可确认的中文姓名")
            else:
                parts.append(f"直属上级是{leader}")
        else:
            missing.append("直属上级")
    if "mobile" in requested_fields:
        mobile = str(item.get("mobile") or "").strip()
        if mobile:
            parts.append(f"手机号是 {mobile}")
        else:
            missing.append("手机号")
    if "email" in requested_fields:
        email = str(item.get("email") or "").strip()
        if email:
            parts.append(f"邮箱是 {email}")
        else:
            missing.append("邮箱")
    if parts and missing:
        return f"{name}的" + "，".join(parts) + f"；当前可读通讯录没有提供{ '、'.join(missing) }。"
    if parts:
        if requested_fields == ("title",) and len(parts) == 1 and parts[0].startswith(("是", "岗位是")):
            return f"{name}{parts[0]}。"
        return f"{name}的" + "，".join(parts) + "。"
    if missing:
        if "可靠性别字段" in missing:
            return f"我查到了{name}，但当前可读通讯录没有提供可靠性别字段，我不会根据名字判断，也不会把这位同事纳入明确男性或女性名单。"
        return f"我查到了{name}，但当前可读通讯录没有提供{ '、'.join(missing) }。"
    return ""


def _people_query_fields_from_text(text: str) -> tuple[str, ...]:
    compact = re.sub(r"\s+", "", str(text or "").lower())
    fields: list[str] = []
    if any(token in compact for token in ("男还是女", "女还是男", "男性还是女性", "性别")):
        fields.append("gender")
    if any(token in compact for token in ("岗位", "职位", "职务")):
        fields.append("title")
    if any(token in compact for token in ("直属上级", "上级", "领导")):
        fields.append("leader")
    if any(token in compact for token in ("电话", "号码", "手机号", "手机")):
        fields.append("mobile")
    if "邮箱" in compact:
        fields.append("email")
    return tuple(fields)


def _organization_relation_from_request(request: ProviderRequest) -> str:
    entities = request.intent.entities if isinstance(request.intent.entities, dict) else {}
    relation = str(entities.get("organization_relation") or "").strip()
    if relation:
        return relation
    domain_query = entities.get("domain_query") if isinstance(entities.get("domain_query"), dict) else {}
    filters = domain_query.get("filters") if isinstance(domain_query.get("filters"), dict) else {}
    return str(filters.get("organization_relation") or "").strip()


def _department_relation_result(
    *,
    relation: str,
    keyword: str,
    items: tuple[dict[str, Any], ...],
    metadata: dict[str, Any],
    resolution: Any | None = None,
) -> tuple[tuple[dict[str, Any], ...], str, int]:
    if not relation:
        return (), "", 0
    resolved_name = _organization_resolved_name(resolution) or str(metadata.get("resolved_department_name") or "").strip() or keyword
    if relation == "children":
        child_items = tuple(
            {
                "name": str(item.get("name") or "").strip(),
                "member_count": int(item.get("member_count") or 0),
                "source_department_id": str(item.get("source_department_id") or "").strip(),
                "resource_type": "organization_department",
            }
            for item in metadata.get("child_member_counts", [])
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        )
        if not child_items:
            return (), f"{resolved_name}下面暂时没有可见的直属子部门。", 0
        names = "、".join(str(item.get("name") or "") for item in child_items)
        return child_items, f"{resolved_name}下面有 {len(child_items)} 个直属子部门：{names}。", len(child_items)
    if relation == "leader":
        leader_items = tuple(
            dict(item)
            for item in metadata.get("leader_items", [])
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        )
        if leader_items:
            names = "、".join(str(item.get("name") or "") for item in leader_items)
            return leader_items, f"{resolved_name}的负责人是 {names}。", len(leader_items)
        leaders = tuple(str(item).strip() for item in metadata.get("leader_source_user_ids", []) if str(item).strip())
        if leaders:
            return (), f"{resolved_name}有负责人标识，但当前可读组织数据里还没有解析到中文姓名。", 0
        return (), f"{resolved_name}当前没有可确认的负责人字段。", 0
    return items, "", len(items)


def _department_members_answer(keyword: str, items: tuple[dict[str, Any], ...], *, resolution: Any | None = None) -> str:
    resolved_name = _organization_resolved_name(resolution)
    display_name = resolved_name or keyword
    correction = ""
    if resolved_name and keyword and resolved_name != keyword:
        requested_name = _organization_requested_name(keyword, resolved_name)
        correction = f"你说的「{requested_name}」我按组织结构匹配到「{resolved_name}」。" if requested_name != resolved_name else ""
    if not items:
        if correction:
            return f"{correction}但我在当前可读通讯录里没有看到「{display_name}」成员。"
        return f"我在当前可读通讯录里没找到「{keyword}」相关成员。可能是部门名称不一致，也可能这个部门不在当前授权范围里。"
    if len(items) == 1:
        name = str(items[0].get("name") or "未知").strip() or "未知"
        if correction:
            return f"{correction}目前只有 {name} 1 位。"
        return f"{display_name}目前 1 位，是{name}。"
    names = "、".join(str(item.get("name") or "未知").strip() or "未知" for item in items[:30])
    header = f"{correction}{display_name}目前 {len(items)} 人" if correction else f"{display_name}目前 {len(items)} 人"
    if names and len(items) <= 30:
        return f"{header}：{names}。"
    lines = [f"{header}："]
    for index, item in enumerate(items[:30], start=1):
        name = str(item.get("name") or "未知")
        title = str(item.get("title") or "").strip()
        email = str(item.get("email") or "").strip()
        mobile = str(item.get("mobile") or "").strip()
        suffix = "，".join(part for part in (title, f"邮箱：{email}" if email else "", f"手机：{mobile}" if mobile else "") if part)
        lines.append(f"{index}. {name}" + (f"（{suffix}）" if suffix else ""))
    return "\n".join(lines)


def _organization_requested_name(keyword: str, resolved_name: str) -> str:
    text = re.sub(r"\s+", "", str(keyword or ""))
    resolved_stem = _organization_display_stem(resolved_name)
    match = re.search(rf"({re.escape(resolved_stem)}(?:事业部|部门|中心|团队|小组|组|部)?)", text)
    if match:
        return match.group(1)
    return str(keyword or "").strip() or resolved_name


def _organization_display_stem(value: str) -> str:
    text = str(value or "").strip()
    for suffix in ("事业部", "部门", "中心", "小组", "团队", "部", "组"):
        if text.endswith(suffix) and len(text) > len(suffix):
            return text[: -len(suffix)]
    return text


def _organization_resolved_name(resolution: Any | None) -> str:
    if resolution is None:
        return ""
    if isinstance(resolution, dict):
        return str(resolution.get("resolved_name") or "").strip()
    return str(getattr(resolution, "resolved_name", "") or "").strip()


def _organization_resolution_metadata(resolution) -> dict[str, Any]:
    return {
        "query": getattr(resolution, "query", ""),
        "normalized_query": getattr(resolution, "normalized_query", ""),
        "resolved_type": getattr(resolution, "resolved_type", ""),
        "resolved_id": getattr(resolution, "resolved_id", ""),
        "resolved_name": getattr(resolution, "resolved_name", ""),
        "confidence": getattr(resolution, "confidence", 0.0),
        "reason": getattr(resolution, "reason", ""),
        "needs_clarification": getattr(resolution, "needs_clarification", False),
        "candidates": [
            {
                "target_type": item.target_type,
                "target_id": item.target_id,
                "name": item.name,
                "confidence": item.confidence,
                "reason": item.reason,
            }
            for item in getattr(resolution, "candidates", ())
        ],
    }


def _department_membership_result_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    if not metadata:
        return {}
    return {
        key: value
        for key, value in {
            "unique_member_count": metadata.get("unique_member_count"),
            "direct_member_count": metadata.get("direct_member_count"),
            "display_member_count": metadata.get("display_member_count"),
            "source_member_count": metadata.get("source_member_count"),
            "resolved_department_name": metadata.get("resolved_department_name"),
            "resolved_department_id": metadata.get("resolved_department_id"),
            "direct_child_count": metadata.get("direct_child_count"),
            "child_member_counts": metadata.get("child_member_counts"),
            "leader_items": metadata.get("leader_items"),
            "leader_source_user_ids": metadata.get("leader_source_user_ids"),
            "count_basis": metadata.get("count_basis"),
        }.items()
        if value not in (None, "", (), [])
    }


def _organization_resolution_failure_answer(keyword: str, resolution) -> str:
    candidates = getattr(resolution, "candidates", ()) or ()
    if candidates:
        names = "、".join(str(item.name) for item in candidates[:5] if str(item.name).strip())
        if names:
            return f"我没有唯一匹配到「{keyword}」这个组织对象。比较接近的是：{names}。你指的是哪一个？"
    return f"我没有找到「{keyword}」这个组织对象。可能是名称不一致，或者它不在当前可读组织范围里。"


def _department_item_matches(item: dict[str, Any], keyword: str) -> bool:
    return item in filter_people_by_department((item,), keyword)


def _task_list_answer(items: tuple[dict[str, Any], ...], *, query: str = "") -> str:
    if not items:
        return f"没有找到与「{query}」匹配的任务。" if query else "暂未查询到你的任务。"
    header = f"找到 {len(items)} 条与「{query}」相关的任务：" if query else f"你有 {len(items)} 条任务："
    lines = [header]
    for index, item in enumerate(items[:20], start=1):
        title = str(item.get("title") or "未命名任务")
        status = str(item.get("status") or "").strip()
        due = str(item.get("due") or "").strip()
        suffix = "，".join(part for part in (status, f"截止：{due}" if due else "") if part)
        lines.append(f"{index}. {title}" + (f"（{suffix}）" if suffix else ""))
    return "\n".join(lines)


def _approval_list_answer(items: tuple[dict[str, Any], ...]) -> str:
    if not items:
        return "暂未查询到待审批任务。"
    grouped = _group_approval_workbench_items(items[:10])
    lines = [f"待审批 {len(items)} 条，我建议这样处理："]
    lines.append(
        "可直接通过："
        f"{len(grouped['pass'])} 条；需看一下：{len(grouped['review'])} 条；不建议通过：{len(grouped['hold'])} 条。"
    )
    for group_key, title in (
        ("pass", "可直接通过"),
        ("review", "需看一下"),
        ("hold", "不建议通过"),
    ):
        group_items = grouped[group_key]
        if not group_items:
            continue
        lines.append("")
        lines.append(f"**{title}**")
        for index, item in group_items:
            lines.append(f"{index}. {_approval_workbench_line(item)}")
    lines.append("")
    lines.append("你可以点卡片按钮查看详情、通过、拒绝；批量处理可先勾选单据，再点「批量通过已选」。")
    return "\n".join(lines)


def _approval_fast_list_answer(items: tuple[dict[str, Any], ...]) -> str:
    if not items:
        return "当前没有查到待你审批的单子。"
    lines = [f"你有 {len(items)} 条待审批。我先把数量和标题发给你，完整审批工作台正在生成。"]
    for index, item in enumerate(items[:10], start=1):
        title = str(item.get("title") or "未命名审批")
        applicant = str(item.get("applicant") or "").strip()
        suffix = f"｜{applicant}" if applicant else ""
        lines.append(f"{index}. {title}{suffix}")
    lines.append("")
    lines.append("接下来会继续读取审批详情、附件、历史记录，并生成 AI 审批建议。稍后我会再发一条分组工作台。")
    return "\n".join(lines)


def _approval_initiated_list_answer(items: tuple[dict[str, Any], ...]) -> str:
    if not items:
        return "暂未查询到你发起的审批。"
    lines = [f"查询到你发起的审批 {len(items)} 条："]
    for index, item in enumerate(items[:20], start=1):
        title = str(item.get("title") or "未命名审批")
        applicant = str(item.get("applicant") or "").strip()
        status = str(item.get("status") or "").strip()
        suffix = "｜".join(part for part in (applicant, status) if part)
        lines.append(f"{index}. {title}" + (f"｜{suffix}" if suffix else ""))
    return "\n".join(lines)


def _approval_write_tool_name(operation: str) -> str:
    return {
        "approve": "feishu_approval_task_approve",
        "reject": "feishu_approval_task_reject",
        "transfer": "feishu_approval_task_transfer",
        "add_sign": "feishu_approval_task_add_sign",
        "rollback": "feishu_approval_task_rollback",
        "remind": "feishu_approval_instance_remind",
        "cancel": "feishu_approval_instance_cancel",
        "cc": "feishu_approval_instance_cc",
    }[operation]


def _approval_write_tool_params(operation: str, params: dict[str, Any]) -> dict[str, Any]:
    comment = str(params.get("comment") or "").strip()
    payload: dict[str, Any] = {"comment": comment or _approval_default_comment(operation)}
    if operation == "transfer":
        payload["transfer_user_id"] = params.get("transfer_user_id")
    elif operation == "add_sign":
        payload["add_sign_user_ids"] = _approval_string_list(params.get("add_sign_user_ids"))
        payload["add_sign_type"] = params.get("add_sign_type") or 1
        if params.get("approval_method"):
            payload["approval_method"] = params.get("approval_method")
    elif operation == "rollback":
        payload["node_ids"] = _approval_string_list(params.get("node_ids"))
    elif operation == "cc":
        payload["cc_user_ids"] = _approval_string_list(params.get("cc_user_ids"))
    return payload


def _approval_write_missing_params(operation: str, params: dict[str, Any]) -> list[str]:
    required: dict[str, tuple[str, ...]] = {
        "transfer": ("transfer_user_id",),
        "add_sign": ("add_sign_user_ids",),
        "rollback": ("node_ids",),
        "cc": ("cc_user_ids",),
    }
    missing: list[str] = []
    for key in required.get(operation, ()):
        value = params.get(key)
        if isinstance(value, list):
            if not [str(item).strip() for item in value if str(item).strip()]:
                missing.append(key)
        elif not str(value or "").strip():
            missing.append(key)
    return missing


def _approval_missing_param_answer(operation: str, missing: list[str]) -> str:
    hint_by_operation = {
        "transfer": "转交需要指定接收人。你可以说：转交给王云飞，并说明理由。",
        "add_sign": "加签需要指定加签人。你可以说：加签给王云飞，并说明理由。",
        "rollback": "退回需要指定退回节点。请先展开审批进度，选择要退回到的节点。",
        "cc": "抄送需要指定抄送人。你可以说：抄送给王云飞。",
    }
    return hint_by_operation.get(operation) or f"审批{_approval_action_text(operation)}还缺少参数：{', '.join(missing)}。"


def _approval_action_text(operation: str) -> str:
    return {
        "approve": "通过",
        "reject": "拒绝",
        "transfer": "转交",
        "add_sign": "加签",
        "rollback": "退回",
        "remind": "催办",
        "cancel": "撤回",
        "cc": "抄送",
    }.get(operation, operation)


def _approval_default_comment(operation: str) -> str:
    return {
        "approve": "同意",
        "reject": "拒绝",
        "transfer": "转交处理",
        "add_sign": "请协助审批",
        "rollback": "退回补充",
        "remind": "请尽快处理",
        "cancel": "撤回审批",
        "cc": "抄送知会",
    }.get(operation, "由数字参谋根据用户确认提交。")


def _approval_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.replace("，", ",").split(",") if part.strip()]
    return []


def _approval_initiated_tool_params(request: ProviderRequest) -> dict[str, Any]:
    params = {"page_size": request.params.get("page_size") or 20, "response_format": "raw_json"}
    for key in (
        "approval_code",
        "definition_code",
        "open_id",
        "user_id",
        "instance_start_time_from",
        "instance_start_time_to",
        "page_token",
        "locale",
    ):
        value = request.params.get(key)
        if value not in (None, "", [], {}):
            params[key] = value
    return params


def _group_approval_workbench_items(items: tuple[dict[str, Any], ...]) -> dict[str, list[tuple[int, dict[str, Any]]]]:
    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = {"pass": [], "review": [], "hold": []}
    for index, item in enumerate(items, start=1):
        grouped[_approval_workbench_group(item)].append((index, item))
    return grouped


def _approval_workbench_group(item: dict[str, Any]) -> str:
    assessment = item.get("assessment") if isinstance(item.get("assessment"), dict) else {}
    suggestion = str(assessment.get("suggestion") or "")
    if suggestion in {"可通过", "可初步通过"}:
        return "pass"
    if suggestion in {"拒绝", "补充后再审"}:
        return "hold"
    return "review"


def _approval_workbench_line(item: dict[str, Any]) -> str:
    title = str(item.get("title") or "未命名审批")
    applicant = str(item.get("applicant") or "").strip()
    amount = item.get("amount")
    amount_text = f"{amount:g}元" if isinstance(amount, (int, float)) else str(amount or "").strip()
    assessment = item.get("assessment") if isinstance(item.get("assessment"), dict) else {}
    reason = str(assessment.get("reason") or "信息不足，建议展开核对")
    parts = [title]
    if amount_text:
        parts.append(amount_text)
    if applicant:
        parts.append(applicant)
    return f"{'｜'.join(parts)}｜{reason}"


def _approval_detail_answer(item: dict[str, Any]) -> str:
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    if raw:
        attachment_results = item.get("_attachment_results")
        if not isinstance(attachment_results, list):
            attachment_results = approval_formatters.approval_attachment_results(raw)
        return _approval_detail_compact_answer(raw, attachment_results=attachment_results)
    return f"审批详情：{item.get('title') or '未命名审批'}\n可继续回复：通过第一个 / 拒绝第一个，执行前我会再次确认。"


def _approval_assessment(raw: dict[str, Any], *, attachment_results: list[Any]) -> dict[str, Any]:
    snapshot = raw.get("_approval_snapshot") if isinstance(raw.get("_approval_snapshot"), dict) else {}
    if snapshot:
        if snapshot.get("status") == "completed":
            reasons = snapshot.get("reasons") if isinstance(snapshot.get("reasons"), list) else []
            reason = "；".join(str(item) for item in reasons if str(item).strip())
            suggestion = str(snapshot.get("recommendation") or "需关注")
            snapshot_assessment = {
                "suggestion": suggestion,
                "reason": reason or str(snapshot.get("summary") or ""),
                "detailed_reason": reason or str(snapshot.get("summary") or ""),
            }
            risk_level = str(snapshot.get("risk_level") or "review")
            if suggestion == "补充后再审" and risk_level == "high" and not _approval_assessment_has_high_risk_signal(snapshot_assessment):
                risk_level = "review"
            return {
                "suggestion": suggestion,
                "reason": reason or str(snapshot.get("summary") or "已完成审批分析"),
                "detailed_reason": reason or str(snapshot.get("summary") or ""),
                "risk_level": risk_level,
                "source": "snapshot",
            }
        return {
            "suggestion": "分析中",
            "reason": "附件或 AI 分析尚未完成",
            "detailed_reason": "附件或 AI 分析尚未完成，请稍后查看。",
            "risk_level": "pending",
            "source": "snapshot",
        }
    llm_decision = raw.get("_approval_llm_decision") if isinstance(raw.get("_approval_llm_decision"), dict) else {}
    if llm_decision:
        return {
            "suggestion": str(llm_decision.get("conclusion") or "需核对"),
            "reason": str(llm_decision.get("concise_reason") or "已结合表单、附件和历史审批判断"),
            "detailed_reason": str(llm_decision.get("detailed_reason") or ""),
            "source": "llm",
        }
    detail = raw.get("instance_detail") if isinstance(raw.get("instance_detail"), dict) else {}
    fields = dict(approval_form_fields(detail.get("form"), max_fields=50))
    amount = approval_amount(fields)
    attachments = approval_attachment_refs(detail.get("form"))
    reason = first_matching_field(fields, ["报销事由", "申请事由", "付款事由", "借款事由", "事由", "用途"])
    readable = [result for result in attachment_results if getattr(result, "text_preview", None)]
    failed = [result for result in attachment_results if getattr(result, "error", None)]

    risk_points: list[str] = []
    positive_points: list[str] = []
    if amount is None:
        risk_points.append("金额未明确")
    elif amount >= 5000:
        risk_points.append("金额较大")
    elif amount <= 2000:
        positive_points.append("金额较小")

    if not reason:
        risk_points.append("缺少事由")
    else:
        positive_points.append("事由已填写")

    if not attachments:
        risk_points.append("未识别到附件")
    elif attachment_results:
        if readable:
            positive_points.append("附件已读取")
        if failed:
            risk_points.append("部分附件读取失败")
    else:
        positive_points.append("有附件")

    if any(point in risk_points for point in ("金额较大", "金额未明确", "未识别到附件")):
        suggestion = "建议先核对"
    elif risk_points:
        suggestion = "需补充核对"
    else:
        suggestion = "可初步通过"

    reason_parts = positive_points[:2] + risk_points[:2]
    if not reason_parts:
        reason_parts.append("基础信息不足")
    return {
        "suggestion": suggestion,
        "reason": "；".join(reason_parts),
        "risk_points": risk_points,
        "positive_points": positive_points,
        "amount": amount,
        "source": "rule",
    }


def _snapshot_payload(snapshot: Snapshot) -> dict[str, Any]:
    return {
        "id": str(snapshot.id),
        "company_id": str(snapshot.company_id),
        "object_type": snapshot.object_type,
        "object_id": snapshot.object_id,
        "snapshot_type": snapshot.snapshot_type,
        "status": snapshot.status,
        "summary": snapshot.summary,
        "recommendation": snapshot.recommendation,
        "risk_level": snapshot.risk_level,
        "reasons": list(snapshot.reasons or []),
        "source_event_ids": list(snapshot.source_event_ids or []),
        "payload": snapshot.payload if isinstance(snapshot.payload, dict) else {},
    }


def _pending_approval_snapshot_payload(*, company_id: Any, object_id: str) -> dict[str, Any]:
    return {
        "id": "",
        "company_id": str(company_id or ""),
        "object_type": "approval",
        "object_id": object_id,
        "snapshot_type": "approval_current_judgment",
        "status": "pending_analysis",
        "summary": "审批附件仍在分析中。",
        "recommendation": "分析中",
        "risk_level": "pending",
        "reasons": ["附件或 AI 分析尚未完成"],
        "source_event_ids": [],
    }


def _approval_snapshot_risk_level(assessment: dict[str, Any]) -> str:
    suggestion = str(assessment.get("suggestion") or "")
    if suggestion in {"可通过", "可初步通过"}:
        return "pass"
    if suggestion == "拒绝":
        return "high"
    if suggestion == "补充后再审" and _approval_assessment_has_high_risk_signal(assessment):
        return "high"
    return "review"


def _approval_assessment_has_high_risk_signal(assessment: dict[str, Any]) -> bool:
    values: list[str] = []
    for key in ("detailed_reason", "reason"):
        value = str(assessment.get(key) or "").strip()
        if value:
            values.append(value)
    for key in ("risk_points", "reasons"):
        value = assessment.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value if str(item).strip())
    text = "；".join(values)
    if not text:
        return False
    high_terms = (
        "虚假",
        "伪造",
        "无票据支撑",
        "无法核实",
        "无法验证",
        "资金风险",
        "重复报销",
        "超预算",
        "不属于公司业务",
        "明显冲突",
        "重大风险",
        "高风险",
    )
    return any(term in text for term in high_terms)


def _approval_snapshot_reasons(assessment: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    for key in ("detailed_reason", "reason"):
        value = str(assessment.get(key) or "").strip()
        if value:
            reasons.append(value)
    for key in ("risk_points", "positive_points"):
        value = assessment.get(key)
        if isinstance(value, list):
            reasons.extend(str(item) for item in value if str(item).strip())
    return reasons[:8]


def _approval_snapshot_safe_item(raw_item: dict[str, Any]) -> dict[str, Any]:
    return {
        "instance_code": approval_formatters.approval_instance_code(raw_item),
        "approval_name": approval_formatters.readable_approval_name(raw_item),
        "serial_number": raw_item.get("serial_number"),
        "task_id": raw_item.get("task_id"),
        "status": raw_item.get("status") or raw_item.get("task_status"),
    }


def _approval_serial_number_for_display(raw_item: dict[str, Any]) -> str:
    detail = raw_item.get("instance_detail") if isinstance(raw_item.get("instance_detail"), dict) else {}
    instance = raw_item.get("instance") if isinstance(raw_item.get("instance"), dict) else {}
    for source in (raw_item, detail, instance):
        for key in ("serial_number", "serial_no", "approval_serial_number", "code"):
            value = str(source.get(key) or "").strip()
            if value:
                return value
    return ""


def _approval_object_id_candidates(raw_item: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    for key in ("instance_code", "process_code", "approval_instance_id", "process_external_id", "task_external_id", "serial_number", "task_id", "id"):
        value = str(raw_item.get(key) or "").strip()
        if value and value not in candidates:
            candidates.append(value)
    instance = raw_item.get("instance") if isinstance(raw_item.get("instance"), dict) else {}
    for key in ("code", "instance_code", "process_code", "approval_instance_id"):
        value = str(instance.get(key) or "").strip()
        if value and value not in candidates:
            candidates.append(value)
    return candidates


def _approval_snapshot_builder_item(raw_item: dict[str, Any]) -> dict[str, Any]:
    item = _approval_snapshot_safe_item(raw_item)
    for key in ("process_code", "task_id", "approval_code", "definition_code", "instance_detail"):
        value = raw_item.get(key)
        if value:
            item[key] = value
    return item


def _attachment_result_payload(result: Any) -> dict[str, Any]:
    return {
        "name": str(getattr(result, "name", "") or ""),
        "token": str(getattr(result, "token", "") or ""),
        "text_preview": str(getattr(result, "text_preview", "") or "")[:500],
        "error": str(getattr(result, "error", "") or "")[:300],
    }


def _approval_llm_decision(raw: dict[str, Any], *, attachment_results: list[Any]) -> dict[str, str] | None:
    detail = raw.get("instance_detail") if isinstance(raw.get("instance_detail"), dict) else {}
    fields = dict(approval_form_fields(detail.get("form"), max_fields=50))
    approval_name = approval_formatters.readable_approval_name(raw)
    attachments = approval_attachment_refs(detail.get("form"))
    amount = approval_amount(fields)
    rule_conclusion, rule_reason = rule_approval_decision_recommendation(
        approval_name,
        fields,
        amount=amount,
        attachments=attachments,
        attachment_results=attachment_results,
    )
    return generate_approval_llm_advice(
        approval_name=approval_name,
        fields=fields,
        amount=amount,
        attachment_summary=approval_attachment_basis(attachment_results).removeprefix("附件要点：").strip("；"),
        history=raw.get("_approval_history") if isinstance(raw.get("_approval_history"), list) else [],
        rule_conclusion=rule_conclusion,
        rule_reason=rule_reason,
    )


def _approval_detail_compact_answer(
    raw: dict[str, Any],
    *,
    attachment_results: list[Any],
) -> str:
    detail = raw.get("instance_detail") if isinstance(raw.get("instance_detail"), dict) else {}
    fields = dict(approval_form_fields(detail.get("form"), max_fields=50))
    approval_name = approval_formatters.readable_approval_name(raw)
    applicant = _approval_applicant_for_display(raw, fields)
    attachments = approval_attachment_refs(detail.get("form"))
    amount = approval_amount(fields)

    lines = [f"**{approval_name}**"]
    if applicant:
        lines.append(f"报销人：{applicant}")

    key_parts = []
    if amount is not None:
        key_parts.append(f"金额：{amount:g} 元")
    for label, candidates in (
        ("费用承担公司", ("费用承担公司", "承担公司", "付款公司", "公司")),
        ("事由", ("报销事由", "申请事由", "付款事由", "借款事由", "事由", "用途")),
        ("项目", ("项目名称", "项目编码", "项目")),
        ("对象", ("供应商名称", "付款对象", "收款方", "客户名称", "对方单位")),
    ):
        value = first_matching_field(fields, list(candidates))
        if value:
            key_parts.append(f"{label}：{approval_formatters.short_approval_text(value, 48)}")

    if key_parts:
        lines.append("")
        lines.extend(key_parts[:6])

    assessment = _approval_assessment(raw, attachment_results=attachment_results)
    lines.append("")
    lines.append(f"建议：{assessment.get('suggestion') or '需核对'}。")
    reason = str(assessment.get("detailed_reason") or assessment.get("reason") or "").strip()
    if reason:
        lines.append(f"理由：{reason}")
    else:
        lines.extend(_approval_decision_summary(fields, amount=amount, attachments=attachments, attachment_results=attachment_results))

    lines.append("")
    lines.append("可回复：通过 / 拒绝。执行前仍会二次确认。")
    return "\n".join(lines)


def _approval_applicant_for_display(raw: dict[str, Any], fields: dict[str, str]) -> str:
    field_value = first_matching_field(
        fields,
        ["报销人", "申请人", "费用归属人", "归属人", "借款人", "付款申请人", "提交人"],
    )
    if field_value and not _looks_like_internal_identifier(field_value):
        return approval_formatters.short_approval_text(field_value, 32)
    applicant = approval_formatters.approval_applicant_name(raw)
    if applicant and not _looks_like_internal_identifier(applicant):
        return approval_formatters.short_approval_text(applicant, 32)
    for key in ("applicant_name", "starter_name", "user_name", "initiator_name", "owner_name"):
        value = str(raw.get(key) or "").strip()
        if value and not _looks_like_internal_identifier(value):
            return approval_formatters.short_approval_text(value, 32)
    return ""


def _approval_decision_summary(
    fields: dict[str, str],
    *,
    amount: float | None,
    attachments: list[dict[str, str]],
    attachment_results: list[Any],
) -> list[str]:
    readable = [result for result in attachment_results if getattr(result, "text_preview", None)]
    failed = [result for result in attachment_results if getattr(result, "error", None)]
    amount_text = f"{amount:g} 元" if amount is not None else "未识别到明确金额"
    reason = first_matching_field(fields, ["报销事由", "申请事由", "付款事由", "借款事由", "事由", "用途"])

    risk_parts = []
    if not attachments:
        risk_parts.append("未识别到附件")
    elif not readable:
        risk_parts.append("附件未能读取出有效摘要")
    if failed:
        risk_parts.append(f"{len(failed)} 个附件读取失败")
    if amount is None:
        risk_parts.append("金额未明确")

    if risk_parts:
        conclusion = "建议先核对后再处理"
    else:
        conclusion = "可初步通过"

    lines = [f"建议：{conclusion}。"]
    reason_parts = [f"表单金额 {amount_text}"]
    if reason:
        reason_parts.append(f"事由为「{approval_formatters.short_approval_text(reason, 40)}」")
    if attachments and readable:
        reason_parts.append("附件已读取，但自动识别只作为辅助依据")
    elif attachments:
        reason_parts.append("有附件但未读出有效摘要")
    else:
        reason_parts.append("未见附件凭证")
    lines.append("理由：" + "；".join(reason_parts) + "。")
    if risk_parts:
        lines.append("关注点：" + "；".join(risk_parts) + "。")
    return lines


def _invoice_evidence_text(results: list[Any]) -> str:
    for result in results[:3]:
        preview = _clean_attachment_preview(getattr(result, "text_preview", ""))
        if preview and "自动识别文本质量较差" not in preview:
            return approval_formatters.short_approval_text(preview, 88) + "。"
    return ""


def _clean_attachment_preview(text: str) -> str:
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return ""
    if _looks_like_ocr_noise(cleaned):
        return "已读取，但自动识别文本质量较差，建议打开原附件核对发票/凭证。"
    return approval_formatters.short_approval_text(cleaned, 120)


def _looks_like_ocr_noise(text: str) -> bool:
    if len(text) < 40:
        return False
    chinese = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    letters = sum(1 for char in text if char.isalpha())
    digits = sum(1 for char in text if char.isdigit())
    useful = chinese + letters + digits
    return chinese < 6 and letters > chinese * 3 and useful > 30


def _looks_like_internal_identifier(text: str) -> bool:
    value = str(text or "").strip()
    compact = value.replace("-", "").replace("_", "")
    if len(compact) >= 8 and all(char.isalnum() for char in compact) and sum(char.isdigit() for char in compact) >= 3:
        return True
    return value.startswith(("ou_", "oc_", "od-", "cli_", "approval_"))


def _field_presence_stats(items: tuple[dict[str, Any], ...]) -> dict[str, int]:
    gender_stats = _gender_stats(items)
    return {
        "total": len(items),
        "email": sum(1 for item in items if str(item.get("email") or "").strip()),
        "mobile": sum(1 for item in items if str(item.get("mobile") or "").strip()),
        "title": sum(1 for item in items if str(item.get("title") or "").strip()),
        "leader": sum(1 for item in items if str(item.get("leader") or "").strip()),
        "gender": gender_stats["known"],
        "male": gender_stats["male"],
        "female": gender_stats["female"],
        "gender_other": gender_stats["other"],
    }


def _department_member_stats(departments: list[Any]) -> dict[str, int | str]:
    department_items = [item for item in departments if isinstance(item, dict)]
    top_level = [
        item
        for item in department_items
        if str(item.get("parent_department_id") or "").strip() in {"", "0"}
    ]
    primary_counts = [_positive_int(item.get("primary_member_count")) for item in top_level]
    member_counts = [_positive_int(item.get("member_count")) for item in top_level]
    primary_total = sum(primary_counts)
    member_total = sum(member_counts)
    if primary_total > 0:
        return {
            "reported_member_count": primary_total,
            "reported_member_count_basis": "top_level_primary_member_count",
        }
    if member_total > 0:
        return {
            "reported_member_count": member_total,
            "reported_member_count_basis": "top_level_member_count",
        }
    all_member_counts = [_positive_int(item.get("member_count")) for item in department_items]
    max_member_count = max(all_member_counts, default=0)
    return {
        "reported_member_count": max_member_count,
        "reported_member_count_basis": "max_department_member_count" if max_member_count else "",
    }


def _positive_int(value: Any) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(number, 0)


def _organization_snapshot_answer(
    department_count: int,
    items: tuple[dict[str, Any], ...],
    field_stats: dict[str, int],
) -> str:
    visible_total = field_stats.get("total", len(items))
    reported_total = int(field_stats.get("reported_member_count") or 0)
    if reported_total and reported_total != visible_total:
        lines = [f"已读取组织架构：{department_count} 个部门，部门统计口径约 {reported_total} 人，当前可展开明细 {visible_total} 人。"]
    else:
        lines = [f"已读取组织架构：{department_count} 个部门，{visible_total} 人。"]
    if visible_total:
        lines.append(
            "字段完整度："
            f"邮箱 {field_stats.get('email', 0)}/{visible_total}，"
            f"手机号 {field_stats.get('mobile', 0)}/{visible_total}，"
            f"职位 {field_stats.get('title', 0)}/{visible_total}，"
            f"直属上级 {field_stats.get('leader', 0)}/{visible_total}。"
        )
    return "\n".join(lines)


def _people_aggregate_answer(
    department_count: int,
    items: tuple[dict[str, Any], ...],
    field_stats: dict[str, int],
    *,
    question: str = "",
    query_mode: str = "",
    domain_query: dict[str, Any] | None = None,
) -> str:
    visible_total = field_stats.get("total", len(items))
    reported_total = int(field_stats.get("reported_member_count") or 0)
    total = reported_total or visible_total
    mode = query_mode or _people_query_mode_from_text(question)
    filters = domain_query.get("filters") if isinstance(domain_query, dict) and isinstance(domain_query.get("filters"), dict) else {}
    if filters.get("field_present") == "mobile":
        filtered_items = tuple(item for item in items if str(item.get("mobile") or "").strip())
        lines = [f"当前可读通讯录里有手机号字段的人员有 {len(filtered_items)} 位。"]
        if mode in {"list", "gender_list", "title_list"}:
            lines.extend(_people_name_lines(filtered_items))
            if len(filtered_items) > 20:
                lines.append("明细较多，后续可以在侧边栏 Webview 展开查看。")
        return "\n".join(lines)
    gender_filter = gender_filter_from_text(question)
    if gender_filter:
        filtered_items = _people_items_by_gender(items, gender_filter)
        unknown_count = sum(1 for item in items if not _reliable_people_gender(item))
        gender_label = "男性" if gender_filter == "male" else "女性"
        lines = [f"公司通讯录里明确标注为{gender_label}的员工有 {len(filtered_items)} 位。"]
        if unknown_count:
            lines.append(f"另有 {unknown_count} 位没有可靠性别字段，我不按姓名推断。")
        if mode == "gender_list":
            lines.extend(_people_name_lines(filtered_items))
            if len(filtered_items) > 20:
                lines.append("明细较多，后续可以在侧边栏 Webview 展开查看。")
        return "\n".join(lines)
    title_filter = _people_title_filter_from_text(question)
    if title_filter:
        filtered_items = filter_people_by_title(items, title_filter)
        lines = [f"公司里岗位/职位包含「{title_filter}」的同事有 {len(filtered_items)} 人。"]
        if mode == "title_list":
            lines.extend(_people_name_lines(filtered_items))
            if len(filtered_items) > 20:
                lines.append("明细较多，后续可以在侧边栏 Webview 展开查看。")
        return "\n".join(lines)
    if mode == "count_only":
        return f"{total}人。"
    if mode == "count":
        return f"公司当前可读通讯录里是 {total} 人。"
    if reported_total and reported_total != visible_total:
        lines = [f"公司通讯录部门统计口径约 {reported_total} 人，我当前能展开到 {visible_total} 位人员明细，覆盖 {department_count} 个部门。"]
    else:
        lines = [f"公司通讯录里现在有 {total} 人，覆盖 {department_count} 个部门。"]
    return "\n".join(lines)


def _people_query_mode_from_text(text: str) -> str:
    compact = re.sub(r"\s+", "", str(text or "").lower()).replace("多少个", "多少")
    wants_list = any(token in compact for token in ("是谁", "谁是", "分别是谁", "都有谁", "名单", "列出", "全部显示", "有哪些"))
    if any(token in compact for token in ("有谁的号码", "谁的号码", "有谁的电话", "谁的电话")):
        return "list"
    if "通讯录" in compact and any(token in compact for token in ("发我", "发下", "发我下", "给我", "给我下", "发一下")):
        return "list"
    if "数量" in compact and any(token in compact for token in ("只", "只需", "只要", "告诉我", "回答")):
        return "count_only"
    if any(token in compact for token in ("只需要回答", "只回答", "不用告诉", "不要告诉", "不用给我详情", "不要给我详情", "不用详情", "不要详情", "不用明细", "不要明细", "不用列", "不要列", "没必要告诉", "直接回答")):
        return "count_only"
    if any(token in compact for token in ("男生", "男性", "男的", "男员工", "女生", "女性", "女的", "女员工")):
        return "gender_list" if wants_list else "gender_count"
    if any(token in compact for token in ("岗位", "职位", "董事长", "负责人", "工程师", "经理", "主管", "总监", "销售", "财务", "测试", "运营", "人事", "研发")):
        return "title_list" if wants_list else "title_count"
    if wants_list:
        return "list"
    return "count"


def _people_field_projection_for_query_mode(query_mode: str) -> str:
    if query_mode in {"numeric_only", "count", "count_only", "gender_count", "title_count"}:
        return "count_only"
    if query_mode in {"sidepanel", "list", "gender_list", "title_list"}:
        return "name_only"
    return ""


def _apply_people_domain_filters(
    items: tuple[dict[str, Any], ...],
    *,
    question: str,
    domain_query: dict[str, Any] | None,
) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
    filters = domain_query.get("filters") if isinstance(domain_query, dict) and isinstance(domain_query.get("filters"), dict) else {}
    filtered = items
    metadata: dict[str, Any] = {}
    gender = str(
        filters.get("gender")
        or (filters.get("value") if filters.get("filter") == "gender" else "")
        or gender_filter_from_text(question)
        or ""
    ).strip()
    if gender:
        normalized_gender = normalize_gender(gender)
        filtered = _people_items_by_gender(filtered, normalized_gender)
        metadata["people_filter"] = {"filter": "gender", "value": normalized_gender}
        metadata["unknown_gender_count"] = sum(1 for item in items if not _reliable_people_gender(item))
    field_present = str(filters.get("field_present") or (filters.get("value") if filters.get("filter") == "field_present" else "") or "").strip()
    if field_present:
        filtered = tuple(item for item in filtered if _people_item_field_value(item, field_present))
        metadata["people_filter"] = {"filter": "field_present", "value": field_present}
    name_prefix = str(filters.get("name_prefix") or (filters.get("value") if filters.get("filter") == "name_prefix" else "") or "").strip()
    if name_prefix:
        filtered = tuple(item for item in filtered if str(item.get("name") or "").startswith(name_prefix))
        metadata["people_filter"] = {"filter": "name_prefix", "value": name_prefix}
    if filtered is not items:
        metadata["filtered_user_count"] = len(filtered)
    return filtered, metadata


def _people_gender_filter(text: str) -> str:
    return gender_filter_from_text(text)


def _people_title_filter_from_text(text: str) -> str:
    compact = re.sub(r"[\s，,。.!！；;：:]+", "", str(text or ""))
    if not any(token in compact for token in ("岗位", "职位", "董事长", "负责人", "工程师", "经理", "主管", "总监", "销售", "财务", "测试", "运营", "人事", "研发")):
        return ""
    keyword = compact
    for token in ("公司", "全公司", "共有", "有多少个", "有多少位", "有多少", "多少个", "多少位", "多少", "几个", "哪些是", "是谁", "谁是", "有哪些", "都有谁", "分别是谁", "分别", "人员", "员工", "岗位", "职位", "的", "？", "?"):
        keyword = keyword.replace(token, "")
    return keyword.strip()


def _asks_people_list(text: str) -> bool:
    return asks_people_list(text)


def _people_items_by_gender(items: tuple[dict[str, Any], ...], gender: str) -> tuple[dict[str, Any], ...]:
    normalized = normalize_gender(gender)
    if not normalized:
        return ()
    return tuple(item for item in items if _reliable_people_gender(item) == normalized)


def _people_name_lines(items: tuple[dict[str, Any], ...], *, limit: int = 50) -> list[str]:
    return [f"{index}. {_format_people_brief(item)}" for index, item in enumerate(items[:limit], start=1)]


def _format_people_brief(item: dict[str, Any]) -> str:
    return format_people_brief(item)


def _has_any_text(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _gender_stats(items: tuple[dict[str, Any], ...]) -> dict[str, int]:
    stats = {"known": 0, "male": 0, "female": 0, "other": 0}
    for item in items:
        value = _normalized_gender(item.get("gender"))
        if not value:
            continue
        stats["known"] += 1
        if value == "male":
            stats["male"] += 1
        elif value == "female":
            stats["female"] += 1
        else:
            stats["other"] += 1
    return stats


def _normalized_gender(value: Any) -> str:
    return normalize_gender(value)


def _organization_base_fields() -> list[dict[str, str]]:
    return [{"name": name, "type": "text"} for name in _organization_export_field_names()]


def _organization_export_field_names() -> list[str]:
    return ["部门", "姓名", "职位", "邮箱", "手机号", "直属上级", "部门ID", "open_id"]


def _base_export_answer(*, row_count: int, app_token: str, table_id: str, url: str = "") -> str:
    lines = [f"已创建飞书多维表格文件「最新组织架构」，并写入 {row_count} 行。"]
    lines.append("导出字段：" + "、".join(_organization_export_field_names()) + "。")
    if url:
        lines.append(f"打开链接：{url}")
    else:
        lines.append("文件已创建到飞书云空间，可在最近文件/多维表格中打开。")
    return "\n".join(lines)


def _organization_rows_from_previous_results(value: Any) -> list[list[str]]:
    if not isinstance(value, tuple):
        return []
    for result in value:
        if not isinstance(result, ProviderResult) or result.source != "people":
            continue
        rows = []
        for item in result.items:
            rows.append(
                [
                    str(item.get("department") or ""),
                    str(item.get("name") or ""),
                    str(item.get("title") or ""),
                    str(item.get("email") or ""),
                    str(item.get("mobile") or ""),
                    str(item.get("leader") or ""),
                    str(item.get("department_ids") or ""),
                    str(item.get("open_id") or ""),
                ]
            )
        if rows:
            return rows
    return []


def _people_tool_params(request: ProviderRequest) -> dict[str, Any]:
    blocked = {"previous_results"}
    params = {key: value for key, value in request.params.items() if key not in blocked}
    params.setdefault("response_format", "raw_json")
    params.setdefault("as", "bot")
    return params


def _task_tool_params(request: ProviderRequest) -> dict[str, Any]:
    blocked = {"previous_results"}
    params = {key: value for key, value in request.params.items() if key not in blocked}
    params.setdefault("response_format", "raw_json")
    return params


def _task_query_tool_params(request: ProviderRequest) -> dict[str, Any]:
    params = _task_tool_params(request)
    params.update(_enterprise_query_scope_params(request))
    return params


def _calendar_tool_params(request: ProviderRequest) -> dict[str, Any]:
    blocked = {"previous_results"}
    params = {key: value for key, value in request.params.items() if key not in blocked}
    params.setdefault("calendar_id", "primary")
    return params


def _calendar_query_tool_params(request: ProviderRequest) -> dict[str, Any]:
    params = _calendar_tool_params(request)
    params.update(_enterprise_query_scope_params(request))
    return params


def _calendar_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return datetime.fromtimestamp(int(text), tz=UTC)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _enterprise_query_scope_params(request: ProviderRequest) -> dict[str, Any]:
    identity = request.context.identity
    runtime_scope = request.context.runtime_scope
    company_id = str(runtime_scope.active_company_id or "")
    scope = request.intent.data_scope or "self"
    scope_filter: dict[str, Any] = {
        "scope": scope,
        "company_id": company_id,
        "actor_open_id": identity.open_id,
        "actor_user_id": identity.user_id,
        "actor_role": identity.role,
    }
    if identity.department_id or runtime_scope.active_department_id:
        scope_filter["department_id"] = identity.department_id or runtime_scope.active_department_id
    if runtime_scope.active_project_id:
        scope_filter["project_id"] = runtime_scope.active_project_id
    if request.intent.entities:
        scope_filter["entities"] = dict(request.intent.entities)

    params: dict[str, Any] = {"scope_filter": scope_filter}
    if scope == "self":
        if identity.open_id:
            params.setdefault("owner_open_id", identity.open_id)
            params.setdefault("assignee_open_id", identity.open_id)
            params.setdefault("actor_open_id", identity.open_id)
        if identity.user_id:
            params.setdefault("owner_user_id", identity.user_id)
            params.setdefault("actor_user_id", identity.user_id)
    return params


def _calendar_item(item: dict[str, Any]) -> dict[str, Any]:
    event = item.get("event") if isinstance(item.get("event"), dict) else item
    return {
        "title": event.get("summary") or event.get("title") or event.get("subject") or event.get("event_id") or "",
        "event_id": event.get("event_id") or event.get("id") or "",
        "start": event.get("start") or event.get("start_time") or "",
        "end": event.get("end") or event.get("end_time") or "",
        "url": event.get("url") or event.get("app_link") or "",
        "raw": event,
    }


def _calendar_list_answer(items: tuple[dict[str, Any], ...]) -> str:
    if not items:
        return "暂未查询到日程。"
    lines = [f"查询到 {len(items)} 条日程："]
    for index, item in enumerate(items[:20], start=1):
        title = str(item.get("title") or "未命名日程")
        time_text = _calendar_time_range_text(item.get("start"), item.get("end"))
        lines.append(f"{index}. {title}{time_text}")
    return "\n".join(lines)


def _calendar_time_range_text(start: Any, end: Any) -> str:
    start_text, start_date = _calendar_time_text(start)
    end_text, end_date = _calendar_time_text(end)
    if not start_text and not end_text:
        return ""
    if start_text and end_text:
        if start_date and start_date == end_date and " " in end_text:
            end_text = end_text.split(" ", 1)[1]
        return f"（{start_text} - {end_text}）"
    return f"（{start_text or end_text}）"


def _calendar_time_text(value: Any) -> tuple[str, str]:
    if value in (None, ""):
        return "", ""
    if isinstance(value, dict):
        if value.get("date"):
            text = str(value.get("date") or "").strip()
            return text, text
        timestamp = value.get("timestamp")
        if timestamp not in (None, ""):
            return _calendar_timestamp_text(timestamp, timezone_name=str(value.get("timezone") or "Asia/Shanghai"))
        for key in ("date_time", "datetime", "time"):
            if value.get(key):
                return _calendar_time_text(value.get(key))
        return "", ""
    if isinstance(value, datetime):
        parsed = value if value.tzinfo else value.replace(tzinfo=UTC)
        local = parsed.astimezone(ZoneInfo("Asia/Shanghai"))
        return local.strftime("%Y-%m-%d %H:%M"), local.strftime("%Y-%m-%d")
    text = str(value).strip()
    if not text:
        return "", ""
    if text.isdigit():
        return _calendar_timestamp_text(text, timezone_name="Asia/Shanghai")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text, text[:10] if len(text) >= 10 else ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    local = parsed.astimezone(ZoneInfo("Asia/Shanghai"))
    return local.strftime("%Y-%m-%d %H:%M"), local.strftime("%Y-%m-%d")


def _calendar_timestamp_text(value: Any, *, timezone_name: str) -> tuple[str, str]:
    try:
        timestamp = float(str(value).strip())
    except (TypeError, ValueError):
        return "", ""
    if timestamp > 10_000_000_000:
        timestamp = timestamp / 1000
    try:
        tz = ZoneInfo(timezone_name or "Asia/Shanghai")
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("Asia/Shanghai")
    parsed = datetime.fromtimestamp(timestamp, tz)
    return parsed.strftime("%Y-%m-%d %H:%M"), parsed.strftime("%Y-%m-%d")


def _im_tool_params(request: ProviderRequest) -> dict[str, Any]:
    blocked = {"previous_results"}
    params = {key: value for key, value in request.params.items() if key not in blocked}
    return params


def _im_chat_item(item: dict[str, Any]) -> dict[str, Any]:
    chat = item.get("chat") if isinstance(item.get("chat"), dict) else item
    return {
        "name": chat.get("name") or chat.get("chat_name") or chat.get("title") or chat.get("chat_id") or "",
        "chat_id": chat.get("chat_id") or chat.get("open_chat_id") or chat.get("id") or "",
        "description": chat.get("description") or "",
        "member_count": chat.get("member_count") or chat.get("members_count") or "",
        "raw": chat,
    }


def _im_message_item(item: dict[str, Any]) -> dict[str, Any]:
    message = item.get("message") if isinstance(item.get("message"), dict) else item
    return {
        "message_id": message.get("message_id") or message.get("id") or "",
        "sender": message.get("sender_name") or message.get("sender") or message.get("open_id") or "",
        "text": message.get("text") or message.get("content_text") or message.get("content") or "",
        "create_time": message.get("create_time") or message.get("created_at") or "",
        "raw": message,
    }


def _im_chat_list_answer(items: tuple[dict[str, Any], ...], *, query: str = "") -> str:
    if not items:
        if not query:
            return "当前没有查询到可见群聊。"
        return f"没有找到与「{query}」匹配的群聊。"
    lines = [f"当前可见群聊 {len(items)} 个："] if not query else [f"找到 {len(items)} 个与「{query}」相关的群聊："]
    for index, item in enumerate(items[:10], start=1):
        name = str(item.get("name") or "未命名群聊")
        member_count = str(item.get("member_count") or "").strip()
        suffix = f"（{member_count} 人）" if member_count else ""
        lines.append(f"{index}. {name}{suffix}")
    return "\n".join(lines)


def _im_message_list_answer(items: tuple[dict[str, Any], ...]) -> str:
    if not items:
        return "暂未查询到群消息。"
    lines = [f"查询到 {len(items)} 条消息："]
    for index, item in enumerate(items[:20], start=1):
        sender = str(item.get("sender") or "").strip()
        text = str(item.get("text") or "").strip()
        prefix = f"{sender}: " if sender else ""
        lines.append(f"{index}. {prefix}{text[:80] or item.get('message_id') or '消息'}")
    return "\n".join(lines)


def _im_target_candidates_answer(kind: str, query: str, items: tuple[dict[str, Any], ...]) -> str:
    if not items:
        return f"没有找到匹配「{query}」的{kind}，我没有发送消息。"
    lines = [f"找到多个匹配「{query}」的{kind}，为避免误发，请补充更明确的名称："]
    for index, item in enumerate(items[:10], start=1):
        name = str(item.get("name") or item.get("title") or "未命名")
        detail = str(item.get("department") or item.get("description") or "").strip()
        lines.append(f"{index}. {name}" + (f"（{detail}）" if detail else ""))
    return "\n".join(lines)


def _im_target_label(params: dict[str, Any]) -> str:
    if params.get("target_name"):
        return str(params.get("target_name"))
    if params.get("chat_id"):
        return "当前会话"
    if params.get("user_id"):
        return "指定用户"
    return "未知对象"


def _normalized_im_delivery_mode(value: Any) -> str:
    text = str(value or "").strip()
    compact = "".join(text.split()).lower()
    if compact in {"bot_multi_notify", "user_multi_private", "create_group_then_send"}:
        return compact
    if any(token in compact for token in ("拉群", "建群", "建个群", "创建群", "群里发", "发到群")):
        return "create_group_then_send"
    if any(token in compact for token in ("机器人通知", "用机器人", "机器人发", "系统通知", "自动通知", "大飞哥通知")):
        return "bot_multi_notify"
    if any(token in compact for token in ("替我发", "用我", "以我的名义", "我发给", "分别发", "私聊发")):
        return "user_multi_private"
    return text


def _people_context_group_chat_name(request: ProviderRequest, items: tuple[dict[str, Any], ...]) -> str:
    explicit = str(request.params.get("chat_name") or request.params.get("group_name") or "").strip()
    if explicit:
        return explicit[:60]
    names = [str(item.get("name") or item.get("display_name") or "").strip() for item in items]
    names = [name for name in names if name]
    if names:
        suffix = "、".join(names[:3])
        if len(names) > 3:
            suffix = f"{suffix}等{len(names)}人"
        return f"临时沟通群-{suffix}"[:60]
    return f"临时沟通群-{len(items)}人"


def _chat_id_from_tool_result(result) -> str:
    payload = _tool_payload(result)
    found = _first_nested_value(payload, ("chat_id", "open_chat_id", "chat_id_v2"))
    if found:
        return found
    answer = str(getattr(result, "answer", "") or "")
    match = re.search(r"\b(oc[_A-Za-z0-9-]+)\b", answer)
    return match.group(1) if match else ""


def _mail_tool_params(request: ProviderRequest) -> dict[str, Any]:
    blocked = {"previous_results"}
    params = {key: value for key, value in request.params.items() if key not in blocked}
    params.setdefault("response_format", "raw_json")
    params.setdefault("mailbox", "me")
    return params


def _mail_item(item: dict[str, Any]) -> dict[str, Any]:
    if isinstance(item.get("meta_data"), dict):
        metadata = item["meta_data"]
        message = {
            **metadata,
            "message_id": item.get("id") or metadata.get("message_biz_id") or metadata.get("message_id"),
            "display_info": item.get("display_info") or "",
        }
    else:
        message = item.get("message") if isinstance(item.get("message"), dict) else item
    sender = message.get("from") or message.get("sender") or message.get("head_from") or message.get("from_address") or message.get("sender_email") or {}
    sender_text = ""
    if isinstance(sender, dict):
        sender_text = str(sender.get("email") or sender.get("address") or sender.get("mail_address") or sender.get("name") or "")
    elif sender:
        sender_text = str(sender)
    subject = _first_nested_value(message, ("subject", "title", "mail_subject"))
    received_at = _first_nested_value(message, ("received_at", "date", "created_time", "create_time", "internal_date", "sent_at"))
    return {
        "subject": subject or "无主题",
        "from": sender_text,
        "received_at": received_at,
        "thread_id": _first_nested_value(message, ("thread_id", "threadID")),
        "message_id": _first_nested_value(message, ("message_id", "id")),
        "url": _first_nested_value(message, ("url", "app_link", "link")),
        "raw": message,
    }


def _mail_list_answer(items: tuple[dict[str, Any], ...], *, query: str = "") -> str:
    if not items:
        return f"没有找到与「{query}」匹配的邮件。" if query else "暂未查询到邮件。"
    header = f"找到 {len(items)} 封与「{query}」相关的邮件：" if query else f"查询到 {len(items)} 封最近邮件："
    lines = [header]
    for index, item in enumerate(items[:20], start=1):
        subject = str(item.get("subject") or "无主题")
        sender = str(item.get("from") or "").strip()
        received_at = str(item.get("received_at") or "").strip()
        suffix = "，".join(part for part in (sender, received_at) if part)
        lines.append(f"{index}. {subject}" + (f"（{suffix}）" if suffix else ""))
    return "\n".join(lines)


def _mail_draft_answer(*, subject: str, url: str = "") -> str:
    lines = [f"已创建邮件草稿：{subject}。"]
    if url:
        lines.append(f"打开链接：{url}")
    else:
        lines.append("草稿已创建，可在飞书邮箱草稿箱中打开。")
    lines.append("我没有发送这封邮件。")
    return "\n".join(lines)


def _first_nested_value(payload: Any, keys: tuple[str, ...]) -> str:
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if value:
                return str(value)
        for value in payload.values():
            found = _first_nested_value(value, keys)
            if found:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = _first_nested_value(item, keys)
            if found:
                return found
    return ""


def _message_text_from_previous_results(value: Any) -> str:
    if not isinstance(value, tuple):
        return ""
    for result in reversed(value):
        if result.source == "base" and result.metadata.get("table_id"):
            lines = ["已完成飞书多维表格文件「最新组织架构」。"]
            if result.metadata.get("url"):
                lines.append(f"打开链接：{result.metadata.get('url')}")
            else:
                lines.append("文件已创建到飞书云空间，可在最近文件/多维表格中打开。")
            return "\n".join(lines)
    return ""
