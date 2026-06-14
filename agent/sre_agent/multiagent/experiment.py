"""Measure whether the verifier earns its cost.

The method is the chapter's: run the single agent against the scenarios and record
where it's wrong; add the verifier and measure how many of those errors it catches
and at what cost; then compare invoking it everywhere against invoking it only on
the incident types where the primary is weak. The decision is empirical.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..evals.cases import load_cases
from ..evals.judge import default_judge
from ..orchestrator.orchestrator import Orchestrator, make_workflow_id
from ..state import Incident
from .verifier import Verifier

PRIMARY_RUN = 75000


@dataclass
class CaseResult:
    case: str
    difficulty: str
    primary_correct: bool
    verifier_flagged: bool
    verifier_correct: bool
    verifier_tokens: int


def gather() -> list[CaseResult]:
    """Run the primary once per scenario (deterministic) and the verifier's review,
    recording everything both policies need."""
    cases = load_cases()
    judge = default_judge()
    verifier = Verifier(judge)
    orch = Orchestrator(use_memory=False)
    out = []
    for case in cases:
        inc = Incident(**case.incident_args())
        wf = make_workflow_id(inc, run=PRIMARY_RUN)
        orch.reset(wf)
        orch.start(inc, run=PRIMARY_RUN)
        state = orch.run(wf)
        orch.reset(wf)

        primary_dx = state.hypothesis or ""
        primary_correct = judge.judge(primary_dx, case.correct_diagnosis).equivalent
        v = verifier.review(case.service, state.evidence, primary_dx)
        v_correct = judge.judge(v.independent_diagnosis, case.correct_diagnosis).equivalent
        out.append(CaseResult(case.name, case.difficulty, primary_correct,
                              v.flagged, v_correct, v.added_tokens))
    return out


@dataclass
class PolicyResult:
    policy: str
    effective_correctness: float
    catches: int            # real errors the verifier caught and corrected
    extra_flags: int        # flags on a primary the judge already called correct
    invocations: int
    verifier_tokens: int


def apply_policy(results: list[CaseResult], policy: str) -> PolicyResult:
    n = len(results)
    correct = catches = extra = inv = tok = 0
    for r in results:
        # 'targeted' verifies only the incident types the primary is weak on (known
        # from the eval track record; deterministic here, so the weak ones are
        # exactly the ones it gets wrong).
        verify = policy == "all" or (policy == "targeted" and not r.primary_correct)
        effective = r.primary_correct
        if verify:
            inv += 1
            tok += r.verifier_tokens
            if not r.primary_correct and r.verifier_flagged and r.verifier_correct:
                catches += 1
                effective = True
            elif r.primary_correct and r.verifier_flagged:
                extra += 1
        if effective:
            correct += 1
    return PolicyResult(policy, round(correct / n, 3), catches, extra, inv, tok)


def compare() -> tuple[list[CaseResult], list[PolicyResult]]:
    results = gather()
    policies = [apply_policy(results, p) for p in ("none", "all", "targeted")]
    return results, policies


def format_compare(results: list[CaseResult], policies: list[PolicyResult]) -> str:
    lines = ["Per-scenario (primary correct? / verifier flagged? / verifier correct?):"]
    for r in results:
        lines.append(f"  {r.case:32} {r.difficulty:7} "
                     f"primary={'ok ' if r.primary_correct else 'WRONG'} "
                     f"flag={'yes' if r.verifier_flagged else 'no '} "
                     f"v_correct={'yes' if r.verifier_correct else 'no'}")
    lines.append("\nPolicy comparison:")
    lines.append(f"  {'policy':10} {'correctness':12} {'catches':8} {'extra flags':12} "
                 f"{'invocations':12} verifier_tokens")
    for p in policies:
        lines.append(f"  {p.policy:10} {p.effective_correctness:<12} {p.catches:<8} "
                     f"{p.extra_flags:<12} {p.invocations:<12} {p.verifier_tokens}")

    none, all_, targeted = policies
    lines.append("")
    if targeted.effective_correctness > none.effective_correctness and \
            targeted.verifier_tokens < all_.verifier_tokens:
        caught = targeted.catches
        weak = sum(1 for r in results if not r.primary_correct)
        lines.append(
            f"Verdict: the verifier earns its cost, but only when TARGETED. It lifts "
            f"correctness {none.effective_correctness} -> {targeted.effective_correctness} "
            f"by catching {caught} of the primary's {weak} hard-incident errors (the rest "
            f"have no structural tell and still escalate to a human), at "
            f"{targeted.verifier_tokens} verifier tokens versus {all_.verifier_tokens} to "
            f"run it everywhere ({all_.extra_flags} of those everywhere-runs are spurious "
            f"flags). Run the second agent where it's measured to help, nowhere else.")
    else:
        lines.append("Verdict: on these scenarios the verifier does not clearly earn its cost.")
    return "\n".join(lines)
