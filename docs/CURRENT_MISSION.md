# Current Mission

本文档只回答：现在在做什么。

系统最高级规则见 `docs/V5_RUNTIME_CONSTITUTION.md`。
V5 能力分类见 `docs/V5_CAPABILITY_TAXONOMY.md`。
Capability Registry 模型见 `docs/CAPABILITY_REGISTRY_MODEL.md`。
页面职责评审见 `docs/PAGE_RESPONSIBILITY_AUDIT.md`。
企业 AI OS 顶层定义见 `docs/ENTERPRISE_AI_OS_V1.md`。
企业认知底座 V1 见 `docs/ENTERPRISE_COGNITIVE_FOUNDATION_V1.md`。
历史阶段归档见 `docs/history/`。

## 当前阶段

当前进入：

```text
Page Responsibility Audit Phase
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

Capability Registry 已冻结。

在进入页面重构前，需要先评审当前页面内容与目标职责的差异，避免只改页面标题而没有真正收口职责。

## 当前目标

评审四个页面职责：

```text
能力目录 = Domain -> Capability
能力清册 = Capability -> Skill
治理中心 = Capability -> Skill -> Provider
系统诊断 = Runtime Health
```

关键决策：

- 能力目录面向业务用户。
- 能力清册面向管理员和开发者。
- 治理中心只展示可处理治理问题。
- 系统诊断只展示运行健康。

本阶段只做评审，不实现页面、数据库和 API。

## 当前禁止范围

本阶段不要做：

- 新数据库表。
- Event Bus / Replay / Subscription。
- Workflow Engine。
- Memory Engine。
- Evidence Engine。
- Snapshot Engine。
- Insight Engine。
- Insight Store。
- Insight Persistence。
- Action Candidate。
- Action Planner。
- 第二套 Runtime。
- Task 业务代码。
- Task Tool 新增。
- Task Runtime Action。
- Task UI 开发。
- 页面迁移实现。
- 数据库迁移。
- API 改造。
- Domain 调整。
- Capability Registry Payload 实现。

## 当前验收标准

- V5 业务域冻结为 People / Communication / Workspace / Process / Knowledge / Business / Intelligence。
- 输出页面职责矩阵。
- 输出需要迁移的数据项。
- 输出需要删除或下沉的数据项。
- 输出 Capability Registry 页面消费设计。
- 输出最终页面结构。
- 不修改 Runtime 代码。
- 不修改页面代码。

## 下一步计划

1. 冻结 `docs/PAGE_RESPONSIBILITY_AUDIT.md`。
2. 进入 Capability Registry Payload Design。
3. 再做最小 UI 迁移。
