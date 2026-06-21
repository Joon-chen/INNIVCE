# Execution Identity Audit

本文件回答：当前 Runtime Capability 实际依赖什么执行身份。

本阶段只审计，不修改代码，不实现身份系统。

## 1. Identity 类型定义

| Identity | 含义 | 当前风险 |
| --- | --- | --- |
| BOT | 企业应用身份，通常对应 tenant access token / app access token | 适合公司资源和机器人可见范围，不代表个人授权 |
| USER | 当前用户身份，目标应是 user access token | 当前部分实现把 USER 映射成 `lark-cli --as user` |
| CLI_PROFILE | 本机或容器内 `lark-cli --profile` 凭证 | 不可天然迁移到云端，强依赖运行环境 |
| ADMIN | 管理员或后台管理身份 | 不应作为普通 Runtime Action 的默认执行身份 |
| UNKNOWN | 代码或配置证据不足，无法确认 | 必须阻断写操作或要求显式配置 |

结论：

```text
BOT / USER 适合作为产品语义。
TENANT_TOKEN / USER_TOKEN / CLI_PROFILE / ADMIN_SESSION 才是执行凭证语义。
```

当前系统最大的问题是：

```text
RuntimeCapability.execution_identity = "user"
↓
Provider params["as"] = "user"
↓
lark-cli --as user
```

这把“应该由谁执行”混成了“本机 CLI 是否已登录”。

## 2. 证据链

| 证据 | 位置 | 结论 |
| --- | --- | --- |
| RuntimeCapability 只有 `execution_identity` 字段 | `app/services/runtime_v5/capabilities.py` | Capability 只声明 `bot/user` |
| FeishuResourceProvider 把 identity 写入 tool params | `app/services/runtime_v5/feishu_resource_providers.py` | `tool_params.setdefault("as", request.execution_identity)` |
| Feishu MCP provider 给 lark-cli 注入 profile | `app/services/tools/providers/feishu_mcp.py` | CLI profile 是真实执行凭证之一 |
| Approval query/detail 使用 FeishuApprovalService + app_config | `app/services/runtime_v5/feishu_resource_providers.py` | 查询可走服务端 Tenant/App 路径 |
| Approval API 写入需要 user_access_token | `app/services/feishu/api_runtime.py` | 审批动作语义上必须 USER |
| Task CLI 写入使用 `lark-cli task ... --as user` | `app/services/tools/providers/feishu_mcp.py` | 当前 Task 写入依赖 CLI user profile |
| Feishu 用户 OAuth 已有存储链路 | `app/services/feishu_admin_apps.py` | 系统已有 USER_TOKEN 基础，但 Task Runtime 未统一消费 |

## 3. Capability -> Runtime Strategy -> Provider -> Identity

### Approval

| Capability | Runtime Strategy | Provider Operation | Declared Identity | Actual Credential |
| --- | --- | --- | --- | --- |
| approval_query | approval_query | list_pending / FeishuApprovalService.fetch_pending_tasks | BOT | TENANT/App + current open_id |
| approval_detail | approval_detail | get_detail / FeishuApprovalService.get_instance | BOT | TENANT/App |
| approval_approve | approval_approve | feishu_approval_task_approve | USER | USER_TOKEN if FEISHU_API path; CLI_PROFILE if MCP path |
| approval_reject | approval_reject | feishu_approval_task_reject | USER | USER_TOKEN if FEISHU_API path; CLI_PROFILE if MCP path |
| approval_transfer | approval_transfer | feishu_approval_task_transfer | USER | USER_TOKEN if FEISHU_API path; CLI_PROFILE if MCP path |
| approval_add_sign | approval_add_sign | feishu_approval_task_add_sign | USER | USER_TOKEN if FEISHU_API path; CLI_PROFILE if MCP path |

判断：

- Approval 查询为什么能跑：它不依赖 `lark-cli --as user`，而是通过公司 Feishu App 配置和当前 `open_id` 拉取待审批。
- Approval 写入不是纯 BOT 能力。API path 明确要求 `user_access_token`；MCP path 则会落到 `lark-cli --as user`。
- 所以 Approval Runtime Sample 能跑，不等于 User Identity 模型已经稳定，只说明当前路径存在可用凭证。

### Task

| Capability | Runtime Strategy | Provider Operation | Declared Identity | Actual Credential |
| --- | --- | --- | --- | --- |
| task_query | task_query | task_qa / task +get-my-tasks | BOT | 声明 BOT，但 CLI handler 默认也支持 user；实际依赖 provider 配置 |
| task_search | task_query | task_qa / task +get-my-tasks | BOT | 同上 |
| task_create | task_create | feishu_task_create / task +create | USER | 当前 MCP path 依赖 CLI_PROFILE + `--as user` |
| task_complete | task_complete | feishu_task_complete / task update completed_at | USER | 当前 MCP path 依赖 CLI_PROFILE + `--as user` |

判断：

- Task 为什么卡住：云端容器没有完成 `lark-cli --as user` 所需的 user profile 授权。
- CLI Profile 只够“在某台机器上执行”，不够成为企业级 Runtime 身份模型。
- Task 写入未来应走 USER_TOKEN，而不是要求每台运行容器拥有本地 CLI 登录状态。

### People

| Capability | Runtime Strategy | Provider Operation | Declared Identity | Actual Credential |
| --- | --- | --- | --- | --- |
| people_lookup | people_lookup | contact user search / org snapshot | BOT | BOT / TENANT, 部分走 CLI bot/profile |
| department_members | department_members | organization snapshot | BOT | BOT / TENANT, 可读缓存 |
| organization_snapshot | organization_snapshot | organization snapshot | BOT | BOT / TENANT, 可读缓存 |
| organization_export | organization_export | base write + im send_result | USER | USER or CLI_PROFILE, 因为写表/发结果是动作 |

判断：

- People 查询属于公司资源，BOT/TENANT 是合理默认。
- People 导出不是单纯查询，一旦写 Base 或发消息，应视为 USER action。

### Knowledge

| Capability | Runtime Strategy | Provider Operation | Declared Identity | Actual Credential |
| --- | --- | --- | --- | --- |
| docs_read | docs_read | feishu_doc_read | BOT | BOT/TENANT 或 CLI_PROFILE，取决于 provider |
| wiki_search | wiki_search | feishu_wiki_search | BOT | BOT/TENANT 或 CLI_PROFILE |
| drive_list | drive_list | feishu_drive_file_list | BOT | BOT/TENANT 或 CLI_PROFILE |
| docs_edit / drive_upload / sheets_write | write actions | Feishu write tools | USER | USER_TOKEN or CLI_PROFILE |

判断：

- 企业知识读可以优先 Tenant 化。
- 个人云盘、未授权文档、编辑写入必须 USER。

### Communication

| Capability | Runtime Strategy | Provider Operation | Declared Identity | Actual Credential |
| --- | --- | --- | --- | --- |
| chat_search | chat_search | feishu_im_chat_search | BOT | BOT/TENANT or CLI_PROFILE |
| message_query | message_query | feishu_im_message_list | BOT | BOT/TENANT or CLI_PROFILE |
| message_send | message_send | feishu_im_send_message | USER | 当前可走 CLI `--as user`；若机器人主动发可走 BOT |
| chat_create | chat_create | feishu_im_create_chat | USER | USER or ADMIN/BOT，取决于组织策略 |
| mail_query | mail_query | mail_qa | 声明 BOT | 实际更接近 USER_TOKEN |
| mail_send / mail_draft_create | mail_draft_create | feishu_mail_drafts_create | USER | USER_TOKEN |

判断：

- IM 发送存在两种产品语义：机器人代发通知是 BOT；以员工个人身份发消息是 USER。
- Mail 是个人邮箱能力，应修正为 USER-first，而不是 BOT-first。

### Business

| Capability | Runtime Strategy | Provider Operation | Declared Identity | Actual Credential |
| --- | --- | --- | --- | --- |
| base_query | base_query | feishu_base_query | BOT | BOT/TENANT or CLI_PROFILE |
| base_create | base_create | feishu_base_create | USER | USER or ADMIN，取决于空间和权限 |
| base write_records | organization_export | bitable record write | USER | USER or ADMIN/CLI_PROFILE |

判断：

- Bitable 只是载体，客户/订单/供应商等业务数据读可以 Tenant 化。
- 创建表、写记录、改结构都应是 USER 或 ADMIN action，不能默认 BOT。

## 4. Capability x Identity Matrix

| Capability | Read/Write | Identity | Reason |
| --- | --- | --- | --- |
| approval_query | Read | BOT/TENANT | 查询当前用户待审批可由企业应用结合 open_id 获取 |
| approval_detail | Read | BOT/TENANT | 实例详情可由企业应用读取 |
| approval_approve | Write | USER | 审批动作必须代表审批人本人 |
| approval_reject | Write | USER | 拒绝必须代表审批人本人 |
| approval_transfer | Write | USER | 转交是审批人动作，目标人解析可用 BOT |
| approval_add_sign | Write | USER | 加签是审批人动作，目标人解析可用 BOT |
| task_query | Read | BOT or USER | 公司/团队任务可 BOT；我的任务更自然是 USER |
| task_create | Write | USER | 创建者应是用户本人 |
| task_complete | Write | USER | 完成任务应代表用户本人 |
| people_lookup | Read | BOT/TENANT | 组织通讯录是公司资源 |
| department_members | Read | BOT/TENANT | 组织通讯录是公司资源 |
| docs_read | Read | BOT/TENANT or USER | 公司知识可 BOT；个人/受限文档需 USER |
| docs_edit | Write | USER | 文档编辑需要资源权限主体 |
| wiki_search | Read | BOT/TENANT | 企业知识空间可 Tenant 化 |
| drive_list | Read | BOT/TENANT or USER | 公司盘可 BOT；个人云盘需 USER |
| mail_query | Read | USER | 邮箱是个人资源 |
| mail_draft_create | Write | USER | 邮件草稿属于个人邮箱 |
| message_send | Write | BOT or USER | 机器人通知可 BOT；个人代发需 USER |
| chat_create | Write | USER/ADMIN | 创建群涉及组织治理和成员权限 |
| base_query | Read | BOT/TENANT | 企业业务数据读取可 Tenant 化 |
| base_create | Write | USER/ADMIN | 创建业务表结构是高风险写动作 |
| base_write_records | Write | USER/ADMIN | 写业务记录需明确执行人 |

## 5. Approval 重点结论

### approval_query / approval_detail

当前能跑的原因：

- Runtime provider 直接通过 `FeishuApprovalService` 读取。
- 依赖公司 app_config、当前用户 open_id、tenant/app token。
- 不要求云端容器存在 `lark-cli --as user`。

### approval_approve / approval_reject

当前身份状态：

- Capability 声明为 USER。
- API runtime 路径要求 `user_access_token`。
- MCP/CLI 路径会执行 `lark-cli ... --as user`。

所以真实结论是：

```text
approval_approve / approval_reject 已经是 USER 语义，
但执行凭证可能是 USER_TOKEN，也可能是 CLI_PROFILE。
```

### approval_transfer / approval_add_sign

当前身份状态：

- 动作本身是 USER。
- 目标人解析可以是 BOT。
- 真正执行 Provider 前必须完成 USER_TOKEN 或 CLI_PROFILE 凭证确认。
- 当前阶段本来没有开放 transfer/add_sign 执行，应继续保持 Runtime Input Only。

## 6. Task 重点结论

### task_query

当前声明是 BOT，但实际有两个方向：

- 企业视角：团队/部门/公司任务查询可以 BOT/TENANT。
- 个人视角：我的任务更应 USER_TOKEN。

当前 `task_qa` 走 CLI handler 时，默认命令是 `task +get-my-tasks`，这类语义更接近 USER。

### task_create / task_complete

当前声明是 USER，判断正确。

真实写入卡住的原因：

```text
RuntimeActionInput
→ Runtime
→ FeishuTaskProvider
→ Tool Router
→ Feishu MCP provider
→ lark-cli task +complete --as user
→ 云端容器未配置 user profile
```

结论：

```text
Task 写入不应该长期依赖云端 CLI user profile。
应该改为 Runtime 自动取当前用户的 Feishu User OAuth Token。
```

## 7. 哪些能力依赖本机身份

高风险依赖：

- Task 写入：create / complete / update / delete / assign / comment / tasklist。
- Approval 写入的 MCP path：approve / reject / transfer / add_sign / rollback 等。
- Calendar 创建。
- IM 个人身份发送、建群、加入群。
- Base 写入、建表、改结构。
- Drive/Docs/Sheets 写入。

中风险依赖：

- 部分读能力虽然声明 BOT，但实际通过 MCP/CLI provider 执行时仍依赖容器内 CLI profile。
- Mail query 已有 OAuth 读取路径，但 Capability 声明仍偏 BOT，语义不一致。

## 8. 哪些能力已经较完整 Tenant 化

较清晰：

- Approval query/detail 的服务端 API 路径。
- People organization snapshot / contact search 的 BOT 查询路径。
- Company profile / WorkEvent / Memory / Knowledge internal AI。
- Approval Snapshot / Evidence / Insight 的内部认知链路。

条件性 Tenant 化：

- Docs/Wiki/Drive/Base/IM read，如果 provider 配置走 FEISHU_API 且 app 权限足够，则可以 Tenant 化；如果走 FEISHU_MCP，则仍依赖 CLI profile。

## 9. 哪些能力未来必须 User 授权

必须 USER_TOKEN：

- Task create / complete / update / delete / comment / assign。
- Approval approve / reject / transfer / add_sign / rollback。
- Mail query / mail send / mail draft。
- Calendar create/update/delete，特别是个人日历。
- Attendance query。
- Personal Drive/Docs/Sheets read/write。
- 以个人名义发送 IM。

可能 ADMIN：

- 创建公共群或修改组织级群治理策略。
- 创建公司级 Base/Wiki 空间。
- 组织通讯录全量导出。
- 跨部门/全公司范围的数据写入。

## 10. Identity 模型建议

不要把 `BOT` / `USER` 扩成一堆业务特例。

建议拆成两层：

```text
actor_identity:
  BOT | USER | ADMIN

credential_mode:
  TENANT_TOKEN | USER_TOKEN | CLI_PROFILE | ADMIN_SESSION
```

最小规则：

| 场景 | actor_identity | credential_mode |
| --- | --- | --- |
| 公司资源读 | BOT | TENANT_TOKEN |
| 个人资源读 | USER | USER_TOKEN |
| 个人写动作 | USER | USER_TOKEN |
| 管理员治理动作 | ADMIN | ADMIN_SESSION |
| 本地开发/临时兼容 | USER/BOT | CLI_PROFILE |

CLI_PROFILE 应降级为：

```text
local/dev compatibility credential
```

不应作为云端 Runtime 的长期主路径。

## 11. 回答关键问题

### 1. Approval 为什么能跑

因为 Approval 查询/详情已经有服务端 API 路径，依赖公司 Feishu App 配置和当前 `open_id`，不强依赖 `lark-cli --as user`。

Approval 写入如果能跑，说明当前环境存在 USER_TOKEN 或 CLI user profile；它不证明身份模型已经稳定。

### 2. Task 为什么卡 User Identity

因为 Task 写入当前落到了 MCP/CLI provider，实际执行为 `lark-cli task ... --as user`。云端容器没有完成对应 CLI user 授权，所以卡住。

### 3. 当前系统哪些能力实际上依赖本机身份

所有走 FEISHU_MCP 且使用 `--as user` 的写能力都依赖本机或容器 CLI profile。Task 写入是第一个真实暴露问题的样板。

### 4. 哪些能力已经完全 Tenant 化

Approval query/detail、People 部分查询、内部认知链路最接近 Tenant 化。其他飞书读能力是否完全 Tenant 化取决于 provider 配置是否走 FEISHU_API，而不是 MCP/CLI。

### 5. 哪些能力未来必须 User 授权

个人资源和代表个人意志的动作都必须 User 授权：Task 写入、Approval 写入、Mail、Calendar 个人日程、Attendance、个人云盘/文档、个人身份发消息。

## 12. 下一步建议

进入：

```text
Execution Identity Contract Design
```

只冻结合同，不实现全量身份系统。

建议最小输出：

1. `ExecutionIdentityContract`
2. `CredentialMode`
3. Capability Identity Matrix Freeze
4. Task `complete_task` 的目标执行路径：`USER_TOKEN` 优先，`CLI_PROFILE` 仅开发 fallback
5. Approval write path 的目标执行路径：统一 `USER_TOKEN`

暂缓：

- 全量 OAuth 改造
- 全量 Provider 迁移
- Admin identity 实现
- CLI profile 删除
- UI 授权入口重构
