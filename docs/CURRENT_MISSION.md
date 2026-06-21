# Current Mission

本文档只回答：现在在做什么。

系统最高级规则见 `docs/V5_RUNTIME_CONSTITUTION.md`。
企业 AI OS 顶层定义见 `docs/ENTERPRISE_AI_OS_V1.md`。
企业认知底座 V1 见 `docs/ENTERPRISE_COGNITIVE_FOUNDATION_V1.md`。
Snapshot 生成节奏见 `docs/SNAPSHOT_TRIGGER_MATRIX.md`。
历史阶段归档见 `docs/history/`。

## 当前阶段

当前进入：

```text
Enterprise Evidence Layer V1
```

背景：

Approval Runtime Sample 和 Cognitive Foundation V1 已建立，但系统仍缺少通用 Evidence Layer。

没有 Evidence Layer，AI 会直接面对原始业务系统数据，例如飞书表单 widget、OCR 片段、附件残缺文本；管理者也会看到技术噪音。这会让 AI OS 退化成“把复杂性转交给人”。

## 当前目标

建立 AI OS 的通用证据层：

```text
Raw System Data
-> Evidence
-> Snapshot
-> Interaction
-> Runtime Action
```

先以 Approval 作为第一条样板验证：

- Evidence Contract。
- Approval Form Evidence。
- Approval Attachment Evidence。
- Approval Expense Evidence。
- Snapshot Builder 消费 Evidence，而不是 raw widget form。
- Portal / Card 展示 Evidence Summary，不展示技术解析噪音。
- Detail / Portal 支持展开 Evidence 明细，尽量在 AI OS 内完成核对和处理，不默认跳回飞书原生页面。

## 当前禁止范围

本阶段不要做：

- 新数据库表。
- Event Bus / Replay / Subscription。
- Workflow Engine。
- Memory Engine。
- Insight Engine。
- 跨业务全量实现。
- Task / Mail / Meeting / Customer 业务开发。
- Batch Approval。
- Transfer / AddSign。
- Diagnostics / Observability 重构。
- 把 Snapshot 当业务缓存。
- 把审批专属逻辑硬编码成系统边界。

## 当前验收标准

- AI OS 层面能区分 Raw Data、Evidence、Snapshot。
- 管理者不再看到 `widget...`、JSON 结构异常、OCR 原始噪音。
- 技术解析失败被表达为 Evidence quality，不直接等同业务高风险。
- Snapshot 原因来自 Evidence Summary。
- Interaction 展示 Live Data + Evidence Summary + Snapshot Judgment。
- 管理者能直接知道：能不能处理、缺什么、下一步怎么做。
- 管理者能在详情页核对费用明细和附件基本情况，用于判断证据冲突。

## 下一步计划

1. 冻结 Evidence Contract V1。
2. 实现 Approval Form Normalizer V0。
3. 实现 Approval Expense Evidence Builder V0。
4. 让 Approval Snapshot Builder 消费 Evidence。
5. 让 Portal Detail 展示 Evidence Summary。
6. 让 Portal Detail 展开费用明细和附件基本情况。
