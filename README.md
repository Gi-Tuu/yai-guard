# YAI Guard

> **Secure Autonomous Execution Agent** — 让 AI 自主执行真实任务，同时让爆炸半径可控。
> 参赛项目：**Vultr Agent Rush Hackathon · Challenge 1「Blast Radius Zero」**（线上 2026-11-03 – 11-08）

**当前状态（2026-11-01）：技术预研完成 + 骨架 + 波次 1 工单落地。**
策略引擎、审计 trace、Agent 循环、Web/API、沙箱抽象与部署文件已实现；**72 个单测全绿**（含 4 个真实 Docker 集成测试），离线演示与真实 Docker 端到端演示的哈希链校验通过；本地已构建沙箱镜像并实测 M1/M2/M3 containment。
**未部署任何收费资源、未调用真实推理 API、未修改 YAI 主仓库。**

---

## Quickstart（不需要 key / 本地 Docker 可选 / 不花钱）

```bash
# 1) 72 个单测（Docker 测试在无 daemon / 无镜像时自动 skip）
python -m unittest discover -s backend/tests -t backend -v

# 2) 离线端到端演示：Mock 模型 + Fake 沙箱 + 真实策略 + 真实 trace
python backend/demo_offline.py

# 3) 红线检查：除沙箱层外禁止宿主机执行
python scripts/check_no_host_exec.py

# 4) 真实 Docker containment（可选，需本地 daemon）
docker build -f deploy/sandbox/Dockerfile -t yaiguard/sandbox-py:1.0 .
python -m unittest discover -s backend/tests -t backend -p "test_docker_backend.py" -v

# 5) 真实 Docker 端到端（Mock 模型 + 真容器 + 真策略 + 真 trace）
python backend/demo_docker.py

# 5b) 演示路径冒烟（API + SSE + 审批 + 真容器 + trace 校验，需 Docker）
python scripts/smoke_demo.py

# 5c) 控制面镜像冒烟（可选）
docker build -f backend/Dockerfile -t yaiguard/app:0.1 .
docker run --rm yaiguard/app:0.1 python -c "from app.main import app; print(app.title)"

# 6) 本地 Web（需要 pip install -r backend/requirements.txt）
cd backend && MOCK_MODEL=1 SANDBOX_DRIVER=fake uvicorn app.main:app --port 8000
```

演示会打印：`allow → deny(rm -rf /) → approval → deny(curl|sh) → finish`，并生成 `artifacts/demo-trace.jsonl`（`demo_docker.py` 生成 `artifacts/e2e-docker/trace.jsonl`，可用 `verify()` 验证未被篡改）。`smoke_demo.py` 会自动启动 API、批准一次审批并断言 7 项检查全 PASS。

## 提交素材（lablab 直接上传）

| 文件 | 用途 |
|---|---|
| [`docs/submission/cover.png`](docs/submission/cover.png) | lablab Cover image（1920×1080，16:9），评委第一眼看到的项目图 |
| [`docs/submission/YAI-Guard-slides.pptx`](docs/submission/YAI-Guard-slides.pptx) | lablab Slide presentation（12 页，含中文演讲备注，录视频时可当提词器） |
| [`docs/submission/submission-copy.md`](docs/submission/submission-copy.md) | 标题 / 短描述 / 长描述 / tags，按字符上限预先写好 |
| [`docs/submission/video-script.md`](docs/submission/video-script.md) | 5 分钟逐秒分镜与旁白 |

重新生成封面与 slides：`python scripts/build_submission_assets.py`（离线、可重复；改文案后重跑）。
实现细节与偏差说明见 [`docs/08-实现方案与骨架说明.md`](docs/08-实现方案与骨架说明.md)。

---

## 一句话

> *Autonomy Without Uncontrolled Access.*

用户用自然语言提任务 → Agent 自主规划 → 在 Vultr 上的**隔离沙箱**里真实执行终端命令与文件操作 → 每一步都受**确定性策略**约束、都被**审计链**记录；当它试图 `rm -rf /`、fork bomb、外联时，系统**真的拦住**，并当场证明宿主机毫发无伤。

这不是"又一个聊天 Agent"，卖点是 **containment（可证明的隔离）**：赛题原文 *"An agent that only chats is a demo; one that executes safely is a product."*

---

## 赛事硬信息（已核实，2026-10-31）

| 项 | 内容 |
|---|---|
| 报名关闭 | **2026-11-03 15:00 UTC = 北京时间 23:00**（与 kickoff 同时，错过无法补报） |
| 提交截止 | **2026-11-08 22:00 UTC = 北京时间 11-09 06:00** |
| 赛道 | Challenge 1 **Blast Radius Zero**（必须：Vultr VM 后端 + Serverless Inference + 隔离沙箱） |
| 必交 | 公开 GitHub 仓库、Vultr 部署的 Demo URL、≤5 分钟视频（含**真实 containment moment**）、slides、README（Vultr 架构说明） |
| 团队 | 1–6 人，solo 合法 |
| 奖金 | 总 $14,000（$9,000 cash + $5,000 credits）；每位参赛者 $200 credits |
| 评分 | Application of Technology · Presentation · Business Value · Originality |

---

## 关键决策（预研结论）

| # | 决策 | 一句话理由 |
|---|---|---|
| 1 | **只做 Pattern A**（终端 + 文件执行） | 浏览器自动化（Pattern B）会把 5 天工作量翻倍；赛题允许 "one or both" |
| 2 | **单 Agent 循环**，不做多 Agent | 可预测、可调试、演示稳定；与安全主线无关的复杂度全部砍掉 |
| 3 | **Docker hardened 基线 + gVisor `runsc` 默认强化档**（失败自动回切 `runc`） | 不把交付押在未验证的 `/dev/kvm` 上；gVisor systrap 不需要 KVM |
| 4 | **双 VM**：`yai-app`（密钥/编排/审计）+ `yai-sandbox`（唯一执行面，零密钥） | 两台 2c4g 与单台 4c8g 同价，等于白送网络边界；沙箱机可整机销毁 |
| 5 | **所有 LLM 调用走 Vultr Serverless Inference** | 赛事强制；主模型 `deepseek-v4.1-flash-1017`（1M ctx、tool calling、$0.15/$0.60 per 1M） |
| 6 | **策略引擎确定性，不交给 LLM** | 命令过滤可绕过，真正的保证是容器边界；策略负责 UX 拦截与审批 |
| 7 | **每任务一次性容器，结束即销毁** | 赛题 lifecycle discipline 要求，也是最直观的 containment 素材 |
| 8 | **5 天只保一条主线**（P0 12 项），P1/P2 按门禁裁剪 | 约 60 人时的现实约束；交付确定性 > 功能数量 |

---

## 文档导航

按这个顺序读：

| 顺序 | 文档 | 你会得到什么 |
|---|---|---|
| 1 | [`docs/00-预研总结与关键决策.md`](docs/00-预研总结与关键决策.md) | 一页看完全部结论、风险与行动清单 |
| 2 | [`docs/01-赛事规则与提交要求.md`](docs/01-赛事规则与提交要求.md) | 规则/时间线/提交物/credits/评分/红线 |
| 3 | [`docs/05-MVP架构设计.md`](docs/05-MVP架构设计.md) | 组件图、信任边界、工具契约、策略引擎、trace |
| 4 | [`docs/06-五天开发计划.md`](docs/06-五天开发计划.md) | 范围分层、逐日计划、Go/No-Go 门禁、裁剪阶梯 |
| 5 | [`docs/02-Vultr-Serverless-Inference接入.md`](docs/02-Vultr-Serverless-Inference接入.md) | 推理接入、模型选型、tool calling、限流、代码骨架 |
| 6 | [`docs/03-沙箱方案选型.md`](docs/03-沙箱方案选型.md) | 七方案对比矩阵 + ADR-001 |
| 7 | [`docs/04-安全边界与策略设计.md`](docs/04-安全边界与策略设计.md) | L0–L5、加固参数、20 条拦截规则、3 个演示脚本 |
| 8 | [`docs/07-部署与成本估算.md`](docs/07-部署与成本估算.md) | 双 VM SOP、防火墙、TLS、成本两档 |
| 9 | [`docs/08-实现方案与骨架说明.md`](docs/08-实现方案与骨架说明.md) | 已实现的模块、运行方式、与设计的偏差、Day-1 清单 |
| 10 | [`docs/09-派单与并行开发计划.md`](docs/09-派单与并行开发计划.md) | 8 张可派工单、依赖图、冲突矩阵、验收总表 |
| 11 | [`docs/10-Kickoff执行手册.md`](docs/10-Kickoff执行手册.md) | 11-03 23:00 起 3 小时上线的逐条命令与降级路径 |
| 12 | [`docs/11-Credits领取手册.md`](docs/11-Credits领取手册.md) | $200 credits 的领取时点、兑换路径、前置条件、失败处理 |
| 11 | [`docs/evidence/`](docs/evidence/) | 波次 1 实测证据：containment、策略红队、API 加固、推理接入状态 |

证据台账（每条事实附 URL + 访问日期 + 原文摘录）：
[`research/00`](research/00-来源与证据.md) · [`research/02`](research/02-inference-证据.md) · [`research/03`](research/03-sandbox-证据.md) · [`research/07`](research/07-cost-证据.md)

---

## 五天计划一览

```
赛前 10-31 ~ 11-02   报名就绪 · Policy Engine + 单测 · Trace · 骨架（不花钱）
D0   11-03 夜        领 credits · 双 VM + VPC · Docker · 推理冒烟        → Gate 0
D1   11-04           远端沙箱执行 + Agent 循环 + 公网时间线              → Gate 1
D2   11-05           文件读写改 + 策略拦截 + 资源限额 + trace 导出       → Gate 2
D3   11-06           3 个 containment 场景 + 审批门 + 审计哈希链         → Gate 3
D4   11-07           录制视频 + README + slides（不再加功能）            → Gate 4
D5   11-08           终审 · 复现测试 · **11-09 03:00 前提交**            → Gate 5
```

完整门禁与裁剪阶梯见 [`docs/06`](docs/06-五天开发计划.md)。

---

## 预算（5 天 ≈ 120h）

| 档 | 组成 | 小计 | 占 $200 |
|---|---|---|---|
| 底线 | 单 VM 2c4g + 轻推理 | $5.40 | 2.7% |
| **推荐** | **双 VM 2c4g + 典型推理 + 存储** | **$13.28** | **6.6%** |
| 上限 | 大机型 + 高推理 + 对象存储 | $34.01 | 17.0% |

结论：**钱不是风险**；风险是"忘记 Destroy"（关机仍计费）和"credits 到期日未知"。控制脚本见 [`docs/07`](docs/07-部署与成本估算.md)。

---

## Containment 证据：三个必须拍进视频的 moment

| # | 攻击 | 本地实测结果（2026-10-31，真实 Docker） | 宿主证明 |
|---|---|---|---|
| M1 | `rm -rf --no-preserve-root /` | 42,349 行 `Read-only file system`，`rm_exit=1`；`/workspace` 写入成功 | 进程数/可用磁盘/容器数前后一致 |
| M2 | `b(){ b\|b& }; b` + `sleep` | 3,978 行 `Cannot fork`，无 OOM，容器被回收 | pids 限额吸收，宿主负载无跳变 |
| M3 | `curl … \| sh` + `169.254.169.254` | 策略拒绝；容器内仅 `lo`，出站 `Temporary failure in name resolution` | 攻击容器 `--network=none` |

完整原始输出、uid/capability/mount 检查与集成测试结果：[`docs/evidence/WO-01-containment.md`](docs/evidence/WO-01-containment.md)。
策略红队语料（82 条）与残余风险：[`docs/evidence/WO-05-policy.md`](docs/evidence/WO-05-policy.md)。
API 鉴权/限流/并发与 trace 校验：[`docs/evidence/WO-06-audit-api.md`](docs/evidence/WO-06-audit-api.md)。

---

## 非目标（明确不做）

- **浏览器自动化（Pattern B）**：赛题允许 "one or both"，5 天只保终端 + 文件执行主线。
- **多 Agent 编排**：与安全主线无关的复杂度全部砍掉，单循环更可预测、可审计。
- **接入 Vultr 以外的 LLM provider**：赛事强制 Serverless Inference，代码没有外部 fallback。
- **多租户 / 生产级 SLA**：Demo 是单任务串行（全局并发=1），按成本与时间裁剪。
- **"命令过滤 = 安全"**：策略只负责 UX 拦截与审批，真正的保证是容器边界（见下）。

---

## 目录结构（当前）

```
YAI Guard/
├── README.md
├── LICENSE                      # MIT
├── docs/                        # 00 预研总结 … 09 派单计划 + evidence/
├── research/                    # 事实台账（URL + 访问时间 + 原文）
├── dispatch/                    # 8 张可直接派发的工单（WO-01 … WO-08）
├── backend/
│   ├── app/
│   │   ├── policy/              # 确定性策略引擎（零依赖）
│   │   ├── trace/               # 哈希链审计 + 独立文件校验
│   │   ├── sandbox/             # Fake / Docker / Remote 三种后端 + 宿主指纹
│   │   ├── agent/               # 循环 / 工具 / Mock / Vultr 适配
│   │   ├── main.py              # Web API（app VM，鉴权/限流/并发）
│   │   └── controller.py        # Sandbox Controller（sandbox VM）
│   ├── tests/                   # 72 个 unittest（含 corpus/ 红队语料）
│   └── demo_offline.py
├── web/                         # index.html + styles.css + app.js（零构建）
├── deploy/                      # 双 VM compose + Caddy + 沙箱镜像 + evidence/
└── scripts/check_no_host_exec.py
```

> 代码仓库将在 kickoff 后推送为公开仓库（独立仓库，不 vendor YAI Agent Core）。若 YAI Agent Core 的许可证与 MIT 不兼容，本项目只借鉴接口设计、不复制代码。

---

## 与 YAI Agent Core 的关系

- **复用（接口层）**：Agent Runtime loop 形状、任务规划数据结构、工具注册机制、MCP 客户端、模型适配接口。
- **独立实现（本项目核心资产）**：Sandbox Runtime、Execution Policy、Terminal Executor、File Isolation、Security Controller、Resource Limits、Execution Trace。
- **硬红线**：任何代码路径都不得在宿主机执行用户/模型产生的命令——由双 VM 架构 + 静态检查（禁止 `subprocess`/`os.system`/`eval`/`exec`）保证。

---

## 现在最该做的一件事

> **在 11-03 23:00 之前完成 lablab 报名 + Discord 连接 + 建 1 人 team 选 Challenge 1，并准备一个从未注册过 Vultr 的邮箱。**
> 其余一切都可以在活动期内补；只有报名不能。
