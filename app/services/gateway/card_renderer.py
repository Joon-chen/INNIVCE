"""Feishu interactive card renderer — universal rendering engine for business data.

Template mapping per skill_hint (derived from route_path):
  contact    → org tree / people list card
  mail       → mail list rich-media card
  sql        → standard table card
  work_event → event status list card (approvals, tasks, meetings)

Decoupled from tool execution: tools return raw JSON/text, card renderer assembles Feishu cards.
"""

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from app.core.config import settings
from app.services.runtime_v5.context import load_result_context, load_session_context, save_session_context


_APPROVAL_BATCH_SELECTION_KEY = "runtime_v5_approval_batch_selection"
_APPROVAL_WORKBENCH_COLLAPSED_KEY = "runtime_v5_approval_workbench_collapsed"
_APPROVAL_WORKBENCH_EXPANDED_GROUPS_KEY = "runtime_v5_approval_workbench_expanded_groups"
_APPROVAL_WORKBENCH_QUERY_ID_KEY = "runtime_v5_approval_workbench_query_id"


@dataclass(frozen=True)
class CardAction:
    """User-facing card action. Cards are navigation/action entries, not workspaces."""

    label: str
    url: str | None = None
    value: dict[str, Any] | None = None
    style: str = "default"
    pc_url: str | None = None
    ios_url: str | None = None
    android_url: str | None = None


@dataclass(frozen=True)
class CardPayload:
    """V5 user-facing card payload.

    type is intentionally coarse:
    summary / action / feedback / delivery.
    Business scenarios should be metadata, not primary card types.
    """

    type: str
    title: str
    summary: str
    recommendation: str = ""
    actions: tuple[CardAction, ...] = ()
    status: str = ""

ROUTE_CARD_HINTS: dict[str, str] = {
    "feishu_contact_organization_snapshot": "contact",
    "feishu_contact_user_search": "contact",
    "people_lookup": "contact",
    "organization_snapshot": "contact",
    "company_qa": "contact",
    "people": "contact",
    "mail_qa": "mail",
    "mail_list": "mail",
    "recent_mail": "mail",
    "mail_qa": "mail",
    "mail_list": "mail",
    "recent_mail": "mail",
    "approval_qa": "work_event",
    "feishu_approval_task_query": "approval_workbench",
    "approval_query": "approval_workbench",
    "feishu_approval_instance_get": "work_event",
    "feishu_approval_instance_initiated": "work_event",
    "feishu_approval_task_approve": "work_event",
    "feishu_approval_task_reject": "work_event",
    "feishu_approval_task_transfer": "work_event",
    "feishu_approval_task_add_sign": "work_event",
    "feishu_approval_task_rollback": "work_event",
    "feishu_approval_instance_remind": "work_event",
    "feishu_approval_instance_cancel": "work_event",
    "feishu_approval_instance_cc": "work_event",
    "personal_tasks": "work_event",
    "feishu_task_create": "work_event",
    "task_qa": "work_event",
    "bitable_qa": "sql",
    "organization_export": "sql",
    "base_export": "sql",
    "feishu_bitable_field_list": "sql",
    "feishu_bitable_view_get_card": "sql",
    "feishu_bitable_view_get_timebar": "sql",
    "feishu_bitable_view_get_visible_fields": "sql",
    "calendar_qa": "work_event",
    "meeting_record": "work_event",
    "feishu_vc_meeting_search": "work_event",
    "report": "sql",
    "dashboard": "sql",
    "owner_cockpit": "sql",
    "domain_qa": "sql",
    "runtime_v5_confirmation": "confirmation",
}

CARD_TITLES: dict[str, str] = {
    "contact": "通讯录",
    "mail": "邮件",
    "sql": "数据查询",
    "work_event": "事件",
    "confirmation": "操作确认",
    "approval_workbench": "待审批",
}

ROUTE_CARD_TITLES: dict[str, str] = {
    "feishu_approval_task_query": "待审批",
    "approval_query": "待审批",
    "feishu_approval_instance_get": "审批详情",
    "approval_detail": "审批详情",
    "feishu_approval_task_approve": "审批通过",
    "feishu_approval_task_reject": "审批拒绝",
    "feishu_approval_task_transfer": "审批转交",
    "feishu_approval_task_add_sign": "审批加签",
    "feishu_approval_task_rollback": "审批退回",
    "feishu_approval_instance_remind": "审批催办",
    "feishu_approval_instance_cancel": "审批撤回",
    "feishu_approval_instance_cc": "审批抄送",
    "feishu_approval_instance_initiated": "我发起的审批",
    "feishu_task_query": "任务",
    "feishu_task_create": "任务",
    "feishu_calendar_query": "日程",
    "feishu_calendar_create": "日程",
    "runtime_v5_confirmation": "操作确认",
    "approval_workbench_refresh": "待审批",
}


@dataclass(frozen=True)
class CardRenderResult:
    card: dict[str, Any]
    card_hint: str
    prefer_interactive: bool = True


def should_use_interactive_card(route_path: str, raw_answer: str) -> bool:
    """Decide whether the reply should use an interactive card based on route_path."""
    hint = ROUTE_CARD_HINTS.get(route_path)
    if not hint:
        return False
    return hint in ("contact", "mail", "sql", "work_event", "confirmation", "approval_workbench")


def card_hint_for_route(route_path: str) -> str | None:
    """Return card template hint for a given route_path."""
    return ROUTE_CARD_HINTS.get(route_path)


def build_interactive_card(
    *,
    card_hint: str,
    route_path: str,
    raw_answer: str,
    chat_id: str | None = None,
    title_override: str | None = None,
) -> dict[str, Any]:
    """Render raw tool answer into a Feishu interactive card JSON."""
    title = title_override or ROUTE_CARD_TITLES.get(route_path) or CARD_TITLES.get(card_hint, card_hint)
    elements = _render_card_elements(card_hint=card_hint, raw_answer=raw_answer, chat_id=chat_id)
    is_approval_workbench = card_hint == "approval_workbench" or route_path in {"feishu_approval_task_query", "approval_query", "approval_workbench_refresh"}
    card: dict[str, Any] = {
        "config": {"wide_screen_mode": not is_approval_workbench},
        "elements": elements,
    }
    if not is_approval_workbench:
        card["header"] = {
            "title": {"tag": "plain_text", "content": title},
            "template": "blue",
        }
    elif is_approval_workbench:
        card["header"] = {
            "title": {"tag": "plain_text", "content": title_override or _approval_workbench_header_title(chat_id, title)},
            "template": "blue",
        }
    # Private data card: only visible to sender in group chats
    if chat_id and chat_id.startswith("oc_"):
        card["config"]["update_multi"] = False
    return card


# ---------------------------------------------------------------------------
# Template routing
# ---------------------------------------------------------------------------

def _render_card_elements(*, card_hint: str, raw_answer: str, chat_id: str | None = None) -> list[dict[str, Any]]:
    if card_hint == "contact":
        return _render_contact_card(raw_answer)
    if card_hint == "mail":
        return _render_mail_card(raw_answer)
    if card_hint == "sql":
        return _render_sql_card(raw_answer)
    if card_hint == "work_event":
        return _render_work_event_card(raw_answer)
    if card_hint == "approval_workbench":
        return _render_approval_workbench_card(raw_answer, chat_id=chat_id)
    if card_hint == "confirmation":
        return _render_confirmation_card(raw_answer, chat_id=chat_id)
    return _render_text_fallback(raw_answer)


# ---------------------------------------------------------------------------
# Contact / org-tree / people-list
# ---------------------------------------------------------------------------

def _render_contact_card(raw_answer: str) -> list[dict[str, Any]]:
    import json as _json
    try:
        data = _json.loads(raw_answer)
    except (_json.JSONDecodeError, ValueError):
        return _render_text_fallback(raw_answer)

    if isinstance(data, dict) and data.get("departments"):
        return _render_org_tree_card(data)
    if isinstance(data, list) and data:
        return _render_people_list_card(data)
    return _render_text_fallback(raw_answer)


def _render_org_tree_card(data: dict[str, Any]) -> list[dict[str, Any]]:
    departments = data.get("departments") or []
    users = data.get("users") or []
    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": f"**组织架构**（{len(departments)} 个部门，{len(users)} 人）"}},
        {"tag": "hr"},
    ]
    # Build user-per-dept mapping
    user_by_dept: dict[str, list[dict]] = {}
    for u in users:
        for did in (u.get("department_ids") or []):
            did_str = str(did)
            if did_str not in user_by_dept:
                user_by_dept[did_str] = []
            user_by_dept[did_str].append(u)
    # Build dept ID -> name mapping
    dept_id_names: dict[str, str] = {}
    dept_id_counts: dict[str, int] = {}
    parent_map: dict[str, str] = {}
    for d in departments:
        did = str(d.get("department_id") or d.get("open_department_id") or "")
        name = d.get("name") or (d.get("i18n_name") or {}).get("zh_cn") or "?"
        count = d.get("member_count") or d.get("primary_member_count") or 0
        dept_id_names[did] = name
        dept_id_counts[did] = count
        pid = str(d.get("parent_department_id") or "0")
        if pid:
            parent_map[did] = pid
    # Render combined tree
    roots = [d for d in departments if str(d.get("parent_department_id") or "0") == "0"]
    if not roots and departments:
        roots = departments[:5]
    # Track shown users to avoid duplicates (users may appear under multiple depts)
    shown_user_ids: set[str] = set()
    card_lines: list[str] = []
    MAX_TOTAL_USERS = 60  # Show all users in the card
    MAX_USERS_PER_DEPT = 6

    def _render(dept, depth):
        nonlocal card_lines, shown_user_ids
        did = str(dept.get("department_id") or dept.get("open_department_id") or "")
        name = dept_id_names.get(did, "?")
        count = dept_id_counts.get(did, 0)
        indent = "  " * depth
        prefix = "▸ " if depth > 0 else ""
        card_lines.append(f"{indent}{prefix}**{name}** `{count}人`")
        # Find children
        children = [d for d in departments if str(d.get("parent_department_id") or "") == did and d is not dept]
        # Show users directly under this dept (limit per dept)
        dept_users = user_by_dept.get(did, [])
        unseen_dept_users = [u for u in dept_users if u.get("open_id") not in shown_user_ids]
        if unseen_dept_users and len(card_lines) < 80:
            for u in unseen_dept_users[:MAX_USERS_PER_DEPT]:
                oid = u.get("open_id") or ""
                if len(shown_user_ids) >= MAX_TOTAL_USERS:
                    break
                shown_user_ids.add(oid)
                uname = u.get("name") or "?"
                utitle = u.get("title") or u.get("job_title") or ""
                uemail = u.get("email") or ""
                ueno = u.get("employee_no") or ""
                ustatus = u.get("status", {})
                u_indent = indent + "    "
                uline = f"{u_indent}👤 {uname}"
                if utitle: uline += f" — {utitle}"
                extras = []
                if ueno: extras.append(f"#{ueno}")
                if uemail and '@' in uemail: extras.append(f"{uemail[:20]}")
                if isinstance(ustatus, dict) and ustatus.get("is_resigned"): extras.append("已离职")
                if extras:
                    card_lines.append(f"{u_indent}   `{' '.join(extras)}`")
                else:
                    card_lines.append(uline)
            if len(unseen_dept_users) > MAX_USERS_PER_DEPT:
                card_lines.append(f"{indent}    ...还有{len(unseen_dept_users) - MAX_USERS_PER_DEPT}人")
        # Recurse children
        for c in children:
            _render(c, depth + 1)

    for r in roots:
        _render(r, 0)

    # Summary
    total_shown = len(shown_user_ids)
    if total_shown < len(users):
        card_lines.append("")
        card_lines.append(f"（显示 {total_shown}/{len(users)} 人，部门列表含全部 {len(departments)} 个部门）")

    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(card_lines)}})
    return elements


def _render_user_summary_lines(users: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for u in users:
        name = u.get("name") or "?"
        title = u.get("title") or u.get("job_title") or ""
        dept_names = u.get("department_names") or []
        dept_str = " / ".join(str(d) for d in dept_names[:2] if d)
        line = f"👤 **{name}**"
        if title:
            line += f" — {title}"
        if dept_str:
            line += f" `{dept_str}`"
        lines.append(line)
    return lines


def _render_people_list_card(users: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lines: list[str] = []
    for u in users[:20]:
        name = u.get("name") or "?"
        title = u.get("title") or u.get("job_title") or ""
        email = u.get("email") or ""
        line = f"👤 **{name}**"
        if title:
            line += f" — {title}"
        if email:
            line += f" \n`{email}`"
        lines.append(line)
    return [{"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}}]


# ---------------------------------------------------------------------------
# Mail list
# ---------------------------------------------------------------------------

def _render_mail_card(raw_answer: str) -> list[dict[str, Any]]:
    import json as _json
    try:
        data = _json.loads(raw_answer)
    except (_json.JSONDecodeError, ValueError):
        return _render_text_fallback(raw_answer)

    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("mails") or data.get("items") or [data]
    else:
        return _render_text_fallback(raw_answer)

    lines = [f"共 {len(items)} 封邮件，以下是前 {min(len(items), 3)} 封摘要。"]
    for i, item in enumerate(items[:3]):
        if not isinstance(item, dict):
            continue
        subject = item.get("subject") or item.get("title") or f"邮件 #{i + 1}"
        sender = item.get("sender") or item.get("from") or ""
        date = item.get("date") or item.get("received_at") or ""
        snippet = _short_text(str(item.get("snippet") or item.get("preview") or ""), 70)
        line = f"{i + 1}. **{subject}**"
        if sender:
            line += f"\n发件人：{sender}"
        if date:
            line += f"｜{date}"
        if snippet:
            line += f"\n{snippet}"
        lines.append(line)
    return _render_card_payload(
        CardPayload(
            type="summary",
            title="邮件摘要",
            summary="\n\n".join(lines),
            recommendation="更多筛选、搜索和处理建议进入邮件工作台完成。" if len(items) > 3 else "可继续追问具体邮件内容。",
        )
    )


# ---------------------------------------------------------------------------
# SQL / table
# ---------------------------------------------------------------------------

def _render_sql_card(raw_answer: str) -> list[dict[str, Any]]:
    import json as _json
    try:
        data = _json.loads(raw_answer)
    except (_json.JSONDecodeError, ValueError):
        return _render_text_fallback(raw_answer)

    if isinstance(data, dict):
        delivery = _delivery_payload_from_dict(data)
        if delivery:
            return _render_card_payload(delivery)
        rows = data.get("rows") or data.get("items") or data.get("data") or [data]
    elif isinstance(data, list):
        rows = data
    else:
        return _render_text_fallback(raw_answer)

    if not isinstance(rows, list) or not rows:
        return _render_text_fallback(raw_answer)

    first = rows[0] if isinstance(rows[0], dict) else {}
    columns = list(first.keys())[:6] if isinstance(first, dict) else []
    lines = [f"共 {len(rows)} 条记录，以下展示前 {min(len(rows), 3)} 条摘要。"]
    for i, row in enumerate(rows[:3]):
        if not isinstance(row, dict):
            continue
        parts = []
        for col in columns:
            val = row.get(col)
            if val is not None:
                parts.append(f"{col}：{_short_text(str(val), 32)}")
        line = f"{i + 1}. " + "｜".join(parts)
        if line:
            lines.append(line)
    return _render_card_payload(
        CardPayload(
            type="summary",
            title="数据摘要",
            summary="\n".join(lines),
            recommendation="完整表格、筛选、批量处理请进入工作台。" if len(rows) > 3 else "可继续追问或要求导出。",
        )
    )


# ---------------------------------------------------------------------------
# Work event (approvals / tasks / meetings)
# ---------------------------------------------------------------------------

def _render_work_event_card(raw_answer: str) -> list[dict[str, Any]]:
    import json as _json
    try:
        data = _json.loads(raw_answer)
    except (_json.JSONDecodeError, ValueError):
        return _render_text_fallback(raw_answer)

    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        feedback = _feedback_payload_from_dict(data)
        if feedback:
            return _render_card_payload(feedback)
        items = data.get("items") or data.get("tasks") or data.get("approvals") or [data]
    else:
        return _render_text_fallback(raw_answer)

    lines = [f"共 {len(items)} 条事项，以下展示前 {min(len(items), 3)} 条。"]
    for i, item in enumerate(items[:3]):
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("title") or item.get("summary") or f"#{i + 1}"
        status = item.get("status") or item.get("state") or ""
        date = item.get("created_at") or item.get("updated_at") or item.get("date") or ""
        assignee = item.get("assignee") or item.get("owner") or ""
        line = f"{i + 1}. **{name}**"
        if status:
            line += f" `{status}`"
        if assignee:
            line += f"\n负责人：{assignee}"
        if date:
            line += f"｜{date}"
        lines.append(line)
    return _render_card_payload(
        CardPayload(
            type="summary",
            title="事项摘要",
            summary="\n\n".join(lines),
            recommendation="超过 3 条事项请进入工作台查看和处理。" if len(items) > 3 else "可继续追问具体事项。",
        )
    )


# ---------------------------------------------------------------------------
# Approval workbench
# ---------------------------------------------------------------------------

def _render_approval_workbench_card(raw_answer: str, *, chat_id: str | None = None) -> list[dict[str, Any]]:
    import re as _re

    structured_items = _approval_workbench_items_from_result_context(chat_id)
    if structured_items:
        return _render_approval_workbench_items_card(structured_items, raw_answer=raw_answer, chat_id=chat_id)

    text = str(raw_answer).strip()

    elements: list[dict[str, Any]] = [_portal_entry_element(chat_id=chat_id)]
    summary_lines: list[str] = []
    item_count = 0
    current_group = ""
    action_base = {"kind": "runtime_approval_workbench", "chat_id": chat_id or ""}
    selected_indexes = _approval_selected_indexes(chat_id)
    item_pattern = _re.compile(r"^(\d+)[.、]\s*(.+)$")

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        item_match = item_pattern.match(line)
        if item_match:
            index = int(item_match.group(1))
            content = item_match.group(2).strip()
            item_count += 1
            selected = index in selected_indexes
            title_prefix = "☑" if selected else "☐"
            elements.extend(
                [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": f"{title_prefix} **{index}. {content}**",
                        },
                    },
                    {
                        "tag": "action",
                        "layout": "flow",
                        "actions": [
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "取消选择" if selected else "选择"},
                                "type": "primary" if selected else "default",
                                "value": {**action_base, "action": "select", "index": index},
                            },
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "详情"},
                                "type": "default",
                                "multi_url": _portal_url_map(
                                    _portal_sidebar_url(chat_id=chat_id, path="/sidepanel", autoload=True, item_index=index)
                                ),
                            },
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "通过"},
                                "type": "primary",
                                "value": {**action_base, "action": "approve", "index": index},
                            },
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "拒绝"},
                                "type": "danger",
                                "value": {**action_base, "action": "reject", "index": index},
                            },
                        ],
                    },
                ]
            )
            continue
        if line.startswith("**") and line.endswith("**"):
            current_group = line.strip("*")
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**▌ {current_group}**"}})
            continue
        if not elements:
            summary_lines.append(line)

    if item_count <= 0:
        return _render_text_fallback(raw_answer)
    if summary_lines:
        elements.insert(0, {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(summary_lines)}})
    selected_count = len(selected_indexes)
    if selected_indexes:
        elements.insert(
            1 if summary_lines else 0,
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**已选 {len(selected_indexes)} 笔：{_format_selected_indexes(selected_indexes)}**",
                },
            },
        )
    if not elements:
        return _render_text_fallback(raw_answer)
    elements.append({"tag": "hr"})
    elements.append(
        {
            "tag": "action",
            "layout": "flow",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": f"批量通过已选（{selected_count}）"},
                    "type": "primary" if selected_count else "default",
                    "value": {**action_base, "action": "batch_approve"},
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "清空选择"},
                    "type": "default",
                    "value": {**action_base, "action": "clear_selection"},
                },
            ],
        }
    )
    elements.append(
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "单笔处理：点「通过」或「拒绝」后再确认。批量处理：先勾选单据，再点「批量通过已选」。",
            },
        }
    )
    return elements


def _portal_entry_element(*, chat_id: str | None) -> dict[str, Any]:
    workbench_url = _portal_entry_url(chat_id=chat_id, path="/portal")
    workbench_button = _portal_url_button("打开工作台", workbench_url, button_type="default")
    return {
        "tag": "action",
        "layout": "flow",
        "actions": [workbench_button],
    }


def _approval_workbench_top_actions(*, chat_id: str | None, collapsed: bool) -> dict[str, Any]:
    workbench_url = _portal_entry_url(chat_id=chat_id, path="/portal")
    return {
        "tag": "action",
        "layout": "bisected",
        "actions": [
            _portal_url_button("打开工作台", workbench_url, button_type="default"),
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "展开" if collapsed else "折叠"},
                "type": "default",
                "value": {
                    "kind": "runtime_approval_workbench",
                    "chat_id": chat_id or "",
                    "action": "toggle_collapse",
                },
            },
        ],
    }


def _portal_url_button(
    text: str,
    url: str,
    *,
    button_type: str,
    pc_url: str | None = None,
    ios_url: str | None = None,
    android_url: str | None = None,
) -> dict[str, Any]:
    button: dict[str, Any] = {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": button_type,
    }
    button["multi_url"] = {
        "url": url,
        "pc_url": pc_url or url,
        "ios_url": ios_url or url,
        "android_url": android_url or url,
    }
    return button


def _portal_entry_url(
    *,
    chat_id: str | None,
    path: str,
    autoload: bool = False,
    risk: str | None = None,
    prefer_app_link: bool = True,
) -> str:
    query: list[str] = []
    if chat_id:
        query.append(f"chat_id={quote(chat_id, safe='')}")
    if autoload:
        query.append("autoload=1")
    if risk:
        query.append(f"risk={quote(risk, safe='')}")
    page_path = f"{path}?{'&'.join(query)}" if query else path
    if settings.feishu_portal_app_id and prefer_app_link:
        encoded_path = quote(page_path, safe="")
        return f"https://applink.feishu.cn/client/web_app/open?appId={settings.feishu_portal_app_id}&path={encoded_path}"
    return f"{settings.api_base_url.rstrip('/')}{page_path}"


def _portal_direct_url(
    *,
    chat_id: str | None,
    path: str,
    autoload: bool = False,
    item_index: int | None = None,
    risk: str | None = None,
) -> str:
    query: list[str] = []
    if chat_id:
        query.append(f"chat_id={quote(chat_id, safe='')}")
    if autoload:
        query.append("autoload=1")
    if risk:
        query.append(f"risk={quote(risk, safe='')}")
    if item_index is not None and item_index > 0:
        query.append(f"item={item_index}")
        query.append("view=detail")
    page_path = f"{path}?{'&'.join(query)}" if query else path
    return f"{settings.api_base_url.rstrip('/')}{page_path}"


def _portal_sidebar_url(
    *,
    chat_id: str | None,
    path: str,
    autoload: bool = False,
    item_index: int | None = None,
    risk: str | None = None,
) -> str:
    target_url = _portal_direct_url(chat_id=chat_id, path=path, autoload=autoload, item_index=item_index, risk=risk)
    return (
        "https://applink.feishu.cn/client/web_url/open"
        f"?mode=sidebar-semi&max_width=800&reload=false&url={quote(target_url, safe='')}"
    )


def _portal_url_map(url: str) -> dict[str, str]:
    return {"url": url, "pc_url": url, "ios_url": url, "android_url": url}


def _approval_workbench_items_from_result_context(chat_id: str | None) -> tuple[dict[str, Any], ...]:
    result_context = load_result_context(chat_id)
    if result_context is None or not result_context.items:
        return ()
    metadata = result_context.metadata if isinstance(result_context.metadata, dict) else {}
    result_type = str(result_context.result_type or "")
    context_kind = str(metadata.get("context_kind") or "")
    if context_kind not in {"query_result", ""}:
        return ()
    if result_type not in {"approval_list", "approval_query"}:
        return ()
    return tuple(item for item in result_context.items if isinstance(item, dict))


def _approval_workbench_header_title(chat_id: str | None, fallback: str) -> str:
    result_context = load_result_context(chat_id)
    if result_context is not None and result_context.items:
        return f"待审批 {len(result_context.items)} 条"
    return fallback or "待审批"


def _render_approval_workbench_items_card(
    items: tuple[dict[str, Any], ...],
    *,
    raw_answer: str,
    chat_id: str | None,
) -> list[dict[str, Any]]:
    _reset_approval_workbench_ui_state_if_new_result(chat_id)
    grouped = _approval_workbench_grouped_items(items[:20])
    payload = CardPayload(
        type="summary",
        title=f"待审批 {len(items)} 条",
        summary="",
        recommendation="",
        actions=(
            _approval_workbench_filter_card_action(chat_id=chat_id, group="hold", label="查看高风险项", count=len(grouped["hold"])),
            _approval_workbench_filter_card_action(chat_id=chat_id, group="review", label="查看需关注项", count=len(grouped["review"])),
            _approval_workbench_filter_card_action(chat_id=chat_id, group="pass", label="查看可通过项", count=len(grouped["pass"])),
            _approval_workbench_workspace_card_action(chat_id=chat_id),
        ),
    )
    return _render_card_payload(payload)


def _render_card_payload(payload: CardPayload) -> list[dict[str, Any]]:
    if payload.type == "summary":
        return _render_summary_card_payload(payload)
    if payload.type == "action":
        return _render_action_card_payload(payload)
    if payload.type == "feedback":
        return _render_feedback_card_payload(payload)
    if payload.type == "delivery":
        return _render_delivery_card_payload(payload)
    return _render_summary_card_payload(payload)


def _render_summary_card_payload(payload: CardPayload) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    if payload.summary:
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**{payload.summary}**",
                },
            }
        )
    if payload.recommendation:
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**建议**：{payload.recommendation}",
                },
            }
        )
    if payload.actions:
        elements.append({"tag": "hr"})
        for action in payload.actions:
            elements.append(_card_action_element(action))
    return elements


def _render_action_card_payload(payload: CardPayload) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": f"**{payload.summary}**"}},
    ]
    if payload.recommendation:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**建议**：{payload.recommendation}"}})
    if payload.actions:
        elements.append({"tag": "hr"})
        elements.append(_card_actions_element(payload.actions))
    return elements


def _render_feedback_card_payload(payload: CardPayload) -> list[dict[str, Any]]:
    status = f"**状态**：{payload.status}\n" if payload.status else ""
    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": f"{status}**结果**：{payload.summary}"}},
    ]
    if payload.recommendation:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**下一步**：{payload.recommendation}"}})
    if payload.actions:
        elements.append({"tag": "hr"})
        for action in payload.actions:
            elements.append(_card_action_element(action))
    return elements


def _render_delivery_card_payload(payload: CardPayload) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": f"**{payload.summary}**"}},
    ]
    if payload.recommendation:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": payload.recommendation}})
    if payload.actions:
        elements.append({"tag": "hr"})
        for action in payload.actions[:2]:
            elements.append(_card_action_element(action))
    return elements


def _card_action_element(action: CardAction) -> dict[str, Any]:
    button: dict[str, Any] = {
        "tag": "button",
        "text": {"tag": "plain_text", "content": action.label},
        "type": action.style,
        "width": "fill",
    }
    if action.url:
        button["multi_url"] = {
            "url": action.url,
            "pc_url": action.pc_url or action.url,
            "ios_url": action.ios_url or action.url,
            "android_url": action.android_url or action.url,
        }
    if action.value:
        button["value"] = action.value
    return {"tag": "action", "layout": "flow", "actions": [button]}


def _card_actions_element(actions: tuple[CardAction, ...]) -> dict[str, Any]:
    layout = "bisected" if len(actions) == 2 else "flow"
    buttons = []
    for action in actions[:4]:
        button: dict[str, Any] = {
            "tag": "button",
            "text": {"tag": "plain_text", "content": action.label},
            "type": action.style,
        }
        if action.url:
            button["multi_url"] = {
                "url": action.url,
                "pc_url": action.pc_url or action.url,
                "ios_url": action.ios_url or action.url,
                "android_url": action.android_url or action.url,
            }
        if action.value:
            button["value"] = action.value
        buttons.append(button)
    return {"tag": "action", "layout": layout, "actions": buttons}


def _approval_workbench_filter_card_action(*, chat_id: str | None, group: str, label: str, count: int) -> CardAction:
    icon = {"hold": "🔴", "review": "🟡", "pass": "🟢"}.get(group, "")
    direct_url = _portal_entry_url(chat_id=chat_id, path="/portal", autoload=True, risk=group, prefer_app_link=False)
    pc_url = _portal_sidebar_url(chat_id=chat_id, path="/sidepanel", autoload=True, risk=group)
    return CardAction(
        label=f"{icon} {label}（{count}）",
        url=direct_url,
        pc_url=pc_url,
        ios_url=direct_url,
        android_url=direct_url,
    )


def _approval_workbench_workspace_card_action(*, chat_id: str | None) -> CardAction:
    direct_url = _portal_entry_url(chat_id=chat_id, path="/portal", prefer_app_link=False)
    pc_url = _portal_entry_url(chat_id=chat_id, path="/portal", prefer_app_link=True)
    return CardAction(
        label="打开审批工作台",
        url=direct_url,
        pc_url=pc_url,
        ios_url=direct_url,
        android_url=direct_url,
        style="primary",
    )


def _approval_workbench_filter_action(*, chat_id: str | None, group: str, label: str, count: int) -> dict[str, Any]:
    icon = {"hold": "🔴", "review": "🟡", "pass": "🟢"}.get(group, "")
    direct_url = _portal_entry_url(chat_id=chat_id, path="/portal", autoload=True, risk=group, prefer_app_link=False)
    pc_url = _portal_sidebar_url(chat_id=chat_id, path="/sidepanel", autoload=True, risk=group)
    button = _portal_url_button(
        f"{icon} {label}（{count}）",
        direct_url,
        button_type="default",
        pc_url=pc_url,
        ios_url=direct_url,
        android_url=direct_url,
    )
    button["width"] = "fill"
    return {
        "tag": "action",
        "layout": "flow",
        "actions": [button],
    }


def _approval_workbench_group_section(
    *,
    chat_id: str | None,
    group: str,
    title: str,
    count: int,
    grouped: dict[str, list[tuple[int, dict[str, Any]]]],
    expanded_groups: set[str],
) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = [
        {
            "tag": "action",
            "layout": "flow",
            "actions": [
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": _approval_workbench_group_button_text(group=group, label=title, count=count, expanded=group in expanded_groups),
                    },
                    "type": "default",
                    "width": "fill",
                    "value": {"kind": "runtime_approval_workbench", "chat_id": chat_id or "", "action": "toggle_group", "group": group},
                }
            ],
        }
    ]
    group_items = grouped[group]
    if group_items and group in expanded_groups:
        for index, item in group_items:
            detail_url = _portal_sidebar_url(chat_id=chat_id, path="/sidepanel", autoload=True, item_index=index)
            elements.append(_approval_workbench_item_block(index, item, detail_url))
            if group == "review":
                elements.append(_approval_workbench_action_columns(chat_id=chat_id, index=index))
            if group == "pass":
                elements.append(_approval_workbench_pass_item_actions(chat_id=chat_id, index=index))
            if group != "pass":
                elements.append({"tag": "hr"})
        if group == "pass":
            elements.append(_approval_workbench_batch_selected_action(chat_id=chat_id))
    return elements


def _approval_workbench_grouped_items(items: tuple[dict[str, Any], ...]) -> dict[str, list[tuple[int, dict[str, Any]]]]:
    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = {"pass": [], "review": [], "hold": []}
    for index, item in enumerate(items, start=1):
        grouped[_approval_workbench_group_key(item)].append((index, item))
    return grouped


def _approval_workbench_group_key(item: dict[str, Any]) -> str:
    assessment = item.get("assessment") if isinstance(item.get("assessment"), dict) else {}
    risk_level = str(assessment.get("risk_level") or "").strip().lower()
    if risk_level in {"pass", "low"}:
        return "pass"
    if risk_level in {"high", "critical"}:
        return "hold"
    if risk_level in {"review", "medium", "pending"}:
        return "review"
    suggestion = str(assessment.get("suggestion") or "")
    if suggestion in {"可通过", "可初步通过"}:
        return "pass"
    if suggestion in {"拒绝", "补充后再审"}:
        return "hold"
    return "review"


def _approval_workbench_summary_line(
    items: tuple[dict[str, Any], ...],
    grouped: dict[str, list[tuple[int, dict[str, Any]]]],
) -> str:
    return (
        f"**待审批 {len(items)} 条**\n"
        f"高风险 {len(grouped['hold'])} ｜ 需关注 {len(grouped['review'])} ｜ 低风险 {len(grouped['pass'])}"
    )


def _approval_workbench_expanded_groups(chat_id: str | None) -> set[str]:
    session_context = load_session_context(chat_id)
    raw_groups = session_context.get(_APPROVAL_WORKBENCH_EXPANDED_GROUPS_KEY)
    if not isinstance(raw_groups, list):
        return set()
    return {str(group) for group in raw_groups if str(group) in {"hold", "review", "pass"}}


def _reset_approval_workbench_ui_state_if_new_result(chat_id: str | None) -> None:
    if not chat_id:
        return
    result_context = load_result_context(chat_id)
    if result_context is None:
        return
    query_id = str(result_context.query_id or "")
    session_context = load_session_context(chat_id)
    if session_context.get(_APPROVAL_WORKBENCH_QUERY_ID_KEY) == query_id:
        return
    session_context[_APPROVAL_WORKBENCH_QUERY_ID_KEY] = query_id
    session_context.pop(_APPROVAL_WORKBENCH_EXPANDED_GROUPS_KEY, None)
    session_context.pop(_APPROVAL_BATCH_SELECTION_KEY, None)
    save_session_context(chat_id, session_context)


def _approval_workbench_group_controls(
    *,
    chat_id: str | None,
    grouped: dict[str, list[tuple[int, dict[str, Any]]]],
    expanded_groups: set[str],
) -> dict[str, Any]:
    return {
        "tag": "action",
        "layout": "trisection",
        "actions": [
            _approval_workbench_group_button(chat_id=chat_id, group="hold", label="高风险", count=len(grouped["hold"]), expanded="hold" in expanded_groups),
            _approval_workbench_group_button(chat_id=chat_id, group="review", label="需关注", count=len(grouped["review"]), expanded="review" in expanded_groups),
            _approval_workbench_group_button(chat_id=chat_id, group="pass", label="可通过", count=len(grouped["pass"]), expanded="pass" in expanded_groups),
        ],
    }


def _approval_workbench_group_button(*, chat_id: str | None, group: str, label: str, count: int, expanded: bool) -> dict[str, Any]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": _approval_workbench_group_button_text(group=group, label=label, count=count, expanded=expanded)},
        "type": "default",
        "value": {"kind": "runtime_approval_workbench", "chat_id": chat_id or "", "action": "toggle_group", "group": group},
    }


def _approval_workbench_group_button_text(*, group: str, label: str, count: int, expanded: bool) -> str:
    icon = {"hold": "🔴", "review": "🟡", "pass": "🟢"}.get(group, "")
    prefix = "收起" if expanded else "展开"
    return f"{icon}  {prefix}{label} {count}"


def _portal_workbench_action(*, chat_id: str | None) -> dict[str, Any]:
    direct_url = _portal_entry_url(chat_id=chat_id, path="/portal", prefer_app_link=False)
    pc_url = _portal_entry_url(chat_id=chat_id, path="/portal", prefer_app_link=True)
    button = _portal_url_button(
        "打开审批工作台",
        direct_url,
        button_type="primary",
        pc_url=pc_url,
        ios_url=direct_url,
        android_url=direct_url,
    )
    button["width"] = "fill"
    return {
        "tag": "action",
        "layout": "flow",
        "actions": [button],
    }


def _approval_workbench_batch_pass_action(*, chat_id: str | None, indexes: list[int]) -> dict[str, Any]:
    return {
        "tag": "action",
        "layout": "flow",
        "actions": [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"全部通过低风险（{len(indexes)}）"},
                "type": "primary",
                "value": {
                    "kind": "runtime_approval_workbench",
                    "chat_id": chat_id or "",
                    "action": "batch_approve_group",
                    "group": "pass",
                },
            }
        ],
    }


def _approval_workbench_select_action(*, chat_id: str | None, index: int) -> dict[str, Any]:
    selected = index in _approval_selected_indexes(chat_id)
    return {
        "tag": "action",
        "layout": "flow",
        "actions": [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "取消选择" if selected else "选择"},
                "type": "primary" if selected else "default",
                "value": {"kind": "runtime_approval_workbench", "chat_id": chat_id or "", "action": "select", "index": index},
            }
        ],
    }


def _approval_workbench_pass_item_actions(*, chat_id: str | None, index: int) -> dict[str, Any]:
    selected = index in _approval_selected_indexes(chat_id)
    return {
        "tag": "action",
        "layout": "trisection",
        "actions": [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "取消" if selected else "选择"},
                "type": "primary" if selected else "default",
                "value": {"kind": "runtime_approval_workbench", "chat_id": chat_id or "", "action": "select", "index": index},
            },
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "同意"},
                "type": "primary",
                "value": {"kind": "runtime_approval_workbench", "chat_id": chat_id or "", "action": "approve", "index": index},
            },
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "拒绝"},
                "type": "default",
                "value": {"kind": "runtime_approval_workbench", "chat_id": chat_id or "", "action": "reject", "index": index},
            },
        ],
    }


def _approval_workbench_batch_selected_action(*, chat_id: str | None) -> dict[str, Any]:
    selected_count = len(_approval_selected_indexes(chat_id))
    return {
        "tag": "action",
        "layout": "flow",
        "actions": [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"通过已选择项（{selected_count}）"},
                "type": "primary" if selected_count else "default",
                "value": {
                    "kind": "runtime_approval_workbench",
                    "chat_id": chat_id or "",
                    "action": "batch_approve",
                },
            }
        ],
    }


def _approval_workbench_item_markdown(index: int, item: dict[str, Any], detail_url: str, *, compact: bool = False) -> str:
    title = _approval_workbench_display_title(item)
    applicant = str(item.get("applicant") or "").strip()
    amount = item.get("amount")
    amount_text = f"{amount:g}元" if isinstance(amount, (int, float)) else str(amount or "").strip()
    assessment = item.get("assessment") if isinstance(item.get("assessment"), dict) else {}
    reason = _short_text(str(assessment.get("reason") or "信息不足，建议展开核对").strip(), 34 if compact else 46)
    head = "｜".join(part for part in (f"{index}. {title}", amount_text, applicant) if part)
    return f"[**{head}**]({detail_url})\n{reason}"


def _approval_workbench_item_block(index: int, item: dict[str, Any], detail_url: str) -> dict[str, Any]:
    title = _approval_workbench_display_title(item)
    applicant = str(item.get("applicant") or "").strip() or "未知"
    amount = item.get("amount")
    amount_text = f"{amount:g}元" if isinstance(amount, (int, float)) else str(amount or "").strip() or "金额未识别"
    assessment = item.get("assessment") if isinstance(item.get("assessment"), dict) else {}
    reason = _short_text(str(assessment.get("reason") or "信息不足，建议展开核对").strip(), 44)
    return {
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": (
                f"[**{index}. {title}**]({detail_url})\n"
                f"申请人：{applicant}　金额：{amount_text}\n"
                f"{reason}"
            ),
        },
    }


def _approval_workbench_action_columns(*, chat_id: str | None, index: int) -> dict[str, Any]:
    return {
        "tag": "column_set",
        "flex_mode": "none",
        "background_style": "default",
        "columns": [
            {
                "tag": "column",
                "width": "weighted",
                "weight": 1,
                "vertical_align": "top",
                "elements": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "拒绝"},
                        "type": "default",
                        "value": {"kind": "runtime_approval_workbench", "chat_id": chat_id or "", "action": "reject", "index": index},
                    }
                ],
            },
            {
                "tag": "column",
                "width": "weighted",
                "weight": 1,
                "vertical_align": "top",
                "elements": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "同意"},
                        "type": "primary",
                        "value": {"kind": "runtime_approval_workbench", "chat_id": chat_id or "", "action": "approve", "index": index},
                    }
                ],
            },
        ],
    }


def _approval_workbench_item_line(item: dict[str, Any]) -> str:
    title = _approval_workbench_display_title(item)
    applicant = str(item.get("applicant") or "").strip()
    amount = item.get("amount")
    amount_text = f"{amount:g}元" if isinstance(amount, (int, float)) else str(amount or "").strip()
    assessment = item.get("assessment") if isinstance(item.get("assessment"), dict) else {}
    reason = str(assessment.get("reason") or "信息不足，建议展开核对").strip()
    parts = [title]
    if amount_text:
        parts.append(amount_text)
    if applicant:
        parts.append(applicant)
    if reason:
        parts.append(reason)
    return "｜".join(parts)


def _short_text(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else f"{text[:limit - 1]}…"



def _feedback_payload_from_dict(data: dict[str, Any]) -> CardPayload | None:
    result_type = str(data.get("result_type") or data.get("type") or "").strip()
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    context_kind = str(metadata.get("context_kind") or data.get("context_kind") or "").strip()
    if result_type != "runtime_action" and context_kind != "action_receipt":
        return None
    status = str(metadata.get("execution_status") or data.get("status") or "已处理").strip()
    answer = str(data.get("answer") or data.get("summary") or "操作已处理。").strip()
    title = str(data.get("title") or metadata.get("operation") or "执行结果").strip()
    next_step = str(metadata.get("recommended_next_step") or "如需查看详情或继续处理，可继续追问或进入工作台。")
    return CardPayload(
        type="feedback",
        title=title,
        summary=answer,
        recommendation=next_step,
        status=_feedback_status_label(status),
    )


def _feedback_status_label(status: str) -> str:
    return {
        "success": "成功",
        "partial": "部分完成",
        "error": "失败",
        "failed": "失败",
        "denied": "无权限",
        "cancelled": "已取消",
        "stale": "已失效",
        "skipped": "已跳过",
        "pending_confirmation": "待确认",
    }.get(str(status or ""), str(status or "已处理"))

def _delivery_payload_from_dict(data: dict[str, Any]) -> CardPayload | None:
    url = _first_nonempty(data, ("url", "link", "open_url", "file_url", "spreadsheet_url", "base_url"))
    app_token = _first_nonempty(data, ("app_token", "base_app_token"))
    table_id = _first_nonempty(data, ("table_id", "sheet_id"))
    title = _first_nonempty(data, ("title", "name", "file_name")) or "交付内容"
    rows = data.get("row_count") or data.get("rows_written") or data.get("count")
    if not (url or app_token or table_id or rows):
        return None
    summary_parts = [str(title)]
    if rows:
        summary_parts.append(f"已写入 {rows} 行")
    if app_token:
        summary_parts.append("已创建多维表格")
    elif table_id:
        summary_parts.append("已创建表格")
    summary = "，".join(summary_parts) + "。"
    actions: list[CardAction] = []
    if url:
        actions.append(CardAction(label="打开", url=str(url), style="primary"))
    return CardPayload(
        type="delivery",
        title="内容已生成",
        summary=summary,
        recommendation="如需继续整理、分享或批量处理，请进入对应工作台。",
        actions=tuple(actions),
        status="success",
    )


def _first_nonempty(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return ""


def _approval_workbench_display_title(item: dict[str, Any]) -> str:
    title = str(item.get("title") or item.get("name") or "未命名审批").strip()
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    candidates = [
        title,
        str(raw.get("title") or ""),
        str(raw.get("name") or ""),
        str(raw.get("approval_name") or ""),
        str(raw.get("approval_definition_name") or ""),
    ]
    normalized = " ".join(candidates).lower()
    if "reserve fund" in normalized:
        return "借款申请"
    if "reimbursement" in normalized:
        return "费用报销"
    return title or "未命名审批"


# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------

def _render_confirmation_card(raw_answer: str, *, chat_id: str | None = None) -> list[dict[str, Any]]:
    fields = _confirmation_fields(raw_answer)
    title = _confirmation_title(fields)
    pending = load_session_context(chat_id).get("runtime_v5_pending_action") if chat_id else None
    pending_message = str(pending.get("message") or "").strip() if isinstance(pending, dict) else ""
    summary_parts = []
    request_text = pending_message or fields.get("请求", "")
    if request_text:
        summary_parts.append(f"请求：{request_text}")
    for label in ("审批对象", "申请人", "金额", "发送对象", "消息摘要", "处理意见"):
        value = fields.get(label)
        if value:
            summary_parts.append(f"{label}：{value}")
    summary = "\n".join(summary_parts) or fields.get("影响") or "这项操作将替你执行实际写入或发送。"
    recommendation = _confirmation_recommendation(fields.get("确认原因", ""))
    action_value = {
        "kind": "runtime_confirmation",
        "chat_id": chat_id or "",
        "pending_action_id": fields.get("确认编号", ""),
        "confirmation_token": fields.get("确认编号", ""),
    }
    return _render_card_payload(
        CardPayload(
            type="action",
            title=title,
            summary=summary,
            recommendation=recommendation,
            actions=(
                CardAction(label="确认", value={**action_value, "action": "confirm"}, style="primary"),
                CardAction(label="取消", value={**action_value, "action": "cancel"}),
            ),
        )
    )


def _confirmation_fields(raw_answer: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in str(raw_answer).splitlines():
        if "：" not in line:
            continue
        key, value = line.split("：", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            fields[key] = value
    return fields


def _confirmation_recommendation(reason: str) -> str:
    raw = str(reason or "").strip()
    if not raw:
        return "确认后立即提交，取消则不执行。"
    labels = [item.strip() for item in raw.replace("、", ",").split(",") if item.strip()]
    if any(item in {"能力要求二次确认", "高风险动作", "行动类请求"} for item in labels):
        return "这会替你执行写入、发送或创建操作，请确认无误后继续。"
    return raw


def _confirmation_title(fields: dict[str, str]) -> str:
    action = fields.get("动作", "")
    if "通过审批" in action:
        return "确认通过审批"
    if "拒绝审批" in action:
        return "确认拒绝审批"
    if "发送飞书消息" in action:
        return "确认发送消息"
    if "创建并写入组织架构表" in action:
        return "确认创建表格"
    if "创建日程" in action:
        return "确认创建日程"
    if "创建任务" in action:
        return "确认创建任务"
    if "创建邮件草稿" in action:
        return "确认创建邮件草稿"
    return "请确认是否执行这项操作"


def _approval_selected_indexes(chat_id: str | None) -> set[int]:
    session_context = load_session_context(chat_id)
    raw = session_context.get(_APPROVAL_BATCH_SELECTION_KEY)
    if not isinstance(raw, list):
        return set()
    selected: set[int] = set()
    for item in raw:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value > 0:
            selected.add(value)
    return selected


def _format_selected_indexes(indexes: set[int]) -> str:
    return "、".join(str(item) for item in sorted(indexes))


# ---------------------------------------------------------------------------
# Fallback: plain text
# ---------------------------------------------------------------------------

def _render_text_fallback(raw_answer: str) -> list[dict[str, Any]]:
    text = str(raw_answer)[:3000]
    return [{"tag": "div", "text": {"tag": "lark_md", "content": text}}]
