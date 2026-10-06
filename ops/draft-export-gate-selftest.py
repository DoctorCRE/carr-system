#!/usr/bin/env python3
"""draft-export-gate-selftest.py — fixtures for hooks/draft-export-gate.py,
written before the hook (rule e65efc68).

THE FAILURE THIS COMES FROM, 2026-08-15, defect 280b1b6c. Asked what was left on
Program 4, a session grepped out/exports/decision-history.md for "program 4",
found one unrelated line, and told Joe that a whole bullet had never been scoped.
It had been scoped four hours earlier. The draft file was THIRTY-TWO HOURS STALE
(2026-08-13 15:16) while the live vault render was five hours old, and the
determination the session missed was sitting in the live one.

WHY THE EXISTING BANNER DID NOT HELP. Every export already carries a header:
"GENERATED from the CARR record layer — do not hand-edit." That guards the WRONG
HAZARD. It protects the file from being written and says nothing to the reader
about the file being a stale draft. A `grep -c` never prints it anyway.

WHY out/exports/ IS THE TRAP SPECIFICALLY. It sits inside the repo, so it is the
natural thing a session reaches for, and it is gitignored, so nothing about it
travels or gets reviewed. `run.sh export` writes there as a DRAFT by default and
only CARR_EXPORT_LIVE=1 reaches the vault, which means the draft copy can be
arbitrarily old and never announces it. CLAUDE.md states this and three separate
tools carry a comment about it; none of that reached the moment of reading.

ANNOUNCE, NEVER DENY, and that is forced by evidence rather than caution: real
consumers read this directory on purpose. tools/calendar-touch-matcher.py and
tools/mail-touch-matcher.py both load the roster and registry from it.
A deny would break working code to prevent a reading
mistake.

WHAT MAKES IT ACTIONABLE is the AGE, not the warning. "This is a draft" is a
label; "this file is 32 hours old and the live render is 5 hours old" is the fact
that changes what the session does next.

Spawns the REAL hook with REAL PreToolUse Bash payloads against a REAL temp tree
with controlled mtimes. Exit 0 = allowed (this hook never denies); the assertion
is on whether it SPEAKS and what it says.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "draft-export-gate.py")

# (name, command, expect_speaks, why)
CASES = [
    ("the-2026-08-15-grep",
     'grep -inE "program 4" out/exports/decision-history.md',
     True,
     "the exact command from defect 280b1b6c"),

    ("absolute-path",
     'grep -c foo /Users/booko/carr-system/out/exports/decision-history.md',
     True,
     "the trap does not depend on the path being relative"),

    ("cat-the-draft",
     'cat out/exports/open-loops.md | head -40',
     True,
     "any read of the directory carries the same staleness"),

    ("xlsx-draft-too",
     'python3 -c "import openpyxl; openpyxl.load_workbook(\'out/exports/lead-registry.xlsx\')"',
     True,
     "the roster and registry drafts go stale exactly like the markdown"),

    # ── must stay silent ────────────────────────────────────────────────────
    ("live-vault-render",
     'grep -c "answered by determination" '
     '"$HOME/My Drive/CARR AI/00_Context/decision-history.md"',
     False,
     "reading the LIVE render is the correct behaviour and must not be nagged"),

    ("other-out-subdir",
     'tail -30 out/nightly.log',
     False,
     "out/ holds logs and state that are not draft exports"),

    ("writing-an-export",
     'CARR_EXPORT_LIVE=1 ./run.sh export --only compiled-rules',
     False,
     "a session RUNNING the live export is not misreading a draft"),

    ("unrelated-command",
     'git log --oneline -40',
     False,
     "no mention of the directory at all"),

    ("mentions-in-prose-only",
     'echo "the draft lives in out/exports and is not the record"',
     False,
     "an echo describing the directory is not a read of it — the same "
     "prose-vs-command distinction guard-unattended.py draws"),
]


def run_hook(command, root):
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": command}, "session_id": "selftest"}
    p = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=30,
                       env=dict(os.environ, CARR_DRAFT_EXPORT_ROOT=root))
    spoke = bool((p.stdout or "").strip())
    return spoke, (p.stdout or "") + (p.stderr or ""), p.returncode


def seed(root):
    """A draft dir whose file is deliberately old, and a live render that is new."""
    exports = os.path.join(root, "out", "exports")
    os.makedirs(exports, exist_ok=True)
    for name in ("decision-history.md", "open-loops.md", "lead-registry.xlsx"):
        path = os.path.join(exports, name)
        with open(path, "w") as fh:
            fh.write("# Decision History\n\n> **GENERATED from the CARR record "
                     "layer — do not hand-edit.**\n")
        old = time.time() - 32 * 3600
        os.utime(path, (old, old))
    return exports


def main():
    if not os.path.exists(HOOK):
        print(f"FAIL: hook not found at {HOOK}")
        return 1

    passed, bad = 0, []
    with tempfile.TemporaryDirectory() as root:
        seed(root)

        for name, cmd, expect, why in CASES:
            spoke, out, rc = run_hook(cmd, root)
            ok = (spoke == expect) and rc == 0
            passed += ok
            if not ok:
                bad.append(name)
            print(f"  {'ok  ' if ok else 'FAIL'} {name:24} "
                  f"want={'SPEAK' if expect else 'quiet'} "
                  f"got={'SPEAK' if spoke else 'quiet'} rc={rc}")
            if not ok:
                print(f"       why this case exists: {why}")

        # NEVER DENIES. Real consumers read this directory on purpose.
        spoke, out, rc = run_hook(CASES[0][1], root)
        if rc == 0:
            passed += 1
            print("  ok   allows the read (announce-only, never deny)")
        else:
            bad.append("never-denies")
            print(f"  FAIL announce-only — rc={rc}")

        # THE AGE IS THE POINT. A label without a number changes nothing.
        if "32" in out or "hour" in out.lower() or "day" in out.lower():
            passed += 1
            print("  ok   the message carries the file's actual age")
        else:
            bad.append("reports-age")
            print(f"  FAIL the message must carry the age — {out[:160]}")

        # It has to name where the truth IS, not only where it is not.
        if "store" in out.lower() or "decision" in out.lower():
            passed += 1
            print("  ok   the message names the authoritative source")
        else:
            bad.append("names-the-source")
            print(f"  FAIL name the real source — {out[:160]}")

        # Fails open when the directory is missing entirely.
        spoke, _, rc = run_hook(CASES[0][1], os.path.join(root, "nope"))
        if rc == 0:
            passed += 1
            print("  ok   fails open when the draft dir does not exist")
        else:
            bad.append("fails-open")
            print(f"  FAIL should fail open — rc={rc}")

    print(f"\ndraft-export-gate-selftest: {passed}/{passed + len(bad)} passed")
    if bad:
        print("FAILURES: " + ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
