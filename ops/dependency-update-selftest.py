#!/usr/bin/env python3
"""Exercise the lock-update repair through its command-line interface."""
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = pathlib.Path("control-room/contracts/fixtures/execution-fabric/assurance-compiler.valid.v1.json")
SCRIPT = ROOT / "ops/dependency-update-repin.py"
RENOVATE_VERSION = "44.138.0"

# Drives Renovate's own advisory-rule, flatten and label code over one pytest
# update so the labels asserted below are the ones Renovate would put on the PR.
RENOVATE_LABELS = r"""
import { readFileSync } from "node:fs";
const [dist, configPath] = process.argv.slice(1);
const load = (path) => import(`${dist}/${path}`);
const { getConfig } = await load("config/defaults.js");
const { mergeChildConfig } = await load("config/utils.js");
const { Vulnerabilities } = await load("workers/repository/process/vulnerabilities.js");
const { flattenUpdates } = await load("workers/repository/updates/flatten.js");
const { prepareLabels } = await load("workers/repository/update/pr/labels.js");
(await load("modules/platform/index.js")).setPlatformApi("github");

async function update(updateType, newVersion, advisory) {
  const config = mergeChildConfig(getConfig(), JSON.parse(readFileSync(configPath, "utf8")));
  config.semanticCommits = "disabled";
  if (advisory) {
    config.packageRules.push(Vulnerabilities.prototype.vulnerabilityToPackageRules.call(
      Object.create(Vulnerabilities.prototype), {
        vulnerability: { id: "GHSA-0000-0000-0000", summary: "synthetic" },
        affected: {}, packageName: "pytest", depVersion: "9.1.1",
        fixedVersion: `==${newVersion}`, datasource: "pypi", packageFileConfig: config,
      }));
  }
  const [result] = await flattenUpdates(config, {
    pip_requirements: [{
      packageFile: "requirements.lock",
      deps: [{
        depName: "pytest", packageName: "pytest", datasource: "pypi", versioning: "pep440",
        currentValue: "==9.1.1", currentVersion: "9.1.1",
        updates: [{ updateType, newVersion, newValue: `==${newVersion}` }],
      }],
    }],
  });
  return {
    isVulnerabilityAlert: result.isVulnerabilityAlert === true,
    schedule: result.schedule,
    automerge: result.automerge,
    platformAutomerge: result.platformAutomerge,
    labels: prepareLabels(result),
  };
}

console.log(JSON.stringify({
  security_major: await update("major", "10.0.0", true),
  security_patch: await update("patch", "9.1.2", true),
  major: await update("major", "10.0.0", false),
}));
"""


def renovate_dist():
    """The pinned Renovate build: RENOVATE_DIST, else the matching npx cache entry."""
    if os.environ.get("RENOVATE_DIST"):
        return pathlib.Path(os.environ["RENOVATE_DIST"])
    for package in sorted(pathlib.Path.home().glob(".npm/_npx/*/node_modules/renovate/package.json")):
        if json.loads(package.read_text()).get("version") == RENOVATE_VERSION:
            return package.parent / "dist"
    return None


class LockRepairTests(unittest.TestCase):
    def test_changed_lock_requires_compiler_repin_and_repair_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            fixture = json.loads((ROOT / FIXTURE).read_text())
            lock = root / "requirements.lock"
            lock.write_text("pytest==9.1.1\n")
            target = root / FIXTURE
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps(fixture, indent=2) + "\n")
            before = target.read_bytes()
            command = [sys.executable, str(SCRIPT), "--repo", str(root)]
            check = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(check.returncode, 1, check.stderr)
            self.assertEqual(target.read_bytes(), before)
            repair = subprocess.run(command + ["--write"], capture_output=True, text=True)
            self.assertEqual(repair.returncode, 0, repair.stderr)
            result = json.loads(repair.stdout)
            self.assertEqual(result["compiler_ok"], True)
            self.assertEqual(result["changed"], True)
            updated = json.loads(target.read_text())
            contract = updated["assurance_slice"]
            self.assertEqual(contract["required_tests"][0]["environment"]["dependency_lock"]["digest"],
                             "sha256:" + hashlib.sha256(lock.read_bytes()).hexdigest())
            self.assertNotEqual(contract["ownership_contract_digest"], fixture["assurance_slice"]["ownership_contract_digest"])
            self.assertNotEqual(contract["contract_digest"], fixture["assurance_slice"]["contract_digest"])
            self.assertEqual(contract["required_tests"][0]["test_artifact"], fixture["assurance_slice"]["required_tests"][0]["test_artifact"])
            repaired = target.read_bytes()
            again = subprocess.run(command + ["--write"], capture_output=True, text=True)
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertEqual(json.loads(again.stdout)["changed"], False)
            self.assertEqual(target.read_bytes(), repaired)

    def test_compiler_refusal_cannot_write_the_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "requirements.lock").write_text("pytest==9.1.1\n")
            fixture = json.loads((ROOT / FIXTURE).read_text())
            fixture["applicable_rules"]["snapshot_digest"] = "sha256:" + "0" * 64
            target = root / FIXTURE
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps(fixture))
            before = target.read_bytes()
            result = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(root), "--write"],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(target.read_bytes(), before)

    def test_policy_keeps_security_immediate_and_majors_outside_groups(self):
        config = json.loads((ROOT / "renovate.json").read_text())
        self.assertEqual(set(config["enabledManagers"]), {"npm", "pip_requirements", "github-actions"})
        self.assertEqual(config["automerge"], False)
        self.assertEqual(config["platformAutomerge"], False)
        self.assertEqual(config["osvVulnerabilityAlerts"], True)
        self.assertEqual(config["vulnerabilityAlerts"]["schedule"], [])
        self.assertEqual(config["vulnerabilityAlerts"]["automerge"], False)
        groups = [rule["matchUpdateTypes"] for rule in config["packageRules"] if rule.get("groupName")]
        self.assertEqual(groups, [["patch"], ["minor"]])
        self.assertEqual(config["postUpgradeTasks"]["commands"], ["python3 ops/dependency-update-repin.py --write"])
        self.assertIn("/(^|/)requirements\\.lock$/", config["pip_requirements"]["managerFilePatterns"])

    def test_security_alerts_keep_the_update_type_label(self):
        # Renovate applies vulnerabilityAlerts as a `force` block after every
        # package rule, so an addLabels there replaces dependency-major/-patch.
        config = json.loads((ROOT / "renovate.json").read_text())
        alerts = config["vulnerabilityAlerts"]
        self.assertNotIn("addLabels", alerts)
        self.assertEqual(alerts["labels"], config["labels"] + ["dependency-security"])

    @unittest.skipUnless(shutil.which("node") and renovate_dist(),
                         f"needs node and Renovate {RENOVATE_VERSION} (set RENOVATE_DIST)")
    def test_renovate_labels_security_updates_with_their_update_type(self):
        result = subprocess.run(
            ["node", "--input-type=module", "-e", RENOVATE_LABELS, str(renovate_dist()), str(ROOT / "renovate.json")],
            capture_output=True, text=True, env={**os.environ, "LOG_LEVEL": "fatal"})
        self.assertEqual(result.returncode, 0, result.stderr)
        updates = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(updates["security_major"]["labels"], ["dependencies", "dependency-major", "dependency-security"])
        self.assertEqual(updates["security_patch"]["labels"], ["dependencies", "dependency-patch", "dependency-security"])
        self.assertEqual(updates["major"]["labels"], ["dependencies", "dependency-major"])
        for name in ("security_major", "security_patch"):
            self.assertEqual(updates[name]["isVulnerabilityAlert"], True)
            self.assertEqual(updates[name]["schedule"], [])
            self.assertEqual(updates[name]["automerge"], False)
            self.assertEqual(updates[name]["platformAutomerge"], False)

    def test_current_workflow_actions_are_immutable(self):
        import re
        uses = []
        for workflow in (ROOT / ".github/workflows").glob("*.yml"):
            for match in re.finditer(r"(?m)^\s*(?:-\s+)?uses:\s*(\S+)", workflow.read_text()):
                action = match.group(1)
                if action.startswith(("./", "docker://")):
                    continue
                uses.append(action)
                self.assertRegex(action, r"^[\w./-]+@[0-9a-f]{40}$", str(workflow))
        self.assertTrue(uses)


if __name__ == "__main__":
    unittest.main()
