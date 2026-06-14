"""Multi-agent, measured (ch13).

The default is one well-built agent. A second agent has to earn the failure
surface it adds, and whether it does is an empirical question answered by the eval
harness, not an architectural preference. This package adds the one second agent
with the strongest case, a verifier, and measures whether it earns its cost. The
finding, as the chapter predicts, is that it earns it narrowly: on the hard
incidents where the primary is weak, not everywhere.
"""
