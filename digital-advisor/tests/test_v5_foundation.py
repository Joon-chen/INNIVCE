from uuid import uuid4

from app.api.routes.v5 import router as v5_router
from app.models.entities import (
    BotUserAccess,
    Company,
    CompanySetting,
    FeishuAppConfig,
    KnowledgePermission,
    Resource,
    ResourcePermission,
    Role,
    ToolConfig,
    User,
    UserCompanyRole,
)
from app.services.v5_administration import (
    _ensure_default_owner_agent,
    bootstrap_v5_administration,
    legacy_bot_role_to_v5_role,
    legacy_scope_to_v5_scope,
)


def test_v5_foundation_models_use_expected_table_names() -> None:
    assert CompanySetting.__tablename__ == "company_settings"
    assert User.__tablename__ == "users"
    assert Role.__tablename__ == "roles"
    assert UserCompanyRole.__tablename__ == "user_company_roles"
    assert Resource.__tablename__ == "resources"
    assert ResourcePermission.__tablename__ == "resource_permissions"
    assert KnowledgePermission.__tablename__ == "knowledge_permissions"
    assert ToolConfig.__tablename__ == "tool_configs"


def test_resource_identity_indexes_handle_nullable_sub_ids() -> None:
    index_names = {index.name for index in Resource.__table__.indexes}

    assert "uq_resources_identity_null_sub_id" in index_names
    assert "uq_resources_identity_with_sub_id" in index_names


def test_legacy_bot_role_mapping_is_conservative() -> None:
    assert legacy_bot_role_to_v5_role("admin") == "owner"
    assert legacy_bot_role_to_v5_role("owner") == "owner"
    assert legacy_bot_role_to_v5_role("manager") == "management"
    assert legacy_bot_role_to_v5_role("guest") == "guest"
    assert legacy_bot_role_to_v5_role("member") == "employee"


def test_legacy_scope_mapping_matches_v5_scope_model() -> None:
    assert legacy_scope_to_v5_scope("all") == "all_companies"
    assert legacy_scope_to_v5_scope("company") == "single_company"
    assert legacy_scope_to_v5_scope("domain") == "department"
    assert legacy_scope_to_v5_scope("project") == "project"
    assert legacy_scope_to_v5_scope("chat") == "team"


def test_bootstrap_empty_database_seeds_launch_company_and_owner_agent(monkeypatch) -> None:
    company = Company(id=uuid4(), name="固势 (Gaustek)", code="gaustek")

    monkeypatch.setattr("app.services.v5_administration._target_companies", lambda db, company_id: [])
    monkeypatch.setattr("app.services.v5_administration._ensure_default_launch_company", lambda db: (company, True))
    monkeypatch.setattr("app.services.v5_administration._ensure_default_owner_agent", lambda db, company: 1)
    monkeypatch.setattr("app.services.v5_administration._ensure_company_setting", lambda db, company: 1)
    monkeypatch.setattr(
        "app.services.v5_administration._ensure_default_roles",
        lambda db, company: ({"employee": object(), "owner": object()}, 5),
    )
    monkeypatch.setattr("app.services.v5_administration._ensure_default_permissions", lambda db, company: 3)
    monkeypatch.setattr("app.services.v5_administration._migrate_bot_users", lambda db, company, role_map: (1, 1))
    monkeypatch.setattr("app.services.v5_administration._migrate_feishu_resources", lambda db, company: 0)
    monkeypatch.setattr("app.services.v5_administration._register_feishu_apps_as_bot_resources", lambda db, company: 0)
    monkeypatch.setattr("app.services.v5_administration._ensure_owner_resource_permissions", lambda db, company, role_map: 0)

    class FakeDb:
        def __init__(self):
            self.flushed = False
            self.committed = False

        def flush(self):
            self.flushed = True

        def commit(self):
            self.committed = True

    db = FakeDb()

    result = bootstrap_v5_administration(db)  # type: ignore[arg-type]

    assert db.flushed is True
    assert db.committed is True
    assert result.as_dict()["companies"] == 1
    assert result.as_dict()["bot_users"] == 1
    assert result.as_dict()["users"] == 1
    assert result.as_dict()["user_roles"] == 1


def test_default_owner_agent_seed_is_bot_user_only_and_does_not_fake_app_config(monkeypatch) -> None:
    company = Company(id=uuid4(), name="固势 (Gaustek)", code="gaustek")

    class FakeDb:
        def __init__(self):
            self.added = []

        def scalar(self, query):
            return None

        def add(self, item):
            self.added.append(item)

    db = FakeDb()
    monkeypatch.setattr("app.services.v5_administration.settings.feishu_bot_admin_open_ids", "ou_owner")

    created = _ensure_default_owner_agent(db, company)  # type: ignore[arg-type]

    assert created == 1
    assert len(db.added) == 1
    assert isinstance(db.added[0], BotUserAccess)
    assert not any(isinstance(item, FeishuAppConfig) for item in db.added)
    assert db.added[0].open_id == "ou_owner"
    assert db.added[0].role == "owner"
    assert db.added[0].access_scope == "company"
    assert db.added[0].settings["source"] == "v5_local_seed_owner_agent"
    assert db.added[0].settings["agent_profile"]["requires_feishu_app_config"] is True
    authorizations = db.added[0].settings["user_identity_authorizations"]
    assert list(authorizations) == ["user_identity_bundle"]
    assert authorizations["user_identity_bundle"]["owner_open_id"] == "ou_owner"
    assert authorizations["user_identity_bundle"]["authorization_owner"] == "resource_owner"
    assert authorizations["user_identity_bundle"]["authorization_model"] == "bundle_authorization"
    assert authorizations["user_identity_bundle"]["covered_resources"] == [
        "personal_feishu",
        "external_mail",
        "personal_dingtalk",
        "personal_wechat",
    ]
    assert authorizations["user_identity_bundle"]["can_escalate_original_permissions"] is False


def test_v5_router_exposes_foundation_endpoints() -> None:
    paths = {route.path for route in v5_router.routes}

    assert "/api/v5/bootstrap/foundation" in paths
    assert "/api/v5/os/overview" in paths
    assert "/api/v5/resources" in paths
    assert "/api/v5/tools" in paths
    assert "/api/v5/tools/{tool_name}" in paths
    assert "/api/v5/administration/users" in paths
