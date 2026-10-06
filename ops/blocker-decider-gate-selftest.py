#!/usr/bin/env python3
"""blocker-decider-gate-selftest.py — fixtures for hooks/blocker-decider-gate.py,
written before the hook (rule e65efc68, enforcing rule 88e9b5eb).

THE RULE. "Not authorized" and "not possible" must never be reported as the
same finding. A loop that says blocker='capability' is claiming this session
cannot hold a credential, gate, or verb — and that claim has two completely
different readings: someone COULD grant it (then the row must say who, or the
grant never happens and the loop rots), or nobody can (then the row must say
that plainly, so it reads as a limit rather than a request). A bare "not
authorized" hides which one it is, and the difference is exactly what the
reader needs.

WHAT THE HOOK MUST DO:
  - DENY an add-loop with blocker='capability' whose text names no decider
    (Joe or Dell) and does not declare the capability genuinely impossible.
  - ALLOW the same row once a decider is named anywhere in its text.
  - ALLOW a row that states the capability does not exist (nobody to ask).
  - IGNORE every other blocker class, every other verb, and malformed input
    (fails open — a wedged filing is worse than a vague one).

Spawns the REAL hook with REAL payloads; exit 2 = denied, 0 = allowed.
"""
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "blocker-decider-gate.py")

# (name, tool_name, tool_input, expect_deny)
CASES = [
    ("bare-capability-denied", "mcp__carr__add-loop",
     {"kind": "open_loop", "owner": "Joe", "blocker": "capability",
      "blocker_detail": "needs the deploy token", "body": "ship the exporter"},
     True),
    ("not-authorized-denied", "mcp__carr__add-loop",
     {"kind": "open_loop", "owner": "Joe", "blocker": "capability",
      "blocker_detail": "session is not authorized to run wrangler deploys",
      "body": "deploy the worker"},
     True),
    ("decider-named-allowed", "mcp__carr__add-loop",
     {"kind": "open_loop", "owner": "Joe", "blocker": "capability",
      "blocker_detail": "needs the NEON_API_KEY only Joe holds — Joe grants it",
      "body": "rotate the key"},
     False),
    ("decider-in-body-allowed", "mcp__carr__add-loop",
     {"kind": "open_loop", "owner": "Joe", "blocker": "capability",
      "blocker_detail": "wrangler deploy permission",
      "body": "Dell can grant the Cloudflare seat when he is back Tuesday"},
     False),
    ("impossible-stated-allowed", "mcp__carr__add-loop",
     {"kind": "open_loop", "owner": "Joe", "blocker": "capability",
      "blocker_detail": "the free GitHub plan has no branch-protection API at "
                        "all — genuinely impossible on this plan, nobody can "
                        "grant it", "body": "server-side protection"},
     False),
    ("other-blocker-ignored", "mcp__carr__add-loop",
     {"kind": "open_loop", "owner": "Joe", "blocker": "counterparty",
      "blocker_detail": "Sanders, the listing broker on C-112",
      "body": "wait for the counter"},
     False),
    ("no-blocker-ignored", "mcp__carr__add-loop",
     {"kind": "team_loop", "owner": "Joe→Dell", "title": "handoff",
      "body": "review the render"},
     False),
    ("other-verb-ignored", "mcp__carr__update-loop",
     {"ref": "250", "body": "capability text that would otherwise trip"},
     False),
    ("malformed-input-open", "mcp__carr__add-loop",
     "not a dict",
     False),
]

passed = 0
bad: list[str] = []


def spawn(tool_name, tool_input):
    payload = {"tool_name": tool_name, "session_id": "selftest",
               "tool_input": tool_input}
    p = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=30)
    return p.returncode == 2, (p.stdout or "") + (p.stderr or "")


def main():
    global passed
    if not os.path.exists(HOOK):
        print(f"FAIL: hook not found at {HOOK}")
        return 1
    for name, tool, ti, expect in CASES:
        got, out = spawn(tool, ti)
        ok = got == expect
        passed_or = "ok  " if ok else "FAIL"
        if not ok:
            bad.append(name)
        else:
            passed += 1
        print(f"  {passed_or} {name:28} want={'DENY ' if expect else 'allow'} "
              f"got={'DENY' if got else 'allow'}")

    # THE SECOND CONCERN this gate hosts: the defect-class advisory. These
    # cases never assert what the ranking SAYS — that is measured against
    # held-out data, not asserted in a gate suite. They assert the only
    # properties the host relationship depends on: that it cannot deny, and
    # that it cannot wedge a session when the judgment is unreachable. Both are
    # checked with no credential in the environment, which is also the state a
    # scheduled run sees.
    advisory_env = {k: v for k, v in os.environ.items()
                    if k not in ("TYPESAFE_API_KEY",)}
    for name, ti in (
        ("advisory-never-denies",
         {"claimed": "the library was finished", "actual": "nothing ever called it"}),
        ("advisory-tolerates-missing-fields", {"claimed": "only half a defect"}),
        ("advisory-tolerates-junk", {"claimed": None, "actual": ["not", "a", "string"]}),
    ):
        proc = subprocess.run(
            [sys.executable, HOOK],
            input=json.dumps({"tool_name": "mcp__carr__record-defect",
                              "session_id": "selftest", "tool_input": ti}),
            capture_output=True, text=True, timeout=60, env=advisory_env)
        if proc.returncode == 0:
            passed += 1
            print(f"  ok   {name:28} want=allow got=allow")
        else:
            bad.append(name)
            print(f"  FAIL {name:28} exit={proc.returncode} {proc.stderr[:100]}")

    # A tool this gate is not registered for must fall straight through, or the
    # advisory becomes a judgment call on every MCP write in the system.
    proc = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps({"tool_name": "mcp__carr__update-deal", "session_id": "selftest",
                          "tool_input": {"claimed": "x", "actual": "y"}}),
        capture_output=True, text=True, timeout=30, env=advisory_env)
    if proc.returncode == 0 and not proc.stdout.strip():
        passed += 1
        print("  ok   advisory-ignores-other-verbs  want=silent got=silent")
    else:
        bad.append("advisory-ignores-other-verbs")
        print(f"  FAIL advisory-ignores-other-verbs — {proc.stdout[:120]}")

    got, out = spawn("mcp__carr__add-loop", CASES[0][2])
    if "decider" in out.lower() and ("joe" in out.lower() or "dell" in out.lower()):
        passed += 1
        print("  ok   the refusal says how to comply")
    else:
        bad.append("refusal-text")
        print(f"  FAIL the refusal says how to comply — {out[:120]}")

    print(f"\nblocker-decider-gate-selftest: {passed}/{passed + len(bad)} passed")
    if bad:
        print("FAILURES: " + ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
