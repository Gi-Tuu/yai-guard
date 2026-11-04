# 18 · 任务运行时硬化设计与迁移方案

> 状态：WP1–WP6、WP9（queued 重启重排）与 interrupted 证据已实现；生产使用 `TASK_STORE=sqlite`。  
> 目标：为取消、队列、审批、重启恢复、周期性孤儿回收和证据签名升级提供单一任务事实源。
>
> 单写者说明：默认 `single` 模式通过 `artifacts/task-runtime.lock` 的 `flock` 独占运行；任务认领使用
> `mark_running` 的 CAS。`TASK_RUNTIME_MODE=multi` 模式使用 `claim_next`、DB 队列、DB 事件/审批和
> 每进程 worker，多个 uvicorn worker 可共享同一 SQLite；多进程迁移由独立 flock 串行化。
>
> Phase 2 更新：multi 模式已实现并线上验收；rate limit / SSE 槽在 multi 下用 SQLite 全局配额，
> lease 恢复由 maintenance leader 执行并立即触发容器清理。

## 0. 当前实现状态（2026-11-04）

```text
WP1  Task Store / 状态机 / lease / approvals     done
WP2  TaskService 桥接 + 降级                      done
WP3  worker pool / TASK_CONCURRENCY / 审批表      done
WP4  协作取消 + 审批唤醒 + cancelled 终态          done
WP5  任务 deadline watchdog                       done
WP6  SQLite-aware orphan reaper                   done
WP9  queued 重启重排 + 周期重试                    done
      task.interrupted 证据 + bundle 规则           done
```

生产语义：

- `TASK_STORE=sqlite`，内存仍是 API 热路径，SQLite 为权威恢复来源；
- 重启时 `running/waiting_approval → interrupted`，并补写 `task.interrupted`；
- `queued` 自动重排，已取消的 queued 直接转 `cancelled`；
- 审批等待不占执行槽；到 deadline 记录 `task_timeout`；
- reaper 只清理终态/queued/未知/lease 过期容器，活跃 lease 不碰；
- 证据签名支持 Ed25519 固定公钥与 v1 HMAC。

## 1. 为什么先做 Task Store

当前 app 的任务状态是：

```text
main.py: TASKS / PENDING / _TASK_SLOT
daemon thread → run_agent
```

这适合单机演示，但存在以下互相纠缠的问题：

- 重启后任务状态丢失；
- 审批等待持有全局唯一槽；
- 没有队列，无法取消/排队；
- 无法区分“任务崩溃”和“任务仍在运行”；
- 孤儿容器回收缺少权威任务归属；
- 状态机无法做原子 claim/transition；
- 取消与 cleanup 容易产生竞态。

因此顺序必须是：

```text
Task Store
  → 状态机 / 恢复
  → 队列 / 并发 / 审批
  → 取消
  → 硬 deadline
  → 周期性 reaper
  → 证据签名 v2
```

## 2. 目标架构

```text
Caddy
  │
FastAPI
  │
TaskService ── SQLite task store
  ├─ create / get / cancel
  ├─ queue claim / lease
  ├─ approvals
  └─ startup recovery
        │
        ▼
WorkerPool（默认 1，可配置 2）
  └─ run_agent
      ├─ RemoteSandbox → controller
      ├─ TraceStore（JSONL + anchor，事实源）
      └─ Evidence bundle
```

约束：

- 不引入 Postgres/Redis；
- 不在本轮改 trace 事件格式；
- 不把整个 trace 事件写进 SQLite；
- SQLite 只保存任务级元数据；
- 旧内存路径保留为回退开关。

## 3. 数据模型

### 3.1 tasks

```sql
CREATE TABLE IF NOT EXISTS tasks (
    id                TEXT PRIMARY KEY,
    trace_id          TEXT NOT NULL UNIQUE,
    owner             TEXT NOT NULL,
    prompt            TEXT NOT NULL,
    status            TEXT NOT NULL,
    summary           TEXT NOT NULL DEFAULT '',
    error             TEXT,
    cancel_requested  INTEGER NOT NULL DEFAULT 0,
    created_at        REAL NOT NULL,
    started_at        REAL,
    finished_at       REAL,
    lease_until       REAL,
    worker_id         TEXT,
    attempt           INTEGER NOT NULL DEFAULT 0,
    version           INTEGER NOT NULL DEFAULT 0
);
```

索引：

```sql
CREATE INDEX IF NOT EXISTS idx_tasks_status_created
    ON tasks(status, created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_owner
    ON tasks(owner, created_at);
```

### 3.2 approvals

```sql
CREATE TABLE IF NOT EXISTS approvals (
    task_id      TEXT NOT NULL,
    call_id      TEXT NOT NULL,
    owner        TEXT NOT NULL,
    status       TEXT NOT NULL,        -- pending / approved / rejected / expired / cancelled
    created_at   REAL NOT NULL,
    expires_at   REAL NOT NULL,
    resolved_at  REAL,
    PRIMARY KEY (task_id, call_id)
);
```

审批记录不保存工具参数原文，只记录 call_id/状态/时序；参数仍在 trace 中。

### 3.3 schema_version

使用 SQLite `PRAGMA user_version`：

```text
0 → 1：初始 tasks/approvals schema
```

## 4. 状态机

```text
queued
  → running
  → waiting_approval
  → completed
  → failed
  → cancelled
  → interrupted
```

允许的转换：

| From | To |
|---|---|
| queued | running, cancelled, interrupted |
| running | waiting_approval, completed, failed, cancelled, interrupted |
| waiting_approval | running, failed, cancelled, interrupted |
| completed / failed / cancelled / interrupted | 不允许回退 |

规则：

- 终态不可变；
- `running/waiting_approval` 必须带 lease；
- 同一任务同一时刻只能有一个 worker；
- `cancel_requested` 不直接改 terminal 状态，由 worker 在边界转 `cancelled`；
- 重启时 `running/waiting_approval` → `interrupted`，不自动重跑；
- 自动重跑只允许显式 `attempt`/requeue 策略，默认关闭。

## 5. 状态更新与 claim

### 5.1 原子转换

```sql
UPDATE tasks
SET status = ?, version = version + 1, ...
WHERE id = ? AND status IN (...) AND version = ?
```

返回 `rowcount == 1` 才算成功；否则是并发冲突。

### 5.2 队列 claim

```sql
BEGIN IMMEDIATE;
SELECT id FROM tasks
WHERE status = 'queued'
ORDER BY created_at
LIMIT 1;
UPDATE tasks
SET status = 'running', worker_id = ?, lease_until = ?,
    started_at = COALESCE(started_at, ?), attempt = attempt + 1,
    version = version + 1
WHERE id = ? AND status = 'queued';
COMMIT;
```

- 单条 `BEGIN IMMEDIATE` 防止两个 worker 同时 claim；
- lease 用于恢复时判断 worker 是否已死；
- 默认 worker 数 1，避免引入容器并发。

### 5.3 恢复

启动时：

```text
running / waiting_approval
  → interrupted
  → error="interrupted by app restart"
  → trace 保留
  → controller 对 task_id 执行幂等 destroy
```

不自动重跑，原因是当前 run_agent 的可重入性和外部副作用尚未证明安全。

## 6. 兼容与双写

第一阶段不直接删除现有实现。

```text
TASK_STORE=memory | sqlite
```

- `memory`：旧路径；
- `sqlite`：新路径；
- 迁移期双写：
  - 创建任务写 SQLite；
  - 状态变化写 SQLite；
  - 内存 `TASKS` 只作为当前 UI 缓存；
- 读取优先 SQLite；
- SQLite 异常时回退内存，并在 healthz 暴露 degraded。

回退：

```text
TASK_STORE=memory
systemctl restart yai-guard
```

不需要改数据库，不破坏 trace/anchor。

## 7. API 兼容

现有端点与字段保持不变：

```text
POST /api/tasks
GET  /api/tasks/{id}
GET  /api/tasks/{id}/events
POST /api/tasks/{id}/approvals/{call_id}
GET  /api/tasks/{id}/trace.jsonl
GET  /api/tasks/{id}/trace/verify
GET  /api/tasks/{id}/evidence
POST /api/containment/run
```

新增字段只做增量：

```text
status: queued | running | waiting_approval | completed | failed | cancelled | interrupted
cancel_requested: bool
attempt: int
```

不改变现有 `completed/failed` 语义；`cancelled/interrupted` 是新增终态。

## 8. 分阶段实现

### WP1：Task Store + 恢复（本分支骨架）

- 本文件；
- `backend/app/tasks/` 模型与 SQLite store；
- 状态机、claim、recovery 测试；
- **不接入 main.py**。

验收：

- schema 可重复初始化；
- 重启后数据仍在；
- 非法状态转换失败；
- 双 worker claim 只有一个成功；
- recovery 只改 running/waiting_approval。

### WP2：双写与恢复器接入

- `TaskService` 包装 memory + SQLite；
- `TASK_STORE` flag；
- 启动恢复；
- API 兼容测试。

### WP3：队列 + 并发 + 审批

- worker pool；
- `TASK_CONCURRENCY`；
- approvals 表；
- 审批等待不再阻塞全局。

### WP4：取消

- `POST /api/tasks/{id}/cancel`；
- `cancel_requested` + event；
- 审批唤醒；
- finally cleanup；
- `cancelled` 终态。

### WP5：硬 deadline

先请求级 timeout，再评估是否每任务子进程。

### WP6：周期性孤儿 reaper

- 基于 Task Store 判断 active/terminal；
- 只清理 terminal/unknown/lease 过期任务容器。

### WP7：Ed25519 证据 v2

- 保留 v1 HMAC；
- 新增公钥签名；
- 独立离线验证。

## 9. 测试矩阵

| 测试 | 目的 |
|---|---|
| schema 初始化/重开 | 持久性 |
| status transition | 状态机 |
| terminal immutability | 防止回退 |
| claim concurrency | 防重复执行 |
| lease/recovery | 重启恢复 |
| duplicate task/trace | 唯一性 |
| approval unique | 审批幂等 |
| DB corruption/unwritable | 启动失败与回退 |
| API compatibility | 旧客户端 |
| restart simulation | interrupted |
| controller cleanup | 孤儿 |

## 10. 风险

| 风险 | 处理 |
|---|---|
| SQLite 写锁 | WAL + 短事务 + 单进程 worker |
| 双写不一致 | 先 SQLite 写入，再刷新内存；冲突以 SQLite 为准 |
| 重启后重跑副作用 | 默认 interrupted，不自动重跑 |
| 取消与 cleanup 竞态 | 幂等 destroy + 单一状态机 |
| 并发容器冲突 | 默认 concurrency=1；controller 已 per-task |
| DB 放在只读目录 | 启动时迁移到 artifacts 下并写权限检查 |
| 旧客户端不识别新状态 | 只新增终态，UI 兼容处理 |

## 11. 部署与回滚

- DB 路径：`ARTIFACT_DIR/tasks.db`，与 trace 同卷；
- 文件权限：app 用户 `yaiguard` 可写；
- 启动时自动 schema 创建；
- 部署前备份 `tasks.db`；
- `TASK_STORE=memory` 可回退；
- 不修改 `.env.app` 结构；
- 每阶段单独 PR、CI、线上验收。

## 12. 当前 WP1 范围

本分支只做：

```text
docs/18-hardening-task-runtime.md
backend/app/tasks/models.py
backend/app/tasks/store.py
backend/app/tasks/__init__.py
backend/tests/test_tasks_store.py
```

明确不做：

- 不修改 `main.py`；
- 不修改线上行为；
- 不启用 SQLite；
- 不新增 API；
- 不迁移数据。

验收通过后，才进入 WP2 双写接线。
