# Adopt jevlint (codegirl-007/jevlint, MIT) in carr-system — code-taste linter on Jev

Joe 2026-10-04: "this code taste linter seems like a good add". Source read in full by the orchestrator:
https://github.com/codegirl-007/jevlint (README, 27 example rules under examples/rules, packs, evals). Clone it yourself
(`gh repo clone codegirl-007/jevlint`) and read README + internal/ before designing. Work in /Users/booko/carr-system-jevlint
(branch claude/jevlint-adopt). Open ONE PR. Never merge. Never print or handle credential values.

## Why it fits (do all of it)
- It is Matt Pocock's deletion test and pstack's "lint/CI" rung of the correction ladder as a standing check: its
  rules unnecessary-abstraction, wrapper-without-value, speculative-generalization, coincidental-abstraction,
  swallowed-errors, function-name-behavior-mismatch catch exactly what our deletion passes removed by hand.
- It runs on Jev, which we already pay for.

## The constraint you must design around
Decision 31ea6383 (Jev rebuild): ONE spend authority, 500 paid calls/day, hard 1,000, ZERO bypassing callers.
jevlint calls TypeSafe directly (one call per code unit x rule batch; a whole-repo run is thousands). So:
1. jevlint must never call api.typesafe.ai directly. It honours TYPESAFE_ENDPOINT (any SystemOne-compatible URL).
   Build a minimal local SystemOne-compatible shim that forwards each request through ops/typesafe_client.py's
   admission (registered call site in ops/config/jev-call-sites.v1.json with its own daily/hourly budget,
   attribution, daily-cap log). Over budget = shim returns an error and jevlint exits 2; never silent pass.
2. Only `jevlint check --changed` (PR diff) in the review path; never full-repo runs on a schedule. Rely on its cache.
3. Measure: record calls per PR on 3 real PR diffs and put the number in the PR body.

## Steps (all required)
1. Pin jevlint by exact commit (go install ...@<sha>); document the install in the repo (no binary committed).
2. jevlint.json for python + javascript/typescript: start from the example rules that match our deletion/retro
   findings; add our own rules from out/orch/matt/retro-report.md coding-standards items and
   out/orch/matt/del-carr-system-report.md. Every rule gets exceptions where our code legitimately differs.
3. Calibrate with `jevlint eval`: build fixtures from REAL code in this repo — bad = the code removed by the deletion
   PRs (carr-system PR 1537, doctorcre-app PR 154 diffs), good = what replaced it. Set minConfidence per rule from
   eval results. Drop any rule that cannot reach matched expectations; list dropped rules with the failing numbers.
4. Wire it advisory-first: a script (tools/ or ops/) the PR review loop can run on a PR worktree that outputs
   findings as JSON for the reviewer. Not in hosted CI (no TypeSafe key in Actions). Add it to the selftest/CI that
   runs WITHOUT a key using a recorded-response fixture so the wiring is tested deterministically.
5. Tests first for the shim's budget refusal and attribution (write the failing test, then the code).
6. PR body: what each rule catches, eval table, calls-per-PR measurement, what was declined and why.

Print the PR URL on its own line at the end.


## Resume note
A previous run of this brief was stopped mid-way. The worktree already contains ops/jevlint-selftest.py (uncommitted). Read it, keep or improve it, and continue from there.
