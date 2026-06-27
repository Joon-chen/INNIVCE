# Execution Identity Contract Design

本文件冻结 Execution Identity 的 V1 合同。

本阶段只做合同设计，不修改 Runtime 代码，不接 OAuth，不迁移 Provider。

## 1. 当前问题

Task Runtime Sample 暴露的问题不是 `RuntimeActionInput`，而是执行身份被混用了：

```text
execution_identity = user
↓
tool params["as"] = user
↓
lark-cli --as user
↓
云端容器缺少 user CLI profile
```

这说明当前系统把两件事混在一起：

| 概念 | 应回答的问题 |
| --- | --- |
| Actor Identity | 这个动作代表谁执行？ |
| Credential Mode | Runtime 用什么凭证执行？ |

V1 合同必须先拆开这两个概念。

## 2. Contract 目标

Execution Identity Contract V1 只解决：

1. Capability 声明执行主体。
2. Runtime 解析执行凭证模式。
3. Provider 不再猜测身份。
4. CLI Profile 被明确降级为 fallback。
5. User Token 成为个人资源和个人动作的目标主路径。

不解决：

- OAuth 接入实现。
- Provider 全量迁移。
- Admin 身份系统。
- UI 授权入口。
- CLI Profile 删除。

## 3. ActorIdentity

```json
{
  "actor_identity": "BOT | USER | ADMIN | SYSTEM | UNKNOWN"
}
```

| ActorIdentity | 含义 | 典型场景 |
| --- | --- | --- |
| BOT | 企业应用或机器人执行 | 公司资源读取、机器人通知、内部认知链路 |
| USER | 当前用户本人执行 | 审批动作、任务写入、个人邮箱、个人日历 |
| ADMIN | 管理员执行 | 组织级治理、全局配置、高风险后台动作 |
| SYSTEM | 系统异步任务执行 | Snapshot Builder、WorkEvent 处理 |
| UNKNOWN | 未确认执行主体 | 必须阻断写动作 |

规则：

- `actor_identity` 是产品语义。
- `actor_identity=USER` 不等于已经有 user access token。
- `actor_identity=BOT` 不等于任何数据都可读，仍要过 Policy。

## 4. CredentialMode

```json
{
  "credential_mode": "TENANT_TOKEN | USER_TOKEN | CLI_PROFILE | ADMIN_SESSION | INTERNAL | NONE | UNKNOWN"
}
```

| CredentialMode | 含义 | 允许范围 |
| --- | --- | --- |
| TENANT_TOKEN | 飞书企业应用凭证 | 公司资源读取、Bot 可执行动作 |
| USER_TOKEN | 用户 OAuth access token | 个人资源、代表用户的动作 |
| CLI_PROFILE | lark-cli 本机/容器 profile | 本地开发、临时兼容、明确 fallback |
| ADMIN_SESSION | 管理员后台会话或授权 | 管理中心动作 |
| INTERNAL | 系统内部数据和 AI 能力 | WorkEvent、Snapshot、Memory、Knowledge |
| NONE | 无需外部凭证 | 纯计算、纯渲染 |
| UNKNOWN | 未确认凭证 | 必须阻断执行 |

规则：

- 云端 Runtime 的目标主路径不得依赖 `CLI_PROFILE`。
- `CLI_PROFILE` 可以作为开发 fallback，但必须在 Result/Log 中显式标记。
- 写动作不得在 `credential_mode=UNKNOWN` 时执行。

## 5. ExecutionIdentityContract

建议合同结构：

```json
{
  "actor_identity": "USER",
  "credential_mode": "USER_TOKEN",
  "credential_owner": {
    "company_id": "",
    "open_id": "",
    "user_id": "",
    "cli_profile": ""
  },
  "resource_scope": "SELF | USER | TEAM | DEPARTMENT | COMPANY",
  "requires_authorization": true,
  "allows_cli_fallback": false,
  "reason": ""
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| actor_identity | 动作代表谁执行 |
| credential_mode | Runtime 实际用什么凭证 |
| credential_owner.company_id | 凭证所属公司 |
| credential_owner.open_id | 用户级凭证所属 open_id |
| credential_owner.user_id | 系统用户 ID |
| credential_owner.cli_profile | 仅 CLI fallback 时使用 |
| resource_scope | 查询/动作范围 |
| requires_authorization | 是否要求用户授权 |
| allows_cli_fallback | 是否允许 CLI 临时执行 |
| reason | 选择该身份模式的原因 |

## 6. Runtime 解析规则

Runtime 执行前必须得到 `ExecutionIdentityContract`。

解析顺序：

```text
Capability Declaration
↓
Scope Context
↓
Policy Decision
↓
Execution Identity Resolution
↓
Provider Execution
```

最小决策表：

| Capability 类型 | ActorIdentity | CredentialMode |
| --- | --- | --- |
| 公司资源读 | BOT | TENANT_TOKEN |
| 个人资源读 | USER | USER_TOKEN |
| 个人写动作 | USER | USER_TOKEN |
| 机器人通知 | BOT | TENANT_TOKEN |
| 管理员治理动作 | ADMIN | ADMIN_SESSION |
| 内部认知任务 | SYSTEM | INTERNAL |
| 本地开发 fallback | USER/BOT | CLI_PROFILE |

阻断规则：

- `actor_identity=UNKNOWN` 阻断写动作。
- `credential_mode=UNKNOWN` 阻断所有 Provider 执行。
- `credential_mode=CLI_PROFILE` 在云端生产环境默认不允许，除非 Capability 显式 `allows_cli_fallback=true`。
- `actor_identity=USER` 且缺少用户授权时，返回 `WAITING_AUTHORIZATION`，不要降级为 BOT。

## 7. Capability Identity Matrix Freeze

### Workspace / Task

| Capability | ActorIdentity | CredentialMode | CLI Fallback |
| --- | --- | --- | --- |
| task_query: self | USER | USER_TOKEN | allowed in dev only |
| task_query: team/department/company | BOT | TENANT_TOKEN | allowed in dev only |
| task_create | USER | USER_TOKEN | allowed in dev only |
| task_complete | USER | USER_TOKEN | allowed in dev only |
| task_update/delete/comment/assign | USER | USER_TOKEN | allowed in dev only |

Task `complete_task` 目标路径：

```text
RuntimeActionInput
→ Policy
→ ExecutionIdentityContract(USER, USER_TOKEN)
→ User OAuth Token lookup by company_id + open_id
→ Feishu Task Provider
→ RuntimeResult(task_complete)
```

### Process / Approval

| Capability | ActorIdentity | CredentialMode | CLI Fallback |
| --- | --- | --- | --- |
| approval_query | BOT | TENANT_TOKEN | not needed |
| approval_detail | BOT | TENANT_TOKEN | not needed |
| approval_approve | USER | USER_TOKEN | allowed in dev only |
| approval_reject | USER | USER_TOKEN | allowed in dev only |
| approval_transfer | USER | USER_TOKEN | not enabled in V1 |
| approval_add_sign | USER | USER_TOKEN | not enabled in V1 |

Approval write 目标路径：

```text
RuntimeActionInput
→ Policy
→ ExecutionIdentityContract(USER, USER_TOKEN)
→ User OAuth Token lookup
→ Approval API Provider
→ RuntimeResult(approval_action)
```

### People

| Capability | ActorIdentity | CredentialMode | CLI Fallback |
| --- | --- | --- | --- |
| people_lookup | BOT | TENANT_TOKEN | allowed in dev only |
| department_members | BOT | TENANT_TOKEN | allowed in dev only |
| organization_snapshot | BOT | TENANT_TOKEN | allowed in dev only |
| organization_export | USER | USER_TOKEN | allowed in dev only |

### Communication

| Capability | ActorIdentity | CredentialMode | CLI Fallback |
| --- | --- | --- | --- |
| chat_search | BOT | TENANT_TOKEN | allowed in dev only |
| message_query | BOT | TENANT_TOKEN | allowed in dev only |
| message_send as bot notification | BOT | TENANT_TOKEN | allowed in dev only |
| message_send as user | USER | USER_TOKEN | allowed in dev only |
| mail_query | USER | USER_TOKEN | no |
| mail_send / mail_draft_create | USER | USER_TOKEN | no |

### Knowledge

| Capability | ActorIdentity | CredentialMode | CLI Fallback |
| --- | --- | --- | --- |
| company docs/wiki/drive read | BOT | TENANT_TOKEN | allowed in dev only |
| personal docs/drive read | USER | USER_TOKEN | no |
| docs/drive/sheets write | USER | USER_TOKEN | allowed in dev only |

### Business

| Capability | ActorIdentity | CredentialMode | CLI Fallback |
| --- | --- | --- | --- |
| base_query company data | BOT | TENANT_TOKEN | allowed in dev only |
| base_create | ADMIN or USER | ADMIN_SESSION or USER_TOKEN | dev only |
| base_write_records | USER | USER_TOKEN | dev only |

### Intelligence

| Capability | ActorIdentity | CredentialMode | CLI Fallback |
| --- | --- | --- | --- |
| workevent summarize | SYSTEM | INTERNAL | no |
| snapshot build | SYSTEM | INTERNAL | no |
| memory candidate generation | SYSTEM | INTERNAL | no |
| insight generation | SYSTEM | INTERNAL | no |

## 8. RuntimeActionInput 影响

V1 不要求立刻修改 `RuntimeActionInput`。

未来建议在 `metadata` 中加入：

```json
{
  "execution_identity": {
    "actor_identity": "USER",
    "credential_mode": "USER_TOKEN",
    "requires_authorization": true
  }
}
```

规则：

- Action Payload 可以携带期望身份。
- Runtime 最终决策以 Policy + Capability + Context 为准。
- Card / Portal / SidePanel 不得自行决定 credential_mode。

## 9. RuntimeResult 影响

未来建议在 `RuntimeResult.metadata` 中加入：

```json
{
  "execution_identity": {
    "actor_identity": "USER",
    "credential_mode": "USER_TOKEN",
    "authorization_status": "authorized | missing | expired | denied",
    "fallback_used": false
  }
}
```

规则：

- Interaction 只展示授权状态或失败原因。
- Interaction 不处理 token、不调用 OAuth、不选择 Provider。
- 如果缺少授权，应返回 `WAITING_AUTHORIZATION` 或明确的 failed result。

## 10. Provider 边界

Provider 不决定业务身份。

Provider 只接收已解析的执行合同：

```text
ProviderRequest
  context
  params
  execution_identity_contract
```

Provider 允许做：

- 使用传入凭证调用 Feishu。
- 返回凭证缺失、过期、权限不足。
- 记录 provider-level error。

Provider 禁止做：

- 自行把 USER 降级为 BOT。
- 自行把 USER_TOKEN 改成 CLI_PROFILE。
- 自行决定是否需要审批确认。
- 自行触发 OAuth 授权流程。

## 11. Authorization 状态

建议冻结状态：

```text
AUTHORIZED
MISSING_AUTHORIZATION
EXPIRED_AUTHORIZATION
INSUFFICIENT_SCOPE
IDENTITY_MISMATCH
UNKNOWN
```

Runtime 行为：

| 状态 | Runtime 行为 |
| --- | --- |
| AUTHORIZED | 继续执行 |
| MISSING_AUTHORIZATION | 返回 WAITING_AUTHORIZATION |
| EXPIRED_AUTHORIZATION | 尝试刷新；失败后 WAITING_AUTHORIZATION |
| INSUFFICIENT_SCOPE | FAILED，提示缺少授权范围 |
| IDENTITY_MISMATCH | FAILED，阻断执行 |
| UNKNOWN | FAILED，阻断执行 |

## 12. CLI Profile 降级规则

CLI Profile 只能作为：

```text
local/dev compatibility fallback
```

允许条件：

- Capability 显式允许 fallback。
- 环境显式允许 fallback。
- RuntimeResult metadata 标记 `fallback_used=true`。
- 执行日志记录 profile 名称，但不得记录密钥。

禁止条件：

- 生产环境默认禁止。
- 高风险动作默认禁止。
- 缺少用户授权时不得静默改走 CLI。
- CLI 执行失败不得自动换身份重试。

## 13. 最小验收标准

Execution Identity Contract V1 完成后，应能回答：

1. 每个 Capability 代表谁执行。
2. 每个 Capability 应使用什么凭证。
3. 云端生产是否允许 CLI fallback。
4. 缺少 User Token 时 Runtime 应返回什么状态。
5. Task complete 为什么不能再依赖 `lark-cli --as user`。
6. Approval write 为什么应统一到 USER_TOKEN。

## 14. 下一阶段建议

进入：

```text
Execution Identity Contract Guard Phase
```

只加最小 Guard，不做全量迁移。

建议任务：

1. 新增合同类型定义。
2. Capability Registry 增加 identity contract 字段。
3. Contract Test 锁住 Task/Approval identity matrix。
4. Runtime 执行前生成 identity contract。
5. Task complete 缺少 USER_TOKEN 时返回 WAITING_AUTHORIZATION。

暂缓：

- Task Provider USER_TOKEN 实现。
- Approval Provider 全量 USER_TOKEN 迁移。
- Admin identity。
- OAuth UI 改造。
- CLI profile 移除。
