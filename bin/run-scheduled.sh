#!/bin/zsh
# run-scheduled.sh — make a launchd job's outcome durable, without touching the
# job. Program 4's first slice.
#
#   usage: bin/run-scheduled.sh [--heartbeat-interval SECONDS]
#                                [--also-heartbeat SERVICE]
#                                <service-key> <run-key> <command> [args...]
#
# WHY THIS EXISTS, measured 2026-08-14. `tools/ops-record.py health` read 21 of
# its 25 registered service/environment rows at "last seen never" — only
# nightly-record-layer and social-batch-weekly were healthy at all. Almost none
# of those 21 was down. They run on schedule and always have; nothing was
# listening. Only bin/nightly.sh and bin/smoke-and-record.sh ever called
# `ops-record run`, so rules-refresh, partner-ping, capture-poll, local-briefs,
# notes-sweep, recordings-purge and cc-version-sentinel reported nothing, ever.
# A failure in any of them was durable NOWHERE: it lived in a launchd log on one
# Mac and nothing turned it into an incident. Program 4's gate is "forced job
# failure is durable and actionable", and for those seven it was neither.
#
# THIS WRAPPER CLOSES THE LAUNCHD SEVEN. The rest of the 21 are the ~13
# Claude Code scheduled tasks, which are prompts rather than scripts and need a
# Stop hook instead of a wrapper — lib/scheduled_run.py and
# bin/record-scheduled-run.py are that path, already merged; only their hook
# wiring is still loose (team loop T75) — plus two staging rows nothing observes
# yet. Naming what this does NOT cover is the point: 7 of 21 fixed is the honest
# claim, and a wrapper that quietly implied 21 would be worse than none.
#
# WHY A WRAPPER AND NOT SEVEN EDITS. The seven are zsh, sh and python, written
# by different hands over months. Teaching each to record would make seven
# copies of one decision and guarantee they drift — rule a8c55a47 pointed the
# other way. One implementation; the plist is the only per-job change.
#
# ── THE PROPERTY THIS FILE MUST HOLD ─────────────────────────────────────────
# THE WRAPPER IS TRANSPARENT. It never changes what the job does, what the job
# prints, or what the job's exit code says. An observer that can turn a passing
# job red is worse than no observation, because it puts itself in the failure
# path of the thing it watches. Concretely, and each one is a check in
# ops/run-scheduled-selftest.py:
#   * the child's exit code is returned verbatim, signals included;
#   * the child's stdout and stderr are never captured, filtered or reordered —
#     they go straight to whatever launchd's StandardOutPath already pointed at;
#   * the child keeps the caller's cwd, because none of the seven plists sets
#     WorkingDirectory and every relative path in those scripts resolves against
#     it today;
#   * the child's arguments are passed through unsplit (notes-sweep's
#     `--scheduled` flag is what confines it to weekday business hours; drop it
#     and the job runs at 3am);
#   * the recorder's own output and the recorder's own failures never reach the
#     job's log or the job's exit code.
#
# RECORDING NEVER FAILS A JOB, and is never hidden either. Since 2026-08-18 the
# recorder is tools/ops-spool.py rather than tools/ops-record.py directly: a
# succeeded/skipped row goes to a local SQLite queue that a scheduled flusher
# (com.carr.run-spool-flush) replays through the real ops-record in batches, so
# ~1000 heartbeat recordings a day stop holding the Neon database awake around
# the clock (the 2026-08-18 audit's measured leak), and a row survives an
# unreachable or schema-drifted ledger instead of being lost — 3,485 rows were
# dropped that way over 2026-08-17/18. A failed/timed_out/cancelled row is
# still tried against the ledger DIRECTLY first (an incident should not wait
# 30 minutes) and queues only when that write fails. recorder_exit=0 now means
# the row is DURABLE — landed or queued with a scheduled path to ops.run —
# and nonzero still means the line below is the only trace. If nothing could
# be recorded the service reads `unknown` at the next health look rather than
# staying green — ops.v_service_environment_health derives health from the
# latest observation and its freshness and stores no health anywhere. Silence
# is visible by design; that is Program 3's load-bearing decision and this
# file leans on it rather than working around it.
#
# THE PROVENANCE LINE IS THE TESTED SURFACE. Every run appends exactly one line
# to out/run-scheduled.log carrying the state this script derived and the exact
# recorder argv it built. ops/run-scheduled-selftest.py asserts against that
# line and nothing else — no injectable recorder, no dry-run flag, no mock. On
# 2026-08-14 the settings-change gate shipped two defects and both were the same
# shape: a test that exercised a path production never takes (team loop T75).
# The line the suite reads is the line the 02:05 run writes.
#
# ── THROTTLING FOR HIGH-FREQUENCY JOBS (Program 4 follow-up) ─────────────────
# partner-ping wakes every 2 minutes and capture-poll every 5; recording every
# fire would be ~1000 rows/day of noise from two channels that are usually
# fine. Both flags below are entirely optional and inert unless a caller
# passes them, so every existing invocation — all seven current plists — is
# untouched byte-for-byte.
#
#   --heartbeat-interval SECONDS   Record a SUCCEEDED row for this job's own
#                                  key at most once per this many seconds (a
#                                  state file under out/run-scheduled-state/).
#                                  Every non-succeeded outcome — failed,
#                                  skipped, timed_out, cancelled — is still
#                                  recorded immediately and CLEARS the
#                                  throttle, so a recovery posts on the very
#                                  next fire instead of waiting out a stale
#                                  interval. Omitted (the default): record
#                                  every fire, which is every existing job's
#                                  actual behavior today.
#   --also-heartbeat SERVICE       On the SAME wake, also record an
#                                  independent SUCCEEDED row for a second,
#                                  unrelated service — riding this job's cron
#                                  slot as the cheapest true signal that the
#                                  Mac is awake and launchd is actually firing
#                                  agents (PROP-010's local edge node).
#                                  Subject to the SAME --heartbeat-interval,
#                                  tracked independently of the primary job.
#                                  Always its own SECOND provenance line
#                                  (key=launchd.heartbeat), so it is asserted
#                                  on exactly the way the primary line is —
#                                  the "provenance line is the tested surface"
#                                  property above extends to it unchanged.
#
# ── THE RECEIPT THIS RUN MINTS FOR ITSELF (2026-09-11) ──────────────────────
# Gate Zero's `step:scheduler-active-receipt` clause, read by
# mcp-server/src/gate-zero-seam-readers.v5.js, wants an ops.run row bound to a
# receipt. Measured against production 2026-09-11: 21,894 rows written by this
# wrapper, none of them carrying an evidence_ref. The clause was not merely
# unmet, it was unsatisfiable by construction.
#
# THIS SCRIPT MINTS THE RECEIPT. There is no flag, no path, no environment
# variable and no channel of any kind by which a caller, a plist or the child
# can supply, pre-seed or point at one. The first draft of this change took a
# `--evidence-ref-file PATH` and promoted whatever that file held, which made a
# stale or fabricated file indistinguishable from a receipt minted during this
# run — the one thing a receipt exists to rule out. A binding whose evidence the
# bound party supplies is not a binding.
#
# WHAT A RECEIPT IS, byte for byte:
#
#     carr-run-receipt:v1:<minted-at>:<nonce>:<run-key-hash>
#
#   minted-at     YYYYMMDDTHHMMSS.mmmZ, off this process's clock AFTER the
#                 child exited, so a receipt cannot predate the run it speaks
#                 for. The reader requires it STRICTLY AFTER the row's
#                 started_at, which is what makes a receipt left on disk by an
#                 earlier run bind nothing. MILLISECONDS, and not because
#                 precision is pretty: started_at is stamped to the second, so a
#                 job that begins and ends inside one second would tie against
#                 its own dispatch and bind nothing at all — which is most of
#                 the fleet. On a zsh without the datetime module the fraction
#                 is `.000`, those runs tie, and they bind nothing; that is
#                 stated rather than papered over.
#   nonce         16 hex from /dev/urandom, minted here. It is what makes THIS
#                 run's receipt distinct from the last run's under the same key,
#                 so the reader can tell one dispatch from another.
#   run-key-hash  the first 32 hex of sha256(run key). A HASH, not the key: the
#                 run key is caller text, and caller text must never travel
#                 verbatim into ops.run.evidence_ref or into the provenance line
#                 at the bottom of this file. The reader recomputes this hash
#                 from the row's own run_key and requires equality, so a receipt
#                 minted for a different job binds nothing.
#
# THE FILE IS THIS SCRIPT'S OWN, in a directory nobody outside this script can
# name: out/run-scheduled-receipts/<service-hash>.<run-key-hash>.receipt under
# the repository this file is installed in ($REPO, which is ${0:A:h:h}). There is
# no argument and no environment variable that moves it, and a variable that
# tries to is refused rather than obeyed. The leaf is created with one
# O_CREAT|O_EXCL|O_NOFOLLOW open, and that descriptor — never the name a second
# time — is what is checked, written and read back, so nothing can be
# substituted between the check and the write. Anything already at that path
# other than a plain regular file of ours with a single link (a symlink, a FIFO,
# a device, a directory, a hard link) refuses under its own code and is left
# untouched. One file per service/run-key pair, so the directory cannot grow
# without bound.
#
# REJECT, NEVER REPAIR. A service key or run key carrying a space, a carriage
# return, a newline, a NUL or any other byte outside [A-Za-z0-9:._-] mints NO
# receipt, and is not first stripped down to an acceptable shape: the earliest
# draft of the guard normalized with `tr -d '[:space:]'`, which turned
# `--state failed --exit-code 1` into `--statefailed--exit-code1` — a token the
# whitelist then accepted. Stripping before matching is how a guard fails open.
#
# IT CANNOT FAIL A JOB. Every refusal above records exactly the row this wrapper
# recorded before receipts existed, and the child's exit code, output, arguments
# and working directory are untouched in all of them.
#
# No flag touches the job itself: the child still runs exactly once,
# unmodified, and nothing here can change what it does, prints, or returns —
# only what this script decides to WRITE afterward.

set -u

EX_USAGE=64

HEARTBEAT_INTERVAL=0
ALSO_HEARTBEAT=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --heartbeat-interval)
      if [ "$#" -lt 2 ]; then
        print -ru2 -- "usage: --heartbeat-interval requires a value"
        exit $EX_USAGE
      fi
      HEARTBEAT_INTERVAL="$2"; shift 2 ;;
    --also-heartbeat)
      if [ "$#" -lt 2 ]; then
        print -ru2 -- "usage: --also-heartbeat requires a value"
        exit $EX_USAGE
      fi
      ALSO_HEARTBEAT="$2"; shift 2 ;;
    --) shift; break ;;
    *) break ;;
  esac
done

if [ $# -lt 3 ]; then
  print -ru2 -- "usage: bin/run-scheduled.sh [--heartbeat-interval SECONDS] [--also-heartbeat SERVICE] <service-key> <run-key> <command> [args...]"
  print -ru2 -- "  e.g. bin/run-scheduled.sh nightly-record-layer rules.refresh /bin/zsh {{REPO}}/bin/refresh-rules.sh"
  exit $EX_USAGE
fi

SERVICE="$1"; shift
RUN_KEY="$1"; shift

REPO="${0:A:h:h}"

# XPC_SERVICE_NAME belongs to the process launchd starts directly.  A nested
# interpreter may replace it with `0`, so preserve the identity only after the
# loaded service, this wrapper PID, and the complete canonical fleet tuple all
# agree.  Ambient variables and a forged XPC name are not evidence.
unset CARR_RUN_SCHEDULED_XPC_SERVICE_NAME
unset CARR_RUN_SCHEDULED_LAUNCHD_PID
unset CARR_RUN_SCHEDULED_FLEET_SELF_CLAIM
wrapper_pid=$$
fleet_self_proof_refused=0
if [ "$SERVICE" = "fleet-sync" ] \
    && [ "${XPC_SERVICE_NAME:-}" = "com.carr.fleet-sync" ]; then
  # This is not authorization. It ensures malformed tuple/PID proof fails
  # closed in the child instead of degrading to an external self-unload.
  export CARR_RUN_SCHEDULED_FLEET_SELF_CLAIM=1
fi
fleet_loaded_wrapper=0
if [ "${CARR_RUN_SCHEDULED_FLEET_SELF_CLAIM:-}" = 1 ] \
    && launchctl print "gui/$UID/com.carr.fleet-sync" 2>/dev/null \
       | grep -Eq "^[[:space:]]*pid = ${wrapper_pid}[[:space:]]*$"; then
  fleet_loaded_wrapper=1
fi
fleet_tuple_valid=0
if [ "$RUN_KEY" = "fleet.sync" ] \
    && [ "$#" -eq 2 ] \
    && [ "$1" = "/bin/zsh" ] \
    && [ "${2:A}" = "$REPO/bin/fleet-sync.sh" ]; then
  fleet_tuple_valid=1
fi
if [ "${CARR_RUN_SCHEDULED_FLEET_SELF_CLAIM:-}" = 1 ]; then
  if [ "$fleet_loaded_wrapper" -eq 1 ] && [ "$fleet_tuple_valid" -eq 1 ]; then
    export CARR_RUN_SCHEDULED_XPC_SERVICE_NAME=com.carr.fleet-sync
    export CARR_RUN_SCHEDULED_LAUNCHD_PID=$wrapper_pid
  else
    fleet_self_proof_refused=1
  fi
fi

LOG="$REPO/out/run-scheduled.log"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY=python3

STARTED="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

# ── the job itself ───────────────────────────────────────────────────────────
# No redirection of any kind. Whatever the caller gave this process for stdout
# and stderr is what the child gets, which is how the existing per-job launchd
# logs keep working unchanged.
if [ "$fleet_self_proof_refused" -eq 1 ]; then
  print -ru2 -- "run-scheduled: ACTIVE-SELF PROOF REFUSED — launchd names this as"
  print -ru2 -- "    com.carr.fleet-sync, but the loaded wrapper PID and exact"
  print -ru2 -- "    fleet-sync/fleet.sync canonical command tuple do not all agree"
  print -ru2 -- "    remedy: run the sanctioned external config-as-code install to restore"
  print -ru2 -- "    and reload the canonical plist; supplied child was not executed"
  rc=1
else
  "$@"
  rc=$?
fi

# Captured HERE, not at recording time: the spool may replay this row into the
# ledger half an hour from now, and without an explicit end stamp ops-record
# would derive ended_at from the clock at INSERT, inflating every spooled run's
# elapsed window by the queue delay.
ENDED="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

# ── what that exit code MEANS ────────────────────────────────────────────────
# 78 = EX_CONFIG: the job ran, found a credential or setting it needs is absent,
# wrote nothing and said so. That is a SKIP, not a failed night — the same
# convention bin/nightly.sh and bin/smoke-and-record.sh already hold, and it
# exists because an alarm that fires every night until someone pastes a token
# trains both partners to stop reading alarms. That is precisely how the smoke
# suite was lost the first time.
#
# 137 (SIGKILL) and 143 (SIGTERM) are the machine's doing, not the job's. The
# documented 2026-08-14 failure is this Mac sleeping through a scheduled window;
# recording "failed" for a job the OS killed sends whoever reads it hunting for
# a bug in a job that does not have one.
case $rc in
  0)         state=succeeded ;;
  78)        state=skipped   ;;
  124|137)   state=timed_out ;;
  143)       state=cancelled ;;
  *)         state=failed    ;;
esac

# ops.run's own constraint `a_failure_names_its_class` (migration 0115) requires
# a failure_class for exactly 'failed' and 'timed_out' and permits none
# elsewhere. Deriving it here rather than letting the database refuse the insert
# is what keeps a recording failure from ever being the job's problem.
fclass=()
case $state in
  failed|timed_out) fclass=(--failure-class "exit_$rc") ;;
esac

# Inherited when a chain or a deploy exported one, so a job a nightly chain
# launched traces WITH that chain instead of starting a lone journey — the same
# reason bin/smoke-and-record.sh reads this variable.
corr=()
[ -n "${CARR_CORRELATION_ID:-}" ] && corr=(--correlation "$CARR_CORRELATION_ID")

mkdir -p "$REPO/out" 2>/dev/null

# The throttle stamp directory, and ONLY the throttle stamp directory. It
# holds one epoch-seconds file per heartbeat service/run-key pair so a
# high-frequency succeeded row records at most once an interval, and a test
# or a drill points it at a scratch path so it never stamps production
# state. It does NOT reach the receipt below: a stamp is a timestamp, and
# evidence is not something a caller gets to place.
STATE_DIR="${CARR_RUN_SCHEDULED_STATE_DIR:-$REPO/out/run-scheduled-state}"

# ── THE RECEIPT THIS RUN MINTS FOR ITSELF ────────────────────────────────────
# Minted HERE, after the child exited and after ENDED was stamped, out of this
# process's own clock and this machine's own entropy. Nothing a caller, a plist
# or the child writes reaches it — there is no argument and no path to give.
#
# THE DIRECTORY IS NOT SELECTABLE, and that is the whole of the 2026-09-12
# correction. It used to be $CARR_RUN_SCHEDULED_STATE_DIR/receipts, so whoever
# set that variable chose the directory this wrapper validated and wrote in —
# and choosing the directory is choosing the parents, which is choosing the
# file. It is now derived from THIS SCRIPT'S OWN resolved location and nothing
# else: $REPO is ${0:A:h:h}, symlinks already resolved, so the answer is fixed
# by where this file is installed rather than by anything the environment says.
# Deliberately NOT `git rev-parse --show-toplevel`: rev-parse answers about the
# CALLER'S working directory, which every launchd job inherits from whoever
# started it and which a child may have changed, and it needs a git checkout a
# deployed copy may not be — a caller-controlled input wearing a fixed name.
# CARR_RUN_SCHEDULED_STATE_DIR still redirects the heartbeat THROTTLE STAMP
# below, which is a timestamp rather than evidence; it does not move the
# receipt, and a variable that names the receipt directory outright is refused
# rather than honoured, so tampering is visible instead of silently obeyed.
#
# ONE EXCLUSIVE CREATE, THEN ONE DESCRIPTOR. The leaf is created with
# O_CREAT|O_EXCL|O_NOFOLLOW (zsh/system's sysopen), and every step after it —
# fstat, write, rewind, read back — addresses fd 3. The pathname is never
# resolved a second time, so there is no interval between checking a name and
# using it: the validate-then-open substitution, the FIFO swapped in after the
# check, the symlink planted between the two, and the hard link that shares an
# inode with a stranger are all closed by the same property. What was inspected
# and what the bytes landed in are one open file, not one name looked up twice.
#
# EVERY REFUSAL IS SILENT TO THE JOB AND NAMED IN THE LOG. This is an
# observation channel bolted to a wrapper whose founding property is that it
# cannot break the thing it watches, so no refusal touches the child's exit
# code, output or arguments, and the recorder is called with exactly the
# arguments it received before receipts existed. What changed on 2026-09-12 is
# that a refusal is no longer anonymous: the provenance line carries
# receipt_code=<one of RECEIPT_CODES>, so "this job records no evidence" and
# "something moved the directory under us" stop looking identical.
RECEIPT_CODES=(
  none key_shape dir_not_selectable dir_not_fixed dir_unusable leaf_symlink
  leaf_not_regular leaf_hard_linked leaf_occupied open_refused fd_identity
  echo_differs no_clock no_nonce no_hasher no_sysopen mint_shape
  forbidden_word unregistered
)

evid=()
evidence_ref=""
receipt_code=none
RECEIPT_DIR=""
RECEIPT_FILE=""

# The closed union of privileged words the 2026-09-11 standing rule names, swept
# as a lowercase substring against the minted token AND against the refusal code
# before either can reach the recorder or the provenance line. Both pass by
# construction today — the token is a fixed prefix, digits and hex; the codes are
# this file's own vocabulary — and that is exactly the point: the day someone
# puts a caller's own run key back into the token, or names a code `passing`,
# THIS refuses it rather than a reviewer catching it on round nine.
RECEIPT_FORBIDDEN=(
  allow commit prompt suppress release read covered drafted proposed queued
  healthy passing ok pass satisfied complete admitted resumed attended verified
  present equivalent operational active green favorable joins_exactly
  coverage_complete would_ _if_authoritative
)

# ONE VALUE, WHOLE, AGAINST THE WHITELIST — no line splitting, no stripping, no
# repair. `${v//[set]/}` deletes every admitted byte and whatever survives is a
# byte the shape does not admit; one such byte refuses the value. A carriage
# return, a newline, a tab, a space and a NUL all survive that deletion, which
# is what makes this REJECT rather than normalize. grep would have read the
# first line of a two-line value and matched it, which is the same fail-open
# shape as stripping.
receipt_token_ok() {
  local v="$1"
  [ -n "$v" ] || return 1
  [ "${#v}" -le 128 ] || return 1
  case "$v" in [A-Za-z0-9]*) ;; *) return 1 ;; esac
  [ -z "${v//[A-Za-z0-9:._-]/}" ] || return 1
  return 0
}

# sha256 of $1's bytes, truncated to $2 hex characters. No hasher on the machine
# is a refusal like any other, not a reason to fall back to something weaker.
receipt_hash() {
  if (( $+commands[shasum] )); then
    print -rn -- "$1" | shasum -a 256 2>/dev/null | cut -c1-"$2"
  elif (( $+commands[sha256sum] )); then
    print -rn -- "$1" | sha256sum 2>/dev/null | cut -c1-"$2"
  fi
}

# The ordered procedure, and the order is the security property: identifiers,
# then the fixed directory, then the token, then what is already at the leaf,
# then ONE create, then the descriptor's own identity, then write and read back
# through that descriptor. Each step's refusal names itself and returns.
mint_receipt() {
  local v root candidate lowered word minted_at now_real now_frac nonce
  local run_key_hash service_hash got gotn wrote
  local -A lst fst dlnk

  # 1 ── the two identifiers, whole, against the whitelist
  if ! receipt_token_ok "$SERVICE" || ! receipt_token_ok "$RUN_KEY"; then
    receipt_code=key_shape
    return 1
  fi

  # 2 ── NO VARIABLE NAMES THE DIRECTORY. Set one and this refuses; there is no
  #      spelling of an override that works, and the attempt is in the log.
  for v in CARR_RUN_SCHEDULED_RECEIPT_DIR CARR_RUN_SCHEDULED_RECEIPT_ROOT \
           CARR_RUN_SCHEDULED_RECEIPT_FILE CARR_RUN_SCHEDULED_RECEIPTS; do
    if [ -n "${(P)v:-}" ]; then
      receipt_code=dir_not_selectable
      return 1
    fi
  done

  # 3 ── the fixed directory, derived from this script's own location. zsh's
  #      sysopen and zstat are how a shell opens a file exclusively and stats a
  #      DESCRIPTOR; a zsh without them refuses rather than falling back to a
  #      path-addressed write that re-resolves the name.
  zmodload zsh/system 2>/dev/null
  zmodload zsh/stat 2>/dev/null
  if (( ! $+builtins[sysopen] || ! $+builtins[zstat] )); then
    receipt_code=no_sysopen
    return 1
  fi
  root="$REPO/out"
  root="${root:A}"
  RECEIPT_DIR="$root/run-scheduled-receipts"
  # lstat FIRST, per component: `:A` resolution below proves the whole chain
  # under the resolved root carries no symlink, and the -L test says so about
  # this component without following it. out/ itself may legitimately be the
  # install's own symlink — every worktree's out/ is one — which is why the root
  # is resolved once, deliberately, and everything beneath it is not.
  if [ -L "$RECEIPT_DIR" ]; then
    receipt_code=dir_not_fixed
    return 1
  fi
  if [ ! -e "$RECEIPT_DIR" ]; then
    mkdir -m 700 -p -- "$RECEIPT_DIR" 2>/dev/null
  fi
  if [ -L "$RECEIPT_DIR" ] || [ "${RECEIPT_DIR:A}" != "$RECEIPT_DIR" ]; then
    receipt_code=dir_not_fixed
    return 1
  fi
  if [ ! -d "$RECEIPT_DIR" ] || [ ! -w "$RECEIPT_DIR" ]; then
    receipt_code=dir_unusable
    return 1
  fi
  # THE THIRD SPELLING OF THE FIXED-DIRECTORY RULE, and the only portable one.
  # A symlink's OWN mode is 0777 on Linux and umask-dependent on macOS, so one
  # lstat whose mode bits are then read as the directory's refuses a symlinked
  # receipt directory on Linux and accepts it on macOS. Say it outright instead:
  # lstat answers only whether this is a link, and the ownership and mode below
  # are read from the directory the write actually lands in.
  if ! zstat -L -H dlnk -- "$RECEIPT_DIR" 2>/dev/null; then
    receipt_code=dir_unusable
    return 1
  fi
  if (( (dlnk[mode] & 8#170000) == 8#120000 )); then
    receipt_code=dir_not_fixed
    return 1
  fi
  if ! zstat -H lst -- "$RECEIPT_DIR" 2>/dev/null; then
    receipt_code=dir_unusable
    return 1
  fi
  # OURS, AND NOT WRITABLE BY ANYONE ELSE. This is what makes the leaf checks
  # below sufficient: nobody but this user can put anything in this directory,
  # so a leaf owned by a stranger is not a case that has to be handled.
  if [ "$lst[uid]" -ne "$UID" ] || (( (lst[mode] & 8#22) != 0 )); then
    receipt_code=dir_unusable
    return 1
  fi

  # 4 ── the token. ONE READ of the clock, snapshotted: $EPOCHREALTIME advances
  #      on every access, so reading it twice gives the seconds of one instant
  #      and the milliseconds of another. strftime runs inside a subshell that
  #      EXPORTS TZ, because the module formats in local time and a receipt in
  #      local time compared against a UTC started_at is a silent hour of drift.
  zmodload zsh/datetime 2>/dev/null
  minted_at=""
  if [ -n "${EPOCHREALTIME:-}" ]; then
    now_real="$EPOCHREALTIME"
    now_frac="${now_real#*.}000"
    minted_at="$( (export TZ=UTC; strftime '%Y%m%dT%H%M%S' "${now_real%%.*}") 2>/dev/null ).${now_frac[1,3]}Z"
  else
    minted_at="$(date -u '+%Y%m%dT%H%M%S').000Z"
  fi
  # The shape is checked rather than assumed: a strftime that printed nothing
  # would otherwise leave a stub like `.000Z` looking like a timestamp.
  case "$minted_at" in
    [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9].[0-9][0-9][0-9]Z) ;;
    *) receipt_code=no_clock
       return 1 ;;
  esac
  nonce="$( (LC_ALL=C od -An -tx1 -N8 /dev/urandom) 2>/dev/null | tr -d ' \n')"
  if [ "${#nonce}" -ne 16 ]; then
    receipt_code=no_nonce
    return 1
  fi
  run_key_hash="$(receipt_hash "$RUN_KEY" 32)"
  service_hash="$(receipt_hash "$SERVICE" 16)"
  if [ "${#run_key_hash}" -ne 32 ] || [ "${#service_hash}" -ne 16 ]; then
    receipt_code=no_hasher
    return 1
  fi
  candidate="carr-run-receipt:v1:$minted_at:$nonce:$run_key_hash"
  lowered="${candidate:l}"
  for word in $RECEIPT_FORBIDDEN; do
    case "$lowered" in
      *$word*) receipt_code=forbidden_word
               return 1 ;;
    esac
  done
  if ! receipt_token_ok "$candidate"; then
    receipt_code=mint_shape
    return 1
  fi

  # 5 ── WHAT IS ALREADY AT THE LEAF DECIDES, by lstat, before anything opens.
  #      The path carries only the two hashes, so no caller byte is in it. A
  #      plain regular file of ours with one link is the only thing this wrapper
  #      itself leaves behind, and the only thing it will clear: it is unlinked
  #      (which follows no symlink and opens no FIFO) and a fresh inode created
  #      below. Anything else — a symlink, a FIFO, a device, a socket, a
  #      directory, or a regular file someone else also has a name for — is
  #      refused under its own code and left exactly as it was.
  RECEIPT_FILE="$RECEIPT_DIR/$service_hash.$run_key_hash.receipt"
  if [ -L "$RECEIPT_FILE" ]; then
    receipt_code=leaf_symlink
    return 1
  fi
  if [ -e "$RECEIPT_FILE" ]; then
    if ! zstat -L -H lst -- "$RECEIPT_FILE" 2>/dev/null; then
      receipt_code=leaf_not_regular
      return 1
    fi
    if (( (lst[mode] & 8#170000) != 8#100000 )); then
      receipt_code=leaf_not_regular
      return 1
    fi
    if [ "$lst[nlink]" -ne 1 ]; then
      receipt_code=leaf_hard_linked
      return 1
    fi
    rm -f -- "$RECEIPT_FILE" 2>/dev/null
    if [ -e "$RECEIPT_FILE" ] || [ -L "$RECEIPT_FILE" ]; then
      receipt_code=leaf_occupied
      return 1
    fi
  fi

  # 6 ── ONE CREATE, and it is the validation: O_EXCL means this inode did not
  #      exist a moment ago, O_NOFOLLOW means no symlink was traversed to reach
  #      it, and losing the race to anyone else refuses instead of writing.
  if ! sysopen -r -w -o creat,excl,nofollow -m 600 -u 3 -- "$RECEIPT_FILE" 2>/dev/null; then
    receipt_code=open_refused
    return 1
  fi

  # 7 ── AND THE DESCRIPTOR ITSELF IS WHAT IS CHECKED: fstat(2) on fd 3, not a
  #      second look at the name. A fresh, empty, single-link regular file of
  #      ours is the only thing step 6 can have produced; anything else means
  #      the assumption was wrong and nothing is written.
  if ! zstat -f 3 -H fst 2>/dev/null; then
    exec 3>&-
    receipt_code=fd_identity
    return 1
  fi
  if (( (fst[mode] & 8#170000) != 8#100000 )) || [ "$fst[nlink]" -ne 1 ] \
      || [ "$fst[uid]" -ne "$UID" ] || [ "$fst[size]" -ne 0 ]; then
    exec 3>&-
    receipt_code=fd_identity
    return 1
  fi

  # 8 ── WRITE, REWIND AND READ BACK THROUGH THAT SAME DESCRIPTOR, and require
  #      equality in content AND in length. syswrite is write(2) and loops until
  #      the whole value is out, so a short write is visible as a count rather
  #      than as a truncated receipt. Nothing is trimmed to make the two agree.
  got=""
  if ! syswrite -o 3 -c wrote -- "$candidate"$'\n' 2>/dev/null; then
    exec 3>&-
    receipt_code=echo_differs
    return 1
  fi
  if ! sysseek -u 3 0 2>/dev/null || ! sysread -c gotn -i 3 got 2>/dev/null; then
    exec 3>&-
    receipt_code=echo_differs
    return 1
  fi
  exec 3>&-
  if [ "$got" != "$candidate"$'\n' ] || [ "$wrote" -ne $(( ${#candidate} + 1 )) ] \
      || [ "$gotn" -ne $(( ${#candidate} + 1 )) ]; then
    receipt_code=echo_differs
    return 1
  fi

  evidence_ref="$candidate"
  evid=(--evidence-ref "$candidate")
  receipt_code=none
  return 0
}

mint_receipt || true

# A code is an export too: it must be one this file registers, and it must carry
# none of the privileged words. A future code that fails either test prints
# `unregistered` rather than shipping a word the standing rule closes.
if [ -z "${RECEIPT_CODES[(r)$receipt_code]:-}" ]; then
  receipt_code=unregistered
fi
receipt_code_lowered="${receipt_code:l}"
for receipt_word in $RECEIPT_FORBIDDEN; do
  case "$receipt_code_lowered" in
    *$receipt_word*) receipt_code=unregistered ;;
  esac
done

# ── throttle a high-frequency SUCCEEDED row ──────────────────────────────────
# Inert when HEARTBEAT_INTERVAL is 0 (the default, and every existing job's
# real invocation today): should_record is always 1 below, so this is a no-op
# for the six jobs that never pass the flag.
STATE_FILE="$STATE_DIR/$SERVICE.$RUN_KEY.last-success"
should_record=1
if [ "$state" = "succeeded" ] && [ "${HEARTBEAT_INTERVAL:-0}" -gt 0 ] 2>/dev/null; then
  mkdir -p "$STATE_DIR" 2>/dev/null
  if [ -f "$STATE_FILE" ]; then
    last="$(cat "$STATE_FILE" 2>/dev/null || print -r -- 0)"
    now_epoch="$(date -u +%s)"
    elapsed=$(( now_epoch - last ))
    [ "$elapsed" -lt "$HEARTBEAT_INTERVAL" ] && should_record=0
  fi
fi

if [ "$should_record" -eq 1 ]; then
  set -A argv "$PY" "$REPO/tools/ops-spool.py" run \
    --service "$SERVICE" --key "$RUN_KEY" --state "$state" \
    --exit-code "$rc" --started-at "$STARTED" --ended-at "$ENDED" \
    --source-kind wrapper --source-ref bin/run-scheduled.sh \
    --detail "$RUN_KEY exited $rc" "${fclass[@]}" "${corr[@]}" "${evid[@]}"
  "${argv[@]}" >> "$LOG" 2>&1
  recorder_exit=$?
  record_action=recorded
  # Stamp ONLY when the row is durably captured (recorder_exit 0: landed
  # directly, or queued in the spool with a scheduled path to ops.run).
  # Stamping on the mere attempt meant a failed recording silenced the next
  # interval's fires too — observed live 2026-08-14: a dev-iteration
  # selftest's failed write stamped carr-local-edge-node and the first real
  # heartbeat came up 'throttled' against a row that never existed.
  if [ "$recorder_exit" -eq 0 ] && [ "$state" = "succeeded" ] && [ "${HEARTBEAT_INTERVAL:-0}" -gt 0 ] 2>/dev/null; then
    mkdir -p "$STATE_DIR" 2>/dev/null
    date -u +%s > "$STATE_FILE" 2>/dev/null
  fi
else
  argv=()
  recorder_exit=throttled
  record_action=throttled
fi

# A non-succeeded outcome always clears the throttle, so the recovery — the
# next succeeded fire — posts immediately rather than waiting out a stale
# interval. A silent recovery is exactly the kind of silence this ledger
# exists to make visible.
if [ "$state" != "succeeded" ] && [ "${HEARTBEAT_INTERVAL:-0}" -gt 0 ] 2>/dev/null; then
  rm -f "$STATE_FILE" 2>/dev/null
fi

# One line, always, whatever happened. The run key and service are the job's own
# identifiers, the detail is a key and a number: nothing here is a secret and
# nothing is client content. record_action sits BEFORE recorder_exit and argv
# stays the trailing field exactly as before, so every existing regex-based
# check against this line (name=value, argv= to end of line) is unaffected —
# it only ever gains the new field, never loses or reorders an old one.
print -r -- "$(date -u '+%Y-%m-%dT%H:%M:%SZ') run-scheduled key=$RUN_KEY service=$SERVICE child_exit=$rc state=$state record_action=$record_action recorder_exit=$recorder_exit evidence_ref=${evidence_ref:-none} receipt_code=$receipt_code argv=${argv[*]}" >> "$LOG"

# ── an independent heartbeat riding this same wake (carr-local-edge-node) ────
# Deliberately NOT gated on the primary job's own outcome above: a broken
# partner-ping query says nothing about whether the Mac is awake, and tying
# the edge node's presence signal to one unrelated script's health would make
# it only as reliable as that script.
if [ -n "$ALSO_HEARTBEAT" ]; then
  HB_RUN_KEY="launchd.heartbeat"
  HB_STATE_FILE="$STATE_DIR/$ALSO_HEARTBEAT.$HB_RUN_KEY.last-success"
  hb_should_record=1
  if [ "${HEARTBEAT_INTERVAL:-0}" -gt 0 ] 2>/dev/null && [ -f "$HB_STATE_FILE" ]; then
    hb_last="$(cat "$HB_STATE_FILE" 2>/dev/null || print -r -- 0)"
    hb_now="$(date -u +%s)"
    hb_elapsed=$(( hb_now - hb_last ))
    [ "$hb_elapsed" -lt "$HEARTBEAT_INTERVAL" ] && hb_should_record=0
  fi
  if [ "$hb_should_record" -eq 1 ]; then
    set -A hb_argv "$PY" "$REPO/tools/ops-spool.py" run \
      --service "$ALSO_HEARTBEAT" --key "$HB_RUN_KEY" --state succeeded \
      --exit-code 0 --started-at "$STARTED" --ended-at "$ENDED" \
      --source-kind wrapper --source-ref bin/run-scheduled.sh \
      --detail "heartbeat via $SERVICE/$RUN_KEY" "${corr[@]}"
    "${hb_argv[@]}" >> "$LOG" 2>&1
    hb_recorder_exit=$?
    hb_record_action=recorded
    # Same rule as the primary stamp above: only a landed row throttles.
    if [ "$hb_recorder_exit" -eq 0 ]; then
      mkdir -p "$STATE_DIR" 2>/dev/null
      date -u +%s > "$HB_STATE_FILE" 2>/dev/null
    fi
  else
    hb_argv=()
    hb_recorder_exit=throttled
    hb_record_action=throttled
  fi
  print -r -- "$(date -u '+%Y-%m-%dT%H:%M:%SZ') run-scheduled key=$HB_RUN_KEY service=$ALSO_HEARTBEAT child_exit=0 state=succeeded record_action=$hb_record_action recorder_exit=$hb_recorder_exit argv=${hb_argv[*]}" >> "$LOG"
fi

# The job's answer, never this script's.
exit $rc
