#!/usr/bin/env python3
"""Pure regressions for rule-classification-parity-check's compare() logic.

Drives compare() directly against in-memory fixtures (never touching the real
ops/config/rule-enforcement-map.json or ops/config/rule-admission-export.v1.json),
the same shape ops/rule-enforcement-map-selftest.py already uses for its sibling
check. A second block drives the real CLI end to end against a throwaway repo
tree, so the file-path wiring (both JSON files, --json output, exit codes) is
exercised too, not just the pure function.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "rule_classification_parity_check",
    os.path.join(REPO, "ops", "rule-classification-parity-check.py"),
)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

MAP_BASE = {
    "schema_version": 1,
    "rule_controls": {
        "aaaaaaaa": {"category": "hard_pre_action"},
        "bbbbbbbb": {"category": "judgment_advisory"},
        "cccccccc": {"category": "session_task_rail"},
    },
}
EXPORT_BASE = {
    "schema_version": 1,
    "rules": {
        "aaaaaaaa": {"enforcement_class": "machine_enforceable", "state": "admitted"},
        "bbbbbbbb": {"enforcement_class": "judgment_advisory", "state": "admitted"},
    },
}


def case(name, map_data, export_data, *, expect_ok, expect_compared=None,
         expect_mismatch_ids=None):
    report = mod.compare(map_data, export_data)
    ok = not report["errors"] and not report["mismatches"]
    passed = ok == expect_ok
    if passed and expect_compared is not None:
        passed = report["compared"] == expect_compared
    if passed and expect_mismatch_ids is not None:
        got_ids = sorted(m["rule_id"] for m in report["mismatches"])
        passed = got_ids == sorted(expect_mismatch_ids)
    print(f"{'PASS' if passed else 'FAIL'}  {name}: {report}")
    return passed


def case_unit_buckets():
    results = [
        mod.file_bucket("hard_pre_action") == "machine_enforceable",
        mod.file_bucket("transactional_schema") == "machine_enforceable",
        mod.file_bucket("post_action_verification") == "machine_enforceable",
        mod.file_bucket("session_task_rail") == "machine_enforceable",
        mod.file_bucket("judgment_advisory") == "judgment_advisory",
        mod.file_bucket("not-a-real-category") is None,
        mod.db_bucket("machine_enforceable") == "machine_enforceable",
        mod.db_bucket("human_only") == "machine_enforceable",
        mod.db_bucket("judgment_advisory") == "judgment_advisory",
        mod.db_bucket("not-a-real-class") is None,
    ]
    ok = all(results)
    print(f"{'PASS' if ok else 'FAIL'}  bucket normalization: {results}")
    return ok


def case_cli_matching_passes():
    return _run_cli(MAP_BASE, EXPORT_BASE, expect_rc=0)


def case_cli_divergent_fails():
    divergent_export = copy.deepcopy(EXPORT_BASE)
    # aaaaaaaa is hard_pre_action (machine_enforceable) in the file, but the
    # export says the database admitted it as judgment_advisory — the exact
    # structural disagreement this check exists to catch.
    divergent_export["rules"]["aaaaaaaa"]["enforcement_class"] = "judgment_advisory"
    return _run_cli(MAP_BASE, divergent_export, expect_rc=1,
                     expect_stdout_contains="aaaaaaaa")


def case_cli_unmapped_export_rule_fails():
    export_with_unknown = copy.deepcopy(EXPORT_BASE)
    export_with_unknown["rules"]["zzzzzzzz"] = {
        "enforcement_class": "machine_enforceable", "state": "admitted",
    }
    return _run_cli(MAP_BASE, export_with_unknown, expect_rc=1,
                     expect_stdout_contains="zzzzzzzz")


def case_cli_empty_export_passes():
    empty_export = {"schema_version": 1, "rules": {}}
    return _run_cli(MAP_BASE, empty_export, expect_rc=0,
                     expect_stdout_contains="0/0")


def with_rule(export_data: dict, rule_id: str, detail: dict) -> dict:
    """A deep copy of export_data with one rules[] entry added or replaced.

    A plain `{**EXPORT_BASE, "rules": {**EXPORT_BASE["rules"], ...}}` spread
    types EXPORT_BASE["rules"] as `object` under mypy (EXPORT_BASE has no
    declared value type), which cannot be unpacked with `**`; this helper
    keeps the fixtures readable without fighting the checker over a literal.
    """
    out = copy.deepcopy(export_data)
    out["rules"][rule_id] = detail
    return out


def _run_cli(map_data, export_data, *, expect_rc, expect_stdout_contains=None):
    tmp = tempfile.mkdtemp(prefix="rule-classification-parity-selftest-")
    try:
        config_dir = os.path.join(tmp, "ops", "config")
        os.makedirs(config_dir, exist_ok=True)
        map_path = os.path.join(config_dir, "rule-enforcement-map.json")
        export_path = os.path.join(config_dir, "rule-admission-export.v1.json")
        with open(map_path, "w", encoding="utf-8") as fh:
            json.dump(map_data, fh)
        with open(export_path, "w", encoding="utf-8") as fh:
            json.dump(export_data, fh)
        # Run the real script with REPO monkeypatched via env: the script
        # resolves MAP/EXPORT from its own file location (REPO/ops/config/...),
        # so drive it by copying the script into the fixture tree's ops/ dir
        # rather than trying to override module-level constants across a
        # subprocess boundary.
        script_src = os.path.join(REPO, "ops", "rule-classification-parity-check.py")
        script_dst = os.path.join(tmp, "ops", "rule-classification-parity-check.py")
        with open(script_src, encoding="utf-8") as fh:
            script_text = fh.read()
        with open(script_dst, "w", encoding="utf-8") as fh:
            fh.write(script_text)
        proc = subprocess.run(
            [sys.executable, script_dst],
            cwd=tmp, capture_output=True, text=True, timeout=30,
        )
        ok = proc.returncode == expect_rc
        if ok and expect_stdout_contains:
            ok = expect_stdout_contains in proc.stdout
        label = f"cli rc={proc.returncode} stdout={proc.stdout.strip()!r}"
        print(f"{'PASS' if ok else 'FAIL'}  {label}")
        return ok
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    partial_map = copy.deepcopy(MAP_BASE)
    partial_controls = partial_map["rule_controls"]
    assert isinstance(partial_controls, dict)
    partial_controls["204391be"] = {"category": "judgment_advisory"}
    partial_export = with_rule(EXPORT_BASE, "204391be",
                               {"enforcement_class": "human_only", "state": "admitted"})
    stale_partial_export = with_rule(EXPORT_BASE, "204391be",
                                     {"enforcement_class": "judgment_advisory",
                                      "state": "admitted"})
    cases = [
        case_unit_buckets(),
        case("matching buckets pass", MAP_BASE, EXPORT_BASE,
             expect_ok=True, expect_compared=2),
        case("divergent bucket fails and names the rule",
             MAP_BASE,
             with_rule(EXPORT_BASE, "aaaaaaaa",
                       {"enforcement_class": "judgment_advisory", "state": "admitted"}),
             expect_ok=False, expect_mismatch_ids=["aaaaaaaa"]),
        case("human_only in the DB matches a machine-enforced file category",
             MAP_BASE,
             with_rule(EXPORT_BASE, "aaaaaaaa",
                       {"enforcement_class": "human_only", "state": "admitted"}),
             expect_ok=True, expect_compared=2),
        case("empty export compares cleanly (nothing synced yet is not a failure)",
             MAP_BASE, {"schema_version": 1, "rules": {}},
             expect_ok=True, expect_compared=0),
        case("export names a rule the file map does not classify at all",
             MAP_BASE,
             with_rule(EXPORT_BASE, "zzzzzzzz",
                       {"enforcement_class": "machine_enforceable", "state": "admitted"}),
             expect_ok=False),
        case("exact partial-admission exception preserves the advisory duty",
             partial_map, partial_export, expect_ok=True, expect_compared=3),
        case("partial-admission exception fails once the database catches up",
             partial_map, stale_partial_export, expect_ok=False, expect_compared=3),
        case_cli_matching_passes(),
        case_cli_divergent_fails(),
        case_cli_unmapped_export_rule_fails(),
        case_cli_empty_export_passes(),
    ]
    print(f"rule-classification-parity-check-selftest: {sum(cases)}/{len(cases)} passed")
    return 0 if all(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
