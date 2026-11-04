# P1-3 · 执行输出硬性限额验证

> 日期：2026-11-02 ｜ 目标：宿主机不会因为沙箱命令的无界 stdout/stderr 而内存膨胀。

## 1. 实现

`DockerSandbox.exec()` 不再使用 `subprocess.run(capture_output=True)`，改为
`run_bounded()`：

- 两个 reader 线程分别持续 drain stdout/stderr（避免管道写满死锁）；
- 每路只保留 `max_output_bytes`（默认 8 KB）；
- 两路合计超过 `max_total_output_bytes`（默认 1 MB）立即 kill 进程组；
- 超时同样 kill 进程组；
- 超时/超限时对容器执行 `docker kill`，确保远端进程不残留；
- 返回 `truncated` 与 `killed_reason`（`output_limit` / `wall_clock`），写入 trace。

## 2. 单元测试

```text
$ python -m unittest discover -s backend/tests -t backend -p "test_bounded_output.py" -v
test_small_output_is_exact ... ok
test_huge_stdout_is_capped_and_killed ... ok
test_huge_stderr_is_capped ... ok
test_both_channels_do_not_deadlock ... ok
test_timeout_kills_process ... ok
Ran 5 tests ... OK
```

## 3. 真实 controller 证据（10 MB stdout）

```text
$ curl -X POST .../v1/sandbox/tk_of3/exec -d '{"command":"python3 -c \"print('x'*10000000)\""}'
stdout_len=8207  truncated=True  killed=output_limit  exit=-9
```

- 宿主机只保留约 8 KB（含截断标记），不是 10 MB；
- 进程在超限时被杀（exit -9）；
- 容器随后被销毁，`docker ps -a` 残留 0。

## 4. 边界

- 截断阈值可由 policy 的 `max_output_bytes` / `max_total_output_bytes` 配置；
- 输出内容仍可能含敏感数据，trace 只存 sha256 与字节数（不存原文）。
