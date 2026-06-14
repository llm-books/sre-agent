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
