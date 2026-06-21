# V5 上线运行手册

本手册用于 V5 上线收口。系统定位是多公司企业数字参谋，飞书是主运营平台和主要数据来源。

## 架构守门

- 实时 Agent 操作：`Agent Runtime -> Tool Router -> Tool -> MCP -> CLI/API -> Feishu`
- 同步入库：`Sync Engine -> API Client -> Feishu -> PostgreSQL`
- 禁止 Agent Runtime 直接调用 CLI/API/MCP。
- 禁止 Sync Engine 调用 MCP。
- Tool Router 负责业务能力，MCP 负责工具调度，CLI 负责实时动作执行，API Client 负责同步入库。

## 上线前检查

```bash
cd digital-advisor
bash scripts/release_check_v5.sh
```

`lark-cli --help` 的关键命令至少应覆盖：`api`、`approval`、`attendance`、`base`、`calendar`、`contact`、`docs`、`drive`、`event`、`im`、`mail`、`minutes`、`okr`、`task`、`vc`、`wiki`。这对应 V5 的审批、知识、多维表格、消息邮件、日历、会议、自动化和人员工具族。

正式上线前再执行一次在线检查：

```bash
lark-cli doctor
```

`bot_identity`、`user_identity`、`endpoint_open`、`endpoint_mcp` 都应为 `pass`。CLI 版本可更新的 `warn` 不阻塞上线，但用户身份或 MCP endpoint 失败会影响 `--as user` 和实时工具调度。

Docker local-prod 镜像会安装固定版本 `@larksuite/cli`，并通过 `LARK_CLI_HOME` 把 CLI 配置只读挂载到应用容器；如果使用加密 appSecret，还会通过 `LARK_CLI_DATA_HOME` 挂载 `/root/.local/share/lark-cli`：

```bash
export LARK_CLI_HOME="${HOME}/.lark-cli"
bash scripts/local_prod_check_v5.sh
```

如果容器内 `doctor --offline` 显示 `config_file=pass` 但 `identity_ready=fail`，说明配置文件已挂载，但用户 token 或 appSecret 仍依赖 macOS Keychain，容器无法读取。此时 `/api/v5/os/overview` 必须降级为 `feishu_cli_identity_unavailable`，禁止误判为可上线。处理方式二选一：

- 在交互式 macOS Terminal 中明确执行 `lark-cli config keychain-downgrade`，把主密钥物化为本机文件，再把对应 lark-cli 支持文件按最小权限挂载进容器。
- 为生产容器单独初始化一套 bot/user CLI 配置，不要把个人 Keychain 依赖带进容器。正式上线以容器内 `lark-cli doctor --offline` 为准，宿主机 doctor 通过不能证明容器可上线。

推荐使用专用 CLI home，不依赖个人 Keychain：

```bash
export LARK_CLI_HOME="$PWD/.local/lark-cli-v5"
LARK_APP_ID="cli_xxx" scripts/prepare_lark_cli_home_v5.sh init-bot-container < app_secret.txt
scripts/prepare_lark_cli_home_v5.sh auth-user-start
# 完成浏览器授权后，按脚本输出的 LARK_DEVICE_CODE 执行 auth-user-complete
scripts/prepare_lark_cli_home_v5.sh check
bash scripts/local_prod_check_v5.sh
```

多家公司接入时，每家公司一个飞书 App、一个 CLI profile。推荐命名为 `company-<company_id>` 或公司稳定 slug，并通过 `LARK_PROFILE` 初始化：

```bash
export LARK_CLI_HOME="$PWD/.local/lark-cli-v5"
LARK_PROFILE="company-gaustek" LARK_APP_ID="cli_xxx" scripts/prepare_lark_cli_home_v5.sh init-bot-container < app_secret.txt
```

运行期必须对每次实时工具调用显式使用 `lark-cli --profile <company-profile>`，由公司当前 `FeishuAppConfig.settings.cli_profile` 决定。Agent Runtime 不允许从用户参数接收 `profile`、`cli_profile` 或 `lark_profile`；这些运行时参数只能由入口层根据公司 App 配置写入 `ToolContext`。禁止用 `lark-cli profile use` 在进程内切换默认 profile；默认 profile 是全局状态，容易让 A 公司请求误用 B 公司机器人身份。

初始化生产库中的公司和飞书 App 配置时，使用幂等 bootstrap 脚本；同一 `app_id` 重复执行会更新密钥、事件配置和 `settings.cli_profile`，不会重复创建 App：

```bash
export V5_COMPANY_NAME="固势"
export V5_COMPANY_CODE="gaustek"
export V5_FEISHU_APP_NAME="大飞哥"
export V5_FEISHU_APP_CONFIG_ID="<current-feishu-app-config-id>"
export V5_FEISHU_APP_ID="cli_xxx"
read -rsp "V5_FEISHU_APP_SECRET: " V5_FEISHU_APP_SECRET; echo
export V5_FEISHU_APP_SECRET
export V5_FEISHU_CLI_PROFILE="v5-local-prod"

docker compose -f docker-compose.local-prod.yml exec -T \
  -e V5_COMPANY_NAME \
  -e V5_COMPANY_CODE \
  -e V5_FEISHU_APP_NAME \
  -e V5_FEISHU_APP_CONFIG_ID \
  -e V5_FEISHU_APP_ID \
  -e V5_FEISHU_APP_SECRET \
  -e V5_FEISHU_CLI_PROFILE \
  api python scripts/bootstrap_feishu_app_config_v5.py
```

后续新增其他公司时，重复以上流程并替换 `V5_COMPANY_CODE`、`V5_FEISHU_APP_CONFIG_ID`、`V5_FEISHU_APP_ID`、`V5_FEISHU_APP_SECRET` 和 `V5_FEISHU_CLI_PROFILE`。每家公司使用独立 CLI profile；不要复用固势的 profile。

必要时再跑全量测试：

```bash
.venv312/bin/pytest
```

## 启动验证

```bash
docker compose -f docker-compose.local-prod.yml up -d --build
docker compose -f docker-compose.local-prod.yml exec api python scripts/seed_demo.py
curl http://localhost:8000/health
curl http://localhost:8000/api/v5/os/overview
```

`/api/v5/os/overview` 必须返回 `release_readiness`。数据库已连接、大飞哥 AI 兜底开启且容器内 `lark-cli doctor --offline` 身份可用时状态应为 `ready`；数据库暂不可用、CLI 缺失或 CLI 身份不可用时可为 `degraded`，但 `feishu_bot`、`admin_console`、`feishu_cli` 和 `feishu_boundary` 检查仍应可读。

打开管理后台：

```text
http://localhost:8000/console
```

数据库不可用时，后台首屏应仍可读，公司下拉显示“数据库未连接”，且只请求 `/api/v5/os/overview`。

## 机器人验收

- HTTP 事件订阅：确认 URL verification、普通消息、自然语言兜底。
- WebSocket 长连接：确认 `feishu-ws` 能收到消息事件。
- 大飞哥固定命令：`帮助`、`最近审批`、`待办事项`。
- 大飞哥自然语言：例如“今天公司有什么风险？”
- 审批卡片：展开详情、通过/拒绝 dry-run、确认令牌。
- 未知卡片动作：系统日志应出现 `gateway.feishu.card_action`，reason 为 `unhandled_card_action`。

## 管理后台验收

- 首屏显示多公司运营状态、大飞哥 AI 兜底状态和数据服务状态。
- 工具页按 9 个 Tool 家族检查：ApprovalTool、KnowledgeTool、BitableTool、ChatTool、CalendarTool、MeetingTool、ReportTool、AutomationTool、PeopleTool。
- Provider 边界：Feishu API 不作为实时写执行层，Feishu MCP 不直接执行动作，CLI 为动作执行器。
- 写工具必须先 dry-run，再使用同参 `confirmation_token` confirmed 执行。
- 审计页能筛选工具执行日志、写目标摘要、Gateway reason 和“卡片未处理”。

## 同步与知识验收

- Sync Engine 只走 API Client，不调用 MCP。
- L1 热知识进入 Document Store / Vector DB / RAG 指标。
- L2 冷知识只登记 token、标题、链接、所属空间，需要时通过 Feishu CLI 实时搜索。
- L3 外部知识默认不入库。

## 首批用户范围

- Owner / 系统管理员：可查看多公司视图、工具配置、审计日志和写工具 dry-run/confirmed。
- 公司负责人：只开放授权公司和授权业务域。
- 普通员工：只开放个人智能体视角和当前会话/个人授权范围。

## 已知风险

- 生产数据库未连接时，后台只能展示离线可读首屏，不能执行同步或写操作。
- `lark-cli doctor --offline` 只能验证本地配置和身份缓存，不能替代线上 Feishu endpoint 连通性测试。
- 旧 `feishu_resources` raw SQL 迁移兜底需等 0019 发布迁移确认后再删除。
- 写工具真实执行依赖飞书权限范围和 user/bot identity，必须按公司逐个演练。

## 回滚

```bash
docker compose -f docker-compose.local-prod.yml logs api worker beat feishu-ws --tail=200
docker compose -f docker-compose.local-prod.yml down
git status --short
```

数据库迁移回滚只允许在确认业务数据影响后执行：

```bash
docker compose -f docker-compose.local-prod.yml exec api alembic current
docker compose -f docker-compose.local-prod.yml exec api alembic downgrade -1
```

回滚后必须重新验证：

```bash
curl http://localhost:8000/health
curl http://localhost:8000/api/v5/os/overview
```
