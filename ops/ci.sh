#!/bin/bash
# Launch the canonical Python CI controller.
if [ -z "${BASH_VERSION:-}" ]; then
  exec /bin/bash "$0" "$@"
fi

set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

COMMON_DIR="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || COMMON_DIR="$REPO/.git"
PY="$(dirname "$COMMON_DIR")/.venv/bin/python"
[ -x "$PY" ] || PY=python3
export CARR_CI_PYTHON="$PY"
if [ "${1:-}" = "--doc-drift" ]; then
  shift
  exec "${CARR_DOC_DRIFT_PYTHON:-$PY}" "$REPO/scripts/doc-drift/ci.py" "$@"
fi
exec "$PY" "$REPO/ops/ci.py" "$@"
