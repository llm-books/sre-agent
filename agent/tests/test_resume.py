"""Durability tests: a crashed investigation resumes without re-running completed
steps or duplicating its side effect.

These need the synthetic environment's Postgres reachable (make up). If it isn't,
they skip rather than fail, so the suite stays green on a machine without the env.
"""
from __future__ import annotations

import pytest

from sre_agent import db
from sre_agent.executor.executor import Executor
from sre_agent.orchestrator.orchestrator import Orchestrator, SimulatedCrash, make_workflow_id
from sre_agent.state import Incident

TEST_RUN = 1000


def _db_available() -> bool:
    try:
        db.bootstrap()
        with db.connect() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="agent Postgres not reachable")


@pytest.fixture
def orch():
    o = Orchestrator()
    incident = Incident(alert="HighRequestLatency", service="orders")
    wf = make_workflow_id(incident, run=TEST_RUN)
    o.reset(wf)
    o.start(incident, run=TEST_RUN)
    yield o, wf, incident
    o.reset(wf)


def _count(table: str, wf: str) -> int:
    with db.connect() as conn:
        return conn.execute(
            f"SELECT COUNT(*) AS n FROM {table} WHERE workflow_id = %s", (wf,)
        ).fetchone()["n"]


def test_crash_then_resume_is_clean(orch):
    o, wf, _ = orch

    # Crash after step 3 (the durable log should hold steps 0..3, no side effect yet).
    with pytest.raises(SimulatedCrash):
        o.run(wf, crash_after=3)
    assert _count("steps", wf) == 4
    assert _count("actions", wf) == 0

    # Resume: completes, records the remaining steps, performs the side effect ONCE.
    state = o.run(wf)
    assert state.done
    assert state.hypothesis
    assert _count("actions", wf) == 1
    steps_after = _count("steps", wf)
    assert steps_after > 4

    # Running again is a full replay: no new steps, no duplicate side effect.
    o.run(wf)
    assert _count("steps", wf) == steps_after
    assert _count("actions", wf) == 1


def test_idempotency_key_dedupes_side_effect(orch):
    o, wf, _ = orch
    ex = Executor()
    with db.connect() as conn:
        first = ex.record_proposal(conn, wf, 7, "h", "r")
        conn.commit()
        second = ex.record_proposal(conn, wf, 7, "h", "r")  # same wf + index => same key
        conn.commit()
    assert first["inserted"] is True
    assert second["inserted"] is False
    assert first["idempotency_key"] == second["idempotency_key"]
    assert _count("actions", wf) == 1
