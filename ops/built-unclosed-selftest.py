#!/usr/bin/env python3
"""built-unclosed-selftest.py — acceptance test for ops/built_unclosed.py.

WHY THIS EXISTS. capability-program said completed=0/51 and a session read that
as "nothing is built" and started a new build. The artifacts already exist on
main — completed counts confirmed_closed attestations, not code on disk — so
the number was attestation truth, not build truth. A session that treats
"not confirmed_closed" as "not built" rebuilds work that already landed.

The latch this tests is the missing second number: built_unclosed, the count of
Work Requests whose evidence paths all exist on disk AND whose state is not
confirmed_closed. The Worker cannot stat the repo; this detector can, because
it runs in local hooks that already see the tree.

GENERALIZED 2026-08-21. The detector now works for any program_key and also
classifies implementation-open (claimed/in_progress/verification) and
conceptual-open (captured/triaged). The root is passed by the caller, never
hardcoded.

Run: python3 ops/built-unclosed-selftest.py
"""
from __future__ import annotations

import importlib.util
import atexit
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MOD = os.path.join(REPO, "ops", "built_unclosed.py")

# Keep every fixture below one owned root so a normal or failing interpreter
# exit cannot leave repo-local TMPDIR litter behind.
_ORIGINAL_MKDTEMP = tempfile.mkdtemp
_FIXTURE_ROOT = _ORIGINAL_MKDTEMP(prefix="built-unclosed-selftest-")
atexit.register(shutil.rmtree, _FIXTURE_ROOT, ignore_errors=True)


def _fixture_mkdtemp(suffix=None, prefix=None, dir=None):
    return _ORIGINAL_MKDTEMP(suffix=suffix, prefix=prefix,
                             dir=_FIXTURE_ROOT)


tempfile.mkdtemp = _fixture_mkdtemp

_spec = importlib.util.spec_from_file_location("built_unclosed", MOD)
assert _spec and _spec.loader, f"cannot load {MOD}"
bu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bu)

failures: list[str] = []
CASES: list[tuple] = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        failures.append(name)


def _rows(*items):
    """Build fixture rows from (ref, state, [evidence_paths]) tuples."""
    return [{"ref": r, "state": s, "evidence": e} for r, s, e in items]


# ── detect_built_unclosed: the core detector ──────────────────────────────


@case("confirmed_closed is not built-unclosed even when evidence exists")
def _(assert_):
    root = tempfile.mkdtemp()
    for p in ["ops/ai_eval.py", "evals/ai/model-boundary.v1.json"]:
        full = os.path.join(root, p)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        open(full, "w").write("x")
    rows = _rows(("WR-AI-001", "confirmed_closed", ["ops/ai_eval.py",
                                                       "evals/ai/model-boundary.v1.json"]))
    result = bu.detect_built_unclosed(rows, root)
    assert_(result == [],
            f"confirmed_closed with all-evidence-present must NOT be built_unclosed: {result!r}")


@case("not-confirmed_closed with all evidence present IS built-unclosed")
def _(assert_):
    root = tempfile.mkdtemp()
    for p in ["ops/ai_eval.py", "evals/ai/model-boundary.v1.json"]:
        full = os.path.join(root, p)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        open(full, "w").write("x")
    rows = _rows(("WR-AI-006", "ready", ["ops/ai_eval.py",
                                         "evals/ai/model-boundary.v1.json"]))
    result = bu.detect_built_unclosed(rows, root)
    assert_([r["ref"] for r in result] == ["WR-AI-006"],
            f"not-confirmed_closed + all evidence present => built_unclosed: {result!r}")


@case("empty evidence is NOT landed")
def _(assert_):
    root = tempfile.mkdtemp()
    rows = _rows(("WR-AI-005", "ready", []),
                 ("WR-AI-006", "ready", []))
    result = bu.detect_built_unclosed(rows, root)
    assert_(result == [],
            f"empty evidence must not be built_unclosed (do not guess): {result!r}")


@case("one missing path => NOT landed")
def _(assert_):
    root = tempfile.mkdtemp()
    exists = os.path.join(root, "ops/ai_eval.py")
    os.makedirs(os.path.dirname(exists), exist_ok=True)
    open(exists, "w").write("x")
    rows = _rows(("WR-AI-006", "ready", ["ops/ai_eval.py",
                                         "evals/ai/does-not-exist.v1.json"]))
    result = bu.detect_built_unclosed(rows, root)
    assert_(result == [],
            f"one missing evidence path => NOT built_unclosed: {result!r}")


@case("mixed: some built-unclosed, some not")
def _(assert_):
    root = tempfile.mkdtemp()
    for p in ["ops/ai_eval.py"]:
        full = os.path.join(root, p)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        open(full, "w").write("x")
    rows = _rows(
        ("WR-AI-001", "confirmed_closed", ["ops/ai_eval.py"]),
        ("WR-AI-002", "ready", ["ops/ai_eval.py"]),         # built-unclosed
        ("WR-AI-003", "ready", []),                          # empty evidence — not landed
        ("WR-AI-004", "ready", ["ops/nonexistent.py"]),      # missing path — not landed
    )
    result = bu.detect_built_unclosed(rows, root)
    refs = [r["ref"] for r in result]
    assert_(refs == ["WR-AI-002"],
            f"only WR-AI-002 should be built_unclosed: {refs!r}")


# ── detect_implementation_open ───────────────────────────────────────────


@case("claimed/in_progress/verification states are implementation-open")
def _(assert_):
    rows = _rows(
        ("WR-X-001", "claimed", []),
        ("WR-X-002", "in_progress", []),
        ("WR-X-003", "verification", []),
        ("WR-X-004", "ready", []),
        ("WR-X-005", "confirmed_closed", []),
    )
    result = bu.detect_implementation_open(rows)
    refs = sorted(r["ref"] for r in result)
    assert_(refs == ["WR-X-001", "WR-X-002", "WR-X-003"],
            f"implementation-open should be claimed/in_progress/verification only: {refs!r}")


@case("implementation_open does not require evidence or root")
def _(assert_):
    rows = _rows(("WR-X-001", "in_progress", []))
    result = bu.detect_implementation_open(rows)
    assert_([r["ref"] for r in result] == ["WR-X-001"],
            f"implementation_open works without evidence: {result!r}")


# ── detect_conceptual_open ────────────────────────────────────────────────


@case("captured/triaged states are conceptual-open")
def _(assert_):
    rows = _rows(
        ("WR-X-001", "captured", []),
        ("WR-X-002", "triaged", []),
        ("WR-X-003", "ready", []),
        ("WR-X-004", "in_progress", []),
    )
    result = bu.detect_conceptual_open(rows)
    refs = sorted(r["ref"] for r in result)
    assert_(refs == ["WR-X-001", "WR-X-002"],
            f"conceptual-open should be captured/triaged only: {refs!r}")


# ── detect_all_open ──────────────────────────────────────────────────────


@case("detect_all_open classifies all three stages")
def _(assert_):
    root = tempfile.mkdtemp()
    p = os.path.join(root, "ops/ai_eval.py")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write("x")
    rows = _rows(
        ("WR-X-001", "captured", []),
        ("WR-X-002", "triaged", []),
        ("WR-X-003", "ready", ["ops/ai_eval.py"]),
        ("WR-X-004", "in_progress", ["ops/ai_eval.py"]),
        ("WR-X-005", "verification", ["ops/ai_eval.py"]),
        ("WR-X-006", "confirmed_closed", ["ops/ai_eval.py"]),
    )
    result = bu.detect_all_open(rows, root)
    bu_refs = sorted(r["ref"] for r in result["built_unclosed"])
    impl_refs = sorted(r["ref"] for r in result["implementation_open"])
    concept_refs = sorted(r["ref"] for r in result["conceptual_open"])
    assert_(bu_refs == ["WR-X-003", "WR-X-004", "WR-X-005"],
            f"built_unclosed: {bu_refs!r}")
    assert_(impl_refs == ["WR-X-004", "WR-X-005"],
            f"implementation_open: {impl_refs!r}")
    assert_(concept_refs == ["WR-X-001", "WR-X-002"],
            f"conceptual_open: {concept_refs!r}")


@case("a verification row with evidence on disk is both built-unclosed and implementation-open")
def _(assert_):
    root = tempfile.mkdtemp()
    p = os.path.join(root, "ops/ai_eval.py")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write("x")
    rows = _rows(("WR-X-005", "verification", ["ops/ai_eval.py"]))
    result = bu.detect_all_open(rows, root)
    assert_(len(result["built_unclosed"]) == 1,
            f"verification+evidence => built_unclosed: {result['built_unclosed']!r}")
    assert_(len(result["implementation_open"]) == 1,
            f"verification => implementation_open: {result['implementation_open']!r}")
    assert_(len(result["conceptual_open"]) == 0,
            f"verification => NOT conceptual_open: {result['conceptual_open']!r}")


# ── landed_in_repo ─────────────────────────────────────────────────────────


@case("landed_in_repo counts all-evidence-present regardless of state")
def _(assert_):
    root = tempfile.mkdtemp()
    for p in ["ops/ai_eval.py", "evals/ai/model-boundary.v1.json"]:
        full = os.path.join(root, p)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        open(full, "w").write("x")
    rows = _rows(
        ("WR-AI-001", "confirmed_closed", ["ops/ai_eval.py"]),  # landed but closed
        ("WR-AI-002", "ready", ["ops/ai_eval.py"]),               # landed and unclosed
        ("WR-AI-003", "ready", []),                               # not landed
    )
    count = bu.landed_in_repo(rows, root)
    assert_(count == 2,
            f"landed_in_repo should be 2 (closed+unclosed with evidence present): {count}")


@case("landed_in_repo is 0 when no evidence paths exist")
def _(assert_):
    root = tempfile.mkdtemp()
    rows = _rows(("WR-AI-001", "ready", []),
                 ("WR-AI-002", "confirmed_closed", []))
    count = bu.landed_in_repo(rows, root)
    assert_(count == 0, f"no evidence => landed_in_repo=0: {count}")


# ── state variations: blocked, failed, needs-Joe, verification ─────────────


@case("blocked/failed/verification states are built-unclosed when evidence exists")
def _(assert_):
    root = tempfile.mkdtemp()
    p = os.path.join(root, "ops/ai_eval.py")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write("x")
    rows = _rows(
        ("WR-AI-001", "blocked", ["ops/ai_eval.py"]),
        ("WR-AI-002", "failed", ["ops/ai_eval.py"]),
        ("WR-AI-003", "verification", ["ops/ai_eval.py"]),
        ("WR-AI-004", "needs_joe", ["ops/ai_eval.py"]),
    )
    result = bu.detect_built_unclosed(rows, root)
    refs = sorted(r["ref"] for r in result)
    assert_(refs == ["WR-AI-001", "WR-AI-002", "WR-AI-003", "WR-AI-004"],
            f"all non-confirmed_closed states with evidence are built_unclosed: {refs!r}")


@case("verification is implementation-open; blocked/failed/needs_joe are not")
def _(assert_):
    rows = _rows(
        ("WR-AI-001", "blocked", []),
        ("WR-AI-002", "failed", []),
        ("WR-AI-003", "verification", []),
        ("WR-AI-004", "needs_joe", []),
    )
    result = bu.detect_implementation_open(rows)
    refs = [r["ref"] for r in result]
    assert_(refs == ["WR-AI-003"],
            f"only verification is implementation-open from this set: {refs!r}")


# ── load_live_rows: DB helper must not be required for unit tests ──────────


@case("load_live_rows returns None when CARR_DB_EXPORTER_URL is absent")
def _(assert_):
    # Mock _exporter_url to return None so no db.env file is read — the
    # function must return None when no URL is resolvable, regardless of
    # whether a real db.env happens to exist on the test machine.
    saved_url = bu._exporter_url
    bu._exporter_url = lambda: None
    try:
        rows = bu.load_live_rows()
        assert_(rows is None,
                f"load_live_rows without DB should return None, got {rows!r}")
    finally:
        bu._exporter_url = saved_url


@case("_exporter_url reads db.env when the env var is unset")
def _(assert_):
    saved = os.environ.pop("CARR_DB_EXPORTER_URL", None)
    saved_home = os.environ.get("HOME")
    try:
        tmp = tempfile.mkdtemp()
        os.environ["HOME"] = tmp
        cfg_dir = os.path.join(tmp, ".config", "carr")
        os.makedirs(cfg_dir, exist_ok=True)
        with open(os.path.join(cfg_dir, "db.env"), "w") as fh:
            fh.write('CARR_DB_EXPORTER_URL="postgresql://fake:5432/fake"\n')
        url = bu._exporter_url()
        assert_(url == "postgresql://fake:5432/fake",
                f"_exporter_url should fall back to db.env, got {url!r}")
    finally:
        if saved is not None:
            os.environ["CARR_DB_EXPORTER_URL"] = saved
        if saved_home is not None:
            os.environ["HOME"] = saved_home
        else:
            os.environ.pop("HOME", None)


@case("_exporter_url returns None when neither env var nor db.env has a URL")
def _(assert_):
    saved = os.environ.pop("CARR_DB_EXPORTER_URL", None)
    saved_home = os.environ.get("HOME")
    try:
        tmp = tempfile.mkdtemp()
        os.environ["HOME"] = tmp
        # No ~/.config/carr/db.env exists under this fake HOME
        url = bu._exporter_url()
        assert_(url is None,
                f"_exporter_url with no env var and no db.env should return None, got {url!r}")
    finally:
        if saved is not None:
            os.environ["CARR_DB_EXPORTER_URL"] = saved
        if saved_home is not None:
            os.environ["HOME"] = saved_home
        else:
            os.environ.pop("HOME", None)


@case("load_live_rows accepts a program_key argument without crashing")
def _(assert_):
    saved_url = bu._exporter_url
    bu._exporter_url = lambda: None
    try:
        rows = bu.load_live_rows(program_key="carr-ai-engineering-suite-v1")
        assert_(rows is None,
                f"load_live_rows without DB should return None even with program_key, got {rows!r}")
    finally:
        bu._exporter_url = saved_url


# ── any program_key, not only carr-ai-engineering-suite-v1 ─────────────────


@case("detect_built_unclosed works with arbitrary ref names, not only WR-AI-*")
def _(assert_):
    root = tempfile.mkdtemp()
    p = os.path.join(root, "ops/widget.py")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write("x")
    rows = _rows(("WR-WIDGET-001", "ready", ["ops/widget.py"]))
    result = bu.detect_built_unclosed(rows, root)
    assert_([r["ref"] for r in result] == ["WR-WIDGET-001"],
            f"detector must work for any ref, not only WR-AI-*: {result!r}")


# ── root is passed by the caller, never hardcoded ──────────────────────────


@case("detect_built_unclosed uses the root argument, not a hardcoded path")
def _(assert_):
    root_a = tempfile.mkdtemp()
    root_b = tempfile.mkdtemp()
    # Evidence exists under root_a but not root_b
    p = os.path.join(root_a, "ops/ai_eval.py")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write("x")
    rows = _rows(("WR-AI-006", "ready", ["ops/ai_eval.py"]))
    result_a = bu.detect_built_unclosed(rows, root_a)
    result_b = bu.detect_built_unclosed(rows, root_b)
    assert_([r["ref"] for r in result_a] == ["WR-AI-006"],
            f"root_a should detect built_unclosed: {result_a!r}")
    assert_(result_b == [],
            f"root_b should NOT detect built_unclosed (file not there): {result_b!r}")


def main():
    for name, fn in CASES:
        errors = []

        def assert_(cond, msg):
            if not cond:
                errors.append(msg)

        try:
            fn(assert_)
        except Exception as exc:
            errors.append(f"raised {type(exc).__name__}: {exc}")
        if errors:
            print(f"[FAIL] {name}")
            for e in errors:
                print(f"    - {e}")
            failures.append(name)
        else:
            print(f"[PASS] {name}")

    print(f"\n{len(CASES) - len(failures)}/{len(CASES)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
