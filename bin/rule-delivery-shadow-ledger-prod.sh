#!/bin/zsh
# Sole live append door for shadow finding dispositions and epoch receipts.
set -eu
REPO="${0:A:h:h}"
PYTHON="$REPO/.venv/bin/python"
[[ -x "$PYTHON" ]] || { print -ru2 -- "rule-delivery-shadow-ledger: repo venv is required"; exit 78; }

source "$REPO/bin/routine-credential-env.sh"
carr_clear_routine_db_env
carr_load_routine_db_env CARR_DB_JOBS_URL
[[ -n "${CARR_DB_JOBS_URL:-}" ]] || {
  print -ru2 -- "rule-delivery-shadow-ledger: CARR_DB_JOBS_URL is required"
  exit 78
}

exec env -i HOME="$HOME" PATH="$PATH" LANG="${LANG:-C}" TMPDIR="${TMPDIR:-/tmp}" \
  CARR_DB_JOBS_URL="$CARR_DB_JOBS_URL" \
  "$PYTHON" "$REPO/ops/rule-delivery-shadow-ledger.py" "$@"
