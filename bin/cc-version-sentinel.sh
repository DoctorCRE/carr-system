#!/bin/zsh
# cc-version-sentinel.sh — the FREE half of the Claude Code update audit (loop #177).
#
# WHY THIS EXISTS. The cc-update-audit scheduled task fired five times a week and,
# by its own description, "exits in seconds on no-change days" — which is most days.
# But a session that exits in seconds still cost a full session boot: the rule store,
# the standing context, the whole opening act, five times a week, to compare two
# strings. The cost model this system runs on (loop #177) is that a script costs
# nothing and a session costs tokens, so anything mechanical belongs in a script and
# sessions are reserved for judgment.
#
# THE SPLIT. The GATE is mechanical — read two version strings, compare to a marker.
# That is this file, run by launchd for free. The AUDIT is genuine judgment — read the
# real changelog, work out what each release changes for CARR, delegate to the IT
# Support lane — and that stays a session.
#
# WHAT THIS DOES WHEN THE VERSION MOVES. It notifies Joe within the hour (the same
# macOS notification shape pipelines/partner_ping.py uses — the worked example loop
# #177 cites by name), and it writes a marker the audit session reads. It deliberately
# does NOT advance last-audited-version.txt: only a completed audit may do that, or a
# notification would silently consume the very change it was reporting.
#
# TWO BINARIES, BOTH WATCHED, and this is not optional. The PATH binary
# (/opt/homebrew/bin/claude, npm-global, self-updating) is what `claude --version`
# reports. The desktop app ships its OWN runtime under
# ~/Library/Application Support/Claude/claude-code/<version>/ and THAT is what
# actually executes Joe's sessions; on 2026-08-09 they sat at 2.1.226 and 2.1.222.
# Watching only the PATH binary blinds the audit to the runtime that matters. The
# string is built by bin/cc-version-string.sh, the one implementation the audit
# task's own STEP 0 also calls (rule a8c55a47: a gate and its script disagreeing
# about what "changed" means is worse than having no gate).
#
# RISK COLOR: GREEN. It reads two version strings, writes two files under the task's
# own directory and one log line, and raises a local notification. It calls no verb,
# touches no record, reaches no network, and sends nothing off this Mac.
#
# Usage: ./bin/cc-version-sentinel.sh [--dry-run]

set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
TASK_DIR="$HOME/.claude/scheduled-tasks/cc-update-audit"
SENTINEL="$TASK_DIR/last-audited-version.txt"
PENDING="$TASK_DIR/pending-version.txt"
LOG="$REPO/out/cc-version-sentinel.log"
NOTIFY_COMMAND="${CARR_CC_VERSION_NOTIFY_COMMAND:-/usr/bin/osascript}"
LOG="${CARR_CC_VERSION_SENTINEL_LOG:-$LOG}"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

mkdir -p "$REPO/out"
say() { print -r -- "$(date -u +%FT%TZ) cc-version-sentinel $*" >> "$LOG" }

# The audit task's directory is the marker's home on purpose: the file that says
# "an audit is owed" belongs beside the file that says "an audit was done", so a
# reader never has to know two places to answer one question.
if [ ! -d "$TASK_DIR" ]; then
  say "SKIP task dir missing: $TASK_DIR (audit task removed?)"
  exit 0
fi

# A HELPER FAILURE MUST SURFACE AS A VISIBLE FAIL, NOT AS `set -eu` silently
# aborting the script mid-run: this file is invoked via bin/run-scheduled.sh
# specifically so its exit code is durably recorded, and an abort with no say()
# line first would defeat that. || is enough to keep `set -eu` from firing
# before the say/exit pair runs.
CUR="$("$REPO/bin/cc-version-string.sh")" || { say "FAIL version helper failed (exit $?)"; exit 1; }

# The read-failure guards below need CLI and APP separately, not just the
# combined string the helper prints.
CLI="${CUR#cli=}"; CLI="${CLI%% *}"
APP="${CUR#*app=}"
LAST="$(cat "$SENTINEL" 2>/dev/null || echo "none")"

# A READ FAILURE IS NOT A VERSION CHANGE, and conflating the two is what produced
# this agent's first-run false alarm (see bin/cc-version-string.sh's header). If a
# component reads `none` now but was a real version at the last audit, the binary
# did not vanish — this script failed to see it. Report that, change nothing, and
# let the next hour retry. A binary that reads `none` on BOTH sides is a genuine
# not-installed state and compares normally.
if [ "${CLI:-none}" = "none" ] && [ -n "${LAST##*cli=none*}" ] && [ "$LAST" != "none" ]; then
  say "FAIL cli unreadable (PATH?) while last_audited names a version — no comparison, marker untouched"
  exit 1
fi
if [ "${APP:-none}" = "none" ] && [ -n "${LAST##*app=none*}" ] && [ "$LAST" != "none" ]; then
  say "FAIL app runtime unreadable while last_audited names a version — no comparison, marker untouched"
  exit 1
fi

if [ "$CUR" = "$LAST" ]; then
  # Versions converged back to the audited state (an audit ran, or the change
  # rolled back) while a pending marker from before that convergence was still
  # sitting on disk. Left alone it would report an update as still owed after
  # it no longer is, so clear it here — the one place that already knows CUR
  # equals the audited version.
  if [ -f "$PENDING" ]; then
    rm -f "$PENDING"
    say "OK cleared stale pending marker — current matches last_audited ($CUR)"
  fi
  say "OK no change ($CUR)"
  exit 0
fi

say "CHANGE current[$CUR] last_audited[$LAST]"

# Latch notifications to one per distinct (current, last-audited) pair.  The
# sentinel runs hourly and deliberately leaves last-audited-version.txt alone
# until the audit completes; without this guard one still-pending update creates
# a notification storm.  Preserve the first marker's detected_at so the audit
# age remains truthful.  A different pair replaces the marker and notifies.
if [ -f "$PENDING" ]; then
  PENDING_CUR="$(awk -F': ' '/^current: /{print $2; exit}' "$PENDING")"
  PENDING_LAST="$(awk -F': ' '/^last_audited: /{print $2; exit}' "$PENDING")"
  if [ "$PENDING_CUR" = "$CUR" ] && [ "$PENDING_LAST" = "$LAST" ]; then
    PENDING_AT="$(awk -F': ' '/^detected_at: /{print $2; exit}' "$PENDING")"
    say "OK already notified for this change (marker since ${PENDING_AT:-unknown}) — staying quiet"
    exit 0
  fi
fi

if [ "$DRY" -eq 1 ]; then
  print -r -- "would notify: Claude Code changed — $CUR (last audited: $LAST)"
  exit 0
fi

# The marker carries BOTH strings, because the audit's scope is the range between
# them and re-deriving `last_audited` after the fact is how a gap gets missed.
print -r -- "current: $CUR
last_audited: $LAST
detected_at: $(date -u +%FT%TZ)" > "$PENDING"

TITLE="Claude Code updated"
SUBTITLE="$CUR"
MESSAGE="Last audited: $LAST — the update audit is owed. It runs Monday, or ask for it now."
"$NOTIFY_COMMAND" -e "display notification \"$MESSAGE\" with title \"$TITLE\" subtitle \"$SUBTITLE\"" \
  >/dev/null 2>&1 || say "WARN notification failed ($NOTIFY_COMMAND)"

say "OK marker written, Joe notified"
