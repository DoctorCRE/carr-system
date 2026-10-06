#!/usr/bin/env python3
"""Contract tests for flash-run's escalation through the Model Room.

Joe's decision 2026-09-24: Flash replaces Sonnet for scoped, testable coding; what Flash
cannot do goes to Sol or Opus, through Model Room desks, never a direct model call.
A code failure goes to the Sol desk, whose patch is applied only if the task's test then
passes; a task routed away as a design/judgment call goes to the Opus desk.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "ops"))
from git_env import fixture_env  # noqa: E402

spec = importlib.util.spec_from_file_location("flash_run", os.path.join(HERE, "flash-run.py"))
if spec is None or spec.loader is None:
    raise ImportError("tools/flash-run.py")
fr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fr)

ENV = fixture_env()
FAILURES: list[str] = []
BROKEN = "def add(a, b):\n    return a - b\n\nassert add(2, 3) == 5\n"
FIX = """```diff
--- a/calc.py
+++ b/calc.py
@@ -1,4 +1,4 @@
 def add(a, b):
-    return a - b
+    return a + b

 assert add(2, 3) == 5
```"""
WRONG = FIX.replace("a + b", "a * b")


def check(name, fn):
    try:
        fn()
        print(f"  ok    {name}")
    except AssertionError as exc:
        FAILURES.append(name)
        print(f"  FAIL  {name}: {exc!r}")


def repo():
    d = tempfile.mkdtemp(prefix="flash-escalate-")
    with open(os.path.join(d, "calc.py"), "w") as fh:
        fh.write(BROKEN)
    subprocess.run(["git", "init", "-q"], cwd=d, env=ENV, check=True)
    return d


class FakeDesks:
    """Stands in for the Model Room. With `edit`, it rewrites calc.py in the copy it is
    handed, the way the writable Sol fixer desk does; with a diff answer, it only talks."""

    def __init__(self, answer="", status="completed", raises=None, edit=None, runs_tests=False):
        self.answer, self.status, self.raises, self.edit = answer, status, raises, edit
        self.runs_tests = runs_tests
        self.sent: list = []

    def __call__(self, desk, text, cwd=None):
        self.sent.append((desk, text, cwd))
        if self.raises:
            raise self.raises
        if self.edit and cwd:
            with open(os.path.join(cwd, "calc.py"), "w") as fh:
                fh.write(self.edit)
        if self.runs_tests and cwd:  # running the test in the copy rewrites Python's bytecode cache
            write_cache(cwd, b"desk run")
        return {"status": self.status, "result": self.answer}


def code_failure_goes_to_sol_with_files_and_test():
    d, desks = repo(), FakeDesks(FIX)
    out = fr.escalate("fix add", d, "t1", "AssertionError", ["calc.py"], "auto",
                      test_cmd="python3 calc.py", dispatcher=desks)
    desk, text, cwd = desks.sent[0]
    assert desk == fr.ESCALATION_DESKS["code"] == "sol-fixer", desk
    assert "python3 calc.py" in text and "return a - b" in text, text[:400]
    assert cwd and cwd != d, "Sol must work in a throwaway copy, never the real folder"
    assert not os.path.exists(cwd), "the copy is cleaned up"
    assert out["desk"] == "sol-fixer", out


def judgment_goes_to_opus():
    desks = FakeDesks(status="launched")
    out = fr.escalate("redesign the routing", repo(), "t2", None, [], "auto",
                      kind="judgment", dispatcher=desks)
    assert desks.sent[0][0] == fr.ESCALATION_DESKS["judgment"] == "claude-desktop", desks.sent
    assert out["outcome"] == "dispatched", out


def suggest_mode_dispatches_nothing():
    desks = FakeDesks(FIX)
    out = fr.escalate("fix add", repo(), "t3", "err", ["calc.py"], "suggest", dispatcher=desks)
    assert desks.sent == [] and out["outcome"] == "handoff_written", out
    assert os.path.isfile(out["handoff"]), out


def sol_edits_in_copy_are_applied_and_kept():
    d = repo()
    out = fr.escalate("fix add", d, "t7", "err", ["calc.py"], "auto", test_cmd="python3 calc.py",
                      dispatcher=FakeDesks(edit=BROKEN.replace("a - b", "a + b")))
    assert out["outcome"] == "fixed_by_desk", out
    assert "a + b" in open(os.path.join(d, "calc.py")).read()


def write_cache(d, payload):
    os.makedirs(os.path.join(d, "__pycache__"), exist_ok=True)
    with open(os.path.join(d, "__pycache__", "calc.cpython-314.pyc"), "wb") as fh:
        fh.write(payload)


def bytecode_caches_never_block_the_fix():
    # Live escalation 2026-09-24: the real folder already had __pycache__ from running the test once,
    # Sol's test run in the copy rewrote it, and the read-back patch failed to apply on the .pyc.
    # The live folder was not a git repo, so the copy skipped __pycache__ and the patch re-created it.
    d = tempfile.mkdtemp(prefix="flash-escalate-nogit-")
    with open(os.path.join(d, "calc.py"), "w") as fh:
        fh.write(BROKEN)
    write_cache(d, b"local run")
    out = fr.escalate("fix add", d, "t9", "err", ["calc.py"], "auto", test_cmd="python3 calc.py",
                      dispatcher=FakeDesks(edit=BROKEN.replace("a - b", "a + b"), runs_tests=True))
    assert out["outcome"] == "fixed_by_desk", out
    assert "a + b" in open(os.path.join(d, "calc.py")).read()


def passing_desk_patch_is_kept():
    d = repo()
    out = fr.escalate("fix add", d, "t4", "err", ["calc.py"], "auto",
                      test_cmd="python3 calc.py", dispatcher=FakeDesks(FIX))
    assert out["outcome"] == "fixed_by_desk", out
    assert "a + b" in open(os.path.join(d, "calc.py")).read()


def failing_desk_patch_is_reverted():
    d = repo()
    out = fr.escalate("fix add", d, "t5", "err", ["calc.py"], "auto",
                      test_cmd="python3 calc.py", dispatcher=FakeDesks(WRONG))
    assert out["outcome"] == "desk_patch_failed_reverted", out
    assert open(os.path.join(d, "calc.py")).read() == BROKEN


def desk_outage_never_crashes():
    out = fr.escalate("fix add", repo(), "t6", "err", ["calc.py"], "auto",
                      test_cmd="python3 calc.py", dispatcher=FakeDesks(raises=RuntimeError("down")))
    assert out["outcome"] == "dispatch_failed" and "down" in out["detail"], out


def extract_diff_finds_the_block():
    assert fr.extract_diff("prose\n" + FIX + "\nmore").startswith("--- a/calc.py")
    assert fr.extract_diff("no patch here") is None


check("code failure goes to Sol with files and test", code_failure_goes_to_sol_with_files_and_test)
check("judgment call goes to Opus", judgment_goes_to_opus)
check("suggest mode dispatches nothing", suggest_mode_dispatches_nothing)
check("Sol's edits in the copy are applied and kept", sol_edits_in_copy_are_applied_and_kept)
check("bytecode caches never block Sol's fix", bytecode_caches_never_block_the_fix)
check("a desk patch that passes the test is kept", passing_desk_patch_is_kept)
check("a desk patch that fails the test is reverted", failing_desk_patch_is_reverted)
check("a desk outage never crashes flash-run", desk_outage_never_crashes)
check("extract_diff finds the diff block", extract_diff_finds_the_block)

if FAILURES:
    print(f"flash-run escalate: {len(FAILURES)} FAILED")
    sys.exit(1)
print("flash-run escalate: every assertion held")
