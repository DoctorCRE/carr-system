#!/usr/bin/env python3
"""Exercise corpus sync through its CLI using only temporary roots."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


class CorpusSyncTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "tools").mkdir()
        (self.root / "corpus").mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        self.source = self.home / "notes.md"
        self.source.write_text("original\n")
        self.mirror = self.root / "corpus/_home/notes.md"
        self.manifest = self.root / "corpus/manifest.json"
        shutil.copyfile(REPO / "tools/corpus-sync.py", self.root / "tools/corpus-sync.py")
        self.set_file = self.root / "corpus/corpus-set.tsv"
        self.set_file.write_text("home:notes.md\tdoctrine\tfixture\n")
        self.run_sync("--sync")
        data = json.loads(self.manifest.read_text())
        data["generated_at"] = "2000-01-01T00:00:00+0000"
        data["files"][0]["mirrored_at"] = "2000-01-01T00:00:00+0000"
        self.manifest.write_text(json.dumps(data, indent=2) + "\n")
        self.before = self.manifest.read_bytes()

    def run_sync(self, mode, expected=0):
        result = subprocess.run(
            [sys.executable, str(self.root / "tools/corpus-sync.py"), mode],
            env={"HOME": str(self.home), "CARR_VAULT": str(self.root / "vault")},
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result.stdout

    def test_sync_with_only_source_mtime_change_preserves_manifest_bytes(self):
        mt = self.source.stat().st_mtime + 120
        os.utime(self.source, (mt, mt))
        self.run_sync("--sync")
        self.assertEqual(self.manifest.read_bytes(), self.before)

    def test_push_restores_identical_render_without_manifest_churn(self):
        mt = self.mirror.stat().st_mtime + 120
        os.utime(self.mirror, (mt, mt))
        self.source.unlink()
        self.run_sync("--push")
        self.assertEqual(self.source.read_bytes(), self.mirror.read_bytes())
        self.assertEqual(self.manifest.read_bytes(), self.before)

    def test_noop_push_preserves_manifest_bytes(self):
        self.run_sync("--push")
        self.assertEqual(self.manifest.read_bytes(), self.before)

    def test_sync_records_content_change(self):
        self.source.write_text("updated\n")
        self.run_sync("--sync")
        self.assertEqual(self.mirror.read_text(), "updated\n")
        self.assertNotEqual(self.manifest.read_bytes(), self.before)
        self.assertNotEqual(json.loads(self.manifest.read_text())["files"][0]["sha256"],
                            json.loads(self.before)["files"][0]["sha256"])

    def test_push_records_content_change(self):
        self.mirror.write_text("updated\n")
        self.run_sync("--push")
        self.assertEqual(self.source.read_text(), "updated\n")
        self.assertNotEqual(json.loads(self.manifest.read_text())["files"][0]["sha256"],
                            json.loads(self.before)["files"][0]["sha256"])

    def test_sync_records_metadata_change(self):
        self.set_file.write_text("home:notes.md\tdoctrine\tnew reason\n")
        self.run_sync("--sync")
        self.assertEqual(json.loads(self.manifest.read_text())["files"][0]["why"], "new reason")

    def test_push_preserves_conflicting_source_edit(self):
        self.source.write_text("hand edit\n")
        output = self.run_sync("--push", expected=2)
        self.assertIn("CONFLICT", output)
        self.assertEqual(self.source.read_text(), "hand edit\n")
        self.assertEqual(self.manifest.read_bytes(), self.before)


if __name__ == "__main__":
    unittest.main()
