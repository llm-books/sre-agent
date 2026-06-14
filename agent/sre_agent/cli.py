"""Command line for the ch04 agent.

    python -m sre_agent init
    python -m sre_agent run --alert HighRequestLatency --service orders
    python -m sre_agent list
    python -m sre_agent show --id <workflow-id>
    python -m sre_agent resume --id <workflow-id>
    python -m sre_agent demo-crash            # the showcase: crash mid-run, resume

The demo-crash command is the chapter 4 payoff: it runs an investigation, crashes
the worker partway, and resumes, showing that completed steps are not re-run and
the side effect is not duplicated.
"""
from __future__ import annotations

import argparse
import json

from . import db
from .orchestrator.orchestrator import Orchestrator, SimulatedCrash, make_workflow_id
from .state import Incident

DEMO_RUN = 99


def _print_steps(workflow_id: str) -> None:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT step_index, kind, request, result FROM steps "
            "WHERE workflow_id = %s ORDER BY step_index",
            (workflow_id,),
        ).fetchall()
        for r in rows:
            label = r["kind"].upper().ljust(7)
            if r["kind"] == "decide":
                d = r["result"]
                detail = d.get("tool") or d.get("action")
                why = d.get("reason", "")
                print(f"  [{r['step_index']}] {label} -> {detail}  ({why})")
            elif r["kind"] == "tool":
                samples = r["result"].get("data", {})
                print(f"  [{r['step_index']}] {label} {r['request'].get('tool')} -> {_summarize(samples)}")
            else:
                act = r["result"].get("action", {})
                print(f"  [{r['step_index']}] {label} record_proposal inserted={r['result'].get('inserted')}")
                print(f"        hypothesis: {act.get('hypothesis')}")


def _summarize(data) -> str:
    if isinstance(data, dict) and "samples" in data:
        if not data["samples"]:
            return "no samples"
        return ", ".join(f"{s['value']:.3f}" for s in data["samples"][:3])
    if isinstance(data, dict) and "recent" in data:
        return f"recent_deploy={data['recent']} (age_h={data.get('most_recent_age_hours')})"
    return json.dumps(data)[:80]


def _action_count(workflow_id: str) -> int:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM actions WHERE workflow_id = %s", (workflow_id,)
        ).fetchone()
        return row["n"]


def cmd_init(args):
    db.bootstrap()
    print("agent database and schema ready")


def cmd_run(args):
    db.bootstrap()
    orch = Orchestrator()
    incident = Incident(alert=args.alert, service=args.service)
    wf = orch.start(incident, run=args.run)
    print(f"workflow: {wf}")
    state = orch.run(wf)
    print(f"\nhypothesis: {state.hypothesis}")
    print(f"proposed:   {state.proposed_remediation}")
    print("\nsteps:")
    _print_steps(wf)


def cmd_resume(args):
    orch = Orchestrator()
    state = orch.run(args.id)
    print(f"resumed and completed: {args.id}")
    print(f"hypothesis: {state.hypothesis}")
    _print_steps(args.id)


def cmd_list(args):
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, status, updated_at FROM workflows ORDER BY updated_at DESC LIMIT 50"
        ).fetchall()
        for r in rows:
            print(f"  {r['status'].ljust(10)} {r['id']}")


def cmd_show(args):
    _print_steps(args.id)


def cmd_demo_crash(args):
    db.bootstrap()
    orch = Orchestrator()
    incident = Incident(alert="HighRequestLatency", service=args.service)
    wf = make_workflow_id(incident, run=DEMO_RUN)
    orch.reset(wf)                      # repeatable: clear any prior demo run
    orch.start(incident, run=DEMO_RUN)

    print(f"workflow: {wf}")
    print("\n1) Running, with a simulated crash after step 3 ...")
    try:
        orch.run(wf, crash_after=3)
    except SimulatedCrash as e:
        print(f"   CRASH: {e}")
    print("\n   durable log at the moment of the crash:")
    _print_steps(wf)
    print(f"   side-effect rows (actions table): {_action_count(wf)}  (expected 0, conclude not reached)")

    print("\n2) Resuming the same workflow ...")
    state = orch.run(wf)
    print("   resumed; completed steps were replayed, not re-run.")
    print("\n   final durable log:")
    _print_steps(wf)
    print(f"\n   hypothesis: {state.hypothesis}")
    print(f"   side-effect rows (actions table): {_action_count(wf)}  (expected exactly 1)")

    print("\n3) Running once more (fully replayed) to show idempotency ...")
    orch.run(wf)
    print(f"   side-effect rows still: {_action_count(wf)}  (expected still 1, no duplicate)")


def cmd_recall(args):
    orch = Orchestrator()
    incident = Incident(alert=args.alert, service=args.service)
    rec = orch.memory.recall(
        args.service, orch._symptom_query(incident),
        orch._current_version(args.service), k=args.k)
    if not rec:
        print(f"no memories for {args.service}")
        return
    for r in rec:
        print(f"  sim={r.similarity} stale={r.stale} [{r.occurred_at[:10]} v={r.service_version}]")
        print(f"      cause: {r.root_cause}")


def cmd_conversation(args):
    orch = Orchestrator()
    turns = orch.conversation.history(args.id)
    if not turns:
        print("(no conversation; Redis may be down, or the workflow has not run)")
    for t in turns:
        print(f"  {t['role']}: {t['text']}")


def cmd_demo_memory(args):
    db.bootstrap()
    orch = Orchestrator()
    svc = args.service

    print("1) First incident on", svc, "-> handled and remembered")
    incA = Incident(alert="HighRequestLatency", service=svc)
    wfA = make_workflow_id(incA, run=991)
    orch.reset(wfA)
    with db.connect() as conn:
        conn.execute("DELETE FROM memory WHERE workflow_id IN (%s, %s)",
                     (wfA, make_workflow_id(incA, run=992)))
        conn.commit()
    orch.start(incA, run=991)
    stateA = orch.run(wfA)
    print(f"   hypothesis: {stateA.hypothesis}")

    print("\n2) A second, similar incident recalls the first")
    incB = Incident(alert="HighRequestLatency", service=svc)
    wfB = make_workflow_id(incB, run=992)
    orch.reset(wfB)
    orch.start(incB, run=992)
    stateB = orch.run(wfB)
    sim = stateB.recalled[0].similarity if stateB.recalled else "n/a"
    print(f"   recalled {len(stateB.recalled)} past incident(s), top similarity {sim}")
    print(f"   hypothesis now carries a Memory clause:\n   {stateB.hypothesis}")
    print("   conversation thread (regenerated from task state + new turns):")
    for t in orch.conversation.history(wfB):
        print(f"     {t['role']}: {t['text']}")

    print("\n3) After a version change, the same memory is treated as stale")
    with db.connect() as conn:
        conn.execute("UPDATE memory SET service_version = '0.0.1-old' WHERE workflow_id = %s", (wfA,))
        conn.commit()
    rec = orch.memory.recall(svc, orch._symptom_query(incB), orch._current_version(svc), k=3)
    for r in rec:
        print(f"     sim={r.similarity} stale={r.stale} (stored v={r.service_version}, current v={orch._current_version(svc)})")


def cmd_demo_tools(args):
    from .executor.tools import TOOLS

    print("Six tools, each behind the defensive wrapper (ok / degraded / partial / failure):\n")
    checks = [
        ("promql_query", {"query": "histogram_quantile(0.95, sum by (service, le) "
                                    "(rate(http_request_duration_seconds_bucket[1m])))"}),
        ("log_search", {"service": "orders"}),
        ("trace_lookup", {"service": "orders"}),
        ("deploy_history", {"service": "api-gateway"}),
        ("runbook_search", {"query": "payments timeout"}),
    ]
    for name, a in checks:
        res = TOOLS[name](a)
        detail = f"data={res.data}" if res.ok else f"reason={res.reason}"
        print(f"  {name:16} status={res.status:8} {detail}")

    print("\nThe drift defense: a bad upstream response becomes a clean failure, not garbage:")
    bad = TOOLS["promql_query"]({"query": "this is not (valid promql"})
    print(f"  promql_query(bad query)   status={bad.status:8} reason={bad.reason}")

    print("\nscoped_kubectl enforces its allowlist and forbidden actions in the tool itself:")
    cases = [
        {"command": "get", "target": "orders"},
        {"command": "restart", "target": "payments"},
        {"command": "delete", "target": "orders"},
        {"command": "rollout-restart", "target": "all"},
        {"command": "rollout-restart", "target": "orders"},
        {"command": "rollout-restart", "target": "orders", "approved": True},
        {"command": "hack", "target": "orders"},
    ]
    for c in cases:
        res = TOOLS["scoped_kubectl"](c)
        label = f"{c['command']} {c['target']}" + (" (approved)" if c.get("approved") else "")
        outcome = f"OK   {res.data.get('note')}" if res.ok else f"REFUSED {res.reason}"
        print(f"  {label:28} -> {outcome}")


def cmd_eval(args):
    from .evals.harness import format_report, run_evals
    report = run_evals(runs=args.runs)
    print(format_report(report))


def main(argv=None):
    p = argparse.ArgumentParser(prog="sre_agent", description="SRE agent (ch04)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)

    pr = sub.add_parser("run")
    pr.add_argument("--alert", default="HighRequestLatency")
    pr.add_argument("--service", default="orders")
    pr.add_argument("--run", type=int, default=1)
    pr.set_defaults(func=cmd_run)

    prs = sub.add_parser("resume")
    prs.add_argument("--id", required=True)
    prs.set_defaults(func=cmd_resume)

    sub.add_parser("list").set_defaults(func=cmd_list)

    ps = sub.add_parser("show")
    ps.add_argument("--id", required=True)
    ps.set_defaults(func=cmd_show)

    pd = sub.add_parser("demo-crash")
    pd.add_argument("--service", default="orders")
    pd.set_defaults(func=cmd_demo_crash)

    prc = sub.add_parser("recall")
    prc.add_argument("--service", default="orders")
    prc.add_argument("--alert", default="HighRequestLatency")
    prc.add_argument("--k", type=int, default=3)
    prc.set_defaults(func=cmd_recall)

    pc = sub.add_parser("conversation")
    pc.add_argument("--id", required=True)
    pc.set_defaults(func=cmd_conversation)

    pdm = sub.add_parser("demo-memory")
    pdm.add_argument("--service", default="orders")
    pdm.set_defaults(func=cmd_demo_memory)

    sub.add_parser("demo-tools").set_defaults(func=cmd_demo_tools)

    pe = sub.add_parser("eval")
    pe.add_argument("--runs", type=int, default=1)
    pe.set_defaults(func=cmd_eval)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
