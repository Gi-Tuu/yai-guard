# YAI Guard · Slides 大纲（10–12 页）

> 规格：16:9 ｜ 每页 1 标题 + 3–5 bullet ｜ 建议截图位置已标注
> 原则：只讲已验证的能力；gVisor / 公网 HTTPS / 真实推理在 Day-1–Day-3 才补截图

---

## 1. 封面

**YAI Guard — Autonomy Without Uncontrolled Access**

- Vultr Agent Rush · Challenge 1「Blast Radius Zero」
- 一个会真实执行、但爆炸半径可控的自主 Agent
- 一句话：*An agent that only chats is a demo; one that executes safely is a product.*

> 视觉：深色终端风，左侧产品名，右侧一行 `rm -rf /` 被红色 DENY 的截图。

## 2. 问题：Agent 会执行，但爆炸半径不可控

- 聊天 Agent 不会造成伤害，也不产生价值
- 一旦给它终端：一条 `rm -rf /`、一个 fork bomb、一次外联就可能毁掉宿主
- 常见做法是"命令黑名单"——可被编码/拼接/包装器绕过
- 赛题要的不是更多功能，而是**可证明的隔离**

> 截图：策略规则匹配 `sh -c "rm -rf /"` 的高亮。

## 3. 架构：双 VM + 单向信任

- `yai-app`：密钥、Planner、策略、审计、Web（**无执行权限**）
- `yai-sandbox`：Sandbox Controller + Docker（**零 LLM 密钥**，唯一执行面）
- `Vultr Serverless Inference`：唯一模型来源（DeepSeek V4.1 Flash）
- VPC 私网 + token；公网只暴露 443；沙箱 VM 可整机销毁

> 图：`docs/05` §10 的信任边界图；标注"app 不挂 docker.sock"。

## 4. 执行闭环：每一步都过闸门

- 模型产出 tool_call → **策略评估** → 允许 / 拒绝 / 审批
- 允许才进入沙箱执行；拒绝把 rule + reason 回灌模型，不重试
- 每步写 hash-chain 审计：plan / policy / exec / approval
- 任务结束一次性销毁容器（lifecycle discipline）

> 截图：Web 时间线按 STEP 分组 + 颜色语义（deny 红 / approval 黄 / exec 蓝 / done 绿）。

## 5. Containment 证据 #1：只读根

- `rm -rf --no-preserve-root /` → 42,349 行 `Read-only file system`
- `/workspace`（tmpfs）仍可写：程序照常运行
- 根文件系统纹丝不动，`rm_exit=1`

> 截图：M1 输出（首尾各 3 行 + 行数），旁边放宿主进程数前后一致。

## 6. Containment 证据 #2：fork bomb

- `b(){ b|b& }; b` → 3,978 行 `Cannot fork`
- pids 限额（64）把攻击吸收在容器内；无 OOM
- 容器被回收，宿主负载无跳变

> 截图：`docker logs` 与 `docker ps -a` 的回收状态。

## 7. Containment 证据 #3：零网络

- `--network=none`：容器内只有 `lo`
- 出站 `Temporary failure in name resolution`
- 策略层同时拒绝 `169.254.169.254` / 私网 IP（第一道门）
- `curl … | sh` 被 `D-CURL-PIPE-SH` 拒绝

> 截图：M3 输出 + 策略 deny 卡片（rule + reason）。

## 8. 审计：可验证，不只是"有日志"

- append-only JSONL + SHA-256 链，篡改定位到 `seq`
- `GET /api/tasks/{id}/trace/verify` 返回 `{ok, bad_seq}`
- 下载 trace → 第三方可自行复算
- 脱敏在哈希前执行：key 不落盘

> 截图：UI 里 "OK · 30 events" 徽章；旁边是 trace 表。

## 9. API 硬化：公网 Demo 不被刷

- `X-Demo-Token` 鉴权（401）
- 每 IP 10 req/min（429 + Retry-After）
- 全局并发 = 1（第二个任务直接 429）
- SSE 15s 心跳

> 截图：curl 的 401/429 输出（打码 token）。

## 10. 策略 + 容器：纵深防御，不混淆职责

- 策略：68 条红队语料，deny 48 / approval 13 / allow 7
- 已知绕过（编码、两段式、Python 任意代码）写进文档
- **命令过滤不是安全保证**；容器边界才是兜底
- 误拦可接受（fail-closed），漏拦不可接受

> 截图：`bypass.jsonl` 片段 + 测试全绿输出。

## 11. 价值与适用场景

- 谁需要：任何让 Agent 动生产代码/数据的团队
- 价值：把"敢不敢让它跑"变成"跑坏了也只坏在一个一次性容器里"
- 成本：双 VM 2c4g + 轻推理，5 天约 $13（占 $200 credits 的 6.6%）
- 可复制：镜像 + compose + Caddy，90 分钟起一套

> 截图：成本表（引用 docs/07 口径与日期）。

## 12. 边界与下一步

- 已验证：本地 Docker containment、策略、审计、API 硬化、UI
- 待验证（Day-0–Day-3）：Vultr 双 VM、真实推理收敛、公网 HTTPS、runsc 兼容
- 不做：浏览器自动化、多 Agent、外部 LLM、生产 SLA
- 仓库：README 从零可复现，MIT

> 截图：README Quickstart + MIT badge。
