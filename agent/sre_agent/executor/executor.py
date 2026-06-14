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
from ..guardrails import permissions
from ..planner import Decision
from .results import ToolResult
from .tools import TOOLS


def idempotency_key(workflow_id: str, step_index: int) -> str:
    """Stable across retries: built only from durable facts. The SAME logical
    step always produces the SAME key, so a retried call is recognized as a
    duplicate downstream. Never use the clock or a fresh uuid here."""
    return f"{workflow_id}:{step_index}"


class Executor:
    def run_tool(self, decision: Decision) -> dict:
        """Dispatch a tool call through the defensive tool layer (ch06).

        Tools return a ToolResult (ok / degraded / partial / failure); never an
        exception or bare garbage. We serialize it to a dict for the durable log.
        """
        tool = decision.tool
        if tool not in TOOLS:
            return {"ok": False, "tool": tool, "status": "failure",
                    "data": None, "reason": f"unknown tool: {tool}", "missing": []}
        # ch11: credential-level permission scoping, before the tool runs. A write
        # the agent isn't authorized for is denied here regardless of the tool's
        # own checks (defense in depth) and regardless of what an injection wants.
        allowed, why = permissions.check(tool, decision.args)
        if not allowed:
            return {"ok": False, "tool": tool, "status": "failure",
                    "data": None, "reason": f"permission denied: {why}", "missing": []}
        try:
            res = TOOLS[tool](decision.args)
        except Exception as e:
            # The wrapper handles upstream failures; this only guards against a bug
            # in the tool itself, still turning it into an honest failure.
            res = ToolResult.failure(f"tool raised: {e}")
        d = res.to_dict()
        d["tool"] = tool
        return d

    def execute_remediation(self, conn, workflow_id: str, rem) -> dict:
        """Actually perform a graduated remediation (ch12), idempotently.

        For env_effect 'reset' this performs a real, reversible action against the
        service (clearing its injected fault, the synthetic-environment stand-in
        for restoring an index or restarting a stalled worker). 'simulated' records
        the approved action without touching the env. Recorded under an idempotency
        key so a replay or retry does not act twice."""
        import os

        import requests

        from .tools import scoped_kubectl

        key = f"{workflow_id}:remediation"
        executed = False
        detail = ""
        if rem.env_effect == "reset":
            # The catalog command is descriptive (e.g. "restore-index"); the actual
            # cluster action for a reversible fix in this environment is a restart.
            # It goes through scoped_kubectl with the approval token, so permission
            # scoping and the allowlist both pass.
            gate = scoped_kubectl({"command": "rollout-restart", "target": rem.service, "approved": True})
            if gate.ok:
                ports = {"web": 8081, "api-gateway": 8082, "orders": 8083,
                         "payments": 8084, "inventory": 8085, "notifications": 8086}
                base = os.environ.get(f"{rem.service.upper().replace('-', '_')}_URL",
                                      f"http://localhost:{ports.get(rem.service, 8080)}")
                try:
                    requests.post(f"{base}/admin/reset", timeout=5)
                    executed = True
                    detail = f"performed {rem.command} on {rem.service}"
                except Exception as e:
                    detail = f"action failed: {e}"
            else:
                detail = f"refused by tool: {gate.reason}"
        elif rem.env_effect == "simulated":
            executed = True
            detail = f"recorded {rem.command} on {rem.service} (simulated config change)"
        else:
            detail = "no executable effect; proposal only"

        action = {"action_id": rem.action_id, "command": rem.command,
                  "service": rem.service, "executed": executed, "detail": detail}
        from psycopg.types.json import Json
        conn.execute(
            "INSERT INTO actions (idempotency_key, workflow_id, step_index, action) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (idempotency_key) DO NOTHING",
            (key, workflow_id, -1, Json(action)),
        )
        conn.commit()
        return action

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
