#!/usr/bin/env python3
"""Contract test for the Call Mode LaunchAgent's Python interpreter.

Bug (verified 2026-09-23 on the Mac Studio): com.carr.call-mode.plist ran
under /usr/bin/python3, the Command Line Tools shim. A LaunchAgent loaded from
~/Library/LaunchAgents gets its Accessibility (TCC) responsibility attributed
to ProgramArguments[0], and the shim cannot usefully be granted Accessibility,
so System Events clicks on Quill's menu failed with -25211. The tracked plist
now names the real interpreter directly. This test pins that, and on macOS
also checks the path exists and is executable.
"""
from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLIST = REPO / "tools" / "dictation-rig" / "launchd" / "com.carr.call-mode.plist"
REAL = "/Library/Developer/CommandLineTools/usr/bin/python3"


def main() -> int:
    failures: list[str] = []

    def check(label: str, ok: bool) -> None:
        print(("  ok    " if ok else "  FAIL  ") + label)
        if not ok:
            failures.append(label)

    text = PLIST.read_text(encoding="utf-8")
    args = plistlib.loads(
        text.replace("{{REPO}}", "/tmp/repo").replace("{{HOME}}", "/tmp/home").encode("utf-8")
    ).get("ProgramArguments", [])
    check("plist no longer runs under the /usr/bin/python3 shim", "/usr/bin/python3" not in args)
    check("ProgramArguments[0] is the real Command Line Tools interpreter", bool(args) and args[0] == REAL)
    check("the call-mode script is still the program's first argument",
          len(args) > 1 and args[1].endswith("tools/dictation-rig/bin/call-mode.py"))
    if sys.platform == "darwin":
        check("that interpreter exists and is executable on this Mac",
              os.path.isfile(REAL) and os.access(REAL, os.X_OK))
    else:
        print("  skip  interpreter existence (not macOS)")
    if failures:
        print(f"call-mode-python-launchd-selftest: {len(failures)} failure(s)")
        return 1
    print("call-mode-python-launchd-selftest: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
