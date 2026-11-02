# P0-1 · Controller Docker 执行环境验证

> 日期：2026-11-02 ｜ 环境：Docker Desktop 29.7.2（Linux 容器，cgroup v2）｜ Windows 11
> 结论：**Controller 镜像自带 Docker CLI，以非 root uid 10001 通过 socket 组访问宿主 daemon；create/exec/file/destroy 全部真实通过，0 容器残留；app 镜像不含 Docker CLI。**

## 1. 镜像

```text
$ docker build -f backend/Dockerfile.controller -t yaiguard/controller:test .
...
naming to docker.io/yaiguard/controller:test done
image size: 322MB
```

- 多阶段从 `docker:27-cli` 只拷贝 `/usr/local/bin/docker`，不安装 daemon。
- 固定 `uid/gid=10001`，`HOME=/tmp`；compose 中 `read_only` + `cap_drop: ALL` + `no-new-privileges`。

## 2. 启动与 socket 权限

```text
# Docker Desktop：socket 为 root:root 660，gid=0
$ docker run --rm -v /var/run/docker.sock:/var/run/docker.sock alpine stat -c 'uid=%u gid=%g mode=%a' /var/run/docker.sock
uid=0 gid=0 mode=660

# controller 容器内身份
$ docker exec yaig-controller-test id
uid=10001(appuser) gid=10001(app) groups=10001(app),0(root)

$ docker exec yaig-controller-test docker info --format 'server={{.ServerVersion}} runtime={{.DefaultRuntime}}'
server=29.7.2 runtime=runc
```

**信任边界（明确记录）**：能访问 docker.sock 等价于宿主机 root 权限。Controller 以非 root 运行只是缩小“应用层被直接拿下”的面；socket 本身仍是高权限通道，因此：
- 只有 sandbox VM 的 controller 挂 socket；
- app VM 不挂、app 镜像不装 Docker CLI；
- compose 用 `group_add: ${DOCKER_GID}` 只加 socket 所在组，不用 `--privileged`。
- 生产 `DOCKER_GID` 必须取 `stat -c '%g' /var/run/docker.sock`（Vultr Ubuntu 通常是 docker 组 GID，不是 0）。

## 3. 启动健康检查

```text
$ curl -s http://127.0.0.1:19000/healthz
{"ok":true,"runtime":"runc","image":"yaiguard/sandbox-py:1.0",
 "daemon":{"ok":true,"error":null,"version":"29.7.2",
           "runtimes":["io.containerd.runc.v2","nvidia","runc"]},
 "image_status":{"ok":true,"image":"yaiguard/sandbox-py:1.0","id":"sha256:3de0558c…"},
 "error":null}
```

- `ensure_ready()` 在启动时检查 daemon / runtime / 镜像；失败时 `/healthz` 返回 **503**，`create` 也显式报错。
- 不再存在“daemon 不可用但服务显示健康”的情况。

## 4. 真实执行（controller → docker）

| 步骤 | 请求 | 结果 |
|---|---|---|
| create | `POST /v1/sandbox/tk_p0` | `{"ok":true}` |
| python | `python3 -c "print(6*7)"` | stdout `42`，exit 0 |
| 身份 | `uname -n; id -u` | 容器 hostname；`65534` |
| 只读根 | `echo x > /root/y` | `Permission denied` / `ROOT_READ_ONLY` |
| 删根 | `rm -rf --no-preserve-root /` | 全部 `Read-only file system` |
| 网络 | `socket.create_connection(('example.com',443))` | `Temporary failure in name resolution` |
| 文件 | `PUT /file` → `GET /file` | `hello-from-p0` 原样返回 |
| destroy | `DELETE /v1/sandbox/tk_p0` | `{"ok":true}` |
| 残留 | `docker ps -a` | **0** |

## 5. app 镜像不具备执行面能力

```text
$ docker run --rm yaiguard/app:0.1 sh -c 'command -v docker && exit 1 || echo NO_DOCKER_CLI'
NO_DOCKER_CLI
```

app 镜像没有 Docker CLI；compose.app 不挂 socket；`SANDBOX_DRIVER=remote` 只能通过 VPC + token 调 controller。

## 6. 自动化测试

```text
$ CONTROLLER_URL=http://127.0.0.1:19000 CONTROLLER_TOKEN=testtoken \
  REQUIRE_DOCKER_TESTS=1 python -m unittest discover -s backend/tests -t backend -p "test_controller_container.py" -v
test_container_name_is_destroyed_after_teardown ... ok
test_lifecycle_and_isolation ... ok
test_rejected_approval_is_not_executed ... ok
test_timeout_is_killed ... ok
Ran 4 tests ... OK
```

`test_rejected_approval_is_not_executed` 覆盖完整链路：Agent → Policy → Approval(拒绝) → **未执行** → Trace。
