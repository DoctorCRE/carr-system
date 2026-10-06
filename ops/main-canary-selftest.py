#!/usr/bin/env python3
"""Verify canary evidence, class parity, batching, and fail-closed freeze behavior."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
CANARY = REPO / ".github" / "workflows" / "main-canary.yml"
PILOT = REPO / ".github" / "workflows" / "automerge-pilot.yml"
CI_YML = REPO / ".github" / "workflows" / "ci.yml"

failures: list[str] = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        failures.append(name)


def load_state_module():
    spec = importlib.util.spec_from_file_location(
        "main_canary_state", REPO / "ops" / "main-canary-state.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ the canary
def test_the_canary_is_event_driven_and_never_always_on():
    y = CANARY.read_text(encoding="utf-8")
    check("it triggers on push to main", "push:" in y and "branches: [main]" in y)
    check("it has NO schedule — the council forbade a new always-on job",
          "schedule:" not in y and "cron:" not in y,
          "ci.yml already carries the 06:20 UTC quiet-period backstop")
    check("it can still be run by hand when main's state must be re-established",
          "workflow_dispatch:" in y)


def test_a_running_canary_finishes_before_the_newest_pending_run():
    y = CANARY.read_text(encoding="utf-8")
    concurrency = re.search(r"^concurrency:\s*\n((?:[ \t]+[^\n]*\n)+)", y, re.M)
    check("the workflow declares concurrency", concurrency is not None)
    if concurrency is None:
        return
    settings = concurrency.group(1)
    check("canaries share the main-canary group",
          re.search(r"^  group: main-canary\s*$", settings, re.M) is not None)
    # Canaries take 20-30 minutes while merges can land every 20 minutes.
    # Cancelling the running canary starves releases of a completed verdict;
    # GitHub still replaces older pending runs with the newest in this group.
    check("a new merge cannot cancel the running canary and starve releases",
          re.search(r"^  cancel-in-progress: false\s*$", settings, re.M) is not None)


def test_parallel_classes_match_pr_ci_and_reuse_is_explicit():
    import json
    import subprocess
    def workflow(path):
        result = subprocess.run(['node', '-e',
            "const fs=require('fs'),y=require('js-yaml');console.log(JSON.stringify(y.load(fs.readFileSync(process.argv[1],'utf8'))))",
            str(path)], cwd=REPO / 'mcp-server', capture_output=True, text=True, check=True)
        return json.loads(result.stdout)
    main, pr = workflow(CANARY), workflow(CI_YML)
    classes = main['jobs']['classes']
    check('main has the identical parallel class set as PR CI',
          classes['strategy']['matrix'] == pr['jobs']['classes']['strategy']['matrix'])
    check('all classes run unless exact-tree proof succeeded',
          classes.get('if') == "${{ needs.evidence.outputs.reused != 'true' }}")
    main_steps = classes['steps']
    pr_steps = pr['jobs']['classes']['steps']
    check('class execution and environment match PR CI',
          main_steps == [st for st in pr_steps if st.get('name') != 'Require a written pull request description'])
    check('service containers match PR CI', classes['services'] == pr['jobs']['classes']['services'])
    aggregate = main['jobs']['canary']
    check('one aggregate main canary always reports',
          aggregate['name'] == 'main canary' and 'always()' in aggregate['if'])
    check('aggregate requires evidence and classes',
          aggregate['needs'] == ['evidence', 'classes'])
    check('reuse is recorded before any classes are skipped',
          'ops/ci-evidence.py resolve' in CANARY.read_text()
          and 'ci-tree-evidence' in CI_YML.read_text())
    check('the obsolete debounce no longer delays reuse', 'sleep 90' not in CANARY.read_text())


def test_role_migration_fixtures_have_postgresql_17_server_binaries():
    for path in (CANARY, CI_YML):
        y = path.read_text(encoding="utf-8")
        install = y.find("sudo apt-get install -y -qq postgresql-17")
        check(f"{path.name} installs PG17 for owned role-migration fixtures", install >= 0)
        check(f"{path.name} exposes PG17 binaries to the shared discovery helper",
              'echo "/usr/lib/postgresql/17/bin" >> "$GITHUB_PATH"' in y)
        check(f"{path.name} provisions PG17 before check execution",
              0 <= install < y.find("run: ops/ci.sh --strict") if path == CI_YML else
              0 <= install < y.find("run: ops/ci.sh --strict"))


def test_a_red_canary_stays_red_and_names_main():
    y = CANARY.read_text()
    check('a failing class fails the aggregate', 'exit 1' in y)
    check('its failure names main', 'MAIN IS RED' in y)
    check('an absent reuse proof cannot green a skipped class',
          'needs.evidence.result' in y and 'needs.classes.result' in y
          and 'needs.evidence.outputs.reused' in y)



# ------------------------------------------------------------------- the freeze
def test_freeze_reads_a_verdict_not_a_cancellation():
    mod = load_state_module()
    calls = []

    def fake(path):
        calls.append(path)
        return {"workflow_runs": [
            {"conclusion": "cancelled", "run_number": 9, "head_sha": "c" * 40},
            {"conclusion": None, "run_number": 8, "head_sha": "b" * 40},
            {"conclusion": "success", "run_number": 7, "head_sha": "a" * 40,
             "html_url": "https://example.invalid/7"},
        ]}
    mod.api = fake
    os.environ.pop("CARR_MAIN_FREEZE", None)
    os.environ["GITHUB_REPOSITORY"] = "carr/system"
    s = mod.state()
    check("cancelled and in-progress runs are skipped for the last real verdict",
          s["state"] == "green" and "run 7" in s["source"], s)
    check("it asked only the canary's own runs on main",
          calls and "main-canary.yml" in calls[0] and "branch=main" in calls[0], calls)


def test_every_not_knowing_refuses():
    mod = load_state_module()
    os.environ.pop("CARR_MAIN_FREEZE", None)
    os.environ["GITHUB_REPOSITORY"] = "carr/system"

    for label, payload in (("the API would not answer", None),
                           ("the workflow has never run", {"workflow_runs": []}),
                           ("a burst is in flight, all cancelled",
                            {"workflow_runs": [{"conclusion": "cancelled", "run_number": 3}]})):
        mod.api = lambda _p, _v=payload: _v
        s = mod.state()
        check(f"unknown when {label}", s["state"] == "unknown", s)
        code = require_green_exit(mod)
        check(f"--require-green REFUSES when {label} (Codex's kill criterion)",
              code == 1, f"exit {code}")


def require_green_exit(mod) -> int:
    argv, out = sys.argv, io.StringIO()
    sys.argv = ["main-canary-state.py", "--require-green"]
    try:
        with contextlib.redirect_stdout(out):
            return mod.main()
    finally:
        sys.argv = argv


def test_red_freezes_and_green_releases():
    mod = load_state_module()
    os.environ.pop("CARR_MAIN_FREEZE", None)
    os.environ["GITHUB_REPOSITORY"] = "carr/system"

    mod.api = lambda _p: {"workflow_runs": [
        {"conclusion": "failure", "run_number": 11, "head_sha": "f" * 40,
         "html_url": "https://example.invalid/11"}]}
    s = mod.state()
    check("a failed canary is red", s["state"] == "red", s)
    check("the red names the commit it was found on", s.get("sha") == "f" * 12, s)
    check("--require-green refuses on red", require_green_exit(mod) == 1)

    mod.api = lambda _p: {"workflow_runs": [
        {"conclusion": "success", "run_number": 12, "head_sha": "e" * 40}]}
    check("the next green canary IS the unfreeze — nothing to reset",
          mod.state()["state"] == "green" and require_green_exit(mod) == 0)


def test_the_manual_lever_can_freeze_but_never_force_green():
    mod = load_state_module()
    os.environ["GITHUB_REPOSITORY"] = "carr/system"
    called = []
    mod.api = lambda _p: called.append(_p) or {"workflow_runs": [
        {"conclusion": "success", "run_number": 12, "head_sha": "e" * 40}]}

    os.environ["CARR_MAIN_FREEZE"] = "on"
    s = mod.state()
    check("CARR_MAIN_FREEZE=on freezes even when the canary is green",
          s["state"] == "red" and not called, s)
    check("the manual freeze says how to clear itself", "unfreeze" in s["detail"], s)
    check("--require-green refuses under the manual freeze",
          require_green_exit(mod) == 1)

    # THE ASYMMETRY IS THE POINT. A lever that can declare a broken main healthy
    # is a verdict weakener wearing a convenience hat, and the council's first
    # constraint was that no verdict is weakened to improve the number.
    for forcing in ("off", "false", "green", "no"):
        os.environ["CARR_MAIN_FREEZE"] = forcing
        called.clear()
        mod.api = lambda _p: {"workflow_runs": [
            {"conclusion": "failure", "run_number": 13, "head_sha": "d" * 40}]}
        check(f"CARR_MAIN_FREEZE={forcing!r} cannot force a red main green",
              mod.state()["state"] == "red")
    os.environ.pop("CARR_MAIN_FREEZE", None)

    src = (REPO / "ops" / "main-canary-state.py").read_text(encoding="utf-8")
    check("there is no force-green anywhere in the door",
          'return {"state": "green"' not in src.replace(
              '"state": "green" if green else "red"', ""),
          "green is only ever derived from a successful canary run")


def test_the_pilot_asks_before_it_plans():
    y = PILOT.read_text(encoding="utf-8")
    check("the automerge pilot consults the freeze",
          "ops/main-canary-state.py --require-green" in y)
    check("it asks BEFORE planning, not after a 20-minute verify job",
          y.find("main-canary-state.py") < y.find("id: plan"),
          "the cheap refusal has to come first or it saves nothing")
    check("the plan job can read the canary's runs",
          "actions: read" in y)
    check("the manual lever reaches the pilot",
          "CARR_MAIN_FREEZE: ${{ vars.CARR_MAIN_FREEZE }}" in y)


def main():
    for fn in (test_the_canary_is_event_driven_and_never_always_on,
               test_a_running_canary_finishes_before_the_newest_pending_run,
               test_parallel_classes_match_pr_ci_and_reuse_is_explicit,
               test_role_migration_fixtures_have_postgresql_17_server_binaries,
               test_a_red_canary_stays_red_and_names_main,
               test_freeze_reads_a_verdict_not_a_cancellation,
               test_every_not_knowing_refuses,
               test_red_freezes_and_green_releases,
               test_the_manual_lever_can_freeze_but_never_force_green,
               test_the_pilot_asks_before_it_plans):
        print(f"\n{fn.__name__}")
        try:
            fn()
        except Exception as exc:  # a crashing case is a failing case, never a skip
            check(f"{fn.__name__} raised", False, repr(exc))
    print(f"\nmain-canary-selftest: {len(failures)} failure(s)")
    for f in failures:
        print(f"  - {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
