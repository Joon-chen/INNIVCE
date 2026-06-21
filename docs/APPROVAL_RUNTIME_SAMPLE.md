# Approval Runtime Sample Frozen Version

本文档回答：标准长什么样。

## Standard Flow

```text
Query
-> Detail
-> WAITING_INPUT
-> WAITING_CONFIRMATION
-> EXECUTING
-> DONE / FAILED / BLOCKED
```

## Query

用户询问待审批：

```text
User Message
-> Command Plan(intent=approval_summary)
-> Policy(company_id + approval permission)
-> Runtime
-> Approval Provider(query)
-> RuntimeResult(result_type=summary, target_ui=card)
-> InteractionPayload(payload_type=summary)
```

RuntimeResult 样板：

```json
{
  "result_type": "summary",
  "status": "success",
  "title": "待审批",
  "summary": "",
  "items": [],
  "actions": [
    {
      "type": "open_detail",
      "target_ui": "sidepanel"
    }
  ],
  "target_ui": "card",
  "metadata": {
    "company_id": "",
    "result_context_type": "approval_list"
  }
}
```

## Detail

用户打开审批详情：

```text
RuntimeActionInput(action_type=open_detail)
-> Runtime
-> Approval Provider(get_detail)
-> RuntimeResult(result_type=summary/detail, target_ui=sidepanel)
-> InteractionPayload(payload_type=summary/detail)
```

RuntimeResult actions 样板：

```json
[
  {
    "type": "approve",
    "requires_confirmation": true
  },
  {
    "type": "reject",
    "requires_confirmation": true,
    "missing_params": ["comment"]
  }
]
```

## WAITING_INPUT

当 action 缺少参数，例如 reject 缺 `comment`：

```text
RuntimeActionInput(action_type=reject, missing_params=["comment"])
-> RuntimeTaskState(status=waiting)
-> RuntimeActionState(status=waiting_input)
-> RuntimeResult(result_type=action, status=waiting)
-> InteractionPayload(payload_type=action)
```

RuntimeActionInput 样板：

```json
{
  "action_id": "",
  "action_type": "reject",
  "intent": "approval_action",
  "strategy": "approval.reject",
  "target": {
    "approval_id": "",
    "instance_code": ""
  },
  "confirmation": {
    "confirmed": false,
    "token": ""
  },
  "context": {
    "company_id": "",
    "chat_id": "",
    "user_id": "",
    "open_id": "",
    "source_ui": "card"
  },
  "metadata": {
    "missing_params": ["comment"]
  }
}
```

RuntimeResult 样板：

```json
{
  "result_type": "action",
  "status": "waiting",
  "title": "需要补充信息",
  "summary": "",
  "items": [],
  "actions": [],
  "target_ui": "card",
  "metadata": {
    "company_id": "",
    "result_context_type": "runtime_waiting_input",
    "missing_params": ["comment"],
    "input_contract": {
      "status": "waiting_input",
      "next_state": "waiting_confirmation",
      "actionable": true
    }
  }
}
```

## WAITING_CONFIRMATION

参数补齐后进入确认：

```text
filled params
-> RuntimeTaskState(status=waiting)
-> RuntimeActionState(status=waiting_confirmation)
-> RuntimeResult(result_type=action, status=waiting)
-> InteractionPayload(payload_type=action)
```

RuntimeResult actions 样板：

```json
[
  {
    "type": "confirm",
    "requires_confirmation": false
  },
  {
    "type": "cancel",
    "requires_confirmation": false
  }
]
```

## EXECUTING

用户确认后：

```text
RuntimeActionInput(action_type=confirm, confirmation.confirmed=true)
-> RuntimeActionState(status=confirmed)
-> RuntimeActionState(status=executing)
-> Tool / Provider
```

执行约束：

- 只有 Runtime 可以调用 Tool / Provider。
- Card / Portal / SidePanel 不得执行 Provider。
- 执行上下文必须包含 `company_id`。

## DONE / FAILED / BLOCKED

成功：

```text
Provider success
-> RuntimeTaskState(status=done)
-> RuntimeActionState(status=done)
-> RuntimeResult(result_type=feedback, status=success)
-> InteractionPayload(payload_type=feedback)
```

失败：

```text
Provider error
-> RuntimeTaskState(status=failed)
-> RuntimeActionState(status=failed)
-> RuntimeResult(result_type=feedback, status=failed)
-> InteractionPayload(payload_type=feedback)
```

阻断：

```text
Policy / guard blocked
-> RuntimeTaskState(status=failed or waiting)
-> RuntimeActionState(status=failed or blocked)
-> RuntimeResult(result_type=feedback, status=failed)
-> InteractionPayload(payload_type=feedback)
```

当前 BLOCKED 样板：

- transfer / add_sign 确认执行前命中 guard。
- reason 为 `guarded_pending_user_resolution`。
- Provider 不执行。

## InteractionPayload Sample

InteractionPayload 只从 RuntimeResult 派生：

```json
{
  "payload_type": "feedback",
  "title": "",
  "summary": "",
  "items": [],
  "actions": [],
  "metadata": {
    "company_id": "",
    "target_ui": "card",
    "result_type": "runtime_action"
  }
}
```

## Frozen Boundary

- Query / Detail / Action 全部经 Runtime 主链路。
- `RuntimeResult` 是唯一业务状态来源。
- `InteractionPayload` 是唯一展示输入。
- Card / Portal / SidePanel 不解释业务状态。
- transfer / add_sign 当前只验证输入合同，不执行。
- batch approval 当前是 legacy island，不属于本样板。
