from __future__ import annotations

from datetime import datetime, timedelta
import re
from zoneinfo import ZoneInfo

from app.services.runtime_v5.llm_intent import llm_command_intent
from app.services.runtime_v5.models import IntentResult, RuntimeContext


_APP_TOKEN_PATTERN = re.compile(r"\b(bascn[-A-Za-z0-9_]+)\b")
_DOC_TOKEN_PATTERN = re.compile(r"\b((?:doccn|doxcn|docxcn)[-A-Za-z0-9_]+)\b")
_WIKI_SPACE_PATTERN = re.compile(r"\b(wksp[-A-Za-z0-9_]+)\b")
_SLIDES_URL_TOKEN_PATTERN = re.compile(r"/slides/([A-Za-z0-9_-]+)")
_SLIDES_TOKEN_PATTERN = re.compile(r"\b(slides[A-Za-z0-9_-]{8,})\b")
_WHITEBOARD_TOKEN_PATTERN = re.compile(r"\b(wbcn[A-Za-z0-9_-]+)\b")
_TASK_GUID_PATTERN = re.compile(r"\b([A-Za-z0-9_-]{8,})\b")
_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_LOCAL_TZ = ZoneInfo("Asia/Shanghai")
_APPROVAL_CURRENT_KEY = "runtime_v5_current_approval_item"


def recognize_intent(question: str, context: RuntimeContext) -> IntentResult:
    rule_intent = _recognize_intent_by_rules(question, context)
    llm_intent = llm_command_intent(question=question, context=context, rule_intent=rule_intent)
    return llm_intent or rule_intent


def _recognize_intent_by_rules(question: str, context: RuntimeContext) -> IntentResult:
    current_message = context.current_message or ""
    text = question if question.strip() == current_message.strip() else f"{question} {current_message}"
    text = text.strip().lower()

    if _is_smalltalk(text):
        return IntentResult(
            question_type="query",
            intent="smalltalk",
            data_scope="self",
            missing_params=(),
            confidence=0.95,
            canonical_question=question,
        )

    if _is_action_trace_query(text):
        return IntentResult(
            question_type="query",
            intent="action_trace",
            data_scope="self",
            missing_params=(),
            confidence=0.9,
            canonical_question=question,
        )

    if _is_governance_view_query(text):
        return IntentResult(
            question_type="query",
            intent="governance_view",
            data_scope="self",
            missing_params=(),
            confidence=0.9,
            canonical_question=question,
        )

    if _is_runtime_status_query(text):
        return IntentResult(
            question_type="query",
            intent="runtime_status",
            data_scope="self",
            missing_params=(),
            confidence=0.9,
            canonical_question=question,
        )

    if _is_docs_read(text):
        document_id = _extract_doc_token(question)
        return IntentResult(
            question_type="query",
            intent="docs_read",
            data_scope="company",
            entities={"document_id": document_id} if document_id else {},
            missing_params=() if document_id else ("document_id",),
            confidence=0.88 if document_id else 0.65,
            canonical_question=question,
        )

    if _is_wiki_search(text):
        space_id = _extract_wiki_space_id(question)
        return IntentResult(
            question_type="query",
            intent="wiki_search",
            data_scope="company",
            entities={"space_id": space_id} if space_id else {},
            missing_params=(),
            confidence=0.86,
            canonical_question=question,
        )

    if _is_drive_list(text):
        return IntentResult(
            question_type="query",
            intent="drive_list",
            data_scope="company",
            entities=_drive_list_params(question),
            missing_params=(),
            confidence=0.84,
            canonical_question=question,
        )

    if _is_slides_read(text):
        slides_token = _extract_slides_token(question)
        return IntentResult(
            question_type="query",
            intent="slides_read",
            data_scope="company",
            entities={"xml_presentation_id": slides_token} if slides_token else {},
            missing_params=() if slides_token else ("xml_presentation_id",),
            confidence=0.86 if slides_token else 0.64,
            canonical_question=question,
        )

    if _is_whiteboard_read(text):
        whiteboard_token = _extract_whiteboard_token(question)
        return IntentResult(
            question_type="query",
            intent="whiteboard_read",
            data_scope="company",
            entities={"whiteboard_token": whiteboard_token} if whiteboard_token else {},
            missing_params=() if whiteboard_token else ("whiteboard_token",),
            confidence=0.86 if whiteboard_token else 0.64,
            canonical_question=question,
        )

    if _is_vc_meeting_search(text):
        return IntentResult(
            question_type="query",
            intent="vc_meeting_search",
            data_scope="company",
            entities={"page_size": 20},
            missing_params=(),
            confidence=0.84,
            canonical_question=question,
        )

    if _is_attendance_query(text):
        return IntentResult(
            question_type="query",
            intent="attendance_query",
            data_scope="self",
            entities={},
            missing_params=(),
            confidence=0.84,
            canonical_question=question,
        )

    if _is_okr_query(text):
        return IntentResult(
            question_type="query",
            intent="okr_query",
            data_scope="self",
            entities={},
            missing_params=(),
            confidence=0.84,
            canonical_question=question,
        )

    if _is_organization_export(text):
        app_token = _extract_app_token(text)
        entities = {"app_token": app_token, "target": "existing_base"} if app_token else {"target": "new_base"}
        entities.update(_result_delivery_target_params(question, context))
        return IntentResult(
            question_type="action",
            intent="organization_export",
            data_scope="organization",
            entities=entities,
            missing_params=(),
            confidence=0.92,
            canonical_question=question,
        )

    if _is_approval_transfer(text, context):
        params = _approval_ref(question, context)
        entities = {**params, "comment": _approval_comment(question, default="转交处理")}
        target = _approval_target_keyword(question)
        if target:
            entities["target_keyword"] = target
        missing = [] if params.get("item") else ["approval_item"]
        missing.extend([] if entities.get("transfer_user_id") or entities.get("target_keyword") else ["transfer_user_id"])
        return IntentResult(
            question_type="action",
            intent="approval_transfer",
            data_scope="self",
            entities=entities,
            missing_params=tuple(missing),
            confidence=0.86 if params.get("item") else 0.62,
            canonical_question=question,
        )

    if _is_approval_add_sign(text, context):
        params = _approval_ref(question, context)
        entities = {**params, "comment": _approval_comment(question, default="请协助审批")}
        target = _approval_target_keyword(question)
        if target:
            entities["target_keyword"] = target
        missing = [] if params.get("item") else ["approval_item"]
        missing.extend([] if entities.get("add_sign_user_ids") or entities.get("target_keyword") else ["add_sign_user_ids"])
        return IntentResult(
            question_type="action",
            intent="approval_add_sign",
            data_scope="self",
            entities=entities,
            missing_params=tuple(missing),
            confidence=0.86 if params.get("item") else 0.62,
            canonical_question=question,
        )

    if _is_approval_rollback(text, context):
        params = _approval_ref(question, context)
        missing = [] if params.get("item") else ["approval_item"]
        missing.extend([] if params.get("node_ids") else ["node_ids"])
        return IntentResult(
            question_type="action",
            intent="approval_rollback",
            data_scope="self",
            entities={**params, "comment": _approval_comment(question, default="退回补充")},
            missing_params=tuple(missing),
            confidence=0.82 if params.get("item") else 0.62,
            canonical_question=question,
        )

    if _is_approval_remind(text, context):
        params = _approval_ref(question, context)
        return IntentResult(
            question_type="action",
            intent="approval_remind",
            data_scope="self",
            entities={**params, "comment": _approval_comment(question, default="请尽快处理")},
            missing_params=() if params.get("item") else ("approval_item",),
            confidence=0.84 if params.get("item") else 0.62,
            canonical_question=question,
        )

    if _is_approval_cancel(text, context):
        params = _approval_ref(question, context)
        return IntentResult(
            question_type="action",
            intent="approval_cancel",
            data_scope="self",
            entities={**params, "comment": _approval_comment(question, default="撤回审批")},
            missing_params=() if params.get("item") else ("approval_item",),
            confidence=0.84 if params.get("item") else 0.62,
            canonical_question=question,
        )

    if _is_approval_cc(text, context):
        params = _approval_ref(question, context)
        entities = {**params, "comment": _approval_comment(question, default="抄送知会")}
        target = _approval_target_keyword(question)
        if target:
            entities["target_keyword"] = target
        missing = [] if params.get("item") else ["approval_item"]
        missing.extend([] if entities.get("cc_user_ids") or entities.get("target_keyword") else ["cc_user_ids"])
        return IntentResult(
            question_type="action",
            intent="approval_cc",
            data_scope="self",
            entities=entities,
            missing_params=tuple(missing),
            confidence=0.84 if params.get("item") else 0.62,
            canonical_question=question,
        )

    if _is_approval_initiated(text):
        return IntentResult(
            question_type="query",
            intent="approval_initiated",
            data_scope="self",
            entities={"page_size": 20},
            missing_params=(),
            confidence=0.84,
            canonical_question=question,
        )

    if _is_approval_approve(text, context):
        params = _approval_ref(question, context)
        return IntentResult(
            question_type="action",
            intent="approval_approve",
            data_scope="self",
            entities={**params, "comment": _approval_comment(question, default="同意")},
            missing_params=() if params.get("item") else ("approval_item",),
            confidence=0.9 if params.get("item") else 0.62,
            canonical_question=question,
        )

    if _is_approval_reject(text, context):
        params = _approval_ref(question, context)
        return IntentResult(
            question_type="action",
            intent="approval_reject",
            data_scope="self",
            entities={**params, "comment": _approval_comment(question, default="拒绝")},
            missing_params=() if params.get("item") else ("approval_item",),
            confidence=0.9 if params.get("item") else 0.62,
            canonical_question=question,
        )

    if _is_approval_detail(text, context):
        params = _approval_ref(question, context)
        return IntentResult(
            question_type="query",
            intent="approval_detail",
            data_scope="self",
            entities=params,
            missing_params=() if params.get("item") else ("approval_item",),
            confidence=0.86 if params.get("item") else 0.62,
            canonical_question=question,
        )

    if _is_approval_query(text):
        return IntentResult(
            question_type="query",
            intent="approval_query",
            data_scope="self" if _has_any(text, ("我", "我的", "待我", "需要我")) else "company",
            confidence=0.9,
            canonical_question=question,
        )

    if _is_task_create(text):
        return IntentResult(
            question_type="action",
            intent="task_create",
            data_scope="self",
            entities={"summary": _task_summary(question)},
            missing_params=(),
            confidence=0.86,
            canonical_question=question,
        )

    if _is_calendar_create(text):
        time_params = _calendar_time_params(question)
        return IntentResult(
            question_type="action",
            intent="calendar_create",
            data_scope="self",
            entities={"summary": _calendar_summary(question), **time_params},
            missing_params=tuple(key for key in ("start", "end") if not time_params.get(key)),
            confidence=0.84,
            canonical_question=question,
        )

    if _is_task_complete(text):
        task_ref = _task_ref(question, context)
        return IntentResult(
            question_type="action",
            intent="task_complete",
            data_scope="self",
            entities=task_ref,
            missing_params=() if task_ref.get("task_guid") else ("task_guid",),
            confidence=0.88 if task_ref.get("task_guid") else 0.62,
            canonical_question=question,
        )

    if _is_task_search(text):
        return IntentResult(
            question_type="query",
            intent="task_search",
            data_scope="self",
            entities={"keyword": _task_search_keyword(question)},
            missing_params=(),
            confidence=0.84,
            canonical_question=question,
        )

    if _is_task_query(text):
        return IntentResult(
            question_type="query",
            intent="task_query",
            data_scope=_query_data_scope(text),
            entities={"page_size": 20},
            missing_params=(),
            confidence=0.84,
            canonical_question=question,
        )

    if _is_calendar_query(text):
        return IntentResult(
            question_type="query",
            intent="calendar_query",
            data_scope=_query_data_scope(text),
            entities=_calendar_query_params(question),
            missing_params=(),
            confidence=0.84,
            canonical_question=question,
        )

    if _is_mail_draft_create(text):
        draft_params = _mail_draft_params(question)
        return IntentResult(
            question_type="action",
            intent="mail_draft_create",
            data_scope="self",
            entities=draft_params,
            missing_params=tuple(key for key in ("to", "subject", "body") if not draft_params.get(key)),
            confidence=0.86,
            canonical_question=question,
        )

    if _is_mail_search(text):
        return IntentResult(
            question_type="query",
            intent="mail_search",
            data_scope="self",
            entities={"query": _mail_search_keyword(question), "page_size": 20},
            missing_params=(),
            confidence=0.84,
            canonical_question=question,
        )

    if _is_mail_query(text):
        return IntentResult(
            question_type="query",
            intent="mail_query",
            data_scope="self",
            entities={"page_size": 20},
            missing_params=(),
            confidence=0.84,
            canonical_question=question,
        )

    if _is_im_send(text):
        send_params = _im_send_params(question, context)
        return IntentResult(
            question_type="action",
            intent="message_send",
            data_scope="self",
            entities=send_params,
            missing_params=tuple(key for key in ("target_type", "text") if not send_params.get(key)),
            confidence=0.88,
            canonical_question=question,
        )

    if _is_im_chat_search(text):
        return IntentResult(
            question_type="query",
            intent="chat_search",
            data_scope="self",
            entities={"query": _im_chat_query(question), "page_size": 20},
            missing_params=(),
            confidence=0.84,
            canonical_question=question,
        )

    if _is_im_message_query(text):
        params = _im_message_query_params(question)
        return IntentResult(
            question_type="query",
            intent="message_query",
            data_scope="self",
            entities=params,
            missing_params=tuple(key for key in ("chat_id",) if not params.get(key)),
            confidence=0.8,
            canonical_question=question,
        )

    if _is_department_members_query(text):
        return IntentResult(
            question_type="query",
            intent="department_members",
            data_scope="department",
            entities={"keyword": _department_keyword(question)},
            missing_params=(),
            confidence=0.86,
            canonical_question=question,
        )

    if _has_any(text, ("组织架构", "组织结构", "通讯录")):
        return IntentResult(
            question_type="query",
            intent="organization_snapshot",
            data_scope="organization",
            confidence=0.88,
            canonical_question=question,
        )

    if _has_any(text, ("电话", "邮箱", "手机号", "职位", "是谁", "谁是", "谁担任")):
        return IntentResult(
            question_type="query",
            intent="people_lookup",
            data_scope="person",
            entities={"keyword": _people_keyword(question)},
            confidence=0.86,
            canonical_question=question,
        )

    if _has_any(text, ("风险", "异常", "预警", "隐患")):
        return IntentResult(
            question_type="insight",
            intent="risk_analysis",
            data_scope="company",
            confidence=0.82,
            canonical_question=question,
        )

    if _is_decision_question(text):
        return IntentResult(
            question_type="decision",
            intent="decision_advice",
            data_scope="company",
            entities={"query": question.strip()},
            confidence=0.78,
            canonical_question=question,
        )

    if _is_analysis_question(text):
        return IntentResult(
            question_type="analysis",
            intent="general_analysis",
            data_scope="company",
            entities={"query": question.strip()},
            confidence=0.78,
            canonical_question=question,
        )

    if _has_any(text, ("公司是做什么", "公司介绍", "这家公司", "公司情况")):
        return IntentResult(
            question_type="query",
            intent="company_intro",
            data_scope="company",
            confidence=0.82,
            canonical_question=question,
        )

    return IntentResult(
        question_type="query",
        intent="general_query",
        data_scope="company",
        confidence=0.55,
        canonical_question=question,
    )


def _is_organization_export(text: str) -> bool:
    return (
        _has_any(text, ("组织", "组织架构", "组织结构", "通讯录"))
        and _has_any(text, ("表格", "多维表格", "base", "bitable", "表"))
        and _has_any(text, ("创建", "新建", "建表", "建一个", "建张", "做一个", "弄一个"))
        and _has_any(text, ("放入", "写入", "导入", "放进去", "放到", "同步"))
    )


def _is_smalltalk(text: str) -> bool:
    normalized = text.strip().lower().strip("。.!！?？ ")
    compact = re.sub(r"\s+", "", normalized)
    if any(token in compact for token in ("现在几点", "几点了", "今天几号", "今天日期", "今天星期几")):
        return True
    if any(token in compact for token in ("我是谁", "你知道我是谁", "你知道我吗", "你认识我吗")):
        return True
    if any(token in compact for token in ("你是谁", "你叫什么", "你叫什么名字")):
        return True
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
        "你是谁",
        "你叫什么",
        "你叫什么名字",
        "你知道我吗",
        "你认识我吗",
        "你知道我是谁吗",
        "我是谁",
    }
    return normalized in terms or compact in terms or any(compact == term * 2 for term in terms)


def _is_action_trace_query(text: str) -> bool:
    return _has_any(
        text,
        (
            "刚才执行",
            "上一步执行",
            "执行结果",
            "刚才按钮",
            "刚才操作",
            "上一步操作",
            "处理结果",
            "按钮没反应",
            "点了没反应",
            "没有下文",
            "没下文",
            "刚才怎么了",
            "执行到哪",
            "刚才为什么",
            "为什么没执行",
            "为什么没有执行",
            "为什么这么判断",
            "为什么这么回复",
            "怎么判断的",
            "走了什么策略",
            "用了什么能力",
            "用了哪个能力",
            "刚才走了什么",
        ),
    )


def _is_runtime_status_query(text: str) -> bool:
    return _has_any(
        text,
        (
            "系统诊断",
            "诊断详情",
            "运行状态",
        ),
    )


def _is_governance_view_query(text: str) -> bool:
    return _has_any(
        text,
        (
            "能力清册",
            "能力目录",
            "治理视图",
            "治理状态",
            "能力接入情况",
            "还有哪些没接",
            "还缺什么能力",
            "能力状态",
        ),
    )


def _is_analysis_question(text: str) -> bool:
    return _has_any(text, ("为什么", "原因", "分析", "怎么看", "如何看待", "复盘", "问题出在哪里"))


def _is_decision_question(text: str) -> bool:
    return _has_any(text, ("怎么办", "怎么处理", "如何处理", "建议", "应该怎么做", "下一步怎么做", "要不要"))


def _is_approval_query(text: str) -> bool:
    if _has_any(
        text,
        (
            "待我审批",
            "需要我审批",
            "我审批",
            "我的审批",
            "待审批",
            "待审",
            "待我审核",
            "需要我审核",
            "审批工作台",
            "待审批工作台",
            "待审工作台",
            "审批列表",
            "待审批列表",
            "审批待办",
            "审批实时待办",
        ),
    ):
        return True
    if _has_any(text, ("审批", "批复", "批准", "审核", "审的单")) and _has_any(
        text,
        ("待", "未", "需要我", "要我", "我批", "我审", "我处理", "有没有", "哪些", "查看", "查一下"),
    ):
        return True
    return "单子" in text and _has_any(text, ("审批", "待我", "需要我", "我批", "我审"))


def _is_approval_initiated(text: str) -> bool:
    return _has_any(text, ("我发起的审批", "我提交的审批", "我申请的审批", "我发起的单", "我提交的单"))


def _is_approval_detail(text: str, context: RuntimeContext) -> bool:
    return _has_approval_ref_context(context) and _has_any(text, ("详情", "明细", "展开", "看看第", "看第", "审批详情"))


def _is_approval_transfer(text: str, context: RuntimeContext) -> bool:
    return _has_approval_ref_context(context) and _has_any(text, ("转交", "转给"))


def _is_approval_add_sign(text: str, context: RuntimeContext) -> bool:
    return _has_approval_ref_context(context) and _has_any(text, ("加签", "加签给"))


def _is_approval_rollback(text: str, context: RuntimeContext) -> bool:
    return _has_approval_ref_context(context) and _has_any(text, ("退回", "打回", "回退"))


def _is_approval_remind(text: str, context: RuntimeContext) -> bool:
    return _has_approval_ref_context(context) and _has_any(text, ("催办", "催一下", "提醒审批人"))


def _is_approval_cancel(text: str, context: RuntimeContext) -> bool:
    return _has_approval_ref_context(context) and _has_any(text, ("撤回", "取消审批", "撤销审批"))


def _is_approval_cc(text: str, context: RuntimeContext) -> bool:
    return _has_approval_ref_context(context) and _has_any(text, ("抄送", "知会"))


def _is_approval_approve(text: str, context: RuntimeContext) -> bool:
    normalized = text.strip().lower().strip("。.!！?？ ")
    compact = re.sub(r"\s+", "", normalized)
    return _has_approval_ref_context(context) and (
        compact in {"通过", "通过通过", "同意", "同意同意", "批准", "批准批准"}
        or _has_any(text, ("通过第", "同意第", "审批通过", "帮我通过", "帮我同意", "通过这个", "同意这个"))
    )


def _is_approval_reject(text: str, context: RuntimeContext) -> bool:
    normalized = text.strip().lower().strip("。.!！?？ ")
    compact = re.sub(r"\s+", "", normalized)
    return _has_approval_ref_context(context) and (
        compact in {"拒绝", "拒绝拒绝", "驳回", "驳回驳回", "不同意", "不同意不同意"}
        or _has_any(text, ("拒绝第", "驳回第", "审批拒绝", "审批驳回", "帮我拒绝", "帮我驳回", "拒绝这个", "驳回这个"))
    )


def _has_approval_ref_context(context: RuntimeContext) -> bool:
    return _has_approval_result_context(context) or bool(_current_approval_item(context))


def _has_approval_result_context(context: RuntimeContext) -> bool:
    return bool(
        context.result_context
        and context.result_context.items
        and (
            context.result_context.result_type in {"approval_list", "approval_detail", "approval_query"}
            or any(_looks_like_approval_item(item) for item in context.result_context.items[:3])
        )
    )


def _looks_like_approval_item(item: dict[str, object]) -> bool:
    if not isinstance(item, dict):
        return False
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else item
    return any(
        str(raw.get(key) or "").strip()
        for key in (
            "approval_name",
            "definition_name",
            "instance_code",
            "process_code",
            "task_id",
            "serial_number",
        )
    )


def _is_task_create(text: str) -> bool:
    return _has_any(
        text,
        (
            "创建任务",
            "新建任务",
            "创建一个任务",
            "新建一个任务",
            "创建待办",
            "新建待办",
            "创建一个待办",
            "新建一个待办",
            "加个待办",
            "加一个待办",
            "记个待办",
            "记一个待办",
            "安排个任务",
            "安排一个任务",
            "提醒我",
        ),
    ) and _has_any(text, ("任务", "待办", "提醒我"))


def _task_summary(question: str) -> str:
    summary = question.strip()
    summary = re.sub(
        r"^\s*(帮我|请)?\s*(创建|新建|加|记|安排)\s*(一个|个)?\s*(任务|待办)[:：]?\s*",
        "",
        summary,
    )
    summary = re.sub(r"^\s*(帮我|请)?\s*提醒我[:：]?\s*", "", summary)
    return summary.strip() or question.strip()


def _is_task_search(text: str) -> bool:
    return _has_any(text, ("搜索任务", "搜索待办", "查找任务", "查找待办")) or (
        _has_any(text, ("任务", "待办")) and _has_any(text, ("搜索", "查找", "关键词"))
    )


def _is_task_complete(text: str) -> bool:
    return _has_any(text, ("完成任务", "完成待办", "标记完成", "设为完成", "任务完成", "待办完成", "完成第"))


def _is_task_query(text: str) -> bool:
    return (
        _has_any(text, ("我的任务", "我的待办", "待办有哪些", "任务有哪些", "查一下待办", "查一下任务", "查看待办", "查看任务"))
        or (_has_any(text, ("任务", "待办")) and _has_any(text, ("全公司", "公司", "所有", "全部", "延期", "高风险", "本周到期")))
    )


def _is_calendar_create(text: str) -> bool:
    if _has_any(text, ("会议纪要", "会议记录", "总结会议")):
        return False
    if _has_any(
        text,
        (
            "创建日程",
            "新建日程",
            "创建一个日程",
            "新建一个日程",
            "安排会议",
            "安排一个会议",
            "约个会",
            "约一个会",
            "开会",
            "建个会议",
            "建一个会议",
            "创建会议",
            "创建一个会议",
            "新建会议",
            "新建一个会议",
        ),
    ):
        return True
    return (
        _has_any(text, ("创建", "新建", "安排", "约", "建"))
        and _has_any(text, ("会议", "日程", "会"))
        and _has_any(
            text,
            (
                "今天",
                "明天",
                "后天",
                "上午",
                "中午",
                "下午",
                "晚上",
                "今晚",
                "点",
                ":",
                "：",
            ),
        )
    )


def _is_calendar_query(text: str) -> bool:
    return _has_any(
        text,
        (
            "今天日程",
            "明天日程",
            "后天日程",
            "本周日程",
            "这周日程",
            "我的日程",
            "日程安排",
            "查看日程",
            "查一下日程",
            "全公司日程",
            "公司日程",
            "所有日程",
            "全部日程",
            "今天有什么会",
            "明天有什么会",
            "后天有什么会",
            "本周有什么会",
            "这周有什么会",
        ),
    )


def _query_data_scope(text: str) -> str:
    if _has_any(text, ("全公司", "公司", "所有", "全部")):
        return "company"
    return "self"


def _is_mail_draft_create(text: str) -> bool:
    return _has_any(text, ("邮件", "email", "mail")) and _has_any(text, ("起草", "草稿", "写一封", "拟一封", "写封", "新建邮件", "创建邮件", "发邮件"))


def _is_mail_search(text: str) -> bool:
    return _has_any(text, ("搜索邮件", "查找邮件")) or (
        _has_any(text, ("邮件", "邮箱", "收件箱")) and _has_any(text, ("搜索", "查找", "关键词", "包含"))
    )


def _is_mail_query(text: str) -> bool:
    return _has_any(text, ("我的邮件", "收件箱", "查看邮件", "查一下邮件", "最近邮件", "未读邮件", "有哪些邮件", "邮件列表"))


def _is_im_send(text: str) -> bool:
    return _has_any(text, ("发给我", "发给自己", "发到这里", "发到当前会话", "发到这个群", "发消息", "发送消息", "发条信息", "发条消息", "发个信息", "发个消息", "告诉")) or (
        _has_any(text, ("发给", "发到", "发送给", "转发给")) and not _has_any(text, ("邮件", "邮箱"))
    )


def _is_im_chat_search(text: str) -> bool:
    return _has_any(text, ("搜索群", "查找群", "找群", "查群", "群聊搜索")) or (
        _has_any(text, ("群", "群聊")) and _has_any(text, ("搜索", "查找", "找一下", "查一下"))
    )


def _is_im_message_query(text: str) -> bool:
    return _has_any(text, ("查看群消息", "查群消息", "最近群消息", "聊天记录", "群聊记录"))


def _is_department_members_query(text: str) -> bool:
    return _has_any(text, ("部门", "团队", "中心", "小组")) and _has_any(
        text,
        ("有哪些人", "都有谁", "成员", "人员", "同事", "名单"),
    )


def _extract_app_token(text: str) -> str | None:
    match = _APP_TOKEN_PATTERN.search(text)
    return match.group(1) if match else None


def _people_keyword(question: str) -> str:
    keyword = question
    for token in ("公司", "的", "是谁", "谁是", "谁担任", "电话", "邮箱", "手机号", "职位", "部门", "查一下", "帮我查", "帮我", "请"):
        keyword = keyword.replace(token, "")
    return keyword.strip() or question.strip()


def _department_keyword(question: str) -> str:
    keyword = question
    for token in ("帮我", "请", "查一下", "查看", "有哪些人", "都有谁", "成员", "人员", "同事", "名单", "的"):
        keyword = keyword.replace(token, "")
    return keyword.strip() or question.strip()


def _task_search_keyword(question: str) -> str:
    keyword = question.strip()
    for token in ("帮我", "请", "搜索任务", "搜索待办", "查找任务", "查找待办", "搜索", "查找", "任务", "待办", "关键词", "：", ":"):
        keyword = keyword.replace(token, "")
    return keyword.strip()


def _task_ref(question: str, context: RuntimeContext) -> dict[str, str]:
    action_entities = _runtime_action_task_entities(context)
    if action_entities:
        return action_entities
    guid = _extract_task_guid(question)
    if guid:
        return {"task_guid": guid}
    index = _task_position_index(question)
    if index is None:
        return {}
    result_context = context.result_context
    if result_context is None or result_context.result_type != "task_list":
        return {}
    if not result_context.items:
        return {}
    item = result_context.items[index] if index >= 0 else result_context.items[-1]
    task_guid = str(item.get("guid") or item.get("task_guid") or item.get("id") or "").strip()
    title = str(item.get("title") or "").strip()
    return {"task_guid": task_guid, "title": title} if task_guid else {}


def _runtime_action_task_entities(context: RuntimeContext) -> dict[str, str]:
    for key in ("runtime_v5_confirmed_action_entities", "runtime_v5_pending_action"):
        payload = context.session_context.get(key)
        entities = payload.get("entities") if isinstance(payload, dict) and key == "runtime_v5_pending_action" else payload
        if not isinstance(entities, dict):
            continue
        task_guid = str(entities.get("task_guid") or "").strip()
        if task_guid:
            return {"task_guid": task_guid, "title": str(entities.get("title") or "").strip()}
    return {}


def _extract_task_guid(question: str) -> str | None:
    match = _TASK_GUID_PATTERN.search(question)
    return match.group(1) if match else None


def _task_position_index(question: str) -> int | None:
    if "最后" in question:
        return -1
    mapping = {
        "第一个": 0,
        "第一条": 0,
        "第一笔": 0,
        "第1个": 0,
        "第1条": 0,
        "第1笔": 0,
        "1号": 0,
        "第二个": 1,
        "第二条": 1,
        "第二笔": 1,
        "第2个": 1,
        "第2条": 1,
        "第2笔": 1,
        "2号": 1,
        "第三个": 2,
        "第三条": 2,
        "第三笔": 2,
        "第3个": 2,
        "第3条": 2,
        "第3笔": 2,
        "3号": 2,
    }
    for token, index in mapping.items():
        if token in question:
            return index
    match = re.search(r"第\s*(\d+)\s*(?:个|条|笔)?", question)
    if match:
        return max(int(match.group(1)) - 1, 0)
    return None


def _approval_ref(question: str, context: RuntimeContext) -> dict[str, object]:
    index = _task_position_index(question)
    result_context = context.result_context
    if result_context is not None and _has_approval_result_context(context):
        if index is None:
            if result_context.result_type == "approval_detail" or len(result_context.items) == 1:
                index = 0
            elif _has_any(question, ("这个", "这笔", "这条", "它")):
                index = 0
            else:
                current = _current_approval_item(context)
                if current:
                    return current
                return {}
        item = result_context.items[index] if index >= 0 else result_context.items[-1]
        return {"item": item, "index": index}

    current = _current_approval_item(context)
    if current and index is None:
        return current
    return {}


def _current_approval_item(context: RuntimeContext) -> dict[str, object]:
    current = context.session_context.get(_APPROVAL_CURRENT_KEY)
    if not isinstance(current, dict):
        return {}
    item = current.get("item")
    if not isinstance(item, dict) or not _looks_like_approval_item(item):
        return {}
    raw_index = current.get("index")
    try:
        index = int(raw_index)
    except (TypeError, ValueError):
        index = 0
    if index is None:
        index = 0
    return {"item": item, "index": index}


def _approval_comment(question: str, *, default: str) -> str:
    match = re.search(r"(?:理由|原因|备注|意见)\s*[：:]\s*(.+)$", question)
    if match:
        return match.group(1).strip()
    return default


def _approval_target_keyword(question: str) -> str:
    match = re.search(r"(?:给|转给|转交给|加签给|抄送给)\s*([^，。,；;：:\s]+)", question)
    if match:
        return match.group(1).strip()
    return ""


def _calendar_summary(question: str) -> str:
    title = _calendar_explicit_title(question)
    if title:
        return title
    summary = question.strip()
    calendar_create_tokens = (
        "帮我",
        "请",
        "创建日程",
        "新建日程",
        "创建一个日程",
        "新建一个日程",
        "安排会议",
        "约个会",
        "约一个会",
        "建个会议",
        "建一个会议",
        "创建会议",
        "创建一个会议",
        "新建会议",
        "新建一个会议",
        "安排一个会议",
        "安排个会议",
    )
    for token in sorted(calendar_create_tokens, key=len, reverse=True):
        summary = summary.replace(token, "")
    summary = summary.lstrip("：: ")
    summary = re.sub(r"(今天|明天|后天|本周|这周)?\s*(上午|中午|下午|晚上|今晚)?\s*(?:\d{1,2}|[零一二两三四五六七八九十]{1,3})(?:点|:|：)(?:半|[0-5]?\d分?)?", "", summary)
    summary = summary.replace("开会", "会议").replace("的会议", "会议").replace("一个会议", "会议")
    return summary.strip() or question.strip()


def _calendar_explicit_title(question: str) -> str:
    match = re.search(r"(?:主题|标题)\s*(?:是|为|:|：)\s*(.+)$", question)
    if not match:
        return ""
    title = match.group(1).strip()
    title = re.split(r"[。；;，,]\s*", title, maxsplit=1)[0].strip()
    return title


def _calendar_time_params(question: str) -> dict[str, str]:
    match = re.search(
        r"(\d{4}-\d{2}-\d{2}[ tT]\d{2}:\d{2}(?::\d{2})?)\s*(?:到|至|-|~)\s*(\d{4}-\d{2}-\d{2}[ tT]\d{2}:\d{2}(?::\d{2})?)",
        question,
    )
    if match:
        return {"start": _normalize_calendar_iso(match.group(1)), "end": _normalize_calendar_iso(match.group(2))}

    start = _relative_calendar_start(question)
    if start is None:
        return {}
    end = start + timedelta(hours=1)
    return {"start": _format_calendar_time(start), "end": _format_calendar_time(end)}


def _calendar_query_params(question: str) -> dict[str, str]:
    start = _relative_day_start(question)
    if start is None:
        return {}
    if "本周" in question or "这周" in question:
        end = start + timedelta(days=max(1, 7 - start.weekday()))
    else:
        end = start + timedelta(days=1)
    return {"start": _format_calendar_time(start), "end": _format_calendar_time(end)}


def _relative_calendar_start(question: str) -> datetime | None:
    day_start = _relative_day_start(question)
    if day_start is None:
        return None
    hour_minute = _calendar_hour_minute(question)
    if hour_minute is None:
        return None
    hour, minute = hour_minute
    return day_start.replace(hour=hour, minute=minute)


def _relative_day_start(question: str) -> datetime | None:
    today = datetime.now(_LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    if "后天" in question:
        return today + timedelta(days=2)
    if "明天" in question:
        return today + timedelta(days=1)
    if "今天" in question or "本周" in question or "这周" in question:
        return today
    return None


def _calendar_hour_minute(question: str) -> tuple[int, int] | None:
    match = re.search(r"(上午|中午|下午|晚上|今晚)?\s*(\d{1,2}|[零一二两三四五六七八九十]{1,3})(?:点|:|：)(半|[0-5]?\d分?)?", question)
    if not match:
        return None
    period = match.group(1) or ""
    hour = _cn_hour(match.group(2))
    minute_raw = match.group(3) or ""
    minute = 30 if minute_raw == "半" else int(minute_raw.replace("分", "") or 0)
    if period in {"下午", "晚上", "今晚"} and hour < 12:
        hour += 12
    if period == "中午" and hour < 11:
        hour += 12
    return hour, minute


def _cn_hour(value: str) -> int:
    if value.isdigit():
        return int(value)
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if value.startswith("十"):
        return 10 + digits.get(value[-1], 0)
    if "十" in value:
        left, _, right = value.partition("十")
        return digits.get(left, 0) * 10 + digits.get(right, 0)
    return digits.get(value, 0)


def _normalize_calendar_iso(value: str) -> str:
    normalized = value.replace(" ", "T")
    return normalized if "+" in normalized or normalized.endswith("Z") else f"{normalized}+08:00"


def _format_calendar_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S+08:00")


def _mail_search_keyword(question: str) -> str:
    keyword = question.strip()
    for token in ("帮我", "请", "搜索邮件", "查找邮件", "搜索", "查找", "邮件", "邮箱", "收件箱", "关键词", "包含", "：", ":"):
        keyword = keyword.replace(token, "")
    return keyword.strip()


def _mail_draft_params(question: str) -> dict[str, str]:
    params: dict[str, str] = {}
    email = _extract_email(question)
    if email:
        params["to"] = email
    subject = _extract_labeled_segment(question, ("主题", "标题"))
    if subject:
        params["subject"] = subject
    body = _extract_labeled_segment(question, ("正文", "内容", "说"))
    if body:
        params["body"] = body
    return params


def _extract_email(question: str) -> str | None:
    match = _EMAIL_PATTERN.search(question)
    return match.group(0) if match else None


def _extract_labeled_segment(question: str, labels: tuple[str, ...]) -> str:
    for label in labels:
        match = re.search(rf"{label}\s*[：:]\s*(.+?)(?=\s*(?:主题|标题|正文|内容|说)\s*[：:]|$)", question)
        if match:
            return match.group(1).strip()
    return ""


def _im_send_params(question: str, context: RuntimeContext) -> dict[str, str]:
    params: dict[str, str] = {}
    if _has_any(question, ("用机器人发", "机器人发", "以机器人", "用大飞哥发", "大飞哥通知", "系统通知", "自动推送", "自动通知")):
        params["execution_identity"] = "bot"
    elif _has_any(question, ("替我发", "用我", "以我的名义", "我发给")):
        params["execution_identity"] = "user"
    transfer_target = _extract_send_target(question)
    if transfer_target:
        target_type, target = transfer_target
        params["target_type"] = target_type
        params["target"] = target
    elif _has_any(question, ("发给我", "发给自己", "发送给我")):
        params["target_type"] = "self"
        params["user_id"] = context.identity.open_id
    elif _has_any(question, ("发到这里", "发到当前会话", "发到这个群")):
        params["target_type"] = "current_chat"
    text = _extract_message_text(question)
    if text:
        params["text"] = text
    elif transfer_target:
        text = _extract_message_text_after_target(question, transfer_target[1])
        if text:
            params["text"] = text
    elif context.result_context:
        params["text"] = _result_context_message_text(context)
        params["use_previous_result"] = "true"
    return params


def _result_delivery_target_params(question: str, context: RuntimeContext) -> dict[str, str]:
    params: dict[str, str] = {}
    transfer_target = _extract_send_target(question)
    if transfer_target:
        target_type, target = transfer_target
        params["target_type"] = target_type
        params["target"] = target
    elif _has_any(question, ("发给我", "发给自己", "发送给我", "把文件发给我")):
        params["target_type"] = "self"
        params["user_id"] = context.identity.open_id
    elif _has_any(question, ("发到这里", "发到当前会话", "发到这个群")):
        params["target_type"] = "current_chat"
    return params


def _result_context_message_text(context: RuntimeContext) -> str:
    result_context = context.result_context
    if result_context is None:
        return ""
    if result_context.items:
        lines = []
        for index, item in enumerate(result_context.items[:20], start=1):
            title = str(item.get("title") or item.get("name") or item.get("subject") or item.get("summary") or "未命名")
            parts = [f"{index}. {title}"]
            for key, label in (
                ("applicant", "申请人"),
                ("amount", "金额"),
                ("status", "状态"),
                ("department", "部门"),
                ("title", "职位"),
                ("email", "邮箱"),
                ("mobile", "手机"),
            ):
                value = item.get(key)
                if value not in (None, "", [], {}):
                    parts.append(f"{label}：{value}")
            lines.append("｜".join(parts))
        return "\n".join(lines)
    return ""


def _extract_send_target(question: str) -> tuple[str, str] | None:
    for pattern in (
        r"发(?:条|个)?(?:信息|消息)给\s*(?!我|自己)([^，,。；;:\s：]+)",
        r"发(?:送)?给\s*(?!我|自己)([^，,。；;\s]+)",
        r"转发给\s*(?!我|自己)([^，,。；;\s]+)",
        r"给\s*(?!我|自己)([^，,。；;\s]+)\s*发消息",
    ):
        match = re.search(pattern, question)
        if match:
            target = match.group(1).strip()
            if target:
                return ("chat" if any(token in target for token in ("群", "群聊")) else "person", target.replace("群聊", "").replace("群", "").strip() or target)
    chat_match = re.search(r"发(?:送)?到\s*([^，,。；;\s]+群(?:聊)?)", question)
    if chat_match:
        target = chat_match.group(1).replace("群聊", "").replace("群", "").strip()
        return ("chat", target or chat_match.group(1).strip())
    return None


def _extract_message_text(question: str) -> str:
    for pattern in (
        r"(?:说|内容是|消息是)\s*[：:]?\s*(.+)$",
        r"[：:]\s*(.+)$",
    ):
        match = re.search(pattern, question)
        if match:
            return match.group(1).strip()
    return ""


def _extract_message_text_after_target(question: str, target: str) -> str:
    if not target:
        return ""
    marker_index = question.find(target)
    if marker_index < 0:
        return ""
    tail = question[marker_index + len(target):].strip()
    tail = re.sub(r"^(?:说|内容是|消息是|通知|告诉)?\s*[：:\s，,]*", "", tail).strip()
    return tail


def _extract_between(question: str, starts: tuple[str, ...], ends: tuple[str, ...]) -> str:
    for start in starts:
        if start not in question:
            continue
        tail = question.split(start, 1)[1].strip()
        for end in ends:
            if end and end in tail:
                value = tail.split(end, 1)[0].strip()
                if value:
                    return value
        if tail:
            return tail.strip()
    return ""


def _im_chat_query(question: str) -> str:
    query = question.strip()
    for token in ("帮我", "请", "搜索群", "查找群", "找群", "查群", "群聊搜索", "搜索", "查找", "找一下", "查一下", "群聊", "群", "：", ":"):
        query = query.replace(token, "")
    return query.strip() or question.strip()


def _im_message_query_params(question: str) -> dict[str, str]:
    # First V5 batch only supports explicit chat_id for message listing.
    match = re.search(r"\b(oc_[A-Za-z0-9_-]+)\b", question)
    return {"chat_id": match.group(1), "page_size": 20} if match else {}


def _is_docs_read(text: str) -> bool:
    return bool(_DOC_TOKEN_PATTERN.search(text)) or ("文档" in text and _has_any(text, ("读取", "读一下", "看一下", "查看", "打开", "总结")))


def _is_wiki_search(text: str) -> bool:
    return _has_any(text, ("知识库", "wiki", "知识空间")) and _has_any(text, ("查", "查询", "搜索", "列出", "看一下", "有哪些"))


def _extract_doc_token(question: str) -> str:
    match = _DOC_TOKEN_PATTERN.search(question)
    return match.group(1) if match else ""


def _extract_wiki_space_id(question: str) -> str:
    match = _WIKI_SPACE_PATTERN.search(question)
    return match.group(1) if match else ""


def _is_drive_list(text: str) -> bool:
    return _has_any(text, ("云盘", "云空间", "drive", "文件夹")) and _has_any(text, ("查", "查询", "列出", "看一下", "有哪些", "文件"))


def _drive_list_params(question: str) -> dict[str, str]:
    match = re.search(r"\b(fld[-A-Za-z0-9_]+)\b", question)
    return {"folder_token": match.group(1)} if match else {}


def _is_slides_read(text: str) -> bool:
    return bool(_SLIDES_URL_TOKEN_PATTERN.search(text) or _SLIDES_TOKEN_PATTERN.search(text)) and _has_any(text, ("读取", "读一下", "查看", "看一下", "总结", "分析", "幻灯片", "ppt", "slides"))


def _extract_slides_token(question: str) -> str:
    url_match = _SLIDES_URL_TOKEN_PATTERN.search(question)
    if url_match:
        return url_match.group(1)
    match = _SLIDES_TOKEN_PATTERN.search(question)
    return match.group(1) if match else ""


def _is_whiteboard_read(text: str) -> bool:
    return bool(_WHITEBOARD_TOKEN_PATTERN.search(text)) and _has_any(text, ("读取", "读一下", "查看", "看一下", "总结", "分析", "画板", "whiteboard"))


def _extract_whiteboard_token(question: str) -> str:
    match = _WHITEBOARD_TOKEN_PATTERN.search(question)
    return match.group(1) if match else ""


def _is_vc_meeting_search(text: str) -> bool:
    return _has_any(text, ("历史会议", "会议记录", "视频会议", "会议列表", "最近会议")) and _has_any(
        text,
        ("查", "查询", "搜索", "看一下", "查看", "最近", "有哪些"),
    )


def _is_attendance_query(text: str) -> bool:
    return _has_any(text, ("考勤", "打卡", "出勤")) and _has_any(text, ("查", "查询", "看一下", "查看", "最近", "记录", "今天", "本周"))


def _is_okr_query(text: str) -> bool:
    return _has_any(text, ("okr", "目标", "关键结果")) and _has_any(text, ("查", "查询", "看一下", "查看", "我的", "当前", "进展"))


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)
