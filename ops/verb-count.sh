#!/bin/sh
# verb-count.sh — print the number of verbs the MCP registry would ship.
#
# WHY THIS IS ITS OWN FILE. This count is the artifact-mismatch guard: it is what
# caught loop #276, where a stale-branch deploy silently dropped 9 verbs from
# production. Until now it lived as a three-line inline snippet inside
# bin/deploy-worker.sh, which meant it only ran at DEPLOY time — after review,
# after merge, at the moment of shipping. Program 2 needs the same guard to run
# at MERGE time, and rule a8c55a47 is explicit that a manual path and an
# automated path doing the same job must be the same code, not two copies that
# drift. So the snippet moved here and both callers import it.
#
# THE COUNT IS EXACT, NOT PARSED, and that property is load-bearing. It imports
# the module and reads Object.keys(TOOLS).length. A regex over the source
# undercounts — 61 against a real 66 on the branch that caused loop #276 — and a
# guard that reports the wrong number is worse than no guard, because it reports
# it confidently.
#
# Usage:  ops/verb-count.sh <worker-dir>      # prints an integer on stdout
#         ops/verb-count.sh <worker-dir> --check-retirements <deployed-count>
# The checked form imports the exact names and applies verb-loss-guard.py.
# CI and deploy use this one implementation, including the committed ledger.
# Exits non-zero, printing nothing to stdout, when the registry will not import.
# A refusal to count is never the same as a count of zero: the caller must treat
# a non-zero exit as "unmeasured", not as "shrank to nothing".

set -eu

CHECK_RETIREMENTS=0
case "$#" in
  1) ;;
  3)
    [ "$2" = "--check-retirements" ] || exit 64
    CHECK_RETIREMENTS=1
    ;;
  *) echo "usage: verb-count.sh <worker-dir> [--check-retirements <deployed-count>]" >&2; exit 64 ;;
esac

# The deploy wrapper may count a typed-recovery candidate from a clean,
# detached historical worktree.  Those worktrees deliberately have no ignored
# node_modules input, so resolving bare ESM imports from WORKER_DIR would fail
# even though the current wrapper checkout has the verified dependency tree
# that Wrangler will use.  Import through a temporary symlink topology instead:
# the historical worker remains the exact source, while Node resolves packages
# from this checkout's dependency runtime.  --preserve-symlinks is essential;
# without it Node follows the worker symlink back into the detached worktree
# before resolving packages.  Nothing is written into the exact source root.
SCRIPT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
RUNTIME_NODE_MODULES="$SCRIPT_ROOT/mcp-server/node_modules"
[ -d "$RUNTIME_NODE_MODULES" ] || {
  echo "verb-count.sh: verified runtime dependencies not found at $RUNTIME_NODE_MODULES" >&2
  exit 1
}

WORKER_DIR="${1:-}"
if [ -z "$WORKER_DIR" ]; then
  echo "verb-count.sh: needs the worker directory as its one argument" >&2
  exit 64
fi
if [ ! -d "$WORKER_DIR" ]; then
  echo "verb-count.sh: worker directory does not exist: $WORKER_DIR" >&2
  exit 66
fi
WORKER_DIR="$(CDPATH= cd -- "$WORKER_DIR" && pwd)"
if [ ! -f "$WORKER_DIR/src/tools.js" ]; then
  echo "verb-count.sh: no registry at $WORKER_DIR/src/tools.js" >&2
  exit 66
fi

IMPORT_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/verb-count.XXXXXX")"
trap 'rm -rf "$IMPORT_ROOT"' EXIT HUP INT TERM
ln -s "$WORKER_DIR" "$IMPORT_ROOT/mcp-server"
ln -s "$RUNTIME_NODE_MODULES" "$IMPORT_ROOT/node_modules"

COUNT="$(node --preserve-symlinks --input-type=module -e '
import { pathToFileURL } from "node:url";
import(pathToFileURL(process.argv[1]).href)
  .then(m => console.log(process.argv[2] === "1"
    ? JSON.stringify(Object.keys(m.TOOLS).sort()) : Object.keys(m.TOOLS).length))
  .catch(e => { console.error(e.message); process.exit(1); });' \
  "$IMPORT_ROOT/mcp-server/src/tools.js" "$CHECK_RETIREMENTS" 2>&1)" || {
  echo "verb-count.sh: the registry did not import — $COUNT" >&2
  exit 1
}

if [ "$CHECK_RETIREMENTS" = "1" ]; then
  PY="$SCRIPT_ROOT/.venv/bin/python"
  [ -x "$PY" ] || PY=python3
  printf '%s\n' "$COUNT" | "$PY" "$SCRIPT_ROOT/ops/verb-loss-guard.py" "$3"
  exit $?
fi

case "$COUNT" in
  ''|*[!0-9]*)
    echo "verb-count.sh: expected an integer, got: $COUNT" >&2
    exit 1
    ;;
esac

echo "$COUNT"
