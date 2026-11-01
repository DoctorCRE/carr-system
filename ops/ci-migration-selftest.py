#!/usr/bin/env python3
"""Characterize migration partitions through the canonical CI command with synthetic programs."""

from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops"))
from git_env import fixture_env

PROOFS = [
    "mcp-server/test/assurance-health-store-postgres.sql",
    "mcp-server/test/catch-me-up-writer-route.test.mjs",
    "mcp-server/test/codex-continuity-postgres.sql",
    "mcp-server/test/completion-tenant-barriers-postgres.sql",
    "mcp-server/test/definer-hardening-catalog-postgres.sql",
    "mcp-server/test/definer-temp-substitution-postgres.sql",
    "mcp-server/test/dispatch-spine-postgres.sql",
    "mcp-server/test/doc-conversation-list-postgres.sql",
    "mcp-server/test/doc-conversation-postgres.sql",
    "mcp-server/test/doc-conversation-write-doors-postgres.sql",
    "mcp-server/test/dot-security-definers-postgres.sql",
    "mcp-server/test/f01-login-bundle-membership-postgres.sql",
    "mcp-server/test/fact-review-installed-postgres.test.mjs",
    "mcp-server/test/find-rule-supersedes.test.mjs",
    "mcp-server/test/foundation-assurance-minimum-postgres.sql",
    "mcp-server/test/governed-correspondence-store-postgres.sql",
    "mcp-server/test/invoice-tracker-postgres.sql",
    "mcp-server/test/invoice-tracker-transaction.test.mjs",
    "mcp-server/test/local-deals-postgres.sql",
    "mcp-server/test/national-account-classification-correction.test.mjs",
    "mcp-server/test/notification-preferences-postgres.sql",
    "mcp-server/test/program-controller-census-postgres.sql",
    "mcp-server/test/program-controller-census-role-boundary.v5.test.mjs",
    "mcp-server/test/r03-loop-queue-postgres.sql",
    "mcp-server/test/r03-notifications-postgres.sql",
    "mcp-server/test/record-source-authority-live-pg.v5.test.mjs",
    "mcp-server/test/rule-context-runtime-postgres.test.mjs",
    "mcp-server/test/security-definer-search-path-postgres.sql",
    "mcp-server/test/session-identity-postgres.sql",
    "mcp-server/test/tour-client-share-allowlist-postgres.sql",
    "mcp-server/test/tour-delivery-data-plane-postgres.sql",
    "mcp-server/test/tour-domain-route-cheat-sheet-postgres.sql",
    "mcp-server/test/tour-operations-slice2-postgres.sql",
    "mcp-server/test/tour-property-identity-jurisdiction-postgres.sql",
    "mcp-server/test/work-portfolio-postgres.sql",
    "mcp-server/test/doc-conversation.test.mjs",
    "mcp-server/test/notifications.test.mjs",
    "mcp-server/test/session-identity.test.mjs",
    "mcp-server/test/dispatch-spine.test.mjs",
    "mcp-server/test/governed-correspondence-thread-postgres.test.mjs",
    "mcp-server/test/meeting-mode.test.mjs",
    "mcp-server/test/amend-closed-loop-postgres.test.mjs",
    "mcp-server/test/independent-review-cycle-postgres.test.mjs",
    "mcp-server/test/a02-rule-enforcement-postgres.test.mjs",
    "mcp-server/test/salesforce-reconciliation-rw02-postgres.test.mjs",
    "mcp-server/test/salesforce-read-run-store-rw02-postgres.test.mjs",
    "mcp-server/test/vendor-directory-postgres.test.mjs",
    "mcp-server/test/relationship-network-postgres.test.mjs",
]


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ci-migration-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("ops", "bin", "tools", "db", "mcp-server/test"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        for name in ("ci.sh", "ci.py", "ci-migration.py"):
            source = ROOT / "ops" / name
            if source.exists():
                shutil.copy2(source, self.root / "ops" / name)
        for name in PROOFS + ["db/schema.sql", "ops/a-gate.py", "ops/b-gate.py"]:
            (self.root / name).write_text("# ci: db-gate\n" if name.endswith("-gate.py") else "")
        shim = self.root / "bin/program"
        shim.write_text(SHIM.replace("REAL_PYTHON", repr(sys.executable)))
        shim.chmod(0o755)
        for name in ("python3", "psql", "node"):
            (self.root / "bin" / name).symlink_to(shim)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.env = dict(
            fixture_env(),
            PATH=str(self.root / "bin") + os.pathsep + os.defpath,
            TRACE=str(self.root / "trace"),
            CARR_CI_DATABASE_URL="postgres://ci@127.0.0.1:55432/ci",
            CARR_SHADOW_ARTIFACT_DIR="",
            RUNNER_TEMP=str(self.root),
        )

    def run_ci(self, part, classes="migration", **env):
        (self.root / "trace").write_text("")
        result = subprocess.run(
            ["/bin/bash", "ops/ci.sh", "--strict", "--only", classes, "--migration-part", part],
            cwd=self.root,
            env=dict(self.env, **env),
            capture_output=True,
            text=True,
            timeout=30,
        )
        trace = [json.loads(line) for line in (self.root / "trace").read_text().splitlines()]
        return result, trace

    def test_partition_commands_match_shell(self):
        for part, expected in BASELINES.items():
            with self.subTest(part=part):
                result, trace = self.run_ci(part)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                digest = hashlib.sha256(json.dumps(trace, sort_keys=True).encode()).hexdigest()
                self.assertEqual(digest, expected, json.dumps(trace))
                self.assertIn("CI passed — every class green", result.stdout)

    def test_core_and_fixtures_run_all_exactly_once(self):
        # The main canary runs these two halves in parallel in place of all.
        runs = {part: self.run_ci(part) for part in ("core", "fixtures", "all")}
        for part, (result, _) in runs.items():
            self.assertEqual(result.returncode, 0, part + result.stdout + result.stderr)
        self.assertEqual(runs["core"][1] + runs["fixtures"][1], runs["all"][1])

    def test_host_shadow_artifact_directory_does_not_change_fixture_commands(self):
        with patch.dict(os.environ, CARR_SHADOW_ARTIFACT_DIR=str(self.root / "host-artifacts")):
            fixture = MigrationTests()
            self.addCleanup(fixture.doCleanups)
            fixture.setUp()
        result, trace = fixture.run_ci("shadow")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        digest = hashlib.sha256(json.dumps(trace, sort_keys=True).encode()).hexdigest()
        self.assertEqual(digest, BASELINES["shadow"], json.dumps(trace))

    def test_explicit_shadow_artifact_directory_reaches_runner(self):
        artifact_dir = "artifacts"
        result, trace = self.run_ci("shadow", CARR_SHADOW_ARTIFACT_DIR=artifact_dir)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        shadow = next(row for row in trace if "ops/migration-shadow.py" in row["argv"])
        self.assertEqual(shadow["argv"], ["python3", "ops/migration-shadow.py", "--artifact-dir", artifact_dir])

    def test_failure_stops_before_next_proof_and_retains_log(self):
        result, trace = self.run_ci("proofs", FAIL_PROGRAM="find-rule-supersedes.test.mjs")
        self.assertEqual(result.returncode, 1)
        self.assertIn("rule lookup or historical supersession SQL database proof failed", result.stdout)
        self.assertFalse(any("catch-me-up-writer-route.test.mjs" in " ".join(row["argv"]) for row in trace))
        logs = list(self.root.glob("carr-ci.*/migration-rule-supersession.log"))
        self.assertEqual(len(logs), 1)
        self.assertIn("synthetic failure", logs[0].read_text())

    def test_fact_review_failure_stops_lane_and_names_reconciliation(self):
        result, trace = self.run_ci("proofs", FAIL_PROGRAM="fact-review-installed-postgres.test.mjs")
        self.assertEqual(result.returncode, 1)
        self.assertIn("reconcile the later function body with the earlier behavior and update the proof", result.stdout)
        self.assertFalse(any("find-rule-supersedes.test.mjs" in row["argv"] for row in trace))
        logs = list(self.root.glob("carr-ci.*/migration-fact-review.log"))
        self.assertEqual(len(logs), 1)
        self.assertIn("synthetic failure", logs[0].read_text())

    def test_empty_shard_setting_keeps_three_shards(self):
        result, trace = self.run_ci("proofs", CARR_DOT_PROOF_SHARDS="")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        digest = hashlib.sha256(json.dumps(trace, sort_keys=True).encode()).hexdigest()
        self.assertEqual(digest, BASELINES["proofs"])

    def test_all_preserves_summary_after_an_earlier_class_fails(self):
        result, _ = self.run_ci("all", classes="unit migration", FAIL_PROGRAM="ops/ci-unit.py")
        self.assertEqual(result.returncode, 1)
        self.assertIn("migration fixture partition all passes", result.stdout)
        self.assertNotIn("committed schema loads;", result.stdout)

    def test_unmarked_database_gate_is_refused(self):
        (self.root / "ops/unmarked-gate.py").write_text('value = os.environ.get("DATABASE_URL")\n')
        result, _ = self.run_ci("gates")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unmarked-gate.py", result.stdout)
        self.assertIn("carry no `# ci: db-gate` marker", result.stdout)

    def test_absent_optional_proofs_are_not_run(self):
        (self.root / "mcp-server/test/rule-context-runtime-postgres.test.mjs").unlink()
        result, trace = self.run_ci("proofs")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(
            any("mcp-server/test/rule-context-runtime-postgres.test.mjs" in row["argv"] for row in trace)
        )

    def test_numeric_report_health_matches_awk(self):
        result, _ = self.run_ci("proofs", LEADING_ZERO_HEALTH="1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_malformed_report_health_fails(self):
        result, _ = self.run_ci("proofs", BAD_HEALTH="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("R03 standing report conflates historical and unresolved failures", result.stdout)

    def test_bootstrap_failure_stops_database_gates(self):
        result, trace = self.run_ci("gates", FAIL_PROGRAM="tools/ops-record.py")
        self.assertEqual(result.returncode, 1)
        self.assertIn("service registry sync failed after migrations", result.stdout)
        self.assertFalse(any("ops/ci-migration-gates.py" in row["argv"] for row in trace))

    def test_restore_security_failure_fails_equivalence(self):
        result, _ = self.run_ci("equivalence", FAIL_PROGRAM="restore-rehearse-record-selftest.py")
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "restore security parity failed against the disposable PostgreSQL fixture", result.stdout
        )

    def test_missing_runner_fails_closed(self):
        (self.root / "ops/ci-migration.py").unlink(missing_ok=True)
        result, _ = self.run_ci("fixtures")
        self.assertEqual(result.returncode, 1)
        self.assertIn("migration runner exited", result.stdout)

    def test_strict_shadow_requires_postgres(self):
        result, _ = self.run_ci("shadow", SHADOW_RC="3")
        self.assertEqual(result.returncode, 1)
        self.assertIn("the migration shadow needs PostgreSQL 18 server binaries", result.stdout)

    def test_non_loopback_database_is_refused(self):
        result, trace = self.run_ci("core", CARR_CI_DATABASE_URL="postgres://ci@invalid.invalid/ci")
        self.assertEqual(result.returncode, 1)
        self.assertIn("REFUSED: CARR_CI_DATABASE_URL is not loopback", result.stdout)
        self.assertFalse(any(row["argv"][0] == "psql" for row in trace))


SHIM = r"""#!REAL_SHEBANG
import json, os, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
if args and args[0].endswith(('ops/ci-migration.py', 'ops/ci.py')):
    os.execv(REAL_PYTHON, [REAL_PYTHON, *args])
if args and args[0].endswith('ops/ci-secret-scan.py'):
    print(sys.stdin.read(), end='')
    raise SystemExit(0)
argv = [Path(sys.argv[0]).name, *args]
argv = ['<logdir>' if '/carr-ci.' in a else a for a in argv]
selected = {k: v for k, v in os.environ.items()
            if k.startswith('CARR_') and (k.endswith('_REQUIRED') or k.endswith('_DATABASE_URL'))
            or k in ('DATABASE_URL', 'PGOPTIONS', 'PSQL')}
with open(os.environ['TRACE'], 'a') as trace:
    log = next((p.name for p in Path.cwd().glob('carr-ci.*/*.log') if p.stat().st_ino == os.fstat(1).st_ino), '')
    trace.write(json.dumps(dict(argv=argv, env=selected, log=log)).replace(str(Path.cwd()), '<root>') + '\n')
if os.environ.get('FAIL_PROGRAM') and os.environ['FAIL_PROGRAM'] in ' '.join(args):
    print('synthetic failure')
    raise SystemExit(9)
if 'ops/migration-shadow.py' in args:
    print('migration-shadow: 2 pending migration(s) applied')
    raise SystemExit(int(os.environ.get('SHADOW_RC', '0')))
if 'tools/migrate.py' in args:
    print('applying fixture')
if any('r03-loop-queue-postgres.sql' in a for a in args):
    print('r03_recovered_health\njoe,' + ('03' if os.environ.get('LEADING_ZERO_HEALTH') else '3') + ',3,3,0,0,0,0,1,0,0,0,0,0,' + ('broken' if os.environ.get('BAD_HEALTH') else 'No open'))
    print('r03_actor_mismatch_health\njoe,4,3,3,0,1,1,1,1,0,0,0,0,0,On missing')
"""
SHIM = SHIM.replace("REAL_SHEBANG", sys.executable)
# Shell captures from 00fa46d7ad5dd89a646cd53874e5ed7455953c4c pin ordered argv,
# synthetic child settings and log names independently of the Python proof table.
BASELINES = {
    "reconstruction": "62125664eea9bc71ea083cbe4b12e300f5d196f0c3ca2508d01320c7997fd1e6",
    "equivalence": "b09903454648438b634b50e5d500f37151daa0f3a0a01d6f94f9aa485d00e29f",
    "shadow": "e152e1e9ff1e9f217bb289391326d3608a9a6296a39fc1c68c1177b79a3f3971",
    "fixtures": "cc6996dc077062e8bc3edcf3fb613c556c140d75eef7d27a61c90845678249e8",
    "proofs": "7188ff4909662444f3b70ba2977c67f528a5ab4ded342852242b6c58a9cbe735",
    "gates": "b56db7dc88a6190a2817d79c4c07e0fac9ca964a615adefb9346f1e6c749665e",
    "acceptance": "f1f0bc65eb63af0eb97d47de87885c239df12bd7c35d6f6edec4ba76c9044cf0",
    "core": "7e4ae96af1e26978d5ba8ea1161beb7930591aafc5b37f5e108f0690ddef34fe",
    "all": "50e961f225baa2c122a59db95e20f7b4108767a6664c8ebe5acc78a2551851ed",
}

if __name__ == "__main__":
    unittest.main()
