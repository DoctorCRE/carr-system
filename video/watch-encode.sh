#!/bin/zsh
# watch-encode.sh — CARR video pipeline watch-folder processor
# Hand-run on the MacBook (no launchd job; retired 2026-10-18) to process files in
# 01_Drop_Horizontal or 02_Drop_Vertical. Encodes platform-ready H.264,
# writes to 03_Output, moves the source to 04_Archive.
# Log: Scripts/watch-encode-log.txt

PIPE="$HOME/Movies/CARR Video Pipeline"
FFMPEG="/opt/homebrew/bin/ffmpeg"
LOG="$PIPE/Scripts/watch-encode-log.txt"
LOCK="$PIPE/Scripts/.watch-encode.lock"

log() { echo "$(date '+%m-%d %H:%M:%S')  $1" >> "$LOG"; }

# single-instance lock (mkdir is atomic); stale after 2h
if ! mkdir "$LOCK" 2>/dev/null; then
    if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +120 2>/dev/null)" ]; then
        rmdir "$LOCK" 2>/dev/null; mkdir "$LOCK" 2>/dev/null || exit 0
    else
        exit 0
    fi
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

stable() { # wait until file size stops changing (still copying = skip this run)
    local f="$1" s1 s2
    s1=$(stat -f%z "$f" 2>/dev/null) || return 1
    sleep 3
    s2=$(stat -f%z "$f" 2>/dev/null) || return 1
    [ "$s1" = "$s2" ]
}

encode() { # encode <src> <w> <h> <suffix>
    local src="$1" w="$2" h="$3" suffix="$4"
    local base="${src:t:r}"
    local out="$PIPE/03_Output/${base}_${suffix}.mp4"
    log "encoding: ${src:t} -> ${out:t}"
    "$FFMPEG" -y -nostdin -i "$src" \
        -vf "scale=${w}:${h}:force_original_aspect_ratio=decrease,pad=${w}:${h}:(ow-iw)/2:(oh-ih)/2,format=yuv420p" \
        -c:v libx264 -preset medium -crf 18 -profile:v high -level 4.2 \
        -movflags +faststart \
        -c:a aac -b:a 256k -ar 48000 \
        "$out" >> "$LOG" 2>&1
    if [ $? -eq 0 ] && [ -s "$out" ]; then
        mv "$src" "$PIPE/04_Archive/${src:t}"
        log "OK: ${out:t}  (source archived)"
    else
        log "FAILED: ${src:t} — left in drop folder"
    fi
}

setopt null_glob
for src in "$PIPE/01_Drop_Horizontal"/*.(mp4|mov|m4v|MP4|MOV)(N); do
    stable "$src" || { log "skip (still copying): ${src:t}"; continue; }
    encode "$src" 1920 1080 "YouTube1080"
done
for src in "$PIPE/02_Drop_Vertical"/*.(mp4|mov|m4v|MP4|MOV)(N); do
    stable "$src" || { log "skip (still copying): ${src:t}"; continue; }
    encode "$src" 1080 1920 "Vertical1080x1920"
done
