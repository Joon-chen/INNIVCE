# Context Contract Phase Archive

本文档回答：过去 company_id 上下文合同如何收口。

## 目标

Runtime 主链路必须显式携带公司上下文，禁止隐式默认公司混用。

当前收口范围：

- `RuntimeContext`
- `RuntimeActionInput`
- `RuntimePendingAction`
- `RuntimeTaskState`
- `RuntimeActionState`
- `RuntimeResult`
- `InteractionPayload`

明确暂缓：

- Memory
- WorkEvent
- Provider Snapshot
- Diagnostics

## Context Spine

已冻结链路：

```text
RuntimeContext
-> RuntimeActionInput
-> RuntimePendingAction
-> RuntimeTaskState / RuntimeActionState
-> RuntimeResult
-> InteractionPayload
```

## 已完成规则

- `RuntimeContext` 由 Policy Layer 阻断无 active `company_id` 的 single-company 执行。
- `RuntimeActionInput` producer builder 强制 `context.company_id`。
- 缺少 `RuntimeActionInput.context.company_id` 时，Runtime 返回 failed `runtime_action`，不进入 pending / confirmed / executing，不执行 Provider。
- legacy action request 兼容路径只透传显式 `company_id`，不做默认公司推断。
- `RuntimePendingAction` builder 强制 `company_id`。
- `RuntimeTaskState.metadata.company_id` 必须存在。
- `RuntimeActionState.metadata.company_id` 必须存在。
- Runtime State retrieval 恢复 pending action 时必须显式补 `company_id`。
- `RuntimeResult.metadata.company_id` 为顶层合同字段。
- `InteractionPayload` 只消费 RuntimeResult，并继承 `metadata.company_id`。

## State Company Assertion

状态写入必须包含：

- waiting input task metadata company_id
- waiting input action metadata company_id
- waiting confirmation task metadata company_id
- waiting confirmation action metadata company_id
- done / failed 终态继承 company_id

状态恢复优先级：

1. `pending_action.company_id`
2. `pending_action.runtime_action_input.context.company_id`
3. `RuntimeActionState.metadata.company_id`
4. `RuntimeTaskState.metadata.company_id`

恢复过程不修改 nested `runtime_action_input.context.company_id`。

## 明确未做

本阶段没有新增：

- `RuntimeStateTransitionInput`
- 状态 repository
- 数据库
- Workflow Engine
- Diagnostics 改造

当时判断：风险来自 company_id 在状态层缺少显式断言，而不是缺少新的状态输入合同。
