#!/bin/zsh
# cc-version-string.sh — the ONE place that reads "what Claude Code version is
# on this Mac". Extracted from cc-version-sentinel.sh (rule a8c55a47: the
# sentinel and the cc-update-audit STEP 0 gate were building the identical
# "cli=<v> app=<v>" string independently; one implementation, two callers).
#
# Prints exactly one line: cli=<version-or-none> app=<version-or-none>
# Always exits 0 — an unreadable component reads as "none"; deciding what
# "none" means (first run vs. a real read failure) is the CALLER's job, not
# this script's, because the two callers here answer that question differently.
#
# Usage: ./bin/cc-version-string.sh

# ABSOLUTE PATH, NOT `claude`. launchd does not run a login shell, so its PATH
# does not contain /opt/homebrew/bin. Found the only way it could be — by
# installing the sentinel and reading its first real run, which reported
# `cli=none`, called that a version CHANGE, and notified Joe about an update
# that had not happened. A sentinel whose failure mode is a false alarm is
# worse than no sentinel: it trains the person receiving it to ignore it.
CLAUDE_BIN=""
for c in /opt/homebrew/bin/claude /usr/local/bin/claude "$HOME/.local/bin/claude"; do
  [ -x "$c" ] && { CLAUDE_BIN="$c"; break; }
done
[ -z "$CLAUDE_BIN" ] && CLAUDE_BIN="$(command -v claude 2>/dev/null || true)"
CLI=""
[ -n "$CLAUDE_BIN" ] && CLI="$("$CLAUDE_BIN" --version 2>/dev/null | awk '{print $1}')"
APP="$(ls -1 "$HOME/Library/Application Support/Claude/claude-code/" 2>/dev/null | sort -V | tail -1)"

print -r -- "cli=${CLI:-none} app=${APP:-none}"
exit 0
