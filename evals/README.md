# evals/

The eval harness scores the agent against the scenarios in `../env/scenarios/`,
using their `correct_diagnosis`, `correct_remediation`, and
`forbidden_remediations` fields as ground truth. The eval case and the chaos
scenario are the same file, so the thing the agent is tested against and the
thing it is scored on never drift apart.

## Where the code lives

The harness is implemented as the `sre_agent.evals` package (under `agent/`) so
it can import the agent directly. This directory holds the documentation and is
where run outputs are written (`results/`, gitignored). Run it with:

```
make agent-eval            # one run per scenario
make agent-eval RUNS=5     # five runs each (rates matter once the planner varies)
```

or `cd agent && .venv/bin/python -m sre_agent eval`.

## What it scores

Two levels, three dimensions, scored separately (don't judge what you can assert):

- **Trajectory-level**, per scenario, over `RUNS` runs:
  - *outcome correctness* — is the diagnosis equivalent to the reference? Judged
    by `sre_agent/evals/judge.py`. The default `EmbeddingJudge` is offline and
    crude, so it is **validated against human labels** before its numbers are
    trusted; set `AGENT_JUDGE=llm` for an Anthropic judge.
  - *safety* — did the agent propose a forbidden action? Deterministic set
    membership, not a judge.
  - *efficiency* — how many steps did the run take.
- **Step-level** — in a frozen initial context, is the first move sensible? This
  localizes where a trajectory failure happens.

## The loop this enables (ch08)

The scenarios the agent gets wrong here (with the scripted planner, payments and
inventory) are exactly the eval cases that should gate future deploys. Capturing
a production failure as a new scenario in `../env/scenarios/` adds it to both the
chaos engine and this harness at once. Chapter 8 wires the harness into a
deployment gate.
