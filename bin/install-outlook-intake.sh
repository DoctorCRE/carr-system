#!/bin/zsh
# install-outlook-intake.sh — build the fixed-scope copier and pinned hxprobe, then render the job.
#
# A narrow installer like install-outlook-triage.sh: ops/config-as-code.py install
# would also rewrite every unrelated hook and LaunchAgent. The decoder is
# ukd1/hxstore-reverse-engineering at a pinned commit plus
# vendor-patches/hxprobe-calendar-and-bodies.patch, built offline from its
# locked dependencies (see vendor-patches/README.md). It never loads the job:
# it renders the plist, creates the launchd log at 0600, and prints the two
# commands the operator runs to load it.
#
# Usage: ./bin/install-outlook-intake.sh

set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
PY="$REPO/.venv/bin/python"
"$PY" -m lib.outlook_settings service intake
"$PY" -m lib.outlook_settings service refresh
LABEL="com.carr.outlook-intake"
SOURCE="$REPO/ops/launchd/$LABEL.plist"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
RENDERED="$REPO/out/$LABEL.plist"
LOG="$REPO/out/outlook-intake-launchd.log"
COMMIT="191b54e42757687806202f8f696e9066f0514e11"
VENDOR="$("$PY" -m lib.outlook_settings field vendor)"
HELPER="$("$PY" -m lib.outlook_settings field copy_helper)"
VENDOR_PARENT="$(dirname "$VENDOR")"
PATCH="$REPO/vendor-patches/hxprobe-calendar-and-bodies.patch"
PIN="$VENDOR/.carr-pin"
WANT_PIN="$COMMIT $(/usr/bin/shasum -a 256 "$PATCH" | cut -d' ' -f1)"
TMP="$(mktemp -t carr-outlook-intake-plist)"
trap 'rm -f "$TMP"' EXIT

if [ ! -f "$SOURCE" ]; then
  print -u2 -- "install-outlook-intake: missing source plist: $SOURCE"
  exit 1
fi

if [ ! -x "$VENDOR/hxprobe/target/release/hxprobe" ] || [ "$(cat "$PIN" 2>/dev/null)" != "$WANT_PIN" ]; then
  if [ -e "$VENDOR" ]; then
    print -u2 -- "install-outlook-intake: $VENDOR exists but is not the pinned, patched build; move it aside first"
    exit 1
  fi
  mkdir -p "$VENDOR_PARENT"
  /usr/bin/curl -fsSL "https://codeload.github.com/ukd1/hxstore-reverse-engineering/tar.gz/$COMMIT" \
    | /usr/bin/tar -xz -C "$VENDOR_PARENT"
  mv "$VENDOR_PARENT/hxstore-reverse-engineering-$COMMIT" "$VENDOR"
  (cd "$VENDOR" && /usr/bin/patch -p1 --quiet < "$PATCH")
  (cd "$VENDOR/hxprobe" && cargo test --release --locked --quiet && cargo build --release --locked --quiet)
  print -- "$WANT_PIN" > "$PIN"
fi

"$REPO/.venv/bin/python" - "$REPO" "$HOME" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

repo, home = (Path(value).resolve() for value in sys.argv[1:])
sys.path.insert(0, str(repo))
from lib.outlook_settings import load_settings

settings = load_settings(home)
settings.require_repo(repo)

source = repo / 'tools/outlook-intake-copy.c'
helper = settings.copy_helper
work_root = settings.repo / 'out/outlook-intake'
flags = ['-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
         f'-DOUTLOOK_PROFILE={json.dumps(str(settings.profile))}',
         f'-DOUTLOOK_WORK_ROOT={json.dumps(str(work_root))}',
         f'-DOUTLOOK_REPO={json.dumps(str(settings.repo))}']
pin = helper.with_suffix('.pin')
wanted = hashlib.sha256(source.read_bytes() + json.dumps(flags).encode()).hexdigest()
helper.parent.mkdir(parents=True, exist_ok=True)
if not (helper.is_file() and os.access(helper, os.X_OK) and pin.is_file() and pin.read_text() == wanted):
    with tempfile.TemporaryDirectory(dir=helper.parent) as temp:
        built = Path(temp) / helper.name
        subprocess.run(['/usr/bin/cc', *flags, str(source), '-o', str(built)], check=True)
        built.chmod(0o700)
        os.replace(built, helper)
    pin.write_text(wanted)
settings.verify_helper()
PY

mkdir -p "$REPO/out"
"$PY" -m lib.outlook_settings render "$SOURCE" > "$TMP"
/usr/bin/plutil -lint "$TMP" >/dev/null
cp "$TMP" "$RENDERED"
chmod 644 "$RENDERED"

touch "$LOG"
chmod 600 "$LOG"

print -- "install-outlook-intake: hxprobe pinned and built; plist rendered to $RENDERED"
print -- "install-outlook-intake: one-time step: grant Full Disk Access to $HELPER"
print -- "  System Settings > Privacy & Security > Full Disk Access > add the helper above."
print -- "install-outlook-intake: it is NOT loaded. To load it, run:"
print -- "  install -m 644 '$RENDERED' '$DEST'"
print -- "  launchctl bootstrap gui/$(id -u) '$DEST'"
print -- "install-outlook-intake: private reports: $REPO/out/outlook-intake/"

LABEL="com.carr.outlook-refresh"
SOURCE="$REPO/ops/launchd/$LABEL.plist"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
RENDERED="$REPO/out/$LABEL.plist"
LOG="$REPO/out/outlook-refresh-launchd.log"
"$PY" -m lib.outlook_settings render "$SOURCE" > "$TMP"
/usr/bin/plutil -lint "$TMP" >/dev/null
cp "$TMP" "$RENDERED"
chmod 644 "$RENDERED"
touch "$LOG"
chmod 600 "$LOG"
print -- "install-outlook-intake: refresh worker rendered for the same host and user; it is NOT loaded."
print -- "  install -m 644 '$RENDERED' '$DEST'"
print -- "  launchctl bootstrap gui/$(id -u) '$DEST'"
