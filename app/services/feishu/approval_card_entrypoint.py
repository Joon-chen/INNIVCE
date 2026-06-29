import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import FeishuAppConfig
from app.services.agent.policies import BotActor
from app.services.audit import write_audit_log
from app.services.feishu import approval_actions
from app.services.feishu import approval_card_responder
from app.services.feishu import confirmation_card_entrypoint
from app.services.feishu import approval_enrichment
from app.services.feishu import approval_formatters
from app.services.feishu import approval_resources
from app.services.feishu.cli_profile import feishu_app_cli_profile
from app.services.feishu import approval_runtime
from app.services.feishu import command_parser
from app.services.feishu import identity as feishu_identity
from app.services.feishu import work_event_replies
from app.services.feishu.approval import (
    approval_amount,
    approval_attachment_refs,
    approval_form_fields,
    extract_approval_task_items,
    first_matching_field,
)
from app.services.feishu.approval_advice import (
    approval_attachment_basis,
    approval_decision_for_item,
    rule_approval_decision_recommendation,
)
from app.services.feishu.approval_cards import approval_action_card_renderers, build_approval_action_card
from app.services.feishu.approval_context import (
    clear_approval_context,
    clear_pending_approval_action,
    load_approval_context,
    load_pending_approval_action,
    store_approval_context,
    store_pending_approval_action,
)
from app.services.feishu.approval_attachments import ApprovalAttachmentReadResult, extract_attachment_text
from app.services.feishu.client import FeishuClient
from app.services.gateway.responder import (
    send_feishu_interactive_reply,
    send_feishu_text_reply,
    update_feishu_message_content,
)
from app.services.gateway.card_responder import (
    GatewayCardResponder,
    dispatch_gateway_card_action_message,
    dispatch_gateway_card_action_response,
)
from app.services.llm.approval_advisor import generate_approval_llm_advice
from app.services.tools.base import ToolContext, ToolExecutionStatus, ToolRequest
from app.services.tools.providers.feishu_api import feishu_write_confirmation_token
from app.services.tools.router import execute_agent_tool


def feishu_approval_card_responder() -> GatewayCardResponder:
    return GatewayCardResponder(
        kind="approval_action",
        handle_message=handle_feishu_card_action_message,
        handle_response=handle_feishu_card_action_response,
    )


def feishu_card_responders() -> tuple[GatewayCardResponder, ...]:
    runtime_responders = (
        confirmation_card_entrypoint.feishu_runtime_confirmation_responder(),
        confirmation_card_entrypoint.feishu_runtime_approval_workbench_responder(),
        confirmation_card_entrypoint.feishu_runtime_approval_batch_confirm_responder(),
        confirmation_card_entrypoint.feishu_runtime_approval_single_confirm_responder(),
        confirmation_card_entrypoint.feishu_runtime_approval_detail_action_responder(),
        confirmation_card_entrypoint.feishu_runtime_action_input_responder(),
    )
    if settings.feishu_bot_runtime_v5_enabled:
        return runtime_responders
    return (feishu_approval_card_responder(), *runtime_responders)


def is_feishu_approval_card_action(payload: dict[str, Any]) -> bool:
    return approval_card_responder.is_approval_card_action(payload)


async def handle_feishu_gateway_card_action_response(
    db: Session,
    app_config: FeishuAppConfig,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    return await dispatch_gateway_card_action_response(
        db,
        app_config,
        payload,
        responders=feishu_card_responders(),
    )


async def handle_feishu_gateway_card_action_message(
    db: Session,
    app_config: FeishuAppConfig,
    payload: dict[str, Any],
) -> bool:
    return await dispatch_gateway_card_action_message(
        db,
        app_config,
        payload,
        responders=feishu_card_responders(),
    )


async def handle_feishu_card_action_response(
    db: Session,
    app_config: FeishuAppConfig,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    return await approval_card_responder.handle_card_action_response(
        db,
        app_config,
        payload,
        get_sender_identity=feishu_identity.get_sender_identity,
        permission_denied_reply=feishu_identity.permission_denied_reply,
        get_chat_id=command_parser.get_chat_id,
        short_text=approval_formatters.short_approval_text,
        load_approval_context=load_feishu_card_approval_context,
        build_approval_action_card=build_feishu_approval_action_card,
        approval_detail_reply=feishu_card_approval_detail_reply,
        prepare_approval_action_reply=prepare_feishu_card_approval_action_reply,
        send_text_reply=send_feishu_text_reply,
        update_message_content=update_feishu_message_content,
        client_factory=FeishuClient,
    )


async def handle_feishu_card_action_message(
    db: Session,
    app_config: FeishuAppConfig,
    payload: dict[str, Any],
) -> bool:
    return await approval_card_responder.handle_approval_card_action(
        db,
        app_config,
        payload,
        get_sender_identity=feishu_identity.get_sender_identity,
        permission_denied_reply=feishu_identity.permission_denied_reply,
        get_chat_id=command_parser.get_chat_id,
        approval_detail_reply=feishu_card_approval_detail_reply,
        prepare_approval_action_reply=prepare_feishu_card_approval_action_reply,
        send_text_reply=send_feishu_text_reply,
        client_factory=FeishuClient,
    )


async def send_feishu_approval_action_card(
    app_config: FeishuAppConfig,
    identity: Any,
    chat_id: str | None,
    reply_target: dict[str, str],
    *,
    items: list[dict[str, Any]] | None = None,
    db: Session | None = None,
) -> bool:
    if not items:
        if db is not None:
            try:
                data = await _fetch_feishu_pending_approval_tasks(db, app_config, identity, limit=20)
                items = data.get("items", [])
            except Exception:
                pass
        if not items:
            return False
    card = build_feishu_approval_action_card(
        items,
        chat_id=chat_id,
        receive_id_type=reply_target["receive_id_type"],
        receive_id=reply_target["receive_id"],
        actor_open_id=getattr(identity, "open_id", None),
        limit=20,
    )
    try:
        await send_feishu_interactive_reply(
            app_config=app_config,
            reply_target=reply_target,
            card=card,
            client_factory=FeishuClient,
        )
    except Exception:
        return False
    return True


async def recent_feishu_approvals_reply(
    db: Session,
    app_config: FeishuAppConfig,
    identity: Any,
    *,
    chat_id: str | None = None,
    limit: int = 8,
) -> str:
    return await approval_runtime.recent_approvals_reply(
        db,
        app_config,
        identity,
        chat_id=chat_id,
        limit=limit,
        fetch_pending_tasks=_fetch_feishu_pending_approval_tasks,
        register_resources=approval_resources.register_pending_approval_resources,
        attach_synced_attachments=approval_resources.attach_synced_approval_attachments,
        ensure_attachment_summaries=lambda app_config, items: _ensure_feishu_pending_approval_attachment_summaries(app_config, items, db=db, open_id=getattr(identity, "open_id", None)),
        attach_history_context=approval_resources.attach_approval_history_context,
        attach_llm_advice=_attach_feishu_approval_llm_advice_async,
        store_context=_store_feishu_approval_context,
        format_pending_tasks=lambda items: approval_formatters.format_pending_approval_tasks(items, limit=limit),
        approval_events_by_status=work_event_replies.approval_events_by_status,
        completed_statuses=work_event_replies.COMPLETED_APPROVAL_STATUSES,
        pending_statuses=work_event_replies.PENDING_APPROVAL_STATUSES,
        format_event_lines=work_event_replies.format_approval_event_lines,
        recent_approval_events=work_event_replies.recent_approval_events,
    )


async def feishu_approval_items_from_context_or_live(
    db: Session,
    app_config: FeishuAppConfig,
    identity: Any,
    *,
    chat_id: str | None,
) -> list[dict[str, Any]]:
    return await approval_runtime.approval_items_from_context_or_live(
        db,
        app_config,
        identity,
        chat_id=chat_id,
        load_context=load_feishu_card_approval_context,
        fetch_pending_tasks=_fetch_feishu_pending_approval_tasks,
        register_resources=approval_resources.register_pending_approval_resources,
        attach_synced_attachments=approval_resources.attach_synced_approval_attachments,
        ensure_attachment_summaries=lambda app_config, items: _ensure_feishu_pending_approval_attachment_summaries(app_config, items, db=db, open_id=getattr(identity, "open_id", None)),
        attach_history_context=approval_resources.attach_approval_history_context,
        attach_llm_advice=_attach_feishu_approval_llm_advice_async,
        store_context=_store_feishu_approval_context,
    )


async def feishu_approval_advice_reply(
    db: Session,
    app_config: FeishuAppConfig,
    identity: Any,
    *,
    chat_id: str | None,
) -> str:
    return await approval_runtime.approval_advice_reply(
        db,
        app_config,
        identity,
        chat_id=chat_id,
        load_items=feishu_approval_items_from_context_or_live,
        form_fields=lambda form: approval_form_fields(form, max_fields=20),
        amount_value=approval_amount,
        first_matching=first_matching_field,
        applicant_name=approval_formatters.approval_applicant_name,
        approval_name=approval_formatters.readable_approval_name,
        attachment_refs=approval_attachment_refs,
        attachment_results=approval_formatters.approval_attachment_results,
        decision_for_item=approval_decision_for_item,
        attachment_basis=approval_attachment_basis,
    )


async def feishu_approval_detail_reply(
    db: Session,
    app_config: FeishuAppConfig,
    identity: Any,
    *,
    chat_id: str | None,
    selector_text: str,
) -> str:
    return await approval_runtime.approval_detail_reply(
        db,
        app_config,
        identity,
        chat_id=chat_id,
        selector_text=selector_text,
        load_items=feishu_approval_items_from_context_or_live,
        select_item=approval_formatters.select_approval_item,
        attachment_refs=approval_attachment_refs,
        attachment_results=approval_formatters.approval_attachment_results,
        format_detail_lines=approval_formatters.format_approval_detail_lines,
    )


async def prepare_feishu_approval_action_reply(
    db: Session,
    app_config: FeishuAppConfig,
    identity: Any,
    *,
    chat_id: str | None,
    action: str,
    selector_text: str | None = None,
) -> str:
    return await approval_actions.prepare_approval_action_reply(
        db,
        app_config,
        identity,
        chat_id=chat_id,
        action=action,
        selector_text=selector_text,
        load_items=feishu_approval_items_from_context_or_live,
        select_item=approval_formatters.select_approval_item,
        store_pending_action=_store_feishu_card_pending_approval_action,
        format_pending_tasks=lambda items: approval_formatters.format_pending_approval_tasks(items, limit=1),
    )


async def execute_feishu_pending_approval_action_reply(
    db: Session,
    app_config: FeishuAppConfig,
    identity: Any,
    *,
    chat_id: str | None,
    expected_action: str,
    audit_action: Any | None = None,
) -> str:
    return await approval_actions.execute_pending_approval_action_reply(
        app_config,
        identity,
        chat_id=chat_id,
        expected_action=expected_action,
        load_pending_action=_load_feishu_pending_approval_action,
        execute_action=lambda app_config, identity, item, action: _execute_feishu_approval_action(
            app_config,
            identity,
            item,
            action=action,
            db=db,
        ),
        clear_pending_action=_clear_feishu_pending_approval_action,
        clear_approval_context=_clear_feishu_approval_context,
        audit_action=audit_action
        or (
            lambda app_config, identity, chat_id, item, action, result: _record_feishu_approval_action_audit(
                db,
                app_config,
                identity,
                chat_id,
                item,
                action,
                result,
            )
        ),
    )


def clear_feishu_pending_approval_action(
    app_config: FeishuAppConfig,
    identity: Any,
    chat_id: str | None,
) -> None:
    _clear_feishu_pending_approval_action(app_config, identity, chat_id)


async def _execute_feishu_approval_action(
    app_config: FeishuAppConfig,
    identity: Any,
    item: dict[str, Any],
    *,
    action: str,
    db: Session | None = None,
) -> dict[str, Any]:
    if action not in {"approve", "reject"}:
        return {"ok": False, "status": "denied", "error": "unsupported_approval_action"}
    tool_name = "feishu_approval_task_approve" if action == "approve" else "feishu_approval_task_reject"
    context = ToolContext(
        db=db,
        company_id=app_config.company_id,
        actor=_feishu_approval_tool_actor(identity),
        cli_profile=feishu_app_cli_profile(app_config),
    )
    request = ToolRequest(
        tool_name=tool_name,
        question=f"飞书机器人确认审批{action}",
        normalized_command=f"飞书机器人确认审批{action}",
        params={
            "app_config": app_config,
            "open_id": getattr(identity, "open_id", None),
            "approval_code": item.get("approval_code") or item.get("definition_code"),
            "instance_code": item.get("instance_code") or item.get("process_code"),
            "task_id": item.get("task_id"),
            "comment": "由数字参谋根据老板在飞书中的二次确认提交。",
            "confirmed": True,
        },
    )
    request.params["confirmation_token"] = feishu_write_confirmation_token(context, request)
    result = execute_agent_tool(context, request)
    return {
        "ok": result.status.value == "success",
        "status": result.status.value,
        "error": result.error,
        "answer": result.answer,
        "metadata": result.metadata,
    }


def _feishu_approval_tool_actor(identity: Any) -> BotActor:
    return BotActor(
        role=getattr(identity, "role", None) or "admin",
        access_scope=getattr(identity, "access_scope", None) or "company",
        domains=tuple(getattr(identity, "domains", None) or ("approval",)),
        display_name=getattr(identity, "display_name", None),
        open_id=getattr(identity, "open_id", None),
        email=getattr(identity, "email", None),
    )


def _record_feishu_approval_action_audit(
    db: Session,
    app_config: FeishuAppConfig,
    identity: Any,
    chat_id: str | None,
    item: dict[str, Any],
    action: str,
    result: dict[str, Any],
) -> None:
    approval_code = str(item.get("approval_code") or item.get("definition_code") or "").strip() or None
    instance_code = str(item.get("instance_code") or item.get("process_code") or "").strip() or None
    task_id = str(item.get("task_id") or "").strip() or None
    status = "success" if result.get("ok") else str(result.get("status") or "failed")
    write_audit_log(
        db,
        action=f"approval.task.{action}",
        company_id=app_config.company_id,
        actor=getattr(identity, "open_id", None),
        target_type="approval_task",
        target_id=task_id or instance_code or approval_code,
        payload={
            "status": status,
            "approval_code": approval_code,
            "instance_code": instance_code,
            "task_id": task_id,
            "chat_id": chat_id,
            "confirmed": True,
            "confirmation_token_checked": True,
            "error": result.get("error"),
            "expected_action": result.get("expected_action"),
        },
    )


def build_feishu_approval_action_card(
    items: list[dict[str, Any]],
    *,
    chat_id: str | None,
    receive_id_type: str,
    receive_id: str,
    actor_open_id: str | None = None,
    limit: int = 6,
    expanded_index: int | None = None,
) -> dict[str, Any]:
    return build_approval_action_card(
        items,
        chat_id=chat_id,
        receive_id_type=receive_id_type,
        receive_id=receive_id,
        actor_open_id=actor_open_id,
        limit=limit,
        expanded_index=expanded_index,
        renderers=approval_action_card_renderers(),
    )


def load_feishu_card_approval_context(
    app_config: FeishuAppConfig,
    identity: Any,
    chat_id: str | None,
) -> list[dict[str, Any]]:
    def load_item(item: dict[str, Any]) -> dict[str, Any]:
        item["_attachment_results"] = approval_resources.approval_attachment_results_from_payload(
            item.get("_attachment_results")
        )
        return item

    return load_approval_context(app_config, identity, chat_id, load_item=load_item, client_factory=FeishuClient)


def store_feishu_approval_context(
    app_config: FeishuAppConfig,
    identity: Any,
    chat_id: str | None,
    items: list[dict[str, Any]],
) -> None:
    _store_feishu_approval_context(app_config, identity, chat_id, items)


async def _fetch_feishu_pending_approval_tasks(
    db: Session,
    app_config: FeishuAppConfig,
    identity: Any,
    *,
    limit: int,
) -> dict[str, Any]:
    open_id = getattr(identity, "open_id", None)
    if not open_id:
        return {"available": False, "items": [], "error": "缺少你的 open_id，无法精确查询待审批任务"}

    try:
        context = ToolContext(
            db=db,
            company_id=app_config.company_id,
            actor=_feishu_approval_tool_actor(identity),
            cli_profile=feishu_app_cli_profile(app_config),
        )
        result = execute_agent_tool(
            context,
            ToolRequest(
                tool_name="feishu_approval_task_query",
                question="飞书机器人查询待审批任务",
                normalized_command="飞书机器人查询待审批任务",
                params={
                    "open_id": open_id,
                    "topic": "1",
                    "user_id_type": "open_id",
                    "page_size": limit,
                    "response_format": "raw_json",
                },
            ),
        )
        if result.status != ToolExecutionStatus.SUCCESS:
            return {"available": False, "items": [], "error": result.error or result.answer}
        payload = json.loads(result.answer)
        data = payload.get("data") if isinstance(payload, dict) else {}
        items = extract_approval_task_items(data if isinstance(data, dict) else {})[:limit]
        _normalize_cli_approval_task_items(items)
        _attach_feishu_cli_approval_instance_details(context, items)
        return {"available": True, "items": items}
    except Exception as exc:
        return {"available": False, "items": [], "error": f"飞书审批任务 CLI 查询暂时不可用：{str(exc)[:240]}"}


def _normalize_cli_approval_task_items(items: list[dict[str, Any]]) -> None:
    for item in items:
        code = str(item.get("definition_code") or item.get("approval_code") or "").strip()
        if code:
            item.setdefault("approval_code", code)
            item.setdefault("approval_name", item.get("definition_name") or code)
        else:
            item.setdefault("approval_name", item.get("definition_name") or item.get("title") or "审批")
        instance_code = str(item.get("instance_code") or item.get("process_code") or "").strip()
        if instance_code:
            item.setdefault("process_code", instance_code)
        if not item.get("initiator_names"):
            applicant = item.get("applicant") or {}
            if isinstance(applicant, dict) and applicant.get("name"):
                item["initiator_names"] = [str(applicant["name"])]
            elif isinstance(applicant, str) and not applicant.startswith("ou_"):
                item["initiator_names"] = [applicant]


def _attach_feishu_cli_approval_instance_details(context: ToolContext, items: list[dict[str, Any]]) -> None:
    for item in items[:8]:
        instance_code = str(item.get("instance_code") or item.get("process_code") or "").strip()
        if not instance_code:
            continue
        result = execute_agent_tool(
            context,
            ToolRequest(
                tool_name="feishu_approval_instance_get",
                question="飞书机器人读取审批实例详情",
                normalized_command="飞书机器人读取审批实例详情",
                params={
                    "instance_code": instance_code,
                    "user_id_type": "open_id",
                    "response_format": "raw_json",
                },
            ),
        )
        if result.status != ToolExecutionStatus.SUCCESS:
            item["detail_error"] = result.error or result.answer
            continue
        try:
            payload = json.loads(result.answer)
        except json.JSONDecodeError as exc:
            item["detail_error"] = f"审批实例详情 CLI 返回不可解析：{str(exc)[:120]}"
            continue
        data = payload.get("data") if isinstance(payload, dict) else {}
        if not isinstance(data, dict):
            item["detail_error"] = "审批实例详情 CLI 未返回结构化 data"
            continue
        item["instance_detail"] = data
        item.setdefault("instance_code", data.get("instance_code") or instance_code)
        item.setdefault("serial_number", data.get("serial_number"))
        detail_name = str(data.get("approval_name") or "").strip()
        current_name = str(item.get("approval_name") or "").strip()
        current_code = str(item.get("approval_code") or item.get("definition_code") or "").strip()
        if detail_name and (not current_name or current_name == current_code):
            item["approval_name"] = detail_name
        else:
            item.setdefault("approval_name", detail_name or item.get("approval_name"))
        item.setdefault("start_time", data.get("start_time"))
        # Extract applicant name from instance detail for display
        # Check all possible name fields at all nesting levels
        _name_keys = ["starter_name", "applicant_name", "user_name", "name", "initiator_name"]
        _app_name = ""
        for _key in _name_keys:
            _v = data.get(_key)
            if _v and isinstance(_v, str) and len(_v) < 100:
                _app_name = _v.strip()
                break
        if not _app_name:
            _inner = data.get("applicant") or {}
            if isinstance(_inner, dict):
                _app_name = str(_inner.get("name") or "").strip()
        if not _app_name and isinstance(data.get("data"), dict):
            _d2 = data["data"]
            for _key in _name_keys:
                _v = _d2.get(_key)
                if _v and isinstance(_v, str) and len(_v) < 100:
                    _app_name = _v.strip()
                    break
            if not _app_name and isinstance(_d2.get("instance"), dict):
                _inst = _d2["instance"]
                for _key in _name_keys:
                    _v = _inst.get(_key)
                    if _v and isinstance(_v, str) and len(_v) < 100:
                        _app_name = _v.strip()
                        break
        if _app_name and not item.get("initiator_names"):
            item["initiator_names"] = [_app_name]


async def _ensure_feishu_pending_approval_attachment_summaries(
    app_config: FeishuAppConfig,
    items: list[dict[str, Any]],
    db: Session | None = None,
    open_id: str | None = None,
) -> None:
    context = ToolContext(
        db=db,
        company_id=app_config.company_id,
        actor=BotActor(role="owner", access_scope="company", domains=("approval",), open_id=open_id),
        cli_profile=feishu_app_cli_profile(app_config),
    )
    for item in items:
        if approval_formatters.approval_attachment_results(item):
            continue
        detail = item.get("instance_detail") if isinstance(item.get("instance_detail"), dict) else {}
        refs = approval_attachment_refs(item.get("form") or detail.get("form"))
        if not refs:
            continue
        results = _read_feishu_approval_attachment_refs(context, refs, max_files=min(len(refs), 8))
        if results:
            item["_attachment_results"] = results
            item["_attachment_total"] = len(refs)


def _read_feishu_approval_attachment_refs(
    context: ToolContext,
    refs: list[dict[str, str]],
    *,
    max_files: int,
) -> list[ApprovalAttachmentReadResult]:
    results: list[ApprovalAttachmentReadResult] = []
    for ref in refs[:max_files]:
        results.append(_read_feishu_approval_attachment_ref(context, ref))
    return results


def _read_feishu_approval_attachment_ref(
    context: ToolContext,
    ref: dict[str, str],
) -> ApprovalAttachmentReadResult:
    token = str(ref.get("token") or "").strip()
    name = str(ref.get("name") or ref.get("field_name") or token or "审批附件").strip()
    url = str(ref.get("url") or "").strip()
    if url:
        try:
            import httpx as _hx
            resp = _hx.get(url, timeout=30)
            if resp.status_code == 200:
                text_preview = extract_attachment_text(resp.content, filename=name)
                if text_preview:
                    return ApprovalAttachmentReadResult(name=name, token=token, text_preview=text_preview)
        except Exception:
            pass
    if token:
        result = execute_agent_tool(
            context,
            ToolRequest(
                tool_name="feishu_approval_attachment_download",
                question="飞书机器人读取审批附件",
                normalized_command="飞书机器人读取审批附件",
                params={"file_token": token, "name": name},
            ),
        )
        if result.status == ToolExecutionStatus.SUCCESS:
            try:
                payload = json.loads(result.answer)
                output_path = Path(str(payload.get("output_path") or ""))
                data = output_path.read_bytes()
                text_preview = extract_attachment_text(data, filename=name)
                try:
                    output_path.unlink(missing_ok=True)
                except OSError:
                    pass
                return ApprovalAttachmentReadResult(name=name, token=token, text_preview=text_preview)
            except Exception:
                pass
    # Neither URL nor token available or both failed
    return ApprovalAttachmentReadResult(name=name, token=token or "", error="附件无法读取")
    if result.status != ToolExecutionStatus.SUCCESS:
        return ApprovalAttachmentReadResult(name=name, token=token, error=result.error or result.answer)
    try:
        payload = json.loads(result.answer)
    except json.JSONDecodeError as exc:
        return ApprovalAttachmentReadResult(name=name, token=token, error=f"附件 CLI 返回不可解析：{str(exc)[:120]}")
    output_path = Path(str(payload.get("output_path") or ""))
    try:
        data = output_path.read_bytes()
        text_preview = extract_attachment_text(data, filename=name)
        return ApprovalAttachmentReadResult(name=name, token=token, text_preview=text_preview)
    except Exception as exc:
        return ApprovalAttachmentReadResult(name=name, token=token, error=f"附件本地读取失败：{str(exc)[:180]}")
    finally:
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass


async def _attach_feishu_approval_llm_advice_async(items: list[dict[str, Any]]) -> None:
    await approval_enrichment.attach_approval_llm_advice_async(items, advice_for_item=_feishu_approval_llm_advice_for_item)


def _feishu_approval_llm_advice_for_item(item: dict[str, Any]) -> dict[str, str] | None:
    return approval_enrichment.approval_llm_advice_for_item(
        item,
        form_fields=lambda form: approval_form_fields(form, max_fields=30),
        attachment_refs=approval_attachment_refs,
        attachment_results=approval_formatters.approval_attachment_results,
        rule_recommendation=rule_approval_decision_recommendation,
        readable_name=approval_formatters.readable_approval_name,
        approval_amount=approval_amount,
        attachment_basis=approval_attachment_basis,
        generate_advice=generate_approval_llm_advice,
    )


def _store_feishu_approval_context(
    app_config: FeishuAppConfig,
    identity: Any,
    chat_id: str | None,
    items: list[dict[str, Any]],
) -> None:
    store_approval_context(
        app_config,
        identity,
        chat_id,
        items,
        serialize_item=_feishu_approval_context_item_payload,
        client_factory=FeishuClient,
    )


def _feishu_approval_context_item_payload(item: dict[str, Any]) -> dict[str, Any]:
    payload = dict(item)
    results = approval_formatters.approval_attachment_results(item)
    if results:
        payload["_attachment_results"] = [
            approval_resources.approval_attachment_result_payload(result) for result in results
        ]
    return payload


async def feishu_card_approval_detail_reply(
    db: Session,
    app_config: FeishuAppConfig,
    identity: Any,
    *,
    chat_id: str | None,
    selector_text: str,
) -> str:
    return await approval_runtime.approval_detail_reply(
        db,
        app_config,
        identity,
        chat_id=chat_id,
        selector_text=selector_text,
        load_items=_approval_items_from_card_context,
        select_item=approval_formatters.select_approval_item,
        attachment_refs=approval_attachment_refs,
        attachment_results=approval_formatters.approval_attachment_results,
        format_detail_lines=approval_formatters.format_approval_detail_lines,
    )


async def prepare_feishu_card_approval_action_reply(
    db: Session,
    app_config: FeishuAppConfig,
    identity: Any,
    *,
    chat_id: str | None,
    action: str,
    selector_text: str | None = None,
) -> str:
    return await approval_actions.prepare_approval_action_reply(
        db,
        app_config,
        identity,
        chat_id=chat_id,
        action=action,
        selector_text=selector_text,
        load_items=_approval_items_from_card_context,
        select_item=approval_formatters.select_approval_item,
        store_pending_action=_store_feishu_card_pending_approval_action,
        format_pending_tasks=lambda items: approval_formatters.format_pending_approval_tasks(items, limit=1),
    )


async def _approval_items_from_card_context(
    db: Session,
    app_config: FeishuAppConfig,
    identity: Any,
    *,
    chat_id: str | None,
) -> list[dict[str, Any]]:
    return load_feishu_card_approval_context(app_config, identity, chat_id)


def _store_feishu_card_pending_approval_action(
    app_config: FeishuAppConfig,
    identity: Any,
    chat_id: str | None,
    item: dict[str, Any],
    action: str,
) -> None:
    store_pending_approval_action(app_config, identity, chat_id, item, action, client_factory=FeishuClient)


def _load_feishu_pending_approval_action(
    app_config: FeishuAppConfig,
    identity: Any,
    chat_id: str | None,
) -> dict[str, Any] | None:
    return load_pending_approval_action(app_config, identity, chat_id, client_factory=FeishuClient)


def _clear_feishu_pending_approval_action(app_config: FeishuAppConfig, identity: Any, chat_id: str | None) -> None:
    clear_pending_approval_action(app_config, identity, chat_id, client_factory=FeishuClient)


def _clear_feishu_approval_context(app_config: FeishuAppConfig, identity: Any, chat_id: str | None) -> None:
    clear_approval_context(app_config, identity, chat_id, client_factory=FeishuClient)
