import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import FeishuAppConfig, Resource
from app.services.feishu.client import FeishuClient
from app.services.resource_registry import migrate_legacy_feishu_resources

ApprovalAction = Literal["approve", "reject"]


@dataclass(frozen=True)
class FeishuApprovalResource:
    approval_code: str
    approval_name: str
    source: str
    resource_id: str | None = None
    legacy_resource_id: str | None = None


class FeishuApprovalService:
    """Native Feishu Approval capability under the V5 resource registry."""

    def __init__(self, app_config: FeishuAppConfig | None, *, client: FeishuClient | None = None) -> None:
        self.app_config = app_config
        if client is not None:
            self.client = client
        elif app_config is not None:
            self.client = FeishuClient(app_config)
        else:
            raise ValueError("app_config or client is required")

    def list_approval_resources(self, db: Session) -> list[FeishuApprovalResource]:
        resources = self._registered_approval_resources(db)
        if resources:
            return resources
        return self._legacy_approval_resources(db)

    async def fetch_pending_tasks(
        self,
        db: Session,
        *,
        open_id: str | None,
        limit: int = 8,
        enrich_details: bool = True,
    ) -> dict[str, Any]:
        if not open_id:
            return {"available": False, "items": [], "error": "缺少 open_id，无法精确查询待审批任务"}

        resources = self.list_approval_resources(db)
        names_by_code = {item.approval_code: item.approval_name for item in resources}
        live_result = await self.fetch_user_pending_tasks(
            open_id=open_id,
            limit=limit,
            names_by_code=names_by_code,
            enrich_details=enrich_details,
        )
        if live_result["available"]:
            return live_result

        if not resources:
            return {
                "available": False,
                "items": [],
                "error": live_result.get("error") or "还没有自动发现审批资源，无法查询审批任务列表",
            }

        items: list[dict[str, Any]] = []
        errors: list[str] = [str(live_result["error"])] if live_result.get("error") else []
        for resource in resources:
            if len(items) >= limit:
                break
            page_size = max(1, min(50, limit - len(items)))
            path = f"/open-apis/approval/v4/tasks/search?page_size={page_size}&user_id_type=open_id"
            payload = {
                "user_id": open_id,
                "approval_code": resource.approval_code,
                "task_status_list": ["PENDING"],
            }
            try:
                body = await self.client.api_post(path, payload)
            except HTTPException as exc:
                errors.append(_safe_feishu_error(exc.detail))
                continue
            data = body.get("data") or {}
            for task in extract_approval_task_items(data):
                task.setdefault("approval_code", resource.approval_code)
                task.setdefault("approval_name", resource.approval_name)
                items.append(task)
                if len(items) >= limit:
                    break

        if items:
            if enrich_details:
                await self.enrich_tasks_with_instance_details(items)
            return {"available": True, "items": items, "errors": errors}
        if errors:
            return {"available": False, "items": [], "error": errors[0]}
        return {"available": True, "items": []}

    async def fetch_user_pending_tasks(
        self,
        *,
        open_id: str,
        limit: int,
        names_by_code: dict[str, str],
        enrich_details: bool = True,
    ) -> dict[str, Any]:
        params = {
            "user_id": open_id,
            "topic": "1",
            "user_id_type": "open_id",
            "page_size": min(max(limit, 1), 200),
        }
        try:
            body = await self.client.api_get("/open-apis/approval/v4/tasks/query", params=params)
        except HTTPException as exc:
            return {"available": False, "items": [], "error": _safe_feishu_error(exc.detail)}

        data = body.get("data") or {}
        items = extract_approval_task_items(data)[:limit]
        for item in items:
            code = str(item.get("definition_code") or item.get("approval_code") or "").strip()
            if code:
                item.setdefault("approval_code", code)
                item.setdefault("approval_name", names_by_code.get(code, code))
            else:
                item.setdefault("approval_name", "审批")
        if enrich_details:
            await self.enrich_tasks_with_instance_details(items)
        return {"available": True, "items": items}

    async def query_tasks(
        self,
        *,
        topic: str = "1",
        user_id: str | None = None,
        definition_code: str | None = None,
        locale: str | None = None,
        page_size: int = 20,
        page_token: str | None = None,
        user_id_type: str = "open_id",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "topic": topic,
            "page_size": min(max(page_size, 1), 200),
            "user_id_type": user_id_type or "open_id",
        }
        if user_id:
            params["user_id"] = user_id
        if definition_code:
            params["definition_code"] = definition_code
        if locale:
            params["locale"] = locale
        if page_token:
            params["page_token"] = page_token
        return await self.client.api_get("/open-apis/approval/v4/tasks", params=params)

    async def query_initiated_instances(
        self,
        *,
        approval_code: str | None = None,
        user_id: str | None = None,
        instance_start_time_from: str | None = None,
        instance_start_time_to: str | None = None,
        locale: str | None = None,
        page_size: int = 20,
        page_token: str | None = None,
        user_id_type: str = "open_id",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "page_size": min(max(page_size, 1), 100),
            "user_id_type": user_id_type or "open_id",
        }
        if approval_code:
            payload["approval_code"] = approval_code
        if user_id:
            payload["user_id"] = user_id
        if instance_start_time_from:
            payload["instance_start_time_from"] = instance_start_time_from
        if instance_start_time_to:
            payload["instance_start_time_to"] = instance_start_time_to
        if locale:
            payload["locale"] = locale
        if page_token:
            payload["page_token"] = page_token
        return await self.client.api_post("/open-apis/approval/v4/instances/query", payload)

    async def get_instance(
        self,
        *,
        instance_code: str,
        locale: str | None = None,
        user_id_type: str = "open_id",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"user_id_type": user_id_type or "open_id"}
        if locale:
            params["locale"] = locale
        return await self.client.api_get(f"/open-apis/approval/v4/instances/{instance_code}", params=params)

    async def enrich_tasks_with_instance_details(self, items: list[dict[str, Any]]) -> None:
        for item in items:
            process_code = str(item.get("process_code") or item.get("instance_code") or "").strip()
            if not process_code:
                continue
            try:
                body = await self.client.api_get(
                    f"/open-apis/approval/v4/instances/{process_code}",
                    params={"user_id_type": "open_id"},
                )
            except HTTPException as exc:
                item["detail_error"] = _safe_feishu_error(exc.detail)
                continue
            data = body.get("data") or {}
            if isinstance(data, dict):
                item["instance_detail"] = data
                item.setdefault("instance_code", data.get("instance_code") or process_code)
                item.setdefault("serial_number", data.get("serial_number"))
                detail_name = str(data.get("approval_name") or "").strip()
                current_name = str(item.get("approval_name") or "").strip()
                current_code = str(item.get("approval_code") or item.get("definition_code") or "").strip()
                if detail_name and (not current_name or current_name == current_code):
                    item["approval_name"] = detail_name
                else:
                    item.setdefault("approval_name", detail_name or item.get("approval_name"))
                item.setdefault("start_time", data.get("start_time"))

    async def execute_task_action(
        self,
        *,
        open_id: str | None,
        user_access_token: str | None = None,
        item: dict[str, Any],
        action: ApprovalAction,
        comment: str = "由数字参谋根据用户在飞书中的二次确认提交。",
        form: str | None = None,
    ) -> dict[str, Any]:
        instance_code = str(item.get("instance_code") or item.get("process_code") or "").strip()
        task_id = str(item.get("task_id") or "").strip()
        if not instance_code or not task_id or not open_id:
            return {"ok": False, "error": "缺少 instance_code、task_id 或 open_id"}
        if not user_access_token:
            return {"ok": False, "error": "审批 approve/reject 需要本人用户级能力包授权，请先由资源所有者本人完成统一授权。"}

        path_by_action = {
            "approve": "/open-apis/approval/v4/tasks/pass?user_id_type=open_id",
            "reject": "/open-apis/approval/v4/tasks/refuse?user_id_type=open_id",
        }
        payload = {
            "instance_code": instance_code,
            "task_id": task_id,
            "comment": comment,
        }
        if form:
            payload["form"] = form
        try:
            body = await self.client.api_post_user(
                path_by_action[action],
                user_access_token=user_access_token,
                payload=payload,
            )
        except HTTPException as exc:
            return {"ok": False, "error": _safe_feishu_error(exc.detail)}
        return {"ok": True, "body": body}

    async def execute_task_transfer(
        self,
        *,
        open_id: str | None,
        user_access_token: str | None = None,
        item: dict[str, Any],
        transfer_user_id: str,
        comment: str | None = None,
        user_id_type: str = "open_id",
    ) -> dict[str, Any]:
        instance_code = str(item.get("instance_code") or item.get("process_code") or "").strip()
        task_id = str(item.get("task_id") or "").strip()
        transfer_user_id = transfer_user_id.strip()
        user_id_type = (user_id_type or "open_id").strip()
        if not instance_code or not task_id or not open_id or not transfer_user_id:
            return {"ok": False, "error": "缺少 instance_code、task_id、open_id 或 transfer_user_id"}
        if not user_access_token:
            return {"ok": False, "error": "审批转交需要本人用户级能力包授权，请先由资源所有者本人完成统一授权。"}

        payload = {
            "instance_code": instance_code,
            "task_id": task_id,
            "transfer_user_id": transfer_user_id,
        }
        if comment:
            payload["comment"] = comment
        try:
            body = await self.client.api_post_user(
                f"/open-apis/approval/v4/tasks/forward?user_id_type={user_id_type}",
                user_access_token=user_access_token,
                payload=payload,
            )
        except HTTPException as exc:
            return {"ok": False, "error": _safe_feishu_error(exc.detail)}
        return {"ok": True, "body": body}

    async def execute_instance_remind(
        self,
        *,
        open_id: str | None,
        user_access_token: str | None = None,
        instance_code: str,
        task_ids: list[str],
        comment: str | None = None,
    ) -> dict[str, Any]:
        instance_code = instance_code.strip()
        task_ids = [task_id.strip() for task_id in task_ids if task_id.strip()]
        if not instance_code or not task_ids or not open_id:
            return {"ok": False, "error": "缺少 instance_code、task_ids 或 open_id"}
        if not user_access_token:
            return {"ok": False, "error": "审批催办需要本人用户级能力包授权，请先由资源所有者本人完成统一授权。"}

        payload: dict[str, Any] = {
            "instance_code": instance_code,
            "task_ids": task_ids,
        }
        if comment:
            payload["comment"] = comment
        try:
            body = await self.client.api_post_user(
                "/open-apis/approval/v4/instances/remind",
                user_access_token=user_access_token,
                payload=payload,
            )
        except HTTPException as exc:
            return {"ok": False, "error": _safe_feishu_error(exc.detail)}
        return {"ok": True, "body": body}

    async def execute_instance_cancel(
        self,
        *,
        open_id: str | None,
        user_access_token: str | None = None,
        instance_code: str,
    ) -> dict[str, Any]:
        instance_code = instance_code.strip()
        if not instance_code or not open_id:
            return {"ok": False, "error": "缺少 instance_code 或 open_id"}
        if not user_access_token:
            return {"ok": False, "error": "审批撤回需要本人用户级能力包授权，请先由资源所有者本人完成统一授权。"}

        try:
            body = await self.client.api_post_user(
                "/open-apis/approval/v4/instances/recall",
                user_access_token=user_access_token,
                payload={"instance_code": instance_code},
            )
        except HTTPException as exc:
            return {"ok": False, "error": _safe_feishu_error(exc.detail)}
        return {"ok": True, "body": body}

    async def execute_instance_cc(
        self,
        *,
        open_id: str | None,
        user_access_token: str | None = None,
        instance_code: str,
        cc_user_ids: list[str],
        comment: str | None = None,
        user_id_type: str = "open_id",
    ) -> dict[str, Any]:
        instance_code = instance_code.strip()
        cc_user_ids = [user_id.strip() for user_id in cc_user_ids if user_id.strip()]
        user_id_type = (user_id_type or "open_id").strip()
        if not instance_code or not cc_user_ids or not open_id:
            return {"ok": False, "error": "缺少 instance_code、cc_user_ids 或 open_id"}
        if not user_access_token:
            return {"ok": False, "error": "审批抄送需要本人用户级能力包授权，请先由资源所有者本人完成统一授权。"}

        payload: dict[str, Any] = {
            "instance_code": instance_code,
            "cc_user_ids": cc_user_ids,
        }
        if comment:
            payload["comment"] = comment
        try:
            body = await self.client.api_post_user(
                f"/open-apis/approval/v4/instances/add_cc?user_id_type={user_id_type}",
                user_access_token=user_access_token,
                payload=payload,
            )
        except HTTPException as exc:
            return {"ok": False, "error": _safe_feishu_error(exc.detail)}
        return {"ok": True, "body": body}

    async def execute_task_add_sign(
        self,
        *,
        open_id: str | None,
        user_access_token: str | None = None,
        item: dict[str, Any],
        add_sign_user_ids: list[str],
        add_sign_type: int,
        approval_method: int | None = None,
        comment: str | None = None,
        user_id_type: str = "open_id",
    ) -> dict[str, Any]:
        instance_code = str(item.get("instance_code") or item.get("process_code") or "").strip()
        task_id = str(item.get("task_id") or "").strip()
        add_sign_user_ids = [user_id.strip() for user_id in add_sign_user_ids if user_id.strip()]
        user_id_type = (user_id_type or "open_id").strip()
        if not instance_code or not task_id or not open_id or not add_sign_user_ids:
            return {"ok": False, "error": "缺少 instance_code、task_id、open_id 或 add_sign_user_ids"}
        if add_sign_type not in {1, 2, 3}:
            return {"ok": False, "error": "add_sign_type 必须是 1、2 或 3"}
        if approval_method is not None and approval_method not in {1, 2, 3}:
            return {"ok": False, "error": "approval_method 必须是 1、2 或 3"}
        if not user_access_token:
            return {"ok": False, "error": "审批加签需要本人用户级能力包授权，请先由资源所有者本人完成统一授权。"}

        payload: dict[str, Any] = {
            "instance_code": instance_code,
            "task_id": task_id,
            "add_sign_user_ids": add_sign_user_ids,
            "add_sign_type": add_sign_type,
        }
        if approval_method is not None:
            payload["approval_method"] = approval_method
        if comment:
            payload["comment"] = comment
        try:
            body = await self.client.api_post_user(
                f"/open-apis/approval/v4/tasks/add_sign?user_id_type={user_id_type}",
                user_access_token=user_access_token,
                payload=payload,
            )
        except HTTPException as exc:
            return {"ok": False, "error": _safe_feishu_error(exc.detail)}
        return {"ok": True, "body": body}

    async def execute_task_rollback(
        self,
        *,
        open_id: str | None,
        user_access_token: str | None = None,
        item: dict[str, Any],
        node_ids: list[str],
        comment: str | None = None,
    ) -> dict[str, Any]:
        instance_code = str(item.get("instance_code") or item.get("process_code") or "").strip()
        task_id = str(item.get("task_id") or "").strip()
        node_ids = [node_id.strip() for node_id in node_ids if node_id.strip()]
        if not instance_code or not task_id or not open_id or not node_ids:
            return {"ok": False, "error": "缺少 instance_code、task_id、open_id 或 node_ids"}
        if not user_access_token:
            return {"ok": False, "error": "审批退回需要本人用户级能力包授权，请先由资源所有者本人完成统一授权。"}

        payload: dict[str, Any] = {
            "instance_code": instance_code,
            "task_id": task_id,
            "node_ids": node_ids,
        }
        if comment:
            payload["comment"] = comment
        try:
            body = await self.client.api_post_user(
                "/open-apis/approval/v4/tasks/rollback",
                user_access_token=user_access_token,
                payload=payload,
            )
        except HTTPException as exc:
            return {"ok": False, "error": _safe_feishu_error(exc.detail)}
        return {"ok": True, "body": body}

    def _registered_approval_resources(self, db: Session) -> list[FeishuApprovalResource]:
        resources = db.scalars(
            select(Resource)
            .where(Resource.company_id == self.app_config.company_id)
            .where(Resource.platform == "feishu")
            .where(Resource.resource_type.in_(("approval", "approval_code")))
            .where(Resource.enabled.is_(True))
            .order_by(Resource.created_at.desc())
        ).all()
        items: list[FeishuApprovalResource] = []
        seen: set[str] = set()
        for resource in resources:
            code = str(resource.resource_id or "").strip()
            if not _valid_approval_code(code) or code in seen:
                continue
            seen.add(code)
            items.append(
                FeishuApprovalResource(
                    approval_code=code,
                    approval_name=resource.resource_name or code,
                    source="resources",
                    resource_id=str(resource.id) if getattr(resource, "id", None) else None,
                )
            )
        return items

    def _legacy_approval_resources(self, db: Session) -> list[FeishuApprovalResource]:
        items: list[FeishuApprovalResource] = []
        seen: set[str] = set()
        for migration in migrate_legacy_feishu_resources(db, app_config=self.app_config, resource_type="approval_code"):
            code = migration.external_id
            if not _valid_approval_code(code) or code in seen:
                continue
            seen.add(code)
            items.append(
                FeishuApprovalResource(
                    approval_code=code,
                    approval_name=migration.name or code,
                    source="resources_migrated",
                    resource_id=str(migration.resource.id),
                    legacy_resource_id=str(migration.legacy_id),
                )
            )
        return items


def extract_approval_task_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("task_list", "tasks", "items", "approval_tasks"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def approval_form_fields(form: Any, *, max_fields: int = 8) -> list[tuple[str, str]]:
    if not form:
        return []
    if isinstance(form, str):
        try:
            form = json.loads(form)
        except json.JSONDecodeError:
            return []
    if not isinstance(form, list):
        return []
    fields: list[tuple[str, str]] = []
    for raw in form:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or raw.get("title") or "").strip()
        value = _approval_field_display_value(raw)
        if not name or value in (None, ""):
            continue
        fields.append((name, value))
        if len(fields) >= max_fields:
            break
    return fields


def approval_attachment_refs(form: Any) -> list[dict[str, str]]:
    if not form:
        return []
    if isinstance(form, str):
        try:
            form = json.loads(form)
        except json.JSONDecodeError:
            return []
    if not isinstance(form, list):
        return []

    refs: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in form:
        if not isinstance(raw, dict):
            continue
        field_name = str(raw.get("name") or raw.get("title") or "附件").strip()
        allow_name_only = _looks_like_attachment_field(raw)
        candidates = _extract_attachment_refs(raw.get("value"), field_name=field_name, allow_name_only=allow_name_only)
        if allow_name_only:
            candidates.extend(_extract_attachment_url_refs(raw, field_name=field_name))
        if not candidates and allow_name_only:
            candidates = _extract_attachment_refs(raw, field_name=field_name, allow_name_only=True)
        for ref in candidates:
            key = (ref.get("token", ""), ref.get("url", ""), ref.get("name", ""))
            if key not in seen:
                refs.append(ref)
                seen.add(key)
    return refs


def approval_amount(fields: dict[str, str]) -> float | None:
    value = first_matching_field(fields, ["申请金额", "付款金额", "报销金额", "合同金额", "费用金额", "费用汇总", "金额"])
    if not value:
        return None
    cleaned = "".join(char for char in value if char.isdigit() or char in ".-")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _looks_like_attachment_field(field: dict[str, Any]) -> bool:
    name = str(field.get("name") or field.get("title") or "").lower()
    field_type = str(field.get("type") or "").lower()
    return any(term in name for term in ("附件", "文件", "合同", "发票", "凭证")) or any(
        term in field_type for term in ("attachment", "file", "upload", "image")
    )


def _extract_attachment_refs(value: Any, *, field_name: str, allow_name_only: bool = False) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return refs
        return _extract_attachment_refs(parsed, field_name=field_name, allow_name_only=allow_name_only)
    if isinstance(value, list):
        for item in value:
            refs.extend(_extract_attachment_refs(item, field_name=field_name, allow_name_only=allow_name_only))
        return refs
    if not isinstance(value, dict):
        return refs

    raw_field_name = str(value.get("name") or value.get("title") or "").strip()
    type_text = str(value.get("type") or "").lower()
    is_explicit_attachment_field = bool(raw_field_name) and any(
        term in type_text for term in ("attachment", "file", "upload", "image")
    )
    nested_field_name = raw_field_name if is_explicit_attachment_field else field_name
    nested_allow_name_only = allow_name_only or _looks_like_attachment_field(value)
    url_refs: list[dict[str, str]] = []
    if nested_allow_name_only:
        url_refs = _extract_attachment_url_refs(value, field_name=nested_field_name)
        refs.extend(url_refs)

    token = _first_string_value(
        value,
        [
            "file_token",
            "fileToken",
            "token",
            "file_id",
            "fileId",
            "file_key",
            "fileKey",
            "media_id",
            "mediaId",
            "attachment_id",
            "attachmentId",
            "attachment_token",
            "attachmentToken",
        ],
    )
    name = _first_string_value(value, ["file_name", "fileName", "filename", "name", "title"])
    file_type = _first_string_value(value, ["file_type", "fileType", "type", "mime_type", "mimeType"])
    url = _first_string_value(value, ["url", "link", "download_url", "downloadUrl"])
    has_file_marker = bool(token or url or _looks_like_file_name(name) or _looks_like_file_type(file_type))
    name_only_container = any(key in value for key in ("value", "children", "items"))
    if has_file_marker or (nested_allow_name_only and name and not url_refs and not name_only_container):
        refs.append(
            {
                "field_name": nested_field_name,
                "name": name or nested_field_name,
                "token": token or "",
                "type": file_type or "",
                "url": url or "",
            }
        )

    for key, nested in value.items():
        if key == "value" and url_refs:
            continue
        if isinstance(nested, str | list | dict):
            refs.extend(
                _extract_attachment_refs(
                    nested,
                    field_name=nested_field_name,
                    allow_name_only=nested_allow_name_only,
                )
            )
    return refs


def _extract_attachment_url_refs(field: dict[str, Any], *, field_name: str) -> list[dict[str, str]]:
    value = field.get("value")
    urls: list[str] = []
    # Format 1: value is a string of comma-separated URLs (native approval attachmentV2 format)
    if isinstance(value, str):
        urls = [u.strip() for u in value.split(",") if u.strip().startswith(("http://", "https://"))]
    elif isinstance(value, list):
        # Format 2: value is a list of URL strings
        urls = [str(item).strip() for item in value if isinstance(item, str) and item.strip().startswith(("http://", "https://"))]
        # Format 3: value is a list of dicts with "url" key (return_file_token)
        if not urls:
            urls = [item.get("url", "").strip() for item in value if isinstance(item, dict) and item.get("url")]
    if not urls:
        return []
    names = [name.strip() for name in str(field.get("ext") or "").split(",") if name.strip()]
    refs: list[dict[str, str]] = []
    for index, url in enumerate(urls):
        name = names[index] if index < len(names) else f"{field_name}{index + 1}"
        refs.append(
            {
                "field_name": field_name,
                "name": name,
                "token": "",
                "type": Path(name).suffix.lstrip(".") if "." in name else str(field.get("type") or ""),
                "url": url,
            }
        )
    return refs


def _looks_like_file_name(value: str | None) -> bool:
    if not value:
        return False
    lowered = value.lower()
    return any(lowered.endswith(ext) for ext in (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".png", ".jpg", ".jpeg"))


def _looks_like_file_type(value: str | None) -> bool:
    if not value:
        return False
    return value.lower() in {"pdf", "doc", "docx", "xls", "xlsx", "zip", "png", "jpg", "jpeg", "image", "file"}


def _first_string_value(data: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return str(value)
    return None


def first_matching_field(fields: dict[str, str], names: list[str]) -> str | None:
    for name in names:
        for key, value in fields.items():
            if name in key:
                return value
    return None


def _approval_field_display_value(field: dict[str, Any]) -> str | None:
    value = field.get("value")
    mapped = _approval_option_display_value(field, value)
    if mapped is not None:
        return mapped
    if isinstance(value, list):
        parts = [_stringify_approval_field_value(item) for item in value[:5]]
        return "、".join(part for part in parts if part)
    return _stringify_approval_field_value(value)


def _approval_option_display_value(field: dict[str, Any], value: Any) -> str | None:
    options = field.get("option")
    if not options:
        return None
    option_items: list[dict[str, Any]] = []
    if isinstance(options, list):
        option_items = [item for item in options if isinstance(item, dict)]
    elif isinstance(options, dict):
        nested = options.get("options") or options.get("items") or options.get("children")
        if isinstance(nested, list):
            option_items = [item for item in nested if isinstance(item, dict)]
    selected_values = {str(item) for item in value} if isinstance(value, list) else {str(value)}
    labels: list[str] = []
    for option in option_items:
        option_id = str(
            option.get("id")
            or option.get("value")
            or option.get("key")
            or option.get("option_id")
            or option.get("text")
            or ""
        )
        if option_id not in selected_values:
            continue
        label = option.get("text") or option.get("name") or option.get("label") or option.get("value")
        if isinstance(label, dict):
            label = label.get("zh_cn") or label.get("zh-CN") or label.get("en_us") or label.get("text")
        if label:
            labels.append(str(label))
    return "、".join(labels) if labels else None


def _stringify_approval_field_value(value: Any) -> str | None:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, dict):
        for key in ("text", "name", "label", "value", "display_name", "amount"):
            inner = value.get(key)
            if inner not in (None, "", [], {}):
                return _stringify_approval_field_value(inner)
        return json.dumps(value, ensure_ascii=False, default=str)[:120]
    return str(value)[:120]


def _safe_feishu_error(detail: Any) -> str:
    if isinstance(detail, dict):
        body = detail.get("body")
        if isinstance(body, dict):
            return json.dumps(body, ensure_ascii=False, default=str)[:500]
        return json.dumps(detail, ensure_ascii=False, default=str)[:500]
    return str(detail)[:500]


def _valid_approval_code(value: str) -> bool:
    if not value or "[" in value or "]" in value:
        return False
    return len(value) >= 8
