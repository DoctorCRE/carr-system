#!/bin/zsh
# import-doctrine.sh — the sanctioned door for doctrine-store imports into
# PRODUCTION (Joe, 2026-08-08: "sanction the import door"), second member of
# the door family bin/migrate-prod.sh founded. Same construction: the
# credential is derived in-process via neonctl (never on a command line),
# dry-run is the default, and every invocation logs.
#
# Usage (arguments pass through to pipelines/doctrine_import.py):
#   ./bin/import-doctrine.sh --batch-no 1 --phase forced_early --class playbook \
#       --files "<vault>/DNA/writing-rules.md" ...            # dry parse
#   ./bin/import-doctrine.sh ... --apply                      # import
#
# RAILS:
#   1. --apply refuses while the importer itself has uncommitted changes —
#      production runs reviewed, committed code (the migrate-prod rail that
#      caught stranded 0074 on its first run).
#   2. Runs as app_writer, NOT the owner: the runtime-role lesson from 0076/0077
#      applied — if grants are missing, this door fails loudly instead of the
#      Worker failing later.
#   3. The importer's own ledger (doctrine_migration_batch) + hash reconcile
#      remain the real integrity rails; this script adds credential hygiene.

set -eu
REPO="${0:A:h:h}"
LOG="$REPO/out/import-doctrine.log"
mkdir -p "$REPO/out"
stamp() { print -r -- "$(date -u +%FT%TZ) import-doctrine $*" >> "$LOG" }

if [[ " $* " == *" --apply "* ]]; then
  dirty=$(cd "$REPO" && git status --porcelain pipelines/doctrine_import.py)
  if [[ -n "$dirty" ]]; then
    stamp "REFUSED uncommitted importer: $dirty"
    print -u2 "REFUSED: pipelines/doctrine_import.py has uncommitted changes — commit first."
    exit 1
  fi
  # Non-interactive auth, same reason and same source as restore-rehearse.sh,
  # migrate-prod.sh and tools/db-tap.py: an expiring browser login does not fail,
  # it prompts and times out, and this is the fourth caller that inherited it.
  if [ -z "${NEON_API_KEY:-}" ] && [ -f "$HOME/.config/carr/db.env" ]; then
    . "$REPO"/bin/routine-credential-env.sh
    carr_require_sourceable_db_env "import-doctrine" || exit $?
    set -a; . "$HOME/.config/carr/db.env"; set +a
  fi
  DSN="$(neonctl connection-string production --role-name app_writer 2>/dev/null)"
else
  DSN=""   # dry runs parse only; no credential needed
fi

if [[ " $* " == *" --apply "* && -z "$DSN" ]]; then
  stamp "FAIL no DSN from neonctl"
  print -u2 "could not derive the production app_writer DSN from neonctl (logged)."
  exit 1
fi

if DATABASE_URL="$DSN" "$REPO/.venv/bin/python" "$REPO/pipelines/doctrine_import.py" "$@"; then
  stamp "OK $*"
else
  rc=$?
  stamp "FAIL rc=$rc $*"
  exit $rc
fi
