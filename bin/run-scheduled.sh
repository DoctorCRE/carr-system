#!/bin/zsh
# Run a scheduled command with heartbeat and outcome recording.
set -u

EX_USAGE=64

HEARTBEAT_INTERVAL=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --heartbeat-interval)
      if [ "$#" -lt 2 ]; then
        print -ru2 -- "usage: --heartbeat-interval requires a value"
        exit $EX_USAGE
      fi
      HEARTBEAT_INTERVAL="$2"; shift 2 ;;
    --) shift; break ;;
    *) break ;;
  esac
done

if [ $# -lt 3 ]; then
  print -ru2 -- "usage: bin/run-scheduled.sh [--heartbeat-interval SECONDS] <service-key> <run-key> <command> [args...]"
  print -ru2 -- "  e.g. bin/run-scheduled.sh nightly-record-layer availability.matcher {{REPO}}/.venv/bin/python {{REPO}}/pipelines/availability_matcher.py"
  exit $EX_USAGE
fi

SERVICE="$1"; shift
RUN_KEY="$1"; shift

REPO="${0:A:h:h}"

LOG="$REPO/out/run-scheduled.log"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY=python3

receipt_file=""
degraded_repeat=0
if [[ "$SERVICE" = outlook-(intake|refresh|triage|outbox) ]]; then
  mkdir -p "$REPO/out" 2>/dev/null
  receipt_file="$(mktemp "$REPO/out/outlook-intake-receipt.XXXXXX")" || exit 1
  trap 'rm -f "$receipt_file"' EXIT
fi

STARTED="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

if [ -n "$receipt_file" ]; then
  "$@" > "$receipt_file" 2>> "$LOG"
  rc=$?
else
  "$@"
  rc=$?
fi

ENDED="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

case $rc in
  0)         state=succeeded ;;
  78)        state=skipped   ;;
  124|137)   state=timed_out ;;
  143)       state=cancelled ;;
  *)         state=failed    ;;
esac

fclass=()
case $state in
  failed|timed_out) fclass=(--failure-class "exit_$rc") ;;
esac

detail="$RUN_KEY exited $rc"
if [ -n "$receipt_file" ]; then
  receipt="$("$PY" - "$receipt_file" "$state" "$SERVICE" <<'PY_RECEIPT'
import json
import re
import sys

reasons = frozenset("outside_window previous_run_active cleanup_failed store_missing carr_unreachable "
                    "decode_failed store_verification_low decoder_degraded calendar_decode_empty store_unchanged "
                    "writes_failed model_failed steps_pending processed nothing_new writes_deferred "
                    "request_blocked full_disk_access_required contacts_empty contacts_read_failed timeout auth_refused "
                    "key_reuse transport_oversized no_acknowledgement reply_unreadable transport_error "
                    "invalid_request subject_unresolved database_refused setup_failed calendar_lifecycle_unsupported "
                    "email_links_read_failed email_links_pending".split())
if sys.argv[3] != "outlook-intake":
    reasons = frozenset("processed setup_failed outside_window previous_run_active nothing_pending user_active "
        "refreshed filed window_closed idle_time_unreadable no_usable_main_window refresh_list_invalid "
        "ui_error cua_error os_error wrong_account carr_find_failed cua_driver_failed folder_guide_invalid "
        "folder_guide_missing folder_guide_not_0600 folder_pane_restore_failed move_failed move_failed_twice "
        "unread_row_selected unread_set_changed unread_set_unverified cua_driver_unavailable "
        "outlook_launch_failed outlook_not_running_after_launch outlook_quit_timeout preflight_command_timeout "
        "compose_window_open".split())
prefix = sys.argv[3] + "-receipt "
# Count keys are code-built names; values carry the meaning. A snake_case name with a
# bounded non-negative integer value cannot carry mail content, so no key list drifts.
count_key = re.compile(r"[a-z][a-z0-9_]{0,63}")
receipt = None
with open(sys.argv[1]) as output:
    for line in output:
        if line.startswith(prefix):
            try:
                receipt = json.loads(line[len(prefix):])
            except ValueError:
                receipt = None
if (isinstance(receipt, dict) and isinstance(receipt.get("counts"), dict)
        and (receipt.get("state") == sys.argv[2]
        or (receipt.get("state") == "degraded" and receipt.get("reason") in ("decoder_degraded", "writes_deferred")
            and ((sys.argv[2] == "failed" and receipt.get("counts", {}).get("decoder_alarm") == 1)
                 or (sys.argv[2] == "succeeded" and receipt.get("counts", {}).get("decoder_alarm") == 0))))
        and isinstance(receipt.get("reason"), str) and receipt["reason"] in reasons
        and isinstance(receipt.get("counts"), dict)
        and all(count_key.fullmatch(k) and type(v) is int and 0 <= v <= 1_000_000_000
                for k, v in receipt["counts"].items())):
    safe = {k: receipt[k] for k in ("state", "reason", "counts")}
    if (isinstance(receipt.get("runtime_sha"), str)
            and re.fullmatch(r"[0-9a-f]{7,40}", receipt["runtime_sha"])
            and (receipt.get("runtime_behind") is None
                 or (type(receipt["runtime_behind"]) is int and 0 <= receipt["runtime_behind"] <= 1_000_000_000))):
        safe.update({k: receipt[k] for k in ("runtime_sha", "runtime_behind") if k in receipt})
        if receipt.get("runtime_note") in ("main_unknown_locally", "history_incomplete"):
            safe["runtime_note"] = receipt["runtime_note"]
    if (isinstance(receipt.get("detail"), str) and re.fullmatch(
            r"(?:outlook-intake-copy|hxprobe) exit=(?:None|-?[0-9]{1,10}) "
            r"code=(?:exit_nonzero|permission_denied|timeout|launch_failed)", receipt["detail"])):
        safe["detail"] = receipt["detail"]
    print(json.dumps(safe, sort_keys=True))
    print(receipt["reason"])
PY_RECEIPT
)"
  if [ -n "$receipt" ]; then
    detail="${receipt%%$'\n'*}"
    if [ "$state" = failed ] || [ "$state" = timed_out ]; then
      fclass=(--failure-class "${receipt##*$'\n'}")
    fi
    if [ "$state" = succeeded ] && [[ "$detail" == *'"state": "degraded"'* ]]; then
      degraded_repeat=1
    fi
    print -r -- "$SERVICE-receipt $detail"
  fi
  "$PY" - "$receipt_file" <<'PY_ROUTER_RECEIPT'
import json
import sys
import re

counts = frozenset("emails routed model_failed abandoned recorded refused dropped write_failed "
                   "queue_failed more out_of_time inherited contact_only error".split())
refusal_codes = frozenset("email_deal_evidence_not_found email_deal_phase_not_eligible email_deal_proposal_stale "
    "email_deal_quote_invalid email_next_step_evidence_not_found email_next_step_input_invalid "
    "email_next_step_proposal_stale email_next_step_quote_invalid "
    "email_proposal_confidence_invalid email_proposal_deal_invalid "
    "email_proposal_evidence_invalid email_proposal_fields_invalid email_proposal_kind_invalid "
    "email_proposal_not_found email_proposal_not_pending email_proposal_quote_invalid "
    "email_proposal_reason_required email_proposal_source_missing email_proposal_stale "
    "email_thread_ambiguous".split())
receipt = None
with open(sys.argv[1]) as output:
    for line in output:
        if line.startswith("email-router-receipt "):
            try:
                receipt = json.loads(line[len("email-router-receipt "):])
            except ValueError:
                receipt = None
if (isinstance(receipt, dict) and receipt.get("state") in ("succeeded", "failed", "skipped")
        and isinstance(receipt.get("counts"), dict)
        and all(type(v) is int and 0 <= v <= 1_000_000_000
                for k, v in receipt["counts"].items() if k in counts)
        and isinstance(receipt.get("refusal_codes", {}), dict)
        and all(type(v) is int and 0 <= v <= 1_000_000_000
                for k, v in receipt.get("refusal_codes", {}).items() if k in refusal_codes)):
    safe = {"state": receipt["state"],
            "counts": {k: v for k, v in receipt["counts"].items() if k in counts}}
    if "refusal_codes" in receipt:
        safe["refusal_codes"] = {k: v for k, v in receipt["refusal_codes"].items() if k in refusal_codes}
    types = receipt.get("model_failed_types", {})
    if isinstance(types, dict):
        types = {k: v for k, v in types.items() if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", k)
                 and type(v) is int and 0 <= v <= 1_000_000_000}
        if types:
            safe["model_failed_types"] = types
    print("email-router-receipt " + json.dumps(safe, sort_keys=True))
PY_ROUTER_RECEIPT
  rm -f "$receipt_file"
fi

corr=()
[ -n "${CARR_CORRELATION_ID:-}" ] && corr=(--correlation "$CARR_CORRELATION_ID")

mkdir -p "$REPO/out" 2>/dev/null

STATE_DIR="${CARR_RUN_SCHEDULED_STATE_DIR:-$REPO/out/run-scheduled-state}"

STATE_FILE="$STATE_DIR/$SERVICE.$RUN_KEY.last-success"
should_record=1
[ "$degraded_repeat" -eq 1 ] && should_record=0
if [ "$state" = "succeeded" ] && [ "${HEARTBEAT_INTERVAL:-0}" -gt 0 ] 2>/dev/null; then
  mkdir -p "$STATE_DIR" 2>/dev/null
  if [ -f "$STATE_FILE" ]; then
    last="$(cat "$STATE_FILE" 2>/dev/null || print -r -- 0)"
    now_epoch="$(date -u +%s)"
    elapsed=$(( now_epoch - last ))
    [ "$elapsed" -lt "$HEARTBEAT_INTERVAL" ] && should_record=0
  fi
fi

if [ "$should_record" -eq 1 ]; then
  set -A argv "$PY" "$REPO/tools/ops-spool.py" run \
    --service "$SERVICE" --key "$RUN_KEY" --state "$state" \
    --exit-code "$rc" --started-at "$STARTED" --ended-at "$ENDED" \
    --source-kind wrapper --source-ref bin/run-scheduled.sh \
    --detail "$detail" "${fclass[@]}" "${corr[@]}"
  "${argv[@]}" >> "$LOG" 2>&1
  recorder_exit=$?
  record_action=recorded
  if [ "$recorder_exit" -eq 0 ] && [ "$state" = "succeeded" ] && [ "${HEARTBEAT_INTERVAL:-0}" -gt 0 ] 2>/dev/null; then
    mkdir -p "$STATE_DIR" 2>/dev/null
    date -u +%s > "$STATE_FILE" 2>/dev/null
  fi
else
  argv=()
  recorder_exit=throttled
  record_action=throttled
fi

if [ "$state" != "succeeded" ] && [ "${HEARTBEAT_INTERVAL:-0}" -gt 0 ] 2>/dev/null; then
  rm -f "$STATE_FILE" 2>/dev/null
fi

print -r -- "$(date -u '+%Y-%m-%dT%H:%M:%SZ') run-scheduled key=$RUN_KEY service=$SERVICE child_exit=$rc state=$state record_action=$record_action recorder_exit=$recorder_exit argv=${argv[*]}" >> "$LOG"

exit $rc
