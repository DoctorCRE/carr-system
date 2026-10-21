#!/bin/zsh
# install-outlook-intake.sh — build the pinned hxprobe and prepare com.carr.outlook-intake.
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
LABEL="com.carr.outlook-intake"
SOURCE="$REPO/ops/launchd/$LABEL.plist"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
RENDERED="$REPO/out/$LABEL.plist"
LOG="$REPO/out/outlook-intake-launchd.log"
COMMIT="191b54e42757687806202f8f696e9066f0514e11"
VENDOR="$HOME/carr-local/hxstore-reverse-engineering"
PATCH="$REPO/vendor-patches/hxprobe-calendar-and-bodies.patch"
PIN="$VENDOR/.carr-pin"
WANT_PIN="$COMMIT $(/usr/bin/shasum -a 256 "$PATCH" | cut -d' ' -f1)"
TMP="$(mktemp -t carr-outlook-intake-plist)"
trap 'rm -f "$TMP"' EXIT

if [ ! -f "$SOURCE" ]; then
  print -u2 -- "install-outlook-intake: missing source plist: $SOURCE"
  exit 1
fi
if ! (cd "$REPO" && "$REPO/.venv/bin/python" -c \
    "import sys; from lib import machine_role; from lib.launchd_scope import allowed_on_machine; sys.exit(0 if allowed_on_machine('$LABEL.plist', machine_role.is_primary('$REPO')) else 1)" \
    2>/dev/null); then
  print -u2 -- "install-outlook-intake: this Mac's role does not run $LABEL (see ops/config/services.json runs_on)"
  exit 1
fi

if [ ! -x "$VENDOR/hxprobe/target/release/hxprobe" ] || [ "$(cat "$PIN" 2>/dev/null)" != "$WANT_PIN" ]; then
  if [ -e "$VENDOR" ]; then
    print -u2 -- "install-outlook-intake: $VENDOR exists but is not the pinned, patched build; move it aside first"
    exit 1
  fi
  mkdir -p "$HOME/carr-local"
  /usr/bin/curl -fsSL "https://codeload.github.com/ukd1/hxstore-reverse-engineering/tar.gz/$COMMIT" \
    | /usr/bin/tar -xz -C "$HOME/carr-local"
  mv "$HOME/carr-local/hxstore-reverse-engineering-$COMMIT" "$VENDOR"
  (cd "$VENDOR" && /usr/bin/patch -p1 --quiet < "$PATCH")
  (cd "$VENDOR/hxprobe" && cargo test --release --locked --quiet && cargo build --release --locked --quiet)
  print -- "$WANT_PIN" > "$PIN"
fi

mkdir -p "$REPO/out"
/usr/bin/sed -e "s|{{REPO}}|$REPO|g" -e "s|{{HOME}}|$HOME|g" "$SOURCE" > "$TMP"
/usr/bin/plutil -lint "$TMP" >/dev/null
cp "$TMP" "$RENDERED"
chmod 644 "$RENDERED"

touch "$LOG"
chmod 600 "$LOG"

print -- "install-outlook-intake: hxprobe pinned and built; plist rendered to $RENDERED"
print -- "install-outlook-intake: it is NOT loaded. To load it, run:"
print -- "  install -m 644 '$RENDERED' '$DEST'"
print -- "  launchctl bootstrap gui/$(id -u) '$DEST'"
print -- "install-outlook-intake: private reports: $REPO/out/outlook-intake/"
