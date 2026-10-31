#!/bin/sh
# purge-recordings.sh — the retention law for ~/Recordings.
#
# THE RULING CHAIN, in order, because each amended the last:
#   db7b0231 (Joe, 2026-08-08): recordings are not kept forever — "we want the
#     context of the recording only. thatas way too much storage."
#   d50e30bc (same day): the gate is INGESTION, not elapsed time. A first cut
#     purged on a timer, which would have destroyed the audio of any meeting
#     nobody distilled — the one case where the raw capture is the only copy.
#   2d1b965f (same day, settles both): TRANSCRIPTS PURGE TOO, with a condition
#     in his words — "purge them both but you've got to summarize everything
#     very well. take all the context needed. who said what etc."
#
# So: audio AND transcripts go, once the distilled context that REPLACES them
# is safely in the record layer. Joe was shown the measured split first (1.6MB
# audio vs 60KB transcripts across four sessions — keeping transcripts was
# nearly free) and chose to purge them anyway. The reason is not storage: a
# transcript is a verbatim record of what a client said, and the whole system
# is built so no table, upload or index holds one.
#
# THE GATE — all three must hold, or the session keeps everything, forever:
#   1. ingested.json exists in the session directory, AND
#   2. a post-call receipt has report hashes and zero pending items, or
#      it names a meeting_record — the attributed narrative that satisfies
#      Joe's "who said what" condition. A marker without one means the
#      summary half was never done, so the transcript is still the only
#      account of the meeting and must not be deleted. This is checked here
#      rather than trusted to the writer: the condition is a precondition of
#      deletion, so it is enforced where the deletion happens.
#   3. GRACE_HOURS elapsed since ingested_at (not since the meeting) — 72h,
#      because weekends are not workdays (rule 236ca227), so a Friday
#      ingestion is still re-runnable Monday morning.
#
# MARKER CONTRACT, written by the capture bridge:
#   ~/Recordings/<session>/ingested.json
#   {"ingested_at": "<ISO-8601 UTC>",
#    "by": "<actor>",
#    "meeting_record": "<record-layer ref for the attributed narrative>",
#    "records": ["<ref>", ...]}
# Note the confirm-queue candidates are NOT the summary: those carry evidence
# quotes capped at ~15 words precisely so the queue cannot become transcript
# storage by the side door. meeting_record points at the separate narrative.
#
# ALWAYS SURVIVES: announcement.json (consent proof must outlive everything it
# documents), meta.json, ingested.json.
#
# The capture bridge writes filing receipts only after review is complete.
# Existing receipts remain authoritative, including sessions filed before the
# question gate. Unfiled sessions have no receipt and keep their recordings.
set -eu

RECORDINGS="$HOME/Recordings"
GRACE_HOURS=72
LOG="$HOME/Library/Logs/recordings-purge.log"

log_line() {
    printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$1" >> "$LOG"
}

[ -d "$RECORDINGS" ] || exit 0

# Validates a marker against the gate's first two conditions and the grace
# clock. Prints PURGE, or HOLD with the reason. Uses only Python's standard
# library. Post-call receipts require report hashes and zero pending items.
marker_verdict() {
    python3 - "$1" "$GRACE_HOURS" <<'PY' 2>/dev/null || echo "HOLD unreadable-marker"
import datetime, json, sys
path, grace_hours = sys.argv[1], float(sys.argv[2])
try:
    marker = json.load(open(path))
except Exception:
    print("HOLD unreadable-marker"); raise SystemExit(0)
from pathlib import Path
if Path(path).parent.name.startswith("phone-") and marker.get("post_call") is not True:
    print("HOLD no-phone-filing-receipt"); raise SystemExit(0)
if marker.get("post_call") is True:
    import re
    if re.fullmatch(r"[0-9a-fA-F]{64}", str(marker.get("aggregate_report_hash") or "")) is None:
        print("HOLD no-filed-report-hash"); raise SystemExit(0)
    if re.fullmatch(r"[0-9a-fA-F]{64}", str(marker.get("backend_report_sha256") or "")) is None:
        print("HOLD no-backend-report-hash"); raise SystemExit(0)
    if marker.get("pending_items") != 0:
        print("HOLD pending-post-call-items"); raise SystemExit(0)
elif not str(marker.get("meeting_record") or "").strip():
    print("HOLD no-meeting-record"); raise SystemExit(0)
stamp = str(marker.get("ingested_at") or "").strip()
if not stamp:
    print("HOLD no-ingested-at"); raise SystemExit(0)
try:
    when = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
except ValueError:
    print("HOLD bad-ingested-at"); raise SystemExit(0)
if when.tzinfo is None:
    when = when.replace(tzinfo=datetime.timezone.utc)
elapsed = (datetime.datetime.now(datetime.timezone.utc) - when).total_seconds() / 3600.0
print("PURGE" if elapsed >= grace_hours else "HOLD in-grace")
PY
}

purged=0
held=0
for dir in "$RECORDINGS"/*/; do
    [ -d "$dir" ] || continue
    case "$(basename "$dir")" in
        [0-9][0-9][0-9][0-9].[0-9][0-9].[0-9][0-9]-[0-9][0-9][0-9][0-9]) ;;
        phone-????????-????-????-????-????????????) ;;
        *) continue ;;
    esac

    # Nothing left to reclaim in this session?
    have_something=0
    for f in "$dir/mic.caf" "$dir/system.caf" "$dir/transcript.json" "$dir/transcript.md" \
             "$dir/imported.audio" "$dir/imported.wav" "$dir/imported.m4a" \
             "$dir/imported.mp3" "$dir/imported.mp4" "$dir/imported.webm" "$dir/post-call-held-draft.json"; do
        [ -f "$f" ] && have_something=1
    done
    [ "$have_something" = "1" ] || continue

    marker="$dir/ingested.json"
    if [ ! -f "$marker" ]; then
        held=$((held + 1))
        continue
    fi

    verdict=$(marker_verdict "$marker")
    case "$verdict" in
        PURGE) ;;
        *)
            held=$((held + 1))
            log_line "HELD $(basename "$dir") — ${verdict#HOLD }"
            continue
            ;;
    esac

    for f in "$dir/mic.caf" "$dir/system.caf" "$dir/transcript.json" "$dir/transcript.md" \
             "$dir/imported.audio" "$dir/imported.wav" "$dir/imported.m4a" \
             "$dir/imported.mp3" "$dir/imported.mp4" "$dir/imported.webm" "$dir/post-call-held-draft.json"; do
        if [ -f "$f" ]; then
            size=$(du -h "$f" | cut -f1)
            rm "$f"
            purged=$((purged + 1))
            log_line "PURGED $f ($size) — ingested with meeting record, grace elapsed"
        fi
    done
done

if [ "$purged" -gt 0 ] || [ "$held" -gt 0 ]; then
    log_line "RUN complete: $purged file(s) purged, $held session(s) held (not ingested, no meeting record, or inside grace)"
fi
exit 0
