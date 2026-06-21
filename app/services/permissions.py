from dataclasses import dataclass, field
from typing import Any


DOMAIN_RULES: dict[str, dict[str, list[str]]] = {
    "finance": {
        "labels": ["财务", "资金", "付款", "报销", "薪资", "工资", "奖金", "回款", "finance"],
        "resources": ["财务数据", "付款审批", "报销审批", "薪资相关摘要"],
    },
    "sales": {
        "labels": ["销售", "商务", "客户", "订单", "合同", "回款", "市场", "sales"],
        "resources": ["客户信息", "销售群", "订单和合同摘要"],
    },
    "rd": {
        "labels": ["研发", "技术", "工程", "测试", "软件", "硬件", "产品", "项目", "rd", "r&d"],
        "resources": ["研发项目", "技术知识库", "测试和问题记录"],
    },
    "quality": {
        "labels": ["质量", "品质", "qa", "qc", "可靠性"],
        "resources": ["质量问题", "测试异常", "售后质量记录"],
    },
    "supply_chain": {
        "labels": ["采购", "供应链", "仓库", "物流", "物料", "计划", "供应商"],
        "resources": ["采购审批", "供应商和物料记录", "交付风险"],
    },
    "hr": {
        "labels": ["人事", "人力", "hr", "招聘", "入职", "离职", "绩效"],
        "resources": ["人事流程", "招聘和入离职摘要"],
    },
    "admin": {
        "labels": ["行政", "办公", "固定资产", "差旅", "会议室"],
        "resources": ["行政采购审批", "办公和行政流程"],
    },
    "approval": {
        "labels": ["审批", "申请", "同意", "拒绝", "流程"],
        "resources": ["本业务域相关审批"],
    },
}

COMPANY_ADMIN_TERMS = ["董事长", "总经理", "ceo", "coo", "总裁", "法人"]
DOMAIN_MANAGER_TERMS = ["负责人", "总监", "经理", "主管", "leader", "head", "director", "vp", "cfo", "cto"]


@dataclass(frozen=True)
class PermissionProfile:
    role: str
    access_scope: str
    domains: list[str] = field(default_factory=list)
    matched_rules: list[str] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)


def infer_permission_profile(
    *,
    display_name: str | None,
    job_title: str | None,
    department_names: list[str] | None = None,
    email: str | None = None,
) -> PermissionProfile:
    text = " ".join(
        item
        for item in [
            display_name or "",
            job_title or "",
            email or "",
            " ".join(department_names or []),
        ]
        if item
    ).lower()
    domains: list[str] = []
    matched_rules: list[str] = []
    resources: list[str] = []
    for domain, rule in DOMAIN_RULES.items():
        labels = rule["labels"]
        if any(label.lower() in text for label in labels):
            domains.append(domain)
            matched_rules.append(domain)
            resources.extend(rule["resources"])

    if any(term.lower() in text for term in COMPANY_ADMIN_TERMS):
        return PermissionProfile(
            role="admin",
            access_scope="company",
            domains=_unique(["all", *domains]),
            matched_rules=_unique(["company_admin", *matched_rules]),
            resources=_unique(["全公司工作事件", "全局日报", "跨部门风险", *resources]),
        )

    is_domain_manager = bool(domains) and any(term.lower() in text for term in DOMAIN_MANAGER_TERMS)
    if is_domain_manager:
        return PermissionProfile(
            role="manager",
            access_scope="domain",
            domains=_unique([*domains, "approval" if "finance" in domains else ""]),
            matched_rules=_unique(["domain_manager", *matched_rules]),
            resources=_unique(resources),
        )

    return PermissionProfile(
        role="member",
        access_scope="chat",
        domains=_unique(domains),
        matched_rules=_unique(matched_rules or ["default_member"]),
        resources=[],
    )


def merge_permission_settings(
    existing: dict[str, Any] | None,
    *,
    profile: PermissionProfile,
    source: str,
    department_ids: list[str] | None,
    department_names: list[str] | None,
    email: str | None,
    job_title: str | None,
    status: Any,
    synced_at: str,
) -> dict[str, Any]:
    current = dict(existing or {})
    current.update(
        {
            "source": source,
            "department_ids": department_ids or [],
            "department_names": department_names or [],
            "email": email,
            "job_title": job_title,
            "status": status,
            "permission_domains": profile.domains,
            "permission_rules": profile.matched_rules,
            "allowed_resources": profile.resources,
            "synced_at": synced_at,
        }
    )
    return current


def question_allowed_by_domains(question: str, domains: list[str]) -> bool:
    if "all" in domains:
        return True
    if not domains:
        return False
    lowered = question.lower()
    for domain in domains:
        rule = DOMAIN_RULES.get(domain)
        if not rule:
            continue
        if any(label.lower() in lowered for label in rule["labels"]):
            return True
    return False


def keywords_for_domains(domains: list[str]) -> list[str]:
    keywords: list[str] = []
    for domain in domains:
        rule = DOMAIN_RULES.get(domain)
        if rule:
            keywords.extend(rule["labels"])
    return _unique(keywords)


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
