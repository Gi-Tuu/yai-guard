# WO-03 · Web 界面（时间线 / 审批 / Trace / 宿主完好面板）

> 派给：前端工程师 ｜ 估时：10h ｜ 依赖：无（可对 mock 事件开发）｜ 可开工：**现在**
> 先读：`web/index.html`、`docs/05` §9（事件 schema）、`backend/app/main.py`（API）

## 0. 背景

现有单页 HTML 能跑通，但还只是"能用"。本单目标：把它做成**可录屏的产品界面**——评委 5 分钟内要能看懂"Agent 在干什么、哪一步被拦了、宿主机没事、日志可查"。

## 1. 交付物

| 文件 | 内容 |
|---|---|
| `web/index.html` | 结构 |
| `web/styles.css` | 视觉（深色、终端感、状态色：deny 红 / approval 黄 / exec 蓝 / done 绿） |
| `web/app.js` | 逻辑（SSE、任务状态、审批、trace 查看） |

**必须有的 5 块**
1. **任务输入区**：demo 任务一键填入；提交后禁用防重复。
2. **实时时间线**：按 step 分组；`policy.decision` 显示 effect + rule_id + reason；deny 红色高亮并展示 `suggestion`；`sandbox.exec` 展示 argv 与 exit_code。
3. **审批卡片**：`approval.requested` 出现时弹按钮（Approve / Reject），带 TTL 倒计时；调用 `POST /api/tasks/{id}/approvals/{call_id}`。
4. **Trace 面板**：拉取 `/api/tasks/{id}/trace.jsonl`，渲染事件表；"校验完整性"按钮调 `/api/tasks/{id}/trace/verify`（WO-06 提供），显示 OK / 第几条被篡改。
5. **宿主完好面板**：从 trace 里取 `sandbox.created` 与 `sandbox.exec` 的 `host` 指纹（`df -i`、文件数、进程数），前后对比，绿色"UNCHANGED"。若 WO-06 还没加字段，先用 `payload.host` 占位。

**品质要求**：无外部 CDN（离线可开）；所有内容用 `textContent` 渲染（防 XSS）；SSE 断线自动重连；空态/加载/错误态齐全；1280×720 录屏比例下不溢出。

## 2. 验收

```bash
# 后端（离线）
cd backend && pip install -r requirements.txt
MOCK_MODEL=1 SANDBOX_DRIVER=fake uvicorn app.main:app --port 8000
# 打开 http://127.0.0.1:8000
```

人工清单：
- [ ] 点"填入演示任务"→ 提交 → 能看到 `plan.created` 与多步 `sandbox.exec`
- [ ] `rm -rf /` 那一步红色高亮，显示 rule `D-RM-ROOT` 与 reason
- [ ] `rm -rf /workspace/tmp` 弹出审批；点 Approve 后继续；点 Reject 则跳过
- [ ] `curl|sh` 被 red 高亮
- [ ] Trace 下载按钮能拿到 jsonl；校验按钮显示 OK
- [ ] 断网/刷新后 SSE 重连正常
- [ ] 无控制台报错；无外部网络请求

## 3. 写文件范围

`web/` 全部。**不要改后端**；API 路径以 `main.py` 现有为准。

## 4. 禁止

- 不引入 React/Vue 构建链（保持零构建，直接静态托管）。
- 不把 API key、token 写进前端。
- 不改事件字段名；需要新字段找 WO-06 加，前端做兼容（字段缺失不崩）。
- 不伪造"宿主未受影响"——没有数据就显示"待采集"。

## 5. 接口契约

- SSE：`GET /api/tasks/{id}/events`，每行 `data: <json>\n\n`，结束 `event: done`。
- 任务：`POST /api/tasks {task}` → `{task_id, trace_id}`；`GET /api/tasks/{id}` → `{status, summary, error}`。
- 审批：`POST /api/tasks/{id}/approvals/{call_id} {approved}`。
- Trace：`GET /api/tasks/{id}/trace.jsonl`。
- 新增（WO-06）：`GET /api/tasks/{id}/trace/verify` → `{ok, bad_seq}`。

## 6. 坑

- SSE 下 Caddy 必须 `flush_interval -1`（已配）。
- 事件是追加的，不要按"全量重渲染"做，按 cursor 增量 append。
- 审批按钮只能点一次，点完禁用。
