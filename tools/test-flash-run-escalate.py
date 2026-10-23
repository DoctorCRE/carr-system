#!/usr/bin/env python3
"""Contract tests for Flash escalation through plain codex exec."""

from __future__ import annotations

import importlib.util
import atexit
from contextlib import ExitStack
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

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
TEMP_STACK = ExitStack()
atexit.register(TEMP_STACK.close)


def managed_temp(prefix):
    return TEMP_STACK.enter_context(tempfile.TemporaryDirectory(prefix=prefix))


def check(name, fn):
    try:
        fn()
        print(f"  ok    {name}")
    except AssertionError as exc:
        FAILURES.append(name)
        print(f"  FAIL  {name}: {exc!r}")


def repo():
    d = managed_temp("flash-escalate-")
    with open(os.path.join(d, "calc.py"), "w") as fh:
        fh.write(BROKEN)
    subprocess.run(["git", "init", "-q"], cwd=d, env=ENV, check=True)
    return d


class FakeCodex:
    def __init__(self, answer="", raises=None, edit=None, runs_tests=False):
        self.answer, self.raises, self.edit = answer, raises, edit
        self.runs_tests = runs_tests
        self.sent: list = []

    def __call__(self, text, cwd):
        self.sent.append((text, cwd))
        if self.raises:
            raise self.raises
        if self.edit and cwd:
            with open(os.path.join(cwd, "calc.py"), "w") as fh:
                fh.write(self.edit)
        if self.runs_tests and cwd:  # running the test in the copy rewrites Python's bytecode cache
            write_cache(cwd, b"codex run")
        return self.answer


def code_failure_goes_to_codex_with_files_and_test():
    d, executor = repo(), FakeCodex(FIX)
    out = fr.escalate("fix add", d, "t1", "AssertionError", ["calc.py"], "auto",
                      test_cmd="python3 calc.py", executor=executor)
    text, cwd = executor.sent[0]
    assert "python3 calc.py" in text and "return a - b" in text, text[:400]
    assert cwd and cwd != d, "Codex must work in a throwaway copy, never the real folder"
    assert not os.path.exists(cwd), "the copy is cleaned up"
    assert out["executor"] == "codex", out


def judgment_goes_to_codex():
    executor = FakeCodex(edit=BROKEN.replace("a - b", "a + b"))
    d = repo()
    out = fr.escalate("redesign the routing", d, "t2", None, [], "auto",
                      kind="judgment", executor=executor)
    assert executor.sent and "redesign the routing" in executor.sent[0][0]
    assert out["outcome"] == "completed", out
    assert Path(d, "calc.py").read_text() == BROKEN
    assert not os.path.exists(executor.sent[0][1])


def suggest_mode_runs_nothing():
    executor = FakeCodex(FIX)
    out = fr.escalate("fix add", repo(), "t3", "err", ["calc.py"], "suggest", executor=executor)
    assert executor.sent == [] and out["outcome"] == "handoff_written", out
    assert os.path.isfile(out["handoff"]), out


def codex_edits_in_copy_are_applied_and_kept():
    d = repo()
    out = fr.escalate("fix add", d, "t7", "err", ["calc.py"], "auto", test_cmd="python3 calc.py",
                      executor=FakeCodex(edit=BROKEN.replace("a - b", "a + b")))
    assert out["outcome"] == "fixed_by_codex", out
    assert "a + b" in open(os.path.join(d, "calc.py")).read()


def write_cache(d, payload):
    os.makedirs(os.path.join(d, "__pycache__"), exist_ok=True)
    with open(os.path.join(d, "__pycache__", "calc.cpython-314.pyc"), "wb") as fh:
        fh.write(payload)


def bytecode_caches_never_block_the_fix():
    # Live escalation 2026-09-24: the real folder already had __pycache__ from running the test once,
    # Codex's test run in the copy rewrote it, and the read-back patch failed to apply on the .pyc.
    # The live folder was not a git repo, so the copy skipped __pycache__ and the patch re-created it.
    d = managed_temp("flash-escalate-nogit-")
    with open(os.path.join(d, "calc.py"), "w") as fh:
        fh.write(BROKEN)
    write_cache(d, b"local run")
    out = fr.escalate("fix add", d, "t9", "err", ["calc.py"], "auto", test_cmd="python3 calc.py",
                      executor=FakeCodex(edit=BROKEN.replace("a - b", "a + b"), runs_tests=True))
    assert out["outcome"] == "fixed_by_codex", out
    assert "a + b" in open(os.path.join(d, "calc.py")).read()


def passing_codex_patch_is_kept():
    d = repo()
    out = fr.escalate("fix add", d, "t4", "err", ["calc.py"], "auto",
                      test_cmd="python3 calc.py", executor=FakeCodex(FIX))
    assert out["outcome"] == "fixed_by_codex", out
    assert "a + b" in open(os.path.join(d, "calc.py")).read()


def failing_codex_patch_is_reverted():
    d = repo()
    out = fr.escalate("fix add", d, "t5", "err", ["calc.py"], "auto",
                      test_cmd="python3 calc.py", executor=FakeCodex(WRONG))
    assert out["outcome"] == "codex_patch_failed_reverted", out
    assert open(os.path.join(d, "calc.py")).read() == BROKEN


def codex_outage_never_crashes():
    out = fr.escalate("fix add", repo(), "t6", "err", ["calc.py"], "auto",
                      test_cmd="python3 calc.py", executor=FakeCodex(raises=RuntimeError("down")))
    assert out["outcome"] == "execution_failed" and "down" in out["detail"], out


def extract_diff_finds_the_block():
    assert fr.extract_diff("prose\n" + FIX + "\nmore").startswith("--- a/calc.py")
    assert fr.extract_diff("no patch here") is None


def codex_exec_uses_the_configured_model_and_final_message():
    d, outputs = repo(), []
    def run(argv, **kwargs):
        assert argv[:2] == ["codex", "exec"], argv
        assert argv[argv.index("-C") + 1] == d, argv
        assert "--dangerously-bypass-hook-trust" in argv, argv
        assert "-m" not in argv and "--model" not in argv, argv
        assert "timeout" not in kwargs and kwargs["check"] is True, kwargs
        output = Path(argv[argv.index("-o") + 1])
        outputs.append(output)
        output.write_text("final answer")
        return subprocess.CompletedProcess(argv, 0)
    with patch.object(fr.subprocess, "run", side_effect=run):
        assert fr._codex_exec("fix add", d) == "final answer"
    assert outputs and not outputs[0].exists()


check("plain codex exec returns its final message", codex_exec_uses_the_configured_model_and_final_message)
check("code failure goes to Codex with files and test", code_failure_goes_to_codex_with_files_and_test)
check("judgment call goes to Codex", judgment_goes_to_codex)
check("suggest mode runs nothing", suggest_mode_runs_nothing)
check("Codex's edits in the copy are applied and kept", codex_edits_in_copy_are_applied_and_kept)
check("bytecode caches never block Codex's fix", bytecode_caches_never_block_the_fix)
check("a Codex patch that passes the test is kept", passing_codex_patch_is_kept)
check("a Codex patch that fails the test is reverted", failing_codex_patch_is_reverted)
check("a Codex outage never crashes flash-run", codex_outage_never_crashes)
check("extract_diff finds the diff block", extract_diff_finds_the_block)

if FAILURES:
    print(f"flash-run escalate: {len(FAILURES)} FAILED")
    sys.exit(1)
print("flash-run escalate: every assertion held")
