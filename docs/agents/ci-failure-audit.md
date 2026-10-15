# CI failure audit

The fixed seven-day window is 2026-10-08 21:05 UTC through 2026-10-15 21:05 UTC.
Daily partitions returned 2,353 CARR runs and 1,051 DoctorCRE runs. Their failure
inventories contain 207 and 113 runs. Failed-job logs cover all 320. One `gh run
view --log-failed` request timed out; its complete failed-job logs were recovered
from the CLI's downloaded run archive. Job metadata is missing for run
38296639363, whose failed-job log remains available.

## Collection and counting

The initial unpartitioned queries silently stopped at GitHub's 1,000-run search
cap. Daily partitions stayed below the cap. Separate `gh run list --status
failure` queries independently returned the same 207 and 113 failure IDs.
`gh run view --log-failed` supplied the logs, and `--json jobs` identified failed
steps. Logs remain in the desk's `/private/tmp/ci-failure-audit` cache. The
[per-run ledger](../../tools/ci/ci-failure-audit-2026-10-15.json) records their
SHA-256 digests, workflow, source SHA, attempt, evidence, and same-SHA passes.

Recollect a new window with:

```sh
python3 tools/ci/ci-failure-audit.py \
  --repo jbookout/carr-system --repo jbookout/doctorcre-app \
  --start 2026-10-08T21:05:00Z --end 2026-10-15T21:05:00Z \
  --cache /private/tmp/ci-failure-audit-new
```

The collector keys new entries by repository, run ID, and attempt. Every log
and metadata request passes the inventoried `--attempt`; returned metadata must
match the run ID, attempt, source SHA, and conclusion. Entries publish atomically
with a SHA-256 manifest, and both files are checked before cache reuse. Invalid
entries fail collection. Old run-ID-only cache files are never reused. These
checks apply to new collections; they do not retrospectively authenticate the
October 15 ledger's underlying logs.

Counts are distinct failed runs, with one primary category per run. An estimated
email is one failure notification per failed run to the pushing account. This is
a workload estimate, not a mailbox count. Cancellations and skipped runs are
excluded. A run that passed on its latest attempt is outside the requested
`--status failure` inventory, even if an earlier attempt failed; therefore these
numbers are lower bounds on total historical failure emails.

A same-SHA success on another event is not flake proof. CARR's structured
`CARR_FLAKE_RESULT` records prove an identical-tree first failure followed by a
passing inline diagnostic rerun. Strict CI remains red. Nineteen CARR runs have
this evidence; seven also fail another independent check and remain in the real
PR failure bucket. DoctorCRE run 38648997949 failed supervisor timing assertions
at the exact SHA that passed the same PR workflow in run 38646040969. That pass
precedes the failure, so it proves nondeterminism rather than a successful later
rerun.

The real PR bug bucket also includes required evidence violations and failures
inherited from main. These remain blocking. Where only an assertion or timeout
is available, the ledger marks the inference conservatively; absence of a
same-SHA pass does not prove a production defect. Suspected timing failures are
not promoted to proven flakes. Several old supervisors suppress the inner
worker error, so those exact inner causes remain unresolved.

## Failure runs and estimated emails per week

| Repository | Cause | Failed runs | Estimated failure emails |
| --- | --- | ---: | ---: |
| jbookout/carr-system | Real PR bug | 157 | 157 |
| jbookout/carr-system | Flaky test | 12 | 12 |
| jbookout/carr-system | Infrastructure/environment | 7 | 7 |
| jbookout/carr-system | Workflow misconfiguration | 7 | 7 |
| jbookout/carr-system | Expected alarm | 24 | 24 |
| jbookout/doctorcre-app | Real PR bug | 85 | 85 |
| jbookout/doctorcre-app | Flaky test | 1 | 1 |
| jbookout/doctorcre-app | Infrastructure/environment | 0 | 0 |
| jbookout/doctorcre-app | Workflow misconfiguration | 27 | 27 |
| jbookout/doctorcre-app | Expected alarm | 0 | 0 |
| **Both repositories** | **All causes** | **320** | **320** |

## Repair order and remaining work

1. Edge liveness produced 24 genuine outage alarms. `ops/edge-liveness.py` keeps
   its original exit codes. `ops/ci-noise-incident.py` records that probe verdict
   in an artifact and updates one assigned incident issue. The probe launcher
   emits a structured verdict only after the probe returns normally. An exit-1
   traceback without that verdict fails as BLIND/ERROR and cannot update or
   close the incident. Timeouts also retain an ERROR artifact and fail.
   Publication includes only the validated status and run link, without raw
   probe stdout or stderr. The incident requires the `ci-edge-liveness` label
   and the GitHub Actions bot's account ID, login, and type; public title and
   body text cannot select it. Every update restores the `jbookout` assignment.
   Repeated observations
   edit the body without comments. Recovery closes the issue, and recurrence
   reopens it. Missing credentials or an unreadable ledger are explicitly BLIND.
   Unexpected probe exits and failed GitHub delivery still fail the workflow.
2. DoctorCRE supervisor timing failures repeatedly report `run-ledger-busy`.
   Its test fixture now uses the production one-second grace instead of 100 ms.
   The four-second cleanup assertion and every physical-process assertion stay.
   The shared drag helper uses the visible column intersection and two target
   moves before release. It never retries a mutation. Geometry and pointer
   cleanup are locally verified; hosted browser verification remains required.
3. Four CARR runs failed Docker Hub's unauthenticated Postgres pull limit. PR CI
   and the main canary use Docker's official Postgres image through Google's public Docker Hub mirror.
   The major version and all database checks stay. The health command names the
   throwaway database and role, avoiding repeated root-role errors in logs.
   [Google documents this mirror](https://docs.cloud.google.com/artifact-registry/docs/pull-cached-dockerhub-images). The exact OCI index was fetched successfully and its SHA-256 digest and Linux amd64 entry verified before pinning.
   The pinned image also pulled and initialized successfully in PR #1792's
   [hosted CI run](https://github.com/jbookout/carr-system/actions/runs/38809784331).
4. CI flake proposals ran 632 times, including 625 completion-triggered runs.
   The existing daily two-day reconciliation remains, with manual dispatch.
   Removing the completion chain cuts this workflow to seven scheduled runs per
   week without changing CI verdicts or adding retries.
5. DoctorCRE CI and e2e each created 56 skipped close-event suites. Removing the
   close trigger cuts 112 empty runs. Edited-body and ready-for-review events
   retain their required validation. Existing PR concurrency already cancels
   superseded pushes; it is preserved in CI, DB acceptance, and DoctorCRE.

Current main already removed the scheduled staging sweep that failed all six
runs. It now requires explicit dispatch, a deployed source SHA, and runtime
qualification. The three old five-second apt-cache failures are already repaired
by a 30-second bounded metadata query on CARR main. The successor fixture's
main-branch assumption is already repaired by PR #1672. These historical fixes
are not claimed as changes in this PR.

The following work remains outside the assigned write paths:

- CARR's confirmed flakes include `tools/test_dot_processes.py`,
  `tools/room-bridge/test_write_ownership_unit.py`, and
  `tools/room-bridge/test_codex_live_unit.py`. Their source needs readiness and
  process-lifecycle fixes; changing retries or treating the second pass as green
  would hide failures. Some merge-queue fixture races were already repaired by
  PR #1734. No changes here touch the retry door owned by PR #1786.
- DoctorCRE's ten full-main schedule failures all report `source_changed`.
  Some acknowledge zero tests and others acknowledge completed tests. The exact
  offending generated path is not logged. Repair requires
  `scripts/full-main.mjs` and its `test/full-main.test.mjs` tests, which are
  outside this desk's write scope. Scheduled full-main remains red until fixed.
- Eleven early DoctorCRE privacy runs exit 2, a setup/input refusal whose old
  diagnostic prints only the scanner filename. The current workflow supplies
  the corpus secret. Diagnosing a recurrence requires the scanner's error-code
  handling in `scripts/privacy/client-data-check.mjs`, outside this scope.
- All 546 Authorized source merge controller runs were skipped, 515 from CI
  completions and 31 from schedules. They consumed no runner job. Its existing
  event/schedule contract is asserted by `mcp-server/test/source-merge-migration.test.mjs`.
  Removing that unused chain requires changing that contract outside this scope;
  this PR preserves the controller's activation behavior.

## Expected email change

For the same historical workload, CARR's directly addressed failure emails are
**207 to 179 per week**: 24 outage-poll failure emails move to one incident issue,
and four Docker Hub pull failures are avoided. Incident creation and recovery
can generate issue notifications according to subscription settings, so this
is not a prediction of zero total incident mail. The image pull change still
needs hosted confirmation.

DoctorCRE's conservative directly supported projection is **113 to 112 per
week**, counting only the proven supervisor flake. The drag and wider supervisor
fixes target additional observed timing failures, but their reduction is not
quantified before hosted confirmation. Combined, that is **320 to 291 failure
emails per week**, plus one incident lifecycle. This estimate deliberately
retains real PR failures, unresolved shadow failures, and unverified flakes.
Already-landed fixes can reduce a future week's count further; no claim here
assumes that every historical PR failure recurs weekly.

Run volume falls from **3,404 to 2,667 per week** for the observed workload:
625 flake-proposal follow-on runs and 112 skipped close-event suites disappear.
This volume reduction is separate from failure-email reduction.

## Verification

- Incident lifecycle tests cover 24 repeated observations, recovery, recurrence,
  blind probes, duplicate issue refusal, and notification errors.
- Existing edge probe selftests and CI-flake selftests pass. CI checker selftests
  pass 236 of 236 under the repository Python environment. Full `ops/ci.sh`
  was not run.
- DoctorCRE geometry/pointer tests and selected workflow-policy replays pass.
  The source check and slice ownership check pass. Browser outcomes require
  hosted CI. One overly broad workflow test invocation attempted browser launch
  subtests despite the desk restriction; those launches failed, and subsequent
  verification used explicit non-browser test names.

## Per-run classification

The ledger contains complete source SHAs and log digests. The evidence below is
bounded for readability. Mixed failures retain their secondary proven flakes
in the ledger rather than counting the same email twice.

| Repository | Run | Workflow | Primary cause | Evidence |
| --- | --- | --- | --- | --- |
| carr-system | [37850559880](https://github.com/jbookout/carr-system/actions/runs/37850559880) | Edge liveness (cloud) | Expected alarm | Studio outage: previously observed services stale |
| carr-system | [37855519001](https://github.com/jbookout/carr-system/actions/runs/37855519001) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt |
| carr-system | [37855581524](https://github.com/jbookout/carr-system/actions/runs/37855581524) | CI | Workflow misconfiguration | fixture reaches real runner model catalog instead of injected test catalog |
| carr-system | [37855612425](https://github.com/jbookout/carr-system/actions/runs/37855612425) | CI | Real PR bug | FAIL  gates       failed: main-canary-selftest.py |
| carr-system | [37861973573](https://github.com/jbookout/carr-system/actions/runs/37861973573) | CI | Real PR bug | FAIL  gates       failed: scheduled-jobs-check-selftest.py tailscale-health-selftest.py test-dot-file-reports.py reachability-check |
| carr-system | [37861973698](https://github.com/jbookout/carr-system/actions/runs/37861973698) | DB acceptance | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [37862403863](https://github.com/jbookout/carr-system/actions/runs/37862403863) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt |
| carr-system | [37863165688](https://github.com/jbookout/carr-system/actions/runs/37863165688) | main canary | Workflow misconfiguration | successor test assumes git init did not create main; already fixed by #1672 |
| carr-system | [37863190545](https://github.com/jbookout/carr-system/actions/runs/37863190545) | main canary | Workflow misconfiguration | successor test assumes git init did not create main; already fixed by #1672 |
| carr-system | [37863624332](https://github.com/jbookout/carr-system/actions/runs/37863624332) | CI | Real PR bug | FAIL  gates       failed: ci-selftest.py |
| carr-system | [37864173059](https://github.com/jbookout/carr-system/actions/runs/37864173059) | CI | Real PR bug | FAIL  gates       failed: mechanism-doctrine-gate |
| carr-system | [37864458625](https://github.com/jbookout/carr-system/actions/runs/37864458625) | DB acceptance | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [37864458626](https://github.com/jbookout/carr-system/actions/runs/37864458626) | CI | Real PR bug | FAIL  gates       failed: scheduled-jobs-check-selftest.py tailscale-health-selftest.py test-dot-file-reports.py reachability-check |
| carr-system | [37864897489](https://github.com/jbookout/carr-system/actions/runs/37864897489) | main canary | Workflow misconfiguration | successor test assumes git init did not create main; already fixed by #1672 |
| carr-system | [37866097904](https://github.com/jbookout/carr-system/actions/runs/37866097904) | CI | Real PR bug | FAIL  gates       failed: test-dot-file-reports.py |
| carr-system | [37866128547](https://github.com/jbookout/carr-system/actions/runs/37866128547) | CI | Real PR bug | FAIL  gates       failed: scheduled-jobs-check-selftest.py tailscale-health-selftest.py test-dot-file-reports.py reachability-check |
| carr-system | [37866128716](https://github.com/jbookout/carr-system/actions/runs/37866128716) | DB acceptance | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [37867383669](https://github.com/jbookout/carr-system/actions/runs/37867383669) | main canary | Workflow misconfiguration | successor test assumes git init did not create main; already fixed by #1672 |
| carr-system | [37867930983](https://github.com/jbookout/carr-system/actions/runs/37867930983) | CI | Real PR bug | FAIL  gates       failed: ci-selftest.py |
| carr-system | [37868134344](https://github.com/jbookout/carr-system/actions/runs/37868134344) | CI | Real PR bug | FAIL  gates       failed: scheduled-jobs-check-selftest.py tailscale-health-selftest.py test-dot-file-reports.py reachability-check |
| carr-system | [37868134349](https://github.com/jbookout/carr-system/actions/runs/37868134349) | DB acceptance | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [37870553331](https://github.com/jbookout/carr-system/actions/runs/37870553331) | CI | Real PR bug | FAIL  gates       failed: conduct-gate-selftest.py |
| carr-system | [37870570830](https://github.com/jbookout/carr-system/actions/runs/37870570830) | DB acceptance | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [37870570878](https://github.com/jbookout/carr-system/actions/runs/37870570878) | CI | Real PR bug | FAIL  gates       failed: ci-selftest.py scheduled-jobs-check-selftest.py tailscale-health-selftest.py test-dot-file-reports.py reachability-check |
| carr-system | [37870924700](https://github.com/jbookout/carr-system/actions/runs/37870924700) | CI | Real PR bug | FAIL  gates       failed: test_session_bridge_unit.py |
| carr-system | [37871653535](https://github.com/jbookout/carr-system/actions/runs/37871653535) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt |
| carr-system | [37873414654](https://github.com/jbookout/carr-system/actions/runs/37873414654) | main canary | Workflow misconfiguration | successor test assumes git init did not create main; already fixed by #1672 |
| carr-system | [37874713567](https://github.com/jbookout/carr-system/actions/runs/37874713567) | CI | Infrastructure/environment | apt-cache metadata query exceeded old five-second deadline; fixed on current main |
| carr-system | [37874986596](https://github.com/jbookout/carr-system/actions/runs/37874986596) | main canary | Workflow misconfiguration | successor test assumes git init did not create main; already fixed by #1672 |
| carr-system | [37876156413](https://github.com/jbookout/carr-system/actions/runs/37876156413) | CI | Real PR bug | FAIL  gates       failed: test_merge_queue.py |
| carr-system | [37876201055](https://github.com/jbookout/carr-system/actions/runs/37876201055) | CI | Real PR bug | FAIL  gates       failed: ci-selftest.py |
| carr-system | [37876240860](https://github.com/jbookout/carr-system/actions/runs/37876240860) | CI | Infrastructure/environment | apt-cache metadata query exceeded old five-second deadline; fixed on current main |
| carr-system | [37876759304](https://github.com/jbookout/carr-system/actions/runs/37876759304) | Edge liveness (cloud) | Expected alarm | Studio outage: previously observed services stale |
| carr-system | [37877897576](https://github.com/jbookout/carr-system/actions/runs/37877897576) | main canary | Infrastructure/environment | apt-cache metadata query exceeded old five-second deadline; fixed on current main |
| carr-system | [37880122011](https://github.com/jbookout/carr-system/actions/runs/37880122011) | CI | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [37880122144](https://github.com/jbookout/carr-system/actions/runs/37880122144) | DB acceptance | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [37884754650](https://github.com/jbookout/carr-system/actions/runs/37884754650) | CI | Flaky test | same-tree inline rerun passed: test_merge_queue.py |
| carr-system | [37886234040](https://github.com/jbookout/carr-system/actions/runs/37886234040) | CI | Real PR bug | FAIL  gates       failed: scheduled-jobs-check-selftest.py |
| carr-system | [37887130878](https://github.com/jbookout/carr-system/actions/runs/37887130878) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt |
| carr-system | [37895177195](https://github.com/jbookout/carr-system/actions/runs/37895177195) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt |
| carr-system | [37901147341](https://github.com/jbookout/carr-system/actions/runs/37901147341) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt |
| carr-system | [37903910299](https://github.com/jbookout/carr-system/actions/runs/37903910299) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt |
| carr-system | [37904279501](https://github.com/jbookout/carr-system/actions/runs/37904279501) | CI | Flaky test | same-tree inline rerun passed: test_merge_queue.py |
| carr-system | [37919744350](https://github.com/jbookout/carr-system/actions/runs/37919744350) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt |
| carr-system | [37923697570](https://github.com/jbookout/carr-system/actions/runs/37923697570) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt |
| carr-system | [37926037079](https://github.com/jbookout/carr-system/actions/runs/37926037079) | Edge liveness (cloud) | Expected alarm | Studio outage: previously observed services stale |
| carr-system | [37928924640](https://github.com/jbookout/carr-system/actions/runs/37928924640) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt |
| carr-system | [37935625574](https://github.com/jbookout/carr-system/actions/runs/37935625574) | CI | Real PR bug | FAIL  gates       failed: ci-selftest.py |
| carr-system | [37939169349](https://github.com/jbookout/carr-system/actions/runs/37939169349) | DB acceptance | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [37946635809](https://github.com/jbookout/carr-system/actions/runs/37946635809) | DB acceptance | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [37946635915](https://github.com/jbookout/carr-system/actions/runs/37946635915) | CI | Real PR bug | FAIL  gates       failed: test_merge_queue.py; also has proven same-tree flake: test_merge_queue.py |
| carr-system | [37947081414](https://github.com/jbookout/carr-system/actions/runs/37947081414) | DB acceptance | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [37947081496](https://github.com/jbookout/carr-system/actions/runs/37947081496) | CI | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [37947294318](https://github.com/jbookout/carr-system/actions/runs/37947294318) | DB acceptance | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [37947294439](https://github.com/jbookout/carr-system/actions/runs/37947294439) | CI | Real PR bug | FAIL  gates       failed: health-check-findings-selftest.py |
| carr-system | [37947726165](https://github.com/jbookout/carr-system/actions/runs/37947726165) | CI | Real PR bug | FAIL  gates       failed: ci-selftest.py |
| carr-system | [37947726310](https://github.com/jbookout/carr-system/actions/runs/37947726310) | DB acceptance | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [37948248211](https://github.com/jbookout/carr-system/actions/runs/37948248211) | CI | Real PR bug | FAIL  gates       failed: test_write_ownership_unit.py; also has proven same-tree flake: test_write_ownership_unit.py |
| carr-system | [37948594061](https://github.com/jbookout/carr-system/actions/runs/37948594061) | CI | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [37948594084](https://github.com/jbookout/carr-system/actions/runs/37948594084) | DB acceptance | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [37948771814](https://github.com/jbookout/carr-system/actions/runs/37948771814) | DB acceptance | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [37948771878](https://github.com/jbookout/carr-system/actions/runs/37948771878) | CI | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [37950434973](https://github.com/jbookout/carr-system/actions/runs/37950434973) | CI | Real PR bug | FAIL  gates       failed: test_github_rate_limit.py |
| carr-system | [37950435017](https://github.com/jbookout/carr-system/actions/runs/37950435017) | DB acceptance | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [37953852948](https://github.com/jbookout/carr-system/actions/runs/37953852948) | DB acceptance | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [37953852971](https://github.com/jbookout/carr-system/actions/runs/37953852971) | CI | Real PR bug | FAIL  gates       failed: test_github_rate_limit.py |
| carr-system | [37962236355](https://github.com/jbookout/carr-system/actions/runs/37962236355) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt |
| carr-system | [37968726675](https://github.com/jbookout/carr-system/actions/runs/37968726675) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt |
| carr-system | [37976607664](https://github.com/jbookout/carr-system/actions/runs/37976607664) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt |
| carr-system | [37978901182](https://github.com/jbookout/carr-system/actions/runs/37978901182) | CI | Real PR bug | FAIL  gates       failed: ci-selftest.py config-as-code-claude-continuity-selftest.py config-as-code-launchd-selftest.py launchd-calendar-selftest.py machine-prerequisites-selftest.py scheduled-jobs-check-selft |
| carr-system | [37978901474](https://github.com/jbookout/carr-system/actions/runs/37978901474) | DB acceptance | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [37985074059](https://github.com/jbookout/carr-system/actions/runs/37985074059) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt |
| carr-system | [37990237171](https://github.com/jbookout/carr-system/actions/runs/37990237171) | CI | Infrastructure/environment | Docker Hub unauthenticated Postgres pull limit |
| carr-system | [37991314341](https://github.com/jbookout/carr-system/actions/runs/37991314341) | main canary | Infrastructure/environment | Docker Hub unauthenticated Postgres pull limit |
| carr-system | [37993204542](https://github.com/jbookout/carr-system/actions/runs/37993204542) | CI | Infrastructure/environment | Docker Hub unauthenticated Postgres pull limit |
| carr-system | [37994369313](https://github.com/jbookout/carr-system/actions/runs/37994369313) | Edge liveness (cloud) | Expected alarm | Studio outage: previously observed services stale |
| carr-system | [37994864112](https://github.com/jbookout/carr-system/actions/runs/37994864112) | CI | Infrastructure/environment | Docker Hub unauthenticated Postgres pull limit |
| carr-system | [38001866207](https://github.com/jbookout/carr-system/actions/runs/38001866207) | CI | Real PR bug | FAIL  gates       failed: orphan-reaper-selftest.py |
| carr-system | [38016274270](https://github.com/jbookout/carr-system/actions/runs/38016274270) | Edge liveness (cloud) | Expected alarm | Studio outage: previously observed services stale |
| carr-system | [38047206890](https://github.com/jbookout/carr-system/actions/runs/38047206890) | Edge liveness (cloud) | Expected alarm | Studio outage: previously observed services stale |
| carr-system | [38066703468](https://github.com/jbookout/carr-system/actions/runs/38066703468) | Edge liveness (cloud) | Expected alarm | Studio outage: previously observed services stale |
| carr-system | [38084145733](https://github.com/jbookout/carr-system/actions/runs/38084145733) | Edge liveness (cloud) | Expected alarm | Studio outage: previously observed services stale |
| carr-system | [38092175267](https://github.com/jbookout/carr-system/actions/runs/38092175267) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt |
| carr-system | [38095120893](https://github.com/jbookout/carr-system/actions/runs/38095120893) | CI | Real PR bug | FAIL  gates       failed: test_merge_queue_liveness.py |
| carr-system | [38099900163](https://github.com/jbookout/carr-system/actions/runs/38099900163) | CI | Real PR bug | FAIL  gates       failed: ci-selftest.py |
| carr-system | [38103011301](https://github.com/jbookout/carr-system/actions/runs/38103011301) | Edge liveness (cloud) | Expected alarm | Studio outage: previously observed services stale |
| carr-system | [38103213798](https://github.com/jbookout/carr-system/actions/runs/38103213798) | CI | Real PR bug | FAIL  gates       failed: ci-selftest.py |
| carr-system | [38108924641](https://github.com/jbookout/carr-system/actions/runs/38108924641) | CI | Real PR bug | FAIL  gates       failed: ci-selftest.py test_dot_processes.py; also has proven same-tree flake: test_dot_processes.py |
| carr-system | [38109167997](https://github.com/jbookout/carr-system/actions/runs/38109167997) | CI | Real PR bug | FAIL  gates       failed: sync-rule-boot-classes |
| carr-system | [38114987263](https://github.com/jbookout/carr-system/actions/runs/38114987263) | main canary | Real PR bug | FAIL  gates       failed: test-public-routing.py |
| carr-system | [38133333802](https://github.com/jbookout/carr-system/actions/runs/38133333802) | main canary | Real PR bug | FAIL  gates       failed: test-public-routing.py |
| carr-system | [38133675208](https://github.com/jbookout/carr-system/actions/runs/38133675208) | CI | Real PR bug | includes a main-inherited failure, not necessarily introduced by this PR; FAIL  gates       INHERITED FROM MAIN: test-public-routing.py |
| carr-system | [38135242415](https://github.com/jbookout/carr-system/actions/runs/38135242415) | Edge liveness (cloud) | Expected alarm | Studio outage: previously observed services stale |
| carr-system | [38135447937](https://github.com/jbookout/carr-system/actions/runs/38135447937) | CI | Real PR bug | FAIL  gates       failed: migration-order-gate-selftest.py pr-description-check-selftest.py test-public-routing.py |
| carr-system | [38136533089](https://github.com/jbookout/carr-system/actions/runs/38136533089) | main canary | Real PR bug | FAIL  gates       failed: test-public-routing.py |
| carr-system | [38137768883](https://github.com/jbookout/carr-system/actions/runs/38137768883) | CI | Real PR bug | includes a main-inherited failure, not necessarily introduced by this PR; FAIL  gates       INHERITED FROM MAIN: test-public-routing.py |
| carr-system | [38138074786](https://github.com/jbookout/carr-system/actions/runs/38138074786) | CI | Real PR bug | includes a main-inherited failure, not necessarily introduced by this PR; FAIL  gates       INHERITED FROM MAIN: test-public-routing.py |
| carr-system | [38145772868](https://github.com/jbookout/carr-system/actions/runs/38145772868) | CI | Flaky test | same-tree inline rerun passed: test_dot_processes.py |
| carr-system | [38145958802](https://github.com/jbookout/carr-system/actions/runs/38145958802) | CI | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [38149867068](https://github.com/jbookout/carr-system/actions/runs/38149867068) | main canary | Flaky test | same-tree inline rerun passed: test_dot_processes.py, test_merge_queue.py |
| carr-system | [38174162743](https://github.com/jbookout/carr-system/actions/runs/38174162743) | Edge liveness (cloud) | Expected alarm | Studio outage: previously observed services stale |
| carr-system | [38195152205](https://github.com/jbookout/carr-system/actions/runs/38195152205) | Edge liveness (cloud) | Expected alarm | Studio outage: previously observed services stale |
| carr-system | [38239712670](https://github.com/jbookout/carr-system/actions/runs/38239712670) | CI | Real PR bug | FAIL  gates       failed: migration-order-gate-selftest.py pr-description-check-selftest.py |
| carr-system | [38252867206](https://github.com/jbookout/carr-system/actions/runs/38252867206) | Edge liveness (cloud) | Expected alarm | Studio outage: previously observed services stale |
| carr-system | [38256744597](https://github.com/jbookout/carr-system/actions/runs/38256744597) | CI | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [38265627535](https://github.com/jbookout/carr-system/actions/runs/38265627535) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt |
| carr-system | [38272107845](https://github.com/jbookout/carr-system/actions/runs/38272107845) | CI | Flaky test | same-tree inline rerun passed: test_write_ownership_unit.py |
| carr-system | [38273862234](https://github.com/jbookout/carr-system/actions/runs/38273862234) | DB acceptance | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [38276398117](https://github.com/jbookout/carr-system/actions/runs/38276398117) | CI | Real PR bug | FAIL  gates       failed: codex-rule-hooks-selftest.py check-eval-receipt |
| carr-system | [38277437709](https://github.com/jbookout/carr-system/actions/runs/38277437709) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt |
| carr-system | [38278334226](https://github.com/jbookout/carr-system/actions/runs/38278334226) | CI | Real PR bug | FAIL  migration   migration shadow: pending migrations did not apply over the base snapshot, or db/schema.sql is not what they produce |
| carr-system | [38284168570](https://github.com/jbookout/carr-system/actions/runs/38284168570) | CI | Real PR bug | FAIL  gates       failed: grok-session-selftest.py |
| carr-system | [38292853603](https://github.com/jbookout/carr-system/actions/runs/38292853603) | CI | Real PR bug | FAIL  migration   migration shadow: pending migrations did not apply over the base snapshot, or db/schema.sql is not what they produce; FAIL  gates       failed: migration-safety-gate |
| carr-system | [38292900056](https://github.com/jbookout/carr-system/actions/runs/38292900056) | CI | Flaky test | same-tree inline rerun passed: test-smoke-transport.sh, test_merge_queue.py |
| carr-system | [38295781763](https://github.com/jbookout/carr-system/actions/runs/38295781763) | CI | Real PR bug | includes a main-inherited failure, not necessarily introduced by this PR; FAIL  gates       INHERITED FROM MAIN: mechanism-doctrine-gate |
| carr-system | [38295880476](https://github.com/jbookout/carr-system/actions/runs/38295880476) | CI | Real PR bug | includes a main-inherited failure, not necessarily introduced by this PR; FAIL  gates       INHERITED FROM MAIN: mechanism-doctrine-gate |
| carr-system | [38296339293](https://github.com/jbookout/carr-system/actions/runs/38296339293) | main canary | Real PR bug | FAIL  gates       failed: test_merge_queue.py mechanism-doctrine-gate; also has proven same-tree flake: test_merge_queue.py |
| carr-system | [38296508861](https://github.com/jbookout/carr-system/actions/runs/38296508861) | CI | Real PR bug | FAIL  gates       failed: test-handler-reads-are-granted.py mechanism-doctrine-gate check-eval-receipt migration-order-gate |
| carr-system | [38296639363](https://github.com/jbookout/carr-system/actions/runs/38296639363) | CI | Real PR bug | includes a main-inherited failure, not necessarily introduced by this PR; FAIL  gates       INHERITED FROM MAIN: mechanism-doctrine-gate |
| carr-system | [38296662043](https://github.com/jbookout/carr-system/actions/runs/38296662043) | CI | Real PR bug | includes a main-inherited failure, not necessarily introduced by this PR; FAIL  gates       INHERITED FROM MAIN: mechanism-doctrine-gate |
| carr-system | [38296791799](https://github.com/jbookout/carr-system/actions/runs/38296791799) | CI | Real PR bug | includes a main-inherited failure, not necessarily introduced by this PR; FAIL  gates       INHERITED FROM MAIN: mechanism-doctrine-gate |
| carr-system | [38301669656](https://github.com/jbookout/carr-system/actions/runs/38301669656) | CI | Real PR bug | FAIL  gates       failed: guidance-registry-selftest.py guidance-situation-curation-selftest.py rule-triage-selftest.py audit-queue-freshness-check rule-classification-parity-check sync-rule-boot-classes |
| carr-system | [38303216429](https://github.com/jbookout/carr-system/actions/runs/38303216429) | CI | Real PR bug | FAIL  gates       failed: release-abandon-selftest.py check-eval-receipt migration-safety-gate |
| carr-system | [38303216650](https://github.com/jbookout/carr-system/actions/runs/38303216650) | DB acceptance | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [38305881195](https://github.com/jbookout/carr-system/actions/runs/38305881195) | CI | Real PR bug | FAIL  gates       failed: guidance-registry-selftest.py guidance-situation-curation-selftest.py rule-triage-selftest.py audit-queue-freshness-check rule-classification-parity-check sync-rule-boot-classes |
| carr-system | [38306094895](https://github.com/jbookout/carr-system/actions/runs/38306094895) | CI | Flaky test | same-tree inline rerun passed: test_dot_processes.py |
| carr-system | [38309626958](https://github.com/jbookout/carr-system/actions/runs/38309626958) | CI | Real PR bug | FAIL  gates       failed: test_dot_processes.py migration-safety-gate; FAIL  migration   migration shadow: pending migrations did not apply over the base snapshot, or db/schema.sql is not what they produce; als |
| carr-system | [38310165482](https://github.com/jbookout/carr-system/actions/runs/38310165482) | CI | Real PR bug | FAIL  gates       failed: guidance-registry-selftest.py guidance-situation-curation-selftest.py rule-triage-selftest.py audit-queue-freshness-check rule-classification-parity-check sync-rule-boot-classes |
| carr-system | [38310283919](https://github.com/jbookout/carr-system/actions/runs/38310283919) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt migration-safety-gate; FAIL  migration   migration shadow: pending migrations did not apply over the base snapshot, or db/schema.sql is not what they produce |
| carr-system | [38314986733](https://github.com/jbookout/carr-system/actions/runs/38314986733) | CI | Real PR bug | FAIL  gates       failed: guidance-registry-selftest.py guidance-situation-curation-selftest.py rule-triage-selftest.py audit-queue-freshness-check rule-classification-parity-check sync-rule-boot-classes |
| carr-system | [38316618966](https://github.com/jbookout/carr-system/actions/runs/38316618966) | CI | Real PR bug | FAIL  gates       failed: test-handler-reads-are-granted.py check-eval-receipt |
| carr-system | [38319911208](https://github.com/jbookout/carr-system/actions/runs/38319911208) | main canary | Flaky test | same-tree inline rerun passed: test_merge_queue.py |
| carr-system | [38320504877](https://github.com/jbookout/carr-system/actions/runs/38320504877) | main canary | Flaky test | same-tree inline rerun passed: test_merge_queue.py |
| carr-system | [38322312289](https://github.com/jbookout/carr-system/actions/runs/38322312289) | CI | Real PR bug | FAIL  gates       failed: test_dot_processes.py check-eval-receipt; also has proven same-tree flake: test_dot_processes.py |
| carr-system | [38322511973](https://github.com/jbookout/carr-system/actions/runs/38322511973) | CI | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [38322873936](https://github.com/jbookout/carr-system/actions/runs/38322873936) | Edge liveness (cloud) | Expected alarm | Studio outage: previously observed services stale |
| carr-system | [38323237797](https://github.com/jbookout/carr-system/actions/runs/38323237797) | CI | Real PR bug | FAIL  gates       failed: guidance-registry-selftest.py guidance-situation-curation-selftest.py rule-triage-selftest.py audit-queue-freshness-check rule-classification-parity-check sync-rule-boot-classes |
| carr-system | [38325837181](https://github.com/jbookout/carr-system/actions/runs/38325837181) | CI | Real PR bug | FAIL  gates       failed: guidance-registry-selftest.py guidance-situation-curation-selftest.py rule-triage-selftest.py audit-queue-freshness-check rule-classification-parity-check sync-rule-boot-classes |
| carr-system | [38325871586](https://github.com/jbookout/carr-system/actions/runs/38325871586) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt |
| carr-system | [38325894149](https://github.com/jbookout/carr-system/actions/runs/38325894149) | CI | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [38325917193](https://github.com/jbookout/carr-system/actions/runs/38325917193) | CI | Real PR bug | FAIL  gates       failed: test-handler-reads-are-granted.py check-eval-receipt |
| carr-system | [38337894158](https://github.com/jbookout/carr-system/actions/runs/38337894158) | CI | Flaky test | same-tree inline rerun passed: test_merge_queue.py |
| carr-system | [38343743963](https://github.com/jbookout/carr-system/actions/runs/38343743963) | CI | Flaky test | same-tree inline rerun passed: test_merge_queue.py |
| carr-system | [38349935516](https://github.com/jbookout/carr-system/actions/runs/38349935516) | CI | Flaky test | same-tree inline rerun passed: test_codex_live_unit.py, test_dot_processes.py |
| carr-system | [38351127794](https://github.com/jbookout/carr-system/actions/runs/38351127794) | CI | Real PR bug | FAIL  migration   migration shadow: pending migrations did not apply over the base snapshot, or db/schema.sql is not what they produce |
| carr-system | [38389095222](https://github.com/jbookout/carr-system/actions/runs/38389095222) | CI | Real PR bug | FAIL  gates       failed: ci-selftest.py gate-integrity; FAIL  pushfloor   failed: gate-integrity (0s) |
| carr-system | [38390852341](https://github.com/jbookout/carr-system/actions/runs/38390852341) | CI | Real PR bug | FAIL  pushfloor   failed: gate-integrity (0s); FAIL  gates       failed: ci-selftest.py gate-integrity |
| carr-system | [38393612316](https://github.com/jbookout/carr-system/actions/runs/38393612316) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt |
| carr-system | [38398561144](https://github.com/jbookout/carr-system/actions/runs/38398561144) | Edge liveness (cloud) | Expected alarm | Studio outage: previously observed services stale |
| carr-system | [38407316848](https://github.com/jbookout/carr-system/actions/runs/38407316848) | CI | Real PR bug | FAIL  gates       failed: sync-rule-boot-classes-selftest.py sync-rule-boot-classes |
| carr-system | [38410455805](https://github.com/jbookout/carr-system/actions/runs/38410455805) | CI | Real PR bug | FAIL  gates       failed: local-review-evidence-selftest.py; FAIL  secret      secret or client data in introduced Git blobs |
| carr-system | [38410456374](https://github.com/jbookout/carr-system/actions/runs/38410456374) | Leak guard | Real PR bug | FAIL  secret      secret or client data in introduced Git blobs |
| carr-system | [38411229735](https://github.com/jbookout/carr-system/actions/runs/38411229735) | CI | Real PR bug | FAIL  migration   migration shadow: pending migrations did not apply over the base snapshot, or db/schema.sql is not what they produce |
| carr-system | [38413524811](https://github.com/jbookout/carr-system/actions/runs/38413524811) | CI | Real PR bug | FAIL  secret      secret or client data in introduced Git blobs |
| carr-system | [38413596937](https://github.com/jbookout/carr-system/actions/runs/38413596937) | Leak guard | Real PR bug | FAIL  secret      secret or client data in introduced Git blobs |
| carr-system | [38459257118](https://github.com/jbookout/carr-system/actions/runs/38459257118) | Leak guard | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [38465406005](https://github.com/jbookout/carr-system/actions/runs/38465406005) | CI | Real PR bug | FAIL  pushfloor   failed: gate-integrity (0s); FAIL  gates       failed: check-eval-receipt-selftest.py ci-selftest.py guidance-registry-import-selftest.py guidance-situation-curation-selftest.py rule-delivery- |
| carr-system | [38465406106](https://github.com/jbookout/carr-system/actions/runs/38465406106) | DB acceptance | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [38473208628](https://github.com/jbookout/carr-system/actions/runs/38473208628) | Edge liveness (cloud) | Expected alarm | Studio outage: previously observed services stale |
| carr-system | [38477876846](https://github.com/jbookout/carr-system/actions/runs/38477876846) | CI | Real PR bug | FAIL  migration   migration shadow: pending migrations did not apply over the base snapshot, or db/schema.sql is not what they produce |
| carr-system | [38479170675](https://github.com/jbookout/carr-system/actions/runs/38479170675) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt-selftest.py jev-calibration-selftest.py rule-delivery-eval-selftest.py rule-trigger-compile-selftest.py test-pii-guard.py test-public-routing.py rule-classification- |
| carr-system | [38480809078](https://github.com/jbookout/carr-system/actions/runs/38480809078) | CI | Real PR bug | includes a main-inherited failure, not necessarily introduced by this PR; FAIL  gates       INHERITED FROM MAIN: check-eval-receipt |
| carr-system | [38497633972](https://github.com/jbookout/carr-system/actions/runs/38497633972) | Edge liveness (cloud) | Expected alarm | Studio outage: previously observed services stale |
| carr-system | [38546856806](https://github.com/jbookout/carr-system/actions/runs/38546856806) | Edge liveness (cloud) | Expected alarm | Studio outage: previously observed services stale |
| carr-system | [38592390696](https://github.com/jbookout/carr-system/actions/runs/38592390696) | Edge liveness (cloud) | Expected alarm | Studio outage: previously observed services stale |
| carr-system | [38600110984](https://github.com/jbookout/carr-system/actions/runs/38600110984) | CI | Real PR bug | FAIL  gates       failed: store-token-selftest.py |
| carr-system | [38600678404](https://github.com/jbookout/carr-system/actions/runs/38600678404) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt-selftest.py jev-calibration-selftest.py rule-delivery-eval-selftest.py rule-trigger-compile-selftest.py test-pii-guard.py test-public-routing.py rule-classification- |
| carr-system | [38603188947](https://github.com/jbookout/carr-system/actions/runs/38603188947) | CI | Real PR bug | FAIL  gates       failed: jev-calibration-selftest.py rule-delivery-eval-selftest.py rule-trigger-compile-selftest.py test-pii-guard.py test-public-routing.py rule-classification-parity-check check-eval-receipt |
| carr-system | [38615827753](https://github.com/jbookout/carr-system/actions/runs/38615827753) | DB acceptance | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [38615827791](https://github.com/jbookout/carr-system/actions/runs/38615827791) | CI | Real PR bug | FAIL  gates       failed: migrate-precondition-selftest.py migration-number-contract-selftest.py release-abandon-selftest.py room-bridge-selftest.py migration-safety-gate; also has proven same-tree flake: room- |
| carr-system | [38616785603](https://github.com/jbookout/carr-system/actions/runs/38616785603) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt-selftest.py jev-calibration-selftest.py rule-delivery-eval-selftest.py rule-trigger-compile-selftest.py sync-rule-boot-classes-selftest.py test-pii-guard.py test-pub |
| carr-system | [38618290911](https://github.com/jbookout/carr-system/actions/runs/38618290911) | DB acceptance | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [38619044529](https://github.com/jbookout/carr-system/actions/runs/38619044529) | DB acceptance | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [38621175502](https://github.com/jbookout/carr-system/actions/runs/38621175502) | DB acceptance | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [38621175578](https://github.com/jbookout/carr-system/actions/runs/38621175578) | CI | Real PR bug | if not ok then; FAIL  gates       failed: migration-safety-gate door-reachability-check |
| carr-system | [38628796500](https://github.com/jbookout/carr-system/actions/runs/38628796500) | DB acceptance | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [38628796565](https://github.com/jbookout/carr-system/actions/runs/38628796565) | CI | Real PR bug | FAIL  gates       failed: migration-safety-gate |
| carr-system | [38633396531](https://github.com/jbookout/carr-system/actions/runs/38633396531) | Edge liveness (cloud) | Expected alarm | Studio outage: previously observed services stale |
| carr-system | [38640043413](https://github.com/jbookout/carr-system/actions/runs/38640043413) | CI | Real PR bug | FAIL  gates       failed: review-tiers-selftest.py |
| carr-system | [38644913648](https://github.com/jbookout/carr-system/actions/runs/38644913648) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt |
| carr-system | [38644931404](https://github.com/jbookout/carr-system/actions/runs/38644931404) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt |
| carr-system | [38647582301](https://github.com/jbookout/carr-system/actions/runs/38647582301) | CI | Real PR bug | FAIL  gates       failed: ci-selftest.py |
| carr-system | [38665389172](https://github.com/jbookout/carr-system/actions/runs/38665389172) | Edge liveness (cloud) | Expected alarm | Studio outage: previously observed services stale |
| carr-system | [38665614091](https://github.com/jbookout/carr-system/actions/runs/38665614091) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt-selftest.py jev-calibration-selftest.py rule-delivery-eval-selftest.py rule-trigger-compile-selftest.py sync-rule-boot-classes-selftest.py test-pii-guard.py test-pub |
| carr-system | [38668121066](https://github.com/jbookout/carr-system/actions/runs/38668121066) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt-selftest.py jev-calibration-selftest.py rule-delivery-eval-selftest.py rule-trigger-compile-selftest.py test-pii-guard.py test-public-routing.py check-eval-receipt |
| carr-system | [38668207156](https://github.com/jbookout/carr-system/actions/runs/38668207156) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt |
| carr-system | [38669223168](https://github.com/jbookout/carr-system/actions/runs/38669223168) | CI | Real PR bug | includes a main-inherited failure, not necessarily introduced by this PR; FAIL  gates       INHERITED FROM MAIN: check-eval-receipt |
| carr-system | [38670039541](https://github.com/jbookout/carr-system/actions/runs/38670039541) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt |
| carr-system | [38691871629](https://github.com/jbookout/carr-system/actions/runs/38691871629) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt |
| carr-system | [38715472890](https://github.com/jbookout/carr-system/actions/runs/38715472890) | Edge liveness (cloud) | Expected alarm | Studio outage: previously observed services stale |
| carr-system | [38750204827](https://github.com/jbookout/carr-system/actions/runs/38750204827) | main canary | Real PR bug | FAIL  gates       failed: ci-selftest.py |
| carr-system | [38752333748](https://github.com/jbookout/carr-system/actions/runs/38752333748) | CI | Real PR bug | includes a main-inherited failure, not necessarily introduced by this PR; FAIL  gates       INHERITED FROM MAIN: ci-selftest.py |
| carr-system | [38755219679](https://github.com/jbookout/carr-system/actions/runs/38755219679) | main canary | Real PR bug | FAIL  gates       failed: ci-selftest.py |
| carr-system | [38755437452](https://github.com/jbookout/carr-system/actions/runs/38755437452) | CI | Real PR bug | includes a main-inherited failure, not necessarily introduced by this PR; FAIL  gates       INHERITED FROM MAIN: ci-selftest.py |
| carr-system | [38757942829](https://github.com/jbookout/carr-system/actions/runs/38757942829) | CI | Real PR bug | includes a main-inherited failure, not necessarily introduced by this PR; FAIL  gates       INHERITED FROM MAIN: ci-selftest.py |
| carr-system | [38758317765](https://github.com/jbookout/carr-system/actions/runs/38758317765) | CI | Real PR bug | includes a main-inherited failure, not necessarily introduced by this PR; FAIL  gates       INHERITED FROM MAIN: ci-selftest.py |
| carr-system | [38761236094](https://github.com/jbookout/carr-system/actions/runs/38761236094) | CI | Real PR bug | includes a main-inherited failure, not necessarily introduced by this PR; FAIL  gates       INHERITED FROM MAIN: ci-selftest.py |
| carr-system | [38761374323](https://github.com/jbookout/carr-system/actions/runs/38761374323) | Edge liveness (cloud) | Expected alarm | Studio outage: previously observed services stale |
| carr-system | [38765450793](https://github.com/jbookout/carr-system/actions/runs/38765450793) | CI | Real PR bug | FAIL  gates       failed: door-reachability-check |
| carr-system | [38765616632](https://github.com/jbookout/carr-system/actions/runs/38765616632) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt |
| carr-system | [38773326486](https://github.com/jbookout/carr-system/actions/runs/38773326486) | CI | Real PR bug | FAIL  gates       failed: ci-selftest.py |
| carr-system | [38775876123](https://github.com/jbookout/carr-system/actions/runs/38775876123) | DB acceptance | Real PR bug | failed required check; inspect linked failed-job evidence |
| carr-system | [38775891503](https://github.com/jbookout/carr-system/actions/runs/38775891503) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt |
| carr-system | [38777234750](https://github.com/jbookout/carr-system/actions/runs/38777234750) | CI | Real PR bug | FAIL  gates       failed: migration-safety-gate; FAIL  migration   migration shadow: pending migrations did not apply over the base snapshot, or db/schema.sql is not what they produce |
| carr-system | [38788046255](https://github.com/jbookout/carr-system/actions/runs/38788046255) | CI | Real PR bug | FAIL  gates       failed: check-eval-receipt door-reachability-check |
| carr-system | [38800607284](https://github.com/jbookout/carr-system/actions/runs/38800607284) | Edge liveness (cloud) | Expected alarm | Studio outage: previously observed services stale |
| carr-system | [38801870597](https://github.com/jbookout/carr-system/actions/runs/38801870597) | CI | Real PR bug | FAIL  gates       failed: ci-selftest.py check-eval-receipt |
| doctorcre-app | [37845640240](https://github.com/jbookout/doctorcre-app/actions/runs/37845640240) | e2e | Workflow misconfiguration | privacy scanner setup/input error, exit 2; old scanner hides specific configuration cause |
| doctorcre-app | [37845640273](https://github.com/jbookout/doctorcre-app/actions/runs/37845640273) | CI | Workflow misconfiguration | privacy scanner setup/input error, exit 2; old scanner hides specific configuration cause |
| doctorcre-app | [37845641226](https://github.com/jbookout/doctorcre-app/actions/runs/37845641226) | e2e | Workflow misconfiguration | privacy scanner setup/input error, exit 2; old scanner hides specific configuration cause |
| doctorcre-app | [37845650394](https://github.com/jbookout/doctorcre-app/actions/runs/37845650394) | CI | Workflow misconfiguration | privacy scanner setup/input error, exit 2; old scanner hides specific configuration cause |
| doctorcre-app | [37845650395](https://github.com/jbookout/doctorcre-app/actions/runs/37845650395) | e2e | Workflow misconfiguration | privacy scanner setup/input error, exit 2; old scanner hides specific configuration cause |
| doctorcre-app | [37845731441](https://github.com/jbookout/doctorcre-app/actions/runs/37845731441) | CI | Workflow misconfiguration | privacy scanner setup/input error, exit 2; old scanner hides specific configuration cause |
| doctorcre-app | [37845731712](https://github.com/jbookout/doctorcre-app/actions/runs/37845731712) | e2e | Workflow misconfiguration | privacy scanner setup/input error, exit 2; old scanner hides specific configuration cause |
| doctorcre-app | [37845740466](https://github.com/jbookout/doctorcre-app/actions/runs/37845740466) | e2e | Workflow misconfiguration | privacy scanner setup/input error, exit 2; old scanner hides specific configuration cause |
| doctorcre-app | [37845740532](https://github.com/jbookout/doctorcre-app/actions/runs/37845740532) | CI | Workflow misconfiguration | privacy scanner setup/input error, exit 2; old scanner hides specific configuration cause |
| doctorcre-app | [37845768104](https://github.com/jbookout/doctorcre-app/actions/runs/37845768104) | CI | Workflow misconfiguration | privacy scanner setup/input error, exit 2; old scanner hides specific configuration cause |
| doctorcre-app | [37845768131](https://github.com/jbookout/doctorcre-app/actions/runs/37845768131) | e2e | Workflow misconfiguration | privacy scanner setup/input error, exit 2; old scanner hides specific configuration cause |
| doctorcre-app | [37866229290](https://github.com/jbookout/doctorcre-app/actions/runs/37866229290) | e2e | Real PR bug | failed required check; inspect linked failed-job evidence |
| doctorcre-app | [37866229293](https://github.com/jbookout/doctorcre-app/actions/runs/37866229293) | CI | Real PR bug | not ok 508 - Renovate can discover immutable action refs without losing release provenance |
| doctorcre-app | [37867514658](https://github.com/jbookout/doctorcre-app/actions/runs/37867514658) | CI | Real PR bug | not ok 508 - Renovate can discover immutable action refs without losing release provenance |
| doctorcre-app | [37868615151](https://github.com/jbookout/doctorcre-app/actions/runs/37868615151) | e2e | Real PR bug | not ok 59 - recorded action proof refuses unbound identities and runtime traversal recovers Home without repeating Save |
| doctorcre-app | [37870344297](https://github.com/jbookout/doctorcre-app/actions/runs/37870344297) | e2e | Real PR bug | not ok 4 - multiple source roots retain nav presses and both tab obligations; owners cover local drawers separately on both viewports; not ok 59 - recorded action proof refuses unbound identities and runtime tr |
| doctorcre-app | [37872579052](https://github.com/jbookout/doctorcre-app/actions/runs/37872579052) | e2e | Real PR bug | not ok 4 - multiple source roots retain nav presses and both tab obligations; owners cover local drawers separately on both viewports |
| doctorcre-app | [37872579061](https://github.com/jbookout/doctorcre-app/actions/runs/37872579061) | CI | Real PR bug | not ok 1861 - every script-running JSDOM page is opened through the harness |
| doctorcre-app | [37878250565](https://github.com/jbookout/doctorcre-app/actions/runs/37878250565) | e2e | Real PR bug | not ok 55 - killing frontier publication before canonical commit retains pending discovery and all child coverage; not ok 59 - recorded action proof refuses unbound identities and runtime traversal recovers Hom |
| doctorcre-app | [37878250595](https://github.com/jbookout/doctorcre-app/actions/runs/37878250595) | CI | Real PR bug | not ok 1861 - every script-running JSDOM page is opened through the harness |
| doctorcre-app | [37885432563](https://github.com/jbookout/doctorcre-app/actions/runs/37885432563) | e2e | Real PR bug | not ok 8 - multiple source roots retain nav presses and both tab obligations; owners cover local drawers separately on both viewports; not ok 17 - board desktop and phone delegate Ideas and Calendar to matching |
| doctorcre-app | [37887402152](https://github.com/jbookout/doctorcre-app/actions/runs/37887402152) | e2e | Real PR bug | not ok 13 - multiple source roots retain nav presses and both tab obligations; owners cover local drawers separately on both viewports; not ok 20 - an unknown Calendar query preserves its source evidence and fa |
| doctorcre-app | [37920140647](https://github.com/jbookout/doctorcre-app/actions/runs/37920140647) | e2e | Workflow misconfiguration | full-main shadow rejects generated inputs as source_changed; exact offending path not logged |
| doctorcre-app | [37926557192](https://github.com/jbookout/doctorcre-app/actions/runs/37926557192) | bounded staging V1 continuation | Workflow misconfiguration | scheduled live sweep exits worker-exit-1 before coverage; schedule already removed on current main |
| doctorcre-app | [37948799162](https://github.com/jbookout/doctorcre-app/actions/runs/37948799162) | e2e | Real PR bug | not ok 179 - two authorized exploration batches receive separate bounded model budgets; not ok 228 - SIGINT reaps the report worker and resistant detached child within four seconds; not ok 230 - whole-run deadl |
| doctorcre-app | [37994860703](https://github.com/jbookout/doctorcre-app/actions/runs/37994860703) | e2e | Real PR bug | not ok 79 - an unproved morning dismiss retains an ERROR but does not stop later controls on the screen |
| doctorcre-app | [38043679830](https://github.com/jbookout/doctorcre-app/actions/runs/38043679830) | e2e | Workflow misconfiguration | full-main shadow rejects generated inputs as source_changed; exact offending path not logged |
| doctorcre-app | [38047495454](https://github.com/jbookout/doctorcre-app/actions/runs/38047495454) | bounded staging V1 continuation | Workflow misconfiguration | scheduled live sweep exits worker-exit-1 before coverage; schedule already removed on current main |
| doctorcre-app | [38092774165](https://github.com/jbookout/doctorcre-app/actions/runs/38092774165) | CI | Real PR bug | not ok 660 - R4 superseded Queue failure cannot replace newer facts |
| doctorcre-app | [38095090609](https://github.com/jbookout/doctorcre-app/actions/runs/38095090609) | e2e | Real PR bug | not ok 138 - production record preparation admits the reviewed app handler into the screen sweep and refuses unsupported handlers |
| doctorcre-app | [38101809256](https://github.com/jbookout/doctorcre-app/actions/runs/38101809256) | e2e | Real PR bug | not ok 140 - production record preparation admits the reviewed app handler into the screen sweep and refuses unsupported handlers |
| doctorcre-app | [38105159926](https://github.com/jbookout/doctorcre-app/actions/runs/38105159926) | e2e | Real PR bug | not ok 310 - SIGTERM reaps the report worker and resistant detached child within four seconds |
| doctorcre-app | [38107817962](https://github.com/jbookout/doctorcre-app/actions/runs/38107817962) | e2e | Real PR bug | not ok 304 - SIGTERM cannot strand a worker signal receipt in the ledger lock |
| doctorcre-app | [38109005929](https://github.com/jbookout/doctorcre-app/actions/runs/38109005929) | e2e | Real PR bug | not ok 96 - recorded action proof refuses unbound identities and runtime traversal recovers Home without repeating Save; not ok 140 - production record preparation admits the reviewed app handler into the scree |
| doctorcre-app | [38109042271](https://github.com/jbookout/doctorcre-app/actions/runs/38109042271) | CI | Real PR bug | not ok 214 - action pins agree with authenticated canonical release evidence; not ok 508 - Renovate can discover immutable action refs without losing release provenance |
| doctorcre-app | [38109131106](https://github.com/jbookout/doctorcre-app/actions/runs/38109131106) | e2e | Real PR bug | not ok 11 - every native entry refuses the working-tree server when a build is bound |
| doctorcre-app | [38109332758](https://github.com/jbookout/doctorcre-app/actions/runs/38109332758) | e2e | Real PR bug | not ok 140 - production record preparation admits the reviewed app handler into the screen sweep and refuses unsupported handlers |
| doctorcre-app | [38111184683](https://github.com/jbookout/doctorcre-app/actions/runs/38111184683) | e2e | Real PR bug | not ok 140 - production record preparation admits the reviewed app handler into the screen sweep and refuses unsupported handlers |
| doctorcre-app | [38111184740](https://github.com/jbookout/doctorcre-app/actions/runs/38111184740) | CI | Real PR bug | not ok 14 - honest controls fit 320px, 390px and iPad in both motion settings with 44px touch targets |
| doctorcre-app | [38111187255](https://github.com/jbookout/doctorcre-app/actions/runs/38111187255) | e2e | Real PR bug | not ok 140 - production record preparation admits the reviewed app handler into the screen sweep and refuses unsupported handlers |
| doctorcre-app | [38111612075](https://github.com/jbookout/doctorcre-app/actions/runs/38111612075) | e2e | Real PR bug | not ok 140 - production record preparation admits the reviewed app handler into the screen sweep and refuses unsupported handlers |
| doctorcre-app | [38111706618](https://github.com/jbookout/doctorcre-app/actions/runs/38111706618) | e2e | Real PR bug | not ok 140 - production record preparation admits the reviewed app handler into the screen sweep and refuses unsupported handlers |
| doctorcre-app | [38111713422](https://github.com/jbookout/doctorcre-app/actions/runs/38111713422) | e2e | Real PR bug | not ok 140 - production record preparation admits the reviewed app handler into the screen sweep and refuses unsupported handlers |
| doctorcre-app | [38111734166](https://github.com/jbookout/doctorcre-app/actions/runs/38111734166) | CI | Real PR bug | not ok 214 - action pins agree with authenticated canonical release evidence; not ok 508 - Renovate can discover immutable action refs without losing release provenance |
| doctorcre-app | [38112019377](https://github.com/jbookout/doctorcre-app/actions/runs/38112019377) | CI | Real PR bug | not ok 928 - no js/ module writes a style= attribute into markup (the Worker's CSP has no 'unsafe-inline') |
| doctorcre-app | [38112019380](https://github.com/jbookout/doctorcre-app/actions/runs/38112019380) | e2e | Real PR bug | not ok 140 - production record preparation admits the reviewed app handler into the screen sweep and refuses unsupported handlers |
| doctorcre-app | [38113556409](https://github.com/jbookout/doctorcre-app/actions/runs/38113556409) | e2e | Real PR bug | not ok 199 - production record preparation admits the reviewed app handler into the screen sweep and refuses unsupported handlers |
| doctorcre-app | [38131341036](https://github.com/jbookout/doctorcre-app/actions/runs/38131341036) | e2e | Workflow misconfiguration | full-main shadow rejects generated inputs as source_changed; exact offending path not logged |
| doctorcre-app | [38135421946](https://github.com/jbookout/doctorcre-app/actions/runs/38135421946) | bounded staging V1 continuation | Workflow misconfiguration | scheduled live sweep exits worker-exit-1 before coverage; schedule already removed on current main |
| doctorcre-app | [38152976002](https://github.com/jbookout/doctorcre-app/actions/runs/38152976002) | e2e | Real PR bug | not ok 199 - production record preparation admits the reviewed app handler into the screen sweep and refuses unsupported handlers |
| doctorcre-app | [38152976064](https://github.com/jbookout/doctorcre-app/actions/runs/38152976064) | CI | Real PR bug | not ok 511 - PR123 R1: Activity models the daily morning brief and rejects unknown operations; not ok 660 - R4 superseded Queue success cannot replace newer facts; not ok 661 - R4 superseded Queue failure canno |
| doctorcre-app | [38157894722](https://github.com/jbookout/doctorcre-app/actions/runs/38157894722) | e2e | Real PR bug | not ok 199 - production record preparation admits the reviewed app handler into the screen sweep and refuses unsupported handlers |
| doctorcre-app | [38157894731](https://github.com/jbookout/doctorcre-app/actions/runs/38157894731) | CI | Real PR bug | not ok 1936 - phone and iPad composers fit the viewport and reduced motion leaves every state legible |
| doctorcre-app | [38163217283](https://github.com/jbookout/doctorcre-app/actions/runs/38163217283) | e2e | Real PR bug | not ok 199 - production record preparation admits the reviewed app handler into the screen sweep and refuses unsupported handlers; not ok 259 - two authorized exploration batches receive separate bounded model  |
| doctorcre-app | [38193322870](https://github.com/jbookout/doctorcre-app/actions/runs/38193322870) | CI | Real PR bug | not ok 1323 - PR119 finding 8: a hung Home read expires and a late response cannot overwrite recovery |
| doctorcre-app | [38195348831](https://github.com/jbookout/doctorcre-app/actions/runs/38195348831) | e2e | Real PR bug | not ok 316 - aggregate log quota stops and reaps every owned group |
| doctorcre-app | [38197649434](https://github.com/jbookout/doctorcre-app/actions/runs/38197649434) | e2e | Real PR bug | not ok 259 - two authorized exploration batches receive separate bounded model budgets; not ok 314 - aggregate log quota stops and reaps every owned group |
| doctorcre-app | [38198643454](https://github.com/jbookout/doctorcre-app/actions/runs/38198643454) | e2e | Real PR bug | not ok 292 - HTTP queue overflow that never drains stops the run without starting replacements |
| doctorcre-app | [38240720665](https://github.com/jbookout/doctorcre-app/actions/runs/38240720665) | e2e | Real PR bug | not ok 316 - aggregate log quota stops and reaps every owned group |
| doctorcre-app | [38240859525](https://github.com/jbookout/doctorcre-app/actions/runs/38240859525) | CI | Real PR bug | not ok 931 - no js/ module writes a style= attribute into markup (the Worker's CSP has no 'unsafe-inline') |
| doctorcre-app | [38241517089](https://github.com/jbookout/doctorcre-app/actions/runs/38241517089) | e2e | Workflow misconfiguration | full-main shadow rejects generated inputs as source_changed; exact offending path not logged |
| doctorcre-app | [38242702186](https://github.com/jbookout/doctorcre-app/actions/runs/38242702186) | e2e | Real PR bug | not ok 199 - production record preparation admits the reviewed app handler into the screen sweep and refuses unsupported handlers |
| doctorcre-app | [38244566193](https://github.com/jbookout/doctorcre-app/actions/runs/38244566193) | e2e | Real PR bug | not ok 259 - two authorized exploration batches receive separate bounded model budgets; not ok 306 - SIGTERM cannot strand a worker signal receipt in the ledger lock; not ok 310 - SIGTERM reaps the exploration  |
| doctorcre-app | [38244660000](https://github.com/jbookout/doctorcre-app/actions/runs/38244660000) | e2e | Real PR bug | not ok 199 - production record preparation admits the reviewed app handler into the screen sweep and refuses unsupported handlers |
| doctorcre-app | [38245634761](https://github.com/jbookout/doctorcre-app/actions/runs/38245634761) | CI | Real PR bug | not ok 1144 - C13-04 the contract pins the merged producer, its two dispatch writes, and V5-UX-C13b's composer write |
| doctorcre-app | [38253792023](https://github.com/jbookout/doctorcre-app/actions/runs/38253792023) | bounded staging V1 continuation | Workflow misconfiguration | scheduled live sweep exits worker-exit-1 before coverage; schedule already removed on current main |
| doctorcre-app | [38275856462](https://github.com/jbookout/doctorcre-app/actions/runs/38275856462) | e2e | Real PR bug | not ok 157 - recorded action proof refuses unbound identities and runtime traversal recovers Home without repeating Save |
| doctorcre-app | [38277442031](https://github.com/jbookout/doctorcre-app/actions/runs/38277442031) | e2e | Real PR bug | failed required check; inspect linked failed-job evidence |
| doctorcre-app | [38277442210](https://github.com/jbookout/doctorcre-app/actions/runs/38277442210) | CI | Real PR bug | not ok 1639 - QA-005 phone drawers leave room under the floating Doc control |
| doctorcre-app | [38280821660](https://github.com/jbookout/doctorcre-app/actions/runs/38280821660) | e2e | Real PR bug | not ok 327 - aggregate log quota stops and reaps every owned group |
| doctorcre-app | [38281573938](https://github.com/jbookout/doctorcre-app/actions/runs/38281573938) | e2e | Real PR bug | not ok 329 - a corrupt registry never prevents known groups from being reaped |
| doctorcre-app | [38290316599](https://github.com/jbookout/doctorcre-app/actions/runs/38290316599) | e2e | Real PR bug | failed required check; inspect linked failed-job evidence |
| doctorcre-app | [38294957379](https://github.com/jbookout/doctorcre-app/actions/runs/38294957379) | e2e | Real PR bug | not ok 72 - every review citation names the line that defines its control |
| doctorcre-app | [38295747445](https://github.com/jbookout/doctorcre-app/actions/runs/38295747445) | e2e | Real PR bug | failed required check; inspect linked failed-job evidence |
| doctorcre-app | [38299112021](https://github.com/jbookout/doctorcre-app/actions/runs/38299112021) | e2e | Real PR bug | not ok 328 - aggregate log quota stops and reaps every owned group |
| doctorcre-app | [38299522825](https://github.com/jbookout/doctorcre-app/actions/runs/38299522825) | CI | Real PR bug | not ok 1493 - cards and open Key have no clipping or overlap at 390px on carr-v5; not ok 1494 - cards and open Key have no clipping or overlap at 390px on all-repos |
| doctorcre-app | [38301565371](https://github.com/jbookout/doctorcre-app/actions/runs/38301565371) | e2e | Real PR bug | not ok 326 - SIGTERM reaps the report worker and resistant detached child within four seconds |
| doctorcre-app | [38305728472](https://github.com/jbookout/doctorcre-app/actions/runs/38305728472) | e2e | Real PR bug | not ok 128 - fresh opener replay waits for delayed async nested drawers without a busy attribute; not ok 330 - a corrupt registry never prevents known groups from being reaped |
| doctorcre-app | [38384171953](https://github.com/jbookout/doctorcre-app/actions/runs/38384171953) | CI | Workflow misconfiguration | full-main shadow rejects generated inputs as source_changed; exact offending path not logged |
| doctorcre-app | [38386939195](https://github.com/jbookout/doctorcre-app/actions/runs/38386939195) | e2e | Workflow misconfiguration | full-main shadow rejects generated inputs as source_changed; exact offending path not logged |
| doctorcre-app | [38399280058](https://github.com/jbookout/doctorcre-app/actions/runs/38399280058) | bounded staging V1 continuation | Workflow misconfiguration | scheduled live sweep exits worker-exit-1 before coverage; schedule already removed on current main |
| doctorcre-app | [38478535303](https://github.com/jbookout/doctorcre-app/actions/runs/38478535303) | CI | Real PR bug | not ok 1743 - both actual publishers refuse ancestor, mismatched and external Wrangler entrypoints; not ok 1744 - real browser preparation preserves tracked source and both publishers accept its artifact; not o |
| doctorcre-app | [38536880353](https://github.com/jbookout/doctorcre-app/actions/runs/38536880353) | CI | Workflow misconfiguration | full-main shadow rejects generated inputs as source_changed; exact offending path not logged |
| doctorcre-app | [38539000825](https://github.com/jbookout/doctorcre-app/actions/runs/38539000825) | e2e | Workflow misconfiguration | full-main shadow rejects generated inputs as source_changed; exact offending path not logged |
| doctorcre-app | [38547929262](https://github.com/jbookout/doctorcre-app/actions/runs/38547929262) | bounded staging V1 continuation | Workflow misconfiguration | scheduled live sweep exits worker-exit-1 before coverage; schedule already removed on current main |
| doctorcre-app | [38628495735](https://github.com/jbookout/doctorcre-app/actions/runs/38628495735) | e2e | Real PR bug | failed required check; inspect linked failed-job evidence |
| doctorcre-app | [38648997949](https://github.com/jbookout/doctorcre-app/actions/runs/38648997949) | e2e | Flaky test | supervisor fixture short lock grace races child bookkeeping; identical source passed in run 38646040969 |
| doctorcre-app | [38650589113](https://github.com/jbookout/doctorcre-app/actions/runs/38650589113) | e2e | Real PR bug | not ok 331 - SIGINT reaps the report worker and resistant detached child within four seconds; not ok 332 - SIGTERM reaps the report worker and resistant detached child within four seconds |
| doctorcre-app | [38664988998](https://github.com/jbookout/doctorcre-app/actions/runs/38664988998) | e2e | Real PR bug | not ok 7 - independent readback without move UI witness blocks canonical coverage and continuation; not ok 8 - independent readback without undo UI witness blocks canonical coverage and continuation; not ok 9 - |
| doctorcre-app | [38665748636](https://github.com/jbookout/doctorcre-app/actions/runs/38665748636) | e2e | Real PR bug | not ok 150 - fresh opener replay waits for delayed async nested drawers without a busy attribute |
| doctorcre-app | [38665768609](https://github.com/jbookout/doctorcre-app/actions/runs/38665768609) | e2e | Real PR bug | not ok 7 - independent readback without move UI witness blocks canonical coverage and continuation; not ok 8 - independent readback without undo UI witness blocks canonical coverage and continuation; not ok 9 - |
| doctorcre-app | [38672627684](https://github.com/jbookout/doctorcre-app/actions/runs/38672627684) | e2e | Real PR bug | failed required check; inspect linked failed-job evidence |
| doctorcre-app | [38672627706](https://github.com/jbookout/doctorcre-app/actions/runs/38672627706) | CI | Real PR bug | failed required check; inspect linked failed-job evidence |
| doctorcre-app | [38673636598](https://github.com/jbookout/doctorcre-app/actions/runs/38673636598) | e2e | Real PR bug | not ok 84 - the checkout carries the exact reviewed bytes of every control-proof source; not ok 86 - preparation admits proofs across unrelated changes and refuses a changed reviewed source; not ok 224 - produc |
| doctorcre-app | [38676558525](https://github.com/jbookout/doctorcre-app/actions/runs/38676558525) | e2e | Real PR bug | not ok 432 - aggregate log quota stops and reaps every owned group |
| doctorcre-app | [38677223015](https://github.com/jbookout/doctorcre-app/actions/runs/38677223015) | e2e | Real PR bug | not ok 328 - HTTP queue overflow that never drains stops the run without starting replacements |
| doctorcre-app | [38688093329](https://github.com/jbookout/doctorcre-app/actions/runs/38688093329) | e2e | Real PR bug | not ok 97 - wrong key and ciphertext tampering fail closed without a partial restore; not ok 131 - wrong id, digest, generation, predecessor, plan or source never reaches a restored worker |
| doctorcre-app | [38694325378](https://github.com/jbookout/doctorcre-app/actions/runs/38694325378) | e2e | Real PR bug | not ok 131 - four serial caller slots carry 5,140 incompressible retained files inside logical and physical caps |
| doctorcre-app | [38704516972](https://github.com/jbookout/doctorcre-app/actions/runs/38704516972) | CI | Workflow misconfiguration | full-main shadow rejects generated inputs as source_changed; exact offending path not logged |
| doctorcre-app | [38706451363](https://github.com/jbookout/doctorcre-app/actions/runs/38706451363) | e2e | Workflow misconfiguration | full-main shadow rejects generated inputs as source_changed; exact offending path not logged |
| doctorcre-app | [38709950959](https://github.com/jbookout/doctorcre-app/actions/runs/38709950959) | e2e | Real PR bug | not ok 88 - every exported staging-context entry point refuses missing opt-in, including an explicitly supplied budget; not ok 153 - local automatic producer receives a fresh private plan only after manual W4/W |
| doctorcre-app | [38714937195](https://github.com/jbookout/doctorcre-app/actions/runs/38714937195) | e2e | Real PR bug | not ok 18 - W15 missing-ui retains quarantine and refuses canonical coverage and replay; not ok 21 - served signed-in app W4/W9 uses the real UI, canonical fixture guard and durable receipts |
| doctorcre-app | [38723373078](https://github.com/jbookout/doctorcre-app/actions/runs/38723373078) | CI | Real PR bug | not ok 2247 - signed-in dell gets workspace, account, persisted controls and sign-out |
| doctorcre-app | [38731149502](https://github.com/jbookout/doctorcre-app/actions/runs/38731149502) | e2e | Real PR bug | not ok 77 - each family cites the line that renders its control, as do its proofs; not ok 139 - global budget admission rejects exhausted counters, unfinished resources and altered immutable deadline; not ok 14 |
| doctorcre-app | [38737303967](https://github.com/jbookout/doctorcre-app/actions/runs/38737303967) | e2e | Real PR bug | not ok 144 - global budget admission rejects exhausted counters, unfinished resources and altered immutable deadline; not ok 146 - cutoff crossed at the actual envelope link refuses success and retains the priv |
| doctorcre-app | [38739784278](https://github.com/jbookout/doctorcre-app/actions/runs/38739784278) | e2e | Real PR bug | not ok 1 - phase change, event-specific undo and date all have UI results and independent reads; not ok 21 - served signed-in app W4/W9 uses the real UI, canonical fixture guard and durable receipts |
| doctorcre-app | [38740237479](https://github.com/jbookout/doctorcre-app/actions/runs/38740237479) | e2e | Real PR bug | not ok 159 - four serial caller slots carry 5,140 incompressible retained files inside logical and physical caps |
| doctorcre-app | [38768314057](https://github.com/jbookout/doctorcre-app/actions/runs/38768314057) | e2e | Real PR bug | not ok 1 - phase change, event-specific undo and date all have UI results and independent reads; not ok 3 - W9 date cancel and retry leave one observed date with independent readback; not ok 4 - interrupted dis |
| doctorcre-app | [38772396910](https://github.com/jbookout/doctorcre-app/actions/runs/38772396910) | e2e | Real PR bug | not ok 1 - phase change, event-specific undo and date all have UI results and independent reads; not ok 3 - W9 date cancel and retry leave one observed date with independent readback; not ok 4 - interrupted dis |
| doctorcre-app | [38780953062](https://github.com/jbookout/doctorcre-app/actions/runs/38780953062) | e2e | Real PR bug | not ok 4 - interrupted dispatch is durable and rerun sends no second mutation; not ok 5 - wrong undo event cannot cross the admitted event binding; not ok 17 - W15 lost-response retains quarantine and refuses c |
| doctorcre-app | [38793086329](https://github.com/jbookout/doctorcre-app/actions/runs/38793086329) | e2e | Real PR bug | not ok 8 - independent readback without move UI witness blocks canonical coverage and continuation; not ok 10 - independent readback without date UI witness blocks canonical coverage and continuation; not ok 21 |
| doctorcre-app | [38797618982](https://github.com/jbookout/doctorcre-app/actions/runs/38797618982) | e2e | Real PR bug | not ok 1 - phase change, event-specific undo and date all have UI results and independent reads; not ok 10 - independent readback without date UI witness blocks canonical coverage and continuation; not ok 21 -  |
| doctorcre-app | [38803206206](https://github.com/jbookout/doctorcre-app/actions/runs/38803206206) | e2e | Real PR bug | not ok 1 - phase change, event-specific undo and date all have UI results and independent reads; not ok 21 - served signed-in app W4/W9 uses the real UI, canonical fixture guard and durable receipts |
