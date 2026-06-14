"""Agent tracing, to Tempo over OTLP/HTTP.

Every incident run becomes a root span, each step a child span, each model
decision and tool call its own span carrying the content that makes it
reconstructable. The discipline is what goes ON the span, not the instrumentation:
the full prompt and completion, not summaries, and the agent's hypothesis as a
first-class attribute, so a reasoning failure is diagnosable from the trace alone.

Tracing is best-effort. If the OTLP endpoint is unreachable, spans are still
created and silently dropped; the agent never breaks over its own telemetry. Set
AGENT_TRACING=off to disable entirely.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager

_TRACER = None
_DISABLED = os.environ.get("AGENT_TRACING", "on").lower() == "off"


def _init():
    global _TRACER
    if _TRACER is not None or _DISABLED:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        endpoint = os.environ.get("OTLP_ENDPOINT", "http://localhost:4318/v1/traces")
        provider = TracerProvider(resource=Resource.create({"service.name": "sre-agent"}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)
        _TRACER = trace.get_tracer("sre-agent")
    except Exception:
        _TRACER = None  # tracing is optional; never break the agent over it


def _trim(value, limit: int = 4000) -> str:
    s = value if isinstance(value, str) else json.dumps(value, default=str)
    return s[:limit]


@contextmanager
def span(name: str, **attrs):
    """A best-effort span context manager. Yields the span (or None if tracing is
    off) so callers can set more attributes."""
    _init()
    if _TRACER is None:
        yield None
        return
    with _TRACER.start_as_current_span(name) as s:
        for k, v in attrs.items():
            try:
                s.set_attribute(k, v if isinstance(v, (str, int, float, bool)) else _trim(v))
            except Exception:
                pass
        yield s


def set_attr(s, key: str, value) -> None:
    if s is None:
        return
    try:
        s.set_attribute(key, value if isinstance(value, (str, int, float, bool)) else _trim(value))
    except Exception:
        pass


def flush() -> None:
    """Force-export pending spans. Useful for short-lived CLI runs."""
    try:
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            provider.force_flush()
    except Exception:
        pass
