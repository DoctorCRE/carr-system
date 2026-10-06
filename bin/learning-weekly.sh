#!/bin/zsh
# learning-weekly.sh — ORDER 15's weekly chain, ONE byte-stable command.
#
# The scheduled task's whole job is to run this line and read the summary. That
# shape is the ghost-run lesson made mechanical (ORDER 8, 2026-07-31): a task
# whose command string changes hits a fresh permission prompt at an hour when
# nobody is awake, and a task that issues several commands has several places to
# stall. One command, character-stable, forever. The script grows steps; the
# approval surface does not.
#
# Three steps, in order — the ordering is not cosmetic:
#   1. placements + metrics  (Blotato -> placement / placement_metric)
#   2. weekly learning       (reads what step 1 just wrote)
#   3. correction miner      (reads the event spine)
# Step 2 reading step 1's rows in the same run is the point; a metrics pull that
# lands after the job that consumes it is a week of lag for no reason.
#
# Every step runs even if an earlier one failed, and the exit code reports the
# worst outcome — except 78 (EX_CONFIG, "a credential this step needs is absent,
# nothing was written, and it said so"), which is a SKIP and not a failure. Same
# convention as bin/nightly.sh, and for the same reason: an alarm that fires
# every week until a credential lands trains people to stop reading alarms.
#
# Appends to out/learning.log. Verified by OUTPUT — the four report files under
# out/Learning/ and their first lines — never by this script existing and never
# by its own claim of success (protocol rule 28).
#
# Run by hand any time: ./bin/learning-weekly.sh
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/opt/homebrew/opt/libpq/bin:/usr/local/bin:/usr/bin:/bin"
LOG="$REPO/out/learning.log"
# THE CUTOFF, 2026-08-19. This used to write its reports into the vault at
# Automation/Learning/ as well as here. Both clauses are PURE READERS of the
# event spine and the metric tables — job_corrections says so in its own output,
# "it proposes nothing and writes nothing" — so the vault copy was a rendering of
# database content, never a home for it, and the same reasoning that retired the
# 37 doctrine renders applies unchanged. The repo copy stays because the report
# is regenerable on demand from rows that never left the database.
LEARN_DIR="$REPO/out/Learning"
mkdir -p "$REPO/out" "$LEARN_DIR"
# Reports are canonical repo-local output.  Do not let an inherited Drive root
# leak into either child clause merely because a caller still has one mounted.
unset CARR_VAULT

# Credentials, both from files on disk, never inlined here.
#  · db.env carries the least-privilege exporter URL the read-only jobs ride.
#  · BLOTATO_API_KEY has lived in ~/.zprofile since the social-media-manager
#    skill was built; this order introduces no new secret.
. "$REPO/bin/routine-credential-env.sh"
carr_require_sourceable_db_env "learning-weekly" || exit $?
[ -f "$HOME/.config/carr/db.env" ] && { set -a; . "$HOME/.config/carr/db.env"; set +a; }
[ -f "$HOME/.zprofile" ] && . "$HOME/.zprofile" >/dev/null 2>&1

# The metrics pull writes the three narrow tables granted to carr_jobs.  Routine
# work therefore has exactly one database credential path.  Clear every legacy
# and ambient fallback before spawning children: a writer URL in db.env must not
# become authority merely because this wrapper happened to source that file.
jobs_url="${CARR_DB_JOBS_URL:-}"
unset DATABASE_URL CARR_DB_WRITER_URL CARR_DB_OWNER_URL CARR_DB_CADENCE_URL CARR_IMPORT_DB_URL
if [ -z "$jobs_url" ]; then
  print -ru2 -- "learning-weekly: CARR_DB_JOBS_URL is required; refusing writer/owner fallback"
  exit 78
fi
export CARR_DB_JOBS_URL="$jobs_url"

say() { print -r -- "$(date -u '+%Y-%m-%dT%H:%M:%SZ')  $*" >> "$LOG"; }

rc_total=0
step() {                        # step <label> <command...>
  local label="$1"; shift
  say "START $label"
  if "$@" >> "$LOG" 2>&1; then
    say "OK    $label"
  else
    local rc=$?
    if [ "$rc" -eq 78 ]; then
      say "SKIP  $label (exit 78 — not configured; the step's own message is above)"
    elif [ "$rc" -eq 3 ]; then
      # 3 = at least one clause read a tier that could not answer it and SAID SO
      # in its report. Honest degradation, not a failure: the job ran, produced
      # its report, and named what it could not see.
      say "OK    $label (one or more clauses UNAVAILABLE under the read tier — see the reports)"
    else
      say "FAIL  $label (exit $rc)"
      rc_total=1
    fi
  fi
}

say "===== learning weekly chain begin ====="
cd "$REPO" || { say "FATAL cannot cd $REPO"; exit 2; }

# CARR_DB_JOBS_URL is required above, so an admitted weekly run always takes
# the registered write path and never degrades by borrowing a writer credential.
step "placements + metrics (Blotato -> records)" \
  ./.venv/bin/python pipelines/pull_placement_metrics.py --apply --report-dir "$LEARN_DIR"

step "weekly learning + correction miner" \
  ./.venv/bin/python pipelines/learning_jobs.py weekly-chain \
    --report-dir "$LEARN_DIR"

if [ "$rc_total" -eq 0 ]; then
  say "===== learning weekly chain OK ====="
else
  say "===== learning weekly chain FINISHED WITH FAILURES — see above ====="
fi

tail -n 2000 "$LOG" > "$LOG.trim" && mv "$LOG.trim" "$LOG"
exit "$rc_total"
