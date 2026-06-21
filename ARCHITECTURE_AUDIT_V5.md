# V5 架构审计与迁移清单

审计日期：2026-06-13

审计目标：在不牺牲 V5 架构完整性的前提下，确认当前 `digital-advisor` 已开发资产、质量基线、架构差距和下一步实施顺序。

全局产品原则：系统不是单一聊天机器人，而是 Owner 管理多家公司的智能中心，也是每个公司员工的个人智能体。飞书是企业数据和运营的主平台，也是本智能系统的主要数据来源。后续所有架构、数据、权限、工具和回答策略都必须围绕这个目标取舍。

交互入口约束：

1. 各个公司的自建应用机器人：大飞哥。
2. 管理后台：美观简洁，参考 OpenAI 风格，不做成传统 IT 管理后台。
3. iOS App：后续开发。

飞书开发原则：所有飞书相关实现优先使用本机 `lark-cli` 和飞书开放平台官网文档核对，不猜 API 字段或事件结构。AI 驱动的实时操作链路固定为 `Agent Runtime -> Tool Router -> Tool -> MCP -> CLI -> Feishu`，Agent Runtime 不允许直接调用 CLI/API/MCP；Tool Router 只负责业务能力路由，MCP 只负责工具调度，CLI 只负责实时动作执行。数据同步与入库链路固定为 `Sync Engine -> API Client -> Feishu -> PostgreSQL`，Sync Engine 禁止调用 MCP，但允许直接调用 API Client；API 只负责数据同步、数据采集、入库、WorkEvent 生成、历史分析和长期存储，不作为 Agent 实时动作执行层。该原则覆盖 Gateway、Agent、Tool Router、Feishu API/MCP provider、同步引擎、资源发现和管理后台，禁止混合职责。已确认本机 CLI 为 `/opt/homebrew/bin/lark-cli`。

## 1. 基线结果

### 1.1 测试

命令：

```bash
cd digital-advisor
.venv312/bin/python -m pytest
```

结果：

```text
720 passed, 1 warning
```

说明：

- `.venv312/bin/pytest -q` 当前可直接执行，并作为主验证命令。
- 前端脚本语法用 `node --check app/static/console/app.js` 验证；Python lint 用 `.venv312/bin/ruff check ...`。
- 当前 warning 来自第三方 `lark_oapi`，不是本项目代码失败。

### 1.3 飞书 CLI 基线

命令：

```bash
lark-cli --help
lark-cli event list
lark-cli event schema im.message.receive_v1
lark-cli im +messages-send --help
lark-cli im +chat-messages-list --help
```

结果：

- 本机命令：`/opt/homebrew/bin/lark-cli`
- 当前版本：`lark-cli version 1.0.52`
- 事件消费命令支持：`event list`、`event schema`、`event consume`
- IM 消息事件：`im.message.receive_v1`
- IM 消息事件关键字段：`event_id`、`message_id`、`sender_id`、`chat_id`、`chat_type`、`content`、`message_type`
- 消息发送命令支持 bot/user 身份、chat-id/user-id、text/markdown/post/media、idempotency key

### 1.2 Lint

命令：

```bash
cd digital-advisor
.venv312/bin/python -m ruff check app tests alembic/versions/0016_account_type_taxonomy.py
```

结果：

```text
All checks passed!
```

本轮为建立干净基线做了 4 个外科式修复：

- 将账号邮件资源注册逻辑从 `create_company` 误放位置移到 `create_account`。
- 删除 `access_control.py` 中未使用的 `WorkEvent` import。
- 删除 `feishu/commands.py` 中未使用的 `extract_items_for_event` import。
- 给 `tests/test_feishu.py` 补充 `Any` 导入。

## 2. 当前资产状态

### 2.1 API 路由

当前路由文件：

- `app/api/routes/health.py`
- `app/api/routes/companies.py`
- `app/api/routes/feishu.py`
- `app/api/routes/mail.py`
- `app/api/routes/work_events.py`
- `app/api/routes/operations.py`
- `app/api/routes/cockpit.py`
- `app/api/routes/v5.py`
- `app/api/routes/console.py`

判断：

- `feishu.py` 已收敛为 `/api/feishu` 子路由聚合器，只保留统一 prefix/tags 和 `feishu_*_routes.py` 子 router include；`app/api/routes/feishu_admin_app_routes.py` 已收敛为 App/OAuth/UserAccount 子路由聚合器，App 配置、OAuth、用户账号子路由也已继续收敛为聚合器，App 创建、tenant access token refresh、client routing、OAuth URL、OAuth token exchange、用户账号列表和 refresh 分别位于单 endpoint 子路由，请求模型位于 `feishu_admin_app_request_models.py`，业务逻辑已拆入 `app/services/feishu_admin_apps.py`；`app/api/routes/feishu_admin_sync_routes.py` 已收敛为同步子路由聚合器，消息同步、information sync 和资源发现入口分别位于 `feishu_admin_sync_message_routes.py`、`feishu_admin_sync_information_routes.py` 和 `feishu_admin_sync_resource_routes.py`，请求模型位于 `feishu_admin_sync_request_models.py`，同步业务逻辑已拆入 `app/services/feishu_admin_sync.py`；管理后台 sync-plan、capability probe 和审批资源列表 HTTP 入口已拆入 `app/api/routes/feishu_admin_capability_routes.py`，该文件已收敛为聚合器，三个具体入口分别位于单 endpoint 子路由，能力探测业务逻辑已拆入 `app/services/feishu_admin_capabilities.py`；管理后台 API client 预览/同步准备读 HTTP 入口只保留邮箱 accessible-mailboxes 辅助，位于 `feishu_admin_api_read_routes.py` -> `feishu_admin_api_read_mail_routes.py` -> `feishu_admin_api_read_mail_access_routes.py`，API read 业务逻辑已拆入 `app/services/feishu_admin_api_reads.py`；管理后台实时只读工具 HTTP 入口已拆入 `app/api/routes/feishu_admin_read_tool_routes.py`，实时读工具编排、通讯录 snapshot 审计和审批 pending 审计已拆入 `app/services/feishu_admin_read_tools.py`，管理后台发送消息、建群、公开群加入和审批动作 HTTP 入口已拆入 `app/api/routes/feishu_admin_write_routes.py`，Tool Router 调用、`ToolRequest` 组装和写审计已拆入 `app/services/feishu_admin_write_tools.py`，OAuth callback HTTP 入口已拆入 `app/api/routes/feishu_oauth_routes.py`，state/page helper 已拆入 `app/services/feishu_oauth_helpers.py`，飞书事件 HTTP 入口已拆入 `app/api/routes/feishu_event_routes.py`，事件 token 校验、URL challenge、事件入库和命令分发已拆入 `app/services/feishu_event_entrypoint.py`。
- `v5.py` 已经有 V5 管理雏形，覆盖资源、同步策略、权限预览、行政基础数据、工作区事件。
- `operations.py` 已从旧管理后台/运营接口集合收敛为子路由聚合器，只保留统一 `/api` prefix、admin 依赖和子 router include；薄 HTTP 入口已拆到 `operations_dashboard_routes.py`、`operations_advisor_routes.py`、`operations_system_status_routes.py`、`operations_automation_status_routes.py`、`operations_sync_routes.py`、`operations_extracted_routes.py`、`operations_report_routes.py`、`operations_audit_routes.py`、`operations_bot_user_upsert_routes.py`、`operations_bot_user_list_routes.py`、`operations_bot_permission_routes.py`、`operations_resource_registration_routes.py`、`operations_resource_list_routes.py`、`operations_memory_fact_create_routes.py`、`operations_memory_fact_list_routes.py`、`operations_memory_generation_routes.py`、`operations_entity_routes.py`，其中 `operations_bot_permission_routes.py` 已进一步收敛为 permission rule/recalculation 子路由聚合器；旧 `operations_read_routes.py`、`operations_bot_routes.py`、`operations_bot_user_routes.py`、`operations_memory_routes.py`、`operations_memory_fact_routes.py`、`operations_resource_routes.py` 和 `operations_status_routes.py` 已删除。这些 route 不再定义 Pydantic 请求模型、拆 `data.*` 字段、执行 SQL 或业务构造。飞书资源、MemoryFact、Bot 用户、advisor chat 和实体创建的请求模型、request wrapper 与业务 payload 继续由对应 `app/services/operations_*` service 承接。
- `cockpit.py` 是 Owner 经营驾驶舱的可保留入口。
- `work_events.py` 已收敛为 Data Layer 工作事件调试/维护子路由聚合器；`work_events_collection_routes.py`、`work_events_processing_routes.py` 和 `work_events_analysis_routes.py` 已继续收敛为聚合器，创建、列表、详情、抽取、单条向量化、日报、向量搜索和 pending 队列分别拆入单 endpoint 子路由，请求模型拆入 `work_events_request_models.py`；业务 payload 已拆入 `app/services/work_events_admin.py`。

### 2.2 数据模型

当前核心模型：

- 基础组织：`Company`、`CompanySetting`、`User`、`Department`、`Team`
- 账号连接：`Account`、`FeishuAppConfig`
- 权限：`Role`、`Permission`、`UserCompanyRole`、`ResourcePermission`、`KnowledgePermission`
- 资源：`Resource`、`ResourceSource`、`ResourceSyncRun`；旧 `FeishuResource` ORM 已删除，旧表只保留历史迁移和 raw SQL 迁移兜底。
- 事件与产物：`WorkEvent`、`Attachment`、`ExtractedItem`、`Report`
- 审计与同步：`AuditLog`、`SyncRun`
- 机器人与记忆：`BotUserAccess`、`BotUserSession`、`BotUserPreference`、`MemoryFact`
- 业务对象：`Person`、`Project`、`Customer`

判断：

- V5 Data Layer 已有较好基础，不应重写。
- `Resource` 和 `ResourceSource` 是最终资源模型核心。
- `FeishuResource` ORM 已从运行模型删除；旧 `feishu_resources` 表只应出现在历史迁移、raw SQL 迁移兜底和旧列表对照中；新资源登记和发现路径已经切到 V5 `Resource`。
- `WorkEvent` 字段已经支持 `source_type`、`source_account_id`、`visibility_scope`、`allowed_*`、`data_classification`、`business_domain`，符合 V5 权限和双身份规则方向。
- `MemoryFact` 已支持 company/domain/personal/user/chat scope，并已通过统一访问条件和数据库约束收紧长期记忆边界。

### 2.3 服务层

当前服务按功能大致分为：

- `feishu/*`：飞书 API、资源发现、同步、命令、OAuth、审批、文档、多维表格、会议、邮箱、任务、IM。
- `agent/runtime.py`、`agent/*` 和 `tools/*`：现有 Agent 意图、路由、回答、上下文、策略和领域工具。
- `cockpit/*`：经营驾驶舱模块。
- `ai/*`：抽取、报告、向量搜索、记忆、经营问答。
- `llm/*`：LLM Gateway、语义识别、回答改写、审批建议。
- `v5_*`：V5 资源、同步、治理、自动同步、行政初始化等阶段性服务。
- `resource_*`、`sync_runs.py`、`work_events.py`、`audit.py`：数据和同步基础。

判断：

- 功能资产丰富，但边界不够终局化。
- `gateway/*` 已经承接飞书事件归一化和标准消息模型，飞书命令入口不再直接解析原始事件。
- `agent/runtime.py` 已经承接核心 Agent Runtime 职责，并通过 Tool Router 调用首批本地领域能力。
- `tools/base.py`、`tools/router.py` 已经建立工具协议、工具定义注册表和路由边界；首批领域能力已迁入正式 `tools/*` 模块。
- `feishu/commands.py` 已降至 110 行，只保留 Gateway 消息入口、审计、命令分发和回复发送；命令 handler 装配、日报事务适配、审批文本命令桥接、审批上下文读写和附件结果读取已迁入 `feishu/command_handlers.py`；架构测试已锁定机器人入口只能通过 `command_dispatcher -> command_handlers -> bot_runtime` 触达 Agent Runtime，不能在入口层直接调用 `employee_bot_answer` 或 `answer_agent_message`。

### 2.4 测试资产

当前测试覆盖：

- Agent Runtime、工具路由和领域工具：`test_agent_runtime.py`、`test_tool_router.py`、`test_tools_*`
- 飞书 API 和事件：`test_feishu.py`、`test_feishu_ws.py`
- 驾驶舱：`test_cockpit.py`
- V5 基础和架构：`test_v5*.py`
- 权限和工作事件：`test_security.py`、`test_work_events.py`
- LLM 层：`test_llm_*`
- 任务、顾问、审批建议：`test_tasks.py`、`test_advisor.py`、`test_approval_advisor.py`

判断：

- 测试数量和覆盖足够支撑结构迁移。
- 迁移时应先复制/新增新边界测试，再移动调用方。
- 旧入口只在新架构尚未承接有价值能力时短期存在；承接后直接删除。

## 3. 模块到 V5 架构映射

| 当前模块 | 最终归属 | 处理方式 |
| --- | --- | --- |
| `app/api/routes/feishu.py` | 交互入口 + 管理后台 + Feishu provider 调试 | 拆分，不直接扩展 |
| `app/api/routes/feishu_admin_api_read_routes.py` | Feishu 管理后台 API client 预览/同步准备读子路由聚合器 | 已从 `feishu.py` 拆出并继续收敛为 Mail access 辅助入口；IM/Meeting/Wiki/Drive/Mail folder/Mail message 等实时读已迁至 Tool Router -> MCP -> CLI |
| `app/api/routes/feishu_admin_app_routes.py` | Feishu App/OAuth/UserAccount 子路由聚合器 | 已从 `feishu.py` 拆出并继续拆为 config/oauth/user-account 子路由 |
| `app/api/routes/feishu_admin_app_config_routes.py` | Feishu App 配置子路由聚合器 | 已继续拆为 create/token/routing 子路由 |
| `app/api/routes/feishu_admin_app_oauth_routes.py` | Feishu App OAuth 子路由聚合器 | 已继续拆为 url/exchange 子路由 |
| `app/api/routes/feishu_admin_app_user_account_routes.py` | Feishu App 用户账号子路由聚合器 | 已继续拆为 list/refresh 子路由 |
| `app/api/routes/feishu_admin_capability_routes.py` | Feishu 管理后台 capability 子路由聚合器 | 已从 `feishu.py` 拆出并继续拆为 sync-plan/probe/approval-resource 子路由 |
| `app/api/routes/feishu_admin_read_tool_routes.py` | Feishu 管理后台实时只读工具子路由聚合器 | 已从 `feishu.py` 拆出并继续拆为 approval/contact/calendar/drive/im/knowledge/wiki/bitable/task/mail/meeting 子路由 |
| `app/api/routes/feishu_admin_sync_routes.py` | Feishu 管理后台同步子路由聚合器 | 已从 `feishu.py` 拆出并继续拆为 message/information/resource 子路由 |
| `app/api/routes/feishu_admin_write_routes.py` | Feishu 管理后台写工具子路由聚合器 | 已从 `feishu.py` 拆出并继续拆为 IM/Approval 子路由 |
| `app/api/routes/feishu_event_routes.py` | Feishu 事件 HTTP 入口 | 已从 `feishu.py` 拆出 |
| `app/api/routes/feishu_oauth_routes.py` | Feishu OAuth callback HTTP 入口 | 已从 `feishu.py` 拆出 |
| `app/services/feishu_admin_apps.py` | Feishu App 配置、OAuth token 和用户账号后台管理 | 已从 `feishu.py` 拆出 |
| `app/services/feishu_admin_api_reads.py` | Feishu 管理后台 API client 预览/同步准备读入口 | 已从 `feishu.py` 拆出 |
| `app/services/feishu_admin_capabilities.py` | Feishu 管理后台 sync-plan、capability probe 和审批资源列表 | 已从 `feishu.py` 拆出 |
| `app/services/feishu_admin_sync.py` | Feishu 管理后台消息同步、information sync 和资源发现入库入口 | 已从 `feishu.py` 拆出 |
| `app/services/feishu_admin_read_tools.py` | Feishu 管理后台实时只读工具编排、通讯录 snapshot 审计和审批 pending 审计 | 已从 `feishu.py` 拆出 |
| `app/services/feishu_admin_write_tools.py` | Feishu 管理后台发送消息、建群、公开群加入和审批动作写入口 | 已从 `feishu.py` 拆出 |
| `app/services/feishu_oauth_helpers.py` | Feishu OAuth callback state/page helper | 已从 `feishu.py` 拆出 |
| `app/services/feishu_event_entrypoint.py` | Feishu 事件入口 token 校验、challenge、入库和命令分发 | 已从 `feishu.py` 拆出 |
| `app/services/feishu/client.py` | Feishu API Client + Auth Manager | 保留为同步/API Client 基础 |
| `app/services/feishu/sync.py` | Sync Engine + API Client 入库路径 | 保留，迁移入口 |
| `app/services/feishu/resources.py` | Resource discovery provider | 保留，输出统一 `Resource` |
| `app/services/feishu/commands.py` | Gateway message entrypoint + audit + reply dispatch | 保留入口职责 |
| `app/services/feishu/command_handlers.py` | Command handler assembly + thin business adapters | 已从入口拆出 |
| `app/services/agent/runtime.py` | Agent Runtime | 已落地，首批工具已通过 Tool Router 调用 |
| `app/services/agent/policies.py` | Agent policy + actor | 已迁移 |
| `app/services/agent/intents.py` | Agent intents | 已迁移 |
| `app/services/agent/context.py` | Agent memory + answer style | 已迁移 |
| `app/services/tools/*` | Tool implementations + providers | 已承接首批本地领域工具、ReportTool、权限闸口和执行审计 |
| `app/services/cockpit/*` | ReportTool + Owner cockpit | 保留，接入 Tool Router |
| `app/services/ai/*` | Agent support + Data processing | 保留，按调用方向收敛 |
| `app/services/llm/*` | LLM provider | 保留 |
| `app/services/v5_*` | Data Layer + Sync Engine + Admin | 拆分到稳定边界 |
| `app/services/access_control.py` | Data access policy | 保留，Phase 1 强化 |
| `app/services/permissions.py` | Agent/domain policy | 保留，后续归并 |
| `app/services/resource_registry.py` | Data Layer resource registry | 保留 |
| `app/services/resource_sources.py` | Data Layer source identity | 保留，Phase 1 强化 |
| `app/services/work_events.py` | Data Layer event ingestion | 保留 |
| `app/services/work_events_admin.py` | Data Layer 工作事件调试/维护 API payload | 已从 `work_events.py` 拆出 |
| `app/api/routes/work_events.py` | Data Layer 工作事件调试/维护子路由聚合器 | 只保留 prefix/依赖/include |
| `app/api/routes/work_events_collection_routes.py` | WorkEvent collection 子路由聚合器 | 已从 `work_events.py` 拆出并继续拆为 create/list/detail 子路由 |
| `app/api/routes/work_events_processing_routes.py` | WorkEvent processing 子路由聚合器 | 已从 `work_events.py` 拆出并继续拆为 extract/vectorize 子路由 |
| `app/api/routes/work_events_analysis_routes.py` | WorkEvent analysis 子路由聚合器 | 已从 `work_events.py` 拆出并继续拆为 daily-report/vector-search/vector-pending 子路由 |
| `app/api/routes/work_events_request_models.py` | WorkEvent 管理 HTTP 请求模型 | 已从 `work_events.py` 拆出 |
| `app/services/companies_admin.py` | 公司、账号和 quick company setup 管理 payload | 已从 `companies.py` 拆出 |
| `app/services/mail_admin.py` | 外部邮箱同步和 OAuth 管理 payload | 已从 `mail.py` 拆出 |
| `app/api/routes/operations.py` | Operations 子路由聚合器 | 只保留 prefix/依赖/include |
| `app/api/routes/operations_*_routes.py` | Operations 薄 HTTP 入口 | 已从旧 `operations.py` 拆出，不定义请求模型或业务逻辑；dashboard、advisor、status 已拆为独立 route 模块 |
| `app/services/operations_resources.py` | Operations 飞书资源管理与 legacy retirement 统计 | 已从旧路由拆出 |
| `app/services/operations_memory.py` | Operations MemoryFact 创建、列表和生成任务入口 | 已从旧路由拆出 |
| `app/services/operations_bot_users.py` | Operations Bot 用户、权限规则和权限重算 | 已从旧路由拆出 |
| `app/services/operations_read_models.py` | Operations 同步记录、提取项、报表、审计日志列表 payload | 已从旧路由拆出 |
| `app/services/operations_status.py` | Operations 自动化状态、系统健康、默认公司和数据计数 | 已从旧路由拆出 |
| `app/services/operations_dashboard.py` | Operations dashboard overview 聚合 | 已从旧路由拆出 |
| `app/services/operations_advisor.py` | Operations advisor chat company 校验和回答入口 | 已从旧路由拆出 |
| `app/services/operations_entities.py` | Operations Person/Project/Customer 实体创建 | 已从旧路由拆出 |
| `app/api/routes/v5.py` | V5 admin API 子路由聚合器 | 只保留 prefix/依赖/include |
| `app/api/routes/v5_administration_routes.py` | V5 Administration 子路由聚合器 | 只保留子 router include |
| `app/api/routes/v5_administration_foundation_routes.py` | V5 foundation 子路由聚合器 | 已从 `v5_administration_routes.py` 拆出并继续拆为 bootstrap/os-overview 子路由 |
| `app/api/routes/v5_administration_bootstrap_routes.py` | V5 foundation bootstrap HTTP 入口 | 已从 `v5_administration_foundation_routes.py` 拆出 |
| `app/api/routes/v5_administration_os_overview_routes.py` | V5 OS overview HTTP 入口 | 已从 `v5_administration_foundation_routes.py` 拆出 |
| `app/api/routes/v5_administration_list_routes.py` | V5 Administration 列表子路由聚合器 | 已从 `v5_administration_routes.py` 拆出并继续拆为 user-setting/org/permission 子路由 |
| `app/api/routes/v5_administration_user_setting_routes.py` | V5 Administration 用户和公司设置子路由聚合器 | 已从 `v5_administration_list_routes.py` 拆出并继续拆为 users/company-settings 子路由 |
| `app/api/routes/v5_administration_user_routes.py` | V5 Administration 用户 HTTP 入口 | 已从 `v5_administration_user_setting_routes.py` 拆出 |
| `app/api/routes/v5_administration_company_setting_routes.py` | V5 Administration 公司设置 HTTP 入口 | 已从 `v5_administration_user_setting_routes.py` 拆出 |
| `app/api/routes/v5_administration_org_routes.py` | V5 Administration 组织子路由聚合器 | 已从 `v5_administration_list_routes.py` 拆出并继续拆为 departments/teams 子路由 |
| `app/api/routes/v5_administration_department_routes.py` | V5 Administration 部门 HTTP 入口 | 已从 `v5_administration_org_routes.py` 拆出 |
| `app/api/routes/v5_administration_team_routes.py` | V5 Administration 团队 HTTP 入口 | 已从 `v5_administration_org_routes.py` 拆出 |
| `app/api/routes/v5_administration_permission_routes.py` | V5 Administration 权限子路由聚合器 | 已从 `v5_administration_list_routes.py` 拆出并继续拆为 roles/permissions/resource-permissions 子路由 |
| `app/api/routes/v5_administration_role_routes.py` | V5 Administration 角色 HTTP 入口 | 已从 `v5_administration_permission_routes.py` 拆出 |
| `app/api/routes/v5_administration_permission_list_routes.py` | V5 Administration 权限 HTTP 入口 | 已从 `v5_administration_permission_routes.py` 拆出 |
| `app/api/routes/v5_administration_resource_permission_routes.py` | V5 Administration 资源权限 HTTP 入口 | 已从 `v5_administration_permission_routes.py` 拆出 |
| `app/api/routes/v5_administration_access_routes.py` | V5 Administration access preview HTTP 入口 | 已从 `v5_administration_routes.py` 拆出 |
| `app/api/routes/v5_administration_request_models.py` | V5 Administration HTTP 请求模型 | 已从 `v5_administration_routes.py` 拆出 |
| `app/api/routes/v5_agent_routes.py` | V5 Agent 管理子路由聚合器 | 已从 `v5.py` 拆出并继续拆为 trace/settings 子路由 |
| `app/api/routes/v5_agent_trace_routes.py` | V5 Agent trace 子路由聚合器 | 已从 `v5_agent_routes.py` 拆出并继续拆为 preview/list 子路由 |
| `app/api/routes/v5_agent_trace_preview_routes.py` | V5 Agent trace preview HTTP 入口 | 已从 `v5_agent_trace_routes.py` 拆出 |
| `app/api/routes/v5_agent_trace_list_routes.py` | V5 Agent trace 日志列表 HTTP 入口 | 已从 `v5_agent_trace_routes.py` 拆出 |
| `app/api/routes/v5_agent_settings_routes.py` | V5 Agent 设置子路由聚合器 | 已从 `v5_agent_routes.py` 拆出并继续拆为 read/write 子路由 |
| `app/api/routes/v5_agent_settings_read_routes.py` | V5 Agent 设置读取 HTTP 入口 | 已从 `v5_agent_settings_routes.py` 拆出 |
| `app/api/routes/v5_agent_settings_write_routes.py` | V5 Agent 设置保存 HTTP 入口 | 已从 `v5_agent_settings_routes.py` 拆出 |
| `app/api/routes/v5_intelligence_routes.py` | V5 Intelligence 子路由聚合器 | 已从 `v5.py` 拆出并继续拆为 risk-noise/low-signal/business-item 子路由 |
| `app/api/routes/v5_intelligence_risk_noise_routes.py` | V5 Intelligence 风险噪声关闭 HTTP 入口 | 已从 `v5_intelligence_routes.py` 拆出 |
| `app/api/routes/v5_intelligence_low_signal_routes.py` | V5 Intelligence 低信号通知关闭 HTTP 入口 | 已从 `v5_intelligence_routes.py` 拆出 |
| `app/api/routes/v5_intelligence_business_item_routes.py` | V5 Intelligence 开放业务事项富化 HTTP 入口 | 已从 `v5_intelligence_routes.py` 拆出 |
| `app/api/routes/v5_resource_routes.py` | V5 资源子路由聚合器 | 只保留子 router include |
| `app/api/routes/v5_resource_status_routes.py` | V5 资源状态子路由聚合器 | 已从 `v5_resource_routes.py` 拆出并继续拆为 overview/sync-status/workspace-events 子路由 |
| `app/api/routes/v5_resource_overview_routes.py` | V5 资源 overview 子路由聚合器 | 已从 `v5_resource_status_routes.py` 拆出并继续拆为 list/company-overview/sync-strategy 子路由 |
| `app/api/routes/v5_resource_list_routes.py` | V5 资源列表 HTTP 入口 | 已从 `v5_resource_overview_routes.py` 拆出 |
| `app/api/routes/v5_resource_company_overview_routes.py` | V5 资源公司概览 HTTP 入口 | 已从 `v5_resource_overview_routes.py` 拆出 |
| `app/api/routes/v5_resource_sync_strategy_routes.py` | V5 资源同步策略概览 HTTP 入口 | 已从 `v5_resource_overview_routes.py` 拆出 |
| `app/api/routes/v5_resource_sync_status_routes.py` | V5 资源同步状态子路由聚合器 | 已从 `v5_resource_status_routes.py` 拆出并继续拆为 sync-status/sync-runs/monitoring 子路由 |
| `app/api/routes/v5_resource_sync_status_list_routes.py` | V5 资源同步状态 HTTP 入口 | 已从 `v5_resource_sync_status_routes.py` 拆出 |
| `app/api/routes/v5_resource_sync_run_routes.py` | V5 资源同步记录 HTTP 入口 | 已从 `v5_resource_sync_status_routes.py` 拆出 |
| `app/api/routes/v5_resource_monitoring_routes.py` | V5 资源监控 HTTP 入口 | 已从 `v5_resource_sync_status_routes.py` 拆出 |
| `app/api/routes/v5_resource_policy_routes.py` | V5 资源同步策略子路由聚合器 | 已从 `v5_resource_routes.py` 拆出并继续拆为 read/write 子路由 |
| `app/api/routes/v5_resource_policy_read_routes.py` | V5 资源同步策略读取 HTTP 入口 | 已从 `v5_resource_policy_routes.py` 拆出 |
| `app/api/routes/v5_resource_policy_write_routes.py` | V5 资源同步策略保存 HTTP 入口 | 已从 `v5_resource_policy_routes.py` 拆出 |
| `app/api/routes/v5_resource_sync_routes.py` | V5 资源批量同步子路由聚合器 | 已从 `v5_resource_routes.py` 拆出并继续拆为 batch-preview/batch-execute 子路由 |
| `app/api/routes/v5_resource_workspace_routes.py` | V5 workspace 子路由聚合器 | 已从 `v5_resource_routes.py` 拆出并继续拆为 single-sync/access 子路由 |
| `app/api/routes/v5_resource_access_routes.py` | V5 资源访问动作子路由聚合器 | 已从 `v5_resource_workspace_routes.py` 拆出并继续拆为 clear/access-decision 子路由 |
| `app/api/routes/v5_resource_clear_access_block_routes.py` | V5 资源 access block 清理 HTTP 入口 | 已从 `v5_resource_access_routes.py` 拆出 |
| `app/api/routes/v5_resource_access_decision_routes.py` | V5 资源访问决策 HTTP 入口 | 已从 `v5_resource_access_routes.py` 拆出 |
| `app/api/routes/v5_resource_request_models.py` | V5 资源 HTTP 请求模型 | 已从 `v5_resource_routes.py` 拆出 |
| `app/api/routes/v5_system_log_routes.py` | V5 系统日志子路由聚合器 | 已从 `v5.py` 拆出并继续拆为 overview/list 子路由 |
| `app/api/routes/v5_tool_routes.py` | V5 工具后台子路由聚合器 | 只保留子 router include |
| `app/api/routes/v5_tool_list_routes.py` | V5 工具列表子路由聚合器 | 已从 `v5_tool_routes.py` 拆出并继续拆为 catalog/execution-log 子路由 |
| `app/api/routes/v5_tool_catalog_routes.py` | V5 工具列表 HTTP 入口 | 已从 `v5_tool_list_routes.py` 拆出 |
| `app/api/routes/v5_tool_execution_log_routes.py` | V5 工具执行日志 HTTP 入口 | 已从 `v5_tool_list_routes.py` 拆出 |
| `app/api/routes/v5_tool_execution_routes.py` | V5 后台工具执行 HTTP 入口 | 已从 `v5_tool_routes.py` 拆出 |
| `app/api/routes/v5_tool_config_routes.py` | V5 工具配置子路由聚合器 | 已从 `v5_tool_routes.py` 拆出并继续拆为 single-config/batch-config 子路由 |
| `app/api/routes/v5_tool_request_models.py` | V5 工具后台 HTTP 请求模型 | 已从 `v5_tool_routes.py` 拆出 |
| `app/services/v5_administration.py` | V5 foundation bootstrap + OS overview + Administration 列表和 access preview | 已承接后台 administration 查询 |
| `app/services/v5_tool_admin.py` | V5 工具后台管理、执行和审计 payload | 已从 `v5.py` 工具路由拆出 |
| `app/services/v5_agent_admin.py` | V5 Agent 设置、trace preview 和 trace 日志 payload | 已从 `v5.py` Agent 路由拆出 |
| `app/services/v5_system_logs.py` | V5 系统日志查询、过滤和 limit 边界 | 已从 `v5.py` 系统日志路由拆出 |
| `app/services/v5_resource_status.py` | V5 资源列表、资源概览、同步记录和 workspace events payload | 已从 `v5.py` 资源路由拆出 |
| `app/services/v5_auto_sync.py` | V5 批量资源同步预览和执行编排 | 已从 `v5.py` 资源路由拆出 |
| `app/services/v5_workspace.py` | V5 单资源同步、access block 和资源访问决策动作 | 已承接资源动作路由 |
| `app/services/v5_intelligence_admin.py` | V5 intelligence 后台清理、关闭和富化动作 | 已从 `v5.py` intelligence 路由拆出 |
| `app/services/v5_sync_policy.py` | V5 资源同步策略默认值、读写和 payload | 已承接策略 API payload |

## 4. 架构差距

### 4.1 Message Gateway 缺口

已完成：

- 标准消息模型。
- 标准 actor/context。
- 飞书事件到标准消息的适配层。
- 飞书 URL verification 处理。
- 命令文本清理函数。
- Gateway 消息安全摘要审计：`app/services/gateway/audit.py` 已将 Feishu GatewayMessage 的事件、会话、回复目标、状态和处理原因写入 `AuditLog`，不保存原始 payload 和消息正文。
- Gateway 卡片动作解析模型：`app/services/gateway/card_actions.py` 已提供 `GatewayCardAction`、`parse_gateway_card_action()`、`gateway_card_action_value()` 和 `gateway_card_action_message_id()`，统一处理 Feishu `event.action.value`、`event.action_info.value`、JSON string value、callback context/message/open_message_id 和 chat_id；`build_feishu_gateway_message()` 已能把 action_info 型回调识别为 `GatewayMessageKind.CARD_ACTION`，审批卡片仅保留业务 wrapper 并委托 Gateway 解析。
- Gateway 卡片 responder 分发模型：`app/services/gateway/card_responder.py` 已提供 `GatewayCardResponder`、`dispatch_gateway_card_action_message()` 和 `dispatch_gateway_card_action_response()`，按 card action `kind` 分发给业务 responder；审批卡片通过 `approval_card_entrypoint.feishu_approval_card_responder()` 注册为 `approval_action`，普通消息回调和 WebSocket callback response 都先走 Gateway 分发，再进入审批业务 responder。后续非审批卡片只需要新增业务 responder 注册项，不需要复制 Gateway payload 解析和入口判断。

仍缺少：

- 非审批类互动卡片的具体业务 responder。

当前风险：

- 普通文本、互动卡片发送和卡片内容更新已进入 Gateway Responder；Celery 后台任务中的待审批异步回复、异常回复和日报推送也已改走 `send_feishu_text_reply`，不再直连 `FeishuClient.send_message`；审批规则建议、文本建议、详细理由和附件摘要判断已拆入 `app/services/feishu/approval_advice.py`；审批互动卡片 payload、toast、回调 value/message_id 解析、默认卡片标题、建议和展开详情渲染已拆入 `app/services/feishu/approval_cards.py`，底层 card action value/message_id 解析已委托 Gateway card action 模型，`commands.py` 不再保留卡片 value/message_id 等兼容 wrapper；审批互动卡片 callback response、详情展开/收起和按钮动作回复已拆入 `app/services/feishu/approval_card_responder.py`，发送文本回复和更新卡片内容由 `approval_card_entrypoint.py` 注入，responder 保持平台中立，不直接依赖 `FeishuClient`、Gateway 发送实现、Tool Router、ToolRequest/ToolContext、confirmation token 或原生 API 写方法；`app/services/feishu/approval_card_entrypoint.py` 已统一装配 WebSocket callback response、消息事件中的卡片动作依赖、审批卡片构建、互动卡片发送、审批确认后的 Tool Router 提交和审批动作审计，并直接使用 `identity.py`、`command_parser.py`、`approval_formatters.py` 提供身份、权限拒绝、chat 解析和短文本 formatter，不再回连 `commands.py` 的私有 wrapper；Celery 待审批异步卡片也直接调用 entrypoint，`handle_feishu_command` 通过 Gateway card responder 分发后进入审批业务 responder，不再直接判断审批卡片或依赖 responder，也不直接持有 ToolRequest/ToolContext/confirmation token 生成逻辑；旧卡片动作兼容 wrapper 已删除，`commands.py` 不再保留 `_build_approval_action_card`/`_maybe_send_approval_action_card` 或直接调用 `send_feishu_interactive_reply`；普通文本回复的 `FeishuClient` 适配已移入 `app/services/feishu/replies.py`，`commands.py` 不再直接导入 `FeishuClient` 或 Gateway responder；审批动作准备、缺字段校验和待确认动作执行已拆入 `app/services/feishu/approval_actions.py`，并已为卡片/命令 pending action 增加内部同参 confirmation token 校验；审批项选择、待审批列表、详情行、附件读取结果、附件状态、审批命名、申请人/单号提取、字段优先级、表单摘要和字段显示值等纯 formatter 已拆入 `app/services/feishu/approval_formatters.py`；待审批资源注册、同步附件合并和历史相似审批匹配已拆入 `app/services/feishu/approval_resources.py`；审批上下文优先读取、live 待审批富化流水线、审批详情回复和审批建议回复编排已拆入 `app/services/feishu/approval_runtime.py`；审批上下文和待确认动作 Redis 存取已拆入 `app/services/feishu/approval_context.py`；邮箱、审批、通讯录同步命令和 quick sync 参数编排已拆入 `app/services/feishu/sync_commands.py`；飞书机器人消息 chat/sender 提取、群聊 @ 判断、命令文本解析、别名归一化和审批上下文意图判断已拆入 `app/services/feishu/command_parser.py`；normalized command 到具体回复能力的主分发规则已拆入 `app/services/feishu/command_dispatcher.py`；飞书通讯录管理人员查询、通讯录快照读取、组织架构 Markdown/XMind 大纲生成和组织架构回复编排已拆入 `app/services/feishu/organization.py`；机器人身份模型、发送者身份识别、管理员兜底、权限拒绝文案、身份回复和 Celery identity payload 序列化已拆入 `app/services/feishu/identity.py`；员工 Agent 问答调用、Bot 会话记录、route/scope 提取、驾驶舱回答生成与重写已拆入 `app/services/feishu/bot_runtime.py`；本地 WorkEvent/ExtractedItem 的审批历史兜底、近期邮件、开放待办、事件行格式化和安全错误摘要已拆入 `app/services/feishu/work_event_replies.py`；审批附件摘要补读、审批 LLM 建议批量附加和单条建议生成依赖编排已拆入 `app/services/feishu/approval_enrichment.py`；审批任务列表解析保留在 `approval.py::extract_approval_task_items`，实时待审批任务和实例详情富化已统一交给 Tool Router/MCP/CLI。
- 审批互动卡片入口只读取 `approval_context.load_approval_context` 中的卡片缓存，并通过 `approval_runtime.approval_detail_reply` 与 `approval_actions.prepare_approval_action_reply` 复用详情和动作准备能力；卡片缓存过期或缺失时不回退到 `commands.py` 或 live 待审批查询，用户需要重新发送“待审批”刷新上下文，避免 Gateway 卡片入口承担实时数据获取职责。
- `commands.py` 中已删除无运行路径依赖的旧审批兼容 wrapper，包括 approval context key、附件结果 formatter、审批状态读取和 task value 提取等纯转发壳；对应测试改为直接验证 `work_event_replies` 等真实归属模块。
- `commands.py` 中已继续删除无引用的 bot runtime、组织架构、同步命令、命令解析、身份回复、权限拒绝、员工问答、审批数据 helper、审批 formatter、审批 service-runtime、审批 advice 和审批 enrichment 兼容 wrapper；dispatch handlers 直接引用 `identity.py`、`organization.py`、`bot_runtime.py`、`sync_commands.py`、`work_event_replies.py` 等真实归属模块，审批金额、表单字段、附件引用、审批任务解析、待审批拉取、组织 Markdown、LLM 建议注入和 quick sync 行为测试也改为直接验证对应真实模块，避免旧 Gateway 文件继续作为业务 helper 集散地。
- 最近审批/待我审批回复编排已迁入 `approval_runtime.recent_approvals_reply()`，包括实时待办、附件/历史/LLM 富化、完成审批提示、本地 pending 兜底和最近历史兜底；`approval_card_entrypoint.py` 负责装配 Feishu 实时审批依赖并提供 `recent_feishu_approvals_reply()`、`feishu_approval_items_from_context_or_live()`、文本审批建议、文本审批详情、通过/拒绝准备入口、待确认审批执行入口、pending action 清理入口和审批上下文读写入口，Celery 待审批异步任务和命令层文本审批入口均直接使用 `identity.py` 和 approval card entrypoint，不再导入或重复装配 pending fetch/enrich/store 私有审批 wrapper。`commands.py` 已删除旧的 pending fetch、附件补读、资源登记、历史上下文、异步 LLM 建议、审批事件格式化、context-or-live、pending action 存取、审批上下文清理、身份回复和审批数据 helper 兼容壳，并把普通文本回复 `FeishuClient` 适配移入 `feishu/replies.py`；命令 handler 装配、日报事务适配、审批文本命令桥接、审批上下文读写和附件结果读取已迁入 `command_handlers.py`，当前 `commands.py` 降至 110 行；`tests/test_v5_architecture.py` 已锁定该编排不得回流到命令入口，并锁定 Celery 不得回连 `commands.py`。
- 后续增加网页机器人或 iOS App 时会复制逻辑。

### 4.2 Agent Runtime 缺口

已完成：

- Agent Runtime 主入口。
- Runtime 到 Tool Router 的首批工具调用路径。
- `answer_agent_message_with_trace` 已提供语义、路由、工具/Advisor/拒绝路径的 runtime 执行轨迹，同时保留原 `answer_agent_message` 字符串接口。
- `/api/v5/agent/trace-preview` 已提供后台可调用的 Agent trace 预览接口，并写入 `agent.trace.preview` 审计日志；审计 payload 已记录 Planner 开关、最大规划步数、写工具启停、写确认策略、`requires_dry_run` 和确认要求；`/api/v5/agent/traces` 已提供最近 Agent trace 历史列表，并在后台历史表显示写操作、dry-run、写策略和确认要求；AI 助理弹窗已显示回答、当前执行轨迹和历史轨迹表。
- `app/services/agent/planner.py` 已建立第一阶段 Planner 边界；trace-preview 会读取公司 Agent 设置，开启后在 trace 中展示 guardrail、工具/Advisor、最终回答等计划步骤，并标注写工具禁用、要求确认或允许执行的策略状态；写工具计划步骤会输出 `requires_dry_run` 和 `confirmed_execution_requires=["dry_run=true","confirmed=true","confirmation_token"]`。Agent Runtime 已开始消费 Planner 的声明式 `tool` step，并通过既有 Tool Router 顺序执行计划内工具；多工具回答会按能力标题做保守合成；计划中遇到写工具确认门禁、工具拒绝或工具错误时会停止后续工具，并在 trace 写入 `plan_stop_reason`；测试已覆盖多个只读工具按计划执行、写工具停在原有 dry-run/confirmation_token 门禁，以及工具 error 后停止后续计划。
- Agent Runtime 已把公司级 `allow_write_tools` 和 `require_write_confirmation` 变成写工具执行前硬门禁：禁写时直接拒绝；要求确认时停在 dry-run/confirmation_token 指引，不调用真实写工具；运行 trace 会以机器可读字段记录 `requires_dry_run` 和真实执行所需确认条件；真实飞书机器人入口 `employee_bot_answer` 与后台 trace-preview 已共用同一公司级 Agent 设置。

仍缺少：

- 自动重试、分支计划和更复杂的跨工具结果合成。

当前风险：

- Planner 目前已能驱动 Runtime 执行计划内工具步骤，并具备失败停止策略；结果合成仍是按能力标题分段的保守合成，复杂任务后续仍需要自动重试、分支策略和更强的跨工具综合回答。
- 写工具直接执行只能在公司策略显式允许且工具层确认流程满足时发生；后续计划执行增强必须沿用同一门禁，不能新增旁路。

### 4.3 Tool Router 缺口

已完成：

- 工具协议：`ToolRequest`、`ToolResult`、`ToolContext`、`ToolProvider`、`ToolDefinition`、`ToolExecutionStatus`。
- 工具注册表：声明 provider、required_permissions、supports_write、audit_action、enabled。
- 工具路由：Approval、Bitable、Chat、Company、Domain、Knowledge、Personal、Calendar、Mail、Task、OKR、Contact、General conversation 本地能力。
- Provider 分派：local、report、feishu_api、feishu_mcp、devops。
- Feishu 实时读绑定：Calendar、Task、Mail、Bitable、Approval instance/task、OKR cycle/objective、Contact scope/department/user/snapshot 等实时读入口默认由 Feishu MCP provider 调度 CLI；API runtime 只保留给注入 `client` 的同步层/受控测试路径。Contact 授权范围、子部门、部门直属用户和组织快照按 `lark-contact` skill、`lark-openapi-explorer`、飞书官方“获取通讯录授权范围/获取子部门列表/获取部门直属用户列表”文档和 `lark-cli api GET ... --dry-run --as bot` 由 MCP provider 调度 `lark-cli api GET` 执行，仍仅开放只读，走 `contact:read` 权限，根部门查询仍受飞书通讯录权限范围限制；Bitable 字段结构读取已按 `lark-cli base +field-list --dry-run` 接入 `GET /open-apis/base/v3/bases/:base_token/tables/:table_id/fields`，作为记录写入前确认真实字段和字段类型的基础工具。
- 审批已发起实例列表已按 `lark-approval` skill、`lark-cli schema approval.instances.initiated --format json` 和 `lark-cli approval instances initiated --params '{"definition_code":"approval_test","page_size":20,"user_id_type":"open_id"}' --dry-run --as user --format json` 验证；`feishu_approval_instance_initiated` 作为只读业务工具接入 Tool Router 和 Feishu MCP provider，由 MCP 调度 `lark-cli approval instances initiated --as user --format json --params ...` 查询，不新增 API provider 业务查询面，走 `approval:read` 权限。
- 飞书云空间实时搜索已按 `lark-drive` 和 `lark-cli drive +search --help` 接入 `feishu_drive_search`，作为 L2 冷知识和云空间对象定位入口走 `Tool Router -> Feishu MCP -> lark-cli drive +search --as user --format json --query ...`；仅使用 shortcut 暴露的扁平 flag，包括 `doc_types`、`page_size`、`sort`、时间窗口、owner/mine、folder/wiki space 限定和只搜标题/评论，不手写嵌套 API JSON。该工具只返回实时搜索结果摘要，不入库、不替代 Sync Engine，也不把 L2 冷知识自动转成本地知识库。
- 飞书文档实时读取已按 `lark-doc`、`lark-drive`、`lark-shared` 与 `lark-doc-fetch` 规则接入 `feishu_doc_fetch`，作为 Agent 实时知识查询工具走 `Tool Router -> Feishu MCP -> lark-cli docs +fetch --api-version v2 --as user --format json --doc ...`；仅允许已验证的 `doc_format=xml|markdown|text`、`detail=simple|with-ids|full`、`scope=outline|range|keyword|section` 和局部读取参数，走 `knowledge:read` 权限，不入库、不替代 Sync Engine 的 L1 热知识正文同步。管理后台 dry-run 参数模板已展示 `doc/doc_format/detail/scope/keyword`，Provider boundary 会把该工具标为已绑定 MCP CLI 工具而不是 API 查询。
- 日程实时读取已按 `lark-calendar` 与 `lark-cli calendar +agenda --help` 接入 `calendar_qa` 的 MCP/CLI 路径：无测试 `client` 的实时读取由 Feishu MCP provider 调用 `lark-cli calendar +agenda --as user --format json --calendar-id --start --end`；API runtime 的日程读取只保留给注入 client 的同步层/受控测试路径，不作为 Agent 实时问答默认链路。
- 任务实时读取已按 `lark-task`、`lark-shared` 与 `lark-cli task +get-my-tasks --help` 接入 `task_qa` 的 MCP/CLI 路径：无测试 `client` 的实时读取由 Feishu MCP provider 调用 `lark-cli task +get-my-tasks --as user --format json`；系统内只追加 CLI help 已验证的关键词、创建时间、截止时间、分页和完成任务 flag，API runtime 的任务列表读取只保留给注入 client 的同步层/受控测试路径。
- 邮箱实时读取已按 `lark-mail`、`lark-shared` 与 `lark-cli mail +triage --help` 接入 `mail_qa` 的 MCP/CLI 路径：无测试 `client` 的实时读取由 Feishu MCP provider 调用 `lark-cli mail +triage --as user --format json --mailbox`；系统内只追加 CLI help 已验证的关键词、filter、分页和标签 flag，API runtime 的邮件列表读取只保留给注入 client 的同步层/受控测试路径，邮件正文/主题等返回内容继续视为不可信外部数据。
- 多维表格实时读取已按 `lark-base`、`lark-shared`、`lark-cli base +table-list --help` 与 `lark-cli base +record-list --help` 接入 `bitable_qa` 的 MCP/CLI 路径：无测试 `client` 且无 `table_id` 时调用 `lark-cli base +table-list --as user --format json --base-token`，有 `table_id` 时调用 `lark-cli base +record-list --as user --format json --base-token --table-id`；系统内只追加 CLI help 已验证的视图、字段投影、filter/sort JSON、offset 和 limit，API runtime 的表/记录读取只保留给注入 client 的同步层/受控测试路径。
- 多维表格字段和视图结构实时读取已按 `lark-base`、`lark-cli base +field-list --help`、`lark-cli base +view-get-visible-fields/+view-get-card/+view-get-timebar --help` 接入 `feishu_bitable_field_list` 与 `feishu_bitable_view_get_visible_fields/card/timebar` 的 MCP/CLI 路径；无测试 `client` 时由 Feishu MCP provider 调度 CLI 读取结构，API runtime 的对应读取只保留给注入 client 的同步层/受控测试路径。
- 审批任务查询和实例详情实时读取已按 `lark-approval`、`lark-shared`、`lark-cli approval tasks query/instances get --help` 和 `lark-cli schema approval.tasks.query/approval.instances.get --format json` 接入 `feishu_approval_task_query` 与 `feishu_approval_instance_get` 的 MCP/CLI 路径：无测试 `client` 时分别调用 `lark-cli approval tasks query --as user --format json --params ...` 和 `lark-cli approval instances get --as user --format json --params ...`；API runtime 的审批任务/实例读取只保留给注入 client 的同步层/受控测试路径。
- OKR 周期和目标实时读取已按 `lark-okr`、`lark-shared` 与 `lark-cli okr +cycle-list/+cycle-detail --help` 接入 `feishu_okr_cycle_list` 与 `feishu_okr_objective_list` 的 MCP/CLI 路径：无测试 `client` 时分别调用 `lark-cli okr +cycle-list --as user --format json --user-id --user-id-type` 和 `lark-cli okr +cycle-detail --as user --format json --cycle-id`；API runtime 的 OKR 周期/目标读取只保留给注入 client 的同步层/受控测试路径。
- Contact 通讯录实时读取已按 `lark-contact`、`lark-openapi-explorer`、`lark-shared` 与 `lark-cli api GET ... --dry-run --as bot` 接入 `feishu_contact_scope_list`、`feishu_contact_department_children`、`feishu_contact_department_users` 和 `feishu_contact_organization_snapshot` 的 MCP/CLI 路径：无测试 `client` 时由 Feishu MCP provider 调度 `lark-cli api GET` 读取授权范围、子部门、部门直属用户和受限组织快照；API runtime 的 Contact 读取只保留给注入 client 的同步层/受控测试路径。
- Feishu API runtime 受控绑定（同步/测试形态，不作为实时 provider）：审批 approve/reject/transfer/remind/add_sign/rollback/cancel/cc 已通过 `app/services/feishu/api_runtime.py` 调用 `FeishuApprovalService.execute_task_action`、`execute_task_transfer`、`execute_instance_remind`、`execute_task_add_sign`、`execute_task_rollback`、`execute_instance_cancel` 与 `execute_instance_cc`；旧管理接口 `/api/feishu/apps/{app_config_id}/approvals/action` 已保留入口但改走 Tool Router，默认 dry-run，真实提交必须带 `confirmed=true + confirmation_token`；机器人确认审批真实提交也已改走 Tool Router 的 confirmed 路径，在内部 pending action token 校验后再生成工具层同参 `confirmation_token`，旧 `approval_actions.execute_approval_action` 直连 service 函数已删除；IM 文本消息发送已通过 `FeishuImService.send_text_message` 调用 `POST /open-apis/im/v1/messages`，飞书建群已通过 `FeishuImService.create_chat` 调用 `POST /open-apis/im/v1/chats`，公开群自动加入已通过 `FeishuImService.auto_join_public_chats` 调用 `POST /open-apis/im/v1/chats/:chat_id/members/me_join`；建群已用 `lark-cli im +chat-create --dry-run` 和 `lark-cli api POST /open-apis/im/v1/chats --dry-run` 验证，公开群加入已用 `lark-cli api POST ...members/me_join --dry-run` 验证。旧管理接口 `/api/feishu/apps/{app_config_id}/bot/send` 已收敛为文本消息工具入口并默认 dry-run，真实发送必须走 `feishu_im_send_message` 的确认令牌边界；旧 `/api/feishu/apps/{app_config_id}/chats/create` 真实执行已改走 `feishu_im_create_chat`；旧公开群自动加入入口真实执行已改走 `feishu_im_auto_join_public_chats`，dry-run 仍返回候选群和确认令牌；日程创建已通过 `FeishuCalendarService.create_event` 调用 `POST /open-apis/calendar/v4/calendars/:calendar_id/events`，并在添加参会人失败时回滚删除已创建日程；任务创建已通过 `FeishuTaskService.create_task` 调用 `POST /open-apis/task/v2/tasks`；任务更新、完成和重新打开已通过 `FeishuTaskService.update_task`/`complete_task`/`reopen_task` 调用 `PATCH /open-apis/task/v2/tasks/:task_guid`，任务负责人分配已按飞书官方任务成员能力索引和 `lark-cli task +assign --dry-run` 验证后，通过 `POST /open-apis/task/v2/tasks/:task_guid/add_members|remove_members` 接入 assignee add/remove，任务关注人维护已按飞书官方任务成员能力索引和 `lark-cli task +followers --dry-run` 验证后，通过同一成员接口接入 follower add/remove，重新打开已用 `lark-cli task +reopen --dry-run` 验证；任务评论已通过 `FeishuTaskService.add_comment` 调用 `POST /open-apis/task/v2/comments`；多维表格记录创建/更新/删除已通过 `FeishuBitableService` 调用 `POST/PUT/DELETE /open-apis/bitable/v1/apps/:app_token/tables/:table_id/records`，批量创建记录已按飞书官方“新增多条记录”能力和 `lark-cli base +record-batch-create --dry-run` 验证后，通过 CLI 已验证的 `POST /open-apis/base/v3/bases/:base_token/tables/:table_id/records/batch_create` 与 `fields + rows` 形态接入，批量更新记录已按飞书官方“更新多条记录”能力和 `lark-cli base +record-batch-update --dry-run` 验证后，通过 CLI 已验证的 `POST /open-apis/base/v3/bases/:base_token/tables/:table_id/records/batch_update` 与 `record_id_list + patch` 形态接入，删除记录已用飞书官方文档和 `lark-cli api DELETE ... --dry-run` 验证；多维表格建表和建字段已按 `lark-cli base +table-create --dry-run`、`lark-cli base +field-create --dry-run` 与 `lark-base` skill 边界接入 `POST /open-apis/base/v3/bases/:base_token/tables` 和 `POST /open-apis/base/v3/bases/:base_token/tables/:table_id/fields`，字段更新已按 `lark-cli base +field-update --help` 与 `lark-cli base +field-update --dry-run --as user` 接入 `PUT /open-apis/base/v3/bases/:base_token/tables/:table_id/fields/:field_id`；视图创建、重命名、删除、筛选设置、排序设置、分组设置、可见字段、卡片和时间轴配置已按 `lark-cli base +view-create/+view-rename/+view-delete/+view-set-filter/+view-set-sort/+view-set-group/+view-get-visible-fields/+view-set-visible-fields/+view-get-card/+view-set-card/+view-get-timebar/+view-set-timebar --help` 与 `--dry-run --as user` 接入 `POST /open-apis/base/v3/bases/:base_token/tables/:table_id/views`、`PATCH /open-apis/base/v3/bases/:base_token/tables/:table_id/views/:view_id`、`DELETE /open-apis/base/v3/bases/:base_token/tables/:table_id/views/:view_id`、`PUT /open-apis/base/v3/bases/:base_token/tables/:table_id/views/:view_id/filter|sort|group|visible_fields|card|timebar` 和 `GET .../visible_fields|card|timebar`；字段更新按 full PUT 高风险语义要求提交完整 `field.name + field.type`，暂不开放 formula/lookup 更新，不接入未验证的角色权限复杂能力；视图能力仅开放 `grid/kanban/gallery/calendar/gantt` 创建、名称修改、显式 view_id 删除、`logic + conditions` 筛选设置、最多 10 项 `sort_config` 排序设置、最多 3 项 `group_config` 分组设置、最多 200 个 `visible_fields` 可见字段顺序配置、`cover_field` 卡片封面字段和 `start_time/end_time/title` 时间轴字段配置；多维表格记录写入已支持 `validate_fields=true` 前置校验，执行前读取真实字段列表，拦截不存在字段、公式/lookup/附件/系统字段等明显只读字段，后台模板默认展示该开关；未经 `dry_run` 或 `confirmed=true + confirmation_token` 时仍拒绝执行，且参数变更会导致 token 不匹配；dry-run 文案已显示写目标摘要，并且任务/日程/多维表格建表会展示标题，便于 Agent/后台确认 Bitable、Task、Calendar、Approval、IM 将写入的对象和字段。`app/services/feishu/api_runtime.py` 已暴露显式读/写绑定表，`tests/test_feishu_provider_boundary.py` 已枚举所有 Feishu API 写能力验证该闸口，并锁定 Feishu API capability、Tool Registry 和 API runtime 读/写绑定表的一致性；`tests/test_tool_router.py` 已覆盖普通成员对 Approval、Bitable、Task 等写权限的拒绝。
- Bitable 批量删除记录已按 `lark-cli base +record-delete --dry-run`、`lark-base` skill 和飞书官方“删除多条记录”文档验证；runtime 调用 `POST /open-apis/base/v3/bases/:base_token/tables/:table_id/records/batch_delete` 并只接受 `record_id_list`，测试已覆盖 confirmed 执行路径、CLI command 声明、普通成员权限拒绝和 `write_target.record_count/record_ids` 审计摘要。
- Bitable `record-upsert` 已按 `lark-cli base +record-upsert --help`、`lark-cli base +record-upsert --dry-run --as user` 和 `lark-base` skill 验证；runtime 保持 CLI 语义：无 `record_id` 时调用 `POST /open-apis/base/v3/bases/:base_token/tables/:table_id/records` 创建记录，有 `record_id` 时调用 `PATCH /open-apis/base/v3/bases/:base_token/tables/:table_id/records/:record_id` 更新该记录，不做业务键自动去重；测试已覆盖 create/update 两条 confirmed 路径、CLI command 声明、普通成员权限拒绝和 `write_target.upsert_mode` 审计摘要。
- Bitable 记录附件上传已按 `lark-base`、`lark-shared`、`lark-cli base +record-upload-attachment --help` 和 `--dry-run --as user` 验证；`feishu_bitable_record_upload_attachment` 已接入 Tool Router、Feishu API capability、Feishu MCP 实时桥、API runtime 受控测试路径、写目标审计和管理后台 dry-run 模板。无测试 `client` 的 confirmed 实时执行会通过 MCP 调用 `lark-cli base +record-upload-attachment --as user --format json --base-token ... --table-id ... --record-id ... --field-id ... --file ...`，真实 multipart 上传与追加附件 token 编排由 CLI 承担；API runtime 受控路径只验证 `POST /open-apis/base/v3/bases/:base_token/tables/:table_id/append_attachments` 的 append 形态，不承担实时本地文件上传。
- Bitable 记录附件移除已按 `lark-base`、`lark-shared`、`lark-cli base +record-remove-attachment --help` 和 `--dry-run --as user` 验证；`feishu_bitable_record_remove_attachment` 已接入 Tool Router、Feishu API capability、Feishu MCP 实时桥、API runtime 受控测试路径、写目标审计和管理后台 dry-run 模板。无测试 `client` 的 confirmed 实时执行会通过 MCP 调用 `lark-cli base +record-remove-attachment --as user --format json --base-token ... --table-id ... --record-id ... --field-id ... --file-token ... --yes`，`--yes` 只在 Tool Router 完成 `confirmed=true + confirmation_token` 校验后出现；API runtime 受控路径只验证 `POST /open-apis/base/v3/bases/:base_token/tables/:table_id/remove_attachments` 的请求形态。
- Bitable 表重命名已按 `lark-cli base +table-update --dry-run --as user` 和 `lark-base` skill 验证；runtime 调用 `PATCH /open-apis/base/v3/bases/:base_token/tables/:table_id` 并只提交 `name`，测试已覆盖 confirmed 执行路径、CLI command 声明、普通成员权限拒绝和写目标审计摘要。
- Bitable 表删除已按 `lark-cli base +table-delete --help`、`lark-cli base +table-delete --base-token app_test --table-id tbl_test --dry-run --as user --format json` 和 `lark-base` skill 验证；runtime 调用 `DELETE /open-apis/base/v3/bases/:base_token/tables/:table_id`，无测试 `client` 的 confirmed 实时执行会通过 MCP 调用 `lark-cli base +table-delete --as user --format json --base-token ... --table-id ... --yes`，`--yes` 只在 Tool Router 完成 confirmed + confirmation_token 校验后出现；测试已覆盖 API runtime 受控测试路径、MCP CLI argv、普通成员权限拒绝、写目标审计摘要和管理后台 dry-run 参数模板。
- Bitable 字段删除已按 `lark-cli base +field-delete --help`、`lark-cli base +field-delete --base-token app_test --table-id tbl_test --field-id fld_test --dry-run --as user --format json` 和 `lark-base` skill 验证；runtime 调用 `DELETE /open-apis/base/v3/bases/:base_token/tables/:table_id/fields/:field_id`，无测试 `client` 的 confirmed 实时执行会通过 MCP 调用 `lark-cli base +field-delete --as user --format json --base-token ... --table-id ... --field-id ... --yes`，`--yes` 只在 Tool Router 完成 confirmed + confirmation_token 校验后出现；测试已覆盖 API runtime 受控测试路径、MCP CLI argv、普通成员权限拒绝、写目标审计摘要和管理后台 dry-run 参数模板。
- Bitable 字段更新已按 `lark-cli base +field-update --help`、`lark-cli base +field-update --dry-run --as user` 和 `lark-base` skill 验证；runtime 调用 `PUT /open-apis/base/v3/bases/:base_token/tables/:table_id/fields/:field_id`，并要求完整 `field.name + field.type` 以匹配 full PUT 语义；测试已覆盖 confirmed 执行路径、formula/lookup 更新阻断、CLI command 声明、普通成员权限拒绝和字段写目标审计摘要。
- Bitable 视图创建、重命名和删除已按 `lark-cli base +view-create --help`、`lark-cli base +view-create --dry-run --as user`、`lark-cli base +view-rename --help`、`lark-cli base +view-rename --dry-run --as user`、`lark-cli base +view-delete --help`、`lark-cli base +view-delete --base-token app_test --table-id tbl_test --view-id viw_test --dry-run --as user --format json` 和 `lark-base` skill 验证；runtime 分别调用 `POST /open-apis/base/v3/bases/:base_token/tables/:table_id/views`、`PATCH /open-apis/base/v3/bases/:base_token/tables/:table_id/views/:view_id` 与 `DELETE /open-apis/base/v3/bases/:base_token/tables/:table_id/views/:view_id`，无测试 `client` 的 confirmed 视图删除会通过 MCP 调用 `lark-cli base +view-delete --as user --format json --base-token ... --table-id ... --view-id ... --yes`，`--yes` 只在 Tool Router 完成 confirmed + confirmation_token 校验后出现；测试已覆盖 confirmed 执行路径、unsupported view type 阻断、CLI command 声明、MCP CLI argv、普通成员权限拒绝、视图写目标审计摘要和管理后台 dry-run 参数模板。
- 所有 confirmed 飞书写工具在未注入测试 `client` 时都会先由 Tool Router 完成 dry-run/confirmation 闸口，再进入 Feishu MCP provider，严格保持 `Agent Runtime -> Tool Router -> Tool -> MCP -> CLI -> Feishu`；Feishu MCP provider 只负责调度已验证 CLI executor，不再 fallback 到 API runtime。全部 Feishu WRITE capability 已通过 `feishu_mcp_realtime_tool_names()` 暴露可测试的 MCP/CLI executor 注册表，并由 `tests/test_feishu_provider_boundary.py::test_all_feishu_write_capabilities_have_mcp_cli_executors` 锁定必须拥有 executor；尚未迁入 CLI executor 的实时写工具会明确阻断，直到补齐 CLI 执行器；显式测试 client 仍允许直接走 API runtime，仅用于单元测试和同步/API 受控路径。
- 全部 Feishu confirmed 实时写入已增加全量防回归测试：`tests/test_feishu_provider_boundary.py::test_all_feishu_confirmed_realtime_writes_delegate_to_mcp_without_client` 会枚举 `FEISHU_API_CAPABILITIES` 中所有 `risk=WRITE` 的能力，生成同参 confirmation token，并验证无 `client` 时全部委托 `feishu_mcp.execute_feishu_mcp_realtime_tool`，避免未来新增飞书写能力时绕回 API runtime 或直接 API 写入。
- Bitable confirmed 实时写已迁入 MCP 后的 `lark-cli base +...` executor：记录 create/update/upsert 使用 `+record-upsert`，批量创建/更新使用 `+record-batch-create/+record-batch-update`，删除使用高风险 `+record-delete --yes`，表使用 `+table-create/+table-update/+table-delete --yes`，字段使用 `+field-create/+field-update/+field-delete --yes`，视图使用 `+view-create/+view-rename/+view-delete --yes`，视图配置使用 `+view-set-filter/+view-set-sort/+view-set-group/+view-set-visible-fields/+view-set-card/+view-set-timebar`；`validate_fields=true` 时会先由 MCP 调用 `+field-list` 校验真实字段并阻断未知字段、附件、formula、lookup 和系统只读字段。已用 `lark-cli base +record-upsert --dry-run --as user` 验证代表性 CLI 请求形态，测试已覆盖字段校验、批量删除 `--yes`、表删除 `--yes`、字段删除 `--yes`、视图删除 `--yes`、字段更新 `--yes`、非法视图类型阻断和原 API runtime 受控测试路径。
- IM 文本消息发送已按 `lark-im` skill、`lark-shared` 认证规则、`lark-cli im +messages-send --help` 和 `lark-cli im +messages-send --chat-id oc_test --text ... --dry-run --as bot` 迁入 MCP 实时桥；MCP 内用参数数组调用 `lark-cli im +messages-send --as bot|user --format json --chat-id/--user-id --text --idempotency-key`，解析 JSON 返回中的 `message_id`。测试已覆盖 MCP 内 CLI argv、返回文案、dry-run 提示和原 API runtime 受控测试路径。
- IM 建群和公开群加入已按 `lark-im`、`lark-shared`、`lark-cli im +chat-create --help`、`lark-cli im +chat-create --dry-run --as bot --format json` 和 `lark-cli api POST /open-apis/im/v1/chats/:chat_id/members/me_join --dry-run --as user --format json` 迁入 MCP 实时桥；MCP 内用参数数组调用 `lark-cli im +chat-create --as bot|user --format json --name ...` 和 `lark-cli api POST ...members/me_join --params '{"chat_id":...}' --data '{}'`。公开群加入 confirmed 路径只接受明确 `chat_ids`，候选搜索仍由 dry-run/预览能力完成，MCP 不根据 query 自行选择加入目标；测试已覆盖 MCP 内 CLI argv、返回文案和原 API runtime 受控测试路径。
- Calendar 日程创建已按 `lark-calendar`、`lark-shared`、`lark-cli calendar +create --help` 和 `lark-cli calendar +create --dry-run --as user --format json` 迁入 MCP 实时桥；MCP 内用参数数组调用 `lark-cli calendar +create --as user --format json --calendar-id ... --summary ... --start ... --end ...`，并仅追加已验证 shortcut 支持的 `--description`、`--attendee-ids`、`--rrule`。该 executor 只承接 Tool Router 已完成 dry-run/confirmation 的明确日程参数，不在 MCP 层做模糊时间、会议室推荐或业务决策；测试已覆盖 MCP 内 CLI argv、返回文案和原 API runtime 受控测试路径。
- Task 创建、更新、完成、重新打开、评论、附件上传、子任务创建、父子任务关系、清单创建、清单名称更新、加入清单、清单成员增删和全量替换已按 `lark-task` skill、`lark-shared` 认证规则、对应 `lark-cli task +... --help`、`lark-cli schema task.subtasks.create`、`lark-cli schema task.tasklists.patch --format json` 和 `--dry-run --as user` 迁入 MCP 实时桥；`feishu_task_create/update/complete/reopen/comment/upload_attachment/subtask_create/add_to_tasklist/set_ancestor/clear_ancestor/tasklist_create/tasklist_update/tasklist_update_members/tasklist_set_members` 在无测试 `client` 时由 Feishu MCP provider 调用对应 CLI shortcut 或 schema 命令。附件上传使用 `lark-cli task +upload-attachment --resource-id ... --resource-type task|task_delivery --file ...`，真实 multipart 执行由 CLI 承担，API runtime 只保留显式测试 client 受控路径。子任务创建按 schema 使用 `lark-cli task subtasks create --params '{"task_guid":...}' --data ...`，清单名称更新按 schema 使用 `lark-cli task tasklists patch --params '{"tasklist_guid":...,"user_id_type":"open_id"}' --data '{"tasklist":{"name":...},"update_fields":["name"]}'`，清空父任务使用 `+set-ancestor` 且不传 `--ancestor-id`，清单成员全量替换使用 `+tasklist-members --set` 并由 CLI 读取当前成员后差异更新。测试已覆盖 MCP 内 CLI argv、schema 参数形态、`--data` payload、返回文案和原 API runtime 受控测试路径。
- 审批 approve/reject/transfer/remind/add_sign/rollback/cancel/cc 写路径已按 `lark-approval`、`lark-shared`、`lark-cli approval tasks approve/reject/transfer/remind/add_sign/rollback --help`、`lark-cli approval instances cancel/cc --help` 与对应 schema 复核：同意/拒绝为 `/open-apis/approval/v4/tasks/pass|refuse`，转交为 `/open-apis/approval/v4/tasks/forward`，催办为 `/open-apis/approval/v4/instances/remind`，加签为 `/open-apis/approval/v4/tasks/add_sign`，退回为 `/open-apis/approval/v4/tasks/rollback`，审批实例撤回为 `/open-apis/approval/v4/instances/recall`，审批实例抄送为 `/open-apis/approval/v4/instances/add_cc`；所有审批实时 confirmed 写入均走 MCP 后 `lark-cli approval ... --as user --data ... --yes`，需要 user_id_type 的 transfer/add_sign/cc 使用 `--params '{"user_id_type":"open_id"}'`。已用本机 `--dry-run --as user --yes` 验证 approve、transfer、remind、cc 代表性请求形态；测试已覆盖 approve form JSON、transfer params、remind task_ids、add_sign 参数校验和原 API runtime 受控测试路径；写目标摘要已补齐审批表单 `form_count/form_fields`、评论存在性、加签类型和审批方式等结构化审计字段，只记录字段标识和计数，不保存表单值、评论正文或 confirmation_token。显式测试 client/API runtime 受控路径仍支持通过个人飞书 `user_access_token` 提交，缺 token 时阻断，用于测试和非实时受控场景。
- Task 清单、清单成员、父子任务、子任务创建、附件上传和提醒更新写能力已接入 Tool Router 与 API runtime 受控路径：`feishu_tasklist_create` 依据 `lark-cli schema task.tasklists.create` 和 `lark-cli task +tasklist-create --dry-run`；`feishu_tasklist_update` 依据 `lark-cli schema task.tasklists.patch --format json` 和 `lark-cli task tasklists patch --params '{"tasklist_guid":"tl_test"}' --data '{"tasklist":{"name":"销售跟进清单2026"},"update_fields":["name"]}' --dry-run --as user --format json` 接入 `PATCH /open-apis/task/v2/tasklists/:tasklist_guid`，当前只开放清单名称更新，不开放 owner 转移；`feishu_task_add_to_tasklist` 依据 `lark-cli task +tasklist-task-add --dry-run`；`feishu_task_set_ancestor` 依据 `lark-cli task +set-ancestor --dry-run` 接入 `POST /open-apis/task/v2/tasks/:task_guid/set_ancestor_task` 与 `ancestor_guid` 请求体；`feishu_task_clear_ancestor` 依据 `lark-cli task +set-ancestor --task-id ... --dry-run --as user` 且不传 `--ancestor-id` 证明的同一路径空 body 接入清空父任务关系；`feishu_task_subtask_create` 依据 `lark-cli schema task.subtasks.create` 和 `lark-cli task subtasks create --dry-run` 接入 `POST /open-apis/task/v2/tasks/:parent_task_guid/subtasks`；`feishu_task_upload_attachment` 依据 `lark-cli task +upload-attachment --dry-run --as user` 接入 `/open-apis/task/v2/attachments/upload`，真实 multipart 上传由 CLI 承担；`feishu_tasklist_update_members` 依据 `lark-cli schema task.tasklists.add_members/remove_members` 和 `lark-cli task +tasklist-members --dry-run` 接入 `POST /open-apis/task/v2/tasklists/:tasklist_guid/add_members|remove_members` 与 `members[{id,type=user,role=editor}]` 请求体；`feishu_tasklist_set_members` 依据 `lark-cli task +tasklist-members --set ... --dry-run --as user` 证明的 GET 清单详情流程接入全量替换，真实执行会先读取当前成员再按差异调用 add/remove；`feishu_task_update_reminders` 依据 `lark-cli schema task.tasks.patch` 与 `lark-cli task tasks patch --dry-run` 接入 API runtime 的 `PATCH /open-apis/task/v2/tasks/:task_guid` 形态，未注入 `client` 时则经 Feishu MCP provider 调用 `lark-cli task +reminder --set 15m,30m|--remove`。`feishu_task_assign_members` 和 `feishu_task_update_followers` 已按 `lark-cli task +assign/+followers --help` 与对应 dry-run 迁入 MCP 实时桥，未注入 `client` 时调用 `--add/--remove/--idempotency-key`，带测试 client 时仍走 API runtime 受控路径。以上均走 dry-run/confirmed/confirmation_token 闸口、`task:write` 权限和写目标审计。
- Task 清单创建的归档参数已按 `lark-cli schema task.tasklists.create --format json` 和 `lark-cli task tasklists create --params '{"user_id_type":"open_id"}' --data '{"name":"测试归档清单","archive_tasklist":true}' --dry-run --as user --format json` 验证；实时 confirmed 写入在带 `archive_tasklist` 时由 Feishu MCP provider 调用 schema 命令 `lark-cli task tasklists create --params ... --data ...`，普通清单和初始化任务列表仍走已验证 shortcut `lark-cli task +tasklist-create`。schema 命令未验证初始化任务列表，因此 `archive_tasklist` 与 `tasks/data` 初始任务同时出现时会在 MCP 执行前阻断；写目标审计和管理后台 dry-run 模板已显式展示 `archive_tasklist`。
- Task 自定义分组写能力已按 `lark-task`、`lark-shared`、`lark-cli schema task.sections.create/patch/delete --format json` 和 `lark-cli task sections create/patch/delete ... --dry-run --as user --format json` 验证；`feishu_task_section_create/update/delete` 已接入 Tool Router、Feishu API capability、API runtime 受控测试路径、Feishu MCP 实时桥、写目标审计和管理后台 dry-run 模板。无测试 `client` 的 confirmed 实时执行会通过 MCP 调用 `lark-cli task sections create|patch|delete --as user --format json --params ... --data ...`，删除分组会在 Tool Router 完成 `confirmed + confirmation_token` 校验后追加 CLI 高风险确认 `--yes`；创建仅开放 `resource_type=tasklist|my_tasks`，tasklist 分组必须带 `resource_id/tasklist_guid`，位置调整只允许 `insert_before` 或 `insert_after` 二选一，更新只允许 `name/insert_before/insert_after` 且 `update_fields` 必须与实际字段一致。
- Task 删除写能力已按 `lark-task`、`lark-shared`、`lark-cli schema task.tasks.delete --format json` 和 `lark-cli task tasks delete --params '{"task_guid":"task_test"}' --dry-run --as user --format json` 验证；`feishu_task_delete` 已接入 Tool Router、Feishu API capability、API runtime 受控测试路径和 Feishu MCP 实时桥。无测试 `client` 的 confirmed 实时执行会通过 MCP 调用 `lark-cli task tasks delete --as user --format json --params '{"task_guid":...}' --yes`，`--yes` 只在 Tool Router 完成 confirmed + confirmation_token 校验后出现；写目标审计记录 `task_guid`，不保存确认令牌原文。
- Task 清单删除写能力已按 `lark-task`、`lark-shared`、`lark-cli schema task.tasklists.delete --format json` 和 `lark-cli task tasklists delete --params '{"tasklist_guid":"tl_test"}' --dry-run --as user --format json` 验证；`feishu_tasklist_delete` 已接入 Tool Router、Feishu API capability、API runtime 受控测试路径和 Feishu MCP 实时桥。无测试 `client` 的 confirmed 实时执行会通过 MCP 调用 `lark-cli task tasklists delete --as user --format json --params '{"tasklist_guid":...}' --yes`，`--yes` 只在 Tool Router 完成 confirmed + confirmation_token 校验后出现；写目标审计记录 `tasklist_guid`，不保存确认令牌原文。
- Task 清单更新写能力已按 `lark-task`、`lark-shared`、`lark-cli schema task.tasklists.patch --format json` 和 `lark-cli task tasklists patch --params '{"tasklist_guid":"tl_test"}' --data '{"tasklist":{"name":"销售跟进清单2026"},"update_fields":["name"]}' --dry-run --as user --format json` 验证；`feishu_tasklist_update` 已接入 Tool Router、Feishu API capability、API runtime 受控测试路径、Feishu MCP 实时桥、写目标审计和管理后台 dry-run 模板。无测试 `client` 的 confirmed 实时执行会通过 MCP 调用 `lark-cli task tasklists patch --as user --format json --params '{"tasklist_guid":...,"user_id_type":"open_id"}' --data '{"tasklist":{"name":...},"update_fields":["name"]}'`，真实执行仍只会在 Tool Router 完成 confirmed + confirmation_token 校验后进入 MCP；写目标审计记录 `tasklist_guid` 和清单新名称，不保存确认令牌原文。
- Runtime 注入：Tool Router 会剥离上游传入的 `client/app_config` runtime-only 参数；Agent Runtime 不能通过参数透传测试或原生 client 绕过 MCP/CLI 实时执行链路。API handler 只保留给同步层、资源发现、管理端同步预览和受控测试路径，不作为实时工具 provider；Feishu API provider 对 confirmed 写操作只做 token 校验后拒绝真实执行，必须改走 Tool Router -> MCP -> CLI。
- 实时读路由：`calendar_qa`、`mail_qa`、`task_qa` 和 `bitable_qa` 默认 provider 已从 local/API 桥接改为 Feishu MCP provider，经 CLI 查询飞书；local 仍作为显式配置 fallback，用于读取已同步 WorkEvent/ExtractedItem 缓存。pending approval 语义意图已从 `approval_qa` 改路由到 `feishu_approval_task_query`，确保“待我审批/待处理审批”查飞书实时审批任务；审批建议和历史审批分析仍走 `approval_qa` 本地历史。后台 Feishu 实时读 API 也已沿用同一链路：通讯录部门/用户/快照、日程列表、IM 群搜索/消息列表、文档内容、Drive 文件列表、Wiki 空间/节点、多维表格表/记录、任务列表、历史会议列表、邮箱邮件列表、邮箱文件夹、邮箱详情和待审批列表通过 `_execute_admin_feishu_read_tool` 进入 Tool Router，并由 MCP provider 调度 CLI 返回 `response_format=raw_json`；`feishu_admin_read_tool_contact_routes.py` 已收敛为通讯录子路由聚合器，部门、用户和 snapshot 入口分别位于 contact department/user/snapshot 子路由；`feishu_admin_read_tool_bitable_routes.py` 已收敛为 Bitable 子路由聚合器，表和记录入口分别位于 Bitable table/record 子路由；待审批列表会通过 `feishu_approval_task_query` 读取任务，再通过 `feishu_approval_instance_get` 补实例详情。Bitable 后台读按 CLI `offset/limit` 执行，旧 `page_token` 只接受数字 offset。架构测试锁定这些入口不得重新引用 `FeishuContactService`、`FeishuCalendarService`、`FeishuBitableService`、`FeishuTaskService` 或 `FeishuMailService`，且不得恢复 `.fetch_pending_tasks(`。IM 群搜索/消息列表已按 `lark-im`/`lark-shared`/`lark-cli im +chat-search/+chat-messages-list` 验证后接入 `feishu_im_chat_search` 与 `feishu_im_message_list`，归入 ChatTool；Wiki 空间/节点已按 `lark-wiki`/`lark-shared`/`lark-cli wiki +space-list/+node-list` 验证后接入 `feishu_wiki_space_list` 与 `feishu_wiki_node_list`，归入 KnowledgeTool；Drive 文件列表已按 `lark-drive`/`lark-shared`/`lark-cli drive files list` 和 schema 验证后接入 `feishu_drive_file_list`，归入 KnowledgeTool；邮箱文件夹和邮箱详情已按 `lark-mail`/`lark-shared`/`lark-cli mail user_mailbox.folders list`/`lark-cli mail +message` 验证后接入 `feishu_mail_folder_list` 与 `feishu_mail_message_get`；历史会议搜索已按 `lark-vc`/`lark-shared`/`lark-cli vc +search` 验证后接入 `feishu_vc_meeting_search`，归入 MeetingTool。
- 旧 `approval_service_runtime.py` 已删除，避免保留一条绕过 Tool Router 的审批实时 API helper；架构测试锁定该文件不得恢复。需要解析审批 task payload 的位置直接使用 `extract_approval_task_items`，需要实时任务/实例详情的入口必须走 `feishu_approval_task_query` 和 `feishu_approval_instance_get`。
- 权限闸口和 `AuditLog` 执行记录；写工具审计记录 `write_mode`、`dry_run`、`confirmed`、`has_confirmation_token` 和 `write_target` 目标摘要，覆盖 Bitable 记录 app/table/record/upsert_mode/附件 file_count/file_names/file_token_count、建表/建字段/字段更新目标与字段名、视图筛选 filter_logic/condition_count、视图排序 sort_count/sort_fields、视图分组 group_count/group_fields、视图可见字段 visible_field_count/visible_fields、视图卡片 cover_field/cover_field_clear、视图时间轴 timebar_fields、Task task_guid/update_fields/relative_fire_minutes/附件 file_name、Task 分组 section_guid/resource_type/resource_id/insert_before/insert_after、Task/Calendar 创建标题、Approval approval_code/instance_code/task_id/task_ids/node_ids/transfer_user_id/add_sign_user_ids/cc_user_ids/form_count/form_fields/comment_present/add_sign_type/approval_method、审批撤回和抄送 instance_code、IM receive_id/name/chat_ids 等关键 ID，不保存 token、审批表单值、评论正文、附件内容、附件本地完整路径、附件 file_token 原文、Bitable 视图筛选条件值或 confirmation_token。`tests/test_tool_router.py::test_all_feishu_write_capabilities_have_auditable_target_operations` 已从 `FEISHU_API_CAPABILITIES` 派生校验所有飞书写工具都有可审计 `write_target.operation`，避免新增写工具但系统日志无法归类。Tool Router 每次执行还会把 `preferred_execution_engine=lark_cli`、`realtime_policy=tool_mcp_cli_only`、`realtime_bridge=feishu_mcp`、`mcp_provider=feishu_mcp`、`api_role=sync_engine_only`、`responsibility_boundary={"tool_router":"business_capability","mcp":"tool_scheduling","cli":"action_execution","api":"sync_engine_data_sync"}`、`execution_chain=["agent_runtime","tool_router","tool","mcp","cli","feishu"]`、`agent_runtime_direct_access=false`、`sync_engine_direct_api_allowed=false`（写工具）和 `sync_engine_mcp_access_allowed=false` 写入结果 metadata 与 AuditLog payload，便于后台和系统日志直接核对实时执行链路没有混用 API/CLI 职责。
- 工具配置模型、后台 API 和管理后台工具页：`ToolConfig`、`/api/v5/tools`、`/api/v5/tools/{tool_name}`、`/api/v5/tools/batch`、`/api/v5/tools/{tool_name}/execute`、`/api/v5/tools/executions`；`app/api/routes/v5_tool_routes.py` 已收敛为工具子路由聚合器，`v5_tool_list_routes.py` 已收敛为工具列表子路由聚合器，工具列表和执行日志入口分别位于 `v5_tool_catalog_routes.py` 与 `v5_tool_execution_log_routes.py`，后台工具执行入口已迁入 `v5_tool_execution_routes.py`，`v5_tool_config_routes.py` 已收敛为配置子路由聚合器，单工具配置更新位于 `v5_tool_single_config_routes.py`，批量策略入口位于 `v5_tool_batch_config_routes.py`，请求模型已迁入 `v5_tool_request_models.py`；工具后台服务已从 `v5.py` 拆入 `app/services/v5_tool_admin.py`，承接工具列表、执行日志、后台工具执行、单工具配置更新、批量工具策略、工具执行结果和写目标摘要 payload，`v5.py`、`v5_tool_routes.py`、`v5_tool_list_routes.py` 和 `v5_tool_config_routes.py` 路由层不再直接持有工具 endpoint、拼 `ToolContext/ToolRequest`、访问 `TOOL_REGISTRY` 或组装工具执行审计 payload。管理后台工具 payload 已回显 `business_tool`、`business_tools` 和 `business_tool_capabilities`，按 XMind 9 个业务工具族组织现有原子工具：ApprovalTool、KnowledgeTool、BitableTool、ChatTool、CalendarTool、MeetingTool、ReportTool、AutomationTool、PeopleTool；Mail 归 ChatTool，Task 归 AutomationTool，日历/日程归 CalendarTool，会议/妙记/会议室/会议纪要归 MeetingTool，Docs/Wiki/Drive 文档搜索归 KnowledgeTool；AutomationTool 覆盖提醒、自动化流程、自动条件触发、执行，PeopleTool 覆盖考勤、绩效、薪酬；`feishu_cli_status`、`feishu_cli_doctor` 作为 DevOps 系统诊断工具保留在 Tool Router 审计链路内，但不归入业务工具族。管理后台工具页已支持查看工具、Provider、兼容 Provider、权限、写能力并保存启用状态、Provider 和 `config_json`，并支持按全部工具、飞书工具或写工具批量预览/应用启用状态与 Provider 策略；批量策略 API 会逐项返回 updated/failed，不因单个工具不兼容中断整批，应用时写入 `tool.config.batch_update` 审计且只记录 `config_json_keys`，不保存配置正文；后台 API 和工具页均可通过统一 Tool Router 执行 Bitable、Task、审批等写工具 dry-run/confirmed，并能从工具执行结果联动到系统日志筛选对应工具动作；后台执行模板已覆盖全部 Feishu 写能力，`tests/test_cockpit.py::test_console_tool_templates_cover_all_feishu_write_capabilities` 会从 `FEISHU_API_CAPABILITIES` 反查控制台模板，防止后续新增 Bitable/Task/Approval/Calendar/IM 写工具但后台无法 dry-run；`tests/test_feishu_provider_boundary.py::test_feishu_api_capabilities_have_complete_router_mcp_and_runtime_bindings` 已从 capability 反查 Tool Registry、MCP realtime/只读绑定和 API runtime 读写绑定，避免新增飞书工具只登记一半链路；列表与保存接口都会回显 `compatible_providers`、`provider_boundaries` 和 `config_json`；`provider_boundaries` 已明确 Feishu API 是否可用、API 写能力是否已登记、API provider 是否禁止 confirmed 实时写、写操作是否只能 dry-run 预演、Feishu MCP 是否需要显式工具绑定、默认禁写状态、当前 `binding_status=unbound` 和阻断原因；写工具的 Tool Router metadata、AuditLog 和 provider boundary 已将 `sync_engine_direct_api_allowed` 收紧为 `false`，并将 Feishu API provider 的 `supports_write=false`、`supports_confirmed_realtime_write=false`、`confirmed_write_policy=blocked_realtime_use_tool_router_mcp_cli` 写入返回，避免把 Bitable/Task/Approval 实时写能力误解释成 API 或同步层职责；后端会拒绝未验证的 Provider 覆盖，不兼容 Provider 返回 400，未知工具返回 404；Agent runtime 读取历史 ToolConfig 时也会重新校验 Provider 兼容性；Provider 切到 Feishu API/MCP/DevOps 时会显示运行边界和风险提示，审计页已显示工具执行日志、写模式、确认令牌状态、执行链路和写目标摘要。单工具和批量工具配置更新均保留已有 Provider，只有显式传入 Provider 时才切换，避免启停策略误把飞书能力重置回默认 Provider。
- 管理后台工具执行面板已补齐所有 Tool Registry 注册工具的参数模板，覆盖 Calendar、Task、Bitable、Mail、Drive、IM、Wiki、Meeting、Approval 实时读工具，以及本地问答、ReportTool、KnowledgeTool 和 DevOps 诊断工具；`tests/test_cockpit.py::test_console_tool_templates_cover_all_registered_tools` 会从 `TOOL_REGISTRY` 反查控制台模板，避免后续新增工具但后台无法执行。
- 管理后台工具列表已直接展示 `business_tool`，统计区显示当前已覆盖的业务工具族数量，选中工具时会显示 `business_tool_capabilities`，便于按 XMind 9 个 Tool 检查上线覆盖。
- Agent 管理后台路由：`app/api/routes/v5_agent_routes.py` 已收敛为子路由聚合器，`v5_agent_trace_routes.py` 已收敛为 Agent trace 子路由聚合器，`/api/v5/agent/trace-preview` 和 `/api/v5/agent/traces` 分别位于 `v5_agent_trace_preview_routes.py` 和 `v5_agent_trace_list_routes.py`；`v5_agent_settings_routes.py` 已收敛为 Agent 设置子路由聚合器，`/api/v5/agent/settings` 的读取和保存入口分别位于 `v5_agent_settings_read_routes.py` 和 `v5_agent_settings_write_routes.py`，请求模型位于 `v5_agent_request_models.py`；业务 payload 已拆入 `app/services/v5_agent_admin.py`，承接 Agent Runtime trace preview、trace 日志 payload、Agent 设置默认值读取和保存审计；`v5.py`、`v5_agent_routes.py`、`v5_agent_trace_routes.py` 与 `v5_agent_settings_routes.py` 路由层不再持有 Agent 管理 endpoint，也不直接调用 `answer_agent_message_with_trace`、`agent_runtime_result_payload`、`get_company_agent_settings` 或 `update_company_agent_settings`。
- 系统日志后台路由：`app/api/routes/v5_system_log_routes.py` 已收敛为系统日志子路由聚合器，`/api/v5/system/logs/overview` 位于 `v5_system_log_overview_routes.py`，`/api/v5/system/logs` 位于 `v5_system_log_list_routes.py`；业务 payload 已拆入 `app/services/v5_system_logs.py`，承接 `AuditLog` 查询、limit 边界和 payload 过滤；`v5.py` 与 `v5_system_log_routes.py` 路由层不再持有系统日志 endpoint，也不直接查询 `AuditLog` 或调用系统日志聚合 helper。
- Administration 后台路由：`app/api/routes/v5_administration_routes.py` 已收敛为子路由聚合器；`v5_administration_foundation_routes.py` 已收敛为 foundation 子路由聚合器，`/api/v5/bootstrap/foundation` 和 `/api/v5/os/overview` 分别位于 `v5_administration_bootstrap_routes.py` 与 `v5_administration_os_overview_routes.py`；`app/api/routes/v5_administration_list_routes.py` 已收敛为列表子路由聚合器，`v5_administration_user_setting_routes.py`、`v5_administration_org_routes.py` 和 `v5_administration_permission_routes.py` 均已继续收敛为子路由聚合器，`/api/v5/administration/users`、`company-settings`、`departments`、`teams`、`roles`、`permissions` 和 `resource-permissions` 分别位于对应单 endpoint 子路由；`access-preview` 已迁入 `app/api/routes/v5_administration_access_routes.py`，请求模型已迁入 `app/api/routes/v5_administration_request_models.py`；业务 payload 已收敛到既有 `app/services/v5_administration.py`。`v5.py`、`v5_administration_routes.py`、`v5_administration_foundation_routes.py`、`v5_administration_list_routes.py`、`v5_administration_user_setting_routes.py`、`v5_administration_org_routes.py` 和 `v5_administration_permission_routes.py` 路由层不再持有 Administration endpoint，不再直接拼用户/角色/部门/权限 SQL，也不直接构造 `AccessPrincipal`。架构测试已锁定所有 `app/api/routes/v5*.py` 文件最多只持有 1 个 endpoint，所有含 `router.include_router(...)` 的 V5 聚合器不得 import service、注入 `get_db`、访问 DB 或返回业务 payload。
- 资源和工作区后台路由：`app/api/routes/v5_resource_routes.py` 已收敛为资源子路由聚合器；`app/api/routes/v5_resource_status_routes.py` 已收敛为资源状态子路由聚合器，`v5_resource_overview_routes.py` 已收敛为 overview 子路由聚合器，`/api/v5/resources`、`company-overview` 和 `sync-strategy` 分别位于 `v5_resource_list_routes.py`、`v5_resource_company_overview_routes.py` 和 `v5_resource_sync_strategy_routes.py`，`v5_resource_sync_status_routes.py` 已收敛为同步状态子路由聚合器，`sync-status`、`sync-runs` 和 `monitoring` 分别位于 `v5_resource_sync_status_list_routes.py`、`v5_resource_sync_run_routes.py` 和 `v5_resource_monitoring_routes.py`，`v5_resource_policy_routes.py` 已收敛为同步策略子路由聚合器，`/api/v5/resources/sync-policy` 的读取和保存入口分别位于 `v5_resource_policy_read_routes.py` 与 `v5_resource_policy_write_routes.py`，`/api/v5/workspace/events` 位于 `v5_workspace_event_routes.py`；`app/api/routes/v5_resource_sync_routes.py` 已收敛为批量同步子路由聚合器，`/api/v5/resources/sync-preview` 位于 `v5_resource_batch_preview_routes.py`，`/api/v5/resources/sync` 位于 `v5_resource_batch_execute_routes.py`；`app/api/routes/v5_resource_workspace_routes.py` 已收敛为 workspace 子路由聚合器，`v5_resource_single_sync_routes.py` 已继续收敛为单资源同步子路由聚合器，`/api/v5/resources/{resource_id}/sync` 位于 `v5_resource_direct_sync_routes.py`，`retry-access-block` 位于 `v5_resource_retry_routes.py`；`v5_resource_access_routes.py` 已收敛为访问动作子路由聚合器，`clear-access-block` 与 `access-decision` 分别位于 `v5_resource_clear_access_block_routes.py` 和 `v5_resource_access_decision_routes.py`；请求模型已迁入 `app/api/routes/v5_resource_request_models.py`。资源状态 payload 已收敛到 `app/services/v5_resource_status.py`，批量同步编排已收敛到 `app/services/v5_auto_sync.py`，access block 和单资源动作已收敛到 `app/services/v5_workspace.py`，同步策略已收敛到 `app/services/v5_sync_policy.py`。`v5.py`、`v5_resource_routes.py`、`v5_resource_status_routes.py`、`v5_resource_overview_routes.py`、`v5_resource_sync_status_routes.py`、`v5_resource_policy_routes.py`、`v5_resource_sync_routes.py`、`v5_resource_workspace_routes.py`、`v5_resource_single_sync_routes.py` 和 `v5_resource_access_routes.py` 不再直接 import Data Layer ORM 模型、拼 `Resource/ResourceSyncRun/WorkEvent` SQL、构造 RAG/document store 摘要、选择批量同步资源或直接处理 access block 状态。
- Intelligence 和同步策略后台路由：`app/api/routes/v5_intelligence_routes.py` 已收敛为 Intelligence 子路由聚合器，`/api/v5/intelligence/risk-noise/close-finance-documents`、`/api/v5/intelligence/noise/close-low-signal-notices` 和 `/api/v5/intelligence/business-items/enrich-open` 分别位于 `v5_intelligence_risk_noise_routes.py`、`v5_intelligence_low_signal_routes.py` 和 `v5_intelligence_business_item_routes.py`，业务 payload 已收敛到 `app/services/v5_intelligence_admin.py`；`/api/v5/resources/sync-policy` 读取和保存 payload 已收敛到 `app/services/v5_sync_policy.py` 并由 `v5_resource_policy_read_routes.py` 和 `v5_resource_policy_write_routes.py` 承接入口，`v5_resource_policy_routes.py` 只 include 两个子路由。`v5.py`、`v5_intelligence_routes.py` 和 `v5_resource_policy_routes.py` 不再直接调用底层 AI 清理/富化 service、不直接 `db.commit()`，也不直接拼同步策略响应。
- ToolConfig 上线迁移：新增 `alembic/versions/0017_tool_configs_feishu_mcp_defaults.py`，将历史 `provider='feishu_api'` 的飞书实时工具配置一次性改为 `provider='feishu_mcp'`；`tests/test_v5_architecture.py::test_feishu_api_tool_config_migration_targets_all_realtime_tools` 会校验迁移工具名与 `FEISHU_API_CAPABILITIES` 完全一致，避免默认 provider 改为 MCP 后旧配置在生产库中被后端拒绝。
- 审批卡片待办列表、实例详情和附件摘要实时查询已收敛到 Tool Router：`app/services/feishu/approval_card_entrypoint.py::_fetch_feishu_pending_approval_tasks` 通过 `execute_agent_tool` 调用 `feishu_approval_task_query`，MCP provider 调度 `lark-cli approval tasks query --format json`，并通过 `response_format=raw_json` 返回结构化 CLI payload 供卡片上下文使用；随后通过 `feishu_approval_instance_get` 调度 `lark-cli approval instances get --format json` 填回 `instance_detail`。附件读取新增 MCP-only `feishu_approval_attachment_download`，由 `lark-cli drive +download --file-token --output ...` 执行下载动作，entrypoint 只负责本地文本提取和临时文件清理。架构测试禁止 entrypoint 重新依赖 `FeishuApprovalService` 或 `FeishuApprovalAttachmentService` 拉实时待办/详情/附件，provider 测试锁定任务、实例详情 raw JSON 和附件下载 CLI argv。
- DevOps provider：已接入只读 `feishu_cli_status` 与 `feishu_cli_doctor`，通过 Tool Router 复用权限和审计，只允许检查 `lark-cli --version`、`lark-cli --help` 与 `lark-cli doctor --offline`，并展示路径、版本、退出码、关键命令覆盖、命令摘要和结构化健康检查摘要，不开放任意 CLI 命令执行；管理后台执行面板已区分读/写工具，读工具直接执行，写工具保留 dry-run/confirmed 流程。业务工具的 provider boundary 已额外暴露 `preferred_execution_engine=lark_cli`、`realtime_policy=tool_mcp_cli_only`、`realtime_bridge=feishu_mcp`、`mcp_provider=feishu_mcp`、`execution_chain=["agent_runtime","tool_router","tool","mcp","cli","feishu"]`、`api_role=sync_engine_only`、读工具 `sync_engine_direct_api_allowed=true`、写工具 `sync_engine_direct_api_allowed=false`、`sync_engine_mcp_access_allowed=false` 和 `agent_runtime_direct_access=false`；同时暴露 API `role=sync_engine_data_sync/realtime_read_allowed=false/realtime_write_allowed=false/mcp_access_allowed=false/controlled_validation_allowed=true`、MCP `role=tool_scheduling/performs_action_execution=false/action_executor=lark_cli` 与 CLI `role=action_execution/allowed_caller_module=app.services.tools.providers.feishu_mcp/accepts_business_capability=false/schedules_tools=false/performs_action_execution=true`，用于区分“实时业务工具经 Tool 和 MCP 调度后只由 CLI 执行动作”和“同步/入库仍由 Sync Engine 走 API Client，禁止走 MCP”。CLI executor 已加入运行时调用来源校验，非 Feishu MCP provider 直接调用会被拒绝。Feishu API provider 自身已要求显式 `api_entrypoint`，缺少同步/资源发现/管理预览/受控验证入口时会拒绝直接执行。
- Agent Runtime、Tool Router 与 Sync Engine 飞书边界已由 `tests/test_v5_architecture.py` 锁定：`app/services/agent` 不得直接引用 `lark-cli`、`FeishuClient`、Feishu API runtime 或 Feishu MCP provider，只能通过 `execute_agent_tool` 进入 Tool Router；Tool Router 只分发业务工具到 provider，不得直接接触 `lark-cli`、`subprocess`、`FeishuClient`、Feishu API runtime、MCP realtime executor 或原生 API 写方法，并必须剥离 Agent 上游传入的 `client/app_config` runtime-only 参数；同一测试文件还锁定 `feishu_api.py` 不得 import/call `subprocess` 或 MCP provider，Feishu MCP provider 只保留工具到 CLI 命令的 MCP 调度映射，真正 `subprocess.run` 执行动作已收敛到 `app/services/tools/providers/lark_cli.py`，MCP 不得 fallback 到 API runtime，并锁定同步相关模块不得引用 Tool Router、ToolRequest/ToolContext/ToolProvider、Feishu API provider/runtime 或 Feishu MCP provider，确保 Sync Engine 只走 API Client/本地入库路径。职责边界红线测试 `test_v5_feishu_realtime_execution_respects_router_mcp_cli_api_roles`、`test_v5_feishu_contracts_do_not_mix_router_mcp_cli_api_roles` 和 Tool Router/provider boundary 测试会同时锁定结构化职责字段：Tool Router=业务能力，MCP=工具调度，CLI=执行动作，API=Sync Engine 数据同步；全仓扫描 `subprocess.run`、Feishu API runtime import 和 MCP realtime executor 调用位置：CLI 执行动作只允许在 `lark_cli.py`（业务实时动作执行器）和 `devops.py`（只读健康检查），Feishu API provider 契约明确 `role=sync_engine_data_sync` 且禁止 realtime read/write 与 MCP access，Feishu API provider 不得调用 `execute_feishu_api_write_tool` 执行 confirmed 写，MCP realtime executor 只能在 Feishu MCP provider 内部调度，进一步固化“Tool Router 负责业务能力、MCP 负责工具调度、CLI 负责执行动作、API 负责数据同步”的职责边界。
- Feishu 管理路由和写 service 防回归：`tests/test_v5_architecture.py` 已锁定 `app/api/routes/feishu.py` 只能 include 子 router，且不得直接调用 `api_post/api_put/api_patch/api_delete/send_message/update_message_content`；Task、Bitable、Approval、IM 写 service 只能由 `app/services/feishu/api_runtime.py` 调用，外部写入口必须进入 Tool Router 或 Gateway responder。管理后台同步聚合器 `app/api/routes/feishu_admin_sync_routes.py` 只 include message/information/resource 子路由，请求模型已迁入 `feishu_admin_sync_request_models.py`，各子路由仅调用 `app/services/feishu_admin_sync.py`，同步业务逻辑留在 service 内，路由不得重新内联 `FeishuClient.list_messages` 循环、`ingest_feishu_message`、抽取触发、`sync_feishu_information`、`discover_feishu_resources` 或 Celery discovery task；管理后台 sync-plan、capability probe 和审批资源列表 HTTP 入口已迁入 `app/api/routes/feishu_admin_capability_routes.py`，能力探测业务逻辑已迁入 `app/services/feishu_admin_capabilities.py`；管理后台 API client 预览/同步准备读聚合器 `app/api/routes/feishu_admin_api_read_routes.py` 只 include Mail 子路由，Mail 聚合器 `feishu_admin_api_read_mail_routes.py` 只 include accessible-mailboxes 资源发现辅助入口；IM、Meeting、Wiki、Drive、邮箱文件夹和邮箱详情等 CLI 已具备能力的实时读取入口不得回退到 API-read route。管理后台实时只读工具聚合器 `app/api/routes/feishu_admin_read_tool_routes.py` 只 include approval/contact/calendar/drive/im/knowledge/wiki/bitable/task/mail/meeting 子路由，请求模型位于 `feishu_admin_read_tool_request_models.py`，各子路由仅调用 `app/services/feishu_admin_read_tools.py`，实时读工具编排、通讯录 snapshot 审计和审批 pending 审计留在 service 内；管理后台写工具聚合器 `app/api/routes/feishu_admin_write_routes.py` 只 include IM/Approval 子路由，IM 聚合器 `feishu_admin_write_im_routes.py` 只 include message/chat/auto-join 子路由，请求模型已迁入 `feishu_admin_write_request_models.py`，各子路由仅调用 `app/services/feishu_admin_write_tools.py`，Tool Router 调用、ToolRequest、confirmation_token 和 write_target 审计细节留在 service 内，旧 route 层 `_auto_join_public_chats/_unique_strings` 兼容别名已删除。审批卡片 responder 已被锁定为平台中立模块，不允许直接引用 `FeishuClient`、Gateway 发送器、Tool Router、ToolRequest/ToolContext 或原生 API 写方法；Feishu 发送、消息更新、确认令牌和 Tool Router 审批提交只能由 `approval_card_entrypoint.py` 装配。公开群 auto-join 旧管理 helper 只保留 dry-run 候选预览，真实加入只能走 Tool Router confirmed 路径。
- Feishu 事件入口防回归：`tests/test_v5_architecture.py` 已锁定 `/api/feishu/events/{app_config_id}` HTTP 入口位于 `app/api/routes/feishu_event_routes.py`，只调用 `app/services/feishu_event_entrypoint.py::receive_feishu_event_payload`，route 不得重新内联 `verify_feishu_token`、URL verification challenge、`ingest_feishu_event` 或 `handle_feishu_command`。
- Feishu 机器人入口可观测性：HTTP 事件和 WebSocket card action response 两条路径中的未知卡片动作不再被误记为普通消息 `not_addressed_to_bot`，会审计为 `gateway.feishu.card_action` 且 `reason=unhandled_card_action`；系统日志 payload 已暴露 `reason` 并将该类入口异常标为 warning，管理后台系统日志表展示原因列并提供“卡片未处理”快捷筛选。V5 OS overview 已返回 `entrypoints`，后台首屏直接展示大飞哥 AI 兜底开启状态；数据库不可用时该接口返回 `status.database=unavailable` 与零计数，前端 bootstrap 和离线态 `safeLoad/safeAction` 会停止后续 DB 重接口与写/操作请求，保证后台首屏和设置/审计导航仍能进入且不刷 500/POST 日志。上线模板 `.env.example` 默认 `FEISHU_BOT_AI_MODE_ENABLED=true`，保证大飞哥新部署后具备自然语言 Agent Runtime 兜底。
- Feishu OAuth callback 防回归：`tests/test_v5_architecture.py` 已锁定 `/api/feishu/oauth/callback` HTTP 入口位于 `app/api/routes/feishu_oauth_routes.py`，只调用 `app/services/feishu_oauth_helpers.py::feishu_oauth_callback_payload`，route 不得重新内联 state 解析、`db.get(FeishuAppConfig)`、token exchange、HTML page 组装或错误文案提取。
- WorkEvent 管理路由防回归：`tests/test_v5_architecture.py` 已锁定 `app/api/routes/work_events.py` 只保留 `/api` prefix、admin 依赖和子 router include；`work_events_collection_routes.py`、`work_events_processing_routes.py` 和 `work_events_analysis_routes.py` 也只 include 单 endpoint 子路由；各子路由不得直接执行 `select/db.get/db.commit/db.refresh`、`upsert_work_event`、抽取、日报、向量索引或 pending task 队列逻辑；这些 Data Layer 调试/维护 payload 统一由 `app/services/work_events_admin.py` 承接。
- 公司/账号和 quick setup 路由防回归：`tests/test_v5_architecture.py` 已锁定 `app/api/routes/companies.py` 只 include 子 router，不得重新出现 route decorator、请求模型、`Depends(get_db)` endpoint、service import 或业务返回逻辑；`companies_company_routes.py` 和 `companies_account_routes.py` 已继续收敛为聚合器，`/companies` 创建/列表、`/accounts` 创建和 `/companies/{company_id}/accounts` 列表分别位于单 endpoint 子路由，`/onboarding/company-setup` 位于 `companies_onboarding_routes.py`，quick setup 请求模型位于 `companies_request_models.py`。这些子路由不得直接执行 `select/db.get/db.commit/db.refresh`、创建 `Company/Account/FeishuAppConfig/BotUserAccess`、审计、账号类型推断或 V5 资源登记；payload 已迁入 `app/services/companies_admin.py`。旧 route 层 `_upsert_*` 兼容别名已删除，quick setup mail resource payload 不再输出 `legacy_*` 字段。
- 外部邮箱管理路由防回归：`tests/test_v5_architecture.py` 已锁定 `app/api/routes/mail.py` 只 include 子 router，不得重新出现 route decorator、请求模型、`Depends(get_db)` endpoint、service import 或业务返回逻辑；`/api/mail/imap/sync` 位于 `mail_imap_routes.py`，`mail_oauth_routes.py` 只 include Gmail/Graph OAuth 子路由，Graph message payload 位于 `mail_graph_routes.py`，IMAP 请求模型位于 `mail_request_models.py`。这些子路由不得直接创建/完成 SyncRun、调用 `ImapMailClient/GmailOAuthService/GraphMailService/get_account_or_404` 或提交数据库；payload 统一由 `app/services/mail_admin.py` 承接。
- Owner 驾驶舱路由防回归：`tests/test_v5_architecture.py` 已锁定 `app/api/routes/cockpit.py` 只 include 子 router，不得重新出现 route decorator、`Depends(get_db)` endpoint、service import 或业务返回逻辑；`/api/cockpit/overview` 位于 `cockpit_overview_routes.py`，`/api/cockpit/modules/{module_key}` 位于 `cockpit_module_routes.py`，查询参数类型位于 `cockpit_query_params.py`。子路由不得直接调用 `build_scope/build_cockpit_overview/build_cockpit_module`、直接 `.model_dump()` 或自行转换 `ValueError/HTTPException`；HTTP 管理 payload 统一由 `app/services/cockpit_admin.py` 承接，底层经营驾驶舱模块仍在 `app/services/cockpit/*`。
- 旧 operations 聚合器防回归：`tests/test_v5_architecture.py` 已锁定 `app/api/routes/operations.py` 只 include 子 router，不得重新出现 route decorator、请求模型、`Depends(get_db)` endpoint、SQL、提交或业务返回逻辑。
- V5 admin 聚合器防回归：`tests/test_v5_architecture.py` 已锁定 `app/api/routes/v5.py` 只 include `v5_*_routes.py` 子 router，不得重新出现 route decorator、请求模型、`Depends(get_db)` endpoint、service import、SQL、提交或业务返回逻辑。
- 旧资源列表和登记防回归：`tests/test_v5_architecture.py` 已锁定旧 `operations_resource_routes.py` 不得恢复；`operations_resource_list_routes.py` 的旧 `/api/feishu/resources` 列表只调用 `operations_resources.list_v5_feishu_resources()`，列表主口径以 V5 `Resource(platform="feishu")` 为准，不得重新以 `FeishuResource` 查询作为列表主口径，且不得恢复 `_legacy_resources_by_id` 旧表二次查询；`operations_resource_registration_routes.py` 的手工登记路由只调用 `operations_resources.register_v5_feishu_resource()` request wrapper，不得重新定义请求模型或创建旧 `FeishuResource`；旧表 raw SQL 读取和 `legacy_feishu_resource_id` 字段使用已被架构测试限制在迁移兜底、`operations_resources` 统计和资源 payload 等 allowlist 内，聚合器不得再持有旧表统计或 legacy 字段。
- 旧 Memory operations 防回归：`tests/test_v5_architecture.py` 已锁定旧 `operations_memory_routes.py` 和 `operations_memory_fact_routes.py` 不得恢复，`operations_memory_fact_create_routes.py` 的 `/api/memory-facts` 创建入口只调用 MemoryFact 创建 service，`operations_memory_fact_list_routes.py` 的 `/api/memory-facts` 列表入口只调用 MemoryFact list service，`operations_memory_generation_routes.py` 的 `/api/memory-facts/generate` 只调用记忆生成 request wrapper；这些 route 不得重新定义请求模型、直接构造/查询 `MemoryFact` 或内联 `generate_recent_memory_task`。
- 旧 Bot 用户 operations 防回归：`tests/test_v5_architecture.py` 已锁定旧 `operations_bot_routes.py` 和 `operations_bot_user_routes.py` 不得恢复，`operations_bot_user_upsert_routes.py` 的 `/api/bot-users` 写入入口只调用 Bot 用户 upsert 服务，`operations_bot_user_list_routes.py` 的 `/api/bot-users` 列表入口只调用 Bot 用户 list 服务，`operations_bot_permission_routes.py` 只 include permission rule/recalculation 子路由，`operations_bot_permission_rule_routes.py` 的 `/api/bot-permission-rules` 只调用权限规则服务，`operations_bot_permission_recalculation_routes.py` 的 `/api/bot-users/recalculate-permissions` 只调用权限重算服务；这些 route 不得重新定义请求模型、直接构造/查询 `BotUserAccess`、内联环境管理员补充、部门 WorkEvent 解析或权限推断/合并逻辑。
- 旧 operations 只读列表和状态防回归：`tests/test_v5_architecture.py` 已锁定旧 `operations_read_routes.py` 和 `operations_status_routes.py` 不得恢复，`/api/sync-runs`、`/api/extracted-items`、`/api/reports`、`/api/audit-logs` 分别只能由 `operations_sync_routes.py`、`operations_extracted_routes.py`、`operations_report_routes.py`、`operations_audit_routes.py` 调用 `operations_read_models` 服务；`/api/system/status` 只能由 `operations_system_status_routes.py` 调用 system status service，`/api/automation/status` 只能由 `operations_automation_status_routes.py` 调用 automation status service，不得重新内联列表 SQL、Redis/Qdrant 健康检查或自动化 settings payload，也不得把 status 入口塞回 dashboard route。
- 旧 operations dashboard/advisor/entities 防回归：`tests/test_v5_architecture.py` 已锁定 `operations_dashboard_routes.py` 的 `/api/dashboard/overview` 只调用 `operations_dashboard`，`operations_advisor_routes.py` 的 `/api/advisor/chat` 只调用 `operations_advisor` request wrapper，`operations_entity_routes.py` 的 `/api/entities/{entity_type}` 只调用 `operations_entities` request wrapper，不得重新定义请求模型、内联 dashboard 聚合、company 校验/LLM 调用或 `Person`/`Project`/`Customer` 构造；同一测试文件还全局扫描 `operations_*_routes.py`，禁止恢复 `BaseModel/Field`、`data.*` 字段适配、SQL、实体构造、`HTTPException` 或 `json_safe`。
- 系统日志：`app/services/system_logs.py`、`/api/v5/system/logs/overview` 和 `/api/v5/system/logs` 已将 `AuditLog` 聚合为分类、级别、最近错误、统计计数和可筛选明细，已包含 approval/gateway 分类；审批动作日志会暴露确认状态、确认校验结果和预期动作，工具写操作日志会暴露 `write_target_summary`，Gateway 日志会暴露 reason，但不保存 confirmation_token 原文；管理后台审计页已接入分类/级别/状态/原因/动作/确认状态/确认校验筛选、日志明细、分类统计、最近错误表、审批确认校验字段、Gateway reason 和写目标摘要。

仍缺少：

- 更多飞书写操作能力、审批高级参数编排。

当前风险：

- 新功能容易继续散落在 `feishu`、`v5` 服务中。
- 无法统一控制本地优先、MCP 优先、API 优先。

### 4.4 Data Layer 缺口

已补：

- `ResourceSource.source_type` 已按新版 XMind 归一为身份型来源：`feishu_app_identity`、`feishu_user_identity`、`external_mail_account`、`personal_dingtalk_account`、`local_import`，并保留旧名称读取兼容；`external_web` 只作为 Knowledge Data 的外部 WEB 索引层，不再作为和飞书/邮箱/钉钉同级的数据来源展示；`0013_data_layer_source_taxonomy.py` 会回填旧 source_type。
- `WorkEvent` 入库已统一调用 source normalizer，旧来源名会在写入时归一到新版 taxonomy。
- `sync_strategy_overview()` 已暴露数据来源目录、数据类型目录和 `query_policies`；数据类型目录按新版 XMind 顺序固定为 Operational Data、Knowledge Data、Memory Data；数据来源目录按新版 XMind 只展示飞书企业身份、飞书个人身份、外部邮箱和个人钉钉，外部 WEB 改由 Knowledge Data 的外部存储层表达；V5 Resource payload 已回显 `data_type` 与 `storage_layer`；Operational Data 已明确 `lark_cli_first`，实时查询优先使用飞书 CLI，重要业务数据再进入 WorkEvent；Knowledge Data 已区分 L1 热知识同步到本地 RAG、L2 冷知识只登记并通过飞书 CLI 实时查询、L3 外部知识实时搜索不入库；外部 WEB 已归一为 `web` 资源并进入独立 `knowledge_external_web` 策略层，只保存引用、摘要、来源和可信度，不写入内部知识库事实层，`sync_action` 固定为 `external_realtime_reference`；Memory Data 已归一为 `memory` 资源并进入 `long_term_memory` 策略层，落 `memory_facts`。
- `sync_action_for_resource()` 和 `sync_decision_for_resource()` 已回显资源级 `query_path`：审批/任务/日历等 Operational Data 为 `lark_cli_first_then_work_event_cache`，Bitable 主数据为 `lark_cli_first_then_index`，L2 冷知识文档为 `lark_cli_realtime`，L1 热知识为 `local_rag`，L3 外部知识为 `external_search_realtime` 且 `sync_action=external_realtime_reference`，长期记忆为 `memory_facts`。L2 冷知识仍只写 `feishu.resource.index` 索引事件，不拉全文；L3 外部 Web 资源即使被误触发 `sync_v5_resource()` 也会在决策阶段跳过，不查询 Feishu source、不调用 Feishu API、不创建内部 WorkEvent。显式 `large_document_mode=knowledge_vectorize` 的 L1 热知识现在是可执行同步动作，会通过 Sync Engine/API Client 拉取飞书文档内容，写入 `business_domain=knowledge` 的 WorkEvent，带 `data_layer=knowledge_hot`、`query_path=local_rag`、`vectorize=full_chunk_or_summary_chunk` 和 `rag_indexing={document_store:work_events, chunking:queued, chunk_count:N, vector_db:qdrant_vectors, rag_index:pending, vector_status:pending}` 元数据，并进入抽取和向量化 pending；L1 正文 WorkEvent 已绑定 V5 `Resource.id` 到 `WorkEvent.resource_id`，让 Document Store、资源同步状态和后续 RAG 检索可以追溯到原始飞书资源；`_sync_document_content()` 已在 WorkEvent payload 中生成稳定的 `document_chunks`，`WorkEventVectorIndex` 只接受 `knowledge_hot + local_rag + rag_indexing.document_store=work_events + qdrant_vectors` 的 L1 热知识事件，并优先按 chunk 写入 Qdrant points，在搜索命中 chunk 时回映到原 WorkEvent id，避免把整篇文档只作为单个向量点；WorkEvent 写入层只会把符合 L1/RAG 条件的事件置为 `vector_status=pending` 并触发向量队列，L2 冷知识资源索引、Operational Data、外部知识引用默认 `vector_status=skipped`；Celery 单条和批量向量化任务已统一调用 `mark_event_vector_indexing_result()`，在 Qdrant 写入成功或跳过后同步回写 `WorkEvent.vector_status` 与 payload 内 `rag_indexing.vector_status/rag_index/indexed_point_count`，避免 L1 热知识入队后长期停留在 pending 审计状态；成功返回、`sync_v5_resource()` 结果和 `ResourceSyncRun.summary` 也显式带出 `data_layer/query_path/sync_action/vectorize/rag_indexing`，`/api/v5/resources/sync-runs` 和资源同步状态 `latest_run` 会额外回显 `rag_indexing_summary`，管理后台同步运行表已增加“RAG 入队”列，资源同步状态表已增加“最近 RAG”列，cockpit 数据覆盖模块的资源健康 payload 也会透出最近 RAG 入队状态，便于资源同步和审计侧直接核对策略。Wiki 空间资源仍按 L2 索引处理，即使请求 `knowledge_vectorize` 也只登记空间索引和 `lark_cli_realtime` 查询路径；具体 Wiki 文档 token 已可在 `document_type=wiki` 的 L1 热知识资源中先通过 `GET /open-apis/wiki/v2/spaces/get_node` 解析 `obj_token/obj_type`，再用 docx/doc 正文接口进入本地 RAG 切片。`_sync_document_content()` 已增加同步层防护：只有 `sync_action=knowledge_vectorize`、`data_layer=knowledge_hot`、`query_path=local_rag` 的 L1 热知识允许拉取正文；冷知识或外部知识误入全文同步时会在调用 Feishu API 前阻断，不创建内容 WorkEvent。资源同步状态、文档索引 WorkEvent payload 和 Owner 资源健康 payload 已继续传递 `query_path`。测试已锁定这些路径，避免同步策略退化为全量本地化、绕过 CLI 实时策略或让 Sync Engine 调 MCP。
- L1 热文档同步现在显式回显 Document Store 结果：`sync_feishu_information()` 返回 `document_store={store:work_events, work_event_ids, chunk_count, vector_db, rag_index, vector_status}`，`sync_v5_resource()` 将其写入返回值和 `ResourceSyncRun.summary`，资源同步运行 API、资源状态 latest_run、Owner 驾驶舱资源健康 payload 和管理后台同步表分别显示 `document_store_summary`/“文档库”列。该字段只在 L1 热知识正文入库后出现，L2 冷知识索引和 L3 外部知识不返回，避免把 register-only 或外部实时查询误判为本地 RAG 已建库。
- 企业级 App 身份和个人级 User 身份已进入 source priority 和 preferred source 规则，外部邮箱/个人钉钉按外部或个人连接器处理。
- `ResourceSource.source_type` 和 `WorkEvent.source_type` 已在 SQLAlchemy 模型与 `0014_source_type_constraints.py` 迁移中增加 V5 canonical taxonomy check constraint，旧名必须先经 normalizer/迁移转为新版来源类型。
- `MemoryFact.scope` 已在 SQLAlchemy 模型与 `0015_memory_fact_scope_constraints.py` 迁移中锁定为 `company/domain/personal/user/chat`；`personal/user` 必须有 `user_open_id`，`chat` 必须有 `chat_id`；`memory_fact_access_condition()` 已收紧 owner/admin 的 company 范围，不再默认返回所有人的个人记忆，chat 范围只返回当前群记忆和本人个人记忆。
- `Account.account_type` 已在 SQLAlchemy 模型与 `0016_account_type_taxonomy.py` 迁移中锁定为 `company_feishu_app/personal_feishu_user/feishu_mail/external_mail/personal_dingtalk`；创建账号时会从 `account_type` 或 `provider` 归一化，`provider` 只保留具体驱动语义，飞书个人 OAuth、外部邮箱资源登记和账号返回 payload 均已带出账号类型。
- `FeishuResource` 到 `Resource` 的迁移兜底已集中到 `resource_registry.migrate_legacy_feishu_resources()`；审批资源列表、审批同步和 Drive folder 发现种子不再直接查询旧模型，`FeishuResource` ORM 已删除，旧表读取只保留在 `operations_resources` 迁移统计、V5 administration 全量迁移和 resource registry 集中迁移 helper 的 raw SQL 路径；集中迁移兜底和 V5 administration bootstrap 已能容忍旧 `feishu_resources` 表被部署迁移删除，旧表不存在时直接返回空迁移/0 迁移，不影响 V5 `Resource` 主路径。`0018_detach_resource_from_legacy_feishu_table` 已移除 `resources.legacy_feishu_resource_id` 指向 `feishu_resources.id` 的外键，ORM 也不再保留 `legacy_feishu_resource` relationship；该字段只作为旧数据追溯 ID。`0019_drop_retired_feishu_resources` 已提供最终 drop table 迁移，迁移会先检查旧行是否都已映射到 V5 Resource，未满足 `legacy_retirement.unmapped_legacy_resources=0` 时阻断，满足时删除旧表。
- Operations 资源摘要逻辑已迁入 `operations_resources.dashboard_resource_summary()`，显式返回旧表状态、旧表总数、启用旧资源数、已映射 V5 数、未映射数、`can_drop_legacy_table` 和 `next_action`；只有未映射旧资源为 0 时才允许进入删除旧表阶段。旧表已为空或已被部署迁移删除时，dashboard 会返回 `legacy_table_status=empty_or_retired` 与 `next_action=keep_legacy_table_retired`，避免退役后旧查询拖垮管理后台。

仍缺：

- `FeishuResource` 旧表 raw SQL 迁移兜底仍保留，用于部署前后兼容旧数据库状态；0019 执行后旧表不存在时这些入口只返回空迁移/0 统计。下一步可在确认所有部署环境都完成 0019 后，删除旧迁移兜底代码。

当前风险：

- 旧资源和新资源并存，长期会增加同步和权限判断复杂度。

### 4.5 管理后台缺口

缺少：

- 按 XMind 的系统设置结构组织后台。
- 工具管理：已完成基础可操作页、Provider 风险提示、后端兼容性校验和批量策略预览/应用。
- Agent 设置：已完成公司级基础可读写配置，复用 `CompanySetting.settings["agent"]`，覆盖启用状态、默认模型、Planner、工具调用上限、记忆模式、回答风格、执行轨迹和写工具二次确认；后续补更细模型参数和策略预设。
- 系统日志已完成概览、筛选明细和基础产品化入口，Agent trace 预览和历史列表已写入审计并可查。
- 网页版机器人。

当前风险：

- `v5.py` 和 `operations.py` 已收敛为子路由聚合器。管理后台首屏已产品化为“多公司运营状态”智能中心视图，工具页已可操作，但设置区仍需继续收敛成连接、资源、权限、工具、日志、概览的清晰产品边界。

## 5. 保留、迁移、删除、补齐清单

### 5.1 保留

- SQLAlchemy 模型和现有 Alembic 历史迁移。
- `Resource`、`ResourceSource`、`WorkEvent`、`MemoryFact`、`AuditLog`。
- Feishu SDK/Raw HTTP 双层路由。
- 飞书资源发现和同步能力。
- 已迁入 `tools/*` 的领域问答逻辑。
- Cockpit 模块。
- LLM Gateway 和本地降级能力。
- 当前测试集。

### 5.2 迁移

- `agent/runtime.py` -> 已接入 Tool Router、V5 trace 预览接口、Planner trace、计划内工具执行、多工具保守合成、失败停止和 trace 历史列表，后续补自动重试、分支计划与更复杂的跨工具结果合成
- `bot_intents.py` -> `agent/intents.py`，已完成
- `bot_context.py` -> `agent/context.py`，已完成
- `bot_interaction.py` -> `agent/policies.py`，已完成
- 领域问答实现已迁移到 `tools/approval.py`、`chat.py`、`company.py`、`domain.py`、`personal.py`、`conversation.py`、`knowledge.py`
- `cockpit/*` -> Report provider 能力，`company_qa` 和 `owner_cockpit` 已接入
- `feishu/commands.py` -> `gateway/feishu.py` + `agent/runtime.py` + `tools/*`
- `v5_*` -> `data/*`、`sync/*`、`admin/*`

### 5.3 删除

只在迁移完成后删除：

- `FeishuResource` 旧表 raw SQL 迁移兜底和相关旧接口。
- 已迁完的旧领域问答入口。
- 旧 `operations.py` 聚合接口。
- `v5_*` 临时命名服务。

可以优先清理：

- `.DS_Store`
- `.pycache`
- `.pytest_cache`
- `.ruff_cache`
- `feishu_mail_advisor.egg-info`

### 5.4 补齐

- `gateway` 包已建立，继续补互动卡片 responder、卡片动作和日志历史筛选。
- `agent` 包已建立，继续补自动重试、分支计划和更复杂的跨工具结果合成。
- `tools` 包已建立，继续补正式业务工具和配置模型。
- `data` 包已建立，继续补事件和执行状态枚举。
- Tool 配置模型。
- Agent 配置模型：基础版已复用 `CompanySetting.settings["agent"]`，后续按需要拆独立表。
- 工具执行日志已接入 `AuditLog`，后端查询入口和管理后台审计视图已完成，写工具模式、确认令牌状态和写目标摘要可见。
- 系统日志概览和筛选明细已接入 `AuditLog` 聚合，后端查询入口和管理后台基础视图已完成。
- Gateway 消息安全摘要日志已接入 `AuditLog`。
- ResourceSource 身份优先级规则测试。
- MemoryFact 权限过滤测试。

## 6. 第一实施阶段建议：Phase 1 Data Layer

虽然 Gateway 和 Agent 很显眼，但第一阶段应先做 Data Layer。理由：

- 权限和数据边界是系统底座。
- Tool Router 和 Agent Runtime 都依赖统一资源、身份、记忆和审计模型。
- 当前 Data Layer 已经具备基础，补齐成本可控，收益最大。

### 6.1 Phase 1 具体任务

1. 定义数据枚举和常量：
   - resource type
   - source identity type
   - visibility scope
   - data classification
   - memory scope
   - tool execution status

2. 强化 `ResourceSource`：
   - 明确 `source_type`：`app_identity`、`user_identity`、`local_import`、`external_connector`。
   - 明确优先级：App 身份优先，User 身份补充。
   - 增加统一选择函数。

3. 强化 `WorkEvent` 入库规范：
   - 所有入库必须带 `source_type`。
   - 所有入库必须能追溯 `resource_id` 或明确是临时事件。
   - 所有入库必须有 `visibility_scope` 和 `data_classification`。

4. 强化 `MemoryFact`：
   - 明确 scope 访问规则。
   - 写入时继承 source work_event 的权限范围。
   - 查询时统一走 actor/principal 过滤。

5. 强化审计：
   - 工具调用、同步、回答、权限拒绝都能写审计或执行日志。

### 6.2 Phase 1 验收

必须通过：

```bash
cd digital-advisor
.venv312/bin/python -m pytest tests/test_security.py tests/test_work_events.py tests/test_v5.py tests/test_v5_foundation.py tests/test_v5_architecture.py
.venv312/bin/python -m ruff check app tests alembic/versions/0016_account_type_taxonomy.py
```

不接受：

- 新增数据字段但没有测试。
- 继续扩大旧 `feishu_resources` 表或 `FeishuResource` ORM 使用面。
- 新增绕过 `ResourceSource` 的资源来源逻辑。
- 新增绕过权限过滤的记忆查询逻辑。

## 7. 当前风险列表

1. 管理后台 API 边界仍需继续拆分，不能继续把核心业务新功能塞进旧聚合路由或 `v5.py`；`operations.py` 已降为子路由聚合器，资源管理、Memory operations、Bot 用户 operations、只读列表、系统状态、dashboard 聚合、advisor chat、实体创建、工具管理、Agent 设置/trace、系统日志、Administration 查询、V5 资源状态、资源动作、intelligence 后台动作和同步策略 payload 已分别迁入 `operations_resources.py`、`operations_memory.py`、`operations_bot_users.py`、`operations_read_models.py`、`operations_status.py`、`operations_dashboard.py`、`operations_advisor.py`、`operations_entities.py`、`v5_tool_admin.py`、`v5_agent_admin.py`、`v5_system_logs.py`、`v5_administration.py`、`v5_resource_status.py`、`v5_auto_sync.py`、`v5_workspace.py`、`v5_intelligence_admin.py`、`v5_sync_policy.py`。
2. `FeishuResource` ORM 已删除，旧 `feishu_resources` 表只剩 raw SQL 迁移兜底；资源发现、审批资源列表、审批同步和 Drive folder 发现种子不再各自直接查询旧模型。后续风险集中在发布级历史数据归档、0019 执行前的数据核对，以及 `operations_resources` 迁移统计是否仍需要保留。
3. 核心 runtime 测试已迁到 `test_agent_runtime.py`；`test_tool_router.py` 已覆盖首批工具路由，领域工具测试已迁为 `test_tools_*`。
4. `.venv312/bin/pytest -q` 当前可用，完整测试最近通过；若切换虚拟环境，需要先验证测试入口。
5. `feishu/commands.py` 已完成入口瘦身，后续风险转为防止命令 handler 和审批编排回流到入口文件。

## 8. 下一步执行

Phase 1 Data Layer、Phase 2 Gateway、Phase 3/4 Tool Router、Agent Runtime 和管理后台工具页核心骨架已经落地。下一步按上线收口推进：

1. 机器人入口验收：用 HTTP 事件和 WebSocket 两条路径验证大飞哥收消息、固定命令、自然语言兜底、审批卡片交互和未知卡片动作审计；后台首屏确认大飞哥 AI 兜底状态；继续防止命令业务编排回流到 `feishu/commands.py`。
2. 管理后台验收：按 XMind 9 个业务工具族逐项 dry-run/read 执行工具模板，系统日志中确认 `business_tool`、Provider、执行链路、写目标摘要、Gateway reason 和“卡片未处理”快捷筛选能被查看；数据库不可用时确认首屏、设置和审计导航只请求 OS overview，不刷 500/POST 错误。
3. 同步入库验收：继续验证 Sync Engine -> API Client -> Feishu -> PostgreSQL，不允许 Sync Engine 调 MCP；L1 热知识检查 Document Store/Vector DB/RAG 指标，L2 冷知识只登记 token 和空间信息。
4. 上线前清理：只删除已确认无用的缓存、旧聚合路由和已迁移 helper；旧 `feishu_resources` raw SQL 兜底等发布迁移保护代码等 0019 后再删。
