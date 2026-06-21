# Approval Runtime Sample Acceptance Review

本文档回答：Approval Runtime Sample V1 是否已经满足真实飞书使用场景，并可作为第二业务样板的复制模板。

本阶段不是开发，不新增 Runtime 能力，不新增 Tool，不重构 Renderer。

## Review Basis

本次验收基于：

- Runtime V1 frozen mainline。
- Approval Runtime Sample frozen version。
- 当前 Runtime / Card / Portal / SidePanel containment。
- 当前测试覆盖。
- 当前代码路径审计。

说明：

- 本文不声称已经完成线上飞书手工验收。
- “飞书实际体验问题”指当前代码和产品路径中仍可能影响真实飞书使用的体验风险。

## 1. Approval Acceptance Checklist

| Area | Acceptance | Status |
| --- | --- | --- |
| Query | 用户可查询待审批列表 | Pass |
| Query | Query 进入 Runtime，生成 RuntimeResult | Pass |
| Query | Summary 通过 InteractionPayload 展示 | Pass |
| Detail | 用户可打开单个审批详情 | Pass |
| Detail | Detail 查询进入 Runtime，不直接 Provider | Pass |
| Detail | Detail 输出 sidepanel/card 可消费 actions | Pass |
| Approve | 单审批 approve 通过 RuntimeActionInput 进入 Runtime | Pass |
| Approve | approve 需要确认后执行 | Pass |
| Approve | Provider success / failed 都回到 RuntimeResult | Pass |
| Reject | reject 支持缺 comment 多轮输入 | Pass |
| Reject | comment 补齐后进入确认，不跳过确认 | Pass |
| WaitingInput | WAITING_INPUT 由 RuntimeState 驱动 | Pass |
| WaitingConfirmation | confirm/cancel 由 RuntimeState 驱动 | Pass |
| Feedback | 成功/失败反馈只由 RuntimeResult/InteractionPayload 展示 | Pass |
| company_id | Runtime 主链路显式携带 company_id | Pass |
| Render-only | Card / Portal / SidePanel 不直接执行 Provider | Pass |
| Legacy | batch approval 被隔离为 legacy island | Pass with Boundary |
| Transfer/AddSign | 只进入 input + guard，不执行 Provider | Pass with Guard |

## 2. Query Acceptance

目标链路：

```text
User Message
-> Command Plan(approval_query)
-> Policy(company_id)
-> Runtime
-> Approval Provider(list_pending)
-> RuntimeResult(summary, target_ui=card)
-> InteractionPayload(summary)
```

已满足：

- Query 走 Runtime 主链路。
- RuntimeResult actions 包含 `open_detail`。
- InteractionPayload 保留 items/actions/target_ui。
- Portal pending/cached approvals 已消费 InteractionPayload。

真实飞书体验风险：

- 列表 card 仍有审批专属渲染逻辑。
- 大量审批、分组、批量入口仍属于 legacy workbench 体验，不属于 V1 mainline。
- 空结果、权限不足、open_id 缺失时的用户提示仍需要真实飞书端手工验收。

## 3. Detail Acceptance

目标链路：

```text
open_detail
-> Runtime
-> Approval Provider(get_detail)
-> RuntimeResult(approval_detail, target_ui=sidepanel)
-> InteractionPayload
```

已满足：

- Card 详情查询不再直接执行 provider helper。
- Detail RuntimeResult 提供 approve/reject actions。
- Detail target_ui 已指向 sidepanel。
- SidePanel approve/reject 经 Portal Runtime action endpoint containment。

真实飞书体验风险：

- 详情卡仍有 fallback 逻辑，Runtime 查询失败时会展示降级详情。
- SidePanel 作为实际飞书体验是否稳定，需要手工验证打开、刷新、回到卡片的路径。
- Detail 展示字段仍审批专属，尚未抽象为通用 object detail。

## 4. Approve Acceptance

目标链路：

```text
approve action
-> RuntimeActionInput
-> WAITING_CONFIRMATION
-> confirm
-> EXECUTING
-> DONE / FAILED
-> Feedback
```

已满足：

- Card / Portal / SidePanel 单审批 approve 进入 RuntimeActionInput。
- approve 不直接调用 Tool/Provider。
- confirmed action 才执行 provider。
- provider error 会进入 failed RuntimeState 和 feedback payload。

真实飞书体验风险：

- 飞书卡片点击后的消息更新、重复点击、防抖和过期 token 体验需要手工验收。
- Provider 执行成功后旧卡片/旧列表的刷新体验仍可能不一致。
- approve 成功后的列表清理和用户可见状态需要真实飞书端确认。

## 5. Reject Acceptance

目标链路：

```text
reject action
-> RuntimeActionInput(missing_params=["comment"])
-> WAITING_INPUT
-> user comment
-> WAITING_CONFIRMATION
-> confirm
-> EXECUTING
-> DONE / FAILED
-> Feedback
```

已满足：

- reject 缺 comment 会进入 WAITING_INPUT。
- 空 comment 会停留在 WAITING_INPUT。
- 补齐 comment 后进入 WAITING_CONFIRMATION。
- confirm 后执行 provider。
- cancel 不执行 provider。

真实飞书体验风险：

- 用户补充原因的自然语言提示仍偏简单。
- 多轮输入期间，用户切换审批对象或点击旧卡片时的体验需要手工验收。
- comment 文本过长、包含换行或特殊字符时，需要端到端验证 provider 参数。

## 6. WaitingInput Acceptance

已满足：

- WAITING_INPUT 由 `RuntimeTaskState` / `RuntimeActionState` 表达。
- `runtime_waiting_input` ResultContext 包含 `missing_params` 与 `input_contract`。
- InteractionPayload 输出 `payload_type=action`。
- Runtime 不执行 Provider。

真实飞书体验风险：

- 当前 WaitingInput 主要通过对话继续输入，不是结构化表单。
- 飞书卡片本身是否需要 input 组件，尚未进入 V1。
- 多个并发 waiting input 的选择与覆盖策略仍依赖 session，上线前需要验证。

## 7. WaitingConfirmation Acceptance

已满足：

- WAITING_CONFIRMATION 输出 `runtime_pending_confirmation`。
- RuntimeResult actions 为 confirm/cancel。
- confirm/cancel action 本身不再二次确认。
- confirmation token 来自 Runtime action id。

真实飞书体验风险：

- 旧确认卡过期、重复确认、跨设备点击需要真实飞书验证。
- 确认卡展示是否足够说明执行对象和风险，需要产品验收。
- 同一 chat/session 多个 pending confirmation 的用户体验仍需谨慎。

## 8. Feedback Acceptance

已满足：

- DONE 输出 success feedback。
- FAILED 输出 failed feedback。
- Provider error 写入 RuntimeTaskState / RuntimeActionState。
- Interaction 只渲染 Result，不处理失败逻辑。

真实飞书体验风险：

- 失败提示是否足够人类可理解，需要真实飞书验收。
- Provider 原始错误可能需要更友好的映射，但 Diagnostics 当前暂停。
- 成功反馈与原卡片状态同步仍需端到端确认。

## 9. Known Non-Acceptance Areas

以下不属于 Approval Runtime Sample V1 acceptance：

- batch approval。
- transfer execution。
- add_sign execution。
- USER resolver / contact lookup / open_id resolution。
- admin write approval API。
- Diagnostics / Observability。
- Runtime state database persistence。
- Workflow Engine。
- 通用 Renderer Registry。

这些不阻止 V1 冻结，但不能被误认为已完成。

## 10. Feishu Actual Experience Issues

需要真实飞书手工验收的体验点：

- 卡片点击后是否及时更新或给出明确反馈。
- 用户重复点击 confirm/cancel 时是否表现稳定。
- 旧卡片、旧 confirmation token、过期 session 的提示是否清楚。
- 查询列表到详情再回到动作反馈的上下文是否连续。
- SidePanel 打开详情、执行动作、刷新状态是否一致。
- 空结果、权限不足、缺 open_id、缺 company_id 的提示是否可理解。
- provider error 是否以业务语言展示，而不是技术错误。
- 多个审批同时等待输入/确认时，用户是否能分清对象。
- reject comment 的输入体验是否自然。
- approve/reject 成功后原审批列表是否刷新或提示用户手动刷新。

## Acceptance Decision

Approval Runtime Sample V1 可以冻结为架构样板。

冻结含义：

- 它可以作为第二业务样板的 Runtime/Result/Interaction 复制模板。
- 可复制的是主链路和合同，不是审批字段、审批卡片或审批 action builder。
- 第二业务样板应复用 `RuntimeActionInput -> RuntimeState -> RuntimeResult -> InteractionPayload`。
- 第二业务样板不应复制 batch legacy island。
- 第二业务样板不应把真实飞书体验风险当成已解决。

结论：

```text
Approval Runtime Sample V1: Accepted as Architecture Template
Feishu Product Experience: Requires Manual Acceptance Before Production Claim
```
