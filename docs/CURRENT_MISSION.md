# Current Mission

本文档只回答：现在在做什么。

最高级架构规则见 `docs/V5_RUNTIME_CONSTITUTION.md`。

## 当前阶段

```text
Architecture Documentation Consolidation Phase
```

## 当前目标

合并、整理、冻结 V5 架构文档入口。

新成员只看 `V5_RUNTIME_CONSTITUTION.md`，应能理解：

- Foundation
- Core Engines
- Interface
- Observability
- Business Domains

## 当前已完成

- V5 五层结构已冻结。
- Business Domain 已冻结为 People / Communication / Workspace / Process / Knowledge / Business / Intelligence。
- Capability Registry 归入 Foundation。
- Command / Policy / Runtime / Cognitive 归入 Core Engines。
- Profile / Style / Preference 归入 Cognitive Engine。
- Policy 独立为 Engine，V0 可同仓同进程实现。
- Command LLM Intent 已定义为结构化候选 + Validator。
- 低置信 LLM 候选已支持引导式 clarification payload。

## 当前禁止范围

- 不新增 Engine。
- 不新增 Foundation。
- 不新增 Runtime Layer。
- 不新增 Architecture V2。
- 不新增 Policy V2。
- 不新增设计文档。
- 不改 Runtime 执行业务逻辑。
- 不做 UI Migration。
- 不接新 Provider。

## 当前验收标准

- `V5_RUNTIME_CONSTITUTION.md` 是唯一架构总图。
- Architecture Index 标记 ACTIVE / FROZEN / ARCHIVED。
- 文档归档清单明确哪些文档只作历史参考。
- 重复概念清单明确旧概念的 canonical concept。
- `CURRENT_MISSION.md` 保持短文档，只回答当前任务。

## 下一步计划

```text
Workspace Policy Result Filter Skeleton
```

建议下一步只做最小合同验证：

- Policy Preflight 已给出 subject / scope / identity decision。
- Result Filter 在 RuntimeResult 生成前裁剪 Operational + Cognitive 混合结果。
- 先用 Workspace Query 做样板，不做完整 ACL 引擎。
