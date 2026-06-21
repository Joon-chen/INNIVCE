# Current Mission

本文档只回答：现在在做什么。

系统最高级规则见 `docs/V5_RUNTIME_CONSTITUTION.md`。
V5 能力分类见 `docs/V5_CAPABILITY_TAXONOMY.md`。
Capability Registry 模型见 `docs/CAPABILITY_REGISTRY_MODEL.md`。
页面职责评审见 `docs/PAGE_RESPONSIBILITY_AUDIT.md`。
Capability Registry Payload 设计见 `docs/CAPABILITY_REGISTRY_PAYLOAD_DESIGN.md`。
Capability Registry Read API 评审见 `docs/CAPABILITY_REGISTRY_READ_API_REVIEW.md`。
Capability Registry 影子校验见 `docs/CAPABILITY_REGISTRY_SHADOW_VERIFICATION.md`。

## 当前阶段

当前进入：

```text
Registry Shadow Verification Phase
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

Capability Registry Read API Implementation 已通过。

当前验证现有四类页面是否可以由 Registry API 作为唯一事实来源驱动。

## 当前目标

逐页面检查：

```text
能力目录
能力清册
治理中心
系统诊断
```

关键决策：

- 不进入 UI Migration。
- 只做字段级 Shadow Verification。
- 特别关注 registry_health.status = needs_attention。

本阶段只输出影子校验结果。

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
- UI Migration。
- 业务逻辑改造。

## 当前验收标准

- 输出当前页面字段清单。
- 输出 Registry 已覆盖字段。
- 输出 Registry 缺失字段。
- 输出 Registry 冗余字段。
- 输出可以立即迁移字段。
- 输出暂不能迁移字段。
- 输出 `needs_attention` 问题清单。
- 不修改页面代码。
- 不修改数据库。
- 不修改业务逻辑。

## 下一步计划

1. 完成 Shadow Verification 文档。
2. 评估 Minimal Registry Shadow Panel。
3. 再决定是否进入 Minimal UI Migration。
