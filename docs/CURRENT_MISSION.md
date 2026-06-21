# Current Mission

本文档只回答：现在在做什么。

系统最高级规则见 `docs/V5_RUNTIME_CONSTITUTION.md`。
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
Task Cognitive Sample Selection
```

背景：

Enterprise Cognitive Foundation V1 已冻结。

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

选择 Task 是否适合作为第二条 Cognitive Sample。

验证 Task 是否可以复用：

```text
WorkEvent
-> Evidence
-> Snapshot
-> Insight
```

本阶段只做选择和分析，不实现 Task 样板。

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

## 当前验收标准

- Enterprise Cognitive Foundation V1 状态为 Frozen。
- WorkEvent = 事实层。
- Evidence = 判断依据层。
- Snapshot = 当前认知层。
- Insight = 建议层。
- Action = Runtime 执行层。
- Task Sample Selection 只评估复用，不实现。

## 下一步计划

1. 分析 Task WorkEvent。
2. 分析 Task Evidence。
3. 分析 Task Snapshot。
4. 分析 Task Insight。
5. 输出 Task Cognitive Sample 是否适合作为第二条样板。
