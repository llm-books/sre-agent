# Chaos engine

Injects faults into the synthetic environment by POSTing to each service's
`/admin/fault` endpoint. It reads the scenario files in `../scenarios`, so the
faults it injects and the ground truth the eval harness scores against come from
the same source.

## Run it

Through the Makefile (recommended, runs inside the compose network):

```
make chaos-list            # show available scenarios
make chaos-inject NAME=orders-slow-query
make chaos-clear  NAME=orders-slow-query
make chaos-day             # run the full five-incident chaos day, compressed
```

Directly, from the host, against the published ports:

```
SERVICE_BASE='http://localhost:{target_port}' python env/chaos/chaos.py list
```

(Inside the network the engine uses `http://<service>:8080`; from the host you
would point it at the mapped localhost ports. The Makefile targets use the
in-network form.)

## The compressed clock

The chaos day spans an eight-hour narrative window. Each scenario carries an
`at_minute` offset and a `clock` time. The `day` command compresses the offsets
with `--speed` (default 60, so one narrative minute passes in one second and the
whole day runs in about a minute). Raise or lower the speed to slow the day down
for a live demo or speed it up for a quick smoke test.

## How a fault reaches a service

Every fault in a scenario's `inject.fault` block is posted verbatim to the
target service's `/admin/fault` endpoint. The service applies it live, with no
restart, and clears it on `/admin/reset`. Valid fault keys: `latency_millis`,
`error_rate`, `dependency_timeout`, `stop_processing`, `memory_leak`. See
`env/services/main.go` for what each one does.
