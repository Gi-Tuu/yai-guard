# 02 · Vultr Serverless Inference 证据（逐条 URL + 访问时间 + 原文摘录）

> 采集时间：**2026-10-31 23:16–23:55（UTC+8）**
> 采集方式：`read_page`（Firecrawl 渲染，个别页回退本地抓取）/ `web_search` 摘要。
> **没有**创建账号、订阅、API key，**没有**发起任何计费请求；`/v1/models` 属官方公开只读端点（文档明示无需 key）。
> 状态定义：**已核实**＝直接读到官方原文；**未核实**＝官方无表述或本次未读到；**假设**＝工程推断，需 Day-0 验证。

## 0. 采集限制（必须披露，影响证据强度）

1. Firecrawl 匿名配额在采集期间多次 403（"your IP address looks like a VPN or proxy"），部分页面（`/docs/models/deepseek-...`、`/v1/models` 首次尝试、`/docs/models.json` 首次尝试）**首轮失败、重试后成功**；因此同一 URL 出现过多次访问时间，下文只记成功那次。
2. `https://www.vultr.com/pricing/` 单页渲染超过返回上限（截断于 Optimized Cloud Compute，约 50,000 字符），**Serverless Inference 价格段未能直读到"模型名 + 价格"配对**；该页只确认了"存在 Serverless Inference 价格段"与若干价格数字（见 §7）。
3. 本机直接 `web_fetch` 到 `api.vultrinference.com` 失败（`fetch failed`），只用渲染桥（Firecrawl）与本地回退，无 HTML 级校验。
4. 无 API key，**所有"行为类"结论（实际 tool calling 往返、实际计费、实际延迟）均未实测**，标记为未核实/假设。

## 1. 官方 API 入口与认证

**URL**：https://api.vultrinference.com/docs/get-started/quickstart.md
**访问**：2026-10-31 23:22 (+08:00)
**状态**：已核实

| # | 原文摘录 |
|---|---|
| 1.1 | "Every request goes to `https://api.vultrinference.com/v1` with the key as a bearer token." |
| 1.2 | "Create an API key in the [Vultr console](https://console.vultr.com/inference/) and keep it in an environment variable, so it never lands in your code" |
| 1.3 | "`model` takes any id from the model list. Leave it out on `/v1/chat/completions` or `/v1/responses` and the request goes to `deepseek-v4.1-flash-1017`" |
| 1.4 | ⚠️ "A model id that does not match any model is not an error on the chat routes: the request goes to `deepseek-v4.1-flash-1017`, and the answer's `model` says so. Check it if you typed the id by hand." |
| 1.5 | "The list is public, so it needs no key."（指 `GET /v1/models`） |
| 1.6 | "`curl https://api.vultrinference.com/v1/models`  # Every model, with its prices, limits and the parameters it reads (no API key needed)" |

**URL**：https://api.vultrinference.com/docs/get-started/authentication.md
**访问**：2026-10-31 23:24 (+08:00)
**状态**：已核实

| # | 原文摘录 |
|---|---|
| 1.7 | "`Authorization: Bearer <your API key>` … `Bearer` takes a capital B." |
| 1.8 | "`x-api-key: <your API key>` … the API reads it when there is no Authorization header"；"Both work on every route; if a request has both, `Authorization` wins." |
| 1.9 | 401 响应原文：`{"error":{"code":401,"message":"Invalid API key","type":"auth_error","metadata":{"error_type":"authentication"}}}`；"A 401 is never worth retrying with the same key." |
| 1.10 | "Routes that need no key: `GET /v1/models`…`GET /v1/embeddings/models`…"；"Every other route needs a key, including `GET /v1/health`." |
| 1.11 | "Keep the key on your servers. A key in a web page or a mobile app can be read by anyone who has it, and spends from your account." |
| 1.12 | "Your usage, files, vector stores, rate limits and webhook secret belong to your subscription: every key of it sees the same ones." |

**URL**：https://api.vultrinference.com/docs/get-started/sdks.md
**访问**：2026-10-31 23:25 (+08:00)
**状态**：已核实

| # | 原文摘录 |
|---|---|
| 1.13 | SDK 表：OpenAI → base URL `https://api.vultrinference.com/v1`；Anthropic → `https://api.vultrinference.com`（SDK 自己加 `/v1`）；OpenRouter → `/v1` |
| 1.14 | "There is no SDK of our own to install." |
| 1.15 | 路由表：`POST /v1/chat/completions` = "OpenRouter's chat completion, which is OpenAI's with more fields"；`POST /v1/responses` = OpenAI Responses；`POST /v1/messages` = Anthropic Messages |
| 1.16 | "The thinking is always `reasoning`, never `reasoning_content`." |
| 1.17 | "`finish_reason` is one of `stop`, `length`, `tool_calls`, `content_filter` or `error`." |
| 1.18 | "`usage` always has both detail objects"（`prompt_tokens_details.cached_tokens`、`completion_tokens_details.reasoning_tokens`） |
| 1.19 | 差异表（Messages）："There is no prompt caching and no token counting route." |
| 1.20 | 差异表（Chat）："No system prompt is added, and a final assistant message is continued rather than answered." |

## 2. 是否必须先 provisioning 订阅 / API key 获取路径

**URL**：https://docs.vultr.com/products/compute/serverless-inference/provisioning
**访问**：2026-10-31 23:21 (+08:00)
**状态**：已核实

> "1. Navigate to **Products**, click **Serverless**, and then click **Inference**.
> 2. Click **Add Serverless Inference**.
> 3. Provide a **Label**, acknowledge the list of supported models and the charges note, and click **Add Serverless Inference**."

**URL**：https://docs.vultr.com/products/compute/serverless-inference/management/connection
**访问**：2026-10-31 23:33 (+08:00)
**状态**：已核实

> "To utilize the Vultr Serverless Inference subscription, users must obtain an API key."
> "1. Navigate to Products, click Serverless, and then click Inference. 2. Click your target inference subscription to open its management page. 3. In the overview page, copy the **API Key**. API key is necessary to send any request to the inference endpoints."

**URL**：https://docs.vultr.com/products/compute/serverless-inference/management
**访问**：2026-10-31 23:30 (+08:00)
**状态**：已核实（目录级）

> 子页包含 Connection / Update / Health Checks / Monitor / Delete，均标注 "23 October 2026"；Usage 分类："Send requests to Vultr Serverless Inference models through the chat, prompt, and text-to-speech endpoints."

**推论（假设）**：订阅是 key 的宿主，理论上"先 provisioning 再取 key"；**未实测**未订阅时 console 是否隐藏 API Key 字段。

**URL**：https://docs.vultr.com/products/compute/serverless-inference/faq
**访问**：2026-10-31 23:36 (+08:00)
**状态**：已核实（正文为 FAQ 骨架，答案段未渲染出全文）

> 标题："Frequently Asked Questions (FAQs) About Vultr Serverless Inference"，"2 min read Updated 23 October 2026"。
> "To view the current supported models, navigate to the Serverless Inference section in the Vultr Console and check the model selector in the Prompt tab."

## 3. 区域 / endpoint 差异

**URL**：各模型 .md 页（见 §4）
**访问**：2026-10-31 23:26–23:47
**状态**：已核实（模型级区域）；未核实（是否有区域 endpoint）

> `deepseek-v4.1-flash-1017`："Regions: **SEA**"
> `qwen3.8-flash-next`："Regions: **SEA**"
> `qwen3.8-27b`："Regions: **SEA**"
> `mimo-v2.6-flash`："Regions: **ATL**"

**未核实**：官方文档（quickstart/sdks/limits/errors/streaming）中**没有任何** `region` 参数、分区域 base URL 或"跨区路由"表述；也没有"订阅需选区域"的说明。产品页只写 "reliable performance across six continents"。

**URL**：https://www.vultr.com/products/cloud-inference/
**访问**：2026-10-31 23:29 (+08:00)
**状态**：已核实

> "Vultr Serverless Inference is designed to effortlessly scale your Generative AI applications across six continents"；"Inference at the edge"。无区域 endpoint 细节。

## 4. Live model catalog（真实模型 ID + 价格 + 能力）

**URL**：https://api.vultrinference.com/v1/models?format=openrouter&output_modalities=text
**访问**：2026-10-31 23:40 (+08:00)
**状态**：已核实（公开、无 key 可读；返回 `total_count: 13`）

字段结构（原文键名）：`id`、`canonical_slug`、`hugging_face_id`、`name`、`created`、`description`、`context_length`、`architecture.input_modalities`、`architecture.output_modalities`、`pricing.prompt`（USD/token 字符串）、`pricing.completion`、`top_provider.context_length`、`top_provider.max_completion_tokens`、`supported_parameters`、`reasoning.supported_efforts`、`rate_limits.text.requests_per_minute`、`rate_limits.text.concurrent_requests`、`input_limits.image.max_bytes`。

| # | 模型 ID | 原文关键字段 |
|---|---|---|
| 4.1 | `deepseek-v4.1-flash-1017` | `"context_length":1048576`、`"pricing":{"prompt":"0.00000015","completion":"0.0000006"}`、`"max_completion_tokens":1048576`、`supported_parameters` 含 `tools`/`tool_choice`/`response_format`/`structured_outputs`/`reasoning`、`"rate_limits":{"text":{"requests_per_minute":120,"concurrent_requests":16}}`、`input_modalities=["text","image","file"]` |
| 4.2 | `qwen3.8-flash-next` | `"context_length":262144`、`"prompt":"0.0000001"`、`"completion":"0.0000002"`、`max_completion_tokens 262144`、`input_modalities=["text","image","file","video"]`、tools ✅、120 rpm / 16 并发 |
| 4.3 | `qwen3.8-27b` | `"context_length":262144`、`"prompt":"0.00000015"`、`"completion":"0.000001"`、`input_modalities=["text","image","file","video"]`、tools ✅、120 rpm / 16 并发；模型描述 "Compact dense **vision-language** model" |
| 4.4 | `mimo-v2.6-flash` | `"context_length":1048576`、`0.0000001` / `0.00000025`、input 含 `audio`/`video`、tools ✅、区域 ATL |
| 4.5 | `glm-5.3-flash` | 1,048,576、`0.0000001` / `0.00000035`、input 含 `video`、tools ✅ |
| 4.6 | `glm-5.3` / `glm-5.2` | 1,048,576、`0.00000075` / `0.000003`、`rate_limits 30 rpm / 8 并发` |
| 4.7 | `minimax-m3.1-flash` | 1,048,576、`0.0000002` / `0.0000009`、tools ✅ |
| 4.8 | `nemotron-3-nano-omni-30b-a3b-reasoning` | 262,144、`0.0000001` / `0.00000025`、input 含 audio/video |
| 4.9 | `raon-1-mini-preview` | 262,144、`0.0000001` / `0.0000003`、tools ✅ |
| 4.10 | `nemotron-3.5-content-safety` | 131,072、`0.00000005` / `0.00000015`、**无 tools** |
| 4.11 | `muse-glimmer-30b` | 131,072、`0.00000025` / `0.000001`、tools ✅ |

**URL**：https://api.vultrinference.com/docs/models.json
**访问**：2026-10-31 23:38 (+08:00)
**状态**：已核实

> 人读清单摘要：`deepseek-v4.1-flash-1017`（$0.15 in, $0.60 out / 1M）；`glm-5.3-flash`（$0.10/$0.35）；`mimo-v2.6-flash`（$0.10/$0.25）；`qwen3.8-flash-next`（$0.10/$0.20）；`qwen3.8-27b`（$0.15/$1.00）；`glm-5.2/5.3`（$0.75/$3.00, "limits 30 a minute, 8 at once"）。`models.json` 每条含 `"price"`、`"context"`、`"own_limits"` 字段。

**URL**：https://api.vultrinference.com/docs/models.md
**访问**：2026-10-31 23:26 (+08:00)
**状态**：已核实
分类：Chat and reasoning / Images / Video / Music / Speech / Transcription / Alignment / Embeddings / Rerank / Decisions；chat 分类共 13 个模型，与 `/v1/models` 的 `total_count` 一致。

**URL**：https://api.vultrinference.com/docs/models/deepseek-v4.1-flash-1017.md （及同名 qwen3.8-flash-next / qwen3.8-27b / mimo-v2.6-flash 页）
**访问**：2026-10-31 23:44–23:47 (+08:00)
**状态**：已核实

> `deepseek-v4.1-flash-1017`："Mixture-of-experts model with a 1M-token context, reasoning, and tool calling." / "Context: 1M tokens" / "Max output: 1M tokens" / "Input: $0.15 / 1M tokens, Output: $0.60 / 1M tokens" / "Regions: SEA"
> `qwen3.8-flash-next`："Fast multimodal model with a 262K-token context, reasoning, and tool calling." / 输入含 "image: sources url, base64; formats png, jpeg, webp, gif; max size 20 MB" / 参数表含 `"tools" | boolean`
> `qwen3.8-27b`："Compact dense vision-language model with flexible thinking and strong coding and agentic skills." / "Max output: 256K tokens"
> 三页均有 `## Endpoints`：`POST /v1/chat/completions`、`POST /v1/responses`、`POST /v1/messages`、`POST /v1/chat/completions/RAG`

## 5. tool calling / streaming / 错误与限流

**URL**：https://api.vultrinference.com/docs/reference/create-chat-completion.md
**访问**：2026-10-31 23:26 (+08:00)
**状态**：已核实

| # | 原文摘录 |
|---|---|
| 5.1 | `tools`："A list of function tools the model may call." — `type`: "Currently, only `function` is supported."；`function.name` "Must be a-z, A-Z, 0-9, or contain underscores and dashes, with a maximum length of 64"；`parameters` "described as a JSON Schema object"；`strict` "Only a subset of JSON Schema is supported when `strict` is `true`." |
| 5.2 | `tool_choice`："Controls which (if any) tool is called by the model. A tool choice other than `none` requires `tools`." |
| 5.3 | `messages.role` 枚举："[system, assistant, user, tool, developer]" |
| 5.4 | `max_tokens`："Deprecated name for `max_completion_tokens`. If both are sent, `max_completion_tokens` takes precedence." |
| 5.5 | `max_completion_tokens`："Maximum completion tokens, including reasoning and visible output… [default 32768, at least 1]" |
| 5.6 | `response_format`："`json_object` makes the model answer with a JSON object; `json_schema` makes it answer with JSON that matches the schema. `text` is the default." |
| 5.7 | "**Continuation:** If the final message has the `assistant` role, the model continues that message instead of starting a new turn" |
| 5.8 | 错误响应码表：400（含 `context_length_exceeded`）、401、429、502 "The model could not be reached"、504 "The model timed out"、404 "No model by that name exists"、503 |
| 5.9 | 响应形状："tool call ids with `call_`… ids start with `chatcmpl-`"；"`finish_reason` is one of `stop`, `length`, `tool_calls`, `content_filter` or `error`" |

**URL**：https://api.vultrinference.com/docs/concepts/streaming.md
**访问**：2026-10-31 23:25 (+08:00)
**状态**：已核实

> "Send `"stream": true` and the answer arrives as server-sent events…"
> "A tool call arrives in pieces of `tool_calls`: its id and name first, then its arguments, matched by `index`."
> "The last chunk always carries `usage`, whether you asked for it or not."
> 失败流原文：chat completions 最后发 "A chunk with `error` (the usual envelope) and `finish_reason: "error"`, then `data: [DONE]`"。
> "Treat the text received before it as incomplete."

**URL**：https://api.vultrinference.com/docs/concepts/limits.md
**访问**：2026-10-31 23:24 (+08:00)
**状态**：已核实

| # | 原文摘录 |
|---|---|
| 5.10 | "Chat and reasoning (`/v1/chat/completions`, `/v1/responses`, `/v1/messages`)：**120 requests a minute / 16 at once**"（多数模型默认；个别模型自带更低限流，如 glm-5.2/5.3 为 30/8） |
| 5.11 | "Output：**32,768 tokens by default**, up to the context window with your own cap"；"Time：About **nine minutes** per request" |
| 5.12 | 429 响应：`{"error":{"code":429,"message":"Rate limit exceeded. Please retry later.","type":"rate_limit_error",…}}`，带 `Retry-After` |
| 5.13 | 并发排队："A request beyond the number at once waits its turn, first come first served, for **up to two minutes**. One that waits longer gets a 429 with `Retry-After: 5`" |
| 5.14 | 重试建议："Retry a 429 after `Retry-After`, and a 502, 503 or 504 after a growing wait." |
| 5.15 | 超窗行为："If the prompt and `max_completion_tokens` would not fit, the answer's budget is cut to what does… `finish_reason: "length"`"；prompt 单独超窗 → 400 `context_length_exceeded` |
| 5.16 | 请求体上限：embeddings/rerank 16 MB、multipart 100 MB、`/v1/files` 100 MB（单订阅 1,000 文件）、图片 20 MB、音视频 50 MB、PDF 300 页 |

**URL**：https://api.vultrinference.com/docs/concepts/errors.md
**访问**：2026-10-31 23:31 (+08:00)
**状态**：已核实

> 状态码-是否重试表：400 No / 401 No / 404 No / 405 No / 409 "Read the job again first" / 413 No / 429 "Yes, after `Retry-After`" / 500 "Once, after a pause" / 502/503/504 "Yes, with a growing wait"。
> 重试细节："**502, 503, 504**: start at a second or two and double the wait each time."；"**500**: once. If it fails again, the request is likely to keep failing."
> 错误体形状：OpenRouter 信封 `{"error":{"code","message","type","metadata":{"error_type"}}}`；`/v1/messages` 用 Anthropic 形状。

**URL**：https://api.vultrinference.com/docs/concepts/documents.md
**访问**：2026-10-31 23:37 (+08:00)
**状态**：已核实

> 图片部件：`image_url`，格式 "PNG, JPEG, WebP or GIF"；"A model that does not [read it] gets a description of it, written by a model that does, so it can still answer about it."
> 文档："Any chat model can read a document: the API turns it into text before the model sees it"；PDF ≤300 页、≤100 MB；"The text counts toward the context window, and is billed as prompt tokens".
> 媒体 URL："A media URL is downloaded by the API, not by the model"；"must be `http` or `https` on a public address"。

**URL**：https://api.vultrinference.com/docs/reference/get-usage.md
**访问**：2026-10-31 23:53 (+08:00)
**状态**：已核实

> "`GET https://api.vultrinference.com/v1/usage` — View usage information for the current and previous months."（需 key，401 定义同前）

## 6. 定价：官方三处口径（存在冲突）

**URL**：https://www.vultr.com/products/cloud-inference/
**访问**：2026-10-31 23:29 (+08:00)
**状态**：已核实

> "**$0.55/million** input tokens / **$2.75/million** output tokens"；"Media inference may incur additional charges based on usage."

**URL**：https://docs.vultr.com/support/products/serverless/how-do-i-monitor-the-usage-and-cost-of-my-vultr-serverless-inference-subscription
**访问**：2026-10-31 23:32 (+08:00)
**状态**：已核实（页面标注 "Updated 26 October 2026"）

> "Serverless Inference pricing is based on token consumption. Requests are billed at **$0.55 per 1,000,000 input tokens and $2.75 per 1,000,000 output tokens**. The Usage tab reflects the total tokens consumed…"

**URL**：https://www.vultr.com/pricing/#inference
**访问**：2026-10-31 23:28 (+08:00)
**状态**：部分核实（**渲染内容在 Cloud Compute 段被截断，Serverless Inference 段未直读**）

> 只确认存在 "## Serverless Inference / ### Chat / Prompt Models / Price per Million Input Tokens / Price per Million Output Tokens" 结构，抓到的价格数字包括 `$0.30/1.20`、`$0.55/1.65`、`$0.30/1.00`、`$0.01/0.01`、`$0.13/0.38`、`$0.10/0.30`、`$0.15/0.60`、`$0.85/3.10`（每 1M token）。**模型名与该列表的配对未核实**（渲染把表格压平）。

**URL**：https://api.vultrinference.com/docs/models.md + live catalog
**访问**：2026-10-31 23:26 / 23:40 (+08:00)
**状态**：已核实（逐模型价）

> deepseek $0.15/$0.60；qwen3.8-flash-next $0.10/$0.20；qwen3.8-27b $0.15/$1.00；mimo $0.10/$0.25；glm-5.3 $0.75/$3.00。

**冲突判定**：官方**产品页与支持文档**（平铺 $0.55/$2.75）与**API 文档/模型目录**（逐模型价）**同时在线、互不引用**。本预研以 catalog 为计算基准、以平铺价为预算上限。**必须在 Day-0 用 Console → Usage 实测对账。**

## 7. 赛事页（Challenge 1 强制项）

**URL**：https://lablab.ai/ai-hackathons/vultr-hackathon
**访问**：2026-10-31 23:35 (+08:00)
**状态**：已核实

| # | 原文摘录 |
|---|---|
| 7.1 | "**Vultr Serverless Inference** — OpenAI-compatible inference at `https://api.vultrinference.com/v1`. **Required for Challenge 1 agent LLM calls** and optional for Challenge 2." |
| 7.2 | "**Important:** Vultr GPUs are not available for this event. Challenge 1 must use Vultr Serverless Inference for agent LLM calls" |
| 7.3 | "**Containment-first is required.** An agent that only chats is a demo; an agent that executes safely is a product." |
| 7.4 | Containment focus：Process isolation / Secret hygiene（"no API keys or credentials inside the sandbox"）/ Resource limits / Lifecycle discipline（"reset or destroy the environment after each task"） |
| 7.5 | Submission expectations：GitHub repository with setup and documentation / Vultr VM backend deployment / Public demo URL / Recorded demo video / Clear architecture and use-case explanation / "One containment moment shown in the video" |
| 7.6 | 在线期：**3 – 8 November, 2026**；Kick-off **Nov 3 11:00 AM EST**；End of Submissions **Nov 8 6:00 PM EST** |
| 7.7 | Credits："Every participant gets **$200 in free Vultr credits**"；"Use a valid email address that has **not previously been associated with a Vultr account**"；gift code 在 kickoff 公布 |
| 7.8 | 评审维度：Application of Technology / Presentation / Business Value / Originality |
| 7.9 | 资源链接：`docs.vultr.com/products/serverless/inference/provisioning`、`api.vultrinference.com/v1/models`（与本次核实一致） |

**与官方 API 文档的差异（需标出）**：赛事页把 Inference 文档链到 `docs.vultr.com/products/serverless/inference/provisioning`（较短路径），该路径可正常打开且内容与 `.../compute/serverless-inference/provisioning` 一致；两者不是冲突，只是别名。

## 8. 未能核实项清单（=> Day-0 必须实测）

| # | 未核实内容 | 原因 | 验证方式 |
|---|---|---|---|
| 8.1 | 实际计费按 catalog 逐模型价还是 $0.55/$2.75 平铺价 | 官方两套口径并存 | Console → Usage 发 1 次已知 token 的请求对账 |
| 8.2 | 图像 token 的计费换算 | 文档只写"更大图更贵"，无公式 | vision 请求后读 `usage.prompt_tokens` |
| 8.3 | tool calling 实际往返质量（是否稳定给 JSON、能否并行多工具） | 无 key 未调用 | Day-0 清单第 4 步 |
| 8.4 | 429 排队在真实高并发下的表现 | 未压测 | 并发 20 请求打同一模型 |
| 8.5 | 订阅是否需选区域、能否指定推理区 | 官方无表述 | provisioning 页面实际选项 + 问 kickoff 讲师 |
| 8.6 | $200 credit 是否覆盖 Serverless Inference | FAQ 仅写 "Vultr services" | kickoff Q&A / Billing 页 |
| 8.7 | 是否有最低计费单位或最低消费 | 官方无表述 | Billing 明细 |
| 8.8 | `deepseek-v4.1-flash-1017` 在赛事期是否持续可用（版本号带日期） | 预览/版本化模型可能轮换 | 每日 `GET /v1/models` 探活 |

## 9. 本次未做的事（合规声明）

- 未注册 Vultr 账号、未创建订阅、未创建/使用任何 API key。
- 未调用任何计费端点（chat/completions、embeddings、images 等），仅读取公开文档与公开的 `/v1/models` 目录。
- 未部署任何资源；工作目录内除本任务两个交付文件外未修改其他文件。
