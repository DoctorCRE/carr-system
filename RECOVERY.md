# RECOVERY — what to do when the Mac is gone

*Written 2026-08-14, the day the key recovery test first passed. Read this on
GitHub from any machine; that is the whole point of where it lives.*

## Why this is a file and not a record

Every other piece of durable knowledge in this system lives in the record layer,
by rule. This one cannot. **A recovery runbook stored in the database you are
trying to recover is unreachable exactly when it is needed.** So it lives in the
repository, which is on GitHub, which you can read from a borrowed laptop with
nothing but your GitHub login.

Keep it that way. If a future session moves this into the store, the system
loses the ability to tell you how to rebuild itself.

---

## What actually survives the Mac

Nothing important is only on the Mac. Confirmed 2026-08-14:

| thing | where it lives | reachable with |
|---|---|---|
| All code, config, migrations, this file | GitHub, `jbookout/carr-system` | your GitHub login |
| Every record — clients, deals, doctrine, rules | Neon Postgres, hosted | your Neon login |
| The API, Deal Room, OAuth | Cloudflare Worker, hosted | your Cloudflare login |
| Encrypted nightly dumps | Cloudflare R2 bucket `carr-documents` | your Cloudflare login |
| Encrypted nightly dumps, second path | GitHub Actions artifacts, 90-day retention | your GitHub login |
| The key that opens those dumps | **paper, off the machine** | you |

### If the R2 archive holds a bad object

`lib/r2_archive.py` can only ever ADD. When a truncated dump is promoted and
uploaded, the archive ends up with two objects both claiming to be that day's
backup and no way to remove either — which is exactly what happened on
2026-08-07. The remedy is:

    bin/r2-forget.py --prefix backups/<sha256-prefix>/          # list only
    bin/r2-forget.py --prefix backups/<sha256-prefix>/ --yes    # delete

Selection is by key prefix, never a glob and never "the newest N", so the blast
radius is visible in the command itself. Nothing is deleted without `--yes`; the
listing run IS the confirmation step. Backup keys are content-addressed, so a
single corrupt upload is named precisely without touching the good object beside
it. It removes the ledger row with the object, so the two cannot disagree.

Pointer added 2026-08-22 (open loop 503, item 4): the tool had zero callers and
was named in no document, so the only remedy for this failure was undiscoverable
on the day it would be needed.

The two dump paths are independently produced rather than copied — different
checksums, verified. They share no failure point.

**The Mac holds no unique data.** It holds the Drive vault mount, the
microphone, the calendar, and the jobs that touch those. Losing it costs those
capabilities and no records.

---

## If the Mac is gone but everything else is fine

This is the likely case, and the record layer is untouched. You are rebuilding an
edge node, not recovering data.

1. **New machine, sign in to GitHub, clone the repo.**
2. **Install the tools**: `age`, `psql`, `neonctl`, `gh`, Python 3, and the
   virtualenv the repo expects.
3. **Restore the credentials.** They are NOT in the repo, by design.
   `secrets-inventory.md` at the repo root lists every one, where it lives, and
   who can reissue it. Work down that table. Anything you cannot reissue
   yourself is named there with the path to get it.
4. **Reinstall the scheduled jobs**: `ops/config-as-code.py install --apply`
   writes the launchd agents from the repo copies.
5. **Verify**: `./run.sh health`, `tools/scheduler-truth.py`, and
   `tools/ops-record.py health`. The last one should stop reading `unknown` for
   the local jobs as each next fires.

Nothing above needs a backup. The records were never on the Mac.

---

## If the record layer itself is lost

This is the case the backups exist for.

**Neon's own point-in-time restore covers the first six hours and is the first
thing to try** — it is faster and loses less. Past six hours, these dumps are the
only history that exists. There is no vendor safety net behind them.

1. **Get a dump.** Either path:
   - GitHub: the artifact on any successful **Nightly backup (cloud)** run.
     Browser download works; no CLI needed.
   - Cloudflare: the R2 bucket `carr-documents`.
2. **Get the key.** From the paper copy. Never from a machine — that is the
   whole reason it is on paper.
3. **Restore it.** `./run.sh restore-rehearse` is the sanctioned path and does
   the whole thing: creates a throwaway Neon branch, restores into it, compares
   row counts table by table against production, and tears the branch down. Use
   `--preflight` first — it checks every prerequisite and creates nothing.
   For a real recovery rather than a rehearsal you want the restored data kept,
   so use `--keep-branch` and promote that branch.

   Do not hand-roll a decrypt. A guard blocks it, and the reason is that a raw
   decrypt leaves production data in cleartext on disk; the sanctioned path
   shreds the plaintext on every exit including interrupt.
4. **Re-point the Worker** at the recovered database and redeploy:
   `bin/deploy-worker.sh`. Apply any migrations the dump predates with
   `bin/migrate-prod.sh`.

---

## What the decrypted file actually is

A plain PostgreSQL dump — SQL text. `CREATE TABLE`, `COPY`, thousands of rows.
Nothing exotic: any Postgres can consume it, and restoring is piping it into an
empty database. `bin/restore-rehearse.sh` automates exactly that, plus the branch
creation, the row-count comparison and the teardown.

That is worth knowing because it means the recovery does not depend on this
system's code being intact. Given the file and the key, Postgres alone is enough.

---

## Proving it still works, before you need it

- **Weekly, automatic**: `restore-rehearse-weekly` runs the full rehearsal and
  records durable evidence. If it stops reporting, the off-Mac liveness check on
  GitHub raises it.
- **The paper key**: `bin/key-recovery-test.sh` prompts for the key typed from
  paper, derives the public key it produces, and compares that against the
  repo-tracked `backups-public-key.txt`. A mismatch is decisive and needs no
  decryption at all. It then hands the typed identity to the rehearsal for the
  full end-to-end proof. You never create a key file and never have to remember
  to delete one — it is read silently and shredded on every exit path.

  Add `--backup <path>` to run the drill against an **off-Mac copy** — an R2
  object or a GitHub artifact you have downloaded — instead of the local
  `backups/` folder. That distinction is the whole point on the day it matters:
  "the backups restore" and "the backups that would SURVIVE restore" are
  different claims, and only the second one is about recovery.
- **Last passed**: 2026-08-14. The paper copy opened a GitHub-artifact backup —
  43,605,559 bytes, plaintext validated as a real dump.

Re-run the paper-key test whenever the key is re-copied, and at least yearly.
A transcription error in the paper copy is silent until the day it matters.

---

## Known limits, so nobody discovers them mid-incident

- **The full Mac-loss rebuild has never been performed.** Each piece above is
  individually verified; the sequence has not been walked on real replacement
  hardware. The first person to do it should correct this file from experience.
- **No measured recovery time.** Nobody has timed any of this, so there is no
  honest target to promise.
- **The credential list is a list, not a vault.** Restoring access means
  reissuing credentials one at a time from `secrets-inventory.md`. How long that
  takes is unmeasured.
- **Cloudflare account recovery depends on the 2FA recovery codes**, which are
  offline with you. If those are lost, the Worker and the R2 archive are both
  unreachable and only the GitHub artifact path remains.

## Worker rollback: backing out a bad release

*Added 2026-08-19, because the release path requires a rollback-plan reference
and the only worker procedure here was about re-pointing at a recovered
database. That is a different incident. This one is: the Worker is up, serving,
and the version now live is wrong.*

**This is the plan `--rollback-plan-ref RECOVERY.md#worker-rollback` names.**

Production traffic is moved by promoting an immutable provider version, never
by rebuilding from source — `bin/deploy-worker.sh` refuses a source deploy to
Production for exactly this reason. That property is what makes rollback cheap:
the previous version is still uploaded, still immutable, and still promotable.

1. **Get the version list.** `cd ~/carr-system && npx wrangler versions list`
   The currently serving version is marked; the one before it is the rollback
   target. Note its id.

2. **Promote the previous version to all traffic.**
   `./bin/deploy-worker.sh --promote-version <previous-id>` with the same four
   approval inputs the forward release used. If the release record for that
   earlier version still exists, its approval is already on file — a rollback
   to a previously approved version is not a new approval decision.

3. **Verify from outside, not from the command's exit code.** The deploy script
   reads the serving identity back from the fixed Production endpoint and
   refuses to claim success without it. Confirm independently:
   `./run.sh call list-verbs '{}'` should return the verb count you expect for
   the version you rolled back to. A verb count that DROPPED unexpectedly is
   the signal that started loop #276 and the whole immutable-release design.

4. **Say what happened.** Record the rollback with
   `.venv/bin/python tools/ops-record.py run --kind check --service carr-mcp
   --key release.rollback --state succeeded --environment production`
   and log the reason with the `log-decision` verb. A rollback nobody recorded
   looks identical to a release that never happened.

**What this does NOT cover.** A bad *migration* is not rolled back this way —
schema changes are forward-only here, and a Worker rollback across a migration
boundary can leave code reading a shape the database no longer has. If the
release included a migration, the rollback question is a database question
first; see the record-layer section above.

**Time to recover** is the length of one version promotion, which is seconds of
Cloudflare propagation rather than a rebuild. That is the reason the recovery
strategy on an ordinary verb release is `rollback` rather than `forward_fix`.
