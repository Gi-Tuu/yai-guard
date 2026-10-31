# WO-06 · 审计增强 + API 鉴权 / 限流 / 并发 + Trace 校验端点

> 派给：后端工程师 ｜ 估时：8h ｜ 依赖：无 ｜ 可开工：**现在**
> 先读：`docs/04` §7、`docs/05` §9、`backend/app/trace/`、`backend/app/main.py`

## 0. 背景

trace 已有哈希链与脱敏，但还缺：每个执行事件的宿主指纹、资源超限事件、前端要的"校验完整性"端点，以及公网 Demo 必须的鉴权/限流。本单目标：让"审计可验证"和"Demo 不被刷"同时成立。

## 1. 交付物

| 文件 | 内容 |
|---|---|
| `backend/app/trace/store.py` / `events.py` | 新增字段（只加不改）：`sandbox.created` 的 `host_fingerprint`、`sandbox.exec` 的 `host_before`/`host_after`、`resource.limit` 的 `oom`/`pids` |
| `backend/app/main.py` | ① `GET /api/tasks/{id}/trace/verify` → `{ok, bad_seq}`；② `DEMO_TOKEN` 鉴权（设置了就必须带 `X-Demo-Token`，否则 401）；③ 每 IP 令牌桶限流（如 10 req/min，超限 429 + Retry-After）；④ 全局并发=1（semaphore），第二个任务返回 429/排队提示；⑤ SSE 心跳（每 15s `: ping`） |
| `backend/tests/test_trace.py` | 新增字段的链校验、verify 端点逻辑（可只测 store 层） |
| `backend/tests/test_api.py` | FastAPI TestClient；**fastapi 未安装时 skip**，不要 fail |
| `docs/evidence/WO-06-audit-api.md` | 端点契约、限流参数、并发策略 |

**宿主指纹采集**（在 `loop.py` 的 `sandbox.created` 与每次 `sandbox.exec` 前后）：
`{"df_inodes": "...", "etc_files": N, "uptime": "...", "proc_count": N, "captured_at": "..."}`。
采集方式：`SandboxBackend` 增加可选方法 `host_snapshot()`（Fake 返回静态假数据，Docker 由 controller 侧采集后回传）。**不要**在 app 进程里跑 shell。

## 2. 验收

```bash
python -m unittest discover -s backend/tests -t backend
python -m unittest discover -s backend/tests -t backend -p "test_api.py" -v   # 无 fastapi 时 skip 也算通过

# 手动（本地起 API 后）
curl -s http://127.0.0.1:8000/api/tasks -X POST -d '{"task":"ls"}' -H 'content-type: application/json'
curl -s http://127.0.0.1:8000/api/tasks/<id>/trace/verify | jq .
# 篡改 trace.jsonl 一行后再调，期望 ok=false 且 bad_seq 指向该行
```

通过标准：链校验通过/篡改可定位；带错 token → 401；连续请求超限 → 429；两个任务并发时第二个被拒或排队；SSE 有心跳。

## 3. 写文件范围

`backend/app/trace/`、`backend/app/main.py`、`backend/tests/test_trace.py`、`backend/tests/test_api.py`、`docs/evidence/`。

## 4. 禁止

- 不改已有事件字段名（前端已按 `docs/05` §9 消费）；只做**加法**。
- 不把鉴权 token 写进 trace/日志；不把 `DEMO_TOKEN` 硬编码。
- 不把并发改成 >1 还宣称"串行"；演示期就串行。
- 不引入 Redis/数据库等新依赖（内存实现即可，单机单进程）。

## 5. 接口契约（新增）

```
GET  /api/tasks/{id}/trace/verify   -> {"ok": true, "bad_seq": null}
POST /api/tasks                     -> 401 若缺/错 X-Demo-Token（当 DEMO_TOKEN 已设置）
                                    -> 429 若超限/并发满
```

## 6. 坑

- 索引重算要在锁内，避免并发 append 造成 seq 重复。
- 限流只对公网入口生效；内部调用（healthz）不限制。
- TestClient 需要 `httpx`；没装就用 `unittest.skipUnless` 跳过，不要卡住 CI。
