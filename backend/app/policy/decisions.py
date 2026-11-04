# 决策模型：策略引擎的唯一输出类型。
# 设计约束：确定性、可序列化、可直接写入 trace（见 trace/store.py）。
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class Effect(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


# 风险等级与 04 文档 §5.4 的 R0–R3 对齐
RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


@dataclass(frozen=True)
class Decision:
    """一次工具调用的策略裁决。

    - effect: allow / deny / require_approval
    - rule_id: 命中规则（空操作时为内置规则名），必须写进 trace，保证可解释
    - reason: 给人和 LLM 看的原因
    - suggestion: 给 LLM 的替代方案（拒绝时避免其反复撞墙）
    """

    effect: Effect
    rule_id: str
    reason: str
    risk: str = "low"
    suggestion: str | None = None
    matched: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.risk not in RISK_ORDER:
            raise ValueError(f"unknown risk level: {self.risk}")

    @property
    def allowed(self) -> bool:
        return self.effect is Effect.ALLOW

    def to_dict(self) -> dict[str, Any]:
        return {
            "effect": self.effect.value,
            "rule_id": self.rule_id,
            "reason": self.reason,
            "risk": self.risk,
            "suggestion": self.suggestion,
            "matched": list(self.matched),
        }

    def for_llm(self) -> dict[str, Any]:
        """回灌给模型的紧凑形状（不泄露内部实现细节）。"""
        out: dict[str, Any] = {
            "ok": False,
            "decision": self.effect.value,
            "rule": self.rule_id,
            "reason": self.reason,
        }
        if self.suggestion:
            out["suggestion"] = self.suggestion
        return out


@dataclass(frozen=True)
class ToolCall:
    """Agent 发起的一次工具调用。"""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class EvalContext:
    """策略评估的运行时上下文（预算、时限、步骤）。"""

    step: int = 1
    max_steps: int = 20
    elapsed_s: float = 0.0
    task_timeout_s: int = 900
    task_id: str = ""

    def budget_exhausted(self) -> tuple[bool, str]:
        if self.step > self.max_steps:
            return True, f"step budget exhausted ({self.step}>{self.max_steps})"
        if self.elapsed_s > self.task_timeout_s:
            return True, f"task deadline exceeded ({self.elapsed_s:.1f}s>{self.task_timeout_s}s)"
        return False, ""
