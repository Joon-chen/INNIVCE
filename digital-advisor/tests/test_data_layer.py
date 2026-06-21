from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.exc import OperationalError

from app.api.routes.operations_resource_list_routes import list_feishu_resources
from app.models.entities import Account, MemoryFact, ResourceSource, WorkEvent
from app.services.access_control import AccessPrincipal
from app.services.companies_admin import quick_setup_mail_resource_payload, upsert_v5_mail_resource
from app.services.data.account_types import (
    EXTERNAL_MAIL,
    PERSONAL_DINGTALK,
    PERSONAL_FEISHU_USER,
    normalize_account_type,
    require_account_type,
)
from app.services.operations_resources import (
    FeishuResourceRegistrationRequest,
    dashboard_resource_summary,
    enabled_legacy_feishu_resource_count,
    enabled_v5_feishu_resource_count,
    legacy_feishu_resource_retirement_status,
    register_v5_feishu_resource,
    v5_feishu_resource_counts,
)
from app.services.data.memory import memory_fact_access_condition
from app.services.data.resource_sources import (
    APP_IDENTITY,
    EXTERNAL_MAIL_ACCOUNT,
    EXTERNAL_WEB,
    FEISHU_APP_IDENTITY,
    FEISHU_USER_IDENTITY,
    KNOWLEDGE_DATA,
    LOCAL_IMPORT,
    OPERATIONAL_DATA,
    PERSONAL_DINGTALK_ACCOUNT,
    USER_IDENTITY,
    data_source_group,
    data_type_for_resource,
    normalize_source_type,
    preferred_source,
    source_identity_type,
    source_policy_payload,
    source_priority,
    storage_for_data_type,
)
from app.services.resource_registry import migrate_legacy_feishu_resources
from app.services.v5_administration import _migrate_feishu_resources


def test_source_identity_type_normalizes_legacy_source_names() -> None:
    assert source_identity_type("feishu_app") == APP_IDENTITY
    assert source_identity_type("feishu_user") == USER_IDENTITY
    assert source_identity_type("mail_account") == USER_IDENTITY
    assert source_identity_type("manual_import") == LOCAL_IMPORT
    assert normalize_source_type("feishu_app") == FEISHU_APP_IDENTITY
    assert normalize_source_type("feishu_user") == FEISHU_USER_IDENTITY
    assert normalize_source_type("mail_account") == EXTERNAL_MAIL_ACCOUNT


def test_account_type_normalizes_v5_account_taxonomy() -> None:
    assert normalize_account_type(provider="feishu_user") == PERSONAL_FEISHU_USER
    assert normalize_account_type(provider="imap") == EXTERNAL_MAIL
    assert normalize_account_type(provider="gmail") == EXTERNAL_MAIL
    assert normalize_account_type(provider="dingtalk") == PERSONAL_DINGTALK
    assert require_account_type("external_mail", provider="unknown") == EXTERNAL_MAIL


def test_account_type_constraints_allow_only_v5_canonical_taxonomy() -> None:
    account_checks = {constraint.name for constraint in Account.__table__.constraints}

    assert "ck_accounts_account_type_v5" in account_checks


def test_source_priority_prefers_app_identity_over_user_identity() -> None:
    assert source_priority("feishu_app") > source_priority("feishu_user")
    assert source_priority("feishu_user") > source_priority("manual_import")


def test_preferred_source_uses_syncable_identity_priority() -> None:
    now = datetime.now(UTC)
    app_source = SimpleNamespace(source_type="feishu_app", priority=None, can_sync=True, last_seen_at=now)
    user_source = SimpleNamespace(
        source_type="feishu_user",
        priority=None,
        can_sync=True,
        last_seen_at=now + timedelta(minutes=5),
    )
    disabled_app_source = SimpleNamespace(
        source_type="feishu_app",
        priority=None,
        can_sync=False,
        last_seen_at=now + timedelta(minutes=10),
    )

    assert preferred_source([user_source, app_source, disabled_app_source]) is app_source


def test_source_policy_payload_explains_identity_rule() -> None:
    payload = source_policy_payload(SimpleNamespace(source_type="mail_account", priority=None, can_sync=True))

    assert payload["identity_type"] == USER_IDENTITY
    assert payload["normalized_source_type"] == EXTERNAL_MAIL_ACCOUNT
    assert payload["data_source"] == "external_mail"
    assert payload["priority_reason"] == "user_identity_supplements_app_identity"


def test_resource_source_payload_exposes_account_type_from_settings() -> None:
    from app.services.resource_sources import resource_source_payload

    source = ResourceSource(
        company_id=uuid4(),
        resource_id=uuid4(),
        source_type="external_mail_account",
        source_account_id="acc_1",
        source_account_label="老板邮箱",
        settings={"account_type": EXTERNAL_MAIL},
    )

    payload = resource_source_payload(source)

    assert payload is not None
    assert payload["account_type"] == EXTERNAL_MAIL
    assert payload["data_source"] == "external_mail"


def test_data_layer_taxonomy_matches_v5_xmind_source_and_type_layers() -> None:
    assert data_source_group(FEISHU_APP_IDENTITY) == "feishu_enterprise_app"
    assert data_source_group(FEISHU_USER_IDENTITY) == "feishu_personal_user"
    assert data_source_group(EXTERNAL_MAIL_ACCOUNT) == "external_mail"
    assert data_source_group(PERSONAL_DINGTALK_ACCOUNT) == "personal_dingtalk"
    assert data_source_group(EXTERNAL_WEB) == "knowledge_external_web"
    assert data_type_for_resource("bitable_table") == OPERATIONAL_DATA
    assert data_type_for_resource("drive_file") == KNOWLEDGE_DATA
    assert data_type_for_resource("web_page") == KNOWLEDGE_DATA
    assert storage_for_data_type(OPERATIONAL_DATA) == "postgresql"
    assert storage_for_data_type(KNOWLEDGE_DATA) == "document_store_vector_db"
    assert storage_for_data_type(KNOWLEDGE_DATA, source_type=EXTERNAL_WEB) == "web"


def test_source_type_constraints_allow_only_v5_canonical_taxonomy() -> None:
    resource_source_checks = {constraint.name for constraint in ResourceSource.__table__.constraints}
    work_event_checks = {constraint.name for constraint in WorkEvent.__table__.constraints}

    assert "ck_resource_sources_source_type_v5" in resource_source_checks
    assert "ck_work_events_source_type_v5" in work_event_checks


def test_memory_fact_access_condition_requires_current_chat_for_chat_scope() -> None:
    principal = AccessPrincipal(company_id=uuid4(), role="member", open_id="ou_1")

    condition = memory_fact_access_condition(
        MemoryFact,
        principal=principal,
        requested_scope="chat",
        chat_id="oc_1",
    )
    compiled = str(condition.compile(compile_kwargs={"literal_binds": True}))

    assert "memory_facts.scope = 'chat'" in compiled
    assert "memory_facts.chat_id = 'oc_1'" in compiled
    assert "memory_facts.user_open_id = 'ou_1'" in compiled


def test_memory_fact_access_condition_limits_personal_scope_to_actor() -> None:
    principal = AccessPrincipal(company_id=uuid4(), role="member", open_id="ou_1")

    condition = memory_fact_access_condition(MemoryFact, principal=principal, requested_scope="personal")
    compiled = str(condition.compile(compile_kwargs={"literal_binds": True}))

    assert "memory_facts.scope IN ('personal', 'user')" in compiled
    assert "memory_facts.user_open_id = 'ou_1'" in compiled


def test_memory_fact_access_condition_owner_company_scope_excludes_personal_and_chat_scope_by_default() -> None:
    principal = AccessPrincipal(company_id=uuid4(), role="owner", open_id="ou_owner")

    condition = memory_fact_access_condition(MemoryFact, principal=principal, requested_scope="company")
    compiled = str(condition.compile(compile_kwargs={"literal_binds": True}))

    assert "memory_facts.scope = 'company'" in compiled
    assert "memory_facts.scope = 'domain'" in compiled
    assert "'chat'" not in compiled
    assert "'personal'" not in compiled
    assert "'user'" not in compiled


def test_memory_fact_access_condition_member_company_scope_is_denied() -> None:
    principal = AccessPrincipal(company_id=uuid4(), role="member", open_id="ou_1")

    condition = memory_fact_access_condition(MemoryFact, principal=principal, requested_scope="company")
    compiled = str(condition.compile(compile_kwargs={"literal_binds": True}))

    assert compiled == "false"


def test_memory_fact_scope_constraints_require_owner_and_chat_context() -> None:
    constraint_names = {constraint.name for constraint in MemoryFact.__table__.constraints}

    assert "ck_memory_facts_scope_v5" in constraint_names
    assert "ck_memory_facts_personal_owner" in constraint_names
    assert "ck_memory_facts_chat_context" in constraint_names


def test_operations_feishu_resource_upsert_writes_v5_resource_only(monkeypatch) -> None:
    calls = {}
    company_id = uuid4()
    app_config_id = uuid4()
    v5_id = uuid4()

    def fake_upsert_resource(db, **kwargs):
        calls["db"] = db
        calls["kwargs"] = kwargs
        return SimpleNamespace(id=v5_id)

    monkeypatch.setattr("app.services.operations_resources.upsert_resource", fake_upsert_resource)
    data = FeishuResourceRegistrationRequest(
        company_id=company_id,
        app_config_id=app_config_id,
        resource_type="bitable_table",
        external_id="app_1:tbl_1",
        name="客户表",
        sync_enabled=True,
        settings={"app_token": "app_1", "table_id": "tbl_1", "business_domain": "sales"},
    )
    db = SimpleNamespace()

    resource = register_v5_feishu_resource(
        db,
        company_id=data.company_id,
        app_config_id=data.app_config_id,
        resource_type=data.resource_type,
        external_id=data.external_id,
        name=data.name,
        sync_enabled=data.sync_enabled,
        settings=data.settings,
    )

    assert resource.id == v5_id
    assert calls["db"] is db
    assert calls["kwargs"]["company_id"] == company_id
    assert calls["kwargs"]["platform"] == "feishu"
    assert calls["kwargs"]["resource_type"] == "bitable_table"
    assert calls["kwargs"]["resource_id"] == "app_1"
    assert calls["kwargs"]["resource_sub_id"] == "tbl_1"
    assert calls["kwargs"]["business_domain"] == "sales"
    assert calls["kwargs"]["app_config_id"] == app_config_id
    assert "legacy_feishu_resource_id" not in calls["kwargs"]
    assert calls["kwargs"]["config_json"]["source"] == "operations_feishu_resource_upsert"


def test_migrate_legacy_feishu_resources_centralizes_legacy_resource_reads(monkeypatch) -> None:
    legacy_id = uuid4()
    v5_id = uuid4()
    app_config = SimpleNamespace(id=uuid4(), company_id=uuid4(), name="大飞哥")
    captured = {}

    class FakeMappingResult:
        def all(self):
            return [
                {
                    "id": legacy_id,
                    "resource_type": "approval_code",
                    "external_id": "approval_xxx",
                    "name": "付款审批",
                    "settings": {"permission_level": "department"},
                    "sync_enabled": True,
                }
            ]

    class FakeExecuteResult:
        def scalar_one_or_none(self):
            return "feishu_resources"

        def mappings(self):
            return FakeMappingResult()

    class FakeDb:
        def execute(self, query, params=None):
            if "to_regclass" in str(query):
                captured["table_check"] = True
                return FakeExecuteResult()
            captured["query"] = query
            captured["params"] = params
            return FakeExecuteResult()

    def fake_upsert(db, *, app_config, item, legacy_feishu_resource_id, source_label):
        captured["db"] = db
        captured["app_config"] = app_config
        captured["item"] = item
        captured["legacy_feishu_resource_id"] = legacy_feishu_resource_id
        captured["source_label"] = source_label
        return SimpleNamespace(id=v5_id)

    monkeypatch.setattr("app.services.resource_registry.upsert_feishu_discovered_resource", fake_upsert)

    migrations = migrate_legacy_feishu_resources(FakeDb(), app_config=app_config, resource_type="approval_code")

    assert migrations[0].legacy_id == legacy_id
    assert migrations[0].external_id == "approval_xxx"
    assert migrations[0].resource.id == v5_id
    assert captured["app_config"] is app_config
    assert captured["item"]["resource_type"] == "approval_code"
    assert captured["item"]["settings"]["source"] == "legacy_feishu_resources"
    assert captured["legacy_feishu_resource_id"] == legacy_id
    assert captured["source_label"] == "legacy_feishu_resources"


def test_migrate_legacy_feishu_resources_handles_retired_table() -> None:
    app_config = SimpleNamespace(id=uuid4(), company_id=uuid4(), name="大飞哥")

    class FakeDb:
        def execute(self, query, params=None):
            if "to_regclass" in str(query):
                class MissingTableResult:
                    def scalar_one_or_none(self):
                        return None

                return MissingTableResult()
            raise AssertionError("retired feishu_resources table should not be queried")

    assert migrate_legacy_feishu_resources(FakeDb(), app_config=app_config, resource_type="approval_code") == []


def test_v5_administration_legacy_resource_migration_handles_retired_table() -> None:
    company = SimpleNamespace(id=uuid4())

    class FakeDb:
        def execute(self, query, params):
            raise OperationalError("select * from feishu_resources", {}, Exception("missing table"))

    assert _migrate_feishu_resources(FakeDb(), company) == 0


def test_operations_feishu_resource_upsert_uses_registry_when_app_config_available(monkeypatch) -> None:
    calls = {}
    company_id = uuid4()
    app_config = SimpleNamespace(id=uuid4(), company_id=company_id)
    v5_id = uuid4()

    def fake_upsert_discovered_resource(db, **kwargs):
        calls["db"] = db
        calls["kwargs"] = kwargs
        return SimpleNamespace(id=v5_id)

    monkeypatch.setattr("app.services.operations_resources.upsert_feishu_discovered_resource", fake_upsert_discovered_resource)
    data = FeishuResourceRegistrationRequest(
        company_id=company_id,
        app_config_id=app_config.id,
        resource_type="approval_code",
        external_id="approval_xxx",
        name="付款审批",
        sync_enabled=True,
        settings={"source": "manual"},
    )
    db = SimpleNamespace()

    resource = register_v5_feishu_resource(
        db,
        company_id=data.company_id,
        app_config_id=data.app_config_id,
        resource_type=data.resource_type,
        external_id=data.external_id,
        name=data.name,
        sync_enabled=data.sync_enabled,
        settings=data.settings,
        app_config=app_config,
    )

    assert resource.id == v5_id
    assert calls["db"] is db
    assert calls["kwargs"]["app_config"] is app_config
    assert calls["kwargs"]["item"]["resource_type"] == "approval_code"
    assert calls["kwargs"]["item"]["external_id"] == "approval_xxx"
    assert calls["kwargs"]["source_label"] == "operations_feishu_resource_upsert"
    assert calls["kwargs"].get("legacy_feishu_resource_id") is None


def test_operations_feishu_resource_list_uses_v5_resource_as_primary() -> None:
    company_id = uuid4()
    app_config_id = uuid4()
    legacy_id = uuid4()
    resource_id = uuid4()
    v5_resource = SimpleNamespace(
        id=resource_id,
        company_id=company_id,
        platform="feishu",
        resource_type="bitable_table",
        resource_name="客户表",
        resource_id="app_1",
        resource_sub_id="tbl_1",
        sync_mode="manual",
        permission_level="company",
        data_classification="company",
        business_domain="sales",
        enabled=True,
        last_sync_at=None,
        app_config_id=app_config_id,
        config_json={"external_id": "app_1:tbl_1", "settings": {"source": "operations_feishu_resource_upsert"}},
        legacy_feishu_resource_id=legacy_id,
    )
    class FakeScalarResult:
        def __init__(self, items):
            self.items = items

        def all(self):
            return self.items

    class FakeDb:
        def scalars(self, query):
            return FakeScalarResult([v5_resource])

    result = list_feishu_resources(company_id=company_id, db=FakeDb())

    assert result["source_model"] == "Resource"
    assert result["counts"] == {"bitable": 1}
    assert result["items"][0]["id"] == str(resource_id)
    assert result["items"][0]["v5_resource_id"] == str(resource_id)
    assert result["items"][0]["resource_id"] == "app_1"
    assert result["items"][0]["resource_sub_id"] == "tbl_1"
    assert result["items"][0]["external_id"] == "app_1:tbl_1"
    assert result["items"][0]["legacy_id"] == str(legacy_id)
    assert result["items"][0]["legacy_external_id"] == "app_1:tbl_1"
    assert result["items"][0]["settings"] == {"source": "operations_feishu_resource_upsert"}


def test_quick_company_setup_mail_resource_writes_v5_resource_only(monkeypatch) -> None:
    calls = {}
    company_id = uuid4()
    app_config_id = uuid4()
    v5_id = uuid4()
    app_config = SimpleNamespace(id=app_config_id, company_id=company_id)

    def fake_upsert_discovered_resource(db, **kwargs):
        calls["db"] = db
        calls["kwargs"] = kwargs
        return SimpleNamespace(id=v5_id)

    monkeypatch.setattr("app.services.companies_admin.upsert_feishu_discovered_resource", fake_upsert_discovered_resource)
    db = SimpleNamespace()

    resource = upsert_v5_mail_resource(
        db,
        company=SimpleNamespace(id=company_id),
        app_config=app_config,
        item={
            "resource_type": "mail_folder",
            "external_id": "boss@example.com:INBOX",
            "name": "INBOX",
            "sync_enabled": True,
            "settings": {"user_mailbox_id": "boss@example.com", "folder_id": "INBOX", "source": "quick_setup"},
        },
    )

    assert resource.id == v5_id
    assert calls["db"] is db
    assert calls["kwargs"]["app_config"] is app_config
    assert calls["kwargs"]["item"]["resource_type"] == "mail_folder"
    assert calls["kwargs"]["item"]["external_id"] == "boss@example.com:INBOX"
    assert calls["kwargs"]["item"]["settings"] == {
        "user_mailbox_id": "boss@example.com",
        "folder_id": "INBOX",
        "source": "quick_setup",
    }
    assert calls["kwargs"].get("legacy_feishu_resource_id") is None
    assert calls["kwargs"]["source_label"] == "quick_company_setup"


def test_quick_company_setup_mail_resource_payload_is_v5_primary() -> None:
    v5_id = uuid4()
    payload = quick_setup_mail_resource_payload(
        SimpleNamespace(
            id=v5_id,
            resource_type="mail_folder",
            resource_id="boss@example.com",
            resource_sub_id="INBOX",
            resource_name="INBOX",
            config_json={
                "external_id": "boss@example.com:INBOX",
                "settings": {"user_mailbox_id": "boss@example.com", "folder_id": "INBOX"},
            },
        )
    )

    assert payload["id"] == str(v5_id)
    assert payload["v5_resource_id"] == str(v5_id)
    assert payload["resource_id"] == "boss@example.com"
    assert payload["resource_sub_id"] == "INBOX"
    assert payload["external_id"] == "boss@example.com:INBOX"
    assert payload["settings"] == {"user_mailbox_id": "boss@example.com", "folder_id": "INBOX"}
    assert "legacy_id" not in payload
    assert "legacy_settings" not in payload


def test_operations_dashboard_resource_counts_use_v5_primary_metrics() -> None:
    class FakeExecuteResult:
        def all(self):
            return [("chat", 2), ("mail_folder", 1)]

    class FakeDb:
        def __init__(self):
            self.scalar_values = [3, 7]

        def execute(self, query):
            return FakeExecuteResult()

        def scalar(self, query):
            return self.scalar_values.pop(0)

    db = FakeDb()

    assert v5_feishu_resource_counts(db) == {"chat": 2, "mail_folder": 1}
    assert enabled_v5_feishu_resource_count(db) == 3
    assert enabled_legacy_feishu_resource_count(db) == 7


def test_operations_dashboard_resource_summary_separates_legacy_migration_counts() -> None:
    class FakeExecuteResult:
        def all(self):
            return [("chat", 2), ("mail_folder", 1)]

    class FakeDb:
        def __init__(self):
            self.scalar_values = [11, 7, 9, 3]

        def execute(self, query):
            return FakeExecuteResult()

        def scalar(self, query):
            return self.scalar_values.pop(0)

    summary = dashboard_resource_summary(FakeDb())

    assert summary["enabled_resources"] == 3
    assert summary["resource_counts"] == {"chat": 2, "mail_folder": 1}
    assert summary["migration"] == {
        "resource_model": "Resource",
        "legacy_feishu_resources": 7,
        "legacy_retirement": {
            "legacy_table_status": "active",
            "total_legacy_resources": 11,
            "enabled_legacy_resources": 7,
            "mapped_to_v5_resources": 9,
            "unmapped_legacy_resources": 2,
            "can_drop_legacy_table": False,
            "next_action": "run_legacy_migration_fallback",
        },
    }


def test_legacy_feishu_resource_retirement_status_allows_drop_after_mapping() -> None:
    class FakeDb:
        def __init__(self):
            self.scalar_values = [7, 0, 7]

        def scalar(self, query):
            return self.scalar_values.pop(0)

    assert legacy_feishu_resource_retirement_status(FakeDb()) == {
        "legacy_table_status": "active",
        "total_legacy_resources": 7,
        "enabled_legacy_resources": 0,
        "mapped_to_v5_resources": 7,
        "unmapped_legacy_resources": 0,
        "can_drop_legacy_table": True,
        "next_action": "drop_legacy_table",
    }


def test_legacy_feishu_resource_retirement_status_handles_retired_table() -> None:
    class FakeDb:
        def __init__(self):
            self.rollbacks = 0

        def scalar(self, query):
            statement = str(query)
            if "feishu_resources" in statement:
                raise OperationalError("select count(*) from feishu_resources", {}, Exception("missing table"))
            return 0

        def rollback(self):
            self.rollbacks += 1

    db = FakeDb()

    assert legacy_feishu_resource_retirement_status(db) == {
        "legacy_table_status": "empty_or_retired",
        "total_legacy_resources": 0,
        "enabled_legacy_resources": 0,
        "mapped_to_v5_resources": 0,
        "unmapped_legacy_resources": 0,
        "can_drop_legacy_table": True,
        "next_action": "keep_legacy_table_retired",
    }
    assert db.rollbacks == 2
