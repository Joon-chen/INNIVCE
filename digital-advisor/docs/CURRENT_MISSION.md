# Current Mission

本文档只回答：现在在做什么。

系统最高级规则见 `docs/V5_RUNTIME_CONSTITUTION.md`。

## 当前阶段

```text
Task Complete Authorization Retry Acceptance Phase In Progress
```

## 当前目标

Task Runtime Sample 已完成 `task_query` / `task_complete` 的最小闭环合同。

本阶段验证真实飞书写入缺少用户授权时，Bot 能否展示授权入口，并在授权后重试同一动作。

当前链路：

```text
Task Query
→ RuntimeResult.task_list
→ Feishu Task Card
→ RuntimeActionInput
→ Runtime
→ Waiting Confirmation / Waiting Authorization
→ Tool
→ RuntimeResult
```

已完成：

- `waiting_authorization` 保留为 Runtime Result Type。
- RuntimeResult 输出 `metadata.authorization` 与 `authorize_user_identity` action。
- Feishu Gateway 可发送现有 User Identity Authorization Card。
- `RuntimeResult.task_list` 可渲染为飞书任务卡。
- Task 卡片完成按钮只携带 `RuntimeActionInput`，不直接执行 Provider。
- `runtime_action_input` 卡片 action 已接入 Runtime 主链路。

## 当前禁止范围

- 全量 OAuth UI 改造。
- 全量 Provider 迁移。
- Approval Provider USER_TOKEN 迁移。
- Task create/update/delete USER_TOKEN 迁移。
- Admin identity 实现。
- 删除 CLI profile fallback。
- Task Portal / Task Insight / Task Snapshot / Task Graph。
- Event Bus / Workflow / Memory / Evidence / Snapshot / Insight Engine。
- UI 重构或 SidePanel 迁移。
- 数据库迁移。
- OAuth UI 新页面。
- 授权完成后的自动重试。
- Runtime State 新状态枚举。
- 自动创建测试任务。
- 自动点击用户 OAuth 授权。

## 当前验收标准

- Feishu Gateway 能从 Runtime V5 RuntimeResult 发送授权卡。
- Feishu Gateway 能从 Runtime V5 `task_list` 发送任务卡。
- Card Action 必须进入 `RuntimeActionInput`，不得直连 Provider。
- 缺少 USER_TOKEN 时必须返回 `waiting_authorization`。

## 当前验证

```text
tests/test_gateway_feishu.py::test_handle_feishu_command_sends_task_list_runtime_result_card
tests/test_gateway_feishu.py::test_runtime_action_input_card_action_enters_runtime
tests/test_gateway_feishu.py::test_build_runtime_result_card_renders_task_complete_action_input
tests/test_runtime_v5.py::test_runtime_v5_task_complete_waiting_confirmation_executes_and_returns_task_complete
tests/test_runtime_v5.py::test_runtime_v5_task_complete_failed_provider_returns_failed_task_complete
tests/test_execution_identity_user_token.py::test_task_complete_missing_user_token_returns_waiting_authorization
tests/test_execution_identity_user_token.py::test_task_complete_authorized_user_token_executes_task_provider
tests/test_runtime_v5.py::test_runtime_v5_waiting_authorization_builds_authorization_interaction_payload
py_compile: changed gateway/feishu files
cloud health: ok
```

已知未处理：

- `tests/test_feishu_provider_boundary.py` 当前存在 Feishu API 路径/timeout/能力清单断言漂移，和本阶段 USER_TOKEN 变更无直接关系。
- 当前部分旧文件仍有历史 `ruff E501/F401` 噪音，本阶段不做无关格式清理。

## 下一步计划

```text
Real Feishu Manual Acceptance
```

- 在真实飞书中触发一次 `task_complete` 缺授权。
- 验证 Bot/Card 是否展示授权入口。
- 用户完成飞书授权后，重新执行同一个任务完成动作。
- 验证 Runtime 自动使用 USER_TOKEN，而不是 CLI_PROFILE。
