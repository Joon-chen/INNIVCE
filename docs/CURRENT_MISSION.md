# Current Mission

本文档只回答：现在在做什么。

历史阶段归档见 `docs/history/`。
系统最高级规则见 `docs/V5_RUNTIME_CONSTITUTION.md`。
审批样板冻结版见 `docs/APPROVAL_RUNTIME_SAMPLE.md`。
Runtime V1 冻结复盘见 `docs/RUNTIME_V1_FREEZE_REVIEW.md`。
第二业务样板选择见 `docs/SECOND_BUSINESS_SAMPLE_SELECTION.md`。
审批验收复盘见 `docs/APPROVAL_RUNTIME_SAMPLE_ACCEPTANCE_REVIEW.md`。
企业认知底座 V1 见 `docs/ENTERPRISE_COGNITIVE_FOUNDATION_V1.md`。

## 当前阶段

当前阶段进入：

```text
Enterprise Cognitive Foundation V1
```

目标不是做审批专属 AI 建议缓存，而是建立企业认知系统的最小闭环：

```text
WorkEvent -> Snapshot -> MemoryCandidate
```

## 当前目标

以 Approval 作为第一条认知样板链路：

- WorkEvent：事实层，append-only。
- Snapshot：当前认知层，保存 AI 对审批的当前判断。
- MemoryCandidate：长期记忆候选层，只写候选，不做 Memory Engine。
- Bot 查询审批时优先读取 Approval Snapshot。
- Snapshot 不存在或 `status != completed` 时，只能展示“分析中”。
- Snapshot completed 后，才能展示“可通过 / 需关注 / 高风险”和原因。

## 当前禁止范围

本阶段不要做：

- WorkEvent Engine / Event Bus / Replay / Subscription。
- Workflow Engine。
- Memory Engine。
- Insight Engine。
- 跨公司聚合。
- Mail / Meeting / Customer / Task Snapshot。
- Approval 专属 Snapshot 表。
- Bot / Card / Portal 直接实时生成 AI 建议。
- Batch Migration、transfer/add_sign execution、USER Resolver。
- Diagnostics / Observability 重构。

## 当前验收标准

- WorkEvent 标准模型已冻结。
- Snapshot 标准模型已冻结。
- MemoryCandidate 标准模型已冻结。
- Approval Cognitive Lifecycle 已冻结。
- WorkEvent 写入语义必须是 append-only。
- Approval Snapshot 是 Bot 展示 AI 判断的唯一来源。
- 附件未完成或 AI 分析未完成时，Bot 显示“分析中”。
- 所有三层数据必须携带 `company_id`。
- 不建立审批专属快照表。

## 下一步计划

- 设计数据库迁移：标准 Snapshot / MemoryCandidate，以及 WorkEvent append-only 认知写入路径。
- 建立 ECF V1 service 边界：write event、read snapshot、upsert snapshot、write memory candidate。
- 用 Approval 样板接入：`approval_created -> attachment_processed -> approval_analysis_completed -> snapshot completed`。
- 调整 Approval 查询读取规则：只读 Snapshot 展示 AI 判断，未完成则显示“分析中”。
