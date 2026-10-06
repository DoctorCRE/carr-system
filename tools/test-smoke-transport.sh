#!/bin/zsh
# test-smoke-transport.sh — call() in mcp-server/smoke-reads.sh must tell a
# network failure apart from a wrong answer.
#
# WHY THIS EXISTS. call() set RESULT to the empty string whenever curl timed out
# or the connection dropped. Empty matches neither '"error"' nor
# '"isError":true' — the two branches every check in that suite tests first — so
# an empty body fell through to the content greps and printed as
# "expected /<pattern>/ in the result". The nightly chain then reported the whole
# golden workflow suite FAILED under a heading that reads "answer correctness".
# It happened for real on 2026-08-14 (out/nightly.log, chain run 13:03-13:08):
# two checks failed on different reps with blank detail lines, and the same suite
# passed 33/33 in the next two chain runs. The answers were never wrong.
#
# HOW IT TESTS WITHOUT A NETWORK. call() invokes `curl` by name, so a shell
# function named curl shadows the binary. Each case installs a stub with a known
# body and exit status and counts its invocations, which makes the retry itself
# observable rather than inferred from timing.
#
# THE COUNTER IS A FILE, not a variable, and that is not a style choice. call()
# captures the body with RESULT=$(curl ...), which runs the stub in a SUBSHELL:
# a variable the stub increments dies with it and reads 0 in every assertion.
# The stub appends a line per invocation instead, and the parent counts lines.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$REPO/mcp-server/smoke-reads.sh"

pass=0; fail=0
check() {
  local label="$1" cond="$2" detail="${3:-}"
  if [ "$cond" = "1" ]; then print -r -- "  ok    $label"; pass=$((pass+1))
  else print -r -- "  FAIL  $label  $detail"; fail=$((fail+1)); fi
}

# Extract the auth helper, tunables, and call() from the real script.
# Sourcing the whole file would run its auth preflight and the entire suite.
CURL_AUTH_SRC="$(awk '/^curl_auth\(\) \{/,/^}$/' "$SRC")"
CALL_SRC="$(awk '/^CALL_ATTEMPTS=/,/^}$/' "$SRC")"
if ! print -r -- "$CURL_AUTH_SRC" | grep -q '^curl_auth() {' ||
   ! print -r -- "$CALL_SRC" | grep -q '^call() {'; then
  print -r -- "  FAIL  could not extract curl_auth() and call() from $SRC — did their shape change?"
  exit 1
fi
eval "$CURL_AUTH_SRC"
eval "$CALL_SRC"

API="https://example.invalid/mcp"
TOKEN="test-token"
_id=0
CALL_ATTEMPTS=3
CALL_RETRY_SLEEP=0

# EXPLICIT XXXXXX TEMPLATE, because `mktemp -t <prefix>` is not portable: BSD
# mktemp (macOS) appends a random suffix to the prefix, while GNU coreutils
# treats -t as deprecated and demands a template ending in at least six X's,
# failing with "too few X's" and printing nothing. TALLY then held the empty
# string, every `wc -l < ""` failed, and all four count assertions reported
# `curl ran  time(s)` with no number at all. The content assertions passed the
# whole time, so this looked like a product failure and was not one. It passed
# the local pre-push hook on this Mac and failed on the ubuntu runner
# (2026-08-14, run 31842748722) — CI catching exactly what a one-platform
# check cannot.
TALLY="$(mktemp "${TMPDIR:-/tmp}/smoke-transport-tally.XXXXXX")"
if [ -z "${TALLY:-}" ] || [ ! -f "$TALLY" ]; then
  # Never let a broken counter degrade into empty assertions again — an
  # unusable tally must stop the run, not quietly compare "" against 3.
  print -r -- "  FAIL  could not create the invocation-counter temp file"
  exit 1
fi
trap 'rm -f "$TALLY"' EXIT
tally_reset() { : > "$TALLY"; }
tally_count() { wc -l < "$TALLY" | tr -d ' '; }

print -r -- "call() transport handling -> $SRC"
print -r --

# 1. THE REGRESSION. Every attempt comes back empty, the way a timeout does.
#    RESULT must never be left empty, and must carry the '"error"' key that
#    every call site in the suite greps for before it looks at content.
tally_reset
curl() { print -r -- x >> "$TALLY"; return 28; }
call find '{"query":"anything"}'
CURL_CALLS="$(tally_count)"
check "an empty response becomes an error envelope, not an empty string" \
      "$([ -n "$RESULT" ] && echo 1 || echo 0)" "RESULT was empty"
check "the envelope carries the \"error\" key every call site greps first" \
      "$(print -r -- "$RESULT" | grep -q '"error"' && echo 1 || echo 0)" "RESULT=$RESULT"
check "the envelope names the transport, so it cannot read as a wrong answer" \
      "$(print -r -- "$RESULT" | grep -q 'transport:' && echo 1 || echo 0)" "RESULT=$RESULT"
check "it does NOT masquerade as a verb-level failure" \
      "$(print -r -- "$RESULT" | grep -q '"isError":true' && echo 0 || echo 1)" "RESULT=$RESULT"
check "a failing call is retried CALL_ATTEMPTS times" \
      "$([ "$CURL_CALLS" -eq 3 ] && echo 1 || echo 0)" "curl ran $CURL_CALLS time(s), expected 3"

# 2. THE PROPERTY THAT MUST SURVIVE. A real answer is returned untouched and is
#    NOT retried — a suite that silently re-called a write verb would be worse
#    than the bug it replaced.
tally_reset
curl() { print -r -- x >> "$TALLY"; print -r -- '{"result":{"content":[{"text":"real answer"}]}}'; return 0; }
call find '{"query":"anything"}'
CURL_CALLS="$(tally_count)"
check "a successful call returns its body verbatim" \
      "$(print -r -- "$RESULT" | grep -q 'real answer' && echo 1 || echo 0)" "RESULT=$RESULT"
check "a successful call is issued exactly once" \
      "$([ "$CURL_CALLS" -eq 1 ] && echo 1 || echo 0)" "curl ran $CURL_CALLS time(s), expected 1"

# 3. THE POINT OF THE RETRY. One blip must not cost the run: attempt 1 comes
#    back empty, attempt 2 answers, and the caller sees the answer.
tally_reset
curl() {
  print -r -- x >> "$TALLY"
  [ "$(wc -l < "$TALLY" | tr -d ' ')" -eq 1 ] && return 28
  print -r -- '{"result":{"content":[{"text":"answer after one blip"}]}}'; return 0
}
call find '{"query":"anything"}'
CURL_CALLS="$(tally_count)"
check "a single blip is absorbed by the retry" \
      "$(print -r -- "$RESULT" | grep -q 'answer after one blip' && echo 1 || echo 0)" "RESULT=$RESULT"
check "…and it stopped as soon as it got a body" \
      "$([ "$CURL_CALLS" -eq 2 ] && echo 1 || echo 0)" "curl ran $CURL_CALLS time(s), expected 2"

# 4. A 200 WITH AN EMPTY BODY is the case curl's own --retry would not catch,
#    which is why the retry is tested on the body rather than on the exit code.
tally_reset
curl() { print -r -- x >> "$TALLY"; return 0; }
call find '{"query":"anything"}'
CURL_CALLS="$(tally_count)"
check "an exit-0 call with no body is treated as a transport failure" \
      "$(print -r -- "$RESULT" | grep -q 'transport:' && echo 1 || echo 0)" "RESULT=$RESULT"
check "…and it too is retried, not accepted on the first empty body" \
      "$([ "$CURL_CALLS" -eq 3 ] && echo 1 || echo 0)" "curl ran $CURL_CALLS time(s), expected 3"

print -r --
print -r -- "passed $pass · failed $fail"
[ "$fail" -eq 0 ] || exit 1
