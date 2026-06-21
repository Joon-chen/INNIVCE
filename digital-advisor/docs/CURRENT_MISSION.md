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

## 当前阶段

当前进入：

```text
Capability Lifecycle Guard Phase
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

Capability Registry Contract Freeze 已通过。

当前新增 Capability Lifecycle Guard，防止 Registry Health 回归。

## 当前目标

新增 RuntimeCapability 时必须同时声明：

```text
Domain
Capability
Skill
Provider
```

关键决策：

- 不新增 Registry 设计。
- 不新增 Registry 页面。
- 不新增 Shadow Panel。
- 只增加 Registry 生命周期守护。

本阶段目标是让测试长期守住 Registry Health。

## 当前禁止范围

本阶段不要做：

- 新数据库表。
- Event Bus / Workflow / Memory / Evidence / Snapshot / Insight Engine。
- 第二套 Runtime。
- Task 业务代码 / Tool / Runtime Action / UI。
- 页面迁移实现。
- 数据库迁移。
- Domain 调整。
- UI 重构。
- 页面迁移。
- Shadow Panel。
- 业务逻辑改造。

## 当前验收标准

- 新增 Capability Lifecycle Guard。
- 新增 CI Contract Test。
- 新增 RuntimeCapability 时缺 Domain / Capability / Skill / Provider 会失败。
- 新增 Skill 时缺 Capability 会失败。
- 新增 Capability 时缺 Domain 会失败。
- 新增 Provider Binding 时缺 Skill 会失败。
- 禁止 `missing_skill`、`missing_provider`、`orphan_skill`、`orphan_provider` 回归。
- 不修改页面代码。
- 不修改数据库。
- 不修改业务逻辑。

## 下一步计划

1. 完成 Lifecycle Guard。
2. 运行 Registry Contract Test。
3. 云端验证 Registry Health 保持 healthy。
