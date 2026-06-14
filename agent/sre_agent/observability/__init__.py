"""Observability (ch09): traces, semantic logs, and drift detection.

The agent emits an OpenTelemetry trace for every incident it handles, to the same
Tempo the services report to. Drift detection watches two streams: the agent's own
behavior, and the environment's telemetry (the signals with no threshold alert,
which is how the silent failure gets caught).
"""
