# agent/

The SRE agent. It grows one component per chapter; see `../README-CHAPTERS.md`
for the checkpoint map. The synthetic environment in `../env/` is fixed; only
this directory and `../evals/` change across chapters.

At this scaffold point, before the chapter 4 build, the only thing here is the
scope configuration from chapter 3, because scope is pure config and can be
written before any executing code exists. Everything else arrives on its chapter:

| Lands in | Directory (added on its chapter) |
|----------|----------------------------------|
| Orchestrator and executor | `orchestrator/`, `executor/` (ch04) |
| State and memory | `state/` (ch05) |
| Tool layer | `tools/` (ch06) |
| Observability | `observability/` (ch09) |
| Guardrails | `guardrails/` (ch11) |
| Rollout and gating | `rollout/` (ch12) |

The eight components from chapter 2 map onto these directories. The scope config
below is the boundary every later component enforces a piece of.

## scope.yaml

`scope.yaml` is the chapter 3 deliverable: the agent's boundary as configuration
it reads at startup. It is the worked scope-reduction worksheet from the chapter,
in machine-readable form. Later components enforce it: the tool layer (ch06)
refuses forbidden actions, the rollout layer (ch12) graduates actions out of the
gated set, and the guardrails (ch11) scope permissions to the in-scope services.
