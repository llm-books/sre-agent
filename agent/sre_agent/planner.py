"""The planner decides the next step. This is the only place a model belongs.

Two implementations:

  ScriptedPlanner  deterministic, no external dependencies. It runs a fixed,
                   believable investigation (latency, errors, deploys, then a
                   hypothesis) so the durability and resume machinery can be
                   demonstrated offline, with no API key. Its decisions are a
                   pure function of the evidence gathered so far, which also
                   makes replay trivially correct.

  LLMPlanner       calls a real model (Anthropic) to choose the next step. Its
                   decisions are recorded in the durable log, so a resumed
                   workflow reuses the earlier reasoning instead of paying to
                   regenerate it and risking divergence.

Both return the same Decision shape, so the orchestrator does not care which is
in use. That is the orchestrator-executor split doing its job: the model's role
is confined to deciding, behind a clean interface.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Protocol

from .state import InvestigationState


@dataclass
class Decision:
    action: str                       # "tool" or "conclude"
    reason: str = ""
    tool: str | None = None           # when action == "tool"
    args: dict = field(default_factory=dict)
    hypothesis: str | None = None     # when action == "conclude"
    remediation: str | None = None    # when action == "conclude"
    action_id: str | None = None      # ch12: the rollout remediation id, when concluding

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "reason": self.reason,
            "tool": self.tool,
            "args": self.args,
            "hypothesis": self.hypothesis,
            "remediation": self.remediation,
            "action_id": self.action_id,
        }

    @staticmethod
    def from_dict(d: dict) -> "Decision":
        return Decision(**d)


class Planner(Protocol):
    def next_step(self, state: InvestigationState) -> Decision: ...


def _p95_latency(service: str) -> str:
    return (
        "histogram_quantile(0.95, sum by (service, le) "
        f'(rate(http_request_duration_seconds_bucket{{service="{service}"}}[1m])))'
    )


def _error_ratio(service: str) -> str:
    return (
        f'sum(rate(http_requests_total{{service="{service}",code="5xx"}}[5m])) '
        f'/ sum(rate(http_requests_total{{service="{service}"}}[5m]))'
    )


class ScriptedPlanner:
    """A fixed investigation, decided purely from the evidence gathered so far."""

    def next_step(self, state: InvestigationState) -> Decision:
        svc = state.incident.service
        n = state.step_count
        if n == 0:
            return Decision(
                action="tool", tool="promql_query", args={"query": _p95_latency(svc)},
                reason=f"check {svc} latency first, the alert points there",
            )
        if n == 1:
            return Decision(
                action="tool", tool="promql_query", args={"query": _error_ratio(svc)},
                reason=f"rule a 5xx error spike on {svc} in or out",
            )
        if n == 2:
            return Decision(
                action="tool", tool="deploy_history", args={"service": svc},
                reason=f"correlate with any recent deploy to {svc}",
            )
        # Conclude from the evidence, without peeking at the scenario answer key.
        deploys = _find(state, "deploy_history")
        had_recent_deploy = bool(deploys and deploys.get("recent"))
        if had_recent_deploy:
            hypothesis = (
                f"{svc} latency is elevated and a recent deploy to {svc} lines up "
                f"with the onset; the deploy is the likely cause."
            )
            remediation = f"Review and consider rolling back the recent {svc} deploy."
        else:
            hypothesis = (
                f"{svc} latency is elevated with no recent deploy to explain it, "
                f"which points at a query, index, or resource issue inside {svc}."
            )
            remediation = f"Investigate {svc} for a slow query or resource pressure."
        from .rollout.config import for_service
        return Decision(
            action="conclude", hypothesis=hypothesis, remediation=remediation,
            reason="enough evidence gathered to form a hypothesis",
            action_id=for_service(svc),
        )


def _find(state: InvestigationState, tool: str) -> dict | None:
    for e in state.evidence:
        if e.get("kind") == tool:
            return e.get("data")
    return None


class LLMPlanner:
    """Decide the next step with a real model. Optional; needs ANTHROPIC_API_KEY."""

    SYSTEM = (
        "You are an SRE agent investigating one incident. Decide the single next "
        "step. Reply ONLY with JSON. To investigate, reply "
        '{"action":"tool","tool":"promql_query"|"deploy_history","args":{...},"reason":"..."}. '
        "When you have enough evidence, reply "
        '{"action":"conclude","hypothesis":"...","remediation":"...","reason":"..."}. '
        "Available tools: promql_query(query), deploy_history(service)."
    )

    def __init__(self, model: str = "claude-sonnet-4-5"):
        try:
            import anthropic  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "LLMPlanner needs the anthropic package: pip install anthropic"
            ) from e
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("LLMPlanner needs ANTHROPIC_API_KEY set")
        import anthropic

        self._client = anthropic.Anthropic()
        self._model = model

    def next_step(self, state: InvestigationState) -> Decision:
        evidence = json.dumps(state.evidence, indent=2)
        prompt = (
            f"Incident: alert={state.incident.alert} service={state.incident.service}\n"
            f"Evidence so far:\n{evidence}\n\nWhat is the next step?"
        )
        msg = self._client.messages.create(
            model=self._model,
            max_tokens=512,
            system=self.SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        # Be forgiving about fenced JSON.
        if text.startswith("```"):
            text = text.strip("`").split("\n", 1)[-1]
        return Decision.from_dict(json.loads(text))


def default_planner() -> Planner:
    """Pick a planner from the environment. Scripted unless AGENT_PLANNER=llm."""
    if os.environ.get("AGENT_PLANNER", "scripted").lower() == "llm":
        return LLMPlanner()
    return ScriptedPlanner()
