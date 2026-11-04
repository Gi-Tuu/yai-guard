"""VultrModel 的离线单测：解析、防静默回落、重试分类、usage 累计、预算选择。

全部用 fake client，不联网、不需要 key。真实 key 的 Day-0 验证见
docs/evidence/WO-02-inference.md。
"""

from __future__ import annotations

import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.agent.model_adapter import (  # noqa: E402
    DEFAULT_MODEL,
    FINAL_TOKENS,
    MODEL_ALLOWLIST,
    TOOL_ROUND_TOKENS,
    ModelError,
    ModelFallbackError,
    VultrModel,
)


def _resp(*, content="", model=DEFAULT_MODEL, tool_calls=None, prompt=10, completion=5):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        model=model,
        choices=[SimpleNamespace(message=message,
                                 finish_reason="tool_calls" if tool_calls else "stop")],
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion, total_tokens=None),
    )


def _tool_call(name="run_command", arguments='{"command": "ls"}'):
    return SimpleNamespace(id="call_1", function=SimpleNamespace(name=name, arguments=arguments))


class _FakeCompletions:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeClient:
    def __init__(self, model_ids=None, outcomes=None):
        ids = model_ids or [DEFAULT_MODEL]
        self.models = SimpleNamespace(list=lambda: [SimpleNamespace(id=m) for m in ids])
        self.chat = SimpleNamespace(completions=_FakeCompletions(outcomes or []))


class _HttpError(Exception):
    def __init__(self, status, text, retry_after=None):
        super().__init__(text)
        self.status_code = status
        self.response = SimpleNamespace(headers={"Retry-After": retry_after} if retry_after else {})
        self.response.status_code = status


class ModelAdapterTest(unittest.TestCase):
    def test_response_is_parsed(self) -> None:
        client = _FakeClient(outcomes=[_resp(content="hello", tool_calls=[_tool_call()])])
        result = VultrModel(client=client).complete([{"role": "user", "content": "hi"}])
        self.assertEqual(result.content, "hello")
        self.assertEqual(result.model, DEFAULT_MODEL)
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0].name, "run_command")
        self.assertEqual(result.tool_calls[0].arguments, {"command": "ls"})
        self.assertEqual(result.usage["prompt_tokens"], 10)
        self.assertEqual(result.usage["completion_tokens"], 5)
        self.assertEqual(result.usage["total_tokens"], 15)

    def test_invalid_tool_arguments_are_preserved(self) -> None:
        client = _FakeClient(outcomes=[
            _resp(tool_calls=[
                SimpleNamespace(id="c1", function=SimpleNamespace(
                    name="run_command", arguments="{bad json")),
            ])
        ])
        result = VultrModel(client=client).complete([{"role": "user", "content": "x"}])
        self.assertEqual(result.tool_calls[0].arguments, {"_raw": "{bad json"})

    def test_silent_model_fallback_raises(self) -> None:
        client = _FakeClient(outcomes=[_resp(model="deepseek-v4.1-flash")])
        with self.assertRaises(ModelFallbackError):
            VultrModel(client=client).complete([{"role": "user", "content": "x"}])
        self.assertEqual(len(client.chat.completions.calls), 1)

    def test_unlisted_model_is_rejected_before_request(self) -> None:
        client = _FakeClient(outcomes=[_resp()])
        with self.assertRaises(ValueError):
            VultrModel(model="gpt-4o", client=client).complete([{"role": "user", "content": "x"}])
        self.assertEqual(client.chat.completions.calls, [])

    def test_assert_model_rejects_unknown_id(self) -> None:
        client = _FakeClient(model_ids=[DEFAULT_MODEL])
        model = VultrModel(model=DEFAULT_MODEL, client=client)
        model.assert_model()  # 在目录里 → 通过
        with self.assertRaises(ValueError):
            VultrModel(model="not-a-real-model", client=client).assert_model()

    def test_assert_model_rejects_catalog_miss(self) -> None:
        client = _FakeClient(model_ids=["some-other-model"])
        with self.assertRaises(ValueError):
            VultrModel(model=DEFAULT_MODEL, client=client).assert_model()

    def test_429_is_retried_and_retry_after_is_honoured(self) -> None:
        client = _FakeClient(outcomes=[_HttpError(429, "rate limited", retry_after="0"), _resp(content="ok")])
        with patch("app.agent.model_adapter.time.sleep") as sleep:
            result = VultrModel(client=client).complete([{"role": "user", "content": "x"}])
        self.assertEqual(result.content, "ok")
        self.assertEqual(len(client.chat.completions.calls), 2)
        sleep.assert_called_once()

    def test_500_is_retryable_but_400_is_not(self) -> None:
        self.assertTrue(VultrModel._retryable(_HttpError(500, "server error")))
        self.assertFalse(VultrModel._retryable(_HttpError(400, "bad request")))
        client = _FakeClient(outcomes=[_HttpError(400, "bad request")])
        with self.assertRaises(ModelError):
            VultrModel(client=client, max_retries=3).complete([{"role": "user", "content": "x"}])
        self.assertEqual(len(client.chat.completions.calls), 1)

    def test_error_message_does_not_leak_api_key(self) -> None:
        secret = "sk-live-supersecret-123456"
        client = _FakeClient(outcomes=[_HttpError(
            401, f"Unauthorized: Authorization: Bearer {secret}")])
        with self.assertRaises(ModelError) as ctx:
            VultrModel(api_key=secret, client=client).complete([{"role": "user", "content": "x"}])
        self.assertNotIn(secret, str(ctx.exception))
        self.assertIn("***", str(ctx.exception))

    def test_usage_accumulates_across_calls(self) -> None:
        client = _FakeClient(outcomes=[_resp(prompt=10, completion=5), _resp(prompt=20, completion=7)])
        model = VultrModel(client=client)
        model.complete([{"role": "user", "content": "a"}])
        model.complete([{"role": "user", "content": "b"}])
        self.assertEqual(model.total_usage["prompt_tokens"], 30)
        self.assertEqual(model.total_usage["completion_tokens"], 12)
        self.assertEqual(model.total_usage["total_tokens"], 42)

    def test_token_budget_depends_on_tool_round(self) -> None:
        client = _FakeClient(outcomes=[_resp(), _resp()])
        model = VultrModel(client=client)
        model.complete([{"role": "user", "content": "x"}], tools=[{"type": "function"}])
        model.complete([{"role": "user", "content": "y"}])
        first, second = client.chat.completions.calls
        self.assertEqual(first["max_completion_tokens"], TOOL_ROUND_TOKENS)
        self.assertEqual(first["tool_choice"], "auto")
        self.assertEqual(second["max_completion_tokens"], FINAL_TOKENS)
        self.assertIsNone(second["tool_choice"])

    def test_allowlist_matches_documented_catalog(self) -> None:
        self.assertIn(DEFAULT_MODEL, MODEL_ALLOWLIST)
        self.assertEqual(len(MODEL_ALLOWLIST), 2)


if __name__ == "__main__":
    unittest.main()
