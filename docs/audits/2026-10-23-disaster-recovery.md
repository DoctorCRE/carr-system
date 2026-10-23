# CARR disaster recovery audit — 2026-10-23

## Verdict

**CARR has a real off-Mac database backup, but complete recovery is not demonstrated.** A Studio failure should leave hosted records and the Worker available. It can still lose local operational state and disable jobs that the current replacement-Mac instructions do not fully reconstruct. Neon corruption is conditionally recoverable from encrypted backups if a good pre-corruption copy, its private key, provider access, and the required database roles survive. The documented disaster procedure is not a reliable end-to-end restore procedure today.

The most consequential defects are the restore wrapper's dependence on readable production, omitted role/ACL reconstruction, and unprotected local application state. Fixing those is necessary before claiming that CARR can be restored as a working, correctly authorized service. A successful data comparison alone does not establish that claim.

## Scope, assumptions, and evidence standard

- Report only; the sole repository change is this report. No implementation, merge, deployment, schedule change, or recovery was performed. A draft PR is the requested endpoint.
- Source snapshot: `c53b791f15d07b4e2c64dfd4aac10f3b03277161`, fetched from GitHub `main` during this audit. Citations are `file:line` at that revision. Main was advancing during the session; later changes are outside this snapshot. “This week” means October 19–23, 2026, America/Chicago.
- `AGENTS.md` was read first. The explicit report-only/no-live instruction overrides its record-layer calls and merge workflow. The requested Markdown report overrides its general prohibition on durable Markdown. An isolated worktree was used.
- No CARR endpoint, Neon, Cloudflare, Microsoft, installed Mac service, or production database was contacted. No environment dump, `env`, `printenv`, or `ps eww` was run. No `.env`, credential/token/key/keychain file, vault content, encrypted backup, or database dump was opened. The committed `db/schema.sql` was treated as a dump and not opened or loaded. Source code referring to these objects was inspected.
- GitHub source/history and Actions **metadata** were read. No workflow logs or artifact bytes were downloaded. Account recovery, key custody, actual R2 retention, installed launchd state, Outlook mailbox retention, and vault version history remain unverified.
- Assumption: the incident operator retains legitimate GitHub/Neon/Cloudflare/Microsoft access or can recover it. Loss of all account recovery factors is a separate dependency, not something Git migrations repair. Effort estimates below are engineering days, excluding account recovery and scheduling a hardware drill.

### Positive operational evidence obtained

GitHub reports seven daily scheduled cloud-backup runs on October 17–23 as successful. Today's [run 39920734721](https://github.com/DoctorCRE/carr-system/actions/runs/39920734721) ran the real dump/upload steps; its disabled and failure-proof branches were skipped. Its producer commit is `86645f8fd100e8e0ac360c2dd7ece13d352e02a7`. The producer's `backup-nightly.yml` is byte-identical to the audited revision.

The artifact API returned the following metadata; this proves a retained ciphertext artifact is listed, **not that it decrypts or restores**:

| Field | Observed value |
|---|---|
| Artifact | `carr-backup-20261023-run-39920734721-attempt-1` |
| Size | 3,885,233,983 bytes, approximately 3.89 GB |
| Created | October 23, 2026, 02:57:26 CDT |
| Expires | January 21, 2027, 01:52:47 CST |
| Expired | `false` |
| Provider digest | `sha256:714cfbcbff99f5bc8dc4daa6d4a77c4768a3b640fc6c9c920c25415b9c897c67` |

The R2 step also concluded success, but its implementation can exit successfully without uploading when configuration is absent. Therefore this audit does **not** claim today's R2 object exists (`.github/workflows/backup-nightly.yml:369-392`). Only today's artifact metadata was individually checked; the other six observations are run conclusions.

## What is backed up, where, and how often

| Asset/path | Producer and cadence | Destination/retention | Recovery boundary |
|---|---|---|---|
| PostgreSQL application schema and data | Both producers call `bin/backup-dump.sh`; guarded `pg_dump` selects `public` and `ops` | Age-encrypted plain SQL | Includes business records and database-held doctrine/rules/ops evidence in those schemas. Excludes other schemas, cluster roles/passwords, ownership and ACLs. `bin/backup-guard.py:439-444`; `bin/backup-dump.sh:41-46,67-90`. |
| Studio database dump | Nightly chain, 02:05 local when it can run | `$REPO/backups/carr-YYYYMMDD.sql.age`; newest 14 files; attempted R2 archive `carr-documents/backups/<sha256-prefix>/<filename>` | Local copy dies with the disk. “Forever” R2 retention is an intent in source, not an inspected bucket policy. `bin/nightly.sh:87-89`; `bin/backup-dump.sh:63-66,102-103`; `bin/backup-archive-r2.py:143-160`; `ops/config/services.json:771`. |
| Cloud database dump | GitHub daily cron `35 2 * * *`, nominally 02:35 UTC / 21:35 previous date CDT; ordinary manual dispatch also supported | Run-and-attempt-specific GitHub artifact, 90 days | Mac-independent. Scheduled mode depends on the enable flag. Today's run started 02:52:47 CDT, so the cron time is not an observed delivery SLA. `.github/workflows/backup-nightly.yml:78-83,165-172,289-320`. |
| Cloud second copy | Same cloud run, after artifact upload | `carr-documents/backups/cloud/YYYYMMDD/carr-YYYYMMDD.sql.age` | Same ciphertext as that run's artifact, not an independently generated dump. Same-date attempts reuse this R2 key. Optional credential check can skip; upload readback compares byte length. `.github/workflows/backup-nightly.yml:333-392`. |
| Database portability mirror | Studio nightly | OneDrive `Backups/portability-mirror` and local `out/mirror` | Additional public-table projection, excluding `sensitive_blob` and `tool_call`; not a vault or full database backup. `bin/nightly.sh:99-101`; `pipelines/doctrine_mirror.py:22,206-217`. |
| Code, tracked templates, migrations, schema reconstruction inputs | Git commits and pushes | GitHub source history | Recoverable source, not uncommitted work or business data. Snapshot-only reconstruction is the supported empty-DB path. `migrations/README.md:10-30`; `.gitignore:5-9,30-31,68-72`. |
| Neon point-in-time history | Provider-managed, not this workflow | Actual configured restore window uninspected | `RECOVERY.md:73-75` says six hours, but that dated prose is not current provider evidence. Do not promise six hours or a measured RPO from it. |

Nominal daily backups imply up to roughly one daily interval of lost writes only when creation succeeds and corruption is detected in time. The real recovery point is the newest **known-good pre-corruption snapshot**, not the newest filename. No current end-to-end recovery time was measured; the existing guide acknowledges this (`RECOVERY.md:138-145`).

### What the database backup does not contain

| Item | What can survive / what must be recovered separately | Evidence |
|---|---|---|
| Local launchd configuration | Git preserves tracked plist templates. Installed edits, disabled/held jobs, registration, machine-role marker, local settings and permissions need separate recovery or deliberate reconstruction. | `ops/config-as-code.py:2051-2097,2144-2163,2668-2678`; `lib/machine_role.py:6-17,66-75`. |
| Keys and account access | Private age identity, DB/provider credentials, signing material, sessions, keychains and recovery factors are outside the dump. Reissuable credentials differ from the irreplaceable key needed for old ciphertext. Escrow was not inspected. | `bin/restore-rehearse.sh:32-37,456-470`; `RECOVERY.md:55-58,143-148`; `.gitignore:143-147`. |
| Outlook profile and application state | Microsoft mailbox access is separate. Local intake checkpoints, unresolved/pending requests, refresh queue, send ledger and OS permissions are not covered by SQL backup. | `lib/outlook_intake.py:20-42,63-77,647-680`; `lib/outlook_send.py:301-313,818`. |
| Calendar archive | Historical changed-feed snapshots live under ignored `out/calendar-archive`; the archive script has no off-host upload. Signing into the current feed does not reconstruct events that rolled out. | `bin/archive-calendar.sh:4-11,19-36`; `.gitignore:5-9`; `bin/nightly.sh:102`. |
| Vault files and attachments | Drive/OneDrive files and finished client deliverables require their own provider retention/recovery. Database references are not file bytes. R2 document objects are likewise not contained in a PostgreSQL dump. No independent file-vault backup was established by this code audit. | `bin/backup-guard.py:440-441`; `.gitignore:80-85`; `exporters/targets.py:28-34`. |
| Local export inputs and working state | Frozen templates, dossier roster, client vocabulary, boards, uncommitted work and other ignored output need explicit classification. The roster error still says to copy it from Joe's machine. | `.gitignore:20-21,36-57`; `exporters/dossier_roster.py:65-74`. |
| Provider-managed state | `neon_auth`, Cloudflare Worker secrets/bindings and Durable Object state, Microsoft state, and provider control-plane configuration need provider-specific restoration. Source declarations do not back up their live contents. | `bin/backup-dump.sh:41-46`; `mcp-server/wrangler.toml:3-18`; `runbooks/rollback-worker.md:11-14`. |

## Findings ranked by impact

### F1 — High: the documented disaster restore requires production and names the wrong cutover abstraction

**Evidence:** `RECOVERY.md:83-95` prescribes rehearsal, `--keep-branch`, promotion and `bin/migrate-prod.sh`. The wrapper lists Neon branches, resolves production's owner DSN, and requires successful production row counts before restoration (`bin/restore-rehearse.sh:630-648,682-689`). Its target database is `restore_rehearse` (`:92,817-839`), separate from the branch's inherited `neondb`. `--keep-branch` retains even failed runs (`:267-271`). The prescribed migration executable was deleted by `9b4b9bb1` / #2124 on October 22.

**Consequence:** total production loss blocks the sanctioned wrapper. After a partial corruption, branch promotion alone can reconnect clients to inherited corrupt `neondb`, rather than the restored database. Retention is not proof of success. The deployment path also requires a readable live `/release` and only applies pending migrations (`.github/workflows/deploy.yml:73-85,144-149`), so it is not a complete outage bootstrap.

**Fix / effort:** one explicit recovery entrypoint using a selected encrypted artifact and fresh target, independent of production counts; retain only after exact verification, report the recovered database name, and supply a cutover/rollback checklist. Replace the deleted command with `tools/migrate.py` against the recovered target, after security reconstruction. **1–2 days**, plus a nonproduction cutover exercise. Slice A and B.

### F2 — High: a successful data restore does not restore authorization

**Evidence:** the actual dump excludes ownership and ACLs (`bin/backup-guard.py:439-444`). Both loaders discard GRANT/REVOKE/default-privilege/owner statements (`tools/restore-watermark.py:695-718`; `bin/restore-rehearse.sh:891-907`). Fresh database creation installs only `pg_trgm` and `pgcrypto` (`tools/restore-watermark.py:752-766`). The restored migration ledger makes existing grant migrations nonpending (`tools/migrate.py:506-510`; example `migrations/0913_grant_v_client_account.sql:2`). The snapshot generator documents that lost function revocations can restore permissive PUBLIC execution (`bin/schema-snapshot.sh:1477-1493`). Policies also reference cluster roles absent from a database-only dump, including `carr_backup` and `dot_reader` (`migrations/0475_backup_role_work_request_rls_read_policy.sql:45-48,65-66`; `migrations/0756_dot_reader.sql:49-56`).

**Consequence:** a bare cluster may fail loading policies because roles are missing. Even a surviving Neon branch with those roles can restore data but lack grants or regain unsafe default privileges in the new database. Running only pending migrations cannot replay the already-applied permission history. The comment claiming migrations rebuild the permission model (`bin/backup-dump.sh:76-78`) overstates what the current path does.

**Fix / effort:** version a reviewed role-membership and ACL/default-privilege bootstrap independently of secret login material. Apply it to the recovered database, then test both allowed and denied operations under actual reader/writer/jobs/authority roles before cutover. Include ownership and SECURITY DEFINER expectations. Do not replay historical migrations indiscriminately or overlay the full schema snapshot onto a data restore. **2–3 days**, plus acceptance rehearsal. Slice B; blocks complete service recovery.

### F3 — High: losing the Studio can lose unique application state

**Evidence:** `RECOVERY.md:41-43` says the Mac holds no unique data. Intake keeps pending actions locally until acknowledgement (`lib/outlook_intake.py:20-23`); missing state becomes `{}` and recovery falls back to only 48 hours (`:77,647-649,679-680`). The send path uses a local idempotency ledger (`lib/outlook_send.py:301-303,818`). Calendar snapshots retain history absent from later feeds (`bin/archive-calendar.sh:4-11,19-36`). These files and local export inputs are outside SQL scope and Git (`.gitignore:5-9,20-21,36-57`).

**Consequence:** acknowledged database records can survive while older unresolved mail, pending actions, enrichment requests, calendar history and local configuration do not. Outlook reauthentication alone does not recover the application queue. Losing the send ledger removes one duplicate-send guard; mailbox matching remains another (`lib/outlook_send.py:309-313`), so duplicate sends are a risk, not a certainty.

**Fix / effort:** encrypted off-host backup of an explicit state allowlist, including intake state/refresh queue, send ledger, calendar archive, boards and necessary local export inputs. Restore state before enabling writers; reconcile pending sends against provider history. Define separate vault-file and mailbox recovery/retention. **1–2 days** for the state package and lost-checkpoint regression test; provider policy decisions separately. Slice C.

### F4 — High consequence, unverified control: key and account custody remain single recovery dependencies

**Evidence:** every encrypted backup needs the private age identity; its default is on the Mac and source requires a separate offline copy (`bin/restore-rehearse.sh:32-37,456-470`). The paper-key test derives a recipient and exercises recovery (`bin/key-recovery-test.sh:229-269`; `RECOVERY.md:116-131`). The only dated pass in the guide is August 14. Cloudflare access also depends on account recovery factors (`RECOVERY.md:146-148`).

**Consequence:** losing every copy of the matching private key makes all these GitHub/R2 ciphertext copies unusable. Distinct storage locations do not remove this shared dependency. This audit found **no evidence that the key is missing**, and deliberately did not inspect it; current recoverable custody is unproven.

**Fix / effort:** paper-only drill against an identified off-Mac artifact; record outcome, copy ID, date and custody owner without key material. Establish a separately recoverable second escrow and tested account recovery. Keep historical decryption keys when rotating recipients. **0.5 day engineering/operator time**, dependent on human custody. Slice D.

### F5 — Medium: archive success and freshness can overstate off-Mac protection

**Evidence:** quota refusal returns success with `archived:false` and a key (`bin/backup-archive-r2.py:149-153`); the caller extracts any key and prints archive success, while swallowing upload errors (`bin/backup-dump.sh:120-134`). Local pruning happens first (`:102-103`). Cloud R2 can skip successfully (`.github/workflows/backup-nightly.yml:369-372`). The registry explicitly says missed/skipped cloud schedules are observed by the Mac-local nightly, not GitHub failure email (`ops/config/services.json:771`). Local freshness uses file mtime (`ops/recovery-point.py:110-120`).

**Consequence:** a local success line may describe no archived object. Studio loss removes the configured artifact-freshness observer while cloud backup creation continues independently. Copying an old local backup can refresh the mtime signal. Today's verified GitHub artifact remains valid positive evidence; these are future-detection and secondary-copy gaps.

**Fix / effort:** parse structured archive status and require `archived == true`; preserve explicit owed/retry state and verify object identity, not just a printed path. Add an off-Mac observer of the newest qualified artifact's producer time and independently test alert delivery. Use producer timestamps for local copies. **1–2 days combined**. Slice D.

### F6 — Medium: local/R2 restoration can remove valid text rows

**Evidence:** the wrapper's global `sed` filter drops lines beginning `GRANT `, `REVOKE ` and other SQL prefixes regardless of COPY state (`bin/restore-rehearse.sh:891,903-907`). The cloud-ID loader already has a COPY-aware filter (`tools/restore-watermark.py:695-718`). A synthetic in-memory COPY row beginning `GRANT request submitted` was removed by the current wrapper filter during this audit.

**Consequence:** some otherwise valid backups fail exact restoration through the local/R2 path. The final watermark comparison detects the loss (`bin/restore-rehearse.sh:1082-1101`), so this is a fail-stop defect, not silent certification of corrupted data.

**Fix / effort:** use the existing COPY-aware `load_artifact` path for every backup source; retain the exact watermark gate. Add one regression covering SQL-looking COPY rows. **0.5–1 day**. Slice B.

### F7 — Medium: replacement-host install and failover do not reconstruct all current capabilities

**Evidence:** the guide's single installer step (`RECOVERY.md:59-63`) skips launchd jobs whose binaries are absent (`ops/config-as-code.py:2079-2082`) and copies scheduled-task definitions without registering them (`:2144-2163`). Outlook needs its helper build and human Full Disk Access (`bin/install-outlook-intake.sh:41-83,95-100`). `set-role` immediately installs jobs (`ops/config-as-code.py:2668-2678`). The failover inventory lists nine jobs, omits Outlook intake/triage, and incorrectly expects `out/backups` instead of `backups` (`ops/config/studio-failover.v1.json:20-107,68-70`; `bin/backup-dump.sh:64`). Its referenced scheduled-jobs manifest is absent (`ops/config/studio-failover.v1.json:13-18`). Current services assign intake to Studio and triage to MacBook (`ops/config/services.json:76-111`).

**Consequence:** a cloned/installed Mac can appear reconstructed while jobs are skipped, unscheduled, or assigned to the wrong host. The nine-job takeover is bounded; it proves neither all Studio capability coverage nor recovery of missing state. The automatic failover rehearsal script/plist were deleted by `ee025e4b`; manual takeover still exists.

**Fix / effort:** reconcile inventory against current services; correct state paths; explicitly build dependencies, restore state, choose machine role and fence the old writer before activation. Register exactly one weekly scheduler and verify per-job results. **1–1.5 days**, plus **0.5–1 day** clean-Mac walkthrough. Slice C.

### F8 — Medium: recovery proof and historical runbook claims exceed what remains supported

**Evidence:** the weekly skill selects a local dump and no cloud run ID (`ops/scheduled-tasks/restore-rehearse-weekly.SKILL.md:10`); that path emits no provider-bound restore receipt (`bin/restore-rehearse.sh:1119-1120`). Routine dispatch payloads are refused for full recovery (`:78-86`). Editing the backup workflow invalidates earlier runs for the strict verifier (`:177-182`; `tools/restore-watermark.py:407-439`). The RPO evaluator now returns `no_evidence / evidence_producer_unavailable` (`mcp-server/src/recovery-matrix.v5.js:672`); `99b5fb45` deleted the old PITR proof tools. Raw migration replay is explicitly unsupported (`migrations/README.md:10-30`; `tools/migrate.py:679-693`).

**Consequence:** “weekly automatic” does not establish that an off-Mac artifact restores, that the current scheduler can obtain recovery capability, or that a promised PITR/RPO bound is met. Old artifacts can remain decryptable while strict provenance verification rejects their old workflow identity. Git reconstruction cannot recreate lost business records.

**Fix / effort:** document retained mechanisms and their limits; schedule a cloud-artifact restore under a scoped recovery capability; retain trusted historical producer identities without allowing arbitrary workflows. Replace the stale PITR claim with current provider evidence and a timed nonproduction exercise. **1–2 days**. Slice A and D. Today's artifact has matching workflow bytes, so the historical-identity restriction is not presently blocking that specific copy.

## Effect of this week's deletions

| Change | Current effect |
|---|---|
| `9b4b9bb1` / #2124, local release pipeline removal | **Broken surviving instruction:** `RECOVERY.md:95` still invokes deleted `bin/migrate-prod.sh`. Use the retained migration runner against a restored, authorized target; deploy through the existing workflow only after its prerequisites pass. |
| `99b5fb45` / #2161, repair/PITR tools removal | `bin/pitr-restore-proof.sh`, `tools/pitr-restore-proof.py`, `bin/outage-drill.py`, old breakglass/reconcile helpers are gone. Do not prescribe them. `docs/frontier-finding/README.md:21-25` identifies that launcher as historical. RPO proof is explicitly unavailable. |
| `ee025e4b`, automatic Studio rehearsal removal | Automatic failover rehearsal script/plist are gone; `ops/studio-failover.py` remains, with real fencing and readiness conditions. |
| `da2ff1eb` / #2159, proof-only metering removal | **Not a restore break:** `ops/platform-metering-gate.py` and its real disposable-branch admission remain. An offline invocation admitted the normal 120-minute/zero-active-branch case. |
| `cebd87e7` / #2155, assurance rehearsal bindings removal | Core backup, restore, key test and watermark files still exist. Their syntax and selected pure tests passed. Removal is not evidence that backup loading was deleted. |
| `7cbedc2c` / #1930, snapshot-only reconstruction | Current policy requires `db/schema.sql` followed by pending migrations; all historical migrations were never a clean-room data reconstruction. No migration or `db/` file deletion was found in October 19–23 history through this snapshot. |
| `98e00ed6` / #2166, broad credential maintenance removal | Nightly no longer runs broad credential mint/health runners. Do not add them back to recovery instructions; recover/reissue the specific required capabilities. Backup, ping and recovery-point steps remain (`bin/nightly.sh:87-89`). |

## Current restore runbook, with honest stopping points

**These are incident-operator instructions, not commands executed by this audit.** All numbered steps within the selected scenario are required. Run command examples in Bash or zsh. Commands below use retained files at the audited revision. They are source-checked; a real full-data restore, role bootstrap and production cutover were prohibited here and remain unverified. There is no supported one-command end-to-end recovery today; F1–F4 describe the prerequisites and missing automation. Do not turn a conditional runbook into a claim that those gaps were solved.

### Common preparation

1. Fence failed or surviving writers before recovery; preserve the original data and the selected recovery copy.
2. Select a known-good point before corruption; record the copy's provider ID, timestamp, digest and source revision.
3. Recover provider accounts and the matching age identity through their approved human custody procedures.
4. Clone CARR and check out the audited source or a reviewed compatible revision.

   ```sh
   git clone https://github.com/DoctorCRE/carr-system.git carr-recovery
   cd carr-recovery
   git checkout --detach c53b791f15d07b4e2c64dfd4aac10f3b03277161
   ```

5. Install Python 3.14, Node matching `.nvmrc`, zsh, age, gh and PostgreSQL 18 clients.
6. Install the frozen Python and Node dependencies in the recovery checkout.

   ```sh
   python3.14 -m venv .venv
   .venv/bin/python -m pip install -r requirements.lock
   npm --prefix mcp-server ci
   ```

7. Verify trusted executable locations and a private working directory before handling the recovery copy.

   The verifier resolves `age`, `gh`, `psql` from fixed system/Homebrew locations, not arbitrary PATH shims (`tools/restore-watermark.py:285-323`). On macOS the wrapper also uses BSD `stat` (`bin/restore-rehearse.sh:476,519`); do not assume the zsh wrapper is Linux-portable. Use an encrypted operator-controlled volume for temporary artifacts, set `umask 077`, and reserve capacity for a roughly 3.89 GB ZIP, extracted ciphertext and a substantially larger restored database. The audit did not determine the database's required disk size.

### A. Studio dies; Neon and hosted services remain healthy

1. Recover the canonical main checkout at `~/carr-system` and frozen runtimes on the replacement Mac.
2. Restore local application state, export inputs and vault access before enabling any scheduled writer.
3. Confirm the old primary is fenced and set the replacement's role marker without activating jobs.

   Run from the canonical checkout, not the detached data-recovery checkout:

   ```sh
   .venv/bin/python -c 'from lib.machine_role import write_marker; write_marker("primary")'
   ```

   The retained marker writer does not install jobs (`lib/machine_role.py:45-57`). This ordering matters: the Outlook installer refuses a nonprimary machine (`bin/install-outlook-intake.sh:34-38`), while `config-as-code.py set-role primary` immediately invokes `install --apply` (`ops/config-as-code.py:2668-2678`). Do not use that activating shortcut as a preflight.

4. Reissue required credentials and complete Outlook sign-in, helper installation and Full Disk Access.

   Install Xcode command-line tools/C compiler and Cargo/Rust before `bin/install-outlook-intake.sh`; the installer builds the pinned reader and helper (`:47-51,80`). Its final instructions cover the human permission and launchctl steps (`:95-100`). Keep jobs unregistered until state and credentials are ready. The roster currently requires an off-machine copy or deliberate reconstruction; copying from the destroyed Studio is not a recovery method.

5. Activate configuration only after state and dependencies are ready.

   ```sh
   .venv/bin/python ops/config-as-code.py install --apply
   ```

   This is an activation step. Administrator-owned policy destinations must be writable as required by the installer (`ops/config-as-code.py:191-200,1894-1902`). Missing targets and any skipped job must be resolved individually.

6. Register exactly one weekly restore scheduler and verify its actual enabled state.

   The retained headless option is `ops/headless-tasks/install.sh install restore-rehearse-weekly`; it requires the reviewed host timezone and does not change desktop scheduling (`lib/headless_tasks.py:764-797`). Disable any duplicate native registration before choosing it. The tracked schedule is Tuesday 07:40 local (`ops/headless-tasks/com.carr.headless.restore-rehearse-weekly.plist:23-31`). A task definition alone is not registration, and payload-based routine dispatch remains refused for full restore.

7. Verify configuration, installed jobs, current health and fresh per-job outcomes.

   ```sh
   .venv/bin/python ops/config-as-code.py check
   .venv/bin/python ops/scheduled-jobs-check.py
   ./run.sh health
   .venv/bin/python tools/ops-record.py health
   ```

   These are incident-time live checks and were not run here. Confirm one current successful result for each required capability, especially Outlook intake, exports, backup, cloud-backup freshness and weekly restore. Do not accept a green nine-job failover receipt as proof of the omitted Outlook paths. If state cannot be recovered, record its loss and reconcile pending outbound work before resuming it.

### B. Restore encrypted data while production remains readable

1. Confirm a selected artifact predates corruption and its matching identity is available privately.
2. Run the wrapper's preflight against the exact cloud run, then restore with retention enabled.

   ```sh
   ./run.sh restore-rehearse --preflight \
     --backup-run-id 39920734721 --identity /secure/recovery/identity.txt
   ./run.sh restore-rehearse --backup-run-id 39920734721 \
     --identity /secure/recovery/identity.txt --keep-branch
   ```

   The run ID above is today's observed copy, **not a claim it predates an unknown corruption**. Substitute the investigated good run when necessary. The wrapper needs the pinned `neonctl`, noninteractive recovery credentials and readable production; even `--preflight` loads credential configuration and contacts Neon (`bin/restore-rehearse.sh:77-86,630-648`). Do not execute it under a no-live audit constraint. A stale/corrupted production comparison can fail even if artifact contents restored correctly; do not disable assertions to claim a pass.

3. Require successful exact watermark verification and record the retained branch ID plus database name `restore_rehearse`.
4. Rebuild and test authorization on that database before reconnecting any application role.
5. Apply compatible pending migrations explicitly to the recovered database, then confirm none remain.

   Securely provide `DATABASE_URL` for **that recovered target**, without echoing it. The retained command is:

   ```sh
   .venv/bin/python tools/migrate.py --apply --yes \
     --through 0940_codex_job_delegation_control_refs.sql
   .venv/bin/python tools/migrate.py \
     --through 0940_codex_job_delegation_control_refs.sql
   ```

6. Rebind every runtime and job credential to the recovered database, then enter the ordinary deployment path.
7. Verify role-denial tests, representative application reads/writes, outbound reconciliation and a new backup before restoring traffic.

   **Cutover is a gap, not “promote and done.”** Update the Worker database secrets, the GitHub production migration secret, backup/exporter/jobs connections and any other current consumers; do not print their values. `PRODUCTION_DATABASE_URL` is the deployment migrator's credential, not the whole Worker binding set (`.github/workflows/deploy.yml:42-53,146-149`; `mcp-server/wrangler.toml:3-18`). Normal deployment needs green main canary and a readable `/release`; use `runbooks/rollback-worker.md`. If `/release` is down or role/grant reconstruction cannot be proven, stop before traffic: a complete outage bootstrap is not implemented. Preserve the old endpoint for an explicit rollback decision.

### C. Production is unavailable; stage data with the existing local verifier

1. Prepare a fresh isolated PostgreSQL 18 cluster and securely supply its loopback/socket admin DSN.
2. Provision the reviewed cluster-role catalog required by the backup's policies before loading it.

   **Current blocker:** no complete raw-backup role/ACL bootstrap exists. `carr_backup` and `dot_reader` are concrete required examples, not a complete role list. The schema snapshot preamble deliberately omits some roles, so extracting it is insufficient (`bin/schema-snapshot.sh:1233-1238,1260-1399`). Do not load the entire schema snapshot before the full dump. If the role catalog cannot be reconstructed from trusted configuration, stop and ship Slice B; do not call an empty-cluster restore runnable merely because the CLI parses.

3. Set `RESTORE_ADMIN_DSN` through secure operator tooling and place the identity at the private path below.
4. Restore the selected cloud artifact through the local-cluster verifier and evaluate its direct output.

   ```sh
   umask 077
   set -o pipefail
   .venv/bin/python tools/restore-watermark.py verify-restore \
     --repository DoctorCRE/carr-system --run-id 39920734721 \
     --identity /secure/recovery/identity.txt \
     --target-kind disposable_local_cluster --database carr_recovered \
     --work-dir /secure/recovery/verify --admin-dsn-env RESTORE_ADMIN_DSN \
     | node mcp-server/bin/recovery-matrix-evaluate.mjs restore -
   ```

   This is the existing path that avoids production head/count reads (`tools/restore-watermark.py:852-896`). It checks loopback/socket access and rejects Neon provider settings, creates a new database, installs extensions, fetches the qualified copy, loads through pipes and compares its watermark (`:823-838,879-914`). It leaves the local recovered database available. A matching artifact's rows can be recovered here; role permissions and application operation are still separate gates. The command was not run against backup bytes in this audit.

5. Require a passing verifier/evaluator result and preserve the recovered database for controlled service restoration.
6. Complete authorization reconstruction, migration reconciliation and the explicit endpoint cutover from Scenario B before restoring service.

For an R2-only or historical artifact rejected by current workflow-identity checks, the wrapper's `--dump PATH` accepts ciphertext, but still requires production and has F6's filter defect. Current `tools/restore-watermark.py` exposes COPY-safe loading functions, but has no equivalent supported CLI for an arbitrary isolated target and R2 copy. **Do not invent a passing receipt or claim an unconditional fallback.** Implement Slice B's entrypoint, or perform a separately reviewed operator recovery using those retained functions and exact comparison. The lack of this ready path is part of the audit finding.

### D. Reconstruct structure only when no full backup can be recovered

1. Provision an empty isolated target and securely bind `DATABASE_URL` to its PostgreSQL connection URL.
2. Load the committed snapshot using the secret-safe database wrapper.

   ```sh
   .venv/bin/python tools/pg-env-exec.py DATABASE_URL \
     psql -X --single-transaction -v ON_ERROR_STOP=1 -f db/schema.sql
   ```

   This wrapper requires a `postgres://` or `postgresql://` URL; keyword-style libpq DSNs are not accepted (`tools/pg-env-exec.py:39-42`).

3. Apply the remaining migrations and check the exact audited prefix using Scenario B's migration commands.
4. Classify business records, history and excluded settings as unrecovered until a separate source restores them.

The snapshot generator emits schema, ledger and reviewed seed configuration; it excludes business tables and `system_config` (`bin/schema-snapshot.sh:1400,1663-1674,1755-1759,1789-1799,1821-1835`). This route reconstructs structure, not CARR's lost record layer. Never load the snapshot and full dump into the same target as an improvised merge.

### Paper-key proof after service recovery prerequisites exist

Run `./run.sh key-recovery-test --backup /secure/recovery/carr-20261023.sql.age` using the off-Mac copy and paper identity. The script reads the typed key silently, checks its derived recipient and invokes the rehearsal; it does not substitute for role bootstrap or account recovery. It can contact Neon and attempt an ops receipt, so this is not an offline audit command. A pass should record the specific surviving artifact and custody source, never the key. Source: `RECOVERY.md:116-131`; `bin/key-recovery-test.sh:154-209,229-269`.

## Ready-to-ship remediation slices

These are proposed follow-up scopes, not changes made by this report. Each supports the concrete product need to restore CARR records and Studio intake/export capability.

| Order | Slice / findings | Concrete deliverable | Acceptance / effort |
|---|---|---|---|
| 1 | **A — Correct recovery instructions**; F1, F8 | Replace deleted migration command; distinguish data recovery, structure reconstruction, security bootstrap and service cutover; remove “no unique data/no shared failure point” claims; inventory operator prerequisites and current limitations. | Every referenced executable exists; all examples parse; explicit `restore_rehearse` database binding; no unsupported success promise. **0.5–1 day**. |
| 2 | **B — Restore data and authorization without production**; F1, F2, F6 | One COPY-aware entrypoint for qualified GitHub or selected R2 ciphertext, fresh-target guard, exact watermark, reviewed role/ACL bootstrap, explicit cutover output. Reuse retained loader; do not resurrect retired release/proof machinery. | Fresh nonproduction PG18 restore with no source DB access; SQL-looking text preserved; allowed/denied role tests; no stale receipt reuse; real artifact and role bootstrap exercise. **3–5 days combined**. |
| 3 | **C — Recover Studio state and capability**; F3, F7 | Encrypted state allowlist archive and restore; independent vault/mailbox policy; corrected takeover inventory/paths; dependency/FDA steps; explicit role and scheduler activation. | Clean-Mac walkthrough, old pending mail beyond 48 hours recovered, outbound history reconciled, old primary fenced, one scheduler, fresh per-job results. **2–3 days plus 0.5–1 day hardware validation**. |
| 4 | **D — Keep recovery provable**; F4, F5, F8 | Paper-only artifact drill, independent escrow, truthful R2 status, off-Mac artifact-age alert, weekly off-Mac restore, current PITR evidence and timed exercise. | Detect missing/disabled schedules and archive refusal; demonstrate alert; demonstrate decryption from offline custody; record measured RPO/RTO and tested account dependencies. **2–3 days plus human custody/drill time**. |

## What was verified sound, and what was not run

- **Actual cloud production of ciphertext:** today's successful real dump/upload steps and nonexpired artifact metadata, with matching workflow source. Source enables run/attempt naming and 90-day retention (`.github/workflows/backup-nightly.yml:289-331`). This is stronger than merely finding a workflow file.
- **Guarded generation:** dedicated read-only `carr_backup`, no owner/BYPASSRLS escape, table/partition census, RLS coverage and sequence checks, exported snapshot held through verification, encryption/floor checks and atomic promotion (`bin/backup-guard.py:128-176,439-489`). A failed generation preserves prior acknowledged files. This control flow was inspected, not executed against production.
- **Exact content validation:** public and ops counts and row-content hashes are compared against the artifact itself (`bin/restore-rehearse.sh:1058-1101`). Cloud loading is COPY-aware, streamed, uses `psql -X ON_ERROR_STOP=1`, and redacts errors (`tools/restore-watermark.py:695-718,786-813`).
- **Isolation controls:** the wrapper creates a separate database, checks target host/default branch, and deletes only its returned branch ID (`bin/restore-rehearse.sh:779-839`). Paper-key entry uses silent input and restricted temporary storage with signal cleanup (`bin/key-recovery-test.sh:150-152,238-269`). These are useful controls, not a substitute for escrow verification.
- **Migration source consistency:** 588 active SQL files; 559 baseline identities through `0888_supersede_triaged_work_request.sql`; combined ledger 588 through `0940_codex_job_delegation_control_refs.sql`. Exact metadata/source checksum validation passed. The runner rejects checksum drift, unknown applied entries, reordered ledgers and incomplete strict atomic groups (`tools/migrate.py:602-652`). Baseline coverage does not invent applied rows (`tools/migration_baseline.py:100-105`).
- **Reconstruction coverage remains wired:** `ops/schema-reconstruction-selftest.py:48-95` tests snapshot loading, exact migration ledger and role-based board queries; current CI retains that test (`ops/ci.sh:576-581`). It was **not executed here**, because it opens the schema dump.
- **Safe offline checks passed:** individual `zsh -n` checks on backup, restore and key-test scripts; evaluator JavaScript syntax; one pure metering admission; 12 selected watermark/reader tests; six synthetic migration-baseline tests. The SQL-looking COPY-row defect was reproduced without opening a real dump.
- **Report checks passed:** 102 explicit source citation locations exist and have valid line numbers; nine shell blocks parse under Bash; staged secret and leak scans pass; `git diff --cached --check` and `ops/ci.sh --only freshness` pass. Independent source review corrected the Outlook role/build/activation order. These checks validate this report, not an operational recovery.
- **Not passing / not attempted:** the full recovery-matrix Node suite did not complete and was terminated, reporting 0 passed / 1 cancelled with a pending-Promise diagnostic. Full watermark tests were excluded because some stubs invoke `env`; key/record tests that open synthetic keys or credential files were excluded. Real decryption, complete snapshot loading, clean-cluster roles, application smoke tests, provider PITR, Mac replacement, R2 object readback and production cutover were not run. No complete strict-CI or disaster-recovery pass is claimed.

The report's design pass keeps one recovery interface around the existing loader and makes authorization/state restoration explicit. Its debt pass replaces obsolete instructions and duplicated loader behavior without proposing restoration of retired machinery. Both are recommendations; this PR changes only the audit report.
