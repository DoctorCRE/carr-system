#!/bin/zsh
# smoke-and-record.sh — run the MCP probe suite, then write its verdict to the
# dead-man freshness table so `smoke` can never again read "stale" while passing.
#
# The 2026-08-02 cold-session audit found `smoke` reading last-ok 2026-07-30 while the
# suite itself passed 17/17 on demand: nothing wrote its heartbeat. The heartbeat is
# recorded HERE, from the probe's real exit code, so a green row can only mean the
# probes actually ran and actually passed.
#
# ── RE-ARMED 2026-08-14 (Phase 1 Program 3) ─────────────────────────────────────
# THIS SCRIPT HAD NO CALLER FOR TEN DAYS, and the reason mattered. It was pulled
# from bin/nightly.sh on 2026-08-04 (see the block there) because smoke-reads.sh
# authenticated with the legacy PARTNER_TOKENS bearer, which had been retired the
# day before: the suite returned 23 failed / 0 passed every night and its red
# became background noise within one day. Joe ruled against minting a machine
# credential to bring it back.
#
# The narrower credential landed anyway and is what makes re-arming honest rather
# than a reversal of that ruling: PROBE_TOKENS maps to ONE actor pinned
# server-side to a locked 'probe' capability profile — reads, plus exactly three
# write verbs replayed under frozen idempotency keys. It is not the retired
# bearer rebuilt; that one authenticated as a full human actor on the full profile.
#
# It was ALSO re-armed only after the suite was made honestly green. On
# 2026-08-14 it returned 31 passed / 3 failed, and all three were fixture drift
# rather than regressions — the system answered correctly in every case:
#   * the auto-edge check had no probe-profile gate where five sibling checks do,
#     so the server's correct not_in_profile refusal printed as a hard FAIL;
#   * a whole-payload substring match for retired_aliases:0 broke when a second
#     live org row, "Henry Schein, Inc.", began matching the same query;
#   * a fixture asserting C-155 has nothing blocked expired when the ternary
#     Joe->Okafor edge was recorded, and reporting it is loop #133 working.
# Scheduling a red canary is what created the background noise the first time.
# DO NOT RE-ARM A RED SUITE; fix it or gate it first.
#
# TWO LEDGERS, ON PURPOSE, AND NEITHER REPLACES THE OTHER:
#   export_run  — the dead-man freshness row `smoke`, which integrity-digest and
#                 tools/health-check.py already read. Untouched.
#   ops.run     — the Program 3 golden-workflow CHECK run, written through
#                 tools/ops-record.py (the collector bin/nightly.sh uses). It
#                 carries the correlation id, the environment and the provenance
#                 that export_run has no columns for, which is what lets a deploy,
#                 the check that caught its breakage and the job that failed all
#                 appear in one `ops-record trace <id>`.
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO" || exit 1

PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY=python3

STARTED="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
OUT="$(./mcp-server/smoke-reads.sh 2>&1)"
probe_exit=$?
print -r -- "$OUT"

# The REAL pass count, not the 17 that record-smoke.py defaults to — that default
# has been stale since the suite grew past it, and the count is the one field
# that makes a shrinking suite visible in the freshness table.
passed="$(print -r -- "$OUT" | sed -n 's/^passed \([0-9]\{1,\}\) .*/\1/p' | tail -1)"
[ -n "$passed" ] || passed=0

"$PY" tools/record-smoke.py "$probe_exit" "$passed"

# The Program 3 check run. Its exit code is ignored for the same reason
# bin/nightly.sh ignores it: a step that goes unrecorded must make the service
# read `unknown` on the next health look, never fail the verification it was
# observing. CARR_CORRELATION_ID is inherited when a chain or a deploy exports
# one, so this check joins that journey instead of starting a lone one.
if [ "$probe_exit" -eq 0 ]; then
  "$PY" tools/ops-record.py run --kind check --service carr-mcp \
    --key golden.smoke-reads --state succeeded --exit-code 0 \
    --started-at "$STARTED" --source-kind collector \
    --source-ref bin/smoke-and-record.sh \
    --detail "$passed checks passed" || true
else
  "$PY" tools/ops-record.py run --kind check --service carr-mcp \
    --key golden.smoke-reads --state failed --exit-code "$probe_exit" \
    --started-at "$STARTED" --source-kind collector \
    --source-ref bin/smoke-and-record.sh \
    --failure-class golden_workflow_check_failed \
    --detail "$passed checks passed before the suite failed" || true
fi

# NOT-CONFIGURED IS A SKIP, NOT A FAILED NIGHT. smoke-reads.sh exits 2 when it
# can find no token at all, which is precisely bin/nightly.sh's EX_CONFIG case:
# the step ran, found a credential it needs is absent, wrote nothing and said so.
# Returning 78 makes the chain record it as 'skipped' and keeps the alarm quiet,
# which is the whole reason that convention exists — a step that alarms every
# night until someone pastes a token trains both partners to stop reading alarms,
# and that is exactly how this suite was lost the first time.
#
# EXIT 3 IS NOT COVERED BY THAT AND MUST STAY LOUD. It means a token was present
# and the Worker REFUSED it — either the probe token no longer matches the
# PROBE_TOKENS secret, or the request fell through to the retired PARTNER_TOKENS
# path. Both mean post-deploy verification is silently not happening, which is a
# real failure and the one this whole re-arm exists to make visible.
if [ $probe_exit -eq 2 ]; then
  print -r -- "smoke-and-record: no MCP probe token configured — reporting SKIP (78), not a failure."
  exit 78
fi

exit $probe_exit
