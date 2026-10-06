# Phase 0 audit and council packet — 2026-08-12, 17:00 CT

## Boundary and pinned inputs

This is an audit runbook, not construction authority. No production migration,
deployment, access grant, DNS change, or irreversible implementation may occur
until the council recommends an integrated roadmap and Joe approves it.

Audit this exact frozen source, not the main checkout:

| Item | Pinned source |
|---|---|
| Phase 0 package | worktree `/Users/booko/carr-system/.claude/worktrees/control-room-phase0` |
| `BASE_SHA` | `d4e0f67259d633bfe16272e070530ea81f08a4a9` — frozen pre-readiness package |
| `AUDIT_RELEASE_SHA` | `________________` — fill after the approved package is integrated for audit; this is the SHA the council audits |
| Integration comparison | `origin/main` at audit start; record the observed SHA and the resulting `AUDIT_RELEASE_SHA` |
| Recovery bundle | `CARR AI/Backups/phase0-audit-2026-08-12/control-room-phase0-preintegration-d4e0f67.bundle` — verified complete pre-integration history |
| Canonical roadmaps | `out/mirror/md/reference/carr-mature-software-end-state-bduf.md`, `carr-workspace-bduf.md`, `carr-control-room-bduf.md`, `carr-production-maturity-baseline.md` |
| Workspace package | `workspace/contracts/phase0-manifest.v1.json`, `phase0-acceptance.v1.json`, `council-review-register.v1.json` |
| Control Room package | `control-room/contracts/phase0-manifest.v1.json`, `phase0-acceptance.v1.json`, `council-review.v1.json` |
| Cross-product program | `phase0/program.v1.json`, `phase0/cross-product-boundary.v1.json`, `phase0/roadmap-revisions.v1.json` |

Historical roadmap text may say 143 shared rules or cite an old branch state.
At the session start, call `standing-context` and record the returned counts.
The current verified post-repair fact is 144 shared and 30 Joe-personal rules.

## Dell migration boundary

Dell's secondary-machine migration is operationally complete. His Mac now uses
`~/carr-system` and the live record/doctrine path, has no CARR AI vault mounted,
and does not install Joe-primary scheduled jobs. Do not treat the Drive code
copies as a Dell dependency or ask Dell to validate the preconstruction audit.

Keep the mapped Drive copies for the separately governed cloud-only/file-mode
delivery path until its own retirement or repointing is evidenced. The compiled
fallback remains supported through 2026-08-21; Dell's migration alone is not a
retirement signal. Dell's open T69 closeout—reconciling two pre-PR6 local edits,
updating to current `origin/main`, rerunning the idempotent migration/config
checks, and confirming all 16 primary-only tasks are inactive and recoverably
quarantined—must finish before T69 closes. If Dell will do repo work before then,
perform that reconciliation first. It does not block Joe's Phase 0 audit or council.

## 15-minute technical preflight

Run in the audit-release worktree. Stop before the council if any check fails,
the release SHA is blank, or it differs from the recorded audit release.

1. `git rev-parse HEAD` → must equal the recorded `AUDIT_RELEASE_SHA`; record
   its relationship to `BASE_SHA`.
2. `git status --short` → record output; no audit-packet or product-contract
   change may be silently included in another lane's commit.
3. `git fetch origin` then `git rev-parse origin/main` → record comparison SHA;
   do not merge or rebase during the audit.
4. `node workspace/contracts/validate.mjs` → must pass.
5. `npm --prefix workspace test` → must pass.
6. `npm --prefix control-room test` → must pass.
7. Run each prototype locally using its documented package script. Capture the
   loopback URL, browser/version, and fixture-load result. Do not call a
   production write route.
8. Confirm the council inputs are the four roadmap files, both package manifests,
   both council registers, test output, and this completed evidence sheet.
9. Verify the 07:15 CT preflight watcher observed the closed A15 record and
   self-retired. If live record connectivity prevented retirement, record it as
   stale automation evidence and preserve the watcher for its next sanctioned
   retry; do not reinterpret it as a failed Dell migration.

Preflight evidence: date/time ___ · executor ___ · BASE_SHA ___ · AUDIT_RELEASE_SHA ___ · origin/main ___ ·
Workspace validation ___ · Workspace tests ___ · Control Room tests ___ ·
prototype URLs/results ___ · deviations ___.

## Human evidence — Workspace (five uncoached journeys)

Use the runnable Workspace prototype and record: participant, start/end time,
completion, comprehension in the participant's words, coaching required,
errors, heavy-session fallback, evidence link, and old-ritual cutover state.

| Journey | Exact steps and essential pass criteria |
|---|---|
| 1. Command Center to source | Open Command Center; identify what needs the signed-in partner; understand why it matters; follow the exact source deep link. Pass: under 15 seconds, one accountable owner, no named lead-outreach reminder, source and freshness visible. |
| 2. Park and restore Marsh | Find Marsh; park with controlled reason/context; verify preserved provenance, history, parties, and documents; verify exclusion from all active pressure; restore. Pass: version/conflict behavior visible, attributed events, read-back, no data loss. |
| 3. Weekly Deal Room call | Start from the single persistent control; confirm consent and sources; record/navigate; stop/process; review facts/actions/drafts independently; complete retention safely. Pass: parked records excluded, no guessed speaker/target, outside communication draft-only, all candidates dispositioned, purge gate truthful. |
| 4. Doc context and engineering request | Open contextual Doc; inspect removable context chip; ask grounded question; report problem/improvement; review diagnostics/optional screenshot; submit; follow lifecycle. Pass: exact request ID, no broad authority, executor/evidence visible, Completed impossible without fresh proof. |
| 5. Multi-stop Tour Mode | Prepare stops and locked appointment; review optimized route; download verified offline pack; start/navigate; capture Pencil note/photo; survive offline interruption; finish/review proposals. Pass: locked appointment preserved, map/list order matches, exact resume, acknowledged evidence not lost, no record write without confirmation, Goodnotes optional. |

The binding detail and automated assertions are in
`workspace/contracts/phase0-acceptance.v1.json` and
`workspace/contracts/phase0-traceability.v1.json`.

## Human evidence — Control Room (six uncoached flows)

Record the same fields as Workspace plus whether environment, freshness, source,
and authority were understood without coaching.

| Flow | Exact steps and essential pass criteria |
|---|---|
| 1. Collector failure is honest | From Overview, open Production health; inspect a simulated collector outage; follow the monitoring-gap action. Pass: health is Unknown, last verified time/source visible, no cached green remains. |
| 2. Service and environment provenance | From Service, compare Production/Rehearsal; inspect planned Staging; read accessible dependency blast radius. Pass: environment explicit, planned Staging is planned and Unknown, version/schema/configuration/source/freshness visible. |
| 3. Business problem to safe Work Request | From Work Request, open synthetic Workspace-origin request; inspect outcome/acceptance criteria; follow safe lifecycle return. Pass: one request ID, origin linked without client payload, one next action, engineering chat absent. |
| 4. Scoped plan and invalidated approval | From Plan and Approval, inspect session scope/plan revision; compare superseded/current hashes; inspect prior approval. Pass: old approval Invalidated, current plan has a new hash, no production action control. |
| 5. Release evidence and reconnect | From Deployment, open a synthetic rehearsal deployment; follow replayed server events; inspect verification and rollback readiness. Pass: browser is not operation owner, sanctioned actuator named, Complete requires version read-back and golden workflows. |
| 6. Incident investigation to audit chain | From Incident, open synthetic SEV-2 incident; separate facts/hypotheses; follow safe Workspace summary; trace audit chain. Pass: restricted diagnostics withheld, resolution requires monitoring proof, origin through verification reconstructable. |

The authoritative flow details are in
`control-room/contracts/phase0-acceptance.v1.json` and its fixture/test links.

## Accessibility and responsive evidence

This is human-only evidence at Phase 0. Record browser, OS, viewport, participant,
issue, severity, screenshot/reference, and retest result for each applicable item.

- Keyboard-only navigation, visible focus, logical order, escape/return behavior.
- Screen-reader labels, headings, state/status announcements, and error meaning.
- WCAG AA contrast, 200% zoom/reflow, 44px targets, and no color-only state.
- Reduced-motion behavior, empty/loading/error/Unknown states, and no silent failure.
- Desktop and phone-width responsive check; do not claim native device acceptance.

Any critical path failure is **must-pass before the affected Phase 1 slice**. It
does not authorize a silent workaround or a production release.

## Five baseline measures — do not invent values

Capture an observation window, method, source/evidence, result or `Unknown`,
bound product action, and like-for-like post-launch comparison. These are
baseline plans, not scorecards to fill by intuition.

| ID | Exact measure | Honest baseline method and action |
|---|---|---|
| `BASE-AI-TIME-001` | Joe's current weekly time using heavy AI sessions for routine system/business operation. | Time diary plus interview; Unknown until observed. Prioritize the highest-time routine for a visual workflow, Doc answer, or governed action card. Compare like-for-like weekly minutes at 30/90 days. |
| `BASE-AI-QUESTIONS-001` | Count and categories of routine questions requiring Claude/Codex explanation. | Classify observed questions and interview recall; do not invent counts. Add grounded visual explanation, source/freshness context, or Doc retrieval for repeated categories. Compare count/category mix at 30/90 days. |
| `BASE-VISUAL-ACTIONS-001` | Count and categories of recurring actions that cannot be completed visually. | Journey inventory and partner interview; do not infer frequency. Create a governed control or deliberately classify it as heavy engineering. Re-run the same inventory after launch. |
| `BASE-STATE-TIME-001` | Median time to determine business state and system health. | Timed uncoached tasks across representative days; no synthetic measured value. Refine Command Center summaries, source links, freshness, and safe status. Repeat at 30/90 days. |
| `BASE-HANDCARRY-001` | Number of hand-carried context transfers between sessions. | Count observed manual copy/relay events and interview examples; Unknown until measured. Add durable request context, task-to-task handoff, origin links, and returned evidence. Compare like-for-like transfers at 30/90 days. |

The time diary, observed question/copy-relay counts, and timed uncoached state
tasks require a representative observation window. They cannot honestly become
numeric in one council meeting. The meeting may set method, owner, window, and
an `Unknown` starting value; it must not manufacture a baseline.

The measure/action/cutover obligation is defined in
`workspace/contracts/phase0-acceptance.v1.json` and
`control-room/contracts/operating-objective.v1.json`.

## Council agenda and decision sheet

Order: confirm preflight → review must-pass/defer evidence → examine each fork →
test scope/timeline → make recommendation → Joe approves or returns revisions.
Council recommendation is advisory; Joe alone approves construction.

| Pending decision | Recommendation to evaluate | What it blocks if deferred | Disposition / evidence / Joe approval |
|---|---|---|---|
| `OPEN-URL-001` | Dedicated private Workspace origin and security audience. | Production auth design | ___ |
| `OPEN-AUTH-KV-001` | Workers Paid is settled; measure every OAuth/session write source, remove needless churn, prove headroom/alerts, observe cost, and rehearse recovery before treating authentication as production-ready. | Production-ready authentication | ___ |
| `OPEN-SURFACE-001` | Inventory/prove Deal Room writers and parity before consolidation. | Deal Room cutover | ___ |
| `OPEN-SURFACE-002` | Choose Lead Board path after lineage and dismissal audit. | Lead Board migration | ___ |
| `OPEN-LIFECYCLE-001` | Preserve settled states; refine reason words only from journey evidence. | Production enum/migration | ___ |
| `OPEN-FRONTDOOR-001`, `OPEN-MARKETING-001`, `OPEN-NOTIFY-001` | Decide only from repeated use and measured urgency. | Their named IA/module/push slice | ___ |
| `OPEN-RET-001` through `OPEN-RET-005`, `OPEN-LEGAL-001` | Keep deletion/recording/upload automation absent pending policy and named owner. | The affected retention/recording feature | ___ |
| `OPEN-DEVICE-FLOOR-001` | Derive floor from device inventory and required APIs. | Native build matrix | ___ |
| `CR-U01` | Ratify dedicated `ops.doctorcre.com` origin and cookie namespace unless evidence changes. | Ops auth/CSP/CORS/redirect design | ___ |
| `CR-U02` | No deletion automation before legal-hold owner and approved durations. | Purge automation | ___ |
| `CR-U03` | Capability-specific qualified-model tests; default upward if evidence is insufficient. | Trustworthy cheapest-qualified routing | ___ |
| `CR-U04` | Start with 10-minute re-auth and plan-specific expiry, then tune from rehearsal. | Production approval policy | ___ |
| `CR-U05` | Overview plus Service provenance first. | First Control Room vertical-slice choice | ___ |

Do not decide a fork merely because the table contains a recommendation. Every
row remains pending council and Joe until its disposition is recorded.

## Must-pass, explicit defer, and stop conditions

**Must-pass to authorize a Phase 1 foundation slice:** pinned preflight; relevant
contract/test suite; Joe approval after council; a selected first slice; every
material fork for that slice resolved or explicitly deferred; no broken authority,
tenant, audit, or production-write boundary.

**Explicitly defer without delaying unrelated foundation work:** native device
acceptance, Local Edge purchase/activation, broad recording, deletion automation,
cross-tenant runtime proof, workflow promotion, retention durations, and future
production mutation controls. Keep their feature flags and production routes
closed.

**Stop immediately:** source SHA mismatch; failed validation/test; evidence of a
production write/deploy/migration outside approval; stale or Unknown state shown
as healthy; personal-brain/sponsor/tenant boundary failure; unrecorded council
fork; critical accessibility failure on the proposed slice; or a scheduled job
modifying the same paths during review.

## Scheduled-job and worktree collision warning

Do not run nightly/export/deploy/migration/refresh jobs while tests, evidence
capture, or council edits touch the package. The hourly rules refresh runs at
17:00, 18:00, 19:00, and 20:00 CT; begin the first preflight after the 17:00 run
finishes and pause package-changing work across later top-of-hour windows. The
capture poll and partner-database ping run frequently but should not touch this
package; treat any unexpected path overlap as a stop condition. Before construction, inspect
launchd/cron/job status and active worktrees; identify the owner and changed paths
for every concurrent session. The normal checkout has unrelated untracked
`deliverables/` and `tools/doc-convo/assets/*` paths. They are not audit inputs,
must not be staged, and do not make this pinned worktree a release source.

## Post-approval Phase 1 launch and rollback

After Joe signs the council output, create a fresh isolated slice worktree from
the then-current integration baseline. Do not merge this packet branch as a
substitute for reconciling the Phase 0 package.

1. Record council dispositions and Joe approval through the sanctioned record
   layer; attach this packet's evidence IDs.
2. `git fetch origin` and record `origin/main`; create a named worktree from it.
3. Reconcile the approved Phase 0 package deliberately, run the relevant
   Workspace/Control Room tests, and stage exact paths only.
4. Establish canonical release truth, an immutable manifest, isolated
   environments, and required CI before integration-led product work.
5. Build the approved read-only Control Room slice (recommended: Overview plus
   Service provenance) behind its feature flag. Workspace shell work may proceed
   against frozen fixtures but may not outrun the foundation gates.
6. Verify contract, integration, accessibility, authority-denial, and live
   read-back evidence. Commit explicit paths, push, then deploy only under the
   approved release procedure.
7. If any stop condition fires: disable the new feature flag, halt rollout, keep
   the prior route/source of truth, preserve evidence, open a bounded work
   request, and rehearse rollback before retrying.

No platform rollout beyond the web foundation may bypass Website Completion →
Mac → iPhone → iPad ordering. No affected slice may bypass the AI-session
displacement test or an explicit old-ritual cutover/rollback path.
