# Vultr Serverless Inference 适配层（唯一 provider）。
#
# 设计要点（见 02 文档）：
# - base_url 写死，不提供任何外部 provider 分支
# - 启动时校验模型 ID在目录内：官方文档明确「模型 ID 打错不报错，会静默回落到
#   deepseek-v4.1-flash-1017」，必须在客户端 fail-fast
# - 响应里的 model 必须等于请求的 model，否则抛 ModelFallbackError
# - 超时/重试只在这里做，上层 Agent 循环不感知 HTTP
# - usage 在这里累计，供任务结束对账（不写进 trace 的原始 key）
from __future__ import annotations

import json
import os
import time
from typing import Any

from ..policy import ToolCall
from .mock_model import ModelResponse

BASE_URL = "https://api.vultrinference.com/v1"
DEFAULT_MODEL = "deepseek-v4.1-flash-1017"
FAST_MODEL = "qwen3.8-flash-next"
MODEL_ALLOWLIST = {DEFAULT_MODEL, FAST_MODEL}

# 工具轮只需要短决策；终答才需要完整总结。上限受模型 32768 约束。
TOOL_ROUND_TOKENS = 1024
FINAL_TOKENS = 2048


class ModelError(RuntimeError):
    """推理失败（已脱敏；不携带 key 或原始请求体）。"""


class ModelFallbackError(ModelError):
    """网关静默换了模型。必须让任务失败，而不是悄悄降级。"""


class VultrModel:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str | None = None,
        base_url: str = BASE_URL,
        timeout_s: float = 90.0,
        max_retries: int = 3,
        client: Any = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("VULTR_INFERENCE_API_KEY", "")
        self.model = model or os.environ.get("MODEL_PRIMARY", DEFAULT_MODEL)
        self.base_url = base_url
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self._allowed_models: set[str] | None = None
        self._client = client
        self.total_usage: dict[str, int] = {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
        }

    # ------------------------------------------------------------------ setup
    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from openai import OpenAI  # 延迟导入：离线测试不依赖 openai
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("openai package is required for VultrModel") from exc
            self._client = OpenAI(base_url=self.base_url, api_key=self.api_key,
                                  timeout=self.timeout_s, max_retries=0)
        return self._client

    def available_models(self) -> set[str]:
        if self._allowed_models is None:
            client = self._get_client()
            self._allowed_models = {m.id for m in client.models.list()}
        return self._allowed_models

    def assert_model(self) -> None:
        """启动时调用：模型不在目录里就 fail-fast。"""
        if self.model not in MODEL_ALLOWLIST:
            raise ValueError(
                f"model {self.model!r} is not in the local allowlist {sorted(MODEL_ALLOWLIST)}"
            )
        if self.model not in self.available_models():
            raise ValueError(
                f"model {self.model!r} is not in the Vultr Inference catalog; "
                "check GET /v1/models before starting"
            )

    # ---------------------------------------------------------------- complete
    def complete(self, messages: list[dict[str, Any]], *, tools: Any = None,
                 model: str | None = None, max_completion_tokens: int | None = None) -> ModelResponse:
        client = self._get_client()
        target = model or self.model
        if target not in MODEL_ALLOWLIST:
            raise ValueError(f"model {target!r} is not allowed; only Vultr catalog models are permitted")
        budget = max_completion_tokens or (TOOL_ROUND_TOKENS if tools else FINAL_TOKENS)
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                resp = client.chat.completions.create(
                    model=target,
                    messages=messages,
                    tools=tools or None,
                    tool_choice="auto" if tools else None,
                    max_completion_tokens=budget,
                )
            except ModelFallbackError:
                raise
            except Exception as exc:  # noqa: BLE001 - 按状态码分类重试
                last_error = exc
                if not self._retryable(exc) or attempt >= self.max_retries:
                    raise ModelError(self._safe(str(exc))) from None
                time.sleep(self._backoff(exc, attempt))
                continue

            returned = getattr(resp, "model", target)
            if returned != target:
                raise ModelFallbackError(
                    f"requested {target!r} but gateway returned {returned!r}"
                )
            response = self._to_response(resp, target)
            self._accumulate(response.usage)
            return response

        raise ModelError(self._safe(f"inference failed after retries: {last_error}"))

    # ---------------------------------------------------------------- helpers
    def _safe(self, text: str) -> str:
        if self.api_key:
            text = text.replace(self.api_key, "***")
        return text

    def _accumulate(self, usage: dict[str, int]) -> None:
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            self.total_usage[key] = self.total_usage.get(key, 0) + int(usage.get(key, 0) or 0)

    @staticmethod
    def _retryable(exc: Exception) -> bool:
        status = getattr(exc, "status_code", None)
        if status is None:
            status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (429, 500, 502, 503, 504):
            return True
        return exc.__class__.__name__ in {"APITimeoutError", "APIConnectionError"}

    @staticmethod
    def _backoff(exc: Exception, attempt: int) -> float:
        status = getattr(exc, "status_code", None)
        if status is None:
            status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 429:
            response = getattr(exc, "response", None)
            retry_after = None
            if response is not None:
                retry_after = response.headers.get("Retry-After")
            try:
                return min(float(retry_after), 30.0)
            except (TypeError, ValueError):
                return min(2.0 ** attempt, 30.0)
        return min(2.0 ** attempt, 10.0)

    @staticmethod
    def _to_response(resp: Any, requested_model: str) -> ModelResponse:
        choice = resp.choices[0]
        message = choice.message
        calls: list[ToolCall] = []
        for index, tc in enumerate(getattr(message, "tool_calls", None) or []):
            try:
                arguments = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {"_raw": tc.function.arguments}
            calls.append(ToolCall(tc.id or f"call_{index}", tc.function.name, arguments))
        usage = getattr(resp, "usage", None)
        prompt = getattr(usage, "prompt_tokens", 0) if usage else 0
        completion = getattr(usage, "completion_tokens", 0) if usage else 0
        total = getattr(usage, "total_tokens", None) if usage else None
        return ModelResponse(
            content=message.content or "",
            tool_calls=calls,
            finish_reason=choice.finish_reason or "stop",
            usage={
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": total if total is not None else prompt + completion,
            },
            model=requested_model,
        )
