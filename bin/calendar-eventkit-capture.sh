#!/bin/sh
# calendar-eventkit-capture.sh — the unattended calendar capture, replacing the
# published-feed fetch Joe turned off on 2026-08-14.
#
# WHY THIS EXISTS. calendar-fetch-daily read a PUBLISHED calendar feed, and
# Microsoft strips ATTENDEE and ORGANIZER from that feed by design: a backfill
# over it matched 6 events out of 81. Reading the LOCAL calendar instead finds
# attendee emails on roughly half of all events — 386 of 936 on the 2026-08-13
# dump. Same meetings, the half that names who was in the room.
#
# THE ONE MECHANIC THAT MATTERS, and the reason this is a script rather than a
# line in a plist: the read MUST be launched with `open -a`, never by executing
# the bundle's inner binary. macOS attributes calendar permission to the
# RESPONSIBLE PROCESS. Launched properly, the bundle is responsible and holds the
# grant. Exec the inner binary and the responsible process is the shell, which has
# no usage description and no grant — macOS then answers DENIED without ever
# prompting. Both were measured on 2026-08-14 within two minutes of each other:
#
#     direct exec of Contents/MacOS/carr-calendar-access -> "DENIED", exit 3
#     open -a "CARR Calendar Access.app"                 -> real events, exit 0
#
# A DENIAL AND AN EMPTY CALENDAR MUST NEVER LOOK THE SAME. That confusion is the
# defect that started this whole thread: a verb answered emptily instead of
# refusing, and the empty answer was read as truth. So this script treats DENIED
# as a hard failure with its own exit code, and never reports "0 touches" when
# what actually happened was "not allowed to look".
#
# WHAT IT WRITES, and what it deliberately does not:
#   EXACT email matches  -> logged as touches. An exact address is evidence a
#                           NAMED person was in the room.
#   DOMAIN-only matches  -> reported, never logged. "Someone from that org" is
#                           not a dated touch on an individual.
#   Unknown externals    -> durably queued for local-mail search, research and
#                           evidenced canonical intake through the Model Room.
#
# RISK: YELLOW. Reads the calendar read-only, writes only internal touch records,
# sends nothing outside and publishes nothing.
#
#   bin/calendar-eventkit-capture.sh            # capture, log exact touches
#   bin/calendar-eventkit-capture.sh --dry-run  # report only, write nothing
#   bin/calendar-eventkit-capture.sh --dry-run --receipt-safe  # aggregate-only receipt output
#   bin/calendar-eventkit-capture.sh --days 14  # widen the window
set -u
umask 077

REPO="${CARR_REPO:-$HOME/carr-system}"
cd "$REPO" || { echo "calendar-capture: FAIL cannot reach $REPO" >&2; exit 1; }

APP="${CARR_CALENDAR_ACCESS_APP:-$REPO/tools/CARR Calendar Access.app}"
OUTPUT_ROOT="${CARR_CALENDAR_OUTPUT_ROOT:-$REPO/out}"
ACCESS_LOG="$OUTPUT_ROOT/calendar-access.log"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY=python3

# All callers, including shadow roots, share one OS-released exclusion.
if [ -z "${CARR_CALENDAR_LOCK_FD:-}" ]; then
  exec "$PY" -c "$(cat <<'LOCKPY'
import fcntl, os, subprocess, sys
from pathlib import Path
repo, script = sys.argv[1:3]
lock_path = Path(repo) / "out/calendar-capture.lock"
lock_path.parent.mkdir(parents=True, exist_ok=True)
fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
os.fchmod(fd, 0o600)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    print("calendar-capture: FAIL another capture is still reading", file=sys.stderr)
    sys.exit(75)
env = dict(os.environ, CARR_CALENDAR_LOCK_FD=str(fd))
sys.exit(subprocess.run(["sh", script, *sys.argv[3:]], env=env, pass_fds=(fd,)).returncode)
LOCKPY
)" "$REPO" "$0" "$@"
fi

DAYS=7
DRY=0
RECEIPT_SAFE=0
CANARY=0
CANARY_SNAPSHOT=""
WAIT_SECONDS="${CARR_CALENDAR_CAPTURE_WAIT_SECONDS:-60}"
case "$WAIT_SECONDS" in
  ''|*[!0-9]*) echo "calendar-capture: invalid wait bound" >&2; exit 64 ;;
esac
while [ "$#" -gt 0 ]; do
  case "$1" in
    --days) DAYS="${2:-7}"; shift ;;
    --dry-run) DRY=1 ;;
    --receipt-safe) RECEIPT_SAFE=1 ;;
    --canary) CANARY=1 ;;
    -h|--help) sed -n '1,40p' "$0"; exit 0 ;;
    *) echo "calendar-capture: unknown argument: $1" >&2; exit 64 ;;
  esac
  shift
done
if [ "$RECEIPT_SAFE" -eq 1 ] && [ "$DRY" -ne 1 ]; then
  echo "calendar-capture: --receipt-safe requires --dry-run" >&2
  exit 64
fi
if [ "$CANARY" -eq 1 ]; then CANARY_SNAPSHOT="$(cat)"; [ -n "$CANARY_SNAPSHOT" ] || { echo "calendar-capture: missing protected contact snapshot" >&2; exit 78; }; fi
if [ "$CANARY" -eq 1 ] && { [ "$DRY" -eq 1 ] || [ "$RECEIPT_SAFE" -eq 1 ] || [ "${CARR_CONTROL_PLANE_MODE:-}" != "canary" ]; }; then
  echo "calendar-capture: --canary requires explicit control-plane canary mode and no dry-run flags" >&2
  exit 64
fi

# Keep late asynchronous completion confined to this invocation forever.
mkdir -p "$OUTPUT_ROOT/calendar-runs"
RUN_ROOT="$(mktemp -d "$OUTPUT_ROOT/calendar-runs/run.XXXXXXXX")" || exit 1
chmod 700 "$RUN_ROOT"
ACCESS_LOG="$RUN_ROOT/calendar-access.log"

# ---------------------------------------------------------------- 1. the read
# Mark the log so we judge THIS run's lines and not a previous run's success —
# the failure mode a naive `tail` would hide.
MARK="$(date -u +%FT%TZ)-$$"
printf '=== capture-run %s ===\n' "$MARK" >> "$ACCESS_LOG"

if [ ! -d "$APP" ]; then
  echo "calendar-capture: FAIL the access bundle is missing at $APP" >&2
  echo "  Without it macOS cannot prompt for calendar permission at all." >&2
  exit 1
fi

# THE BUNDLE IS BUILT PER MACHINE, NOT SHIPPED WHOLE. Two things about it are
# machine-specific and neither used to be, which is why this job could not run on
# a second Mac at all:
#   - an ad-hoc SIGNATURE verifies only on the machine that made it; checked out
#     elsewhere it reads as "code or signature have been modified" and macOS
#     refuses the launch;
#   - macOS 26 refuses to launch a bundle whose main executable is a SCRIPT,
#     answering -10669 without running it (measured 2026-08-18, macOS 26.5.2).
# So build when the compiled stub is missing or the signature does not verify.
# On a machine where both already hold this is a no-op.
# Guarded on the builder AND its source both being present. The selftest points
# CARR_REPO at a temp root holding a stub bundle and no build tooling; without
# this guard the rebuild fires there, fails, and aborts the run before the
# behavior under test is ever reached.
if [ "$APP" = "$REPO/tools/CARR Calendar Access.app" ] && [ -x "$REPO/bin/build-calendar-access.sh" ] && [ -f "$REPO/tools/calendar-access-stub.c" ] \
   && { [ ! -x "$APP/Contents/MacOS/carr-calendar-access" ] || ! codesign -v "$APP" 2>/dev/null; }; then
  echo "calendar-capture: bundle needs building for this machine — running bin/build-calendar-access.sh"
  "$REPO/bin/build-calendar-access.sh" || {
    echo "calendar-capture: FAIL could not build the access bundle" >&2; exit 1; }
fi

open -n -a "$APP" --args dump "$RUN_ROOT" || {
  echo "calendar-capture: FAIL could not launch the access bundle" >&2
  echo "  If this is -10669 the bundle's executable is not a Mach-O binary;" >&2
  echo "  bin/build-calendar-access.sh rebuilds it." >&2
  exit 1; }

# `open` returns as soon as the app is launched, so wait for the bundle's own
# exit line to appear after our marker rather than assuming it finished.
i=0
while [ "$i" -lt "$WAIT_SECONDS" ]; do
  if sed -n "/=== capture-run $MARK ===/,\$p" "$ACCESS_LOG" 2>/dev/null | grep -q '^exit='; then
    break
  fi
  sleep 1
  i=$((i + 1))
done

RUN_LOG="$(sed -n "/=== capture-run $MARK ===/,\$p" "$ACCESS_LOG" 2>/dev/null)"
if [ -z "$RUN_LOG" ] || ! printf '%s' "$RUN_LOG" | grep -q '^exit='; then
  echo "calendar-capture: FAIL the read did not finish within ${WAIT_SECONDS}s" >&2
  exit 1
fi

if printf '%s' "$RUN_LOG" | grep -qi 'DENIED'; then
  echo "calendar-capture: FAIL calendar access DENIED — nothing was read." >&2
  echo "  This is a PERMISSION answer, not an empty calendar, and it must never" >&2
  echo "  be reported as zero touches. Grant Calendars to \"CARR Calendar Access\"" >&2
  echo "  in System Settings > Privacy & Security > Calendars, then re-run." >&2
  exit 3
fi

if ! printf '%s\n' "$RUN_LOG" | grep -qx 'exit=0'; then
  echo "calendar-capture: FAIL the reader exited unsuccessfully" >&2
  exit 1
fi

SCANNED="$(printf '%s' "$RUN_LOG" | sed -n 's/.*events scanned: \([0-9]*\).*/\1/p' | head -1)"
echo "calendar-capture: read OK — ${SCANNED:-?} events scanned"

# ---------------------------------------------------------------- 2. the match
# Keep matcher stderr in local scratch for failure-class detection. The first
# launchd fire discarded it and lost the diagnosis; printing it into the job log
# would expose attendee data. Only fixed aggregate messages leave this script.
MATCH_JSON="$RUN_ROOT/calendar-touch-proposals.json"
MATCH_ERR="$RUN_ROOT/calendar-matcher.err"
INTAKE_EVIDENCE="$OUTPUT_ROOT/calendar-intake-evidence.json"
# --from-dump, and this is the whole reason the job works unattended. The
# matcher's default path opens the local Calendar DATABASE, which needs FULL DISK
# ACCESS granted to the responsible process — held by a terminal, NOT by a launchd
# agent. The first real fire died there: "cannot read the calendar database. This
# is a Full Disk Access answer, not an empty calendar." The bundle above already
# read the same meetings through EventKit under a permission that DOES survive
# into the agent, so the match runs off its dump and the pipeline needs ONE grant
# instead of two. Verified identical output both ways: 1 exact, 0 domain, 2 unknown.
DUMP="$RUN_ROOT/calendar-attendees.json"
if [ ! -s "$DUMP" ]; then
  echo "calendar-capture: FAIL the bundle read OK but wrote no dump at $DUMP" >&2
  exit 1
fi
if [ "$CANARY" -eq 1 ]; then
  printf '%s' "$CANARY_SNAPSHOT" | "$PY" "$REPO/tools/calendar-touch-matcher.py" "$DAYS" --json --from-dump "$DUMP" --contact-snapshot-stdin > "$MATCH_JSON" 2> "$MATCH_ERR"
  MATCH_STATUS=$?
else
  "$PY" "$REPO/tools/calendar-touch-matcher.py" "$DAYS" --json --from-dump "$DUMP" > "$MATCH_JSON" 2> "$MATCH_ERR"
  MATCH_STATUS=$?
fi
if [ "$MATCH_STATUS" -ne 0 ]; then
  echo "calendar-capture: FAIL the matcher did not complete" >&2
  # The matcher reads the local Calendar database directly, which is a SEPARATE
  # macOS permission from the EventKit read above: Full Disk Access, granted to
  # the responsible process. A launchd agent's responsible process is not the
  # terminal that was granted it, so a read that works by hand can still fail
  # here — name that plainly rather than leaving a bare "did not complete".
  if grep -qiE "operation not permitted|unable to open|authoriz|permission" "$MATCH_ERR"; then
    echo "  This reads the local Calendar database, which needs FULL DISK ACCESS" >&2
    echo "  for the process launchd runs — a separate grant from the calendar" >&2
    echo "  permission the bundle already holds." >&2
    exit 4
  fi
  exit 1
fi

"$PY" - "$MATCH_JSON" "$OUTPUT_ROOT/calendar-touch-proposals.json" <<'PROJECTPY'
import os, shutil, sys, tempfile
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(sys.argv[2]))
with os.fdopen(fd, "wb") as out, open(sys.argv[1], "rb") as source:
    shutil.copyfileobj(source, out)
os.replace(tmp, sys.argv[2])
PROJECTPY

# The canary receipt target is deliberately before all normal-record intake.
# It proves deterministic EventKit output without creating activity, intake, or
# research writes against the live record layer.
if [ "$CANARY" -eq 1 ]; then
  exec "$PY" "$REPO/tools/calendar-canary-result.py" --proposals "$MATCH_JSON"
fi

# Triage still needs the complete local-store export outside the rolling touch
# window. Publish only after this invocation's reader and matcher succeeded;
# private completion and scratch remain the authority for capture writes.
if ! "$PY" - "$DUMP" "$OUTPUT_ROOT/calendar-attendees.json" <<'TRIAGEPY'
import os, shutil, sys, tempfile
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(sys.argv[2]))
with os.fdopen(fd, "wb") as out, open(sys.argv[1], "rb") as source:
    shutil.copyfileobj(source, out)
os.replace(tmp, sys.argv[2])
TRIAGEPY
then
  echo "calendar-capture: FAIL triage snapshot publication did not complete" >&2
  exit 1
fi

# Exact matches have their own evidence and deterministic idempotency keys.
# Process them even when a separate unknown attendee still requires intake.
"$PY" - "$MATCH_JSON" "$DRY" "$DAYS" "${SCANNED:-0}" "$RECEIPT_SAFE" <<'PYEOF'
import hashlib, json, os, subprocess, sys, tempfile
from collections import defaultdict
path, dry, days, scanned, receipt_safe = (sys.argv[1], sys.argv[2] == "1", sys.argv[3],
                                          sys.argv[4], sys.argv[5] == "1")
d = json.load(open(path))
c = d["counts"]
print(f"calendar-capture: window {days}d — {c['emails']} attendee address(es): "
      f"{c['exact']} exact, {c['domain']} domain-only, {c['unknown']} unknown")

if dry and not receipt_safe:
    for u in d["unknown"]:
        print(f"  research candidate  {u['email']}  (last seen {u['last_seen']})")
    for m in d["domain"]:
        print(f"  domain-only, NOT logged  {m['email']} -> {m['org'][:50]}")

touches = [(e, ev) for e in d["exact"] for ev in (e["events"] or
           [{"day": e["last_seen"], "title": "(untitled)"}])]
if not touches:
    print("calendar-capture: no exact matches in this window — nothing to log")
    print(f"calendar-capture: source=eventkit mode={'shadow' if dry else 'live'} "
          f"scanned={scanned} exact=0 domain={c['domain']} unknown={c['unknown']} "
          "writes=0 failed=0 would_write=0")
    sys.exit(0)

if dry:
    if not receipt_safe:
        for e, ev in touches:
            print(f"  would log touch  {e['ref']}  via {e['email']}  ({ev.get('day', e['last_seen'])})")
    print(f"calendar-capture: source=eventkit mode=shadow scanned={scanned} "
          f"exact={c['exact']} domain={c['domain']} unknown={c['unknown']} writes=0 failed=0 "
          f"would_write={len(touches)}")
    sys.exit(0)

def occurrence_key(e, ev):
    if not ev.get("event_id") or not ev.get("start_at"):
        raise ValueError("live capture needs timestamped occurrence identity")
    identity = json.dumps([e["ref"], e["email"], ev["event_id"]], separators=(",", ":"))
    return "calcap-occurrence-v2-" + hashlib.sha256(identity.encode()).hexdigest()

# Bind pre-upgrade attendee/day activities to occurrences before enabling writes.
# An ambiguous legacy day refuses; it cannot manufacture a second historical touch.
try:
    keys = sorted({k for e, ev in touches for k in (
        occurrence_key(e, ev), f"calcap-{e['email']}-{ev['day']}")})
    history = {}
    observed = {}
    for ref in sorted({e["ref"] for e, ev in touches}):
        r = subprocess.run(["./run.sh", "call", "catch-me-up", json.dumps({"ref": ref})],
                           capture_output=True, text=True, timeout=30)
        response = json.loads(r.stdout)
        if (r.returncode or not isinstance(response, dict)
                or not isinstance(response.get("calendar_history"), list)
                or any(set(row) != {"key", "activity_id", "summary"} or
                       not isinstance(row["activity_id"], str) or not isinstance(row["key"], str)
                       for row in response["calendar_history"])):
            raise ValueError("history unavailable")
        observed.update({row["key"]: row for row in response["calendar_history"]})
    history = {key: observed.get(key, {"key": key, "activity_id": None, "summary": None})
               for key in keys}
    bindings_path = os.path.join(os.environ.get("CARR_CALENDAR_OUTPUT_ROOT", "out"), "calendar-identity-bindings.json")
    bindings = json.load(open(bindings_path)) if os.path.exists(bindings_path) else {}
    if not isinstance(bindings, dict):
        raise ValueError("invalid identity bindings")
    cohorts = defaultdict(list)
    for e, ev in touches:
        cohorts[f"calcap-{e['email']}-{ev['day']}"].append((e, ev))
    for old_key, cohort in cohorts.items():
        legacy = history[old_key]
        if not legacy["activity_id"]:
            continue
        already_bound = [pair for pair in cohort if bindings.get(occurrence_key(*pair)) == legacy["activity_id"]]
        candidates = already_bound or [pair for pair in cohort
            if f"Meeting: {pair[1]['title']}"[:180] == legacy["summary"]]
        if len(cohort) == 1:
            candidates = cohort
        if len(candidates) != 1:
            raise ValueError("ambiguous legacy history")
        bindings[occurrence_key(*candidates[0])] = legacy["activity_id"]
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(bindings_path))
    with os.fdopen(fd, "w") as fh:
        json.dump(bindings, fh)
    os.replace(tmp, bindings_path)
except (ValueError, KeyError, TypeError, OSError, subprocess.TimeoutExpired):
    print("calendar-capture: FAIL legacy history or occurrence identity needs reconciliation", file=sys.stderr)
    sys.exit(1)

failed = 0
written = 0
replayed = 0
for e, ev in touches:
    # log-activity with kind "meeting", NOT stamp-touch. stamp-touch is shorthand
    # for a call or a text and its enum accepts only those two; a calendar meeting
    # is neither, and the first live launchd fire was refused for exactly that —
    # caught by the required-argument guard rather than written wrong.
    day = ev.get("day", e["last_seen"])
    key = occurrence_key(e, ev)
    # A bound occurrence can move to another day. Authenticate its original
    # activity against the complete canonical read, not just today's key cohort.
    verified_legacy_ids = {row["activity_id"] for old, row in observed.items()
                           if old.startswith("calcap-") and "@" in old and row["activity_id"]}
    if history[key]["activity_id"] or bindings.get(key) in verified_legacy_ids:
        replayed += 1
        continue
    args = json.dumps({
        "idempotency_key": key,
        "ref": e["ref"],
        "kind": "meeting",
        "occurred_at": ev["start_at"],
        "summary": f"Meeting: {ev.get('title', '(untitled)')}"[:180],
        "detail": (f"Calendar evidence — {e['email']} was an attendee of "
                   f"\"{ev.get('title','(untitled)')}\" on {day}. Matched to this "
                   f"record by an exact email address. Captured automatically from "
                   f"the local calendar; not self-reported."),
    })
    r = subprocess.run(["./run.sh", "call", "log-activity", args],
                       capture_output=True, text=True)
    # local-verb emits one JSON value on stdout; diagnostics stay on stderr.
    # Nested success, malformed output, and failed processes cannot acknowledge
    # this activity. Keep every raw response out of persisted capture logs.
    try:
        response = json.loads(r.stdout)
    except json.JSONDecodeError:
        response = None
    ok = (r.returncode == 0 and isinstance(response, dict)
          and response.get("ok") is True)
    print("  logged exact touch" if ok else "  FAILED to log exact touch")
    if not ok:
        failed += 1
    else:
        written += 1
print(f"calendar-capture: source=eventkit mode=live scanned={scanned} exact={c['exact']} "
      f"domain={c['domain']} unknown={c['unknown']} writes={written} failed={failed} replayed={replayed}")
sys.exit(1 if failed else 0)
PYEOF
CAPTURE_STATUS=$?

# Unmatched attendees remain in the durable queue for local-mail search,
# research and a canonical record. Pending intake cannot invalidate
# independently matched meetings. Invalid evidence remains a hard failure;
# the standalone intake gate still requires all three receipts by default.
# --dry-run neither consumes evidence nor writes canonical records.
INTAKE_STATUS=0
if [ "$DRY" -ne 1 ]; then
  "$PY" "$REPO/tools/calendar-intake-gate.py" \
          --proposals "$MATCH_JSON" --evidence "$INTAKE_EVIDENCE" \
          --aggregate-only --defer-unmatched --dispatch || INTAKE_STATUS=$?
fi
if [ "$CAPTURE_STATUS" -ne 0 ]; then
  echo "calendar-capture: FAIL one or more exact touches were not logged" >&2
  exit "$CAPTURE_STATUS"
fi
if [ "$INTAKE_STATUS" -ne 0 ]; then
  echo "calendar-capture: REFUSE invalid unmatched attendee intake evidence" >&2
  exit 65
fi
