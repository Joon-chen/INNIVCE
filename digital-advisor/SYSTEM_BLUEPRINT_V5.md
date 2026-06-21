# 老司机（企业数字参谋）V5 系统蓝图

本蓝图以 `老司机（企业数字参谋）V5.xmind` 为最新架构源，结合当前
`digital-advisor` 已实现后端进行重整。目标不是继续堆功能，而是把已有代码迁移到
V5 的稳定分层里：Message Gateway、Agent Runtime、Tool Router、Data Layer、Interaction Layer。

## 0. 开发原则

1. 以 XMind 为准：新架构名词、层级和能力边界以 XMind 为最新版本。
2. 保留有价值资产：已能支撑 V5 的模型、同步、权限、机器人回答、驾驶舱、飞书 API 封装继续保留。
3. 删除无用残留：旧概念、重复入口、没有测试保护且不再属于 V5 边界的代码分阶段删除。
4. 新架构直接替换旧边界：先确认有价值能力，再迁入新边界；无价值旧入口不保留兼容。
5. 每阶段有验收：每个阶段必须有可运行检查，不以“看起来完成”为验收标准。
6. 飞书能力以官方为准：所有飞书相关功能都优先用 `lark-cli` 做 schema、事件、API 调试和验证；`lark-cli` 也是 Agent 实时执行飞书资源的优先引擎，但必须藏在 Tool Router 后面，Agent Runtime 不允许直接调用 CLI/API/MCP。接口行为以飞书开放平台官网文档和 CLI schema 为准，不凭经验猜字段。这个原则覆盖 Gateway、Agent、Tool Router、Feishu API/MCP provider、同步引擎、资源发现和管理后台。
7. API 仍是同步层核心：API Client 主要用于 Auth Manager、Sync Engine、数据采集、入库、WorkEvent 生成、历史分析和长期存储；实时问答和实时写操作优先走 Tool Router 下的飞书 CLI/MCP 能力，不在 Agent Runtime 内重复封装 API。
7. 全局产品原则：系统的核心目的始终是“Owner 的多公司智能中心 + 各公司员工的个人智能体”。飞书是企业数据和运营的主平台，也是本智能系统的主要数据来源；所有模块设计都必须服务这个目标。

## 1. 产品定位

系统名称：老司机（企业数字参谋）

定位：老板管理多家公司的经营驾驶舱和智能中心，同时也是各个公司、各个员工的个人工作智能体。飞书是企业数据和运营的主平台，也是本智能系统的主要数据来源；本系统在飞书之上形成智能中心。
它不是普通聊天机器人，而是 Digital Chief of Staff：持续读取企业运营数据、个人工作数据、
知识库、审批、任务、日程、会议、邮件和外部情报，向不同身份的人提供不同权限范围内的回答、
报告、风险提醒、任务追踪和决策支持。

核心用户：

- Owner：跨公司经营驾驶舱、风险、决策、报告、关键任务。
- 公司高管：授权公司或业务域内的经营与协同信息。
- 部门经理：部门、项目、客户、审批、任务和会议动态。
- 员工：本人相关事项、当前群上下文、公开知识和授权资源。

核心目标：

- 对 Owner：把多家公司分散在飞书、邮箱、审批、会议、任务、知识库和业务表里的信息，汇聚成可决策的智能中心。
- 对员工：在权限范围内成为个人智能体，回答本人工作、当前群协作、授权业务域和公开知识相关问题。
- 对公司：以飞书为主运营及数据平台，所有飞书 API/MCP/CLI 能力都按官方文档核对后纳入工具体系。

## 2. V5 目标架构

```text
交互入口
  -> Message Gateway
    -> Agent Runtime
      -> Tool Router
        -> 飞书 API / 飞书 MCP / 本地 Tool / 报告 Tool / 开发运维 Tool
          -> Data Layer
```

### 2.1 交互入口

目标入口：

- 各个公司的自建应用机器人：每家公司一个“大飞哥”，但每个人看到的是自己的独立智能体视角。
- 管理后台：公司账号、飞书连接、权限配置、工具管理、Agent 设置、系统日志、经营概览。后台不要做成传统 IT 管理系统，视觉上要美观、简洁、克制，参考 OpenAI 风格。
- iOS App：未来开发，当前不进入后端优先级。

当前可保留：

- `app/api/routes/feishu.py`：飞书 App、事件、消息、资源、审批、邮箱等管理 API。
- `app/api/routes/console.py` 和 `app/static/console/`：已有管理后台雏形，首屏已加入“多公司运营状态”智能中心视图，聚合公司、员工智能体、飞书资源、工作事件、同步异常、治理动作、工具边界状态和大飞哥 AI 兜底状态；V5 OS overview 在数据库暂不可用时会返回 `status.database=unavailable`、零计数和 entrypoints，前端 bootstrap 先请求 OS overview，发现数据库不可用时跳过 dashboard、companies、sync-status 等 DB 重接口，公司下拉会禁用并明确显示“数据库未连接”，且 `safeLoad` 在离线态只允许继续请求 OS overview，`safeAction` 会暂停写/操作按钮，确保后台首屏和设置/审计导航仍可读且不刷 500/POST 错误日志；工具管理页已支持查看工具、Provider、兼容 Provider、权限、写能力并保存启用状态和 Provider，并可对选中工具发起 dry-run/confirmed 执行，执行后可一键跳转到系统日志筛选对应工具动作；AI 设置页已支持公司级 Agent 基础配置，Agent 历史轨迹会显示写操作、dry-run 要求、写策略和确认要求；审计页已接入系统日志概览、筛选明细、分类统计、最近错误、工具执行日志、审批提交日志、写模式、确认令牌状态、审批确认校验字段、Gateway reason 筛选和未知卡片动作快捷筛选；当本地数据服务暂不可用时也会显示可读离线态。
- `app/api/routes/cockpit.py`：经营驾驶舱入口。

需要迁移：

- 管理后台不应直接暴露零散 Feishu API 调试风格入口，应逐步收敛为“连接、资源、权限、工具、日志、概览”五类页面。
- 飞书机器人入口需要统一进入 Message Gateway，而不是直接在 Feishu 路由里混合事件验证、消息归一化、回答生成和发送。

### 2.2 Message Gateway

职责：

- 收消息：解析飞书事件、按钮、命令、群聊、用户、公司、上下文。
- 预处理：识别用户身份、公司、群、资源、会话上下文。
- 规则命中：关键词、命令、按钮、卡片动作。
- 回消息：快速回复、思考回复、卡片交互、排版分层。

当前可保留：

- `app/services/feishu/client.py` 中的事件归一化和 `ingest_feishu_event`。
- `app/workers/feishu_ws.py` 中的飞书 WebSocket 事件处理。
- `app/services/agent/policies.py` 中已有的身份形状、权限前置判断和路由选择。
- `app/services/feishu/approval_advice.py`：已承接审批规则建议、文本建议、详细理由和附件摘要判断。
- `app/services/feishu/approval_cards.py`：已承接审批互动卡片 payload、toast、回调 value/message_id 解析，以及默认卡片标题、建议和展开详情渲染；`commands.py` 不再保留卡片 value/message_id 等兼容 wrapper。
- `app/services/feishu/approval_card_responder.py`：已承接审批互动卡片 callback response、详情展开/收起和按钮动作回复；发送文本回复和更新卡片内容已改为由 `approval_card_entrypoint.py` 注入，responder 不直接依赖 Gateway 发送实现；`approval_card_entrypoint.py` 已统一装配 WebSocket callback response、消息事件中的卡片动作依赖、审批卡片构建和互动卡片发送；`handle_feishu_command` 只调用 entrypoint，不再直接依赖 responder，也不保留卡片动作兼容 wrapper，`commands.py` 不再直接构建审批互动卡片或调用 `send_feishu_interactive_reply`。
- `app/services/feishu/approval_actions.py`：已承接审批动作准备、缺字段校验和待确认动作校验；审批卡片/命令 pending action 会存储内部同参 confirmation token，确认执行前重新校验，防止待确认参数变化；真实 approve/reject 提交不再由该模块直接调用飞书 service，机器人确认审批已进入 Tool Router 的 `feishu_approval_task_approve/reject` confirmed 路径，同时保留审批业务审计；`feishu/commands.py` 仅保留审批详情编排和业务编排调用；旧管理接口 `/api/feishu/apps/{app_config_id}/approvals/action` 已改走同一 Tool Router 写边界，默认 dry-run，真实提交必须带 `confirmed=true + confirmation_token`。
- 审批 approve/reject/transfer/remind/add_sign/rollback/cancel/cc 已按 `lark-cli approval tasks approve/reject/transfer/remind/add_sign/rollback --dry-run --as user`、`lark-cli approval instances cancel/cc --dry-run --as user` 与对应 schema 校准：同意/拒绝真实路径为 `/open-apis/approval/v4/tasks/pass|refuse`，转交真实路径为 `/open-apis/approval/v4/tasks/forward`，催办真实路径为 `/open-apis/approval/v4/instances/remind`，加签真实路径为 `/open-apis/approval/v4/tasks/add_sign`，退回真实路径为 `/open-apis/approval/v4/tasks/rollback`，审批实例撤回真实路径为 `/open-apis/approval/v4/instances/recall`，审批实例抄送真实路径为 `/open-apis/approval/v4/instances/add_cc`；同意/拒绝请求体只提交 `instance_code`、`task_id`、`comment/form`，转交请求体提交 `instance_code`、`task_id`、`transfer_user_id` 和可选 `comment`，催办请求体提交 `instance_code`、`task_ids` 和可选 `comment`，加签请求体提交 `instance_code`、`task_id`、`add_sign_user_ids`、`add_sign_type`、可选 `approval_method/comment`，退回请求体提交 `instance_code`、`task_id`、`node_ids` 和可选 `comment`，审批实例撤回只提交 `instance_code`，审批实例抄送提交 `instance_code`、`cc_user_ids` 和可选 `comment/user_id_type`，并且必须使用个人飞书 `user_access_token`，不能用租户 token 或 `open_id` 伪造写入身份。
- Task 写能力继续按飞书 CLI 证据扩展：`feishu_tasklist_create` 已按 `lark-cli schema task.tasklists.create` 和 `lark-cli task +tasklist-create --dry-run` 接入 `POST /open-apis/task/v2/tasklists`；`feishu_task_add_to_tasklist` 已按 `lark-cli task +tasklist-task-add --dry-run` 接入单个任务加入清单的 `POST /open-apis/task/v2/tasks/:task_guid/add_tasklist`；`feishu_task_set_ancestor` 已按 `lark-cli task +set-ancestor --dry-run` 接入设置父任务的 `POST /open-apis/task/v2/tasks/:task_guid/set_ancestor_task`；`feishu_task_clear_ancestor` 已按 `lark-cli task +set-ancestor --task-id ... --dry-run --as user` 且不传 `--ancestor-id` 证明的同一路径空 body 接入清空父任务关系；`feishu_tasklist_update_members` 已按 `lark-cli schema task.tasklists.add_members/remove_members` 和 `lark-cli task +tasklist-members --dry-run` 接入清单成员 add/remove；`feishu_tasklist_set_members` 已按 `lark-cli task +tasklist-members --set ... --dry-run --as user` 证明的 GET 清单详情流程接入全量替换，系统内先读取当前成员再按差异调用 add/remove；`feishu_task_update_reminders` 已按 `lark-cli schema task.tasks.patch` 和 `lark-cli task tasks patch --dry-run` 接入 `PATCH /open-apis/task/v2/tasks/:task_guid` 的 `positive_reminders` 更新。
- 通讯录组织读能力已按 `lark-contact` 与 `lark-openapi-explorer` 边界接入 Tool Router：`feishu_contact_scope_list` 依据飞书官方“获取通讯录授权范围”文档和 `lark-cli api GET /open-apis/contact/v3/scopes --dry-run --as bot` 接入，用于解释应用身份可见的数据覆盖；`feishu_contact_department_children` 依据飞书官方“获取子部门列表”文档和 `lark-cli api GET /open-apis/contact/v3/departments/0/children --dry-run --as bot` 接入；`feishu_contact_department_users` 依据飞书官方“获取部门直属用户列表”文档和 `lark-cli api GET /open-apis/contact/v3/users/find_by_department --dry-run --as bot` 接入；`feishu_contact_organization_snapshot` 基于上述两个官方组织读接口读取组织快照，用于多公司成员、权限和员工个人智能体基础数据，走 `contact:read` 权限。
- `app/services/feishu/approval_formatters.py`：已承接审批项选择、待审批列表、审批详情行、附件读取结果、附件状态、审批命名、申请人/单号提取、字段优先级、表单摘要和字段显示值等纯 formatter。
- `app/services/feishu/approval_resources.py`：已承接待审批资源注册、同步附件结果合并、历史相似审批匹配和审批附件结果 payload 序列化。
- `app/services/feishu/approval_runtime.py`：已承接审批上下文优先读取、live 待审批拉取后的富化流水线、审批详情回复和审批建议回复编排。
- `app/services/feishu/approval_context.py`：已承接审批上下文、待确认审批动作的 Redis key、TTL、内部确认 token 和序列化边界。
- `app/services/feishu/sync_commands.py`：已承接邮箱、审批、通讯录同步命令回复，以及问题触发 quick sync 的意图识别和参数组装。
- `app/services/feishu/command_parser.py`：已承接飞书机器人消息的 chat/sender 提取、群聊 @ 判断、命令文本解析、别名归一化和审批上下文意图判断；`feishu/commands.py` 仅保留兼容包装和业务编排调用。
- `app/services/feishu/command_dispatcher.py`：已承接 normalized command 到具体回复能力的主分发规则，`handle_feishu_command` 保留事件解析、发送和记录链路。
- `app/services/feishu/organization.py`：已承接飞书通讯录管理人员查询、通讯录快照读取、组织架构 Markdown/XMind 大纲生成和组织架构回复编排。
- `app/services/feishu/identity.py`：已承接机器人身份模型、发送者身份识别、管理员兜底、权限拒绝文案、身份回复和 Celery identity payload 序列化。
- `app/services/feishu/bot_runtime.py`：已承接员工 Agent 问答调用、Bot 会话记录、route/scope 提取、驾驶舱回答生成与重写；真实飞书机器人入口会读取公司级 Agent 设置，把 Planner 开关、最大规划步数、写工具启停和写确认策略传入 Agent Runtime。
- `app/services/feishu/work_event_replies.py`：已承接本地 WorkEvent/ExtractedItem 的审批历史兜底、近期邮件、开放待办、事件行格式化和安全错误摘要。
- `app/services/feishu/approval_enrichment.py`：已承接审批附件摘要补读、审批 LLM 建议批量附加和单条建议生成依赖编排。
- `app/services/feishu/approval_service_runtime.py`：已承接审批任务列表解析、用户待审批任务拉取、实例详情富化和审批资源回复数据组装。

需要新建边界：

- `app/services/gateway/`：统一承接所有消息入口。
- `gateway/message.py`：标准消息结构，如 `GatewayMessage`、`GatewayActor`、`GatewayContext`。
- `gateway/feishu.py`：只负责把飞书事件转成标准消息，不负责业务回答。
- `gateway/responder.py`：统一快速回复、异步思考回复、卡片排版。
- `gateway/audit.py`：已将 GatewayMessage 安全摘要写入 `AuditLog`，记录事件、会话、回复目标、处理状态和原因，不保存原始 payload 和消息正文。
- Celery 后台任务中的待审批异步回复、异常回复和日报推送已改走 `gateway/responder.py` 的 `send_feishu_text_reply`，不再直连 `FeishuClient.send_message`。

飞书核对来源：

- 官方文档：`https://open.feishu.cn/document/`
- CLI schema：`lark-cli event schema im.message.receive_v1`
- CLI 运维：`lark-cli event list`、`lark-cli im +messages-send --help`、`lark-cli im +chat-messages-list --help`

验收：

- 飞书事件、卡片动作、后台测试消息都能被转成同一个 `GatewayMessage`。
- 单测覆盖：用户身份、群聊、公司、命令、按钮动作解析。

### 2.3 Agent Runtime

职责：

- 意图识别：默认模型、备用模型、本地降级。
- 任务规划：决定是否需要工具、需要哪些工具、是否需要多步。
- 权限检查：每次检索、工具调用、回答前都必须检查。
- 记忆管理：长期事实、用户偏好、上下文摘要。
- 调用工具：只通过 Tool Router，不直接调用飞书 API 或本地数据库散落查询。
- 组织答案：按用户身份、风格、权限范围输出。

当前已落地/可保留：

- `app/services/agent/runtime.py`：已承接“语义识别 -> 路由 -> 分域回答 -> 改写”的主链路。
- `answer_agent_message_with_trace`、`/api/v5/agent/trace-preview` 和 `/api/v5/agent/traces`：已提供后台可见的语义、路由、工具/Advisor/拒绝执行轨迹、最近历史列表，并写入 `agent.trace.preview` 审计日志；trace-preview 会把公司级 `allow_write_tools` 和 `require_write_confirmation` 一并写入审计。
- `app/services/agent/planner.py`：已建立非执行型 Planner 边界；后台 trace-preview 会按公司 Agent 设置开启计划步骤，展示 guardrail、工具/Advisor、最终回答阶段，并标注写工具禁用、要求确认或允许执行的策略状态；写工具计划步骤会输出 `requires_dry_run` 和 `confirmed_execution_requires=["dry_run=true","confirmed=true","confirmation_token"]`。
- Agent Runtime 已把公司级写工具策略变成执行前硬门禁：`allow_write_tools=false` 时拒绝任何写工具执行；`require_write_confirmation=true` 时写工具停在 dry-run/confirmation_token 流程，不进入真实执行；运行 trace 会以机器可读字段记录 `requires_dry_run` 和真实执行所需确认条件；后台 trace-preview 和真实飞书机器人入口共用同一组公司设置。
- `app/services/llm/`：LLM Gateway、回答改写、审批建议、语义识别。
- `app/services/agent/context.py`：用户偏好、记忆事实、回答风格。
- `app/services/access_control.py` 和 `app/services/permissions.py`：权限过滤基础。

需要迁移：

- 将领域问答能力沉淀为 Agent 可调用的 `tools/*`，由 Tool Router 统一调度。
- 把“规则：IM API 负责收消息；LLM 负责理解问题和组织语言；Tool 负责干活；API 负责取数据”固化为代码边界。

验收：

- Runtime 不直接调用 Feishu client。
- Runtime 工具调用全部经过 Tool Router，写工具在调用 Tool Router 前先经过公司级写策略门禁。
- 单测覆盖：普通聊天、当前群总结、个人任务、审批问答、公司问答、权限拒绝。

### 2.4 Tool Router

职责：

- 统一注册工具：ApprovalTool、KnowledgeTool、BitableTool、ChatTool、CalendarTool、MeetingTool、ReportTool、AutomationTool、PeopleTool。
- 决定工具实现：飞书 API、飞书 MCP、本地 Tool、报告 Tool、开发运维 Tool。
- 执行前检查：权限、参数、格式、审计、错误处理。
- 执行后标准化：统一返回 `ToolResult`，给 Agent Runtime 组织答案。

当前可保留：

- `app/services/feishu/*`：飞书 API 能力已经比较完整。
- `app/services/cockpit/*`：经营概览模块可作为 ReportTool 的基础。
- `app/services/ai/reports.py`：日报生成基础。
- `app/services/resource_registry.py`、`resource_sources.py`、`v5_resources.py`：资源目录基础。

需要新建边界：

- `app/services/tools/base.py`：已建立 `ToolRequest`、`ToolResult`、`ToolContext`、`ToolProvider`、`ToolDefinition`、`ToolExecutionStatus`。
- `app/services/tools/router.py`：已建立工具注册表、provider 分派、权限闸口和执行审计入口。
- `app/models/entities.py`：已建立 `ToolConfig`，支持按公司覆盖工具启停、provider 和配置。
- `app/api/routes/v5.py`：已暴露 `/api/v5/tools`、`/api/v5/tools/{tool_name}`、`/api/v5/tools/{tool_name}/execute` 和 `/api/v5/tools/executions` 管理接口；后台可通过统一 Tool Router 入口对 Bitable、Task、审批等写工具执行 dry-run/confirmed，不再新增分散写 API。
- `app/services/tools/approval.py`、`knowledge.py`、`bitable.py`、`chat.py`、`calendar.py`、`report.py`、`domain.py` 等已按 XMind 9 个业务工具族挂载到 Tool Router。
- `app/services/tools/providers/feishu_api.py`：已建立经官方文档和 `lark-cli` 核对的飞书能力注册表；现有 provider key 仍为 `feishu_api` 以保持 ToolConfig 兼容，但能力边界已明确 `preferred_execution_engine=lark_cli`、`realtime_policy=lark_cli_first`、`api_role=sync_collection_storage` 和 `agent_runtime_direct_access=false`。写操作必须先 dry-run，dry-run 文案会显示实时执行优先引擎、CLI 命令和写目标摘要，任务/日程创建会展示基于 `summary` 的标题，真实执行必须带回同参 `confirmation_token` 与 `confirmed=true`。
- `feishu_im_send_message` 已按 `lark-im` skill、`lark-shared` 认证规则、`lark-cli im +messages-send --help` 和 `--dry-run --as bot` 验证后，迁入 Tool Router 后的 CLI 优先 confirmed 路径；真实执行使用参数数组调用 `lark-cli im +messages-send --as bot|user --format json --chat-id/--user-id --text --idempotency-key`，不经 shell 拼接。测试注入 `client` 时仍保留 API runtime 路径，作为单元测试和受控兜底。
- `feishu_task_create/update/complete/comment` 已按 `lark-task` skill、`lark-shared` 认证规则、`lark-cli task +create/+update/+complete/+comment --help` 和对应 `--dry-run --as user` 验证后，迁入 Tool Router 后的 CLI 优先 confirmed 路径；真实执行使用参数数组调用 `lark-cli task +create --data`、`lark-cli task +update --task-id --data`、`lark-cli task +complete --task-id`、`lark-cli task +comment --task-id --content`，保留现有任务 JSON 语义。测试注入 `client` 时仍保留 API runtime 路径，作为单元测试和受控兜底。
- `app/services/feishu/api_runtime.py`：已承接 Feishu API provider 的 Calendar、Task、Mail、Bitable、Approval instance/task、OKR cycle/objective、Contact scope/department/user/snapshot 真实读执行绑定，其中 Bitable 字段结构读取已按 `lark-cli base +field-list --dry-run` 接入 `base/v3` 字段列表 API；OKR 周期和目标列表已按 `lark-okr` skill、schema 和 `lark-cli okr +cycle-list/+cycle-detail --dry-run --as user` 接入 `GET /open-apis/okr/v2/cycles` 与 `GET /open-apis/okr/v2/cycles/:cycle_id/objectives`，仅开放只读并走 `okr:read` 权限；Contact 组织和授权范围读能力按官方通讯录文档和 `lark-cli api ... --dry-run` 裸调验证接入 `GET /open-apis/contact/v3/scopes`、`GET /open-apis/contact/v3/departments/:department_id/children` 与 `GET /open-apis/contact/v3/users/find_by_department`，走 `contact:read` 权限；同时承接审批 approve/reject/transfer/remind/add_sign/rollback/cancel/cc、IM 文本消息发送、IM 建群、IM 公开群自动加入、日程创建、任务创建/更新/提醒更新/负责人分配/关注人维护/完成/重新打开/评论、多维表格记录创建/批量创建/更新/批量更新/删除/建表/建字段/字段更新/视图创建/视图重命名真实写执行绑定；读/写 runtime 通过显式绑定表暴露，新增能力必须同时进入 capability、Tool Registry 和 runtime 绑定表。
- `app/services/tools/providers/local.py`：已承接本地库、长期记忆和领域问答。
- `app/services/tools/providers/report.py`：已接入 `company_qa`、`owner_cockpit` 和 cockpit 报告能力。
- `app/services/tools/providers/devops.py`：已建立边界并接入只读 `feishu_cli_status` 与 `feishu_cli_doctor`；后台可通过 Tool Router 检查本机 `lark-cli` 路径、版本、固定命令退出码、关键命令覆盖、命令摘要和 `doctor --offline` JSON 健康摘要；关键命令覆盖已扩展到 V5 的 approval、attendance、base、calendar、contact、docs、drive、event、im、mail、minutes、okr、task、vc、wiki；不开放任意 CLI 命令执行。管理后台工具执行面板已区分读/写工具：读工具直接执行，写工具仍必须 dry-run 后带回确认令牌。
- `app/services/tools/providers/feishu_mcp.py`：已建立必须显式绑定、默认禁写的 MCP provider 合同；工具配置 API 会通过 `provider_boundaries` 明确该合同，并回显 `binding_status=unbound` 与 `blocked_reason=explicit_mcp_tool_binding_required`；后台运行提示会显示 MCP 只负责工具调度、不直接执行动作，动作执行器是 `lark_cli`，并显示显式工具绑定、默认写入关闭和当前阻断原因。
- `tests/test_v5_architecture.py` 已锁定 Agent Runtime 飞书边界：`app/services/agent` 不得直接引用 `lark-cli`、`FeishuClient`、Feishu API runtime 或 Feishu MCP provider；Agent 只能通过 `execute_agent_tool` 进入 Tool Router，再由具体 Tool 调用 CLI/MCP/API/Local DB。

迁移规则：

- 现有 Feishu service 不重写，先包成 provider。
- 首批本地领域工具已迁入正式 `tools/*` 模块；BitableTool、CalendarTool、ChatTool、AutomationTool、MeetingTool、KnowledgeTool、PeopleTool 等本地或实时读能力（Mail 归 ChatTool，Task 归 AutomationTool，日历/日程归 CalendarTool，历史会议搜索归 MeetingTool，Docs/Wiki/Drive 归 KnowledgeTool）和 Feishu MCP provider 实时读绑定、API runtime 受控验证绑定、OKR 周期/目标只读工具、Contact 授权范围/组织/成员只读工具、审批 approve/reject/transfer/remind、IM 文本消息发送、IM 建群、IM 公开群自动加入、日程创建、任务创建/更新/子任务创建/负责人分配/关注人维护/完成/重新打开/评论/设置父任务/清单成员维护与多维表格记录创建/批量创建/更新/批量更新/删除/建表/建字段/字段更新真实写执行绑定、dry-run/同参 confirmation_token 二次确认边界、provider、权限闸口、执行审计、工具配置模型、Provider 兼容性校验、工具执行入口和工具执行日志查询 API、写工具模式审计、系统日志概览与筛选明细 API、Gateway 消息安全摘要日志、后台工具管理页、工具 dry-run/confirmed 面板、工具执行到系统日志联动和后台审计视图已接入；管理后台工具列表已展示 `business_tool` 并统计业务工具族数量，工具执行面板已覆盖全部 Tool Registry 参数模板。HTTP 事件和 WebSocket card action response 中的未知卡片动作会记录为 `gateway.feishu.card_action` 且 reason 为 `unhandled_card_action`，后台系统日志按 warning 展示并提供“卡片未处理”快捷筛选，避免机器人卡片入口问题被误归类为普通消息未命中机器人；V5 OS overview 已暴露 `entrypoints` 和 `release_readiness`，后台首屏会显示大飞哥 AI 兜底、数据库、飞书 CLI 执行层和飞书职责边界状态，数据库不可用、大飞哥 AI 兜底关闭或 `lark-cli` 缺失都会使上线检查降级；`.env.example` 默认开启 `FEISHU_BOT_AI_MODE_ENABLED=true`，新部署的大飞哥具备自然语言 Agent Runtime 兜底。Bitable、Task、Contact、OKR、IM、Approval 等能力继续按飞书官方文档、`lark-cli --dry-run` 和对应 lark skill 验证后接入；所有 Feishu API 写能力均有统一测试证明未经 `dry_run` 或 `confirmed=true + confirmation_token` 会被拒绝；普通成员对 Bitable、Task、Approval 等写工具以及 OKR/Contact 公司级只读工具会被 Tool Router 权限闸口拒绝；Feishu API capability、Tool Registry 和 API runtime 读/写绑定表已有一致性测试锁定。
- Bitable 批量删除记录已按 `lark-cli base +record-delete --dry-run`、`lark-base` skill 和飞书官方“删除多条记录”文档验证后接入 `POST /open-apis/base/v3/bases/:base_token/tables/:table_id/records/batch_delete`；系统内仅承接明确的 `record_id_list`，继续执行 dry-run/同参 `confirmation_token` 二次确认和写目标审计。
- Bitable `record-upsert` 已按 `lark-cli base +record-upsert --help`、`lark-cli base +record-upsert --dry-run --as user` 和 `lark-base` skill 验证后接入；系统内严格保持 CLI 语义：不传 `record_id` 时调用 `POST /open-apis/base/v3/bases/:base_token/tables/:table_id/records` 创建记录，传 `record_id` 时调用 `PATCH /open-apis/base/v3/bases/:base_token/tables/:table_id/records/:record_id` 更新该记录，不做业务键自动去重；写目标审计会展示 `upsert_mode=create|update_by_record_id`。
- Bitable 表重命名已按 `lark-cli base +table-update --dry-run --as user` 和 `lark-base` skill 验证后接入 `PATCH /open-apis/base/v3/bases/:base_token/tables/:table_id`；系统内仅提交 `name`，用于整理主数据表名，不承接未验证的复杂表结构变更。
- Bitable 字段更新已按 `lark-cli base +field-update --help`、`lark-cli base +field-update --dry-run --as user` 和 `lark-base` skill 验证后接入 `PUT /open-apis/base/v3/bases/:base_token/tables/:table_id/fields/:field_id`；由于 CLI 明确该接口是 full PUT 语义，系统内要求提交完整 `field.name + field.type`，暂不开放未验证的 formula/lookup 字段更新，避免高风险字段结构被误覆盖。
- Bitable 视图创建、重命名、删除、筛选、排序、分组、可见字段、卡片和时间轴配置已按 `lark-cli base +view-create/+view-rename/+view-delete/+view-set-filter/+view-set-sort/+view-set-group/+view-get-visible-fields/+view-set-visible-fields/+view-get-card/+view-set-card/+view-get-timebar/+view-set-timebar --help`、对应 `--dry-run --as user` 和 `lark-base` skill 验证后接入；runtime 分别调用 `POST/PATCH/DELETE /open-apis/base/v3/bases/:base_token/tables/:table_id/views...`、`PUT .../filter|sort|group|visible_fields|card|timebar` 和 `GET .../visible_fields|card|timebar`，系统内仅开放 `grid/kanban/gallery/calendar/gantt` 视图类型、名称修改、显式 view_id 删除、`logic + conditions` 筛选、最多 10 项 `sort_config` 排序、最多 3 项 `group_config` 分组、最多 200 个 `visible_fields` 可见字段顺序配置、`cover_field` 卡片封面字段和 `start_time/end_time/title` 时间轴字段配置。
- 审批加签按 `lark-cli approval tasks add_sign --dry-run` 与 `approval.tasks.add_sign` schema 验证后接入 `POST /open-apis/approval/v4/tasks/add_sign`，系统内仅处理明确的 `add_sign_user_ids`、`add_sign_type` 和可选 `approval_method`；写目标审计会展示 `add_sign_user_ids`，避免 dry-run 确认时遗漏加签目标。
- 审批退回按 `lark-cli approval tasks rollback --dry-run` 与 `approval.tasks.rollback` schema 验证后接入 `POST /open-apis/approval/v4/tasks/rollback`，系统内仅处理明确的 `node_ids`；写目标审计会展示 `node_ids`，避免 dry-run 确认时遗漏退回节点。
- 审批实例撤回按 `lark-cli approval instances cancel --dry-run --as user`、`approval.instances.cancel` schema、`lark-approval` skill 和飞书官方“撤回审批实例”文档验证后接入 `POST /open-apis/approval/v4/instances/recall`；系统内仅处理明确的 `instance_code`，真实执行必须使用个人飞书 `user_access_token` 并经过 dry-run/同参 `confirmation_token`。
- 审批实例抄送按 `lark-cli approval instances cc --dry-run --as user`、`approval.instances.cc` schema、`lark-approval` skill 和飞书官方“抄送审批实例”文档验证后接入 `POST /open-apis/approval/v4/instances/add_cc`；系统内仅处理明确的 `instance_code`、`cc_user_ids` 和可选 `comment/user_id_type`，真实执行必须使用个人飞书 `user_access_token` 并经过 dry-run/同参 `confirmation_token`。
- `tests/test_v5_architecture.py` 已增加 Feishu 管理路由和写 service 防回归检查，禁止 `app/api/routes/feishu.py` 直接调用 native write/send client，并锁定 Task、Bitable、Approval、IM 写 service 只能由 `app/services/feishu/api_runtime.py` 调用；外部写入口必须进入 Tool Router 或 Gateway responder。公开群 auto-join 旧管理 helper 只保留 dry-run 候选预览，真实加入只能走 Tool Router confirmed 路径。
- 工具必须声明 `required_permissions`、`source_type`、`supports_write`、`audit_action`。

验收：

- 已完成 Approval、Bitable、Chat、Company、Domain、Knowledge、Personal、Calendar、Mail、Task、General conversation 首批工具路由包装。
- ToolConfig 切换到 `feishu_api` 时，Tool Router 会自动注入当前公司 active 的 FeishuAppConfig。
- 所有工具调用产生审计日志或可追踪执行记录。
- 单测覆盖：API 优先、本地优先、禁用工具、权限拒绝、错误格式。

### 2.5 Data Layer

职责：

- 本地数据：公司、用户、角色、权限、资源、工作事件、报告、审计。
- 向量数据：Qdrant 语义检索。
- 长期记忆：稳定事实、个人偏好、群聊上下文。
- 数据来源：飞书企业级资源（应用身份 App）、飞书个人级资源（用户身份 User）、外部邮箱、个人钉钉。
- 数据类型：Operational Data 落 PostgreSQL/索引，Memory Data 落长期记忆事实，Knowledge Data 进入内部 Document Store + Vector DB 或外部 WEB 来源。
- 数据规则：企业级资源用应用身份，个人级资源用用户身份；应用身份优先，用户身份补充；外部邮箱和个人钉钉只能按个人授权范围进入。

当前可保留：

- `Company`、`Account`、`FeishuAppConfig`、`User`、`Role`、`Permission`、`UserCompanyRole`。
- `Resource`、`ResourceSource`、`ResourcePermission`、`ResourceSyncRun`。
- `WorkEvent`、`Attachment`、`ExtractedItem`、`Report`、`AuditLog`、`SyncRun`。
- `MemoryFact`、`BotUserAccess`、`BotUserSession`、`BotUserPreference`。
- `app/services/ai/vector_search.py`：向量索引基础。

需要调整：

- `ResourceSource` 已按新版 XMind 建立身份型数据来源 taxonomy：`feishu_app_identity`、`feishu_user_identity`、`external_mail_account`、`personal_dingtalk_account`、`local_import`；旧 `feishu_app`、`feishu_user`、`mail_account` 等名称仍可被归一化读取，`external_web` 只作为 Knowledge Data 的外部 WEB 索引层，不再作为和飞书/邮箱/钉钉同级的数据来源展示。新增迁移 `0013_data_layer_source_taxonomy.py` 会回填旧数据。
- `WorkEvent` 入库已统一调用 source normalizer，旧来源名会在写入时归一到新版 taxonomy。
- `sync_strategy_overview()` 已暴露 `data_sources`、`data_types`、`query_policies` 和同步层查询路径，其中 `data_sources` 仅展示飞书企业身份、飞书个人身份、外部邮箱和个人钉钉四类身份型来源；V5 Resource payload 已回显 `data_type` 与 `storage_layer`；Operational Data 已明确 `lark_cli_first`，实时查询优先走飞书 CLI，重要业务数据再同步为 WorkEvent；Memory Data 已归一为 `memory` 资源，进入 `long_term_memory` 策略层并落 `memory_facts`；Knowledge Data 已区分 L1 热知识本地 RAG、L2 冷知识只登记并用飞书 CLI 实时查询、L3 外部知识实时搜索不入库，外部 WEB 已归一为 `web` 资源并进入独立 `knowledge_external_web` 策略层，只保存引用、摘要、来源和可信度，不作为本地向量库写入目标。资源同步状态、`sync_v5_resource()` 执行结果、文档索引 WorkEvent payload 和 Owner 资源健康 payload 已回显 `query_path`，用于解释每类资源是 CLI 实时查询、索引缓存、本地 RAG、外部搜索还是长期记忆读取。
- `ResourceSource.source_type` 与 `WorkEvent.source_type` 已在模型和 `0014_source_type_constraints.py` 迁移中增加数据库 check constraint，防止绕过 normalizer 写入旧来源名或非 V5 taxonomy 值。
- `MemoryFact.scope` 已在模型和 `0015_memory_fact_scope_constraints.py` 迁移中锁定为 `company/domain/personal/user/chat`；`personal/user` 必须绑定 `user_open_id`，`chat` 必须绑定 `chat_id`；长期记忆读取统一走 `memory_fact_access_condition()`，company 范围不默认读取所有人的个人记忆，chat 范围只读取当前群记忆和本人个人记忆。
- `FeishuResource` 已经是旧资源模型，只在迁移有价值数据时短期存在；资源发现、旧 operations 手工登记、待审批任务注册、快速公司初始化邮箱资源和公开群自动加入均已停止写旧 `FeishuResource`，新资源主写入统一为 V5 `Resource`。旧资源迁移兜底已集中到 `resource_registry.migrate_legacy_feishu_resources()`，审批资源列表、审批同步和 Drive folder 发现种子不再直接查询旧模型；架构测试已把旧模型直接引用限制在模型定义、operations 迁移统计、V5 administration 全量迁移和 resource registry 集中迁移 helper。旧 `/api/feishu/resources` 列表入口、operations 概览统计和快速初始化邮箱资源响应均以 V5 `Resource` 字段为主，历史旧表关联只通过 `legacy_resource_id`/`legacy_feishu_resource_id` 做迁移核对。
- Operations 资源摘要已增加 `legacy_retirement`，按旧表总数、启用旧资源数、已映射到 V5 Resource 数、未映射旧资源数判断 `can_drop_legacy_table` 和下一步动作；未映射为 0 时才能进入删除旧表阶段。
- `Account.account_type` 已在模型和 `0016_account_type_taxonomy.py` 迁移中锁定为 `company_feishu_app/personal_feishu_user/feishu_mail/external_mail/personal_dingtalk`；`provider` 继续表示具体技术驱动，`account_type` 表示 V5 数据层账号边界，飞书个人 OAuth 和外部邮箱创建路径已写入该字段。
- `ResourceSource` 是双身份取数规则的核心，已明确 app/user/external/local 优先级并补齐数据库层 check constraint。
- 长期记忆表已区分 company/domain/personal/user/chat scope 并补齐读取过滤；后续如果新增 department/project scope，需要先补模型枚举、迁移和访问条件。

验收：

- 每条 `WorkEvent` 能追溯公司、资源、来源账号、身份类型、权限范围。
- 应用身份与用户身份同一资源合并时可解释优先级。
- 单测覆盖资源权限过滤和工作事件权限过滤。

## 3. 当前实现资产盘点

### 3.1 应保留并迁移的资产

- FastAPI 应用骨架：`app/main.py`。
- 数据模型和 Alembic 迁移：现有 V5 基础表已经覆盖公司、资源、权限、事件、报告、记忆。
- 飞书连接层：`FeishuClient` 已有 SDK/Raw HTTP 双层规则，符合 XMind 的 API Client 方向。
- 飞书业务服务：审批、通讯录、多维表格、云文档、日历、会议、邮箱、任务、OKR、IM。
- 资源发现和同步：`feishu/resources.py`、`feishu/sync.py`、`v5_workspace.py`。
- 机器人回答链路：意图识别、身份路由、分域回答、回答风格改写。
- 驾驶舱：经营概览模块与跨公司 scope。
- 权限系统：角色、资源、知识、工作事件访问过滤。
- AI 基础：抽取、日报、向量索引、长期记忆、审批建议。

### 3.2 需要重命名或收敛的资产

- 旧机器人领域问答命名已迁到 `tools` 语义；意图、上下文、策略已迁到 `agent/intents.py`、`agent/context.py`、`agent/policies.py`。
- `v5_*` 服务命名是阶段性产物，稳定后应归并到 `resources`、`sync`、`administration`、`runtime`。
- `feishu.py` 路由过大，混合后台配置、调试、资源发现、消息处理，后续拆成连接管理、事件入口、工具调试、资源同步。
- `Digital Advisor OS V5.md` 实际是 RTF 格式，不适合作为开发蓝图源，已删除；V5 蓝图以 `SYSTEM_BLUEPRINT_V5.md`、`V5_MASTER_DEVELOPMENT_PLAN.md` 和 XMind 为准。

### 3.3 候选删除项

以下只列候选，不在没有测试保护前直接删除：

- `.DS_Store`、`.pycache`、`.pytest_cache`、`.ruff_cache`、`feishu_mail_advisor.egg-info` 等生成物。
- `FeishuResource` 旧 ORM 已删除；旧 `feishu_resources` raw SQL 迁移兜底等 0019 发布迁移确认后再删。
- 直接面向旧版资源的调试 API：迁移到管理后台工具调试后删除。
- 与 V5 XMind 不一致的旧报告/旧机器人入口：先找引用和测试，再决定删除。

## 4. 新目录规划

```text
app/
  services/
    gateway/
      message.py
      feishu.py
      responder.py
      commands.py
    agent/
      runtime.py
      planner.py
      intents.py
      memory.py
      answer.py
      policies.py
    tools/
      base.py
      router.py
      approval.py
      knowledge.py
      bitable.py
      chat.py
      calendar.py
      report.py
      providers/
        feishu_api.py
        feishu_mcp.py
        local.py
        report.py
        devops.py
    data/
      resources.py
      work_events.py
      memory.py
      access.py
```

迁移时不一次性移动所有文件。先建立薄包装和测试，再逐步把旧模块内部调用改到新边界。

## 5. 阶段路线图

### Phase 1：蓝图和边界冻结

目标：

- 完成本文档。
- 建立架构决策：XMind 为最新源，当前代码按 V5 分层迁移。
- 明确哪些旧代码保留、迁移、候选删除。

验证：

- `SYSTEM_BLUEPRINT_V5.md` 存在且覆盖 XMind 五层。
- 不改业务代码，不影响现有测试。

### Phase 2：Message Gateway 最小落地

目标：

- 新增 `services/gateway` 标准消息模型。
- 飞书事件和后台测试消息统一转为 `GatewayMessage`。
- 现有 Feishu webhook 的有效能力迁入 Gateway，旧处理路径不长期保留。

验证：

- 新增 gateway 单测。
- 现有 `test_feishu.py`、`test_feishu_ws.py` 通过。

### Phase 3：Agent Runtime 正式入口

目标：

- 新增 `services/agent/runtime.py`，承接核心问答编排。
- 明确 Runtime 只做意图、规划、权限、工具调用、答案组织。
- 删除旧 `bot_answering.py` 入口。

验证：

- `test_agent_runtime.py` 和相关 bot domain 测试通过。
- 新增 runtime 边界测试：不直接依赖 Feishu client。

### Phase 4：Tool Router 最小可用

目标：

- 新增工具基础协议和路由器。
- 首批迁移 ApprovalTool、ChatTool、KnowledgeTool。
- 支持工具启用/禁用、本地优先/API 优先、审计记录。

验证：

- 新增 `test_tool_router.py`。
- 审批问答、当前群总结、公开知识问答通过工具路由仍能返回。

### Phase 5：Data Layer 双身份规则

目标：

- 强化 `ResourceSource`：应用身份和用户身份的取数优先级。
- 让资源同步策略明确“企业级资源 App 优先，个人级资源 User 补充”。
- 对 `WorkEvent` 补齐来源账号、可见范围、数据分类。

验证：

- 新增资源合并与权限过滤测试。
- `test_v5*` 和 `test_work_events.py` 通过。

### Phase 6：管理后台收敛

目标：

- 管理后台按 XMind 重排：系统设置、经营概览、网页版机器人。
- 工具管理支持启用/禁用、本地优先/MCP 优先。
- Agent 设置已支持默认模型、最大工具调用次数、最大规划步骤、Planner、Memory、回答风格、执行轨迹和写工具二次确认；后续补温度等模型参数。

验证：

- 后台 API 返回工具配置、Agent 设置、Agent trace 预览与历史列表、系统日志。
- 静态控制台可加载并显示核心页面。

### Phase 7：删除旧残留

目标：

- 删除生成物和无用文档。
- 删除已完全替代的旧资源模型和旧调试入口。
- 清理旧命名和旧入口，只保留确有外部依赖且仍有价值的稳定 API。

验证：

- 全量测试通过。
- `ruff check app tests` 通过。
- 数据迁移脚本能解释旧表到新表的迁移路径。

## 6. 近期优先级

1. 建立 `gateway` 标准消息结构。
2. 建立 `agent` runtime 正式入口。
3. 建立 `tools` 基础协议和 Tool Router。
4. 将 Approval、Chat、Knowledge 三个最高频能力接入 Tool Router。
5. 再处理后台页面和旧代码删除。

不建议优先做：

- iOS App。
- 大规模重写 Feishu service。
- 一次性移动所有 `bot_*` 和 `v5_*` 文件。
- 在 Tool Router 尚未落地前继续扩展更多零散 API。

## 7. 总体验收标准

- 飞书机器人能按用户身份、权限、上下文回答问题。
- Owner 能看跨公司经营概览，普通员工不能越权查看。
- 审批、知识、多维表格、聊天、日历、报告都通过 Tool Router 调用。
- 所有入库事件都能追溯来源、资源、身份、权限和审计。
- 管理后台能配置公司账号、飞书连接、权限、工具、Agent 和日志。
- 旧版有价值能力被迁移，旧残留被测试保护后删除。
