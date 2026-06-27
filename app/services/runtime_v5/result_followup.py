from __future__ import annotations

import re

from app.services.runtime_v5.models import ResultContext, ResultFollowup


_PRONOUN_PATTERN = re.compile(r"他们|她们|这些人|这些单子|这些审批|这些任务|这些邮件|这些日程|这些|那些")
_DETAIL_PATTERN = re.compile(r"详情|详细|明细|展开|展开看看|看看第|看第")
_EXPAND_PATTERN = re.compile(r"全部|具体|列出来|列出|显示|全部显示|展开全部|哪些|哪几位|哪几条|哪\d+位|分别|名单|名字")
_NAME_ONLY_PATTERN = re.compile(r"只要名字|只需要名字|只看名字|名字就行|全部名字|姓名就行|只要姓名")
_CONTINUE_PATTERN = re.compile(r"补全|继续|接着列|剩下|剩余|后面")
_POSITION_PATTERN = re.compile(
    r"最后(?:一个|一条|一笔)?|"
    r"第?[一二两三四五六七八九十\d]+\s*(?:个|条|笔|号)"
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
    projection = _field_projection(text) or str(metadata.get("field_projection") or "")
    people_filter = _people_followup_filter(text, result_context)
    if people_filter:
        projection = projection or _inherited_people_filter_projection(metadata) or "count_only"
        if people_filter.get("wants_detail") == "true" and projection == "count_only":
            projection = "detail"
        return ResultFollowup(
            is_result_followup=True,
            followup_type="people_filter",
            entity_ref={
                **_context_ref(result_context),
                **people_filter,
                "result_context_operation": "filter" if people_filter.get("filter") != "all" else "expand",
                "field_projection": projection,
            },
        )
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
    if _EXPAND_PATTERN.search(text) or _CONTINUE_PATTERN.search(text) or projection:
        operation = "continue" if _CONTINUE_PATTERN.search(text) else "expand"
        return ResultFollowup(
            is_result_followup=True,
            followup_type="expand",
            entity_ref={
                **_context_ref(result_context),
                "result_context_operation": operation,
                "field_projection": projection,
            },
        )
    return ResultFollowup(is_result_followup=False)


def _inherited_people_filter_projection(metadata: dict) -> str:
    if str(metadata.get("field_projection") or "") == "count_only":
        return "count_only"
    if str(metadata.get("people_query_mode") or "") in {"count", "count_only", "gender_count", "title_count"}:
        return "count_only"
    if metadata.get("result_context_presentation") == "summary":
        return "count_only"
    return ""


def _field_projection(text: str) -> str:
    compact = re.sub(r"\s+", "", str(text or ""))
    if _asks_for_count(compact):
        return "count_only"
    if any(
        token in compact
        for token in (
            "不用给我详情",
            "不要给我详情",
            "不用详情",
            "不要详情",
            "不用明细",
            "不要明细",
            "不用列",
            "不要列",
            "别列",
            "只要数量",
            "只需要数量",
            "只回答数量",
            "只说数量",
            "只回答多少",
            "只说多少",
        )
    ):
        return "count_only"
    if _NAME_ONLY_PATTERN.search(compact):
        return "name_only"
    if any(token in compact for token in ("名字", "姓名")) and any(token in compact for token in ("只", "要", "全部", "补全", "继续", "剩下", "名单", "告诉我")):
        return "name_only"
    return ""


def _asks_for_count(compact: str) -> bool:
    count_tokens = ("多少", "几人", "几个", "几位", "多少人", "多少位", "数量")
    if not any(token in compact for token in count_tokens):
        return False
    return not any(token in compact for token in ("分别是谁", "都有谁", "名单", "列出", "全部显示", "有哪些"))


def _people_followup_filter(text: str, result_context: ResultContext) -> dict[str, str]:
    if result_context.result_type not in {"people_search", "department_members", "organization_snapshot"}:
        return {}
    compact = re.sub(r"\s+", "", text.lower())
    wants_detail = any(token in compact for token in ("分别是谁", "都有谁", "名单", "列出来", "列出", "全部显示", "有哪些", "哪几位", "哪些")) or bool(
        re.search(r"哪[一二两三四五六七八九十\d]*个|哪[一二两三四五六七八九十\d]*位", compact)
    )
    gender = ""
    if any(token in compact for token in ("男生", "男性", "男的", "男人", "男员工")):
        gender = "male"
    elif any(token in compact for token in ("女生", "女性", "女的", "女人", "女员工")):
        gender = "female"
    if gender:
        result = {"filter": "gender", "gender": gender}
        if wants_detail:
            result["wants_detail"] = "true"
        return result
    department = _people_department_filter(compact)
    if department:
        result = {"filter": "department", "keyword": department}
        if wants_detail:
            result["wants_detail"] = "true"
        return result
    title = _people_title_filter(compact)
    if title:
        result = {"filter": "title", "keyword": title}
        if wants_detail:
            result["wants_detail"] = "true"
        return result
    if any(token in compact for token in ("分别是谁", "都有谁", "名单", "继续列出", "继续列", "列出来", "全部列出", "全部显示")):
        return {"filter": "all", "wants_detail": "true"}
    return {}


def _people_department_filter(compact: str) -> str:
    if not any(token in compact for token in ("部门", "团队", "中心", "小组", "部")):
        return ""
    if not any(token in compact for token in ("哪些", "谁", "名单", "成员", "人员", "同事", "都有")):
        return ""
    return _people_filter_keyword(compact, ("部门", "团队", "中心", "小组", "成员", "人员", "同事", "名单"))


def _people_title_filter(compact: str) -> str:
    if not any(token in compact for token in ("岗位", "职位", "工程师", "经理", "主管", "总监", "销售", "财务", "测试", "运营", "人事", "研发")):
        return ""
    if not any(token in compact for token in ("哪些", "谁", "名单", "人员", "员工", "都有", "列")):
        return ""
    return _people_filter_keyword(compact, ("岗位", "职位", "人员", "员工", "名单"))


def _people_filter_keyword(compact: str, domain_tokens: tuple[str, ...]) -> str:
    keyword = compact
    for token in (
        "上一轮",
        "刚才",
        "这些人",
        "这些",
        "里面",
        "中",
        "里",
        "哪些是",
        "谁是",
        "有哪些",
        "都有谁",
        "分别是谁",
        "列出来",
        "列出",
        "全部",
        "的",
        "呢",
        "吗",
        "？",
        "?",
        *domain_tokens,
    ):
        keyword = keyword.replace(token, "")
    return keyword.strip()


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
        "result_context_operation": "reference",
        "field_projection": "",
    }
