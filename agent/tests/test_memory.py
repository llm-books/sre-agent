"""Long-term memory tests: recall by service and symptom, idempotent writes, and
the staleness flag. Skip if the agent Postgres is not reachable.

These use a synthetic service name (testsvc) so they are isolated from any real
or demo memory in the shared agent database.
"""
from __future__ import annotations

import pytest

from sre_agent import db
from sre_agent.memory.store import MemoryStore

SVC = "testsvc"
SYMPTOM = "HighRequestLatency on testsvc"


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
def store():
    s = MemoryStore()
    _clean()
    yield s
    _clean()


def _clean():
    with db.connect() as conn:
        conn.execute("DELETE FROM memory WHERE service IN (%s, %s)", (SVC, "othersvc"))
        conn.commit()


def test_recall_is_scoped_and_idempotent(store):
    with db.connect() as conn:
        first = store.remember(
            conn, workflow_id="test-mem-1", service=SVC, symptom=SYMPTOM,
            root_cause="slow query", remediation="restore index", service_version="1.0.0")
        conn.commit()
        again = store.remember(
            conn, workflow_id="test-mem-1", service=SVC, symptom=SYMPTOM,
            root_cause="other", remediation="other", service_version="1.0.0")
        conn.commit()
    assert first is True
    assert again is False  # idempotent per workflow

    rec = store.recall(SVC, SYMPTOM, current_version="1.0.0")
    assert len(rec) == 1
    assert rec[0].similarity > 0.9
    assert rec[0].root_cause == "slow query"

    # Scoped by service: a different service has no memory of it.
    assert store.recall("othersvc", "HighRequestLatency on othersvc") == []


def test_staleness_flag(store):
    with db.connect() as conn:
        store.remember(
            conn, workflow_id="test-mem-2", service=SVC, symptom=SYMPTOM,
            root_cause="slow query", remediation="restore index", service_version="1.0.0")
        conn.commit()

    fresh = store.recall(SVC, SYMPTOM, current_version="1.0.0")
    assert fresh[0].stale is False

    stale = store.recall(SVC, SYMPTOM, current_version="2.0.0")
    assert stale[0].stale is True
