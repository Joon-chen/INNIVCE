from types import SimpleNamespace
from uuid import uuid4

from app.services.feishu import identity as feishu_identity


def test_sender_identity_defaults_to_employee_personal_agent() -> None:
    class FakeDb:
        def __init__(self):
            self.added = []
            self.flushed = False

        def scalar(self, query):
            return None

        def add(self, item):
            self.added.append(item)

        def flush(self):
            self.flushed = True

    db = FakeDb()
    company_id = uuid4()
    payload = {
        "event": {
            "sender": {"sender_id": {"open_id": "ou_member"}, "name": "员工A"},
            "message": {"chat_id": "oc_1", "message_id": "om_1"},
        }
    }

    identity = feishu_identity.get_sender_identity(db, SimpleNamespace(company_id=company_id), payload)

    assert identity.role == "member"
    assert identity.access_scope == "personal"
    assert identity.source == "default_employee_agent"
    assert identity.can_query_company is False
    assert db.flushed is True
    assert len(db.added) == 2
    access = next(item for item in db.added if getattr(item, "open_id", None) == "ou_member")
    assert access.company_id == company_id
    assert access.open_id == "ou_member"
    assert access.display_name == "员工A"
    assert access.role == "member"
    assert access.access_scope == "personal"
    assert access.settings["agent_profile"]["status"] == "active"
    assert access.settings["agent_profile"]["scope"] == "personal"
    assert access.settings["agent_profile"]["entrypoint"] == "feishu_bot"
    assert access.settings["agent_profile"]["first_chat_id"] == "oc_1"
    assert access.settings["agent_profile"]["first_message_id"] == "om_1"
    assert access.settings["agent_profile"]["activated_at"]
    bundle = access.settings["user_identity_authorizations"]["user_identity_bundle"]
    assert bundle["status"] == "not_authorized"
    assert bundle["authorization_model"] == "bundle_authorization"
    assert bundle["covered_resources"] == ["personal_feishu", "external_mail", "personal_dingtalk", "personal_wechat"]
    assert bundle["owner_open_id"] == "ou_member"
    assert bundle["can_escalate_original_permissions"] is False
    audit = next(item for item in db.added if getattr(item, "action", None) == "agent.employee.activate")
    assert audit.company_id == company_id
    assert audit.actor == "ou_member"
    assert audit.target_type == "employee_agent"
    assert audit.target_id == "ou_member"
    assert audit.payload["agent_type"] == "employee_personal_agent"
    assert audit.payload["agent_entrypoint"] == "feishu_bot"
    assert audit.payload["agent_scope"] == "personal"
    assert audit.payload["first_chat_id"] == "oc_1"
    assert audit.payload["first_message_id"] == "om_1"
    assert audit.payload["shared_business_tool_count"] == 9
    assert audit.payload["enterprise_scope_status"] == "available"
    assert audit.payload["enterprise_resources_available_after_agent_created"] is True
    assert audit.payload["user_identity_access_model"] == "bundle_authorization"
    assert audit.payload["user_identity_bundle_status"] == "not_authorized"
    assert audit.payload["can_escalate_original_permissions"] is False


def test_sender_identity_uses_database_access() -> None:
    class FakeDb:
        def __init__(self):
            self.added = []

        def scalar(self, query):
            return SimpleNamespace(
                role="manager",
                access_scope="domain",
                display_name="财务负责人",
                settings={"permission_domains": ["finance"], "allowed_resources": ["付款审批"], "email": "owner@example.com"},
            )

    payload = {"event": {"sender": {"sender_id": {"open_id": "ou_finance"}}}}

    identity = feishu_identity.get_sender_identity(FakeDb(), SimpleNamespace(company_id=uuid4()), payload)

    assert identity.role == "manager"
    assert identity.domains == ("finance",)
    assert identity.allowed_resources == ("付款审批",)
    assert identity.email == "owner@example.com"


def test_identity_payload_round_trip() -> None:
    original = feishu_identity.BotIdentity(
        open_id="ou_1",
        role="admin",
        access_scope="company",
        display_name="老板",
        domains=("all",),
        allowed_resources=("审批",),
        email="boss@example.com",
    )

    restored = feishu_identity.identity_from_payload(feishu_identity.identity_payload(original))

    assert restored == original


def test_permission_denied_reply_mentions_current_identity() -> None:
    identity = feishu_identity.BotIdentity(open_id="ou_1", role="member", access_scope="chat")

    reply = feishu_identity.permission_denied_reply(identity, "老板邮箱")

    assert "老板邮箱" in reply
    assert identity.label in reply
