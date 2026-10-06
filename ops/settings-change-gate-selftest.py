#!/usr/bin/env python3
"""
settings-change-gate-selftest.py — the acceptance test for the settings-change
gate, written before the gate (rule e65efc68).

THE INCIDENT IT COMES FROM, 2026-08-14. A session was commissioned to fix main's
merge treadmill. Its instructions were explicit: "this likely changes repo
settings, so present the finished configuration to Joe with what changed and
why." It picked a remedy Joe had listed himself, turned off the branch ruleset's
"require branches to be up to date" flag — and was INTERRUPTED before it
delivered the report.

The change reached the repository. The reason reached nothing: no commit, no
loop, no decision entry. Eight hours later another session found the setting
missing, could not tell an authorised change from silent tampering, and spent
real time reconstructing authorship from ruleset version history, a shell
history mtime, and a transcript search.

THE CRUX, and it is not "sessions should write things down". That session was
TOLD to write it down and would have. The record died because it depended on the
session SURVIVING TO THE END. So the record must be taken at the moment of the
change, by the same mechanism that performs it, or it is not a record — it is an
intention.

WHAT THE GATE MUST DO, one assertion per line of that:

  1. RECOGNISE a settings-mutating command. GitHub rulesets, Actions variables
     and secrets, branch protection, repo config; launchd load/unload/submit;
     git config writes; Worker secrets. All four of today's invisible changes
     are in that list because all four happened today.

  2. IGNORE reads. `gh api` without a write method, `git config --get`,
     `launchctl list` — a gate that fires on reads gets muted within a day.

  3. ANNOUNCE a settings write that carries no reason, and say how to give
     one — WR-000019 slice S3 replaced the fail-closed refusal with this: the
     change is always ALLOWED, and the reason's durable home moved to the
     PR-description convention CODEOWNERS and CI now enforce.

  4. ALLOW it silently when a reason is supplied inline, so compliance costs
     one prefix rather than a ritual.

  5. RECORD it AFTER it runs, with the outcome — a change that failed and a
     change that succeeded are different facts, and only the post hook knows
     which happened.

  6. NEVER BLOCK ON THE RECORDER. If the record layer is unreachable the change
     still stands; the gate degrades to a local spool and says so. A gate that
     can strand a partner mid-incident because a database is down would be
     removed within a week, and rightly.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

GATE = Path(__file__).resolve().parent.parent / "hooks" / "settings-change-gate.py"

PASSED = 0
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def run(command: str, event: str = "PreToolUse", env_extra: dict | None = None,
        exit_code: int = 0, reason: str | None = None) -> tuple[int, str]:
    """Drive the hook the way Claude Code drives it: JSON on stdin.

    `reason` is prepended to the COMMAND, not exported into the hook's
    environment, because that is the only path production has. The first version
    of this file put it in the environment and every assertion passed against a
    path the real gate never takes — the gate then refused every settings change
    on this machine with no way to satisfy it."""
    if reason is not None:
        command = f'CARR_CHANGE_REASON="{reason}" ' + command
    payload = {
        "hook_event_name": event,
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "session_id": "selftest-session",
    }
    if event == "PostToolUse":
        payload["tool_response"] = {"exit_code": exit_code, "stdout": "", "stderr": ""}
    env = dict(os.environ)
    env.update(env_extra or {})
    env["CARR_SETTINGS_GATE_SELFTEST"] = "1"
    proc = subprocess.run(
        [sys.executable, str(GATE)],
        input=json.dumps(payload), capture_output=True, text=True, env=env)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


WRITES = [
    ('gh api -X PATCH repos/jbookout/carr-system/rulesets/20824501 -f x=y', "a ruleset edit"),
    ('gh api --method PUT repos/jbookout/carr-system/branches/main/protection', "branch protection"),
    ('gh api -X POST repos/jbookout/carr-system/actions/variables -f name=X -f value=1',
     "an Actions variable"),
    ('gh api -X DELETE repos/jbookout/carr-system/actions/variables/CARR_AUTOMERGE_PILOT_ENABLED',
     "deleting an Actions variable"),
    ('gh secret set NEON_API_KEY', "a repo secret"),
    ('gh variable set CARR_AUTOMERGE_PILOT_ENABLED --body true', "a repo variable"),
    ('gh repo edit --enable-auto-merge', "a repo setting"),
    ('git config --local core.bare true', "a git config write"),
    ('launchctl unload ~/Library/LaunchAgents/com.carr.nightly-record-layer.plist',
     "unloading a scheduled job"),
    ('launchctl submit -l com.carr.quill-key-monitor -- /private/tmp/x', "submitting a job"),
    ('npx wrangler secret put DATABASE_URL_WRITER', "a Worker secret"),
]

READS = [
    'gh api repos/jbookout/carr-system/rulesets/20824501',
    'gh api repos/jbookout/carr-system/actions/variables/CARR_AUTOMERGE_PILOT_ENABLED',
    'gh pr list --state open',
    'git config --get core.bare',
    'git config --local --list',
    'launchctl list | grep carr',
    'gh run view 123 --log',
    'python3 ops/action-risk-registry.py',
]


def main() -> int:
    if not GATE.exists():
        print(f"settings-change-gate-selftest: {GATE} does not exist yet")
        return 1

    print("1. it recognises a settings write and announces one with no reason")
    for command, what in WRITES:
        rc, out = run(command)
        check(f"allows (announced) {what}", rc == 0, f"rc={rc}")
    rc, out = run(WRITES[0][0])
    check("the announcement says how to supply a reason",
          "CARR_CHANGE_REASON" in out, out[:120])
    check("the announcement names the PR-description convention",
          "PR" in out, out[:200])

    print("\n2. it ignores reads")
    for command in READS:
        rc, out = run(command)
        check(f"allows: {command[:52]}", rc == 0, f"rc={rc} {out[:80]}")

    print("\n3. a reason IN THE COMMAND lets the change through")
    for command, what in WRITES[:4]:
        rc, out = run(command, reason="because Joe asked for it")
        check(f"allows {what} with an inline reason", rc == 0, f"rc={rc} {out[:90]}")
    rc, out = run(WRITES[0][0], env_extra={"CARR_CHANGE_REASON": "an exported reason works too"})
    check("an exported reason still works as a fallback", rc == 0, f"rc={rc}")

    print("\n3b. a MENTION is not an invocation")
    mentions = [
        ('python3 - <<EOF\nprint("gh api -X DELETE repos/o/r/actions/variables/Z")\nEOF',
         "a heredoc quoting a settings call"),
        ('echo "run gh api -X PATCH repos/o/r/rulesets/1 later"', "a quoted settings call"),
        ('grep -rn "launchctl unload" ops/', "a grep for one"),
    ]
    for command, what in mentions:
        rc, out = run(command)
        check(f"allows {what}", rc == 0, f"rc={rc}")

    print("\n4. an empty or throwaway reason is not a reason")
    for bogus in ("", "   ", "x", "test"):
        rc, out = run(WRITES[0][0], reason=bogus)
        check(f"allows (announced) reason {bogus!r}", rc == 0, f"rc={rc}")

    print("\n5. the post hook records the outcome, and distinguishes them")
    with tempfile.TemporaryDirectory() as tmp:
        spool = os.path.join(tmp, "settings-changes.jsonl")
        env = {"CARR_CHANGE_REASON": "because Joe asked",
               "CARR_SETTINGS_SPOOL": spool,
               "CARR_SETTINGS_GATE_OFFLINE": "1"}
        run(WRITES[0][0], event="PostToolUse", env_extra=env, exit_code=0)
        run(WRITES[1][0], event="PostToolUse", env_extra=env, exit_code=1)
        rows = [json.loads(line) for line in open(spool)] if os.path.exists(spool) else []
        check("both changes were recorded", len(rows) == 2, f"{len(rows)} row(s)")
        if len(rows) == 2:
            check("the successful one is marked applied", rows[0].get("outcome") == "applied")
            check("the failed one is marked failed", rows[1].get("outcome") == "failed")
            check("the reason is carried", rows[0].get("reason") == "because Joe asked")
            check("the session is identified", rows[0].get("session_id") == "selftest-session")
            check("the target is named", "rulesets" in (rows[0].get("target") or ""))
            check("no secret VALUE is stored",
                  "NEON_API_KEY" not in json.dumps(rows) or
                  all("put " not in json.dumps(r) for r in rows))

    print("\n6. a recorder failure never blocks the change")
    rc, out = run(WRITES[0][0], event="PostToolUse",
                  env_extra={"CARR_CHANGE_REASON": "because Joe asked",
                             "CARR_SETTINGS_SPOOL": "/nonexistent-dir/nope.jsonl",
                             "CARR_SETTINGS_GATE_OFFLINE": "1"}, exit_code=0)
    check("post hook exits 0 even when it cannot record", rc == 0, f"rc={rc}")

    print(f"\nsettings-change-gate-selftest: {PASSED}/{PASSED + len(FAILURES)} passed")
    if FAILURES:
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    return 0



# Independently reproduced Dot cases share the offline behavioral fixtures.
import runpy as _dot_runpy
_dot_runpy.run_path(str(__import__("pathlib").Path(__file__).with_name("dot-review-selftest.py")))["run_regressions"](['test_b07'])

if __name__ == "__main__":
    sys.exit(main())
