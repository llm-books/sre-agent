"""Cost modeling, profiling, and budgets (ch10).

The agent's cost is dominated by the context sent on each model call, and the
context grows as the investigation accumulates evidence, so the late steps are the
expensive ones. This module models that, then shows the three levers from the
chapter shrinking it:

  - prompt caching: the stable prefix (system prompt + tool definitions) is sent
    every call, so caching it makes the repeated tokens nearly free.
  - model routing: routine "pick the next tool" decisions go to a cheap model;
    only the judgment call (the diagnosis) needs the capable one.
  - budgets: a per-incident token ceiling, enforced while the agent runs, so a
    runaway investigation wraps up gracefully instead of spending through.

With the scripted planner there is no real model call, so token counts are
ESTIMATED from context size. Plug in real usage from the LLM planner's responses
and the same math holds.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import db

# Rough token sizes for the assembled context. The stable prefix is what caching
# targets; the volatile part grows with the evidence gathered so far.
STABLE_TOKENS = 1000        # system prompt + tool definitions (cacheable prefix)
INCIDENT_TOKENS = 60        # the alert and service
EVIDENCE_TOKENS = 150       # per gathered finding
INSTRUCTION_TOKENS = 50
OUTPUT_TOKENS = 80          # the decision the model returns

# Illustrative prices, USD per 1M tokens. Cached input is a fraction of fresh.
PRICES = {
    "capable": {"in": 3.00, "cached_in": 0.30, "out": 15.00},
    "cheap":   {"in": 0.25, "cached_in": 0.025, "out": 1.25},
}


def estimate_tokens(text: str) -> int:
    return int(len(text) * 0.25)  # ~4 chars per token


@dataclass
class StepCost:
    index: int
    model: str            # 'capable' | 'cheap'  (the routing decision)
    stable_tokens: int
    volatile_tokens: int
    output_tokens: int

    @property
    def input_tokens(self) -> int:
        return self.stable_tokens + self.volatile_tokens


def steps_from_evidence_counts(counts: list[int]) -> list[StepCost]:
    """Build per-step costs from the evidence count at each decide step. Routing:
    the final decision (the diagnosis) is judgment and goes to the capable model;
    the routine 'pick the next tool' decisions go to the cheap one."""
    steps = []
    for i, n in enumerate(counts):
        is_diagnosis = i == len(counts) - 1
        steps.append(StepCost(
            index=i,
            model="capable" if is_diagnosis else "cheap",
            stable_tokens=STABLE_TOKENS,
            volatile_tokens=INCIDENT_TOKENS + n * EVIDENCE_TOKENS + INSTRUCTION_TOKENS,
            output_tokens=OUTPUT_TOKENS,
        ))
    return steps


def _usd(in_tok: int, out_tok: int, model: str, cached_in: int = 0) -> float:
    p = PRICES[model]
    fresh_in = in_tok - cached_in
    return (fresh_in * p["in"] + cached_in * p["cached_in"] + out_tok * p["out"]) / 1_000_000


@dataclass
class CostProfile:
    steps: int
    total_input_tokens: int
    naive_usd: float        # all capable, no caching
    cached_usd: float       # all capable, stable prefix cached after the first call
    routed_usd: float       # routing + caching together

    def savings_pct(self) -> float:
        if self.naive_usd == 0:
            return 0.0
        return round(100 * (1 - self.routed_usd / self.naive_usd), 1)


def profile(steps: list[StepCost]) -> CostProfile:
    naive = cached = routed = 0.0
    total_in = 0
    for i, s in enumerate(steps):
        total_in += s.input_tokens
        cached_prefix = s.stable_tokens if i > 0 else 0  # prefix is cached after call 0
        naive += _usd(s.input_tokens, s.output_tokens, "capable")
        cached += _usd(s.input_tokens, s.output_tokens, "capable", cached_in=cached_prefix)
        routed += _usd(s.input_tokens, s.output_tokens, s.model, cached_in=cached_prefix)
    return CostProfile(len(steps), total_in, round(naive, 6), round(cached, 6), round(routed, 6))


def profile_run(workflow_id: str) -> CostProfile:
    """Profile a real run from its durable log (the decide steps and how much
    evidence each one carried)."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT request FROM steps WHERE workflow_id = %s AND kind = 'decide' "
            "ORDER BY step_index", (workflow_id,),
        ).fetchall()
    counts = [r["request"].get("evidence_count", 0) for r in rows]
    return profile(steps_from_evidence_counts(counts))


def cumulative_input_tokens(evidence_count: int) -> int:
    """Estimated cumulative input tokens after gathering `evidence_count` findings,
    summed over the decide steps so far. The budget is checked against this."""
    total = 0
    for i in range(evidence_count + 1):
        total += STABLE_TOKENS + INCIDENT_TOKENS + i * EVIDENCE_TOKENS + INSTRUCTION_TOKENS
    return total
