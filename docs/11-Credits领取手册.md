# 11 · $200 Vultr Credits 领取手册

> 结论：**现在（11-01）还没有可领的 code。** 官方明确 "Gift code available at kickoff"——code 在 **11-03 kickoff** 公布，唯一官方兑换路径是 Vultr Console → Billing → Gift Code → 输入 code。没有任何公开的“提前领取”入口；第三方页面上的通用 $200 优惠码**不是**这个活动的码，用了会占掉“一人一码”的名额。
> 适用：YAI Guard / Vultr Agent Rush 2026 ｜ 信息来源：lablab 赛事页（2026-11-01 复核）+ Vultr 官方文档

---

## 0. 一页速查

| 项 | 内容 |
|---|---|
| 金额 | $200 / 人（每个合格参赛者） |
| 发放时点 | **kickoff（11-03）现场公布 gift code**；不是邮件自动发、不是 lablab 后台按钮 |
| 兑换入口 | `https://console.vultr.com/billing/?promo=HACKATHON` → **Gift Code** → 输入 → **Apply Gift Code** |
| 前置条件 | ① 全新 Vultr 账号（邮箱从未注册过 Vultr）② 邮箱已验证 ③ 已绑 **Credit Card 或 PayPal** ④ 该账号**从未用过任何 promo code** |
| 成功标志 | Billing 页 `Remaining Credit = $200.00` |
| 有效期 | 官方只说 "Promotional credits are subject to expiration and are non-transferable"；**具体到期日未公布**，kickoff 当场要问 |
| 覆盖范围 | Compute / Managed DB / Kubernetes 等 Vultr 服务；是否覆盖 Serverless Inference / 快照 / 对象存储需当场确认 |
| 一人一码 | 官方原文 "Only one promotional code is permitted per user" |

---

## 1. 为什么现在领不到

lablab 页面把领取拆成四步，第 2 步写死了时点：

1. 创建并验证新的 Vultr 账号
2. **在 11 月 3 日 kickoff 领取 gift code**
3. 在 Vultr Billing 兑换
4. 开始构建

Vultr 官方文档只规定“怎么兑换”，不提供活动码；活动码由主办方在 kickoff 现场给出。所以：
- 不需要现在注册 Vultr **也可以先注册并绑卡**（推荐提前做，见 §2）；
- 但**不要提前用任何 promo code**，否则“一人一码”名额被占，活动码可能兑换失败。

---

## 2. 现在就做的准备（10 分钟，避免 kickoff 夜卡住）

- [ ] **全新邮箱**：从未注册过 Vultr。检查方法：直接用该邮箱走一次注册流程，若提示已存在就换。
- [ ] **注册 Vultr**：`https://www.vultr.com/register/` → 验证邮件 → 进 Console。
- [ ] **绑卡 / PayPal**：Console → Billing → Payment Methods。官方兑换前置条件明确要求 "valid Credit Card or PayPal account linked"。
  - 没有卡会卡在兑换这一步；这是最常见的失败原因。
- [ ] **确认没有用过 promo code**：Billing → Gift Code 页面若显示无历史兑换，最好；若已用过，换一个新账号（但注意别违反一人一码）。
- [ ] **准备好记录 code 的地方**：kickoff 是北京时间深夜，code 只出现一次。

> 不要做的事：不要用 `FLYTWOHUNDRED` / `VULTRMATCH` 这类通用优惠码来“先试试”——它们与活动码共享“一人一码”名额。

---

## 3. kickoff 当晚：code 从哪里拿

官方只说 "shared during kickoff"，没有指定单一渠道。按优先级盯这 4 个地方：

| 渠道 | 链接 | 说明 |
|---|---|---|
| lablab 直播页 | `https://lablab.ai/ai-hackathons/vultr-hackathon/live` | 最可能显示倒计时/直播入口 |
| lablab Discord | `https://discord.gg/lablabai` | 看 `#announcements` / `#vultr` / kickoff 语音频道；Q&A 也在 Discord |
| lablab Twitch | `https://twitch.tv/lablabai` | 官方直播频道（页面有 Twitch 链接） |
| Vultr Community 事件帖 | `https://community.vultr.com/e/vultr-agent-rush-hackathon` | 主办方也可能在这里贴 code/说明 |

**时区注意（重要）**：lablab 页面同时出现两套时间口径——
- 直播页倒计时按 **11-03 15:00 UTC**（= 北京 23:00）；
- 赛事页 Agenda 写 **Nov 3 11:00 AM Eastern**，提交写 **Nov 8 6:00 PM Eastern**。11 月美国已转冬令时（EST, UTC-5），若按字面 EST 换算则是北京 **11-04 00:00** 开始、**11-09 07:00** 截止，比直播页晚 1 小时。

**行动建议**：**11-03 22:30（北京）就上线**，两套口径都覆盖；提交按更早的 **11-09 03:00** 完成，不赌那 1 小时。

---

## 4. 兑换（拿到 code 后 2 分钟）

1. 打开 **`https://console.vultr.com/billing/?promo=HACKATHON`**（这就是官方 "Hackathon Promotion Page"；也可以 Console → Billing → 找 **Gift Code** 区域）。
2. 在 **Gift Code** 输入框粘贴 kickoff 公布的 code（注意大小写/空格）。
3. 点 **Apply Gift Code**。
4. 回到 Billing 首页，确认 **Remaining Credit = `$200.00`**。
5. **截图存档**：余额页 + 兑换成功提示（提交材料/申诉都可能用到）。
6. 如果页面有到期日/适用范围说明，**立刻记下来**；没写就去 Discord 问。

---

## 5. 兑换成功后 30 分钟内做什么

按 `docs/10-Kickoff执行手册.md` 执行，顺序不要乱：

1. 创建 **Serverless Inference 订阅** → 复制 API key（先不跑大任务）。
2. 创建 **两台 VM**（app + sandbox，2vCPU/4GB，同 region）+ VPC + Firewall。
3. 装 Docker → 构建沙箱镜像 → 起 controller。
4. app 侧配 `.env` → compose up → Caddy 证书。
5. 跑 `docs/10` §7 的 Gate 0 冒烟。
6. 记一次成本基线：两台 VM ≈ $0.06/hr，5 天 ≈ $7.2。

---

## 6. 常见失败与处理

| 现象 | 原因 | 处理 |
|---|---|---|
| 提示 code 无效/过期 | code 输错、活动码已过期、或不是本活动码 | 回 kickoff 录屏/公告核对；在 Discord 问主办方 |
| 提示需要支付方式 | 未绑卡/PayPal | Billing → Payment Methods 绑定后重试 |
| 提示该账号已使用过 promo code | “一人一码”已被占用 | 用全新邮箱账号；不要在同一账号反复试 |
| 兑换成功但余额 $0 | 到账延迟 / 兑到了别的 promo | 等 5 分钟刷新；查 Billing History；开 support ticket |
| 不知道到期日 | 官方未公布 | kickoff 当场问；按“只覆盖活动期”做最坏打算（活动末降配/加支付方式） |
| 团队多人 | 每人各自领（each participant） | 每个人用自己的新邮箱/新账号；不要共用 |

**兜底渠道**
- lablab Help Center：`https://lablab.ai/help-center`
- lablab Discord：`https://discord.gg/lablabai`
- Vultr 支持工单：`https://console.vultr.com/support/create_ticket/`

---

## 7. 领到之后必须确认的 5 件事（kickoff 当场问）

1. credits 的**确切到期日**（活动结束就失效？还是 30 天？）。
2. 是否覆盖 **Serverless Inference** 用量（还是只覆盖 VM）。
3. 是否覆盖 **快照 / 对象存储 / 带宽超额**。
4. 活动结束后 Demo 还能在线多久；要不要提前加支付方式。
5. 团队多人的 code 是否各自独立、能否叠加。

---

## 8. 来源（2026-11-01 复核）

- lablab 赛事页「Access & credits → Claim your hackathon credits」：https://lablab.ai/ai-hackathons/vultr-hackathon
- lablab Live 页（倒计时/截止）：https://lablab.ai/ai-hackathons/vultr-hackathon/live
- 官方兑换入口：https://console.vultr.com/billing/?promo=HACKATHON
- Vultr 注册：https://www.vultr.com/register/
- Vultr 官方 promo code 规则：https://docs.vultr.com/support/platform/billing/how-can-i-add-a-promotional-code
- Vultr 支持工单：https://console.vultr.com/support/create_ticket/
- lablab Discord：https://discord.gg/lablabai
- 第三方汇总（仅参考，不当作规则）：https://that.promo/vultr-agent-rush-hackathon
