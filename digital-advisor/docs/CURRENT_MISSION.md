# Current Mission

本文档只回答：现在在做什么。

系统最高级规则见 `docs/V5_RUNTIME_CONSTITUTION.md`。
V5 能力分类见 `docs/V5_CAPABILITY_TAXONOMY.md`。
Capability Registry 模型见 `docs/CAPABILITY_REGISTRY_MODEL.md`。
Registry fallback 退役评审见 `docs/REGISTRY_FALLBACK_DEPRECATION_REVIEW.md`。
Task 认知样板设计见 `docs/TASK_COGNITIVE_SAMPLE_DESIGN.md`。
Task Runtime 样板合同评审见 `docs/TASK_RUNTIME_SAMPLE_CONTRACT_REVIEW.md`。
Task Runtime 样板验收评审见 `docs/TASK_RUNTIME_SAMPLE_ACCEPTANCE_REVIEW.md`。
Task Runtime 飞书手工验收见 `docs/TASK_RUNTIME_SAMPLE_FEISHU_MANUAL_ACCEPTANCE.md`。
企业级 Scope 模型评审见 `docs/ENTERPRISE_SCOPE_MODEL_REVIEW.md`。
执行身份审计见 `docs/EXECUTION_IDENTITY_AUDIT.md`。
执行身份合同设计见 `docs/EXECUTION_IDENTITY_CONTRACT_DESIGN.md`。

## 当前阶段

当前进入：

```text
Execution Identity Contract Guard Completed
```

背景：

Capability Registry 项目已验收通过，并进入维护状态：

- Registry Payload 是四个页面的主事实来源。
- 旧数据源按“立即废弃 / 保留一个版本周期 / 长期保留”分级。
- 不继续扩展 Registry。

## 当前目标

Enterprise Scope Model 已冻结，最小 Scope Contract 已落地。

Digital Advisor 是企业数字参谋，不是个人助手。

当前已冻结 Query 范围：

```text
SELF / USER / TEAM / DEPARTMENT / COMPANY
```

Task Runtime Sample 验收评审已完成。

当前已验证：

```text
task_query
→ RuntimeResult actions.runtime_action_input
→ WAITING_CONFIRMATION
→ CONFIRMED
→ EXECUTING
task_complete
→ InteractionPayload feedback
```

真实 Feishu Task 只读抽样已验证：

```text
guid / summary / status / url
```

当前 Provider/RuntimeResult 可以把真实 `guid` 转成 `RuntimeActionInput.target.task_guid`。

真实飞书写操作验收已尝试：

- 本地 Runtime 到 `WAITING_CONFIRMATION` 成立。
- 本地 Provider 执行受本地 PostgreSQL 未运行阻塞。
- 云端容器 `lark-cli --as user` 未配置，无法执行 user identity 写操作。
- 测试任务已用本地 CLI 清理完成，不作为 Runtime 验收通过依据。

执行身份审计已完成：

- `BOT / USER` 适合作为产品语义。
- `TENANT_TOKEN / USER_TOKEN / CLI_PROFILE / ADMIN_SESSION` 才是执行凭证语义。
- Approval query/detail 已接近 Tenant 化。
- Approval write 是 USER 语义，但当前可能走 USER_TOKEN 或 CLI_PROFILE。
- Task write 当前实际落到 `lark-cli --as user`，云端缺少 CLI user profile。
- Task write 目标路径应切换为 Runtime 自动取当前用户 Feishu User OAuth Token。

执行身份合同设计已完成：

- `actor_identity` 回答“代表谁执行”。
- `credential_mode` 回答“用什么凭证执行”。
- 云端 Runtime 的目标主路径不得依赖 `CLI_PROFILE`。
- `CLI_PROFILE` 只允许作为本地开发/临时兼容 fallback。
- Task `complete_task` 目标路径冻结为 `USER_TOKEN` 优先。
- Approval write 目标路径冻结为 `USER_TOKEN`。

执行身份最小 Guard 已完成：

- 新增 `ExecutionIdentityContract` / `CredentialOwner` 合同类型。
- `ProviderRequest` 现在携带 `execution_identity_contract`。
- Capability Registry Skill payload 现在输出 `identity_contract`。
- Task `complete_task` 已在合同层标记为 `USER + USER_TOKEN`。
- Approval `approve/reject` 已在合同层标记为 `USER + USER_TOKEN`。
- Approval query 已在合同层标记为 `BOT + TENANT_TOKEN`。
- 当前未接 User OAuth Token lookup，`authorization_status` 仍保持 `UNKNOWN`。

## 当前禁止范围

本阶段不要做：

- Event Bus / Workflow / Memory / Evidence / Snapshot / Insight Engine。
- 第二套 Runtime。
- Task Portal / Task Insight / Task Snapshot / Task Risk / Task Graph。
- 数据库迁移。
- Domain 调整。
- UI 重构。
- Shadow Panel。
- 业务逻辑改造。
- Registry 概念扩展。
- 立即移除旧数据源 fallback。
- OBJECT / USER / DATE 参数类型或 Resolver。
- Calendar / Meeting / Customer 样板。
- Task Snapshot / Insight 实现。
- Task Create WAITING_INPUT 实现。
- Task UI / Portal / SidePanel 迁移。
- 全量 OAuth 改造。
- 全量 Provider 迁移。
- Admin identity 实现。
- CLI profile 删除。
- Task Provider USER_TOKEN 实现。
- Approval Provider 全量 USER_TOKEN 迁移。
- OAuth UI 改造。
- WAITING_AUTHORIZATION 状态机落地。

## 当前验收标准

- `RuntimeResult.metadata.scope_context` 已存在。
- `RuntimeActionInput.metadata.scope_context` 可透传。
- 普通员工 COMPANY scoped query 被 Policy 拒绝。
- `task_query` Contract Test 已通过。
- `task_complete` Contract Test 已通过。
- `task_query` 可生成 `task_complete` 的 `RuntimeActionInput`。
- `task_complete` 可通过 Runtime State 执行并返回 `RuntimeResult`。
- `InteractionPayload` 只消费 `RuntimeResult` 并输出 feedback。
- `complete_task` 已收敛为 Runtime result_type `task_complete`。
- 真实 Feishu Task 只读样本字段已验证。
- 真实 Feishu `complete_task` 写操作因云端 user identity 未配置而阻塞。
- Execution Identity Audit 已完成。
- 已明确 Task 写入阻塞来自 CLI_PROFILE，而不是 RuntimeActionInput Contract。
- 已明确下一步应冻结 Execution Identity Contract，而不是直接补丁式配置云端 CLI。
- Execution Identity Contract Design 已完成。
- 已冻结 `actor_identity` / `credential_mode` 双层模型。
- 已冻结 Task / Approval 的目标身份路径。
- Execution Identity Contract Guard 已完成。
- Runtime ProviderRequest 已可传递 identity contract。
- Capability Registry 已可展示 Skill identity contract。
- 不修改数据库。
- 不做 Task UI / Snapshot / Insight / 业务迁移。

## 下一步计划

1. 进入 User Token Readiness Phase。
2. 审计现有 Feishu User OAuth Account lookup 能否被 Runtime 复用。
3. 设计 `USER_TOKEN` lookup helper，不迁移全量 Provider。
4. 只选择 Task `complete_task` 作为第一条 USER_TOKEN 执行样板。
5. 缺少 USER_TOKEN 时再落地 `WAITING_AUTHORIZATION`。
