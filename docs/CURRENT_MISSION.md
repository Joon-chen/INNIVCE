# Current Mission

本文档只回答：现在在做什么。

系统最高级规则见 `docs/V5_RUNTIME_CONSTITUTION.md`。
V5 能力分类见 `docs/V5_CAPABILITY_TAXONOMY.md`。
Capability Registry 模型见 `docs/CAPABILITY_REGISTRY_MODEL.md`。
页面职责评审见 `docs/PAGE_RESPONSIBILITY_AUDIT.md`。
Capability Registry Payload 设计见 `docs/CAPABILITY_REGISTRY_PAYLOAD_DESIGN.md`。
企业 AI OS 顶层定义见 `docs/ENTERPRISE_AI_OS_V1.md`。
历史阶段归档见 `docs/history/`。

## 当前阶段

当前进入：

```text
Capability Registry Payload Design Phase
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

页面职责评审已冻结。

在进入页面重构前，需要先定义四个页面各自消费的只读 Payload，避免 UI 继续读取混合状态字段。

## 当前目标

冻结四个页面输入合同：

```text
能力目录 = Catalog Payload
能力清册 = Skill Registry Payload
治理中心 = Governance Payload
系统诊断 = Diagnostics Payload
```

关键决策：

- Catalog Payload 只包含 Domain -> Capability。
- Skill Registry Payload 只包含 Capability -> Skill。
- Governance Payload 只包含 Finding。
- Diagnostics Payload 只包含 Runtime Health。

本阶段只做 Payload 设计，不实现页面、数据库和 API。

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
- Capability Registry Builder 实现。

## 当前验收标准

- 输出 Root Payload。
- 输出 Catalog Payload。
- 输出 Skill Registry Payload。
- 输出 Governance Payload。
- 输出 Diagnostics Payload。
- Diagnostics Payload 不包含 Domain / Capability / Skill。
- 不修改 Runtime 代码。
- 不修改页面代码。

## 下一步计划

1. 冻结 `docs/CAPABILITY_REGISTRY_PAYLOAD_DESIGN.md`。
2. 进入 Capability Registry Builder Implementation。
3. 先做只读 Builder 和合同测试，再做最小 UI 迁移。
