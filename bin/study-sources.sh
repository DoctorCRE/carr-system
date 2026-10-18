#!/usr/bin/env bash
# One retrieval and one application study per URL. No interactive stdin.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/study_sources.py" "$@" < /dev/null
