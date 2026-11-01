# 部署配置本地校验（2026-11-01）

> 目的：在 kickoff 之前把 `deploy/` 的 Caddyfile 与两个 compose 文件先“编译”一遍，避免 11-03 深夜才发现缩进/变量/挂载写错。
> 环境：Docker Desktop 29.7.2（Linux 容器），Windows 11。

## 1. Caddyfile

```bash
docker run --rm \
  -e DOMAIN=test.sslip.io -e ACME_EMAIL=test@example.com \
  -v "$PWD/deploy/Caddyfile:/etc/caddy/Caddyfile:ro" \
  caddy:2 caddy validate --config /etc/caddy/Caddyfile
```

```text
{"msg":"adapted config to JSON","adapter":"caddyfile"}
{"msg":"Caddyfile input is not formatted; run 'caddy fmt --overwrite'","level":"warn"}
{"msg":"server is listening only on the HTTPS port ... adding one to enable TLS"}
{"msg":"enabling automatic HTTP->HTTPS redirects"}
Valid configuration
```

- **结果：有效**。
- 唯一的 warn 是格式建议（缩进），不影响启动；如需消除：`caddy fmt --overwrite deploy/Caddyfile`。
- TLS 自动配置、HTTP→HTTPS 跳转、反代 `api:8000` 均被识别。

## 2. compose 文件

```bash
# app：需要仓库根目录有 .env；校验时用 .env.example 临时复制，校验后删除
cp .env.example .env
docker compose -f deploy/compose.app.yaml config --quiet   # exit 0
rm .env

# sandbox：需要 SANDBOX_TOKEN / SANDBOX_PRIVATE_IP
SANDBOX_TOKEN=x SANDBOX_PRIVATE_IP=10.99.0.20 \
  docker compose -f deploy/compose.sandbox.yaml config --quiet   # exit 0
```

- **两个 compose 均通过 `config --quiet`（exit 0）**：变量、挂载、端口、depends_on 均可解析。
- 注意：`compose.app.yaml` 的 `env_file: ../.env` 要求部署时仓库根目录存在 `.env`；kickoff 手册 §6 已包含创建步骤。

## 3. 结论

- 部署文件不是“看起来对”，而是被 Caddy 与 Docker Compose 官方解析器接受。
- 仍需在 Vultr 上验证的只剩：真实 TLS 签发、VPC 路由、firewall 生效、真实推理 key。
