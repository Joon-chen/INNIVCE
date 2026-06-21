# Current Mission

本文档只回答：现在在做什么。

系统最高级规则见 `docs/V5_RUNTIME_CONSTITUTION.md`。
V5 能力分类见 `docs/V5_CAPABILITY_TAXONOMY.md`。
Capability Registry 模型见 `docs/CAPABILITY_REGISTRY_MODEL.md`。
页面职责评审见 `docs/PAGE_RESPONSIBILITY_AUDIT.md`。
Capability Registry Payload 设计见 `docs/CAPABILITY_REGISTRY_PAYLOAD_DESIGN.md`。
Capability Registry Read API 评审见 `docs/CAPABILITY_REGISTRY_READ_API_REVIEW.md`。

## 当前阶段

当前进入：

```text
Capability Registry Read API Review Phase
```

背景：

V5 业务域已冻结为：

```text
People
Communication
Workspace
Process
Knowledge
Business
Intelligence
```

Capability Registry Builder 已通过。

当前评估 Registry Builder 是否可以成为四个页面唯一数据源，并冻结 Read API 方案。

## 当前目标

评估两种 Read API：

```text
方案A: GET /api/v5/capability-registry
方案B: GET /catalog + /skills + /governance + /diagnostics
```

关键决策：

- 先冻结 Read API，不实现 UI。
- 证明四个页面可以完全由 Registry API 驱动。
- Builder 继续只做聚合。
- API 只负责 transport。

本阶段只做 API Review，不实现路由。

## 当前禁止范围

本阶段不要做：

- 新数据库表。
- Event Bus / Workflow / Memory / Evidence / Snapshot / Insight Engine。
- 第二套 Runtime。
- Task 业务代码 / Tool / Runtime Action / UI。
- 页面迁移实现。
- 数据库迁移。
- API 实现。
- Domain 调整。
- UI 重构。
- 业务逻辑改造。

## 当前验收标准

- 输出推荐方案。
- 输出 Payload 大小评估。
- 输出缓存策略。
- 输出前端消费复杂度。
- 输出 Builder 与 API 边界。
- 输出页面迁移路径。
- 证明能力目录 / 能力清册 / 治理中心 / 系统诊断可由 Registry API 驱动。
- 不修改页面代码。
- 不修改 API 路由。
- 不修改业务逻辑。

## 下一步计划

1. 冻结 `docs/CAPABILITY_REGISTRY_READ_API_REVIEW.md`。
2. 进入 Capability Registry Read API Implementation。
3. 只做只读 API 和合同测试，暂不做 UI 迁移。
