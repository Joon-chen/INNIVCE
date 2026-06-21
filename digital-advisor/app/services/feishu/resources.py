from collections.abc import Iterable
from datetime import UTC, datetime
import json
import re
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import Account, BotUserAccess, Company, FeishuAppConfig, Resource, WorkEvent
from app.services.feishu.approval import FeishuApprovalService
from app.services.feishu.client import FeishuClient
from app.services.feishu.sync import _safe_feishu_error
from app.services.resource_registry import migrate_legacy_feishu_resources, upsert_feishu_discovered_resource


DEFAULT_FEISHU_RESOURCE_KINDS = [
    "contacts",
    "calendar",
    "meetings",
    "tasks",
    "chats",
    "drive",
    "wiki",
    "bitable",
    "approvals",
    "local",
]


async def discover_feishu_resources(
    db: Session,
    app_config: FeishuAppConfig,
    *,
    kinds: list[str],
    mailbox_id: str | None = None,
    mailbox_ids: list[str] | None = None,
    app_tokens: list[str] | None = None,
    bitable_tables: list[str] | None = None,
    document_ids: list[str] | None = None,
    wiki_space_ids: list[str] | None = None,
    folder_tokens: list[str] | None = None,
    docs_search_keywords: list[str] | None = None,
    approval_codes: list[str] | None = None,
    limit: int = 50,
    include_local_mining: bool = True,
) -> dict[str, Any]:
    selected = _normalize_discovery_kinds(kinds or DEFAULT_FEISHU_RESOURCE_KINDS)
    client = FeishuClient(app_config)
    discovered: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    bitable_tokens = set(app_tokens or [])

    discovered.extend(_capability_resources(selected))
    discovered.extend(_explicit_approval_resources(approval_codes or []))
    discovered.extend(_explicit_document_resources(document_ids or []))
    discovered.extend(_explicit_wiki_resources(wiki_space_ids or []))
    discovered.extend(_explicit_folder_resources(folder_tokens or []))
    discovered.extend(_explicit_bitable_table_resources(bitable_tables or []))

    if "approvals" in selected:
        items, approval_errors = await _discover_approval_resources(db, client, company_id=app_config.company_id, limit=limit)
        discovered.extend(items)
        errors.extend(approval_errors)

    if "chats" in selected:
        items, error = await _discover_chats(client, limit=limit)
        discovered.extend(items)
        if error:
            errors.append(error)

    if "mail" in selected:
        mail_items, mail_errors = await _discover_mail_resources(
            db,
            client,
            company_id=app_config.company_id,
            explicit_mailbox_ids=[item for item in [mailbox_id, *(mailbox_ids or [])] if item],
        )
        discovered.extend(mail_items)
        errors.extend(mail_errors)

    if "drive" in selected or "bitable" in selected:
        folder_items, folder_errors = await _discover_drive_folder_tree(
            client,
            folder_tokens=_folder_tokens_from_resources(db, app_config=app_config, discovered=discovered),
            limit=limit,
        )
        discovered.extend(folder_items)
        bitable_tokens.update(_bitable_tokens_from_drive(folder_items))
        errors.extend(folder_errors)

        items, error = await _discover_drive_files(client, limit=limit)
        discovered.extend(items)
        bitable_tokens.update(_bitable_tokens_from_drive(items))
        if error:
            errors.append(error)

        search_items, search_errors = await _discover_docs_by_search(
            db,
            client,
            app_config=app_config,
            keywords=docs_search_keywords or [],
            limit=limit,
        )
        discovered.extend(search_items)
        bitable_tokens.update(_bitable_tokens_from_drive(search_items))
        errors.extend(search_errors)

        user_items, user_errors = await _discover_user_drive_files(
            db,
            app_config=app_config,
            client=client,
            limit=limit,
        )
        discovered.extend(user_items)
        bitable_tokens.update(_bitable_tokens_from_drive(user_items))
        errors.extend(user_errors)

    if "wiki" in selected:
        items, error = await _discover_wiki_spaces(client, limit=limit)
        discovered.extend(items)
        if error:
            errors.append(error)
        user_items, user_errors = await _discover_user_wiki_spaces(
            db,
            app_config=app_config,
            client=client,
            limit=limit,
        )
        discovered.extend(user_items)
        errors.extend(user_errors)

    if "local" in selected and include_local_mining:
        local_items = discover_local_resource_candidates(db, company_id=app_config.company_id, limit=max(limit * 10, 100))
        discovered.extend(local_items)
        bitable_tokens.update(
            item["external_id"]
            for item in local_items
            if item.get("resource_type") == "bitable_app" and item.get("external_id")
        )

    if "bitable" in selected:
        items, bitable_errors = await _discover_bitable_tables(client, app_tokens=sorted(bitable_tokens), limit=limit)
        discovered.extend(items)
        errors.extend(bitable_errors)
        user_items, user_errors = await _discover_user_bitable_tables(
            db,
            app_config=app_config,
            client=client,
            app_tokens=sorted(bitable_tokens),
            limit=limit,
        )
        discovered.extend(user_items)
        errors.extend(user_errors)

    saved = []
    for item in _dedupe_discovered(discovered):
        registered_resource = upsert_feishu_discovered_resource(
            db,
            app_config=app_config,
            item=item,
        )
        db.flush()
        _link_local_work_events_to_resource(db, registered_resource, item)
        saved.append(_discovered_resource_payload(registered_resource))
    db.flush()
    return {
        "saved_count": len(saved),
        "items": saved,
        "errors": errors,
        "coverage": _resource_coverage(selected, saved, errors),
        "next_steps": _resource_next_steps(saved),
    }


def _discovered_resource_payload(
    registered_resource: Resource,
    legacy_resource: Any | None = None,
) -> dict[str, Any]:
    config_json = registered_resource.config_json or {}
    return {
        "id": str(registered_resource.id),
        "resource_id": registered_resource.resource_id,
        "resource_sub_id": registered_resource.resource_sub_id,
        "resource_type": registered_resource.resource_type,
        "resource_name": registered_resource.resource_name,
        "platform": registered_resource.platform,
        "sync_mode": registered_resource.sync_mode,
        "permission_level": registered_resource.permission_level,
        "data_classification": registered_resource.data_classification,
        "business_domain": registered_resource.business_domain,
        "enabled": registered_resource.enabled,
        "app_config_id": str(registered_resource.app_config_id) if registered_resource.app_config_id else None,
        "config_json": registered_resource.config_json,
        "external_id": config_json.get("external_id")
        or (legacy_resource.external_id if legacy_resource else registered_resource.resource_id),
        "name": registered_resource.resource_name,
        "sync_enabled": registered_resource.enabled,
        "settings": config_json.get("settings") or {},
        "legacy_id": str(legacy_resource.id) if legacy_resource else None,
        "legacy_resource_type": legacy_resource.resource_type if legacy_resource else None,
        "legacy_external_id": legacy_resource.external_id if legacy_resource else None,
        "legacy_name": legacy_resource.name if legacy_resource else None,
        "legacy_sync_enabled": legacy_resource.sync_enabled if legacy_resource else None,
        "legacy_settings": legacy_resource.settings if legacy_resource else None,
        "resource_mapping": {
            "platform": registered_resource.platform,
            "resource_type": registered_resource.resource_type,
            "resource_id": registered_resource.resource_id,
            "resource_sub_id": registered_resource.resource_sub_id,
            "sync_mode": registered_resource.sync_mode,
            "permission_level": registered_resource.permission_level,
        },
    }


def _link_local_work_events_to_resource(db: Session, resource: Resource, item: dict[str, Any]) -> None:
    settings = item.get("settings") if isinstance(item.get("settings"), dict) else {}
    source = str(settings.get("source") or "")
    if not source.startswith("local_work_event"):
        return
    if resource.resource_type != "chat" or not resource.resource_id:
        return
    db.execute(
        update(WorkEvent)
        .where(WorkEvent.company_id == resource.company_id)
        .where(WorkEvent.source == "feishu")
        .where(WorkEvent.thread_id == resource.resource_id)
        .where(WorkEvent.resource_id.is_(None))
        .values(resource_id=resource.id)
    )


def discover_local_resource_candidates(db: Session, *, company_id, limit: int = 500) -> list[dict[str, Any]]:
    events = db.scalars(
        select(WorkEvent)
        .where(WorkEvent.company_id == company_id)
        .where(WorkEvent.source == "feishu")
        .order_by(WorkEvent.occurred_at.desc())
        .limit(limit)
    ).all()
    candidates: list[dict[str, Any]] = []
    for event in events:
        if event.thread_id and _is_chat_event(event):
            candidates.append(
                _resource(
                    "chat",
                    event.thread_id,
                    event.title or event.thread_id,
                    {"source": "local_work_event", "work_event_id": str(event.id)},
                )
            )
        candidates.extend(extract_resource_candidates_from_payload(event.payload or {}, source_work_event_id=str(event.id)))
        candidates.extend(
            extract_resource_candidates_from_text(
                _event_search_text(event),
                source_work_event_id=str(event.id),
            )
        )
    return _dedupe_discovered(candidates)


def _is_chat_event(event: WorkEvent) -> bool:
    labels = {str(label).lower() for label in (event.labels or [])}
    event_type = (event.event_type or "").lower()
    thread_id = event.thread_id or ""
    if "mail" in labels or "mail" in event_type:
        return False
    return (
        thread_id.startswith("oc_")
        or "im" in event_type
        or "message" in event_type
        or "chat" in labels
        or "message" in labels
    )


def extract_resource_candidates_from_payload(
    payload: dict[str, Any],
    *,
    source_work_event_id: str | None = None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in _walk_dicts(payload):
        chat_id = _first_string(item, ["chat_id", "open_chat_id"])
        if chat_id:
            candidates.append(_resource("chat", chat_id, _first_string(item, ["chat_name", "name"]) or chat_id, _local_settings(source_work_event_id)))

        app_token = _first_string(item, ["app_token", "bitable_token"])
        if app_token:
            candidates.append(_resource("bitable_app", app_token, _first_string(item, ["name", "title"]) or app_token, _local_settings(source_work_event_id)))
            table_id = _first_string(item, ["table_id"])
            if table_id:
                candidates.append(
                    _resource(
                        "bitable_table",
                        f"{app_token}:{table_id}",
                        _first_string(item, ["table_name", "name"]) or table_id,
                        {**_local_settings(source_work_event_id), "app_token": app_token, "table_id": table_id},
                    )
                )

        approval_code = _first_string(item, ["approval_code", "definition_code"])
        if approval_code:
            candidates.append(
                _resource(
                    "approval_code",
                    approval_code,
                    _first_string(item, ["approval_name", "name"]) or approval_code,
                    _local_settings(source_work_event_id),
                )
            )

        document_id = _first_string(item, ["document_id", "file_token", "obj_token", "spreadsheet_token"])
        if document_id:
            candidates.append(
                _resource(
                    "drive_file",
                    document_id,
                    _first_string(item, ["title", "name"]) or document_id,
                    {**_local_settings(source_work_event_id), "document_type": _first_string(item, ["type", "file_type"])},
                )
            )

        folder_token = _first_string(item, ["folder_token", "folder_id"])
        if folder_token:
            candidates.append(
                _resource(
                    "drive_folder",
                    folder_token,
                    _first_string(item, ["folder_name", "name", "title"]) or folder_token,
                    {**_local_settings(source_work_event_id), "folder_token": folder_token},
                )
            )
    return _dedupe_discovered(candidates)


def extract_resource_candidates_from_text(
    text: str,
    *,
    source_work_event_id: str | None = None,
) -> list[dict[str, Any]]:
    if not text:
        return []
    candidates: list[dict[str, Any]] = []
    settings = _local_settings(source_work_event_id)

    for app_token in _unique_strings(_BITABLE_APP_RE.findall(text)):
        candidates.append(_resource("bitable_app", app_token, app_token, {**settings, "source_detail": "text_token"}))

    app_tokens = _unique_strings(_BITABLE_APP_RE.findall(text))
    table_tokens = _unique_strings(_BITABLE_TABLE_RE.findall(text))
    for app_token, table_id in _bitable_table_pairs_from_text(text, app_tokens, table_tokens):
        candidates.append(
            _resource(
                "bitable_table",
                f"{app_token}:{table_id}",
                table_id,
                {**settings, "app_token": app_token, "table_id": table_id, "source_detail": "text_token"},
            )
        )

    for document_id in _unique_strings(_DOCUMENT_TOKEN_RE.findall(text)):
        candidates.append(
            _resource(
                "drive_file",
                document_id,
                document_id,
                {**settings, "document_type": _document_type_for_token(document_id), "source_detail": "text_token"},
            )
        )

    for folder_token in _unique_strings([*_FOLDER_TOKEN_RE.findall(text), *_folder_tokens_from_links(text)]):
        candidates.append(
            _resource(
                "drive_folder",
                folder_token,
                folder_token,
                {**settings, "folder_token": folder_token, "source_detail": "text_token"},
            )
        )

    for wiki_id in _unique_strings(_WIKI_TOKEN_RE.findall(text)):
        candidates.append(_resource("wiki_space", wiki_id, wiki_id, {**settings, "source_detail": "text_token"}))

    return _dedupe_discovered(candidates)


async def _discover_approval_resources(
    db: Session,
    client: FeishuClient,
    *,
    company_id,
    limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    users = db.scalars(
        select(BotUserAccess)
        .where(BotUserAccess.company_id == company_id)
        .where(BotUserAccess.is_active.is_(True))
        .where(BotUserAccess.role.in_(("owner", "admin")))
        .order_by(BotUserAccess.updated_at.desc())
        .limit(5)
    ).all()
    if not users:
        return [], [
            {
                "kind": "approvals",
                "error": "未发现 owner/admin 的飞书 open_id，无法自动探测个人审批任务；机器人收到一次老板或管理员私聊后会自动补齐。",
            }
        ]

    service = FeishuApprovalService(None, client=client)
    discovered: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for user in users:
        result = await service.fetch_user_pending_tasks(open_id=user.open_id, limit=limit, names_by_code={})
        if not result.get("available"):
            errors.append({"kind": "approvals", "error": str(result.get("error") or "审批任务探测失败")[:500]})
            continue
        for item in result.get("items") or []:
            approval_code = _first_string(item, ["approval_code", "definition_code"])
            if not approval_code:
                continue
            approval_name = _first_string(item, ["approval_name", "name"]) or approval_code
            discovered.append(
                _resource(
                    "approval_code",
                    approval_code,
                    approval_name,
                    {
                        "source": "feishu_approval_tasks_query",
                        "usage": "approval_resource_identifier",
                        "discovered_from_role": user.role,
                    },
                )
            )
    return _dedupe_discovered(discovered), errors


async def _discover_mail_resources(
    db: Session,
    client: FeishuClient,
    *,
    company_id,
    explicit_mailbox_ids: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    mailbox_ids = _mailbox_candidates(db, company_id=company_id, explicit_mailbox_ids=explicit_mailbox_ids)
    if not mailbox_ids:
        return [], [
            {
                "kind": "mail",
                "error": "未提供要监控的邮箱，也没有默认飞书邮箱配置；请在邮箱入口手动录入或完成邮箱授权。",
            }
        ]

    discovered: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for mailbox_id in mailbox_ids:
        items, error = await _discover_mail_folders(client, mailbox_id=mailbox_id)
        discovered.extend(items)
        if error:
            errors.append(error)
    return _dedupe_discovered(discovered), errors


def _mailbox_candidates(db: Session, *, company_id, explicit_mailbox_ids: list[str] | None = None) -> list[str]:
    candidates: list[str] = []
    candidates.extend(explicit_mailbox_ids or [])
    if settings.auto_feishu_mail_user_mailbox_id:
        candidates.append(settings.auto_feishu_mail_user_mailbox_id)

    users = db.scalars(
        select(BotUserAccess)
        .where(BotUserAccess.company_id == company_id)
        .where(BotUserAccess.is_active.is_(True))
        .where(BotUserAccess.role.in_(("owner", "admin")))
        .order_by(BotUserAccess.updated_at.desc())
        .limit(100)
    ).all()
    for user in users:
        user_settings = user.settings or {}
        if isinstance(user_settings, dict):
            email = str(user_settings.get("email") or "").strip()
            if email:
                candidates.append(email)
    return _unique_strings(candidates)


async def _discover_chats(client: FeishuClient, *, limit: int) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
    try:
        body = await client.api_get("/open-apis/im/v1/chats", params={"page_size": min(limit, 100)})
    except HTTPException as exc:
        return [], {"kind": "chats", "error": _safe_feishu_error(exc.detail)}
    items = _items(body.get("data") or {}, ["items", "chats"])
    return [
        _resource(
            "chat",
            _first_string(item, ["chat_id", "open_chat_id", "id"]) or "",
            _first_string(item, ["name", "chat_name", "avatar"]) or "飞书群组",
            {"source": "feishu_api", "raw": item},
        )
        for item in items
        if _first_string(item, ["chat_id", "open_chat_id", "id"])
    ], None


async def _discover_mail_folders(
    client: FeishuClient,
    *,
    mailbox_id: str,
) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
    try:
        mailbox = quote(mailbox_id, safe="")
        body = await client.api_get(f"/open-apis/mail/v1/user_mailboxes/{mailbox}/folders")
    except HTTPException as exc:
        return [], {"kind": "mail", "error": _safe_feishu_error(exc.detail)}
    except Exception as exc:
        return [], {"kind": "mail", "external_id": mailbox_id, "error": str(exc)[:500] or exc.__class__.__name__}
    items = _items(body.get("data") or {}, ["items", "folders"])
    return [
        _resource(
            "mail_folder",
            f"{mailbox_id}:{_first_string(item, ['id', 'folder_id'])}",
            _first_string(item, ["name"]) or _first_string(item, ["id", "folder_id"]) or "邮箱文件夹",
            {"source": "feishu_api", "user_mailbox_id": mailbox_id, "folder_id": _first_string(item, ["id", "folder_id"]), "raw": item},
        )
        for item in items
        if _first_string(item, ["id", "folder_id"])
    ], None


async def _discover_drive_files(
    client: FeishuClient,
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
    try:
        body = await client.api_get("/open-apis/drive/v1/files", params={"page_size": min(limit, 100)})
    except HTTPException as exc:
        return [], {"kind": "drive", "error": _safe_feishu_error(exc.detail)}
    items = _items(body.get("data") or {}, ["files", "items"])
    return _drive_file_resources(items, source="feishu_api"), None


async def _discover_drive_folder_tree(
    client: FeishuClient,
    *,
    folder_tokens: list[str],
    limit: int,
    max_depth: int = 2,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    seeds = _unique_strings(folder_tokens)
    if not seeds:
        return [], []

    resources: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    queue: list[tuple[str, int]] = [(token, 0) for token in seeds]
    visited: set[str] = set()
    while queue and len(visited) < max(limit * 3, 30):
        folder_token, depth = queue.pop(0)
        if folder_token in visited or depth > max_depth:
            continue
        visited.add(folder_token)
        resources.append(
            _resource(
                "drive_folder",
                folder_token,
                folder_token,
                {"source": "feishu_drive_folder_tree", "folder_token": folder_token, "depth": depth},
            )
        )
        try:
            body = await client.api_get(
                "/open-apis/drive/v1/files",
                params={"page_size": min(limit, 100), "folder_token": folder_token},
            )
        except HTTPException as exc:
            errors.append({"kind": "drive_folder", "folder_token": folder_token, "error": _safe_feishu_error(exc.detail)})
            continue
        items = _items(body.get("data") or {}, ["files", "items"])
        folder_resources = _drive_file_resources(items, source="feishu_drive_folder_tree", parent_folder_token=folder_token)
        resources.extend(folder_resources)
        for child_folder in _folder_tokens_from_discovered_items(folder_resources):
            if child_folder not in visited:
                queue.append((child_folder, depth + 1))
    return _dedupe_discovered(resources), errors


def _drive_file_resources(
    items: list[dict[str, Any]],
    *,
    source: str,
    account: Account | None = None,
    parent_folder_token: str | None = None,
) -> list[dict[str, Any]]:
    resources: list[dict[str, Any]] = []
    for item in items:
        token = _first_string(item, ["token", "file_token", "document_id", "spreadsheet_token", "app_token"])
        if not token:
            continue
        file_type = _first_string(item, ["type", "file_type"])
        settings = {"source": source, "document_type": file_type, "raw": item}
        if parent_folder_token:
            settings["parent_folder_token"] = parent_folder_token
        if account is not None:
            settings["account_id"] = str(account.id)
            settings["account_label"] = account.display_name
        if str(file_type).lower() == "folder":
            folder_settings = {**settings, "folder_token": token}
            resources.append(
                _resource(
                    "drive_folder",
                    token,
                    _first_string(item, ["name", "title"]) or token,
                    folder_settings,
                )
            )
            continue
        resources.append(
            _resource(
                "drive_file",
                token,
                _first_string(item, ["name", "title"]) or token,
                settings,
            )
        )
        if str(file_type).lower() in {"bitable", "base"}:
            resources.append(
                _resource(
                    "bitable_app",
                    token,
                    _first_string(item, ["name", "title"]) or token,
                    {key: value for key, value in settings.items() if key != "document_type"},
                )
            )
    return resources


async def _discover_docs_by_search(
    db: Session,
    client: FeishuClient,
    *,
    app_config: FeishuAppConfig,
    keywords: list[str],
    limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    search_terms = _docs_search_keywords(db, app_config=app_config, explicit_keywords=keywords)
    if not search_terms:
        return [], []

    resources: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for keyword in search_terms:
        try:
            body = await client.api_post(
                "/open-apis/suite/docs-api/search/object",
                {
                    "search_key": keyword,
                    "count": min(limit, 100),
                    "offset": 0,
                    "docs_types": ["bitable", "sheet", "doc", "docx", "file"],
                },
            )
        except HTTPException as exc:
            errors.append({"kind": "docs_search", "keyword": keyword, "error": _safe_feishu_error(exc.detail)})
            continue
        except Exception as exc:
            errors.append({"kind": "docs_search", "keyword": keyword, "error": str(exc)[:500] or exc.__class__.__name__})
            continue
        resources.extend(_docs_search_resources(_items(body.get("data") or {}, ["docs_entities"]), keyword=keyword))
    return _dedupe_discovered(resources), errors


def _docs_search_keywords(
    db: Session,
    *,
    app_config: FeishuAppConfig,
    explicit_keywords: list[str],
) -> list[str]:
    company = db.get(Company, app_config.company_id)
    candidates = [
        *(explicit_keywords or []),
        company.name if company else "",
        company.code if company else "",
        "经营分析",
        "业务管理",
        "项目",
        "客户",
        "销售",
        "航空",
    ]
    return _unique_strings(candidates)[:8]


def _docs_search_resources(items: list[dict[str, Any]], *, keyword: str) -> list[dict[str, Any]]:
    resources: list[dict[str, Any]] = []
    for item in items:
        token = _first_string(item, ["docs_token", "token", "file_token", "app_token"])
        if not token:
            continue
        docs_type = _first_string(item, ["docs_type", "type", "file_type"])
        title = _first_string(item, ["title", "name"]) or token
        settings = {"source": "feishu_docs_search", "document_type": docs_type, "keyword": keyword, "raw": item}
        resources.append(_resource("drive_file", token, title, settings))
        if str(docs_type).lower() == "bitable":
            resources.append(
                _resource(
                    "bitable_app",
                    token,
                    title,
                    {"source": "feishu_docs_search", "keyword": keyword, "raw": item},
                )
            )
    return resources


async def _discover_user_drive_files(
    db: Session,
    *,
    app_config: FeishuAppConfig,
    client: FeishuClient,
    limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    accounts = _feishu_user_accounts(db, company_id=app_config.company_id)
    if not accounts:
        return [], [{"kind": "feishu_user", "error": "未完成用户级能力包授权，无法发现个人客户端里最近/可见的多维表和云文档。"}]

    resources: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for account in accounts:
        token, error = await _user_access_token(db, account=account, app_config=app_config, client=client)
        if not token:
            errors.append({"kind": "feishu_user", "account_id": str(account.id), "error": error or "missing user_access_token"})
            continue
        try:
            body = await client.api_get_user(
                "/open-apis/drive/v1/files",
                user_access_token=token,
                params={"page_size": min(limit, 100)},
            )
        except HTTPException as exc:
            errors.append({"kind": "drive", "source": "feishu_user", "account_id": str(account.id), "error": _safe_feishu_error(exc.detail)})
            continue
        items = _items(body.get("data") or {}, ["files", "items"])
        for resource in _drive_file_resources(items, source="feishu_user_oauth", account=account):
            resources.append(resource)
    return _dedupe_discovered(resources), errors


async def _discover_bitable_tables(
    client: FeishuClient,
    *,
    app_tokens: Iterable[str],
    limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    resources: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for app_token in app_tokens:
        if not app_token:
            continue
        try:
            body = await client.api_get(
                f"/open-apis/bitable/v1/apps/{quote(app_token, safe='')}/tables",
                params={"page_size": min(limit, 100)},
            )
        except HTTPException as exc:
            errors.append({"kind": "bitable", "external_id": app_token, "error": _safe_feishu_error(exc.detail)})
            continue
        for item in _items(body.get("data") or {}, ["items", "tables"]):
            table_id = _first_string(item, ["table_id", "id"])
            if not table_id:
                continue
            resources.append(
                _resource(
                    "bitable_table",
                    f"{app_token}:{table_id}",
                    _first_string(item, ["name", "table_name"]) or table_id,
                    {"source": "feishu_api", "app_token": app_token, "table_id": table_id, "raw": item},
                )
            )
    return resources, errors


async def _discover_user_bitable_tables(
    db: Session,
    *,
    app_config: FeishuAppConfig,
    client: FeishuClient,
    app_tokens: Iterable[str],
    limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    accounts = _feishu_user_accounts(db, company_id=app_config.company_id)
    if not accounts or not app_tokens:
        return [], []

    resources: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for account in accounts:
        token, error = await _user_access_token(db, account=account, app_config=app_config, client=client)
        if not token:
            errors.append({"kind": "feishu_user", "account_id": str(account.id), "error": error or "missing user_access_token"})
            continue
        for app_token in app_tokens:
            if not app_token:
                continue
            try:
                body = await client.api_get_user(
                    f"/open-apis/bitable/v1/apps/{quote(app_token, safe='')}/tables",
                    user_access_token=token,
                    params={"page_size": min(limit, 100)},
                )
            except HTTPException as exc:
                errors.append(
                    {
                        "kind": "bitable",
                        "source": "feishu_user",
                        "account_id": str(account.id),
                        "external_id": app_token,
                        "error": _safe_feishu_error(exc.detail),
                    }
                )
                continue
            for item in _items(body.get("data") or {}, ["items", "tables"]):
                table_id = _first_string(item, ["table_id", "id"])
                if not table_id:
                    continue
                resources.append(
                    _resource(
                        "bitable_table",
                        f"{app_token}:{table_id}",
                        _first_string(item, ["name", "table_name"]) or table_id,
                        {
                            "source": "feishu_user_oauth",
                            "account_id": str(account.id),
                            "account_label": account.display_name,
                            "app_token": app_token,
                            "table_id": table_id,
                            "raw": item,
                        },
                    )
                )
    return _dedupe_discovered(resources), errors


async def _discover_wiki_spaces(client: FeishuClient, *, limit: int) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
    try:
        body = await client.api_get("/open-apis/wiki/v2/spaces", params={"page_size": min(limit, 50)})
    except HTTPException as exc:
        return [], {"kind": "wiki", "error": _safe_feishu_error(exc.detail)}
    items = _items(body.get("data") or {}, ["items", "spaces"])
    return [
        _resource(
            "wiki_space",
            _first_string(item, ["space_id", "id"]) or "",
            _first_string(item, ["name", "title"]) or "飞书知识空间",
            {"source": "feishu_api", "raw": item},
        )
        for item in items
        if _first_string(item, ["space_id", "id"])
    ], None


async def _discover_user_wiki_spaces(
    db: Session,
    *,
    app_config: FeishuAppConfig,
    client: FeishuClient,
    limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    accounts = _feishu_user_accounts(db, company_id=app_config.company_id)
    if not accounts:
        return [], []

    resources: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for account in accounts:
        token, error = await _user_access_token(db, account=account, app_config=app_config, client=client)
        if not token:
            errors.append({"kind": "feishu_user", "account_id": str(account.id), "error": error or "missing user_access_token"})
            continue
        try:
            body = await client.api_get_user(
                "/open-apis/wiki/v2/spaces",
                user_access_token=token,
                params={"page_size": min(limit, 50)},
            )
        except HTTPException as exc:
            errors.append({"kind": "wiki", "source": "feishu_user", "account_id": str(account.id), "error": _safe_feishu_error(exc.detail)})
            continue
        for item in _items(body.get("data") or {}, ["items", "spaces"]):
            wiki_id = _first_string(item, ["space_id", "id"])
            if not wiki_id:
                continue
            resources.append(
                _resource(
                    "wiki_space",
                    wiki_id,
                    _first_string(item, ["name", "title"]) or "飞书知识空间",
                    {
                        "source": "feishu_user_oauth",
                        "account_id": str(account.id),
                        "account_label": account.display_name,
                        "raw": item,
                    },
                )
            )
    return _dedupe_discovered(resources), errors


def _capability_resources(selected: set[str]) -> list[dict[str, Any]]:
    specs = {
        "contacts": ("capability", "feishu:contacts", "通讯录与组织架构", "scheduled"),
        "calendar": ("capability", "feishu:calendar", "日历日程", "scheduled"),
        "meetings": ("capability", "feishu:meetings", "视频会议", "scheduled"),
        "tasks": ("capability", "feishu:tasks", "飞书任务", "scheduled"),
    }
    return [
        _resource(
            resource_type,
            external_id,
            name,
            {"source": "capability_registry", "sync_mode": sync_mode, "permission_level": "company"},
        )
        for key, (resource_type, external_id, name, sync_mode) in specs.items()
        if key in selected
    ]


def _explicit_approval_resources(approval_codes: Iterable[str]) -> list[dict[str, Any]]:
    return [
        _resource(
            "approval_code",
            code,
            code,
            {"source": "explicit_config", "usage": "approval_resource_identifier"},
        )
        for code in _unique_strings(approval_codes)
    ]


def _feishu_user_accounts(db: Session, *, company_id) -> list[Account]:
    return list(
        db.scalars(
            select(Account)
            .where(Account.company_id == company_id)
            .where(Account.provider == "feishu_user")
            .where(Account.is_active.is_(True))
            .order_by(Account.updated_at.desc())
            .limit(5)
        ).all()
    )


async def _user_access_token(
    db: Session,
    *,
    account: Account,
    app_config: FeishuAppConfig,
    client: FeishuClient,
) -> tuple[str | None, str | None]:
    credentials = account.credentials or {}
    token = credentials.get("access_token")
    if token and not _token_expired(credentials.get("expires_at")):
        return str(token), None

    refresh_token = credentials.get("refresh_token")
    if not refresh_token:
        return None, "用户级能力包授权已失效，请由资源所有者本人重新授权。"
    try:
        refreshed = await client.refresh_user_access_token(refresh_token=str(refresh_token))
    except HTTPException as exc:
        return None, _safe_feishu_error(exc.detail)
    from app.services.feishu.user_accounts import refresh_feishu_user_account

    refresh_feishu_user_account(account, app_config=app_config, token_response=refreshed)
    db.flush()
    new_token = (account.credentials or {}).get("access_token")
    return (str(new_token), None) if new_token else (None, "刷新后仍缺少 user_access_token")


def _token_expired(value: Any) -> bool:
    if not value:
        return True
    try:
        expires_at = datetime.fromisoformat(str(value))
    except ValueError:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= datetime.now(UTC)


def _explicit_document_resources(document_ids: Iterable[str]) -> list[dict[str, Any]]:
    return [
        _resource(
            "drive_file",
            document_id,
            document_id,
            {
                "source": "explicit_config",
                "document_type": _document_type_for_token(document_id),
                "usage": "document_index_identifier",
            },
        )
        for document_id in _unique_strings(document_ids)
    ]


def _explicit_wiki_resources(wiki_space_ids: Iterable[str]) -> list[dict[str, Any]]:
    return [
        _resource(
            "wiki_space",
            wiki_id,
            wiki_id,
            {"source": "explicit_config", "usage": "wiki_index_identifier"},
        )
        for wiki_id in _unique_strings(wiki_space_ids)
    ]


def _explicit_folder_resources(folder_tokens: Iterable[str]) -> list[dict[str, Any]]:
    return [
        _resource(
            "drive_folder",
            folder_token,
            folder_token,
            {"source": "explicit_config", "folder_token": folder_token, "usage": "drive_folder_tree_seed"},
        )
        for folder_token in _unique_strings(folder_tokens)
    ]


def _explicit_bitable_table_resources(values: Iterable[str]) -> list[dict[str, Any]]:
    resources: list[dict[str, Any]] = []
    for value in _unique_strings(values):
        app_token, table_id = _split_bitable_table_identifier(value)
        if not app_token or not table_id:
            continue
        resources.append(
            _resource(
                "bitable_table",
                f"{app_token}:{table_id}",
                table_id,
                {
                    "source": "explicit_config",
                    "app_token": app_token,
                    "table_id": table_id,
                    "usage": "bitable_master_data_identifier",
                },
            )
        )
    return resources


def _normalize_discovery_kinds(kinds: Iterable[str]) -> set[str]:
    aliases = {
        "contact": "contacts",
        "directory": "contacts",
        "calendars": "calendar",
        "meeting": "meetings",
        "task": "tasks",
        "docs": "drive",
        "doc": "drive",
        "documents": "drive",
        "bases": "bitable",
        "base": "bitable",
        "approval": "approvals",
    }
    selected: set[str] = set()
    for kind in kinds:
        text = str(kind or "").strip().lower()
        if text:
            selected.add(aliases.get(text, text))
    return selected


def _bitable_tokens_from_drive(items: list[dict[str, Any]]) -> set[str]:
    return {
        item["external_id"]
        for item in items
        if item.get("resource_type") == "bitable_app" and item.get("external_id")
    }


def _folder_tokens_from_resources(
    db: Session,
    *,
    app_config: FeishuAppConfig,
    discovered: list[dict[str, Any]],
) -> list[str]:
    candidates: list[str | None] = []
    candidates.extend(_folder_tokens_from_discovered_items(discovered))
    v5_existing = db.scalars(
        select(Resource)
        .where(Resource.company_id == app_config.company_id)
        .where(Resource.platform == "feishu")
        .where(Resource.resource_type == "drive_folder")
        .where(Resource.enabled.is_(True))
        .limit(100)
    ).all()
    for resource in v5_existing:
        candidates.extend(_folder_tokens_from_v5_resource(resource))
    for migration in migrate_legacy_feishu_resources(db, app_config=app_config, resource_type="drive_folder", limit=100):
        candidates.append(migration.external_id)
        candidates.append(migration.settings.get("folder_token"))
    configured = app_config.settings.get("drive_folder_tokens") if isinstance(app_config.settings, dict) else None
    if isinstance(configured, list):
        candidates.extend(str(item) for item in configured)
    return _unique_strings(candidates)


def _folder_tokens_from_v5_resource(resource: Resource) -> list[str | None]:
    config = resource.config_json if isinstance(resource.config_json, dict) else {}
    settings = config.get("settings") if isinstance(config.get("settings"), dict) else {}
    return [
        resource.resource_id,
        resource.resource_sub_id,
        config.get("folder_token"),
        settings.get("folder_token"),
    ]


def _folder_tokens_from_discovered_items(items: Iterable[dict[str, Any]]) -> list[str]:
    tokens: list[str | None] = []
    for item in items:
        if item.get("resource_type") == "drive_folder":
            tokens.append(item.get("external_id"))
            settings = item.get("settings") if isinstance(item.get("settings"), dict) else {}
            tokens.append(settings.get("folder_token"))
    return _unique_strings(tokens)


def _dedupe_discovered(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str | None]] = set()
    results: list[dict[str, Any]] = []
    for item in items:
        resource_type = str(item.get("resource_type") or "").strip()
        external_id = str(item.get("external_id") or "").strip()
        if not resource_type or not external_id:
            continue
        key = _discovered_resource_identity(resource_type, external_id, item)
        if key in seen:
            continue
        seen.add(key)
        results.append({**item, "resource_type": resource_type, "external_id": external_id})
    return results


def _discovered_resource_identity(
    resource_type: str,
    external_id: str,
    item: dict[str, Any],
) -> tuple[str, str, str | None]:
    settings = item.get("settings") if isinstance(item.get("settings"), dict) else {}
    if resource_type == "mail_folder":
        return (
            resource_type,
            str(settings.get("user_mailbox_id") or external_id.split(":", 1)[0]),
            str(settings.get("folder_id") or external_id.split(":", 1)[-1]),
        )
    if resource_type == "bitable_table":
        return (
            resource_type,
            str(settings.get("app_token") or external_id.split(":", 1)[0]),
            str(settings.get("table_id") or external_id.split(":", 1)[-1]),
        )
    return (resource_type, external_id, None)


def _resource(resource_type: str, external_id: str, name: str | None, settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "resource_type": resource_type,
        "external_id": external_id,
        "name": name,
        "sync_enabled": True,
        "settings": settings,
    }


def _resource_next_steps(items: list[dict[str, Any]]) -> list[str]:
    types = {item.get("resource_type") for item in items}
    steps = []
    if "chat" in types:
        steps.append("群组资源已自动登记，可用于消息事件和历史补数。")
    if "mail_folder" in types:
        steps.append("邮箱资源已自动登记，可用于邮件线程摘要和附件索引同步。")
    if "drive_folder" in types:
        steps.append("云空间文件夹已自动登记，可用于目录树优先的文档和多维表发现。")
    if "bitable_table" in types:
        steps.append("多维表格资源已自动登记，可用于主数据索引同步。")
    if "approval_code" in types:
        steps.append("审批资源已自动登记，可用于审批实例、待办和附件摘要同步。")
    if "wiki_space" in types:
        steps.append("知识库资源已自动登记，默认只做目录和索引同步。")
    if "capability" in types:
        steps.append("capability 资源表示已登记的飞书原生模块，可用于后续自动同步编排。")
    return steps


def _resource_coverage(
    selected: set[str],
    items: list[dict[str, Any]],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    counts = {kind: 0 for kind in sorted(selected)}
    for item in items:
        for kind in _coverage_kinds_for_item(item):
            if kind in counts:
                counts[kind] += 1
    error_kinds = {str(error.get("kind") or "") for error in errors}
    return {
        "counts": counts,
        "covered": [kind for kind, count in counts.items() if count > 0],
        "not_found": [kind for kind, count in counts.items() if count == 0 and kind not in error_kinds],
        "error_kinds": sorted(kind for kind in error_kinds if kind),
    }


def _coverage_kinds_for_item(item: dict[str, Any]) -> list[str]:
    resource_type = str(item.get("resource_type") or "")
    external_id = str(item.get("external_id") or "")
    settings = item.get("settings") if isinstance(item.get("settings"), dict) else {}
    source = str(settings.get("source") or "")
    kinds = ["local"] if source.startswith("local_work_event") else []
    if resource_type == "chat":
        return [*kinds, "chats"]
    if resource_type == "mail_folder":
        return [*kinds, "mail"]
    if resource_type == "approval_code":
        return [*kinds, "approvals"]
    if resource_type in {"drive_file", "drive_folder"}:
        return [*kinds, "drive"]
    if resource_type in {"bitable_app", "bitable_table"}:
        return [*kinds, "bitable"]
    if resource_type == "wiki_space":
        return [*kinds, "wiki"]
    if resource_type == "capability":
        capability_kinds = {
            "feishu:contacts": ["contacts"],
            "feishu:calendar": ["calendar"],
            "feishu:meetings": ["meetings"],
            "feishu:tasks": ["tasks"],
        }.get(external_id, [])
        return [*kinds, *capability_kinds]
    return kinds


_TOKEN_CHARS = r"[A-Za-z0-9_-]{8,}"
_BITABLE_APP_RE = re.compile(r"\b(bascn" + _TOKEN_CHARS + r")\b")
_BITABLE_TABLE_RE = re.compile(r"\b(tbl" + _TOKEN_CHARS + r")\b")
_DOCUMENT_TOKEN_RE = re.compile(r"\b((?:doccn|doxcn|docxcn|shtcn|sheetscn)" + _TOKEN_CHARS + r")\b")
_FOLDER_TOKEN_RE = re.compile(r"\b(fld" + _TOKEN_CHARS + r")\b")
_WIKI_TOKEN_RE = re.compile(r"\b((?:wikcn|spc)" + _TOKEN_CHARS + r")\b")
_BITABLE_LINK_RE = re.compile(
    r"/base/(?P<app>bascn[A-Za-z0-9_-]+)[^\s]*?[?&](?:table|table_id)=(?P<table>tbl[A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
_FOLDER_LINK_RE = re.compile(r"/drive/(?:folder|home)/(?P<folder>fld[A-Za-z0-9_-]+)", re.IGNORECASE)


def _bitable_table_pairs_from_text(
    text: str,
    app_tokens: list[str],
    table_tokens: list[str],
) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for match in _BITABLE_LINK_RE.finditer(text):
        pairs.append((match.group("app"), match.group("table")))
    if len(app_tokens) == 1:
        pairs.extend((app_tokens[0], table_id) for table_id in table_tokens)
    return _unique_pairs(pairs)


def _folder_tokens_from_links(text: str) -> list[str]:
    return [match.group("folder") for match in _FOLDER_LINK_RE.finditer(text)]


def _split_bitable_table_identifier(value: str) -> tuple[str | None, str | None]:
    text = value.strip()
    if ":" in text:
        app_token, table_id = text.split(":", 1)
        return app_token.strip() or None, table_id.strip() or None
    parts = re.split(r"[,\\s]+", text)
    app_token = next((part for part in parts if _BITABLE_APP_RE.fullmatch(part)), None)
    table_id = next((part for part in parts if _BITABLE_TABLE_RE.fullmatch(part)), None)
    return app_token, table_id


def _document_type_for_token(token: str) -> str:
    lowered = token.lower()
    if lowered.startswith("sht"):
        return "sheet"
    if lowered.startswith("doc"):
        return "docx"
    return "file"


def _event_search_text(event: WorkEvent) -> str:
    pieces = [
        event.title or "",
        event.content_text or "",
        _json_text(event.payload or {}),
        _json_text(event.raw_json or {}),
    ]
    return "\n".join(piece for piece in pieces if piece)


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)


def _unique_pairs(values: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    result: list[tuple[str, str]] = []
    for left, right in values:
        pair = (str(left or "").strip(), str(right or "").strip())
        if not pair[0] or not pair[1] or pair in seen:
            continue
        seen.add(pair)
        result.append(pair)
    return result


def _items(data: dict[str, Any], keys: list[str]) -> list[dict[str, Any]]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _unique_strings(values: Iterable[str | None]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _first_string(item: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        value = item.get(key)
        if value is not None and value != "":
            return str(value)
    return None


def _local_settings(source_work_event_id: str | None) -> dict[str, Any]:
    settings = {"source": "local_work_event_payload"}
    if source_work_event_id:
        settings["work_event_id"] = source_work_event_id
    return settings
