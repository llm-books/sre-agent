"""Basic tools the executor can call. READ-ONLY at this checkpoint.

These are deliberately minimal. Chapter 6 turns them into the defensive tool
layer: schema validation, timeouts, retries, fallbacks, partial results, contract
tests, and an allowlist on the one tool that can change the world. Here they are
just enough to run a real investigation against the live environment, so the
orchestrator and durable engine have something true to drive.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import requests

PROM_URL = os.environ.get("PROM_URL", "http://localhost:9090")

# The deploy ledger lives at the repo root.
_LEDGER = Path(__file__).resolve().parents[3] / "deploys.jsonl"
if os.environ.get("DEPLOY_LEDGER"):
    _LEDGER = Path(os.environ["DEPLOY_LEDGER"])


def promql_query(query: str) -> dict:
    """Run an instant PromQL query. Returns a simplified result."""
    resp = requests.get(
        f"{PROM_URL}/api/v1/query", params={"query": query}, timeout=10
    )
    resp.raise_for_status()
    body = resp.json()
    results = body.get("data", {}).get("result", [])
    # Flatten to (labels, value) pairs for easy reasoning.
    simplified = [
        {"labels": r.get("metric", {}), "value": float(r["value"][1])}
        for r in results
        if r.get("value")
    ]
    return {"query": query, "samples": simplified}


def deploy_history(service: str) -> dict:
    """Read the deploy ledger for a service and judge whether a deploy is recent.

    Recency is measured against the latest entry in the ledger, treated as 'now',
    so the static demo ledger still yields a meaningful recent/not-recent answer.
    """
    if not _LEDGER.exists():
        return {"service": service, "deploys": [], "recent": False}
    entries = [json.loads(line) for line in _LEDGER.read_text().splitlines() if line.strip()]
    if not entries:
        return {"service": service, "deploys": [], "recent": False}
    now = max(_ts(e["ts"]) for e in entries)
    svc_entries = [e for e in entries if e.get("service") == service]
    recent = False
    most_recent_age_hours = None
    if svc_entries:
        latest = max(svc_entries, key=lambda e: _ts(e["ts"]))
        age_h = (now - _ts(latest["ts"])).total_seconds() / 3600.0
        most_recent_age_hours = round(age_h, 1)
        recent = age_h <= 2.0  # within two hours of 'now' counts as correlated
    return {
        "service": service,
        "deploys": svc_entries,
        "recent": recent,
        "most_recent_age_hours": most_recent_age_hours,
    }


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# Registry the executor dispatches through.
TOOLS = {
    "promql_query": lambda args: promql_query(args["query"]),
    "deploy_history": lambda args: deploy_history(args["service"]),
}
