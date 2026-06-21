# Current Mission

本文档只回答：现在在做什么。

系统最高级规则见 `docs/V5_RUNTIME_CONSTITUTION.md`。
V5 能力分类见 `docs/V5_CAPABILITY_TAXONOMY.md`。
Capability Registry 模型见 `docs/CAPABILITY_REGISTRY_MODEL.md`。
Registry fallback 退役评审见 `docs/REGISTRY_FALLBACK_DEPRECATION_REVIEW.md`。
Task 认知样板设计见 `docs/TASK_COGNITIVE_SAMPLE_DESIGN.md`。
Task Runtime 样板合同评审见 `docs/TASK_RUNTIME_SAMPLE_CONTRACT_REVIEW.md`。
企业级 Scope 模型评审见 `docs/ENTERPRISE_SCOPE_MODEL_REVIEW.md`。

## 当前阶段

当前进入：

```text
Task Runtime Sample Contract Test Completed
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

Task Runtime Sample 合同测试已完成。

当前已验证：

```text
task_query
task_complete
WAITING_CONFIRMATION -> CONFIRMED -> EXECUTING -> DONE / FAILED
```

## 当前禁止范围

本阶段不要做：

- Event Bus / Workflow / Memory / Evidence / Snapshot / Insight Engine。
- 第二套 Runtime。
- Task 业务代码 / Tool / Provider / Runtime Action / UI。
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
- Task Runtime Sample 实现。
- Task UI / Portal / SidePanel 迁移。

## 当前验收标准

- `RuntimeResult.metadata.scope_context` 已存在。
- `RuntimeActionInput.metadata.scope_context` 可透传。
- 普通员工 COMPANY scoped query 被 Policy 拒绝。
- `task_query` Contract Test 已通过。
- `task_complete` Contract Test 已通过。
- `complete_task` 已收敛为 Runtime result_type `task_complete`。
- 不修改数据库。
- 不做 Task UI / Snapshot / Insight / 业务迁移。

## 下一步计划

1. 进入 Task Runtime Sample Minimal Implementation Review。
2. 确认是否把 Task 完成动作接到真实入口。
3. 继续禁止 UI / Snapshot / Insight 扩展。
