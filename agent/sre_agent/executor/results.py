"""ToolResult: the honest-failure-over-silent-garbage contract.

Every tool returns one of these instead of raising or returning a bare value. The
agent can reason about a structured failure; it cannot reason about an exception
that crashed the run or a garbage value that looks like success. The four statuses
are the chapter's total-failure options plus the happy path:

  ok        clean success
  degraded  a fallback was used; the answer is second-best, and says so
  partial   some of a multi-source gather succeeded; `missing` lists the rest
  failure   no usable answer, with a reason the agent can act on
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolResult:
    status: str                       # ok | degraded | partial | failure
    data: Any = None
    reason: str = ""
    missing: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        # degraded and partial are usable; only a hard failure is not.
        return self.status in ("ok", "degraded", "partial")

    def map(self, fn: Callable[[Any], Any]) -> "ToolResult":
        """Transform the data of a usable result; pass failures through."""
        if not self.ok:
            return self
        return ToolResult(self.status, fn(self.data), self.reason, self.missing)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "ok": self.ok,
            "data": self.data,
            "reason": self.reason,
            "missing": self.missing,
        }

    @staticmethod
    def ok_(data: Any) -> "ToolResult":
        return ToolResult("ok", data)

    @staticmethod
    def degraded(data: Any, reason: str = "fallback used") -> "ToolResult":
        return ToolResult("degraded", data, reason)

    @staticmethod
    def partial(data: Any, missing: list, reason: str = "partial gather") -> "ToolResult":
        return ToolResult("partial", data, reason, missing)

    @staticmethod
    def failure(reason: str) -> "ToolResult":
        return ToolResult("failure", None, reason)
