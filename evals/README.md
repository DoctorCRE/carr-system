# Behavior evaluations

Keep current regression cases, scorers, runners and baselines here. Run relevant
behavior tests when changing model behavior. Use measured comparisons when claiming
an improvement. Active product retrieval cases live in `retrieval/` and `retrieval-real-misses/`.

Steering evals (`/claude-api build-eval`, then `/claude-api hillclimb`):

- Each surface keeps its current baseline: `<surface>/evidence/baseline.jsonl`
  (rule-delivery also keeps `runs/baseline/` and `runs/v3/`, with
  `runs/trajectory/scores.tsv` as the score history).
- Run the surface's runner (`evals/rule-delivery/run_eval.py`, `evals/retrieval-real-misses/run_eval.py` or
  `evals/doctrine-semantic/run.py`; `--help` lists its modes) to produce a candidate
  and compare it with the baseline, for example
  `python3 evals/rule-delivery/run_eval.py --compare baseline v3`.
- Candidates, new runs and reports are generated output; they are gitignored.
  Promote a candidate to the new baseline only when the comparison shows an improvement.

Other reset jobs own the retained context-hook scorer (`context-hooks/score.py`).

For agent runs, keep network access explicit and start a fresh session when enabling it.
Use the provider's native sandbox and network controls.
