"""The tool layer: six tools, each behind the defensive wrapper.

Six distinct jobs, no overlap, which is small enough that a model chooses among
them reliably. Each goes through `defensive_call`, so every one gets the timeout,
the failure classification and retries, the schema check before reading, and an
honest total-failure result. The one tool that can change the world,
`scoped_kubectl`, enforces its allowlist and the forbidden actions in the tool
itself, because the model can be convinced and the tool cannot.

Tracing tools query Tempo, which is empty until the ch09 build emits traces, so
`trace_lookup` will usually return zero traces. That is honest, not a bug.
"""
from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime
from pathlib import Path

from . import schemas
from .results import ToolResult
from .wrapper import PermanentError, defensive_call, http_get_json

PROM_URL = os.environ.get("PROM_URL", "http://localhost:9090")
LOKI_URL = os.environ.get("LOKI_URL", "http://localhost:3100")
TEMPO_URL = os.environ.get("TEMPO_URL", "http://localhost:3200")

_LEDGER = Path(__file__).resolve().parents[3] / "deploys.jsonl"
if os.environ.get("DEPLOY_LEDGER"):
    _LEDGER = Path(os.environ["DEPLOY_LEDGER"])

_RUNBOOKS = Path(__file__).resolve().parents[3] / "env" / "runbooks"
if os.environ.get("RUNBOOKS_DIR"):
    _RUNBOOKS = Path(os.environ["RUNBOOKS_DIR"])


# ---- promql_query ---------------------------------------------------------

def promql_query(args: dict) -> ToolResult:
    query = args["query"]
    op = lambda t: http_get_json(f"{PROM_URL}/api/v1/query", {"query": query}, t)
    res = defensive_call(op, schema=schemas.PROMQL, timeout=10)
    # Drop non-finite samples. A PromQL ratio over a zero denominator returns NaN,
    # and NaN/Inf are not valid JSON, so they would break the durable-log write.
    # An undefined ratio is honestly represented as "no sample", not as garbage.
    return res.map(lambda b: {
        "query": query,
        "samples": [
            {"labels": r.get("metric", {}), "value": float(r["value"][1])}
            for r in b["data"]["result"]
            if r.get("value") and math.isfinite(float(r["value"][1]))
        ],
    })


# ---- log_search -----------------------------------------------------------

def log_search(args: dict) -> ToolResult:
    service = args.get("service")
    query = args.get("query") or (f'{{service="{service}"}}' if service else '{job="docker"}')
    end = int(time.time() * 1e9)
    start = end - int(300 * 1e9)
    op = lambda t: http_get_json(
        f"{LOKI_URL}/loki/api/v1/query_range",
        {"query": query, "start": start, "end": end, "limit": 20, "direction": "backward"}, t)
    res = defensive_call(op, schema=schemas.LOKI, timeout=10)
    return res.map(lambda b: {
        "query": query,
        "streams": len(b["data"]["result"]),
        "lines": sum(len(s.get("values", [])) for s in b["data"]["result"]),
    })


# ---- trace_lookup ---------------------------------------------------------

def trace_lookup(args: dict) -> ToolResult:
    service = args.get("service", "")
    params = {"tags": f"service.name={service}" if service else "", "limit": 10}
    op = lambda t: http_get_json(f"{TEMPO_URL}/api/search", params, t)
    res = defensive_call(op, schema=schemas.TEMPO, timeout=10)
    return res.map(lambda b: {"traces": len(b.get("traces") or [])})


# ---- deploy_history -------------------------------------------------------

def fetch_deploys(service: str) -> dict:
    """Raw ledger read. Used by deploy_history (the tool) and by the orchestrator
    to learn a service's current version. Raises PermanentError if the ledger is
    missing, which the wrapper turns into a clean failure."""
    if not _LEDGER.exists():
        raise PermanentError(f"deploy ledger not found: {_LEDGER}")
    entries = [json.loads(line) for line in _LEDGER.read_text().splitlines() if line.strip()]
    if not entries:
        return {"service": service, "deploys": [], "recent": False}
    now = max(_ts(e["ts"]) for e in entries)
    svc = [e for e in entries if e.get("service") == service]
    recent, age = False, None
    if svc:
        latest = max(svc, key=lambda e: _ts(e["ts"]))
        age = round((now - _ts(latest["ts"])).total_seconds() / 3600.0, 1)
        recent = age <= 2.0
    return {"service": service, "deploys": svc, "recent": recent, "most_recent_age_hours": age}


def deploy_history(args: dict) -> ToolResult:
    service = args["service"]
    op = lambda t: fetch_deploys(service)
    return defensive_call(op, schema=schemas.DEPLOYS, timeout=5)


# ---- runbook_search -------------------------------------------------------

def _runbook_search(query: str) -> list:
    if not _RUNBOOKS.exists():
        raise PermanentError(f"runbooks dir not found: {_RUNBOOKS}")
    terms = [t for t in query.lower().split() if t]
    matches = []
    for path in sorted(_RUNBOOKS.glob("*.md")):
        text = path.read_text()
        low = text.lower()
        if any(term in low for term in terms):
            matches.append({"runbook": path.name, "snippet": text.strip().splitlines()[0][:120]})
    return matches


def runbook_search(args: dict) -> ToolResult:
    query = args.get("query", "")
    op = lambda t: _runbook_search(query)
    return defensive_call(op, schema=schemas.RUNBOOKS, timeout=5)


# ---- scoped_kubectl (the only tool that can change the world) --------------

READ_CMDS = {"get", "describe", "logs", "top"}
WRITE_CMDS = {"restart", "rollout-restart", "scale", "rollout-undo"}


def scoped_kubectl(args: dict) -> ToolResult:
    """Constrained command interface. The allowlist and the forbidden actions are
    enforced HERE, in the tool, regardless of what the agent concluded. Writes are
    gated behind approval; actual execution stays simulated until the ch12 rollout
    build, so at this checkpoint nothing is really mutated."""
    command = (args.get("command") or "").lower()
    target = (args.get("target") or "").lower()
    approved = bool(args.get("approved"))

    # Forbidden, enforced in the tool. These mirror scope.yaml's forbidden_actions.
    if "delete" in command:
        return ToolResult.failure("refused: delete is a forbidden destructive action")
    if target in ("all", "*"):
        return ToolResult.failure("refused: blind action across all services is forbidden")
    if command in WRITE_CMDS and "restart" in command.replace("rollout-", "") and target == "payments":
        return ToolResult.failure(
            "refused: restarting payments during provider slowness is a forbidden remediation")

    if command in READ_CMDS:
        return ToolResult.ok_({
            "command": f"kubectl {command} {target}", "executed": False,
            "note": "read command (autonomous); simulated at ch06",
        })
    if command in WRITE_CMDS:
        if not approved:
            return ToolResult.failure("requires approval (write actions are gated until ch12)")
        return ToolResult.ok_({
            "command": f"kubectl {command} {target}", "executed": False,
            "note": "write approved; execution gated until ch12",
        })
    return ToolResult.failure(f"refused: '{command}' is not in the allowlist")


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# The registry the executor dispatches through. Six tools, no overlap.
TOOLS = {
    "promql_query": promql_query,
    "log_search": log_search,
    "trace_lookup": trace_lookup,
    "deploy_history": deploy_history,
    "runbook_search": runbook_search,
    "scoped_kubectl": scoped_kubectl,
}
