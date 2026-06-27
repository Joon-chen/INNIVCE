import re
import runpy
from pathlib import Path


PUBLIC_ENTRYPOINTS = (
    Path("app/api"),
    Path("app/tasks"),
    Path("app/workers"),
)

RETIRED_OPERATIONS_ROUTE_MODULES = (
    "operations_read_routes.py",
    "operations_bot_routes.py",
    "operations_bot_user_routes.py",
    "operations_memory_routes.py",
    "operations_memory_fact_routes.py",
    "operations_resource_routes.py",
    "operations_status_routes.py",
)

RETIRED_FEISHU_API_READ_ROUTE_MODULES = (
    "feishu_admin_api_read_drive_routes.py",
    "feishu_admin_api_read_im_routes.py",
    "feishu_admin_api_read_knowledge_routes.py",
    "feishu_admin_api_read_mail_folder_routes.py",
    "feishu_admin_api_read_mail_message_routes.py",
    "feishu_admin_api_read_meeting_routes.py",
    "feishu_admin_api_read_wiki_routes.py",
)



def test_public_entrypoints_use_v5_feishu_boundary() -> None:
    offenders: list[str] = []
    for root in PUBLIC_ENTRYPOINTS:
        for path in root.rglob("*.py"):
            text = path.read_text()
            if "app.services.feishu.client" in text:
                offenders.append(str(path))

    assert offenders == []


def test_feishu_implementations_live_under_v5_boundary() -> None:
    legacy_files = list(Path("app/services/integrations").glob("feishu*.py"))
    assert legacy_files == []

    offenders: list[str] = []
    for path in Path("app/services/feishu").rglob("*.py"):
        text = path.read_text()
        if "app.services.integrations.feishu" in text:
            offenders.append(str(path))

    assert offenders == []


def test_agent_runtime_only_reaches_feishu_through_tool_router() -> None:
    offenders: list[str] = []
    forbidden_tokens = (
        "lark-cli",
        "subprocess",
        "app.services.feishu.client",
        "app.services.feishu.api_runtime",
        "app.services.tools.providers.feishu_api",
        "app.services.tools.providers.feishu_mcp",
    )
    for path in Path("app/services/agent").rglob("*.py"):
        text = path.read_text()
        offenders.extend(f"{path}:{token}" for token in forbidden_tokens if token in text)

    runtime_text = Path("app/services/agent/runtime.py").read_text()

    assert offenders == []
    assert "from app.services.tools.router import TOOL_REGISTRY, execute_agent_tool" in runtime_text
    assert "execute_agent_tool(" in runtime_text


def test_feishu_bot_entrypoint_reaches_agent_runtime_only_through_dispatcher() -> None:
    command_text = Path("app/services/feishu/commands.py").read_text()
    handler_text = Path("app/services/feishu/command_handlers.py").read_text()
    dispatcher_text = Path("app/services/feishu/command_dispatcher.py").read_text()
    bot_runtime_text = Path("app/services/feishu/bot_runtime.py").read_text()

    assert "build_feishu_gateway_message(payload)" in command_text
    assert "command_dispatcher.dispatch_command_reply(" in command_text
    assert "command_handlers.default_command_dispatch_handlers()" in command_text
    assert "employee_bot_answer" not in command_text
    assert "answer_agent_message" not in command_text
    assert "app.services.agent.runtime" not in command_text
    assert "bot_runtime.employee_bot_answer" in handler_text
    assert "bot_runtime.employee_bot_answer_result" in handler_text
    assert "handlers.employee_bot_answer(" in dispatcher_text
    assert "from app.services.runtime_v5.runtime import run_runtime_v5" in bot_runtime_text


def test_v5_runtime_provider_import_does_not_load_feishu_bot_entrypoint() -> None:
    feishu_init_text = Path("app/services/feishu/__init__.py").read_text()
    provider_text = Path("app/services/runtime_v5/feishu_resource_providers.py").read_text()
    bot_runtime_text = Path("app/services/feishu/bot_runtime.py").read_text()

    assert "from app.services.feishu.commands import handle_feishu_command" not in feishu_init_text
    assert "def __getattr__(name: str):" in feishu_init_text
    assert "from app.services.feishu import approval_formatters" in provider_text
    assert "from app.services.runtime_v5.feishu_resource_providers import build_feishu_provider_registry" in bot_runtime_text


def test_v5_portal_actions_enter_runtime_instead_of_tools_or_providers() -> None:
    portal_text = Path("app/api/routes/portal.py").read_text()
    portal_service_text = Path("app/services/portal_runtime.py").read_text()

    forbidden_tokens = (
        "submit_approval_action_payload",
        "ProviderRequest(",
        "IntentResult(",
        "PlannerResult(",
        ".execute(\n        ProviderRequest",
        "execute_agent_tool(",
        "ToolRequest(",
        "ToolContext(",
    )

    assert [token for token in forbidden_tokens if token in portal_text] == []
    assert [token for token in forbidden_tokens if token in portal_service_text] == []
    assert "runtime_v5_pending_action" not in portal_service_text
    assert "runtime_v5_action_request" not in portal_service_text
    assert "runtime_v5_action_input" in portal_service_text
    assert "run_runtime_v5(" in portal_service_text
    assert "build_feishu_provider_registry(" in portal_service_text
    assert "_portal_runtime_context(" in portal_service_text
    assert "_approval_action_result_context(" in portal_service_text
    assert "_approval_action_result_context(data, company_id=str(app_config.company_id))" in portal_service_text
    assert '"company_id": ""' not in portal_service_text


def test_v5_runtime_interaction_layer_is_result_only() -> None:
    interaction_text = Path("app/services/runtime_v5/interaction_layer.py").read_text()

    forbidden_tokens = (
        "execute_agent_tool",
        "ToolRequest",
        "ToolContext",
        "build_feishu_provider_registry",
        "run_runtime_v5",
        "get_db",
        "Session",
        "FeishuClient",
        "subprocess",
    )

    assert [token for token in forbidden_tokens if token in interaction_text] == []
    assert "def interaction_payload_from_runtime_result(result: RuntimeResult)" in interaction_text
    assert "target_ui" in interaction_text
    assert "payload_type=_payload_type_for_result(result)" in interaction_text
    assert "actions=result.actions" in interaction_text
    assert '"target_ui": result.target_ui' in interaction_text
    assert '"result_type": result.result_type' in interaction_text


def test_v5_runtime_result_builder_contract_is_frozen() -> None:
    runtime_result_text = Path("app/services/runtime_v5/runtime_result.py").read_text()

    forbidden_tokens = (
        "execute_agent_tool",
        "ToolRequest",
        "ToolContext",
        "build_feishu_provider_registry",
        "run_runtime_v5",
        "get_db",
        "Session",
        "FeishuClient",
        "subprocess",
    )

    assert [token for token in forbidden_tokens if token in runtime_result_text] == []
    assert "def build_runtime_result(" in runtime_result_text
    assert "RuntimeResult(" in runtime_result_text
    assert '"company_id": company_id' in runtime_result_text
    assert '"runtime_action", "runtime_pending_confirmation", "runtime_waiting_input"' in runtime_result_text
    assert 'result_type == "approval_detail"' in runtime_result_text
    assert 'result_type in {"approval_list", "approval_query"}' in runtime_result_text
    assert 'result_type == "runtime_pending_confirmation"' in runtime_result_text


def test_v5_runtime_result_payload_serialization_stays_in_builder_module() -> None:
    runtime_text = Path("app/services/runtime_v5/runtime.py").read_text()
    runtime_result_text = Path("app/services/runtime_v5/runtime_result.py").read_text()

    assert "def runtime_result_payload(result: RuntimeResult)" in runtime_result_text
    assert '"actions": list(result.actions)' in runtime_result_text
    assert '"target_ui": result.target_ui' in runtime_result_text
    assert '"item_count": len(result.items)' in runtime_result_text
    assert "runtime_result_payload(runtime_result)" in runtime_text
    assert '"actions": list(runtime_result.actions)' not in runtime_text
    assert '"target_ui": runtime_result.target_ui' not in runtime_text


def test_v5_interaction_payload_consumer_boundary_is_render_only() -> None:
    portal_text = Path("app/services/portal_runtime.py").read_text()
    card_text = Path("app/services/feishu/confirmation_card_entrypoint.py").read_text()
    portal_pending_text = portal_text.split("def portal_pending_approvals_payload(", 1)[1].split(
        "\n\ndef portal_cached_approvals_payload(",
        1,
    )[0]
    portal_cached_text = portal_text.split("def portal_cached_approvals_payload(", 1)[1].split(
        "\n\ndef portal_bootstrap_payload(",
        1,
    )[0]
    portal_action_text = portal_text.split("def portal_approval_action_payload(", 1)[1].split(
        "\n\ndef _require_portal_session(",
        1,
    )[0]
    card_detail_text = card_text.split("async def _handle_runtime_approval_detail_action(", 1)[1].split(
        "\n\nasync def _handle_runtime_approval_workbench(",
        1,
    )[0]

    assert "interaction_payload_from_runtime_result" in portal_text
    assert "runtime_result_from_payload(" in portal_text
    assert "interaction_payload_payload(interaction_payload)" in portal_pending_text
    assert "provider_results" not in portal_pending_text
    assert "result.error" not in portal_pending_text
    assert "envelope.execution.status" not in portal_pending_text
    assert '"items": list(interaction_payload.items)' in portal_pending_text
    assert "cached_result_context_runtime_result_payload(result_context)" in portal_cached_text
    assert "IntentResult(" not in portal_cached_text
    assert "PlannerResult(" not in portal_cached_text
    assert "interaction_payload_payload(interaction_payload)" in portal_cached_text
    assert '"items": list(interaction_payload.items)' in portal_cached_text
    assert '"items": list(result_context.items)' not in portal_cached_text
    assert '"answer": result_context.answer' not in portal_cached_text
    assert '"metadata": result_context.metadata' not in portal_cached_text
    assert "interaction_payload_payload(interaction_payload)" in portal_action_text
    assert "provider_results" not in portal_action_text
    assert "result.error" not in portal_action_text
    assert "envelope.execution.status" not in portal_action_text
    assert "interaction_payload.status" in portal_action_text
    assert "interaction_payload.summary" in portal_action_text
    assert '"interaction_payload":' in portal_action_text

    forbidden_card_tokens = (
        "_execute_single_approval_action(",
        "_execute_approval_provider(",
        "ProviderRequest(",
        "ToolRequest(",
        "execute_agent_tool(",
        "build_feishu_provider_registry(",
        "result.status",
    )
    assert [token for token in forbidden_card_tokens if token in card_detail_text] == []
    assert "_save_single_approval_runtime_action_input(" in card_detail_text
    assert "_enqueue_runtime_card_reply(" in card_detail_text


def test_v5_bot_trace_payload_is_outside_feishu_bot_runtime() -> None:
    bot_runtime_text = Path("app/services/feishu/bot_runtime.py").read_text()
    bot_trace_text = Path("app/services/runtime_v5/bot_trace.py").read_text()

    assert "runtime_v5_bot_trace_payload(" in bot_runtime_text
    assert "def runtime_v5_bot_trace_payload(" in bot_trace_text
    assert "def runtime_contract_payload(" in bot_trace_text
    assert "def _runtime_contract_payload(" not in bot_runtime_text
    assert '"runtime_trace_summary": runtime_summary' not in bot_runtime_text
    assert '"runtime_trace_summary": runtime_summary' in bot_trace_text


def test_v5_bot_diagnostics_snapshot_base_is_outside_feishu_bot_runtime() -> None:
    bot_runtime_text = Path("app/services/feishu/bot_runtime.py").read_text()
    bot_diagnostics_text = Path("app/services/runtime_v5/bot_diagnostics.py").read_text()

    assert "runtime_v5_diagnostics_snapshot_base(" in bot_runtime_text
    assert "def runtime_v5_diagnostics_snapshot_base(" in bot_diagnostics_text
    assert '"diagnostics_version": runtime_summary.get("diagnostics_version")' not in bot_runtime_text
    assert '"diagnostics_version": runtime_summary.get("diagnostics_version")' in bot_diagnostics_text
    assert "from app.services.feishu" not in bot_diagnostics_text


def test_v5_runtime_plan_and_result_models_keep_frozen_contracts() -> None:
    models_text = Path("app/services/runtime_v5/models.py").read_text()
    command_text = Path("app/services/runtime_v5/command_layer.py").read_text()
    runtime_result_text = Path("app/services/runtime_v5/runtime_result.py").read_text()

    for field in ("intent:", "steps:", "target_ui:", "tool_candidates:", "context_scope:"):
        assert field in models_text
    for field in ("result_type:", "status:", "title:", "summary:", "items:", "actions:", "target_ui:", "metadata:"):
        assert field in models_text
    assert '"company_id": str(context.runtime_scope.active_company_id or "")' in command_text
    assert "build_runtime_result(" in runtime_result_text


def test_v5_runtime_state_model_is_session_backed_without_workflow_engine() -> None:
    models_text = Path("app/services/runtime_v5/models.py").read_text()
    runtime_state_text = Path("app/services/runtime_v5/runtime_state.py").read_text()

    assert "class RuntimeTaskState" in models_text
    assert "class RuntimeActionState" in models_text
    assert '"waiting_input"' in models_text
    assert "def save_waiting_input_state(" in runtime_state_text
    assert "def mark_runtime_action_waiting_confirmation(" in runtime_state_text
    assert "save_session_context" in runtime_state_text
    assert '"company_id": company_id' in runtime_state_text
    assert "def _company_id_from_pending_action(" in runtime_state_text
    assert "def _restore_pending_action_payload(" in runtime_state_text
    assert "def _company_id_from_restored_pending_action(" in runtime_state_text
    assert "payload[\"company_id\"] = _company_id_from_restored_pending_action(" in runtime_state_text
    assert "RuntimeStateTransitionInput" not in models_text
    assert "RuntimeStateTransitionInput" not in runtime_state_text
    forbidden_tokens = (
        "Session",
        "sqlalchemy",
        "celery",
        "workflow",
        "Temporal",
        "DAG",
    )
    assert [token for token in forbidden_tokens if token in runtime_state_text] == []


def test_v5_runtime_company_id_context_spine_is_frozen() -> None:
    models_text = Path("app/services/runtime_v5/models.py").read_text()
    policy_text = Path("app/services/runtime_v5/policy_layer.py").read_text()
    action_input_text = Path("app/services/runtime_v5/runtime_action_input.py").read_text()
    pending_action_text = Path("app/services/runtime_v5/runtime_pending_action.py").read_text()
    runtime_state_text = Path("app/services/runtime_v5/runtime_state.py").read_text()
    runtime_result_text = Path("app/services/runtime_v5/runtime_result.py").read_text()
    interaction_text = Path("app/services/runtime_v5/interaction_layer.py").read_text()

    assert "active_company_id: UUID | None = None" in models_text
    assert "reason=\"missing_company_id\"" in policy_text
    assert 'raise ValueError("RuntimeActionInput requires context.company_id")' in action_input_text
    assert 'raise ValueError("RuntimePendingAction requires company_id")' in pending_action_text
    assert '"company_id": company_id' in runtime_state_text
    assert "payload[\"company_id\"] = _company_id_from_restored_pending_action(" in runtime_state_text
    assert '"company_id": company_id' in runtime_result_text
    assert "**result.metadata" in interaction_text

    out_of_scope_tokens = (
        "Memory",
        "WorkEvent",
        "ProviderSnapshot",
        "Diagnostics",
    )
    combined_spine_text = "\n".join(
        (
            action_input_text,
            pending_action_text,
            runtime_state_text,
            runtime_result_text,
            interaction_text,
        )
    )
    assert [token for token in out_of_scope_tokens if token in combined_spine_text] == []


def test_v5_missing_params_helpers_are_outside_runtime_main_loop() -> None:
    runtime_text = Path("app/services/runtime_v5/runtime.py").read_text()
    helper_text = Path("app/services/runtime_v5/runtime_missing_params.py").read_text()

    assert "from app.services.runtime_v5.runtime_missing_params import" in runtime_text
    assert 'MissingParamType = Literal["TEXT", "USER"]' in helper_text
    assert "class MissingParamResolver" in helper_text
    assert "class MissingParamContract" in helper_text
    assert 'TEXT: MissingParamType = "TEXT"' in helper_text
    assert 'USER: MissingParamType = "USER"' in helper_text
    assert '"comment": MissingParamContract(' in helper_text
    assert "param_type=TEXT" in helper_text
    assert '"target_user": MissingParamContract(' in helper_text
    assert "param_type=USER" in helper_text
    assert "def pending_action_with_user_input(" in helper_text
    assert "def waiting_input_still_missing(" in helper_text
    assert "def resolve_missing_param_value(" in helper_text
    assert "def _pending_action_with_user_input(" not in runtime_text
    assert "def _waiting_input_still_missing(" not in runtime_text
    assert "def _comment_from_user_input(" not in runtime_text
    forbidden_tokens = (
        "Session",
        "sqlalchemy",
        "execute_runtime_task",
        "run_runtime_v5",
        "ProviderRequest",
        "contact",
        "open_id",
        "workflow",
        "Temporal",
        "DAG",
    )
    assert [token for token in forbidden_tokens if token in helper_text] == []


def test_v5_runtime_action_input_contract_is_frozen() -> None:
    models_text = Path("app/services/runtime_v5/models.py").read_text()
    action_input_text = Path("app/services/runtime_v5/runtime_action_input.py").read_text()

    assert "class RuntimeActionInput" in models_text
    assert 'RuntimeActionType = Literal["approve", "reject", "transfer", "add_sign", "confirm", "cancel", "open_detail", "execute"]' in models_text
    for field in (
        "action_id:",
        "action_type:",
        "intent:",
        "strategy:",
        "target:",
        "confirmation:",
        "context:",
    ):
        assert field in models_text
    assert "class RuntimeActionConfirmation" in models_text
    assert "class RuntimeActionContext" in models_text
    assert 'RUNTIME_ACTION_INPUT_KEY = "runtime_v5_action_input"' in action_input_text
    assert 'LEGACY_ACTION_REQUEST_KEY = "runtime_v5_action_request"' in action_input_text
    assert "def build_runtime_action_input(" in action_input_text
    assert "def build_runtime_action_input_payload(" in action_input_text
    assert 'raise ValueError("RuntimeActionInput requires context.company_id")' in action_input_text
    assert "runtime_action_input_from_session(" in action_input_text


def test_v5_runtime_pending_action_contract_is_frozen() -> None:
    models_text = Path("app/services/runtime_v5/models.py").read_text()
    runtime_text = Path("app/services/runtime_v5/runtime.py").read_text()
    pending_action_text = Path("app/services/runtime_v5/runtime_pending_action.py").read_text()

    assert "class RuntimePendingAction" in models_text
    for field in (
        "action_id:",
        "message:",
        "intent:",
        "strategy:",
        "sources:",
        "company_id:",
        "confirmation_token:",
        "runtime_action_input:",
    ):
        assert field in models_text
    assert "def pending_action_from_runtime_action_input(" in pending_action_text
    assert "def runtime_pending_action_payload(" in pending_action_text
    assert "def runtime_pending_action_with_missing_input(" in pending_action_text
    assert 'raise ValueError("RuntimePendingAction requires company_id")' in pending_action_text
    assert "runtime_pending_action_payload(pending_action_from_runtime_action_input(" in runtime_text
    assert '"runtime_action_input": runtime_action_input_payload(action_input)' not in runtime_text


def test_v5_approval_card_confirmation_adapts_to_runtime_action_input() -> None:
    card_text = Path("app/services/feishu/confirmation_card_entrypoint.py").read_text()

    assert "def _save_runtime_confirmation_action_input(" in card_text
    assert "def _save_single_approval_runtime_action_input(" in card_text
    assert '"runtime_v5_action_input"' in card_text
    assert 'source_ui="card"' in card_text
    assert "build_runtime_action_input_payload(" in card_text
    assert "_save_runtime_confirmation_action_input(" in card_text
    assert "_save_single_approval_runtime_action_input(" in card_text
    assert "runtime_v5_action_request" not in card_text


def test_v5_runtime_action_input_producers_use_builder() -> None:
    card_text = Path("app/services/feishu/confirmation_card_entrypoint.py").read_text()
    portal_text = Path("app/services/portal_runtime.py").read_text()
    card_confirmation_producer = card_text.split("def _save_runtime_confirmation_action_input(", 1)[1].split(
        "\n\ndef _save_single_approval_runtime_action_input(",
        1,
    )[0]
    card_single_producer = card_text.split("def _save_single_approval_runtime_action_input(", 1)[1].split(
        "\n\ndef _pending_action_trace_fields(",
        1,
    )[0]
    portal_action_producer = portal_text.split("def portal_approval_action_payload(", 1)[1].split(
        "\n\ndef _require_portal_session(",
        1,
    )[0]

    for producer_text in (card_confirmation_producer, card_single_producer, portal_action_producer):
        assert "build_runtime_action_input_payload(" in producer_text
        assert '"runtime_v5_action_input": {' not in producer_text
        assert 'session_context["runtime_v5_action_input"] = {' not in producer_text
        assert "company_id=str(app_config.company_id)" in producer_text


def test_v5_sidepanel_approval_actions_are_contained_by_portal_runtime() -> None:
    sidepanel_text = Path("app/static/portal/sidepanel.html").read_text()
    portal_app_text = Path("app/static/portal/app.js").read_text()
    portal_route_text = Path("app/api/routes/portal_approval_action_routes.py").read_text()
    portal_service_text = Path("app/services/portal_runtime.py").read_text()

    assert 'class="sidepanel-mode"' in sidepanel_text
    assert 'submitAction("approve")' in portal_app_text
    assert 'submitAction("reject")' in portal_app_text
    assert 'postJson("/api/portal/approvals/action", payload)' in portal_app_text
    assert "portal_approval_action_payload(db, data, x_admin_token)" in portal_route_text
    assert "build_runtime_action_input_payload(" in portal_service_text
    assert "run_runtime_v5(" in portal_service_text
    assert 'source_ui="portal"' in portal_service_text

    forbidden_frontend_tokens = (
        "ProviderRequest",
        "execute_agent_tool",
        "submit_approval_action_payload",
        "/api/admin/apps/",
    )
    forbidden_service_tokens = (
        "submit_approval_action_payload",
        "ProviderRequest(",
        "execute_agent_tool(",
        "ToolRequest(",
        "ToolContext(",
    )
    assert [token for token in forbidden_frontend_tokens if token in portal_app_text] == []
    assert [token for token in forbidden_service_tokens if token in portal_route_text] == []
    assert [token for token in forbidden_service_tokens if token in portal_service_text] == []


def test_v5_legacy_runtime_action_request_is_contained() -> None:
    runtime_text = Path("app/services/runtime_v5/runtime.py").read_text()
    action_input_text = Path("app/services/runtime_v5/runtime_action_input.py").read_text()
    portal_text = Path("app/services/portal_runtime.py").read_text()
    card_text = Path("app/services/feishu/confirmation_card_entrypoint.py").read_text()

    assert 'LEGACY_ACTION_REQUEST_KEY = "runtime_v5_action_request"' in action_input_text
    assert "runtime_action_input_from_legacy_request(" in action_input_text
    assert '"legacy_runtime_v5_action_request": True' in action_input_text
    assert "legacy_runtime_action_request_unsupported" in runtime_text
    assert "legacy_action_request_not_allowed" in runtime_text
    assert "runtime_v5_action_request" not in portal_text
    assert "runtime_v5_action_request" not in card_text


def test_v5_approval_single_confirmation_card_does_not_execute_tool_directly() -> None:
    card_text = Path("app/services/feishu/confirmation_card_entrypoint.py").read_text()
    handler_text = card_text.split("async def _handle_runtime_approval_single_confirm(", 1)[1].split(
        "\n\nasync def _toggle_batch_selection(",
        1,
    )[0]

    assert "_execute_single_approval_action(" not in handler_text
    assert "_execute_approval_provider(" not in handler_text
    assert "ProviderRequest(" not in handler_text
    assert "build_feishu_provider_registry(" not in handler_text
    assert "_save_single_approval_runtime_action_input(" in handler_text
    assert "_enqueue_runtime_card_reply(" in handler_text


def test_v5_approval_direct_execution_helpers_are_retired() -> None:
    card_text = Path("app/services/feishu/confirmation_card_entrypoint.py").read_text()
    single_handler_text = card_text.split("async def _handle_runtime_approval_single_confirm(", 1)[1].split(
        "\n\nasync def _toggle_batch_selection(",
        1,
    )[0]
    detail_handler_text = card_text.split("async def _handle_runtime_approval_detail_action(", 1)[1].split(
        "\n\nasync def _handle_runtime_approval_workbench(",
        1,
    )[0]
    batch_handler_text = card_text.split("async def _handle_runtime_approval_batch_confirm(", 1)[1].split(
        "\n\nasync def _handle_runtime_approval_single_confirm(",
        1,
    )[0]
    detail_query_text = card_text.split("async def _send_single_approval_detail(", 1)[1].split(
        "\n\nasync def _send_single_approval_confirmation(",
        1,
    )[0]

    assert "_execute_single_approval_action(" not in card_text
    assert "_execute_approval_provider(" not in card_text
    assert "ProviderRequest(" not in card_text
    assert "IntentResult(" not in card_text
    assert "PlannerResult(" not in card_text
    assert "_execute_approval_provider(" not in detail_query_text
    assert "run_runtime_v5(" in detail_query_text
    assert "build_feishu_provider_registry(" in detail_query_text
    assert "_execute_approval_provider(" not in single_handler_text
    assert "_execute_approval_provider(" not in detail_handler_text
    assert "_execute_approval_provider(" not in batch_handler_text
    assert "_enqueue_batch_approve(" in batch_handler_text


def test_v5_batch_approval_legacy_island_is_explicitly_contained() -> None:
    card_text = Path("app/services/feishu/confirmation_card_entrypoint.py").read_text()
    celery_text = Path("app/tasks/celery_app.py").read_text()
    renderer_text = Path("app/services/gateway/card_renderer.py").read_text()
    portal_text = Path("app/services/portal_runtime.py").read_text()
    batch_handler_text = card_text.split("async def _handle_runtime_approval_batch_confirm(", 1)[1].split(
        "\n\nasync def _handle_runtime_approval_single_confirm(",
        1,
    )[0]
    batch_task_text = celery_text.split("def bot_approvals_batch_approve_task(", 1)[1].split(
        "\n@celery_app.task(",
        1,
    )[0]

    assert '"action": "batch_approve"' in renderer_text
    assert '"action": "batch_approve_group"' in renderer_text
    assert "_prepare_batch_approve_confirmation(" in card_text
    assert "_enqueue_batch_approve(" in batch_handler_text
    assert '"bot.approvals.batch_approve"' in card_text
    assert "ProviderRequest(" in batch_task_text
    assert "provider.execute(" in batch_task_text
    assert "run_runtime_v5(" not in batch_task_text
    assert "build_runtime_action_input_payload(" not in batch_task_text
    assert "runtime_v5_action_input" not in batch_task_text
    assert "runtime_v5_pending_approval_batch" in batch_task_text

    assert "ProviderRequest(" not in card_text
    assert "ProviderRequest(" not in portal_text
    assert "runtime_v5_pending_approval_batch" not in portal_text


def test_v5_approval_action_boundary_matrix_is_frozen() -> None:
    card_text = Path("app/services/feishu/confirmation_card_entrypoint.py").read_text()
    portal_text = Path("app/services/portal_runtime.py").read_text()
    sidepanel_text = Path("app/static/portal/app.js").read_text()
    runtime_text = Path("app/services/runtime_v5/runtime.py").read_text()
    celery_text = Path("app/tasks/celery_app.py").read_text()
    admin_write_route_text = Path("app/api/routes/feishu_admin_write_approval_routes.py").read_text()

    card_detail_action_text = card_text.split("async def _handle_runtime_approval_detail_action(", 1)[1].split(
        "\n\nasync def _handle_runtime_approval_workbench(",
        1,
    )[0]
    batch_task_text = celery_text.split("def bot_approvals_batch_approve_task(", 1)[1].split(
        "\n@celery_app.task(",
        1,
    )[0]

    assert 'action not in {"approve", "request_reject", "request_add_sign", "request_transfer", "dismiss"}' in card_detail_action_text
    assert "_save_single_approval_runtime_action_input(" in card_detail_action_text
    assert "build_runtime_action_input_payload(" in portal_text
    assert 'submitAction("approve")' in sidepanel_text
    assert 'submitAction("reject")' in sidepanel_text

    transfer_branch = card_detail_action_text.split('if action == "request_transfer":', 1)[1].split('if action == "request_add_sign":', 1)[0]
    add_sign_branch = card_detail_action_text.split('if action == "request_add_sign":', 1)[1].split('return True, "missing_add_sign"', 1)[0]
    assert 'missing_params=("target_user",)' in transfer_branch
    assert 'missing_params=("target_user",)' in add_sign_branch
    assert "guarded_pending_user_resolution" in runtime_text
    assert '"approval_transfer", "approval_add_sign"' in runtime_text

    assert "runtime_v5_pending_approval_batch" in batch_task_text
    assert "ProviderRequest(" in batch_task_text
    assert "provider.execute(" in batch_task_text
    assert "build_runtime_action_input_payload(" not in batch_task_text
    assert "runtime_v5_pending_approval_batch" not in portal_text
    assert "runtime_v5_pending_approval_batch" not in sidepanel_text

    assert "submit_approval_action_payload(db, app_config, data)" in admin_write_route_text
    assert "run_runtime_v5(" not in admin_write_route_text
    assert "build_runtime_action_input_payload(" not in admin_write_route_text


def test_v5_approval_runtime_sample_entry_boundaries_are_frozen() -> None:
    card_text = Path("app/services/feishu/confirmation_card_entrypoint.py").read_text()
    portal_text = Path("app/services/portal_runtime.py").read_text()
    runtime_text = Path("app/services/runtime_v5/runtime.py").read_text()
    card_single_text = card_text.split("async def _handle_runtime_approval_single_confirm(", 1)[1].split(
        "\n\nasync def _toggle_batch_selection(",
        1,
    )[0]
    card_detail_action_text = card_text.split("async def _handle_runtime_approval_detail_action(", 1)[1].split(
        "\n\nasync def _handle_runtime_approval_workbench(",
        1,
    )[0]
    card_detail_query_text = card_text.split("async def _send_single_approval_detail(", 1)[1].split(
        "\n\nasync def _send_single_approval_confirmation(",
        1,
    )[0]

    forbidden_entry_tokens = (
        "ProviderRequest(",
        "ToolRequest(",
        "execute_agent_tool(",
        "_execute_single_approval_action(",
        "_execute_approval_provider(",
    )
    assert [token for token in forbidden_entry_tokens if token in portal_text] == []
    assert [token for token in forbidden_entry_tokens if token in card_single_text] == []
    assert [token for token in forbidden_entry_tokens if token in card_detail_action_text] == []
    assert [token for token in forbidden_entry_tokens if token in card_detail_query_text] == []
    assert "_save_single_approval_runtime_action_input(" in card_single_text
    assert "_save_single_approval_runtime_action_input(" in card_detail_action_text
    assert "run_runtime_v5(" in card_detail_query_text
    assert "runtime_v5_action_input" in portal_text
    assert "interaction_payload_payload(interaction_payload)" in portal_text
    assert 'result_type="runtime_waiting_input"' in runtime_text
    assert 'result_type="runtime_pending_confirmation"' in runtime_text
    assert 'result_type="runtime_action"' in runtime_text
    assert 'action == "request_transfer"' in card_detail_action_text
    assert 'action == "request_add_sign"' in card_detail_action_text
    assert "_enqueue_batch_approve(" in card_text


def test_v5_approval_detail_approve_card_enters_runtime_action_input() -> None:
    card_text = Path("app/services/feishu/confirmation_card_entrypoint.py").read_text()
    handler_text = card_text.split("async def _handle_runtime_approval_detail_action(", 1)[1].split(
        "\n\nasync def _handle_runtime_approval_workbench(",
        1,
    )[0]

    assert "_execute_single_approval_action(" not in handler_text
    assert "_save_single_approval_runtime_action_input(" in handler_text
    assert 'entrypoint="runtime_approval_detail_action"' in handler_text
    assert "_enqueue_runtime_card_reply(" in handler_text
    assert 'command="确认执行"' in handler_text


def test_v5_approval_detail_reject_missing_params_enters_runtime_action_input() -> None:
    card_text = Path("app/services/feishu/confirmation_card_entrypoint.py").read_text()
    handler_text = card_text.split("async def _handle_runtime_approval_detail_action(", 1)[1].split(
        "\n\nasync def _handle_runtime_approval_workbench(",
        1,
    )[0]

    assert 'action == "request_reject"' in handler_text
    assert "_save_single_approval_runtime_action_input(" in handler_text
    assert 'confirmed=False' in handler_text
    assert 'missing_params=("comment",)' in handler_text
    assert 'command="拒绝这个审批"' in handler_text
    assert 'action == "request_transfer"' in handler_text
    assert 'return True, "missing_transfer"' in handler_text
    assert 'action == "request_add_sign"' in handler_text
    assert 'return True, "missing_add_sign"' in handler_text
    transfer_branch = handler_text.split('if action == "request_transfer":', 1)[1].split('if action == "request_add_sign":', 1)[0]
    add_sign_branch = handler_text.split('if action == "request_add_sign":', 1)[1].split('return True, "missing_add_sign"', 1)[0]
    assert "_save_single_approval_runtime_action_input(" in transfer_branch
    assert "_save_single_approval_runtime_action_input(" in add_sign_branch
    assert 'action="transfer"' in transfer_branch
    assert 'action="add_sign"' in add_sign_branch
    assert 'missing_params=("target_user",)' in transfer_branch
    assert 'missing_params=("target_user",)' in add_sign_branch
    assert "_execute_approval_provider(" not in transfer_branch
    assert "_execute_approval_provider(" not in add_sign_branch


def test_v5_runtime_action_input_cancel_initializes_runtime_state() -> None:
    runtime_text = Path("app/services/runtime_v5/runtime.py").read_text()
    action_request_text = runtime_text.split("def _context_from_action_request(", 1)[1].split(
        "\n\ndef _pending_action_from_action_input(",
        1,
    )[0]

    assert 'action_input.action_type == "cancel"' in action_request_text
    assert "save_waiting_confirmation_state(" in action_request_text
    assert 'current_message="取消"' in action_request_text


def test_v5_waiting_input_result_contract_is_frozen() -> None:
    runtime_text = Path("app/services/runtime_v5/runtime.py").read_text()
    result_text = runtime_text.split("def _waiting_input_result_context(", 1)[1].split(
        "\n\ndef _waiting_input_prompt(",
        1,
    )[0]

    assert 'result_type="runtime_waiting_input"' in result_text
    assert '"actionable": True' in result_text
    assert '"missing_params": missing_params' in result_text
    assert '"input_contract": {' in result_text
    assert '"status": "waiting_input"' in result_text
    assert '"next_state": "waiting_confirmation"' in result_text


def test_v5_transfer_add_sign_confirmation_execution_guard_is_frozen() -> None:
    runtime_text = Path("app/services/runtime_v5/runtime.py").read_text()
    confirmation_block = runtime_text.split("if pending_action and _is_confirmation_message(context.current_message):", 1)[1].split(
        "\n\n    followup = detect_result_followup",
        1,
    )[0]
    guard_text = runtime_text.split("def _confirmation_execution_guard_reason(", 1)[1].split(
        "\n\ndef _context_from_action_request(",
        1,
    )[0]

    assert "_confirmation_execution_guard_reason(pending_action)" in confirmation_block
    assert "mark_runtime_action_done(" in confirmation_block
    assert "success=False" in confirmation_block
    assert "guarded_pending_user_resolution" in guard_text
    assert '"approval_transfer", "approval_add_sign"' in guard_text
    assert "execute_runtime_task(" not in confirmation_block


def test_feishu_realtime_cli_execution_is_hidden_behind_mcp_provider() -> None:
    feishu_api_text = Path("app/services/tools/providers/feishu_api.py").read_text()
    feishu_mcp_text = Path("app/services/tools/providers/feishu_mcp.py").read_text()
    lark_cli_text = Path("app/services/tools/providers/lark_cli.py").read_text()

    assert "import subprocess" not in feishu_api_text
    assert "subprocess.run" not in feishu_api_text
    assert "execute_feishu_mcp_realtime_tool" not in feishu_api_text
    assert "app.services.tools.providers.feishu_mcp" not in feishu_api_text
    assert 'import_module("app.services.feishu.api_runtime")' in feishu_api_text
    assert "execute_feishu_api_write_tool" not in feishu_api_text
    assert "Feishu API provider is not allowed to execute realtime writes" in feishu_api_text
    assert "import subprocess" not in feishu_mcp_text
    assert "subprocess.run" not in feishu_mcp_text
    assert "run_lark_cli_json as _run_lark_cli_json" in feishu_mcp_text
    assert "import subprocess" in lark_cli_text
    assert "subprocess.run" in lark_cli_text
    assert "execute_feishu_api_write_tool" not in feishu_mcp_text
    assert "app.services.feishu.api_runtime" not in feishu_mcp_text
    assert "app.services.feishu.client" not in feishu_mcp_text
    assert ".api_post(" not in feishu_mcp_text
    assert ".api_put(" not in feishu_mcp_text
    assert ".api_patch(" not in feishu_mcp_text
    assert ".api_delete(" not in feishu_mcp_text
    assert "requires a CLI executor behind MCP" in feishu_mcp_text


def test_v5_feishu_realtime_execution_respects_router_mcp_cli_api_roles() -> None:
    allowed_subprocess_files = {
        Path("app/services/tools/providers/devops.py"),
        Path("app/services/tools/providers/lark_cli.py"),
        Path("app/services/feishu_cli_user_auth.py"),
    }
    subprocess_offenders: list[str] = []
    api_runtime_offenders: list[str] = []
    mcp_realtime_call_offenders: list[str] = []

    for path in Path("app").rglob("*.py"):
        text = path.read_text()
        if ("import subprocess" in text or "subprocess.run" in text) and path not in allowed_subprocess_files:
            subprocess_offenders.append(str(path))
        if "app.services.feishu.api_runtime" in text and path != Path("app/services/tools/providers/feishu_api.py"):
            api_runtime_offenders.append(str(path))
        if "execute_feishu_mcp_realtime_tool(" in text and path not in {
            Path("app/services/tools/providers/feishu_mcp.py"),
        }:
            mcp_realtime_call_offenders.append(str(path))

    router_text = Path("app/services/tools/router.py").read_text()
    feishu_api_text = Path("app/services/tools/providers/feishu_api.py").read_text()
    feishu_mcp_text = Path("app/services/tools/providers/feishu_mcp.py").read_text()
    lark_cli_text = Path("app/services/tools/providers/lark_cli.py").read_text()

    assert subprocess_offenders == []
    assert api_runtime_offenders == []
    assert mcp_realtime_call_offenders == []
    assert "return execute_feishu_api_tool(context, request)" not in router_text
    assert "Feishu API provider is reserved for Sync Engine data synchronization" in router_text
    assert "return execute_feishu_mcp_tool(context, request)" in router_text
    assert 'import_module("app.services.feishu.api_runtime")' in feishu_api_text
    assert "execute_feishu_api_write_tool" not in feishu_api_text
    assert "Feishu API provider is not allowed to execute realtime writes" in feishu_api_text
    assert "execute_feishu_mcp_realtime_tool" not in feishu_api_text
    assert "execute_feishu_mcp_realtime_tool(context, request)" in feishu_mcp_text
    assert "subprocess.run(args" in lark_cli_text
    assert 'args = ["lark-cli"' in feishu_mcp_text
    assert "subprocess.run" not in feishu_mcp_text
    assert "app.services.feishu.api_runtime" not in feishu_mcp_text
    assert 'role="tool_scheduling"' in feishu_mcp_text
    assert "performs_action_execution=False" in feishu_mcp_text
    assert 'role="action_execution"' in lark_cli_text
    assert 'allowed_caller_module="app.services.tools.providers.feishu_mcp"' in lark_cli_text
    assert "performs_action_execution=True" in lark_cli_text
    assert "Feishu CLI action execution must be scheduled by the Feishu MCP provider" in lark_cli_text


def test_v5_feishu_contracts_do_not_mix_router_mcp_cli_api_roles() -> None:
    router_text = Path("app/services/tools/router.py").read_text()
    api_provider_text = Path("app/services/tools/providers/feishu_api.py").read_text()
    mcp_provider_text = Path("app/services/tools/providers/feishu_mcp.py").read_text()
    cli_text = Path("app/services/tools/providers/lark_cli.py").read_text()

    assert '"tool_router": "business_capability"' in router_text
    assert 'role="sync_engine_data_sync"' in api_provider_text
    assert "realtime_read_allowed=False" in api_provider_text
    assert "realtime_write_allowed=False" in api_provider_text
    assert "mcp_access_allowed=False" in api_provider_text
    assert "agent_runtime_direct_access=False" in api_provider_text
    assert 'role="tool_scheduling"' in mcp_provider_text
    assert 'action_executor=LARK_CLI_EXECUTION_CONTRACT.engine_name' in mcp_provider_text
    assert "performs_action_execution=False" in mcp_provider_text
    assert 'role="action_execution"' in cli_text
    assert 'allowed_caller_module="app.services.tools.providers.feishu_mcp"' in cli_text
    assert "accepts_business_capability=False" in cli_text
    assert "schedules_tools=False" in cli_text
    assert "performs_action_execution=True" in cli_text


def test_feishu_api_tool_config_migration_targets_all_realtime_tools() -> None:
    migration = runpy.run_path("alembic/versions/0017_tool_configs_feishu_mcp_defaults.py")
    capability_module = runpy.run_path("app/services/tools/providers/feishu_api.py")

    assert migration["down_revision"] == "0016_account_type_taxonomy"
    assert "provider = 'feishu_mcp'" in Path("alembic/versions/0017_tool_configs_feishu_mcp_defaults.py").read_text()
    assert set(migration["FEISHU_REALTIME_TOOL_NAMES"]) == set(capability_module["FEISHU_API_CAPABILITIES"])


def test_resource_legacy_feishu_fk_is_detached_before_legacy_table_retirement() -> None:
    migration = runpy.run_path("alembic/versions/0018_detach_resource_from_legacy_feishu_table.py")
    migration_text = Path("alembic/versions/0018_detach_resource_from_legacy_feishu_table.py").read_text()

    assert migration["down_revision"] == "0017_tool_configs_feishu_mcp_defaults"
    assert 'foreign_key.get("constrained_columns") == ["legacy_feishu_resource_id"]' in migration_text
    assert 'op.drop_constraint(constraint_name, "resources", type_="foreignkey")' in migration_text
    assert "op.drop_column" not in migration_text
    assert 'op.drop_table("feishu_resources")' not in migration_text


def test_legacy_feishu_table_drop_migration_requires_zero_unmapped_rows() -> None:
    migration = runpy.run_path("alembic/versions/0019_drop_retired_feishu_resources.py")
    migration_text = Path("alembic/versions/0019_drop_retired_feishu_resources.py").read_text()

    assert migration["down_revision"] == "0018_detach_resource_from_legacy_feishu_table"
    assert "legacy_retirement.unmapped_legacy_resources=0" in migration_text
    assert "where not exists" in migration_text
    assert "resource.legacy_feishu_resource_id = legacy.id" in migration_text
    assert 'op.drop_table("feishu_resources")' in migration_text


def test_alembic_version_table_allows_long_v5_revision_ids() -> None:
    migration_text = Path("alembic/versions/0015_memory_fact_scope_constraints.py").read_text()

    assert 'op.alter_column("alembic_version", "version_num"' in migration_text
    assert "sa.String(length=128)" in migration_text
    for migration_path in Path("alembic/versions").glob("*.py"):
        migration = runpy.run_path(str(migration_path))
        assert len(migration["revision"]) <= 128


def test_local_prod_image_installs_feishu_cli_execution_layer() -> None:
    dockerfile = Path("Dockerfile").read_text()
    dockerignore = Path(".dockerignore").read_text()
    compose = Path("docker-compose.local-prod.yml").read_text()
    local_prod_check = Path("scripts/local_prod_check_v5.sh").read_text()
    cli_home_prepare = Path("scripts/prepare_lark_cli_home_v5.sh").read_text()

    assert "ARG LARK_CLI_VERSION=" in dockerfile
    assert "nodejs" in dockerfile
    assert "npm" in dockerfile
    assert 'npm install -g --omit=dev --no-audit --no-fund "@larksuite/cli@${LARK_CLI_VERSION}"' in dockerfile
    assert "COPY pyproject.toml requirements.lock ./" in dockerfile
    assert "COPY pyproject.toml README.md requirements.lock" not in dockerfile
    assert "lark-cli --version" in dockerfile
    assert ".npm-cache" in dockerignore
    assert ".local" in dockerignore
    assert "tmp" in dockerignore
    assert "app_secret*.txt" in dockerignore
    gitignore = Path(".gitignore").read_text()
    assert ".local/" in gitignore
    assert "tmp/" in gitignore
    assert "app_secret*.txt" in gitignore
    assert compose.count("${LARK_CLI_HOME:-${PWD}/.local/lark-cli-v5}:/root/.lark-cli\n") == 1
    assert compose.count("${LARK_CLI_HOME:-${PWD}/.local/lark-cli-v5}:/root/.lark-cli:ro") == 3
    assert compose.count(
        "${LARK_CLI_DATA_HOME:-${LARK_CLI_HOME:-${PWD}/.local/lark-cli-v5}/share}:/root/.local/share/lark-cli\n"
    ) == 1
    assert compose.count(
        "${LARK_CLI_DATA_HOME:-${LARK_CLI_HOME:-${PWD}/.local/lark-cli-v5}/share}:/root/.local/share/lark-cli:ro"
    ) == 3
    assert "docker compose -p \"$COMPOSE_PROJECT\" -f \"$COMPOSE_FILE\" config --quiet" in local_prod_check
    assert "exec -T api lark-cli doctor --offline" in local_prod_check
    assert "curl -fsS \"$API_URL/console\"" in local_prod_check
    assert "数字参谋驾驶舱" in local_prod_check
    assert "urljoin(api_url, asset_path.lstrip(\"/\"))" in local_prod_check
    assert "loadOperatingCenter" in local_prod_check
    assert "feishu_cli_identity_unavailable" in local_prod_check
    assert "V5_FEISHU_APP_ID" in local_prod_check
    assert "V5_FEISHU_APP_SECRET" in local_prod_check
    assert "scripts/bootstrap_feishu_app_config_v5.py" in local_prod_check
    assert "ALLOW_DEGRADED_FEISHU_CLI" in local_prod_check
    assert "docker compose config" not in local_prod_check
    assert "config init --app-id \"$LARK_APP_ID\" --app-secret-stdin" in cli_home_prepare
    assert "init-bot-container)" in cli_home_prepare
    assert "-v \"$LARK_CLI_DATA_HOME:/root/.local/share/lark-cli\"" in cli_home_prepare
    assert "-v \"$LARK_CLI_DATA_HOME:/root/.local/share/lark-cli:ro\"" in cli_home_prepare
    assert "--no-wait --json" in cli_home_prepare
    assert "auth qrcode \"$url\" --output tmp/v5-lark-auth-qrcode.png" in cli_home_prepare
    assert "auth login --device-code \"$LARK_DEVICE_CODE\"" in cli_home_prepare
    assert "-v \"$LARK_CLI_HOME:/root/.lark-cli:ro\"" in cli_home_prepare
    assert "--app-secret " not in cli_home_prepare


def test_sync_engine_does_not_call_tool_router_or_feishu_providers() -> None:
    sync_engine_paths = (
        Path("app/services/feishu/sync.py"),
        Path("app/services/feishu/sync_commands.py"),
        Path("app/services/feishu_admin_sync.py"),
        Path("app/services/v5_workspace.py"),
        Path("app/services/v5_auto_sync.py"),
        Path("app/tasks/celery_app.py"),
    )
    forbidden_tokens = (
        "app.services.tools",
        "execute_agent_tool",
        "ToolRequest",
        "ToolContext",
        "ToolProvider",
        "app.services.feishu.api_runtime",
        "execute_feishu_api_tool",
        "execute_feishu_api_write_tool",
        "execute_feishu_api_read_tool",
        "FEISHU_API_CAPABILITIES",
        "FEISHU_API_WRITE_BINDINGS",
        "app.services.tools.providers.feishu_mcp",
        "execute_feishu_mcp",
        "ToolProvider.FEISHU_MCP",
        "feishu_mcp",
    )
    offenders: list[str] = []
    for path in sync_engine_paths:
        text = path.read_text()
        offenders.extend(f"{path}:{token}" for token in forbidden_tokens if token in text)

    assert offenders == []


def test_feishu_admin_routes_do_not_call_native_write_clients_directly() -> None:
    text = Path("app/api/routes/feishu.py").read_text()

    forbidden_calls = (
        ".api_post(",
        ".api_put(",
        ".api_patch(",
        ".api_delete(",
        ".send_message(",
        ".update_message_content(",
    )

    assert [call for call in forbidden_calls if call in text] == []


def test_feishu_admin_realtime_read_routes_use_tool_router_not_api_services() -> None:
    collection_text = Path("app/api/routes/feishu.py").read_text()
    route_collection_text = Path("app/api/routes/feishu_admin_read_tool_routes.py").read_text()
    route_text = "\n".join(
        path.read_text()
        for path in (
            Path("app/api/routes/feishu_admin_read_tool_approval_routes.py"),
            Path("app/api/routes/feishu_admin_read_tool_bitable_table_routes.py"),
            Path("app/api/routes/feishu_admin_read_tool_bitable_record_routes.py"),
            Path("app/api/routes/feishu_admin_read_tool_calendar_routes.py"),
            Path("app/api/routes/feishu_admin_read_tool_contact_department_routes.py"),
            Path("app/api/routes/feishu_admin_read_tool_contact_user_routes.py"),
            Path("app/api/routes/feishu_admin_read_tool_contact_snapshot_routes.py"),
            Path("app/api/routes/feishu_admin_read_tool_drive_routes.py"),
            Path("app/api/routes/feishu_admin_read_tool_im_chat_routes.py"),
            Path("app/api/routes/feishu_admin_read_tool_im_message_routes.py"),
            Path("app/api/routes/feishu_admin_read_tool_knowledge_routes.py"),
            Path("app/api/routes/feishu_admin_read_tool_wiki_space_routes.py"),
            Path("app/api/routes/feishu_admin_read_tool_wiki_node_routes.py"),
            Path("app/api/routes/feishu_admin_read_tool_mail_folder_routes.py"),
            Path("app/api/routes/feishu_admin_read_tool_mail_message_detail_routes.py"),
            Path("app/api/routes/feishu_admin_read_tool_mail_message_list_routes.py"),
            Path("app/api/routes/feishu_admin_read_tool_meeting_routes.py"),
            Path("app/api/routes/feishu_admin_read_tool_task_routes.py"),
        )
    )
    service_text = Path("app/services/feishu_admin_read_tools.py").read_text()

    migrated_service_tokens = (
        "FeishuContactService",
        "FeishuCalendarService",
        "FeishuBitableService",
        "FeishuTaskService",
        "FeishuMailService",
    )
    migrated_tool_names = (
        "feishu_contact_department_children",
        "feishu_contact_department_users",
        "feishu_contact_organization_snapshot",
        "calendar_qa",
        "feishu_doc_fetch",
        "feishu_drive_file_list",
        "feishu_wiki_space_list",
        "feishu_wiki_node_list",
        "bitable_qa",
        "task_qa",
        "mail_qa",
        "feishu_im_chat_search",
        "feishu_im_message_list",
        "feishu_mail_folder_list",
        "feishu_mail_message_get",
        "feishu_vc_meeting_search",
    )

    assert "from app.api.routes.feishu_admin_read_tool_routes import router as admin_read_tool_router" in collection_text
    assert "router.include_router(admin_read_tool_router)" in collection_text
    assert "router.include_router(read_tool_approval_router)" in route_collection_text
    assert "router.include_router(read_tool_bitable_router)" in route_collection_text
    assert "router.include_router(read_tool_calendar_router)" in route_collection_text
    assert "router.include_router(read_tool_contact_router)" in route_collection_text
    assert "router.include_router(read_tool_drive_router)" in route_collection_text
    assert "router.include_router(read_tool_im_router)" in route_collection_text
    assert "router.include_router(read_tool_knowledge_router)" in route_collection_text
    assert "router.include_router(read_tool_mail_router)" in route_collection_text
    assert "router.include_router(read_tool_meeting_router)" in route_collection_text
    assert "router.include_router(read_tool_task_router)" in route_collection_text
    assert "router.include_router(read_tool_wiki_router)" in route_collection_text
    assert "from app.services.feishu_admin_read_tools import" not in collection_text
    assert "from app.services.feishu_admin_read_tools import" not in route_collection_text
    assert [token for token in migrated_service_tokens if token in route_text] == []
    assert "from app.services.feishu_admin_read_tools import" in route_text
    assert "execute_admin_feishu_read_tool(" in route_text
    assert "def execute_admin_feishu_read_tool(" in service_text
    assert "execute_agent_tool(" in service_text
    assert all(tool_name in route_text + service_text for tool_name in migrated_tool_names)
    assert ".fetch_pending_tasks(" not in route_text
    assert "feishu_approval_task_query" in service_text
    assert "feishu_approval_instance_get" in service_text


def test_feishu_admin_read_tool_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/feishu_admin_read_tool_routes.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert "router.include_router(read_tool_approval_router)" in collection_text
    assert "router.include_router(read_tool_bitable_router)" in collection_text
    assert "router.include_router(read_tool_calendar_router)" in collection_text
    assert "router.include_router(read_tool_contact_router)" in collection_text
    assert "router.include_router(read_tool_drive_router)" in collection_text
    assert "router.include_router(read_tool_im_router)" in collection_text
    assert "router.include_router(read_tool_knowledge_router)" in collection_text
    assert "router.include_router(read_tool_mail_router)" in collection_text
    assert "router.include_router(read_tool_meeting_router)" in collection_text
    assert "router.include_router(read_tool_task_router)" in collection_text
    assert "router.include_router(read_tool_wiki_router)" in collection_text


def test_feishu_admin_read_tool_mail_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/feishu_admin_read_tool_mail_routes.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert "router.include_router(read_tool_mail_folder_router)" in collection_text
    assert "router.include_router(read_tool_mail_message_detail_router)" in collection_text
    assert "router.include_router(read_tool_mail_message_list_router)" in collection_text


def test_feishu_admin_read_tool_im_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/feishu_admin_read_tool_im_routes.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert "router.include_router(read_tool_im_chat_router)" in collection_text
    assert "router.include_router(read_tool_im_message_router)" in collection_text


def test_feishu_admin_read_tool_wiki_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/feishu_admin_read_tool_wiki_routes.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert "router.include_router(read_tool_wiki_space_router)" in collection_text
    assert "router.include_router(read_tool_wiki_node_router)" in collection_text


def test_feishu_admin_read_tool_bitable_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/feishu_admin_read_tool_bitable_routes.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert "router.include_router(bitable_table_router)" in collection_text
    assert "router.include_router(bitable_record_router)" in collection_text


def test_feishu_admin_read_tool_contact_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/feishu_admin_read_tool_contact_routes.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert "router.include_router(contact_department_router)" in collection_text
    assert "router.include_router(contact_user_router)" in collection_text
    assert "router.include_router(contact_snapshot_router)" in collection_text


def test_feishu_admin_approval_action_lives_outside_route_collection() -> None:
    collection_text = Path("app/api/routes/feishu.py").read_text()
    route_collection_text = Path("app/api/routes/feishu_admin_write_routes.py").read_text()
    route_text = Path("app/api/routes/feishu_admin_write_approval_routes.py").read_text()
    service_text = Path("app/services/feishu_admin_write_tools.py").read_text()

    forbidden_route_tokens = (
        "ToolRequest(",
        "ToolContext(",
        "feishu_approval_task_approve",
        "feishu_approval_task_reject",
        "write_audit_log(",
        "confirmation_token",
    )

    assert "from app.api.routes.feishu_admin_write_routes import router as admin_write_router" in collection_text
    assert "router.include_router(admin_write_router)" in collection_text
    assert "router.include_router(admin_write_approval_router)" in route_collection_text
    assert "router.include_router(admin_write_im_router)" in route_collection_text
    assert "from app.services.feishu_admin_write_tools import" not in collection_text
    assert "from app.services.feishu_admin_write_tools import" not in route_collection_text
    assert [token for token in forbidden_route_tokens if token in route_text] == []
    assert "return submit_approval_action_payload(db, app_config, data)" in route_text
    assert "def submit_approval_action_payload(" in service_text
    assert "feishu_approval_task_approve" in service_text
    assert "feishu_approval_task_reject" in service_text
    assert "write_audit_log(" in service_text


def test_feishu_admin_im_write_routes_live_outside_route_collection() -> None:
    collection_text = Path("app/api/routes/feishu.py").read_text()
    route_collection_text = Path("app/api/routes/feishu_admin_write_routes.py").read_text()
    im_collection_text = Path("app/api/routes/feishu_admin_write_im_routes.py").read_text()
    route_text = "\n".join(
        [
            Path("app/api/routes/feishu_admin_write_im_message_routes.py").read_text(),
            Path("app/api/routes/feishu_admin_write_im_chat_routes.py").read_text(),
            Path("app/api/routes/feishu_admin_write_im_auto_join_routes.py").read_text(),
        ]
    )
    service_text = Path("app/services/feishu_admin_write_tools.py").read_text()

    forbidden_route_tokens = (
        "execute_agent_tool(",
        "ToolContext(",
        "ToolRequest(",
        "write_audit_log(",
        "feishu_im_send_message",
        "feishu_im_create_chat",
        "feishu_im_auto_join_public_chats",
        "confirmation_token",
        "write_target",
    )

    assert "from app.api.routes.feishu_admin_write_routes import router as admin_write_router" in collection_text
    assert "router.include_router(admin_write_router)" in collection_text
    assert "router.include_router(admin_write_approval_router)" in route_collection_text
    assert "router.include_router(admin_write_im_router)" in route_collection_text
    assert "from app.services.feishu_admin_write_tools import" not in collection_text
    assert "from app.services.feishu_admin_write_tools import" not in route_collection_text
    assert "router.include_router(im_message_router)" in im_collection_text
    assert "router.include_router(im_chat_router)" in im_collection_text
    assert "router.include_router(im_auto_join_router)" in im_collection_text
    assert "from app.services.feishu_admin_write_tools import" not in im_collection_text
    assert [token for token in forbidden_route_tokens if token in route_text] == []
    assert "return send_message_payload(db, app_config, data)" in route_text
    assert "return create_chat_with_bot_payload(db, app_config, data)" in route_text
    assert "return await auto_join_public_chats_payload(db, app_config, data)" in route_text
    assert "def send_message_payload(" in service_text
    assert "def create_chat_with_bot_payload(" in service_text
    assert "def auto_join_public_chats_payload(" in service_text
    assert "execute_agent_tool(" in service_text
    assert "write_audit_log(" in service_text


def test_feishu_admin_write_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/feishu_admin_write_routes.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert "router.include_router(admin_write_approval_router)" in collection_text
    assert "router.include_router(admin_write_im_router)" in collection_text


def test_feishu_admin_write_im_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/feishu_admin_write_im_routes.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert "router.include_router(im_message_router)" in collection_text
    assert "router.include_router(im_chat_router)" in collection_text
    assert "router.include_router(im_auto_join_router)" in collection_text


def test_feishu_admin_pending_approval_audit_lives_outside_route_collection() -> None:
    collection_text = Path("app/api/routes/feishu.py").read_text()
    route_text = Path("app/api/routes/feishu_admin_read_tool_approval_routes.py").read_text()
    service_text = Path("app/services/feishu_admin_read_tools.py").read_text()

    forbidden_route_tokens = (
        "fetch_admin_pending_approval_tasks(",
        "write_audit_log(",
        "db.commit()",
        "available",
    )

    assert "from app.api.routes.feishu_admin_read_tool_routes import router as admin_read_tool_router" in collection_text
    assert [token for token in forbidden_route_tokens if token in route_text] == []
    assert "return pending_approval_tasks_payload(db, app_config, open_id=data.open_id, limit=data.limit)" in route_text
    assert "def pending_approval_tasks_payload(" in service_text
    assert "fetch_admin_pending_approval_tasks(" in service_text
    assert "write_audit_log(" in service_text


def test_feishu_admin_contact_snapshot_audit_lives_outside_route_collection() -> None:
    collection_text = Path("app/api/routes/feishu.py").read_text()
    route_text = Path("app/api/routes/feishu_admin_read_tool_contact_snapshot_routes.py").read_text()
    contact_collection_text = Path("app/api/routes/feishu_admin_read_tool_contact_routes.py").read_text()
    service_text = Path("app/services/feishu_admin_read_tools.py").read_text()

    forbidden_route_tokens = (
        "feishu_contact_organization_snapshot",
        "write_audit_log(",
        "db.commit()",
        "department_count",
        "user_count",
    )

    assert "from app.api.routes.feishu_admin_read_tool_routes import router as admin_read_tool_router" in collection_text
    assert "router.include_router(contact_snapshot_router)" in contact_collection_text
    assert "from app.services.feishu_admin_read_tools import" not in contact_collection_text
    assert [token for token in forbidden_route_tokens if token in route_text] == []
    assert "return contact_snapshot_payload(db, app_config, data)" in route_text
    assert "def contact_snapshot_payload(" in service_text
    assert "feishu_contact_organization_snapshot" in service_text
    assert "feishu.contacts.snapshot" in service_text


def test_feishu_admin_api_read_helpers_live_outside_route_collection() -> None:
    collection_text = Path("app/api/routes/feishu.py").read_text()
    route_collection_text = Path("app/api/routes/feishu_admin_api_read_routes.py").read_text()
    route_text = "\n".join(
        path.read_text()
        for path in (
            Path("app/api/routes/feishu_admin_api_read_mail_access_routes.py"),
        )
    )
    mail_collection_text = Path("app/api/routes/feishu_admin_api_read_mail_routes.py").read_text()
    service_text = Path("app/services/feishu_admin_api_reads.py").read_text()

    forbidden_route_tokens = (
        "FeishuMailService(",
        "飞书邮箱没有列出所有可访问邮箱",
    )

    assert "from app.api.routes.feishu_admin_api_read_routes import router as admin_api_read_router" in collection_text
    assert "router.include_router(admin_api_read_router)" in collection_text
    assert "api_read_im_router" not in route_collection_text
    assert "api_read_knowledge_router" not in route_collection_text
    assert "router.include_router(api_read_mail_router)" in route_collection_text
    assert "api_read_meeting_router" not in route_collection_text
    assert "router.include_router(api_read_mail_access_router)" in mail_collection_text
    assert "api_read_mail_folder_router" not in mail_collection_text
    assert "api_read_mail_message_router" not in mail_collection_text
    assert "from app.services.feishu_admin_api_reads import" not in collection_text
    assert "from app.services.feishu_admin_api_reads import" not in route_collection_text
    assert "from app.services.feishu_admin_api_reads import" not in mail_collection_text
    assert [token for token in forbidden_route_tokens if token in route_text] == []
    assert "return accessible_mailboxes_payload()" in route_text
    assert "FeishuMailService(" not in service_text


def test_feishu_admin_api_read_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/feishu_admin_api_read_routes.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert "api_read_im_router" not in collection_text
    assert "api_read_knowledge_router" not in collection_text
    assert "router.include_router(api_read_mail_router)" in collection_text
    assert "api_read_meeting_router" not in collection_text


def test_retired_feishu_api_read_route_modules_do_not_return() -> None:
    restored_modules = [
        module
        for module in RETIRED_FEISHU_API_READ_ROUTE_MODULES
        if (Path("app/api/routes") / module).exists()
    ]

    assert restored_modules == []


def test_feishu_admin_api_read_mail_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/feishu_admin_api_read_mail_routes.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert "router.include_router(api_read_mail_access_router)" in collection_text
    assert "api_read_mail_folder_router" not in collection_text
    assert "api_read_mail_message_router" not in collection_text


def test_feishu_event_entrypoint_lives_outside_route_collection() -> None:
    collection_text = Path("app/api/routes/feishu.py").read_text()
    route_text = Path("app/api/routes/feishu_event_routes.py").read_text()
    service_text = Path("app/services/feishu_event_entrypoint.py").read_text()

    forbidden_route_tokens = (
        "verify_feishu_token(",
        "feishu_url_verification_challenge(",
        "ingest_feishu_event(",
        "handle_feishu_command_result(",
        "work_event_id",
        "command_handled",
        "gateway_result",
    )

    assert "from app.api.routes.feishu_event_routes import router as event_router" in collection_text
    assert "router.include_router(event_router)" in collection_text
    assert "from app.services.feishu_event_entrypoint import" not in collection_text
    assert [token for token in forbidden_route_tokens if token in route_text] == []
    assert "return await receive_feishu_event_payload(db, app_config, payload)" in route_text
    assert "verify_feishu_token(" in service_text
    assert "feishu_url_verification_challenge(" in service_text
    assert "ingest_feishu_event(" in service_text
    assert "handle_feishu_command_result(" in service_text
    assert '"gateway_result": command_result.as_payload()' in service_text


def test_feishu_cli_devops_health_check_covers_v5_tool_scope() -> None:
    devops_text = Path("app/services/tools/providers/devops.py").read_text()

    for command in (
        "approval",
        "attendance",
        "base",
        "calendar",
        "contact",
        "docs",
        "drive",
        "event",
        "im",
        "mail",
        "minutes",
        "okr",
        "task",
        "vc",
        "wiki",
    ):
        assert f'"{command}"' in devops_text


def test_work_events_route_logic_lives_outside_route_collection() -> None:
    collection_text = Path("app/api/routes/work_events.py").read_text()
    collection_route_collection_text = Path("app/api/routes/work_events_collection_routes.py").read_text()
    processing_route_collection_text = Path("app/api/routes/work_events_processing_routes.py").read_text()
    analysis_route_collection_text = Path("app/api/routes/work_events_analysis_routes.py").read_text()
    route_text = "\n".join(
        (
            Path("app/api/routes/work_events_create_routes.py").read_text(),
            Path("app/api/routes/work_events_list_routes.py").read_text(),
            Path("app/api/routes/work_events_detail_routes.py").read_text(),
            Path("app/api/routes/work_events_extract_routes.py").read_text(),
            Path("app/api/routes/work_events_vectorize_routes.py").read_text(),
            Path("app/api/routes/work_events_daily_report_routes.py").read_text(),
            Path("app/api/routes/work_events_vector_search_routes.py").read_text(),
            Path("app/api/routes/work_events_vector_pending_routes.py").read_text(),
        )
    )
    service_text = Path("app/services/work_events_admin.py").read_text()

    forbidden_route_tokens = (
        "select(",
        "db.get(",
        "db.commit()",
        "db.refresh(",
        "upsert_work_event(",
        "extract_items_for_event(",
        "generate_daily_report(",
        "WorkEventVectorIndex(",
        "vectorize_pending_work_events_task",
        "mark_event_vector_indexing_result(",
    )

    assert "router.include_router(work_events_analysis_router)" in collection_text
    assert "router.include_router(work_events_collection_router)" in collection_text
    assert "router.include_router(work_events_processing_router)" in collection_text
    assert "from app.services." not in collection_text
    assert "router.include_router(work_events_create_router)" in collection_route_collection_text
    assert "router.include_router(work_events_list_router)" in collection_route_collection_text
    assert "router.include_router(work_events_detail_router)" in collection_route_collection_text
    assert "from app.services." not in collection_route_collection_text
    assert "Depends(get_db)" not in collection_route_collection_text
    assert "router.include_router(work_events_extract_router)" in processing_route_collection_text
    assert "router.include_router(work_events_vectorize_router)" in processing_route_collection_text
    assert "from app.services." not in processing_route_collection_text
    assert "Depends(get_db)" not in processing_route_collection_text
    assert "router.include_router(work_events_daily_report_router)" in analysis_route_collection_text
    assert "router.include_router(work_events_vector_search_router)" in analysis_route_collection_text
    assert "router.include_router(work_events_vector_pending_router)" in analysis_route_collection_text
    assert "from app.services." not in analysis_route_collection_text
    assert "Depends(get_db)" not in analysis_route_collection_text
    assert [token for token in forbidden_route_tokens if token in route_text] == []
    assert "return create_work_event_payload(db, data)" in route_text
    assert "return list_work_event_payloads(db, company_id=company_id, limit=limit)" in route_text
    assert "return work_event_detail_payload(db, event_id)" in route_text
    assert "return extract_event_items_payload(db, event_id)" in route_text
    assert "return vectorize_event_payload(db, event_id)" in route_text
    assert "return daily_report_payload(db, data)" in route_text
    assert "return vector_search_payload(data)" in route_text
    assert "return vectorize_pending_payload(data)" in route_text
    assert "select(" in service_text
    assert "upsert_work_event(" in service_text
    assert "WorkEventVectorIndex(" in service_text
    assert "vectorize_pending_work_events_task" in service_text


def test_work_events_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/work_events.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert 'router = APIRouter(prefix="/api", tags=["work-events"]' in collection_text
    assert "router.include_router(work_events_analysis_router)" in collection_text
    assert "router.include_router(work_events_collection_router)" in collection_text
    assert "router.include_router(work_events_processing_router)" in collection_text


def test_work_events_nested_route_collections_only_include_subrouters() -> None:
    route_collections = {
        "app/api/routes/work_events_collection_routes.py": (
            "router.include_router(work_events_create_router)",
            "router.include_router(work_events_list_router)",
            "router.include_router(work_events_detail_router)",
        ),
        "app/api/routes/work_events_processing_routes.py": (
            "router.include_router(work_events_extract_router)",
            "router.include_router(work_events_vectorize_router)",
        ),
        "app/api/routes/work_events_analysis_routes.py": (
            "router.include_router(work_events_daily_report_router)",
            "router.include_router(work_events_vector_search_router)",
            "router.include_router(work_events_vector_pending_router)",
        ),
    }
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    for route_path, expected_includes in route_collections.items():
        collection_text = Path(route_path).read_text()
        assert [token for token in forbidden_tokens if token in collection_text] == []
        for expected_include in expected_includes:
            assert expected_include in collection_text


def test_company_basic_crud_lives_outside_route_collection() -> None:
    collection_text = Path("app/api/routes/companies.py").read_text()
    company_collection_text = Path("app/api/routes/companies_company_routes.py").read_text()
    account_collection_text = Path("app/api/routes/companies_account_routes.py").read_text()
    route_text = "\n".join(
        (
            Path("app/api/routes/companies_company_create_routes.py").read_text(),
            Path("app/api/routes/companies_company_list_routes.py").read_text(),
            Path("app/api/routes/companies_account_create_routes.py").read_text(),
            Path("app/api/routes/companies_account_list_routes.py").read_text(),
        )
    )
    service_text = Path("app/services/companies_admin.py").read_text()

    forbidden_route_tokens = (
        "select(",
        "db.get(",
        "db.commit()",
        "db.refresh(",
        "write_audit_log(",
        "Account(",
        "Company(",
        "require_account_type(",
        "upsert_mail_account_resource(",
    )

    assert "router.include_router(company_account_router)" in collection_text
    assert "router.include_router(company_onboarding_router)" in collection_text
    assert "router.include_router(company_router)" in collection_text
    assert "from app.services.companies_admin import" not in collection_text
    assert "router.include_router(company_create_router)" in company_collection_text
    assert "router.include_router(company_list_router)" in company_collection_text
    assert "from app.services.companies_admin import" not in company_collection_text
    assert "Depends(get_db)" not in company_collection_text
    assert "router.include_router(account_create_router)" in account_collection_text
    assert "router.include_router(account_list_router)" in account_collection_text
    assert "from app.services.companies_admin import" not in account_collection_text
    assert "Depends(get_db)" not in account_collection_text
    assert [token for token in forbidden_route_tokens if token in route_text] == []
    assert "return create_company_payload(db, data)" in route_text
    assert "return list_company_payloads(db)" in route_text
    assert "return create_account_payload(db, data)" in route_text
    assert "return list_company_account_payloads(db, company_id)" in route_text
    assert "select(" in service_text
    assert "write_audit_log(" in service_text
    assert "require_account_type(" in service_text


def test_quick_company_setup_lives_outside_route_collection() -> None:
    collection_text = Path("app/api/routes/companies.py").read_text()
    route_text = Path("app/api/routes/companies_onboarding_routes.py").read_text()
    request_model_text = Path("app/api/routes/companies_request_models.py").read_text()
    service_text = Path("app/services/companies_admin.py").read_text()

    forbidden_route_tokens = (
        "select(",
        "db.commit()",
        "db.rollback()",
        "write_audit_log(",
        "Company(",
        "FeishuAppConfig(",
        "BotUserAccess",
        "upsert_feishu_discovered_resource(",
        "upsert_resource(",
        "IntegrityError",
    )

    assert "router.include_router(company_onboarding_router)" in collection_text
    assert "from app.services.companies_admin import" not in collection_text
    assert [token for token in forbidden_route_tokens if token in route_text] == []
    assert "return quick_company_setup_payload(db, data)" in route_text
    assert "class QuickCompanySetupRequest(BaseModel):" in request_model_text
    assert "class QuickFeishuAppSetup(BaseModel):" in request_model_text
    assert "def quick_company_setup_payload(" in service_text
    assert "def upsert_quick_setup_mail_resource(" in service_text
    assert "def upsert_v5_mail_resource(" in service_text
    assert "bootstrap_v5_administration(db, company_id=company.id)" in service_text
    assert '"v5_bootstrap": bootstrap_result.as_dict()' in service_text
    assert "app_config.app_secret = data.feishu_app.app_secret" in service_text
    assert '"requires_feishu_app_config": app_config is None' in service_text
    assert "default_user_identity_authorizations(open_id=data.bot_admin_open_id)" in service_text
    assert "upsert_feishu_discovered_resource(" in service_text
    assert "write_audit_log(" in service_text


def test_companies_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/companies.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert 'router = APIRouter(prefix="/api", tags=["companies"]' in collection_text
    assert "router.include_router(company_account_router)" in collection_text
    assert "router.include_router(company_onboarding_router)" in collection_text
    assert "router.include_router(company_router)" in collection_text


def test_company_nested_route_collections_only_include_subrouters() -> None:
    route_collections = {
        "app/api/routes/companies_company_routes.py": (
            "router.include_router(company_create_router)",
            "router.include_router(company_list_router)",
        ),
        "app/api/routes/companies_account_routes.py": (
            "router.include_router(account_create_router)",
            "router.include_router(account_list_router)",
        ),
    }
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    for route_path, expected_includes in route_collections.items():
        collection_text = Path(route_path).read_text()
        assert [token for token in forbidden_tokens if token in collection_text] == []
        for expected_include in expected_includes:
            assert expected_include in collection_text


def test_mail_admin_route_logic_lives_outside_route_collection() -> None:
    collection_text = Path("app/api/routes/mail.py").read_text()
    imap_route_text = Path("app/api/routes/mail_imap_routes.py").read_text()
    oauth_collection_text = Path("app/api/routes/mail_oauth_routes.py").read_text()
    oauth_route_text = "\n".join(
        (
            Path("app/api/routes/mail_oauth_gmail_routes.py").read_text(),
            Path("app/api/routes/mail_oauth_graph_routes.py").read_text(),
        )
    )
    graph_route_text = Path("app/api/routes/mail_graph_routes.py").read_text()
    request_model_text = Path("app/api/routes/mail_request_models.py").read_text()
    route_text = "\n".join((imap_route_text, oauth_route_text, graph_route_text))
    service_text = Path("app/services/mail_admin.py").read_text()

    forbidden_route_tokens = (
        "start_sync_run(",
        "finish_sync_run(",
        "ImapMailClient(",
        "GmailOAuthService(",
        "GraphMailService(",
        "get_account_or_404(",
        "db.commit()",
    )

    assert "router.include_router(mail_graph_router)" in collection_text
    assert "router.include_router(mail_imap_router)" in collection_text
    assert "router.include_router(mail_oauth_router)" in collection_text
    assert "router.include_router(mail_oauth_gmail_router)" in oauth_collection_text
    assert "router.include_router(mail_oauth_graph_router)" in oauth_collection_text
    assert "from app.services.mail_admin import" not in oauth_collection_text
    assert "return " not in oauth_collection_text
    assert "from app.services.mail_admin import" not in collection_text
    assert [token for token in forbidden_route_tokens if token in route_text] == []
    assert "class ImapSyncRequest(BaseModel):" in request_model_text
    assert "return sync_imap_payload(db, account_id=data.account_id, folder=data.folder, limit=data.limit)" in route_text
    assert "return gmail_oauth_url_payload(state=state)" in route_text
    assert "return graph_oauth_url_payload(state=state)" in route_text
    assert "return await graph_messages_payload(access_token=access_token, limit=limit)" in route_text
    assert "def sync_imap_payload(" in service_text
    assert "start_sync_run(" in service_text
    assert "ImapMailClient(" in service_text
    assert "GmailOAuthService(" in service_text
    assert "GraphMailService(" in service_text


def test_mail_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/mail.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert 'router = APIRouter(prefix="/api/mail", tags=["mail"]' in collection_text
    assert "router.include_router(mail_graph_router)" in collection_text
    assert "router.include_router(mail_imap_router)" in collection_text
    assert "router.include_router(mail_oauth_router)" in collection_text


def test_mail_oauth_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/mail_oauth_routes.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert "router.include_router(mail_oauth_gmail_router)" in collection_text
    assert "router.include_router(mail_oauth_graph_router)" in collection_text


def test_cockpit_admin_route_logic_lives_outside_route_collection() -> None:
    collection_text = Path("app/api/routes/cockpit.py").read_text()
    overview_route_text = Path("app/api/routes/cockpit_overview_routes.py").read_text()
    module_route_text = Path("app/api/routes/cockpit_module_routes.py").read_text()
    route_text = "\n".join((overview_route_text, module_route_text))
    service_text = Path("app/services/cockpit_admin.py").read_text()

    forbidden_route_tokens = (
        "build_scope(",
        "build_cockpit_overview(",
        "build_cockpit_module(",
        "HTTPException(",
        "ValueError",
        ".model_dump()",
    )

    assert "router.include_router(cockpit_module_router)" in collection_text
    assert "router.include_router(cockpit_overview_router)" in collection_text
    assert "from app.services.cockpit_admin import" not in collection_text
    assert [token for token in forbidden_route_tokens if token in route_text] == []
    assert "return cockpit_overview_payload(" in overview_route_text
    assert "return cockpit_module_payload(" in module_route_text
    assert "build_scope(" in service_text
    assert "build_cockpit_overview(" in service_text
    assert "build_cockpit_module(" in service_text
    assert "HTTPException(" in service_text


def test_cockpit_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/cockpit.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert 'router = APIRouter(prefix="/api/cockpit", tags=["cockpit"]' in collection_text
    assert "router.include_router(cockpit_module_router)" in collection_text
    assert "router.include_router(cockpit_overview_router)" in collection_text


def test_feishu_admin_message_sync_lives_outside_route_collection() -> None:
    collection_text = Path("app/api/routes/feishu.py").read_text()
    route_collection_text = Path("app/api/routes/feishu_admin_sync_routes.py").read_text()
    route_text = Path("app/api/routes/feishu_admin_sync_message_routes.py").read_text()
    request_model_text = Path("app/api/routes/feishu_admin_sync_request_models.py").read_text()
    service_text = Path("app/services/feishu_admin_sync.py").read_text()

    forbidden_route_tokens = (
        "FeishuClient(",
        "ingest_feishu_message(",
        "client.list_messages(",
        "extract_items_for_event(",
        "while page_count",
        "def _to_epoch_seconds(",
    )

    assert "from app.api.routes.feishu_admin_sync_routes import router as admin_sync_router" in collection_text
    assert "router.include_router(admin_sync_router)" in collection_text
    assert "from app.services.feishu_admin_sync import" not in collection_text
    assert "from app.services.feishu_admin_sync import" not in route_collection_text
    assert "class FeishuSyncMessagesRequest(BaseModel):" in request_model_text
    assert [token for token in forbidden_route_tokens if token in route_text] == []
    assert "from app.services.feishu_admin_sync import sync_chat_messages_payload" in route_text
    assert "return await sync_chat_messages_payload(db, app_config, data)" in route_text
    assert "def to_epoch_seconds(" in service_text
    assert "FeishuClient(" in service_text
    assert "ingest_feishu_message(" in service_text


def test_feishu_admin_sync_and_discovery_live_outside_route_collection() -> None:
    collection_text = Path("app/api/routes/feishu.py").read_text()
    route_collection_text = Path("app/api/routes/feishu_admin_sync_routes.py").read_text()
    route_text = "\n".join(
        [
            Path("app/api/routes/feishu_admin_sync_information_routes.py").read_text(),
            Path("app/api/routes/feishu_admin_sync_organization_routes.py").read_text(),
            Path("app/api/routes/feishu_admin_sync_resource_routes.py").read_text(),
        ]
    )
    request_model_text = Path("app/api/routes/feishu_admin_sync_request_models.py").read_text()
    service_text = Path("app/services/feishu_admin_sync.py").read_text()

    forbidden_route_tokens = (
        "sync_feishu_information(",
        "discover_feishu_resources(",
        "discover_feishu_resources_task",
        "data.model_dump(",
        "saved_count",
        "approval_discovery_mode",
    )

    assert "from app.api.routes.feishu_admin_sync_routes import router as admin_sync_router" in collection_text
    assert "router.include_router(admin_sync_router)" in collection_text
    assert "from app.services.feishu_admin_sync import" not in collection_text
    assert "from app.services.feishu_admin_sync import" not in route_collection_text
    assert "class FeishuInformationSyncRequest(BaseModel):" in request_model_text
    assert "class FeishuOrganizationSyncRequest(BaseModel):" in request_model_text
    assert "class FeishuResourceDiscoverRequest(BaseModel):" in request_model_text
    assert [token for token in forbidden_route_tokens if token in route_text] == []
    assert "return await sync_app_information_payload(db, app_config, data)" in route_text
    assert "return await sync_organization_foundation_payload(db, app_config, data)" in route_text
    assert "return await discover_app_resources_payload(db, app_config, data)" in route_text
    assert "sync_feishu_information(" in service_text
    assert "discover_feishu_resources(" in service_text
    assert "discover_feishu_resources_task" in service_text


def test_feishu_admin_sync_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/feishu_admin_sync_routes.py").read_text()

    assert "router.include_router(sync_message_router)" in collection_text
    assert "router.include_router(sync_information_router)" in collection_text
    assert "router.include_router(sync_organization_router)" in collection_text
    assert "router.include_router(sync_resource_router)" in collection_text
    assert "BaseModel" not in collection_text
    assert "Depends(" not in collection_text
    assert "get_feishu_app_or_404" not in collection_text
    assert "from app.services.feishu_admin_sync import" not in collection_text


def test_feishu_admin_app_and_oauth_logic_lives_outside_route_collection() -> None:
    collection_text = Path("app/api/routes/feishu.py").read_text()
    route_collection_text = Path("app/api/routes/feishu_admin_app_routes.py").read_text()
    route_text = "\n".join(
        path.read_text()
        for path in (
            Path("app/api/routes/feishu_admin_app_create_routes.py"),
            Path("app/api/routes/feishu_admin_app_tenant_token_routes.py"),
            Path("app/api/routes/feishu_admin_app_client_routing_routes.py"),
            Path("app/api/routes/feishu_admin_app_oauth_url_routes.py"),
            Path("app/api/routes/feishu_admin_app_oauth_exchange_routes.py"),
            Path("app/api/routes/feishu_admin_app_user_account_list_routes.py"),
            Path("app/api/routes/feishu_admin_app_user_account_refresh_routes.py"),
        )
    )
    config_collection_text = Path("app/api/routes/feishu_admin_app_config_routes.py").read_text()
    oauth_collection_text = Path("app/api/routes/feishu_admin_app_oauth_routes.py").read_text()
    user_account_collection_text = Path("app/api/routes/feishu_admin_app_user_account_routes.py").read_text()
    request_model_text = Path("app/api/routes/feishu_admin_app_request_models.py").read_text()
    service_text = Path("app/services/feishu_admin_apps.py").read_text()

    forbidden_route_tokens = (
        "select(",
        "IntegrityError",
        "json_safe(",
        "FEISHU_ROUTE_RULES",
        "route_rule_to_dict(",
        "exchange_user_access_token(",
        "refresh_user_access_token(",
        "upsert_feishu_user_account(",
        "sanitized_feishu_user_account(",
        'Account.provider == "feishu_user"',
    )

    assert "from app.api.routes.feishu_admin_app_routes import router as admin_app_router" in collection_text
    assert "router.include_router(admin_app_router)" in collection_text
    assert "router.include_router(admin_app_config_router)" in route_collection_text
    assert "router.include_router(admin_app_oauth_router)" in route_collection_text
    assert "router.include_router(admin_app_user_account_router)" in route_collection_text
    assert "router.include_router(admin_app_create_router)" in config_collection_text
    assert "router.include_router(admin_app_tenant_token_router)" in config_collection_text
    assert "router.include_router(admin_app_client_routing_router)" in config_collection_text
    assert "router.include_router(admin_app_oauth_url_router)" in oauth_collection_text
    assert "router.include_router(admin_app_oauth_exchange_router)" in oauth_collection_text
    assert "router.include_router(admin_app_user_account_list_router)" in user_account_collection_text
    assert "router.include_router(admin_app_user_account_refresh_router)" in user_account_collection_text
    assert "from app.services.feishu_admin_apps import" not in collection_text
    assert "from app.services.feishu_admin_apps import" not in route_collection_text
    assert "from app.services.feishu_admin_apps import" not in config_collection_text
    assert "from app.services.feishu_admin_apps import" not in oauth_collection_text
    assert "from app.services.feishu_admin_apps import" not in user_account_collection_text
    assert [token for token in forbidden_route_tokens if token in route_text] == []
    assert "class FeishuAppCreate(BaseModel):" in request_model_text
    assert "class FeishuAppOut(BaseModel):" in request_model_text
    assert "class FeishuOAuthExchangeRequest(BaseModel):" in request_model_text
    assert "create_feishu_app_config(db, payload)" in route_text
    assert "refresh_tenant_access_token(db, app_config)" in route_text
    assert "feishu_client_routing_payload(app_config, path_or_key)" in route_text
    assert "Admin OAuth URL generation for personal Feishu accounts is retired" in route_text
    assert "Admin OAuth exchange for personal Feishu accounts is retired" in route_text
    assert "Admin refresh for personal Feishu accounts is retired" in route_text
    assert "/api/user-identity/oauth/feishu/start" in route_text
    assert "feishu_user_oauth_url_payload(app_config, state)" not in route_text
    assert "exchange_and_store_feishu_user_token(" not in route_text
    assert "list_feishu_user_account_payloads(db, app_config)" in route_text
    assert "refresh_feishu_user_token_payload(db, app_config, account_id)" not in route_text
    assert "select(" in service_text
    assert "IntegrityError" in service_text
    assert "upsert_feishu_user_account(" in service_text


def test_feishu_admin_app_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/feishu_admin_app_routes.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert "router.include_router(admin_app_config_router)" in collection_text
    assert "router.include_router(admin_app_oauth_router)" in collection_text
    assert "router.include_router(admin_app_user_account_router)" in collection_text


def test_feishu_admin_app_nested_route_collections_only_include_subrouters() -> None:
    route_collections = {
        "app/api/routes/feishu_admin_app_config_routes.py": (
            "router.include_router(admin_app_create_router)",
            "router.include_router(admin_app_tenant_token_router)",
            "router.include_router(admin_app_client_routing_router)",
        ),
        "app/api/routes/feishu_admin_app_oauth_routes.py": (
            "router.include_router(admin_app_oauth_url_router)",
            "router.include_router(admin_app_oauth_exchange_router)",
        ),
        "app/api/routes/feishu_admin_app_user_account_routes.py": (
            "router.include_router(admin_app_user_account_list_router)",
            "router.include_router(admin_app_user_account_refresh_router)",
        ),
    }
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    for route_path, expected_includes in route_collections.items():
        collection_text = Path(route_path).read_text()
        assert [token for token in forbidden_tokens if token in collection_text] == []
        for expected_include in expected_includes:
            assert expected_include in collection_text


def test_feishu_oauth_callback_lives_outside_route_collection() -> None:
    collection_text = Path("app/api/routes/feishu.py").read_text()
    route_text = Path("app/api/routes/feishu_oauth_routes.py").read_text()
    helper_text = Path("app/services/feishu_oauth_helpers.py").read_text()

    forbidden_route_tokens = (
        "app_config_id_from_oauth_state(",
        "callback_error_text(",
        "feishu_oauth_callback_page(",
        "db.get(FeishuAppConfig",
        "exchange_and_store_feishu_user_token(",
        "display_name",
    )

    assert "from app.api.routes.feishu_oauth_routes import router as oauth_router" in collection_text
    assert "router.include_router(oauth_router)" in collection_text
    assert "from app.services.feishu_oauth_helpers import" not in collection_text
    assert [token for token in forbidden_route_tokens if token in route_text] == []
    assert "return await feishu_oauth_callback_payload(db, code=code, state=state)" in route_text
    assert "async def feishu_oauth_callback_payload(" in helper_text
    assert "db.get(FeishuAppConfig" in helper_text
    assert "exchange_and_store_feishu_user_token(" in helper_text
    assert "feishu_oauth_callback_page(" in helper_text


def test_feishu_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/feishu.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.feishu",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert "router = APIRouter(prefix=\"/api/feishu\"" in collection_text
    assert "router.include_router(admin_api_read_router)" in collection_text
    assert "router.include_router(admin_app_router)" in collection_text
    assert "router.include_router(admin_capability_router)" in collection_text
    assert "router.include_router(admin_read_tool_router)" in collection_text
    assert "router.include_router(admin_sync_router)" in collection_text
    assert "router.include_router(admin_write_router)" in collection_text
    assert "router.include_router(event_router)" in collection_text
    assert "router.include_router(oauth_router)" in collection_text


def test_feishu_admin_capability_helpers_live_outside_route_collection() -> None:
    collection_text = Path("app/api/routes/feishu.py").read_text()
    route_collection_text = Path("app/api/routes/feishu_admin_capability_routes.py").read_text()
    route_text = "\n".join(
        path.read_text()
        for path in (
            Path("app/api/routes/feishu_admin_capability_sync_plan_routes.py"),
            Path("app/api/routes/feishu_admin_capability_probe_routes.py"),
            Path("app/api/routes/feishu_admin_capability_approval_resource_routes.py"),
        )
    )
    service_text = Path("app/services/feishu_admin_capabilities.py").read_text()

    forbidden_route_tokens = (
        "get_feishu_sync_plan(",
        "probe_feishu_capabilities(",
        "FeishuApprovalService(",
        "write_audit_log(",
        "legacy_resource_id",
    )

    assert "from app.api.routes.feishu_admin_capability_routes import router as admin_capability_router" in collection_text
    assert "router.include_router(admin_capability_router)" in collection_text
    assert "from app.services.feishu_admin_capabilities import" not in collection_text
    assert "router.include_router(admin_capability_sync_plan_router)" in route_collection_text
    assert "router.include_router(admin_capability_probe_router)" in route_collection_text
    assert "router.include_router(admin_capability_approval_resource_router)" in route_collection_text
    assert "from app.services.feishu_admin_capabilities import" not in route_collection_text
    assert "Depends(get_db)" not in route_collection_text
    assert [token for token in forbidden_route_tokens if token in route_text] == []
    assert "return feishu_sync_plan_payload()" in route_text
    assert "return await probe_app_capabilities_payload(db, app_config)" in route_text
    assert "return approval_resources_payload(db, app_config)" in route_text
    assert "get_feishu_sync_plan(" in service_text
    assert "probe_feishu_capabilities(" in service_text
    assert "FeishuApprovalService(" in service_text


def test_feishu_admin_capability_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/feishu_admin_capability_routes.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert "router.include_router(admin_capability_sync_plan_router)" in collection_text
    assert "router.include_router(admin_capability_probe_router)" in collection_text
    assert "router.include_router(admin_capability_approval_resource_router)" in collection_text


def test_v5_tool_admin_logic_lives_outside_route_collection() -> None:
    collection_text = Path("app/api/routes/v5.py").read_text()
    route_collection_text = Path("app/api/routes/v5_tool_routes.py").read_text()
    list_collection_text = Path("app/api/routes/v5_tool_list_routes.py").read_text()
    tool_catalog_route_text = Path("app/api/routes/v5_tool_catalog_routes.py").read_text()
    tool_execution_log_route_text = Path("app/api/routes/v5_tool_execution_log_routes.py").read_text()
    execution_route_text = Path("app/api/routes/v5_tool_execution_routes.py").read_text()
    config_collection_text = Path("app/api/routes/v5_tool_config_routes.py").read_text()
    config_route_text = "\n".join(
        [
            Path("app/api/routes/v5_tool_single_config_routes.py").read_text(),
            Path("app/api/routes/v5_tool_batch_config_routes.py").read_text(),
        ]
    )
    route_text = "\n".join((tool_catalog_route_text, tool_execution_log_route_text, execution_route_text, config_route_text))
    service_text = Path("app/services/v5_tool_admin.py").read_text()

    forbidden_route_tokens = (
        "ToolContext(",
        "ToolRequest(",
        "execute_agent_tool(",
        "TOOL_REGISTRY",
        "def _tool_execution_payload(",
        "def _tool_batch_target_names(",
        "write_target_summary(",
    )

    assert "from app.api.routes.v5_tool_routes import router as tool_router" in collection_text
    assert "router.include_router(tool_router)" in collection_text
    assert "from app.services.v5_tool_admin import (" not in collection_text
    assert "from app.services." not in route_collection_text
    assert "router.include_router(tool_config_router)" in route_collection_text
    assert "router.include_router(tool_execution_router)" in route_collection_text
    assert "router.include_router(tool_list_router)" in route_collection_text
    assert "router.include_router(tool_catalog_router)" in list_collection_text
    assert "router.include_router(tool_execution_log_router)" in list_collection_text
    assert "from app.services.v5_tool_admin import" not in list_collection_text
    assert "return " not in list_collection_text
    assert "router.include_router(tool_single_config_router)" in config_collection_text
    assert "router.include_router(tool_batch_config_router)" in config_collection_text
    assert "from app.services." not in config_collection_text
    assert "Depends(get_db)" not in config_collection_text
    assert [token for token in forbidden_route_tokens if token in route_text] == []
    assert "from app.services.v5_tool_admin import list_tools_for_company" in tool_catalog_route_text
    assert "from app.services.v5_tool_admin import list_tool_execution_logs" in tool_execution_log_route_text
    assert "from app.services.v5_tool_admin import execute_admin_tool" in route_text
    assert "from app.services.v5_tool_admin import update_tool_configuration" in route_text
    assert "from app.services.v5_tool_admin import batch_update_tool_configurations" in route_text
    assert "return list_tools_for_company(db, company_id=company_id)" in route_text
    assert "return list_tool_execution_logs(" in route_text
    assert "return execute_admin_tool(" in route_text
    assert "return update_tool_configuration(" in route_text
    assert "return batch_update_tool_configurations(" in route_text
    assert "ToolContext(" in service_text
    assert "ToolRequest(" in service_text
    assert "execute_agent_tool(" in service_text
    assert "TOOL_REGISTRY" in service_text
    assert "def tool_execution_payload(" in service_text
    assert "def tool_batch_target_names(" in service_text


def test_v5_tool_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/v5_tool_routes.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert "router.include_router(tool_config_router)" in collection_text
    assert "router.include_router(tool_execution_router)" in collection_text
    assert "router.include_router(tool_list_router)" in collection_text


def test_v5_tool_list_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/v5_tool_list_routes.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert "router.include_router(tool_catalog_router)" in collection_text
    assert "router.include_router(tool_execution_log_router)" in collection_text


def test_v5_tool_config_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/v5_tool_config_routes.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert "router.include_router(tool_single_config_router)" in collection_text
    assert "router.include_router(tool_batch_config_router)" in collection_text


def test_v5_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/v5.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.v5_",
        "db.get(",
        "db.commit(",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert "router = APIRouter(prefix=\"/api/v5\"" in collection_text
    assert "router.include_router(administration_router)" in collection_text
    assert "router.include_router(agent_router)" in collection_text
    assert "router.include_router(intelligence_router)" in collection_text
    assert "router.include_router(resource_router)" in collection_text
    assert "router.include_router(system_log_router)" in collection_text
    assert "router.include_router(tool_router)" in collection_text


def test_all_v5_route_collections_only_include_subrouters() -> None:
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.patch(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
        "select(",
        "db.",
    )
    offenders: list[str] = []

    for path in sorted(Path("app/api/routes").glob("v5*.py")):
        text = path.read_text()
        if "router.include_router(" not in text:
            continue
        offenders.extend(f"{path}:{token}" for token in forbidden_tokens if token in text)

    assert offenders == []


def test_all_v5_route_files_keep_single_endpoint_boundary() -> None:
    offenders: list[str] = []
    endpoint_pattern = re.compile(r"@router\.(get|post|put|patch|delete)\(")

    for path in sorted(Path("app/api/routes").glob("v5*.py")):
        endpoint_count = len(endpoint_pattern.findall(path.read_text()))
        if endpoint_count > 1:
            offenders.append(f"{path}:{endpoint_count}")

    assert offenders == []


def test_all_route_files_keep_single_endpoint_boundary() -> None:
    offenders: list[str] = []
    endpoint_pattern = re.compile(r"@router\.(get|post|put|patch|delete)\(")

    for path in sorted(Path("app/api/routes").glob("*.py")):
        endpoint_count = len(endpoint_pattern.findall(path.read_text()))
        if endpoint_count > 1:
            offenders.append(f"{path}:{endpoint_count}")

    assert offenders == []


def test_all_route_collections_keep_business_logic_out() -> None:
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.patch(",
        "@router.delete(",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
        "select(",
        "db.",
    )
    offenders: list[str] = []

    for path in sorted(Path("app/api/routes").glob("*.py")):
        text = path.read_text()
        if "router.include_router(" not in text:
            continue
        offenders.extend(f"{path}:{token}" for token in forbidden_tokens if token in text)

    assert offenders == []


def test_v5_agent_admin_logic_lives_outside_route_collection() -> None:
    collection_text = Path("app/api/routes/v5.py").read_text()
    route_collection_text = Path("app/api/routes/v5_agent_routes.py").read_text()
    route_text = "\n".join(
        [
            Path("app/api/routes/v5_agent_trace_preview_routes.py").read_text(),
            Path("app/api/routes/v5_agent_trace_list_routes.py").read_text(),
            Path("app/api/routes/v5_agent_settings_read_routes.py").read_text(),
            Path("app/api/routes/v5_agent_settings_write_routes.py").read_text(),
        ]
    )
    request_model_text = Path("app/api/routes/v5_agent_request_models.py").read_text()
    service_text = Path("app/services/v5_agent_admin.py").read_text()

    forbidden_route_tokens = (
        "BotActor(",
        "answer_agent_message_with_trace(",
        "agent_runtime_result_payload(",
        "get_company_agent_settings(",
        "update_company_agent_settings(",
        "def _agent_trace_payload(",
        "def _agent_trace_write_policy_summary(",
    )

    assert "from app.api.routes.v5_agent_routes import router as agent_router" in collection_text
    assert "router.include_router(agent_router)" in collection_text
    assert "from app.services.v5_agent_admin import (" not in collection_text
    assert "router.include_router(agent_trace_router)" in route_collection_text
    assert "router.include_router(agent_settings_router)" in route_collection_text
    assert "from app.services.v5_agent_admin import" not in route_collection_text
    assert "BaseModel" not in route_collection_text
    assert "router.include_router(agent_trace_preview_router)" in Path(
        "app/api/routes/v5_agent_trace_routes.py"
    ).read_text()
    assert "router.include_router(agent_trace_list_router)" in Path("app/api/routes/v5_agent_trace_routes.py").read_text()
    assert "from app.services.v5_agent_admin import" not in Path("app/api/routes/v5_agent_trace_routes.py").read_text()
    assert "Depends(get_db)" not in Path("app/api/routes/v5_agent_trace_routes.py").read_text()
    assert "router.include_router(agent_settings_read_router)" in Path(
        "app/api/routes/v5_agent_settings_routes.py"
    ).read_text()
    assert "router.include_router(agent_settings_write_router)" in Path(
        "app/api/routes/v5_agent_settings_routes.py"
    ).read_text()
    assert "from app.services.v5_agent_admin import" not in Path("app/api/routes/v5_agent_settings_routes.py").read_text()
    assert "Depends(get_db)" not in Path("app/api/routes/v5_agent_settings_routes.py").read_text()
    assert "class AgentTracePreviewRequest(BaseModel):" in request_model_text
    assert "class AgentSettingsUpdate(BaseModel):" in request_model_text
    assert [token for token in forbidden_route_tokens if token in route_text] == []
    assert "from app.services.v5_agent_admin import preview_agent_trace" in route_text
    assert "from app.services.v5_agent_admin import list_agent_trace_logs" in route_text
    assert "from app.services.v5_agent_admin import read_agent_settings" in route_text
    assert "from app.services.v5_agent_admin import save_agent_settings as save_agent_settings_service" in route_text
    assert "return preview_agent_trace(" in route_text
    assert "return list_agent_trace_logs(" in route_text
    assert "return read_agent_settings(db, company_id=company_id)" in route_text
    assert "return save_agent_settings_service(" in route_text
    assert "BotActor(" in service_text
    assert "answer_agent_message_with_trace(" in service_text
    assert "agent_runtime_result_payload(" in service_text
    assert "get_company_agent_settings(" in service_text
    assert "update_company_agent_settings(" in service_text
    assert "def agent_trace_payload(" in service_text
    assert "def agent_trace_write_policy_summary(" in service_text


def test_v5_system_log_logic_lives_outside_route_collection() -> None:
    collection_text = Path("app/api/routes/v5.py").read_text()
    route_collection_text = Path("app/api/routes/v5_system_log_routes.py").read_text()
    route_text = "\n".join(
        [
            Path("app/api/routes/v5_system_log_overview_routes.py").read_text(),
            Path("app/api/routes/v5_system_log_list_routes.py").read_text(),
        ]
    )
    service_text = Path("app/services/v5_system_logs.py").read_text()

    forbidden_route_tokens = (
        "AuditLog",
        "system_log_overview_from_audit_logs(",
        "filtered_system_log_overview_from_audit_logs(",
        "confirmation_token_checked is not None",
        "def _bounded_log_limit(",
    )

    assert "from app.api.routes.v5_system_log_routes import router as system_log_router" in collection_text
    assert "router.include_router(system_log_router)" in collection_text
    assert "from app.services.v5_system_logs import" not in collection_text
    assert "router.include_router(system_log_overview_router)" in route_collection_text
    assert "router.include_router(system_log_list_router)" in route_collection_text
    assert "from app.services.v5_system_logs import" not in route_collection_text
    assert "Depends(get_db)" not in route_collection_text
    assert [token for token in forbidden_route_tokens if token in route_text] == []
    assert "from app.services.v5_system_logs import system_logs_overview_for_company" in route_text
    assert "from app.services.v5_system_logs import list_system_log_items" in route_text
    assert "return system_logs_overview_for_company(db, company_id=company_id, limit=limit)" in route_text
    assert "return list_system_log_items(" in route_text
    assert "AuditLog" in service_text
    assert "system_log_overview_from_audit_logs(" in service_text
    assert "filtered_system_log_overview_from_audit_logs(" in service_text
    assert "def _bounded_log_limit(" in service_text


def test_v5_system_log_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/v5_system_log_routes.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert "router.include_router(system_log_overview_router)" in collection_text
    assert "router.include_router(system_log_list_router)" in collection_text


def test_v5_administration_list_logic_lives_outside_route_collection() -> None:
    collection_text = Path("app/api/routes/v5.py").read_text()
    route_collection_text = Path("app/api/routes/v5_administration_routes.py").read_text()
    foundation_collection_text = Path("app/api/routes/v5_administration_foundation_routes.py").read_text()
    foundation_route_text = "\n".join(
        [
            Path("app/api/routes/v5_administration_bootstrap_routes.py").read_text(),
            Path("app/api/routes/v5_administration_os_overview_routes.py").read_text(),
        ]
    )
    list_collection_text = Path("app/api/routes/v5_administration_list_routes.py").read_text()
    user_setting_collection_text = Path("app/api/routes/v5_administration_user_setting_routes.py").read_text()
    org_collection_text = Path("app/api/routes/v5_administration_org_routes.py").read_text()
    permission_collection_text = Path("app/api/routes/v5_administration_permission_routes.py").read_text()
    list_route_text = "\n".join(
        [
            Path("app/api/routes/v5_administration_user_routes.py").read_text(),
            Path("app/api/routes/v5_administration_company_setting_routes.py").read_text(),
            Path("app/api/routes/v5_administration_department_routes.py").read_text(),
            Path("app/api/routes/v5_administration_team_routes.py").read_text(),
            Path("app/api/routes/v5_administration_role_routes.py").read_text(),
            Path("app/api/routes/v5_administration_permission_list_routes.py").read_text(),
            Path("app/api/routes/v5_administration_resource_permission_routes.py").read_text(),
        ]
    )
    access_route_text = Path("app/api/routes/v5_administration_access_routes.py").read_text()
    route_text = "\n".join((foundation_route_text, list_route_text, access_route_text))
    service_text = Path("app/services/v5_administration.py").read_text()

    forbidden_route_tokens = (
        ".join(UserCompanyRole, UserCompanyRole.user_id == User.id)",
        ".outerjoin(CompanySetting, CompanySetting.company_id == Company.id)",
        "select(Department).order_by(Department.company_id.asc(), Department.name.asc())",
        "select(Team, Department)",
        "select(Permission).order_by(Permission.company_id.asc(), Permission.permission_type.asc(), Permission.code.asc())",
        "select(ResourcePermission, Resource, Role, User)",
        "AccessPrincipal(",
        "build_principal_for_user(",
        "access_preview_for_resource(",
        "def _department_payload(",
    )

    assert "from app.api.routes.v5_administration_routes import router as administration_router" in collection_text
    assert "router.include_router(administration_router)" in collection_text
    assert "from app.services.v5_administration import (" not in collection_text
    assert "from app.services." not in route_collection_text
    assert "router.include_router(administration_access_router)" in route_collection_text
    assert "router.include_router(administration_foundation_router)" in route_collection_text
    assert "router.include_router(administration_list_router)" in route_collection_text
    assert "router.include_router(administration_bootstrap_router)" in foundation_collection_text
    assert "router.include_router(administration_os_overview_router)" in foundation_collection_text
    assert "from app.services.v5_administration import" not in foundation_collection_text
    assert "Depends(get_db)" not in foundation_collection_text
    assert "router.include_router(administration_user_setting_router)" in list_collection_text
    assert "router.include_router(administration_org_router)" in list_collection_text
    assert "router.include_router(administration_permission_router)" in list_collection_text
    assert "from app.services.v5_administration import" not in list_collection_text
    assert "Depends(get_db)" not in list_collection_text
    assert "router.include_router(administration_user_router)" in user_setting_collection_text
    assert "router.include_router(administration_company_setting_router)" in user_setting_collection_text
    assert "from app.services.v5_administration import" not in user_setting_collection_text
    assert "Depends(get_db)" not in user_setting_collection_text
    assert "router.include_router(administration_department_router)" in org_collection_text
    assert "router.include_router(administration_team_router)" in org_collection_text
    assert "from app.services.v5_administration import" not in org_collection_text
    assert "Depends(get_db)" not in org_collection_text
    assert "router.include_router(administration_role_router)" in permission_collection_text
    assert "router.include_router(administration_permission_list_router)" in permission_collection_text
    assert "router.include_router(administration_resource_permission_router)" in permission_collection_text
    assert "from app.services.v5_administration import" not in permission_collection_text
    assert "Depends(get_db)" not in permission_collection_text
    assert [token for token in forbidden_route_tokens if token in route_text] == []
    assert "from app.services.v5_administration import bootstrap_v5_administration" in route_text
    assert "from app.services.v5_administration import v5_os_overview" in route_text
    assert "list_administration_company_settings" in route_text
    assert "from app.services.v5_administration import preview_administration_resource_access" in route_text
    assert "return v5_os_overview(db, company_id=company_id)" in route_text
    assert "return list_administration_users(db, company_id=company_id)" in route_text
    assert "return list_administration_company_settings(db, company_id=company_id)" in route_text
    assert "return list_administration_departments(db, company_id=company_id)" in route_text
    assert "return list_administration_teams(db, company_id=company_id)" in route_text
    assert "return list_administration_roles(db, company_id=company_id)" in route_text
    assert "return list_administration_permissions(db, company_id=company_id)" in route_text
    assert "return list_administration_resource_permissions(db, company_id=company_id)" in route_text
    assert "return preview_administration_resource_access(" in route_text
    assert "def list_administration_users(" in service_text
    assert "def list_administration_resource_permissions(" in service_text
    assert "def preview_administration_resource_access(" in service_text
    assert "def department_payload(" in service_text
    assert "def v5_os_overview(" in service_text
    assert '"entrypoints": {' in service_text
    assert '"feishu_bot": {' in service_text
    assert "settings.feishu_bot_ai_mode_enabled" in service_text
    assert '"admin_console": {"enabled": True}' in service_text
    assert '"ios_app": {"enabled": False, "status": "planned"}' in service_text


def test_v5_administration_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/v5_administration_routes.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert "router.include_router(administration_access_router)" in collection_text
    assert "router.include_router(administration_foundation_router)" in collection_text
    assert "router.include_router(administration_list_router)" in collection_text


def test_v5_administration_list_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/v5_administration_list_routes.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert "router.include_router(administration_user_setting_router)" in collection_text
    assert "router.include_router(administration_org_router)" in collection_text
    assert "router.include_router(administration_permission_router)" in collection_text


def test_v5_administration_nested_route_collections_only_include_subrouters() -> None:
    route_collections = {
        "app/api/routes/v5_administration_foundation_routes.py": (
            "router.include_router(administration_bootstrap_router)",
            "router.include_router(administration_os_overview_router)",
        ),
        "app/api/routes/v5_administration_user_setting_routes.py": (
            "router.include_router(administration_user_router)",
            "router.include_router(administration_company_setting_router)",
        ),
        "app/api/routes/v5_administration_org_routes.py": (
            "router.include_router(administration_department_router)",
            "router.include_router(administration_team_router)",
        ),
        "app/api/routes/v5_administration_permission_routes.py": (
            "router.include_router(administration_role_router)",
            "router.include_router(administration_permission_list_router)",
            "router.include_router(administration_resource_permission_router)",
        ),
    }
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    for route_path, expected_includes in route_collections.items():
        collection_text = Path(route_path).read_text()
        assert [token for token in forbidden_tokens if token in collection_text] == []
        for expected_include in expected_includes:
            assert expected_include in collection_text


def test_v5_resource_status_logic_lives_outside_route_collection() -> None:
    collection_text = Path("app/api/routes/v5.py").read_text()
    route_collection_text = Path("app/api/routes/v5_resource_routes.py").read_text()
    status_collection_text = Path("app/api/routes/v5_resource_status_routes.py").read_text()
    overview_collection_text = Path("app/api/routes/v5_resource_overview_routes.py").read_text()
    status_route_text = "\n".join(
        [
            Path("app/api/routes/v5_resource_list_routes.py").read_text(),
            Path("app/api/routes/v5_resource_company_overview_routes.py").read_text(),
            Path("app/api/routes/v5_resource_sync_strategy_routes.py").read_text(),
            Path("app/api/routes/v5_resource_sync_status_list_routes.py").read_text(),
            Path("app/api/routes/v5_resource_sync_run_routes.py").read_text(),
            Path("app/api/routes/v5_resource_monitoring_routes.py").read_text(),
            Path("app/api/routes/v5_workspace_event_routes.py").read_text(),
        ]
    )
    policy_collection_text = Path("app/api/routes/v5_resource_policy_routes.py").read_text()
    policy_route_text = "\n".join(
        [
            Path("app/api/routes/v5_resource_policy_read_routes.py").read_text(),
            Path("app/api/routes/v5_resource_policy_write_routes.py").read_text(),
        ]
    )
    workspace_collection_text = Path("app/api/routes/v5_resource_workspace_routes.py").read_text()
    single_sync_collection_text = Path("app/api/routes/v5_resource_single_sync_routes.py").read_text()
    workspace_route_text = "\n".join(
        [
            Path("app/api/routes/v5_resource_direct_sync_routes.py").read_text(),
            Path("app/api/routes/v5_resource_retry_routes.py").read_text(),
            Path("app/api/routes/v5_resource_clear_access_block_routes.py").read_text(),
            Path("app/api/routes/v5_resource_access_decision_routes.py").read_text(),
        ]
    )
    sync_collection_text = Path("app/api/routes/v5_resource_sync_routes.py").read_text()
    sync_route_text = "\n".join(
        [
            Path("app/api/routes/v5_resource_batch_preview_routes.py").read_text(),
            Path("app/api/routes/v5_resource_batch_execute_routes.py").read_text(),
        ]
    )
    route_text = "\n".join((status_route_text, policy_route_text, workspace_route_text, sync_route_text))
    service_text = Path("app/services/v5_resource_status.py").read_text()
    workspace_text = Path("app/services/v5_workspace.py").read_text()
    auto_sync_text = Path("app/services/v5_auto_sync.py").read_text()

    forbidden_route_tokens = (
        "from app.models.entities import",
        "select(",
        "func.",
        "db.get(",
        "ResourceSyncRun",
        "select(WorkEvent).order_by(WorkEvent.occurred_at.desc())",
        "def _resource_sync_run_payload(",
        "def _rag_indexing_summary(",
        "def _document_store_summary(",
        "def _company_resource_overview_item(",
        "def _candidate_resources_for_batch_sync(",
        "def _batch_sync_policy(",
        "def _company_setting_payload(",
        "def _company_counts(",
        "def _count_v5_users(",
        "get_v5_resource_sync_policy(",
        "update_v5_resource_sync_policy(",
    )

    assert "from app.api.routes.v5_resource_routes import router as resource_router" in collection_text
    assert "router.include_router(resource_router)" in collection_text
    assert "from app.services.v5_resource_status import (" not in collection_text
    assert "from app.services.v5_workspace import (" not in collection_text
    assert "from app.services.v5_auto_sync import" not in collection_text
    assert "from app.services.v5_sync_policy import" not in collection_text
    assert "from app.services.v5_sync_strategy import" not in collection_text
    assert "from app.services." not in route_collection_text
    assert "router.include_router(resource_policy_router)" in route_collection_text
    assert "router.include_router(resource_status_router)" in route_collection_text
    assert "router.include_router(resource_sync_router)" in route_collection_text
    assert "router.include_router(resource_workspace_router)" in route_collection_text
    assert "router.include_router(resource_batch_preview_router)" in sync_collection_text
    assert "router.include_router(resource_batch_execute_router)" in sync_collection_text
    assert "from app.services.v5_auto_sync import" not in sync_collection_text
    assert "Depends(get_db)" not in sync_collection_text
    assert "router.include_router(resource_overview_router)" in status_collection_text
    assert "router.include_router(resource_sync_status_router)" in status_collection_text
    assert "router.include_router(workspace_event_router)" in status_collection_text
    assert "from app.services." not in status_collection_text
    assert "Depends(get_db)" not in status_collection_text
    assert "router.include_router(resource_list_router)" in overview_collection_text
    assert "router.include_router(resource_company_overview_router)" in overview_collection_text
    assert "router.include_router(resource_sync_strategy_router)" in overview_collection_text
    assert "from app.services." not in overview_collection_text
    assert "Depends(get_db)" not in overview_collection_text
    assert "router.include_router(resource_policy_read_router)" in policy_collection_text
    assert "router.include_router(resource_policy_write_router)" in policy_collection_text
    assert "from app.services." not in policy_collection_text
    assert "Depends(get_db)" not in policy_collection_text
    assert "router.include_router(resource_sync_status_list_router)" in Path(
        "app/api/routes/v5_resource_sync_status_routes.py"
    ).read_text()
    assert "router.include_router(resource_sync_run_router)" in Path(
        "app/api/routes/v5_resource_sync_status_routes.py"
    ).read_text()
    assert "router.include_router(resource_monitoring_router)" in Path(
        "app/api/routes/v5_resource_sync_status_routes.py"
    ).read_text()
    assert "from app.services." not in Path("app/api/routes/v5_resource_sync_status_routes.py").read_text()
    assert "Depends(get_db)" not in Path("app/api/routes/v5_resource_sync_status_routes.py").read_text()
    assert "router.include_router(resource_single_sync_router)" in workspace_collection_text
    assert "router.include_router(resource_access_router)" in workspace_collection_text
    assert "from app.services.v5_workspace import" not in workspace_collection_text
    assert "Depends(get_db)" not in workspace_collection_text
    assert "router.include_router(resource_clear_access_block_router)" in Path(
        "app/api/routes/v5_resource_access_routes.py"
    ).read_text()
    assert "router.include_router(resource_access_decision_router)" in Path(
        "app/api/routes/v5_resource_access_routes.py"
    ).read_text()
    assert "from app.services.v5_workspace import" not in Path("app/api/routes/v5_resource_access_routes.py").read_text()
    assert "Depends(get_db)" not in Path("app/api/routes/v5_resource_access_routes.py").read_text()
    assert "router.include_router(resource_direct_sync_router)" in single_sync_collection_text
    assert "router.include_router(resource_retry_router)" in single_sync_collection_text
    assert "from app.services.v5_workspace import" not in single_sync_collection_text
    assert "Depends(get_db)" not in single_sync_collection_text
    assert [token for token in forbidden_route_tokens if token in route_text] == []
    assert "list_v5_resources(" in route_text
    assert "list_resource_sync_runs(" in route_text
    assert "resource_company_overview_service(" in route_text
    assert "list_workspace_event_payloads(" in route_text
    assert "clear_v5_resource_access_block(" in route_text
    assert "retry_v5_resource_access_block(" in route_text
    assert "update_v5_resource_access_decision(" in route_text
    assert "preview_company_resource_batch_sync(" in route_text
    assert "sync_company_resource_batch(" in route_text
    assert "get_v5_resource_sync_policy_payload(" in route_text
    assert "update_v5_resource_sync_policy_payload(" in route_text
    assert "def list_v5_resources(" in service_text
    assert "def list_resource_sync_runs(" in service_text
    assert "def resource_sync_run_payload(" in service_text
    assert "def resource_company_overview(" in service_text
    assert "def list_workspace_event_payloads(" in service_text
    assert "def _company_resource_overview_item(" in service_text
    assert "def clear_v5_resource_access_block(" in workspace_text
    assert "def retry_v5_resource_access_block(" in workspace_text
    assert "def update_v5_resource_access_decision(" in workspace_text
    assert "def preview_company_resource_batch_sync(" in auto_sync_text
    assert "def sync_company_resource_batch(" in auto_sync_text


def test_v5_resource_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/v5_resource_routes.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert "router.include_router(resource_policy_router)" in collection_text
    assert "router.include_router(resource_status_router)" in collection_text
    assert "router.include_router(resource_sync_router)" in collection_text
    assert "router.include_router(resource_workspace_router)" in collection_text


def test_v5_resource_policy_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/v5_resource_policy_routes.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert "router.include_router(resource_policy_read_router)" in collection_text
    assert "router.include_router(resource_policy_write_router)" in collection_text


def test_v5_resource_overview_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/v5_resource_overview_routes.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert "router.include_router(resource_list_router)" in collection_text
    assert "router.include_router(resource_company_overview_router)" in collection_text
    assert "router.include_router(resource_sync_strategy_router)" in collection_text


def test_v5_resource_sync_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/v5_resource_sync_routes.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert "router.include_router(resource_batch_preview_router)" in collection_text
    assert "router.include_router(resource_batch_execute_router)" in collection_text


def test_v5_resource_sync_status_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/v5_resource_sync_status_routes.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert "router.include_router(resource_sync_status_list_router)" in collection_text
    assert "router.include_router(resource_sync_run_router)" in collection_text
    assert "router.include_router(resource_monitoring_router)" in collection_text


def test_v5_resource_status_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/v5_resource_status_routes.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert "router.include_router(resource_overview_router)" in collection_text
    assert "router.include_router(resource_sync_status_router)" in collection_text
    assert "router.include_router(workspace_event_router)" in collection_text


def test_v5_resource_workspace_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/v5_resource_workspace_routes.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert "router.include_router(resource_single_sync_router)" in collection_text
    assert "router.include_router(resource_access_router)" in collection_text


def test_v5_resource_single_sync_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/v5_resource_single_sync_routes.py").read_text()
    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "@router.put(",
        "@router.delete(",
        "from pydantic import",
        "BaseModel",
        "Field(",
        "Depends(get_db)",
        "from app.db.session import get_db",
        "from app.services.",
        "return ",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert "router.include_router(resource_direct_sync_router)" in collection_text
    assert "router.include_router(resource_retry_router)" in collection_text


def test_v5_intelligence_admin_logic_lives_outside_route_collection() -> None:
    collection_text = Path("app/api/routes/v5.py").read_text()
    route_collection_text = Path("app/api/routes/v5_intelligence_routes.py").read_text()
    route_text = "\n".join(
        [
            Path("app/api/routes/v5_intelligence_risk_noise_routes.py").read_text(),
            Path("app/api/routes/v5_intelligence_low_signal_routes.py").read_text(),
            Path("app/api/routes/v5_intelligence_business_item_routes.py").read_text(),
        ]
    )
    service_text = Path("app/services/v5_intelligence_admin.py").read_text()

    forbidden_route_tokens = (
        "close_finance_document_noise_risks(",
        "close_low_signal_extraction_noise(",
        "enrich_open_business_items(",
        "db.commit()",
        "min(max(limit, 1), 2000)",
    )

    assert "from app.api.routes.v5_intelligence_routes import router as intelligence_router" in collection_text
    assert "router.include_router(intelligence_router)" in collection_text
    assert "from app.services.v5_intelligence_admin import (" not in collection_text
    assert "router.include_router(intelligence_risk_noise_router)" in route_collection_text
    assert "router.include_router(intelligence_low_signal_router)" in route_collection_text
    assert "router.include_router(intelligence_business_item_router)" in route_collection_text
    assert "from app.services.v5_intelligence_admin import" not in route_collection_text
    assert "Depends(get_db)" not in route_collection_text
    assert "return " not in route_collection_text
    assert [token for token in forbidden_route_tokens if token in route_text] == []
    assert "from app.services.v5_intelligence_admin import close_finance_document_risk_noise_items" in route_text
    assert "from app.services.v5_intelligence_admin import close_low_signal_notice_noise_items" in route_text
    assert "from app.services.v5_intelligence_admin import enrich_open_business_extracted_items" in route_text
    assert "return close_finance_document_risk_noise_items(db, company_id=company_id, limit=limit)" in route_text
    assert "return close_low_signal_notice_noise_items(db, company_id=company_id, limit=limit)" in route_text
    assert "return enrich_open_business_extracted_items_service(db, company_id=company_id, limit=limit)" in route_text
    assert "close_finance_document_noise_risks(" in service_text
    assert "close_low_signal_extraction_noise(" in service_text
    assert "enrich_open_business_items(" in service_text
    assert "def _bounded_intelligence_limit(" in service_text
    assert "db.commit()" in service_text


def test_approval_card_responder_stays_platform_neutral() -> None:
    responder_text = Path("app/services/feishu/approval_card_responder.py").read_text()
    entrypoint_text = Path("app/services/feishu/approval_card_entrypoint.py").read_text()

    forbidden_responder_tokens = (
        "FeishuClient",
        "app.services.gateway.responder",
        "send_feishu_text_reply",
        "send_feishu_interactive_reply",
        "update_feishu_message_content",
        "execute_agent_tool",
        "ToolRequest",
        "ToolContext",
        "feishu_write_confirmation_token",
        "confirmation_token",
        ".api_post(",
        ".api_put(",
        ".api_patch(",
        ".api_delete(",
    )

    assert [token for token in forbidden_responder_tokens if token in responder_text] == []
    assert "send_text_reply" in responder_text
    assert "update_message_content" in responder_text
    assert "client_factory" in responder_text
    assert "from app.services.feishu.client import FeishuClient" in entrypoint_text
    assert "from app.services.tools.router import execute_agent_tool" in entrypoint_text
    assert "approval_card_responder.handle_card_action_response(" in entrypoint_text
    assert "approval_card_responder.handle_approval_card_action(" in entrypoint_text


def test_feishu_write_services_are_only_called_by_api_runtime() -> None:
    allowed_runtime = Path("app/services/feishu/api_runtime.py")
    write_service_calls = (
        ".execute_task_action(",
        ".execute_task_transfer(",
        ".execute_instance_remind(",
        ".execute_task_add_sign(",
        ".execute_task_rollback(",
        ".create_task(",
        ".complete_task(",
        ".reopen_task(",
        ".update_task(",
        ".add_comment(",
        ".create_record(",
        ".batch_create_records(",
        ".batch_update_records(",
        ".update_record(",
        ".delete_record(",
        ".assign_members(",
        ".update_followers(",
        ".create_tasklist(",
        ".add_to_tasklist(",
        ".send_text_message(",
        ".create_chat(",
        ".auto_join_public_chats(",
    )

    offenders: list[str] = []
    for path in Path("app").rglob("*.py"):
        if path == allowed_runtime:
            continue
        for line_no, line in enumerate(path.read_text().splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith(("#", "def ", "async def ")):
                continue
            matched = [call for call in write_service_calls if call in stripped]
            offenders.extend(f"{path}:{line_no}: {call}" for call in matched)

    assert offenders == []


def test_legacy_feishu_resource_list_uses_v5_resource_primary_model() -> None:
    text = Path("app/services/operations_resources.py").read_text()
    route_text = Path("app/api/routes/operations_resource_list_routes.py").read_text()
    registration_route_text = Path("app/api/routes/operations_resource_registration_routes.py").read_text()
    collection_text = Path("app/api/routes/operations.py").read_text()
    start = text.index('def list_v5_feishu_resources(')
    end = text.index('\ndef operations_feishu_resource_payload', start)
    function_text = text[start:end]

    assert "select(Resource)" in function_text
    assert "where(Resource.platform == \"feishu\")" in function_text
    assert "select(FeishuResource)" not in function_text
    assert "return list_v5_feishu_resources(db, company_id=company_id)" in route_text
    assert "register_v5_feishu_resource_from_request" not in route_text
    assert "list_v5_feishu_resources" not in registration_route_text
    assert "legacy_feishu_resource_id" not in route_text + registration_route_text + collection_text
    assert "select count(*) from feishu_resources" not in route_text + registration_route_text + collection_text
    assert "_legacy_resources_by_id" not in text


def test_feishu_resource_registration_uses_v5_resource_only() -> None:
    text = Path("app/services/operations_resources.py").read_text()
    route_text = Path("app/api/routes/operations_resource_registration_routes.py").read_text()
    list_route_text = Path("app/api/routes/operations_resource_list_routes.py").read_text()
    start = text.index('def register_v5_feishu_resource(')
    end = text.index('\ndef list_v5_feishu_resources', start)
    function_text = text[start:end]

    assert "return register_v5_feishu_resource_from_request(db, data)" in route_text
    assert "register_v5_feishu_resource_payload(" in text
    assert not Path("app/api/routes/operations_resource_routes.py").exists()
    assert "BaseModel" not in route_text
    assert "Field(" not in route_text
    assert "data." not in route_text
    assert "list_v5_feishu_resources" not in route_text
    assert "register_v5_feishu_resource_from_request" not in list_route_text
    assert "class FeishuResourceRegistrationRequest" in text
    assert "db.get(FeishuAppConfig" not in route_text
    assert "Feishu app_config_id belongs to another company" not in route_text
    assert "FeishuResource(" not in function_text
    assert "select(FeishuResource)" not in function_text
    assert "db.refresh(" not in function_text


def test_operations_route_collection_only_includes_subrouters() -> None:
    collection_text = Path("app/api/routes/operations.py").read_text()

    forbidden_tokens = (
        "@router.get(",
        "@router.post(",
        "Session = Depends(get_db)",
        "BaseModel",
        "Field(",
        "return ",
        "select(",
        "db.get(",
        "db.commit(",
        "__all__",
        "FeishuResourceRegistrationRequest",
        "MemoryFactCreate",
        "BotUserAccessCreate",
        "AdvisorChatRequest",
        "EntityCreate",
        "list_feishu_resources",
        "create_memory_fact",
        "upsert_bot_user",
        "dashboard_overview",
        "advisor_chat",
        "read_router",
        "bot_router",
        "bot_user_router",
        "memory_router",
        "memory_fact_router",
        "resource_router",
        "from app.api.routes.operations_status_routes",
        "router.include_router(status_router)",
        "from app.api.routes.operations_bot_user_routes",
        "router.include_router(bot_user_router)",
        "from app.api.routes.operations_memory_fact_routes",
        "router.include_router(memory_fact_router)",
    )

    assert [token for token in forbidden_tokens if token in collection_text] == []
    assert "router.include_router(dashboard_router)" in collection_text
    assert "router.include_router(advisor_router)" in collection_text
    assert "router.include_router(system_status_router)" in collection_text
    assert "router.include_router(automation_status_router)" in collection_text
    assert "router.include_router(sync_router)" in collection_text
    assert "router.include_router(extracted_router)" in collection_text
    assert "router.include_router(report_router)" in collection_text
    assert "router.include_router(audit_router)" in collection_text
    assert "router.include_router(bot_user_upsert_router)" in collection_text
    assert "router.include_router(bot_user_list_router)" in collection_text
    assert "router.include_router(bot_permission_router)" in collection_text
    assert "router.include_router(resource_registration_router)" in collection_text
    assert "router.include_router(resource_list_router)" in collection_text
    assert "router.include_router(memory_fact_create_router)" in collection_text
    assert "router.include_router(memory_fact_list_router)" in collection_text
    assert "router.include_router(memory_generation_router)" in collection_text
    assert "router.include_router(entity_router)" in collection_text


def test_retired_operations_route_modules_do_not_return() -> None:
    restored_modules = [
        str(Path("app/api/routes") / module)
        for module in RETIRED_OPERATIONS_ROUTE_MODULES
        if (Path("app/api/routes") / module).exists()
    ]

    assert restored_modules == []


def test_operations_subroutes_do_not_define_request_models_or_business_logic() -> None:
    forbidden_tokens = (
        "from pydantic import",
        "BaseModel",
        "Field(",
        "select(",
        "db.get(",
        "db.scalar(",
        "db.scalars(",
        "db.execute(",
        "db.add(",
        "db.commit(",
        "db.refresh(",
        "HTTPException",
        "json_safe",
        "data.",
        "MemoryFact(",
        "BotUserAccess(",
        "Person(",
        "Project(",
        "Customer(",
        "FeishuResource(",
    )

    offenders = []
    for path in Path("app/api/routes").glob("operations_*_routes.py"):
        text = path.read_text()
        offenders.extend(f"{path}:{token}" for token in forbidden_tokens if token in text)

    assert offenders == []


def test_operations_subroutes_are_single_endpoint_or_collection_modules() -> None:
    offenders: list[str] = []
    for path in Path("app/api/routes").glob("operations_*_routes.py"):
        text = path.read_text()
        route_decorators = re.findall(r"@router\.(?:get|post|put|delete)\(", text)
        include_count = text.count("router.include_router(")
        service_import = "from app.services." in text
        if route_decorators and len(route_decorators) != 1:
            offenders.append(f"{path}:route_count={len(route_decorators)}")
        if route_decorators and include_count:
            offenders.append(f"{path}:mixes_endpoint_and_collection")
        if not route_decorators and not include_count:
            offenders.append(f"{path}:empty_route_module")
        if not route_decorators and service_import:
            offenders.append(f"{path}:collection_imports_service")

    assert offenders == []


def _operations_subroute_endpoint_specs() -> set[tuple[str, str]]:
    expected_routes: set[tuple[str, str]] = set()
    for path in Path("app/api/routes").glob("operations_*_routes.py"):
        text = path.read_text()
        for method, route_path in re.findall(r'@router\.(get|post|put|delete)\("([^"]+)"', text):
            expected_routes.add((f"/api{route_path}", method.upper()))
    return expected_routes


def test_operations_subroute_endpoints_are_registered_by_collection_router() -> None:
    from app.main import app

    main_text = Path("app/main.py").read_text()
    expected_routes = _operations_subroute_endpoint_specs()
    registered_routes = {
        (route.path, method)
        for route in app.routes
        for method in getattr(route, "methods", set())
        if str(route.path).startswith("/api/")
    }

    assert "app.include_router(operations.router)" in main_text
    assert expected_routes
    assert sorted(expected_routes - registered_routes) == []


def test_operations_subroute_endpoints_keep_admin_api_dependency() -> None:
    from app.main import app

    expected_routes = _operations_subroute_endpoint_specs()
    routes_by_spec = {
        (route.path, method): route
        for route in app.routes
        for method in getattr(route, "methods", set())
        if (route.path, method) in expected_routes
    }
    missing_admin_dependency = []
    for route_spec in expected_routes:
        route = routes_by_spec.get(route_spec)
        dependency_names = {
            getattr(dependency.call, "__name__", "")
            for dependency in getattr(getattr(route, "dependant", None), "dependencies", [])
        }
        if "require_admin_api_token" not in dependency_names:
            missing_admin_dependency.append(route_spec)

    assert sorted(missing_admin_dependency) == []


def test_operations_memory_logic_lives_outside_route_collection() -> None:
    fact_create_route_text = Path("app/api/routes/operations_memory_fact_create_routes.py").read_text()
    fact_list_route_text = Path("app/api/routes/operations_memory_fact_list_routes.py").read_text()
    generation_route_text = Path("app/api/routes/operations_memory_generation_routes.py").read_text()
    service_text = Path("app/services/operations_memory.py").read_text()

    forbidden_route_tokens = (
        "BaseModel",
        "Field(",
        "class MemoryFactCreate",
        "class GenerateMemoryRequest",
        "MemoryFact(",
        "select(MemoryFact)",
        "generate_recent_memory_task",
        "payload=json_safe(data.payload)",
    )

    assert not Path("app/api/routes/operations_memory_routes.py").exists()
    assert not Path("app/api/routes/operations_memory_fact_routes.py").exists()
    all_route_text = fact_create_route_text + fact_list_route_text + generation_route_text
    assert [token for token in forbidden_route_tokens if token in all_route_text] == []
    assert "from app.services.operations_memory import MemoryFactCreate" in fact_create_route_text
    assert "return create_memory_fact_from_request(db, data)" in fact_create_route_text
    assert "list_memory_facts_service" not in fact_create_route_text
    assert "GenerateMemoryRequest" not in fact_create_route_text
    assert "enqueue_recent_memory_generation_from_request" not in fact_create_route_text
    assert "from app.services.operations_memory import list_memory_facts as list_memory_facts_service" in fact_list_route_text
    assert "return list_memory_facts_service(" in fact_list_route_text
    assert "MemoryFactCreate" not in fact_list_route_text
    assert "GenerateMemoryRequest" not in fact_list_route_text
    assert "create_memory_fact_from_request" not in fact_list_route_text
    assert "enqueue_recent_memory_generation_from_request" not in fact_list_route_text
    assert "from app.services.operations_memory import GenerateMemoryRequest" in generation_route_text
    assert "return enqueue_recent_memory_generation_from_request(data)" in generation_route_text
    assert "MemoryFactCreate" not in generation_route_text
    assert "list_memory_facts_service" not in generation_route_text
    assert "class MemoryFactCreate" in service_text
    assert "class GenerateMemoryRequest" in service_text
    assert "def create_memory_fact_from_request(" in service_text
    assert "def enqueue_recent_memory_generation_from_request(" in service_text
    assert "MemoryFact(" in service_text
    assert "select(MemoryFact)" in service_text
    assert "generate_recent_memory_task" in service_text


def test_operations_bot_user_logic_lives_outside_route_collection() -> None:
    user_upsert_route_text = Path("app/api/routes/operations_bot_user_upsert_routes.py").read_text()
    user_list_route_text = Path("app/api/routes/operations_bot_user_list_routes.py").read_text()
    permission_route_text = Path("app/api/routes/operations_bot_permission_routes.py").read_text()
    permission_rule_route_text = Path("app/api/routes/operations_bot_permission_rule_routes.py").read_text()
    permission_recalculation_route_text = Path(
        "app/api/routes/operations_bot_permission_recalculation_routes.py"
    ).read_text()
    service_text = Path("app/services/operations_bot_users.py").read_text()

    forbidden_route_tokens = (
        "BaseModel",
        "Field(",
        "class BotUserAccessCreate",
        "class RecalculateBotPermissionsRequest",
        "BotUserAccess(",
        "select(BotUserAccess)",
        "DOMAIN_RULES",
        "infer_permission_profile(",
        "merge_permission_settings(",
        "feishu_bot_admin_open_ids",
        "def _env_bot_admin_items(",
        "def _department_names_by_id(",
        "def _permission_preview(",
    )

    assert not Path("app/api/routes/operations_bot_routes.py").exists()
    assert not Path("app/api/routes/operations_bot_user_routes.py").exists()
    all_route_text = (
        user_upsert_route_text
        + user_list_route_text
        + permission_route_text
        + permission_rule_route_text
        + permission_recalculation_route_text
    )
    assert [token for token in forbidden_route_tokens if token in all_route_text] == []
    assert "from app.services.operations_bot_users import BotUserAccessCreate" in user_upsert_route_text
    assert "return upsert_bot_user_access_from_request(db, data)" in user_upsert_route_text
    assert "list_bot_user_access" not in user_upsert_route_text
    assert "bot_permission_rules_payload" not in user_upsert_route_text
    assert "recalculate_bot_user_permissions_from_request" not in user_upsert_route_text
    assert "from app.services.operations_bot_users import list_bot_user_access" in user_list_route_text
    assert "return list_bot_user_access(db, company_id=company_id)" in user_list_route_text
    assert "BotUserAccessCreate" not in user_list_route_text
    assert "upsert_bot_user_access_from_request" not in user_list_route_text
    assert "bot_permission_rules_payload" not in user_list_route_text
    assert "recalculate_bot_user_permissions_from_request" not in user_list_route_text
    assert "router.include_router(bot_permission_rule_router)" in permission_route_text
    assert "router.include_router(bot_permission_recalculation_router)" in permission_route_text
    assert "from app.services.operations_bot_users import" not in permission_route_text
    assert "return " not in permission_route_text
    assert "BotUserAccessCreate" not in permission_route_text
    assert "RecalculateBotPermissionsRequest" not in permission_route_text
    assert "list_bot_user_access" not in permission_route_text
    assert "from app.services.operations_bot_users import bot_permission_rules_payload" in permission_rule_route_text
    assert "return bot_permission_rules_payload()" in permission_rule_route_text
    assert "RecalculateBotPermissionsRequest" not in permission_rule_route_text
    assert "recalculate_bot_user_permissions_from_request" not in permission_rule_route_text
    assert "from app.services.operations_bot_users import (" in permission_recalculation_route_text
    assert "return recalculate_bot_user_permissions_from_request(db, data)" in permission_recalculation_route_text
    assert "bot_permission_rules_payload" not in permission_recalculation_route_text
    assert "list_bot_user_access" not in permission_route_text
    assert "class BotUserAccessCreate" in service_text
    assert "class RecalculateBotPermissionsRequest" in service_text
    assert "def upsert_bot_user_access_from_request(" in service_text
    assert "def recalculate_bot_user_permissions_from_request(" in service_text
    assert "BotUserAccess(" in service_text
    assert "select(BotUserAccess)" in service_text
    assert "DOMAIN_RULES" in service_text
    assert "infer_permission_profile(" in service_text
    assert "merge_permission_settings(" in service_text
    assert "def _env_bot_admin_items(" in service_text
    assert "def _department_names_by_id(" in service_text
    assert "def _permission_preview(" in service_text


def test_operations_read_model_lists_live_outside_route_collection() -> None:
    service_text = Path("app/services/operations_read_models.py").read_text()
    route_paths = {
        "sync": Path("app/api/routes/operations_sync_routes.py"),
        "extracted": Path("app/api/routes/operations_extracted_routes.py"),
        "report": Path("app/api/routes/operations_report_routes.py"),
        "audit": Path("app/api/routes/operations_audit_routes.py"),
    }

    forbidden_route_tokens = (
        "select(SyncRun)",
        "select(ExtractedItem)",
        "select(Report)",
        "select(AuditLog)",
        "_display_extracted_title(",
        "content_markdown",
        "cursor",
        "target_type",
    )

    assert not Path("app/api/routes/operations_read_routes.py").exists()
    route_texts = {name: path.read_text() for name, path in route_paths.items()}
    offenders = [
        f"{name}:{token}"
        for name, text in route_texts.items()
        for token in forbidden_route_tokens
        if token in text
    ]
    assert offenders == []
    assert "from app.services.operations_read_models import list_sync_run_payloads" in route_texts["sync"]
    assert "return list_sync_run_payloads(" in route_texts["sync"]
    assert "from app.services.operations_read_models import list_extracted_item_payloads" in route_texts["extracted"]
    assert "return list_extracted_item_payloads(" in route_texts["extracted"]
    assert "from app.services.operations_read_models import list_report_payloads" in route_texts["report"]
    assert "return list_report_payloads(" in route_texts["report"]
    assert "from app.services.operations_read_models import list_audit_log_payloads" in route_texts["audit"]
    assert "return list_audit_log_payloads(" in route_texts["audit"]
    assert "select(SyncRun)" in service_text
    assert "select(ExtractedItem)" in service_text
    assert "select(Report)" in service_text
    assert "select(AuditLog)" in service_text
    assert "def display_extracted_title(" in service_text


def test_operations_dashboard_lives_outside_route_collection() -> None:
    route_text = Path("app/api/routes/operations_dashboard_routes.py").read_text()
    service_text = Path("app/services/operations_dashboard.py").read_text()
    dashboard_route_text = route_text[route_text.index('@router.get("/dashboard/overview")') :]

    forbidden_route_tokens = (
        "BaseModel",
        "Field(",
        "class AdvisorChatRequest",
        '@router.post("/advisor/chat")',
        '@router.get("/system/status")',
        '@router.get("/automation/status")',
        "answer_operations_advisor_chat",
        "system_status_payload",
        "automation_status_payload",
        "select(",
        "func.count()",
        "dashboard_resource_summary(",
        "data_counts(",
        "default_company_id(",
        "count_bot_user_access(",
        "system_status(",
        "list_sync_run_payloads(",
    )

    assert [token for token in forbidden_route_tokens if token in dashboard_route_text] == []
    assert "BaseModel" not in route_text
    assert "Field(" not in route_text
    assert "from app.services.operations_dashboard import dashboard_overview_payload" in route_text
    assert "return dashboard_overview_payload(db)" in route_text
    assert "def dashboard_overview_payload(" in service_text
    assert "dashboard_resource_summary(" in service_text
    assert "list_sync_run_payloads(" in service_text


def test_operations_automation_status_lives_outside_route_collection() -> None:
    route_text = Path("app/api/routes/operations_automation_status_routes.py").read_text()
    system_route_text = Path("app/api/routes/operations_system_status_routes.py").read_text()
    service_text = Path("app/services/operations_status.py").read_text()
    automation_route_text = route_text[route_text.index('@router.get("/automation/status")') :]

    forbidden_route_tokens = (
        "auto_feishu_sync_enabled",
        "auto_imap_sync_enabled",
        "auto_daily_report_enabled",
        "feishu_ws_enabled",
    )

    assert not Path("app/api/routes/operations_status_routes.py").exists()
    assert [token for token in forbidden_route_tokens if token in automation_route_text] == []
    assert "from app.services.operations_status import automation_status_payload" in route_text
    assert "return automation_status_payload()" in route_text
    assert "system_status_payload" not in route_text
    assert "automation_status_payload" not in system_route_text
    assert "def automation_status_payload(" in service_text
    assert "auto_feishu_sync_enabled" in service_text


def test_operations_system_status_lives_outside_route_collection() -> None:
    route_text = Path("app/api/routes/operations_system_status_routes.py").read_text()
    automation_route_text = Path("app/api/routes/operations_automation_status_routes.py").read_text()
    service_text = Path("app/services/operations_status.py").read_text()

    forbidden_route_tokens = (
        "import httpx",
        "import redis",
        "def _database_status(",
        "def _redis_status(",
        "def _qdrant_status(",
        "def _data_counts(",
        "def _sync_status(",
        "def _default_company_id(",
        "Redis.from_url(",
        "qdrant_url",
    )

    assert [token for token in forbidden_route_tokens if token in route_text] == []
    assert "return system_status_payload(db)" in route_text
    assert "automation_status_payload" not in route_text
    assert "system_status_payload" not in automation_route_text
    assert "def system_status_payload(" in service_text
    assert "data_counts(db)" in service_text
    assert "def default_company_id(" in service_text
    assert "Redis.from_url(" in service_text
    assert "qdrant_url" in service_text


def test_operations_entity_creation_lives_outside_route_collection() -> None:
    route_text = Path("app/api/routes/operations_entity_routes.py").read_text()
    service_text = Path("app/services/operations_entities.py").read_text()
    entity_route_text = route_text[route_text.index('@router.post("/entities/{entity_type}")'):]

    forbidden_route_tokens = (
        "BaseModel",
        "Field(",
        "class EntityCreate",
        "Person(",
        "Project(",
        "Customer(",
        "json_safe(data.payload)",
        "entity_type must be people, projects, or customers",
        "db.commit()",
        "db.refresh(",
    )

    assert [token for token in forbidden_route_tokens if token in entity_route_text] == []
    assert "BaseModel" not in route_text
    assert "Field(" not in route_text
    assert "from app.services.operations_entities import" in route_text
    assert "EntityCreate" in route_text
    assert "create_operations_entity_from_request" in route_text
    assert "return create_operations_entity_from_request(db, entity_type=entity_type, data=data)" in route_text
    assert "class EntityCreate" in service_text
    assert "def create_operations_entity_from_request(" in service_text
    assert "Person(" in service_text
    assert "Project(" in service_text
    assert "Customer(" in service_text
    assert "entity_type must be people, projects, or customers" in service_text


def test_operations_advisor_chat_lives_outside_route_collection() -> None:
    dashboard_route_text = Path("app/api/routes/operations_dashboard_routes.py").read_text()
    route_text = Path("app/api/routes/operations_advisor_routes.py").read_text()
    service_text = Path("app/services/operations_advisor.py").read_text()
    advisor_route_text = route_text[route_text.index('@router.post("/advisor/chat")') :]

    forbidden_route_tokens = (
        "BaseModel",
        "Field(",
        "class AdvisorChatRequest",
        "answer_advisor_question(",
        "db.get(Company",
        "Invalid company_id",
        "actor_domains=",
    )

    assert [token for token in forbidden_route_tokens if token in advisor_route_text] == []
    assert "AdvisorChatRequest" not in dashboard_route_text
    assert "answer_operations_advisor_chat" not in dashboard_route_text
    assert "from app.services.operations_advisor import" in route_text
    assert "AdvisorChatRequest" in route_text
    assert "answer_operations_advisor_chat_from_request" in route_text
    assert "return answer_operations_advisor_chat_from_request(db, data)" in route_text
    assert "class AdvisorChatRequest" in service_text
    assert "def answer_operations_advisor_chat_from_request(" in service_text
    assert "answer_advisor_question(" in service_text
    assert "db.get(Company" in service_text
    assert "Invalid company_id" in service_text


def test_resource_discovery_does_not_create_legacy_feishu_resources() -> None:
    text = Path("app/services/feishu/resources.py").read_text()
    start = text.index('async def discover_feishu_resources(')
    end = text.index('\ndef _discovered_resource_payload', start)
    function_text = text[start:end]

    assert "upsert_feishu_discovered_resource(" in function_text
    assert "upsert_feishu_resource(" not in function_text


def test_quick_company_setup_mail_resource_uses_v5_resource_only() -> None:
    text = Path("app/services/companies_admin.py").read_text()
    start = text.index('def upsert_quick_setup_mail_resource(')
    end = text.index('\ndef quick_setup_mail_resource_payload', start)
    function_text = text[start:end]

    assert "upsert_v5_mail_resource(" in function_text
    assert "FeishuResource(" not in function_text
    assert "select(FeishuResource)" not in function_text
    assert "legacy_feishu_resource_id" not in function_text
    assert "legacy_resource" not in text
    assert '"legacy_id"' not in text
    assert "FeishuResource" not in text
    assert "_upsert_mail_resource" not in Path("app/api/routes/companies.py").read_text()
    assert "_upsert_v5_mail_resource" not in Path("app/api/routes/companies.py").read_text()


def test_legacy_feishu_resource_usage_is_limited_to_migration_fallbacks() -> None:
    entities_text = Path("app/models/entities.py").read_text()
    legacy_model_allowed = {
        Path("app/services/resource_registry.py"),
        Path("app/services/v5_administration.py"),
    }
    legacy_id_field_allowed = {
        *legacy_model_allowed,
        Path("app/models/entities.py"),
        Path("app/services/operations_resources.py"),
        Path("app/services/resource_registry.py"),
        Path("app/services/v5_resources.py"),
    }

    offenders: list[str] = []
    for path in Path("app").rglob("*.py"):
        text = path.read_text()
        uses_legacy_model = any(
            token in text
            for token in (
                " FeishuResource,",
                " FeishuResource)",
                "FeishuResource |",
                "select(FeishuResource)",
                "FeishuResource.",
            )
        )
        uses_legacy_id_field = "legacy_feishu_resource_id" in text
        if uses_legacy_model and path not in legacy_model_allowed:
            offenders.append(f"{path}:FeishuResource")
        if uses_legacy_id_field and path not in legacy_id_field_allowed:
            offenders.append(f"{path}:legacy_feishu_resource_id")

    assert offenders == []
    assert "class FeishuResource" not in entities_text
    assert 'ForeignKey("feishu_resources.id")' not in entities_text
    assert "legacy_feishu_resource:" not in entities_text


def test_public_chat_auto_join_does_not_register_resources() -> None:
    route_text = Path("app/api/routes/feishu_admin_write_im_auto_join_routes.py").read_text()
    route_start = route_text.index('async def auto_join_public_chats(')
    route_function = route_text[route_start:]

    admin_write_text = Path("app/services/feishu_admin_write_tools.py").read_text()
    admin_write_start = admin_write_text.index('async def auto_join_public_chats_payload(')
    admin_write_end = admin_write_text.index('\ndef unique_strings', admin_write_start)
    admin_write_function = admin_write_text[admin_write_start:admin_write_end]

    runtime_text = Path("app/services/feishu/api_runtime.py").read_text()
    runtime_start = runtime_text.index('def _execute_im_auto_join_public_chats(')
    runtime_end = runtime_text.index('\ndef _execute_task_read', runtime_start)
    runtime_function = runtime_text[runtime_start:runtime_end]

    service_text = Path("app/services/feishu/im.py").read_text()
    service_start = service_text.index('    async def auto_join_public_chats(')
    service_end = service_text.index('\ndef extract_chat_items', service_start)
    service_function = service_text[service_start:service_end]

    combined = "\n".join([route_function, admin_write_function, runtime_function, service_function])
    forbidden = (
        "FeishuResource",
        "Resource(",
        "upsert_resource(",
        "upsert_feishu_discovered_resource(",
        "legacy_feishu_resource_id",
    )

    assert [token for token in forbidden if token in combined] == []


def test_approval_card_responder_is_hidden_behind_entrypoint() -> None:
    command_text = Path("app/services/feishu/commands.py").read_text()
    command_handler_text = Path("app/services/feishu/command_handlers.py").read_text()
    entrypoint_text = Path("app/services/feishu/approval_card_entrypoint.py").read_text()
    responder_text = Path("app/services/feishu/approval_card_responder.py").read_text()
    cards_text = Path("app/services/feishu/approval_cards.py").read_text()
    worker_text = Path("app/workers/feishu_ws.py").read_text()

    assert "approval_card_responder" not in command_text
    assert "approval_card_entrypoint" in command_text
    assert "_build_approval_action_card" not in command_text
    assert "_maybe_send_approval_action_card" not in command_text
    assert "send_feishu_interactive_reply" not in command_text
    assert "FeishuClient" not in command_text
    assert "app.services.gateway.responder" not in command_text
    assert "from app.services.feishu import replies as feishu_replies" in command_text
    assert "command_handlers.default_command_dispatch_handlers()" in command_text
    assert "generate_daily_report" not in command_text
    assert "bot_runtime" not in command_text
    assert "sync_commands" not in command_text
    assert "from app.services.feishu import organization" not in command_text
    assert "work_event_replies" not in command_text
    assert "default_command_dispatch_handlers(" in command_handler_text
    assert "generate_daily_report" in command_handler_text
    assert "bot_runtime.record_command_context" in command_handler_text
    assert "build_feishu_approval_action_card" in entrypoint_text
    assert "send_feishu_approval_action_card" in entrypoint_text
    assert "approval_card_responder.handle_card_action_response(" in entrypoint_text
    assert "approval_card_responder.handle_approval_card_action(" in entrypoint_text
    assert "dispatch_gateway_card_action_response(" in entrypoint_text
    assert "dispatch_gateway_card_action_message(" in entrypoint_text
    assert "handle_feishu_gateway_card_action_message(" in command_text
    assert "is_feishu_approval_card_action(" not in command_text
    assert "gateway_card_action_value" in cards_text
    assert "gateway_card_action_message_id" in cards_text
    assert "json.loads" not in cards_text
    assert "app.services.feishu.commands" not in entrypoint_text
    assert re.search(r"(?<![A-Za-z0-9])_approval_detail_reply\b", entrypoint_text) is None
    assert re.search(r"(?<![A-Za-z0-9])_load_approval_context\b", entrypoint_text) is None
    assert re.search(r"(?<![A-Za-z0-9])_prepare_approval_action_reply\b", entrypoint_text) is None
    assert "_get_sender_identity" not in entrypoint_text
    assert "_permission_denied_reply" not in entrypoint_text
    assert "_get_chat_id" not in entrypoint_text
    assert "_short_approval_text" not in entrypoint_text
    assert "feishu_identity.get_sender_identity" in entrypoint_text
    assert "feishu_identity.permission_denied_reply" in entrypoint_text
    assert "command_parser.get_chat_id" in entrypoint_text
    assert "approval_formatters.short_approval_text" in entrypoint_text
    assert "load_feishu_card_approval_context" in entrypoint_text
    assert "feishu_card_approval_detail_reply" in entrypoint_text
    assert "prepare_feishu_card_approval_action_reply" in entrypoint_text
    assert "feishu_approval_advice_reply" in entrypoint_text
    assert "feishu_approval_detail_reply" in entrypoint_text
    assert "prepare_feishu_approval_action_reply" in entrypoint_text
    assert "execute_feishu_pending_approval_action_reply" in entrypoint_text
    assert "clear_feishu_pending_approval_action" in entrypoint_text
    assert "approval_runtime.approval_detail_reply" in entrypoint_text
    assert "approval_actions.prepare_approval_action_reply" in entrypoint_text
    assert "approval_actions.execute_pending_approval_action_reply" in entrypoint_text
    assert "approval_card_entrypoint.feishu_approval_advice_reply(" not in command_text
    assert "approval_card_entrypoint.feishu_approval_detail_reply(" not in command_text
    assert "approval_card_entrypoint.prepare_feishu_approval_action_reply(" not in command_text
    assert "approval_card_entrypoint.execute_feishu_pending_approval_action_reply(" not in command_text
    assert "approval_card_entrypoint.clear_feishu_pending_approval_action(" not in command_text
    assert "approval_card_entrypoint.feishu_approval_advice_reply(" not in command_handler_text
    assert "approval_card_entrypoint.feishu_approval_detail_reply(" not in command_handler_text
    assert "approval_card_entrypoint.prepare_feishu_approval_action_reply(" in command_handler_text
    assert "approval_card_entrypoint.execute_feishu_pending_approval_action_reply(" in command_handler_text
    assert "approval_card_entrypoint.clear_feishu_pending_approval_action(" in command_handler_text
    assert "execute_agent_tool" not in command_text
    assert "ToolRequest" not in command_text
    assert "ToolContext" not in command_text
    assert "feishu_write_confirmation_token" not in command_text
    assert "from app.services.feishu import approval_runtime" not in command_text
    assert "from app.services.feishu import approval_actions" not in command_text
    assert "approval_runtime.approval_advice_reply(" not in command_text
    assert "approval_runtime.approval_detail_reply(" not in command_text
    assert "approval_actions.prepare_approval_action_reply(" not in command_text
    assert "approval_actions.execute_pending_approval_action_reply(" not in command_text
    assert "load_pending_approval_action" not in command_text
    assert "store_pending_approval_action" not in command_text
    assert "FeishuApprovalService" not in entrypoint_text
    assert "FeishuApprovalAttachmentService" not in entrypoint_text
    assert "feishu_approval_attachment_download" in entrypoint_text
    assert "_execute_feishu_approval_action" in entrypoint_text
    assert "_record_feishu_approval_action_audit" in entrypoint_text
    assert "app.services.gateway.responder" not in responder_text
    assert "send_text_reply=" in entrypoint_text
    assert "update_message_content=" in entrypoint_text
    assert "handle_feishu_gateway_card_action_response" in worker_text


def test_recent_approval_reply_orchestration_is_not_command_handler_direct_reply() -> None:
    command_text = Path("app/services/feishu/commands.py").read_text()
    command_handler_text = Path("app/services/feishu/command_handlers.py").read_text()
    entrypoint_text = Path("app/services/feishu/approval_card_entrypoint.py").read_text()
    runtime_text = Path("app/services/feishu/approval_runtime.py").read_text()
    task_text = Path("app/tasks/celery_app.py").read_text()

    assert "async def recent_approvals_reply(" in runtime_text
    assert "async def recent_feishu_approvals_reply(" in entrypoint_text
    assert "async def feishu_approval_items_from_context_or_live(" in entrypoint_text
    assert "approval_runtime.recent_approvals_reply(" in entrypoint_text
    assert "approval_runtime.approval_items_from_context_or_live(" in entrypoint_text
    assert "async def recent_approvals_reply(" not in command_text
    assert "async def recent_approvals_reply(" not in command_handler_text
    assert "approval_card_entrypoint.recent_feishu_approvals_reply(" not in command_handler_text
    assert "fetch_pending_tasks=" not in command_handler_text
    assert "register_resources=" not in command_handler_text
    assert "attach_synced_attachments=" not in command_handler_text
    assert "store_context=" not in command_handler_text
    assert "approval_runtime.approval_items_from_context_or_live(" not in command_text
    assert "async def _approval_items_from_context_or_live(" not in command_text
    assert "async def _fetch_pending_approval_tasks(" not in command_text
    assert "async def _ensure_pending_approval_attachment_summaries(" not in command_text
    assert "def _register_pending_approval_resources(" not in command_text
    assert "def _attach_approval_history_context(" not in command_text
    assert "bot.approvals.recent_reply" not in task_text
    assert "recent_feishu_approvals_reply(" not in task_text
    assert "load_feishu_card_approval_context(" not in task_text
    assert "feishu_identity.identity_from_payload(" not in task_text
    assert "app.services.feishu.commands" not in task_text
    assert "_recent_approvals_reply(" not in task_text
    assert "_load_approval_context(" not in task_text
    assert "_identity_from_payload(" not in task_text
    assert "pending_tasks = await" not in command_handler_text
    assert "pending_events = " not in command_handler_text
    assert "recent_approval_events(" not in command_handler_text


def test_legacy_approval_service_runtime_helpers_are_retired() -> None:
    assert not Path("app/services/feishu/approval_service_runtime.py").exists()
