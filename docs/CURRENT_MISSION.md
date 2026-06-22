# Current Mission

本文档只回答：现在在做什么。

最高级架构规则见 `docs/V5_RUNTIME_CONSTITUTION.md`。

## 当前阶段

```text
Unified Policy Engine V0 Implementation Planning
```

## 当前目标

把 Unified Policy Engine V0 从设计文档推进到最小可实施计划。

第一条样板选择 Workspace：

- `task_query`
- `calendar_query`
- `task_create`
- `calendar_create`

## 当前已完成

- 架构文档已收口到 V5 五层总图。
- Query identity 已冻结为 BOT/TENANT first。
- USER_TOKEN 只允许作为 SELF personal resource 的受控 fallback。
- company / department / team Query 不允许当前用户 USER_TOKEN 代查。
- Task create / Calendar create 写入已走 Runtime controlled write。
- Policy 实施计划已并入 `UNIFIED_POLICY_ENGINE_V0_DESIGN.md`。

## 当前禁止范围

- 不新增 Policy V2。
- 不新增数据库表。
- 不实现完整 ACL DSL。
- 不做 Policy UI。
- 不接新的飞书 Provider。
- 不用本地认知数据伪装企业实时读取。
- 不允许 Query 默认走 USER_TOKEN。

## 当前验收标准

- Policy Preflight 输出 subject / scope / identity decision 摘要。
- Workspace Query 默认 BOT/TENANT first。
- SELF Query 可标记 USER fallback，非 SELF Query 不允许当前用户 fallback。
- 企业范围 Provider 未接入时返回“企业实时读取能力未授权/未接入”。
- RuntimeResult 后续只输出 Policy 过滤后的混合结果。

## 下一步计划

```text
Workspace Policy Preflight Contract Test
```

先补 Contract Test，再做最小代码：

- `policy_subject` metadata。
- `policy_scope` metadata。
- `identity_decision` metadata。
- Workspace company query 未接入返回明确原因。
