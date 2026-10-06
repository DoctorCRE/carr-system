#!/bin/zsh
# nightly.sh — the record layer's unattended chain (ORDER 2).
#
# Until this existed, every generated file was only as fresh as the last time a
# human remembered to run the export by hand. Seven steps, in order:
#   1. cadence      (spawn the next_action rows the rules are owed)   [ORDER 14]
#   2. matcher      (availability x open space searches -> digest)    [ORDER 14]
#   3. exports      (all seven targets, LIVE -> the vault)
#   4. corpus push  (doctrine tier: git -> the Drive/vault render copies) [CORPUS FLIP, 2026-08-06]
#   5. consumers    (renewal-feed, lead-board, deal-room — the boards built FROM
#                    those files; they must follow the export that feeds them)
#   6. graph        (derived from the freshly exported files, so it follows too)
#   7. backup       (encrypted pg_dump -> git)
#
# Steps 1 and 2 run BEFORE the exports (ORDER 14d) for one reason: they WRITE,
# and a write that lands after the export it belongs in sits invisible for a
# whole day. Same night, same file.
#
# Step 2 was added by Fable's ORDER 2 addendum. Without it the chain refreshed
# lead-registry.xlsx and panhandle-team-deals.json every night while their boards
# sat unrebuilt, so the façade check reported Lead Board and Deal Room BEHIND
# every single morning — by design, forever. An alarm that fires every day trains
# people to stop reading alarms, which costs more than the two minutes it saves.
#
# Every step runs even if an earlier one failed, and the exit code reports the
# worst outcome. The backup especially must not be skipped because an export
# broke — a bad export is exactly when you want a snapshot of the database.
#
# Appends to out/nightly.log. Verified by OUTPUT freshness (protocol rule 28),
# never by this script existing: tools/health-check.py watches the seven files.
#
# Run by hand any time: ./bin/nightly.sh
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/opt/homebrew/opt/libpq/bin:/usr/local/bin:/usr/bin:/bin"

# Drive projection is never part of the ordinary unattended path.  Recovery is
# an operator act, not an environment default: it must name why noncanonical
# Drive state is needed before any child can receive that route.
RECOVERY=0
RECOVERY_REASON=""

# Control-plane shadow path. Validate the exact versioned chain's executable
# surfaces without loading credentials, taking locks, writing logs, touching
# Drive, or calling production. A canary uses the normal path under the same
# singleton lock as the legacy schedule.
run_canary() {
  # The Nightly canary is intentionally only the availability-matcher subchain.
  # It must reject ambient capability before it can create a directory, log,
  # acquire the normal nightly lock, or source the routine credential loader.
  [ "${CARR_CONTROL_PLANE_MODE:-}" = "canary" ] || {
    print -ru2 -- "nightly canary requires explicit control-plane canary mode"; return 64; }
  [ -n "${CARR_NIGHTLY_CANARY_ROOT:-}" ] || {
    print -ru2 -- "nightly canary requires a parent-selected output root"; return 64; }
  local key value
  while IFS='=' read -r key value; do
    case "$key" in
      DATABASE_URL|BACKUP_DATABASE_URL|PG*|CARR_VAULT|CARR_ONEDRIVE_DEALS|CARR_EXPORT_LIVE|CARR_DRIVE_RECOVERY|CARR_ROUTINE_DB_ENV_FILE|CARR_INGEST_URL|CARR_AI_ROUTE_PRIMARY_URL|CARR_AI_ROUTE_SECONDARY_URL|CARR_GMAIL_APP_PASSWORD|CARR_AGE_IDENTITY|CARR_DB_*|*TOKEN*|*SECRET*|*PASSWORD*|*API_KEY*|*PROVIDER_URL*|*EXPORTER_URL*|*BACKUP_URL*)
        print -ru2 -- "nightly canary refused ambient live capability: $key"; return 78 ;;
    esac
  done < <(env)
  local base="$REPO/out/canary/nightly-record-layer" root="$CARR_NIGHTLY_CANARY_ROOT"
  case "$root" in "$base"/*) ;; *) print -ru2 -- "nightly canary output root escapes dedicated canary directory"; return 64 ;; esac
  [[ "$root" != *'/../'* && "$root" != *'/./'* && "$root" != "$base" ]] || {
    print -ru2 -- "nightly canary output root is not a direct run directory"; return 64; }
  local component
  for component in "$REPO/out" "$REPO/out/canary" "$base"; do
    [ ! -L "$component" ] || { print -ru2 -- "nightly canary output root crosses a symlink"; return 64; }
  done
  local py="$REPO/.venv/bin/python"
  [ -x "$py" ] || py=python3
  local snapshot marker
  snapshot="$(cat)"
  [ -n "$snapshot" ] || { print -ru2 -- "nightly canary requires a protected parent snapshot"; return 78; }
  marker="$(print -rn -- "$snapshot" | "$py" "$REPO/pipelines/availability_matcher.py" --canary)" || return $?
  case "$marker" in
    'availability-matcher: canary-result '*) print -r -- "nightly canary result: ${marker#availability-matcher: canary-result }" ;;
    *) print -ru2 -- "nightly canary child emitted no registered aggregate"; return 1 ;;
  esac
}

if [ "${1:-}" = "--preflight" ]; then
  required=(
    ops/seat-health.py ops/seat_health.py
    ops/vault-drift-watch.py bin/schema-snapshot.sh ops/p1-environment-gate.py
    ops/p1-rebuild-gate.py ops/p1-integration-gate.py pipelines/cadence_engine.py
    pipelines/availability_matcher.py ops/fetch-allowlist.py
    ops/cutover-readiness.py ops/codex-hook-smoke.sh tools/corpus-sync.py
    ops/vendor-level-drift-check.py bin/backup-dump.sh bin/archive-calendar.sh
    bin/sync-settings.sh bin/type-check.sh ops/store-markup-scan.py
    generators/build-open-items-dashboard.py ops/nightly-verb-probe.py
    bin/smoke-and-record.sh tools/ops-record.py ops/staging-observed-prune.py
    tools/health-check.py ops/jev_spend_health.py ops/grok_session.py
    lib/claude_continuity_spool.py
    # bin/routine-canonical-seam-refusal.sh came off this list on 2026-08-23: the
    # chain stopped launching it when the refusals became tombstones, and a
    # preflight that requires a file no step runs is checking the wrong thing.
    # It and bin/routine-admin-refusal.sh stay in the tree and keep their exit
    # contracts (69 and 78), asserted by running them in
    # ops/scheduled-drive-writer-selftest.py and
    # ops/nightly-capability-skip-selftest.py rather than through this chain.
  )
  missing=0
  for path in "${required[@]}"; do
    if [ ! -f "$REPO/$path" ]; then
      print -ru2 -- "nightly preflight missing: $path"
      missing=$((missing+1))
    fi
  done
  [ "$missing" -eq 0 ] || exit 1
  /bin/zsh -n "$REPO/bin/nightly.sh" || exit 1
  print -r -- "nightly preflight: ${#required[@]} chain surfaces present; writes=0"
  exit 0
fi
if [ "$#" -eq 1 ] && [ "$1" = "--canary" ]; then
  run_canary
  exit $?
elif [ "$#" -eq 0 ]; then
  unset CARR_VAULT CARR_EXPORT_LIVE
elif [ "$#" -eq 3 ] && [ "$1" = "--recovery" ] && [ "$2" = "--reason" ] && [ -n "$3" ]; then
  RECOVERY=1
  RECOVERY_REASON="$3"
else
  print -ru2 -- "usage: bin/nightly.sh [--preflight|--recovery --reason WHY]"
  exit 64
fi
LOG="$REPO/out/nightly.log"
mkdir -p "$REPO/out"

# ── WHERE THIS RUN'S LINES BEGIN, so the trim below cannot eat them ──────────
# The rolling trim at the end of carr_chain_exit keeps the last 2000 lines, and
# its comment used to say that was "several months". It is not: one chain now
# emits well over 2000 lines on its own, so the trim was deleting the FIRST half
# of the very run that had just written it. Measured 2026-09-15: the whole log
# held 36 timestamped lines, all from 07:14Z onward, and the exports step at
# 07:05Z — the step that had failed twelve nights running — was gone before
# anyone could read why. A chain that destroys its own diagnostics is worse than
# one that keeps none, because the green-looking remainder reads as the whole run.
LOG_START_LINES=0
[ -f "$LOG" ] && LOG_START_LINES="$(wc -l < "$LOG" | tr -d ' ')"

say() { print -r -- "$(date -u '+%Y-%m-%dT%H:%M:%SZ')  $*" >> "$LOG"; }

# ── THE DEATH THAT LEFT NO LINE (2026-08-23) ─────────────────────────────────
# On 2026-08-17, 08-18 and 08-19 this chain ran ZERO steps and out/nightly.log
# held nothing to find it by. An unset variable under `set -u` (line 33) does
# not fail a step — it kills the shell where it stands, and the shell's own
# complaint goes to STDERR, which no part of this file was reading. `say` writes
# to the log; the shell writes to fd 2; the two never met. Three nights of no
# cadence, no matcher, no exports, no boards, no graph, no backup, and every
# dead-man check green because the pings are the last step and the chain never
# reached them.
#
# The default a few lines below stops that ONE variable from killing the chain.
# It does nothing for the next unset variable somebody adds, and the failure MODE
# is what made the outage expensive, not the particular name. So this closes the
# mode rather than the instance, in two parts:
#
#   1. THE SHELL'S OWN STDERR IS CAPTURED AND LANDS IN THE LOG. Steps already
#      redirect their own output there; what was never captured is what the
#      chain script itself says when it dies. Captured to a file rather than
#      teed through a co-process on purpose: a `set -u` death is exactly the
#      moment a background tee is least likely to have flushed.
#   2. AN EXIT HANDLER THAT KNOWS WHETHER THE CHAIN FINISHED. CHAIN_OUTCOME is
#      `incomplete` until the completion block at the bottom sets it. Any exit
#      that reaches the handler still holding `incomplete` died mid-run, so the
#      handler names the variable out of the captured stderr, writes the FATAL
#      and completion lines a reader looks for, and FIRES THE CHAIN PING — a
#      night that ran no steps must alarm on the same clock as a night that ran
#      them and failed, not wait out the dead-man grace period.
#
# It also owns the lock release, so the chain has ONE exit path instead of an
# EXIT trap that releases and a separate death that reports nothing. LOCK_HELD
# keeps that safe here, above where bin/run-lock.sh has even been sourced.
CHAIN_OUTCOME=incomplete
LOCK_HELD=0
CHAIN_EXIT_RAN=0
CHAIN_STDERR="$REPO/out/.nightly-stderr.$$"
# The handler removes this file; SIGKILL, a panic and a reboot all run no
# handler, so sweep anything a previous run left behind. A day old, not an hour:
# a capture belonging to a chain still running must never be taken out from
# under it, and no run of this chain lasts anywhere near that long.
find "$REPO/out" -maxdepth 1 -name '.nightly-stderr.*' -mtime +1 -delete 2>/dev/null
exec 3>&2
exec 2>"$CHAIN_STDERR"

carr_chain_exit() {             # carr_chain_exit <exit-status>
  local rc="${1:-0}"
  # Re-entrant: the INT/TERM handler calls this and then exits, which fires the
  # EXIT trap on the way out. Reporting one death twice is its own confusion.
  [ "$CHAIN_EXIT_RAN" -eq 1 ] && return 0
  CHAIN_EXIT_RAN=1
  exec 2>&3 3>&-
  local captured=""
  if [ -s "$CHAIN_STDERR" ]; then
    captured="$(cat "$CHAIN_STDERR")"
    print -r -- "$captured" >&2
    print -r -- "--- shell stderr this run ---" >> "$LOG"
    print -r -- "$captured" >> "$LOG"
  fi
  rm -f "$CHAIN_STDERR"
  if [ "$CHAIN_OUTCOME" = incomplete ]; then
    local named
    named="$(print -r -- "$captured" | grep -E 'parameter not set|unbound variable' | head -1)"
    if [ -n "$named" ]; then
      # zsh reports "<path>:<line>: <NAME>: parameter not set". Stripping the
      # path leaves the file, the line and the variable — which is the whole
      # question a morning asks — instead of somebody's home directory.
      say "FATAL  set -u killed the chain at ${named##*/} — every step below that line did not run tonight"
    else
      say "FATAL  the chain exited $rc without reaching its completion line — every step below that point did not run tonight"
    fi
    say "===== nightly chain FINISHED WITH FAILURES — died before its completion line ====="
    # The named steps' outcomes are deliberately NOT supplied: the chain died, so
    # nobody knows what exports or the backup did. hc-ping.sh pings nothing for an
    # unsupplied outcome and lets those checks go late on their own clock, which
    # is the honest signal. The WHOLE-CHAIN check is supplied and alarms now.
    HC_CHAIN_RC="$rc" "$REPO/bin/hc-ping.sh" >> "$LOG" 2>&1
    print -r -- "nightly receipt: died_before_completion=1 exit=$rc"
    print -r -- "nightly result: chain_failed"
  fi
  # ── KEEP THIS RUN'S OWN LOG BEFORE THE ROLLING TRIM ────────────────────────
  # Here rather than at the foot of the script because the foot is only reached
  # by a chain that completes, and a chain that DIES is exactly the one whose
  # log is worth keeping. carr_chain_exit runs on both paths.
  local runlog_dir="$REPO/out/nightly-runs"
  mkdir -p "$runlog_dir" 2>/dev/null || true
  if [ -f "$LOG" ] && [ -d "$runlog_dir" ]; then
    tail -n "+$(( LOG_START_LINES + 1 ))" "$LOG" \
      > "$runlog_dir/nightly-$(date -u '+%Y%m%dT%H%M%SZ').log" 2>/dev/null || true
    # Last 14 runs. Bounded on purpose: the point is diagnosing the last two
    # weeks of nightlies, not keeping an archive nobody prunes.
    ls -1t "$runlog_dir"/nightly-*.log 2>/dev/null | tail -n +15 | while read -r stale; do
      rm -f "$stale"
    done
  fi
  # Keep the rolling log from growing without bound. The per-run archive above
  # is what a diagnosis reads; this file is the recent-history tail.
  tail -n 2000 "$LOG" > "$LOG.trim" 2>/dev/null && mv "$LOG.trim" "$LOG"
  [ "$LOCK_HELD" -eq 1 ] && carr_release_lock
  return 0
}
trap 'carr_chain_exit $?; exit 143' INT TERM HUP
trap 'carr_chain_exit $?' EXIT

# Routine work receives only named least-privilege database capabilities.  Do
# not source db.env: it also holds owner/admin credentials and shell syntax.
source "$REPO/bin/routine-credential-env.sh"
carr_clear_routine_db_env
carr_load_routine_db_env CARR_DB_JOBS_URL CARR_DB_EXPORTER_URL CARR_DB_BACKUP_URL || exit $?

# A CAPABILITY THIS MACHINE DOES NOT HOLD IS A SKIP, NEVER A SILENT DEATH.
# The loader exports only the names it FINDS in db.env, so an unprovisioned
# capability leaves its variable unset — and under `set -u` (line 33) the first
# "$CARR_DB_BACKUP_URL" below then kills the whole chain mid-run: no FAIL line,
# no `chain FINISHED` line, nothing in out/nightly.log to find it by. Every step
# after that point simply never happens.
#
# That is not hypothetical. It began on 2026-08-17, when the hardening that
# replaced backup-dump.sh's neonctl owner-credential path with a dedicated
# carr_backup DSN landed in the code while this Mac had no such DSN. By 08-19
# the local backup had aged past the catch-up threshold, which moved the first
# dereference to the TOP of the chain, and three consecutive nights then ran
# zero steps — no cadence, no matcher, no exports, no boards, no graph.
#
# ONE DEFAULT, HERE, rather than a `:-` at each call site. #380 did it inline at
# four sites, which works but leaves nothing to point at when asking "is every
# use of this variable safe?" — the answer has to be re-derived by reading all
# four. Defaulting once makes that a single fact, and lets
# ops/nightly-capability-skip-selftest.py check "every use comes AFTER the
# default" as a property of the file instead of a habit.
#
# bin/backup-dump.sh ALREADY handles the missing credential correctly — it
# exits 78, which this chain reports as SKIP and steps past. Defaulting the
# variable here is what lets that graceful path be reached at all: the callee's
# fallback cannot run if dereferencing the variable kills the caller first.
CARR_DB_BACKUP_URL="${CARR_DB_BACKUP_URL:-}"
# The portability mirror binds the exporter capability (see its step below) and
# needs the same treatment for the same reason: ops/nightly-tombstone-selftest.py
# runs this chain with a stripped environment, and an unset variable under set -u
# aborts the whole run before any step can report — which is the failure the
# paragraph above exists to prevent, not a second one.
CARR_DB_EXPORTER_URL="${CARR_DB_EXPORTER_URL:-}"

rc_total=0
# LAST_STEP_RC carries the outcome of the step that just ran (0 = OK, 78 = SKIP,
# 69 = BLOCKED, anything else = FAIL). Added 2026-08-07 so the dead-man pings can
# report on the steps they are named after instead of on the mere fact that the
# chain reached the end. See the ping block at the bottom.
LAST_STEP_RC=0
# How many steps refused tonight for want of a canonical seam. Counted separately
# from rc_total so the backlog stays a figure somebody reads, rather than either
# a red chain every night or nothing at all. Reported on the completion line.
seam_blocked=0

# ── THE PER-STEP WALL CLOCK (2026-08-23) ─────────────────────────────────────
# How many steps were cut off tonight for making no progress. Counted separately
# from seam_blocked for the same reason that one is separate from rc_total: a
# step that refused for a known missing seam and a step that WEDGED are
# different mornings' work. A timeout IS a failure and does set rc_total, unlike
# 78 and 69 below.
#
# What this buys is not the killing, it is the CONTINUING. The chain already
# survived a step that fails; it did not survive one that hangs, and the
# encrypted backup is the last step, so a hang anywhere earlier took the backup
# down with it and left the singleton lock held — which made every later
# invocation a silent exit 0. See bin/step-timeout.zsh for the measurements
# behind the limit.
timed_out=0
source "$REPO/bin/step-timeout.zsh"

# ── TOMBSTONES (2026-08-23, process-audit council R2) ────────────────────────
# How many steps were GATED OUT tonight rather than run. A tombstoned step does
# not execute, so it cannot be OK, SKIP, BLOCKED, TIMEOUT or FAIL — it is a
# fourth thing and it gets its own count.
#
# WHY IT IS NOT JUST A DELETED STEP. On a typical night eleven of these thirty
# steps did no work: six refused for a missing canonical seam, four for an admin
# capability nobody has provisioned here, one was a retired no-op still being
# executed. Every one of them printed its refusal into the log every night for a
# week or more, and this file's own comments call that expected. That is the
# alarm-fatigue failure the chain has now learned three separate times — the
# boards before the ORDER 2 addendum, the mypy tripwire hiding behind an
# already-red row from 08-08 to 08-10, and the fourteen nightly seam refusals
# that made BLOCKED a word people skip. A reader who learns to skip BLOCKED
# skips it on the night it means something.
#
# AND NOT A SILENT DELETION EITHER (rule 1b8e7f43: a blocked action is filed,
# never dropped). Cutting the line would remove the duty along with the noise,
# and nothing would remember the system once owed this. A tombstone IS the file:
# the step does not run, and the log states in one line what is missing and what
# would reopen it. Executing a refusal to produce that sentence was always the
# expensive way to say it — it costs a process launch, a ledger write, a wall
# clock, and a word in the log that also means "something broke".
#
# BUILDING THE MISSING SEAMS IS PRODUCT WORK AND IS NOT IN SCOPE HERE. The
# tombstone records the debt; it does not pay it.
tombstoned=0

# ── THE JOB-RUN LEDGER (Program 3, 2026-08-14) ───────────────────────────────
# Until this existed, step() computed exactly the right outcome for every step,
# including the SKIP distinction below that nothing else in the system makes,
# and then threw it into a text file on one Mac. A morning question like "which
# step failed, on which code, and what else broke at the same time" could only
# be answered by a human reading out/nightly.log over somebody's shoulder. That
# is the "terminal archaeology" Program 3's gate is named against.
#
# ONE CORRELATION ID PER NIGHT, exported so every step of one chain — and
# anything a step itself records — threads onto the same journey. `ops-record
# trace <id>` then returns the whole night as one chain.
#
# THE RUN KEY IS DERIVED FROM THE LABEL, up to the first parenthesis, so
# "cadence engine (spawn owed next actions)" is always nightly.cadence-engine.
# The parenthetical is where wording actually churns, so keeping it out of the
# key means a reworded step keeps its history. Renaming the leading words DOES
# start a new key, which is the tradeoff taken deliberately rather than changing
# the signature of every call site in a chain that runs unattended tonight.
#
# RECORDING NEVER FAILS A STEP. The recorder's exit code is ignored on purpose.
# It is also not hidden: a step that goes unrecorded makes this service read
# `unknown` on the next health look rather than staying green, because
# ops.v_service_environment_health derives health from the latest observation
# and its freshness and stores no health anywhere. Silence is visible by design.
LEDGER_OFF=0
record_run() {                  # record_run <label> <state> <rc> <started_at>
  [ "$LEDGER_OFF" -eq 1 ] && return 0
  local key
  # The '(' is escaped because zsh treats an unescaped one in a ${..%%..}
  # pattern as globbing syntax and errors with "bad pattern" — caught by running
  # the expression before shipping it, not by reading it.
  key="nightly.$(print -r -- "${1%% \(*}" | tr 'A-Z ' 'a-z-' | tr -cd 'a-z0-9.-')"
  local fclass=()
  [ "$2" = "failed" ] && fclass=(--failure-class "exit_$3")
  ./.venv/bin/python tools/ops-record.py run \
      --service nightly-record-layer --key "$key" --state "$2" \
      --exit-code "$3" --started-at "$4" --source-ref bin/nightly.sh \
      --detail "$1" "${fclass[@]}" >> "$LOG" 2>&1
  # 78 = the ops schema is not there yet (migration 0115 unapplied on this
  # database). Say it once and stop trying, rather than printing the same line
  # for every step — an error that repeats every night trains people to stop
  # reading the log, which is the failure this chain already learned once.
  if [ $? -eq 78 ]; then
    LEDGER_OFF=1
    say "SKIP  job-run ledger not configured on this database (migration 0115 unapplied) — steps will not be recorded tonight"
  fi
  return 0
}

step() {                        # step <label> <command...>
  local label="$1"; shift
  local t0; t0="$(date -u +%FT%TZ)"
  # The wall clock goes INSIDE carr_routine_exec, not around it: that function
  # ends in `env -i` and a python wrapper cannot exec a zsh function. The
  # stripped environment passes straight through the wrapper to the real
  # command, so the credential boundary is unchanged.
  carr_step_timeout_prefix "$(carr_step_timeout_for "$label")"
  say "START $label"
  LAST_STEP_RC=0
  if carr_routine_exec "${CARR_STEP_TIMEOUT_ARGV[@]}" "$@" >> "$LOG" 2>&1; then
    say "OK    $label"
    record_run "$label" succeeded 0 "$t0"
  else
    local rc=$?
    LAST_STEP_RC=$rc
    # 78 = EX_CONFIG: the step ran, found a credential or setting it needs is
    # absent, wrote nothing and said so. That is NOT a failed night. Treating it
    # as one would fire the alarm every single night until the credential lands,
    # which is precisely the "an alarm that fires every day trains people to stop
    # reading alarms" problem the ORDER 2 addendum fixed for the boards. The line
    # still appears in the log, so it is visible without being a failure.
    if [ "$rc" -eq 78 ]; then
      say "SKIP  $label (exit 78 — not configured; see the step's own message above)"
      record_run "$label" skipped "$rc" "$t0"
    # 69 = EX_UNAVAILABLE, our seam refusal: a retired Drive projection was
    # reached without the record/document replacement that has to exist before
    # the work may resume (drive_projection below). The step ran, wrote nothing,
    # and named the missing seam.
    #
    # NOT A FAILED NIGHT, changed 2026-08-21 on Joe's instruction, and the
    # argument is the one already settled for 78 above. Through the store-first
    # cutover FOURTEEN steps refuse this way every single night. A chain that is
    # red every night is a chain nobody reads, and on the night one of these is a
    # real break there is no contrast left to see it by — the identical failure
    # the ORDER 2 addendum fixed for the boards, and the one that let the mypy
    # tripwire sit red from 08-08 to 08-10 behind an already-red row.
    #
    # NOT SILENT EITHER, which is the objection the refusal script itself raised:
    # skipping quietly would mark the chain healthy while an ordinary system
    # function did not run. So BLOCKED is its own word in the log, distinct from
    # SKIP, it is counted in seam_blocked, the count rides the completion line,
    # and the nightly-chain health row prints the backlog beside the verdict.
    # Visible and counted, just not fatal.
    elif [ "$rc" -eq 69 ]; then
      say "BLOCKED  $label (exit 69 — canonical seam missing; see the step's own message above)"
      record_run "$label" skipped "$rc" "$t0"
      seam_blocked=$((seam_blocked + 1))
    # 124 = the per-step wall clock fired: the step stopped making progress and
    # was cut off, along with any grandchildren still holding what it was stuck
    # on. THIS IS A FAILURE, not a SKIP — unlike 78 and 69 above it sets
    # rc_total and turns the night red, because a wedged step is never expected.
    #
    # It gets its own word rather than reading as a plain FAIL because the two
    # need different mornings' work: an ordinary FAIL ran and reported a reason,
    # while a TIMEOUT reported nothing at all and the reason has to be found in
    # what it was waiting on. The 2026-08-23 case was a Postgres socket the
    # server had already closed.
    elif [ "$rc" -eq 124 ]; then
      say "TIMEOUT  $label (exit 124 — no progress inside its wall clock; the step and its children were stopped)"
      record_run "$label" failed "$rc" "$t0"
      timed_out=$((timed_out + 1))
      rc_total=1
    else
      say "FAIL  $label (exit $rc)"
      record_run "$label" failed "$rc" "$t0"
      rc_total=1
    fi
  fi
}

tombstone() {                   # tombstone <label> <what is missing> <what reopens it>
  local label="$1" missing="$2" reopen="$3"
  local t0; t0="$(date -u +%FT%TZ)"
  # NOTHING IS EXECUTED. No wall clock, no credential boundary, no child — there
  # is no command here to bound or to strip an environment for.
  #
  # LAST_STEP_RC IS 69, NOT 0. A tombstoned step did not do its work, and the two
  # variables this carries into (EXPORTS_RC, BACKUP_RC) are read by the dead-man
  # pings. Reporting 0 would tell hc-ping.sh the work succeeded, which is the
  # 2026-08-07 corrupt-backup defect one layer up: a success signal that never
  # looked at the thing it reports on. No step with a registered ping is
  # tombstoned today, and this is what keeps that safe by construction rather
  # than by whoever adds the next tombstone remembering.
  LAST_STEP_RC=69
  say "TOMBSTONE  $label — gated out, not run · missing: $missing · reopens when: $reopen"
  # `cancelled`, not `skipped`: skipped means the step ran and found it could not
  # proceed, which is what the 78 branch above records and is a different fact.
  # tools/ops-record.py opens incidents from failed/timed_out only, so a tombstone
  # keeps its row in the ledger every night — the file that says the duty still
  # exists — without raising a nightly incident for a debt already named here.
  record_run "$label" cancelled 69 "$t0"
  tombstoned=$((tombstoned + 1))
}

# Keep the exact old projection commands available only behind the recovery
# envelope.  In normal scheduled operation each names the concrete canonical
# seam that still has to exist before the work may resume; none falls back to a
# Drive mount just because it happens to be present on this Mac.
#
# ON THE NORMAL PATH THIS IS NOW A TOMBSTONE, not an executed refusal (2026-08-23).
# It used to launch bin/routine-canonical-seam-refusal.sh under the step wrapper
# purely so that process could exit 69 and have step() print BLOCKED. The sentence
# was the whole product; the process was overhead. The callsites are unchanged —
# a projection still declares its label, its missing seam and the command the
# recovery envelope runs — so nothing about the recovery path or the Drive
# registry moves.
drive_projection() {            # drive_projection <label> <missing-seam> <command...>
  local label="$1" seam="$2"; shift 2
  if [ "$RECOVERY" -eq 1 ]; then
    step "RECOVERY NONCANONICAL $label" "$@"
  else
    # THE REOPEN CONDITION POINTS AT THE STEP'S OWN BLOCK rather than asserting
    # what reopening takes. For most projections it is a build; for upstream
    # corroborate it is not — #539 established that the block there is a decision
    # about how external provider datasets are acquired and how long they may be
    # kept, which no amount of wiring settles. A line that told every reader "that
    # seam exists" would send them looking for something to build, which is the
    # exact wrong turn the old seam NAME already cost two sessions.
    tombstone "$label" "canonical seam — $seam" \
      "that seam exists — read this step's block in bin/nightly.sh for what it would take — and the step moves out of drive_projection onto the normal route"
  fi
}

say "===== nightly chain begin ====="
if [ "$RECOVERY" -eq 1 ]; then
  say "RECOVERY NONCANONICAL: Drive projections enabled; reason=$RECOVERY_REASON"
  print -ru2 -- "RECOVERY NONCANONICAL: Drive projections enabled; reason=$RECOVERY_REASON"
fi
cd "$REPO" || { say "FATAL cannot cd $REPO"; exit 2; }

# ── ONE CHAIN AT A TIME (2026-08-14) ─────────────────────────────────────────
# Until this existed, `./bin/nightly.sh` while the scheduled run was mid-flight
# gave you two chains against one database and one vault. The measured cost, on
# 2026-08-14: the second run's vault-drift check read the 30 files the first run
# had just re-exported and reported all 30 as TAMPER. Nothing had been tampered
# with — every quarantined diff was one line, the export timestamp. A drift
# alarm that fires 30 times for a self-inflicted reason is an alarm nobody will
# read on the night it means something.
#
# The false alarm is only the cheap symptom. Two chains also race on the export
# ledger, the encrypted backup, the drift baseline each of them rewrites at the
# end, and the consumer rebuilds — and the loser of any of those races writes a
# file that no longer matches the database it was derived from.
#
# A duplicate run is a NO-OP, not a failure: exit 0, before the dead-man pings,
# so a second run cannot ping /fail for a night the first run is handling fine.
# The traps are here at top level and not inside carr_take_lock because zsh runs
# an EXIT trap set inside a function when the FUNCTION returns — see the header
# of bin/run-lock.sh, where that cost the first cut of this fix.
#
# A HOLDER THAT NEVER LETS GO IS THE OTHER FAILURE, and it is not hypothetical
# either: on 2026-08-23 a stalled holder took three launch attempts and the run
# that finally completed started 76 MINUTES LATE. pid liveness alone cannot see
# that — the stalled process was alive, so every later invocation read a live
# claim and exited 0 as a well-behaved duplicate. The bound in bin/run-lock.sh is
# what turns "alive" into "alive and still plausibly working".
#
# THE TRAPS ARE INSTALLED FAR ABOVE, at the top of the file, and carr_chain_exit
# releases the lock as one of the things it does. That is a change from the two
# bare `carr_release_lock` traps that used to sit right here: a trap installed
# only after the lock is taken cannot report the deaths that happen before it,
# which is every death the 2026-08-17..19 outage consisted of. LOCK_HELD is what
# tells the one handler whether there is yet a lock to release.
source "$REPO/bin/run-lock.sh"
if ! carr_take_lock nightly >> "$LOG" 2>&1; then
  say "SKIP  chain already running — this invocation is a no-op (see the LOCKED line above)"
  # A duplicate is a deliberate no-op, not a death: without this the exit handler
  # would read `incomplete`, call it a chain failure and ping /fail for a night
  # the holder is handling fine.
  CHAIN_OUTCOME=duplicate
  exit 0
fi
LOCK_HELD=1

# ONE ID FOR THE WHOLE NIGHT. Exported so every step, and anything a step itself
# records, threads onto the same journey — `tools/ops-record.py trace <id>`
# returns the night as one chain instead of a text file to read by hand.
export CARR_CORRELATION_ID="$(uuidgen | tr 'A-Z' 'a-z')"
say "correlation: $CARR_CORRELATION_ID"

# WHICH CODE RAN. Added 2026-08-07. This chain executes whatever is checked out
# in the working tree, and on 2026-08-07 the checked-out branch moved twice in
# one evening with nobody noticing — once leaving a committed fix stranded off
# main, so the tree ran code that main did not have. Nothing anywhere recorded
# which code a given night ran, which means a morning's log could not answer the
# first question you ask when a night goes wrong: what was actually running.
#
# Three cheap facts, logged before any step. The uncommitted count matters as
# much as the sha: a dirty tree means the night ran something that exists in no
# commit and cannot be recovered by checking one out.
GIT_BRANCH="$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
GIT_SHA="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo '?')"
GIT_DIRTY="$(git -C "$REPO" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
if [ "${GIT_DIRTY:-0}" -gt 0 ] 2>/dev/null; then
  say "code: branch $GIT_BRANCH @ $GIT_SHA — $GIT_DIRTY uncommitted path(s) in the tree"
else
  say "code: branch $GIT_BRANCH @ $GIT_SHA (tree clean)"
fi

# Export live Drive projections only in the explicitly labeled recovery path.
if [ "$RECOVERY" -eq 1 ]; then
  export CARR_EXPORT_LIVE=1
  export CARR_DRIVE_RECOVERY=1
fi

# ── PHASE 1 v2 (2026-08-13): vault drift watch --check, FIRST THING THIS CHAIN
# DOES. "install that vault drift watch into the nightly rewrite. That way, the
# first thing it does is check all the drive files" — the design brief for
# this step. It compares the vault's registered/generated files against the
# LAST --rebaseline (taken after last night's exports, see the step at the
# bottom of this chain) so a file tampered with outside the export path is
# caught BEFORE tonight's export would otherwise silently overwrite the
# evidence — the exact gap ops/vault-drift-watch.py's v1 (plain manifest diff)
# could not close. It also flags any UNEXPECTED add/modify/delete of a non-
# registry, non-corpus-mirror .md file. A finding here is loud (exit 2, marks
# this step FAIL, quarantines the file + a diff, appends an intake payload to
# out/vault-drift-salvage-manifest.jsonl) but NEVER aborts the chain — exports
# still have to run and heal the file either way, same "does not abort"
# contract every other step in this chain already has via the step() wrapper
# below. Live routing to /ingest ENABLED 2026-08-13 (Joe: "Let's go ahead and
# do it"): posts each salvage payload as source vault_drift via the token in
# ~/.config/carr/ingest.env (CARR_INGEST_TOKEN_VAULT_DRIFT, Joe-pasted per the
# secrets policy, held in the additive INGEST_TOKENS_EXTRA Worker secret).
# Until that token is pasted on both ends, the POST 401s loudly and the local
# quarantine + salvage manifest still capture everything — fail-visible.
# RECOVERY-POINT CATCH-UP, ahead of every step that could hang.
#
# The chain already survives a step that FAILS: `step` records the bad exit and
# keeps going, so a broken export cannot skip the backup. It does NOT survive a
# step that HANGS — the backup is the last step, so anything that blocks earlier
# takes the backup down with it, wake schedule or no.
#
# Joe accepted a 24-hour recovery point objective on 2026-08-13. That tolerance is
# a contract, and a contract that only holds when nothing hangs is not a contract.
# So: if the newest backup is already older than the objective, take one NOW,
# before the chain risks stalling. The end-of-chain backup still runs and is still
# the authoritative post-write snapshot; this only fires when we are ALREADY out
# of contract, which on a healthy night is never.
# THE RECOVERY POINT IS MEASURED ACROSS BOTH BACKUP PATHS, not just the local
# one. Until 2026-08-21 this block read `ls -t backups/*.sql.age` and nothing
# else, so it saw only the dump taken on this Mac. There are two paths, and
# Program 4 built the second one precisely so the backup of last resort would
# not depend on this machine: .github/workflows/backup-nightly.yml runs the
# SAME bin/backup-dump.sh on GitHub's infrastructure with the same age key.
#
# WHAT THE ONE-PATH VIEW COST, measured the day this changed. The local
# credential was absent, so the newest local dump was 104h old and this block
# announced "objective is 24h" on every run. The cloud workflow had succeeded
# that morning and every morning before it; the true recovery point was about
# twelve hours, well inside the objective. Every local signal said otherwise.
#
# The alarm also pointed at a fix that would have made things worse. The role
# comment in migrations/0119_backup_role.sql is explicit: the backup credential
# is held "never on Joe's Mac and never in this repo". Adding it locally to
# quiet this line would have put the credential on the exact machine the second
# path exists to be independent of.
#
# ops/recovery-point.py keeps UNKNOWN separate from STALE, which is the whole
# reason it is a script rather than another line of shell here: a path that
# could not be read is not a path that reported a gap, and collapsing the two
# is how a false alarm gets built in the first place.
#   exit 0 = within objective · 1 = a real, verified gap · 2 = unknown
RPO_HOURS=24
recovery_report="$(./.venv/bin/python ops/recovery-point.py 2>&1)"
recovery_rc=$?
print -r -- "$recovery_report" >> "$LOG"
if [ "$recovery_rc" -eq 1 ]; then
  say "CATCH-UP  recovery point is out of contract on every readable path — taking one before the chain begins"
  step "recovery-point catch-up (out of contract)" env CARR_DB_BACKUP_URL="$CARR_DB_BACKUP_URL" ./bin/backup-dump.sh
elif [ "$recovery_rc" -eq 2 ]; then
  # Deliberately NOT a catch-up. Unknown means we could not ask, so taking a
  # dump here would be acting on an absence of evidence — and on a machine with
  # no local credential it would only ever produce a SKIP line that reads like
  # a gap, which is the noise this whole change removes.
  say "UNKNOWN  recovery point could not be read on any path — see the lines above; not assuming a gap and not assuming a backup"
else
  say "OK    recovery point intact (objective ${RPO_HOURS}h) — see the lines above for which path is newest"
fi

# VAULT DRIFT WATCH RETIRED, both ends — nothing writes to the vault to drift.
# It existed because the vault accepted writes from more than one actor and a
# client write door had been demonstrated open, so a generated markdown file
# could be clobbered between runs. It watches tracked .md files under the vault.
# The 2026-08-19 cutoff retired those renders and this chain no longer writes
# there at all, so the watch guards a surface with no writers and no files. The
# script and its manifests stay for the recovery envelope.

# SCHEMA SNAPSHOT DRIFT, added 2026-08-13 with the snapshot itself. db/schema.sql
# is now what builds staging AND what CI's migration check applies pending
# migrations on top of. If production's structure moves and the committed file
# does not, both of those keep passing against a shape that no longer exists —
# a green check measuring the wrong database, which is the failure mode this
# whole session kept finding. It runs HERE and not in CI because it needs
# production, and CI cannot reach production by construction.
tombstone "schema snapshot drift" \
  "admin capability — a separately provisioned admin database credential this Mac does not hold" \
  "that credential is provisioned here and bin/schema-snapshot.sh is restored to this line"

# ── PROGRAM 1's GATE, ASKED NIGHTLY (2026-08-15) ─────────────────────────────
# "Staging cannot access Production data or credentials" is G1's gate, and until
# this line it was a sentence in a roadmap and an argument in wrangler.toml. The
# isolation was built on 2026-08-13; nothing has ever asked whether it still
# holds, and nothing would have noticed the day it stopped.
#
# WHAT PROVOKED IT is the same shape as the step above. On 2026-08-15 the
# staging database was found FOUR migrations behind production, including the
# one carrying P0-1's release object. It surfaced when a person went to run an
# acceptance test there and got "relation ops.release does not exist" — six days
# after the drift began. The gate's assertions 5 and 6 are that discovery turned
# into a question the machine asks every night, in both directions.
#
# It runs HERE, not in CI, for the same reason: it needs the two REAL Neon
# projects, and CI reaches neither by construction. It writes to neither, and
# opens production read-only at the session level rather than merely intending
# to. step() records the outcome to the operational ledger, so a night it fails
# is a row rather than a line in a log nobody opens (rule 1f3a7372).
tombstone "environment isolation gate" \
  "admin capability — the two real Neon projects, which the routine boundary's two credentials cannot open" \
  "an admin credential is provisioned here and ops/p1-environment-gate.py is restored to this line"

# ── PROGRAM 1's REBUILD CLAUSE, PROVEN NIGHTLY (2026-08-15) ──────────────────
# "A fresh non-production environment can be reconstructed from repository
# declarations and approved secret references." Until this ran, reconstruction
# had never been attempted once, and the only way to discover a missing piece
# would have been to need it.
#
# It branches STAGING, never production — a Neon branch is a copy-on-write child
# of its parent, so branching production would hand a throwaway database every
# production row. Guard 0 refuses on the production project id before anything
# is created, and the branch is destroyed on every exit path.
#
# The nightly cost is one Neon branch created and deleted. The thing it buys is
# that the repository's sufficiency is a measured fact each morning rather than
# an argument nobody has tested.
tombstone "environment rebuild proof" \
  "admin capability — creating and destroying a Neon branch off staging" \
  "an admin credential is provisioned here and ops/p1-rebuild-gate.py is restored to this line"

# Program 1's INTEGRATION clause, the third use of the rehearsal lane. The
# rebuild gate above proves the SCHEMA stands up; this proves the APPLICATION
# works on what it built — the catalog populates, a real entry point writes, the
# value reads back, and the grant surface matches production's. The distinction
# is not academic: on 2026-08-16 a table whose CREATE had succeeded refused every
# write, which `to_regclass` calls perfect.
tombstone "environment integration proof" \
  "admin capability — the same rehearsal lane the rebuild proof above cannot open" \
  "an admin credential is provisioned here and ops/p1-integration-gate.py is restored to this line"

# ── THE ENFORCEMENT STACK'S OTHER COST: DISK (2026-08-27) ────────────────────
# On 2026-08-27 out/staging-observed held 152 orphaned temp files over 1MB
# apiece (~3.2GB) beside per-session observation files, one of them 110MB. The
# disk filled and no new worktree could be created — a hard stop on every
# session on this Mac, not a slow morning.
#
# hooks/staging-observation-tracker.py now sweeps abandoned temps before each
# of its own writes and bounds its state files in bytes. That handles the
# machine while sessions are running on it and cannot handle anything else: a
# hook only runs while a session runs, so the state file of a session that has
# ENDED is precisely the one nothing will ever touch again. Reaping by
# absence-of-activity is a question only something outside the sessions can
# ask, which is why it is a line here and not more code in the hook.
#
# IT RUNS HERE, AHEAD OF EVERYTHING THAT WRITES, and the position is the point.
# The first cut of this put it beside the hook telemetry rollup near the bottom,
# on the reasoning that both price the enforcement stack — one in latency, one
# in bytes. That is a tidy grouping and it is the wrong order: the encrypted
# backup is the LAST step and it writes a dump, so a full disk fails the backup
# and then frees 3.2GB immediately afterwards. Reclamation belongs before the
# steps that need the space, not beside the report it rhymes with.
#
# NO NEW SCHEDULED JOB, same argument the rollup makes: one more step on a chain
# that is already awake. It exits 0 whether or not it removes anything — a
# housekeeping step that reddens the chain is a check people learn to skip.
step "staging-observed prune (temp orphans + idle sessions)" \
    ./.venv/bin/python ops/staging-observed-prune.py

step "daily AI seat health" ./.venv/bin/python ops/seat-health.py

# ── ORDER 14: the two writing steps, BEFORE the exports ──────────────────────
# The cadence engine WRITES (next_action + event), so the read-only exporter
# credential above cannot run it. Both steps look for CARR_DB_JOBS_URL first
# (ORDER 19a settled the name: ONE nightly-jobs role, `carr_jobs`, not one
# credential per pipeline; the older CARR_DB_CADENCE_URL / CARR_DB_MATCHER_URL
# names stay accepted) and exit 78 — SKIP, not FAIL — when there is none. Until
# that row exists in ~/.config/carr/db.env both are armed and silent, which is
# honest. THE COMMAND LINES BELOW DO NOT CHANGE when the credential lands: the
# scripts read the environment, so the night the row appears both steps simply
# start doing work, with no new approval surface and no task-file edit.
step "cadence engine (spawn owed next actions)"      ./.venv/bin/python pipelines/cadence_engine.py --apply
step "availability matcher (digest, never sent)"     ./.venv/bin/python pipelines/availability_matcher.py

# UPSTREAM CORROBORATE (fixed 2026-08-14): writes Automation/pre-entity-watch.json
# and, in the same run, maps it into candidate_pool via map_radar_lanes' writer-
# side hook (ORDER 26b) — both need CARR_DB_JOBS_URL, same as the two writing
# steps above, so it belongs in this group. Runs before the exports and well
# before "consumers" below, because build-lead-board.py reads
# pre-entity-watch.json as one of its segment feeds — a run after that step
# would leave the board a full night stale. This step had silently never run
# nightly before: it existed only as a hand-run command, which is why
# candidate_pool saw no renewal-radar/upstream writes between 2026-08-01 and
# 2026-08-07.
# THE SEAM NAME WAS WRONG AND COST A SESSION A DAY. It read "record-backed
# pre-entity-watch document destination", which describes the OUTPUT and sent
# two readers looking for somewhere to write. The output was never the problem.
#
# WHAT ACTUALLY BLOCKS IT IS THE INPUT, and it is not a wiring job. corroborate
# joins seven weak signal families — tips, deeds, PECOS enrolments, NPPES moves,
# job posts, new licences, domains — and surfaces a person only at weight 3
# across at least 2 distinct classes. One signal is noise; two is somebody
# packing boxes. Those seven files are built by five scripts in pipelines/radar,
# and checked 2026-08-23, NONE of the five is called by this chain, by ops/ or by
# run.sh. They are hand-run tools.
#
# AND THEY CANNOT SIMPLY BE CALLED FROM HERE. build-pecos-pool.py consumes a
# quarterly CMS provider-enrolment extract a person downloads; build-license-pool
# .py consumes a licensing file filtered in browser memory so the raw statewide
# file never touches disk. Both DELETE their input immediately after running,
# because it is identifiable data about real practitioners. That discard rule is
# a privacy control somebody chose, not an oversight to automate around.
#
# So this step stays refused, and the refusal is honest. Making it nightly means
# deciding how external provider datasets are acquired and how long they may be
# kept — a question about handling other people's data, which is not a session's
# to answer.
drive_projection "upstream corroborate (pre-entity-watch + pool mapping)" \
  "upstream signal INTAKE: five hand-run pool builders, no caller, inputs are externally acquired and discarded by design" \
  ./run.sh corroborate

# FETCH ALLOWLIST (2026-08-09): regenerate the record-derived practice domains
# the egress guard unions with its KNOWN_HOSTS. Runs AFTER the writing steps so a
# client added tonight is fetchable tomorrow, and BEFORE the exports because it
# reads the same views and a stale list silently costs the weekly research slice
# a whole client. Cheap (one query pair); failing it must never stop the chain,
# and it does not — the guard falls back to KNOWN_HOSTS alone when the file is
# missing, which tightens the gate rather than loosening it.
step "fetch allowlist (client domains -> guard)"     ./.venv/bin/python ops/fetch-allowlist.py

# THE EXPORTS HAVE A CANONICAL HOME AGAIN (Joe's ruling, 2026-08-22): CARR's
# OneDrive, beside the finished deal documents this system already writes there.
# They were addressed to the Drive vault, which the 2026-08-19 cutoff retired,
# so they refused here every night for want of a destination rather than for
# anything being broken — they build in seconds and always did.
#
# NO LONGER A DRIVE PROJECTION, so it runs in the normal route rather than only
# behind the recovery envelope. CARR_EXPORT_LIVE is set on THIS STEP ALONE, with
# env, so the chain's ambient-Drive unset above still holds for every other
# child: nothing else inherits a live-write flag.
#
# SIX TARGETS, NOT SEVEN. The old label said seven and so did the loop that
# tracked this; the code declares six non-markdown targets and always has since
# the cutoff. The seventh was a markdown render, retired with the other 38 and
# counted as a survivor by mistake. The retired renders stay retired: each still
# prints RETIRED here rather than being rewritten.
#
# ── PRE-PUBLISH WAKE (defect: nightly.exports failed 9/22, 9/23, 9/24; on 9/24
# it consumed the FULL 1800s exports wall-clock with zero partial output — see
# out/nightly-runs/nightly-20260924T080221Z.log). Jev at 0.71: the OneDrive
# File Provider idling overnight. ops/onedrive-prepublish-wake.py does one
# single-attempt stat+small-read per live export target, bounded ONLY by its
# own short external wall-clock (see bin/step-timeout.zsh), so a fully dark
# provider costs at most that budget here rather than eating into the exports
# step's own, much larger, wait_for_provider() budget below. A cold read here
# is not a failure and never blocks the exports step from running.
step "onedrive pre-publish wake" ./.venv/bin/python ops/onedrive-prepublish-wake.py
#
# CAFFEINATE (same defect). Runs the exports step under `caffeinate -i -s` so
# this Mac cannot drop into an idle/system sleep state DURING the publish
# window — the 02:05 launchd fire lands ten minutes into a scheduled dark
# wake (see wait_for_provider()'s own header in exporters/common.py), and an
# idle-related sleep transition mid-publish is exactly the kind of thing that
# would leave the File Provider unresponsive with no error to catch. `-i`
# prevents idle sleep, `-s` prevents system sleep while on AC power; both end
# the instant this one command exits, so nothing outside this step is held
# awake by it.
step "exports (6 targets -> OneDrive)" caffeinate -i -s env CARR_EXPORT_LIVE=1 ./run.sh export
EXPORTS_RC=$LAST_STEP_RC

# CUTOVER READINESS (Phase 1, 2026-08-13, August 21 cutover). Right after the
# exports because it checks the compiled-rules renders those exports just
# wrote against an independent store query, plus a LIVE standing-context /
# doctrine-index call through this machine's own local-actor identity. Proves
# nightly, for whichever partner this machine belongs to, that the store-first
# boot path agrees with both the store and the fallback file — so drift shows
# up here before the 21st instead of on it. The OTHER partner's live half
# cannot be proven from this machine on purpose (Phase 1 closed the caller-
# supplied-identity hole in local-verb.mjs); this step correctly reports that
# half PARTIAL rather than failing on it — see ops/cutover-readiness.py.
# CUTOVER READINESS RETIRED — it verified a fallback that no longer exists.
# Its job was to prove, before the August 21 cutover, that the store-first boot
# path agreed with the generated compiled-rules markdown a partner still booted
# from. The cutover happened, and the 2026-08-19 cutoff retired those renders;
# ops/cutover-readiness.py reads DNA/compiled-rules-shared.md and
# 00_Context/compiled-rules-joe.md out of the vault, so on the normal path it
# was checking two files that are gone against a date that has passed. A check
# whose subject has been retired is not a gap to reopen. The script stays in the
# tree for the recovery envelope and for its history.

# CODEX HOOK SMOKE (2026-08-14). Live negative proof that Codex's PreToolUse
# hooks — this repo's OWN gate code, including guard-unattended.py — actually
# fire on an unattended `codex exec` run, not merely that the config file
# matches the tracked contract. Verified live the same day: plain `codex
# exec` SILENTLY SKIPS every hook (no denial, no warning) unless it carries
# --dangerously-bypass-hook-trust, because Codex requires persisted hook
# trust granted only interactively, which an unattended run never has. That
# flag is now on every sanctioned invocation site (bin/council-lib.sh,
# pipelines/run_codex_review.py); this step is the ongoing check that it
# keeps working, so a Codex CLI upgrade or config change that silently
# reopens the gap gets caught here rather than in production.
# tools/health-check.py reads this step's result record
# (out/codex-hook-smoke.json) and goes red if it is ever missing or older
# than 30 days — SKIP (78) on a machine with no Codex CLI installed, not a
# nightly alarm nobody can fix.
step "codex hook smoke (live negative PreToolUse proof)" ./ops/codex-hook-smoke.sh

# ── EVERY LOADED JOB MUST BE REPRODUCIBLE FROM THE REPOSITORY (loop 498) ─────
# ops/launchd-plist-parity.py was written on 2026-08-13 for a real hole and
# then called by nothing for nine days. The job that prompted it,
# com.carr.quill-key-monitor, had been registered by a one-off `launchctl
# submit`, ran a binary out of /private/tmp, and had respawned about 2000
# times. A clean plist listing and a clean repo grep both reported all-clear
# while it ran; it surfaced only because a human ran `launchctl print` during a
# manual inventory. A detector nobody runs would not have caught it either.
#
# IT RUNS HERE AND NOT IN CI for the same reason the environment gates do: it
# reads THIS Mac's live launchd domain, which a CI runner does not have. On a
# runner it would find zero CARR jobs and pass vacuously, which is worse than
# not running it.
#
# WHAT DELAYED THE WIRING, recorded because the delay was correct. Until
# 2026-08-22 the detector failed on com.carr.synthetic-load-failure — a fixture
# leaked by ops/config-as-code-selftest.py from a temp HOME that no longer
# exists. Wiring it while that sat there would have failed the chain on
# somebody else's leftover on night one, which is how a check gets muted rather
# than fixed. The job was torn down first; the detector then reported 20 loaded
# jobs, all backed. Read-only — it never loads, unloads or edits anything.
step "launchd plist parity (every loaded job reproducible)" ./.venv/bin/python ops/launchd-plist-parity.py

# CORPUS FLIP (2026-08-06): the doctrine tier is git-canonical now — corpus/ under this
# repo, not the Drive, is the source of truth. This step pushes whatever changed in git
# out to the Drive/vault/home render copies. It refuses to clobber a source-side hand-edit
# (a CONFLICT, printed by name) rather than silently overwriting it, and exits 78 — SKIP,
# not FAIL, same contract as the credential-gated steps above — when the Drive mount isn't
# up, since that is not a broken night, just an unreachable render target.
# THE CORPUS PUSH HAD A LIVE HALF AND A DEAD ONE, and the dead half was
# switching off the live one. Twelve of the fifty-four rows carry a `home:`
# prefix and address ~/.claude/skills on this Mac; the other forty-two are
# vault-rooted and retired. The tool gated the whole run on the vault resolving,
# so nothing moved at all.
#
# IT IS SAFE ON THE NORMAL PATH BECAUSE THE TOOL NOW REFUSES THE VAULT ITSELF,
# on retirement rather than on reachability — the Drive mount still resolves
# here, so a reachability probe would have said yes and rewritten retired
# doctrine renders straight back into the vault. Drive-rooted rows move only
# inside an opened recovery envelope. Measured on the normal path 2026-08-23:
# 12 rows in sync, 42 held with their reason printed, exit 0; with the envelope
# open, all 54.
step "corpus push (skills on this Mac; vault rows held)" \
  ./.venv/bin/python tools/corpus-sync.py --push

# THE CONSUMER BOARDS WERE NEVER BLOCKED ON A DESTINATION. They write inside
# this repository and read the store; what refused them was lib/record_sources
# treating a present-but-unprivileged elevated DSN as the end of the search,
# so the working exporter path was never tried. Fixed 2026-08-23. Run under the
# routine boundary's exact two credentials before unblocking: lead board 9,868
# leads, deal room 172 deals, both exit 0.
#
# THE LABEL SAID renewal-feed AND DID NOT RUN IT. `run.sh all` is lead_board
# plus deal_room; renewal_feed is separate and stays explicit-recovery until a
# canonical listing intake exists. The name is corrected rather than carried.
step "consumers (lead-board, deal-room)" ./run.sh all

# Added 2026-08-07 (loop #204, Joe's ruling): the promotion gate runs nightly so
# the renewal T1 review shortlist is fresh each morning. READ-ONLY plus one
# artifact: it prints the shortlist and writes out/lead-promote/
# renewal-t1-shortlist.json (gitignored; the brief pack's renewal-shortlist
# section reads it). It writes NO lead, NO registry row, and nothing to the
# database — T1 candidates queue for Joe's review, and only his claim at the
# board creates a lead. Runs after the consumers because the renewal feed it
# reads is rebuilt by the step above.
# Same cause, same fix, same evidence: 11 candidates, 7 waiting on contact
# info, exit 0 under the routine boundary's credentials.
step "lead promote (review shortlist, writes no leads)" ./run.sh lead-promote

# THE GRAPH IS NOT DERIVED FROM EXPORTED FILES, whatever the old label said.
# Both halves read the doctrine store and write out/Graph inside this repository.
# What actually stopped it was one raw disk read left behind when the rest moved
# to records: a listing of DNA/Clients/prospects, used to recover the exact
# casing of hand-written client dossiers so a client's node is titled after its
# dossier rather than a generated slug. That directory lived in the vault, the
# cutoff retired it, and in normal mode the root is this repository, which never
# had it — so the listing raised and took the whole graph with it. Behind a
# refusal about a destination sat a crash that needed fixing.
#
# The records carry those names themselves: 23 of 201 client rows name a detail
# file. The map is built from the rows now, which returns the stem the record
# states rather than confirming it against a file that no longer exists.
# Measured under the routine boundary's two credentials: 77 nodes, 1,895 edges,
# 178 client nodes, exit 0.
step "graph (client, lead, deal and vendor notes)" ./run.sh graph

# Added 2026-08-13 (Phase 1): the retrieval index and the system graph were
# invoked by NOTHING — not this chain, not any launchd plist, not brief_pack.py,
# not local-briefs.sh, not crontab — so `run.sh retrieve` and Graph-System were
# only ever as fresh as the last time a human remembered to run them by hand.
# Both read the freshly EXPORTED vault files (section-index walks the vault
# tree; the system graph's folder-to-folder edges come from file text) AND the
# doctrine STORE (both take the lib/record_sources store pass this Mac's venv
# can reach), so they must run AFTER the exports step and AFTER the corpus push
# above (git-canonical doctrine -> vault) — running earlier would index/graph
# yesterday's vault content. Placed here, late in the chain and right after the
# Obsidian graph render, rather than any earlier: they also want the record
# layer to have finished its own writes and reads for the night (cadence,
# matcher, cutover readiness, consumers), and there is nothing downstream of
# them in-chain that reads their output before the backup, so their exact slot
# among the late steps is not load-bearing — only "after exports+corpus push,
# before backup" is. Same $PY venv convention as every other DB-touching step
# (run.sh's own graph_system/section_index functions already use $PY, not
# plain python3, for the same psycopg reason as ORDER 29a).
# NOT A DRIVE PROJECTION ANY MORE, and it had not been one for some time. The
# builder reads the doctrine store and writes out/section-index.tsv inside this
# repository; it names no Drive root and calls no recovery boundary that could
# refuse for want of one. Exercised on the normal path 2026-08-22: 137 files
# plus 234 store documents, 3,348 rows, exit 0. The wrapper was the only thing
# refusing, which is the "repointed already, only unmarked" case the Drive
# registry keeps finding.
step "section index (retrieval-as-code layer)" ./run.sh section-index
# Same case, same evidence. Writes out/Graph-System inside this repository and
# reads the store. Exercised on the normal path 2026-08-22: 77 folders, 322
# documents linked directly, ~486 edges of which 234 are store-read, exit 0.
step "system graph (Graph-System/, derived)" ./run.sh graph-system

# Added 2026-08-15 (rule faf1b643). Joe defined the vendor relationship levels by
# COUNTABLE EVENTS so they stop being impressions — "you can email fifty people
# and have fifty relationships that do not exist" — and the rule names its own
# mechanism, v_vendor_level_suggestion. That view has existed since migration
# 0052 and NOTHING has ever read it: a finding that only lives in a record has
# not reached the partner (rule d8c9b1f0). This step reads it and prints the
# disagreements. It changes no level: the level stays a human judgment, stored
# and not computed, because a relationship can matter for reasons no event count
# can see. Exits 0 whether or not it finds drift, and 78 = SKIP with no
# credential, same contract as the other steps.
step "vendor level drift (reports, never changes a level)" ./.venv/bin/python ops/vendor-level-drift-check.py

# CONTROL-PLANE REGISTRY DRIFT, added 2026-08-23. ops/control-plane-registry-gate.py
# proves the manifest and the schema agree on a throwaway database on every
# proposed change, and said nothing about Production for as long as it existed:
# the first run against Production found 5 job definitions where the manifest
# declares 25 and a downgraded risk colour on the one enabled definition. The
# door that installs the manifest is bin/sync-control-plane-prod.sh, but a door
# only reports when a human opens it. This is the same comparison, nightly,
# under the routine jobs role in a read-only transaction — it needs no new grant
# and cannot write. It reports the one bit that role may not read rather than
# counting it as a pass. Exits 0 whether or not it finds drift, and 78 = SKIP
# with no credential, same contract as the step above.
step "control-plane registry drift (reports, never installs)" ./.venv/bin/python ops/control-plane-registry-drift.py

# RULE-ADMISSION DRIFT, added 2026-08-23, the other half of the step above. The
# admission contract is the control-plane roadmap's Phase 1 exit condition, and
# it reopens on its own: a rule taught after ops/config/rule-enforcement-map.json
# was last extended carries no contract until someone notices, which took two
# days the one time it happened. This could not run unattended until migration
# 0285, because the audit joins the authority-scoped `rule` table; that grant is
# exactly two columns, id and status, so this counts rules and cannot read one.
# Same contract as its sibling: 0 whether or not it finds a gap, 78 = SKIP.
step "rule-admission drift (reports, never admits)" ./.venv/bin/python ops/rule-admission-drift.py

# RULE-DELIVERY SHADOW, added 2026-08-23 with the load-layer tags. The 2026-08-23
# rules council required a week of running the scoped selector beside the full
# recitation before a single rule stops being recited, and a week of measurement
# has exactly one failure mode: it stops, and its silence reads as a clean
# result. That is what happened to the admission contract for months and it is
# the reason its watch two lines above exists. This one reports the same way —
# whether the recorder is still writing at all, then what it found — and takes
# the log half even with no credential, because the log is on this Mac. Same
# contract as its siblings: 0 whether or not it finds a miss, 78 = SKIP.
step "rule-delivery shadow (reports, never scopes)" ./.venv/bin/python ops/rule-delivery-shadow-watch.py

# CREDENTIAL HEALTH, added 2026-09-24. Joe is replacing every interactive login
# CARR depends on with a long-lived scoped token, so the daily question is no
# longer "did somebody re-authenticate" but "is each credential still good, and
# how long until it isn't". ops/credential-health.py probes every credential in
# ops/config/credential-inventory.v1.json (exit status / HTTP status only, never
# a command's output or a response body beyond one named non-secret field),
# writes out/credential-health.jsonl, and on a failed or expiring_soon finding
# files exactly one deduplicated CARR loop per credential through
# `./run.sh call add-loop` — the SAME finding-channel a human reading
# `run.sh health` sees under tools/health-check.py's `credentials` section.
#
# --nightly IS THE POINT OF THIS COMMENT. Without it this step would exit
# nonzero on every ordinary finding and redden the whole night for something
# the loop it just filed already says — the exact "an alarm that fires every
# day trains people to stop reading alarms" failure the four report-only steps
# above this one were each written to avoid, and the same shape as
# ops/rule-admission-drift.py: 0 on a finding, nonzero reserved for the lane
# itself being unable to run (its inventory unreadable, or zero credentials
# configured — a real defect, not a credential going stale).
#
# THE CREDENTIAL BOUNDARY (bin/routine-credential-env.sh's carr_routine_exec,
# which every step runs through) strips the child process down to
# HOME/PATH/LANG/TMPDIR plus the two named database capabilities — never a
# broader ambient credential set, by design (see that file's own header). An
# earlier version of this lane read NEON_API_KEY, CARR_MCP_PROBE_TOKEN and
# CARR_MCP_LOCAL_TOKEN straight from os.environ, so all three read `unknown`
# under every nightly run and a genuine finding on any of them would have
# gone unfiled forever (independent review, PR #1218, blocked at aa435a54).
# Fixed: those three probes now read their tokens file-first, the same way
# cloudflare-deploy-token and claude-cli-oauth-token-studio always have —
# straight off their dotenv files in ~/.config/carr/db.env and
# ~/.config/carr/mcp-tokens.env via HOME, which IS in the stripped set — and
# fall back to the (here, absent) environment variable only when the file
# itself doesn't exist. All five file-backed credentials are therefore
# checked for real under this boundary now; only a credential with no `path`
# configured at all in the inventory, or one Joe hasn't provisioned as a file
# on THIS machine yet, can still read `unknown` here — and that unknown now
# files its own deduplicated loop too, whenever the file it names IS present
# but couldn't be verified, so a never-checked credential can't stay silent.
# `run.sh health` run by hand carries the fuller ambient environment and
# reads all eleven credentials the same way; this nightly step is the same
# lane, not a second implementation of it.
step "credential health (reports, never rotates; loops on a finding)" \
     ./.venv/bin/python ops/credential-health.py --nightly

step "Monthly system cost view and spike loops" \
     ./run.sh costs --publish --alerts

# Re-sends Claude continuity receipts whose delivery was unknown, in order and
# under their original keys; refused ones are archived with the store's reason.
# The row's bound action files or clears one deduplicated loop.
step "Claude continuity spool drain" \
     ./.venv/bin/python tools/health-check.py --section claude-continuity-spool

# Authentication readback only; no model work and no interactive login.
step "Grok authentication health" \
     ./.venv/bin/python tools/health-check.py --section grok-session

step "encrypted backup -> R2"                        env CARR_DB_BACKUP_URL="$CARR_DB_BACKUP_URL" ./bin/backup-dump.sh
# CAPTURED HERE, ON THE NEXT LINE, AND THAT IS THE WHOLE POINT (fixed 2026-08-23).
# This assignment used to sit at the bottom of the portability mirror below, so
# `HC_BACKUP_RC` — the value the "backup" dead-man check is pinged with — carried
# the MIRROR's exit code and not the backup's. Two different steps: one dumps and
# encrypts the database, the other renders markdown and csv.
#
# WHAT THAT WOULD HAVE COST. A backup that FAILED while the mirror succeeded
# pinged the backup check OK. That is the 2026-08-07 corrupt-backup defect
# exactly — "a success signal that does not look at the thing it reports on is
# worse than no signal, because it is believed" — reintroduced one variable over,
# and hidden because on this Mac both steps happen to SKIP for the same absent
# credential, so the wrong answer and the right answer have been identical.
#
# LAST_STEP_RC is overwritten by every step() call, so the only safe place to
# read it is immediately after the step you mean. ops/nightly-tombstone-selftest.py
# replays this region with a fake step() and asserts that the value reaching the
# ping is the backup's, wherever this assignment happens to sit.
BACKUP_RC=$LAST_STEP_RC
# The portability mirror (Joe's ruling 2026-08-08): the readable escape hatch —
# md per doctrine doc + CSV per table, Drive + local disk, wholesale overwrite.
# NO GUARD HERE. #391 wrapped this in `if [ -n "$CARR_DB_BACKUP_URL" ]` because
# an empty DATABASE_URL used to make doctrine_mirror.py exit 2, which reads as
# FAIL. #387 fixed that in the callee on the same day: the mirror now returns
# 78 on an absent credential, the house SKIP contract, exactly as
# bin/backup-dump.sh already did. Both landed within an hour of each other and
# nobody noticed they overlapped.
#
# The callee is the right home for it. A guard here protects one caller; 78
# protects every caller, including anyone who runs the mirror by hand. Keeping
# both meant two places to read before you could answer what happens when the
# credential is missing, and the shell branch would go stale the moment the
# exit code changed again. ops/nightly-capability-skip-selftest.py asserts the
# behaviour against the mirror itself rather than against this line.
# THE PORTABILITY MIRROR HAS A DESTINATION AGAIN (Joe's ruling, 2026-08-22):
# CARR OneDrive, the same home the six business exports were given.
#
# It wrote to two places and only one of them died. The second copy, under this
# repository's out/mirror, never stopped working; the first pointed at the Drive
# vault the 2026-08-19 cutoff retired, so the step refused and BOTH copies
# stopped being written. That is worth naming: a step with a live half and a
# dead half fails whole, and the live half goes quiet without anyone deciding it
# should.
#
# WHAT IT IS FOR, which is why it earns an offsite home rather than a repo path.
# It renders the doctrine database as plain markdown and csv with a manifest —
# the copy a person can still read if Postgres is unreachable and the record
# layer cannot answer. A portability mirror that exists only on the machine that
# would be lost is not portability.
#
# ONE DEFINITION OF WHERE JOE'S FILES LIVE. The root comes from the same
# variable the exporters read (exporters/common.py), so the destination is
# stated once and this step follows it. Hard-coding the path again would recreate
# the two-homes problem in the same week it was removed.
# The default is a plain assignment rather than an inline ${VAR:-default}: the
# path contains an apostrophe in "Joe's Folder", and inside a parameter
# expansion the shell processes quotes, so that apostrophe opens a quoted run
# and swallows the rest of the file. It parsed as an unmatched brace 200 lines
# later, which is a long way from the cause.
portability_root="${CARR_EXPORT_HOME:-}"
[ -n "$portability_root" ] || portability_root="$HOME/Library/CloudStorage/OneDrive-CARR,Inc/Joe's Folder/CARR AI"
# THE CAPABILITY THIS STEP WAS PINNED TO IS ONE NO AGENT MAY EVER HOLD, so the
# step deleted itself quietly. carr_backup's password is deliberately a value
# nothing here can possess — migration 0119 says so in capitals ("THE PASSWORD IS
# NOT IN THIS FILE AND NOBODY HAS EVER SEEN IT") because that role's purpose is
# the GitHub-Actions pg_dump, whose secret lives in Actions and not on this Mac.
# So CARR_DB_BACKUP_URL is absent from ~/.config/carr/db.env, doctrine_mirror.py
# exited 78 on it every night from 2026-08-16, and 78 is the house SKIP contract,
# which reads as "nothing to do" rather than as a step that has not run in two
# weeks. INC-20260817-01 stayed open the whole time.
#
# THE MIRROR IS A RENDER, NOT A DUMP. It reads the doctrine database and writes
# markdown and csv — the same shape as the six business exports a few steps
# above, which is why the exporter capability fits it and the backup capability
# was only ever incidental.
#
# AND THE EXPORTER ROLE IS STRICTLY NARROWER, measured against production rather
# than argued: over public+ops, app_exporter_local can SELECT 236 relations and
# carr_backup 459; relations the exporter can read that the backup role cannot is
# ZERO, the reverse is 223, and the exporter holds no non-SELECT grant anywhere.
# ops/routine-credential-boundary-selftest.py holds that ceiling and was updated
# in this same commit, because the check it was making pinned a variable NAME
# where its stated intent was a privilege LIMIT.
step "portability mirror (md+csv, 2 locations)" \
  env DATABASE_URL="$CARR_DB_EXPORTER_URL" .venv/bin/python pipelines/doctrine_mirror.py \
    --out "$portability_root/Backups/portability-mirror" \
    --also "$HOME/carr-system/out/mirror"
# NO BACKUP_RC HERE ANY MORE — it is captured beside the encrypted backup above,
# which is the step the check is named after. The mirror has no registered ping
# of its own: it fails into rc_total and alarms through the whole-chain check,
# and it skips silently on an absent credential like every other 78 in this file.

# Added 2026-08-06 (loop #180): the published Outlook feeds are a ROLLING window
# (~1 month back on current publish settings), so history that scrolls out is
# gone forever and any "look back N months" backfill silently under-covers.
# This step snapshots both partners' feeds into out/calendar-archive/ (dedup by
# content, gitignored — PII stays out of git). It fetches independently of the
# Shortcuts drop files, so archiving survives a dead Shortcut. Exit 78 = SKIP
# when ~/.config/carr/calendar.env is absent, same contract as the other steps.
step "calendar archive (both partners' feeds)"       ./bin/archive-calendar.sh

# The archive is a participant-stripped schedule, not meeting-touch evidence.
# Use the attendee-aware EventKit capture after the live contact exports above.
# Unknown attendees remain queued separately; exact write failures fail the step.
step "calendar meetings to touches (EventKit)" \
  ./bin/calendar-eventkit-capture.sh --days 7

# MAIL, loop #169. The calendar lane proves a meeting happened; most follow-up
# never becomes one, so a calendar-only view of the relationship sees a fraction
# of the real contact. That gap is why vendor touch coverage sat at 0.7%.
#
# WHY IT LIVES IN THIS CHAIN RATHER THAN A 25TH LAUNCHD AGENT. The row asked for
# "launchd, weekly is enough", written before the scheduler cutover. Recurrence
# is moving to ONE adapter (loop #532), and this file's own scheduled-task note
# says anything the chain gains in future goes inside bin/nightly.sh. Apple Mail
# is a LOCAL store the cloud tick cannot reach, so the local edge is right and a
# new scheduler is not. Daily also beats the weekly the row settled for.
#
# NEITHER STEP WRITES TO THE RECORD. The extractor emits envelope metadata only,
# never message bodies (decision 745ab4aa admits derived facts); the matcher
# emits proposals to out/mail-matched.json. Exit 78 = SKIP when Mail is not
# running, because launching Joe's mail client at 02:05 with nobody at the
# machine is a side effect no capture is worth.
step "mail capture (extract + match, writes nothing)" ./bin/mail-capture.sh

# Consume canonical local contact activities. The Worker records stage evidence
# and prepares human-send-only drafts; this step has no mail send operation.
step "lead stages (evidence and approval drafts)" python3 ./bin/lead-stage-job.py

# Added 2026-08-12 (Joe's go, "put settings in the repo"): mirror the Claude Code
# permission surface — the three settings.json files carrying the allow list, the
# hook wiring, and the autoMode clearances — into claude-tree/settings/. Those
# files live in Google Drive and were tracked NOWHERE, so on 2026-08-08 a plugin
# install could delete an entire hooks block and leave five gates off for a day
# with no diff to find it by. Runs --apply on purpose: the mirror's job is
# HISTORY, and the diff surfaces in this chain's own backup commit rather than by
# failing the chain every time Joe approves a permission interactively. The
# printed diff lands in nightly.log either way. Direction is Drive -> repo (the
# opposite of sync-skills.sh) because Claude Code itself writes these files.
# SETTINGS MIRROR RETIRED, and it had already retired itself everywhere else.
# bin/sync-settings.sh opens by calling itself a retired live-settings mirror:
# the repository owns CARR's hook configuration, normal operation is repo-only,
# and the old import survives solely as an explicit reasoned recovery action.
# ops/config-as-code.py is what checks and renders the managed hooks block now.
# This chain was the last caller still treating a synced settings file as a
# thing to read on the normal path.

# Added 2026-08-06 (Joe's go, the Python-native answer to the Rust question,
# loop #218): mypy over the whole repo, lenient config in mypy.ini. The legacy
# files that were grandfathered at wiring time carried self-removing headers and
# are all gone (verified 2026-08-14), so the sweep now runs with no exemptions.
# Catches shape mistakes in data hand-offs the night they land. A red here is a
# NEW regression, never legacy noise — the baseline was green the day it was
# wired, and there is no longer any legacy bucket for a red to hide in.
step "type-check tripwire (mypy)"                    ./bin/type-check.sh

# Added 2026-08-13. The rules half of this defect is caught hourly by
# ops/rule-render-markup-check.py on the refresh; this is the records half. The
# same malformed-parameter write path serves every verb that takes prose, and
# rule c53beeaa names loop #159's absorbed `unblocks` as a known instance — so
# the record side was known to be affected and had never once been swept. A
# scanner nobody runs is not a scanner, which is precisely how c53beeaa's own
# prescribed check came to sit unexecuted from 2026-08-03 to 2026-08-13.
# Read-only (exporter role) and reports only; repair needs the absorbed text read
# first and each parameter returned to its own field, per row.
step "tool-call markup sweep (records)"              ./.venv/bin/python ops/store-markup-scan.py

# Added 2026-08-25 (loop #314's named work). tools/check.sh verifies code
# integrity (every manifest.tsv row clean vs HEAD), canonical export receipts,
# and the manifest-governed runtime fork. It is read-only. Both of its
# historical blockers are gone: the twin-copy SPLIT retired as an intentional
# runtime fork (manifest-governed) and export receipts are current. A chronically
# red check detects nothing; it is green today and wired so a new drift is seen
# by the chain that runs every night, not only by a session that remembers to
# run it.
step "code integrity (manifest rows vs HEAD)"        ./tools/check.sh

# The legacy one-page file is recovery-only. Normal dashboard delivery is the
# record-native Front Door composition (today-triage, deal-room-board, and
# loop-board), so the nightly chain must not call a missing-seam refusal for a
# replacement that already exists.
if [ "$RECOVERY" -eq 1 ]; then
  step "RECOVERY NONCANONICAL open-items dashboard (one-page view)" \
    ./.venv/bin/python generators/build-open-items-dashboard.py --recovery --reason "$RECOVERY_REASON"
else
  # A step whose command was literally `true` — it launched a process every night
  # to succeed at nothing, and then read OK in the log beside steps that had done
  # work.
  tombstone "open-items dashboard replaced by record-native Front Door" \
    "nothing — the duty moved, it was not lost: today-triage, deal-room-board and loop-board serve it record-natively" \
    "never on the normal route; the one-page file is recovery-only and the branch above still builds it"
fi

# Added 2026-08-02 (cold-session audit): the smoke canary runs IN the chain and records
# its own heartbeat. Before this it sat in the dead-man freshness list with NOTHING
# writing to it, so it read stale from 7/30 onward while passing 17/17 every time anyone
# ran it by hand — a canary whose silence was indistinguishable from its health.
#
# REPLACED 2026-08-04 (loop #178, Joe's ruling). smoke-and-record.sh runs
# mcp-server/smoke-reads.sh, which authenticates to /mcp with the legacy
# PARTNER_TOKENS bearer. That auth path was retired on purpose (5b13ed7,
# 2026-08-03), so the suite has returned 23 failed / 0 passed every night since —
# a canary that is not merely silent but structurally dead, and whose red had
# already become background noise in one day.
#
# Joe ruled against minting a machine credential to revive it, since that would
# partly undo a deliberate retirement. So the chain now runs what it CAN verify
# without a partner identity: the Worker is deployed and still refuses anonymous
# callers, and all 40 v_* views the read verbs sit on are queryable — which is
# the failure that actually bites, because migrations break views and sixteen
# landed in one day on 2026-08-02.
#
# THE FULL VERB SUITE IS NOT DEAD, IT MOVED: run ./mcp-server/smoke-reads.sh
# AFTER EVERY WORKER DEPLOY from an interactive session holding a real grant. It
# still covers transport, dispatch and answer correctness, and this probe says so
# in its own output rather than letting a green row imply more than it proves.
step "verb probe (worker gate + view sweep)"         ./.venv/bin/python ops/nightly-verb-probe.py

# ── THE FULL VERB SUITE IS BACK IN THE CHAIN, 2026-08-14 (Program 3) ──────────
# The paragraph above is now HISTORY rather than current instruction, and is kept
# because the reasoning is the load-bearing part. What changed: the credential
# whose retirement forced the 2026-08-04 removal has been replaced by a narrower
# one (PROBE_TOKENS -> a locked 'probe' profile, loop #192), it is provisioned,
# and the suite was verified honestly green — 33 passed, 0 failed, three
# profile-locked SKIPs — BEFORE this line was added. All three failures found on
# the way there were fixture drift, not regressions; the system answered
# correctly in every case. See bin/smoke-and-record.sh for the whole account.
#
# It runs AFTER the verb probe rather than instead of it. The two cover different
# failures and the cheap one should not be gated behind the expensive one: the
# probe needs no credential and sweeps all 40 views, while this suite needs the
# probe token and covers transport, dispatch and ANSWER CORRECTNESS — the class
# where a verb responds 200 with a wrong answer, which no view sweep can see.
#
# A MISSING TOKEN IS A SKIP, A REFUSED TOKEN IS AN ALARM. smoke-and-record.sh
# maps "no token configured" to 78 so an absent credential cannot alarm every
# night until someone pastes one — that pattern is how this suite was lost the
# first time. A token that EXISTS and is refused stays a failure, because it
# means post-deploy verification has silently stopped happening.
step "golden workflow suite (read verbs, answer correctness)" ./bin/smoke-and-record.sh

# PHASE 1 v2 (2026-08-13): vault drift watch --rebaseline, LAST functional step
# — after every export and every other step that touches a vault or repo file,
# so the snapshot it writes is the true post-chain state. This is what
# tomorrow's first-thing --check (top of this file) compares against: a
# registered file whose hash differs from tonight's rebaseline was touched
# OUTSIDE this chain, which is exactly the "system rewrites every file, so
# tamper would never be caught" gap this watch exists to close. Always exit 0
# (it snapshots, it does not judge) unless the vault root itself is
# unreachable.
# (rebaseline half of the retired vault drift watch — see the check above)

# ── ORDER 5: dead-man pings LAST — a ping means the whole chain above ran ────
#
# CHANGED 2026-08-07, and the reason is the same defect the backup guard had one
# layer down. This step used to run unconditionally, so the exports check and the
# backup check were pinged OK on the strength of the chain REACHING them, never
# on what they did. On 2026-08-07 a 200-byte corrupt backup pinged the backup
# check as healthy. A success signal that does not look at the thing it reports
# on is worse than no signal, because it is believed.
#
# Each check now gets the exit code of the step it is named after. hc-ping.sh
# pings /fail on anything non-zero, so a bad night ALARMS IMMEDIATELY instead of
# waiting out the dead-man grace period — the same treatment the Worker health
# check has had since ORDER 5. Exported rather than prefixed onto the call for
# the zsh scoping reason stated at CARR_EXPORT_LIVE above.
export HC_EXPORTS_RC="$EXPORTS_RC"
export HC_BACKUP_RC="$BACKUP_RC"
# The WHOLE chain's outcome, added 2026-08-10. rc_total is final here: every step
# except this one has run. Without it, only the three named steps are watched,
# and any other failing step alarms nowhere — which is how the mypy tripwire
# stayed red from 08-08 to 08-10 with every dead-man check reporting healthy.
export HC_CHAIN_RC="$rc_total"
step "healthchecks dead-man pings"               ./bin/hc-ping.sh

# ── TURN TONIGHT'S FAILURES INTO INCIDENTS (Program 3, 2026-08-14) ───────────
# LAST, and after the pings on purpose. Every step above has now recorded its
# outcome to the job-run ledger, so this sees the whole night rather than a
# prefix of it. The pings stay ahead of it because a dead-man alarm must not wait
# on anything that touches the database.
#
# WHY IT IS A STEP AND NOT A TAIL COMMAND: a step() call records ITSELF to the
# ledger, so an assess that starts failing is visible the same way every other
# step is. An assessment nobody assesses is the blind spot this whole program
# exists to remove.
#
# It opens an incident for each job whose LATEST run failed, appends a fact to
# one that is already open rather than raising a second, and counts recurrences
# on the row instead of reprinting them (0293). Since 0293 it also CLOSES a
# SEV-2 or SEV-3 job incident whose job has run green three consecutive times,
# through ops.clear_recovered_incident — a SECURITY DEFINER function that
# re-derives that sequence itself. 0117's grant is untouched: carr_jobs still
# cannot write resolved_at, and a SEV-1 still waits for a human.
#
# THIS PASS IS THE BACKSTOP, NOT THE MECHANISM. The same recovery runs inside
# every successful `ops-record.py run`, so a ticker that recovers at 09:02 is
# cleared by 09:06 rather than by the next night.
step "incident assessment (latest run of every job)"  ./.venv/bin/python tools/ops-record.py assess --environment production

# ── PR-NAMED RECOVERY EVIDENCE (WR-000019 slice S1, Obedience & Autonomy,
# 2026-08-27) ─────────────────────────────────────────────────────────────
# A merged PR naming an INC-<id> in its title or body is itself evidence the
# fix shipped, and until this step existed nothing carried that fact onto the
# incident row — recovery_evidence_ref stayed null until a human typed it in
# at close time. Same boundary as the assessment step just above: carr_jobs
# already holds 0117's grant on recovery_evidence_ref and nothing more, so this
# can attach evidence and structurally cannot resolve, reclassify, or theorise
# about the incident it attaches to. `resolve`/`sweep` remain the only close
# path. --since-hours 36 overlaps this step's own ~24h cadence on purpose, so
# a PR merged just before or after midnight is never missed by a boundary.
step "pr recovery evidence (merged PRs -> incident evidence)"  ./.venv/bin/python tools/ops-record.py pr-evidence --since-hours 36

# WHAT IS LEFT FOR A HUMAN. 0293 closes recovered lower-severity collector
# incidents from their own three-run evidence. It deliberately does not touch:
#
#   SEV-1 closure. Evidence-required and human-approved, in the grant, on
#   purpose: critical incidents are the work, not hygiene.
#
#   Anything a human opened, or anything that never recovered. The run ledger
#   cannot see what a person saw and has no standing to declare either over.
#
# WHY BREAK-GLASS. carr_jobs cannot write resolved_at or root_cause at all
# (0117's column-scoped grant), so the owner credential remains the only close
# route for those cases. The tombstone says that explicitly and records the
# restoration condition rather than claiming an unreachable automatic sweep.
tombstone "incident sweep" \
  "authority capability — 0117 withholds resolved_at and root_cause from carr_jobs, so closing needs the owner credential through break-glass" \
  "that credential is provisioned here and the break-glass close path is restored to this line"

# WHAT THE ENFORCEMENT STACK COST LAST WEEK, printed where the rest of the
# night's reporting already lands. 13 hook processes fire per Bash tool call and
# 11 per turn on this machine; until 2026-08-23 nobody could say what that cost
# or which of those gates had caught anything real, because live refusals and
# selftest fixtures were written to the same file. They are separate streams now
# and hooks/hook-meter-run.py records one line per firing, so this prints per-
# gate p95 latency, live denies, turn reopens and the 7-day trend.
#
# IT REPORTS AND NEVER ACTS, including the retire-candidate list, which is a
# list of gates to LOOK at — a gate whose deterrent is working looks exactly
# like a gate nobody needs. It exits 0 even when a provisional budget is
# exceeded (--strict is for a human at a terminal), because a chain step that
# goes red on a latency number would be the check that teaches people to stop
# reading the chain.
#
# NO NEW SCHEDULED JOB: this is the council's "piggyback on what already runs",
# and one more step on a chain that is already awake is the whole cost.
step "hook telemetry rollup (reports, never retires)"  ./.venv/bin/python ops/hook-telemetry-rollup.py --days 7

# ── RULE REPLAY + WEEKLY RE-SORT (WR-000019 slice S12, Obedience & Autonomy,
# 2026-08-27) ─────────────────────────────────────────────────────────────
# The measurement loop that makes the 219-rule rulebook a MANAGED population
# rather than a fixed one. Every night, ops/rule-replay-nightly.py scores the
# prior day's real transcripts for this repo against the rules that were live
# for each turn -- reusing hooks/rule-pack-preuse-reselection.py's own
# trigger matcher and ops/gate-lifecycle-report.py's own read_rows()/
# is_true_positive() rather than a second copy of either. It writes one row
# per (session, fired rule-signal) plus a summary row to
# out/rule-replay-nightly.jsonl, and stages -- never files -- defect
# proposals to out/rule-replay-defect-proposals.jsonl for the two named
# failure-class signatures (shadow writing-check findings, materially
# under-delegated sessions); a human morning session reviews and files them
# through record-defect with its own fresh idempotency_key, same reasoning
# as this file's own "WHAT IS LEFT FOR A HUMAN" note two steps up.
step "rule replay (nightly transcript scoring, deterministic, reports only)" \
    ./.venv/bin/python ops/rule-replay-nightly.py --since-hours 24

# The weekly re-sort report reads that ledger plus gate-lifecycle-report.py's
# own build() (imported, not duplicated) plus the JIT shadow ledger, and
# proposes exact ops/config/rule-triage.v1.json / gate-lifecycle.json edits
# for a PR -- nothing here self-applies. The Monday-only cadence is guarded
# INSIDE the script itself (same convention bin/notes-sweep-post.sh's own
# business-hours guard uses), so this step call is unconditional every
# night and a non-Monday run prints SKIP and exits 0.
step "rule re-sort (weekly proposals: gate/jit/core moves, reports only)" \
    ./.venv/bin/python ops/rule-resort-weekly.py --days 7

if [ "$seam_blocked" -gt 0 ]; then
  say "===== $seam_blocked step(s) BLOCKED on a missing canonical seam — not counted as failures ====="
fi

# The tombstone backlog rides the completion line for the same reason the other
# two counts do: a count nobody sees is a count nobody acts on. It is deliberately
# NOT folded into the blocked count. Blocked means a step ran tonight and refused;
# tombstoned means it was gated out and did not run at all. Reading a healthy
# night's blocked=0 as "the seams got built" would be exactly wrong, so the
# receipt below prints both numbers together, always, including when they are zero.
if [ "$tombstoned" -gt 0 ]; then
  say "===== $tombstoned step(s) TOMBSTONED — gated out of execution, each naming what is missing and what reopens it ====="
fi

# A timeout rides the completion line for the same reason the seam backlog does:
# a count nobody sees is a count nobody acts on. Unlike the seam backlog this one
# IS counted as a failure, so it names itself rather than trusting the reader to
# spot one TIMEOUT line in a long log.
if [ "$timed_out" -gt 0 ]; then
  say "===== $timed_out step(s) TIMED OUT — cut off for making no progress; the chain continued past them ====="
fi

# THE RECEIPT, one line, every night, whether or not any count is non-zero. A
# figure that appears only when it is non-zero cannot be read as a measurement:
# "nothing to report" and "the code that would report it never ran" look
# identical, and the second is what three silent nights in August were.
#
# It is emitted BEFORE the result line, never after, because tools/control-plane.py
# matches `nightly result: chain_ok` against this command's stdout TAIL and the
# result line has to stay the last thing said.
say "===== nightly receipt: blocked=$seam_blocked tombstoned=$tombstoned timed_out=$timed_out ====="
print -r -- "nightly receipt: blocked=$seam_blocked tombstoned=$tombstoned timed_out=$timed_out"

# The chain reached its own end. Everything below this point is bookkeeping, so
# the exit handler must stop treating this run as a death — see carr_chain_exit.
CHAIN_OUTCOME=complete

if [ "$rc_total" -eq 0 ]; then
  say "===== nightly chain OK ====="
  # The dispatcher captures stdout as immutable command evidence.  Keep this
  # concise, emitted only after the chain has completed, and independent of
  # the human-readable log line above.
  print -r -- "nightly result: chain_ok"
else
  say "===== nightly chain FINISHED WITH FAILURES — see above ====="
  print -r -- "nightly result: chain_failed"
fi

# The trim moved into carr_chain_exit, which runs on the dead path too, and it
# now happens AFTER this run has been archived to out/nightly-runs/.
exit "$rc_total"
