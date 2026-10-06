# Joe's one-sitting batch queue — WR-000019 Obedience & Autonomy, S13 staging

Everything below is staged, read-only against the record layer except two
allowed writes noted in the S13 task (the doctrine section and the
outcome-feedback proposal). Nothing in this document performs any of the
acts it describes. Each item names the finished artifact, the exact call or
tooling, what it does in plain words, the evidence behind it, and what
happens if Joe declines it.

The 168-hour clean-week clock for item (e) starts only when a fresh shadow
epoch is opened (see item (e) prerequisite) — it has not started yet as of
this staging pass (2026-08-27). Items (a)-(d) and (f) do not depend on the
clean week and can be worked in any order Joe prefers; only (e) is gated on
the week completing clean.

---

## (a) Triage batch acceptance — WR-000019 slice S7

**What it does.** Formally accepts the 219-rule triage (20 core / 62 gate /
126 jit / 11 gone) already classified and merged in `ops/config/rule-triage.v1.json`,
and applies the 11 GONE-rule merges (each folds into a named survivor rule).

**Artifact.** The dry-run plan is reproducible on demand —
`./.venv/bin/python3 ops/rule-triage-apply.py --emit-receipts-plan` — and was
re-run during this staging pass. It printed exactly 11 `retire-rule` calls
(one per GONE rule, each carrying its `superseded_by` survivor) and 3
OPTIONAL `amend-rule` wording flags (not required):

- `4f7c348f` (the session-boot recitation rule) — flags that its wording
  still says "both rule FILES," which is stale post the 2026-08-19 md-cutoff.
  **This overlaps with the core-compression proposal for the same rule in
  item (b) below** — Joe should apply the compression and this wording fix
  together in one `amend-rule` call rather than two, so the rule is not
  amended twice in the same sitting.
- `1fddcffb` (core routing test) — flags a stale enforcement pointer
  (`00_Context/sweep-sop.md`) that should now name `ops/config/rule-triage.v1.json`.
- `70e372f0` (Dell experience calibration) — flagged for a move from the
  shared rule set into Dell's own personal set.

**Exact call sequence for the retire batch** (one per GONE rule; each needs a
freshly minted `idempotency_key` at the moment Joe runs it, never reused from
the dry-run plan):
```
retire-rule(idempotency_key=<fresh-uuid>, rule_id="006a7eaa", reason="...", superseded_by="aa411351")
retire-rule(idempotency_key=<fresh-uuid>, rule_id="14e0408b", reason="...", superseded_by="aa411351")
retire-rule(idempotency_key=<fresh-uuid>, rule_id="3fa422b7", reason="...", superseded_by="aa411351")
retire-rule(idempotency_key=<fresh-uuid>, rule_id="581cb3fe", reason="...", superseded_by="aa411351")
retire-rule(idempotency_key=<fresh-uuid>, rule_id="634a2d94", reason="...", superseded_by="aa411351")
retire-rule(idempotency_key=<fresh-uuid>, rule_id="75c2e4c9", reason="...", superseded_by="aa411351")
retire-rule(idempotency_key=<fresh-uuid>, rule_id="8117b414", reason="...", superseded_by="aa411351")
retire-rule(idempotency_key=<fresh-uuid>, rule_id="937252fb", reason="...", superseded_by="97326357")
retire-rule(idempotency_key=<fresh-uuid>, rule_id="d367188d", reason="...", superseded_by="0f38532e")
retire-rule(idempotency_key=<fresh-uuid>, rule_id="dff58fef", reason="...", superseded_by="aa411351")
retire-rule(idempotency_key=<fresh-uuid>, rule_id="e065aa82", reason="...", superseded_by="aa411351")
```
(Reasons are the full text `rule-triage-apply.py` prints per rule; re-run the
script to get the exact, untruncated strings rather than retyping from this
summary.) Optionally follow with:
```
log-decision(decision="WR-000019 slice S7 rule triage batch-accepted: 20 core, 62 gate, 126 jit, 11 retired-by-merge.", rationale="...")
```

**Evidence.** `ops/config/rule-triage.v1.json` (219 rules, counts verified:
core=20, gate=62, jit=126, gone=11), `ops/rule-triage-report.py`'s rendered
review, and `ops/rule-triage-selftest.py` (22/22 passing at this staging
pass, re-run 2026-08-27).

**If Joe declines.** The 11 GONE rules stay active and recited forever
alongside their survivors — no harm, just permanent duplication and a wider
gist recitation than necessary. Nothing else in the program depends on this
landing; it is the cheapest, lowest-risk item in the queue.

---

## (b) Core compression amendments

**What it does.** Applies compressed `statement` text to some or all of the
20 CORE rules, cutting narrative/provenance detail while preserving every
binding clause, so a future full-text core recitation (post-S13-flip) costs
materially fewer tokens.

**Artifact.** `ops/config/core-compression-proposals.v1.json` (written this
pass) — 20 proposals, each `{rule_id, current_chars, proposed_statement,
proposed_chars, clauses_preserved_note}`. Measured totals: current core
payload 41,505 chars (~11,859 tokens); proposed 20,121 chars (~5,749 tokens),
a 51.5% reduction. (A separate committed measurement,
`ops/config/boot-budget-core-fixture.v1.json`, puts the full-text core payload
at 48,091 bytes / ~13,740 tokens by a slightly different byte-counting method
— both numbers describe the same underlying fact: the 20 core rules in full
are larger than the entire current boot budget.) 5 of 20 rules already sit at
or near the 400-700 char target and were left essentially unchanged
(`0f38532e`, `6a4e6283`, `1fddcffb` were already short; `4f7c348f` and
`2dbb0ad8` compressed into the band). The other 15 carry more binding clauses
than the band can hold without cutting substance — clause-preservation was
weighted over the char target for those, per this task's own instruction
("keep every binding clause" before "target 400-700 chars"). Read
`target_char_band_note` in the proposals file for the full accounting.

**Exact per-rule call the tooling would make** (Joe reviews each
`proposed_statement` first — these are proposals, not pre-approved text):
```
amend-rule(idempotency_key=<fresh-uuid>, rule_id="<rule_id>", base_version=<fresh read>,
           statement="<proposed_statement from the file>",
           reason="Core compression pass, WR-000019 slice S13 staging: narrative/provenance cut, binding clauses preserved.")
```
`human_quote` is never touched by this batch — `amend-rule` only changes
`statement` here. Recommended: batch all 20 (or however many Joe accepts)
into one sitting rather than one-by-one, since each needs its own fresh
`base_version` from a read taken immediately before the call.

**Evidence.** The full current text of all 20 rules was fetched live via
`standing-context` with `rule_ids` during this pass (two batched calls,
2026-08-27); the proposals file was built and character-counted
programmatically from that fetch, not estimated.

**If Joe declines.** No harm today — the current shadow-mode boot budget
passes (9,948.6 of 10,000 tokens, see item (e)'s prerequisite discussion and
AC-S11 in the outcome-feedback report). But the margin is only 51.4 tokens,
and `ops/config/boot-budget-core-fixture.v1.json` already documents that the
full-text core payload (48,091 bytes, ~13,740 tokens) is the SEPARATE, larger
number the S13 flip would introduce once `standing-context`'s enforced-mode
`core_preview` starts actually being used — that is not covered by today's
budget check at all. Declining compression means that whenever the enforced
core-preview payload goes live, the boot budget will very likely blow through
its 10,000-token ceiling and there will be no compressed text ready to fix it
with.

---

## (c) Guidance Registry pair: decide-guidance-import-batch + activate-guidance-registry

**What it does.** Two sequential authority-only decisions that bring the
typed Guidance Registry (219-entry migration manifest, built across S9's
guidance-cluster work) from staged to active: first deciding the staged
import batch, then activating the registry itself.

**Writer-credential staging step that precedes both** (mandatory, and not
itself one of the two authority verbs): `ops/guidance-registry-import.py
--apply` must run first, using a separately authenticated
`CARR_DB_WRITER_URL` connection (never the read-only `DATABASE_URL`) plus an
explicit `--mapping-plan <reviewed JSON>` and one or more
`--constitution-guidance-id` values. That run computes and stages the
canonical activation-manifest digest via `ops.stage_guidance_import_batch`
and `ops.apply_guidance_import_batch`. **No reviewed `--mapping-plan` file
exists in the repository today** — one must be authored and reviewed before
this step can run at all; this staging pass found no such file under any
`*mapping-plan*` name.

**Exact calls, after the staging step above has produced a real `batch_id`
and confirmed `manifest_digest`:**
```
decide-guidance-import-batch(idempotency_key=<fresh-uuid>, batch_id="<from staging step>",
                              manifest_digest="<from staging step>", reason="...")
activate-guidance-registry(idempotency_key=<fresh-uuid>, registry_id="<staged registry id>",
                            manifest_digest="<same digest>", reason="...")
```

**On the digest handed to this task** (the orchestrator's task text names
"219 entries, digest
f68b4ee968264febed3842df5821d4375d2332caccb4259236ee6b277a31ed24" from S9's
report). This staging session searched the tracked repo tree for that exact
digest and did not find it in any committed artifact — it appears to be a
number from a session report or PR narrative rather than a checked-in file.
Because this session lacks `CARR_DB_WRITER_URL` and the mapping-plan file to
recompute it, **the digest above could not be independently re-verified
here.** Whoever runs the staging step in (c) should treat the dry-run output
of `ops/guidance-registry-import.py` (no `--apply`) as the authoritative
digest at the moment of decision, not this cited number, since the manifest
is a function of the live active-rule inventory and could have shifted since
S9.

**Evidence.** `mcp-server/src/tools.js` verb definitions (both
`authorityOnly: true`, Joe-only); `ops/guidance-registry-import.py`'s
`stage_and_apply` (the only writer sequence in that CLI — no
decision/activation call exists inside it, confirming the two MCP verbs are
the only path to activation); `audits/guidance-migration-manifest.v1.tsv`
(94 rows in the checked-in source TSV — a smaller, earlier-stage artifact
than the "219 entries" cited, consistent with the TSV being raw source rows
rather than the compiled activation manifest's entry count).

**If Joe declines.** The Guidance Registry stays inactive. This directly
keeps AC-S9 partially unmet (see the outcome-feedback report) — the JIT
rebuild's registry half of that criterion has no other path to completion.

---

## (d) GitHub ruleset decision: required review on `hooks/` and `ops/config/`

**What it does.** Decides whether a PR touching `hooks/` or `ops/config/`
should be BLOCKED from merging without a human review, versus today's
advisory-only posture.

**Current state, verified this pass.** `.github/CODEOWNERS` already names
`@jbookout` as owner of `/hooks/` and `/ops/config/` (added S2, PR #717-adjacent
work). The repository's one active ruleset
(`main: CI must be green`, id `20824501`) was read live via
`gh api repos/jbookout/carr-system/rulesets/20824501` this pass and contains
exactly three rules: `deletion`, `non_fast_forward`, and
`required_status_checks` (`ops/ci.sh --strict`). **There is no
`pull_request` rule in it at all** — so CODEOWNERS review is requested on a
matching PR but never required before merge. This confirms the task's own
framing: CODEOWNERS is advisory today.

**Exact call to make it required** (adds a `pull_request` rule to the
existing ruleset, leaning on the CODEOWNERS file already in place so the
requirement only bites on PRs that touch `hooks/` or `ops/config/`):
```
gh api --method PUT repos/jbookout/carr-system/rulesets/20824501 \
  --input - <<'JSON'
{
  "name": "main: CI must be green",
  "target": "branch",
  "enforcement": "active",
  "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
  "rules": [
    {"type": "deletion"},
    {"type": "non_fast_forward"},
    {"type": "required_status_checks", "parameters": {"strict_required_status_checks_policy": false, "do_not_enforce_on_create": false, "required_status_checks": [{"context": "ops/ci.sh --strict"}]}},
    {"type": "pull_request", "parameters": {"required_approving_review_count": 1, "require_code_owner_review": true, "dismiss_stale_reviews_on_push": true, "require_last_push_approval": false, "required_review_thread_resolution": false}}
  ]
}
JSON
```
Equivalent UI path: repo Settings -> Rules -> Rulesets -> "main: CI must be
green" -> add rule -> "Require a pull request before merging" -> check
"Require review from Code Owners", approving review count 1.

**Either way, the decision itself should be recorded** (this is the kind of
internal-but-consequential config choice rule `aa411351` puts in the
session's own hands to decide-and-report rather than escalate — but since it
changes merge mechanics for the whole repo and only Joe can call `gh api`
with owner authority here, it is queued for his sitting rather than acted on
unilaterally in this staging pass, consistent with this task's hard scope
boundary: no gates/hooks/config changes).

**If Joe declines (keeps it advisory).** A PR touching `hooks/` or
`ops/config/` can still merge on green CI alone with no human review, same
as every other PR today. Nothing breaks; the risk is silent to a
mis-reviewed gate/config change landing without Joe (or a delegate) actually
looking at it.

---

## (e) THE FLIP itself: shadow -> enforced

**What it does.** The one-row policy update that turns scoped rule delivery
from shadow (observe-only) to enforced (actually gates pack loading),
executed atomically by `ops.set_rule_delivery_mode(mode, changed_by, reason,
digest)` inside a single transaction.

**Prerequisite that has NOT been satisfied as of this staging pass — the
eligibility check.** `./.venv/bin/python3 ops/rule-delivery-shadow-eligibility.py --json`,
re-run 2026-08-27 as part of this staging pass, reports **NOT ELIGIBLE**:
window is only 22.2 of the required 168 hours old, 123 open/unresolved
findings sit under the current epoch, and — the structural finding this
staging pass surfaced — 251 of 255 reasons are `map_digest`/`source_digest`
mismatches between the epoch (opened 2026-08-26T09:42:27Z) and every
observation recorded after slices S9-S12 landed and changed
`ops/config/rule-enforcement-map.json` and the files in
`WINDOW_SOURCE_PATHS`. **The epoch must be restarted** with:
```
CARR_DB_JOBS_URL=<carr_jobs role DSN> ops/rule-delivery-shadow-ledger.py start-epoch \
  --reason "Restart clean-week clock after S9-S12 source/map changes (WR-000019 slice S13 staging)" \
  --owner "<actor>" --remedy-ref "git:<HEAD sha>:..." --rollback-ref "policy:shadow:recurrence-invalidates-eligibility:git=<HEAD sha>"
```
This staging session confirmed (attempted the exact command, saw a clean
`REFUSED — jobs-credential-required` before any ledger write, and verified
the ledger's line count was unchanged before and after) that it lacks
`CARR_DB_JOBS_URL`, so it could not run this itself even though the
operation is local and append-only. **This is the first act of Joe's
sitting for item (e)**, since the 168-hour clock cannot start until it runs.
Once it does, the 168-hour clean-week wait itself is calendar time, not a
session action — Joe should expect roughly a week of elapsed time between
running this and being eligible to flip.

**Exact flip sequence, after the eligibility check reports ELIGIBLE:**
```
DATABASE_URL=<preview/read DSN> ops/rule-delivery-cutover.py --mode enforced \
  --changed-by "<actor>" --reason "WR-000019 slice S13: promote scoped rule delivery to enforced" \
  --apply
```
This single invocation re-checks, inside the same locked transaction: the
migration 0317 target count is exactly 9; the 38-item human curation
approval batch is exactly (38 approved, 38 human-reviewed); the eligibility
check passes against the identity read at write-time (re-read under `FOR
UPDATE`, so a change between preflight and commit aborts the whole
transition); and Claude/Codex hook config parity live-matches
`ops/config/hooks.json`/`codex-hooks.json` against the actually-installed
`~/.claude/settings.json`/`~/.codex/hooks.json`. Only if every one of those
holds does it call `ops.set_rule_delivery_mode(...)` and commit.

**Evidence.** `ops/rule-delivery-shadow-eligibility.py`'s live JSON output
(re-run this pass); `ops/rule-delivery-shadow-ledger.py`'s `start-epoch`
subcommand (read, and its credential refusal confirmed live without side
effects); `ops/rule-delivery-cutover.py`'s full gate sequence (read
end-to-end this pass).

**If Joe declines (never flips).** Rule delivery stays in shadow mode
forever — every session keeps getting the full gist recitation regardless of
task relevance, the JIT rail keeps only measuring drift rather than
preventing it, and AC-S13 stays permanently unmet. Nothing regresses; the
program simply stops short of its own stated goal.

---

## (f) Production classification-parity export

**What it does.** Runs the read/export side of
`bin/sync-rule-admission-prod.sh` (`--export` flag) to write
`ops/config/rule-admission-export.v1.json`, the artifact S10's
"classification parity" acceptance criterion checks against production.

**Exact call:**
```
CARR_DB_JOBS_URL=<or the role this script's rails require> ./bin/sync-rule-admission-prod.sh --export
```
This needs live production database credentials this staging session does
not have and should not be given (staging is read-only-by-design on the
record layer). The script's own header documents it as read-only in its
default and `--export` forms — only `--apply` performs a write, and
`--export` never does.

**Evidence.** `bin/sync-rule-admission-prod.sh` header (read this pass):
confirms the three usage forms (bare = read-only status, `--apply` =
backfill + re-audit, `--export` = write the local JSON snapshot only) and the
four ordered rails (uncommitted map refuses; `--apply` always re-verifies
against production's own audit exit code; unmapped active rules refuse
rather than invent a contract; every invocation logs outcome to
`out/sync-rule-admission-prod.log`).

**If Joe declines.** The classification-parity check simply never runs
against live production data in this cycle; the reconciled DB/file
classification map (AC-S10) stays verified only against whatever the last
run captured, not today's production state.

---

## Sequencing note

(a), (b), and (d) are independent and can run in any order or be skipped
individually with no cross-effects. (c) has its own internal prerequisite
(author + review a mapping-plan file, then run the writer-credential staging
step) that gates both of its verb calls. (e) has the longest lead time by
far — its first step (epoch restart) should be run as early in the sitting
as possible, specifically before (f), so the calendar week starts counting
while Joe works through the rest of the list. (f) is independent of
everything else and can run whenever production credentials are at hand.

## STATUS UPDATE — 2026-08-27, end of ship session

- (a) Triage batch acceptance: **EXECUTED** — 11 retirements receipted; recite counts 163 shared / 31 personal; 208 in scope.
- (b) Core compressions: **EXECUTED** — 17 amended with receipts + 3 already at target; live core payload 20,121 chars (~5,748 tokens), 51.5% cut.
- (c) Guidance Registry: **STAGED AND APPLIED** — batch id b46f9c26-2291-46e5-9ce2-7e843680150f, manifest digest c22151d2ae2a4ecc63d1493e14afbc34f089de2202277dc1b866960d8bf863fd, 208 entries. YOUR TWO CALLS, in order, all required:
  1. decide-guidance-import-batch {"batch_id":"b46f9c26-2291-46e5-9ce2-7e843680150f","manifest_digest":"c22151d2ae2a4ecc63d1493e14afbc34f089de2202277dc1b866960d8bf863fd","reason":"...","idempotency_key":"<fresh uuid>"}
  2. activate-guidance-registry {"registry_id":"67aa96b6-3cf8-45ce-b73c-6965d36a664c","manifest_digest":"c22151d2ae2a4ecc63d1493e14afbc34f089de2202277dc1b866960d8bf863fd","reason":"...","idempotency_key":"<fresh uuid>"} — call after 1 succeeds.
- (d) GitHub ruleset review requirement: **DECLINED** — see the close-out below.
- (e) The flip: clock started 2026-08-27T10:56Z, but **waiting alone will not deliver an eligibility verdict** — see the correction below.
- (f) Prod classification export: **EXECUTED** — parity 208/208 green, committed as PR 748.
- NEW: rotate the neondb_owner database password when convenient — bin/schema-snapshot.sh --check leaked the DSN with password into a session transcript (defect filed, class credential-leaked-by-tool-error-path).

## CORRECTION TO ITEM (e) — 2026-08-27 14:45Z, verified against live state

The clean-week clock is running, but on its current trajectory it cannot
finish, so nothing useful happens by waiting until 2026-09-03.

Eligibility requires zero unresolved findings. Measured 3.7 hours into the
new epoch: **all 44 observations recorded at least one missing pack — not one
clean observation** — accruing findings at about 11.8 per hour, which projects
to roughly 2,000 open findings by the expected eligibility date. The check
will report NOT ELIGIBLE on 2026-09-03 and on every date after it, for a
condition that was already measurable on day one.

Two causes are evidenced and both may contribute:

1. **Sessions do not declare packs.** 37 of the 44 observations show
   `loaded=[]`, and the 7 that declared some packs still missed others.
   Shadow mode by design does not force declaration, so an ordinary session
   generates a finding roughly every time it works.
2. **The trigger detector over-fires.** One observation named 11 needed
   packs, including `client-deal` and `vendor-intros`, for a session that
   touched neither — the keyword sets in `pack_index` are broad enough that
   reading a document that says "post" or "tour" pulls in a pack.

Filed as **WR-000027** (captured, awaiting triage) with acceptance criteria
that separate the two causes by measurement before anything is changed, and
as a defect in the new class
`gate-clock-started-without-measuring-whether-it-can-finish`.

**Worth keeping separate from the bookkeeping:** zero clean observations in
3.7 hours is the shadow mechanism working exactly as designed and reporting
that the scoped selector is not ready to enforce today. That verdict should
survive whatever is decided about the finding counter. The flip stays yours
either way.

## QUEUE CLOSED — 2026-08-27 15:05Z, Joe answered all four open items

Nothing in this document is waiting on anyone. It is kept as the record of
what was staged and what was decided, not as a live to-do list.

- **Program sign-off: ACCEPTED.** Joe: "accepted". Recorded against the
  corrected proposal (OUTCOME-5af1efab3352-v3), which supersedes the earlier
  one that wrongly blamed the calendar for the flip being stuck.
- **Guidance Registry: LIVE.** Joe: "lets do it". Both calls made and then
  re-read from production to confirm rather than trusted: the import batch
  decision is `active` as of 15:02:01Z and the registry itself is `active`
  as of 15:02:06Z under digest c22151d2. This means AC-S9's activation half
  is now met — the accepted sign-off above still records it as not met,
  because it was true when he accepted it. The next outcome proposal, filed
  once the clean-week gate is fixed, carries both corrections at once rather
  than charging Joe a second acceptance for one line.
- **Required human review on pull requests: DECLINED, permanently and in
  general.** Joe: "no blocking for human review. i never wanted this for
  anything". Branch protection keeps its three existing rules and gains no
  pull-request review requirement; code-owner review stays advisory. The
  staged `gh api` call in item (d) above is retained as a record of what was
  offered and should not be run. Ruling logged as decision
  8daefaba-8f55-4186-872d-36e2ee37edbd.
- **Database owner password rotation: DECLINED.** Joe: "I dont care about the
  password. just leave it". Closed by ruling so it stops being re-raised;
  logged as decision fc1d48ab-31b6-406b-958a-76da15cca2c3. The separate fix —
  stopping the snapshot tool printing credentials on its error path — is
  untouched by that ruling and still worth doing, since it prevents the next
  leak rather than cleaning up this one.

The only thing left in the whole program is the clean-week gate (WR-000027).
The flip waits on that, not on a date.
