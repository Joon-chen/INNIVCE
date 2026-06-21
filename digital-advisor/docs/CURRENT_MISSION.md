# Current Mission

本文档只回答：现在在做什么。

系统最高级规则见 `docs/V5_RUNTIME_CONSTITUTION.md`。
V5 能力分类见 `docs/V5_CAPABILITY_TAXONOMY.md`。
Capability Registry 模型见 `docs/CAPABILITY_REGISTRY_MODEL.md`。
页面职责评审见 `docs/PAGE_RESPONSIBILITY_AUDIT.md`。
Capability Registry Payload 设计见 `docs/CAPABILITY_REGISTRY_PAYLOAD_DESIGN.md`。
Capability Registry Read API 评审见 `docs/CAPABILITY_REGISTRY_READ_API_REVIEW.md`。
Capability Registry 影子校验见 `docs/CAPABILITY_REGISTRY_SHADOW_VERIFICATION.md`。
Skill Registry 治理计划见 `docs/SKILL_REGISTRY_CLEANUP_PLAN.md`。
Registry UI 冻结评审见 `docs/REGISTRY_UI_FREEZE_REVIEW.md`。
Registry fallback 观察期见 `docs/REGISTRY_FALLBACK_OBSERVATION.md`。
Registry fallback 退役评审见 `docs/REGISTRY_FALLBACK_DEPRECATION_REVIEW.md`。
Task 认知样板设计见 `docs/TASK_COGNITIVE_SAMPLE_DESIGN.md`。

## 当前阶段

当前进入：

```text
Task Cognitive Sample Design Phase
```

背景：

Capability Registry 项目已验收通过，并进入维护状态。

Registry fallback 已进入退役评审：

- Registry Payload 是四个页面的主事实来源。
- 旧数据源按“立即废弃 / 保留一个版本周期 / 长期保留”分级。
- 不继续扩展 Registry。

## 当前目标

恢复主线，设计 Task 认知样板。

目标是验证 Task 是否可以复用：

```text
Workspace
-> Capability
-> Skill
-> Provider
```

以及认知链路：

```text
WorkEvent
-> Evidence
-> Snapshot
-> Insight
-> Runtime Action
```

## 当前禁止范围

本阶段不要做：

- 新数据库表。
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
- USER Resolver / DATE Resolver。
- Calendar / Meeting / Customer 样板。

## 当前验收标准

- 输出 Registry Fallback Deprecation Review。
- 输出 Task Cognitive Sample Design。
- 明确 Task 属于 Workspace，不建立 Task Module。
- 明确 Capability / Skill / Provider 映射。
- 明确 Task Snapshot 不替代实时 Task 状态。
- 明确 Insight 只给建议，Action 继续归 Runtime。
- 不修改运行代码。
- 不修改数据库。
- 不修改业务逻辑。

## 下一步计划

1. 评审 `task_query` / `task_complete` Contract。
2. 确认 Task Runtime Sample 最小验收标准。
3. 稳定后再决定是否进入 Task Runtime Sample Implementation。
