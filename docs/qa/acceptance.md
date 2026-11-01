# WO-08 · 验收清单（波次 1 跑完）

> 日期：2026-10-31 ｜ 执行环境：Windows 11 + Docker Desktop 29.7.2 + Python 3.14
> 自动检查：[`scripts/verify_submission.py`](../../scripts/verify_submission.py)
> 结论：**本地可验证项全部 PASS**；云端项（WO-04）BLOCKED（等 11-03 kickoff / credits）；视频与真实推理待后续。

## 1. 自动检查

```text
$ python scripts/verify_submission.py
[PASS] unit tests                 OK
[PASS] host-exec redline          OK: no host-execution primitives outside app/sandbox/ and app/controller.py
[PASS] offline demo + hash chain  hash-chain verify = True
[PASS] markdown links             33 files checked
[PASS] git hygiene                85 files, no secrets/artifacts tracked
[PASS] LICENSE                    MIT found
[PASS] README keywords            Vultr, Containment, Quickstart, MIT, sandbox
[PASS] submission docs            slides + copy present
[SKIP] video spec                 docs/submission/video.mp4 not recorded yet
[SKIP] demo path smoke            set YAIG_SMOKE_DEMO=1 to run (needs Docker + image)
RESULT: ALL CHECKS PASSED (2 skipped)

# 有 Docker 时：
$ YAIG_SMOKE_DEMO=1 python scripts/verify_submission.py
[PASS] demo path smoke            deny rm -rf / | deny curl|sh | approval happened | real exec | trace verified | sse events
RESULT: ALL CHECKS PASSED (1 skipped)
```

## 2. 功能与安全验收

| # | 项 | 结果 | 证据 / owner |
|---|---|---|---|
| 1 | 72 个单测全绿（含 Docker 集成） | PASS | `python -m unittest discover -s backend/tests -t backend` |
| 2 | 红线静态检查（无宿主机执行逃逸） | PASS | `scripts/check_no_host_exec.py` |
| 3 | 离线 demo：allow → deny → approval → deny → finish | PASS | `backend/demo_offline.py`，hash-chain True |
| 4 | 沙箱镜像构建 | PASS | `yaiguard/sandbox-py:1.0`，498 MB |
| 5 | M1 只读根 | PASS | [WO-01](../evidence/WO-01-containment.md) §2 |
| 6 | M2 fork bomb 被 pids 吸收 | PASS | WO-01 §3 |
| 7 | M3 零网络（仅 lo） | PASS | WO-01 §4 |
| 8 | 非 root / 零 capability / mount 拒绝 | PASS | WO-01 §5 |
| 9 | 宿主指标前后一致（本地） | PASS | 进程 473→473，磁盘 51.75→51.75 GB，容器 0→0 |
| 10 | 策略红队语料 82 条 | PASS | [WO-05](../evidence/WO-05-policy.md) |
| 11 | API 401 / 429 / 并发 429 / 校验端点 | PASS | [WO-06](../evidence/WO-06-audit-api.md) |
| 12 | UI：时间线 / 审批 / trace 校验 / 宿主面板 | PASS | 浏览器实测：D-RM-ROOT 红色高亮、Approve 后 completed、校验 `OK · 30 events` |
| 13 | 提交文案字符限制 | PASS | 标题 47/50，短描述 234/255，长描述 164 words |
| 14 | 文档链接完整 | PASS | 33 个 markdown 文件 |
| 15 | 演示路径冒烟（docker 直连 + remote controller 两种模式） | PASS | `scripts/smoke_demo.py`：两种模式均 30 事件 / 7 项检查全 PASS |
| 16 | 控制面镜像 + 部署配置校验 | PASS | [镜像冒烟](../evidence/app-image-smoke-2026-11-01.md) + [Caddy/compose 校验](../evidence/deploy-config-validation-2026-11-01.md) |

## 3. 阻塞与待办

| # | 项 | 状态 | 原因 / 下一步 | owner |
|---|---|---|---|---|
| 17 | Vultr 双 VM + VPC + TLS + 防火墙 | **BLOCKED** | 需 11-03 kickoff 后才能领 $200 credits；代码/文件已就绪 | WO-04 |
| 18 | 公网 HTTPS Demo 无痕可打开 | **BLOCKED** | 依赖 #17 | WO-04 |
| 19 | 真实 DeepSeek tool-calling 收敛 | **PENDING** | 无真实 key；Day-0 清单见 [WO-02](../evidence/WO-02-inference.md) §3 | WO-02 |
| 20 | 云端宿主 `df -i` / `/etc` 对比 | **PENDING** | 依赖 #17；脚本已就绪 | WO-01 |
| 21 | gVisor `runsc` 兼容性 | **PENDING** | 本机无 runsc；Day-1 探测 | WO-01 |
| 22 | 视频 ≤5:00 / ≤300MB | **PENDING** | 脚本已写好，未录制 | WO-07 |
| 23 | slides / cover image | **PASS** | `YAI-Guard-slides.pptx`（12 页、含 notes）+ `cover.png`（1920×1080）已生成 | WO-07 |
| 24 | lablab 表单提交 | **BLOCKED** | 报名窗口 11-03 开放 | Lead |

## 4. 本轮未修的问题（记录，不掩盖）

1. `RemoteSandbox` 的宿主指纹来自 controller 容器命名空间，不是物理机；云端对外的宿主证据须用 `deploy/evidence/host_fingerprint.sh` 在 sandbox VM 上采集。
2. Windows 本地无法运行 `.sh` 取证脚本（MSYS 的 `/etc`、`df -i` 语义不同），已用 PowerShell 等价项替代并注明。
3. 策略对 `python3 main.py` 的脚本内容不做静态分析；容器边界是唯一保证。
4. 单进程内存限流/并发：uvicorn 多 worker 时失效；演示期固定单进程。
5. `docs/08` 的数字与状态已在 2026-11-01 复核时更新为 v0.2（72 测试 / API 已加固 / Docker E2E）；如与证据文件冲突，以 `docs/evidence/` 为准。

## 5. 提交前必须再做一次

```bash
python scripts/verify_submission.py
# 期望 RESULT: ALL CHECKS PASSED（video 仍 SKIP 时先录视频）
```
