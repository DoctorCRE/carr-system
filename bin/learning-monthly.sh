#!/bin/zsh
# learning-monthly.sh — ORDER 15's monthly pair, ONE byte-stable command.
#
# Rides the EXISTING monthly review (no new scheduler — the order's stop rule).
# Two jobs, both reading the rule store, neither writing anything anywhere:
#   1. promotion review   — active rules vs promotion.min_repeat_violations
#   2. conflict surfacing — rules that contradict
#
# "N active rules, 0 repeat violations, nothing to promote" is a PASS, not an
# empty result. Neither job changes a rule's enforcement, retires a rule, or
# resolves a conflict; promotion and resolution are human rulings and stay that
# way. These two produce reading material for the monthly review, nothing more.
#
# Appends to out/learning.log. Verified by OUTPUT: the two report files under
# out/Learning/ and their first lines.
#
# Run by hand any time: ./bin/learning-monthly.sh
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/opt/homebrew/opt/libpq/bin:/usr/local/bin:/usr/bin:/bin"
LOG="$REPO/out/learning.log"
# THE CUTOFF, 2026-08-19 — same change and same reasoning as bin/learning-weekly.sh:
# these reports are renderings of database content, not a home for it, so the
# vault copy retired with the 37 doctrine renders. The repo copy stays.
LEARN_DIR="$REPO/out/Learning"
mkdir -p "$REPO/out" "$LEARN_DIR"
# Reports are canonical repo-local output.  Do not let an inherited Drive root
# leak into either child clause merely because a caller still has one mounted.
unset CARR_VAULT

. "$REPO/bin/routine-credential-env.sh"
carr_require_sourceable_db_env "learning-monthly" || exit $?
[ -f "$HOME/.config/carr/db.env" ] && { set -a; . "$HOME/.config/carr/db.env"; set +a; }
jobs_url="${CARR_DB_JOBS_URL:-}"
unset DATABASE_URL CARR_DB_WRITER_URL CARR_DB_OWNER_URL CARR_DB_CADENCE_URL CARR_IMPORT_DB_URL
if [ -z "$jobs_url" ]; then
  print -ru2 -- "learning-monthly: CARR_DB_JOBS_URL is required; refusing writer/owner fallback"
  exit 78
fi
export CARR_DB_JOBS_URL="$jobs_url"

say() { print -r -- "$(date -u '+%Y-%m-%dT%H:%M:%SZ')  $*" >> "$LOG"; }

say "===== learning monthly pair begin ====="
cd "$REPO" || { say "FATAL cannot cd $REPO"; exit 2; }

rc=0
./.venv/bin/python pipelines/learning_jobs.py monthly-chain \
  --report-dir "$LEARN_DIR" >> "$LOG" 2>&1 || rc=$?

# Evidence stays outside the checkout; the log reports collection and review gaps.
./.venv/bin/python ops/corrections-sweep.py \
  >> "$LOG" 2>&1 \
  && say "correction corpus collected privately — coverage and review gaps are in $LOG" \
  || say "corrections sweep did not complete — see $LOG (chain continues)"

# 3 = at least one clause read a tier that could not answer it and SAID SO in
# its report. That is the honest-degradation path, not a failure.
if [ "$rc" -eq 0 ]; then
  say "===== learning monthly pair OK ====="
elif [ "$rc" -eq 3 ]; then
  say "===== learning monthly pair OK (one or more clauses UNAVAILABLE under the read tier — see the reports) ====="
  rc=0
else
  say "===== learning monthly pair FAILED (exit $rc) ====="
fi

tail -n 2000 "$LOG" > "$LOG.trim" && mv "$LOG.trim" "$LOG"
exit "$rc"
