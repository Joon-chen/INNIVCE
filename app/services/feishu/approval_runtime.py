from collections.abc import Awaitable, Callable
from typing import Any


async def approval_items_from_context_or_live(
    db: Any,
    app_config: Any,
    identity: Any,
    *,
    chat_id: str | None,
    load_context: Callable[[Any, Any, str | None], list[dict[str, Any]]],
    fetch_pending_tasks: Callable[..., Awaitable[dict[str, Any]]],
    register_resources: Callable[[Any, Any, list[dict[str, Any]]], None],
    attach_synced_attachments: Callable[[Any, Any, list[dict[str, Any]]], None],
    ensure_attachment_summaries: Callable[[Any, list[dict[str, Any]]], Awaitable[None]],
    attach_history_context: Callable[[Any, Any, list[dict[str, Any]]], None],
    attach_llm_advice: Callable[[list[dict[str, Any]]], Awaitable[None]],
    store_context: Callable[[Any, Any, str | None, list[dict[str, Any]]], None],
) -> list[dict[str, Any]]:
    items = load_context(app_config, identity, chat_id)
    if items:
        return items
    result = await fetch_pending_tasks(db, app_config, identity, limit=8)
    if result.get("available") and result.get("items"):
        live_items = result["items"]
        register_resources(db, app_config, live_items)
        attach_synced_attachments(db, app_config, live_items)
        await ensure_attachment_summaries(app_config, live_items)
        attach_history_context(db, app_config, live_items)
        await attach_llm_advice(live_items)
        store_context(app_config, identity, chat_id, live_items)
        return live_items
    return []


async def recent_approvals_reply(
    db: Any,
    app_config: Any,
    identity: Any,
    *,
    chat_id: str | None,
    limit: int,
    fetch_pending_tasks: Callable[..., Awaitable[dict[str, Any]]],
    register_resources: Callable[[Any, Any, list[dict[str, Any]]], None],
    attach_synced_attachments: Callable[[Any, Any, list[dict[str, Any]]], None],
    ensure_attachment_summaries: Callable[[Any, list[dict[str, Any]]], Awaitable[None]],
    attach_history_context: Callable[[Any, Any, list[dict[str, Any]]], None],
    attach_llm_advice: Callable[[list[dict[str, Any]]], Awaitable[None]],
    store_context: Callable[[Any, Any, str | None, list[dict[str, Any]]], None],
    format_pending_tasks: Callable[[list[dict[str, Any]]], list[str]],
    approval_events_by_status: Callable[..., list[Any]],
    completed_statuses: set[str],
    pending_statuses: set[str],
    format_event_lines: Callable[[list[Any]], list[str]],
    recent_approval_events: Callable[..., list[Any]],
) -> str:
    pending_tasks = await fetch_pending_tasks(db, app_config, identity, limit=limit)
    if pending_tasks["available"]:
        items = pending_tasks["items"]
        register_resources(db, app_config, items)
        attach_synced_attachments(db, app_config, items)
        await ensure_attachment_summaries(app_config, items)
        attach_history_context(db, app_config, items)
        await attach_llm_advice(items)
        store_context(app_config, identity, chat_id, items)
        lines = format_pending_tasks(items)
        completed_events = approval_events_by_status(db, app_config, statuses=completed_statuses, limit=3)
        if completed_events:
            lines.append("")
            lines.append("已完成审批我不会再混到“待我审批”里。最近完成的审批记录：")
            lines.extend(format_event_lines(completed_events))
        return "\n".join(lines)

    pending_events = approval_events_by_status(db, app_config, statuses=pending_statuses, limit=limit)
    if pending_events:
        lines = [
            "我还没能直接查到飞书分配给你的审批任务，只能先按本地审批实例状态兜底。",
            "下面这些是“未完成审批实例”，不一定都是待你本人处理：",
            *format_event_lines(pending_events),
            "",
            f"接口提示：{pending_tasks.get('error') or '审批任务接口暂不可用'}",
        ]
        return "\n".join(lines)

    events = recent_approval_events(db, app_config, limit=limit)
    if not events:
        return "我这边还没有审批实例数据。你可以先说“待我审批”或“同步审批”；系统会自动发现审批资源并同步。"

    lines = [
        "我没有查到待你审批的任务。下面只是最近审批历史，已完成和未完成不会再当成“待我审批”：",
        *format_event_lines(events),
    ]
    if pending_tasks.get("error"):
        lines.append(f"\n审批任务接口提示：{pending_tasks['error']}")
    return "\n".join(lines)


async def approval_detail_reply(
    db: Any,
    app_config: Any,
    identity: Any,
    *,
    chat_id: str | None,
    selector_text: str,
    load_items: Callable[..., Awaitable[list[dict[str, Any]]]],
    select_item: Callable[[list[dict[str, Any]], str], dict[str, Any] | None],
    attachment_refs: Callable[[Any], list[dict[str, str]]],
    attachment_results: Callable[[dict[str, Any]], list[Any]],
    format_detail_lines: Callable[..., list[str]],
) -> str:
    items = await load_items(db, app_config, identity, chat_id=chat_id)
    if not items:
        return "我现在没有待审批上下文。你先问“帮我查下待我审批的单子”，我会先拿到审批任务。"
    selected = select_item(items, selector_text)
    if selected is None:
        if len(items) == 1:
            selected = items[0]
        else:
            return "你当前有多笔待审批。请回复“展开第N条”，我再给你看那一笔的关键字段和附件。"
    results: list[Any] = []
    form = (selected.get("instance_detail") or {}).get("form")
    if attachment_refs(form):
        results = attachment_results(selected)
    return "\n".join(format_detail_lines(selected, attachment_results=results))


async def approval_advice_reply(
    db: Any,
    app_config: Any,
    identity: Any,
    *,
    chat_id: str | None,
    load_items: Callable[..., Awaitable[list[dict[str, Any]]]],
    form_fields: Callable[[Any], list[tuple[str, str]]],
    amount_value: Callable[[dict[str, str]], float | None],
    first_matching: Callable[[dict[str, str], list[str]], str | None],
    applicant_name: Callable[[dict[str, Any]], str | None],
    approval_name: Callable[[dict[str, Any]], str],
    attachment_refs: Callable[[Any], list[dict[str, str]]],
    attachment_results: Callable[[dict[str, Any]], list[Any]],
    decision_for_item: Callable[..., tuple[str, str]],
    attachment_basis: Callable[[list[Any]], str],
) -> str:
    items = await load_items(db, app_config, identity, chat_id=chat_id)
    if not items:
        return "我现在没有可分析的待审批上下文。你先问我“帮我查下待我审批的单子”，我会记住当前这一笔。"
    if len(items) > 1:
        return "我看到不止一笔待审批。为了避免误判，请先告诉我你要分析哪一笔，比如回复单号或金额。"

    item = items[0]
    form = (item.get("instance_detail") or {}).get("form")
    fields = dict(form_fields(form))
    amount = amount_value(fields)
    reason = first_matching(fields, ["事由", "原因", "用途", "说明"])
    project = first_matching(fields, ["项目名称", "项目编码", "项目"])
    applicant = applicant_name(item)
    title = approval_name(item)
    attachments = attachment_refs(form)
    results = attachment_results(item)
    conclusion, decision_reason = decision_for_item(
        item,
        title,
        fields,
        amount=amount,
        attachments=attachments,
        attachment_results=results,
    )
    lines = [f"我基于上一笔待审批看了一下：{title}。"]
    if applicant:
        lines.append(f"- 申请人：{applicant}")
    if amount is not None:
        lines.append(f"- 金额：{amount:g}")
    if reason:
        lines.append(f"- 事由：{reason}")
    if project:
        lines.append(f"- 项目：{project}")
    basis = attachment_basis(results)
    if basis:
        lines.append(f"- {basis.rstrip('；')}")
    lines.append(f"建议：{conclusion}。理由：{decision_reason}")
    if conclusion in {"可通过", "谨慎通过"}:
        lines.append("如果你确认采用这个建议，可以让我准备通过；我会先让你二次确认。")
    else:
        lines.append("如果你要继续处理，可以先展开附件或让我准备拒绝/退回；我会先让你二次确认。")
    lines.append("可继续回复：帮我审批通过 / 帮我拒绝。真正提交前我会再问你确认。")
    return "\n".join(lines)
