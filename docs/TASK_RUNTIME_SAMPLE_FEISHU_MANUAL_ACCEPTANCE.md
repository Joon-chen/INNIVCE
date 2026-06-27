# Task Runtime Sample Feishu Manual Acceptance

本文档记录 Task Runtime Sample 在真实飞书环境中的手工验收结果。

本阶段目标不是开发 Task Portal、Task Insight、Task Snapshot、Task Risk 或 Task Graph。

## Scope

验收范围：

```text
RuntimeActionInput(task_complete)
-> Runtime
-> FeishuTaskProvider
-> Feishu Task write
-> RuntimeResult(task_complete)
-> InteractionPayload(feedback)
```

## Acceptance Attempt

### 1. Test Task Created

使用本地已授权 user identity 创建测试任务：

```text
Digital Advisor Task Runtime 验收测试
```

创建结果：

```text
task_guid = d4c692f7-90dd-4627-8071-736a31edbaa7
```

### 2. Local Runtime Attempt

本地执行 RuntimeActionInput 路径：

```text
RuntimeActionInput
-> WAITING_CONFIRMATION
```

结果：

```text
WAITING_CONFIRMATION reached
```

随后执行 Provider 时失败，原因：

```text
local PostgreSQL not running
```

判断：

- Runtime State 前半段成立。
- 本地环境不能作为真实 Provider 写验收环境。
- 不应在本地绕过数据库去伪造 Runtime 完成结果。

### 3. Cloud Runtime Attempt

按全云端测试原则，改在云端容器检查 user identity：

```text
docker compose exec api lark-cli task +get-related-tasks --as user
```

结果：

```text
not_configured
run `lark-cli config init --new`
```

判断：

- 云端容器当前没有配置 user identity lark-cli。
- 因此云端 Runtime Provider 无法用 user identity 执行 `feishu_task_complete`。
- Task Feishu 写操作不能宣称已通过生产验收。

### 4. Test Task Cleanup

为避免残留测试待办，使用本地已授权 user identity 完成测试任务：

```text
lark-cli task +complete --as user --task-id d4c692f7-90dd-4627-8071-736a31edbaa7
```

结果：

```text
ok = true
```

说明：

- 该清理动作未作为 Runtime 验收通过依据。
- 它只用于清理测试数据。

## Acceptance Result

| Item | Status |
| --- | --- |
| RuntimeActionInput contract | Pass |
| WAITING_CONFIRMATION | Pass |
| Confirmed Runtime Provider execution | Blocked |
| Real Feishu task completion via Runtime | Blocked |
| RuntimeResult task_complete feedback | Not verified in cloud write path |
| InteractionPayload feedback | Verified by contract tests only |

## Blocking Condition

当前阻塞不是 Task Runtime Contract，而是云端 user identity 执行环境：

```text
Cloud lark-cli user identity not configured
```

这影响所有需要 user identity 的真实 OA 写操作，不只影响 Task：

- Task complete。
- Calendar create / update。
- Mail draft / send。
- Drive / Doc user-scoped operations。
- 未来 People / Workspace / Knowledge 中需要用户身份的动作。

## Decision

```text
Task Runtime Core Loop: Accepted
Task Feishu Manual Write Acceptance: Blocked
```

不要用直接 CLI 完成任务来冒充 Runtime 验收。

下一步应先进入：

```text
Cloud User Identity Runtime Readiness Phase
```

目标：

1. 明确云端 user identity 如何授权。
2. 明确 Runtime Provider 使用哪个 `lark-cli` home/profile。
3. 验证云端 `--as user` 可读 Task。
4. 再执行一条测试任务的 Runtime 完成动作。

继续禁止：

- Task Portal。
- Task Insight。
- Task Snapshot。
- Task Risk。
- Task Graph。
