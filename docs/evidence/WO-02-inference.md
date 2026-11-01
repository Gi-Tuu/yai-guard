# WO-02 · 推理接入 + Planner + tool-calling 收敛

> 日期：2026-10-31 ｜ 状态：**代码与离线测试完成；真实 key 验证待 Day-0（11-04）**
> 代码：[`backend/app/agent/model_adapter.py`](../../backend/app/agent/model_adapter.py)、[`backend/app/agent/prompts.py`](../../backend/app/agent/prompts.py)
> 测试：[`backend/tests/test_model_adapter.py`](../../backend/tests/test_model_adapter.py)（12 项，全部离线）

## 1. 本轮变更

| 变更 | 原因 |
|---|---|
| 本地模型白名单 `MODEL_ALLOWLIST = {deepseek-v4.1-flash-1017, qwen3.8-flash-next}` | 官方文档明确「模型 ID 写错不报错，静默回落到 deepseek」；在本地先拦一次 |
| `assert_model()` 双重校验（白名单 + `/v1/models` 目录） | 启动即失败，避免演示时才发现模型不对 |
| 响应 `model` 回显必须等于请求值 | 网关静默换模型 → `ModelFallbackError`，任务失败而不是降级 |
| 429 按 `Retry-After` 等待；500/502/503/504 与超时重试 | 并发超额会排队；不能把限流当失败 |
| `ModelError` 消息脱敏（`api_key` 替换为 `***`） | key 不进日志、trace、异常栈 |
| `total_usage` 累计 prompt/completion/total tokens | 任务结束可对账 |
| 工具轮 `max_completion_tokens=1024`，终答 2048 | 决策要短、总结要完整；上限受 32768 约束 |
| `MODEL_PRIMARY` 环境变量生效 | 部署时改模型不用改代码 |
| Planner 提示词：先 `list_dir`、小步执行、被拒后换方案、finish 必须引用真实 stdout | 真实模型容易在 deny 后重复撞墙；把「rule 是权威」写进 system prompt |

`complete(messages, tools=...)` 签名保持；`ModelResponse` 字段名未变（`content/tool_calls/finish_reason/usage/model`）。新增 `total_usage` 属性与 `max_completion_tokens` 可选参数（向后兼容）。

## 2. 离线验收（已执行）

```text
$ python -m unittest discover -s backend/tests -t backend -p "test_model_adapter.py" -v
test_429_is_retried_and_retry_after_is_honoured ... ok
test_500_is_retryable_but_400_is_not ... ok
test_allowlist_matches_documented_catalog ... ok
test_assert_model_rejects_catalog_miss ... ok
test_assert_model_rejects_unknown_id ... ok
test_error_message_does_not_leak_api_key ... ok
test_invalid_tool_arguments_are_preserved ... ok
test_response_is_parsed ... ok
test_silent_model_fallback_raises ... ok
test_token_budget_depends_on_tool_round ... ok
test_unlisted_model_is_rejected_before_request ... ok
test_usage_accumulates_across_calls ... ok
Ran 12 tests in 0.001s
OK
```

全部用 fake client，不联网、不需要 key。

## 3. Day-0 真实 key 清单（未执行，11-04 执行）

| # | 检查 | 命令/判定 | 状态 |
|---|---|---|---|
| 1 | key 生效、目录可见 | `assert_model()` 通过 | ⏳ 待 Day-0 |
| 2 | 模型回显 | 返回 `model == deepseek-v4.1-flash-1017` | ⏳ |
| 3 | tool calling | 演示任务触发 `run_command`/`write_file` | ⏳ |
| 4 | 收敛步数 | 演示任务 ≤12 步；被 deny 后不重复同一命令 | ⏳ |
| 5 | finish 质量 | summary 含真实 stdout（`2`），不是猜测 | ⏳ |
| 6 | usage 对账 | `total_usage` 与网关账单一致（±5%） | ⏳ |
| 7 | 首 token 延迟 | p50 记录进 README/视频素材 | ⏳ |
| 8 | key 不落盘 | `grep -r "VULTR_INFERENCE_API_KEY" artifacts/` 无明文 | ⏳ |

Day-0 冒烟命令：

```bash
export VULTR_INFERENCE_API_KEY=...
cd backend && python - <<'PY'
from app.agent.model_adapter import VultrModel
m = VultrModel()
m.assert_model()
r = m.complete([{"role": "user", "content": "say ok"}])
print(r.model, r.content[:80], r.usage, m.total_usage)
PY
```

## 4. 已知限制

- 未接除 Vultr Inference 以外的 provider（赛事强制；代码没有 fallback 分支）。
- 真实模型的 Planner 收敛尚未验证；若 12 步内跑不完，优先改提示词/工具 schema，不靠放宽策略。
- `MODEL_PRIMARY` 若填了目录外 ID，会在启动时直接报错——这是预期行为。
