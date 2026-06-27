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
| `ENTERPRISE_COGNITIVE_FOUNDATION_V1.md` | ACTIVE | Cognitive Engine V1 |
| `CURRENT_MISSION.md` | ACTIVE | 当前任务 |

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
