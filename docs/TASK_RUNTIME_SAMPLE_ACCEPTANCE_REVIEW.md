# Task Runtime Sample Acceptance Review

本文档回答：Task Runtime Sample V1 是否可以作为 Approval 之后的第一条业务样板闭环。

本阶段不是开发 Task Portal，不做 Task Insight、Task Snapshot、Task Risk 或 Task Graph。

## Review Basis

本次验收基于：

- Task Runtime Sample Contract Test。
- Task Runtime Sample Minimal Implementation。
- 当前真实 Feishu Task 只读查询抽样。
- 当前 V5 Runtime / Provider / RuntimeResult / InteractionPayload 路径。

真实只读抽样结果：

```text
lark-cli task +get-related-tasks --as user --page-limit 5 --json
```

抽样字段包含：

```text
guid
summary
status
url
created_at
creator
```

说明：

- 当前 `get-my-tasks` 返回空列表。
- `get-related-tasks` 返回 1 条真实样本，字段形态可用于验证 Provider 归一化。
- 本阶段未执行真实 `complete_task`，避免误完成用户真实任务。

## 1. Acceptance Checklist

| Area | Acceptance | Status |
| --- | --- | --- |
| Intent | “我的任务 / 我的待办” 可识别为 `task_query` | Pass |
| Query | `task_query` 进入 Runtime | Pass |
| Provider | `task_query` 路由到 Task Provider `list_my_tasks` | Pass |
| Result | Provider 输出 `task_list` | Pass |
| Field Mapping | 真实 `guid/summary/status/url` 可归一化为任务对象 | Pass |
| Action Input | `task_list` 可生成 `task_complete` RuntimeActionInput | Pass |
| Scope | `RuntimeActionInput` 携带 `company_id` 与 `scope_context` | Pass |
| Confirmation | `task_complete` 未确认时进入 `WAITING_CONFIRMATION` | Pass |
| Execution | 确认后进入 `EXECUTING` 并调用 Provider `complete_task` | Pass |
| Feedback | Provider 返回后收敛为 `RuntimeResult.result_type=task_complete` | Pass |
| Interaction | InteractionPayload 对 `task_complete` 渲染为 `feedback` | Pass |
| Real Write | 真实 Feishu 完成任务未执行 | Not Accepted Yet |

## 2. Query Acceptance

目标链路：

```text
User Intent(task_query)
-> Runtime
-> Task Provider(list_my_tasks)
-> RuntimeResult(task_list)
-> InteractionPayload(summary)
```

已满足：

- `task_query` Contract 已冻结。
- `RuntimeResult.metadata.scope_context.scope = SELF`。
- `RuntimeResult.metadata.company_id` 存在。
- `task_list` 可作为 InteractionPayload 的 summary 输入。

真实飞书字段验收：

| Feishu Field | Runtime Field | Status |
| --- | --- | --- |
| `guid` | `guid` / action `target.task_guid` | Pass |
| `summary` | title fallback | Pass |
| `status` | status | Pass |
| `url` | url | Pass |

## 3. Complete Acceptance

目标链路：

```text
task_complete RuntimeActionInput
-> WAITING_CONFIRMATION
-> CONFIRMED
-> EXECUTING
-> DONE / FAILED
-> RuntimeResult(task_complete)
-> InteractionPayload(feedback)
```

已满足：

- `task_query` 输出的 action 内含冻结版 `RuntimeActionInput`。
- Action target 从真实 `guid` 生成 `target.task_guid`。
- 确认前不执行 Provider。
- 确认后 Provider operation 为 `complete_task`。
- Provider result_type 收敛为 `task_complete`。
- Interaction 不解释业务状态，只消费 `RuntimeResult`。

未验收：

- 未对真实飞书任务执行完成动作。
- 未验证真实飞书侧任务状态刷新。
- 未验证飞书 Bot/Card 点击动作是否已经消费该 action。

## 4. Runtime Boundary

当前 Task V1 样板边界：

```text
Intent
-> Runtime
-> Provider
-> RuntimeResult
-> InteractionPayload
```

仍禁止：

- Task Portal。
- Task Insight。
- Task Snapshot。
- Task Risk。
- Task Graph。
- Task Create WAITING_INPUT。
- USER / DATE / OBJECT Resolver。

## 5. Known Gaps

| Gap | Impact | Decision |
| --- | --- | --- |
| `get-my-tasks` 当前样本为空 | 无法验证“我的任务”真实列表展示 | 保留为真实环境验收项 |
| 真实 `complete_task` 未执行 | 不能宣称飞书写操作生产验收完成 | 需要用户提供测试任务或明确允许完成一条测试任务 |
| Bot/Card 未接真实点击动作 | Runtime 闭环成立，但入口体验未验收 | 下一阶段单独验收入口 |
| 只覆盖 `SELF` scope | 不能代表团队/部门/公司任务查询 | 符合 V1 范围，后续 Scope 扩展 |

## 6. Acceptance Decision

```text
Task Runtime Sample V1 Core Loop: Accepted
Task Feishu Write Experience: Not Accepted Yet
Task UI Experience: Not Accepted Yet
```

结论：

Task 可以作为 Approval 之后的第一条 Runtime 业务样板，因为它已经验证了：

```text
Query
-> RuntimeResult action
-> RuntimeActionInput
-> Runtime State
-> Provider
-> RuntimeResult feedback
-> InteractionPayload
```

但当前只冻结 Runtime Core Loop，不冻结 Task UI 或真实飞书写操作体验。

## Next Phase

建议进入：

```text
Task Runtime Sample Feishu Manual Acceptance
```

目标：

1. 准备一条明确可测试的飞书任务。
2. 用 Bot 查询任务。
3. 从 Interaction action 触发 RuntimeActionInput。
4. 完成确认。
5. 验证飞书任务状态变为 completed。

继续禁止 Task Portal / Insight / Snapshot 扩展。
