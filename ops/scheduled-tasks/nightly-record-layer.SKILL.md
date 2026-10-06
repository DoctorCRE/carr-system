---
name: nightly-record-layer
description: Nightly record-layer VERIFIER (7 days, 2:30am CT, after the launchd chain at 2:05). launchd com.carr.nightly-record-layer is the executor; this task reads the chain's terminal line, verifies OUTPUT freshness via run.sh health, reports amber rows, and runs the chain itself only as failover when launchd did not fire.
---

RULE-DELIVERY WORKFLOW: nightly-record-layer
RULE-DELIVERY PACKS: scheduled-automation

Before any other read, command, or report, call `standing-context` with exactly
`{"packs":["scheduled-automation"]}`. Recite the returned shared and personal
rule counts plus `rule_delivery.mode`, `rule_delivery.declared_packs`, and
`rule_delivery.would_omit_count`. Refuse the run before executing the chain if
the selector result is absent, if `packs_not_found` is non-empty, or if the
declared pack is anything other than the exact canonical name
`scheduled-automation`. The alias `automation` is unknown and loads nothing.

STORE-FIRST (added 2026-08-09, loop #289): the doctrine STORE is the source of truth for every governing doc named below. Before reading any `.md` path in the vault, try `read-doctrine` with that file's stem as the document slug; if a store doc exists, IT WINS and the vault file may be a stale duplicate. Two such duplicates were found on 2026-08-09 and this routine's sibling had been reading a three-week-old SOP because its pointer named the file instead of the slug. Do not edit the vault copy either way: hand-authored vault markdown is closed by record-home-gate.py (rule 14181e60).

FIRST, ESTABLISH WHETHER THE CHAIN ALREADY RAN TONIGHT (added 2026-08-31). This task is NOT the chain's executor. The launchd job `com.carr.nightly-record-layer` runs `bin/nightly.sh` at 02:05 and is the registered executor — it is the only nightly surface the control plane models (`ops/config/control-plane-scheduler-cutover.v1.json`). This task is the reporting half, which launchd cannot do.

Why this gate exists, measured on 2026-08-31: this task used to invoke the chain unconditionally. `bin/nightly.sh` takes a lock, so the second invocation is a deliberate no-op THAT EXITS 0 — the task read `direct script exit=0`, concluded the chain had run, and then health-checked a tree the launchd chain was still writing. That night launchd began 07:05:01Z and signed off 07:12:51Z; this task fired at 07:06:10Z, sixty-nine seconds in, with the consumer rebuilds still seven minutes away. Every board row it read was BEHIND, and the file below calls a BEHIND board row a real finding. The alarm was manufactured by the ordering, not by the system.

Read the terminal line for tonight's run and branch on it:

cd ~/carr-system && tail -400 out/nightly.log | grep -E "===== nightly chain (begin|OK|FINISHED)" | tail -5

  * A `nightly chain OK` or `nightly chain FINISHED WITH FAILURES` line dated tonight — the chain is COMPLETE. Do not invoke it. Skip straight to the verification section below, and report that line's verdict as the chain result.
  * A `nightly chain begin` dated tonight with no terminal line after it — the chain is STILL RUNNING. Do not invoke it; a second invocation is a no-op that will mislead you exactly as described above. Wait and re-read the same command until a terminal line appears, then verify. The chain has been taking about eight minutes; if twenty-five minutes pass with no terminal line, stop waiting and report the chain as STALLED, naming the last `START` line in the log as where it hung.
  * No `nightly chain begin` at all tonight — LAUNCHD DID NOT FIRE. This is the one case where this task runs the chain itself, as failover. Run the command below, then verify. Report the launchd miss prominently: a missed native run is a finding in its own right, separate from whatever the chain then reports, and `ops/launchd-plist-parity.py` plus the health check's launchd rows are where its cause will be.

IF AND ONLY IF the third branch applies — launchd did not fire tonight — run the chain yourself. Execute EXACTLY this via Bash, VERBATIM, character for character — do not paraphrase it, do not add flags, do not substitute paths, do not re-quote it. Permission approval matches the exact command string, and the string below is the one carrying a persisted approval (Joe, 2026-07-31); any rewording can hit a permission prompt at 2am with nobody awake to answer it, which is exactly how the first scheduled run produced nothing:

cd ~/carr-system && ./bin/nightly.sh >/dev/null 2>&1; echo "direct script exit=$?"

Do NOT invoke it with `bash <script>`: the script is `#!/bin/zsh` (its logging helper uses zsh's `print` builtin; under bash every log line dies with `print: command not found` — the steps still run but the timestamped `chain begin` / `START` / `OK` / `FAIL` lines never reach out/nightly.log, and a broken step leaves no FAIL line to find). Running it as `./bin/nightly.sh` uses the shebang and is correct. Fixed 2026-07-31 after the first scheduled run proved it.

That script does six things in order: (1) runs the cadence engine, which spawns the next_action rows the cadence rules are owed, (2) runs the availability matcher, which writes a digest of availability-vs-space-search matches and sends nothing to anyone, (3) exports all seven generated files LIVE to the vault, (4) rebuilds the consumer boards (`./run.sh all`: renewal-feed, lead-board, deal-room), (5) rebuilds the Graph from the exported files, (6) takes the encrypted pg_dump backup and commits it to git. Steps 1 and 2 come first because they WRITE, and a write that lands after the export it belongs in sits invisible for a whole day. It logs to {{REPO}}/out/nightly.log and exits non-zero if any step failed.

THE COMMAND ABOVE DID NOT CHANGE WHEN THOSE TWO STEPS WERE ADDED (2026-07-31, ORDER 14), AND IT MUST NOT. The script grows steps; the command string stays byte-identical, because the persisted permission approval matches the string and a new string is a new approval prompt at 2am. Anything the chain gains in future goes inside bin/nightly.sh, never into this file's command.

A step logging `SKIP … (exit 78 — not configured)` is NOT a failure and does not need reporting as one: it means that step ran, found a credential or setting it needs is absent, wrote nothing, and said which one in the line above it. The cadence engine is expected to SKIP until a write-capable database credential exists in ~/.config/carr/db.env; the matcher likewise. Report a SKIP as one line of context, never as a 🔔.

THEN VERIFY THE OUTPUT, NOT THE EXIT CODE (protocol rule 28: an automation is verified by output freshness, never by its schedule existing, and never by its own claim of success). Run EXACTLY, same verbatim rule:

cd ~/carr-system && ./run.sh health

Every row beginning "GEN " must read OK. Those seven rows are the generated files on a 26-hour cadence; a STALE one means the export step did not actually reach the vault regardless of what the exit code said.

ALSO REPORT THE AMBER ROWS, not only the GEN ones (added 2026-08-10). Until today this routine ran the full health check and was told to read seven rows out of forty-odd, so every other finding was printed and discarded. That is how the mypy tripwire stayed red from 08-08 to 08-10 with nobody told, and how nine finished scheduled-task edits sat uncommitted for thirteen hours until a human happened to read a row carefully. A check nobody reports on is a check nobody is running.

Report every `⚠︎` row EXCEPT the Deprecation register block, which is permanently amber by design (it lists shims kept alive on purpose and prompts rather than enforces). Reporting those nightly would train the reader to skip the whole section, which is the disease, not the cure. Two rows deserve naming in particular because they are new and they are about work in flight:

  * `nightly chain result` — whether the LAST run exited clean, and how many runs in a row have been red. A first failure and a week-long failure need different responses and the row says which it is.
  * `uncommitted work` — tracked files sitting outside git, with the oldest one's age. Past twelve hours it means work crossed a night unlanded; name the paths so whoever wrote them can land them by NAMING PATHS, never a sweep, because this machine runs several sessions against one working tree.

Keep it to one line per amber row. If nothing is amber outside the deprecation register, say so in four words and stop.

REPORT:
- If the chain exited 0 and all seven GEN rows are OK: report success in one line with the row counts from the log, then the amber rows per the section above. Nothing else needed.
- If anything failed: quote the actual FAIL line from out/nightly.log and the STALE/BEHIND rows from the health check. Do not retry more than once. Do NOT modify the script, the exporters, or any generated file, and do NOT hand-edit a vault file to make a check pass — the seven generated files are never hand-edited, ever. If the Worker or Neon is unreachable, say so plainly and stop; the runbook is DNA/Deal Management/record-layer/runbook.md.

(Historical note, resolved: the chain originally omitted the consumer rebuilds, leaving Lead Board / Deal Room reading BEHIND each morning. The ORDER 2 ADDENDUM added `./run.sh all` to the chain on 2026-07-31, so ALL 20 health rows — GEN files and boards alike — should now read OK after a good run. A BEHIND board row is therefore a real finding again, not an expected artifact.)

Context, not to be re-litigated: this replaced manual-only exports on 2026-07-31. It runs 7 days a week because the record layer has no weekend stand-down — the files must be true whenever either partner opens them, and every Cowork and phone session still reads them. (Corrected 2026-08-04: this sentence used to end "and Dell works off the generated files rather than the MCP verbs." Dell is on the connector now, per Joe. The line outlived the fact by days and was still being cited as live evidence for Dell's side in ORDER 28's inventory — where it was the whole basis for classifying his row UNKNOWN. The chain's 7-day rationale does not depend on it.) If the Mac was asleep at 2am the task fires on wake; that is normal for this system and not a failure.

DAYTIME SAFETY NET FOR THE EXPORTS STEP (added 2026-09-24, doctrine `nightly-exports-onedrive-wake`). The exports step failed 9/22-9/24 on the OneDrive File Provider idling overnight. `bin/nightly.sh` now runs that step under `caffeinate -i -s` behind a bounded pre-publish wake, and a separate launchd job, `com.carr.nightly-exports-daytime-retry` (`bin/nightly-exports-retry.sh`), fires once daily at 11:00 local and re-runs `./run.sh export` ONLY if tonight's own archived nightly log did not report the exports step OK — a no-op on a healthy night. If this routine's verification finds the exports GEN rows STALE during the day, that job is the reason not to hand-run `./run.sh export` yourself first: check `out/nightly.log` for a `RETRY` line before assuming nobody is already on it. It is not installed by default; install it by hand:
```
mkdir -p ~/Library/LaunchAgents \
  && sed "s|{{REPO}}|$(pwd)|g" ops/launchd/com.carr.nightly-exports-daytime-retry.plist > /tmp/com.carr.nightly-exports-daytime-retry.plist \
  && plutil -lint /tmp/com.carr.nightly-exports-daytime-retry.plist \
  && install -m 644 /tmp/com.carr.nightly-exports-daytime-retry.plist ~/Library/LaunchAgents/com.carr.nightly-exports-daytime-retry.plist \
  && { launchctl bootout gui/$(id -u)/com.carr.nightly-exports-daytime-retry 2>/dev/null || true; } \
  && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.carr.nightly-exports-daytime-retry.plist \
  && launchctl print gui/$(id -u)/com.carr.nightly-exports-daytime-retry
```
(the `bootout` is wrapped in its own `{ ... || true; }` — it fails, harmlessly, the first time the job is installed, when nothing is bootstrapped yet to boot out — but that group is still joined to the rest with `&&`, so a real failure anywhere else in the chain (a bad plist, a failed `install`) stops the sequence before `bootstrap` ever runs. A bare `;` there would let `bootstrap` run even after `plutil -lint` or `install` failed.)

REGISTERING THE NEW SERVICE ROW is a **separate, human, owner-credential step**, not part of the install above and not automatable from this checkout. `tools/ops-record.py sync-registry` needs `DATABASE_URL` (the owner role) and applies `ops/config/services.json` verbatim — including RETIRING every live service missing from whatever checkout it's run from, so it must run from an up-to-date `main` checkout after this PR has merged, never from a branch worktree. It also must NOT be run through `tools/db-tap.py`: db-tap's "READ-ONLY BY DEFAULT" hardening (2026-08-13) puts every ordinary `run`/`sql` invocation behind `default_transaction_read_only=on` unless `CARR_BREAK_GLASS=1` is set with a `--reason` — sync-registry is routine maintenance, not a break-glass event, and a break-glass wrapper here would just be a workaround for the wrong credential, not a fix. This is the standing doctrine for every service-catalog declaration, not new here: commit `4fb58a8a` ends "the 8 new declarations need `ops-record sync-registry` to reach the database" as the step still owed after merge, and commit `5c2a53ec` is explicit that "the database is the RENDER of this file and does not update itself: nothing runs sync-registry on a schedule, so the row reaches `ops.service` only when someone runs it. That step is owed after this merges and is not done by this commit." (Commit `0693406d`, which added `com.carr.calendar-prebrief-joe`, only shows that `sync-registry` runs correctly with a bare `DATABASE_URL` and no `db-tap.py` wrapper — via its own `ops/calendar-prebrief-service-registration-gate.py`, exercised against a disposable migration database in CI, not a record of Joe personally running the command against production.) So: whoever holds `DATABASE_URL`, from an up-to-date main checkout, runs

```
.venv/bin/python tools/ops-record.py sync-registry
```

directly (no `db-tap.py` wrapper). This gives the new service its `ops.service`/`ops.service_environment` row so `tools/ops-record.py health` can read it.