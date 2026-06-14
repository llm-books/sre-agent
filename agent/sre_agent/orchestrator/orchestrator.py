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

from .. import cost, db, scope
from ..conversation import ConversationStore
from ..executor.executor import Executor
from ..executor.tools import fetch_deploys
from ..guardrails import output_guards
from ..memory.store import MemoryStore
from ..observability import tracing
from ..planner import Decision, Planner, default_planner
from ..rollout import approval, config as rollout_config
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
    def __init__(self, planner: Planner | None = None, executor: Executor | None = None,
                 use_memory: bool = True):
        self.planner = planner or default_planner()
        self.executor = executor or Executor()
        self.scope = scope.load()
        self.memory = MemoryStore()
        self.conversation = ConversationStore()
        self.approver = approval.Approver()  # ch12: stands in for the human reviewer
        # Evals run with memory off so each run is independent: recalling an
        # earlier eval run of the same scenario would make the runs non-i.i.d.
        self.use_memory = use_memory

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

    def run(self, workflow_id: str, crash_after: int | None = None,
            budget_tokens: int | None = None) -> InvestigationState:
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

            # ch05: the service's current version (for judging staleness) and a
            # recall of similar past incidents on this service from long-term
            # memory. Memory enriches context; it never overrides current signals.
            state.service_version = self._current_version(incident.service)
            if self.use_memory:
                state.recalled = self.memory.recall(
                    incident.service, self._symptom_query(incident),
                    state.service_version, k=3)

            # ch05: rebuild the engineer-facing conversation from the durable log.
            # This is the derive-from-task-state discipline: on resume the
            # narration is regenerated from what actually happened, so it can't
            # drift from task state.
            self.conversation.regenerate_from_steps(
                workflow_id, engine.replay_steps(conn, workflow_id))
            top = state.recalled[0] if state.recalled else None
            if top and top.similarity >= 0.6 and not top.stale:
                self.conversation.append(
                    workflow_id, "agent",
                    f"Seen a similar symptom on {incident.service} before "
                    f"({top.occurred_at[:10]}); weighing it against current signals.")

            # ch09: the whole investigation is one root span; each step a child
            # span. The trace is the substrate for evals, the gate, and drift.
            with tracing.span("incident", workflow_id=workflow_id,
                              alert=incident.alert, service=incident.service):
                while not state.done:
                    if step_index % 2 == 0:
                        # DECIDE. The planner is only invoked for a NEW step; a
                        # recorded decision is replayed, so resume reuses it.
                        with tracing.span("model.decide") as sp:
                            tracing.set_attr(sp, "step.index", step_index)
                            tracing.set_attr(sp, "cost.input_tokens_estimate",
                                             cost.cumulative_input_tokens(state.step_count))
                            result, replayed = engine.get_or_record_step(
                                conn, workflow_id, step_index, "decide",
                                {"evidence_count": state.step_count},
                                compute=lambda c: self._decide(state, budget_tokens),
                            )
                            if not replayed:
                                d = Decision.from_dict(result)
                                # Full prompt-context and completion on the span, plus
                                # the semantic 'why', so a reasoning failure is legible.
                                tracing.set_attr(sp, "llm.prompt", state.evidence)
                                tracing.set_attr(sp, "llm.completion", d.to_dict())
                                tracing.set_attr(sp, "agent.hypothesis", d.hypothesis or d.reason)
                                what = d.tool or d.action
                                self.conversation.append(
                                    workflow_id, "agent", f"Next: {what}. {d.reason}")
                        step_index += 1
                        self._maybe_crash(crash_after, step_index - 1, replayed)
                    else:
                        decision = Decision.from_dict(engine.get_step(conn, workflow_id, step_index - 1))
                        if decision.action == "conclude":
                            with tracing.span("action.record_proposal") as sp:
                                tracing.set_attr(sp, "step.index", step_index)
                                hypothesis = self._augment_with_memory(decision.hypothesis, state)
                                # ch11: output guardrail, the last check before an
                                # intent becomes an effect. A destructive or
                                # exfiltrating proposal is refused and escalated.
                                remediation = decision.remediation
                                ok, why = output_guards.validate_action(
                                    {"hypothesis": hypothesis, "remediation": remediation})
                                if not ok:
                                    tracing.set_attr(sp, "guardrail.blocked", why)
                                    remediation = (f"[blocked by output guardrail: {why}] "
                                                   f"escalating to a human")
                                result, replayed = engine.get_or_record_step(
                                    conn, workflow_id, step_index, "action",
                                    {"kind": "record_proposal"},
                                    compute=lambda c: self.executor.record_proposal(
                                        c, workflow_id, step_index, hypothesis, remediation),
                                )
                                act = result.get("action", {})
                                state.hypothesis = act.get("hypothesis")
                                state.proposed_remediation = act.get("remediation")
                                state.proposed_action_id = decision.action_id
                                tracing.set_attr(sp, "agent.hypothesis", state.hypothesis)
                                tracing.set_attr(sp, "agent.remediation", state.proposed_remediation)
                            self._finish(conn, workflow_id)
                            # ch05: write to long-term memory, idempotent per workflow.
                            if self.use_memory:
                                self._remember(conn, workflow_id, state)
                            self.conversation.append(
                                workflow_id, "agent", f"Proposed: {state.proposed_remediation}")
                            state.done = True
                            step_index += 1
                        else:
                            with tracing.span(f"tool.{decision.tool}") as sp:
                                tracing.set_attr(sp, "step.index", step_index)
                                tracing.set_attr(sp, "tool.args", decision.args)
                                result, replayed = engine.get_or_record_step(
                                    conn, workflow_id, step_index, "tool",
                                    {"tool": decision.tool, "args": decision.args},
                                    compute=lambda c: self.executor.run_tool(decision),
                                )
                                tracing.set_attr(sp, "tool.status", result.get("status"))
                                state.add_evidence(decision.tool, decision.reason, result.get("data"))
                                if not replayed:
                                    self.conversation.append(
                                        workflow_id, "agent", f"Checked {decision.tool}.")
                            step_index += 1
                            self._maybe_crash(crash_after, step_index - 1, replayed)
                # ch12: once concluded, dispatch the proposed remediation by its
                # rollout mode. Idempotent, so it's safe after a fresh run or a
                # resume (it runs here rather than inside the loop precisely so a
                # crash between concluding and acting still disposes on resume).
                if state.done and state.proposed_action_id:
                    self._dispose(conn, workflow_id, state)
            tracing.flush()
            return state
        finally:
            conn.close()

    # ---- helpers ------------------------------------------------------------

    def _decide(self, state: InvestigationState, budget_tokens: int | None) -> dict:
        """The next decision, unless the token budget is reached, in which case the
        agent wraps up gracefully with what it has and escalates, rather than
        spending through the ceiling."""
        if budget_tokens and cost.cumulative_input_tokens(state.step_count) > budget_tokens:
            return Decision(
                action="conclude",
                hypothesis=(f"Investigation of {state.incident.service} stopped at the token "
                            f"budget after {state.step_count} findings; conclusion is partial."),
                remediation="Escalate to a human to continue; the agent stopped at its budget.",
                reason="token budget reached",
            ).to_dict()
        return self.planner.next_step(state).to_dict()

    def _dispose(self, conn, workflow_id: str, state: InvestigationState) -> None:
        """Dispatch the proposed remediation by its rollout mode (ch12). Idempotent.
        Autonomous actions execute; assisted actions request approval and execute
        if approved, else escalate; gated actions are proposed only."""
        rem = rollout_config.get(state.proposed_action_id)
        if rem is None:
            state.rollout_mode = "none"
            return
        state.rollout_mode = rem.mode
        evidence = {"hypothesis": state.hypothesis,
                    "remediation": state.proposed_remediation, "service": rem.service}

        if rem.mode == "autonomous":
            outcome = self.executor.execute_remediation(conn, workflow_id, rem)
            state.acted = bool(outcome.get("executed"))
            self.conversation.append(workflow_id, "agent",
                                     f"Acted autonomously: {outcome.get('detail')}")
        elif rem.mode == "assisted":
            approval_id = approval.request(workflow_id, rem.action_id, evidence)
            decision = self.approver.decide(rem)
            approval.record_decision(approval_id, decision.approved, decision.reviewer, decision.reason)
            if decision.approved:
                outcome = self.executor.execute_remediation(conn, workflow_id, rem)
                state.acted = bool(outcome.get("executed"))
                self.conversation.append(workflow_id, "agent",
                                         f"Approved by {decision.reviewer}; acted: {outcome.get('detail')}")
            else:
                state.acted = False
                self.conversation.append(workflow_id, "agent",
                                         f"Held by {decision.reviewer}: {decision.reason}; escalated.")
        else:  # gated
            state.acted = False
            self.conversation.append(workflow_id, "agent",
                                     "Gated: proposed only, escalated to a human.")

    def _replay_state(self, conn, workflow_id: str, incident: Incident) -> InvestigationState:
        state = InvestigationState(incident=incident)
        for row in engine.replay_steps(conn, workflow_id):
            if row["kind"] == "tool":
                req, res = row["request"], row["result"]
                state.add_evidence(req.get("tool"), "", res.get("data"))
            elif row["kind"] == "decide" and row["result"].get("action") == "conclude":
                # ch12: recover the proposed remediation id so a resumed run can
                # still dispatch it after the loop.
                state.proposed_action_id = row["result"].get("action_id")
            elif row["kind"] == "action":
                act = row["result"].get("action", {})
                state.hypothesis = act.get("hypothesis")
                state.proposed_remediation = act.get("remediation")
                state.done = True
        return state

    # ---- ch05: memory helpers ----------------------------------------------

    def _symptom_query(self, incident: Incident) -> str:
        """The query symptom used to recall similar past incidents at the start,
        before any cause is known: just the alert and the service."""
        return f"{incident.alert} on {incident.service}"

    def _current_version(self, service: str) -> str | None:
        """The service's current version from the deploy ledger. A memory stored
        under a different version is treated as potentially stale."""
        try:
            deploys = fetch_deploys(service).get("deploys") or []
        except Exception:
            return None
        if not deploys:
            return None
        latest = max(deploys, key=lambda d: d.get("ts", ""))
        return latest.get("version")

    def _augment_with_memory(self, base_hypothesis: str | None, state: InvestigationState) -> str:
        base = base_hypothesis or ""
        top = state.recalled[0] if state.recalled else None
        if top and top.similarity >= 0.6 and not top.stale and top.root_cause:
            return (
                f"{base} Memory: a similar symptom on {state.incident.service} "
                f"({top.occurred_at[:10]}) had root cause: {top.root_cause} "
                f"This is context from the past, not a substitute for the current signals."
            )
        if top and top.stale:
            return (
                f"{base} (A past incident on this service looked similar, but it "
                f"predates the current version, so it is treated as stale.)"
            )
        return base

    def _remember(self, conn, workflow_id: str, state: InvestigationState) -> None:
        # Store the OBSERVED symptom (what a future incident queries by), not the
        # diagnosis. The diagnosis goes in root_cause. Keeping the symptom aligned
        # with the recall query is what makes similarity search actually match.
        symptom = self._symptom_query(state.incident)
        # Store the base diagnosis, stripped of any Memory clause we appended, so a
        # recalled-then-re-remembered hypothesis doesn't nest on itself.
        base = (state.hypothesis or "").split(" Memory:")[0].split(" (A past incident")[0].strip()
        self.memory.remember(
            conn,
            workflow_id=workflow_id,
            service=state.incident.service,
            symptom=symptom,
            root_cause=base,
            remediation=state.proposed_remediation,
            service_version=state.service_version,
        )
        conn.commit()

    def _finish(self, conn, workflow_id: str) -> None:
        conn.execute(
            "UPDATE workflows SET status = 'done', updated_at = now() WHERE id = %s",
            (workflow_id,),
        )
        conn.commit()

    def _maybe_crash(self, crash_after: int | None, last_step: int, replayed: bool) -> None:
        if crash_after is not None and not replayed and last_step >= crash_after:
            raise SimulatedCrash(f"simulated crash after recording step {last_step}")
