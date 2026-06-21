from __future__ import annotations

import asyncio
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from time import perf_counter
from types import SimpleNamespace
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.entities import Company, FeishuAppConfig, MemoryFact, WorkEvent
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
from app.services.feishu.drive import FeishuDriveService
from app.services.feishu.meeting import FeishuMeetingService
from app.services.feishu.okr import FeishuOkrService
from app.services.llm.approval_advisor import generate_approval_llm_advice
from app.services.runtime_v5.context import load_people_snapshot, save_people_snapshot
from app.services.runtime_v5.models import ProviderRequest, ProviderResult, RuntimeContext
from app.services.tools.base import ToolContext, ToolExecutionStatus, ToolRequest
from app.services.tools.providers.feishu_api import feishu_write_confirmation_token
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
        "search_person": ("feishu_contact_user_search", False),
        "get_person": ("feishu_contact_user_get", False),
        "department_children": ("feishu_contact_department_children", False),
        "department_users": ("feishu_contact_department_users", False),
        "scope_list": ("feishu_contact_scope_list", False),
        "get_org_snapshot": ("feishu_contact_organization_snapshot", False),
        "list_department_members": ("feishu_contact_organization_snapshot", False),
    }

    def execute(self, request: ProviderRequest) -> ProviderResult:
        if request.operation == "search_person":
            keyword = str(request.params.get("keyword") or request.intent.canonical_question or "").strip()
            cached_items = _people_items_from_snapshot(load_people_snapshot(request.context.runtime_scope.active_company_id), keyword)
            if cached_items:
                return ProviderResult(
                    source="people",
                    status="success",
                    result_type="people_search",
                    count=len(cached_items),
                    items=cached_items,
                    metadata={"keyword": keyword, "cache_hit": True},
                    answer=_people_search_answer(keyword, cached_items),
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
            return ProviderResult(
                source="people",
                status=_provider_status(result),
                result_type="people_search",
                count=len(items),
                items=items,
                metadata={"keyword": keyword, "raw": payload},
                answer=_people_search_answer(keyword, items),
                error=result.error or str(payload.get("error") or ""),
            )

        if request.operation == "list_department_members":
            keyword = str(request.params.get("keyword") or request.intent.canonical_question or "").strip()
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
                metadata={"keyword": keyword, "raw": payload},
                answer=_department_members_answer(keyword, items),
                error=result.error or "",
            )

        if request.operation == "get_org_snapshot":
            result, payload = self._organization_snapshot_payload(request)
            departments = payload.get("departments") if isinstance(payload.get("departments"), list) else []
            users = payload.get("users") if isinstance(payload.get("users"), list) else []
            items = tuple(_user_item(user) for user in users if isinstance(user, dict))
            field_stats = _field_presence_stats(items)
            if _provider_status(result) == "success" and not departments and not items:
                return ProviderResult(
                    source="people",
                    status="error",
                    result_type="organization_snapshot",
                    metadata={"department_count": 0, "raw": payload},
                    answer="没有读取到组织架构数据，已停止后续建表写入。",
                    error="empty_organization_snapshot",
                )
            return ProviderResult(
                source="people",
                status=_provider_status(result),
                result_type="organization_snapshot",
                count=len(items),
                items=items,
                metadata={
                    "department_count": len(departments),
                    "field_stats": field_stats,
                    "fetch_ms": payload.get("_runtime_v5_fetch_ms", 0),
                    "cache_hit": bool(payload.get("_runtime_v5_cached", False)),
                    "raw": payload,
                },
                answer=_organization_snapshot_answer(len(departments), items, field_stats),
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
        if cached:
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
        if _provider_status(result) == "success" and payload:
            save_people_snapshot(company_id, payload)
        return result, payload


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

        step_started = perf_counter()
        attachment_results = self._read_approval_attachments(request, raw_item)
        metadata["substeps"].append({"step": "approval_attachments", "duration_ms": int((perf_counter() - step_started) * 1000), "status": "success" if attachment_results else "empty", "count": len(attachment_results)})
        if attachment_results:
            raw_item["_attachment_results"] = attachment_results
            enriched_item["_attachment_results"] = attachment_results
            metadata["attachment_read_count"] = len(attachment_results)

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
        detail_loaded = 0
        for raw_item in raw_items[:10]:
            if not isinstance(raw_item, dict):
                continue
            detail = self._fetch_approval_instance_detail(request, raw_item)
            if detail:
                detail_loaded += 1
                raw_item["instance_detail"] = detail
                raw_item.pop("detail_error", None)
                raw_item.setdefault("instance_code", detail.get("instance_code") or detail.get("process_code"))
                raw_item.setdefault("serial_number", detail.get("serial_number"))
                detail_name = str(detail.get("approval_name") or detail.get("definition_name") or "").strip()
                if detail_name:
                    raw_item.setdefault("approval_name", detail_name)
        substeps.append({"step": "approval_detail_batch", "duration_ms": int((perf_counter() - step_started) * 1000), "status": "success" if detail_loaded else "empty", "count": detail_loaded})
        if app_config is not None:
            step_started = perf_counter()
            approval_resources.attach_approval_history_context(self.db, app_config, raw_items[:10])
            substeps.append({"step": "approval_history", "duration_ms": int((perf_counter() - step_started) * 1000), "status": "success"})
        step_started = perf_counter()
        attachment_read_count = 0
        for raw_item in raw_items[:10]:
            if not isinstance(raw_item, dict):
                continue
            attachment_results = self._read_approval_attachments(request, raw_item)
            if attachment_results:
                attachment_read_count += len(attachment_results)
                raw_item["_attachment_results"] = attachment_results
            llm_started = perf_counter()
            llm_decision = _approval_llm_decision(raw_item, attachment_results=attachment_results)
            raw_item["_approval_llm_ms"] = int((perf_counter() - llm_started) * 1000)
            if llm_decision:
                raw_item["_approval_llm_decision"] = llm_decision
            raw_item["_approval_assessment"] = _approval_assessment(raw_item, attachment_results=attachment_results)
        substeps.append({"step": "approval_attachments", "duration_ms": int((perf_counter() - step_started) * 1000), "status": "success" if attachment_read_count else "empty", "count": attachment_read_count})
        llm_total_ms = sum(int(item.get("_approval_llm_ms") or 0) for item in raw_items[:10] if isinstance(item, dict))
        substeps.append({"step": "approval_ai_judgement", "duration_ms": llm_total_ms, "status": "success" if llm_total_ms else "empty", "count": len([item for item in raw_items[:10] if isinstance(item, dict) and item.get("_approval_llm_decision")])})
        return substeps

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

    def _im_send_params(self, request: ProviderRequest) -> dict[str, Any] | ProviderResult:
        text = str(request.params.get("text") or "").strip()
        if not text and request.operation == "send_result":
            text = _message_text_from_previous_results(request.params.get("previous_results"))
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
            params = _task_tool_params(request)
            if request.operation == "search_tasks":
                keyword = str(request.params.get("keyword") or "").strip()
                if keyword:
                    params["query"] = keyword
            return self._execute_task_query(
                request,
                tool_name=tool_name,
                operation=request.operation,
                params=params,
            )

        if request.operation != "create_task":
            return self._execute_task_tool(
                request,
                tool_name=tool_name,
                operation=request.operation,
                is_write=is_write,
                params=_task_tool_params(request),
            )

        summary = str(request.params.get("summary") or request.intent.canonical_question or request.context.current_message).strip()
        if not summary:
            return _missing_params_result(
                source="task",
                result_type="task_create",
                operation=request.operation,
                missing=("summary",),
                answer="创建任务还缺少任务标题。",
                error="missing_task_summary",
            )
        result = self._execute_tool(
            request,
            tool_name=tool_name,
            confirm_write=True,
            params={
                "summary": summary,
                "response_format": "raw_json",
            },
        )
        status = _provider_status(result)
        payload = _tool_payload(result)
        task_id = _first_nested_value(payload, ("task_id", "id", "guid"))
        url = _first_nested_value(payload, ("url", "app_link", "link"))
        return ProviderResult(
            source="task",
            status=status,
            result_type="task_create",
            count=1 if status == "success" else 0,
            items=({"title": summary, "task_id": task_id, "url": url},) if status == "success" else (),
            metadata={
                "operation": request.operation,
                "tool_name": tool_name,
                "summary": summary,
                "tool_answer": result.answer,
                **_provider_error_metadata(result),
            },
            answer=f"已创建任务：{summary}" if status == "success" else _tool_failure_answer("任务创建", result),
            error=result.error or "",
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
            result_type=operation,
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
            result = self._execute_tool(
                request,
                tool_name=tool_name,
                params={**_calendar_tool_params(request), "response_format": "raw_json"},
            )
            status = _provider_status(result)
            payload = _tool_payload(result)
            raw_items = _items_from_payload(payload)
            items = tuple(_calendar_item(item) for item in raw_items)
            return ProviderResult(
                source="calendar",
                status=status,
                result_type="calendar_event_list",
                count=len(items),
                items=items,
                metadata={"operation": request.operation, "tool_name": tool_name, **_provider_error_metadata(result)},
                answer=_calendar_list_answer(items),
                error=result.error or "",
            )
        if request.operation == "create_event":
            missing = [key for key in ("summary", "start", "end") if not str(request.params.get(key) or "").strip()]
            if missing:
                return _missing_params_result(
                    source="calendar",
                    result_type="calendar_create",
                    operation=request.operation,
                    missing=missing,
                    answer="创建日程还需要主题、开始时间和结束时间。你可以说：明天下午 3 点到 4 点创建一个会议，主题是项目进度。",
                    error="missing_calendar_time",
                )
            result = self._execute_tool(
                request,
                tool_name=tool_name,
                confirm_write=is_write,
                params={**_calendar_tool_params(request), "response_format": "raw_json"},
            )
            status = _provider_status(result)
            payload = _tool_payload(result)
            summary = str(request.params.get("summary") or "")
            event_id = _first_nested_value(payload, ("event_id", "id", "calendar_event_id"))
            url = _first_nested_value(payload, ("url", "app_link", "link"))
            return ProviderResult(
                source="calendar",
                status=status,
                result_type="calendar_create",
                count=1 if status == "success" else 0,
                items=({"title": summary, "start": request.params.get("start"), "end": request.params.get("end"), "event_id": event_id, "url": url},)
                if status == "success"
                else (),
                metadata={"operation": request.operation, "tool_name": tool_name, **_provider_error_metadata(result)},
                answer=f"已创建日程：{summary}" if status == "success" else _tool_failure_answer("日程创建", result),
                error=result.error or "",
            )
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
        if request.operation == "risk_policy":
            seed_text = f"{seed_text} 风险 预警 异常 制度 流程 规范"
        keywords = _knowledge_keywords(seed_text)
        facts = _knowledge_facts(self.db, company_id=company_id, keywords=keywords, limit=6)
        events = _knowledge_events(self.db, company_id=company_id, keywords=keywords, limit=6)
        items = tuple(
            [
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
    intro = str(
        metadata.get("intro")
        or metadata.get("description")
        or metadata.get("business")
        or metadata.get("summary")
        or ""
    ).strip()
    lines = [f"{name}"]
    if status:
        lines.append(f"状态：{status}")
    if intro:
        lines.append(f"简介：{intro}")
    else:
        lines.append("公司档案里暂时没有维护简介。")
    return "\n".join(lines)


def _workevent_item(event: WorkEvent) -> dict[str, Any]:
    return {
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
        kind = "知识事实" if item.get("kind") == "fact" else "文档事件"
        lines.append(f"{index}. {kind}｜{title}" + (f"｜{summary}" if summary else ""))
    lines.append("说明：这里只使用已同步的公开知识/流程制度，不读取邮件、财务、客户或私密数据。")
    return "\n".join(lines)


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
    if _contains_blocked_knowledge_terms(text):
        return False
    lowered = text.lower()
    return any(term.lower() in lowered for term in _KNOWLEDGE_EVENT_TERMS)


def _contains_blocked_knowledge_terms(text: str) -> bool:
    return any(term in text for term in _BLOCKED_KNOWLEDGE_TERMS)


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
    department_names = user.get("department_names")
    department_ids = user.get("department_ids")
    return {
        "name": user.get("name") or user.get("english_name") or user.get("open_id") or "",
        "department": ", ".join(str(item) for item in department_names if item) if isinstance(department_names, list) else "",
        "department_ids": ", ".join(str(item) for item in department_ids if item) if isinstance(department_ids, list) else "",
        "leader": user.get("leader") or user.get("manager") or user.get("leader_name") or user.get("manager_name") or "",
        "title": user.get("title") or user.get("job_title") or "",
        "email": user.get("email") or "",
        "mobile": user.get("mobile") or "",
        "open_id": user.get("open_id") or "",
    }


def _people_items_from_snapshot(payload: dict[str, Any] | None, keyword: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(payload, dict):
        return ()
    users = payload.get("users") if isinstance(payload.get("users"), list) else []
    normalized = keyword.strip().lower()
    if not normalized:
        return ()
    return tuple(
        item
        for item in (_user_item(user) for user in users if isinstance(user, dict))
        if normalized in str(item.get("name") or "").lower()
        or normalized in str(item.get("email") or "").lower()
        or normalized in str(item.get("mobile") or "").lower()
        or normalized in str(item.get("title") or "").lower()
    )


def _approval_item(item: dict[str, Any]) -> dict[str, Any]:
    detail = item.get("instance_detail") if isinstance(item.get("instance_detail"), dict) else {}
    fields = dict(approval_form_fields(detail.get("form"), max_fields=50))
    amount = approval_amount(fields)
    applicant = _approval_applicant_for_display(item, fields)
    assessment = item.get("_approval_assessment") if isinstance(item.get("_approval_assessment"), dict) else _approval_assessment(item, attachment_results=[])
    title = (
        item.get("title")
        or item.get("approval_name")
        or item.get("name")
        or item.get("summary")
        or item.get("instance_code")
        or item.get("task_id")
        or ""
    )
    return {
        "title": title,
        "applicant": applicant,
        "status": item.get("status") or item.get("task_status") or "",
        "amount": amount if amount is not None else item.get("amount") or item.get("total_amount") or "",
        "id": item.get("task_id") or item.get("instance_code") or item.get("id") or "",
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


def _people_search_answer(keyword: str, items: tuple[dict[str, Any], ...]) -> str:
    if not items:
        return f"没有找到与「{keyword}」匹配的人员。"
    lines = [f"找到 {len(items)} 位与「{keyword}」相关的人员："]
    for item in items[:10]:
        name = str(item.get("name") or "未知")
        title = str(item.get("title") or "").strip()
        department = str(item.get("department") or "").strip()
        email = str(item.get("email") or "").strip()
        mobile = str(item.get("mobile") or "").strip()
        suffix = "，".join(part for part in (title, department, f"邮箱：{email}" if email else "", f"手机：{mobile}" if mobile else "") if part)
        lines.append(f"{name}（{suffix}）" if suffix else name)
    return "\n".join(lines)


def _department_members_answer(keyword: str, items: tuple[dict[str, Any], ...]) -> str:
    if not items:
        return f"没有找到「{keyword}」相关部门成员。"
    lines = [f"「{keyword}」相关成员 {len(items)} 人："]
    for index, item in enumerate(items[:30], start=1):
        name = str(item.get("name") or "未知")
        title = str(item.get("title") or "").strip()
        email = str(item.get("email") or "").strip()
        mobile = str(item.get("mobile") or "").strip()
        suffix = "，".join(part for part in (title, f"邮箱：{email}" if email else "", f"手机：{mobile}" if mobile else "") if part)
        lines.append(f"{index}. {name}" + (f"（{suffix}）" if suffix else ""))
    return "\n".join(lines)


def _department_item_matches(item: dict[str, Any], keyword: str) -> bool:
    normalized = keyword.replace("部门", "").replace("团队", "").replace("中心", "").replace("小组", "").strip()
    if not normalized:
        return True
    haystack = " ".join(
        str(item.get(key) or "")
        for key in ("department", "department_ids", "name", "title")
    )
    return normalized in haystack or keyword in haystack


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
    return {
        "total": len(items),
        "email": sum(1 for item in items if str(item.get("email") or "").strip()),
        "mobile": sum(1 for item in items if str(item.get("mobile") or "").strip()),
        "title": sum(1 for item in items if str(item.get("title") or "").strip()),
        "leader": sum(1 for item in items if str(item.get("leader") or "").strip()),
    }


def _organization_snapshot_answer(
    department_count: int,
    items: tuple[dict[str, Any], ...],
    field_stats: dict[str, int],
) -> str:
    total = field_stats.get("total", len(items))
    lines = [f"已读取组织架构：{department_count} 个部门，{total} 人。"]
    if total:
        lines.append(
            "字段完整度："
            f"邮箱 {field_stats.get('email', 0)}/{total}，"
            f"手机号 {field_stats.get('mobile', 0)}/{total}，"
            f"职位 {field_stats.get('title', 0)}/{total}，"
            f"直属上级 {field_stats.get('leader', 0)}/{total}。"
        )
    return "\n".join(lines)


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


def _calendar_tool_params(request: ProviderRequest) -> dict[str, Any]:
    blocked = {"previous_results"}
    params = {key: value for key, value in request.params.items() if key not in blocked}
    params.setdefault("calendar_id", "primary")
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
        start = str(item.get("start") or "").strip()
        end = str(item.get("end") or "").strip()
        time_text = f"（{start} - {end}）" if start or end else ""
        lines.append(f"{index}. {title}{time_text}")
    return "\n".join(lines)


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
        return f"没有找到与「{query}」匹配的群聊。"
    lines = [f"找到 {len(items)} 个与「{query}」相关的群聊："]
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
