# Current Mission

本文档只回答：现在在做什么。

历史阶段归档见 `docs/history/`。
系统最高级规则见 `docs/V5_RUNTIME_CONSTITUTION.md`。
审批样板冻结版见 `docs/APPROVAL_RUNTIME_SAMPLE.md`。
Runtime V1 冻结复盘见 `docs/RUNTIME_V1_FREEZE_REVIEW.md`。
第二业务样板选择见 `docs/SECOND_BUSINESS_SAMPLE_SELECTION.md`。
审批验收复盘见 `docs/APPROVAL_RUNTIME_SAMPLE_ACCEPTANCE_REVIEW.md`。
企业认知底座 V1 见 `docs/ENTERPRISE_COGNITIVE_FOUNDATION_V1.md`。
Snapshot 生成节奏见 `docs/SNAPSHOT_TRIGGER_MATRIX.md`。

## 当前阶段

当前阶段进入：

```text
Approval Snapshot Builder Alignment Phase
```

Snapshot Trigger Matrix 已建立。本阶段按矩阵修正 Approval Snapshot Builder：

```text
WorkEvent -> Snapshot Builder -> Snapshot
```

## 当前目标

定义 Snapshot 触发规则：

- Snapshot 不由 Query 触发。
- Snapshot 不由用户打开页面触发。
- Snapshot 由 WorkEvent 驱动。
- Bot / Card / Portal / SidePanel 只读取 Snapshot。
- Snapshot Builder 根据 WorkEvent 判断是否需要重建 Snapshot。
- Approval 是第一条按 Trigger Matrix 对齐的样板。

## 当前禁止范围

本阶段不要做：

- WorkEvent Engine / Event Bus / Replay / Subscription。
- Workflow Engine。
- Memory Engine。
- Insight Engine。
- 跨公司聚合。
- Mail / Meeting / Customer / Task Snapshot。
- Approval 专属 Snapshot 表。
- 使用 Snapshot 替代实时审批状态。
- 将审批状态、审批列表、申请人、金额等业务事实缓存到 Snapshot。
- Bot / Card / Portal 直接实时生成 AI 建议。
- 在 Bot Query 链路中执行附件读取或 AI 分析。
- 由 Query / Portal / SidePanel 触发 Snapshot Builder。
- Batch Migration、transfer/add_sign execution、USER Resolver。
- Diagnostics / Observability 重构。

## 当前验收标准

- WorkEvent 标准模型已冻结。
- Snapshot 标准模型已冻结。
- MemoryCandidate 标准模型已冻结。
- Approval Cognitive Lifecycle 已冻结。
- WorkEvent 写入语义必须是 append-only。
- Approval Snapshot 是 Bot 展示 AI 判断的唯一来源，不是审批状态来源。
- Approval 状态、审批列表和审批详情仍来自 Feishu live data。
- 附件未完成或 AI 分析未完成时，Bot 显示“分析中”。
- `attachment_processed` 是 Approval AI 分析的主要触发事件。
- `approval_analysis_completed` 负责写入 completed Snapshot。
- 所有三层数据必须携带 `company_id`。
- 不建立审批专属快照表。
- 同一批 live data 和同一批 completed Snapshot 下，多次查询结果必须一致。

## 下一步计划

- 部署 WorkEvent 驱动的 Approval Snapshot Builder。
- 验证 Bot Query 不写 WorkEvent、不写 Snapshot、不触发 Builder。
- 验证重复查询在同一批 Snapshot 下结果稳定。
