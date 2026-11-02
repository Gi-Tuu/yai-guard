# P1-6 · gVisor / runc 真实状态

> 日期：2026-11-02 ｜ 结论：**当前唯一已验证运行时是 runc；runsc 未安装、未实测；不实现自动降级。**

## 1. 宿主 runtime 列表

```text
$ docker info --format '{{range $k,$v := .Runtimes}}{{$k}} {{end}}'
io.containerd.runc.v2 nvidia runc
```

没有 `runsc`。

## 2. 默认配置

- `deploy/compose.sandbox.yaml`：`SANDBOX_RUNTIME: ${SANDBOX_RUNTIME:-runc}`
- `.env.example`：`SANDBOX_RUNTIME=runc`
- `backend/app/controller.py`：`_RUNTIME = os.environ.get("SANDBOX_RUNTIME") or "runc"`
- 容器实际 runtime 由 `docker run --runtime <值>` 决定；trace 的 `sandbox.created` 记录 limits 与 runtime 来源。

## 3. 显式请求不存在的 runtime：失败而非降级

```text
$ docker run ... -e SANDBOX_RUNTIME=runsc yaiguard/controller:test
$ curl -s -w ' HTTP=%{http_code}' http://127.0.0.1:19001/healthz
{"ok":false,"runtime":"runsc","image":"yaiguard/sandbox-py:1.0",
 "daemon":null,"image_status":null,
 "error":"DockerError: runtime 'runsc' not available; daemon runtimes=['io.containerd.runc.v2', 'nvidia', 'runc']"}
HTTP=503
```

- 启动即失败，`/healthz` 503；
- 不会出现“配置写 runsc、实际静默跑 runc”的情况。

## 4. 文档已同步

- README 关键决策 #3：runc 已实测，runsc 仅预留、不自动降级；
- `docs/03` ADR-001、`docs/04` §4.3、`docs/05` 运行时表、`docs/00` 摘要、`docs/06` P0-4 均已改为同一口径；
- 视频脚本仍把 `runsc` 列为待验证项，禁止口播“gVisor enabled”。

## 5. 待验证（BLOCKED / NOT RUN）

- `runsc` 在 Vultr VM 上安装与 numpy/pandas/matplotlib 兼容性：**NOT RUN**（当前环境无该 runtime）；
- 若未来启用：必须先在目标 VM 上 `docker run --runtime=runsc` 跑业务镜像，并记录启动日志；失败保持 runc，不做自动切换。
