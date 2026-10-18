#!/bin/bash
# The progress board's scheduled launchd job. It publishes ONLY the JSON data
# contract; the one board UI is the interactive app page at
# https://app.doctorcre.com/progress-board (Joe's ruling 2026-09-29). There is
# no static page and no copy to sync anywhere. The same run rebuilds and
# publishes the system-wide all-repos board from gh.
set -euo pipefail
# launchd's PATH is /usr/bin:/bin:/usr/sbin:/sbin; gh lives in Homebrew. Without
# this every PR card silently stopped syncing from GitHub.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
# Run from this checkout, never from an extracted copy: the tool reads the
# release lanes, the review rules and ./run.sh from CARR_REPO_ROOT.
root="$(cd "$(dirname "$0")/.." && pwd -P)"
export CARR_REPO_ROOT="$root"
cd "$root"
python="$root/.venv/bin/python"
[ -x "$python" ] || { echo "progress-board: repository interpreter unavailable: $python" >&2; exit 1; }
exec "$python" tools/progress_board.py render carr-v5 --publish
