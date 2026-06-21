# Current Mission

本文档只回答：现在在做什么。

系统最高级规则见 `docs/V5_RUNTIME_CONSTITUTION.md`。
企业 AI OS 顶层定义见 `docs/ENTERPRISE_AI_OS_V1.md`。
企业认知底座 V1 见 `docs/ENTERPRISE_COGNITIVE_FOUNDATION_V1.md`。
Snapshot 生成节奏见 `docs/SNAPSHOT_TRIGGER_MATRIX.md`。
OA 智能闭环设计见 `docs/OA_INTELLIGENCE_LOOP_DESIGN.md`。
历史阶段归档见 `docs/history/`。

## 当前阶段

当前进入：

```text
OA Intelligence Loop Design Phase
```

背景：

Approval Runtime Sample、Cognitive Foundation V1 和 Approval Evidence 样板已建立。

下一步不是把审批继续做成单点工具，而是明确 Digital Advisor 如何通过飞书 OA 原始数据长期沉淀企业认知，并把认知反哺到员工日常 OA 工作中。

关键调整：

Action 不直接由 AI 或 Snapshot 驱动，而是由 Insight 驱动。

## 当前目标

建立 AI OS 的通用智能闭环：

```text
OA Raw Data
-> WorkEvent
-> Evidence
-> Snapshot
-> MemoryCandidate
-> Insight
-> Action
```

本阶段目标：

- 定义 OA Intelligence Loop。
- 明确 Insight 与 Evidence / Snapshot / MemoryCandidate / Action 的边界。
- 明确 `Insight -> Action` 是动作前置原则。
- 明确 AI OS 优先在 Bot + Detail View + Management Portal 内闭环。
- 保持 Approval 只是第一条样板，不把审批变成系统边界。

## 当前禁止范围

本阶段不要做：

- 新数据库表。
- Event Bus / Replay / Subscription。
- Workflow Engine。
- Memory Engine。
- Insight Engine。
- 跨业务 Action 实现。
- 跨业务全量实现。
- Task / Mail / Meeting / Customer 业务开发。
- Batch Approval。
- Transfer / AddSign。
- Diagnostics / Observability 重构。
- 把 Snapshot 当业务缓存。
- 把审批专属逻辑硬编码成系统边界。

## 当前验收标准

- AI OS 层面能区分 Raw Data、Evidence、Snapshot。
- AI OS 层面能区分 Evidence、Snapshot、Insight、Action。
- 管理者不再看到 `widget...`、JSON 结构异常、OCR 原始噪音。
- 技术解析失败被表达为 Evidence quality，不直接等同业务高风险。
- Snapshot 原因来自 Evidence Summary。
- Interaction 展示 Live Data + Evidence Summary + Snapshot Judgment。
- 管理者能直接知道：能不能处理、缺什么、下一步怎么做。
- 管理者能在详情页核对费用明细和附件基本情况，用于判断证据冲突。
- Action 必须由 Insight 驱动，不能直接由 raw data 或 snapshot 驱动。

## 下一步计划

1. 冻结 OA Intelligence Loop Design。
2. 定义 Insight Contract V0。
3. 定义 Approval Insight Sample。
4. 明确 Insight 如何驱动 Runtime Action。
5. 再决定是否进入 Insight Engine V0。
