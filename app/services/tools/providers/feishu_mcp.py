from concurrent.futures import ThreadPoolExecutor, as_completed
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import httpx
from pathlib import Path
import tempfile
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.models.entities import Account, FeishuAppConfig
from app.services.tools.base import ToolContext, ToolRequest
from app.services.tools.providers.lark_cli import LARK_CLI_EXECUTION_CONTRACT
from app.services.tools.providers.lark_cli import run_lark_cli_json as _run_lark_cli_json_base
from app.services.tools.providers.lark_cli import run_lark_cli_json_loose as _run_lark_cli_json_loose_base
from app.services.tools.providers.lark_cli import run_lark_cli_text as _run_lark_cli_text_base
from sqlalchemy import select


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
_CURRENT_CLI_PROFILE: ContextVar[str | None] = ContextVar("feishu_mcp_cli_profile", default=None)
_TENANT_CONTACT_TOOL_NAMES = frozenset(
    {
        "feishu_contact_department_children",
        "feishu_contact_department_users",
        "feishu_contact_organization_snapshot",
        "feishu_contact_scope_list",
        "feishu_contact_user_get",
        "feishu_contact_user_search",
    }
)
_TENANT_IM_TOOL_NAMES = frozenset(
    {
        "feishu_im_chat_search",
        "feishu_im_create_chat",
        "feishu_im_send_message",
    }
)
_TENANT_ACCESS_TOKEN_CACHE: dict[str, tuple[str, datetime]] = {}


@dataclass(frozen=True)
class FeishuMcpProviderContract:
    provider_name: str
    role: str
    requires_explicit_tool_binding: bool
    default_write_enabled: bool
    allowed_entrypoints: tuple[str, ...]
    action_executor: str
    performs_action_execution: bool


FEISHU_MCP_CONTRACT = FeishuMcpProviderContract(
    provider_name="feishu_mcp",
    role="tool_scheduling",
    requires_explicit_tool_binding=True,
    default_write_enabled=False,
    allowed_entrypoints=("agent_runtime", "admin_tool_console"),
    action_executor=LARK_CLI_EXECUTION_CONTRACT.engine_name,
    performs_action_execution=False,
)


def execute_feishu_mcp_tool(context: ToolContext, request: ToolRequest) -> str:
    handlers = _feishu_mcp_tool_handlers()
    handler = handlers.get(request.tool_name)
    if handler is not None:
        if context is not None and request.tool_name in _TENANT_CONTACT_TOOL_NAMES:
            return _execute_tenant_contact_tool(
                context,
                request,
                fallback=lambda: _with_cli_profile(request, lambda: handler(request)),
            )
        if context is not None and request.tool_name in _TENANT_IM_TOOL_NAMES:
            return _execute_tenant_im_tool(
                context,
                request,
                fallback=lambda: _with_cli_profile(request, lambda: handler(request)),
            )
        if request.tool_name == "feishu_approval_task_query" or request.tool_name.startswith("feishu_mail_") or request.tool_name == "mail_qa":
            if context is None:
                return _with_cli_profile(request, lambda: handler(request))
            if request.tool_name == "feishu_approval_task_query":
                return _with_cli_profile(request, lambda: _execute_approval_task_query_with_context(request, context))
            return _with_cli_profile(request, lambda: _execute_feishu_mail_tool(request, context))
        return _with_cli_profile(request, lambda: handler(request))
    return execute_feishu_mcp_realtime_tool(context, request)


def execute_feishu_mcp_realtime_tool(context: ToolContext | None, request: ToolRequest) -> str:
    handlers = _feishu_mcp_realtime_handlers()
    handler = handlers.get(request.tool_name)
    if handler is not None:
        if request.tool_name == "mail_qa":
            if context is None:
                return _with_cli_profile(request, lambda: handler(request))
            return _with_cli_profile(request, lambda: _execute_mail_triage_with_context(request, context))
        return _with_cli_profile(request, lambda: handler(request))
    raise NotImplementedError(
        f"Feishu realtime tool requires a CLI executor behind MCP before confirmed execution: {request.tool_name}"
    )


def feishu_mcp_realtime_tool_names() -> frozenset[str]:
    return frozenset(_feishu_mcp_realtime_handlers())


def feishu_mcp_bound_tool_names() -> frozenset[str]:
    return frozenset({*_feishu_mcp_tool_handlers(), *_feishu_mcp_realtime_handlers()})


def _with_cli_profile(request: ToolRequest, call: Callable[[], str]) -> str:
    profile = _safe_cli_profile(request.params.get("cli_profile"))
    token = _CURRENT_CLI_PROFILE.set(profile)
    try:
        return call()
    finally:
        _CURRENT_CLI_PROFILE.reset(token)


def _safe_cli_profile(value: Any) -> str | None:
    profile = str(value or "").strip()
    if not profile:
        return None
    if not all(char.isalnum() or char in {"-", "_", "."} for char in profile):
        raise ValueError("Feishu CLI profile contains unsupported characters.")
    return profile


def _run_lark_cli_json(args: list[str], *, action: str) -> dict[str, Any]:
    return _run_lark_cli_json_base(_cli_args_with_profile(args), action=action)


def _run_lark_cli_json_loose(args: list[str], *, action: str, timeout: int = 30) -> dict[str, Any]:
    return _run_lark_cli_json_loose_base(_cli_args_with_profile(args), action=action, timeout=timeout)


def run_lark_cli_json_via_mcp(
    args: list[str],
    *,
    cli_profile: str | None,
    action: str,
    timeout: int = 30,
) -> dict[str, Any]:
    token = _CURRENT_CLI_PROFILE.set(_safe_cli_profile(cli_profile))
    try:
        return _run_lark_cli_json_loose(args, action=action, timeout=timeout)
    finally:
        _CURRENT_CLI_PROFILE.reset(token)


def run_lark_cli_text_via_mcp(
    args: list[str],
    *,
    cli_profile: str | None,
    action: str,
    timeout: int = 30,
) -> str:
    token = _CURRENT_CLI_PROFILE.set(_safe_cli_profile(cli_profile))
    try:
        return _run_lark_cli_text_base(_cli_args_with_profile(args), action=action, timeout=timeout)
    finally:
        _CURRENT_CLI_PROFILE.reset(token)


def _cli_args_with_profile(args: list[str]) -> list[str]:
    profile = _CURRENT_CLI_PROFILE.get()
    if not profile or not args or args[0] != "lark-cli" or "--profile" in args:
        return args
    return ["lark-cli", "--profile", profile, *args[1:]]


def _feishu_mcp_tool_handlers() -> dict[str, Callable[[ToolRequest], str]]:
    return {
        "calendar_qa": _execute_cli_calendar_agenda,
        "feishu_approval_instance_get": _execute_cli_approval_instance_get,
        "feishu_approval_instance_initiated": _execute_cli_approval_instances_initiated,
        "feishu_approval_attachment_download": _execute_cli_approval_attachment_download,
        "feishu_approval_task_query": _execute_cli_approval_task_query,
        "feishu_bitable_field_list": _execute_cli_bitable_field_list,
        "feishu_bitable_view_get_card": _execute_cli_bitable_view_get_card,
        "feishu_bitable_view_get_timebar": _execute_cli_bitable_view_get_timebar,
        "feishu_bitable_view_get_visible_fields": _execute_cli_bitable_view_get_visible_fields,
        "feishu_contact_department_children": _execute_cli_contact_department_children,
        "feishu_contact_department_users": _execute_cli_contact_department_users,
        "feishu_contact_organization_snapshot": _execute_cli_contact_organization_snapshot,
        "feishu_contact_scope_list": _execute_cli_contact_scope_list,
        "feishu_contact_user_get": _execute_cli_contact_user_get,
        "feishu_contact_user_search": _execute_cli_contact_user_search,
        "feishu_drive_file_list": _execute_cli_drive_file_list,
        "feishu_drive_search": _execute_cli_drive_search,
        "feishu_doc_fetch": _execute_cli_doc_fetch,
        "feishu_im_chat_search": _execute_cli_im_chat_search,
        "feishu_im_message_list": _execute_cli_im_message_list,
        "feishu_mail_folder_list": _execute_cli_mail_folder_list,
        "feishu_mail_message_get": _execute_cli_mail_message_get,
        "feishu_okr_cycle_list": _execute_cli_okr_cycle_list,
        "feishu_okr_objective_list": _execute_cli_okr_objective_list,
        "feishu_vc_meeting_search": _execute_cli_vc_meeting_search,
        "feishu_wiki_space_list": _execute_cli_wiki_space_list,
        "feishu_wiki_node_list": _execute_cli_wiki_node_list,
        "bitable_qa": _execute_cli_bitable_read,
        "mail_qa": _execute_cli_mail_read,
        "task_qa": _execute_cli_task_read,
    }


def _feishu_mcp_realtime_handlers() -> dict[str, Callable[[ToolRequest], str]]:
    return {
        "feishu_approval_instance_cancel": _execute_cli_approval_instance_cancel,
        "feishu_approval_instance_cc": _execute_cli_approval_instance_cc,
        "feishu_approval_instance_remind": _execute_cli_approval_tasks_remind,
        "feishu_approval_task_add_sign": _execute_cli_approval_task_add_sign,
        "feishu_approval_task_approve": lambda item: _execute_cli_approval_task_action(item, action="approve"),
        "feishu_approval_task_reject": lambda item: _execute_cli_approval_task_action(item, action="reject"),
        "feishu_approval_task_rollback": _execute_cli_approval_task_rollback,
        "feishu_approval_task_transfer": _execute_cli_approval_task_transfer,
        "feishu_bitable_base_create": _execute_cli_bitable_base_create,
        "feishu_bitable_field_create": _execute_cli_bitable_field_create,
        "feishu_bitable_field_delete": _execute_cli_bitable_field_delete,
        "feishu_bitable_field_update": _execute_cli_bitable_field_update,
        "feishu_bitable_record_batch_create": _execute_cli_bitable_record_batch_create,
        "feishu_bitable_record_batch_delete": _execute_cli_bitable_record_batch_delete,
        "feishu_bitable_record_batch_update": _execute_cli_bitable_record_batch_update,
        "feishu_bitable_record_create": _execute_cli_bitable_record_create,
        "feishu_bitable_record_delete": _execute_cli_bitable_record_delete,
        "feishu_bitable_record_remove_attachment": _execute_cli_bitable_record_remove_attachment,
        "feishu_bitable_record_update": _execute_cli_bitable_record_update,
        "feishu_bitable_record_upload_attachment": _execute_cli_bitable_record_upload_attachment,
        "feishu_bitable_record_upsert": _execute_cli_bitable_record_upsert,
        "feishu_bitable_table_create": _execute_cli_bitable_table_create,
        "feishu_bitable_table_delete": _execute_cli_bitable_table_delete,
        "feishu_bitable_table_update": _execute_cli_bitable_table_update,
        "feishu_bitable_view_create": _execute_cli_bitable_view_create,
        "feishu_bitable_view_delete": _execute_cli_bitable_view_delete,
        "feishu_bitable_view_rename": _execute_cli_bitable_view_rename,
        "feishu_bitable_view_set_card": _execute_cli_bitable_view_set_card,
        "feishu_bitable_view_set_filter": _execute_cli_bitable_view_set_filter,
        "feishu_bitable_view_set_group": _execute_cli_bitable_view_set_group,
        "feishu_bitable_view_set_sort": _execute_cli_bitable_view_set_sort,
        "feishu_bitable_view_set_timebar": _execute_cli_bitable_view_set_timebar,
        "feishu_bitable_view_set_visible_fields": _execute_cli_bitable_view_set_visible_fields,
        "feishu_calendar_create_event": _execute_cli_calendar_create_event,
        "feishu_im_auto_join_public_chats": _execute_cli_im_auto_join_public_chats,
        "feishu_im_create_chat": _execute_cli_im_create_chat,
        "feishu_im_send_message": _execute_cli_im_send_message,
        "feishu_mail_drafts_create": _execute_cli_mail_draft_create,
        "feishu_task_assign_members": lambda item: _execute_cli_task_membership(
            item,
            action="+assign",
            add_key="add_assignees",
            remove_key="remove_assignees",
        ),
        "feishu_task_comment": _execute_cli_task_comment,
        "feishu_task_complete": _execute_cli_task_complete,
        "feishu_task_create": _execute_cli_task_create,
        "feishu_task_add_to_tasklist": _execute_cli_task_add_to_tasklist,
        "feishu_task_clear_ancestor": _execute_cli_task_clear_ancestor,
        "feishu_task_reopen": _execute_cli_task_reopen,
        "feishu_task_set_ancestor": _execute_cli_task_set_ancestor,
        "feishu_task_subtask_create": _execute_cli_task_subtask_create,
        "feishu_task_delete": _execute_cli_task_delete,
        "feishu_task_update": _execute_cli_task_update,
        "feishu_task_update_followers": lambda item: _execute_cli_task_membership(
            item,
            action="+followers",
            add_key="add_followers",
            remove_key="remove_followers",
        ),
        "feishu_task_update_reminders": _execute_cli_task_reminder,
        "feishu_tasklist_create": _execute_cli_tasklist_create,
        "feishu_tasklist_delete": _execute_cli_tasklist_delete,
        "feishu_tasklist_update": _execute_cli_tasklist_update,
        "feishu_tasklist_set_members": _execute_cli_tasklist_set_members,
        "feishu_tasklist_update_members": _execute_cli_tasklist_update_members,
        "feishu_task_section_create": _execute_cli_task_section_create,
        "feishu_task_section_delete": _execute_cli_task_section_delete,
        "feishu_task_section_update": _execute_cli_task_section_update,
        "feishu_task_upload_attachment": _execute_cli_task_upload_attachment,
    }


def _execute_cli_im_send_message(request: ToolRequest) -> str:
    params = request.params
    identity = str(params.get("as") or params.get("identity") or "bot").strip()
    if identity not in {"bot", "user"}:
        raise ValueError("Feishu CLI im +messages-send identity must be bot or user.")
    args = ["lark-cli", "im", "+messages-send", "--as", identity, "--format", "json"]
    chat_id = str(params.get("chat_id") or "").strip()
    receive_id = str(params.get("receive_id") or "").strip()
    receive_id_type = str(params.get("receive_id_type") or "").strip()
    user_id = str(params.get("user_id") or params.get("open_id") or "").strip()
    if chat_id:
        args.extend(["--chat-id", chat_id])
    elif user_id:
        args.extend(["--user-id", user_id])
    elif receive_id and receive_id_type == "chat_id":
        args.extend(["--chat-id", receive_id])
    elif receive_id and receive_id_type in {"open_id", "user_id"}:
        args.extend(["--user-id", receive_id])
    else:
        raise ValueError("Feishu CLI im +messages-send requires chat_id or user_id/open_id.")
    text = str(params.get("text") or "").strip()
    if not text:
        raise ValueError("Feishu CLI im +messages-send requires text.")
    args.extend(["--text", text])
    idempotency_key = str(params.get("idempotency_key") or params.get("uuid") or "").strip()
    if idempotency_key:
        args.extend(["--idempotency-key", idempotency_key])
    payload = _run_lark_cli_json(args, action="im +messages-send")
    message_id = _cli_message_id(payload)
    if message_id:
        return f"飞书消息已通过 CLI 发送：{message_id}"
    return "飞书消息已通过 CLI 发送。"


def _execute_cli_im_create_chat(request: ToolRequest) -> str:
    params = request.params
    args = [
        "lark-cli",
        "im",
        "+chat-create",
        "--as",
        _cli_identity(params, default="bot", label="im +chat-create"),
        "--format",
        "json",
        "--name",
        _required_cli_str(params, "name"),
    ]
    _maybe_cli_arg(args, "--description", params.get("description"))
    users = _string_list_value(params.get("user_id_list") or params.get("users"))
    if users:
        args.extend(["--users", ",".join(users)])
    bots = _string_list_value(params.get("bot_id_list") or params.get("bots"))
    if bots:
        args.extend(["--bots", ",".join(bots)])
    _maybe_cli_arg(args, "--owner", params.get("owner") or params.get("owner_id"))
    chat_type = str(params.get("chat_type") or params.get("type") or "private").strip()
    if chat_type not in {"private", "public"}:
        raise ValueError("Feishu CLI im +chat-create type must be private or public.")
    args.extend(["--type", chat_type])
    chat_mode = str(params.get("chat_mode") or "group").strip()
    if chat_mode not in {"group", "topic"}:
        raise ValueError("Feishu CLI im +chat-create chat_mode must be group or topic.")
    args.extend(["--chat-mode", chat_mode])
    if params.get("set_bot_manager") is True:
        args.append("--set-bot-manager")
    payload = _run_lark_cli_json(args, action="im +chat-create")
    chat_id = _cli_chat_id(payload)
    return f"飞书群已通过 CLI 创建：{chat_id}" if chat_id else "飞书群已通过 CLI 创建。"


def _execute_cli_im_auto_join_public_chats(request: ToolRequest) -> str:
    params = request.params
    chat_ids = _string_list_value(params.get("chat_ids") or params.get("chat_id"))
    if not chat_ids:
        raise ValueError("Feishu CLI public chat auto-join requires explicit chat_ids from dry-run preview.")
    identity = _cli_identity(params, default="user", label="api members/me_join")
    for chat_id in chat_ids:
        _run_lark_cli_json(
            [
                "lark-cli",
                "api",
                "POST",
                "/open-apis/im/v1/chats/:chat_id/members/me_join",
                "--as",
                identity,
                "--format",
                "json",
                "--params",
                _json_arg({"chat_id": chat_id}),
                "--data",
                "{}",
            ],
            action="api members/me_join",
        )
    return f"飞书公开群加入已通过 CLI 执行：成功 {len(chat_ids)} 个。"


def _execute_cli_task_create(request: ToolRequest) -> str:
    params = request.params
    payload = _cli_task_create_payload(params)
    args = [
        "lark-cli",
        "task",
        "+create",
        "--as",
        _cli_identity(params, default="user", label="task +create"),
        "--format",
        "json",
        "--data",
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str),
    ]
    idempotency_key = str(params.get("client_token") or params.get("idempotency_key") or "").strip()
    if idempotency_key:
        args.extend(["--idempotency-key", idempotency_key])
    title = _cli_task_title(_run_lark_cli_json(args, action="task +create")) or str(payload.get("summary") or "").strip()
    return f"飞书任务已通过 CLI 创建：{title}" if title else "飞书任务已通过 CLI 创建。"


def _execute_cli_task_read(request: ToolRequest) -> str:
    params = request.params
    args = [
        "lark-cli",
        "task",
        "+get-my-tasks",
        "--as",
        _cli_identity(params, default="user", label="task +get-my-tasks"),
        "--format",
        "json",
    ]
    _maybe_cli_arg(args, "--query", params.get("query") or params.get("keyword"))
    _maybe_cli_arg(args, "--created_at", params.get("created_at") or params.get("created_since"))
    _maybe_cli_arg(args, "--due-start", params.get("due_start") or params.get("due_since"))
    _maybe_cli_arg(args, "--due-end", params.get("due_end") or params.get("due_until"))
    _maybe_cli_arg(args, "--page-token", params.get("page_token"))
    page_limit = params.get("page_limit") if params.get("page_limit") is not None else params.get("page_size")
    if page_limit is not None:
        args.extend(
            [
                "--page-limit",
                str(_optional_cli_int({"page_limit": page_limit}, "page_limit", default=20, minimum=1, maximum=40)),
            ]
        )
    if _optional_cli_flag({"page_all": params.get("page_all")}, "page_all"):
        args.append("--page-all")
    if _optional_cli_flag({"complete": params.get("complete") or params.get("completed")}, "complete"):
        args.append("--complete")
    payload = _run_lark_cli_json(args, action="task +get-my-tasks")
    if _raw_json_response_requested(params):
        return _json_arg(payload)
    items = _cli_task_items(payload)
    titles = [_cli_task_item_title(item) for item in items[:5]]
    titles = [title for title in titles if title]
    if titles:
        return f"飞书任务已通过 CLI 读取 {len(items)} 条：{', '.join(titles)}"
    return f"飞书任务已通过 CLI 读取 {len(items)} 条。"


def _execute_mail_triage_with_context(request: ToolRequest, context: ToolContext) -> str:
    """Read mail using the user's OAuth access token from the Account table."""
    params = request.params
    db = context.db
    open_id = context.actor.open_id
    company_id = context.company_id

    if db is None or not open_id:
        return "邮件查询需要使用个人飞书授权。请先通过大飞哥完成飞书 OAuth 授权。"

    accounts = db.scalars(
        select(Account)
        .where(Account.company_id == company_id)
        .where(Account.provider == "feishu_user")
        .where(Account.is_active.is_(True))
    ).all()

    matched_account = None
    user_token = None
    mailbox = "me"
    for account in accounts:
        settings = account.settings or {}
        if str(settings.get("open_id") or "") != open_id:
            continue
        matched_account = account
        credentials = account.credentials or {}
        token = credentials.get("access_token")
        expires_at = credentials.get("expires_at")
        if token and not _feishu_token_expired(expires_at):
            user_token = str(token)
            break

    if not user_token and matched_account:
        new_token = _refresh_account_token(matched_account, company_id=company_id, db=db)
        if new_token:
            user_token = new_token

    if not user_token:
        return (
            "暂未找到有效的飞书 OAuth 授权。请先完成飞书 OAuth 授权后重试。\n\n"
            "已授权但 Token 已过期的用户，请联系管理员刷新授权。"
        )

    query = ""
    for key in ("query", "keyword", "q"):
        value = params.get(key)
        if value:
            query = str(value)
            break

    if query:
        try:
            payload = _search_mail_messages(
                user_token=user_token,
                mailbox=mailbox,
                query=query,
                page_size=int(params.get("page_size") or 15),
                page_token=str(params.get("page_token") or ""),
            )
        except Exception as exc:
            return f"邮件搜索请求失败：{exc}"
        if _raw_json_response_requested(params):
            return json.dumps(payload, ensure_ascii=False)
        items = _cli_mail_items(payload)
        titles = [_cli_mail_title(item) for item in items[:5]]
        titles = [title for title in titles if title]
        if titles:
            return f"搜索到 {len(items)} 封邮件：{', '.join(titles)}"
        return f"搜索到 {len(items)} 封邮件。"

    query_params: dict[str, str] = {"page_size": str(params.get("page_size", 20)), "folder_id": "INBOX"}
    if params.get("page_token"):
        query_params["page_token"] = str(params["page_token"])

    try:
        # Step 1: List message IDs
        list_resp = httpx.get(
            f"https://open.feishu.cn/open-apis/mail/v1/user_mailboxes/{mailbox}/messages",
            params=query_params,
            headers={"Authorization": f"Bearer {user_token}"},
            timeout=30,
        )
        list_resp.raise_for_status()
        list_payload = list_resp.json()
    except Exception as exc:
        return f"邮件查询请求失败：{exc}"

    if not isinstance(list_payload, dict):
        return "邮件查询返回了无效的响应格式。"

    if list_payload.get("code", 0) != 0:
        error_msg = list_payload.get("message") or list_payload.get("msg", "未知错误")
        return f"邮件查询失败（{list_payload.get('code')}）：{error_msg}"

    list_items = list_payload.get("data", {}).get("items") if isinstance(list_payload.get("data"), dict) else []
    if not list_items:
        if _raw_json_response_requested(params):
            return json.dumps(list_payload, ensure_ascii=False)
        return "没有找到邮件。"

    message_ids = [item["message_id"] for item in list_items if isinstance(item, dict) and item.get("message_id")]

    # Try batch_get to get message subjects (with format parameter as lark-cli does)
    try:
        batch_resp = httpx.post(
            f"https://open.feishu.cn/open-apis/mail/v1/user_mailboxes/{mailbox}/messages/batch_get",
            json={"format": "metadata", "message_ids": message_ids[:20]},
            headers={"Authorization": f"Bearer {user_token}"},
            timeout=30,
        )
        batch_resp.raise_for_status()
        payload = batch_resp.json()
    except Exception:
        payload = list_payload

    items = _cli_mail_items(payload)
    if not _cli_mail_items_have_subject(items):
        detail_items = _fetch_mail_message_details(
            user_token=user_token,
            mailbox=mailbox,
            message_ids=message_ids[:20],
        )
        if detail_items:
            payload = {"code": 0, "data": {"items": detail_items}}
            items = detail_items
    if not _cli_mail_items_have_subject(items):
        try:
            search_payload = _search_mail_messages(
                user_token=user_token,
                mailbox=mailbox,
                query="",
                page_size=int(params.get("page_size") or 15),
                page_token=str(params.get("page_token") or ""),
            )
            if _cli_mail_items_have_subject(_cli_mail_items(search_payload)):
                payload = search_payload
        except Exception:
            pass

    if _raw_json_response_requested(params):
        return json.dumps(payload, ensure_ascii=False)
    if not list_items:
        return "没有找到邮件。"

    items = _cli_mail_items(payload)
    titles = [_cli_mail_title(item) for item in items[:5]]
    titles = [title for title in titles if title]

    if titles:
        return f"查询到 {len(items)} 封邮件：{', '.join(titles)}"
    return f"查询到 {len(items)} 封邮件。"


def _fetch_mail_message_details(*, user_token: str, mailbox: str, message_ids: list[str]) -> list[dict[str, Any]]:
    if not message_ids:
        return []

    def fetch(message_id: str) -> dict[str, Any] | None:
        try:
            resp = httpx.get(
                f"https://open.feishu.cn/open-apis/mail/v1/user_mailboxes/{mailbox}/messages/{message_id}",
                params={"format": "metadata"},
                headers={"Authorization": f"Bearer {user_token}"},
                timeout=20,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception:
            return {"message_id": message_id}
        if not isinstance(payload, dict) or payload.get("code", 0) != 0:
            return {"message_id": message_id}
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        return {"message_id": message_id}

    results_by_id: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(message_ids))) as executor:
        futures = {executor.submit(fetch, message_id): message_id for message_id in message_ids}
        for future in as_completed(futures):
            message_id = futures[future]
            item = future.result()
            results_by_id[message_id] = item if isinstance(item, dict) else {"message_id": message_id}
    return [results_by_id.get(message_id, {"message_id": message_id}) for message_id in message_ids]


def _search_mail_messages(
    *,
    user_token: str,
    mailbox: str,
    query: str,
    page_size: int,
    page_token: str = "",
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "user_mailbox_id": mailbox,
        "page_size": max(1, min(page_size, 15)),
    }
    if page_token:
        params["page_token"] = page_token
    resp = httpx.post(
        f"https://open.feishu.cn/open-apis/mail/v1/user_mailboxes/{mailbox}/search",
        params={"page_size": str(params["page_size"]), **({"page_token": page_token} if page_token else {})},
        json={"query": query},
        headers={"Authorization": f"Bearer {user_token}"},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, dict):
        raise RuntimeError("邮件搜索返回了无效的响应格式。")
    if payload.get("code", 0) != 0:
        raise RuntimeError(f"邮件搜索失败（{payload.get('code')}）：{payload.get('message') or payload.get('msg') or '未知错误'}")
    return payload


def _execute_feishu_mail_tool(request: ToolRequest, context: ToolContext) -> str:
    """Execute mail tools with OAuth token from Account table."""
    tool_name = request.tool_name
    params = request.params
    db = context.db
    open_id = context.actor.open_id
    company_id = context.company_id

    if db is None or not open_id:
        return "邮件操作需要个人飞书授权。请先通过大飞哥完成飞书 OAuth 授权。"

    # Look up Account for OAuth token
    accounts = db.scalars(
        select(Account)
        .where(Account.company_id == company_id)
        .where(Account.provider == "feishu_user")
        .where(Account.is_active.is_(True))
    ).all()

    matched_account = None
    user_token = None
    for account in accounts:
        settings = account.settings or {}
        if str(settings.get("open_id") or "") != open_id:
            continue
        matched_account = account
        credentials = account.credentials or {}
        token = credentials.get("access_token")
        expires_at = credentials.get("expires_at")
        if token and not _feishu_token_expired(expires_at):
            user_token = str(token)
            break

    if not user_token and matched_account:
        new_token = _refresh_account_token(matched_account, company_id=company_id, db=db)
        if new_token:
            user_token = new_token

    if not user_token:
        return "邮件操作需要飞书 OAuth 授权。请先完成授权后重试。"
    
    mailbox = str(params.get("mailbox") or "me")

    try:
        if tool_name in ("feishu_mail_folders_list", "feishu_mail_folder_list"):
            resp = httpx.get(
                f"https://open.feishu.cn/open-apis/mail/v1/user_mailboxes/{mailbox}/folders",
                headers={"Authorization": f"Bearer {user_token}"},
                timeout=30,
            )
            resp.raise_for_status()
            payload = resp.json()
        elif tool_name == "feishu_mail_mailbox_info":
            resp = httpx.get(
                f"https://open.feishu.cn/open-apis/mail/v1/user_mailboxes/{mailbox}",
                headers={"Authorization": f"Bearer {user_token}"},
                timeout=30,
            )
            resp.raise_for_status()
            payload = resp.json()
        elif tool_name in ("feishu_mail_folder_messages", "feishu_mail_threads_list"):
            folder_id = str(params.get("folder_id") or "INBOX")
            path = "threads" if tool_name == "feishu_mail_threads_list" else "messages"
            query = {"page_size": str(params.get("page_size", 20))}
            if params.get("page_token"):
                query["page_token"] = str(params["page_token"])
            resp = httpx.get(
                f"https://open.feishu.cn/open-apis/mail/v1/user_mailboxes/{mailbox}/folders/{folder_id}/{path}",
                params=query,
                headers={"Authorization": f"Bearer {user_token}"},
                timeout=30,
            )
            resp.raise_for_status()
            payload = resp.json()
        elif tool_name in ("feishu_mail_message_get",):
            msg_id = str(params.get("message_id") or "")
            if not msg_id:
                return "需要提供 message_id。"
            resp = httpx.get(
                f"https://open.feishu.cn/open-apis/mail/v1/user_mailboxes/{mailbox}/messages/{msg_id}",
                headers={"Authorization": f"Bearer {user_token}"},
                timeout=30,
            )
            resp.raise_for_status()
            payload = resp.json()
        elif tool_name in ("mail_qa",):
            return _execute_mail_triage_with_context(request, context)
        else:
            # Write tools (send, reply, forward, move, mark, drafts, folder_create)
            # Return a message that the tool is pending implementation
            return f"邮件工具 {tool_name} 正在开发中。请先使用邮件问答（mail_qa）或文件夹查询。"

    except Exception as exc:
        return f"邮件操作失败：{exc}"

    if not isinstance(payload, dict):
        return "邮件操作返回了无效的响应格式。"

    if payload.get("code", 0) != 0:
        error_msg = payload.get("message") or payload.get("msg", "未知错误")
        return f"邮件操作失败（{payload.get('code')}）：{error_msg}"

    if _raw_json_response_requested(params):
        return json.dumps(payload, ensure_ascii=False)

    # Format result
    data = payload.get("data") if isinstance(payload, dict) else {}
    items = []
    if isinstance(data, dict):
        items = data.get("items") or []

    if tool_name in ("feishu_mail_folders_list", "feishu_mail_folder_list"):
        inbox = next((f for f in items if isinstance(f, dict) and f.get("id") == "INBOX"), None)
        total = sum(f.get("total_message_count", 0) for f in items if isinstance(f, dict))
        unread = sum(f.get("unread_message_count", 0) for f in items if isinstance(f, dict))
        inbox_total = inbox.get("total_message_count", 0) if inbox else 0
        inbox_unread = inbox.get("unread_message_count", 0) if inbox else 0
        return f"共有 {len(items)} 个文件夹，全部邮件 {total} 封，未读 {unread} 封。收件箱 {inbox_total} 封（未读 {inbox_unread} 封）。"

    if tool_name in ("feishu_mail_folder_messages",):
        titles = []
        for item in items[:5]:
            if isinstance(item, dict):
                titles.append(str(item.get("subject") or item.get("message_id", "无主题"))[:60])
        return f"共 {len(items)} 封邮件：" + "；".join(titles) if titles else f"共 {len(items)} 封邮件。"

    if tool_name == "feishu_mail_mailbox_info":
        if isinstance(data, dict):
            email = data.get("email", "")
            name = data.get("name", "")
            return f"邮箱：{name} <{email}>" if name else f"邮箱：{email}"
        return "邮箱信息未返回。"

    return f"操作成功，返回 {len(items)} 条结果。"

def _execute_cli_mail_read(request: ToolRequest) -> str:
    params = request.params
    args = [
        "lark-cli",
        "mail",
        "+triage",
        "--as",
        _cli_identity(params, default="user", label="mail +triage"),
        "--format",
        "json",
        "--mailbox",
        str(params.get("mailbox") or params.get("user_mailbox_id") or "me").strip() or "me",
    ]
    _maybe_cli_arg(args, "--query", params.get("query") or params.get("keyword"))
    _maybe_cli_arg(args, "--page-token", params.get("page_token"))
    max_items = params.get("max") if params.get("max") is not None else params.get("page_size")
    if max_items is not None:
        args.extend(["--max", str(_optional_cli_int({"max": max_items}, "max", default=20, minimum=1, maximum=400))])
    filter_value = _mail_triage_filter(params)
    if filter_value:
        args.extend(["--filter", _json_arg(filter_value)])
    if _optional_cli_flag({"labels": params.get("labels")}, "labels"):
        args.append("--labels")
    payload = _run_lark_cli_json(args, action="mail +triage")
    if _raw_json_response_requested(params):
        return _json_arg(payload)
    items = _cli_mail_items(payload)
    titles = [_cli_mail_title(item) for item in items[:5]]
    titles = [title for title in titles if title]
    if titles:
        return f"飞书邮箱邮件已通过 CLI 读取 {len(items)} 条：{', '.join(titles)}"
    return f"飞书邮箱邮件已通过 CLI 读取 {len(items)} 条。"


def _execute_cli_im_chat_search(request: ToolRequest) -> str:
    params = request.params
    args = [
        "lark-cli",
        "im",
        "+chat-search",
        "--as",
        _cli_identity(params, default="user", label="im +chat-search"),
        "--format",
        "json",
        "--query",
        _required_cli_str(params, "query"),
        "--page-size",
        str(_optional_cli_int(params, "page_size", default=20, minimum=1, maximum=100)),
    ]
    _maybe_cli_arg(args, "--page-token", params.get("page_token"))
    _maybe_cli_arg(args, "--member-ids", _comma_cli_arg(params.get("member_ids")))
    _maybe_cli_arg(args, "--chat-modes", _comma_cli_arg(params.get("chat_modes")))
    _maybe_cli_arg(args, "--search-types", _comma_cli_arg(params.get("search_types")))
    payload = _run_lark_cli_json(args, action="im +chat-search")
    if _raw_json_response_requested(params):
        return _json_arg(payload)
    items = _cli_im_items(payload, keys=("items", "chats", "chat_list"))
    titles = [_cli_im_title(item) for item in items[:5]]
    titles = [title for title in titles if title]
    if titles:
        return f"飞书群聊已通过 CLI 搜索 {len(items)} 个：{', '.join(titles)}"
    return f"飞书群聊已通过 CLI 搜索 {len(items)} 个。"


def _execute_cli_im_message_list(request: ToolRequest) -> str:
    params = request.params
    args = [
        "lark-cli",
        "im",
        "+chat-messages-list",
        "--as",
        _cli_identity(params, default="user", label="im +chat-messages-list"),
        "--format",
        "json",
        "--chat-id",
        _required_cli_str(params, "chat_id"),
        "--page-size",
        str(_optional_cli_int(params, "page_size", default=20, minimum=1, maximum=50)),
    ]
    _maybe_cli_arg(args, "--start", _cli_time_arg(params.get("start") or params.get("start_time")))
    _maybe_cli_arg(args, "--end", _cli_time_arg(params.get("end") or params.get("end_time")))
    _maybe_cli_arg(args, "--page-token", params.get("page_token"))
    _maybe_cli_arg(args, "--sort", params.get("sort"))
    if params.get("no_reactions") is True:
        args.append("--no-reactions")
    payload = _run_lark_cli_json(args, action="im +chat-messages-list")
    if _raw_json_response_requested(params):
        return _json_arg(payload)
    items = _cli_im_items(payload, keys=("items", "messages", "message_list"))
    titles = [_cli_im_title(item) for item in items[:5]]
    titles = [title for title in titles if title]
    if titles:
        return f"飞书群聊消息已通过 CLI 读取 {len(items)} 条：{', '.join(titles)}"
    return f"飞书群聊消息已通过 CLI 读取 {len(items)} 条。"


def _execute_cli_mail_folder_list(request: ToolRequest) -> str:
    params = request.params
    user_mailbox_id = str(params.get("user_mailbox_id") or params.get("mailbox") or "me").strip() or "me"
    args = [
        "lark-cli",
        "mail",
        "user_mailbox.folders",
        "list",
        "--as",
        _cli_identity(params, default="user", label="mail user_mailbox.folders list"),
        "--format",
        "json",
        "--params",
        _json_arg({"user_mailbox_id": user_mailbox_id}),
    ]
    payload = _run_lark_cli_json(args, action="mail user_mailbox.folders list")
    if _raw_json_response_requested(params):
        return _json_arg(payload)
    items = _cli_mail_items(payload)
    data = payload.get("data") if isinstance(payload, dict) else None
    if not items and isinstance(data, dict) and isinstance(data.get("folders"), list):
        items = data["folders"]
    titles = [_cli_mail_title(item) for item in items[:5]]
    titles = [title for title in titles if title]
    if titles:
        return f"飞书邮箱文件夹已通过 CLI 读取 {len(items)} 个：{', '.join(titles)}"
    return f"飞书邮箱文件夹已通过 CLI 读取 {len(items)} 个。"


def _execute_cli_mail_message_get(request: ToolRequest) -> str:
    params = request.params
    args = [
        "lark-cli",
        "mail",
        "+message",
        "--as",
        _cli_identity(params, default="user", label="mail +message"),
        "--format",
        "json",
        "--mailbox",
        str(params.get("mailbox") or params.get("user_mailbox_id") or "me").strip() or "me",
        "--message-id",
        _required_cli_str(params, "message_id"),
    ]
    if params.get("html") is not None:
        args.extend(["--html", str(params.get("html")).lower()])
    payload = _run_lark_cli_json(args, action="mail +message")
    if _raw_json_response_requested(params):
        return _json_arg(payload)
    data = payload.get("data") if isinstance(payload, dict) else None
    title = _cli_mail_title(data) or _cli_mail_title(payload) or _required_cli_str(params, "message_id")
    return f"飞书邮箱邮件详情已通过 CLI 读取：{title}"


def _execute_cli_mail_draft_create(request: ToolRequest) -> str:
    params = request.params
    args = [
        "lark-cli",
        "mail",
        "+draft-create",
        "--as",
        _cli_identity(params, default="user", label="mail +draft-create"),
        "--format",
        "json",
        "--mailbox",
        str(params.get("mailbox") or params.get("user_mailbox_id") or "me").strip() or "me",
        "--subject",
        _required_cli_str(params, "subject"),
        "--body",
        _required_cli_str(params, "body"),
    ]
    _maybe_cli_arg(args, "--to", params.get("to"))
    _maybe_cli_arg(args, "--cc", params.get("cc"))
    _maybe_cli_arg(args, "--bcc", params.get("bcc"))
    _maybe_cli_arg(args, "--from", params.get("from"))
    _maybe_cli_arg(args, "--template-id", params.get("template_id"))
    _maybe_cli_arg(args, "--priority", params.get("priority"))
    _maybe_cli_arg(args, "--signature-id", params.get("signature_id"))
    if _optional_cli_flag({"plain_text": params.get("plain_text")}, "plain_text"):
        args.append("--plain-text")
    if _optional_cli_flag({"no_signature": params.get("no_signature")}, "no_signature"):
        args.append("--no-signature")
    payload = _run_lark_cli_json(args, action="mail +draft-create")
    if _raw_json_response_requested(params):
        return _json_arg(payload)
    draft_id = _first_cli_value(payload, keys=("draft_id", "id"))
    subject = str(params.get("subject") or "").strip()
    return f"飞书邮件草稿已通过 CLI 创建：{subject}（{draft_id}）" if draft_id else f"飞书邮件草稿已通过 CLI 创建：{subject}"


def _execute_cli_vc_meeting_search(request: ToolRequest) -> str:
    params = request.params
    args = [
        "lark-cli",
        "vc",
        "+search",
        "--as",
        _cli_identity(params, default="user", label="vc +search"),
        "--format",
        "json",
    ]
    query = params.get("query") or params.get("keyword")
    start = params.get("start") or params.get("start_time")
    end = params.get("end") or params.get("end_time")
    if not any(
        (
            query,
            start,
            end,
            params.get("participant_ids"),
            params.get("organizer_ids"),
            params.get("room_ids"),
        )
    ):
        now = datetime.now(UTC)
        start = now - timedelta(days=7)
        end = now
    _maybe_cli_arg(args, "--query", query)
    _maybe_cli_arg(args, "--start", _cli_time_arg(start))
    _maybe_cli_arg(args, "--end", _cli_time_arg(end))
    _maybe_cli_arg(args, "--page-token", params.get("page_token"))
    args.extend(
        [
            "--page-size",
            str(_optional_cli_int(params, "page_size", default=20, minimum=1, maximum=30)),
        ]
    )
    _maybe_cli_arg(args, "--participant-ids", _comma_cli_arg(params.get("participant_ids")))
    _maybe_cli_arg(args, "--organizer-ids", _comma_cli_arg(params.get("organizer_ids")))
    _maybe_cli_arg(args, "--room-ids", _comma_cli_arg(params.get("room_ids")))
    payload = _run_lark_cli_json(args, action="vc +search")
    if _raw_json_response_requested(params):
        return _json_arg(payload)
    items = _cli_vc_meeting_items(payload)
    titles = [_cli_vc_meeting_title(item) for item in items[:5]]
    titles = [title for title in titles if title]
    if titles:
        return f"飞书历史会议已通过 CLI 读取 {len(items)} 条：{', '.join(titles)}"
    return f"飞书历史会议已通过 CLI 读取 {len(items)} 条。"


def _execute_cli_wiki_space_list(request: ToolRequest) -> str:
    params = request.params
    args = [
        "lark-cli",
        "wiki",
        "+space-list",
        "--as",
        _cli_identity(params, default="user", label="wiki +space-list"),
        "--format",
        "json",
        "--page-size",
        str(_optional_cli_int(params, "page_size", default=20, minimum=1, maximum=50)),
    ]
    _maybe_cli_arg(args, "--page-token", params.get("page_token"))
    payload = _run_lark_cli_json(args, action="wiki +space-list")
    if _raw_json_response_requested(params):
        return _json_arg(payload)
    items = _cli_wiki_items(payload, keys=("items", "spaces", "space_list"))
    titles = [_cli_wiki_title(item) for item in items[:5]]
    titles = [title for title in titles if title]
    if titles:
        return f"飞书知识空间已通过 CLI 读取 {len(items)} 个：{', '.join(titles)}"
    return f"飞书知识空间已通过 CLI 读取 {len(items)} 个。"


def _execute_cli_wiki_node_list(request: ToolRequest) -> str:
    params = request.params
    args = [
        "lark-cli",
        "wiki",
        "+node-list",
        "--as",
        _cli_identity(params, default="user", label="wiki +node-list"),
        "--format",
        "json",
        "--space-id",
        _required_cli_str(params, "space_id"),
        "--page-size",
        str(_optional_cli_int(params, "page_size", default=50, minimum=1, maximum=50)),
    ]
    _maybe_cli_arg(args, "--parent-node-token", params.get("parent_node_token"))
    _maybe_cli_arg(args, "--page-token", params.get("page_token"))
    payload = _run_lark_cli_json(args, action="wiki +node-list")
    if _raw_json_response_requested(params):
        return _json_arg(payload)
    items = _cli_wiki_items(payload, keys=("items", "nodes", "node_list"))
    titles = [_cli_wiki_title(item) for item in items[:5]]
    titles = [title for title in titles if title]
    if titles:
        return f"飞书知识库节点已通过 CLI 读取 {len(items)} 个：{', '.join(titles)}"
    return f"飞书知识库节点已通过 CLI 读取 {len(items)} 个。"


def _execute_cli_bitable_read(request: ToolRequest) -> str:
    params = request.params
    if str(params.get("table_id") or "").strip():
        args = _base_cli_args(params, "+record-list")
        _maybe_cli_arg(args, "--view-id", params.get("view_id"))
        for field_name in _string_list_value(params.get("field_names") or params.get("field_ids")):
            args.extend(["--field-id", field_name])
        filter_json = params.get("filter_json") or params.get("filter")
        if filter_json is not None:
            args.extend(["--filter-json", _json_arg(filter_json) if isinstance(filter_json, dict) else str(filter_json)])
        sort_json = params.get("sort_json") or params.get("sort")
        if sort_json is not None:
            args.extend(["--sort-json", _json_arg(sort_json) if isinstance(sort_json, (dict, list)) else str(sort_json)])
        offset = _optional_cli_nonnegative_int(params, "offset")
        if offset is not None:
            args.extend(["--offset", str(offset)])
        limit = params.get("limit") if params.get("limit") is not None else params.get("page_size")
        if limit is not None:
            args.extend(["--limit", str(_optional_cli_int({"limit": limit}, "limit", default=100, minimum=1, maximum=200))])
        payload = _run_lark_cli_json(args, action="base +record-list")
        if _raw_json_response_requested(params):
            return _json_arg(payload)
        items = _cli_bitable_items(payload)
        titles = [_cli_bitable_title(item, ("record_id", "id")) for item in items[:5]]
        titles = [title for title in titles if title]
        if titles:
            return f"飞书多维表格记录已通过 CLI 读取 {len(items)} 条：{', '.join(titles)}"
        return f"飞书多维表格记录已通过 CLI 读取 {len(items)} 条。"
    args = _base_cli_args(params, "+table-list", include_table=False)
    offset = _optional_cli_nonnegative_int(params, "offset")
    if offset is not None:
        args.extend(["--offset", str(offset)])
    limit = params.get("limit") if params.get("limit") is not None else params.get("page_size")
    if limit is not None:
        args.extend(["--limit", str(_optional_cli_int({"limit": limit}, "limit", default=50, minimum=1, maximum=100))])
    payload = _run_lark_cli_json(args, action="base +table-list")
    if _raw_json_response_requested(params):
        return _json_arg(payload)
    items = _cli_bitable_items(payload)
    titles = [_cli_bitable_title(item, ("name", "table_id", "id")) for item in items[:5]]
    titles = [title for title in titles if title]
    if titles:
        return f"飞书多维表格数据表已通过 CLI 读取 {len(items)} 条：{', '.join(titles)}"
    return f"飞书多维表格数据表已通过 CLI 读取 {len(items)} 条。"


def _execute_cli_bitable_field_list(request: ToolRequest) -> str:
    params = request.params
    args = _base_cli_args(params, "+field-list")
    limit = params.get("limit") if params.get("limit") is not None else params.get("page_size")
    if limit is not None:
        args.extend(["--limit", str(_optional_cli_int({"limit": limit}, "limit", default=100, minimum=1, maximum=200))])
    offset = _optional_cli_nonnegative_int(params, "offset")
    if offset is not None:
        args.extend(["--offset", str(offset)])
    payload = _run_lark_cli_json(args, action="base +field-list")
    items = _cli_bitable_items(payload)
    titles = [_cli_bitable_title(item, ("field_name", "name", "field_id")) for item in items[:5]]
    titles = [title for title in titles if title]
    if titles:
        return f"飞书多维表格字段已通过 CLI 读取 {len(items)} 条：{', '.join(titles)}"
    return f"飞书多维表格字段已通过 CLI 读取 {len(items)} 条。"


def _execute_cli_bitable_view_get_visible_fields(request: ToolRequest) -> str:
    payload = _run_lark_cli_json(
        _base_view_get_cli_args(request.params, "+view-get-visible-fields"),
        action="base +view-get-visible-fields",
    )
    visible_fields = _cli_bitable_visible_fields(payload)
    if visible_fields is not None:
        return f"飞书多维表格视图可见字段已通过 CLI 读取：{len(visible_fields)} 个"
    return "飞书多维表格视图可见字段已通过 CLI 读取。"


def _execute_cli_bitable_view_get_card(request: ToolRequest) -> str:
    _run_lark_cli_json(_base_view_get_cli_args(request.params, "+view-get-card"), action="base +view-get-card")
    return "飞书多维表格视图卡片配置已通过 CLI 读取。"


def _execute_cli_bitable_view_get_timebar(request: ToolRequest) -> str:
    _run_lark_cli_json(_base_view_get_cli_args(request.params, "+view-get-timebar"), action="base +view-get-timebar")
    return "飞书多维表格视图时间轴配置已通过 CLI 读取。"


def _execute_cli_okr_cycle_list(request: ToolRequest) -> str:
    params = request.params
    args = [
        "lark-cli",
        "okr",
        "+cycle-list",
        "--as",
        _cli_identity(params, default="user", label="okr +cycle-list"),
        "--format",
        "json",
        "--user-id",
        _required_cli_str(params, "user_id", aliases=("open_id",)),
        "--user-id-type",
        _cli_user_id_type(params, label="okr +cycle-list"),
    ]
    _maybe_cli_arg(args, "--time-range", params.get("time_range"))
    payload = _run_lark_cli_json(args, action="okr +cycle-list")
    items = _cli_okr_items(payload, keys=("items", "cycles"))
    titles = [_cli_okr_title(item, ("id", "tenant_cycle_id", "name")) for item in items[:5]]
    titles = [title for title in titles if title]
    if titles:
        return f"飞书 OKR 周期已通过 CLI 读取 {len(items)} 条：{', '.join(titles)}"
    return f"飞书 OKR 周期已通过 CLI 读取 {len(items)} 条。"


def _execute_cli_okr_objective_list(request: ToolRequest) -> str:
    params = request.params
    args = [
        "lark-cli",
        "okr",
        "+cycle-detail",
        "--as",
        _cli_identity(params, default="user", label="okr +cycle-detail"),
        "--format",
        "json",
        "--cycle-id",
        _required_cli_str(params, "cycle_id"),
    ]
    payload = _run_lark_cli_json(args, action="okr +cycle-detail")
    items = _cli_okr_items(payload, keys=("objectives", "items"))
    titles = [_cli_okr_title(item, ("content", "id", "name")) for item in items[:5]]
    titles = [title for title in titles if title]
    if titles:
        return f"飞书 OKR 目标已通过 CLI 读取 {len(items)} 条：{', '.join(titles)}"
    return f"飞书 OKR 目标已通过 CLI 读取 {len(items)} 条。"


def _execute_cli_contact_department_children(request: ToolRequest) -> str:
    params = request.params
    payload = _run_contact_department_children(params, department_id=str(params.get("department_id") or "0"))
    if _raw_json_response_requested(params):
        return _json_arg(payload)
    items = _cli_contact_items(payload, keys=("items", "departments"))
    titles = [_cli_contact_title(item, ("name", "department_id", "open_department_id")) for item in items[:5]]
    titles = [title for title in titles if title]
    if titles:
        return f"飞书通讯录子部门已通过 CLI 读取 {len(items)} 条：{', '.join(titles)}"
    return f"飞书通讯录子部门已通过 CLI 读取 {len(items)} 条。"


def _execute_cli_contact_department_users(request: ToolRequest) -> str:
    params = request.params
    payload = _run_contact_department_users(params, department_id=str(params.get("department_id") or "0"))
    if _raw_json_response_requested(params):
        return _json_arg(payload)
    items = _cli_contact_items(payload, keys=("items", "users"))
    titles = [_cli_contact_title(item, ("name", "en_name", "open_id", "user_id")) for item in items[:5]]
    titles = [title for title in titles if title]
    if titles:
        return f"飞书通讯录部门用户已通过 CLI 读取 {len(items)} 条：{', '.join(titles)}"
    return f"飞书通讯录部门用户已通过 CLI 读取 {len(items)} 条。"


def _execute_cli_contact_scope_list(request: ToolRequest) -> str:
    params = request.params
    payload = _run_contact_scope_list_payload(params)
    data = payload.get("data") if isinstance(payload, dict) else {}
    data = data if isinstance(data, dict) else {}
    department_count = len(data.get("department_ids") or [])
    user_count = len(data.get("user_ids") or [])
    group_count = len(data.get("group_ids") or [])
    if _raw_json_response_requested(params):
        return _json_arg(payload)
    return f"飞书通讯录授权范围已通过 CLI 读取：部门 {department_count} 个，用户 {user_count} 个，用户组 {group_count} 个。"


def _run_contact_scope_list_payload(params: dict[str, Any]) -> Any:
    query = {
        "department_id_type": str(params.get("department_id_type") or "open_department_id"),
        "user_id_type": _cli_user_id_type(params, label="api contact scopes"),
        "page_size": _optional_cli_int(params, "page_size", default=100, minimum=1, maximum=100),
    }
    _maybe_set(query, "page_token", params.get("page_token"))
    return _run_lark_cli_json(
        _contact_api_get_args(params, "/open-apis/contact/v3/scopes", query, label="api contact scopes"),
        action="api contact scopes",
    )


def _execute_tenant_contact_tool(context: ToolContext, request: ToolRequest, *, fallback: Callable[[], str]) -> str:
    app_config = _active_feishu_app_config(context)
    if app_config is None:
        return fallback()
    params = request.params
    if request.tool_name == "feishu_contact_department_children":
        payload = _tenant_contact_department_children(app_config, params, department_id=str(params.get("department_id") or "0"))
        return _format_contact_department_children(payload, params, source_label="Tenant Token")
    if request.tool_name == "feishu_contact_department_users":
        payload = _tenant_contact_department_users(app_config, params, department_id=str(params.get("department_id") or "0"))
        return _format_contact_department_users(payload, params, source_label="Tenant Token")
    if request.tool_name == "feishu_contact_scope_list":
        payload = _tenant_contact_scope_list(app_config, params)
        return _format_contact_scope_list(payload, params, source_label="Tenant Token")
    if request.tool_name == "feishu_contact_user_get":
        user_id = _required_cli_str(params, "user_id", aliases=("open_id",))
        payload = _tenant_contact_user_get(app_config, params, user_id=user_id)
        return _format_contact_user_get(payload, params, user_id=user_id, source_label="Tenant Token")
    if request.tool_name == "feishu_contact_user_search":
        return _execute_tenant_contact_user_search(app_config, request)
    if request.tool_name == "feishu_contact_organization_snapshot":
        return _execute_tenant_contact_organization_snapshot(app_config, request)
    return fallback()


def _execute_tenant_im_tool(context: ToolContext, request: ToolRequest, *, fallback: Callable[[], str]) -> str:
    app_config = _active_feishu_app_config(context)
    if app_config is None:
        return fallback()
    params = request.params
    if request.tool_name == "feishu_im_chat_search":
        query = str(params.get("query") or "").strip()
        view = str(params.get("view") or "").strip()
        if not query and view in {"count", "list"}:
            payload = _tenant_im_chat_list(app_config, params)
            return _format_im_chat_items(payload, params, source_label="Tenant Token", query="")
        payload = _tenant_im_chat_search(app_config, params)
        return _format_im_chat_items(payload, params, source_label="Tenant Token", query=query)
    if request.tool_name == "feishu_im_create_chat":
        payload = _tenant_im_create_chat(app_config, params)
        return _format_im_create_chat(payload, params, source_label="Tenant Token")
    if request.tool_name == "feishu_im_send_message":
        payload = _tenant_im_send_message(app_config, params)
        return _format_im_send_message(payload, params, source_label="Tenant Token")
    return fallback()


def _active_feishu_app_config(context: ToolContext) -> FeishuAppConfig | None:
    db = getattr(context, "db", None)
    if db is None or not callable(getattr(db, "scalar", None)):
        return None
    return db.scalar(
        select(FeishuAppConfig)
        .where(FeishuAppConfig.company_id == context.company_id)
        .where(FeishuAppConfig.is_active.is_(True))
    )


def _tenant_access_token(app_config: FeishuAppConfig) -> str:
    cache_key = str(app_config.app_id)
    cached = _TENANT_ACCESS_TOKEN_CACHE.get(cache_key)
    now = datetime.now(UTC)
    if cached is not None:
        token, expires_at = cached
        if expires_at > now + timedelta(seconds=60):
            return token
    payload = _tenant_http_request(
        app_config,
        "POST",
        "/open-apis/auth/v3/tenant_access_token/internal",
        payload={"app_id": app_config.app_id, "app_secret": app_config.app_secret},
        auth=False,
        label="Feishu tenant token",
    )
    token = str(payload.get("tenant_access_token") or "").strip()
    if not token:
        raise RuntimeError("Feishu tenant token response did not include tenant_access_token.")
    expires_in = int(payload.get("expire") or 7200)
    _TENANT_ACCESS_TOKEN_CACHE[cache_key] = (token, now + timedelta(seconds=max(expires_in - 300, 60)))
    return token


def _tenant_http_request(
    app_config: FeishuAppConfig,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    auth: bool = True,
    label: str = "Feishu API",
) -> dict[str, Any]:
    headers = {}
    if auth:
        headers["Authorization"] = f"Bearer {_tenant_access_token(app_config)}"
    response = httpx.request(
        method,
        f"{settings.feishu_base_url.rstrip('/')}{path}",
        params=params or {},
        json=payload if method in {"POST", "PATCH", "DELETE"} else None,
        headers=headers,
        timeout=settings.request_timeout_seconds,
    )
    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text}
    if response.status_code >= 400:
        raise RuntimeError(f"{label} HTTP error {response.status_code}: {body}")
    if not isinstance(body, dict) or body.get("code", 0) != 0:
        code = body.get("code") if isinstance(body, dict) else "invalid_response"
        message = body.get("message") or body.get("msg") if isinstance(body, dict) else str(body)
        raise RuntimeError(f"{label} failed ({code}): {message}")
    return body


def _tenant_contact_department_children(app_config: FeishuAppConfig, params: dict[str, Any], *, department_id: str) -> dict[str, Any]:
    query: dict[str, Any] = {
        "department_id": department_id,
        "department_id_type": str(params.get("department_id_type") or "department_id"),
        "user_id_type": _cli_user_id_type(params, label="api contact department children"),
        "page_size": _optional_cli_int(params, "page_size", default=50, minimum=1, maximum=50),
    }
    if params.get("fetch_child") is not None:
        query["fetch_child"] = _optional_cli_flag(params, "fetch_child")
    _maybe_set(query, "page_token", params.get("page_token"))
    return _tenant_http_request(
        app_config,
        "GET",
        "/open-apis/contact/v3/departments/" + department_id + "/children",
        params=query,
        label="Feishu contact department children",
    )


def _tenant_contact_department_users(app_config: FeishuAppConfig, params: dict[str, Any], *, department_id: str) -> dict[str, Any]:
    query: dict[str, Any] = {
        "department_id": department_id,
        "department_id_type": str(params.get("department_id_type") or "department_id"),
        "user_id_type": _cli_user_id_type(params, label="api contact department users"),
        "page_size": _optional_cli_int(params, "page_size", default=50, minimum=1, maximum=50),
    }
    _maybe_set(query, "page_token", params.get("page_token"))
    return _tenant_http_request(
        app_config,
        "GET",
        "/open-apis/contact/v3/users/find_by_department",
        params=query,
        label="Feishu contact department users",
    )


def _tenant_contact_scope_list(app_config: FeishuAppConfig, params: dict[str, Any]) -> dict[str, Any]:
    query = {
        "department_id_type": str(params.get("department_id_type") or "open_department_id"),
        "user_id_type": _cli_user_id_type(params, label="api contact scopes"),
        "page_size": _optional_cli_int(params, "page_size", default=100, minimum=1, maximum=100),
    }
    _maybe_set(query, "page_token", params.get("page_token"))
    return _tenant_http_request(app_config, "GET", "/open-apis/contact/v3/scopes", params=query, label="Feishu contact scopes")


def _tenant_contact_user_get(app_config: FeishuAppConfig, params: dict[str, Any], *, user_id: str) -> dict[str, Any]:
    query = {
        "user_id_type": _cli_user_id_type(params, label="api contact user get"),
        "department_id_type": str(params.get("department_id_type") or "open_department_id"),
    }
    return _tenant_http_request(
        app_config,
        "GET",
        "/open-apis/contact/v3/users/" + user_id,
        params=query,
        label="Feishu contact user get",
    )


def _tenant_im_chat_list(app_config: FeishuAppConfig, params: dict[str, Any]) -> dict[str, Any]:
    query: dict[str, Any] = {
        "page_size": _optional_cli_int(params, "page_size", default=20, minimum=1, maximum=100),
    }
    _maybe_set(query, "page_token", params.get("page_token"))
    return _tenant_http_request(
        app_config,
        "GET",
        "/open-apis/im/v1/chats",
        params=query,
        label="Feishu IM chat list",
    )


def _tenant_im_chat_search(app_config: FeishuAppConfig, params: dict[str, Any]) -> dict[str, Any]:
    query: dict[str, Any] = {
        "query": str(params.get("query") or "").strip(),
        "page_size": _optional_cli_int(params, "page_size", default=20, minimum=1, maximum=100),
    }
    _maybe_set(query, "page_token", params.get("page_token"))
    _maybe_set(query, "owner_id", params.get("owner_id"))
    _maybe_set(query, "user_id_type", params.get("user_id_type"))
    return _tenant_http_request(
        app_config,
        "GET",
        "/open-apis/im/v1/chats/search",
        params=query,
        label="Feishu IM chat search",
    )


def _tenant_im_create_chat(app_config: FeishuAppConfig, params: dict[str, Any]) -> dict[str, Any]:
    name = _required_cli_str(params, "name")
    payload: dict[str, Any] = {
        "name": name,
        "chat_mode": str(params.get("chat_mode") or "group").strip(),
        "chat_type": str(params.get("chat_type") or params.get("type") or "private").strip(),
    }
    _maybe_set(payload, "description", params.get("description"))
    users = _string_list_value(params.get("user_id_list") or params.get("users"))
    if users:
        payload["user_id_list"] = users
    bots = _string_list_value(params.get("bot_id_list") or params.get("bots"))
    if bots:
        payload["bot_id_list"] = bots
    _maybe_set(payload, "owner_id", params.get("owner_id") or params.get("owner"))
    query = {"user_id_type": str(params.get("user_id_type") or "open_id")}
    return _tenant_http_request(
        app_config,
        "POST",
        "/open-apis/im/v1/chats",
        params=query,
        payload=payload,
        label="Feishu IM chat create",
    )


def _tenant_im_send_message(app_config: FeishuAppConfig, params: dict[str, Any]) -> dict[str, Any]:
    chat_id = str(params.get("chat_id") or "").strip()
    receive_id = str(params.get("receive_id") or "").strip()
    receive_id_type = str(params.get("receive_id_type") or "").strip()
    user_id = str(params.get("user_id") or params.get("open_id") or "").strip()
    if chat_id:
        receive_id = chat_id
        receive_id_type = "chat_id"
    elif user_id:
        receive_id = user_id
        receive_id_type = str(params.get("user_id_type") or "open_id")
    elif not receive_id or not receive_id_type:
        raise ValueError("Feishu IM message create requires chat_id or user_id/open_id.")
    text = str(params.get("text") or "").strip()
    if not text:
        raise ValueError("Feishu IM message create requires text.")
    query = {"receive_id_type": receive_id_type}
    _maybe_set(query, "uuid", params.get("idempotency_key") or params.get("uuid"))
    return _tenant_http_request(
        app_config,
        "POST",
        "/open-apis/im/v1/messages",
        params=query,
        payload={"receive_id": receive_id, "msg_type": "text", "content": _json_arg({"text": text})},
        label="Feishu IM message create",
    )


def _format_im_chat_items(payload: Any, params: dict[str, Any], *, source_label: str, query: str) -> str:
    data = payload.get("data") if isinstance(payload, dict) else {}
    items = data.get("items") if isinstance(data, dict) and isinstance(data.get("items"), list) else []
    normalized = {
        "available": True,
        "source": source_label,
        "query": query,
        "items": items,
        "chats": items,
        "has_more": bool(data.get("has_more")) if isinstance(data, dict) else False,
        "page_token": str(data.get("page_token") or "") if isinstance(data, dict) else "",
    }
    if _raw_json_response_requested(params):
        return _json_arg(normalized)
    titles = [_cli_im_title(item) for item in items[:5]]
    titles = [title for title in titles if title]
    if titles:
        return f"飞书群聊已通过 {source_label} 读取 {len(items)} 个：{', '.join(titles)}"
    return f"飞书群聊已通过 {source_label} 读取 {len(items)} 个。"


def _format_im_create_chat(payload: Any, params: dict[str, Any], *, source_label: str) -> str:
    if _raw_json_response_requested(params):
        return _json_arg(payload)
    chat_id = _cli_chat_id(payload)
    if chat_id:
        return f"飞书群已通过 {source_label} 创建：{chat_id}"
    return f"飞书群已通过 {source_label} 创建。"


def _format_im_send_message(payload: Any, params: dict[str, Any], *, source_label: str) -> str:
    if _raw_json_response_requested(params):
        return _json_arg(payload)
    message_id = _cli_message_id(payload)
    if message_id:
        return f"飞书消息已通过 {source_label} 发送：{message_id}"
    return f"飞书消息已通过 {source_label} 发送。"


def _format_contact_department_children(payload: Any, params: dict[str, Any], *, source_label: str) -> str:
    if _raw_json_response_requested(params):
        return _json_arg(payload)
    items = _cli_contact_items(payload, keys=("items", "departments"))
    titles = [_cli_contact_title(item, ("name", "department_id", "open_department_id")) for item in items[:5]]
    titles = [title for title in titles if title]
    if titles:
        return f"飞书通讯录子部门已通过 {source_label} 读取 {len(items)} 条：{', '.join(titles)}"
    return f"飞书通讯录子部门已通过 {source_label} 读取 {len(items)} 条。"


def _format_contact_department_users(payload: Any, params: dict[str, Any], *, source_label: str) -> str:
    if _raw_json_response_requested(params):
        return _json_arg(payload)
    items = _cli_contact_items(payload, keys=("items", "users"))
    titles = [_cli_contact_title(item, ("name", "en_name", "open_id", "user_id")) for item in items[:5]]
    titles = [title for title in titles if title]
    if titles:
        return f"飞书通讯录部门用户已通过 {source_label} 读取 {len(items)} 条：{', '.join(titles)}"
    return f"飞书通讯录部门用户已通过 {source_label} 读取 {len(items)} 条。"


def _format_contact_scope_list(payload: Any, params: dict[str, Any], *, source_label: str) -> str:
    data = payload.get("data") if isinstance(payload, dict) else {}
    data = data if isinstance(data, dict) else {}
    department_count = len(data.get("department_ids") or [])
    user_count = len(data.get("user_ids") or [])
    group_count = len(data.get("group_ids") or [])
    if _raw_json_response_requested(params):
        return _json_arg(payload)
    return f"飞书通讯录授权范围已通过 {source_label} 读取：部门 {department_count} 个，用户 {user_count} 个，用户组 {group_count} 个。"


def _format_contact_user_get(payload: Any, params: dict[str, Any], *, user_id: str, source_label: str) -> str:
    if _raw_json_response_requested(params):
        return _json_arg(payload)
    data = payload.get("data") if isinstance(payload, dict) else {}
    user = data.get("user") if isinstance(data, dict) else {}
    if not isinstance(user, dict):
        user = {}
    title = _cli_contact_title(user, ("name", "en_name", "open_id", "user_id")) or user_id
    email = str(user.get("email") or "").strip()
    mobile = str(user.get("mobile") or "").strip()
    detail = "，".join(item for item in (email, mobile) if item)
    if detail:
        return f"飞书通讯录用户已通过 {source_label} 读取：{title}（{detail}）"
    return f"飞书通讯录用户已通过 {source_label} 读取：{title}"


def _execute_tenant_contact_user_search(app_config: FeishuAppConfig, request: ToolRequest) -> str:
    params = request.params
    keyword = str(params.get("keyword") or params.get("query") or "").strip()
    if not keyword:
        keyword = request.question.replace("公司", "").replace("的", "").replace("是谁", "").replace("谁是", "").strip()
    if not keyword:
        return "请提供搜索关键词。"
    snapshot_params = {**params, "response_format": "raw_json", "max_departments": 200, "max_users": 1000}
    payload = json.loads(
        _execute_tenant_contact_organization_snapshot(
            app_config,
            ToolRequest(
                tool_name="feishu_contact_organization_snapshot",
                question=request.question,
                normalized_command=request.normalized_command,
                params=snapshot_params,
            ),
        )
    )
    users = payload.get("users") if isinstance(payload, dict) else []
    users = users if isinstance(users, list) else []
    lowered = keyword.lower()
    matches = [
        user
        for user in users
        if isinstance(user, dict)
        and (
            lowered in str(user.get("name") or "").lower()
            or lowered in str(user.get("en_name") or "").lower()
            or lowered in str(user.get("title") or user.get("job_title") or "").lower()
            or lowered in str(user.get("email") or "").lower()
        )
    ]
    if _raw_json_response_requested(params):
        return _json_arg({"result_type": "people_search", "keyword": keyword, "items": matches, "users": matches})
    if not matches:
        return f"未找到匹配 [{keyword}] 的用户。"
    lines_out = [f"搜索 [{keyword}] 找到 {len(matches)} 人："]
    for user in matches[:20]:
        name = user.get("name") or "?"
        title = user.get("title") or user.get("job_title") or ""
        mobile = user.get("mobile") or ""
        email = user.get("email") or ""
        line = f"  {name}"
        if title:
            line += f" - {title}"
        if mobile:
            line += f" | {mobile}"
        if email:
            line += f" | {email}"
        lines_out.append(line)
    return chr(10).join(lines_out)


def _execute_tenant_contact_organization_snapshot(app_config: FeishuAppConfig, request: ToolRequest) -> str:
    params = request.params
    return _execute_contact_organization_snapshot_with_runner(
        params,
        children=lambda item, department_id: _tenant_contact_department_children(app_config, item, department_id=department_id),
        users=lambda item, department_id: _tenant_contact_department_users(app_config, item, department_id=department_id),
        scope=lambda item: _tenant_contact_scope_list(app_config, item),
        source_label="Tenant Token",
    )



def _execute_cli_contact_user_search(request: ToolRequest) -> str:
    """Search for users by keyword."""
    params = request.params
    keyword = str(params.get("keyword") or params.get("query") or "").strip()
    if not keyword:
        keyword = request.question.replace("公司", "").replace("的", "").replace("是谁", "").replace("谁是", "").strip()
    if not keyword:
        return "请提供搜索关键词。"
    try:
        _args = _cli_args_with_profile(["lark-cli", "contact", "+search-user", "--query", keyword, "--as", "bot", "--format", "json"])
        sdata = _run_lark_cli_json_loose_base(_args, action="contact user search", timeout=15)
        if sdata.get("ok") and sdata.get("data", {}).get("items"):
            items = sdata["data"]["items"]
            if _raw_json_response_requested(params):
                return _json_arg({"result_type": "people_search", "keyword": keyword, "items": items, "users": items})
            lines_out = [f"搜索 [{keyword}] 找到 {len(items)} 人："]
            for u in items[:20]:
                name = u.get("name") or "?"
                title = u.get("title") or u.get("job_title") or ""
                mobile = u.get("mobile") or ""
                email = u.get("email") or ""
                line = f"  {name}"
                if title:
                    line += f" - {title}"
                if mobile:
                    line += f" | {mobile}"
                if email:
                    line += f" | {email}"
                lines_out.append(line)
            return chr(10).join(lines_out)
    except Exception:
        pass
    try:
        _args = _cli_args_with_profile([
            "lark-cli",
            "api",
            "GET",
            "/open-apis/contact/v3/departments/0/children",
            "--as",
            "bot",
            "--format",
            "json",
            "--params",
            json.dumps({
                "department_id_type": "department_id",
                "user_id_type": "open_id",
                "page_size": 50,
                "fetch_child": True,
            }),
        ])
        dd = _run_lark_cli_json_loose_base(_args, action="contact department children", timeout=30)
        di = dd.get("data", {}).get("items", [])
        dids = ["0"] + [str(d.get("department_id", "")) for d in di if d.get("department_id")]
        all_u = []
        seen = set()
        import concurrent.futures as _cf
        def _fu(did):
            try:
                _a2 = _cli_args_with_profile([
                    "lark-cli",
                    "api",
                    "GET",
                    "/open-apis/contact/v3/users/find_by_department",
                    "--as",
                    "bot",
                    "--format",
                    "json",
                    "--params",
                    json.dumps({
                        "department_id": did,
                        "department_id_type": "department_id",
                        "user_id_type": "open_id",
                        "page_size": 50,
                    }),
                ])
                return _run_lark_cli_json_loose_base(_a2, action="contact department users", timeout=30).get("data", {}).get("items", [])
            except Exception:
                return []
        with _cf.ThreadPoolExecutor(max_workers=12) as pool:
            for _its in pool.map(_fu, dids):
                for u in _its:
                    oid = u.get("open_id", "")
                    if oid and oid not in seen:
                        seen.add(oid)
                        all_u.append(u)
        kl = keyword.lower()
        mt = [u for u in all_u if kl in (u.get("name", "") or "").lower() or kl in (u.get("title", "") or "").lower()]
        if not mt:
            if _raw_json_response_requested(params):
                return _json_arg({"result_type": "people_search", "keyword": keyword, "items": [], "users": []})
            return f"未找到匹配 [{keyword}] 的用户。"
        if _raw_json_response_requested(params):
            return _json_arg({"result_type": "people_search", "keyword": keyword, "items": mt, "users": mt})
        lines_out = [f"搜索 [{keyword}] 找到 {len(mt)} 人："]
        for u in mt[:20]:
            name = u.get("name") or "?"
            title = u.get("title") or u.get("job_title") or ""
            mobile = u.get("mobile") or ""
            email = u.get("email") or ""
            line = f"  {name}"
            if title:
                line += f" - {title}"
            if mobile:
                line += f" | {mobile}"
            if email:
                line += f" | {email}"
            lines_out.append(line)
        return chr(10).join(lines_out)
    except Exception:
        if _raw_json_response_requested(params):
            return _json_arg({"result_type": "people_search", "keyword": keyword, "items": [], "users": [], "error": "search_failed"})
        return f"搜索 [{keyword}] 时出错，请稍后重试。"


def _execute_cli_contact_user_get(request: ToolRequest) -> str:
    params = request.params
    user_id = _required_cli_str(params, "user_id", aliases=("open_id",))
    query = {
        "user_id_type": _cli_user_id_type(params, label="api contact user get"),
        "department_id_type": str(params.get("department_id_type") or "open_department_id"),
    }
    payload = _run_lark_cli_json(
        _contact_api_get_args(
            params,
            "/open-apis/contact/v3/users/" + user_id,
            query,
            label="api contact user get",
        ),
        action="api contact user get",
    )
    if _raw_json_response_requested(params):
        return _json_arg(payload)
    data = payload.get("data") if isinstance(payload, dict) else {}
    user = data.get("user") if isinstance(data, dict) else {}
    if not isinstance(user, dict):
        user = {}
    title = _cli_contact_title(user, ("name", "en_name", "open_id", "user_id")) or user_id
    email = str(user.get("email") or "").strip()
    mobile = str(user.get("mobile") or "").strip()
    detail = "，".join(item for item in (email, mobile) if item)
    if detail:
        return f"飞书通讯录用户已通过 CLI 读取：{title}（{detail}）"
    return f"飞书通讯录用户已通过 CLI 读取：{title}"

def _execute_cli_contact_organization_snapshot(request: ToolRequest) -> str:
    params = request.params
    return _execute_contact_organization_snapshot_with_runner(
        params,
        children=lambda item, department_id: _run_contact_department_children(item, department_id=department_id),
        users=lambda item, department_id: _run_contact_department_users(item, department_id=department_id),
        scope=_run_contact_scope_list_payload,
        source_label="CLI",
    )


def _execute_contact_organization_snapshot_with_runner(
    params: dict[str, Any],
    *,
    children: Callable[[dict[str, Any], str], Any],
    users: Callable[[dict[str, Any], str], Any],
    scope: Callable[[dict[str, Any]], Any],
    source_label: str,
) -> str:
    max_departments = _optional_cli_int(params, "max_departments", default=100, minimum=1, maximum=500)
    max_users = _optional_cli_int(params, "max_users", default=500, minimum=1, maximum=2000)
    root_department_id = str(params.get("root_department_id") or params.get("department_id") or "0")
    departments: list[dict[str, Any]] = []
    users_by_open_id: dict[str, dict[str, Any]] = {}
    queue = [root_department_id]
    seen_departments = set(queue)
    department_fetch_errors: list[dict[str, str]] = []

    snapshot_params = {**params, "department_id_type": "department_id", "user_id_type": "open_id", "page_size": 50}
    while queue and len(departments) < max_departments:
        department_id = queue.pop(0)
        try:
            department_items = _contact_paged_items(
                lambda page_params, current_department_id=department_id: children(page_params, current_department_id),
                snapshot_params,
                keys=("items", "departments"),
                max_items=max_departments - len(departments),
            )
        except Exception as exc:
            department_fetch_errors.append({"department_id": department_id, "error": str(exc)[:200]})
            continue
        for department in department_items:
            departments.append(department)
            child_id = str(department.get("department_id") or department.get("open_department_id") or "").strip()
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
    user_department_ids = [
        root_department_id,
        *[str(item.get("department_id")) for item in departments if item.get("department_id")],
    ]
    def _department_users(department_id: str) -> tuple[str, list[dict[str, Any]]]:
        return department_id, _contact_paged_items(
            lambda page_params: users(page_params, department_id),
            snapshot_params,
            keys=("items", "users"),
            max_items=max_users,
        )

    user_fetch_errors: list[dict[str, str]] = []
    for department_id in user_department_ids:
        if len(users_by_open_id) >= max_users:
            break
        try:
            department_id, department_users = _department_users(department_id)
        except Exception as exc:
            user_fetch_errors.append({"department_id": department_id, "error": str(exc)[:200]})
            continue
        for user in department_users:
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
    if not departments:
        scoped_departments, scoped_users = _contact_snapshot_from_authorized_scope(
            params,
            max_departments=max_departments,
            max_users=max_users,
            scope=scope,
            users=users,
        )
        departments = scoped_departments
        users_by_open_id = {**scoped_users, **users_by_open_id}
    if _raw_json_response_requested(params):
        return _json_arg(
            {
                "available": True,
                "root_department_id": root_department_id,
                "department_count": len(departments),
                "user_count": len(users_by_open_id),
                "departments": departments,
                "users": list(users_by_open_id.values()),
                "department_fetch_errors": department_fetch_errors,
                "user_fetch_errors": user_fetch_errors,
                "_runtime_v5_snapshot_version": 3,
            }
        )
    return f"飞书通讯录组织快照已通过 {source_label} 读取：部门 {len(departments)} 个，人员 {len(users_by_open_id)} 人。"


def _contact_snapshot_from_authorized_scope(
    params: dict[str, Any],
    *,
    max_departments: int,
    max_users: int,
    scope: Callable[[dict[str, Any]], Any] | None = None,
    users: Callable[[dict[str, Any], str], Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    scope_runner = scope or _run_contact_scope_list_payload
    users_runner = users or (lambda item, department_id: _run_contact_department_users(item, department_id=department_id))
    department_ids, user_ids = _contact_authorized_scope_ids(
        scope_runner,
        params,
        max_departments=max_departments,
        max_users=max_users,
    )
    departments = [{"open_department_id": item, "department_id": item} for item in department_ids[:max_departments]]
    users_by_open_id: dict[str, dict[str, Any]] = {item: {"open_id": item} for item in user_ids[:max_users]}
    if not department_ids or len(users_by_open_id) >= max_users:
        return departments, users_by_open_id

    scoped_params = {**params, "department_id_type": "open_department_id", "user_id_type": "open_id", "page_size": 50}
    import concurrent.futures as _cf

    def _department_users(department_id: str) -> list[dict[str, Any]]:
        try:
            return _contact_paged_items(
                lambda page_params: users_runner(page_params, department_id),
                scoped_params,
                keys=("items", "users"),
                max_items=max_users,
            )
        except Exception:
            return []

    with _cf.ThreadPoolExecutor(max_workers=min(12, max(1, len(department_ids)))) as pool:
        for users in pool.map(_department_users, department_ids[:max_departments]):
            if len(users_by_open_id) >= max_users:
                break
            for user in users:
                open_id = str(user.get("open_id") or user.get("user_id") or "").strip()
                if not open_id:
                    continue
                users_by_open_id[open_id] = {**users_by_open_id.get(open_id, {}), **user}
                if len(users_by_open_id) >= max_users:
                    break
    return departments, users_by_open_id


def _contact_paged_items(
    runner: Callable[[dict[str, Any]], Any],
    params: dict[str, Any],
    *,
    keys: tuple[str, ...],
    max_items: int,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page_token = str(params.get("page_token") or "").strip()
    seen_tokens: set[str] = set()
    while len(items) < max_items:
        page_params = {**params}
        if page_token:
            page_params["page_token"] = page_token
        else:
            page_params.pop("page_token", None)
        payload = runner(page_params)
        items.extend(_cli_contact_items(payload, keys=keys)[: max_items - len(items)])
        data = payload.get("data") if isinstance(payload, dict) else {}
        data = data if isinstance(data, dict) else {}
        next_token = str(data.get("page_token") or "").strip()
        if not data.get("has_more") or not next_token or next_token in seen_tokens:
            break
        seen_tokens.add(next_token)
        page_token = next_token
    return items


def _contact_authorized_scope_ids(
    scope_runner: Callable[[dict[str, Any]], Any],
    params: dict[str, Any],
    *,
    max_departments: int,
    max_users: int,
) -> tuple[list[str], list[str]]:
    department_ids: list[str] = []
    user_ids: list[str] = []
    page_token = str(params.get("page_token") or "").strip()
    seen_tokens: set[str] = set()
    while len(department_ids) < max_departments or len(user_ids) < max_users:
        page_params = {**params}
        if page_token:
            page_params["page_token"] = page_token
        else:
            page_params.pop("page_token", None)
        scope_payload = scope_runner(page_params)
        data = scope_payload.get("data") if isinstance(scope_payload, dict) else {}
        data = data if isinstance(data, dict) else {}
        for item in data.get("department_ids") or []:
            value = str(item).strip()
            if value and value not in department_ids and len(department_ids) < max_departments:
                department_ids.append(value)
        for item in data.get("user_ids") or []:
            value = str(item).strip()
            if value and value not in user_ids and len(user_ids) < max_users:
                user_ids.append(value)
        next_token = str(data.get("page_token") or "").strip()
        if not data.get("has_more") or not next_token or next_token in seen_tokens:
            break
        seen_tokens.add(next_token)
        page_token = next_token
    return department_ids, user_ids


def _execute_cli_task_update(request: ToolRequest) -> str:
    params = request.params
    payload = _cli_task_update_payload(params)
    args = [
        "lark-cli",
        "task",
        "+update",
        "--as",
        _cli_identity(params, default="user", label="task +update"),
        "--format",
        "json",
        "--task-id",
        _required_cli_str(params, "task_guid", aliases=("task_id",)),
        "--data",
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str),
    ]
    title = _cli_task_title(_run_lark_cli_json(args, action="task +update")) or str(payload.get("summary") or "").strip()
    return f"飞书任务已通过 CLI 更新：{title}" if title else "飞书任务已通过 CLI 更新。"


def _execute_cli_task_delete(request: ToolRequest) -> str:
    params = request.params
    task_id = _required_cli_str(params, "task_guid", aliases=("task_id",))
    args = [
        "lark-cli",
        "task",
        "tasks",
        "delete",
        "--as",
        _cli_identity(params, default="user", label="task tasks delete"),
        "--format",
        "json",
        "--params",
        _json_arg({"task_guid": task_id}),
        "--yes",
    ]
    _run_lark_cli_json(args, action="task tasks delete")
    return f"飞书任务已通过 CLI 删除：{task_id}"


def _execute_cli_task_complete(request: ToolRequest) -> str:
    params = request.params
    task_id = _required_cli_str(params, "task_guid", aliases=("task_id",))
    args = [
        "lark-cli",
        "task",
        "+complete",
        "--as",
        _cli_identity(params, default="user", label="task +complete"),
        "--format",
        "json",
        "--task-id",
        task_id,
    ]
    title = _cli_task_title(_run_lark_cli_json(args, action="task +complete")) or task_id
    return f"飞书任务已通过 CLI 完成：{title}"


def _execute_cli_task_reopen(request: ToolRequest) -> str:
    params = request.params
    task_id = _required_cli_str(params, "task_guid", aliases=("task_id",))
    args = [
        "lark-cli",
        "task",
        "+reopen",
        "--as",
        _cli_identity(params, default="user", label="task +reopen"),
        "--format",
        "json",
        "--task-id",
        task_id,
    ]
    title = _cli_task_title(_run_lark_cli_json(args, action="task +reopen")) or task_id
    return f"飞书任务已通过 CLI 重新打开：{title}"


def _execute_cli_task_comment(request: ToolRequest) -> str:
    params = request.params
    task_id = _required_cli_str(params, "task_guid", aliases=("task_id",))
    args = [
        "lark-cli",
        "task",
        "+comment",
        "--as",
        _cli_identity(params, default="user", label="task +comment"),
        "--format",
        "json",
        "--task-id",
        task_id,
        "--content",
        _required_cli_str(params, "content"),
    ]
    _run_lark_cli_json(args, action="task +comment")
    return f"飞书任务评论已通过 CLI 添加：{task_id}"


def _execute_cli_task_upload_attachment(request: ToolRequest) -> str:
    params = request.params
    task_id = _required_cli_str(params, "resource_id", aliases=("task_guid", "task_id"))
    resource_type = _optional_allowed_cli_str(params, "resource_type", {"task", "task_delivery"}, default="task")
    file_path = _required_cli_str(params, "file_path", aliases=("file",))
    args = [
        "lark-cli",
        "task",
        "+upload-attachment",
        "--as",
        _cli_identity(params, default="user", label="task +upload-attachment"),
        "--format",
        "json",
        "--resource-id",
        task_id,
        "--resource-type",
        resource_type or "task",
        "--file",
        file_path,
    ]
    user_id_type = str(params.get("user_id_type") or "open_id").strip()
    if user_id_type:
        args.extend(["--user-id-type", user_id_type])
    _run_lark_cli_json(args, action="task +upload-attachment")
    return f"飞书任务附件已通过 CLI 上传：{task_id}"


def _execute_cli_task_subtask_create(request: ToolRequest) -> str:
    params = request.params
    parent_id = _required_cli_str(params, "parent_task_guid", aliases=("parent_guid",))
    payload = _cli_task_create_payload(params)
    params_payload = {
        "task_guid": parent_id,
        "user_id_type": str(params.get("user_id_type") or "open_id"),
    }
    args = [
        "lark-cli",
        "task",
        "subtasks",
        "create",
        "--as",
        _cli_identity(params, default="user", label="task subtasks create"),
        "--format",
        "json",
        "--params",
        _json_arg(params_payload),
        "--data",
        _json_arg(payload),
    ]
    title = _cli_task_title(_run_lark_cli_json(args, action="task subtasks create")) or str(
        payload.get("summary") or ""
    ).strip()
    return f"飞书子任务已通过 CLI 创建：{title}" if title else "飞书子任务已通过 CLI 创建。"


def _execute_cli_tasklist_create(request: ToolRequest) -> str:
    params = request.params
    name = _required_cli_str(params, "name")
    if params.get("archive_tasklist") is not None:
        return _execute_cli_tasklist_create_with_schema(params, name)
    args = [
        "lark-cli",
        "task",
        "+tasklist-create",
        "--as",
        _cli_identity(params, default="user", label="task +tasklist-create"),
        "--format",
        "json",
        "--name",
        name,
    ]
    members = _string_list_value(params.get("editors") or params.get("member_ids"))
    if members:
        args.extend(["--member", ",".join(members)])
    data = params.get("data") if isinstance(params.get("data"), list) else params.get("tasks")
    if isinstance(data, list) and data:
        args.extend(["--data", _json_arg(data)])
    title = _cli_tasklist_title(_run_lark_cli_json(args, action="task +tasklist-create")) or name
    return f"飞书任务清单已通过 CLI 创建：{title}"


def _execute_cli_tasklist_create_with_schema(params: dict[str, Any], name: str) -> str:
    if isinstance(params.get("data"), list) or isinstance(params.get("tasks"), list):
        raise ValueError("Feishu CLI task tasklists create verified schema does not support initial tasks.")
    data: dict[str, Any] = {
        "name": name,
        "archive_tasklist": _required_cli_bool(params, "archive_tasklist"),
    }
    members = _tasklist_create_members(params)
    if members:
        data["members"] = members
    args = [
        "lark-cli",
        "task",
        "tasklists",
        "create",
        "--as",
        _cli_identity(params, default="user", label="task tasklists create"),
        "--format",
        "json",
        "--params",
        _json_arg({"user_id_type": str(params.get("user_id_type") or "open_id")}),
        "--data",
        _json_arg(data),
    ]
    title = _cli_tasklist_title(_run_lark_cli_json(args, action="task tasklists create")) or name
    return f"飞书任务清单已通过 CLI 创建：{title}"


def _execute_cli_tasklist_delete(request: ToolRequest) -> str:
    params = request.params
    tasklist_id = _required_cli_str(params, "tasklist_guid", aliases=("tasklist_id",))
    args = [
        "lark-cli",
        "task",
        "tasklists",
        "delete",
        "--as",
        _cli_identity(params, default="user", label="task tasklists delete"),
        "--format",
        "json",
        "--params",
        _json_arg({"tasklist_guid": tasklist_id}),
        "--yes",
    ]
    _run_lark_cli_json(args, action="task tasklists delete")
    return f"飞书任务清单已通过 CLI 删除：{tasklist_id}"


def _execute_cli_tasklist_update(request: ToolRequest) -> str:
    params = request.params
    tasklist_id = _required_cli_str(params, "tasklist_guid", aliases=("tasklist_id",))
    name = _required_cli_str(params, "name")
    args = [
        "lark-cli",
        "task",
        "tasklists",
        "patch",
        "--as",
        _cli_identity(params, default="user", label="task tasklists patch"),
        "--format",
        "json",
        "--params",
        _json_arg({"tasklist_guid": tasklist_id, "user_id_type": str(params.get("user_id_type") or "open_id")}),
        "--data",
        _json_arg({"tasklist": {"name": name}, "update_fields": ["name"]}),
    ]
    title = _cli_tasklist_title(_run_lark_cli_json(args, action="task tasklists patch")) or name
    return f"飞书任务清单已通过 CLI 更新：{title}"


def _execute_cli_task_section_create(request: ToolRequest) -> str:
    params = request.params
    data = _cli_task_section_create_data(params)
    args = [
        "lark-cli",
        "task",
        "sections",
        "create",
        "--as",
        _cli_identity(params, default="user", label="task sections create"),
        "--format",
        "json",
        "--params",
        _json_arg({"user_id_type": str(params.get("user_id_type") or "open_id")}),
        "--data",
        _json_arg(data),
    ]
    title = _cli_task_section_title(_run_lark_cli_json(args, action="task sections create")) or str(
        data.get("name") or ""
    )
    return f"飞书任务分组已通过 CLI 创建：{title}" if title else "飞书任务分组已通过 CLI 创建。"


def _execute_cli_task_section_update(request: ToolRequest) -> str:
    params = request.params
    section_id = _required_cli_str(params, "section_guid", aliases=("section_id",))
    data = _cli_task_section_update_data(params)
    args = [
        "lark-cli",
        "task",
        "sections",
        "patch",
        "--as",
        _cli_identity(params, default="user", label="task sections patch"),
        "--format",
        "json",
        "--params",
        _json_arg({"section_guid": section_id, "user_id_type": str(params.get("user_id_type") or "open_id")}),
        "--data",
        _json_arg(data),
    ]
    title = _cli_task_section_title(_run_lark_cli_json(args, action="task sections patch")) or str(
        data["section"].get("name") or section_id
    )
    return f"飞书任务分组已通过 CLI 更新：{title}"


def _execute_cli_task_section_delete(request: ToolRequest) -> str:
    params = request.params
    section_id = _required_cli_str(params, "section_guid", aliases=("section_id",))
    args = [
        "lark-cli",
        "task",
        "sections",
        "delete",
        "--as",
        _cli_identity(params, default="user", label="task sections delete"),
        "--format",
        "json",
        "--params",
        _json_arg({"section_guid": section_id}),
        "--yes",
    ]
    _run_lark_cli_json(args, action="task sections delete")
    return f"飞书任务分组已通过 CLI 删除：{section_id}"


def _execute_cli_task_add_to_tasklist(request: ToolRequest) -> str:
    params = request.params
    task_id = _required_cli_str(params, "task_guid", aliases=("task_id",))
    tasklist_id = _required_cli_str(params, "tasklist_guid", aliases=("tasklist_id",))
    args = [
        "lark-cli",
        "task",
        "+tasklist-task-add",
        "--as",
        _cli_identity(params, default="user", label="task +tasklist-task-add"),
        "--format",
        "json",
        "--tasklist-id",
        tasklist_id,
        "--task-id",
        task_id,
    ]
    section_guid = str(params.get("section_guid") or "").strip()
    if section_guid:
        args.extend(["--section-guid", section_guid])
    _run_lark_cli_json(args, action="task +tasklist-task-add")
    return f"飞书任务已通过 CLI 加入清单：{tasklist_id}"


def _execute_cli_task_set_ancestor(request: ToolRequest) -> str:
    params = request.params
    task_id = _required_cli_str(params, "task_guid", aliases=("task_id",))
    ancestor_id = _required_cli_str(params, "ancestor_guid", aliases=("ancestor_task_guid", "ancestor_id"))
    args = [
        "lark-cli",
        "task",
        "+set-ancestor",
        "--as",
        _cli_identity(params, default="user", label="task +set-ancestor"),
        "--format",
        "json",
        "--task-id",
        task_id,
        "--ancestor-id",
        ancestor_id,
    ]
    _run_lark_cli_json(args, action="task +set-ancestor")
    return f"飞书任务已通过 CLI 设置父任务：{ancestor_id}"


def _execute_cli_task_clear_ancestor(request: ToolRequest) -> str:
    params = request.params
    task_id = _required_cli_str(params, "task_guid", aliases=("task_id",))
    args = [
        "lark-cli",
        "task",
        "+set-ancestor",
        "--as",
        _cli_identity(params, default="user", label="task +set-ancestor"),
        "--format",
        "json",
        "--task-id",
        task_id,
    ]
    _run_lark_cli_json(args, action="task +set-ancestor")
    return "飞书任务父任务关系已通过 CLI 清空。"


def _execute_cli_tasklist_update_members(request: ToolRequest) -> str:
    return _execute_cli_tasklist_members(request, mode="update")


def _execute_cli_tasklist_set_members(request: ToolRequest) -> str:
    return _execute_cli_tasklist_members(request, mode="set")


def _execute_cli_tasklist_members(request: ToolRequest, *, mode: str) -> str:
    params = request.params
    tasklist_id = _required_cli_str(params, "tasklist_guid", aliases=("tasklist_id",))
    args = [
        "lark-cli",
        "task",
        "+tasklist-members",
        "--as",
        _cli_identity(params, default="user", label="task +tasklist-members"),
        "--format",
        "json",
        "--tasklist-id",
        tasklist_id,
    ]
    if mode == "set":
        set_members = _string_list_value(params.get("set_members") or params.get("set"))
        if not set_members:
            raise ValueError("Feishu CLI task +tasklist-members requires set_members.")
        args.extend(["--set", ",".join(set_members)])
        _run_lark_cli_json(args, action="task +tasklist-members")
        return f"飞书任务清单成员已通过 CLI 全量替换：{len(set_members)} 个。"
    add_members = _string_list_value(params.get("add_members") or params.get("add"))
    remove_members = _string_list_value(params.get("remove_members") or params.get("remove"))
    if not add_members and not remove_members:
        raise ValueError("Feishu CLI task +tasklist-members requires add_members or remove_members.")
    if add_members:
        args.extend(["--add", ",".join(add_members)])
    if remove_members:
        args.extend(["--remove", ",".join(remove_members)])
    _run_lark_cli_json(args, action="task +tasklist-members")
    return f"飞书任务清单成员已通过 CLI 更新：新增 {len(add_members)} 个，移除 {len(remove_members)} 个。"


def _execute_cli_task_membership(request: ToolRequest, *, action: str, add_key: str, remove_key: str) -> str:
    params = request.params
    task_id = _required_cli_str(params, "task_guid", aliases=("task_id",))
    add_items = _string_list_value(params.get(add_key) or params.get("add"))
    remove_items = _string_list_value(params.get(remove_key) or params.get("remove"))
    if not add_items and not remove_items:
        raise ValueError(f"Feishu CLI task {action} requires {add_key} or {remove_key}.")
    args = [
        "lark-cli",
        "task",
        action,
        "--as",
        _cli_identity(params, default="user", label=f"task {action}"),
        "--format",
        "json",
        "--task-id",
        task_id,
    ]
    if add_items:
        args.extend(["--add", ",".join(add_items)])
    if remove_items:
        args.extend(["--remove", ",".join(remove_items)])
    idempotency_key = str(params.get("client_token") or params.get("idempotency_key") or "").strip()
    if idempotency_key:
        args.extend(["--idempotency-key", idempotency_key])
    _run_lark_cli_json(args, action=f"task {action}")
    noun = "负责人" if action == "+assign" else "关注人"
    return f"飞书任务{noun}已通过 CLI 更新：新增 {len(add_items)} 个，移除 {len(remove_items)} 个。"


def _execute_cli_task_reminder(request: ToolRequest) -> str:
    params = request.params
    task_id = _required_cli_str(params, "task_guid", aliases=("task_id",))
    args = [
        "lark-cli",
        "task",
        "+reminder",
        "--as",
        _cli_identity(params, default="user", label="task +reminder"),
        "--format",
        "json",
        "--task-id",
        task_id,
    ]
    reminder_value = _cli_reminder_set_value(params)
    if reminder_value:
        args.extend(["--set", reminder_value])
    else:
        args.append("--remove")
    _run_lark_cli_json(args, action="task +reminder")
    return f"飞书任务提醒已通过 CLI {'更新' if reminder_value else '清空'}：{task_id}"


def _execute_cli_approval_instances_initiated(request: ToolRequest) -> str:
    params = request.params
    query: dict[str, Any] = {
        "page_size": _optional_cli_int(params, "page_size", default=20, minimum=1, maximum=200),
        "user_id_type": _approval_user_id_type(params),
    }
    definition_code = str(params.get("definition_code") or params.get("approval_code") or "").strip()
    if definition_code:
        query["definition_code"] = definition_code
    for key in ("locale", "page_token"):
        value = str(params.get(key) or "").strip()
        if value:
            query[key] = value
    payload = _run_lark_cli_json(
        [
            "lark-cli",
            "approval",
            "instances",
            "initiated",
            "--as",
            _cli_identity(params, default="user", label="approval instances initiated"),
            "--format",
            "json",
            "--params",
            _json_arg(query),
        ],
        action="approval instances initiated",
    )
    data = payload.get("data") if isinstance(payload, dict) else {}
    instances = data.get("instances") if isinstance(data, dict) else []
    items = instances if isinstance(instances, list) else []
    titles = [_cli_approval_instance_title(item) for item in items[:3]]
    titles = [title for title in titles if title]
    if titles:
        return f"飞书已发起审批实例已通过 CLI 读取 {len(items)} 条：{', '.join(titles)}"
    return f"飞书已发起审批实例已通过 CLI 读取 {len(items)} 条。"



def _cli_approval_instance_query_items(payload: Any) -> list[Any]:
    """Extract instance_list from instances/query response."""
    if not isinstance(payload, dict):
        return []
    if payload.get("code") != 0:
        return []
    data = payload.get("data")
    if isinstance(data, dict):
        instance_list = data.get("instance_list")
        if isinstance(instance_list, list):
            return instance_list
    return []


def _execute_approval_task_query_with_context(request: ToolRequest, context: ToolContext) -> str:
    """Query approval tasks using the user's OAuth access token from the Account table.
    """
    return _execute_approval_task_query_impl(request, context)


def _execute_approval_task_query_impl(request: ToolRequest, context: ToolContext) -> str:
    """Internal implementation of approval task query with OAuth token.

    Uses the user's access_token obtained through the application's OAuth flow,
    stored in the Account table. Replaces the lark-cli --as user approach which
    uses lark-cli's own stored user token (single-user limit).
    """
    params = request.params
    db = context.db
    open_id = context.actor.open_id
    company_id = context.company_id

    if db is None or not open_id:
        return "审批查询需要使用个人飞书授权。请先通过大飞哥完成飞书 OAuth 授权。"

    topic = str(params.get("topic") or "1").strip()
    if topic not in {"1", "2", "3", "17", "18"}:
        return f"审批分组参数无效：{topic}"

    accounts = db.scalars(
        select(Account)
        .where(Account.company_id == company_id)
        .where(Account.provider == "feishu_user")
        .where(Account.is_active.is_(True))
    ).all()

    matched_account = None
    user_token = None
    for account in accounts:
        settings = account.settings or {}
        if str(settings.get("open_id") or "") != open_id:
            continue
        matched_account = account
        credentials = account.credentials or {}
        token = credentials.get("access_token")
        expires_at = credentials.get("expires_at")
        if token and not _feishu_token_expired(expires_at):
            user_token = str(token)
            break

    if not user_token and matched_account:
        new_token = _refresh_account_token(matched_account, company_id=company_id, db=db)
        if new_token:
            user_token = new_token

    if not user_token:
        return (
            "暂未找到有效的飞书 OAuth 授权。请先完成飞书 OAuth 授权后重试。\n\n"
            "已授权但 Token 已过期的用户，请联系管理员刷新授权。"
        )

    query_params: dict[str, str] = {
        "topic": topic,
        "page_size": str(params.get("page_size", 20)),
        "user_id_type": str(params.get("user_id_type", "open_id")),
    }
    for source, target in (("page_token", "page_token"), ("locale", "locale")):
        value = str(params.get(source) or "").strip()
        if value:
            query_params[target] = value

    try:
        response = httpx.get(
            "https://open.feishu.cn/open-apis/approval/v4/tasks",
            params=query_params,
            headers={"Authorization": f"Bearer {user_token}"},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return f"审批查询请求失败：{exc}"

    if not isinstance(payload, dict):
        return "审批查询返回了无效的响应格式。"

    if payload.get("code", 0) != 0:
        error_msg = payload.get("message") or payload.get("msg", "未知错误")
        return f"审批查询失败（{payload.get('code')}）：{error_msg}"

    items = _cli_approval_task_items(payload)
    if not items:
        return "暂未查询到待审批任务。"

    titles = [_cli_approval_task_title(item) for item in items[:20]]
    titles = [title for title in titles if title]

    if _raw_json_response_requested(params):
        return json.dumps(payload, ensure_ascii=False)

    if titles:
        return f"查询到 {len(items)} 条待审批任务：{'；'.join(titles)}"
    return f"查询到 {len(items)} 条待审批任务。"


def _feishu_token_expired(expires_at: object) -> bool:
    if not expires_at:
        return True
    try:
        expires = datetime.fromisoformat(str(expires_at))
    except ValueError:
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires <= datetime.now(UTC)




def _refresh_account_token(account, *, company_id, db):
    """Synchronous refresh of a Feishu user access_token using its refresh_token."""
    from app.models.entities import FeishuAppConfig
    from app.services.feishu.user_accounts import refresh_feishu_user_account
    
    credentials = account.credentials or {}
    refresh_token = credentials.get("refresh_token")
    if not refresh_token:
        return None
    refresh_expires_at = credentials.get("refresh_expires_at")
    if refresh_expires_at and _feishu_token_expired(refresh_expires_at):
        return None
    app_config = db.scalar(
        select(FeishuAppConfig)
        .where(FeishuAppConfig.company_id == company_id)
        .where(FeishuAppConfig.is_active.is_(True))
    )
    if app_config is None:
        return None
    try:
        tenant_resp = httpx.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_config.app_id, "app_secret": app_config.app_secret},
            timeout=30,
        )
        tenant_resp.raise_for_status()
        tenant_token = (tenant_resp.json() or {}).get("tenant_access_token")
        if not tenant_token:
            return None
        refresh_resp = httpx.post(
            "https://open.feishu.cn/open-apis/authen/v1/refresh_access_token",
            json={"grant_type": "refresh_token", "refresh_token": refresh_token},
            headers={"Authorization": f"Bearer {tenant_token}"},
            timeout=30,
        )
        refresh_resp.raise_for_status()
        token_data = refresh_resp.json()
    except Exception:
        return None
    refresh_feishu_user_account(account, app_config=app_config, token_response=token_data)
    db.flush()
    new_token = (account.credentials or {}).get("access_token")
    return str(new_token) if new_token else None



def _execute_cli_approval_task_query(request: ToolRequest) -> str:
    """Query pending approval tasks using user identity (tasks/query --as user).

    Feishu API requires user_access_token for task-level queries.
    """
    params = request.params
    topic = str(params.get("topic") or "1").strip()
    if topic not in {"1", "2", "3", "17", "18"}:
        raise ValueError("Feishu CLI approval tasks query topic must be one of 1, 2, 3, 17, 18.")
    query: dict[str, Any] = {
        "topic": topic,
        "page_size": _optional_cli_int(params, "page_size", default=20, minimum=1, maximum=200),
        "user_id_type": _approval_user_id_type(params),
    }
    for source, target in (
        ("open_id", "user_id"),
        ("user_id", "user_id"),
        ("definition_code", "definition_code"),
        ("approval_code", "definition_code"),
        ("locale", "locale"),
        ("page_token", "page_token"),
    ):
        value = str(params.get(source) or "").strip()
        if value and target not in query:
            query[target] = value
    try:
        payload = _run_lark_cli_json(
            [
                "lark-cli",
                "approval",
                "tasks",
                "query",
                "--as",
                _cli_identity(params, default="user", label="approval tasks query"),
                "--format",
                "json",
                "--params",
                _json_arg(query),
            ],
            action="approval tasks query",
        )
    except RuntimeError:
        return "审批查询需要先完成飞书授权。请执行 lark-cli auth login 后重试。"
    if _raw_json_response_requested(params):
        return _json_arg(payload)
    items = _cli_approval_task_items(payload)
    titles = [_cli_approval_task_title(item) for item in items[:20]]
    titles = [title for title in titles if title]
    if titles:
        t = ", ".join(titles)
        return f"飞书待审批任务已通过 CLI 读取 {len(items)} 条：{t}"
    return f"飞书待审批任务已通过 CLI 读取 {len(items)} 条。"

def _execute_cli_approval_instance_get(request: ToolRequest) -> str:
    params = request.params
    query: dict[str, Any] = {
        "instance_code": _required_cli_str(params, "instance_code"),
        "user_id_type": _approval_user_id_type(params),
    }
    _maybe_set(query, "locale", params.get("locale"))
    payload = _run_lark_cli_json(
        [
            "lark-cli",
            "approval",
            "instances",
            "get",
            "--as",
            _cli_identity(params, default="user", label="approval instances get"),
            "--format",
            "json",
            "--params",
            _json_arg(query),
        ],
        action="approval instances get",
    )
    if _raw_json_response_requested(params):
        return _json_arg(payload)
    data = payload.get("data") if isinstance(payload, dict) else None
    title = _cli_approval_instance_title(data) if isinstance(data, dict) else _cli_approval_instance_title(payload)
    return f"飞书审批实例已通过 CLI 读取：{title}" if title else "飞书审批实例已通过 CLI 读取。"


def _execute_cli_approval_attachment_download(request: ToolRequest) -> str:
    params = request.params
    token = _required_cli_str(params, "file_token", aliases=("token",))
    name = str(params.get("name") or params.get("filename") or "approval_attachment").strip()
    output_path = _approval_attachment_output_path(name)
    _run_lark_cli_json(
        [
            "lark-cli",
            "drive",
            "+download",
            "--as",
            _cli_identity(params, default="user", label="drive +download"),
            "--format",
            "json",
            "--file-token",
            token,
            "--output",
            str(output_path),
            "--overwrite",
        ],
        action="drive +download",
    )
    return _json_arg(
        {
            "downloaded": True,
            "name": name,
            "output_path": str(output_path),
        }
    )


def _execute_cli_doc_fetch(request: ToolRequest) -> str:
    params = request.params
    doc = _required_cli_str(params, "doc", aliases=("document_id", "document_token", "document_url", "url"))
    args = [
        "lark-cli",
        "docs",
        "+fetch",
        "--api-version",
        "v2",
        "--as",
        _cli_identity(params, default="user", label="docs +fetch"),
        "--format",
        "json",
        "--doc",
        doc,
    ]
    doc_format = _optional_allowed_cli_str(params, "doc_format", {"xml", "markdown", "text"}, default="xml")
    if doc_format:
        args.extend(["--doc-format", doc_format])
    detail = _optional_allowed_cli_str(params, "detail", {"simple", "with-ids", "full"}, default="simple")
    if detail:
        args.extend(["--detail", detail])
    scope = _optional_allowed_cli_str(params, "scope", {"outline", "range", "keyword", "section"})
    if scope:
        args.extend(["--scope", scope])
    _maybe_cli_arg(args, "--start-block-id", params.get("start_block_id"))
    _maybe_cli_arg(args, "--end-block-id", params.get("end_block_id"))
    _maybe_cli_arg(args, "--keyword", params.get("keyword"))
    _maybe_cli_arg(args, "--revision-id", params.get("revision_id"))
    _maybe_cli_arg(args, "--context-before", params.get("context_before"))
    _maybe_cli_arg(args, "--context-after", params.get("context_after"))
    _maybe_cli_arg(args, "--max-depth", params.get("max_depth"))
    payload = _run_lark_cli_json(args, action="docs +fetch")
    if _raw_json_response_requested(params):
        return _json_arg(payload)
    document = _cli_doc_document(payload)
    content = str(document.get("content") or "").strip()
    document_id = str(document.get("document_id") or doc).strip()
    if not content:
        return f"飞书文档已通过 CLI 读取：{document_id}，但未返回正文。"
    return f"飞书文档已通过 CLI 读取：{document_id}\n{_text_preview(content, limit=1200)}"


def _execute_cli_drive_file_list(request: ToolRequest) -> str:
    params = request.params
    cli_params: dict[str, Any] = {
        "page_size": _optional_cli_int(params, "page_size", default=50, minimum=1, maximum=200),
    }
    for key in ("page_token", "folder_token"):
        value = str(params.get(key) or "").strip()
        if value:
            cli_params[key] = value
    args = [
        "lark-cli",
        "drive",
        "files",
        "list",
        "--as",
        _cli_identity(params, default="user", label="drive files list"),
        "--format",
        "json",
        "--params",
        _json_arg(cli_params),
    ]
    payload = _run_lark_cli_json(args, action="drive files list")
    if _raw_json_response_requested(params):
        return _json_arg(payload)
    items = _cli_drive_file_items(payload)
    titles = [_cli_drive_file_title(item) for item in items[:5]]
    titles = [title for title in titles if title]
    if titles:
        return f"飞书云空间文件已通过 CLI 读取 {len(items)} 个：{', '.join(titles)}"
    return f"飞书云空间文件已通过 CLI 读取 {len(items)} 个。"


def _execute_cli_drive_search(request: ToolRequest) -> str:
    params = request.params
    args = [
        "lark-cli",
        "drive",
        "+search",
        "--as",
        _cli_identity(params, default="user", label="drive +search"),
        "--format",
        "json",
        "--query",
        str(params.get("query") or params.get("keyword") or "").strip(),
        "--page-size",
        str(_optional_cli_int(params, "page_size", default=15, minimum=1, maximum=20)),
    ]
    sort = _optional_allowed_cli_str(
        params,
        "sort",
        {"default", "edit_time", "edit_time_asc", "open_time", "create_time"},
        default="default",
    )
    if sort and sort != "default":
        args.extend(["--sort", sort])
    doc_types = _drive_search_doc_types(params)
    if doc_types:
        args.extend(["--doc-types", ",".join(doc_types)])
    for key, flag in (
        ("chat_ids", "--chat-ids"),
        ("creator_ids", "--creator-ids"),
        ("sharer_ids", "--sharer-ids"),
        ("folder_tokens", "--folder-tokens"),
        ("space_ids", "--space-ids"),
    ):
        values = _string_list_value(params.get(key))
        if values:
            args.extend([flag, ",".join(values)])
    for key, flag in (
        ("page_token", "--page-token"),
        ("created_since", "--created-since"),
        ("created_until", "--created-until"),
        ("edited_since", "--edited-since"),
        ("edited_until", "--edited-until"),
        ("opened_since", "--opened-since"),
        ("opened_until", "--opened-until"),
        ("commented_since", "--commented-since"),
        ("commented_until", "--commented-until"),
    ):
        _maybe_cli_arg(args, flag, params.get(key))
    for key, flag in (
        ("mine", "--mine"),
        ("only_title", "--only-title"),
        ("only_comment", "--only-comment"),
    ):
        if _optional_cli_flag(params, key):
            args.append(flag)
    payload = _run_lark_cli_json(args, action="drive +search")
    if _raw_json_response_requested(params):
        return _json_arg(payload)
    items = _drive_search_items(payload)
    titles = [_drive_search_title(item) for item in items[:5]]
    titles = [title for title in titles if title]
    if titles:
        return f"飞书云空间已通过 CLI 搜索 {len(items)} 条：{', '.join(titles)}"
    return f"飞书云空间已通过 CLI 搜索 {len(items)} 条。"


def _execute_cli_calendar_create_event(request: ToolRequest) -> str:
    params = request.params
    args = [
        "lark-cli",
        "calendar",
        "+create",
        "--as",
        _cli_identity(params, default="user", label="calendar +create"),
        "--format",
        "json",
        "--calendar-id",
        str(params.get("calendar_id") or "primary").strip() or "primary",
        "--summary",
        _required_cli_str(params, "summary"),
        "--start",
        _required_cli_str(params, "start", aliases=("start_time",)),
        "--end",
        _required_cli_str(params, "end", aliases=("end_time",)),
    ]
    _maybe_cli_arg(args, "--description", params.get("description"))
    attendee_ids = _string_list_value(params.get("attendee_ids"))
    if attendee_ids:
        args.extend(["--attendee-ids", ",".join(attendee_ids)])
    _maybe_cli_arg(args, "--rrule", params.get("rrule") or params.get("recurrence"))
    payload = _run_lark_cli_json(args, action="calendar +create")
    title = _cli_calendar_event_title(payload) or _required_cli_str(params, "summary")
    return f"飞书日程已通过 CLI 创建：{title}"


def _execute_cli_calendar_agenda(request: ToolRequest) -> str:
    params = request.params
    args = [
        "lark-cli",
        "calendar",
        "+agenda",
        "--as",
        _cli_identity(params, default="user", label="calendar +agenda"),
        "--format",
        "json",
        "--calendar-id",
        str(params.get("calendar_id") or "primary").strip() or "primary",
    ]
    _maybe_cli_arg(args, "--start", params.get("start") or params.get("start_time"))
    _maybe_cli_arg(args, "--end", params.get("end") or params.get("end_time"))
    payload = _run_lark_cli_json(args, action="calendar +agenda")
    if _raw_json_response_requested(params):
        return _json_arg(payload)
    items = _calendar_agenda_items(payload)
    titles = [_calendar_agenda_title(item) for item in items[:5]]
    titles = [title for title in titles if title]
    if titles:
        return f"飞书日程已通过 CLI 读取 {len(items)} 条：{', '.join(titles)}"
    return f"飞书日程已通过 CLI 读取 {len(items)} 条。"


def _execute_cli_approval_task_action(request: ToolRequest, *, action: str) -> str:
    params = request.params
    item = _approval_item(params)
    data = {
        "instance_code": _approval_required(item, "instance_code"),
        "task_id": _approval_required(item, "task_id"),
        "comment": str(params.get("comment") or _default_approval_comment(action)),
    }
    form = _approval_form(params)
    if form:
        data["form"] = form
    _run_lark_cli_json(_approval_cli_args(["tasks", action], data=data), action=f"approval tasks {action}")
    return "飞书审批任务已通过 CLI 提交同意。" if action == "approve" else "飞书审批任务已通过 CLI 提交拒绝。"


def _execute_cli_approval_task_transfer(request: ToolRequest) -> str:
    params = request.params
    item = _approval_item(params)
    data = {
        "instance_code": _approval_required(item, "instance_code"),
        "task_id": _approval_required(item, "task_id"),
        "transfer_user_id": _required_cli_str(params, "transfer_user_id"),
    }
    _maybe_set(data, "comment", params.get("comment"))
    _run_lark_cli_json(
        _approval_cli_args(["tasks", "transfer"], data=data, user_id_type=_approval_user_id_type(params)),
        action="approval tasks transfer",
    )
    return "飞书审批任务已通过 CLI 转交。"


def _execute_cli_approval_tasks_remind(request: ToolRequest) -> str:
    params = request.params
    item = _approval_item(params)
    data = {
        "instance_code": _approval_required(item, "instance_code"),
        "task_ids": _approval_task_ids(params, item),
    }
    _maybe_set(data, "comment", params.get("comment"))
    _run_lark_cli_json(_approval_cli_args(["tasks", "remind"], data=data), action="approval tasks remind")
    return "飞书审批催办已通过 CLI 发送。"


def _execute_cli_approval_instance_cancel(request: ToolRequest) -> str:
    item = _approval_item(request.params)
    data = {"instance_code": _approval_required(item, "instance_code")}
    _run_lark_cli_json(_approval_cli_args(["instances", "cancel"], data=data), action="approval instances cancel")
    return "飞书审批实例已通过 CLI 撤回。"


def _execute_cli_approval_instance_cc(request: ToolRequest) -> str:
    params = request.params
    item = _approval_item(params)
    data = {
        "instance_code": _approval_required(item, "instance_code"),
        "cc_user_ids": _required_cli_list(params, "cc_user_ids"),
    }
    _maybe_set(data, "comment", params.get("comment"))
    _run_lark_cli_json(
        _approval_cli_args(["instances", "cc"], data=data, user_id_type=_approval_user_id_type(params)),
        action="approval instances cc",
    )
    return "飞书审批实例已通过 CLI 抄送。"


def _execute_cli_approval_task_add_sign(request: ToolRequest) -> str:
    params = request.params
    item = _approval_item(params)
    data = {
        "instance_code": _approval_required(item, "instance_code"),
        "task_id": _approval_required(item, "task_id"),
        "add_sign_user_ids": _required_cli_list(params, "add_sign_user_ids"),
        "add_sign_type": _bounded_cli_int(params, "add_sign_type", minimum=1, maximum=3),
    }
    approval_method = _optional_bounded_cli_int(params, "approval_method", minimum=1, maximum=3)
    if approval_method is not None:
        data["approval_method"] = approval_method
    _maybe_set(data, "comment", params.get("comment"))
    _run_lark_cli_json(
        _approval_cli_args(["tasks", "add_sign"], data=data, user_id_type=_approval_user_id_type(params)),
        action="approval tasks add_sign",
    )
    return "飞书审批任务已通过 CLI 加签。"


def _execute_cli_approval_task_rollback(request: ToolRequest) -> str:
    params = request.params
    item = _approval_item(params)
    data = {
        "instance_code": _approval_required(item, "instance_code"),
        "task_id": _approval_required(item, "task_id"),
        "node_ids": _required_cli_list(params, "node_ids"),
    }
    _maybe_set(data, "comment", params.get("comment"))
    _run_lark_cli_json(_approval_cli_args(["tasks", "rollback"], data=data), action="approval tasks rollback")
    return "飞书审批任务已通过 CLI 退回。"


def _approval_cli_args(path: list[str], *, data: dict[str, Any], user_id_type: str | None = None) -> list[str]:
    args = [
        "lark-cli",
        "approval",
        *path,
        "--as",
        "user",
        "--format",
        "json",
        "--data",
        _json_arg(data),
        "--yes",
    ]
    if user_id_type:
        args.extend(["--params", _json_arg({"user_id_type": user_id_type})])
    return args


def _approval_attachment_output_path(name: str) -> Path:
    suffix = Path(name).suffix
    if not suffix or len(suffix) > 12:
        suffix = ".bin"
    return Path(tempfile.gettempdir()) / f"feishu-approval-attachment-{uuid4().hex}{suffix}"


def _approval_item(params: dict[str, Any]) -> dict[str, Any]:
    item = params.get("item")
    merged = dict(item) if isinstance(item, dict) else {}
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


def _approval_required(item: dict[str, Any], key: str) -> str:
    aliases = {"instance_code": ("instance_code", "process_code")}
    for source in aliases.get(key, (key,)):
        value = str(item.get(source) or "").strip()
        if value:
            return value
    raise ValueError(f"Feishu CLI approval tool requires {key}.")


def _approval_form(params: dict[str, Any]) -> str | None:
    form = params.get("form")
    if form is None:
        return None
    return form if isinstance(form, str) else _json_arg(form)


def _approval_task_ids(params: dict[str, Any], item: dict[str, Any]) -> list[str]:
    task_ids = _string_list_value(params.get("task_ids"))
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
    raise ValueError("Feishu CLI approval tool requires task_ids.")


def _approval_user_id_type(params: dict[str, Any]) -> str:
    user_id_type = str(params.get("user_id_type") or "open_id").strip()
    if user_id_type not in {"open_id", "user_id", "union_id"}:
        raise ValueError("Feishu approval user_id_type must be one of open_id, user_id, union_id.")
    return user_id_type


def _default_approval_comment(action: str) -> str:
    return "由数字参谋根据用户在飞书中的二次确认提交同意。" if action == "approve" else "由数字参谋根据用户在飞书中的二次确认提交拒绝。"


def _maybe_set(payload: dict[str, Any], key: str, value: Any) -> None:
    if value is not None and str(value).strip():
        payload[key] = value


def _bounded_cli_int(params: dict[str, Any], key: str, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(params.get(key))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Feishu CLI approval tool requires integer {key}.") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"Feishu CLI approval tool requires {key} between {minimum} and {maximum}.")
    return parsed


def _optional_bounded_cli_int(params: dict[str, Any], key: str, *, minimum: int, maximum: int) -> int | None:
    if params.get(key) is None:
        return None
    return _bounded_cli_int(params, key, minimum=minimum, maximum=maximum)


def _optional_cli_int(
    params: dict[str, Any],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if params.get(key) is None:
        return default
    try:
        parsed = int(params[key])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Feishu CLI tool requires integer {key}.") from exc
    return min(max(parsed, minimum), maximum)


def _optional_cli_nonnegative_int(params: dict[str, Any], key: str) -> int | None:
    if params.get(key) is None:
        return None
    try:
        parsed = int(params[key])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Feishu CLI tool requires integer {key}.") from exc
    return max(parsed, 0)


def _optional_cli_flag(params: dict[str, Any], key: str) -> bool:
    value = params.get(key)
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off", ""}:
            return False
    raise ValueError(f"Feishu CLI tool requires boolean flag {key}.")


def _cli_approval_instance_title(item: Any) -> str | None:
    if not isinstance(item, dict):
        return str(item) if item else None
    for key in ("definition_name", "approval_name", "instance_code", "serial_number"):
        value = item.get(key)
        if value:
            return str(value)
    return None


def _cli_approval_task_items(payload: Any) -> list[Any]:
    if not isinstance(payload, dict):
        return []
    tasks = payload.get("tasks")
    if isinstance(tasks, list):
        return tasks
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("tasks", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def _cli_approval_task_title(item: Any) -> str | None:
    if not isinstance(item, dict):
        return str(item) if item else None
    for key in ("title", "definition_name", "task_id", "instance_code"):
        value = item.get(key)
        if value:
            return str(value)
    return None


DRIVE_SEARCH_DOC_TYPES = {
    "doc",
    "sheet",
    "bitable",
    "mindnote",
    "file",
    "wiki",
    "docx",
    "folder",
    "catalog",
    "slides",
    "shortcut",
}


def _drive_search_doc_types(params: dict[str, Any]) -> list[str]:
    values = _string_list_value(params.get("doc_types") or params.get("doc_type"))
    invalid = [value for value in values if value not in DRIVE_SEARCH_DOC_TYPES]
    if invalid:
        raise ValueError(
            "Feishu CLI drive +search requires doc_types to be one of "
            + ", ".join(sorted(DRIVE_SEARCH_DOC_TYPES))
            + "."
        )
    return values


def _drive_search_items(payload: Any) -> list[Any]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return []
    for key in ("docs", "items", "results", "files"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def _drive_search_title(item: Any) -> str | None:
    if not isinstance(item, dict):
        return str(item) if item else None
    for key in ("title", "name", "file_name", "display_name", "token", "url"):
        value = item.get(key)
        if value:
            return str(value)
    return None


def _cli_calendar_event_title(payload: Any) -> str | None:
    data = payload.get("data") if isinstance(payload, dict) else None
    event = data.get("event") if isinstance(data, dict) else None
    if not isinstance(event, dict):
        return None
    for key in ("summary", "event_id", "id"):
        value = event.get(key)
        if value:
            return str(value)
    return None


def _calendar_agenda_items(payload: Any) -> list[Any]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("items", "events", "event_list", "calendar_events"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    items: list[Any] = []
    for key in ("agenda", "days", "dates"):
        groups = data.get(key)
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            for item_key in ("items", "events"):
                value = group.get(item_key)
                if isinstance(value, list):
                    items.extend(value)
    return items


def _calendar_agenda_title(item: Any) -> str | None:
    if not isinstance(item, dict):
        return str(item) if item else None
    event = item.get("event")
    if isinstance(event, dict):
        nested = _calendar_agenda_title(event)
        if nested:
            return nested
    for key in ("summary", "title", "subject", "event_id", "id"):
        value = item.get(key)
        if value:
            return str(value)
    return None


def _execute_cli_bitable_record_create(request: ToolRequest) -> str:
    params = request.params
    fields = _required_cli_dict(params, "fields")
    _validate_cli_bitable_write_fields(params, list(fields))
    payload = _run_lark_cli_json(
        _base_cli_args(params, "+record-upsert") + ["--json", _json_arg(fields)],
        action="base +record-upsert",
    )
    record_id = _cli_bitable_title(payload, ("record_id", "id"))
    return f"飞书多维表格记录已通过 CLI 创建：{record_id}" if record_id else "飞书多维表格记录已通过 CLI 创建。"


def _execute_cli_bitable_record_update(request: ToolRequest) -> str:
    params = request.params
    fields = _required_cli_dict(params, "fields")
    record_id = _required_cli_str(params, "record_id")
    _validate_cli_bitable_write_fields(params, list(fields))
    payload = _run_lark_cli_json(
        _base_cli_args(params, "+record-upsert") + ["--record-id", record_id, "--json", _json_arg(fields)],
        action="base +record-upsert",
    )
    title = _cli_bitable_title(payload, ("record_id", "id")) or record_id
    return f"飞书多维表格记录已通过 CLI 更新：{title}"


def _execute_cli_bitable_record_upsert(request: ToolRequest) -> str:
    params = request.params
    fields = _required_cli_dict(params, "fields")
    record_id = str(params.get("record_id") or "").strip()
    _validate_cli_bitable_write_fields(params, list(fields))
    args = _base_cli_args(params, "+record-upsert")
    if record_id:
        args.extend(["--record-id", record_id])
    args.extend(["--json", _json_arg(fields)])
    payload = _run_lark_cli_json(args, action="base +record-upsert")
    title = _cli_bitable_title(payload, ("record_id", "id")) or record_id
    if record_id:
        return f"飞书多维表格记录已通过 CLI 按 record_id 更新：{title}"
    return f"飞书多维表格记录已通过 CLI 创建：{title}" if title else "飞书多维表格记录已通过 CLI 创建。"


def _execute_cli_bitable_record_upload_attachment(request: ToolRequest) -> str:
    params = request.params
    record_id = _required_cli_str(params, "record_id")
    field_id = _required_cli_str(params, "field_id", aliases=("field_name",))
    files = _string_list_value(params.get("files") or params.get("file_paths") or params.get("file"))
    if not files:
        raise ValueError("Feishu CLI base +record-upload-attachment requires files.")
    if len(files) > 50:
        raise ValueError("Feishu CLI base +record-upload-attachment supports at most 50 files.")
    args = _base_cli_args(params, "+record-upload-attachment") + [
        "--record-id",
        record_id,
        "--field-id",
        field_id,
    ]
    for file_path in files:
        args.extend(["--file", file_path])
    _run_lark_cli_json(args, action="base +record-upload-attachment")
    return f"飞书多维表格记录附件已通过 CLI 上传：{record_id}"


def _execute_cli_bitable_record_remove_attachment(request: ToolRequest) -> str:
    params = request.params
    record_id = _required_cli_str(params, "record_id")
    field_id = _required_cli_str(params, "field_id", aliases=("field_name",))
    file_tokens = _string_list_value(params.get("file_tokens") or params.get("file_token"))
    if not file_tokens:
        raise ValueError("Feishu CLI base +record-remove-attachment requires file_tokens.")
    if len(file_tokens) > 50:
        raise ValueError("Feishu CLI base +record-remove-attachment supports at most 50 file tokens.")
    args = _base_cli_args(params, "+record-remove-attachment") + [
        "--record-id",
        record_id,
        "--field-id",
        field_id,
    ]
    for file_token in file_tokens:
        args.extend(["--file-token", file_token])
    args.append("--yes")
    _run_lark_cli_json(args, action="base +record-remove-attachment")
    return f"飞书多维表格记录附件已通过 CLI 移除：{record_id}"


def _execute_cli_bitable_record_batch_create(request: ToolRequest) -> str:
    params = request.params
    fields = _required_cli_list(params, "fields")
    rows = _required_cli_rows(params)
    _validate_cli_bitable_write_fields(params, fields)
    payload = _run_lark_cli_json(
        _base_cli_args(params, "+record-batch-create")
        + ["--json", _json_arg({"fields": fields, "rows": rows})],
        action="base +record-batch-create",
    )
    return f"飞书多维表格记录已通过 CLI 批量创建：{_cli_bitable_records_count(payload, default=len(rows))} 条"


def _execute_cli_bitable_record_batch_update(request: ToolRequest) -> str:
    params = request.params
    record_ids = _required_cli_list(params, "record_id_list")
    patch = _required_cli_dict(params, "patch")
    _validate_cli_bitable_write_fields(params, list(patch))
    payload = _run_lark_cli_json(
        _base_cli_args(params, "+record-batch-update")
        + ["--json", _json_arg({"record_id_list": record_ids, "patch": patch})],
        action="base +record-batch-update",
    )
    return f"飞书多维表格记录已通过 CLI 批量更新：{_cli_bitable_records_count(payload, default=len(record_ids))} 条"


def _execute_cli_bitable_record_batch_delete(request: ToolRequest) -> str:
    params = request.params
    record_ids = _required_cli_list(params, "record_id_list")
    args = _base_cli_args(params, "+record-delete")
    for record_id in record_ids:
        args.extend(["--record-id", record_id])
    args.append("--yes")
    _run_lark_cli_json(args, action="base +record-delete")
    return f"飞书多维表格记录已通过 CLI 批量删除：{len(record_ids)} 条"


def _execute_cli_bitable_record_delete(request: ToolRequest) -> str:
    params = request.params
    record_id = _required_cli_str(params, "record_id")
    _run_lark_cli_json(
        _base_cli_args(params, "+record-delete") + ["--record-id", record_id, "--yes"],
        action="base +record-delete",
    )
    return f"飞书多维表格记录已通过 CLI 删除：{record_id}"


def _execute_cli_bitable_base_create(request: ToolRequest) -> str:
    params = request.params
    name = _required_cli_str(params, "name")
    args = [
        "lark-cli",
        "base",
        "+base-create",
        "--as",
        _cli_identity(params, default="user", label="base +base-create"),
        "--name",
        name,
        "--format",
        "json",
    ]
    folder_token = str(params.get("folder_token") or "").strip()
    if folder_token:
        args.extend(["--folder-token", folder_token])
    table_name = str(params.get("table_name") or "").strip()
    if table_name:
        args.extend(["--table-name", table_name])
    fields = _optional_cli_list(params, "fields")
    if fields:
        args.extend(["--fields", _json_arg(fields)])
    time_zone = str(params.get("time_zone") or "").strip()
    if time_zone:
        args.extend(["--time-zone", time_zone])
    payload = _run_lark_cli_json(args, action="base +base-create")
    app_token = _cli_bitable_base_token(payload)
    table_id = _cli_bitable_title(payload, ("table_id", "id"))
    url = _cli_bitable_title(payload, ("url", "app_url", "share_url"))
    if _raw_json_response_requested(params):
        return json.dumps(
            {
                "app_token": app_token,
                "base_token": app_token,
                "name": name,
                "table_id": table_id,
                "url": url,
                "raw": payload,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
    suffix = f"：{app_token}" if app_token else ""
    return f"飞书多维表格已通过 CLI 创建{suffix}"


def _execute_cli_bitable_table_create(request: ToolRequest) -> str:
    params = request.params
    name = _required_cli_str(params, "name")
    args = _base_cli_args(params, "+table-create", include_table=False) + ["--name", name]
    fields = _optional_cli_list(params, "fields")
    if fields:
        args.extend(["--fields", _json_arg(fields)])
    view = params.get("view")
    if isinstance(view, (dict, list)):
        args.extend(["--view", _json_arg(view)])
    payload = _run_lark_cli_json(args, action="base +table-create")
    title = _cli_bitable_title(payload, ("name", "table_id", "id")) or name
    table_id = _cli_bitable_title(payload, ("table_id", "id"))
    if _raw_json_response_requested(params):
        return json.dumps(
            {
                "app_token": params.get("app_token") or params.get("base_token"),
                "name": title,
                "table_id": table_id,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
    return f"飞书多维表格数据表已通过 CLI 创建：{title}"


def _execute_cli_bitable_table_update(request: ToolRequest) -> str:
    params = request.params
    name = _required_cli_str(params, "name")
    payload = _run_lark_cli_json(
        _base_cli_args(params, "+table-update") + ["--name", name],
        action="base +table-update",
    )
    title = _cli_bitable_title(payload, ("name", "table_id", "id")) or name
    return f"飞书多维表格数据表已通过 CLI 重命名：{title}"


def _execute_cli_bitable_table_delete(request: ToolRequest) -> str:
    params = request.params
    table_id = _required_cli_str(params, "table_id")
    _run_lark_cli_json(
        _base_cli_args(params, "+table-delete") + ["--yes"],
        action="base +table-delete",
    )
    return f"飞书多维表格数据表已通过 CLI 删除：{table_id}"


def _execute_cli_bitable_field_create(request: ToolRequest) -> str:
    params = request.params
    field = _required_cli_dict(params, "field")
    payload = _run_lark_cli_json(
        _base_cli_args(params, "+field-create") + ["--json", _json_arg(field)],
        action="base +field-create",
    )
    title = _cli_bitable_title(payload, ("field_name", "name", "field_id", "id")) or str(field.get("name") or "")
    return f"飞书多维表格字段已通过 CLI 创建：{title}" if title else "飞书多维表格字段已通过 CLI 创建。"


def _execute_cli_bitable_field_delete(request: ToolRequest) -> str:
    params = request.params
    field_id = _required_cli_str(params, "field_id")
    _run_lark_cli_json(
        _base_cli_args(params, "+field-delete") + ["--field-id", field_id, "--yes"],
        action="base +field-delete",
    )
    return f"飞书多维表格字段已通过 CLI 删除：{field_id}"


def _execute_cli_bitable_field_update(request: ToolRequest) -> str:
    params = request.params
    field = _required_cli_dict(params, "field")
    field_type = str(field.get("type") or "").strip().lower()
    if field_type in {"formula", "lookup"}:
        raise ValueError("Feishu Bitable formula/lookup field update is not enabled for CLI realtime execution.")
    payload = _run_lark_cli_json(
        _base_cli_args(params, "+field-update")
        + ["--field-id", _required_cli_str(params, "field_id"), "--json", _json_arg(field), "--yes"],
        action="base +field-update",
    )
    title = _cli_bitable_title(payload, ("field_name", "name", "field_id", "id")) or str(field.get("name") or "")
    return f"飞书多维表格字段已通过 CLI 更新：{title}" if title else "飞书多维表格字段已通过 CLI 更新。"


def _execute_cli_bitable_view_create(request: ToolRequest) -> str:
    params = request.params
    view = _cli_bitable_view(params)
    payload = _run_lark_cli_json(
        _base_cli_args(params, "+view-create") + ["--json", _json_arg(view)],
        action="base +view-create",
    )
    title = _cli_bitable_title(payload, ("view_name", "name", "view_id", "id")) or str(view.get("name") or "")
    return f"飞书多维表格视图已通过 CLI 创建：{title}" if title else "飞书多维表格视图已通过 CLI 创建。"


def _execute_cli_bitable_view_delete(request: ToolRequest) -> str:
    params = request.params
    view_id = _required_cli_str(params, "view_id")
    _run_lark_cli_json(
        _base_cli_args(params, "+view-delete") + ["--view-id", view_id, "--yes"],
        action="base +view-delete",
    )
    return f"飞书多维表格视图已通过 CLI 删除：{view_id}"


def _execute_cli_bitable_view_rename(request: ToolRequest) -> str:
    params = request.params
    name = _required_cli_str(params, "name")
    payload = _run_lark_cli_json(
        _base_cli_args(params, "+view-rename")
        + ["--view-id", _required_cli_str(params, "view_id"), "--name", name],
        action="base +view-rename",
    )
    title = _cli_bitable_title(payload, ("view_name", "name", "view_id", "id")) or name
    return f"飞书多维表格视图已通过 CLI 重命名：{title}"


def _execute_cli_bitable_view_set_filter(request: ToolRequest) -> str:
    params = request.params
    view_id = _required_cli_str(params, "view_id")
    filter_config = _cli_bitable_view_filter(params)
    _run_lark_cli_json(
        _base_cli_args(params, "+view-set-filter") + ["--view-id", view_id, "--json", _json_arg(filter_config)],
        action="base +view-set-filter",
    )
    return f"飞书多维表格视图筛选已通过 CLI 更新：{view_id}"


def _execute_cli_bitable_view_set_sort(request: ToolRequest) -> str:
    params = request.params
    view_id = _required_cli_str(params, "view_id")
    sort_config = _cli_bitable_view_sort(params)
    _run_lark_cli_json(
        _base_cli_args(params, "+view-set-sort") + ["--view-id", view_id, "--json", _json_arg(sort_config)],
        action="base +view-set-sort",
    )
    return f"飞书多维表格视图排序已通过 CLI 更新：{view_id}"


def _execute_cli_bitable_view_set_group(request: ToolRequest) -> str:
    params = request.params
    view_id = _required_cli_str(params, "view_id")
    group_config = _cli_bitable_view_group(params)
    _run_lark_cli_json(
        _base_cli_args(params, "+view-set-group") + ["--view-id", view_id, "--json", _json_arg(group_config)],
        action="base +view-set-group",
    )
    return f"飞书多维表格视图分组已通过 CLI 更新：{view_id}"


def _execute_cli_bitable_view_set_visible_fields(request: ToolRequest) -> str:
    params = request.params
    view_id = _required_cli_str(params, "view_id")
    visible_fields = _cli_bitable_view_visible_fields(params)
    _run_lark_cli_json(
        _base_cli_args(params, "+view-set-visible-fields")
        + ["--view-id", view_id, "--json", _json_arg(visible_fields)],
        action="base +view-set-visible-fields",
    )
    return f"飞书多维表格视图可见字段已通过 CLI 更新：{view_id}"


def _execute_cli_bitable_view_set_card(request: ToolRequest) -> str:
    params = request.params
    view_id = _required_cli_str(params, "view_id")
    card = _cli_bitable_view_card(params)
    _run_lark_cli_json(
        _base_cli_args(params, "+view-set-card") + ["--view-id", view_id, "--json", _json_arg(card)],
        action="base +view-set-card",
    )
    return f"飞书多维表格视图卡片配置已通过 CLI 更新：{view_id}"


def _execute_cli_bitable_view_set_timebar(request: ToolRequest) -> str:
    params = request.params
    view_id = _required_cli_str(params, "view_id")
    timebar = _cli_bitable_view_timebar(params)
    _run_lark_cli_json(
        _base_cli_args(params, "+view-set-timebar") + ["--view-id", view_id, "--json", _json_arg(timebar)],
        action="base +view-set-timebar",
    )
    return f"飞书多维表格视图时间轴配置已通过 CLI 更新：{view_id}"


def _cli_task_create_payload(params: dict[str, Any]) -> dict[str, Any]:
    task = params.get("task")
    payload = dict(task) if isinstance(task, dict) else {}
    for key in ("summary", "description", "due", "start", "tasklists"):
        if params.get(key) is not None:
            payload[key] = params[key]
    members = _cli_task_members(params)
    if members:
        payload["members"] = members
    if not str(payload.get("summary") or "").strip():
        raise ValueError("Feishu CLI task +create requires summary.")
    return payload


def _cli_task_update_payload(params: dict[str, Any]) -> dict[str, Any]:
    value = params.get("task")
    payload = dict(value) if isinstance(value, dict) else {}
    for key in ("summary", "description", "due", "start", "completed_at"):
        if params.get(key) is not None:
            payload[key] = params[key]
    if not payload:
        raise ValueError("Feishu CLI task +update requires task payload.")
    return payload


def _cli_task_section_create_data(params: dict[str, Any]) -> dict[str, Any]:
    name = _required_cli_str(params, "name")
    resource_type = str(params.get("resource_type") or "tasklist").strip()
    if resource_type not in {"tasklist", "my_tasks"}:
        raise ValueError("Feishu CLI task sections create resource_type must be tasklist or my_tasks.")
    _ensure_single_task_section_insert(params)
    data: dict[str, Any] = {"name": name, "resource_type": resource_type}
    if resource_type == "tasklist":
        data["resource_id"] = _required_cli_str(params, "resource_id", aliases=("tasklist_guid", "tasklist_id"))
    elif params.get("resource_id") is not None:
        data["resource_id"] = _required_cli_str(params, "resource_id")
    for key in ("insert_before", "insert_after"):
        value = str(params.get(key) or "").strip()
        if value:
            data[key] = value
    return data


def _cli_task_section_update_data(params: dict[str, Any]) -> dict[str, Any]:
    _ensure_single_task_section_insert(params)
    section: dict[str, str] = {}
    for key in ("name", "insert_before", "insert_after"):
        value = str(params.get(key) or "").strip()
        if value:
            section[key] = value
    if not section:
        raise ValueError("Feishu CLI task sections patch requires name, insert_before, or insert_after.")
    update_fields = _string_list_value(params.get("update_fields"))
    if update_fields:
        unknown = [field for field in update_fields if field not in {"name", "insert_before", "insert_after"}]
        if unknown:
            raise ValueError(f"Feishu CLI task sections patch unsupported update_fields: {', '.join(unknown)}")
        missing = [field for field in update_fields if field not in section]
        if missing:
            raise ValueError(f"Feishu CLI task sections patch missing values for update_fields: {', '.join(missing)}")
    else:
        update_fields = list(section)
    return {"section": section, "update_fields": update_fields}


def _ensure_single_task_section_insert(params: dict[str, Any]) -> None:
    if str(params.get("insert_before") or "").strip() and str(params.get("insert_after") or "").strip():
        raise ValueError("Feishu task section supports only one of insert_before or insert_after.")


def _cli_task_members(params: dict[str, Any]) -> list[dict[str, str]] | None:
    members = params.get("members")
    if isinstance(members, list) and all(isinstance(item, dict) for item in members):
        return [dict(item) for item in members]
    generated = [
        {"id": member_id, "type": "user", "role": "assignee"}
        for member_id in _string_list_value(params.get("assignees") or params.get("assignee"))
    ]
    generated.extend(
        {"id": member_id, "type": "user", "role": "follower"}
        for member_id in _string_list_value(params.get("followers") or params.get("follower"))
    )
    return generated or None


def _tasklist_create_members(params: dict[str, Any]) -> list[dict[str, str]] | None:
    members = params.get("members")
    if isinstance(members, list) and all(isinstance(item, dict) for item in members):
        return [dict(item) for item in members]
    generated = [
        {"id": member_id, "type": "user", "role": "editor"}
        for member_id in _string_list_value(params.get("editors") or params.get("member_ids"))
    ]
    return generated or None


def _string_list_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _base_cli_args(params: dict[str, Any], action: str, *, include_table: bool = True) -> list[str]:
    args = [
        "lark-cli",
        "base",
        action,
        "--as",
        _cli_identity(params, default="user", label=f"base {action}"),
        "--format",
        "json",
        "--base-token",
        _required_cli_str(params, "app_token", aliases=("base_token",)),
    ]
    if include_table:
        args.extend(["--table-id", _required_cli_str(params, "table_id")])
    return args


def _base_view_get_cli_args(params: dict[str, Any], action: str) -> list[str]:
    return _base_cli_args(params, action) + ["--view-id", _required_cli_str(params, "view_id")]


def _contact_api_get_args(params: dict[str, Any], path: str, query: dict[str, Any], *, label: str) -> list[str]:
    return [
        "lark-cli",
        "api",
        "GET",
        path,
        "--as",
        _cli_identity(params, default="bot", label=label),
        "--format",
        "json",
        "--params",
        _json_arg(query),
    ]


def _run_contact_department_children(params: dict[str, Any], *, department_id: str) -> Any:
    query: dict[str, Any] = {
        "department_id": department_id,
        "department_id_type": str(params.get("department_id_type") or "department_id"),
        "user_id_type": _cli_user_id_type(params, label="api contact department children"),
        "page_size": _optional_cli_int(params, "page_size", default=50, minimum=1, maximum=50),
    }
    if params.get("fetch_child") is not None:
        query["fetch_child"] = _optional_cli_flag(params, "fetch_child")
    _maybe_set(query, "page_token", params.get("page_token"))
    return _run_lark_cli_json_loose(
        _contact_api_get_args(
            params,
            "/open-apis/contact/v3/departments/" + department_id + "/children",
            query,
            label="api contact department children",
        ),
        action="api contact department children",
    )


def _run_contact_department_users(params: dict[str, Any], *, department_id: str) -> Any:
    query: dict[str, Any] = {
        "department_id": department_id,
        "department_id_type": str(params.get("department_id_type") or "department_id"),
        "user_id_type": _cli_user_id_type(params, label="api contact department users"),
        "page_size": _optional_cli_int(params, "page_size", default=50, minimum=1, maximum=50),
    }
    _maybe_set(query, "page_token", params.get("page_token"))
    return _run_lark_cli_json_loose(
        _contact_api_get_args(
            params,
            "/open-apis/contact/v3/users/find_by_department",
            query,
            label="api contact department users",
        ),
        action="api contact department users",
    )


def _required_cli_dict(params: dict[str, Any], key: str) -> dict[str, Any]:
    value = params.get(key)
    if not isinstance(value, dict) or not value:
        raise ValueError(f"Feishu CLI tool requires {key}.")
    return value


def _optional_cli_list(params: dict[str, Any], key: str) -> list[Any]:
    value = params.get(key)
    return value if isinstance(value, list) else []


def _required_cli_list(params: dict[str, Any], key: str) -> list[str]:
    values = _string_list_value(params.get(key))
    if not values:
        raise ValueError(f"Feishu CLI tool requires {key}.")
    return values


def _required_cli_bool(params: dict[str, Any], key: str) -> bool:
    value = params.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off"}:
            return False
    raise ValueError(f"Feishu CLI tool requires boolean {key}.")


def _optional_allowed_cli_str(
    params: dict[str, Any],
    key: str,
    allowed: set[str],
    *,
    default: str | None = None,
) -> str | None:
    value = params.get(key)
    if value is None:
        return default
    normalized = str(value).strip()
    if not normalized:
        return default
    if normalized not in allowed:
        raise ValueError(f"Feishu CLI tool requires {key} to be one of {', '.join(sorted(allowed))}.")
    return normalized


def _maybe_cli_arg(args: list[str], flag: str, value: Any) -> None:
    if value is None:
        return
    normalized = str(value).strip()
    if normalized:
        args.extend([flag, normalized])


def _required_cli_rows(params: dict[str, Any]) -> list[list[Any]]:
    value = params.get("rows")
    if not isinstance(value, list) or not value:
        raise ValueError("Feishu CLI base +record-batch-create requires rows.")
    rows = [row for row in value if isinstance(row, list)]
    if len(rows) != len(value) or not rows:
        raise ValueError("Feishu CLI base +record-batch-create requires rows as a list of row arrays.")
    return rows


def _json_arg(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _raw_json_response_requested(params: dict[str, Any]) -> bool:
    return str(params.get("response_format") or "").strip() == "raw_json"


def _cli_doc_document(payload: Any) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict) and isinstance(data.get("document"), dict):
        return dict(data["document"])
    return {}


def _text_preview(value: str, *, limit: int) -> str:
    text = " ".join(value.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _cli_bitable_view(params: dict[str, Any]) -> dict[str, Any]:
    view = params.get("view")
    value = dict(view) if isinstance(view, dict) else {}
    for key in ("name", "type"):
        if params.get(key) is not None:
            value[key] = params[key]
    if not value:
        raise ValueError("Feishu CLI base +view-create requires view.")
    name = str(value.get("name") or "").strip()
    if not name:
        raise ValueError("Feishu CLI base +view-create requires view.name.")
    view_type = str(value.get("type") or "grid").strip().lower()
    if view_type not in BITABLE_VIEW_TYPES:
        raise ValueError("Feishu Bitable view type must be one of calendar, gantt, gallery, grid, kanban.")
    value["name"] = name
    value["type"] = view_type
    return value


def _cli_bitable_view_filter(params: dict[str, Any]) -> dict[str, Any]:
    value = params.get("filter") or params.get("filter_config")
    if not isinstance(value, dict):
        raise ValueError("Feishu CLI base +view-set-filter requires filter object.")
    logic = str(value.get("logic") or "").strip().lower()
    if logic not in {"and", "or"}:
        raise ValueError("Feishu CLI base +view-set-filter filter.logic must be and or or.")
    conditions = value.get("conditions")
    if not isinstance(conditions, list):
        raise ValueError("Feishu CLI base +view-set-filter filter.conditions must be a list.")
    normalized = dict(value)
    normalized["logic"] = logic
    normalized["conditions"] = conditions
    return normalized


def _cli_bitable_view_sort(params: dict[str, Any]) -> dict[str, Any]:
    value = params.get("sort") or params.get("sort_config")
    if not isinstance(value, dict):
        raise ValueError("Feishu CLI base +view-set-sort requires sort object.")
    sort_config = value.get("sort_config")
    if not isinstance(sort_config, list):
        raise ValueError("Feishu CLI base +view-set-sort requires sort.sort_config list.")
    if len(sort_config) > 10:
        raise ValueError("Feishu CLI base +view-set-sort supports at most 10 sort items.")
    return {"sort_config": [dict(item) if isinstance(item, dict) else item for item in sort_config]}


def _cli_bitable_view_group(params: dict[str, Any]) -> dict[str, Any]:
    value = params.get("group") or params.get("group_config")
    if not isinstance(value, dict):
        raise ValueError("Feishu CLI base +view-set-group requires group object.")
    group_config = value.get("group_config")
    if not isinstance(group_config, list):
        raise ValueError("Feishu CLI base +view-set-group requires group.group_config list.")
    if len(group_config) > 3:
        raise ValueError("Feishu CLI base +view-set-group supports at most 3 group items.")
    return {"group_config": [dict(item) if isinstance(item, dict) else item for item in group_config]}


def _cli_bitable_view_visible_fields(params: dict[str, Any]) -> dict[str, Any]:
    value = params.get("visible_fields_config") or params.get("visible_fields")
    fields = value.get("visible_fields") if isinstance(value, dict) else value
    visible_fields = _string_list_value(fields)
    if not visible_fields:
        raise ValueError("Feishu CLI base +view-set-visible-fields requires visible_fields.")
    if len(visible_fields) > 200:
        raise ValueError("Feishu CLI base +view-set-visible-fields supports at most 200 visible fields.")
    return {"visible_fields": visible_fields}


def _cli_bitable_view_card(params: dict[str, Any]) -> dict[str, Any]:
    value = params.get("card") or params.get("card_config")
    if isinstance(value, dict):
        if "cover_field" not in value:
            raise ValueError("Feishu CLI base +view-set-card requires card.cover_field.")
        return {"cover_field": value.get("cover_field")}
    if "cover_field" in params and params.get("cover_field") is not None:
        return {"cover_field": str(params["cover_field"]).strip()}
    raise ValueError("Feishu CLI base +view-set-card requires card JSON object.")


def _cli_bitable_view_timebar(params: dict[str, Any]) -> dict[str, str]:
    value = params.get("timebar") or params.get("timebar_config")
    payload = dict(value) if isinstance(value, dict) else {}
    for key in ("start_time", "end_time", "title"):
        if params.get(key) is not None:
            payload[key] = params[key]
    missing = [key for key in ("start_time", "end_time", "title") if not str(payload.get(key) or "").strip()]
    if missing:
        raise ValueError(f"Feishu CLI base +view-set-timebar requires {', '.join(missing)}.")
    return {key: str(payload[key]).strip() for key in ("start_time", "end_time", "title")}


def _validate_cli_bitable_write_fields(params: dict[str, Any], field_names: list[str]) -> None:
    if params.get("validate_fields") is not True:
        return
    fields_payload = _run_lark_cli_json(_base_cli_args(params, "+field-list"), action="base +field-list")
    field_map = _cli_bitable_field_map(_cli_bitable_items(fields_payload))
    missing = [name for name in field_names if name not in field_map]
    if missing:
        raise ValueError("Feishu Bitable write field validation failed: unknown fields " + ", ".join(missing))
    readonly = [name for name in field_names if _is_cli_bitable_readonly_field(field_map[name])]
    if readonly:
        raise ValueError("Feishu Bitable write field validation failed: readonly fields " + ", ".join(readonly))


def _cli_bitable_field_map(items: list[Any]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in ("field_name", "name", "field_id", "id"):
            value = item.get(key)
            if value:
                mapping[str(value)] = item
    return mapping


def _is_cli_bitable_readonly_field(field: dict[str, Any]) -> bool:
    if field.get("is_readonly") is True or field.get("readonly") is True:
        return True
    property_value = field.get("property")
    if isinstance(property_value, dict) and (
        property_value.get("is_readonly") is True or property_value.get("readonly") is True
    ):
        return True
    field_type = str(field.get("type") or field.get("field_type") or "").strip().lower()
    return field_type in BITABLE_READONLY_FIELD_TYPES


def _cli_bitable_records_count(payload: Any, *, default: int) -> int:
    records = _cli_bitable_items(payload, key="records")
    return len(records) if records else default


def _cli_bitable_items(payload: Any, *, key: str = "items") -> list[Any]:
    if not isinstance(payload, dict):
        return []
    value = payload.get(key)
    if isinstance(value, list):
        return value
    data = payload.get("data")
    if isinstance(data, dict):
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def _cli_bitable_visible_fields(payload: Any) -> list[Any] | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get("visible_fields")
    if isinstance(value, list):
        return value
    data = payload.get("data")
    if isinstance(data, dict):
        value = data.get("visible_fields")
        if isinstance(value, list):
            return value
    return None


def _cli_bitable_title(payload: Any, keys: tuple[str, ...]) -> str | None:
    if not isinstance(payload, dict):
        return None
    direct = _first_value(payload, keys)
    if direct:
        return direct
    data = payload.get("data")
    if isinstance(data, dict):
        direct = _first_value(data, keys)
        if direct:
            return direct
        for nested_key in ("record", "table", "field", "view", "app", "base", "file", "resource", "item"):
            nested = data.get(nested_key)
            if isinstance(nested, dict):
                value = _first_value(nested, keys)
                if value:
                    return value
        records = data.get("records")
        if isinstance(records, list) and records and isinstance(records[0], dict):
            return _first_value(records[0], keys)
    return None


def _cli_bitable_base_token(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    direct = _first_value(payload, ("app_token", "base_token", "token", "obj_token"))
    if direct:
        return direct
    data = payload.get("data")
    if isinstance(data, dict):
        direct = _first_value(data, ("app_token", "base_token", "token", "obj_token"))
        if direct:
            return direct
        for nested_key in ("app", "base", "file", "resource", "item"):
            nested = data.get(nested_key)
            if isinstance(nested, dict):
                value = _first_value(nested, ("app_token", "base_token", "token", "obj_token"))
                if value:
                    return value
    return None


def _first_value(item: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = item.get(key)
        if value:
            return str(value)
    return None


def _cli_reminder_set_value(params: dict[str, Any]) -> str | None:
    reminders = params.get("positive_reminders")
    if isinstance(reminders, list):
        if not reminders:
            return None
        minutes = []
        for item in reminders:
            if isinstance(item, dict) and item.get("relative_fire_minute") is not None:
                minutes.append(f"{int(item['relative_fire_minute'])}m")
        if minutes:
            return ",".join(minutes)
    values = params.get("relative_fire_minutes")
    if values is None:
        values = params.get("relative_fire_minute")
    if values is None:
        raise ValueError("Feishu CLI task +reminder requires positive_reminders or relative_fire_minutes.")
    if isinstance(values, list):
        if not values:
            return None
        return ",".join(f"{int(item)}m" for item in values)
    return f"{int(values)}m"


def _cli_identity(params: dict[str, Any], *, default: str, label: str) -> str:
    identity = str(params.get("as") or params.get("identity") or default).strip()
    if identity not in {"bot", "user"}:
        raise ValueError(f"Feishu CLI {label} identity must be bot or user.")
    return identity


def _cli_user_id_type(params: dict[str, Any], *, label: str) -> str:
    user_id_type = str(params.get("user_id_type") or "open_id").strip()
    if user_id_type not in {"open_id", "union_id", "user_id"}:
        raise ValueError(f"Feishu CLI {label} user_id_type must be open_id, union_id or user_id.")
    return user_id_type


def _required_cli_str(params: dict[str, Any], key: str, *, aliases: tuple[str, ...] = ()) -> str:
    for source in (key, *aliases):
        value = str(params.get(source) or "").strip()
        if value:
            return value
    names = " or ".join((key, *aliases))
    raise ValueError(f"Feishu CLI tool requires {names}.")


def _cli_task_title(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if isinstance(data, dict):
        task = data.get("task")
        if isinstance(task, dict):
            for key in ("summary", "guid", "task_id"):
                value = task.get(key)
                if value:
                    return str(value)
        for key in ("summary", "guid", "task_id"):
            value = data.get(key)
            if value:
                return str(value)
    return None


def _cli_task_items(payload: Any) -> list[Any]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("items", "tasks", "task_list"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def _cli_task_item_title(item: Any) -> str | None:
    if not isinstance(item, dict):
        return str(item) if item else None
    task = item.get("task")
    if isinstance(task, dict):
        nested = _cli_task_item_title(task)
        if nested:
            return nested
    for key in ("summary", "title", "guid", "task_id", "id", "url"):
        value = item.get(key)
        if value:
            return str(value)
    return None


def _mail_triage_filter(params: dict[str, Any]) -> dict[str, Any]:
    raw_filter = params.get("filter")
    if isinstance(raw_filter, dict):
        return raw_filter
    if isinstance(raw_filter, str) and raw_filter.strip():
        try:
            parsed = json.loads(raw_filter)
        except json.JSONDecodeError as exc:
            raise ValueError("Feishu CLI mail +triage filter must be a JSON object.") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Feishu CLI mail +triage filter must be a JSON object.")
        return parsed
    filter_value: dict[str, Any] = {}
    for source, target in (
        ("folder", "folder"),
        ("folder_id", "folder"),
        ("from", "from"),
        ("to", "to"),
        ("label", "label"),
        ("label_id", "label"),
    ):
        value = params.get(source)
        if value is not None and str(value).strip():
            filter_value[target] = value
    return filter_value


def _cli_mail_items(payload: Any) -> list[Any]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("items", "messages", "message_list", "data"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def _cli_mail_title(item: Any) -> str | None:
    if not isinstance(item, dict):
        return str(item) if item else None
    meta_data = item.get("meta_data")
    if isinstance(meta_data, dict):
        nested = _cli_mail_title(meta_data)
        if nested:
            return nested
    message = item.get("message")
    if isinstance(message, dict):
        nested = _cli_mail_title(message)
        if nested:
            return nested
    for key in ("subject", "title", "mail_subject", "name", "message_id", "message_biz_id", "folder_id", "id"):
        value = item.get(key)
        if value:
            return str(value)
    return None


def _cli_mail_items_have_subject(items: list[Any]) -> bool:
    for item in items:
        if not isinstance(item, dict):
            continue
        meta_data = item.get("meta_data") if isinstance(item.get("meta_data"), dict) else {}
        if meta_data.get("title") or meta_data.get("subject"):
            return True
        message = item.get("message") if isinstance(item.get("message"), dict) else item
        if message.get("subject") or message.get("title") or message.get("mail_subject"):
            return True
    return False


def _first_cli_value(payload: Any, *, keys: tuple[str, ...]) -> str:
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if value:
                return str(value)
        for value in payload.values():
            found = _first_cli_value(value, keys=keys)
            if found:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = _first_cli_value(item, keys=keys)
            if found:
                return found
    return ""


def _cli_im_items(payload: Any, *, keys: tuple[str, ...]) -> list[Any]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def _cli_im_title(item: Any) -> str | None:
    if not isinstance(item, dict):
        return str(item) if item else None
    for key in ("name", "chat_name", "title", "summary", "text", "content", "message_id", "chat_id", "id"):
        value = item.get(key)
        if value:
            return str(value)
    message = item.get("message")
    if isinstance(message, dict):
        return _cli_im_title(message)
    return None


def _cli_vc_meeting_items(payload: Any) -> list[Any]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("items", "meetings", "meeting_list", "records", "data"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def _cli_vc_meeting_title(item: Any) -> str | None:
    if not isinstance(item, dict):
        return str(item) if item else None
    for key in ("topic", "subject", "title", "meeting_topic", "meeting_id", "id"):
        value = item.get(key)
        if value:
            return str(value)
    meeting = item.get("meeting")
    if isinstance(meeting, dict):
        return _cli_vc_meeting_title(meeting)
    return None


def _cli_wiki_items(payload: Any, *, keys: tuple[str, ...]) -> list[Any]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def _cli_wiki_title(item: Any) -> str | None:
    if not isinstance(item, dict):
        return str(item) if item else None
    for key in ("name", "title", "node_name", "space_name", "space_id", "node_token", "obj_token", "id"):
        value = item.get(key)
        if value:
            return str(value)
    node = item.get("node")
    if isinstance(node, dict):
        return _cli_wiki_title(node)
    return None


def _cli_drive_file_items(payload: Any) -> list[Any]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("files", "items", "file_list", "data"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def _cli_drive_file_title(item: Any) -> str | None:
    if not isinstance(item, dict):
        return str(item) if item else None
    for key in ("name", "title", "token", "file_token", "id"):
        value = item.get(key)
        if value:
            return str(value)
    return None


def _cli_time_arg(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _comma_cli_arg(value: Any) -> str | None:
    items = _string_list_value(value)
    if not items:
        return None
    return ",".join(items)


def _cli_okr_items(payload: Any, *, keys: tuple[str, ...]) -> list[Any]:
    if not isinstance(payload, dict):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    data = payload.get("data")
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def _cli_okr_title(item: Any, keys: tuple[str, ...]) -> str | None:
    if not isinstance(item, dict):
        return str(item) if item else None
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if value is not None and not isinstance(value, (dict, list)):
            return str(value)
    return None


def _cli_contact_items(payload: Any, *, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    source = data if isinstance(data, dict) else payload
    for key in keys:
        value = source.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _cli_contact_title(item: Any, keys: tuple[str, ...]) -> str | None:
    if not isinstance(item, dict):
        return str(item) if item else None
    for key in keys:
        value = item.get(key)
        if value:
            return str(value)
    return None


def _cli_tasklist_title(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if isinstance(data, dict):
        tasklist = data.get("tasklist")
        if isinstance(tasklist, dict):
            for key in ("name", "guid", "tasklist_guid"):
                value = tasklist.get(key)
                if value:
                    return str(value)
        for key in ("name", "guid", "tasklist_guid"):
            value = data.get(key)
            if value:
                return str(value)
    return None


def _cli_task_section_title(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if isinstance(data, dict):
        section = data.get("section")
        if isinstance(section, dict):
            for key in ("name", "guid", "section_guid"):
                value = section.get(key)
                if value:
                    return str(value)
        for key in ("name", "guid", "section_guid"):
            value = data.get(key)
            if value:
                return str(value)
    return None


def _cli_message_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("message_id", "id"):
        value = payload.get(key)
        if value:
            return str(value)
    data = payload.get("data")
    if isinstance(data, dict):
        message = data.get("message")
        if isinstance(message, dict):
            for key in ("message_id", "id"):
                value = message.get(key)
                if value:
                    return str(value)
        for key in ("message_id", "id"):
            value = data.get(key)
            if value:
                return str(value)
    return None


def _cli_chat_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("chat_id", "open_chat_id"):
        value = payload.get(key)
        if value:
            return str(value)
    data = payload.get("data")
    if isinstance(data, dict):
        chat = data.get("chat")
        if isinstance(chat, dict):
            for key in ("chat_id", "open_chat_id", "chat_id_v2"):
                value = chat.get(key)
                if value:
                    return str(value)
        for key in ("chat_id", "open_chat_id", "chat_id_v2"):
            value = data.get(key)
            if value:
                return str(value)
    return None
