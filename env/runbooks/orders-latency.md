# Runbook: orders service latency

Quality: good, kept current.

## Symptom
p99 latency on the orders service climbs while other services stay flat.

## First checks
1. Confirm the latency is isolated to orders, not shared infrastructure. Compare
   p99 across services.
2. Check the deploy ledger for a recent orders deploy. A code change is the most
   common cause.
3. If there is no recent deploy, suspect the database: a slow query or a missing
   or unused index.

## Known causes
- Missing index on `orders(created_at)`. A scan instead of an index lookup turns
  a sub-millisecond query into seconds. Restore the index.
- A long-running migration holding locks.

## Remediation
Restore the missing index, or let the slow query finish if it is a one-off
migration. Restarting the service does not fix a slow query and is not advised.
