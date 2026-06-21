from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import FeishuAppConfig
from app.services.ai.reports import generate_daily_report
from app.services.feishu import approval_card_entrypoint
from app.services.feishu import bot_runtime
from app.services.feishu import command_dispatcher
from app.services.feishu import command_parser
from app.services.feishu import identity as feishu_identity
from app.services.feishu import sync_commands
from app.services.feishu import work_event_replies
from app.services.feishu.approval_attachments import ApprovalAttachmentReadResult
from app.services.feishu.identity import BotIdentity


HELP_TEXT = """我在。你可以直接像问助理一样问我，不用背指令。

常用说法：
- 今天公司有什么重点？
- 最近邮件里有什么要我处理？
- 最近审批有什么要处理？
- 同步通讯录
- 把组织架构以 XMind 形式输出
- 现在有哪些风险和待办？
- 帮我总结固势今天的情况
- 同步邮箱
- 同步审批
- 今日日报

我的身份是老板的企业数字助理。你是系统所有者时，我可以查全公司数据；其他人找我单聊时，我会先识别身份，再按权限回答。

我会优先查已经同步进来的飞书 OA 信息，也会把后续企业知识库作为第二信息来源。"""


def default_command_dispatch_handlers() -> command_dispatcher.CommandDispatchHandlers:
    return command_dispatcher.CommandDispatchHandlers(
        help_text=HELP_TEXT,
        member_help_text=feishu_identity.member_help_text,
        identity_reply=feishu_identity.identity_reply,
        bot_identity_reply=feishu_identity.bot_identity_reply,
        online_status_reply=feishu_identity.online_status_reply,
        greeting_reply=feishu_identity.greeting_reply,
        permission_denied_reply=feishu_identity.permission_denied_reply,
        mail_capability_reply=feishu_identity.mail_capability_reply,
        build_daily_report_reply=build_daily_report_reply,
        sync_mail_reply=sync_commands.sync_mail_reply,
        quick_sync_for_question=sync_commands.quick_sync_for_question,
        recent_mail_reply=work_event_replies.recent_mail_reply,
        sync_approvals_reply=sync_commands.sync_approvals_reply,
        sync_contacts_reply=sync_commands.sync_contacts_reply,
        prepare_approval_action_reply=prepare_approval_action_reply,
        execute_pending_approval_action_reply=execute_pending_approval_action_reply,
        clear_pending_approval_action=clear_pending_approval_action,
        employee_bot_answer=bot_runtime.employee_bot_answer,
        employee_bot_answer_result=bot_runtime.employee_bot_answer_result,
    )


def record_command_context(
    db: Session,
    app_config: FeishuAppConfig,
    identity: BotIdentity,
    chat_id: str | None,
    question: str,
    normalized: str,
    reply: str,
) -> None:
    bot_runtime.record_command_context(db, app_config, identity, chat_id, question, normalized, reply)


def runtime_v5_answer_result(
    db: Session,
    app_config: FeishuAppConfig,
    question: str,
    normalized: str,
    identity: BotIdentity,
    chat_id: str | None,
):
    return bot_runtime.employee_bot_answer_result(db, app_config, question, normalized, identity, chat_id)


def normalize_command_with_context(
    app_config: FeishuAppConfig,
    identity: BotIdentity,
    chat_id: str | None,
    command: str,
    normalized: str,
) -> str:
    return command_parser.normalize_command_with_context(
        command,
        normalized,
        has_approval_context=bool(load_approval_context(app_config, identity, chat_id)),
    )


def build_daily_report_reply(db: Session, app_config: FeishuAppConfig) -> str:
    report = generate_daily_report(
        db,
        report_date=datetime.now(UTC).date(),
        company_id=app_config.company_id,
    )
    db.commit()
    return report.content_markdown


async def prepare_approval_action_reply(
    db: Session,
    app_config: FeishuAppConfig,
    identity: BotIdentity,
    *,
    chat_id: str | None,
    action: str,
    selector_text: str | None = None,
) -> str:
    return await approval_card_entrypoint.prepare_feishu_approval_action_reply(
        db,
        app_config,
        identity,
        chat_id=chat_id,
        action=action,
        selector_text=selector_text,
    )


async def execute_pending_approval_action_reply(
    db: Session,
    app_config: FeishuAppConfig,
    identity: BotIdentity,
    *,
    chat_id: str | None,
    expected_action: str,
) -> str:
    return await approval_card_entrypoint.execute_feishu_pending_approval_action_reply(
        db,
        app_config,
        identity,
        chat_id=chat_id,
        expected_action=expected_action,
    )


def approval_attachment_results(item: dict[str, Any]) -> list[ApprovalAttachmentReadResult]:
    value = item.get("_attachment_results")
    if isinstance(value, list) and all(isinstance(result, ApprovalAttachmentReadResult) for result in value):
        return value
    return []


def store_approval_context(
    app_config: FeishuAppConfig,
    identity: BotIdentity,
    chat_id: str | None,
    items: list[dict[str, Any]],
) -> None:
    approval_card_entrypoint.store_feishu_approval_context(app_config, identity, chat_id, items)


def load_approval_context(app_config: FeishuAppConfig, identity: BotIdentity, chat_id: str | None) -> list[dict[str, Any]]:
    return approval_card_entrypoint.load_feishu_card_approval_context(app_config, identity, chat_id)


def clear_pending_approval_action(app_config: FeishuAppConfig, identity: BotIdentity, chat_id: str | None) -> None:
    approval_card_entrypoint.clear_feishu_pending_approval_action(app_config, identity, chat_id)
