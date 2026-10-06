#!/usr/bin/env python3
"""model-floor-gate.py — stop a voice run that landed on too small a model.

WHY THIS EXISTS. Two daily runs draft in Joe's public voice and both carry a
HARD model floor taught by Joe himself: the X reply run (2026-07-20, after
Sonnet produced shallow keyword-matched drafts) and the LinkedIn engagement run
(2026-07-21, "comment quality is the whole point of the routine"). Both floors
were enforced the same way: prose telling the run to "check your own model" and
stop if it is Sonnet.

THAT SELF-CHECK IS THE WEAK LINK, and not for the obvious reason. It is not
that a run would defy the instruction; it is that asking a model to introspect
its own identity is asking the least reliable component in the system for the
one fact the gate depends on. The harness KNOWS the answer and writes it down.
So the run should be TOLD, not asked.

WHERE IT CAN AND CANNOT BITE, stated plainly because it shaped the design.
Neither run publishes anything. The X run says "Claude never posts a reply,
ever"; the LinkedIn run says "You never post, comment, like, connect, or
follow." Both draft and hand the shortlist to Joe in chat. So unlike the
executor-tier gate next door, there is NO publish call at the moment of harm to
intercept — the harmful artifact is chat text, which no hook sees. The only
enforceable moment is EARLY, before the drafting work happens, and the earliest
tool both runs reach for is the browser. That is where this sits. A run that
somehow drafted without touching a tool would slip past, and that is an honest
limit of the mechanism rather than something papered over.

HOW IT KNOWS THE MODEL. The PreToolUse payload does NOT carry one. Verified
against the payload-construction code in the installed binary: a tool hook
receives session_id, transcript_path, cwd, prompt_id, permission_mode,
agent_id, agent_type and effort, and `effort` is a derived reasoning tier, not
a model identity. But transcript_path IS handed over, and every assistant
record in that JSONL carries `message.model` as a plain string written by the
harness. Tail the transcript, read the newest one. That is the fact the prose
self-check was guessing at.

HOW IT KNOWS WHICH RUN. The floors are per-run, so a general session must not
be gated. The opening user message of a scheduled run carries the task name, so
the head of the transcript is matched against the `match` strings in
ops/config/model-floors.json. A session that matches nothing is cached as
unbound in out/model-floor-cache/ and costs one small head read for the whole
session, not one per tool call.

FLOORS ARE DATA. Adding a third floor is an entry in the config, not an edit
here (rule 5e89c211).

FAILS OPEN on any error, like every gate here. A gate that crashes must never
be able to stop work. Logged to out/hook-guard.log.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

# Script-relative, NOT expanduser("~/carr-system") — same fix as
# hooks/record-home-gate.py and the tools/test-*.py suites (commit fad87a4).
# A clone outside $HOME makes CONFIG a path that does not exist, and this gate
# fails open when it cannot read its floors — so the failure mode is a gate
# that reports healthy while enforcing nothing.
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:                                    # telemetry only — never load-bearing
    import hook_meter
    LOG = hook_meter.guard_log_path(REPO)
except Exception:                       # a missing meter must not change a verdict
    LOG = os.path.join(REPO, "out", "hook-guard.log")
CONFIG = os.path.join(REPO, "ops", "config", "model-floors.json")
CACHE_DIR = os.path.join(REPO, "out", "model-floor-cache")

# How many lines at the head of a transcript to scan for the run's identity.
#
# THREE, not twelve, and the difference is correctness rather than speed.
# Measured against a real scheduled-run transcript on 2026-08-13: a scheduled
# run's FIRST record (line 0) is a queue-operation whose `content` field opens
# with <scheduled-task name="..." file="...">, so the marker is always on line
# 0 and one line would do. Twelve lines read 139KB of an active session, which
# is not just wasteful: it sweeps in ordinary conversation, so a session merely
# DISCUSSING the X reply run would match its name and be gated as if it were
# that run. Narrow to the opening record plus margin, so identity comes from
# how the session was launched rather than from anything it later talks about.
HEAD_LINES = 3
# How many lines at the tail to scan back through for the newest assistant
# record carrying a model.
TAIL_BYTES = 400_000


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        with open(LOG, "a") as fh:
            fh.write(f"{ts} model-floor-gate {msg.rstrip()}\n")
    except Exception:
        pass


def load_config():
    with open(CONFIG, "r", encoding="utf-8") as fh:
        return json.load(fh)


def tier_of(model_string, ranks):
    """Rank a model string by the tier word it contains. Returns (name, rank).

    Substring matching on purpose: the exact ids churn (claude-opus-4-8,
    claude-opus-5, claude-sonnet-5, claude-sonnet-5-5, claude-haiku-4-5-20251001, claude-fable-5)
    and a gate that has to be edited every time a model ships is a gate that
    silently stops matching. Unknown strings return rank 0, which is treated as
    unknown rather than as failing the floor.
    """
    if not model_string:
        return (None, 0)
    low = model_string.lower()
    best = (None, 0)
    for name, rank in ranks.items():
        if name in low and rank > best[1]:
            best = (name, rank)
    return best


LAUNCH_MARKER = re.compile(r'<scheduled-task\s+name="([^"]+)"')


def launched_task(path, n=HEAD_LINES):
    """The scheduled task this session was LAUNCHED as, or None.

    Identity comes from the launch marker and from nothing else. A scheduled
    run's first record is a queue-operation whose `content` opens with
    <scheduled-task name="..." file="...">, so the name is read out of that
    attribute.

    THIS REPLACED A SUBSTRING SEARCH OVER THE TRANSCRIPT HEAD, which was wrong
    in a way a test caught before this ever ran: an ordinary interactive
    session whose opening message merely MENTIONED a floor-bound run ("let's
    look at x-reply-run-daily") matched the floor and was gated as if it were
    that run. Naming a run is not being one. Reading the launch attribute makes
    the check immune to whatever the session goes on to talk about.
    """
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            if i >= n:
                break
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if not isinstance(rec, dict):
                continue
            content = rec.get("content")
            if not isinstance(content, str):
                continue
            found = LAUNCH_MARKER.search(content)
            if found:
                return found.group(1).strip()
    return None


def newest_model(path):
    """Newest assistant record's message.model, scanning backwards."""
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        if size > TAIL_BYTES:
            fh.seek(size - TAIL_BYTES)
            fh.readline()  # discard the partial line
        chunk = fh.read().decode("utf-8", errors="replace")
    for line in reversed(chunk.splitlines()):
        line = line.strip()
        if not line or '"model"' not in line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        msg = rec.get("message")
        if isinstance(msg, dict) and isinstance(msg.get("model"), str):
            return msg["model"]
    return None


def cache_path(session_id):
    safe = "".join(c for c in (session_id or "") if c.isalnum() or c in "-_")
    return os.path.join(CACHE_DIR, safe or "unknown")


def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        log(f"ALLOW(parse-error) {exc}")
        sys.exit(0)

    try:
        if (payload.get("hook_event_name") or "") != "PreToolUse":
            sys.exit(0)

        transcript = payload.get("transcript_path") or ""
        session_id = payload.get("session_id") or ""
        if not transcript or not os.path.exists(transcript):
            sys.exit(0)

        # Cached verdict: this session was already found not to be floor-bound.
        cp = cache_path(session_id)
        if session_id and os.path.exists(cp):
            sys.exit(0)

        cfg = load_config()
        ranks = cfg.get("tier_rank") or {}
        floors = cfg.get("floors") or []

        task = launched_task(transcript)
        bound = None
        if task:
            low = task.lower()
            for floor in floors:
                names = [str(n).lower() for n in (floor.get("match") or [])]
                if low in names or low == str(floor.get("name", "")).lower():
                    bound = floor
                    break

        if not bound:
            try:
                os.makedirs(CACHE_DIR, exist_ok=True)
                with open(cp, "w") as fh:
                    fh.write("unbound\n")
            except Exception:
                pass
            sys.exit(0)

        model = newest_model(transcript)
        name, rank = tier_of(model, ranks)
        floor_name = bound.get("min_tier") or "opus"
        floor_rank = ranks.get(floor_name, 3)

        # Unknown model string: do NOT block. A gate that cannot read the fact
        # it gates on must not guess, and blocking a whole run on an unparsed
        # string would be the expensive direction to be wrong in.
        if rank == 0:
            log(f"ALLOW(unknown-model) run={bound.get('name')} model={model!r}")
            sys.exit(0)

        if rank >= floor_rank:
            sys.exit(0)

        log(f"DENY run={bound.get('name')} model={model} tier={name} floor={floor_name}")
        deny(
            f"MODEL FLOOR — THIS RUN LANDED ON {name.upper()} AND REQUIRES {floor_name.upper()} OR BETTER.\n\n"
            f"Run: {bound.get('name')}\n"
            f"Model actually running: {model}\n"
            f"Floor: {floor_name} (taught by {bound.get('taught') or 'Joe'})\n\n"
            f"WHY: {bound.get('reason') or 'output quality is the point of this run.'}\n\n"
            "STOP NOW. Draft nothing, publish nothing, file no loop. Tell Joe the run landed on "
            f"{name} and that the app model needs to be switched to {floor_name.capitalize()}, then either wait "
            "for him to switch it or skip this run.\n\n"
            "You are being TOLD this rather than asked to check it: the model name above was read from the "
            "harness's own transcript record, not from the run's self-assessment, because a model introspecting "
            "its own identity is the least reliable way to learn the one fact this floor depends on. A session "
            "cannot switch its own model, so there is nothing to retry here."
        )
    except Exception as exc:
        log(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
