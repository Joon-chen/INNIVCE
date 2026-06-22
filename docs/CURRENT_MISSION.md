# Current Mission

本文档只回答：现在在做什么。

最高级架构规则见 `docs/V5_RUNTIME_CONSTITUTION.md`。

## 当前阶段

```text
Architecture Documentation Consolidation Phase
```

## 当前目标

合并、整理、冻结 V5 架构文档。

当前架构总图冻结为：

```text
Foundation
-> Core Engines
-> Interface
-> Observability
-> Business Domains
```

Core Engines：

```text
Command
Policy
Runtime
Cognitive
Capability Registry
```

## 当前已完成

- `V5_RUNTIME_CONSTITUTION.md` 明确唯一架构总图。
- `V5_RUNTIME_CONSTITUTION.md` 明确 Architecture Index。
- `ENTERPRISE_COGNITIVE_FOUNDATION_V1.md` 收口到 WorkEvent / Evidence / Snapshot / Insight。
- `UNIFIED_POLICY_ENGINE_V0_DESIGN.md` 明确统一实时数据和认知数据权限。
- `CAPABILITY_REGISTRY_MODEL.md` 明确 Registry 是能力元数据控制面，不属于 Diagnostics。
- Command LLM Intent V0 已完成低置信引导式对话边界验证。
- Task create 写入已收敛到 Runtime controlled write。

## 当前禁止范围

- 不新增 Engine。
- 不新增 Foundation。
- 不新增 Runtime Layer。
- 不新增 Architecture V2。
- 不新增 Policy V2。
- 不新增数据库表。
- 不重构 UI。
- 不扩展业务能力。

## 当前验收标准

- 新成员只看 `V5_RUNTIME_CONSTITUTION.md` 即可理解 V5 五层结构。
- ACTIVE / FROZEN / ARCHIVED 文档状态明确。
- 重复概念已归并到现有文档。
- `CURRENT_MISSION.md` 保持短文档，只描述当前任务。

## 下一步计划

```text
Unified Policy Engine V0 Implementation Planning
```

建议先选择 Workspace 作为第一条 Policy 打通样板：

- Query identity：BOT/TENANT first，USER_TOKEN 只做受控个人 fallback。
- Scope：SELF / USER / TEAM / DEPARTMENT / COMPANY。
- Mixed Result Filter：Operational + Cognitive 统一裁剪。
- RuntimeResult：只输出 Policy 过滤后的结果。
