# CI and local-check simplification audit — 2026-10-23

**Report only.** No workflow, test, hook, ruleset, production record, or deployment is changed by this PR.

## Recommendation

Stop running substantive CI on draft PRs. Run the complete applicable suite when a PR becomes ready and on every subsequent ready-PR update. Keep a small, fail-closed required verdict named **`ops/ci.sh --strict`**; a draft must not produce reusable full-suite success. First repair the local pre-commit scanner lookup, consolidate repeated checks, and remove tests whose only consumers were purged.

Keep three logical verification lanes: product/source, portable safety tests, and disposable-database proofs. Keep the existing database parallelism initially. The current main-canary fallback demonstrates why collapsing everything into one serial command is a poor target: its migration job alone has a historical median of about 27 minutes. Minimize repeated assertions and setup, then reduce physical shards with measurements.

The largest measured avoidable cost is draft CI: **about 17.4 runner-minutes per successful draft event**, including the separate Instruction drift job, become approximately 10–20 seconds for a verdict. Ready-PR setup consolidation offers **about 5.9 runner-minutes per full run**, before eliminating duplicated instruction-drift setup or database work. Do not promise a corresponding ready-PR wall-time improvement: several removed jobs currently run off the critical path.

## Scope, source and measurement limits

- Audited repository: `DoctorCRE/carr-system`. Source pin: [`804a3794fb120dcfec980e11b958d205d90f6130`](https://github.com/DoctorCRE/carr-system/commit/804a3794fb120dcfec980e11b958d205d90f6130), observed October 23 at 11:13 CDT. The initial checkout was `dca1863a`; the audit was reconciled to the newer pin after eleven more purge commits landed. Recommendations exclude their already-realized savings.
- Read `AGENTS.md` first, all six `.github/workflows/*.yml`, all of `ops/ci.sh`, its class helpers and dispatch wrappers, the 370 dynamically collected gate/selftest files, 41 discovered database gates, nine outer-wrapper acceptance programs, and the directly named contract/Node/SQL proof programs. Appendix inventories identify each file and its disposition. Package-wide unit discovery was traced; this is not a claim of a line-by-line semantic audit of every unrelated Node unit test.
- Source inspection combined full-file reads and parsed assertion/caller inventories. Candidate deletion/merge closures and safety-sensitive runners received targeted full-body review. **Keep** means retain the named invariant, not that every existing assertion or process boundary is optimal. **Merge** requires moving unique assertions before deleting their old owner. No deletion is justified merely by an old filename.
- Historical window: **October 19, 00:00 CDT–October 23, 11:00 CDT**. PR timing window: October 22, 19:00 CDT–October 23, 11:00 CDT. Current-shape comparisons exclude runs containing the already-deleted `Test-first footprint` job. They still span changing source; some DB timings precede the last purge. They are observed operating costs, not an exact benchmark of the final pin.
- Local measurements used a separate clone, Python 3.14.8 and Node 24.19.0; CI pins Python 3.14 and Node 26.12.1. Dependencies were installed from committed locks. Repeated local results below use the same source. Python type checking has a significant cold-cache cost.
- Unmodified full CI was deliberately not executed: it can read the committed schema dump, live credentials, or machine configuration. Full-tree secret scanning also traverses the schema dump. Local migration, full unit/gates, production probe, backup and deploy paths were not run. No environment-value dump or prohibited file was opened. Synthetic source/fixture checks were timed where safe; missing measurements are explicit.
- An initial clone lacked `origin/main`; its first pushfloor attempt failed before assertions. Initial types lacked a Node dependency and dependency audit lacked sandbox network access. Those setup failures were repaired and rerun; they are not flakes. The shared checkout remained untouched.

## What actually runs

| Entry point | Current execution | Decision |
|---|---|---|
| `ci.yml`, draft | Scope + three class jobs + four Fallow jobs + required aggregate: **9 active jobs**, plus separate Instruction drift. Gates and migration are deferred. | Replace with a tiny draft verdict; defer substantive jobs. |
| `ci.yml`, ready, DB relevant | Scope + six class cells (including two gate shards) + six DB fixtures + four Fallow jobs + aggregate: **18 active jobs**, plus Instruction drift. | Consolidate source/setup; retain meaningful DB proofs and explicit scope. |
| `ci.yml`, ready, DB irrelevant | Scope receipt explicitly says not applicable; migration fixtures skip and migration cell reports the receipt. Other checks still run. | Keep fail-closed applicability, including transitive proof inputs and unreadable-diff fallback to full. |
| `main-canary.yml` | Reuses successful exact-tree PR evidence, otherwise executes an older five-cell matrix: unsharded gates and monolithic migration. | Keep evidence reuse and final canary; share the ready-PR suite definition and setup for fallback. |
| `doc-drift.yml` | Separate PR/weekly/manual job, its own installs, product qualification, changed-instruction scan, sanitized artifact. | Merge PR changed-reference checks into source lane; keep weekly full scan and sanitization. Qualification already belongs to gates. |
| `edge-liveness.yml` | Scheduled production health read, incident delivery, retained probe verdict. | Keep operational monitoring; exclude from per-PR savings. Reduce install scope only after import validation. |
| `backup-nightly.yml` | Mode guard, independently hosted encrypted backup, size floor, exact run/attempt artifact identity, readback, R2 copy, failure recording. | Keep recovery invariants. Merge the separate “Confirm the size floor passed” informational step; it repeats a successful script exit. Review remaining failure-proof ceremony separately, preserving truthful failure delivery. Never enable provisioning through this cleanup. |
| `deploy.yml` | Successful main-canary gate, main candidate, anti-rollback readback, exact app assets/manifest, DB migrations, Worker deployment, `/release` identity verification. | Keep. This is production delivery, not draft CI overhead. Share install implementation without sharing credentials or removing isolation. |

Pinned action/tool versions, lock installs, bounded shell/Postgres setup, output redaction, failure artifacts and truthful test-count checks protect reproducibility or diagnosis. Retain them where consumed. Installing Python, both Node packages, zsh and sometimes a Postgres service in every unrelated class job is redundant. `ops/ci-postgres.sh` installs **17 and 18**, despite the step name saying 17; matching production-major shadow checks must survive simplification.

## Runtime and unchanged-main failures

“Local 0/3” means three same-source local executions passed. **Unchanged-main hosted rate is N/A for every class**: there were no repeated main-canary SHAs or attempts in the observed week. The next table also supplies the available changed-main observations rather than substituting them for flakiness.

| Class | Decision | Rough runtime, with provenance | Failure evidence |
|---|---|---|---|
| `pushfloor` | Keep local; merge hosted source preflight | Local median **0.27s** (0.27–0.36s), without changed-range branches | Local 0/3. Included in composite: 0 failed composite executions/52 changed-main executions. |
| `unit` | Keep; absorb unique contracts | Seven sampled successful hosted class logs: **176–266s** (median 192s); eight-class check-step median across the larger sample is **364s**. Safe local subsets: control-room **0.57s**, workspace **1.03s** medians | Each local subset 0/3; whole class not run locally. Composite 0/52, not per-test statistics. |
| `types` | Keep | Hosted **2–4s** in seven sampled logs; local **35.78s cold**, **5.05–5.11s warm** | Local 0/3 after dependency setup; composite 0/52. |
| `contract` | Merge/delete class | Hosted **0–1s**; local workspace validator **0.115s**, repeated control-room pair **0.317s** median. Live probe not timed | Both synthetic parts 0/3; composite 0/52. Live probe may skip while class reports success. |
| `gates` | Keep useful coverage; delete/merge appendix items | Hosted shard check medians **184s / 181s**; jobs **276s / 274s** including setup. Historical unsharded canary check **504s**, job **615s** | **3/52 changed-main gate executions failed**. Individual suite rates unknown; full local class not run. |
| `secret` | Keep detections; merge duplicate scanning | Hosted **53–88s** in seven sampled logs; full local scan withheld | Composite 0/52; individual detector rates unknown. |
| `dependency` | Keep; merge job | Local median **1.32s** (1.28–1.68s); hosted check **1s**, job **76s** | Local 0/3 with registry access; changed-main 0/25 standalone, plus 0/27 old composite. |
| `migration` | Keep; consolidate setup and exact duplicates | Hosted proofs check **344s**, job **430s**; subparts below. Historical monolithic canary check **1,489s**, job **1,621s** | **1/52 changed-main migration executions failed**. Local not run under dump restriction. |
| `binding` | Keep actual workerd boot; fix strict skip | Hosted **2s** in each of seven sampled logs; local not executed | Composite 0/52. Source proves missing Wrangler can return green without boot. |
| `artifact` | Keep; cache and merge repeated registry import | Hosted **1s** in each of seven sampled logs; network artifact path not timed locally | Composite 0/52. Do not count pin verification and verb retirement as equivalent checks. |
| `freshness` | Merge source preflight | Local **0.033s** | Local 0/3 at initial pin, unchanged implementation; composite 0/52. |
| `hxprobe` | Keep tests and release build; share setup | Hosted check **72s**, job **152s**. Local Rust toolchain absent | Changed-main 0/26; no same-SHA sample. Debug preparation in gates is not identical to release build. |

The 52 composite observations include 25 current eight-class runs and 27 earlier nine-class runs with dependency included. These show the group did not fail; they do not prove every nested test executed on every run. Known optional/skipped branches remain disclosed.

| Database partition | Median check seconds | Median job seconds | Decision |
|---|---:|---:|---|
| proofs | 344 | 430 | Keep real handler/SQL, role and atomicity proofs. |
| reconstruction | 148 | 232.5 | Keep exact source-ledger reconstruction. |
| equivalence | 324 | 399.5 | Keep comparator negatives; replace repeated full-schema rebuilds with small synthetic schemas where valid. |
| shadow | 324 | 398 | Keep base + pending migration and refreshed reviewed-seed equivalence. |
| gates-serial | 306 | 385 | Keep current DB invariants; remove literal duplicate owners. |
| gates-isolated | 365.5 | 446 | Keep isolated SIEP roles/clusters and declared scenario coverage. |
| acceptance | 215.5 | 289.5 | Keep the **nine** programs in the outer `local-pg-ci.py` manifest. It is not an empty partition. |

The fixture medians use 38 successful ready-shaped runs; proofs uses 39 including one scoped-out run. Per-file DB timing and same-SHA failure frequency are unavailable; the appendices inherit their containing partition's timing, not invented per-file numbers.

Safe individual gate medians from three repeated executions: native scheduling **0.115s**, PR design policy **0.116s**, review tiers **0.115s**, performance budget **0.457s**, routine cutover **0.169s**; all 0/3 failures. These were measured at the initial pin and their implementations were unchanged at the final pin. A passing obsolete test is still removable. Additional three-run samples at `804a3794`: no-client-deliverables **0.036s**, retired-merge-service **0.172s**, sync-core-rule-ids **0.039s**, codemap **0.836s**, all 0/3 failures.

### Historical denominator

The week contains **196 main canaries on 196 distinct SHAs**: 131 success, four failure, 60 cancellation, one incomplete. The noncancelled completed failure ratio is **4/135 = 3.0%**, not a flake rate. Of the 131 successes, 83 reused prior exact-tree evidence and are not new executions. Cancellation is expected from the main-canary pending-run concurrency behavior.

Failed class steps: gates in runs [39340701405](https://github.com/DoctorCRE/carr-system/actions/runs/39340701405), [39435564672](https://github.com/DoctorCRE/carr-system/actions/runs/39435564672), [39785830024](https://github.com/DoctorCRE/carr-system/actions/runs/39785830024); migration in [39878799950](https://github.com/DoctorCRE/carr-system/actions/runs/39878799950). Setup succeeded in all four. A class failure alone does not establish whether its cause was a product regression, stale fixture, timing race or infrastructure inside the check. Five scheduled full CI runs passed on different SHAs; this also does not establish unchanged-source repeatability.

Add small machine-readable per-program elapsed/exit records to the existing summary/artifact, bound to SHA, test-contract digest and attempt, without raw output or environment values. Retain the **first** result if a named infrastructure retry occurs. This is the missing measurement needed to retire genuinely flaky/slow tests responsibly; no new service or flake controller is needed.

## Draft policy, rulesets and concurrency

Live ruleset [“main: CI must be green” (20824501)](https://github.com/DoctorCRE/carr-system/rules/20824501) is active and requires exactly the check context **`ops/ci.sh --strict`**. That string is the context, not the ruleset name. Up-to-date/strict status policy is false, there is no bypass actor, and the context has no integration binding. [“Public repository leak guard” (24526078)](https://github.com/DoctorCRE/carr-system/rules/24526078) is disabled; do not assume it independently protects uploads or merges.

The current CI concurrency group is workflow + event + PR/run ID + **draft/ready**. Marking ready does not cancel the old draft run; converting back does not cancel the old ready run. Instruction drift already uses a PR group without that suffix. Main canary has one global group with `cancel-in-progress: false`; running work finishes and older pending work can be replaced. Production deployment also stays serialized and non-cancelling.

| Policy | Product feedback | Cost / protection |
|---|---|---|
| Current draft-trimmed matrix | Early unit/type/secret/boot/Hx/dependency and Fallow feedback; DB/gates wait | Successful draft median **8m01s wall**, **15m31s runner**, plus **1m53s runner** for Instruction drift. A trimmed aggregate can be green, although it correctly omits full-tree reuse evidence. |
| Literal zero jobs while draft | No hosted feedback until ready | Saves all draft compute, but workflow event filters cannot directly express draft state. Job-level skips can be accepted as successful required checks; same-SHA draft→ready transitions need explicit proof against stale/skipped success. Do not ship only a blanket `if: !draft`. |
| Recommended: no substantive draft jobs, tiny failing draft verdict | Local targeted checks; full hosted feedback on ready | Saves almost all cost and keeps required-context meaning explicit. Draft verdict exits nonzero with an explicit **failure** conclusion and “deferred; no full-suite evidence”; neutral/skipped conclusions are not safe substitutes because GitHub can accept them as required checks. It emits no reusable receipt and cannot satisfy a ready merge. |

Implementation requirements:

1. Keep `opened`, `synchronize`, `reopened`, `ready_for_review`, and `converted_to_draft`. Run the complete applicable suite for **every ready head**, not only the transition event. Keep `merge_group`, scheduled and manual full verification.
2. Use one PR-number concurrency group across draft/ready; retain separate non-PR scopes. Conversion to draft cancels expensive ready work. Conversion to ready cancels draft work. Do not let cancellation produce a successful aggregate.
3. Keep an always-evaluated required verdict. On a draft event it must explicitly fail (nonzero exit), rather than conclude neutral or skipped; on a ready event it accepts only all required successful lanes plus a valid non-applicability receipt. Missing, cancelled, timed-out, failed and unexpectedly skipped work block.
4. Bind ready evidence to current event/head/tested merge tree, class contract and attempt. No draft receipt is reusable. Test the same-SHA ready/draft/ready sequence and a new synchronize commit on a disposable PR before adopting the policy.
5. Repair local secret-scanner execution first, but do not claim local hooks are enforced server security: they may be absent or bypassed. Hosted draft scanning happens **after** upload, so it cannot replace scanning before a public push. Keep ready-PR security checks even after local repair.

No ruleset rename or weakening is needed. If a later implementation changes the aggregate name, coordinate the ruleset atomically; this report recommends retaining it. The existing non-strict/up-to-date policy is a separate integration-risk choice; keep merge-group testing and main exact-tree/fallback verification rather than quietly changing it here.

## Target CI shape

Use `ops/ci.sh` as the one local/hosted entry point, with a small explicit program inventory consumed by collection, scope and verdict checks. Replace duplicated YAML/string parsing rules with one validated inventory; do not introduce another controller.

| Lane | Required work | Setup and ownership |
|---|---|---|
| Product/source | Public-source protection, unit + unique contracts, types, actual workerd boot, artifact pin/verb retirement, dependency integrity, unresolved index | One locked Node/Python setup. One introduction scan per bind moment; retain both detectors' unique coverage until proven equivalent. Include changed-instruction reference diagnostics with existing PR advisory semantics; do not promote drift findings to a new required veto. |
| Portable safety | Retained offline product/operational selftests; CI collector/verdict/isolation/redaction/watchdog qualification; Rust tests/build | Keep two shards initially. Run global invariants once, not at the end of both shards. Rust build on one suitable shard with content-addressed cache; preserve debug-vs-release and mutation tests. |
| Database | One scoped obligation: proofs, reconstruction, equivalence, shadow, serial/isolated DB gates, acceptance | Keep the seven current physical partitions initially. Share static preflight and fixture preparation where isolation is proved. No production credentials. Unknown scope means full. |
| Verdict | Exactly `ops/ci.sh --strict`; evidence only after complete ready verification | One small job, scheduler-needs based. Drafts run only this explicitly failing verdict. This creates a visible expected red/deferred status while draft; accept that notification tradeoff to preserve check meaning. |
| Main canary | Exact-tree reuse or the same full suite, then canary verdict | Remove its divergent legacy fallback setup/matrix; preserve deploy prerequisite. |

This gives **12 active CI jobs** for a full ready DB run (scope + source + two safety shards + seven DB partitions + verdict), down from 18, and removes the standalone PR Instruction drift job. Four Fallow jobs become optional local/weekly advice because generic dead-code, architecture and duplication baselines are not a demonstrated product or production invariant; keep import/type/build/runtime checks that actually prove shipping behavior. Fallow remains useful for purge review without being an additional merge veto.

Twelve is the smallest defensible *initial* runner shape with the current latency evidence, not a claim that seven DB runners are intrinsically necessary. The next measured reduction is fixture/setup sharing, then balanced DB grouping. Do not trade a 9-minute ready check for the observed 27-minute monolithic fallback simply to lower the job count. A single safety shard is also a later measurement decision: historical unsharded gate checks already take about eight minutes before setup.

## Ready-to-ship slices

Each slice is independently reviewable. All paths below are relative to the repository. These are implementation instructions for later PRs; this PR changes only this report.

| Slice | Exact deletion/merge/fix | Required acceptance and expected saving |
|---|---|---|
| 1. Restore the local floor | Initialize `hookdir` in `ops/githooks/pre-commit`. It is currently used but never assigned, so scanner/path/index lookups usually skip. Fix pushfloor's SCAC matcher from version-number-only `scac-mutation-registry.v[0-9]+.generated.js` to the actual `scac-mutation-registry.current.generated.js`. Deduplicate a changed hook's paired test if both hook and selftest paths changed. | Disposable fixture reproduced exit 0/no scanner when unset, exit 1/scanner called with the correct directory. Preserve main-commit refusal and synthetic scanner refusal. No runtime saving promised. |
| 2. Delete orphan/process-only gates | Delete `ops/performance-budget-gate.py` + `ops/performance-budget-gate-selftest.py`; delete `ops/pr-design-policy-selftest.py` but retain both skills/policy. Delete `ops/pr-description-check.py` + its selftest and the single `ci.yml` step (currently executed in both gate shards); preserve useful PR template/review guidance. Delete the orphan review-tier generator closure: `ops/review-tiers-selftest.py`, `ops/sync-review-tiers.py`, `ops/config/review-tiers.v1.json`, `mcp-server/src/review-tiers.generated.js`, `ops/fixtures/review-tiers/pre-change-baseline.v1.json` and `tier-vectors.v1.json`; remove corresponding Fallow baseline entries. Delete `ops/lead-board-runtime-fork-selftest.py`, which only asserts a historical comment exists. Remove unwired canonical-edit/CoStar/weekend-quiet hooks with their selftests and catalog/baseline/route entries, as appendix specifies. | Collector still covers every retained program; all retained safety hooks pass; no remaining executable import/caller. Measured scalar/design/tier checks together are under one second; do not sell this as minutes. This removes dead obligations. |
| 3. Remove dead migration promises | Delete five `if [ -f ... ]` blocks in `ops/ci.sh`: foundation-assurance oracle role-boundary, Gate Zero outcome role-boundary, outcome race, digest-tagged and candidate-authority-filing tests. Exact filenames are in the migration appendix. | No change to executed inventory; **approximately zero** runtime saving. Update collection expectations so absent files cannot appear to provide coverage. |
| 4. One owner for contracts | Remove the two duplicate control-room invocations from `check_contract`; unit already executes them. Move unique `workspace/contracts/validate.mjs` assertions into workspace unit once. Remove live `capture-verb-reachability.py` from merge CI, retaining an explicit operational diagnostic if useful. Remove `contract` bookkeeping in `CLASS_ORDER`, workflow groups, evidence and collection tests together. | Seeded contract break still fails unit; zero-test/skipped-only refusal stays. About **0.32s** local duplicate work saved; unique workspace coverage retained. |
| 5. One scan per local push | `pre-push` already runs receiver-aware `security/leak_guard.py --pre-push`; avoid rerunning it through `check_secret --range`. Consolidate orchestration without trusting an environment flag claiming the scan happened. Run `security/test_leak_guard.py` once hosted and locally on detector/hook/corpus changes. Keep shaped/PII detector coverage until a union corpus proves equivalence. Make path-depth/draft-name and INDEX style checks optional advice. | Intermediate commits, receiver ranges, merge resolutions, encoded/split secrets, binaries, PII and redacted diagnostics still qualify. Saving unmeasured; no security coverage deletion. |
| 6. Merge duplicate selftest execution | Put cleanup assertions in the six suites currently rerun by `ops/selftest-temp-cleanup-selftest.py`; remove repeated complete-suite launches and the two additional `runpy` executions. Make `ops/fixture-host-isolation-selftest.py` the sole complete-suite owner for `ops/config-as-code-selftest.py` and `ops/reinstall-launchd-calendar-selftest.py`, retaining hostile-host cases and correcting scope reasons. Consolidate CI collection/event/wiring tests under `ops/ci-selftest.py`; preserve mutants. Execute `test_migr*.py` once before DB partitions. Run real sync-core parity once, retaining mutant generator cases. Run `test/ci-noise-incident-test.py` and shared invariant checks once rather than once per shard. | Exact before/after program inventory and seeded red/skip/timeout/empty-scope/isolation cases. Saves repeated processes and fixture construction; measure per-program records before assigning seconds. |
| 7. Trim repeated DB work | Remove `renewal-lease` entry from `ops/db_acceptance_programs.py` because the same file is a required DB gate. Move current-schema safe-view/grant/partner checks from `ops/renewal-decision-delivery-gate.py` into the lease gate, then delete the old file/dead signed-market branch. Merge Program 3 incident assertions into incident acceptance; retrieval/zero-hit assertions into one owner; Tour storage-ref assertions into Tour delivery proof. Keep closure tests before removing their old files. | Each retained invariant executes once; wrong grants, hidden columns, cross-partner and rollback negatives still fail. Runtime saving unmeasured. Do not delete the acceptance partition or actual runtime SIEP/engineering/Work Request proofs. |
| 8. Reduce DB transport/fixture cost | Batch `ops/trigger-grant-check.py` catalog privilege queries on one connection without changing `has_*_privilege` semantics. Give open-PR claims and static migration ordering one base/head-bound preflight owner. Reuse valid fixture preparation; use small synthetic equivalence mutations and retain one full reconstruction. Preserve fresh clusters for commits/global-role changes and scenario-union checks. | Revoking `SELECT(side)` while preserving `SELECT(slug)` must still fail. All isolated scenarios and product proofs remain required. Experimental savings only until measured. |
| 9. Consolidate ready setup | Merge dependency into source job; move Hx tests/release build to one existing safety runner with proper cache; remove four required Fallow jobs. Merge PR instruction drift into the source lane as advisory diagnostics and its duplicate qualification into gates; do not turn documentation-drift findings into a new required merge veto. Update `ci-selftest.py`, `ci-evidence.py`, scope/collection tests and main-canary fallback as one contract change. | Dependency failure, workerd boot failure, missing class and changed DB input remain red. **About 5.9 runner-minutes** modeled setup/Fallow saving; modest or no ready wall-time improvement. |
| 10. Defer drafts | Apply the lifecycle/concurrency/verdict rules above to CI and PR instruction-drift work. Retain full ready/synchronize/reopen/merge-group/schedule/manual execution. | Disposable PR same-SHA readiness transitions, cancellation and failed/missing lane must block merge. Save approximately **17.1–17.2 runner-minutes per draft event** after a 10–20s sentinel; exact billable saving depends on rounding and queueing. |

Additional behavior-preserving merges in the appendix (calendar envelope/canary, scheduling/cutover, snapshot roles, hook/Git isolation, small CLI wrapper suites) should ship as their named assertion transfers, not a bulk file deletion. Fix false-green citation fixtures, missing-Wrangler strict behavior, unsafe ambient-DB tests, fixed local workerd port collisions and brittle race sleeps. Source risk is not evidence of observed flakiness. Remove automatic `ops/migration-production-proof.py` execution from local gates; retain only an explicitly requested release diagnostic. Move codemap missing-description counts, boot byte counts, stale prose counts and historical audit-TSV freshness out of mandatory gate execution. Keep canonical rule/control consistency and actual hook reachability. Fix the gate timeout report: `xargs` already finishes the Python queue before the result loop says it is aborting remaining tests, and `break` then hides later results. Report all started suites and distinguish timeout from assertion failure.

## Savings ledger

| Change | Basis | Expected saving |
|---|---|---|
| Draft substantive CI + separate drift deferred | 28 successful shape-matched drafts: CI median 930.5 runner-s; drift latest 20 successful jobs median 113s | 1,023.5–1,033.5 runner-s (**17.1–17.2 min**) after 10–20s verdict. About 98% compute reduction. Approximately eight minutes of draft feedback wait removed; full ready checks still take their own time. |
| Dependency job merged | 76s job minus 1s useful check | **75 runner-s** per full ready run; check still executes. |
| Hx runner setup shared | 152s job minus 72s check; use conservative ~76s setup median | **~76 runner-s**, assuming moving the check does not repeat toolchain/build preparation. Debug/release work still exists. |
| Fallow removed as required PR jobs | Ready job medians 48+48+54+54s | **~204 runner-s** per ready event. Weekly/ad-hoc advisory work still consumes time when invoked. |
| Three ready changes above | Independent modeled costs, not sum-of-medians proof of a new pipeline | **~355 runner-s = 5.9 min**, about 9% of observed 68 runner-min baseline. |
| Standalone PR instruction drift merged | Whole job median 113s includes useful scan time | Saving is **less than 113 runner-s**; scan remains. Do not count the whole job as saved. |
| Contract duplicate + obsolete scalar/design/tier tests | Direct local measurements | Roughly **one second**; maintenance benefit dominates. |
| DB fixture/query consolidation | Seven lanes have repeated setup and expensive comparator restoration | Not numerically promised. Capture before/after class/program timings; retain a ready-run critical-path budget near the observed 9–14 minute range. |

The 37 shape-matched draft events including failures/cancellations consumed **32,999 completed-job runner-seconds (550 runner-minutes)** before Instruction drift. Replaying that sample with only a 10–20s draft verdict would save roughly **538–544 runner-minutes**, excluding drift and any incomplete intervals. This is a workload illustration, not a monthly forecast.

## Design and debt pass

- **Codebase Design:** one check interface owns collection, applicability and truthful verdicts; process/shard splits are internal execution details. Compared one serial job with bounded lanes; rejected serializing all DB proofs because observed fallback latency is substantially worse.
- **Zero Tech Debt:** recommendations follow current callers at the pinned source, remove orphan closures and duplicate owners, and preserve real product/security/recovery assertions. Already-purged files are excluded from savings. This report introduces no runtime abstraction or compatibility path.

## Inventory conventions

The following inventories are the complete reviewed source lists for the dynamic gates and direct migration/acceptance programs at the pinned revision. For every row without an individual local timing above, **runtime is unmeasured individually**; use the containing gate shard/DB partition measurement. For every row, **unchanged-main hosted failure frequency is unknown (no repeated-source sample)**. Do not interpret a parent class's failure as failure of every child. Keep/merge/fix decisions refer to the specific invariant stated in each row.


## Measurement receipts and reproduction

Run/job metadata was paginated from GitHub's repository Actions API (`actions/runs`, `actions/runs/{id}/jobs?per_page=100`), filtered to the stated Chicago windows and matching job shapes. All relevant job lists fit one 100-job page. Draft-shaped means the historical job set omitted gates/migration; present-day PR metadata cannot reconstruct historical draft state perfectly. Wall time is run `updated_at - created_at`, including queueing; compute is summed job duration, not GitHub rounded billing. Main reuse and cancelled/incomplete runs were excluded from execution-failure denominators.

For individual class timings, seven successful run logs were processed with an allowlist that retained only numeric `ci-timing` fields and known `ci-failed-classes` names. Raw logs were neither printed nor saved. This sample is separate from the larger job medians. Seconds are integer-resolution script measurements; zero means below that resolution, not no work.

| Run | Unit | Types | Contract | Secret | Binding | Artifact |
|---|---:|---:|---:|---:|---:|---:|
| [39981660756](https://github.com/DoctorCRE/carr-system/actions/runs/39981660756) | 190 | 3 | 0 | 57 | 2 | 1 |
| [39981431943](https://github.com/DoctorCRE/carr-system/actions/runs/39981431943) | 265 | 3 | 1 | 88 | 2 | 1 |
| [39981414464](https://github.com/DoctorCRE/carr-system/actions/runs/39981414464) | 266 | 4 | 0 | 87 | 2 | 1 |
| [39982382634](https://github.com/DoctorCRE/carr-system/actions/runs/39982382634) | 228 | 3 | 1 | 72 | 2 | 1 |
| [39982337529](https://github.com/DoctorCRE/carr-system/actions/runs/39982337529) | 176 | 2 | 0 | 53 | 2 | 1 |
| [39980974007](https://github.com/DoctorCRE/carr-system/actions/runs/39980974007) | 192 | 3 | 0 | 58 | 2 | 1 |
| [39948711223](https://github.com/DoctorCRE/carr-system/actions/runs/39948711223) | 190 | 2 | 0 | 55 | 2 | 1 |

Observed same-SHA overlap: draft-shaped [39982382634](https://github.com/DoctorCRE/carr-system/actions/runs/39982382634) ran from 10:55:11 to 11:03:02 CDT; ready-shaped [39982395294](https://github.com/DoctorCRE/carr-system/actions/runs/39982395294) started 10:55:16 CDT on the same head. This proves duplicate work survived readiness conversion, not that a merge bypass occurred.

Local timing commands were `bash ops/ci.sh --strict --only <safe-class>`, direct Node contract commands, `npm --prefix control-room test`, `npm --prefix workspace test`, and individually reviewed synthetic Python selftests. Each retained local sample used three sequential executions, elapsed monotonic wall time and exit code. Root-run class/contract samples removed production-capable ambient database variables without displaying values. Four separately reviewed gate samples (no-client, retired-merge, sync-core, codemap) inherited the environment but had no database or credential execution path. Full-CI timing was not approximated by summing these partial tests. The report's source revision remains fixed even if main advances afterward.

## Appendix A — Non-gates dispatch inventory

| Entry | Helpers and distinct assertion | Disposition |
|---|---|---|
| pushfloor baseline | `ops/no-client-deliverables-gate.py`: tracked path pattern guard, public source; `ops/hook-deletion-check.py`: deleted hook scripts may not remain in HEAD wiring | Keep, source preflight |
| pushfloor path style | `ops/githooks/path-hygiene-check.py --paths`: four-directory limit and draft/final name policy | Delete as mandatory product gate; optional lint only |
| pushfloor migration structure | `ops/migration-order-gate.py` on changed migration/baseline/number/source files | Keep local early check; avoid duplicate within a full run |
| pushfloor collection | `ops/ci-selftest.py --collection-only` on tests/ci changes | Keep, consolidated collector invariant |
| pushfloor Python | `bin/type-check.sh` on any changed Python | Merge result with `types` when both selected |
| pushfloor SCAC | `mcp-server/test/atlas-inventory-graph-read.test.mjs`, `atlas-inventory-graph-web.test.mjs` on SCAC migration/registry change | **Fix** stale changed-path regex: it matches `.v[0-9]+.generated.js`, but the only tracked registry and the read test import are `.current.generated.js`; keep local targeted tests, hosted unit already runs each |
| pushfloor gate closure | Changed hooks' `ops/<base>-selftest.py`, changed ops selftests, `ops/hook-env-isolation-selftest.py`, `ops/selftest-git-isolation-check.py` | Keep changed-local behavior; deduplicate paired selftest if touched via both hook and selftest lists |
| unit | `ops/ci-unit.py`, `ops/node_test_summary.py`, `ops/ci-node-file.mjs`, `bin/with-timeout.py`; four package scripts | Keep truthful count/timeout/error propagation |
| MCP unit batching | `mcp-server/run-tests.mjs`: Chrome launch first, browser suites serial, selected private-Postgres suites serial, remainder four-worker pool | Keep resource isolation; measure before changing |
| types Worker | `mcp-server/generate-verb-types.mjs`, `generate-worker-types.mjs`, TypeScript project | Keep generated/source contract compile |
| types Python | `bin/type-check.sh`, `mypy.ini`; missing tool exit 78 | Keep; strict must refuse skip |
| contract workspace | `workspace/contracts/validate.mjs` imports schema compiler and JSON fixture/contracts; extensive fixed historical council/roadmap assertions overlap unit | Merge into workspace unit, delete duplicated assertions only after mapping |
| contract control-room | `control-room/test/contracts.test.mjs`; `work-request-projection.test.mjs` | Delete only duplicate class invocations; tests remain in unit |
| contract live probe | `ops/capture-verb-reachability.py`: schema refusal via deployed report-problem, then live `carr_jobs` column grant read | Delete from merge CI; local diagnostic not product deterministic test |
| secret qualification | `security/test_leak_guard.py`: 44 synthetic repository/scanner qualification tests, isolation, history/range/merge coverage and linear-time caps | Keep once hosted, changed-only local |
| secret introduced blobs | `security/leak_guard.py`: pinned gitleaks archive and generic/hashed-client PII; `security/allowlist.json`, corpus schema; receiver-aware commit ancestry | Keep one execution per bind moment |
| secret shaped tree | `ops/ci-secret-scan.py`, `ops/pii_guard.py`, shaped pattern allowlist and public identities | Merge only after union-of-coverage proof; retain redaction library |
| dependencies | `ops/ci-dep-check.py` + `ops/doc-drift-product.py` requirements verification, production npm audit, Python declaration/hash/pin checks | Keep in common source lane; bounded network calls |
| binding config | Inline Wrangler name/main/compatibility fields and no inline DSN | Keep (cheap) |
| binding workerd | `bin/worker-boot-check.sh` | Keep; missing execution under strict is a bug |
| binding machine drift | `ops/config-as-code.py`, `ops/config_drift_ownership.py` former invocation | Already deleted by `d61b5944`; no additional savings to claim |
| artifact pin | `tools/release-manifest.py doctorcre-artifact verify` delegates `tools/doctorcre-artifact.py`; checks pinned remote tar and detached manifest without activating/materializing | Keep; digest cache |
| artifact registry | `ops/verb-count.sh`, `ops/verb-loss-vs-base.sh`, `ops/verb-loss-guard.py`, `ops/config/retired-verbs.v1.json` | Keep; one current registry import |
| freshness | `git ls-files --unmerged` | Merge source preflight |
| hxprobe | locked `cargo test`, `cargo build --release`; build.rs emits atlas names via `ops/hxstore-atlas.py rust spec/outlook/hxstore-atlas.json` | Keep decoder/read safety and release-build check |


## Appendix B — Fixed gate checks and runner corrections

| Invocation | Decision | Reason |
|---|---|---|
| `test/ci-failure-audit-test.py` (main-canary only) | keep/merge | Six synthetic cases bind attempt/head identity, cache log/metadata digests, race refusal and atomic publication. Merge into retained CI evidence/collector qualification so the ready and fallback inventories agree. Individual runtime/rate unmeasured. |
| `test/ci-noise-incident-test.py` (both workflows; twice in PR shards) | keep once | Trusted incident ownership, recurrence/recovery, unknown vs alarm, pagination and redacted artifact integrity. One portable test owner; no live sends in its mocked qualification. |
| `tools/codemap.py --missing` | delete from required gate execution | Informational opening-description scan; retains command and fixture tests, no product failure. |
| Rust `cargo build --locked` before gates | merge/fix | Needed by HxStore consumers; build once in owner/job or use cache, do not remove without those consumers. |
| `hooks/gate-integrity.py --strict` | keep | Current declared hook implementation and installed safety registration must agree; once in source lane. |
| `ops/boot-budget-check.py` | delete from repeated gate execution | Informational byte count, already not a verdict. |
| `ops/audit-queue-freshness-check.py` | fix | Dated audit TSV and rule-ID mentions are weak evidence; replace merge prerequisite with current canonical control consistency. |
| `ops/stale-count-check.py` | delete from required gate execution | Prose linter; keep as manual/advisory writing check if desired. |
| `ops/rule-load-layer-check.py` | merge | Keep active-rule loadability invariants; share one parsed rule manifest with route checks. |
| `ops/rule-classification-parity-check.py` | fix/merge | Compares two committed snapshots, not live production truth; preserve current contract semantics in canonical rule manifest validation. |
| `ops/selftest-git-isolation-check.py` | keep | Test fixtures must not mutate the live Git tree; necessary local and hosted suite safety. Extend discovery to underscore/room-bridge tests. |
| `ops/sync-core-rule-ids.py --check` | merge | Same real parity already asserted by its selftest; keep one execution. |
| `ops/rule-route-coverage.py` | fix | Active scoped rules need actual reachable routes; fix gate existence checks to require current hook/event registration or executable caller, since retired weekend gate still looks reachable. |
| `ops/migration-order-gate.py` | merge | Keep immutable history/slot/order check once in migration-static owner, not every gate shard and DB partition. |
| `ops/migration-safety-gate.py` | keep | Preserve migration permission/destructive-operation invariants. |
| `ops/door-reachability-check.py` | keep | Orphan executable/check declarations must be exposed rather than counted as protection. |
| Working-tree fingerprint before/after | keep | Tests must not alter caller HEAD, tracked files or index. |


## Appendix C — Every dynamic gate/selftest

At the pin: **370 entries (368 Python, two shell)**: **313 keep, 40 merge, eight delete, nine fix**. Three direct entries are configured local-only, leaving 367 selected hosted entries before internal skips. “Declared/installed” descriptions below refer to source declarations and test assertions, not inspection of this machine’s settings or live production. Individual unmeasured runtimes and failure frequencies follow the inventory conventions above.

| Script | Decision | Protected invariant / disposition |
|---|---|---|
| `ops/action-risk-guardrails-selftest.py` | keep | Closed action-risk ownership and exact registry digest; refuses missing or unowned runtime protection. |
| `ops/audit-queue-freshness-selftest.py` | fix | Gate binds a dated rule audit to current active rules and treats rule-ID mentions as enforcement evidence; move evidence consistency into the canonical rule-contract check instead of maintaining a historical TSV as a merge prerequisite. |
| `ops/audit-template-colors-selftest.py` | keep | Client audit output preserves amendments while replacing obsolete send instructions. |
| `ops/backup-dump-selftest.py` | keep | Encrypted dump pipeline, failure propagation, size floors and atomic promotion; mocked transport complements real PostgreSQL proof. |
| `ops/backup-guard-selftest.py` | keep | Snapshot import, table census, RLS and concurrent-DDL safety; portable mode is not the live PostgreSQL/age round trip. |
| `ops/backup-role-rls-coverage-selftest.py` | keep | Backup role sees every RLS row without BYPASSRLS or write grants; retain real local-database proof. |
| `ops/backup-workflow-selftest.py` | keep | Backup producer and observer bind exact run identity, cancellation and terminal status; source checks plus fake-provider scenarios protect recovery. |
| `ops/blocker-review-selftest.py` | keep | Blocker resolution distinguishes unavailable permissions from no blockers and names unsupported checks. |
| `ops/brief-check-selftest.py` | keep | Requirement evidence cannot be invented from comments, missing files, stale heads or unrelated checks; publication transport is mocked. |
| `ops/build-duration-check-selftest.py` | merge | One pure completed-run duration classifier; move into the retained CI/reporting helper test module, preserving its completed-versus-running case. |
| `ops/built-unclosed-selftest.py` | keep | Distinguishes landed code from closed attestations and prevents false rebuild claims. |
| `ops/calendar-canary-record-selftest.py` | merge | Move static canary aggregate/receipt assertions beside calendar-capture scenarios; preserve no-live-write and exact-lease checks. |
| `ops/calendar-capture-selftest.py` | keep | Permission denial, timeout, intake ambiguity and write failure cannot masquerade as an empty successful Calendar capture. |
| `ops/calendar-dry-run-purity-selftest.py` | keep | Dry-run Calendar acquisition cannot write activity or durable receipts. |
| `ops/calendar-intake-gate-selftest.py` | keep | New attendee intake needs its record, search and identity receipts; unmatched intake cannot hide independent exact touches. |
| `ops/calendar-prebrief-collector-selftest.py` | keep | Signed EventKit acquisition binds database allowlist, fixed window and collector identity without a database credential in the collector. |
| `ops/calendar-prebrief-coordinator-selftest.py` | keep | Real parent/child boundary verifies signed envelopes, execution identity, FIFO bounds and credential separation. |
| `ops/calendar-prebrief-envelope-selftest.py` | merge | Fold tiny signature/key/version/replay fixtures into the signed-envelope coordinator suite; preserve the wrong-key and replay negatives. |
| `ops/calendar-prebrief-eventkit-selftest.py` | keep | Sponsor allowlist, denied reads, ambiguous attendees and bounded event projection; no raw attendee leak. |
| `ops/calendar-prebrief-ingest-selftest.py` | keep | Acquisition-to-ingest schema and sponsor separation; raw identifiers and mismatched login refuse. |
| `ops/call-mode-python-launchd-selftest.py` | merge | Move interpreter/TCC contract into launchd-plist portable suite; retain macOS executable-presence assertion in local verification. |
| `ops/candidate-match-threshold-selftest.py` | keep | Names alone never suppress a candidate; corroboration and exact email retain their distinct behaviors. |
| `ops/canonical-edit-gate-selftest.py` | delete | Exercises an explicitly unwired retired hook. Remove hook plus suite and stale catalog/route/baseline entries in one closure; retain harness-retirement absence/safety checks. |
| `ops/canonical-ownership-siep18-normalization-selftest.py` | keep | Ownership fingerprint permits only the narrow SIEP-18 normalization, not arbitrary drift. |
| `ops/capability-program-selftest.py` | fix | Retain ordering/closure authority assertions only while its live API exists; tier2 currently accepts inherited DATABASE_URL and writes before rollback without a loopback check. Require an explicit disposable local fixture. |
| `ops/catchup-writer-selftest.py` | keep | Catch-up view reads work under the actual writer role using synthetic PostgreSQL data. |
| `ops/ci-control-selftest.py` | merge | Centralize event/draft/concurrency and aggregate verdict assertions with ci-selftest; preserve behavioral verdict scenarios and current required-check contract. |
| `ops/ci-enforcement-wiring-selftest.py` | merge | Make executable checker registration part of one CI collection contract; retain refusal for comments, echoes and inactive classes. |
| `ops/ci-evidence-selftest.py` | keep | Exact tested tree, SHA, class completeness and latest result bind canary evidence reuse; unreadable evidence falls back to checks. |
| `ops/ci-migration-gates-selftest.py` | keep | Partition union, private cluster ownership, scenario coverage and failed-shard propagation protect the DB gate runner. |
| `ops/ci-preflight-selftest.py` | merge | Move collection-only, fixture-size and membership mutation tests into ci-selftest; avoid a separate Python process and duplicated YAML assumptions. |
| `ops/ci-selftest.py` | keep | Keep seeded red/skip/empty-class failures, complete collection and shard union, redacted diagnostics and watchdog behavior. Simplify only obsolete workflow-shape assertions after the target manifest is executable. |
| `ops/ci-skip-receipt-selftest.py` | keep | Local skip requires a reason/receipt and cannot bypass minimum checks; merge-time hosted required check remains independent. |
| `ops/ci-unit-selftest.py` | keep | Zero tests, skipped-only suites, missing packages and hidden failures cannot report a green unit class. |
| `ops/citation-trace-check-selftest.py` | fix | All four fixtures live in gitignored out/doctorcre-program. Missing fixtures currently print SKIPPED and return 0: hosted CI asserts nothing. Replace with small committed synthetic parser fixtures or remove this suite from hosted collection explicitly. |
| `ops/claude-scheduler-native-selftest.py` | keep | Provider-owned scheduler snapshot, exact task definition and host timezone must be observed; source now uses NativeSchedulerError after cutover machinery removal. |
| `ops/client-asset-controls-selftest.py` | keep | Client assets require required market blocks, tiers and replacement receipts; weekly batch refuses forbidden reply forms. |
| `ops/client-name-warn-selftest.py` | keep | Warn-only client-name note stays bounded and cannot block pre-push through expensive textconv or hung Git. |
| `ops/cloud-hook-paths-selftest.py` | keep | Cloud clones resolve project hooks while canonical Mac hooks cannot fall back to feature-tree copies. |
| `ops/cloudflare-tokens-selftest.py` | keep | Retained local/fleet token installation is atomic and recoverable, remote errors redact and retries preserve old values; removed nightly-mint cases are already gone. |
| `ops/cmd-text-selftest.py` | keep | Separates shell execution from quoted prose/heredocs; guard cannot be bypassed by actual quoted command wrappers. |
| `ops/codex-continuity-selftest.py` | keep | Transcript identity, path confinement, pagination/rotation and redaction preserve trustworthy session continuity. |
| `ops/codex-hook-smoke-selftest.py` | keep | Smoke judges genuine guard denial separately from unavailable CLI, boot refusal and no output; verifies sanctioned invocation wiring. |
| `ops/codex-rule-hooks-selftest.py` | keep | Native Codex rule-delivery events use the source-owned hook contracts. |
| `ops/command-precheck-selftest.py` | keep | Advisory command analysis never denies, never leaks failure and avoids spending on obvious reads or absent credentials. |
| `ops/config-as-code-launchd-selftest.py` | keep | Install/reload handoff, exact ownership, retries and retired template cleanup protect live scheduled jobs. |
| `ops/config-as-code-selftest.py` | merge | Current source is hermetic reconciliation, despite stale local-only reason. It already runs inside fixture-host-isolation. Keep the hostile-host entrypoint and avoid running the complete reconciliation suite twice locally. |
| `ops/control-plane-authority-runtime-preflight-selftest.py` | keep | Read-only authority probe rejects broad credentials and wrong identities; results cannot impersonate rollout evidence. |
| `ops/control-plane-registry-gate-selftest.py` | keep | Superseded disabled workflows are history rather than drift; bootstrap now explicitly avoids removed legacy-schedule tables. |
| `ops/convo-shutdown-selftest.py` | keep | Real child inherited stderr cannot hang the conversation server or block the next turn. |
| `ops/corpus-push-root-split-selftest.py` | keep | Retired vault roots cannot suppress live home-root sync, and recovery remains explicit. |
| `ops/corpus-render-gate-selftest.py` | keep | Generated corpus rendering remains a write boundary; preserve refusal and authorized-render cases. |
| `ops/corpus-sync-selftest.py` | keep | No-op sync preserves manifest bytes; conflicting source edits cannot be overwritten by an export. |
| `ops/corrections-sweep-selftest.py` | keep | Native correction capture reports gaps, deduplicates copies and redacts secrets before private persistence. |
| `ops/costar-lane-gate-selftest.py` | delete | Exercises the CoStar browser hook explicitly absent from active hooks. Delete hook plus suite and stale route/control/baseline references; preserve retired-hook absence in consolidated harness test. |
| `ops/council-grok-selftest.py` | keep | Council consumes only completed intended-model results and preserves substantive final text. |
| `ops/credential-file-selftest.py` | keep | Shared credential parser quoting/precedence and missing-file semantics; fixtures are synthetic. |
| `ops/db-acceptance-scope-selftest.py` | keep | Changed transitive proof inputs, quoted paths, renames and unreadable events trigger DB proof; retained business gate inventory is checked. |
| `ops/definer-hardening-selftest.py` | keep | Security-definer source scanner catches unsafe relation resolution and validates precise legacy/predecessor forms. |
| `ops/dell-local-client-provision-selftest.py` | keep | Local actor provisioner is idempotent and refuses identity drift or embedded credential material. |
| `ops/dependency-update-selftest.py` | keep | Lock/compiler repin repair and immutable workflow actions protect reproducible dependencies. |
| `ops/disposable-pg-selftest.py` | keep | Cluster ownership, UTF-8 initialization and signal cleanup protect local DB isolation. |
| `ops/doctorcre-artifact-selftest.py` | keep | Artifact hash, traversal, activation and rollback invariants still protect deployed application artifacts. |
| `ops/doctorcre-production-smoke-selftest.py` | keep | Mock HTTP validates production identity, semver contract floors and same-origin auth redirects. |
| `ops/doctrine-meta-singleton-selftest.py` | merge | Fold the three snapshot/upgrade singleton assertions into schema snapshot contracts; retain generation-zero and no-production-state requirements. |
| `ops/door-reachability-check-selftest.py` | keep | Executable gates require actual callers; prose mentions cannot claim coverage and stale declarations refuse. |
| `ops/dot-file-reports-selftest.py` | keep | Offline Dot report completeness; compare with tools/test-dot-file-reports.py before consolidating to one module, preserving unique failure cases. |
| `ops/drive-client-artifact-slice3-selftest.py` | keep | Normal client-artifact mode stays independent from retired Drive sources and recovery remains explicit. |
| `ops/drive-dependency-inventory-selftest.py` | keep | Drive dependency classification detects uncovered/multiply classified paths; do not remove remaining runtime/recovery boundary from its migration-style name. |
| `ops/drive-reader-selftest.py` | keep | Canonical record mode cannot depend on retired Drive feeds. |
| `ops/drive-reader-slice2-selftest.py` | keep | Canonical health/export/registry readers reject ambient Drive and distinguish failures, missing due work and carried legacy schedules. |
| `ops/drive-retirement-readiness-gate-selftest.py` | fix | Preflight always refuses phase4-exit absent immutable receipts; replace phase-closure ceremony with current canonical-reader/recovery checks once remaining legacy references are retired. Not safe to delete solely from its name. |
| `ops/drive-zero-default-cli-selftest.py` | keep | Canonical mode is default and Drive access requires explicit recovery intent. |
| `ops/edge-liveness-selftest.py` | keep | Critical service staleness and empty observations alarm; low-criticality noise does not fabricate outages. |
| `ops/egress-scope-selftest.py` | keep | Real outbound sends cannot hide behind read-looking command forms. |
| `ops/enrichment-queue-selftest.py` | keep | Queue prioritization, budget, expiry, deduplication and ambiguous identities protect lead enrichment. |
| `ops/environment-matrix-selftest.py` | keep | Declared environments, domains and Worker configuration agree; production and diagnostics remain distinct. |
| `ops/export-blast-radius-selftest.py` | keep | One failed export target cannot cancel later independent targets. |
| `ops/export-provider-liveness-selftest.py` | keep | Provider probes and recovery are bounded, distinguish unknown from down and do not invent a cause. |
| `ops/export-vault-root-selftest.py` | keep | Live export root cannot resolve to the repository; explicit valid roots survive projection. |
| `ops/fetch-allowlist-selftest.py` | keep | Source-derived fetch allowlist rejects unapproved fetches without trusting caller text. |
| `ops/fixture-host-isolation-selftest.py` | keep | Hostile HOME/launchctl fixture proves installer suites never affect native host; use this as sole complete installer-suite entrypoint. |
| `ops/fleet-sync-selftest.py` | keep | Exact Quill tree proof rejects mode/binary/deletion/rename drift while allowing canonical updates. |
| `ops/fleet-sync-siblings-selftest.py` | keep | Sibling fast-forward never damages dirty, diverged or off-main repositories or blocks canonical sync. |
| `ops/front-door-video-assets-selftest.py` | keep | Front Door/video assets use Drive-free product boundary with explicit recovery paths. |
| `ops/gate-integrity-selftest.py` | keep | Required safety hooks and their exact reviewed bytes cannot disappear or be replaced silently. |
| `ops/git-env-selftest.py` | keep | Fixture subprocesses cannot mutate another repository via inherited Git configuration; broaden this retained suite with hook-env same-statement assertion. |
| `ops/github-gh-caller-selftest.py` | keep | Actual caller subprocess seams preserve JSON payloads and pagination without live GitHub access. |
| `ops/github-rate-limit-selftest.py` | keep | Provider-issued holds, pagination completeness, principal pools and bounded retry protect external API correctness. |
| `ops/github-reader-selftest.py` | keep | Reader decodes real paging shapes and distinguishes auth, rate limits and unavailable evidence; retries are bounded. |
| `ops/grok-run-selftest.py` | keep | Runner preserves exact final result/model and retrieval boundary; cancellation and sign-in refusal never report success. |
| `ops/grok-session-selftest.py` | keep | Session timestamp health and alert deduplication remain independent from credential values; unknown cannot clear refusal. |
| `ops/grok_invocation-selftest.py` | keep | Native Grok invocation and authenticated hook lifecycle keep strict fetch grammar. |
| `ops/guard-selftest.py` | keep | Core unattended guard negative cases cover sensitive paths, external sends, destructive operations and provider effects. |
| `ops/guidance-registry-import-selftest.py` | keep | Typed guidance import remains dry-run-first and validates boundary before record effects. |
| `ops/guidance-registry-migration-selftest.py` | keep | Guidance database migration constraints protect typed guidance storage; merge static duplicates only after real DB equivalence is shown. |
| `ops/guidance-registry-selftest.py` | keep | Typed guidance matching, placement and refusal semantics remain live rule delivery. |
| `ops/guidance-situation-curation-selftest.py` | keep | Human curation package preserves typed guidance provenance and review requirements. |
| `ops/harness-retirement-selftest.py` | keep | One consolidated absence check prevents retired hook resurrection while retaining safety hooks and core card delivery. |
| `ops/headless-task-selftest.py` | keep | Headless task scheduling, termination, retries, login refusal and private logs protect unattended execution. |
| `ops/health-act-selftest.py` | keep | Health failures create/dedupe/clear only matching observed loops; incomplete green evidence cannot clear unrelated incidents. |
| `ops/health-export-home-selftest.py` | keep | Recovery watches use actual export home; a fresh retired-vault copy cannot mask a missing/stale export. |
| `ops/health-loose-work-selftest.py` | keep | Shared loose-work classifier identifies actionable unfinished work without fabricating state. |
| `ops/health-rule-gap-selftest.py` | merge | Move five-column query/state assertions into rule-delivery or health contract suite; retain blocked-is-not-enforced and advisory distinctions. |
| `ops/hermes-autonomy-check-selftest.py` | fix | Tests prohibit unaccepted Hermes jobs but AGENTS now explicitly endorses maximum automation. Reconcile the standing invariant with current policy; do not preserve an obsolete generic autonomy prohibition as a product gate. |
| `ops/hermes-memory-check-selftest.py` | keep | Record facts/rules cannot silently become unmanaged local memory; retained health reader uses this checker. |
| `ops/hook-command-selftest.py` | keep | Missing safety scripts terminate while missing optional scripts warn; installer resolves only invoked scripts. |
| `ops/hook-deletion-selftest.py` | keep | Deleted hook files require simultaneous resulting-config unwiring across path spellings. |
| `ops/hook-env-isolation-selftest.py` | merge | Transfer same-logical-command scrub assertion into git-env-selftest, which already tests victim protection and all known location variables; remove duplicate suite and pushfloor invocation. |
| `ops/hook-interpreter-pin-selftest.py` | merge | Combine configured-hook interpreter assertions with hook-command and cloud-hook-paths tests; retain Python floor and exact executable resolution. |
| `ops/hook-runtime-selftest.py` | keep | Hook payload parsing, ordered output and gate-error policy share one runtime decision interface. |
| `ops/hook-safety-selftest.py` | keep | Real shell guard and native managed-policy installation protect source delivery and destructive/sensitive operations. |
| `ops/host-liveness-selftest.py` | keep | Tailnet unknown versus offline distinction and seat mapping protect operational diagnosis. |
| `ops/hxstore-atlas-selftest.py` | keep | Atlas identities/digests match rebuilt worker output without disclosing source bytes. |
| `ops/hxstore-harness-selftest.py` | fix | Preserve synthetic-store isolation and hypothesis checks; avoid repeated full isolated Cargo builds where the tested source is identical. Measure the mutation rebuild separately before choosing a cache. |
| `ops/hxstore-observe-selftest.py` | keep | Count-only reader accounts for malformed/overlapping data without emitting private store values. |
| `ops/immediate-incident-selftest.py` | keep | Failure-to-incident correlation, locking and recurrence are atomic and idempotent. |
| `ops/incident-fingerprint-selftest.py` | keep | Incident identity and success-clears decision cannot clear the wrong failure. |
| `ops/incident-numbering-selftest.py` | keep | Concurrent incident refs retain correct day prefix and unique sequence. |
| `ops/incident-resolve-selftest.py` | keep | Incident resolution requires actual recovery evidence and permitted state transitions. |
| `ops/incident-sweep-selftest.py` | keep | Elapsed-window sweep cannot close incidents before their defined recovery window. |
| `ops/inherited-from-main-selftest.py` | keep | Failure hint performs one bounded read and never replays checks or hides an unavailable history. |
| `ops/integration-successor-selftest.py` | merge | Move pure migration allocation/union fixtures into migration-number-contract suite; preserve exact bytes, forward order and burned slots. |
| `ops/isometric-map-selftest.py` | keep | Topology renderer remains a current product/operations generator; no live map or provider operations occur in its fixture. |
| `ops/job-watchdog-digest-selftest.py` | keep | Digest does not repeatedly interrupt sessions with unchanged findings and ignores retired categories. |
| `ops/key-recovery-test-selftest.py` | keep | Synthetic key comparison precedes restore, failure/interrupt preserve evidence and cleanup; never substitute access to real recovery material. |
| `ops/launchd-calendar-selftest.py` | keep | Calendar cadence conversion is deterministic, rejects ambiguous templates and prevents synchronized job bursts. |
| `ops/launchd-hold-selftest.py` | keep | Held jobs cannot be reactivated through install/reload races or malformed definitions. |
| `ops/launchd-main-path-selftest.py` | keep | Declared active runtime paths never select feature branches or corrupt Git metadata. |
| `ops/launchd-plist-portable-selftest.py` | keep | All tracked launchd declarations must parse on hosted CI; consolidate small interpreter checks here. |
| `ops/lead-board-runtime-fork-selftest.py` | delete | Entire test only asserts a dated FORKED BY DESIGN comment in manifest.tsv. It exercises no renderer or runtime contract. |
| `ops/lead-stage-db-gate-selftest.py` | keep | Business lead-stage fixture must execute against disposable DB and propagate failure. |
| `ops/learning-credential-boundary-selftest.py` | keep | Weekly learning children receive only jobs credentials; writer credentials cannot be borrowed. |
| `ops/learning-monthly-credential-selftest.py` | merge | Parameterize weekly/monthly credential-boundary runner cases in one suite, retaining each distinct child command contract. |
| `ops/lint-gate-selftest.py` | keep | Still wired in current native hooks. Writing lint distinguishes internal/generated/scratch files and cannot leak a broken linter into sessions. |
| `ops/live-typer-actor-selftest.py` | keep | LiveTyper actor contracts are compiled and exercised without keyboard emission. |
| `ops/local-pg-ci-selftest.py` | keep | One local DB owner scrubs credentials, orders business proofs, propagates failures and always stops/removes its cluster. |
| `ops/local-verb-zero-install-selftest.py` | keep | Actual local CLI works without npm runtime on ordinary authenticated Worker calls; uses a synthetic loopback server. |
| `ops/loop-guard-selftest.py` | keep | Loop state/duplicate safeguards remain executable controls. |
| `ops/machine-converge-selftest.py` | keep | Secondary-machine bootstrap convergence preserves local work and does not silently operate from the wrong checkout. |
| `ops/machine-prerequisites-selftest.py` | keep | Runtime prerequisites are proven by behavior before install, including real Ed25519 support and safe output roots. |
| `ops/machine-role-selftest.py` | merge | Small marker-versus-Git-identity precedence fixture belongs in machine-converge/provisioning suite; preserve fallback order. |
| `ops/mail-extract-selftest.py` | keep | Mail capture is metadata-only, does not launch Mail or send messages, and distinguishes liveness from enumeration timeout. |
| `ops/main-canary-selftest.py` | merge | Move CI/main parity and scheduler/aggregate contract tests into one workflow contract suite; retain exact evidence reuse and noncanceling production canary semantics. |
| `ops/main-commit-gate-selftest.py` | keep | Real pre-commit hook refuses main without discarding staged work; isolated fixtures prove permitted branches remain usable. |
| `ops/migrate-dell-selftest.py` | fix | Local-only scope says launchd-dependent; retain provisioner contract locally and move portable source assertions into portable installer suite. |
| `ops/migrate-prod-support-selftest.py` | keep | Production proof connection resolution uses a mocked provider; errors redact and retired wrapper commands refuse. |
| `ops/migration-consumers-selftest.py` | keep | Baseline compaction retains migration logical identity across release, allocation, snapshot and role-grant consumers. |
| `ops/migration-number-contract-selftest.py` | keep | Collision, immutable history and successor allocation guards protect forward migrations. |
| `ops/migration-open-pr-gate-selftest.py` | keep | Earlier PR/main migration slot ownership remains unambiguous and names the next usable slot. |
| `ops/migration-order-gate-selftest.py` | keep | Forward numbering checks current main rather than stale fork history; unreadable base refuses. |
| `ops/migration-production-proof-selftest.py` | keep | Offline comparison checks exact ledger/schema/privilege equivalence and forbids hosted production access; PostgreSQL mode remains separate. |
| `ops/migration-reservation-selftest.py` | merge | Consolidate single-checkout reservation fixtures with migration-worktree-reservations; retain append-only ownership and no silent stale-slot reuse. |
| `ops/migration-safety-gate-selftest.py` | keep | Destructive SQL/lock risks, rollback notes and exact source ledger checks protect migration safety. |
| `ops/migration-shadow-selftest.py` | keep | Shadow compares admitted seeds and schema, uses correct PostgreSQL major and fails cleanup errors. |
| `ops/migration-worktree-reservations-selftest.py` | keep | Concurrent worktrees see one migration reservation authority and fail on unreadable shared inventory. |
| `ops/module-deletion-selftest.py` | merge | Delete historical absent-file/comment-wording assertions; move live rule-context import and removed-command consumer assertions into their retained product contract tests. |
| `ops/monthly-gate-selftest.py` | keep | Completion ledger stops duplicate monthly routine execution while unavailable ledger remains an explicit fail-open contract. |
| `ops/native-scheduling-selftest.py` | merge | Move three retained job cadence/entrypoint assertions into scheduled-jobs-check-selftest; avoid a separate retired-tick suite. |
| `ops/nightly-canary-record-selftest.py` | keep | Scheduled availability canary uses isolated records and reports actual outcomes. |
| `ops/nightly-capability-skip-selftest.py` | keep | Unprovisioned capability skips visibly without killing remaining nightly jobs. |
| `ops/nightly-step-timeout-selftest.py` | keep | Independent failures/timeouts preserve later scheduled jobs and truthful heartbeat outcomes. |
| `ops/no-client-deliverables-gate-selftest.py` | keep | Synthetic path patterns prevent client deliverables from entering public source. |
| `ops/onedrive-prepublish-wake-selftest.py` | keep | Bounded provider wake precedes export; failure cannot hang scheduled publication. |
| `ops/operatory-math-selftest.py` | keep | Rendered dental operatory economics retain arithmetic and visible output contracts. |
| `ops/ops-run-fixture-guard-selftest.py` | keep | Fabricated drill results cannot masquerade as production recovery evidence. |
| `ops/outage-drill-selftest.py` | keep | Recording outage preserves scheduled child failure and reports unavailable evidence. |
| `ops/outlook-atlas-selftest.py` | keep | Synthetic bundle extraction binds provenance and rejects symlinks/malformed metadata. |
| `ops/outlook-intake-copy-selftest.py` | keep | Copier confines reads/writes, rejects symlinks and preserves private mailbox copies. |
| `ops/outlook-intake-selftest.py` | keep | Contact identity, replay-safe activity writes, retry state and private temporary mailbox cleanup. |
| `ops/outlook-send-selftest.py` | keep | No accidental sends: suppression, account/recipient readback, idle guard and idempotent uncertainty. |
| `ops/outlook-triage-selftest.py` | keep | Unread messages and active user are protected; bounded filing preserves truthful counts. |
| `ops/ownership-issuer-credential-selftest.py` | keep | Issuer planner remains inert, scoped and secret-free before any future enablement. |
| `ops/partner-continuity-gate-selftest.py` | keep | Retained manual continuity/Drive-retirement verifier must reject missing partner/device evidence. |
| `ops/partner-ping-window-selftest.py` | keep | No skipped weekday ping; no unnecessary overnight database wakeup. |
| `ops/path-index-hygiene-selftest.py` | keep | Local path/index hook behavior remains qualified; keep advisory naming out of extra hosted jobs. |
| `ops/performance-budget-gate-selftest.py` | delete | Delete with ops/performance-budget-gate.py: orphan scalar/evidence-reference gate after release assurance purge; no retained executable caller. |
| `ops/platform-metering-gate-selftest.py` | keep | Retained restore branch creation is bounded and cleanup-bound; preserve actual cost safety. |
| `ops/platform-metering-policy-selftest.py` | keep | Metered dispatch inventory fails closed for missing or malformed registered policy. |
| `ops/pr-actor-selftest.py` | keep | Retained automatic PR handling preserves scope, owner identity and safe failure. |
| `ops/pr-description-check-selftest.py` | delete | Delete with non-empty PR-body merge gate; template prose is process metadata, while code/test invariants remain required. |
| `ops/pr-design-policy-selftest.py` | delete | Only asserts two SKILL.md files exist; no product or production behavior. Retain the skills and review policy. |
| `ops/pr-hygiene-selftest.py` | keep | Retained abandoned-PR reporting must distinguish active work from stranded state. |
| `ops/pr-size-check-selftest.py` | keep | PR-size note is already warn-only; preserve classification correctness if local advisory remains, do not add a required step. |
| `ops/prepush-floor-selftest.py` | keep | Local push runs exact floor/range and faithfully propagates failure. |
| `ops/program6-release-flag-selftest.py` | keep | Program 6 activation is config-bound and accurately reported by release identity. |
| `ops/record-call-selftest.py` | keep | Unattended verb adapter preserves refusal and idempotent contract envelopes. |
| `ops/recovery-point-selftest.py` | keep | Fresh, stale and unavailable backup evidence remain distinct. |
| `ops/refused-content-selftest.py` | keep | Declared active content routing cannot hide a refused canonical write at another path. |
| `ops/reinstall-launchd-calendar-selftest.py` | keep | Preserve pure dry-run and scoped/truthful apply assertions via the hostile-host wrapper once; remove its duplicate direct execution. |
| `ops/release-manifest-selftest.py` | keep | Release source, schema and independent DoctorCRE artifact identities are deterministic and verified. |
| `ops/release-schema-provenance-selftest.py` | keep | Recorded schema provenance is bound to source migration identity. |
| `ops/release-smoke-selftest.py` | keep | Release smoke binds the lane/SHA, validates complete business reads and never turns failures green. |
| `ops/renewal-source-adapter-selftest.py` | keep | Signed renewal ingress preserves exact source authentication and contract shape. |
| `ops/replay-fake-column-pin-selftest.py` | keep | Replay fakes accept extended SQL projections without hiding actual replay behavior. |
| `ops/repo-config-selftest.py` | keep | Fixture commands cannot corrupt the real checkout Git configuration. |
| `ops/restore-rehearse-record-selftest.py` | keep | Restore success/failure is durably recorded; preview/verify-only runs create no false receipt. |
| `ops/restore-rehearse-report-selftest.py` | keep | Report only complete isolated restoration; propagate cleanup/evidence failures. |
| `ops/retired-merge-service-selftest.py` | merge | Fold absent-daemon/registration checks into the retained retirement inventory test; remove separate historical executor suite. |
| `ops/retrieval-eval-selftest.py` | keep | Retrieval scoring and denominator arithmetic are qualified on synthetic fixtures. |
| `ops/retrieve-source-boundary-selftest.py` | keep | Normal retrieval uses canonical records and only explicit recovery reads Drive. |
| `ops/review-policy-selftest.py` | keep | Shared trusted-review and exact-SHA verdict parsing remains used by board surfaces. |
| `ops/review-tiers-selftest.py` | delete | Reader mcp-server/src/review-tiers.js is absent; generated module has no retained consumer. Delete generator, map, vectors and suite together. |
| `ops/rotate-credential-mint-selftest.py` | keep | Still tests retained rotation helpers, role identity, fail-closed entrypoints and private publication; removal of other mint helpers is not enough. |
| `ops/routine-credential-boundary-selftest.py` | keep | Unattended job credentials cannot borrow interactive/admin authority. |
| `ops/routine-cutover-selftest.py` | merge | Move active lead-signals cadence, private file mode and scheduler wiring to routine-runner tests; drop completed-cutover-only assertions. |
| `ops/routine-leads-selftest.py` | keep | Lead-signal routine preserves territory, contact identity and replay-safe proposals. |
| `ops/routine-runner-selftest.py` | keep | Scheduled routines lock, resume confirmed effects and stop uncertain replay. |
| `ops/rule-cards-selftest.py` | keep | Known rules remain available under caps, collisions, inherited routing and scope. |
| `ops/rule-claim-gate-selftest.py` | keep | Declared active rule-claim hook requires current-turn enforcement evidence without false cross-repo credit. |
| `ops/rule-classification-parity-check-selftest.py` | keep | Enforcement/admission representations agree; mutant and CLI failures remain visible. |
| `ops/rule-delivery-eval-selftest.py` | keep | Measured rule delivery scores have honest precision/recall and scope denominators. |
| `ops/rule-gold-label-selftest.py` | keep | Reviewed rule labels, corpus binding and incomplete evidence remain distinct. |
| `ops/rule-load-layer-check-selftest.py` | keep | Rule control map layer and binding invariants reject seeded mutations. |
| `ops/rule-pack-preuse-reselection-selftest.py` | keep | Rule lookup outages stay advisory; sponsor mismatch still blocks unauthorized work. |
| `ops/rule-projection-fixture-selftest.py` | keep | One observer reconstructs complete reviewed rules without per-row fingerprint machinery. |
| `ops/rule-route-coverage-selftest.py` | keep | Declared routes reach installed controls and verbs; missing implementations are named. |
| `ops/rule-scope-lookup-selftest.py` | keep | Store-backed selection preserves sponsor scope, known rules and freshness. |
| `ops/rule-triage-selftest.py` | keep | Retained triage source feeds rule-generation consumers; dry-run plan must not write. |
| `ops/run-all-canonical-selftest.py` | keep | Normal aggregate rebuild uses canonical lead/deal sources without implicit recovery feed. |
| `ops/run-scheduled-selftest.py` | keep | Scheduled wrapper records honest outcomes and spools failures without changing child status. |
| `ops/run-spool-selftest.py` | keep | Local durable run queue survives retries and preserves ledger-bound result identity. |
| `ops/scheduled-drive-writer-selftest.py` | keep | Normal scheduled writes cannot be redirected into poisoned legacy Drive paths. |
| `ops/scheduled-jobs-check-selftest.py` | keep | Loaded job cadence/runtime drift and unreadable evidence remain actionable. |
| `ops/scheduled-run-record-selftest.py` | keep | Scheduled exits become durable bounded run records under the jobs credential. |
| `ops/scheduled-step0-selftest.py` | keep | Scheduled skip decisions use current Chicago time and verified completion evidence. |
| `ops/schema-reconstruction-selftest.py` | merge | Keep source-ledger reconstruction; distinguish static mode in gates from --postgres migration mode, then give each one owner. |
| `ops/schema-snapshot-connection-selftest.py` | keep | Connection credentials stay out of command arguments. |
| `ops/schema-snapshot-control-catalog-selftest.py` | keep | Snapshot control catalog parity guards approve-rule reconstruction; static source coverage differs from DB permission proof. |
| `ops/schema-snapshot-currentness-selftest.py` | keep | Currentness comparison rejects meaningful schema drift and preserves exact boundaries. |
| `ops/schema-snapshot-rule-delivery-seed-selftest.py` | keep | Exact bounded rule-delivery seeds and activation scope must survive rebuild; historical source is still the reconstruction authority. |
| `ops/selftest-harness-selftest.py` | keep | Shared fixture harness propagates failures, cleans scratch data and parses hook results faithfully. |
| `ops/selftest-temp-cleanup-selftest.py` | merge | Move cleanup assertions to suite/fixture teardown; this wrapper reruns six collected suites plus two again via runpy. |
| `ops/session-brief-selftest.py` | keep | Session brief distinguishes current failures, unavailable evidence and recovered jobs. |
| `ops/session-crm-neon-selftest.py` | keep | Session work collectors preserve canonical identities and truthful stale/active summaries. |
| `ops/settlement-run-token-selftest.py` | keep | Destructive settlement scope, token identity and refusal boundaries remain qualified. |
| `ops/situation-retrieval-gate-selftest.py` | keep | Retrieval release threshold uses bound observations and rejects seeded regressions. |
| `ops/situation-retrieval-observation-selftest.py` | keep | Observed retrieval results bind the measured suite and candidate without live fixture guessing. |
| `ops/snapshot-role-coverage-selftest.py` | merge | Unify role-discovery/current snapshot assertions with tools/test-schema-snapshot-roles.py; preserve role closure and fixture mutants. |
| `ops/snapshot-seed-coverage-selftest.py` | keep | Snapshot seed analysis covers actual migration writes, dynamic SQL and dependency closure. |
| `ops/snapshot-seeds-selftest.py` | keep | Only reviewed applied seed columns may enter a rebuilt snapshot. |
| `ops/source-study-room-selftest.py` | keep | Model/retrieval subprocesses are confined; secrets, sockets, writes and escaped children are denied. |
| `ops/sql-scope-selftest.py` | keep | Destructive SQL refusal remains exact without blocking ordinary prose. |
| `ops/staging-database-credential-selftest.py` | keep | Staging credential publication remains crash/race-safe and role-scoped. |
| `ops/staging-jobs-dsn-selftest.py` | keep | Job recorder cannot inherit forbidden authority roles or fall back to wrong tiers. |
| `ops/stale-count-selftest.py` | keep | Static count checker detects real stale enforcement counts without false positives. |
| `ops/store-token-selftest.py` | keep | Token storage validates before atomic private publication; errors never disclose values. |
| `ops/studio-failover-selftest.py` | keep | Failover checks host scope, leader evidence and loaded jobs before any transfer. |
| `ops/studio-leader-selftest.py` | keep | Disposable PostgreSQL proof enforces sole active host and authorized transfers. |
| `ops/study-sources-selftest.py` | keep | Research results bind provider/model, reject credentials in URLs and preserve pending work on failure. |
| `ops/submodule-patch-dirt-selftest.py` | keep | Health distinguishes expected vendored patches from uncommitted source loss. |
| `ops/sync-core-rule-ids-selftest.py` | merge | Keep mutant generator tests; run real source/output parity once, removing duplicate real-tree check also called by ci.sh. |
| `ops/sync-skills-drift-selftest.py` | keep | Skill sync reports both destructive drift directions without touching the real Drive tree. |
| `ops/system-governance-selftest.py` | keep | Retained contracts preserve Dell capability without inventing mandatory co-approval. |
| `ops/tailscale-health-selftest.py` | keep | VPN health distinguishes verified connectivity from process presence. |
| `ops/tailscale-route-selftest.py` | keep | Missing route stays unhealthy until verified recovery, even if daemon is running. |
| `ops/trigger-grant-selftest.py` | keep | Mutation fixtures qualify trigger dependency/grant analysis; real trigger check in migration is complementary. |
| `ops/type-check-selftest.py` | keep | Type-check wrapper selects the locked runtime and refuses an empty scoped check. |
| `ops/untracked-anomaly-report-selftest.py` | keep | Untracked work classification is truthful and read-only. |
| `ops/vault-drift-watch-selftest.py` | keep | Retained export/recovery files are checked against authorized generations; isolate concurrency fixtures. |
| `ops/vendor-level-drift-selftest.py` | keep | Vendor relationship level is derived from recorded events, not invented judgments. |
| `ops/verb-count-selftest.py` | merge | Preserve clean no-node_modules registry import case inside verb-loss-guard-selftest; avoid another full Worker source copy. |
| `ops/verb-loss-guard-selftest.py` | fix | Keep exact-name retirement protection; replace three historical Worker archive/import reconstructions with pinned minimal registries where equivalent. |
| `ops/verb-loss-vs-base-selftest.py` | merge | Keep base-SHA lookup failures and removal cases in one registry-loss suite; share fixtures with verb-loss-guard-selftest. |
| `ops/weekend-quiet-gate-selftest.py` | delete | Hook is explicitly unwired by harness-retirement-selftest; delete hook, suite and stale rule/control/triage/baseline references together. |
| `ops/whose-work-selftest.py` | keep | Ownership attribution compares the correct remote branch and does not blame another session. |
| `ops/worktree-attribution-selftest.py` | keep | Unknown work ownership stays unknown; local and remote evidence is not conflated. |
| `ops/worktree-runtime-lock-selftest.py` | keep | Worktree cannot link npm runtime built from another lockfile. |
| `ops/worktree-self-plumb-selftest.py` | keep | Retained session-start plumbing preserves runtime isolation and visible failures. |
| `ops/worktree-venv-lock-selftest.py` | keep | Worktree Python dependencies cannot silently borrow a mismatched canonical lock. |
| `tools/test-assurance-slice-compiler.py` | keep | Focused invariants for the pure Assurance Slice Compiler v1. |
| `tools/test-browser-visual-gate-capture.py` | keep | Hermetic tests for the dependency-free real-browser visual gate runner. |
| `tools/test-build-calendar-access.py` | keep | bin/build-calendar-access.sh keeps a valid bundle instead of recompiling. |
| `tools/test-calendar-prebrief-unknown-attendees.py` | keep | Coordinator side of migration 0735: skip and count unknown attendees. |
| `tools/test-calendar-touch-matcher.py` | keep | The calendar matcher reads record contacts from the record layer's export views. |
| `tools/test-closest-first.py` | keep | Regression tests for the read-only closest-first command. |
| `tools/test-cloudflare-loop-watch.py` | keep | Loop watch: the local, once-a-minute E2E dead-loop killer. |
| `tools/test-cloudflare-spend-guard.py` | keep | Cloudflare spend guard: every branch runs against a mocked Cloudflare API. |
| `tools/test-codemap.py` | keep | Tracked-only safe discovery, symlink exclusion and CLI behavior are useful; opening-description completeness should remain advisory. |
| `tools/test-codex-job.py` | keep | Exercise the launcher with a real Git origin and a stand-in Codex CLI. |
| `tools/test-deal-reading.py` | keep | Offline product tests for the generated Deal Room's deal reading. |
| `tools/test-design-kernel.py` | keep | Deterministic acceptance checks for the shared CARR Design Kernel. |
| `tools/test-disposable-pg-fixture.py` | merge | Join ops/disposable-pg-selftest.py as nested fixture-budget case; same fixture owner, retain cross-process contention coverage. |
| `tools/test-doctrine-mirror.py` | keep | Behavioral coverage: serialize cell, document markdown has banner orders and filters sections. |
| `tools/test-dot-file-reports.py` | keep | Synthetic behavioral verification; never reads live Dot files or credentials. |
| `tools/test-dot-reader-credential.py` | keep | Private credential publication, recovery, redaction and revoke contract. |
| `tools/test-dot-reader.py` | keep | Dot SQL boundary proof on an owned disposable PostgreSQL cluster. |
| `tools/test-drift-export-ledger.py` | keep | test-drift-export-ledger.py — the vault-drift tamper verdict, decided against the export ledger rather than against a once-a-night snapshot. |
| `tools/test-export-keep-generation.py` | keep | Focused, synthetic checks for FileProvider-safe export generations. |
| `tools/test-export-publish-isolation.py` | keep | One unreadable cloud file must cost ONE target, not the whole sweep. |
| `tools/test-f03-fixture-replay.py` | keep | Guards against accidentally replaying complete F03 corpus during binding setup; direct protection against duplicate DB work. |
| `tools/test-f03-production-migration.py` | keep | Covered historical migration still defines active receipt/plan validator reconstruction; static digest and optional DB behavior differ. |
| `tools/test-flash-lifecycle.py` | keep | Offline lifecycle fixtures. No launchd command or model request can escape these tests. |
| `tools/test-flash-on-demand.py` | keep | Flash policy tests with isolated state and no live launchd or model traffic. |
| `tools/test-flash-prompt-rules.py` | keep | Interactive Flash preserves complete task context and continues through advisory selection gaps. |
| `tools/test-flash-run-rules.py` | keep | Flash uses the same store-scoped lookup and keeps known rules when selection advice is incomplete. |
| `tools/test-git-index-fixture.py` | keep | Staged source fixtures retain merge ancestry without copying unstaged edits. |
| `tools/test-handler-reads-are-granted.py` | keep | A table a handler reads directly must be granted to somebody. |
| `tools/test-lead-stage-job.py` | keep | Behavioral coverage: subprocess failures never disclose contact inputs, dry run is only a read. |
| `tools/test-needs-joe-local.py` | keep | The local half of the needs-Joe list: out/orch/needs-joe-or-wait.txt plus live PR state, re-derived from scratch on every board run so an item leaves the moment its PR closes or its line is removed. |
| `tools/test-orchestrator-publication-size.py` | keep | Behavioral coverage: secondary and invalid roles refuse every orchestrator door, repeated updates reuse page slots. |
| `tools/test-partial-render.py` | keep | Prove the partial-render contract for the CLAUDE.md gist block (PASS 1). |
| `tools/test-pii-guard.py` | keep | Synthetic integration cases for the public-source identity guard. |
| `tools/test-policy-epoch-fixture.py` | keep | Maintains planner estimates for rollback-only DB fixtures; protects suite runtime rather than adding a separate release gate. |
| `tools/test-policy-learning-envelope.py` | keep | Adversarial acceptance checks for the offline Policy Learning Envelope. |
| `tools/test-pr-incident-evidence.py` | keep | Hermetic tests for ops-record.py's pr-evidence pure logic (WR-000019 slice S1). No database, no gh binary, no network — extract_incident_refs and pr_evidence_decision are pure functions exercised directly, the same way. |
| `tools/test-progress-board-1380.py` | merge | Move untrusted-review cases into test-progress-board-github.py and evidence-free completion cases into consumer-proof suite. |
| `tools/test-progress-board-clean.py` | keep | Board cleanup previews, activity counts, and V1 publication behavior. |
| `tools/test-progress-board-consumer-proof.py` | keep | Completion requires an exercise receipt for the delivered capability. |
| `tools/test-progress-board-github.py` | keep | The board's GitHub reads: one GraphQL query per repository per refresh. |
| `tools/test-progress-board-legacy.py` | keep | Legacy disposition through the CLI and published History contract. |
| `tools/test-progress-board-refresh.py` | keep | Scheduled source observation survives legacy publication failures. |
| `tools/test-progress-board-size.py` | keep | Regression for the live board's duplicated watchdog command diagnostics. |
| `tools/test-progress-board.py` | keep | Behavioral tests for the progress-board command line surface. |
| `tools/test-progress-hierarchy.py` | keep | Behavioral coverage: archived slices do not hold completed requests open, backfill reuses existing implicit slice and completed request. |
| `tools/test-progress-orchestrator.py` | keep | Behavioral coverage: review return merge and release are distinct facts, projection excludes private payloads and keeps unknown explicit. |
| `tools/test-record-home-gate.py` | keep | test-record-home-gate.py — proves the record-home gate denies what it claims and, just as importantly, ALLOWS ordinary narrative editing. |
| `tools/test-research-sites-1646.py` | merge | Move command refusal and URL-redaction cases to guard/source-study owners; retain each exploit regression, retire PR-number wrapper. |
| `tools/test-retrieval-real-misses.py` | keep | Behavior checks for the read-only real-miss evaluator. |
| `tools/test-retrieve-situation-path.py` | keep | Regression check: the retrieval CLI uses the same database ranker as MCP. |
| `tools/test-run-lock.py` | keep | test-run-lock.py — the mutual-exclusion contract for bin/run-lock.sh. |
| `tools/test-rw02-signin-1342.py` | merge | Move synthetic password/MFA page-mismatch matrix into existing salesforce-browser-read-rw02.v5 Node suite; retain all cases. |
| `tools/test-salesforce-active-snapshot.py` | keep | Behavioral coverage: active snapshot plans phase and unpark without inventing full ids, counterpart owner is a market agent and partner override wins. |
| `tools/test-salesforce-browser-refresh.py` | keep | Behavioral coverage: complete capture saves loader compatible private snapshot and sync is idempotent, signout and incomplete capture never import or replace last success. |
| `tools/test-salesforce-deal-sync.py` | keep | Behavioral coverage: missing opportunity proposes create and partner owner, id wins over name and updates only changed values. |
| `tools/test-schema-snapshot-execution-seeds.py` | keep | Pin the bounded execution seeds carried by bin/schema-snapshot.sh. |
| `tools/test-schema-snapshot-grants.py` | keep | test-schema-snapshot-grants.py — db/schema.sql must carry the app roles' PRIVILEGES, not just the roles. |
| `tools/test-schema-snapshot-roles.py` | keep | test-schema-snapshot-roles.py — db/schema.sql must carry the roles its grants depend on. |
| `tools/test-section-index-store-classification.py` | keep | test-section-index-store-classification.py — regression set for the Phase 1 store-vs-file classification (2026-08-13, doctrine-store build). |
| `tools/test-smoke-private-fixtures.py` | merge | Combine with smoke-profile/transport fixtures; retain private request config, stable replay and output redaction. |
| `tools/test-snapshot-seed-coverage-committed.py` | keep | Run the seed-coverage check against the COMMITTED db/schema.sql. |
| `tools/test-system-cost-publish.py` | keep | Cost collector publication through its command/module seam, without providers. |
| `tools/test-system-costs.py` | keep | Behavioral coverage: ai fixture costs use vendor money units, first of month checks last complete day for spike. |
| `tools/test-system-work-cache.py` | keep | Behavioral coverage: refresh call budget does not grow with stale branches, both repos open prs and stale unmerged branches. |
| `tools/test-visual-recipe-gallery.py` | keep | Acceptance checks for the rendered CARR Visual Recipe comparison gallery. |
| `tools/test-visual-recipe-library.py` | keep | Deterministic acceptance checks for the CARR-owned Visual Recipe Library. |
| `tools/test-worktree-plumb.py` | keep | test-worktree-plumb.py — the regression set for `bin/worktree.sh --plumb` (added 2026-08-14 alongside hooks/worktree-self-plumb.py). |
| `tools/test-worktree-sweep.py` | keep | test-worktree-sweep.py — the regression set for bin/worktree.sh's --sweep reap gate and its CREATE PATH FRESHNESS fix (both added 2026-08-14). |
| `tools/test-writing-lint.py` | keep | Behavioral coverage: typography and wrapping keep the hard ban, shared network and unrelated names are not attribution. |
| `tools/test_codex_job_skills.py` | keep | Send-time skill selection against temporary catalogs and a fake SDK. |
| `tools/test_credential_env.py` | keep | Unit proof for tools/credential_env.py — the shared long-lived-token loader every unattended `claude -p` launcher in this repo uses. |
| `tools/test_dictation_speaker_split.py` | keep | Guards the dictation rig's within-track speaker labels. |
| `tools/test_displacement_turn_filter.py` | keep | Guards the partner-turn filter behind displacement baselines 1, 2 and 5. |
| `tools/test_generated_merges.py` | keep | Two source branches merge through regen without hand-editing projections. |
| `tools/test_github_rate_limit.py` | merge | Share fixture/owner with ops/github-rate-limit-selftest.py; preserve absolute-slot/delay schedule and completion-cooldown cases. |
| `tools/test_job_watchdog.py` | keep | Read-only local watchdog behavior through its collection and scan interfaces. |
| `tools/test_local_jobs.py` | keep | Explicit local job execution remains independent of watchdog scans. |
| `tools/test_migrate_baseline.py` | merge | Run once with migration-static tests; currently gates plus every non-fixture migration partition discover it again. |
| `tools/test_migrate_startup.py` | merge | Run once with migration-static tests; startup/refusal behavior is duplicated by each DB partition discovery. |
| `tools/test_migration_baseline.py` | merge | Run once with migration-static tests; immutable filename/hash/source coverage is required, repeated executions are not. |
| `tools/test_migration_claims.py` | merge | Run once with migration-static tests; preserve truncated-provider refusal and deterministic open-PR slot ownership. |
| `tools/test_migration_source.py` | merge | Run once with migration-static tests; preserve pinned historical-source recovery/checksums after SQL purge. |
| `tools/test_pg_env_exec.py` | keep | Tests for tools/pg-env-exec.py: the URL moves into PG* variables, never argv. |
| `tools/test_regen.py` | keep | Regeneration executes owning commands, stops on failures and converges. |
| `tools/test_restore_watermark.py` | keep | Tests for tools/restore-watermark.py and bin/restore-rehearse.sh phase 5 (V5-F08 item 4). |
| `tools/test_workflow_checks_write_grant.py` | keep | Only backup workflow may write provenance Checks; guards a forged backup-evidence path across workflow changes. |
| `tools/feature-switch-health-selftest.py` | keep | Behavioral coverage: missing check verb warns that deployment is pending, every other worker failure remains unknown. |
| `tools/health-check-findings-selftest.py` | keep | health-check-findings-selftest.py — offline proof of tools/health-check.py's --findings-json schema, added for PR #1237's independent-review fixes (points C, D on the finding schema; the "every rc=1 path emits a hard_error. |
| `tools/renumber-at-merge-selftest.py` | keep | Exercise merge-time renumbering through its CLI in isolated Git repositories. |
| `tools/uptime-health-selftest.py` | keep | Behavioral coverage: refused or malformed loop read reports error, dropped monitor loop does not block a new fault. |
| `tools/room-bridge/test_engineering_passport_unit.py` | keep | Acceptance tests for the offline Engineering Passport v1 vertical slice. |
| `tools/room-bridge/test_execution_contract_unit.py` | keep | Offline acceptance tests for the provider-neutral execution fabric v1. |
| `tools/room-bridge/test_execution_environment_unit.py` | keep | Offline acceptance tests for governed execution-environment providers. |
| `tools/room-bridge/test_activation_reliability.py` | keep | Behavioral coverage: bundle is bounded and deterministic, required omission stale conflict block and advisory not applicable. |
| `test/doc_drift/test_checker.py` | keep | Checker semantics cover path/verb/job references, negation and unchecked data; scheduled advisory drift still needs correct scanner. |
| `test/doc_drift/test_ci.py` | keep | Pinned scanner checksum and forwarding prevent misleading drift reports; remove its extra cleanup-wrapper rerun only. |
| `test/doc_drift_product/test_integration.py` | keep | Retain product wrapper manifest/baseline/known-drift and no-issue-write behavior; do not make drift a product merge veto. |
| `ops/migration-production-proof.py` | fix | Remove from generic local gates; keep explicit release preflight only. It resolves production credentials and may schema-dump production. |
| `tools/test-smoke-profile.sh` | merge | Combine smoke-profile/transport/private-fixture orchestration tests under one owner; preserve read-only probe versus partner-write separation. |
| `tools/test-smoke-transport.sh` | merge | Combine with smoke-profile test; retain distinct timeout/non-JSON/auth-failure outcomes. |

## Appendix D — Database gates, acceptance and direct proofs

### Migration dispatch and shared helpers

All are source-reviewed; local execution was withheld under the dump restriction. Their time is included in the partition measurements and individual unchanged-main failure rates are unknown.

| Helper / step | Decision | Invariant and exact simplification |
|---|---|---|
| `ops/migration-open-pr-gate.py` | Keep once | Open-PR/main slot ownership; remove duplicate workflow and partition calls only when one base/head-bound, fail-closed preflight owns them. |
| `python -m unittest discover -s tools -p 'test_migr*.py'` | Merge | Retain all five baseline/startup/source/claim tests once in migration-static; remove repeated discovery in each partition and gates. |
| `ops/migration-order-gate.py` | Keep once | Immutable migration source, numbering and collision checks before any database work. |
| Loopback DSN guard, psql/schema prerequisites | Keep | Refuse nonlocal DB targets and missing setup; no credential fallback. |
| `psql ... -f db/schema.sql` and `tools/migrate.py --apply --yes` | Keep / share isolated preparation | Candidate snapshot must restore and every pending migration must apply; do not share mutable state across role-changing or committing tests. The audit did not open or restore the dump. |
| Inline app-role grant sentinels | Keep | Writer lead INSERT, jobs ops USAGE and incident.state UPDATE, denied resolved_at UPDATE, reader state_as_of EXECUTE and exporter membership. Run once per prepared schema/role baseline. |
| `ops/trigger-grant-check.py` | Keep / fix transport | Actual firing roles can read trigger dependencies, including column-specific grants; batch repeated psql connections, retaining negative controls. |
| `tools/workflow-registry.py sync` and `tools/ops-record.py sync-registry` | Keep fixture prerequisites | Populate the real workflow/service registry before dependent proofs; execute once per isolated fixture preparation. These are local fixture writes only in CI. |
| `# ci: db-gate` discovery and missing-marker rejection | Keep | No DB-reading gate silently disappears; replace repeated regex inventories with the shared executable inventory only when coverage union is proved. |
| `ops/ci-migration-gates.py` | Keep | Serial vs isolated clusters, required pre-isolation roles, Dot/SIEP sharding, exact declared-scenario union and failure propagation. |
| `ops/schema-reconstruction-selftest.py --postgres` | Keep | Bootstrap/refusal/source-ledger reconstruction; distinct from its cheap non-Postgres mode. |
| `ops/migration-production-proof-selftest.py --postgres` | Keep / shrink fixtures | Offline equivalence engine detects ACL/owner/role/ledger drift; use tiny synthetic comparator mutations plus one full-snapshot path. Never substitute the live production-proof command. |
| `ops/migration-shadow.py` + `ops/schema-snapshot-rebuild-test.py` | Keep / share valid setup | Base snapshot plus pending migrations equals candidate; an independent refresh/rebuild preserves reviewed seeds and new timestamps while excluding business data. |
| `ops/local-pg-ci.py` + `ops/db_acceptance_programs.py` | Keep / simplify interface | Own disposable cluster lifecycle, cleanup and nine acceptance consumers; plain ci.sh acceptance alone is insufficient. Preserve optional integration-base restore/forward/consumer verification when locally requested. |

### All 41 currently discovered database gates

Paths in this table are under `ops/`. “Keep” means preserve the assertions, not necessarily the existing file/process split. Runtime and unchanged-main failure frequencies are recorded at their containing CI class/partition in the main report; this review did not invent per-file timing.

| Gate | Decision | Concrete invariant / change |
|---|---|---|
| `calendar-prebrief-projection-local-pg-gate.py` | Keep | Authenticated sponsor/lease and exact signed snapshot replay; allowlist revision invalidation; no cross-sponsor/canary leakage; concurrent attestation dedupe. Also provisions roles needed by isolated SIEP fixtures. |
| `capability-completion-gate.py` | Keep | Serving-role completion can read immutable verification evidence without requiring an impossible UPDATE lock. `complete-capability-project` remains in runtime source. |
| `completion-register-schema-local-pg-gate.py` | Keep | Completion is derived from coherent evidence; conflicting/disposition state ordering and tenant-isolated projection. |
| `control-catalog-parity-gate.py` | Keep | Installed enforcement catalog agrees with compiled repository controls, including test/implementation references and verified installed state. |
| `control-plane-registry-gate.py` | Keep | Live workflow rows match manifest, including authority-managed calendar activation; genuine native-observation and engineering-claim consumers remain. |
| `cre-lifecycle-local-pg-gate.py` | Keep | J102 SQL truth table and real-verb/role attribution, concurrent reconciliation, migration shadow; separate disposable sibling keeps committed fixtures isolated. |
| `definer-hardening-local-pg-gate.py` | Keep / fix shared setup | Live definer bodies schema-qualify application dependencies and catalog/seal changes fail closed. Different from the search-path and Dot executable-door tests. Avoid revalidating unchanged historical seals in every unrelated proof once common immutable-chain verification owns that assertion. |
| `delivery-cadence-a05-assurance-reader-local-pg-gate.py` | Keep | `morning-brief` reader can call cadence batch but cannot read private raw notification tables. |
| `doc-outcome-cards-local-pg-gate.py` | Keep | Bounded, stable cursor pages; six state mappings; exact job/attempt joins and actor/tenant authorization. |
| `dot-database-design-db-gate.py` | Keep / trim only mapped duplicates | Private-conversation access, merge survivorship, optimistic updates, acyclic allocations and concurrent parent races, WIP/budget serialization, cache correctness, temp-object safety and timeline index use. Many retained assertions are product data invariants even though the suite also mentions engineering. |
| `engineering-claim-local-pg-gate.py` | Keep | Real runtime claim CTE, lease bounds, actor/session/lineage locking and generic terminal-door refusal. `mcp-server/src/engineering-runtime.js` still calls `ops.engineering_claim_slice`; current manifest still has enabled `engineering-slice`. Removal of desk wrappers does not prove this seam removed. |
| `evidence-activation-db-gate.py` | Keep | Actual database activation/refusal constraints and replay on evidence-backed records; retain until its registered SQL/runtime consumers are retired, not merely based on “evidence” in filename. |
| `gate-runtime-role-gate.py` | Keep | Fixture role switching cannot silently retain owner privilege; tests the actual app-role semantics on which other DB gates depend. |
| `guidance-registry-db-gate.py` | Keep | Typed guidance revisions, authority-bound approval, append-only lifecycle, scoped read surfaces and stale/import rejection. |
| `joe-answer-work-request-gate.py` | Keep | Human answer transitions derive authority from actual login and refuse writer/spoofed identities; one answer advances exact request version. |
| `lead-stage-db-gate.py` | Keep / merge wrapper | Runs `mcp-server/test/lead-automation.postgres.mjs` under loopback guard; keep stage behavior, use common test manifest instead of a dedicated 16-line subprocess wrapper if simplifying runner. |
| `legacy-program-tenant-backfill-gate.py` | Merge | Keep present-day tenant/source-shape constraints and closed-row immutability negative cases in Work Request/tenant suite. Delete repeated historical migration-source string checks and one-time 0314 census/repair rehearsal once those invariant assertions are moved; baseline immutable history already pins 0314. Do not discard closed-row/forged-tenant cases. |
| `party-identity-local-pg-gate.py` | Keep | Correct-party-identity must detect shared organizations, ignore tombstones, count client sharing and keep writer-only helper privileges. |
| `private-row-sponsor-rls-gate.py` | Keep | Partner personal-row write fences with the current cross-partner read policy; guards owner-column enumeration and actor spoofing. |
| `program3-incident-gate.py` | Merge | Fold unique incident opening/recurrence/severity/no-hypothesis/skip semantics into incident-recovery acceptance; same ops-record entrypoint, preserve all assertions and reader trace. |
| `program3-trace-gate.py` | Keep | Failed critical journey shows ordered deployment/check/job/incident chain; stale evidence is unknown, not healthy; reader cannot write ops records. |
| `program6-browser-action-challenge-gate.py` | Keep | One-time browser challenge is exact-authority/action-bound and replay-safe, with no raw challenge-ledger access. |
| `program6-human-triage-gate.py` | Keep / merge fixture | Captured-to-triaged authority and source/version refusal; share Program 6 setup with answer/plan tests but keep transition-specific negatives. |
| `program6-outcome-feedback-gate.py` | Keep / merge fixture | Pending proposals are inert/private; accepted outcome readback/replay and shape bindings remain exact. |
| `program6-ready-plan-gate.py` | Keep | Includes positive proof that bounded/successor plans no longer need retired admission/review lifecycle; prevents accidental reintroduction as well as enforcing immutable plan/replay boundaries. |
| `program6-sourced-routine-gate.py` | Keep / merge fixture | Sourced Work Request provenance/current source and tenant/authority invariants. |
| `program6-sourced-shape-disposition-gate.py` | Keep / merge fixture | Exact shape disposition with authenticated caller and correct source/version. |
| `renewal-decision-delivery-gate.py` | Merge then delete | Current schema returns after small safe-view/redaction/role checks. Move those to lease gate; remove dead pre-0279 signed-market branch and file. |
| `renewal-lease-ledger-local-pg-gate.py` | Keep once | Authenticated executed-lease renewal, sponsor transfer and superseded history. Delete duplicate acceptance-manifest execution. |
| `resource-observation-local-pg-gate.py` | Keep | Real writer/read dashboard path, capacity values and stale-vs-unavailable semantics; fake client cannot prove installed SQL shape/grants. |
| `salesforce-reconciliation-rw02-local-pg-gate.py` | Keep / merge wrapper | Runs RW02 SQL fixture for page-stop/replay/append-only grants; distinct from handler/store Node tests. Common SQL proof manifest can remove subprocess-only wrapper. |
| `self-closing-records-gate.py` | Keep | Work Request completion requires all bound acceptance evidence; no early/forged completion and no still-open queue entry; defects close with exact resolution evidence. |
| `siep12-policy-epoch-local-pg-gate.py` | Keep / fix cost | Current authorization policy epoch fails closed on doctrine, schema, grant or role drift; monotonic epochs and token compatibility. Use shared immutable catalog/chain precondition, keep runtime authorization negatives and isolated clusters. |
| `siep18-reference-monitor-local-pg-gate.py` | Keep / fix split | Retained reference monitor grant/guard binding, sourced shape correction and incident Work Request behavior. Split monolithic fixture by independent domain only if all declared cases remain mandatory. Do not delete because SIEP-11/13/14/15/17 were purged. |
| `situation-retrieval-db-gate.py` | Merge | Keep ranked/filtered doctrine retrieval behavior in one SQL search suite with zero-hit fallback, preserving strict near-miss/no-hit cases. |
| `tour-pdf-render-result-storage-ref-local-pg-gate.py` | Merge | Fold valid storage-ref boundary, overlength refusal and stored-ref equality into retained Tour delivery SQL proof; it guards a real prior hosted rendering failure. |
| `work-portfolio-local-pg-gate.py` | Keep | Production-shaped login/role authority, JS/SQL canonical digest parity and corrupt/ambiguous portfolio refusal. Complements transaction-only portfolio SQL test; not equivalent. |
| `work-request-withdrawal-db-gate.py` | Keep | Decline/supersede terminal transitions preserve history and refuse stranded evidence, including cross-session supersession races. |
| `workflow-cutover-r02-local-pg-gate.py` | Keep | Workflow cutover SQL doors and caller retirement use production-shaped single-bundle logins. `mcp-server/src/workflow-cutover.v5.js` remains a real caller despite unrelated completed cutover helper purge. |
| `zero-hit-fallback-db-gate.py` | Merge | Merge with situation retrieval suite; preserve strict default semantics, fallback only on genuine zero strict results, and explicit fallback provenance. |
| `zz-engineering-controller-concurrency-gate.py` | Keep / fix brittle timing | Actual receipt/claim/currentness/expiry and successor races across real connections, declared lock ordering, exact lease/session retirement. Replace polling/short wall-clock race windows with DB barriers where needed; no observed flake rate is established by source alone. |

## Nine acceptance programs after ci.sh

The explicit manifest is `ops/db_acceptance_programs.py`. Their runner is `ops/local-pg-ci.py`, not the `ci.sh` loop alone.

| Program and path | Decision | Unique assertion |
|---|---|---|
| continuity — `mcp-server/test/codex-continuity.test.mjs` | Keep | Handler/database continuity roundtrip and resume semantics; not identical to checkpoint schema SQL. |
| leads — `mcp-server/test/lead-workspace-pg.test.mjs` | Keep | Lead workspace real data/read/write behavior. |
| rule-delivery — `ops/rule-delivery-local-pg-acceptance.py` | Keep | Actual map-to-store sync, nonempty boot/pack selection, partner scope, absent/retired rule refusal and epoch-safe installation. |
| calendar — `ops/calendar-canary-local-pg-acceptance.py` | Keep | Lease-bound canary receipts, concurrent exact/mutated replay and no changes to live activity. |
| nightly — `ops/nightly-canary-local-pg-acceptance.py` | Keep | Newest availability snapshot, exact lease/source receipt, no canonical event/activity/export changes and real concurrent replay. |
| renewal-ingress — `ops/renewal-signed-ingress-local-pg-acceptance.py` | Keep narrow / reconsider with capability retirement | Dedicated attestor-only ingress, malformed/lease refusal and exclusion of signed-market rows from current lease reader. Workflow is disabled, but adapter and SQL capability survive; deleting the test alone is not equivalent to retiring that capability. |
| renewal-lease — `ops/renewal-lease-ledger-local-pg-gate.py` | Delete duplicate invocation | Already a required discovered DB gate; keep file and execute once there. |
| incident — `ops/incident-recovery-local-pg-acceptance.py` | Keep / merge incident gate | End-to-end ops-record as `carr_jobs`, fingerprint recurrence, recovery, two-success closure and no direct resolved-at write; fold Program 3's unique assertions here. |
| snapshot — `bin/schema-snapshot.sh --from-disposable-local ... --verify-only` | Keep / merge setup | Regeneration verifies exact ledger/seed/structure with no business data. Coordinate with shadow/rebuild tests, but preserve export behavior of the real script. |

## Direct Node/SQL product proof inventory

Every currently present direct proof named by the `check_migration` source was read for its executed assertions or test cases. These protect durable product behavior or live role boundaries. A fake unit client cannot replace the live database proof. A Node and SQL pair often intentionally test different boundaries (handler envelope versus database grants/constraints); merge process setup only after mapping exact assertions.

| Paths under `mcp-server/test/` | Decision / assertion |
|---|---|
| `find-rule-supersedes.test.mjs`, `one-step-rule-approval.test.mjs` | Keep real-PG portions: authenticated rule lookup/supersession/approval, exact replay and rollback. Offline portions may run once in unit. |
| `catch-me-up-writer-route.test.mjs` | Keep: all timeline queries work under carr_writer read-only route. |
| `national-account-classification-correction.test.mjs` | Merge narrow one-time correction checks into reconstruction's correction regression; preserve fail-closed linked-work/census and rollback evidence before removing the separate run. |
| `security-definer-search-path-postgres.sql` | Keep: trusted path policy across every definer, runtime schema CREATE refusal, mutation controls. |
| `definer-temp-substitution-postgres.sql` | Keep: real historical vulnerable positive control plus current cadence function resists same temp-object attack. |
| `completion-tenant-barriers-postgres.sql` | Keep: optimizer/error expression cannot expose another tenant row; includes positive leak control. |
| `definer-hardening-catalog-postgres.sql` | Keep / merge invariant owner: live head seal and policy agree; unsealed metadata refused. Move historical seal enumeration into a once-per-candidate catalog precondition shared with definer/SIEP tests, retaining mutation controls. |
| `dot-security-definers-postgres.sql` | Keep / fix cost: actual dot_reader session executes every reviewed definer and proves no table/sequence mutation, including positive unsafe definer control. Full catalog coverage cannot be replaced by sampling. |
| `invoice-tracker-transaction.test.mjs`, `invoice-tracker-postgres.sql` | Keep: handler transaction/replay/race/rollback and actual SQL invoice lifecycle. |
| `tour-operations-slice2-postgres.sql` | Keep: source/rights-gated sealing, client projection, cross-language digest, immutable facts/ACL. |
| `tour-property-identity-jurisdiction-postgres.sql` | Keep: identifiers and tenant foreign keys, evidence time, supported jurisdictions and authority-only entrance verification. |
| `tour-domain-route-cheat-sheet-postgres.sql` | Keep: route review digest, appended-stop refusal and locked appointment/property preservation. |
| `tour-delivery-data-plane-postgres.sql` | Keep / merge render-ref regression: promotion/share readiness, rejection, stale cart, render/share field safety and grants. |
| `tour-client-share-allowlist-postgres.sql` | Keep: exact client field/value allowlist and unsafe legacy projection refusal across list/PDF/map. |
| `assurance-health-store-postgres.sql` | Keep: current assurance health store refusals; do not confuse with deleted rehearsal producer bindings. |
| `work-portfolio-postgres.sql` | Keep: graph/child/accepted digests and immutable accepted structure. Role boundary separately proven by Python gate. |
| `local-deals-postgres.sql` | Keep: real local-deals query/data contract. |
| `governed-correspondence-store-postgres.sql` | Keep: consent, actor/partner privacy, human-send-only inert drafts and no raw runtime writes. |
| `record-source-authority-live-pg.v5.test.mjs` | Keep: actual F01 registered verbs, stale/recycled identity/overwrite refusals, immutable evidence and document state readback. It runs `document-derivative-registration-postgres.sql` and `record-source-authority-postgres.sql` on separate template copies. |
| `f01-login-bundle-membership-postgres.sql` | Keep: real login bundle classification; writer/read/exporter/jobs spoofing boundaries. |
| `rule-context-runtime-postgres.test.mjs` | Keep: append-only scoped contracts, missing projections explicit, Joe-only binder identity and stale-rule-version refusal. |
| `codex-continuity-postgres.sql` | Keep / share setup: checkpoint schema/read/write/grant assertions. |
| `program-controller-census-role-boundary.v5.test.mjs`, `program-controller-census-postgres.sql` | Keep pending explicit seam retirement: real writer/reader/authority admission/census and exact lease/grant/idempotency boundary; absent Gate Zero tests are a separate cleanup. |
| `doc-conversation.test.mjs`, `doc-conversation-postgres.sql` | Keep: verb reachability/envelope/store and database actor/ACL/idempotency behavior. |
| `doc-conversation-write-doors-postgres.sql`, `doc-conversation-list-postgres.sql` | Keep: creator-only mutation, withdrawal preserves audit, DB grant matrix, actor-derived list and stable pinned cursor. |
| `notifications.test.mjs`, `r03-notifications-postgres.sql` | Keep: handler/store notification lifecycle plus actual mint/recipient/ACL boundary. |
| `r03-loop-queue-postgres.sql` | Keep / fix harness: notification producer failure/retry/dedupe and actor mismatch; replace ci.sh's CSV-position AWK assertions with named SQL or structured test assertions, preserving historical vs unresolved failure distinction. |
| `notification-preferences-postgres.sql` | Keep: correct STABLE read, default/no-write behavior, CAS/replay, quiet hours/timezone and partner identity. |
| `session-identity.test.mjs`, `session-identity-postgres.sql` | Keep: all source books correctly actor-filtered, parent-known distinction and bounded age semantics. |
| `dispatch-spine.test.mjs`, `dispatch-spine-postgres.sql` | Keep: derived hermes actor, per-dispatch ack/reason, immutable link/ack uniqueness and no wire-row mutation. |
| `meeting-mode.test.mjs` | Keep DB path: durable meeting mode behavior under actual store and role. |
| `amend-closed-loop-postgres.test.mjs` | Keep: real locks/CAS, immutable amendment history, version increments and envelope replay. |
| `independent-review-cycle-postgres.test.mjs` | Keep pending explicit runtime retirement: actual review-cycle data/identity constraints; removed Python review tiers are not this DB contract. |
| `a02-rule-enforcement-postgres.test.mjs` | Keep: live rule coverage and fallback writer SQL. |
| `salesforce-reconciliation-rw02-postgres.test.mjs`, `salesforce-read-run-store-rw02-postgres.test.mjs` | Keep: store replay, malformed write refusals, real reader route, attended ledger/reset/stop/consent revocation and invoice absence scope. |
| `vendor-directory-postgres.test.mjs` | Keep: bounded local DB guard plus installed contract, statistics/override/audit/replay/CAS and stable pagination across all sorts. |
| `relationship-network-postgres.test.mjs` | Keep: relationship graph real store contract. |
| `approve-rule-legacy-adoption-postgres.test.mjs` | Keep: active legacy rule adoption real-PG path; removed approval wrappers do not remove this registered product verb. |
| `foundation-assurance-minimum-postgres.sql` | Keep pending explicit storage retirement: candidate/evidence atomic binding, replay and wrong-key refusal, distinct from removed source producer. |


### Already-purged coverage, excluded from savings

Between dca1863a and 804a3794, source removed nine previously auto-discovered DB gates: `atomic-rule-compat-migration-gate.py`, `authority-privilege-gate.py`, `p0-1-release-gate.py`, `program5-forward-fix-rehearsal-gate.py`, `program5-promotion-gate.py`, `release-readiness-gate.py`, `rule-admission-db-gate.py`, `snapshot-authority-membership-gate.py`, `staging-release-readback-gate.py`. It also removed `atomic-rule-approval-local-pg-acceptance.py` and its manifest entry, and P1 out-of-CI gates/selftests. Historical comparisons that still include those runs overstate the current suite cost. Source baseline `db/migration-baseline.json` is through `0888_supersede_triaged_work_request.sql`; it is a metadata manifest, not the forbidden snapshot dump.


### Exact dead conditional proof filenames

Delete only the absent-file runner blocks in `ops/ci.sh` for `mcp-server/test/foundation-assurance-oracle-role-boundary.v5.test.mjs`, `gate-zero-outcome-role-boundary.test.mjs`, `gate-zero-outcome-record-race.test.mjs`, `gate-zero-outcome-digest-tagged.test.mjs`, and `gate-zero-candidate-authority-filing.test.mjs`. The files themselves are already absent. Preserve shared required switches used by remaining programs.
