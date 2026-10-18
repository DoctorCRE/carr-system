#!/bin/zsh
# refresh-rules.sh — close the same-day taught-rules gap (Joe, 2026-07-31:
# "i dont love that it lags over night").
#
# The compiled-rules files are the BINDING auto-loaded surface; a rule taught
# from Cowork/phone mid-day sat in the DB until the 2am chain re-rendered them.
# This job re-renders JUST those two files HOURLY, 7:00-20:00 local (launchd
# StartCalendarInterval) — business hours only, on Joe's cost ruling 2026-07-31:
# Neon free tier = 100 CU-h/month, every wake burns ~5 min of compute, and
# capping out SUSPENDS the DB until the next cycle. Overnight wakes would spend
# real budget re-rendering rules nobody teaches at 3am; the 2am chain stays the
# night's only DB visitor. Pure code, no model (T0 per model-tiering).
#
# SKIP-not-FAIL: missing env = exit 0 with a SKIP line, per house convention.
#
# EXIT CODE HONESTY (Program 4 follow-up). This script used to end via the
# `tail | mv` log-trim below, so its own exit code was always whatever THAT
# returned — 0, regardless of a FAIL logged above it. bin/run-scheduled.sh now
# wraps this job and records succeeded/failed off the real exit code, so a
# silent-0 exit meant a night this export actually failed would still be
# recorded as succeeded. EXIT_CODE carries the real outcome to the bottom,
# where the script now exits with it explicitly.
REPO="$HOME/carr-system"
LOG="$REPO/out/rules-refresh.log"
[ -f "$HOME/.config/carr/db.env" ] || { echo "$(date -u +%FT%TZ) SKIP no db.env" >> "$LOG"; exit 0; }
cd "$REPO" || exit 1

EXIT_CODE=0

# TEST HOOK ONLY — never set by launchd or a real "Run Now". Lets
# ops/run-scheduled-selftest.py substitute a stub for the real
# network/DB-touching export below, so the exit-code fix is provable without
# a live refresh ever running. The real invocation is untouched when unset.
if [ -n "${CARR_REFRESH_RULES_EXPORT_CMD:-}" ]; then
  test_cmd=(${(z)CARR_REFRESH_RULES_EXPORT_CMD})
  run_export() { "${test_cmd[@]}"; }
else
  run_export() { CARR_EXPORT_LIVE=1 ./run.sh export --only compiled-rules; }
fi

if run_export >> "$LOG" 2>&1; then
  echo "$(date -u +%FT%TZ) OK rules refreshed" >> "$LOG"
  # Activating a rule updates the renders above but NOT the enforcement map,
  # whose inventory must match the render order exactly. That gap fired twice
  # in two days (2026-08-12, 2026-08-13) and each time every session booting
  # afterwards was told the gates were not in force. The map inventory is
  # derived data, so it is synced by code here, right where the renders move.
  # It re-stamps only the map's own contract hash — never the gate-script
  # hashes, so tampering with a gate is still detected on the next boot.
  # Assert no tool-call markup reached a rule. Rule c53beeaa has prescribed this
  # exact check since 2026-08-03 and nothing ran it; on 2026-08-13 six active
  # shared rules were found carrying it, five with Joe's verbatim quote absorbed
  # into the statement and human_quote written NULL, the oldest sitting there
  # four days. Runs here because this is where the renders are produced, and a
  # check nobody runs is not a check.
  if ! python3 "$REPO/ops/rule-render-markup-check.py" >> "$LOG" 2>&1; then
    echo "$(date -u +%FT%TZ) FAIL tool-call markup found in a rendered rule" >> "$LOG"
  fi
  # Same class of gap as the enforcement map above, same remedy, same place.
  # vault-drift-watch's tamper baseline is refreshed by the NIGHTLY chain, but
  # these three renders move HOURLY here — and they carry an `Exported:`
  # timestamp, so their bytes change on every pass even when no rule changed.
  # The two beats disagreed, so the drift check reported all three as TAMPER
  # ("rewritten outside the nightly export") every day; run 20260813T201608Z is
  # the measured case. The baseline for these paths is derived data, so it is
  # moved by code here, right where the renders move. It merges, so a genuine
  # out-of-band rewrite of any OTHER registered file is still caught tonight.
  #
  # --only-target takes the SAME selector as the export above and resolves it
  # through exporters/targets.py. The first cut hardcoded the three render paths
  # and was wrong immediately: `--only compiled-rules` also matches
  # compiled-rules-gist-index (CLAUDE.md) and compiled-rules-intro
  # (DNA/Network/introduction-rules.md), so the job rewrote five files, the
  # rebaseline covered three, and the next check called the other two TAMPER.
  # One selector, one registry, no hand-kept list to drift.
  # The drift watch is gated behind explicit recovery (Phase 4, 2026-08-22):
  # it reads the legacy vault and has no canonical mode, so it refuses unless
  # the caller says why. This IS an acknowledged legacy-render touch -- it
  # rebaselines the compiled-rules renders this job just rewrote -- so the
  # reason is fixed here rather than left to whoever runs the refresh.
  "$REPO/.venv/bin/python" "$REPO/ops/vault-drift-watch.py" \
    --recovery --reason "compiled-rules render rebaseline (bin/refresh-rules.sh)" \
    --rebaseline --only-target compiled-rules >> "$LOG" 2>&1 || true
else
  rc=$?  # capture BEFORE the date subshell resets $? — the old line always logged rc=0
  echo "$(date -u +%FT%TZ) FAIL rules refresh rc=$rc" >> "$LOG"
  EXIT_CODE=$rc
fi
# keep the log from growing forever: trim to the last 500 lines
tail -n 500 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
exit $EXIT_CODE
