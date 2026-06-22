# Current Mission

本文档只回答：现在在做什么。

系统最高级规则见 `docs/V5_RUNTIME_CONSTITUTION.md`。

## 当前阶段

```text
Task Enterprise Provider Feasibility Audit Completed
```

## 当前目标

确认 `查看全公司任务` 是否可以通过飞书 Task Bot/Tenant 实时读取能力实现。

当前结论：

- `查看我的任务` 已支持 Bot first + SELF USER fallback。
- `查看全公司任务` 已进入 `company` scope，并正确返回企业实时读取能力边界。
- 飞书 Task v2 当前确认的任务列表接口 `task.tasks.list` 只支持 `my_tasks`。
- `task.tasklists.tasks` 可读取指定清单任务，但需要 `tasklist_guid`，不能代表全公司所有个人任务。
- 下一步可做“公司托管任务清单”能力，而不是假装已经具备全公司任务读取。

## 当前禁止范围

- 不使用 USER_TOKEN 代查公司/部门/他人任务。
- 不把 WorkEvent / ExtractedItem 当作实时任务来源。
- 不实现全公司任务索引。
- 不实现 Task Insight / Snapshot / Graph。
- 不做 UI 重构。
- 不做数据库迁移。
- 不做 Event Bus / Workflow / Memory Engine。

## 当前验收标准

- `查看我的任务` 返回个人任务。
- `查看全公司任务` 不再被识别为“不确定”。
- `查看全公司任务` 不走 USER fallback。
- `查看全公司任务` 返回明确企业任务实时读取能力边界。
- Task 企业能力审计文档说明可行与不可行路径。

## 当前验证

```text
tests/test_runtime_v5.py::test_runtime_v5_company_task_query_recognizes_company_scope
tests/test_runtime_v5.py::test_runtime_v5_company_calendar_query_recognizes_company_scope
tests/test_runtime_v5.py::test_runtime_permission_denies_company_scope_for_ordinary_employee
tests/test_execution_identity_user_token.py::test_company_task_query_does_not_use_user_fallback_even_when_authorized
tests/test_execution_identity_user_token.py::test_task_query_authorized_self_user_fallback_executes_provider
tests/test_execution_identity_user_token.py::test_calendar_query_authorized_self_user_fallback_executes_provider
cloud health: ok
```

已知未处理：

- `lark-cli api GET /open-apis/task/v2/tasks` 现场调用时遇到飞书 token 网络 reset；schema 结果已足够确认接口 shape。
- `tests/test_feishu_provider_boundary.py` 仍有历史断言漂移，本阶段不处理。

## 下一步计划

```text
Managed Tasklist Provider V1 Review
```

- 设计公司托管任务清单查询边界。
- 明确它不是“全公司所有个人任务”。
- 冻结 RuntimeResult metadata：`source_scope=managed_tasklists`、`not_all_company_tasks=true`。
- 再决定是否进入最小实现。
