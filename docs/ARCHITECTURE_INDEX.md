# Architecture Index

本文档是 V5 架构文档索引，不是新的架构定义。

若本文与 `V5_RUNTIME_CONSTITUTION.md` 冲突，以 Constitution 为准。

## Architecture Convergence Phase

从 V5 Freeze 起，AI OS 进入 Architecture Convergence Phase。

新增问题必须先归类到已有 Architecture，而不是先设计新模块。

当前冻结架构：

```text
Foundation
- Business Domain Taxonomy
- Capability Registry
- Skill Registry
- Provider Registry
- Identity & Scope
- Context Store
- Organization Foundation
- Semantic Protocol

Core Engines
- Command Engine
- Policy Engine
- Runtime Engine
- Cognitive Engine

Interface
- Interaction Layer
- Provider Layer

Observability
- Diagnostics
- Audit
- Telemetry
```

新增系统模块只有在同时满足以下条件时才允许：

- 已有模块无法承担职责。
- 职责具有长期稳定性。
- 至少两个以上业务域都会依赖。

否则必须放回已有模块。

示例归类：

- People 查询错误：Command Engine / Organization Resolver。
- 权限问题：Policy Engine。
- 组织关系：Organization Foundation。
- LLM 表达：Response Orchestrator。
- Provider 调用问题：Provider Layer。
- 飞书同一会话消息乱序：Interface / Gateway 入口顺序控制，不属于 Command Engine 路由问题。

## Status

- ACTIVE：当前系统合同的一部分。
- FROZEN：阶段结论或样板已冻结，可参考但不继续扩展。
- ARCHIVED：历史文档，已被 ACTIVE 文档吸收。

## Active Documents

| Document | Status | Role |
| --- | --- | --- |
| `V5_RUNTIME_CONSTITUTION.md` | ACTIVE | 唯一架构总图 |
| `ORGANIZATION_FOUNDATION_V1.md` | ACTIVE | Foundation: 组织事实层 |
| `UNIFIED_POLICY_ENGINE_V0.md` | ACTIVE | 唯一权限系统 |
| `CAPABILITY_REGISTRY_MODEL.md` | ACTIVE | Foundation: Domain -> Capability -> Skill -> Provider |
| `app/services/semantic_protocol/` | ACTIVE | Foundation Contract: AI OS 统一语义协议 |
| `ENTERPRISE_COGNITIVE_FOUNDATION_V1.md` | ACTIVE | Cognitive Engine V1 |
| `CURRENT_MISSION.md` | ACTIVE | 当前任务 |

## Cognitive Foundation V1.2 Freeze

Cognitive V1.2 仍属于 Cognitive Engine，不新增 Memory 系统，不新增 Business Domain，也不新增 Provider。

冻结链路：

```text
Source Systems
-> Shared File Intelligence
-> Evidence Pack
-> Evidence Builder
-> WorkEvent (Evidence Carrier)
-> Extractor Registry
-> Candidate
-> Snapshot Builder
-> Snapshot
-> Runtime
```

Snapshot V1.1 结构固定为：

```text
identity
structured
understanding
evidence_refs
confidence
version
snapshot_status
derived_from
```

Evidence Pack 是通用证据结构，字段固定为：

```text
source_ref
organization_binding
visibility_binding
content_profile
outline
key_claims
entities
topics
relations
metrics
time_refs
evidence_spans
uncertainties
quality
derived_from
```

Evidence Pack 禁止出现 `company_* / customer_* / project_*` 业务字段；这些解释只能进入对应 Extractor Candidate。

## Compatibility Notes

| Document | Status | Role |
| --- | --- | --- |
| `UNIFIED_POLICY_ENGINE_V0_DESIGN.md` | ARCHIVED | 已被 `UNIFIED_POLICY_ENGINE_V0.md` 吸收 |

## Freeze Rules

- 新增问题先归类到已有模块，禁止默认新增 Engine、Foundation、Pipeline 或 Contract。
- Organization Foundation 是唯一组织事实来源。
- Policy Engine 是唯一权限系统。
- Operational Filter 和 Cognitive Filter 都属于 Policy Engine。
- Runtime、Cognitive Engine、LLM 只能消费 Policy Filter 之后的数据。
- People Domain 不等同于通讯录。
- Contact Directory 只是 Organization Foundation 的数据集。
- Semantic Schema 属于 Foundation Contract；LLM 只是 SemanticFrame Producer 之一。
- Semantic Protocol 是 Foundation Contract，不是 Foundation Engine，不包含业务知识、关键词入口或业务域规则。
- 迁移到 Conversation First V1 的域不得再由旧 Intent 规则抢路由；未迁移能力只能作为 legacy island 兼容。
- 自学习必须通过 Trace、Regression、Schema/Alias/Prompt 更新和发布流程完成，禁止线上自动新增路由规则。
- Command Engine 修复必须优先落在 Semantic Understanding、Dialogue Resolver、Context Contract 或 Response Orchestrator，不得以飞书测试短句为单位新增业务域入口。
- Interface / Gateway 必须保持同一用户会话内消息顺序；异步执行可以跨会话并行，但不得让同一 `chat_id` 的后发消息先完成并污染上下文或可见回复顺序。
- Cognitive Extractor 只能输出 Candidate，Snapshot 只能由 Snapshot Builder 融合生成。
- Cognitive V1.1 不设计 MemoryCandidate / MemoryFact / Memory Pipeline；长期 Memory 属于 V2。
- Snapshot 不得变成第二个 Knowledge Database；开放问答依赖一份 `understanding`，精确回答依赖 `structured`。
- Evidence Pack 是共享认知基础服务的一部分，不得在 Company、Knowledge、Mail、IM、Approval 各自复制一套。
- 通讯录和组织架构属于 Organization Foundation 基础数据；数字标识、工号、open_id 不得被展示为人员姓名或负责人姓名。
