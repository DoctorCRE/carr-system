#!/usr/bin/env python3
"""Review admission replays, using isolated Git repositories and real CLI seams.

Owns its temporary repositories, body files and check artifacts. No network,
production data, shared scratch state or paid model calls are used.
"""
from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from git_env import fixture_env

ROOT = Path(__file__).resolve().parents[1]


def copy_ci(root):
    for relative in ("ops/ci.sh", "ops/ci-quarantine.py", "ops/git_env.py",
                     "ops/config/ci-quarantine.json", "ops/config/ci-check-scope.json"):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / relative, target)
    if not (root / ".git").exists():
        env = fixture_env()
        for arguments in (("init", "-q", "-b", "main"),
                          ("config", "user.name", "Fixture"),
                          ("config", "user.email", "64207374+jbookout@users.noreply.github.com"),
                          ("add", "ops")):
            subprocess.run(["git", *arguments], cwd=root, env=env, check=True,
                           capture_output=True)
        message = root / ".git/fixture-message"
        message.write_text("CI fixture\n")
        subprocess.run(["git", "commit", "-q", "-F", str(message)], cwd=root, env=env,
                       check=True, capture_output=True)


class CheckArtifacts(unittest.TestCase):
    def test_eval_caller_and_inherited_probe_receive_identical_body_arguments(self):
        self.enterContext(patch.dict(os.environ, CARR_PR_BODY_FILE='outside-fixture-body'))
        with tempfile.TemporaryDirectory(prefix="review-floor-caller-") as td:
            root = Path(td)
            for folder in ["ops", "hooks"]:
                (root / folder).mkdir()
            copy_ci(root)
            (root / "hooks/gate-integrity.py").write_text("raise SystemExit(0)\n")
            (root / "ops/check-eval-receipt.py").write_text(
                "import sys\nfrom pathlib import Path\nPath('eval-args').write_text(' '.join(sys.argv[1:]))\nraise SystemExit(1 if '--pr-body-file' in sys.argv else 0)\n")
            (root / "ops/inherited-from-main.py").write_text(
                "import sys\nfrom pathlib import Path\nPath('probe-args').write_text(' '.join(sys.argv[1:]))\nraise SystemExit(1)\n")
            body = root / "body"
            body.write_text("no-eval: fixture: invalid\n")
            env = fixture_env()
            env.pop('CARR_PR_BODY_FILE', None)
            for supplied in [False, True]:
                with self.subTest(supplied=supplied):
                    argv = ["bash", str(root / "ops/ci.sh"), "--strict", "--only", "gates"]
                    if supplied:
                        argv += ["--pr-body-file", str(body)]
                    run = subprocess.run(argv, cwd=root, env=env, capture_output=True, text=True)
                    self.assertNotIn("unbound variable", run.stdout + run.stderr)
                    self.assertTrue((root / "eval-args").exists())
                    if supplied:
                        self.assertNotEqual(run.returncode, 0)
                        self.assertIn("--pr-body-file " + str(body), (root / "probe-args").read_text())
                    else:
                        self.assertEqual((root / "eval-args").read_text(), "")

    def test_ci_artifact_preserves_pass_refusal_and_strict_skip(self):
        with tempfile.TemporaryDirectory(prefix="review-floor-ci-") as td:
            root = Path(td)
            (root / "ops").mkdir()
            copy_ci(root)
            result = root / "result.json"
            for rc, expected in [(0, "passed"), (1, "refused"), (78, "refused")]:
                with self.subTest(rc=rc):
                    (root / "ops/stale-config-check.py").write_text(f"raise SystemExit({rc})\n")
                    run = subprocess.run(["bash", str(root / "ops/ci.sh"), "--strict", "--only",
                                          "freshness", "--result-file", str(result)],
                                         env=fixture_env(), capture_output=True, text=True)
                    self.assertTrue(result.exists(), run.stdout + run.stderr)
                    artifact = json.loads(result.read_text())
                    self.assertEqual(artifact["classes"][0]["status"], expected)
                    self.assertEqual(artifact["classes"][0]["checks"], 1)
                    self.assertEqual(run.returncode == 0, rc == 0)

    def test_gate_suite_declining_with_exit_78_is_partial_coverage_not_a_pass(self):
        # Real producer: the actual gates loop and timeout helper, one baseline
        # check passing and one selftest declining to run.
        spec = importlib.util.spec_from_file_location("review_evidence", ROOT / "ops/local-review-evidence.py")
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        with tempfile.TemporaryDirectory(prefix="review-floor-gates-") as td:
            root = Path(td)
            for folder in ["ops", "hooks", "bin"]:
                (root / folder).mkdir()
            copy_ci(root)
            shutil.copy(ROOT / "bin/with-timeout.py", root / "bin/with-timeout.py")
            (root / "hooks/gate-integrity.py").write_text("raise SystemExit(0)\n")
            result = root / "result.json"
            for rc, expected in [(0, "passed"), (78, "partial")]:
                with self.subTest(rc=rc):
                    (root / "ops/fixture-selftest.py").write_text(f"print('fixture'); raise SystemExit({rc})\n")
                    run = subprocess.run(["bash", str(root / "ops/ci.sh"), "--strict", "--only", "gates",
                                          "--result-file", str(result)], env=fixture_env(),
                                         capture_output=True, text=True)
                    # Ordinary CI policy is unchanged: exit 78 still exits zero.
                    self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                    artifact = json.loads(result.read_text())
                    self.assertEqual(artifact["classes"][0]["status"], expected)
                    if rc:
                        with self.assertRaises(adapter.Refusal):
                            adapter.validate_ci(artifact, ["gates"])
                    else:
                        adapter.validate_ci(artifact, ["gates"])

    def test_inherited_main_abort_never_publishes_a_passing_result(self):
        with tempfile.TemporaryDirectory(prefix="review-floor-inherited-") as td:
            root = Path(td)
            for folder in ["ops", "hooks"]:
                (root / folder).mkdir()
            copy_ci(root)
            (root / "hooks/gate-integrity.py").write_text("raise SystemExit(1)\n")
            (root / "ops/inherited-from-main.py").write_text("print('INHERITED FROM MAIN: seeded baseline failure')\n")
            result = root / "result.json"
            run = subprocess.run(["bash", str(root / "ops/ci.sh"), "--strict", "--only", "gates",
                                  "--result-file", str(result)], env=fixture_env(), capture_output=True, text=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("ci-inherited-from-main:gate-integrity", run.stdout)
            self.assertFalse(result.exists())

    def test_quarantined_python_and_shell_suites_publish_both_diagnostics(self):
        with tempfile.TemporaryDirectory(prefix="review-quarantined-logs-") as td:
            root = Path(td)
            copy_ci(root)
            (root / 'hooks').mkdir()
            (root / 'hooks/gate-integrity.py').write_text('raise SystemExit(0)\n')
            (root / 'bin').mkdir()
            shutil.copy(ROOT / 'bin/with-timeout.py', root / 'bin/with-timeout.py')
            shutil.copy(ROOT / 'ops/ci-secret-scan.py', root / 'ops/ci-secret-scan.py')
            (root / 'tools').mkdir()
            for name, command in [('ops/fixture-selftest.py', 'print("python diagnostic"); raise SystemExit(1)\n'),
                                  ('tools/test-fixture.sh', '#!/bin/sh\necho "shell diagnostic"\nexit 1\n')]:
                (root / name).write_text(command)
                (root / name).chmod(0o755)
            entries = [{'test': name, 'owner': 'qa-engineer', 'expires': '2099-01-01', 'reason': 'Fixture',
                'loop': 'https://github.com/jbookout/carr-system/issues/123'}
                for name in ('ops/fixture-selftest.py', 'tools/test-fixture.sh')]
            (root / 'ops/config/ci-quarantine.json').write_text(json.dumps({'version': 1, 'tests': entries}))
            run = subprocess.run(['bash', str(root / 'ops/ci.sh'), '--strict', '--only', 'gates'],
                env=fixture_env(), capture_output=True, text=True, timeout=20)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertEqual(run.stdout.count('python diagnostic'), 2, run.stdout)
            self.assertEqual(run.stdout.count('shell diagnostic'), 2, run.stdout)



class Admission(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("review_evidence", ROOT / "ops/local-review-evidence.py")
        self.adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.adapter)
        self.tmp = tempfile.TemporaryDirectory(prefix="review-floor-source-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        self.env = fixture_env()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Fixture")
        for folder in ["ops", "evals", "hooks", "migrations", "mcp-server/src"]:
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / "evals/surfaces.json", self.root / "evals/surfaces.json")
        (self.root / "README.md").write_text("base\n")
        (self.root / "migrations/0001_seed.sql").write_text("SELECT 1;\n")
        (self.root / "ops/ci.sh").write_text('''#!/bin/bash
CLASS_ORDER="pushfloor unit types contract gates secret dependency migration binding artifact freshness"
while [ "$#" -gt 0 ]; do
 case "$1" in
 --only) classes=$2; shift;; --result-file) result=$2; shift;;
 esac
 shift
done
python3 - "$result" "$classes" <<'RESULT'
import json, sys
json.dump({"schema":"carr-ci-result/v1","strict":True,"classes":[
 {"name":c,"status":"passed","checks":1} for c in sys.argv[2].split(",")]},open(sys.argv[1],"w"))
RESULT
''')
        (self.root / "ops/scac-mutation-inventory.mjs").write_text(
            "export function assertCurrentSourceInventoryMatchesFixture() {}\n")
        (self.root / "mcp-server/src/tools.js").write_text("export const TOOLS = {};\n")
        self.git("add", "README.md", "ops", "evals", "migrations", "mcp-server")
        self.git("commit", "-qm", "base")
        self.git("update-ref", "refs/remotes/origin/main", self.git("rev-parse", "HEAD"))
        self.git("checkout", "-qb", "work")
        self.edit("README.md", "candidate\n")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, env=self.env,
                              capture_output=True, text=True, check=True).stdout.strip()

    def edit(self, path, text):
        (self.root / path).write_text(text)
        self.git("add", path)
        self.git("commit", "-qm", "candidate")

    def collect(self, body=""):
        return self.adapter.collect(self.root, "origin/main", body)

    def test_clean_small_floor_and_each_binding_substitution(self):
        receipt = self.collect()
        self.assertEqual(receipt["classes"], ["pushfloor", "secret", "freshness"])
        result = self.adapter.verify(self.root, "origin/main", "", receipt)
        self.assertEqual(result["local_floor"], "passed")
        self.assertIn("ops/ci.sh --strict", result["required_hosted_checks"])
        self.assertFalse(result["merge_ready"])
        for binding in ["head", "tree", "base", "environment", "checker_revision", "body_sha256"]:
            with self.subTest(binding=binding):
                changed = json.loads(json.dumps(receipt))
                changed["binding"][binding] = "substituted"
                with self.assertRaises(self.adapter.Refusal):
                    self.adapter.verify(self.root, "origin/main", "", changed)

    def test_missing_changed_receipt_and_body_only_invalid_exception_use_real_oracle(self):
        self.edit("AGENTS.md", "changed instructions\n")
        with self.assertRaises(self.adapter.Refusal):
            self.collect()
        body = "no-eval: session-instructions: The live service is unavailable to this offline fixture, so no representative model outcome can be measured."
        receipt = self.collect(body)
        self.adapter.verify(self.root, "origin/main", body, receipt)
        invalid = "no-eval: session-instructions: trivial"
        with self.assertRaises(self.adapter.Refusal):
            self.adapter.verify(self.root, "origin/main", invalid, receipt)
        # Even substituting the current body hash cannot bypass the eval oracle.
        receipt["binding"]["body_sha256"] = self.adapter.digest(invalid.encode())
        with self.assertRaises(self.adapter.Refusal):
            self.adapter.verify(self.root, "origin/main", invalid, receipt)

    def test_actual_platform_source_and_current_main_movement_invalidate(self):
        receipt = self.collect()
        receipt["binding"]["environment"]["system"] = "incompatible-worker"
        with self.assertRaises(self.adapter.Refusal):
            self.adapter.verify(self.root, "origin/main", "", receipt)
        receipt = self.collect()
        self.edit("README.md", "next head\n")
        with self.assertRaises(self.adapter.Refusal):
            self.adapter.verify(self.root, "origin/main", "", receipt)
        self.git("update-ref", "refs/remotes/origin/main", self.git("rev-parse", "HEAD"))
        with self.assertRaises(self.adapter.Refusal):
            self.adapter.verify(self.root, "origin/main", "", receipt)

    def test_inherited_red_or_partial_artifact_cannot_become_pass_even_at_exit_zero(self):
        for status in ["refused", "partial", "unknown"]:
            with self.subTest(status=status):
                self.edit("ops/ci.sh", (self.root / "ops/ci.sh").read_text().replace('"status":"passed"', f'"status":"{status}"'))
                with self.assertRaises(self.adapter.Refusal):
                    self.collect()
                self.edit("ops/ci.sh", (self.root / "ops/ci.sh").read_text().replace(f'"status":"{status}"', '"status":"passed"'))

    def test_migration_union_and_source_seal_are_executed_before_ci(self):
        self.edit("migrations/0001_collision.sql", "SELECT 2;\n")
        with self.assertRaises(self.adapter.Refusal):
            self.collect()
        # Independent control: no collision, then only the seal is broken.
        self.git("rm", "migrations/0001_collision.sql")
        self.git("commit", "-qm", "remove fixture collision")
        self.collect()
        self.edit("ops/scac-mutation-inventory.mjs", "export function assertCurrentSourceInventoryMatchesFixture() { throw Error('mismatch'); }\n")
        with self.assertRaises(self.adapter.Refusal):
            self.collect()

    def test_result_fault_matrix_and_sensitive_sink(self):
        healthy = (self.root / "ops/ci.sh").read_text()
        body = "fixture-client-canary https://fixture.invalid/?token=canary-secret\nquoted='credential-canary'"
        receipt = self.collect(body)
        for canary in ["fixture-client-canary", "canary-secret", "credential-canary"]:
            self.assertNotIn(canary, json.dumps(receipt))
        inventory = next(line for line in healthy.splitlines() if line.startswith("CLASS_ORDER="))
        for script in ["exit 0", "exit 1", "exit 75", "echo 'canary-secret' >&2; exit 1",
                       healthy.replace('"checks":1', '"checks":0'),
                       healthy.replace('"classes":[', '"classes":[').replace('for c in sys.argv[2].split(",")', 'for c in []')]:
            with self.subTest(script=script[:20]):
                payload = "\n".join(line for line in script.splitlines()
                                    if not line.startswith(("#!/", "CLASS_ORDER=")))
                self.edit("ops/ci.sh", "#!/bin/bash\n" + inventory + "\necho exercised > ci-exercised\n" + payload)
                with self.assertRaises(self.adapter.Refusal) as err:
                    self.collect(body)
                self.assertTrue((self.root / "ci-exercised").exists())
                (self.root / "ci-exercised").unlink()
                self.assertNotIn("canary-secret", str(err.exception))
        self.edit("ops/ci.sh", healthy)
        self.collect()

    def test_terminal_transport_exception_is_a_refusal_with_no_error_payload(self):
        # External process transport is the varied dependency at this seam.
        with patch("subprocess.run", side_effect=OSError("credential-canary")):
            with self.assertRaises(self.adapter.Refusal) as caught:
                self.adapter.run(self.root, ["unavailable-external-command"])
        self.assertNotIn("credential-canary", str(caught.exception))

    def test_source_movement_during_eval_readback_invalidates_admission(self):
        receipt = self.collect()
        real_run = subprocess.run
        def concurrent_writer(argv, **kwargs):
            result = real_run(argv, **kwargs)
            if any(str(arg).endswith("check-eval-receipt.py") for arg in argv):
                (self.root / "README.md").write_text("changed during readback\n")
            return result
        with patch("subprocess.run", side_effect=concurrent_writer):
            with self.assertRaises(self.adapter.Refusal):
                self.adapter.verify(self.root, "origin/main", "", receipt)

    def test_index_suppressed_tracked_bytes_cannot_reuse_evidence(self):
        path = "ops/scac-mutation-inventory.mjs"
        healthy = (self.root / path).read_text()
        receipt = self.collect()
        for flag in ["--assume-unchanged", "--skip-worktree"]:
            with self.subTest(flag=flag):
                self.git("update-index", flag, path)
                (self.root / path).write_text(
                    "export function assertCurrentSourceInventoryMatchesFixture() { throw Error('mismatch'); }\n")
                self.assertEqual(self.git("status", "--porcelain", "--untracked-files=no"), "")
                with self.assertRaises(self.adapter.Refusal):
                    self.adapter.verify(self.root, "origin/main", "", receipt)
                (self.root / path).write_text(healthy)
                self.git("update-index", flag.replace("--", "--no-", 1), path)
        self.adapter.verify(self.root, "origin/main", "", receipt)

    def test_untracked_runtime_source_after_collection_invalidates(self):
        receipt = self.collect()
        (self.root / "mcp-server/src/injected.js").write_text("export const injected = true;\n")
        with self.assertRaises(self.adapter.Refusal):
            self.adapter.verify(self.root, "origin/main", "", receipt)
        (self.root / "mcp-server/src/injected.js").unlink()
        self.adapter.verify(self.root, "origin/main", "", receipt)

    def test_alternate_root_ci_interpreter_and_its_replacement_are_bound(self):
        # .venv is ignored, so only the runtime binding can see this movement.
        self.edit(".gitignore", ".venv/\n")
        receipt = self.collect()
        venv = self.root / ".venv/bin/python"
        venv.parent.mkdir(parents=True)
        venv.symlink_to(sys.executable)
        with self.assertRaises(self.adapter.Refusal):  # PATH python3 -> root venv
            self.adapter.verify(self.root, "origin/main", "", receipt)
        receipt = self.collect()
        venv.unlink()
        venv.write_text(f"#!/bin/sh\nexec {sys.executable} \"$@\"\n")
        venv.chmod(0o755)
        with self.assertRaises(self.adapter.Refusal):  # same version, different interpreter
            self.adapter.verify(self.root, "origin/main", "", receipt)

    def test_inherited_scan_range_cannot_narrow_the_real_secret_scan(self):
        copy_ci(self.root)
        shutil.copy(ROOT / "ops/ci-secret-scan.py", self.root / "ops/ci-secret-scan.py")
        (self.root / "bin").mkdir()
        shutil.copy(ROOT / "bin/with-timeout.py", self.root / "bin/with-timeout.py")
        for stub in ["hooks/gate-integrity.py", "ops/no-client-deliverables-gate.py", "ops/stale-config-check.py"]:
            (self.root / stub).write_text("raise SystemExit(0)\n")
        self.git("add", "ops", "bin", "hooks")
        self.git("commit", "-qm", "real floor")
        self.git("update-ref", "refs/remotes/origin/main", self.git("rev-parse", "HEAD"))
        self.edit("README.md", "clean candidate\n")
        with patch.dict(os.environ, {"CARR_CI_RANGE": "HEAD..HEAD"}):
            receipt = self.collect()  # control: a clean tree passes the full scan
        self.adapter.verify(self.root, "origin/main", "", receipt)
        self.edit("README.md", "-----BEGIN " + "OPENSSH PRIVATE KEY-----\n")
        with patch.dict(os.environ, {"CARR_CI_RANGE": "HEAD..HEAD"}):
            with self.assertRaises(self.adapter.Refusal):
                self.collect()
        with self.assertRaises(self.adapter.Refusal):
            self.collect()

    def test_missing_seal_acknowledgement_is_refused_independently(self):
        self.collect()
        with self.assertRaises(self.adapter.Refusal):
            self.adapter.checked(self.root, "source-seal", ["node", "-e", ""], "source-seal: OK")

    def test_timeout_disposes_owned_descendants(self):
        pidfile = Path(self.tmp.name) / "child.pid"
        script = ("import subprocess,sys,time; from pathlib import Path; "
                  "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],"
                  "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
                  "Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(30)")
        pid = None
        try:
            with self.assertRaises(self.adapter.Refusal):
                self.adapter.run(self.root, [sys.executable, "-c", script, str(pidfile)], timeout=1)
            pid = int(pidfile.read_text())
            state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                                   capture_output=True, text=True).stdout.strip()
            self.assertTrue(not state or state.startswith("Z"), "timed-out check left a live descendant")
        finally:
            if pid is None and pidfile.exists():
                pid = int(pidfile.read_text())
            if pid is not None:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

    def test_unknown_and_broad_selection_keep_broader_checks(self):
        # Spelled in pieces: this file names paths in strings, so a literal
        # name would make this suite a textual consumer of it.
        for paths in [["unclassified/thing"], ["README.md"] * 31, ["ops/ci.sh"], [".github/workflows/ci.yml"],
                      ["ops/" + "unclassified" + "-runtime.mjs"]]:
            self.assertEqual(self.adapter.select_classes(ROOT, paths), self.adapter.class_order(ROOT))
        self.assertIn("migration", self.adapter.select_classes(ROOT, ["migrations/0002_fixture.sql"]))
        self.assertIn("unit", self.adapter.select_classes(ROOT, ["mcp-server/src/fixture.js"]))

    def test_direct_canonical_consumers_select_the_class_that_executes_them(self):
        # ci.sh names these runners in the classes that execute them; a directory
        # prefix or a paired selftest is not a dependency closure.
        for path, owner in [("tools/migrate.py", "migration"), ("tools/release-manifest.py", "artifact"),
                            ("tools/doctorcre-artifact.py", "artifact")]:
            with self.subTest(path=path):
                self.assertIn(owner, self.adapter.select_classes(ROOT, [path]))

    def test_selection_narrows_only_to_verified_consumers(self):
        # Fixture inventory: a gate selftest glob and a named migration runner.
        self.edit("ops/ci.sh", (self.root / "ops/ci.sh").read_text() + (
            "check_gates() {\n  for t in tools/test-*.py; do python3 \"$t\"; done\n}\n"
            "check_migration() {\n  python3 tools/runner.py\n}\n"))
        (self.root / "tools").mkdir()
        (self.root / "tools/leaf.py").write_text("VALUE = 1\n")
        (self.root / "tools/test-leaf.py").write_text("import leaf\n")
        (self.root / "tools/helper.py").write_text("VALUE = 2\n")
        (self.root / "tools/runner.py").write_text("import helper\n")
        (self.root / "tools/orphan.py").write_text("VALUE = 3\n")
        self.git("add", "tools")
        self.git("commit", "-qm", "tools")
        order = self.adapter.class_order(self.root)
        self.assertEqual(self.adapter.select_classes(self.root, ["tools/leaf.py"]),
                         [c for c in order if c in {"pushfloor", "types", "gates", "secret", "freshness"}])
        # Imported by a runner the migration class names: ci.sh is in its closure.
        self.assertEqual(self.adapter.select_classes(self.root, ["tools/helper.py"]), order)
        # Executed by nothing ci.sh can see: no verified coverage, so everything.
        self.assertEqual(self.adapter.select_classes(self.root, ["tools/orphan.py"]), order)

    def test_installed_unit_package_dependency_movement_invalidates(self):
        (self.root / "practice-plugin").mkdir()
        self.edit("practice-plugin/package-lock.json", '{"lockfileVersion": 3}')
        receipt = self.collect()
        lock = self.root / "practice-plugin/node_modules/.package-lock.json"
        lock.parent.mkdir(parents=True)
        lock.write_text('{"new-version": "fixture"}')
        with self.assertRaises(self.adapter.Refusal):
            self.adapter.verify(self.root, "origin/main", "", receipt)

    def test_broad_coverage_uses_canonical_ci_class_inventory(self):
        self.edit("ops/ci.sh", (self.root / "ops/ci.sh").read_text().replace(' artifact freshness"', ' artifact freshness newclass"'))
        self.edit("unclassified-file", "unknown impact\n")
        self.assertIn("newclass", self.collect()["classes"])

    def test_ci_result_schema_rejects_contradictory_or_extra_fields(self):
        receipt = self.collect()
        receipt["ci"]["classes"][0]["failure"] = "hidden refusal"
        with self.assertRaises(self.adapter.Refusal):
            self.adapter.verify(self.root, "origin/main", "", receipt)

    def test_live_admission_cli_rechecks_provider_head_base_and_body_without_leaks(self):
        provider = Path(self.tmp.name) / "provider.json"
        bin_dir = Path(self.tmp.name) / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text("#!/usr/bin/env python3\nimport os\nfrom pathlib import Path\nprint(Path(os.environ['FIXTURE_PR']).read_text())\n")
        gh.chmod(0o755)
        data = {"number": 7, "state": "open", "body": "", "head": {
            "sha": self.git("rev-parse", "HEAD"), "repo": {"full_name": "jbookout/carr-system"}},
            "base": {"sha": self.git("rev-parse", "origin/main"), "ref": "main",
                     "repo": {"full_name": "jbookout/carr-system"}}}
        receipt = Path(self.tmp.name) / "receipt.json"
        with patch.dict(os.environ, {"PATH": str(bin_dir) + os.pathsep + os.environ["PATH"], "FIXTURE_PR": str(provider),
                                     "CARR_JEV_OFFLINE": "1",
                                     "CARR_CI_PYTHON": str(ROOT / ".venv/bin/python") if (ROOT / ".venv/bin/python").is_file() else "python3"}):
            receipt.write_text(json.dumps(self.collect()))
            argv = ["bash", str(ROOT / "ops/ci.sh"), "--review-admit", "--root", str(self.root),
                    "--receipt", str(receipt), "--pr", "7"]
            provider.write_text(json.dumps(data))
            healthy = subprocess.run(argv, capture_output=True, text=True)
            self.assertEqual(healthy.returncode, 0, healthy.stdout + healthy.stderr)
            self.assertEqual(json.loads(healthy.stdout)["preflight"], "partial")
            cases = ["", "{}", "null", json.dumps({**data, "state": "closed"}),
                     json.dumps({**data, "body": "secret-canary"}),
                     json.dumps({**data, "head": {**data["head"], "sha": "a" * 40}}),
                     json.dumps({**data, "base": {**data["base"], "sha": "a" * 40}})]
            for response in cases:
                with self.subTest(response=response[:25]):
                    provider.write_text(response)
                    refused = subprocess.run(argv, capture_output=True, text=True)
                    self.assertNotEqual(refused.returncode, 0)
                    self.assertNotIn("secret-canary", refused.stdout + refused.stderr)
                    self.assertEqual(json.loads(refused.stdout)["local_floor"], "refused")
            provider.write_text(json.dumps(data))  # reopened on unchanged exact evidence
            self.assertEqual(subprocess.run(argv, capture_output=True, text=True).returncode, 0)

    def test_cli_persists_only_private_data_evidence_and_keeps_old_receipt_on_refusal(self):
        body = Path(self.tmp.name) / "body"
        receipt = Path(self.tmp.name) / "receipt"
        canaries = ["client-canary", "https://user:password-canary@fixture.invalid/?key=query-canary",
                    '"credential":"quoted-canary"', "multiline-canary\nsecret-canary",
                    '{"identity":{"email":"nested-canary@fixture.invalid"}}']
        body.write_text("\n".join(canaries))
        argv = ["bash", str(ROOT / "ops/ci.sh"), "--review-floor", "--root", str(self.root),
                "--body-file", str(body), "--receipt", str(receipt)]
        run = subprocess.run(argv, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        raw = receipt.read_bytes()
        self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
        for value in ["client-canary", "password-canary", "query-canary", "quoted-canary", "secret-canary", "nested-canary"]:
            self.assertNotIn(value.encode(), raw)
            self.assertNotIn(value, run.stdout + run.stderr)
        self.edit("AGENTS.md", "missing changed eval receipt\n")
        refused = subprocess.run(argv, capture_output=True, text=True)
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(receipt.read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
