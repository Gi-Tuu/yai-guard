# 07 · 部署与成本证据（逐条 URL + 访问时间 + 原文摘录）

> 采集时间：**2026-10-31 23:18–23:42（UTC+8）**
> 采集方式：`read_page`（Firecrawl 渲染）/ `web_search` 摘要。凡是未直接读到原文的条目，标注「二手」（仅摘要可证）。
> ⚠️ 本文档只做核实与算术，**没有创建、注册、开通任何 Vultr 资源**。

## 0. 采集过程中的两个技术限制（影响证据强度，必须披露）

1. `https://www.vultr.com/pricing/` 单页超过渲染返回上限（返回内容在 Optimized Cloud Compute 段被截断于 ~50,000 字符），**Block Storage / Object Storage / Serverless Inference 三节未能从定价页直读**。改用对应产品页（`/products/block-storage/`、`/products/object-storage/`、`/products/cloud-inference/`）核实，这三页的价格表述与定价页同源，但**严格说不是「定价页原文」**（见条目 3、5、6）。
2. 直接用 `Invoke-WebRequest` 抓 `www.vultr.com` 被 Cloudflare 拦截（返回 “Just a moment...”，长度 0），所以无法做 HTML 级校验；所有证据均为渲染文本。

## 1. 计算价格（来源：定价页，已直读）

**URL**：https://www.vultr.com/pricing/
**访问**：2026-10-31 23:20 (+08:00)

| # | 机型 | 原文摘录 | 状态 |
|---|---|---|---|
| 1.1 | VX1 2vCPU/8GB（Block Storage 启动） | 「**2** vCPUs / **8** GB Memory / **5.00** TB Bandwidth / Block Storage Storage / **$0.060**/hr」 | 已核实 |
| 1.2 | VX1 2vCPU/8GB + 120GB NVMe | 「**2** vCPUs / **8** GB Memory / **5.00** TB Bandwidth / **120** GB NVMe Storage / **$0.076**/hr」 | 已核实 |
| 1.3 | VX1 计费不封顶 | 「VX1 instances are billed on actual hours used each month and are **not capped at 672 hours**.」 | 已核实（**关键**：30 天=720h 全额计费） |
| 1.4 | VX1 支持虚拟化 | 「dedicated CPU resources with full support for features such as **virtualization**」 | 已核实 |
| 1.5 | Cloud Compute Regular 2vCPU/4GB | 「**2** vCPUs / **4** GB Memory / **3.00** TB Bandwidth / **80** GB Storage / **$20.00**/mo / **$0.03**/hr」 | 已核实 |
| 1.6 | Cloud Compute Regular 4vCPU/8GB | 「**4** vCPUs / **8** GB Memory / **4.00** TB Bandwidth / **160** GB Storage / **$40.00**/mo / **$0.06**/hr」 | 已核实 |
| 1.7 | Cloud Compute High Performance 2vCPU/4GB | 「**2** vCPUs / **4** GB Memory / **5.00** TB Bandwidth / **100** GB Storage / **$24.00**/mo / **$0.036**/hr」（AMD 与 Intel 报价相同） | 已核实 |
| 1.8 | Cloud Compute High Performance 4vCPU/8GB | 「**4** vCPUs / **8** GB Memory / **6.00** TB Bandwidth / **180** GB Storage / **$48.00**/mo / **$0.071**/hr」 | 已核实 |
| 1.9 | Cloud Compute High Frequency 2vCPU/4GB | 「**2** vCPUs / **4** GB Memory / **3.00** TB Bandwidth / **128** GB Storage / **$24.00**/mo / **$0.036**/hr」 | 已核实 |
| 1.10 | Cloud Compute Regular 2vCPU/2GB（更省档） | 「**2** vCPUs / **2** GB Memory / **3.00** TB Bandwidth / **65** GB Storage / **$15.00**/mo / **$0.022**/hr」 | 已核实 |
| 1.11 | Cloud Compute High Performance 1vCPU/2GB | 「**1** vCPU / **2** GB Memory / **3.00** TB Bandwidth / **50** GB Storage / **$12.00**/mo / **$0.018**/hr」 | 已核实 |
| 1.12 | 小时价与月封顶关系 | 「All servers on your account are billed hourly up to the monthly rate cap. The hourly rate is determined by dividing the monthly rate by **672 hours (28 days)**.」 | 已核实（来自 Block Storage 页同段 FAQ，定价页同一计费模型） |

**与任务书口径的差异（必须纠正，别把两档混用）**：

- 任务书给的「Cloud Compute 2vCPU/4GB $0.036/hr」对应的是 **High Performance / High Frequency** 档（$24/mo）；**Regular Performance 同规格是 $0.030/hr（$20/mo）**。
- 任务书给的「Cloud Compute 4vCPU/8GB $0.060/hr」对应的是 **Regular Performance**（$40/mo）；**High Performance 同规格是 $0.071/hr（$48/mo）**。
- 两档都真实存在，但**不可混称**「Cloud Compute 2vCPU/4GB」；写成本表时必须注明 performance 档位。

## 2. 块存储（来源：产品页，非定价页原节）

**URL**：https://www.vultr.com/products/block-storage/
**访问**：2026-10-31 23:26 (+08:00)

- 「Block Storage volumes with NVMe SSD deliver high performance data throughput and IOPS to your instance, for just **$1 per 10 GB**.」
- 页面价格组件默认值：「**10** GB Selected → Vultr Block Storage **$1.00/month**」→ 折算 **$0.10/GB/月**。
- 「Starting at **$1.00 / month**」；「up to 40 TB of highly affordable **HDD**, or up to 10 TB of blazing fast **NVMe SSD**」。
- **HDD 档单价未在页面文本中给出 → 未核实**（本文所有估算一律用 NVMe $0.10/GB/月，属保守上界）。
- 关键风险原文（停止仍计费，Lead 已独立复核）：「instances in a **stopped state** continue to reserve dedicated system resources (RAM, SSD storage, IP aliases, vCPU) and therefore **incur charges until you destroy the instance**.」
  - 来源：https://docs.vultr.com/support/platform/billing/are-stopped-instances-still-billed-on-vultr （访问 2026-10-31）；FAQ 同义表述：https://www.vultr.com/resources/faq/
- 计费模型原文：「All servers on your account are billed hourly up to the monthly rate cap. The hourly rate is determined by dividing the monthly rate by 672 hours (28 days).」
- NVMe 性能上限：「10,000 IOPS and 400 MiB per second throughput」，可 60 秒 burst +50%。

## 3. 快照（来源：docs 产品概览页，已直读）

**URL**：https://docs.vultr.com/product-overviews/snapshots
**访问**：2026-10-31 23:34 (+08:00)

- 「Starting Price **$0.05 /GB/mo**」「**150 GB** Max」
- FAQ 原文：「Vultr charges **$0.05/GB per month** for the **compressed size** of your Snapshots.」
- 适用范围：「**Cloud Compute & GPU Only** — Available for Cloud Compute and Cloud GPU instances; not supported for Bare Metal.」
- 「Cross-Region Portability — Snapshots span all regions, so you can provision a new instance in any Vultr location using the same snapshot.」
- **VX1 能否手动快照：未核实**。VX1 文档只说「Automated Backups are currently disabled for VX1 instances」（见条目 5），未说明手动 snapshot 支持情况。

## 4. 带宽超额

**URL**：https://docs.vultr.com/support/platform/billing/what-is-the-bandwidth-overage-rate
**访问**：2026-10-31 23:36 (+08:00)

- 原文：「Bandwidth usage exceeding your plan's allocated quota is billed at an overage rate of **$0.01 per GB**.」
- 各档内含量（来自定价页，见 1.5–1.11）：Regular 2c4g = 3 TB；HP 2c4g = 5 TB；VX1 2c8g = 5 TB。
- 演示项目的实际出网量（页面/SSE/视频流）通常远低于 1 TB，**本文成本模型按 0 超额计**，并单列敏感性：超额 500 GB = **$5.00**。

## 5. 对象存储（来源：产品页，非定价页原节）

**URL**：https://www.vultr.com/products/object-storage/
**访问**：2026-10-31 23:28 (+08:00)

- 「Standard **$18 / TB / month**」「Premium $36 / TB / month」「Performance $50 / TB / month」「Accelerated $100 / TB / month」「Archival **$6 / TB / month**」
- 内含额度（FAQ 原文）：「File storage included: **1000 GB** / Outbound data transfer included: **1000 GB/month** / Additional outbound data transfer: **$0.01/GB**」
- 超出存储单价：「Standard Tier: ... additional storage will be priced at **$0.018/GB**」
- 免 API 请求费：「only charging for capacity and egress bandwidth, and not charging for API requests」

## 6. Serverless Inference（来源：产品页，非定价页原节）

**URL**：https://www.vultr.com/products/cloud-inference/
**访问**：2026-10-31 23:30 (+08:00)

- 原文大字价：「**$0.55/million input tokens**」「**$2.75/million output tokens**」
- 「Media inference may incur additional charges based on usage.」
- OpenAI 兼容 API 原文：「With Vultr Serverless Inference's **OpenAI-compatible API**...」
- 文档侧确认 base URL：`https://api.vultrinference.com/v1`（来自条目 7 的 OpenCode 配置示例）。
- **逐模型价格**：定价页 `#inference` 节未能直读；但 **已从公开模型目录 `GET https://api.vultrinference.com/v1/models` 的 `pricing` 字段逐一核实**（见 §15），逐模型价不再是「未核实」。
- FAQ 侧证据（只说去 Console 看模型选择器）：「navigate to the Serverless Inference section in the Vultr Console ... check the model selector」（https://docs.vultr.com/products/compute/serverless-inference/faq ，访问 2026-10-31 23:38）。
- → 成本模型**主口径改用 §15 的目录实价**，$0.55/$2.75 仅作保守上界（约 3.7×）。
- **最低消费 / 月费 / 是否可被 $200 credits 抵扣：未核实**。

## 7. 沙箱可行性（决定性证据：Vultr 官方写了 Microsandbox on VX1 的教程）

**URL**：https://docs.vultr.com/how-to-set-up-agent-sandboxing-on-vultr-cloud-compute
**访问**：2026-10-31 23:32 (+08:00)

- 架构原文：「Microsandbox is an open-source platform built for this, launching each sandbox as its own **microVM on `libkrun` over the Kernel-based Virtual Machine (KVM)**, with a **separate kernel from the host** rather than the shared-kernel isolation a container provides.」
- 前置条件原文：「Have access to a **Vultr VX1™ Cloud Compute** instance running **Ubuntu 24.04** as a non-root user with sudo privileges.」
- `/dev/kvm` 校验原文：「Microsandbox relies directly on KVM, so the host needs a CPU virtualization flag and a usable **`/dev/kvm`** device before the installer runs.」→ `ls -l /dev/kvm`
- `msb doctor` 期望输出（原文）：「✓ CPU virt **svm** / ✓ KVM device **/dev/kvm** / ✓ KVM access **read/write** / done Host setup is ready.」
- 安装：`curl -fsSL https://install.microsandbox.dev | sh`，装到 `~/.microsandbox`，`msb`/`microsandbox` 链接进 `~/.local/bin`。
- 沙箱间隔离原文：「Microsandbox **denies traffic between separate microVMs by default** ... Each sandbox reaches the public internet, but not its siblings, and the host needs no firewall rules to enforce this.」
- 资源限额示例：`msb create --name sandbox01 --cpus 1 --memory 256M debian`；`msb status/exec/logs/metrics/ssh/stop/remove`。
- **结论**：VX1 + Ubuntu 24.04 + `/dev/kvm` 是被官方文档背书过的组合，风险从「未知」降为「需在 ewr 实际验证一次 `msb doctor`」。

## 8. VX1 的区域与限制（直接影响拓扑与「同区 VPC」）

**URL**：https://docs.vultr.com/vultr-vx1-cloud-compute
**访问**：2026-10-31 23:40 (+08:00)

- 区域原文：「Currently available in **New Jersey (`ewr`)** with expansion regions coming soon.」
- 计划命名原文：「General Purpose (`vx1-g-`)」「Memory Optimized (`vx1-m-`)」；「All VX1 plans ending with `-###s`, for example `vx1-g-4c-16g-240s`, come with local NVMe」。
- 备份原文：「**Automated Backups are currently disabled for VX1 instances**」
- 其它限制：「**Windows** OS is currently not available for VX1 instances」「Block Type must be `high_perf` for bootable block devices」
- 虚拟化特性原文：「Supports additional CPU features including support for **virtualization**」
- **风险**：VX1 只在 ewr → 双 VM 方案里 app VM 也必须放 ewr（同区才能上同一 VPC）。**VX1 是否已扩区未核实**。

## 9. Firewall（可执行步骤的出处）

**URL**：https://docs.vultr.com/products/network/firewall-groups/management/rules
**访问**：2026-10-31 23:33 (+08:00)

- 定义原文：「Vultr Firewall rules enable traffic filtering using port numbers and source IP addresses for **incoming network requests**.」
- Console 步骤原文：Products → Network → Firewall → 选 group → 选 network type → Protocol 下拉选 profile 或 Custom + Port (or range) → Source 下拉选来源 + 填源 IP → Add note → **Add Firewall Rule**。
- **关键含义**：Vultr Firewall 只过滤**入站**，**出站/egress 必须在沙箱层做**（`--network=none`、容器 iptables、或 gVisor/microVM 网络栈）。这一点直接决定「沙箱不泄露」的实现位置。

## 10. 兑换 gift code（回答「是否需要绑卡」）

**URL**：https://docs.vultr.com/support/platform/billing/how-can-i-add-a-promotional-code
**访问**：2026-10-31 23:35 (+08:00)

- 「You can apply a valid promotional code from the **Gift Code** section of the Vultr Console.」
- 资格原文：「You must have a valid **Credit Card** or **PayPal** account linked to your Vultr profile.」
- 「**Only one promotional code is permitted per user.**」
- 「Promotional credits are **subject to expiration** and are **non-transferable**.」
- → 「是否需要绑卡」的答案：**官方文档口径是需要**（CC 或 PayPal）。这条要在 kickoff 确认 gift code 是否走同一通道。

## 11. 通知/预算提醒（官方只有三类通知，没有预算阈值提醒）

**URL**：https://docs.vultr.com/platform/other/manage-notifications
**访问**：2026-10-31 23:37 (+08:00，二手：web_search 摘要引用该页正文)

- 摘要原文：「Vultr provides customizable notifications to keep you informed about key events ... You can configure email notifications for **maintenance, outages, bandwidth usage, and new invoices**」
- 步骤原文：右上账号 → **Manage User** → Preferences → Notification Preferences → Edit → 配置 **Outage and Maintenance / Invoices / Bandwidth Usage** → Save Changes。
- **未发现「余额阈值/预算提醒」的官方功能 → 标记为「官方无原生预算提醒」**，本文改用 API 轮询自建（见主文档 §6.2）。

## 12. VPC 内网（二手，仅搜索摘要）

**URL**：https://www.vultr.com/features/advanced-networking/
**访问**：2026-10-31 23:41 (+08:00)｜**状态：二手**，未直读原文

- 搜索摘要原文：「Vultr does not charge for bandwidth between instances in a **VPC**.」
- 另有 VPC 产品/文档入口：https://www.vultr.com/products/vpc/ 、https://docs.vultr.com/products/network/vpc-networks

## 13. 未核实的清单（写进主文档「未决问题」，不许当事实用）

1. VX1 是否支持手动 Snapshot；VX1 是否已扩到 ewr 以外区域（文档写「currently ewr」）。
2. ~~Serverless Inference 逐模型 token 单价~~（**已核实，见 §15**）；但**是否有最低消费 / 月费、能否被 credits 抵扣、是否按区域（`sea`/`sjc`）分价**仍未核实。
3. gift code 的**有效期 / credits 到期日 / 是否覆盖对象存储与 Inference**。
4. HDD 块存储单价；VPC 是否完全免费（仅二手摘要）；VX1 block-boot 计划是否**额外**收启动卷费用（本文按含在 $0.060/hr 内估算，属乐观假设）。
5. 「停止实例是否仍计费」已核实为**是**（条目 2），但「VX1 + block boot 时销毁后 block volume 是否继续计费」未核实 → 收尾清单一律要求「实例 + block volume + snapshot 全部删除」。

## 14. 证据可信度分级

| 级别 | 含义 | 本文用到的地方 |
|---|---|---|
| A 已直读官方页 | 渲染文本直读，原文可引 | §1 定价页、快照、带宽、Microsandbox、VX1、Firewall、促销码 |
| B 官方产品页（非定价页） | 同源但非定价页原节 | 块存储、对象存储、Inference 单价 |
| C 搜索摘要二手 | 只有摘要，可能过期 | 通知偏好、VPC 不计费 |
| D 未核实 | 本文只标记，不计算 | 逐模型价、HDD 价、VX1 快照 |

## 15. Serverless Inference 逐模型目录价（**已核实，A 级**）

**URL**：https://api.vultrinference.com/v1/models （公开目录，无需鉴权，`GET` 返回 200）
**访问**：2026-10-31 23:47 (+08:00)｜返回体 68,832 字节，`data` 数组 35 个模型
**读取方式**：`Invoke-WebRequest` 直取 JSON，解析每个模型 `input_modalities[].pricing[]` 与 `output_modalities[].pricing[]`，字段 `unit = "token"`、`cost_usd` 单位为 **USD/token**。

| 模型 id | 输入 USD/token | 输出 USD/token | 折算 输入/1M | 折算 输出/1M | 上下文 | 区域 |
|---|---|---|---|---|---|---|
| `deepseek-v4.1-flash-1017` | 0.00000015 | 0.0000006 | **$0.15** | **$0.60** | 1,048,576 | sea |
| `glm-5.3-flash` | 0.0000001 | 0.00000035 | $0.10 | $0.35 | 1,048,576 | sea |
| `glm-5.3` / `glm-5.2` | 0.00000075 | 0.000003 | $0.75 | $3.00 | 1,048,576 | sea |
| `qwen3.8-flash-next` | 0.0000001 | 0.0000002 | $0.10 | **$0.20** | 262,144 | sea |
| `mimo-v2.6-flash` | 0.0000001 | 0.00000025 | $0.10 | $0.25 | — | — |
| `qwen3.8-27b` | 0.00000015 | 0.000001 | $0.15 | $1.00 | — | — |
| `minimax-m3.1-flash` | 0.0000002 | 0.0000009 | $0.20 | $0.90 | — | — |
| `nemotron-3.5-content-safety` | 0.00000005 | 0.00000015 | **$0.05** | **$0.15** | 131,072 | sjc |
| `qwen3-embedding-4b` | — | 0.00000005（embeddings） | — | $0.05 | 32,768 | sjc |

**结论**：

1. Lead 给的三组数字**全部复核一致**（`deepseek-v4.1-flash-1017` $0.15/$0.60、`glm-5.3-flash` $0.10/$0.35、`glm-5.3` $0.75/$3.00）。
2. 产品页头条价（$0.55/$2.75，证据 §6）≈ **目录里中高价模型的档位**，不是 DeepSeek Flash 的价；两者不可混用，混用会把推理成本高估 **3.7–5 倍**。
3. **最便宜的合规选择**：`nemotron-3.5-content-safety`（$0.05/$0.15）适合做每步策略/风险判定的小模型；主 planning 用 `deepseek-v4.1-flash-1017`；极端省钱可用 `qwen3.8-flash-next`（输出 $0.20/M，是目录里最低的输出价）。
4. **对成本结论的影响**：推理从「$5–60」降到「**$2–16**（5 天档）」，**推理不再是成本项，VM 时长与「忘记销毁」才是**。
5. `cost_usd` 是**字符串**（如 `"0.00000015"`），解析时必须转 decimal，禁止当 float 直接累加（浮点误差在百万 token 量级会放大）。

## 16. IPv4 / 额外 IP（复核 Lead 的问题 2）

**URL**：https://docs.vultr.com/support/products/network/are-reserved-ips-free
**访问**：2026-10-31 23:49 (+08:00)｜**状态：二手**（web_search 摘要引用该页正文，未直读）

- 摘要原文：「They are billed at **$0.004 per hour**, which amounts to **$3 per month**.」
- 定价页侧证据（§1）：最小档同时列出「$2.50/mo **IPv6 Only**」与「$3.50/mo」两行，说明**带 IPv4 是套餐内含项的默认形态**；Regular 2c4g（$20/mo）等档位未标注 IPv6-only。
- **本文采用口径**：每台实例自带 1 个 IPv4，**不额外计费**；只有「额外 IPv4 / Reserved IP」才可能计费，而本文方案每台只用 1 个 IP → **IPv4 成本 = $0**。
- **未核实**：Reserved IP 在「已绑定到运行中实例」时是否仍收 $3/mo（文档表述有歧义）→ 本文**不使用 Reserved IP**，规避该不确定性。

## 17. 「销毁 vs 关机」复核（Lead 的问题 2，A 级）

- 原文（见 §2）：「instances in a **stopped state** continue to reserve dedicated system resources (RAM, SSD storage, IP aliases, vCPU) and therefore **incur charges until you destroy the instance**. If you wish to no longer accumulate charges for a virtual machine, please use the **DESTROY** button in the customer portal.」
- **含义**：Vultr 没有「关机省钱」这回事。黑马松最常见的烧钱方式就是「当晚关机了，以为不花钱」。收尾动作只能是 **Destroy instance（+ 删除 block volume / snapshot / object storage）**。
- **未核实**：VX1 搭配 Block Storage 启动时，销毁实例后 block volume 是否独立继续计费（API 上 block 与 instance 是两个对象，**按最坏情况假设会继续计费**，因此清单一律要求两者都删）。

## 18. 复核结论汇总（Lead 三条线索）

| Lead 的说法 | 复核结果 | 处理 |
|---|---|---|
| Inference 目录价：deepseek $0.15/$0.60、glm-5.3-flash $0.10/$0.35、glm-5.3 $0.75/$3 | **一致，A 级**（§15） | 采纳为主口径 |
| VX1 $0.060 / $0.076；Regular 4c8g $0.060 | **一致，A 级**（§1） | 采纳 |
| Regular **2vCPU/4GB = $0.036/hr（$20/mo）** | **不一致**：定价页 Regular 2c4g 是 **$0.03/hr / $20/mo**；**$0.036/hr 是 High Performance 与 High Frequency 的 2c4g（$24/mo）**。$20 ÷ 672 = $0.0298 ≈ $0.03，与 $0.036 自相矛盾 | 主文档按 **$0.030（Regular）/ $0.036（HP）** 分列，不混称 |
| 带宽 3–5 TB/月内含 | **一致**：Regular 2c4g 3 TB；HP 2c4g 5 TB；VX1 2c8g 5 TB（§1） | 采纳 |
