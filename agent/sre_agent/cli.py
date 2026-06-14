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

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
