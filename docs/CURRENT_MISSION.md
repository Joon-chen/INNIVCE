# Current Mission

本文档只回答：现在在做什么。

系统最高级规则见 `docs/V5_RUNTIME_CONSTITUTION.md`。
V5 能力分类见 `docs/V5_CAPABILITY_TAXONOMY.md`。
企业 AI OS 顶层定义见 `docs/ENTERPRISE_AI_OS_V1.md`。
企业认知底座 V1 见 `docs/ENTERPRISE_COGNITIVE_FOUNDATION_V1.md`。
企业认知底座 V1 冻结复盘见 `docs/ECF_V1_FREEZE_REVIEW.md`。
Snapshot 生成节奏见 `docs/SNAPSHOT_TRIGGER_MATRIX.md`。
OA 智能闭环设计见 `docs/OA_INTELLIGENCE_LOOP_DESIGN.md`。
Insight 合同 V0 见 `docs/INSIGHT_CONTRACT_V0.md`。
审批 Insight 样板见 `docs/APPROVAL_INSIGHT_SAMPLE.md`。
Insight 展示边界见 `docs/INSIGHT_RENDERER_BOUNDARY.md`。
历史阶段归档见 `docs/history/`。

## 当前阶段

当前进入：

```text
V5 Capability Taxonomy Freeze Phase
```

背景：

Enterprise Cognitive Foundation V1 已冻结。

在进入 Task Cognitive Sample 前，需要先冻结 V5 业务域与能力分类，避免后续继续按飞书产品建立模块。

当前冻结链路：

```text
Raw Data
-> WorkEvent
-> Evidence
-> Snapshot
-> Insight
```

Action 继续归 Runtime。

## 当前目标

冻结统一模型：

```text
Business Domain
-> Capability
-> Skill
-> Provider
```

最终业务域：

```text
People
Communication
Workspace
Process
Knowledge
Business
Intelligence
```

关键决策：

- Calendar 归入 Workspace。
- Approval 归入 Process。
- 不建立 Approval Module / Task Module / Wiki Module 等飞书产品导向模型。

本阶段只做分类冻结，不实现页面和数据模型。

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

## 当前验收标准

- Enterprise Cognitive Foundation V1 状态为 Frozen。
- WorkEvent = 事实层。
- Evidence = 判断依据层。
- Snapshot = 当前认知层。
- Insight = 建议层。
- Action = Runtime 执行层。
- V5 业务域冻结为 People / Communication / Workspace / Process / Knowledge / Business / Intelligence。
- Task 未来归属 Workspace，而不是 Task Module。
- Approval 未来归属 Process，而不是 Approval Module。
- Calendar 未来归属 Workspace，而不是 Calendar Module。

## 下一步计划

1. 冻结 `docs/V5_CAPABILITY_TAXONOMY.md`。
2. 进入 Task Cognitive Sample Selection。
3. 分析 Task WorkEvent / Evidence / Snapshot / Insight 是否可复用。
