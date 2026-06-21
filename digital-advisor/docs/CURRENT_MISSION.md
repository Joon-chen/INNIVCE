# Current Mission

本文档只回答：现在在做什么。

系统最高级规则见 `docs/V5_RUNTIME_CONSTITUTION.md`。
V5 能力分类见 `docs/V5_CAPABILITY_TAXONOMY.md`。
Capability Registry 模型见 `docs/CAPABILITY_REGISTRY_MODEL.md`。
Registry fallback 退役评审见 `docs/REGISTRY_FALLBACK_DEPRECATION_REVIEW.md`。
Task 认知样板设计见 `docs/TASK_COGNITIVE_SAMPLE_DESIGN.md`。
Task Runtime 样板合同评审见 `docs/TASK_RUNTIME_SAMPLE_CONTRACT_REVIEW.md`。

## 当前阶段

当前进入：

```text
Task Runtime Sample Contract Review Phase
```

背景：

Capability Registry 项目已验收通过，并进入维护状态：

- Registry Payload 是四个页面的主事实来源。
- 旧数据源按“立即废弃 / 保留一个版本周期 / 长期保留”分级。
- 不继续扩展 Registry。

## 当前目标

Task 认知样板设计已完成。

当前目标是评审 Task 最小 Runtime Contract。

当前验证 Task 是否可以复用：

```text
Workspace
-> Capability
-> Skill
-> Provider
```

首选最小 Runtime 样板是：

```text
task_query
-> task_list
-> task_complete
-> feedback
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

## 当前验收标准

- 输出 Task Runtime Sample Contract Review。
- 明确 Task 属于 Workspace，不建立 Task Module。
- 明确 Capability / Skill / Provider 映射。
- 明确 Task Snapshot 不替代实时 Task 状态。
- 明确 Insight 只给建议，Action 继续归 Runtime。
- 明确 `task_query` / `task_complete` 的 Contract 缺口。
- 明确下一步是否只补 Contract Test。
- 不修改运行代码、数据库或业务逻辑。

## 下一步计划

1. 进入 Task Runtime Sample Contract Test Phase。
2. 先补 `task_query` / `task_complete` Contract Test。
3. 只做最小命名收敛，不做 UI / Snapshot / Insight。
