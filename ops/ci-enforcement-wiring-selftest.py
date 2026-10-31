#!/usr/bin/env python3
"""CI-declared Python checkers must run outside their fixture selftests."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import unittest

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ci_selftest", REPO / "ops/ci-selftest.py")
if spec is None or spec.loader is None:
    raise ImportError("Cannot load CI fixture module")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def declared_checks(catalog):
    return [(name, path) for name, control in catalog.items()
            if re.search(r"\bCI\b", control.get("failure_mode", ""))
            for path in control.get("implementation", [])
            if path.startswith("ops/") and path.endswith(".py")]


def unwired_checks(catalog, mutate=lambda root: None):
    missing = []
    for name, path in declared_checks(catalog):
        with fixture.ci_fixture() as root:
            # Evidence hashes the actual CI contract, even in a synthetic checkout.
            for rel in (".github/workflows/ci.yml", "ops/ci-evidence.py", "ops/ci-unit.py",
                        "ops/config/ci-unit-durations.json", "ops/ci-node-file.mjs", "ops/ci_unit_shards.py", "mcp-server/run-tests.mjs",
                        "mcp-server/package-lock.json", ".nvmrc", ".python-version"):
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(REPO / rel, target)
            for rel in ("ops/migration-open-pr-gate.py", "ops/migration-order-gate.py", "tools/migrate.py"):
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("pass\n")
            (root / "db").mkdir()
            (root / "db/schema.sql").touch()
            psql = root / "bin/psql"
            psql.write_text("#!/bin/sh\nexit 0\n")
            psql.chmod(0o755)
            checker = root / path
            checker.parent.mkdir(parents=True, exist_ok=True)
            marker = "catalog-checker-executed"
            checker.write_text(f"print({marker!r})\nraise SystemExit(1)\n")
            mutate(root)
            records = root / "programs.json"
            env = fixture.fixture_env()
            env.update(CARR_CI_DATABASE_URL="postgres://fixture@localhost/fixture",
                       CARR_CI_PROGRAM_RECORDS=str(records), RUNNER_TEMP=str(root),
                       NODE_COMPILE_CACHE=str(root / "node-compile-cache"),
                       PATH=str(root / "bin") + os.pathsep + os.environ["PATH"])
            result = subprocess.run([str(root / "ops/ci.sh"), "--migration-part", "acceptance"],
                                    cwd=root, env=env, capture_output=True, text=True, timeout=60)
            output = result.stdout + result.stderr
            rows = json.loads(records.read_text())["programs"] if records.exists() else []
            # Failed CI retains logs, so the marker identifies the row that ran this stub.
            logdir = re.search(r"ci-retained-logs: (.+)", output)
            matched = False
            if logdir:
                logs = Path(logdir.group(1))
                matched = any(row["exit"] == 1 and "selftest" not in row["program"]
                              and (logs / (row["program"] + ".log")).is_file()
                              and marker in (logs / (row["program"] + ".log")).read_text()
                              for row in rows)
            if not matched:
                missing.append(f"{name}: {path}")
    return sorted(missing)


class EnforcementWiringTest(unittest.TestCase):
    catalog = {"example": {"failure_mode": "CI class failure",
                           "implementation": ["ops/trigger-grant-check.py"]}}

    def test_current_map_has_no_unwired_ci_checkers(self):
        mapping = json.loads((REPO / "ops/config/rule-enforcement-map.json").read_text())
        for name, path in declared_checks(mapping["control_catalog"]):
            self.assertTrue((REPO / path).is_file(), f"{name}: missing {path}")
        self.assertEqual([], unwired_checks(mapping["control_catalog"]))

    def test_unwired_checker_is_rejected(self):
        catalog = {"example": {"failure_mode": "CI failure",
                               "implementation": ["ops/example-check.py"]}}
        self.assertEqual(["example: ops/example-check.py"], unwired_checks(catalog))

    def test_direct_checker_in_another_class_is_observed(self):
        catalog = {"example": {"failure_mode": "CI failure",
                               "implementation": ["ops/example-check.py"]}}
        def mutate(root):
            ci = root / "ops/ci.sh"
            ci.write_text(ci.read_text().replace("check_dependency() {",
                'check_dependency() {\n  run_quiet "$LOGDIR/example.log" "$PY" ops/example-check.py'))
        self.assertEqual([], unwired_checks(catalog, mutate))

    def test_guarded_checker_is_not_reachable(self):
        def mutate(root):
            runner = root / "ops/ci-migration.py"
            source = runner.read_text().replace('        self.py(\n            "migration-triggers",',
                '        if False: self.py(\n            "migration-triggers",')
            runner.write_text(source)
        self.assertEqual(["example: ops/trigger-grant-check.py"], unwired_checks(self.catalog, mutate))

    def test_selftest_execution_does_not_prove_checker_wiring(self):
        def mutate(root):
            runner = root / "ops/ci-migration.py"
            runner.write_text(runner.read_text().replace('        self.py(\n            "migration-triggers",',
                '        if False: self.py(\n            "migration-triggers",'))
            (root / "ops/proxy-selftest.py").write_text(
                'import subprocess, sys\nraise SystemExit(subprocess.call([sys.executable, "ops/trigger-grant-check.py"]))\n')
        self.assertEqual(["example: ops/trigger-grant-check.py"], unwired_checks(self.catalog, mutate))

    def test_inactive_class_is_not_reachable(self):
        def mutate(root):
            ci = root / "ops/ci.sh"
            ci.write_text(ci.read_text().replace("dependency migration binding", "dependency binding"))
        self.assertEqual(["example: ops/trigger-grant-check.py"], unwired_checks(self.catalog, mutate))

    def test_commented_call_is_not_reachable(self):
        def mutate(root):
            runner = root / "ops/ci-migration.py"
            source = runner.read_text()
            start = source.index('        self.py(\n            "migration-triggers",')
            end = source.index('        self.step("triggers")', start)
            runner.write_text(source[:start] + "".join("#" + line for line in source[start:end].splitlines(keepends=True)) + source[end:])
        self.assertEqual(["example: ops/trigger-grant-check.py"], unwired_checks(self.catalog, mutate))


if __name__ == "__main__":
    unittest.main()
