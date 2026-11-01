# WO-06 · 审计增强 + API 鉴权 / 限流 / 并发 + Trace 校验端点

> 日期：2026-10-31 ｜ 状态：**离线全绿**（`python -m unittest` 72 项通过，其中 `test_api.py` 8 项；fastapi/httpx 缺失时这 8 项自动 skip）
> 代码：[`backend/app/trace/store.py`](../../backend/app/trace/store.py)、[`backend/app/main.py`](../../backend/app/main.py)、[`backend/app/agent/loop.py`](../../backend/app/agent/loop.py)

## 1. 新增审计字段（只加不改）

| 事件 | 新字段 | 内容 |
|---|---|---|
| `sandbox.created` | `payload.host_fingerprint` | `{source, df_inodes, etc_files, uptime, proc_count, connections, captured_at}` |
| `sandbox.exec` | `exec.host_before` / `exec.host_after` | 同上，命令执行前后各一次 |
| `resource.limit` | `oom` / `pids` / `memory_mb` / `timed_out` | 被 pids / wall-clock / OOM 打断时由 Agent 循环写入 |

字段名沿用 `docs/05 §9`，前端未做破坏性改动。已有 `payload.host`（`host_fingerprint()`）保留兼容。

## 2. 宿主指纹采集方式

- `FakeSandbox.host_snapshot()`：静态假数据（`source: "fake"`），只用于离线 UI。
- `DockerSandbox.host_snapshot()`：读 `/proc`、`os.statvfs("/")`，**不执行 shell**；Windows 宿主返回 `source: "unavailable"`。
- `RemoteSandbox.host_snapshot()`：`GET /v1/host/snapshot`（带 `X-Sandbox-Token`），由 sandbox VM 的 controller 采集；app 进程不执行任何命令。

已知边界（诚实写）：controller 以容器方式运行时，读到的是 controller 容器的命名空间，不是物理机；真正对外的「宿主前后对比」应使用 `deploy/evidence/host_fingerprint.sh` 在沙箱 VM 上取证（WO-01/WO-04）。

## 3. API 保护（`backend/app/main.py`）

| 能力 | 参数/行为 | 端点 |
|---|---|---|
| Demo Token | 设置 `DEMO_TOKEN` 后必须带 `X-Demo-Token`，否则 401（`secrets.compare_digest`） | `POST /api/tasks`、`POST /api/tasks/{id}/approvals/{call_id}` |
| 每 IP 限流 | 滑动窗口 60s，默认 10 req/min（`RATE_LIMIT_PER_MIN`），超限 429 + `Retry-After`；`<=0` 关闭 | `POST /api/tasks` |
| 全局并发 | `BoundedSemaphore(1)`，第二个任务立即 429（不排队），避免容器并发压垮沙箱 VM | `POST /api/tasks` |
| SSE 心跳 | 每 15s 发送 `: ping` | `GET /api/tasks/{id}/events` |
| 链校验 | 重新读取落盘 JSONL 计算哈希链，返回第一个坏事件 | `GET /api/tasks/{id}/trace/verify` → `{ok, bad_seq, events}` |

`/healthz` 不鉴权、不限流（用于探活）。

## 4. 验收命令与结果

```text
$ python -m unittest discover -s backend/tests -t backend -p "test_api.py" -v
test_correct_token_is_accepted ... ok
test_empty_task_is_400 ... ok
test_healthz_is_not_rate_limited ... ok
test_missing_token_is_401 ... ok
test_rate_limit_returns_429_with_retry_after ... ok
test_second_task_is_rejected_while_slot_is_busy ... ok
test_trace_verify_endpoint_and_tamper_detection ... ok
test_wrong_token_is_401 ... ok
Ran 8 tests in 4.309s
OK
```

`test_trace_verify_endpoint_and_tamper_detection` 会真实改写 trace 的第一行，再调端点，断言 `{ok: false, bad_seq: 1}`。

手动复现（本地起 API）：

```bash
DEMO_TOKEN=demo RATE_LIMIT_PER_MIN=10 \
  MOCK_MODEL=1 SANDBOX_DRIVER=fake uvicorn app.main:app --port 8000

curl -s -X POST http://127.0.0.1:8000/api/tasks \
  -H 'content-type: application/json' -H 'X-Demo-Token: demo' \
  -d '{"task":"ls /workspace"}'
curl -s http://127.0.0.1:8000/api/tasks/<id>/trace/verify
```

## 5. 测试边界

- `test_api.py` 在 fastapi/httpx 缺失时整体 `skip`，不会 fail。
- 测试把 `POLICY.approval_ttl` 临时降为 1s，避免默认「拒绝审批」把任务线程阻塞 120s。
- 并发槽位由任务线程 `finally` 释放；Thread 启动失败时也会回滚。

## 6. 未做（交给后续工单）

- 审批端点的限流未单独计数（只做了鉴权）；审批是低频人工动作。
- 内存计数，多进程部署（uvicorn workers>1）会失效；演示期固定单进程（与 `docs/07` 一致）。
- 真实宿主指纹的云端验证见 WO-04。
