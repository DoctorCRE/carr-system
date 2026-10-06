#!/usr/bin/env bash
# The sanctioned CARR Grok entrypoint. See --help for options and exit codes.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/grok_run.py" "$@"
