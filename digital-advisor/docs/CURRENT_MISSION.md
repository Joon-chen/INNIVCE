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

## 当前阶段

当前进入：

```text
Registry Fallback Observation Phase
```

背景：

Registry UI V1 已冻结。

四个页面已接入 Registry Payload：

- 能力目录 -> `catalog_payload`
- 能力清册 -> `skill_registry_payload`
- 治理中心 -> `governance_payload`
- 系统诊断 -> `diagnostics_payload`

## 当前目标

观察 fallback 是否仍被需要。

目标是确认：

- Registry Payload 持续稳定覆盖四个页面。
- Registry Health 持续 healthy。
- `capability-registry-diff` 没有暴露阻塞缺口。
- fallback 保留但不再作为主数据源。
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
- Registry 概念扩展。

## 当前验收标准

- 输出 Registry Fallback Observation。
- 明确观察指标。
- 明确观察期禁止范围。
- 明确进入 fallback 移除评审的条件。
- 不修改运行代码。
- 不修改数据库。
- 不修改业务逻辑。

## 下一步计划

1. 观察四个页面真实使用情况。
2. 收集 `capability-registry-diff`。
3. 稳定后进入 Registry Fallback Deprecation Review。
