# Chapter checkpoints

The agent in this repo grows one component per chapter. Each chapter's Build
section adds a layer, and each layer lives behind a git tag so you can check out
the agent as it stood at the end of any chapter.

The synthetic environment in `env/` does not change across chapters. It is the
fixed world the agent operates on. Only `agent/` and `evals/` grow.

## How checkpoints work

Each checkpoint is a git tag of the form `ch04`, `ch05`, and so on. To see the
agent as it stood at the end of a chapter:

```
git checkout ch07      # the agent with its eval harness, before deployment gates
make agent-run         # run it against the current environment
```

To return to the latest:

```
git checkout main
```

## Map

| Tag  | Chapter | What the checkpoint adds | The agent can now |
|------|---------|--------------------------|-------------------|
| `ch03` | 3. Designing for Capability and Boundary | Scope config: in-scope, frontier, out-of-scope, forbidden actions | State what it will and will not do |
| `ch04` | 4. Orchestration and Control Flow | Durable orchestrator and executor, checkpoint and replay | Run a checkpointed investigation, survive a worker crash |
| `ch05` | 5. State and Memory | Task state in Postgres, conversation in Redis, long-term memory in a vector store | Recover coherent state, recall past incidents on a service |
| `ch06` | 6. Tools and Integrations | Six defensive tool wrappers, schema validation, contract tests | Query metrics, logs, traces, deploys, runbooks through tested tools |
| `ch07` | 7. Building an Eval Harness | Step and trajectory evals scored against the chaos scenarios | Be scored on diagnosis correctness, safety, efficiency |
| `ch08` | 8. Evals as Deployment Gates | CI eval gate with baseline and thresholds, production sampling | Block its own deploys on a regression |
| `ch09` | 9. Observability and Tracing | OpenTelemetry tracing, semantic logs, drift detection. Services also gain trace emission here. | Reconstruct any run, surface drift before failures get loud |
| `ch10` | 10. Cost and Latency Engineering | Prompt-cache-friendly context assembly, model routing, per-incident budget | Diagnose under a token budget |
| `ch11` | 11. Security and Guardrails | Input guardrails, credential-level permission scoping, action gates | Resist injection from hostile logs, act only within scope |
| `ch12` | 12. Human-in-the-Loop and Rollout | Shadow, assisted, autonomous modes per action; approval surface | Graduate actions individually as they earn trust |
| `ch13` | 13. When Multi-Agent Earns Its Cost | A targeted verifier agent, invoked only on the hard incidents | Be measured for whether a second agent earns its cost |

## Field Notes (the chaos day)

The three Field Notes run the same chaos day against the agent at three
milestones. Reproduce them with:

```
git checkout ch06 && make chaos-day     # Field Notes 1: architecture complete
git checkout ch09 && make chaos-day     # Field Notes 2: proven and observable
git checkout ch12 && make chaos-day     # Field Notes 3: the shipped agent
```

The chaos day is five incidents in an eight-hour window, compressed for a local
run. See `env/scenarios/` for the incidents and `env/chaos/README.md` for how
the timeline is driven.

## Chapter Notes
Chapter 03 includes:
- Six-instance microservice topology (one configurable Go service) with live fault injection via /admin/fault
- Telemetry stack: Prometheus, Grafana, Loki, Promtail, Tempo
- k6 load generator and a Python chaos engine
- Five chaos-day scenarios plus a security scenario, doubling as eval ground truth; deliberately uneven runbooks; a deploy ledger
- agent/scope.yaml: the chapter 3 boundary as configuration
- Makefile, docker-compose, and getting-started + troubleshooting docs
- Verified: ```make up``` brings up all services, chaos injection moves the metrics (orders p95 0.08s -> 2.02s on the slow-query fault).

Chapter 04 includes:
- Durable orchestrator + executor under agent/sre_agent/, split so the orchestrator decides and records while the executor touches the world
- Homegrown durable engine: workflows + steps tables in a dedicated agent database, with get_or_record_step (replay a recorded step, or run it once and record it)
- Idempotency keys (workflow_id:step_index) on the one state-changing step, with an actions dedup table; the chapter-4 double-charge defense made concrete
- Pluggable planner: deterministic ScriptedPlanner (offline, no API key) and an optional Anthropic LLMPlanner whose decisions are recorded for replay
- CLI (python -m sre_agent) with run / list / show / resume / demo-crash, plus Makefile targets (agent-setup, agent-init, agent-demo, agent-run, agent-test)
- Verified: agent investigates the live env (orders latency 2.027s under the injected slow-query fault, correct "no recent deploy" diagnosis); demo-crash shows resume replays steps 0-3 and finishes with exactly one side-effect row; both durability tests pass.

Chapter 05 includes:
- The three kinds of state, made distinct. Task state stays in the durable log; conversation state moves to Redis; long-term memory becomes a vector store.
- Conversation state (sre_agent/conversation.py): ephemeral in Redis, regenerated from the task-state log on every run, so it can never drift from what actually happened. Best-effort: a Redis outage degrades narration but never breaks the investigation.
- Long-term memory (sre_agent/memory/): a vector store keyed by service and symptom. recall() filters by service then ranks by symptom similarity; one memory per incident (idempotent); each carries date + service_version for staleness. A LocalHashEmbedder keeps it offline; swap in a real embedder/vector DB for production.
- Orchestrator now recalls similar past incidents at the start, folds a relevant non-stale recollection into its hypothesis (never overriding current signals), and remembers each incident on conclude.
- New: demo-memory CLI + make agent-memory; recall and conversation inspection commands; memory table added to the schema; redis dependency.
- Verified: demo-memory shows a second incident recalling the first at similarity 1.0, the hypothesis carrying a Memory clause, the conversation regenerated from task state, and a version change flipping the recollection to stale. Full suite 6/6 (2 ch04 + 4 ch05) passes.

Chapter 06 includes:
- The defensive tool layer under sre_agent/executor/: results.py (ToolResult: ok/degraded/partial/failure), schemas.py (response-shape schemas, validate before reading), wrapper.py (defensive_call with timeout, transient/auth/permanent classification, retry with backoff, fallback, and a gather() helper for partial results), tools.py (the six tools, each behind the wrapper).
- Six tools, no overlap: promql_query (Prometheus), log_search (Loki), trace_lookup (Tempo, empty until ch09), deploy_history (ledger), runbook_search (runbooks dir), scoped_kubectl.
- scoped_kubectl enforces its allowlist and the forbidden actions IN THE TOOL: refuses delete, refuses restart of payments, refuses blind all-services actions, gates writes behind approval (execution still simulated until ch12), refuses anything off the allowlist.
- Both contract-test flavors: test_tools_contract.py (fake-backend, every commit: drift -> clean failure, transient -> retry, auth -> refresh-once, permanent -> no retry, exhausted -> fallback/degraded, gather -> partial) and test_tools_real.py (real Prometheus/Loki/ledger/runbooks shape checks, skip if env down).
- New: demo-tools CLI + make agent-tools.
- Verified: demo-tools shows all six tools returning ok against the live env, a bad PromQL query becoming a clean failure (not garbage), and scoped_kubectl refusing every forbidden case. Full suite 26/26 passes.

Chapter 07 includes:
- The eval harness as the sre_agent.evals package: cases.py (eval cases loaded from the chaos scenarios; the scenario file IS the eval case), judge.py (Judge interface, offline EmbeddingJudge that blends embedding cosine with content-word Dice, validated against a built-in human-labeled set; optional Anthropic LLMJudge via AGENT_JUDGE=llm), harness.py (trajectory + step scoring).
- Three dimensions scored SEPARATELY: outcome correctness (the judge, only where a judge is actually needed), safety (deterministic forbidden-action check, not a judge), efficiency (step count). Each scenario runs RUNS times and the harness reports rates.
- Step-level evals score a single decision in a frozen context (is the first investigative move sensible) to localize where trajectory failures happen.
- Judge validation gate: the harness reports the judge's agreement with human labels and flags it trustworthy/NEEDS WORK. The first cut of the embedding judge scored 0.6 (correctly flagged untrustworthy); blending in content-word overlap raised it to 1.0.
- Orchestrator gained use_memory flag (evals run with memory off so runs are independent).
- New: eval CLI + make agent-eval (RUNS=N); top-level evals/README points at the package.
- Verified against the live env: judge agreement 1.0 (trustworthy); per-scenario profile correctness 0.6 / safety 1.0 / 8 steps (orders, notifications, gateway pass; payments and inventory fail, the realistic mixed profile that sets up the ch08 deploy gate); all first moves sensible. Full suite 29 passed / 1 skipped (Loki real-upstream test skips when Loki is down).
