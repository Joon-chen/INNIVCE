#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v lark-cli >/dev/null 2>&1; then
  echo "ERROR: lark-cli not found in PATH. Feishu realtime execution layer is required for V5 launch." >&2
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  echo "ERROR: node not found in PATH. Node is required to validate the admin console script." >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker not found in PATH. Docker Compose is required for V5 launch rehearsal." >&2
  exit 1
fi

if [[ ! -x .venv312/bin/pytest || ! -x .venv312/bin/ruff ]]; then
  echo "ERROR: .venv312 is missing pytest or ruff. Restore the project virtualenv before release check." >&2
  exit 1
fi

docker compose config --quiet
docker compose -f docker-compose.local-prod.yml config --quiet
bash -n scripts/local_prod_check_v5.sh
bash -n scripts/prepare_lark_cli_home_v5.sh
.venv312/bin/python -m py_compile scripts/bootstrap_feishu_app_config_v5.py
for sensitive_path in .local/lark-cli-v5/config.json tmp/v5-lark-auth-qrcode.png app_secret.txt; do
  git check-ignore -q "$sensitive_path"
done

lark-cli --version
lark-cli doctor --offline

.venv312/bin/pytest -q \
  tests/test_health.py \
  tests/test_tools_devops.py \
  tests/test_data_layer.py::test_legacy_feishu_resource_retirement_status_handles_retired_table \
  tests/test_gateway_feishu.py::test_feishu_event_route_delegates_to_entrypoint \
  tests/test_gateway_feishu.py::test_receive_feishu_event_payload_returns_challenge_without_ingest_or_command \
  tests/test_gateway_feishu.py::test_receive_feishu_event_payload_ingests_before_command_handler \
  tests/test_gateway_feishu.py::test_handle_feishu_command_routes_natural_language_to_ai_fallback \
  tests/test_gateway_feishu.py::test_handle_feishu_command_result_exposes_gateway_contract \
  tests/test_gateway_feishu.py::test_thinking_notice_only_for_thinking_reply_mode \
  tests/test_gateway_feishu.py::test_handle_feishu_command_sends_authorization_card_for_user_identity_actions \
  tests/test_gateway_feishu.py::test_agent_runtime_trace_gateway_summary_excludes_answer_text \
  tests/test_authorization_cards.py \
  tests/test_feishu_bot_runtime.py::test_employee_bot_answer_result_exposes_trace_payload \
  tests/test_feishu_identity.py::test_sender_identity_defaults_to_employee_personal_agent \
  tests/test_command_dispatcher.py::test_dispatch_ai_mode_uses_agent_runtime_trace_when_available \
  tests/test_feishu_ws.py::test_command_result_runs_inside_existing_event_loop \
  tests/test_feishu_ws.py::test_command_bool_wrapper_uses_structured_result \
  tests/test_v5.py::test_system_logs_overview_groups_audit_payloads \
  tests/test_v5.py::test_list_system_logs_filters_gateway_agent_runtime_route \
  tests/test_v5.py::test_list_system_logs_route_passes_agent_runtime_filters \
  tests/test_v5.py::test_v5_router_exposes_foundation_endpoints \
  tests/test_cockpit.py::test_console_system_logs_can_filter_gateway_reason \
  tests/test_cockpit.py::test_console_owner_center_matches_v5_xmind_structure \
  tests/test_cockpit.py::test_console_company_space_reuses_v5_data_apis \
  tests/test_cockpit.py::test_console_feishu_oauth_autofills_default_app_config \
  tests/test_cockpit.py::test_console_item_type_labels_are_domain_specific \
  tests/test_feishu.py::test_dedupe_discovered_uses_v5_resource_identity_not_source \
  tests/test_feishu.py::test_local_chat_resource_discovery_links_existing_work_events \
  tests/test_feishu.py::test_non_local_resource_discovery_does_not_link_work_events \
  tests/test_tool_router.py::test_execute_agent_tool_injects_only_trusted_cli_profile_for_feishu_mcp \
  tests/test_tool_router.py::test_execute_agent_tool_marks_general_chat_as_fast_no_enterprise_data \
  tests/test_tool_router.py::test_execute_agent_tool_marks_personal_task_boundary \
  tests/test_tool_router.py::test_execute_agent_tool_requires_user_identity_authorization_for_mail \
  tests/test_tool_router.py::test_execute_agent_tool_allows_mail_with_resource_owner_authorization \
  tests/test_user_identity_authorizations.py \
  tests/test_tools_personal.py::test_personal_task_access_boundary_requires_strong_identity \
  tests/test_agent_reply_modes.py \
  tests/test_v5.py::test_agent_reply_modes_endpoint_exposes_runtime_boundary \
  tests/test_v5.py::test_bot_user_access_payload_exposes_employee_agent_profile \
  tests/test_v5.py::test_bot_user_access_payload_exposes_user_identity_authorizations \
  tests/test_v5.py::test_agent_trace_preview_returns_trace_and_writes_audit \
  tests/test_v5.py::test_list_agent_traces_returns_audit_summaries \
  tests/test_agent_policies.py::test_answer_scope_for_actor_uses_company_domain_or_chat \
  tests/test_agent_runtime.py::test_agent_runtime_trace_records_tool_route \
  tests/test_agent_runtime.py::test_agent_runtime_trace_marks_personal_actor_boundary \
  tests/test_agent_runtime.py::test_agent_runtime_turns_user_identity_denial_into_authorization_guide \
  tests/test_feishu_provider_boundary.py::test_feishu_mcp_provider_injects_cli_profile_into_lark_cli_args \
  tests/test_v5.py::test_v5_os_overview_exposes_interaction_entrypoints \
  tests/test_v5.py::test_v5_os_overview_blocks_release_when_feishu_bot_ai_fallback_is_disabled \
  tests/test_v5.py::test_v5_os_overview_blocks_release_when_feishu_cli_is_missing \
  tests/test_v5.py::test_v5_os_overview_blocks_release_when_feishu_app_config_is_missing \
  tests/test_v5.py::test_v5_os_overview_blocks_release_when_feishu_cli_identity_is_unavailable \
  tests/test_v5.py::test_v5_os_overview_blocks_release_when_company_cli_profile_is_unavailable \
  tests/test_v5.py::test_v5_os_overview_keeps_entrypoints_when_database_is_unavailable \
  tests/test_v5.py::test_v5_entrypoint_status_exposes_bot_console_and_ios_boundaries \
  tests/test_work_events.py::test_upsert_work_event_binds_feishu_chat_resource_from_thread \
  tests/test_v5_foundation.py::test_resource_identity_indexes_handle_nullable_sub_ids \
  tests/test_v5_architecture.py::test_feishu_event_entrypoint_lives_outside_route_collection \
  tests/test_v5_architecture.py::test_feishu_cli_devops_health_check_covers_v5_tool_scope \
  tests/test_v5_architecture.py::test_local_prod_image_installs_feishu_cli_execution_layer \
  tests/test_v5_architecture.py::test_business_tool_families_match_xmind_v5 \
  tests/test_v5_architecture.py::test_sync_engine_does_not_call_tool_router_or_feishu_providers \
  tests/test_tools_config.py::test_list_tool_configurations_uses_registry_defaults \
  tests/test_tools_config.py::test_provider_boundaries_explain_feishu_api_and_mcp_contracts

node --check app/static/console/app.js

.venv312/bin/ruff check \
  app/services/v5_administration.py \
  app/services/work_events.py \
  app/services/tools/providers/devops.py \
  app/services/tools/providers/feishu_mcp.py \
  app/services/tools/config.py \
  app/services/tools/router.py \
  app/services/tools/personal.py \
  app/services/tools/base.py \
  app/services/user_identity_authorizations.py \
  app/services/agent/reply_modes.py \
  app/services/agent/runtime.py \
  app/services/v5_agent_admin.py \
  app/api/routes/v5_agent_reply_mode_routes.py \
  app/api/routes/v5_agent_trace_routes.py \
  app/services/operations_resources.py \
  app/services/feishu/cli_profile.py \
  app/services/feishu/identity.py \
  app/services/feishu/bot_runtime.py \
  app/services/feishu/commands.py \
  app/services/feishu/command_dispatcher.py \
  app/services/feishu/resources.py \
  app/services/feishu/approval_card_entrypoint.py \
  app/services/gateway/audit.py \
  app/services/system_logs.py \
  app/workers/feishu_ws.py \
  app/services/feishu_admin_read_tools.py \
  app/services/feishu_admin_write_tools.py \
  app/services/feishu_event_entrypoint.py \
  app/api/routes/feishu_event_routes.py \
  app/api/routes/user_identity_oauth_routes.py \
  app/static \
  alembic/versions/0020_resource_identity_partial_unique_indexes.py \
  scripts/bootstrap_feishu_app_config_v5.py \
  tests/test_v5.py \
  tests/test_work_events.py \
  tests/test_v5_foundation.py \
  tests/test_tools_devops.py \
  tests/test_data_layer.py \
  tests/test_feishu_bot_runtime.py \
  tests/test_feishu_identity.py \
  tests/test_command_dispatcher.py \
  tests/test_gateway_feishu.py \
  tests/test_feishu_ws.py \
  tests/test_tool_router.py \
  tests/test_user_identity_authorizations.py \
  tests/test_agent_reply_modes.py \
  tests/test_agent_policies.py \
  tests/test_agent_runtime.py \
  tests/test_feishu_provider_boundary.py \
  tests/test_cockpit.py \
  tests/test_feishu.py \
  tests/test_v5_architecture.py \
  tests/test_tools_config.py
