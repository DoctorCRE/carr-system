---
name: restore-rehearse-weekly
description: Weekly proof that the encrypted backups actually restore: decrypts the newest dump into a throwaway Neon branch, reconciles row counts against production, reports PASS/FAIL, deletes the branch
---

Weekly backup restore rehearsal for Joe Bookout's CARR record layer. LOCAL Claude Code session on Joe's Mac.

WHY THIS EXISTS. Until 2026-08-02 nothing in the repo could restore a backup — a grep for `age -d`, `age --decrypt`, `pg_restore` and `psql ... backups/` returned zero hits across every language and every doc. The chain had been running nightly since 7/30 and had never been exercised. The first real run found the dumps would NOT restore: plain `pg_dump` embedded `ALTER DEFAULT PRIVILEGES` / `GRANT` / `OWNER TO` statements naming the source role, and the first one aborted the load. Three dumps existed and not one would have come back. An untested backup is a hope, and this job is what converts it to a fact every week.

RUN EXACTLY THIS, verbatim, one Bash call:

cd ~/carr-system && zsh -o pipefail -c './run.sh restore-rehearse 2>&1 | .venv/bin/python -m lib.restore_rehearsal_report'

That is the whole job. Behaviour lives in `bin/restore-rehearse.sh`, never in this prompt — if something needs to change, change the script (thin-prompt law). Do NOT rewrite, paraphrase or "improve" the command; do not add flags.

DO NOT ADD THE KEY PATH BACK. This prompt used to prescribe `CARR_AGE_IDENTITY=~/.config/carr/age-key.txt ./run.sh restore-rehearse`, and that command could never run: `hooks/guard-unattended.py` rule 3 matches `age-key` as private key material and denies the Bash call before it starts. The env var was redundant anyway — `bin/restore-rehearse.sh` line 72 defaults the identity to that exact same path. The script resolves its own secret; the command names no key. Caught 2026-08-04, the first session that tried to run it. The `--identity` and `CARR_AGE_IDENTITY` overrides stay in the script for a human whose key lives somewhere else; they do not belong in automation.

WHAT IT DOES, so you can read the output rather than trust it: preflight (tools, key, dump age, Neon reachability), reads production row counts in a `default_transaction_read_only=on` session, creates a timestamped throwaway branch, asserts the branch endpoint is a DIFFERENT host from production, creates a fresh database on it, decrypts the newest `backups/*.sql.age` straight into psql, reconciles all ~67 tables against production, then deletes the branch from a trap. It contains no DROP and no TRUNCATE. It never touches production with anything but a read.

Report the parser's JSON verdict verbatim. PASS requires exactly one complete
PASS marker followed by exactly one confirmed branch deletion. Missing,
conflicting, failed, or incomplete output produces FAIL. The parser handles
expected snapshot drift without model interpretation and prints no backup data.
If the pipeline exits nonzero, report failure even if its JSON says PASS.
On failure, stop without retrying, decrypting anything yourself, or editing a dump.
