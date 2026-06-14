# Optional: run the agent as a container on the compose network instead of from a
# local venv. Build context is the repo root so the agent can see scope.yaml and
# the deploy ledger. Run with the AGENT_DSN and PROM_URL pointing at the compose
# service names (postgres:5432, prometheus:9090).
FROM python:3.12-slim
WORKDIR /app
COPY agent/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY agent/sre_agent ./sre_agent
COPY agent/scope.yaml ./scope.yaml
COPY deploys.jsonl ./deploys.jsonl
ENV AGENT_DSN=postgresql://postgres:dev@postgres:5432/agent \
    AGENT_ADMIN_DSN=postgresql://postgres:dev@postgres:5432/postgres \
    PROM_URL=http://prometheus:9090 \
    REDIS_URL=redis://redis:6379/0 \
    AGENT_SCOPE=/app/scope.yaml \
    DEPLOY_LEDGER=/app/deploys.jsonl
ENTRYPOINT ["python", "-m", "sre_agent"]
CMD ["demo-crash"]
