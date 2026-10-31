# WO-01 · 沙箱镜像 + 真实 containment + 宿主取证

> 派给：沙箱/后端工程师 ｜ 估时：6h（本机可完成 4h）｜ 依赖：无 ｜ 可开工：**现在**
> 先读：`docs/03`、`docs/04` §4/§6/§9、`docs/08` §6

## 0. 背景

骨架里的 `DockerSandbox` 只有参数（`build_run_args`），真实镜像还没构建、M1/M2/M3 还没在真实容器跑过。本单目标：把「沙箱真的能吃掉攻击」变成可复现证据，供视频与 README 使用。

## 1. 交付物

| 文件 | 内容 |
|---|---|
| `deploy/sandbox/Dockerfile` | 可构建的沙箱镜像（python 数据栈，uid 65534，/workspace） |
| `deploy/evidence/host_fingerprint.sh` | 宿主取证脚本：`df -i /`、`find /etc -xdev \| wc -l`、`uptime`、`ps -e \| wc -l`、`ss -tunp \| wc -l`、`docker ps -a` |
| `backend/tests/test_docker_backend.py` | 真实 Docker 集成测试（无 daemon 时 skip，不要 fail） |
| `docs/evidence/WO-01-containment.md` | 每条命令 + 真实输出 + 宿主前后对比 |

**必须覆盖的 3 个场景（直接 `docker exec`，绕过策略，证明内核层兜底）：**

1. **M1 只读根**：`rm -rf --no-preserve-root /` → `Read-only file system`；`/workspace` 仍可写。
2. **M2 fork bomb**：`b(){ b\|b& }; b` → `can't fork: Resource temporarily unavailable`；容器退出/无 OOM。
3. **M3 零网络**：`curl -sS https://example.com` + `wget http://169.254.169.254/` → DNS/连接失败；容器内 `ip addr` 只有 `lo`。

**加分（可选，1h）**：`runsc` 安装探测，记录 `docker run --runtime=runsc` 跑 `numpy/pandas/matplotlib` 是否 OK；失败就如实写"未启用"。

## 2. 验收（逐条可复制）

```bash
# 1) 构建
docker build -f deploy/sandbox/Dockerfile -t yaiguard/sandbox-py:1.0 .

# 2) M1
docker run --rm --network=none --read-only --cap-drop=ALL --user=65534:65534 \
  --mount type=tmpfs,destination=/workspace,tmpfs-size=67108864,tmpfs-mode=1777 \
  yaiguard/sandbox-py:1.0 sh -c 'rm -rf --no-preserve-root /; echo exit=$?; echo ok > /workspace/x'

# 3) M2（detached + 观察，避免挂住终端）
docker run -d --rm --name m2 --pids-limit=64 --memory=128m yaiguard/sandbox-py:1.0 \
  sh -c 'b(){ b|b& }; b; sleep 1; echo after'
sleep 3 && docker logs m2 2>&1 | head -3 && docker rm -f m2

# 4) 宿主取证（前后各跑一次）
bash deploy/evidence/host_fingerprint.sh > /tmp/host-before.txt
# ... 跑 M1/M2/M3 ...
bash deploy/evidence/host_fingerprint.sh > /tmp/host-after.txt
diff -u /tmp/host-before.txt /tmp/host-after.txt || true

# 5) 集成测试
python -m unittest discover -s backend/tests -t backend -p "test_docker_backend.py" -v
```

通过标准：M1 输出只读错误且 `/workspace` 可写；M2 出现 `can't fork`；M3 无网络；宿主 `df -i`/文件数/进程数/连接数前后一致（允许噪声）。

## 3. 写文件范围

`deploy/sandbox/`、`deploy/evidence/`、`backend/app/sandbox/`、`backend/tests/test_docker_backend.py`、`docs/evidence/`。

## 4. 禁止

- 禁止 `--privileged`、`--pid=host`、`--network=host`、宿主目录挂载、挂 docker.sock。
- 禁止为了让演示好看而放宽镜像/权限；失败就写失败。
- 不碰 `backend/app/policy/`、`web/`、`backend/app/main.py`。

## 5. 接口契约

若发现 `DockerSandbox` 有 bug（例如 `get_file` base64、超时 kill），直接修 `backend/app/sandbox/docker_backend.py`，但**不要改 `SandboxBackend` 的方法签名**（`interface.py` 归架构，先问）。

## 6. 坑

- Windows 上 Docker Desktop 用 Linux 容器；`docker exec -w` 的路径是容器内路径。
- `--read-only` 下 `/workspace` 必须是 tmpfs 挂载，否则无法写。
- fork bomb 用 ash 兼容写法 `b(){ b|b& }; b`；`:(){...}` 在 busybox 会语法错误。
- 证据里的输出要贴**原始文本**，不要润色。
