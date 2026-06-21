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
Approval Cognitive Integration Phase
```

Enterprise Cognitive Foundation V1 的三层底座已建立。本阶段把 Approval 查询接入认知闭环：

```text
Approval -> WorkEvent -> Snapshot -> Bot Read Snapshot
```

## 当前目标

以 Approval 作为第一条认知样板链路：

- Approval Query 读取 Snapshot。
- Snapshot 不存在或 `status != completed` 时，只能展示“分析中”。
- Snapshot completed 后，展示 `recommendation / risk_level / reasons`。
- `approval_analysis_completed` 必须写入 Snapshot。
- Bot / Card / Portal / SidePanel 不直接使用实时 AI 判断作为展示依据。

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
- `approval_analysis_completed` 会写入 completed Snapshot。
- 所有三层数据必须携带 `company_id`。
- 不建立审批专属快照表。

## 下一步计划

- 部署 Approval Snapshot 读取/写入链路。
- 用真实飞书审批查询验证：首次无 completed Snapshot 时展示“分析中”；分析完成后展示 Snapshot 建议。
- 观察是否需要独立异步 Attachment Processor；暂不引入 WorkEvent Engine。
