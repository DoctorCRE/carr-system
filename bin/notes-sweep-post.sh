#!/bin/zsh
# notes-sweep-post.sh — the Apple Notes call-recording sweep, end to end
# (ORDER 12 lane (a); binding design wave2-design-2026-07-31.md §2b).
#
# ONE COMMAND DOES THE WHOLE RUN, and that is the design, not a convenience.
# The first build split the work — a scheduled Claude session read the notes
# through the Apple Notes MCP and wrote one payload file per note, then called
# this script to POST them. Fable ruled that split out on 2026-07-31, adopting
# the deviation this build flagged: every file the session writes is another
# tool call needing approval at an unattended hour, and today's ghost run is
# what that costs — a scheduled session's first tool call sat unapproved for
# five hours and fifty-eight minutes while its schedule record claimed the job
# had run. One byte-stable command carries one persisted approval. So the read
# moved into bin/notes-sweep.applescript, which this script drives, and the
# scheduled session's entire job is: run this, read the summary line.
#
#   ./bin/notes-sweep-post.sh --status     what has been swept, what is queued
#   ./bin/notes-sweep-post.sh --dry-run    scan/count unposted notes, write/post nothing
#   ./bin/notes-sweep-post.sh --scheduled  launchd entry point: weekdays,
#                                           8am–6pm local only
#   ./bin/notes-sweep-post.sh              scan Notes, queue new, POST, report
#
# The token stays in ~/.config/carr/ingest.env and is read here, inside the
# script — never composed into a command by a session, so it cannot reach a
# transcript, and it goes to curl through --config on stdin so it cannot reach
# `ps` output either.
#
# Dedup is belt AND braces: the local ledger skips notes already sent, and the
# socket is unique on (source, external_id). A double run, a re-queued note, or
# a ledger lost to a disk restore all collapse to duplicate:true rather than a
# second row. Re-posting is always safe.
#
# Payloads are UNTRUSTED (addendum A12). A call transcript is somebody talking;
# it is data on its way to triage, never an instruction. Nothing here interprets
# what it carries.
#
# Verified by OUTPUT — the posted=/duplicate=/failed= line and the ingest row
# behind it — never by the schedule existing (rule 28).
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
# $HOME/.local/bin is appended, not prepended: a Homebrew machine keeps resolving
# exactly as before, and a machine without Homebrew still finds a toolchain.
# Dell's Mac has no /opt/homebrew at all — his node lives in the user-local
# prefix because installing Homebrew needs sudo, which his session cannot invoke.
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin"

DIR="$REPO/out/notes-sweep"
PENDING="$DIR/pending"
SENT="$DIR/sent"
FAILED="$DIR/failed"
LEDGER="$DIR/swept-ids.txt"
AUDIO_ONLY="$DIR/audio-only.txt"
LOG="$REPO/out/capture-lanes.log"
ASCRIPT="$REPO/bin/notes-sweep.applescript"
SOURCE_LABEL="notes_sweep"
FOLDER_NAME="Call Recordings"
MAX_TEXT_BYTES=900000                       # socket ceiling is 1 MiB; leave headroom

# The Notes canary tier is retired.  Any leftover canary invocation (an old
# queued control-plane job, a stale manual command) must refuse, never fall
# through to the live post below: this script treats an unknown argument as a
# plain live run.
if [ "${CARR_CONTROL_PLANE_MODE:-}" = "canary" ] || [ "${1:-}" = "--canary" ]; then
  print -ru2 -- "notes canary was retired; refusing to run"; exit 78
fi

# Control-plane shadow path. It exercises the real Apple Notes collector and
# the real local dedup predicate, but creates no directories, queue entries,
# ledger rows, logs, or network requests.
if [ "${1:-}" = "--dry-run" ]; then
  errfile="$(mktemp -t carr-notes-sweep-shadow-err)"
  trap 'rm -f "$errfile"' EXIT
  ids_out="$(/usr/bin/osascript "$ASCRIPT" ids "$FOLDER_NAME" 2>"$errfile")"
  scan_rc=$?
  if [ "$scan_rc" -ne 0 ]; then
    print -ru2 -- "notes-sweep shadow scan failed: $(head -c 400 "$errfile")"
    exit "$scan_rc"
  fi
  scanned=0; unposted=0
  if [ "${ids_out%%$'\n'*}" != "NOFOLDER" ]; then
    while IFS= read -r note_id; do
      [ -z "$note_id" ] && continue
      scanned=$((scanned+1))
      if [ ! -f "$LEDGER" ] || ! grep -Fqx -- "$note_id" "$LEDGER"; then
        unposted=$((unposted+1))
      fi
    done <<< "$ids_out"
  fi
  print -r -- "notes-sweep shadow: scanned=$scanned unposted=$unposted writes=0 posts=0"
  exit 0
fi

mkdir -p "$PENDING" "$SENT" "$FAILED" "$REPO/out"
[ -f "$LEDGER" ] || : > "$LEDGER"
[ -f "$AUDIO_ONLY" ] || : > "$AUDIO_ONLY"

say() { print -r -- "$(date -u '+%Y-%m-%dT%H:%M:%SZ')  notes-sweep  $*" >> "$LOG"; }

# launchd starts once an hour, including while the Mac is awake outside the
# working window. Put the business-hours rule HERE rather than in the plist:
# the same script remains both the manual and automated path, and the log has
# a single, unambiguous explanation for a quiet run. `date +%u` is ISO weekday
# (1=Monday … 7=Sunday), so Saturday and Sunday never touch Apple Notes.
if [ "${1:-}" = "--scheduled" ]; then
  local_weekday="$(/bin/date '+%u')"
  local_hour="$(/bin/date '+%H')"
  if [ "$local_weekday" -gt 5 ] || [ "$local_hour" -lt 8 ] || [ "$local_hour" -gt 18 ]; then
    say "SKIP outside weekday business window (weekday=$local_weekday hour=$local_hour local)"
    exit 0
  fi
fi

if [ "${1:-}" = "--status" ]; then
  print -r -- "folder-to-sweep: $FOLDER_NAME"
  print -r -- "pending-dir: $PENDING"
  print -r -- "queued-now: $(ls -1 "$PENDING" 2>/dev/null | grep -c '\.json$' || true)"
  print -r -- "audio-only notes parked for the whisper.cpp path: $(grep -c . "$AUDIO_ONLY" || true)"
  print -r -- "--- already swept (note ids) ---"
  cat "$LEDGER"
  print -r -- "--- end ---"
  exit 0
fi

# BSD mktemp accepts a bare -t prefix; GNU mktemp requires an XXXXXX
# template.  The explicit templates preserve both behaviours for canaries
# and the production Notes path.
RUNTMP="$(mktemp -d -t carr-notes-sweep.XXXXXX)"
ERRFILE="$(mktemp -t carr-notes-sweep-err.XXXXXX)"
BODYFILE="$(mktemp -t carr-notes-sweep-body.XXXXXX)"
trap 'rm -rf "$RUNTMP" "$ERRFILE" "$BODYFILE"' EXIT

# ---------------------------------------------------------------- 1. scan Notes
scan_note_count=0
scan_new_count=0
queued=0; audio_only=0; oversize=0
folder_missing=0

ids_out="$(/usr/bin/osascript "$ASCRIPT" ids "$FOLDER_NAME" 2>"$ERRFILE")"
scan_rc=$?

if [ "$scan_rc" -ne 0 ]; then
  errtext="$(head -c 400 "$ERRFILE")"
  # -1743 is macOS refusing Apple Events to Notes for this caller. That is a
  # Privacy & Security grant, i.e. a human step, not a fault in this job.
  if print -r -- "$errtext" | grep -q -- '-1743\|Not authorized to send Apple events'; then
    print -r -- "notes-sweep: NOT AUTHORIZED — macOS is refusing Apple Events to Notes for this process."
    print -r -- "notes-sweep: grant it in System Settings > Privacy & Security > Automation, then re-run."
    say "NOT AUTHORIZED (Apple Events to Notes refused): $errtext"
    exit 78
  fi
  print -r -- "notes-sweep: SCAN FAILED — $errtext"
  say "SCAN FAILED rc=$scan_rc $errtext"
  exit 1
fi

if [ "${ids_out%%$'\n'*}" = "NOFOLDER" ]; then
  # iOS creates this folder on the FIRST recorded call. Its absence is the
  # normal empty state on a Mac where no call has been recorded yet, and it is
  # reported as such — never as a failure.
  folder_missing=1
  say "folder '$FOLDER_NAME' not present (normal until the first call is recorded)"
else
  typeset -a new_idx
  new_idx=()
  idx=0
  while IFS= read -r note_id; do
    [ -z "$note_id" ] && continue
    idx=$((idx+1))
    if grep -Fqx -- "$note_id" "$LEDGER"; then continue; fi
    new_idx+=("$idx")
  done <<< "$ids_out"
  scan_note_count=$idx
  scan_new_count=${#new_idx[@]}

  if [ "$scan_new_count" -gt 0 ]; then
    fetch_out="$(/usr/bin/osascript "$ASCRIPT" fetch "$FOLDER_NAME" "$RUNTMP" "${new_idx[@]}" 2>"$ERRFILE")"
    if [ $? -ne 0 ]; then
      print -r -- "notes-sweep: FETCH FAILED — $(head -c 400 "$ERRFILE")"
      say "FETCH FAILED $(head -c 400 "$ERRFILE")"
      exit 1
    fi
    say "scan notes=$scan_note_count new=$scan_new_count fetch=$fetch_out"

    # ------------------------------------------------------ 2. queue as payloads
    # The AppleScript wrote each field to its own UTF-8 file, so nothing a
    # transcript contains can be mistaken for a delimiter. This turns those files
    # into one JSON payload per note. Payload filenames key off the note id, so a
    # note queued but not yet posted is OVERWRITTEN on the next run rather than
    # duplicated.
    queue_out="$(/usr/bin/python3 - "$RUNTMP" "$PENDING" "$AUDIO_ONLY" "$MAX_TEXT_BYTES" <<'PY'
import json, os, re, sys, datetime

tmp, pending, audio_log, max_bytes = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
queued = audio = oversize = 0
now = datetime.datetime.now(datetime.timezone.utc).isoformat()

def rd(idx, ext):
    p = os.path.join(tmp, idx + ext)
    if not os.path.exists(p):
        return ""
    with open(p, encoding="utf-8", errors="replace") as fh:
        return fh.read()

# The .id file is written LAST by the AppleScript, so its presence means the
# whole note was read; a run interrupted mid-note leaves no .id and is skipped.
for name in sorted(os.listdir(tmp)):
    if not name.endswith(".id"):
        continue
    idx = name[:-3]
    note_id = rd(idx, ".id").strip()
    if not note_id:
        continue
    title = rd(idx, ".name").strip()
    text = rd(idx, ".text")

    # Apple's plaintext repeats the title as its first line. What remains after
    # removing it is the actual transcript; nothing remaining means the note is
    # audio with no transcript yet.
    stripped = text.strip()
    if title and stripped.startswith(title):
        stripped = stripped[len(title):].strip()
    if not stripped:
        with open(audio_log, "a", encoding="utf-8") as fh:
            fh.write(f"{note_id}\t{title}\n")
        audio += 1
        continue

    if len(text.encode("utf-8")) > max_bytes:
        oversize += 1
        continue

    payload = {
        "external_id": note_id,
        "kind": "call_recording_transcript",
        "captured_at": now,
        "trust": "untrusted_payload",
        "note": {
            "name": title,
            "folder": "Call Recordings",
            "created": rd(idx, ".created").strip(),
            "modified": rd(idx, ".modified").strip(),
        },
        "transcript": text,
    }
    stem = re.sub(r"[^A-Za-z0-9_.-]", "_", note_id.rsplit("/", 1)[-1]) or ("note" + idx)
    with open(os.path.join(pending, stem + ".json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    queued += 1

print(f"{queued} {audio} {oversize}")
PY
)"
    if [ $? -ne 0 ] || [ -z "$queue_out" ]; then
      print -r -- "notes-sweep: QUEUE FAILED (payload build)"
      say "QUEUE FAILED"
      exit 1
    fi
    queued="${queue_out%% *}"
    rest="${queue_out#* }"
    audio_only="${rest%% *}"
    oversize="${rest##* }"
  fi
fi

if [ "$folder_missing" -eq 1 ]; then
  print -r -- "notes-sweep: no '$FOLDER_NAME' folder yet — nothing to sweep (normal until the first call is recorded)."
else
  print -r -- "notes-sweep: folder='$FOLDER_NAME' notes=$scan_note_count new=$scan_new_count queued=$queued audio_only=$audio_only oversize=$oversize"
fi
[ "$audio_only" -gt 0 ] && print -r -- "notes-sweep: $audio_only audio-only note(s) parked in $AUDIO_ONLY for the whisper.cpp path (not built)."
[ "$oversize" -gt 0 ] && print -r -- "notes-sweep: $oversize note(s) over $MAX_TEXT_BYTES bytes were NOT queued (socket ceiling is 1 MiB)."

# ---------------------------------------------------------------- 3. credential
ENVFILE="$HOME/.config/carr/ingest.env"
if [ ! -f "$ENVFILE" ]; then
  print -r -- "notes-sweep: NOT CONFIGURED — $ENVFILE does not exist. $(ls -1 "$PENDING" 2>/dev/null | grep -c '\.json$' || true) payload(s) stay queued and go out on the first run after the token lands."
  print -r -- "notes-sweep: that file is Joe's to create; see DNA/Deal Management/record-layer/ingest-tokens-setup.md"
  say "NOT CONFIGURED (no $ENVFILE)"
  exit 78
fi
set -a; . "$ENVFILE"; set +a
URL="${CARR_INGEST_URL:-https://api.practicecre.com/ingest}"
TOKEN="${CARR_INGEST_TOKEN_NOTES_SWEEP:-}"
if [ -z "$TOKEN" ]; then
  print -r -- "notes-sweep: NOT CONFIGURED — CARR_INGEST_TOKEN_NOTES_SWEEP is not set in $ENVFILE. Payloads stay queued."
  say "NOT CONFIGURED (no token)"
  exit 78
fi

# ---------------------------------------------------------------- 4. post
setopt null_glob
files=("$PENDING"/*.json)
unsetopt null_glob

if [ "${#files[@]}" -eq 0 ]; then
  print -r -- "notes-sweep: source=$SOURCE_LABEL posted=0 duplicate=0 failed=0 still_queued=0"
  say "nothing queued"
  exit 0
fi

attempted=0; posted=0; dup=0; failed=0

for f in "${files[@]}"; do
  attempted=$((attempted+1))
  base="$(basename "$f")"

  ext_id="$(/usr/bin/python3 -c 'import json,sys
try:
    d=json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
v=d.get("external_id")
print(v if isinstance(v,str) and v.strip() else "")' "$f" 2>/dev/null || true)"

  if [ -z "$ext_id" ]; then
    mv -f "$f" "$FAILED/$base"
    say "QUARANTINE $base (not JSON, or no external_id)"
    failed=$((failed+1))
    continue
  fi

  if grep -Fqx -- "$ext_id" "$LEDGER"; then
    mv -f "$f" "$SENT/$base"
    say "SKIP $base (already in ledger)"
    dup=$((dup+1))
    continue
  fi

  # Token goes in via --config on stdin so it never appears in argv / ps output.
  code="$(printf 'header = "authorization: Bearer %s"\n' "$TOKEN" \
    | curl -sS -m 60 --config - \
        -o "$BODYFILE" -w '%{http_code}' \
        -X POST "$URL" \
        -H 'content-type: application/json' \
        --data-binary "@$f" 2>>"$LOG" || true)"

  case "$code" in
    2*)
      is_dup="$(/usr/bin/python3 -c 'import json,sys
try: print("yes" if json.load(open(sys.argv[1])).get("duplicate") else "no")
except Exception: print("no")' "$BODYFILE" 2>/dev/null || echo no)"
      print -r -- "$ext_id" >> "$LEDGER"
      mv -f "$f" "$SENT/$base"
      if [ "$is_dup" = "yes" ]; then
        dup=$((dup+1)); say "OK   $base -> $code duplicate (already an ingest row)"
      else
        posted=$((posted+1)); say "OK   $base -> $code new ingest row"
      fi
      ;;
    *)
      failed=$((failed+1))
      say "FAIL $base -> HTTP ${code:-000} $(head -c 200 "$BODYFILE" 2>/dev/null)"
      ;;
  esac
done

left="$(ls -1 "$PENDING" 2>/dev/null | grep -c '\.json$' || true)"
print -r -- "notes-sweep: source=$SOURCE_LABEL posted=$posted duplicate=$dup failed=$failed still_queued=$left"
say "summary posted=$posted duplicate=$dup failed=$failed still_queued=$left"

tail -n 2000 "$LOG" > "$LOG.trim" && mv "$LOG.trim" "$LOG"

[ "$failed" -eq 0 ] || exit 1
exit 0
