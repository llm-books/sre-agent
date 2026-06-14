"""Rollout tests: the matrix loads, graduation is data-driven, and the orchestrator
dispatches each action by its mode. The dispatch test needs Postgres and skips if
it's down."""
from __future__ import annotations

import pytest

from sre_agent import db
from sre_agent.rollout import config
from sre_agent.rollout.graduation import recommend_mode


def test_rollout_matrix_loads():
    rems = config.all_remediations()
    assert len(rems) >= 5
    assert config.mode_for("restart_worker") == "autonomous"
    assert config.mode_for("extend_timeout") == "gated"  # unreliable diagnosis -> gated
    assert config.mode_for("rollback_config") == "assisted"  # reliable but high-stakes
    assert config.mode_for("address_leak") == "gated"
    assert config.for_service("notifications") == "restart_worker"


def test_graduation_is_data_driven():
    assert recommend_mode(1.0, 1.0, "low")[0] == "autonomous"     # reliable + low-stakes
    assert recommend_mode(0.7, 1.0, "low")[0] == "assisted"       # so-so + low-stakes
    assert recommend_mode(0.4, 1.0, "low")[0] == "gated"          # unreliable
    assert recommend_mode(0.9, 1.0, "moderate")[0] == "assisted"  # reliable + moderate
    assert recommend_mode(1.0, 1.0, "high")[0] == "gated"         # high-stakes stays gated
    assert recommend_mode(1.0, 0.8, "low")[0] == "gated"          # safety regression bars autonomy


def _db_up() -> bool:
    try:
        db.bootstrap()
        with db.connect() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


def _has_remediation(wf: str) -> bool:
    with db.connect() as conn:
        return conn.execute(
            "SELECT 1 FROM actions WHERE idempotency_key = %s", (f"{wf}:remediation",)
        ).fetchone() is not None


@pytest.mark.skipif(not _db_up(), reason="agent Postgres not reachable")
def test_dispatch_by_mode():
    from sre_agent.orchestrator.orchestrator import Orchestrator, make_workflow_id
    from sre_agent.state import Incident

    orch = Orchestrator(use_memory=False)

    # Autonomous: the agent acts (a remediation is dispatched).
    inc = Incident(alert="HighRequestLatency", service="orders")
    wf = make_workflow_id(inc, run=61001)
    orch.reset(wf)
    orch.start(inc, run=61001)
    s = orch.run(wf)
    assert s.rollout_mode == "autonomous"
    assert _has_remediation(wf)

    # Gated: the agent only proposes (no remediation dispatched).
    inc2 = Incident(alert="HighRequestLatency", service="inventory")
    wf2 = make_workflow_id(inc2, run=61002)
    orch.reset(wf2)
    orch.start(inc2, run=61002)
    s2 = orch.run(wf2)
    assert s2.rollout_mode == "gated"
    assert s2.acted is False
    assert not _has_remediation(wf2)

    orch.reset(wf)
    orch.reset(wf2)
