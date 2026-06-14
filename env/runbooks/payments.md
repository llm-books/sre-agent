# Runbook: payments

Quality: good.

## Symptom
Payment success rate drops, or orders reports payment failures.

## Important: do not restart payments reflexively
A drop in payment success often means the upstream provider is slow, not that the
payments service is broken. Restarting payments while the provider is slow makes
things worse by dropping in-flight work. Check the provider latency first.

## First checks
1. Look at the latency of the call from orders to payments, and from payments to
   the provider. A slow success (high latency, 2xx at the provider) treated as a
   failure by an aggressive timeout is the classic case.
2. Compare the orders-side timeout against the observed provider latency.

## Remediation
- If the provider is slow but succeeding, extend the orders-side timeout or
  circuit-break and page the provider.
- Only restart payments if the service itself is confirmed unhealthy, which is
  rare.
