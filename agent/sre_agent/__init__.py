"""The SRE agent.

Grows one component per chapter. At the chapter 4 checkpoint this package
contains the durable orchestrator and executor: the control-flow mechanism that lets
the agent run a checkpointed multi-step investigation and survive a worker crash
without re-doing completed work or duplicating side effects.

Later chapters add state and memory (ch05), the defensive tool layer (ch06),
evals (ch07-08), observability (ch09), and so on.
"""
