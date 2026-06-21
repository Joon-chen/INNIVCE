from __future__ import annotations

import re

from app.services.runtime_v5.models import ResultContext, ResultFollowup


_PRONOUN_PATTERN = re.compile(r"他们|她们|这些人|这些单子|这些审批|这些任务|这些邮件|这些日程|这些|那些")
_DETAIL_PATTERN = re.compile(r"详情|详细|明细|展开|展开看看|看看第|看第")
_EXPAND_PATTERN = re.compile(r"全部|具体|列出来|显示|全部显示|展开全部")
_POSITION_PATTERN = re.compile(
    r"最后(?:一个|一条|一笔)?|"
    r"第?[一二两三四五六七八九十\d]+\s*(?:个|条|笔|号)?"
)
_RECEIPT_DETAIL_PATTERN = re.compile(
    r"链接|地址|在哪|哪里|发给谁|发到哪|发到哪里|草稿|表格|文件|刚才那个|刚才的|刚刚那个|刚刚的|"
    r"上一次|上一条|上一个|刚才执行|刚刚执行|"
    r"结果|状态|执行了吗|有没有执行|执行完了吗|处理完了吗|完成了吗|成功了吗|有没有成功|"
    r"为什么没执行|为什么失败|失败原因|错误|报错|取消了吗|过期|失效"
)
_ACTION_PATTERN = re.compile(
    r"通过|同意|拒绝|驳回|转交|加签|退回|催办|撤回|抄送|发送|发给|转发|创建|新建|写入|导出|完成|删除|更新|取消"
)


def detect_result_followup(question: str, result_context: ResultContext | None) -> ResultFollowup:
    if result_context is None or not result_context.has_items:
        return ResultFollowup(is_result_followup=False)

    text = question.strip()
    if not text:
        return ResultFollowup(is_result_followup=False)
    metadata = result_context.metadata if isinstance(result_context.metadata, dict) else {}
    if metadata.get("context_kind") == "action_receipt" and _RECEIPT_DETAIL_PATTERN.search(text):
        return ResultFollowup(
            is_result_followup=True,
            followup_type="receipt_detail",
            entity_ref=_context_ref(result_context),
        )
    if _looks_like_action(text):
        return ResultFollowup(is_result_followup=False)

    position = _POSITION_PATTERN.search(text)
    if position:
        index = _position_index(position.group(0))
        followup_type = "detail" if _DETAIL_PATTERN.search(text) else "position"
        return ResultFollowup(
            is_result_followup=True,
            followup_type=followup_type,
            entity_ref={"index": index, **_context_ref(result_context)},
        )
    if _PRONOUN_PATTERN.search(text):
        return ResultFollowup(is_result_followup=True, followup_type="pronoun", entity_ref=_context_ref(result_context))
    if _DETAIL_PATTERN.search(text):
        return ResultFollowup(is_result_followup=True, followup_type="detail", entity_ref=_context_ref(result_context))
    if _EXPAND_PATTERN.search(text):
        return ResultFollowup(is_result_followup=True, followup_type="expand", entity_ref=_context_ref(result_context))
    return ResultFollowup(is_result_followup=False)


def _looks_like_action(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    if _ACTION_PATTERN.search(normalized):
        return True
    return False


def _position_index(token: str) -> int:
    token = token.strip().replace(" ", "")
    if "最后" in token:
        return -1
    numeric = re.search(r"\d+", token)
    if numeric:
        return max(int(numeric.group(0)) - 1, 0)
    mapping = {
        "一": 0,
        "第一": 0,
        "第一个": 0,
        "第一条": 0,
        "第一笔": 0,
        "二": 1,
        "两": 1,
        "第二": 1,
        "第二个": 1,
        "第二条": 1,
        "第二笔": 1,
        "三": 2,
        "第三": 2,
        "第三个": 2,
        "第三条": 2,
        "第三笔": 2,
    }
    if token in mapping:
        return mapping[token]
    cn_number = _cn_position_number(token)
    if cn_number is not None:
        return max(cn_number - 1, 0)
    for key, value in mapping.items():
        if key in token:
            return value
    return 0


def _cn_position_number(token: str) -> int | None:
    cleaned = token.replace("第", "").replace("个", "").replace("条", "").replace("笔", "").replace("号", "")
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if cleaned in digits:
        return digits[cleaned]
    if cleaned == "十":
        return 10
    if cleaned.startswith("十"):
        return 10 + digits.get(cleaned[-1], 0)
    if "十" in cleaned:
        left, _, right = cleaned.partition("十")
        return digits.get(left, 0) * 10 + digits.get(right, 0)
    return None


def _context_ref(result_context: ResultContext) -> dict:
    metadata = result_context.metadata if isinstance(result_context.metadata, dict) else {}
    return {
        "result_type": result_context.result_type,
        "context_kind": metadata.get("context_kind") or "query_result",
        "item_count": result_context.count,
        "actionable": bool(metadata.get("actionable", False)),
    }
