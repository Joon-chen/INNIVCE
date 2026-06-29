from dataclasses import dataclass
import re
from typing import Any, Callable

from app.services.agent.policies import BotAnswerRoute
from app.services.agent.complex_task_matrix import should_allow_query_to_bitable_route
from app.services.agent.query_bitable_lexicon import is_ambiguous_bitable_query, is_strong_bitable_query
from app.services.tools.router import TOOL_REGISTRY


_ORG_TERM_PATTERN = re.compile(r"组织|组织架构|组织结构|部门|员工")
_TABLE_TERM_PATTERN = re.compile(r"表格|多维表格|bitable|base|表")
_CREATE_TERM_PATTERN = re.compile(
    r"创建|建立|新建|建表|建一|建张|建个|新建一个|新建一张|做一|做张|做个|弄一|弄张|弄个|起一|起张|起个|整一|整张"
)
_IMPORT_TERM_PATTERN = re.compile(r"放入|写入|导入|填入|装入|放到|塞进|放进|放进去|写进|写到|同步|更新|写入到|更新到")
_NEGATED_IMPORT_TERM_PATTERN = re.compile(r"不要写入|不写入|先不要写|不要导入|不导入|不要同步|不同步")
_EXPORT_TERM_PATTERN = re.compile(r"导出|导成|导到|导出到")
_SHARE_BACK_TERM_PATTERN = re.compile(r"发我|发给我|给我发|发到我|发一下|发一份|回我|推送我|同步给我|发我一份|私信我|发我这个")
_QUERY_TERM_PATTERN = re.compile(r"查|查询|查看|看看|筛选|列出|列举|找|搜|搜索|看下|看一看|看下|同步|抽取")
_APP_TOKEN_PATTERN = re.compile(r"(bascn[-A-Za-z0-9_]+)")
_TABLE_TARGET_TERM_PATTERN = re.compile(r"表|表格|多维表格|bitable|base|电子表格")
_TASK_ACTION_TERM_PATTERN = re.compile(
    r"创建|新建|建立|修改|更新|删除|移除|指派|分配|认领|催办|催促|安排|转交|转办|转派|加派|派发|完成|开启|关闭|开始|挂起|恢复|重开|验收|重提|交付|延期|加急|优先|重发|标记|归档|取消|复制|作废"
)
_APPROVAL_ACTION_TERM_PATTERN = re.compile(
    r"批复|同意|拒绝|驳回|批准|加签|抄送|通过|否决|反对|审批|起草|草拟|作废|终止|重提|提交|打回|退回|撤回|撤销"
)
_APPROVAL_STRONG_ACTION_TERM_PATTERN = re.compile(r"拒绝|驳回|打回|撤回|撤销|加签|批准|同意|批复")
_MAIL_ACTION_TERM_PATTERN = re.compile(
    r"发送|上传|下载|回复|回信|下发|抄送|转交|转发|群发|转寄|归档|标记|已读|未读|配置"
)
_MAIL_STRONG_ACTION_TERM_PATTERN = re.compile(r"上传|发送(?!人)|回复|转发|归档|标记")
_ACTION_TERM_PATTERN = re.compile(
    r"创建|新建|修改|更新|删除|移除|发起|提交|发送|同步|批复|同意|驳回|上传|下载|覆盖|覆盖掉|推送|配置|写入|放入|塞入|迁移|复制|转移|转发|安排|加|批准|拒绝|加签|抄送|转交|催办|催促|认领|指派|分配|完成|归档|关闭|开启|评论|回复|回信|下发|转办|打回|退回|撤回|撤销|转派|通过|否决|反对|审批|起草|草拟|群发|转寄|作废|终止|重开|启用|停用|挂起|恢复|验收|重提|交付|延期|加急|优先|重发|标记|已读|未读|取消|开始|加派|派发"
)
_TASK_QUERY_ACTION_NOISE_TERM_PATTERN = re.compile(
    r"开始时间|截止时间|截止日期|截止日|开始日期|预计开始|预计截止|预计完成|到期时间|完成时间|状态|进度|当前进度|进展|优先级|负责人|处理人|发起人|创建者|提交人|是否开始|是否完成|是否关闭|是否结束|是否延期|是否重开|是否复盘|超期|逾期"
)
_APPROVAL_QUERY_ACTION_NOISE_TERM_PATTERN = re.compile(
    r"状态|进度|当前进度|办理进度|处理进度|详情|结果|是否|报销状态|当前状态|审批状态|审批结果|处理结果|发起人|提交人|创建者|处理人|申请人|审批人|截止时间|截止日期|截止日|到期时间|完成时间"
)
_MAIL_QUERY_ACTION_NOISE_TERM_PATTERN = re.compile(
    r"是否|状态|详情|结果|已读状态|未读状态|是否已读|是否未读|收件|发送人|收件人|发件人|主题|标题|时间|附件|超期|逾期"
)
_DECISION_TERM_PATTERN = re.compile(r"该不该|要不要|要不|应该|是否合理|是否要|是否需要|是否值得|是否可行|最好|可否|可不可以|能不能|行不行|帮我判断|帮我决|选择|选哪个|选哪种|对比|比较后|建议")
_ANALYSIS_TERM_PATTERN = re.compile(r"分析|对比|趋势|原因|影响|评估|风险|异常|统计|总结|汇总|为什么|问题|情况|现状|进展|复盘|回顾")
_DEFAULT_ORGANIZATION_BATABLE_FIELDS = [
    {"name": "部门", "type": "text"},
    {"name": "用户", "type": "text"},
    {"name": "直属上级", "type": "text"},
]
_DEFAULT_QUERY_BI_TABLE_FIELDS = [
    {"name": "记录", "type": "text"},
]


@dataclass(frozen=True)
class AgentPlanStep:
    kind: str
    name: str
    purpose: str
    metadata: dict[str, Any]
    required: bool = True
    on_error: str = "stop"
    depends_on: tuple[str, ...] = ()
    permitted: bool = True
    deny_reason: str | None = None


@dataclass(frozen=True)
class AgentPlan:
    route_path: str
    max_steps: int
    steps: tuple[AgentPlanStep, ...]
    execution_category: str = "query"
    execution_category_source: str = "route_fallback"

    @property
    def requires_confirmation(self) -> bool:
        return any(bool(step.metadata.get("requires_confirmation")) for step in self.steps)


@dataclass(frozen=True)
class AgentPlanTemplate:
    name: str
    route_paths: tuple[str, ...]
    matches: Callable[..., bool]
    build_plan: Callable[..., tuple[AgentPlanStep, ...]]


def _build_organization_snapshot_plan(
    *,
    route: BotAnswerRoute,
    semantic_text: str,
    **kwargs: Any,
) -> tuple[AgentPlanStep, ...]:
    del route
    del semantic_text
    del kwargs
    return (
        AgentPlanStep(
            kind="tool",
            name="feishu_contact_organization_snapshot",
            purpose="读取组织快照。",
            metadata={
                "provider": TOOL_REGISTRY["feishu_contact_organization_snapshot"].provider.value,
                "required_permissions": list(TOOL_REGISTRY["feishu_contact_organization_snapshot"].required_permissions),
                "supports_write": False,
                "tool_params": {
                    "max_departments": 100,
                    "max_users": 500,
                    "response_format": "raw_json",
                },
                "plan_context_keys": ["app_token", "users", "departments", "organization_rows"],
            },
        ),
    )


def _build_meeting_search_plan(
    *,
    route: BotAnswerRoute,
    semantic_text: str,
    **kwargs: Any,
) -> tuple[AgentPlanStep, ...]:
    """Build a plan for meeting search with optimized params."""
    del route
    del semantic_text
    del kwargs
    return (
        AgentPlanStep(
            kind="tool",
            name="feishu_vc_meeting_search",
            purpose="搜索会议记录并分析会议主题、参会人和产物。",
            metadata={
                "provider": TOOL_REGISTRY["feishu_vc_meeting_search"].provider.value,
                "required_permissions": list(TOOL_REGISTRY["feishu_vc_meeting_search"].required_permissions),
                "supports_write": False,
                "tool_params": {
                    "page_size": 20,
                    "response_format": "raw_json",
                },
                "plan_context_keys": ["meetings", "meeting_count", "summaries"],
            },
        ),
    )


def _build_task_overview_plan(
    *,
    route: BotAnswerRoute,
    semantic_text: str,
    **kwargs: Any,
) -> tuple[AgentPlanStep, ...]:
    """Build a plan for task overview with categorization context."""
    del semantic_text
    del kwargs
    return (
        AgentPlanStep(
            kind="tool",
            name=route.path,
            purpose="查询待办任务并整理分类概览。",
            metadata={
                "provider": TOOL_REGISTRY[route.path].provider.value,
                "required_permissions": list(TOOL_REGISTRY[route.path].required_permissions),
                "supports_write": False,
                "tool_params": {
                    "page_size": 50,
                    "status": "open",
                    "response_format": "raw_json",
                },
                "plan_context_keys": ["tasks", "task_count", "task_categories"],
            },
        ),
    )


def build_agent_plan(
    *,
    route: BotAnswerRoute,
    semantic: Any,
    max_steps: int = 3,
    allow_write_tools: bool = True,
    require_write_confirmation: bool = True,
) -> AgentPlan:
    """Build a declarative plan for runtime execution and trace visibility.

    The planner does not call tools itself. Runtime consumes tool steps through
    the existing Tool Router path and write permission gates.
    """

    bounded_steps = min(max(int(max_steps or 3), 1), 10)
    execution_category, execution_category_source = resolve_execution_category_with_source(route=route, semantic=semantic)
    semantic_text = (
        " ".join(
            filter(
                None,
                (
                    getattr(semantic, "canonical_question", None),
                    getattr(semantic, "name", None),
                    getattr(semantic, "source", None),
                    str(semantic) if semantic is not None else None,
                ),
            )
        )
        .lower()
        .strip()
    )
    steps: list[AgentPlanStep] = [
        AgentPlanStep(
            kind="guardrail",
            name="permission_scope",
            purpose="按用户身份、授权范围和路由结果约束可访问数据。",
            metadata={
                "route_scope": route.scope,
                "route_reason": route.reason,
                "semantic_intent": getattr(semantic, "source", None) or getattr(semantic, "name", None) or "semantic",
                "execution_category": execution_category,
                "execution_category_source": execution_category_source,
            },
        )
    ]
    if route.denied:
        steps.append(
            AgentPlanStep(
                kind="stop",
                name="permission_denied",
                purpose="权限不足时停止检索和工具调用，只返回拒绝说明。",
                metadata={"message": route.message},
            )
        )
        return AgentPlan(
            route_path=route.path,
            max_steps=bounded_steps,
            steps=tuple(steps[:bounded_steps]),
            execution_category=execution_category,
            execution_category_source=execution_category_source,
        )

    definition = TOOL_REGISTRY.get(route.path)
    template = _select_workflow_template(
        route=route,
        semantic=semantic,
        execution_category=execution_category,
    )
    if template is not None and template.name == "query_to_bitable_table" and not allow_write_tools:
        template = None
    if template is not None:
        steps.extend(
            template.build_plan(
                route=route,
                semantic_text=semantic_text,
                allow_write_tools=allow_write_tools,
                require_write_confirmation=require_write_confirmation,
            )
        )
    elif definition:
        steps.append(
            AgentPlanStep(
                kind="tool",
                name=route.path,
                purpose=f"调用{route.capability_name}能力生成回答。",
                metadata={
                    "provider": definition.provider.value,
                    "required_permissions": list(definition.required_permissions),
                    "supports_write": definition.supports_write,
                    "requires_confirmation": definition.supports_write
                    and allow_write_tools
                    and require_write_confirmation,
                    "requires_dry_run": definition.supports_write and allow_write_tools,
                    "confirmed_execution_requires": _confirmed_execution_requirements(
                        supports_write=definition.supports_write,
                        allow_write_tools=allow_write_tools,
                    ),
                    "allow_write_tools": allow_write_tools,
                    "write_policy": _write_policy_status(
                        supports_write=definition.supports_write,
                        allow_write_tools=allow_write_tools,
                        require_write_confirmation=require_write_confirmation,
                    ),
                },
            )
        )
    else:
        steps.append(
            AgentPlanStep(
                kind="advisor",
                name="answer_advisor_question",
                purpose="使用顾问问答服务在已授权范围内生成回答。",
                metadata={"route_scope": route.scope},
            )
        )
    steps.append(
        AgentPlanStep(
            kind="answer",
            name="finalize_answer",
            purpose="补充回答范围标签，并按身份配置改写最终表达。",
            metadata={"route_label": route.capability_name},
        )
    )
    return AgentPlan(
        route_path=route.path,
        max_steps=bounded_steps,
        steps=tuple(steps[:bounded_steps]),
        execution_category=execution_category,
        execution_category_source=execution_category_source,
    )


def _extract_organization_text(*, semantic: Any) -> str:
    return str(
        " ".join(
            filter(
                None,
                (
                    getattr(semantic, "canonical_question", None),
                    getattr(semantic, "name", None),
                    getattr(semantic, "source", None),
                    str(semantic) if semantic is not None else None,
                ),
            )
        )
    ).lower().strip()


def _organization_to_bitable_table_intent(
    semantic_text: str,
    **_: Any,
) -> bool:
    if not (_ORG_TERM_PATTERN.search(semantic_text) and _TABLE_TERM_PATTERN.search(semantic_text)):
        return False
    if _NEGATED_IMPORT_TERM_PATTERN.search(semantic_text):
        return False
    return (
        _CREATE_TERM_PATTERN.search(semantic_text) is not None
        and _IMPORT_TERM_PATTERN.search(semantic_text) is not None
    )


def _query_to_bitable_table_intent(
    semantic_text: str,
    *,
    route: BotAnswerRoute | None = None,
    execution_category: str | None = None,
) -> bool:
    if route is not None and not should_allow_query_to_bitable_route(
        route_hint=route.path,
        execution_category=execution_category,
    ):
        return False
    if _organization_to_bitable_table_intent(semantic_text):
        return False
    if _ORG_TERM_PATTERN.search(semantic_text) and _TABLE_TERM_PATTERN.search(semantic_text):
        return False
    strong_intent = is_strong_bitable_query(semantic_text)
    if strong_intent:
        return True
    if is_ambiguous_bitable_query(semantic_text):
        return False
    return False


def _is_noisy_action_query(route: BotAnswerRoute, semantic_text: str) -> bool:
    if route.path in {"task_qa", "personal_tasks", "chat_tasks"}:
        return _TASK_QUERY_ACTION_NOISE_TERM_PATTERN.search(semantic_text) is not None
    if route.path in {"feishu_approval_task_query", "approval_qa"}:
        return _APPROVAL_QUERY_ACTION_NOISE_TERM_PATTERN.search(semantic_text) is not None
    if route.path == "mail_qa":
        return _MAIL_QUERY_ACTION_NOISE_TERM_PATTERN.search(semantic_text) is not None
    return False


def _organization_snapshot_intent(
    semantic_text: str,
    **_: Any,
) -> bool:
    if _organization_to_bitable_table_intent(semantic_text):
        return False
    return _ORG_TERM_PATTERN.search(semantic_text) is not None


def _meeting_search_intent(
    semantic_text: str,
    **_: Any,
) -> bool:
    """Detect meeting search/analysis intent from semantic text."""
    if not semantic_text:
        return False
    terms = ("会议", "纪要", "开会", "妙记", "逐字稿", "会议记录", "会议总结", "会后总结")
    return any(term in semantic_text for term in terms)


def _task_overview_intent(
    semantic_text: str,
    **_: Any,
) -> bool:
    """Detect task overview/analysis intent (not action intent)."""
    if not semantic_text:
        return False
    overview_terms = ("整理", "归类", "分类", "概览", "全部", "所有", "回顾")
    task_terms = ("待办", "任务", "事项", "我的", "跟进")
    return any(t in semantic_text for t in overview_terms) and any(t in semantic_text for t in task_terms)


def _query_table_name(semantic_text: str) -> str:
    sanitized = re.sub(r"\b(请|帮我|创建|建一|建张|建个|新建|新建一个|新建一张|弄一|弄个|做一|做张|做个|起一|起张|起个|整一|整张)\b", "", semantic_text)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    if len(sanitized) > 16:
        sanitized = sanitized[:16]
    if not sanitized:
        return "查询结果导出"
    return f"{sanitized}快照"


def _select_workflow_template(
    *,
    route: BotAnswerRoute,
    semantic: Any,
    execution_category: str | None = None,
) -> AgentPlanTemplate | None:
    semantic_text = _extract_organization_text(semantic=semantic)
    if not semantic_text:
        return None
    if execution_category is None:
        execution_category = resolve_execution_category(route=route, semantic=semantic)
    for template in WORKFLOW_TEMPLATES:
        if route.path not in template.route_paths:
            continue
        if template.matches(
            semantic_text,
            route=route,
            execution_category=execution_category,
        ):
            return template
    return None


def classify_execution_category(*, route: BotAnswerRoute, semantic: Any) -> str:
    semantic_text = _extract_organization_text(semantic=semantic).lower()

    if _DECISION_TERM_PATTERN.search(semantic_text):
        return "decision"
    if route.path == "bitable_qa" and _organization_to_bitable_table_intent(semantic_text) and "整" in semantic_text:
        return "query"
    if route.path == "approval_qa" and _APPROVAL_STRONG_ACTION_TERM_PATTERN.search(semantic_text):
        return "action"
    if route.path == "mail_qa" and _MAIL_STRONG_ACTION_TERM_PATTERN.search(semantic_text):
        return "action"
    if route.path in {"mail_qa", "chat_tasks"} and _query_to_bitable_table_intent(
        semantic_text,
        route=route,
        execution_category="query",
    ):
        return "query"
    if route.path in {"task_qa", "personal_tasks", "chat_tasks"} and _TASK_ACTION_TERM_PATTERN.search(semantic_text):
        if not _is_noisy_action_query(route=route, semantic_text=semantic_text):
            return "action"
    elif route.path in {"feishu_approval_task_query", "approval_qa"}:
        if _is_noisy_action_query(route=route, semantic_text=semantic_text):
            return "query" if route.path == "feishu_approval_task_query" else "analysis"
        if _APPROVAL_ACTION_TERM_PATTERN.search(semantic_text):
            return "action"
    elif route.path == "mail_qa" and _MAIL_ACTION_TERM_PATTERN.search(semantic_text):
        if not _is_noisy_action_query(route=route, semantic_text=semantic_text):
            return "action"
    elif _ACTION_TERM_PATTERN.search(semantic_text):
        if not _is_noisy_action_query(route=route, semantic_text=semantic_text):
            return "action"
    if _ANALYSIS_TERM_PATTERN.search(semantic_text):
        return "analysis"

    definition = TOOL_REGISTRY.get(route.path)
    if definition is not None and definition.supports_write:
        return "action"

    if route.path in {"company_qa", "domain_qa", "chat_summary", "bitable_qa", "task_qa"}:
        return "analysis"
    if route.path in {"feishu_approval_task_query", "personal_tasks", "mail_qa"}:
        return "query"
    if route.path == "approval_qa":
        return "analysis"
    return "query"


def _coerce_execution_category(candidate: Any) -> str | None:
    if not isinstance(candidate, str):
        return None

    normalized = candidate.strip().lower()
    if normalized not in {"query", "analysis", "decision", "action"}:
        return None
    return normalized


def resolve_execution_category(*, route: BotAnswerRoute, semantic: Any) -> str:
    execution_category = getattr(semantic, "execution_category", None)
    normalized_category = _coerce_execution_category(execution_category)
    if normalized_category is not None:
        return normalized_category
    return classify_execution_category(route=route, semantic=semantic)


def resolve_execution_category_with_source(*, route: BotAnswerRoute, semantic: Any) -> tuple[str, str]:
    execution_category = getattr(semantic, "execution_category", None)
    normalized_category = _coerce_execution_category(execution_category)
    if normalized_category is not None:
        return normalized_category, "semantic"
    return classify_execution_category(route=route, semantic=semantic), "route_fallback"


def _organization_to_bitable_table_workflow_needed(*, route: BotAnswerRoute, semantic: Any) -> bool:
    # legacy helper used by tests and callers that still expect route/semantic style checks
    if route.path not in {"bitable_qa", "feishu_contact_organization_snapshot"}:
        return False
    if semantic is None:
        return False
    return _organization_to_bitable_table_intent(_extract_organization_text(semantic=semantic))


def _build_organization_to_bitable_plan(
    *,
    route: BotAnswerRoute,
    semantic_text: str,
    allow_write_tools: bool,
    require_write_confirmation: bool,
) -> tuple[AgentPlanStep, ...]:
    app_token = _extract_first_bitable_app_token(semantic_text)
    create_name = "最新组织快照"
    shared_base = {"app_token": app_token} if app_token else {}
    share_back = _should_share_bitable_result_back(semantic_text)

    steps = [
        AgentPlanStep(
            kind="tool",
            name="feishu_contact_organization_snapshot",
            purpose="读取最新组织信息（包含部门和人员）。",
            metadata={
                "provider": TOOL_REGISTRY["feishu_contact_organization_snapshot"].provider.value,
                "required_permissions": list(TOOL_REGISTRY["feishu_contact_organization_snapshot"].required_permissions),
                "supports_write": False,
                "tool_params": {
                    "max_departments": 100,
                    "max_users": 500,
                    "response_format": "raw_json",
                },
                "plan_context_keys": ["app_token", "organization_rows", "departments", "users"],
            },
        ),
        AgentPlanStep(
            kind="tool",
            name="feishu_bitable_table_create",
            purpose="按组织快照创建新表。",
            depends_on=("feishu_contact_organization_snapshot",),
            metadata={
                "provider": TOOL_REGISTRY["feishu_bitable_table_create"].provider.value,
                "required_permissions": list(TOOL_REGISTRY["feishu_bitable_table_create"].required_permissions),
                "supports_write": True,
                "requires_confirmation": allow_write_tools and require_write_confirmation,
                "requires_dry_run": allow_write_tools,
                "confirmed_execution_requires": _confirmed_execution_requirements(
                    supports_write=True,
                    allow_write_tools=allow_write_tools,
                ),
                "allow_write_tools": allow_write_tools,
                "write_policy": _write_policy_status(
                    supports_write=True,
                    allow_write_tools=allow_write_tools,
                    require_write_confirmation=require_write_confirmation,
                ),
                "tool_params": {
                    "app_token": "${shared.app_token}",
                    **shared_base,
                    "name": create_name,
                    "fields": _DEFAULT_ORGANIZATION_BATABLE_FIELDS,
                    "response_format": "raw_json",
                },
                "plan_context_keys": ["app_token", "table_id"],
            },
        ),
        AgentPlanStep(
            kind="tool",
            name="feishu_bitable_record_batch_create",
            purpose="写入组织数据到新建表。",
            depends_on=("feishu_bitable_table_create",),
            on_error="continue",
            metadata={
                "provider": TOOL_REGISTRY["feishu_bitable_record_batch_create"].provider.value,
                "required_permissions": list(TOOL_REGISTRY["feishu_bitable_record_batch_create"].required_permissions),
                "supports_write": True,
                "requires_confirmation": allow_write_tools and require_write_confirmation,
                "requires_dry_run": allow_write_tools,
                "confirmed_execution_requirements": _confirmed_execution_requirements(
                    supports_write=True,
                    allow_write_tools=allow_write_tools,
                ),
                "allow_write_tools": allow_write_tools,
                "write_policy": _write_policy_status(
                    supports_write=True,
                    allow_write_tools=allow_write_tools,
                    require_write_confirmation=require_write_confirmation,
                ),
                "tool_params": {
                    "app_token": "${shared.app_token}",
                    "table_id": "${shared.table_id}",
                    "fields": ["部门", "用户", "直属上级"],
                    "rows": "${shared.organization_rows}",
                    "response_format": "raw_json",
                },
                "plan_context_keys": ["app_token", "table_id", "organization_rows"],
            },
        ),
    ]
    if share_back:
        steps.append(
            _build_bitable_share_result_step(
                table_name=create_name,
                depends_on=("feishu_bitable_table_create",),
                allow_write_tools=allow_write_tools,
                require_write_confirmation=require_write_confirmation,
            )
        )
    return tuple(steps)


def _build_query_to_bitable_table_plan(
    *,
    route: BotAnswerRoute,
    semantic_text: str,
    allow_write_tools: bool,
    require_write_confirmation: bool,
) -> tuple[AgentPlanStep, ...]:
    query_tool_name = route.path
    app_token = _extract_first_bitable_app_token(semantic_text)
    create_name = _query_table_name(semantic_text)
    shared_base = {"app_token": app_token} if app_token else {}
    share_back = _should_share_bitable_result_back(semantic_text)

    steps = [
        AgentPlanStep(
            kind="tool",
            name=query_tool_name,
            purpose="先查询基础数据，供后续表格写入使用。",
            metadata={
                "provider": TOOL_REGISTRY[query_tool_name].provider.value,
                "required_permissions": list(TOOL_REGISTRY[query_tool_name].required_permissions),
                "supports_write": False,
                "tool_params": {
                    "response_format": "raw_json",
                },
                "plan_context_keys": ["query_rows", "query_fields", "app_token"],
            },
        ),
        AgentPlanStep(
            kind="tool",
            name="feishu_bitable_table_create",
            purpose="按查询结果创建新表。",
            depends_on=(query_tool_name,),
            metadata={
                "provider": TOOL_REGISTRY["feishu_bitable_table_create"].provider.value,
                "required_permissions": list(TOOL_REGISTRY["feishu_bitable_table_create"].required_permissions),
                "supports_write": True,
                "requires_confirmation": allow_write_tools and require_write_confirmation,
                "requires_dry_run": allow_write_tools,
                "confirmed_execution_requires": _confirmed_execution_requirements(
                    supports_write=True,
                    allow_write_tools=allow_write_tools,
                ),
                "allow_write_tools": allow_write_tools,
                "write_policy": _write_policy_status(
                    supports_write=True,
                    allow_write_tools=allow_write_tools,
                    require_write_confirmation=require_write_confirmation,
                ),
                "tool_params": {
                    "app_token": "${shared.app_token}",
                    **shared_base,
                    "name": create_name,
                    "fields": "${shared.query_fields}",
                    "response_format": "raw_json",
                },
                "plan_context_keys": ["app_token", "table_id"],
            },
        ),
        AgentPlanStep(
            kind="tool",
            name="feishu_bitable_record_batch_create",
            purpose="将查询到的清单写入新表。",
            depends_on=(query_tool_name, "feishu_bitable_table_create"),
            required=False,
            on_error="continue",
            metadata={
                "provider": TOOL_REGISTRY["feishu_bitable_record_batch_create"].provider.value,
                "required_permissions": list(TOOL_REGISTRY["feishu_bitable_record_batch_create"].required_permissions),
                "supports_write": True,
                "requires_confirmation": allow_write_tools and require_write_confirmation,
                "requires_dry_run": allow_write_tools,
                "confirmed_execution_requirements": _confirmed_execution_requirements(
                    supports_write=True,
                    allow_write_tools=allow_write_tools,
                ),
                "allow_write_tools": allow_write_tools,
                "write_policy": _write_policy_status(
                    supports_write=True,
                    allow_write_tools=allow_write_tools,
                    require_write_confirmation=require_write_confirmation,
                ),
                "tool_params": {
                    "app_token": "${shared.app_token}",
                    "table_id": "${shared.table_id}",
                    "fields": "${shared.query_fields}",
                    "rows": "${shared.query_rows}",
                    "response_format": "raw_json",
                },
                "plan_context_keys": ["app_token", "table_id", "query_rows"],
            },
        ),
    ]
    if share_back:
        steps.append(
            _build_bitable_share_result_step(
                table_name=create_name,
                depends_on=("feishu_bitable_table_create",),
                allow_write_tools=allow_write_tools,
                require_write_confirmation=require_write_confirmation,
            )
        )
    return tuple(steps)


def _build_bitable_share_result_step(
    *,
    table_name: str,
    depends_on: tuple[str, ...],
    allow_write_tools: bool,
    require_write_confirmation: bool,
) -> AgentPlanStep:
    return AgentPlanStep(
        kind="tool",
        name="feishu_im_send_message",
        purpose="发送建表结果给用户。",
        required=False,
        on_error="continue",
        depends_on=depends_on,
        metadata={
            "provider": TOOL_REGISTRY["feishu_im_send_message"].provider.value,
            "required_permissions": list(TOOL_REGISTRY["feishu_im_send_message"].required_permissions),
            "supports_write": True,
            "requires_confirmation": allow_write_tools and require_write_confirmation,
            "requires_dry_run": allow_write_tools,
            "confirmed_execution_requires": _confirmed_execution_requirements(
                supports_write=True,
                allow_write_tools=allow_write_tools,
            ),
            "allow_write_tools": allow_write_tools,
            "write_policy": _write_policy_status(
                supports_write=True,
                allow_write_tools=allow_write_tools,
                require_write_confirmation=require_write_confirmation,
            ),
            "tool_params": {
                "chat_id": "${shared.chat_id}",
                "user_id": "${shared.user_id}",
                "text": f"已完成「{table_name}」建表。app_token=${{shared.app_token}}，table_id=${{shared.table_id}}。",
            },
            "plan_context_keys": ["chat_id", "user_id", "app_token", "table_id"],
        },
    )


def _should_share_bitable_result_back(semantic_text: str, **_: Any) -> bool:
    return _SHARE_BACK_TERM_PATTERN.search(semantic_text) is not None


WORKFLOW_TEMPLATES: tuple[AgentPlanTemplate, ...] = (
    AgentPlanTemplate(
        name="organization_to_bitable_table",
        route_paths=("bitable_qa", "feishu_contact_organization_snapshot"),
        matches=_organization_to_bitable_table_intent,
        build_plan=_build_organization_to_bitable_plan,
    ),
    AgentPlanTemplate(
        name="query_to_bitable_table",
        route_paths=("bitable_qa", "task_qa", "feishu_approval_task_query", "approval_qa", "mail_qa", "personal_tasks", "chat_tasks", "feishu_vc_meeting_search"),
        matches=_query_to_bitable_table_intent,
        build_plan=_build_query_to_bitable_table_plan,
    ),
    AgentPlanTemplate(
        name="organization_snapshot",
        route_paths=("bitable_qa", "feishu_contact_organization_snapshot"),
        matches=_organization_snapshot_intent,
        build_plan=_build_organization_snapshot_plan,
    ),
    AgentPlanTemplate(
        name="meeting_search_analysis",
        route_paths=("feishu_vc_meeting_search",),
        matches=_meeting_search_intent,
        build_plan=_build_meeting_search_plan,
    ),
    AgentPlanTemplate(
        name="task_overview_analysis",
        route_paths=("personal_tasks", "task_qa"),
        matches=_task_overview_intent,
        build_plan=_build_task_overview_plan,
    ),
)


def _extract_first_bitable_app_token(text: str) -> str | None:
    match = _APP_TOKEN_PATTERN.search(text or "")
    return match.group(1) if match else None


def _write_policy_status(*, supports_write: bool, allow_write_tools: bool, require_write_confirmation: bool) -> str:
    if not supports_write:
        return "read_only"
    if not allow_write_tools:
        return "disabled"
    if require_write_confirmation:
        return "confirmation_required"
    return "allowed"


def _confirmed_execution_requirements(*, supports_write: bool, allow_write_tools: bool) -> list[str]:
    if not supports_write or not allow_write_tools:
        return []
    return ["dry_run=true", "confirmed=true", "confirmation_token"]


def agent_plan_payload(plan: AgentPlan) -> dict[str, Any]:
    return {
        "route_path": plan.route_path,
        "max_steps": plan.max_steps,
        "requires_confirmation": plan.requires_confirmation,
        "execution_category": plan.execution_category,
        "execution_category_source": plan.execution_category_source,
        "steps": [
            {
                "kind": step.kind,
                "name": step.name,
                "purpose": step.purpose,
                "metadata": step.metadata,
                "required": step.required,
                "on_error": step.on_error,
                "depends_on": list(step.depends_on),
            }
            for step in plan.steps
        ],
    }
