#!/usr/bin/env python3
"""scheduled-run-record.py — the Stop hook that closes Program 3's last gap:
"Job failure becomes a durable failed run, incident, or Work Request. It
cannot exist only in stdout or a local log." (roadmap line, quoted verbatim).

WHY THIS EXISTS. The ~17 Claude Code scheduled tasks on this Mac (loop-drain-
weekdays, restore-rehearse-weekly, the nightly-record-layer LAUNCH session,
...) are prompts executed by a Claude session the scheduler starts, not scripts
with an exit code. bin/nightly.sh's launchd-run STEPS already record
themselves to ops.run via record_run() (see lib/scheduled_run.py's docstring
for the full design). Nothing did the equivalent for a scheduled Claude session
itself — if one hit a permission gate, ran out of turns, or otherwise failed,
that fact lived only in the session transcript and in Joe's inbox notification,
never in the record layer, so it never became an incident and never showed up
in `ops-record.py health` the way every other automation on this Mac does.

HOW IT WORKS, all of it in lib/scheduled_run.py so the hook and the manual CLI
(bin/record-scheduled-run.py) are the same code (rule a8c55a47):
  1. Cheap check: does this transcript's first few lines carry the
     `<scheduled-task name="...">` launch marker? If not, this is an ordinary
     interactive session and the hook exits immediately — no file writes.
  2. If it does: read the transcript, work out succeeded/failed
     DETERMINISTICALLY (a PreToolUse gate DENY anywhere, or a trailing
     unrecovered tool error — see lib/scheduled_run.py), and record one
     ops.run row via tools/ops-record.py.

IDEMPOTENCY: exactly once per session, at the FIRST Stop. A scheduled session
that a human later continues interactively (observed in practice — a scheduled
run's transcript can keep growing for hours after its own turn ends) must NOT
re-record on every later turn of that same session; only the first Stop is the
scheduled task's own work, so the per-session cache short-circuits every Stop
after that.

NEVER BLOCKS. This hook only observes; it has no opinion on whether the turn
should continue, so it never emits a "block" decision and never denies
anything. It also never lets a recording failure become visible as a session
failure: if the DB credential is absent, ops-record.py's own refusal is logged
to out/hook-guard.log (see lib/scheduled_run.py's run_ops_record) and this
hook still exits 0. A scheduled task's own work is never blocked or altered by
whether its own outcome could be filed.

FAILS OPEN on any internal error, like every gate in this file's family.
"""
import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from lib.scheduled_run import log, quick_launched_task, record_from_transcript  # noqa: E402


def _repo_root():
    """The repo root, from CARR_REPO_ROOT env or script-relative.

    The selftest points CARR_REPO_ROOT at a throwaway tree
    so concurrent ci.sh runs never share one idempotency-cache directory.
    Production leaves the variable unset.
    """
    env = os.environ.get("CARR_REPO_ROOT")
    if env and os.path.isdir(env):
        return os.path.realpath(env)
    return REPO


CACHE_DIR = os.path.join(_repo_root(), "out", "scheduled-run-record-cache")


def cache_path(session_id: str) -> str:
    safe = "".join(c for c in (session_id or "") if c.isalnum() or c in "-_")
    return os.path.join(CACHE_DIR, safe or "unknown")


def already_recorded(session_id: str) -> bool:
    return bool(session_id) and os.path.exists(cache_path(session_id))


def mark_recorded(session_id: str) -> None:
    if not session_id:
        return
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cache_path(session_id), "w") as fh:
            fh.write("recorded\n")
    except Exception:
        pass


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.hook_runtime import decision, run


def _parse_error(exc):
    log(f"ALLOW(parse-error) {exc}")
    return 0


def _decision_error(exc):
    log(f"ALLOW(internal-error) {exc}")
    return 0


@decision(on_error=_decision_error)
def decide(payload):
    if (payload.get("hook_event_name") or "") != "Stop":
        return 0
    if payload.get("stop_hook_active"):
        return 0

    session_id = payload.get("session_id") or payload.get("sessionId") or ""
    transcript = payload.get("transcript_path") or payload.get("transcriptPath") or ""
    if not transcript or not os.path.exists(transcript):
        return 0

    if already_recorded(session_id):
        return 0

    # Cheap head-only check first — this is the cost every ordinary,
    # non-scheduled session's every Stop pays, so it must stay cheap.
    if not quick_launched_task(transcript):
        return 0

    result = record_from_transcript(transcript, source_ref="hooks/scheduled-run-record.py")
    mark_recorded(session_id)
    if not result.get("recorded"):
        # Logged inside record_from_transcript already; nothing more to do.
        # Never surface this as a block — an unrecordable run is a gap in
        # observability, not a reason to alter or repeat the scheduled
        # task's own turn.
        pass
    return 0


def main():
    return run(decide, parse_error=_parse_error)


if __name__ == "__main__":
    sys.exit(main())
