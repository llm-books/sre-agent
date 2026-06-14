"""Multi-agent tests: the verifier flags a wrong primary, and the policy math
shows targeted invocation matches 'everywhere' at lower cost. The end-to-end
experiment is DB-gated."""
from __future__ import annotations

import pytest

from sre_agent import db
from sre_agent.multiagent.experiment import CaseResult, apply_policy
from sre_agent.multiagent.verifier import Verifier


def test_verifier_flags_disagreement_and_agrees_on_match():
    from sre_agent.multiagent.verifier import HEURISTICS
    v = Verifier()
    # A primary matching the verifier's independent view -> no flag.
    assert not v.review("inventory", [], HEURISTICS["inventory"]).flagged
    # A primary that clearly disagrees -> flag, at a real token cost.
    flag = v.review("inventory", [], "the system is healthy and nothing is wrong")
    assert flag.flagged
    assert flag.added_tokens > 0
    # No structural tell for the payments timeout-mismatch: the verifier has no
    # independent objection, echoes the primary, and does NOT flag -> that error is
    # left for a human, not the verifier. Independence is not sufficiency.
    assert not v.review("payments", [], "a resource problem inside payments").flagged
    # No heuristic for orders either, so the verifier agrees with the primary.
    assert not v.review("orders", [], "orders has a slow query").flagged


def _synthetic():
    return [
        CaseResult("easy", "easy", primary_correct=True, verifier_flagged=False,
                   verifier_correct=True, verifier_tokens=1000),
        CaseResult("hard1", "hard", primary_correct=False, verifier_flagged=True,
                   verifier_correct=True, verifier_tokens=1000),
        CaseResult("hard2", "hard", primary_correct=False, verifier_flagged=True,
                   verifier_correct=True, verifier_tokens=1000),
    ]


def test_targeted_matches_everywhere_at_lower_cost():
    results = _synthetic()
    none = apply_policy(results, "none")
    everywhere = apply_policy(results, "all")
    targeted = apply_policy(results, "targeted")

    assert none.invocations == 0
    assert none.effective_correctness < everywhere.effective_correctness
    # Same correctness gain...
    assert targeted.effective_correctness == everywhere.effective_correctness
    # ...at lower cost and fewer invocations.
    assert targeted.verifier_tokens < everywhere.verifier_tokens
    assert targeted.invocations < everywhere.invocations
    assert targeted.catches == everywhere.catches


def _db_up() -> bool:
    try:
        db.bootstrap()
        with db.connect() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_up(), reason="agent Postgres not reachable")
def test_experiment_runs_and_targeted_earns_its_cost():
    from sre_agent.multiagent.experiment import compare
    results, policies = compare()
    by = {p.policy: p for p in policies}
    assert len(results) >= 5
    assert by["targeted"].effective_correctness >= by["none"].effective_correctness
    assert by["targeted"].verifier_tokens <= by["all"].verifier_tokens
