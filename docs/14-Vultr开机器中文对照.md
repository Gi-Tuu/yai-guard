# 14 · Vultr 开机器中文对照（今晚照着点）

> 用途：给非技术执行者的一页式对照。**看不懂英文页面就整页截图发我**，我告诉你点哪个按钮。
> 今晚时间：**23:00（北京）kickoff**；code 当场公布；提交截止 **11-09 06:00（北京）**。
> 原则：先开机器、先跑通；不接浏览器扩展、不改代码。

---

## 0. 现在先做 3 件事（10 分钟）

- [ ] **绑卡**：Vultr Console → **Billing**（账单）→ **Payment Methods**（支付方式）→ 添加 Credit Card 或 PayPal。
- [ ] **加 SSH key**：**Account → SSH Keys → Add SSH Key**，Name 填 `yai-guard`，粘贴公钥。
  - 公钥路径：`C:\Users\<你>\.ssh\id_ed25519.pub`
  - 复制命令：`Get-Content "$HOME\.ssh\id_ed25519.pub" | Set-Clipboard`
  - 私钥 `id_ed25519` 绝不外发。
- [ ] **确认没兑换过任何 promo code**：Billing → Gift Code 页面；用过就换新账号（一人一码）。

---

## 1. 23:00 拿 gift code

官方原文：**Gift code available at kickoff**。现在没有是正常的。

盯这 3 个地方（看到一串 `AGENTRUSH-…` 的字符就是它）：

1. https://lablab.ai/ai-hackathons/vultr-hackathon/live
2. Discord：https://discord.gg/lablabai（`#announcements` / kickoff 语音）
3. Twitch：https://twitch.tv/lablabai

**23:10 还没看到**：在 Discord 问一句
`Where can I find the gift code?`

---

## 2. 兑换（2 分钟）

打开：**https://console.vultr.com/billing/?promo=HACKATHON**

| 英文 | 中文 | 操作 |
|---|---|---|
| Gift Code | 礼品码 | 粘贴 code |
| Apply Gift Code | 应用兑换码 | 点击 |
| Remaining Credit | 剩余额度 | 确认 **$200.00** |

> 不要先兑换 `FLYTWOHUNDRED` / `VULTRMATCH` 等通用码，会占掉活动码名额。

---

## 3. 开两台 VM（照抄参数）

路径：**Products → Deploy → Cloud Compute**

| 选项（英文） | 中文 | 选择 |
|---|---|---|
| Cloud Compute | 云计算 | 选它 |
| **Regular Performance** | 普通性能 | **选这个**（不要选 High Frequency / Optimized） |
| 2 vCPU / 4 GB | 2 核 4G | 选它（约 $0.03/hr） |
| Region | 区域 | 选离你近的（东京/新加坡/西雅图），**两台必须同区域** |
| Operating System | 系统 | **Ubuntu 24.04 LTS x64** |
| SSH Keys | SSH 密钥 | 勾选 `yai-guard` |
| IPv6 | IPv6 | 关 |
| Auto Backups | 自动备份 | 关（要钱） |
| Additional IP | 额外 IP | 不选（要钱） |
| Hostname | 主机名 | `yai-app` / `yai-sandbox` |

点 **Deploy Now**，等 1–2 分钟，状态变 **Running**。

> 两台成本 ≈ $0.06/hr，5 天 ≈ $7.2；$200 额度足够。

---

## 4. 防火墙（Products → Network → Firewall）

建两个组，再绑到对应实例（实例 → Settings → Firewall）。

| 组 | 方向 | 端口 | 来源 | 用途 |
|---|---|---|---|---|
| `yai-app-fw` | In | 22 | `<你的公网IP>/32` | SSH |
| `yai-app-fw` | In | 80 | `0.0.0.0/0` | 证书/跳转 |
| `yai-app-fw` | In | 443 | `0.0.0.0/0` | 公网 Demo |
| `yai-sandbox-fw` | In | 22 | `<你的公网IP>/32` | SSH |
| `yai-sandbox-fw` | In | 9000 | `<app 私网IP>/32` | Controller |

公网 IP 获取：`curl.exe -4 -s https://ifconfig.me`。

---

## 5. VPC（两台私网互通）

**Products → Network → VPC Networks → Add VPC**：同区域，网段 `10.99.0.0/24`。
建好后在**两台实例的 Settings → VPC** 里挂上同一个 VPC，记下两台私网 IP。

---

## 6. 中英对照小抄

| 英文 | 中文 |
|---|---|
| Products | 产品 |
| Deploy / Deploy New Server | 部署 / 部署新服务器 |
| Instances | 实例 |
| Cloud Compute | 云计算 |
| Billing | 账单 |
| Payment Methods | 支付方式 |
| Gift Code | 礼品码 |
| Remaining Credit | 剩余额度 |
| SSH Keys | SSH 密钥 |
| Firewall | 防火墙 |
| VPC Network | 私有网络 |
| View Console | 网页控制台 |
| Settings | 设置 |
| Destroy | 销毁（**只有销毁才停止计费**，关机仍计费） |

---

## 7. 开好机器后发我什么（不要发敏感信息）

可以发：
- 两台实例的 **公网 IP**、**私网 IP**、**region**
- `https://<domain>/healthz` 的返回
- 任何报错文本 / 整页截图

**不要发**：Vultr 密码、银行卡、API key、私钥内容。

---

## 8. 卡住怎么办

| 现象 | 处理 |
|---|---|
| 页面看不懂 | 整页截图发我 |
| 兑换提示需要支付方式 | Billing → Payment Methods 绑卡后重试 |
| code 无效 | 回 kickoff 录屏核对；Discord 问主办方 |
| SSH 连不上 | 检查防火墙 22 来源 IP；用 **View Console** 网页终端 |
| 创建实例失败 | 换区域/换 2GB 规格；截图发我 |
| 时间超过 00:30 | 先只保一台 app VM + 单 VM 模式（`SANDBOX_DRIVER=docker`），先有公网 Demo |
