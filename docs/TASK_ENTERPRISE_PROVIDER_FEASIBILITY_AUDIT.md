# Task Enterprise Provider Feasibility Audit

## Purpose

Clarify whether `查看全公司任务` can be implemented as a Bot/Tenant realtime query.

This audit does not implement a provider. It freezes the boundary for the next implementation phase.

## Current Finding

`查看我的任务` is implementable today through:

```text
Bot/Tenant first
-> enterprise realtime gap
-> SELF-only USER_TOKEN fallback
-> Feishu Task realtime data
```

`查看全公司任务` is not equivalent to `list all tasks in tenant`.

Feishu Task v2 exposes these relevant read surfaces:

| Surface | Endpoint / Command | Token | Scope Shape | Company-Wide? |
| --- | --- | --- | --- | --- |
| My tasks | `GET /open-apis/task/v2/tasks` / `task.tasks.list` | bot/user | `type=my_tasks` only | No |
| Task detail | `GET /open-apis/task/v2/tasks/:task_guid` / `task.tasks.get` | bot/user | known task GUID + visibility | No |
| Tasklists | `GET /open-apis/task/v2/tasklists` / `task.tasklists.list` | bot/user | visible tasklists | Partial |
| Tasklist tasks | `GET /open-apis/task/v2/tasklists/:tasklist_guid/tasks` / `task.tasklists.tasks` | bot/user | known visible tasklist | Partial |

Important schema evidence:

- `task.tasks.list` parameter `type` currently only supports `my_tasks`.
- `task.tasklists.tasks` requires `tasklist_guid`.
- Tasklist visibility is not the same as all employee personal task visibility.

## API Evidence

Local `lark-cli schema` results:

```text
lark-cli schema task.tasks.list --format json
```

Key result:

```text
type: "列取任务的类型，目前只支持 \"my_tasks\"，即“我负责的”。"
access_tokens: ["bot", "user"]
scopes: ["task:task:read", "task:task:write"]
doc_url: https://open.feishu.cn/api-explorer?from=op_doc_tab&apiName=list&project=task&resource=task&version=v2
```

```text
lark-cli schema task.tasklists.tasks --format json
```

Key result:

```text
required: tasklist_guid
scopes: ["task:tasklist:read", "task:tasklist:write"]
doc_url: https://open.feishu.cn/api-explorer?from=op_doc_tab&apiName=tasks&project=task&resource=tasklist&version=v2
```

Official overview reference:

```text
https://open.feishu.cn/document/task-v2/overview
```

## Feasibility Matrix

| User Request | V0 Runtime Scope | Feishu Realtime Feasibility | Recommended Behavior |
| --- | --- | --- | --- |
| 查看我的任务 | `SELF` | Yes, via USER fallback if Bot path is not connected | Supported |
| 查看张三的任务 | `USER` | Not safe via current actor USER_TOKEN; requires explicit enterprise/admin/provider capability | Return capability gap |
| 查看部门任务 | `DEPARTMENT` | No direct tenant-wide task API confirmed | Return capability gap |
| 查看全公司任务 | `COMPANY` | No direct all-tenant task API confirmed | Return capability gap |
| 查看某个任务清单 | `TEAM` / object scope | Feasible if `tasklist_guid` is known and Bot has visibility | Candidate V1 |
| 查看公司托管任务清单 | `COMPANY` with managed tasklists | Feasible if company adopts managed/shared tasklists | Candidate V1 |

## Implementation Options

### Option A: True Company Task Realtime Query

Goal:

```text
company scope
-> Bot/Tenant token
-> all company tasks
```

Status:

```text
Not implementable with the currently confirmed Task v2 list API.
```

Reason:

`task.tasks.list` only supports `my_tasks`.

### Option B: Company Managed Tasklists

Goal:

```text
company scope
-> Bot/Tenant token
-> visible company tasklists
-> tasklist tasks
```

Status:

```text
Implementable as a constrained V1.
```

Boundary:

It returns tasks from company-managed/shared tasklists, not every employee personal task.

Required capability:

- `task.tasklist:read`
- Bot visibility on the relevant tasklists
- optional configured managed tasklist IDs

### Option C: Event-Driven Enterprise Task Index

Goal:

```text
Task Events
-> WorkEvent
-> Snapshot / Index
-> Bot reads company task intelligence
```

Status:

```text
Future cognitive path, not realtime source of truth.
```

Boundary:

This can power task intelligence, risk, trend, overdue analysis, and management insights. It must not pretend to be raw realtime Feishu task data unless event coverage and initial sync are proven complete.

## Recommended Next Step

Enter `Managed Tasklist Provider V1`.

Scope:

1. Add read-only provider operation:

```text
task_managed_tasklist_query
```

2. Read configured or visible tasklists using Bot/Tenant token.
3. Read tasks from selected tasklists using Bot/Tenant token.
4. Return RuntimeResult with explicit source label:

```text
source_scope = managed_tasklists
not_all_company_tasks = true
```

5. Keep `查看全公司任务` as capability gap until user/company defines managed tasklists or Feishu provides a true all-company task API.

## User-Facing Product Language

For `查看全公司任务`, current correct response should be:

```text
我现在还不能读取全公司所有个人任务。
飞书 Task API 当前确认的任务列表接口只支持“我负责的任务”，清单任务需要指定或可见的任务清单。

可以先接入“公司托管任务清单”，用于查询公司项目/部门清单中的任务；
真正的全公司个人任务查询，需要飞书开放企业级任务读取能力或建立事件驱动的企业任务索引。
```

## Decision

Do not implement `company task query` by:

- Using current user USER_TOKEN.
- Looping through employees with user impersonation.
- Reading WorkEvent or extracted items as realtime task source.
- Returning managed tasklist data as if it were all company tasks.

Allowed:

- Implement managed tasklist query as a clearly labeled constrained capability.
- Keep true company task query blocked until provider support is confirmed.
