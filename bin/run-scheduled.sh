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
if [ "$SERVICE" = "outlook-intake" ]; then
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
  receipt="$("$PY" - "$receipt_file" "$state" <<'PY_RECEIPT'
import json
import sys

reasons = frozenset("outside_window previous_run_active cleanup_failed store_missing carr_unreachable "
                    "decode_failed store_verification_low calendar_decode_empty store_unchanged "
                    "writes_failed model_failed steps_pending processed nothing_new writes_deferred "
                    "request_blocked full_disk_access_required contacts_empty contacts_read_failed timeout auth_refused "
                    "key_reuse transport_oversized no_acknowledgement reply_unreadable transport_error "
                    "invalid_request subject_unresolved database_refused setup_failed calendar_lifecycle_unsupported".split())
counts = frozenset("emails events_past events_upcoming logged preview_only full_text_notes balls_set "
                   "balls_kept unmatched model_failed steps_pending writes_failed needs_refresh_listed "
                   "calendar_lifecycle_unsupported calendar_requests_quarantined hydration_pending hydration_expired "
                   "unresolved_messages unresolved_addresses unresolved_calendar mapping_missing "
                   "mapping_ambiguous mapping_unsupported writes_deferred requests_blocked error_timeout error_auth_refused "
                   "error_key_reuse error_transport_oversized error_no_acknowledgement error_reply_unreadable "
                   "error_transport_error error_invalid_request error_subject_unresolved error_database_refused "
                   "error_setup_failed error_contacts_empty error_contacts_read_failed "
                   "error_next_action_conflict error_legacy_action_guard_required error_agenda_version_conflict "
                   "calendar_agenda_items calendar_agenda_canceled calendar_agenda_conflicts "
                   "calendar_agenda_other_accounts calendar_agenda_published calendar_agenda_failed "
                   "calendar_agenda_time_changed calendar_agenda_recurring calendar_agenda_trimmed "
                   "calendar_agenda_undecoded".split())
receipt = None
with open(sys.argv[1]) as output:
    for line in output:
        if line.startswith("outlook-intake-receipt "):
            try:
                receipt = json.loads(line[len("outlook-intake-receipt "):])
            except ValueError:
                receipt = None
if (isinstance(receipt, dict) and receipt.get("state") == sys.argv[2]
        and isinstance(receipt.get("reason"), str) and receipt["reason"] in reasons
        and isinstance(receipt.get("counts"), dict)
        and all(k in counts and type(v) is int and 0 <= v <= 1_000_000_000
                for k, v in receipt["counts"].items())):
    print(json.dumps({k: receipt[k] for k in ("state", "reason", "counts")}, sort_keys=True))
    print(receipt["reason"])
PY_RECEIPT
)"
  if [ -n "$receipt" ]; then
    detail="${receipt%%$'\n'*}"
    if [ "$state" = failed ] || [ "$state" = timed_out ]; then
      fclass=(--failure-class "${receipt##*$'\n'}")
    fi
    print -r -- "outlook-intake-receipt $detail"
  fi
  "$PY" - "$receipt_file" <<'PY_ROUTER_RECEIPT'
import json
import sys

counts = frozenset("emails routed model_failed abandoned recorded refused dropped write_failed "
                   "queue_failed more out_of_time error".split())
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
        and all(k in counts and type(v) is int and 0 <= v <= 1_000_000_000
                for k, v in receipt["counts"].items())):
    print("email-router-receipt " + json.dumps(
        {k: receipt[k] for k in ("state", "counts")}, sort_keys=True))
PY_ROUTER_RECEIPT
  rm -f "$receipt_file"
fi

corr=()
[ -n "${CARR_CORRELATION_ID:-}" ] && corr=(--correlation "$CARR_CORRELATION_ID")

mkdir -p "$REPO/out" 2>/dev/null

STATE_DIR="${CARR_RUN_SCHEDULED_STATE_DIR:-$REPO/out/run-scheduled-state}"

STATE_FILE="$STATE_DIR/$SERVICE.$RUN_KEY.last-success"
should_record=1
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
