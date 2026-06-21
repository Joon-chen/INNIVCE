# 老司机（企业数字参谋）V5 完整开发计划

本文档是 `SYSTEM_BLUEPRINT_V5.md` 的执行计划。新的约束是：不能为了尽快上线牺牲功能完整性和架构整体性。系统可以分阶段交付，但每一阶段都必须沿着最终架构前进，不能做上线后必然推倒重来的临时方案。

系统核心目的：它是 Owner 管理多家公司的智能中心，也是各个公司、各个员工的个人智能体。飞书是企业数据和运营的主平台，也是本智能系统的主要数据来源；飞书 API/MCP/CLI 能力是系统工具体系的主干。

## 1. 总原则

1. 架构先定型：Message Gateway、Agent Runtime、Tool Router、Data Layer、交互入口必须先成为真实代码边界。
2. 功能不降级：现有有价值能力必须迁移到新架构，而不是被砍掉换取速度。
3. 删除要彻底：旧代码只有在新边界完整承接、测试覆盖通过后删除。
4. 数据边界优先：权限、身份、资源来源、审计、长期记忆必须从一开始纳入设计。
5. 可上线不等于粗糙：每个阶段都要可运行、可验证、可回滚，但不接受临时拼接。
6. 飞书开发不猜接口：所有飞书相关开发必须优先查看飞书开放平台官网文档，并用本机 `lark-cli` 查询 schema、事件、API 行为；MCP/API/provider 的选择必须可解释。该原则覆盖 Gateway、Agent、Tool Router、Feishu API/MCP provider、同步引擎、资源发现和管理后台。
7. 全局产品原则：系统的核心目的始终是“Owner 的多公司智能中心 + 各公司员工的个人智能体”。飞书是企业数据和运营的主平台，也是本智能系统的主要数据来源；所有阶段和模块都必须围绕这个目标取舍。

## 2. 最终架构目标

```text
交互入口
  - 各个公司的自建应用机器人：大飞哥
  - 管理后台：美观简洁，参考 OpenAI 风格，不做成传统 IT 管理后台
  - 网页版机器人
  - iOS App（后续）

Message Gateway
  - 标准消息模型
  - 飞书事件适配
  - 命令/按钮/卡片动作解析
  - 快速回复/思考回复/卡片响应

Agent Runtime
  - 意图识别
  - 任务规划
  - 权限检查
  - 记忆管理
  - Tool Router 调用
  - 个性化答案组织

Tool Router
  - ApprovalTool
  - KnowledgeTool
  - BitableTool
  - ChatTool
  - CalendarTool
  - MeetingTool
  - ReportTool
  - AutomationTool
  - PeopleTool
  - DevOpsTool
  - provider: Feishu API / Feishu MCP / Local / Report / CLI

Data Layer
  - 本地业务数据
  - 向量数据
  - 长期记忆
  - 资源与权限
  - 审计与同步日志
```

最终代码目录必须体现这些边界：

```text
app/services/gateway/
app/services/agent/
app/services/tools/
app/services/data/
app/services/feishu/
app/services/cockpit/
app/services/llm/
```

其中 `feishu`、`cockpit`、`llm` 是能力提供方；不能再让机器人入口直接散落调用这些能力。

## 3. 必须完整保留的能力

### 3.1 飞书机器人

- 每家公司一个机器人，显示名可统一为“大飞哥”。
- 每个人是独立智能体视角，回答受身份、风格、权限、上下文限制。
- 支持群聊、单聊、卡片动作、按钮命令。
- 支持快速回复和思考回复。
- 支持当前群上下文、个人上下文、公司上下文、跨公司 Owner 上下文。

### 3.2 管理后台

- 系统设置。
- 公司账号。
- 飞书连接。
- 权限配置。
- 工具管理。
- Agent 设置：基础可读写已完成，支持启用状态、默认模型、Planner、工具调用上限、记忆模式、回答风格、执行轨迹、写工具启停和写工具二次确认；运行时已按公司设置在调用写工具前硬拦截。
- 系统日志。
- 经营概览。
- 网页版机器人。

### 3.3 飞书连接

- Auth Manager。
- API/MCP Client。
- Stream Client。
- Event Handler。
- Sync Engine。
- 通讯录、多维表格、Wiki、云文档、OKR、审批、任务、邮箱、日历、人事、IM 消息。

### 3.4 Tool Router

- 工具启用/禁用。
- 本地优先/MCP 优先/API 优先。
- 权限检查。
- 参数校验。
- 审计日志。
- 错误标准化。
- 可追踪执行记录。

### 3.5 Data Layer

- 企业级资源用 App 身份。
- 个人级资源用 User 身份。
- 优先应用身份数据，用户身份数据做补充。
- 本地数据、向量数据、长期记忆、数据规则必须统一。
- 不同权限范围之间不能发生数据泄漏。

## 4. 现有代码迁移策略

### 4.1 保留并迁移

- `app/services/feishu/*`：作为 Feishu API Client、Auth、Sync Engine、资源发现、管理端同步预览和受控 API handler 基础保留，不作为 Agent 实时执行入口。
- `app/services/agent/runtime.py`：已承接问答编排入口，并通过 Tool Router 调用首批工具。
- `app/services/tools/*`：已承接首批领域工具、provider 分派、权限闸口、工具注册表、执行审计和工具配置，继续补更多业务工具。
- `app/services/cockpit/*`：作为 ReportTool 和 Owner 驾驶舱能力保留。
- `app/services/v5_*`：拆分进 `data`、`tools`、`agent`、`admin` 等稳定边界。
- `app/models/entities.py`：保留，但需要补齐最终字段和关系。
- Alembic 迁移：保留历史迁移，不改旧迁移，新增迁移承接结构调整。

### 4.2 明确删除

满足以下条件才删除：

1. 新模块已经完整替代旧模块。
2. 所有调用方已经迁移。
3. 测试覆盖新路径。
4. 全量测试通过。

候选删除：

- 生成物：`.DS_Store`、`.pycache`、`.pytest_cache`、`.ruff_cache`、egg-info。
- 旧资源模型：`FeishuResource` ORM 已从运行模型删除；当前资源发现、旧 operations 手工登记、待审批任务注册、快速公司初始化邮箱资源和公开群自动加入均已停止写旧 `FeishuResource`，新资源主写入统一为 V5 `Resource`。旧 operations 资源注册入口已拆到 `operations_resource_registration_routes.py`，资源列表入口已拆到 `operations_resource_list_routes.py`，旧 `operations_resource_routes.py` 已删除；route 不再直接承载请求模型、请求字段适配、飞书资源注册、列表 payload 或 legacy retirement 统计，这些 V5 Resource 规则、请求模型和 request wrapper 已迁入 `app/services/operations_resources.py`。旧资源迁移兜底已集中到 `resource_registry.migrate_legacy_feishu_resources()`，审批资源列表、审批同步和 Drive folder 发现种子不再直接查询旧模型；旧表读取仅保留为 raw SQL 迁移兜底，架构测试已锁定不得恢复 `FeishuResource` ORM、外键或 relationship。
- 旧 Memory operations：`/api/memory-facts` 创建入口已拆到 `operations_memory_fact_create_routes.py`，`/api/memory-facts` 列表入口已拆到 `operations_memory_fact_list_routes.py`，`/api/memory-facts/generate` 已拆到 `operations_memory_generation_routes.py`，旧 `operations_memory_routes.py` 和 `operations_memory_fact_routes.py` 已删除；路由不再直接定义请求模型、拆 `data.*` 字段、创建/查询 `MemoryFact` 或内联 Celery 任务；请求模型、request wrapper、创建、列表 payload 和近期记忆生成任务已迁入 `app/services/operations_memory.py`。
- 旧 Bot 用户 operations：`/api/bot-users` 写入入口已拆到 `operations_bot_user_upsert_routes.py`，`/api/bot-users` 列表入口已拆到 `operations_bot_user_list_routes.py`，`operations_bot_permission_routes.py` 已收敛为 Bot permission 子路由聚合器，`/api/bot-permission-rules` 和 `/api/bot-users/recalculate-permissions` 分别位于 `operations_bot_permission_rule_routes.py` 与 `operations_bot_permission_recalculation_routes.py`，旧 `operations_bot_routes.py` 和 `operations_bot_user_routes.py` 已删除；路由不再直接定义请求模型、拆 `data.*` 字段、构造/查询 `BotUserAccess`、拼环境管理员、读取部门 WorkEvent 或内联权限推断；请求模型、request wrapper、Bot 用户 upsert/list、权限规则 payload、权限重算和环境管理员补充已迁入 `app/services/operations_bot_users.py`。
- 旧 operations 只读列表和状态：`/api/sync-runs`、`/api/extracted-items`、`/api/reports`、`/api/audit-logs` HTTP 入口已从旧 read route 拆到 `operations_sync_routes.py`、`operations_extracted_routes.py`、`operations_report_routes.py`、`operations_audit_routes.py`，旧 `operations_read_routes.py` 已删除；`/api/system/status` 已拆到 `operations_system_status_routes.py`，`/api/automation/status` 已拆到 `operations_automation_status_routes.py`，旧 `operations_status_routes.py` 已删除；payload 已迁入 `app/services/operations_read_models.py` 和 `app/services/operations_status.py`。旧路由不再直接拼这些 payload、Redis/Qdrant 健康检查或 extracted title 解析。
- 旧 operations dashboard/advisor/entities：`/api/dashboard/overview`、`/api/advisor/chat` 和 `/api/entities/{entity_type}` HTTP 入口已分别拆到 `operations_dashboard_routes.py`、`operations_advisor_routes.py` 与 `operations_entity_routes.py`；请求模型、request wrapper 和业务 payload 已迁入 `app/services/operations_dashboard.py`、`app/services/operations_advisor.py`、`app/services/operations_entities.py`。旧聚合 `operations.py` 当前只保留统一 prefix/依赖和子 router include，不再保留无运行价值的历史导出。
- 旧机器人兼容入口：不再保留无运行价值的私有 wrapper；当前 `commands.py` 只作为飞书消息入口，命令 handler 装配已迁入 `command_handlers.py`。
- 旧 V5 阶段性服务：拆分进稳定边界后删除，不保留临时命名。

## 5. 开发阶段

### Phase 0：基线审计和架构差距分析

目标：

- 跑通现有测试和 lint，确认真实质量基线。
- 生成当前模块到最终架构的映射表。
- 标出保留、迁移、删除、补齐四类清单。

交付：

- `ARCHITECTURE_AUDIT_V5.md`
- 当前测试结果。
- 模块迁移表。
- 风险清单。

验收：

```bash
cd digital-advisor
.venv312/bin/python -m pytest
.venv312/bin/ruff check app tests
```

### Phase 1：Data Layer 定型

目标：

- 先定数据模型，因为后续 Gateway、Agent、Tool 都依赖数据边界。
- 明确 Company、User、Role、Resource、ResourceSource、WorkEvent、MemoryFact、AuditLog 的最终职责。
- 完成双身份取数规则：App 身份优先，User 身份补充。
- 补齐权限字段和索引。

交付：

- 数据模型调整。
- Alembic 新迁移。
- 资源来源合并规则：已按新版 XMind 建立 `feishu_app_identity`、`feishu_user_identity`、`external_mail_account`、`personal_dingtalk_account`、`local_import` 身份型来源 taxonomy，并新增 `0013_data_layer_source_taxonomy.py` 回填旧 source_type；`external_web` 只作为 Knowledge Data 的外部 WEB 索引层，不再作为和飞书/邮箱/钉钉同级的数据来源展示。
- 数据类型规则：已按新版 XMind 顺序暴露 `operational_data`、`knowledge_data`、`memory_data`、`query_policies` 和资源级 `query_path`，Resource payload 会回显 `data_type` 和 `storage_layer`；Operational Data 按新版 XMind 明确为实时查询优先飞书 CLI，重要业务数据再进入 WorkEvent；Knowledge Data 已区分 L1 热知识本地 RAG、L2 冷知识只登记并使用飞书 CLI 实时查询、L3 外部知识实时搜索不入库，外部 WEB 归一为 `web` 资源并进入独立 `knowledge_external_web` 策略层，只作为外部引用、摘要和可信度来源，不作为本地向量库写入目标；Memory Data 归一为 `memory` 资源并进入 `long_term_memory` 策略层，落 `memory_facts`。L3 外部 Web 同步动作已明确为 `external_realtime_reference`；`sync_v5_resource()` 对该类资源在决策阶段跳过，不查询飞书 source、不调用飞书 API、不创建内部 WorkEvent。具体 Wiki 文档 token 已按 `wiki.spaces.get_node` 解析 `obj_token/obj_type` 后进入 L1 热知识正文同步；Wiki 空间仍按 L2 只登记索引。向量化边界已收紧为仅允许 `knowledge_hot + local_rag + rag_indexing.document_store=work_events + qdrant_vectors` 的 L1 热知识 WorkEvent 入队，L2 冷知识索引和 Operational Data WorkEvent 默认 `vector_status=skipped`。资源同步状态、同步执行结果、文档索引 WorkEvent payload 和 Owner 资源健康 payload 已回显 `query_path`，避免后台和驾驶舱只看到同步动作、看不到实时查询路径。
- L1 热文档同步可观测性：`sync_feishu_information()` 在正文写入 WorkEvent Document Store 后会返回 `document_store={store,work_event_ids,chunk_count,vector_db,rag_index,vector_status}`；`sync_v5_resource()` 会把该字段写入同步返回和 `ResourceSyncRun.summary`，`/api/v5/resources/sync-runs`、资源同步状态 latest_run、Owner 驾驶舱资源健康 payload 和管理后台同步表会显示 `document_store_summary`，便于上线前直接确认“正文已入本地 Document Store、切片已准备进入向量库”。L2 冷知识和 L3 外部知识不会返回该字段，避免误判为已本地化。
- 数据库约束：已新增 `0014_source_type_constraints.py`，并在模型层锁定 `ResourceSource.source_type` 与 `WorkEvent.source_type` 只能写入 V5 canonical taxonomy。
- 长期记忆边界：已新增 `0015_memory_fact_scope_constraints.py`，并在模型层锁定 `MemoryFact.scope=company/domain/personal/user/chat`；`memory_fact_access_condition()` 已收紧 company/chat/personal/domain 读取规则，防止跨员工读取个人记忆。
- 账号类型边界：已新增 `Account.account_type` 和 `0016_account_type_taxonomy.py`，锁定公司 App、个人飞书、飞书邮箱、外部邮箱、个人钉钉五类账号；`provider` 保留为 IMAP/Gmail/Graph/Feishu OAuth 等技术驱动字段，账号类型用于 Data Layer 权限和来源归类。
- 旧资源迁移边界：`FeishuResource` ORM 已删除；`resource_registry.migrate_legacy_feishu_resources()` 和 V5 administration bootstrap 仅通过 raw SQL 在旧表存在时读取迁移数据，业务同步/审批/资源发现模块不再各自直接查询旧模型；旧 `feishu_resources` 表被部署迁移删除时直接返回空迁移/0 迁移，不影响 V5 `Resource` 主路径。`0018_detach_resource_from_legacy_feishu_table` 已移除 `resources.legacy_feishu_resource_id -> feishu_resources.id` 外键，`legacy_feishu_resource_id` 只保留为审计追溯字段，V5 主资源表不再依赖旧表关系。
- 旧资源删除条件：`operations_resources.dashboard_resource_summary()` 已增加 `legacy_retirement`，上线前以 `unmapped_legacy_resources=0` 作为删除旧 `feishu_resources` 表的硬条件，未满足时只允许继续运行集中迁移兜底，不再恢复旧写路径；`0019_drop_retired_feishu_resources` 已提供最终 drop table 迁移，迁移会先检查所有旧行是否已映射到 V5 `resources.legacy_feishu_resource_id`，未清零时直接阻断，清零或空表才删除旧表。
- 权限过滤规则。
- 数据层测试。

验收：

```bash
cd digital-advisor
.venv312/bin/python -m pytest tests/test_security.py tests/test_work_events.py tests/test_v5*.py
```

### Phase 2：Message Gateway 完整落地

目标：

- 所有交互入口先进入 Gateway。
- 飞书事件、飞书 WebSocket、卡片动作、后台测试消息统一转成标准消息。
- Gateway 不做业务推理，只做消息、身份、上下文、响应通道。

交付：

- `app/services/gateway/message.py`
- `app/services/gateway/feishu.py`
- `app/services/gateway/commands.py`
- `app/services/gateway/responder.py`
- `app/services/gateway/audit.py`：已将 GatewayMessage 安全摘要写入 `AuditLog`，覆盖 handled、ignored、缺文本、缺回复目标、未处理命令等状态，不保存原始 payload 和消息正文。
- Celery 后台任务中的待审批异步回复、异常回复和日报推送已改走 `send_feishu_text_reply`，不再直连 `FeishuClient.send_message`。
- `app/services/feishu/approval_advice.py`：已拆出审批规则建议、文本建议、详细理由和附件摘要判断。
- `app/services/feishu/approval_cards.py`：已拆出审批互动卡片 payload、toast、回调 value/message_id 解析，以及默认卡片标题、建议和展开详情渲染；`commands.py` 不再保留卡片 value/message_id 等兼容 wrapper。
- `app/services/feishu/approval_card_responder.py`：已拆出审批互动卡片 callback response、详情展开/收起和按钮动作回复；文本回复发送和卡片内容更新由 `approval_card_entrypoint.py` 注入，responder 保持平台中立，不直接依赖 `FeishuClient`、Gateway 发送实现、Tool Router、ToolRequest/ToolContext、confirmation token 或原生 API 写方法；`approval_card_entrypoint.py` 已统一装配 WebSocket callback response、消息事件中的卡片动作依赖、审批卡片构建、互动卡片发送、审批确认后的 Tool Router 提交和审批动作审计；`handle_feishu_command` 只调用 entrypoint，不再直接依赖 responder，也不直接持有 ToolRequest/ToolContext/confirmation token 生成逻辑；旧卡片动作兼容 wrapper 已删除；`commands.py` 不再导出 `handle_feishu_card_action_response`，也不再保留 `_build_approval_action_card`/`_maybe_send_approval_action_card` 或直接调用 `send_feishu_interactive_reply`；普通文本回复的 `FeishuClient` 适配已移入 `app/services/feishu/replies.py`，`commands.py` 不再直接导入 `FeishuClient` 或 Gateway responder。
- `app/services/feishu/approval_actions.py`：已拆出审批动作准备、缺字段校验和待确认动作执行；卡片/命令 pending action 已加入内部同参 confirmation token 校验，approve/reject 真实提交与安全审计由 `approval_card_entrypoint.py` 进入 Tool Router。
- `app/services/feishu/approval_formatters.py`：已拆出审批项选择、待审批列表、审批详情行、附件读取结果、附件状态、审批命名、申请人/单号提取、字段优先级、表单摘要和字段显示值等纯 formatter。
- `app/services/feishu/approval_resources.py`：已拆出待审批资源注册、同步附件结果合并、历史相似审批匹配和审批附件结果 payload 序列化。
- `app/services/feishu/approval_runtime.py`：已拆出审批上下文优先读取、live 待审批拉取后的富化流水线、审批详情回复和审批建议回复编排。
- `app/services/feishu/approval_context.py`：已拆出审批上下文、待确认审批动作和内部确认 token 的 Redis 存取边界。
- `app/services/feishu/sync_commands.py`：已拆出飞书邮箱、审批、通讯录同步命令和 quick sync 参数编排。
- `app/services/feishu/command_handlers.py`：已拆出命令 handler 装配、日报事务适配、审批文本命令桥接、审批上下文读写和附件结果读取；`commands.py` 只保留 Gateway 消息入口、审计、分发和回复发送。
- `app/services/feishu/command_parser.py`：已拆出飞书机器人消息 chat/sender 提取、群聊 @ 判断、命令文本解析、别名归一化和审批上下文意图判断。
- `app/services/feishu/command_dispatcher.py`：已拆出 normalized command 到具体回复能力的主分发规则。
- `app/services/feishu/organization.py`：已拆出飞书通讯录管理人员查询、通讯录快照读取、组织架构 Markdown/XMind 大纲生成和组织架构回复编排。
- `app/services/feishu/identity.py`：已拆出机器人身份模型、发送者身份识别、管理员兜底、权限拒绝文案、身份回复和 Celery identity payload 序列化。
- `app/services/feishu/bot_runtime.py`：已拆出员工 Agent 问答调用、Bot 会话记录、route/scope 提取、驾驶舱回答生成与重写。
- `app/services/feishu/work_event_replies.py`：已拆出本地 WorkEvent/ExtractedItem 的审批历史兜底、近期邮件、开放待办、事件行格式化和安全错误摘要。
- `app/services/feishu/approval_enrichment.py`：已拆出审批附件摘要补读、审批 LLM 建议批量附加和单条建议生成依赖编排。
- 审批任务列表解析保留在 `app/services/feishu/approval.py::extract_approval_task_items`；用户待审批任务拉取和实例详情富化已统一走 Tool Router/MCP/CLI，不再保留独立 `approval_service_runtime.py` 兼容层。
- `app/services/v5_administration.py`：除 V5 foundation bootstrap 外，已承接 V5 OS overview、Administration 用户、公司设置、部门、团队、角色、权限、资源权限列表和 access preview；`v5.py` 不再直接拼这些后台列表的 SQL/payload 或公司计数。
- 旧 Feishu 入口迁移到 Gateway。
- 飞书事件字段以 `lark-cli event schema im.message.receive_v1` 和官方文档核对。

验收：

```bash
cd digital-advisor
.venv312/bin/python -m pytest tests/test_feishu.py tests/test_feishu_ws.py tests/test_agent_policies.py
```

### Phase 3：Tool Router 完整落地

目标：

- 工具层成为 Agent 调用所有业务能力的唯一入口。
- 所有工具必须声明能力、权限、输入输出、provider 策略、审计动作。
- 首批完整业务工具族：ApprovalTool、KnowledgeTool、BitableTool、ChatTool、CalendarTool、MeetingTool、ReportTool、AutomationTool、PeopleTool；Mail 归 ChatTool，Task 归 AutomationTool，日历/日程归 CalendarTool，会议/妙记/会议室/会议纪要归 MeetingTool，Docs/Wiki 归 KnowledgeTool；AutomationTool 覆盖提醒、自动化流程、自动条件触发和执行，PeopleTool 覆盖考勤、绩效和薪酬。

交付：

- `app/services/tools/base.py`：已完成工具协议、`ToolDefinition`、`ToolExecutionStatus` 和 XMind 9 个业务工具族枚举；原子工具继续保留，但必须挂到业务工具族，DevOps 诊断工具不归入业务工具族。
- `app/services/tools/router.py`：已完成工具注册表、provider 分派、权限闸口、执行审计和首批工具路由。
- `app/services/tools/providers/`：已建立 local、report、feishu_api、feishu_mcp、devops。
- `app/api/routes/v5_tool_routes.py` 已收敛为工具子路由聚合器；`app/api/routes/v5_tool_list_routes.py` 已收敛为工具列表子路由聚合器，工具列表和执行日志入口分别位于 `v5_tool_catalog_routes.py` 与 `v5_tool_execution_log_routes.py`，`app/api/routes/v5_tool_execution_routes.py` 承接后台工具执行入口，`app/api/routes/v5_tool_config_routes.py` 已收敛为配置子路由聚合器，单工具配置更新位于 `v5_tool_single_config_routes.py`，批量策略入口位于 `v5_tool_batch_config_routes.py`，请求模型已迁入 `app/api/routes/v5_tool_request_models.py`。`app/services/v5_tool_admin.py` 继续承接工具列表、执行日志、后台工具执行、单工具配置更新、批量工具策略和写目标摘要 payload；工具配置 payload 已回显 `business_tool` 与 `business_tools`，便于后台按 XMind 9 个业务工具族组织原子工具；`v5.py`、`v5_tool_routes.py`、`v5_tool_list_routes.py` 与 `v5_tool_config_routes.py` 不再直接拼 `ToolContext/ToolRequest`、访问 `TOOL_REGISTRY`、组装工具执行审计 payload 或持有工具后台 endpoint。
- `app/api/routes/v5_agent_routes.py` 已收敛为 Agent 管理子路由聚合器；`app/api/routes/v5_agent_trace_routes.py` 已收敛为 Agent trace 子路由聚合器，trace preview 和 trace 日志列表分别位于 `v5_agent_trace_preview_routes.py` 与 `v5_agent_trace_list_routes.py`；`app/api/routes/v5_agent_settings_routes.py` 已收敛为 Agent 设置子路由聚合器，Agent 设置读取和保存分别位于 `v5_agent_settings_read_routes.py` 与 `v5_agent_settings_write_routes.py`，请求模型已迁入 `app/api/routes/v5_agent_request_models.py`。`app/services/v5_agent_admin.py` 继续承接 Agent trace preview、trace 日志列表、Agent 设置读取和保存，以及 trace payload/write policy summary；`v5.py`、`v5_agent_routes.py`、`v5_agent_trace_routes.py` 与 `v5_agent_settings_routes.py` 不再直接调用 Agent Runtime、CompanySetting helper 或持有 Agent 管理 endpoint。
- `app/api/routes/v5_system_log_routes.py` 已收敛为系统日志子路由聚合器；`/api/v5/system/logs/overview` 位于 `v5_system_log_overview_routes.py`，`/api/v5/system/logs` 位于 `v5_system_log_list_routes.py`。`app/services/v5_system_logs.py` 继续承接系统日志查询、payload 过滤和 limit 边界；`v5.py` 与 `v5_system_log_routes.py` 不再直接查询 `AuditLog`、组装系统日志过滤逻辑或持有系统日志 endpoint。
- `app/api/routes/v5_administration_routes.py` 已收敛为 Administration 子路由聚合器；`app/api/routes/v5_administration_foundation_routes.py` 已收敛为 foundation 子路由聚合器，`/api/v5/bootstrap/foundation` 和 `/api/v5/os/overview` 分别位于 `v5_administration_bootstrap_routes.py` 与 `v5_administration_os_overview_routes.py`；`app/api/routes/v5_administration_list_routes.py` 已收敛为列表子路由聚合器，`v5_administration_user_setting_routes.py`、`v5_administration_org_routes.py` 和 `v5_administration_permission_routes.py` 均已继续收敛为子路由聚合器，用户、公司设置、部门、团队、角色、权限和资源权限分别位于对应单 endpoint 子路由；`app/api/routes/v5_administration_access_routes.py` 承接 access preview，请求模型已迁入 `app/api/routes/v5_administration_request_models.py`；`app/services/v5_administration.py` 继续承接 V5 foundation bootstrap、OS overview、Administration 用户、公司设置、部门、团队、角色、权限、资源权限和 access preview payload；`v5.py`、`v5_administration_routes.py`、`v5_administration_foundation_routes.py`、`v5_administration_list_routes.py`、`v5_administration_user_setting_routes.py`、`v5_administration_org_routes.py` 与 `v5_administration_permission_routes.py` 不再持有 Administration endpoint。架构测试已锁定所有 `app/api/routes/v5*.py` 文件最多只持有 1 个 endpoint，所有含 `router.include_router(...)` 的 V5 聚合器不得 import service、注入 `get_db`、访问 DB 或返回业务 payload。
- `app/api/routes/v5_resource_routes.py` 已收敛为资源子路由聚合器；`app/api/routes/v5_resource_status_routes.py` 已收敛为资源状态子路由聚合器，`v5_resource_overview_routes.py` 已收敛为 overview 子路由聚合器，资源列表、公司概览和同步策略概览分别位于 `v5_resource_list_routes.py`、`v5_resource_company_overview_routes.py` 和 `v5_resource_sync_strategy_routes.py`，`v5_resource_sync_status_routes.py` 已收敛为同步状态子路由聚合器，同步状态、同步记录和监控分别位于 `v5_resource_sync_status_list_routes.py`、`v5_resource_sync_run_routes.py` 和 `v5_resource_monitoring_routes.py`，workspace events 位于 `v5_workspace_event_routes.py`；`v5_resource_policy_routes.py` 已收敛为同步策略子路由聚合器，同步策略读取和保存入口分别位于 `v5_resource_policy_read_routes.py` 与 `v5_resource_policy_write_routes.py`；`app/api/routes/v5_resource_sync_routes.py` 已收敛为批量同步子路由聚合器，批量同步预览位于 `v5_resource_batch_preview_routes.py`，批量同步执行位于 `v5_resource_batch_execute_routes.py`；`app/api/routes/v5_resource_workspace_routes.py` 已收敛为 workspace 子路由聚合器，`v5_resource_single_sync_routes.py` 已继续收敛为单资源同步子路由聚合器，普通单资源同步位于 `v5_resource_direct_sync_routes.py`，access block retry 位于 `v5_resource_retry_routes.py`；`v5_resource_access_routes.py` 已收敛为访问动作子路由聚合器，access block 清理和访问决策分别位于 `v5_resource_clear_access_block_routes.py` 与 `v5_resource_access_decision_routes.py`；请求模型已迁入 `app/api/routes/v5_resource_request_models.py`。`app/services/v5_resource_status.py`、`app/services/v5_auto_sync.py`、`app/services/v5_workspace.py`、`app/services/v5_sync_policy.py` 继续承接对应 payload 和编排；`v5.py`、`v5_resource_routes.py`、`v5_resource_status_routes.py`、`v5_resource_overview_routes.py`、`v5_resource_sync_status_routes.py`、`v5_resource_policy_routes.py`、`v5_resource_sync_routes.py`、`v5_resource_workspace_routes.py`、`v5_resource_single_sync_routes.py` 与 `v5_resource_access_routes.py` 不再直接 import Data Layer ORM 模型、拼资源 SQL、构造 RAG/document store 摘要、选择批量同步资源或持有资源管理 endpoint。
- `app/api/routes/v5_intelligence_routes.py` 已收敛为 Intelligence 子路由聚合器，风险噪声关闭、低信号通知关闭和开放业务事项富化入口分别位于 `v5_intelligence_risk_noise_routes.py`、`v5_intelligence_low_signal_routes.py` 和 `v5_intelligence_business_item_routes.py`；`app/services/v5_intelligence_admin.py` 承接对应业务 payload；`v5.py` 和 `v5_intelligence_routes.py` 不再直接调用底层 AI 清理/富化 service、提交事务或持有 intelligence 后台入口。
- `app/api/routes/v5.py`：已收敛为 V5 admin 子路由聚合器，只保留统一 `/api/v5` prefix、admin 依赖和 `v5_*_routes.py` 子 router include，不再直接持有请求模型、数据库依赖 endpoint 或业务 service import。
- `app/services/v5_sync_policy.py`：已承接资源同步策略读取/保存 payload；`v5.py` 不再直接拼 `company_id/policy` 响应或调用底层策略更新函数。
- Feishu API provider、Feishu MCP provider 和 CLI executor：已建立经 `lark-cli` 和官方文档约束的边界；`feishu_api` provider 仅保留同步层、资源发现、管理端同步预览和受控验证语义，实时业务工具默认 `feishu_mcp` provider，并在 provider boundary 中明确 `registered_api_write_capability` 只代表 API 能力已登记，不代表实时写可执行；`supports_write=false`、`supports_confirmed_realtime_write=false`、`confirmed_write_policy=blocked_realtime_use_tool_router_mcp_cli`、`preferred_execution_engine=lark_cli`、`realtime_policy=tool_mcp_cli_only`、`realtime_bridge=feishu_mcp`、`mcp_provider=feishu_mcp`、`execution_chain=["agent_runtime","tool_router","tool","mcp","cli","feishu"]`、`api_role=sync_engine_only`、`agent_runtime_direct_access=false` 和 `responsibility_boundary={"tool_router":"business_capability","mcp":"tool_scheduling","cli":"action_execution","api":"sync_engine_data_sync"}`。`feishu_mcp.py` 只负责工具到 CLI 命令的调度映射，真实 `subprocess.run` 执行动作已收敛到 `app/services/tools/providers/lark_cli.py`，防止 MCP 和 CLI 职责混在同一模块。Calendar、Task、Mail、Bitable、Approval instance/task、OKR cycle/objective、Contact scope/department/user/snapshot、Bitable 字段和视图结构等实时读能力默认由 Feishu MCP provider 调度 CLI；`calendar_qa`、`mail_qa`、`task_qa` 和 `bitable_qa` 默认 provider 已切到 Feishu MCP provider 并经 CLI 实时查询，显式工具配置仍可切回 local 读取已同步缓存；pending approval 语义意图会路由到 `feishu_approval_task_query` 实时查询飞书审批任务，审批建议和历史分析继续走 `approval_qa` 本地历史。Tool Router 入口会剥离 Agent 上游传入的 `client/app_config` runtime-only 参数，且已显式拒绝 `feishu_api` provider 作为实时业务工具执行入口；Feishu API provider 对 confirmed 写操作会在校验 confirmation token 后直接拒绝，真实写入必须走 Tool Router -> MCP -> CLI。Contact 授权范围、子部门、部门直属用户和组织快照已按 `lark-contact` skill、`lark-openapi-explorer`、飞书官方通讯录文档和 `lark-cli api GET ... --dry-run --as bot` 验证后由 MCP provider 调度 `lark-cli api GET` 执行，仍仅开放只读并走 `contact:read` 权限；审批 approve/reject/transfer/remind/add_sign/rollback/cancel/cc、IM 文本消息发送、IM 建群、IM 公开群自动加入、日程创建、任务创建/更新/提醒更新/负责人分配/关注人维护/完成/重新打开/评论/附件上传和多维表格记录创建/批量创建/更新/批量更新/删除/记录附件上传/移除/建表/建字段/字段更新/视图创建/视图重命名/视图筛选/视图排序/视图分组/视图可见字段/视图卡片/视图时间轴配置已接入真实写执行，但必须先 dry-run，dry-run 文案会显示实时执行链路、CLI 命令和写目标摘要，任务/日程/多维表格建表会展示标题，并在真实执行时带回同参 `confirmation_token` 与 `confirmed=true`；旧飞书审批管理提交接口、旧机器人文本发送管理接口、旧建群接口、旧公开群自动加入真实执行和机器人确认审批真实提交已改走 `execute_agent_tool`，旧 `approval_actions` 直接提交 service 的函数已删除，旧 `/bot/send` 不再直连 IM client。
- 四段职责契约已显式进入 provider boundary 和架构测试：Tool Router 只负责业务能力与 provider 分发；MCP 只负责工具调度，`role=tool_scheduling`、`performs_action_execution=false`、`action_executor=lark_cli`；CLI 只负责执行动作，`role=action_execution`、`allowed_caller_module=app.services.tools.providers.feishu_mcp`、`accepts_business_capability=false`、`schedules_tools=false`，并在运行时拒绝非 MCP provider 直接调用；API 只负责数据同步、资源发现、管理端同步预览和受控验证，`role=sync_engine_data_sync`、`allowed_entrypoints=["sync_engine","resource_discovery","admin_sync_preview"]`、`realtime_read_allowed=false`、`realtime_write_allowed=false`、`mcp_access_allowed=false`、`controlled_validation_allowed=true`。Feishu API provider 自身也会校验 `api_entrypoint`，缺少明确同步/资源发现/管理预览/受控验证入口时拒绝直接执行。
- 管理后台实时读路由已按同一职责边界迁移：`app/api/routes/feishu_admin_read_tool_routes.py` 已收敛为子路由聚合器，`/approvals/pending` 位于 `feishu_admin_read_tool_approval_routes.py`，`feishu_admin_read_tool_contact_routes.py` 已收敛为通讯录子路由聚合器，部门、用户和 snapshot 入口分别位于 `feishu_admin_read_tool_contact_department_routes.py`、`feishu_admin_read_tool_contact_user_routes.py` 和 `feishu_admin_read_tool_contact_snapshot_routes.py`，`/calendar/events` 位于 `feishu_admin_read_tool_calendar_routes.py`，IM 群搜索/消息列表位于 `feishu_admin_read_tool_im_routes.py` 及其 chat/message 子路由，`/documents/content` 位于 `feishu_admin_read_tool_knowledge_routes.py`，Drive 文件列表位于 `feishu_admin_read_tool_drive_routes.py`，Wiki 空间/节点位于 `feishu_admin_read_tool_wiki_routes.py` 及其 space/node 子路由，`feishu_admin_read_tool_bitable_routes.py` 已收敛为 Bitable 子路由聚合器，`/bitable/tables` 和 `/bitable/records` 分别位于 `feishu_admin_read_tool_bitable_table_routes.py` 与 `feishu_admin_read_tool_bitable_record_routes.py`，`/tasks/list` 位于 `feishu_admin_read_tool_task_routes.py`，历史会议列表位于 `feishu_admin_read_tool_meeting_routes.py`，`feishu_admin_read_tool_mail_routes.py` 已收敛为 Mail 实时读子路由聚合器，邮件列表、邮箱文件夹和邮箱详情分别位于 `feishu_admin_read_tool_mail_message_list_routes.py`、`feishu_admin_read_tool_mail_folder_routes.py` 与 `feishu_admin_read_tool_mail_message_detail_routes.py`，请求模型位于 `feishu_admin_read_tool_request_models.py`。这些入口不直接调用对应 Feishu service/API client，而是通过 `app/services/feishu_admin_read_tools.py::execute_admin_feishu_read_tool -> execute_agent_tool -> Feishu MCP provider -> lark-cli` 获取 `response_format=raw_json` 的结构化结果；审批 pending 后台入口会先调用 `feishu_approval_task_query`，再按 `process_code/instance_code` 调用 `feishu_approval_instance_get` 补实例详情。Bitable 后台读按 CLI 能力使用 `offset/limit` 分页，旧 `page_token` 只接受数字 offset，不静默伪兼容 API 的 opaque page token。IM 群搜索/消息列表已按 `lark-im`/`lark-shared`/`lark-cli im +chat-search/+chat-messages-list` 验证后接入 `feishu_im_chat_search` 与 `feishu_im_message_list`，归入 ChatTool；Wiki 空间/节点已按 `lark-wiki`/`lark-shared`/`lark-cli wiki +space-list/+node-list` 验证后接入 `feishu_wiki_space_list` 与 `feishu_wiki_node_list`，归入 KnowledgeTool；Drive 文件列表已按 `lark-drive`/`lark-shared`/`lark-cli drive files list` 和 schema 验证后接入 `feishu_drive_file_list`，归入 KnowledgeTool；邮箱文件夹和邮箱详情已按 `lark-mail`/`lark-shared`/`lark-cli mail user_mailbox.folders list`/`lark-cli mail +message` 验证后接入 Tool Router -> MCP -> CLI，后台对应入口调用 `feishu_mail_folder_list` 与 `feishu_mail_message_get`；历史会议搜索已按 `lark-vc`/`lark-shared`/`lark-cli vc +search` 验证后接入 `feishu_vc_meeting_search`，归入 MeetingTool。
- 管理后台写工具入口已拆出 route：`app/api/routes/feishu_admin_write_routes.py` 已收敛为子路由聚合器，`feishu_admin_write_im_routes.py` 已收敛为 IM 写子路由聚合器，机器人发送消息、飞书建群和公开群加入入口分别位于 `feishu_admin_write_im_message_routes.py`、`feishu_admin_write_im_chat_routes.py` 和 `feishu_admin_write_im_auto_join_routes.py`，审批动作入口位于 `feishu_admin_write_approval_routes.py`，请求模型位于 `feishu_admin_write_request_models.py`；`ToolRequest` 构造、文本参数校验、用户/机器人 ID 去重、公开群 dry-run 预览和写审计已迁入 `app/services/feishu_admin_write_tools.py`。真实执行仍经 `execute_agent_tool -> Tool Router -> MCP -> CLI`，该 service 不直接调用 CLI/API；旧 route 层 `_auto_join_public_chats/_unique_strings` 兼容别名已删除。
- OAuth callback 已拆出 route：HTTP 入口已迁入 `app/api/routes/feishu_oauth_routes.py`，`app/services/feishu_oauth_helpers.py` 承接 state 解析、app_config 查询、用户 token exchange、错误文案提取和 callback HTML page 生成；`feishu.py` 只 include OAuth 子 router。
- 管理后台同步入库入口已拆为子路由：`app/api/routes/feishu_admin_sync_routes.py` 已收敛为聚合器，`/messages/sync` 位于 `feishu_admin_sync_message_routes.py`，`/information/sync` 位于 `feishu_admin_sync_information_routes.py`，`/resources/discover` 位于 `feishu_admin_sync_resource_routes.py`，请求模型位于 `feishu_admin_sync_request_models.py`；时间窗口校验、`FeishuClient.list_messages` 分页、`ingest_feishu_message` 写入、可选抽取触发、`sync_feishu_information` 参数映射、同步发现、异步 Celery discovery task、资源发现审计均留在 `app/services/feishu_admin_sync.py`。这些路径属于 Sync/API Client 入库、resource discovery 或 admin sync preview，不进入 MCP，也不作为 Agent Runtime 实时问答链路。
- 管理后台 App/OAuth/UserAccount 入口已拆为子路由：`app/api/routes/feishu_admin_app_routes.py` 已收敛为聚合器，`feishu_admin_app_config_routes.py`、`feishu_admin_app_oauth_routes.py` 和 `feishu_admin_app_user_account_routes.py` 也已继续收敛为聚合器，App 创建、tenant access token refresh、client routing、OAuth URL、OAuth token exchange、用户账号列表和 refresh 分别位于单 endpoint 子路由，请求模型位于 `feishu_admin_app_request_models.py`；业务逻辑留在 `app/services/feishu_admin_apps.py`，`feishu.py` 只 include 该聚合 router，不再直接持有这组入口，OAuth callback 业务控制流已单独迁出。
- 管理后台 sync-plan、capability probe 和审批资源列表已拆出 route：`app/api/routes/feishu_admin_capability_routes.py` 已收敛为聚合器，sync-plan、capability probe 和审批资源列表分别位于 `feishu_admin_capability_sync_plan_routes.py`、`feishu_admin_capability_probe_routes.py` 和 `feishu_admin_capability_approval_resource_routes.py`，`app/services/feishu_admin_capabilities.py` 承接 `get_feishu_sync_plan` payload、`probe_feishu_capabilities` 审计和 `FeishuApprovalService.list_approval_resources` 响应组装；`feishu.py` 不再直接持有这些后台辅助入口或拼 payload。
- 管理后台 IM 写入口和审批动作写入口继续收敛：`/bot/send`、`/chats/create`、`/chats/public/auto-join` 的 HTTP 入口已分别迁入 `feishu_admin_write_im_message_routes.py`、`feishu_admin_write_im_chat_routes.py` 和 `feishu_admin_write_im_auto_join_routes.py`，`feishu_admin_write_im_routes.py` 只 include 三个 IM 子路由，`/approvals/action` 已迁入 `feishu_admin_write_approval_routes.py`；Tool Router 调用、`ToolRequest` 组装、dry-run/confirmed 结果 payload 和写审计已迁入 `app/services/feishu_admin_write_tools.py`；route 不再持有 IM/审批动作工具名、confirmation_token 或 write_target 审计细节。
- 管理后台审批 pending 审计已拆出 route：`/approvals/pending` 只调用 `app/services/feishu_admin_read_tools.py::pending_approval_tasks_payload`，待审批任务实时读取、实例详情补全和 audit payload 均留在 read service。
- 管理后台通讯录 snapshot 审计已拆出 route：`/contacts/snapshot` 位于 `feishu_admin_read_tool_contact_snapshot_routes.py`，只调用 `app/services/feishu_admin_read_tools.py::contact_snapshot_payload`，实时读取、组织快照 payload 和审计提交均留在 read service。
- 管理后台 API client 读入口已继续收敛：`app/api/routes/feishu_admin_api_read_routes.py` 已收敛为子路由聚合器，只 include `feishu_admin_api_read_mail_routes.py`；`feishu_admin_api_read_mail_routes.py` 只保留邮箱 accessible-mailboxes 资源发现辅助入口，位于 `feishu_admin_api_read_mail_access_routes.py`。IM、Meeting、Wiki、Drive、邮箱文件夹和邮箱详情等 CLI 已具备能力的后台实时读取入口已迁到 Tool Router -> MCP -> CLI；API Client 保留给同步、入库、资源发现和管理端同步预览，不作为 Agent Runtime 实时问答链路。
- 飞书事件入口已拆出 route：HTTP 入口已迁入 `app/api/routes/feishu_event_routes.py`，`/api/feishu/events/{app_config_id}` 只负责取得 app_config 并调用 `app/services/feishu_event_entrypoint.py::receive_feishu_event_payload`；token 校验、URL verification challenge、事件 WorkEvent 入库和命令分发均在 service 内。
- Data Layer 调试/维护入口已拆出 route：`app/api/routes/work_events.py` 已收敛为 `/api` prefix、admin 依赖和子 router include；`work_events_collection_routes.py`、`work_events_processing_routes.py` 和 `work_events_analysis_routes.py` 已继续收敛为聚合器，WorkEvent 创建、列表、详情、抽取、单条向量化、日报、向量搜索和 pending 队列分别位于单 endpoint 子路由，请求模型已迁入 `app/api/routes/work_events_request_models.py`；业务 payload 统一由 `app/services/work_events_admin.py` 承接，route 不再直接查询 WorkEvent/ExtractedItem/Attachment 或调用向量/日报底层 service。
- 公司/账号管理入口已拆出 route：`app/api/routes/companies.py` 已收敛为子路由聚合器，`companies_company_routes.py` 和 `companies_account_routes.py` 也已继续收敛为聚合器，`/companies` 创建/列表、`/accounts` 创建和 `/companies/{company_id}/accounts` 列表分别位于单 endpoint 子路由，`/onboarding/company-setup` 已迁入 `companies_onboarding_routes.py`，quick setup 请求模型已迁入 `companies_request_models.py`；创建、列表、审计、账号类型推断、外部邮箱资源登记、飞书应用绑定、Bot 管理员授权和 V5 邮箱资源登记均由 `app/services/companies_admin.py` 承接。旧 route 层 `_upsert_*` 兼容别名已删除，quick setup mail resource payload 不再输出 `legacy_*` 字段。
- 外部邮箱管理入口已拆出 route：`app/api/routes/mail.py` 已收敛为子路由聚合器，`/api/mail/imap/sync` 已迁入 `mail_imap_routes.py`，`mail_oauth_routes.py` 已继续收敛为聚合器，Gmail/Graph OAuth URL 分别位于 `mail_oauth_gmail_routes.py` 与 `mail_oauth_graph_routes.py`，Graph message payload 已迁入 `mail_graph_routes.py`，IMAP 请求模型已迁入 `mail_request_models.py`；SyncRun 创建/完成、IMAP 同步调用、OAuth URL 和 Graph message payload 统一由 `app/services/mail_admin.py` 承接。
- Owner 驾驶舱入口已拆出 route：`app/api/routes/cockpit.py` 已收敛为子路由聚合器，`/api/cockpit/overview` 已迁入 `cockpit_overview_routes.py`，`/api/cockpit/modules/{module_key}` 已迁入 `cockpit_module_routes.py`，查询参数类型已迁入 `cockpit_query_params.py`；scope 构造、overview/module payload 和 unknown module 的 404 转换统一由 `app/services/cockpit_admin.py` 承接，底层经营模块仍保留在 `app/services/cockpit/*`。
- 旧审批实时 API helper 已退役：`app/services/feishu/approval_service_runtime.py` 删除，后台和审批卡片不再通过该模块调用 `FeishuApprovalService.fetch_user_pending_tasks/enrich_tasks_with_instance_details`；审批任务解析直接复用 `extract_approval_task_items`，实时任务和实例详情统一走 Tool Router/MCP/CLI。
- ToolConfig provider 迁移：新增 `0017_tool_configs_feishu_mcp_defaults`，上线时将历史飞书实时工具的 `provider='feishu_api'` 改为 `provider='feishu_mcp'`，并由架构测试校验迁移工具名与当前 Feishu capability 表一致，避免旧配置与新职责边界冲突。
- 审批卡片实时待办查询：`recent_feishu_approvals_reply` 保留审批卡片和 pending action 上下文能力，但 `_fetch_feishu_pending_approval_tasks` 已不再直接调用 `FeishuApprovalService.fetch_pending_tasks`；它通过 Tool Router 执行 `feishu_approval_task_query`，MCP provider 调度 `lark-cli approval tasks query --format json` 并在 `response_format=raw_json` 时返回结构化 CLI payload，再由 entrypoint 转为卡片 item。待办 item 的实例详情已继续通过 Tool Router 执行 `feishu_approval_instance_get`，MCP provider 调度 `lark-cli approval instances get --format json` 并用 raw JSON 填回 `instance_detail`，避免审批卡片详情回退到 direct API service。附件下载/摘要富化也已改走 Tool Router：新增 MCP-only `feishu_approval_attachment_download`，由 `lark-cli drive +download --file-token --output ...` 下载到临时文件，entrypoint 本地提取文本摘要后删除临时文件；同步层仍可保留 `FeishuApprovalAttachmentService` 作为 API Client 入库路径。
- 审批 approve/reject/transfer/remind/add_sign/rollback/cancel/cc 写执行已按 `lark-cli approval tasks approve/reject/transfer/remind/add_sign/rollback --dry-run --as user`、`lark-cli approval instances cancel/cc --dry-run --as user` 和 schema 重新校准：同意/拒绝使用 `/open-apis/approval/v4/tasks/pass|refuse`，body 只包含 `instance_code`、`task_id`、`comment/form`；转交使用 `/open-apis/approval/v4/tasks/forward`，body 包含 `instance_code`、`task_id`、`transfer_user_id` 和可选 `comment`；催办使用 `/open-apis/approval/v4/instances/remind`，body 包含 `instance_code`、`task_ids` 和可选 `comment`；加签使用 `/open-apis/approval/v4/tasks/add_sign`，body 包含 `instance_code`、`task_id`、`add_sign_user_ids`、`add_sign_type`、可选 `approval_method/comment`；退回使用 `/open-apis/approval/v4/tasks/rollback`，body 包含 `instance_code`、`task_id`、`node_ids` 和可选 `comment`；审批实例撤回使用 `/open-apis/approval/v4/instances/recall`，body 只包含 `instance_code`；审批实例抄送使用 `/open-apis/approval/v4/instances/add_cc`，body 包含 `instance_code`、`cc_user_ids` 和可选 `comment`；真实提交必须绑定个人飞书 `user_access_token`。
- Task 清单、清单成员、父子任务、子任务创建、附件上传和提醒更新写能力已按 CLI 证据接入：`feishu_tasklist_create` 使用 `task.tasklists.create` schema 和 `lark-cli task +tasklist-create --dry-run` 证明的 `POST /open-apis/task/v2/tasklists`；`feishu_task_add_to_tasklist` 使用 `lark-cli task +tasklist-task-add --dry-run` 证明的 `POST /open-apis/task/v2/tasks/:task_guid/add_tasklist`；`feishu_task_set_ancestor` 使用 `lark-cli task +set-ancestor --dry-run` 证明的 `POST /open-apis/task/v2/tasks/:task_guid/set_ancestor_task` 与 `ancestor_guid` 请求体；`feishu_task_clear_ancestor` 使用 `lark-cli task +set-ancestor --task-id ... --dry-run --as user` 且不传 `--ancestor-id` 证明的同一路径空 body 接入清空父任务关系；`feishu_task_subtask_create` 使用 `task.subtasks.create` schema 和 `lark-cli task subtasks create --dry-run` 证明的 `POST /open-apis/task/v2/tasks/:parent_task_guid/subtasks`；`feishu_task_upload_attachment` 使用 `lark-cli task +upload-attachment --dry-run --as user` 证明的 `/open-apis/task/v2/attachments/upload` 接入 MCP 实时桥，multipart 执行由 CLI 承担；`feishu_tasklist_update_members` 使用 `task.tasklists.add_members/remove_members` schema 和 `lark-cli task +tasklist-members --dry-run` 证明的 `POST /open-apis/task/v2/tasklists/:tasklist_guid/add_members|remove_members`；`feishu_tasklist_set_members` 使用 `lark-cli task +tasklist-members --set ... --dry-run --as user` 证明的 GET 清单详情流程接入全量替换，系统内先读取当前成员再按差异调用 add/remove；`feishu_task_update_reminders` 使用 `task.tasks.patch` schema 和 `lark-cli task tasks patch --dry-run` 证明的 `PATCH /open-apis/task/v2/tasks/:task_guid` 与 `task.positive_reminders` 请求体。
- 业务工具中 Approval、Bitable、Chat、Company、Domain、Knowledge、Personal、Calendar、Meeting、Mail、Task、OKR、Contact、General conversation 已迁入正式 `tools/*` 模块；Mail 原子工具归 ChatTool，Task 原子工具归 AutomationTool，日历/日程原子工具归 CalendarTool，历史会议搜索已归 MeetingTool，Docs/Wiki/Drive 文档搜索原子工具归 KnowledgeTool；`company_qa` 和 `owner_cockpit` 已归入 Report provider；OKR 周期/目标只读工具、Contact 授权范围/组织/成员只读工具、审批 approve/reject/transfer/remind、IM 文本消息发送、IM 建群、IM 公开群自动加入、日程创建、任务创建/更新/子任务创建/负责人分配/关注人维护/完成/重新打开/评论/附件上传/设置父任务/清单成员维护与多维表格记录创建/批量创建/更新/批量更新/删除/记录附件上传/移除/建表/建字段/字段更新已建立并执行 dry-run/二次确认边界。Bitable 批量创建记录按飞书官方“新增多条记录”能力和 `lark-cli base +record-batch-create --dry-run` 验证后接入，系统内采用 CLI 已验证的 `fields + rows` 输入形态；Bitable 批量更新记录按飞书官方“更新多条记录”能力和 `lark-cli base +record-batch-update --dry-run` 验证后接入，系统内采用 CLI 已验证的 `record_id_list + patch` 输入形态；Bitable 记录附件上传按 `lark-cli base +record-upload-attachment --dry-run --as user` 验证后接入，真实文件上传和附件追加编排由 CLI 承担；Bitable 记录附件移除按 `lark-cli base +record-remove-attachment --dry-run --as user` 验证后接入，真实移除动作由 Tool Router 确认后经 MCP 调度 CLI 执行，写审计只记录 `file_token_count` 不保存 token 原文；Bitable 建表/建字段按 `lark-cli base +table-create --dry-run`、`lark-cli base +field-create --dry-run` 和 `lark-base` skill 边界验证后接入 `base/v3` 表结构 API，Bitable 字段更新按 `lark-cli base +field-update --dry-run --as user` 接入 full PUT 字段更新；系统内仅承接明确字段 JSON，不把未验证的公式、lookup、角色权限复杂能力标为已完成；Bitable 记录写入支持 `validate_fields=true`，会先读取真实字段结构并拦截不存在字段、公式/lookup/附件/系统字段等明显只读字段；Task 负责人分配按飞书官方任务成员能力索引和 `lark-cli task +assign --dry-run` 验证后接入，系统内仅处理 assignee 的 add/remove；Task 关注人维护按飞书官方任务成员能力索引和 `lark-cli task +followers --dry-run` 验证后接入，系统内仅处理 follower 的 add/remove；Task 父子任务关系按 `lark-cli task +set-ancestor --dry-run` 验证后接入，系统内支持设置和清空父任务；Task 子任务创建按 `lark-cli task subtasks create --dry-run` 和 schema 验证后接入；Task 附件上传按 `lark-cli task +upload-attachment --dry-run --as user` 验证后接入；Task 清单成员按 `lark-cli task +tasklist-members --dry-run`、`--set` dry-run 和 add/remove schema 验证后接入，系统内支持成员 add/remove 和读取当前成员后差异化全量替换；Contact 授权范围、子部门、部门直属用户和组织快照按官方通讯录文档、`lark-contact` 限定和 `lark-cli api ... --dry-run` 验证后接入只读；Bitable 删除记录按飞书官方文档和 `lark-cli api DELETE ... --dry-run` 验证后接入；Task 重新打开按 `lark-cli task +reopen --dry-run` 验证后接入；OKR 周期和目标列表按 `lark-cli okr +cycle-list/+cycle-detail --dry-run --as user`、schema 和 `lark-okr` skill 验证后接入只读；飞书建群按 `lark-cli im +chat-create --dry-run` 和 `lark-cli api POST /open-apis/im/v1/chats --dry-run` 验证后接入；公开群加入按 `lark-cli api POST /open-apis/im/v1/chats/:chat_id/members/me_join --dry-run` 验证后接入；审批转交按 `lark-cli approval tasks transfer --dry-run` 和 schema 验证后接入；审批催办按 `lark-cli approval tasks remind --dry-run` 和 schema 验证后接入。测试已覆盖所有 Feishu API 写能力未经 `dry_run` 或 `confirmed=true + confirmation_token` 时拒绝执行，并覆盖 Feishu API provider 即使 token 正确也不得执行 confirmed 实时写，并覆盖旧审批管理提交接口、旧机器人文本发送管理接口、旧建群接口、旧公开群自动加入真实执行和机器人确认审批真实提交必须通过 Tool Router；普通成员对 Approval、Bitable、Task 等写工具以及 OKR/Contact/Meeting 公司级只读工具的权限拒绝；`test_feishu_api_capabilities_have_complete_router_mcp_and_runtime_bindings` 已从 capability 派生校验 Tool Registry、MCP realtime/只读绑定、API runtime 读写绑定完整性，`test_all_feishu_write_capabilities_have_auditable_target_operations` 已从 capability 派生校验所有写工具都有可审计 `write_target.operation` 且不记录 confirmation token。
- Bitable 批量删除记录已按 `lark-cli base +record-delete --dry-run`、`lark-base` skill 和飞书官方“删除多条记录”文档验证后接入，系统内使用 `record_id_list` 调用 `POST /open-apis/base/v3/bases/:base_token/tables/:table_id/records/batch_delete`，并纳入 Feishu API capability、Tool Registry、runtime 写绑定、权限拒绝和写目标审计一致性测试。
- Bitable `record-upsert` 已按 `lark-cli base +record-upsert --help`、`lark-cli base +record-upsert --dry-run --as user` 和 `lark-base` skill 验证后接入，系统内保持 CLI 语义：无 `record_id` 创建记录，有 `record_id` 按该记录更新，不做业务键自动去重；runtime 分别调用 `POST /open-apis/base/v3/bases/:base_token/tables/:table_id/records` 与 `PATCH /open-apis/base/v3/bases/:base_token/tables/:table_id/records/:record_id`，并纳入 Feishu API capability、Tool Registry、runtime 写绑定、权限拒绝和 `upsert_mode` 写目标审计一致性测试。
- Bitable 表重命名已按 `lark-cli base +table-update --dry-run --as user` 和 `lark-base` skill 验证后接入，runtime 调用 `PATCH /open-apis/base/v3/bases/:base_token/tables/:table_id` 并只提交 `name`，已纳入 Feishu API capability、Tool Registry、runtime 写绑定、权限拒绝和写目标审计一致性测试。
- Bitable 字段更新已按 `lark-cli base +field-update --help`、`lark-cli base +field-update --dry-run --as user` 和 `lark-base` skill 验证后接入，runtime 调用 `PUT /open-apis/base/v3/bases/:base_token/tables/:table_id/fields/:field_id`；系统内按 full PUT 风险要求提交完整 `field.name + field.type`，并暂时拒绝 formula/lookup 字段更新，已纳入 Feishu API capability、Tool Registry、runtime 写绑定、权限拒绝和写目标审计一致性测试。
- Bitable 视图创建、重命名、删除、筛选、排序、分组、可见字段、卡片和时间轴配置已按 `lark-cli base +view-create/+view-rename/+view-delete/+view-set-filter/+view-set-sort/+view-set-group/+view-get-visible-fields/+view-set-visible-fields/+view-get-card/+view-set-card/+view-get-timebar/+view-set-timebar --help`、对应 `--dry-run --as user` 和 `lark-base` skill 验证后接入；runtime 分别调用 `POST/PATCH/DELETE /open-apis/base/v3/bases/:base_token/tables/:table_id/views...`、`PUT .../filter|sort|group|visible_fields|card|timebar` 和 `GET .../visible_fields|card|timebar`，系统内仅开放 `grid/kanban/gallery/calendar/gantt` 视图类型、名称修改、显式 view_id 删除、`logic + conditions` 筛选、最多 10 项 `sort_config` 排序、最多 3 项 `group_config` 分组、最多 200 个 `visible_fields` 可见字段顺序配置、`cover_field` 卡片封面字段和 `start_time/end_time/title` 时间轴字段配置，已纳入 Feishu API capability、Tool Registry、runtime 读/写绑定、权限拒绝、后台工具模板和写目标审计一致性测试。
- IM 文本消息发送已开始从 API runtime 执行迁移到 CLI 优先执行：`feishu_im_send_message` confirmed 路径在未注入测试 `client` 时调用 `lark-cli im +messages-send --as bot|user --format json --chat-id/--user-id --text --idempotency-key`，参数通过数组传入，不做 shell 拼接；该能力已按 `lark-im` skill、`lark-shared` 认证规则、`lark-cli im +messages-send --help` 和 `--dry-run --as bot` 验证，测试已锁定 CLI argv、返回 message_id 文案、dry-run 提示和带测试 client 的 API runtime 受控路径。
- IM 建群和公开群加入已从 API runtime 执行迁移到 CLI 优先执行：`feishu_im_create_chat` confirmed 路径在未注入测试 `client` 时调用 `lark-cli im +chat-create --as bot|user --format json --name ...` 并按已验证 shortcut 追加 `--description`、`--users`、`--bots`、`--owner`、`--type`、`--chat-mode`、`--set-bot-manager`；`feishu_im_auto_join_public_chats` confirmed 路径要求使用 dry-run 预览得到的显式 `chat_ids`，再由 MCP 调度 `lark-cli api POST /open-apis/im/v1/chats/:chat_id/members/me_join --as user --params '{"chat_id":...}' --data '{}'`，不在 MCP 层用 query 自行选择群。两项能力已按 `lark-im`、`lark-shared`、`lark-cli im +chat-create --help`、`+chat-create --dry-run` 和 `lark-cli api ...members/me_join --dry-run` 验证，测试已锁定 MCP 内 CLI argv 和原 API runtime 受控测试路径。
- Calendar 日程创建已从 API runtime 执行迁移到 CLI 优先执行：`feishu_calendar_create_event` confirmed 路径在未注入测试 `client` 时调用 `lark-cli calendar +create --as user --format json --calendar-id --summary --start --end`，并按已验证 shortcut 追加 `--description`、`--attendee-ids`、`--rrule`；该能力已按 `lark-calendar`、`lark-shared`、`lark-cli calendar +create --help` 和 `--dry-run --as user` 验证，测试已锁定 MCP 内 CLI argv、返回文案和带测试 client 的 API runtime 受控路径。
- Calendar 日程实时读取已开始从 API runtime 迁移到 MCP/CLI：`calendar_qa` 在未注入测试 `client` 时由 Feishu MCP provider 调用 `lark-cli calendar +agenda --as user --format json --calendar-id --start --end`；带测试 client 的 API runtime 受控路径保留为同步层/测试受控路径，不作为 Agent 实时问答默认执行链路。
- Task 任务实时读取已开始从 API runtime 迁移到 MCP/CLI：`task_qa` 在未注入测试 `client` 时由 Feishu MCP provider 调用 `lark-cli task +get-my-tasks --as user --format json`，仅追加 CLI help 已验证的 `--query`、`--created_at`、`--due-start`、`--due-end`、`--page-token`、`--page-limit`、`--page-all` 和 `--complete`；带测试 client 的 API runtime 受控路径保留为同步层/测试受控路径。
- Mail 邮箱实时读取已开始从 API runtime 迁移到 MCP/CLI：`mail_qa` 在未注入测试 `client` 时由 Feishu MCP provider 调用 `lark-cli mail +triage --as user --format json --mailbox`，仅追加 CLI help 已验证的 `--query`、`--filter`、`--max`、`--page-token` 和 `--labels`；带测试 client 的 API runtime 受控路径保留为同步层/测试受控路径，邮件内容只作为不可信外部数据摘要读取，不触发写动作。
- Bitable 多维表格实时读取已开始从 API runtime 迁移到 MCP/CLI：`bitable_qa` 在未注入测试 `client` 时由 Feishu MCP provider；无 `table_id` 时调用 `lark-cli base +table-list --as user --format json --base-token`，有 `table_id` 时调用 `lark-cli base +record-list --as user --format json --base-token --table-id`，仅追加 CLI help 已验证的 `--view-id`、`--field-id`、`--filter-json`、`--sort-json`、`--offset` 和 `--limit`；带测试 client 的 API runtime 受控路径保留为同步层/测试受控路径。
- Bitable 字段和视图结构实时读取已开始从 API runtime 迁移到 MCP/CLI：`feishu_bitable_field_list` 调用 `lark-cli base +field-list --as user --format json --base-token --table-id`；`feishu_bitable_view_get_visible_fields/card/timebar` 分别调用 `lark-cli base +view-get-visible-fields/+view-get-card/+view-get-timebar --as user --format json --base-token --table-id --view-id`；这些能力只读取结构，不承担写入，API runtime 路径保留为同步层/测试受控路径。
- Approval 审批任务查询和实例详情实时读取已开始从 API runtime 迁移到 MCP/CLI：`feishu_approval_task_query` 在未注入测试 `client` 时调用 `lark-cli approval tasks query --as user --format json --params ...`，`feishu_approval_instance_get` 调用 `lark-cli approval instances get --as user --format json --params ...`；两项能力按 `lark-approval`、`lark-shared`、`lark-cli approval ... --help` 和 `lark-cli schema approval.tasks.query/approval.instances.get --format json` 验证，API runtime 路径保留为同步层/测试受控路径。
- OKR 周期和目标实时读取已开始从 API runtime 迁移到 MCP/CLI：`feishu_okr_cycle_list` 在未注入测试 `client` 时调用 `lark-cli okr +cycle-list --as user --format json --user-id --user-id-type`，`feishu_okr_objective_list` 调用 `lark-cli okr +cycle-detail --as user --format json --cycle-id`；两项能力按 `lark-okr`、`lark-shared` 和 `lark-cli okr +cycle-list/+cycle-detail --help` 验证，API runtime 路径保留为同步层/测试受控路径。
- Contact 通讯录实时读取已开始从 API runtime 迁移到 MCP/CLI：`feishu_contact_scope_list`、`feishu_contact_department_children`、`feishu_contact_department_users` 和 `feishu_contact_organization_snapshot` 在未注入测试 `client` 时由 Feishu MCP provider 调度 `lark-cli api GET ... --as bot --format json --params ...`；该能力按 `lark-contact`、`lark-openapi-explorer`、`lark-shared` 和 `lark-cli api GET ... --dry-run --as bot` 验证，API runtime 路径保留为同步层/测试受控路径。
- Task 创建、更新、完成、评论和附件上传已开始从 API runtime 执行迁移到 CLI 优先执行：`feishu_task_create/update/complete/comment/upload_attachment` confirmed 路径在未注入测试 `client` 时分别调用 `lark-cli task +create --data`、`lark-cli task +update --task-id --data`、`lark-cli task +complete --task-id`、`lark-cli task +comment --task-id --content`、`lark-cli task +upload-attachment --resource-id --resource-type --file`，继续保留系统现有任务 JSON 输入语义；这些能力已按 `lark-task` skill、`lark-shared` 认证规则、对应 `lark-cli task +... --help` 和 `--dry-run --as user` 验证，测试已锁定 CLI argv、payload、返回文案和带测试 client 的 API runtime 受控路径。
- 审批实例撤回已按 `lark-cli approval instances cancel --dry-run --as user`、`approval.instances.cancel` schema、`lark-approval` skill 和飞书官方“撤回审批实例”文档验证后接入，系统内使用 `instance_code` 调用 `POST /open-apis/approval/v4/instances/recall`，并纳入 Feishu API capability、Tool Registry、runtime 写绑定、权限拒绝和写目标审计一致性测试。
- 审批实例抄送已按 `lark-cli approval instances cc --dry-run --as user`、`approval.instances.cc` schema、`lark-approval` skill 和飞书官方“抄送审批实例”文档验证后接入，系统内使用 `instance_code + cc_user_ids` 调用 `POST /open-apis/approval/v4/instances/add_cc`，并纳入 Feishu API capability、Tool Registry、runtime 写绑定、权限拒绝和写目标审计一致性测试。
- 工具配置模型、后台 API 和管理后台工具页：已完成 `ToolConfig`、Alembic migration、`/api/v5/tools`、`/api/v5/tools/{tool_name}`、`/api/v5/tools/batch`、`/api/v5/tools/{tool_name}/execute`、`/api/v5/tools/executions`，前端可查看工具、Provider、兼容 Provider、权限、写能力并保存启用状态、Provider 和 `config_json`；后台 API 和工具页均可通过统一 Tool Router 执行 Bitable、Task、审批等写工具 dry-run/confirmed，执行后可联动系统日志筛选对应工具动作；列表与保存接口都会回显 `compatible_providers`、`provider_boundaries` 和 `config_json`；`provider_boundaries` 已明确 Feishu API 是否可用、API 写能力是否已登记、API provider 是否禁止 confirmed 实时写、写操作是否只能 dry-run 预演、Feishu MCP 是否需要显式工具绑定、默认禁写状态、当前 `binding_status=unbound` 和阻断原因；写工具边界会回显 `supports_write=false`、`supports_confirmed_realtime_write=false`、`confirmed_write_policy=blocked_realtime_use_tool_router_mcp_cli`、`sync_engine_direct_api_allowed=false` 和 `sync_engine_mcp_access_allowed=false`，避免把 Bitable/Task/Approval 实时写能力误解释成 API 或同步层职责；后端会拒绝未验证的 Provider 覆盖，不兼容 Provider 返回 400，未知工具返回 404；Agent runtime 读取历史 ToolConfig 时也会重新校验 Provider 兼容性；Provider 切到 Feishu API/MCP/DevOps 时前端会显示运行边界和风险提示，其中 MCP 文案明确“只负责工具调度、不直接执行动作”，动作执行器是 CLI；工具执行日志已显示写模式、确认令牌状态和写目标摘要。
- 管理后台工具执行面板已补齐所有 Tool Registry 注册工具的参数模板，覆盖 Calendar、Task、Bitable、Mail、Drive、IM、Wiki、Meeting、Approval 实时读工具，以及本地问答、ReportTool、KnowledgeTool 和 DevOps 诊断工具；`tests/test_cockpit.py::test_console_tool_templates_cover_all_registered_tools` 会从 `TOOL_REGISTRY` 反查控制台模板，避免后续新增工具但后台无法执行。
- 管理后台工具列表已直接展示 `business_tool`，并在统计区显示当前已覆盖的业务工具族数量，便于按 XMind 9 个 Tool 检查上线覆盖；选中工具时会显示 `business_tool_capabilities` 方便核对 AutomationTool/PeopleTool 等能力定义。
- 上线模板 `.env.example` 已将 `FEISHU_BOT_AI_MODE_ENABLED=true` 作为默认值，保证新部署的大飞哥机器人在固定命令之外会进入 Agent Runtime 自然语言兜底；V5 OS overview 已返回 `entrypoints.feishu_bot.ai_mode_enabled`，管理后台首屏会直接显示“大飞哥 AI 兜底已开/未开”；生产仍可通过环境变量显式关闭。
- Feishu 管理路由和写 service 已加架构防回归测试，禁止 `app/api/routes/feishu.py` 直接调用 native write/send client，并锁定 Task、Bitable、Approval、IM 写 service 只能由 `app/services/feishu/api_runtime.py` 调用；外部写入口必须进入 Tool Router 或 Gateway responder。公开群 auto-join 旧管理 helper 只保留 dry-run 候选预览，真实加入只能走 Tool Router confirmed 路径。
- Tool Router 已支持在工具切换为 `feishu_api` provider 时自动注入当前公司的 active FeishuAppConfig。
- 审批加签按 `lark-cli approval tasks add_sign --dry-run` 和 schema 验证后接入；审批退回按 `lark-cli approval tasks rollback --dry-run` 和 schema 验证后接入；审批实例撤回按 `lark-cli approval instances cancel --dry-run --as user` 和 schema 验证后接入；审批实例抄送按 `lark-cli approval instances cc --dry-run --as user` 和 schema 验证后接入；测试已覆盖真实 runtime 请求、普通成员权限拒绝、Tool Registry/provider/runtime 绑定一致性和写目标审计。
- 工具执行审计已接入 `AuditLog`，并已提供后端查询 API 和管理后台工具执行日志视图；写工具审计会记录 `write_mode`、`dry_run`、`confirmed`、`has_confirmation_token` 和 `write_target` 目标摘要，覆盖 Bitable 记录 app/table/record/upsert_mode/附件 file_count/file_names、建表/建字段目标与字段名、Task task_guid/update_fields/relative_fire_minutes/附件 file_name、Task/Calendar 创建标题、Approval approval_code/instance_code/task_id/task_ids/node_ids/transfer_user_id/add_sign_user_ids/cc_user_ids、IM receive_id/name/chat_ids 等关键 ID，但不保存 confirmation_token、附件内容或本地完整路径原文。
- DevOps provider 已接入只读 `feishu_cli_status` 与 `feishu_cli_doctor`，后台可通过 Tool Router 检查本机 `lark-cli` 路径、版本、固定命令退出码、关键命令覆盖、命令摘要和 `doctor --offline` JSON 健康摘要；这些工具固定调用 `--version`、`--help` 和 `doctor --offline`，不允许传入任意 CLI 命令，且需要 `system:admin` 权限。管理后台工具执行面板已区分读/写工具：读工具直接执行，写工具仍必须 dry-run 后带回确认令牌。
- 系统日志入口已接入 `AuditLog` 聚合：`/api/v5/system/logs/overview` 返回分类、级别、最近错误和统计计数；`/api/v5/system/logs` 支持分类、级别、状态、原因、动作、对象类型、确认状态和 confirmation token 校验状态筛选，已包含 approval/gateway 分类，管理后台审计页已显示系统日志概览、筛选明细、分类统计、最近错误、审批确认校验字段、Gateway reason、未知卡片动作快捷筛选和写目标摘要；确认令牌状态和写目标可查，但不保存 confirmation_token 原文。
- 管理后台首屏已补“多公司运营状态”智能中心视图，聚合公司、员工智能体、飞书资源、工作事件、同步异常、治理动作、工具边界状态和大飞哥 AI 兜底状态；V5 OS overview 在数据库不可用时返回 `status.database=unavailable`、零计数和 entrypoints，前端 bootstrap 先读 OS overview，数据库不可用时跳过 dashboard、companies、sync-status 等 DB 重接口，公司下拉禁用并显示“数据库未连接”，离线态 `safeLoad` 会跳过除 OS overview 之外的 GET 数据接口，`safeAction` 会暂停写/操作按钮，不再让后台首屏或设置/审计导航因本地 Postgres 未连接而 500、POST 或刷屏；工具管理和系统日志已从隐藏 API 变成可操作页面；本地数据服务未连接时保持可读离线态，不再出现首屏空白。

验收：

```bash
cd digital-advisor
.venv312/bin/python -m pytest tests/test_tool_router.py tests/test_agent_runtime.py
.venv312/bin/python -m pytest tests/test_tools_approval.py tests/test_tools_chat.py tests/test_tools_knowledge.py
.venv312/bin/python -m pytest tests/test_cockpit.py
```

### Phase 4：Agent Runtime 完整落地

目标：

- Agent Runtime 成为唯一问答编排中心。
- 意图识别、规划、权限、记忆、工具调用、答案组织全部在 Runtime 管理。
- 旧 `bot_answering.py` 已删除；后续不再以旧模块名作为兼容目标。
- Runtime 已通过 Tool Router 调用首批工具；意图、上下文、策略、第一阶段 Planner 已迁入 `agent/*`；已新增不破坏旧接口的 `answer_agent_message_with_trace`，可返回语义、路由、Planner、工具/Advisor/拒绝路径的执行轨迹；`/api/v5/agent/trace-preview` 已按公司 Agent 设置接入 Planner 开关、写工具启停和写工具确认策略，并写入 `agent.trace.preview` 审计日志；真实飞书机器人入口通过 `commands.py -> command_dispatcher -> command_handlers -> bot_runtime` 读取同一公司设置并传给 Agent Runtime，入口层不允许直接调用 `employee_bot_answer` 或 `answer_agent_message`；运行时已在写工具执行前应用 `allow_write_tools` 和 `require_write_confirmation`，禁止绕过 dry-run/confirmation_token；Planner 和 Runtime trace 已用机器可读字段记录 `requires_dry_run` 与 `confirmed_execution_requires=["dry_run=true","confirmed=true","confirmation_token"]`；`/api/v5/agent/traces` 已提供最近 trace 历史列表，AI 助理弹窗已显示回答、当前执行轨迹和历史轨迹表，历史轨迹表已显示写操作、dry-run、写策略和确认要求。后续重点是真正多步执行和计划失败恢复。

交付：

- `app/services/agent/runtime.py`
- `app/services/agent/intents.py`
- `app/services/agent/planner.py`
- `app/services/agent/memory.py`
- `app/services/agent/answer.py`
- `app/services/agent/policies.py`
- 现有 bot 测试迁移到 agent 测试。

验收：

```bash
cd digital-advisor
.venv312/bin/python -m pytest tests/test_agent_runtime.py tests/test_agent_intents.py tests/test_tools_*.py
```

### Phase 5：管理后台重构

目标：

- 后台按 XMind 功能重新组织。
- 不只是调试接口集合，而是真正的系统管理入口。

页面/能力：

- 系统设置。
- 公司账号。
- 飞书机器人配置。
- 群聊策略。
- 个人账号。
- 飞书连接。
- 权限配置。
- 工具管理。
- Agent 设置基础可读写已完成，后续补更细模型参数和策略预设。
- 系统日志概览与筛选、Agent trace 预览/历史列表和 Gateway 消息安全摘要日志已完成。
- 经营概览。
- 网页版机器人。

设计原则：

- 美观、简洁、克制，参考 OpenAI 风格。
- 面向 Owner 和业务管理者，不按传统 IT 管理后台思路堆配置项。
- 默认展示经营状态、数据覆盖、风险、待办和关键动作；底层配置收进二级入口。

交付：

- 后台 API 收敛。
- 静态控制台页面重整。
- 工具和 Agent 配置可读写；工具可通过统一后台执行入口 dry-run/confirmed。
- 系统日志概览与筛选明细可查，Agent trace 可预览、可看最近历史并写审计。

验收：

```bash
cd digital-advisor
.venv312/bin/python -m pytest tests/test_v5.py tests/test_v5_foundation.py tests/test_v5_architecture.py
curl http://localhost:8000/console
```

### Phase 6：同步和知识完整性

目标：

- 飞书同步能力完整迁移到最终 Sync Engine。
- 资源发现、资源同步、事件入库、向量索引、长期记忆抽取形成闭环。
- 数据覆盖率进入驾驶舱。

交付：

- Sync Engine 边界。
- 各类资源同步策略。
- 向量索引状态。
- 长期记忆抽取策略。
- 数据覆盖率 API。

验收：

```bash
cd digital-advisor
.venv312/bin/python -m pytest tests/test_tasks.py tests/test_v5*.py
```

### Phase 7：报告和驾驶舱完整化

目标：

- Owner 驾驶舱不是简单列表，而是围绕经营决策组织。
- 报告中心支持日报、周报、月报、跨公司总报。
- 风险、任务、决策、项目动态有统一结构。

交付：

- ReportTool 完整化。
- cockpit 模块补齐。
- 报告生成、存储、查询。
- 风险和决策支持链路。

验收：

```bash
cd digital-advisor
.venv312/bin/python -m pytest tests/test_cockpit.py tests/test_advisor.py tests/test_approval_advisor.py
```

### Phase 8：旧代码删除和结构收口

目标：

- 删除已经被最终架构替代的旧代码。
- 清理旧命名和临时 V5 文件。
- 保留仍有价值的对外 API；旧系统兼容不是目标。

交付：

- 删除生成物。
- 删除旧机器人核心入口。
- 旧资源 ORM 模型已删除；旧表只保留历史迁移脚本和 raw SQL 迁移兜底。
- 删除无用调试接口。
- README 更新。

验收：

```bash
cd digital-advisor
.venv312/bin/python -m pytest
.venv312/bin/ruff check app tests
node --check app/static/console/app.js
```

### Phase 9：上线准备

目标：

- 不是“抢上线”，而是最终架构版本上线。
- 完成真实环境演练、权限演练、同步演练、机器人演练、回滚演练。

交付：

- 上线检查清单。
- 运行手册。
- 回滚手册。
- 已知风险清单。
- 首批用户使用范围。

验收：

```bash
cd digital-advisor
docker compose up -d --build
docker compose exec api python scripts/seed_demo.py
curl http://localhost:8000/health
curl http://localhost:8000/api/v5/os/overview
```

## 6. 一次做好的关键设计点

### 6.1 不允许的临时方案

- 不允许 Agent 直接调用 Feishu API。
- 不允许 Gateway 写业务逻辑。
- 不允许 Tool 绕过权限检查。
- 不允许本地数据和飞书实时数据混在一个不可解释的结果里。
- 不允许普通员工默认看到公司级数据。
- 不允许为了兼容旧接口继续扩展旧边界。

### 6.2 必须保留的扩展点

- Feishu API 和 Feishu MCP 可切换。
- 本地优先、API 优先、MCP 优先可配置。
- Planner 可启用/禁用。
- Memory 可启用/禁用。
- 模型、温度、最大工具调用次数、最大循环次数可配置。
- 工具调用全链路可审计。

## 7. 推荐执行顺序

严格按以下顺序执行：

1. Phase 0：审计。
2. Phase 1：Data Layer。
3. Phase 2：Gateway。
4. Phase 3：Tool Router。
5. Phase 4：Agent Runtime。
6. Phase 5：管理后台。
7. Phase 6：同步和知识。
8. Phase 7：报告和驾驶舱。
9. Phase 8：删除旧代码。
10. Phase 9：上线准备。

原因：数据层决定权限和资源边界；工具层决定 Agent 能力边界；最后再删旧代码，风险最低。

## 8. 下一步

当前已进入上线收口阶段，不再回到 Phase 0。下一步按以下顺序推进：

1. 机器人入口演练：HTTP 事件、WebSocket、固定命令、自然语言兜底、审批卡片交互和未知卡片动作审计。
2. 管理后台演练：按 XMind 9 个 Tool 检查工具列表、Provider 边界、参数模板、dry-run/confirmed、系统日志和工具执行日志；同时验证数据库不可用时首屏、设置和审计导航只请求 OS overview，不刷 500/POST 错误。
3. 同步与知识演练：验证 Sync Engine 只走 API Client 入库，L1 热知识进入 Document Store/Vector DB/RAG，L2 冷知识只登记并由 CLI 实时查询。
4. 发布前检查：跑定向测试、必要全量测试、ruff、前端语法、uvicorn 启动和 `/console` 静态资源检查。
5. 清理确认：只删除缓存、已迁移旧文件和无用入口；发布迁移兜底代码在对应迁移完成前保留。
