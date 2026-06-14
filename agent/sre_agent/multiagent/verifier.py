"""A verifier agent: the strongest multi-agent case (adversarial separation).

It reviews the primary agent's diagnosis with an independent perspective and flags
when it disagrees. Independence is the whole value, which is why it's a separate
agent rather than a check the primary runs on itself.

Here the independent perspective is a small set of adversarial SRE heuristics, the
kind a careful second pass applies: "if the symptom spans services through a shared
dependency, suspect the dependency"; "if there's a climbing backlog with no error
and no alert, suspect a stalled worker." These are structural tells. The payments
timeout-mismatch (a slow-but-succeeding downstream that an aggressive timeout treats
as a failure) has no such tell, so the verifier's independent pass lands on the same
service-local read the primary reaches, agrees with it, and misses it too.
Independence is not sufficiency: two independent reasoners can share a blind spot,
which is why payments still escalates to a human rather than being caught by the
verifier. In a real system the verifier would be an LLM doing open-ended adversarial
reasoning; the heuristics stand in for that offline. Either way it costs a model
pass, which is what has to be earned.
"""
from __future__ import annotations

from dataclasses import dataclass

from .. import cost
from ..evals.judge import Judge, default_judge

# Adversarial heuristics, framed as independent re-derivations of the diagnosis.
# Note there is deliberately no "payments" entry: the timeout-mismatch failure has
# no structural tell, so the verifier has no independent objection and falls back to
# echoing the primary's (wrong) service-local read. That error is left for a human.
HEURISTICS = {
    "inventory": ("The symptom spans several services through the shared cache, so "
                  "the root cause is the dependency, a memory leak in the inventory "
                  "service cascading downstream, not the service where the alert fired."),
    "notifications": ("There is no error and no alert but the queue backlog is "
                      "climbing, which points at a stalled worker that has stopped "
                      "processing, not a latency or error problem."),
}


@dataclass
class Verdict:
    flagged: bool
    independent_diagnosis: str
    reason: str
    added_tokens: int


class Verifier:
    def __init__(self, judge: Judge | None = None):
        self.judge = judge or default_judge()

    def review(self, service: str, evidence: list, primary_diagnosis: str) -> Verdict:
        independent = HEURISTICS.get(service, primary_diagnosis)
        agrees = self.judge.judge(independent, primary_diagnosis).equivalent
        # The verifier costs one model pass over the same context the primary used.
        tokens = (cost.STABLE_TOKENS + cost.INCIDENT_TOKENS
                  + len(evidence) * cost.EVIDENCE_TOKENS + cost.OUTPUT_TOKENS)
        return Verdict(
            flagged=not agrees,
            independent_diagnosis=independent,
            reason="disagrees with the primary" if not agrees else "agrees with the primary",
            added_tokens=tokens,
        )
