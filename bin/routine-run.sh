#!/bin/sh
# Launch the Python routines runtime with the supplied arguments.
set -eu
routine_repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec "$routine_repo/.venv/bin/python" "$routine_repo/tools/routines/runtime.py" "$@"
