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

## 当前阶段

当前进入：

```text
Registry UI Freeze Review Phase
```

背景：

Capability Registry Builder、Read API、Lifecycle Guard、Minimal UI Migration 已完成。

四个页面已接入 Registry Payload：

- 能力目录 -> `catalog_payload`
- 能力清册 -> `skill_registry_payload`
- 治理中心 -> `governance_payload`
- 系统诊断 -> `diagnostics_payload`

## 当前目标

冻结 Registry UI V1。

目标是确认：

- Registry API 可以成为四个页面的主数据源。
- 页面仍保留旧数据源 fallback。
- 不继续新增 Registry 设计。
- 不继续新增 Registry 页面。
- 不提前移除 fallback。

## 当前禁止范围

本阶段不要做：

- 新数据库表。
- Event Bus / Workflow / Memory / Evidence / Snapshot / Insight Engine。
- 第二套 Runtime。
- Task 业务代码 / Tool / Runtime Action / UI。
- 数据库迁移。
- Domain 调整。
- UI 重构。
- Shadow Panel。
- 业务逻辑改造。
- 移除旧数据源 fallback。

## 当前验收标准

- 输出 Registry UI Freeze Review。
- 确认四个页面 Payload 映射完整。
- 确认 Registry Health 云端为 healthy。
- 确认 Diagnostics Summary 为 healthy。
- 确认 fallback 保留。
- 不修改运行代码。
- 不修改数据库。
- 不修改业务逻辑。

## 下一步计划

1. 冻结 Registry UI V1。
2. 进入 Registry Fallback Observation Phase。
3. 观察稳定后评估是否进入 Fallback Deprecation Review。
