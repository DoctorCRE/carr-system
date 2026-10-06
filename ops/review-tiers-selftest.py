#!/usr/bin/env python3
"""Offline suite for the review-tier map (ops/config/review-tiers.v1.json).

doctrine: engineering-workflow-sop

The map replaced four private path lists that disagreed with each other:
the source-merge controller's protected paths, the Stop-hook triage's
high-risk floor, the council runner's security-lens trigger and the Jev code
partition's noise filter. This suite holds three properties.

  1. NO LOOSENING (rule a6e6ab4e). Every path a consumer protected, floored
     high or armed the security lens on BEFORE the map existed is still so.
     Checked two ways: against the decisions captured by calling the old code
     (ops/fixtures/review-tiers/pre-change-baseline.v1.json), and against
     frozen literal copies of the old lists applied to every tracked file.
  2. CONSISTENCY. Each Python consumer's decision equals the map's decision
     for the same path; the cross-language vector file the JS test reads
     equals this reader's output; the generated Worker module is current.
  3. NON-BLOCKING TIER 3. Joe ruled code-owner review advisory on every path
     (decision 8daefaba): .github/CODEOWNERS names no owner.

No network, no credential, no database. CLI cases use disposable Git fixtures.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "ops"))
from git_env import scrubbed_env  # noqa: E402

BASELINE = json.loads((REPO / "ops/fixtures/review-tiers/pre-change-baseline.v1.json").read_text())
VECTORS_PATH = REPO / "ops/fixtures/review-tiers/tier-vectors.v1.json"


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    assert spec and spec.loader, rel
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rt = _load("review_tiers", "lib/review_tiers.py")
jdc = _load("jev_done_checks", "ops/jev_done_checks.py")
rcr = _load("run_codex_review", "pipelines/run_codex_review.py")
part = _load("jev_code_partition", "ops/jev_code_partition.py")
sync = _load("sync_review_tiers", "ops/sync-review-tiers.py")


# ---------------------------------------------------------------- the old lists
# FROZEN literal copies of each consumer's list as of commit 3d6aa244, the
# commit before the map. They are the baseline expressed as code, so the
# no-loosening check can run over every tracked file, not only the fixture.
# test_frozen_copies_reproduce_captured_baseline proves they are faithful.

OLD_MERGE_PREFIXES = (".github/actions/", ".github/workflows/", "migrations/", "ops/")
OLD_MERGE_PATHS = frozenset({
    ".github/CODEOWNERS", ".nvmrc", "AGENTS.md", "CLAUDE.md", "control-room/package.json",
    "db/schema.sql", "mcp-server/package-lock.json", "mcp-server/package.json",
    "mcp-server/bin/run-source-merge.mjs", "mcp-server/src/engineering-runtime.js",
    "mcp-server/src/identity.js", "mcp-server/src/index.js", "mcp-server/src/mcp.js",
    "mcp-server/src/sha256.js", "mcp-server/src/source-merge-policy.js",
    "mcp-server/src/tools.js", "mcp-server/src/work-request-intake.js",
    "mcp-server/wrangler.toml", "requirements.lock", "requirements.txt",
    "tools/migrate.py", "workspace/package.json",
})
OLD_TRIAGE = re.compile(r"auth|security|migrat|db/|payment|crypto|secret", re.I)
OLD_LENS_PREFIXES = ("mcp-server/src/", "mcp-server/wrangler.toml", "hooks/", "migrations/", "bin/")
OLD_EXCLUDE_PARTS = ("node_modules/", "/vendor/", ".min.js")


def old_merge(path):
    return path in OLD_MERGE_PATHS or any(path.startswith(p) for p in OLD_MERGE_PREFIXES)


def old_triage(path):
    return bool(OLD_TRIAGE.search(path))


def old_lens(path):
    return any(path.startswith(p) for p in OLD_LENS_PREFIXES)


def old_excluded(path):
    return any(p in path for p in OLD_EXCLUDE_PARTS)


# ---------------------------------------------------------------- the consumers
# Each is driven through its real entry point, with only git stubbed out.

def triage_floor(path):
    diff = f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-a\n+b\n"
    result = jdc.triage_review(diff, "")
    files = result["detail"]["files"]
    assert list(files) == [path], (path, result)
    return files[path]["source"] == "deterministic_floor"


def lens_armed(paths):
    fake = types.SimpleNamespace(returncode=0, stdout="".join(p + "\n" for p in paths), stderr="")
    original = rcr.subprocess.run
    rcr.subprocess.run = lambda *a, **k: fake
    try:
        return rcr.security_lens_if_triggered("0" * 40) is not None
    finally:
        rcr.subprocess.run = original


def partition_kept(paths):
    fake = types.SimpleNamespace(stdout=b"\0".join(p.encode() for p in paths))
    original = part.subprocess.run
    part.subprocess.run = lambda *a, **k: fake
    try:
        return set(part.tracked_sources())
    finally:
        part.subprocess.run = original


def tracked_paths():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True,
                         env=scrubbed_env(), timeout=120, check=True).stdout
    return [p for p in os.fsdecode(out).split("\0") if p]


# ---------------------------------------------------------------- tests

class FrozenBaselineTests(unittest.TestCase):
    def test_frozen_copies_reproduce_captured_baseline(self):
        paths = BASELINE["paths"]
        self.assertEqual(sorted(p for p in paths if old_merge(p)), BASELINE["merge_protected"])
        self.assertEqual(sorted(p for p in paths if old_triage(p)), BASELINE["triage_high"])
        self.assertEqual(sorted(p for p in paths if old_lens(p)), BASELINE["security_lens"])
        candidates = BASELINE["partition_candidates"]
        self.assertEqual(sorted(p for p in candidates if old_excluded(p)), BASELINE["partition_excluded"])


class NoLooseningTests(unittest.TestCase):
    """Rule a6e6ab4e: compared against the baseline, not only the spec."""

    def test_captured_merge_protected_paths_stay_tier3(self):
        loosened = [p for p in BASELINE["merge_protected"] if rt.tier_for_path(p) < 3]
        self.assertEqual(loosened, [])

    def test_captured_triage_high_paths_still_floor_high(self):
        loosened = [p for p in BASELINE["triage_high"] if not triage_floor(p)]
        self.assertEqual(loosened, [])

    def test_captured_lens_paths_still_arm_the_lens(self):
        loosened = [p for p in BASELINE["security_lens"] if not lens_armed([p])]
        self.assertEqual(loosened, [])

    def test_every_tracked_file_keeps_its_old_protection(self):
        loosened = {"merge": [], "triage": [], "lens": []}
        for path in tracked_paths():
            tier = rt.tier_for_path(path)
            if old_merge(path) and tier < rt.MERGE_REFUSAL_TIER:
                loosened["merge"].append(path)
            if old_triage(path) and tier < rt.TRIAGE_HIGH_FLOOR_TIER:
                loosened["triage"].append(path)
            if old_lens(path) and tier < rt.SECURITY_LENS_TIER:
                loosened["lens"].append(path)
        self.assertEqual(loosened, {"merge": [], "triage": [], "lens": []})

    def test_old_noise_exclusions_still_excluded_outside_migrations(self):
        still_read = [p for p in BASELINE["partition_candidates"]
                      if old_excluded(p) and not p.startswith("migrations/")
                      and not rt.is_review_noise(p)]
        self.assertEqual(still_read, [])


class ConsistencyTests(unittest.TestCase):
    """Each consumer's decision equals the map's decision for the same path."""

    def paths(self):
        return BASELINE["paths"] + [v["path"] for v in json.loads(VECTORS_PATH.read_text())["vectors"]]

    def test_triage_floor_equals_map(self):
        for path in self.paths():
            with self.subTest(path=path):
                self.assertEqual(triage_floor(path), rt.tier_for_path(path) >= rt.TRIAGE_HIGH_FLOOR_TIER)

    def test_security_lens_equals_map(self):
        for path in self.paths():
            with self.subTest(path=path):
                self.assertEqual(lens_armed([path]), rt.tier_for_path(path) >= rt.SECURITY_LENS_TIER)

    def test_security_lens_takes_highest_tier_across_a_change_set(self):
        self.assertFalse(lens_armed(["README.md", "corpus/notes.md"]))
        self.assertTrue(lens_armed(["README.md", "migrations/0001_init.sql"]))
        self.assertEqual(rt.tier_for_paths(["README.md", "mcp-server/src/tour-map-route-state.js"]), 2)
        self.assertEqual(rt.tier_for_paths(["README.md", "CLAUDE.md"]), 3)
        self.assertEqual(rt.tier_for_paths([]), rt.load()["default_tier"])

    def test_partition_filter_equals_map(self):
        candidates = [p for p in self.paths() if p.endswith(part.SUFFIXES)]
        kept = partition_kept(candidates)
        for path in candidates:
            with self.subTest(path=path):
                self.assertEqual(path in kept, not rt.is_review_noise(path))

    def test_vectors_equal_this_reader(self):
        doc = json.loads(VECTORS_PATH.read_text())
        for row in doc["vectors"]:
            with self.subTest(path=row["path"]):
                self.assertEqual(row["tier"], rt.tier_for_path(row["path"]))
                self.assertEqual(row["noise"], rt.is_review_noise(row["path"]))
                self.assertEqual(row["test"], rt.is_test_file(row["path"]))

    def test_change_vectors_equal_this_reader(self):
        for row in json.loads(VECTORS_PATH.read_text())["change_vectors"]:
            self.assertEqual(rt.change_summary(row["changes"]), row["summary"])
            self.assertEqual(rt.review_decision(row["changes"], base="a" * 40, head="b" * 40,
                policy_revision="c" * 40, diff_digest="sha256:" + "d" * 64), row["decision"])
            self.assertEqual(rt.tier_for_paths([c["path"] for c in row["changes"]]), row["path_tier"])

    def test_generated_module_and_vectors_are_current(self):
        self.assertEqual(sync.check(), [])


class LensFaultTests(unittest.TestCase):
    """A review-tier map that cannot be imported, read or validated must ARM
    the security lens and record the fault, never quietly disarm it (council
    review of PR 1450, finding 1). The git-error fallback is unchanged."""

    SENSITIVE = ["mcp-server/src/identity.js"]

    def _lens_with_reader(self, reader_factory):
        logged = []
        fake_git = types.SimpleNamespace(returncode=0, stdout="".join(p + "\n" for p in self.SENSITIVE), stderr="")
        original_run, original_reader, original_log = rcr.subprocess.run, rcr._review_tiers, rcr.log
        rcr.subprocess.run = lambda *a, **k: fake_git
        rcr._review_tiers = reader_factory
        rcr.log = logged.append
        try:
            return rcr.security_lens_if_triggered("0" * 40), logged
        finally:
            rcr.subprocess.run, rcr._review_tiers, rcr.log = original_run, original_reader, original_log

    def _reader_reading(self, text):
        """The real reader, with load() pointed at a scratch map holding `text`
        (None: the map file is missing)."""
        def factory():
            module = _load("review_tiers_fault", "lib/review_tiers.py")
            path = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"review-tiers-fault-{os.getpid()}.json")
            if text is None:
                path += ".missing"
            else:
                Path(path).write_text(text)
            module.load = lambda p=path: json.loads(Path(p).read_text())
            module._map.cache_clear()
            return module
        return factory

    def assert_armed_and_recorded(self, factory):
        lens, logged = self._lens_with_reader(factory)
        self.assertEqual(lens, rcr.SECURITY_LENS)
        self.assertTrue(any("review-tier map" in line and "ARMED" in line for line in logged), logged)

    def test_valid_map_still_arms_normally(self):
        lens, logged = self._lens_with_reader(rcr._review_tiers)
        self.assertEqual(lens, rcr.SECURITY_LENS)
        self.assertFalse(any("review-tier map" in line for line in logged), logged)

    def test_missing_map_file_arms_the_lens(self):
        self.assert_armed_and_recorded(self._reader_reading(None))

    def test_truncated_json_arms_the_lens(self):
        self.assert_armed_and_recorded(self._reader_reading("{"))

    def test_empty_object_arms_the_lens(self):
        self.assert_armed_and_recorded(self._reader_reading("{}"))

    def test_schema_invalid_map_arms_the_lens(self):
        bad = rt.load()
        bad["rules"][0]["tier"] = 9
        self.assert_armed_and_recorded(self._reader_reading(json.dumps(bad)))

    def test_reader_import_failure_arms_the_lens(self):
        def broken():
            raise ImportError("lib/review_tiers.py")
        self.assert_armed_and_recorded(broken)

    def test_git_error_fallback_is_unchanged(self):
        fake_git = types.SimpleNamespace(returncode=128, stdout="", stderr="bad object")
        original = rcr.subprocess.run
        rcr.subprocess.run = lambda *a, **k: fake_git
        try:
            self.assertIsNone(rcr.security_lens_if_triggered("0" * 40))
        finally:
            rcr.subprocess.run = original


class TestEvidenceTests(unittest.TestCase):
    def decision(self, changes):
        return rt.review_decision(changes, base="a" * 40, head="b" * 40,
            policy_revision="c" * 40, diff_digest="sha256:" + "d" * 64)

    def test_hyphenated_python_tests_are_evidence(self):
        for path in ("test-example.py", "tools/test-progress-board.py", "./tools\\test-example.py"):
            self.assertTrue(rt.is_test_file(path), path)
        for path in ("tools/contest-example.py", "tools/test-example.py.bak", "tools/test-example.js",
                     "tools/test-example.py/source.py", "tools/test-example/source.py"):
            self.assertFalse(rt.is_test_file(path), path)
        paths = [p for p in tracked_paths() if p.startswith("tools/test-") and p.endswith(".py")]
        self.assertTrue(paths)
        self.assertEqual([p for p in paths if not rt.is_test_file(p)], [])
        change = {"path": "tools/test-progress-board.py", "additions": 400, "deletions": 0}
        result = self.decision([{"path": "tools/progress_board.py", "additions": 3, "deletions": 0}, change])
        self.assertEqual((result["code_lines"], result["test_lines"], result["change_size"]), (3, 400, "small"))
        self.assertEqual(self.decision([change])["tier"], 2)
        self.assertEqual(self.decision([dict(change, path="ops/test-example.py")])["tier"], 3)

    def test_missing_and_partial_counts_are_unknown(self):
        for metadata in ({}, {"additions": 0}, {"deletions": 0}, {"additions": 3}, {"deletions": 3}):
            with self.subTest(metadata=metadata):
                change = dict(metadata, path="lib/a.py")
                result = self.decision([{"path": "lib/known.py", "additions": 2, "deletions": 1}, change])
                self.assertEqual((result["code_lines"], result["change_size"]), (None, "unknown"))
                evidence = self.decision([dict(metadata, path="tests/a.py")])
                self.assertEqual((evidence["code_lines"], evidence["test_lines"], evidence["change_size"]),
                                 (0, None, "small"))
        for changes in ([], [{"path": "lib/a.py", "additions": 0, "deletions": 0}]):
            result = self.decision(changes)
            self.assertEqual((result["code_lines"], result["test_lines"], result["change_size"]), (0, 0, "small"))

    def test_small_fix_with_large_test_sizes_by_code(self):
        result = self.decision([{"path": "lib/example.py", "additions": 2, "deletions": 1},
            {"path": "tests/test_example.py", "additions": 400, "deletions": 0}])
        self.assertEqual((result["code_lines"], result["test_lines"], result["change_size"]), (3, 400, "small"))
        self.assertEqual(result["code_paths"], ["lib/example.py"])
        self.assertEqual(result["test_paths"], ["tests/test_example.py"])

    def test_test_only_change_still_requires_review(self):
        result = self.decision([{"path": "tests/test_example.py", "additions": 400, "deletions": 0}])
        self.assertEqual((result["code_lines"], result["test_lines"], result["tier"], result["lane"]), (0, 400, 2, "review"))
        self.assertFalse(rt.is_review_noise("tests/package-lock.json"))

    def test_ops_test_retains_ops_tier(self):
        result = self.decision([{"path": "ops/example-selftest.py", "additions": 400, "deletions": 0}])
        self.assertEqual((result["code_lines"], result["test_lines"], result["tier"]), (0, 400, 3))

    def test_test_matchers_use_normalized_paths_and_segment_boundaries(self):
        for path in ("test_example.py", "src/test_example.py", "src/example_test.py", "ops/example-selftest.py",
                     "src/example.test.tsx", "src/example.spec.js", "test/review.test.mjs", "tests/a.py",
                     "src/__tests__/a.ts", "__snapshots__/a.snap", "e2e/journeys/a.ts", "fixtures/a.json",
                     "./src\\tests\\a.py", "src/tests/package-lock.json"):
            self.assertTrue(rt.is_test_file(path), path)
            self.assertFalse(rt.is_review_noise(path), path)
        for path in ("src/contest_example.py", "src/latest/a.py", "src/example.test.tsx.bak", "src/testing/a.py"):
            self.assertFalse(rt.is_test_file(path), path)

    def test_binary_code_counts_do_not_claim_a_small_change(self):
        result = self.decision([{"path": "fixtures/example.png", "additions": None, "deletions": None}])
        self.assertEqual((result["change_size"], result["test_lines"]), ("small", None))
        result = self.decision([{"path": "src/example.png", "additions": None, "deletions": None}])
        self.assertEqual(result["change_size"], "unknown")

    def test_cli_counts_real_git_changes_including_literal_filenames(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            for path in ("lib/review_tiers.py", "ops/config/review-tiers.v1.json"):
                target = repo / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((REPO / path).read_bytes())
            env = scrubbed_env()
            def git(*args):
                return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args],
                    cwd=repo, env=env, check=True, capture_output=True, text=True).stdout.strip()
            git("init", "-q")
            git("config", "user.name", "Fixture")
            git("config", "user.email", "fixture@example.invalid")
            git("add", "lib/review_tiers.py", "ops/config/review-tiers.v1.json")
            message = repo / "message"
            message.write_text("Base fixture")
            git("commit", "-q", "-F", str(message))
            base = git("rev-parse", "HEAD")
            paths = ("lib/fix\tname.py", "tests/test_fix\nname.py")
            for path, lines in zip(paths, (3, 400)):
                (repo / path).parent.mkdir(parents=True, exist_ok=True)
                (repo / path).write_text("changed\n" * lines)
            git("add", *paths)
            message.write_text("Code and test fixture")
            git("commit", "-q", "-F", str(message))
            head = git("rev-parse", "HEAD")
            run = subprocess.run([sys.executable, str(repo / "lib/review_tiers.py"),
                "--base", base, "--head", head, "--policy-revision", base],
                cwd=repo, env=env, check=True, capture_output=True, text=True)
            decision = json.loads(run.stdout)
            self.assertEqual((decision["code_lines"], decision["test_lines"], decision["change_size"]), (3, 400, "small"))
            self.assertEqual(decision["code_paths"], [paths[0]])
            self.assertEqual(decision["test_paths"], [paths[1]])

    def test_size_boundaries(self):
        for lines, size in ((50, "small"), (51, "medium"), (200, "medium"), (201, "large")):
            self.assertEqual(self.decision([{"path": "lib/a.py", "additions": lines, "deletions": 0}])["change_size"], size)


class MapContentTests(unittest.TestCase):
    def test_cli_rejects_encodings_the_runtime_cannot_load(self):
        path = "ops/config/jev-cost-guard.v1.json"
        before = json.loads((REPO / path).read_text())
        before["daily_paid_call_cap"] = 1500
        after = dict(before, daily_paid_call_cap=3000)
        for encoding in ("utf-8", "utf-8-sig", "utf-16"):
            for invalid_side in ("base", "head"):
                with self.subTest(encoding=encoding, side=invalid_side), tempfile.TemporaryDirectory() as tmp:
                    repo = Path(tmp)
                    (repo / "lib").mkdir()
                    (repo / "lib/review_tiers.py").write_bytes((REPO / "lib/review_tiers.py").read_bytes())
                    (repo / path).parent.mkdir(parents=True)
                    policy = "ops/config/review-tiers.v1.json"
                    (repo / policy).write_bytes((REPO / policy).read_bytes())
                    env = scrubbed_env()
                    def git(*args):
                        return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args],
                            cwd=repo, env=env, check=True, capture_output=True, text=True).stdout.strip()
                    git("init", "-q")
                    git("config", "user.name", "Fixture")
                    git("config", "user.email", "fixture@example.invalid")
                    revisions = []
                    for side, content in (("base", before), ("head", after)):
                        (repo / path).write_bytes(json.dumps(content).encode(
                            encoding if side == invalid_side else "utf-8"))
                        git("add", path, policy, "lib/review_tiers.py")
                        message = repo / "message"
                        message.write_text(side)
                        git("commit", "-q", "-F", str(message))
                        revisions.append(git("rev-parse", "HEAD"))
                    result = subprocess.run([sys.executable, str(repo / "lib/review_tiers.py"),
                        "--base", revisions[0], "--head", revisions[1],
                        "--policy-revision", revisions[0]], cwd=repo, env=env,
                        check=True, capture_output=True, text=True)
                    decision = json.loads(result.stdout)
                    self.assertEqual(decision["lane"], "tunable_scalar" if encoding == "utf-8" else "review")
                    self.assertEqual(decision["tier"], 1 if encoding == "utf-8" else 3)

    def test_budget_only_change_uses_bounded_lane_with_revision_evidence(self):
        before = json.loads((REPO / "ops/config/jev-cost-guard.v1.json").read_text())
        before["daily_paid_call_cap"] = 1500
        after = dict(before, daily_paid_call_cap=3000)
        changes = [{"path": "ops/config/jev-cost-guard.v1.json", "before": before, "after": after}]
        decision = rt.review_decision(changes, base="a" * 40, head="b" * 40,
                                      policy_revision="c" * 40, diff_digest="sha256:" + "d" * 64)
        self.assertEqual(decision["lane"], "tunable_scalar")
        self.assertEqual(decision["tier"], 1)
        self.assertEqual(decision["head"], "b" * 40)
        self.assertEqual(decision["policy_revision"], "c" * 40)
        self.assertEqual(decision["diff_digest"], "sha256:" + "d" * 64)
        self.assertEqual((decision["code_lines"], decision["change_size"]), (None, "unknown"))
        for changed in [dict(after, daily_paid_call_cap=True), dict(after, daily_paid_call_cap=-1),
                        dict(after, daily_paid_call_cap=3001), dict(after, daily_paid_call_cap="3000"),
                        dict(after, allowed_paths=["*"]), dict(after, command="new authority")]:
            with self.subTest(after=changed):
                changes[0]["after"] = changed
                refused = rt.review_decision(changes, base="a" * 40, head="b" * 40,
                                             policy_revision="c" * 40, diff_digest="sha256:" + "d" * 64)
                self.assertEqual(refused["lane"], "review")
                self.assertEqual(refused["tier"], 3)
        changes[0].update(after=after, mode_changed=True)
        self.assertEqual(rt.review_decision(changes, base="a" * 40, head="b" * 40,
            policy_revision="c" * 40, diff_digest="sha256:" + "d" * 64)["lane"], "review")

    def test_tunable_policy_and_strict_json_fail_closed(self):
        with self.assertRaises(ValueError):
            rt.review_decision([], base="a" * 40, head="b" * 40,
                policy_revision="c" * 40, diff_digest="sha256:" + "d" * 64, doc={})
        for tunables in (None, {}, ["not a field"], [{"path": "x", "field": "n", "minimum": True, "maximum": 3}]):
            doc = dict(rt.load(), tunable_scalars=tunables)
            self.assertTrue(rt.validate(doc))
        for text in ('{"daily_paid_call_cap":1500,"daily_paid_call_cap":3000}', '{"cap":NaN}'):
            with self.assertRaises(ValueError):
                rt._strict_json(text)

    def test_migrations_are_never_noise(self):
        for path in ("migrations/0001_init.sql", "migrations/node_modules/x.js",
                     "migrations/vendor/a.min.js", "migrations/package-lock.json"):
            self.assertFalse(rt.is_review_noise(path), path)

    def test_lockfiles_and_generated_registries_are_noise(self):
        for path in ("mcp-server/package-lock.json", "requirements.lock", "skills-lock.json",
                     "tools/foo/yarn.lock", "mcp-server/src/scac-mutation-registry.v36.generated.js",
                     "mcp-server/src/review-tiers.generated.js", "static/app.min.js",
                     "node_modules/pkg/index.js", "tools/vendor/lib.js"):
            self.assertTrue(rt.is_review_noise(path), path)

    def test_doctrine_tier3_protected_paths(self):
        for path in ("hooks/lint-gate.py", "ops/config/anything.json", "ops/githooks/pre-push",
                     ".github/workflows/ci.yml", ".claude/settings.json",
                     "claude-tree/settings/user.settings.json", "CLAUDE.md", "AGENTS.md",
                     "tools/room-bridge/AGENTS.md", "mcp-server/src/tools.js",
                     "mcp-server/src/review-tiers.generated.js", "mcp-server/src/review-tiers.js",
                     "lib/review_tiers.py"):
            self.assertEqual(rt.tier_for_path(path), 3, path)

    def test_doctrine_tier3_security_paths(self):
        for path in ("migrations/0001_init.sql", "mcp-server/src/identity.js",
                     "mcp-server/src/partner-authority.js", "mcp-server/src/index.js",
                     "mcp-server/wrangler.toml", "hooks/guard-unattended.py",
                     "control-room/fixtures/tenant-boundary.v1.json", "lib/credential_store.py"):
            self.assertEqual(rt.tier_for_path(path), 3, path)

    def test_doctrine_tier2_paths(self):
        for path in ("mcp-server/src/tour-map-route-state.js", "tools/room-bridge/engineering_dispatch_adapter.py",
                     "control-room/contracts/x.v1.json", "dealroom/index.html", "bin/nightly.sh"):
            self.assertEqual(rt.tier_for_path(path), 2, path)

    def test_everything_else_is_tier1(self):
        for path in ("README.md", "corpus/notes.md", "video/cut.py", "docs/guide.md"):
            self.assertEqual(rt.tier_for_path(path), 1, path)

    def test_paths_are_normalized_before_matching(self):
        self.assertEqual(rt.tier_for_path("./ops/ci.sh"), 3)
        self.assertEqual(rt.tier_for_path("ops\\ci.sh"), 3)

    def test_validator_refuses_malformed_maps(self):
        good = rt.load()
        self.assertEqual(rt.validate(good), [])
        bad_tier = json.loads(json.dumps(good))
        bad_tier["rules"][0]["tier"] = 4
        self.assertTrue(rt.validate(bad_tier))
        bad_kind = json.loads(json.dumps(good))
        bad_kind["rules"][0]["match"] = "glob"
        self.assertTrue(rt.validate(bad_kind))
        dup = json.loads(json.dumps(good))
        dup["rules"].append(dict(dup["rules"][0]))
        self.assertTrue(rt.validate(dup))
        # True == 1 in Python: a boolean must not pass as a tier.
        bool_default = json.loads(json.dumps(good))
        bool_default["default_tier"] = True
        self.assertTrue(rt.validate(bool_default))
        bool_rule = json.loads(json.dumps(good))
        bool_rule["rules"][0]["tier"] = True
        self.assertTrue(rt.validate(bool_rule))


class NonBlockingTests(unittest.TestCase):
    def test_codeowners_names_no_owner(self):
        text = (REPO / ".github/CODEOWNERS").read_text()
        owners = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
        self.assertEqual(owners, [], "Tier 3 is never a blocking human review (decision 8daefaba)")


if __name__ == "__main__":
    unittest.main(verbosity=1)
