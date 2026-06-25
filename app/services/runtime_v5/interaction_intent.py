from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class InteractionIntent:
    kind: str
    confidence: float
    reason: str = ""

    @property
    def is_smalltalk(self) -> bool:
        return self.kind in {
            "greeting",
            "assistant_identity",
            "user_context",
            "time_date",
            "emoji_or_reaction",
            "conversation",
            "conversation_feedback",
        }


def classify_interaction_intent(text: str) -> InteractionIntent:
    normalized = str(text or "").strip().lower().strip("。.!！?？ ")
    compact = re.sub(r"\s+", "", normalized)
    if not compact:
        return InteractionIntent(kind="conversation", confidence=0.7, reason="empty_or_non_text")
    if _is_time_date(compact):
        return InteractionIntent(kind="time_date", confidence=0.96, reason="time_or_date_question")
    if _is_user_context(compact):
        return InteractionIntent(kind="user_context", confidence=0.9, reason="user_context_conversation")
    if _is_assistant_identity(compact):
        return InteractionIntent(kind="assistant_identity", confidence=0.96, reason="assistant_identity_question")
    if _is_conversation_feedback(compact):
        return InteractionIntent(kind="conversation_feedback", confidence=0.9, reason="conversation_feedback")
    if _is_emoji_or_reaction(compact):
        return InteractionIntent(kind="emoji_or_reaction", confidence=0.9, reason="emoji_or_reaction_question")
    if _is_greeting(compact, normalized):
        return InteractionIntent(kind="greeting", confidence=0.94, reason="greeting")
    if _is_short_conversation(compact):
        return InteractionIntent(kind="conversation", confidence=0.82, reason="short_non_business_conversation")
    if not _has_business_signal(compact) and not _has_external_signal(compact):
        return InteractionIntent(kind="conversation", confidence=0.74, reason="no_business_or_external_signal")
    return InteractionIntent(kind="business", confidence=0.7, reason="business_or_unknown")


def _is_time_date(compact: str) -> bool:
    return any(token in compact for token in ("现在几点", "几点了", "今天几号", "今天日期", "今天星期几"))


def _is_user_context(compact: str) -> bool:
    if _contains_user_context_topic(compact):
        return True
    if any(
        token in compact
        for token in (
            "我是谁",
            "我叫什么",
            "我的名字",
            "你知道我是谁",
            "你知道我叫什么",
            "你知道我吗",
            "你认识我吗",
            "我的身份",
            "我是什么身份",
            "我的职位",
            "我的岗位",
            "我在公司的职位",
            "我在这个公司的职位",
            "你知道我的职位",
            "你知道我的岗位",
            "你应该叫我什么",
            "你该叫我什么",
            "你到底叫我什么",
            "怎么称呼我",
            "如何称呼我",
            "我是什么性格",
            "我的性格",
            "你了解我吗",
            "你知道我的风格吗",
        )
    ):
        return True
    if "老板" in compact and any(token in compact for token in ("我是", "我就是", "我是不是", "我的身份", "你忘", "忘记", "告诉过你")):
        return True
    if _is_address_preference(compact):
        return True
    if any(token in compact for token in ("职位", "岗位")) and any(
        token in compact for token in ("我", "我的", "我现在", "我在公司", "我在这个公司", "你知道我")
    ):
        return True
    if any(
        token in compact
        for token in (
            "这个公司老板是谁",
            "这个公司的老板是谁",
            "这个公司谁是老板",
            "这个公司的谁是老板",
            "当前公司老板是谁",
            "当前公司的老板是谁",
            "公司老板是谁",
            "公司的老板是谁",
        )
    ):
        return True
    if any(token in compact for token in ("之前告诉过你", "刚才告诉过你", "不是告诉你")) and any(
        token in compact for token in ("身份", "名字", "职位", "老板", "称呼", "外号")
    ):
        return True
    return False


def _contains_user_context_topic(compact: str) -> bool:
    first_person = any(token in compact for token in ("我", "我的", "叫我", "称呼我", "喊我"))
    user_topic = any(
        token in compact
        for token in (
            "身份",
            "角色",
            "职位",
            "岗位",
            "职务",
            "风格",
            "性格",
            "偏好",
            "习惯",
            "称呼",
            "怎么叫",
            "怎么喊",
            "直呼",
            "老板",
            "管理者",
        )
    )
    return first_person and user_topic


def _is_address_preference(compact: str) -> bool:
    return any(
        token in compact
        for token in (
            "叫我",
            "喊我",
            "称呼我",
            "你叫我",
            "你喊我",
            "该叫我",
            "应该叫我",
            "到底叫我",
            "别叫我名字",
            "不要叫我名字",
            "不要直呼我名字",
            "不要直接叫我名字",
            "不直接叫我名字",
        )
    )


def _is_assistant_identity(compact: str) -> bool:
    return any(token in compact for token in ("你是谁", "你叫什么", "你叫什么名字"))


def _is_conversation_feedback(compact: str) -> bool:
    if any(token in compact for token in ("不明确", "没明白", "听不懂", "不智能", "不聪明", "很傻", "太傻", "笨", "慢", "非所问")):
        return True
    if any(token in compact for token in ("联网", "上网", "外部实时", "实时联网")) and any(
        token in compact for token in ("你能", "你可以", "能不能", "可不可以", "要不要", "想不想", "变得", "更强", "聊天", "对话", "推理", "能力")
    ):
        return True
    return any(
        token in compact
        for token in (
            "你太机械",
            "太机械了",
            "你太生硬",
            "太生硬了",
            "你很笨",
            "你太笨",
            "越搞越笨",
            "不像ai助理",
            "没有情绪价值",
            "回复太慢",
            "消息太慢",
            "回答太慢",
            "别老列菜单",
            "不要老列菜单",
            "别总是",
            "不要总是",
        )
    )


def _is_emoji_or_reaction(compact: str) -> bool:
    return any(token in compact for token in ("这个表情", "表情是什么", "什么情绪", "这个情绪", "这个emoji", "这个emoj"))


def _is_greeting(compact: str, normalized: str) -> bool:
    terms = {
        "在不在",
        "在吗",
        "在么",
        "你在吗",
        "你在不在",
        "有人吗",
        "能听到吗",
        "还在吗",
        "hello",
        "hi",
        "嗨",
        "你好",
        "你好呀",
        "你好啊",
        "您好",
        "哈喽",
    }
    return normalized in terms or compact in terms or any(compact == term * 2 for term in terms)


def _is_short_conversation(compact: str) -> bool:
    if len(compact) > 14:
        return False
    return any(token in compact for token in ("谢谢", "没事", "哭了", "哈哈", "嗯嗯", "好的", "知道了", "不理我", "你觉得呢", "那你觉得"))


def _has_business_signal(compact: str) -> bool:
    return any(
        token in compact
        for token in (
            "审批",
            "单子",
            "报销",
            "付款",
            "采购",
            "请假",
            "出差",
            "任务",
            "待办",
            "工作",
            "负荷",
            "项目",
            "okr",
            "目标",
            "日程",
            "会议",
            "安排",
            "邮箱",
            "邮件",
            "消息",
            "群聊",
            "通讯录",
            "人员",
            "部门",
            "组织",
            "公司",
            "企业",
            "主营",
            "业务",
            "客户",
            "商机",
            "订单",
            "合同",
            "供应商",
            "文档",
            "知识",
            "表格",
            "base",
            "wiki",
            "风险",
            "异常",
            "预警",
            "分析",
            "建议",
            "决策",
            "经营",
            "数据",
            "查询",
            "查看",
            "搜索",
            "创建",
            "新建",
            "发送",
            "发给",
            "转发给",
            "告诉",
            "通知",
        )
    )


def _has_external_signal(compact: str) -> bool:
    return any(
        token in compact
        for token in (
            "天气",
            "气温",
            "下雨",
            "新闻",
            "官网",
            "网页",
            "网站",
            "公开资料",
            "网上",
            "股价",
            "汇率",
            "油价",
            "航班",
            "附近",
            "餐厅",
            "外卖",
            "门店",
            "市场价",
            "最新",
        )
    )
