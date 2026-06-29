import json
import os
import re
from dataclasses import dataclass, replace
from typing import Any

from app.core.config import settings
from app.services.agent.complex_task_matrix import QUERY_TO_BITABLE_ROUTE_HINTS, should_allow_query_to_bitable_route
from app.services.agent.intents import classify_bot_intent
from app.services.agent.query_bitable_lexicon import is_ambiguous_bitable_query, is_strong_bitable_query
from app.services.llm.gateway import LLMGateway


# Session cache for guided dialogue (clarification → user response → execution)
import json as _json
import redis as _redis
_redis_client = _redis.Redis.from_url(settings.redis_url, decode_responses=True)
_SESSION_TTL = 600  # 10 minutes
_BUSINESS_TTL = 600  # 10 minutes (longer for business context)


def _session_key(chat_id: str) -> str:
    return f"feishu:session:{chat_id}"


def _biz_key(chat_id: str) -> str:
    return f"feishu:biz:{chat_id}"


def _save_session(chat_id: str, data: dict) -> None:
    try:
        _redis_client.setex(_session_key(chat_id), _SESSION_TTL, _json.dumps(data, ensure_ascii=False))
        if data.get("route_hint") not in {"general_chat", "基础沟通"}:
            _redis_client.setex(_biz_key(chat_id), _BUSINESS_TTL, _json.dumps(data, ensure_ascii=False))
    except Exception:
        pass  # Redis unavailable — session not persisted


def _load_session(chat_id: str) -> dict | None:
    try:
        raw = _redis_client.get(_session_key(chat_id))
        if raw:
            return _json.loads(raw)
    except Exception:
        pass
    return None


def _clear_session(chat_id: str) -> None:
    try:
        _redis_client.delete(_session_key(chat_id), _biz_key(chat_id))
    except Exception:
        pass
ROUTE_HINTS = {
    "company_qa",
    "chat_summary",
    "chat_tasks",
    "personal_tasks",
    "domain_qa",
    "feishu_approval_task_query",
    "public_knowledge_qa",
    "chat_qa",
    "general_chat",
    "calendar_qa",
    "feishu_calendar_create_event",
    "feishu_vc_meeting_search",
    "feishu_contact_organization_snapshot",
    "feishu_contact_department_users",
    "feishu_contact_user_search",
    "feishu_contact_user_get",
    "feishu_task_create",
   "mail_qa",
   "bitable_qa",
   "task_qa",
    "personal_tasks",
}
MODULE_HINTS = {
    "today-focus",
    "risks",
    "tasks",
    "approvals",
    "projects",
    "decisions",
    "communications",
    "meetings",
    "resources",
    "reports",
}


@dataclass(frozen=True)
class SemanticIntent:
    route_hint: str | None = None
    module_hint: str | None = None
    canonical_question: str | None = None
    confidence: float = 0.0
    source: str = "none"
    execution_category: str = "query"
    clarification: str | None = None
    session_context: dict | None = None
    entities: dict | None = None
    missing_params: list[str] | None = None


_ACTION_TERM_PATTERN = re.compile(
    r"创建|新建|建一|建张|建个|新建一个|新建一张|修改|更新|删除|移除|发起|提交|发送|同步|上传|下载|覆盖|覆盖掉|推送|配置|写入|放入|塞入|迁移|复制|转移|转发|安排|加|创建|建表|建表|表格|填写|填充|批准|批复|同意|驳回|拒绝|加签|抄送|转交|催办|催促|认领|指派|分配|完成|归档|关闭|开启|评论|回复|回信|下发|转办|打回|撤回"
)
_DECISION_TERM_PATTERN = re.compile(r"该不该|要不要|要不|应该|最好|可否|是否|可不可以|能不能|行不行|帮我判断|帮我决|选择|选哪个|选哪种|对比|比较后|建议")
_ANALYSIS_TERM_PATTERN = re.compile(r"分析|对比|趋势|原因|影响|评估|风险|异常|统计|总结|汇总|为什么|问题|情况|现状|进展|复盘|回顾|可疑|隐患|波动")
_ORG_TERM_PATTERN = re.compile(r"组织|组织架构|组织结构|部门|员工")
_TABLE_TERM_PATTERN = re.compile(r"表|表格|多维表格|bitable|base")
_CREATE_TERM_PATTERN = re.compile(
    r"创建|新建|建表|建一|建张|建个|新建一个|新建一张|起一|起张|起个|整一|整张|弄一|弄个|做一|做张|做个|起一张|建张"
)
_IMPORT_TERM_PATTERN = re.compile(r"放入|写入|导入|填入|装入|放到|塞进|放进|放进去|同步|更新|写入到|更新到")
_EXPORT_TERM_PATTERN = re.compile(r"导出|导成|导到|导出到")
_QUERY_TERM_PATTERN = re.compile(r"查|查询|查看|看看|筛选|列出|列举|找|搜|搜索|看下|看一看|看下|同步|抽取")
_TABLE_TARGET_TERM_PATTERN = re.compile(r"表|表格|多维表格|bitable|base|电子表格|sheet|工作表")
_ANALYSIS_ROUTE_HINTS = {"company_qa", "domain_qa", "chat_summary", "bitable_qa", "task_qa"}
_QUERY_ROUTE_HINTS = {"feishu_approval_task_query", "personal_tasks", "mail_qa"}
_PENDING_APPROVAL_TRIGGER_TERMS = (
    "待审批",
    "待审",
    "待批",
    "待办",
    "待我",
    "待我处理",
    "待我同意",
    "待我审批",
    "待我审核",
    "待我批复",
    "需要我",
    "要我",
    "我批",
    "我审",
    "我同意",
)
_PENDING_APPROVAL_SUBJECT_TERMS = (
    "审批",
    "批复",
    "单子",
    "付款",
    "报销",
    "用章",
    "合同",
    "申请",
    "申请单",
    "审批申请",
    "报销申请",
    "付款申请",
)


def _coerce_execution_category(value: Any | None) -> str | None:
    normalized = str(value).strip().lower() if isinstance(value, str) else ""
    return normalized if normalized in {"query", "analysis", "decision", "action"} else None


def _coerce_query_to_bitable_route(intent: SemanticIntent, *, text: str) -> SemanticIntent:
    route_hint = intent.route_hint
    if route_hint == "bitable_qa":
        return intent
    if route_hint in QUERY_TO_BITABLE_ROUTE_HINTS and _is_query_to_bitable_text(text):
        execution_category = _coerce_execution_category(intent.execution_category) or classify_execution_category_from_text(
            text=text,
            route_hint=route_hint,
        )
        if not should_allow_query_to_bitable_route(
            route_hint=route_hint,
            execution_category=execution_category,
        ):
            return intent
        return SemanticIntent(
            route_hint="bitable_qa",
            module_hint=intent.module_hint or "resources",
            canonical_question=intent.canonical_question,
            confidence=max(float(intent.confidence), 0.9),
            source=intent.source,
            execution_category=intent.execution_category,
        )
    return intent


def semantic_intent_for_question(*, question: str, normalized_command: str, actor: Any, chat_id: str | None = None) -> SemanticIntent:
    session = _load_session(chat_id) if chat_id else None
    heuristic = _heuristic_intent(question=question, normalized_command=normalized_command, actor=actor)
    heuristic = _coerce_query_to_bitable_route(heuristic, text=f"{question} {normalized_command}".lower())
    # Skip LLM intent for clear greetings — heuristic more reliable, avoids session pollution
    _skip_llm = heuristic.route_hint == "general_chat" and heuristic.confidence >= 0.85
    if settings.bot_llm_semantics_enabled and not _running_tests() and not _skip_llm:
        llm_intent = _llm_intent(question=question, normalized_command=normalized_command, actor=actor, session_context=session)
        if llm_intent.route_hint is not None:
            chosen = llm_intent
            if chat_id and chosen.route_hint not in {"general_chat", "基础沟通"}:
                _save_session(chat_id, {
                    "question": question,
                    "normalized_command": normalized_command,
                    "route_hint": chosen.route_hint or "",
                    "session_context": chosen.session_context or {},
                    "clarification": chosen.clarification or "",
                    "canonical_question": chosen.canonical_question or "",
                })
            if chosen.clarification and (chosen.confidence < 0.6 or (chosen.missing_params and len(chosen.missing_params) > 0)):
                return _attach_execution_category(chosen, question=question, normalized_command=normalized_command)
        else:
            chosen = heuristic if heuristic.confidence > 0 else heuristic
    else:
        chosen = heuristic if heuristic.confidence >= 0.85 else heuristic
    chosen = _coerce_query_to_bitable_route(chosen, text=f"{question} {normalized_command}".lower())
    # Classification corrections
    if chosen.route_hint == "company_qa" and any(w in question for w in ("建议", "你觉得", "你相信", "聊", "聊聊", "闲聊")):
        chosen = replace(chosen, route_hint="general_chat", source="post_process")
    return _attach_execution_category(chosen, question=question, normalized_command=normalized_command)


def _heuristic_intent(*, question: str, normalized_command: str, actor: Any) -> SemanticIntent:
    intent = classify_bot_intent(question, normalized_command=normalized_command)
    text = f"{question} {normalized_command}".lower()
    if _contains(text, ("审批", "付款", "报销", "用章", "合同", "请假", "单子")) and _contains(
        text,
        ("拒绝", "驳回", "退回", "打回", "撤回", "加签", "转交", "抄送", "催办"),
    ):
        return SemanticIntent(
            route_hint="approval_qa",
            module_hint="approvals",
            canonical_question=question,
            confidence=0.92,
            source="heuristic",
            execution_category="action",
        )
    if _contains(text, ("任务", "待办")) and _contains(text, ("转办",)):
        return SemanticIntent(
            route_hint="task_qa",
            module_hint="tasks",
            canonical_question=question,
            confidence=0.9,
            source="heuristic",
            execution_category="action",
        )
    if _contains(text, ("任务", "待办")) and (_DECISION_TERM_PATTERN.search(text) or _ANALYSIS_TERM_PATTERN.search(text)):
        if _contains(text, ("群", "群里", "这个群", "当前群")) or getattr(actor, "access_scope", "") == "chat":
            route_hint = "chat_tasks"
        else:
            route_hint = "personal_tasks" if _contains(text, ("我的", "我负责", "待我")) else "task_qa"
        return SemanticIntent(
            route_hint=route_hint,
            module_hint="tasks",
            canonical_question=question,
            confidence=0.9,
            source="heuristic",
            execution_category=classify_execution_category_from_text(text=text, route_hint=route_hint),
        )
    if intent.confidence >= 0.85:
        return SemanticIntent(
            route_hint=intent.route_hint,
            module_hint=intent.module_hint,
            canonical_question=intent.canonical_question or question,
            confidence=intent.confidence,
            source="intent_rules",
            execution_category=getattr(intent, "execution_category", None),
        )

    # Greeting + business mixed queries should NOT go to general_chat
    _business_kw = ("审批", "总经理", "公司", "组织架构", "职位", "电话", "邮箱", "谁", "多少", "哪", "什么", "信息")
    if _contains(text, ("你好", "好呀", "在吗", "还在吗", "不理我", "没回复", "没有回复", "什么情况", "卡住", "掉线", "离线")):
        if not any(w in text for w in _business_kw):
            return SemanticIntent(
                route_hint="general_chat",
                canonical_question=question,
                confidence=0.9,
                source="heuristic",
                execution_category="query",
            )
    # Self-info queries: route to user get with actor's open_id
    _self_keywords = ("我的", "我是谁", "我本人", "本人", "我自己的")
    _self_fields = ("职位", "电话", "邮箱", "姓名", "手机", "邮件", "职务", "角色")
    if _contains(text, _self_keywords) and any(w in question for w in _self_fields):
        _display = getattr(actor, "display_name", "") or getattr(actor, "name", "") or ""
        return SemanticIntent(
            route_hint="feishu_contact_user_get",
            canonical_question=f"{_display} {question}",
            confidence=0.92,
            source="heuristic",
            execution_category="query",
        )
    # Self-info queries: route to user get with actor's open_id
    _self_keywords = ("我的", "我是谁", "本人", "我本人", "我自己的")
    _self_fields = ("职位", "电话", "邮箱", "姓名", "手机", "邮件", "职务", "角色")
    if _contains(text, _self_keywords) and any(w in question for w in _self_fields):
        _name = getattr(actor, "display_name", "") or getattr(actor, "name", "") or ""
        return SemanticIntent(
            route_hint="feishu_contact_user_get",
            canonical_question=f"{_name} {question}",
            confidence=0.92,
            source="heuristic",
            execution_category="query",
        )
    # Self-info queries
    _self_kw = ("我的", "我是谁", "本人")
    _self_fields = ("职位", "电话", "邮箱", "姓名", "手机", "邮件")
    if _contains(text, _self_kw) and any(w in question for w in _self_fields):
        _sn = getattr(actor, "display_name", "") or getattr(actor, "name", "") or ""
        if not _sn:
            pass  # fall through to normal search
        elif question == "我是谁":
            return SemanticIntent(route_hint="general_chat", canonical_question="你是谁", confidence=0.92, source="heuristic", execution_category="query")
        else:
            return SemanticIntent(route_hint="feishu_contact_user_get", canonical_question=question, confidence=0.92, source="heuristic", execution_category="query")
    if _contains(text, _PENDING_APPROVAL_TRIGGER_TERMS) and _contains(text, _PENDING_APPROVAL_SUBJECT_TERMS):
        return SemanticIntent(
            route_hint="feishu_approval_task_query",
            module_hint="approvals",
            canonical_question="待我处理的审批",
            confidence=0.92,
            source="heuristic",
            execution_category=None,
        )
    if _contains(text, ("审批", "付款", "报销", "用章", "合同", "请假", "待审", "单子")):
        return SemanticIntent(
            route_hint="feishu_approval_task_query",
            module_hint="approvals",
            canonical_question=question,
            confidence=0.9,
            source="heuristic",
            execution_category=None,
        )
    if _contains(text, ("任务", "待办")) and (_DECISION_TERM_PATTERN.search(text) or _ANALYSIS_TERM_PATTERN.search(text)):
        if _contains(text, ("群", "群里", "这个群", "当前群")) or getattr(actor, "access_scope", "") == "chat":
            route_hint = "chat_tasks"
        else:
            route_hint = "personal_tasks" if _contains(text, ("我的", "我负责", "待我")) else "task_qa"
        return SemanticIntent(
            route_hint=route_hint,
            module_hint="tasks",
            canonical_question=question,
            confidence=0.9,
            source="heuristic",
            execution_category=classify_execution_category_from_text(text=text, route_hint=route_hint),
        )
    if _contains(text, ("风险", "预警", "隐患", "异常", "逾期")):
        return SemanticIntent(
            route_hint="company_qa",
            module_hint="risks",
            canonical_question=question,
            confidence=0.88,
            source="heuristic",
            execution_category="analysis",
        )
    if (
        _ORG_TERM_PATTERN.search(text)
        and _CREATE_TERM_PATTERN.search(text)
        and _IMPORT_TERM_PATTERN.search(text)
        and _TABLE_TERM_PATTERN.search(text)
    ):
        return SemanticIntent(
            route_hint="bitable_qa",
            module_hint="resources",
            canonical_question=question,
            confidence=0.95,
            source="heuristic",
            execution_category="action",
        )
    if _is_query_to_bitable_text(text):
        return SemanticIntent(
            route_hint="bitable_qa",
            module_hint="resources",
            canonical_question=question,
            confidence=0.9,
            source="heuristic",
            execution_category="action",
        )
    if _contains(text, ("项目", "进度", "交付", "里程碑", "测试验证")):
        return SemanticIntent(
            route_hint="bitable_qa",
            module_hint="projects",
            canonical_question=question,
            confidence=0.88,
            source="heuristic",
            execution_category="analysis",
        )
    if _contains(text, ("日报", "周报", "月报", "报告", "总结")):
        return SemanticIntent(
            route_hint="company_qa",
            module_hint="reports",
            canonical_question=question,
            confidence=0.88,
            source="heuristic",
            execution_category="analysis",
        )
    if _contains(text, ("今天", "今日", "重点", "盯一下", "关注什么", "现在怎么样", "公司怎么样")):
        return SemanticIntent(
            route_hint="company_qa",
            module_hint="today-focus",
            canonical_question="今日经营重点",
            confidence=0.78,
            source="heuristic",
            execution_category="analysis",
        )
    if getattr(actor, "access_scope", "") in {"personal", "self", "user"} or _contains(text, ("我的", "我负责", "待我")):
        return SemanticIntent(
            route_hint="personal_tasks",
            module_hint="tasks",
            canonical_question=question,
            confidence=0.78,
            source="heuristic",
            execution_category="query",
        )
    return SemanticIntent(canonical_question=question, confidence=0.0, source="none", execution_category="query")


def _llm_intent(*, question: str, normalized_command: str, actor: Any, session_context: dict | None = None) -> SemanticIntent:
    session_block = ""
    if session_context:
        clarification = session_context.get("clarification", "") or ""
        question_text = session_context.get("question", "") or ""
        if clarification:
            session_block = f"""
上一次追问上下文：
- 原问题：{question_text}
- 追问：{clarification}
- 当前用户回复：{question}
- 根据原问题+追问+用户回复，判断最终意图。
"""
        else:
            prev_route = session_context.get("route_hint", "")
            session_block = f"""
前文参考（仅作参考）：
- 上一条问题：{question_text}
- 上一条路径：{prev_route}
- 当前用户输入：{question}
如果当前问题明显是上一条的后续追问（如"展开"、"详情"、"具体内容"、"展示出来"），优先使用上一条路径。
"""
    prompt = f"""你只做机器人问题的语义分类，不读取数据库，不生成答案。
可用工具（route_hint）：
- general_chat：问候、闲聊、时间等常识问题
- company_qa：经营分析、趋势、风险、日报等综合问答
- feishu_approval_task_query：查询待审批任务
- feishu_contact_organization_snapshot：通讯录/组织架构相关查询。按部门、性别、人数等条件查询人员信息，如"多少人""多少男生""女性有几位"
- feishu_contact_department_users：按部门查人员
- feishu_contact_user_search：搜索具体人员（如"总经理是谁"）
- feishu_contact_user_get：获取人员详细信息（手机号、邮箱等），需要 open_id
- calendar_qa：日程、日历
- mail_qa：邮件
- bitable_qa：多维表格（Base/Bitable）的增删改查。仅限表格本身的操作，不负责人员/组织/通讯录查询
- feishu_task_create：创建任务
- feishu_vc_meeting_search：会议记录
- chat_summary：聊天总结
- personal_tasks：个人待办
- chat_qa：不确定时走 chat_qa

用户身份：role={getattr(actor, "role", "")}, access_scope={getattr(actor, "access_scope", "")}, domains={list(getattr(actor, "domains", ()) or ())}

输出规则：
1. 如果问题明确，直接输出 route_hint 和 canonical_question
2. 如果问题模糊、有歧义（如"显示部门人员"但没说哪个部门），输出 clarification 追问用户
3. 如果收到 clarification 后的用户回复，结合上下文判断最终意图
route_hint 必须严格匹配以上列表中的一项。
{session_block}
- 只改写问题为 canonical_question，不加入事实。

用户身份：role={getattr(actor, "role", "")}, access_scope={getattr(actor, "access_scope", "")}, domains={list(getattr(actor, "domains", ()) or ())}
原始问题：{question[:300]}
规则归一：{normalized_command[:300]}

JSON格式：
{{"route_hint": "...", "module_hint": "...", "canonical_question": "...", "execution_category": "...", "confidence": 0.0, "entities": {{}}, "missing_params": []}}
entities：从问题中提取的关键实体（如部门名称、人员姓名）。没有则为空对象。
missing_params：问题不明确时缺失的参数列表（如["department_name"]）。没有则为空数组。
"""
    import concurrent.futures as _cf
    text = ""
    if settings.deepseek_api_key:
        try:
            with _cf.ThreadPoolExecutor(max_workers=1) as _p:
                _f = _p.submit(lambda: LLMGateway().complete_deepseek_text(prompt, temperature=0.0))
                text = _f.result(timeout=15) or ""
        except Exception:
            text = ""
    if not text:
        text = LLMGateway().complete_text(prompt, temperature=0.0) or ""
    try:
        data = _parse_json_object(text)
    except Exception:
        return SemanticIntent(canonical_question=question, confidence=0.0, source="llm_error")
    route_hint = data.get("route_hint") if data.get("route_hint") in ROUTE_HINTS else None
    module_hint = data.get("module_hint") if data.get("module_hint") in MODULE_HINTS else None
    canonical_question = str(data.get("canonical_question") or question).strip()[:300]
    try:
        confidence = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    clarification = str(data.get("clarification") or "").strip() or None
    session_ctx = data.get("session_context") if isinstance(data.get("session_context"), dict) else None
    entities = data.get("entities") if isinstance(data.get("entities"), dict) else None
    missing = data.get("missing_params") if isinstance(data.get("missing_params"), list) else None
    return SemanticIntent(
        route_hint=route_hint,
        module_hint=module_hint,
        canonical_question=canonical_question or question,
        execution_category=_coerce_execution_category(data.get("execution_category"))
        or classify_execution_category_from_text(
            text=f"{question} {normalized_command}",
            route_hint=route_hint,
        ),
        clarification=clarification,
        session_context=session_ctx,
        entities=entities,
        missing_params=missing,
        confidence=max(0.0, min(confidence, 1.0)),
        source="llm",
    )


def _is_query_to_bitable_text(text: str) -> bool:
    return is_strong_bitable_query(text) and not is_ambiguous_bitable_query(text)


def classify_execution_category_from_text(
    *,
    text: str,
    route_hint: str | None = None,
) -> str:
    semantic_text = text.lower()
    if _DECISION_TERM_PATTERN.search(semantic_text):
        return "decision"
    if _contains(semantic_text, ("分析", "对比", "趋势", "原因", "影响", "评估", "风险", "异常", "统计", "总结", "汇总", "复盘", "隐患", "预警")):
        return "analysis"
    if _ACTION_TERM_PATTERN.search(semantic_text):
        return "action"
    if _contains(
        semantic_text,
        ("待我", "待处理", "待审", "待我审批", "待我处理", "待我批复", "待我同意", "待办", "待我审核", "待我批", "待我批核"),
    ) and _contains(semantic_text, ("审批", "待我", "付款", "报销", "用章", "合同", "申请")):
        return "query"
    if route_hint in _ANALYSIS_ROUTE_HINTS:
        return "analysis"
    if route_hint in _QUERY_ROUTE_HINTS:
        return "query"
    return "query"


def _attach_execution_category(intent: SemanticIntent, *, question: str, normalized_command: str) -> SemanticIntent:
    text = f"{question} {normalized_command}"
    return SemanticIntent(
        route_hint=intent.route_hint,
        module_hint=intent.module_hint,
        canonical_question=intent.canonical_question,
        confidence=round(max(0.0, min(float(intent.confidence), 1.0)), 2),
        source=intent.source,
        execution_category=_coerce_execution_category(intent.execution_category)
        or classify_execution_category_from_text(
            text=text,
            route_hint=intent.route_hint,
        ),
    )


def _parse_json_object(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return {}
    data = json.loads(match.group(0))
    return data if isinstance(data, dict) else {}


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    return any(term.lower() in text for term in terms)


def _running_tests() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))
