from types import SimpleNamespace
from uuid import UUID, uuid4

from app.services.organization_foundation import (
    ORG_TARGET_DEPARTMENT,
    ORG_TARGET_GROUP,
    ORG_TARGET_USER,
    OrganizationDirectory,
    normalize_organization_name,
    organization_directory_from_payload,
    resolve_department_members,
    resolve_organization_object,
)


COMPANY_ID = UUID("091fb8ae-443d-49b2-9c67-5e5f1353f2d5")


class _ScalarResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _ExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _OrganizationSession:
    def __init__(self, *, departments, users, root, rows):
        self._scalar_results = [departments, users, []]
        self._root = root
        self._departments = departments
        self._rows = rows

    def scalars(self, _statement):
        if self._scalar_results:
            return _ScalarResult(self._scalar_results.pop(0))
        return _ScalarResult(self._departments)

    def scalar(self, _statement):
        return self._root

    def execute(self, _statement):
        return _ExecuteResult(self._rows)


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


def test_organization_resolver_alias_returns_target_name_not_alias_text() -> None:
    directory = OrganizationDirectory(
        departments=(
            {
                "id": "group_business",
                "target_type": ORG_TARGET_GROUP,
                "name": "商务组",
                "normalized_name": normalize_organization_name("商务组"),
            },
        ),
        aliases=(
            {
                "alias": "商务部",
                "normalized_alias": normalize_organization_name("商务部"),
                "target_type": ORG_TARGET_GROUP,
                "target_id": "group_business",
                "confidence": 0.98,
            },
        ),
    )

    resolution = resolve_organization_object("商务部", directory, target_types=(ORG_TARGET_DEPARTMENT, ORG_TARGET_GROUP))

    assert resolution.resolved_type == ORG_TARGET_GROUP
    assert resolution.resolved_department_id == "group_business"
    assert resolution.resolved_name == "商务组"
    assert resolution.reason == "alias_exact"


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


def test_organization_resolver_uses_bare_unit_stem_when_unique() -> None:
    directory = organization_directory_from_payload(
        {
            "departments": [
                {"department_id": "group_admin", "name": "行政组"},
                {"department_id": "group_it", "name": "IT组"},
            ],
            "users": [],
        }
    )

    admin = resolve_organization_object("行政", directory, target_types=(ORG_TARGET_DEPARTMENT, ORG_TARGET_GROUP))
    it = resolve_organization_object("IT", directory, target_types=(ORG_TARGET_DEPARTMENT, ORG_TARGET_GROUP))

    assert admin.resolved_type == ORG_TARGET_GROUP
    assert admin.resolved_department_id == "group_admin"
    assert admin.resolved_name == "行政组"
    assert admin.reason == "unit_suffix_match"
    assert it.resolved_type == ORG_TARGET_GROUP
    assert it.resolved_department_id == "group_it"
    assert it.resolved_name == "IT组"
    assert it.reason == "unit_suffix_match"


def test_resolve_department_members_includes_descendant_departments_and_dedupes_people() -> None:
    division_id = uuid4()
    product_id = uuid4()
    project_id = uuid4()
    leader_id = uuid4()
    engineer_id = uuid4()
    division = SimpleNamespace(
        id=division_id,
        company_id=COMPANY_ID,
        source_system="feishu",
        source_department_id="division_power",
        open_department_id="",
        parent_source_department_id="0",
        name="半导体事业部",
        normalized_name=normalize_organization_name("半导体事业部"),
        unit_type=ORG_TARGET_DEPARTMENT,
        status="active",
        path_names=[],
    )
    product = SimpleNamespace(
        id=product_id,
        company_id=COMPANY_ID,
        source_system="feishu",
        source_department_id="dept_product",
        open_department_id="",
        parent_source_department_id="division_power",
        name="产品部",
        normalized_name=normalize_organization_name("产品部"),
        unit_type=ORG_TARGET_DEPARTMENT,
        status="active",
        path_names=[],
    )
    project = SimpleNamespace(
        id=project_id,
        company_id=COMPANY_ID,
        source_system="feishu",
        source_department_id="dept_project",
        open_department_id="",
        parent_source_department_id="division_power",
        name="项目部",
        normalized_name=normalize_organization_name("项目部"),
        unit_type=ORG_TARGET_DEPARTMENT,
        status="active",
        path_names=[],
    )
    leader = SimpleNamespace(
        id=leader_id,
        open_id="ou_leader",
        source_user_id="",
        name="戴留兴",
        normalized_name=normalize_organization_name("戴留兴"),
        email="",
        mobile="",
        job_title="总经理",
        status="active",
        source_system="feishu",
    )
    engineer = SimpleNamespace(
        id=engineer_id,
        open_id="ou_engineer",
        source_user_id="",
        name="缪瀛",
        normalized_name=normalize_organization_name("缪瀛"),
        email="",
        mobile="",
        job_title="助理测试工程师",
        status="active",
        source_system="feishu",
    )
    rows = [
        (SimpleNamespace(organization_department_id=division_id, is_primary=True), leader),
        (SimpleNamespace(organization_department_id=project_id, is_primary=False), leader),
        (SimpleNamespace(organization_department_id=product_id, is_primary=True), engineer),
    ]
    db = _OrganizationSession(departments=[division, product, project], users=[leader, engineer], root=division, rows=rows)

    result = resolve_department_members(db, company_id=COMPANY_ID, query="半导体事业部有多少人")

    assert result is not None
    assert result.resolution.resolved_name == "半导体事业部"
    assert [item["name"] for item in result.items] == ["戴留兴", "缪瀛"]
    assert result.items[0]["department"] == "半导体事业部"
    assert result.items[1]["department"] == "产品部"


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
    assert resolution.reason == "unit_suffix_ambiguous"


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
