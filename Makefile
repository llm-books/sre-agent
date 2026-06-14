SERVICES = web api-gateway orders payments inventory notifications
SPEED ?= 60
COMPOSE = docker compose

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

.PHONY: up
up: ## Build and start the synthetic environment
	$(COMPOSE) up -d --build
	@echo
	@$(MAKE) --no-print-directory urls

.PHONY: down
down: ## Stop the environment (keeps volumes)
	$(COMPOSE) down

.PHONY: nuke
nuke: ## Stop and remove everything including volumes
	$(COMPOSE) down -v

.PHONY: reset
reset: ## Clear all injected faults and restart the services (fast, < 30s)
	-$(COMPOSE) run --rm chaos clear-all
	$(COMPOSE) restart $(SERVICES)
	@echo "environment reset to clean state"

.PHONY: ps
ps: ## Show container status
	$(COMPOSE) ps

.PHONY: logs
logs: ## Tail logs from the six services
	$(COMPOSE) logs -f $(SERVICES)

.PHONY: urls
urls: ## Print the dashboard URLs
	@echo "Grafana:     http://localhost:3000  (anonymous admin)"
	@echo "Prometheus:  http://localhost:9090"
	@echo "Tempo:       http://localhost:3200  (empty until the ch09 build)"
	@echo "web service: http://localhost:8081/checkout"

.PHONY: smoke
smoke: ## Quick check that the services respond
	@for p in 8081 8082 8083 8084 8085 8086; do \
		printf "localhost:%s/healthz -> " $$p; \
		curl -s -o /dev/null -w "%{http_code}\n" http://localhost:$$p/healthz || echo "down"; \
	done

# ---- chaos ----------------------------------------------------------------

.PHONY: chaos-list
chaos-list: ## List available chaos scenarios
	$(COMPOSE) run --rm chaos list

.PHONY: chaos-inject
chaos-inject: ## Inject one scenario:  make chaos-inject NAME=orders-slow-query
	$(COMPOSE) run --rm chaos inject $(NAME)

.PHONY: chaos-clear
chaos-clear: ## Clear one scenario:   make chaos-clear NAME=orders-slow-query
	$(COMPOSE) run --rm chaos clear $(NAME)

.PHONY: chaos-clear-all
chaos-clear-all: ## Clear all injected faults
	$(COMPOSE) run --rm chaos clear-all

.PHONY: chaos-day
chaos-day: ## Run the full five-incident chaos day (override SPEED=NN)
	$(COMPOSE) run --rm chaos day --speed $(SPEED)

# ---- agent (ch04+) --------------------------------------------------------
# The agent runs from a local Python venv against the environment's exposed
# ports. It needs the environment up (make up) for Postgres and Prometheus.

VENV = agent/.venv
PY   = $(VENV)/bin/python
ALERT   ?= HighRequestLatency
SERVICE ?= orders
RUNS    ?= 1

$(VENV): ## Create the agent venv and install dependencies
	python3 -m venv $(VENV)
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -r agent/requirements.txt pytest

.PHONY: agent-setup
agent-setup: $(VENV) ## Set up the agent's Python venv

.PHONY: agent-init
agent-init: $(VENV) ## Create the agent database and schema
	cd agent && .venv/bin/python -m sre_agent init

.PHONY: agent-demo
agent-demo: $(VENV) ## Run the ch04 crash/resume showcase
	cd agent && .venv/bin/python -m sre_agent demo-crash

.PHONY: agent-memory
agent-memory: $(VENV) ## Run the ch05 memory + conversation showcase (needs Redis)
	cd agent && .venv/bin/python -m sre_agent demo-memory

.PHONY: agent-tools
agent-tools: $(VENV) ## Run the ch06 defensive tool-layer showcase
	cd agent && .venv/bin/python -m sre_agent demo-tools

.PHONY: agent-eval
agent-eval: $(VENV) ## Run the ch07 eval harness against the scenarios
	cd agent && .venv/bin/python -m sre_agent eval --runs $(RUNS)

.PHONY: agent-gate
agent-gate: $(VENV) ## Run the ch08 deployment gate (establishes a baseline, then blocks regressions)
	cd agent && .venv/bin/python -m sre_agent gate

.PHONY: agent-gate-demo
agent-gate-demo: $(VENV) ## Run the ch08 gate showcase (baseline, pass, regression block, override)
	cd agent && .venv/bin/python -m sre_agent demo-gate

.PHONY: agent-run
agent-run: $(VENV) ## Run one investigation: make agent-run ALERT=... SERVICE=...
	cd agent && .venv/bin/python -m sre_agent run --alert $(ALERT) --service $(SERVICE)

.PHONY: agent-test
agent-test: $(VENV) ## Run the agent's durability tests (needs the env up)
	cd agent && PYTHONPATH=. .venv/bin/python -m pytest tests/ -v
