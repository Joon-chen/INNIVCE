from typing import Any
from urllib.parse import quote

from app.models.entities import FeishuAppConfig
from app.services.feishu.client import FeishuClient


class FeishuTaskService:
    """Native Feishu Task capability for V5 workspace resources."""

    def __init__(self, app_config: FeishuAppConfig, *, client: FeishuClient | None = None) -> None:
        self.app_config = app_config
        self.client = client or FeishuClient(app_config)

    async def list_tasks(
        self,
        *,
        page_size: int = 50,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page_size": min(max(page_size, 1), 100)}
        if page_token:
            params["page_token"] = page_token
        return await self.client.api_get("/open-apis/task/v2/tasks", params=params)

    async def create_task(
        self,
        *,
        summary: str,
        description: str | None = None,
        due: dict[str, Any] | None = None,
        members: list[dict[str, Any]] | None = None,
        tasklists: list[dict[str, Any]] | None = None,
        client_token: str | None = None,
        user_id_type: str = "open_id",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"summary": summary}
        if description:
            payload["description"] = description
        if due:
            payload["due"] = due
        if members:
            payload["members"] = members
        if tasklists:
            payload["tasklists"] = tasklists
        if client_token:
            payload["client_token"] = client_token
        return await self.client.api_post(
            f"/open-apis/task/v2/tasks?user_id_type={user_id_type or 'open_id'}",
            payload,
        )

    async def complete_task(
        self,
        *,
        task_guid: str,
        completed_at: str,
        user_id_type: str = "open_id",
        user_access_token: str | None = None,
    ) -> dict[str, Any]:
        path = f"/open-apis/task/v2/tasks/{quote(task_guid, safe='')}?user_id_type={user_id_type or 'open_id'}"
        payload = {"task": {"completed_at": completed_at}, "update_fields": ["completed_at"]}
        if user_access_token:
            return await self.client.api_patch_user(path, user_access_token=user_access_token, payload=payload)
        return await self.client.api_patch(path, payload)

    async def reopen_task(
        self,
        *,
        task_guid: str,
        user_id_type: str = "open_id",
    ) -> dict[str, Any]:
        return await self.client.api_patch(
            f"/open-apis/task/v2/tasks/{quote(task_guid, safe='')}?user_id_type={user_id_type or 'open_id'}",
            {"task": {"completed_at": "0"}, "update_fields": ["completed_at"]},
        )

    async def update_task(
        self,
        *,
        task_guid: str,
        task: dict[str, Any],
        update_fields: list[str] | None = None,
        user_id_type: str = "open_id",
    ) -> dict[str, Any]:
        fields = update_fields or list(task.keys())
        return await self.client.api_patch(
            f"/open-apis/task/v2/tasks/{quote(task_guid, safe='')}?user_id_type={user_id_type or 'open_id'}",
            {"task": task, "update_fields": fields},
        )

    async def delete_task(
        self,
        *,
        task_guid: str,
    ) -> dict[str, Any]:
        return await self.client.api_delete(
            f"/open-apis/task/v2/tasks/{quote(task_guid, safe='')}",
        )

    async def assign_members(
        self,
        *,
        task_guid: str,
        add_assignees: list[str] | None = None,
        remove_assignees: list[str] | None = None,
        client_token: str | None = None,
        user_id_type: str = "open_id",
    ) -> dict[str, Any]:
        results: dict[str, Any] = {"added": 0, "removed": 0, "responses": []}
        if add_assignees:
            payload: dict[str, Any] = {"members": _task_members(add_assignees, role="assignee")}
            if client_token:
                payload["client_token"] = client_token
            results["responses"].append(
                await self.client.api_post(
                    f"/open-apis/task/v2/tasks/{quote(task_guid, safe='')}/add_members?user_id_type={user_id_type or 'open_id'}",
                    payload,
                )
            )
            results["added"] = len(add_assignees)
        if remove_assignees:
            payload = {"members": _task_members(remove_assignees, role="assignee")}
            results["responses"].append(
                await self.client.api_post(
                    f"/open-apis/task/v2/tasks/{quote(task_guid, safe='')}/remove_members?user_id_type={user_id_type or 'open_id'}",
                    payload,
                )
            )
            results["removed"] = len(remove_assignees)
        if not add_assignees and not remove_assignees:
            raise ValueError("Feishu task assign requires add_assignees or remove_assignees.")
        return results

    async def update_followers(
        self,
        *,
        task_guid: str,
        add_followers: list[str] | None = None,
        remove_followers: list[str] | None = None,
        client_token: str | None = None,
        user_id_type: str = "open_id",
    ) -> dict[str, Any]:
        results: dict[str, Any] = {"added": 0, "removed": 0, "responses": []}
        if add_followers:
            payload: dict[str, Any] = {"members": _task_members(add_followers, role="follower")}
            if client_token:
                payload["client_token"] = client_token
            results["responses"].append(
                await self.client.api_post(
                    f"/open-apis/task/v2/tasks/{quote(task_guid, safe='')}/add_members?user_id_type={user_id_type or 'open_id'}",
                    payload,
                )
            )
            results["added"] = len(add_followers)
        if remove_followers:
            payload = {"members": _task_members(remove_followers, role="follower")}
            results["responses"].append(
                await self.client.api_post(
                    f"/open-apis/task/v2/tasks/{quote(task_guid, safe='')}/remove_members?user_id_type={user_id_type or 'open_id'}",
                    payload,
                )
            )
            results["removed"] = len(remove_followers)
        if not add_followers and not remove_followers:
            raise ValueError("Feishu task followers update requires add_followers or remove_followers.")
        return results

    async def add_comment(
        self,
        *,
        task_guid: str,
        content: str,
        user_id_type: str = "open_id",
    ) -> dict[str, Any]:
        return await self.client.api_post(
            f"/open-apis/task/v2/comments?user_id_type={user_id_type or 'open_id'}",
            {"resource_id": task_guid, "resource_type": "task", "content": content},
        )

    async def upload_attachment(
        self,
        *,
        resource_id: str,
        file_path: str,
        resource_type: str = "task",
        user_id_type: str = "open_id",
    ) -> dict[str, Any]:
        return await self.client.api_post(
            f"/open-apis/task/v2/attachments/upload?user_id_type={user_id_type or 'open_id'}",
            {
                "resource_id": resource_id,
                "resource_type": resource_type,
                "file": {"path": file_path},
            },
        )

    async def create_tasklist(
        self,
        *,
        name: str,
        members: list[dict[str, Any]] | None = None,
        archive_tasklist: bool | None = None,
        user_id_type: str = "open_id",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": name}
        if members:
            payload["members"] = members
        if archive_tasklist is not None:
            payload["archive_tasklist"] = archive_tasklist
        return await self.client.api_post(
            f"/open-apis/task/v2/tasklists?user_id_type={user_id_type or 'open_id'}",
            payload,
        )

    async def delete_tasklist(
        self,
        *,
        tasklist_guid: str,
    ) -> dict[str, Any]:
        return await self.client.api_delete(
            f"/open-apis/task/v2/tasklists/{quote(tasklist_guid, safe='')}",
        )

    async def update_tasklist(
        self,
        *,
        tasklist_guid: str,
        name: str,
        user_id_type: str = "open_id",
    ) -> dict[str, Any]:
        return await self.client.api_patch(
            f"/open-apis/task/v2/tasklists/{quote(tasklist_guid, safe='')}?user_id_type={user_id_type or 'open_id'}",
            {"tasklist": {"name": name}, "update_fields": ["name"]},
        )

    async def create_section(
        self,
        *,
        name: str,
        resource_type: str = "tasklist",
        resource_id: str | None = None,
        insert_before: str | None = None,
        insert_after: str | None = None,
        user_id_type: str = "open_id",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": name, "resource_type": resource_type}
        if resource_id:
            payload["resource_id"] = resource_id
        if insert_before:
            payload["insert_before"] = insert_before
        if insert_after:
            payload["insert_after"] = insert_after
        return await self.client.api_post(
            f"/open-apis/task/v2/sections?user_id_type={user_id_type or 'open_id'}",
            payload,
        )

    async def update_section(
        self,
        *,
        section_guid: str,
        section: dict[str, str],
        update_fields: list[str],
        user_id_type: str = "open_id",
    ) -> dict[str, Any]:
        return await self.client.api_patch(
            f"/open-apis/task/v2/sections/{quote(section_guid, safe='')}?user_id_type={user_id_type or 'open_id'}",
            {"section": section, "update_fields": update_fields},
        )

    async def delete_section(
        self,
        *,
        section_guid: str,
    ) -> dict[str, Any]:
        return await self.client.api_delete(
            f"/open-apis/task/v2/sections/{quote(section_guid, safe='')}",
        )

    async def add_to_tasklist(
        self,
        *,
        task_guid: str,
        tasklist_guid: str,
        section_guid: str | None = None,
        user_id_type: str = "open_id",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"tasklist_guid": tasklist_guid}
        if section_guid:
            payload["section_guid"] = section_guid
        return await self.client.api_post(
            f"/open-apis/task/v2/tasks/{quote(task_guid, safe='')}/add_tasklist?user_id_type={user_id_type or 'open_id'}",
            payload,
        )

    async def set_ancestor(
        self,
        *,
        task_guid: str,
        ancestor_guid: str,
        user_id_type: str = "open_id",
    ) -> dict[str, Any]:
        return await self.client.api_post(
            f"/open-apis/task/v2/tasks/{quote(task_guid, safe='')}/set_ancestor_task?user_id_type={user_id_type or 'open_id'}",
            {"ancestor_guid": ancestor_guid},
        )

    async def clear_ancestor(
        self,
        *,
        task_guid: str,
        user_id_type: str = "open_id",
    ) -> dict[str, Any]:
        return await self.client.api_post(
            f"/open-apis/task/v2/tasks/{quote(task_guid, safe='')}/set_ancestor_task?user_id_type={user_id_type or 'open_id'}",
            {},
        )

    async def create_subtask(
        self,
        *,
        parent_task_guid: str,
        summary: str,
        description: str | None = None,
        due: dict[str, Any] | None = None,
        members: list[dict[str, Any]] | None = None,
        tasklists: list[dict[str, Any]] | None = None,
        client_token: str | None = None,
        user_id_type: str = "open_id",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"summary": summary}
        if description:
            payload["description"] = description
        if due:
            payload["due"] = due
        if members:
            payload["members"] = members
        if tasklists:
            payload["tasklists"] = tasklists
        if client_token:
            payload["client_token"] = client_token
        return await self.client.api_post(
            f"/open-apis/task/v2/tasks/{quote(parent_task_guid, safe='')}/subtasks?user_id_type={user_id_type or 'open_id'}",
            payload,
        )

    async def update_tasklist_members(
        self,
        *,
        tasklist_guid: str,
        add_members: list[str] | None = None,
        remove_members: list[str] | None = None,
        user_id_type: str = "open_id",
    ) -> dict[str, Any]:
        results: dict[str, Any] = {"added": 0, "removed": 0, "responses": []}
        if add_members:
            results["responses"].append(
                await self.client.api_post(
                    f"/open-apis/task/v2/tasklists/{quote(tasklist_guid, safe='')}/add_members?user_id_type={user_id_type or 'open_id'}",
                    {"members": _tasklist_members(add_members, role="editor")},
                )
            )
            results["added"] = len(add_members)
        if remove_members:
            results["responses"].append(
                await self.client.api_post(
                    f"/open-apis/task/v2/tasklists/{quote(tasklist_guid, safe='')}/remove_members?user_id_type={user_id_type or 'open_id'}",
                    {"members": _tasklist_members(remove_members, role="editor")},
                )
            )
            results["removed"] = len(remove_members)
        if not add_members and not remove_members:
            raise ValueError("Feishu tasklist members update requires add_members or remove_members.")
        return results

    async def set_tasklist_members(
        self,
        *,
        tasklist_guid: str,
        set_members: list[str],
        user_id_type: str = "open_id",
    ) -> dict[str, Any]:
        target_members = _dedupe(set_members)
        if not target_members:
            raise ValueError("Feishu tasklist set members requires a non-empty set_members list.")
        current = await self.client.api_get(
            f"/open-apis/task/v2/tasklists/{quote(tasklist_guid, safe='')}",
            params={"user_id_type": user_id_type or "open_id"},
        )
        current_members = _current_tasklist_member_ids(current)
        add_members = [member_id for member_id in target_members if member_id not in current_members]
        remove_members = [member_id for member_id in current_members if member_id not in target_members]
        if not add_members and not remove_members:
            return {"added": 0, "removed": 0, "responses": []}
        return await self.update_tasklist_members(
            tasklist_guid=tasklist_guid,
            add_members=add_members,
            remove_members=remove_members,
            user_id_type=user_id_type,
        )


def extract_task_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("items", "tasks", "task_list"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _task_members(member_ids: list[str], *, role: str) -> list[dict[str, str]]:
    return [{"id": member_id, "role": role, "type": "user"} for member_id in member_ids]


def _tasklist_members(member_ids: list[str], *, role: str) -> list[dict[str, str]]:
    return [{"id": member_id, "role": role, "type": "user"} for member_id in member_ids]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value).strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _current_tasklist_member_ids(payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") if isinstance(payload, dict) else None
    tasklist = data.get("tasklist") if isinstance(data, dict) else None
    members = tasklist.get("members") if isinstance(tasklist, dict) else []
    if not isinstance(members, list):
        return []
    result: list[str] = []
    for member in members:
        if not isinstance(member, dict):
            continue
        member_type = str(member.get("type") or "user")
        member_id = str(member.get("id") or "").strip()
        if member_id and member_type == "user":
            result.append(member_id)
    return _dedupe(result)
