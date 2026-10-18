#!/usr/bin/env python3
"""sync-core-rule-ids-selftest.py — the parity suite for
ops/sync-core-rule-ids.py --check (WR-000019 slice S11, boot diet).

Builds its own synthetic triage file and a synthetic generated-JS file (the
established split: this measures the CHECK, never the real repository's
actual triage/generated-file pair). Proves: a freshly-regenerated file
matches and passes; a hand-edited or stale file is caught and fails; the
real repository pair (ops/config/rule-triage.v1.json and
mcp-server/src/core-rule-ids.js) is ALSO checked directly, since that is the
one thing that actually protects doctrine.js from reading a stale list.
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def _load_module(name: str, path: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, \
        f"could not build a module spec for {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync_core_rule_ids = _load_module(
    "sync_core_rule_ids", os.path.join(HERE, "sync-core-rule-ids.py"))

FAILURES: list[str] = []


def check(name, condition):
    if condition:
        print(f"  ok  {name}")
    else:
        print(f"  FAIL  {name}")
        FAILURES.append(name)


SYNTHETIC_TRIAGE = {
    "schema_version": 1,
    "work_request": "WR-TEST",
    "slice": "test",
    "rules": [
        {"id": "AAAAAAAA", "home": "core"},
        {"id": "bbbbbbbb", "home": "core"},
        {"id": "cccccccc", "home": "gate"},
        {"id": "dddddddd", "home": "jit"},
        {"id": "eeeeeeee", "home": "gone"},
    ],
}


def test_fresh_generation_passes_its_own_check():
    tmp = tempfile.mkdtemp(prefix="core-rule-ids-fresh-")
    try:
        triage_path = os.path.join(tmp, "rule-triage.v1.json")
        out_path = os.path.join(tmp, "core-rule-ids.js")
        with open(triage_path, "w") as fh:
            json.dump(SYNTHETIC_TRIAGE, fh)
        rc_write = sync_core_rule_ids.main(["--triage", triage_path, "--out", out_path])
        check("regeneration exits 0", rc_write == 0)
        rc_check = sync_core_rule_ids.main(
            ["--check", "--triage", triage_path, "--out", out_path])
        check("a freshly-generated file passes --check", rc_check == 0)
        with open(out_path) as fh:
            generated = fh.read()
        check("the generated ids are lowercased and sorted",
              '"aaaaaaaa"' in generated and '"bbbbbbbb"' in generated
              and generated.index('"aaaaaaaa"') < generated.index('"bbbbbbbb"'))
        check("a gate/jit/gone id is excluded from the CORE list",
              "cccccccc" not in generated and "dddddddd" not in generated
              and "eeeeeeee" not in generated)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_stale_generated_file_is_caught():
    tmp = tempfile.mkdtemp(prefix="core-rule-ids-stale-")
    try:
        triage_path = os.path.join(tmp, "rule-triage.v1.json")
        out_path = os.path.join(tmp, "core-rule-ids.js")
        with open(triage_path, "w") as fh:
            json.dump(SYNTHETIC_TRIAGE, fh)
        sync_core_rule_ids.main(["--triage", triage_path, "--out", out_path])
        # Simulate the triage gaining a THIRD core rule with no regeneration.
        drifted = dict(SYNTHETIC_TRIAGE)
        drifted["rules"] = [*SYNTHETIC_TRIAGE["rules"],
                             {"id": "ffffffff", "home": "core"}]
        with open(triage_path, "w") as fh:
            json.dump(drifted, fh)
        rc = sync_core_rule_ids.main(["--check", "--triage", triage_path, "--out", out_path])
        check("a triage change with no regeneration fails --check", rc == 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_hand_edit_is_caught():
    tmp = tempfile.mkdtemp(prefix="core-rule-ids-handedit-")
    try:
        triage_path = os.path.join(tmp, "rule-triage.v1.json")
        out_path = os.path.join(tmp, "core-rule-ids.js")
        with open(triage_path, "w") as fh:
            json.dump(SYNTHETIC_TRIAGE, fh)
        sync_core_rule_ids.main(["--triage", triage_path, "--out", out_path])
        with open(out_path, "a") as fh:
            fh.write("\n// a hand edit that never ran the generator\n")
        rc = sync_core_rule_ids.main(["--check", "--triage", triage_path, "--out", out_path])
        check("a hand-edited generated file fails --check", rc == 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_missing_out_file_is_caught():
    tmp = tempfile.mkdtemp(prefix="core-rule-ids-missing-")
    try:
        triage_path = os.path.join(tmp, "rule-triage.v1.json")
        out_path = os.path.join(tmp, "core-rule-ids.js")  # never written
        with open(triage_path, "w") as fh:
            json.dump(SYNTHETIC_TRIAGE, fh)
        rc = sync_core_rule_ids.main(["--check", "--triage", triage_path, "--out", out_path])
        check("a never-generated file fails --check rather than crashing", rc == 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_real_repository_pair_is_currently_in_sync():
    """The one assertion that actually matters day to day: the real checked-in
    pair passes right now. ops/ci.sh invokes the generator with --check."""
    rc = sync_core_rule_ids.main(["--check"])
    check("the real ops/config/rule-triage.v1.json and "
          "mcp-server/src/core-rule-ids.js are in sync right now", rc == 0)


if __name__ == "__main__":
    test_fresh_generation_passes_its_own_check()
    test_stale_generated_file_is_caught()
    test_hand_edit_is_caught()
    test_missing_out_file_is_caught()
    test_real_repository_pair_is_currently_in_sync()
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S): {FAILURES}")
        sys.exit(1)
    print("\nOK: sync-core-rule-ids selftest passed")
    sys.exit(0)
