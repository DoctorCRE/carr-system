#!/usr/bin/env python3
"""run-record-gate-selftest.py — the drift gates, run through the REAL chain.

WHY IT EXISTS. hooks.json does not run drift-claim-gate or drift-assertion-gate
directly. It runs

    /usr/bin/python3 hooks/hook-meter-run.py hooks/run-record-gate.py <gate>

hook-meter-run.py loads the wrapper in-process and reads the hook payload off
fd 0 first, handing the wrapper a replacement sys.stdin. The wrapper then
os.execve()s the gate, and execve keeps fd 0, not sys.stdin. Until 2026-09-24
the gate therefore read a drained pipe:

  * drift-claim-gate logged ALLOW(parse-error) and allowed, on every call in
    production on 2026-09-23 and -24 (26 of 26);
  * drift-assertion-gate, one of the three gates allowed to reopen a turn,
    exited 0 on the parse failure WITHOUT A LOG LINE, so it was dead and silent.

Each gate's own selftest runs the gate directly and passed throughout. This
suite runs the chain hooks.json runs, with fixtures that must make each gate
take its non-allow path:

  * drift-claim-gate on record-defect: the only non-allow verdict it has is an
    ANNOUNCE (a PreToolUse allow carrying additionalContext with the rulings);
  * drift-assertion-gate on Stop: a REOPEN (exit 2, the rulings on stderr).

It fails on the unfixed wrapper and passes with the fix. It also holds the
second half of the fix: drift-assertion-gate now logs ALLOW(parse-error), so a
payload that never arrives can never again be silent.

Hermetic: a throwaway repository holding copies of hooks/ and lib/ and a
.venv/bin/python that points at this interpreter (a hosted runner has no
.venv, and the wrapper refuses ambient Python by design), a throwaway decision
log, transcript, latch state, guard log and telemetry file.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(REPO, "lib"))
from selftest_harness import Checker  # noqa: E402

CHECKER = Checker()
check = CHECKER.check

# What hooks.json names. A machine without it (none expected) uses this one.
HARNESS_PYTHON = "/usr/bin/python3" if os.access("/usr/bin/python3", os.X_OK) else sys.executable

RULING = ("- `2026-08-13` — The quokka-indexer lane was DELIBERATELY disabled by Joe "
          "after the overnight run cost more than it returned; leaving it off is the "
          "chosen state and must not be read as drift.\n")
CLAIM = ("The quokka-indexer lane is no longer running. It was supposed to fire nightly "
         "and the schedule has silently reverted, so the index is stale and nothing has "
         "been re-pointed. I think this regressed when the overnight lane changed.")
PLAIN = ("The quokka-indexer lane finished its nightly run and the index was rebuilt; "
         "the report is attached and every figure in it checks out against the source.")


def sandbox(tmp: str) -> str:
    root = os.path.join(tmp, "repo")
    for part in ("hooks", "lib"):
        shutil.copytree(os.path.join(REPO, part), os.path.join(root, part),
                        ignore=shutil.ignore_patterns("__pycache__"))
    os.makedirs(os.path.join(root, ".venv", "bin"))
    os.symlink(sys.executable, os.path.join(root, ".venv", "bin", "python"))
    os.makedirs(os.path.join(root, "out"))
    return root


def chain(root: str, gate: str, payload: str, env: dict) -> subprocess.CompletedProcess:
    """Exactly the hooks.json command, run from the repository."""
    return subprocess.run(
        [HARNESS_PYTHON, os.path.join(root, "hooks", "hook-meter-run.py"),
         os.path.join(root, "hooks", "run-record-gate.py"), gate],
        input=payload, capture_output=True, text=True, env=env, cwd=root, timeout=60)


def transcript(tmp: str, text: str, name: str) -> str:
    path = os.path.join(tmp, name)
    with open(path, "w") as fh:
        fh.write(json.dumps({"type": "user", "message": {"content": "go"}}) + "\n")
        fh.write(json.dumps({"type": "assistant",
                             "message": {"content": [{"type": "text", "text": text}]}}) + "\n")
    return path


def read(path: str) -> str:
    try:
        with open(path, errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def main() -> int:
    print("run-record-gate: the drift gates through hook-meter-run -> run-record-gate")
    tmp = tempfile.mkdtemp(prefix="run-record-gate-selftest-")
    try:
        root = sandbox(tmp)
        decisions = os.path.join(tmp, "decisions.md")
        with open(decisions, "w") as fh:
            fh.write("# Decision history\n\n" + RULING)
        guard_log = os.path.join(tmp, "hook-guard.log")
        env = {**os.environ,
               "HOME": os.path.join(tmp, "home"),
               "CARR_NONCANONICAL_DECISIONS_PATH": decisions,
               "CARR_DRIFT_ASSERTION_STATE": os.path.join(tmp, "latch"),
               "CARR_HOOK_GUARD_LOG": guard_log,
               "CARR_HOOK_TELEMETRY": os.path.join(tmp, "telemetry.jsonl"),
               "CARR_HOOK_FIXTURE": "1"}
        env.pop("CARR_STOP_LATCH_STATE", None)
        os.makedirs(env["HOME"])

        # drift-claim-gate: a record-defect whose claim a ruling governs.
        defect = json.dumps({
            "session_id": "rrg-selftest-claim", "hook_event_name": "PreToolUse",
            "tool_name": "mcp__carr__record-defect", "cwd": root,
            "tool_input": {"title": "quokka-indexer lane stopped", "claimed": CLAIM,
                           "actual": "the quokka-indexer schedule has silently reverted"}})
        p = chain(root, "drift-claim-gate.py", defect, env)
        log = read(guard_log)
        check("drift-claim-gate through the chain does not log ALLOW(parse-error)",
              "parse-error" not in log, log[-300:])
        context = ""
        try:
            context = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
        except (ValueError, KeyError, TypeError):
            pass
        check("drift-claim-gate through the chain ANNOUNCES the governing ruling",
              p.returncode == 0 and "DRIFT CLAIM" in context and "quokka-indexer" in context,
              f"exit {p.returncode} stdout={p.stdout[:200]!r} stderr={p.stderr[:200]!r}")

        plain_defect = json.dumps({
            "session_id": "rrg-selftest-claim", "hook_event_name": "PreToolUse",
            "tool_name": "mcp__carr__record-defect", "cwd": root,
            "tool_input": {"title": "quokka-indexer report", "claimed": PLAIN}})
        p = chain(root, "drift-claim-gate.py", plain_defect, env)
        check("drift-claim-gate through the chain stays silent on a claim with no drift",
              p.returncode == 0 and "DRIFT CLAIM" not in p.stdout, p.stdout[:200])

        # drift-assertion-gate: a final reply asserting drift that a ruling governs.
        stop = json.dumps({
            "session_id": "rrg-selftest-assert", "hook_event_name": "Stop",
            "stop_hook_active": False, "cwd": root,
            "transcript_path": transcript(tmp, CLAIM, "claim.jsonl")})
        p = chain(root, "drift-assertion-gate.py", stop, env)
        log = read(guard_log)
        check("drift-assertion-gate through the chain REOPENS the turn with the ruling",
              p.returncode == 2 and "DRIFT ASSERTION" in p.stderr and "quokka-indexer" in p.stderr,
              f"exit {p.returncode} stderr={p.stderr[:200]!r}")
        check("drift-assertion-gate through the chain logs its BLOCK, not a parse error",
              "drift-assertion-gate BLOCK" in log and "drift-assertion-gate ALLOW(parse-error)" not in log,
              log[-300:])

        plain_stop = json.dumps({
            "session_id": "rrg-selftest-plain", "hook_event_name": "Stop",
            "stop_hook_active": False, "cwd": root,
            "transcript_path": transcript(tmp, PLAIN, "plain.jsonl")})
        p = chain(root, "drift-assertion-gate.py", plain_stop, env)
        check("drift-assertion-gate through the chain allows a reply with no drift claim",
              p.returncode == 0 and "DRIFT ASSERTION" not in p.stderr, p.stderr[:200])

        # Never silent again: a payload that does not parse is logged.
        before = read(guard_log)
        p = chain(root, "drift-assertion-gate.py", "not json", env)
        added = read(guard_log)[len(before):]
        check("drift-assertion-gate logs ALLOW(parse-error) when the payload does not parse",
              p.returncode == 0 and "drift-assertion-gate ALLOW(parse-error)" in added, added[-300:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return CHECKER.summary()


if __name__ == "__main__":
    sys.exit(main())
