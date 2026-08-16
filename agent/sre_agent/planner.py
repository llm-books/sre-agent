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

  GroqPlanner      the same idea on Groq's OpenAI-compatible API, defaulting to
                   a small open model that costs a fraction of a cent per
                   incident. Needs only GROQ_API_KEY; uses the requests package
                   already in the dependency list, no new SDK.

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


_TOOLS = {"promql_query", "deploy_history"}


def _decision_from_model_json(text: str, service: str) -> Decision:
    """Parse a model's JSON reply into a Decision, defensively.

    Models sometimes fence the JSON, add commentary keys, or omit fields. We
    strip fences, keep only the fields Decision knows, and fill action_id from
    the rollout config on conclude: which remediation maps to which service is
    configuration, not a model decision.
    """
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[: -3]
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        # Prose around the JSON is common without strict JSON mode; take the
        # outermost object. Anything less parseable is the caller's retry.
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        raw = json.loads(text[start:end + 1])
    known = {f for f in Decision.__dataclass_fields__}
    d = Decision(**{k: v for k, v in raw.items() if k in known})
    # Small models sometimes put the tool name in "action" instead of "tool".
    # Normalize that one obvious mistake; anything else unrecognized must be a
    # clean failure here, because an invalid action would otherwise never match
    # "conclude" and the investigation would run forever.
    if d.action not in ("tool", "conclude"):
        if d.action in _TOOLS or d.tool:
            d.tool = d.tool or d.action
            d.action = "tool"
        else:
            raise ValueError(f"model returned invalid action: {d.action!r}")
    if d.action == "conclude" and d.action_id is None:
        from .rollout.config import for_service
        d.action_id = for_service(service)
    return d


class LLMPlanner:
    """Decide the next step with a real model. Optional; needs ANTHROPIC_API_KEY."""

    SYSTEM = (
        "You are an SRE agent investigating one incident. Decide the single next "
        "step. Reply ONLY with JSON. To investigate, reply "
        '{"action":"tool","tool":"promql_query"|"deploy_history","args":{...},"reason":"..."}. '
        "When you have enough evidence, reply "
        '{"action":"conclude","hypothesis":"...","remediation":"...","reason":"..."}. '
        "Available tools: promql_query(query), deploy_history(service). "
        '"action" must be exactly "tool" or "conclude". Never repeat a tool call '
        "whose evidence you already have. Once you have checked latency, the "
        "error rate, and the deploy history, you MUST conclude."
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
        return _decision_from_model_json(text, state.incident.service)


class GroqPlanner:
    """Decide the next step with a small open model on Groq's cheap, fast API.

    Optional; needs GROQ_API_KEY. The API is OpenAI-compatible and we call it
    with the requests package already in the dependency list, so there is
    nothing new to install. The default model costs a fraction of a cent per
    incident, cheap enough for a reader to run the whole eval suite on.
    JSON mode makes the small model reliable at the structured reply.
    """

    URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, model: str | None = None):
        if not os.environ.get("GROQ_API_KEY"):
            raise RuntimeError("GroqPlanner needs GROQ_API_KEY set")
        # gpt-oss-20b is the cheapest model that reliably drives this loop; the
        # 8B-class models below it compose bad PromQL and never conclude. An
        # incident costs a fraction of a cent either way.
        self._model = model or os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")

    def next_step(self, state: InvestigationState) -> Decision:
        import time

        import requests

        svc = state.incident.service
        evidence = json.dumps(state.evidence, indent=2)
        # Small models are unreliable at composing PromQL, so hand them the
        # known-good queries; choosing WHICH signal to check next, and when to
        # stop, is the decision we are paying the model for.
        prompt = (
            f"Incident: alert={state.incident.alert} service={svc}\n"
            f"Known-good queries for this service:\n"
            f"  p95 latency: {_p95_latency(svc)}\n"
            f"  error ratio: {_error_ratio(svc)}\n"
            f"Evidence so far:\n{evidence}\n\nWhat is the next step?"
        )
        # Free-tier keys rate-limit per minute; 429s are expected, transient,
        # and retryable, the same lesson the ch06 tool wrapper teaches. A whole
        # eval sweep sits at the limit for minutes, so be patient, and honor
        # Retry-After when the server states it.
        attempts = 8
        json_mode = True
        messages = [
            {"role": "system", "content": LLMPlanner.SYSTEM},
            {"role": "user", "content": prompt},
        ]
        for attempt in range(attempts):
            body = {
                "model": self._model,
                # Reasoning models (gpt-oss) spend completion tokens thinking
                # before the JSON; a tight cap truncates mid-reply. Tokens this
                # cheap, be generous.
                "max_tokens": 2048,
                "temperature": 0,
                "messages": messages,
            }
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            resp = requests.post(
                self.URL,
                headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"},
                json=body,
                timeout=30,
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == attempts - 1:
                    resp.raise_for_status()
                wait = float(resp.headers.get("retry-after") or 2 ** attempt)
                time.sleep(min(wait, 60))
                continue
            if resp.status_code >= 400:
                # Some models trip over strict JSON mode itself (the server
                # validates the reply and 400s). Our parser is defensive anyway,
                # so fall back to a plain completion once before giving up.
                if json_mode and "json_validate_failed" in resp.text:
                    json_mode = False
                    continue
                # Groq's 4xx bodies say what was wrong; surface that instead of
                # a bare status code.
                raise RuntimeError(f"groq {resp.status_code}: {resp.text[:300]}")
            text = resp.json()["choices"][0]["message"]["content"] or ""
            try:
                return _decision_from_model_json(text, state.incident.service)
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                # A malformed reply is a transient failure, the same class as a
                # 429: tell the model what went wrong and ask again.
                if attempt == attempts - 1:
                    raise RuntimeError(
                        f"model reply unparseable after retries: {text[:200]!r}") from e
                messages = messages + [
                    {"role": "assistant", "content": text or "(empty)"},
                    {"role": "user", "content":
                        "That reply was not the required JSON. Reply with ONLY "
                        "the JSON object, no prose."},
                ]
                continue


def default_planner() -> Planner:
    """Pick a planner from the environment.

    Scripted unless AGENT_PLANNER=llm (Anthropic) or AGENT_PLANNER=groq.
    """
    choice = os.environ.get("AGENT_PLANNER", "scripted").lower()
    if choice == "llm":
        return LLMPlanner()
    if choice == "groq":
        return GroqPlanner()
    return ScriptedPlanner()
