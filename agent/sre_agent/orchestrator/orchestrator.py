"""The investigation orchestrator.

It owns control flow and never touches the world directly. Each iteration is two
recorded steps: a DECIDE step (the planner chooses the next move) and an ACT step
(the executor runs a tool or records a proposal). Both are memoized on the durable
log via the engine, so a resumed workflow replays completed steps without
re-running them and continues from the first unrecorded step.

The orchestrator is deterministic on replay: it never reads the clock or calls a
tool itself. The only non-determinism, the planner's decisions and the tools'
results, lives in recorded step results, so replay reproduces the exact run.
"""
from __future__ import annotations

import re

from psycopg.types.json import Json

from .. import db, scope
from ..executor.executor import Executor
from ..planner import Decision, Planner, default_planner
from ..state import Incident, InvestigationState
from . import engine


class SimulatedCrash(RuntimeError):
    """Raised by the crash hook to demonstrate resume. Not a real failure."""


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def make_workflow_id(incident: Incident, run: int = 1) -> str:
    # Stable from the alert and service, so the same alert delivered twice maps to
    # the same workflow and deduplicates instead of investigating twice. The run
    # suffix only exists to make the local demo repeatable.
    return f"wf-{_slug(incident.service)}-{_slug(incident.alert)}-{run}"


class Orchestrator:
    def __init__(self, planner: Planner | None = None, executor: Executor | None = None):
        self.planner = planner or default_planner()
        self.executor = executor or Executor()
        self.scope = scope.load()

    # ---- lifecycle ----------------------------------------------------------

    def start(self, incident: Incident, run: int = 1) -> str:
        workflow_id = make_workflow_id(incident, run)
        if not self.scope.service_in_scope(incident.service):
            # Out of scope is wilderness: refuse and escalate rather than act.
            with db.connect() as conn:
                conn.execute(
                    "INSERT INTO workflows (id, kind, input, status) "
                    "VALUES (%s, %s, %s, 'escalated') ON CONFLICT (id) DO NOTHING",
                    (workflow_id, "incident", Json(incident.to_dict())),
                )
                conn.commit()
            return workflow_id
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO workflows (id, kind, input) VALUES (%s, %s, %s) "
                "ON CONFLICT (id) DO NOTHING",
                (workflow_id, "incident", Json(incident.to_dict())),
            )
            conn.commit()
        return workflow_id

    def reset(self, workflow_id: str) -> None:
        """Delete a workflow and its steps. For the repeatable local demo only."""
        with db.connect() as conn:
            conn.execute("DELETE FROM steps WHERE workflow_id = %s", (workflow_id,))
            conn.execute("DELETE FROM actions WHERE workflow_id = %s", (workflow_id,))
            conn.execute("DELETE FROM workflows WHERE id = %s", (workflow_id,))
            conn.commit()

    # ---- the loop -----------------------------------------------------------

    def run(self, workflow_id: str, crash_after: int | None = None) -> InvestigationState:
        conn = db.connect()
        try:
            wf = conn.execute(
                "SELECT input, status FROM workflows WHERE id = %s", (workflow_id,)
            ).fetchone()
            if wf is None:
                raise ValueError(f"no such workflow: {workflow_id}")
            if wf["status"] == "escalated":
                state = InvestigationState(incident=Incident.from_dict(wf["input"]))
                state.hypothesis = "out of scope, escalated to a human"
                state.done = True
                return state

            incident = Incident.from_dict(wf["input"])
            state = self._replay_state(conn, workflow_id, incident)
            step_index = engine.next_index(conn, workflow_id)

            while not state.done:
                if step_index % 2 == 0:
                    # DECIDE. The planner is only invoked for a NEW step; a recorded
                    # decision is replayed, so resume reuses the earlier reasoning.
                    result, replayed = engine.get_or_record_step(
                        conn, workflow_id, step_index, "decide",
                        {"evidence_count": state.step_count},
                        compute=lambda c: self.planner.next_step(state).to_dict(),
                    )
                    step_index += 1
                    self._maybe_crash(crash_after, step_index - 1, replayed)
                else:
                    decision = Decision.from_dict(engine.get_step(conn, workflow_id, step_index - 1))
                    if decision.action == "conclude":
                        result, replayed = engine.get_or_record_step(
                            conn, workflow_id, step_index, "action",
                            {"kind": "record_proposal"},
                            compute=lambda c: self.executor.record_proposal(
                                c, workflow_id, step_index,
                                decision.hypothesis, decision.remediation),
                        )
                        state.hypothesis = decision.hypothesis
                        state.proposed_remediation = decision.remediation
                        self._finish(conn, workflow_id)
                        state.done = True
                        step_index += 1
                    else:
                        result, replayed = engine.get_or_record_step(
                            conn, workflow_id, step_index, "tool",
                            {"tool": decision.tool, "args": decision.args},
                            compute=lambda c: self.executor.run_tool(decision),
                        )
                        state.add_evidence(decision.tool, decision.reason, result.get("data"))
                        step_index += 1
                        self._maybe_crash(crash_after, step_index - 1, replayed)
            return state
        finally:
            conn.close()

    # ---- helpers ------------------------------------------------------------

    def _replay_state(self, conn, workflow_id: str, incident: Incident) -> InvestigationState:
        state = InvestigationState(incident=incident)
        for row in engine.replay_steps(conn, workflow_id):
            if row["kind"] == "tool":
                req, res = row["request"], row["result"]
                state.add_evidence(req.get("tool"), "", res.get("data"))
            elif row["kind"] == "action":
                act = row["result"].get("action", {})
                state.hypothesis = act.get("hypothesis")
                state.proposed_remediation = act.get("remediation")
                state.done = True
        return state

    def _finish(self, conn, workflow_id: str) -> None:
        conn.execute(
            "UPDATE workflows SET status = 'done', updated_at = now() WHERE id = %s",
            (workflow_id,),
        )
        conn.commit()

    def _maybe_crash(self, crash_after: int | None, last_step: int, replayed: bool) -> None:
        if crash_after is not None and not replayed and last_step >= crash_after:
            raise SimulatedCrash(f"simulated crash after recording step {last_step}")
