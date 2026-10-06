#!/usr/bin/env bash
# Controller acceptance gate for a DoctorCRE build bundle.
#
# WHY THIS EXISTS. On 2026-09-02 three capability-map builds failed review on
# one shape: rationales citing evidence the build never opened. The check that
# catches it (ops/citation-trace-check.py) existed only as a thing a controller
# might remember to run, and a check nobody runs is not a check. This wires it
# into the one step every bundle must pass through.
#
# It also copies the builder's trace out of the session directory and into the
# evidence tree beside the bundle, verifying the copy byte-for-byte. That copy
# was previously a manual step, which meant the reviewer's access to the
# verification surface depended on the controller remembering.
#
#   ops/accept-doctorcre-bundle.sh <seed> <revision> <agent-id>
#     seed      a03a | a03b | a03c | ...
#     revision  r2 | r3 | ...
#     agent-id  the builder agent id, whose trace lives under the session's
#               subagents/ directory as agent-<id>.jsonl
#
# Exit 0 = accepted for review. Nonzero = returned to the builder, with the
# reason printed. This gate does NOT judge the graph; it judges whether the
# bundle's own citations survive contact with its trace.
set -euo pipefail

if [ "$#" -ne 3 ]; then
  echo "usage: $0 <seed> <revision> <agent-id>" >&2
  exit 2
fi
seed="$1"; rev="$2"; agent="$3"
root="${CARR_ROOT:-/Users/booko/carr-system}"
bundle="$root/out/doctorcre-program/${seed}-${rev}"
dest="$root/out/doctorcre-program/${seed}-${rev}-builder-trace.jsonl"

# The session directory is discovered, never assumed: a hardcoded session id
# would silently accept a stale trace from a previous run, which is the
# dated-artifact-read-as-present-state failure this program keeps having.
src="$(find "$HOME/.claude/projects" -name "agent-${agent}.jsonl" -type f 2>/dev/null | head -1)"
if [ -z "$src" ]; then
  echo "REFUSED: no trace found for agent ${agent}. Without the trace there is" >&2
  echo "no verification surface, and an artifact-only review cannot catch a" >&2
  echo "fabricated or unread citation. Locate the trace and retry." >&2
  exit 3
fi
if [ ! -d "$bundle" ]; then
  echo "REFUSED: no bundle at $bundle" >&2
  exit 3
fi

cp "$src" "$dest"
a="$(shasum -a 256 "$src"  | cut -d' ' -f1)"
b="$(shasum -a 256 "$dest" | cut -d' ' -f1)"
if [ "$a" != "$b" ]; then
  echo "REFUSED: trace copy is not byte-identical to the source." >&2
  exit 3
fi
echo "trace:   $src"
echo "         -> $dest"
echo "         sha256 $a (verified identical)"
echo

# The check is resolved relative to THIS script, not to CARR_ROOT. The two
# ship together, and a worktree running its own copy of the gate must run its
# own copy of the check — resolving through CARR_ROOT pointed at a canonical
# checkout that did not have the tool yet and failed on the first run.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
check="$here/citation-trace-check.py"
if [ ! -f "$check" ]; then
  echo "REFUSED: citation-trace-check.py not found beside this gate at $here" >&2
  exit 3
fi
py="$root/.venv/bin/python"
[ -x "$py" ] || py="$(command -v python3)"
set +e
PYTHONDONTWRITEBYTECODE=1 "$py" "$check" "$bundle" "$dest"
rc=$?
set -e

echo
if [ "$rc" -ne 0 ]; then
  echo "BUNDLE RETURNED TO THE BUILDER — it cites evidence its trace does not"
  echo "show it opening. Every path listed above is either a citation to fix or"
  echo "a file to actually read. Do not send this to review."
  exit 1
fi
echo "ACCEPTED FOR REVIEW — citations survive the trace. This gate is a FLOOR:"
echo "it matches literal paths only, and it does not judge the graph. The"
echo "reviewer still reads the trace."
