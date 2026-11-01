# lablab 提交文案（可直接粘贴）

> 提交前用 `wc -m` 复核字符数；日期口径统一用 2026-10-31。

## 标题（≤50 字符）

```text
YAI Guard: Autonomy Without Uncontrolled Access
```

## 短描述（≤255 字符）

```text
Secure execution agent on Vultr: an LLM plans, a deterministic policy gates every call, and commands run inside a hardened one-shot Docker sandbox on a separate VM. Denied actions are explainable; every step is hash-chained for audit.
```

## 长描述（英文，≥100 words）

```text
YAI Guard is an autonomous execution agent built for Challenge 1, "Blast Radius Zero". A
user submits a task in natural language. The planner runs on Vultr Serverless Inference
(DeepSeek V4.1 Flash) and proposes terminal commands and file edits. Before anything
executes, a deterministic policy engine (not the LLM) returns allow, deny, or
require-human-approval with a stable rule id and reason. Approved calls run inside a
hardened, one-shot Docker container on a separate sandbox VM: read-only root, tmpfs
workspace, zero capabilities, non-root uid 65534, pids/memory/CPU limits, and
network=none. The app VM holds the inference key and never mounts docker.sock; the
sandbox VM holds no secrets. Every event is appended to a SHA-256 hash-chained trace
that anyone can re-verify after download. We demonstrate rm -rf / hitting a read-only
root (42,349 read-only errors, workspace still writable), a fork bomb absorbed by the
pids limit, and outbound network failing inside a container that only sees loopback.
This is containment you can reproduce, not a chat demo.
```

## Tags

```text
vultr, serverless-inference, autonomous-agents, sandbox, docker, containment,
security, audit-log, policy-engine, blast-radius-zero
```

## Demo 平台

```text
Vultr VM (app + sandbox, VPC) ｜ https://<domain>/ ｜ 无痕窗口可打开
```

## 提交检查

- [ ] 标题 ≤ 50 字符
- [ ] 短描述 ≤ 255 字符
- [ ] 长描述 ≥ 100 words
- [ ] 仓库公开、LICENSE = MIT
- [ ] Demo URL 无痕可访问
- [ ] 视频 ≤5:00、≤300MB、含真实 containment moment
- [ ] slides / cover image 上传
- [ ] 11-09 03:00 前提交（留 3 小时缓冲）
