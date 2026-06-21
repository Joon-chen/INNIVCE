from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.serialization import json_safe
from app.models.entities import Account, BotUserAccess, Company, FeishuAppConfig, Resource
from app.schemas.common import AccountCreate, CompanyCreate
from app.services.audit import write_audit_log
from app.services.data.account_types import is_external_mail_provider, is_user_identity_account_type, require_account_type
from app.services.feishu_admin_apps import pending_credential_settings
from app.services.resource_registry import feishu_discovered_resource_spec, upsert_feishu_discovered_resource, upsert_resource
from app.services.resource_sources import upsert_mail_account_resource
from app.services.user_identity_authorizations import default_user_identity_authorizations
from app.services.v5_administration import bootstrap_v5_administration


def create_company_payload(db: Session, data: CompanyCreate) -> Company:
    existing_company_id = db.scalar(select(Company.id).where(Company.code == data.code))
    if existing_company_id is not None:
        raise HTTPException(status_code=400, detail="Duplicate company code")

    company = Company(name=data.name, code=data.code, metadata_json=json_safe(data.metadata_json))
    db.add(company)
    try:
        db.flush()
        write_audit_log(
            db,
            action="company.created",
            company_id=company.id,
            target_type="company",
            target_id=str(company.id),
            payload=json_safe(data.model_dump()),
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Duplicate company code") from exc
    db.refresh(company)
    return company


def list_company_payloads(db: Session) -> list[Company]:
    return list(db.scalars(select(Company).order_by(Company.created_at.desc())).all())


def create_account_payload(db: Session, data: AccountCreate) -> Account:
    if db.get(Company, data.company_id) is None:
        raise HTTPException(status_code=400, detail="Invalid company_id")
    try:
        account_type = require_account_type(data.account_type, provider=data.provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Unsupported account provider or account_type") from exc
    if is_user_identity_account_type(account_type):
        raise HTTPException(
            status_code=400,
            detail=(
                "User Identity resources must be authorized by the resource owner. "
                "The admin console only displays personal authorization status and cannot configure personal credentials."
            ),
        )

    account = Account(
        company_id=data.company_id,
        provider=data.provider,
        account_type=account_type,
        display_name=data.display_name,
        external_account_id=data.external_account_id,
        email_address=data.email_address,
        credentials=json_safe(data.credentials),
        settings=json_safe(data.settings),
    )
    db.add(account)
    try:
        db.flush()
        if is_external_mail_provider(account.provider):
            folder_id = str((account.settings or {}).get("folder_id") or (account.settings or {}).get("folder") or "INBOX")
            upsert_mail_account_resource(db, account, folder_id=folder_id)
        write_audit_log(
            db,
            action="account.created",
            company_id=account.company_id,
            target_type="account",
            target_id=str(account.id),
            payload=json_safe(data.model_dump()),
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Invalid or duplicate account") from exc
    db.refresh(account)
    return account


def list_company_account_payloads(db: Session, company_id: UUID) -> list[Account]:
    return list(db.scalars(select(Account).where(Account.company_id == company_id)).all())


def quick_company_setup_payload(db: Session, data: Any) -> dict[str, Any]:
    company = db.scalar(select(Company).where(Company.code == data.company_code))
    company_created = False
    if not company:
        company = Company(
            name=data.company_name,
            code=data.company_code,
            metadata_json=json_safe(data.metadata_json),
        )
        db.add(company)
        db.flush()
        company_created = True
        write_audit_log(
            db,
            action="company.created.quick_setup",
            company_id=company.id,
            target_type="company",
            target_id=str(company.id),
            payload={"company_name": data.company_name, "company_code": data.company_code},
        )

    app_config = None
    app_created = False
    if data.feishu_app:
        existing_app = db.scalar(select(FeishuAppConfig).where(FeishuAppConfig.app_id == data.feishu_app.app_id))
        if existing_app and existing_app.company_id != company.id:
            raise HTTPException(status_code=400, detail="Feishu app_id already belongs to another company")
        if existing_app:
            app_config = existing_app
            app_config.name = data.feishu_app.name or app_config.name
            app_config.app_secret = data.feishu_app.app_secret
            app_config.verification_token = data.feishu_app.verification_token
            app_config.encrypt_key = data.feishu_app.encrypt_key
            app_config.settings = json_safe(
                pending_credential_settings({**(app_config.settings or {}), **(data.feishu_app.settings or {})})
            )
            app_config.is_active = True
        else:
            app_config = FeishuAppConfig(
                company_id=company.id,
                name=data.feishu_app.name,
                app_id=data.feishu_app.app_id,
                app_secret=data.feishu_app.app_secret,
                verification_token=data.feishu_app.verification_token,
                encrypt_key=data.feishu_app.encrypt_key,
                settings=json_safe(pending_credential_settings(data.feishu_app.settings)),
            )
            db.add(app_config)
            db.flush()
            app_created = True
            write_audit_log(
                db,
                action="feishu_app.created.quick_setup",
                company_id=company.id,
                target_type="feishu_app",
                target_id=str(app_config.id),
                payload={"name": app_config.name, "app_id": app_config.app_id},
            )

    bot_user = None
    if data.bot_admin_open_id:
        bot_user = db.scalar(
            select(BotUserAccess)
            .where(BotUserAccess.company_id == company.id)
            .where(BotUserAccess.open_id == data.bot_admin_open_id)
        )
        if not bot_user:
            bot_user = BotUserAccess(company_id=company.id, open_id=data.bot_admin_open_id)
            db.add(bot_user)
        if data.bot_admin_name:
            bot_user.display_name = data.bot_admin_name
        elif not bot_user.display_name:
            bot_user.display_name = data.bot_admin_open_id
        bot_user.role = "owner"
        bot_user.access_scope = "company"
        bot_user.is_active = True
        bot_user.settings = json_safe(
            {
                **(bot_user.settings or {}),
                "source": "quick_company_setup_owner_agent" if app_config else "quick_company_setup_pending_app_config",
                "permission_domains": ["all"],
                "user_identity_authorizations": (bot_user.settings or {}).get("user_identity_authorizations")
                or default_user_identity_authorizations(open_id=data.bot_admin_open_id),
                "agent_profile": {
                    **((bot_user.settings or {}).get("agent_profile") or {}),
                    "status": "active",
                    "scope": "company",
                    "entrypoint": "feishu_bot",
                    "activation_source": "quick_setup" if app_config else "quick_setup_pending_app_config",
                    "requires_feishu_app_config": app_config is None,
                },
            }
        )

    mail_resource = None
    if data.feishu_mailbox_id:
        mail_resource = upsert_quick_setup_mail_resource(
            db,
            company=company,
            app_config=app_config,
            mailbox_id=data.feishu_mailbox_id,
            folder_id=data.feishu_mail_folder_id,
        )

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Invalid or duplicate quick setup data") from exc
    bootstrap_result = bootstrap_v5_administration(db, company_id=company.id)

    return {
        "company": {"id": str(company.id), "name": company.name, "code": company.code, "created": company_created},
        "feishu_app": (
            {"id": str(app_config.id), "name": app_config.name, "app_id": app_config.app_id, "created": app_created}
            if app_config
            else None
        ),
        "bot_admin": (
            {"id": str(bot_user.id), "open_id": bot_user.open_id, "role": bot_user.role}
            if bot_user
            else None
        ),
        "mail_resource": quick_setup_mail_resource_payload(mail_resource)
        if mail_resource
        else None,
        "v5_bootstrap": bootstrap_result.as_dict(),
        "next_steps": [
            "调用飞书 tenant-access-token 检查 App 凭证。",
            "调用 resources/discover 自动发现群、邮箱文件夹、云文档和多维表格。",
            "调用 information/sync 将已发现资源同步到 work_events。",
        ],
    }


def upsert_quick_setup_mail_resource(
    db: Session,
    *,
    company: Company,
    app_config: FeishuAppConfig | None,
    mailbox_id: str,
    folder_id: str,
) -> Resource:
    external_id = f"{mailbox_id}:{folder_id}"
    item = {
        "resource_type": "mail_folder",
        "external_id": external_id,
        "name": folder_id,
        "sync_enabled": True,
        "settings": {"user_mailbox_id": mailbox_id, "folder_id": folder_id, "source": "quick_setup"},
    }
    return upsert_v5_mail_resource(db, company=company, app_config=app_config, item=item)


def quick_setup_mail_resource_payload(resource: Resource) -> dict[str, Any]:
    config = resource.config_json if isinstance(resource.config_json, dict) else {}
    settings = config.get("settings") if isinstance(config.get("settings"), dict) else {}
    external_id = config.get("external_id")
    if not external_id:
        external_id = f"{resource.resource_id}:{resource.resource_sub_id}" if resource.resource_sub_id else resource.resource_id
    return {
        "id": str(resource.id),
        "v5_resource_id": str(resource.id),
        "resource_type": resource.resource_type,
        "resource_id": resource.resource_id,
        "resource_sub_id": resource.resource_sub_id,
        "resource_name": resource.resource_name,
        "external_id": external_id,
        "settings": settings,
    }


def upsert_v5_mail_resource(
    db: Session,
    *,
    company: Company,
    app_config: FeishuAppConfig | None,
    item: dict[str, Any],
) -> Resource:
    if app_config:
        return upsert_feishu_discovered_resource(
            db,
            app_config=app_config,
            item=item,
            source_label="quick_company_setup",
        )
    spec = feishu_discovered_resource_spec(item)
    return upsert_resource(
        db,
        company_id=company.id,
        platform="feishu",
        resource_type=spec["resource_type"],
        resource_id=spec["resource_id"],
        resource_sub_id=spec.get("resource_sub_id"),
        resource_name=spec["resource_name"],
        sync_mode=spec["sync_mode"],
        permission_level=spec["permission_level"],
        data_classification=spec["data_classification"],
        business_domain=spec["business_domain"],
        enabled=bool(item.get("sync_enabled", True)),
        config_json={
            "source": "quick_company_setup",
            "external_id": item.get("external_id"),
            "settings": json_safe(item.get("settings") or {}),
        },
        app_config_id=app_config.id if app_config else None,
    )
