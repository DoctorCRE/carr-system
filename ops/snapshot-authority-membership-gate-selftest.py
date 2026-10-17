#!/usr/bin/env python3
"""Authority membership expectations survive removal of covered SQL."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "ops"))
from git_env import fixture_env

spec = importlib.util.spec_from_file_location(
    "snapshot_authority_membership_gate", REPO / "ops/snapshot-authority-membership-gate.py")
assert spec is not None and spec.loader is not None
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class AuthorityHistoryTests(unittest.TestCase):
    def test_covered_logins_remain_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "migrations").mkdir()
            sql = "grant carr_authority to carr_authority_fixture;\n"
            path = repo / "migrations/0001_authority.sql"
            path.write_text(sql)
            env = dict(fixture_env(), GIT_AUTHOR_NAME="Fixture",
                       GIT_AUTHOR_EMAIL="fixture@example.invalid", GIT_COMMITTER_NAME="Fixture",
                       GIT_COMMITTER_EMAIL="fixture@example.invalid")
            def git(*args):
                return subprocess.check_output(["git", "-C", str(repo), *args], env=env, text=True)
            git("init", "-q")
            git("add", "migrations/0001_authority.sql")
            git("commit", "-qm", "Historical membership")
            source = git("rev-parse", "HEAD").strip()
            self.assertEqual(gate.authority_logins(repo), ("carr_authority_fixture",))
            (repo / "db").mkdir()
            (repo / "db/migration-baseline.json").write_text(json.dumps({
                "schema": "carr-migration-baseline/v1", "source_revision": source,
                "snapshot": {"path": "db/schema.sql", "sha256": "0" * 64},
                "cutoff": path.name, "migrations": [{"filename": path.name,
                    "sha256": hashlib.sha256(sql.encode()).hexdigest()}]}))
            path.unlink()
            self.assertEqual(gate.authority_logins(repo), ("carr_authority_fixture",))
            path.write_text("grant carr_authority to carr_authority_other;\n")
            with self.assertRaisesRegex(ValueError, "checksum"):
                gate.authority_logins(repo)


if __name__ == "__main__":
    unittest.main()
