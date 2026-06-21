# V5 Runtime Constitution

本文档是 Digital Advisor OS V5 的运行时宪法。它的更新频率应很低，只在长期架构原则变化时修改。

## 1. 核心目标

Digital Advisor 不是一组飞书 API、卡片逻辑或后台调试接口的集合，而是面向 Owner 多公司管理和企业员工个人智能体的 AI OS。

当前阶段的优先级是：

1. 先稳架构边界。
2. 再扩复杂能力。
3. 最后打磨体验。

在 V5 架构校准完成前，暂停继续堆叠复杂任务能力。

## 2. 五层边界

所有用户请求必须收敛到以下主链路：

```text
User Message
-> Command Layer
-> Policy & Context Layer
-> Agent Runtime
-> Tool Layer
-> Result
-> Interaction Layer
```

五层职责如下：

```text
Interaction 只展示
Command 只规划
Policy 只管边界和权限
Runtime 唯一执行
Tool 只做被动能力
```

## 3. Interaction Layer

允许：

- 渲染 Bot 卡片、SidePanel、WebView、Push、iOS 页面。
- 消费 Runtime Result 或 Interaction Payload。
- 展示摘要、详情、反馈、入口和少量快速操作。
- 根据 Runtime Result 的 `target_ui` 选择展示形态。

禁止：

- 调用 Tool、Provider、Feishu API、MCP 或 CLI。
- 做权限判断。
- 做复杂业务规划。
- 决定跨公司上下文。
- 自己解释业务流程结果。
- 自己生成业务建议。
- 查询数据库来补业务状态。

交互入口只能将用户输入或用户动作提交给 Runtime Action Endpoint。

RuntimeActionInput 入口规则：

- Portal / Card / SidePanel / WebView 只能把点击动作转换为 `RuntimeActionInput`。
- 入口适配层不得直接调用 Tool、Provider、Feishu API、MCP 或 CLI。
- 入口适配层不得判断执行失败、权限策略或高风险确认策略。
- 入口适配层不得生成业务 Result，只能等待 Runtime 输出 RuntimeResult / InteractionPayload。
- 需要确认的动作必须通过 `confirmation.token` 和 `confirmation.confirmed` 表达。
- 所有动作必须携带 `context.company_id`、`context.chat_id`、`context.user_id` 或 `context.open_id`、`context.source_ui`。

Missing Params Contract：

- 当动作缺少执行参数时，入口只能在 `RuntimeActionInput.metadata.missing_params` 中声明缺失字段。
- Runtime 必须进入 `RuntimeActionState(waiting_input)`，不得直接执行 Tool。
- 用户补齐参数后，Runtime 必须先进入 `waiting_confirmation`，不得跳过确认直接执行。
- 只有确认后，Runtime 才能进入 `executing -> done / failed`。
- 当前样板仅覆盖审批 reject 的 `comment` 参数；transfer / add_sign 暂不纳入。

交互形态职责：

- Bot Card：摘要、建议、入口、少量快速操作、等待确认和操作反馈。
- SidePanel：单对象详情，例如审批、任务、人员、资源详情。
- WebView：复杂列表、批量操作、管理工作台和多步骤操作流。
- Push：主动提醒、状态变化通知和运行结果送达。
- iOS：Owner / Admin 多公司控制台和跨公司切换入口。

所有 Card、SidePanel、WebView action 必须携带 company_id、user_id 或 open_id、chat_id 或 session_id、action_id、result_id 或 task_id。若动作需要确认，还必须携带 confirmation token。缺少这些上下文字段时，交互层只能返回安全错误，不得猜测默认公司或默认会话。

## 4. Command Layer

允许：

- 识别意图。
- 生成标准 Plan。
- 决定目标 UI。
- 给出候选工具。
- 描述上下文范围。

禁止：

- 执行 Tool。
- 调用 Provider。
- 发送消息。
- 检查权限。
- 做二次确认策略。

标准 Plan 结构：

```json
{
  "intent": "",
  "steps": [],
  "target_ui": "",
  "tool_candidates": [],
  "context_scope": {
    "company_id": "",
    "mode": "single_company"
  }
}
```

`target_ui` 必须从业务实现中抽出来，由 Command Layer 决定。

## 5. Policy & Context Layer

允许：

- 公司隔离。
- 用户权限判断。
- 执行身份判断。
- 高风险动作确认策略。
- 全局模式授权判断。
- 阻断无 company_id 的执行请求。

禁止：

- 业务规划。
- Tool 选择。
- UI 展示。
- 实际执行。

所有 Runtime Context 必须包含：

```json
{
  "company_id": "",
  "user_id": "",
  "chat_id": "",
  "session_id": "",
  "permission_scope": []
}
```

禁止隐式混用“默认公司”。除非当前会话已明确绑定公司，否则不得执行单公司请求。

## 6. Agent Runtime

Runtime 是唯一执行入口。

禁止出现：

```text
Bot handler -> Tool
Card action -> Tool
WebView -> Tool
Provider -> Tool
```

必须变成：

```text
Bot/Card/WebView
-> Runtime Action Endpoint
-> Runtime State Machine
-> Tool
```

Runtime 管理状态：

```text
pending -> running -> waiting -> done / failed
```

Runtime 必须负责：

- Runtime Task。
- Runtime Step。
- Runtime Action 状态机。
- Runtime 执行日志。
- Runtime 错误恢复。
- Runtime 等待确认状态。

## 7. Tool Layer

Tool 只能是被动能力。

例如 ApprovalTool 只负责：

- 查审批。
- 查详情。
- 同意。
- 拒绝。
- 转交。
- 加签。

ApprovalTool 不负责：

- 判断是否高风险。
- 判断是否建议通过。
- 决定展示卡片。
- 决定是否二次确认。

对应职责归属：

- Command 决定任务。
- Policy 决定权限和确认。
- Runtime 负责执行。
- Composer / Interaction 负责展示。

## 8. Runtime Result

Runtime 输出给交互层的 Result 必须标准化：

```json
{
  "result_type": "summary/action/feedback/delivery",
  "status": "success/failed/waiting",
  "title": "",
  "summary": "",
  "items": [],
  "actions": [],
  "target_ui": "",
  "metadata": {}
}
```

Card、SidePanel、WebView、Push、iOS 都只能消费 Result，不直接碰业务逻辑。

RuntimeResult 只能由 RuntimeResult Builder 生成。Interaction Layer 不得自行拼装业务 Result。

InteractionPayload 只能由 InteractionPayload Builder 从 RuntimeResult 转换而来。Card、SidePanel、WebView 不得绕过 Builder 重新解释业务状态或重新生成 actions。

RuntimeResult Builder 当前冻结映射：

- `approval_list` / `approval_query`：`target_ui=card`，actions 为 `open_detail`。
- `approval_detail`：`target_ui=sidepanel`，actions 为 `approve` / `reject`。
- `runtime_waiting_input`：`target_ui=card`，actions 为空。
- `runtime_pending_confirmation`：`target_ui=card`，actions 为 `confirm` / `cancel`。
- `runtime_action`：`target_ui=card`，actions 为空。

InteractionPayload Builder 当前冻结映射：

- `runtime_waiting_input`：`payload_type=action`。
- `runtime_pending_confirmation`：`payload_type=action`。
- `runtime_action`：`payload_type=feedback`。
- `approval_detail`：默认 `payload_type=summary`，并原样保留 RuntimeResult actions。

Result 到 UI 的映射规则：

- `target_ui=card`：渲染 Bot Card。
- `target_ui=sidepanel`：打开或刷新 SidePanel。
- `target_ui=webview`：打开 WebView 工作台。
- `target_ui=push`：发送主动提醒。
- `target_ui=ios`：交给 iOS 控制台。
- `target_ui=none`：只记录或静默完成。

Interaction Layer 不得重新解释 `target_ui` 的业务含义。

## 9. 多租户数据隔离

WorkEvent 必须按 company_id 隔离，核心维度：

```text
company_id + event_type + source + object_id + actor + timestamp
```

Memory 必须按 company_id 隔离，核心维度：

```text
company_id + memory_type + source_event_id + confidence + ttl/delete policy
```

全局视图只能动态聚合，不能建立共享 Memory。

## 10. iOS App 定位

iOS App 不是普通员工入口。

它是：

- 多公司控制台。
- 全局聚合入口。
- Owner / Admin 视角。
- 跨公司切换入口。

iOS 不能复用普通 Bot 的默认上下文逻辑，必须显式进入全局模式。

## 11. 当前改造顺序

第一阶段：架构边界收口。

1. 新建/整理 Command Layer。
2. 新建/整理 Policy Layer。
3. 新建 Runtime Task / Runtime Result 标准模型。
4. 把 Bot 入口改成只调用 Runtime 主入口。
5. 把 Card/WebView 变成 Result 渲染器。

第二阶段：多租户上下文打底。

1. Runtime Context 增加 company_id。
2. Session / Result / WorkEvent / Memory 加 company_id。
3. Provider Snapshot 加 company_id。
4. Tool 调用参数透传 company_id。
5. 阻断无 company_id 的执行请求。

第三阶段：审批作为样板重构。

```text
用户：等待审批的单子
-> Command 生成 approval_summary Plan
-> Policy 检查当前公司和审批权限
-> Runtime 调 ApprovalTool
-> Result 生成 Summary Result
-> Card 展示摘要
-> 用户点某类
-> SidePanel/WebView 展示详情
-> 用户操作
-> Runtime 执行审批动作
-> Feedback Result
```

第四阶段：回到 Agent Runtime 增强。

- 建表写组织架构。
- 查邮件并总结。
- 建会议。
- 发消息。
- 创建任务。
- 多步流程编排。
