from dataclasses import dataclass
from typing import Any


FAST_ROUTES = {"general_chat"}
THINKING_ROUTES = {
    "owner_cockpit",
    "company_qa",
    "domain_qa",
    "chat_summary",
    "chat_tasks",
    "personal_tasks",
    "task_qa",
    "mail_qa",
}

REPLY_MODE_ROUTE_EXAMPLES = {
    "fast": ("general_chat",),
    "normal": ("calendar_qa", "bitable_qa", "feishu_im_message_list"),
    "thinking": ("company_qa", "domain_qa", "chat_summary", "mail_qa", "task_qa"),
}


def _coerce_execution_category(candidate: Any | None) -> str | None:
    if not isinstance(candidate, str):
        return None
    normalized = candidate.strip().lower()
    if normalized not in {"query", "analysis", "decision", "action"}:
        return None
    return normalized


@dataclass(frozen=True)
class AgentReplyMode:
    mode_id: str
    label: str
    data_requirement: str
    trigger: str
    response_contract: tuple[str, ...]
    enterprise_data_required: bool
    tool_strategy: str
    show_thinking_map: bool = False
    pre_reply_required: bool = False

    @property
    def style_hint(self) -> str:
        contract = "；".join(self.response_contract)
        return f"回复模式：{self.label}。数据要求：{self.data_requirement}。触发条件：{self.trigger}。规则：{contract}。"


def resolve_agent_reply_mode(
    *,
    route_path: str,
    route_label: str,
    planned_tool_count: int = 0,
    execution_category: str | None = None,
) -> AgentReplyMode:
    normalized_category = _coerce_execution_category(execution_category)
    if normalized_category in {"analysis", "decision"}:
        return AgentReplyMode(
            mode_id="thinking",
            label="Thinking（需要分析数据）",
            data_requirement="WorkEvent / Knowledge / Memory / 多 Tool 分析",
            trigger="用户需要决策判断或风险分析，先输出依据再给建议",
            response_contract=(
                "说明分析来源和数据边界",
                "先给判断依据，再给建议",
                "标注未决事项和建议动作",
            ),
            enterprise_data_required=True,
            tool_strategy="analysis_before_decision",
            show_thinking_map=True,
            pre_reply_required=True,
        )

    if normalized_category == "action":
        return AgentReplyMode(
            mode_id="normal",
            label="Action（需要执行）",
            data_requirement="实时企业数据 / 写操作执行",
            trigger="用户要求创建、更新、提交或其他可变更企业状态动作",
            response_contract=(
                "先说明执行预检查或确认边界",
                "执行后返回结果与影响范围",
                "给出下一步操作建议",
            ),
            enterprise_data_required=True,
            tool_strategy="single_or_multi_write_execution",
            show_thinking_map=False,
            pre_reply_required=False,
        )

    if planned_tool_count > 1 or route_path in THINKING_ROUTES:
        return AgentReplyMode(
            mode_id="thinking",
            label="Thinking（需要分析数据）",
            data_requirement="WorkEvent / Knowledge / Memory / 多 Tool 分析",
            trigger="需要分析企业沉淀数据、长期记忆或多个工具结果",
            response_contract=(
                "回复栏先显示一个思考动图/思考路径",
                "先说明使用了哪些数据层或工具",
                "再给结论、依据和下一步",
            ),
            enterprise_data_required=True,
            tool_strategy="multi_tool_or_data_layer_analysis",
            show_thinking_map=True,
            pre_reply_required=True,
        )
    if route_path in FAST_ROUTES:
        return AgentReplyMode(
            mode_id="fast",
            label="Fast（无需企业数据）",
            data_requirement="纯推理 / 简单外部查询",
            trigger="无需读取企业数据即可回答",
            response_contract=(
                "直接回答",
                "不伪装读取企业数据",
                "不展示思考路径",
            ),
            enterprise_data_required=False,
            tool_strategy="pure_reasoning_or_simple_external_lookup",
        )
    return AgentReplyMode(
        mode_id="normal",
        label="normal（实时企业数据）",
        data_requirement="MCP/CLI / 单 Tool",
        trigger=f"{route_label or route_path} 需要实时企业数据或单工具结果",
        response_contract=(
            "通过 Tool 获取实时企业数据",
            "简洁给出结果",
            "不展示思考路径",
        ),
        enterprise_data_required=True,
        tool_strategy="single_business_tool",
    )


def reply_mode_payload(mode: AgentReplyMode) -> dict[str, Any]:
    return {
        "mode_id": mode.mode_id,
        "label": mode.label,
        "data_requirement": mode.data_requirement,
        "trigger": mode.trigger,
        "response_contract": list(mode.response_contract),
        "enterprise_data_required": mode.enterprise_data_required,
        "tool_strategy": mode.tool_strategy,
        "show_thinking_map": mode.show_thinking_map,
        "pre_reply_required": mode.pre_reply_required,
    }


def reply_mode_catalog() -> list[dict[str, Any]]:
    modes = [
        resolve_agent_reply_mode(route_path="general_chat", route_label="基础沟通"),
        resolve_agent_reply_mode(route_path="calendar_qa", route_label="日程问答"),
        resolve_agent_reply_mode(route_path="company_qa", route_label="公司级问答"),
    ]
    return [
        {
            **reply_mode_payload(mode),
            "route_examples": list(REPLY_MODE_ROUTE_EXAMPLES.get(mode.mode_id, ())),
        }
        for mode in modes
    ]


def thinking_map_text(*, route_label: str, scope_label: str, reply_mode: AgentReplyMode) -> str:
    if not reply_mode.show_thinking_map:
        return ""
    return (
        "思考路径：\n"
        f"1. 识别问题范围：{scope_label}\n"
        f"2. 选择能力路径：{route_label}\n"
        f"3. 调用数据层/工具：{reply_mode.data_requirement}\n"
        "4. 汇总结论、依据和下一步"
    )


def thinking_preview_payload(*, route_label: str, scope_label: str, reply_mode: AgentReplyMode) -> dict[str, Any] | None:
    if not reply_mode.pre_reply_required:
        return None
    frames = [
        {"step": "scope", "label": "识别范围", "text": f"问题范围：{scope_label}"},
        {"step": "route", "label": "选择路径", "text": f"能力路径：{route_label}"},
        {"step": "data", "label": "调用数据", "text": f"数据/工具：{reply_mode.data_requirement}"},
        {"step": "answer", "label": "生成回复", "text": "输出结论、依据和下一步"},
    ]
    return {
        "kind": "thinking_flow_animation",
        "presentation": "animated_thinking_map",
        "send_before_final_answer": True,
        "frames": frames,
        "data_layers": ["WorkEvent", "Knowledge", "Memory"],
        "tool_strategy": reply_mode.tool_strategy,
        "final_answer_owner": "agent_runtime",
    }
