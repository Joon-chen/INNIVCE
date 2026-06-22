import asyncio
import json
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any, Callable

from sqlalchemy import select

from app.models.entities import Account
from app.services.feishu.approval import FeishuApprovalService, extract_approval_task_items
from app.services.feishu.bitable import FeishuBitableService
from app.services.feishu.calendar import FeishuCalendarService
from app.services.feishu.contact import FeishuContactService
from app.services.feishu.drive import FeishuDriveService
from app.services.feishu.im import FeishuImService
from app.services.feishu.mail import FeishuMailService, extract_mail_items
from app.services.feishu.meeting import FeishuMeetingService
from app.services.feishu.okr import FeishuOkrService
from app.services.feishu.resources import _user_access_token
from app.services.feishu.task import FeishuTaskService, extract_task_items
from app.services.tools.base import ToolContext, ToolRequest


BITABLE_READONLY_FIELD_TYPES = {
    "attachment",
    "auto_number",
    "created_time",
    "created_user",
    "formula",
    "lookup",
    "modified_time",
    "modified_user",
}

BITABLE_VIEW_TYPES = {"calendar", "gantt", "gallery", "grid", "kanban"}


ReadToolHandler = Callable[[ToolRequest], str]
WriteToolHandler = Callable[[ToolContext | None, ToolRequest], str]


def execute_feishu_api_read_tool(request: ToolRequest) -> str:
    handler = FEISHU_API_READ_BINDINGS.get(request.tool_name)
    if handler is not None:
        return handler(request)
    raise NotImplementedError(f"Verified Feishu API tool is not bound to runtime execution yet: {request.tool_name}")


def execute_feishu_api_write_tool(context: ToolContext | None, request: ToolRequest) -> str:
    handler = FEISHU_API_WRITE_BINDINGS.get(request.tool_name)
    if handler is not None:
        return handler(context, request)
    raise NotImplementedError(f"Verified Feishu API write tool is not bound to runtime execution yet: {request.tool_name}")


def _execute_approval_task_action(context: ToolContext | None, request: ToolRequest, *, action: str) -> str:
    result = _run_async(_execute_approval_task_action_async(context, request, action=action))
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or "Feishu approval action failed"))
    return "飞书审批任务已提交同意。" if action == "approve" else "飞书审批任务已提交拒绝。"


async def _execute_approval_task_action_async(
    context: ToolContext | None, request: ToolRequest, *, action: str
) -> dict[str, Any]:
    params = request.params
    item = _approval_item_params(params)
    open_id = _approval_actor_open_id(context, params)
    service = FeishuApprovalService(_app_config(params), client=params.get("client"))
    user_access_token = await _approval_user_access_token(context, params, open_id=open_id, service=service)
    return await service.execute_task_action(
        open_id=open_id,
        user_access_token=user_access_token,
        item=item,
        action=action,
        comment=str(params.get("comment") or _default_approval_comment(action)),
        form=_form_param(params),
    )


def _execute_approval_task_transfer(context: ToolContext | None, request: ToolRequest) -> str:
    result = _run_async(_execute_approval_task_transfer_async(context, request))
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or "Feishu approval transfer failed"))
    return "飞书审批任务已转交。"


async def _execute_approval_task_transfer_async(context: ToolContext | None, request: ToolRequest) -> dict[str, Any]:
    params = request.params
    item = _approval_item_params(params)
    open_id = _approval_actor_open_id(context, params)
    service = FeishuApprovalService(_app_config(params), client=params.get("client"))
    user_access_token = await _approval_user_access_token(context, params, open_id=open_id, service=service)
    return await service.execute_task_transfer(
        open_id=open_id,
        user_access_token=user_access_token,
        item=item,
        transfer_user_id=_required_str(params, "transfer_user_id"),
        comment=_optional_str(params, "comment"),
        user_id_type=_approval_user_id_type(params),
    )


def _execute_approval_instance_remind(context: ToolContext | None, request: ToolRequest) -> str:
    result = _run_async(_execute_approval_instance_remind_async(context, request))
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or "Feishu approval remind failed"))
    return "飞书审批催办已发送。"


async def _execute_approval_instance_remind_async(context: ToolContext | None, request: ToolRequest) -> dict[str, Any]:
    params = request.params
    item = _approval_item_params(params)
    instance_code = str(item.get("instance_code") or item.get("process_code") or "").strip()
    open_id = _approval_actor_open_id(context, params)
    service = FeishuApprovalService(_app_config(params), client=params.get("client"))
    user_access_token = await _approval_user_access_token(context, params, open_id=open_id, service=service)
    return await service.execute_instance_remind(
        open_id=open_id,
        user_access_token=user_access_token,
        instance_code=instance_code,
        task_ids=_approval_task_ids(params, item),
        comment=_optional_str(params, "comment"),
    )


def _execute_approval_instance_cancel(context: ToolContext | None, request: ToolRequest) -> str:
    result = _run_async(_execute_approval_instance_cancel_async(context, request))
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or "Feishu approval cancel failed"))
    return "飞书审批实例已撤回。"


async def _execute_approval_instance_cancel_async(context: ToolContext | None, request: ToolRequest) -> dict[str, Any]:
    params = request.params
    item = _approval_item_params(params)
    instance_code = str(item.get("instance_code") or item.get("process_code") or "").strip()
    open_id = _approval_actor_open_id(context, params)
    service = FeishuApprovalService(_app_config(params), client=params.get("client"))
    user_access_token = await _approval_user_access_token(context, params, open_id=open_id, service=service)
    return await service.execute_instance_cancel(
        open_id=open_id,
        user_access_token=user_access_token,
        instance_code=instance_code,
    )


def _execute_approval_instance_cc(context: ToolContext | None, request: ToolRequest) -> str:
    result = _run_async(_execute_approval_instance_cc_async(context, request))
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or "Feishu approval cc failed"))
    return "飞书审批实例已抄送。"


async def _execute_approval_instance_cc_async(context: ToolContext | None, request: ToolRequest) -> dict[str, Any]:
    params = request.params
    item = _approval_item_params(params)
    instance_code = str(item.get("instance_code") or item.get("process_code") or "").strip()
    open_id = _approval_actor_open_id(context, params)
    service = FeishuApprovalService(_app_config(params), client=params.get("client"))
    user_access_token = await _approval_user_access_token(context, params, open_id=open_id, service=service)
    return await service.execute_instance_cc(
        open_id=open_id,
        user_access_token=user_access_token,
        instance_code=instance_code,
        cc_user_ids=_required_str_list(params, "cc_user_ids"),
        comment=_optional_str(params, "comment"),
        user_id_type=_approval_user_id_type(params),
    )


def _execute_approval_task_add_sign(context: ToolContext | None, request: ToolRequest) -> str:
    result = _run_async(_execute_approval_task_add_sign_async(context, request))
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or "Feishu approval add-sign failed"))
    return "飞书审批任务已加签。"


async def _execute_approval_task_add_sign_async(context: ToolContext | None, request: ToolRequest) -> dict[str, Any]:
    params = request.params
    item = _approval_item_params(params)
    open_id = _approval_actor_open_id(context, params)
    service = FeishuApprovalService(_app_config(params), client=params.get("client"))
    user_access_token = await _approval_user_access_token(context, params, open_id=open_id, service=service)
    return await service.execute_task_add_sign(
        open_id=open_id,
        user_access_token=user_access_token,
        item=item,
        add_sign_user_ids=_required_str_list(params, "add_sign_user_ids"),
        add_sign_type=_bounded_int_param(params, "add_sign_type", minimum=1, maximum=3),
        approval_method=_optional_bounded_int_param(params, "approval_method", minimum=1, maximum=3),
        comment=_optional_str(params, "comment"),
        user_id_type=_approval_user_id_type(params),
    )


def _execute_approval_task_rollback(context: ToolContext | None, request: ToolRequest) -> str:
    result = _run_async(_execute_approval_task_rollback_async(context, request))
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or "Feishu approval rollback failed"))
    return "飞书审批任务已退回。"


async def _execute_approval_task_rollback_async(context: ToolContext | None, request: ToolRequest) -> dict[str, Any]:
    params = request.params
    item = _approval_item_params(params)
    open_id = _approval_actor_open_id(context, params)
    service = FeishuApprovalService(_app_config(params), client=params.get("client"))
    user_access_token = await _approval_user_access_token(context, params, open_id=open_id, service=service)
    return await service.execute_task_rollback(
        open_id=open_id,
        user_access_token=user_access_token,
        item=item,
        node_ids=_required_str_list(params, "node_ids"),
        comment=_optional_str(params, "comment"),
    )


def _execute_approval_task_query(request: ToolRequest) -> str:
    params = request.params
    service = FeishuApprovalService(_app_config(params), client=params.get("client"))
    open_id = _optional_str(params, "user_id") or _optional_str(params, "open_id")
    if not open_id:
        raise ValueError("查询待审批任务缺少用户 open_id。")
    result = _run_async(
        service.fetch_user_pending_tasks(
            open_id=open_id,
            limit=_int_param(params, "page_size", 20),
            names_by_code={},
        )
    )
    if not result.get("available"):
        raise RuntimeError(str(result.get("error") or "飞书审批任务查询失败。"))
    items = result.get("items") if isinstance(result.get("items"), list) else []
    if str(params.get("response_format") or "").strip() == "raw_json":
        return json.dumps({"available": True, "data": {"items": items}}, ensure_ascii=False)
    return _summary("飞书审批任务", items, title_keys=("title", "definition_name", "task_id"))


def _execute_approval_instance_get(request: ToolRequest) -> str:
    params = request.params
    service = FeishuApprovalService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.get_instance(
            instance_code=_required_str(params, "instance_code"),
            locale=_optional_str(params, "locale"),
            user_id_type=str(params.get("user_id_type") or "open_id"),
        )
    )
    data = _response_data(payload)
    title = _item_title(data, ("definition_name", "approval_name", "serial_number", "instance_code"))
    return f"飞书审批实例已读取：{title}" if title else "飞书审批实例已读取。"


def _execute_approval_instance_initiated(request: ToolRequest) -> str:
    params = request.params
    service = FeishuApprovalService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.query_initiated_instances(
            approval_code=_optional_str(params, "approval_code") or _optional_str(params, "definition_code"),
            user_id=_optional_str(params, "user_id") or _optional_str(params, "open_id"),
            instance_start_time_from=_optional_str(params, "instance_start_time_from"),
            instance_start_time_to=_optional_str(params, "instance_start_time_to"),
            locale=_optional_str(params, "locale"),
            page_size=_int_param(params, "page_size", 20),
            page_token=_optional_str(params, "page_token"),
            user_id_type=str(params.get("user_id_type") or "open_id"),
        )
    )
    data = _response_data(payload)
    items = data.get("instance_list") or data.get("instances") or data.get("items") or []
    return _summary("我发起的审批", list(items) if isinstance(items, list) else [], title_keys=("approval_name", "definition_name", "serial_number", "instance_code"))


def _execute_calendar_read(request: ToolRequest) -> str:
    params = request.params
    service = FeishuCalendarService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.list_primary_events(
            page_size=_int_param(params, "page_size", 50),
            page_token=params.get("page_token"),
        )
    )
    items = _items(_response_data(payload))
    return _summary("飞书日程", items, title_keys=("summary", "event_id"))


def _execute_okr_cycle_list(request: ToolRequest) -> str:
    params = request.params
    service = FeishuOkrService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.list_cycles(
            user_id=_required_str(params, "user_id"),
            user_id_type=str(params.get("user_id_type") or "open_id"),
            page_size=_int_param(params, "page_size", 100),
            page_token=_optional_str(params, "page_token"),
        )
    )
    items = _items(_response_data(payload))
    return _summary("飞书 OKR 周期", items, title_keys=("id", "tenant_cycle_id"))


def _execute_okr_objective_list(request: ToolRequest) -> str:
    params = request.params
    service = FeishuOkrService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.list_objectives(
            cycle_id=_required_str(params, "cycle_id"),
            user_id_type=str(params.get("user_id_type") or "open_id"),
            department_id_type=str(params.get("department_id_type") or "open_department_id"),
            page_size=_int_param(params, "page_size", 100),
            page_token=_optional_str(params, "page_token"),
        )
    )
    items = _items(_response_data(payload))
    return _summary("飞书 OKR 目标", items, title_keys=("content", "id"))


def _execute_contact_department_children(request: ToolRequest) -> str:
    params = request.params
    service = FeishuContactService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.list_child_departments(
            department_id=str(params.get("department_id") or "0"),
            department_id_type=str(params.get("department_id_type") or "department_id"),
            user_id_type=str(params.get("user_id_type") or "open_id"),
            fetch_child=_optional_bool(params, "fetch_child"),
            page_size=_int_param(params, "page_size", 50),
            page_token=_optional_str(params, "page_token"),
        )
    )
    items = _items(_response_data(payload))
    return _summary("飞书通讯录子部门", items, title_keys=("name", "department_id", "open_department_id"))


def _execute_contact_department_users(request: ToolRequest) -> str:
    params = request.params
    service = FeishuContactService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.list_users_by_department(
            department_id=str(params.get("department_id") or "0"),
            department_id_type=str(params.get("department_id_type") or "department_id"),
            user_id_type=str(params.get("user_id_type") or "open_id"),
            page_size=_int_param(params, "page_size", 50),
            page_token=_optional_str(params, "page_token"),
        )
    )
    items = _items(_response_data(payload))
    return _summary("飞书通讯录部门用户", items, title_keys=("name", "en_name", "open_id", "user_id"))


def _execute_contact_scope_list(request: ToolRequest) -> str:
    params = request.params
    service = FeishuContactService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.list_authorized_scopes(
            department_id_type=str(params.get("department_id_type") or "open_department_id"),
            user_id_type=str(params.get("user_id_type") or "open_id"),
            page_size=_int_param(params, "page_size", 100),
            page_token=_optional_str(params, "page_token"),
        )
    )
    data = _response_data(payload)
    department_count = len(data.get("department_ids") or [])
    user_count = len(data.get("user_ids") or [])
    group_count = len(data.get("group_ids") or [])
    return f"飞书通讯录授权范围已读取：部门 {department_count} 个，用户 {user_count} 个，用户组 {group_count} 个。"


def _execute_contact_organization_snapshot(request: ToolRequest) -> str:
    params = request.params
    service = FeishuContactService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.snapshot_organization(
            root_department_id=str(params.get("root_department_id") or params.get("department_id") or "0"),
            max_departments=_int_param(params, "max_departments", 100),
            max_users=_int_param(params, "max_users", 500),
        )
    )
    return (
        "飞书通讯录组织快照已读取："
        f"部门 {int(payload.get('department_count') or 0)} 个，人员 {int(payload.get('user_count') or 0)} 人。"
    )


def _execute_calendar_create_event(request: ToolRequest) -> str:
    params = request.params
    service = FeishuCalendarService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.create_event(
            calendar_id=str(params.get("calendar_id") or "primary"),
            summary=_required_str(params, "summary"),
            description=_optional_str(params, "description"),
            start_time=_calendar_time_param(params, "start_time", "start"),
            end_time=_calendar_time_param(params, "end_time", "end"),
            recurrence=_optional_str(params, "recurrence") or _optional_str(params, "rrule"),
            attendee_ids=_string_list_param(params, "attendee_ids"),
            attendees=_list_dict_param(params, "attendees"),
            user_id_type=str(params.get("user_id_type") or "open_id"),
            need_notification=_optional_bool(params, "need_notification") is not False,
        )
    )
    event = _response_data(payload).get("event")
    title = _item_title(event, ("summary", "event_id", "id")) if isinstance(event, dict) else None
    return f"飞书日程已创建：{title}" if title else "飞书日程已创建。"


def _execute_im_send_message(request: ToolRequest) -> str:
    params = request.params
    receive_id_type, receive_id = _im_receive_target(params)
    service = FeishuImService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.send_text_message(
            receive_id_type=receive_id_type,
            receive_id=receive_id,
            text=_required_str(params, "text"),
            idempotency_key=_optional_str(params, "idempotency_key") or _optional_str(params, "uuid"),
        )
    )
    data = _response_data(payload)
    message = data.get("message")
    message_id = data.get("message_id")
    if not message_id and isinstance(message, dict):
        message_id = message.get("message_id")
    return f"飞书消息已发送：{message_id}" if message_id else "飞书消息已发送。"


def _execute_im_create_chat(request: ToolRequest) -> str:
    params = request.params
    service = FeishuImService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.create_chat(
            name=_required_str(params, "name"),
            description=_optional_str(params, "description"),
            user_ids=_string_list_param(params, "user_id_list") or _string_list_param(params, "users"),
            bot_ids=_string_list_param(params, "bot_id_list") or _string_list_param(params, "bots"),
            user_id_type=_optional_str(params, "user_id_type") or "open_id",
            chat_mode=_optional_str(params, "chat_mode") or "group",
            chat_type=_optional_str(params, "chat_type") or "private",
        )
    )
    data = _response_data(payload)
    chat = _chat_payload(data)
    chat_id = _optional_str(chat, "chat_id") or _optional_str(chat, "open_chat_id") or _optional_str(chat, "chat_id_v2")
    return f"飞书群已创建：{chat_id}" if chat_id else f"飞书群已创建：{_required_str(params, 'name')}"


def _execute_im_auto_join_public_chats(request: ToolRequest) -> str:
    params = request.params
    service = FeishuImService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.auto_join_public_chats(
            query=_optional_str(params, "query"),
            chat_ids=_string_list_param(params, "chat_ids"),
            limit=_int_param(params, "limit", 20),
            max_pages=_int_param(params, "max_pages", 2),
            dry_run=False,
        )
    )
    joined_count = int(payload.get("joined_count") or len(payload.get("joined") or []))
    error_count = len(payload.get("errors") or [])
    if error_count:
        return f"飞书公开群加入已执行：成功 {joined_count} 个，失败 {error_count} 个。"
    return f"飞书公开群加入已执行：成功 {joined_count} 个。"


def _execute_im_chat_search(request: ToolRequest) -> str:
    params = request.params
    service = FeishuImService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.search_chats(
            query=_required_str(params, "query"),
            limit=_int_param(params, "page_size", _int_param(params, "limit", 20)),
            max_pages=_int_param(params, "max_pages", 2),
        )
    )
    items = payload.get("items") if isinstance(payload, dict) else []
    return _summary("飞书群聊", list(items) if isinstance(items, list) else [], title_keys=("name", "chat_name", "chat_id"))


def _execute_im_message_list(request: ToolRequest) -> str:
    params = request.params
    service = FeishuImService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.list_messages(
            chat_id=_required_str(params, "chat_id"),
            start_time=params.get("start_time") or params.get("start"),
            end_time=params.get("end_time") or params.get("end"),
            page_size=_int_param(params, "page_size", 20),
            page_token=params.get("page_token"),
        )
    )
    return _summary("飞书群聊消息", _items(_response_data(payload)), title_keys=("summary", "message_id", "id"))


def _execute_task_read(request: ToolRequest) -> str:
    params = request.params
    service = FeishuTaskService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.list_tasks(
            page_size=_int_param(params, "page_size", 50),
            page_token=params.get("page_token"),
        )
    )
    items = extract_task_items(_response_data(payload))
    return _summary("飞书任务", items, title_keys=("summary", "guid"))


def _execute_task_create(request: ToolRequest) -> str:
    params = request.params
    service = FeishuTaskService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.create_task(
            summary=_required_str(params, "summary"),
            description=_optional_str(params, "description"),
            due=_dict_param(params, "due"),
            members=_members_param(params),
            tasklists=_list_dict_param(params, "tasklists"),
            client_token=_optional_str(params, "client_token") or _optional_str(params, "idempotency_key"),
            user_id_type=str(params.get("user_id_type") or "open_id"),
            user_access_token=_optional_str(params, "user_access_token"),
        )
    )
    if str(params.get("response_format") or "").strip() == "raw_json":
        return json.dumps(payload, ensure_ascii=False)
    task = _response_data(payload).get("task")
    title = _item_title(task, ("summary", "guid", "task_id")) if isinstance(task, dict) else None
    return f"飞书任务已创建：{title}" if title else "飞书任务已创建。"


def _execute_task_subtask_create(request: ToolRequest) -> str:
    params = request.params
    parent_task_guid = _required_str_alias(params, "parent_task_guid", "parent_guid")
    service = FeishuTaskService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.create_subtask(
            parent_task_guid=parent_task_guid,
            summary=_required_str(params, "summary"),
            description=_optional_str(params, "description"),
            due=_dict_param(params, "due"),
            members=_members_param(params),
            tasklists=_list_dict_param(params, "tasklists"),
            client_token=_optional_str(params, "client_token") or _optional_str(params, "idempotency_key"),
            user_id_type=str(params.get("user_id_type") or "open_id"),
        )
    )
    subtask = _response_data(payload).get("subtask")
    title = _item_title(subtask, ("summary", "guid", "task_id")) if isinstance(subtask, dict) else None
    return f"飞书子任务已创建：{title}" if title else "飞书子任务已创建。"


def _execute_task_complete(request: ToolRequest) -> str:
    params = request.params
    service = FeishuTaskService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.complete_task(
            task_guid=_required_str(params, "task_guid"),
            completed_at=_completed_at_param(params),
            user_id_type=str(params.get("user_id_type") or "open_id"),
        )
    )
    task = _response_data(payload).get("task")
    title = _item_title(task, ("summary", "guid", "task_id")) if isinstance(task, dict) else None
    return f"飞书任务已完成：{title}" if title else "飞书任务已完成。"


def _execute_task_reopen(request: ToolRequest) -> str:
    params = request.params
    service = FeishuTaskService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.reopen_task(
            task_guid=_required_str(params, "task_guid"),
            user_id_type=str(params.get("user_id_type") or "open_id"),
        )
    )
    task = _response_data(payload).get("task")
    title = _item_title(task, ("summary", "guid", "task_id")) if isinstance(task, dict) else None
    return f"飞书任务已重新打开：{title}" if title else "飞书任务已重新打开。"


def _execute_task_update(request: ToolRequest) -> str:
    params = request.params
    task_payload = _task_update_payload(params)
    service = FeishuTaskService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.update_task(
            task_guid=_required_str(params, "task_guid"),
            task=task_payload,
            update_fields=_string_list_param(params, "update_fields") or list(task_payload.keys()),
            user_id_type=str(params.get("user_id_type") or "open_id"),
        )
    )
    task = _response_data(payload).get("task")
    title = _item_title(task, ("summary", "guid", "task_id")) if isinstance(task, dict) else None
    return f"飞书任务已更新：{title}" if title else "飞书任务已更新。"


def _execute_task_delete(request: ToolRequest) -> str:
    params = request.params
    task_guid = _required_str(params, "task_guid")
    service = FeishuTaskService(_app_config(params), client=params.get("client"))
    _run_async(service.delete_task(task_guid=task_guid))
    return f"飞书任务已删除：{task_guid}"


def _execute_task_update_reminders(request: ToolRequest) -> str:
    params = request.params
    positive_reminders = _positive_reminders_param(params)
    service = FeishuTaskService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.update_task(
            task_guid=_required_str(params, "task_guid"),
            task={"positive_reminders": positive_reminders},
            update_fields=["positive_reminders"],
            user_id_type=str(params.get("user_id_type") or "open_id"),
        )
    )
    task = _response_data(payload).get("task")
    title = _item_title(task, ("summary", "guid", "task_id")) if isinstance(task, dict) else None
    prefix = "飞书任务提醒已清空" if not positive_reminders else "飞书任务提醒已更新"
    return f"{prefix}：{title}" if title else f"{prefix}。"


def _execute_task_assign_members(request: ToolRequest) -> str:
    params = request.params
    add_assignees = _string_list_param(params, "add_assignees") or _string_list_param(params, "add")
    remove_assignees = _string_list_param(params, "remove_assignees") or _string_list_param(params, "remove")
    if not add_assignees and not remove_assignees:
        raise ValueError("Feishu API write tool requires add_assignees or remove_assignees.")
    service = FeishuTaskService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.assign_members(
            task_guid=_required_str(params, "task_guid"),
            add_assignees=add_assignees,
            remove_assignees=remove_assignees,
            client_token=_optional_str(params, "client_token") or _optional_str(params, "idempotency_key"),
            user_id_type=str(params.get("user_id_type") or "open_id"),
        )
    )
    return f"飞书任务负责人已更新：新增 {int(payload.get('added') or 0)} 个，移除 {int(payload.get('removed') or 0)} 个。"


def _execute_task_update_followers(request: ToolRequest) -> str:
    params = request.params
    add_followers = _string_list_param(params, "add_followers") or _string_list_param(params, "add")
    remove_followers = _string_list_param(params, "remove_followers") or _string_list_param(params, "remove")
    if not add_followers and not remove_followers:
        raise ValueError("Feishu API write tool requires add_followers or remove_followers.")
    service = FeishuTaskService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.update_followers(
            task_guid=_required_str(params, "task_guid"),
            add_followers=add_followers,
            remove_followers=remove_followers,
            client_token=_optional_str(params, "client_token") or _optional_str(params, "idempotency_key"),
            user_id_type=str(params.get("user_id_type") or "open_id"),
        )
    )
    return f"飞书任务关注人已更新：新增 {int(payload.get('added') or 0)} 个，移除 {int(payload.get('removed') or 0)} 个。"


def _execute_task_comment(request: ToolRequest) -> str:
    params = request.params
    service = FeishuTaskService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.add_comment(
            task_guid=_required_str(params, "task_guid"),
            content=_required_str(params, "content"),
            user_id_type=str(params.get("user_id_type") or "open_id"),
        )
    )
    comment = _response_data(payload).get("comment")
    title = _item_title(comment, ("comment_id", "id")) if isinstance(comment, dict) else None
    return f"飞书任务评论已添加：{title}" if title else "飞书任务评论已添加。"


def _execute_task_upload_attachment(request: ToolRequest) -> str:
    params = request.params
    resource_id = (
        _optional_str(params, "resource_id") or _optional_str(params, "task_guid") or _required_str(params, "task_id")
    )
    resource_type = str(params.get("resource_type") or "task")
    if resource_type not in {"task", "task_delivery"}:
        raise ValueError("Feishu task attachment resource_type must be task or task_delivery.")
    service = FeishuTaskService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.upload_attachment(
            resource_id=resource_id,
            file_path=_required_str_alias(params, "file_path", "file"),
            resource_type=resource_type,
            user_id_type=str(params.get("user_id_type") or "open_id"),
        )
    )
    attachment = _response_data(payload).get("attachment")
    title = (
        _item_title(attachment, ("file_name", "name", "guid", "attachment_guid"))
        if isinstance(attachment, dict)
        else None
    )
    return f"飞书任务附件已上传：{title}" if title else f"飞书任务附件已上传：{resource_id}"


def _execute_tasklist_create(request: ToolRequest) -> str:
    params = request.params
    service = FeishuTaskService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.create_tasklist(
            name=_required_str(params, "name"),
            members=_tasklist_members_param(params),
            archive_tasklist=_optional_bool(params, "archive_tasklist"),
            user_id_type=str(params.get("user_id_type") or "open_id"),
        )
    )
    tasklist = _response_data(payload).get("tasklist")
    title = _item_title(tasklist, ("name", "guid")) if isinstance(tasklist, dict) else None
    return f"飞书任务清单已创建：{title}" if title else "飞书任务清单已创建。"


def _execute_tasklist_delete(request: ToolRequest) -> str:
    params = request.params
    tasklist_guid = _required_str(params, "tasklist_guid")
    service = FeishuTaskService(_app_config(params), client=params.get("client"))
    _run_async(service.delete_tasklist(tasklist_guid=tasklist_guid))
    return f"飞书任务清单已删除：{tasklist_guid}"


def _execute_tasklist_update(request: ToolRequest) -> str:
    params = request.params
    name = _required_str(params, "name")
    service = FeishuTaskService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.update_tasklist(
            tasklist_guid=_required_str(params, "tasklist_guid"),
            name=name,
            user_id_type=str(params.get("user_id_type") or "open_id"),
        )
    )
    tasklist = _response_data(payload).get("tasklist")
    title = _item_title(tasklist, ("name", "guid")) if isinstance(tasklist, dict) else None
    return f"飞书任务清单已更新：{title or name}"


def _execute_task_section_create(request: ToolRequest) -> str:
    params = request.params
    data = _task_section_create_data(params)
    service = FeishuTaskService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.create_section(
            name=str(data["name"]),
            resource_type=str(data["resource_type"]),
            resource_id=data.get("resource_id"),
            insert_before=data.get("insert_before"),
            insert_after=data.get("insert_after"),
            user_id_type=str(params.get("user_id_type") or "open_id"),
        )
    )
    section = _response_data(payload).get("section")
    title = _item_title(section, ("name", "guid")) if isinstance(section, dict) else None
    return f"飞书任务分组已创建：{title or data['name']}"


def _execute_task_section_update(request: ToolRequest) -> str:
    params = request.params
    section_guid = _required_str_alias(params, "section_guid", "section_id")
    data = _task_section_update_data(params)
    service = FeishuTaskService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.update_section(
            section_guid=section_guid,
            section=data["section"],
            update_fields=data["update_fields"],
            user_id_type=str(params.get("user_id_type") or "open_id"),
        )
    )
    section = _response_data(payload).get("section")
    title = _item_title(section, ("name", "guid")) if isinstance(section, dict) else None
    return f"飞书任务分组已更新：{title or data['section'].get('name') or section_guid}"


def _execute_task_section_delete(request: ToolRequest) -> str:
    params = request.params
    section_guid = _required_str_alias(params, "section_guid", "section_id")
    service = FeishuTaskService(_app_config(params), client=params.get("client"))
    _run_async(service.delete_section(section_guid=section_guid))
    return f"飞书任务分组已删除：{section_guid}"


def _execute_task_add_to_tasklist(request: ToolRequest) -> str:
    params = request.params
    service = FeishuTaskService(_app_config(params), client=params.get("client"))
    _run_async(
        service.add_to_tasklist(
            task_guid=_required_str(params, "task_guid"),
            tasklist_guid=_required_str(params, "tasklist_guid"),
            section_guid=_optional_str(params, "section_guid"),
            user_id_type=str(params.get("user_id_type") or "open_id"),
        )
    )
    return f"飞书任务已加入清单：{_required_str(params, 'tasklist_guid')}"


def _execute_task_set_ancestor(request: ToolRequest) -> str:
    params = request.params
    ancestor_guid = _required_str_alias(params, "ancestor_guid", "ancestor_task_guid")
    service = FeishuTaskService(_app_config(params), client=params.get("client"))
    _run_async(
        service.set_ancestor(
            task_guid=_required_str(params, "task_guid"),
            ancestor_guid=ancestor_guid,
            user_id_type=str(params.get("user_id_type") or "open_id"),
        )
    )
    return f"飞书任务已设置父任务：{ancestor_guid}"


def _execute_task_clear_ancestor(request: ToolRequest) -> str:
    params = request.params
    service = FeishuTaskService(_app_config(params), client=params.get("client"))
    _run_async(
        service.clear_ancestor(
            task_guid=_required_str(params, "task_guid"),
            user_id_type=str(params.get("user_id_type") or "open_id"),
        )
    )
    return "飞书任务父任务关系已清空。"


def _execute_tasklist_update_members(request: ToolRequest) -> str:
    params = request.params
    add_members = _string_list_param(params, "add_members") or _string_list_param(params, "add")
    remove_members = _string_list_param(params, "remove_members") or _string_list_param(params, "remove")
    if not add_members and not remove_members:
        raise ValueError("Feishu API write tool requires add_members or remove_members.")
    service = FeishuTaskService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.update_tasklist_members(
            tasklist_guid=_required_str(params, "tasklist_guid"),
            add_members=add_members,
            remove_members=remove_members,
            user_id_type=str(params.get("user_id_type") or "open_id"),
        )
    )
    return f"飞书任务清单成员已更新：新增 {int(payload.get('added') or 0)} 个，移除 {int(payload.get('removed') or 0)} 个。"


def _execute_tasklist_set_members(request: ToolRequest) -> str:
    params = request.params
    set_members = _string_list_param(params, "set_members") or _string_list_param(params, "set")
    if not set_members:
        raise ValueError("Feishu API write tool requires non-empty set_members.")
    service = FeishuTaskService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.set_tasklist_members(
            tasklist_guid=_required_str(params, "tasklist_guid"),
            set_members=set_members,
            user_id_type=str(params.get("user_id_type") or "open_id"),
        )
    )
    return f"飞书任务清单成员已全量替换：新增 {int(payload.get('added') or 0)} 个，移除 {int(payload.get('removed') or 0)} 个。"


def _execute_bitable_read(request: ToolRequest) -> str:
    params = request.params
    app_token = _required_str(params, "app_token")
    service = FeishuBitableService(_app_config(params), client=params.get("client"))
    table_id = params.get("table_id")
    if table_id:
        payload = _run_async(
            service.list_records(
                app_token=app_token,
                table_id=str(table_id),
                page_size=_int_param(params, "page_size", 100),
                page_token=params.get("page_token"),
                view_id=params.get("view_id"),
                field_names=_string_list_param(params, "field_names"),
            )
        )
        items = _items(_response_data(payload))
        return _summary("飞书多维表格记录", items, title_keys=("record_id", "id"))
    payload = _run_async(
        service.list_tables(
            app_token=app_token,
            page_size=_int_param(params, "page_size", 100),
            page_token=params.get("page_token"),
        )
    )
    items = _items(_response_data(payload))
    return _summary("飞书多维表格数据表", items, title_keys=("name", "table_id"))


def _execute_bitable_field_list(request: ToolRequest) -> str:
    params = request.params
    service = FeishuBitableService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.list_fields(
            app_token=_required_str(params, "app_token"),
            table_id=_required_str(params, "table_id"),
            limit=_int_param(params, "limit", 100),
            offset=_int_param(params, "offset", 0),
        )
    )
    items = _items(_response_data(payload))
    return _summary("飞书多维表格字段", items, title_keys=("field_name", "name", "field_id"))


def _execute_bitable_view_get_visible_fields(request: ToolRequest) -> str:
    params = request.params
    service = FeishuBitableService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.get_view_visible_fields(
            app_token=_required_str(params, "app_token"),
            table_id=_required_str(params, "table_id"),
            view_id=_required_str(params, "view_id"),
        )
    )
    visible_fields = _response_data(payload).get("visible_fields")
    if isinstance(visible_fields, list):
        return f"飞书多维表格视图可见字段：{len(visible_fields)} 个"
    return "飞书多维表格视图可见字段已读取。"


def _execute_bitable_view_get_card(request: ToolRequest) -> str:
    params = request.params
    service = FeishuBitableService(_app_config(params), client=params.get("client"))
    _run_async(
        service.get_view_card(
            app_token=_required_str(params, "app_token"),
            table_id=_required_str(params, "table_id"),
            view_id=_required_str(params, "view_id"),
        )
    )
    return "飞书多维表格视图卡片配置已读取。"


def _execute_bitable_view_get_timebar(request: ToolRequest) -> str:
    params = request.params
    service = FeishuBitableService(_app_config(params), client=params.get("client"))
    _run_async(
        service.get_view_timebar(
            app_token=_required_str(params, "app_token"),
            table_id=_required_str(params, "table_id"),
            view_id=_required_str(params, "view_id"),
        )
    )
    return "飞书多维表格视图时间轴配置已读取。"


def _execute_bitable_table_create(request: ToolRequest) -> str:
    params = request.params
    service = FeishuBitableService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.create_table(
            app_token=_required_str(params, "app_token"),
            name=_required_str(params, "name"),
            fields=_list_dict_param(params, "fields"),
            view=_bitable_view_param(params),
        )
    )
    table = _response_data(payload).get("table")
    title = _item_title(table, ("name", "table_id")) if isinstance(table, dict) else None
    return f"飞书多维表格数据表已创建：{title}" if title else f"飞书多维表格数据表已创建：{_required_str(params, 'name')}"


def _execute_bitable_table_update(request: ToolRequest) -> str:
    params = request.params
    name = _required_str(params, "name")
    service = FeishuBitableService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.update_table(
            app_token=_required_str(params, "app_token"),
            table_id=_required_str(params, "table_id"),
            name=name,
        )
    )
    table = _response_data(payload).get("table")
    title = _item_title(table, ("name", "table_id")) if isinstance(table, dict) else None
    return f"飞书多维表格数据表已重命名：{title or name}"


def _execute_bitable_table_delete(request: ToolRequest) -> str:
    params = request.params
    table_id = _required_str(params, "table_id")
    service = FeishuBitableService(_app_config(params), client=params.get("client"))
    _run_async(service.delete_table(app_token=_required_str(params, "app_token"), table_id=table_id))
    return f"飞书多维表格数据表已删除：{table_id}"


def _execute_bitable_field_create(request: ToolRequest) -> str:
    params = request.params
    field = _bitable_field_param(params)
    service = FeishuBitableService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.create_field(
            app_token=_required_str(params, "app_token"),
            table_id=_required_str(params, "table_id"),
            field=field,
        )
    )
    item = _response_data(payload).get("field")
    title = _item_title(item, ("field_name", "name", "field_id")) if isinstance(item, dict) else None
    return f"飞书多维表格字段已创建：{title}" if title else f"飞书多维表格字段已创建：{field.get('name')}"


def _execute_bitable_field_delete(request: ToolRequest) -> str:
    params = request.params
    field_id = _required_str(params, "field_id")
    service = FeishuBitableService(_app_config(params), client=params.get("client"))
    _run_async(
        service.delete_field(
            app_token=_required_str(params, "app_token"),
            table_id=_required_str(params, "table_id"),
            field_id=field_id,
        )
    )
    return f"飞书多维表格字段已删除：{field_id}"


def _execute_bitable_field_update(request: ToolRequest) -> str:
    params = request.params
    field = _bitable_full_field_param(params)
    service = FeishuBitableService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.update_field(
            app_token=_required_str(params, "app_token"),
            table_id=_required_str(params, "table_id"),
            field_id=_required_str(params, "field_id"),
            field=field,
        )
    )
    item = _response_data(payload).get("field")
    title = _item_title(item, ("field_name", "name", "field_id")) if isinstance(item, dict) else None
    return f"飞书多维表格字段已更新：{title or field.get('name')}"


def _execute_bitable_view_create(request: ToolRequest) -> str:
    params = request.params
    view = _bitable_single_view_param(params)
    service = FeishuBitableService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.create_view(
            app_token=_required_str(params, "app_token"),
            table_id=_required_str(params, "table_id"),
            view=view,
        )
    )
    item = _response_data(payload).get("view")
    title = _item_title(item, ("view_name", "name", "view_id")) if isinstance(item, dict) else None
    return f"飞书多维表格视图已创建：{title or view.get('name')}"


def _execute_bitable_view_delete(request: ToolRequest) -> str:
    params = request.params
    view_id = _required_str(params, "view_id")
    service = FeishuBitableService(_app_config(params), client=params.get("client"))
    _run_async(
        service.delete_view(
            app_token=_required_str(params, "app_token"),
            table_id=_required_str(params, "table_id"),
            view_id=view_id,
        )
    )
    return f"飞书多维表格视图已删除：{view_id}"


def _execute_bitable_view_rename(request: ToolRequest) -> str:
    params = request.params
    name = _required_str(params, "name")
    service = FeishuBitableService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.rename_view(
            app_token=_required_str(params, "app_token"),
            table_id=_required_str(params, "table_id"),
            view_id=_required_str(params, "view_id"),
            name=name,
        )
    )
    item = _response_data(payload).get("view")
    title = _item_title(item, ("view_name", "name", "view_id")) if isinstance(item, dict) else None
    return f"飞书多维表格视图已重命名：{title or name}"


def _execute_bitable_view_set_filter(request: ToolRequest) -> str:
    params = request.params
    view_id = _required_str(params, "view_id")
    service = FeishuBitableService(_app_config(params), client=params.get("client"))
    _run_async(
        service.set_view_filter(
            app_token=_required_str(params, "app_token"),
            table_id=_required_str(params, "table_id"),
            view_id=view_id,
            filter_config=_bitable_view_filter_param(params),
        )
    )
    return f"飞书多维表格视图筛选已更新：{view_id}"


def _execute_bitable_view_set_sort(request: ToolRequest) -> str:
    params = request.params
    view_id = _required_str(params, "view_id")
    service = FeishuBitableService(_app_config(params), client=params.get("client"))
    _run_async(
        service.set_view_sort(
            app_token=_required_str(params, "app_token"),
            table_id=_required_str(params, "table_id"),
            view_id=view_id,
            sort_config=_bitable_view_sort_param(params),
        )
    )
    return f"飞书多维表格视图排序已更新：{view_id}"


def _execute_bitable_view_set_group(request: ToolRequest) -> str:
    params = request.params
    view_id = _required_str(params, "view_id")
    service = FeishuBitableService(_app_config(params), client=params.get("client"))
    _run_async(
        service.set_view_group(
            app_token=_required_str(params, "app_token"),
            table_id=_required_str(params, "table_id"),
            view_id=view_id,
            group_config=_bitable_view_group_param(params),
        )
    )
    return f"飞书多维表格视图分组已更新：{view_id}"


def _execute_bitable_view_set_visible_fields(request: ToolRequest) -> str:
    params = request.params
    view_id = _required_str(params, "view_id")
    visible_fields = _bitable_view_visible_fields_param(params)
    service = FeishuBitableService(_app_config(params), client=params.get("client"))
    _run_async(
        service.set_view_visible_fields(
            app_token=_required_str(params, "app_token"),
            table_id=_required_str(params, "table_id"),
            view_id=view_id,
            visible_fields=visible_fields,
        )
    )
    return f"飞书多维表格视图可见字段已更新：{view_id}"


def _execute_bitable_view_set_card(request: ToolRequest) -> str:
    params = request.params
    view_id = _required_str(params, "view_id")
    service = FeishuBitableService(_app_config(params), client=params.get("client"))
    _run_async(
        service.set_view_card(
            app_token=_required_str(params, "app_token"),
            table_id=_required_str(params, "table_id"),
            view_id=view_id,
            card=_bitable_view_card_param(params),
        )
    )
    return f"飞书多维表格视图卡片配置已更新：{view_id}"


def _execute_bitable_view_set_timebar(request: ToolRequest) -> str:
    params = request.params
    view_id = _required_str(params, "view_id")
    service = FeishuBitableService(_app_config(params), client=params.get("client"))
    _run_async(
        service.set_view_timebar(
            app_token=_required_str(params, "app_token"),
            table_id=_required_str(params, "table_id"),
            view_id=view_id,
            timebar=_bitable_view_timebar_param(params),
        )
    )
    return f"飞书多维表格视图时间轴配置已更新：{view_id}"


def _execute_bitable_record_create(request: ToolRequest) -> str:
    params = request.params
    service = FeishuBitableService(_app_config(params), client=params.get("client"))
    fields = _required_dict(params, "fields")
    _validate_bitable_write_fields(params, service, field_names=list(fields))
    payload = _run_async(
        service.create_record(
            app_token=_required_str(params, "app_token"),
            table_id=_required_str(params, "table_id"),
            fields=fields,
            user_id_type=str(params.get("user_id_type") or "open_id"),
            client_token=_optional_str(params, "client_token") or _optional_str(params, "idempotency_key"),
            ignore_consistency_check=_optional_bool(params, "ignore_consistency_check"),
        )
    )
    record = _response_data(payload).get("record")
    title = _item_title(record, ("record_id", "id")) if isinstance(record, dict) else None
    return f"飞书多维表格记录已创建：{title}" if title else "飞书多维表格记录已创建。"


def _execute_bitable_record_batch_create(request: ToolRequest) -> str:
    params = request.params
    fields = _required_str_list(params, "fields")
    rows = _required_rows(params)
    service = FeishuBitableService(_app_config(params), client=params.get("client"))
    _validate_bitable_write_fields(params, service, field_names=fields)
    payload = _run_async(
        service.batch_create_records(
            app_token=_required_str(params, "app_token"),
            table_id=_required_str(params, "table_id"),
            fields=fields,
            rows=rows,
        )
    )
    records = _response_data(payload).get("records")
    count = len(records) if isinstance(records, list) else len(rows)
    return f"飞书多维表格记录已批量创建：{count} 条"


def _execute_bitable_record_batch_update(request: ToolRequest) -> str:
    params = request.params
    record_id_list = _required_str_list(params, "record_id_list")
    patch = _required_dict(params, "patch")
    service = FeishuBitableService(_app_config(params), client=params.get("client"))
    _validate_bitable_write_fields(params, service, field_names=list(patch))
    payload = _run_async(
        service.batch_update_records(
            app_token=_required_str(params, "app_token"),
            table_id=_required_str(params, "table_id"),
            record_id_list=record_id_list,
            patch=patch,
        )
    )
    records = _response_data(payload).get("records")
    count = len(records) if isinstance(records, list) else len(record_id_list)
    return f"飞书多维表格记录已批量更新：{count} 条"


def _execute_bitable_record_batch_delete(request: ToolRequest) -> str:
    params = request.params
    record_id_list = _required_str_list(params, "record_id_list")
    service = FeishuBitableService(_app_config(params), client=params.get("client"))
    _run_async(
        service.batch_delete_records(
            app_token=_required_str(params, "app_token"),
            table_id=_required_str(params, "table_id"),
            record_id_list=record_id_list,
        )
    )
    return f"飞书多维表格记录已批量删除：{len(record_id_list)} 条"


def _execute_bitable_record_upsert(request: ToolRequest) -> str:
    params = request.params
    service = FeishuBitableService(_app_config(params), client=params.get("client"))
    fields = _required_dict(params, "fields")
    record_id = _optional_str(params, "record_id")
    _validate_bitable_write_fields(params, service, field_names=list(fields))
    payload = _run_async(
        service.upsert_record(
            app_token=_required_str(params, "app_token"),
            table_id=_required_str(params, "table_id"),
            record_id=record_id,
            fields=fields,
        )
    )
    record = _response_data(payload).get("record")
    title = _item_title(record, ("record_id", "id")) if isinstance(record, dict) else None
    if record_id:
        return f"飞书多维表格记录已按 record_id 更新：{title or record_id}"
    return f"飞书多维表格记录已创建：{title}" if title else "飞书多维表格记录已创建。"


def _execute_bitable_record_update(request: ToolRequest) -> str:
    params = request.params
    service = FeishuBitableService(_app_config(params), client=params.get("client"))
    fields = _required_dict(params, "fields")
    _validate_bitable_write_fields(params, service, field_names=list(fields))
    payload = _run_async(
        service.update_record(
            app_token=_required_str(params, "app_token"),
            table_id=_required_str(params, "table_id"),
            record_id=_required_str(params, "record_id"),
            fields=fields,
            user_id_type=str(params.get("user_id_type") or "open_id"),
            ignore_consistency_check=_optional_bool(params, "ignore_consistency_check"),
        )
    )
    record = _response_data(payload).get("record")
    title = _item_title(record, ("record_id", "id")) if isinstance(record, dict) else None
    return f"飞书多维表格记录已更新：{title}" if title else "飞书多维表格记录已更新。"


def _execute_bitable_record_upload_attachment(request: ToolRequest) -> str:
    params = request.params
    file_tokens = _required_str_list(params, "file_tokens")
    if len(file_tokens) > 50:
        raise ValueError("Feishu API write tool supports at most 50 file_tokens.")
    service = FeishuBitableService(_app_config(params), client=params.get("client"))
    record_id = _required_str(params, "record_id")
    _run_async(
        service.append_record_attachments(
            app_token=_required_str(params, "app_token"),
            table_id=_required_str(params, "table_id"),
            record_id=record_id,
            field_id=_required_str_alias(params, "field_id", "field_name"),
            file_tokens=file_tokens,
        )
    )
    return f"飞书多维表格记录附件已追加：{record_id}"


def _execute_bitable_record_remove_attachment(request: ToolRequest) -> str:
    params = request.params
    file_tokens = _required_str_list(params, "file_tokens")
    if len(file_tokens) > 50:
        raise ValueError("Feishu API write tool supports at most 50 file_tokens.")
    service = FeishuBitableService(_app_config(params), client=params.get("client"))
    record_id = _required_str(params, "record_id")
    _run_async(
        service.remove_record_attachments(
            app_token=_required_str(params, "app_token"),
            table_id=_required_str(params, "table_id"),
            record_id=record_id,
            field_id=_required_str_alias(params, "field_id", "field_name"),
            file_tokens=file_tokens,
        )
    )
    return f"飞书多维表格记录附件已移除：{record_id}"


def _execute_bitable_record_delete(request: ToolRequest) -> str:
    params = request.params
    service = FeishuBitableService(_app_config(params), client=params.get("client"))
    _run_async(
        service.delete_record(
            app_token=_required_str(params, "app_token"),
            table_id=_required_str(params, "table_id"),
            record_id=_required_str(params, "record_id"),
            user_id_type=str(params.get("user_id_type") or "open_id"),
        )
    )
    return f"飞书多维表格记录已删除：{_required_str(params, 'record_id')}"


def _execute_mail_read(request: ToolRequest) -> str:
    params = request.params
    service = FeishuMailService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.list_messages(
            user_mailbox_id=str(params.get("user_mailbox_id") or "me"),
            folder_id=str(params.get("folder_id") or "INBOX"),
            page_size=_int_param(params, "page_size", 20),
            page_token=params.get("page_token"),
        )
    )
    items = extract_mail_items(_response_data(payload))
    return _summary("飞书邮箱邮件", items, title_keys=("subject", "message_id", "id"))


def _execute_mail_folder_list(request: ToolRequest) -> str:
    params = request.params
    service = FeishuMailService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.list_folders(
            user_mailbox_id=str(params.get("user_mailbox_id") or params.get("mailbox") or "me"),
        )
    )
    data = _response_data(payload)
    items = extract_mail_items(data)
    if not items and isinstance(data.get("folders"), list):
        items = list(data["folders"])
    return _summary("飞书邮箱文件夹", items, title_keys=("name", "folder_id", "id"))


def _execute_mail_message_get(request: ToolRequest) -> str:
    params = request.params
    service = FeishuMailService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.get_message_detail(
            user_mailbox_id=str(params.get("user_mailbox_id") or params.get("mailbox") or "me"),
            message_id=_required_str(params, "message_id"),
            fmt=str(params.get("format") or params.get("fmt") or "plain_text_full"),
        )
    )
    data = _response_data(payload)
    title = data.get("subject") or data.get("message_id") or params.get("message_id")
    return f"飞书邮箱邮件详情已读取：{title}"


def _execute_vc_meeting_search(request: ToolRequest) -> str:
    params = request.params
    service = FeishuMeetingService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.list_meetings(
            start_time=params.get("start_time") or params.get("start"),
            end_time=params.get("end_time") or params.get("end"),
            page_size=_int_param(params, "page_size", 20),
            page_token=params.get("page_token"),
            user_id_type=str(params.get("user_id_type") or "open_id"),
        )
    )
    return _summary("飞书历史会议", _items(_response_data(payload)), title_keys=("topic", "subject", "title", "id"))


def _execute_wiki_space_list(request: ToolRequest) -> str:
    params = request.params
    service = FeishuDriveService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.list_wiki_spaces(
            page_size=_int_param(params, "page_size", 20),
            page_token=params.get("page_token"),
        )
    )
    return _summary("飞书知识空间", _items(_response_data(payload)), title_keys=("name", "space_id", "id"))


def _execute_wiki_node_list(request: ToolRequest) -> str:
    params = request.params
    service = FeishuDriveService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.list_wiki_nodes(
            space_id=_required_str(params, "space_id"),
            parent_node_token=params.get("parent_node_token"),
            page_size=_int_param(params, "page_size", 50),
            page_token=params.get("page_token"),
        )
    )
    return _summary("飞书知识库节点", _items(_response_data(payload)), title_keys=("title", "node_token", "obj_token"))


def _execute_drive_file_list(request: ToolRequest) -> str:
    params = request.params
    service = FeishuDriveService(_app_config(params), client=params.get("client"))
    payload = _run_async(
        service.list_files(
            page_size=_int_param(params, "page_size", 50),
            page_token=params.get("page_token"),
            folder_token=params.get("folder_token"),
        )
    )
    data = _response_data(payload)
    files = data.get("files") or data.get("items") or []
    return _summary("飞书云空间文件", list(files) if isinstance(files, list) else [], title_keys=("name", "token", "type"))


FEISHU_API_READ_BINDINGS: dict[str, ReadToolHandler] = {
    "bitable_qa": _execute_bitable_read,
    "calendar_qa": _execute_calendar_read,
    "feishu_bitable_field_list": _execute_bitable_field_list,
    "feishu_bitable_view_get_card": _execute_bitable_view_get_card,
    "feishu_bitable_view_get_timebar": _execute_bitable_view_get_timebar,
    "feishu_bitable_view_get_visible_fields": _execute_bitable_view_get_visible_fields,
    "feishu_approval_instance_get": _execute_approval_instance_get,
    "feishu_approval_instance_initiated": _execute_approval_instance_initiated,
    "feishu_approval_task_query": _execute_approval_task_query,
    "feishu_drive_file_list": _execute_drive_file_list,
    "feishu_im_chat_search": _execute_im_chat_search,
    "feishu_im_message_list": _execute_im_message_list,
    "feishu_contact_department_children": _execute_contact_department_children,
    "feishu_contact_department_users": _execute_contact_department_users,
    "feishu_contact_scope_list": _execute_contact_scope_list,
    "feishu_contact_organization_snapshot": _execute_contact_organization_snapshot,
    "feishu_mail_folder_list": _execute_mail_folder_list,
    "feishu_mail_message_get": _execute_mail_message_get,
    "feishu_okr_cycle_list": _execute_okr_cycle_list,
    "feishu_okr_objective_list": _execute_okr_objective_list,
    "feishu_vc_meeting_search": _execute_vc_meeting_search,
    "feishu_wiki_space_list": _execute_wiki_space_list,
    "feishu_wiki_node_list": _execute_wiki_node_list,
    "mail_qa": _execute_mail_read,
    "task_qa": _execute_task_read,
}


FEISHU_API_WRITE_BINDINGS: dict[str, WriteToolHandler] = {
    "feishu_approval_instance_cancel": lambda context, request: _execute_approval_instance_cancel(context, request),
    "feishu_approval_instance_cc": lambda context, request: _execute_approval_instance_cc(context, request),
    "feishu_approval_instance_remind": lambda context, request: _execute_approval_instance_remind(context, request),
    "feishu_approval_task_add_sign": lambda context, request: _execute_approval_task_add_sign(context, request),
    "feishu_approval_task_approve": lambda context, request: _execute_approval_task_action(
        context, request, action="approve"
    ),
    "feishu_approval_task_reject": lambda context, request: _execute_approval_task_action(
        context, request, action="reject"
    ),
    "feishu_approval_task_rollback": lambda context, request: _execute_approval_task_rollback(context, request),
    "feishu_approval_task_transfer": lambda context, request: _execute_approval_task_transfer(context, request),
    "feishu_bitable_record_create": lambda _context, request: _execute_bitable_record_create(request),
    "feishu_bitable_record_batch_create": lambda _context, request: _execute_bitable_record_batch_create(request),
    "feishu_bitable_record_batch_delete": lambda _context, request: _execute_bitable_record_batch_delete(request),
    "feishu_bitable_record_batch_update": lambda _context, request: _execute_bitable_record_batch_update(request),
    "feishu_bitable_record_delete": lambda _context, request: _execute_bitable_record_delete(request),
    "feishu_bitable_record_remove_attachment": lambda _context, request: _execute_bitable_record_remove_attachment(
        request
    ),
    "feishu_bitable_record_upsert": lambda _context, request: _execute_bitable_record_upsert(request),
    "feishu_bitable_record_update": lambda _context, request: _execute_bitable_record_update(request),
    "feishu_bitable_record_upload_attachment": lambda _context, request: _execute_bitable_record_upload_attachment(
        request
    ),
    "feishu_bitable_table_create": lambda _context, request: _execute_bitable_table_create(request),
    "feishu_bitable_table_delete": lambda _context, request: _execute_bitable_table_delete(request),
    "feishu_bitable_table_update": lambda _context, request: _execute_bitable_table_update(request),
    "feishu_bitable_field_create": lambda _context, request: _execute_bitable_field_create(request),
    "feishu_bitable_field_delete": lambda _context, request: _execute_bitable_field_delete(request),
    "feishu_bitable_field_update": lambda _context, request: _execute_bitable_field_update(request),
    "feishu_bitable_view_create": lambda _context, request: _execute_bitable_view_create(request),
    "feishu_bitable_view_delete": lambda _context, request: _execute_bitable_view_delete(request),
    "feishu_bitable_view_rename": lambda _context, request: _execute_bitable_view_rename(request),
    "feishu_bitable_view_set_filter": lambda _context, request: _execute_bitable_view_set_filter(request),
    "feishu_bitable_view_set_group": lambda _context, request: _execute_bitable_view_set_group(request),
    "feishu_bitable_view_set_sort": lambda _context, request: _execute_bitable_view_set_sort(request),
    "feishu_bitable_view_set_card": lambda _context, request: _execute_bitable_view_set_card(request),
    "feishu_bitable_view_set_timebar": lambda _context, request: _execute_bitable_view_set_timebar(request),
    "feishu_bitable_view_set_visible_fields": lambda _context, request: _execute_bitable_view_set_visible_fields(
        request
    ),
    "feishu_calendar_create_event": lambda _context, request: _execute_calendar_create_event(request),
    "feishu_im_auto_join_public_chats": lambda _context, request: _execute_im_auto_join_public_chats(request),
    "feishu_im_create_chat": lambda _context, request: _execute_im_create_chat(request),
    "feishu_im_send_message": lambda _context, request: _execute_im_send_message(request),
    "feishu_task_comment": lambda _context, request: _execute_task_comment(request),
    "feishu_task_clear_ancestor": lambda _context, request: _execute_task_clear_ancestor(request),
    "feishu_task_complete": lambda _context, request: _execute_task_complete(request),
    "feishu_task_create": lambda _context, request: _execute_task_create(request),
    "feishu_task_subtask_create": lambda _context, request: _execute_task_subtask_create(request),
    "feishu_task_delete": lambda _context, request: _execute_task_delete(request),
    "feishu_task_assign_members": lambda _context, request: _execute_task_assign_members(request),
    "feishu_task_reopen": lambda _context, request: _execute_task_reopen(request),
    "feishu_task_update_followers": lambda _context, request: _execute_task_update_followers(request),
    "feishu_task_update_reminders": lambda _context, request: _execute_task_update_reminders(request),
    "feishu_task_update": lambda _context, request: _execute_task_update(request),
    "feishu_task_upload_attachment": lambda _context, request: _execute_task_upload_attachment(request),
    "feishu_tasklist_create": lambda _context, request: _execute_tasklist_create(request),
    "feishu_tasklist_delete": lambda _context, request: _execute_tasklist_delete(request),
    "feishu_tasklist_update": lambda _context, request: _execute_tasklist_update(request),
    "feishu_tasklist_set_members": lambda _context, request: _execute_tasklist_set_members(request),
    "feishu_tasklist_update_members": lambda _context, request: _execute_tasklist_update_members(request),
    "feishu_task_section_create": lambda _context, request: _execute_task_section_create(request),
    "feishu_task_section_delete": lambda _context, request: _execute_task_section_delete(request),
    "feishu_task_section_update": lambda _context, request: _execute_task_section_update(request),
    "feishu_task_add_to_tasklist": lambda _context, request: _execute_task_add_to_tasklist(request),
    "feishu_task_set_ancestor": lambda _context, request: _execute_task_set_ancestor(request),
}


def _app_config(params: dict[str, Any]) -> Any:
    app_config = params.get("app_config")
    if app_config is None and params.get("client") is None:
        raise ValueError("Feishu API read tool requires app_config or client.")
    return app_config


def _approval_item_params(params: dict[str, Any]) -> dict[str, Any]:
    item = params.get("item")
    if isinstance(item, dict):
        merged = dict(item)
    else:
        merged = {}
    for source, target in (
        ("approval_code", "approval_code"),
        ("definition_code", "definition_code"),
        ("instance_code", "instance_code"),
        ("process_code", "process_code"),
        ("task_id", "task_id"),
    ):
        if params.get(source):
            merged[target] = params[source]
    return merged


def _form_param(params: dict[str, Any]) -> str | None:
    form = params.get("form")
    if form is None:
        return None
    if isinstance(form, str):
        return form
    return json.dumps(form, ensure_ascii=False, default=str)


def _approval_actor_open_id(context: ToolContext | None, params: dict[str, Any]) -> str:
    explicit = params.get("open_id") or params.get("user_id")
    if explicit:
        return str(explicit).strip()
    if context is not None and context.actor.open_id:
        return context.actor.open_id.strip()
    return ""


def _approval_user_id_type(params: dict[str, Any]) -> str:
    user_id_type = str(params.get("user_id_type") or "open_id").strip()
    if user_id_type not in {"open_id", "user_id", "union_id"}:
        raise ValueError("Feishu approval user_id_type must be one of open_id, user_id, union_id.")
    return user_id_type


def _approval_task_ids(params: dict[str, Any], item: dict[str, Any]) -> list[str]:
    task_ids = _string_list_param(params, "task_ids")
    if task_ids:
        return task_ids
    item_task_ids = item.get("task_ids")
    if isinstance(item_task_ids, list):
        task_ids = [str(task_id).strip() for task_id in item_task_ids if str(task_id).strip()]
        if task_ids:
            return task_ids
    task_id = str(item.get("task_id") or params.get("task_id") or "").strip()
    if task_id:
        return [task_id]
    raise ValueError("Feishu API write tool requires task_ids.")


async def _approval_user_access_token(
    context: ToolContext | None,
    params: dict[str, Any],
    *,
    open_id: str,
    service: FeishuApprovalService,
) -> str | None:
    explicit = _optional_str(params, "user_access_token")
    if explicit:
        return explicit
    app_config = params.get("app_config")
    db = getattr(context, "db", None) if context is not None else None
    if db is None or app_config is None or not open_id:
        return None
    accounts = db.scalars(
        select(Account)
        .where(Account.company_id == app_config.company_id)
        .where(Account.provider == "feishu_user")
        .where(Account.is_active.is_(True))
    ).all()
    app_config_id = str(getattr(app_config, "id", "") or "")
    for account in accounts:
        settings = account.settings or {}
        if str(settings.get("open_id") or "") != open_id:
            continue
        if app_config_id and str(settings.get("feishu_app_config_id") or "") not in {"", app_config_id}:
            continue
        token, _error = await _user_access_token(db, account=account, app_config=app_config, client=service.client)
        return token
    return None


def _members_param(params: dict[str, Any]) -> list[dict[str, Any]] | None:
    members = _list_dict_param(params, "members")
    if members:
        return members
    assignees = _string_list_param(params, "assignees") or _string_list_param(params, "assignee")
    followers = _string_list_param(params, "followers") or _string_list_param(params, "follower")
    generated = [
        {"id": member_id, "type": "user", "role": "assignee"}
        for member_id in assignees or []
    ]
    generated.extend(
        {"id": member_id, "type": "user", "role": "follower"}
        for member_id in followers or []
    )
    return generated or None


def _tasklist_members_param(params: dict[str, Any]) -> list[dict[str, Any]] | None:
    members = _list_dict_param(params, "members")
    if members:
        return members
    editors = _string_list_param(params, "editors") or _string_list_param(params, "member_ids")
    return [{"id": member_id, "type": "user", "role": "editor"} for member_id in editors or []] or None


def _task_section_create_data(params: dict[str, Any]) -> dict[str, str]:
    name = _required_str(params, "name")
    resource_type = str(params.get("resource_type") or "tasklist").strip()
    if resource_type not in {"tasklist", "my_tasks"}:
        raise ValueError("Feishu task section resource_type must be tasklist or my_tasks.")
    _ensure_single_section_insert(params)
    data = {"name": name, "resource_type": resource_type}
    if resource_type == "tasklist":
        data["resource_id"] = _required_str_alias(params, "resource_id", "tasklist_guid")
    elif params.get("resource_id") is not None:
        data["resource_id"] = _required_str(params, "resource_id")
    for key in ("insert_before", "insert_after"):
        value = str(params.get(key) or "").strip()
        if value:
            data[key] = value
    return data


def _task_section_update_data(params: dict[str, Any]) -> dict[str, Any]:
    _ensure_single_section_insert(params)
    section: dict[str, str] = {}
    for key in ("name", "insert_before", "insert_after"):
        value = str(params.get(key) or "").strip()
        if value:
            section[key] = value
    if not section:
        raise ValueError("Feishu task section update requires name, insert_before, or insert_after.")
    update_fields = _string_list_param(params, "update_fields") or list(section)
    unknown = [field for field in update_fields if field not in {"name", "insert_before", "insert_after"}]
    if unknown:
        raise ValueError(f"Feishu task section update does not support update_fields: {', '.join(unknown)}")
    missing = [field for field in update_fields if field not in section]
    if missing:
        raise ValueError(f"Feishu task section update missing values for update_fields: {', '.join(missing)}")
    return {"section": section, "update_fields": update_fields}


def _ensure_single_section_insert(params: dict[str, Any]) -> None:
    if str(params.get("insert_before") or "").strip() and str(params.get("insert_after") or "").strip():
        raise ValueError("Feishu task section supports only one of insert_before or insert_after.")


def _task_update_payload(params: dict[str, Any]) -> dict[str, Any]:
    value = params.get("task")
    task = dict(value) if isinstance(value, dict) else {}
    for key in ("summary", "description", "due", "start", "completed_at"):
        if params.get(key) is not None:
            task[key] = params[key]
    if not task:
        raise ValueError("Feishu API write tool requires task.")
    return task


def _positive_reminders_param(params: dict[str, Any]) -> list[dict[str, int]]:
    value = params.get("positive_reminders")
    if isinstance(value, list):
        reminders: list[dict[str, int]] = []
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("Feishu API write tool requires positive_reminders as reminder objects.")
            reminders.append(
                {"relative_fire_minute": _positive_relative_fire_minute(item.get("relative_fire_minute"))}
            )
        return reminders
    minutes = params.get("relative_fire_minutes")
    if minutes is None:
        minutes = params.get("relative_fire_minute")
    if minutes is None:
        raise ValueError("Feishu API write tool requires positive_reminders or relative_fire_minutes.")
    if isinstance(minutes, list):
        return [{"relative_fire_minute": _positive_relative_fire_minute(item)} for item in minutes]
    return [{"relative_fire_minute": _positive_relative_fire_minute(minutes)}]


def _positive_relative_fire_minute(value: Any) -> int:
    try:
        minute = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Feishu task reminder relative_fire_minute must be an integer.") from exc
    if minute < 0:
        raise ValueError("Feishu task reminder relative_fire_minute must be greater than or equal to 0.")
    return minute


def _bitable_field_param(params: dict[str, Any]) -> dict[str, Any]:
    field = _dict_param(params, "field") or _dict_param(params, "field_property")
    if field:
        return field
    generated: dict[str, Any] = {}
    for key in ("name", "type", "description", "multiple", "options", "property"):
        if params.get(key) is not None:
            generated[key] = params[key]
    if not generated:
        raise ValueError("Feishu API write tool requires field.")
    if not generated.get("name") or not generated.get("type"):
        raise ValueError("Feishu API write tool requires field.name and field.type.")
    return generated


def _bitable_full_field_param(params: dict[str, Any]) -> dict[str, Any]:
    field = _bitable_field_param(params)
    if not field.get("name") or not field.get("type"):
        raise ValueError("Feishu Bitable field update requires full field.name and field.type.")
    field_type = str(field.get("type") or "").strip().lower()
    if field_type in {"formula", "lookup"}:
        raise ValueError("Feishu Bitable formula/lookup field update is not enabled in Tool Router yet.")
    return field


def _bitable_view_param(params: dict[str, Any]) -> dict[str, Any] | list[dict[str, Any]] | None:
    value = params.get("view")
    if isinstance(value, dict):
        return value
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return value
    return None


def _bitable_single_view_param(params: dict[str, Any]) -> dict[str, Any]:
    view = _dict_param(params, "view") or {}
    if not view:
        view = {}
        for key in ("name", "type"):
            if params.get(key) is not None:
                view[key] = params[key]
    if not view:
        raise ValueError("Feishu API write tool requires view.")
    name = str(view.get("name") or "").strip()
    if not name:
        raise ValueError("Feishu Bitable view create requires view.name.")
    view_type = str(view.get("type") or "grid").strip().lower()
    if view_type not in BITABLE_VIEW_TYPES:
        raise ValueError("Feishu Bitable view type must be one of calendar, gantt, gallery, grid, kanban.")
    normalized = dict(view)
    normalized["name"] = name
    normalized["type"] = view_type
    return normalized


def _bitable_view_filter_param(params: dict[str, Any]) -> dict[str, Any]:
    value = params.get("filter") or params.get("filter_config")
    if not isinstance(value, dict):
        raise ValueError("Feishu Bitable view set filter requires filter object.")
    logic = str(value.get("logic") or "").strip().lower()
    if logic not in {"and", "or"}:
        raise ValueError("Feishu Bitable view set filter requires filter.logic to be and or or.")
    conditions = value.get("conditions")
    if not isinstance(conditions, list):
        raise ValueError("Feishu Bitable view set filter requires filter.conditions list.")
    normalized = dict(value)
    normalized["logic"] = logic
    normalized["conditions"] = conditions
    return normalized


def _bitable_view_sort_param(params: dict[str, Any]) -> dict[str, Any]:
    value = params.get("sort") or params.get("sort_config")
    if not isinstance(value, dict):
        raise ValueError("Feishu Bitable view set sort requires sort object.")
    sort_config = value.get("sort_config")
    if not isinstance(sort_config, list):
        raise ValueError("Feishu Bitable view set sort requires sort.sort_config list.")
    if len(sort_config) > 10:
        raise ValueError("Feishu Bitable view set sort supports at most 10 sort items.")
    return {"sort_config": [dict(item) if isinstance(item, dict) else item for item in sort_config]}


def _bitable_view_group_param(params: dict[str, Any]) -> dict[str, Any]:
    value = params.get("group") or params.get("group_config")
    if not isinstance(value, dict):
        raise ValueError("Feishu Bitable view set group requires group object.")
    group_config = value.get("group_config")
    if not isinstance(group_config, list):
        raise ValueError("Feishu Bitable view set group requires group.group_config list.")
    if len(group_config) > 3:
        raise ValueError("Feishu Bitable view set group supports at most 3 group items.")
    return {"group_config": [dict(item) if isinstance(item, dict) else item for item in group_config]}


def _bitable_view_visible_fields_param(params: dict[str, Any]) -> list[str]:
    value = params.get("visible_fields_config") or params.get("visible_fields")
    fields = value.get("visible_fields") if isinstance(value, dict) else value
    visible_fields = _string_list_param({"visible_fields": fields}, "visible_fields")
    if not visible_fields:
        raise ValueError("Feishu Bitable view set visible fields requires visible_fields.")
    if len(visible_fields) > 200:
        raise ValueError("Feishu Bitable view set visible fields supports at most 200 visible fields.")
    return visible_fields


def _bitable_view_card_param(params: dict[str, Any]) -> dict[str, Any]:
    value = params.get("card") or params.get("card_config")
    if isinstance(value, dict):
        if "cover_field" not in value:
            raise ValueError("Feishu Bitable view set card requires card.cover_field.")
        return {"cover_field": value.get("cover_field")}
    if "cover_field" in params and params.get("cover_field") is not None:
        return {"cover_field": str(params["cover_field"]).strip()}
    raise ValueError("Feishu Bitable view set card requires card object.")


def _bitable_view_timebar_param(params: dict[str, Any]) -> dict[str, str]:
    value = params.get("timebar") or params.get("timebar_config")
    payload = dict(value) if isinstance(value, dict) else {}
    for key in ("start_time", "end_time", "title"):
        if params.get(key) is not None:
            payload[key] = params[key]
    missing = [key for key in ("start_time", "end_time", "title") if not str(payload.get(key) or "").strip()]
    if missing:
        raise ValueError(f"Feishu Bitable view set timebar requires {', '.join(missing)}.")
    return {key: str(payload[key]).strip() for key in ("start_time", "end_time", "title")}


def _validate_bitable_write_fields(
    params: dict[str, Any],
    service: FeishuBitableService,
    *,
    field_names: list[str],
) -> None:
    if _optional_bool(params, "validate_fields") is not True:
        return
    fields = _run_async(
        service.list_fields(
            app_token=_required_str(params, "app_token"),
            table_id=_required_str(params, "table_id"),
            limit=200,
            offset=0,
        )
    )
    field_map = _bitable_field_map(_items(_response_data(fields)))
    missing = [name for name in field_names if name not in field_map]
    if missing:
        raise ValueError("Feishu Bitable write field validation failed: unknown fields " + ", ".join(missing))
    readonly = [name for name in field_names if _is_bitable_readonly_field(field_map[name])]
    if readonly:
        raise ValueError("Feishu Bitable write field validation failed: readonly fields " + ", ".join(readonly))


def _bitable_field_map(items: list[Any]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in ("field_name", "name", "field_id", "id"):
            value = item.get(key)
            if value:
                mapping[str(value)] = item
    return mapping


def _is_bitable_readonly_field(field: dict[str, Any]) -> bool:
    if field.get("is_readonly") is True or field.get("readonly") is True:
        return True
    property_value = field.get("property")
    if isinstance(property_value, dict) and (
        property_value.get("is_readonly") is True or property_value.get("readonly") is True
    ):
        return True
    field_type = str(field.get("type") or field.get("field_type") or "").strip().lower()
    return field_type in BITABLE_READONLY_FIELD_TYPES


def _default_approval_comment(action: str) -> str:
    return "由数字参谋根据用户在飞书中的二次确认提交同意。" if action == "approve" else "由数字参谋根据用户在飞书中的二次确认提交拒绝。"


def _required_str(params: dict[str, Any], key: str) -> str:
    value = params.get(key)
    if not value:
        raise ValueError(f"Feishu API read tool requires {key}.")
    return str(value)


def _required_str_alias(params: dict[str, Any], key: str, alias: str) -> str:
    value = params.get(key) or params.get(alias)
    if not value:
        raise ValueError(f"Feishu API write tool requires {key}.")
    return str(value)


def _required_dict(params: dict[str, Any], key: str) -> dict[str, Any]:
    value = params.get(key)
    if not isinstance(value, dict) or not value:
        raise ValueError(f"Feishu API write tool requires {key}.")
    return value


def _required_str_list(params: dict[str, Any], key: str) -> list[str]:
    items = _string_list_param(params, key)
    if not items:
        raise ValueError(f"Feishu API write tool requires {key}.")
    return items


def _required_rows(params: dict[str, Any]) -> list[list[Any]]:
    value = params.get("rows")
    if not isinstance(value, list) or not value:
        raise ValueError("Feishu API write tool requires rows.")
    rows = [row for row in value if isinstance(row, list)]
    if len(rows) != len(value) or not rows:
        raise ValueError("Feishu API write tool requires rows as a list of row arrays.")
    return rows


def _int_param(params: dict[str, Any], key: str, default: int) -> int:
    value = params.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bounded_int_param(params: dict[str, Any], key: str, *, minimum: int, maximum: int) -> int:
    value = params.get(key)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Feishu API write tool requires integer {key}.") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"Feishu API write tool requires {key} between {minimum} and {maximum}.")
    return parsed


def _optional_bounded_int_param(params: dict[str, Any], key: str, *, minimum: int, maximum: int) -> int | None:
    if params.get(key) is None:
        return None
    return _bounded_int_param(params, key, minimum=minimum, maximum=maximum)


def _optional_str(params: dict[str, Any], key: str) -> str | None:
    value = params.get(key)
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _optional_bool(params: dict[str, Any], key: str) -> bool | None:
    value = params.get(key)
    return value if isinstance(value, bool) else None


def _completed_at_param(params: dict[str, Any]) -> str:
    explicit = _optional_str(params, "completed_at")
    if explicit:
        return explicit
    return str(int(datetime.now(UTC).timestamp() * 1000))


def _calendar_time_param(params: dict[str, Any], key: str, alias: str) -> dict[str, Any]:
    value = params.get(key) or params.get(alias)
    if isinstance(value, dict) and value:
        return value
    if value is None:
        raise ValueError(f"Feishu API write tool requires {key}.")
    if isinstance(value, (int, float)):
        return {"timestamp": str(int(value))}
    text = str(value).strip()
    if text.isdigit():
        return {"timestamp": text}
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Feishu API write tool requires ISO datetime or timestamp for {key}.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return {"timestamp": str(int(parsed.timestamp()))}


def _im_receive_target(params: dict[str, Any]) -> tuple[str, str]:
    chat_id = _optional_str(params, "chat_id")
    if chat_id:
        return "chat_id", chat_id
    user_id = _optional_str(params, "user_id") or _optional_str(params, "open_id")
    if user_id:
        return "open_id", user_id
    receive_id = _optional_str(params, "receive_id")
    receive_id_type = _optional_str(params, "receive_id_type")
    if receive_id and receive_id_type:
        return receive_id_type, receive_id
    raise ValueError("Feishu API write tool requires chat_id or user_id.")


def _dict_param(params: dict[str, Any], key: str) -> dict[str, Any] | None:
    value = params.get(key)
    return value if isinstance(value, dict) else None


def _list_dict_param(params: dict[str, Any], key: str) -> list[dict[str, Any]] | None:
    value = params.get(key)
    if not isinstance(value, list):
        return None
    items = [item for item in value if isinstance(item, dict)]
    return items or None


def _string_list_param(params: dict[str, Any], key: str) -> list[str] | None:
    value = params.get(key)
    if value is None:
        return None
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return None


def _response_data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, dict) else {}


def _chat_payload(data: dict[str, Any]) -> dict[str, Any]:
    for key in ("chat", "item"):
        value = data.get(key)
        if isinstance(value, dict):
            return value
    return data


def _items(data: dict[str, Any]) -> list[Any]:
    value = data.get("items") or data.get("events") or data.get("records") or []
    return list(value) if isinstance(value, list) else []


def _summary(label: str, items: list[Any], *, title_keys: tuple[str, ...]) -> str:
    titles = [_item_title(item, title_keys) for item in items[:3]]
    titles = [title for title in titles if title]
    if titles:
        return f"{label}已读取 {len(items)} 条：{', '.join(titles)}"
    return f"{label}已读取 {len(items)} 条。"


def _item_title(item: Any, title_keys: tuple[str, ...]) -> str | None:
    if not isinstance(item, dict):
        return str(item) if item else None
    for key in title_keys:
        value = item.get(key)
        if value:
            return str(value)
    fields = item.get("fields")
    if isinstance(fields, dict):
        for value in fields.values():
            if isinstance(value, str) and value:
                return value
    return None


def _run_async(coro: Coroutine[Any, Any, dict[str, Any]]) -> dict[str, Any]:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(coro)).result()
