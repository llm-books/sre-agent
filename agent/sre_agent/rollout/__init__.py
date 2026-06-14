"""Progressive rollout (ch12): autonomy granted per action, not to the agent.

Each remediation has a mode (autonomous, assisted, gated) decided by its eval
track record and its stakes. The agent acts on what it has earned, proposes on
what it's earning, and only escalates what it hasn't. The rare wrong action lands
on a graduated, reversible, low-stakes remediation, so it dents trust rather than
destroying it.
"""
