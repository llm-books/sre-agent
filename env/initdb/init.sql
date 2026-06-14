-- One Postgres instance, three logical databases, to keep the footprint small
-- while still giving orders, payments, and inventory their own stores. The book
-- describes per-service Postgres; a single instance with separate databases is
-- the lighter local equivalent and keeps us inside the 16GB budget.

CREATE DATABASE orders;
CREATE DATABASE payments;
CREATE DATABASE inventory;

\connect orders
CREATE TABLE IF NOT EXISTS orders (
    id          BIGSERIAL PRIMARY KEY,
    customer    TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Note: the chaos "slow query" scenario simulates the loss of this index.
CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders (created_at);

\connect payments
CREATE TABLE IF NOT EXISTS charges (
    id            BIGSERIAL PRIMARY KEY,
    order_id      BIGINT NOT NULL,
    amount_cents  INTEGER NOT NULL,
    idempotency_key TEXT UNIQUE,            -- the chapter-4 double-charge defense
    status        TEXT NOT NULL DEFAULT 'pending',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

\connect inventory
CREATE TABLE IF NOT EXISTS stock (
    sku       TEXT PRIMARY KEY,
    on_hand   INTEGER NOT NULL DEFAULT 0
);
INSERT INTO stock (sku, on_hand) VALUES
    ('widget-a', 1000),
    ('widget-b', 500),
    ('widget-c', 250)
ON CONFLICT (sku) DO NOTHING;
