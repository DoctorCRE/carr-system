#!/bin/zsh
# pitr-restore-proof.sh — PROVE how recent a point production can be restored
# to (V5-F08, RPO cell), then judge it at the real current time.
#
# The proof itself is tools/pitr-restore-proof.py; read its header for the
# design. In one line: it writes a POSITIVE probe row, then a NEGATIVE one at
# least 5 s after an instant T, asks the provider API for a disposable branch
# whose parent is the production branch AT T, and requires the positive probe
# present and the negative ABSENT on that branch. Every decisive read happens in
# that one process, the branch-side ones while the branch exists, and the bound
# evidence it prints goes straight to the evaluator (no file in between):
#
#   tools/pitr-restore-proof.py prove | node mcp-server/bin/recovery-matrix-evaluate.mjs rpo -
#
# Production business data is never written: the only writes are the two
# ops.pitr_probe rows (migration 0597's own function, append-only table).
# No credential or connection string is printed or placed on an argument list.
#
#   bin/pitr-restore-proof.sh        # attended; needs the provider API credential
#
# Exit 0 only when the evaluator passes the RPO cell; 1 otherwise; 130 on
# Ctrl-C, 129 on a closed terminal. Every branch the proof creates carries a
# one-hour provider-side expiry; the proof also deletes its branches on every
# exit path, and the next run sweeps any a crash left behind.
set -u
trap 'exit 130' INT TERM
trap 'exit 129' HUP

REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/usr/local/opt/node@22/bin:/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
source "$REPO/bin/routine-credential-env.sh"
if [ -n "${CARR_JOB_PAYLOAD:-}" ]; then
  print -ru2 -- "pitr-restore-proof: routine dispatch refused; run it attended"
  exit 78
fi
unset NEON_API_KEY
carr_clear_routine_db_env
carr_load_routine_db_env NEON_API_KEY || exit $?
export NEON_API_KEY

PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || { print -ru2 -- "pitr-restore-proof: $PY missing (the proof needs the repo venv's psycopg)"; exit 1; }
if [ "$#" -ne 0 ]; then
  print -ru2 -- "pitr-restore-proof: takes no arguments (the stand-in-parent rehearsal mode was removed)"
  exit 2
fi

set -o pipefail
"$PY" "$REPO/tools/pitr-restore-proof.py" prove \
  | node "$REPO/mcp-server/bin/recovery-matrix-evaluate.mjs" rpo -
exit $?
