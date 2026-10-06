# Rule-delivery eval

Measures the deterministic half of CARR rule delivery: which taught rules the
trigger and pack layer puts in front of a session at each prompt and each tool
call, with the Jev judgment switched off. It is the "skill triggering" case:
the score is coupled directly to the trigger text.

## What is replayed

| event | production code called |
|---|---|
| prompt | `ops/rule_trigger_delivery.advise` (compiled `prompt_regex` rows, always-on; ranking and binding stubbed to raise), then `lib/rule_delivery_preuse.semantic_delivery` |
| tool call | `hooks/rule-pack-preuse-reselection.py`: `_matches` (scheduled rail), `routed_rule_ids` (route rail, `ops/config/rule-routes.v1.json`), `matched_triggers` (compiled table), unioned as `process()` unions them and deduped per (rule, tool) inside a case |

Nothing is written to production logs or caches. `selftest.py` checks that.

## Inputs (all offline)

1. **Recorded traces.** `ops/fixtures/rule-delivery-eval/cases.v2.json`: 240
   paraphrased shapes of real session turns, subagent briefs, defects,
   notifications and log lines, each with dense hand-adjudicated gold. The
   repo's own split (30% held out per stratum) is kept.
2. **Recorded routine reads.** The `Read` calls in `ops/fixtures/real-replay/read-calls.jsonl`
   and recorded Bash commands whose segments are all read-only verbs target
   source/history paths with no review-time rule. Surface reads are positive
   review cases in `selftest.py`.
3. **Hand-judged hard cases**, `hard_cases.v1.json`: 33 cases, one sentence of
   justification each. 13 should-not-fire (routine reads, chatter), 7 near
   misses (trigger vocabulary without the action), 13 positives.

`split.json` freezes every case id to train or test before any edit;
`selftest.py` fails if it moves. `expectations.v1.json` freezes each case's
grading labels (expected and allowed rules, envelope or human cohort, input
hash). A change to the routes cannot move its own denominator: grading reads
the frozen labels, and a relabel is a new expectations version.

## Grader (programmatic)

* `expected` = gold, minus disputed, minus boot-delivered rules, restricted to
  rules the trigger layer owes (a trigger or path route, or pack-layer member).
  Hand-written cases carry their own `required` list.
* `recall` = delivered and expected over expected (micro over rules; macro over cases).
* `false delivery` = a delivered rule outside the case's full gold set.
* `tokens` = estimated tokens of the receipts the events would inject
  (receipt render replicated offline, chars / 4).

## Run it

```
python3 evals/rule-delivery/selftest.py                       # eval health checks
python3 evals/rule-delivery/run_eval.py --variant baseline --split all
python3 evals/rule-delivery/run_eval.py --compare baseline v3
python3 evals/rule-delivery/run_eval.py --verdict baseline v3 recall
python3 evals/rule-delivery/noise.py baseline
python3 evals/rule-delivery/explain.py                        # train split only
python3 evals/rule-delivery/make_report.py receipt --baseline-ref <sha>   # evidence/ + receipt.json
python3 evals/rule-delivery/make_report.py rounds             # runs/results.md (+ report.html with --builder)
python3 evals/rule-delivery/run_eval.py --freeze-expectations # only for a new expectations version
```

## The receipt

`make_report.py receipt` replays every case through the working tree
(candidate) and through the tree at `--baseline-ref` (baseline) with the same
harness, and writes the raw observations to `evidence/{baseline,candidate}.jsonl`.
`run_eval.score_receipt` grades both against `expectations.v1.json` and
computes each dimension on the test split of its cohort: per-arm case
bootstrap intervals and a paired-bootstrap delta over identical cohorts.
`receipt.json` binds the harness, every tracked file the candidate replay
read, the expectations and both cohorts by sha256; authored fields (change,
verdict, notes, critical flags) carry over from the current receipt.
`ops/check-eval-receipt.py` re-runs the scorer and refuses any difference.

The current round command and candidate verdict read the train split only.
Historical rounds v1-v3 used the test split to decide keep/revert; those test
intervals are descriptive, not untouched-holdout evidence. The frozen split
still protects future rounds. `explain.py` reads train only. The system is
deterministic, so run-to-run noise is zero; paired bootstrap intervals measure
case-sampling variation only.

## Not covered here

The Jev half (ranking and single-rule binding at `UserPromptSubmit`) needs a
live TypeSafe binding. `tools/rule-delivery-eval.py ... --jev live` measures it;
this eval leaves it alone.
