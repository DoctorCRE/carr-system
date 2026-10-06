#!/usr/bin/env python3
"""
unread-artifact-gate-selftest.py — acceptance test for hooks/unread-artifact-gate.py,
written before the gate (rule e65efc68).

WHY IT EXISTS. On 2026-08-14 one session filed four defects against itself, and
every one had the same shape underneath: a confident, well-formed claim about an
artifact the session had never opened. The evidence was always a PROXY —

  · a grep hit. "tools/health-check.py writes the radar digest" came from a
    filename match. The file only WATCHES it, and the ten lines around the hit
    said so. That wrong name shipped into the one message whose entire job is
    telling a job's author which job to fix.
  · a rendering. "the write landed on a tombstone" cited three read surfaces as
    three confirmations. They are one collection, and the thing under suspicion
    was how records render. One SQL read settled it the other way.
  · a header, unread. Five files were described as holding content that needed
    routing through verbs. Each says "GENERATED" in its own first line.
  · an imported function. A ruling was called drift while the twenty lines above
    the function that produced the verdict explained why the verdict had changed.

EVERY EXISTING GATE MISSED ALL FOUR, and not by accident. They check claims
against RULES and DECISIONS. These statements broke no rule and contradicted no
ruling. They were simply false, and false in a way that reads as authoritative.

THE ONE MECHANICAL SIGNAL THEY SHARE: the reply asserts what a file DOES, and
this session never read that file. The transcript records every tool call, so
that is a checkable question rather than a judgement.

GREP IS NOT A READ, and that is the sharp edge of this gate. A grep hit proves a
string occurs; it says nothing about whether the line is a write, a watch, a
comment, or a test fixture. Treating it as knowledge is precisely the failure
above, so a path known only through grep counts as UNREAD here.

WHAT IT MUST GET RIGHT:

  1. A BEHAVIOURAL CLAIM, not a mention. "Committed hooks/foo.py" asserts
     nothing about foo.py. "hooks/foo.py writes the digest" does. Only the
     second is this gate's business, or it fires on every status report and gets
     muted.
  2. AUTHORSHIP COUNTS AS KNOWLEDGE. A file this session Wrote or Edited is one
     it knows. Flagging those would make every build turn noisy.
  3. FIRE ONCE. A Stop hook that blocks the same reply forever is a session that
     cannot end.
  4. FAIL OPEN. No transcript, an unreadable line, a parse error: none may
     strand a turn.

Hermetic: throwaway transcripts, no repo state.
"""

import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
GATE = os.path.join(REPO, "hooks", "unread-artifact-gate.py")

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "lib"))
from selftest_harness import Checker  # noqa: E402

CHECKER = Checker()
failures = CHECKER.failures
check = CHECKER.check


def transcript(tmp, tool_calls, assistant_text, name=None):
    """A session: a list of (tool_name, input dict), then the final reply."""
    path = os.path.join(tmp, name or f"t{abs(hash(assistant_text)) % 10**8}.jsonl")
    with open(path, "w") as fh:
        fh.write(json.dumps({"type": "user", "message": {"content": "go"}}) + "\n")
        for tool, ti in tool_calls:
            fh.write(json.dumps({
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "name": tool,
                                         "input": ti}]},
            }) + "\n")
        fh.write(json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": assistant_text}]},
        }) + "\n")
    return path


def spoken(stdout):
    """What the gate SAID, or "" for silence.

    DEMOTED 2026-08-23 (the gates-audit council's Stop-gate rationing). This
    gate used to exit 2, which reopens the turn; it now announces into context
    instead. "Caught" therefore means an announcement was emitted, and an exit 2
    is itself the regression. A payload carrying `decision` would be a block
    wearing an announce's clothes and reads here as silence, deliberately, so
    the shape cannot regress quietly.
    """
    try:
        emitted = json.loads(stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return ""
    if "decision" in emitted:
        return ""
    return (emitted.get("hookSpecificOutput") or {}).get("additionalContext") or ""


def run(tmp, path, state=None, stop_active=False, event="Stop", session="s1"):
    payload = json.dumps({"session_id": session, "transcript_path": path,
                          "hook_event_name": event,
                          "stop_hook_active": stop_active})
    env = {**os.environ,
           "CARR_UNREAD_ARTIFACT_STATE": state or os.path.join(tmp, "state"),
           "CARR_STOP_LATCH_STATE": state or os.path.join(tmp, "state")}
    p = subprocess.run([sys.executable, GATE], input=payload, capture_output=True,
                       text=True, env=env)
    # rc is the COST (0 announce, 2 would be a reopened turn); the second value
    # is what it said, in whichever register it reached for.
    return p.returncode, (spoken(p.stdout) + p.stderr)


def main():
    print("unread artifact gate")
    tmp = tempfile.mkdtemp(prefix="unread-artifact-selftest-")
    try:
        CLAIM = ("The digest is produced upstream. `tools/health-check.py` writes "
                 "the radar digest every Monday, so re-pointing it is the cheapest "
                 "of the three jobs to move into the exporter.")

        # 1. THE FAILURE THIS EXISTS FOR — the real one, from 2026-08-14.
        #    The session grepped the file and asserted what it does.
        t = transcript(tmp, [
            ("Bash", {"command": "grep -rn 'radar-digest' tools/ | head"}),
        ], CLAIM)
        rc, err = run(tmp, t)
        check("a behavioural claim about a GREPPED-only file IS ANNOUNCED",
              bool(err), f"exit {rc}, said nothing")
        check("...and it does not reopen the turn to say so", rc == 0, f"exit {rc}")
        check("the block names the file", "health-check.py" in err, err[:200])
        check("the block says grep is not a read",
              "grep" in err.lower(), err[:250])

        # 2. Reading it clears the claim. Same words, a Read in the transcript.
        t2 = transcript(tmp, [
            ("Bash", {"command": "grep -rn 'radar-digest' tools/ | head"}),
            ("Read", {"file_path": os.path.join(REPO, "tools/health-check.py")}),
        ], CLAIM, name="read.jsonl")
        rc2, err2 = run(tmp, t2, state=os.path.join(tmp, "s2"))
        check("the same claim is ALLOWED once the file was actually read",
              rc2 == 0, f"exit {rc2}: {err2[:160]}")

        # 3. Authorship is knowledge — a file this session wrote is one it knows.
        t3 = transcript(tmp, [
            ("Write", {"file_path": os.path.join(REPO, "tools/health-check.py"),
                       "content": "x"}),
        ], CLAIM, name="wrote.jsonl")
        rc3, _ = run(tmp, t3, state=os.path.join(tmp, "s3"))
        check("a file this session WROTE is not flagged", rc3 == 0, f"exit {rc3}")

        # 4. A cat/sed read through Bash counts too.
        for label, cmd in [("cat", "cat tools/health-check.py | head -40"),
                           ("sed -n", "sed -n '180,214p' tools/health-check.py")]:
            t4 = transcript(tmp, [("Bash", {"command": cmd})], CLAIM,
                            name=f"{label.replace(' ','')}.jsonl")
            rc4, _ = run(tmp, t4, state=os.path.join(tmp, f"s4{label[:3]}"))
            check(f"a Bash read via {label} counts as reading", rc4 == 0,
                  f"exit {rc4}")

        # 5. A MENTION IS NOT A CLAIM. This is the case that decides whether the
        #    gate is liveable: status reports name paths constantly.
        for label, text in [
            ("a shipping report",
             "Committed `hooks/one-repo-gate.py` and `ops/one-repo-gate-selftest.py`, "
             "pushed, and the pull request is open with automerge armed."),
            ("a file list",
             "Changed: `exporters/targets.py`, `hooks/md_manifest.py`, "
             "`ops/config/gate-baseline.json`. 47 cases green."),
        ]:
            t5 = transcript(tmp, [], text, name=f"m{abs(hash(label))%10**6}.jsonl")
            rc5, err5 = run(tmp, t5, state=os.path.join(tmp, f"s5{abs(hash(label))%999}"))
            check(f"allowed: {label} — naming a path is not asserting what it does",
                  rc5 == 0 and err5.strip() == "", f"exit {rc5}: {err5[:140]}")

        # 6. FIRE ONCE, or the session cannot end.
        rc6a, a6a = run(tmp, t, state=os.path.join(tmp, "s6"))
        rc6b, a6b = run(tmp, t, state=os.path.join(tmp, "s6"))
        check("first pass speaks", bool(a6a), f"exit {rc6a}, said nothing")
        check("the SAME claim a second time is silent", not a6b,
              f"exit {rc6b}: {a6b[:140]} — this gate would nag every turn")

        # 6b. THE LATCH IS ON THE CLAIM-SET, NOT ON THE WORDING, and that is the
        # defect the 2026-08-23 council found one gate over: drift-assertion
        # keyed its "speak once" memory on a sha256 of the exact prose, so a
        # single changed word minted a fresh identity for an identical finding
        # and held the same reading twice. Same claim, different sentence: silent.
        reworded = transcript(tmp, [
            ("Bash", {"command": "grep -rn 'radar-digest' tools/ | head"}),
        ], "Re-pointing is cheapest because `tools/health-check.py` writes the "
           "radar digest on a weekly cadence, upstream of the exporter.",
           name="reworded.jsonl")
        rc6c, a6c = run(tmp, reworded, state=os.path.join(tmp, "s6"))
        check("a REWORDED restatement of the same unread file is silent",
              not a6c, f"exit {rc6c}: {a6c[:140]}")

        # ...but a DIFFERENT unread file is a different finding and must speak.
        another = transcript(tmp, [
            ("Bash", {"command": "grep -rn 'digest' exporters/ | head"}),
        ], "The scheduling lives further up: `exporters/targets.py` writes the "
           "weekly digest rows before anything renders them.",
           name="another.jsonl")
        rc6d, a6d = run(tmp, another, state=os.path.join(tmp, "s6"))
        check("a DIFFERENT unread file still speaks", bool(a6d),
              f"exit {rc6d}, said nothing — the latch became a mute")

        # ...and another SESSION hears it, because latch state is per session.
        rc6e, a6e = run(tmp, t, state=os.path.join(tmp, "s6"), session="other")
        check("a second session is not silenced by the first", bool(a6e),
              f"exit {rc6e}, said nothing")

        # 7. FAIL OPEN, every path.
        rc7, _ = run(tmp, t, state=os.path.join(tmp, "s7"), stop_active=True)
        check("stop_hook_active is respected", rc7 == 0, f"exit {rc7}")
        rc8, _ = run(tmp, os.path.join(tmp, "missing.jsonl"),
                     state=os.path.join(tmp, "s8"))
        check("a missing transcript fails OPEN", rc8 == 0, f"exit {rc8}")
        rc9, _ = run(tmp, t, state=os.path.join(tmp, "s9"), event="PreToolUse")
        check("a non-Stop event is ignored", rc9 == 0, f"exit {rc9}")
        p10 = subprocess.run([sys.executable, GATE], input="{not json",
                             capture_output=True, text=True)
        check("a malformed payload fails OPEN", p10.returncode == 0,
              f"exit {p10.returncode}")
    finally:
        __import__("shutil").rmtree(tmp)

    return CHECKER.summary()



# Independently reproduced Dot cases share the offline behavioral fixtures.
import runpy as _dot_runpy
_dot_runpy.run_path(str(__import__("pathlib").Path(__file__).with_name("dot-review-selftest.py")))["run_regressions"](['test_b23'])

if __name__ == "__main__":
    raise SystemExit(main())
