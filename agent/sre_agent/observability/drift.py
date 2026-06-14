"""Drift detection over two streams.

The agent watches two families of signal, and it needs both because they catch
two different things going wrong:

  agent-behavior drift   the agent's own runs getting longer or wandering. Caught
                         from the durable log (step counts over recent runs).
  environment drift      the world the agent watches moving out of band on a
                         signal that has NO threshold alert. This is the family
                         that catches the silent notifications failure: the queue
                         climbs with no error and no alert, and the trend flags it.

The environment family is the one the alert-driven agent in Field Notes 1 lacked
entirely.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

from .. import db
from ..executor.tools import promql_query

EVAL_RUN_MARKER = "%-70000"  # eval workflow ids end in the eval run number


@dataclass
class DriftFinding:
    family: str          # 'agent' | 'environment'
    signal: str
    value: float
    threshold: float | None
    status: str          # 'ok' | 'drift'
    detail: str


# Environment signals with NO threshold alert attached. Trending them out of band
# is the whole point: the absence of an alert is not the absence of a problem.
ENV_SIGNALS = [
    {
        "signal": "notifications.queue_depth",
        "query": 'max(worker_queue_depth{service="notifications"})',
        "normal_max": 20.0,
        "note": "queue climbing with no alert: the worker has silently stalled",
    },
    {
        "signal": "inventory.leaked_bytes",
        "query": 'max(process_leaked_bytes{service="inventory"})',
        "normal_max": 50 * 1024 * 1024,  # 50 MiB
        "note": "memory growing with no alert: a leak in inventory",
    },
]


def environment_drift() -> list[DriftFinding]:
    findings = []
    for sig in ENV_SIGNALS:
        res = promql_query({"query": sig["query"]})
        if not res.ok or not res.data.get("samples"):
            findings.append(DriftFinding("environment", sig["signal"], 0.0,
                                         sig["normal_max"], "ok", "no data"))
            continue
        val = max(s["value"] for s in res.data["samples"])
        drift = val > sig["normal_max"]
        findings.append(DriftFinding(
            "environment", sig["signal"], round(val, 1), sig["normal_max"],
            "drift" if drift else "ok",
            sig["note"] if drift else "within normal band"))
    return findings


def agent_behavior_drift(window: int = 21) -> list[DriftFinding]:
    """Trend recent runs' step counts. With a deterministic planner this is flat
    (honest); with an LLM planner a rising step count flags the agent wandering
    before its answers go outright wrong."""
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT w.id AS id, COUNT(s.*) AS steps, MAX(s.created_at) AS last
               FROM workflows w JOIN steps s ON s.workflow_id = w.id
               WHERE w.status = 'done' AND w.id NOT LIKE %s
               GROUP BY w.id ORDER BY MAX(s.created_at) DESC LIMIT %s""",
            (EVAL_RUN_MARKER, window),
        ).fetchall()
    if len(rows) < 6:
        return [DriftFinding("agent", "step_count", 0.0, None, "ok",
                             f"only {len(rows)} runs in history, need more to trend")]
    steps = [r["steps"] for r in rows]
    third = max(1, len(steps) // 3)
    recent = statistics.mean(steps[:third])
    baseline = statistics.mean(steps[third:])
    threshold = baseline * 1.5
    status = "drift" if baseline and recent > threshold else "ok"
    return [DriftFinding("agent", "step_count", round(recent, 1), round(threshold, 1),
                         status, f"recent mean {recent:.1f} vs baseline {baseline:.1f}")]


def drift_report() -> dict:
    return {"agent": agent_behavior_drift(), "environment": environment_drift()}
