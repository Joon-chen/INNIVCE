from app.services.capability_registry_builder import (
    CapabilityRegistryBuilder,
    build_mock_capability_registry_payload,
)


def test_capability_registry_builder_outputs_page_payloads() -> None:
    payload = CapabilityRegistryBuilder(company_id="company-1", generated_at="2026-06-21T00:00:00+00:00").build()

    assert payload["version"] == "capability_registry_payload_v1"
    assert payload["registry_version"] == "capability_registry_v1"
    assert payload["company_id"] == "company-1"
    assert set(payload) >= {
        "registry_health",
        "catalog_payload",
        "skill_registry_payload",
        "governance_payload",
        "diagnostics_payload",
    }
    assert set(payload["registry_health"]) >= {
        "status",
        "domains",
        "capabilities",
        "skills",
        "providers",
        "findings",
        "orphan_skills",
        "missing_provider",
    }

    catalog = payload["catalog_payload"]
    assert catalog["summary"]["domain_count"] == 7
    assert {item["domain_id"] for item in catalog["domains"]} == {
        "people",
        "communication",
        "workspace",
        "process",
        "knowledge",
        "business",
        "intelligence",
    }
    assert all("skills" not in capability for domain in catalog["domains"] for capability in domain["capabilities"])
    assert all("provider_bindings" not in capability for domain in catalog["domains"] for capability in domain["capabilities"])


def test_skill_registry_payload_maps_capability_to_skills() -> None:
    payload = CapabilityRegistryBuilder(company_id="company-1").skill_registry_payload()
    capability_map = {item["capability_id"]: item for item in payload["capabilities"]}

    assert "approval_approve" in capability_map
    assert "task_query" in capability_map
    assert "meeting_schedule" in capability_map

    approval_skills = {skill["skill_id"]: skill for skill in capability_map["approval_approve"]["skills"]}
    assert approval_skills["approval.approve"]["risk_level"] == "high"
    assert approval_skills["approval.approve"]["requires_confirmation"] is True
    assert approval_skills["approval.approve"]["provider_binding_count"] == 1


def test_skill_registry_payload_freezes_execution_identity_contracts() -> None:
    payload = CapabilityRegistryBuilder(company_id="company-1").skill_registry_payload()
    capability_map = {item["capability_id"]: item for item in payload["capabilities"]}

    task_update_skills = {skill["skill_id"]: skill for skill in capability_map["task_update"]["skills"]}
    task_complete_identity = task_update_skills["task.complete_task"]["identity_contract"]
    assert task_complete_identity["actor_identity"] == "USER"
    assert task_complete_identity["credential_mode"] == "USER_TOKEN"
    assert task_complete_identity["requires_authorization"] is True

    approval_approve_skills = {skill["skill_id"]: skill for skill in capability_map["approval_approve"]["skills"]}
    approval_approve_identity = approval_approve_skills["approval.approve"]["identity_contract"]
    assert approval_approve_identity["actor_identity"] == "USER"
    assert approval_approve_identity["credential_mode"] == "USER_TOKEN"

    approval_query_skills = {skill["skill_id"]: skill for skill in capability_map["approval_query"]["skills"]}
    approval_query_identity = approval_query_skills["approval.list_pending"]["identity_contract"]
    assert approval_query_identity["actor_identity"] == "BOT"
    assert approval_query_identity["credential_mode"] == "TENANT_TOKEN"
    assert approval_query_identity["requires_authorization"] is False


def test_governance_payload_accepts_findings_and_provider_health() -> None:
    builder = CapabilityRegistryBuilder(
        company_id="company-1",
        tool_configs=({"tool_name": "task_qa", "provider": "lark_cli", "enabled": False, "supports_write": False},),
        provider_health=({"provider_id": "feishu_approval", "status": "failed", "last_error": "token expired"},),
        governance_findings=(
            {
                "finding_id": "custom.skill.missing_provider",
                "scope": "skill",
                "severity": "P1",
                "domain_id": "process",
                "capability_id": "approval_approve",
                "skill_id": "approval.approve",
                "provider_id": "feishu_approval",
                "title": "Provider missing",
                "message": "provider missing",
                "recommendation": "bind provider",
                "status": "open",
            },
        ),
    )

    governance = builder.governance_payload()
    diagnostics = builder.diagnostics_payload()

    assert governance["summary"]["finding_count"] >= 2
    assert any(item["finding_id"] == "custom.skill.missing_provider" for item in governance["findings"])
    assert any(item["scope"] == "provider" and item["provider_id"] == "feishu_approval" for item in governance["findings"])
    assert any(item["scope"] == "skill" and item["skill_id"] == "task_qa" for item in governance["findings"])
    assert diagnostics["summary"]["provider_status"] == "degraded"
    assert diagnostics["summary"]["status"] == "degraded"
    assert "domain_id" not in diagnostics["summary"]


def test_consistency_report_and_migration_assessment_are_available() -> None:
    builder = CapabilityRegistryBuilder(company_id="company-1")
    report = builder.consistency_report()
    assessment = builder.migration_assessment()

    assert set(report) == {
        "status",
        "MissingCapability",
        "MissingSkill",
        "MissingProvider",
        "OrphanSkill",
        "OrphanProvider",
    }
    assert "capability_catalog" in assessment["ready_pages"]
    assert "skill_registry" in assessment["ready_pages"]
    assert "governance_center" in assessment["ready_pages"]
    assert "system_diagnostics" in assessment["ready_pages"]


def test_registry_health_is_healthy_after_skill_cleanup() -> None:
    payload = CapabilityRegistryBuilder(company_id="company-1").build()
    health = payload["registry_health"]

    assert health["status"] == "healthy"
    assert health["missing_capability"] == []
    assert health["missing_skill"] == []
    assert health["missing_provider"] == []
    assert health["orphan_skills"] == []
    assert health["orphan_provider"] == []


def test_capability_lifecycle_guard_enforces_full_chain() -> None:
    guard = CapabilityRegistryBuilder(company_id="company-1").lifecycle_guard_report()

    assert guard == {
        "status": "healthy",
        "MissingDomainForCapability": [],
        "MissingCapabilityForSkill": [],
        "MissingSkillForRuntime": [],
        "MissingProviderForSkill": [],
        "OrphanSkill": [],
        "OrphanProviderBinding": [],
    }


def test_mock_payload_can_drive_four_pages() -> None:
    payload = build_mock_capability_registry_payload()

    assert payload["catalog_payload"]["domains"]
    assert payload["skill_registry_payload"]["capabilities"]
    assert payload["governance_payload"]["findings"]
    assert payload["diagnostics_payload"]["providers"]
