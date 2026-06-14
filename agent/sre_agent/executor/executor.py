"""The executor runs a single step against the real world.

It is the side-effecting half of the orchestrator-executor split. It calls tools,
and for the one kind of step that changes state, it applies an idempotency key so
a retry after a lost acknowledgment cannot duplicate the effect. It holds no
durable task state of its own; the orchestrator owns that.

At ch04 the only state-changing action is recording a proposed remediation, a
stand-in for the real, ch12-gated remediation. It is enough to make the
idempotency machinery real and testable: record the same proposal twice and the
second write is a no-op.
"""
from __future__ import annotations

from .. import db
from ..planner import Decision
from .tools import TOOLS


def idempotency_key(workflow_id: str, step_index: int) -> str:
    """Stable across retries: built only from durable facts. The SAME logical
    step always produces the SAME key, so a retried call is recognized as a
    duplicate downstream. Never use the clock or a fresh uuid here."""
    return f"{workflow_id}:{step_index}"


class Executor:
    def run_tool(self, decision: Decision) -> dict:
        """Execute a read-only tool call. No side effects, so naturally idempotent."""
        tool = decision.tool
        if tool not in TOOLS:
            return {"ok": False, "error": f"unknown tool: {tool}"}
        try:
            data = TOOLS[tool](decision.args)
            return {"ok": True, "tool": tool, "data": data}
        except Exception as e:  # ch06 replaces this with classified, defensive handling
            return {"ok": False, "tool": tool, "error": str(e)}

    def record_proposal(
        self, conn, workflow_id: str, step_index: int, hypothesis: str, remediation: str
    ) -> dict:
        """Side-effecting step: durably record the proposed remediation.

        Uses an idempotency key and INSERT ... ON CONFLICT DO NOTHING, so running
        this step twice (a retry, or a resume after a crash that performed the
        write but did not record the step result) inserts at most one row. This is
        the double-charge defense from chapter 4, made concrete on a real write.
        """
        key = idempotency_key(workflow_id, step_index)
        action = {"hypothesis": hypothesis, "remediation": remediation, "mode": "shadow"}
        from psycopg.types.json import Json

        cur = conn.execute(
            """
            INSERT INTO actions (idempotency_key, workflow_id, step_index, action)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (idempotency_key) DO NOTHING
            """,
            (key, workflow_id, step_index, Json(action)),
        )
        inserted = cur.rowcount == 1
        return {"ok": True, "idempotency_key": key, "inserted": inserted, "action": action}
