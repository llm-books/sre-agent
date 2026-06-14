# sre-agent

<p align="center">
  <a href="https://www.llm-books.com/production-ai-agents">
    <img src="assets/Production-AI-Agents-Front-Cover.webp" alt="Production AI Agents — Front Cover" width="320">
  </a>
</p>

The companion repository for [*Production AI Agents: Building Systems That Survive
Real Users*](https://www.llm-books.com/production-ai-agents). It is two things:

1. A **synthetic production environment** you can run on a laptop: six
   microservices modeling an e-commerce checkout, a full telemetry stack, a load
   generator, and a chaos engine that injects realistic failures on a schedule.
2. The **SRE agent** that operates on that environment, built up one component
   per chapter, so the book's architecture is real code you can run, not a
   diagram you have to trust.

The agent watches for incidents, investigates with telemetry tools, proposes
diagnoses and remediations, and (once it has earned the trust, per chapter 12)
acts on them. The environment is the world it works on.

## Requirements

Just **Docker Desktop**. Go, k6, and the Python tooling all run inside
containers, so nothing else needs installing. Everything fits a 16GB laptop and
idles well under 6GB.

- Install Docker Desktop: https://www.docker.com/products/docker-desktop/
- About 3GB of disk for the images on first pull.
- Docker's default resource allocation is fine. If you have changed it, give
  Docker at least 4GB of memory (Docker Desktop, Settings, Resources).

## Quick start

**Step 0: start Docker Desktop and wait for it to be ready.** This is the step
people skip. The `make` commands talk to the Docker daemon, and the daemon only
runs while Docker Desktop is running. Open the Docker Desktop app (on macOS you
can also run `open -a Docker`), and wait until the whale icon in the menu bar
stops animating, or until this prints a version with no error:

```
docker info
```

If you run `make up` before Docker is ready, you will see
`Cannot connect to the Docker daemon`. That is not a bug in this repo; it just
means Docker Desktop is not running yet. Start it, wait, and try again. See
[Troubleshooting](#troubleshooting) below.

**Step 1: bring up the environment.**

```
make up          # build and start everything
make smoke       # confirm the six services respond (expect six 200s)
make urls        # print the dashboard links
```

The **first** `make up` pulls several images and builds the Go service, so it
can take a few minutes. Later runs start in seconds. If it looks like it is
hanging, it is almost certainly still pulling or building; watch progress in the
Docker Desktop dashboard or with `docker compose logs -f`.

**Step 2: watch and break things.** Open Grafana at http://localhost:3000
(anonymous admin) and watch the Services Overview dashboard. Then inject a
failure and watch it land:

```
make chaos-list
make chaos-inject NAME=orders-slow-query
# ... watch orders p99 climb in Grafana ...
make chaos-clear NAME=orders-slow-query
```

Or run the whole chaos day, five incidents compressed into about a minute:

```
make chaos-day            # or: make chaos-day SPEED=20  (slower, more watchable)
```

Tear down with `make down`, or `make nuke` to remove volumes too.

## Troubleshooting

**`Cannot connect to the Docker daemon at unix:///...docker.sock. Is the docker
daemon running?`**
Docker Desktop is not running. Start it, wait until `docker info` succeeds, then
re-run your command. Every `make` target here needs the daemon. This is the most
common first-run snag.

**The first `make up` seems stuck.**
It is pulling images (Postgres, Prometheus, Grafana, Loki, Tempo, k6) and
building the Go service with `go mod tidy`. On a first run over a normal
connection this is a few minutes, not seconds. Watch real progress with
`docker compose logs -f` in another terminal, or the Docker Desktop dashboard.

**`Bind for 0.0.0.0:3000 failed: port is already allocated`** (or 9090, 5432,
3100, 3200, 6379, 8081-8086).
Something else on your machine already uses that port. Stop the other process, or
change the host side of the port mapping in `docker-compose.yml` (the left number
in `"3000:3000"`) and `make up` again.

**The Go build fails resolving modules.**
The build runs `go mod tidy`, which needs network access to fetch the dependency
graph the first time. Check your connection and retry `make up`. Once built, the
image is cached.

**Grafana shows no data.**
Give it about 30 seconds after `make up` for the first Prometheus scrape, and
confirm load is running with `docker compose ps load`. If `load` exited, the
ramp finished; it restarts automatically, or run `docker compose up -d load`.

**No logs in Loki / Explore is empty.**
Logs are shipped by Promtail through the mounted Docker socket. This works on
Docker Desktop but logs are a convenience, not the core signal; metrics drive
most of the agent's work. If logs matter to you and are missing, check that the
`promtail` container is running.

**Tempo is empty.**
On purpose. The services start emitting traces in the chapter 9 build
(observability). Until then Tempo runs empty and waiting.

**I want a clean slate.**
`make reset` clears all injected faults and restarts the services in well under
30 seconds. `make nuke` tears everything down including volumes for a full
rebuild.

## What's here

```
sre-agent/
  env/                 the synthetic environment (fixed across chapters)
    services/          one configurable Go service, run as six instances
    telemetry/         prometheus, grafana, loki, promtail, tempo configs
    load/              k6 load generator
    chaos/             the chaos engine
    scenarios/         incidents as YAML; also the eval ground truth
    runbooks/          deliberately uneven runbooks
    initdb/            postgres schema
  agent/               the SRE agent (grows per chapter; scope.yaml is ch03)
  evals/               the eval harness (lands in ch07)
  deploys.jsonl        the deploy ledger the agent correlates against
  README-CHAPTERS.md   which git tag holds the agent at the end of each chapter
```

## Architecture

```mermaid
flowchart LR
  load[k6 load] --> web
  web --> gw[api-gateway]
  gw --> orders
  orders --> payments
  orders --> inventory
  notifications[notifications worker]

  subgraph telemetry
    prom[Prometheus]
    loki[Loki]
    tempo[Tempo]
    graf[Grafana]
  end

  web -.metrics/logs.-> telemetry
  orders -.-> telemetry
  payments -.-> telemetry
  inventory -.-> telemetry
  notifications -.-> telemetry

  chaos[chaos engine] -. /admin/fault .-> orders
  chaos -. /admin/fault .-> payments
  chaos -. /admin/fault .-> inventory
  chaos -. /admin/fault .-> notifications
```

Traffic enters at `web` and fans out through the dependency chain, so a fault in
one service shows up as a symptom in the services above it. The chaos engine
injects faults by calling each service's `/admin/fault` endpoint, no restart
required. The `notifications` worker drains a queue on a timer, which is what the
silent-failure scenario stalls.

## How the agent grows

Each chapter's Build section adds one component, tagged in git. See
[README-CHAPTERS.md](README-CHAPTERS.md) for the full map. The short version: the
boundary (ch03) comes first as `agent/scope.yaml`, then the orchestrator and
executor (ch04), state (ch05), tools (ch06), evals (ch07 to ch08), observability
(ch09), cost (ch10), security (ch11), and rollout (ch12), with a measured look at
multi-agent in ch13.

## Status

This is the scaffold. What runs today: the full environment, telemetry, load,
and chaos, plus the chapter 3 scope config. The agent's executing components land
on their chapters. Trace emission from the services is deferred to the chapter 9
build (observability), which is why Tempo starts empty.

## License

MIT. See [LICENSE](LICENSE).
