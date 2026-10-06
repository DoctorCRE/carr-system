#!/bin/sh
# build-calendar-access.sh — compile and sign "CARR Calendar Access.app".
#
# WHY A BUILD STEP EXISTS AT ALL. This bundle used to ship whole in git: a zsh
# script as its main executable, plus a committed _CodeSignature. Both halves
# broke on a second Mac, and neither failure said so plainly.
#
#   1. SIGNATURE. An ad-hoc signature is made against exact bytes on the machine
#      that signed it. Checked out elsewhere it verifies as "code or signature
#      have been modified", and macOS refuses the launch.
#   2. EXECUTABLE FORMAT. macOS 26 will not launch an app bundle whose main
#      executable is a script; Launch Services answers -10669 and the app never
#      runs. Measured 2026-08-18 on macOS 26.5.2 against two throwaway bundles
#      differing only in that: script -> -10669, Mach-O -> launches.
#
# So the per-machine artifacts are BUILT per machine and the repo tracks only
# sources: tools/calendar-access-stub.c and the bundle's Info.plist and
# Contents/Resources/run.zsh. The compiled binary and the signature are ignored.
#
# Idempotent and cheap — bin/calendar-eventkit-capture.sh calls it automatically
# when the bundle is missing or its signature does not verify, so no one has to
# remember this file exists.
#
# A VALID BUILD IS KEPT, NOT REBUILT (2026-09-27). macOS keys the Calendar grant
# on the bundle id AND its ad-hoc designated requirement, which is the bundle's
# cdhash. clang does not emit byte-identical output twice (the Mach-O LC_UUID
# differs run to run), so every recompile produced a new cdhash and silently
# voided the grant Joe had already given. Worse, the repo bundle and the
# installed copy in ~/Applications share one bundle id, so a rebuilt copy and
# the granted copy would each need the grant and each Allow click revokes the
# other. So when the binary exists, the signature verifies, and the stub source
# has not changed since the recorded build, this keeps the bundle as it is.
# --force rebuilds anyway; a rebuild means Joe grants Calendars again.
#
#   bin/build-calendar-access.sh [--force]
set -u

FORCE=0
case "${1:-}" in
  --force) FORCE=1 ;;
  "") ;;
  *) echo "usage: build-calendar-access.sh [--force]" >&2; exit 64 ;;
esac

REPO="${CARR_REPO:-$HOME/carr-system}"
APP="$REPO/tools/CARR Calendar Access.app"
SRC="$REPO/tools/calendar-access-stub.c"
BIN="$APP/Contents/MacOS/carr-calendar-access"
# Outside the bundle on purpose: a stray file inside Contents/ is sealed into
# (or refused by) the signature. Ignored by git like the binary itself.
STAMP="$REPO/tools/.calendar-access-stub.sha256"

[ -d "$APP" ] || { echo "build-calendar-access: FAIL no bundle at $APP" >&2; exit 1; }
[ -f "$SRC" ] || { echo "build-calendar-access: FAIL no source at $SRC" >&2; exit 1; }
[ -f "$APP/Contents/Resources/run.zsh" ] || {
  echo "build-calendar-access: FAIL bundle is missing Contents/Resources/run.zsh" >&2; exit 1; }

SRC_SHA="$(shasum -a 256 "$SRC" | cut -d' ' -f1)"
if [ "$FORCE" -eq 0 ] && [ -x "$BIN" ] && codesign -v "$APP" 2>/dev/null; then
  # A build from before the stamp existed has no record of its source; the
  # stub has not changed since 2026-08-18, so keep it and record it now.
  if [ ! -f "$STAMP" ] || [ "$(cat "$STAMP")" = "$SRC_SHA" ]; then
    [ -f "$STAMP" ] || printf '%s\n' "$SRC_SHA" > "$STAMP"
    echo "build-calendar-access: OK — existing signed bundle kept (its Calendar grant stays valid)"
    exit 0
  fi
  echo "build-calendar-access: stub source changed since the last build — rebuilding" >&2
fi

command -v clang >/dev/null 2>&1 || {
  echo "build-calendar-access: FAIL no clang. Install the Xcode command line tools:" >&2
  echo "  xcode-select --install" >&2
  exit 1
}

mkdir -p "$APP/Contents/MacOS"
clang -O2 -Wall -o "$BIN" "$SRC" || {
  echo "build-calendar-access: FAIL compile failed" >&2; exit 1; }
chmod +x "$BIN"

# Ad-hoc is the right identity here: this never leaves the Mac that built it,
# and re-signing is what makes the bundle valid for THIS machine's TCC record.
codesign --force --sign - "$APP" >/dev/null 2>&1 || {
  echo "build-calendar-access: FAIL could not sign the bundle" >&2; exit 1; }
codesign -v "$APP" 2>/dev/null || {
  echo "build-calendar-access: FAIL the signature does not verify after signing" >&2; exit 1; }
printf '%s\n' "$SRC_SHA" > "$STAMP"

echo "build-calendar-access: OK — Mach-O stub built and bundle signed ad-hoc"
# Every compile mints a new cdhash, whatever triggered it (--force, an invalid
# seal, a changed stub): say so on every path that reaches here.
echo "build-calendar-access: NEW signature — Joe must grant Calendars to CARR Calendar Access again" >&2
echo "  (System Settings > Privacy & Security > Calendars)" >&2
