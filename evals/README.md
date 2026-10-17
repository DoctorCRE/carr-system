# Behavior evaluations

Keep current regression cases, scorers, runners and baselines here. Run relevant
behavior tests when changing model behavior. Use measured comparisons when claiming
an improvement. Surfaces and their source paths are listed in `surfaces.json`.

Steering evals (`/claude-api build-eval`, then `/claude-api hillclimb`):

- Each surface keeps its current baseline: `<surface>/evidence/baseline.jsonl`
  (`retrieval-real-misses/baseline.json`; rule-delivery also `runs/baseline/` and
  `runs/v3/`, with `runs/trajectory/scores.tsv` as the score history).
- Run the surface's `run_eval.py` (`--help` lists its modes) to produce a candidate
  and compare it with the baseline, for example
  `python3 evals/rule-delivery/run_eval.py --compare baseline v3`.
  `session-instructions/run_eval.py` takes `build`, `run` or `report`.
- Candidates, new runs and reports are generated output; they are gitignored.
  Promote a candidate to the new baseline only when the comparison shows an improvement.
