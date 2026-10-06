#!/bin/zsh
# restore-rehearse.sh — prove the encrypted backups can actually come BACK.
#
# WHY THIS EXISTS (2026-08-02 cold-session audit). bin/backup-dump.sh has been
# writing encrypted dumps since 2026-07-30 and NOTHING has ever read one. A grep
# across the whole repo for `age -d`, `age --decrypt`, `pg_restore` and
# `psql ... backups/` returned zero hits: no code existed that could restore
# these files, so "we have backups" was an assumption, not a fact. Three things
# make that assumption expensive to keep:
#   1. Neon's point-in-time-restore window is SIX HOURS. Past that, these dumps
#      are the only history that exists. There is no vendor safety net behind
#      them.
#   2. backup-dump.sh runs inside the nightly chain, which is skipped whenever
#      the Mac sleeps through 2am. The gap is visible in backups/ right now:
#      2026-07-30, 2026-07-31, then 2026-08-02. No 08-01. A backup you cannot
#      restore AND cannot count on being there is two unknowns stacked.
#   3. An unrestorable dump is indistinguishable from a good one until the day
#      you need it, which is the worst possible day to find out.
#
# WHAT IT DOES. Creates a throwaway Neon branch, restores the newest
# backups/*.sql.age into a brand-new database ON that branch, compares row
# counts table-by-table against production, reports PASS/FAIL loudly, and
# deletes the branch. Re-runnable: the branch name carries a UTC timestamp, so
# two runs never collide, and teardown runs from a trap so it happens even on
# a failure or a Ctrl-C.
#
#   ./run.sh restore-rehearse                  # the full rehearsal
#   ./run.sh restore-rehearse --preflight      # checks only; creates nothing
#   ./run.sh restore-rehearse --identity path  # where the age private key is
#   ./run.sh restore-rehearse --keep-branch    # leave the branch for debugging
#
# THE PRIVATE KEY IS NOT OPTIONAL AND NOT STORED HERE. backups-public-key.txt in
# the repo can only ENCRYPT. Decryption needs ~/.config/carr/age-key.txt (600,
# local, plus the offline copy Joe owes per secrets-inventory.md). Override with
# --identity or CARR_AGE_IDENTITY. The key check runs FIRST, before anything is
# created or charged for, so a keyless run costs nothing and says exactly what
# is missing.
#
# ── PRODUCTION IS NEVER WRITTEN. Four independent guards, because one is a
#    promise and four is a design:
#   1. The ONLY statement this script ever sends to production is a read, and
#      that session is opened with default_transaction_read_only=on via
#      PGOPTIONS. A stray write errors at the server, not at our good intentions.
#   2. The restore target DSN is asserted to resolve to a DIFFERENT HOST than
#      production. Every Neon branch gets its own compute endpoint hostname
#      (verified 2026-08-02: production is ep-restless-resonance-…, a branch is
#      ep-damp-star-…), so a same-host restore target means something is wrong
#      and we stop rather than guess.
#   3. The restore goes into a NEW DATABASE created for this run, never into the
#      branch's inherited copy of neondb. Nothing is dropped, anywhere, ever —
#      this script contains no DROP and no TRUNCATE. A restore rehearsal that
#      needs to destroy something first is one typo away from destroying the
#      wrong thing.
#   4. Teardown deletes a branch BY THE ID neonctl handed back when we created
#      it this run, never by name and never by search. The default branch id is
#      also checked explicitly, so the delete cannot reach production even if
#      the create step returned something unexpected.
#
# ── THE DECRYPTED DUMP IS PRODUCTION DATA IN CLEARTEXT. It lands in a mktemp -d
#    (mode 700) outside the repo, never in backups/ and never anywhere git can
#    see, and the same trap that deletes the branch removes it. --keep-branch
#    keeps the BRANCH, never the plaintext; there is no flag that keeps the
#    plaintext, on purpose.
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/usr/local/opt/node@22/bin:/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/opt/homebrew/opt/libpq/bin:/usr/local/opt/libpq/bin:/usr/local/bin:/usr/bin:/bin"

# NON-INTERACTIVE AUTH. This job is scheduled weekly and runs with nobody
# watching, so it must never depend on a browser login. When neonctl's saved
# session expires it does not fail cleanly: it prompts, waits 60 seconds for a
# browser that is not there, and times out — and this script's only symptom was
# "cannot list Neon branches", which reads like a Neon outage rather than an
# expired credential. NEON_API_KEY does not expire on a timer and needs no
# browser. Sourced from db.env, the same file db-tap.py and migrate-prod.sh read,
# so all four neonctl callers share one credential and one failure mode.
source "$REPO/bin/routine-credential-env.sh"
# A dispatcher supplies CARR_JOB_PAYLOAD.  It must never trigger the recovery
# administrator path until a distinct recovery capability is provisioned.
if [ -n "${CARR_JOB_PAYLOAD:-}" ] && [ "${1:-}" != "--preflight" ]; then
  print -ru2 -- "restore-rehearse: routine dispatch refused; recovery admin capability is not provisioned"
  exit 78
fi
unset NEON_API_KEY
carr_clear_routine_db_env
carr_load_routine_db_env NEON_API_KEY CARR_DB_JOBS_URL || exit $?

PROJECT_ID="steep-field-48688294"
PROD_BRANCH="production"
NEONCTL="$REPO/mcp-server/node_modules/.bin/neonctl"
IDENTITY="${CARR_AGE_IDENTITY:-$HOME/.config/carr/age-key.txt}"
RESTORE_DB="restore_rehearse"
# Interpreter for the platform-metering-gate call in phase 0, the one place this
# script uses "$PY". Prefer the repo venv, fall back to python3: the same
# two-line idiom bin/deploy-worker.sh uses to invoke the same gate. Under
# `set -u` an unassigned $PY aborts the run at that call before the gate is
# consulted.
PY="$REPO/.venv/bin/python"
# Every psql below reaches its database through this: the connection URL goes
# into PG* environment variables, never onto a command line where `ps` shows
# the password (V5-F08 review G6).
PGX="$REPO/tools/pg-env-exec.py"
[ -x "$PY" ] || PY="$(command -v python3 || true)"

# The tables whose absence means the restore is worthless rather than merely
# odd. These are the record layer's spine: a dump that comes back without them
# is not a backup of this system. Kept short on purpose — the all-tables sweep
# below covers the other ~60 without a list anyone has to maintain.
CORE_TABLES="party lead client vendor deal activity next_action event"

# Aggregate floor. See the comparison block for why this is the truncation gate
# and per-table deltas are not.
FLOOR_PCT="${CARR_RESTORE_FLOOR_PCT:-90}"

# Freshness warning threshold for the newest dump, in hours. 26 matches the
# cadence health-check.py uses for every nightly-chain output (24h plus two
# hours of slack), so this script and the façade check agree on what "the chain
# ran last night" means.
STALE_HOURS="${CARR_BACKUP_STALE_HOURS:-26}"

# Minimum size for a dump to be worth spending a branch on. Mirrors the floor in
# backup-dump.sh, which learned on 2026-08-07 that "more than zero bytes" is not
# a check. This script had the same weak `[ -s ]` test below and would have
# created a Neon branch, restored a 200-byte file into it and only then
# discovered the problem — the expensive way to learn something free.
MIN_DUMP_BYTES="${CARR_MIN_DUMP_BYTES:-1048576}"

PREFLIGHT_ONLY=0
KEEP_BRANCH=0
VERIFY_ONLY=0
WANT_DATE=""
WANT_DUMP=""
BACKUP_RUN_ID=""
BACKUP_REPOSITORY="jbookout/carr-system"
COPYDIR=""
COPY_ARCHIVE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --preflight)   PREFLIGHT_ONLY=1; shift ;;
    --keep-branch) KEEP_BRANCH=1; shift ;;
    # --verify-only ADDED 2026-08-07. The missing middle rung. Until now the only
    # two things this system could say about a dump were "the file exists" (free,
    # and worth nothing — a 200-byte file exists) and "it restored into a real
    # database" (conclusive, but costs a Neon branch and several minutes). So in
    # practice nobody asked. This decrypts the dump and reads its structure: a
    # pg_dump ends with an explicit completion footer, so a truncated dump is
    # provable in seconds, with no branch created and nothing charged for.
    --verify-only) VERIFY_ONLY=1; shift ;;
    # --date ADDED the same day. `ls -t` picks the newest, which is right for the
    # nightly drill and useless for the actual question that came up: are the
    # 2026-07-30 and 2026-07-31 dumps (138K and 1.2M, against 15-17M from 08-02
    # onward) genuine early-buildout snapshots or truncated ones.
    --date)        [ $# -ge 2 ] || { echo "FAIL: --date needs YYYYMMDD" >&2; exit 2; }
                   WANT_DATE="$2"; shift 2 ;;
    # --dump ADDED 2026-08-14, consolidating bin/verify-key-custody.sh into this
    # path rather than leaving two scripts doing one job. Both --date and the
    # `ls -t` default look ONLY in backups/, which is the copy that dies with
    # this Mac. The question that actually matters for recovery is whether an
    # OFF-MAC copy restores — the R2 archive object, or the GitHub Actions
    # artifact — and neither of those lands in backups/. This takes an arbitrary
    # path so the drill can be run against the copy that would survive.
    --dump)        [ $# -ge 2 ] || { echo "FAIL: --dump needs a path" >&2; exit 2; }
                   WANT_DUMP="$2"; shift 2 ;;
    --identity)    [ $# -ge 2 ] || { echo "FAIL: --identity needs a path" >&2; exit 2; }
                   IDENTITY="$2"; shift 2 ;;
    # --backup-run-id ADDED 2026-09-24 (V5-F08 item 4). Restore the copy the
    # nightly CLOUD workflow run RUN produced, fetched from the store that holds
    # it, and take every fact about it — the digest its producer recorded, when
    # it was produced, the store's own digest — from the provider: the run's
    # provider-authenticated "Backup artifact" Check and the artifact API
    # (tools/restore-watermark.py fetch-copy). Nothing about the copy is typed
    # in by whoever runs this. Only then does phase 5 write a typed
    # restore-exercise-receipt.v1, and it re-reads the provider to verify it.
    # Without it the exact watermark still gates; no receipt is written, because
    # a local backups/ file has no independently recorded digest to hold it to.
    #
    # RUNBOOK NOTE (review H2): a run counts only while the
    # .github/workflows/backup-nightly.yml at its commit is byte-identical to
    # main's. EDITING backup-nightly.yml MAKES EVERY EARLIER RUN UNVERIFIABLE —
    # verify-restore refuses them (fails closed). After such an edit merges,
    # wait for (or dispatch from main) a fresh nightly run and rehearse against
    # that run id. Only backup-nightly.yml may hold `checks: write`
    # (tools/test_workflow_checks_write_grant.py fails CI otherwise).
    --backup-run-id) [ $# -ge 2 ] || { echo "FAIL: --backup-run-id needs a workflow run id" >&2; exit 2; }
                   case "$2" in (*[!0-9]*|'') echo "FAIL: --backup-run-id must be a number" >&2; exit 2 ;; esac
                   BACKUP_RUN_ID="$2"; shift 2 ;;
    -h|--help)     sed -n '2,60p' "$0"; exit 0 ;;
    *)             echo "FAIL: unknown argument '$1'" >&2; exit 2 ;;
  esac
done

say()  { print -r -- "$*"; }
step() { print -r -- ""; print -r -- "== $*"; }
die()  { print -r -- ""; print -r -- "REHEARSAL FAILED: $*" >&2; DIE_REASON="$*"; exit 1; }

# ── EVIDENCE STATE (Program 4) ────────────────────────────────────────────────
# Doctrine's exit bar for this drill is "recurring isolated restore from actual
# artifact, timed, integrity checked, application verified, AND RECORDED" — until
# now the PASS/FAIL below printed to a terminal that nobody is watching on a
# scheduled Tuesday run, and its evidence died with that terminal. Tracked from
# here so record_rehearsal(), called from the EXIT trap below, can build ONE
# ops.run row no matter which exit point below is actually taken — a die() in
# preflight, a failed branch/restore, a failed verification, or the ordinary
# PASS/FAIL at the bottom. Pre-declared, not left to spring into existence
# wherever first assigned, because `set -u` is on: a trap reading a variable no
# earlier line touched is a hard error, and the earliest exit points are exactly
# the ones least likely to have touched most of these (verified empirically —
# see the note in cleanup() below).
#
# RECORD_REHEARSAL is decided HERE, once, from flags already fully parsed by the
# time execution reaches this line. --preflight and --verify-only both return
# before phase 1 ever reads production, and neither one rehearses anything, so
# neither gets a row (dry-run-doctrine-gates precedent: a check is not the thing
# it checks).
RECORD_REHEARSAL=1
[ "$PREFLIGHT_ONLY" -eq 1 ] && RECORD_REHEARSAL=0
[ "$VERIFY_ONLY" -eq 1 ] && RECORD_REHEARSAL=0
SCRIPT_START_ISO="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SCRIPT_START_EPOCH="$(date +%s)"
RESTORE_START_ISO=""
RESTORE_START_EPOCH=""
DIE_REASON=""
DUMP=""
DUMP_BYTES=""
DUMP_STAMP=""
PROD_TABLES=""
PROD_ROWS=""
REHEARSE_SUMMARY=""
REST_PCT=""
REST_ROWS=""
REST_TABLES=""
EXERCISE_VERDICT=""

BRANCH_ID=""
WORKDIR=""
VERIFY_PID=""
VERIFY_FIFO=""
cleanup() {
  # rc MUST be captured as the very first statement, before any other command —
  # even a bare `[ ]` test — runs and overwrites $?. This is the exit status the
  # script is about to leave with, and record_rehearsal() below needs the real
  # one. Verified empirically: a zsh EXIT trap that never calls `exit` itself
  # leaves that pending status untouched no matter what the trap body does
  # afterward, so nothing below — including a failure inside record_rehearsal
  # itself — can turn a passing rehearsal red or a failing one green.
  local rc=$?

  # An armed verify-restore (review round-4 item 3) is stopped before anything
  # it might still write into is removed; closing the handoff ends it too.
  if [ -n "${VERIFY_FIFO:-}" ]; then exec 4>&-; fi
  if [ -n "${VERIFY_PID:-}" ]; then
    kill "$VERIFY_PID" 2>/dev/null
    wait "$VERIFY_PID" 2>/dev/null
  fi

  # Runs on every exit path, including a failure or a Ctrl-C. Plaintext first:
  # if only one of the two teardowns can happen, it must be the one holding
  # production data in the clear.
  if [ -n "$WORKDIR" ] && [ -d "$WORKDIR" ]; then
    rm -rf "$WORKDIR"
    say "  teardown: decrypted dump removed"
  fi
  if [ -n "$COPYDIR" ] && [ -d "$COPYDIR" ]; then
    rm -rf "$COPYDIR"
    say "  teardown: fetched encrypted copy removed"
  fi
  if [ -n "$BRANCH_ID" ]; then
    if [ "$KEEP_BRANCH" -eq 1 ]; then
      say "  teardown: KEEPING branch $BRANCH_ID (--keep-branch)."
      say "            It holds a copy of production. Delete it when you are done:"
      say "            $NEONCTL branches delete $BRANCH_ID --project-id $PROJECT_ID"
    elif "$NEONCTL" branches delete "$BRANCH_ID" --project-id "$PROJECT_ID" >/dev/null 2>&1; then
      say "  teardown: branch $BRANCH_ID deleted"
    else
      # Loud, and deliberately not a silent success: an orphaned branch is a
      # copy of production sitting in the vendor account, and it is also a bill.
      say "  teardown: COULD NOT DELETE branch $BRANCH_ID — delete it by hand:" >&2
      say "            $NEONCTL branches delete $BRANCH_ID --project-id $PROJECT_ID" >&2
    fi
  fi

  record_rehearsal "$rc"
}
trap cleanup EXIT INT TERM

# ── record_rehearsal: the ONE ops.run row (Program 4) ─────────────────────────
# Called only from cleanup() above, so it runs from EVERY exit path a real
# (non-preflight, non-verify-only) invocation can take. Never recomputes a
# number this script already computed elsewhere — it reads REHEARSE_SUMMARY,
# built from the SAME awk pass that decided PASS/FAIL in phase 4, so the ledger
# and the human-facing report can never quietly disagree.
#
# THREE FAILURE BUCKETS, chosen from two checkpoints this script already passes
# through on a good run — not from the exit code, which is always a bare 1 here
# (this script has no per-cause exit codes the way a wrapped child process
# would), so "exit_$rc" would be true every time and tell a reader nothing:
#   preflight_failed    — died before production's baseline could even be read
#                          (missing tool, missing key, bad/missing/small dump,
#                          Neon or production unreachable). PROD_TABLES is unset.
#   restore_failed       — production's baseline WAS read, but branch creation
#                          or the decrypt/load itself failed. PROD_TABLES is set,
#                          REHEARSE_SUMMARY is not.
#   verification_failed  — the restore completed and phase 4's row-count
#                          comparison found a real problem (or the 90% floor).
#                          REHEARSE_SUMMARY is set.
# The coarse bucket is for spotting a pattern across weeks; the full reason —
# DIE_REASON or the comparison numbers — goes in --detail for the one week that
# needs it read in full.
#
# RECORDING NEVER FAILS THE REHEARSAL (house pattern, bin/nightly.sh's
# record_run / bin/run-scheduled.sh): its own exit code is never checked by a
# caller of this function, and cleanup()'s captured rc is what the script
# actually exits with regardless of what happens here. It is also never
# hidden — a recorder that cannot reach the database says so LOUD, on stderr,
# because a rehearsal whose evidence silently failed to land is exactly the
# false comfort this whole drill exists to rule out.
record_rehearsal() {                      # record_rehearsal <exit-code>
  local rc="$1"
  [ "$RECORD_REHEARSAL" -eq 1 ] || return 0

  local state=succeeded
  local fclass=()
  if [ "$rc" -ne 0 ]; then
    state=failed
    local reason_bucket
    if [ -n "$REHEARSE_SUMMARY" ]; then
      reason_bucket=verification_failed
    elif [ -n "$PROD_TABLES" ]; then
      reason_bucket=restore_failed
    else
      reason_bucket=preflight_failed
    fi
    fclass=(--failure-class "$reason_bucket")
  fi

  # THE RTO CLOCK. started_at is RESTORE_START_ISO — captured right before the
  # decrypt+load pipe in phase 3, deliberately AFTER branch provisioning so a
  # slow Neon API call never inflates the number a human reads as "how long
  # does restore take" — falling back to the script's own start for a run that
  # never got that far. ended_at is left unset below, so ops-record.py's own
  # terminal-state default (now()) becomes the moment THIS call runs, which is
  # after phase 4's verification completes — together giving duration_ms the
  # full decrypt+restore+verify wall time, computed by the database from these
  # two timestamps rather than by this shell.
  local started="${RESTORE_START_ISO:-$SCRIPT_START_ISO}"

  # ONE LINE THAT ANSWERS "how long does restore take and from which artifact"
  # without archaeology — the measured seconds and the dump's own date/size,
  # restated here rather than left for a human to derive from started_at/
  # ended_at, because detail is what gets read first.
  local detail
  if [ -n "$REHEARSE_SUMMARY" ]; then
    local dur=$(( $(date +%s) - ${RESTORE_START_EPOCH:-$SCRIPT_START_EPOCH} ))
    detail="dump=${DUMP:t} (${DUMP_BYTES:-?}B, taken ${DUMP_STAMP:-unknown}) restored ${REST_PCT:-?}% of production (${REST_TABLES:-?}/${PROD_TABLES:-?} tables, ${REST_ROWS:-?}/${PROD_ROWS:-?} rows) — decrypt+restore+verify took ${dur}s"
  elif [ -n "$DUMP" ]; then
    detail="dump=${DUMP:t} (${DUMP_BYTES:-?}B, taken ${DUMP_STAMP:-unknown}) — ${DIE_REASON:-aborted before verification}"
  else
    detail="${DIE_REASON:-aborted during preflight, before a dump was selected}"
  fi
  # Review K3: the restore-exercise verdict recorded is the one decided on the
  # PIPED verify-restore output; the receipt file in out/ is only a copy.
  # Review M2: the binary check guards against an accidental PATH shim only; an
  # adversary running as the same OS user is out of scope.
  [ -n "$EXERCISE_VERDICT" ] && detail="$detail; exercise=$EXERCISE_VERDICT (piped verify-restore verdict; receipt file is a copy; binary check stops PATH shims only, same-OS-user adversary out of scope)"
  # ops.run's detail column is one redacted line: no secrets, no client
  # content — and nothing here is either, only a filename, a byte count, a
  # percentage and a duration.
  detail="${detail[1,400]}"

  # THE PROVENANCE LINE IS THE TESTED SURFACE (same discipline as
  # bin/run-scheduled.sh's out/run-scheduled.log line). Printed BEFORE the call
  # is attempted and independent of whether it lands, so
  # ops/restore-rehearse-record-selftest.py can assert the exact
  # state/failure_class/detail this function derived from a fixture without a
  # live database — and so a human reading the transcript sees what SHOULD have
  # been recorded even on the one run where the recorder itself could not reach
  # the database.
  say "  evidence: state=$state exit_code=$rc${fclass:+ failure_class=${fclass[2]}} started_at=$started"
  say "            detail: $detail"

  local py="$REPO/.venv/bin/python"
  [ -x "$py" ] || py=python3
  local out
  if out="$("$py" "$REPO/tools/ops-record.py" run \
        --service restore-rehearse-weekly --key restore.rehearsal \
        --state "$state" --exit-code "$rc" --started-at "$started" \
        --source-kind collector --source-ref bin/restore-rehearse.sh \
        --detail "$detail" "${fclass[@]}" 2>&1)"; then
    say "  evidence: recorded to ops.run ($out)"
  else
    # FAIL LOUD (house pattern). The rehearsal's own PASS/FAIL above is the
    # operational answer and this can never touch it — but a second, silent
    # failure here would be exactly the false comfort ops.v_service_
    # environment_health's "silence is visible" design exists to rule out, so
    # it gets its own loud line instead of a swallowed exit code.
    print -r -- "" >&2
    print -r -- "  EVIDENCE WARNING: the rehearsal result above is unaffected, but it" >&2
    print -r -- "  was NOT recorded to ops.run — $out" >&2
  fi
}

# parse_rehearse_summary — the ONE place REHEARSE_SUMMARY's key=value tokens
# are read, called from phase 4 below AND from the selftest hook that follows,
# so a test proves the exact parsing production uses rather than a copy of it.
parse_rehearse_summary() {
  [ -n "$REHEARSE_SUMMARY" ] || return 0
  for kv in ${(z)REHEARSE_SUMMARY}; do
    case "$kv" in
      pct=*)         REST_PCT="${kv#pct=}" ;;
      rest_rows=*)   REST_ROWS="${kv#rest_rows=}" ;;
      rest_tables=*) REST_TABLES="${kv#rest_tables=}" ;;
    esac
  done
}

# TEST HOOK ONLY — never set by a scheduled task, launchd, or a real "Run Now".
# Lets ops/restore-rehearse-record-selftest.py drive record_rehearsal() through
# THIS SAME EXIT TRAP a real rehearsal uses: argument parsing, the
# RECORD_REHEARSAL gate, cleanup(), the three-bucket failure logic and the real
# `tools/ops-record.py run` call all execute for real — with fixture values
# standing in for the Neon branch, the decrypt and the row-count comparison a
# test must never actually perform. Same no-mock discipline as
# bin/refresh-rules.sh's CARR_REFRESH_RULES_EXPORT_CMD: nothing below this
# guard is reachable without CARR_RESTORE_REHEARSE_SELFTEST set, and no real
# invocation ever sets it. Placed AFTER RECORD_REHEARSAL is decided (above) and
# BEFORE anything is created (below), so a test can combine this with
# --preflight or --verify-only to prove those two still record nothing even
# with a fixture that would otherwise produce a full row.
if [ -n "${CARR_RESTORE_REHEARSE_SELFTEST:-}" ]; then
  DUMP="${CARR_RESTORE_REHEARSE_SELFTEST_DUMP:-}"
  DUMP_BYTES="${CARR_RESTORE_REHEARSE_SELFTEST_BYTES:-}"
  DUMP_STAMP="${CARR_RESTORE_REHEARSE_SELFTEST_STAMP:-}"
  PROD_TABLES="${CARR_RESTORE_REHEARSE_SELFTEST_PROD_TABLES:-}"
  PROD_ROWS="${CARR_RESTORE_REHEARSE_SELFTEST_PROD_ROWS:-}"
  REHEARSE_SUMMARY="${CARR_RESTORE_REHEARSE_SELFTEST_SUMMARY:-}"
  parse_rehearse_summary
  DIE_REASON="${CARR_RESTORE_REHEARSE_SELFTEST_DIE_REASON:-}"
  exit "${CARR_RESTORE_REHEARSE_SELFTEST_EXIT:-0}"
fi

# ── PHASE 0: preflight. Nothing is created and nothing is charged for until
#    every one of these passes. Ordered cheapest-and-most-likely-missing first.
step "phase 0: preflight"

for tool in age psql neonctl; do
  case "$tool" in
    neonctl) [ -x "$NEONCTL" ] || die "neonctl not found at $NEONCTL (run npm install in mcp-server/)" ;;
    *) command -v "$tool" >/dev/null 2>&1 || die "$tool is not on PATH. brew install $tool" ;;
  esac
  say "  ok    $tool present"
done

# THE KEY CHECK, FIRST AND LOUD. This is the step that cannot be worked around,
# and the failure message has to tell a human who is holding a paper key at 2am
# exactly what to do, not just that something is missing.
if [ ! -f "$IDENTITY" ]; then
  print -r -- "" >&2
  print -r -- "REHEARSAL CANNOT RUN: no age private key at $IDENTITY" >&2
  print -r -- "" >&2
  print -r -- "  backups-public-key.txt in this repo ENCRYPTS ONLY. It cannot decrypt anything." >&2
  print -r -- "  The private key normally lives at ~/.config/carr/age-key.txt (mode 600), with" >&2
  print -r -- "  an offline copy per secrets-inventory.md." >&2
  print -r -- "" >&2
  print -r -- "  Supply it one of two ways:" >&2
  print -r -- "    ./run.sh restore-rehearse --identity /path/to/age-key.txt" >&2
  print -r -- "    CARR_AGE_IDENTITY=/path/to/age-key.txt ./run.sh restore-rehearse" >&2
  print -r -- "" >&2
  print -r -- "  If NO copy of that key can be found, the dumps in backups/ are already" >&2
  print -r -- "  unrecoverable and that is the emergency, not this message." >&2
  exit 1
fi
say "  ok    age identity present at $IDENTITY"

# Permission check on the identity, advisory. A 644 private key is not a reason
# to refuse a restore rehearsal, but it is a reason to say something out loud.
IDENT_MODE="$(stat -f '%Lp' "$IDENTITY" 2>/dev/null || echo '???')"
case "$IDENT_MODE" in
  600|400) : ;;
  *) say "  WARN  identity file mode is $IDENT_MODE, expected 600 — chmod 600 $IDENTITY" ;;
esac

# Newest dump. `ls -t` matches how backup-dump.sh prunes, so "newest" means the
# same thing in both scripts.
if [ -n "$BACKUP_RUN_ID" ]; then
  # The off-Mac copy the cloud workflow produced, fetched from the store with
  # its producer's record (see --backup-run-id). Encrypted bytes only; the
  # directory is a mktemp removed by the teardown trap.
  [ -z "$WANT_DUMP$WANT_DATE" ] || die "--backup-run-id names the copy; do not combine it with --dump or --date"
  COPYDIR="$(mktemp -d "${TMPDIR:-/tmp}/carr-restore-copy.XXXXXX")"
  "$PY" "$REPO/tools/restore-watermark.py" fetch-copy --repository "$BACKUP_REPOSITORY" \
      --run-id "$BACKUP_RUN_ID" --out-dir "$COPYDIR" > "$COPYDIR/fetched.json" \
    || die "could not fetch the backup workflow run $BACKUP_RUN_ID copy and its producer record"
  DUMP="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["dump"])' "$COPYDIR/fetched.json")"
  COPY_ARCHIVE="$COPYDIR/artifact.zip"
  [ -f "$DUMP" ] && [ -f "$COPY_ARCHIVE" ] || die "fetch-copy reported no dump"
  say "  ok    selected dump: $(basename "$DUMP") (--backup-run-id $BACKUP_RUN_ID, fetched from the artifact store)"
elif [ -n "$WANT_DUMP" ]; then
  # An explicit path, which is how an OFF-MAC copy gets tested: the R2 object or
  # the GitHub artifact, downloaded anywhere. Named loudly in the output because
  # a rehearsal against a copy that would survive this Mac is a different and
  # stronger claim than one against backups/, and the transcript should say which
  # was run.
  DUMP="$WANT_DUMP"
  [ -f "$DUMP" ] || die "no such dump: $DUMP"
  say "  ok    selected dump: $DUMP (--dump, an explicit path rather than backups/)"
elif [ -n "$WANT_DATE" ]; then
  DUMP="$REPO/backups/carr-$WANT_DATE.sql.age"
  [ -f "$DUMP" ] || die "no dump for $WANT_DATE. Present: $(ls "$REPO"/backups/carr-*.sql.age 2>/dev/null | xargs -n1 basename 2>/dev/null | tr '\n' ' ')"
  say "  ok    selected dump: $(basename "$DUMP") (--date $WANT_DATE)"
else
  DUMP="$(ls -t "$REPO"/backups/carr-*.sql.age 2>/dev/null | head -1)"
  [ -n "$DUMP" ] || die "no backups/carr-*.sql.age files exist. Run ./bin/backup-dump.sh first."
  say "  ok    newest dump: $(basename "$DUMP")"
fi

# Size, in exact bytes. `du -h` used to report this and floors at the 4K block
# size, so it printed "4.0K" for the 200-byte corrupt backup of 2026-08-07 — the
# one number that would have exposed the failure, rounded up out of visibility.
DUMP_BYTES="$(stat -f%z "$DUMP")"
say "  ok    size: $DUMP_BYTES bytes"
if [ "$DUMP_BYTES" -lt "$MIN_DUMP_BYTES" ]; then
  # The floor gates the EXPENSIVE path only. --verify-only costs nothing and is
  # precisely how you examine a suspiciously small dump, so refusing to look at
  # one would be the check preventing its own use. It warns and proceeds; the
  # structure test below is what actually rules.
  if [ "$VERIFY_ONLY" -eq 1 ]; then
    say "  WARN  $DUMP_BYTES bytes is under the $MIN_DUMP_BYTES-byte floor — verifying anyway"
  else
    die "$(basename "$DUMP") is $DUMP_BYTES bytes, under the $MIN_DUMP_BYTES-byte floor. This is not a usable dump; nothing was created."
  fi
fi

# Is it actually an age file? The header is a plain ASCII line, so this is a
# free check that catches a truncated or half-written dump before we spend a
# branch on it.
if ! head -c 21 "$DUMP" | grep -q 'age-encryption.org/v1'; then
  die "$DUMP does not start with the age header — it is not an age file (truncated write?)"
fi
say "  ok    age header intact"

# Freshness. NOT a failure: an old dump still deserves to be rehearsed, and in
# fact an old dump is when you most want to know it works. But the gap in
# backups/ (no 2026-08-01) is exactly the silent-skip this warns about.
#
# FRESHNESS IS READ FROM THE FILENAME, NOT THE MTIME (changed 2026-08-07).
# mtime answers "when was this file last written to this disk", which is a
# different question and stopped being a usable proxy on 2026-08-06: the ORDER
# 42b history purge rewrote backups/ and stamped every dump from 07-30 through
# 08-06 with the same mtime, 02:05 that morning. This check then reported the
# 2026-07-31 dump as "28h old". An instrument that measures the last git
# operation instead of the backup is worse than no instrument, because it still
# prints a confident number.
#
# backup-dump.sh names every dump from `date -u +%Y%m%d` at the moment it runs,
# so the STAMP IN THE NAME is the taken-on date, and it survives a copy, a
# restore, or a history rewrite. Age is measured from midnight UTC of that date,
# which reads up to a couple of hours young against a 2:33am run — immaterial
# against a 26h threshold on a daily cadence, and written down here rather than
# left for someone to rediscover.
DUMP_STAMP="${${DUMP:t}#carr-}"; DUMP_STAMP="${DUMP_STAMP%%.*}"
DUMP_EPOCH="$(date -j -u -f '%Y%m%d %H%M%S' "$DUMP_STAMP 000000" +%s 2>/dev/null || true)"
if [ -z "${DUMP_EPOCH:-}" ]; then
  say "  WARN  no date stamp readable from $(basename "$DUMP") — falling back to mtime"
  DUMP_EPOCH="$(stat -f %m "$DUMP")"
else
  # The disagreement is worth one line: it is the tell that a dump was rewritten
  # after it was taken, and it is what made this check lie in the first place.
  MTIME_EPOCH="$(stat -f %m "$DUMP")"
  DRIFT_H=$(( (MTIME_EPOCH - DUMP_EPOCH) / 3600 ))
  [ "$DRIFT_H" -lt 0 ] && DRIFT_H=$(( 0 - DRIFT_H ))
  if [ "$DRIFT_H" -gt 48 ]; then
    say "  note  mtime sits ${DRIFT_H}h off the name's date — this file was rewritten after"
    say "        it was taken (a checkout, a restore, or the ORDER 42b purge). Harmless;"
    say "        noted because mtime is no longer what freshness reads."
  fi
fi
DUMP_AGE_H=$(( ( $(date +%s) - DUMP_EPOCH ) / 3600 ))
if [ -n "$WANT_DATE" ]; then
  say "  ok    dump is ${DUMP_AGE_H}h old (freshness not gated: --date asked for this one)"
elif [ "$DUMP_AGE_H" -gt "$STALE_HOURS" ]; then
  say "  WARN  newest dump is ${DUMP_AGE_H}h old (>${STALE_HOURS}h). The nightly chain is"
  say "        skipped whenever the Mac sleeps through 2am — check out/nightly.log."
else
  say "  ok    dump is ${DUMP_AGE_H}h old"
fi

# ── VERIFY-ONLY: structure, not a restore. Nothing on Neon is touched, so this
#    exits before the reachability checks below.
#
#    WHAT IT PROVES AND WHAT IT DOES NOT. A pg_dump writes a header line and, as
#    its last line, an explicit completion footer. The footer is only reached if
#    pg_dump ran to the end, so its ABSENCE is proof of truncation. Its presence
#    proves the file is a complete pg_dump, which is not the same as proving the
#    dump will load — only phase 3 proves that. This rung is cheap and
#    conclusive in one direction, which is exactly what a triage check should be.
if [ "$VERIFY_ONLY" -eq 1 ]; then
  step "verify: decrypt and read the dump's structure (nothing created)"
  WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/carr-verify.XXXXXX")"
  chmod 700 "$WORKDIR"
  PLAIN="$WORKDIR/dump.sql"
  if ! age --decrypt -i "$IDENTITY" "$DUMP" > "$PLAIN" 2>"$WORKDIR/verify.err"; then
    say "$(cat "$WORKDIR/verify.err")" >&2
    die "$(basename "$DUMP") did not decrypt. THIS IS THE FINDING."
  fi
  PLAIN_BYTES="$(stat -f%z "$PLAIN")"
  say "  ok    decrypted: $PLAIN_BYTES bytes of SQL"

  HEAD_OK=0; FOOT_OK=0
  head -5 "$PLAIN" | grep -q 'PostgreSQL database dump' && HEAD_OK=1
  tail -5 "$PLAIN" | grep -q 'PostgreSQL database dump complete' && FOOT_OK=1
  N_TABLES="$(grep -c '^CREATE TABLE ' "$PLAIN" || true)"
  N_COPY="$(grep -c '^COPY ' "$PLAIN" || true)"
  say "  ok    CREATE TABLE statements: $N_TABLES"
  say "  ok    COPY (data) blocks:      $N_COPY"
  [ "$HEAD_OK" -eq 1 ] && say "  ok    pg_dump header present" \
                       || say "  FAIL  pg_dump header MISSING"
  [ "$FOOT_OK" -eq 1 ] && say "  ok    completion footer present — pg_dump ran to the end" \
                       || say "  FAIL  completion footer MISSING — this dump is TRUNCATED"

  say ""
  if [ "$HEAD_OK" -eq 1 ] && [ "$FOOT_OK" -eq 1 ] && [ "$N_TABLES" -gt 0 ]; then
    say "VERIFY PASS — $(basename "$DUMP") is a structurally complete pg_dump."
    say "Complete is not the same as loadable: drop --verify-only for the real rehearsal."
    exit 0
  fi
  die "$(basename "$DUMP") is NOT a complete pg_dump. Treat it as no backup at all."
fi

# Production reachability + the default branch id, which teardown checks against.
BRANCH_LIST_JSON="$("$NEONCTL" branches list --project-id "$PROJECT_ID" --output json 2>/dev/null)"
PROD_DEFAULT_ID="$(print -r -- "$BRANCH_LIST_JSON" \
  | python3 -c 'import json,sys; print(next((b["id"] for b in json.load(sys.stdin) if b.get("default")), ""))' 2>/dev/null)"
[ -n "$PROD_DEFAULT_ID" ] || die "cannot list Neon branches — is neonctl authenticated? ($NEONCTL auth)"
ACTIVE_NONDEFAULT_BRANCHES="$(print -r -- "$BRANCH_LIST_JSON" \
  | python3 -c 'import json,sys; print(sum(1 for b in json.load(sys.stdin) if not b.get("default")))' 2>/dev/null)"
"$PY" "$REPO/ops/platform-metering-gate.py" --gate neon-disposable-branch \
  --requested-lifetime-minutes 120 --active-nondefault-branches "$ACTIVE_NONDEFAULT_BRANCHES" \
  --cleanup-registered >/dev/null \
  || die "neon-disposable-branch metering admission refused"
say "  ok    Neon reachable; default branch is $PROD_DEFAULT_ID"

# The production DSN, derived INSIDE this process. Same pattern as
# backup-dump.sh and tools/db-tap.py, and for the same two reasons: the harness
# classifier refuses a Bash command containing $(...), and a DSN that never
# reaches a shell argument never reaches a shell history either. Never printed.
PROD_URL="$("$NEONCTL" connection-string "$PROD_BRANCH" --project-id "$PROJECT_ID" \
            --role-name neondb_owner 2>/dev/null)"
[ -n "$PROD_URL" ] || die "could not obtain the production connection string"
PROD_HOST="${${PROD_URL#*@}%%/*}"; PROD_HOST="${PROD_HOST%%\?*}"
say "  ok    production endpoint resolved (${PROD_HOST%%.*}…)"

if [ "$PREFLIGHT_ONLY" -eq 1 ]; then
  say ""
  say "PREFLIGHT OK — every check that runs before anything is created has passed."
  say "Nothing was created, decrypted or charged for. Drop --preflight for the real rehearsal."
  exit 0
fi

# ── PHASE 1: production baseline, READ ONLY.
# Taken BEFORE the branch exists so the numbers are not measured against a
# database we just influenced, and taken through a read-only session so the
# server enforces what the comment promises.
step "phase 1: production row counts (read-only session)"

# Exact counts for every base table in one round trip. query_to_xml is the
# standard way to run count(*) per table without generating SQL in the shell —
# no table name ever crosses a shell boundary, so nothing here is injectable by
# a table someone names creatively.
COUNT_SQL="select table_name || '|' || (xpath('/row/c/text()',
             query_to_xml(format('select count(*) as c from %I.%I', table_schema, table_name),
                          false, true, '')))[1]::text
           from information_schema.tables
          where table_schema = 'public' and table_type = 'BASE TABLE'
          order by table_name"

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/carr-restore-rehearse.XXXXXX")"
chmod 700 "$WORKDIR"
PROD_COUNTS="$WORKDIR/prod-counts.txt"

# default_transaction_read_only=on is the guard, not the comment above it. Any
# write against production in this session fails at the server.
if ! PGOPTIONS="-c default_transaction_read_only=on" PGURL_PROD="$PROD_URL" \
     "$PY" "$PGX" PGURL_PROD psql -v ON_ERROR_STOP=1 -At -c "$COUNT_SQL" > "$PROD_COUNTS" 2>"$WORKDIR/prod.err"; then
  say "$(cat "$WORKDIR/prod.err")" >&2
  die "could not read production row counts"
fi
PROD_TABLES=$(wc -l < "$PROD_COUNTS" | tr -d ' ')
PROD_ROWS=$(awk -F'|' '{s+=$2} END {print s+0}' "$PROD_COUNTS")
[ "$PROD_TABLES" -gt 0 ] || die "production reported zero tables — refusing to rehearse against nothing"
say "  ok    $PROD_TABLES tables, $PROD_ROWS rows in production right now"

# ── PHASE 2: the throwaway branch.
# ── VERIFY-RESTORE, ARMED BEFORE THE BRANCH EXISTS (--backup-run-id only).
# Review round 4, item 3 and M3: verify-restore performs the restore itself —
# it creates $RESTORE_DB on the branch (CREATE DATABASE fails if the name
# exists, so it is new by construction), decrypts and loads the artifact it
# downloaded, reads the watermark back and receipts it. It is started HERE,
# before phase 2, so that it reads production's flushed WAL position before the
# branch exists and then waits its bounded settle interval (round-5 review: the
# branch's parent_lsn is what the provider's storage has ingested, which trails
# the flush point by ordinary lag); after the branch is created, its id and
# admin DSN go to it over a private FIFO (never an argument), and it requires
# the branch's parent_lsn at or after that head. The head, the parent point and
# the gap are in the receipt.
#
# ERROR TAILS (round-5 review): a failed COPY makes psql print the offending
# row. Every tail below that can hold psql output goes through
# `restore-watermark.py redact-errors` (ERROR/FATAL lines only, values
# replaced) before it reaches the terminal. Its stdout goes straight through tee into the evaluator:
# THAT verdict is the authority (review K3); the tee's file is a copy.
redact_tail() {
  # redact_tail N FILE: the last N redacted error lines of FILE, on stderr.
  "$PY" "$REPO/tools/restore-watermark.py" redact-errors < "$2" 2>/dev/null | tail -"$1" >&2
}
exercise_reason() {
  # The evaluator's reason_id, or why there is none (verify-restore refused).
  "$PY" -c 'import json,sys
try: print(json.load(open(sys.argv[1]))["reason_id"])
except Exception: print("verify_restore_refused")' "$WORKDIR/exercise-verdict.json" 2>/dev/null || print -r -- "verify_restore_refused"
}
arm_verify_restore() {
  VERIFY_FIFO="$WORKDIR/verify.handoff"
  mkfifo -m 600 "$VERIFY_FIFO" || die "could not create the verify-restore handoff"
  mkdir -p "$COPYDIR/verify"
  (
    set -o pipefail
    "$PY" "$REPO/tools/restore-watermark.py" verify-restore \
      --repository "$BACKUP_REPOSITORY" --run-id "$BACKUP_RUN_ID" --identity "$IDENTITY" \
      --target-kind disposable_branch --project-id "$PROJECT_ID" --database "$RESTORE_DB" \
      --work-dir "$COPYDIR/verify" --armed-file "$WORKDIR/verify.armed" \
      < "$VERIFY_FIFO" 2>"$WORKDIR/verify.err" \
      | tee "$WORKDIR/receipt.verified.json" \
      | node "$REPO/mcp-server/bin/recovery-matrix-evaluate.mjs" restore - > "$WORKDIR/exercise-verdict.json"
  ) &
  VERIFY_PID=$!
  exec 4>"$VERIFY_FIFO"
  local waited=0
  # Liveness only: an unarmed verifier reads the head later, which can only
  # make it refuse (fail closed), never pass a branch it should not. The
  # timeout must exceed verify-restore's settle wait (30 s by default).
  while [ ! -s "$WORKDIR/verify.armed" ] && [ "$waited" -lt "${VERIFY_ARM_TIMEOUT:-180}" ]; do
    kill -0 "$VERIFY_PID" 2>/dev/null || break
    sleep 1
    waited=$((waited + 1))
  done
  if [ ! -s "$WORKDIR/verify.armed" ]; then
    redact_tail 5 "$WORKDIR/verify.err"
    die "verify-restore did not arm (it reads production's head LSN before the branch exists)"
  fi
  say "  ok    verify-restore armed: production's head LSN read before the branch exists"
}
hand_over_to_verify_restore() {
  # Returns verify-restore's pipeline status: 0 only on a passing piped verdict.
  setopt localoptions localtraps
  trap '' PIPE
  { print -r -- "$BRANCH_ID"; print -r -- "$BRANCH_ADMIN_URL"; } >&4 2>/dev/null
  exec 4>&-
  VERIFY_FIFO=""
  local rc=0
  wait "$VERIFY_PID" || rc=$?
  VERIFY_PID=""
  EXERCISE_VERDICT="$(exercise_reason)"
  return $rc
}
if [ -n "$BACKUP_RUN_ID" ]; then
  step "phase 1b: arm verify-restore"
  arm_verify_restore
fi

step "phase 2: throwaway branch"
BRANCH_NAME="restore-rehearse-$(date -u +%Y%m%dT%H%M%SZ)"
BRANCH_JSON="$("$NEONCTL" branches create --project-id "$PROJECT_ID" --name "$BRANCH_NAME" \
               --output json 2>"$WORKDIR/branch.err")"
BRANCH_ID="$(print -r -- "$BRANCH_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); b=d.get("branch",d); print(b.get("id",""))' 2>/dev/null)"
if [ -z "$BRANCH_ID" ]; then
  say "$(cat "$WORKDIR/branch.err")" >&2
  die "could not create the rehearsal branch"
fi
# Guard 4. If this ever trips, the create step returned something that is not a
# new branch, and the trap is about to delete whatever it names.
[ "$BRANCH_ID" != "$PROD_DEFAULT_ID" ] || { BRANCH_ID=""; die "branch create returned the DEFAULT branch id — refusing to continue or delete"; }
say "  ok    created $BRANCH_NAME ($BRANCH_ID)"

# ── PHASE 3: restore into a NEW database on that branch.
# The branch inherits production's copy of neondb, so restoring there would
# collide with existing objects and prove nothing. A fresh database is a genuine
# empty target and costs one statement.
step "phase 3: restore"
BRANCH_ADMIN_URL="$("$NEONCTL" connection-string "$BRANCH_ID" --project-id "$PROJECT_ID" \
                    --role-name neondb_owner 2>/dev/null)"
[ -n "$BRANCH_ADMIN_URL" ] || die "could not obtain the branch connection string"
BRANCH_HOST="${${BRANCH_ADMIN_URL#*@}%%/*}"; BRANCH_HOST="${BRANCH_HOST%%\?*}"

# Guard 2, the one that matters most. Different branch, different compute
# endpoint, different hostname. Same host means we are about to write to
# production and we stop instead.
if [ "$BRANCH_HOST" = "$PROD_HOST" ]; then
  die "the branch DSN resolves to the SAME HOST as production — refusing to restore anywhere near it"
fi
say "  ok    branch endpoint is a different host from production"

if [ -n "$BACKUP_RUN_ID" ]; then
  # verify-restore creates $RESTORE_DB, installs the extensions and loads the
  # artifact itself (see arm_verify_restore above); this script does not load.
  RESTORE_START_ISO="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  RESTORE_START_EPOCH="$(date +%s)"
  say "  handing the branch to verify-restore: it creates $RESTORE_DB, decrypts and loads the artifact, reads it back (the slow step) ..."
  if ! hand_over_to_verify_restore; then
    say "  --- verify-restore (last 10 lines, row data redacted) ---" >&2
    redact_tail 10 "$WORKDIR/verify.err"
    die "verify-restore did not restore and verify the artifact ($EXERCISE_VERDICT). THIS IS THE FINDING."
  fi
  say "  ok    verify-restore restored the artifact into $RESTORE_DB and the piped verdict passed: $EXERCISE_VERDICT"
  RESTORE_URL="$("$NEONCTL" connection-string "$BRANCH_ID" --project-id "$PROJECT_ID" \
                 --role-name neondb_owner --database-name "$RESTORE_DB" 2>/dev/null)"
  [ -n "$RESTORE_URL" ] || die "could not obtain the $RESTORE_DB connection string"
elif ! PGURL_ADMIN="$BRANCH_ADMIN_URL" "$PY" "$PGX" PGURL_ADMIN \
     psql -v ON_ERROR_STOP=1 -q -c "create database $RESTORE_DB" >/dev/null 2>"$WORKDIR/createdb.err"; then
  say "$(cat "$WORKDIR/createdb.err")" >&2
  die "could not create the $RESTORE_DB database on the branch"
fi
if [ -z "$BACKUP_RUN_ID" ]; then
RESTORE_URL="$("$NEONCTL" connection-string "$BRANCH_ID" --project-id "$PROJECT_ID" \
               --role-name neondb_owner --database-name "$RESTORE_DB" 2>/dev/null)"
[ -n "$RESTORE_URL" ] || die "could not obtain the $RESTORE_DB connection string"
say "  ok    empty database $RESTORE_DB created on the branch"

# SCOPED DUMPS DO NOT CARRY CLUSTER EXTENSIONS. backup-dump.sh deliberately
# selects only the application-owned public and ops schemas. PostgreSQL
# extensions are cluster/database objects outside that selection, even though
# the functions they install live in public. The 2026-08-17 recovery drill
# proved the consequence: the dump reached a guidance view using public.digest
# and failed because pgcrypto had never been installed in the new database.
# These are the exact two extension prerequisites declared by the migration
# tree (0001 and 0135). Install them in the throwaway target before loading;
# production is untouched and every application object still comes from the
# encrypted artifact.
if ! PGURL_RESTORE="$RESTORE_URL" "$PY" "$PGX" PGURL_RESTORE psql -v ON_ERROR_STOP=1 -q \
    -c "create extension if not exists pg_trgm; create extension if not exists pgcrypto;" \
    >/dev/null 2>"$WORKDIR/extensions.err"; then
  say "$(cat "$WORKDIR/extensions.err")" >&2
  die "could not install the dump's pg_trgm and pgcrypto prerequisites"
fi
say "  ok    pg_trgm and pgcrypto prerequisites installed in the throwaway database"

# THE DECRYPT. Piped straight into psql: the plaintext dump exists only in a
# pipe, never as a file, so there is nothing to forget to delete.
#
# BOTH sides of the pipe append to the SAME stderr file, and that is not tidiness.
# The first run of this script (2026-08-02, with a deliberately wrong identity)
# captured psql's stderr only, so age's "unknown identity type" scrolled past
# while the diagnostic tail printed an empty file — the script reported a failure
# and then showed nothing about it. The single most useful line this can ever
# print is age's "no identity matched any of the recipients", so it goes where
# the tail will find it.
#
# pipefail matters just as much: without it the pipeline's status is psql's, and
# psql exits 0 on an empty stdin. A failed decrypt would have loaded nothing and
# passed.
# THE PORTABILITY FILTER, and why it is a filter rather than ON_ERROR_STOP=0.
#
# FOUND BY THE FIRST REAL RUN, 2026-08-02: the restore died on
#   ERROR: permission denied to change default privileges
# `bin/backup-dump.sh` calls plain `pg_dump` with no --no-owner/--no-acl, so
# every dump carries ALTER DEFAULT PRIVILEGES / GRANT / REVOKE / OWNER TO
# statements naming the role that owned the source database. Restoring into a
# fresh database on a branch, those roles are not ours to act for, so the first
# one errors and ON_ERROR_STOP=1 abandons the whole load. The DATA was never the
# problem; the permission grammar wrapped around it was.
#
# backup-dump.sh is fixed so future dumps carry none of this. That does NOT help
# the dumps we already hold, and those are the only backups that exist — which is
# exactly the situation a restore has to survive. So the stream is filtered here.
#
# ON_ERROR_STOP STAYS 1. Dropping it would have been one word and would have made
# every future failure silent — a restore that skips a broken COPY and reports
# success is worse than one that fails loudly, and is the same false-comfort
# defect this whole drill was built to expose. Filtering removes exactly the
# statement classes we know cannot apply and leaves every other error fatal.
#
# FOUND AGAIN BY THE 2026-08-17 PRE-ACTIVATION RECOVERY DRILL: scoped plain-SQL
# dumps now include `CREATE SCHEMA public;`, while a newly created PostgreSQL
# database already owns that schema. ON_ERROR_STOP correctly refused the
# duplicate before loading a table. `public` is the only pre-created schema;
# `CREATE SCHEMA ops;` must remain and every table/COPY error stays fatal. Strip
# exactly that one idempotent declaration, not CREATE SCHEMA generally.
#
# The count assertion in phase 4 is what proves the filter did not eat data: ACL
# statements move no rows, so if stripping them changed a single count, the
# comparison fails.
RESTORE_FILTER='/^(CREATE SCHEMA public;|ALTER DEFAULT PRIVILEGES|GRANT |REVOKE |ALTER .* OWNER TO |COMMENT ON EXTENSION )/d'

# EVIDENCE (Program 4): the RTO clock for "how long does restore take" starts
# HERE — right before the actual decrypt+load — not at branch-create above, so
# a slow Neon provisioning call never inflates the number a human reads as
# restore time. Read by record_rehearsal() in the EXIT trap once the script
# actually exits, which is after phase 4's verification has also completed.
RESTORE_START_ISO="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
RESTORE_START_EPOCH="$(date +%s)"
say "  decrypting and loading (this is the slow step) ..."
say "  note: stripping the pre-created public schema declaration and ownership/ACL statements (see comment)"
set -o pipefail
if ! age --decrypt -i "$IDENTITY" "$DUMP" 2>>"$WORKDIR/restore.err" \
     | sed -E "$RESTORE_FILTER" \
     | PGURL_RESTORE="$RESTORE_URL" "$PY" "$PGX" PGURL_RESTORE \
         psql -v ON_ERROR_STOP=1 -v VERBOSITY=terse -v SHOW_CONTEXT=never -q \
         >"$WORKDIR/restore.out" 2>>"$WORKDIR/restore.err"; then
  set +o pipefail
  say ""
  say "  --- decrypt/restore errors (last 40, row data redacted) ---" >&2
  redact_tail 40 "$WORKDIR/restore.err"
  die "the dump did not decrypt and load. THIS IS THE FINDING — a backup that will not restore is not a backup."
fi
set +o pipefail
say "  ok    dump decrypted and loaded"
fi  # the local-dump path; with --backup-run-id verify-restore loaded it above

# ── PHASE 4: the assertion. This is the whole point; everything above is setup.
step "phase 4: row counts, restored vs production"
REST_COUNTS="$WORKDIR/restored-counts.txt"
if ! PGURL_RESTORE="$RESTORE_URL" "$PY" "$PGX" PGURL_RESTORE \
     psql -v ON_ERROR_STOP=1 -At -c "$COUNT_SQL" > "$REST_COUNTS" 2>"$WORKDIR/rest.err"; then
  say "$(cat "$WORKDIR/rest.err")" >&2
  die "could not read row counts back out of the restored database"
fi

# WHY THE GATE IS SHAPED THIS WAY, because the obvious shape is wrong. The dump
# is a snapshot taken at 2am and production has been moving all day, so a strict
# per-table equality check would go red every single run on nothing but normal
# churn — the "an alarm that fires every day trains people to stop reading
# alarms" failure bin/nightly.sh already had to fix once. Per-table deltas are
# therefore REPORTED, not gated. What IS gated:
#   FAIL  a table present in production is missing from the restore
#   FAIL  a table with rows in production restored with zero rows
#   FAIL  the restored total is below FLOOR_PCT% of production's total
# Those three catch the failure modes that actually exist — a dependency error
# that skips a table, a truncated dump, a half-written file — without pretending
# a moving database should match a snapshot to the row.
# NO SORT FUNCTION IS USED HERE, deliberately. The first draft called asorti(),
# which is a gawk extension: macOS ships the one-true-awk (version 20200816) and
# died with "calling undefined function asorti" on the first fixture run. Both
# input files already arrive sorted — the counting query ends in `order by
# table_name` — so walking them in file order gives sorted output for free.
# Production's tables print first, then any table the dump carries that
# production no longer has. Row counts are summed with explicit counters rather
# than length(array), for the same portability reason.
awk -F'|' -v floor="$FLOOR_PCT" -v core="$CORE_TABLES" '
  FNR==NR { prod[$1]=$2; ptot+=$2; npro++; ord[++n]=$1; next }
          { rest[$1]=$2; rtot+=$2; nres++; if (!($1 in prod)) ord[++n]=$1 }
  END {
    fails = 0
    split(core, c, " "); for (i in c) is_core[c[i]] = 1
    # HOW FAR BEHIND THE SCHEMA IS, in migrations. Both sides already count
    # schema_migrations as an ordinary table, so this costs no extra query: the
    # dump carries every migration applied before it was taken, production carries
    # every one applied since. The difference is the licence to treat a missing
    # table or an empty one as expected rather than as a failure.
    behind = 0
    if (("schema_migrations" in prod) && ("schema_migrations" in rest))
      behind = prod["schema_migrations"] - rest["schema_migrations"]
    if (behind > 0)
      printf "  NOTE: %d migration(s) were applied AFTER this dump was taken.\n        Tables and rows created by them cannot be in it — reported as expected, not failed.\n\n", behind

    printf "  %-28s %12s %12s %10s\n", "table", "production", "restored", "delta"
    printf "  %-28s %12s %12s %10s\n", "----------------------------", "------------", "------------", "----------"
    for (i = 1; i <= n; i++) {
      t = ord[i]
      p = (t in prod) ? prod[t] : -1
      r = (t in rest) ? rest[t] : -1
      if (p >= 0 && r < 0) {
        # SCHEMA EVOLUTION IS NOT DATA LOSS, and the first real run got this wrong.
        # On 2026-08-02 the drill reported 8 failures against a dump that was
        # perfectly intact: 25 migrations (0032..0056) were applied in the ten
        # hours AFTER the 08:50 dump, so candidate_pool, contact_state,
        # loop_domain, vendor_category, vendor_disposition,
        # vendor_relationship_level and deprecation did not exist yet. The
        # giveaway was in the table itself — prospect_pool carried 9860 rows in
        # the dump and candidate_pool carried 9860 in production, one table
        # renamed by 0048. A restore cannot produce a table its dump predates,
        # and calling that a failure is how a weekly alarm teaches everyone to
        # ignore it. `behind` is production_migrations - restored_migrations,
        # read from the schema_migrations counts both sides already report.
        if (behind > 0) {
          printf "  %-28s %12d %12s %10s   expected: table postdates this dump\n", t, p, "-", "-"
        } else {
          printf "  %-28s %12d %12s %10s   FAIL missing from the restore\n", t, p, "-", "-"
          fails++
        }
        continue
      }
      if (p < 0 && r >= 0) {
        # Expected and harmless: a migration landed AFTER this dump was taken,
        # so production dropped a table the dump still carries. Worth printing,
        # never worth failing.
        printf "  %-28s %12s %12d %10s   note: in the dump, not in production\n", t, "-", r, "-"
        continue
      }
      d = r - p
      if (p > 0 && r == 0) {
        # Same discrimination, one level down: the TABLE existed in the dump but
        # every ROW in it is newer. record_flag is the worked example — created by
        # 0001, still empty at 08:50, and its first 8 rows written that evening.
        # A failed COPY and a table nobody had written to yet look identical from
        # here, so when the dump is known to be schema-behind this is reported and
        # not gated. The aggregate floor below is what still catches a genuinely
        # truncated dump, and it is the right instrument for that.
        if (behind > 0) {
          printf "  %-28s %12d %12d %10d   expected: rows postdate this dump\n", t, p, r, d
        } else {
          printf "  %-28s %12d %12d %10d   FAIL restored EMPTY\n", t, p, r, d
          fails++
        }
        continue
      }
      mark = ""
      if (is_core[t] && r == 0 && p == 0) mark = "   note: core table, empty on both sides"
      else if (d != 0) mark = "   (drift — the dump is a snapshot; production moved)"
      printf "  %-28s %12d %12d %10d%s\n", t, p, r, d, mark
    }

    # The core spine must have restored at all. Separate from the per-table loop
    # because a core table missing entirely from BOTH sides is its own alarm:
    # it means this dump predates the record layer, not that the restore failed.
    for (t in is_core) {
      if (!(t in rest)) { printf "  FAIL core table %s is absent from the restored database\n", t; fails++ }
    }

    printf "\n  totals: production %d rows across %d tables · restored %d rows across %d tables\n",
           ptot, npro, rtot, nres
    if (ptot > 0) {
      pct = 100.0 * rtot / ptot
      printf "  restored total is %.1f%% of production (floor %d%%)\n", pct, floor
      if (pct < floor) { printf "  FAIL restored total is below the %d%% floor — the dump looks truncated\n", floor; fails++ }
      # Machine-readable, for the --detail line tools/ops-record.py writes
      # (Program 4). Filtered from the human-facing table below by the same
      # grep that already strips AWKFAILS=. Reuses THIS pct rather than a
      # second computation, so the evidence row and the printed report can
      # never quietly disagree.
      printf "REHEARSE_SUMMARY pct=%.1f rest_rows=%d rest_tables=%d prod_rows=%d prod_tables=%d\n",
             pct, rtot, nres, ptot, npro
    }
    printf "AWKFAILS=%d\n", fails
  }
' "$PROD_COUNTS" "$REST_COUNTS" > "$WORKDIR/compare.txt"

grep -v -e '^AWKFAILS=' -e '^REHEARSE_SUMMARY ' "$WORKDIR/compare.txt"
FAILS="$(sed -n 's/^AWKFAILS=//p' "$WORKDIR/compare.txt")"
: "${FAILS:=1}"

# EVIDENCE (Program 4): pulled from the SAME awk pass that just decided
# PASS/FAIL, never recomputed. Consumed by record_rehearsal() in the EXIT trap
# — captured into plain shell variables here, rather than having the trap
# reopen compare.txt, because cleanup()'s teardown deletes WORKDIR (and
# everything in it) before record_rehearsal runs.
REHEARSE_SUMMARY="$(sed -n 's/^REHEARSE_SUMMARY //p' "$WORKDIR/compare.txt")"
parse_rehearse_summary

# ── PHASE 5 (V5-F08 item 4): EXACT watermark and hash, against the ARTIFACT.
# Phase 4 compares against live production, which moves after the dump, so it
# can only ever be approximate. This compares the restored database against the
# rows the artifact itself carries — read from its own COPY blocks by a second
# streamed decrypt (plaintext never touches disk) — and requires equality,
# table for table, of the row count AND a content digest over the rows in
# pg_dump's own COPY text form (so a row that came back different, not just
# missing, fails). Every public/ops base table is read, schema-qualified,
# except extension-owned tables, whose rows CREATE EXTENSION makes rather than
# the dump. The restored side is read through a read-only session whose DSN is
# handed over in the environment, never on an argument list. Production is not
# touched.
step "phase 5: exact watermark vs the artifact itself"
if [ -n "$COPY_ARCHIVE" ]; then
  # The store holds the ZIP the producer uploaded; its recorded digest is of
  # those bytes, so those are the bytes hashed here.
  ARTIFACT_DIGEST="$("$PY" "$REPO/tools/restore-watermark.py" digest "$COPY_ARCHIVE")" \
    || die "could not hash the fetched artifact"
else
  ARTIFACT_DIGEST="$("$PY" "$REPO/tools/restore-watermark.py" digest "$DUMP")" \
    || die "could not hash the artifact that was restored"
fi
say "  ok    artifact sha256: ${ARTIFACT_DIGEST#sha256:}"
set -o pipefail
if ! age --decrypt -i "$IDENTITY" "$DUMP" 2>>"$WORKDIR/wm.err" \
     | "$PY" "$REPO/tools/restore-watermark.py" count > "$WORKDIR/artifact-watermark.json" 2>>"$WORKDIR/wm.err"; then
  set +o pipefail
  tail -5 "$WORKDIR/wm.err" >&2
  die "could not read the artifact's own watermark"
fi
set +o pipefail
if ! RESTORE_DSN="$RESTORE_URL" "$PY" "$REPO/tools/restore-watermark.py" restored \
       --artifact "$WORKDIR/artifact-watermark.json" > "$WORKDIR/restored-watermark.json" 2>"$WORKDIR/wm-rest.err"; then
  tail -5 "$WORKDIR/wm-rest.err" >&2
  die "could not read the restored watermark"
fi
RESTORE_FINISHED_ISO="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
"$PY" "$REPO/tools/restore-watermark.py" compare \
    --artifact "$WORKDIR/artifact-watermark.json" --restored "$WORKDIR/restored-watermark.json"
case $? in
  0) say "  ok    restored database equals the artifact exactly, rows and content, table for table" ;;
  1) say "  FAIL  restored database does not equal the artifact (see MISMATCH lines)" >&2
     FAILS=$((FAILS + 1)) ;;
  *) die "the exact watermark comparison could not be read" ;;
esac
if [ -n "$BACKUP_RUN_ID" ]; then
  mkdir -p "$REPO/out"
  RECEIPT_PATH="$REPO/out/restore-exercise-receipt.json"
  # An earlier run's receipt must not stand in for this one's outcome.
  rm -f "$RECEIPT_PATH"
  if [ "$FAILS" -ne 0 ]; then
    # Review G4: a rehearsal that failed phase 4 (the production comparison)
    # or phase 5 keeps NO receipt copy, whatever verify-restore's verdict was.
    say "  FAIL  no restore-exercise receipt copy: $FAILS assertion(s) failed" >&2
  else
    # Review K3: the verdict that counted was decided in phase 3 on the piped
    # output and is recorded in this run's ops.run row; this is only a copy.
    mv "$WORKDIR/receipt.verified.json" "$RECEIPT_PATH"
    say "  ok    restore exercise: $EXERCISE_VERDICT (decided on the piped verify-restore output)"
    say "        (copy of the bound receipt, not authority: $RECEIPT_PATH)"
  fi
else
  say "  note  no --backup-run-id: exact watermark gated, no receipt written (a local file has no producer record)"
fi

say ""
if [ "$FAILS" -eq 0 ]; then
  say "================================================================"
  say "  RESTORE REHEARSAL: PASS"
  say "  $(basename "$DUMP") decrypted, loaded and reconciled against production."
  say "  The backups are restorable. Proven, not assumed."
  say "================================================================"
  exit 0
else
  say "================================================================" >&2
  say "  RESTORE REHEARSAL: FAIL ($FAILS assertion(s) failed)" >&2
  say "  $(basename "$DUMP") did NOT come back intact. Read the table above." >&2
  say "  Do not treat these dumps as a working backup until this is green." >&2
  say "================================================================" >&2
  exit 1
fi
