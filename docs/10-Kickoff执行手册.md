# 10 · Kickoff 执行手册（11-03 23:00 → 11-04 02:30）

> 目标：**3 小时内**从"领到 gift code"走到"公网 HTTPS Demo 跑通一条真实任务"。
> 前提：报名已完成（lablab enroll + Discord + 1 人 team 选 Challenge 1）、全新邮箱、已绑卡/PayPal。
> **领取细节见 [`11-Credits领取手册.md`](11-Credits领取手册.md)**；时区有 1 小时口径冲突，**按 22:30 上线、11-09 03:00 提交执行**。
> 出问题时的降级路径在 §8；**任何一步卡超过 20 分钟就跳降级，不要硬扛**。

---

## 0. 现在（11-01）就要做完的 4 件事

- [ ] lablab **enroll 成功**（不是"注册账号"，是报名本赛事）
- [ ] Discord 已连接 lablab profile，已进 lablab Discord
- [ ] 已建 1 人 team，选择 **Challenge 1: Blast Radius Zero**
- [ ] 准备一个**从未注册过 Vultr 的邮箱** + 可用的 Credit Card 或 PayPal

> 报名在 kickoff 那一刻关闭，没有补报。其余一切都能在活动期内补。

---

## 1. 时间表（北京时间）

| 时间 | 动作 | 门禁 |
|---|---|---|
| 23:00–23:15 | 参加 kickoff，**记录 gift code**，确认 credits 到期日/覆盖范围 | 拿到 gift code || 23:15–23:30 | 注册 Vultr（新邮箱）→ 绑卡 → Billing → Gift Code → 确认 `$200.00` | 余额到账 |
| 23:30–23:50 | 创建两台 VM（同 region、同 VPC） | 两台可 SSH |
| 23:50–00:05 | 两个 Firewall Group + VPC 私网验证 | 私网互通、公网只开 443 |
| 00:05–00:25 | 两台装 Docker / Compose / git | `docker run hello-world` OK |
| 00:25–00:50 | sandbox VM：clone 仓库 → build 沙箱镜像 → 起 controller | `:9000/healthz` 私网可访问 |
| 00:50–01:15 | app VM：`.env` → compose up → Caddy 证书 | `https://<domain>/healthz` 200 |
| 01:15–01:35 | 冒烟：公网提交任务 → 真实容器执行 | Gate 0 |
| 01:35–02:05 | Serverless Inference 订阅 + key + Day-0 清单 | 推理可用、tool calling OK |
| 02:05–02:30 | 真实任务端到端 + 截图留证 | Gate 1（Demo 可演示） |

---

## 2. 创建 VM（两台，参数照抄）

| 参数 | `yai-app` | `yai-sandbox` |
|---|---|---|
| 规格 | Cloud Compute **Regular 2 vCPU / 4 GB** | 同左 |
| 区域 | 靠近你的位置（如 `nrt`/`sjc`/`ewr`） | **必须同 region** |
| 系统 | Ubuntu 24.04 LTS x64 | 同左 |
| SSH Key | 粘贴公钥 | 同左 |
| 网络 | 挂 VPC `10.99.0.0/24` | 同 VPC |
| IPv6 / 自动备份 / 额外 IP | 全关 | 全关 |

成本：两台 ≈ `$0.06/hr`，5 天 ≈ `$7.2`。

---

## 3. Firewall（先建组，再绑实例）

| 组 | 方向 | 协议/端口 | 来源 | 用途 |
|---|---|---|---|---|
| `yai-app-fw` | In | TCP 22 | `<你的IP>/32` | SSH |
| `yai-app-fw` | In | TCP 80 | `0.0.0.0/0` | ACME + 跳转 |
| `yai-app-fw` | In | TCP 443 | `0.0.0.0/0` | 公网 Demo |
| `yai-sandbox-fw` | In | TCP 22 | `<你的IP>/32` | 运维 |
| `yai-sandbox-fw` | In | TCP 9000 | `<app 私网IP>/32` | Controller |

验证：`nmap -Pn -p 9000 <sandbox公网IP>` 应全部 filtered。

---

## 4. 装 Docker（两台）

```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl git jq
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo tee /etc/apt/keyrings/docker.asc >/dev/null
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
| sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker $USER && newgrp docker
docker run --rm hello-world
```

顺手加固 SSH（**先确认 22 已限源 IP**）：禁用密码登录、`PermitRootLogin no`。

---

## 5. sandbox VM：镜像 + Controller

```bash
sudo mkdir -p /opt/yai-guard && sudo chown $USER:$USER /opt/yai-guard
git clone <你的公开仓库> /opt/yai-guard && cd /opt/yai-guard

# 沙箱镜像（构建需要外网；运行期不需要）
docker build -f deploy/sandbox/Dockerfile -t yaiguard/sandbox-py:1.0 .

# 环境
cat > .env <<'EOF'
SANDBOX_TOKEN=<openssl rand -hex 32 的结果>
SANDBOX_RUNTIME=runc
SANDBOX_IMAGE=yaiguard/sandbox-py:1.0
SANDBOX_PRIVATE_IP=<sandbox 私网 IP>
EOF
chmod 600 .env

docker compose -f deploy/compose.sandbox.yaml up -d
curl -s http://127.0.0.1:9000/healthz
```

> `SANDBOX_RUNTIME`：Day-1 再试 `runsc`；第一晚一律 `runc`，先保交付。

---

## 6. app VM：Caddy + API

```bash
IP=$(curl -s -4 ifconfig.me)
DOMAIN="${IP//./-}.sslip.io"
dig +short "$DOMAIN" | grep -q "$IP" && echo "domain ok: $DOMAIN"

cd /opt/yai-guard
cat > .env <<EOF
DOMAIN=$DOMAIN
ACME_EMAIL=you@example.com
VULTR_INFERENCE_API_KEY=<稍后填>
MODEL_PRIMARY=deepseek-v4.1-flash-1017
MOCK_MODEL=0
DEMO_TOKEN=<openssl rand -hex 16>
RATE_LIMIT_PER_MIN=10
SANDBOX_DRIVER=remote
SANDBOX_BASE_URL=http://<sandbox 私网 IP>:9000
SANDBOX_TOKEN=<与 sandbox 一致>
EOF
chmod 600 .env

docker compose -f deploy/compose.app.yaml up -d
docker compose -f deploy/compose.app.yaml logs -f caddy   # 等证书成功
curl -sS -o /dev/null -w '%{http_code}\n' "https://$DOMAIN/healthz"
```

`?` 说明：`Caddyfile` 用 `{$DOMAIN}` / `{$ACME_EMAIL}`，由 compose 注入。
> 这两个文件已在本机用官方镜像校验过（见 [`docs/evidence/deploy-config-validation-2026-11-01.md`](evidence/deploy-config-validation-2026-11-01.md)）；当晚只需替换域名与密钥。

---

## 7. Gate 0 冒烟（必须全绿）

```bash
# 1) 公网健康
curl -sS "https://$DOMAIN/healthz"

# 2) 公网访问 sandbox 必须失败
curl -m 5 -sS "http://<sandbox公网IP>:9000/healthz" || echo "BLOCKED(正确)"

# 3) 提交任务（带 demo token）
curl -sS -X POST "https://$DOMAIN/api/tasks" \
  -H "content-type: application/json" -H "X-Demo-Token: $DEMO_TOKEN" \
  -d '{"task":"run uname -n in the sandbox and show the real output"}' | jq .

# 4) 轮询状态 + 校验 trace
curl -sS "https://$DOMAIN/api/tasks/<id>" | jq .
curl -sS "https://$DOMAIN/api/tasks/<id>/trace/verify" | jq .
```

通过标准：healthz 200 / sandbox 9000 公网不可达 / 任务 `completed` / trace `{"ok": true}` / 浏览器无痕可打开 Demo。

---

## 8. 降级路径（按顺序）

| 故障 | 降级动作 | 耗时 |
|---|---|---|
| 双 VM 私网不通 | 先单 VM：`SANDBOX_DRIVER=docker`，app 本机跑容器；11-05 前再拆 | 10 min |
| Caddy 证书失败 | 先 `http://` 演示 + 视频里说明；或换 `nip.io`；避免反复重试触发 LE 限流 | 10 min |
| sandbox 镜像构建失败 | 先改用预构建的 `python:3.12-slim` + 启动时 `pip install`（仅演示用，记录风险） | 15 min |
| 推理 key 不可用 | 先用 Mock 模型把 Demo 跑通并录屏；key 恢复后只替换模型，不改 UI | 5 min |
| credits 未到账 | 自费 $5–10 先跑通（两台 2c4g 一天 < $2）；credits 到账后再扩 | 5 min |
| 时间超过 02:30 | **停止加功能**，只保"公网 200 + 一条真实任务 + trace" | — |

---

## 9. 成本与销毁（每天 23:50 检查）

```bash
curl -s -H "Authorization: Bearer $VULTR_API_KEY" https://api.vultr.com/v2/account \
  | jq -r '"balance=\(.account.balance) pending=\(.account.pending_charges)"'
docker ps -a --format '{{.Names}} {{.Status}}'
```

- **关机仍计费，只有 Destroy 停费**。
- 演示/提交结束后：Destroy 两台实例 → 删 block volume → 删 snapshot → 删对象存储。
- 若评审期仍需在线：降到单台 Regular 2c4g（$20/月封顶）+ 加支付方式。

---

## 10. 11-04 上午（Day-1）第一件事

1. `runsc` 探测：`docker info --format '{{json .Runtimes}}'`；能装就装，跑 `python3 -c "import numpy,pandas,matplotlib"`。
2. 在 sandbox VM 上跑 `deploy/evidence/host_fingerprint.sh` 前后对比，补上云端宿主证据。
3. 真实推理 Day-0 清单（`docs/02` §7 / `docs/evidence/WO-02-inference.md` §3）。
4. 把 Demo 任务真实跑 3 遍，录下第 1 条可用素材。

---

## 11. 绝对不要做

- 不要在 kickoff 夜重构架构或升级依赖。
- 不要把 `SANDBOX_TOKEN` / `VULTR_INFERENCE_API_KEY` 写进镜像、前端或 git。
- 不要让 controller 监听 `0.0.0.0` 对公网。
- 不要相信"关机省钱"。
- 不要等到 11-08 才录视频。
