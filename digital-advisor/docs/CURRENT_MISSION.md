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

## 当前阶段

当前进入：

```text
Task Runtime Sample Feishu Manual Acceptance Blocked
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
- 不修改数据库。
- 不做 Task UI / Snapshot / Insight / 业务迁移。

## 下一步计划

1. 进入 Cloud User Identity Runtime Readiness Phase。
2. 明确云端 Runtime Provider 使用的 `lark-cli` home/profile。
3. 完成云端 `--as user` 授权后，再验证真实 Task complete Runtime 写链路。
3. 继续禁止 Task Portal / Insight / Snapshot 扩展。
