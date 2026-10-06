#!/bin/zsh
# fleet-sync.sh — each Mac keeps its OWN checkout and its OWN installed gates
# current, so neither partner has to carry an update to the other by hand.
#
# WHY THIS EXISTS (2026-08-18). Dell's Mac was migrated clean on 2026-08-11 with
# its adapter wiring exact. Over the next week the repo grew new gates
# (map-architecture, costar-lane, draft-export). Nothing on his machine ever
# fetched them, so his sessions started announcing the whole hook tuple as
# missing — and, running with no gates, had no escalation gate to stop them
# sending every blocker to Joe. He was pulled between two machines twice in one
# day: "i cant keep running back and forth between dells computer and mine."
#
# WHY THE SELF-HEAL IN gate-integrity.py IS NOT ENOUGH ON ITS OWN. That repairs
# an install that lags THE LOCAL REPO. It re-runs the installer from whatever
# ops/config/hooks.json is on disk. If the CHECKOUT is the stale thing, it
# faithfully reinstalls stale gates and reports success. Something has to move
# the checkout, and before this job nothing did: all nineteen scheduled jobs
# were audited on 2026-08-18 and not one touched a git remote.
#
# WHAT IT REFUSES TO DO, which is most of its design:
#   - It NEVER discards local work. A dirty tracked tree exits 78 (skip) with
#     the paths named. Dell's machine deliberately carries two uncommitted edits
#     from his migration; a blind fast-forward would have destroyed them.
#   - It NEVER merges, rebases, resets or force-anythings. Fast-forward only.
#     If the branches have diverged it skips and says so — a diverged checkout
#     is a human question, not a job's decision.
#   - It NEVER runs off main, and never inside a worktree. Worktree-per-session
#     means most sessions run somewhere else entirely; syncing from there would
#     move a branch somebody is mid-edit on.
#
# SIBLING REPOS (2026-09-23). Joe wants EVERY GitHub checkout on a Mac to stay
# current, not just this one: "if im on my macbook working or my studio or
# whatever, i dont want to have to remember to do that." The sibling list
# (currently doctorcre-app, software-factory) lives in ONE place,
# ops/config/fleet-sync-siblings.json, and each sibling is synced through
# tools/fleet_sync_sibling_safety.py under the exact same fail-closed contract
# as this checkout: absent -> skip, off-main -> skip, dirty -> skip and name
# the paths, diverged -> skip, otherwise fetch + `merge --ff-only`. A sibling's
# own skip or failure is INTENTIONALLY ignored below — see sync_siblings() —
# so it can never prevent this checkout's own sync, the wiring re-render, or
# change this job's own exit code.
#
# Exit codes follow bin/run-scheduled.sh's convention: 0 did something or was
# already current, 78 deliberately skipped and said why, anything else failed.
# This convention is about the CANONICAL checkout only; the sibling repos are
# reported (one line each) but never participate in it.

set -u
EX_CONFIG=78

# Never wait on a login prompt. This runs unattended from launchd; if GitHub
# ever wants credentials again (expired token, revoked login), a git allowed to
# prompt sits waiting for an answer nobody types and the run stalls instead of
# skipping. With these, the fetch fails fast and the existing "fetch failed"
# skip path reports it. The keychain credential helper still answers silently.
export GIT_TERMINAL_PROMPT=0
export GCM_INTERACTIVE=never
export GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -o BatchMode=yes}"

REPO="${0:A:h:h}"
cd "$REPO" || { print -ru2 -- "fleet-sync: cannot enter $REPO"; exit 1 }
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY=python3

# Canonical checkout only. --git-common-dir differs from --git-dir inside a
# worktree, which is the cheapest reliable test. A worktree invocation is
# anomalous enough (this job's REPO-relative sibling paths would not even be
# trustworthy) that it skips everything, siblings included.
common="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"
gitdir="$(git rev-parse --path-format=absolute --git-dir 2>/dev/null)"
if [ "$common" != "$gitdir" ]; then
  print -r -- "fleet-sync: SKIP — this is a worktree, not the canonical checkout"
  exit $EX_CONFIG
fi

# sync_siblings — fast-forward every sibling checkout named in
# ops/config/fleet-sync-siblings.json (the single source of truth for the
# list), through the tools/fleet_sync_sibling_safety.py LIBRARY (no shebang,
# no main guard on purpose — this inline snippet is the only dispatch, so the
# helper never becomes a second sealed SCAC ingress for the same one job this
# already-inventoried script covers). Exit statuses are printed but
# deliberately never inspected: a sibling's outcome must never affect
# $canonical_status or stop the wiring re-render below.
sync_siblings() {
  local siblings_json="$REPO/ops/config/fleet-sync-siblings.json"
  [ -f "$siblings_json" ] || return 0
  local siblings
  siblings="$("$PY" -c '
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    for name in data.get("siblings", []):
        print(name)
except Exception:
    pass
' "$siblings_json" 2>/dev/null)"
  [ -n "$siblings" ] || return 0

  print -r -- "fleet-sync: sibling repos —"
  local name
  for name in ${(f)siblings}; do
    "$PY" -c '
import sys
sys.path.insert(0, sys.argv[4])
from fleet_sync_sibling_safety import EXIT_CODES, sync_sibling
status, message = sync_sibling(sys.argv[1], sys.argv[2], sys.argv[3])
print("fleet-sync:   " + message)
sys.exit(EXIT_CODES[status])
' "${REPO:h}/$name" "$name" main "$REPO/tools"
    # Intentionally ignore $? here. See the header note: a sibling's skip or
    # failure is never allowed to change this job's own exit code.
  done
}

canonical_status=0
canonical_skip=0

branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
if [ "$branch" != "main" ]; then
  print -r -- "fleet-sync: SKIP — checkout is on '$branch', not main; leaving it alone"
  canonical_status=$EX_CONFIG
  canonical_skip=1
fi

if [ "$canonical_skip" -eq 0 ] && ! git fetch --quiet origin main 2>/dev/null; then
  print -ru2 -- "fleet-sync: fetch of origin/main failed (offline, or no credential)"
  canonical_status=$EX_CONFIG
  canonical_skip=1
fi

if [ "$canonical_skip" -eq 0 ]; then
  local_sha="$(git rev-parse HEAD)"
  remote_sha="$(git rev-parse origin/main)"

  if [ "$local_sha" = "$remote_sha" ]; then
    print -r -- "fleet-sync: checkout already current at ${local_sha:0:8}"
  else
    # Tracked changes only. Untracked scratch is a session's business, not this
    # job's, and refusing on it would mean this never runs on a working machine.
    if ! dirt_reason="$("$PY" "$REPO/tools/fleet_sync_safety.py" "$REPO" origin/main)"; then
      print -r -- "fleet-sync: SKIP — local changes present, refusing to fast-forward over them:"
      print -r -- "    $dirt_reason"
      canonical_status=$EX_CONFIG
      canonical_skip=1
    elif ! git merge-base --is-ancestor HEAD origin/main; then
      # Fast-forward only: HEAD must already be an ancestor of origin/main.
      print -r -- "fleet-sync: SKIP — main has diverged from origin/main; a human decides this one"
      canonical_status=$EX_CONFIG
      canonical_skip=1
    elif ! git merge --ff-only origin/main >/dev/null 2>&1; then
      print -ru2 -- "fleet-sync: fast-forward failed unexpectedly"
      canonical_status=1
      canonical_skip=1
    else
      print -r -- "fleet-sync: fast-forwarded ${local_sha:0:8} -> ${remote_sha:0:8}"
    fi
  fi
fi

# Siblings sync regardless of the canonical outcome above (short of the
# worktree check, which already exited). A dirty or off-main carr-system
# checkout says nothing about whether doctorcre-app or software-factory are
# safe to fast-forward.
sync_siblings

if [ "$canonical_skip" -eq 1 ]; then
  exit $canonical_status
fi

# Re-render the installed wiring from whatever the checkout now holds. Idempotent
# by design and the same installer bin/migrate-dell.sh runs; on an already-correct
# machine it changes nothing.
# run-scheduled preserves a PID-bound launchd identity before the nested zsh
# replaces XPC_SERVICE_NAME with `0`.  Re-prove that the attested PID is our
# actual parent and is still launchd's loaded fleet process.  A manual ambient
# variable therefore cannot claim the exemption.
attested_pid="${CARR_RUN_SCHEDULED_LAUNCHD_PID:-}"
loaded_parent=0
if launchctl print "gui/$UID/com.carr.fleet-sync" 2>/dev/null \
   | grep -Eq "^[[:space:]]*pid = ${PPID}[[:space:]]*$"; then
  loaded_parent=1
fi
valid_attestation=0
if [ "${CARR_RUN_SCHEDULED_XPC_SERVICE_NAME:-}" = "com.carr.fleet-sync" ] \
    && [[ "$attested_pid" = <-> ]] \
    && [ "$PPID" = "$attested_pid" ] \
    && [ "$loaded_parent" -eq 1 ]; then
  valid_attestation=1
fi
if [ "$valid_attestation" -eq 1 ]; then
  export CARR_CONFIG_AS_CODE_ACTIVE_LAUNCHD_LABEL=com.carr.fleet-sync
elif [ "${CARR_RUN_SCHEDULED_FLEET_SELF_CLAIM:-}" = 1 ] \
    || [ -n "${CARR_RUN_SCHEDULED_XPC_SERVICE_NAME:-}" ] \
    || [ -n "${CARR_RUN_SCHEDULED_LAUNCHD_PID:-}" ] \
    || [ "$loaded_parent" -eq 1 ]; then
  unset CARR_CONFIG_AS_CODE_ACTIVE_LAUNCHD_LABEL
  unset CARR_RUN_SCHEDULED_XPC_SERVICE_NAME
  unset CARR_RUN_SCHEDULED_LAUNCHD_PID
  unset CARR_RUN_SCHEDULED_FLEET_SELF_CLAIM
  print -ru2 -- "fleet-sync: ACTIVE-SELF PROOF REFUSED — self evidence exists, but"
  print -ru2 -- "    the exact fleet-sync/fleet.sync canonical tuple, wrapper parent PID,"
  print -ru2 -- "    and live launchctl PID do not all agree; refusing config install so"
  print -ru2 -- "    this job cannot unload itself"
  exit 1
else
  unset CARR_CONFIG_AS_CODE_ACTIVE_LAUNCHD_LABEL
fi
unset CARR_RUN_SCHEDULED_XPC_SERVICE_NAME
unset CARR_RUN_SCHEDULED_LAUNCHD_PID
unset CARR_RUN_SCHEDULED_FLEET_SELF_CLAIM
if ! "$PY" "$REPO/ops/config-as-code.py" install --apply </dev/null; then
  print -ru2 -- "fleet-sync: config-as-code install --apply failed"
  exit 1
fi

# The board agent is an existing local.* label, not a com.carr.* template.
# Repair its runtime after main advances; preserve its schedule and board state.
if [[ -f "$HOME/Library/LaunchAgents/local.carr-progress-board.plist" ]]; then
  if ! "$PY" "$REPO/ops/config-as-code.py" install-progress-board --apply </dev/null; then
    print -ru2 -- "fleet-sync: canonical progress-board installation failed"
    exit 1
  fi
fi

print -r -- "fleet-sync: installed wiring re-rendered from $(git rev-parse --short HEAD)"
exit 0
