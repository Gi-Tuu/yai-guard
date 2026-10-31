# 沙箱选型证据台账（research/03-sandbox-证据.md）

> 采集时间：**2026-10-31（+08:00）**，全部页面于该日访问。
> 规则：只记录「原文摘录 + 它证明了什么」；推断性结论单独标 `[推断]`；未能核实标 `[未核实]`。
> 免责：网页正文可能随版本变化，README/文档引用以本台账日期为准。

---

## A. 赛题（Challenge 1「Blast Radius Zero」）

### A-1 Vultr Community 赛事页（**权威度最高：原文含必需栈**）
- URL：<https://community.vultr.com/e/vultr-agent-rush-hackathon>
- 访问：2026-10-31
- 关键原文：
  > “**Containment-first is required**: an agent that only chats is a demo, one that executes safely is a product.”
  > “**Required stack** … **Sandbox boundary: containers or throwaway instances on Vultr, never inside the app process. Docker strongly recommended; OpenSandbox, gVisor and E2B are good starting points.**”
  > “**Vultr Serverless Inference**: all agent LLM calls. OpenAI-compatible, base URL `https://api.vultrinference.com/v1`, vision-capable models available.”
  > “**Vultr API (optional)**: spin up and destroy a throwaway instance per task.”
  > “**Containment focus areas** — Process isolation: execution never touches the host or app runtime. Secret hygiene: no API keys or credentials inside the sandbox. Resource limits: time and memory caps on every run. Lifecycle discipline: reset or destroy the environment after each task.”
  > “Recorded demo video including **one containment moment**, such as the sandbox absorbing an `rm -rf`, an infinite loop, or a hostile web page.”
- 证明：赛题强制/推荐的原文；四类 containment focus；demo 必须有 containment moment；一次性实例是可选加分。
- 状态：**已核实**

### A-2 lablab.ai 赛事页（**技术栈卡片原文**）
- URL：<https://lablab.ai/ai-hackathons/vultr-hackathon>
- 访问：2026-10-31
- 关键原文：
  > “**Agent sandboxing** — Challenge 1 requires isolated execution. **Docker is strongly recommended**, with **OpenSandbox, gVisor, E2B Sandboxes and Microsandbox** listed as example approaches.”
  > “Both challenges require a Vultr VM backend, and the web application must be deployed on Vultr. Vultr should be the central system of control and orchestration, not just static hosting.”
  > “**Vultr GPUs are not available for this event.** Challenge 1 must use Vultr Serverless Inference for agent LLM calls.”
- 证明：五个推荐方案全部进矩阵；GPU 不可用（影响是否需要 GPU 沙箱）；Vultr VM 是控制面。
- 状态：**已核实**

---

## B. Vultr 官方 agent sandboxing 指南（**参考架构**）

- URL：<https://docs.vultr.com/how-to-set-up-agent-sandboxing-on-vultr-cloud-compute>
- 访问：2026-10-31
- 关键原文：
  > “Agent sandboxing runs code an AI agent generates inside an isolated environment, so a bug, a prompt-injection attack, or malicious output cannot reach the host. **Microsandbox** is an open-source platform built for this, launching each sandbox as its own microVM on `libkrun` over the Kernel-based Virtual Machine (**KVM**), with a **separate kernel from the host** rather than the shared-kernel isolation a container provides.”
  > “Have access to a **Vultr VX1™ Cloud Compute** instance running **Ubuntu 24.04** as a non-root user with sudo privileges.”
  > `sudo usermod -aG kvm $USER` / `newgrp kvm` / `id` / `ls -l /dev/kvm`（“Verify the output reports a character device that belongs to the `kvm` group.”）
  > `msb doctor` 期望输出：`✓ CPU virt svm`、`✓ KVM device /dev/kvm`、`✓ KVM access read/write`
  > “Create a named, detached sandbox with explicit CPU and memory limits.” → `msb create --name sandbox01 --cpus 1 --memory 256M debian`
  > “Verify the sandbox has **outbound internet access by default**.” → `REACHABLE`
  > “Microsandbox denies traffic between separate microVMs by default … Each sandbox reaches the public internet, but not its siblings, and the host needs no firewall rules to enforce this.”
  > 文件进出：`msb copy /tmp/hostfile.txt SANDBOX_NAME:/tmp/hostfile.txt`、`msb copy SANDBOX_NAME:/tmp/result.txt /tmp/result-from-sandbox.txt`
- 证明：官方参考架构 = VX1 + KVM + microVM；KVM 是硬前置；microVM 之间默认隔离、**但默认放通公网**（我们必须额外收紧）；文件进出方式。
- 状态：**已核实**

### B-2 Vultr VX1 文档（virtualization 承诺）
- URL：<https://docs.vultr.com/vultr-vx1-cloud-compute>
- 访问：2026-10-31
- 关键原文：
  > “**Supports additional CPU features including support for virtualization**”
  > “Dedicated CPU resources ensuring predictable performance over time even under the heaviest workloads”
  > “**Currently available in New Jersey (`ewr`)** with expansion regions coming soon.”
- 证明：VX1 明确宣称支持虚拟化（这是 `/dev/kvm` 可用的间接依据）；区域目前主要 `ewr`。
- 状态：**已核实**（`/dev/kvm` 实机仍须 Day-0 `ls -l /dev/kvm` 一票确认）

### B-3 Vultr 定价页（成本 + provision 时间）
- URL：<https://www.vultr.com/pricing/>
- 访问：2026-10-31
- 关键原文：
  > VX1：“…dedicated CPU resources with full support for features such as virtualization, **instant provisioning in under 15 seconds**… billed on actual hours used each month and are not capped at 672 hours.”
  > VX1 General Purpose：**2 vCPU/8 GB = $0.060/hr**；**4 vCPU/16 GB = $0.120/hr**；8 vCPU/32 GB = $0.240/hr
  > Cloud Compute Regular：**1 vCPU/1 GB = $5.00/mo（$0.007/hr）**；2 vCPU/2 GB = $15.00/mo
  > Cloud Compute High Performance（AMD）：1 vCPU/1 GB = $6.00/mo（$0.009/hr）；2 vCPU/4 GB = $24.00/mo（$0.036/hr）
- 证明：成本维度的真实数字；一次性实例的 provision 下限（15s）。
- 状态：**已核实**（价格为 2026-10-31 页面值，可能变动）

---

## C. 方案事实源

### C-1 gVisor 平台（**关键结论：不需要 KVM**）
- URL：<https://gvisor.dev/docs/user_guide/platforms/>
- 访问：2026-10-31
- 关键原文：
  > “If you intend to run the **KVM** platform, you will also need to have KVM installed on your system… your user has permissions to access the `/dev/kvm` device.”
  > “**For best performance, use the KVM platform on bare-metal machines only.** If you have to run gVisor within a virtual machine, the **`systrap`** platform will often yield better performance than KVM.”
  > “A third platform, `ptrace`, also has the versatility of running on **any environment**. However, it has higher performance overhead than `systrap` in almost all cases. **`systrap` replaced `ptrace` as the default platform in mid-2023.**”
  > “The platform is selected by the `--platform` command line flag passed to `runsc`. **By default, the `systrap` platform is selected.**”
  > 示例 `daemon.json`：`{"runtimes":{"runsc":{"path":"/usr/local/bin/runsc","runtimeArgs":["--platform=kvm"]}}}`
- 证明：gVisor 默认平台 `systrap` 无需 KVM，可在普通 Vultr VM 上跑；只有 `--platform=kvm` 才需要 `/dev/kvm` + 嵌套虚拟化。→ 我们的强化档不依赖 KVM。
- 状态：**已核实**

### C-2 gVisor 安装（**含 2026 年安装方式变化**）
- URL：<https://gvisor.dev/docs/user_guide/install/>
- 访问：2026-10-31
- 关键原文：
  > “**gVisor supports x86_64 and ARM64, and requires Linux 5.6+.**”
  > apt 源：`curl -fsSL https://gvisor.dev/archive.key | sudo gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg`；`deb [arch=$(dpkg --print-architecture) signed-by=…] https://storage.googleapis.com/gvisor/releases release main`；`sudo apt-get update && sudo apt-get install -y runsc`；“If you have Docker installed, it will be automatically configured.”
  > “To install gVisor as a Docker runtime… `sudo /usr/local/bin/runsc install` / `sudo systemctl reload docker` / `docker run --rm --runtime=runsc hello-world`”
  > “Prior to 2026-07, gVisor releases were limited to 2 binaries (`runsc` and `containerd-shim-runsc-v1`)… **This auto-download functionality will be dropped at the end of September 2026.**”
- 证明：Ubuntu 24.04 的安装路径；Linux 5.6+；**2026 年必须用 apt/tarball 完整安装**（不能只拷 `runsc`）。
- 状态：**已核实**

### C-3 gVisor Docker Quick Start（`runsc install` + 验证）
- URL：<https://gvisor.dev/docs/user_guide/quick_start/docker/>
- 访问：2026-10-31
- 关键原文：
  > “First you will need to configure Docker to use `runsc` by adding a runtime entry to your Docker configuration (e.g. `/etc/docker/daemon.json`). The easiest way to this is via the `runsc install` command.”
  > “You must restart the Docker daemon after installing the runtime.”
  > 验证：`docker run --runtime=runsc -it ubuntu dmesg` → 输出 `[ 0.000000] Starting gVisor...`
  > “Note that this is easily replicated by an attacker so applications should never use `dmesg` to verify the runtime in a security sensitive context.”
- 证明：切换 runtime 的具体命令；`dmesg` 只能当 demo 演示、不能当安全验证（我们审计里用更可靠的方式：容器 ID + runtime 字段）。
- 状态：**已核实**

### C-4 Microsandbox 文档 · Linux 排障（**强依赖 KVM**）
- URL：<https://docs.microsandbox.dev/troubleshooting/linux>
- 访问：2026-10-31
- 关键原文：
  > “microsandbox local sandboxes use **hardware virtualization**. On Linux, confirm that the host has **glibc 2.28 or newer** and that **KVM is available through `/dev/kvm`**.”
  > “If you are inside a cloud VM, CI runner, or another hypervisor, **the outer environment must expose nested virtualization before `/dev/kvm` can appear**.”
  > `lsmod | grep kvm`、`ls -l /dev/kvm`、`[ -r /dev/kvm ] && [ -w /dev/kvm ]`
- 证明：Microsandbox（Linux）**无 KVM 不可用**；普通云 VM 默认未必有嵌套虚拟化 → 决定它只能是 Plan B。
- 状态：**已核实**

### C-5 Microsandbox 文档 · 网络策略（**默认放通公网，需加固**）
- URL：<https://docs.microsandbox.dev/networking/overview>
- 访问：2026-10-31
- 关键原文：
  > “**Sandboxes can reach the public internet by default. Private networks, the host, loopback, link-local addresses, and cloud metadata endpoints are blocked.** Inbound traffic needs a published port.”
  > `msb create python --name isolated --no-net`
  > `--net-default-egress deny --net-rule "allow@public:tcp:443,allow@dns"`
  > “Local sandboxes use `single-tenant` by default. Use `multi-tenant` for shared hosts: it enforces public-network access, DNS rebinding protection, and stricter limits.”
  > 域名规则：“**Strict hostname policy mode is enabled by default: hostname allowlists fail closed** unless microsandbox can inspect the request authority.”（HTTPS 需 TLS 拦截）
- 证明：若采用 Plan B，网络安全故事可补强：`--no-net`、default-deny、hostname fail-closed、multi-tenant profile；但**默认状态不安全，需要显式配置**。
- 状态：**已核实**

### C-6 Microsandbox 文档 · Introduction（定位与 SDK）
- URL：<https://docs.microsandbox.dev/getting-started/introduction>
- 访问：2026-10-31
- 关键原文：
  > “microsandbox is a **local-first microVM runtime** for untrusted workloads… Each sandbox is a **lightweight VM with its own Linux kernel, filesystem, and network boundary**.”
  > “**Hardware isolation.** Each sandbox is a VM, not a container namespace on the host kernel.”
  > “**Fast startup.** Sandboxes are lightweight enough to create from application code.”
- 证明：microVM 隔离叙事；但「fast」无具体数字 → 我们的矩阵里标 `[估算]`。
- 状态：**已核实（定性）**；启动延迟数字 **未核实**

### C-7 OpenSandbox 架构（**Docker 模式不需要 k8s**）
- URL：<https://open-sandbox.ai/architecture/>
- 访问：2026-10-31
- 关键原文：
  > “The server selects its lifecycle implementation based on `[runtime].type`: `docker` -> `DockerSandboxService`; `kubernetes` -> `CompositeSandboxService`… **The top-level `runtime.type` configuration option remains strictly `docker` or `kubernetes`.**”
  > “The **Docker runtime** is the local and single-host backend. It talks directly to the Docker daemon… Create containers with CPU, memory, GPU, platform, **capability, AppArmor, seccomp, PID**, and secure-runtime settings… Support network modes `host`, `bridge`, `none`, `pasta`, `slirp4netns`, and custom user-defined networks… Attach an **egress sidecar** when `networkPolicy` is requested.”
  > “**Credential Vault**: … manage sandbox-local credential bindings and token injection without exposing raw secrets to the workload.”
- 证明：OpenSandbox 的 Docker runtime 不需要 k8s；能力齐全（capability/AppArmor/seccomp/PID/网络模式/egress sidecar/vault）→ 可作架构参考，但集成成本高。
- 状态：**已核实**

### C-8 OpenSandbox Quick Start（前置依赖）
- URL：<https://open-sandbox.ai/getting-started/>
- 访问：2026-10-31
- 关键原文：
  > “**Prerequisites** — **Docker** Engine 20.10+ (for local execution); **Python** 3.10+; **uv** (recommended) or pip”
  > `uvx opensandbox-server init-config ~/.sandbox.toml --example docker` / `uvx opensandbox-server` / `curl http://127.0.0.1:8080/health`
  > SDK：`pip install opensandbox` / `npm install @alibaba-group/opensandbox`
- 证明：引入 OpenSandbox = 多一个 FastAPI 控制面 + 自有 API/SDK 适配；5 天性价比低。
- 状态：**已核实**

### C-9 OpenSandbox 仓库（项目活跃度 / 隔离后端）
- URL：<https://github.com/opensandbox-group/OpenSandbox>
- 访问：2026-10-31
- 关键原文（仓库描述与提交活动）：
  > “Secure, Fast, and Extensible Sandbox runtime for AI agents.”；4,195 Commits；最新提交 2026-10-30；含 `kubernetes/`、`server/`、`sdks/`、`components/`、`oseps/`
  > OSEP 提交说明提及 “`ptrace` platform is deprecated (and very slow, it shouldn't be used)”（该描述位于 oseps 目录提交信息）
- 证明：项目演进快（每日提交）→ 版本漂移是集成风险；同时印证 gVisor `ptrace` 已弃用。
- 状态：**已核实**（仅作为活跃度与生态参考，未做功能验证）

### C-10 E2B（**云服务，不在 Vultr 上**）
- URL：<https://docs.e2b.dev/>（原 <https://e2b.dev/docs> 重定向）
- 访问：2026-10-31
- 关键原文：
  > “E2B provides **isolated cloud sandboxes** where AI agents safely execute code, process data, and run tools.”
  > “Sandbox — A fast, secure Linux VM created on demand for your agent, which you can pause and resume as needed”
- URL：<https://docs.e2b.dev/faq>
  > “E2B bills per second, only while a sandbox is running, based on the vCPU and RAM allocated to it.”
  > “## Can I run sandboxes in the EU? Yes. E2B offers a shared EU cluster… Each region is a separate deployment. Contact support to get a project in the new region.”
  > “Do you publish egress IP ranges (CIDRs)? **No.** Sandbox outbound traffic leaves from ephemeral, rotating IPs.”
- 证明：E2B 沙箱运行在 E2B 自己的云集群（按 region 部署），**不在我们的 Vultr VM 上** → 违反赛题「containers or throwaway instances **on Vultr**」。作为「沙箱边界」方案淘汰；可作为 egress/配额设计参考。
- 状态：**已核实（结论）**；`[未核实]`：E2B 自托管（BYOC）在 Vultr 上的可行性/成本（未查证，且 5 天内不可行）

### C-11 Firecracker（**需要 KVM / 裸金属**）
- URL：<https://github.com/firecracker-microvm/firecracker/blob/main/README.md>
- 访问：2026-10-31
- 关键原文：
  > “The main component of Firecracker is a virtual machine monitor (VMM) that uses the **Linux Kernel Virtual Machine (KVM)** to create and run microVMs.”
  > “**Tested platforms**”（表格中全部为 `m5n.metal` / `m6i.metal` / `m7a.metal-48xl` / `m8g.metal-…` 等 **裸金属** 机型）
  > “The overall security of Firecracker microVMs… depends on a well configured Linux host operating system.”
- 证明：直用 Firecracker 需 KVM + 自建 kernel/rootfs/jailer + 生产宿主加固；5 天不可行。
- 状态：**已核实**

---

## D. Docker 加固参数事实源

### D-1 `docker run` 官方参考（**逐 flag 依据**）
- URL：<https://docs.docker.com/reference/cli/docker/container/run/>（另取 markdown 版 `…/run.md` 交叉核对）
- 访问：2026-10-31
- 关键原文（选项表逐条）：
  > `--cap-drop` … “Drop Linux capabilities”
  > `--cgroupns` … `'private': Run the container in its own private cgroup namespace`
  > `--cpus` … “Number of CPUs”（API 1.25+）
  > `--ipc` … `"none": Own private IPC namespace, with /dev/shm not mounted.`
  > `--memory` … “Memory limit”；`--memory-swap` … “Swap limit equal to memory plus swap”
  > `--network` … “Connect a container to a network”
  > `--pid` … `'host': use the host's PID namespace inside the container`（我们禁用）
  > `--pids-limit` … “Tune container pids limit (set -1 for unlimited)”
  > `--read-only` … “Mount the container's root filesystem as read only”
  > `--runtime` … “Runtime to use for this container”
  > `--security-opt` … “Security Options”
  > `--tmpfs` … 示例：`docker run -d --tmpfs /run:rw,noexec,nosuid,size=65536k my_image`（“options… identical to the Linux `mount -t tmpfs -o` command”）
  > `--storage-opt` … `docker run -it --storage-opt size=120G fedora /bin/bash`（“only available for the `btrfs`, `overlay2`, `windowsfilter`, and `zfs` storage drivers”；overlay2 需 “backing filesystem is `xfs` and mounted with the `pquota` mount option”）
  > `--ulimit` … “Ulimit options”；`-u, --user` … “Username or UID (format: <name|uid>[:<group|gid>])”
  > `--privileged` … “A container with `--privileged` is not a securely sandboxed process. Containers in this mode can get a root shell on the host and take control over the system.”
- 证明：本文 §4 的每条 flag 都是真实存在的官方参数；`--storage-opt size` 有前提条件（xfs+pquota），所以磁盘限额我们改用 `--mount type=tmpfs,tmpfs-size=…`。
- 状态：**已核实**
- `[未核实]`：`--security-opt=no-new-privileges` 的官方逐字说明 —— 该页在本轮抓取中于 50000 字符处被截断（`security-opt` 段落未取到）。仅由 OWASP 与社区文档佐证（见 D-4），Day-0 用冒烟命令确认可用。

### D-2 Docker 默认 seccomp（**不改默认 profile**）
- URL：<https://docs.docker.com/engine/security/seccomp/>
- 访问：2026-10-31
- 关键原文：
  > “The **default `seccomp` profile** … provides a sane default for running containers with seccomp and **disables around 44 system calls out of 300+** … In effect, the profile is an allowlist that denies access to system calls by default.”
  > “**It is not recommended to change the default `seccomp` profile.**”
  > 被屏蔽 syscall 示例：`mount`、`umount`、`ptrace`、`keyctl`/`add_key`/`request_key`、`bpf`、`unshare`、`setns`、`pivot_root`、`reboot`、`kexec_load`、`io_uring_setup`/`io_uring_enter`/`io_uring_register`（“Blocked due to security vulnerabilities that can be exploited to break out of containers”）、`open_by_handle_at`、`perf_event_open`、`userfaultfd`
- 证明：默认 seccomp 已是强 allowlist，覆盖 `io_uring`/`keyctl`/`unshare` 等常见逃逸面；我们**不替换**，只继承。
- 状态：**已核实**

### D-3 Docker AppArmor（`docker-default`）
- URL：<https://docs.docker.com/engine/security/apparmor/>
- 访问：2026-10-31
- 关键原文：
  > “Docker automatically generates and loads a default profile for containers named **`docker-default`**.”
  > “When you run a container, it uses the `docker-default` policy unless you override it with the `security-opt` option. For example… `docker run --rm -it --security-opt apparmor=docker-default hello-world`”
  > 自定义 profile：`apparmor_parser -r -W /path/to/your_profile` 后 `docker run --rm -it --security-opt apparmor=your_profile hello-world`
  > 校验：`docker info --format '{{json .SecurityOptions}}'`
- 证明：`--security-opt=apparmor=…` 语法正确；`docker-default` 默认启用；可用自定义 profile 收紧。
- 状态：**已核实**

### D-4 OWASP Docker Security Cheat Sheet（`no-new-privileges` 写法佐证 + `--read-only`）
- URL：<https://cheatsheetseries.owasp.org/cheatsheets/Docker_Security_Cheat_Sheet.html>
- 访问：2026-10-31（经搜索摘要索引到该页，非 Docker 官方）
- 关键原文（摘要给出）：
  > `docker run --read-only alpine sh -c 'echo "whatever" > /tmp'`
  > “Prevent fork bombs … `--pids-limit 99` + `--memory="1g" --cpus="2"`”
  > `security_opt: [no-new-privileges:true]`（Compose 等价写法）
- 证明：`no-new-privileges` 与 `--pids-limit` 的社区标准写法。**注意：非 Docker 官方页，仅作交叉佐证。**
- 状态：**部分核实（第三方）**

### D-5 Docker Engine security（能力/命名空间/cgroup 的官方论述）
- URL：<https://docs.docker.com/engine/security/>
- 访问：2026-10-31
- 关键原文：
  > “**Control Groups** … they are essential to fend off some denial-of-service attacks.”
  > “By default Docker drops all capabilities except those needed, an allowlist instead of a denylist approach… **The best practice for users would be to remove all capabilities except those explicitly required.**”
  > “**Docker daemon attack surface** … only trusted users should be allowed to control your Docker daemon… you can start a container where the `/host` directory is the `/` directory on your host; and the container can alter your host filesystem without any restriction.”
  > “Exposing the daemon API over HTTP without TLS is not permitted, and such a configuration causes the daemon to fail early on startup.”
  > “User Namespaces… available but not enabled by default.”
- 证明：`--cap-drop=ALL`、禁宿主挂载、禁 TCP 暴露 dockerd、userns 非默认 —— 全部有官方依据。
- 状态：**已核实**

---

## E. 二手/待核实项（不得当事实使用）

| 项 | 内容 | 来源 | 状态 |
|---|---|---|---|
| E-1 | “gVisor 启动开销约 10–50ms；Kata(QEMU)≈500ms；Kata(Firecracker)≈125ms” | 第三方对比表（note.com 文章与 Northflank 博客的转述） | `[第三方，未核实]` 仅用于矩阵量级参考 |
| E-2 | “gVisor 不实现 iptables `nat` 表，因此不能与使用 nat REDIRECT 做 DNS 拦截的 egress sidecar 组合” | 同上（第三方） | `[第三方，未核实]` 已在我方 L3 设计里规避（用 `--network=none` + 代理，而非 nat REDIRECT） |
| E-3 | 普通 Vultr Cloud Compute 是否暴露 `/dev/kvm` | 未找到 Vultr 官方承诺 | `[未核实]` → Day-0 一票确认；设计上「默认没有」 |
| E-4 | Microsandbox / Firecracker 的具体冷启动毫秒数 | 官方仅给「fast / lightweight」定性 | `[未核实]` |
| E-5 | cgroup v2 的 `pids` controller 在 Vultr Ubuntu 24.04 上是否启用 | 未找到 Vultr 侧文档；Ubuntu 24.04 默认 cgroup v2 属发行版常识 | `[未核实]` → `cat /sys/fs/cgroup/cgroup.controllers` |
| E-6 | `--security-opt=no-new-privileges` 官方逐字文档 | Docker `docker run` 参考页 `security-opt` 段被抓取截断 | `[未核实，命令本身为真]` |

---

## F. 复核清单（每次更新本文件时重跑）

```bash
# 1) 页面可达性（只需 200）
for u in \
  https://community.vultr.com/e/vultr-agent-rush-hackathon \
  https://lablab.ai/ai-hackathons/vultr-hackathon \
  https://docs.vultr.com/how-to-set-up-agent-sandboxing-on-vultr-cloud-compute \
  https://docs.vultr.com/vultr-vx1-cloud-compute \
  https://gvisor.dev/docs/user_guide/platforms/ \
  https://gvisor.dev/docs/user_guide/install/ \
  https://docs.microsandbox.dev/troubleshooting/linux \
  https://open-sandbox.ai/architecture/ \
  https://docs.e2b.dev/faq \
  https://github.com/firecracker-microvm/firecracker/blob/main/README.md \
  https://docs.docker.com/reference/cli/docker/container/run/ ; do
  printf '%s -> ' "$u"; curl -o /dev/null -s -w '%{http_code}\n' -L "$u"
done
```

| 复核结论 | 日期 |
|---|---|
| 以上 11 条主证据均在 2026-10-31 抓取成功（1 条 Docker 参考页被 50000 字符截断，已标注） | 2026-10-31 |
