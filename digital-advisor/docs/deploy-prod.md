# Digital Advisor 云服务器部署

目标：在 Ubuntu 云服务器上部署 Digital Advisor，并通过 Caddy 自动签发 HTTPS。

## 服务器要求

- Ubuntu 22.04/24.04
- 2 vCPU / 4GB 以上，当前 2 vCPU / 8GB 足够
- 安全组开放 22、80、443
- 域名 A 记录指向服务器公网 IP

## 1. 安装基础依赖

```bash
apt update
apt install -y git curl ca-certificates
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker
```

## 2. 拉取代码

```bash
mkdir -p /opt
cd /opt
git clone https://github.com/Joon-chen/INNIVCE.git digital-advisor
cd /opt/digital-advisor
git checkout codex/digital-advisor-convergence
```

## 3. 配置环境变量

```bash
cp .env.prod.example .env
nano .env
```

必须修改：

- `ADVISOR_DOMAIN`
- `API_BASE_URL`
- `APP_SECRET_KEY`
- `ADMIN_API_TOKEN`
- `POSTGRES_PASSWORD`
- `MINIO_ACCESS_KEY`
- `MINIO_SECRET_KEY`
- `DEEPSEEK_API_KEY` 或其他 LLM Key
- 飞书相关 App 配置和事件配置

## 4. 启动服务

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

## 5. 查看状态

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f api
```

访问：

```text
https://你的域名/portal
```

## 6. 飞书后台配置

- 网页应用入口：`https://你的域名/portal`
- OAuth 回调：`.env` 中对应的 HTTPS 回调地址
- 事件订阅/机器人配置按现有应用配置填写

## 7. 更新部署

```bash
cd /opt/digital-advisor
git pull
docker compose -f docker-compose.prod.yml up -d --build
```

## 8. 回滚

```bash
cd /opt/digital-advisor
git log --oneline -5
git checkout <commit>
docker compose -f docker-compose.prod.yml up -d --build
```
