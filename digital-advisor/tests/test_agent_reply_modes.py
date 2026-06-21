from app.services.agent.reply_modes import (
    reply_mode_catalog,
    resolve_agent_reply_mode,
    thinking_map_text,
    thinking_preview_payload,
)


def test_reply_mode_fast_for_no_enterprise_data() -> None:
    mode = resolve_agent_reply_mode(route_path="general_chat", route_label="基础沟通")

    assert mode.mode_id == "fast"
    assert mode.label == "Fast（无需企业数据）"
    assert mode.show_thinking_map is False
    assert mode.enterprise_data_required is False
    assert mode.tool_strategy == "pure_reasoning_or_simple_external_lookup"
    assert "纯推理" in mode.data_requirement


def test_reply_mode_action_category_prefers_execution_mode() -> None:
    mode = resolve_agent_reply_mode(
        route_path="feishu_task_create",
        route_label="创建任务",
        execution_category="action",
    )

    assert mode.mode_id == "normal"
    assert mode.label == "Action（需要执行）"
    assert mode.show_thinking_map is False
    assert mode.pre_reply_required is False
    assert mode.tool_strategy == "single_or_multi_write_execution"


def test_reply_mode_normalizes_category_input() -> None:
    mode = resolve_agent_reply_mode(
        route_path="feishu_task_create",
        route_label="创建任务",
        execution_category="  Action ",
    )

    assert mode.mode_id == "normal"
    assert mode.label == "Action（需要执行）"


def test_reply_mode_normal_for_realtime_single_tool() -> None:
    mode = resolve_agent_reply_mode(route_path="feishu_calendar_create_event", route_label="日程创建")

    assert mode.mode_id == "normal"
    assert mode.label == "normal（实时企业数据）"
    assert mode.show_thinking_map is False
    assert mode.enterprise_data_required is True
    assert mode.tool_strategy == "single_business_tool"
    assert "MCP/CLI" in mode.data_requirement


def test_reply_mode_thinking_for_workevent_knowledge_memory_analysis() -> None:
    mode = resolve_agent_reply_mode(route_path="company_qa", route_label="公司级问答")

    assert mode.mode_id == "thinking"
    assert mode.label == "Thinking（需要分析数据）"
    assert mode.show_thinking_map is True
    assert mode.pre_reply_required is True
    assert mode.tool_strategy == "multi_tool_or_data_layer_analysis"
    assert "WorkEvent / Knowledge / Memory" in mode.data_requirement
    assert thinking_map_text(route_label="公司级问答", scope_label="指定公司", reply_mode=mode).startswith("思考路径：")
    preview = thinking_preview_payload(route_label="公司级问答", scope_label="指定公司", reply_mode=mode)
    assert preview is not None
    assert preview["kind"] == "thinking_flow_animation"
    assert preview["send_before_final_answer"] is True
    assert [item["label"] for item in preview["frames"]] == ["识别范围", "选择路径", "调用数据", "生成回复"]


def test_reply_mode_thinking_for_multi_tool_plan() -> None:
    mode = resolve_agent_reply_mode(route_path="feishu_calendar_create_event", route_label="日程创建", planned_tool_count=2)

    assert mode.mode_id == "thinking"
    assert mode.show_thinking_map is True


def test_reply_mode_catalog_exposes_three_v5_modes() -> None:
    items = reply_mode_catalog()

    assert [item["mode_id"] for item in items] == ["fast", "normal", "thinking"]
    assert items[0]["route_examples"] == ["general_chat"]
    assert items[0]["enterprise_data_required"] is False
    assert items[2]["show_thinking_map"] is True
    assert items[2]["pre_reply_required"] is True
