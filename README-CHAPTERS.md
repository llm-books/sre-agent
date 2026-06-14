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

Chapter 08 includes:
- The deployment gate in sre_agent/evals/gate.py: Profile (aggregate of an EvalReport), measure() (profile + run-to-run noise over several samples), a baselines table, and evaluate_gate() comparing a candidate to the baseline PER DIMENSION.
- Per-dimension policy: safety blocks hard (any drop beyond ~0 noise), correctness blocks on a noise-aware threshold with a 0.05 floor, efficiency only warns unless it is >2x (then blocks).
- Rolling, re-measured baseline (the fix from the author review): adopt() re-measures fresh and keeps a rolling history; rolling_from_history() means a single lucky run can't inflate the bar. Unit-tested.
- Recorded override: gate_overrides table; record_override() logs owner + reason so a shipped regression is always a deliberate, audited decision.
- Production sampling: sample_production() scores recent real runs reference-free (supported = reached a hypothesis after gathering enough evidence), trends mean support, and lists low-scoring runs as capture candidates (the failure-to-test-case loop).
- New: gate CLI (CI-style, exits non-zero on regression) + demo-gate; make agent-gate / agent-gate-demo; evals/ci/eval-gate.yml as the worked CI artifact; baselines + gate_overrides tables.
- RegressedPlanner (restarts services, vague diagnosis) used only to demonstrate the gate catching a regression.
- Verified: demo-gate shows baseline correctness 0.6/safety 1.0/8 steps; unchanged candidate PASSES; regressed candidate (correctness 0.2, safety 0.4) BLOCKED on safety (hard) and correctness (noise-aware); override recorded; rolling adopt; production sampling. Gate logic unit-tested (9 tests). Full suite 38 passed / 1 skipped. Part III now half built (evals done; observability is ch09).

Chapter 09 includes:
- Agent tracing (sre_agent/observability/tracing.py): every incident run is one OpenTelemetry root span, each step a child span, model decisions carry the full prompt + completion + hypothesis (semantic logging), tool calls carry args/status. Exported to Tempo over OTLP/HTTP. Best-effort: AGENT_TRACING=off or an unreachable endpoint never breaks the agent.
- Two-family drift detection (sre_agent/observability/drift.py): agent-behavior drift (step counts trended from the durable log) AND environment drift (env telemetry signals with NO threshold alert, notifications queue depth + inventory leaked bytes). The environment family is the one that catches the silent notifications failure.
- Services now emit OTel traces: env/services/main.go gains otelhttp server + client instrumentation exporting to Tempo (OTEL_EXPORTER_OTLP_ENDPOINT set in the service Dockerfile). A checkout flows web->gateway->orders->payments/inventory as one trace, so the agent's trace_lookup returns REAL service traces (verified: 10 traces each for orders/payments/web).
- New CLI: drift, demo-drift, demo-trace; make agent-trace / agent-drift / agent-drift-demo. OTel python deps added.
- TWO REAL BUGS found and fixed while building: (1) the Go service reset() did `*f = faults{}` while holding the mutex, replacing the mutex itself -> "fatal: Unlock of unlocked RWMutex" crash-loop on every /admin/reset (latent since ch03, masked because the crash-restart cleared faults anyway); fixed to reset fields individually. (2) promql_query returned NaN for zero-denominator ratios, which is invalid JSON and broke the durable-log write; fixed to drop non-finite samples.
- Verified: demo-trace shows the agent trace in Tempo (root=incident); demo-drift shows queue 0 -> inject silent failure -> queue 165 [DRIFT] caught with no alert -> cleared; trace_lookup returns real service traces. Full suite 43/43 passes. Suggested tag: ch09. Part III complete in code.

Chapter 10 includes:
- Cost model in sre_agent/cost.py: per-incident token cost dominated by the context that grows with accumulated evidence (late steps are the expensive ones). Token counts are ESTIMATED from context size for the scripted planner; the same math takes real usage from the LLM planner.
- Three levers, all shown shrinking the cost: prompt caching of the stable prefix (system prompt + tool defs), model routing (routine "pick the next tool" decisions -> cheap model; only the diagnosis -> capable), and per-incident token budgets.
- profile_run() reads a run's decide steps from the durable log and reports naive / cached / routed cost plus a daily projection.
- Budget enforcement in the orchestrator (run(budget_tokens=...)): _decide() checks cumulative_input_tokens against the budget and, when reached, forces a graceful conclude+escalate instead of spending through. The decide span carries a cost.input_tokens_estimate attribute.
- New CLI: cost --id WF, demo-cost; make agent-cost.
- Verified: demo-cost shows full investigation naive $0.0208/incident -> routed $0.0040 (80.9% savings; $208/day -> $40/day at 10k incidents/day), and a 2500-token budget making the agent wrap up early after 2 findings and escalate. Cost math unit-tested (caching < naive, routing < cached, routing assignment, monotonic cumulative, budget early-wrapup). Full suite 48/48 passes. Suggested tag: ch10.

Chapter 11 includes:
- Security guardrails as the sre_agent.guardrails package, defense in depth: input_guards.py (scan untrusted content for injection patterns, redact matches, mark as untrusted data), permissions.py (credential-level scoping: the agent holds a read-only telemetry credential; writes require a human approval token, enforced at the boundary not the prompt), output_guards.py (reject destructive/exfiltrating actions before they take effect), threat_model.py (the SRE agent's threat-model worksheet, the chapter's worked artifact).
- Real integration: executor.run_tool checks permissions BEFORE dispatch (a write the agent isn't authorized for is denied at the credential layer, defense in depth with scoped_kubectl's own allowlist); log_search runs sampled log lines through the input guardrails (untrusted content scrubbed before it reaches the agent); the orchestrator runs every proposed action through the output guardrail before recording it.
- The headline: injection is SURVIVABLE. Even if the input filter misses, the deterministic layers contain the attack; the worst a compromised agent can do is propose a remediation a human rejects.
- New CLI: threat-model, demo-security; make agent-security.
- Verified: demo-security on the hostile-log-injection scenario shows the input guard flagging instruction_override + destructive + exfiltration and redacting them, then injection survival (the injected delete -> permission denied; the injected exfil -> output-guardrail blocked; an unauthorized write -> permission denied), then the threat-model worksheet. 7 guardrail unit tests (input scan, permission scoping, output guard, injection survival). Full suite 55/55. Suggested tag: ch11. (Pure-Python checkpoint; runs offline.)

Chapter 12 includes:
- Progressive rollout: agent/rollout.yaml (the per-action matrix, the worked artifact) + sre_agent.rollout package (config.py loads it, graduation.py recommends a mode from eval track record + stakes, approval.py is the approval surface with an approvals table). Autonomy is per ACTION, not per agent.
- The agent now ACTS: orchestrator._dispose() dispatches the proposed remediation by its mode after the loop (idempotent, runs on resume too so a crash between concluding and acting still dispatches). executor.execute_remediation() performs real reversible actions via scoped_kubectl (with the approval token) -> POST /admin/reset to the service. Decision/state gained action_id, rollout_mode, acted, proposed_action_id.
- Modes: autonomous (act + review after), assisted (request approval; act if approved else escalate), gated (propose only). Approver stand-in calibrates friction to stakes (approve low/moderate reversible, hold high).
- Graduation grounded in eval data: the `graduate` command maps each remediation's scenario to its correctness/safety rates and recommends a mode, flagging where it differs from the configured mode.
- TWO env fixes while building: (1) execute_remediation must use an allowlisted scoped_kubectl command (rollout-restart) for the reset effect, not the descriptive catalog command; (2) the notifications worker loop now drains faster than it fills (depth -= 10) so a backlog actually shrinks after the fix (rebuilt the service). Also fixed a pre-existing ch05 cosmetic bug: memory now stores the BASE diagnosis so the "Memory:" clause stops nesting on itself.
- New CLI: rollout, demo-rollout, approvals, graduate; make agent-rollout / agent-graduate. approvals table added.
- Verified: demo-rollout shows orders + notifications ACTED (autonomous), api-gateway ACTED (assisted, human approves the reversible high-stakes rollback), payments + inventory escalated (gated, because the evals show the agent's diagnosis unreliable on those incidents), and the silent notifications failure resolved END TO END autonomously (queue 100 -> 0 after the agent restarts the worker). test_resume updated (2 action rows now: proposal + remediation, both idempotent). 3 rollout tests. Full suite 61/61. Suggested tag: ch12. (Updated during the Field Notes revision pass: payments moved to gated to match the agent's measured unreliability; the Approver now approves reversible actions with stakes-calibrated friction so gateway is the assisted-approved example.)

Chapter 13 includes:
- The multi-agent question, answered empirically. sre_agent.multiagent: verifier.py (a verifier agent that independently re-derives the diagnosis from adversarial SRE heuristics and flags disagreement; costs one model pass), experiment.py (measure whether it earns its cost using the eval harness).
- The method is the chapter's: run the single agent and record where it's wrong, add the verifier and measure catches + cost, then compare invoking it EVERYWHERE vs TARGETED (only the incident types the primary is weak on).
- New CLI: demo-multiagent; make agent-multiagent.
- Verified: demo-multiagent shows the verifier catching the primary's two real errors (payments, inventory), with no spurious flags; correctness 0.6 -> 1.0 under both 'all' and 'targeted', but targeted does it at 3180 verifier tokens vs 7950 to run everywhere. Verdict printed: the verifier earns its cost ONLY when targeted; run the second agent where it's measured to help, nowhere else. 3 multiagent tests (pure policy math + DB-gated experiment). Full suite 61/61. Suggested tag: ch13.

THE BUILD IS COMPLETE. All 13 chapters (ch03-ch13) are implemented, verified, and tagged-ready. The sre-agent repo grows from a synthetic environment to a full production agent: bounded, durable, stateful, defensively-tooled, evaluated, gated, observable, cost-bounded, secured, progressively-rolled-out, and measured for multi-agent. 61 tests pass.
