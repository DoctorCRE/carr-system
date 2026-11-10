# Test reading historical migration sources.
import hashlib
import json
import os
from unittest.mock import patch
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

from migration_source import migration_names, read_migration, read_baseline_snapshot
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
        with zipfile.ZipFile(self.repo / "db/migration-history.zip", "w") as archive:
            archive.writestr("migrations/0001_first.sql", b"select 1;\r\n")
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

    def test_checksum_and_missing_archive_refuse(self):
        self.baseline()
        (self.repo / "migrations/0001_first.sql").write_text("select 9;")
        with self.assertRaisesRegex(ValueError, "checksum"):
            read_migration(self.repo, "0001_first.sql")
        (self.repo / "migrations/0001_first.sql").unlink()
        manifest = self.repo / "db/migration-baseline.json"
        manifest.write_text(manifest.read_text().replace(self.source, "f" * 40))
        (self.repo / "db/migration-history.zip").unlink()
        with self.assertRaisesRegex(ValueError, "archive"):
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

    def test_archive_is_sufficient_without_historical_object(self):
        self.baseline()
        (self.repo / "migrations/0001_first.sql").unlink()
        manifest = self.repo / "db/migration-baseline.json"
        manifest.write_text(manifest.read_text().replace(self.source, "f" * 40))
        self.assertEqual(read_migration(self.repo, "0001_first.sql"), "select 1;\n")

    def test_corrupt_member_refuses(self):
        self.baseline()
        (self.repo / "migrations/0001_first.sql").unlink()
        with zipfile.ZipFile(self.repo / "db/migration-history.zip", "w") as archive:
            archive.writestr("migrations/0001_first.sql", "select 999;")
        with self.assertRaisesRegex(ValueError, "checksum"):
            read_migration(self.repo, "0001_first.sql")

    def test_archive_structure_and_limits_refuse(self):
        import migration_source as reader
        self.baseline()
        (self.repo / "migrations/0001_first.sql").unlink()
        archive_path = self.repo / "db/migration-history.zip"
        for entry in ("../escape.sql", "/absolute.sql", "migrations/unregistered.sql"):
            with self.subTest(entry=entry):
                with zipfile.ZipFile(archive_path, "w") as archive:
                    archive.writestr(entry, "select 1;")
                with self.assertRaisesRegex(ValueError, "unexpected"):
                    read_migration(self.repo, "0001_first.sql")
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("migrations/0001_first.sql", b"select 1;\r\n")
        for limit, value in (("MAX_MEMBER_BYTES", 10), ("MAX_TOTAL_BYTES", 10)):
            with patch.object(reader, limit, value), self.assertRaisesRegex(ValueError, "size limit"):
                read_migration(self.repo, "0001_first.sql")
        with patch.object(reader, "MAX_MEMBER_BYTES", 11), patch.object(reader, "MAX_TOTAL_BYTES", 11):
            self.assertEqual(read_migration(self.repo, "0001_first.sql"), "select 1;\n")
        for raw in (b"not a zip", b""):
            archive_path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, "archive"):
                read_migration(self.repo, "0001_first.sql")

    def test_missing_duplicate_symlink_and_invalid_encoding_refuse(self):
        import warnings
        self.baseline()
        (self.repo / "migrations/0001_first.sql").unlink()
        archive_path = self.repo / "db/migration-history.zip"
        name = "migrations/0001_first.sql"
        for kind in ("missing", "duplicate", "symlink", "encoding"):
            with self.subTest(kind=kind), warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(archive_path, "w") as archive:
                    if kind == "duplicate":
                        archive.writestr(name, "select 1;")
                        archive.writestr(name, "select 1;")
                    elif kind == "symlink":
                        info = zipfile.ZipInfo(name)
                        info.external_attr = 0o120777 << 16
                        archive.writestr(info, "target")
                    elif kind == "encoding":
                        archive.writestr(name, b"\xff")
                with self.assertRaisesRegex(ValueError, "archive"):
                    read_migration(self.repo, "0001_first.sql")

    def test_snapshot_hashes_raw_bytes_and_uses_exact_ref(self):
        self.baseline()
        snapshot = b"historical\r\n"
        manifest = self.repo / "db/migration-baseline.json"
        document = json.loads(manifest.read_text())
        document["snapshot"]["sha256"] = hashlib.sha256(snapshot).hexdigest()
        manifest.write_text(json.dumps(document))
        with zipfile.ZipFile(self.repo / "db/migration-history.zip", "w") as archive:
            archive.writestr("db/schema.sql", snapshot)
        self.git("add", "db")
        self.git("commit", "-qm", "snapshot")
        ref = self.git("rev-parse", "HEAD").strip()
        with zipfile.ZipFile(self.repo / "db/migration-history.zip", "w") as archive:
            archive.writestr("db/schema.sql", b"historical\n")
        (self.repo / "db/schema.sql").write_bytes(snapshot)
        with self.assertRaisesRegex(ValueError, "checksum"):
            read_baseline_snapshot(self.repo)
        self.assertEqual(read_baseline_snapshot(self.repo, ref), snapshot)

    def test_generator_is_deterministic_and_byte_identical(self):
        from generate_migration_archive import generate
        self.baseline()
        raw = b"select 1;\r\n"
        snapshot = ("COPY public.schema_migrations (filename, sha256) FROM stdin;\n"
                    "0001_first.sql\t" + hashlib.sha256(b"select 1;\n").hexdigest() + "\n\\.\n").encode()
        (self.repo / "db/schema.sql").write_bytes(snapshot)
        self.git("add", "db/schema.sql")
        self.git("commit", "-qm", "historical snapshot")
        manifest = self.repo / "db/migration-baseline.json"
        document = json.loads(manifest.read_text())
        document["source_revision"] = self.git("rev-parse", "HEAD").strip()
        document["snapshot"]["sha256"] = hashlib.sha256(snapshot).hexdigest()
        manifest.write_text(json.dumps(document))
        (self.repo / "migrations/0001_first.sql").unlink()
        first = generate(self.repo)
        self.assertEqual(first, generate(self.repo))
        import io
        with zipfile.ZipFile(io.BytesIO(first)) as archive:
            self.assertEqual(archive.read("migrations/0001_first.sql"), raw)
            self.assertEqual(archive.read("db/schema.sql"), snapshot)

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
        self.git("add", "db/migration-baseline.json", "db/migration-history.zip", "migrations/0001_first.sql")
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
