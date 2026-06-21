# Interaction Renderer Generalization Audit

本文档回答：Approval Runtime Sample 的展示模式中，哪些已经通用，哪些仍是审批专属。

本阶段不是 Renderer 重构，不改 Portal、Card、SidePanel，不做 USER Resolver，不执行 transfer/add_sign。

## 1. Renderer Taxonomy

Approval Runtime Sample 当前可以抽象为五类 Renderer：

```text
Summary Renderer
Detail Renderer
WaitingInput Renderer
WaitingConfirmation Renderer
Feedback Renderer
```

这五类并不等于五个代码组件。当前它们主要通过：

- `RuntimeResult`
- `InteractionPayload`
- `target_ui`
- `payload_type`
- `actions`
- `metadata.result_context`

共同表达。

## 2. Summary Renderer

当前审批样板：

```text
approval_list / approval_query
-> RuntimeResult(result_type=summary, target_ui=card)
-> InteractionPayload(payload_type=summary)
-> actions=open_detail
```

已经通用的部分：

- `items` 列表。
- `summary` 文本。
- `status`。
- `target_ui=card`。
- `actions` 从 RuntimeResult 透传到 InteractionPayload。
- `company_id` 在 metadata 中透传。

仍是 Approval 专属的部分：

- `result_type in {"approval_list", "approval_query"}`。
- `open_detail` action 的字段名是审批字段：`approval_code`、`instance_code`、`task_id`。
- RuntimeResult builder 中 `_approval_detail_action()` 是审批专用。
- Portal pending/cached approvals 返回结构仍是审批语义：`available`、`items`、`answer`。

通用化方向：

- 第二条业务样板应复用 `items + actions + target_ui`。
- 不应复用审批字段名。
- 应抽象为 `open_detail` action contract：`object_type`、`object_id`、`display_index`、`target_ui`。

## 3. Detail Renderer

当前审批样板：

```text
approval_detail
-> RuntimeResult(target_ui=sidepanel)
-> InteractionPayload(payload_type=summary)
-> actions=approve/reject
```

已经通用的部分：

- Detail 由 `target_ui=sidepanel` 表达。
- Detail 仍消费 RuntimeResult / InteractionPayload。
- Detail actions 从 RuntimeResult 透传，不由 SidePanel 重新生成。
- `requires_confirmation` 可作为动作级通用字段。

仍是 Approval 专属的部分：

- `result_type == "approval_detail"`。
- actions 固定为 `approve` / `reject`。
- `_approval_mutation_action()` 生成审批动作字段。
- Detail item 内容依赖审批 item shape。

通用化方向：

- Detail Renderer 应按 `object_type + item + actions` 渲染。
- Task/Meeting/Customer 的 detail actions 应由 RuntimeResult 提供，不由 renderer 推断。
- `requires_confirmation`、`missing_params`、`target_ui` 可保留为通用 action 字段。

## 4. WaitingInput Renderer

当前审批样板：

```text
runtime_waiting_input
-> RuntimeResult(status=waiting, target_ui=card)
-> InteractionPayload(payload_type=action)
-> metadata.input_contract
```

已经通用的部分：

- `runtime_waiting_input` 不含审批前缀。
- `payload_type=action` 基于等待状态生成。
- `input_contract.status=waiting_input`。
- `input_contract.next_state=waiting_confirmation`。
- `missing_params` 可承载任意参数类型，例如 TEXT / USER。
- actions 为空，等待用户自然语言补输入。

仍是 Approval 专属的部分：

- 当前样板主要来自 reject/comment 和 transfer/add_sign/target_user。
- 文案和 command 仍偏审批。
- 缺参字段目前来自审批动作上下文。

通用化方向：

- WaitingInput Renderer 可以直接作为跨业务通用 renderer。
- 不同业务只需要提供 `missing_params`、参数 label、输入提示和 next_state。
- Task/Meeting/Customer 可复用 TEXT / USER / DATE / OBJECT 等未来参数类型。

## 5. WaitingConfirmation Renderer

当前审批样板：

```text
runtime_pending_confirmation
-> RuntimeResult(status=waiting, target_ui=card)
-> InteractionPayload(payload_type=action)
-> actions=confirm/cancel
```

已经通用的部分：

- `runtime_pending_confirmation` 不含审批前缀。
- confirm/cancel 是通用动作。
- `confirmation_token` 是通用确认凭证。
- `requires_confirmation=false` 表示 confirm/cancel 本身不再二次确认。
- Card / Portal 只渲染 RuntimeResult actions。

仍是 Approval 专属的部分：

- summary/title 目前多来自审批动作语义。
- pending action metadata 里包含审批 item 与审批 strategy。
- Portal/Card 入口 action producer 当前只覆盖审批。

通用化方向：

- WaitingConfirmation Renderer 可以作为跨业务通用 renderer。
- 业务差异应放在 `summary`、`items`、`metadata.pending_action` 中。
- confirm/cancel contract 不应绑定审批。

## 6. Feedback Renderer

当前审批样板：

```text
runtime_action
-> RuntimeResult(result_type=feedback, status=success/failed)
-> InteractionPayload(payload_type=feedback)
```

已经通用的部分：

- `runtime_action` 是通用 action receipt。
- `payload_type=feedback`。
- actions 为空。
- status 可表达 success / error / failed。
- `metadata.result_context.runtime_state.error` 可表达失败原因。

仍是 Approval 专属的部分：

- receipt item/title/summary 当前多来自审批 action。
- Composer 中仍有 `approval_*` receipt 格式化分支。
- 旧结果清理原因中仍有审批专用 key。

通用化方向：

- Feedback Renderer 应只依赖 `status + title + summary + items + error metadata`。
- 业务字段可作为 item metadata 展示，但 renderer 不应解释审批状态。
- 第二条业务样板应验证失败反馈也只来自 RuntimeResult。

## 7. Current Generic Surface

当前已经通用的展示合同：

- `RuntimeResult.result_type`
- `RuntimeResult.status`
- `RuntimeResult.title`
- `RuntimeResult.summary`
- `RuntimeResult.items`
- `RuntimeResult.actions`
- `RuntimeResult.target_ui`
- `RuntimeResult.metadata.company_id`
- `InteractionPayload.payload_type`
- `InteractionPayload.items`
- `InteractionPayload.actions`
- `InteractionPayload.metadata.target_ui`
- `InteractionPayload.metadata.result_type`

当前 render-only 规则已经成立：

- InteractionPayload 只从 RuntimeResult 派生。
- Portal pending/cached/action 已消费 InteractionPayload。
- Card/Portal 不直接调用 Tool/Provider。
- SidePanel approve/reject 经 Portal Runtime action endpoint containment。

## 8. Current Approval-Specific Surface

仍审批专属的展示逻辑：

- `_target_ui_for_result()` 中 `approval_detail -> sidepanel`、`approval_list/query -> card`。
- `_actions_for_result()` 中 `approval_list/query -> open_detail`。
- `_actions_for_result()` 中 `approval_detail -> approve/reject`。
- `_approval_detail_action()`。
- `_approval_mutation_action()`。
- `portal_pending_approvals_payload()`。
- `portal_cached_approvals_payload()`。
- `portal_approval_action_payload()`。
- `_approval_action_result_context()`。
- Composer 中 `approval_*` item/receipt/followup 格式化分支。

这些不是问题，但它们不能作为 Task / Meeting / Customer 的默认复制模板。

## 9. Candidate Generic Renderer Model

建议未来通用 Renderer 合同围绕以下模型设计：

```json
{
  "renderer_type": "summary/detail/waiting_input/waiting_confirmation/feedback",
  "object_type": "approval/task/meeting/customer",
  "status": "success/waiting/failed",
  "title": "",
  "summary": "",
  "items": [],
  "actions": [],
  "target_ui": "card/sidepanel/webview",
  "metadata": {
    "company_id": "",
    "result_type": "",
    "input_contract": {},
    "runtime_state": {}
  }
}
```

注意：

- `renderer_type` 可以由 `InteractionPayload.payload_type + result_type/status` 推导，不必立刻新增字段。
- `object_type` 未来有价值，但本阶段不修改合同。
- 第二条业务样板应先用现有 `RuntimeResult -> InteractionPayload` 验证，避免提前重构 renderer。

## 10. Second Business Sample Readiness

Task / Meeting / Customer 可以复用：

- Summary Renderer：列表 + open detail。
- Detail Renderer：详情 + actions。
- WaitingInput Renderer：缺参数补齐。
- WaitingConfirmation Renderer：confirm/cancel。
- Feedback Renderer：成功/失败回执。

第二条业务样板需要补齐：

- 业务自己的 `result_type`。
- 业务自己的 `object_id` / `object_type`。
- 业务自己的 actions。
- 业务自己的 missing params。
- 业务自己的 provider result item shape。

不应该复用：

- 审批字段名。
- 审批 action builder。
- 审批 Portal response shape。
- 审批 Composer 格式化分支。

## Audit Decision

Interaction Renderer Generalization 可以进入设计准备状态，但不应立即重构。

冻结判断：

- WaitingInput / WaitingConfirmation / Feedback 已接近通用。
- Summary / Detail 的合同通用，但当前 action builder 仍审批专属。
- 第二条业务样板应先通过现有 RuntimeResult / InteractionPayload 跑通，再决定是否抽象 Renderer Registry。
- 当前阶段不修改 Portal/Card/SidePanel。
