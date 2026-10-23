# Post-purge integrity audit — 2026-10-23

Report only. The most serious confirmed defect is a pre-commit regression: deleting approval machinery also removed `hookdir`, silently disabling three retained checks. The current SCAC dispatch registry and direct entry-point file references are otherwise consistent within the static scope below. Remaining work is stale enforcement/configuration declarations, a secondary action-risk inventory, selftests preserving retired execution paths, operational guidance, and coordinated SQL retirement.

## Ranked implementation order

| Order | Slice | Priority | Result |
|---|---|---|---|
| 1 | E1 | P1 | Restore three accidentally skipped pre-commit checks with one initialization line. |
| 2 | D1 | P1 | Repair recovery/migration instructions that call a deleted executable. |
| 3 | E2, E5, R1 | P2/P3 | Reconcile enforcement claims and the stale action-risk baseline; delete the orphan review-tier generator family. |
| 4 | T1, E4, D2 | P2/P3 | Remove test-only fleet helpers and the scheduler wrapper's fleet proof; correct AGENTS. |
| 5 | T2 | P2 | Test the actual Codex launcher and retire the former council review stack. |
| 6 | T3 | P2 | Remove retired scheduler canary paths while preserving normal nightly availability matching. |
| 7 | E3, D3 | P3 | Delete skipped CI references and repair a historical documentation link. |
| 8 | S1, S2, S4, optional S3 | P2/P3 | Drop three exclusive scheduler ledgers and optionally one no-consumer cache column with forward SQL. |
| 9 | Retained ceremony / DB-C1–3 | P2, coordinated | Retire doors/functions only after their remaining callers and seal dependencies are addressed. |

P1 means an existing check, operational instruction, or writer-to-reader contract is broken; P2 means consequential stale machinery or misleading coverage; P3 means dead guarded branches or documentary debt. “Ready” means a bounded source fix can be implemented from this evidence, not that the report applies production changes. DB-C1's writerless schedule projections are **not** in a safe-drop slice.


## Scope, assumptions, and evidence boundary

- Audited repository: `DoctorCRE/carr-system`, fetched `main` pinned to [`3e91aef47f8a97731befc4a0f46ee7dc0ca36db5`](https://github.com/DoctorCRE/carr-system/tree/3e91aef47f8a97731befc4a0f46ee7dc0ca36db5). All `file:line` references refer to this commit, not the initially attached, older checkout. Report date requested by the user: 2026-10-23 (America/Chicago).
- The request contained an interleaved integrity audit and schema audit. Both were treated as intended. Each report is delivered in its own draft PR containing only the requested Markdown file; no proposed runtime fixes or migrations are implemented here.
- Read `AGENTS.md` before repository work. Its evidence rules apply. The explicit user instructions to write these reports, open draft PRs, and avoid production effects take precedence over its generic record-layer-only writing and merge/delivery policy. The required codebase-design and zero-tech-debt skills were read and applied to the reports. No CARR record verbs, approval requests, production calls, database connections, or deployments were used.
- Reviewed source and Git history, using deletions since 2026-10-20 to seed the scan and following older history when a current defect required it. A reference to a deleted fixture name, an immutable historical migration, an explicitly archived document, a generated output with a surviving producer, or an external installed executable is not by itself a defect.
- The audit uses repository source, not installed macOS jobs, deployed verb discovery, another repository's application source, database statistics, or live database contents. “No caller” means no caller found in the stated source collections. Dynamic dispatch, external database clients, applied migration frontier, actual data retention, and live query plans remain unverified.
- The worktree helper created isolated trees but refused shared Python and Node runtimes because their installed packages did not match the pinned locks. That is an environment limitation, not a missing-source defect. Checks below used static parsers, dependency-free tools, and hermetic fixtures. Full strict CI and database reconstruction were not run; this report-only change does not modify runtime behavior.

- Final `main` refresh: `804a3794fb120dcfec980e11b958d205d90f6130` (#2178) adds `tools/codemap.py`, its test, a `run.sh map` entry, and a warning-only CI description check. The complete four-file delta from the audit pin was reviewed: new direct targets/imports resolve, it contains no SQL/schema changes, and it does not repair or invalidate the findings below. Line evidence remains pinned to `3e91aef4` for reproducibility.


## Slice E1 — repair silently disabled pre-commit checks (P1)

**Finding:** removing the bless gate also deleted the shared `hookdir` assignment. `ops/githooks/pre-commit:56–61` tests/runs `$hookdir/../ci-secret-scan.py`; `:93–97` tests/runs `$hookdir/path-hygiene-check.py` and `index-upkeep-check.py`. No assignment remains. In ordinary invocation with no externally exported `hookdir`, these become `/../ci-secret-scan.py`, `/path-hygiene-check.py`, and `/index-upkeep-check.py`, and the `-f` guards skip all three. The main-branch check still runs, so a success exit masks the lost staged-secret/path checks.

**Purge provenance:** `git show 1755c2fb -- ops/githooks/pre-commit` shows PR #1801 (2026-10-16) deleting `hookdir="$(dirname "$0")"` with the bless block. This predates the last-few-days window but remains broken at the audited main commit.

**Exact fix:** add `hookdir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"` immediately after `PROTECTED="main"` at line 37. Do not restore the removed bless gate. Add a focused hook fixture invoking the real shell hook with harmless marker helpers in a temporary Git repository and no inherited hookdir.

**Verification performed:** temporary Git repo on branch `audit`, three benign marker helpers at the real relative paths, `/bin/sh` invocation and an explicit minimal child environment. Current hook: exit 0, **0 helper calls**. The same temporary copy with this one-line fix: exit 0, **3 helper calls**. No real credential scanner or production repository hook was invoked.

## Slice E2 — reconcile enforcement inventories with actual removal (P2)

**Finding:** `ops/config/rule-enforcement-map.json` still declares eight controls whose implementation or test files were deleted. Offline `ops/sync_control_catalog.py:156–200` compilation returns all eight as `installed=false` with `declared but not tracked by git`; it does **not** falsely mark them installed. Their prose/route inventories still advertise enforcement that no longer exists.

| Control | Evidence in the map | Missing references |
|---|---|---|
| `control-plane-workflow-manifest` | `ops/config/rule-enforcement-map.json:372–380` | `ops/control-plane-selftest.py`, `ops/control-plane-db-gate.py` |
| `draft_export_read` | `:411–417` | `hooks/draft-export-gate.py`, its selftest |
| `github-burst-guard` | `:438–444` | `hooks/github-burst-guard.py`, its selftest |
| `model_floor` | `:494–500` | `hooks/model-floor-gate.py` |
| `one_repo` | `:512–520` | `hooks/one-repo-gate.py`, `hooks/bash-write-gate.py`, both selftests |
| `record_home` | `:565–576` | `hooks/bash-write-gate.py`, `hooks/write-effect-check.py`, both selftests; the actual record-home hook remains |
| `verification_status` | `:655–664` | `tools/control-plane.py`, `lib/control_plane_inputs.py`, their two checkers |
| `weekly_social_route` | `:687–695` | `lib/control_plane_runner.py`, `tools/social-batch-candidates.py`, control-plane runner selftest |

Related stale claims: `ops/config/rule-triage.v1.json:235,626,816,1319` still says these paths machine-enforce rules; `ops/config/rule-routes.v1.json:374,1186` names removed social/control-plane paths. The model-floor configuration explicitly names its deleted reader (`ops/config/model-floors.json:2`) and is retained solely as a hashed contract (`hooks/gate-integrity.py:30`); no runtime model-floor reader was found.

**Consequence:** catalog sync would report eight uninstalled controls, and readers of the declaration/triage files get obsolete enforcement guidance. This is configuration debt, not evidence of eight product outages or a live database state.

**Exact fix:** retire deleted-only catalog keys (`control-plane-workflow-manifest`, `draft_export_read`, `github-burst-guard`, `model_floor`, `one_repo`, `verification_status`, `weekly_social_route`) and their class-sidecar entries, replacing associated rule-controls/load-layer claims with the retained prose/product behavior where still applicable. For `record_home`, retain `hooks/record-home-gate.py` and its real test and remove the four deleted references. Remove obsolete social/control-plane route matches. Delete `ops/config/model-floors.json` and its `gate-integrity.py` CONTRACTS entry together; update the hash baseline and any generated projections from those actual source changes. Do not reintroduce deleted gates merely to satisfy the inventory. If database catalog rows are later reconciled, use a new forward migration, not edits to historical SQL. No migration is proposed/applied by this report-only slice.

**Verification performed:** imported only the pure compiler and called `compile_catalog()`: **42 rows**, eight `declared but not tracked` results above. `python3 ops/ci-enforcement-wiring-selftest.py`: **4 tests passed**. That test covers only controls whose failure mode contains `CI`, so it does not catch these stale runtime-hook declarations.

## Slice E3 — remove dead guarded CI steps and stale onboarding probes (P3)

`ops/ci.sh:790–808,959–987` has five `if [ -f ... ]` blocks naming absent tests:

- `mcp-server/test/gate-zero-outcome-role-boundary.test.mjs`
- `mcp-server/test/foundation-assurance-oracle-role-boundary.v5.test.mjs`
- `mcp-server/test/gate-zero-outcome-record-race.test.mjs`
- `mcp-server/test/gate-zero-outcome-digest-tagged.test.mjs`
- `mcp-server/test/gate-zero-candidate-authority-filing.test.mjs`

**Consequence:** these branches are skipped and do not fail CI, but the script carries obsolete acceptance/proof claims and dead environment-variable wiring. Some removals date to #1806 on October 16; do not attribute every stale block to the newest PRs.

**Exact fix:** delete those five complete guarded blocks. Preserve adjacent rule-context, codex-continuity, program-controller and foundation-minimum tests that still exist. At `bin/migrate-dell.sh:508`, delete the `run_check "git-writer gate" "ops/git-writer-gate-selftest.py"` invocation; the deleted test is currently skipped by `run_check`'s file guard at `:500`. Clean the three workflow comments that justify zsh by the deleted council runner (`.github/workflows/ci.yml:142`, `main-canary.yml:105`, `backup-nightly.yml:238`) but keep zsh installation: retained scripts still require it.

## Slice E4 — delete fleet-specific self-proof left in the general scheduler wrapper (P3)

`bin/run-scheduled.sh:31–60` recognizes service `fleet-sync`, `com.carr.fleet-sync`, and the exact deleted `$REPO/bin/fleet-sync.sh` tuple. `:68–78` can reject the child as `ACTIVE-SELF PROOF REFUSED` and instruct an operator to reinstall the deleted plist. `bin/fleet-sync.sh` was deleted in #2150 (`798e200a`). The new `ops/launchd/com.carr.checkout-pull.plist:19` runs ordinary Git directly.

**Consequence:** no product caller reaches the fleet-specific branch. It is dead approval/proof machinery carried by every scheduled job wrapper. Ordinary run recording still serves product jobs and must remain.

**Exact fix:** delete the fleet-only proof block at lines 31–60 and replace the conditional at lines 68–78 with the existing child invocation and exit capture (`"$@"; rc=$?`). Preserve timestamps, exit-state mapping, heartbeat throttling and `tools/ops-spool.py` recording. Remove the stale fleet-sync promises/comments in `hooks/machine-converge.py:15,48,173` as part of the same slice. Actual fleet helpers/tests are handled in the separate orphan-test audit.

## Slice E5 — prune current-config claims about absent sources (P3)

- `ops/config/drive-dependencies.v1.json:195–213` still declares `normal-control-plane-env` with only deleted `tools/control-plane.py`. `:290` retains removed `tools/report-card.py` in `normal-client-artifacts`; `:413–414` names removed `tools/check.sh`, `tools/smoke.sh`; `:549–550` names removed bash-write/control-plane tests. The registry is still read by `ops/drive-dependency-inventory.py:25` and source-boundary selftests. **Fix:** remove the deleted-only entry and prune only missing `sources` elements from mixed entries; revise verification prose to describe current remaining sources.
- `ops/config/review-tiers.v1.json:31,34,36` has exact-path rules for deleted `source-merge-policy.js`, `run-source-merge.mjs`, and `lib/review_tiers.py`; `:3,10` describes removed merge-controller/Stop-hook consumers and automatic-merge refusal. **Independent import-graph confirmation:** #2168 deleted the JavaScript reader and #2149 deleted the Python reader; no source module imports the generated review-tier module. **Fix:** delete the complete seven-file orphan unit: `ops/config/review-tiers.v1.json`, `mcp-server/src/review-tiers.generated.js`, `ops/sync-review-tiers.py`, `ops/review-tiers-selftest.py`, and the three JSON fixtures under `ops/fixtures/review-tiers/`. `ops/review-tiers-selftest.py:16` only checks that the orphan generated artifact matches its orphan source; preserving that check does not preserve product behavior.
- `ops/config/studio-failover-inventory.v1.json:50–61,98–107` records installed control-plane/fix-train jobs whose repository definitions were removed. This is an old capture, not evidence those jobs remain installed today. **Fix:** archive/label the capture as historical, or regenerate from a later explicitly authorized machine read; do not present its `disabled:false` fields as current operational facts.

## Verified clean and scope limits

- Every literal source-file target in `run.sh` resolves, including its remaining direct Python-module dispatches. No missing purged target was found there.
- The only tracked `.claude/settings*.json` file is `.claude/settings.json`; its three hook commands reference existing `rule-claim-gate.py`, `worktree-self-plumb.py`, and `machine-converge.py`. PreToolUse is intentionally empty in that project file. This does not certify installed global settings.
- All **41** tracked plist templates parse: **27** `ops/launchd` plus **14** `ops/headless-tasks`. All 41 direct repository argv references in launchd resolve to source or declared generated runtime outputs. Generated `.venv/bin/python` and Swift `.build/release/quill-dictate` were excluded from deleted-file findings. External Homebrew/macOS executables and local `outlook-intake-copy` were not claimed installed.
- All **14** headless task IDs have a matching plist and `ops/scheduled-tasks/<id>.SKILL.md`; there are no extra headless plist IDs.
- All six GitHub workflows' direct repository script references resolve. The obsolete council-runner names are comments, not executed steps. No workflows were run, so this is not a hosted-CI health claim.
- `ops/ci.sh` direct missing paths found in this audit are the five explicitly guarded deleted tests above. Generic selftest globs and dependency build outputs are not missing-source findings.
- No installed launchd state, deployed hook state, live database rows, external service availability or performance was inspected. These source findings are bounded to the pinned main commit.

Additional negative checks: an expanded scan of **650** tracked source/config files in `ops`, `bin`, `hooks`, workflows and project settings found no additional unconditional deleted-program invocation after separating comments, fixture data and migration-history seeds. This scan excluded credential/token-named metadata and files over 1 MB. `ops/config-as-code.py:567–576` deliberately recognizes the removed `codex-continuity-hook.py` as a CARR-owned hook so old installed entries can be removed; keep that ownership matcher. `ops/program3-trace-gate.py:134–144` uses `bin/deploy-worker.sh` only as synthetic deployment provenance in a transaction rolled back by the fixture, not as a command invocation. It should be renamed to current synthetic provenance when touching that test, but it is not a missing executable failure.


## T1 — P2: two fleet helpers survive only because their selftests call them

**Evidence.** The fleet scheduler/runner was deleted by `798e200a` / PR #2150. `tools/fleet_sync_sibling_safety.py:34` still implements `sync_sibling()`, including fetch and fast-forward at lines 65–87. Its only remaining executable importer is `ops/fleet-sync-siblings-selftest.py:43`; that suite embeds the deleted runner's old dispatch snippet at lines 50–63 and calls eight sibling-sync cases at lines 252–264. `tools/fleet_sync_safety.py:38` defines the exact Quill tree proof, and lines 186–218 retain the old standalone fast-forward CLI. The sole remaining external executable call to that helper is `ops/fleet-sync-selftest.py:13`, which imports the proof and runs ten cases at lines 245–258. Searches covered tracked `bin/`, `hooks/`, `lib/`, `tools/`, `pipelines/`, `ops/`, `mcp-server/src`, `.github/`, and `.claude/`; fixture references are not product callers.

**Consequence.** The gates class still discovers both suites through `ops/ci.sh:246` (`ops/*-selftest.py`). Green tests preserve an updater implementation with no current product or scheduled caller. The Quill test's description was intentionally narrowed during #2150, but its proven function remains unused outside the test.

**Exact source-only slice.** Delete `tools/fleet_sync_sibling_safety.py`, `ops/fleet-sync-siblings-selftest.py`, `tools/fleet_sync_safety.py`, and `ops/fleet-sync-selftest.py`. Keep `tools/health_submodule.py` and `ops/submodule-patch-dirt-selftest.py`: the health product still imports `health_submodule` at `tools/health-check.py:26`. No explicit CI list edit is needed because collection is by glob. The separate obsolete fleet-specific branch in `bin/run-scheduled.sh` belongs in the entry-point cleanup slice.

**Verification.** Seven sibling-sync cases passed against disposable, local Git fixture repositories. The environment-logging eighth case was deliberately not run under the audit's prohibition on printing environment variables. Fixture commit identities were set to `jbookout <jbookout@users.noreply.github.com>`. No real sibling checkout or remote was touched. The exact Quill suite was reviewed statically, not run.

## T2 — P2: review-council selftests keep a retired invocation stack alive and omit the real launcher

**Evidence.** `ops/codex-hook-smoke-selftest.py:146–149` requires the smoke to source `bin/council-lib.sh` and call its `run_precheck` or `run_codex` function. Lines 211–231 assert the hook-trust flag on those old wrappers and on `pipelines/run_codex_review.py`. The production launchers `bin/precheck.sh`, `bin/council.sh`, and `bin/review-council-runner.sh` were deleted in `c9ab3788` / #2154. The remaining executable caller of `bin/council-lib.sh` is the smoke itself (`ops/codex-hook-smoke.sh:71,144`), plus `ops/council-grok-selftest.py:34–35`. The review pipeline's remaining executable importer is the smoke selftest (`ops/codex-hook-smoke-selftest.py:221–227`); its internal helper is `pipelines/review_findings_ledger.py`, imported at `pipelines/run_codex_review.py:220`.

The replacement job launcher does include the hook-trust flag (`bin/codex-job:40–41`), but the selftest's purported all-sites scan only opens `.sh` and `.py` files (`ops/codex-hook-smoke-selftest.py:248`) and searches the literal regex `codex\s+exec` (`:260`). The extensionless launcher and its list-form command are both outside that proof. The health row still describes the old wrapper as the automation's invocation helper (`tools/health-check.py:2592–2601`) and accepts a stored smoke receipt at lines 2618–2621.

**Consequence.** This is a coverage defect and surviving ceremony, not a current missing-module failure. The offline selftest passes while proving the old review path; a regression in the actual job launcher's flag would not be caught by its all-sites assertion. A PASS on the old wrapper does not verify the replacement launcher's environment or installed hook behavior.

**Exact replacement slice.** Replace the smoke selftest's council/review assertions with a hermetic launcher test that intercepts subprocess execution and asserts the final `bin/codex-job` argument vector. Make the live smoke exercise the same retained launcher command construction or retire that old live-smoke receipt and its health row together. Delete `pipelines/run_codex_review.py` and `pipelines/review_findings_ledger.py` once that sole selftest import is removed. After removing the smoke's wrapper dependency, delete `bin/council-lib.sh` and `ops/council-grok-selftest.py`; do not delete `bin/grok-run.sh`, which has separate current callers. Preserve the actual `guard-unattended.py` safety control and native hook configuration.

**Verification.** `PYTHONDONTWRITEBYTECODE=1 python3 ops/codex-hook-smoke-selftest.py` exited 0; all 28 checks passed. No live Codex/Grok call or network probe was run. This passing result is evidence of stale coverage, not a claim that live hooks were verified.

## T3 — P2: lease-bound nightly canary tests preserve the removed generic scheduler's proof path

**Evidence.** `bin/nightly.sh:6–35,40` retains the control-plane-only `--canary` branch. `pipelines/availability_matcher.py:67,74–198,250–257` retains its protected-snapshot protocol, canary output directory, and control-plane mode. Its source-defined producer was the generic scheduler removed in #2100; outside migrations, the canary contract now appears only in those implementations, `ops/config/control-plane-workflows.v1.json:188–194`, `ops/nightly-canary-record-selftest.py`, and `ops/nightly-canary-local-pg-acceptance.py`. The latter still appears in `ops/db_acceptance_programs.py:17`; `ops/local-pg-ci.py:495–506` runs declared acceptance programs. The selftest explicitly asserts the old job-definition-v3 lease and canary tables at `ops/nightly-canary-record-selftest.py:94–104`.

**Consequence.** CI preserves scheduler admission/receipt behavior after the scheduler's removal. This is distinct from the current product: ordinary nightly availability matching still runs at `bin/nightly.sh:91`, and `pipelines/availability_matcher.py:277–345` performs the real availability/search joins and matching.

**Exact source slice.** Remove `run_canary()` and the `--canary` case/usage option from `bin/nightly.sh`. Remove `CANARY_BASE`, `_canonical_digest`, `_safe_canary_root`, `_read_canary_snapshot`, `canary_report`, `_run_canary`, and the `--canary` option/dispatch from `pipelines/availability_matcher.py`; then remove imports used only by those helpers. Delete `ops/nightly-canary-record-selftest.py` and `ops/nightly-canary-local-pg-acceptance.py`, and remove `Program("nightly", "ops/nightly-canary-local-pg-acceptance.py")` from `ops/db_acceptance_programs.py`. Rewrite/remove the manifest's canary contract in the same slice. Preserve `matches()`, the normal matcher, direct nightly scheduling, and product matching tests. Defer canary table/function drops to the schema report's dependency-checked migration slices; historical SQL is not edited.

**Verification limit.** These two canary suites were not executed: the shell branch invokes `env` at `bin/nightly.sh:17`, and the DB acceptance suite opens a database. The source test also exits 78 before any assertions when the worktree's `out/` is symlinked (`ops/nightly-canary-record-selftest.py:33–37`). Do not describe that exit as a passing canary test.

## T4 — P2: a mixed database gate still spends coverage on retired cache/cost machinery

**Evidence.** `ops/control-plane-retirement.md:8–18` explicitly identifies provider routes, cognition cache/observations, and cost reservations/refusals as having no remaining runtime consumer while historical SQL/tests remain. `ops/dot-database-design-db-gate.py:355–377` exercises cache collision, settled monthly cost reservations, and cache invalidation. Lines 537–560 exercise competing cost reservations. The called function names have no remaining match in `mcp-server/src`, `lib/`, or `tools/`. This gate remains eligible through the `# ci: db-gate` discovery in `ops/ci.sh:1038–1048`.

**Consequence.** Schema cleanup must prune the retired scenarios or CI will demand obsolete APIs. Deleting the entire gate would discard unrelated product isolation and constraint coverage.

**Exact coordinated slice.** When the corresponding cache/cost SQL APIs are retired, remove only `test_12_cache_collision_preserves_matching_metadata`, `test_13_month_boundary_counts_settled_reservations`, `test_14_invalidation_counts_changed_cache_rows`, `test_11_budget_serializes_competing_reservations`, and `test_11b_budget_competing_fixed_snapshots_preserve_limit`, plus fixtures exclusively needed by those cases after a call-site check. Retain, for example, `test_15_cadence_does_not_read_temporary_actor` (`:379`) and participant-side product constraint coverage (`:384`). This is not permission to drop shared `ops.job*` tables.

**Verification limit.** Static review only; database gate execution is outside this audit.

## Verified clean or deliberately retained

- `ops/retired-merge-service-selftest.py` passed 2 tests. These assert that the removed daemon cannot reappear in executable/service surfaces (`:21–38,41–66`); they do not exercise the removed daemon implementation. Keep them.
- `ops/harness-retirement-selftest.py` passed 4 tests. They verify current safety configuration, removal of old continuity hooks, the current catalog, and native core-card delivery (`:12–61`). Keep them.
- `ops/gate-integrity-selftest.py` passed 7 tests. Its `CHANGED: hooks/guard-unattended.py...` output is an intentional negative fixture, not a modification or failure in the audited tree.
- Historical file names such as `ops/ai_eval.py` inside `ops/built-unclosed-selftest.py:83–104` are created beneath a temporary fixture root and are not missing imports. That suite's current subject `ops/built_unclosed.py` still exists. Do not file those strings as broken file references.
- `mcp-server/test/work-request-ready-plan.test.mjs:82–83` uses removed source filenames as opaque authorized-path fixture values. That alone does not make the suite broken. Its proposal/acceptance handlers remain registered; removal is a separate product/ceremony scope decision.
- `mcp-server/test/engineering-program-controller-admission-door.test.mjs:18–21` tests still-present registered `engineeringRuntimeTools`; `mcp-server/src/engineering-runtime.js:2280–2284` retains the admission handler. The engineering stack may warrant a broader ceremony retirement, but its tests cannot truthfully be labeled imports of deleted code.
- Test discovery is broad, not a fixed CI checklist: 270 tracked `ops/*-selftest.py` files match the primary gates glob at this revision. `mcp-server/run-tests.mjs:17–24,35–39` discovers `*.test.js`/`*.test.mjs` under `mcp-server/test`. No full suite or live/DB test was run for this audit.


## Registry, imports, and retained ceremony audit

Audited commit: `3e91aef47f8a97731befc4a0f46ee7dc0ca36db5`.

### Verified clean, with boundaries

- **All 365 assembled MCP handlers match all 365 current generated registry rows.** Each `TOOLS` entry has a function-valued handler; there are zero generated operations without handlers and zero handlers without generated rows. Source locators, canonical input-schema SHA-256, write, human-only, and authority-only flags all match. This includes the dispatch requirement at `mcp-server/src/mutation-registry.js:48` and the registration seam at `mcp-server/src/tools.js:9150`. The current projection remains `scac-mutation-registry.v118` (`mcp-server/src/scac-mutation-registry.current.generated.js:3`). The read was an offline assembly of the actual modules in a Node VM: database constructors and network functions throw, and the VM receives an empty synthetic `process.env` rather than the host environment. No handler was called and no database or production service was accessed. This verifies repository source, not the deployed runtime or the database seal.
- **All 13 MCP profiles resolve.** `mcp-server/src/mcp.js:123` defines `full` plus 12 explicit sets. Their explicit set sizes are `doc=20`, `codex-continuity=2`, `capture=17`, `away=31`, `read=0`, `probe=0`, `reviewer=1`, `benchmark-author=0`, `benchmark-reviewer=0`, `hermes=11`, `unattended=23`, `hermes-cos=13`; every named verb exists. The separate eight-entry unattended authority allowlist (`mcp-server/src/tools.js:8966`) also has zero missing handlers. `FEATURE_VERBS`' two names (`mcp-server/src/feature-switches.js:7`) and the four system-authority mappings (`mcp-server/src/global-boundaries-door.v5.js:134`) all resolve. Empty benchmark profiles are vestigial candidates, not missing-handler errors.
- **Tracked Claude MCP permission allowlists are clean.** Parsed only the permission arrays from all four tracked settings files (`.claude/settings.json` plus `claude-tree/settings/{carr-ai-project,my-drive-root,user}.settings.json`). `.claude/settings.json:63-116` contains 54 explicit CARR MCP allow entries, representing 18 verbs under each of the `carr`, `claude_ai_CARR_Record_Layer`, and UUID connector aliases; every verb exists in the 365-handler registry. There are no explicit MCP wildcard entries and no stale CARR verb permissions in these files. The four file-service permissions at `claude-tree/settings/carr-ai-project.settings.json:39-42` belong to a different connector (`search_files`, `read_file_content`, `get_file_metadata`, `list_recent_files`) and were excluded from CARR handler comparison; their external connector availability was not checked. The other two settings templates contain no MCP permission names. Shell permission wildcards are not verb advertisements. This is a source-template check, not verification of installed user settings.
- **JavaScript module links checked with an AST, not textual import grep.** Acorn parsed 554 tracked `.js/.mjs/.cjs` files, inspected 2,372 literal static/dynamic imports and requires (1,075 relative), and resolved 2,804 named/default relative imports against declarations and export-star chains. No missing named export was found. Three n8n workflow files use top-level `return`; their parser mode was adjusted to allow their wrapper convention. The only missing literal modules after stripping URL query suffixes are `practice-plugin/.build/planner-resource.mjs` in the resource module and two tests. This is **generated, not deleted**: `practice-plugin/build-ui.mjs:9-10` writes it and `practice-plugin/package.json:7-9` runs build before tests. Test imports of `identity.js?...` resolve to the existing module with cache-busting query parameters.
- **Python direct imports checked statically:** 7,131 AST import statements across the source scan; no unresolved relative import and no unresolved absolute import whose module name matches a Python file deleted in Git history since 2026-10-20. The combined Python/JS scan covered 1,383 tracked programming files; its conservative filename exclusions include credential/token/keychain code names and `db/`. This is bounded evidence, not proof about arbitrary dynamically constructed import names or optional external packages. No repository Python was imported or executed for this check.


### Retained ceremony: bounded, source-only candidates

These are registered runtime surfaces, so they are **not missing-handler findings**. Searches covered executable source in `mcp-server/src`, `lib`, `tools`, `ops`, `bin`, `hooks`, `control-room`, `workspace`, and `dealroom`. No live usage telemetry was read. Generic MCP access can reach registered verbs from external clients, so “no product caller found in this repository” is the strongest supported claim.

| Rank / proposed slice | Evidence and consequence | Exact source change proposed; dependency boundary |
| --- | --- | --- |
| P2 / remove the obsolete merge-authority read door | `mcp-server/src/engineering-runtime.js:2308-2311` still advertises `source-merge-authority` as a packet derived from a “Joe-accepted plan-hash file scope” and Engineering receipt/reviewer generations. Its only executable source references are its own implementation, its own handler, and the generated registry; no DoctorCRE app, morning-brief, Leads, intake, vendor, or deal-room caller was found. This preserves the old source-delivery ceremony in tool discovery after ordinary PR delivery replaced it. | Remove the `source-merge-authority` entry from `engineeringRuntimeTools`; remove the now-unreferenced `resolveSourceMergeAuthority` helper and any helper/constants it alone owns; regenerate the current registry with `node ops/scac-mutation-inventory.mjs --write-current`. Remove/update only the tests proving this retired door. Do **not** delete all of `engineering-runtime.js`: `lib/progress_hierarchy.py:466-475` still calls `engineering-passport`, and `dealroom/js/job-passport.js:581` validates the passport projection. SQL retirement is a separate dependency-checked forward migration, not historical migration edits. |
| P2 / retire Work Shape implementation ceremony as a coherent slice | `mcp-server/src/work-shape.js:185-224` records and freezes a disposition; `:227-269` requires exactly three archetypes, 2–3 searches, at least five maintained repositories, and an approximately 120-word brief, then writes `ops.work_shape_revision`. `mcp-server/src/tools.js:10305` still registers the module. No executable product caller of `write-work-shape` or `set-work-shape-disposition` was found; they remain generic MCP tools. They enforce source-work form rather than a CRE product invariant. | Retire the `read-work-shape`, `set-work-shape-disposition`, and `write-work-shape` registry entries and module registration together; remove the import and their serialization cases at `mcp-server/src/tools.js:331`; remove the trace mapping for `set-work-shape-disposition` at `mcp-server/src/trace.js:100`; regenerate the registry. **Not safe as a verbs-only deletion:** `mcp-server/src/capability-program.js:397-402` still refuses a return to `ready` without shape disposition and explicitly names the SQL `ops.work_request_shape_gate`; the current SQL gate and sourced-plan functions must be forward-replaced in the same implementation slice. Rewrite the generated shape instructions at `mcp-server/src/capability-program.js:59-64` too. Preserve or explicitly migrate the Work Inventory reader of `ops.work_shape_revision` (`mcp-server/src/work-inventory-census.v5.js:141-160`) before any table retirement. Keep product Work Request capture/card paths. |
| P2 / isolate obsolete engineering admission/review from remaining read projections | `mcp-server/src/engineering-runtime.js:2255-2290` retains immutable plan registration, job/capability/envelope admission, and separate reviewer facts. No executable DoctorCRE product caller of `register-engineering-slice-plan`, `admit-engineering-slice`, or `review-engineering-slice` was found. The surviving scheduled-task text `ops/scheduled-tasks/engineering-slice.SKILL.md:24,34` still prescribes admission and review; that is a remaining documentation consumer. | Retire those three mutation entries and their exclusive implementation only after replacing/removing that scheduled-task contract and their SQL dependency chain. Keep the Work Inventory reader of `ops.engineering_slice_plan` (`mcp-server/src/work-inventory-census.v5.js:169-185`), and keep `engineering-passport`/`engineering-progress` until the progress reader is migrated (`lib/progress_hierarchy.py:471`). Regenerate the current registry and remove exclusively matching ceremony tests. Do not call the entire module orphaned or drop its tables while these readers remain. |
| P3 / portfolio acceptance ceremony candidate | `mcp-server/src/work-portfolio.js:940-961` requires a fresh passing independent review and three distinct proposer/reviewer/acceptor identities for `accept-portfolio-revision`, then calls `ops.portfolio_accept_revision`. No executable product caller of this verb was found outside its handler/registry. This retains a human approval surface for engineering portfolio records. | Remove the `accept-portfolio-revision` door and exclusive approval paths only as a coordinated portfolio retirement, after tracing the surviving portfolio readers and acceptance-based workflow evidence. Do not drop the portfolio relations merely from absent UI calls: `mcp-server/src/workflow-cutover.v5.js:351` explicitly supports portfolio acceptance as evidence. |

### Explicitly not orphaned / preserve until product changes

- `accept-ready-plan` is still exposed as a concrete app route: `POST /api/system-work/:WR/plan/accept` dispatches to it at `mcp-server/src/program6-routine-controller.js:32`; outcome acceptance has a route at `:34`. They may conflict with desired simplification, but a no-product-caller claim is false.
- Engineering Passport reads remain used by the progress hierarchy (`lib/progress_hierarchy.py:471`) and have a dealroom representation (`dealroom/js/job-passport.js:581`). Their presence prevents a blanket table/module drop.
- Business approvals are distinct from source-delivery ceremony: lead-draft/lead-move approval, share grants, deal identity merges, OAuth consent, and CRE representation evidence still have product paths. The audit does not recommend removing those by name matching.


## Slice R1 — mark the secondary action-risk registry as a historical baseline (P2)

`control-room/contracts/action-risk-registry.v1.json:6` claims “one row per action the runtime can perform,” but its 141 verb rows include four removed tools: `attest-capability-project` (`:263`), `begin-capability-project` (`:276`), `prepare-capability-project` (`:1043`), and `start-capability-project` (`:1654`). Comparing it with the 365 assembled current handlers finds **228 current handlers absent** from this secondary projection. The actual SCAC dispatch registry remains 365-for-365 correct; this is a stale derived configuration/audit inventory, not a runtime allowlist that exposes nonexistent handlers.

The stale claim survives checking: `ops/action-risk-guardrails.py:104-133` validates a pinned digest and 13 baseline dispositions, and its safe offline invocation returned `status=valid`, `rows=13`, `selected_slice=set-lead`. The validator explicitly says it is not a dispatcher (`:2-7`). The generator's promise that its output cannot drift (`ops/action-risk-registry.py:11-14`) does not establish checked-in freshness. Blind regeneration would introduce another error: non-inline handlers receive zero SQL counts (`:117`) and unversioned writes can be classified append-only (`:107-108`) without tracing their implementations.

**Consequence:** a reader can mistake a green historical contract check for a complete current action-risk census. The missing and retired rows are not four production call failures.

**Exact fix slice:** label v1 as the frozen phase-1 baseline in the generator documentation and its consumers; replace the artifact's current-runtime claim with an explicit historical-baseline purpose/status and provenance for its source snapshot (last artifact change: `e1dcf5126b56c0ca762152ca6b7de31d3c3213cb`, which is not proof of the original classification date). Preserve all baseline verb/disposition payloads; update the bound artifact digest only for the reviewed metadata change. Point current handler-existence readers to `scac-mutation-registry.current.generated.js`. Do not present the baseline validator as freshness coverage, delete historical dispositions, or regenerate risk classifications until delegated handlers are traced correctly. This cleanup needs no restored capability-project handlers.


## Documentation slice: repair current instructions without rewriting historical evidence

### D1 — Recovery and migration instructions execute a deleted release wrapper (P1 operational guidance)

Evidence: `RECOVERY.md:93-95` tells the operator to apply migrations using `bin/migrate-prod.sh`; `migrations/README.md:45-50` also prescribes that wrapper for a bounded prefix. The file was deleted by `9b4b9bb1` (#2124), and is absent at the audited commit. The supported runner still implements exact-filename prefix selection (`tools/migrate.py:534-543`, `tools/migrate.py:661`); the active production workflow calls it directly (`.github/workflows/deploy.yml:146-149`). Consequence: following recovery guidance fails with a missing executable; the documented bounded deployment front door does not exist.

Exact fix: in `RECOVERY.md`, replace the deleted-wrapper reference with “the recovered database's configured migration runner, `./run.sh migrate --apply --yes`, before the GitHub production deployment workflow.” In `migrations/README.md:45-50`, replace `bin/migrate-prod.sh --through NNNN_name.sql` with `./run.sh migrate --through NNNN_name.sql`, retaining the exact checked-in prefix rule; state that a subsequent explicitly authorized run adds `--apply --yes`. Do not add a new wrapper or restore the retired release stack. These are proposed instructions only; no runner was executed during this audit.

### D2 — AGENTS promises a deleted fleet-sync maintenance path (P2)

Evidence: `AGENTS.md:226-231` says the Progress board installer uses “fleet sync's canonical main checkout” and that “Fleet sync rebinds the agent after main advances.” `bin/fleet-sync.sh` was deleted in `798e200a` (#2150); `ops/config/fleet-sync-siblings.json` was deleted in `d61b5944` (#2174). The explicit installer and installed-path audit remain in `ops/config-as-code.py`; the automated rebind actor does not. Consequence: agents may assume checkout movement repairs installed job paths automatically when no surviving job supplies that promise.

Exact fix: replace the fleet-sync sentences with “Install from the canonical checkout on main with `./run.sh config install-progress-board --apply`; verify the canonical runtime path using `./run.sh config verify-progress-board` and `./run.sh config check-launchd-main-paths`. After moving that checkout, rerun the explicit installer.” Preserve the main-checkout restriction and canonical board data. Do not describe missing automation as active.

### D3 — One local Markdown link points to a purged audit artifact (P3)

Evidence: `docs/agents/ci-failure-audit.md:17` links `../../tools/ci/ci-failure-audit-2026-10-15.json`, deleted in #2154. This is a historical audit reference, not an executable dependency. Exact fix: change the link to the same path at the parent of deletion commit `c9ab3788` (a permanent GitHub blob link), or remove the linked-ledger claim while retaining the audit's dated findings. No reason to restore the deleted ledger into current main.

Clean/false-positive checks: tracked, non-secret Markdown local links yielded exactly one unresolved relative target (D3). References in the dated retrieval design document and source paths used as fabricated test fixture strings are not live imports. `AGENTS.md:259` points to an existing `ops/rule-admission-audit.py`; its lifecycle instructions are still executable in source and are not a dangling-file defect. `CLAUDE.md:97-98` correctly links that section. `AGENTS.md:306-307` and `CLAUDE.md:122-123` correctly describe the GitHub deploy workflow. The three repository-provided skills exist; `/poteto-mode`, `/grill-with-docs`, `/wayfinder`, and `/tdd` are external workflow references, not repository files, and were not claimed installed or executed.


## Database retirement findings

The source-only schema audit identified the following complete or conditional slices. The separate schema report additionally contains the table/column census and index recommendations. This integrity report includes the complete proposed drop SQL so these findings stand alone.

## Ranked retirement findings and drop slices

These are source-safe candidates at `3e91aef47f8a97731befc4a0f46ee7dc0ca36db5`, not a claim that production contains no externally retained consumers or data. No database, dump, installed catalog, live caller log, or applied frontier was inspected. Each SQL block is a proposed **new forward migration body**, not an executed migration. Reserve the filename only when implementing. The explicit PostgreSQL `RESTRICT` behavior and pre/post checks of all 118 stored historical seals plus the current capability catalog deliberately abort on unexpected dependencies. Preserve any required historical evidence before an actual contract release.

### S1 — P2: remove the unused cost-refusal ledger (one table, all columns)

**Finding.** The generic scheduler purge deleted the runtime that could call the cost-admission function. `ops/control-plane-retirement.md:8-18` explicitly lists `ops.cost_refusal` among seven tables with no repository runtime consumer. `migrations/0165_control_plane_cost_refusals.sql:9` defines its ten columns: `id`, `job_id`, `attempt`, `route_key`, `estimated_cost_usd`, `monthly_budget_usd`, `spent_usd`, `reserved_usd`, `reason`, `refused_at`. Its only retained writer is the dormant `ops.admit_job_cost(uuid,uuid,text,numeric)` at `:27-69`; the only retained reader is `ops.v_cost_refusal_metric` at `:71-77` and that function's idempotency read at `:59`. No invocation of these objects remains in `mcp-server/src`, `lib`, or `tools`; repository searches found no additional non-migration consumer. `ops.reserve_job_cost` is a different function: the current version raises a budget exception (`migrations/0906_dot_database_design.sql:250-255`) rather than writing this ledger.

**Consequence.** A table, view, executable authority function, and append-only trigger survive solely for a deleted execution path. No path to DoctorCRE app verbs, morning brief, Leads, deal rooms, intake, or vendors was found in this source revision.

**Exact fix.** Drop its sole function and view before dropping the table. The table has SELECT grants only (`0165:79-82`), so its removal does not change the SCAC relation-DML category. Its single `carr_jobs` SECURITY DEFINER EXECUTE capability must remain accounted for using the existing retirement contract established by `0890` and exercised by `0935`; the block below preserves the old sealed row, not the executable function. It never rewrites a historical migration or sealed registry row.

```sql
-- rollback: forward-only; restore this retired scheduler subsystem only in a new migration.
-- expand-contract: contract; the generic scheduler caller was removed in PR #2100.
-- lock-review: ACCESS EXCLUSIVE on one retired ledger; actual row count and lock window
-- must be checked in the implementation rehearsal, with lock_timeout bounding acquisition.
SET LOCAL lock_timeout = '5s';

DO $retire_cost_capability$
DECLARE
  original jsonb := ops.scac_current_grant_contract();
  retired jsonb;
  next_contract jsonb;
  expected_key text :=
    'db-function-acl:ops.admit_job_cost(p_job_id uuid, p_lease_token uuid, p_route_key text, p_estimated_cost_usd numeric):carr_jobs:execute';
BEGIN
  IF EXISTS (SELECT 1 FROM generate_series(1,118) AS v(n)
      WHERE ops.scac_mutation_registry_seal_valid(
        'scac-mutation-registry.v' || n::text) IS NOT TRUE)
     OR ops.scac_mutation_catalog_v118_current() IS NOT TRUE THEN
    RAISE EXCEPTION 'Current or historical SCAC seal is not valid';
  END IF;
  SELECT contract - 'effect_class' - 'owner_package' - 'implementation_state'
                  - 'classification_authorizing' - 'source_locator'
    INTO STRICT retired
    FROM ops.scac_mutation_registry_entry
   WHERE registry_version = original->>'registry_version'
     AND ingress_key = expected_key;
  IF EXISTS (SELECT 1 FROM jsonb_array_elements(
      coalesce(original->'retired_secdef_execute','[]'::jsonb)) item
      WHERE item->>'ingress_key' = expected_key) THEN
    RAISE EXCEPTION 'Cost-admission capability already retired';
  END IF;
  next_contract := jsonb_set(original, '{retired_secdef_execute}',
    coalesce(original->'retired_secdef_execute','[]'::jsonb)
      || jsonb_build_array(retired));
  EXECUTE format(
    'CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb '
    'LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS %L',
    'SELECT ' || quote_literal(next_contract::text) || '::jsonb');
END
$retire_cost_capability$;

DROP FUNCTION ops.admit_job_cost(uuid,uuid,text,numeric) RESTRICT;
DROP VIEW ops.v_cost_refusal_metric RESTRICT;
DROP TABLE ops.cost_refusal RESTRICT;

DO $verify_cost_retirement$
BEGIN
  IF EXISTS (SELECT 1 FROM generate_series(1,118) AS v(n)
      WHERE ops.scac_mutation_registry_seal_valid(
        'scac-mutation-registry.v' || n::text) IS NOT TRUE)
     OR ops.scac_mutation_catalog_v118_current() IS NOT TRUE THEN
    RAISE EXCEPTION 'Cost retirement changed an unaccounted SCAC capability';
  END IF;
END
$verify_cost_retirement$;
```

This is statically reviewable SQL. It still needs the normal disposable PostgreSQL 18 reconstruction and migration-shadow check when implemented; it was not executed for this audit. The shared `ops.refuse_job_evidence_rewrite()` is deliberately retained because job receipts and other live tables use it.

### S2 — P2: remove the unused cognition cache-observation ledger and lease-bound cache doors

**Finding.** `ops.cognition_cache_observation` and its complete column set (`id`, `job_id`, `attempt`, `workflow_key`, `workflow_version`, `mode`, `cache_key`, `observation_kind`, `dependency_ref`, `observed_at`) originate at `migrations/0195_control_plane_cache_observations.sql:8-24`. Its only writers are `get_cognition_cache_for_job` (`:34-66`), `put_cognition_cache_for_job` (current definition `migrations/0906_dot_database_design.sql:289-333`) and `invalidate_cognition_cache_for_job` (`0906:335-366`). No current product path invokes these doors. The remaining explicit invocations are retired-behavior tests: `ops/dot-database-design-db-gate.py:359-376`. `ops/control-plane-retirement.md:14-16` explicitly identifies this dead runtime family.

**Consequence.** Runtime lease, cache-contract and immutability machinery survives for a removed scheduler; its tests can fail future cleanup while proving no retained product flow.

**Exact fix.** Remove those three test-only assertions with the eventual implementation; preserve the product-related tests in the same file. Drop the three doors and observation table, retaining the older cache table and older unbound cache functions for a separate slice. The observation table has only SELECT grants (`0195:146-153`); account for its three removed `carr_jobs` function grants with the existing seal retirement mechanism.

```sql
-- rollback: forward-only; restore retired cache observations only in a new migration.
-- expand-contract: contract; the generic scheduler caller was removed in PR #2100.
-- lock-review: ACCESS EXCLUSIVE on one retired observation ledger; rehearse its actual
-- lock window and row-retention disposition before delivery.
SET LOCAL lock_timeout = '5s';

DO $retire_cache_capabilities$
DECLARE
  original jsonb := ops.scac_current_grant_contract();
  expected_keys text[] := ARRAY[
    'db-function-acl:ops.get_cognition_cache_for_job(p_job_id uuid, p_lease_token uuid, p_cache_key text):carr_jobs:execute',
    'db-function-acl:ops.put_cognition_cache_for_job(p_job_id uuid, p_lease_token uuid, p_cache_key text, p_cognition_key text, p_cognition_version integer, p_output_schema_version integer, p_proposal jsonb, p_dependency_refs text[], p_ttl_seconds integer):carr_jobs:execute',
    'db-function-acl:ops.invalidate_cognition_cache_for_job(p_job_id uuid, p_lease_token uuid, p_dependency_ref text):carr_jobs:execute'
  ];
  retired jsonb;
  next_contract jsonb;
BEGIN
  IF EXISTS (SELECT 1 FROM generate_series(1,118) AS v(n)
      WHERE ops.scac_mutation_registry_seal_valid(
        'scac-mutation-registry.v' || n::text) IS NOT TRUE)
     OR ops.scac_mutation_catalog_v118_current() IS NOT TRUE THEN
    RAISE EXCEPTION 'Current or historical SCAC seal is not valid';
  END IF;
  SELECT jsonb_agg(contract - 'effect_class' - 'owner_package' - 'implementation_state'
                           - 'classification_authorizing' - 'source_locator'
                  ORDER BY ingress_key)
    INTO retired
    FROM ops.scac_mutation_registry_entry
   WHERE registry_version = original->>'registry_version'
     AND ingress_key = ANY(expected_keys);
  IF coalesce(jsonb_array_length(retired),0) <> cardinality(expected_keys) THEN
    RAISE EXCEPTION 'Expected cache capabilities absent from the sealed registry';
  END IF;
  IF EXISTS (SELECT 1 FROM jsonb_array_elements(
      coalesce(original->'retired_secdef_execute','[]'::jsonb)) item
      WHERE item->>'ingress_key' = ANY(expected_keys)) THEN
    RAISE EXCEPTION 'A cache capability is already retired';
  END IF;
  next_contract := jsonb_set(original, '{retired_secdef_execute}',
    coalesce(original->'retired_secdef_execute','[]'::jsonb) || retired);
  EXECUTE format(
    'CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb '
    'LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS %L',
    'SELECT ' || quote_literal(next_contract::text) || '::jsonb');
END
$retire_cache_capabilities$;

DROP FUNCTION ops.get_cognition_cache_for_job(uuid,uuid,text) RESTRICT;
DROP FUNCTION ops.put_cognition_cache_for_job(
  uuid,uuid,text,text,integer,integer,jsonb,text[],integer) RESTRICT;
DROP FUNCTION ops.invalidate_cognition_cache_for_job(uuid,uuid,text) RESTRICT;
DROP TABLE ops.cognition_cache_observation RESTRICT;

DO $verify_cache_retirement$
BEGIN
  IF EXISTS (SELECT 1 FROM generate_series(1,118) AS v(n)
      WHERE ops.scac_mutation_registry_seal_valid(
        'scac-mutation-registry.v' || n::text) IS NOT TRUE)
     OR ops.scac_mutation_catalog_v118_current() IS NOT TRUE THEN
    RAISE EXCEPTION 'Cache retirement changed an unaccounted SCAC capability';
  END IF;
END
$verify_cache_retirement$;
```

### S3 — P3: remove the unused cache evidence column

`ops.cognition_result_cache.evidence_refs` is declared with an empty-array default at `migrations/0149_control_plane_jobs.sql:185`. Its implicit default writes and whole-row transport remain, but no code consumes the value. Neither original cache writer (`migrations/0153_control_plane_resilience.sql:84-91`) nor the retained lease-bound writer (`migrations/0906_dot_database_design.sql:318-327`) writes it. The latter writers use the separate `dependency_refs` column instead; an omitted `evidence_refs` receives the empty-array default. The reader loads `%rowtype` at `migrations/0195_control_plane_cache_observations.sql:39,56` but consumes only cache identity, version, expiry, invalidation and proposal at `:57-66`; `evidence_refs` is never read from that record. All non-generated migration files containing the cache relation name were checked for this field; its declaration was the sole occurrence. No repository product path names the table. This is a no-consumer field rather than a literally untouched field: the retained SQL reader performs whole-row transport and the default still fills new rows. This is an optional smaller slice if retaining the otherwise orphaned cache table temporarily; skip it if removing that table in a later complete scheduler retirement.

```sql
-- rollback: forward-only; re-add only with a real producer and consumer.
-- expand-contract: contract; no retained writer or consumer uses cache evidence_refs.
-- lock-review: metadata-only DROP COLUMN; ACCESS EXCLUSIVE acquisition bounded to 5s.
SET LOCAL lock_timeout = '5s';
ALTER TABLE ops.cognition_result_cache DROP COLUMN evidence_refs RESTRICT;
```

No column-specific DML capability was found for this field; table-level grants remain unchanged. Production value retention remains unverified.

### S4 — P2: remove cost reservations after S1

`ops.cost_reservation` is also explicitly runtime-orphaned at `ops/control-plane-retirement.md:17`. All nine columns (`id`, `job_id`, `attempt`, `route_key`, `estimated_cost_usd`, `actual_cost_usd`, `state`, `created_at`, `settled_at`) belong to provider cost admission and settlement (`migrations/0153_control_plane_resilience.sql:15-30`). Its surviving SQL writers/readers are the three lease-bound APIs `reserve_job_cost` / `settle_job_cost` (`migrations/0906_dot_database_design.sql:217-287`) and `release_job_cost` (`migrations/0154_control_plane_cost_release.sql:8-26`), plus the admission API removed by S1. No retained product source invokes these APIs. The remaining calls in `ops/dot-database-design-db-gate.py:368-370,540-556` test the retired provider-budget runtime.

**Exact fix.** Apply after S1, remove just those retired test cases, and retire the three function capabilities before dropping the ledger. This table has SELECT-only runtime grants (`migrations/0153_control_plane_resilience.sql:171`); its outward job/provider FKs do not require dropping those parent tables. Its no-delete trigger is removed with the table; retain the shared trigger function.

```sql
-- rollback: forward-only; restore provider cost reservation only in a new migration.
-- expand-contract: contract; generic provider execution was removed in PR #2100.
-- lock-review: ACCESS EXCLUSIVE on one retired reservation ledger; rehearse row retention
-- and lock duration. This slice requires the preceding cost-refusal retirement.
SET LOCAL lock_timeout = '5s';

DO $retire_reservation_capabilities$
DECLARE
  original jsonb := ops.scac_current_grant_contract();
  expected_keys text[] := ARRAY[
    'db-function-acl:ops.reserve_job_cost(p_job_id uuid, p_lease_token uuid, p_route_key text, p_estimated_cost_usd numeric):carr_jobs:execute',
    'db-function-acl:ops.settle_job_cost(p_reservation_id uuid, p_job_id uuid, p_lease_token uuid, p_input_tokens integer, p_output_tokens integer, p_actual_cost_usd numeric):carr_jobs:execute',
    'db-function-acl:ops.release_job_cost(p_reservation_id uuid, p_job_id uuid, p_lease_token uuid):carr_jobs:execute'
  ];
  retired jsonb;
  next_contract jsonb;
BEGIN
  IF to_regprocedure('ops.admit_job_cost(uuid,uuid,text,numeric)') IS NOT NULL
     OR to_regclass('ops.cost_refusal') IS NOT NULL THEN
    RAISE EXCEPTION 'Apply cost-refusal retirement before reservation retirement';
  END IF;
  IF EXISTS (SELECT 1 FROM generate_series(1,118) AS v(n)
      WHERE ops.scac_mutation_registry_seal_valid(
        'scac-mutation-registry.v' || n::text) IS NOT TRUE)
     OR ops.scac_mutation_catalog_v118_current() IS NOT TRUE THEN
    RAISE EXCEPTION 'Current or historical SCAC seal is not valid';
  END IF;
  SELECT jsonb_agg(contract - 'effect_class' - 'owner_package' - 'implementation_state'
                           - 'classification_authorizing' - 'source_locator'
                  ORDER BY ingress_key)
    INTO retired
    FROM ops.scac_mutation_registry_entry
   WHERE registry_version = original->>'registry_version'
     AND ingress_key = ANY(expected_keys);
  IF coalesce(jsonb_array_length(retired),0) <> cardinality(expected_keys) THEN
    RAISE EXCEPTION 'Expected reservation capabilities absent from sealed registry';
  END IF;
  IF EXISTS (SELECT 1 FROM jsonb_array_elements(
      coalesce(original->'retired_secdef_execute','[]'::jsonb)) item
      WHERE item->>'ingress_key' = ANY(expected_keys)) THEN
    RAISE EXCEPTION 'A reservation capability is already retired';
  END IF;
  next_contract := jsonb_set(original, '{retired_secdef_execute}',
    coalesce(original->'retired_secdef_execute','[]'::jsonb) || retired);
  EXECUTE format(
    'CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb '
    'LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS %L',
    'SELECT ' || quote_literal(next_contract::text) || '::jsonb');
END
$retire_reservation_capabilities$;

DROP FUNCTION ops.reserve_job_cost(uuid,uuid,text,numeric) RESTRICT;
DROP FUNCTION ops.settle_job_cost(uuid,uuid,uuid,integer,integer,numeric) RESTRICT;
DROP FUNCTION ops.release_job_cost(uuid,uuid,uuid) RESTRICT;
DROP TABLE ops.cost_reservation RESTRICT;

DO $verify_reservation_retirement$
BEGIN
  IF EXISTS (SELECT 1 FROM generate_series(1,118) AS v(n)
      WHERE ops.scac_mutation_registry_seal_valid(
        'scac-mutation-registry.v' || n::text) IS NOT TRUE)
     OR ops.scac_mutation_catalog_v118_current() IS NOT TRUE THEN
    RAISE EXCEPTION 'Reservation retirement changed an unaccounted SCAC capability';
  END IF;
END
$verify_reservation_retirement$;
```

### DB-C1 — P1: the three named legacy-schedule candidates are writerless, not safely droppable

The later schedule-contract purge (`bfc5e60b`, PR #2158) removes `lib/control_plane_scheduler_cutover.py` and the synchronization code that populated these projections. `tools/workflow-registry.py:33-116` now writes `ops.job_definition`, but no longer writes any of these three tables. The earlier statement in `ops/control-plane-retirement.md:20-23` predates that removal and needs this qualification.

| Table and all affected columns | Last source writer | Surviving SQL use / consequence |
| --- | --- | --- |
| `ops.legacy_schedule_surface_registry`: `workflow_key`, `workflow_version`, `surface_id`, `locator`, `scheduler_kind`, `duplicate_group` | Removed synchronization in PR #2158; test inserts remain in `ops/workflow-cutover-r02-local-pg-gate.py:355,445,481` | `ops.enqueue_job` reads the registry for dual-write and duplicate-group gating (`migrations/0604_doctorcre_r02_dual_write_gating.sql:127-178`); `ops.retire_workflow_cutover_plan` reads it at `migrations/0602_doctorcre_r02_workflow_cutover_and_caller_inventory.sql:812-821`. |
| `ops.legacy_schedule_provider_contract`: `surface_id`, `workflow_key`, `workflow_version`, `locator`, `cron_expression`, `timezone`, `definition_relpath`, `definition_sha256` | Removed synchronization in PR #2158 | `ops.record_claude_scheduler_observation` declares/reads its row at `migrations/0180_claude_scheduler_observation_receipt.sql:62,90`; current `ops.disable_legacy_schedule` validates it in `migrations/0184_notes_duplicate_schedule_cutover.sql:133-156,177-200`. |
| `ops.legacy_schedule_launchd_contract`: `surface_id`, `workflow_key`, `workflow_version`, `locator`, `repo_plist_relpath`, `installed_plist_name`, `program_arguments`, `plist_sha256`, `schedule_sha256`, `timezone` | Removed synchronization in PR #2158 | `ops.record_launchd_scheduler_observation` declares/reads its row at `migrations/0182_launchd_scheduler_observation_receipt.sql:44,72`; current disable function uses it alongside the provider contract in `0184`. |

The `disable-legacy-schedule` verb is still registered and calls the SQL door (`mcp-server/src/tools.js:6754-6789`), as are workflow cutover handlers (`mcp-server/src/workflow-cutover.v5.js:170-271`). Thus these are surviving machinery callers, not three empty SQL-only projections that can simply be dropped. Old projection rows can drift because their reconciler is gone. A new environment cannot populate them through the retained registry CLI. That can make the remaining observation/disable doors fail or operate against stale definitions. Product-level use is indirect: shared job execution still exists for on-demand engineering, and health/schedule views read their observation receipts. No production usage claim is made.

**Fix slice.** First remove or replace the remaining schedule approval verbs and the dual-write branches in the shared enqueue function, then remove obsolete tests and native observation contracts. Do not drop `ops.job_definition`, `ops.job`, `ops.job_attempt`, `ops.job_receipt`, or all observation receipts merely because generic recurrence was removed. The final dependency-ordered table fragment is:

```sql
-- This is a deferred contract fragment, NOT a runnable standalone migration.
-- Prerequisite: replace/remove every SQL caller and reconcile SCAC EXECUTE grants.
DROP TABLE ops.legacy_schedule_provider_contract RESTRICT;
DROP TABLE ops.legacy_schedule_launchd_contract RESTRICT;
DROP TABLE ops.legacy_schedule_surface_registry RESTRICT;
```

No exact complete safe migration is asserted for this group: the required product/retirement decision is which retained administrative verbs and shared enqueue semantics remain. A naked three-table DROP would leave installed PL/pgSQL bodies broken even when PostgreSQL does not record their text references in `pg_depend`.

### DB-C2 — P2: the rest of the dead generic scheduler requires one coherent retirement

After S1/S2/S4, the remaining four explicit runtime orphans are `ops.provider_route`, `ops.provider_observation`, `ops.cognition_job`, and `ops.cognition_result_cache`. All their columns are scheduler-only in retained source; SQL still reads/writes them. Definitions: `migrations/0149_control_plane_jobs.sql:7-65,179-192`; `migrations/0153_control_plane_resilience.sql:9-34`. Retained wrappers: `0153:37-180`, `0154:8-26`; current cost reserve/settle at `0906:217-287`. The provider route view expands `r.*` (`0149:40`), so individual route columns cannot be declared unread solely by missing source field names. The lease-bound cache and cost tests remain at `ops/dot-database-design-db-gate.py:359-376,540-556`.

After S1/S2/S4, a complete later slice must drop the old unbound cache functions; provider selection/observation functions; the provider-route view; and then the child tables before `provider_route`/`cognition_job`. Unlike S1/S2, these tables carry runtime DML grants (`0149:507-509`), so the fixed relation-DML digest in the current seal changes. The existing `retired_secdef_execute` mechanism alone is insufficient. Do not claim a runnable drop until a forward successor or a narrowly reviewed relation-grant retirement extension is generated and verified. Keep their whole column sets as conditional candidates, not individually “safe” fields.

### DB-C3 — P2: approval triggers still attached to retained tables

| Surviving constraint / trigger | Evidence and current scope | Exact bounded next action |
| --- | --- | --- |
| `job_definition_cutover_requires_evidence` | `migrations/0149_control_plane_jobs.sql:441-459` requires accepted shadow and canary rows before setting `legacy_disabled_at`. It is purely the old schedule-cutover ceremony; no later DROP was found. The remaining disable verb still activates it. | In DB-C1's caller-removal slice: `DROP TRIGGER job_definition_cutover_requires_evidence ON ops.job_definition; DROP FUNCTION ops.require_cutover_evidence() RESTRICT;`. Do not drop `legacy_disabled_at`: Atlas reads it (`mcp-server/src/atlas-inventory-graph.v5.js:285-295`) and health reads it (`tools/health-check.py:602,842`). |
| `staging_release_approval_is_typed` and `a_staging_release_plan_revision_invalidates_approval` | `migrations/0219_staging_release_approval_receipt.sql:34-83` still requires a Joe-owned typed receipt for promoted staging records. Current GitHub deployment no longer goes through the deleted staging approval gate (PR #2153). SQL approval function and historical receipt FKs remain. | Defer as one release-record contract slice: remove these two triggers and their trigger functions `ops.staging_release_approval_is_typed()` / `ops.staging_release_plan_revision_invalidates_approval()` only after replacing the approved-state contract and retiring `ops.approve_staging_release`. Preserve historical receipt rows until retention is decided. |
| `approved_by_actor='joe'` and approval-expiry CHECKs on `ops.staging_release_approval_receipt` | `migrations/0219_staging_release_approval_receipt.sql:12-18`; only historical staging approval machinery owns these semantics. | Remove with the table's complete retirement, not by weakening its historical evidence constraints in place. |
| `legacy_schedule_disable_receipt_append_only`, `legacy_schedule_observation_receipt_append_only`, and sibling receipt FKs/CHECK | `migrations/0176_legacy_schedule_disable_receipt.sql:37-45`, `0180:42-44`, `0184:12-29`. They preserve old approval observations but observations remain read by `mcp-server/src/schedule-board.js:34` and `tools/health-check.py:395`. | Retain with evidence tables until DB-C1 replaces the remaining readers; dropping only the immutability controls yields no schema cleanup and weakens historical data. |

Do not classify `deployment_requires_a_live_approval` or `an_approved_release_names_its_approval` by their old names alone: migration `0540_release_readiness_without_repeat_approval.sql:10-23,363` explicitly broadened them to technical readiness without repeat human approval. Likewise, the heavy-build ready-plan trigger was already dropped at `migrations/0890_retire_heavy_build_runtime.sql:6`; reporting it as surviving would be false. Rule approval tables still have living rule lifecycle verbs and are not orphaned merely because local acceptance harnesses were removed.

## Migration and historical-seal constraints

1. `AGENTS.md:51-85` says authorized work proceeds without CARR plan/approval ceremony, but technical checks remain. `AGENTS.md:136-159` requires an isolated tree, and `AGENTS.md:196-209` makes `ops/ci.sh` the check entry point. Its explicit production guidance at `:304-307` delegates deployment to the workflow and migration runner. **AGENTS.md does not itself enumerate the SQL seal versions**; those constraints are in the migrations/runner below. This audit's no-production and report-only instructions take precedence over generic delivery instructions.
2. `migrations/README.md:39-41,99-108` and `tools/migrate.py:63-67,462-484` make applied SQL immutable. Fix stale comments and removed script paths by forward migration or explanatory current documentation; never edit old SQL to erase references. New contract migrations need rollback, expand-contract and lock-review headers (`migrations/README.md:55-68`). The number is reserved at implementation time (`:131-136`); this report reserves none.
3. The supported rebuild uses a PostgreSQL 18 snapshot and pending migrations, not an empty replay (`migrations/README.md:8-35,69-74`). The audit intentionally did not open that dump. Therefore static inventory is the schema implied by source, not verified reconstruction of the deployed catalog or every seed-dependent DO branch.
4. The latest historical SQL registry is **`scac-mutation-registry.v118`** (`migrations/0873_self_closing_records_scac_successor.sql:21,530-533`). Historical versions **v1 through v118** are checked by `ops.scac_policy_epoch_snapshot()` (`0873:583-625`); v1–v3 use the generic seal validator and v4–v118 use versioned seal functions. Preserve the old `scac_mutation_registry_version` / `entry` rows, version checks and policy-epoch chain. Do not delete old SQL seal functions because current generated JavaScript advertises only today's handler registry.
5. `migrations/0890_retire_heavy_build_runtime.sql:558-588` is the current catalog comparator. It accounts for removed SECURITY DEFINER EXECUTE capabilities using `ops.scac_current_grant_contract()->'retired_secdef_execute'`, but still compares the relation-DML category to 321 exact historical grant rows and its digest. `migrations/0935_retire_jev_gateway.sql:9-22` is the current precedent for a caller-removal DROP accompanied by this grant-retirement contract. S1/S2/S4 preserve that mechanism. S3 changes no table/column grants.
6. The ledger insert triggers a deferred policy-epoch refresh (`migrations/0455_siep12_policy_epoch.sql:452`). `tools/migrate.py:105-115` explains why schema change and matching capability change must commit together; listed atomic groups cannot be split with `--through`. Any new successor needed by DB-C2 must share a transaction with its DDL. Never disable that trigger just to make a drop pass.

The existing catalog hardening check distinguishes current capabilities from stored history: `ops/definer-hardening-local-pg-gate.py:198-212` checks the v118 live catalog and then validates every historical stored seal separately. Dropping a runtime object does not itself erase a historical seal; changing its live capability set without the corresponding forward retirement contract breaks the current comparator.


## Report validation and design/debt pass

Only this report is proposed for delivery. SQL blocks are recommendations, not migration files. Source citations were checked against the pinned tree; report whitespace and staged-only credential-pattern checks passed. Independent review corrected overbroad orphan claims, preserved current product readers, and checked the proposed SQL dependency closures. The companion index audit's 12 proposed index statements and this report's four complete drop-migration bodies, the deferred schedule DROP fragment, and six PL/pgSQL DO bodies parse with the PostgreSQL parser. Parsing is not database execution or a migration acceptance result.

- **Codebase-design:** kept fixes at existing interfaces: repair one shared hook initializer, remove exclusive retired callers with their tests, and preserve shared product modules. No new runtime abstraction is introduced by the reports.
- **Zero-tech-debt:** grouped removals into complete source/SQL slices rather than restoring purged wrappers or keeping tests as their sole consumers. Explicitly retained live product/record readers and immutable historical evidence.
