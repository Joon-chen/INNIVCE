# Runtime Refactor Phase Archive

本文档回答：过去 Runtime V5 主链路如何收口。

## 背景

V5 架构校准的核心不是继续补复杂任务能力，而是先把系统重新切成五层：

```text
Interaction 只展示
Command 只规划
Policy 只管边界和权限
Runtime 唯一执行
Tool / Provider 只做被动能力
```

因此暂停了建表、邮件总结、会议、发消息、创建任务和多步流程编排等复杂能力扩展。

## 已完成的主链路收口

- 修正 `runtime_v5` 与 Feishu Bot 入口之间的循环导入。
- 建立 Command / Policy / Runtime / Interaction 的基础 V5 模块边界。
- Portal 待审批查询和审批动作进入 `run_runtime_v5()`。
- MCP / CLI 执行边界下沉到 provider 辅助层，Runtime 不直接散落 subprocess 逻辑。
- Bot trace payload 外移到 Runtime V5 helper。
- Bot diagnostics snapshot 基础字段外移，但 Diagnostics 后续整体暂缓。

## Runtime State Model

已建立最小状态模型：

- `RuntimeTaskState`
- `RuntimeActionState`
- `runtime_state.py`

约束：

- 先基于 Session 存储。
- 不引入数据库。
- 不引入 Workflow Engine。

第一条状态化链路为审批：

```text
Query
-> WAITING_CONFIRMATION
-> CONFIRMED
-> EXECUTING
-> DONE / FAILED
```

后续补充了 `WAITING_INPUT`，用于多轮参数补齐。

## RuntimeActionInput Contract

已冻结入口合同：

```json
{
  "action_id": "",
  "action_type": "approve/reject/confirm/cancel/open_detail/execute",
  "intent": "",
  "strategy": "",
  "target": {},
  "confirmation": {
    "confirmed": false,
    "token": ""
  },
  "context": {
    "company_id": "",
    "chat_id": "",
    "user_id": "",
    "open_id": "",
    "source_ui": "portal/card/sidepanel/webview/bot/unknown"
  }
}
```

当前规则：

- Portal / Card / SidePanel / WebView 只能把用户动作转换为 `RuntimeActionInput`。
- 入口不得直接调用 Tool、Provider、Feishu API、MCP 或 CLI。
- 入口不得判断权限、失败、高风险确认策略。
- 入口不得生成业务 Result。
- producer 必须通过 builder 生成 payload，并强制携带 `company_id`。
- legacy `runtime_v5_action_request` 已 containment，Runtime 收到后返回 failed Result，不进入执行。

## RuntimeResult 与 InteractionPayload

已冻结输出侧合同：

- `RuntimeResult` 只能由 RuntimeResult Builder 生成。
- `InteractionPayload` 只能由 InteractionPayload Builder 从 RuntimeResult 转换。
- Runtime 主链路不再手写展示字段清单。
- Card / Portal / SidePanel 只消费 InteractionPayload，不解释业务状态。

冻结映射：

| ResultContext | target_ui | actions | payload_type |
| --- | --- | --- | --- |
| `approval_list` / `approval_query` | `card` | `open_detail` | `summary` |
| `approval_detail` | `sidepanel` | `approve` / `reject` | `detail` |
| `runtime_waiting_input` | `card` | empty | `action` |
| `runtime_pending_confirmation` | `card` | `confirm` / `cancel` | `action` |
| `runtime_action` | `card` | empty | `feedback` |

## Missing Params Contract

已建立：

- `MissingParamType`
- `MissingParamResolver`
- `MissingParamContract`

当前类型：

- `comment -> TEXT`
- `target_user -> USER`

USER 当前只证明 registry 支持多参数类型，不做通讯录查询、不解析 open_id、不执行 transfer/add_sign。

## Legacy Direct Execution Containment

已退休旧单审批直接执行 helper：

- `_execute_single_approval_action()`
- `_execute_approval_provider()`

当前普通单审批动作不再通过 Card 直接执行 Provider。

保留的 legacy island 仅限 batch approval，详见审批归档。
