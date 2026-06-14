"""Postgres connection and schema bootstrap for the durable log.

The agent keeps its workflow state in its own `agent` database inside the same
Postgres instance the synthetic environment already runs. Three tables:

  workflows    one row per incident the agent is handling
  steps        the durable log: one row per recorded step, the source of truth
  actions      a dedup table keyed by idempotency key, the double-charge defense

The `steps` table is what makes execution durable. Every completed step is
recorded here before the next one begins, so a crashed worker resumes by reading
this log rather than re-running anything.
"""
from __future__ import annotations

import os

import psycopg
from psycopg.rows import dict_row

# Default points at the synthetic environment's Postgres (see docker-compose).
# Override with AGENT_DSN to point elsewhere.
DEFAULT_DSN = "postgresql://postgres:dev@localhost:5432/agent"
ADMIN_DSN = "postgresql://postgres:dev@localhost:5432/postgres"

SCHEMA = """
CREATE TABLE IF NOT EXISTS workflows (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    input       JSONB NOT NULL,
    status      TEXT NOT NULL DEFAULT 'running',   -- running | done | failed
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS steps (
    workflow_id TEXT NOT NULL REFERENCES workflows(id),
    step_index  INT  NOT NULL,
    kind        TEXT NOT NULL,                      -- decide | tool | action
    request     JSONB NOT NULL,
    result      JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workflow_id, step_index)
);

-- The idempotency dedup table. A side-effecting action records itself here under
-- a stable key BEFORE (or as) it acts. A retry with the same key conflicts and
-- does nothing, so the action happens at most once no matter how many retries.
CREATE TABLE IF NOT EXISTS actions (
    idempotency_key TEXT PRIMARY KEY,
    workflow_id     TEXT NOT NULL,
    step_index      INT  NOT NULL,
    action          JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Long-term memory (ch05): a vector store of past incidents, keyed by service
-- and symptom. One row per incident (workflow_id is unique) so re-running an
-- investigation cannot duplicate its memory. The embedding is stored as a JSON
-- array; similarity is computed in the store. occurred_at and service_version
-- are the staleness metadata the agent weighs against current telemetry.
CREATE TABLE IF NOT EXISTS memory (
    id              BIGSERIAL PRIMARY KEY,
    workflow_id     TEXT UNIQUE,
    service         TEXT NOT NULL,
    symptom         TEXT NOT NULL,
    embedding       JSONB NOT NULL,
    root_cause      TEXT,
    remediation     TEXT,
    service_version TEXT,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_memory_service ON memory (service);

-- Deployment gate (ch08). The baseline is the deployed agent's eval profile, kept
-- as a rolling history so no single lucky measurement can inflate the bar. The
-- override table records every deliberate ship-past-a-red-gate, with an owner and
-- a reason, so a shipped regression is always a decision someone made on the record.
CREATE TABLE IF NOT EXISTS baselines (
    name        TEXT PRIMARY KEY,
    profile     JSONB NOT NULL,            -- the rolling baseline profile
    noise       JSONB NOT NULL,            -- per-dimension run-to-run stddev
    history     JSONB NOT NULL,            -- recent measured profiles (rolling window)
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS gate_overrides (
    id          BIGSERIAL PRIMARY KEY,
    baseline    TEXT NOT NULL,
    owner       TEXT NOT NULL,
    reason      TEXT NOT NULL,
    candidate   JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def dsn() -> str:
    return os.environ.get("AGENT_DSN", DEFAULT_DSN)


def admin_dsn() -> str:
    return os.environ.get("AGENT_ADMIN_DSN", ADMIN_DSN)


def ensure_database() -> None:
    """Create the `agent` database if it does not exist.

    CREATE DATABASE cannot run inside a transaction, so this uses an autocommit
    connection to the maintenance database.
    """
    target = dsn().rsplit("/", 1)[-1]
    with psycopg.connect(admin_dsn(), autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (target,)
        ).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{target}"')


def connect() -> psycopg.Connection:
    return psycopg.connect(dsn(), row_factory=dict_row)


def bootstrap() -> None:
    """Idempotently ensure the database and tables exist."""
    ensure_database()
    with connect() as conn:
        conn.execute(SCHEMA)
        conn.commit()
