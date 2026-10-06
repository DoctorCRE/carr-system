#!/usr/bin/env bash
# The release budget measures the Worker's response after connection setup.
# Exercise the real deploy helper with curl shadowed so the five-sample maximum
# and connection-time exclusion remain deterministic and network-free.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$REPO/bin/deploy-worker.sh"
MEASURE_SRC="$(awk '/^measure_release_response_ms\(\) {/,/^}/' "$SRC")"
if ! printf '%s\n' "$MEASURE_SRC" | grep -q '^measure_release_response_ms() {'; then
  echo "FAIL: could not extract measure_release_response_ms() from $SRC"
  exit 1
fi
eval "$MEASURE_SRC"

PY="${PYTHON:-python3}"
TALLY="$(mktemp "${TMPDIR:-/tmp}/release-performance-tally.XXXXXX")"
trap 'rm -f "$TALLY"' EXIT
: > "$TALLY"

curl() {
  printf 'x\n' >> "$TALLY"
  case "$(wc -l < "$TALLY" | tr -d ' ')" in
    1) printf '1.714 1.964\n' ;; # slow connection, 250 ms Worker response
    2) printf '0.150 0.450\n' ;; # 300 ms
    3) printf '0.200 1.100\n' ;; # 900 ms: the measured maximum
    4) printf '0.500 0.900\n' ;; # 400 ms
    5) printf '0.100 0.650\n' ;; # 550 ms
  esac
}

measured="$(measure_release_response_ms https://example.invalid/release)"
calls="$(wc -l < "$TALLY" | tr -d ' ')"
[ "$calls" -eq 5 ] || { echo "FAIL: expected five curl samples, got $calls"; exit 1; }
[ "$measured" -eq 900 ] || {
  echo "FAIL: expected max response time 900ms with connection time excluded, got $measured"
  exit 1
}

: > "$TALLY"
curl() { printf 'not-a-timing\n'; }
if measure_release_response_ms https://example.invalid/release >/dev/null 2>&1; then
  echo "FAIL: malformed curl timing evidence was accepted"
  exit 1
fi

echo "PASS: release performance sampling excludes connection setup and keeps max-of-five"
