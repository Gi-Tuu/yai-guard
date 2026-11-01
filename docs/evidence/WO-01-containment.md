# WO-01 · 沙箱镜像 + 真实 containment + 宿主取证

> 日期：2026-10-31 ｜ 环境：Linux 容器（Docker Desktop 29.7.2，cgroup v2，Windows 11 宿主）
> 镜像：`yaiguard/sandbox-py:1.0`（498 MB，manifest `3de0558cd8de`）
> 结论：**M1/M2/M3 全部在真实容器里复现**；宿主进程数 / 可用磁盘 / 容器数前后一致。

## 1. 镜像构建

```text
$ docker build -f deploy/sandbox/Dockerfile -t yaiguard/sandbox-py:1.0 .
...
Successfully installed contourpy-1.4.0 cycler-0.12.1 fonttools-4.68.0 kiwisolver-1.5.1
matplotlib-3.10.8 numpy-2.3.5 packaging-26.3 pandas-3.0.1 pillow-13.0.0
pyparsing-3.3.3 python-dateutil-3.0.0 six-1.17.0 tzdata-2026.6
...
naming to docker.io/yaiguard/sandbox-py:1.0 done
```

首次构建曾因 `auth.docker.io` 连接超时失败，重试后成功（网络问题，非 Dockerfile 问题）。

## 2. M1 · 只读根

```bash
docker run --rm --network=none --read-only --cap-drop=ALL --user=65534:65534 \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --mount type=tmpfs,destination=/workspace,tmpfs-size=67108864,tmpfs-mode=1777 \
  yaiguard/sandbox-py:1.0 sh -c \
  'rm -rf --no-preserve-root /; echo rm_exit=$?; echo ok > /workspace/x; echo write_exit=$?; cat /workspace/x'
```

原始输出（共 **42,349 行**，全部为只读错误）：

```text
rm: cannot remove '/tmp': Read-only file system
rm: cannot remove '/run/lock': Read-only file system
rm: cannot remove '/mnt': Read-only file system
...
rm: cannot remove '/workspace': Read-only file system
rm_exit=1
write_exit=0
ok
```

- 根文件系统纹丝不动（`rm_exit=1`，每条都是 `Read-only file system`）。
- `/workspace` 是 tmpfs：`write_exit=0`，写入成功并可 `cat` 回读。
- 注意：`rm` 尝试 `rmdir /workspace` 时也报只读，这是挂载点语义（GNU rm 不会真正删除挂载点），不影响 tmpfs 可写结论。

## 3. M2 · fork bomb

```bash
docker run -d --name yaig-m2 --pids-limit=64 --memory=128m --network=none \
  yaiguard/sandbox-py:1.0 sh -c 'b(){ b|b& }; b; sleep 10'
sleep 2 && docker logs yaig-m2
```

原始输出（共 **3,978 行**）：

```text
sh: 1: sh: 1: sh: 1: sh: 1: Cannot forkCannot forkCannot forkCannot fork
sh: 1: Cannot fork
sh: 1: Cannot fork
...
container_status=Up 2 seconds
```

- pids 限额生效，进程创建被内核拒绝；日志中无 `out of memory`。
- 容器仍存在（被 `sleep 10` 保活），说明 fork bomb 没有把容器/宿主拖垮；随后 `docker rm -f` 回收。

## 4. M3 · 零网络

```bash
docker run --rm --network=none --read-only --cap-drop=ALL --user=65534:65534 \
  --mount type=tmpfs,destination=/workspace,tmpfs-size=67108864,tmpfs-mode=1777 \
  yaiguard/sandbox-py:1.0 python3 -c \
  "import socket; socket.create_connection(('example.com', 443), timeout=3)"
```

原始输出：

```text
exit=1
    for res in _socket.getaddrinfo(host, port, family, type, proto, flags):
socket.gaierror: [Errno -3] Temporary failure in name resolution
```

容器内网络接口：

```text
interfaces ['lo']
```

- 出站连接失败（DNS 解析失败），容器只有一个 loopback 接口。
- 应用层策略（`D-METADATA` / `D-NET-PRIVATE`）是第一道门；`--network=none` 是兜底。

## 5. 身份 / capability / 挂载

```text
uid=65534(nobody) gid=65534(nogroup) groups=65534(nogroup)
CapEff:	0000000000000000
mount: drop permissions failed.
mount_denied
```

- 非 root（uid 65534）。
- 零有效 capability（`CapEff: 0000000000000000`）。
- `mount` 被拒绝（无 CAP_SYS_ADMIN）。

## 6. 宿主取证

Linux VM 上用 [`deploy/evidence/host_fingerprint.sh`](../../deploy/evidence/host_fingerprint.sh)：

```bash
bash deploy/evidence/host_fingerprint.sh > /tmp/host-before.txt
# 跑 M1 / M2 / M3 ...
bash deploy/evidence/host_fingerprint.sh > /tmp/host-after.txt
diff -u /tmp/host-before.txt /tmp/host-after.txt || true
```

Windows 本地无法运行 MSYS 版脚本（`/etc`、`df -i` 指向 Git 安装目录，`ss` 不存在），因此本轮用 PowerShell 等价项：

```text
before: {"tcp":413,"procs":473,"containers":0,"freeGB":51.75}
after : {"tcp":424,"procs":473,"containers":0,"freeGB":51.75}
```

- 进程数 473 → 473（不变）
- C 盘可用 51.75 GB → 51.75 GB（不变）
- 容器数 0 → 0（一次性容器已回收）
- TCP 端点 413 → 424：开发机后台进程噪声，不是攻击产生的连接（攻击容器 `--network=none`）

**真实 Linux 宿主的 `df -i` / `/etc` 文件数对比在 Vultr sandbox VM 上执行（WO-04 / Day-1）**，本文件不伪造该项结果。

## 7. 集成测试

```text
$ python -m unittest discover -s backend/tests -t backend -p "test_docker_backend.py" -v
test_m1_readonly_root_but_workspace_writable ... ok
test_m2_fork_bomb_is_absorbed_by_pids_limit ... ok
test_m3_network_is_none ... ok
test_backend_end_to_end ... ok
Ran 4 tests in 5.739s
OK
```

`test_backend_end_to_end` 通过 `DockerSandbox` 走完整生命周期：create → put_file → exec `python3 main.py`（stdout `2`）→ get_file → list_files → `rm -rf --no-preserve-root /` 被只读根拒绝 → destroy。

## 8. gVisor（未启用）

本机 Docker 未安装 `runsc` runtime，本轮**未探测**；`SANDBOX_RUNTIME` 留空即 `runc`。Day-1 在 Vultr 上执行：

```bash
docker info --format '{{json .Runtimes}}'
docker run --rm --runtime=runsc yaiguard/sandbox-py:1.0 python3 -c "import numpy,pandas,matplotlib; print('ok')"
```

若 systrap 可用则启用，失败就如实写“runsc 未启用”，不把交付押在未验证的 KVM 上。
