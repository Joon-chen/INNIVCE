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
Enterprise Scope Model Review Phase
```

背景：

Capability Registry 项目已验收通过，并进入维护状态：

- Registry Payload 是四个页面的主事实来源。
- 旧数据源按“立即废弃 / 保留一个版本周期 / 长期保留”分级。
- 不继续扩展 Registry。

## 当前目标

Task Runtime Sample 实现前，先冻结企业级 Scope Model。

Digital Advisor 是企业数字参谋，不是个人助手。

当前目标是统一 Query 范围：

```text
SELF / USER / TEAM / DEPARTMENT / COMPANY
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

## 当前验收标准

- 输出 Enterprise Scope Model Review。
- 冻结 `SELF / USER / TEAM / DEPARTMENT / COMPANY`。
- 明确 Scope 数据模型。
- 明确 Scope -> Permission 映射。
- 输出 Task / Approval / People / Business Query Scope Matrix。
- 明确 RuntimeActionInput 是否需要 Scope。
- 明确 RuntimeResult 是否需要 Scope。
- 明确对 Task Runtime Contract 的影响。
- 不修改运行代码、数据库或业务逻辑。

## 下一步计划

1. 进入 Enterprise Scope Contract Test Phase。
2. 先补 Scope Contract Test。
3. 再回到 Task Runtime Sample Contract Test Phase。
