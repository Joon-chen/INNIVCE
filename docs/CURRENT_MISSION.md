# Current Mission

本文档只回答：现在在做什么。

系统最高级规则见 `docs/V5_RUNTIME_CONSTITUTION.md`。
V5 能力分类见 `docs/V5_CAPABILITY_TAXONOMY.md`。
Capability Registry 模型见 `docs/CAPABILITY_REGISTRY_MODEL.md`。
页面职责评审见 `docs/PAGE_RESPONSIBILITY_AUDIT.md`。
Capability Registry Payload 设计见 `docs/CAPABILITY_REGISTRY_PAYLOAD_DESIGN.md`。

## 当前阶段

当前进入：

```text
Capability Registry Builder Implementation Phase
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

Capability Registry Payload Design 已通过。

当前进入只读 Builder 实现，先验证四个页面是否可以由 Registry Payload 驱动。

## 当前目标

实现只读聚合链路：

```text
RuntimeCapability
+ SkillAtomicCapability
+ ToolConfig
+ ProviderHealth
+ GovernanceFinding
-> CapabilityRegistryBuilder
-> catalog_payload / skill_registry_payload / governance_payload / diagnostics_payload
```

关键决策：

- Builder 只做聚合。
- Builder 不新增存储。
- Builder 不改变 Runtime 运行逻辑。
- UI 暂不迁移。

本阶段只实现 Builder 和合同测试。

## 当前禁止范围

本阶段不要做：

- 新数据库表。
- Event Bus / Workflow / Memory / Evidence / Snapshot / Insight Engine。
- 第二套 Runtime。
- Task 业务代码 / Tool / Runtime Action / UI。
- 页面迁移实现。
- 数据库迁移。
- API 改造。
- Domain 调整。
- 新增设计文档。

## 当前验收标准

- 实现 `CapabilityRegistryBuilder`。
- 输出 `catalog_payload`。
- 输出 `skill_registry_payload`。
- 输出 `governance_payload`。
- 输出 `diagnostics_payload`。
- 输出一致性检查：MissingCapability / MissingSkill / MissingProvider / OrphanSkill / OrphanProvider。
- 输出 Mock Payload。
- 输出迁移评估。
- 不修改 Runtime 代码。
- 不修改页面代码。
- 不修改 API 路由。
- Builder 合同测试通过。

## 下一步计划

1. 完成 Builder 实现和测试。
2. 进入 Capability Registry Read Endpoint Review。
3. 再决定是否开放只读 API，暂不做 UI 迁移。
