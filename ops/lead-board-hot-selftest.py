#!/usr/bin/env python3
"""Exercise Hot rendering and refusal through synthetic records-mode builds."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BUILDERS = ("generators/build-lead-board.py", "shared/build-lead-board-template.py")
SOURCES = '''
MODE_FILES, MODE_RECORDS = "files", "records"
ROUTER_SOURCE = "lead-router"
LANE_SOURCES = ()
def pool_reach(want): return True, "synthetic", None, None
def load_pool(want): return {"lead-router": []}
def load_leads(root, mode): return []
def resolve_mode(argv, default=MODE_RECORDS): return MODE_RECORDS, []
def source_note(mode): return "synthetic records"
'''


class HotBuildTest(unittest.TestCase):
    def build(self, builder, hot, *, unreadable=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / builder
            script.parent.mkdir(parents=True)
            shutil.copyfile(ROOT / builder, script)
            lib = root / "lib"
            lib.mkdir()
            (lib / "__init__.py").touch()
            (lib / "record_sources.py").write_text(SOURCES)
            for name in ("sheets.py", "drive_recovery.py"):
                shutil.copyfile(ROOT / "lib" / name, lib / name)
            template = root / "generators" / "lead-board-template.html"
            template.parent.mkdir(exist_ok=True)
            shutil.copyfile(ROOT / "generators/lead-board-template.html", template)
            auto = root / "Automation"
            auto.mkdir()
            source = auto / "lead-board-hot.json"
            if unreadable:
                source.mkdir()
            elif hot is not None:
                source.write_text(hot)
            output = (root / "out" if builder.startswith("generators/") else auto) / "lead-board.html"
            output.parent.mkdir(exist_ok=True)
            output.write_text("previous board")
            result = subprocess.run([sys.executable, str(script)], cwd=root,
                                    capture_output=True, text=True, timeout=20)
            return result, output.read_text(), source.name

    def test_missing_hot_refuses_to_overwrite_board(self):
        for builder in BUILDERS:
            with self.subTest(builder=builder):
                result, output, source = self.build(builder, None)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn(source, result.stderr)
                self.assertIn("Hot source unavailable", result.stderr)
                self.assertEqual(output, "previous board")

    def test_unreadable_hot_refuses_to_overwrite_board(self):
        for builder in BUILDERS:
            with self.subTest(builder=builder):
                result, output, source = self.build(builder, None, unreadable=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(source, result.stderr)
                self.assertIn("Hot source unavailable", result.stderr)
                self.assertEqual(output, "previous board")

    def test_invalid_json_refuses_to_overwrite_board(self):
        for builder in BUILDERS:
            with self.subTest(builder=builder):
                result, output, source = self.build(builder, "{broken")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(source, result.stderr)
                self.assertIn("Hot source unavailable", result.stderr)
                self.assertEqual(output, "previous board")

    def test_valid_hot_payload_and_count_reach_rendered_board(self):
        hot = {"ranked": [{"id": "L-TEST", "name": "Synthetic Ranked"}],
               "proposed": [{"name": "Synthetic Proposal"}],
               "holds": [{"name": "Synthetic Hold"}],
               "signals": [{"name": "Synthetic Signal"}]}
        for builder in BUILDERS:
            with self.subTest(builder=builder):
                result, output, _ = self.build(builder, json.dumps(hot))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(json.dumps(hot, separators=(",", ":")), output)
                self.assertNotIn("__HOTN__", output)
                self.assertTrue('data-p="hot">Hot <span class="ct">2</span>' in output, 'Hot count must be 2')

    def test_confirmed_empty_hot_can_render_zero(self):
        hot = dict.fromkeys(("ranked", "proposed", "holds", "signals"), [])
        for builder in BUILDERS:
            with self.subTest(builder=builder):
                result, output, _ = self.build(builder, json.dumps(hot))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue('data-p="hot">Hot <span class="ct">0</span>' in output, 'Confirmed empty Hot count must be 0')


if __name__ == "__main__":
    unittest.main()
