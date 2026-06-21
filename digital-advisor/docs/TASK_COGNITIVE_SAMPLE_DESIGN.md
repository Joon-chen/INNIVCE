# Task Cognitive Sample Design

本文档设计第二条业务认知样板：Task。

本阶段不开发 Task 功能，不新增 Tool，不新增 Runtime State，不新增 Resolver。

Task 归属：

```text
Domain: Workspace
```

禁止建立：

```text
Task Module
```

## 1. Design Goal

验证 Approval Runtime Sample 和 Enterprise Cognitive Foundation V1 是否可复制到 Task。

目标链路：

```text
Live Task Data
+ Task Cognitive State
-> RuntimeResult
-> InteractionPayload
```

认知生成链路：

```text
Task Raw Data
-> WorkEvent
-> Evidence
-> Snapshot
-> Insight
```

执行链路：

```text
Insight
-> RuntimeActionInput
-> Runtime
-> Provider
-> RuntimeResult
```

Insight 只负责建议，不负责执行。

Action 继续归 Runtime。

## 2. Workspace Capability Map

| Capability | User Meaning | Status |
| --- | --- | --- |
| `task_query` | 查询任务 | 可作为第一条只读样板 |
| `task_create` | 创建任务 | 第二阶段验证 WAITING_INPUT |
| `task_update` | 更新任务 | 第一阶段只选 `task_complete` |
| `task_follow_up` | 跟进任务 | 暂缓，后续验证评论和提醒 |
| `task_delay_risk` | 识别任务延期风险 | 认知能力，先设计不实现 |

Task 不独立成业务域。

Task 能力必须继续挂在：

```text
Workspace
-> Capability
-> Skill
-> Provider
```

## 3. Capability -> Skill -> Provider

| Capability | Skill | Provider | Phase |
| --- | --- | --- | --- |
| `task_query` | `task.list_my_tasks` | Feishu Task | V1 sample |
| `task_query` | `task.search_tasks` | Feishu Task | V1 sample |
| `task_update` | `task.complete_task` | Feishu Task | V1 sample |
| `task_create` | `task.create_task` | Feishu Task | V1.1 sample |
| `task_update` | `task.update_task` | Feishu Task | Later |
| `task_update` | `task.reopen_task` | Feishu Task | Later |
| `task_update` | `task.delete_task` | Feishu Task | Later |
| `task_create` | `task.create_subtask` | Feishu Task | Later |
| `task_follow_up` | `task.comment_task` | Feishu Task | Later |
| `task_update` | `task.assign_members` | Feishu Task | Later, requires USER |
| `task_follow_up` | `task.update_followers` | Feishu Task | Later, requires USER |
| `task_follow_up` | `task.update_reminders` | Feishu Task | Later, requires DATE |
| `task_update` | `task.upload_attachment` | Feishu Task | Later |
| `task_update` | `task.add_to_tasklist` | Feishu Task | Later, requires OBJECT |
| `task_update` | `task.set_ancestor` | Feishu Task | Later, requires OBJECT |
| `task_update` | `task.clear_ancestor` | Feishu Task | Later |

Tasklist / Section 管理能力已登记，但不进入首个 Task Cognitive Sample。

## 4. Sample V1 Scope

首个 Task 样板只做两条路径设计。

### 4.1 Query + Detail

```text
User: 我的任务
-> Command Plan: task_query
-> Policy: company_id / user scope
-> Runtime
-> Feishu Task Provider
-> RuntimeResult(task_list)
-> InteractionPayload(summary)
```

展示原则：

- 任务状态来自实时 Feishu Task。
- AI 判断来自 Task Snapshot / Insight。
- Snapshot 不替代实时任务状态。
- Snapshot 不存在时显示“分析中”或“不足以判断”。

### 4.2 Complete Task

```text
Task Detail
-> user clicks complete
-> RuntimeActionInput(task_complete)
-> WAITING_CONFIRMATION
-> EXECUTING
-> DONE / FAILED
-> Feedback Result
```

这是最接近 Approval `approve/reject` 的轻量动作样板。

它验证：

- RuntimeActionInput 可跨业务复用。
- Runtime State 可跨业务复用。
- RuntimeResult / InteractionPayload 可跨业务复用。
- Card / Detail 仍然只渲染 Result。

## 5. Sample V1.1 Scope

第二条路径验证 Missing Params。

```text
User: 帮我建一个任务
-> RuntimeActionInput(task_create)
-> WAITING_INPUT(title)
-> WAITING_CONFIRMATION
-> EXECUTING
-> DONE / FAILED
```

V1.1 只允许 TEXT 参数。

暂缓：

- USER assignee。
- DATE due date。
- Tasklist / Section。
- Project / OKR。

## 6. Task Cognitive State

Task Snapshot 只保存 AI Cognitive State。

不保存实时任务状态。

建议字段沿用 Snapshot Model：

```text
id
company_id
object_type = task
object_id
snapshot_type = task_cognitive_state
status
summary
recommendation
risk_level
reasons
source_event_ids
updated_at
```

可表达：

- 是否延期。
- 是否阻塞。
- 是否缺 owner。
- 是否缺截止时间。
- 是否长期无人响应。
- 是否有会议 / 消息 / 审批相关证据。

## 7. Task Evidence

Evidence 解释 AI 为什么这么判断。

可记录：

| Evidence Type | Meaning |
| --- | --- |
| `task_due_date` | 截止时间 |
| `task_owner` | 负责人 |
| `task_status` | 当前状态 |
| `task_comment_signal` | 评论中的阻塞或承诺 |
| `task_activity_gap` | 长时间无更新 |
| `task_dependency_signal` | 依赖其他人或其他事项 |
| `meeting_reference` | 会议中产生或讨论过 |
| `approval_reference` | 与审批事项相关 |

Evidence 用于详情页解释判断依据。

Snapshot 只展示结论。

Insight 展示建议。

## 8. Task WorkEvent

Task V1 设计以下 WorkEvent。

| Event Type | Trigger |
| --- | --- |
| `task_created` | 任务创建 |
| `task_updated` | 任务字段变化 |
| `task_completed` | 任务完成 |
| `task_reopened` | 任务重新打开 |
| `task_due_changed` | 截止时间变化 |
| `task_comment_added` | 新增评论 |
| `task_overdue_detected` | 系统检测到逾期 |
| `task_snapshot_completed` | Task Snapshot 生成完成 |
| `task_insight_generated` | Task Insight 生成完成 |

本阶段只设计，不实现 WorkEvent Engine。

## 9. Task Insight

Insight = Recommendation。

Insight 不执行动作。

示例：

| Insight Type | Example |
| --- | --- |
| `delay_risk` | 任务明天到期但 5 天无更新，建议先提醒负责人确认进展。 |
| `missing_owner` | 任务没有明确负责人，建议先指定 owner。 |
| `blocked_by_dependency` | 评论中多次提到等待他人输入，建议创建跟进提醒。 |
| `completion_candidate` | 子项均完成且无阻塞，建议确认是否完成任务。 |

Runtime 可基于用户确认执行：

- complete task
- create follow-up task
- comment task
- send reminder

但这些 Action 不属于 Insight。

## 10. Interaction Design Boundary

Task 首轮不做 Portal。

建议使用：

- Bot Card：任务摘要。
- Detail：单任务详情和 AI 判断依据。
- Feedback：完成 / 创建后的结果回执。

禁止：

- 大规模 Portal 重构。
- Task 管理工作台。
- 批量任务操作。
- 项目 / OKR 管理台。

## 11. Out Of Scope

本阶段禁止：

- 编写 Task 业务代码。
- 新增 Tool。
- 新增 Provider。
- 新增 Runtime State。
- 新增 WorkEvent Engine。
- 新增 Evidence Engine。
- 新增 Snapshot Engine。
- 新增 Insight Engine。
- USER Resolver。
- DATE Resolver。
- Calendar 联动。
- Meeting Sample。
- Customer Sample。
- Batch Task Action。
- Task Portal。

## 12. Acceptance Criteria

Task Cognitive Sample Design 可冻结的条件：

- Task 明确归属 Workspace。
- Capability / Skill / Provider 映射完整。
- 第一条样板为 `task_query -> task_complete`。
- 第二条样板为 `task_create -> WAITING_INPUT(TEXT)`。
- Task Snapshot 不替代实时 Task 状态。
- Insight 只给建议，Action 继续归 Runtime。
- Approval 的 RuntimeResult / InteractionPayload / State Model 可被 Task 复用。

## 13. Next Step

进入：

```text
Task Runtime Sample Contract Review
```

先评审 `task_query` 和 `task_complete` 是否已有足够 Contract。

不要直接进入实现。
