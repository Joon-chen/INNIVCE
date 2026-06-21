from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.entities import ExtractedItem
from app.services.cockpit.schemas import CockpitItem, CockpitModuleResult
from app.services.cockpit.scope import CockpitScope
from app.services.cockpit.utils import display_extracted_title, list_extracted_items


CLOSED_STATUSES = {"closed", "done", "resolved", "completed", "cancelled", "canceled"}
HIGH_PRIORITIES = {"high", "urgent", "p0", "p1"}
OWNER_DECISION_TERMS = ("确认", "审批", "同意", "拒绝", "拍板", "决策", "金额", "预算", "付款", "合同")
LOW_SIGNAL_TERMS = (
    "欢迎使用",
    "使用指南",
    "帮助中心",
    "提醒",
    "规范",
    "模板",
    "通知",
    "请查阅",
    "同步完成",
    "写入或更新",
    "配置飞书 App",
    "配置飞书",
    "配置邮箱",
    "数字参谋系统启动",
)
SYSTEM_RECORD_TITLE_TERMS = (
    "欢迎使用",
    "使用指南",
    "帮助中心",
    "同步完成",
    "写入或更新",
    "基础配置完成",
    "基础配置已完成",
    "数字参谋系统启动",
    "待办事项",
)
STRONG_TERMS = ("逾期", "延期", "异常", "风险", "阻塞", "付款", "客户", "项目", "审批", "合同")


def build_tasks_module(db: Session, *, scope: CockpitScope, limit: int) -> CockpitModuleResult:
    items = list_extracted_items(db, scope=scope, item_type="task", limit=max(limit * 3, 30))
    open_items = [item for item in items if _is_open_task(item)]
    cockpit_items = _dedupe_task_items([_task_item(item) for item in open_items])
    owner_decisions = [item for item in cockpit_items if item.attention_label == "需要你决策"]
    assignee_actions = [item for item in cockpit_items if item.attention_label == "需要负责人推进"]
    system_records = [item for item in cockpit_items if item.attention_label == "系统记录"]
    actionable_items = [*owner_decisions, *assignee_actions]
    ordered = (actionable_items or system_records)[:limit]
    return CockpitModuleResult(
        key="tasks",
        name="待办事项",
        description="从工作事件中抽取出的任务、责任人和截止时间。",
        count=len(owner_decisions) + len(assignee_actions),
        summary=(
            f"当前开放待办 {len(open_items)} 条；"
            f"需要你决策 {len(owner_decisions)} 条、需要负责人推进 {len(assignee_actions)} 条、系统记录 {len(system_records)} 条。"
        ),
        items=ordered,
        next_actions=_task_next_actions(
            owner_decisions=len(owner_decisions),
            assignee_actions=len(assignee_actions),
            system_records=len(system_records),
        ),
        metrics={
            "owner_decision": len(owner_decisions),
            "assignee_action": len(assignee_actions),
            "system_record": len(system_records),
            "open": len(open_items),
            "total": len(items),
        },
    )


def _dedupe_task_items(items: list[CockpitItem]) -> list[CockpitItem]:
    seen: set[str] = set()
    result: list[CockpitItem] = []
    for item in items:
        key = f"{item.attention_label}:{item.title}:{item.owner or ''}:{item.due_at or ''}"
        if key in seen:
            continue
        seen = seen | {key}
        result.append(item)
    return result


def _is_open_task(item: ExtractedItem) -> bool:
    if str(item.status or "open").lower() in CLOSED_STATUSES:
        return False
    return True


def _task_item(item: ExtractedItem) -> CockpitItem:
    attention_level = _task_attention_level(item)
    return CockpitItem(
        id=str(item.id),
        title=display_extracted_title(item),
        attention_label=_task_attention_label(attention_level),
        subtitle=_task_subtitle(item, attention_level),
        description=_task_next_step(item, attention_level),
        status=item.status,
        priority=item.priority,
        owner=item.owner,
        due_at=item.due_at.isoformat() if item.due_at else None,
        occurred_at=item.created_at.isoformat() if item.created_at else None,
        payload={
            "task_attention_level": attention_level,
            "task_attention_label": _task_attention_label(attention_level),
            **(item.payload or {}),
        },
    )


def _task_attention_level(item: ExtractedItem) -> str:
    text = _task_text(item)
    if _is_low_signal_task(item):
        return "system_record"
    if str(item.priority or "").lower() in HIGH_PRIORITIES:
        return "owner_decision"
    if _is_due_soon(item):
        return "owner_decision"
    if any(term in text for term in OWNER_DECISION_TERMS) and not item.owner:
        return "owner_decision"
    if item.owner:
        return "assignee_action"
    return "assignee_action"


def _task_attention_label(level: str) -> str:
    labels = {
        "owner_decision": "需要你决策",
        "assignee_action": "需要负责人推进",
        "system_record": "系统记录",
    }
    return labels[level]


def _task_subtitle(item: ExtractedItem, level: str) -> str:
    if level == "owner_decision":
        if _is_due_soon(item):
            return "临近截止或优先级较高，需要老板确认是否升级处理。"
        return "缺少明确负责人或涉及审批/资金/合同，需要老板确认口径。"
    if level == "assignee_action":
        if item.owner:
            return f"已有负责人：{item.owner}，适合跟进进度。"
        return "需要指定负责人推进。"
    return "低信号待办，仅作为系统记录，不建议占用老板注意力。"


def _task_next_step(item: ExtractedItem, level: str) -> str:
    if level == "owner_decision":
        return "确认是否今天处理、是否需要升级，以及指定最终负责人和时限。"
    if level == "assignee_action":
        return "让负责人更新进度、阻塞点和预计完成时间。"
    return "默认不处理；后续如多次出现再归并分析。"


def _task_next_actions(*, owner_decisions: int, assignee_actions: int, system_records: int) -> list[str]:
    if owner_decisions:
        actions = [f"先处理 {owner_decisions} 条需要你决策的待办。"]
        if assignee_actions:
            actions.append(f"其余 {assignee_actions} 条交给负责人推进，系统记录项不需要你处理。")
        else:
            actions.append("当前没有需要负责人推进的待办；系统记录项不需要你处理。")
        return actions
    if assignee_actions:
        return [
            f"当前没有必须你决策的待办；有 {assignee_actions} 条需要负责人推进。",
            "建议只检查负责人、截止时间和是否存在阻塞。",
        ]
    if system_records:
        return [f"当前只有 {system_records} 条系统记录类待办，默认不需要你处理。"]
    return ["当前没有开放待办。"]


def _is_due_soon(item: ExtractedItem) -> bool:
    if not item.due_at:
        return False
    now = datetime.now(UTC)
    due_at = item.due_at if item.due_at.tzinfo else item.due_at.replace(tzinfo=UTC)
    return due_at <= now + timedelta(days=1)


def _is_low_signal_task(item: ExtractedItem) -> bool:
    text = _task_text(item)
    title = item.title or ""
    if any(term in title for term in SYSTEM_RECORD_TITLE_TERMS):
        return True
    if any(term in text for term in STRONG_TERMS):
        return False
    return any(term in text for term in LOW_SIGNAL_TERMS)


def _task_text(item: ExtractedItem) -> str:
    return " ".join([item.title or "", item.description or "", str(item.payload or "")])
