# 02 · Vultr Serverless Inference 接入预研

> 项目：YAI Guard（Challenge 1「Blast Radius Zero」）
> 撰写：inference-researcher / 2026-10-31 23:15–23:55 (+08:00)
> 范围：只采信 `api.vultrinference.com/docs`、`docs.vultr.com`、`vultr.com` 产品/定价页、`lablab.ai` 赛事页。
> 未执行任何部署、注册、订阅、计费调用；`/v1/models` 为官方公开只读端点，无需 key。
> 逐条原文摘录与 URL 见 [`research/02-inference-证据.md`](../research/02-inference-证据.md)。

## 0. 结论摘要（可执行的 6 条）

1. **接入形态确定**：`base_url=https://api.vultrinference.com/v1`，`Authorization: Bearer <key>`，OpenAI Python SDK 与 `chat.completions.create` 直接可用；key 在 Vultr Console 的 Serverless Inference 订阅概览页复制，**必须先 provisioning 一个订阅**。
2. **Agent 必需能力全部已核实**：`tools` / `tool_choice` / `role:"tool"` / `finish_reason:"tool_calls"` / SSE `stream:true` / `response_format:{json_object|json_schema}` 均在官方 API 契约与 live catalog 的 `supported_parameters` 中。tool calling 不是阻塞项，**不需要退化到"受控 JSON 输出"方案**（可回滚但非必需）。
3. **模型选型（真实 ID，来自 live catalog）**：主推理 `deepseek-v4.1-flash-1017`（1M ctx，$0.15/$0.60 per 1M）；快速便宜 `qwen3.8-flash-next`（256K ctx，$0.10/$0.20）；vision `qwen3.8-27b`（256K ctx，$0.15/$1.00）。三者均 `tools: true`。
4. **成本量级**：一次 20 轮工具调用的 demo 任务约 **12.9 万 input + 3.4K output token**，主模型约 **$0.021/次**（¥0.15）；按官方 `$0.55/$2.75` 的保守口径也只有 **$0.08/次**。100 次彩排 ≈ $2–8，$200 额度完全够用。
5. **最大风险不是价格而是口径冲突**：`vultr.com/products/cloud-inference` 与 `docs.vultr.com` 支持页写"$0.55 输入 / $2.75 输出 每 1M"，而 live model list 写的是逐模型价（$0.10–$0.75 输入）。**两套价格同时在线**，必须 Day-0 用 `console → Usage` 实测一次 1K token 请求对账，预算按高值（$0.55/$2.75）兜底。
6. **第二个风险是静默降级**：文档明确写"模型 ID 打错不报错，请求会落到 `deepseek-v4.1-flash-1017`"。适配层必须启动时校验 `GET /v1/models` 白名单，否则线上可能悄悄换模型而不自知。

## 1. 已核实事实表

### 1.1 端点与认证

| # | 事实 | 结论 | 来源（访问 2026-10-31） |
|---|---|---|---|
| 1.1.1 | Base URL | `https://api.vultrinference.com/v1` | docs/get-started/quickstart.md；docs/get-started/sdks.md |
| 1.1.2 | 认证头 | `Authorization: Bearer <key>`（`Bearer` 首字母必须大写）；`x-api-key` 为 Anthropic 路由备用，两者同时出现时 `Authorization` 优先 | docs/get-started/authentication.md |
| 1.1.3 | Key 创建位置 | Vultr Console `https://console.vultr.com/inference/` | docs/get-started/quickstart.md、authentication.md |
| 1.1.4 | Key 复制路径 | Products → Serverless → Inference → 点订阅 → Overview 页复制 API Key | docs.vultr.com/.../management/connection |
| 1.1.5 | 是否必须先 provisioning 订阅 | **是**：先 `Add Serverless Inference`（Label + 确认支持模型与计费说明），订阅存在后才有 API Key；用量、文件、限流、webhook secret 均"belong to your subscription" | docs.vultr.com/.../serverless-inference/provisioning；authentication.md |
| 1.1.6 | 无 key 可访问的端点 | `GET /v1/models`、`/v1/models/count`、`/v1/models/{id}`、`/v1/model/{author}/{slug}` 及各模态 model list；**`/v1/health` 需要 key** | authentication.md |
| 1.1.7 | 错误 key | 401 `{"error":{"code":401,"message":"Invalid API key","type":"auth_error"}}`；文档注明"401 never worth retrying" | authentication.md |
| 1.1.8 | 单请求最长耗时 | 约 9 分钟，超时返回 504 | docs/concepts/limits.md |

### 1.2 区域 / endpoint 差异

| # | 事实 | 结论 | 来源 |
|---|---|---|---|
| 1.2.1 | 是否有区域专属 endpoint | **未核实（无任何官方表述）**。文档只给单一全局 base URL，未见 `region` 参数或分区域名 | quickstart / sdks / limits |
| 1.2.2 | 区域体现在模型维度 | 每个模型页有 `Regions:` 字段：`deepseek-v4.1-flash-1017`=SEA、`qwen3.8-flash-next`=SEA、`qwen3.8-27b`=SEA、`mimo-v2.6-flash`=ATL | 各模型 .md 页 |
| 1.2.3 | 跨区影响 | 未核实。推论（假设）：VM 选 SEA 附近区域可降低 RTT；不能用参数指定推理区 | — |

### 1.3 OpenAI 兼容范围矩阵

| 能力 | 状态 | 证据要点 |
|---|---|---|
| `POST /v1/chat/completions` | **已核实** | OpenAI SDK 官方示例直接调用 |
| `GET /v1/models`（OpenAI 形状） | **已核实** | `client.models.list()` 官方示例；支持 `?format=openrouter` |
| SSE streaming `stream:true` | **已核实** | 每 chunk 一行 `data:`，结尾 `data: [DONE]`；**最后一个 chunk 必带 `usage`** |
| tools / function calling | **已核实** | `tools[].type` 目前仅 `function`；`function.name` ≤64 字符；`parameters` 为 JSON Schema；`tool_choice`；消息角色含 `tool`；`finish_reason` 含 `tool_calls`；`tool_call id` 以 `call_` 开头 |
| 流式 tool call | **已核实** | "A tool call arrives in pieces of `tool_calls`: its id and name first, then its arguments, matched by `index`" |
| JSON mode / 结构化输出 | **已核实** | `response_format` 支持 `text` / `json_object` / `json_schema`（含 `strict`）；模型参数表另有 `structured_outputs` |
| vision / 多模态输入 | **已核实（能力层）** | catalog `input_modalities` 含 `image`；`image_url` 部件支持 PNG/JPEG/WebP/GIF，≤20MB，url 或 base64 |
| vision 计费与效果 | **未核实** | 文档只说"a larger picture costs more prompt tokens"，无 per-image token 公式 |
| `reasoning_effort` / `reasoning.max_tokens` | **已核实** | 7 档 effort（ultra…none）；思考文本固定回在 `reasoning` 字段，`reasoning_tokens` 计入 `completion_tokens` |
| Prompt caching | **未核实/大概率为无** | 响应有 `prompt_tokens_details.cached_tokens`，但 `/v1/messages` 明确写"There is no prompt caching"；chat 路由未承诺 |
| 并行 tool calls | **部分核实** | `tool_calls` 是数组、按 `index` 区分，但未承诺一定会并行发起；单轮多工具需自行处理 |
| `max_tokens` / `max_completion_tokens` | **已核实** | `max_tokens` 是 deprecated 别名，`max_completion_tokens` 优先；默认 32768，可上调至上下文窗口 |
| 上下文窗口 | **已核实** | 超窗时答案预算被裁剪且 `finish_reason:"length"`；prompt 单独超窗才 400 `context_length_exceeded` |
| top_k / repetition_penalty / seed / logprobs | **已核实** | 非 OpenAI 标准但已支持，走 `extra_body` |
| 末位 assistant 消息 = prefill | **已核实（坑）** | 若最后一条是 `assistant`，模型会"续写"而非重新回答；Agent 循环必须避免 |

## 2. 模型选型（live catalog 实测，2026-10-31）

> 数据源：`GET https://api.vultrinference.com/v1/models?format=openrouter&output_modalities=text`（公开、无 key 可读），返回 `total_count: 13`。
> 下表价格单位均为 **USD / 1M tokens**（catalog 原文是每 token 字符串，已换算核对）。

| 模型 ID | ctx | max output | in | out | tools | 模态 | 区域 |
|---|---|---|---|---|---|---|---|
| **deepseek-v4.1-flash-1017** | 1,048,576 | 1,048,576 | 0.15 | 0.60 | ✅ | text+image+file | SEA |
| **qwen3.8-flash-next** | 262,144 | 262,144 | 0.10 | 0.20 | ✅ | text+image+file+video | SEA |
| **qwen3.8-27b** | 262,144 | 262,144 | 0.15 | 1.00 | ✅ | text+image+file+video | SEA |
| mimo-v2.6-flash | 1,048,576 | 1,048,576 | 0.10 | 0.25 | ✅ | +audio+video | ATL |
| glm-5.3-flash | 1,048,576 | 1,048,576 | 0.10 | 0.35 | ✅ | text+image+file+video | — |
| glm-5.3 / glm-5.2 | 1,048,576 | 1,048,576 | 0.75 | 3.00 | ✅ | text+image+file | — |
| minimax-m3.1-flash | 1,048,576 | 1,048,576 | 0.20 | 0.90 | ✅ | text+image+file+video | — |
| nemotron-3-nano-omni-30b-a3b-reasoning | 262,144 | 262,144 | 0.10 | 0.25 | ✅ | text+image+file+audio+video | — |
| raon-1-mini-preview | 262,144 | 262,144 | 0.10 | 0.30 | ✅ | text+image+file | — |
| nemotron-3.5-content-safety | 131,072 | 131,072 | 0.05 | 0.15 | ❌ | text+image+file | — |
| muse-glimmer-30b | 131,072 | 131,072 | 0.25 | 1.00 | ✅ | text+image+file+video | — |

**选型理由**

- **主推理 = `deepseek-v4.1-flash-1017`**：1M ctx（20 轮工具调用后仍余量巨大）、reasoning + tool calling、$0.15/$0.60，且是网关默认模型（省 key 配置时的兜底）。工具调用 + 长上下文是 Agent 主循环的硬需求。
- **快速便宜 = `qwen3.8-flash-next`**：$0.10/$0.20，13 个模型里 output 最便宜且带 tools；用于任务分类、工具结果摘要、审计日志生成、名称/步骤抽取这类高频小调用。
- **vision = `qwen3.8-27b`**：官方描述"Compact dense **vision-language** model… strong coding and **agentic** skills"，且 tools 可用——截图验证后可以继续发起动作，避免"看图的模型不会调工具"。
- **不选 glm-5.3**：$0.75/$3.00 是主模型的 5 倍，除非 deepseek 在 agentic 上明显不达标；**不选 mimo**：区域在 ATL，与 SEA 上的主模型不同区，延迟不可控。
- **降级链**：`deepseek-v4.1-flash-1017` → `qwen3.8-flash-next`（同区、同模态、tools 齐全，能力略降但可跑）→ 若仍失败则任务失败并如实回报（不伪造结果）。限流是**按模型独立计数**的，换模型可绕开单模型 429。

## 3. 接入设计

### 3.1 位置与边界

```
Browser ──HTTPS/SSE──▶ App (FastAPI on Vultr VM) ──HTTPS──▶ api.vultrinference.com/v1
                            │  ▲
                     key 只在 │  │ 审计日志(脱敏)
                            ▼  │
                    Docker 沙箱(无外网、无 key、无挂载宿主目录)
```

- App 层是唯一持有 `VULTR_INFERENCE_API_KEY` 的进程；沙箱只通过 App 的 `run_command` 工具间接影响世界，**容器内 `env` 清空 `VULTR_*`、`--network=none`**，即使 Agent 被提示注入也拿不到 key、发不出请求。
- **双 VM 落位**（见 `05` §10 / `07`）：App 与沙箱分处两台 Vultr VM，App 持 key，sandbox VM 零密钥；上图中的"Docker 沙箱"在独立 VM 上执行。
- 前端不直连 Vultr（CORS + key 泄露双风险），统一由 App 转发 SSE。

### 3.2 模型适配层（对齐 `05-MVP架构设计.md` 的 Model Adapter）

```python
class ModelAdapter(Protocol):
    def chat(self, messages, *, tools=None, tool_choice="auto",
             stream=False, model=None, max_tokens=1024,
             timeout=60.0) -> ChatResult: ...
```

- 只实现一个 provider（Vultr），`base_url` 写死常量，不含 OpenAI/Anthropic 分支。
- 三个模型 ID 由 env 注入：`MODEL_PRIMARY`、`MODEL_FAST`、`MODEL_VISION`，默认值即上表三个 ID。
- 启动时 `GET /v1/models` 校验三个 ID 在白名单内；不在则 fail-fast（防证据文件 1.4 条的"静默换模型"）。
- `ChatResult` 统一携带 `content`、`tool_calls`、`finish_reason`、`usage`（含 `reasoning_tokens`）、`model`（回显模型，用于审计）。

### 3.3 超时 / 重试 / 降级

| 项 | 策略 | 依据 |
|---|---|---|
| connect timeout | 10s | 网关 9 分钟上限，短连接足够 |
| read timeout（单请求） | 90s；任务级总预算 300s（5 分钟 demo） | limits.md 约 9 分钟/请求 |
| 429 | 按 `Retry-After` 精确等待，上限 30s，最多 3 次 | limits.md / errors.md |
| 502/503/504 | 指数退避 1s→2s→4s，最多 3 次，末次换备用模型 | errors.md「start at a second or two and double」 |
| 500 | 只重试 1 次 | errors.md「once」 |
| 400/401/404/405/413 | **不重试**，直接失败并写审计 | errors.md 表格 |
| 流中断（首 token 后） | 标记该轮 `partial`，丢弃半截 tool_call，用同一 messages 重新发起一次；工具执行侧用 `call_id` 去重 | streaming.md「treat the text received before it as incomplete」 |
| 降级 | 主模型连续 2 次 5xx/超时 → 切 `qwen3.8-flash-next`，审计日志记 `degraded=true` 与原因 | 自定义策略 |
| 429 排队 | 并发超过 16 时网关会先排队最多 2 分钟，客户端超时需 > 排队时间 | limits.md |

### 3.4 密钥卫生（对应赛事 containment 评分点）

1. key 只从宿主机 env / systemd credential 读取；仓库 `.env.example` 只放空值，`.gitignore` 含 `.env`。
2. 审计日志落库前用正则 `Bearer\s+\S+`、`sk-[A-Za-z0-9]{8,}` 双向脱敏；错误响应体入库前同样脱敏。
3. 沙箱启动参数白名单 env（`docker run --env HOME=/tmp --env TASK_ID=...`），**禁止 `--env-file`**、禁止挂载宿主机 `/etc`、`~/.vultr`。
4. 沙箱 `--network=none`（Challenge 1 的 code-execution 模式不需要外网）；确需联网的场景用 App 侧代理白名单。
5. 前端只拿到 `request_id`，拿不到任何 header 回显。

### 3.5 Token 预算与成本上限

| 层级 | 上限 | 触发动作 |
|---|---|---|
| 单请求 | `max_completion_tokens`=1024（工具轮）/ 2048（终答） | 硬截断，`finish_reason:"length"` 计入告警 |
| 单任务 | 轮次 ≤ 24；累计 input ≤ 200K；累计 output ≤ 8K；估算成本 ≤ $0.05 | 超限立即终止，返回"预算耗尽"而非胡编结果 |
| 单日 | 估算成本 ≤ $5（约 200+ 次 demo） | App 侧熔断，返回 503 并提示 |
| 观测 | 每任务落 `usage` 明细（prompt/completion/reasoning tokens）；`GET /v1/usage` 每日对账 | 与 Console Usage Tab 对账 |

> 预算执行点放在适配层（每次 `chat()` 前后累加），不放在 prompt 里——prompt 约束不可靠。

### 3.6 流式输出到前端

- 后端用 `stream=True` 消费 SSE，按事件类型分流：`content` → 前端正文；`reasoning` → 可选"思考中"折叠区（不进入最终答案）；`tool_calls` 分片按 `index` 聚合，聚合完成后才执行工具。
- 前端用 `text/event-stream` 接收自定义事件：`token` / `tool_start` / `tool_result` / `audit` / `done` / `error`；不以 OpenAI 原始 chunk 直出（避免把 `usage`、`id` 泄露给前端）。
- 流失败时后端补发 `error` 事件 + 已生成文本标记 `partial:true`，前端不得把 partial 当作成功结果。

## 4. 代码骨架（可运行形态，未执行——本机无 key）

```python
# app/inference.py —— 单 Agent Planner/Executor 循环，含超时、重试、预算、流式
import os, json, time, asyncio
from dataclasses import dataclass, field
from typing import Any
from openai import OpenAI, APIStatusError, APITimeoutError, APIConnectionError

BASE_URL = "https://api.vultrinference.com/v1"
MODEL_PRIMARY = os.getenv("MODEL_PRIMARY", "deepseek-v4.1-flash-1017")
MODEL_FAST    = os.getenv("MODEL_FAST", "qwen3.8-flash-next")
MODEL_VISION  = os.getenv("MODEL_VISION", "qwen3.8-27b")

client = OpenAI(base_url=BASE_URL, api_key=os.environ["VULTR_INFERENCE_API_KEY"],
                timeout=90.0, max_retries=0)   # 重试自己实现,便于按状态码分类

RETRYABLE = {429, 502, 503, 504}
TASK_DEADLINE_S, MAX_ROUNDS = 300, 24
MAX_IN_TOKENS, MAX_OUT_TOKENS, MAX_COST_USD = 200_000, 8_000, 0.05
PRICE = {MODEL_PRIMARY: (0.15, 0.60), MODEL_FAST: (0.10, 0.20), MODEL_VISION: (0.15, 1.00)}

@dataclass
class Budget:
    tin: int = 0; tout: int = 0; rounds: int = 0
    def add(self, u) -> None:
        self.tin += u.prompt_tokens; self.tout += u.completion_tokens
    def cost(self, model: str) -> float:
        pin, pout = PRICE.get(model, (0.55, 2.75))   # 未知模型按保守价计
        return self.tin / 1e6 * pin + self.tout / 1e6 * pout
    def exhausted(self, model: str) -> bool:
        return (self.rounds >= MAX_ROUNDS or self.tin >= MAX_IN_TOKENS
                or self.tout >= MAX_OUT_TOKENS or self.cost(model) >= MAX_COST_USD)

def call_model(messages, *, tools=None, model=MODEL_PRIMARY, max_tokens=1024,
               stream=False, deadline=time.time() + TASK_DEADLINE_S) -> Any:
    last = None
    for attempt in range(4):
        if time.time() > deadline:
            raise TimeoutError("task deadline exceeded")
        try:
            return client.chat.completions.create(
                model=model, messages=messages, tools=tools, tool_choice="auto" if tools else None,
                max_completion_tokens=max_tokens, stream=stream,
            )
        except APIStatusError as e:
            last = e
            if e.status_code == 429:
                wait = min(int(e.response.headers.get("Retry-After", "2")), 30)
            elif e.status_code in RETRYABLE:
                wait = 2 ** attempt
            elif e.status_code == 500:
                wait, attempt = 1, max(attempt, 3)      # 只重试一次
            else:
                raise                                         # 400/401/404/405/413 不重试
            time.sleep(wait)
        except (APITimeoutError, APIConnectionError) as e:
            last = e; time.sleep(2 ** attempt)
    raise RuntimeError(f"model call failed after retries: {last}")

async def run_agent(task: str, tools: list[dict], run_tool, emit, model=MODEL_PRIMARY):
    """run_tool(name, args) -> str 在沙箱执行；emit(event, payload) 推 SSE。"""
    budget = Budget()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]
    deadline = time.time() + TASK_DEADLINE_S
    while True:
        budget.rounds += 1
        if budget.exhausted(model):
            await emit("error", {"kind": "budget_exhausted", "usage": budget.__dict__})
            return
        try:
            resp = await asyncio.to_thread(call_model, messages, tools=tools,
                                           model=model, deadline=deadline)
        except Exception as e:
            await emit("error", {"kind": "inference_failed", "detail": str(e)[:300]})
            return
        budget.add(resp.usage)
        msg = resp.choices[0].message
        await emit("usage", {"model": resp.model, "in": budget.tin, "out": budget.tout,
                             "usd": round(budget.cost(model), 4)})
        if not msg.tool_calls:
            await emit("token", {"text": msg.content or ""})
            await emit("done", {"finish_reason": resp.choices[0].finish_reason})
            return
        messages.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            name, args = tc.function.name, _safe_json(tc.function.arguments)
            await emit("tool_start", {"id": tc.id, "name": name, "args": args})
            try:
                result = await asyncio.wait_for(asyncio.to_thread(run_tool, name, args), timeout=60)
            except Exception as e:
                result = json.dumps({"ok": False, "error": str(e)[:400]})
            await emit("tool_result", {"id": tc.id, "ok": '"ok": true' in result})
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
```

**骨架强制项**：① 末条消息永远不是 `assistant`（除续写场景）——否则会被当成 prefill；② 工具失败以结构化 `{"ok":false}` 回灌，让模型自我修复而不是抛栈；③ 每轮 `usage` 都累加并落审计；④ `emit` 与工具执行都不得把 API key 或完整 env 写进事件。

## 5. 成本折算（用 catalog 真实单价，不估算）

### 5.1 单价（live catalog，USD / 1M tokens）

| 模型 | input | output | catalog 原文（每 token） |
|---|---|---|---|
| deepseek-v4.1-flash-1017 | 0.15 | 0.60 | `"prompt":"0.00000015","completion":"0.0000006"` |
| qwen3.8-flash-next | 0.10 | 0.20 | `"0.0000001"` / `"0.0000002"` |
| qwen3.8-27b | 0.15 | 1.00 | `"0.00000015"` / `"0.000001"` |

**计费粒度**：按 token 计费（prompt + completion，含 reasoning tokens）；响应 `usage` 即计费口径；`GET /v1/usage` 可查当月/上月；Console `Usage` Tab 近实时。无最低消费、无按请求固定费的官方表述（未核实是否有最低计费单位）。

### 5.2 一次 5 分钟 demo 任务（20 轮工具调用）的 token 模型

假设（工程估算，非官方数据）：system + 工具 schema 1,200 token/请求；每轮工具结果 400 token；每轮 assistant 输出（tool call JSON）150 token；终答 400 token；上下文累积重发。

- 累计 input = 20×1200 + 550×(0+1+…+19) = 24,000 + 104,500 = **128,500 token**
- output = 20×150 + 400 = **3,400 token**（reasoning 另计，按 2× 兜底 ≈ 6,800）

| 场景 | input 成本 | output 成本 | 单次合计 |
|---|---|---|---|
| deepseek，无 reasoning 溢价 | $0.0193 | $0.0020 | **$0.0213（¥0.15）** |
| deepseek，reasoning 按 2× output | $0.0193 | $0.0041 | **$0.0234** |
| qwen3.8-flash-next 同工作量 | $0.0129 | $0.0007 | **$0.0136** |
| 保守口径（官方 $0.55/$2.75 平铺价） | $0.0707 | $0.0094 | **$0.0801（¥0.57）** |

**结论**：单次 demo $0.02（目录价）～$0.08（保守价）。11-03~11-08 全期按 300 次彩排 + 30 次正式演示估算 = **$6.5～$26**，$200 赛事额度绰绰有余，成本不是风险项。vision 单张截图约 1K–1.6K prompt token（未核实官方换算），几十张也不到 $0.01。

## 6. 风险与未决问题

| # | 风险 / 未决 | 影响 | 缓解 |
|---|---|---|---|
| R1 | **定价口径冲突**：产品页/支持文档 $0.55/$2.75 与 live catalog 逐模型价不一致 | 成本估算偏差 4× | 预算按 $0.55/$2.75 兜底；Day-0 实测 1 次请求对账 |
| R2 | **未知模型 ID 静默回落到 deepseek** | 线上悄悄换模型，评测/审计失真 | 启动白名单校验 + 响应 `model` 回显写审计 |
| R3 | 区域只到模型级（SEA/ATL），无区域 endpoint | 跨洋 RTT 影响 5 分钟 demo | VM 选 SEA 相邻区域；用首 token 延迟实测 |
| R4 | vision 的 per-image token 计费、OCR 质量未核实 | 截图验证任务成本/准确率不确定 | Day-0 用 1 张图跑通并读 `usage` |
| R5 | `stream` 中断后 tool_call 可能重复执行 | 沙箱副作用重复 | 工具侧幂等 + `call_id` 去重 + 中断重发前丢弃半截调用 |
| R6 | 429 排队最长 2 分钟，与 5 分钟 demo 预算冲突 | 演示卡顿 | 主/备模型分池；演示前 60s 预热请求；前端显示排队状态 |
| R7 | `/v1/messages` 无 prompt caching，chat 路由缓存能力未核实 | 长任务成本不可优化 | 不改设计，仅把"无缓存"写进成本假设 |
| R8 | 赛事要求"所有 Agent LLM 调用走 Vultr"，含嵌入/判定调用 | 违规风险 | 全部 LLM 调用（含摘要、分类、vision）统一走本适配层，不出现 OpenAI/Anthropic 端点 |

**必须在 11-03 kickoff 确认**：① 计费到底按 catalog 还是平铺价（现场问 Vultr 讲师/看 Billing 明细）；② 订阅是否需要选区域、能否指定推理区；③ $200 credit 是否覆盖 Inference（FAQ 只说"Vultr services"，需确认）；④ 免费额度是否有并发上限；⑤ 模型可用性是否在整个赛事期稳定（预览模型可能下线）。

## 7. Day-0 验证清单（拿到 key 后 10 分钟内跑完）

| # | 目的 | 命令 | 通过标准 |
|---|---|---|---|
| 1 | 端点可达 + 模型清单（无需 key） | `curl -s https://api.vultrinference.com/v1/models?format=openrouter&output_modalities=text \| jq '.total_count, .data[].id'` | 返回 13 个 ID，含三个选定 ID |
| 2 | 鉴权 + 最小 chat | `curl -s https://api.vultrinference.com/v1/chat/completions -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d '{"model":"deepseek-v4.1-flash-1017","messages":[{"role":"user","content":"say ok"}],"max_completion_tokens":16}'` | 200，`choices[0].message.content` 非空，`usage` 有值 |
| 3 | 回显模型不被替换 | 同上，检查 `model` 字段 | 等于 `deepseek-v4.1-flash-1017` |
| 4 | tool calling | 带一个 `get_time` 工具，`tool_choice:"auto"` | `finish_reason=="tool_calls"`，`tool_calls[0].function.name=="get_time"` |
| 5 | 流式 + usage | `"stream":true`，`curl -N` | 收到多个 `data:`，末 chunk 有 `usage`，以 `[DONE]` 结束 |
| 6 | JSON mode | `"response_format":{"type":"json_object"}` | 内容可 `jq` 解析 |
| 7 | vision | `content:[{"type":"text",...},{"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}]` | 正确描述图中文字 |
| 8 | 错误码 | 用错 key / 错模型名 | 401 有 `auth_error`；错模型名**不报错而是换模型**（验证 R2） |
| 9 | 用量对账 | `curl -s https://api.vultrinference.com/v1/usage -H "Authorization: Bearer $KEY"` | 与步骤 2/4 的 token 累计一致 |
| 10 | 延迟基线 | 步骤 2 计时（`time curl`） | 首字节 < 3s（SEA VM），记录 P50 供 demo 预算 |

## 8. 与官方文档的冲突（必须显式标注）

1. **价格冲突（重要）**：`https://www.vultr.com/products/cloud-inference/` 与 `https://docs.vultr.com/support/products/serverless/how-do-i-monitor-the-usage-and-cost-of-my-vultr-serverless-inference-subscription`（Updated 26 October 2026）写明 "Requests are billed at **$0.55 per 1,000,000 input tokens and $2.75 per 1,000,000 output tokens**"；而 `api.vultrinference.com/docs/models` 与 live catalog 给出逐模型价（deepseek $0.15/$0.60）。两者**同时在线且未互相说明**，本预研以 catalog 为计算基准、以平铺价为预算上限。
2. **模型目录两个形状**：`/docs/models.md` 是"每个模型一行"的人读版（含 `glm-5.2`、`glm-5.3` 限流 30 rpm/8 并发）；`/v1/models` 是完整 JSON（含 `rate_limits`、`supported_parameters`、`max_completion_tokens`）。**以 `/v1/models` 为准**，人读页仅作索引。
3. **默认输出长度**：limits.md 说默认 32,768 token；模型页/catalog 写 `max_completion_tokens` 可到 1M。二者不冲突（默认 vs 上限），但写代码时必须显式传上限，否则长任务会被 32K 截断且只体现为 `finish_reason:"length"`。
