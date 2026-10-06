#!/bin/zsh
# type-check.sh — the nightly type-check tripwire (added 2026-08-06, Joe's go:
# the Python-native answer to the Rust question, loop #218).
# Runs mypy over the repo's Python with the DELIBERATELY LENIENT mypy.ini:
# it catches shape mistakes in data hand-offs (text where a date should be,
# None where a row was assumed), not truth mistakes — output verification
# (protocol rule 28) remains the real safety net above this.
# THE GRANDFATHER LIST IS EMPTY (verified 2026-08-14). This header used to say
# "19 legacy files are grandfathered with # mypy: ignore-errors headers"; those
# headers were self-removing and every one of them has since been removed, so
# the sweep now covers every file it names with no exemptions at all. Stating
# the CONDITION rather than a count, per rule b01edd26 — the old number went
# stale silently, and while it stood it told every session reading this file
# that a legacy-noise bucket existed, which is the one place a real regression
# gets waved off as already known.
# If you add an exemption, say why here; a green run means green everywhere.
# Manual path is THE SAME script (rule a8c55a47): ./bin/type-check.sh
# Risk color GREEN: read-only, writes nothing, sends nothing.
# WHERE mypy COMES FROM, and why this is not just ./.venv/bin/mypy any more.
# Since 2026-08-14 this script is also CI's `types` class (ops/ci.sh), so it
# runs in two environments: Joe's Mac, which has a .venv, and a GitHub runner,
# which pip-installs requirements.lock into the system python and has no .venv
# at all. One script, two homes — rule a8c55a47 says the manual path and the
# automated path must be the SAME CODE, and the directory list below is the
# thing that must not fork.
#
# Exit 78 (EX_CONFIG) when mypy is absent anywhere: the nightly chain reads 78
# as SKIP rather than FAIL, and CI reads it as a skip that --strict then refuses.
# Neither treats "the tool is missing" as "the code is broken".
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO" || exit 2

if [ "$#" -gt 0 ]; then
  if [ "$1" != "--files" ] || [ "$#" -lt 2 ]; then
    echo "usage: type-check.sh [--files <path> ...]" >&2
    exit 2
  fi
  shift
else
  set -- pipelines tools exporters lib generators shared fill-engine bin hooks ops
fi

MYPY="$REPO/.venv/bin/mypy"
# A WORKTREE HAS NO .venv OF ITS OWN, and worktrees are now the normal way to
# push here: hooks/git-writer-gate.py refuses a branch change while the shared
# canonical checkout carries another session's uncommitted work, which it almost
# always does. So the second place to look is the COMMON checkout's virtualenv --
# the same repository, the same pinned mypy, one directory up from the shared
# .git that `git rev-parse --git-common-dir` names.
#
# WHY THIS MATTERED. On 2026-08-27 a push from a worktree printed "mypy absent"
# and skipped, the hosted run then failed two classes on one missing annotation,
# and eight minutes of CI caught what a local second would have. Exit 78 is the
# right answer for a bare runner with no toolchain; it was the wrong answer for a
# worktree of a checkout that has one.
if [ ! -x "$MYPY" ]; then
  COMMON_DIR="$(git -C "$REPO" rev-parse --git-common-dir 2>/dev/null || true)"
  case "$COMMON_DIR" in
    "") ;;
    /*) CANONICAL="$(dirname "$COMMON_DIR")" ;;
    *)  CANONICAL="$(cd "$REPO/$(dirname "$COMMON_DIR")" 2>/dev/null && pwd || true)" ;;
  esac
  if [ -n "${CANONICAL:-}" ] && [ "$CANONICAL" != "$REPO" ] && [ -x "$CANONICAL/.venv/bin/mypy" ]; then
    MYPY="$CANONICAL/.venv/bin/mypy"
  fi
fi
# A GitHub runner has neither: no .venv anywhere, and its git common dir is the
# repository itself, so this falls through to PATH exactly as it always did.
if [ ! -x "$MYPY" ]; then
  MYPY="$(command -v mypy 2>/dev/null || true)"
fi
if [ -z "$MYPY" ] || [ ! -x "$MYPY" ]; then
  echo "type-check: mypy is not installed (it is pinned in requirements.lock)" >&2
  exit 78
fi

exec "$MYPY" "$@"
