# Current Mission

本文档只回答：现在在做什么。

最高级架构规则见 `docs/V5_RUNTIME_CONSTITUTION.md`。

## 当前阶段

```text
Workspace Cognitive Aggregation V0
```

## 当前目标

冻结 Workspace 部门 / 公司聚合查询的最小认知输出。

个人任务 / 日程明细仍归 Operational Data；部门 / 公司管理视角读取 Cognitive Projection 生成的 Aggregation Summary。

## 当前已完成

- V5 五层结构已冻结。
- Business Domain 已冻结为 People / Communication / Workspace / Process / Knowledge / Business / Intelligence。
- Capability Registry 归入 Foundation。
- Command / Policy / Runtime / Cognitive 归入 Core Engines。
- Profile / Style / Preference 归入 Cognitive Engine。
- Policy 独立为 Engine，V0 可同仓同进程实现。
- Command LLM Intent 已定义为结构化候选 + Validator。
- 低置信 LLM 候选已支持引导式 clarification payload。
- Policy Result Filter 已接入 RuntimeResult。
- Workspace operational items 已标注 PolicyResource 元数据。
- Cognitive items 已标注 `resource_plane=cognitive` 和继承可见范围。
- Workspace Query 已阻断错误的 Tenant 空结果语义。
- SELF Workspace Query 已改为显式 USER_TOKEN fallback。
- Workspace Cognitive Projection Contract 已冻结。
- Workspace Aggregation Summary V0 已固定六个管理指标。

## 当前禁止范围

- 不新增 Engine。
- 不新增 Foundation。
- 不新增 Runtime Layer。
- 不新增 Architecture V2。
- 不新增 Policy V2。
- 不新增设计文档。
- 不做 UI Migration。
- 不接新 Provider。
- 不做完整 ACL Engine。
- 不新增权限数据库表。
- 不同步完整个人任务 / 日程明细到 WorkEvent。
- 不用 Cognitive Projection 冒充 Feishu realtime data。
- 不把 Aggregation Summary 当作明细数据源。

## 当前验收标准

- Workspace Cognitive Projection 只保存允许字段。
- 投影明确标记 `operational_detail_stored=false`。
- 个人明细字段不得进入 WorkEvent。
- Workspace Aggregation Summary 固定输出 `task_total` / `overdue_task_count` / `due_soon_task_count` / `calendar_conflict_count` / `meeting_occupied_minutes` / `workload_buckets`。
- Workspace Aggregation Summary 必须标记 `detail_available=false`。
- Workspace Aggregation Summary 不包含任务 / 日程运营明细。
- 部门 / 公司查询读取认知聚合时必须经过 Policy Result Filter。
- USER_TOKEN 只作为观察/执行身份，不作为越权依据。

## 下一步计划

```text
Workspace Cognitive Sync Acceptance
```

建议下一步：

- 用 Task / Calendar 授权数据生成 Workspace Cognitive Projection。
- 验证部门 / 公司可读 Aggregation Summary，不可读无权限明细。
- 接入 Workspace Evidence / Snapshot / Insight 前先完成投影验收。
