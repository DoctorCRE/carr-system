#!/usr/bin/env python3
"""Exercise reservation commands across linked worktrees with separate out dirs."""
from pathlib import Path
import json
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from git_env import scrubbed_env

ROOT = Path(__file__).resolve().parents[1]


class WorktreeReservations(unittest.TestCase):
    def test_missing_git_inventory_refuses_instead_of_using_a_local_ledger(self):
        sys.path.insert(0, str(ROOT / "tools"))
        spec = importlib.util.spec_from_file_location("next_migration", ROOT / "tools/next-migration.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(module, "run", return_value=""):
            with self.assertRaisesRegex(module.MigrationNumberError, "cannot identify the shared checkout"):
                module.worktree_paths()

    def test_shared_claims_and_legacy_claims_are_visible_to_both_commands(self):
        with tempfile.TemporaryDirectory(prefix="migration-reservation-worktrees-") as temp:
            repo = Path(temp) / "primary checkout"
            peer = Path(temp) / "peer checkout"
            repo.mkdir()

            def git(*args):
                return subprocess.run(["git", "-C", str(repo), *args], check=True,
                                      capture_output=True, text=True,
                                      env=scrubbed_env()).stdout

            git("init", "-b", "main")
            git("config", "user.name", "Synthetic Builder")
            git("config", "user.email", "builder@example.invalid")
            git("config", "core.hooksPath", "/dev/null")
            (repo / "tools").mkdir()
            (repo / "ops").mkdir()
            for source in [ROOT / "tools/reserve-migration.py", ROOT / "tools/next-migration.py",
                           *(ROOT / "tools").glob("migration_*.py")]:
                shutil.copyfile(source, repo / "tools" / source.name)
            shutil.copyfile(ROOT / "ops/git_env.py", repo / "ops/git_env.py")
            (repo / "migrations").mkdir()
            # Keep the allocator's filename contract, without copying any data.
            for source in (ROOT / "migrations").glob("*.sql"):
                (repo / "migrations" / source.name).touch()
            git("add", "tools", "ops/git_env.py", "migrations")
            git("commit", "-m", "Synthetic fixture")
            git("update-ref", "refs/remotes/origin/main", "HEAD")
            git("worktree", "add", "-b", "peer", str(peer))
            (repo / "out").mkdir()
            (peer / "out").mkdir()

            def command(tree, script, *args):
                result = subprocess.run([sys.executable, str(tree / "tools" / script), *args],
                                        cwd=tree, capture_output=True, text=True,
                                        env=scrubbed_env())
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout

            first = int(command(repo, "next-migration.py", "--quiet").strip())
            command(peer, "reserve-migration.py", f"--number={first}")
            ledger = repo / "out/migration-reservations.jsonl"
            self.assertTrue(ledger.exists(), "peer must write the canonical ledger")
            self.assertFalse((peer / "out/migration-reservations.jsonl").exists())
            self.assertEqual(int(command(repo, "next-migration.py", "--quiet")), first + 1)
            self.assertEqual(int(command(peer, "next-migration.py", "--quiet")), first + 1)

            # Existing worktree-local claims must survive the routing repair.
            legacy = peer / "out/migration-reservations.jsonl"
            legacy.write_text(json.dumps({"number": first + 5, "name": "legacy",
                                          "ts": "2026-01-01T00:00:00+00:00"}) + "\n")
            self.assertEqual(int(command(repo, "next-migration.py", "--quiet")), first + 6)
            command(repo, "reserve-migration.py")
            rows = [json.loads(line) for line in ledger.read_text().splitlines()]
            self.assertEqual([row["number"] for row in rows], [first, first + 6])
            self.assertIn(str(first + 5), command(repo, "reserve-migration.py", "--list"))

            # Concurrent callers must share the same lock even with local out dirs.
            children = [subprocess.Popen([sys.executable, str(tree / "tools/reserve-migration.py")],
                                         cwd=tree, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         text=True, env=scrubbed_env()) for tree in (repo, peer)]
            for child in children:
                _, stderr = child.communicate(timeout=30)
                self.assertEqual(child.returncode, 0, stderr)
            rows = [json.loads(line) for line in ledger.read_text().splitlines()]
            self.assertEqual([row["number"] for row in rows],
                             [first, first + 6, first + 7, first + 8])
            self.assertEqual(legacy.read_text().count("\n"), 1)


if __name__ == "__main__":
    unittest.main()
