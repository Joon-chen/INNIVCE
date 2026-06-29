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
                children = await self._list_all_child_departments(department_id=department_id)
            except HTTPException as exc:
                errors.append(_safe_error(exc.detail))
                continue
            for department in children:
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
        department_paths = build_department_paths(departments, root_department_id=root_department_id)
        for department in departments:
            department_id = str(department.get("department_id") or department.get("open_department_id") or "").strip()
            path = department_paths.get(department_id) or {"names": [], "ids": []}
            department["path_names"] = path["names"]
            department["path_source_department_ids"] = path["ids"]
        user_department_ids = [root_department_id, *[str(item.get("department_id")) for item in departments if item.get("department_id")]]
        for department_id in user_department_ids:
            if len(users_by_open_id) >= max_users:
                break
            try:
                users = await self._list_all_users_by_department(department_id=department_id, remaining=max_users - len(users_by_open_id))
            except HTTPException as exc:
                errors.append(_safe_error(exc.detail))
                continue
            for user in users:
                open_id = str(user.get("open_id") or user.get("user_id") or "").strip()
                if not open_id:
                    continue
                existing = users_by_open_id.get(open_id) or {}
                users_by_open_id[open_id] = merge_user_department_membership(
                    existing=existing,
                    user=user,
                    department_id=department_id,
                    department_names_by_id=department_names_by_id,
                    department_paths=department_paths,
                )
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

    async def _list_all_child_departments(self, *, department_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            body = await self.list_child_departments(department_id=department_id, page_token=page_token)
            data = body.get("data") or {}
            items.extend(extract_items(data, ("items", "departments")))
            page_token = str(data.get("page_token") or "").strip() or None
            if not data.get("has_more") or not page_token:
                break
        return items

    async def _list_all_users_by_department(self, *, department_id: str, remaining: int) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while remaining > 0:
            body = await self.list_users_by_department(
                department_id=department_id,
                page_size=50,
                page_token=page_token,
            )
            data = body.get("data") or {}
            page_items = extract_items(data, ("items", "users"))
            items.extend(page_items)
            remaining -= len(page_items)
            page_token = str(data.get("page_token") or "").strip() or None
            if not data.get("has_more") or not page_token:
                break
        return items


def extract_items(data: dict[str, Any], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def build_department_paths(departments: list[dict[str, Any]], *, root_department_id: str = "0") -> dict[str, dict[str, list[str]]]:
    by_id = {str(item.get("department_id") or item.get("open_department_id") or ""): item for item in departments}
    cache: dict[str, dict[str, list[str]]] = {}

    def path_for(department_id: str) -> dict[str, list[str]]:
        if department_id in cache:
            return cache[department_id]
        item = by_id.get(department_id)
        if not item:
            cache[department_id] = {"names": [], "ids": []}
            return cache[department_id]
        parent_id = str(item.get("parent_department_id") or item.get("parent_open_department_id") or "").strip()
        parent_path = path_for(parent_id) if parent_id and parent_id != root_department_id else {"names": [], "ids": []}
        name = str(item.get("name") or item.get("i18n_name") or department_id).strip()
        cache[department_id] = {
            "names": [*parent_path["names"], name],
            "ids": [*parent_path["ids"], department_id],
        }
        return cache[department_id]

    for key in by_id:
        path_for(key)
    return cache


def merge_user_department_membership(
    *,
    existing: dict[str, Any],
    user: dict[str, Any],
    department_id: str,
    department_names_by_id: dict[str, str],
    department_paths: dict[str, dict[str, list[str]]],
) -> dict[str, Any]:
    raw_department_ids = _ordered_department_ids(user, fallback_department_id=department_id)
    existing_department_ids = _string_list(existing.get("department_ids"))
    department_ids = _dedupe_preserve_order([*existing_department_ids, *raw_department_ids, department_id])
    department_names = [department_names_by_id[item] for item in department_ids if department_names_by_id.get(item)]
    paths = [department_paths[item] for item in department_ids if department_paths.get(item)]
    orders_by_department = _orders_by_department(user.get("orders"))
    return {
        **existing,
        **user,
        "department_ids": department_ids,
        "department_names": _dedupe_preserve_order(department_names),
        "department_paths": paths,
        "orders_by_department_id": orders_by_department,
    }


def _ordered_department_ids(user: dict[str, Any], *, fallback_department_id: str) -> list[str]:
    order_items = user.get("orders") if isinstance(user.get("orders"), list) else []
    by_order = sorted(
        (
            (
                0 if bool(item.get("is_primary_dept")) else 1,
                int(item.get("department_order") or 0),
                str(item.get("department_id") or "").strip(),
            )
            for item in order_items
            if isinstance(item, dict) and str(item.get("department_id") or "").strip()
        ),
        key=lambda item: (item[0], item[1], item[2]),
    )
    ids = [item[2] for item in by_order]
    ids.extend(_string_list(user.get("department_ids")))
    ids.append(fallback_department_id)
    return _dedupe_preserve_order(ids)


def _orders_by_department(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        department_id = str(item.get("department_id") or "").strip()
        if not department_id:
            continue
        result[department_id] = {
            "is_primary_dept": bool(item.get("is_primary_dept")),
            "department_order": item.get("department_order"),
            "user_order": item.get("user_order"),
        }
    return result


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if value in (None, ""):
        return []
    return [str(value).strip()]


def _safe_error(detail: Any) -> str:
    return str(detail)[:500]
