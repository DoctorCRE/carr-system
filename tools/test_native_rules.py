import copy
import tempfile
import unittest
from pathlib import Path

import native_rules as rules


class NativeRulesTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = rules.load_snapshot(rules.ROOT / rules.SNAPSHOT)

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
            (root / "AGENTS.md").write_text("Existing instructions.\n")
            outputs, report = rules.render(self.snapshot, root)
            self.assertTrue(outputs["AGENTS.md"].startswith("Existing instructions.\n"))
            self.assertIn("paths:\n", outputs[".claude/rules/rule-6da539b5.md"])
            skill = outputs[".claude/skills/carr-before-send/SKILL.md"]
            self.assertIn("email, Slack", skill)
            self.assertIn("Exceptions", skill)
            self.assertEqual(len(report["homes"]), 222)
            self.assertTrue(report["unmatched_globs"])

    def test_regeneration_is_idempotent_and_preserves_native_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
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
