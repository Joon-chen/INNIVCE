from uuid import UUID

from sqlalchemy.orm import Session

from app.services.tools.evidence import wants_evidence_detail
from app.services.agent.policies import BotActor
from app.services.cockpit import (
    build_cockpit_module,
    build_cockpit_overview,
    build_scope,
    format_module_for_chat,
    format_overview_for_chat,
)


MODULE_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("risks", ("风险", "预警", "隐患", "异常", "逾期", "问题")),
    ("tasks", ("待办", "任务", "跟进", "事项", "todo")),
    ("approvals", ("审批", "付款", "报销", "请假", "合同", "采购", "同意", "拒绝", "单子")),
    ("projects", ("项目", "研发", "进度", "交付", "里程碑", "测试")),
    ("decisions", ("决策", "决定", "待决策", "拍板", "判断")),
    ("communications", ("消息", "邮件", "沟通", "客户", "群聊", "聊天")),
    ("meetings", ("会议", "日程", "纪要", "会")),
    ("resources", ("资源", "同步", "数据源", "飞书", "连接", "监控", "数据覆盖", "数据盲区", "盲区", "未接入", "关键群", "接入建议", "机器人入群")),
    ("reports", ("日报", "周报", "月报", "报告", "总结")),
    ("today-focus", ("今日", "今天", "重点", "概览", "经营")),
)

DATA_COVERAGE_TERMS = (
    "数据覆盖",
    "数据盲区",
    "盲区",
    "未接入",
    "没接入",
    "关键群",
    "接入建议",
    "机器人入群",
    "机器人加群",
)


def answer_company_question(
    db: Session,
    *,
    company_id: UUID,
    actor: BotActor,
    question: str,
    normalized_command: str,
    limit: int = 8,
) -> str:
    if not actor.can_query_company:
        return "这个入口需要公司级权限。我不会向非授权人员开放公司级数据。"

    scope = build_scope(all_companies=True) if actor.access_scope == "all" else build_scope(company_id=company_id)
    module_key = _module_key_for_question(question, normalized_command)
    if module_key:
        module = build_cockpit_module(db, key=module_key, scope=scope, limit=limit)
        if module_key == "resources" and not wants_evidence_detail(question):
            return _compact_resources_answer(module)
        if not wants_evidence_detail(question):
            return _compact_module_answer(module)
        return format_module_for_chat(module, max_items=6)

    overview = build_cockpit_overview(db, scope=scope, limit=5)
    if not wants_evidence_detail(question):
        return _compact_overview_answer(overview)
    return format_overview_for_chat(overview, max_modules=10)


def _module_key_for_question(question: str, normalized_command: str) -> str | None:
    text = f"{question} {normalized_command}".lower()
    module_hint = _module_hint(text)
    if module_hint:
        return module_hint
    if _is_data_coverage_question(text):
        return "resources"
    for key, terms in MODULE_TERMS:
        if any(term.lower() in text for term in terms):
            return key
    return None


def _module_hint(text: str) -> str | None:
    for key, _terms in MODULE_TERMS:
        if f"module:{key}" in text:
            return key
    return None


def _is_data_coverage_question(text: str) -> bool:
    return any(term.lower() in text for term in DATA_COVERAGE_TERMS)


def _compact_module_answer(module) -> str:
    lines = [
        module.name,
        module.summary,
    ]
    if module.items:
        lines.append("重点：")
        for item in module.items[:3]:
            parts = [item.title]
            if item.owner:
                parts.append(f"负责人：{item.owner}")
            if item.status:
                parts.append(f"状态：{item.status}")
            lines.append(f"- {' / '.join(parts)}")
    if module.next_actions:
        lines.append(f"建议：{module.next_actions[0]}")
    lines.append(f"依据：驾驶舱 {module.name} 模块，命中 {module.count} 条；回复“展开依据”可查看明细。")
    return "\n".join(lines)


def _compact_resources_answer(module) -> str:
    high_value_actions = _high_value_group_access_actions(module)
    lines = ["老板，当前数据盲区我按“是否影响经营判断”来筛：", f"- 总体：{module.summary}"]
    if high_value_actions:
        lines.append("- 关键群接入建议：以下关键群/资源可能影响数据完整性")
        for action in high_value_actions[:3]:
            for resource in action["resources"][:3]:
                recommendation = resource.get("access_recommendation") or {}
                target = recommendation.get("recommended_notify_target") or action.get("responsible_role") or "业务负责人"
                reason = recommendation.get("reason") or action.get("business_impact") or "该群可能影响经营数据完整性"
                name = resource.get("resource_name") or resource.get("resource_id") or "未命名群"
                label = recommendation.get("label") or "建议确认"
                lines.append(f"  {name}：{label}；确认人：{target}；原因：{reason}")
        lines.append("- 建议：先让业务负责人确认是否接入；确认后请群主邀请机器人，入群后系统会自动同步；如状态未恢复，可在驾驶舱点“重试并恢复”。")
    elif module.items:
        lines.append("- 结论：当前没有需要你亲自处理的高价值关键群接入建议。")
        lines.append("- 说明：仍有普通同步/资源动作，可在驾驶舱设置页查看，不建议你日常手工维护。")
    else:
        lines.append("- 结论：当前没有需要老板关注的数据覆盖动作。")
    lines.append(f"依据：驾驶舱 {module.name} 模块，命中 {module.count} 条；回复“展开依据”可查看明细。")
    return "\n".join(lines)


def _high_value_group_access_actions(module) -> list[dict]:
    actions = module.metrics.get("owner_actions", []) if isinstance(module.metrics, dict) else []
    result: list[dict] = []
    for action in actions:
        if action.get("action_code") != "invite_bot_to_chats":
            continue
        resources = [
            resource
            for resource in action.get("resources", [])
            if (resource.get("access_recommendation") or {}).get("label") in {"建议接入", "已确认接入"}
        ]
        if resources:
            result.append({**action, "resources": resources})
    return result


def _compact_overview_answer(overview) -> str:
    lines = ["老板，这是当前驾驶舱概览（简版）："]
    for module in overview.modules[:5]:
        lines.append(f"- {module.name}：{module.summary}")
    lines.append(f"依据：驾驶舱模块 {len(overview.modules)} 个；回复“展开依据”可查看明细。")
    return "\n".join(lines)
