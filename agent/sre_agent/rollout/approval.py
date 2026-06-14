"""The approval surface (ch12).

An action in assisted mode creates an approval request carrying the agent's
evidence and reasoning, so the human's approval is a real judgment, not a rubber
stamp. The decision is recorded: it feeds the action's track record, and a
rejection is a labeled example of the agent being wrong, a capture candidate for
the eval set.

The "human" here is an Approver policy so the demo runs without interactive input.
Stakes: 
    low/moderate reversible actions are approved,
    high-stakes ones are held for a human, which in the demo reads as a rejection.
"""
from __future__ import annotations

from dataclasses import dataclass

from psycopg.types.json import Json

from .. import db
from .config import Remediation


def request(workflow_id: str, action_id: str, evidence: dict) -> int:
    """Create (or return the existing) approval request for this workflow+action.
    Idempotent, so a replayed disposition does not create duplicate requests."""
    with db.connect() as conn:
        existing = conn.execute(
            "SELECT id FROM approvals WHERE workflow_id = %s AND action_id = %s",
            (workflow_id, action_id),
        ).fetchone()
        if existing:
            return existing["id"]
        row = conn.execute(
            "INSERT INTO approvals (workflow_id, action_id, evidence) "
            "VALUES (%s, %s, %s) RETURNING id",
            (workflow_id, action_id, Json(evidence)),
        ).fetchone()
        conn.commit()
        return row["id"]


def record_decision(approval_id: int, approved: bool, reviewer: str, reason: str) -> None:
    with db.connect() as conn:
        conn.execute(
            "UPDATE approvals SET status = %s, reviewer = %s, reason = %s WHERE id = %s",
            ("approved" if approved else "rejected", reviewer, reason, approval_id),
        )
        conn.commit()


def capture_candidates() -> list[dict]:
    """Rejected approvals: labeled examples of the agent being wrong, to capture
    as new eval scenarios."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT workflow_id, action_id, reason FROM approvals WHERE status = 'rejected' "
            "ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
    return [dict(r) for r in rows]


@dataclass
class Decision:
    approved: bool
    reviewer: str
    reason: str


class Approver:
    """A stand-in for the human reviewer. Friction calibrated to stakes."""

    def decide(self, rem: Remediation) -> Decision:
        # A human reviewing strong evidence approves a reversible action, with more
        # deliberate review when the stakes are high (the friction matches the
        # stakes). An irreversible action is held for a human to perform directly.
        if rem.reversible is True:
            care = "after deliberate review" if rem.stakes == "high" else "evidence clear"
            return Decision(True, "oncall-engineer",
                            f"{rem.stakes}-stakes reversible action approved, {care}")
        return Decision(False, "oncall-engineer",
                        "irreversible action held for a human to perform directly")
