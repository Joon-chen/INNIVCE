# Current Mission

本文档只回答：现在在做什么。

系统最高级规则见 `docs/V5_RUNTIME_CONSTITUTION.md`。

## 当前阶段

```text
User Token Readiness Completed
```

## 当前目标

Task Runtime Sample 已完成 `task_query` / `task_complete` 的最小闭环合同。

本阶段解决真实飞书写入暴露出的身份问题：

```text
公司资源 → BOT / TENANT_TOKEN
个人资源 → USER / USER_TOKEN
```

已完成：

- 新增 Feishu User Token resolver，复用现有 `feishu_user` OAuth Account。
- `FeishuClient` 增加 user access token PATCH 能力。
- `FeishuTaskService.complete_task` 支持 `user_access_token`。
- `task_complete` 作为第一条 USER_TOKEN 写入样板。
- 缺少用户授权时，Runtime Provider 返回 `waiting_authorization`，不执行旧 Tool 写入。
- 已授权时，Task Provider 通过 USER_TOKEN 调飞书 Task 完成接口。

## 当前禁止范围

本阶段不要做：

- 全量 OAuth UI 改造。
- 全量 Provider 迁移。
- Approval Provider USER_TOKEN 迁移。
- Task create/update/delete USER_TOKEN 迁移。
- Admin identity 实现。
- 删除 CLI profile fallback。
- Task Portal / Task Insight / Task Snapshot / Task Graph。
- Event Bus / Workflow / Memory / Evidence / Snapshot / Insight Engine。
- UI 重构或 SidePanel 迁移。
- 数据库迁移。

## 当前验收标准

- `ProviderRequest.execution_identity_contract` 可表达 `USER + USER_TOKEN`。
- `complete_task` 缺用户授权时返回 `ProviderResult.status = denied`。
- 缺授权结果 `result_type = waiting_authorization`。
- 缺授权路径不调用飞书写接口。
- 已授权路径调用 Task User Token API。
- `complete_task` 成功后仍输出 `Runtime result_type = task_complete`。

## 当前验证

已通过：

```text
tests/test_execution_identity_user_token.py
tests/test_runtime_v5.py
tests/test_capability_registry_builder.py
tests/test_v5_architecture.py::test_feishu_write_services_are_only_called_by_api_runtime
ruff check
```

已知未处理：

- `tests/test_feishu_provider_boundary.py` 当前存在 Feishu API 路径/timeout/能力清单断言漂移，和本阶段 USER_TOKEN 变更无直接关系。

## 下一步计划

建议进入：

```text
WAITING_AUTHORIZATION Interaction Phase
```

目标：

- RuntimeResult 标准表达 `waiting_authorization`。
- InteractionPayload 渲染“需要飞书用户授权”。
- Card / Portal 只展示授权入口，不处理授权业务逻辑。
- 完成授权后，用户可重试同一 `RuntimeActionInput`。
