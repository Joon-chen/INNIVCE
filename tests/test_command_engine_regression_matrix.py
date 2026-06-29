from uuid import uuid4

import pytest

from app.services.runtime_v5.command_layer import build_command_plan
from app.services.runtime_v5.models import ResultContext, RuntimeContext, RuntimeIdentity, RuntimeScope


def _context(message: str, *, result_context: ResultContext | None = None) -> RuntimeContext:
    company_id = uuid4()
    return RuntimeContext(
        identity=RuntimeIdentity(open_id="ou_test", role="owner", domains=("all",)),
        runtime_scope=RuntimeScope(company_ids=(company_id,), active_company_id=company_id),
        current_message=message,
        session_context={},
        result_context=result_context,
        chat_id="chat_command_engine_matrix",
    )


PEOPLE_RESULT = ResultContext(
    result_type="organization_snapshot",
    count=47,
    items=({"name": "张三"},),
    metadata={"entity_domain": "People", "result_context_presentation": "summary"},
    answer="47人。",
)

MALE_RESULT = ResultContext(
    result_type="organization_snapshot",
    count=24,
    items=({"name": "王云飞", "gender_normalized": "male", "gender_source": "source"},),
    metadata={
        "entity_domain": "People",
        "people_filter": {"filter": "gender", "value": "male"},
        "field_projection": "name_only",
        "result_context_presentation": "summary",
    },
    answer="目前能确认的男性员工是 24 位。",
)

TASK_RESULT = ResultContext(
    result_type="task_list",
    count=2,
    items=({"title": "出差西安", "status": "todo"}, {"title": "测试会", "status": "done"}),
    metadata={"context_kind": "query_result"},
    answer="你有 2 条任务。",
)


@pytest.mark.parametrize(
    ("message", "result_context", "intent", "strategy", "sources", "domain"),
    (
        ("马云是谁", PEOPLE_RESULT, "smalltalk", "smalltalk", (), "Conversation"),
        ("宋朝开国皇帝是谁", PEOPLE_RESULT, "smalltalk", "smalltalk", (), "Conversation"),
        ("我的审批", TASK_RESULT, "approval_query", "approval_query", ("approval",), "Process"),
        ("我的任务", PEOPLE_RESULT, "task_query", "task_query", ("task",), "Workspace"),
        ("我的日程", TASK_RESULT, "calendar_query", "calendar_query", ("calendar",), "Workspace"),
        ("我的会议", TASK_RESULT, "calendar_query", "calendar_query", ("calendar",), "Workspace"),
    ),
)
def test_command_engine_new_questions_do_not_inherit_wrong_business_domain(
    message: str,
    result_context: ResultContext,
    intent: str,
    strategy: str,
    sources: tuple[str, ...],
    domain: str,
) -> None:
    plan = build_command_plan(context=_context(message, result_context=result_context))

    assert plan.intent == intent
    assert plan.planner_result.strategy == strategy
    assert plan.planner_result.sources == sources
    assert plan.command_frame.domain == domain
    assert plan.intent_result.question_type == "query"


@pytest.mark.parametrize(
    ("message", "list_delivery"),
    (
        ("你帮我把明细直接发出来", "sidepanel"),
        ("不要在侧边栏，就在对话框显示。", "inline_text"),
    ),
)
def test_command_engine_previous_people_result_presentation_stays_read_only(message: str, list_delivery: str) -> None:
    plan = build_command_plan(context=_context(message, result_context=MALE_RESULT))

    assert plan.intent == "organization_snapshot"
    assert plan.intent_result.question_type == "query"
    assert plan.planner_result.sources == ("people",)
    assert plan.command_frame.dialogue_mode == "present"
    assert plan.command_frame.context_mode == "inherit_result_context"
    assert plan.command_frame.action_type == "read"
    assert plan.command_frame.params["domain_query"]["operation_kind"] == "read"
    assert plan.command_frame.params["domain_query"]["filters"] == {"gender": "male"}
    assert plan.command_frame.params["output_contract"]["surface"] == ("text" if list_delivery == "inline_text" else "sidepanel")
    assert plan.command_frame.params["output_contract"]["list_delivery"] == list_delivery
