from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.routes.portal_request_models import PortalApprovalActionRequest, PortalPendingApprovalsRequest
from app.core.config import settings
from app.services.feishu import get_feishu_app_or_404
from app.services.feishu.cli_profile import feishu_app_cli_profile
from app.services.runtime_v5.context import load_portal_session_context, load_result_context, load_session_context
from app.services.runtime_v5.feishu_resource_providers import build_feishu_provider_registry
from app.services.runtime_v5.interaction_layer import interaction_payload_from_runtime_result, interaction_payload_payload
from app.services.runtime_v5.models import ResultContext, RuntimeContext, RuntimeIdentity, RuntimeProfile, RuntimeScope
from app.services.runtime_v5.runtime import run_runtime_v5
from app.services.runtime_v5.runtime_action_input import build_runtime_action_input_payload
from app.services.runtime_v5.runtime_result import cached_result_context_runtime_result_payload, runtime_result_from_payload


def portal_pending_approvals_payload(
    db: Session,
    data: PortalPendingApprovalsRequest,
    x_admin_token: str | None,
) -> dict[str, Any]:
    _require_portal_session(
        chat_id=data.chat_id,
        app_config_id=data.app_config_id,
        open_id=data.open_id,
        x_admin_token=x_admin_token,
    )
    app_config = get_feishu_app_or_404(db, data.app_config_id)
    envelope = run_runtime_v5(
        context=_portal_runtime_context(
            app_config=app_config,
            open_id=data.open_id,
            chat_id=data.chat_id,
            message=f"Portal 查询待我审批，最多 {data.limit} 条",
        ),
        providers=build_feishu_provider_registry(db=db, cli_profile=feishu_app_cli_profile(app_config)),
    )
    runtime_result = envelope.composed.metadata.get("runtime_result") if isinstance(envelope.composed.metadata, dict) else {}
    interaction_payload = _portal_interaction_payload(runtime_result if isinstance(runtime_result, dict) else {})
    result_context_metadata = interaction_payload.metadata.get("result_context") if isinstance(interaction_payload.metadata.get("result_context"), dict) else {}
    return {
        "available": interaction_payload.status == "success",
        "items": list(interaction_payload.items),
        "answer": interaction_payload.summary or envelope.composed.answer,
        "error": str(result_context_metadata.get("error") or ""),
        "metadata": {
            **result_context_metadata,
            "runtime_version": "v5",
            "runtime_status": interaction_payload.status or "not_executed",
            "interaction_payload": interaction_payload_payload(interaction_payload),
        },
    }


def portal_cached_approvals_payload(
    data: PortalPendingApprovalsRequest,
    x_admin_token: str | None,
) -> dict[str, Any]:
    _require_portal_session(
        chat_id=data.chat_id,
        app_config_id=data.app_config_id,
        open_id=data.open_id,
        x_admin_token=x_admin_token,
    )
    result_context = load_result_context(data.chat_id)
    if result_context is None or result_context.result_type not in {"approval_list", "approval_query"}:
        return {"available": False, "items": [], "answer": "", "metadata": {}}
    runtime_result = cached_result_context_runtime_result_payload(result_context)
    interaction_payload = _portal_interaction_payload(runtime_result)
    result_context_metadata = interaction_payload.metadata.get("result_context") if isinstance(interaction_payload.metadata.get("result_context"), dict) else {}
    return {
        "available": interaction_payload.status == "success",
        "items": list(interaction_payload.items),
        "answer": interaction_payload.summary or result_context.answer,
        "metadata": {
            **result_context_metadata,
            "runtime_version": "v5",
            "runtime_status": interaction_payload.status or "not_executed",
            "interaction_payload": interaction_payload_payload(interaction_payload),
        },
    }


def portal_bootstrap_payload(chat_id: str | None = None) -> dict[str, Any]:
    portal = load_portal_session_context(chat_id)
    if not portal:
        session_context = load_session_context(chat_id)
        portal = session_context.get("portal") if isinstance(session_context.get("portal"), dict) else {}
    return {
        "available": bool(portal.get("app_config_id") and portal.get("open_id")),
        "app_config_id": portal.get("app_config_id") or "",
        "company_id": portal.get("company_id") or "",
        "open_id": portal.get("open_id") or "",
        "display_name": portal.get("display_name") or "",
        "role": portal.get("role") or "",
        "access_scope": portal.get("access_scope") or "",
    }


def portal_approval_action_payload(
    db: Session,
    data: PortalApprovalActionRequest,
    x_admin_token: str | None,
) -> dict[str, Any]:
    _require_portal_session(
        chat_id=data.chat_id,
        app_config_id=data.app_config_id,
        open_id=data.open_id,
        x_admin_token=x_admin_token,
    )
    app_config = get_feishu_app_or_404(db, data.app_config_id)
    action_message = "同意这个审批" if data.action == "approve" else "拒绝这个审批"
    result_context = _approval_action_result_context(data, company_id=str(app_config.company_id))
    session_context = load_session_context(data.chat_id)
    session_context = {
        **session_context,
        "runtime_v5_action_input": build_runtime_action_input_payload(
            action_id=data.confirmation_token or "",
            action_type=data.action,
            message=action_message,
            intent=f"approval_{data.action}",
            strategy=f"approval_{data.action}",
            target={
                "approval_code": data.approval_code,
                "instance_code": data.instance_code,
                "task_id": data.task_id,
            },
            confirmed=bool(data.confirmed),
            confirmation_token=data.confirmation_token or "",
            company_id=str(app_config.company_id),
            chat_id=data.chat_id or "",
            open_id=data.open_id,
            source_ui="portal",
            sources=["approval"],
        ),
    }
    envelope = run_runtime_v5(
        context=_portal_runtime_context(
            app_config=app_config,
            open_id=data.open_id,
            chat_id=data.chat_id,
            message=action_message,
            session_context=session_context,
            result_context=result_context,
        ),
        providers=build_feishu_provider_registry(db=db, cli_profile=feishu_app_cli_profile(app_config)),
    )
    runtime_result = envelope.composed.metadata.get("runtime_result") if isinstance(envelope.composed.metadata, dict) else {}
    interaction_payload = _portal_interaction_payload(runtime_result if isinstance(runtime_result, dict) else {})
    result_context_metadata = interaction_payload.metadata.get("result_context") if isinstance(interaction_payload.metadata.get("result_context"), dict) else {}
    return {
        "ok": interaction_payload.status == "success",
        "status": interaction_payload.status or "waiting",
        "answer": interaction_payload.summary or envelope.composed.answer,
        "error": str(result_context_metadata.get("error") or ""),
        "metadata": {
            "runtime_version": "v5",
            "requires_confirmation": bool(interaction_payload.metadata.get("requires_confirmation")),
            "interaction_payload": interaction_payload_payload(interaction_payload),
            "runtime_result": runtime_result if isinstance(runtime_result, dict) else {},
            "result_context": result_context_metadata,
        },
    }


def _require_portal_session(
    *,
    chat_id: str | None,
    app_config_id,
    open_id: str,
    x_admin_token: str | None,
) -> None:
    if settings.admin_api_token and x_admin_token == settings.admin_api_token:
        return
    if not settings.admin_api_token and not chat_id:
        return
    portal = load_portal_session_context(chat_id)
    if not portal:
        session_context = load_session_context(chat_id)
        portal = session_context.get("portal") if isinstance(session_context.get("portal"), dict) else {}
    if str(portal.get("app_config_id") or "") != str(app_config_id) or str(portal.get("open_id") or "") != open_id:
        raise HTTPException(status_code=403, detail="Portal 会话身份已失效，请从机器人卡片重新打开。")


def _portal_interaction_payload(runtime_result: dict[str, Any]):
    return interaction_payload_from_runtime_result(runtime_result_from_payload(runtime_result))


def _portal_runtime_context(
    *,
    app_config,
    open_id: str,
    chat_id: str | None,
    message: str,
    session_context: dict[str, Any] | None = None,
    result_context: ResultContext | None = None,
) -> RuntimeContext:
    return RuntimeContext(
        identity=RuntimeIdentity(open_id=open_id, role="employee"),
        runtime_scope=RuntimeScope(
            scope_type="single_company",
            company_ids=(app_config.company_id,),
            active_company_id=app_config.company_id,
        ),
        current_message=message,
        session_context=session_context or {},
        result_context=result_context,
        chat_id=chat_id,
        profile=RuntimeProfile(),
    )


def _approval_action_result_context(data: PortalApprovalActionRequest, *, company_id: str) -> ResultContext:
    item = {
        "title": data.approval_code or data.instance_code or "审批单",
        "approval_code": data.approval_code,
        "process_code": data.approval_code,
        "instance_code": data.instance_code,
        "task_id": data.task_id,
        "raw": {
            "approval_code": data.approval_code,
            "process_code": data.approval_code,
            "instance_code": data.instance_code,
            "task_id": data.task_id,
        },
    }
    return ResultContext(
        result_type="approval_detail",
        query_id=f"portal:approval:{data.instance_code}:{data.task_id}",
        count=1,
        items=(item,),
        metadata={
            "company_id": company_id,
            "source": "portal",
            "context_kind": "query_result",
            "item_count": 1,
        },
        answer="Portal 审批动作上下文",
    )
