#!/usr/bin/env python3
"""Fixtures for hooks/team-memory-index.py and its wiring (WR562.S10.J1)."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
HOOK = os.path.join(REPO, "hooks", "team-memory-index.py")

spec = importlib.util.spec_from_file_location("team_memory_index", HOOK)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

failures: list[str] = []

DOC = {
    "ok": True,
    "generation": 42,
    "sections": [
        {"section_key": "operations/machine-access", "title": "Mac roles and SSH aliases",
         "status": "active", "body": {"text": "SECRET-BODY-TEXT"}},
        {"section_key": "lessons/untitled", "title": None, "status": "active"},
        {"section_key": "old/retired-topic", "title": "Gone", "status": "retired"},
        {"section_key": "long/title", "title": "x" * 500, "status": "active"},
    ],
}


def emit(fetch):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = mod.main(fetch=fetch)
    return rc, out.getvalue()


# The index names every active section by key and title, and the attach rule.
rc, text = emit(lambda: DOC)
if rc != 0:
    failures.append(f"a good fetch must exit 0, got {rc}")
for needed in ("attach-memory", "write-doctrine-section", "generation 42",
               "operations/machine-access: Mac roles and SSH aliases",
               "lessons/untitled", "3 sections"):
    if needed not in text:
        failures.append(f"index is missing {needed!r}")
if "old/retired-topic" in text:
    failures.append("a retired section must not be listed")
if "SECRET-BODY-TEXT" in text:
    failures.append("section bodies must never be injected, only keys and titles")
if "x" * 200 in text:
    failures.append("long titles must be shortened")

# Many sections stay inside the hook output budget.
big = {"generation": 1, "sections": [
    {"section_key": f"k/{i:04d}-" + "y" * 60, "title": "t" * 200, "status": "active"}
    for i in range(400)]}
_rc, big_text = emit(lambda: big)
if len(big_text) > mod.MAX_CHARS:
    failures.append(f"output exceeds {mod.MAX_CHARS} chars: {len(big_text)}")
if "more sections" not in big_text:
    failures.append("a clipped index must say how many sections were left out")


# Fail open: every failure prints exactly one warning line and exits 0.
def boom():
    raise RuntimeError("store down")


for label, fetch in (
        ("raised", boom),
        ("not ok", lambda: {"ok": False, "error": "x"}),
        ("no sections", lambda: {"ok": True}),
        ("timeout", lambda: (_ for _ in ()).throw(subprocess.TimeoutExpired("x", 1)))):
    rc, text = emit(fetch)
    lines = text.strip().splitlines()
    if rc != 0 or len(lines) != 1 or not lines[0].startswith("CARR HOOK WARNING"):
        failures.append(f"{label}: expected one warning line and exit 0, got rc={rc} {text!r}")

# The real fetcher reads team-memory through the existing verb door.
calls: list[list[str]] = []


def fake_run(args, **kw):
    calls.append(args)
    return subprocess.CompletedProcess(args, 0, stdout=json.dumps(DOC), stderr="banner")


real_run = mod.subprocess.run
mod.subprocess.run = fake_run
try:
    got = mod.fetch()
finally:
    mod.subprocess.run = real_run
if got.get("generation") != 42 or not calls or "read-doctrine" not in calls[0] \
        or not any(a.endswith("tools/call-verb.py") for a in calls[0]):
    failures.append(f"fetch must call tools/call-verb.py read-doctrine; got {calls!r}")

# Wiring: the tracked project settings run the hook at SessionStart and turn
# Claude Code's private auto-memory off.
settings = json.load(open(os.path.join(REPO, ".claude", "settings.json"), encoding="utf-8"))
if settings.get("autoMemoryEnabled") is not False:
    failures.append(".claude/settings.json must set autoMemoryEnabled to false")
starts = [h.get("command", "") for g in settings.get("hooks", {}).get("SessionStart", [])
          for h in g.get("hooks", [])]
if not any("hooks/team-memory-index.py" in c for c in starts):
    failures.append("SessionStart must run hooks/team-memory-index.py")

if failures:
    print("team-memory-index selftest FAILED")
    for f in failures:
        print("  " + f)
    sys.exit(1)
print("team-memory-index selftest passed")
