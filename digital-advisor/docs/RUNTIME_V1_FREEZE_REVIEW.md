# Runtime V1 Freeze Review

本文档回答：Runtime V1 是否已经可以正式冻结。

结论：Runtime V1 可以冻结为第一版主链路。它不是 Runtime V2 的开始，也不包含下一轮能力扩展。

## 1. Runtime V1 已完成能力

Runtime V1 已完成以下能力边界：

- 五层职责边界已建立：Interaction / Command / Policy / Runtime / Tool。
- Bot / Card / Portal / SidePanel 的普通单审批动作已收敛到 Runtime 主入口。
- `RuntimeActionInput` 入口合同已冻结，并强制携带 `context.company_id`。
- `RuntimePendingAction` 已作为 action input 到 pending action 的标准转换合同。
- `RuntimeTaskState` 与 `RuntimeActionState` 已建立最小状态模型。
- 状态模型支持 `WAITING_INPUT -> WAITING_CONFIRMATION -> CONFIRMED -> EXECUTING -> DONE / FAILED`。
- `RuntimeResult` 已成为 Runtime 输出给交互层的标准业务状态。
- `InteractionPayload` 已成为 Card / Portal / SidePanel 的标准展示输入。
- Missing Params Registry 已支持 `TEXT` 与 `USER` 两种参数类型。
- reject/comment 已作为 TEXT 参数样板跑通多轮输入。
- transfer/add_sign 已进入 USER 参数输入合同，但由 guard 阻断真实执行。
- company_id context spine 已贯穿 Runtime 主链路。
- legacy `runtime_v5_action_request` 已 containment，不再进入执行。
- 审批作为 V1 样板链路已冻结。

## 2. Runtime Mainline

Runtime V1 主链路：

```text
User Message / User Action
-> Command Plan / RuntimeActionInput
-> Policy & Context
-> Runtime
-> RuntimePendingAction
-> RuntimeTaskState / RuntimeActionState
-> Tool / Provider
-> RuntimeResult
-> InteractionPayload
-> Card / Portal / SidePanel
```

当前 Runtime Mainline 覆盖：

- approval query。
- approval detail query。
- single approval approve。
- single approval reject。
- reject missing comment input。
- confirmation confirm / cancel。
- provider success feedback。
- provider failed feedback。

入口覆盖：

- Card single approve / reject。
- Card detail approve / reject。
- Portal approve / reject。
- SidePanel approve / reject，经 Portal Runtime action endpoint。

强制规则：

- Runtime 是唯一执行入口。
- Tool / Provider 只能由 Runtime 调用。
- `RuntimeResult` 是唯一业务状态来源。
- `InteractionPayload` 是唯一展示输入。
- 主链路执行必须显式携带 `company_id`。

## 3. Legacy Island

Runtime V1 保留的 legacy island：

### Batch Approval

batch approval 当前不是 Runtime V1 主链路的一部分。

明确 legacy path：

```text
runtime_v5_pending_approval_batch
runtime_v5_approval_batch_selection
runtime_approval_batch_confirm
_prepare_batch_approve_confirmation()
_handle_runtime_approval_batch_confirm()
_enqueue_batch_approve()
bot.approvals.batch_approve
bot_approvals_batch_approve_task()
```

当前 batch direct Provider 只允许存在于：

```text
app/tasks/celery_app.py::bot_approvals_batch_approve_task()
```

冻结判断：

- batch approval 已被识别和圈定。
- 它绕过 `RuntimeActionInput` / `RuntimeState` / `RuntimeResult` / `InteractionPayload`。
- 它不得被误认为 Runtime Mainline。
- 后续要迁移 batch，必须单独设计 Batch Runtime。

## 4. Out-of-Scope

Runtime V1 明确不包含：

- Batch RuntimeActionInput。
- Batch RuntimeState。
- Batch RuntimeResult。
- Batch Workflow Design。
- transfer execution。
- add_sign execution。
- USER Resolver / contact lookup / open_id resolution。
- Diagnostics / Observability 重构。
- Memory company_id 改造。
- WorkEvent company_id 改造。
- Provider Snapshot company_id 改造。
- Runtime Step 持久化日志。
- 数据库状态存储。
- Workflow Engine。
- admin write approval API 迁入 Runtime。
- 复杂任务能力扩展，例如建表、邮件总结、会议、发消息、创建任务、多步流程编排。

## 5. Remaining Technical Debt

Runtime V1 冻结时仍保留以下技术债：

- `bot_runtime.py` 仍然偏重，后续应继续瘦身为入口适配层。
- Runtime 状态仍基于 Session，不是数据库持久化状态。
- Runtime Task / Step 执行日志还没有完整持久化模型。
- Diagnostics 仍是止血式外移，尚未统一为 Observability Layer。
- batch approval 仍是 legacy direct execution island。
- transfer/add_sign 只有输入合同与 guard，没有真实 user resolution 和执行。
- USER 参数类型目前只做文本抽取，不做身份解析。
- SidePanel action 目前通过 Portal action endpoint containment，尚未做独立大规模迁移。
- Memory / WorkEvent / Provider Snapshot 尚未纳入 company_id sweep。
- 部分旧文档仍描述 V5 总体升级过程，不等同于当前任务板。

这些债务不阻止 Runtime V1 冻结，但会影响 Runtime V2 或后续能力扩展的优先级。

## 6. 下一阶段候选方向

Runtime V1 冻结后，不应立即默认进入 Runtime V2。候选方向需要按风险选择：

### 候选 A：USER Parameter Resolution Audit

目标：

- 审计 `target_user -> USER` 到未来 UserResolver 的边界。
- 明确通讯录查找、open_id resolution、权限判断和 Runtime 编排的分层。
- 继续禁止 transfer/add_sign 执行。

适合在开始 transfer/add_sign 前进行。

### 候选 B：Runtime Diagnostics / Observability Design

目标：

- 把现有 diagnostics 止血式外移统一为 Observability Layer。
- 不干扰 Runtime 主链路。

适合在需要线上排障能力前进行。

### 候选 C：Batch Runtime Design

目标：

- 为 batch approval 单独设计批量 Runtime 合同。
- 不复用 single-action `RuntimeActionInput` 硬塞批量语义。

适合在 batch approval 要正式迁移出 legacy island 时进行。

### 候选 D：Runtime State Persistence Design

目标：

- 从 Session state 走向持久化 task/action/step/log。
- 仍不直接引入 Workflow Engine，除非需求明确。

适合在多轮任务、跨会话恢复和审计日志成为优先级时进行。

### 候选 E：Interaction Renderer Generalization

目标：

- 把审批卡片样板进一步抽象为通用 Card / Portal renderer。
- 保持 render-only contract。

适合在继续扩展任务、邮件、会议等其它业务 UI 前进行。

## Freeze Decision

Runtime V1 可以正式冻结。

冻结含义：

- V1 主链路合同不再随意改名或重塑。
- 新能力必须先证明不会绕过 Runtime Mainline。
- Legacy Island 必须继续登记，不能静默扩散。
- Out-of-Scope 不能借小修小补进入 V1。
- 下一阶段应从候选方向中显式选择，而不是自然滑入 Runtime V2。
