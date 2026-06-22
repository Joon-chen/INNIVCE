# Current Mission

本文档只回答：现在在做什么。

最高级架构规则见 `docs/V5_RUNTIME_CONSTITUTION.md`。

## 当前阶段

```text
Policy Filter Contract Freeze Review
```

## 当前目标

冻结 Unified Policy Result Filter V0 合同。

确认 Operational Data + Cognitive Data 混合结果在进入 Interaction 前统一裁剪。

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

## 当前验收标准

- Policy Preflight 输出 subject / scope / identity decision。
- RuntimeResult 生成前执行 Result Filter。
- RuntimeResult items 是过滤后的副本。
- RuntimeResult actions 基于过滤后的 items 生成。
- Cognitive item 在 company / department / team scope 下隐藏来源引用。
- Denied resource item 不进入 InteractionPayload。

## 下一步计划

```text
Workspace Query Provider Completion
```

建议下一步：

- 补 Workspace 的 Bot/Tenant 实时查询 Provider 能力评估。
- 明确哪些 Task / Calendar 查询可由 Bot/Tenant 读取。
- 对读不到的范围返回能力未接入，而不是走 User 或本地认知代查。
