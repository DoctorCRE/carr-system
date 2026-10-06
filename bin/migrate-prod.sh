#!/bin/zsh
# migrate-prod.sh — the ONE sanctioned door for applying migrations to the
# PRODUCTION database. Sanctioned by Joe 2026-08-07 night ("yea go ahead and
# build it"), widening addendum A14's build-session fence for this script only:
# sessions apply production migrations HERE, never by juggling credentials
# inline. Same code path every time (rule a8c55a47): derives the owner DSN via
# neonctl inside the process — the db-tap.py pattern — so no credential ever
# appears in a command line or a transcript.
#
# Usage:
#   ./bin/migrate-prod.sh            # dry run: list pending against production
#   ./bin/migrate-prod.sh --apply    # apply (migrate.py adds --yes only from us)
#   ./bin/migrate-prod.sh --through 0170_guidance_import_lifecycle.sql
#   ./bin/migrate-prod.sh --apply --through 0170_guidance_import_lifecycle.sql
#
# RAILS, in order:
#   1. Uncommitted migration files REFUSE to apply — production runs reviewed,
#      committed DDL only. (Dry runs are always allowed.)
#   2. Rehearse-first stays doctrine: migrations rehearse on a Neon branch
#      before this script applies them (migrations/README.md). This script
#      cannot verify that mechanically — the discipline is the builder's, and
#      the branch step is in every phase's acceptance criteria.
#   3. Every invocation logs to out/migrate-prod.log with host and outcome.

set -eu
REPO="${0:A:h:h}"
LOG="$REPO/out/migrate-prod.log"
mkdir -p "$REPO/out"

# ── PREVENTION WIRING (WR-000048 activation, design at
#    out/wr48-activation/design-rehearse/prevention-design.md) ─────────────
# Master plan §6: "a parked lane becomes a loud, attributed, routed decision
# within minutes of first refusal, never a discovery." Every refusal below
# sets LAST_REASON_CLASS/LAST_DETAIL/LAST_MIGRATION and then exits exactly as
# it always has; the EXIT trap turns an unhandled exit into a durable local
# receipt plus an escalation attempt, and INT/TERM get the same treatment so
# an operator's Ctrl-C or a supervisor's TERM is never a silent gap either.
#
# MP_PY / RUN_DOOR: the receipt writer and the escalation orchestrator live
# in tools/migrate-prod-support.py (JSON handling and the fsync discipline
# belong in Python, not inlined shell — design §2). RUN_DOOR is the ONE
# sanctioned Bash door (`<door> call <verb> '<json>'`) both record-layer
# calls go through; CARR_MIGRATE_PROD_RUN_DOOR is a TEST HOOK ONLY, mirroring
# the CARR_KEY_RECOVERY_TEST_SELFTEST / CARR_RESTORE_REHEARSE_SELFTEST
# precedent (bin/key-recovery-test.sh, bin/restore-rehearse.sh) — never set
# by a real invocation, so the real door is always the default.
MP_PY="$REPO/.venv/bin/python"
[ -x "$MP_PY" ] || MP_PY=python3
RUN_DOOR="${CARR_MIGRATE_PROD_RUN_DOOR:-$REPO/run.sh}"

LAST_REASON_CLASS=""
LAST_DETAIL=""
LAST_MIGRATION=""
COMPLETED=0
SIGNAL_HANDLED=0
MIGRATE_TRACE=""

cleanup_migrate_trace() {
  if [[ -n "$MIGRATE_TRACE" && -f "$MIGRATE_TRACE" ]]; then
    rm -f "$MIGRATE_TRACE"
  fi
  MIGRATE_TRACE=""
}

write_refusal_receipt() {   # write_refusal_receipt <reason_class> <detail> [migration_name]
  local reason_class="$1" detail="$2" migration="${3:-}"
  local host=""
  host="$(hostname 2>/dev/null || true)"
  local path
  if path="$("$MP_PY" "$REPO/tools/migrate-prod-support.py" write-receipt \
      --reason-class "$reason_class" --detail "$detail" \
      --migration "$migration" --host "$host" --out-dir "$REPO/out")"; then
    print -r -- "$path"
  else
    # NEVER SWALLOWED — the helper already printed why to stderr; this line
    # is the wrapper's own loud line, matching restore-rehearse.sh's
    # "RECORDING NEVER FAILS THE REHEARSAL... never hidden" posture.
    print -u2 "COULD NOT WRITE THE LOCAL REFUSAL RECEIPT — out/ may be full or unwritable."
    return 1
  fi
}

escalate_refusal() {   # escalate_refusal <receipt_path>
  # Design §3's retry-then-escalate route for open-incident and
  # add-room-turn, each followed by an independent readback (get-incident /
  # read-room), lives entirely in the python helper below — it still only
  # ever reaches the record layer through "$RUN_DOOR" call <verb> '<json>',
  # the same sanctioned door a human would type by hand. Escalation's own
  # outcome never changes migrate-prod.sh's exit code (design §6): this is
  # notification, never a second production write path, so a failure here is
  # swallowed by `|| true` — the loud fallback message on stderr, printed by
  # the helper itself when a leg does not land, is what carries the signal.
  "$MP_PY" "$REPO/tools/migrate-prod-support.py" escalate \
    --receipt-path "$1" \
    --reason-class "${LAST_REASON_CLASS:-wrapper_terminated}" \
    --detail "${LAST_DETAIL:-migrate-prod.sh exited with no named cause}" \
    --migration "${LAST_MIGRATION:-}" \
    --run-door "$RUN_DOOR" \
  || true
}

# CORRECTED per cross-family round 3 (design §4): signal handlers write the
# receipt UNCONDITIONALLY and exit 128+signo themselves — never rely on $?,
# which reads 0 inside a signal-interrupted trap — and the EXIT handler gates
# on the explicit COMPLETED flag, never on $?, so a signal that already ran
# on_signal cannot make on_exit fire a second time for the same refusal.
#
# SIGNAL_HANDLED, found during this build's own verification and NOT in the
# design's literal sketch: `trap - EXIT` alone does not stop zsh's EXIT trap
# from also firing when the CURRENT trap handler is itself what calls `exit`
# — reproduced directly (a minimal `trap 'f' TERM` + `trap onexit EXIT`
# script; `f` runs `trap - EXIT; exit 143`; `onexit` still ran). Gating
# on_exit on an explicit flag, exactly like COMPLETED, is what actually
# prevents the double-fire in this shell; `trap - EXIT` is kept too (it is
# correct in spirit and harmless) but the flag is the real guard.
on_signal() {   # on_signal <signal-name> <signal-number>
  local receipt_path
  # A failed receipt write (out/ full or unwritable) must NOT abort this handler
  # under `set -e`: escalation is the load-bearing half and has to run even when
  # the local receipt could not be persisted. write_refusal_receipt already
  # printed its loud "COULD NOT WRITE..." line to stderr; fall through to
  # escalation with a sentinel path the python helper treats as a missing
  # receipt (it still opens the incident and posts to the room — only the
  # receipt-update legs note the file is absent).
  receipt_path="$(write_refusal_receipt "${LAST_REASON_CLASS:-wrapper_terminated}" \
                   "${LAST_DETAIL:-migrate-prod.sh received SIG$1 mid-run}" \
                   "${LAST_MIGRATION:-}")" \
    || receipt_path="$REPO/out/refusal-receipt-unwritten"
  escalate_refusal "$receipt_path"
  cleanup_migrate_trace
  SIGNAL_HANDLED=1
  trap - EXIT   # the receipt is written; do not let the EXIT trap double-fire
  exit $((128 + $2))
}
trap 'on_signal INT 2'  INT
trap 'on_signal TERM 15' TERM

on_exit() {
  local rc=$?
  [ "$COMPLETED" -eq 1 ] && return 0
  [ "$SIGNAL_HANDLED" -eq 1 ] && return 0
  local receipt_path
  # Same set -e guard as on_signal: a failed receipt write must not abort the
  # EXIT handler before escalation runs. Fall through with a sentinel path.
  receipt_path="$(write_refusal_receipt "${LAST_REASON_CLASS:-wrapper_terminated}" \
                   "${LAST_DETAIL:-migrate-prod.sh exited (rc=$rc) with no named cause}" \
                   "${LAST_MIGRATION:-}")" \
    || receipt_path="$REPO/out/refusal-receipt-unwritten"
  escalate_refusal "$receipt_path"
  cleanup_migrate_trace
}
trap on_exit EXIT
# ── end prevention wiring setup; the wrapper's existing body runs below,
#    each of its five refusal points setting LAST_* first (set -u discipline
#    unchanged), COMPLETED=1 marking the one successful path at the very end.

APPLY=0
THROUGH=""
while (( $# )); do
  case "$1" in
    --apply)
      APPLY=1
      shift
      ;;
    --through)
      if (( $# < 2 )) || [[ -z "$2" ]]; then
        print -u2 "--through requires an exact migration filename"
        exit 2
      fi
      THROUGH="$2"
      shift 2
      ;;
    *)
      print -u2 "unknown argument: $1"
      exit 2
      ;;
  esac
done

stamp() { print -r -- "$(date -u +%FT%TZ) migrate-prod $*" >> "$LOG" }

if (( APPLY )); then
  dirty=$(cd "$REPO" && git status --porcelain migrations/)
  if [[ -n "$dirty" ]]; then
    stamp "REFUSED uncommitted migrations: ${dirty//$'\n'/ · }"
    LAST_REASON_CLASS="uncommitted_migrations"
    LAST_DETAIL="REFUSED uncommitted migrations: ${dirty//$'\n'/ · }"
    print -u2 "REFUSED: uncommitted files under migrations/ — commit them first."
    print -u2 "$dirty"
    exit 1
  fi
fi

# NON-INTERACTIVE AUTH, added 2026-08-10. neonctl's saved browser login expires
# on its own schedule, and when it does it does not fail — it prompts, waits 60
# seconds for a browser nobody is at, and times out. That took down FOUR things
# at once on 2026-08-03 and again tonight: this script, bin/import-doctrine.sh,
# bin/restore-rehearse.sh (the only proof the encrypted backups can be restored)
# and ops/partner_ping.py, the Joe/Dell interrupt channel, which then printed
# "nothing new since" 382 consecutive times over six days. A quiet channel and a
# dead one produced identical output, which is the defect loop #272 already names
# in another lane.
#
# A NEON API KEY does not expire on a timer and needs no browser. neonctl reads
# it from NEON_API_KEY. This preserves the property this script was built for —
# the DSN is still derived per invocation and still never appears in a command
# line or a transcript — and only removes the human from the refresh.
#
# Joe creates the key once in the Neon console and puts it in db.env beside the
# credentials already there. Until he does, the fallback is the old interactive
# path, so nothing breaks in the meantime; it just still needs a browser.
if [ -z "${NEON_API_KEY:-}" ] && [ -f "$HOME/.config/carr/db.env" ]; then
  # THE NEXT LINE IS A `source`, WHICH MAKES THAT FILE PART OF THIS SCRIPT, and a
  # credential file that does not parse is therefore a syntax error in this one.
  # See carr_require_sourceable_db_env for what that cost and why the check is
  # one shared function rather than an inlined copy per caller.
  . "$REPO/bin/routine-credential-env.sh"
  carr_require_sourceable_db_env "migrate-prod" || {
    stamp "FAIL db.env is not sourceable — nothing applied"
    LAST_REASON_CLASS="db_env_unsourceable"
    LAST_DETAIL="FAIL db.env is not sourceable — nothing applied"
    exit 78
  }
  set -a; . "$HOME/.config/carr/db.env"; set +a
fi
if [ -z "${NEON_API_KEY:-}" ]; then
  stamp "WARN no NEON_API_KEY — falling back to interactive neonctl auth"
  print -u2 "note: NEON_API_KEY is not set, so this needs a browser login if the"
  print -u2 "      saved neonctl session has expired. Add NEON_API_KEY to"
  print -u2 "      ~/.config/carr/db.env to make this path unattended."
fi

# PRODUCTION IS PINNED BY PROJECT ID, and that is not tidiness — it is the fix
# for a real outage of this script.
#
# This line read `neonctl connection-string production --role-name neondb_owner`
# until 2026-08-14 and worked for as long as the account held exactly ONE Neon
# project. Program 1 created a second one (the isolated staging project), and
# from that moment neonctl refused every invocation with "Multiple projects
# found, please provide one with the --project-id option". Because the DSN was
# captured with 2>/dev/null inside a `set -eu` script, the refusal produced
# NOTHING: no message, no stderr, exit 1 and an empty log line. The one
# sanctioned door for applying production migrations was closed for every
# session on this machine and said nothing about why.
#
# The id, not the name. tools/db-tap.py pins production the same way and states
# the reason: a name lookup can drift to whatever it happens to return, and a
# name collision or typo that silently repointed this at another project is the
# worst failure this file could have. Staging is resolved by name over there
# because it is rebuilt often; production is never rebuilt.
NEON_PROJECT_PRODUCTION="steep-field-48688294"

# Capture failure inside a conditional so set -e cannot skip the named cause.
# The helper redacts stderr in memory before any diagnostic file is written.
NEON_ERR="$(mktemp "${TMPDIR:-/tmp}/carr-neonctl.XXXXXX")"
NEON_RC=0
if DSN="$("$MP_PY" "$REPO/tools/migrate-prod-support.py" neon-dsn \
        --project-id "$NEON_PROJECT_PRODUCTION" 2>"$NEON_ERR")"; then
  :
else
  NEON_RC=$?
fi
if [[ "$NEON_RC" -ne 0 || -z "$DSN" ]]; then
  reason="$(cat "$NEON_ERR" 2>/dev/null)"
  rm -f "$NEON_ERR"
  stamp "FAIL no DSN from neonctl rc=$NEON_RC: ${reason:-no error text}"
  LAST_REASON_CLASS="dsn_unavailable"
  LAST_DETAIL="FAIL no DSN from neonctl rc=$NEON_RC: ${reason:-no error text}"
  print -u2 "could not derive the production owner DSN from neonctl (logged)."
  print -u2 "neonctl said: ${reason:-nothing at all}"
  exit 1
fi
rm -f "$NEON_ERR"

migrate_args=()
if [[ -n "$THROUGH" ]]; then
  migrate_args+=(--through "$THROUGH")
fi

if (( APPLY )); then
  MIGRATE_TRACE="$(mktemp "${TMPDIR:-/tmp}/carr-migrate-prod.XXXXXX")"
  setopt pipefail
  if DATABASE_URL="$DSN" "$REPO/.venv/bin/python" "$REPO/tools/migrate.py" \
       --apply --yes "${migrate_args[@]}" 2>&1 | tee "$MIGRATE_TRACE"; then
    unsetopt pipefail
    cleanup_migrate_trace
    stamp "OK applied${THROUGH:+ through $THROUGH}"
    # THE SNAPSHOT REFRESH RIDES WITH THE APPLY, and this is the only place it
    # can. db/schema.sql is a picture of production's structure, and THIS is the
    # moment that structure changes — so leaving the refresh to a later, separate
    # act is what let five layers of drift pile up behind one refresh nobody
    # took, measured 2026-08-20: two app roles that had stopped being created
    # anywhere, 67 PUBLIC revokes the file never carried, three seeded
    # configuration tables, and two tests that had quietly come to depend on the
    # file being stale. Every one of them was found by a rebuild failing, never
    # by the change that caused it.
    #
    # It runs AFTER the apply and cannot refuse it. Production has already
    # changed by this line; a snapshot that fails to regenerate is a reason to
    # shout, never a reason to pretend the migration did not happen. So this
    # never touches the exit code.
    print ""
    print "== refreshing db/schema.sql, because production's structure just moved =="
    if "$REPO/bin/schema-snapshot.sh"; then
      if "$REPO/bin/schema-snapshot.sh" --check >/dev/null 2>&1; then
        stamp "OK snapshot refreshed"
        print "  db/schema.sql now matches production. COMMIT IT — naming the path,"
        print "  in the same change as the migration you just applied."
      else
        stamp "WARN snapshot refreshed but --check still stale"
        print -u2 "  schema-snapshot.sh wrote the file and --check still reports it stale."
        print -u2 "  That is a real finding: read it before committing anything."
      fi
    else
      stamp "WARN snapshot refresh FAILED rc=$?"
      print -u2 "  THE MIGRATION APPLIED; ONLY THE SNAPSHOT DID NOT REFRESH."
      print -u2 "  Production is changed and db/schema.sql now describes a database"
      print -u2 "  that no longer exists. Re-run bin/schema-snapshot.sh by hand and"
      print -u2 "  commit it, or the next rebuild inherits the gap."
    fi
  else
    rc=$?
    unsetopt pipefail
    stamp "FAIL apply rc=$rc"
    LAST_REASON_CLASS="apply_refused"
    migrate_error="$(grep '^ERROR:' "$MIGRATE_TRACE" | tail -1)"
    LAST_DETAIL="${migrate_error:-FAIL apply rc=$rc}"
    # Prefer the explicit commit-batch attribution emitted by migrate.py; for
    # an execute-time refusal, use the last file whose apply line began. Never
    # substitute --through: that is an authorization boundary, not evidence of
    # which migration failed (the 2026-09-05 refusal incorrectly named 0483
    # when the database had actually rolled back 0480).
    LAST_MIGRATION="$(sed -n \
      -e 's/^ERROR: commit refused for migration batch \[\([^]]*\)\].*/\1/p' \
      -e 's/^applying \([^ ]*\.sql\) \.\.\..*/\1/p' \
      "$MIGRATE_TRACE" | tail -1)"
    cleanup_migrate_trace
    exit $rc
  fi
else
  DATABASE_URL="$DSN" "$REPO/.venv/bin/python" "$REPO/tools/migrate.py" "${migrate_args[@]}"
  stamp "OK dry-run${THROUGH:+ through $THROUGH}"
fi

COMPLETED=1   # the LAST line before a successful natural exit — everything
              # above this point that reaches here did so without an
              # explicit refusal exit, so the EXIT trap has nothing to do.
