"""Permission scoping: the deterministic layer that bounds the blast radius.

The agent is a constrained principal, not the user. It holds a read-only
telemetry credential. Read tools run autonomously; anything that would change the
world requires an approval token threaded in from a human. Enforced HERE, at the
credential boundary, regardless of what the agent (or an injection) concludes,
which is why it holds against attacks the input filter never anticipated. This is
defense in depth with the tool-level allowlist in scoped_kubectl.
"""
from __future__ import annotations

READ_TELEMETRY = "read_telemetry"
WRITE_REMEDIATION = "write_remediation"

# What each tool requires. All six investigative tools are read-scoped; the only
# tool that can change the world, scoped_kubectl, requires write permission for
# its write commands (its read commands stay read-scoped).
TOOL_PERMISSION = {
    "promql_query": READ_TELEMETRY,
    "log_search": READ_TELEMETRY,
    "trace_lookup": READ_TELEMETRY,
    "deploy_history": READ_TELEMETRY,
    "runbook_search": READ_TELEMETRY,
}

WRITE_COMMANDS = {"restart", "rollout-restart", "scale", "rollout-undo", "delete"}

# The agent's autonomously granted credential. Note: NOT write.
GRANTED = frozenset({READ_TELEMETRY})


def required_permission(tool: str, args: dict) -> str:
    if tool == "scoped_kubectl":
        cmd = (args.get("command") or "").lower()
        return WRITE_REMEDIATION if cmd in WRITE_COMMANDS else READ_TELEMETRY
    return TOOL_PERMISSION.get(tool, WRITE_REMEDIATION)  # unknown tools default to write-scoped


def check(tool: str, args: dict) -> tuple[bool, str]:
    """Return (allowed, reason). A write requires an approval token in args; the
    credential alone never grants it."""
    needed = required_permission(tool, args)
    if needed in GRANTED:
        return True, "read-scoped"
    if args.get("approved"):
        return True, "write approved by human"
    return False, f"credential is read-only; {needed} requires human approval"
