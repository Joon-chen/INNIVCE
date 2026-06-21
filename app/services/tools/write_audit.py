import json
from collections.abc import Mapping
from typing import Any


WRITE_AUDIT_IGNORED_PARAM_KEYS = {"app_config", "client", "confirmation_token"}


def write_target_metadata(tool_name: str, params: Mapping[str, Any] | None) -> dict[str, object]:
    params = params or {}
    target: dict[str, object] = {
        "tool_name": tool_name,
        "operation": _write_operation(tool_name),
        "param_keys": [
            key
            for key in sorted(params)
            if key not in WRITE_AUDIT_IGNORED_PARAM_KEYS and key not in {"dry_run", "confirmed"}
        ],
    }
    identifiers = _write_target_identifiers(params)
    if identifiers:
        target["identifiers"] = identifiers
    title = _write_target_title(tool_name, params)
    if title:
        target["title"] = title
    fields = params.get("fields")
    if tool_name == "feishu_bitable_table_create" and isinstance(fields, list):
        target["field_keys"] = [
            str(item.get("name"))
            for item in fields
            if isinstance(item, dict) and item.get("name")
        ]
    elif isinstance(fields, dict):
        target["field_keys"] = [str(key) for key in sorted(fields)]
    elif isinstance(fields, list):
        target["field_keys"] = [str(item) for item in fields if item]
    if tool_name in {"feishu_bitable_field_create", "feishu_bitable_field_update"}:
        field = params.get("field") or params.get("field_property")
        if isinstance(field, dict) and field.get("name"):
            target["field_keys"] = [str(field["name"])]
        elif params.get("name"):
            target["field_keys"] = [str(params["name"])]
    if tool_name == "feishu_bitable_view_set_filter":
        filter_config = params.get("filter") or params.get("filter_config")
        if isinstance(filter_config, dict):
            logic = str(filter_config.get("logic") or "").strip()
            if logic:
                target["filter_logic"] = logic
            conditions = filter_config.get("conditions")
            if isinstance(conditions, list):
                target["condition_count"] = len(conditions)
    if tool_name == "feishu_bitable_view_set_sort":
        sort_config = params.get("sort") or params.get("sort_config")
        if isinstance(sort_config, dict) and isinstance(sort_config.get("sort_config"), list):
            items = sort_config["sort_config"]
            target["sort_count"] = len(items)
            fields = [str(item.get("field")) for item in items if isinstance(item, dict) and item.get("field")]
            if fields:
                target["sort_fields"] = fields
    if tool_name == "feishu_bitable_view_set_group":
        group_config = params.get("group") or params.get("group_config")
        if isinstance(group_config, dict) and isinstance(group_config.get("group_config"), list):
            items = group_config["group_config"]
            target["group_count"] = len(items)
            fields = [str(item.get("field")) for item in items if isinstance(item, dict) and item.get("field")]
            if fields:
                target["group_fields"] = fields
    if tool_name == "feishu_bitable_view_set_visible_fields":
        value = params.get("visible_fields_config") or params.get("visible_fields")
        visible_fields = value.get("visible_fields") if isinstance(value, dict) else value
        fields = _string_list(visible_fields)
        if fields:
            target["visible_field_count"] = len(fields)
            target["visible_fields"] = fields[:50]
    if tool_name == "feishu_bitable_view_set_card":
        card = params.get("card") or params.get("card_config")
        has_cover_field = isinstance(card, dict) and "cover_field" in card
        cover_field = card.get("cover_field") if isinstance(card, dict) else params.get("cover_field")
        if cover_field is not None:
            target["cover_field"] = str(cover_field)
        elif has_cover_field:
            target["cover_field_clear"] = True
    if tool_name == "feishu_bitable_view_set_timebar":
        timebar = params.get("timebar") or params.get("timebar_config")
        config = timebar if isinstance(timebar, dict) else params
        fields = {
            key: str(config[key])
            for key in ("start_time", "end_time", "title")
            if config.get(key) is not None
        }
        if fields:
            target["timebar_fields"] = fields
    patch = params.get("patch")
    if isinstance(patch, dict):
        target["field_keys"] = [str(key) for key in sorted(patch)]
    rows = params.get("rows")
    if isinstance(rows, list):
        target["row_count"] = len(rows)
    record_id_list = params.get("record_id_list")
    if isinstance(record_id_list, list):
        target["record_count"] = len(record_id_list)
    if tool_name == "feishu_bitable_record_upsert":
        target["upsert_mode"] = "update_by_record_id" if params.get("record_id") else "create"
    update_fields = params.get("update_fields")
    if isinstance(update_fields, list):
        target["update_fields"] = [str(item) for item in update_fields]
    reminder_minutes = _reminder_minutes(params)
    if reminder_minutes is not None:
        target["relative_fire_minutes"] = reminder_minutes
    _apply_approval_write_metadata(target, params)
    for source in (
        "add_assignees",
        "remove_assignees",
        "add_followers",
        "remove_followers",
        "add_sign_user_ids",
        "cc_user_ids",
    ):
        value = params.get(source)
        if isinstance(value, list):
            target[source] = [str(item) for item in value if item]
    for source in ("add_members", "remove_members", "set_members"):
        value = params.get(source)
        if isinstance(value, list):
            target[source] = [str(item) for item in value if item]
    members = params.get("members")
    if isinstance(members, list):
        target["member_count"] = len(members)
    editors = params.get("editors") or params.get("member_ids")
    if isinstance(editors, list):
        target["member_count"] = len(editors)
    archive_tasklist = _optional_bool(params.get("archive_tasklist"))
    if tool_name == "feishu_tasklist_create" and archive_tasklist is not None:
        target["archive_tasklist"] = archive_tasklist
    if tool_name.startswith("feishu_task_section_"):
        for key in ("resource_type", "resource_id", "insert_before", "insert_after"):
            value = params.get(key)
            if value:
                target[key] = str(value)
    if tool_name == "feishu_task_upload_attachment":
        resource_type = str(params.get("resource_type") or "task").strip()
        if resource_type:
            target["resource_type"] = resource_type
        file_path = str(params.get("file_path") or params.get("file") or "").strip()
        if file_path:
            target["file_name"] = file_path.rsplit("/", 1)[-1]
    if tool_name == "feishu_bitable_record_upload_attachment":
        files = _string_list(params.get("files") or params.get("file_paths") or params.get("file"))
        if files:
            target["file_count"] = len(files)
            target["file_names"] = [item.rsplit("/", 1)[-1] for item in files][:20]
    if tool_name == "feishu_bitable_record_remove_attachment":
        file_tokens = _string_list(params.get("file_tokens") or params.get("file_token"))
        if file_tokens:
            target["file_token_count"] = len(file_tokens)
    return target


def write_target_summary(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    operation = str(value.get("operation") or value.get("tool_name") or "").strip()
    identifiers = value.get("identifiers") if isinstance(value.get("identifiers"), dict) else {}
    title = str(value.get("title") or "").strip()
    fields = value.get("field_keys") if isinstance(value.get("field_keys"), list) else []
    update_fields = value.get("update_fields") if isinstance(value.get("update_fields"), list) else []
    reminder_minutes = value.get("relative_fire_minutes") if isinstance(value.get("relative_fire_minutes"), list) else []
    add_assignees = value.get("add_assignees") if isinstance(value.get("add_assignees"), list) else []
    remove_assignees = value.get("remove_assignees") if isinstance(value.get("remove_assignees"), list) else []
    add_followers = value.get("add_followers") if isinstance(value.get("add_followers"), list) else []
    remove_followers = value.get("remove_followers") if isinstance(value.get("remove_followers"), list) else []
    add_sign_user_ids = value.get("add_sign_user_ids") if isinstance(value.get("add_sign_user_ids"), list) else []
    cc_user_ids = value.get("cc_user_ids") if isinstance(value.get("cc_user_ids"), list) else []
    add_members = value.get("add_members") if isinstance(value.get("add_members"), list) else []
    remove_members = value.get("remove_members") if isinstance(value.get("remove_members"), list) else []
    set_members = value.get("set_members") if isinstance(value.get("set_members"), list) else []
    form_count = value.get("form_count") if isinstance(value.get("form_count"), int) else None
    form_fields = value.get("form_fields") if isinstance(value.get("form_fields"), list) else []
    comment_present = value.get("comment_present") if isinstance(value.get("comment_present"), bool) else None
    add_sign_type = value.get("add_sign_type") if isinstance(value.get("add_sign_type"), int) else None
    approval_method = value.get("approval_method") if isinstance(value.get("approval_method"), int) else None
    row_count = value.get("row_count") if isinstance(value.get("row_count"), int) else None
    record_count = value.get("record_count") if isinstance(value.get("record_count"), int) else None
    condition_count = value.get("condition_count") if isinstance(value.get("condition_count"), int) else None
    sort_count = value.get("sort_count") if isinstance(value.get("sort_count"), int) else None
    sort_fields = value.get("sort_fields") if isinstance(value.get("sort_fields"), list) else []
    group_count = value.get("group_count") if isinstance(value.get("group_count"), int) else None
    group_fields = value.get("group_fields") if isinstance(value.get("group_fields"), list) else []
    visible_field_count = value.get("visible_field_count") if isinstance(value.get("visible_field_count"), int) else None
    visible_fields = value.get("visible_fields") if isinstance(value.get("visible_fields"), list) else []
    cover_field = str(value.get("cover_field") or "").strip()
    cover_field_clear = value.get("cover_field_clear") if isinstance(value.get("cover_field_clear"), bool) else None
    timebar_fields = value.get("timebar_fields") if isinstance(value.get("timebar_fields"), dict) else {}
    member_count = value.get("member_count") if isinstance(value.get("member_count"), int) else None
    archive_tasklist = value.get("archive_tasklist") if isinstance(value.get("archive_tasklist"), bool) else None
    upsert_mode = str(value.get("upsert_mode") or "").strip()
    filter_logic = str(value.get("filter_logic") or "").strip()
    resource_type = str(value.get("resource_type") or "").strip()
    resource_id = str(value.get("resource_id") or "").strip()
    insert_before = str(value.get("insert_before") or "").strip()
    insert_after = str(value.get("insert_after") or "").strip()
    file_name = str(value.get("file_name") or "").strip()
    file_count = value.get("file_count") if isinstance(value.get("file_count"), int) else None
    file_names = value.get("file_names") if isinstance(value.get("file_names"), list) else []
    file_token_count = value.get("file_token_count") if isinstance(value.get("file_token_count"), int) else None
    parts = [operation] if operation else []
    if identifiers:
        parts.extend(f"{key}={identifiers[key]}" for key in sorted(identifiers))
    if title:
        parts.append(f"title={title}")
    if fields:
        parts.append("fields=" + ",".join(str(item) for item in fields))
    if row_count is not None:
        parts.append(f"rows={row_count}")
    if record_count is not None:
        parts.append(f"records={record_count}")
    if condition_count is not None:
        parts.append(f"conditions={condition_count}")
    if sort_count is not None:
        parts.append(f"sorts={sort_count}")
    if sort_fields:
        parts.append("sort_fields=" + ",".join(str(item) for item in sort_fields))
    if group_count is not None:
        parts.append(f"groups={group_count}")
    if group_fields:
        parts.append("group_fields=" + ",".join(str(item) for item in group_fields))
    if visible_field_count is not None:
        parts.append(f"visible_fields={visible_field_count}")
    if visible_fields:
        parts.append("visible_field_names=" + ",".join(str(item) for item in visible_fields))
    if cover_field:
        parts.append(f"cover_field={cover_field}")
    elif cover_field_clear:
        parts.append("cover_field=clear")
    if timebar_fields:
        parts.append(
            "timebar_fields="
            + ",".join(f"{key}:{timebar_fields[key]}" for key in ("start_time", "end_time", "title") if key in timebar_fields)
        )
    if filter_logic:
        parts.append(f"filter_logic={filter_logic}")
    if member_count is not None:
        parts.append(f"members={member_count}")
    if archive_tasklist is not None:
        parts.append(f"archive_tasklist={str(archive_tasklist).lower()}")
    if resource_type:
        parts.append(f"resource_type={resource_type}")
    if resource_id:
        parts.append(f"resource_id={resource_id}")
    if insert_before:
        parts.append(f"insert_before={insert_before}")
    if insert_after:
        parts.append(f"insert_after={insert_after}")
    if file_name:
        parts.append(f"file_name={file_name}")
    if file_count is not None:
        parts.append(f"files={file_count}")
    if file_names:
        parts.append("file_names=" + ",".join(str(item) for item in file_names))
    if file_token_count is not None:
        parts.append(f"file_tokens={file_token_count}")
    if upsert_mode:
        parts.append(f"upsert_mode={upsert_mode}")
    if update_fields:
        parts.append("update_fields=" + ",".join(str(item) for item in update_fields))
    if reminder_minutes:
        parts.append("relative_fire_minutes=" + ",".join(str(item) for item in reminder_minutes))
    if add_assignees:
        parts.append("add_assignees=" + ",".join(str(item) for item in add_assignees))
    if remove_assignees:
        parts.append("remove_assignees=" + ",".join(str(item) for item in remove_assignees))
    if add_followers:
        parts.append("add_followers=" + ",".join(str(item) for item in add_followers))
    if remove_followers:
        parts.append("remove_followers=" + ",".join(str(item) for item in remove_followers))
    if add_sign_user_ids:
        parts.append("add_sign_user_ids=" + ",".join(str(item) for item in add_sign_user_ids))
    if cc_user_ids:
        parts.append("cc_user_ids=" + ",".join(str(item) for item in cc_user_ids))
    if add_members:
        parts.append("add_members=" + ",".join(str(item) for item in add_members))
    if remove_members:
        parts.append("remove_members=" + ",".join(str(item) for item in remove_members))
    if set_members:
        parts.append("set_members=" + ",".join(str(item) for item in set_members))
    if form_count is not None:
        parts.append(f"form_count={form_count}")
    if form_fields:
        parts.append("form_fields=" + ",".join(str(item) for item in form_fields))
    if comment_present is not None:
        parts.append(f"comment_present={str(comment_present).lower()}")
    if add_sign_type is not None:
        parts.append(f"add_sign_type={add_sign_type}")
    if approval_method is not None:
        parts.append(f"approval_method={approval_method}")
    return " / ".join(parts) if parts else None


def _write_operation(tool_name: str) -> str:
    for prefix, operation in (
        ("feishu_bitable_record_", "bitable_record"),
        ("feishu_bitable_table_", "bitable_table"),
        ("feishu_bitable_field_", "bitable_field"),
        ("feishu_bitable_view_", "bitable_view"),
        ("feishu_approval_instance_", "approval_instance"),
        ("feishu_approval_task_", "approval_task"),
        ("feishu_tasklist_", "tasklist"),
        ("feishu_task_section_", "task_section"),
        ("feishu_task_", "task"),
        ("feishu_calendar_", "calendar"),
        ("feishu_im_", "im"),
    ):
        if tool_name.startswith(prefix):
            return f"{operation}.{tool_name.removeprefix(prefix)}"
    return tool_name


def _apply_approval_write_metadata(target: dict[str, object], params: Mapping[str, Any]) -> None:
    if params.get("comment") is not None:
        target["comment_present"] = bool(str(params.get("comment") or "").strip())
    form = _approval_form_metadata(params.get("form"))
    if form:
        target.update(form)
    add_sign_type = _optional_int(params.get("add_sign_type"))
    if add_sign_type is not None:
        target["add_sign_type"] = add_sign_type
    approval_method = _optional_int(params.get("approval_method"))
    if approval_method is not None:
        target["approval_method"] = approval_method


def _approval_form_metadata(value: Any) -> dict[str, object]:
    form = value
    if isinstance(value, str):
        if not value.strip():
            return {}
        try:
            form = json.loads(value)
        except json.JSONDecodeError:
            return {"form_present": True}
    if not isinstance(form, list):
        return {"form_present": True}
    fields = [_approval_form_field_key(item) for item in form if isinstance(item, Mapping)]
    return {
        "form_count": len(form),
        "form_fields": [field for field in fields if field][:20],
    }


def _approval_form_field_key(field: Mapping[str, Any]) -> str | None:
    for key in ("id", "field_id", "custom_id", "name", "field_name"):
        value = field.get(key)
        if value:
            return str(value)
    return None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off"}:
            return False
    return None


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def _write_target_identifiers(params: Mapping[str, Any]) -> dict[str, str]:
    identifier_keys = (
        "approval_code",
        "instance_code",
        "task_id",
        "transfer_user_id",
        "task_guid",
        "parent_task_guid",
        "parent_guid",
        "tasklist_guid",
        "resource_id",
        "ancestor_guid",
        "ancestor_task_guid",
        "section_guid",
        "field_id",
        "view_id",
        "app_token",
        "table_id",
        "record_id",
        "calendar_id",
        "event_id",
        "chat_id",
        "name",
        "open_id",
        "receive_id",
    )
    identifiers: dict[str, str] = {}
    item = params.get("item")
    if isinstance(item, dict):
        for key in ("approval_code", "instance_code", "task_id"):
            value = item.get(key)
            if value:
                identifiers[key] = str(value)
    for key in identifier_keys:
        value = params.get(key)
        if value:
            identifiers[key] = str(value)
    chat_ids = params.get("chat_ids")
    if isinstance(chat_ids, list):
        cleaned = [str(item) for item in chat_ids if item]
        if cleaned:
            identifiers["chat_ids"] = ",".join(cleaned)
    record_ids = params.get("record_id_list")
    if isinstance(record_ids, list):
        cleaned = [str(item) for item in record_ids if item]
        if cleaned:
            identifiers["record_ids"] = ",".join(cleaned)
    task_ids = params.get("task_ids")
    if isinstance(task_ids, list):
        cleaned = [str(item) for item in task_ids if item]
        if cleaned:
            identifiers["task_ids"] = ",".join(cleaned)
    node_ids = params.get("node_ids")
    if isinstance(node_ids, list):
        cleaned = [str(item) for item in node_ids if item]
        if cleaned:
            identifiers["node_ids"] = ",".join(cleaned)
    return identifiers


def _write_target_title(tool_name: str, params: Mapping[str, Any]) -> str | None:
    if tool_name in {
        "feishu_tasklist_create",
        "feishu_tasklist_update",
        "feishu_task_section_create",
        "feishu_task_section_update",
        "feishu_bitable_table_create",
        "feishu_bitable_view_create",
        "feishu_bitable_view_rename",
    }:
        value = params.get("name")
        if not value and tool_name == "feishu_bitable_view_create":
            view = params.get("view")
            if isinstance(view, Mapping):
                value = view.get("name")
        return str(value) if value else None
    if tool_name not in {"feishu_task_create", "feishu_task_subtask_create", "feishu_calendar_create_event"}:
        return None
    value = params.get("summary")
    if not value:
        return None
    return str(value)


def _reminder_minutes(params: Mapping[str, Any]) -> list[int] | None:
    value = params.get("positive_reminders")
    if isinstance(value, list):
        minutes: list[int] = []
        for item in value:
            if isinstance(item, Mapping) and item.get("relative_fire_minute") is not None:
                try:
                    minutes.append(int(item["relative_fire_minute"]))
                except (TypeError, ValueError):
                    continue
        return minutes
    value = params.get("relative_fire_minutes")
    if value is None:
        value = params.get("relative_fire_minute")
    if isinstance(value, list):
        minutes = []
        for item in value:
            try:
                minutes.append(int(item))
            except (TypeError, ValueError):
                continue
        return minutes
    if value is not None:
        try:
            return [int(value)]
        except (TypeError, ValueError):
            return []
    return None
