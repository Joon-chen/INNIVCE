from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import FeishuAppConfig, Resource
from app.services.feishu.sync import sync_feishu_information


async def sync_mail_reply(db: Session, app_config: FeishuAppConfig) -> str:
    mail_args = mail_sync_args_from_resources(db, app_config) or mail_sync_args_from_settings()
    if mail_args:
        result = await sync_feishu_information(
            db,
            app_config,
            kinds=["mail"],
            limit=min(settings.auto_feishu_sync_limit, 50),
            max_pages=2,
            user_mailbox_id=mail_args["user_mailbox_id"],
            folder_id=mail_args["folder_id"],
            extract_items=True,
        )
        db.commit()
        mail = result.get("mail") or {}
        if mail.get("available") is False:
            return f"我试着同步飞书邮箱，但没有成功：{mail.get('error') or '接口不可用'}"
        return f"飞书邮箱同步好了，写入或更新 {mail.get('saved_count', 0)} 条工作事件。你现在可以问我“最近邮件有什么重点”。"

    return "我还没有自动发现可同步的飞书邮箱资源。请先同步通讯录或执行资源自动发现；系统会从企业邮箱和邮箱文件夹里自动登记。"


async def quick_sync_for_question(db: Session, app_config: FeishuAppConfig, question: str) -> dict[str, Any]:
    kinds = quick_sync_kinds(question)
    if not kinds:
        return {"skipped": True, "reason": "no matching sync intent"}

    kwargs: dict[str, Any] = {
        "kinds": kinds,
        "limit": 20,
        "max_pages": 1,
        "extract_items": True,
    }
    if "mail" in kinds:
        mail_args = mail_sync_args_from_resources(db, app_config) or mail_sync_args_from_settings()
        if not mail_args:
            kinds.remove("mail")
        else:
            kwargs["user_mailbox_id"] = mail_args["user_mailbox_id"]
            kwargs["folder_id"] = mail_args["folder_id"]
    if not kinds:
        return {"skipped": True, "reason": "required params not configured"}

    try:
        result = await sync_feishu_information(db, app_config, **kwargs)
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        return {"skipped": True, "error": str(exc)[:300]}


def mail_sync_args_from_resources(db: Session, app_config: FeishuAppConfig) -> dict[str, str] | None:
    resource = db.scalar(
        select(Resource)
        .where(Resource.company_id == app_config.company_id)
        .where(Resource.platform == "feishu")
        .where(Resource.resource_type.in_(("mailbox", "mail_folder")))
        .where(Resource.enabled.is_(True))
        .order_by(Resource.updated_at.desc())
    )
    if not resource:
        return None
    folder_id = _mail_folder_id_from_resource(resource)
    mailbox_id = str(resource.resource_id or "").strip()
    if not mailbox_id or not folder_id:
        return None
    return {"user_mailbox_id": mailbox_id, "folder_id": folder_id}


def _mail_folder_id_from_resource(resource: Resource) -> str:
    config = resource.config_json if isinstance(resource.config_json, dict) else {}
    settings_payload = config.get("settings") if isinstance(config.get("settings"), dict) else {}
    return str(resource.resource_sub_id or config.get("folder_id") or settings_payload.get("folder_id") or "").strip()


def mail_sync_args_from_settings() -> dict[str, str] | None:
    if not settings.auto_feishu_mail_user_mailbox_id:
        return None
    return {
        "user_mailbox_id": settings.auto_feishu_mail_user_mailbox_id,
        "folder_id": settings.auto_feishu_mail_folder_id,
    }


def quick_sync_kinds(question: str) -> list[str]:
    text = question.lower()
    kinds: list[str] = []
    if any(word in question for word in ["邮件", "邮箱", "最近一封"]):
        kinds.append("mail")
    if any(word in question for word in ["审批", "付款", "报销", "合同", "请假", "采购"]):
        kinds.append("approvals")
    if any(word in question for word in ["待办", "任务"]):
        kinds.append("tasks")
    if any(word in question for word in ["会议", "日程", "日历"]):
        kinds.extend(["calendar", "meetings"])
    has_broad_question = any(word in question for word in ["今天", "今日", "最近", "最新", "重点", "风险", "异常", "阻塞"]) or any(
        word in text for word in ["today", "risk", "todo"]
    )
    if has_broad_question and not kinds:
        kinds.extend(["mail", "approvals", "tasks"])
    return _unique_sync_kinds(kinds)


async def sync_approvals_reply(db: Session, app_config: FeishuAppConfig) -> str:
    result = await sync_feishu_information(
        db,
        app_config,
        kinds=["approvals"],
        limit=50,
        max_pages=2,
        extract_items=True,
    )
    db.commit()
    approvals = result.get("approvals") or {}
    if approvals.get("available") is False:
        return (
            "我现在还读不到审批实例，因为系统还没有自动发现可同步的审批资源。\n"
            "请先执行一次资源自动发现，或直接问我“待我审批”，我会从你的飞书审批任务里自动识别并登记审批资源。"
        )
    return (
        f"审批同步好了，已按 {approvals.get('approval_code_count', 0)} 个审批流读取，"
        f"写入或更新 {approvals.get('saved_count', 0)} 条工作事件。"
    )


async def sync_contacts_reply(db: Session, app_config: FeishuAppConfig) -> str:
    result = await sync_feishu_information(
        db,
        app_config,
        kinds=["contacts"],
        limit=100,
        max_pages=5,
        extract_items=False,
    )
    db.commit()
    contacts = result.get("contacts") or {}
    if contacts.get("available") is False:
        return f"通讯录同步失败：{contacts.get('error') or '接口不可用'}。请检查通讯录权限是否已发布到企业。"
    return (
        "通讯录同步好了："
        f"部门 {contacts.get('department_count', 0)} 个，"
        f"人员 {contacts.get('user_count', 0)} 人，"
        f"写入或更新 {contacts.get('saved_count', 0)} 条工作事件。"
    )


def _unique_sync_kinds(kinds: list[str]) -> list[str]:
    order = ["mail", "approvals", "tasks", "calendar", "meetings"]
    selected = set(kinds)
    return [kind for kind in order if kind in selected]
