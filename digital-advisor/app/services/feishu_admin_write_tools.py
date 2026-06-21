from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.entities import FeishuAppConfig
from app.services.agent.policies import BotActor
from app.services.audit import write_audit_log
from app.services.feishu import FeishuClient, FeishuImService
from app.services.feishu.cli_profile import feishu_app_cli_profile
from app.services.tools.base import ToolContext, ToolExecutionStatus, ToolRequest
from app.services.tools.router import execute_agent_tool


def admin_write_tool_context(
    db: Session,
    app_config: FeishuAppConfig,
    *,
    domains: tuple[str, ...],
    open_id: str | None,
) -> ToolContext:
    return ToolContext(
        db=db,
        company_id=app_config.company_id,
        actor=BotActor(
            role="admin",
            access_scope="company",
            domains=domains,
            open_id=open_id,
            display_name="feishu_admin_api",
        ),
        cli_profile=feishu_app_cli_profile(app_config),
    )


def tool_result_payload(result: Any) -> dict[str, Any]:
    return {
        "ok": result.status == ToolExecutionStatus.SUCCESS,
        "status": result.status.value,
        "answer": result.answer,
        "error": result.error,
        "metadata": result.metadata,
    }


def send_message_text_from_request(data: Any) -> str:
    if data.msg_type != "text":
        raise HTTPException(status_code=400, detail="Only text messages are supported by the Tool Router send endpoint.")
    text = str(data.content.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="content.text is required.")
    return text


def send_message_tool_request(
    app_config: FeishuAppConfig,
    data: Any,
    *,
    text: str,
) -> ToolRequest:
    return ToolRequest(
        tool_name="feishu_im_send_message",
        question="管理后台发送飞书消息",
        normalized_command="管理后台发送飞书消息",
        params={
            "app_config": app_config,
            "receive_id_type": data.receive_id_type,
            "receive_id": data.receive_id,
            "text": text,
            "idempotency_key": data.idempotency_key,
            "dry_run": data.dry_run,
            "confirmed": data.confirmed,
            "confirmation_token": data.confirmation_token,
        },
    )


def send_message_payload(
    db: Session,
    app_config: FeishuAppConfig,
    data: Any,
) -> dict[str, Any]:
    text = send_message_text_from_request(data)
    result = execute_agent_tool(
        admin_write_tool_context(db, app_config, domains=("im",), open_id=data.actor_open_id),
        send_message_tool_request(app_config, data, text=text),
    )
    write_audit_log(
        db,
        action="feishu.bot.send",
        company_id=app_config.company_id,
        actor=data.actor_open_id,
        target_type="feishu_app",
        target_id=str(app_config.id),
        payload={
            "tool_name": "feishu_im_send_message",
            "status": result.status.value,
            "ok": result.status == ToolExecutionStatus.SUCCESS,
            "receive_id_type": data.receive_id_type,
            "receive_id": data.receive_id,
            "dry_run": result.metadata.get("dry_run"),
            "confirmed": result.metadata.get("confirmed"),
            "has_confirmation_token": result.metadata.get("has_confirmation_token"),
            "write_target": result.metadata.get("write_target"),
        },
    )
    db.commit()
    return tool_result_payload(result)


def submit_approval_action_payload(
    db: Session,
    app_config: FeishuAppConfig,
    data: Any,
) -> dict[str, Any]:
    tool_name = "feishu_approval_task_approve" if data.action == "approve" else "feishu_approval_task_reject"
    result = execute_agent_tool(
        admin_write_tool_context(db, app_config, domains=("approval",), open_id=data.open_id),
        ToolRequest(
            tool_name=tool_name,
            question=f"管理后台审批{data.action}",
            normalized_command=f"管理后台审批{data.action}",
            params={
                "app_config": app_config,
                "open_id": data.open_id,
                "approval_code": data.approval_code,
                "instance_code": data.instance_code,
                "task_id": data.task_id,
                "comment": data.comment,
                "dry_run": data.dry_run,
                "confirmed": data.confirmed,
                "confirmation_token": data.confirmation_token,
            },
        ),
    )
    write_audit_log(
        db,
        action=f"feishu.approvals.{data.action}",
        company_id=app_config.company_id,
        actor=data.open_id,
        target_type="approval_task",
        target_id=data.task_id,
        payload={
            "approval_code": data.approval_code,
            "instance_code": data.instance_code,
            "open_id": data.open_id,
            "tool_name": tool_name,
            "status": result.status.value,
            "ok": result.status == ToolExecutionStatus.SUCCESS,
            "dry_run": result.metadata.get("dry_run"),
            "confirmed": result.metadata.get("confirmed"),
            "has_confirmation_token": result.metadata.get("has_confirmation_token"),
            "write_target": result.metadata.get("write_target"),
        },
    )
    db.commit()
    return tool_result_payload(result)


def create_chat_tool_request(
    app_config: FeishuAppConfig,
    data: Any,
    bot_ids: list[str],
) -> ToolRequest:
    return ToolRequest(
        tool_name="feishu_im_create_chat",
        question="管理后台创建飞书群",
        normalized_command="管理后台创建飞书群",
        params={
            "app_config": app_config,
            "name": data.name,
            "description": data.description,
            "user_id_list": unique_strings(data.user_id_list),
            "bot_id_list": bot_ids,
            "user_id_type": data.user_id_type,
            "chat_mode": data.chat_mode,
            "chat_type": data.chat_type,
            "dry_run": data.dry_run,
            "confirmed": data.confirmed,
            "confirmation_token": data.confirmation_token,
        },
    )


def create_chat_with_bot_payload(
    db: Session,
    app_config: FeishuAppConfig,
    data: Any,
) -> dict[str, Any]:
    bot_ids = unique_strings([*data.bot_id_list, *([app_config.app_id] if data.include_current_bot else [])])
    result = execute_agent_tool(
        admin_write_tool_context(db, app_config, domains=("im",), open_id=data.actor_open_id),
        create_chat_tool_request(app_config, data, bot_ids),
    )
    write_audit_log(
        db,
        action="feishu.chat.create_with_bot",
        company_id=app_config.company_id,
        actor=data.actor_open_id,
        target_type="feishu_chat",
        target_id=data.name,
        payload={
            "tool_name": "feishu_im_create_chat",
            "status": result.status.value,
            "ok": result.status == ToolExecutionStatus.SUCCESS,
            "name": data.name,
            "user_count": len(data.user_id_list),
            "bot_count": len(bot_ids),
            "dry_run": result.metadata.get("dry_run"),
            "confirmed": result.metadata.get("confirmed"),
            "has_confirmation_token": result.metadata.get("has_confirmation_token"),
            "write_target": result.metadata.get("write_target"),
        },
    )
    db.commit()
    response = tool_result_payload(result)
    response["bot_id_list"] = bot_ids
    return response


def auto_join_public_chat_tool_request(
    app_config: FeishuAppConfig,
    data: Any,
) -> ToolRequest:
    return ToolRequest(
        tool_name="feishu_im_auto_join_public_chats",
        question="管理后台加入飞书公开群",
        normalized_command="管理后台加入飞书公开群",
        params={
            "app_config": app_config,
            "query": data.query,
            "chat_ids": data.chat_ids,
            "limit": data.limit,
            "max_pages": data.max_pages,
            "dry_run": data.dry_run,
            "confirmed": data.confirmed,
            "confirmation_token": data.confirmation_token,
        },
    )


async def preview_auto_join_public_chats(
    app_config: FeishuAppConfig,
    data: Any,
) -> dict[str, Any]:
    if not data.dry_run:
        raise ValueError("Public chat auto-join helper is dry-run only; real joins must use Tool Router.")
    return await FeishuImService(app_config, client=FeishuClient(app_config)).preview_public_chats(
        query=data.query,
        chat_ids=data.chat_ids,
        limit=data.limit,
        max_pages=data.max_pages,
    )


async def auto_join_public_chats_payload(
    db: Session,
    app_config: FeishuAppConfig,
    data: Any,
) -> dict[str, Any]:
    if not data.chat_ids and not (data.query and data.query.strip()):
        raise HTTPException(status_code=400, detail="Provide chat_ids or query")
    tool_result = execute_agent_tool(
        admin_write_tool_context(db, app_config, domains=("im",), open_id=data.actor_open_id),
        auto_join_public_chat_tool_request(app_config, data),
    )
    if data.dry_run:
        result = await preview_auto_join_public_chats(app_config, data)
        result.update(
            {
                "status": tool_result.status.value,
                "answer": tool_result.answer,
                "error": tool_result.error,
                "metadata": tool_result.metadata,
            }
        )
    else:
        result = tool_result_payload(tool_result)
    write_audit_log(
        db,
        action="feishu.chat.public_auto_join",
        company_id=app_config.company_id,
        actor=data.actor_open_id,
        target_type="feishu_app",
        target_id=str(app_config.id),
        payload={
            "tool_name": "feishu_im_auto_join_public_chats",
            "query": data.query,
            "requested_chat_count": len(data.chat_ids),
            "status": tool_result.status.value,
            "ok": tool_result.status == ToolExecutionStatus.SUCCESS,
            "dry_run": tool_result.metadata.get("dry_run"),
            "confirmed": tool_result.metadata.get("confirmed"),
            "has_confirmation_token": tool_result.metadata.get("has_confirmation_token"),
            "write_target": tool_result.metadata.get("write_target"),
            "joined_count": len(result.get("joined") or []),
            "error_count": len(result.get("errors") or []),
        },
    )
    db.commit()
    return result


def unique_strings(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        cleaned = str(value or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result
