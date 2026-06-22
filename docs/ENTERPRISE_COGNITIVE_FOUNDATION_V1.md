# Enterprise Cognitive Foundation V1

架构归属：`V5_RUNTIME_CONSTITUTION.md` 中的 Cognitive Engine。

本文档不是独立架构总图。它只定义 Cognitive Engine V1 内部的认知数据模型。若本文与 `V5_RUNTIME_CONSTITUTION.md` 冲突，以 Constitution 为准。

V1 冻结结论见 `docs/ECF_V1_FREEZE_REVIEW.md`。

本文档定义 Digital Advisor 企业认知系统的最小闭环。Approval 是第一条样板链路，但本阶段目标不是做审批专属缓存，而是建立可复制到 Task / Meeting / Customer 的认知数据底座。

## 0. Engine Boundary

Cognitive Engine 负责：

```text
Operational Data
-> WorkEvent
-> Evidence
-> Snapshot
-> Insight
```

其中：

- WorkEvent：事实层。
- Evidence：判断依据层。
- Snapshot：当前认知层。
- Insight：建议层。
- Profile / Style / Preference：用户风格、角色画像、偏好快照。

Cognitive Engine 不负责执行动作。Action 继续归 Runtime Engine。

## 1. 目标

建立企业认知最小闭环：

```text
Raw Data
-> WorkEvent
-> Evidence
-> Snapshot
-> Insight
```

V1 代码中的最小落地允许 Evidence 和 Insight 先作为 Snapshot payload / RuntimeResult metadata 的派生结构，不引入 Evidence Engine、Insight Engine 或 Insight Store。

核心职责：

- WorkEvent：事实层，记录已经发生或已被系统观察到的事实。
- Evidence：判断依据层，保存系统为什么得出判断的可解释依据。
- Snapshot：当前认知层，保存系统对某个业务对象的最新判断。
- Insight：建议层，输出 Recommendation，不负责执行。
- MemoryCandidate：长期记忆候选层，保存可能值得沉淀的组织模式。
- Profile / Style / Preference：属于 Cognitive Engine，但 V1 只冻结边界，不实现画像引擎。

Approval 查询必须从 Snapshot 读取 AI 判断。附件未完成或 Snapshot 未完成时，Bot / Card / Portal / SidePanel 只能展示“分析中”，不得展示不完整 AI 建议。

## 2. WorkEvent Model

标准字段：

```text
id
company_id
event_type
object_type
object_id
source
actor
payload
created_at
```

语义规则：

- WorkEvent 是 append-only，不允许覆盖。
- 每个事件必须携带 `company_id`。
- `object_type + object_id` 指向业务对象，例如 `approval + instance_id`。
- `source` 表示事件来源，例如 `feishu_event`、`bot_query_discovered`、`attachment_processor`、`ai_analysis`、`runtime_action`。
- `actor` 表示触发者或系统执行身份，可以是用户 open_id、bot、system、provider。
- `payload` 保存事实原文和必要上下文，不保存展示状态。

Approval V1 事件类型：

```text
approval_created
attachment_processed
approval_analysis_completed
approval_approved
approval_rejected
```

现状约束：

- 代码中已有 `work_events` 表和 `upsert_work_event` 服务，但当前语义偏资源同步和向量化，并且存在按 external_id 更新旧事件的行为。
- ECF V1 允许复用已有表和基础设施，但新增认知链路必须使用 append-only 写入语义。
- 不允许为了 Approval Snapshot 引入审批专属 `approval_snapshots` 表。

## 3. Snapshot Model

标准字段：

```text
id
company_id
object_type
object_id
snapshot_type
status
summary
recommendation
risk_level
reasons
source_event_ids
updated_at
```

语义规则：

- Snapshot 保存系统对业务对象的当前认知状态。
- Snapshot 只保存 AI Cognitive State，不保存业务对象状态。
- Approval Snapshot 不是审批缓存；审批列表、审批状态、申请人、金额、审批详情必须来自 Feishu live data。
- 最终展示由 Live Approval Data 与 Approval Snapshot 合并输出。
- Snapshot 可以更新，WorkEvent 不可更新。
- `status` 至少支持 `pending_analysis`、`analysis_running`、`completed`、`failed`。
- `recommendation` 是给业务用户看的建议，例如 `可通过`、`需关注`、`高风险`。
- `risk_level` 是稳定枚举，例如 `pass`、`review`、`high`。
- `reasons` 必须来自已完成分析，不得基于未完成附件生成。
- `source_event_ids` 必须指向生成该 Snapshot 的 WorkEvent。
- Snapshot payload 只允许保存 AI 分析结果或认知状态，不允许保存审批状态快照。

Approval Snapshot 读取规则：

```text
Snapshot 不存在 -> 分析中
Snapshot.status != completed -> 分析中
Snapshot.status == completed -> 展示 recommendation / risk_level / reasons
```

禁止 fallback 到实时 AI 判断。否则 Snapshot 层失去权威性。
Bot Query 不允许同步读取附件或执行 AI 分析；只能合并 Feishu live data 与已有 Snapshot。
Snapshot Builder 是本阶段唯一允许生成 completed Approval Snapshot 的组件。
Snapshot 生成节奏由 `docs/SNAPSHOT_TRIGGER_MATRIX.md` 定义；Query 和页面打开不得触发 Snapshot Builder。

## 3.1 Evidence Model

Evidence 是判断依据层，不是展示缓存，也不是审批详情副本。

建议字段：

```text
id
company_id
object_type
object_id
evidence_type
quality
summary
facts
conflicts
missing_items
source_event_ids
created_at
```

语义规则：

- Evidence 必须继承来源对象权限。
- Evidence 可以来自附件解析、表单字段解析、OCR、规则校验、历史模式或人工补充。
- Evidence 用于解释 Snapshot 和 Insight，不直接决定 Action。
- Evidence 不保存无关原始附件全文，只保存判断所需的结构化依据和引用。
- Evidence 细节必须经过 Unified Policy Result Filter 后才能展示。

## 3.2 Insight Model

Insight 是认知系统输出层，语义等于 Recommendation。

建议字段：

```text
id
company_id
insight_type
severity
title
summary
recommendation
evidence_refs
snapshot_refs
memory_refs
scope
created_at
```

语义规则：

- Insight 不负责执行。
- Action 继续归 Runtime。
- Insight 可以被 Runtime 用作建议输入，但不能绕过 Policy 或确认。
- Insight 可以聚合到管理视角，但必须脱敏，不得暴露无权限来源对象。
- V1 不实现 Insight Store；Insight 可先作为 RuntimeResult / Snapshot metadata 的派生输出。

## 4. MemoryCandidate Model

标准字段：

```text
id
company_id
memory_type
object_type
object_id
evidence_event_ids
candidate_text
confidence
status
created_at
```

语义规则：

- MemoryCandidate 只是候选，不是正式长期记忆。
- 本阶段只写入候选，不做 Memory Engine。
- `status` 至少支持 `candidate`、`accepted`、`rejected`，V1 只写 `candidate`。
- `evidence_event_ids` 必须指向支持该候选的 WorkEvent。
- `confidence` 表示候选可信度，不表示系统已经确认该模式。

Approval 可生成的候选示例：

- 某员工多次缺附件。
- 某供应商多次付款异常。
- 某部门经常超预算。

V1 不做跨对象聚合结论，只允许写入低风险候选记录。

## 5. Approval Cognitive Lifecycle

目标链路：

```text
Approval Raw Data
-> WorkEvent(approval_created)
-> Attachment Processed
-> WorkEvent(attachment_processed)
-> AI Analysis
-> WorkEvent(approval_analysis_completed)
-> Snapshot(approval current judgment)
-> Bot / Card / Portal / SidePanel read Snapshot
```

状态规则：

```text
approval_created -> pending_analysis
attachment_processed -> analysis_running
approval_analysis_completed -> snapshot completed
bot_query -> read snapshot
```

动作事件：

```text
Runtime approve -> WorkEvent(approval_approved)
Runtime reject -> WorkEvent(approval_rejected)
```

动作事件只记录事实，不反向决定 Snapshot 建议。

## 6. Read Flow

Bot 查询审批时：

1. 查询 Approval Raw Data。
2. 为新发现的审批写入 `approval_created` WorkEvent，source 可为 `bot_query_discovered`。
3. 读取 Approval Snapshot。
4. 若 Snapshot 不存在或未完成，展示“分析中”。
5. 若 Snapshot completed，展示 `recommendation`、`risk_level`、`reasons`。

Interaction Layer 只能消费 Snapshot 派生出的 RuntimeResult / InteractionPayload，不得自己解释附件状态或生成 AI 建议。

## 7. Write Flow

V1 不引入 WorkEvent Engine。允许实现一个 Approval 样板写入器：

```text
ensure_approval_snapshot(...)
```

允许职责：

- 写入 append-only Approval WorkEvent。
- 检查附件处理是否完成。
- 触发 AI Analysis。
- 写入 `approval_analysis_completed` WorkEvent。
- upsert 标准 Snapshot。
- 可选写入 MemoryCandidate。

禁止职责：

- Event Bus。
- Replay。
- Subscription。
- Workflow Engine。
- 跨业务路由。
- 跨公司聚合。

## 8. Out Of Scope

本阶段禁止：

- WorkEvent Engine。
- Event Bus。
- Replay。
- Subscription。
- Workflow Engine。
- Memory Engine。
- Insight Engine。
- 跨公司聚合。
- Mail Snapshot。
- Meeting Snapshot。
- Customer Snapshot。
- Task Snapshot。
- Approval 专属 Snapshot 表。
- Bot / Card / Portal 直接实时生成 AI 建议。

## 9. Acceptance Criteria

Approval 查询不再基于附件未完成时的实时分析。

正确结果：

```text
附件未完成 -> 分析中
AI 分析未完成 -> 分析中
Snapshot completed -> 可通过 / 需关注 / 高风险 + 原因
```

验收要求：

- WorkEvent 写入是 append-only。
- Approval Snapshot 是 Bot 展示 AI 判断的唯一来源。
- Snapshot 未完成时不得展示不完整建议。
- MemoryCandidate 只写候选，不晋升正式 Memory。
- 所有三层数据必须携带 `company_id`。
- 不新增审批专属表。
