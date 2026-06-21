from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Resource, ResourceSyncRun
from app.services.cockpit.schemas import CockpitItem, CockpitModuleResult
from app.services.cockpit.scope import CockpitScope
from app.services.cockpit.utils import item_to_cockpit_item, list_extracted_items, scope_filter
from app.services.v5_owner_actions import owner_actions_from_resource_items


HIGH_PRIORITIES = {"high", "urgent", "p0", "p1"}
CLOSED_STATUSES = {"closed", "done", "resolved", "completed"}


def build_risks_module(db: Session, *, scope: CockpitScope, limit: int) -> CockpitModuleResult:
    items = list_extracted_items(db, scope=scope, item_type="risk", limit=limit)
    open_items = [item for item in items if _is_open_business_risk(item)]
    high_items = [item for item in open_items if str(item.priority or "").lower() in HIGH_PRIORITIES]
    coverage_risks = _data_coverage_risk_items(db, scope=scope, limit=limit)
    display_items = high_items or open_items
    return CockpitModuleResult(
        key="risks",
        name="风险预警",
        description="从消息、邮件、审批、会议中抽取出的经营和执行风险。",
        count=len(open_items) + len(coverage_risks),
        summary=f"{_risk_summary(open_items=open_items, high_items=high_items, coverage_risks=coverage_risks)}",
        items=coverage_risks + [item_to_cockpit_item(item) for item in display_items[:limit]],
        next_actions=[
            "先看高优先级风险，再确认负责人和处理时限。",
            "对没有来源说明的风险，回到原工作事件核实上下文。",
        ],
        metrics={"total": len(items), "open": len(open_items), "high": len(high_items), "data_coverage_risks": len(coverage_risks)},
    )


def _is_open_business_risk(item: object) -> bool:
    if str(getattr(item, "status", None) or "open").lower() in CLOSED_STATUSES:
        return False
    return not _is_low_signal_risk(item)


def _is_low_signal_risk(item: object) -> bool:
    text = " ".join(
        [
            str(getattr(item, "title", None) or ""),
            str(getattr(item, "description", None) or ""),
            str(getattr(item, "payload", None) or ""),
        ]
    )
    normalized = text
    for neutral_phrase in ("有问题随时沟通", "如有问题请", "有问题请", "如有疑问", "若有疑问"):
        normalized = normalized.replace(neutral_phrase, "")
    finance_doc_terms = ("工资明细", "薪资明细", "工资汇总", "薪资汇总", "工资总表", "薪资总表")
    neutral_terms = ("附件", "汇总表", "请核对", "请查收", "明细", "总表")
    risk_terms = ("异常", "差异", "错误", "逾期", "未发", "未付", "拖欠", "投诉", "风险", "问题")
    return (
        any(term in text for term in finance_doc_terms)
        and any(term in text for term in neutral_terms)
        and not any(term in normalized for term in risk_terms)
    ) or _is_low_signal_notice(text)


def _is_low_signal_notice(text: str) -> bool:
    headline = text.strip()[:240]
    strong_terms = ("异常", "差异", "错误", "逾期", "未付", "拖欠", "投诉", "风险", "阻塞", "延期", "违约", "事故")
    business_terms = ("付款", "报销", "合同", "采购", "回款", "客户", "订单", "项目", "审批")
    notice_terms = ("通知", "提醒", "规范", "模板", "指引", "录用通知书", "请查阅", "欢迎使用", "使用指南", "帮助中心")
    if not any(term in headline for term in notice_terms):
        return False
    if any(term in headline for term in strong_terms):
        return False
    return not (any(term in headline for term in business_terms) and "录用通知书" not in headline)


def _risk_summary(*, open_items: list[object], high_items: list[object], coverage_risks: list[CockpitItem]) -> str:
    if not open_items and not coverage_risks:
        return "当前没有开放风险或数据盲区处理动作。"
    return f"当前开放风险 {len(open_items)} 条，高优先级 {len(high_items)} 条；数据盲区处理动作 {len(coverage_risks)} 项。"


def _data_coverage_risk_items(db: Session, *, scope: CockpitScope, limit: int) -> list[CockpitItem]:
    blocked_resources = _blocked_resources(db, scope=scope, limit=limit)
    blocked_ids = {item.id for item in blocked_resources}
    query = (
        select(ResourceSyncRun)
        .where(ResourceSyncRun.status.in_(["failed", "partial", "skipped"]))
        .where(ResourceSyncRun.resource.has(Resource.enabled.is_(True)))
        .order_by(ResourceSyncRun.started_at.desc())
        .limit(min(limit, 5))
    )
    query = scope_filter(query, ResourceSyncRun, scope)
    if blocked_ids:
        query = query.where(ResourceSyncRun.resource_id.notin_(blocked_ids))
    issue_runs = list(db.scalars(query).all())
    owner_actions = owner_actions_from_resource_items(
        [_resource_action_payload(item) for item in blocked_resources]
        + [_sync_run_action_payload(item) for item in issue_runs]
    )
    owner_action_items = [
        CockpitItem(
            id=item["action_code"],
            title=f"数据盲区：{_owner_action_title(item)}",
            subtitle=item["business_impact"],
            description=item.get("owner_next_step_detail") or item["owner_next_step"],
            status="待处理",
            priority="high",
            owner=item.get("responsible_role") or "系统管理员",
            payload={
                "count": item["count"],
                "business_domain": item.get("business_domain"),
                "responsible_role": item.get("responsible_role"),
                "system_behavior": item["system_behavior"],
                "resources": item["resources"],
            },
        )
        for item in owner_actions[:limit]
    ]
    return owner_action_items


def _blocked_resources(db: Session, *, scope: CockpitScope, limit: int) -> list[Resource]:
    query = select(Resource).where(Resource.enabled.is_(True)).order_by(Resource.updated_at.desc()).limit(limit)
    query = scope_filter(query, Resource, scope)
    return [resource for resource in db.scalars(query).all() if _governance_status(resource)]


def _owner_action_title(item: dict) -> str:
    count = int(item.get("count") or 0)
    return f"{item['title']}（{count}项）" if count else item["title"]


def _governance(resource: Resource) -> dict:
    config = resource.config_json if isinstance(resource.config_json, dict) else {}
    governance = config.get("governance") or {}
    return governance if isinstance(governance, dict) else {}


def _governance_status(resource: Resource) -> str | None:
    status = _governance(resource).get("status")
    return str(status) if status else None


def _governance_reason(resource: Resource) -> str | None:
    reason = _governance(resource).get("reason")
    return str(reason) if reason else None


def _business_impact_reason(resource: Resource) -> str:
    status = _governance_status(resource)
    name = resource.resource_name or resource.resource_id
    if status == "bot_not_in_chat":
        return f"系统暂时看不到「{name}」群消息，会影响相关经营判断。"
    if status == "requires_user_authorization":
        return f"系统暂时看不到「{name}」日程，会影响会议和待办判断。"
    return _governance_reason(resource) or "该数据来源暂时不可见，会影响分析完整性。"


def _resource_action_payload(resource: Resource) -> dict:
    return {
        "id": str(resource.id),
        "resource_name": resource.resource_name,
        "resource_id": resource.resource_id,
        "resource_type": resource.resource_type,
        "status": _governance_status(resource),
        "config_json": {"governance": _governance(resource)},
    }


def _sync_run_action_payload(run: ResourceSyncRun) -> dict:
    return {
        "id": str(run.resource_id),
        "resource_name": str(run.resource_id),
        "resource_id": str(run.resource_id),
        "resource_type": "unknown",
        "status": run.status,
        "config_json": {},
    }
