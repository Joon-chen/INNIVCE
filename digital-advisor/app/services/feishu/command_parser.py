import json
from typing import Any

from app.services.agent.intents import classify_bot_intent
from app.services.feishu.approval_cards import approval_card_action_value
from app.services.feishu.client import _extract_message_text


def get_chat_id(payload: dict[str, Any]) -> str | None:
    event = payload.get("event") or payload
    message = event.get("message") or {}
    return message.get("chat_id") or event.get("chat_id")


def get_sender_open_id(payload: dict[str, Any]) -> str | None:
    event = payload.get("event") or payload
    sender = event.get("sender") or {}
    sender_id = sender.get("sender_id") or sender.get("id") or {}
    if isinstance(sender_id, dict):
        open_id = sender_id.get("open_id")
        if open_id:
            return open_id
    operator = event.get("operator") if isinstance(event.get("operator"), dict) else {}
    operator_id = operator.get("operator_id") or operator.get("id") or {}
    if isinstance(operator_id, dict):
        open_id = operator_id.get("open_id") or operator_id.get("user_id")
        if open_id:
            return open_id
    user_id = event.get("user_id") if isinstance(event.get("user_id"), dict) else {}
    open_id = operator.get("open_id") or operator.get("user_id") or user_id.get("open_id") or user_id.get("user_id")
    if open_id:
        return open_id
    value = approval_card_action_value(payload)
    actor_open_id = value.get("actor_open_id") if isinstance(value, dict) else None
    return str(actor_open_id) if actor_open_id else None


def is_app_message(payload: dict[str, Any]) -> bool:
    event = payload.get("event") or payload
    sender = event.get("sender") or {}
    if sender.get("sender_type") == "app":
        return True
    message = event.get("message") or {}
    message_sender = message.get("sender") or {}
    return message_sender.get("sender_type") == "app"


def should_reply_to_message(app_config: Any, payload: dict[str, Any]) -> bool:
    if not is_group_chat_message(payload):
        return True
    return message_mentions_bot(app_config, payload)


def is_group_chat_message(payload: dict[str, Any]) -> bool:
    event = payload.get("event") or payload
    message = event.get("message") or {}
    chat_type = str(message.get("chat_type") or event.get("chat_type") or "").lower()
    if chat_type:
        return chat_type not in {"p2p", "private", "single", "direct"}
    chat_id = str(message.get("chat_id") or event.get("chat_id") or "")
    return chat_id.startswith("oc_")


def message_mentions_bot(app_config: Any, payload: dict[str, Any]) -> bool:
    event = payload.get("event") or payload
    message = event.get("message") or {}
    mentions = message.get("mentions") or event.get("mentions") or []
    if not isinstance(mentions, list):
        return False
    app_ids = {app_config.app_id}
    bot_names = {"大飞哥", str(app_config.name or "").strip()}
    for mention in mentions:
        if not isinstance(mention, dict):
            continue
        mention_id = mention.get("id") if isinstance(mention.get("id"), dict) else {}
        candidates = {
            str(mention.get("app_id") or ""),
            str(mention.get("bot_id") or ""),
            str(mention.get("name") or ""),
            str(mention.get("key") or ""),
            str(mention_id.get("app_id") or ""),
            str(mention_id.get("open_id") or ""),
            str((mention.get("name") or "").replace("@", "")),
        }
        if app_ids & candidates:
            return True
        if bot_names & candidates:
            return True
    return False


def extract_command_text(payload: dict[str, Any]) -> str | None:
    event = payload.get("event") or payload
    message = event.get("message") or {}
    raw_text = _extract_message_text(message) or event.get("text")
    if raw_text is None:
        return None
    if isinstance(raw_text, str):
        stripped = raw_text.strip()
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return stripped
        if isinstance(parsed, dict):
            text = parsed.get("text")
            return text.strip() if isinstance(text, str) else stripped
        return stripped
    return str(raw_text).strip()


def normalize_command(command: str) -> str:
    text = command.strip()
    for prefix in ("/", "／"):
        if text.startswith(prefix):
            text = text[1:].strip()
    compact = "".join(text.split())
    aliases = {
        "help": "帮助",
        "帮助": "帮助",
        "指令": "帮助",
        "我是你的主人": "身份确认",
        "我是主人": "身份确认",
        "我是企业老板": "身份确认",
        "我是公司老板": "身份确认",
        "我是老板": "身份确认",
        "我是固势老板": "身份确认",
        "我是固势的老板": "身份确认",
        "你忘记我是老板了": "身份确认",
        "你忘了我是老板": "身份确认",
        "你忘记我是企业老板了": "身份确认",
        "你知道我是谁吗": "身份确认",
        "我是谁": "身份确认",
        "身份": "身份确认",
        "我的权限": "身份确认",
        "你是什么身份": "机器人身份",
        "你是谁": "机器人身份",
        "你是谁?": "机器人身份",
        "你是谁？": "机器人身份",
        "大飞哥好呀": "寒暄",
        "大飞哥你好": "寒暄",
        "你好": "寒暄",
        "你好呀": "寒暄",
        "老板你好": "寒暄",
        "不理我了呢": "在线状态",
        "不理我了": "在线状态",
        "怎么不回复": "在线状态",
        "怎么没回复": "在线状态",
        "什么情况": "在线状态",
        "在吗": "在线状态",
        "你在线吗": "在线状态",
        "现在你是离线的吗": "在线状态",
        "你离线了吗": "在线状态",
        "在线吗": "在线状态",
        "离线了吗": "在线状态",
        "我能查看邮件吗": "邮件能力",
        "可以查看邮件吗": "邮件能力",
        "你能查看邮件吗": "邮件能力",
        "今日日报": "今日日报",
        "日报": "今日日报",
        "今天日报": "今日日报",
        "同步邮箱": "同步邮箱",
        "同步邮件": "同步邮箱",
        "收邮件": "同步邮箱",
        "最近邮件": "最近邮件",
        "最新邮件": "最近邮件",
        "查看最近一封邮件": "最近一封邮件",
        "最近一封邮件": "最近一封邮件",
        "最新一封邮件": "最近一封邮件",
        "查最近一封邮件": "最近一封邮件",
        "查看最新一封邮件": "最近一封邮件",
        "待办事项": "待办事项",
        "待办": "待办事项",
        "任务": "待办事项",
        "同步审批": "同步审批",
        "刷新审批": "同步审批",
        "审批同步": "同步审批",
        "同步通讯录": "同步通讯录",
        "刷新通讯录": "同步通讯录",
        "通讯录同步": "同步通讯录",
        "最近审批": "最近审批",
        "最新审批": "最近审批",
        "查看审批": "最近审批",
        "审批": "最近审批",
        "确认通过": "确认审批通过",
        "确认同意": "确认审批通过",
        "确认审批通过": "确认审批通过",
        "确认拒绝": "确认审批拒绝",
        "确认驳回": "确认审批拒绝",
        "取消": "取消审批操作",
        "取消审批": "取消审批操作",
        "取消审批操作": "取消审批操作",
        "驾驶舱": "驾驶舱概览",
        "经营概览": "驾驶舱概览",
        "老板驾驶舱": "驾驶舱概览",
        "今天有什么重点": "驾驶舱今日重点",
        "今天公司有什么重点": "驾驶舱今日重点",
        "今日重点": "驾驶舱今日重点",
        "风险预警": "驾驶舱风险",
        "风险中心": "驾驶舱风险",
        "审批动态": "驾驶舱审批",
        "项目动态": "驾驶舱项目",
        "决策事项": "驾驶舱决策",
        "消息邮件": "驾驶舱沟通",
        "会议日程": "驾驶舱会议",
        "资源同步": "驾驶舱资源",
        "报告中心": "驾驶舱报告",
    }
    if compact in aliases:
        return aliases[compact]
    if is_online_status_question(compact):
        return "在线状态"
    if is_owner_identity_statement(compact):
        return "身份确认"
    if is_executive_lookup_question(compact):
        return "管理人员查询"
    if is_approval_advice_question(compact):
        return "审批建议"
    if is_approval_detail_request(compact):
        return "审批详情请求"
    if is_approval_approve_request(compact):
        return "审批通过请求"
    if is_approval_reject_request(compact):
        return "审批拒绝请求"
    if is_pending_approval_question(compact):
        return "最近审批"
    if is_organization_outline_question(compact):
        return "组织架构"
    intent = classify_bot_intent(compact)
    if intent.canonical_command:
        return intent.canonical_command
    return compact


def normalize_command_with_context(command: str, normalized: str, *, has_approval_context: bool) -> str:
    compact = "".join(command.strip().split())
    if normalized in {
        "审批详情请求",
        "审批通过请求",
        "审批拒绝请求",
        "审批建议",
        "确认审批通过",
        "确认审批拒绝",
        "取消审批操作",
    }:
        return normalized
    if not has_approval_context:
        return normalized
    if is_contextual_approval_detail(compact):
        return "审批详情请求"
    if is_contextual_approval_approve(compact):
        return "审批通过请求"
    if is_contextual_approval_reject(compact):
        return "审批拒绝请求"
    if is_contextual_approval_advice(compact):
        return "审批建议"
    return normalized


def is_contextual_approval_detail(text: str) -> bool:
    return any(word in text for word in ["展开", "详情", "明细", "看看", "看一下", "它呢", "这个呢", "为什么"])


def is_contextual_approval_approve(text: str) -> bool:
    if any(word in text for word in ["建议", "拒绝", "驳回", "为什么"]):
        return False
    return any(word in text for word in ["通过它", "通过这个", "同意它", "同意这个", "帮我通过", "可以通过"])


def is_contextual_approval_reject(text: str) -> bool:
    if any(word in text for word in ["建议", "通过", "同意", "为什么"]):
        return False
    return any(word in text for word in ["拒绝它", "拒绝这个", "驳回它", "驳回这个", "帮我拒绝", "帮我驳回"])


def is_contextual_approval_advice(text: str) -> bool:
    return any(word in text for word in ["建议", "该不该", "能不能", "同意还是拒绝", "通过还是拒绝"])


def is_online_status_question(text: str) -> bool:
    return any(word in text for word in ["在线", "离线", "掉线", "还在吗", "在不在"])


def is_owner_identity_statement(text: str) -> bool:
    return "我是" in text and any(word in text for word in ["老板", "负责人", "法人", "董事长", "总经理"])


def is_executive_lookup_question(text: str) -> bool:
    if not any(word in text for word in ["谁", "是谁", "哪位", "叫什么"]):
        return False
    return any(word in text for word in ["总经理", "董事长", "负责人", "法人", "财务负责人", "销售负责人", "研发负责人"])


def is_pending_approval_question(text: str) -> bool:
    approval_subject = any(word in text for word in ["审批", "批复", "批准", "审核", "审的单", "单子"])
    if not approval_subject:
        return False
    return any(
        word in text
        for word in [
            "未审批",
            "待审批",
            "待审",
            "未审",
            "未处理",
            "待处理",
            "待我",
            "待你",
            "需要我",
            "要我",
            "我批",
            "我审",
            "我处理",
            "给我批",
            "给我审",
            "有没有",
            "是否有",
            "哪些",
        ]
    )


def is_approval_advice_question(text: str) -> bool:
    if "同意" in text and "拒绝" in text and any(word in text for word in ["建议", "你看", "帮我看", "该"]):
        return True
    if not any(word in text for word in ["审批", "单子", "这笔", "这个"]):
        return False
    return ("同意" in text and "拒绝" in text) or any(word in text for word in ["建议", "该不该", "是否通过", "能不能通过"])


def is_approval_detail_request(text: str) -> bool:
    return any(word in text for word in ["展开", "详情", "明细", "附件"]) and any(
        word in text for word in ["第", "审批", "单子", "这笔", "这个"]
    )


def is_approval_approve_request(text: str) -> bool:
    if any(word in text for word in ["建议", "还是", "拒绝", "驳回"]):
        return False
    return any(
        word in text
        for word in ["帮我审批通过", "帮我通过", "审批通过", "同意这笔", "同意这个", "通过这笔", "通过这个", "通过第", "同意第"]
    )


def is_approval_reject_request(text: str) -> bool:
    if any(word in text for word in ["建议", "还是"]):
        return False
    return any(
        word in text
        for word in ["帮我拒绝", "帮我驳回", "审批拒绝", "审批驳回", "拒绝这笔", "驳回这笔", "拒绝这个", "驳回这个", "拒绝第", "驳回第"]
    )


def is_organization_outline_question(text: str) -> bool:
    return any(word in text for word in ["组织架构", "组织结构", "通讯录", "部门架构"]) and any(
        word in text for word in ["输出", "整理", "生成", "xmind", "Xmind", "XMind", "大纲"]
    )
