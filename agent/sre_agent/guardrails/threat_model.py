"""The SRE agent's threat model, as a worked worksheet (the chapter 11 artifact).

Inputs marked trusted/untrusted, actions rated by reversibility, permissions, data
flows, and the injection-survival check. This mirrors the worksheet the chapter's
Reader Action asks you to build for your own agent.
"""
from __future__ import annotations

WORKSHEET = {
    "untrusted_inputs": [
        ("log lines (carry customer-controlled fields)", "untrusted"),
        ("trace and metric content", "untrusted (external origin)"),
        ("the triggering alert", "semi-trusted (validate the shape)"),
        ("runbook text", "trusted (internal, reviewed)"),
    ],
    "actions": [
        ("read-only investigation", "no effect to reverse", "none", "ungated"),
        ("reversible remediation (timeout, index)", "reversible", "moderate", "gated"),
        ("irreversible or destructive action", "no", "high", "gated hard or forbidden"),
    ],
    "permissions": "read-only telemetry on the six known services, enforced in the credential, not the prompt; write only via the gated path",
    "data_flows": "reads telemetry and proposes remediations; no general external-send capability, so the obvious exfiltration path (read a secret, send it out) does not exist; residual leak risk into a proposal or a log is covered by output validation and redaction",
    "injection_survival": "assume a hostile log line fully compromises the agent: the worst it can do is propose a remediation a human rejects, because every consequential action is gated and the credential is read-only",
}


def render() -> str:
    w = WORKSHEET
    lines = ["Threat model: SRE agent\n", "Untrusted inputs:"]
    for inp, verdict in w["untrusted_inputs"]:
        lines.append(f"  - {inp:46} {verdict}")
    lines.append("\nActions (reversibility / stakes / treatment):")
    for action, rev, stakes, treat in w["actions"]:
        lines.append(f"  - {action:42} {rev:12} {stakes:9} {treat}")
    lines.append(f"\nPermissions:  {w['permissions']}")
    lines.append(f"Data flows:   {w['data_flows']}")
    lines.append(f"Injection survival:  {w['injection_survival']}")
    return "\n".join(lines)
