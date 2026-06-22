# Current Mission

本文档只回答：现在在做什么。

系统唯一架构总图见 `docs/V5_RUNTIME_CONSTITUTION.md`。

## 当前阶段

```text
Architecture Documentation Consolidation Phase Completed
```

## 当前目标

合并、整理、冻结 V5 AI OS 架构文档。

本阶段只更新：

- `V5_RUNTIME_CONSTITUTION.md`
- `ENTERPRISE_COGNITIVE_FOUNDATION_V1.md`
- `UNIFIED_POLICY_ENGINE_V0_DESIGN.md`
- `CAPABILITY_REGISTRY_MODEL.md`
- `CURRENT_MISSION.md`

## 当前冻结架构

```text
Foundation Layer
Core Engines
Interface Layers
Provider Layer
Observability Layer
```

Core Engines：

```text
Command Engine
Policy Engine
Runtime Engine
Cognitive Engine
```

Business Domains：

```text
People
Communication
Workspace
Process
Knowledge
Business
Intelligence
```

## 当前禁止范围

- 不新增 Engine。
- 不新增 Foundation。
- 不新增 Runtime Layer。
- 不新增 Architecture V2。
- 不新增 Policy V2。
- 不新增散乱架构文档。
- 不改 Runtime 代码。
- 不改业务逻辑。
- 不改数据库。

## 当前验收标准

- 新成员只看 `V5_RUNTIME_CONSTITUTION.md` 可以理解五层结构。
- `V5_RUNTIME_CONSTITUTION.md` 包含唯一架构总图。
- `V5_RUNTIME_CONSTITUTION.md` 包含 Architecture Index。
- `V5_RUNTIME_CONSTITUTION.md` 标记 ACTIVE / FROZEN / ARCHIVED。
- `V5_RUNTIME_CONSTITUTION.md` 包含文档归档清单。
- `V5_RUNTIME_CONSTITUTION.md` 包含重复概念清单。

## 下一步计划

```text
Workspace Cognitive Policy Sample Review
```

目标：

- 以 Workspace 作为第一条跨域认知 + Policy 样板。
- 先评审，不直接实现。
- 验证 Workspace 如何复用：
  - Business Domain。
  - Capability Registry。
  - Command Engine。
  - Policy Engine。
  - Runtime Engine。
  - Cognitive Engine。
