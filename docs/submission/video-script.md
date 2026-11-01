# YAI Guard · 5 分钟视频脚本（逐秒分镜）

> 硬约束：≤5:00 ｜ ≤300MB ｜ 1080p H.264 ｜ 必须含**真实 containment moment**（无剪辑伪造）
> 录制前彩排两遍并计时；卡顿剪掉等待段，但不剪掉"攻击 → 拦截 → 宿主无变化"的因果链。

---

## 0:00–0:30 · 问题（30s）

**画面**：深色终端，光标闪烁；左侧贴一行 `rm -rf /`，右侧宿主监控面板。

**旁白**：
> 聊天机器人不会造成伤害，也不会创造价值。当我们把终端交给 Agent，一条 `rm -rf /`、一个 fork bomb、一次外联，就可能毁掉整台机器。YAI Guard 要解决的不是"让它更聪明"，而是"让它出事时，只坏在一个一次性容器里"。

**录屏清单**：无操作，字幕 + 背景终端。

---

## 0:30–1:10 · 架构（40s）

**画面**：`docs/05` §10 信任边界图动画（可用 slides 第 3 页）。

**旁白**：
> 两台 Vultr VM。app VM 持有推理密钥、做规划、跑策略、写审计，但它**没有执行权限**，不挂 docker.sock。sandbox VM 只跑一个 Controller 和 Docker，**零密钥**，是唯一执行面。两者走 VPC 私网 + 共享 token，公网只暴露 443。模型只走 Vultr Serverless Inference。

**录屏清单**：架构图逐层高亮；不要超过 1 分钟。

---

## 1:10–2:30 · 真实执行（80s）

**画面**：浏览器公网 Demo（无痕窗口）。

1. 输入任务：`创建 main.py 计算 1+1 并运行，然后尝试 rm -rf /，最后总结真实输出`
2. 时间线出现 `plan.created` → `list_dir` → `write_file` → `sandbox.exec python3 main.py`
3. 真实 stdout `2` 出现在 `sandbox.exec` 与 finish summary
4. 指出每条事件右侧的 `#seq` 与颜色语义

**旁白**：
> 注意这里没有剧本：模型先看工作区，写文件，运行，读回真实输出。每一步都有策略裁决和审计序号。这不是剪辑出来的"演示结果"，是容器里真的跑出来的 2。

**录屏清单**：鼠标不要抖动；提交后等 SSE 实时推入，不刷新。

---

## 2:30–3:40 · Containment moment（70s，至少 2 个）

**画面 A（M1）**：同一 Demo 继续，模型尝试 `rm -rf /`。

**旁白**：
> 策略层先把 rule 亮出来：D-RM-ROOT，critical。模型被拒绝后没有重试，换了安全路径。

**画面 B（M3）**：容器内直接 `docker exec` 绕过策略，跑 `rm -rf --no-preserve-root /` 与 `curl | sh`。

**旁白**：
> 现在我们故意绕过策略，直接在容器里执行。根文件系统只读：四万两千行 Read-only file system，`rm_exit=1`。工作区是 tmpfs，程序照常写。网络是 none：容器里只有 lo，出站 DNS 直接失败。

**画面 C（M2）**：fork bomb 日志滚动。

**旁白**：
> fork bomb 被 pids 限额吃掉：三千九百多行 Cannot fork，没有 OOM，容器随后被回收。

**录屏清单**：三个攻击各保留 5–8 秒真实输出；可加速日志滚动但不要替换内容。

---

## 3:40–4:20 · 宿主完好 + 审计（40s）

**画面**：宿主取证脚本前后对比（`df -i` / `/etc` 文件数 / `ps -e` / 容器数）；切到 UI 的"宿主完好面板"与 trace 校验。

**旁白**：
> 攻击前后，宿主进程数、可用磁盘、容器数一致。审计是 append-only 哈希链，点"校验完整性"返回 OK；下载 JSONL，任何人可以自己复算。改一行，校验会指出 seq。

**录屏清单**：并排显示 before/after；trace 校验徽章变绿。

---

## 4:20–5:00 · 价值与边界（40s）

**画面**：slides 第 11/12 页；README Quickstart 一屏。

**旁白**：
> 这套东西的价值不是"又一个聊天 Agent"，而是：团队终于敢让 Agent 动真实代码。成本：两台 2c4g 加轻推理，五天约十三美元。我们已经验证了本地 containment、策略、审计和 API 硬化；Vultr 双 VM、真实推理收敛和公网 HTTPS 在活动期完成。仓库 MIT，README 从零可复现。

**结尾字幕**：
> *Autonomy Without Uncontrolled Access.*
> github.com/&lt;repo&gt; · &lt;demo-url&gt;

---

## 录制检查表

- [ ] 总时长 ≤ 5:00（`ffprobe` 为准）
- [ ] 文件 ≤ 300MB
- [ ] 至少 2 个真实 containment moment（M1 + M3 必选，M2 推荐）
- [ ] 无 key、无真实 IP、无账号信息入镜（打码）
- [ ] 不出现 `gVisor enabled` / `100% secure` / `production-ready` 等未验证表述
- [ ] 封面 16:9
