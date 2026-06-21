from typing import Any

from app.services.v5_resource_governance import resource_access_decision


def owner_actions_from_resource_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for item in items:
        action_code = _owner_action_code(item)
        if not action_code:
            continue
        business_domain = _business_domain(item)
        responsible_role = _responsible_role(business_domain, action_code)
        group = groups.setdefault(action_code, _owner_action_template(action_code))
        group["count"] += 1
        group["business_domains"] = _append_unique(group["business_domains"], business_domain)
        group["responsible_roles"] = _append_unique(group["responsible_roles"], responsible_role)
        access_recommendation = _group_access_recommendation(item, business_domain=business_domain)
        _merge_group_access_recommendation(group, access_recommendation)
        group["business_impact"] = _merge_business_impact(
            group["business_impact"],
            _business_impact(item),
        )
        if len(group["resources"]) < 10:
            group["resources"].append(
                {
                    "resource_id": item["id"],
                    "resource_name": item.get("resource_name"),
                    "resource_type": item.get("resource_type"),
                    "status": item.get("status"),
                    "business_domain": business_domain,
                    "responsible_role": responsible_role,
                    "business_impact": _business_impact(item),
                    "owner_next_step": group["owner_next_step"],
                    "access_recommendation": access_recommendation,
                }
            )
    for group in groups.values():
        group["responsible_role"] = " / ".join(group["responsible_roles"])
        group["business_domain"] = " / ".join(group["business_domains"])
        group["affected_resources"] = _affected_resources(group["resources"])
        if group["affected_resources"]:
            group["owner_next_step_detail"] = f"{group['owner_next_step']} 涉及：{group['affected_resources']}。"
        else:
            group["owner_next_step_detail"] = group["owner_next_step"]
        group["access_recommendation_summary"] = _access_recommendation_summary(group)
    return sorted(groups.values(), key=lambda item: item["priority"])


def _owner_action_code(item: dict[str, Any]) -> str | None:
    if resource_access_decision(item) in {"ignore", "do_not_connect"}:
        return None
    governance = (item.get("config_json") or {}).get("governance") or {}
    governance_status = governance.get("status")
    identifier_issue = item.get("identifier_issue") or {}
    if governance_status == "bot_not_in_chat":
        return "invite_bot_to_chats"
    if governance_status == "requires_user_authorization" or identifier_issue.get("code") == "requires_user_authorization":
        return "complete_user_authorization"
    if identifier_issue.get("code") == "missing_required_identifier":
        return "complete_resource_identifiers"
    if item.get("status") == "policy_excluded":
        return "review_data_coverage_policy"
    if item.get("status") in {"failed", "partial"}:
        return "wait_or_review_data_refresh"
    return None


def _owner_action_template(action_code: str) -> dict[str, Any]:
    templates = {
        "invite_bot_to_chats": {
            "priority": 10,
            "title": "确认关键群是否接入数字助理",
            "owner_next_step": "先按建议等级确认是否为经营关键群；确认后再请业务负责人邀请机器人入群。",
            "system_behavior": "系统只生成接入建议，不自动通知群主；加入前不会反复读取这些群。",
            "business_impact": "高价值群里的项目、销售、研发或交付动态暂时不会进入日报、风险和问答。",
        },
        "complete_user_authorization": {
            "priority": 20,
            "title": "完成个人域授权",
            "owner_next_step": "按系统提示完成日历/会议等个人域授权，系统会继续自动发现可同步资源。",
            "system_behavior": "授权前系统不会读取个人域数据，避免越权。",
            "business_impact": "会议、日程、时间安排和相关待办判断可能不完整。",
        },
        "complete_resource_identifiers": {
            "priority": 30,
            "title": "检查资源自动发现",
            "owner_next_step": "确认飞书权限、事件订阅和机器人接入状态；资源标识由系统自动发现和登记。",
            "system_behavior": "未自动发现的数据源不会进入自动同步队列，会保留为资源盲区。",
            "business_impact": "相关客户、项目、审批、知识或文档索引可能不完整。",
        },
        "review_data_coverage_policy": {
            "priority": 40,
            "title": "确认是否纳入数据覆盖",
            "owner_next_step": "确认这些数据是否对经营分析有价值；有价值再纳入自动覆盖。",
            "system_behavior": "策略排除的数据不会自动进入驾驶舱。",
            "business_impact": "被排除的数据不会影响现有报告，但也不会被数字参谋参考。",
        },
        "wait_or_review_data_refresh": {
            "priority": 50,
            "title": "等待系统自动恢复或查看诊断",
            "owner_next_step": "通常不需要你处理；如果连续失败，再到设置页查看诊断。",
            "system_behavior": "系统会在下一轮自动同步中重试，不影响其他数据来源。",
            "business_impact": "相关分析可能不是最新，但不会阻断整体驾驶舱。",
        },
    }
    return {
        "action_code": action_code,
        "count": 0,
        "resources": [],
        "business_domains": [],
        "responsible_roles": [],
        "recommendation_counts": {},
        "recommendation_reasons": [],
        "recommended_notify_targets": [],
        **templates[action_code],
    }


def _group_access_recommendation(item: dict[str, Any], *, business_domain: str) -> dict[str, Any]:
    if _owner_action_code(item) != "invite_bot_to_chats":
        return {}
    access_decision = resource_access_decision(item)
    if access_decision == "business_group":
        return {
            "score": 90,
            "level": "high",
            "label": "建议接入",
            "reason": "已标记为业务群",
            "recommended_notify_target": _recommended_notify_target(business_domain, "high"),
            "suggested_action": "通知业务负责人确认后邀请机器人",
            "access_decision": access_decision,
        }
    if access_decision == "owner_confirmed":
        return {
            "score": 85,
            "level": "confirmed",
            "label": "已确认接入",
            "reason": "负责人已确认接入",
            "recommended_notify_target": _recommended_notify_target(business_domain, "high"),
            "suggested_action": "请群主邀请机器人，加入后点击重试并恢复",
            "access_decision": access_decision,
        }
    text = " ".join(
        str(item.get(key) or "")
        for key in ("resource_name", "resource_id", "resource_type", "next_action")
    ).lower()
    score = 10
    reasons: list[str] = []
    if business_domain in {"销售/客户", "财务/资金", "研发/技术", "项目/交付", "审批"}:
        score += 35
        reasons.append(f"归属{business_domain}经营域")
    business_terms = {
        "客户": 20,
        "销售": 20,
        "订单": 20,
        "合同": 20,
        "回款": 20,
        "项目": 20,
        "交付": 20,
        "现场": 15,
        "研发": 20,
        "技术": 15,
        "测试": 15,
        "质量": 15,
        "财务": 20,
        "付款": 20,
        "报销": 15,
        "采购": 15,
        "售后": 15,
    }
    for term, points in business_terms.items():
        if term in text:
            score += points
            reasons.append(f"名称或上下文包含“{term}”")
            break
    if "群" in text or "chat" in text:
        score += 5
    low_value_terms = ("闲聊", "茶水", "摸鱼", "福利", "团建", "红包", "临时", "测试群")
    if any(term in text for term in low_value_terms):
        score -= 35
        reasons.append("疑似低经营价值或临时群")
    level, label, action = _recommendation_level(score)
    if not reasons:
        reasons.append("缺少明确业务信号，先人工确认")
    return {
        "score": max(score, 0),
        "level": level,
        "label": label,
        "reason": "；".join(dict.fromkeys(reasons[:3])),
        "recommended_notify_target": _recommended_notify_target(business_domain, level),
        "suggested_action": action,
        "access_decision": access_decision,
    }


def _recommendation_level(score: int) -> tuple[str, str, str]:
    if score >= 65:
        return "high", "建议接入", "通知业务负责人确认后邀请机器人"
    if score >= 40:
        return "medium", "人工确认", "先确认是否为经营关键群"
    if score >= 20:
        return "low", "暂不处理", "暂不通知，后续有业务信号再处理"
    return "blocked", "不建议接入", "不通知，除非老板手动标记为业务群"


def _recommended_notify_target(business_domain: str, level: str) -> str:
    if level in {"low", "blocked"}:
        return "不通知"
    targets = {
        "销售/客户": "销售负责人",
        "财务/资金": "财务负责人",
        "研发/技术": "研发负责人",
        "项目/交付": "项目负责人",
        "审批": "审批管理员",
        "会议/日程": "相关负责人",
    }
    return targets.get(business_domain, "业务负责人先确认")


def _merge_group_access_recommendation(group: dict[str, Any], recommendation: dict[str, Any]) -> None:
    if not recommendation:
        return
    label = str(recommendation.get("label") or "未知")
    group["recommendation_counts"][label] = group["recommendation_counts"].get(label, 0) + 1
    reason = str(recommendation.get("reason") or "")
    if reason and reason not in group["recommendation_reasons"]:
        group["recommendation_reasons"].append(reason)
    target = str(recommendation.get("recommended_notify_target") or "")
    if target and target not in group["recommended_notify_targets"]:
        group["recommended_notify_targets"].append(target)


def _access_recommendation_summary(group: dict[str, Any]) -> str:
    counts = group.get("recommendation_counts") or {}
    if not counts:
        return ""
    return " / ".join(f"{label} {count} 个" for label, count in counts.items())


def _business_impact(item: dict[str, Any]) -> str:
    governance = (item.get("config_json") or {}).get("governance") or {}
    status = governance.get("status")
    name = item.get("resource_name") or item.get("resource_id") or "该数据来源"
    if status == "bot_not_in_chat":
        return f"系统暂时看不到「{name}」群消息，相关经营动态不会进入日报、风险和问答。"
    if status == "requires_user_authorization":
        return f"系统暂时看不到「{name}」日程，会议、待办和时间安排判断可能不完整。"
    if item.get("status") == "policy_excluded":
        return f"「{name}」当前不纳入自动覆盖，数字参谋默认不会参考它。"
    if item.get("status") in {"failed", "partial"}:
        return f"「{name}」最近更新不完整，相关分析可能不是最新。"
    return "该数据来源暂时不可见，可能影响驾驶舱分析完整性。"


def _business_domain(item: dict[str, Any]) -> str:
    text = " ".join(
        str(item.get(key) or "")
        for key in ("resource_name", "resource_type", "resource_id", "next_action")
    ).lower()
    if any(term in text for term in ("销售", "客户", "订单", "合同", "回款", "sales", "customer", "order")):
        return "销售/客户"
    if any(term in text for term in ("财务", "付款", "报销", "薪资", "工资", "奖金", "finance", "mailbox")):
        return "财务/资金"
    if any(term in text for term in ("研发", "技术", "测试", "质量", "rd", "r&d", "engineering")):
        return "研发/技术"
    if any(term in text for term in ("项目", "交付", "现场", "project", "delivery")):
        return "项目/交付"
    if any(term in text for term in ("审批", "approval")):
        return "审批"
    if any(term in text for term in ("日历", "会议", "calendar", "meeting")):
        return "会议/日程"
    if any(term in text for term in ("通讯录", "组织", "部门", "directory", "contact")):
        return "组织/人员"
    if any(term in text for term in ("文档", "知识", "wiki", "doc")):
        return "知识/文档"
    if any(term in text for term in ("chat", "群")):
        return "群聊/协同"
    return "经营数据"


def _responsible_role(business_domain: str, action_code: str) -> str:
    if action_code in {"complete_user_authorization", "complete_resource_identifiers"}:
        return "系统管理员"
    if business_domain == "销售/客户":
        return "销售负责人"
    if business_domain == "财务/资金":
        return "财务负责人"
    if business_domain == "研发/技术":
        return "研发负责人"
    if business_domain == "项目/交付":
        return "项目负责人"
    if business_domain == "会议/日程":
        return "相关负责人"
    return "系统管理员"


def _merge_business_impact(current: str, new_value: str) -> str:
    if not current:
        return new_value
    if new_value in current:
        return current
    return current


def _append_unique(values: list[str], value: str) -> list[str]:
    if value and value not in values:
        values.append(value)
    return values


def _affected_resources(resources: list[dict[str, Any]]) -> str:
    names = []
    for resource in resources[:5]:
        name = resource.get("resource_name") or resource.get("resource_id")
        if name and name not in names:
            names.append(str(name))
    suffix = "等" if len(resources) > len(names) else ""
    return "、".join(names) + suffix if names else ""
