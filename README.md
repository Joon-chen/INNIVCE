# 老司机（企业数字参谋）V5

这是面向多家公司运营管理的企业数字参谋系统。飞书是企业数据和运营主平台，系统通过大飞哥机器人、管理后台和后续 iOS App 入口，为 Owner 和员工提供个人智能体能力，并把审批、消息、日历、任务、会议、文档、Wiki、多维表格、通讯录等数据沉淀为 Operational Data、Knowledge Data、WorkEvent 和 Memory。

## 技术栈

- Python 3.12
- FastAPI
- PostgreSQL + SQLAlchemy + Alembic
- Redis + Celery
- Qdrant
- MinIO
- Docker Compose
- OpenAI API

## 当前已实现

- 多公司、多账号基础模型
- 飞书 App 配置
- 飞书 `tenant_access_token` 获取与 Redis 缓存
- 飞书事件订阅 URL verification
- 飞书事件归一化写入 `work_events`
- 大飞哥机器人入口：固定命令、自然语言 Agent Runtime 兜底、审批互动卡片和未知卡片动作审计
- Tool Router：按 V5 9 个业务工具族组织 ApprovalTool、KnowledgeTool、BitableTool、ChatTool、CalendarTool、MeetingTool、ReportTool、AutomationTool、PeopleTool
- Feishu MCP / CLI / API 职责边界：实时 Agent 操作走 `Tool Router -> Tool -> MCP -> CLI`，同步入库走 `Sync Engine -> API Client -> Feishu -> PostgreSQL`
- IMAP 邮件读取、线程、标签、附件解析
- MinIO 附件存储
- Gmail OAuth URL 生成入口
- Microsoft Graph OAuth URL 和读取消息入口
- `work_events` 统一写入、去重、脱敏、审计日志
- AI 任务/风险/决策抽取
- AI 日报生成，未配置 OpenAI Key 时提供本地降级摘要
- Qdrant 语义向量检索：自动索引 `work_events`，机器人问答可优先检索语义相关上下文
- Celery 后台任务
- Docker Compose、Alembic migration、`.env.example`

## 快速启动

```bash
cd digital-advisor
cp .env.example .env
docker compose up -d --build
```

服务启动后：

- API: http://localhost:8000
- Swagger: http://localhost:8000/docs
- MinIO Console: http://localhost:9001
- Qdrant: http://localhost:6333

健康检查：

```bash
curl http://localhost:8000/health
```

V5 上线前低噪声检查：

```bash
bash scripts/release_check_v5.sh
```

V5 上线演练、机器人验收、管理后台验收和回滚步骤见 `RELEASE_RUNBOOK_V5.md`。

依赖版本已经在 `pyproject.toml` 和 `requirements.lock` 中锁定。Docker 构建默认使用阿里云 PyPI 镜像，减少官方 PyPI/CDN 抖动造成的下载失败或 hash mismatch。

如果你要切换镜像源，可以显式传入：

```bash
docker compose build --build-arg PIP_INDEX_URL=https://pypi.org/simple api
docker compose build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple api
```

本机开发测试优先使用项目虚拟环境：

```bash
.venv312/bin/pytest
.venv312/bin/ruff check app tests
```

不要直接用系统 `python3 -m pytest` 跑本项目测试；当前系统 Python 可能是 3.9，而本项目要求 Python 3.12。

本机长期运行可以使用更稳的 Compose 模板，API 不启用 `--reload`，数据库、Redis、Qdrant、MinIO 端口只暴露到本机：

```bash
docker compose -f docker-compose.local-prod.yml up -d --build
```

仍在开发和改代码时，优先使用默认 `docker-compose.yml`。

## 初始化演示数据

容器启动后可以运行：

```bash
docker compose exec api python scripts/seed_demo.py
```

它会创建一个演示公司、一条工作事件，并生成一份 AI 日报。如果没有 `OPENAI_API_KEY`，系统会自动使用本地规则摘要。

## 飞书接入

### V5 职责边界

实时 Agent 操作必须经过：

```text
Agent Runtime -> Tool Router -> Tool -> MCP -> CLI/API -> Feishu
```

数据同步与入库必须经过：

```text
Sync Engine -> API Client -> Feishu -> PostgreSQL
```

禁止 Agent Runtime 直接调用 Feishu CLI、API 或 MCP；禁止 Sync Engine 调用 MCP。Tool Router 负责业务能力，MCP 负责工具调度，CLI 负责实时动作执行，API Client 负责同步、采集、入库、WorkEvent 生成、历史分析和长期存储。

### 统一 FeishuClient 双层规则

同步层和受控验证路径统一使用 `FeishuClient`，内部拆成两层：

```text
FeishuClient.sdk      飞书服务端 SDK 层
FeishuClient.raw      Raw HTTP 层
```

路由规则：

```text
SDK 优先：
- tenant_access_token 生命周期托管在 SDK 调用内部
- 机器人发送消息
- 通讯录用户/部门
- 群组/消息基础能力
- 日历
- 视频会议

Raw HTTP 优先：
- 显式 tenant_access_token 调试接口
- Wiki / 知识库
- Bitable 多维表格高级能力
- 审批高级能力
- 资源发现
- 飞书邮箱
- 云文档/文件复杂上传下载
- 未来新增接口或 SDK 尚未覆盖的接口
```

在 Swagger 可查看当前路由规则和 SDK 安装状态：

```text
GET /api/feishu/apps/{app_config_id}/client-routing
```

1. 创建公司：

```bash
curl -X POST http://localhost:8000/api/companies \
  -H "Content-Type: application/json" \
  -d '{"name":"Acme","code":"acme"}'
```

2. 创建飞书 App 配置：

```bash
curl -X POST http://localhost:8000/api/feishu/apps \
  -H "Content-Type: application/json" \
  -d '{
    "company_id":"替换为公司ID",
    "name":"Acme Feishu",
    "app_id":"cli_xxx",
    "app_secret":"xxx",
    "verification_token":"飞书事件订阅 token"
  }'
```

3. 获取并缓存 `tenant_access_token`：

```bash
curl -X POST http://localhost:8000/api/feishu/apps/替换为配置ID/tenant-access-token
```

4. 配置飞书事件订阅 URL：

```text
http://你的公网域名/api/feishu/events/替换为配置ID
```

飞书 URL verification 请求会返回：

```json
{"challenge":"飞书传入的 challenge"}
```

5. 机器人发送消息：

```bash
curl -X POST http://localhost:8000/api/feishu/apps/替换为配置ID/bot/send \
  -H "Content-Type: application/json" \
  -d '{
    "receive_id_type":"open_id",
    "receive_id":"ou_xxx",
    "msg_type":"text",
    "content":{"text":"数字参谋测试消息"}
  }'
```

互动卡片可把 `msg_type` 设为 `interactive`，`content` 按飞书互动卡片 JSON 传入。

## IMAP 邮件接入

1. 创建 IMAP 账号：

```bash
curl -X POST http://localhost:8000/api/accounts \
  -H "Content-Type: application/json" \
  -d '{
    "company_id":"替换为公司ID",
    "provider":"imap",
    "display_name":"My Mail",
    "external_account_id":"me@example.com",
    "email_address":"me@example.com",
    "credentials":{
      "host":"imap.example.com",
      "port":993,
      "ssl":true,
      "username":"me@example.com",
      "password":"应用专用密码"
    }
  }'
```

2. 同步邮件：

```bash
curl -X POST http://localhost:8000/api/mail/imap/sync \
  -H "Content-Type: application/json" \
  -d '{"account_id":"替换为账号ID","folder":"INBOX","limit":20}'
```

同步后的邮件会写入 `work_events`，附件会上传到 MinIO 并记录到 `attachments`。

## Gmail 和 Outlook

Gmail OAuth URL：

```bash
curl "http://localhost:8000/api/mail/gmail/oauth-url?state=acme"
```

Microsoft Graph OAuth URL：

```bash
curl "http://localhost:8000/api/mail/graph/oauth-url?state=acme"
```

Microsoft Graph 读取消息：

```bash
curl "http://localhost:8000/api/mail/graph/messages?access_token=ACCESS_TOKEN&limit=10"
```

## work_events 与日报

手动写入工作事件：

```bash
curl -X POST http://localhost:8000/api/work-events \
  -H "Content-Type: application/json" \
  -d '{
    "company_id":"替换为公司ID",
    "source":"manual",
    "event_type":"decision",
    "title":"确认上线窗口",
    "content_text":"决定周五 20:00 上线。风险：审批可能延期。待办：同步客户成功团队。"
  }'
```

生成日报：

```bash
curl -X POST http://localhost:8000/api/reports/daily \
  -H "Content-Type: application/json" \
  -d '{"report_date":"2026-06-08","company_id":"替换为公司ID"}'
```

抽取任务、风险、决策：

```bash
curl -X POST http://localhost:8000/api/work-events/替换为事件ID/extract
```

## 自动同步与机器人指令

Docker Compose 已包含 `beat` 服务，用于周期性触发 Celery 任务：

```bash
docker compose up --build
```

`.env` 中可配置：

```dotenv
AUTO_IMAP_SYNC_ENABLED=true
AUTO_IMAP_ACCOUNT_IDS=替换为邮箱账号ID
AUTO_IMAP_INTERVAL_SECONDS=1800
AUTO_IMAP_LIMIT=50

AUTO_DAILY_REPORT_ENABLED=true
AUTO_DAILY_REPORT_COMPANY_IDS=替换为公司ID
AUTO_DAILY_REPORT_HOUR=18
AUTO_DAILY_REPORT_MINUTE=30
AUTO_DAILY_REPORT_PUSH_FEISHU=true

AUTO_V5_RESOURCE_SYNC_ENABLED=true
AUTO_V5_RESOURCE_SYNC_INTERVAL_SECONDS=900
AUTO_V5_RESOURCE_SYNC_LIMIT_RESOURCES=10
AUTO_V5_RESOURCE_SYNC_EVENT_LIMIT=20
AUTO_V5_RESOURCE_SYNC_MAX_PAGES=2
AUTO_V5_RESOURCE_SYNC_RESOURCE_TYPES=approval,chat,calendar,meeting,task,directory
AUTO_V5_RESOURCE_SYNC_STATUSES=never_synced,stale

FEISHU_DEFAULT_APP_CONFIG_ID=替换为飞书App配置ID
FEISHU_DEFAULT_RECEIVE_ID_TYPE=open_id
FEISHU_DEFAULT_RECEIVE_ID=替换为Open ID
```

V5 资源自动同步会按资源登记表自动筛选“未同步/已过期”的审批、邮箱、群聊、日历、会议、任务和通讯录资源。管理后台里的手动同步只作为调试和应急按钮保留。

推荐在管理后台进入 `资源 -> 同步`，直接配置每家公司的自动同步策略；`.env` 中的 `AUTO_V5_RESOURCE_SYNC_*` 只作为系统级默认值和兜底配置。Celery beat 负责周期性轮询，实际是否同步、同步哪些类型、每轮同步多少资源，以控制台保存的公司策略为准。

机器人支持的文字指令：

```text
帮助
今日日报
同步邮箱
最近邮件
待办事项
```

机器人指令依赖飞书事件订阅回调。若本地没有公网 HTTPS，系统仍会自动同步邮箱和生成日报，但飞书里发给机器人的指令无法主动到达本机。

### 飞书 SDK 长连接

本项目也支持飞书 Python SDK 的长连接事件订阅，不需要公网 HTTPS。

在飞书开放平台中选择：

```text
开发配置 → 事件订阅 → 订阅方式 → 长连接
```

订阅事件：

```text
im.message.receive_v1
```

本地启用：

```dotenv
FEISHU_WS_ENABLED=true
FEISHU_DEFAULT_APP_CONFIG_ID=替换为飞书App配置ID
```

启动服务：

```bash
docker compose up -d --no-build api worker beat feishu-ws
```

然后可以在飞书里给机器人发送：

```text
帮助
今日日报
同步邮箱
最近邮件
待办事项
```

开启机器人 AI 顾问模式：

```dotenv
FEISHU_BOT_AI_MODE_ENABLED=true
FEISHU_BOT_CONTEXT_EVENTS=80
FEISHU_BOT_MODEL_CONTEXT_EVENTS=18
FEISHU_BOT_ADMIN_OPEN_IDS=管理员OpenID，多个用英文逗号分隔
FEISHU_SYNC_CONTACTS_TO_BOT_USERS=true
AI_PROVIDER=local
OPENAI_USE_FOR_BOT=false
```

权限边界：

```text
管理员 Open ID：可查询全局 work_events、邮件、日报、全局待办和风险。
非管理员用户：只能基于当前飞书 chat_id 的会话内容回答。
```

机器人也支持按飞书通讯录自动推断业务域权限：

```text
财务负责人：finance / approval，可查询财务、付款、报销等授权业务域。
销售负责人：sales，可查询客户、销售群、订单、合同等授权业务域。
研发负责人：rd，可查询研发项目、技术知识库、测试问题等授权业务域。
质量负责人：quality，可查询质量、测试异常、售后质量记录。
采购/供应链负责人：supply_chain，可查询采购、供应商、物料和交付风险。
人事负责人：hr，可查询招聘、入离职和人事流程。
行政负责人：admin，可查询行政采购、办公和行政流程。
```

默认规则：同步通讯录后，普通成员仍是 `member/chat`，只能看当前会话；岗位名或部门名包含负责人、经理、总监、主管等管理词，并命中业务域关键词时，自动升级为 `manager/domain`。老板/管理员的 `owner/admin + company` 权限不会被自动降级。

在管理后台 `/console` 进入「设置 → 权限 → 机器人权限」可以：

```text
查看规则 → 预览重算 → 应用重算
```

也可以直接调用 API：

```bash
curl -X POST http://localhost:8000/api/bot-users/recalculate-permissions \
  -H "Content-Type: application/json" \
  -d '{"company_id":"替换为公司ID","dry_run":false,"update_manual_admins":false}'
```

推荐的本地优先方案：

```dotenv
AI_PROVIDER=hybrid
LOCAL_LLM_BASE_URL=http://host.docker.internal:11434/v1
LOCAL_LLM_API_KEY=ollama
LOCAL_LLM_MODEL=qwen2.5:14b
OPENAI_USE_FOR_BOT=true
OPENAI_USE_FOR_REPORTS=false
OPENAI_USE_FOR_EXTRACTION=false
DEEPSEEK_API_KEY=替换为DeepSeek API Key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_USE_FOR_REPORTS=true
DEEPSEEK_USE_FOR_EXTRACTION=true
DEEPSEEK_USE_FOR_BOT_ANALYSIS=true
```

配套下载模型：

```bash
ollama pull qwen2.5:14b
```

这个模式下，机器人日常问答默认走本地 Qwen；日报、周报、抽取任务/风险/决策、复杂经营分析和关键管理建议使用 DeepSeek 云端 API。`FEISHU_BOT_MODEL_CONTEXT_EVENTS` 控制喂给本地模型的上下文条数，数值越小响应越快。

如需使用 DeepSeek 云端 API：

```dotenv
AI_PROVIDER=deepseek
DEEPSEEK_API_KEY=替换为DeepSeek API Key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
OPENAI_USE_FOR_BOT=true
```

`DEEPSEEK_MODEL` 也可以设置为 `deepseek-reasoner`，但日常顾问对话建议先用 `deepseek-chat`。

AI 顾问模式下，可以直接问：

```text
今天有什么风险？
我有哪些待办？
最近飞书聊了什么？
总结一下固势今天的重点
```

## 飞书全信息同步策略

飞书数据按三种方式处理：

```text
事件订阅：实时变化直接入库，例如消息、审批变化、通讯录变化。
实时工具：用户临时询问或执行动作时，通过 Tool Router -> MCP -> CLI 查询或执行，例如今天日程、某个审批状态、发送消息、创建任务。
定时入库：用于长期记忆和日报/周报/风险分析，例如飞书邮箱、通讯录、群组、审批、日历、任务、会议、云文档元数据。
```

查看当前分类计划：

```text
GET /api/feishu/apps/{app_config_id}/sync-plan
```

探测当前飞书 App 的接口权限是否可用：

```text
POST /api/feishu/apps/{app_config_id}/capabilities/probe
```

立即同步已支持的信息类型：

```text
POST /api/feishu/apps/{app_config_id}/information/sync
```

请求示例：

```json
{
  "kinds": ["mail", "chats", "contacts", "approvals", "calendar", "tasks", "meetings", "drive"],
  "limit": 20,
  "extract_items": true
}
```

同步飞书邮箱需要指定邮箱和文件夹：

```json
{
  "kinds": ["mail"],
  "user_mailbox_id": "替换为飞书邮箱地址",
  "folder_id": "INBOX",
  "limit": 20,
  "max_pages": 3,
  "extract_items": true
}
```

说明：飞书邮箱 API 围绕 `user_mailbox_id` 操作。当前系统使用 `tenant_access_token`，建议把 `user_mailbox_id` 填成你的飞书邮箱地址；`me` 通常只适用于 `user_access_token` 模式。

如需要用户身份能力，可先生成飞书 OAuth 链接，再用回调得到的 `code` 交换 `user_access_token`：

```text
GET /api/feishu/apps/{app_config_id}/oauth-url
POST /api/feishu/apps/{app_config_id}/oauth/exchange
```

同步某个群的历史消息需要 `chat_id`：

```json
{
  "kinds": ["messages"],
  "chat_id": "oc_xxx",
  "limit": 20,
  "extract_items": true
}
```

定时同步配置：

```dotenv
AUTO_FEISHU_SYNC_ENABLED=true
AUTO_FEISHU_APP_CONFIG_IDS=替换为飞书App配置ID
AUTO_FEISHU_SYNC_KINDS=mail,chats,contacts,calendar,tasks,meetings,drive
AUTO_FEISHU_SYNC_INTERVAL_SECONDS=1800
AUTO_FEISHU_SYNC_LIMIT=50
AUTO_FEISHU_MAIL_USER_MAILBOX_ID=替换为飞书邮箱ID
AUTO_FEISHU_MAIL_FOLDER_ID=INBOX
```

多维表格需要先发现或指定 `app_token` 和 `table_id` 后再做表级同步。

```json
{
  "kinds": ["bitable"],
  "app_token": "替换为多维表格 app_token",
  "table_id": "替换为 table_id",
  "limit": 20,
  "extract_items": true
}
```

文档正文同步需要指定 `document_id` 和 `document_type`：

```json
{
  "kinds": ["docx"],
  "document_id": "替换为文档 token",
  "document_type": "docx",
  "extract_items": true
}
```

如果企业邮箱已经托管在飞书邮箱，建议优先使用 `mail` 这种飞书邮箱同步模式。IMAP/Gmail/Graph 保留为外部邮箱或备用接入方式。

## 运营与控制台接口

本地控制台：

```text
http://localhost:8000/console
```

控制台采用静态前端文件组织，入口在 `app/static/console/`。当前定位是 Owner 智能中心：首页是多公司运营状态和经营概览，包含公司、员工智能体、飞书资源、工作事件、同步异常、治理动作、飞书 CLI 执行层状态、工具边界状态、大飞哥 AI 兜底状态、今日重点、风险预警、待办事项、审批动态、项目动态、沟通、会议、资源同步和报告入口；系统配置统一放在「设置」中。

如果数据库暂不可用，`/api/v5/os/overview` 会降级返回 `status.database=unavailable`、`release_readiness.status=degraded`、零计数和 entrypoints。控制台首屏仍可读，公司下拉显示“数据库未连接”，并暂停除 OS overview 外的数据加载和写/操作按钮，避免刷 500 或 POST 错误。

Docker local-prod 会在镜像中安装 `lark-cli`，并通过 `LARK_CLI_HOME` 和 `LARK_CLI_DATA_HOME` 把 CLI 配置及加密 appSecret store 只读挂载到应用容器。上线前必须在容器内执行 `lark-cli doctor --offline`；如果显示 `identity_ready=fail`，说明 macOS Keychain 或本机 secret store 中的 CLI 身份没有进入容器，`/api/v5/os/overview` 会降级为 `feishu_cli_identity_unavailable`。此时需要明确执行 `lark-cli config keychain-downgrade` 后挂载对应支持文件，或为生产容器单独初始化一套 CLI bot/user 身份。

生产容器推荐使用独立 CLI home：`LARK_CLI_HOME="$PWD/.local/lark-cli-v5" LARK_APP_ID="cli_xxx" scripts/prepare_lark_cli_home_v5.sh init-bot-container < app_secret.txt`，再用 `scripts/prepare_lark_cli_home_v5.sh auth-user-start` 发起用户授权，最后运行 `scripts/prepare_lark_cli_home_v5.sh check` 和 `bash scripts/local_prod_check_v5.sh`。

设置页按当前 V5 信息架构拆分：

- 公司与资源：公司、飞书应用/机器人、发现、登记、同步、监控。
- 知识库：文档、关系、记忆，后续接入。
- 权限：部门、团队、用户、角色、系统权限、资源权限、机器人权限。
- AI 设置：本地模型、DeepSeek/OpenAI 等 LLM Router 配置。
- 审计日志：系统日志、工具执行日志、Gateway reason、未知卡片动作和写目标摘要。

新公司快速配置：

```text
POST /api/onboarding/company-setup
```

最少只需要公司名和公司代码；如果同时提供飞书 App、飞书邮箱和管理员 Open ID，系统会一起创建 App 配置、邮箱资源和机器人 owner 权限。

```json
{
  "company_name": "新公司名称",
  "company_code": "new_company",
  "feishu_app": {
    "name": "大飞哥",
    "app_id": "cli_xxx",
    "app_secret": "替换为 App Secret",
    "verification_token": "可选",
    "encrypt_key": "可选"
  },
  "feishu_mailbox_id": "name@example.com",
  "bot_admin_open_id": "ou_xxx"
}
```

资源发现与登记：

```text
POST /api/feishu/apps/{app_config_id}/resources/discover
```

它会自动尝试发现并登记：

- `chat`: 群和会话 ID，可用于同步历史消息
- `mail_folder`: 飞书邮箱文件夹，可用于同步收件箱、已发送等
- `drive_file`: 云文档和云盘文件 token
- `bitable_app` / `bitable_table`: 多维表格 app_token 和 table_id
- `approval_code`: 审批定义 code，主要从本地已入库事件里挖掘

请求示例：

```json
{
  "kinds": ["chats", "mail", "drive", "bitable", "approvals", "local"],
  "mailbox_id": "name@example.com",
  "app_tokens": [],
  "limit": 50,
  "include_local_mining": true
}
```

系统健康总览：

```text
GET /api/system/status
```

同步历史：

```text
GET /api/sync-runs
```

机器人用户角色：

```text
POST /api/bot-users
GET /api/bot-users
```

飞书资源登记，例如重点群、审批流、多维表格、文档：

```text
POST /api/feishu/resources
GET /api/feishu/resources
```

长期记忆事实：

```text
POST /api/memory-facts
GET /api/memory-facts
```

可选 API 管理 Token：

```dotenv
ADMIN_API_TOKEN=替换为强随机字符串
```

设置后，管理类接口需要带请求头：

```text
X-Admin-Token: 替换为强随机字符串
```

## Qdrant 语义向量检索

Qdrant 负责保存向量，embedding 模型负责把文字变成向量。默认使用本地 `local_hash` embedding，不会把邮件或飞书内容发送到外部模型；聊天模型可以继续使用 DeepSeek 或 OpenAI。

配置：

```dotenv
QDRANT_ENABLED=true
QDRANT_URL=http://qdrant:6333
QDRANT_COLLECTION=work_events
QDRANT_SCORE_THRESHOLD=0.25
EMBEDDING_PROVIDER=local_hash
LOCAL_EMBEDDING_SIZE=384
```

如果你明确希望使用 OpenAI embedding，可改为：

```dotenv
EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=替换为OpenAI API Key
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
```

新写入的飞书消息、邮件和手动 `work_events` 会自动排队建立向量索引。历史数据可以在 Swagger 中执行：

```text
POST /api/vector/index-pending
```

语义搜索可以在 Swagger 中执行：

```text
POST /api/vector/search
```

请求示例：

```json
{
  "company_id": "替换为公司ID",
  "query": "最近哪些客户有回款或订单风险",
  "limit": 10
}
```

权限边界：

```text
管理员机器人问答：按 company_id 检索全公司向量上下文。
非管理员机器人问答：只按当前飞书 chat_id 检索当前会话的飞书消息。
```

如果 Qdrant 不可用，系统会跳过向量索引和语义搜索，继续使用关键词与最近事件检索。

## 数据库表

- `companies`: 公司
- `accounts`: 外部邮箱等账号，飞书邮箱优先走飞书 API
- `feishu_app_configs`: 飞书 App 配置
- `work_events`: 统一工作事件库
- `attachments`: 邮件和文档附件
- `extracted_items`: AI 抽取的任务、风险、决策
- `reports`: 日报、周报、跨公司总报
- `audit_logs`: 审计日志
- `resources`: V5 飞书/邮箱/本地导入等资源登记主表
- `resource_sources`: V5 数据来源身份表
- `resource_sync_runs`: V5 资源同步运行记录
- `sync_runs`: 旧同步运行记录和兼容统计
- `bot_user_access`: 机器人用户角色和访问范围
- `memory_facts`: 长期记忆事实
- `people` / `projects` / `customers`: 人、项目、客户基础实体

## 安全与合规

- 审计日志会脱敏 `password`、`secret`、`token`、`authorization` 等字段。
- `work_events.content_text` 会做基础邮箱和手机号脱敏。
- 生产环境建议进一步接入 KMS 或 Vault 加密 `accounts.credentials` 和 `feishu_app_configs.app_secret`。

## 迁移

```bash
docker compose exec api alembic upgrade head
```

生成新迁移：

```bash
docker compose exec api alembic revision --autogenerate -m "change message"
```

## 后续扩展建议

- 将飞书邮箱附件下载到 MinIO，并把附件正文也纳入检索。
- 扩展多维表格同步字段映射，把重点表配置固化到 V5 `resources` 和公司级同步策略。
- 进一步完善 user_access_token 的加密落库与自动刷新。
- 对 `accounts.credentials`、`app_secret` 做字段级加密。
- 增强管理后台的上线自检、同步演练和回滚演练页面。
