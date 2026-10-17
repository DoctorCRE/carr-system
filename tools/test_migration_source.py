import hashlib
import json
import os
from unittest.mock import patch
from pathlib import Path
import subprocess
import tempfile
import unittest

from migration_source import migration_names, read_migration
from git_env import fixture_env


class HistoricalMigrationSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Fixture")
        (self.repo / "migrations").mkdir()
        (self.repo / "migrations/0001_first.sql").write_bytes(b"select 1;\r\n")
        self.git("add", "migrations/0001_first.sql")
        self.git("commit", "-qm", "Historical source")
        self.source = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True, env=fixture_env())

    def baseline(self):
        (self.repo / "db").mkdir()
        (self.repo / "db/migration-baseline.json").write_text(json.dumps({
            "schema": "carr-migration-baseline/v1", "source_revision": self.source,
            "snapshot": {"path": "db/schema.sql", "sha256": "0" * 64},
            "cutoff": "0001_first.sql", "migrations": [{"filename": "0001_first.sql",
                "sha256": hashlib.sha256(b"select 1;\n").hexdigest()}]}))

    def test_covered_file_remains_readable_after_removal(self):
        self.baseline()
        self.assertEqual(read_migration(self.repo, "0001_first.sql"), "select 1;\n")
        (self.repo / "migrations/0001_first.sql").unlink()
        (self.repo / "migrations/0002_next.sql").write_text("select 2;\n")
        self.assertEqual(migration_names(self.repo), {"0001_first.sql", "0002_next.sql"})
        self.assertEqual(read_migration(self.repo, "0001_first.sql"), "select 1;\n")
        self.assertEqual(read_migration(self.repo, "0002_next.sql"), "select 2;\n")

    def test_checksum_and_missing_pinned_source_refuse(self):
        self.baseline()
        (self.repo / "migrations/0001_first.sql").write_text("select 9;")
        with self.assertRaisesRegex(ValueError, "checksum"):
            read_migration(self.repo, "0001_first.sql")
        (self.repo / "migrations/0001_first.sql").unlink()
        manifest = self.repo / "db/migration-baseline.json"
        manifest.write_text(manifest.read_text().replace(self.source, "f" * 40))
        with self.assertRaisesRegex(ValueError, "pinned Git source"):
            read_migration(self.repo, "0001_first.sql")

    def test_absent_source_checksum_must_match_the_manifest(self):
        self.baseline()
        (self.repo / "migrations/0001_first.sql").unlink()
        manifest = self.repo / "db/migration-baseline.json"
        document = json.loads(manifest.read_text())
        document["migrations"][0]["sha256"] = "f" * 64
        manifest.write_text(json.dumps(document))
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            read_migration(self.repo, "0001_first.sql")

    def test_foreign_git_environment_cannot_redirect_source(self):
        self.baseline()
        (self.repo / "migrations/0001_first.sql").unlink()
        with patch.dict(os.environ, {"GIT_DIR": "/missing/foreign/.git",
                                     "GIT_INDEX_FILE": "/missing/foreign/index"}):
            self.assertEqual(read_migration(self.repo, "0001_first.sql"), "select 1;\n")
            self.assertEqual(migration_names(self.repo, self.source), {"0001_first.sql"})

    def test_exact_ref_uses_its_manifest_and_never_infers_coverage(self):
        self.baseline()
        (self.repo / "migrations/0001_first.sql").unlink()
        self.git("add", "db/migration-baseline.json", "migrations/0001_first.sql")
        self.git("commit", "-qm", "Covered source")
        covered = self.git("rev-parse", "HEAD").strip()
        (self.repo / "db/migration-baseline.json").unlink()
        self.assertEqual(migration_names(self.repo, covered), {"0001_first.sql"})
        self.assertEqual(read_migration(self.repo, "0001_first.sql", covered), "select 1;\n")
        self.assertEqual(read_migration(self.repo, "0001_first.sql", self.source), "select 1;\n")
        with self.assertRaisesRegex(ValueError, "not covered"):
            read_migration(self.repo, "0001_first.sql")
        with self.assertRaisesRegex(ValueError, "filename"):
            read_migration(self.repo, "../0001_first.sql", covered)


if __name__ == "__main__":
    unittest.main()
