from __future__ import annotations

from app.services.runtime_v5.models import (
    ComposedAnswer,
    ExecutionResult,
    IntentResult,
    PermissionDecision,
    ResultFollowup,
    RuntimeContext,
)


def compose_answer(
    *,
    context: RuntimeContext,
    intent: IntentResult,
    permission: PermissionDecision,
    execution: ExecutionResult | None,
    followup: ResultFollowup | None = None,
) -> ComposedAnswer:
    if followup and followup.is_result_followup:
        return _compose_followup(context=context, followup=followup)

    if intent.intent == "smalltalk":
        return ComposedAnswer(answer="我在。你可以继续让我查审批、任务、日程、邮件或通讯录。")

    if intent.intent == "action_trace":
        return ComposedAnswer(answer=_action_trace_answer(context))

    if intent.intent == "runtime_status":
        return ComposedAnswer(answer=_runtime_status_answer(context))

    if intent.intent == "governance_view":
        return ComposedAnswer(answer="治理视图由机器人入口层生成，不进入 Runtime 执行链路。")

    if intent.needs_clarification:
        return ComposedAnswer(answer=_clarification_text(intent))

    if not permission.allowed:
        return ComposedAnswer(answer="这部分数据或操作当前没有权限访问。")

    if execution is None:
        return ComposedAnswer(answer="我已理解问题，但还没有执行结果。")

    if execution.status == "partial":
        partial_answer = _partial_answer(execution)
        if partial_answer:
            return ComposedAnswer(
                answer=partial_answer,
                result_context=execution.result_context,
                metadata={"strategy": execution.strategy, "execution_status": execution.status},
            )

    if execution.status in {"error", "partial", "denied"}:
        problem_answer = _problem_answer(execution)
        if problem_answer:
            return ComposedAnswer(
                answer=problem_answer,
                result_context=execution.result_context,
                metadata={"strategy": execution.strategy, "execution_status": execution.status},
            )

    if intent.intent == "approval_detail":
        answer = "\n\n".join(
            _human_readable_answer(item.answer)
            for item in execution.provider_results
            if item.status == "success" and _human_readable_answer(item.answer)
        ).strip()
        if answer:
            return ComposedAnswer(
                answer=answer,
                result_context=execution.result_context,
                metadata={"strategy": execution.strategy},
            )

    if intent.question_type in {"analysis", "decision"}:
        answer = _analysis_or_decision_answer(intent=intent, execution=execution)
        if answer:
            return ComposedAnswer(
                answer=answer,
                result_context=execution.result_context,
                metadata={"strategy": execution.strategy},
            )

    if execution.result_context and execution.result_context.answer:
        metadata = execution.result_context.metadata if isinstance(execution.result_context.metadata, dict) else {}
        if metadata.get("empty_result"):
            next_step = str(metadata.get("recommended_next_step") or "").strip()
            answer = _human_readable_answer(execution.result_context.answer) or "没有查到结构化结果。"
            if next_step:
                answer = f"{answer}\n\n建议：{next_step}"
            return ComposedAnswer(
                answer=answer,
                result_context=execution.result_context,
                metadata={"strategy": execution.strategy},
            )
        answer = _human_readable_answer(execution.result_context.answer)
        if not answer:
            answer = _result_context_items_answer(execution.result_context)
        if not answer:
            answer = "已取得结构化结果，但暂时无法生成可读摘要。你可以继续问「展开」或「第一个详情」。"
        return ComposedAnswer(
            answer=_with_followup_hint(_with_command_enrichment(answer, intent=intent), execution.result_context),
            result_context=execution.result_context,
            metadata={"strategy": execution.strategy},
        )

    successful = [item for item in execution.provider_results if item.status == "success"]
    if successful:
        answer = "\n\n".join(_human_readable_answer(item.answer) for item in successful if _human_readable_answer(item.answer)).strip()
        if not answer and execution.result_context:
            answer = _result_context_items_answer(execution.result_context)
        if answer:
            return ComposedAnswer(
                answer=_with_followup_hint(_with_command_enrichment(answer, intent=intent), execution.result_context),
                result_context=execution.result_context,
                metadata={"strategy": execution.strategy},
            )

    errors = [_human_readable_error(item.error) for item in execution.provider_results if item.error]
    errors = [item for item in errors if item]
    if errors:
        return ComposedAnswer(answer=errors[0])
    return ComposedAnswer(answer="暂时没有查到可用结果。")


def _with_command_enrichment(answer: str, *, intent: IntentResult) -> str:
    enrichment = _command_enrichment(intent)
    if not enrichment:
        return answer
    objective = str(enrichment.get("objective") or "").strip()
    output_preferences = enrichment.get("output_preferences") if isinstance(enrichment.get("output_preferences"), dict) else {}
    preference_text = _output_preference_text(output_preferences)
    tags = enrichment.get("semantic_tags") if isinstance(enrichment.get("semantic_tags"), list) else []
    tag_text = "、".join(str(item).strip() for item in tags[:3] if str(item).strip())
    context_parts = []
    if objective:
        context_parts.append(f"目标：{objective}")
    if preference_text:
        context_parts.append(f"视图：{preference_text}")
    if tag_text:
        context_parts.append(f"关注：{tag_text}")
    if not context_parts:
        return answer
    text = str(answer or "").strip()
    if not text:
        return "；".join(context_parts)
    if text.startswith("目标："):
        return answer
    return f"{'；'.join(context_parts)}。\n{text}"


def _command_enrichment(intent: IntentResult) -> dict:
    entities = intent.entities if isinstance(intent.entities, dict) else {}
    enrichment = entities.get("command_enrichment")
    return enrichment if isinstance(enrichment, dict) else {}


def _output_preference_text(preferences: dict) -> str:
    labels = []
    detail_level = str(preferences.get("detail_level") or "").strip()
    if detail_level:
        labels.append({"summary": "摘要", "detail": "明细", "detailed": "明细"}.get(detail_level, detail_level))
    group_by = str(preferences.get("group_by") or "").strip()
    if group_by:
        labels.append(f"按{_group_by_label(group_by)}分组")
    sort_by = str(preferences.get("sort_by") or "").strip()
    if sort_by:
        labels.append(f"按{_group_by_label(sort_by)}排序")
    return "，".join(labels)


def _group_by_label(value: str) -> str:
    return {
        "owner": "负责人",
        "assignee": "负责人",
        "department": "部门",
        "risk": "风险",
        "due": "截止时间",
        "status": "状态",
        "time": "时间",
    }.get(value, value)


def _with_followup_hint(answer: str, result_context) -> str:
    if result_context is None or not getattr(result_context, "items", ()):
        return answer
    text = answer.strip()
    if not text or "可回复" in text or "可以回复" in text:
        return answer
    metadata = result_context.metadata if isinstance(result_context.metadata, dict) else {}
    actionable = bool(metadata.get("actionable", False))
    context_kind = str(metadata.get("context_kind") or "")
    if context_kind == "action_receipt":
        hint = "可继续问：链接在哪里 / 发给谁了 / 为什么失败 / 刚才结果是什么。"
    elif actionable:
        hint = "可继续问：第一个详情 / 全部显示 / 把这些发给某人。"
    else:
        hint = "可继续问：展开 / 第一个是什么 / 全部显示。"
    return f"{text}\n\n{hint}"


def _partial_answer(execution: ExecutionResult) -> str:
    success_answer = "\n\n".join(
        _human_readable_answer(item.answer)
        for item in execution.provider_results
        if item.status == "success" and _human_readable_answer(item.answer)
    ).strip()
    problem_answer = _problem_answer(execution)
    if success_answer and problem_answer:
        return f"{success_answer}\n\n补充说明：{problem_answer}"
    return success_answer or problem_answer


def _analysis_or_decision_answer(*, intent: IntentResult, execution: ExecutionResult) -> str:
    successful = [item for item in execution.provider_results if item.status == "success"]
    evidence = [_human_readable_answer(item.answer) for item in successful if _human_readable_answer(item.answer)]
    item_summaries = []
    if execution.result_context and execution.result_context.items:
        item_summaries = [_format_item(item, result_type=execution.result_context.result_type) for item in execution.result_context.items[:5]]
    basis = evidence or item_summaries
    if not basis:
        if intent.question_type == "decision":
            return "结论：目前证据不足，不建议直接做决策。\n\n依据：本轮没有查到可支撑判断的结构化结果。\n\n建议：请补充对象、范围或时间段；如果涉及企业数据，我会先查询事实，再给出建议。"
        return "结论：目前证据不足，暂时无法给出可靠分析。\n\n依据：本轮没有查到可支撑分析的结构化结果。\n\n建议：请补充对象、范围或时间段，我会先获取事实再分析原因。"
    if intent.question_type == "decision":
        lines = ["结论：建议先按当前可见事实推进，但关键动作仍需结合权限和最新业务数据确认。", "", "依据："]
        lines.extend(f"- {item[:180]}" for item in basis[:5])
        lines.extend(["", "建议：", "1. 先确认目标、影响范围和负责人。", "2. 涉及写入、审批、发送等动作时，继续走二次确认。", "3. 如果需要，我可以基于这批结果继续拆成可执行步骤。"])
        return "\n".join(lines)
    lines = ["结论：目前能看到的主要线索如下。", "", "依据："]
    lines.extend(f"- {item[:180]}" for item in basis[:5])
    lines.extend(["", "建议：如果要进一步判断原因，我可以继续按部门、人员、时间或业务对象展开。"])
    return "\n".join(lines)


def _action_trace_answer(context: RuntimeContext) -> str:
    decision_lines = _decision_trace_detail_lines(context)
    traces = context.session_context.get("runtime_v5_action_trace") if isinstance(context.session_context, dict) else None
    if (not isinstance(traces, list) or not traces) and not decision_lines:
        return "还没有查到最近的执行记录。"
    lines = []
    if decision_lines:
        lines.append("最近决策链路：")
        lines.extend(decision_lines)
    if isinstance(traces, list) and traces:
        if lines:
            lines.append("")
        lines.append("最近动作记录：")
        for index, item in enumerate([entry for entry in traces if isinstance(entry, dict)][-5:], start=1):
            kind = str(item.get("kind") or "操作")
            action = str(item.get("action") or "").strip()
            status = str(item.get("status") or "").strip()
            title = str(item.get("title") or "").strip()
            applicant = str(item.get("applicant") or "").strip()
            amount = str(item.get("amount") or "").strip()
            detail = "｜".join(part for part in (_action_kind_label(kind), _action_label(action), _status_label(status), title, applicant, amount) if part)
            lines.append(f"{index}. {detail or '操作记录'}")
            error = str(item.get("error") or "").strip()
            if error:
                lines.append(f"   错误摘要：{_compact_error(error, limit=120)}")
            next_step = str(item.get("recommended_next_step") or "").strip()
            if next_step:
                lines.append(f"   建议：{next_step}")
    return "\n".join(lines)


def _decision_trace_detail_lines(context: RuntimeContext) -> list[str]:
    traces = context.session_context.get("runtime_v5_decision_trace") if isinstance(context.session_context, dict) else None
    if not isinstance(traces, list) or not traces:
        return []
    lines: list[str] = []
    for index, item in enumerate([entry for entry in traces if isinstance(entry, dict)][-5:], start=1):
        question_type = _question_type_label(str(item.get("question_type") or ""))
        strategy = _action_label(str(item.get("strategy") or item.get("intent") or "未知策略"))
        status = _status_label(str(item.get("execution_status") or "unknown"))
        identity = _execution_identity_label(str(item.get("execution_identity") or ""))
        result_type = str(item.get("result_type") or "").strip()
        item_count = item.get("item_count")
        timing = int(item.get("pipeline_total_ms") or 0)
        parts = [question_type, strategy, status, f"执行身份：{identity}"]
        if result_type:
            parts.append(f"结果：{result_type}")
        if item_count not in (None, ""):
            parts.append(f"{item_count} 条")
        if timing > 0:
            parts.append(f"{timing}ms")
        lines.append(f"{index}. " + "｜".join(parts))
        if item.get("empty_result"):
            reason = _empty_reason_label(str(item.get("empty_reason") or ""))
            next_step = str(item.get("recommended_next_step") or "").strip()
            lines.append(f"   空结果原因：{reason}" + (f"；建议：{next_step}" if next_step else ""))
        if item.get("requires_confirmation"):
            lines.append("   原因：该轮需要二次确认后才会执行写入/发送/审批等动作。")
        slowest_stage = str(item.get("slowest_stage") or "").strip()
        slowest_ms = int(item.get("slowest_ms") or 0)
        if slowest_stage and slowest_ms > 0:
            lines.append(f"   最慢阶段：{_runtime_stage_name_label(slowest_stage)} {slowest_ms}ms")
        locator = str(item.get("debug_locator") or "").strip()
        primary_issue = str(item.get("primary_issue_fingerprint") or "").strip()
        if locator or primary_issue:
            lines.append("   定位：" + (locator or f"主问题 #{primary_issue}"))
    return lines


def _runtime_status_answer(context: RuntimeContext) -> str:
    snapshot = context.session_context.get("runtime_v5_last_diagnostics") if isinstance(context.session_context, dict) else None
    if not isinstance(snapshot, dict):
        return "还没有运行诊断快照。你可以先问一个业务问题，再问「V5状态」。"
    if not _runtime_status_detail_requested(context.current_message):
        return _runtime_status_summary_answer_from_snapshot(snapshot)
    return _runtime_status_detail_answer_from_snapshot(snapshot)
    capability = snapshot.get("capability_summary") if isinstance(snapshot.get("capability_summary"), dict) else {}
    provider_registry = snapshot.get("provider_registry") if isinstance(snapshot.get("provider_registry"), dict) else {}
    health = snapshot.get("health") if isinstance(snapshot.get("health"), dict) else {}
    permission = snapshot.get("permission") if isinstance(snapshot.get("permission"), dict) else {}
    installed = capability.get("installed_count", 0)
    total = capability.get("count", 0)
    pending = capability.get("pending_count", 0)
    query_count = capability.get("query_count", 0)
    action_count = capability.get("action_count", 0)
    confirmation_action_count = capability.get("confirmation_action_count", 0)
    runtime_strategy_count = capability.get("runtime_strategy_count", 0)
    atomic_count = capability.get("atomic_count", 0)
    path_maturity_counts = capability.get("path_maturity_counts") if isinstance(capability.get("path_maturity_counts"), dict) else {}
    migration_queue = capability.get("migration_queue_summary") if isinstance(capability.get("migration_queue_summary"), dict) else {}
    skill_inventory = capability.get("skill_atomic_inventory") if isinstance(capability.get("skill_atomic_inventory"), dict) else {}
    provider_count = len(provider_registry.get("registered_sources") or [])
    pending_ops = provider_registry.get("pending_operation_count", 0)
    read_ops = provider_registry.get("read_operation_count", 0)
    write_ops = provider_registry.get("write_operation_count", 0)
    confirm_ops = provider_registry.get("requires_confirmation_operation_count", 0)
    drift = provider_registry.get("capability_drift") if isinstance(provider_registry.get("capability_drift"), dict) else {}
    planner_drift = provider_registry.get("planner_drift") if isinstance(provider_registry.get("planner_drift"), dict) else {}
    status_summary = provider_registry.get("status_summary") if isinstance(provider_registry.get("status_summary"), dict) else {}
    contract_health = provider_registry.get("contract_health") if isinstance(provider_registry.get("contract_health"), dict) else {}
    write_confirmation_contract = provider_registry.get("write_confirmation_contract")
    if not isinstance(write_confirmation_contract, dict):
        write_confirmation_contract = (
            status_summary.get("write_confirmation_contract")
            if isinstance(status_summary.get("write_confirmation_contract"), dict)
            else {}
        )
    skill_registry = provider_registry.get("skill_registry")
    if not isinstance(skill_registry, dict):
        skill_registry = status_summary.get("skill_registry") if isinstance(status_summary.get("skill_registry"), dict) else {}
    top_skill_gap = skill_registry.get("top_priority_operation") if isinstance(skill_registry.get("top_priority_operation"), dict) else {}
    snapshot_summary = snapshot.get("snapshot_summary") if isinstance(snapshot.get("snapshot_summary"), dict) else {}
    runtime_mode = str(snapshot.get("runtime_mode") or snapshot_summary.get("runtime_mode") or "unknown")
    runtime_path_kind = str(snapshot.get("runtime_path_kind") or snapshot_summary.get("runtime_path_kind") or "")
    bypass_reason = str(snapshot.get("bypass_reason") or snapshot_summary.get("bypass_reason") or "")
    runtime_mode_label = {
        "full_runtime": "完整 V5 主链路",
        "manual_fast_path": "手工快速路径",
    }.get(runtime_mode, runtime_mode or "未知")
    runtime_path_label = {
        "v5_pipeline": "V5 Pipeline",
        "approval_fast_path": "审批快速摘要",
        "approval_workbench_path": "审批工作台",
    }.get(runtime_path_kind, runtime_path_kind or "未知")
    source_contract_for_summary = snapshot.get("source_execution_contract") if isinstance(snapshot.get("source_execution_contract"), dict) else {}
    followup_contract_for_summary = snapshot.get("followup_consume_contract") if isinstance(snapshot.get("followup_consume_contract"), dict) else {}
    action_closure_for_summary = snapshot.get("action_closure") if isinstance(snapshot.get("action_closure"), dict) else {}
    current_path_maturity = snapshot.get("current_path_maturity") if isinstance(snapshot.get("current_path_maturity"), dict) else {}
    current_capability_readiness = snapshot.get("current_capability_readiness") if isinstance(snapshot.get("current_capability_readiness"), dict) else {}
    snapshot_integrity = snapshot.get("snapshot_integrity") if isinstance(snapshot.get("snapshot_integrity"), dict) else {}
    pipeline_constitution_contract = snapshot.get("pipeline_constitution_contract") if isinstance(snapshot.get("pipeline_constitution_contract"), dict) else {}
    guardrail_parts = [
        "来源已对齐" if source_contract_for_summary.get("status") == "healthy" else "来源需关注",
        "追问已守护" if followup_contract_for_summary.get("status") == "healthy" else "追问需关注",
    ]
    if action_closure_for_summary.get("latest_available"):
        guardrail_parts.append("动作已闭环" if action_closure_for_summary.get("has_action_receipt_event") else "动作待回执")
    else:
        guardrail_parts.append("无动作待闭环")
    lines = [
        "V5 Runtime 状态：",
        f"整体结论：{status_summary.get('health_label') or '未生成'}。",
        f"运行模式：{runtime_mode_label}｜路径：{runtime_path_label}" + (f"｜原因：{bypass_reason}" if bypass_reason else "") + "。",
        "当前路径：" + f"{current_path_maturity.get('label') or current_path_maturity.get('status') or '未知'}。"
        if current_path_maturity
        else "当前路径：未生成路径成熟度。",
        "主链路契约："
        + (
            f"{pipeline_constitution_contract.get('label') or pipeline_constitution_contract.get('status') or '未知'}"
            f"｜阶段 {pipeline_constitution_contract.get('stage_count', 0)} 个"
            f"｜问题 {pipeline_constitution_contract.get('issue_count', 0)} 个。"
        )
        if pipeline_constitution_contract
        else "主链路契约：未生成。",
        "诊断完整性："
        + (
            f"{snapshot_integrity.get('label') or snapshot_integrity.get('status') or '未知'}"
            f"｜缺失 {snapshot_integrity.get('missing_count', 0)}"
            f"｜空模块 {snapshot_integrity.get('empty_count', 0)}。"
        )
        if snapshot_integrity
        else "诊断完整性：未生成。",
        "V5 护栏：" + "｜".join(guardrail_parts) + "。",
        f"Provider：已注册 {provider_count} 个，待接运行操作 {pending_ops} 个。",
        "写操作确认契约："
        f"写入 {write_confirmation_contract.get('write_operation_count', 0)} 个，"
        f"需 dry-run {write_confirmation_contract.get('dry_run_required_count', 0)} 个，"
        f"需确认令牌 {write_confirmation_contract.get('confirmation_token_required_count', 0)} 个，"
        f"缺口 {write_confirmation_contract.get('gap_count', 0)} 个。",
        f"架构漂移：Provider {status_summary.get('drift_count', 0)} 项，Planner {status_summary.get('planner_drift_count', 0)} 项，契约 {status_summary.get('contract_issue_count', 0)} 项，退役源 {provider_registry.get('retired_registered_source_count', 0)} 项。",
        f"最近链路阶段：{_status_label(str(health.get('stage') or 'unknown'))}。",
    ]
    main_pipeline_timing = snapshot.get("pipeline_timing") if isinstance(snapshot.get("pipeline_timing"), dict) else {}
    main_debug_locator = snapshot.get("debug_locator") if isinstance(snapshot.get("debug_locator"), dict) else {}
    if main_pipeline_timing or main_debug_locator:
        lines.append(
            "耗时定位："
            f"总耗时 {main_pipeline_timing.get('total_ms', 0)}ms"
            f"｜最慢阶段 {_runtime_stage_name_label(str(main_pipeline_timing.get('slowest_stage') or ''))} {main_pipeline_timing.get('slowest_ms', 0)}ms"
            f"｜最慢 Provider {_source_label(str(main_debug_locator.get('slowest_provider_source') or ''))} {main_debug_locator.get('slowest_provider_ms', 0)}ms。"
        )
        timing_advice = _runtime_pipeline_timing_advice(main_pipeline_timing)
        if timing_advice:
            lines.append(f"耗时建议：{timing_advice}")
    if snapshot_summary:
        sources = snapshot_summary.get("sources") if isinstance(snapshot_summary.get("sources"), list) else []
        lines.append(
            "快照摘要："
            f"{snapshot_summary.get('version') or snapshot.get('diagnostics_version') or '未知版本'}"
            f"｜{snapshot_summary.get('gate_label') or snapshot_summary.get('gate_status') or '未知门禁'}"
            f"｜健康问题 {snapshot_summary.get('health_issue_count', 0)} 个"
            f"｜修复项 {snapshot_summary.get('repair_item_count', 0)} 个"
            + (f"｜主问题 {snapshot_summary.get('primary_issue_fingerprint')}" if snapshot_summary.get("primary_issue_fingerprint") else "")
            + "。"
        )
        lines.append(
            "本轮摘要："
            f"{_question_type_label(str(snapshot_summary.get('question_type') or ''))}"
            f"｜{_data_scope_label(str(snapshot_summary.get('data_scope') or ''))}"
            f"｜{_action_label(str(snapshot_summary.get('strategy') or ''))}"
            f"｜来源：{'、'.join(_source_label(str(source)) for source in sources) or '无'}"
            f"｜{_status_label(str(snapshot_summary.get('execution_status') or 'unknown'))}"
            f"｜{snapshot_summary.get('result_type') or '无结果'} {snapshot_summary.get('result_count', 0)} 条。"
        )
        if snapshot_summary.get("next_step"):
            lines.append(f"摘要建议：{snapshot_summary.get('next_step')}")
    source_contract = snapshot.get("source_execution_contract") if isinstance(snapshot.get("source_execution_contract"), dict) else {}
    if source_contract:
        missing_sources = source_contract.get("missing_sources") if isinstance(source_contract.get("missing_sources"), list) else []
        executed_sources = source_contract.get("executed_sources") if isinstance(source_contract.get("executed_sources"), list) else []
        lines.append(
            "来源执行契约："
            f"{source_contract.get('label') or source_contract.get('status') or '未知'}"
            f"｜计划 {source_contract.get('planned_count', 0)} 个"
            f"｜已执行 {source_contract.get('executed_planned_count', len(executed_sources))} 个"
            f"｜缺失 {source_contract.get('missing_count', len(missing_sources))} 个"
            f"｜额外 {source_contract.get('extra_count', 0)} 个"
            f"｜重复 {source_contract.get('duplicate_count', 0)} 个"
            f"｜顺序：{'异常' if source_contract.get('order_mismatch') else '正常'}。"
        )
        if missing_sources:
            lines.append("缺失来源：" + "、".join(_source_label(str(source)) for source in missing_sources[:6]))
        source_issues = source_contract.get("issues") if isinstance(source_contract.get("issues"), list) else []
        if source_issues:
            first_issue = source_issues[0] if isinstance(source_issues[0], dict) else {}
            if first_issue:
                lines.append(
                    "来源执行下一修复："
                    f"{first_issue.get('label') or first_issue.get('kind')}"
                    + (f"｜{first_issue.get('detail')}" if first_issue.get("detail") else "")
                    + (f"｜建议：{first_issue.get('recommendation')}" if first_issue.get("recommendation") else "")
                )
    followup_contract = snapshot.get("followup_consume_contract") if isinstance(snapshot.get("followup_consume_contract"), dict) else {}
    if followup_contract:
        lines.append(
            "追问消费："
            f"{followup_contract.get('label') or followup_contract.get('status') or '未知'}"
            f"｜结构化条目 {followup_contract.get('item_count', 0)} 条"
            f"｜字段 {followup_contract.get('followup_field_count', 0)} 个"
            f"｜身份字段 {followup_contract.get('item_identity_field_count', 0)} 个"
            f"｜{'结构化条目优先' if followup_contract.get('prefer_items') else '未声明结构化条目优先'}"
            f"｜主消费：{followup_contract.get('primary_consume_source') or '未知'}。"
        )
        top_followup_repair = followup_contract.get("top_repair_item") if isinstance(followup_contract.get("top_repair_item"), dict) else {}
        if top_followup_repair:
            lines.append(
                "追问消费下一修复："
                f"{top_followup_repair.get('label') or top_followup_repair.get('kind')}"
                f"｜优先级 {top_followup_repair.get('priority_label', '未知')}"
                f"｜下一步：{top_followup_repair.get('next_step', '')}"
            )
    correlation = snapshot.get("correlation") if isinstance(snapshot.get("correlation"), dict) else {}
    if correlation:
        provider_sources = correlation.get("provider_sources") if isinstance(correlation.get("provider_sources"), list) else []
        lines.append(
            "追踪关联："
            f"trace_id={correlation.get('trace_id') or snapshot.get('trace_id') or '无'}"
            + (f"｜chat={correlation.get('chat_id_tail')}" if correlation.get("chat_id_tail") else "")
            + (f"｜query={correlation.get('query_id_tail')}" if correlation.get("query_id_tail") else "")
            + (f"｜策略：{_action_label(str(correlation.get('strategy') or ''))}" if correlation.get("strategy") else "")
            + (f"｜Provider：{'、'.join(_source_label(str(source)) for source in provider_sources)}" if provider_sources else "")
        )
    snapshot_lifecycle = snapshot.get("snapshot_lifecycle") if isinstance(snapshot.get("snapshot_lifecycle"), dict) else {}
    if snapshot_lifecycle:
        lines.append(
            "快照生命周期："
            f"{snapshot_lifecycle.get('label') or snapshot_lifecycle.get('status') or '未知'}"
            f"｜生成时间：{snapshot_lifecycle.get('generated_at') or snapshot.get('generated_at') or '未知'}"
            f"｜{'有执行' if snapshot_lifecycle.get('has_execution') else '未执行'}"
            f"｜{'有答案' if snapshot_lifecycle.get('has_answer') else '无答案'}"
            f"｜{'有修复计划' if snapshot_lifecycle.get('has_repair_plan') else '无修复计划'}。"
        )
    debug_locator = snapshot.get("debug_locator") if isinstance(snapshot.get("debug_locator"), dict) else {}
    if debug_locator:
        lines.append(
            "调试定位："
            f"{debug_locator.get('locator') or debug_locator.get('trace_id') or '无'}"
            + (f"｜回执：{'已关联' if debug_locator.get('has_correlated_receipt') else '未关联'}" if debug_locator.get("latest_action_id_tail") else "")
            + (f"｜Result 剩余 {debug_locator.get('result_expires_in_seconds', 0)} 秒" if "result_expires_in_seconds" in debug_locator else "")
            + "。"
        )
    diagnostics_capabilities = snapshot.get("diagnostics_capabilities") if isinstance(snapshot.get("diagnostics_capabilities"), dict) else {}
    if diagnostics_capabilities:
        modules = diagnostics_capabilities.get("modules") if isinstance(diagnostics_capabilities.get("modules"), list) else []
        lines.append(
            "诊断能力："
            f"{diagnostics_capabilities.get('module_count', len(modules))} 个模块"
            f"｜版本：{diagnostics_capabilities.get('version') or snapshot.get('diagnostics_version') or '未知'}。"
        )
    evidence_summary = snapshot.get("evidence_summary") if isinstance(snapshot.get("evidence_summary"), dict) else {}
    if evidence_summary:
        lines.append(
            "证据链："
            f"{evidence_summary.get('label') or evidence_summary.get('status') or '未知'}"
            f"｜可用 {evidence_summary.get('available_count', 0)} 类"
            f"｜缺失 {evidence_summary.get('missing_count', 0)} 类。"
        )
        evidence_items = evidence_summary.get("items") if isinstance(evidence_summary.get("items"), list) else []
        evidence_parts = []
        for item in [entry for entry in evidence_items if isinstance(entry, dict)]:
            label = str(item.get("label") or item.get("key") or "")
            count = item.get("count", 0)
            evidence_parts.append(f"{label}:{count}" if item.get("available") else f"{label}:缺失")
        if evidence_parts:
            lines.append("证据：" + "｜".join(evidence_parts[:6]))
    readiness = status_summary.get("readiness") if isinstance(status_summary.get("readiness"), dict) else {}
    if readiness:
        lines.append(f"V5 就绪度：{readiness.get('score', 0)}/100｜{readiness.get('label') or '未评估'}。")
        penalties = readiness.get("penalties") if isinstance(readiness.get("penalties"), list) else []
        if penalties:
            lines.append("主要扣分：")
            for item in [entry for entry in penalties if isinstance(entry, dict)][:4]:
                label = str(item.get("label") or item.get("kind") or "未分类")
                reason = str(item.get("reason") or "").strip()
                lines.append(f"- {label}：{item.get('count', 0)} 项，-{item.get('points', 0)} 分" + (f"｜{reason}" if reason else ""))
    health_matrix = snapshot.get("health_matrix") if isinstance(snapshot.get("health_matrix"), dict) else {}
    if health_matrix:
        lines.append(f"健康矩阵：{health_matrix.get('label') or health_matrix.get('status') or '未知'}｜问题 {health_matrix.get('issue_count', 0)} 个。")
        status_counts = health_matrix.get("status_counts") if isinstance(health_matrix.get("status_counts"), dict) else {}
        if status_counts:
            lines.append(
                "层级统计："
                f"正常 {status_counts.get('healthy', 0)}，"
                f"需关注 {status_counts.get('needs_attention', 0)}，"
                f"阻断 {status_counts.get('blocked', 0)}，"
                f"无数据 {status_counts.get('none', 0)}。"
            )
        layers = health_matrix.get("layers") if isinstance(health_matrix.get("layers"), list) else []
        layer_parts = []
        for item in [entry for entry in layers if isinstance(entry, dict)]:
            label = str(item.get("label") or item.get("key") or "")
            status = str(item.get("status_label") or item.get("status") or "")
            count = int(item.get("issue_count") or 0)
            layer_parts.append(f"{label}:{status}" + (f"({count})" if count else ""))
        if layer_parts:
            lines.append("层级：" + "｜".join(layer_parts[:8]))
    repair_plan = snapshot.get("repair_plan") if isinstance(snapshot.get("repair_plan"), dict) else {}
    if repair_plan:
        contract_issues_for_plan = contract_health.get("issues") if isinstance(contract_health.get("issues"), list) else []
        drift_categories_for_plan = status_summary.get("drift_categories") if isinstance(status_summary.get("drift_categories"), list) else []
        repair_summary_count = int(repair_plan.get("item_count") or 0) + len(contract_issues_for_plan) + len(drift_categories_for_plan)
        repair_summary = "暂无明显问题" if repair_plan.get("status") == "healthy" and repair_summary_count == 0 else f"{repair_summary_count} 项待处理"
        lines.append(f"修复计划：{repair_summary}。")
        severity_counts = repair_plan.get("severity_counts") if isinstance(repair_plan.get("severity_counts"), dict) else {}
        if severity_counts:
            lines.append(
                "风险统计："
                f"严重 {severity_counts.get('critical', 0)}，"
                f"高 {severity_counts.get('high', 0)}，"
                f"中 {severity_counts.get('medium', 0)}，"
                f"低 {severity_counts.get('low', 0)}。"
            )
        repair_items = repair_plan.get("items") if isinstance(repair_plan.get("items"), list) else []
        for item in [entry for entry in repair_items if isinstance(entry, dict)][:4]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            label = str(item.get("label") or "修复项")
            detail = str(item.get("detail") or "").strip()
            next_step_item = str(item.get("next_step") or "").strip()
            fingerprint = str(item.get("fingerprint") or "").strip()
            lines.append(f"- {severity_label}" + (f"｜#{fingerprint}" if fingerprint else "") + f"｜{label}" + (f"｜{detail}" if detail else ""))
            if next_step_item:
                lines.append(f"  下一步：{next_step_item}")
        for item in [entry for entry in contract_issues_for_plan if isinstance(entry, dict)][:3]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            source = _source_label(str(item.get("source") or ""))
            operation = _action_label(str(item.get("operation") or ""))
            label = str(item.get("label") or item.get("kind") or "Provider 契约问题")
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- {severity_label}｜Provider 契约｜{source}.{operation}｜{label}")
            if recommendation:
                lines.append(f"  下一步：{recommendation}")
        if not repair_items and not contract_issues_for_plan:
            for item in [entry for entry in drift_categories_for_plan if isinstance(entry, dict)][:3]:
                label = str(item.get("label") or item.get("kind") or "架构漂移")
                reason = str(item.get("reason") or "").strip()
                lines.append(f"- 中｜架构漂移｜{label}：{item.get('count', 0)} 项")
                if reason:
                    lines.append(f"  下一步：{reason}")
    source_readiness = status_summary.get("source_readiness") if isinstance(status_summary.get("source_readiness"), list) else []
    if source_readiness:
        lines.append("Provider 就绪度：")
        for item in [entry for entry in source_readiness if isinstance(entry, dict)][:8]:
            lines.append(
                f"- {_source_label(str(item.get('source') or ''))}"
                f"｜{item.get('label') or item.get('status') or '未知'}"
                f"｜已接 {item.get('installed', 0)}"
                f"｜待接 {item.get('pending', 0)}"
                f"｜问题 {item.get('issue_count', 0)}"
            )
    capability_contract = provider_registry.get("capability_contract") if isinstance(provider_registry.get("capability_contract"), dict) else {}
    if capability_contract:
        lines.append(
            "能力契约："
            f"{capability_contract.get('label') or capability_contract.get('status') or '未知'}"
            f"｜声明 {capability_contract.get('declared_count', 0)}"
            f"｜Provider {capability_contract.get('provider_operation_count', 0)}"
            f"｜已对齐 {capability_contract.get('aligned_count', 0)}"
            f"｜未纳管写 {capability_contract.get('ungoverned_write_count', 0)}"
            f"｜确认缺口 {capability_contract.get('confirmation_gap_count', 0)}"
            f"｜身份缺口 {capability_contract.get('identity_gap_count', 0)}。"
        )
        source_rows = capability_contract.get("source_rows") if isinstance(capability_contract.get("source_rows"), list) else []
        if source_rows:
            lines.append("契约来源：" + "｜".join(
                f"{_source_label(str(item.get('source') or ''))}:{item.get('aligned_count', 0)}/{item.get('provider_operation_count', 0)}"
                for item in [entry for entry in source_rows if isinstance(entry, dict)][:8]
            ))
        gaps = []
        for key, label in (
            ("ungoverned_writes", "未纳管写"),
            ("confirmation_gaps", "确认缺口"),
            ("identity_gaps", "身份缺口"),
            ("pending_declared", "声明待接"),
        ):
            rows = capability_contract.get(key) if isinstance(capability_contract.get(key), list) else []
            for row in [entry for entry in rows if isinstance(entry, dict)][:3]:
                gaps.append(f"{label}:{_source_label(str(row.get('source') or ''))}.{_action_label(str(row.get('operation') or ''))}")
        if gaps:
            lines.append("契约缺口：" + "｜".join(gaps[:6]))
    path_maturity = capability.get("path_maturity") if isinstance(capability.get("path_maturity"), list) else []
    migration_items = [
        item
        for item in path_maturity
        if isinstance(item, dict) and str(item.get("kind") or "") in {"manual_fast_path", "legacy_adapter"}
    ]
    if migration_items:
        lines.append("路径迁移：")
        top_item = migration_queue.get("top_item") if isinstance(migration_queue.get("top_item"), dict) else {}
        if top_item:
            lines.append(
                "最高优先："
                f"{top_item.get('migration_priority_label') or '中'}｜"
                f"{_source_label(str(top_item.get('source') or ''))}."
                f"{_action_label(str(top_item.get('operation') or ''))}"
                f"｜{top_item.get('label') or top_item.get('strategy') or ''}"
            )
        for item in migration_items[:6]:
            lines.append(
                f"- {item.get('migration_priority_label') or '中'}｜"
                f"{_source_label(str(item.get('source') or ''))}."
                f"{_action_label(str(item.get('operation') or ''))}"
                f"｜{item.get('kind_label') or item.get('kind') or '未知'}"
                f"｜{item.get('label') or item.get('strategy') or ''}"
            )
            bypassed = item.get("bypassed_stages") if isinstance(item.get("bypassed_stages"), list) else []
            if bypassed:
                lines.append("  绕过阶段：" + "、".join(_runtime_pipeline_label(str(stage)) for stage in bypassed[:8]))
            if item.get("target_runtime_path"):
                lines.append(f"  目标路径：{item.get('target_runtime_path')}")
            if item.get("migration_next_step"):
                lines.append(f"  下一步：{item.get('migration_next_step')}")
    current_migration_items = current_path_maturity.get("migration_items") if isinstance(current_path_maturity.get("migration_items"), list) else []
    if current_migration_items:
        lines.append("当前策略迁移：")
        for item in [entry for entry in current_migration_items if isinstance(entry, dict)][:4]:
            lines.append(
                f"- {item.get('migration_priority_label') or '中'}｜"
                f"{_source_label(str(item.get('source') or ''))}."
                f"{_action_label(str(item.get('operation') or ''))}"
                f"｜{item.get('kind_label') or item.get('kind') or '未知'}"
                f"｜{item.get('reason') or ''}"
            )
            bypassed = item.get("bypassed_stages") if isinstance(item.get("bypassed_stages"), list) else []
            if bypassed:
                lines.append("  绕过阶段：" + "、".join(_runtime_pipeline_label(str(stage)) for stage in bypassed[:8]))
            if item.get("target_runtime_path"):
                lines.append(f"  目标路径：{item.get('target_runtime_path')}")
            if item.get("migration_next_step"):
                lines.append(f"  下一步：{item.get('migration_next_step')}")
    priority_gaps = status_summary.get("priority_gaps") if isinstance(status_summary.get("priority_gaps"), list) else []
    if priority_gaps:
        lines.append("优先能力缺口：")
        for item in [entry for entry in priority_gaps if isinstance(entry, dict)][:5]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            source = _source_label(str(item.get("source") or ""))
            operation = _action_label(str(item.get("operation") or ""))
            label = str(item.get("label") or "能力缺口")
            next_step_item = str(item.get("next_step") or "").strip()
            lines.append(f"- {severity_label}｜{source}.{operation}｜{label}")
            if next_step_item:
                lines.append(f"  下一步：{next_step_item}")
    contract_issues = contract_health.get("issues") if isinstance(contract_health.get("issues"), list) else []
    if contract_issues:
        lines.append(f"Provider 契约：{contract_health.get('label') or '需要修复'}，阻断 {contract_health.get('blocking_count', 0)} 项。")
        for item in [entry for entry in contract_issues if isinstance(entry, dict)][:5]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            source = _source_label(str(item.get("source") or ""))
            operation = _action_label(str(item.get("operation") or ""))
            label = str(item.get("label") or item.get("kind") or "契约问题")
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- {severity_label}｜{source}.{operation}｜{label}")
            if recommendation:
                lines.append(f"  建议：{recommendation}")
    snapshot_summary = snapshot.get("snapshot_summary") if isinstance(snapshot.get("snapshot_summary"), dict) else {}
    runtime_state = snapshot_summary.get("runtime_state") if isinstance(snapshot_summary.get("runtime_state"), dict) else {}
    if runtime_state:
        lines.append(
            "运行状态："
            f"{runtime_state.get('label') or runtime_state.get('code') or '未知'}"
            f"｜级别：{runtime_state.get('severity') or '未知'}"
            f"｜原因：{runtime_state.get('reason') or '无'}"
            f"｜耗时 {runtime_state.get('total_ms', 0)}ms"
            f"｜动作等待 {runtime_state.get('latest_action_age_seconds', 0)} 秒。"
        )
    runtime_gate = snapshot.get("runtime_gate") if isinstance(snapshot.get("runtime_gate"), dict) else {}
    if runtime_gate:
        lines.append(f"运行门禁：{runtime_gate.get('label') or runtime_gate.get('status') or '未知'}。")
        severity_counts = runtime_gate.get("severity_counts") if isinstance(runtime_gate.get("severity_counts"), dict) else {}
        if severity_counts:
            lines.append(
                "门禁风险："
                f"严重 {severity_counts.get('critical', 0)}，"
                f"高 {severity_counts.get('high', 0)}，"
                f"中 {severity_counts.get('medium', 0)}，"
                f"低 {severity_counts.get('low', 0)}。"
            )
        priority_items = runtime_gate.get("priority_items") if isinstance(runtime_gate.get("priority_items"), list) else []
        if priority_items:
            lines.append("优先修复：")
            for item in [entry for entry in priority_items if isinstance(entry, dict)][:4]:
                severity = str(item.get("severity") or "medium")
                severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
                label = str(item.get("label") or item.get("key") or "未命名风险")
                detail = str(item.get("detail") or "").strip()
                recommendation = str(item.get("recommendation") or "").strip()
                fingerprint = str(item.get("fingerprint") or "").strip()
                lines.append(f"- {severity_label}" + (f"｜#{fingerprint}" if fingerprint else "") + f"｜{label}" + (f"｜{detail}" if detail else ""))
                if recommendation:
                    lines.append(f"  建议：{recommendation}")
        elif runtime_gate.get("next_step"):
            lines.append(f"门禁建议：{runtime_gate.get('next_step')}")
    context_health = snapshot.get("context_health") if isinstance(snapshot.get("context_health"), dict) else {}
    if context_health:
        lines.append(
            "上下文健康："
            f"{context_health.get('label') or context_health.get('status') or '未知'}"
            f"｜Identity:{'有' if context_health.get('identity_available') else '缺'}"
            f"｜Session:{'有' if context_health.get('session_available') else '缺'}"
            f"｜Profile:{'有' if context_health.get('profile_available') else '缺'}"
            f"｜Message:{'有' if context_health.get('current_message_available') else '缺'}"
            f"｜Result:{'有' if context_health.get('result_context_available') else '无'}"
            f"｜问题 {context_health.get('issue_count', 0)} 个。"
        )
        context_issues = context_health.get("issues") if isinstance(context_health.get("issues"), list) else []
        for item in [entry for entry in context_issues if isinstance(entry, dict)][:4]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            label = str(item.get("label") or item.get("kind") or "上下文问题")
            detail = str(item.get("detail") or "").strip()
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- {severity_label}｜{label}" + (f"｜{detail}" if detail else ""))
            if recommendation:
                lines.append(f"  建议：{recommendation}")
    profile_health = snapshot.get("profile_health") if isinstance(snapshot.get("profile_health"), dict) else {}
    if profile_health:
        lines.append(
            "Profile 隔离："
            f"{profile_health.get('label') or profile_health.get('status') or '未知'}"
            f"｜风格：{profile_health.get('style') or '默认'}"
            f"｜详细度：{profile_health.get('verbosity') or '默认'}"
            f"｜{'使用格式化' if profile_health.get('use_formatting') else '不强制格式化'}"
            f"｜问题 {profile_health.get('issue_count', 0)} 个。"
        )
        allowed_effects = profile_health.get("allowed_effects") if isinstance(profile_health.get("allowed_effects"), list) else []
        forbidden_effects = profile_health.get("forbidden_effects") if isinstance(profile_health.get("forbidden_effects"), list) else []
        if allowed_effects or forbidden_effects:
            lines.append(
                "Profile 边界："
                f"只影响 {'、'.join(str(item) for item in allowed_effects[:4]) or '回答呈现'}"
                f"；禁止影响 {'、'.join(str(item) for item in forbidden_effects[:6]) or '控制面'}。"
            )
        profile_issues = profile_health.get("issues") if isinstance(profile_health.get("issues"), list) else []
        for item in [entry for entry in profile_issues if isinstance(entry, dict)][:4]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            label = str(item.get("label") or item.get("kind") or "Profile 问题")
            detail = str(item.get("detail") or "").strip()
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- {severity_label}｜{label}" + (f"｜{detail}" if detail else ""))
            if recommendation:
                lines.append(f"  建议：{recommendation}")
    followup_health = snapshot.get("followup_health") if isinstance(snapshot.get("followup_health"), dict) else {}
    if followup_health:
        lines.append(
            "追问健康："
            f"{followup_health.get('label') or followup_health.get('status') or '未知'}"
            f"｜{'使用上一轮结果' if followup_health.get('uses_previous_result') else '非追问'}"
            f"｜类型：{_followup_type_label(str(followup_health.get('followup_type') or ''))}"
            f"｜{_result_context_kind_label(str(followup_health.get('context_kind') or ''))}"
            f"｜items {followup_health.get('item_count', 0)}"
            f"｜问题 {followup_health.get('issue_count', 0)} 个。"
        )
        followup_issues = followup_health.get("issues") if isinstance(followup_health.get("issues"), list) else []
        for item in [entry for entry in followup_issues if isinstance(entry, dict)][:4]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            label = str(item.get("label") or item.get("kind") or "追问问题")
            detail = str(item.get("detail") or "").strip()
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- {severity_label}｜{label}" + (f"｜{detail}" if detail else ""))
            if recommendation:
                lines.append(f"  建议：{recommendation}")
    followup_contract = snapshot.get("followup_consume_contract") if isinstance(snapshot.get("followup_consume_contract"), dict) else {}
    if followup_contract:
        lines.append(
            "追问消费定位："
            f"{followup_contract.get('label') or followup_contract.get('status') or '未知'}"
            f"｜{'结构化条目优先' if followup_contract.get('prefer_items') else '未声明 items 优先'}"
            f"｜可按序号：{'是' if followup_contract.get('supports_index_followup') else '否'}"
            f"｜可看详情：{'是' if followup_contract.get('supports_detail_followup') else '否'}"
            f"｜过期刷新：{'是' if followup_contract.get('requires_refresh_when_expired') else '否'}"
            f"｜问题 {followup_contract.get('issue_count', 0)} 个。"
        )
        contract_issues = followup_contract.get("issues") if isinstance(followup_contract.get("issues"), list) else []
        for item in [entry for entry in contract_issues if isinstance(entry, dict)][:4]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            label = str(item.get("label") or item.get("kind") or "追问消费问题")
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- {severity_label}｜{label}")
            if recommendation:
                lines.append(f"  建议：{recommendation}")
    decision_health = snapshot.get("decision_health") if isinstance(snapshot.get("decision_health"), dict) else {}
    if decision_health:
        sources = decision_health.get("sources") if isinstance(decision_health.get("sources"), list) else []
        lines.append(
            "决策健康："
            f"{decision_health.get('label') or decision_health.get('status') or '未知'}"
            f"｜{_question_type_label(str(decision_health.get('question_type') or ''))}"
            f"｜{_data_scope_label(str(decision_health.get('data_scope') or ''))}"
            f"｜{_action_label(str(decision_health.get('strategy') or ''))}"
            f"｜来源：{'、'.join(_source_label(str(source)) for source in sources) or '无'}"
            f"｜置信度 {decision_health.get('confidence', 0)}"
            f"｜{'需澄清' if decision_health.get('should_clarify') else '可执行'}。"
        )
        missing_params = decision_health.get("missing_params") if isinstance(decision_health.get("missing_params"), list) else []
        if missing_params:
            lines.append("缺少参数：" + "、".join(str(item) for item in missing_params[:6]))
        decision_issues = decision_health.get("issues") if isinstance(decision_health.get("issues"), list) else []
        for item in [entry for entry in decision_issues if isinstance(entry, dict)][:4]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            label = str(item.get("label") or item.get("kind") or "决策问题")
            detail = str(item.get("detail") or "").strip()
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- {severity_label}｜{label}" + (f"｜{detail}" if detail else ""))
            if recommendation:
                lines.append(f"  建议：{recommendation}")
    router_health = snapshot.get("router_health") if isinstance(snapshot.get("router_health"), dict) else {}
    if router_health:
        planned_sources = router_health.get("planned_sources") if isinstance(router_health.get("planned_sources"), list) else []
        executed_sources = router_health.get("executed_unique_sources") if isinstance(router_health.get("executed_unique_sources"), list) else []
        lines.append(
            "Router 健康："
            f"{router_health.get('label') or router_health.get('status') or '未知'}"
            f"｜计划：{'、'.join(_source_label(str(source)) for source in planned_sources) or '无'}"
            f"｜实际：{'、'.join(_source_label(str(source)) for source in executed_sources) or '无'}"
            f"｜覆盖率 {router_health.get('coverage_percent', 0)}%"
            f"｜问题 {router_health.get('issue_count', 0)} 个。"
        )
        router_issues = router_health.get("issues") if isinstance(router_health.get("issues"), list) else []
        for item in [entry for entry in router_issues if isinstance(entry, dict)][:4]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            label = str(item.get("label") or item.get("kind") or "Router 问题")
            detail = str(item.get("detail") or "").strip()
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- {severity_label}｜{label}" + (f"｜{detail}" if detail else ""))
            if recommendation:
                lines.append(f"  建议：{recommendation}")
    execution_health = snapshot.get("execution_health") if isinstance(snapshot.get("execution_health"), dict) else {}
    if execution_health:
        lines.append(
            "执行健康："
            f"{execution_health.get('label') or execution_health.get('status') or '未知'}"
            f"｜{_status_label(str(execution_health.get('execution_status') or 'unknown'))}"
            f"｜Provider {execution_health.get('provider_count', 0)}"
            f"｜成功 {execution_health.get('success_count', 0)}"
            f"｜失败 {execution_health.get('error_count', 0)}"
            f"｜无权限 {execution_health.get('denied_count', 0)}"
            f"｜跳过 {execution_health.get('skipped_count', 0)}"
            f"｜{'有上下文' if execution_health.get('has_result_context') else '无上下文'}"
            f"｜问题 {execution_health.get('issue_count', 0)} 个。"
        )
        execution_issues = execution_health.get("issues") if isinstance(execution_health.get("issues"), list) else []
        for item in [entry for entry in execution_issues if isinstance(entry, dict)][:4]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            label = str(item.get("label") or item.get("kind") or "执行问题")
            detail = str(item.get("detail") or "").strip()
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- {severity_label}｜{label}" + (f"｜{detail}" if detail else ""))
            if recommendation:
                lines.append(f"  建议：{recommendation}")
    answer_health = snapshot.get("answer_health") if isinstance(snapshot.get("answer_health"), dict) else {}
    if answer_health:
        lines.append(
            "答案健康："
            f"{answer_health.get('label') or answer_health.get('status') or '未知'}"
            f"｜{answer_health.get('answer_chars', 0)} 字"
            f"｜{'需确认' if answer_health.get('requires_confirmation') else '无需确认'}"
            f"｜{'发现技术载荷' if answer_health.get('raw_payload_detected') else '未见技术载荷'}"
            f"｜问题 {answer_health.get('issue_count', 0)} 个。"
        )
        answer_issues = answer_health.get("issues") if isinstance(answer_health.get("issues"), list) else []
        for item in [entry for entry in answer_issues if isinstance(entry, dict)][:4]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            label = str(item.get("label") or item.get("kind") or "答案问题")
            detail = str(item.get("detail") or "").strip()
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- {severity_label}｜{label}" + (f"｜{detail}" if detail else ""))
            if recommendation:
                lines.append(f"  建议：{recommendation}")
    action_closure = snapshot.get("action_closure") if isinstance(snapshot.get("action_closure"), dict) else {}
    if action_closure.get("latest_available"):
        action_id_tail = str(action_closure.get("latest_action_id") or "")[-8:]
        receipt_label = (
            "已关联回执"
            if action_closure.get("has_correlated_receipt")
            else "已有动作回执"
            if action_closure.get("has_action_receipt_event")
            else "暂无动作回执"
        )
        action_parts = [
            _action_kind_label(str(action_closure.get("latest_kind") or "")),
            _action_label(str(action_closure.get("latest_action") or "")),
            _status_label(str(action_closure.get("latest_status") or "unknown")),
        ]
        if action_id_tail:
            action_parts.append(f"动作ID {action_id_tail}")
        action_parts.extend(
            [
                f"已等待 {action_closure.get('latest_age_seconds', 0)} 秒",
                "疑似卡住" if action_closure.get("latest_stuck") else "未超时",
                receipt_label,
            ]
        )
        lines.append("动作闭环：" + "｜".join(part for part in action_parts if part) + "。")
        contract_label = str(action_closure.get("contract_label") or "").strip()
        contract_next_step = str(action_closure.get("contract_next_step") or "").strip()
        contract_severity = str(action_closure.get("contract_severity") or "").strip()
        if contract_label:
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(contract_severity, contract_severity or "低")
            lines.append(
                "动作闭环定位："
                f"{contract_label}"
                f"｜级别：{severity_label}"
                + (f"｜下一步：{contract_next_step}" if contract_next_step else "")
            )
        receipt_contract_label = "回执已闭环" if action_closure.get("has_action_receipt_event") else "等待动作回执"
        if action_closure.get("latest_terminal") and not action_closure.get("has_action_receipt_event"):
            receipt_contract_label = "最终状态缺少回执"
        elif action_closure.get("latest_pending") and not action_closure.get("has_action_receipt_event"):
            receipt_contract_label = "处理中，尚未产生回执"
        lines.append(
            "动作回执契约："
            f"{receipt_contract_label}"
            f"｜关联：{'是' if action_closure.get('has_correlated_receipt') else '否'}"
            f"｜终态：{'是' if action_closure.get('latest_terminal') else '否'}"
            f"｜等待：{'是' if action_closure.get('latest_pending') else '否'}。"
        )
        missing_fields = action_closure.get("missing_fields") if isinstance(action_closure.get("missing_fields"), list) else []
        if missing_fields:
            lines.append("动作追踪缺字段：" + "、".join(str(item) for item in missing_fields[:6]))
        top_repair_item = action_closure.get("top_repair_item") if isinstance(action_closure.get("top_repair_item"), dict) else {}
        repair_queue = action_closure.get("repair_queue") if isinstance(action_closure.get("repair_queue"), list) else []
        if top_repair_item:
            lines.append(
                "动作闭环下一修复："
                f"{top_repair_item.get('label') or top_repair_item.get('kind')}"
                f"｜优先级 {top_repair_item.get('priority_label', '未知')}"
                f"｜{top_repair_item.get('detail', '')}"
                f"｜下一步：{top_repair_item.get('next_step', '')}"
            )
        if repair_queue:
            lines.append(f"动作闭环修复队列：{len(repair_queue)} 项。")
    action_timeline = snapshot.get("action_timeline") if isinstance(snapshot.get("action_timeline"), dict) else {}
    if action_timeline.get("available"):
        lines.append(
            "动作时间线："
            f"{action_timeline.get('event_count', 0)} 个事件"
            f"｜动作 {action_timeline.get('action_event_count', 0)}"
            f"｜上下文 {action_timeline.get('result_context_event_count', 0)}"
            f"｜回执 {action_timeline.get('action_receipt_event_count', 0)}。"
        )
        timeline_events = action_timeline.get("events") if isinstance(action_timeline.get("events"), list) else []
        for item in [entry for entry in timeline_events if isinstance(entry, dict)][-4:]:
            label = str(item.get("label") or item.get("kind") or "")
            detail = str(item.get("detail") or item.get("status") or "").strip()
            lines.append(f"- {label}" + (f"｜{detail}" if detail else ""))
    permission_health = snapshot.get("permission_health") if isinstance(snapshot.get("permission_health"), dict) else {}
    if permission_health:
        lines.append(
            f"权限健康：{permission_health.get('label') or permission_health.get('status') or '未知'}"
            f"｜实际身份：{_execution_identity_label(str(permission_health.get('execution_identity') or ''))}"
            f"｜默认身份：{_execution_identity_label(str(permission_health.get('default_identity') or ''))}"
            f"｜策略：{'显式覆盖' if permission_health.get('identity_override') else '默认'}"
            f"｜问题 {permission_health.get('issue_count', 0)} 个。"
        )
        permission_issues = permission_health.get("issues") if isinstance(permission_health.get("issues"), list) else []
        for item in [entry for entry in permission_issues if isinstance(entry, dict)][:4]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            label = str(item.get("label") or item.get("kind") or "权限问题")
            detail = str(item.get("detail") or "").strip()
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- {severity_label}｜{label}" + (f"｜{detail}" if detail else ""))
            if recommendation:
                lines.append(f"  建议：{recommendation}")
    result_context_quality = snapshot.get("result_context_quality") if isinstance(snapshot.get("result_context_quality"), dict) else {}
    if result_context_quality.get("available"):
        lines.append(
            "结果上下文质量："
            f"{result_context_quality.get('label') or result_context_quality.get('status') or '未知'}"
            f"｜{_result_context_kind_label(str(result_context_quality.get('context_kind') or ''))}"
            f"｜{result_context_quality.get('result_type') or '未知类型'}"
            f"｜{result_context_quality.get('item_count', 0)}/{result_context_quality.get('count', 0)} 条"
            f"｜证据 {result_context_quality.get('provider_evidence_count', 0)} 个"
            f"｜可追问字段 {result_context_quality.get('followup_field_count', 0)} 个"
            f"｜标识字段 {result_context_quality.get('item_identity_field_count', 0)} 个"
            f"｜{'结构化条目优先' if result_context_quality.get('prefer_items') else '未声明结构化条目优先'}"
            f"｜主消费：{result_context_quality.get('primary_consume_source') or '未知'}"
            f"｜{'answer含技术载荷' if result_context_quality.get('raw_answer_detected') else 'answer正常'}"
            f"｜已存活 {result_context_quality.get('age_seconds', 0)} 秒"
            f"｜剩余 {result_context_quality.get('expires_in_seconds', 0)} 秒。"
        )
        consume_order = result_context_quality.get("consume_order") if isinstance(result_context_quality.get("consume_order"), list) else []
        if consume_order:
            lines.append(
                "上下文消费顺序："
                + " -> ".join(str(item.get("source") or "") for item in consume_order if isinstance(item, dict))
                + f"｜answer：{result_context_quality.get('answer_fallback_label') or '未知'}。"
            )
        followup_fields = result_context_quality.get("followup_fields") if isinstance(result_context_quality.get("followup_fields"), list) else []
        if followup_fields:
            lines.append("可追问字段：" + "、".join(str(item) for item in followup_fields[:10]))
        quality_issues = result_context_quality.get("issues") if isinstance(result_context_quality.get("issues"), list) else []
        for item in [entry for entry in quality_issues if isinstance(entry, dict)][:4]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            label = str(item.get("label") or item.get("kind") or "上下文问题")
            detail = str(item.get("detail") or "").strip()
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- {severity_label}｜{label}" + (f"｜{detail}" if detail else ""))
            if recommendation:
                lines.append(f"  建议：{recommendation}")
    pipeline_health = snapshot.get("pipeline_health") if isinstance(snapshot.get("pipeline_health"), dict) else {}
    if pipeline_health:
        lines.append(
            "Pipeline 健康："
            f"{pipeline_health.get('label') or pipeline_health.get('status') or '未知'}"
            f"｜阶段 {pipeline_health.get('stage_count', 0)} 个"
            f"｜问题 {pipeline_health.get('issue_count', 0)} 个。"
        )
        pipeline_issues = pipeline_health.get("issues") if isinstance(pipeline_health.get("issues"), list) else []
        for item in [entry for entry in pipeline_issues if isinstance(entry, dict)][:4]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            label = str(item.get("label") or item.get("kind") or "阶段问题")
            detail = str(item.get("detail") or item.get("stage") or "").strip()
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- {severity_label}｜{label}" + (f"｜{detail}" if detail else ""))
            if recommendation:
                lines.append(f"  建议：{recommendation}")
    if pipeline_constitution_contract:
        contract_issues = pipeline_constitution_contract.get("issues") if isinstance(pipeline_constitution_contract.get("issues"), list) else []
        if contract_issues:
            lines.append("主链路契约问题：")
            for item in [entry for entry in contract_issues if isinstance(entry, dict)][:5]:
                severity = str(item.get("severity") or "medium")
                severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
                label = str(item.get("label") or item.get("kind") or "主链路问题")
                detail = str(item.get("detail") or "").strip()
                recommendation = str(item.get("recommendation") or "").strip()
                lines.append(f"- {severity_label}｜{label}" + (f"｜{detail}" if detail else ""))
                if recommendation:
                    lines.append(f"  建议：{recommendation}")
    if skill_registry:
        unexecutable_operations = (
            skill_registry.get("exposed_unexecutable_operations")
            if isinstance(skill_registry.get("exposed_unexecutable_operations"), list)
            else []
        )
        if unexecutable_operations:
            lines.append("已开放但不可执行的原子能力：")
            for item in [entry for entry in unexecutable_operations if isinstance(entry, dict)][:6]:
                source = _source_label(str(item.get("source") or ""))
                operation = _action_label(str(item.get("operation") or ""))
                label = str(item.get("label") or "").strip()
                lines.append(f"- {source}｜{operation}" + (f"｜{label}" if label else "") + "｜建议：补 Provider 或先关闭开放。")
    timing_health = snapshot.get("timing_health") if isinstance(snapshot.get("timing_health"), dict) else {}
    if timing_health:
        lines.append(
            "耗时健康："
            f"{timing_health.get('label') or timing_health.get('status') or '未知'}"
            f"｜总耗时 {timing_health.get('total_ms', 0)}ms"
            f"｜最慢阶段：{_runtime_stage_name_label(str(timing_health.get('slowest_stage') or ''))}"
            f" {timing_health.get('slowest_ms', 0)}ms"
            f"｜Provider {timing_health.get('provider_total_ms', 0)}ms"
            f"｜最慢 Provider：{_source_label(str(timing_health.get('slowest_provider_source') or ''))}"
            f" {timing_health.get('slowest_provider_ms', 0)}ms"
            f"｜问题 {timing_health.get('issue_count', 0)} 个。"
        )
        timing_issues = timing_health.get("issues") if isinstance(timing_health.get("issues"), list) else []
        for item in [entry for entry in timing_issues if isinstance(entry, dict)][:4]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            label = str(item.get("label") or item.get("kind") or "耗时问题")
            detail = str(item.get("detail") or "").strip()
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- {severity_label}｜{label}" + (f"｜{detail}" if detail else ""))
            if recommendation:
                lines.append(f"  建议：{recommendation}")
    constitution_guard = snapshot.get("constitution_guard") if isinstance(snapshot.get("constitution_guard"), dict) else {}
    if constitution_guard:
        lines.append(
            "架构守卫："
            + ("通过" if constitution_guard.get("healthy") else f"发现 {constitution_guard.get('failed_count', 0)} 项风险")
        )
        checks = constitution_guard.get("checks") if isinstance(constitution_guard.get("checks"), list) else []
        for item in [entry for entry in checks if isinstance(entry, dict)][:5]:
            label = str(item.get("label") or item.get("key") or "")
            detail = str(item.get("detail") or "").strip()
            if item.get("ok"):
                lines.append(f"- 通过｜{label}" + (f"｜{detail}" if detail else ""))
                continue
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- 风险｜{severity_label}｜{label}" + (f"｜{detail}" if detail else ""))
            if recommendation:
                lines.append(f"  建议：{recommendation}")
    pipeline_timing = snapshot.get("pipeline_timing") if isinstance(snapshot.get("pipeline_timing"), dict) else {}
    if pipeline_timing:
        lines.append(
            "本轮耗时："
            f"{pipeline_timing.get('total_ms', 0)}ms，"
            f"最慢阶段：{_runtime_stage_name_label(str(pipeline_timing.get('slowest_stage') or ''))}"
            f" {pipeline_timing.get('slowest_ms', 0)}ms。"
        )
        timing_advice = _runtime_pipeline_timing_advice(pipeline_timing)
        if timing_advice:
            lines.append(f"耗时诊断：{timing_advice}")
    decision_summary = snapshot.get("decision_summary") if isinstance(snapshot.get("decision_summary"), dict) else {}
    if decision_summary:
        sources = decision_summary.get("sources") if isinstance(decision_summary.get("sources"), list) else []
        confirmation_reasons = decision_summary.get("confirmation_reasons") if isinstance(decision_summary.get("confirmation_reasons"), list) else []
        lines.append(
            "本轮决策摘要："
            f"{_question_type_label(str(decision_summary.get('question_type') or ''))}"
            f"｜{_action_label(str(decision_summary.get('strategy') or ''))}"
            f"｜来源：{'、'.join(_source_label(str(source)) for source in sources) or '无'}"
            f"｜{_execution_identity_label(str(decision_summary.get('execution_identity') or ''))}"
            f"｜{'需确认' if decision_summary.get('requires_confirmation') else '无需确认'}"
            f"｜{_status_label(str(decision_summary.get('execution_status') or 'unknown'))}"
        )
        if confirmation_reasons:
            lines.append("确认原因：" + "、".join(_confirmation_reason_label(str(item)) for item in confirmation_reasons))
        provider_count = int(decision_summary.get("provider_success_count") or 0) + int(decision_summary.get("provider_error_count") or 0) + int(decision_summary.get("provider_denied_count") or 0)
        if provider_count:
            lines.append(
                "Provider 摘要："
                f"成功 {decision_summary.get('provider_success_count', 0)}，"
                f"失败 {decision_summary.get('provider_error_count', 0)}，"
                f"无权限 {decision_summary.get('provider_denied_count', 0)}，"
                f"总耗时 {decision_summary.get('provider_total_duration_ms', 0)}ms。"
            )
    provider_summary = snapshot.get("provider_summary") if isinstance(snapshot.get("provider_summary"), dict) else {}
    provider_by_source = provider_summary.get("by_source") if isinstance(provider_summary.get("by_source"), list) else []
    if provider_by_source:
        lines.append("本轮 Provider 来源：")
        for item in [entry for entry in provider_by_source if isinstance(entry, dict)][:5]:
            operations = item.get("operations") if isinstance(item.get("operations"), list) else []
            lines.append(
                f"- {_source_label(str(item.get('source') or ''))}"
                f"｜调用 {item.get('count', 0)} 次"
                f"｜成功 {item.get('success_count', 0)}"
                f"｜失败 {item.get('error_count', 0)}"
                f"｜耗时 {item.get('total_duration_ms', 0)}ms"
                + (f"｜操作：{', '.join(_action_label(str(op)) for op in operations[:3])}" if operations else "")
            )
    evidence_contract = provider_summary.get("evidence_contract") if isinstance(provider_summary.get("evidence_contract"), dict) else {}
    if evidence_contract:
        lines.append(
            "Provider 证据契约："
            f"{evidence_contract.get('label') or evidence_contract.get('status') or '未知'}"
            f"｜Provider {evidence_contract.get('provider_count', 0)} 个"
            f"｜问题 {evidence_contract.get('issue_count', 0)} 个"
            f"｜阻断 {evidence_contract.get('blocking_count', 0)} 个。"
        )
        top_issue = evidence_contract.get("top_issue") if isinstance(evidence_contract.get("top_issue"), dict) else {}
        if top_issue:
            lines.append(
                "Provider 证据下一修复："
                f"{top_issue.get('label') or top_issue.get('kind')}"
                f"｜{_source_label(str(top_issue.get('source') or ''))}"
                + (f"｜{_action_label(str(top_issue.get('operation') or ''))}" if top_issue.get("operation") else "")
                + (f"｜下一步：{top_issue.get('next_step')}" if top_issue.get("next_step") else "")
            )
    latency = provider_summary.get("latency_diagnostics") if isinstance(provider_summary.get("latency_diagnostics"), dict) else {}
    if latency:
        lines.append(
            "Provider 慢调用："
            f"{latency.get('label') or latency.get('status') or '未知'}"
            f"｜总耗时 {latency.get('total_duration_ms', 0)}ms"
            f"｜慢 Provider {latency.get('slow_provider_count', 0)} 个"
            f"｜慢子步骤 {latency.get('slow_substep_count', 0)} 个"
            f"｜缓存命中 {latency.get('cache_hit_count', 0)}/{latency.get('cache_known_count', 0)}。"
        )
        top_slow = latency.get("top_slow_item") if isinstance(latency.get("top_slow_item"), dict) else {}
        if top_slow:
            lines.append(
                "Provider 慢调用下一优化："
                f"{top_slow.get('label') or top_slow.get('kind')}"
                f"｜{_source_label(str(top_slow.get('source') or ''))}"
                + (f"｜{_action_label(str(top_slow.get('operation') or ''))}" if top_slow.get("operation") else "")
                + (f"｜{top_slow.get('step')}" if top_slow.get("step") else "")
                + f"｜耗时 {top_slow.get('duration_ms', 0)}ms"
                + (f"｜原因：{top_slow.get('reason')}" if top_slow.get("reason") else "")
                + (f"｜下一步：{top_slow.get('next_step')}" if top_slow.get("next_step") else "")
            )
    fast_response = snapshot.get("fast_response_contract") if isinstance(snapshot.get("fast_response_contract"), dict) else {}
    if fast_response:
        lines.append(
            "首包/异步契约："
            f"{fast_response.get('label') or fast_response.get('status') or '未知'}"
            f"｜总耗时 {fast_response.get('total_ms', 0)}ms"
            f"｜首包：{'需要' if fast_response.get('fast_ack_required') else '非必需'}"
            f"｜异步：{'建议' if fast_response.get('async_recommended') else '非必需'}"
            f"｜回执：{'需要' if fast_response.get('progress_receipt_required') else '非必需'}"
            f"｜已有回执：{'是' if fast_response.get('has_action_receipt') else '否'}。"
        )
        top_fast_issue = fast_response.get("top_issue") if isinstance(fast_response.get("top_issue"), dict) else {}
        if top_fast_issue:
            lines.append(
                "首包/异步下一修复："
                f"{top_fast_issue.get('label') or top_fast_issue.get('kind')}"
                + (f"｜{top_fast_issue.get('detail')}" if top_fast_issue.get("detail") else "")
                + (f"｜下一步：{top_fast_issue.get('next_step')}" if top_fast_issue.get("next_step") else "")
            )
    provider_failure_summary = provider_summary.get("failure_summary") if isinstance(provider_summary.get("failure_summary"), dict) else {}
    if int(provider_failure_summary.get("issue_count") or 0) > 0:
        categories = provider_failure_summary.get("categories") if isinstance(provider_failure_summary.get("categories"), dict) else {}
        category_labels = {
            "missing_params": "缺参数",
            "permission_or_auth": "权限/授权",
            "not_open": "未开放",
            "execution_failed": "执行失败",
            "provider_missing": "未注册",
            "empty_result": "空结果",
            "unknown": "未知",
        }
        category_text = "｜".join(
            f"{category_labels.get(str(key), str(key))} {value}"
            for key, value in categories.items()
            if int(value or 0) > 0
        )
        lines.append(
            f"Provider 失败定位：阻断 {provider_failure_summary.get('blocking_count', 0)} 项"
            + (f"｜{category_text}" if category_text else "")
            + "。"
        )
        failure_items = provider_failure_summary.get("items") if isinstance(provider_failure_summary.get("items"), list) else []
        for item in [entry for entry in failure_items if isinstance(entry, dict)][:3]:
            source = _source_label(str(item.get("source") or ""))
            operation = _action_label(str(item.get("operation") or ""))
            reason = _empty_reason_label(str(item.get("reason") or item.get("error_type") or ""))
            category = str(item.get("category_label") or "").strip()
            next_step = str(item.get("next_step") or "").strip()
            lines.append(
                f"- {source}"
                + (f"｜{operation}" if operation else "")
                + (f"｜{category}" if category else "")
                + (f"｜{reason}" if reason else "")
            )
            if next_step:
                lines.append(f"  下一步：{next_step}")
    provider_quality_issues = provider_summary.get("quality_issues") if isinstance(provider_summary.get("quality_issues"), list) else []
    if provider_quality_issues:
        lines.append("Provider 质量问题：")
        for item in [entry for entry in provider_quality_issues if isinstance(entry, dict)][:5]:
            severity = str(item.get("severity") or "medium")
            severity_label = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}.get(severity, severity)
            label = str(item.get("label") or item.get("kind") or "结果质量问题")
            detail = str(item.get("detail") or "").strip()
            recommendation = str(item.get("recommendation") or "").strip()
            lines.append(f"- {severity_label}｜{label}" + (f"｜{detail}" if detail else ""))
            if recommendation:
                lines.append(f"  建议：{recommendation}")
    provider_recommendations = provider_summary.get("recommendations") if isinstance(provider_summary.get("recommendations"), list) else []
    if provider_recommendations:
        lines.append("Provider 修复建议：")
        for item in [entry for entry in provider_recommendations if isinstance(entry, dict)][:5]:
            source = _source_label(str(item.get("source") or ""))
            operation = _action_label(str(item.get("operation") or ""))
            error_type = _empty_reason_label(str(item.get("error_type") or ""))
            pending_reason = str(item.get("pending_reason") or "").strip()
            next_step = str(item.get("recommended_next_step") or "").strip()
            lines.append(
                f"- {source}"
                + (f"｜{operation}" if operation else "")
                + (f"｜{error_type}" if error_type else "")
                + (f"｜{pending_reason}" if pending_reason else "")
                + (f"｜建议：{next_step}" if next_step else "")
            )
    drift_categories = status_summary.get("drift_categories") if isinstance(status_summary.get("drift_categories"), list) else []
    if drift_categories:
        lines.append("漂移分类：")
        for item in [entry for entry in drift_categories if isinstance(entry, dict)][:5]:
            label = str(item.get("label") or item.get("kind") or "未分类")
            count = int(item.get("count") or 0)
            reason = str(item.get("reason") or "").strip()
            lines.append(f"- {label}：{count} 项" + (f"｜{reason}" if reason else ""))
    usable = status_summary.get("usable_sources") if isinstance(status_summary.get("usable_sources"), list) else []
    readonly = status_summary.get("readonly_sources") if isinstance(status_summary.get("readonly_sources"), list) else []
    if usable:
        lines.append("可执行来源：" + "、".join(_source_label(str(source)) for source in usable[:12]))
    if readonly:
        lines.append("只读降级来源：" + "、".join(_source_label(str(source)) for source in readonly[:12]))
    pending_approval = snapshot.get("pending_approval") if isinstance(snapshot.get("pending_approval"), dict) else {}
    if pending_approval.get("available"):
        lines.append(
            "当前待确认审批："
            f"{pending_approval.get('type_label') or '审批确认'}，"
            f"动作：{_action_label(str(pending_approval.get('action') or '')) or pending_approval.get('action') or '未知'}，"
            f"数量：{pending_approval.get('count', 0)}，"
            + (f"审批单：{pending_approval.get('title')}，" if pending_approval.get("title") else "")
            + f"确认编号：{pending_approval.get('id') or '无'}"
            + (
                f"，有效期：{'已过期' if pending_approval.get('expired') else str(pending_approval.get('expires_in_seconds', 0)) + '秒'}"
                if pending_approval.get("expires_at")
                else ""
            )
            + "。"
        )
    pending_cleanup = snapshot.get("pending_cleanup") if isinstance(snapshot.get("pending_cleanup"), dict) else {}
    if int(pending_cleanup.get("cleaned_count") or 0) > 0:
        cleaned = pending_cleanup.get("cleaned") if isinstance(pending_cleanup.get("cleaned"), list) else []
        lines.append("待确认清理：已清理 " + "、".join(str(item) for item in cleaned) + "，并生成终态回执。")
    if permission.get("available"):
        confirmation_reasons = permission.get("confirmation_reasons") if isinstance(permission.get("confirmation_reasons"), list) else []
        lines.append(
            "权限决策："
            f"{'允许' if permission.get('allowed') else '拒绝'}，"
            f"执行身份：{_execution_identity_label(str(permission.get('execution_identity') or ''))}，"
            f"{'需要确认' if permission.get('requires_confirmation') else '无需确认'}"
            + (f"｜原因：{'、'.join(_confirmation_reason_label(str(item)) for item in confirmation_reasons)}" if confirmation_reasons else "")
        )
    result_context = snapshot.get("result_context") if isinstance(snapshot.get("result_context"), dict) else {}
    if result_context.get("available"):
        sources = result_context.get("sources") if isinstance(result_context.get("sources"), list) else []
        lines.append(
            "当前结果上下文："
            f"{_result_context_kind_label(str(result_context.get('context_kind') or 'query_result'))}"
            f"｜{result_context.get('result_type') or '未知类型'}"
            f"｜{_question_type_label(str(result_context.get('question_type') or ''))}"
            f"｜{result_context.get('display_count', result_context.get('count', 0))} 条"
            f"｜来源：{'、'.join(_source_label(str(source)) for source in sources) or '未知'}"
            f"｜证据 {result_context.get('provider_evidence_count', 0)} 个"
            f"｜证据耗时 {result_context.get('provider_evidence_duration_ms', 0)}ms"
            f"｜剩余 {result_context.get('expires_in_seconds', 0)} 秒"
        )
    if result_context.get("available") and result_context.get("execution_status"):
        lines.append(f"上下文执行状态：{_status_label(str(result_context.get('execution_status') or 'unknown'))}")
    if result_context.get("available"):
        followup_fields = _result_context_followup_fields(result_context)
        if followup_fields:
            lines.append("可追问字段：" + "、".join(followup_fields))
    if result_context.get("available") and result_context.get("empty_result"):
        reason = _empty_reason_label(str(result_context.get("empty_reason") or ""))
        next_step = str(result_context.get("recommended_next_step") or "").strip()
        lines.append("空结果：" + reason + (f"｜建议：{next_step}" if next_step else ""))
    context_event_summary = snapshot.get("result_context_event_summary") if isinstance(snapshot.get("result_context_event_summary"), dict) else {}
    if context_event_summary.get("available"):
        lines.append(
            "结果上下文事件摘要："
            f"事件 {context_event_summary.get('event_count', 0)} 个"
            f"｜保存 {context_event_summary.get('save_count', 0)} 次"
            f"｜清除 {context_event_summary.get('clear_count', 0)} 次"
            f"｜待确认 {context_event_summary.get('pending_confirmation_count', 0)} 次"
            f"｜回执 {context_event_summary.get('action_receipt_count', 0)} 次"
            f"｜最近：{_result_context_event_label(str(context_event_summary.get('latest_action') or ''))}"
            f"｜{context_event_summary.get('latest_result_type') or '无类型'}"
            f"｜items {context_event_summary.get('latest_item_count', 0)} 条"
            f"｜字段 {context_event_summary.get('latest_followup_field_count', 0)} 个"
            f"｜标识 {context_event_summary.get('latest_identity_field_count', 0)} 个"
            f"｜{'items-first' if context_event_summary.get('latest_prefer_items') else '未声明 items-first'}"
            + (f"｜动作:{context_event_summary.get('latest_action_status_group')}" if context_event_summary.get("latest_action_status_group") else "")
            + (f"｜ID {str(context_event_summary.get('latest_action_id') or '')[-8:]}" if context_event_summary.get("latest_action_id") else "")
            + (f"｜确认 {str(context_event_summary.get('latest_confirmation_token') or '')[-8:]}" if context_event_summary.get("latest_confirmation_token") else "")
            + (f"｜路由 {context_event_summary.get('latest_route_path')}" if context_event_summary.get("latest_route_path") else "")
            + "。"
        )
    context_events = snapshot.get("result_context_events") if isinstance(snapshot.get("result_context_events"), list) else []
    if context_events:
        lines.append("结果上下文事件：")
        for event in [item for item in context_events if isinstance(item, dict)][-3:]:
            action = _result_context_event_label(str(event.get("action") or ""))
            result_type = str(event.get("result_type") or "").strip()
            count = event.get("count")
            reason = _result_context_clear_reason_label(str(event.get("reason") or ""))
            source = str(event.get("source") or "").strip()
            token = str(event.get("confirmation_token") or "").strip()
            route_path = str(event.get("route_path") or "").strip()
            lines.append(
                f"- {action}"
                + (f"｜{result_type}" if result_type else "")
                + (f"｜{count} 条" if count not in (None, "") else "")
                + (f"｜原因：{reason}" if reason else "")
                + (f"｜来源：{source}" if source else "")
                + (f"｜确认 {token[-8:]}" if token else "")
                + (f"｜路由 {route_path}" if route_path else "")
            )
    action_lines = _action_trace_summary_lines(context)
    if action_lines:
        lines.append("最近动作：")
        lines.extend(action_lines)
    decision_lines = _decision_trace_summary_lines(context)
    if decision_lines:
        lines.append("最近决策链路：")
        lines.extend(decision_lines)
    return "\n".join(lines)


def _runtime_status_detail_requested(message: str) -> bool:
    text = str(message or "").lower()
    return any(token in text for token in ("诊断详情", "详情", "详细", "完整", "全部", "展开", "debug", "full"))


def _runtime_status_summary_answer_from_snapshot(snapshot: dict[str, Any]) -> str:
    snapshot_summary = snapshot.get("snapshot_summary") if isinstance(snapshot.get("snapshot_summary"), dict) else {}
    pipeline_contract = snapshot.get("pipeline_constitution_contract") if isinstance(snapshot.get("pipeline_constitution_contract"), dict) else {}
    runtime_provider_snapshot = snapshot.get("runtime_provider_snapshot") if isinstance(snapshot.get("runtime_provider_snapshot"), dict) else {}
    result_context_quality = snapshot.get("result_context_quality") if isinstance(snapshot.get("result_context_quality"), dict) else {}
    action_closure = snapshot.get("action_closure") if isinstance(snapshot.get("action_closure"), dict) else {}
    runtime_grade = snapshot.get("runtime_health_grade") if isinstance(snapshot.get("runtime_health_grade"), dict) else {}

    provider_labels = _runtime_status_provider_labels(runtime_provider_snapshot)
    provider_issue_count = int(runtime_provider_snapshot.get("issue_count") or 0)
    provider_available_count = len(provider_labels.get("available", []))
    provider_abnormal = "、".join(provider_labels.get("abnormal", [])[:3]) or "无"
    pipeline_issue_count = int(pipeline_contract.get("issue_count") or 0)
    context_issue_count = int(result_context_quality.get("issue_count") or 0)
    action_status = "正常"
    if action_closure.get("latest_stuck"):
        action_status = "疑似卡住"
    elif action_closure.get("latest_pending"):
        action_status = "处理中"
    elif action_closure.get("latest_available") and not action_closure.get("has_action_receipt_event"):
        action_status = "缺少回执"
    impact = (
        "可能影响当前使用"
        if (not runtime_grade.get("usable") or provider_issue_count > 0 or pipeline_issue_count > 0 or context_issue_count > 0)
        else "不影响当前已接通能力使用"
    )
    lines = [
        "系统诊断：",
        f"结论：{runtime_grade.get('label') or snapshot_summary.get('gate_label') or '未生成'}",
        f"影响：{impact}",
        "",
        "关键状态",
        f"- 主链路：{pipeline_issue_count} 个问题",
        f"- 能力源：{provider_available_count} 类可用，异常：{provider_abnormal}",
        f"- 上下文：{result_context_quality.get('label') or result_context_quality.get('status') or '未生成'}",
        f"- 动作闭环：{action_status}",
    ]
    next_step = _runtime_status_summary_next_step(snapshot)
    if next_step:
        lines.extend(["", f"下一步：{next_step}"])
    lines.append("回复「诊断详情」查看完整技术诊断。")
    return "\n".join(lines)


def _runtime_status_detail_answer_from_snapshot(snapshot: dict[str, Any]) -> str:
    snapshot_summary = snapshot.get("snapshot_summary") if isinstance(snapshot.get("snapshot_summary"), dict) else {}
    pipeline_contract = snapshot.get("pipeline_constitution_contract") if isinstance(snapshot.get("pipeline_constitution_contract"), dict) else {}
    runtime_provider_snapshot = snapshot.get("runtime_provider_snapshot") if isinstance(snapshot.get("runtime_provider_snapshot"), dict) else {}
    planner_snapshot_contract = snapshot.get("planner_runtime_snapshot_contract") if isinstance(snapshot.get("planner_runtime_snapshot_contract"), dict) else {}
    provider_summary = snapshot.get("provider_summary") if isinstance(snapshot.get("provider_summary"), dict) else {}
    latency = provider_summary.get("latency_diagnostics") if isinstance(provider_summary.get("latency_diagnostics"), dict) else {}
    fast_response = snapshot.get("fast_response_contract") if isinstance(snapshot.get("fast_response_contract"), dict) else {}
    timing_health = snapshot.get("timing_health") if isinstance(snapshot.get("timing_health"), dict) else {}
    result_context_quality = snapshot.get("result_context_quality") if isinstance(snapshot.get("result_context_quality"), dict) else {}
    source_contract = snapshot.get("source_execution_contract") if isinstance(snapshot.get("source_execution_contract"), dict) else {}
    followup_health = snapshot.get("followup_health") if isinstance(snapshot.get("followup_health"), dict) else {}
    followup_contract = snapshot.get("followup_consume_contract") if isinstance(snapshot.get("followup_consume_contract"), dict) else {}
    permission = snapshot.get("permission") if isinstance(snapshot.get("permission"), dict) else {}
    permission_health = snapshot.get("permission_health") if isinstance(snapshot.get("permission_health"), dict) else {}
    runtime_grade = snapshot.get("runtime_health_grade") if isinstance(snapshot.get("runtime_health_grade"), dict) else {}
    provider_labels = _runtime_status_provider_labels(runtime_provider_snapshot)
    sources = snapshot_summary.get("sources") if isinstance(snapshot_summary.get("sources"), list) else []
    action_closure = snapshot.get("action_closure") if isinstance(snapshot.get("action_closure"), dict) else {}
    action_status = "正常"
    if action_closure.get("latest_stuck"):
        action_status = "疑似卡住"
    elif action_closure.get("latest_pending"):
        action_status = "处理中"
    elif action_closure.get("latest_available") and not action_closure.get("has_action_receipt_event"):
        action_status = "缺少回执"
    followup_uses_previous = bool(followup_health.get("uses_previous_result") or followup_contract.get("uses_previous_result"))
    permission_decision = "允许" if permission.get("allowed") else "拒绝" if permission.get("available") else "未知"
    permission_identity = _execution_identity_label(str(permission.get("execution_identity") or permission_health.get("execution_identity") or ""))
    detail_lines = [
        "诊断详情：",
        "",
        "一、当前请求",
        f"- 问题类型：{_question_type_label(str(snapshot_summary.get('question_type') or ''))}",
        f"- 执行策略：{_runtime_strategy_display_label(snapshot)}",
        f"- 数据来源：{'、'.join(_source_label(str(source)) for source in sources) or '无'}",
        f"- 执行状态：{_status_label(str(snapshot_summary.get('execution_status') or snapshot.get('execution_status') or 'unknown'))}",
        "",
        "二、主链路",
        f"- 状态：{_human_runtime_label(pipeline_contract.get('label') or pipeline_contract.get('status') or '未生成')}",
        f"- 阶段：{pipeline_contract.get('stage_count', 0)} 个",
        f"- 问题：{pipeline_contract.get('issue_count', 0)} 个",
        "",
        "三、能力源",
        f"- 状态：{_human_runtime_label(runtime_provider_snapshot.get('label') or runtime_provider_snapshot.get('status') or '未生成')}",
        f"- 可用：{'、'.join(provider_labels.get('available', [])) or '无'}",
        f"- 异常：{'、'.join(provider_labels.get('abnormal', [])) or '无'}",
        f"- 规划快照：{_human_runtime_label(planner_snapshot_contract.get('label') or planner_snapshot_contract.get('status') or '未生成')}",
        "",
        "四、执行与上下文",
        f"- 能力执行：成功 {provider_summary.get('success_count', 0)}，失败 {provider_summary.get('error_count', 0)}，无权限 {provider_summary.get('denied_count', 0)}，总耗时 {provider_summary.get('total_duration_ms', 0)}ms",
        f"- 动作闭环：{action_status}",
        f"- 结果上下文：{_human_runtime_label(result_context_quality.get('label') or result_context_quality.get('status') or '未生成')}；结构化结果 {result_context_quality.get('item_count', 0)} 条",
        f"- 追问：{followup_health.get('label') or followup_health.get('status') or '未生成'}；{'命中上一轮结果' if followup_uses_previous else '本轮未触发'}",
        f"- 权限：{permission_health.get('label') or permission_health.get('status') or '未生成'}；决策 {permission_decision}；身份 {permission_identity or '未知'}",
        f"- 长期记忆：{_runtime_status_memory_brief(snapshot)}",
        "",
        "五、数据来源契约",
        (
            f"- {_human_runtime_label(source_contract.get('label') or source_contract.get('status') or '未生成')}"
            f"；计划 {source_contract.get('planned_count', 0)}，已执行 {source_contract.get('executed_planned_count', 0)}，缺失 {source_contract.get('missing_count', 0)}，额外 {source_contract.get('extra_count', 0)}"
        ),
    ]
    next_step = _runtime_status_summary_next_step(snapshot)
    if next_step:
        detail_lines.extend(["", f"下一步：{next_step}"])
    detail_lines.append("治理信息请单独回复「能力清册」「能力目录」或「治理视图」。")
    return "\n".join(detail_lines)
    lines = [
        "诊断详情：",
        (
            "系统："
            f"{runtime_grade.get('label') or snapshot_summary.get('gate_label') or '未生成'}"
            f"｜{runtime_grade.get('reason') or '暂无补充说明'}。"
        ),
        (
            "主链路："
            f"{pipeline_contract.get('label') or pipeline_contract.get('status') or '未生成'}"
            f"｜阶段 {pipeline_contract.get('stage_count', 0)}"
            f"｜问题 {pipeline_contract.get('issue_count', 0)}"
            f"｜本轮 {_question_type_label(str(snapshot_summary.get('question_type') or ''))}"
            f"｜策略 {_action_label(str(snapshot_summary.get('strategy') or snapshot.get('strategy') or ''))}"
            f"｜来源：{'、'.join(_source_label(str(source)) for source in sources) or '无'}。"
        ),
        (
            "Provider Snapshot："
            f"{runtime_provider_snapshot.get('label') or runtime_provider_snapshot.get('status') or '未生成'}"
            f"｜问题 {runtime_provider_snapshot.get('issue_count', 0)}。"
        ),
        (
            "Provider 可用性："
            f"可用：{'、'.join(provider_labels.get('available', [])) or '无'}"
            f"｜异常：{'、'.join(provider_labels.get('abnormal', [])) or '无'}。"
        ),
        _runtime_status_planner_snapshot_line(planner_snapshot_contract),
        (
            "Provider 执行："
            f"成功 {provider_summary.get('success_count', 0)}"
            f"｜失败 {provider_summary.get('error_count', 0)}"
            f"｜无权限 {provider_summary.get('denied_count', 0)}"
            f"｜总耗时 {provider_summary.get('total_duration_ms', 0)}ms。"
        ),
        _runtime_status_latency_line(provider_summary, latency, fast_response, timing_health),
        _runtime_status_action_line(snapshot),
        _runtime_status_result_context_line(result_context_quality),
        _runtime_status_followup_line(followup_health, followup_contract),
        _runtime_status_permission_line(permission, permission_health),
        _runtime_status_memory_line(snapshot),
        (
            "数据来源："
            f"{source_contract.get('label') or source_contract.get('status') or '未生成'}"
            f"｜计划 {source_contract.get('planned_count', 0)}"
            f"｜已执行 {source_contract.get('executed_planned_count', 0)}"
            f"｜缺失 {source_contract.get('missing_count', 0)}"
            f"｜额外 {source_contract.get('extra_count', 0)}"
            f"｜顺序：{'异常' if source_contract.get('order_mismatch') else '正常'}。"
        ),
    ]
    correlation = snapshot.get("correlation") if isinstance(snapshot.get("correlation"), dict) else {}
    if correlation:
        lines.append(
            "追踪："
            f"trace_id={correlation.get('trace_id') or snapshot.get('trace_id') or '无'}"
            + (f"｜query={correlation.get('query_id_tail')}" if correlation.get("query_id_tail") else "")
            + (f"｜chat={correlation.get('chat_id_tail')}" if correlation.get("chat_id_tail") else "")
            + "。"
        )
    next_step = _runtime_status_summary_next_step(snapshot)
    if next_step:
        lines.append(f"下一步：{next_step}")
    lines.append("治理信息请单独回复「能力清册」「能力目录」或「治理视图」。")
    return "\n".join(lines)


def _runtime_status_latency_line(
    provider_summary: dict[str, Any],
    latency: dict[str, Any],
    fast_response: dict[str, Any],
    timing_health: dict[str, Any],
) -> str:
    def _int_value(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    total_ms = _int_value(provider_summary.get("total_duration_ms") or timing_health.get("total_ms"))
    slow_provider_source = str(
        latency.get("slowest_provider_source")
        or timing_health.get("slowest_provider_source")
        or ""
    )
    slow_provider_ms = _int_value(
        latency.get("slowest_provider_ms")
        or timing_health.get("slowest_provider_ms")
    )
    top_slow = latency.get("top_slow_item") if isinstance(latency.get("top_slow_item"), dict) else {}
    top_issue = (
        fast_response.get("top_issue")
        if isinstance(fast_response.get("top_issue"), dict)
        else {}
    )

    if top_slow:
        source = str(top_slow.get("source") or top_slow.get("provider") or slow_provider_source)
        operation = str(top_slow.get("operation") or top_slow.get("step") or top_slow.get("name") or "")
        duration_ms = _int_value(
            top_slow.get("duration_ms")
            or top_slow.get("elapsed_ms")
            or top_slow.get("ms")
            or slow_provider_ms
        )
        slowest = _source_label(source) if source else "未知步骤"
        if operation:
            slowest = f"{slowest}/{operation}"
        if duration_ms:
            slowest = f"{slowest} {duration_ms}ms"
    elif slow_provider_source:
        slowest = f"{_source_label(slow_provider_source)} {slow_provider_ms}ms" if slow_provider_ms else _source_label(slow_provider_source)
    else:
        slowest = "无"

    suggestion = str(
        top_issue.get("next_step")
        or top_slow.get("next_step")
        or top_slow.get("recommendation")
        or ""
    ).strip()
    if not suggestion and fast_response.get("fast_ack_required"):
        suggestion = "先快速回复，再异步完成慢步骤"

    line = (
        "响应体验："
        f"{latency.get('label') or timing_health.get('label') or '未生成'}"
        f"｜总耗时 {total_ms}ms"
        f"｜最慢：{slowest}"
        f"｜慢能力 {latency.get('slow_provider_count', 0)}"
        f"｜慢步骤 {latency.get('slow_substep_count', 0)}"
        f"｜快速回复：{'需要' if fast_response.get('fast_ack_required') else '非必需'}"
        f"｜后台处理：{'建议' if fast_response.get('async_recommended') else '非必需'}"
    )
    if suggestion:
        line += f"｜建议：{suggestion}"
    return f"{line}。"


def _runtime_status_planner_snapshot_line(contract: dict[str, Any]) -> str:
    planned_sources = contract.get("planned_sources") if isinstance(contract.get("planned_sources"), list) else []
    issues = contract.get("issues") if isinstance(contract.get("issues"), list) else []
    if not contract:
        return "Planner Snapshot：未生成。"
    issue_text = "无"
    if issues:
        parts = []
        for item in [entry for entry in issues if isinstance(entry, dict)][:4]:
            source = _source_label(str(item.get("source") or ""))
            label = str(item.get("label") or item.get("kind") or "异常")
            parts.append(f"{source}({label})")
        issue_text = "、".join(parts) or "有异常"
    return (
        "Planner Snapshot："
        f"{contract.get('label') or contract.get('status') or '未生成'}"
        f"｜已消费：{'是' if contract.get('consumed') else '否'}"
        f"｜规划来源：{'、'.join(_source_label(str(source)) for source in planned_sources) or '无'}"
        f"｜操作就绪 {contract.get('ready_operation_count', 0)}/{contract.get('planned_operation_count', 0)}"
        f"｜阻断 {contract.get('blocked_operation_count', 0)}"
        f"｜异常：{issue_text}。"
    )


def _runtime_status_path_line(snapshot: dict[str, Any], current_path_maturity: dict[str, Any]) -> str:
    runtime_mode = str(snapshot.get("runtime_mode") or "unknown")
    runtime_path_kind = str(snapshot.get("runtime_path_kind") or "")
    bypass_reason = str(snapshot.get("bypass_reason") or "").strip()
    explicit_user_requested = bool(current_path_maturity.get("explicit_user_requested"))
    migration_items = (
        current_path_maturity.get("migration_items")
        if isinstance(current_path_maturity.get("migration_items"), list)
        else []
    )
    bypassed_stages: list[str] = []
    for item in migration_items:
        if not isinstance(item, dict):
            continue
        stages = item.get("bypassed_stages") if isinstance(item.get("bypassed_stages"), list) else []
        for stage in stages:
            if str(stage) not in bypassed_stages:
                bypassed_stages.append(str(stage))
    mode_label = {
        "full_runtime": "完整 V5 主链路",
        "manual_fast_path": "手工快速路径",
        "v5_disabled": "V5 已禁用",
    }.get(runtime_mode, runtime_mode or "未知")
    path_label = {
        "v5_pipeline": "V5 Pipeline",
        "approval_fast_path": "审批快速摘要",
        "approval_workbench_path": "审批工作台",
        "manual_provider_path": "手工 Provider 编排",
        "legacy_fallback_blocked": "旧链路已阻断",
    }.get(runtime_path_kind, runtime_path_kind or "未知")
    full_path = runtime_mode == "full_runtime" and not bypassed_stages
    line = (
        "当前路径："
        f"{mode_label}｜{path_label}"
        f"｜完整主链路：{'是' if full_path else '否'}"
        f"｜显式请求：{'是' if explicit_user_requested else '否'}"
        f"｜绕过阶段 {len(bypassed_stages)} 个"
    )
    if bypass_reason:
        line += f"｜原因：{bypass_reason}"
    return f"{line}。"


def _runtime_status_provider_labels(snapshot: dict[str, Any]) -> dict[str, list[str]]:
    providers = snapshot.get("providers") if isinstance(snapshot.get("providers"), dict) else {}
    available: list[str] = []
    abnormal: list[str] = []
    for source, payload in sorted(providers.items()):
        if not isinstance(payload, dict):
            continue
        label = _source_label(str(source))
        if payload.get("enabled") and payload.get("healthy"):
            available.append(label)
        else:
            abnormal.append(label)
    coverage = snapshot.get("coverage") if isinstance(snapshot.get("coverage"), list) else []
    for item in coverage:
        if not isinstance(item, dict) or item.get("ready"):
            continue
        source = str(item.get("source") or "")
        status = str(item.get("status") or "")
        reason = {
            "provider_missing": "缺失",
            "provider_disabled": "未启用",
            "provider_unhealthy": "不健康",
            "operation_not_covered": "缺操作",
            "operation_disabled": "操作未启用",
        }.get(status, "异常")
        label = f"{_source_label(source)}({reason})"
        if label not in abnormal:
            abnormal.append(label)
    return {"available": available[:8], "abnormal": abnormal[:8]}


def _runtime_status_memory_line(snapshot: dict[str, Any]) -> str:
    provider_summary = snapshot.get("provider_summary") if isinstance(snapshot.get("provider_summary"), dict) else {}
    source_contract = snapshot.get("source_execution_contract") if isinstance(snapshot.get("source_execution_contract"), dict) else {}
    runtime_provider_snapshot = snapshot.get("runtime_provider_snapshot") if isinstance(snapshot.get("runtime_provider_snapshot"), dict) else {}
    planned_sources = source_contract.get("planned_sources") if isinstance(source_contract.get("planned_sources"), list) else []
    providers = runtime_provider_snapshot.get("providers") if isinstance(runtime_provider_snapshot.get("providers"), dict) else {}
    memory_provider = providers.get("memory") if isinstance(providers.get("memory"), dict) else {}
    by_source = provider_summary.get("by_source") if isinstance(provider_summary.get("by_source"), list) else []
    memory_result = next((item for item in by_source if isinstance(item, dict) and str(item.get("source") or "") == "memory"), {})
    memory_planned = "memory" in {str(source) for source in planned_sources}
    if memory_result:
        success = int(memory_result.get("success_count") or 0)
        error = int(memory_result.get("error_count") or 0)
        denied = int(memory_result.get("denied_count") or 0)
        duration_ms = int(memory_result.get("duration_ms") or memory_result.get("total_duration_ms") or 0)
        if error or denied:
            return f"Memory：本轮已调用｜成功 {success}｜失败 {error}｜无权限 {denied}｜影响：可能影响长期知识参考。"
        return f"Memory：本轮已调用｜成功 {success}｜耗时 {duration_ms}ms｜状态正常。"
    if memory_planned:
        return "Memory：本轮计划使用，但未看到执行结果｜影响：可能影响长期知识参考。"
    if memory_provider:
        status = "可用" if memory_provider.get("enabled") and memory_provider.get("healthy") else "异常"
        return f"Memory：本轮未调用｜Provider {status}。"
    return "Memory：本轮未调用｜未注册 Memory Provider。"


def _runtime_status_memory_brief(snapshot: dict[str, Any]) -> str:
    runtime_provider_snapshot = snapshot.get("runtime_provider_snapshot") if isinstance(snapshot.get("runtime_provider_snapshot"), dict) else {}
    providers = runtime_provider_snapshot.get("providers") if isinstance(runtime_provider_snapshot.get("providers"), dict) else {}
    memory_provider = providers.get("memory") if isinstance(providers.get("memory"), dict) else {}
    if memory_provider:
        return "执行源可用" if memory_provider.get("enabled") and memory_provider.get("healthy") else "执行源异常"
    return "本轮未调用"


def _runtime_strategy_display_label(snapshot: dict[str, Any]) -> str:
    snapshot_summary = snapshot.get("snapshot_summary") if isinstance(snapshot.get("snapshot_summary"), dict) else {}
    strategy = str(snapshot_summary.get("strategy") or snapshot.get("strategy") or "")
    if strategy == "runtime_status":
        return "系统诊断"
    if strategy == "governance_view":
        return "治理视图"
    return _action_label(strategy)


def _human_runtime_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "未生成"
    for old, new in (
        ("Runtime Provider Snapshot", "运行能力快照"),
        ("Planner Snapshot", "规划快照"),
        ("Planner", "规划器"),
        ("Provider", "执行源"),
        ("Result Context", "结果上下文"),
        ("Memory", "长期记忆"),
        ("V5 主链路契约完整", "主链路契约完整"),
    ):
        text = text.replace(old, new)
    return text


def _runtime_status_action_line(snapshot: dict[str, Any]) -> str:
    action_closure = snapshot.get("action_closure") if isinstance(snapshot.get("action_closure"), dict) else {}
    pending_action = snapshot.get("pending_action") if isinstance(snapshot.get("pending_action"), dict) else {}
    pending_approval = snapshot.get("pending_approval") if isinstance(snapshot.get("pending_approval"), dict) else {}
    pending_bits: list[str] = []
    if pending_action.get("available"):
        pending_bits.append("通用待确认已过期" if pending_action.get("expired") else "通用待确认")
    if pending_approval.get("available"):
        pending_bits.append("审批待确认已过期" if pending_approval.get("expired") else "审批待确认")
    pending_text = "、".join(pending_bits) if pending_bits else "无待确认"
    if not action_closure.get("latest_available"):
        return f"动作闭环：无最近动作｜{pending_text}。"
    status = str(action_closure.get("contract_label") or action_closure.get("latest_status") or "未知")
    receipt = "已关联回执" if action_closure.get("has_correlated_receipt") else "有回执未关联" if action_closure.get("has_action_receipt_event") else "无回执"
    terminal = "终态" if action_closure.get("latest_terminal") else "处理中" if action_closure.get("latest_pending") else "非终态"
    stuck = "疑似卡住" if action_closure.get("latest_stuck") else "未超时"
    age = int(action_closure.get("latest_age_seconds") or 0)
    action_id = str(action_closure.get("latest_action_id") or "").strip()
    receipt_status = str(action_closure.get("latest_receipt_status") or "").strip()
    receipt_bits = []
    if action_id:
        receipt_bits.append(f"编号 {action_id[-8:]}")
    if receipt_status:
        receipt_bits.append(f"回执状态 {_status_label(receipt_status)}")
    receipt_detail = "｜" + "｜".join(receipt_bits) if receipt_bits else ""
    return f"动作闭环：{status}｜{terminal}｜{receipt}{receipt_detail}｜{stuck}｜等待 {age}s｜{pending_text}。"


def _runtime_status_result_context_line(result_context_quality: dict[str, Any]) -> str:
    followup_field_count = int(result_context_quality.get("followup_field_count") or 0)
    identity_field_count = int(result_context_quality.get("item_identity_field_count") or 0)
    prefer_items = bool(result_context_quality.get("prefer_items"))
    supports_index = bool(result_context_quality.get("supports_index_followup"))
    supports_detail = bool(result_context_quality.get("supports_detail_followup"))
    answer_fallback = bool(result_context_quality.get("allow_answer_fallback"))
    items_normalized = bool(result_context_quality.get("items_normalized"))
    index_base = int(result_context_quality.get("index_base") or 0)
    return (
        "Result Context："
        f"{result_context_quality.get('label') or result_context_quality.get('status') or '未生成'}"
        f"｜结构化结果 {result_context_quality.get('item_count', 0)}/{result_context_quality.get('count', 0)}"
        f"｜规范化：{'是' if items_normalized else '否'}"
        f"｜索引基准：{index_base or '未知'}"
        f"｜优先读取 {_runtime_summary_consume_source_label(str(result_context_quality.get('primary_consume_source') or ''))}"
        f"｜追问字段 {followup_field_count}"
        f"｜身份字段 {identity_field_count}"
        f"｜索引追问：{'支持' if supports_index else '未知/不支持'}"
        f"｜详情追问：{'支持' if supports_detail else '未知/不支持'}"
        f"｜answer兜底：{'允许' if answer_fallback else '禁用'}"
        f"｜items优先：{'是' if prefer_items else '否'}。"
    )


def _runtime_status_followup_line(
    followup_health: dict[str, Any],
    followup_contract: dict[str, Any],
) -> str:
    uses_previous = bool(followup_health.get("uses_previous_result") or followup_contract.get("uses_previous_result"))
    followup_type = str(followup_health.get("followup_type") or followup_contract.get("followup_type") or "")
    prefer_items = bool(followup_contract.get("prefer_items"))
    allow_answer_fallback = bool(followup_contract.get("allow_answer_fallback"))
    item_count = int(followup_contract.get("item_count") or followup_health.get("item_count") or 0)
    issue_count = int(followup_health.get("issue_count") or 0) + int(followup_contract.get("issue_count") or 0)
    context_kind = str(followup_health.get("context_kind") or followup_contract.get("context_kind") or "")
    hit_text = "命中动作回执" if uses_previous and context_kind == "action_receipt" else "命中结构化结果" if uses_previous and item_count > 0 else "未命中上一轮结果" if uses_previous else "未触发"
    return (
        "Follow-up："
        f"{followup_health.get('label') or followup_health.get('status') or '未生成'}"
        f"｜{'追问' if uses_previous else '非追问'}"
        f"｜类型：{_followup_type_label(followup_type)}"
        f"｜{hit_text}"
        f"｜items {item_count}"
        f"｜items优先：{'是' if prefer_items else '否'}"
        f"｜answer兜底：{'允许' if allow_answer_fallback else '禁用'}"
        f"｜问题 {issue_count} 个。"
    )


def _runtime_status_permission_line(permission: dict[str, Any], permission_health: dict[str, Any]) -> str:
    if not permission and not permission_health:
        return "Permission：未生成。"
    available = bool(permission.get("available"))
    allowed = "允许" if permission.get("allowed") else "拒绝" if available else "未知"
    identity = _execution_identity_label(str(permission.get("execution_identity") or permission_health.get("execution_identity") or ""))
    requires_confirmation = "需确认" if permission.get("requires_confirmation") else "无需确认"
    confirmation_reasons = permission.get("confirmation_reasons") if isinstance(permission.get("confirmation_reasons"), list) else []
    reason_text = "、".join(_confirmation_reason_label(str(item)) for item in confirmation_reasons) if confirmation_reasons else ""
    source_capabilities = permission.get("source_capabilities") if isinstance(permission.get("source_capabilities"), list) else []
    issue_count = int(permission_health.get("issue_count") or 0)
    denied_reason = str(permission.get("reason") or "").strip()
    return (
        "Permission："
        f"{permission_health.get('label') or permission_health.get('status') or '未生成'}"
        f"｜决策：{allowed}"
        f"｜身份：{identity or '未知'}"
        f"｜{requires_confirmation}"
        f"｜来源能力 {len(source_capabilities)}"
        f"｜问题 {issue_count} 个"
        + (f"｜确认原因：{reason_text}" if reason_text else "")
        + (f"｜拒绝原因：{denied_reason}" if denied_reason else "")
        + "。"
    )


def _runtime_summary_consume_source_label(source: str) -> str:
    return {
        "items": "结构化结果",
        "metadata": "上下文信息",
        "answer": "展示摘要",
        "none": "无可用缓存",
    }.get(source, source or "未知")


def _runtime_status_summary_next_step(snapshot: dict[str, Any]) -> str:
    candidates: list[str] = []
    path_next_step = _runtime_path_next_step(snapshot)
    if path_next_step:
        candidates.append(path_next_step)
    runtime_grade = snapshot.get("runtime_health_grade") if isinstance(snapshot.get("runtime_health_grade"), dict) else {}
    if runtime_grade.get("primary_next_step"):
        candidates.append(str(runtime_grade.get("primary_next_step") or ""))
    provider_snapshot_next_step = _runtime_provider_snapshot_next_step(snapshot)
    if provider_snapshot_next_step:
        candidates.append(provider_snapshot_next_step)
    for container_key, item_key, next_key in (
        ("fast_response_contract", "top_issue", "next_step"),
        ("action_closure", "top_repair_item", "next_step"),
        ("provider_summary", "evidence_contract", "top_issue"),
        ("provider_summary", "latency_diagnostics", "top_slow_item"),
        ("result_context_quality", "top_repair_item", "next_step"),
        ("source_execution_contract", "issues", "recommendation"),
    ):
        container = snapshot.get(container_key) if isinstance(snapshot.get(container_key), dict) else {}
        item: Any = {}
        if container_key == "provider_summary":
            nested = container.get(item_key) if isinstance(container.get(item_key), dict) else {}
            item = nested.get(next_key) if isinstance(nested.get(next_key), dict) else {}
            value = str(item.get("next_step") or "").strip()
        elif item_key == "issues":
            issues = container.get("issues") if isinstance(container.get("issues"), list) else []
            item = issues[0] if issues and isinstance(issues[0], dict) else {}
            value = str(item.get(next_key) or "").strip()
        else:
            item = container.get(item_key) if isinstance(container.get(item_key), dict) else {}
            value = str(item.get(next_key) or "").strip()
        if value:
            candidates.append(value)
    snapshot_summary = snapshot.get("snapshot_summary") if isinstance(snapshot.get("snapshot_summary"), dict) else {}
    if snapshot_summary.get("next_step"):
        candidates.append(str(snapshot_summary.get("next_step") or ""))
    return _runtime_status_product_next_step(candidates[0]) if candidates else ""


def _runtime_path_next_step(snapshot: dict[str, Any]) -> str:
    current_path = snapshot.get("current_path_maturity") if isinstance(snapshot.get("current_path_maturity"), dict) else {}
    runtime_mode = str(snapshot.get("runtime_mode") or "")
    if runtime_mode == "full_runtime":
        return ""
    if current_path.get("explicit_user_requested"):
        return "把显式工作台体验迁入 V5 Composer/Card Renderer，保留交互体验，但由 Planner/Router/Provider 提供数据。"
    if runtime_mode == "manual_fast_path":
        return "先把该手工快速路径切回完整 V5 Pipeline，避免绕过 Intent、Planner、Permission 和 Router。"
    if runtime_mode == "v5_disabled":
        return "先恢复 V5 Runtime 开关，禁止静默回退到旧链路。"
    return ""


def _runtime_provider_snapshot_next_step(snapshot: dict[str, Any]) -> str:
    runtime_provider_snapshot = snapshot.get("runtime_provider_snapshot") if isinstance(snapshot.get("runtime_provider_snapshot"), dict) else {}
    if int(runtime_provider_snapshot.get("issue_count") or 0) <= 0:
        return ""
    reason_map = {
        "provider_missing": "Provider 缺失",
        "provider_disabled": "Provider 未启用",
        "provider_unhealthy": "Provider 不健康",
        "operation_not_covered": "能力未覆盖",
        "operation_disabled": "能力未启用",
    }
    coverage = runtime_provider_snapshot.get("coverage") if isinstance(runtime_provider_snapshot.get("coverage"), list) else []
    for item in coverage:
        if not isinstance(item, dict) or item.get("ready"):
            continue
        source = str(item.get("source") or "")
        operation = str(item.get("operation") or "")
        status = str(item.get("status") or "")
        reason = reason_map.get(status, status or "异常")
        target = _source_label(source) if source else "未知 Provider"
        if operation:
            target = f"{target}/{operation}"
        return f"先处理 {target}：{reason}，避免 Planner 选择到不可执行来源。"
    providers = runtime_provider_snapshot.get("providers") if isinstance(runtime_provider_snapshot.get("providers"), dict) else {}
    for source, payload in sorted(providers.items()):
        if not isinstance(payload, dict):
            continue
        if payload.get("enabled") and payload.get("healthy"):
            continue
        if not payload.get("enabled"):
            reason = "Provider 未启用"
        elif not payload.get("healthy"):
            reason = "Provider 不健康"
        else:
            reason = "Provider 异常"
        return f"先处理 {_source_label(str(source))}：{reason}，恢复运行时可用状态。"
    return "先处理 Runtime Provider Snapshot 中的异常项。"


def _runtime_status_product_next_step(next_step: str) -> str:
    text = str(next_step or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if "快速首包" in text or "已收到" in text or "fast" in lowered:
        return "先给用户快速回复“已收到，正在处理”，再继续执行慢步骤。"
    if "后台" in text or "异步" in text or "worker" in lowered or "queue" in lowered:
        return "把耗时步骤放到后台处理，完成后再发结果通知。"
    if "action_receipt" in text or "回执" in text or "最终结果" in text:
        return "动作完成后补发清晰的结果通知，说明成功、失败或取消原因。"
    if "Result Context" in text or "items" in text or "结构化" in text:
        return "把本轮结果保存成结构化数据，方便继续问“第一个详情”“这些人是谁”。"
    if "Provider" in text or "provider" in lowered or "飞书 api" in lowered or "API" in text:
        return "检查对应飞书能力是否接通，并确认返回了可读结果和错误原因。"
    if "Planner" in text or "Router" in text or "Capability" in text:
        return "对齐问题规划、能力清单和实际执行来源，避免漏执行或走错能力。"
    if "缓存" in text or "cache" in lowered:
        return "优先复用最近结果或短期缓存，避免每次都重新全量读取。"
    if "附件" in text:
        return "列表先展示摘要，附件内容改为点详情时再读取。"
    if "LLM" in text or "AI" in text:
        return "把 AI 判断合并成批量处理，必要时放到后台生成。"
    return text


def _runtime_drift_lines(status_summary: dict, drift: dict, planner_drift: dict) -> list[str]:
    lines = [
        f"Capability/Provider 漂移：缺 Provider {drift.get('declared_missing_provider_count', 0)} 项，未声明 Provider 操作 {drift.get('provider_missing_declaration_count', 0)} 项。",
        f"Planner/Capability 漂移：Planner 缺能力声明 {planner_drift.get('planner_missing_capability_count', 0)} 项，Capability 未被 Planner 使用 {planner_drift.get('capability_missing_planner_count', 0)} 项。",
    ]
    declared_missing = status_summary.get("declared_missing_provider") if isinstance(status_summary.get("declared_missing_provider"), list) else []
    if declared_missing:
        lines.append("优先补齐：")
        for item in declared_missing[:5]:
            if isinstance(item, dict):
                label = item.get("label") or item.get("strategy") or ""
                source = _source_label(str(item.get("source") or ""))
                operation = item.get("operation") or ""
                lines.append(f"- {label}｜{source}.{operation}")
    return lines


def _runtime_next_step(drift: dict, planner_drift: dict, pending_ops: int | str) -> str:
    declared_missing = int(drift.get("declared_missing_provider_count") or 0)
    provider_extra = int(drift.get("provider_missing_declaration_count") or 0)
    provider_extra_items = drift.get("provider_missing_declaration") if isinstance(drift.get("provider_missing_declaration"), list) else []
    provider_extra_write_count = len([item for item in provider_extra_items if isinstance(item, dict) and item.get("is_write")])
    planner_missing = int(planner_drift.get("planner_missing_capability_count") or 0)
    capability_unused = int(planner_drift.get("capability_missing_planner_count") or 0)
    pending_operation_count = int(pending_ops or 0)
    if declared_missing:
        return "先补 Capability 已声明但 Provider 未实现的能力，避免规划后无法执行。"
    if provider_extra_write_count:
        return "先把 Provider 已有但 Capability 未声明的写操作补进能力清单，确保权限和二次确认不被绕过。"
    if pending_operation_count:
        return "先接入待补原子操作，尤其是动作类和高频查询类。"
    if planner_missing:
        return "先补 Planner 到 Capability 的声明，避免策略绕过能力清单。"
    if provider_extra:
        return "把 Provider 已支持但未声明的操作补进 Capability 清单。"
    if capability_unused:
        return "清理或接入 Capability 已声明但 Planner 暂未使用的能力。"
    return "主链路映射暂时一致，可以继续扩业务场景或做端到端验证。"


def _runtime_pipeline_timing_advice(pipeline_timing: dict) -> str:
    try:
        slowest_ms = int(pipeline_timing.get("slowest_ms") or 0)
        total_ms = int(pipeline_timing.get("total_ms") or 0)
    except (TypeError, ValueError):
        return ""
    if slowest_ms < 2000 and total_ms < 4000:
        return ""
    slowest_stage = str(pipeline_timing.get("slowest_stage") or "").strip()
    if slowest_stage in {"intent_recognition", "task_planner", "answer_composer"}:
        return "慢点在语义理解或答案组织，优先看 LLM 调用、提示词长度和是否发生重复生成。"
    if slowest_stage in {"capability_router", "execution"}:
        return "慢点在能力执行，优先看 Provider 耗时、飞书 API 响应、附件/OCR 或批量写入。"
    if slowest_stage == "permission_check":
        return "慢点在权限检查，优先看身份加载、公司范围和权限来源聚合。"
    if slowest_stage == "result_followup_detector":
        return "慢点在追问识别，优先看 Result Context 体积和序号/指代解析。"
    if slowest_stage == "pre_gateway":
        return "慢点在入口上下文加载，优先看 Redis、Profile 和会话上下文读取。"
    return "本轮耗时偏高，优先结合 Provider 耗时和最近决策链路定位。"


def _action_trace_summary_lines(context: RuntimeContext) -> list[str]:
    traces = context.session_context.get("runtime_v5_action_trace") if isinstance(context.session_context, dict) else None
    if not isinstance(traces, list):
        return []
    lines: list[str] = []
    for item in [entry for entry in traces if isinstance(entry, dict)][-3:]:
        kind = str(item.get("kind") or "操作")
        action = str(item.get("action") or "").strip()
        status = str(item.get("status") or "").strip()
        lines.append(f"- {_action_kind_label(kind)}｜{_action_label(action)}｜{_status_label(status)}")
        context_parts = []
        strategy = str(item.get("strategy") or "").strip()
        sources = item.get("sources") if isinstance(item.get("sources"), list) else []
        execution_identity = str(item.get("execution_identity") or "").strip()
        confirmation_token = str(item.get("confirmation_token") or "").strip()
        if strategy:
            context_parts.append(f"策略：{_action_label(strategy)}")
        if sources:
            context_parts.append("来源：" + "、".join(_source_label(str(source)) for source in sources))
        if execution_identity:
            context_parts.append(f"身份：{_execution_identity_label(execution_identity)}")
        if confirmation_token:
            context_parts.append(f"确认令牌：{confirmation_token[-8:]}")
        if item.get("requires_confirmation"):
            reasons = item.get("confirmation_reasons") if isinstance(item.get("confirmation_reasons"), list) else []
            reason_text = "、".join(_confirmation_reason_label(str(reason)) for reason in reasons) if reasons else "需要确认"
            context_parts.append(f"确认：{reason_text}")
        if context_parts:
            lines.append("  " + "｜".join(context_parts))
        error = str(item.get("error") or "").strip()
        if error:
            lines.append(f"  错误：{error[:120]}")
        recommended_next_step = str(item.get("recommended_next_step") or "").strip()
        if recommended_next_step:
            lines.append(f"  建议：{recommended_next_step}")
    return lines


def _decision_trace_summary_lines(context: RuntimeContext) -> list[str]:
    traces = context.session_context.get("runtime_v5_decision_trace") if isinstance(context.session_context, dict) else None
    if not isinstance(traces, list):
        return []
    lines: list[str] = []
    for item in [entry for entry in traces if isinstance(entry, dict)][-3:]:
        question_type = _question_type_label(str(item.get("question_type") or ""))
        strategy = _action_label(str(item.get("strategy") or item.get("intent") or "未知策略"))
        status = _status_label(str(item.get("execution_status") or "unknown"))
        identity = _execution_identity_label(str(item.get("execution_identity") or ""))
        result_type = str(item.get("result_type") or "").strip()
        parts = [question_type, strategy, status, identity, "需确认" if item.get("requires_confirmation") else "无需确认"]
        if result_type:
            parts.append(result_type)
        lines.append("- " + "｜".join(parts))
        path_label = str(item.get("path_maturity_label") or item.get("path_maturity_status") or "").strip()
        if path_label:
            lines.append(
                f"  路径：{path_label}"
                f"｜迁移 {item.get('path_migration_count', 0)} 项"
                f"｜来源缺失 {item.get('source_contract_missing_count', 0)} 项"
            )
        capability_label = str(item.get("capability_readiness_label") or item.get("capability_readiness_status") or "").strip()
        if capability_label:
            lines.append(
                f"  能力：{capability_label}"
                f"｜就绪 {item.get('capability_ready_count', 0)}/{item.get('capability_declared_count', 0)}"
                f"｜问题 {item.get('capability_issue_count', 0)} 个"
            )
        integrity_label = str(item.get("snapshot_integrity_label") or item.get("snapshot_integrity_status") or "").strip()
        if integrity_label:
            lines.append(
                f"  诊断：{integrity_label}"
                f"｜缺失 {item.get('snapshot_missing_count', 0)}"
                f"｜空模块 {item.get('snapshot_empty_count', 0)}"
            )
        event_count = int(item.get("result_context_event_count") or 0)
        if event_count:
            latest_action = _result_context_event_label(str(item.get("result_context_latest_action") or ""))
            lines.append(
                f"  上下文事件：{event_count} 个"
                f"｜最近 {latest_action or '未知'}"
                f"｜{'items-first' if item.get('result_context_latest_items_first') else '未声明 items-first'}"
                + (f"｜动作 {item.get('result_context_latest_action_status_group')}" if item.get("result_context_latest_action_status_group") else "")
                + (f"｜ID {str(item.get('result_context_latest_action_id') or '')[-8:]}" if item.get("result_context_latest_action_id") else "")
            )
        action_status = str(item.get("action_latest_status") or "").strip()
        if action_status:
            lines.append(
                f"  动作：{_status_label(action_status)}"
                f"｜回执：{'已关联' if item.get('action_has_correlated_receipt') else '已有' if item.get('action_has_receipt') else '无'}"
            )
        provider_total = int(item.get("provider_success_count") or 0) + int(item.get("provider_error_count") or 0) + int(item.get("provider_denied_count") or 0)
        if provider_total:
            lines.append(
                f"  Provider：成功 {item.get('provider_success_count', 0)}，"
                f"失败 {item.get('provider_error_count', 0)}，"
                f"无权限 {item.get('provider_denied_count', 0)}，"
                f"耗时 {item.get('provider_total_duration_ms', 0)}ms"
            )
        locator = str(item.get("debug_locator") or "").strip()
        primary_issue = str(item.get("primary_issue_fingerprint") or "").strip()
        if locator or primary_issue:
            lines.append("  定位：" + (locator or f"主问题 #{primary_issue}"))
    return lines


def _action_kind_label(kind: str) -> str:
    return {
        "runtime_confirmation": "确认操作",
        "approval_single": "单笔审批",
        "approval_batch": "批量审批",
        "runtime_action": "运行操作",
    }.get(kind, kind)


def _data_scope_label(data_scope: str) -> str:
    return {
        "self": "本人",
        "person": "人员",
        "department": "部门",
        "company": "公司",
        "project": "项目",
        "organization": "组织",
        "external": "外部",
    }.get(data_scope, data_scope or "未知范围")


def _followup_type_label(followup_type: str) -> str:
    return {
        "pronoun": "指代追问",
        "detail": "详情追问",
        "expand": "展开追问",
        "position": "序号追问",
        "receipt_detail": "动作回执追问",
    }.get(followup_type, followup_type or "无")


def _action_label(action: str) -> str:
    return {
        "confirm": "确认",
        "cancel": "取消",
        "prepare_confirm": "准备确认",
        "confirmation_without_pending_action": "无待确认操作",
        "approve": "通过",
        "reject": "拒绝",
        "transfer": "转交",
        "add_sign": "加签",
        "rollback": "退回",
        "remind": "催办",
        "cc": "抄送",
        "approval_query": "审批查询",
        "approval_detail": "审批详情",
        "approval_approve": "审批通过",
        "approval_reject": "审批拒绝",
        "approval_transfer": "审批转交",
        "approval_add_sign": "审批加签",
        "approval_rollback": "审批退回",
        "approval_remind": "审批催办",
        "approval_cancel": "审批撤回",
        "approval_cc": "审批抄送",
        "approval_initiated": "我发起的审批",
        "people_lookup": "人员查询",
        "department_members": "部门成员查询",
        "organization_snapshot": "组织架构查询",
        "organization_export": "导出组织架构",
        "send_result": "发送结果",
        "message_send": "发送消息",
        "message_query": "消息查询",
        "chat_search": "群聊搜索",
        "chat_create": "创建群聊",
        "task_query": "任务查询",
        "task_search": "任务搜索",
        "task_create": "创建任务",
        "task_complete": "完成任务",
        "calendar_query": "日程查询",
        "calendar_create": "创建日程",
        "mail_query": "最近邮件",
        "mail_search": "邮件搜索",
        "mail_get_message": "邮件详情",
        "mail_draft_create": "创建邮件草稿",
        "company_intro": "公司介绍",
        "risk_analysis": "风险分析",
        "general_analysis": "综合分析",
        "decision_advice": "决策建议",
        "general_query": "通用查询",
        "list_pending": "查询待审批",
        "get_detail": "读取详情",
        "list_initiated": "查询我发起的审批",
        "search_person": "查找人员",
        "list_department_members": "查询部门成员",
        "get_org_snapshot": "读取组织架构",
        "write_records": "写入表格",
        "list_my_tasks": "查询我的任务",
        "search_tasks": "搜索任务",
        "create_task": "创建任务",
        "complete_task": "完成任务",
        "list_events": "查询日程",
        "create_event": "创建日程",
        "list_recent": "查询最近邮件",
        "search_messages": "搜索消息",
        "get_message": "读取邮件详情",
        "create_draft": "创建草稿",
        "send_message": "发送消息",
        "search_chats": "搜索群聊",
        "list_messages": "查询消息",
        "create_chat": "创建群聊",
    }.get(action, action)


def _runtime_pipeline_label(name: str) -> str:
    return {
        "pre_gateway": "入口上下文",
        "result_followup_detector": "追问识别",
        "intent_recognition": "意图识别",
        "task_planner": "任务规划",
        "permission_check": "权限检查",
        "capability_router": "能力路由",
        "execution": "执行",
        "answer_composer": "答案组织",
    }.get(name, name)


def _status_label(status: str) -> str:
    return {
        "loaded": "已加载",
        "checked": "已检查",
        "observed": "旁路观测",
        "used_previous_result": "使用上一轮结果",
        "routed": "已路由",
        "not_routed": "未路由",
        "allowed": "已允许",
        "missing": "缺失",
        "empty": "空结果",
        "queued": "已入队",
        "started": "处理中",
        "confirmation_card_started": "正在发送确认卡",
        "confirmation_card_sent": "确认卡已发送",
        "confirmation_card_failed": "确认卡发送失败",
        "success": "成功",
        "partial": "部分成功",
        "error": "失败",
        "failed": "失败",
        "denied": "无权限",
        "skipped": "已跳过",
        "not_executed": "未执行",
        "pending_confirmation": "待确认",
        "unknown": "未知",
        "cancelled": "已取消",
        "stale": "已失效",
        "stale_cleanup": "过期已清理",
        "provider_error": "Provider 异常",
        "permission_denied": "权限不足",
        "clarification": "等待补充信息",
    }.get(status, status)


def _runtime_stage_name_label(name: str) -> str:
    return {
        "result_followup_detector": "结果追问识别",
        "intent_recognition": "意图识别",
        "task_planner": "任务规划",
        "permission_check": "权限检查",
        "capability_router": "能力路由与执行",
        "answer_composer": "答案组织",
    }.get(name, name or "未知阶段")


def _execution_identity_label(identity: str) -> str:
    return {
        "bot": "机器人",
        "user": "用户",
    }.get(identity, identity or "未知")


def _question_type_label(question_type: str) -> str:
    return {
        "query": "查询",
        "analysis": "分析",
        "insight": "洞察",
        "decision": "决策",
        "action": "行动",
    }.get(question_type, question_type or "类型未知")


def _confirmation_reason_label(reason: str) -> str:
    return {
        "capability_requires_confirmation": "能力要求",
        "high_risk_action": "高风险动作",
        "action_question": "行动类问题",
    }.get(reason, reason or "未知")


def _result_context_kind_label(kind: str) -> str:
    return {
        "query_result": "查询结果",
        "action_receipt": "动作回执",
        "pending_confirmation": "待确认操作",
        "no_result": "空结果",
    }.get(kind, kind or "结果上下文")


def _result_context_followup_fields(result_context: dict) -> list[str]:
    context_kind = str(result_context.get("context_kind") or "")
    result_type = str(result_context.get("result_type") or "")
    if result_context.get("empty_result"):
        return ["空结果原因", "下一步建议"]
    if context_kind == "action_receipt":
        return ["状态", "对象", "链接", "编号", "错误原因", "权限原因", "摘要"]
    if result_type in {"approval_list", "approval_detail"}:
        return ["序号", "详情", "申请人", "金额", "建议", "通过/拒绝"]
    if result_type in {"people_search", "department_members", "organization_snapshot"}:
        return ["人员", "部门", "电话", "邮箱", "负责人"]
    if result_type in {"mail_list", "im_message_list", "chat_list"}:
        return ["序号", "主题", "发件人", "时间", "详情"]
    if result_type in {"task_list", "calendar_event_list"}:
        return ["序号", "标题", "时间", "负责人", "详情"]
    if result_type == "docs_read":
        return ["文档", "正文预览", "类型"]
    if result_type in {"wiki_space_list", "wiki_node_list"}:
        return ["序号", "名称", "token", "类型"]
    if result_type == "drive_file_list":
        return ["序号", "文件名", "token", "类型"]
    if result_type == "vc_meeting_list":
        return ["序号", "会议主题", "开始时间", "会议 ID"]
    if result_type == "attendance_record_list":
        return ["序号", "日期", "状态", "结果"]
    if result_type == "okr_objective_list":
        return ["序号", "目标", "得分", "关键结果"]
    if result_type == "slides_read":
        return ["标题", "页数", "版本", "文字预览"]
    if result_type == "whiteboard_read":
        return ["画板", "代码类型", "内容预览"]
    return ["序号", "展开", "详情"]


def _empty_reason_label(reason: str) -> str:
    return {
        "no_provider_executed": "没有 Provider 被执行",
        "all_providers_skipped": "全部 Provider 已跳过",
        "missing_params": "缺少必要参数",
        "low_confidence": "语义置信度不足",
        "clarification": "等待补充信息",
        "tool_not_installed": "原子能力未接入",
        "capability_not_installed": "能力已规划但原子能力未启用",
        "operation_not_installed": "Provider 操作未接入",
        "not_installed": "能力未安装",
        "unsupported_operation": "Provider 暂不支持该操作",
        "operation_not_supported": "Provider 暂不支持该操作",
        "not_supported": "Provider 暂不支持该能力",
        "tool_execution_failed": "飞书原子能力执行失败",
        "ambiguous_target": "目标不唯一",
        "provider_not_registered": "Provider 未注册",
        "provider_error": "Provider 执行失败",
        "permission_denied": "权限不足",
        "no_company_permission": "没有公司范围权限",
        "empty_items": "没有结构化条目",
        "missing_dependency_result": "缺少上一步结果",
        "missing_company": "缺少公司上下文",
    }.get(reason, reason or "未知")


def _result_context_event_label(action: str) -> str:
    return {
        "save": "保存",
        "clear": "清除",
    }.get(action, action or "未知")


def _result_context_clear_reason_label(reason: str) -> str:
    return {
        "query_without_result_context": "本轮查询没有生成结构化结果",
        "runtime_action_success": "动作成功后清除旧结果",
        "approval_action_success": "审批动作成功后清除旧审批列表",
        "approval_batch_success": "批量审批成功后清除旧审批列表",
    }.get(reason, reason)


def _problem_answer(execution: ExecutionResult) -> str:
    pending_answer = _pending_provider_answer(execution)
    if pending_answer:
        return pending_answer
    structured = [_structured_problem_answer(item) for item in execution.provider_results if item.status in {"error", "denied"}]
    structured = [item for item in structured if item]
    if structured:
        return "\n\n".join(structured)
    answers = [
        _human_readable_answer(item.answer)
        for item in execution.provider_results
        if item.status in {"error", "denied"} and _human_readable_answer(item.answer)
    ]
    if answers:
        return "\n\n".join(answers)
    errors = [_human_readable_error(item.error) for item in execution.provider_results if item.status in {"error", "denied"} and item.error]
    errors = [item for item in errors if item]
    if errors:
        return errors[0]
    return ""


def _pending_provider_answer(execution: ExecutionResult) -> str:
    pending = []
    for item in execution.provider_results:
        metadata = item.metadata if isinstance(item.metadata, dict) else {}
        if item.status == "error" and metadata.get("error_type") == "tool_not_installed":
            pending.append((_source_label(item.source), str(metadata.get("operation") or item.result_type or "")))
    if not pending:
        return ""
    if len(pending) == len([item for item in execution.provider_results if item.status == "error"]):
        names = "、".join(label for label, _ in pending)
        return f"这个问题已经进入 V5 规划链路，但以下知识来源还没接入执行能力：{names}。我没有回退旧系统，避免给你不可靠的答案。"
    names = "、".join(label for label, _ in pending)
    return f"部分知识来源还没接入执行能力：{names}。"


def _structured_problem_answer(item) -> str:
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    error_type = str(metadata.get("error_type") or "").strip()
    source = _source_label(str(item.source or ""))
    if error_type == "missing_params":
        missing = metadata.get("missing_params") if isinstance(metadata.get("missing_params"), list) else []
        if item.answer:
            return item.answer
        if missing:
            return "还缺少这些信息：" + "、".join(_missing_param_label(str(value)) for value in missing if value) + "。"
    if error_type == "tool_not_installed":
        operation = str(metadata.get("operation") or item.result_type or "").strip()
        next_step = str(metadata.get("recommended_next_step") or "").strip()
        text = f"{source}能力已经登记，但底层飞书原子能力还没有接上。"
        if operation:
            text += f"\n待接操作：{operation}"
        if next_step:
            text += f"\n下一步：{next_step}"
        return text
    if error_type == "unsupported_operation":
        operation = str(metadata.get("operation") or item.result_type or "").strip()
        available = metadata.get("available_operations") if isinstance(metadata.get("available_operations"), list) else []
        text = f"{source} Provider 已接入，但暂不支持这个操作。"
        if operation:
            text += f"\n当前操作：{operation}"
        if available:
            text += "\n已支持：" + "、".join(str(value) for value in available[:8])
        return text
    if error_type == "tool_execution_failed":
        tool_error = str(metadata.get("tool_error") or item.error or "").strip()
        if _looks_like_raw_payload(item.answer):
            answer = ""
        else:
            answer = str(item.answer or "").strip()
        if answer:
            return answer
        if tool_error:
            return f"{source}原子能力执行失败。建议先检查飞书授权、参数和接口权限。\n错误摘要：{_human_readable_error(tool_error)}"
        return f"{source}原子能力执行失败。建议先检查飞书授权、参数和接口权限。"
    if error_type == "ambiguous_target":
        return item.answer or "找到多个可能对象，请补充更明确的名称。"
    if error_type == "provider_not_registered":
        return f"{source} Provider 还没有注册到 V5 Runtime，当前不会回退旧系统。"
    if error_type == "missing_dependency_result":
        missing_source = _source_label(str(metadata.get("missing_source") or ""))
        return item.answer or f"{source}执行缺少上一步结果：{missing_source}。请先完成对应查询或重新发起完整任务。"
    if error_type == "missing_company":
        return "当前没有明确公司上下文，请先切换或绑定公司后再试。"
    return ""


def _missing_param_label(key: str) -> str:
    return {
        "approval_item": "审批单",
        "transfer_user_id": "转交人",
        "add_sign_user_ids": "加签人",
        "cc_user_ids": "抄送人",
        "node_ids": "退回节点",
        "target_base_or_create_file": "新建或写入的表格目标",
        "task_guid": "任务",
        "start": "开始时间",
        "end": "结束时间",
        "to": "收件人",
        "subject": "主题",
        "body": "正文",
        "target_type": "发送对象",
        "text": "消息内容",
        "chat_id": "会话",
    }.get(key, key)


def _looks_like_raw_payload(text: str) -> bool:
    stripped = str(text or "").strip()
    return stripped.startswith("{") or stripped.startswith("[") or "HTTP error" in stripped or "status_code" in stripped


def _human_readable_answer(text: str) -> str:
    answer = str(text or "").strip()
    if not answer or _looks_like_raw_payload(answer):
        return ""
    return answer


def _human_readable_error(text: str) -> str:
    error = str(text or "").strip()
    if not error:
        return ""
    if _looks_like_raw_payload(error):
        return "执行时遇到飞书接口或参数错误，原始诊断已记录到 V5 状态里。你可以稍后重试，或让我查看 V5状态。"
    return _compact_error(error)


def _result_context_items_answer(result_context) -> str:
    if result_context is None or not getattr(result_context, "items", ()):
        return ""
    lines = [
        f"{index}. {_format_item(item, result_type=result_context.result_type)}"
        for index, item in enumerate(result_context.items[:10], start=1)
    ]
    total = len(result_context.items)
    if total > len(lines):
        lines.append(f"还有 {total - len(lines)} 条，可继续回复「全部显示」。")
    return "\n".join(lines)


def _compact_error(text: str, *, limit: int = 160) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[:limit].rstrip() + "..."


def _source_label(source: str) -> str:
    return {
        "task": "任务",
        "calendar": "日程",
        "mail": "邮件",
        "message": "结果发送",
        "approval": "审批",
        "im": "飞书消息",
        "people": "通讯录",
        "base": "多维表格",
        "docs": "飞书文档",
        "wiki": "飞书知识库",
        "drive": "飞书云盘",
        "sheets": "飞书电子表格",
        "vc": "飞书会议",
        "minutes": "飞书妙记",
        "note": "飞书会议纪要",
        "markdown": "飞书 Markdown",
        "apps": "飞书妙搭应用",
        "openapi": "飞书原生接口",
        "attendance": "飞书考勤",
        "okr": "飞书 OKR",
        "slides": "飞书幻灯片",
        "whiteboard": "飞书画板",
        "vc_agent": "飞书会中能力",
        "company_profile": "公司档案",
        "knowledge": "企业知识库",
        "workevent": "工作事件",
        "memory": "长期记忆",
        "web": "网页搜索",
        "runtime": "运行时",
    }.get(source, source)


def _compose_followup(*, context: RuntimeContext, followup: ResultFollowup) -> ComposedAnswer:
    result_context = context.result_context
    if result_context is None or not result_context.items:
        return ComposedAnswer(answer="上一轮结果已经过期了，请重新查询一次。")

    if followup.followup_type == "receipt_detail":
        lines = [
            _format_followup_item_detail(
                item,
                result_type=result_context.result_type,
                fallback_index=index,
                receipt_mode=True,
            )
            for index, item in enumerate(result_context.items[:5], start=1)
        ]
        return ComposedAnswer(
            answer="\n".join(lines),
            result_context=result_context,
            metadata=_followup_metadata(result_context, followup),
        )

    if followup.followup_type == "position" or (followup.followup_type == "detail" and "index" in followup.entity_ref):
        index = int(followup.entity_ref.get("index", 0))
        if index >= len(result_context.items) or abs(index) > len(result_context.items):
            return ComposedAnswer(
                answer=f"上一轮结构化结果只有 {len(result_context.items)} 条，没有找到你说的第 {index + 1} 条。",
                result_context=result_context,
                metadata=_followup_metadata(result_context, followup),
            )
        item = result_context.items[index] if index >= 0 else result_context.items[-1]
        return ComposedAnswer(
            answer=_format_followup_item_detail(
                item,
                result_type=result_context.result_type,
                fallback_index=(len(result_context.items) if index < 0 else index + 1),
                receipt_mode=result_context.metadata.get("context_kind") == "action_receipt" if isinstance(result_context.metadata, dict) else False,
            ),
            result_context=result_context,
            metadata=_followup_metadata(result_context, followup),
        )

    if followup.followup_type in {"expand", "pronoun", "detail"}:
        lines = [
            f"{_item_display_index(item, fallback_index=index)}. {_format_item(item, result_type=result_context.result_type)}"
            for index, item in enumerate(result_context.items[:20], start=1)
        ]
        return ComposedAnswer(
            answer="\n".join(lines),
            result_context=result_context,
            metadata=_followup_metadata(result_context, followup),
        )

    return ComposedAnswer(answer="上一轮结果里没有可展开的结构化明细，请重新查询一次。")


def _format_followup_item_detail(
    item: dict,
    *,
    result_type: str,
    fallback_index: int,
    receipt_mode: bool = False,
) -> str:
    display_index = _item_display_index(item, fallback_index=fallback_index)
    if receipt_mode or result_type in {"runtime_action", "approval_action"} or str(item.get("status_group") or ""):
        detail = _format_action_receipt_detail(item, result_type=result_type)
    else:
        detail = _format_item(item, result_type=result_type)
    prefix = f"第{display_index}条"
    if "\n" in detail:
        return f"{prefix}：\n{detail}"
    return f"{prefix}：{detail}"


def _item_display_index(item: dict, *, fallback_index: int) -> int:
    for key in ("index", "_result_index"):
        try:
            value = int(item.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    return fallback_index


def _followup_metadata(result_context, followup: ResultFollowup) -> dict:
    metadata = result_context.metadata if isinstance(result_context.metadata, dict) else {}
    return {
        "followup_type": followup.followup_type,
        "result_type": result_context.result_type,
        "context_kind": metadata.get("context_kind") or "query_result",
        "item_count": len(result_context.items),
        "query_id": result_context.query_id,
        "actionable": bool(metadata.get("actionable", False)),
    }


def _clarification_text(intent: IntentResult) -> str:
    clarification_prompt = intent.entities.get("clarification_prompt") if isinstance(intent.entities, dict) else None
    if isinstance(clarification_prompt, str) and clarification_prompt.strip():
        return clarification_prompt.strip()
    if "approval_item" in intent.missing_params:
        if intent.intent == "approval_detail":
            return "你要看第几个审批的详情？可以回复「第一个详情」或「展开第2个」。"
        if intent.intent.startswith("approval_"):
            return "你要处理第几个审批？可以回复「通过第一个」「拒绝第2个」「催办第一个」。"
    if "transfer_user_id" in intent.missing_params:
        return "转交审批还需要接收人。可以回复「转交第一个给王云飞」。"
    if "add_sign_user_ids" in intent.missing_params:
        return "加签审批还需要加签人。可以回复「加签第一个给王云飞」。"
    if "cc_user_ids" in intent.missing_params:
        return "抄送审批还需要抄送人。可以回复「抄送第一个给王云飞」。"
    if "node_ids" in intent.missing_params:
        return "退回审批需要明确退回节点。请先展开详情查看可退回节点。"
    if "target_base_or_create_file" in intent.missing_params:
        return "你想让我新建一个多维表格文件，还是写入某个已有多维表格？如果是已有表，请提供 bascn-xxx。"
    if intent.intent == "mail_draft_create":
        labels = {"to": "收件邮箱", "subject": "主题", "body": "正文"}
        missing = [labels.get(key, key) for key in intent.missing_params]
        return f"创建邮件草稿还缺少：{', '.join(missing)}。我会先创建草稿，不会直接发送。"
    if intent.missing_params:
        return f"还缺少这些信息：{', '.join(intent.missing_params)}。"
    return "这个问题我还不够确定，你可以再补充一点范围或对象。"


def _format_item(item: dict, *, result_type: str = "") -> str:
    item = _public_item(item)
    company_suffix = _company_suffix(item)
    if result_type in {"approval_action", "runtime_action"}:
        return _append_suffix(_format_action_receipt_item(item, result_type=result_type), company_suffix)
    if result_type.startswith("approval"):
        return _append_suffix(_format_approval_item(item), company_suffix)
    if result_type in {"people_search", "department_members", "organization_snapshot"}:
        return _append_suffix(_format_people_item(item), company_suffix)
    if result_type == "task_list":
        return _append_suffix(_format_task_item(item), company_suffix)
    if result_type == "mail_list":
        return _append_suffix(_format_mail_item(item), company_suffix)
    if result_type == "calendar_event_list":
        return _append_suffix(_format_calendar_item(item), company_suffix)
    if result_type in {"base_export", "message_send", "mail_draft_create", "task_create", "task_complete", "calendar_create"}:
        return _append_suffix(_format_action_receipt_item(item, result_type=result_type), company_suffix)
    if result_type == "company_profile":
        return _append_suffix(_format_company_profile_item(item), company_suffix)
    if result_type in {"workevent_summary", "risk_event_list"}:
        return _append_suffix(_format_workevent_item(item), company_suffix)
    if result_type == "memory_fact_list":
        return _append_suffix(_format_memory_item(item), company_suffix)
    if result_type in {"knowledge_list", "risk_policy_list"}:
        return _append_suffix(_format_knowledge_item(item), company_suffix)
    if result_type == "web_search_list":
        return _append_suffix(_format_web_item(item), company_suffix)
    if result_type == "docs_read":
        return _append_suffix(_format_docs_item(item), company_suffix)
    if result_type in {"wiki_space_list", "wiki_node_list"}:
        return _append_suffix(_format_wiki_item(item), company_suffix)
    if result_type == "drive_file_list":
        return _append_suffix(_format_drive_item(item), company_suffix)
    if result_type == "vc_meeting_list":
        return _append_suffix(_format_vc_meeting_item(item), company_suffix)
    if result_type == "attendance_record_list":
        return _append_suffix(_format_attendance_item(item), company_suffix)
    if result_type == "okr_objective_list":
        return _append_suffix(_format_okr_objective_item(item), company_suffix)
    if result_type == "slides_read":
        return _append_suffix(_format_slides_item(item), company_suffix)
    if result_type == "whiteboard_read":
        return _append_suffix(_format_whiteboard_item(item), company_suffix)
    title = str(item.get("title") or item.get("subject") or item.get("name") or item.get("summary") or item)
    details = []
    for key in ("company_name", "department", "applicant", "owner", "status", "amount", "from", "received_at"):
        value = item.get(key)
        if value not in (None, ""):
            details.append(f"{key}: {value}")
    if details:
        return _append_suffix(f"{title} | " + " | ".join(details), company_suffix)
    return _append_suffix(title, company_suffix)


def _format_docs_item(item: dict) -> str:
    doc_id = str(item.get("document_id") or "文档").strip()
    doc_type = str(item.get("document_type") or "").strip()
    preview = str(item.get("content_preview") or "").strip()
    parts = [doc_id]
    if doc_type:
        parts.append(f"类型：{doc_type}")
    if preview:
        parts.append(f"预览：{preview[:160]}")
    return "｜".join(parts)


def _format_wiki_item(item: dict) -> str:
    title = str(item.get("title") or item.get("name") or "未命名").strip()
    token = str(item.get("node_token") or item.get("space_id") or item.get("obj_token") or "").strip()
    obj_type = str(item.get("obj_type") or "").strip()
    parts = [title]
    if obj_type:
        parts.append(f"类型：{obj_type}")
    if token:
        parts.append(f"token：{token}")
    return "｜".join(parts)


def _format_drive_item(item: dict) -> str:
    name = str(item.get("name") or "未命名文件").strip()
    token = str(item.get("token") or "").strip()
    file_type = str(item.get("type") or "").strip()
    parts = [name]
    if file_type:
        parts.append(f"类型：{file_type}")
    if token:
        parts.append(f"token：{token}")
    return "｜".join(parts)


def _format_vc_meeting_item(item: dict) -> str:
    title = str(item.get("topic") or item.get("title") or item.get("meeting_id") or "未命名会议").strip()
    parts = [title]
    if item.get("start_time"):
        parts.append(f"开始：{item.get('start_time')}")
    if item.get("meeting_id"):
        parts.append(f"ID：{item.get('meeting_id')}")
    return "｜".join(parts)


def _format_attendance_item(item: dict) -> str:
    date_value = str(item.get("date") or "未标明日期").strip()
    parts = [date_value]
    if item.get("status"):
        parts.append(f"状态：{item.get('status')}")
    if item.get("result"):
        parts.append(str(item.get("result"))[:120])
    return "｜".join(parts)


def _format_okr_objective_item(item: dict) -> str:
    content = str(item.get("content") or item.get("objective_id") or "未命名目标").strip()
    parts = [content]
    if item.get("score") not in (None, ""):
        parts.append(f"得分：{item.get('score')}")
    if item.get("key_result_count") not in (None, ""):
        parts.append(f"KR：{item.get('key_result_count')} 个")
    return "｜".join(parts)


def _format_slides_item(item: dict) -> str:
    title = str(item.get("title") or item.get("xml_presentation_id") or "未命名幻灯片").strip()
    parts = [title]
    if item.get("slide_count") not in (None, ""):
        parts.append(f"页数：{item.get('slide_count')}")
    if item.get("revision_id"):
        parts.append(f"版本：{item.get('revision_id')}")
    preview = str(item.get("content_preview") or "").strip()
    if preview:
        parts.append(f"预览：{preview[:120]}")
    return "｜".join(parts)


def _format_whiteboard_item(item: dict) -> str:
    token = str(item.get("whiteboard_token") or "飞书画板").strip()
    parts = [token]
    if item.get("code_type"):
        parts.append(f"类型：{item.get('code_type')}")
    preview = str(item.get("content_preview") or "").strip()
    if preview:
        parts.append(f"预览：{preview[:120]}")
    return "｜".join(parts)


def _public_item(item: dict) -> dict:
    return {key: value for key, value in item.items() if not str(key).startswith("_")}


def _format_action_receipt_item(item: dict, *, result_type: str) -> str:
    if result_type == "base_export":
        title = str(item.get("title") or item.get("table_name") or "多维表格")
        parts = [title]
        row_count = item.get("row_count")
        if row_count not in (None, ""):
            parts.append(f"写入：{row_count} 行")
        url = str(item.get("url") or "").strip()
        if url:
            parts.append(f"链接：{url}")
        return "｜".join(parts)
    if result_type == "message_send":
        target = str(item.get("target") or "未知对象")
        text = str(item.get("text") or "").strip()
        return f"消息已发送｜对象：{target}" + (f"｜内容：{text[:120]}" if text else "")
    if result_type.startswith("approval_") and result_type not in {"approval_list", "approval_detail", "approval_initiated_list"}:
        action_label = str(item.get("action_label") or _action_label(str(item.get("operation") or "")) or "处理")
        title = str(item.get("title") or "审批单")
        status = _status_label(str(item.get("status") or ""))
        applicant = str(item.get("applicant") or "").strip()
        amount = str(item.get("amount") or "").strip()
        parts = [f"审批{action_label}", title, status]
        if applicant:
            parts.append(f"申请人：{applicant}")
        if amount:
            parts.append(f"金额：{amount}元")
        return "｜".join(part for part in parts if part)
    if result_type == "mail_draft_create":
        subject = str(item.get("subject") or "邮件草稿")
        to = str(item.get("to") or "").strip()
        url = str(item.get("url") or "").strip()
        parts = [subject]
        if to:
            parts.append(f"收件人：{to}")
        if url:
            parts.append(f"链接：{url}")
        return "｜".join(parts)
    if result_type == "calendar_create":
        title = str(item.get("title") or "日程")
        start = str(item.get("start") or "").strip()
        end = str(item.get("end") or "").strip()
        parts = [title]
        if start:
            parts.append(f"开始：{start}")
        if end:
            parts.append(f"结束：{end}")
        url = str(item.get("url") or "").strip()
        event_id = str(item.get("event_id") or "").strip()
        if url:
            parts.append(f"链接：{url}")
        elif event_id:
            parts.append(f"日程ID：{event_id}")
        return "｜".join(parts)
    if result_type in {"task_create", "task_complete"}:
        title = str(item.get("title") or item.get("summary") or "任务")
        parts = [title]
        url = str(item.get("url") or "").strip()
        task_id = str(item.get("task_id") or item.get("id") or "").strip()
        if url:
            parts.append(f"链接：{url}")
        elif task_id:
            parts.append(f"任务ID：{task_id}")
        return "｜".join(parts)
    if result_type == "runtime_action":
        title = str(item.get("title") or "动作")
        status = _status_label(str(item.get("status") or ""))
        summary = str(item.get("summary") or "").strip()
        parts = [title, status]
        if summary and summary != title:
            parts.append(summary[:160])
        return "｜".join(parts)
    return str(item.get("title") or item.get("summary") or "动作完成")


def _format_action_receipt_detail(item: dict, *, result_type: str) -> str:
    item = _public_item(item)
    title = str(item.get("title") or item.get("summary") or "动作结果").strip()
    operation = _action_label(str(item.get("operation") or ""))
    raw_status = str(item.get("status") or "")
    status = _receipt_status_label(raw_status)
    lines = [title]
    if operation or status:
        lines.append("结果：" + "".join(part for part in (operation, status) if part))
    if result_type.startswith("approval_") and result_type not in {"approval_list", "approval_detail", "approval_initiated_list"}:
        applicant = str(item.get("applicant") or "").strip()
        amount = str(item.get("amount") or "").strip()
        serial_number = str(item.get("serial_number") or "").strip()
        instance_code = str(item.get("instance_code") or "").strip()
        if applicant:
            lines.append(f"申请人：{applicant}")
        if amount:
            lines.append(f"金额：{amount} 元")
        if serial_number:
            lines.append(f"单号：{serial_number}")
        if instance_code:
            lines.append(f"实例编号：{instance_code}")
    target = str(item.get("target") or item.get("to") or item.get("chat_name") or item.get("applicant") or "").strip()
    if target:
        lines.append(f"对象：{target}")
    url = str(item.get("url") or item.get("link") or "").strip()
    app_token = str(item.get("app_token") or "").strip()
    table_id = str(item.get("table_id") or "").strip()
    task_id = str(item.get("task_id") or item.get("event_id") or item.get("message_id") or "").strip()
    if url:
        lines.append(f"链接：{url}")
    if app_token:
        lines.append(f"app_token：{app_token}")
    if table_id:
        lines.append(f"table_id：{table_id}")
    if task_id:
        lines.append(f"编号：{task_id}")
    open_hint = str(item.get("open_hint") or "").strip()
    if open_hint:
        lines.append(f"打开提示：{open_hint}")
    row_count = item.get("row_count")
    count = item.get("count")
    if row_count not in (None, ""):
        lines.append(f"写入行数：{row_count}")
    elif count not in (None, ""):
        lines.append(f"数量：{count}")
    error = str(item.get("error") or "").strip()
    if error:
        lines.append(f"错误：{_compact_error(error, limit=180)}")
    reason = str(item.get("reason") or "").strip()
    if reason:
        lines.append(f"原因：{_empty_reason_label(reason)}")
    summary = str(item.get("summary") or "").strip()
    if summary and summary != title:
        lines.append(f"摘要：{summary[:240]}")
    if len(lines) == 1:
        lines[0] = _format_action_receipt_item(item, result_type=result_type)
    return "\n".join(lines)


def _receipt_status_label(status: str) -> str:
    value = str(status or "").strip()
    return {
        "success": "已完成",
        "partial": "部分完成",
        "error": "失败",
        "failed": "失败",
        "cancelled": "已取消",
        "stale": "已失效",
        "expired": "已过期",
        "queued": "处理中",
        "started": "处理中",
        "processing": "处理中",
        "pending": "处理中",
        "denied": "无权限",
        "skipped": "未执行",
    }.get(value, _status_label(value))


def _company_suffix(item: dict) -> str:
    company_id = str(item.get("company_id") or "").strip()
    if not company_id:
        return ""
    return f"公司：{company_id}"


def _append_suffix(text: str, suffix: str) -> str:
    return f"{text}｜{suffix}" if suffix else text


def _format_approval_item(item: dict) -> str:
    title = str(item.get("title") or "未命名审批")
    parts = [title]
    applicant = str(item.get("applicant") or "").strip()
    amount = item.get("amount")
    status = str(item.get("status") or "").strip()
    if applicant:
        parts.append(f"申请人：{applicant}")
    if amount not in (None, ""):
        parts.append(f"金额：{amount}元" if isinstance(amount, (int, float)) else f"金额：{amount}")
    if status:
        parts.append(f"状态：{status}")
    assessment = item.get("assessment") if isinstance(item.get("assessment"), dict) else {}
    suggestion = str(assessment.get("suggestion") or "").strip()
    reason = str(assessment.get("reason") or "").strip()
    if suggestion:
        parts.append(f"建议：{suggestion}")
    if reason:
        parts.append(f"理由：{reason}")
    return "｜".join(parts)


def _format_people_item(item: dict) -> str:
    name = str(item.get("name") or "未知人员")
    parts = [name]
    for label, key in (("职位", "title"), ("部门", "department"), ("邮箱", "email"), ("手机", "mobile")):
        value = str(item.get(key) or "").strip()
        if value:
            parts.append(f"{label}：{value}")
    return "｜".join(parts)


def _format_task_item(item: dict) -> str:
    title = str(item.get("title") or "未命名任务")
    parts = [title]
    status = str(item.get("status") or "").strip()
    due = str(item.get("due") or "").strip()
    if status:
        parts.append(f"状态：{status}")
    if due:
        parts.append(f"截止：{due}")
    return "｜".join(parts)


def _format_mail_item(item: dict) -> str:
    subject = str(item.get("subject") or item.get("title") or "无主题")
    parts = [subject]
    sender = str(item.get("from") or item.get("sender") or "").strip()
    received_at = str(item.get("received_at") or item.get("date") or "").strip()
    if sender:
        parts.append(f"发件人：{sender}")
    if received_at:
        parts.append(f"时间：{received_at}")
    return "｜".join(parts)


def _format_calendar_item(item: dict) -> str:
    title = str(item.get("title") or "未命名日程")
    parts = [title]
    start = str(item.get("start") or "").strip()
    end = str(item.get("end") or "").strip()
    if start or end:
        parts.append(f"时间：{start} - {end}".strip())
    return "｜".join(parts)


def _format_company_profile_item(item: dict) -> str:
    name = str(item.get("name") or "当前公司")
    status = str(item.get("status") or "").strip()
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    intro = str(metadata.get("intro") or metadata.get("description") or metadata.get("summary") or "").strip()
    parts = [name]
    if status:
        parts.append(f"状态：{status}")
    if intro:
        parts.append(f"简介：{intro}")
    return "｜".join(parts)


def _format_workevent_item(item: dict) -> str:
    title = str(item.get("title") or "未命名事件")
    parts = [title]
    event_type = str(item.get("event_type") or "").strip()
    occurred_at = str(item.get("occurred_at") or "").strip()
    summary = str(item.get("summary") or "").strip()
    if event_type:
        parts.append(f"类型：{event_type}")
    if occurred_at:
        parts.append(f"时间：{occurred_at}")
    if summary:
        parts.append(f"摘要：{summary}")
    return "｜".join(parts)


def _format_memory_item(item: dict) -> str:
    subject = str(item.get("subject") or "未命名记忆")
    content = str(item.get("content") or "").strip()
    scope = str(item.get("scope") or "").strip()
    parts = [subject]
    if content:
        parts.append(content)
    if scope:
        parts.append(f"范围：{scope}")
    return "｜".join(parts)


def _format_knowledge_item(item: dict) -> str:
    title = str(item.get("title") or "未命名知识")
    summary = str(item.get("summary") or "").strip()
    kind = "知识事实" if item.get("kind") == "fact" else "文档事件" if item.get("kind") == "event" else "知识"
    parts = [kind, title]
    if summary:
        parts.append(summary)
    return "｜".join(parts)


def _format_web_item(item: dict) -> str:
    title = str(item.get("title") or "未命名网页")
    summary = str(item.get("summary") or "").strip()
    url = str(item.get("url") or "").strip()
    parts = ["网页资料", title]
    if summary:
        parts.append(summary)
    if url:
        parts.append(url)
    return "｜".join(parts)
