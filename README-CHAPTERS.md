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
