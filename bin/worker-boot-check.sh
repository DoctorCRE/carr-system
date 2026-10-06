#!/bin/sh
# worker-boot-check.sh — proves the CARR MCP Worker actually BOOTS in
# workerd, not merely that it bundles.
#
# WHY THIS EXISTS (DoctorCRE V5-R02 review, PR #1245, 2026-09-24). The
# reviewer's worktree at scratchpad/pr1245 reproduced a real production
# crash: mcp-server/src/workflow-cutover.v5.js called
# `fileURLToPath(import.meta.url)` at MODULE SCOPE, and tools.js imports that
# module unconditionally. In workerd there is no filesystem and no
# import.meta.url-relative repo path the way there is on Node, so that line
# throws a TypeError the instant the Worker's script is evaluated -- before
# any request is routed, before wrangler's own bundling step would ever
# catch it (esbuild only compiles the module graph; it does not EXECUTE it).
# Every verb would have gone down on the next deploy. Nothing in ops/ci.sh
# ran the Worker in a Worker runtime, so nothing caught it. This is a new
# class of defect CI did not have a check for: code that is syntactically
# fine, type-checks fine, and passes every Node-side unit test, but never
# runs because the Worker never finishes loading.
#
# WHAT THIS CHECKS, PRECISELY. It builds the Worker exactly the way
# bin/deploy-worker.sh would (same wrangler binary, same source tree, same
# main entrypoint), boots it in a REAL local workerd instance via
# `wrangler dev`, and calls GET /healthz -- a route that touches no secret,
# no database and no binding, so a 200 from it proves only one thing: the
# Worker's module graph finished loading and its fetch handler ran. That is
# exactly the class of failure this check exists to catch; it deliberately
# does not attempt to prove the Worker is otherwise correct (mcp-server's own
# `node --test` suite already owns that).
#
# WHY A THROWAWAY ASSETS DIRECTORY. wrangler.toml's [assets] binding points
# at ../out/doctorcre-artifacts/current, which is populated by a separate,
# heavier build this check has no business depending on. `wrangler dev
# --assets <dir>` overrides the binding's source directory for this run only
# -- the committed wrangler.toml is never touched -- with a minimal stub that
# exists purely so the ASSETS binding has somewhere to point.
#
# Usage: bin/worker-boot-check.sh
set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORKER_DIR="$REPO/mcp-server"
WRANGLER="$WORKER_DIR/node_modules/.bin/wrangler"

[ -x "$WRANGLER" ] || {
  echo "worker-boot-check: wrangler not found at $WRANGLER (run npm install in mcp-server/)." >&2
  exit 1
}

PORT="${WORKER_BOOT_CHECK_PORT:-18799}"
ASSETS_DIR="$(mktemp -d 2>/dev/null || mktemp -d -t carr-worker-boot)"
echo "<!doctype html><title>boot check stub</title>" > "$ASSETS_DIR/index.html"
LOG="$(mktemp 2>/dev/null || mktemp -t carr-worker-boot-log)"

# wrangler dev writes bundles and local state under mcp-server/.wrangler/.
# An ignored directory left inside mcp-server/ makes every later exact-source
# check on this checkout refuse ("uncommitted or ignored inputs"; see
# ops/exact-recovery-runtime-selftest.py), so the check removes the directory
# again when it created it, and leaves one it found alone.
WRANGLER_STATE_DIR="$WORKER_DIR/.wrangler"
if [ -e "$WRANGLER_STATE_DIR" ]; then WRANGLER_STATE_PREEXISTED=1; else WRANGLER_STATE_PREEXISTED=0; fi

cleanup() {
  if [ -n "${WRANGLER_PID:-}" ]; then
    kill "$WRANGLER_PID" >/dev/null 2>&1 || true
    wait "$WRANGLER_PID" 2>/dev/null || true
  fi
  rm -rf "$ASSETS_DIR" "$LOG" >/dev/null 2>&1 || true
  if [ "$WRANGLER_STATE_PREEXISTED" -eq 0 ]; then
    rm -rf "$WRANGLER_STATE_DIR" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

(
  cd "$WORKER_DIR" &&
  exec "$WRANGLER" dev --port "$PORT" --ip 127.0.0.1 --assets "$ASSETS_DIR"
) > "$LOG" 2>&1 &
WRANGLER_PID=$!

READY=0
i=0
while [ "$i" -lt 40 ]; do
  if grep -q "Ready on" "$LOG" 2>/dev/null; then READY=1; break; fi
  if ! kill -0 "$WRANGLER_PID" 2>/dev/null; then break; fi
  sleep 1
  i=$((i + 1))
done

if [ "$READY" -ne 1 ]; then
  echo "worker-boot-check: FAIL — the Worker never reached 'Ready' in workerd (module load likely threw)." >&2
  echo "---- wrangler dev output ----" >&2
  cat "$LOG" >&2
  exit 1
fi

HEALTHZ_STATUS="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$PORT/healthz" 2>/dev/null || echo "curl_failed")"

if [ "$HEALTHZ_STATUS" != "200" ]; then
  echo "worker-boot-check: FAIL — GET /healthz returned '$HEALTHZ_STATUS', not 200." >&2
  echo "---- wrangler dev output ----" >&2
  cat "$LOG" >&2
  exit 1
fi

echo "worker-boot-check: OK — Worker booted in workerd; GET /healthz -> 200."
