# WO-04 · Vultr 双 VM 开通、部署、TLS、防火墙

> 派给：运维/部署 ｜ 估时：6h ｜ 依赖：WO-01 镜像、kickoff 的 $200 credits ｜ 可开工：**11-03 23:00 后**
> 先读：`docs/07` 全文、`docs/05` §10、`deploy/`

## 0. 背景

目标：90 分钟内把"app VM + sandbox VM + VPC + HTTPS"跑起来，公网只暴露 443；sandbox 的 9000 只对 app 私网 IP 开放；两台机器重启后自动恢复。

## 1. 交付物

| 文件/动作 | 内容 |
|---|---|
| 两台 VM | `yai-app` 2vCPU/4GB、`yai-sandbox` 2vCPU/4GB，同 region、同 VPC `10.99.0.0/24`，Ubuntu 24.04 |
| Firewall | app：22（限你的 IP）/80/443；sandbox：22（限 IP）/9000（仅 app 私网 IP），其余全拒 |
| `deploy/compose.app.yaml` | api + caddy；`deploy/compose.sandbox.yaml` | controller + docker.sock |
| `deploy/Caddyfile` | `{$DOMAIN}` 自动 TLS、SSE 不缓冲、安全头 |
| systemd | 两台各一个 unit，开机 `docker compose up -d` |
| `docs/evidence/WO-04-deploy-log.md` | 每一步命令 + 输出 + 截图 + 公网 URL |

**部署后必须验证**
1. `https://<domain>/healthz` → 200，证书有效。
2. 从公网访问 `http://<sandbox-public-ip>:9000/healthz` → **超时/拒绝**。
3. 提交一个任务，真实在 sandbox VM 的容器里执行（`uname -n` 与 app VM 不同）。
4. `ssh yai@<sandbox>` → `docker ps` 能看到一次性容器；任务结束后容器消失。
5. 两台 reboot 后服务自动恢复。

## 2. 验收

```bash
curl -sS -o /dev/null -w '%{http_code} %{ssl_verify_result}\n' "https://$DOMAIN/healthz"   # 200 0
curl -m 5 -sS "http://$SANDBOX_PUBLIC_IP:9000/healthz" || echo "BLOCKED(正确)"
curl -sS -X POST "https://$DOMAIN/api/tasks" -H 'content-type: application/json' \
  -d '{"task":"run uname -n in the sandbox"}' | jq .
# 浏览器无痕打开 Demo，跑完整演示任务
```

## 3. 写文件范围

`deploy/compose.app.yaml`、`deploy/compose.sandbox.yaml`、`deploy/Caddyfile`、`.env.example`、`docs/evidence/WO-04-*`。
**不要改 `backend/` 下任何 Python**；缺环境变量找 WO-02/WO-06 加。

## 4. 禁止

- controller 不得监听 `0.0.0.0` 对公网；只能用 VPC 私网 IP。
- sandbox VM 上不得出现 `VULTR_INFERENCE_API_KEY`（用 `env | grep -i vultr` 自证为空）。
- 不得创建额外收费资源（负载均衡、对象存储、快照滥用）。
- 演示结束按 `docs/07` §6.3 清单 **Destroy**，关机不等于停费。

## 5. 接口契约

- app 通过 `SANDBOX_BASE_URL=http://<sandbox-private-ip>:9000` + `SANDBOX_TOKEN` 访问 controller。
- controller 的 token 与 app 的 `SANDBOX_TOKEN` 必须一致；**不要**把 token 写进镜像。
- 域名方案先 `sslip.io`（`<ip-with-dashes>.sslip.io`），有自有域名再换；域名必须在起 Caddy 前定死。

## 6. 坑

- Vultr 防火墙只管**入站**；出站必须靠容器 `--network=none`。
- 先限 22 端口到你的 IP，再改 sshd，否则会把自己锁在外面。
- Docker 装完要 `usermod -aG docker $USER` + 重新登录。
- Caddy 申请证书失败会限流；域名解析不通就别起。
- 记账：两台 2c4g ≈ $0.06/hr，5 天约 $7.2；`credits` 到期日 kickoff 当晚问清。
