# agent/

The SRE agent. It grows one component per chapter; see `../README-CHAPTERS.md`
for the checkpoint map. The synthetic environment in `../env/` is fixed; only
this directory and `../evals/` change across chapters.

## Checkpoints so far

**ch04** gave the agent its control-flow spine: a **durable orchestrator** and an
**executor**, split so the orchestrator decides and records while the executor
touches the world. It can pick up an alert, run a checkpointed investigation,
survive a worker crash, and resume without re-running completed steps or
duplicating its side effect.

**ch05** adds the three kinds of state. Task state already lived in the durable
log; now there is also **conversation state** in Redis (ephemeral, regenerated
from task state so it can never drift) and **long-term memory** in a vector store
keyed by service and symptom, with a staleness policy.

**ch06** turns the basic read-only calls into the real **defensive tool layer**:
six tools, each behind a wrapper that applies a timeout, classifies failures
(transient retry / auth refresh / permanent stop), validates the response schema
before reading, and returns an honest result (ok / degraded / partial / failure)
instead of throwing or returning garbage. `scoped_kubectl` enforces its allowlist
and the forbidden actions in the tool itself. Both flavors of contract test ship.

**ch07** adds the **eval harness** (`sre_agent/evals/`). It scores the agent
against the scenarios on three separate dimensions, outcome correctness (a
validated judge), safety (deterministic), and efficiency (steps), at both the
trajectory and step level. The judge is validated against human labels before its
numbers are trusted. The scenarios the agent fails become the cases that gate
deploys in ch08.

**ch08** turns the harness into a **deployment gate** (`sre_agent/evals/gate.py`).
It keeps a baseline (the deployed agent's profile, as a rolling re-measured
history so a lucky run can't inflate the bar), compares each candidate per
dimension with noise-aware thresholds (safety blocks hard, correctness blocks past
the noise band, efficiency only warns), and exits non-zero on a regression so CI
blocks the deploy. Shipping past a red gate requires a recorded override. It also
samples production runs reference-free to catch decay between deploys.

**ch09** adds **observability** (`sre_agent/observability/`). The agent emits an
OpenTelemetry trace of every incident to Tempo, with the full prompt, completion,
and hypothesis on each span (semantic logging), so any run is reconstructable. The
services also start emitting traces here, so `trace_lookup` returns real service
traces. Drift detection watches two families: the agent's own behavior, and the
environment's telemetry, the signals with no threshold alert, which is how the
silent notifications failure gets caught.

**ch10** adds **cost engineering** (`sre_agent/cost.py`). It models the per-incident
token cost (dominated by the context that grows as evidence accumulates), then
shrinks it with the chapter's three levers: prompt caching of the stable prefix,
routing routine steps to a cheap model while only the diagnosis uses the capable
one, and a per-incident token budget the orchestrator enforces, wrapping up
gracefully when it's reached rather than spending through.

```
agent/
  scope.yaml              the ch03 boundary as config (read at startup)
  requirements.txt
  sre_agent/
    db.py                 Postgres connection + the durable-log + memory schema
    scope.py              loads and enforces scope.yaml
    state.py              the in-memory investigation state (rebuilt from the log)
    planner.py            ScriptedPlanner (offline) + optional LLMPlanner (Anthropic)
    conversation.py       ch05: conversation state in Redis, regenerable from task state
    memory/
      embeddings.py       ch05: LocalHashEmbedder (offline) behind an Embedder interface
      store.py            ch05: the vector store; recall by service + symptom, staleness
    orchestrator/
      engine.py           the durable engine: get_or_record_step (replay or run)
      orchestrator.py     the investigation loop (now recalls + remembers + narrates)
    executor/
      executor.py         runs steps; idempotency key on the side-effecting one
      results.py          ch06: ToolResult (ok / degraded / partial / failure)
      schemas.py          ch06: response-shape schemas (validate before reading)
      wrapper.py          ch06: defensive_call + failure classification + gather
      tools.py            ch06: the six tools, each behind the wrapper
    evals/
      cases.py            ch07: eval cases loaded from the chaos scenarios
      judge.py            ch07: EmbeddingJudge (offline) + validation, optional LLMJudge
      harness.py          ch07: trajectory + step scoring across 3 dimensions
      gate.py             ch08: baseline, noise-aware gate, rolling adopt, override, prod sampling
    observability/
      tracing.py          ch09: OpenTelemetry spans to Tempo, best-effort
      drift.py            ch09: two-family drift (agent behavior + environment signals)
    cost.py               ch10: cost model, profiling, caching/routing, token budget
    cli.py                command line
  tests/
    test_resume.py        ch04: crash/resume + idempotency
    test_memory.py        ch05: recall, idempotent writes, staleness
    test_conversation.py  ch05: regenerate-from-task-state survives eviction
    test_tools_contract.py ch06: fake-backend contract tests (every commit)
    test_tools_real.py    ch06: real-upstream contract tests (catch drift)
    test_evals.py         ch07: case loading, judge validation, safety, smoke
    test_gate.py          ch08: gate decisions, noise band, rolling baseline, override
    test_observability.py ch09: tracing no-op safety, drift classification
    test_cost.py          ch10: caching/routing savings, budget early-wrapup
```

## The six tools (ch06)

| Tool | Upstream | Drift defense | Failure behavior |
|------|----------|---------------|------------------|
| `promql_query` | Prometheus | schema-validated | partial / clean failure |
| `log_search` | Loki | schema-validated | partial / clean failure |
| `trace_lookup` | Tempo (empty until ch09) | schema-validated | clean failure |
| `deploy_history` | deploy ledger | schema-validated | clean failure |
| `runbook_search` | runbooks dir | schema-validated | clean failure |
| `scoped_kubectl` | cluster | schema + allowlist | refuses off-allowlist / forbidden |

Every tool goes through `defensive_call`: timeout, classify-and-retry, validate
the response shape **before** reading any field, then return an honest result.
The two flavors of contract test, fake-backend (every commit) and real-upstream
(catches actual drift), both ship. `make agent-tools` shows them all running,
including the allowlist refusing a forbidden `restart payments`.

## The three kinds of state (ch05)

| State | Where it lives | Source of truth? |
|-------|----------------|------------------|
| Task state (steps, hypothesis, actions) | Postgres durable log | Yes, authoritative |
| Conversation (the engineer-facing thread) | Redis, ephemeral | No, regenerated from task state |
| Long-term memory (past incidents) | Vector store (Postgres-backed here) | No, external reference |

The discipline: task state is the only source of truth. The conversation is
derived from it, so on recovery it is rebuilt rather than trusted, which is the
defense against the drift failure. Long-term memory is an external reference,
queried fresh, and it enriches context without ever overriding current signals.
Every memory carries the date and service version it applied to; a memory from a
different version is flagged stale.

## Run it

The agent runs from a local Python venv against the environment's exposed ports,
so the environment must be up first.

```
make up            # from the repo root: start the synthetic environment
make agent-setup   # create the agent venv and install deps
make agent-init    # create the agent database and schema
make agent-demo    # the ch04 showcase: crash mid-run, then resume
make agent-memory  # the ch05 showcase: recall a past incident, staleness, conversation
make agent-tools   # the ch06 showcase: the six tools + allowlist enforcement
make agent-eval    # the ch07 eval harness (RUNS=N for multiple runs per scenario)
make agent-gate-demo # the ch08 deployment-gate showcase (baseline, block, override)
make agent-trace     # the ch09 trace showcase (run an incident, confirm it reached Tempo)
make agent-drift-demo # the ch09 drift showcase (silent failure caught by env drift)
make agent-cost      # the ch10 cost showcase (caching, routing, token budget)
make agent-test    # the full suite (durability, memory, contract, eval, gate, obs, cost)
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

The tool layer is now defensive (ch06), but `scoped_kubectl` writes stay
simulated and shadow-only until the chapter 12 rollout work, so nothing in the
real environment is mutated yet. As of ch09 the services emit OpenTelemetry
traces, so `trace_lookup` returns real service traces. The memory embedder is a
deterministic local one so the demo runs offline; swap in a real embedding model
and vector DB for production.

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `AGENT_DSN` | `postgresql://postgres:dev@localhost:5432/agent` | the durable log + memory |
| `REDIS_URL` | `redis://localhost:6379/0` | conversation state (best-effort) |
| `PROM_URL` | `http://localhost:9090` | Prometheus for tool queries |
| `AGENT_PLANNER` | `scripted` | set to `llm` for the Anthropic planner |
| `AGENT_SCOPE` | `agent/scope.yaml` | scope config path |
