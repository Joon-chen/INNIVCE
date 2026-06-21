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

当前已完成 Runtime V5 授权卡接入：

```text
ProviderResult.waiting_authorization
→ RuntimeResult.authorization
→ InteractionPayload.authorization
→ Feishu Authorization Card
```

已完成：

- `waiting_authorization` 保留为 Runtime Result Type，不再被折回普通 `task_complete`。
- RuntimeResult 标准输出 `metadata.authorization`。
- RuntimeResult 标准输出 `authorize_user_identity` action。
- InteractionPayload 标准输出 `payload_type = authorization`。
- 授权入口指向 `/api/user-identity/oauth/feishu/start`。
- Card / Portal 只需渲染 InteractionPayload，不需要解释 Provider 失败逻辑。
- Feishu Gateway 可从 Runtime V5 Trace 中读取 `authorize_user_identity` action。
- Runtime V5 授权等待会发送现有 User Identity Authorization Card，而不是普通文本。

## 当前禁止范围

本阶段不要做：

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

- `ProviderResult.result_type = waiting_authorization` 可进入 RuntimeResult。
- `RuntimeResult.status = waiting_authorization`。
- `RuntimeResult.actions[0].action = authorize_user_identity`。
- `InteractionPayload.payload_type = authorization`。
- 授权 action 包含 `url / resource_type / channel / authorization_status`。
- 交互层不生成授权 URL，不处理授权业务逻辑。
- Feishu Gateway 能从 Runtime V5 RuntimeResult 发送授权卡。
- 授权卡发送结果写入 Gateway audit payload。

## 当前验证

已通过：

```text
tests/test_execution_identity_user_token.py
tests/test_runtime_v5.py
tests/test_capability_registry_builder.py
tests/test_v5_architecture.py
tests/test_gateway_feishu.py
ruff check
```

已知未处理：

- `tests/test_feishu_provider_boundary.py` 当前存在 Feishu API 路径/timeout/能力清单断言漂移，和本阶段 USER_TOKEN 变更无直接关系。

## 下一步计划

下一步继续：

```text
Real Feishu Manual Acceptance
```

目标：

- 在真实飞书中触发一次 `task_complete` 缺授权。
- 验证 Bot/Card 是否展示授权入口。
- 用户完成飞书授权后，重新执行同一个任务完成动作。
- 验证 Runtime 自动使用 USER_TOKEN，而不是 CLI_PROFILE。
