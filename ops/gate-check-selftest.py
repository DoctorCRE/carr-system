#!/usr/bin/env python3
"""Selftest for lib/gate_check.py — the shared check() the selftests use.

Two claims, each proven here:

1. The helper reproduces the output formats the suites already had, records
   failures where they were recorded, and bumps the counters they bumped.
2. A helper that always passes cannot hide behind a suite: with lib/gate_check.py
   mutated to always pass (or to record nothing, or never to raise), the helper
   refuses to be built, so EVERY suite that imports it exits non-zero. The
   mutation is applied in memory and the suites run unmodified — the file on
   disk is never touched.

Run `ops/gate-check-selftest.py --restore` to also run each suite unmutated
beside its mutated run and print both exit codes (slow: it runs every suite).
"""
from __future__ import annotations

import contextlib
import io
import re
import subprocess
import sys
import tempfile
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SOURCE = (ROOT / "lib" / "gate_check.py").read_text()
IMPORT_LINE = "from lib.gate_check import Check"
RAISING = re.compile(r"^check = Check\((?:[^()]|\([^()]*\))*?raises=", re.M)
FAILURES: list[str] = []

# Each mutant is a way a broken shared helper could make suites pass silently.
MUTANTS = {
    "always passes": ("verdict = bool(condition)", "verdict = True"),
    "never records a failure": ("if sink is not None:", "if False:"),
    "never raises on a failure": ("if not verdict and self.raises is not None:", "if False:"),
}

RUNNER = """
import runpy, sys, types
root, source, path = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, root)
import lib
module = types.ModuleType("lib.gate_check")
module.__file__ = root + "/lib/gate_check.py"
exec(compile(open(source).read(), module.__file__, "exec"), module.__dict__)
sys.modules["lib.gate_check"] = module
sys.path.insert(0, __import__("os").path.dirname(path))
sys.argv = [path]
runpy.run_path(path, run_name="__main__")
"""


def expect(label: str, condition: bool, detail: str = "") -> None:
    print(("  ok    " if condition else "  FAIL  ") + label + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


def load(source: str) -> types.ModuleType:
    module = types.ModuleType("gate_check_under_test")
    exec(compile(source, "gate_check_under_test", "exec"), module.__dict__)
    return module


def run_quiet(call, *args) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        call(*args)
    return out.getvalue()


def mutate(name: str) -> str:
    old, new = MUTANTS[name]
    assert SOURCE.count(old) == 1, f"mutation target for {name!r} must occur exactly once"
    return SOURCE.replace(old, new)


def migrated_suites() -> list[Path]:
    me = Path(__file__).resolve()
    found = []
    for folder in ("ops", "tools", "hooks", "bin"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            if path.resolve() != me and IMPORT_LINE in path.read_text(errors="replace"):
                found.append(path)
    return found


def run_suite(path: Path, source_file: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", RUNNER, str(ROOT), str(source_file), str(path)],
        cwd=ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=110,
    )


def unit_behaviour() -> None:
    gate = load(SOURCE)
    failures: list[str] = []
    passes: list[str] = []
    counts = {"passed": 0, "checked": 0}
    check = gate.Check(failures=failures, passes=passes, counters={"passed": "pass", "checked": "all"}, ns=counts)
    out = run_quiet(check, "alpha", True) + run_quiet(check, "beta", False, "why")
    expect("a pass prints the ok line", out.splitlines()[0] == "  ok    alpha", out)
    expect("a failure prints the FAIL line with its detail", out.splitlines()[1] == "  FAIL  beta — why", out)
    expect("a failure lands in the failures list", failures == ["beta"], str(failures))
    expect("a pass lands in the passes list", passes == ["alpha"], str(passes))
    expect("counters move by their rule", counts == {"passed": 1, "checked": 2}, str(counts))
    verdicts = []
    run_quiet(lambda: verdicts.extend([check("gamma", 0), check("delta", "x")]))
    expect("the verdict is returned", verdicts == [False, True], str(verdicts))

    quiet = gate.Check(ok=None, fail=None, detail=None, raises="{label}: {detail}")
    try:
        quiet("boom", False, "reason")
        raised = ""
    except AssertionError as error:
        raised = str(error)
    expect("raises renders its message template", raised == "boom: reason", raised)
    expect("a passing raising check stays quiet", run_quiet(quiet, "fine", True) == "")

    block = gate.Check(detail=None, detail_block="    ")
    expect("detail_block indents each detail line", run_quiet(block, "x", False, "a\nb") == "  FAIL  x\n    a\n    b\n")


def mutants_are_refused() -> None:
    for name in MUTANTS:
        gate = load(mutate(name))
        try:
            gate.Check(failures=[], raises="{label}")
            refused = False
        except RuntimeError as error:
            refused = "gate_check is broken" in str(error)
        expect(f"a helper that {name} refuses to be built", refused)


def suites_go_red(restore: bool) -> None:
    suites = migrated_suites()
    expect("migrated suites were found", len(suites) > 0, "none import lib.gate_check")
    with tempfile.TemporaryDirectory(prefix="gate-check-mutants-") as folder:
        sources = {}
        for name in MUTANTS:
            target = Path(folder) / (name.replace(" ", "-") + ".py")
            target.write_text(mutate(name))
            sources[name] = target
        real = Path(folder) / "real.py"
        real.write_text(SOURCE)
        with ThreadPoolExecutor(max_workers=8) as pool:
            for name, target in sources.items():
                # "never raises" can only matter to a suite whose check() raises.
                scope = [s for s in suites if name != "never raises on a failure" or RAISING.search(s.read_text())]
                results = list(pool.map(lambda suite: run_suite(suite, target), scope))
                red = [s for s, r in zip(scope, results) if r.returncode != 0 and "gate_check is broken" in r.stderr + r.stdout]
                stayed = [str(s.relative_to(ROOT)) for s in scope if s not in red]
                expect(f"all {len(scope)} migrated suites go red when the helper {name}", not stayed, ", ".join(stayed))
            if restore:
                restored = list(pool.map(lambda suite: run_suite(suite, real), suites))
                direct = list(pool.map(lambda s: subprocess.run([sys.executable, str(s)], cwd=ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=150).returncode, suites))
                for suite, r, d in zip(suites, restored, direct):
                    print(f"  restore {suite.relative_to(ROOT)}: restored-helper exit {r.returncode}, direct exit {d}")
                    expect(f"{suite.name} behaves the same through the restored helper", r.returncode == d)


def main() -> int:
    unit_behaviour()
    mutants_are_refused()
    suites_go_red("--restore" in sys.argv)
    if FAILURES:
        print(f"gate-check-selftest: {len(FAILURES)} failed")
        for label in FAILURES:
            print(f"  - {label}")
        return 1
    print("gate-check-selftest: all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
