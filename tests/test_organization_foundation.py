from app.services.organization_foundation import (
    ORG_TARGET_DEPARTMENT,
    ORG_TARGET_GROUP,
    ORG_TARGET_USER,
    OrganizationDirectory,
    normalize_organization_name,
    organization_directory_from_payload,
    resolve_organization_object,
)


def test_organization_resolver_resolves_department_and_group_without_string_fallback() -> None:
    directory = organization_directory_from_payload(
        {
            "departments": [
                {"department_id": "dept_business", "name": "商务部"},
                {"department_id": "group_business", "name": "商务组"},
                {"department_id": "division_power", "name": "半导体事业部"},
            ],
            "users": [{"open_id": "ou_wangyue", "name": "王悦"}],
        }
    )

    department = resolve_organization_object("商务部有多少人", directory, target_types=(ORG_TARGET_DEPARTMENT, ORG_TARGET_GROUP))
    group = resolve_organization_object("商务组多少人，分别是谁", directory, target_types=(ORG_TARGET_DEPARTMENT, ORG_TARGET_GROUP))
    division = resolve_organization_object("半导体事业部多少人", directory, target_types=(ORG_TARGET_DEPARTMENT, ORG_TARGET_GROUP))

    assert department.resolved_type == ORG_TARGET_DEPARTMENT
    assert department.resolved_department_id == "dept_business"
    assert group.resolved_type == ORG_TARGET_GROUP
    assert group.resolved_department_id == "group_business"
    assert division.resolved_type == ORG_TARGET_DEPARTMENT
    assert division.resolved_department_id == "division_power"


def test_organization_resolver_uses_alias_as_standard_org_truth() -> None:
    directory = OrganizationDirectory(
        departments=(
            {
                "id": "dept_sales",
                "target_type": ORG_TARGET_DEPARTMENT,
                "name": "销售部",
                "normalized_name": normalize_organization_name("销售部"),
            },
        ),
        aliases=(
            {
                "alias": "业务部",
                "normalized_alias": normalize_organization_name("业务部"),
                "target_type": ORG_TARGET_DEPARTMENT,
                "target_id": "dept_sales",
                "name": "销售部",
                "confidence": 0.98,
            },
        ),
    )

    resolution = resolve_organization_object("业务部有多少人", directory, target_types=(ORG_TARGET_DEPARTMENT,))

    assert resolution.resolved_type == ORG_TARGET_DEPARTMENT
    assert resolution.resolved_department_id == "dept_sales"
    assert resolution.reason == "alias_exact"
    assert resolution.needs_clarification is False


def test_organization_resolver_uses_unit_suffix_as_generic_org_candidate() -> None:
    directory = organization_directory_from_payload(
        {
            "departments": [{"department_id": "group_business", "name": "商务组"}],
            "users": [],
        }
    )

    resolution = resolve_organization_object("商务部多少人", directory, target_types=(ORG_TARGET_DEPARTMENT, ORG_TARGET_GROUP))

    assert resolution.resolved_type == ORG_TARGET_GROUP
    assert resolution.resolved_department_id == "group_business"
    assert resolution.reason == "unit_suffix_match"
    assert resolution.needs_clarification is False


def test_organization_resolver_does_not_guess_unit_suffix_when_ambiguous() -> None:
    directory = organization_directory_from_payload(
        {
            "departments": [
                {"department_id": "dept_business", "name": "商务部"},
                {"department_id": "group_business", "name": "商务组"},
            ],
            "users": [],
        }
    )

    resolution = resolve_organization_object("商务团队多少人", directory, target_types=(ORG_TARGET_DEPARTMENT, ORG_TARGET_GROUP))

    assert resolution.resolved_department_id == ""
    assert resolution.needs_clarification is True
    assert {item.target_id for item in resolution.candidates} == {"dept_business", "group_business"}
    assert resolution.reason == "unit_suffix_ambiguous"


def test_organization_resolver_returns_candidates_instead_of_full_org_when_ambiguous() -> None:
    directory = organization_directory_from_payload(
        {
            "departments": [
                {"department_id": "dept_business", "name": "商务部"},
                {"department_id": "group_business", "name": "商务组"},
            ],
            "users": [],
        }
    )

    resolution = resolve_organization_object("商务", directory, target_types=(ORG_TARGET_DEPARTMENT, ORG_TARGET_GROUP))

    assert resolution.resolved_department_id == ""
    assert resolution.needs_clarification is True
    assert {item.target_id for item in resolution.candidates} == {"dept_business", "group_business"}
    assert resolution.reason == "not_resolved"


def test_organization_resolver_not_found_does_not_return_people_or_company_fallback() -> None:
    directory = organization_directory_from_payload(
        {
            "departments": [{"department_id": "dept_sales", "name": "销售部"}],
            "users": [{"open_id": "ou_chen", "name": "陈俊"}],
        }
    )

    resolution = resolve_organization_object("商务部", directory, target_types=(ORG_TARGET_DEPARTMENT,))

    assert resolution.resolved_department_id == ""
    assert resolution.candidates == ()
    assert resolution.reason == "not_found"
    assert resolution.needs_clarification is False


def test_organization_resolver_resolves_user_separately_from_department() -> None:
    directory = organization_directory_from_payload(
        {
            "departments": [{"department_id": "dept_project", "name": "项目部"}],
            "users": [{"open_id": "ou_wangyue", "name": "王悦"}],
        }
    )

    resolution = resolve_organization_object("王悦是谁", directory, target_types=(ORG_TARGET_USER,))

    assert resolution.resolved_type == ORG_TARGET_USER
    assert resolution.resolved_user_id == "ou_wangyue"
    assert resolution.reason == "name_exact"


def test_organization_directory_normalizes_feishu_user_status_object() -> None:
    directory = organization_directory_from_payload(
        {
            "departments": [],
            "users": [
                {
                    "open_id": "ou_chen",
                    "name": "陈俊",
                    "status": {
                        "is_activated": True,
                        "is_exited": False,
                        "is_frozen": False,
                        "is_resigned": False,
                        "is_unjoin": False,
                    },
                }
            ],
        }
    )

    assert directory.users[0]["status"] == "active"
