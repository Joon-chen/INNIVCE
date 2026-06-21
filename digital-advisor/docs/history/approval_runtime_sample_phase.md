# Approval Runtime Sample Phase Archive

本文档回答：过去审批样板链路如何冻结。

## 样板目标

审批是 V5 第一条业务样板链路，用来证明：

- Query / Detail / Action 全部经 Runtime 主链路。
- RuntimeResult 是唯一业务状态来源。
- InteractionPayload 是唯一展示输入。
- Card / Portal 不生成业务动作、不解释执行状态、不调用 Tool / Provider。

## 冻结链路

```text
Query
-> Summary RuntimeResult
-> Summary InteractionPayload
-> Detail RuntimeResult
-> Detail InteractionPayload
-> RuntimeActionInput
-> WAITING_INPUT
-> WAITING_CONFIRMATION
-> EXECUTING
-> DONE / FAILED
-> Feedback InteractionPayload
```

## 已完成入口

Runtime Mainline：

- Card single approve / reject
- Card detail approve / reject
- Portal approve / reject
- SidePanel approve / reject

共同要求：

```text
RuntimeActionInput
-> RuntimePendingAction
-> RuntimeTaskState / RuntimeActionState
-> RuntimeResult
-> InteractionPayload
```

## transfer / add_sign

当前归属：Runtime Input Only。

已完成：

- `request_transfer` / `request_add_sign` 进入 `RuntimeActionInput`。
- 两者均使用 `missing_params=["target_user"]`。
- `target_user` 使用 USER parameter type。
- 补齐后只到 `WAITING_CONFIRMATION`。
- 用户确认时由 guard 阻断。

明确禁止：

- 不查通讯录。
- 不解析 open_id。
- 不执行 transfer。
- 不执行 add_sign。

Guard reason：

```text
guarded_pending_user_resolution
```

## Batch Approval Legacy Island

batch approval 当前明确绕过 Runtime 主链路。

调用链：

```text
Card renderer
-> runtime_approval_workbench action=batch_approve / batch_approve_group
-> _handle_runtime_approval_workbench()
-> _prepare_batch_approve_confirmation()
-> runtime_approval_batch_confirm card
-> _handle_runtime_approval_batch_confirm()
-> _enqueue_batch_approve()
-> Celery task bot.approvals.batch_approve
-> bot_approvals_batch_approve_task()
```

legacy island 清单：

- `runtime_v5_pending_approval_batch`
- `runtime_v5_approval_batch_selection`
- `runtime_approval_batch_confirm`
- `_prepare_batch_approve_confirmation()`
- `_handle_runtime_approval_batch_confirm()`
- `_enqueue_batch_approve()`
- `bot.approvals.batch_approve`
- `bot_approvals_batch_approve_task()`

当前 batch direct Provider 只允许存在于：

```text
app/tasks/celery_app.py::bot_approvals_batch_approve_task()
```

## Approval Action Boundary Matrix

| 动作类型 | 当前归属 | 允许状态 | 禁止事项 |
| --- | --- | --- | --- |
| approve | Runtime Mainline | Card / Portal / SidePanel -> RuntimeActionInput -> Runtime | 不得回落 batch / direct Provider |
| reject | Runtime Mainline | Card / Portal / SidePanel -> RuntimeActionInput -> Runtime | 不得回落 batch / direct Provider |
| transfer | Runtime Input Only | RuntimeActionInput + missing target_user + guard | 不得执行 Provider，不做 USER resolver |
| add_sign | Runtime Input Only | RuntimeActionInput + missing target_user + guard | 不得执行 Provider，不做 USER resolver |
| batch approve | Legacy Island | Card batch selection + batch confirm + Celery legacy task | 不做 Batch RuntimeActionInput / State / Result |
| admin write approval API | Out-of-Scope | 独立 admin write route/service | 不纳入当前 Runtime path |

## Future Migration Decision

下一次要迁移 batch approval 时，必须先单独设计 Batch Runtime，不允许复用当前 single-action `RuntimeActionInput` 合同硬塞批量语义。
下一次要执行 transfer / add_sign 时，必须先进入 USER resolver / target user resolution 阶段，不允许绕过 `target_user` missing param contract。
