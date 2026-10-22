#!/usr/bin/env python3
# doctrine: change-scoped-gates
"""The binding class vetoes only the drift THIS branch touched.

Every case below is a state the machine was actually in on 2026-08-22, not an
invented one. The wedge that produced this file: a one-word comment repair to a
launchd file made two unrelated scheduled tasks another session had installed
into that branch's problem, while WITHOUT the repair the gates class failed on
the unparsable file. Both halves blocked at once and the only remaining move was
to skip every check.

The rule being pinned is the one ops/ci.sh's own comment already stated and its
code did not implement: a machine-global condition may open a loop, never veto
unrelated work.
"""
from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "config_drift_ownership", ROOT / "ops" / "config_drift_ownership.py")
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

PASSED = 0
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  ok    {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL  {name}" + (f"\n        {detail}" if detail else ""))


# The exact shape ops/config-as-code.py prints.
REPORT = """config-as-code: DRIFT — 2 of 49 items
  scheduled-task handoff-continuation-20260822-1310
      on disk: present; in repo: NOT TRACKED
  scheduled-task notes-sweep-hourly
      TRACKED BUT DIFFERENT from the live copy

  `ops/config-as-code.py pull` to capture the machine into the repo.
"""

LAUNCHD_REPORT = """config-as-code: DRIFT — 1 of 49 items
  launchd com.carr.calendar-prebrief-joe.plist
      TRACKED BUT DIFFERENT from the live copy
"""

# ── THE WEDGE ITSELF ─────────────────────────────────────────────────────────
check("a launchd repair does not inherit somebody else's scheduled-task drift",
      mod.owned(["ops/launchd/com.carr.room-bridge.plist"], REPORT) == [],
      "this is the exact 2026-08-22 wedge: the branch could not fix that drift "
      "without committing another session's in-flight work")

# ── WHAT MUST STILL FAIL, or the narrowing is a hole ─────────────────────────
check("a branch touching the drifting launchd file still owns it",
      mod.owned(["ops/launchd/com.carr.calendar-prebrief-joe.plist"], LAUNCHD_REPORT)
      == ["launchd com.carr.calendar-prebrief-joe.plist"])

check("a branch touching the drifting scheduled task still owns it",
      mod.owned(["ops/scheduled-tasks/notes-sweep-hourly.json"], REPORT)
      == ["scheduled-task notes-sweep-hourly"])

check("changing the reconciler itself owns every drifting item",
      len(mod.owned(["ops/config-as-code.py"], REPORT)) == 2,
      "a change to the thing that reconciles is in this business by definition")

check("changing the settings declaration owns every drifting item",
      len(mod.owned(["ops/config/settings.json"], REPORT)) == 2)

# ── SHAPE CASES THAT WOULD MAKE IT SILENTLY USELESS ──────────────────────────
check("a branch touching no declaration owns nothing",
      mod.owned(["db/schema.sql", "mcp-server/src/tools.js"], REPORT) == [])

check("an unrecognised drift family is OWNED, never silently dropped",
      mod.owned(["ops/launchd/x.plist"],
                "  hostsfile something-new\n      TRACKED BUT DIFFERENT")
      == ["hostsfile something-new"],
      "a new drift kind must not stop being anybody's problem by default")

check("a clean report yields nothing to own",
      mod.owned(["ops/launchd/com.carr.room-bridge.plist"],
                "config-as-code: 49 of 49 items match") == [])

check("the plist suffix is not required to match on either side",
      mod.owned(["ops/launchd/com.carr.calendar-prebrief-joe.plist"],
                "  launchd com.carr.calendar-prebrief-joe\n      DIFFERENT")
      == ["launchd com.carr.calendar-prebrief-joe"])

# ── ONE CODE PATH, TWO CALLERS ───────────────────────────────────────────────
# ops/ci.sh must ask THIS module rather than carry its own copy of the rule.
CI = (ROOT / "ops" / "ci.sh").read_text(encoding="utf-8")
check("ops/ci.sh asks this module instead of matching paths itself",
      "config_drift_ownership.py" in CI,
      "a second copy of the rule in shell would be free to drift from the tested one")
check("ops/ci.sh no longer treats any declaration path as owning all drift",
      "ops/scheduled-tasks/|ops/config-as-code" not in CI,
      "the old directory-wide test is still there, so the narrowing did not take effect")

# ── THE EXIT CONTRACT THE SHELL DEPENDS ON ───────────────────────────────────
proc = subprocess.run([sys.executable, str(ROOT / "ops" / "config_drift_ownership.py"),
                       "ops/launchd/com.carr.room-bridge.plist"],
                      input=REPORT, capture_output=True, text=True, timeout=60)
check("exit 0 when the branch owns nothing", proc.returncode == 0,
      f"rc={proc.returncode} out={proc.stdout!r}")

proc = subprocess.run([sys.executable, str(ROOT / "ops" / "config_drift_ownership.py"),
                       "ops/scheduled-tasks/notes-sweep-hourly.json"],
                      input=REPORT, capture_output=True, text=True, timeout=60)
check("exit 1 and name the item when the branch owns one",
      proc.returncode == 1 and "notes-sweep-hourly" in proc.stdout,
      f"rc={proc.returncode} out={proc.stdout!r}")

print(f"\nconfig-drift-ownership-selftest: {PASSED}/{PASSED + len(FAILED)} passed")
if FAILED:
    print("FAILURES: " + ", ".join(FAILED))
    raise SystemExit(1)
