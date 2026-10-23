#!/bin/zsh
# Read the CLI and desktop runtime versions for the cc-update-audit predicate.
# Prints cli=<version-or-none> app=<version-or-none> and exits 0.

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
