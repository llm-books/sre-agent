# agent/

The SRE agent. It grows one component per chapter; see `../README-CHAPTERS.md`
for the checkpoint map. The synthetic environment in `../env/` is fixed; only
this directory and `../evals/` change across chapters.

## At the ch04 checkpoint

The agent now has its control-flow spine: a **durable orchestrator** and an
**executor**, split so the orchestrator decides and records while the executor
touches the world. The agent can pick up an alert, run a checkpointed multi-step
investigation against the live environment, survive a worker crash, and resume
without re-running completed steps or duplicating its side effect.

```
agent/
  scope.yaml              the ch03 boundary as config (read at startup)
  requirements.txt
  sre_agent/
    db.py                 Postgres connection + the durable-log schema
    scope.py              loads and enforces scope.yaml
    state.py              the in-memory investigation state (rebuilt from the log)
    planner.py            ScriptedPlanner (offline) + optional LLMPlanner (Anthropic)
    orchestrator/
      engine.py           the durable engine: get_or_record_step (replay or run)
      orchestrator.py     the investigation loop
    executor/
      executor.py         runs steps; idempotency key on the side-effecting one
      tools.py            basic read-only tools (ch06 hardens these)
    cli.py                command line
  tests/
    test_resume.py        crash/resume + idempotency
```

## Run it

The agent runs from a local Python venv against the environment's exposed ports,
so the environment must be up first.

```
make up            # from the repo root: start the synthetic environment
make agent-setup   # create the agent venv and install deps
make agent-init    # create the agent database and schema
make agent-demo    # the ch04 showcase: crash mid-run, then resume
make agent-test    # the durability tests
```

Or directly:

```
cd agent && . .venv/bin/activate
python -m sre_agent run --alert HighRequestLatency --service orders
python -m sre_agent list
python -m sre_agent show --id <workflow-id>
```

## The crash/resume showcase

`make agent-demo` runs an investigation, crashes the worker after step 3, and
resumes. It prints the durable log at the moment of the crash (steps 0 to 3, no
side effect yet), then resumes and shows that the completed steps were replayed
rather than re-run, the investigation finished, and the side-effecting step ran
exactly once. Running it a third time replays the whole thing and still produces
no duplicate. That is the chapter 4 guarantee, made concrete:

- **Durability**: the `steps` table is the source of truth; a crashed worker
  resumes from it.
- **Idempotency**: the one state-changing step uses a key derived from the
  workflow id and step index, so a retry deduplicates instead of double-acting.

## How the model fits

The planner is the only place a model belongs, behind a clean interface. By
default the agent uses `ScriptedPlanner`, which is deterministic and needs no API
key, so the durability machinery runs offline. Set `AGENT_PLANNER=llm` and
`ANTHROPIC_API_KEY` to use a real model instead; its decisions are recorded in the
durable log, so a resumed run reuses the earlier reasoning rather than paying to
regenerate it.

## What's still basic here

The tools in `executor/tools.py` are read-only and undefended on purpose. Chapter
6 turns them into the real tool layer: schema validation, timeouts, retries,
fallbacks, partial results, contract tests, and the allowlist on the one tool
that can change the world. Remediations stay shadow-only until the chapter 12
rollout work. State lives only in Postgres for now; chapter 5 adds the
conversation and long-term-memory tiers.

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `AGENT_DSN` | `postgresql://postgres:dev@localhost:5432/agent` | the durable log |
| `PROM_URL` | `http://localhost:9090` | Prometheus for tool queries |
| `AGENT_PLANNER` | `scripted` | set to `llm` for the Anthropic planner |
| `AGENT_SCOPE` | `agent/scope.yaml` | scope config path |
