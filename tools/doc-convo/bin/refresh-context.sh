#!/usr/bin/env bash
# refresh-context.sh — pull the hot-context snapshot Doc answers from (loop #250).
#
# Council v1 cut: the voice brain reads a LOCAL SNAPSHOT, not live tools.
# Uses the probe token (server-side locked profile: reads + 3 logging writes),
# so this path structurally cannot alter records — the same rail smoke-reads.sh
# runs under. Output: assets/hot-context.md (overwritten whole each run).

set -euo pipefail

TOOL_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
OUT="$TOOL_DIR/assets/hot-context.md"
API="${CARR_MCP_URL:-https://api.doctorcre.com/mcp}"
ENVFILE="${CARR_MCP_ENV:-$HOME/.config/carr/mcp-tokens.env}"
[ -f "$ENVFILE" ] && { set -a; . "$ENVFILE"; set +a; }
TOKEN="${CARR_MCP_PROBE_TOKEN:-}"
[ -n "$TOKEN" ] || { echo "refresh-context: no CARR_MCP_PROBE_TOKEN in $ENVFILE" >&2; exit 2; }

_id=0
call() { # $1 verb, $2 json args -> result text on stdout, or nonempty error on stderr
  _id=$((_id + 1))
  curl -sS -m 30 "$API" -X POST \
    -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
    -d "{\"jsonrpc\":\"2.0\",\"id\":$_id,\"method\":\"tools/call\",\"params\":{\"name\":\"$1\",\"arguments\":$2}}" \
  | python3 -c '
import json, sys
r = json.load(sys.stdin)
if "error" in r:
    sys.exit("verb error: %s" % r["error"])
for block in r["result"]["content"]:
    if block.get("type") != "text":
        continue
    # Compact for the voice brain (demo lesson: a 36KB dump slows every
    # turn): drop nulls, ids, and file paths the spoken layer never uses.
    try:
        data = json.loads(block["text"])
        def slim(o):
            if isinstance(o, dict):
                return {k: slim(v) for k, v in o.items()
                        if v is not None and k not in
                        ("id", "notes_path", "phase_sort", "uuid")}
            if isinstance(o, list):
                return [slim(x) for x in o]
            return o
        print(json.dumps(slim(data), separators=(",", ":")))
    except (json.JSONDecodeError, TypeError):
        print(block["text"])
'
}

mkdir -p "$TOOL_DIR/assets"
TMP=$(mktemp)
{
  echo "# HOT CONTEXT — snapshot $(date '+%Y-%m-%d %H:%M %Z')"
  echo "Read-only picture of the book. Anything not here: Doc says he'd need the desk."
  echo
  echo "## Deal board";    call deal-board '{}'
  echo
  echo "## Hot leads";     call lead-hot '{}'
  echo
  echo "## Today's triage"; call today-triage '{}'
} > "$TMP"
mv "$TMP" "$OUT"
echo "refresh-context: wrote $OUT ($(wc -c < "$OUT" | tr -d ' ') bytes)"
