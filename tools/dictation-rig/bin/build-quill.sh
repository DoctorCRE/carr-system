#!/bin/sh
# build-quill.sh — the one way quill gets built. Wraps the toolchain quirk so
# nobody rediscovers it: this Mac's CommandLineTools ships without its own
# libc++ headers (include/c++/v1 missing), so C++ compiles inside SPM
# dependencies (FluidAudio's FastClusterWrapper) die with "'cmath' file not
# found" unless CPLUS_INCLUDE_PATH points at the SDK's libc++. Found and fixed
# 2026-08-07 (dictation-rig Phase A build session).
set -eu

QUILL_DIR="$(cd "$(dirname "$0")/../vendor/quill" && pwd)"
CPLUS_INCLUDE_PATH="$(xcrun --show-sdk-path)/usr/include/c++/v1"
export CPLUS_INCLUDE_PATH

# --- carry our patches onto the vendored upstream --------------------------
#
# vendor/quill is a SUBMODULE of the third-party upstream github.com/digimata/quill
# (MIT), checked out detached. Any edit made directly in that tree is invisible to
# this repo and a `git submodule update` erases it without a word. Our changes
# therefore live as patch files here and get re-applied on every build, so the
# build is reproducible from a clean submodule checkout and nothing depends on
# pushing to a repo we do not own.
#
# Each patch is applied only if it is not already present, so repeated builds and
# a dirty submodule tree are both fine.
PATCH_DIR="$(cd "$(dirname "$0")/../patches" && pwd)"
if [ -d "$PATCH_DIR" ]; then
    for patch in "$PATCH_DIR"/*.patch; do
        [ -f "$patch" ] || continue
        patch_name="$(basename "$patch")"
        if git -C "$QUILL_DIR" apply --reverse --check "$patch" >/dev/null 2>&1; then
            echo "build-quill.sh: $patch_name already applied"
        elif git -C "$QUILL_DIR" apply "$patch" >/dev/null 2>&1; then
            echo "build-quill.sh: applied $patch_name"
        else
            echo "build-quill.sh: ERROR $patch_name does not apply to this quill checkout" >&2
            echo "build-quill.sh: the upstream submodule probably moved; rebase the patch before building" >&2
            exit 1
        fi
    done
fi

cd "$QUILL_DIR"
swift build -c release
ls -la .build/release/quill
echo "quill built at $QUILL_DIR/.build/release/quill"

# FluidAudio's own CLI, built from the same pinned checkout quill resolves, is
# the rig's on-device diarizer (bin/speaker_split.py). Without it transcripts
# fall back to one label per channel, so a failure here is loud but not fatal.
if swift build -c release --product fluidaudiocli; then
    echo "diarizer built at $QUILL_DIR/.build/release/fluidaudiocli"
else
    echo "build-quill.sh: WARN fluidaudiocli failed to build; transcripts keep one label per channel" >&2
fi
