#!/bin/sh
# Refuse a Worker registry that drops a verb its base commit shipped, unless
# ops/config/retired-verbs.v1.json retires it by name (loop #276: production
# once lost nine verbs and nothing objected). Production deploys only main
# commits that passed this check, so the base commit's registry is the
# baseline; no deployment ledger or credential is needed.
#   usage: verb-loss-vs-base.sh <repo> <base-rev>   (prints the verb count)
set -eu

[ "$#" -eq 2 ] || { echo "usage: verb-loss-vs-base.sh <repo> <base-rev>" >&2; exit 64; }
REPO="$1"
BASE="$2"
OPS="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PY="$OPS/../.venv/bin/python"
[ -x "$PY" ] || PY=python3

BASE_TREE="$(mktemp -d "${TMPDIR:-/tmp}/verb-loss-base.XXXXXX")"
trap 'rm -rf "$BASE_TREE"' EXIT HUP INT TERM
git -C "$REPO" archive "$BASE" mcp-server | tar -x -C "$BASE_TREE" \
  || { echo "verb-loss-vs-base: cannot read mcp-server at $BASE" >&2; exit 1; }

BASE_NAMES="$(sh "$OPS/verb-count.sh" "$BASE_TREE/mcp-server" --names)"
BASE_REGISTRY="$(printf '%s' "$BASE_NAMES" | "$PY" -c \
  'import json,sys; v=json.load(sys.stdin); print(json.dumps({"verbs": v, "verb_count": len(v)}))')"
sh "$OPS/verb-count.sh" "$REPO/mcp-server" --check-retirements "$BASE_REGISTRY"
