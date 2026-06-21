from typing import Any
from urllib.parse import quote

from fastapi import HTTPException

from app.models.entities import FeishuAppConfig
from app.services.feishu.client import FeishuClient


class FeishuContactService:
    """Native Feishu contact capability for V5 Administration."""

    def __init__(self, app_config: FeishuAppConfig, *, client: FeishuClient | None = None) -> None:
        self.app_config = app_config
        self.client = client or FeishuClient(app_config)

    async def list_child_departments(
        self,
        *,
        department_id: str = "0",
        department_id_type: str = "department_id",
        user_id_type: str = "open_id",
        fetch_child: bool | None = None,
        page_size: int = 50,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "department_id_type": department_id_type,
            "user_id_type": user_id_type,
            "page_size": min(max(page_size, 1), 50),
        }
        if fetch_child is not None:
            params["fetch_child"] = fetch_child
        if page_token:
            params["page_token"] = page_token
        return await self.client.api_get(
            f"/open-apis/contact/v3/departments/{quote(department_id, safe='')}/children",
            params=params,
        )

    async def list_users_by_department(
        self,
        *,
        department_id: str = "0",
        department_id_type: str = "department_id",
        user_id_type: str = "open_id",
        page_size: int = 50,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "department_id": department_id,
            "department_id_type": department_id_type,
            "user_id_type": user_id_type,
            "page_size": min(max(page_size, 1), 50),
        }
        if page_token:
            params["page_token"] = page_token
        return await self.client.api_get("/open-apis/contact/v3/users/find_by_department", params=params)

    async def list_authorized_scopes(
        self,
        *,
        department_id_type: str = "open_department_id",
        user_id_type: str = "open_id",
        page_size: int = 100,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "department_id_type": department_id_type,
            "user_id_type": user_id_type,
            "page_size": min(max(page_size, 1), 100),
        }
        if page_token:
            params["page_token"] = page_token
        return await self.client.api_get("/open-apis/contact/v3/scopes", params=params)

    async def snapshot_organization(
        self,
        *,
        root_department_id: str = "0",
        max_departments: int = 100,
        max_users: int = 500,
    ) -> dict[str, Any]:
        departments: list[dict[str, Any]] = []
        users_by_open_id: dict[str, dict[str, Any]] = {}
        queue = [root_department_id]
        seen_departments = set(queue)
        errors: list[str] = []

        while queue and len(departments) < max_departments:
            department_id = queue.pop(0)
            try:
                body = await self.list_child_departments(department_id=department_id)
            except HTTPException as exc:
                errors.append(_safe_error(exc.detail))
                continue
            for department in extract_items(body.get("data") or {}, ("items", "departments")):
                departments.append(department)
                child_id = str(department.get("department_id") or "").strip()
                if child_id and child_id not in seen_departments:
                    seen_departments.add(child_id)
                    queue.append(child_id)
                if len(departments) >= max_departments:
                    break

        department_names_by_id = {
            str(item.get("department_id") or item.get("open_department_id") or ""): str(
                item.get("name") or item.get("i18n_name") or ""
            )
            for item in departments
            if item.get("department_id") or item.get("open_department_id")
        }
        user_department_ids = [root_department_id, *[str(item.get("department_id")) for item in departments if item.get("department_id")]]
        for department_id in user_department_ids:
            if len(users_by_open_id) >= max_users:
                break
            try:
                body = await self.list_users_by_department(department_id=department_id)
            except HTTPException as exc:
                errors.append(_safe_error(exc.detail))
                continue
            for user in extract_items(body.get("data") or {}, ("items", "users")):
                open_id = str(user.get("open_id") or user.get("user_id") or "").strip()
                if not open_id:
                    continue
                existing = users_by_open_id.get(open_id) or {}
                department_ids = set(existing.get("department_ids") or [])
                department_ids.add(department_id)
                users_by_open_id[open_id] = {
                    **existing,
                    **user,
                    "department_ids": sorted(department_ids),
                    "department_names": [
                        department_names_by_id[item]
                        for item in sorted(department_ids)
                        if department_names_by_id.get(item)
                    ],
                }
                if len(users_by_open_id) >= max_users:
                    break

        return {
            "available": not errors,
            "departments": departments,
            "users": list(users_by_open_id.values()),
            "department_count": len(departments),
            "user_count": len(users_by_open_id),
            **({"errors": errors, "error": errors[0]} if errors else {}),
        }


def extract_items(data: dict[str, Any], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _safe_error(detail: Any) -> str:
    return str(detail)[:500]
