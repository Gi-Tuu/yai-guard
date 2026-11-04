# 镜像冒烟 · app 控制面（2026-11-01）

> 环境：Docker Desktop 29.7.2（Linux 容器），Windows 11。

## 1. 构建

```text
$ docker build -f backend/Dockerfile -t yaiguard/app:0.1 .
...
naming to docker.io/yaiguard/app:0.1 done
```

- 镜像大小：**263 MB**
- 构建上下文用 `.dockerignore` 排除 `.git` / `docs` / `research` / `artifacts` / `__pycache__`

## 2. 导入冒烟

```text
$ docker run --rm yaiguard/app:0.1 python -c "from app.main import app; from app.controller import app as c; print(...)"
imports ok: YAI Guard YAI Guard Sandbox Controller
```

## 3. 运行冒烟

```text
$ docker run -d --name yaig-app-test -p 8015:8000 \
    -e MOCK_MODEL=1 -e SANDBOX_DRIVER=fake yaiguard/app:0.1
Up ... 0.0.0.0:8015->8000/tcp

$ curl -s http://127.0.0.1:8015/healthz
{"ok":true,"version":"0.1.0","trace_ok":true,"trace_bad":null}

$ curl -s -o /dev/null -w "%{http_code} %{size_download}" http://127.0.0.1:8015/
200 3082

$ curl -s -o /dev/null -w "%{http_code} %{size_download}" http://127.0.0.1:8015/app.js
200 17345

$ curl -s http://127.0.0.1:8015/api/policy
{"version":1,"workspace":"/workspace","limits":{"cpu":1.0,"memory_mb":512,...}}
```

容器以非 root（uid 10001）运行；`MOCK_MODEL=1` / `SANDBOX_DRIVER=fake` 仅用于本次冒烟。

## 4. 结论

- 控制面镜像可独立构建、可启动、静态资源与 API 均正常。
- 真实部署时把 env 换成 `SANDBOX_DRIVER=remote` + Vultr key + `DEMO_TOKEN` 即可。
- 沙箱镜像（`yaiguard/sandbox-py:1.0`，498 MB）另见 containment 证据。

## 5. 已知坑（本轮遇到）

- 本机 8013 端口被一个无关的 `agent/serve.py` 进程占用，导致第一次 `-p 8013:8000` 的请求打到了那个进程（返回 404）。**换端口后正常**；部署时如果端口冲突，先 `Get-NetTCPConnection -LocalPort <port>` 确认占用者。
