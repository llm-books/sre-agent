# evals/

The eval harness. Built in chapter 7 and wired into a deployment gate in chapter
8. It scores the agent against the scenarios in `../env/scenarios/`, using their
`correct_diagnosis`, `correct_remediation`, and `forbidden_remediations` fields
as ground truth.

This directory is a placeholder until the chapter 7 build. When it lands it will
hold:

- `harness.py`: runs the agent against each scenario, multiple times, and scores
  three dimensions separately: outcome correctness (LLM judge, validated),
  safety (set membership against forbidden actions, deterministic), and
  efficiency (steps and tokens, measured).
- `judge.py`: the LLM-as-judge for diagnosis equivalence, with its validation
  harness against human labels.
- `gate.py` (ch08): compares a candidate's profile to the baseline per dimension
  and blocks on a regression past threshold.
- `results/`: gitignored run outputs.

The harness runs against the tool layer's fake backends (chapter 6), so scoring
is deterministic and does not depend on live telemetry. The same scenario files
that drive the chaos engine drive the evals, which is what keeps the chaos day
and the eval set in sync.
