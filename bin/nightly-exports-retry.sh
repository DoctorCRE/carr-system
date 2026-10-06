#!/bin/zsh
# nightly-exports-retry.sh — daytime safety net for the nightly "exports (6
# targets -> OneDrive)" step.
#
# DEFECT THIS ANSWERS. The nightly export failed 9/22, 9/23 and 9/24 around
# 07:05 UTC (~2am local); on 9/24 it hung the full 1800s step wall-clock (see
# out/nightly-runs/nightly-20260924T080221Z.log). A by-hand run the same day
# at ~4pm (`env CARR_EXPORT_LIVE=1 ./run.sh export`) published all 6 targets
# in 18s. Jev at 0.71: the OneDrive File Provider idling overnight. The
# nightly chain now also runs the exports step under `caffeinate -i -s` and
# ahead of a bounded pre-publish wake (ops/onedrive-prepublish-wake.py) — see
# bin/nightly.sh — but the 2am window is inherently the worst time to reach a
# provider that only wakes on real activity. Working hours are the proven
# fallback: re-run the export once, during the day, ONLY if the night's own
# export did not land clean.
#
# THIS IS A RETRY, NOT A SECOND SCHEDULE. It changes nothing about what gets
# built or how a target is validated/published/read back — exporters/common.py
# owns that, unchanged. It only decides WHETHER to call `./run.sh export`
# again and records the truth of what happened when it does.
#
# WHY A launchd StartCalendarInterval JOB, NOT StartInterval. StartInterval
# jobs have been observed to never fire on this Mac (see CLAUDE.md /
# ops/launchd's other jobs); a fixed daily time is the reliable primitive.
#
# WHY NOT A SECOND IMPLEMENTATION OF THE NIGHTLY CHAIN (rule a8c55a47: a
# manual path and an automated path doing the same job must be the same
# code). This calls the exact same `./run.sh export` the nightly chain calls
# and the exact same credential loader, lock and step-timeout machinery —
# just once, gated on the night's own recorded outcome, rather than seven
# other steps around it.
#
# TEST HOOKS (selftest only — never set these for a real install or a real
# manual run). CARR_NOW pins "now" to a fixed epoch-seconds moment so the lock-
# race guard below is provably correct at exact clock boundaries, rather than
# only observed on whatever moment a live run happened to catch (a single live
# 00:24 UTC run is weak evidence for a midnight-rollover edge — see
# ops/nightly-exports-retry-guard-selftest.py). CARR_NIGHTLY_OUT_DIR redirects
# the log/archive/marker paths away from the real out/ tree so a test run never
# writes into, or reads stale state from, this machine's real nightly history.
# CARR_NIGHTLY_RETRY_DRY_RUN makes record() log instead of writing to the real
# operational ledger (this machine may have real DB credentials in
# ~/.config/carr/db.env, and a test must never tag synthetic timestamps onto
# the real nightly-exports-daytime-retry service history).
carr_now_epoch() {
  if [ -n "${CARR_NOW:-}" ]; then
    print -r -- "$CARR_NOW"
  else
    date -u '+%s'
  fi
}
carr_local() {  # carr_local <epoch> <strftime-format>
  date -j -r "$1" "$2" 2>/dev/null || date -d "@$1" "$2" 2>/dev/null
}

# Run by hand any time: ./bin/nightly-exports-retry.sh
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/opt/homebrew/opt/libpq/bin:/usr/local/bin:/usr/bin:/bin"
cd "$REPO" || { print -ru2 -- "nightly-exports-retry: cannot cd $REPO"; exit 2; }

OUT_DIR="${CARR_NIGHTLY_OUT_DIR:-$REPO/out}"
LOG="$OUT_DIR/nightly.log"
mkdir -p "$OUT_DIR"
say() { print -r -- "$(date -u '+%Y-%m-%dT%H:%M:%SZ')  RETRY  $*" >> "$LOG"; }

# ── LOAD THE LEDGER CREDENTIAL EARLY, BEFORE ANY SKIP BRANCH ─────────────────
# CARR_DB_EXPORTER_URL (loaded further down, gating the retry attempt itself)
# is a different credential from the one tools/ops-record.py's `run` needs —
# that write authenticates as carr_jobs, via CARR_DB_JOBS_URL (see its own
# CREDENTIALS note). Loading it here, unconditionally and non-fatally, is what
# lets EVERY exit path below record a heartbeat, including the common ones
# that return before the exporter credential is ever touched. An absent
# CARR_DB_JOBS_URL does not gate anything here: record() below already never
# fails its caller (same contract as bin/nightly.sh's own record_run()).
source "$REPO/bin/routine-credential-env.sh"
carr_clear_routine_db_env
carr_load_routine_db_env CARR_DB_JOBS_URL || true

# ── THE LEDGER HEARTBEAT, ON EVERY EXIT PATH ─────────────────────────────────
# ops/config/services.json registers THIS job — "nightly-exports-daytime-retry",
# its own service key, not nightly-record-layer's — with a cadence of one fire
# a day. launchd DOES fire this script daily even on the (common) night the
# retry turns out to be unnecessary, but a job that only writes ops.run when it
# actually retries would go quiet on every healthy night and read permanently
# STALE/unknown against that cadence — the exact "an alarm nobody reads because
# it fires when nothing is wrong" shape this codebase has already relearned
# more than once (see the ORDER 2 addendum in bin/nightly.sh). So every exit
# path below records a row, `skipped` for the SKIP branches and
# `succeeded`/`failed` for an actual attempt, all under this job's OWN service
# key so its cadence fields describe something that is really being observed.
# record <state> <rc> <detail> [--started-at TS] [--failure-class X ...]
record() {
  local state="$1" rc="$2" detail="$3"; shift 3
  local started="$(date -u +%FT%TZ)"
  if [ "${1:-}" = "--started-at" ]; then started="$2"; shift 2; fi
  if [ -n "${CARR_NIGHTLY_RETRY_DRY_RUN:-}" ]; then
    say "DRY-RUN record state=$state rc=$rc detail=$detail (no real ops-record write — CARR_NIGHTLY_RETRY_DRY_RUN is set)"
    return 0
  fi
  ./.venv/bin/python "$REPO/tools/ops-record.py" run \
      --service nightly-exports-daytime-retry --key nightly.exports-daytime-retry \
      --state "$state" --exit-code "$rc" --started-at "$started" \
      --source-ref bin/nightly-exports-retry.sh --detail "$detail" "$@" >> "$LOG" 2>&1
}

# ── DID TONIGHT'S EXPORT ALREADY LAND CLEAN? ─────────────────────────────────
# bin/nightly.sh's own step() already wrote the authoritative line for tonight
# into the per-run archive under out/nightly-runs/. Read that back rather than
# re-deriving the answer: the archive is what "OK", "FAIL", "TIMEOUT",
# "BLOCKED" and "SKIP" already mean there, and re-deciding it here would be a
# second place for that judgment to drift from the chain that made it.
runlog_dir="$OUT_DIR/nightly-runs"
latest=""
if [ -d "$runlog_dir" ]; then
  # The (N) glob qualifier is NULL_GLOB for this one expansion only: without it
  # zsh's own glob expansion errors "no matches found" on an empty archive dir
  # (a fresh worktree, or a Mac before its first nightly run) BEFORE `ls` ever
  # runs, so redirecting `ls`'s stderr cannot silence it.
  latest="$(ls -1t "$runlog_dir"/nightly-*.log(N) 2>/dev/null | head -1)"
fi

if [ -z "$latest" ]; then
  say "SKIP  no nightly run has ever been archived — nothing to retry against"
  record skipped 0 "no nightly run archived yet"
  exit 0
fi

# ── THE LOCK-RACE GUARD (#1241 review round 5) ───────────────────────────────
# bin/nightly.sh's per-run archive is written ONLY at chain exit (carr_chain_exit
# in bin/nightly.sh), never progressively while the chain runs. That means a
# nightly chain that is CURRENTLY IN PROGRESS -- including one launchd fired
# moments ago, on the SAME wake as this retry, because the Mac slept through
# both the 02:05 scheduled fire and this job's own daytime fire -- has written
# NOTHING to out/nightly-runs/ yet. Reading "latest" and finding it merely
# dated "today or yesterday" is not proof nightly finished; it could just as
# easily be last night's leftover while tonight's run is mid-flight right now.
# Retrying in that window would call carr_take_lock nightly BEFORE bin/nightly.sh
# gets there, and lock ownership is first-come-first-served (bin/run-lock.sh) --
# so THIS script could win the lock and make the real nightly chain exit as a
# "duplicate", skipping the WHOLE night's chain, not just exports. That is a
# strictly worse outcome than the OneDrive defect this retry exists to fix.
#
# THE GUARD: only ever consider retrying when a nightly run has ACTUALLY
# COMPLETED (successfully or not -- carr_chain_exit runs on both paths) since
# the most recent SCHEDULED 02:05-local fire. If the latest archive predates
# that boundary, nightly has not finished this cycle -- it may be starting,
# mid-flight, or genuinely not yet fired -- and this script skips without ever
# touching the lock. This is a stronger, TIME-based version of "the archive
# must be today's": date alone cannot tell "finished before I woke" apart from
# "still running right now", but the boundary comparison can.
NOW_EPOCH="$(carr_now_epoch)"
TODAY_LOCAL="$(carr_local "$NOW_EPOCH" '+%Y-%m-%d')"
NOW_HHMM_NUM=$((10#$(carr_local "$NOW_EPOCH" '+%H%M')))
if [ "$NOW_HHMM_NUM" -ge 205 ]; then
  BOUNDARY_DATE="$TODAY_LOCAL"
else
  BOUNDARY_DATE="$(carr_local $((NOW_EPOCH - 86400)) '+%Y-%m-%d')"
fi
BOUNDARY_EPOCH="$(date -j -f '%Y-%m-%d %H:%M' "$BOUNDARY_DATE 02:05" '+%s' 2>/dev/null \
  || date -d "$BOUNDARY_DATE 02:05" '+%s' 2>/dev/null)"
if [ -z "$BOUNDARY_EPOCH" ]; then
  say "SKIP  could not compute the last-scheduled-02:05-local boundary — treating conservatively as no proof any nightly run has completed"
  record skipped 0 "could not compute the nightly boundary"
  exit 0
fi

ARCHIVE_TS="$(basename "$latest" | sed -n 's/^nightly-\([0-9]\{8\}T[0-9]\{6\}Z\)[.]log$/\1/p')"
if [ -z "$ARCHIVE_TS" ]; then
  say "SKIP  latest archive ($latest) has an unrecognized filename — cannot prove it postdates the last scheduled nightly"
  record skipped 0 "latest archive filename unrecognized"
  exit 0
fi
# Compact -> ISO 8601, the same reformat bin/run-lock.sh's carr_lock_age_seconds
# uses, so both BSD (this Mac) and GNU (ubuntu-latest, in a fixed-clock
# selftest) date(1) dialects can parse it.
ARCHIVE_ISO="${ARCHIVE_TS[1,4]}-${ARCHIVE_TS[5,6]}-${ARCHIVE_TS[7,8]}T${ARCHIVE_TS[10,11]}:${ARCHIVE_TS[12,13]}:${ARCHIVE_TS[14,15]}Z"
ARCHIVE_EPOCH="$(date -u -j -f '%Y-%m-%dT%H:%M:%SZ' "$ARCHIVE_ISO" '+%s' 2>/dev/null \
  || date -u -d "$ARCHIVE_ISO" '+%s' 2>/dev/null)"
if [ -z "$ARCHIVE_EPOCH" ] || [ "$ARCHIVE_EPOCH" -lt "$BOUNDARY_EPOCH" ]; then
  say "SKIP  no nightly run has completed since the last scheduled 02:05 local ($BOUNDARY_DATE 02:05) — nightly may still be starting or mid-flight; retrying now could win the lock ahead of it and starve the whole chain. Latest archive: ${latest:-<none>}"
  record skipped 0 "no nightly completion since the last scheduled 02:05 local boundary"
  exit 0
fi
MARKER="$OUT_DIR/.nightly-exports-retry-$(print -r -- "$BOUNDARY_DATE" | tr -d -).done"

# ── ONE RETRY PER SCHEDULED CYCLE, ON PURPOSE ────────────────────────────────
# The plist fires this at one fixed daytime hour. The marker keeps a second,
# hand-run invocation the same cycle from spending a second publish for no
# reason once that cycle's retry has already run. If that prior attempt
# FAILED, a plain "skipped" heartbeat here would replace the failed row with
# something that reads healthier than reality — so a prior failure is logged
# and left alone (no new record() call), and the earlier failed row stays
# latest, rather than being papered over.
if [ -f "$MARKER" ]; then
  prior="$(cat "$MARKER" 2>/dev/null)"
  case "$prior" in
    failed:*)
      say "SKIP  already attempted the daytime retry for the $BOUNDARY_DATE cycle and it FAILED ($MARKER: $prior) — not overwriting that failure with a skipped heartbeat; the failed row stays latest"
      exit 0
      ;;
    *)
      say "SKIP  already attempted the daytime retry for the $BOUNDARY_DATE cycle ($MARKER: ${prior:-<no recorded outcome>})"
      record skipped 0 "already attempted the $BOUNDARY_DATE cycle's retry"
      exit 0
      ;;
  esac
fi

# step()'s say() pads each status word differently (OK gets 4 spaces, FAIL/
# TIMEOUT/BLOCKED/SKIP get 2) so the label columns line up — matching one
# fixed gap would silently miss every status but the one it was tested
# against.
exports_line="$(grep -E '(OK|FAIL|TIMEOUT|BLOCKED|SKIP)[[:space:]]+exports \(6 targets -> OneDrive\)' "$latest" 2>/dev/null | tail -1)"

if print -r -- "$exports_line" | grep -qE '^\S+[[:space:]]+OK[[:space:]]+exports '; then
  say "SKIP  tonight's exports step already landed OK ($latest) — no retry needed"
  record succeeded 0 "tonight's exports step already landed OK"
  exit 0
fi

if [ -z "$exports_line" ]; then
  say "RETRY tonight's archive ($latest) has no exports outcome line at all — treating as a failure and retrying"
else
  say "RETRY tonight's exports step did not report OK ($latest): $exports_line"
fi

# ── ONE CHAIN'S WORTH OF MUTUAL EXCLUSION, SAME LOCK AS THE NIGHTLY CHAIN ────
# Reusing the "nightly" lock (not a lock of this script's own) means a retry
# can never run concurrently with the nightly chain itself against the same
# OneDrive files and the same export ledger — the exact race bin/run-lock.sh
# was built to close. If the nightly chain (or a still-running earlier retry)
# holds it, this is a no-op, same as a duplicate nightly invocation.
CHAIN_OUTCOME=incomplete
LOCK_HELD=0
carr_retry_exit() {
  local rc="${1:-0}"
  [ "$LOCK_HELD" -eq 1 ] && carr_release_lock
  return 0
}
trap 'carr_retry_exit $?; exit 143' INT TERM HUP
trap 'carr_retry_exit $?' EXIT

source "$REPO/bin/run-lock.sh"
if ! carr_take_lock nightly >> "$LOG" 2>&1; then
  say "SKIP  nightly lock held elsewhere — this retry is a no-op (see the LOCKED line above)"
  record skipped 0 "nightly lock held elsewhere"
  exit 0
fi
LOCK_HELD=1

# NOT carr_clear_routine_db_env here: that would unset the CARR_DB_JOBS_URL
# already loaded above for record(), and this call only ADDS a name to load,
# it does not need a clean slate. carr_load_routine_db_env's own contract is
# "export whatever of these names the file has"; a name already exported by
# an earlier call is simply exported again with the same value.
carr_load_routine_db_env CARR_DB_JOBS_URL CARR_DB_EXPORTER_URL || true
if [ -z "${CARR_DB_EXPORTER_URL:-}" ]; then
  # Tonight's exports step did NOT land OK (we only reach this branch past
  # that check above) and this machine cannot even attempt the retry — that
  # is a real, unaddressed failure, not a benign gated-out no-op. Recording
  # it as `skipped` would hide it from anyone reading the service's health;
  # `failed` is the honest state.
  say "SKIP  no exporter credential provisioned on this machine, but tonight's exports step did not land OK — recording FAILED, not skipped"
  record failed 1 "no exporter credential provisioned on this machine; tonight's exports step did not land OK" --failure-class credential_missing
  exit 0
fi

source "$REPO/bin/step-timeout.zsh"
# carr_step_timeout_prefix returns WORDS (CARR_STEP_TIMEOUT_ARGV), never a
# function call: carr_routine_exec ends in `env -i ... "$@"`, and `env`
# execs a real program, not a zsh function — carr_step_with_timeout itself
# would silently fail to run under env -i. Same caveat bin/nightly.sh's own
# step() already works around; see this function's docstring.
carr_step_timeout_prefix "$(carr_step_timeout_for exports)"
t0="$(date -u +%FT%TZ)"
say "START exports (daytime retry, caffeinate -i -s, exports timeout budget)"
if carr_routine_exec "${CARR_STEP_TIMEOUT_ARGV[@]}" \
    caffeinate -i -s env CARR_EXPORT_LIVE=1 ./run.sh export >> "$LOG" 2>&1; then
  rc=0
  say "OK    exports (daytime retry)"
else
  rc=$?
  say "FAIL  exports (daytime retry) (exit $rc)"
fi

# THE TRUTH LIVES IN THE SAME LEDGER THE NIGHTLY CHAIN WRITES TO (rule
# 1f3a7372), under its own service+run key so a retry is never mistaken for
# the night's own run, and record-run's own honesty rules apply unchanged:
# this reports exactly the exit code ./run.sh export returned, which is
# itself driven by exporters/common.py's real publish + read-back
# verification, not by anything decided here.
state="succeeded"; fclass=()
if [ "$rc" -ne 0 ]; then
  state="failed"; fclass=(--failure-class "exit_$rc")
fi
record "$state" "$rc" "daytime retry of the nightly exports step" --started-at "$t0" "${fclass[@]}"

# The marker carries the OUTCOME, not just "attempted" — a same-day rerun
# reads this back above to decide whether a failure must stay visible rather
# than being replaced by a skipped heartbeat.
print -r -- "$state:$rc" > "$MARKER"
exit "$rc"
