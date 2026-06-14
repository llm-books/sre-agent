"""Cost-model tests. The cost math is pure (no env); the budget-enforcement test
runs the agent and skips if Postgres is down."""
from __future__ import annotations

import pytest

from sre_agent import db
from sre_agent.cost import (
    cumulative_input_tokens,
    profile,
    profile_run,
    steps_from_evidence_counts,
)


def test_caching_and_routing_each_reduce_cost():
    steps = steps_from_evidence_counts([0, 1, 2, 3])
    p = profile(steps)
    # Each lever is at least as cheap as the one before, and routing is cheaper.
    assert p.cached_usd < p.naive_usd
    assert p.routed_usd < p.cached_usd
    assert p.savings_pct() > 0


def test_routing_sends_only_the_diagnosis_to_the_capable_model():
    steps = steps_from_evidence_counts([0, 1, 2, 3])
    assert steps[-1].model == "capable"      # the diagnosis is judgment
    assert all(s.model == "cheap" for s in steps[:-1])  # routine moves are cheap


def test_cumulative_tokens_is_monotonic():
    vals = [cumulative_input_tokens(n) for n in range(5)]
    assert vals == sorted(vals)
    assert all(b > a for a, b in zip(vals, vals[1:]))


def test_late_steps_cost_more_than_early_ones():
    # Cost concentrates in the late steps, which carry the most accumulated context.
    steps = steps_from_evidence_counts([0, 1, 2, 3, 4])
    assert steps[-1].input_tokens > steps[0].input_tokens


def _db_up() -> bool:
    try:
        db.bootstrap()
        with db.connect() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_up(), reason="agent Postgres not reachable")
def test_budget_forces_early_wrapup():
    from sre_agent.orchestrator.orchestrator import Orchestrator, make_workflow_id
    from sre_agent.state import Incident

    orch = Orchestrator(use_memory=False)
    incident = Incident(alert="HighRequestLatency", service="orders")

    wf_full = make_workflow_id(incident, run=60001)
    orch.reset(wf_full)
    orch.start(incident, run=60001)
    orch.run(wf_full)

    wf_budget = make_workflow_id(incident, run=60002)
    orch.reset(wf_budget)
    orch.start(incident, run=60002)
    state = orch.run(wf_budget, budget_tokens=2500)

    full = profile_run(wf_full)
    budgeted = profile_run(wf_budget)
    assert budgeted.steps < full.steps              # wrapped up earlier
    assert "budget" in (state.hypothesis or "").lower()

    orch.reset(wf_full)
    orch.reset(wf_budget)
