# Rolling the CARR MCP Worker back

*Written 2026-08-19, immediately after the procedure below was executed and
verified end to end on staging. Every command here has been run; nothing in it
is proposed from reading the code.*

## Why this file exists at this path

`ops.release` rows written from 2026-08-19 onward carry
`rollback_plan_ref = runbooks/rollback-worker.md`. That reference is checked at
approval time and is recorded permanently against each production release, so
the path is load-bearing: a release approved against a plan that does not exist
is a release whose recovery story was never written down. The first version of
that reference pointed at `RECOVERY.md#worker`, which is about **losing the
Mac** — it tells you how to re-point a Worker at a recovered database, and says
nothing about backing out a bad Worker build. Different failure, different
procedure.

## What "rollback" means here, precisely

The Worker ships as an **immutable provider version**. A rollback does not
rebuild anything and does not touch the database: it moves 100% of traffic back
to a version that was already serving. Schema is deliberately identical across
environments, so a Worker rollback is safe exactly when the previous version's
code is compatible with the *current* schema — which is true for every release
that did not itself apply a migration.

**A release that applied a migration is NOT covered by this runbook.** Rolling
the Worker back under a migrated schema is a different and larger decision;
stop and treat it as one.

### After a Durable Object migration: forward fix only

A release whose `[[migrations]]` tag was applied (the release pipeline's upload
step prints `DO migration applied: tag=…` or `DO migration possibly applied:
tag=…`, the run record carries `do_migration`, and every deployment row of
that promotion names `do-migration=<tag>@<version>`) **cannot be rolled back
with this procedure.** Cloudflare blocks rollback to any Worker version from
before a Durable Object lifecycle change. Promoting the prior version, with
`--promote-version <previous-version-id>` or with raw `wrangler versions
deploy`, is refused by Cloudflare, and trying it wastes the incident's first
minutes.

Recovery is **forward fix only**: fix the code in a PR, keep it working with
the migrated Durable Object class, and let the next release ship it. Treat
"possibly applied" exactly like applied: if the tag could not be read back, a
post-migration version may be serving. To find out which side of the line the
Worker is on, read the applied tag the way the wrapper does
(`ops/worker-do-migration.py` names the endpoint and the field) and compare it
with the newest `[[migrations]]` tag in `mcp-server/wrangler.toml`. The durable
account of the move is the `worker-do-migration` row in `ops.settings_change`
(written the moment the deploy returned) and the pipeline's run record; the
wrapper's own `out/deploy-worker/do-migration-<sha>.json` lives in the release
worktree, which the pipeline deletes after the run. Neither `wrangler rollback`
nor a revert of the `[[migrations]]` entry undoes an applied tag.

### Staging carries the tag but no durable receipt says with which steps

Before Production moves, the wrapper applies the migration to staging. It then
writes a durable per-tag receipt beside the main checkout, at
`out/deploy-worker/do-migration-tags/carr-mcp-staging--<tag>.json`, or under
`$CARR_DO_MIGRATION_STATE_DIR` when that is set. A tag is applied only once,
so when a later run finds staging already carrying the tag, that receipt is the
only proof of which steps staging applied. Without it the release refuses
before staging or Production moves. Two refusals lead here:

- `staging applied <tag>, but its durable receipt could not be written to …`.
  Staging moved in this run, but the receipt write failed. Production was not
  touched. Every later run then hits the next refusal.
- `staging already carries <tag>, but no durable receipt proves it was applied
  with the steps wrangler.toml declares now`. A partial staging failure, a
  manual `wrangler deploy --env staging`, or a lost `out/` directory left
  staging on the tag with no receipt.

**Writing the receipt by hand is safe only when you can prove the steps
staging applied the tag with are the steps `wrangler.toml` declares now.** A
tag is applied only by the FIRST deploy that carries it; every later deploy
applies nothing. So the SHA staging serves now, from `/release`, proves
nothing. Here is how it fails:
1. Deploy S1 applies tag T with steps D1, then fails before writing its receipt.
2. A fix-forward edits T's steps to D2 but keeps the name T.
3. Deploy S2 lands on staging and applies nothing new, because T is already applied.
4. `/release` now shows S2, whose digest D2 equals the current one.
5. A receipt written from that would certify D2, but staging ran D1.

The receipt's `steps_digest` covers the whole `[[migrations]]` list. You need
**one** of these two proofs.

**(a) The history proof.** The tag's entry, and every `[[migrations]]` entry
before it, is identical in every commit on `main` since the commit that
introduced the tag. `tag-receipt write --history-repo` checks this itself and
records what it checked in the receipt. It walks main's first-parent history of
`mcp-server/wrangler.toml` from the commit that introduced the tag. It refuses
(exit 4, nothing written) in any of these cases:
- the list up to the tag changed in any later commit;
- the tag is not the newest entry;
- `--digest` is not the digest that history proves.

Always pass `--history-repo` for a hand-written receipt. To see the same
history yourself:

```sh
cd ~/carr-system && git fetch -q origin
git log --first-parent -p origin/main -- mcp-server/wrangler.toml   # read every [[migrations]] hunk since the tag appeared
./.venv/bin/python ops/worker-do-migration.py target --config mcp-server/wrangler.toml --env staging   # steps_digest
DIR="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")/out/deploy-worker/do-migration-tags"
./.venv/bin/python ops/worker-do-migration.py tag-receipt write --dir "$DIR" \
  --script carr-mcp-staging --tag <tag> --digest <steps_digest> \
  --sha <staging git_sha> --version-id <staging worker_version.id> --environment staging \
  --history-repo ~/carr-system --history-ref origin/main
./.venv/bin/python ops/worker-do-migration.py tag-receipt check --dir "$DIR" \
  --script carr-mcp-staging --tag <tag> --digest <steps_digest>   # must print "match": true
```

**(b) The applying-deploy proof,** used only when (a) refuses because the
steps were edited. Staging's deployment history must identify the deploy that
APPLIED the tag, not the one serving now: the earliest staging deployment
whose `GIT_SHA` declares the tag in `wrangler.toml`.

**`npx wrangler deployments list --env staging` shows only the 10 most recent
deployments (wrangler 4.137), and it prints no truncation marker.** After more
than ten staging deploys, "the earliest one that declares the tag" can
therefore be only the earliest VISIBLE one, and the real applying deploy may
have scrolled off the list. So (b) also needs the **boundary**, the deployment
IMMEDIATELY BEFORE the candidate in the list. The boundary proves nothing
earlier could have applied the tag. All of these must hold:

1. The boundary is visible in the list. If the candidate is the oldest entry
   shown, there is no boundary, and (b) FAILS.
2. The boundary and the candidate each serve a single version at 100%. With a
   split deployment, which code ran is ambiguous, and (b) fails.
3. Each one's `GIT_SHA` var is readable
   (`npx wrangler versions view <version-id> --env staging`), and each is a real
   commit on `main`.
4. `wrangler.toml` at the boundary's `GIT_SHA` does NOT declare the tag, and
   `wrangler.toml` at the candidate's `GIT_SHA` does. Check with
   `git show <sha>:mcp-server/wrangler.toml`.
5. The `steps_digest` at the candidate's `GIT_SHA` equals the current digest.
   Get it by running `target --config` on that file.

If all five hold, write the receipt without `--history-repo`. In the incident
record, record the boundary and the applying deployment: their ids, versions,
SHAs, and both digests. `--history-repo` would rightly refuse here, because the
history did change. **If the boundary is not visible, or any check fails, (b)
fails and the fix is a new tag.**

Then let the release pipeline retry.

**It is NOT safe when** any of the following holds:
- neither (a) nor (b) holds, for example when the tag's entry was edited and
  the deploy that applied it cannot be identified;
- the digest at the applying commit differs from the current one;
- staging was deployed from an uncommitted or unknown tree, so no commit names
  what was applied;
- you cannot read staging's deployment history, or the applying version has no
  `GIT_SHA`;
- the boundary deployment (the one immediately before the applying deploy)
  is not among the 10 deployments `wrangler deployments list` shows, or it
  already declares the tag.

In those cases, never write a receipt to get past the refusal: it would certify
steps staging never ran. Leave the applied tag's entry exactly as it was
applied, and put the corrected steps under a **new** `[[migrations]]` tag.
Every environment then applies the new tag once, with steps that are known.

## The procedure

### 1. Find what is serving now, and what preceded it

```sh
cd ~/carr-system/mcp-server
npx wrangler deployments list            # production
npx wrangler deployments list --env staging
```

Record the current version id and the one before it. Confirm the current
identity independently rather than trusting the list:

```sh
curl -s https://api.doctorcre.com/release
```

`worker_version.id`, `git_sha.value` and `env.value` are the authoritative
triple. `env` matters because `git_sha` and schema are identical across
environments by design — without it you cannot tell which deployment answered.

### 2. Promote the previous version

Production goes through the sanctioned wrapper, never raw wrangler, because the
wrapper re-checks the approved release and records the deployment:

```sh
cd ~/carr-system
./bin/deploy-worker.sh --promote-version <previous-version-id> \
  --performance-budget-ref ops/performance-budget-gate.py \
  --performance-budget-ms 1000 \
  --recovery-strategy rollback \
  --rollback-plan-ref runbooks/rollback-worker.md
```

### 3. Verify from the Worker, not from the command's exit code

```sh
curl -s https://api.doctorcre.com/release
```

`worker_version.id` must equal the version you promoted, and `verb_count` must
match what that build carried. **A deploy returning success and a registry that
answers are two different claims.**

### 4. Typed recovery is the only verb-shrink authority

A rollback usually removes verbs the newer build added, so the preflight
refuses:

```
REFUSED: this deploy would REMOVE 1 verb(s) from staging.
  last deployed: 140
  about to ship: 139
```

That guard exists because production silently went from 75 verbs to 66 in the
middle of a working session. There is no `--allow-shrink` override. For any
exact typed recovery step (`current_before`, `prior`, `current_after`, or the
isolated `restore_only` repair), the wrapper first calls that step's matching
database writer. The writer must durably prepare the exact candidate/prior,
SHA, service, recovery attempt, and correlation, then return the deterministic
provider tag that the later prepare must replay idempotently. Standalone/source,
manual-flag, and mismatched-prior deploys remain refused.

## The rehearsal, and why it is required before approval

A production release cannot be approved without a successful
`recovery.rehearsal.worker` receipt bound to it. That is not paperwork: it
means somebody proved the rollback path works *before* needing it.

Rehearse on staging with the typed three-step recovery chain. Keep the
Production candidate in `candidate` state while the rehearsal runs; these are
not three independent staging releases. Use one recovery-attempt UUID for the
whole chain, one distinct staging-receipt UUID per step, the candidate's exact
release key, and a completed/read-back Production release as the prior:
Run `current_before`, `prior`, and `current_after` in that order and finish all
three within one hour. Production approval must then be recorded within 24
hours of the completed bundle.

```sh
RECOVERY_ATTEMPT_ID=<one-new-uuid>

# From a clean worktree at <current-sha>:
./bin/deploy-worker.sh --env staging --release-sha <current-sha> \
  --release-key <production-candidate-key> \
  --recovery-attempt-id "$RECOVERY_ATTEMPT_ID" \
  --recovery-prior-release-key <completed-production-prior-key> \
  --recovery-step current_before \
  --staging-receipt-idempotency-key <current-before-uuid>

# From a clean worktree at <prior-sha>. The wrapper can permit a lower count
# only after it has prepared this exact typed step against the completed prior;
# the same rule applies to current_before, current_after, and restore_only:
./bin/deploy-worker.sh --env staging --release-sha <prior-sha> \
  --release-key <production-candidate-key> \
  --recovery-attempt-id "$RECOVERY_ATTEMPT_ID" \
  --recovery-prior-release-key <completed-production-prior-key> \
  --recovery-step prior \
  --staging-receipt-idempotency-key <prior-uuid>

# Back in the clean <current-sha> worktree:
./bin/deploy-worker.sh --env staging --release-sha <current-sha> \
  --release-key <production-candidate-key> \
  --recovery-attempt-id "$RECOVERY_ATTEMPT_ID" \
  --recovery-prior-release-key <completed-production-prior-key> \
  --recovery-step current_after \
  --staging-receipt-idempotency-key <current-after-uuid>
```

The wrapper prepares, claims, reads back, and records each typed staging
receipt. The final `current_after` step creates the recovery bundle and its
`recovery.rehearsal.worker` run atomically. Do not add a manual `ops-record run`
receipt and do not create or approve separate staging releases for these three
steps; either would describe a different, unbound procedure.

### If a staging leg fails after it may have changed staging

Do not repeat `current_after`. The recovery controller invokes the wrapper with
the internal `restore_only` safety step, bound to the same candidate, prior,
rollback plan, migration set, and recovery attempt. Its result is audited as
`succeeded`, `failed`, or `unknown`, but it is structurally outside the three
receipt tables and can never complete, repair, promote, or approve a recovery
bundle. A repaired partial run remains ineligible; start a new three-step
rehearsal after diagnosing the original failure.

## Verified execution, 2026-08-19

Run against staging while promoting the `export-email-domains` verb:

| Step | Result, read from `/release` |
|---|---|
| Starting state | `7c7e1bd1`, 140 verbs |
| Rolled back to `8e761a0c` | `8e761a0c`, **139 verbs** |
| Restored forward | `7c7e1bd1`, **140 verbs** |

The typed recovery path allowed the expected temporary verb reduction only
after the matching database writer bound each exact step to the candidate and
completed prior. Both directions were confirmed from the Worker's own
`/release` endpoint rather than from deploy output.

## Two things that will bite you

**The plan hash moves with its inputs.** An approval binds a specific plan
hash, and the promotion recomputes that hash from the git SHA plus the four
approval inputs you pass. Pass different inputs than the ones the approved
manifest was built with and it refuses with `THE PLAN MOVED SINCE APPROVAL`,
which is correct and means: rebuild the manifest with the exact inputs you
intend to promote with, then approve *that* hash.

**Approval needs the full evidence set together.** `an_approved_release_carries_its_evidence`
requires `test_evidence_ref`, `security_evidence_ref` **and**
`maker_verification_ref` — all three, at the moment of approval, not later.
Supply them on the candidate.
