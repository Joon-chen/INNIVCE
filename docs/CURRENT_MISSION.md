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
- Command LLM 已从低置信补救扩展为受控语义增强：可补充业务域、能力、目标、约束、时间和输出偏好。
- 高置信业务规则命中时，LLM 只能 enrich 同一 intent；低置信或通用意图才允许改成更具体 intent。
- Command Enrichment 已进入 RuntimeResult metadata，并被 Composer 用于查询类结果的目标 / 视图 / 关注点表达。
- LLM Capability Architecture 已冻结为 Command / Reasoning / Conversation / Presentation / External Research 五个受控能力位。
- External Research 已定义为 Policy 管控的外部信息补充能力，不是自由浏览器。
- ExternalResearchPolicy / Request / Result / SourceReference V0 合同已冻结。
- Architecture Documentation Consolidation 已完成：总宪法成为唯一架构总图，ACTIVE / FROZEN / ARCHIVED 文档职责已收口。
- Conversation LLM V0 已接入闲聊 / 边界解释 / 引导补充路径，不读取业务数据、不执行动作、不改变权限。
- 低置信 LLM 候选已支持引导式 clarification payload。
- Policy Result Filter 已接入 RuntimeResult。
- Workspace operational items 已标注 PolicyResource 元数据。
- Cognitive items 已标注 `resource_plane=cognitive` 和继承可见范围。
- Workspace Query 已阻断错误的 Tenant 空结果语义。
- SELF Workspace Query 已改为显式 USER_TOKEN fallback。
- Workspace Cognitive Projection Contract 已冻结。
- Workspace Aggregation Summary V0 已固定六个管理指标。
- Workspace Cognitive Sync Acceptance 已建立最小验收链路：授权观察项 -> Projection WorkEvent -> Aggregation Summary。
- Workspace Cognitive Query Integration 已开始：企业 Task / Calendar 实时未接入时，可返回已授权观察数据生成的认知聚合。
- SELF Task / Calendar 查询成功后会写入 Workspace Cognitive Projection，作为后续部门 / 公司聚合原料。
- Workspace 聚合已处理飞书 `1970-01-01` 完成时间哨兵，避免把未完成任务误判为完成。
- V5 Bot 回答已开始接入 Profile / LLM Answer Rewrite，但动作确认、授权、诊断类答案不改写。

## 当前禁止范围

- 不新增 Engine。
- 不新增 Foundation。
- 不新增 Runtime Layer。
- 不新增 Architecture V2。
- 不新增 Policy V2。
- 不新增设计文档。
- 不做 UI Migration。
- 不接新 Provider。
- 不实现 External Research Provider。
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
- Workspace 授权观察项只能写入认知投影，不得写入完整任务 / 日程明细。
- 企业 Task / Calendar 查询返回认知聚合时，必须明确标记不是 Feishu 实时明细。
- Workspace 聚合必须按对象取最新投影，不能因为 WorkEvent append-only 重复计数。
- 部门 / 公司查询读取认知聚合时必须经过 Policy Result Filter。
- USER_TOKEN 只作为观察/执行身份，不作为越权依据。

## 下一步计划

```text
Workspace Cognitive Query Integration
```

建议下一步：

- 用真实已授权用户的 Task / Calendar 观察数据产生 Projection。
- 在飞书 Bot 中验证“全公司任务 / 全公司日程”能返回认知聚合。
- 继续让 Command Enrichment 驱动后续 Result layout / InteractionPayload，而不是只影响文本表达。
- 若进入联网能力，先做 External Research Provider 接入评审，不直接实现自由联网。
- 接入 Workspace Evidence / Snapshot / Insight 前先完成投影验收。
