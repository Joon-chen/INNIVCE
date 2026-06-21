from app.services.cockpit.schemas import CockpitModuleResult, CockpitOverview


def format_module_for_chat(module: CockpitModuleResult, *, max_items: int = 6) -> str:
    lines = [
        f"{module.name}",
        module.summary,
    ]
    if module.items:
        lines.append("")
        lines.append("重点明细：")
        for item in module.items[:max_items]:
            parts = [item.title]
            if item.owner:
                parts.append(f"负责人：{item.owner}")
            if item.priority:
                parts.append(f"优先级：{item.priority}")
            if item.status:
                parts.append(f"状态：{item.status}")
            lines.append(f"- {' / '.join(parts)}")
            if item.description:
                lines.append(f"  {item.description}")
    if module.next_actions:
        lines.append("")
        lines.append("建议动作：")
        lines.extend(f"- {action}" for action in module.next_actions[:3])
    return "\n".join(lines)


def format_overview_for_chat(overview: CockpitOverview, *, max_modules: int = 10) -> str:
    lines = ["老板，这是当前驾驶舱概览："]
    for module in overview.modules[:max_modules]:
        lines.append(f"- {module.name}：{module.summary}")
    lines.append("")
    lines.append("你可以继续问：今日重点、风险预警、待办事项、审批动态、项目动态、决策事项、消息邮件、会议日程、数据覆盖、报告中心。")
    return "\n".join(lines)
