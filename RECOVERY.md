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

Hosted records can survive Studio loss while local application state is lost.
This inventory describes recovery dependencies, not verified custody or a
current provider availability guarantee.

| thing | where it lives | reachable with |
|---|---|---|
| Committed code, tracked config, migrations, this file | GitHub, `DoctorCRE/carr-system` | GitHub access |
| Database-held records, doctrine, and rules | Neon Postgres, hosted | Neon access and a healthy database or recoverable copy |
| The API, Deal Room, OAuth | Cloudflare Worker, hosted | your Cloudflare login |
| Encrypted database copies | Cloudflare R2 bucket `carr-documents` | Cloudflare access and confirmed object retention |
| Cloud encrypted database copies | GitHub Actions artifacts, 90-day retention | GitHub access and a nonexpired qualified artifact |
| Matching age identity and account recovery factors | Operator-held custody, separately recoverable off the Mac | Verified custody and provider recovery procedures |

### If the R2 archive holds a bad object

Use Cloudflare R2 object management to inspect the exact content-addressed
backup key. Preserve good backups and review any archive-ledger discrepancy
through the authenticated record layer before changing stored evidence.

The Studio and cloud producers generate dumps separately. The cloud run copies
the same ciphertext to GitHub and R2. Those storage locations share the matching
age identity and account-access dependencies. R2 upload can skip successfully
when configuration is absent. A successful workflow alone does not prove an
R2 object exists, decrypts, or restores. Confirm the selected copy independently.

### Operator-held state inventory

Names only. The Studio state package below covers the explicit local allowlist.
Its existence does not prove an off-host copy exists. Recover or deliberately
reconstruct each required item before enabling writers. Never put values or
client contents into this runbook or a PR.

| State or capability | Recovery requirement |
|---|---|
| `out/outlook-intake/state.json` | Pending actions, unresolved mail, and intake checkpoints need an independent copy. Missing state falls back to 48 hours. |
| `out/outlook-intake/needs-refresh.json` | Recover the preview-body refresh queue with intake state. |
| `out/outlook-send/ledger.jsonl` and `~/.local/state/carr/outlook-send/ledger.jsonl` | Recover the ledger for the configured caller. Reconcile outbound history before sending again. |
| `out/calendar-archive/` | Recover historical snapshots separately from the current calendar feed. |
| `out/boards/` | Recover local notes and questions. |
| `exporters/templates/`, `exporters/dossier-roster.local.json`, `tools/dictation-rig/vocab-local.txt` | Recover local export inputs and vocabulary separately from Git. |
| Uncommitted work and other ignored working state | Classify losses or recover independent copies. |
| Installed job configuration, disabled jobs, machine-role marker, and OS permissions | Reconstruct deliberately from trusted configuration and incident records. |
| Outlook profile, mailbox access, Full Disk Access, and helper installation | Reauthenticate and rebuild capability. Mailbox retention is separate from SQL backup. |
| Drive and OneDrive vault files, attachments, finished deliverables, and R2 document objects | Recover file bytes through provider-specific retention. Database references are not file backups. |
| Provider configuration, `neon_auth`, Worker bindings, secrets, and Durable Object state | Recover live provider state separately from source declarations. |
| Matching age identity, historical decryption identities, credentials, signing material, sessions, keychains, and account recovery factors | Verify independent custody or reissue specific capabilities privately. Reissuing credentials cannot unlock old ciphertext without its matching identity. |

### Preserve and stage Studio state

`tools/studio-state.py` packages intake state and refresh queues, both send-ledger
locations, calendar history, boards, the dossier roster, local vocabulary,
the client-name list, and the triage folder guide. It records missing paths inside
its encrypted manifest. It does not collect Outlook stores, credentials, keys, sessions, templates,
`frozen-sources/`, or arbitrary ignored files. Recover templates and vault files
through their providers. Verify mailbox retention with Microsoft separately.
Neither a current calendar feed nor a new Outlook login replaces lost history.

All steps below are required to create an off-host recovery copy.

1. Fence intake, refresh, senders, calendar archival, board edits, and other writers before capture.
2. Select the public age recipient whose matching identity has verified independent custody.
3. Create and archive a uniquely named encrypted state package.

   ```sh
   .venv/bin/python tools/studio-state.py backup \
     --repo "$RECOVERY_REPO" --home "$RECOVERY_HOME" \
     --recipient "$RECOVERY_RECIPIENT" --writers-fenced \
     --output /secure/recovery/studio-state.tar.age --archive-r2 \
     --r2-quota-bytes "$RECOVERY_R2_QUOTA_BYTES"
   ```

4. Require an archived result and preserve its object key and ciphertext digest independently.
5. Resume source writers only after confirming the selected copy's recovery coverage.

Supply the repo and home paths, public recipient, and reviewed current R2 quota
in bytes through operator tooling. Use the configured quota, not remaining space.
Use a new output filename per capture. A local encrypted file alone is not an
off-host backup. A quota refusal, failed upload, or failed readback leaves the
off-host copy owed. No scheduled job is installed by this command. Run capture
again after material state changes and verify independent retention. Retry an
owed copy with the existing ciphertext and the same reviewed quota.

   ```sh
   .venv/bin/python tools/studio-state.py archive \
     --archive /secure/recovery/studio-state.tar.age \
     --r2-quota-bytes "$RECOVERY_R2_QUOTA_BYTES"
   ```

All steps below are required before activating a replacement host.

1. Fence the old primary and keep replacement writers disabled.
2. Retrieve the selected ciphertext and verify its independently recorded digest.
3. Stage the package into a new private directory on an encrypted operator-controlled volume.

   ```sh
   .venv/bin/python tools/studio-state.py restore \
     --archive /secure/recovery/studio-state.tar.age \
     --identity /secure/recovery/identity.txt \
     --staging /secure/recovery/staged-studio
   ```

4. Review the missing-path inventory and recover or explicitly classify each missing dependency.
5. Copy verified staged files to their matching replacement repo and home locations while writers remain fenced.
6. Reconcile pending sends against provider Sent history before enabling any sender.
7. Complete host preparation below and verify fresh intake, refresh, and other required job outcomes.

Restore verifies allowlisted names, exact sizes, and SHA-256 values before
publishing `repo/` and `home/` under the staging directory. It refuses an
existing staging path and does not activate jobs or overwrite live state.
Recover the full intake checkpoint even when its pending mail predates the
48-hour fallback. Preserve outbound ledger entries without treating them as
proof that Microsoft accepted or rejected a send. A staged copy is not a
completed host recovery or provider reconciliation receipt.

---

## If the Mac is gone but everything else is fine

Confirm hosted records remain healthy first. Local state can still require
recovery. All steps below are required.

1. Fence the old primary before preparing replacement writers.
2. Recover the canonical checkout and frozen runtimes on the replacement Mac.
3. Restore required operator-held state, export inputs, mailbox access, and vault files before enabling jobs.
4. Reissue specific required credentials through approved custody procedures.
5. Set the replacement's machine-role marker without activating jobs.

   ```sh
   .venv/bin/python -c 'from lib.machine_role import write_marker; write_marker("primary")'
   ```

6. Install Outlook build dependencies, helper, and human-granted Full Disk Access before activation.
7. Activate configuration only after state and dependencies are ready.

   ```sh
   .venv/bin/python ops/config-as-code.py install --apply
   ```

8. Register exactly one weekly restore scheduler and verify its actual enabled state.
9. Verify configuration and fresh per-job outcomes, resolving every missing or skipped job.

   ```sh
   .venv/bin/python ops/config-as-code.py check
   .venv/bin/python ops/scheduled-jobs-check.py
   ./run.sh health
   .venv/bin/python tools/ops-record.py health
   ```

Run these commands from the recovered canonical checkout. `set-role primary`
immediately installs jobs, so do not use it as a preflight. The Outlook installer
requires primary role, Xcode command-line tools, and Cargo/Rust. Follow
`bin/install-outlook-intake.sh` for helper build and permission steps. A task
definition alone is not scheduler registration. The retained headless option is
`ops/headless-tasks/install.sh install restore-rehearse-weekly`; disable any
duplicate registration and verify the reviewed host timezone first.

The failover inventory includes intake and refresh state requirements and an
explicit Outlook permission and outbound reconciliation prerequisite. Triage
must run on the host named by the current services catalog. The inventory does
not prove Full Disk Access, Accessibility, mailbox access, or a working helper.
If state is unavailable, record its loss and reconcile pending outbound work
before enabling senders. A clean-Mac walkthrough and actual off-host state
recovery remain operational checks, not outcomes established by this source.

---

## Data recovery

These are incident-operator instructions, not a recovery receipt. All numbered
steps in the selected procedure are required. There is no supported one-command
end-to-end recovery. Keep writers and traffic fenced until security bootstrap
and service cutover pass.

1. Fence surviving writers and preserve the original database and recovery copies.
2. Select a known-good pre-corruption copy, recording its provider ID, timestamp, digest, and source revision.
3. Recover provider access and the matching age identity through approved private custody procedures.
4. Prepare an isolated recovery checkout at the selected compatible source revision with frozen dependencies.

Use Python 3.14, Node matching `.nvmrc`, zsh, age, gh, PostgreSQL 18 clients,
and the pinned neonctl required by `bin/restore-rehearse.sh`. Install Python
packages from `requirements.lock` and Node packages with `npm --prefix mcp-server ci`.
Verify trusted executable locations and use private storage on an encrypted
operator-controlled volume. Reserve space for ciphertext and the restored database.
Never print connection URLs or key material. Do not hand-roll decryption or leave
plaintext on disk. Preserve restricted temporary storage and cleanup on interrupts.

### When production remains readable

The Neon wrapper requires readable production and provider access, including for
`--preflight`. It resolves production credentials and compares production state.
Do not use it as a total-outage bootstrap or under an offline-only constraint.
Complete the role-catalog prerequisite in Security bootstrap before loading data.

All steps below are required for this route.

1. Set `RECOVERY_RUN_ID` to the investigated good cloud run ID through operator tooling.
2. Run preflight against that exact copy and private identity path.

   ```sh
   ./run.sh restore-rehearse --preflight \
     --backup-run-id "$RECOVERY_RUN_ID" --identity /secure/recovery/identity.txt
   ```

3. Restore the same copy with branch retention enabled.

   ```sh
   ./run.sh restore-rehearse --backup-run-id "$RECOVERY_RUN_ID" \
     --identity /secure/recovery/identity.txt --keep-branch
   ```

4. Require exact count, row-content watermark, and app-role security parity verification before accepting the restored data.
5. Record the returned branch ID, target host, and recovered database name `restore_rehearse`.
6. Complete Security bootstrap and Service cutover before restoring traffic.

The newest successful workflow may postdate corruption. The wrapper retains failed runs
with `--keep-branch` too. Retention alone proves nothing. Its database `restore_rehearse`
is separate from inherited `neondb`. Branch promotion alone does not connect
consumers to the recovered database. Investigate failed comparisons against
corrupted production. Do not disable assertions to manufacture a pass.

### When production is unavailable

Stop before using the Neon wrapper. The retained local verifier,
`tools/restore-watermark.py verify-restore --target-kind disposable_local_cluster`,
can load a qualified cloud artifact into a fresh loopback PostgreSQL 18 target
without production counts. It still needs a trusted cluster-role catalog before
loading policies. A complete role and ACL bootstrap is not implemented.

No complete outage recovery is established until that prerequisite and role tests
can be proven. Local ciphertext paths are refused because they cannot identify
the artifact's pinned schema. There is no equivalent supported isolated-target
CLI for arbitrary R2 copies. Use a separately reviewed procedure
for those cases. Do not bypass workflow-identity checks, invent receipts, or call
staged rows a restored service.

## Structure reconstruction

Use this route only when no full data backup can be recovered. It reconstructs
structure, the migration ledger, and reviewed seeds. It does not recover lost
business records, history, or excluded `system_config` settings. Source history
cannot recreate those values.

All steps below are required for this route.

1. Provision an empty isolated target and securely bind `DATABASE_URL` to its PostgreSQL connection URL.
2. Load the committed snapshot through the secret-safe database wrapper.

   ```sh
   .venv/bin/python tools/pg-env-exec.py DATABASE_URL \
     psql -X --single-transaction -v ON_ERROR_STOP=1 -f db/schema.sql
   ```

3. Complete Security bootstrap, including pending migration reconciliation.
4. Record missing business data and settings as unrecovered before considering Service cutover.

The wrapper accepts `postgres://` or `postgresql://` URLs, not keyword-style
libpq DSNs. Never overlay `db/schema.sql` onto a full restored dump. Do not replay
all historical migrations from empty or invent ledger rows to bypass refusal.
See [the rebuild contract](migrations/README.md#rebuild-contract-wr-000385).

## Security bootstrap

Database-only backups exclude cluster roles, passwords, ownership, and ACLs.
The artifact verifier replays object grants from the authenticated producer's
pinned `db/schema.sql` and checks effective app-role privileges, including allowed
and denied operations. It does not reconstruct cluster roles, memberships, or ownership.
Pending migrations do not replay already-applied permission history.
Data equality alone is not authorization.

All steps below are required. Step 1 is a prerequisite to loading a data backup.

1. Reconstruct the reviewed cluster-role catalog before loading policies that reference those roles.
2. Restore memberships, ownership, ACLs, default privileges, and SECURITY DEFINER expectations on the recovered database.
3. Prove allowed and denied operations under actual reader, writer, jobs, and authority roles.
4. Securely bind `DATABASE_URL` to the recovered host and database, using `restore_rehearse` for the Neon wrapper.
5. Inspect the pending migration prefix at the selected compatible source revision.

   ```sh
   .venv/bin/python tools/migrate.py --through 0940_codex_job_delegation_control_refs.sql
   ```

6. Apply only the reviewed compatible prefix to that recovered target.

   ```sh
   .venv/bin/python tools/migrate.py --apply --yes --through 0940_codex_job_delegation_control_refs.sql
   ```

7. Confirm no migrations remain pending within that prefix.

   ```sh
   .venv/bin/python tools/migrate.py --through 0940_codex_job_delegation_control_refs.sql
   ```

8. Repeat authorization checks after migration before entering Service cutover.

The filename bounds this example's reviewed source prefix. Select a compatible
exact checked-in boundary for another revision. Later files remain held back.
Keep checksum, unknown-entry, ordering, and atomic-group checks intact. Stop if
role reconstruction or denial tests cannot be proven. `carr_backup` and
`dot_reader` are required examples, not a complete role catalog. Extracting the
snapshot preamble is insufficient. A complete raw-backup security bootstrap is
still a separate remediation, not something these instructions implement.

## Service cutover

All steps below are required after data and security checks pass.

1. Inventory every current database consumer and preserve the old endpoint for an explicit rollback decision.
2. Rebind Worker database secrets to the recovered host and actual recovered database.
3. Rebind the GitHub production migration secret `PRODUCTION_DATABASE_URL` to the same recovered database.
4. Rebind backup, exporter, scheduled-job, and other consumer connections without printing values.
5. Follow the ordinary deployment path only when its green main canary and readable `/release` prerequisites hold.
6. Verify role-denial checks and representative application reads and writes before restoring traffic.
7. Reconcile outbound work against provider history before enabling senders.
8. Verify a fresh backup and the deployed `/release` identity before declaring service recovered.

`PRODUCTION_DATABASE_URL` is only the deployment migrator's credential, not the
whole Worker binding set. If `/release` is unavailable or authorization cannot
be proven, stop before traffic. The deployment workflow applies pending
migrations and verifies release identity. It is not a complete outage bootstrap.
See [Worker recovery](runbooks/rollback-worker.md) for schema compatibility and
Durable Object migration constraints. These instructions do not authorize a
production cutover during a documentation change or offline review.

---

## Proving it still works, before you need it

- The weekly task definition selects `--backup-run-id latest`. The selector pins
  one verified numeric cloud run ID before restoring its artifact. Registration
  and a fresh successful outcome must be checked separately. Routine dispatch
  payloads are refused for full recovery. Do not assume the scheduler can obtain
  recovery capability.
- Cloud-artifact restore verification binds the producer's workflow identity.
  Changing workflow bytes can reject historical artifacts that remain decryptable.
  Preserve those checks. Historical producer support needs separately reviewed
  implementation, not a bypass during an incident.
- The RPO evaluator reports `no_evidence / evidence_producer_unavailable`.
  Deleted PITR proof tools are not a fallback. Inspect the provider's current restore window
  and available recovery points during an incident. Do not promise a fixed six-hour
  window or a measured recovery-point bound from dated prose.
- Off-Mac backup creation and off-Mac freshness observation are different controls.
  `.github/workflows/backup-freshness.yml` independently checks qualified cloud
  artifacts every six hours using `ops/recovery-point.py --cloud-only --json`.
  Stale, missing or unreadable evidence fails the workflow for GitHub Actions
  failure notifications. Local copies cannot mask a cloud failure. Notification
  receipt depends on GitHub subscription settings and remains unverified, as
  does a scoped cloud-artifact restore drill.
- `./run.sh key-recovery-test --backup-run-id RUN` tests an authenticated cloud
  artifact with the paper identity. Omitting the option selects `latest`.
  It reads input silently,
  checks the derived recipient, and uses restricted temporary storage with cleanup
  on exit and interrupts. It can contact Neon and attempt an ops receipt. Run it
  only as an authorized incident or drill after the recovery prerequisites exist.
  It does not prove account recovery, role bootstrap, or complete service cutover.
- The original guide records a historical result dated 2026-08-14, opening a
  43,605,559-byte GitHub-artifact backup. That is not current custody or a full
  recovery pass. A new drill must name its artifact, custody source, and outcome
  without recording key material.

Re-run the paper-key test whenever the key is re-copied, and at least yearly.
A transcription error in the paper copy is silent until the day it matters.

---

## Known limits, so nobody discovers them mid-incident

- No complete Mac-loss rebuild, raw-backup authorization bootstrap, or service
  cutover is demonstrated by these instructions. Correct commands are not a drill.
- No measured recovery time or verified current PITR window is established here.
  Daily backup cadence bounds lost writes only when a known-good pre-corruption
  copy exists and its custody and provider access survive.
- The SQL backup covers `public` and `ops`, not file-vault bytes, local state,
  other schemas, cluster roles, ownership, or ACLs. Git preserves committed source,
  not lost business data. Provider recovery and local state protection remain separate.
- The matching age identity is a shared dependency of every ciphertext copy.
  Keep historical identities when rotating recipients. No evidence here establishes
  that custody is absent or currently recoverable.
- Cloudflare access depends on recoverable account factors. Losing those can make
  both Worker and R2 inaccessible. GitHub access alone cannot unlock a backup
  without the matching identity. Recover or reissue specific required capabilities;
  do not restore retired broad credential-maintenance runners.

The retained safety controls include read-only guarded dump generation, table and
partition census, RLS and sequence checks, snapshot-held verification, encryption
and size-floor checks, and atomic promotion that preserves prior acknowledged files.
Recovery retains fresh-target isolation, target-host and default-branch guards,
cleanup limited to the returned branch ID, exact counts and content hashes,
COPY-aware streaming in the cloud loader, `psql -X` with `ON_ERROR_STOP=1`, and
error redaction. These controls must remain intact. Artifact verification also
requires the pinned schema grants and app-role security parity before completion.

## Worker recovery

See [Worker recovery](runbooks/rollback-worker.md). Production deployments run
through `.github/workflows/deploy.yml` after a green main canary.
