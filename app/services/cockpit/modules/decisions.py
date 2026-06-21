from sqlalchemy.orm import Session

from app.models.entities import ExtractedItem
from app.services.cockpit.schemas import CockpitItem, CockpitModuleResult
from app.services.cockpit.scope import CockpitScope
from app.services.cockpit.utils import display_extracted_title, list_extracted_items


CLOSED_STATUSES = {"closed", "done", "resolved", "completed", "cancelled", "canceled"}
HIGH_PRIORITIES = {"high", "urgent", "p0", "p1"}
OWNER_DECISION_TERMS = (
    "拍板",
    "决策",
    "同意",
    "拒绝",
    "预算",
    "付款",
    "合同",
    "报价",
    "客户承诺",
    "组织调整",
    "奖金",
    "录用通知书",
)
EVIDENCE_TERMS = ("依据", "数据", "材料", "附件", "明细", "背景", "补充", "测算", "方案")
STRONG_DECISION_TERMS = OWNER_DECISION_TERMS + ("立项", "任命", "晋升", "薪酬")
LOW_SIGNAL_TERMS = (
    "欢迎使用",
    "使用指南",
    "帮助中心",
    "同步完成",
    "写入或更新",
    "基础配置完成",
    "数字参谋系统启动",
    "成功传达",
    "及时回复",
    "收到邮件后",
    "邮件回执",
)


def build_decisions_module(db: Session, *, scope: CockpitScope, limit: int) -> CockpitModuleResult:
    items = list_extracted_items(db, scope=scope, item_type="decision", limit=max(limit * 3, 30))
    open_items = [item for item in items if _is_open_decision(item)]
    cockpit_items = _dedupe_decision_items([_decision_item(item) for item in open_items])
    owner_decisions = [item for item in cockpit_items if item.attention_label == "需要老板拍板"]
    evidence_needed = [item for item in cockpit_items if item.attention_label == "需要补充依据"]
    records = [item for item in cockpit_items if item.attention_label == "已沉淀记录"]
    actionable_items = [*owner_decisions, *evidence_needed]
    ordered = (actionable_items or records)[:limit]
    return CockpitModuleResult(
        key="decisions",
        name="决策事项",
        description="需要老板确认、拍板或复盘的决策线索。",
        count=len(owner_decisions) + len(evidence_needed),
        summary=(
            f"当前开放决策 {len(open_items)} 条；"
            f"需要老板拍板 {len(owner_decisions)} 条、需要补充依据 {len(evidence_needed)} 条、已沉淀记录 {len(records)} 条。"
        ),
        items=ordered,
        next_actions=_decision_next_actions(
            owner_decisions=len(owner_decisions),
            evidence_needed=len(evidence_needed),
            records=len(records),
        ),
        metrics={
            "owner_final_decision": len(owner_decisions),
            "evidence_needed": len(evidence_needed),
            "decision_record": len(records),
            "open": len(open_items),
            "total": len(items),
        },
    )


def _dedupe_decision_items(items: list[CockpitItem]) -> list[CockpitItem]:
    seen: set[str] = set()
    result: list[CockpitItem] = []
    for item in items:
        key = f"{item.attention_label}:{_normalized_decision_title(item.title)}:{item.owner or ''}"
        if key in seen:
            continue
        seen = seen | {key}
        result.append(item)
    return result


def _is_open_decision(item: ExtractedItem) -> bool:
    return str(item.status or "open").lower() not in CLOSED_STATUSES


def _decision_item(item: ExtractedItem) -> CockpitItem:
    level = _decision_attention_level(item)
    return CockpitItem(
        id=str(item.id),
        title=display_extracted_title(item),
        attention_label=_decision_attention_label(level),
        subtitle=_decision_subtitle(item, level),
        description=_decision_next_step(item, level),
        status=item.status,
        priority=item.priority,
        owner=item.owner,
        due_at=item.due_at.isoformat() if item.due_at else None,
        occurred_at=item.created_at.isoformat() if item.created_at else None,
        payload={
            "decision_attention_level": level,
            "decision_attention_label": _decision_attention_label(level),
            **(item.payload or {}),
        },
    )


def _decision_attention_level(item: ExtractedItem) -> str:
    text = _decision_text(item)
    if _is_low_signal_decision(item):
        return "decision_record"
    if str(item.priority or "").lower() in HIGH_PRIORITIES:
        return "owner_final_decision"
    if any(term in text for term in OWNER_DECISION_TERMS):
        return "owner_final_decision"
    if any(term in text for term in EVIDENCE_TERMS):
        return "evidence_needed"
    return "evidence_needed"


def _decision_attention_label(level: str) -> str:
    labels = {
        "owner_final_decision": "需要老板拍板",
        "evidence_needed": "需要补充依据",
        "decision_record": "已沉淀记录",
    }
    return labels[level]


def _decision_subtitle(item: ExtractedItem, level: str) -> str:
    if level == "owner_final_decision":
        return "涉及资金、客户承诺、合同、预算或方向取舍，需要老板明确结论。"
    if level == "evidence_needed":
        return "已有决策线索，但依据、数据、负责人或背景仍需补齐。"
    return "低信号或历史决策记录，仅保留为上下文。"


def _decision_next_step(item: ExtractedItem, level: str) -> str:
    suggested_action = (item.payload or {}).get("suggested_action") if isinstance(item.payload, dict) else None
    if suggested_action:
        return str(suggested_action)
    if level == "owner_final_decision":
        return "补齐关键依据后，明确同意、拒绝、延期或指定负责人继续推进。"
    if level == "evidence_needed":
        return "要求责任人补充数据、方案、影响范围和建议选项。"
    return "默认不处理，后续用于复盘和报告引用。"


def _decision_next_actions(*, owner_decisions: int, evidence_needed: int, records: int) -> list[str]:
    if owner_decisions:
        actions = [f"先处理 {owner_decisions} 条需要老板拍板的决策。"]
        if evidence_needed:
            actions.append(f"{evidence_needed} 条先要求补充依据，不要直接拍板。")
        return actions
    if evidence_needed:
        return [
            f"当前没有必须立即拍板的决策；有 {evidence_needed} 条需要补充依据。",
            "建议让责任人补齐数据、影响范围和备选方案。",
        ]
    if records:
        return [f"当前只有 {records} 条已沉淀决策记录，默认不需要老板处理。"]
    return ["当前没有开放决策事项。"]


def _is_low_signal_decision(item: ExtractedItem) -> bool:
    text = _decision_text(item)
    if not any(term in text for term in LOW_SIGNAL_TERMS):
        return False
    return not any(term in text for term in STRONG_DECISION_TERMS)


def _decision_text(item: ExtractedItem) -> str:
    return " ".join([item.title or "", item.description or "", str(item.payload or "")])


def _normalized_decision_title(title: str) -> str:
    value = str(title or "").strip()
    for prefix in ("回复：", "转发：", "Re:", "RE:", "Fw:", "FW:"):
        if value.startswith(prefix):
            value = value[len(prefix) :].strip()
    return value
