#!/usr/bin/env bash
# The production smoke suite has two credential modes with different contracts:
# the probe token can observe only, while a partner session still exercises the
# fixed-key write probes. Run the real orchestration against a deterministic curl
# double so a profile change cannot turn an expected refusal into a failed deploy.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SMOKE="$REPO/mcp-server/smoke-reads.sh"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/smoke-profile.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT

cat > "$TMP_ROOT/curl" <<'CURL'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$SMOKE_PROFILE_CALL_LOG.args"
cat >> "$SMOKE_PROFILE_CALL_LOG.headers"
payload=""
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-d" ]; then
    shift
    payload="${1:-}"
    break
  fi
  shift
done
printf '%s\n' "$payload" >> "$SMOKE_PROFILE_CALL_LOG"

case "$payload" in
  *'"method":"tools/list"'*)
    # An empty published tool list closes the script's feature gates. The
    # profile contract under test is the decision to issue a write call at all.
    printf '%s\n' '{"result":{"tools":[]}}'
    ;;
  *'"ref":"dental"'*)
    printf '%s\n' '{"result":"needs_disambiguation"}'
    ;;
  *'"target":"Ric"'*)
    printf '%s\n' '{"result":"needs_disambiguation V-BNK-034"}'
    ;;
  *'"query":"CPA"'*)
    printf '%s\n' '{"result":"CPA"}'
    ;;
  *'"query":"__smoke_graph_vendor_fixture_unset__"'*)
    # SMOKE_GRAPH_VENDOR_QUERY is unset here (WR-000049, the double's fixture
    # file carries only the ball-probe subject — see run_smoke), so
    # smoke-reads.sh SKIPs this check rather than calling it; this case is
    # dead but kept so a future canned run has something to match.
    printf '%s\n' '{"result":"\"connections\""}'
    ;;
  *'"target":"C-155"'*)
    printf '%s\n' '{"result":"Synthetic Twohop \"hops\":2"}'
    ;;
  *'"target":"Nobody Smokeprobe Xyzzy"'*)
    printf '%s\n' '{"result":"No counterparty history"}'
    ;;
  *'"name":"source-attribution"'*)
    printf '%s\n' '{"result":"\"lanes\" unattributed"}'
    ;;
  *'"target":"Qwertzuiop Vraxmandel"'*)
    printf '%s\n' '{"result":"No record and no graph node matches"}'
    ;;
  *'"query":"Qwertzuiop Vraxmandel"'*)
    printf '%s\n' '{"result":"\"parties\":[] \"organizations\":[]"}'
    ;;
  *'"query":"__smoke_merged_client_fixture_unset__"'*)
    # This pattern cannot match anything a real fixture would ever query for
    # (WR-000049: SMOKE_MERGED_CLIENT_QUERY is unset in this double — see the
    # note above run_smoke — so smoke-reads.sh SKIPs the merged-record check
    # rather than calling it). Kept only so a future real query still has a
    # canned case to fall into, without hardcoding the real client name this
    # double does not need.
    printf '%s\n' '{"result":"C-046 C-036 \"merged\":true"}'
    ;;
  *'"name":"log-activity"'*|*'"name":"set-next-action"'*|*'"name":"complete-action"'*)
    printf '%s\n' '{"result":"\"ok\":true \"replayed\":true \"existing\":true"}'
    ;;
  *)
    printf '%s\n' '{"result":"ok"}'
    ;;
esac
CURL
chmod +x "$TMP_ROOT/curl"

pass=0
fail=0
check() {
  local label="$1"
  shift
  if "$@"; then
    printf '  ok    %s\n' "$label"
    pass=$((pass + 1))
  else
    printf '  FAIL  %s\n' "$label"
    fail=$((fail + 1))
  fi
}

# The double's own fixture file: synthetic, and only the ball-probe subject,
# so partner mode still exercises the fixed-key completion path (WR-000049
# moved that real deal name out of the tracked script).
printf '%s\n' 'SMOKE_BALL_PROBE_REF="Synthetic Closed Deal"' > "$TMP_ROOT/fixtures.env"

run_smoke() {
  local mode="$1" output="$2" calls="$3"
  : > "$calls"
  # SMOKE_LOCAL_FIXTURES forced to a path that cannot exist (WR-000049): this
  # double must behave the same on every machine, including Joe's own, which
  # carries a real (gitignored) mcp-server/smoke-reads.local.env with real
  # client names smoke-reads.sh would otherwise pick up — this contract test
  # is about call routing, not that fixture's content, and a canned response
  # here has no matching real data behind it anyway.
  if [ "$mode" = probe ]; then
    env -i PATH="$TMP_ROOT:$PATH" HOME="$TMP_ROOT" \
      SMOKE_PROFILE_CALL_LOG="$calls" CARR_MCP_ENV="$TMP_ROOT/no-env" \
      SMOKE_LOCAL_FIXTURES="$TMP_ROOT/fixtures.env" \
      CARR_MCP_PROBE_TOKEN=probe-token SMOKE_REPS=1 SMOKE_REP_SLEEP=0 \
      SMOKE_CALL_ATTEMPTS=1 SMOKE_CALL_RETRY_SLEEP=0 \
      bash "$SMOKE" > "$output" 2>&1
  else
    env -i PATH="$TMP_ROOT:$PATH" HOME="$TMP_ROOT" \
      SMOKE_PROFILE_CALL_LOG="$calls" CARR_MCP_ENV="$TMP_ROOT/no-env" \
      SMOKE_LOCAL_FIXTURES="$TMP_ROOT/fixtures.env" \
      CARR_MCP_TOKEN_JOE=partner-token SMOKE_REPS=1 SMOKE_REP_SLEEP=0 \
      SMOKE_CALL_ATTEMPTS=1 SMOKE_CALL_RETRY_SLEEP=0 \
      bash "$SMOKE" > "$output" 2>&1
  fi
}

has_call() {
  local calls="$1" needle="$2"
  grep -Fq "$needle" "$calls"
}

printf 'smoke profile contract -> %s\n\n' "$SMOKE"

PROBE_OUT="$TMP_ROOT/probe.out"
PROBE_CALLS="$TMP_ROOT/probe.calls"
run_smoke probe "$PROBE_OUT" "$PROBE_CALLS"
probe_rc=$?
check "probe-mode smoke completes without treating policy as a failure" test "$probe_rc" -eq 0
check "probe bearer is absent from curl process arguments" \
  bash -c '! grep -Fq probe-token "$1"' _ "$PROBE_CALLS.args"
check "probe bearer reaches curl through stdin config" \
  grep -Fq 'header = "Authorization: Bearer probe-token"' "$PROBE_CALLS.headers"
check "probe mode never calls log-activity" \
  bash -c '! grep -Fq '\''"name":"log-activity"'\'' "$1"' _ "$PROBE_CALLS"
check "probe mode never calls set-next-action" \
  bash -c '! grep -Fq '\''"name":"set-next-action"'\'' "$1"' _ "$PROBE_CALLS"
check "probe mode never calls complete-action" \
  bash -c '! grep -Fq '\''"name":"complete-action"'\'' "$1"' _ "$PROBE_CALLS"
check "probe output explicitly skips the fixed-key write path" \
  grep -Fq 'SKIP  write path' "$PROBE_OUT"
check "probe output explicitly skips the completion path" \
  grep -Fq 'SKIP  completion path' "$PROBE_OUT"
check "probe output explicitly skips the analysis path" \
  grep -Fq 'SKIP  analysis path' "$PROBE_OUT"

PARTNER_OUT="$TMP_ROOT/partner.out"
PARTNER_CALLS="$TMP_ROOT/partner.calls"
run_smoke partner "$PARTNER_OUT" "$PARTNER_CALLS"
partner_rc=$?
check "partner-mode smoke completes" test "$partner_rc" -eq 0
check "partner bearer is absent from curl process arguments" \
  bash -c '! grep -Fq partner-token "$1"' _ "$PARTNER_CALLS.args"
check "partner bearer reaches curl through stdin config" \
  grep -Fq 'header = "Authorization: Bearer partner-token"' "$PARTNER_CALLS.headers"
check "partner mode retains the fixed-key log-activity probe" \
  has_call "$PARTNER_CALLS" 'smoke-write-probe-permanent'
check "partner mode retains the set-next-action probe" \
  has_call "$PARTNER_CALLS" 'smoke-ball-probe-permanent'
check "partner mode retains the complete-action probe" \
  has_call "$PARTNER_CALLS" 'smoke-complete-probe-permanent'
check "partner mode retains the auto-edge probe" \
  has_call "$PARTNER_CALLS" 'smoke-links-probe-permanent'
check "partner mode retains the analysis probe" \
  has_call "$PARTNER_CALLS" 'smoke-analysis-probe-permanent'

printf '\npassed %s · failed %s\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
