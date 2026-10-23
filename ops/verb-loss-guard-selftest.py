#!/usr/bin/env python3
"""Exercise the same registry import and retirement check CI and deploy use."""
import io
import re
import tarfile
import sys
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parent.parent
LEDGER = REPO / "ops/config/retired-verbs.v1.json"
def source_registry(git_sha: str, count: int) -> dict:
    """Reconstruct the deployed name set from its immutable ledger binding."""
    if not isinstance(git_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", git_sha):
        raise ValueError("deployed source must have a full Git commit SHA")
    archive = subprocess.run(
        ["git", "archive", git_sha, "mcp-server"], cwd=REPO,
        capture_output=True, check=True, timeout=30).stdout
    with tempfile.TemporaryDirectory(prefix="deployed-registry-") as raw:
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            source.extractall(raw, filter="data")
        result = subprocess.run(
            ["sh", str(REPO / "ops/verb-count.sh"), str(Path(raw) / "mcp-server"), "--names"],
            capture_output=True, text=True, check=True, timeout=60)
        names = json.loads(result.stdout)
    if (not isinstance(names, list) or any(not isinstance(name, str) for name in names)
            or len(set(names)) != len(names) or len(names) != count):
        raise ValueError("deployed source registry does not match the ledger verb count")
    return {"git_sha": git_sha, "verb_count": count, "verbs": names}


class VerbLossGuardTests(unittest.TestCase):
    def setUp(self):
        entries = json.loads(LEDGER.read_text())["retirements"]
        self.retired = [name for entry in entries for name in entry["verbs"]]
        self.pr1806 = next(entry["verbs"] for entry in entries
                           if entry["reference"].endswith("/1806"))

    def run_guard(self, names, deployed):
        with tempfile.TemporaryDirectory() as raw:
            worker = Path(raw)
            (worker / "src").mkdir()
            (worker / "package.json").write_text('{"type":"module"}')
            (worker / "src/tools.js").write_text(
                "export const TOOLS = " + json.dumps(dict.fromkeys(names, {})) + ";\n")
            return subprocess.run(
                ["sh", str(REPO / "ops/verb-count.sh"), str(worker),
                 "--check-retirements", json.dumps(
                     {"verb_count": len(deployed), "verbs": deployed}
                     if isinstance(deployed, list) else deployed)],
                text=True, capture_output=True)

    def test_intended_retirement_passes(self):
        result = self.run_guard(["keep-a", "keep-b"], ["keep-a", "keep-b"] + self.retired)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "2")

    def test_post_retirement_377_to_341_unlisted_loss_fails(self):
        sha = subprocess.check_output(
            ["git", "rev-parse", "6015711f5"], cwd=REPO, text=True).strip()
        names = source_registry(sha, 377)["verbs"]
        self.assertEqual(len(names), 377)
        self.assertFalse(set(names[:36]).intersection(self.retired))
        for deployed in (377, names):
            with self.subTest(baseline="count" if deployed == 377 else "names"):
                result = self.run_guard([name for name in names[36:] if name not in self.retired], deployed)
                self.assertNotEqual(result.returncode, 0,
                                    "already-shipped retirements excused 36 unlisted removals")
                self.assertEqual(result.stdout, "")

    def test_unlisted_missing_verb_fails(self):
        result = self.run_guard(["keep-a"], ["keep-a", "keep-b"] + self.retired)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unlisted verb loss", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_listed_verb_still_present_fails_even_without_shrink(self):
        result = self.run_guard(["keep-a", self.retired[0]], ["keep-a", self.retired[0]])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(self.retired[0], result.stderr)
        self.assertEqual(result.stdout, "")

    def test_count_only_baseline_cannot_authorize_retirement(self):
        result = self.run_guard(["keep-a", "keep-b"], 3)
        self.assertNotEqual(result.returncode, 0)

    def test_after_retirement_baseline_and_growth_pass(self):
        for deployed in (0, ["keep-a"], ["keep-a", "keep-b"]):
            with self.subTest(deployed=deployed):
                result = self.run_guard(["keep-a", "keep-b"], deployed)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "2")

    def test_loss_masked_by_addition_fails(self):
        result = self.run_guard(["keep-a", "new"], ["keep-a", "unlisted"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unlisted", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_only_deployed_retirements_excuse_loss(self):
        result = self.run_guard(["keep-a"], ["keep-a", self.retired[0]])
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_guard(["keep-a"], ["keep-a", "unlisted"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unlisted", result.stderr)

    def test_deployed_source_registry_matches_exact_historical_retirements(self):
        sha = subprocess.check_output(
            ["git", "rev-parse", "6015711f5^"], cwd=REPO, text=True).strip()
        before = source_registry(sha, 413)
        after_sha = subprocess.check_output(
            ["git", "rev-parse", "6015711f5"], cwd=REPO, text=True).strip()
        after = source_registry(after_sha, 377)
        self.assertEqual(set(before["verbs"]) - set(after["verbs"]), set(self.pr1806))
        self.assertEqual(before["git_sha"], sha)
        result = self.run_guard([name for name in after["verbs"] if name not in self.retired], before["verbs"])
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_guard([name for name in after["verbs"][36:] if name not in self.retired], after["verbs"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unlisted verb loss", result.stderr)
        with self.assertRaisesRegex(ValueError, "does not match"):
            source_registry(after_sha, 413)
        with self.assertRaises(ValueError):
            source_registry("main", 377)
        with self.assertRaises(subprocess.CalledProcessError):
            source_registry("0" * 40, 377)

    def test_ledger_has_exact_names_removed_by_pr_1806(self):
        patch = subprocess.check_output(
            ["git", "show", "6015711f5", "--", "mcp-server/src"], cwd=REPO, text=True)
        self.assertEqual(len(self.pr1806), 36)
        self.assertEqual(len(set(self.pr1806)), 36)
        for name in self.pr1806:
            self.assertIn('"' + name + '"', patch)


if __name__ == "__main__":
    unittest.main()
