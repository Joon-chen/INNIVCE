# Current Mission

本文档只回答：现在在做什么。

系统最高级规则见 `docs/V5_RUNTIME_CONSTITUTION.md`。
V5 能力分类见 `docs/V5_CAPABILITY_TAXONOMY.md`。
Capability Registry 模型见 `docs/CAPABILITY_REGISTRY_MODEL.md`。
企业 AI OS 顶层定义见 `docs/ENTERPRISE_AI_OS_V1.md`。
企业认知底座 V1 见 `docs/ENTERPRISE_COGNITIVE_FOUNDATION_V1.md`。
历史阶段归档见 `docs/history/`。

## 当前阶段

当前进入：

```text
Capability Registry Design Phase
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

在进入 Task Cognitive Sample 前，需要冻结 Capability Registry，避免能力目录、能力清册、治理中心、系统诊断继续混用飞书产品、原子能力和运行状态分类。

## 当前目标

冻结统一模型：

```text
Business Domain
-> Capability
-> Skill
-> Provider
```

关键决策：

- Calendar 归入 Workspace。
- Approval 归入 Process。
- Capability 是能力目录展示对象。
- Skill 是能力清册展示对象。
- Governance Center 以 Capability / Skill / Provider 为治理对象。
- System Diagnostics 保持 Runtime 视角，不参与能力分类。

本阶段只做模型冻结，不实现页面、数据库和 API。

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

## 当前验收标准

- V5 业务域冻结为 People / Communication / Workspace / Process / Knowledge / Business / Intelligence。
- Capability Registry 冻结 Domain -> Capability -> Skill -> Provider。
- 能力目录以 Capability 为展示对象。
- 能力清册以 Skill 为展示对象。
- 治理中心以 Capability / Skill / Provider 为治理对象。
- 系统诊断保持 Runtime 视角，不按业务域分类。
- Task 未来归属 Workspace Capability，而不是 Task Module。

## 下一步计划

1. 冻结 `docs/CAPABILITY_REGISTRY_MODEL.md`。
2. 进入 Task Cognitive Sample Selection。
3. 分析 Task 是否复用 WorkEvent -> Evidence -> Snapshot -> Insight。
