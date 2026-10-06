#!/bin/zsh
# sync-control-plane-prod.sh — the ONE sanctioned door for installing the
# reviewed control-plane registry into the PRODUCTION database.
#
# WHY THIS EXISTS (2026-08-23). The control-plane roadmap's Phase 2 exit asks
# that every scheduled run have durable identity, state, idempotency, retries,
# receipts and an auditable owner. The check that asserts it is
# ops/control-plane-registry-gate.py, and on 2026-08-21 that gate was wired into
# CI (PR 461) where it passes against a throwaway database built from the
# committed schema: 25 workflows and 8 cognition contracts exact.
#
# Nothing installed any of it in production. On 2026-08-23 production held 5
# job definitions against the manifest's 25, ZERO cognition jobs against 8, and
# zero provider routes — and all five of those rows had been written by some
# path other than this manifest, carrying contract text from an older revision.
# One of them mattered: calendar-prebrief-projection-joe-daily, the one enabled
# definition, was recorded risk=green while the reviewed manifest classifies it
# yellow (rule 708c2150 — green is read-only, yellow is drafts a human
# approves). A downgraded risk colour on the only live definition is exactly the
# bypass Phase 1 exists to close.
#
# The cause was structural, not careless: `tools/control-plane.py sync` had
# exactly one caller in the whole repository, ops/ci.sh line 546, against the
# disposable database. There was no production door at all, so the gap could not
# be closed by remembering to be careful. Rule a8c55a47 — a manual path and an
# automated path that do the same job must be the same code — so this door runs
# the SAME sync CI runs, and nothing else.
#
# Usage:
#   ./bin/sync-control-plane-prod.sh           # read-only: what does production differ on?
#   ./bin/sync-control-plane-prod.sh --apply   # sync, then prove it landed
#
# RAILS, in order:
#   1. An uncommitted manifest REFUSES to apply — production runs reviewed,
#      committed configuration only. (Read-only runs are always allowed.)
#   2. --apply always re-runs ops/control-plane-registry-gate.py against
#      production afterwards and takes its exit code as the verdict. The sync
#      reporting success is not the success signal; the gate reading production
#      back and finding every contract exact is (rule a9ecd5b4).
#   3. Every invocation logs to out/sync-control-plane-prod.log with outcome.
#   4. The DSN is derived inside this process and never reaches a command line,
#      a terminal or a transcript.

set -eu
REPO="${0:A:h:h}"
LOG="$REPO/out/sync-control-plane-prod.log"
mkdir -p "$REPO/out"

APPLY=0
while (( $# )); do
  case "$1" in
    --apply) APPLY=1; shift ;;
    *) print -u2 "unknown argument: $1"; exit 2 ;;
  esac
done

stamp() { print -r -- "$(date -u +%FT%TZ) sync-control-plane-prod $*" >> "$LOG" }

MANIFEST="ops/config/control-plane-workflows.v1.json"
CUTOVER="ops/config/control-plane-scheduler-cutover.v1.json"

if (( APPLY )); then
  dirty=$(cd "$REPO" && git status --porcelain -- "$MANIFEST" "$CUTOVER")
  if [[ -n "$dirty" ]]; then
    stamp "REFUSED uncommitted manifest: ${dirty//$'\n'/ · }"
    print -u2 "REFUSED: the control-plane manifest is uncommitted — commit it first."
    print -u2 "$dirty"
    exit 1
  fi
fi

# Same non-interactive auth and same project pin as bin/migrate-prod.sh, and for
# the same reasons stated there: a Neon API key needs no browser, and production
# is pinned BY ID because a name lookup can drift to another project.
if [ -z "${NEON_API_KEY:-}" ] && [ -f "$HOME/.config/carr/db.env" ]; then
  . "$REPO"/bin/routine-credential-env.sh
  carr_require_sourceable_db_env "sync-control-plane-prod" || exit $?
  set -a; . "$HOME/.config/carr/db.env"; set +a
fi
if [ -z "${NEON_API_KEY:-}" ]; then
  stamp "WARN no NEON_API_KEY — falling back to interactive neonctl auth"
  print -u2 "note: NEON_API_KEY is not set, so this needs a browser login if the"
  print -u2 "      saved neonctl session has expired."
fi

NEON_PROJECT_PRODUCTION="steep-field-48688294"
NEONCTL="$REPO/mcp-server/node_modules/.bin/neonctl"
[[ -x "$NEONCTL" ]] || NEONCTL="neonctl"

DSN="$("$NEONCTL" connection-string production \
        --project-id "$NEON_PROJECT_PRODUCTION" \
        --role-name neondb_owner 2>/tmp/sync-control-plane-neonctl.err)"
if [[ -z "$DSN" ]]; then
  reason="$(head -1 /tmp/sync-control-plane-neonctl.err 2>/dev/null)"
  rm -f /tmp/sync-control-plane-neonctl.err
  stamp "FAIL no DSN from neonctl: ${reason:-no error text}"
  print -u2 "could not derive the production owner DSN from neonctl (logged)."
  print -u2 "neonctl said: ${reason:-nothing at all}"
  exit 1
fi
rm -f /tmp/sync-control-plane-neonctl.err

PY="$REPO/.venv/bin/python"

if (( ! APPLY )); then
  print "== read-only: comparing production's registry against $MANIFEST =="
  set +e
  DATABASE_URL="$DSN" "$PY" "$REPO/ops/control-plane-registry-gate.py"
  rc=$?
  set -e
  if (( rc == 0 )); then
    stamp "OK read-only: production already matches the manifest"
    print "production already matches the reviewed manifest; --apply would change nothing."
  else
    stamp "DIFF read-only rc=$rc"
    print ""
    print "production differs from the reviewed manifest (above). Re-run with --apply."
  fi
  exit $rc
fi

print "== syncing the reviewed manifest into production =="
if ! DATABASE_URL="$DSN" "$PY" "$REPO/tools/control-plane.py" sync; then
  rc=$?
  stamp "FAIL sync rc=$rc"
  print -u2 "the sync itself failed; production may be partially unchanged."
  print -u2 "the sync runs in one transaction per invocation — re-read with a"
  print -u2 "read-only run before assuming anything about the current state."
  exit $rc
fi

# THE SUCCESS SIGNAL IS THE GATE, NOT THE SYNC. The sync prints its own counts,
# which are counts of what it was ASKED to write. Only the gate reads production
# back and compares every contract column against the manifest, so it is the
# gate's exit code this script returns.
print ""
print "== reading production back through ops/control-plane-registry-gate.py =="
set +e
DATABASE_URL="$DSN" "$PY" "$REPO/ops/control-plane-registry-gate.py"
rc=$?
set -e
if (( rc == 0 )); then
  stamp "OK applied and verified"
  print "production's registry now matches the reviewed manifest, read back and checked."
else
  stamp "FAIL applied but gate rc=$rc"
  print -u2 "THE SYNC RAN AND THE GATE STILL DISAGREES. That is a real finding:"
  print -u2 "read the differences above before running this again."
fi
exit $rc
