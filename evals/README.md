# Evals for LLM-steering changes

Any change to a file registered in `evals/surfaces.json` is a change to what a
model reads or which model runs. It ships with a measurement. In practice that
means running `/claude-api build-eval`, then `/claude-api hillclimb` (Claude
Code 2.1.284 or later), and committing the result as
`evals/<surface>/receipt.json` in the same pull request.

When a measurement genuinely cannot be taken, the pull-request body says so on
its own line:

```
no-eval: <surface>: <at least ten words on why a measurement is impossible>
```

"Trivial", "docs only" and "n/a" are refused. A line inside an HTML comment,
backtick or tilde code fence, indented code block, or blockquote does not count.
`ops/check-eval-receipt.py` enforces both paths
in CI (the `gates` class of `ops/ci.sh`). It reads the body from the
pull-request event, so edit the body before you push, or push again after.

## One evaluation system

CARR already has a standing evaluation ruling: a laddered, multidimensional
portfolio. Rungs run smoke, regression, hill_climb and launch. Results are
named dimensions with no blended score, bound to user-job stages and to the
model, harness and adapter that produced them, and a critical-dimension
regression blocks no matter what else improved. It lives in
`tools/room-bridge/evaluation_kernel.py`, with its adapter fields in
`tools/room-bridge/execution_contract.py`, its schema in
`control-room/contracts/carr-evaluation-kernel.v1.schema.json`, and its
synthetic fixture in
`control-room/contracts/fixtures/execution-fabric/carr-evaluation-kernel.synthetic.v1.json`.

A receipt is a small projection of that portfolio. The check imports the
kernel's rungs, dimension fields and stage fields, and calls its
`critical_dimension_blockers()`. Offline suites bind through the existing
provider-neutral kernel `ops/ai_eval.py` and its suites under `evals/ai/`.
Nothing here is a second evaluation system; extend the kernel, not this file.

## The procedure

1. **Name one flow and one surface.** One eval per flow. If a change touches
   two surfaces, each gets its own receipt or its own no-eval line.
2. **Start from production traces and human-judged hard cases.** Pull real
   inputs first: Jev call receipts, session transcripts, rule-delivery misses,
   the cases someone complained about. Hand-written and synthesized cases are
   seeds, never the whole set. A receipt must list `production_trace` or
   `human_judged_hard_case` among its sources.
3. **Include should-not-fire cases.** A rule that should stay quiet, a
   judgment that should answer "no", a route that should not escalate. Without
   them an eval rewards firing on everything. At least one is required, and
   more is better.
4. **Pick the cheapest grader that measures the real property.** Programmatic
   checks first, then a pairwise blind comparison, then a pointwise rubric,
   then human spot-checks. Cheaper is not better when the property needs
   judgment.
5. **Grade twice before you trust the grader.** Run it over the same outputs
   twice and record agreement. Push an oracle (should score about 100%) and a
   null answer (should score about 0%) through runner and grader together. A
   grader that fails this cannot put its name on a "ship".
6. **Make the noise smaller than the smallest useful gain.** For a pass rate
   the 95% interval half-width is roughly `1/sqrt(cases x repeats)`: 25 test
   cases at 2 repeats is about 14 points. Decide the smallest gain you would act
   on, then size cases and repeats so the noise floor sits below it. If it
   cannot, the receipt can report but cannot ship.
7. **Seal the test split.** Draw train and test at random, stratified by the
   first tag. Only train transcripts are read while proposing changes. The test
   split is scored every round and is the headline, and nobody opens its
   transcripts.
8. **One attributable change per round.** Each round changes one thing, names
   it in `change`, and can be tied to a behaviour that moved. If train rose and
   test did not, revert.
9. **Never paste failures into prompts.** Fix the behaviour the failing cases
   share. Copying a failing case's text into the prompt is overfitting with
   extra steps, and the sealed split exists to catch it.
10. **After two stalled rounds, bucket the failures.** Sort what is left into
    artifact gap, grader disagreement, structural, and variance before spending
    another round. Most stalls are not artifact gaps.
11. **Report test against baseline, per dimension, with confidence
    intervals.** Every dimension gets its own baseline, candidate and paired
    delta interval. A critical dimension that fails or regresses blocks, even
    if the primary dimension and any overall number rose. When the primary
    delta interval contains zero the verdict says "do not merge on quality
    grounds".
12. **Hold cost at parity as a standing objective.** Record dollars per case
    for baseline and candidate on every receipt. A change that is quality-flat
    but cheaper may ship as `ship_cost_at_parity`; its statement still says do
    not merge on quality grounds.
13. **Record where it ran.** Model, harness and adapter go in `adapter`, in
    the execution contract's own fields, with a configuration fingerprint.
    A number with no model attached is not evidence.

## The receipt

`evals/<surface>/receipt.json`, changed in the same pull request as the
surface. A receipt carried over from an earlier change does not count.

```json
{
  "schema_version": 2,
  "surface": "jev-judgments",
  "change": "one sentence naming the single change measured",
  "measured_on": "2026-09-29",
  "rung": "hill_climb",
  "adapter": {
    "surface": "claude_code_cli", "adapter_id": "...", "adapter_version": "...",
    "harness_id": "...", "harness_version": "...", "provider_id": "...",
    "model_id": "...", "native_session_ref": "...",
    "configuration_fingerprint": "sha256:<64 hex>"
  },
  "cases": {"total": 80, "train": 40, "test": 40, "should_not_fire": 16,
            "sources": ["production_trace", "human_judged_hard_case"]},
  "split": {"method": "random, stratified by first tag", "seed": 11, "sealed_test": true},
  "repeats": 3,
  "grader": {"kind": "programmatic",
             "validation": {"graded_twice": true, "agreement": 0.98,
                            "oracle_pass_rate": 1.0, "null_pass_rate": 0.0, "result": "pass"}},
  "noise_floor": 0.06,
  "min_useful_gain": 0.10,
  "primary_dimension": "correct-judgment",
  "dimensions": [
    {"dimension_id": "correct-judgment", "critical": true, "status": "passed",
     "direction_vs_baseline": "improved", "evidence_refs": ["..."],
     "baseline": {"score": 0.62, "ci_low": 0.55, "ci_high": 0.69},
     "candidate": {"score": 0.81, "ci_low": 0.75, "ci_high": 0.87},
     "delta": {"value": 0.19, "ci_low": 0.12, "ci_high": 0.26}}
  ],
  "stage_results": [
    {"stage_id": "judgment", "status": "passed",
     "dimension_ids": ["correct-judgment"], "evidence_refs": ["..."]}
  ],
  "cost": {"baseline_usd_per_case": 0.0041, "candidate_usd_per_case": 0.0043},
  "verdict": {"decision": "ship", "statement": "..."},
  "evidence": {
    "scorer": {"path": "evals/jev-judgments/score.py", "function": "score"},
    "source": {"evals/jev-judgments/score.py": "<sha256>", "...": "<sha256>"},
    "dependencies": {"ops/jev_judge.py": "<sha256>", "...": "<sha256>"},
    "expectations": {"path": "evals/jev-judgments/expectations.v1.json",
                     "version": "jev-judgments-expectations/v1", "sha256": "<sha256>"},
    "cohorts": {"baseline": {"path": "evals/jev-judgments/evidence/baseline.jsonl", "sha256": "<sha256>"},
                "candidate": {"path": "evals/jev-judgments/evidence/candidate.jsonl", "sha256": "<sha256>"}}
  }
}
```

### The evidence chain

The check recomputes a receipt; it never trusts one. `evidence` binds by sha256:

| field | what it binds |
|---|---|
| `source` | the harness and scorer code; `scorer.path` must be one of these |
| `dependencies` | every file the measured run read (the system under test and its inputs) |
| `expectations` | the labels, in a file with a `version` and `cases: {id: {split, should_not_fire, input_sha256, ...}}`. The same version must keep the same bytes as at the merge base: relabelling is a new version, never an edit in place |
| `cohorts` | one JSONL row per case per arm, `{case_id, split, input_sha256, ...}`: raw observations, not grades |

Expectations and cohorts live under `evals/<surface>/`. Both cohorts must be
exactly the labelled cases, with the labelled split and input, no gaps and no
repeats. `cases.total/train/test/should_not_fire` must equal the expectations.
The check then calls `scorer.function(expectations, baseline_rows,
candidate_rows)`, which returns `{dimensions: {id: {baseline, candidate,
delta}}, controls: {oracle_pass_rate, null_pass_rate}}`; every dimension the
scorer measures must be in the receipt with the same numbers, and the
grader's oracle and null rates must match. Deleting a failing row, shrinking
the owed denominator, editing result bytes and carrying a stale summary
forward each fail. `evals/rule-delivery/` is the worked example: its
`make_report.py receipt` produces both cohorts and the receipt.

Scores are test-split numbers in [0, 1]. `direction_vs_baseline` must agree
with the delta interval: `improved` when it sits above zero, `regressed` below,
`equivalent` when it contains zero. Optional fields: `overall` (reported,
never decisive), `offline_suite` (`{path, suite_digest}` for an
`evals/ai/` suite), `rounds`, `notes`.

`verdict.decision` is one of `ship`, `ship_cost_at_parity`, `do_not_merge`,
`inconclusive`. The check refuses a receipt whose verdict disagrees with its
own numbers. A changed surface passes the gate only with `ship` or
`ship_cost_at_parity`; `do_not_merge` and `inconclusive` are valid recorded
results but block the pull request, even if its body also has a no-eval line.
An unreadable or malformed pull-request event also blocks the check.

## The control behind the rule

The check is registered for approve-rule as control `eval_receipt`
(`deny_gate`), the carrying control for rule
6cbaa63a-be57-4c2e-955f-4ea7b5c0405d. It is declared under
`controls_absent_from_the_map` in
`ops/config/control-enforcement-classes.v1.json`, compiled by
`ops/sync_control_catalog.py`, and seeded by
`migrations/0753_eval_receipt_control.sql`.

## Registering a surface

Add globs to `evals/surfaces.json`. A pull request is judged by the union of the
registry at its merge base and at its head, and an exclude counts only when
both carry it, so dropping a glob, dropping a surface or adding an exclude
narrows coverage only for pull requests that come after it merges. A hook
that emits `additionalContext`, `systemMessage` or `hookSpecificOutput` must be
matched there or the check fails. Anything you were unsure about goes in `unsure` with a reason, so the
next person can decide.

Rule text and doctrine that change through record-layer verbs never pass
through a pull request, so this gate cannot see them. That gap is listed in
`unsure` on purpose.
