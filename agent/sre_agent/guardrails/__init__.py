"""Security guardrails (ch11): defense in depth around the agent.

No single layer stops every injection. Input guardrails reduce the rate (a
probabilistic first layer); permission scoping and action gates contain the
damage when injection slips through (the deterministic layers that hold against
attacks nobody anticipated). Together they make a successful injection survivable:
the worst a compromised agent can do is propose a remediation a human rejects.
"""
