#!/usr/bin/env python3
"""Logical migration identities survive removal of baseline-covered files."""
from __future__ import annotations
import hashlib
import contextlib
import io
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from git_env import fixture_env

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

release = load("consumer_release", "tools/release-manifest.py")
order = load("consumer_order", "ops/migration-order-gate.py")
renumber = load("consumer_renumber", "tools/renumber-at-merge.py")
import integration_candidate as integration
from migration_baseline import runner_sha256
from migration_number_contract import FROZEN_COLLISIONS
allocator = load("consumer_allocator", "tools/next-migration.py")
reservation = load("consumer_reservation", "tools/reserve-migration.py")

class LogicalConsumers(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        self.env = fixture_env()
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Fixture")
        (self.repo / "migrations").mkdir()
        (self.repo / "db").mkdir()
        self.history = {name: "select 0;\n" for names in FROZEN_COLLISIONS.values() for name in names}
        self.history.update({"0887_history.sql": "select 1;\r\n", "0888_history.sql": "select 2;\n"})
        for name, text in self.history.items():
            (self.repo / "migrations" / name).write_bytes(text.encode())
        (self.repo / "db/schema.sql").write_text("COPY public.schema_migrations (filename, sha256, applied_at) FROM stdin;\n" + "".join(f"{n}\t{runner_sha256(t)}\t2026-01-01\n" for n,t in self.history.items()) + "\\.\n")
        self.source = self.commit()
        document = {"schema": "carr-migration-baseline/v1", "source_revision": self.source,
                    "snapshot": {"path": "db/schema.sql", "sha256": hashlib.sha256((self.repo / "db/schema.sql").read_bytes()).hexdigest()},
                    "cutoff": "0888_history.sql", "migrations": [
                        {"filename": n, "sha256": runner_sha256(t)} for n, t in self.history.items()]}
        (self.repo / "db/migration-baseline.json").write_text(json.dumps(document))
        for name in self.history:
            (self.repo / "migrations" / name).unlink()
        self.base = self.commit()
        self.git("update-ref", "refs/remotes/origin/main", self.base)

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo, env=self.env, stderr=subprocess.DEVNULL).decode().strip()

    def commit(self):
        self.git("add", "migrations", "db")
        self.git("-c", "core.hooksPath=/dev/null", "commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def test_release_ledger_binds_full_names_and_normalized_content_at_ref(self):
        expected = sorted((n, runner_sha256(t)) for n, t in self.history.items())
        with patch.object(release, "REPO", self.repo):
            self.assertEqual(release.migration_tree_ledger(self.base), expected)
            self.assertEqual(release.migration_tree_ledger(self.source), expected)
            self.assertEqual(release.migration_set(self.base)[0], sorted(n.split("_", 1)[0] for n in self.history))
            self.assertEqual(release.migration_set(self.base, self.source)[0], [])
            (self.repo / "migrations/0889_pending.sql").write_text("select 3;\n")
            head = self.commit()
            self.assertEqual(release.migration_tree_ledger(self.base), expected)
            self.assertEqual(release.migration_tree_ledger(head), expected + [("0889_pending.sql", runner_sha256("select 3;\n"))])

    def test_order_gate_keeps_the_covered_ceiling(self):
        base = order.tree_paths(self.repo, self.base)
        self.assertEqual(base, {"migrations/" + n for n in self.history})
        self.assertEqual(order.violations(base, base, base | {"migrations/0887_intruder.sql"}), ["migrations/0887_intruder.sql"])

    def test_integration_union_accepts_compacted_history_and_forward_suffix(self):
        (self.repo / "migrations/0889_pending.sql").write_text("select 3;\n")
        self.commit()
        observed = integration.validate_candidate(self.repo, self.base)
        self.assertEqual(observed["pending_migrations"], ["0889_pending.sql"])
        self.assertEqual(set(integration.main_snapshot(self.repo, self.base)), {"migrations/" + n for n in self.history})

    def test_renumber_uses_covered_ceiling_without_recreating_history(self):
        (self.repo / "migrations/0887_pending.sql").write_text("select 3;\n")
        result = renumber.plan(self.repo, None, {})
        self.assertEqual(result.renames, {"0887_pending.sql": "0889_pending.sql"})
        self.assertFalse(set(result.writes) & {"migrations/" + n for n in self.history})

    def retained_main(self):
        for name, text in self.history.items():
            (self.repo / "migrations" / name).write_bytes(text.encode())
        self.base = self.commit()
        self.git("update-ref", "refs/remotes/origin/main", self.base)

    def snapshot_generator(self, *, delete_retained=False):
        script = self.repo / "ops/migration-shadow.py"
        script.parent.mkdir()
        script.write_text(
            "from pathlib import Path\n"
            "p=Path('db/schema.sql')\n"
            "p.write_text(p.read_text().replace('\\\\.\\n', '0889_pending.sql\\tunused\\t2026-01-01\\n\\\\.\\n'))\n"
            + ("Path('migrations/0888_history.sql').unlink()\n" if delete_retained else ""))

    def test_renumber_applies_covered_deletion_transition(self):
        self.retained_main()
        for name in self.history:
            (self.repo / "migrations" / name).unlink()
        (self.repo / "migrations/0887_pending.sql").write_text("select 3;\n")
        self.snapshot_generator()
        result = renumber.plan(self.repo, None, {})
        self.assertEqual(result.renames, {"0887_pending.sql": "0889_pending.sql"})
        renumber.apply(self.repo, result)
        self.assertEqual((self.repo / "migrations/0889_pending.sql").read_text(), "select 3;\n")
        self.assertFalse(any((self.repo / "migrations" / name).exists() for name in self.history))

    def test_renumber_refuses_uncovered_main_deletion(self):
        self.retained_main()
        (self.repo / "migrations/0889_uncovered.sql").write_text("select 4;\n")
        self.base = self.commit()
        self.git("update-ref", "refs/remotes/origin/main", self.base)
        (self.repo / "migrations/0889_uncovered.sql").unlink()
        (self.repo / "migrations/0887_pending.sql").write_text("select 3;\n")
        with self.assertRaisesRegex(ValueError, "cannot be renamed, deleted or edited"):
            renumber.plan(self.repo, None, {})

    def test_generator_still_cannot_delete_retained_covered_sql(self):
        self.retained_main()
        (self.repo / "migrations/0887_pending.sql").write_text("select 3;\n")
        self.snapshot_generator(delete_retained=True)
        result = renumber.plan(self.repo, None, {})
        with self.assertRaisesRegex(ValueError, "generator changed an applied main migration"):
            renumber.apply(self.repo, result)
        self.assertEqual((self.repo / "migrations/0888_history.sql").read_bytes(),
                         self.history["0888_history.sql"].encode())
        self.assertTrue((self.repo / "migrations/0887_pending.sql").is_file())
        self.assertFalse((self.repo / "migrations/0889_pending.sql").exists())

    def test_allocator_and_reservation_keep_covered_claims(self):
        with patch.object(allocator, "REPO", str(self.repo)), patch.object(allocator, "worktree_paths", return_value=[str(self.repo)]), patch.object(sys, "argv", ["next-migration", "--quiet"]):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(allocator.main(), 0)
            self.assertEqual(output.getvalue().strip(), "0889")
            with patch.object(reservation, "REPO", str(self.repo)), patch.object(reservation, "read_reservations", return_value=[]):
                claims, _ = reservation._scan_claims(allocator)
            self.assertIn("0888_history.sql", claims[888])
            self.assertIn(533, claims)

    def test_role_grants_compose_snapshot_and_active_suffix_with_covered_files_absent(self):
        from schema_snapshot_grants import SECTION_MARKER, SECTION_END, load_current_grants_to_role
        schema = self.repo / "db/schema.sql"
        schema.write_text(schema.read_text() + SECTION_MARKER + "\n" +
                          "grant select on table public.history to carr_writer;\n" + SECTION_END + "\n")
        (self.repo / "migrations/0889_acl.sql").write_text(
            "grant insert on table public.history to carr_writer;\n")
        self.assertEqual(load_current_grants_to_role(schema, self.repo / "migrations", "carr_writer"),
                         ["grant insert on table public.history to carr_writer;",
                          "grant select on table public.history to carr_writer;"])

    def test_same_sql_with_changed_baseline_provenance_is_refused(self):
        alternate = self.git("commit-tree", self.git("rev-parse", self.source + "^{tree}"),
                             "-p", self.source, "-m", "alternate source")
        path = self.repo / "db/migration-baseline.json"
        document = json.loads(path.read_text())
        document["source_revision"] = alternate
        path.write_text(json.dumps(document))
        self.commit()
        with self.assertRaises(ValueError):
            integration.validate_candidate(self.repo, self.base)
        with self.assertRaises(ValueError):
            renumber.plan(self.repo, None, {})

    def test_manifest_errors_refuse_allocation(self):
        (self.repo / "db/migration-baseline.json").write_text("{}")
        with patch.object(allocator, "REPO", str(self.repo)), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(allocator.main(), 1)

    def test_edited_retained_history_refuses_source_proof(self):
        (self.repo / "migrations/0888_history.sql").write_text("select 999;\n")
        self.commit()
        with self.assertRaises(ValueError):
            integration.validate_candidate(self.repo, self.base)

if __name__ == "__main__":
    unittest.main()
