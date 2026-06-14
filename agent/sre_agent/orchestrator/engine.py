"""The durable execution engine, homegrown and small on purpose.

This is the few-hundred-line version the book describes: a workflow table, a
step-results table, and the one primitive that makes execution durable,
`get_or_record_step`. It memoizes each step on the durable log. If a step is
already recorded, its result is replayed without re-execution; otherwise it is
computed, recorded, and committed. That single rule is what lets a crashed worker
resume from where it died instead of restarting or leaving the world half-changed.

For a real deployment, swap this for Temporal (or Inngest, Restate, DBOS). The
orchestrator-executor split here maps directly onto Temporal's workflow/activity
distinction, so the agent code above this layer does not change.
"""
from __future__ import annotations

from typing import Any, Callable

from psycopg.types.json import Json


def get_step(conn, workflow_id: str, step_index: int) -> Any | None:
    row = conn.execute(
        "SELECT result FROM steps WHERE workflow_id = %s AND step_index = %s",
        (workflow_id, step_index),
    ).fetchone()
    return row["result"] if row else None


def record_step(conn, workflow_id, step_index, kind, request, result) -> None:
    conn.execute(
        "INSERT INTO steps (workflow_id, step_index, kind, request, result) "
        "VALUES (%s, %s, %s, %s, %s)",
        (workflow_id, step_index, kind, Json(request), Json(result)),
    )


def get_or_record_step(
    conn,
    workflow_id: str,
    step_index: int,
    kind: str,
    request: dict,
    compute: Callable[[Any], dict],
) -> tuple[Any, bool]:
    """Return (result, replayed).

    If step `step_index` is already in the durable log, return its recorded
    result and replayed=True, running nothing. Otherwise call compute(conn) to
    produce the result, record it, COMMIT, and return replayed=False.

    compute receives the connection so a state-changing step can perform its side
    effect in the SAME transaction as the step record, making the two atomic.
    Read-only steps just ignore the connection. For a side effect that cannot be
    transactional with this database (an external HTTP charge, say), the executor
    additionally guards it with an idempotency key, which is the general case the
    book builds toward.
    """
    existing = get_step(conn, workflow_id, step_index)
    if existing is not None:
        return existing, True
    result = compute(conn)
    record_step(conn, workflow_id, step_index, kind, request, result)
    conn.commit()
    return result, False


def replay_steps(conn, workflow_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT step_index, kind, request, result FROM steps "
        "WHERE workflow_id = %s ORDER BY step_index",
        (workflow_id,),
    ).fetchall()
    return rows


def next_index(conn, workflow_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(step_index) + 1, 0) AS n FROM steps WHERE workflow_id = %s",
        (workflow_id,),
    ).fetchone()
    return row["n"]
