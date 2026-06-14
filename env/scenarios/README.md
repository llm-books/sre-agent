# Scenarios

Each file here is one incident. A scenario does double duty: the chaos engine
reads it to inject a fault on a schedule, and the eval harness reads the same
file to score the agent, because the `correct_diagnosis`, `correct_remediation`,
and `forbidden_remediations` fields are the ground truth. One definition, two
consumers. That is what keeps the chaos day and the eval set in sync.

## The chaos day

Five incidents, mixed difficulty, in an eight-hour narrative window that the
chaos engine compresses into a short local run:

| File | Clock | Difficulty | The lesson |
|------|-------|------------|------------|
| `01-orders-slow-query.yaml` | 09:14 | easy | one service, one signal |
| `02-payments-provider-timeout.yaml` | 10:47 | medium | the surface symptom points the wrong way |
| `03-notifications-silent-failure.yaml` | 12:03 | hard | no alert fires; only drift catches it |
| `04-gateway-bad-config.yaml` | 14:31 | medium | correlate the symptom with a deploy |
| `05-inventory-leak-cascade.yaml` | 16:18 | hard | one root cause, symptoms across services |

## Other scenarios

- `06-hostile-log-injection.yaml` is a security test (chapter 11), driven by a
  fixture rather than the timed chaos engine.

## Schema

Required for every scenario: `name`, `difficulty`.

Timed chaos scenarios add:
- `clock`, `at_minute`, `duration_minutes`: when it fires.
- `inject.target`: the service to fault.
- `inject.fault`: the JSON object posted verbatim to that service's
  `/admin/fault` endpoint. Valid keys: `latency_millis`, `error_rate`,
  `dependency_timeout`, `stop_processing`, `memory_leak`.
- `expected_alerts`: the alert names that should fire (may be empty on purpose).

Eval ground truth (timed scenarios):
- `correct_diagnosis`, `correct_remediation`, `forbidden_remediations`.

Fixture scenarios add `type: fixture` and a `fixture` block, and replace the
ground-truth fields with `expected_behavior` and `must_not`.

## Adding a scenario from a production failure

The book's failure-to-test-case loop lives here. When the agent gets an incident
wrong, capture it as a new YAML in this directory with the correct outcome, and
it becomes both a chaos scenario you can replay and a permanent eval case the
deployment gate enforces.
