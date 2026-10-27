#!/bin/zsh
# Build the configured Outlook copier and decoder, then render the jobs.
#
# A narrow installer like install-outlook-triage.sh: ops/config-as-code.py install
# would also rewrite every unrelated hook and LaunchAgent. The decoder is built
# from vendor/hxprobe in this checkout, the tree the hxprobe CI class tests, with
# the same `cargo build --release --locked`. Cargo's target directory sits under
# the settings `vendor` path. The source receipt must match before locked
# tests and build; a local receipt records the installed binary and toolchain. It
# never loads the job: it renders the plist, creates the launchd log at 0600,
# and prints the two commands the operator runs to load it.
#
# The Outlook jobs run from the settings `repo`, a checkout that only this
# installer moves. Run from any other checkout, it first fetches its own HEAD
# commit into that path (detached), links .venv, out and mcp-server/node_modules
# back to the installing checkout the first time only, then re-runs itself from
# there. Pulling the canonical checkout therefore never ships Outlook code, and
# the granted helper's compiled paths stay the same on every later run.
#
# Usage: ./bin/install-outlook-intake.sh

set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd -P)"
cd "$REPO"
PY="$REPO/.venv/bin/python"
RUNTIME="$("$PY" -m lib.outlook_settings field repo)"
export PATH="$("$PY" -m lib.outlook_settings field executable_path)"
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE
if [ "$REPO" != "$RUNTIME" ]; then
  SHA="$(git rev-parse HEAD)"
  if [ -e "$RUNTIME" ]; then
    if [ "$(git -C "$RUNTIME" rev-parse --show-toplevel 2>/dev/null)" != "$RUNTIME" ] \
       || [ -n "$(git -C "$RUNTIME" status --porcelain --untracked-files=no)" ]; then
      print -u2 -- "install-outlook-intake: $RUNTIME must be a clean checkout that only this installer moves"
      exit 1
    fi
  else
    mkdir -p "$(dirname "$RUNTIME")"
    git init --quiet "$RUNTIME"
  fi
  git -C "$RUNTIME" fetch --quiet --depth=1 "$REPO" "$SHA"
  git -C "$RUNTIME" checkout --quiet --detach "$SHA"
  mkdir -p "$REPO/out"
  for shared in .venv out mcp-server/node_modules; do
    if [ ! -e "$RUNTIME/$shared" ] && [ ! -L "$RUNTIME/$shared" ] && [ -e "$REPO/$shared" ]; then
      ln -s "$(cd "$REPO/$shared" && pwd -P)" "$RUNTIME/$shared"
    fi
  done
  print -- "install-outlook-intake: Outlook jobs run from $RUNTIME at $SHA"
  exec /bin/zsh "$RUNTIME/bin/install-outlook-intake.sh"
fi
"$PY" -m lib.outlook_settings service intake
"$PY" -m lib.outlook_settings service refresh
LABEL="com.carr.outlook-intake"
SOURCE="$REPO/ops/launchd/$LABEL.plist"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
RENDERED="$REPO/out/$LABEL.plist"
LOG="$REPO/out/outlook-intake-launchd.log"
VENDOR="$("$PY" -m lib.outlook_settings field vendor)"
HELPER="$("$PY" -m lib.outlook_settings field copy_helper)"
# build.rs also reads the store atlas and its generator from the repository root.
SOURCES=(vendor/hxprobe spec/outlook/hxstore-atlas.json ops/hxstore-atlas.py)
DECODER="$VENDOR/hxprobe/target/release/hxprobe"
PIN="$VENDOR/.carr-pin"
TMP="$(mktemp -t carr-outlook-intake-plist)"
trap 'rm -f "$TMP"' EXIT

if [ ! -f "$SOURCE" ]; then
  print -u2 -- "install-outlook-intake: missing source plist: $SOURCE"
  exit 1
fi

if [ -n "$(git -C "$REPO" status --porcelain -- "${SOURCES[@]}")" ]; then
  print -u2 -- "install-outlook-intake: $REPO has uncommitted decoder sources; the decoder builds only from committed source"
  exit 1
fi
WANT_PIN="source $(git -C "$REPO" rev-parse "${SOURCES[@]/#/HEAD:}" | /usr/bin/paste -sd' ' -)"
HAVE_PIN="$(cat "$PIN" 2>/dev/null || true)"
if [ ! -x "$DECODER" ] || [ "$HAVE_PIN" != "$WANT_PIN" ]; then
  # Only the old upstream-plus-patch checkout is refused; a folder left by a
  # failed build of this installer has no pin and no crate, and is retried.
  if [ -e "$VENDOR/hxprobe/Cargo.toml" ] || { [ -n "$HAVE_PIN" ] && [ "${HAVE_PIN#source }" = "$HAVE_PIN" ]; }; then
    print -u2 -- "install-outlook-intake: $VENDOR holds the old upstream decoder checkout; move it aside first"
    exit 1
  fi
  "$PY" "$REPO/ops/hxprobe-source-digest.py" --build-into "$VENDOR"
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
work_root = (settings.repo / 'out/outlook-intake').resolve()
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

print -- "install-outlook-intake: hxprobe built from vendor/hxprobe; plist rendered to $RENDERED"
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
