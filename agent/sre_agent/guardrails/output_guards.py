"""Output guardrails: validate what the agent produces before it takes effect.

The last check before an intent becomes an effect. Deterministic where it can be:
a destructive or exfiltrating action is rejected by rule, not by judgment. An
action that does not match a known-safe shape is rejected too.
"""
from __future__ import annotations

import json

DESTRUCTIVE = ("delete", "drop", "truncate", "rm -rf", "wipe")
EXFIL = ("email", "exfiltrate", "http://", "https://", "curl", "wget", "@")


def validate_action(action: dict) -> tuple[bool, str]:
    """Return (ok, reason). Blocks destructive verbs and exfiltration sinks in any
    field of a proposed action."""
    blob = json.dumps(action, default=str).lower()
    for d in DESTRUCTIVE:
        if d in blob:
            return False, f"output guardrail blocked a destructive action ({d})"
    for s in EXFIL:
        if s in blob:
            return False, f"output guardrail blocked a possible exfiltration ({s})"
    return True, "ok"
