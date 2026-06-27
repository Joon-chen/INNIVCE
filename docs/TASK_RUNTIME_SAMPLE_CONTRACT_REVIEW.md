# Task Runtime Sample Contract Review

本文档评审 Task 作为第二条业务样板的最小 Runtime Contract。

本阶段不实现 Task 样板，不新增 Tool，不新增 Runtime State，不迁移 UI。

## 1. Review Goal

评审以下两条路径是否可以进入实现前冻结：

```text
task_query
-> RuntimeResult(task_list)
-> InteractionPayload(summary)
```

```text
task_complete
-> RuntimeActionInput
-> WAITING_CONFIRMATION
-> EXECUTING
-> DONE / FAILED
-> RuntimeResult(feedback)
```

目标不是扩展 Task 全能力，而是验证 Approval Runtime Sample 是否可复制到 Workspace 域。

## 2. Current Contract Inventory

### Command / Intent

已存在：

| Intent | Status | Notes |
| --- | --- | --- |
| `task_query` | Ready | 识别“我的任务 / 我的待办”等查询语义 |
| `task_search` | Ready | 识别关键词搜索 |
| `task_create` | Partial | 可提取 `summary`，但 V1.1 才验证 WAITING_INPUT |
| `task_complete` | Partial | 可从上下文提取 `task_guid`，缺失时标记 missing param |

### Planner

已存在：

| Strategy | Sources |
| --- | --- |
| `task_query` | `task` |
| `task_search` | `task` |
| `task_create` | `task` |
| `task_complete` | `task` |

### Capability

已存在：

| Runtime Strategy | Source | Operation | Route |
| --- | --- | --- | --- |
| `task_query` | `task` | `list_my_tasks` | `feishu_task_query` |
| `task_search` | `task` | `search_tasks` | `feishu_task_query` |
| `task_create` | `task` | `create_task` | `feishu_task_create` |
| `task_complete` | `task` | `complete_task` | `feishu_task_complete` |

### Provider

已存在：

| Operation | Provider Tool | Write | Current Result Type |
| --- | --- | --- | --- |
| `list_my_tasks` | `task_qa` | No | `task_list` |
| `search_tasks` | `task_qa` | No | `task_list` |
| `create_task` | `feishu_task_create` | Yes | `task_create` |
| `complete_task` | `feishu_task_complete` | Yes | `complete_task` |

注意：

`complete_task` ProviderResult 当前使用 operation 作为 `result_type`，即 `complete_task`。

Composer 已支持 `task_complete` action receipt。

这会造成命名不一致：

```text
Runtime strategy: task_complete
Provider operation: complete_task
Expected result_type: task_complete
Current provider result_type: complete_task
```

这是 Task Runtime Sample 进入实现前需要锁住的第一个 Contract 缺口。

## 3. RuntimeResult / InteractionPayload Fit

当前 RuntimeResult 基础字段可复用：

```text
result_type
status
title
summary
items
actions
target_ui
metadata.company_id
```

当前 InteractionPayload 可复用：

```text
payload_type
title
summary
status
items
actions
metadata
```

Task 不需要新增 Result 模型。

但需要冻结 Task 的最小 result_type：

| Scenario | RuntimeResult.result_type | InteractionPayload.payload_type |
| --- | --- | --- |
| 查询任务 | `task_list` | `summary` |
| 等待确认完成任务 | `runtime_pending_confirmation` | `action` |
| 完成任务成功 | `task_complete` or `runtime_action` | `feedback` |
| 完成任务失败 | `task_complete` or `runtime_action` | `feedback` |

建议：

首轮实现时统一将完成任务回执收敛为：

```text
result_type = task_complete
```

不要暴露 Provider operation 名：

```text
complete_task
```

## 4. Minimal Sample Contract

### 4.1 task_query

输入：

```text
intent = task_query
strategy = task_query
source = task
operation = list_my_tasks
company_id required
```

输出：

```text
RuntimeResult(
  result_type = task_list,
  status = success / failed,
  target_ui = card,
  items = task items,
  metadata.company_id = required
)
```

Interaction：

```text
InteractionPayload(
  payload_type = summary,
  items = RuntimeResult.items,
  actions = RuntimeResult.actions
)
```

规则：

- Query 不触发 Task Snapshot 生成。
- Query 可以合并 Task Snapshot / Insight，但不得阻塞实时任务列表。
- Snapshot 不存在时只能显示“分析中 / 尚无判断”，不得编造 AI 判断。

### 4.2 task_complete

输入：

```text
RuntimeActionInput(
  strategy = task_complete,
  sources = [task],
  company_id = required,
  execution_identity = user,
  requires_confirmation = true,
  entities.task_guid = required
)
```

状态：

```text
CREATED
-> WAITING_CONFIRMATION
-> CONFIRMED
-> EXECUTING
-> DONE / FAILED
```

输出：

```text
RuntimeResult(
  result_type = task_complete,
  status = success / failed,
  target_ui = card,
  metadata.company_id = required,
  metadata.strategy = task_complete
)
```

Interaction：

```text
InteractionPayload(
  payload_type = feedback,
  summary = RuntimeResult.summary,
  status = RuntimeResult.status
)
```

规则：

- Card / Detail 不直接调用 `feishu_task_complete`。
- Card / Detail 只能提交 RuntimeActionInput。
- Provider Error 必须进入 RuntimeResult，不由 UI 解释失败。

## 5. Missing Params Boundary

`task_complete` 缺 `task_guid` 时，不应直接执行。

允许状态：

```text
WAITING_INPUT(task_guid)
```

但 Task V1 不建议以 `task_complete` 验证 WAITING_INPUT。

原因：

- `task_guid` 是 OBJECT 参数，不是 TEXT。
- OBJECT 解析会牵涉任务列表上下文和选中对象。
- 容易提前进入 Detail/SidePanel 重构。

Task V1.1 应用 `task_create` 验证 WAITING_INPUT(TEXT)：

```text
missing summary/title
-> WAITING_INPUT(TEXT)
-> WAITING_CONFIRMATION
-> EXECUTING
```

## 6. Current Gaps

| Gap | Risk | Required Before Implementation |
| --- | --- | --- |
| `complete_task` vs `task_complete` result_type 不一致 | Composer / Interaction 可能无法稳定识别反馈类型 | Contract Test 先锁定 expected result_type |
| Task Detail action contract 未冻结 | UI 可能直接传 Provider 参数 | 先定义 `task_guid` action payload |
| `task_list` actions 未冻结 | Query 后无法从列表进入 RuntimeActionInput | 先定义 single-task complete action |
| OBJECT 参数未冻结 | 缺 `task_guid` 时容易做临时解析 | V1 不做 OBJECT missing input |
| Task Snapshot 合并规则未实现 | 可能混淆实时任务状态和认知判断 | 先只定义，不阻塞 Runtime sample |

## 7. Out Of Scope

本阶段禁止：

- 实现 Task Runtime Sample。
- 新增 Task Provider。
- 新增 Runtime State。
- 新增 OBJECT Parameter Type。
- 新增 USER Resolver。
- 新增 DATE Resolver。
- Task Portal。
- Task Snapshot Builder。
- Task Insight Engine。
- 批量完成任务。
- 分配任务成员。
- 更新提醒。
- 任务清单 / 分组管理。

## 8. Contract Tests To Add Next

下一阶段如果进入实现，先补 Contract Test：

1. `task_query` 输出 `RuntimeResult.result_type = task_list`。
2. `task_query` 输出 `metadata.company_id`。
3. `task_complete` action input 必须携带 `company_id`。
4. `task_complete` 未确认不得执行 Provider。
5. `task_complete` confirmed 后进入 Provider。
6. Provider 成功后 RuntimeResult 收敛为 `task_complete`。
7. Provider 失败后 RuntimeResult status = failed。
8. InteractionPayload 对 `task_complete` 渲染为 `feedback`。

这些测试验证 Contract，不验证完整 Task 工作流。

## 9. Recommendation

建议进入：

```text
Task Runtime Sample Contract Test Phase
```

先只补测试和最小命名收敛：

```text
complete_task
-> task_complete
```

不要做 UI 迁移。

不要做 Task Snapshot。

不要做 Task Insight。

不要做 Task Create WAITING_INPUT。
