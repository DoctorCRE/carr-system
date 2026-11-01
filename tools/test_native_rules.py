"""Test native rule generation, ownership checks, and offline CI discovery."""
import copy
from contextlib import contextmanager, redirect_stdout
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import native_rules as rules
from git_env import fixture_env


class NativeRulesTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = rules.load_snapshot(rules.ROOT / rules.SNAPSHOT)

    def git(self, root, *args):
        subprocess.run(["git", "-C", str(root), "-c", "core.hooksPath=/dev/null", *args],
                       check=True, capture_output=True, env=fixture_env())

    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.git(root, "init", "-q")
            (root / "AGENTS.md").write_text("Maintained instructions.\n")
            (root / "CLAUDE.md").write_text("Maintained Claude instructions.\n")
            folder = root / "tools/room-bridge"
            folder.mkdir(parents=True)
            (folder / "tracked.py").write_text("pass\n")
            (root / ".gitignore").write_text("__pycache__/\n")
            self.git(root, "add", "AGENTS.md", "CLAUDE.md", ".gitignore", "tools/room-bridge/tracked.py")
            outputs, report = rules.render(self.snapshot, root)
            startup = {"AGENTS.md": outputs["AGENTS.md"],
                       "CLAUDE.md": (root / "CLAUDE.md").read_text(),
                       ".claude/rules/carr-core.md": outputs[".claude/rules/carr-core.md"]}
            report["startup_measurement"] = {
                "tokenizer": "cl100k_base", "tokens": 1,
                "files": {p: {"sha256": rules.digest(t), "tokens": 1} for p, t in startup.items()}}
            outputs[rules.REPORT] = rules.dump(report)
            outputs[rules.SNAPSHOT] = rules.dump(self.snapshot)
            rules.write_outputs(root, outputs)
            yield root

    def check(self, root):
        with patch.object(rules, "ROOT", root), patch.object(sys, "argv", ["native_rules.py", "--check"]):
            with redirect_stdout(io.StringIO()):
                rules.main()

    def test_untracked_and_ignored_files_do_not_change_check(self):
        with self.fixture() as root:
            self.check(root)
            (root / "tools/room-bridge/untracked.py").write_text("pass\n")
            cache = root / "tools/room-bridge/__pycache__"
            cache.mkdir()
            (cache / "module.pyc").write_bytes(b"synthetic bytecode")
            source = root / "src/feature"
            source.mkdir()
            (source / "untracked.css").write_text("body {}\n")
            self.check(root)

    def test_add_tracked_file_under_active_glob_does_not_change_check(self):
        with self.fixture() as root:
            self.check(root)
            name = "tools/room-bridge/another.py"
            (root / name).write_text("pass\n")
            self.git(root, "add", name)
            self.check(root)
            renamed = "tools/room-bridge/renamed.py"
            self.git(root, "mv", name, renamed)
            self.check(root)
            self.git(root, "rm", "--cached", renamed)
            self.check(root)

    def test_maintained_guidance_edit_does_not_require_measurement(self):
        with self.fixture() as root:
            self.check(root)
            for name in ("CLAUDE.md", "AGENTS.md"):
                path = root / name
                path.write_text(path.read_text() + "\nMore maintained instructions.\n")
                self.check(root)

    def test_check_rejects_oversized_codex_chain(self):
        with self.fixture() as root:
            path = root / "src/AGENTS.md"
            path.write_text(path.read_text() + "é" * 16384)
            with self.assertRaisesRegex(ValueError, "Codex.*32768"):
                self.check(root)

    def test_retired_nested_block_deletes_empty_file_and_preserves_guidance(self):
        for maintained in ("", "Keep this guidance.\n"):
            with self.subTest(maintained=maintained), self.fixture() as root:
                path = root / "retired/AGENTS.md"
                path.parent.mkdir()
                path.write_text(rules.block(maintained, rules.NOTICE))
                manifest = root / rules.REPORT
                report = json.loads(manifest.read_text())
                report["outputs"].append("retired/AGENTS.md")
                report["nested_agents"].append("retired/AGENTS.md")
                manifest.write_text(rules.dump(report))
                outputs, report = rules.render(self.snapshot, root)
                outputs[rules.REPORT] = rules.dump(report)
                rules.write_outputs(root, outputs)
                if maintained:
                    self.assertEqual(path.read_text().strip(), maintained.strip())
                else:
                    self.assertFalse(path.exists())

    def test_offline_ci_discovers_generator_tests_without_live_probes(self):
        spec = importlib.util.spec_from_file_location('ci_gates', rules.ROOT / 'ops/ci-gates.py')
        gates = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gates)
        programs = gates.gate_programs()
        self.assertIn('tools/test_native_rules.py', programs)
        self.assertFalse(any('native_codex_loading' in p or 'native_rule_loading' in p
                             for p in programs))

    def test_symlink_overwrite_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "repo"
            root.mkdir()
            outside = base / "outside"
            outside.write_text("untouched")
            (root / "AGENTS.md").symlink_to(outside)
            with self.assertRaises(ValueError):
                rules.write_outputs(root, {".claude/rules/first.md": rules.NOTICE,
                                           "AGENTS.md": rules.NOTICE})
            self.assertEqual(outside.read_text(), "untouched")
            self.assertFalse((root / ".claude").exists())

    def test_traversal_manifest_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "repo"
            root.mkdir()
            outside = base / "outside"
            outside.write_text(rules.NOTICE)
            manifest = root / rules.REPORT
            manifest.parent.mkdir(parents=True)
            manifest.write_text(rules.dump({"outputs": ["../outside"]}))
            with self.assertRaises(ValueError):
                rules.write_outputs(root, {".claude/rules/first.md": rules.NOTICE})
            self.assertTrue(outside.exists())
            self.assertFalse((root / ".claude").exists())

    def test_every_rule_contains_full_statement(self):
        outputs, report = rules.render(self.snapshot, rules.ROOT)
        for row in self.snapshot["active"]:
            if row["id"] in rules.WITHHELD:
                self.assertIn(row["id"], report["withheld_statements"])
                self.assertNotIn("statement", row)
                continue
            self.assertIn("statement", row)
            self.assertTrue(any(row["statement"] in text for text in outputs.values()), row["id"])

    def test_foreign_skill_directory_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / ".agents/skills/carr-foreign"
            skill.mkdir(parents=True)
            (skill / "notes.txt").write_text("foreign")
            with self.assertRaisesRegex(ValueError, "foreign skill"):
                rules.write_outputs(root, {".agents/skills/carr-foreign/SKILL.md": rules.NOTICE})
            self.assertFalse((skill / "SKILL.md").exists())

    def test_invalid_destinations_and_parent_symlinks_refused(self):
        for name in ("/tmp/absolute", "../outside", "tools/../AGENTS.md", "tools/source.py",
                     ".agents/skills/foreign/SKILL.md"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with self.assertRaises(ValueError):
                    rules.write_outputs(root, {name: rules.NOTICE})
                self.assertEqual(list(root.iterdir()), [])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            outside = Path(directory) / "outside"
            outside.mkdir()
            (root / ".agents").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                rules.write_outputs(root, {".agents/skills/carr-test/SKILL.md": rules.NOTICE})
            self.assertEqual(list(outside.iterdir()), [])

    def test_foreign_delete_refuses_entire_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / rules.REPORT
            manifest.parent.mkdir(parents=True)
            obsolete = ".claude/rules/obsolete.md"
            path = root / obsolete
            path.parent.mkdir(parents=True)
            path.write_text("foreign")
            manifest.write_text(rules.dump({"outputs": [obsolete]}))
            with self.assertRaisesRegex(ValueError, "foreign"):
                rules.write_outputs(root, {".claude/rules/new.md": rules.NOTICE})
            self.assertFalse((root / ".claude/rules/new.md").exists())
            self.assertEqual(path.read_text(), "foreign")

    def test_exactly_one_primary_home(self):
        rows = rules.placements(self.snapshot)
        self.assertEqual(len(rows), 222)
        self.assertEqual(len({row["id"] for row in rows}), 222)

    def test_duplicate_and_missing_fail(self):
        data = copy.deepcopy(self.snapshot)
        data["sections"][0]["body_text"] += "\n" + next(
            line for line in data["sections"][0]["body_text"].splitlines()
            if line.startswith("| 015183"))
        data["sections"][0]["text_hash"] = rules.digest(data["sections"][0]["body_text"])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            rules.placements(data)
        data = copy.deepcopy(self.snapshot)
        data["active"].pop()
        with self.assertRaisesRegex(ValueError, "coverage"):
            rules.placements(data)

    def test_changed_statement_fails(self):
        data = copy.deepcopy(self.snapshot)
        data["active"][0]["text_hash"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "stale"):
            rules.placements(data)

    def test_render_preserves_foreign_guidance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.git(root, "init", "-q")
            (root / "AGENTS.md").write_text("Existing instructions.\n")
            outputs, report = rules.render(self.snapshot, root)
            self.assertTrue(outputs["AGENTS.md"].startswith("Existing instructions.\n"))
            self.assertIn("paths:\n", outputs[".claude/rules/rule-6da539b5.md"])
            skill = outputs[".claude/skills/carr-before-send/SKILL.md"]
            self.assertIn("email, Slack", skill)
            self.assertIn("Exempt: single drafts Joe reviews line-by-line.", skill)
            self.assertEqual(len(report["homes"]), 222)
            self.assertTrue(report["unmatched_globs"])

    def test_regeneration_is_idempotent_and_preserves_native_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.git(root, "init", "-q")
            outputs, report = rules.render(self.snapshot, root)
            outputs[rules.REPORT] = rules.dump(report)
            rules.write_outputs(root, outputs)
            again, again_report = rules.render(self.snapshot, root)
            again[rules.REPORT] = rules.dump(again_report)
            self.assertEqual(outputs, again)
            self.assertEqual(rules.stale_outputs(root, again), [])
            for path in outputs:
                if path.endswith("SKILL.md"):
                    name = path.split("/")[-2]
                    self.assertRegex(name, r"^[a-z0-9-]+$")
                    self.assertLessEqual(len(name), 64)

    def test_stale_missing_and_extra_outputs_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.git(root, "init", "-q")
            outputs, _ = rules.render(self.snapshot, root)
            rules.write_outputs(root, outputs)
            self.assertEqual(rules.stale_outputs(root, outputs), [])
            path = root / ".claude/rules/carr-core.md"
            extra = root / "other/AGENTS.md"
            extra.parent.mkdir()
            extra.write_text(rules.NOTICE)
            self.assertIn("other/AGENTS.md", rules.stale_outputs(root, outputs))
            extra.unlink()
            path.write_text("corrupt")
            self.assertIn(str(path.relative_to(root)), rules.stale_outputs(root, outputs))
            path.unlink()
            self.assertIn(str(path.relative_to(root)), rules.stale_outputs(root, outputs))


if __name__ == "__main__":
    unittest.main()
