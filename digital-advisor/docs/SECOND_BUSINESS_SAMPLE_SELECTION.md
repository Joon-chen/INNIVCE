# Second Business Sample Selection

本文档回答：Runtime V2 第一个业务样板应该选 Task、Meeting 还是 Customer。

本阶段不是开发第二业务样板，不新增 Tool，不新增 Runtime State，不新增 Resolver。

## Selection Criteria

对比维度：

- Runtime Fit：是否能复用 V1 主链路。
- Renderer Reuse：是否能复用 Summary / Detail / WaitingInput / WaitingConfirmation / Feedback。
- State Reuse：是否能复用 WAITING_INPUT / WAITING_CONFIRMATION / EXECUTING / DONE / FAILED。
- Result Reuse：是否能复用 RuntimeResult / InteractionPayload。
- Interaction Reuse：是否能复用 Card / SidePanel / Portal 的 render-only contract。

## 1. Task Sample Analysis

当前已有基础：

- `task_query`
- `task_search`
- `task_create`
- `task_complete`
- 多个 task action capabilities 已登记。
- Intent 层已有任务查询、搜索、创建、完成识别。
- Planner 已映射 task strategies。
- Provider 已有 task query / create / complete 工具路径。
- Composer 已有 `task_list`、`task_create`、`task_complete` 格式化分支。

Runtime Fit：高。

- Task 既有 query，也有 action。
- `task_complete` 很像 approval approve/reject 的轻量动作样板。
- `task_create` 可验证 missing params 与 confirmation。
- 不需要一开始引入复杂 batch 或 USER resolver。

Renderer Reuse：高。

- Summary Renderer：`task_list` 可作为列表摘要。
- Detail Renderer：可以用 task detail 或选中 task context 验证。
- WaitingInput Renderer：创建任务缺 `title` / `due` / `assignee` 时可复用。
- WaitingConfirmation Renderer：创建/完成任务前确认可复用。
- Feedback Renderer：创建/完成结果回执可复用。

State Reuse：高。

推荐样板链路：

```text
task_query
-> task_list Summary
-> open task detail
-> task_complete / task_create
-> WAITING_INPUT
-> WAITING_CONFIRMATION
-> EXECUTING
-> DONE / FAILED
```

Result Reuse：高。

- `task_list`、`task_create`、`task_complete` 已有 result type 迹象。
- 可直接验证 RuntimeResult action builder 是否能从审批专属走向 object/action 通用。

Interaction Reuse：中高。

- Card list + SidePanel detail + feedback 可复用。
- Portal 目前审批语义较重，Task 第一轮可以不碰 Portal。

风险：

- Task 可能很快碰到 USER 参数，例如 assign_members。
- 任务创建可能需要日期解析，DATE 参数类型尚未冻结。
- 不应一开始选择 assign/subtask/list member 等复杂动作。

建议 Task 子样板：

```text
task_query
-> task_list Summary
-> task_complete
-> confirm/cancel
-> feedback
```

然后再加：

```text
task_create
-> missing title or due
-> confirmation
-> feedback
```

## 2. Meeting Sample Analysis

这里把 Meeting 分成两类：

- Calendar meeting：`calendar_query` / `calendar_create`
- VC historical meeting：`vc_meeting_search`

当前已有基础：

- `calendar_query`
- `calendar_create`
- `vc_meeting_search`
- Intent 层已有日程查询、创建和历史会议搜索。
- Provider 有 calendar create 和 vc meeting search 路径。
- Composer 有 `calendar_event_list`、`calendar_create`、`vc_meeting_list` 格式化分支。

Runtime Fit：中。

- Meeting query 和 create 能复用 Runtime。
- 但 create meeting 很快需要时间、参会人、会议室等参数。
- 时间解析和人员解析会引入 DATE / USER / RESOURCE 参数体系。

Renderer Reuse：中高。

- Summary Renderer：日程列表、历史会议列表可复用。
- Detail Renderer：会议详情适合 SidePanel。
- WaitingInput Renderer：缺时间/参会人可复用，但需要新参数类型。
- WaitingConfirmation Renderer：创建日程前确认可复用。
- Feedback Renderer：创建结果可复用。

State Reuse：中。

- `calendar_create` 适合验证 WAITING_INPUT 与 WAITING_CONFIRMATION。
- 但时间/参会人 resolution 不应在当前阶段顺手实现。

Result Reuse：中高。

- `calendar_event_list` 与 `vc_meeting_list` 已经有 item 格式化分支。
- 但 Meeting 需要更丰富的 detail/action schema。

Interaction Reuse：中。

- Card summary 与 feedback 可复用。
- SidePanel detail 很适合会议详情。
- Portal 复用价值较低，可能更适合 WebView 或 Calendar-specific surface。

风险：

- 很容易提前进入 DATE resolver、USER resolver、会议室资源 resolver。
- `vc_meeting_search` 更偏只读，不足以验证 action state。
- `calendar_create` 是高风险写动作，确认链路必须稳定。

建议 Meeting 子样板：

```text
calendar_query
-> calendar_event_list Summary
-> calendar detail
```

暂缓：

```text
calendar_create with attendees / rooms
```

## 3. Customer Sample Analysis

当前已有基础：

- 系统里有 customer / sales / order / contract 等业务概念。
- 权限与数据分类中已有 customer/sales 语义。
- operations entities 支持 customers。
- 但 Runtime V5 capabilities / planner / provider 中没有明确 customer sample 主链路。

Runtime Fit：低到中。

- Customer 适合验证多租户和业务对象模型。
- 但当前缺少清晰的 Runtime capability、provider、result type、action set。
- 若直接选 Customer，容易变成建业务域，而不是验证 Runtime 通用性。

Renderer Reuse：中。

- Summary Renderer：客户列表可复用。
- Detail Renderer：客户详情非常适合 SidePanel。
- Feedback Renderer：备注、跟进、状态更新可复用。
- WaitingInput / WaitingConfirmation 也适用。

State Reuse：中。

- 客户跟进、备注、状态变更都能用状态机。
- 但业务动作需要先定义边界和权限。

Result Reuse：中。

- 可以复用 RuntimeResult / InteractionPayload。
- 但需要先建立 `customer_list`、`customer_detail`、`customer_action` 等 result type。

Interaction Reuse：中。

- Customer 更可能需要 WebView / CRM workspace。
- Card/SidePanel 可用于摘要和详情，但很快会进入复杂管理界面。

风险：

- 领域建模成本高。
- 权限和数据隔离更敏感。
- 可能触碰 Memory / WorkEvent / Provider Snapshot，而这些仍是当前 out-of-scope。
- 容易把 Runtime V2 样板变成 CRM 功能开发。

建议：

- Customer 不适合作为 Runtime V2 第一个业务样板。
- 更适合作为第三阶段，用于验证多租户业务对象、Memory/WorkEvent 和 WebView 管理台。

## Comparison Matrix

| Candidate | Runtime Fit | Renderer Reuse | State Reuse | Result Reuse | Interaction Reuse | Risk |
| --- | --- | --- | --- | --- | --- | --- |
| Task | High | High | High | High | Medium-High | Medium |
| Meeting | Medium | Medium-High | Medium | Medium-High | Medium | Medium-High |
| Customer | Low-Medium | Medium | Medium | Medium | Medium | High |

## Recommendation

推荐 Runtime V2 第一个业务样板选择：

```text
Task
```

原因：

- Task 已有 Runtime capability、planner、intent、provider 和 composer 基础。
- Task 同时具备 query 和 action，能验证 Approval Runtime Sample 的完整跨业务通用性。
- Task 可以从低风险动作开始，不必马上进入 USER resolver、DATE resolver 或复杂 WebView。
- Task 的 `task_query -> task_complete -> feedback` 路径最接近 approval 的 `query -> action -> feedback`。
- Task 能暴露当前 RuntimeResult action builder 的审批专属问题，但不要求立刻重构 Renderer。

首选最小样板：

```text
task_query
-> Summary Renderer
-> task_complete
-> WaitingConfirmation Renderer
-> EXECUTING
-> Feedback Renderer
```

第二步再验证：

```text
task_create
-> WaitingInput Renderer
-> WaitingConfirmation Renderer
-> Feedback Renderer
```

暂不选择 Meeting 的原因：

- Meeting 很快需要 DATE / USER / ROOM resolution。
- 如果一上来做 `calendar_create`，会把参数体系扩展和 Renderer 验证混在一起。

暂不选择 Customer 的原因：

- Customer 当前 Runtime 主链路基础不足。
- 它更像业务域建设，不适合作为验证 Runtime 通用性的第一个 V2 样板。

## Out Of Scope

本阶段禁止：

- 编写业务代码。
- 新增 Tool。
- 新增 Runtime State。
- 新增 Resolver。
- 重构 Portal。
- 重构 Card。
- 重构 SidePanel。
- 实现 Task sample。
- 实现 Meeting sample。
- 实现 Customer sample。

## Selection Decision

Second Business Sample Selection Phase 结论：

- Runtime V2 第一个业务样板建议选择 Task。
- 样板目标应限定为验证 Approval Runtime Sample 的跨业务通用性。
- 第一条 Task 样板应选择 `task_query -> task_complete -> feedback`。
- 不应在第一轮引入 assign_members、attendees、rooms、customer CRM 等复杂对象解析。
