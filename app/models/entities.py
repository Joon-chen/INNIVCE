import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Company(Base, TimestampMixin):
    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(40), default="active")
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)

    accounts: Mapped[list["Account"]] = relationship(back_populates="company")
    feishu_apps: Mapped[list["FeishuAppConfig"]] = relationship(back_populates="company")
    sync_runs: Mapped[list["SyncRun"]] = relationship(back_populates="company")


class Account(Base, TimestampMixin):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    account_type: Mapped[str] = mapped_column(String(80), default="external_mail")
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    external_account_id: Mapped[str | None] = mapped_column(String(300))
    email_address: Mapped[str | None] = mapped_column(String(320))
    credentials: Mapped[dict] = mapped_column(JSONB, default=dict)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    company: Mapped["Company"] = relationship(back_populates="accounts")
    work_events: Mapped[list["WorkEvent"]] = relationship(back_populates="account")

    __table_args__ = (
        CheckConstraint(
            "account_type in ("
            "'company_feishu_app', 'personal_feishu_user', 'feishu_mail', "
            "'external_mail', 'personal_dingtalk'"
            ")",
            name="ck_accounts_account_type_v5",
        ),
        UniqueConstraint("company_id", "provider", "external_account_id", name="uq_account_external"),
    )


class FeishuAppConfig(Base, TimestampMixin):
    __tablename__ = "feishu_app_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    app_id: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    app_secret: Mapped[str] = mapped_column(Text, nullable=False)
    verification_token: Mapped[str | None] = mapped_column(String(300))
    encrypt_key: Mapped[str | None] = mapped_column(String(300))
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    company: Mapped["Company"] = relationship(back_populates="feishu_apps")


class CompanySetting(Base, TimestampMixin):
    __tablename__ = "company_settings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(40), default="active")
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)

    company: Mapped["Company"] = relationship()


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_user_id: Mapped[str | None] = mapped_column(String(300))
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    avatar_url: Mapped[str | None] = mapped_column(String(800))
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)

    company_roles: Mapped[list["UserCompanyRole"]] = relationship(back_populates="user")

    __table_args__ = (Index("ix_users_external_user_id", "external_user_id"),)


class Department(Base, TimestampMixin):
    __tablename__ = "departments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    parent_department_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("departments.id"))
    external_id: Mapped[str | None] = mapped_column(String(300))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)

    company: Mapped["Company"] = relationship()
    parent_department: Mapped["Department | None"] = relationship(remote_side=[id])

    __table_args__ = (
        UniqueConstraint("company_id", "external_id", name="uq_department_company_external"),
        Index("ix_departments_company_name", "company_id", "name"),
    )


class Team(Base, TimestampMixin):
    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    department_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("departments.id"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)

    company: Mapped["Company"] = relationship()
    department: Mapped["Department | None"] = relationship()

    __table_args__ = (Index("ix_teams_company_department", "company_id", "department_id"),)


class OrganizationDepartment(Base, TimestampMixin):
    __tablename__ = "organization_departments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    source_system: Mapped[str] = mapped_column(String(80), default="feishu")
    source_department_id: Mapped[str] = mapped_column(String(300), nullable=False)
    open_department_id: Mapped[str | None] = mapped_column(String(300))
    parent_source_department_id: Mapped[str | None] = mapped_column(String(300))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(200), nullable=False)
    unit_type: Mapped[str] = mapped_column(String(80), default="department")
    status: Mapped[str] = mapped_column(String(40), default="active")
    path_names: Mapped[list] = mapped_column(JSONB, default=list)
    path_source_department_ids: Mapped[list] = mapped_column(JSONB, default=list)
    leader_source_user_ids: Mapped[list] = mapped_column(JSONB, default=list)
    source_payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    company: Mapped["Company"] = relationship()

    __table_args__ = (
        UniqueConstraint("company_id", "source_system", "source_department_id", name="uq_org_department_source"),
        Index("ix_org_departments_company_name", "company_id", "normalized_name"),
        Index("ix_org_departments_company_parent", "company_id", "parent_source_department_id"),
    )


class OrganizationUser(Base, TimestampMixin):
    __tablename__ = "organization_users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    source_system: Mapped[str] = mapped_column(String(80), default="feishu")
    source_user_id: Mapped[str | None] = mapped_column(String(300))
    open_id: Mapped[str] = mapped_column(String(300), nullable=False)
    union_id: Mapped[str | None] = mapped_column(String(300))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    mobile: Mapped[str | None] = mapped_column(String(120))
    job_title: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(40), default="active")
    source_payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    company: Mapped["Company"] = relationship()
    memberships: Mapped[list["OrganizationMembership"]] = relationship(back_populates="user")

    __table_args__ = (
        UniqueConstraint("company_id", "source_system", "open_id", name="uq_org_user_source_open_id"),
        Index("ix_org_users_company_name", "company_id", "normalized_name"),
        Index("ix_org_users_company_source_user", "company_id", "source_user_id"),
    )


class OrganizationMembership(Base, TimestampMixin):
    __tablename__ = "organization_memberships"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    organization_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization_users.id"), nullable=False)
    organization_department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization_departments.id"), nullable=False)
    source_system: Mapped[str] = mapped_column(String(80), default="feishu")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    role_in_department: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(40), default="active")
    source_payload: Mapped[dict] = mapped_column(JSONB, default=dict)

    company: Mapped["Company"] = relationship()
    user: Mapped["OrganizationUser"] = relationship(back_populates="memberships")
    department: Mapped["OrganizationDepartment"] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "organization_user_id",
            "organization_department_id",
            name="uq_org_membership_user_department",
        ),
        Index("ix_org_memberships_company_department", "company_id", "organization_department_id"),
        Index("ix_org_memberships_company_user", "company_id", "organization_user_id"),
    )


class OrganizationAlias(Base, TimestampMixin):
    __tablename__ = "organization_aliases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    alias: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(200), nullable=False)
    target_type: Mapped[str] = mapped_column(String(80), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_system: Mapped[str] = mapped_column(String(80), default="manual")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)

    company: Mapped["Company"] = relationship()

    __table_args__ = (
        UniqueConstraint("company_id", "normalized_alias", "target_type", "target_id", name="uq_org_alias_target"),
        Index("ix_org_aliases_company_alias", "company_id", "normalized_alias", "is_active"),
    )


class OrganizationSyncRun(Base, TimestampMixin):
    __tablename__ = "organization_sync_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    source_system: Mapped[str] = mapped_column(String(80), default="feishu")
    sync_type: Mapped[str] = mapped_column(String(80), default="full")
    status: Mapped[str] = mapped_column(String(40), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    department_count: Mapped[int] = mapped_column(default=0)
    user_count: Mapped[int] = mapped_column(default=0)
    membership_count: Mapped[int] = mapped_column(default=0)
    cursor: Mapped[dict] = mapped_column(JSONB, default=dict)
    summary: Mapped[dict] = mapped_column(JSONB, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text)

    company: Mapped["Company"] = relationship()

    __table_args__ = (
        Index("ix_org_sync_runs_company_started", "company_id", "started_at"),
        Index("ix_org_sync_runs_source_status", "source_system", "status"),
    )


class Role(Base, TimestampMixin):
    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    scope_type: Mapped[str] = mapped_column(String(40), default="single_company")
    permissions: Mapped[dict] = mapped_column(JSONB, default=dict)

    company: Mapped["Company"] = relationship()
    user_roles: Mapped[list["UserCompanyRole"]] = relationship(back_populates="role")

    __table_args__ = (UniqueConstraint("company_id", "name", name="uq_role_company_name"),)


class Permission(Base, TimestampMixin):
    __tablename__ = "permissions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    permission_type: Mapped[str] = mapped_column(String(80), default="system")
    config_json: Mapped[dict] = mapped_column(JSONB, default=dict)

    company: Mapped["Company"] = relationship()

    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_permission_company_code"),)


class ToolConfig(Base, TimestampMixin):
    __tablename__ = "tool_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(120), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    required_permissions: Mapped[list] = mapped_column(JSONB, default=list)
    supports_write: Mapped[bool] = mapped_column(Boolean, default=False)
    audit_action: Mapped[str] = mapped_column(String(120), nullable=False)
    config_json: Mapped[dict] = mapped_column(JSONB, default=dict)

    company: Mapped["Company"] = relationship()

    __table_args__ = (
        UniqueConstraint("company_id", "tool_name", name="uq_tool_config_company_tool"),
        Index("ix_tool_configs_company_enabled", "company_id", "enabled"),
    )


class UserCompanyRole(Base, TimestampMixin):
    __tablename__ = "user_company_roles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id"), nullable=False)
    department_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("departments.id"))
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id"))
    scope_type: Mapped[str] = mapped_column(String(40), default="single_company")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)

    user: Mapped["User"] = relationship(back_populates="company_roles")
    company: Mapped["Company"] = relationship()
    role: Mapped["Role"] = relationship(back_populates="user_roles")
    department: Mapped["Department | None"] = relationship()
    team: Mapped["Team | None"] = relationship()

    __table_args__ = (
        UniqueConstraint("user_id", "company_id", "role_id", name="uq_user_company_role"),
        Index("ix_user_company_roles_company", "company_id", "is_active"),
    )


class Resource(Base, TimestampMixin):
    __tablename__ = "resources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_name: Mapped[str | None] = mapped_column(String(500))
    resource_id: Mapped[str] = mapped_column(String(500), nullable=False)
    resource_sub_id: Mapped[str | None] = mapped_column(String(500))
    sync_mode: Mapped[str] = mapped_column(String(80), default="manual")
    permission_level: Mapped[str] = mapped_column(String(80), default="company")
    data_classification: Mapped[str] = mapped_column(String(80), default="company")
    business_domain: Mapped[str] = mapped_column(String(120), default="general")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    config_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    legacy_feishu_resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    app_config_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("feishu_app_configs.id"))

    company: Mapped["Company"] = relationship()
    app_config: Mapped["FeishuAppConfig | None"] = relationship()
    permissions: Mapped[list["ResourcePermission"]] = relationship(back_populates="resource")
    sync_runs: Mapped[list["ResourceSyncRun"]] = relationship(back_populates="resource")
    sources: Mapped[list["ResourceSource"]] = relationship(back_populates="resource")

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "platform",
            "resource_type",
            "resource_id",
            "resource_sub_id",
            name="uq_resource_identity",
        ),
        Index("ix_resources_company_type", "company_id", "resource_type"),
        Index("ix_resources_company_enabled", "company_id", "enabled"),
        Index("ix_resources_company_classification", "company_id", "data_classification"),
        Index(
            "uq_resources_identity_null_sub_id",
            "company_id",
            "platform",
            "resource_type",
            "resource_id",
            unique=True,
            postgresql_where=text("resource_sub_id IS NULL"),
        ),
        Index(
            "uq_resources_identity_with_sub_id",
            "company_id",
            "platform",
            "resource_type",
            "resource_id",
            "resource_sub_id",
            unique=True,
            postgresql_where=text("resource_sub_id IS NOT NULL"),
        ),
    )


class ResourceSource(Base, TimestampMixin):
    __tablename__ = "resource_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resources.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_account_id: Mapped[str] = mapped_column(String(300), default="")
    source_account_label: Mapped[str | None] = mapped_column(String(300))
    access_level: Mapped[str] = mapped_column(String(80), default="read")
    can_sync: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(default=0)
    visibility_scope: Mapped[str] = mapped_column(String(80), default="company")
    allowed_user_ids: Mapped[list] = mapped_column(JSONB, default=list)
    allowed_roles: Mapped[list] = mapped_column(JSONB, default=list)
    allowed_departments: Mapped[list] = mapped_column(JSONB, default=list)
    limitations: Mapped[dict] = mapped_column(JSONB, default=dict)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    company: Mapped["Company"] = relationship()
    resource: Mapped["Resource"] = relationship(back_populates="sources")

    __table_args__ = (
        CheckConstraint(
            "source_type in ("
            "'feishu_app_identity', 'feishu_user_identity', 'external_mail_account', "
            "'personal_dingtalk_account', 'external_web', 'local_import', 'external_connector', 'unknown'"
            ")",
            name="ck_resource_sources_source_type_v5",
        ),
        UniqueConstraint("resource_id", "source_type", "source_account_id", name="uq_resource_source_identity"),
        Index("ix_resource_sources_company_type", "company_id", "source_type"),
        Index("ix_resource_sources_resource_sync", "resource_id", "can_sync"),
    )


class ResourceSyncRun(Base, TimestampMixin):
    __tablename__ = "resource_sync_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resources.id"), nullable=False)
    sync_action: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    items_seen: Mapped[int] = mapped_column(default=0)
    items_indexed: Mapped[int] = mapped_column(default=0)
    items_skipped: Mapped[int] = mapped_column(default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[dict] = mapped_column(JSONB, default=dict)

    company: Mapped["Company"] = relationship()
    resource: Mapped["Resource"] = relationship(back_populates="sync_runs")

    __table_args__ = (
        Index("ix_resource_sync_runs_company_started", "company_id", "started_at"),
        Index("ix_resource_sync_runs_resource_started", "resource_id", "started_at"),
        Index("ix_resource_sync_runs_action_status", "sync_action", "status"),
    )


class ResourcePermission(Base, TimestampMixin):
    __tablename__ = "resource_permissions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resources.id"), nullable=False)
    role_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("roles.id"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    permission_level: Mapped[str] = mapped_column(String(80), default="read")
    conditions_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    company: Mapped["Company"] = relationship()
    resource: Mapped["Resource"] = relationship(back_populates="permissions")
    role: Mapped["Role | None"] = relationship()
    user: Mapped["User | None"] = relationship()

    __table_args__ = (Index("ix_resource_permissions_company", "company_id", "enabled"),)


class KnowledgePermission(Base, TimestampMixin):
    __tablename__ = "knowledge_permissions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    role_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("roles.id"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    domain: Mapped[str] = mapped_column(String(120), nullable=False)
    visibility_level: Mapped[str] = mapped_column(String(80), default="internal")
    permission_level: Mapped[str] = mapped_column(String(80), default="read")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    conditions_json: Mapped[dict] = mapped_column(JSONB, default=dict)

    company: Mapped["Company"] = relationship()
    role: Mapped["Role | None"] = relationship()
    user: Mapped["User | None"] = relationship()

    __table_args__ = (Index("ix_knowledge_permissions_company", "company_id", "enabled"),)


class WorkEvent(Base, TimestampMixin):
    __tablename__ = "work_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id"))
    resource_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("resources.id"))
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    source_type: Mapped[str] = mapped_column(String(80), default="unknown")
    source_account_id: Mapped[str | None] = mapped_column(String(300))
    visibility_scope: Mapped[str] = mapped_column(String(80), default="company")
    allowed_user_ids: Mapped[list] = mapped_column(JSONB, default=list)
    allowed_roles: Mapped[list] = mapped_column(JSONB, default=list)
    allowed_departments: Mapped[list] = mapped_column(JSONB, default=list)
    data_classification: Mapped[str] = mapped_column(String(80), default="company")
    business_domain: Mapped[str] = mapped_column(String(120), default="general")
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    object_type: Mapped[str] = mapped_column(String(80), default="unknown")
    object_id: Mapped[str] = mapped_column(String(500), default="")
    actor: Mapped[str] = mapped_column(String(300), default="system")
    external_id: Mapped[str | None] = mapped_column(String(500))
    thread_id: Mapped[str | None] = mapped_column(String(500))
    title: Mapped[str | None] = mapped_column(String(500))
    content_text: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actors: Mapped[list] = mapped_column(JSONB, default=list)
    labels: Mapped[list] = mapped_column(JSONB, default=list)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    raw_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    importance_score: Mapped[float] = mapped_column(Float, default=0.0)
    sensitivity: Mapped[str] = mapped_column(String(40), default="normal")
    vector_status: Mapped[str] = mapped_column(String(40), default="pending")

    company: Mapped["Company"] = relationship()
    account: Mapped["Account"] = relationship(back_populates="work_events")
    resource: Mapped["Resource | None"] = relationship()
    attachments: Mapped[list["Attachment"]] = relationship(back_populates="work_event")
    extracted_items: Mapped[list["ExtractedItem"]] = relationship(back_populates="work_event")

    __table_args__ = (
        CheckConstraint(
            "source_type in ("
            "'feishu_app_identity', 'feishu_user_identity', 'external_mail_account', "
            "'personal_dingtalk_account', 'external_web', 'local_import', 'external_connector', 'unknown'"
            ")",
            name="ck_work_events_source_type_v5",
        ),
        UniqueConstraint("source", "external_id", name="uq_work_event_source_external"),
        Index("ix_work_events_company_occurred", "company_id", "occurred_at"),
        Index("ix_work_events_company_object", "company_id", "object_type", "object_id"),
        Index("ix_work_events_resource_occurred", "resource_id", "occurred_at"),
        Index("ix_work_events_thread", "thread_id"),
        Index("ix_work_events_company_classification", "company_id", "data_classification"),
    )


class Snapshot(Base, TimestampMixin):
    __tablename__ = "snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    object_type: Mapped[str] = mapped_column(String(80), nullable=False)
    object_id: Mapped[str] = mapped_column(String(500), nullable=False)
    snapshot_type: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="pending_analysis")
    summary: Mapped[str] = mapped_column(Text, default="")
    recommendation: Mapped[str] = mapped_column(String(120), default="")
    risk_level: Mapped[str] = mapped_column(String(40), default="unknown")
    reasons: Mapped[list] = mapped_column(JSONB, default=list)
    source_event_ids: Mapped[list] = mapped_column(JSONB, default=list)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)

    company: Mapped["Company"] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "object_type",
            "object_id",
            "snapshot_type",
            name="uq_snapshots_company_object_type",
        ),
        Index("ix_snapshots_company_status", "company_id", "snapshot_type", "status"),
        Index("ix_snapshots_company_object", "company_id", "object_type", "object_id"),
    )


class MemoryCandidate(Base, TimestampMixin):
    __tablename__ = "memory_candidates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    memory_type: Mapped[str] = mapped_column(String(120), nullable=False)
    object_type: Mapped[str] = mapped_column(String(80), nullable=False)
    object_id: Mapped[str] = mapped_column(String(500), nullable=False)
    evidence_event_ids: Mapped[list] = mapped_column(JSONB, default=list)
    candidate_text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(40), default="candidate")
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)

    company: Mapped["Company"] = relationship()

    __table_args__ = (
        Index("ix_memory_candidates_company_status", "company_id", "memory_type", "status"),
        Index("ix_memory_candidates_company_object", "company_id", "object_type", "object_id"),
    )


class Attachment(Base, TimestampMixin):
    __tablename__ = "attachments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_events.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(200))
    size_bytes: Mapped[int | None]
    storage_bucket: Mapped[str] = mapped_column(String(200), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(800), nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(128))

    work_event: Mapped["WorkEvent"] = relationship(back_populates="attachments")


class ExtractedItem(Base, TimestampMixin):
    __tablename__ = "extracted_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    work_event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("work_events.id"))
    item_type: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    owner: Mapped[str | None] = mapped_column(String(200))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    priority: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40), default="open")
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)

    company: Mapped["Company"] = relationship()
    work_event: Mapped["WorkEvent"] = relationship(back_populates="extracted_items")


class Report(Base, TimestampMixin):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("companies.id"))
    report_type: Mapped[str] = mapped_column(String(40), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)

    company: Mapped["Company"] = relationship()


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("companies.id"))
    actor: Mapped[str | None] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(120))
    target_id: Mapped[str | None] = mapped_column(String(120))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)

    company: Mapped["Company"] = relationship()


class SyncRun(Base, TimestampMixin):
    __tablename__ = "sync_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("companies.id"))
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    sync_type: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    saved_count: Mapped[int] = mapped_column(default=0)
    error_count: Mapped[int] = mapped_column(default=0)
    cursor: Mapped[dict] = mapped_column(JSONB, default=dict)
    summary: Mapped[dict] = mapped_column(JSONB, default=dict)

    company: Mapped["Company"] = relationship(back_populates="sync_runs")

    __table_args__ = (
        Index("ix_sync_runs_company_started", "company_id", "started_at"),
        Index("ix_sync_runs_provider_type", "provider", "sync_type"),
    )


class BotUserAccess(Base, TimestampMixin):
    __tablename__ = "bot_user_access"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    open_id: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(40), default="member")
    access_scope: Mapped[str] = mapped_column(String(40), default="chat")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)

    company: Mapped["Company"] = relationship()

    __table_args__ = (
        UniqueConstraint("company_id", "open_id", name="uq_bot_user_company_open_id"),
        Index("ix_bot_user_open_id", "open_id"),
    )


class BotUserSession(Base, TimestampMixin):
    __tablename__ = "bot_user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    user_open_id: Mapped[str] = mapped_column(String(200), nullable=False)
    chat_id: Mapped[str | None] = mapped_column(String(300))
    session_key: Mapped[str] = mapped_column(String(600), nullable=False)
    last_intent: Mapped[str | None] = mapped_column(String(120))
    last_route: Mapped[str | None] = mapped_column(String(120))
    last_scope: Mapped[str | None] = mapped_column(String(120))
    short_context: Mapped[dict] = mapped_column(JSONB, default=dict)
    message_count: Mapped[int] = mapped_column(default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    company: Mapped["Company"] = relationship()

    __table_args__ = (
        UniqueConstraint("company_id", "session_key", name="uq_bot_user_session_company_key"),
        Index("ix_bot_user_sessions_user", "company_id", "user_open_id"),
        Index("ix_bot_user_sessions_chat", "company_id", "chat_id"),
    )


class BotUserPreference(Base, TimestampMixin):
    __tablename__ = "bot_user_preferences"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    user_open_id: Mapped[str] = mapped_column(String(200), nullable=False)
    answer_style: Mapped[str | None] = mapped_column(Text)
    preferred_language: Mapped[str] = mapped_column(String(40), default="zh-CN")
    favorite_modules: Mapped[list] = mapped_column(JSONB, default=list)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)

    company: Mapped["Company"] = relationship()

    __table_args__ = (
        UniqueConstraint("company_id", "user_open_id", name="uq_bot_user_preference_company_user"),
        Index("ix_bot_user_preferences_user", "company_id", "user_open_id"),
    )


class MemoryFact(Base, TimestampMixin):
    __tablename__ = "memory_facts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    fact_type: Mapped[str] = mapped_column(String(80), nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[str] = mapped_column(String(40), default="medium")
    scope: Mapped[str] = mapped_column(String(80), default="company")
    user_open_id: Mapped[str | None] = mapped_column(String(200))
    chat_id: Mapped[str | None] = mapped_column(String(300))
    source_kind: Mapped[str] = mapped_column(String(80), default="extracted_fact")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_work_event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("work_events.id"))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)

    company: Mapped["Company"] = relationship()
    source_work_event: Mapped["WorkEvent"] = relationship()

    __table_args__ = (
        CheckConstraint(
            "scope in ('company', 'domain', 'personal', 'user', 'chat')",
            name="ck_memory_facts_scope_v5",
        ),
        CheckConstraint(
            "(scope not in ('personal', 'user') or user_open_id is not null)",
            name="ck_memory_facts_personal_owner",
        ),
        CheckConstraint(
            "(scope != 'chat' or chat_id is not null)",
            name="ck_memory_facts_chat_context",
        ),
        Index("ix_memory_facts_company_type", "company_id", "fact_type"),
        Index("ix_memory_facts_subject", "subject"),
        Index("ix_memory_facts_user_scope", "company_id", "user_open_id", "scope"),
        Index("ix_memory_facts_chat_scope", "company_id", "chat_id", "scope"),
    )


class Person(Base, TimestampMixin):
    __tablename__ = "people"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(300))
    email: Mapped[str | None] = mapped_column(String(320))
    role_title: Mapped[str | None] = mapped_column(String(200))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)

    company: Mapped["Company"] = relationship()

    __table_args__ = (Index("ix_people_company_name", "company_id", "name"),)


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(80), default="active")
    owner: Mapped[str | None] = mapped_column(String(200))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)

    company: Mapped["Company"] = relationship()

    __table_args__ = (Index("ix_projects_company_status", "company_id", "status"),)


class Customer(Base, TimestampMixin):
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(80), default="active")
    owner: Mapped[str | None] = mapped_column(String(200))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)

    company: Mapped["Company"] = relationship()

    __table_args__ = (Index("ix_customers_company_status", "company_id", "status"),)
