"""Observability tests: tracing is a safe no-op when it can't export, and drift
classification is correct. Drift classification is unit-tested by mocking the
PromQL tool, so it needs no environment."""
from __future__ import annotations

from sre_agent.executor.results import ToolResult
from sre_agent.observability import drift, tracing


def test_tracing_is_a_safe_noop():
    # Spans and attributes must never crash the agent, with or without an exporter.
    with tracing.span("unit-test-span", attr=1) as s:
        tracing.set_attr(s, "k", "v")
        tracing.set_attr(s, "obj", {"nested": [1, 2, 3]})
    tracing.set_attr(None, "k", "v")  # None span is fine
    tracing.flush()


def test_environment_drift_classifies(monkeypatch):
    def fake_promql(args):
        value = 150.0 if "worker_queue_depth" in args["query"] else 0.0
        return ToolResult.ok_({"samples": [{"labels": {}, "value": value}]})

    monkeypatch.setattr(drift, "promql_query", fake_promql)
    findings = {f.signal: f for f in drift.environment_drift()}
    # Queue depth 150 is well over the 20 normal_max -> drift, with no alert.
    assert findings["notifications.queue_depth"].status == "drift"
    # Leaked bytes 0 is within band -> ok.
    assert findings["inventory.leaked_bytes"].status == "ok"


def test_environment_drift_within_band_is_ok(monkeypatch):
    monkeypatch.setattr(
        drift, "promql_query",
        lambda args: ToolResult.ok_({"samples": [{"labels": {}, "value": 0.0}]}),
    )
    assert all(f.status == "ok" for f in drift.environment_drift())
