#!/usr/bin/env python3
"""Pin reviewed selector admission and fail-closed export behavior."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("seeds", ROOT / "ops/snapshot-seeds.py")
assert spec is not None and spec.loader is not None
seeds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seeds)


class ReviewedSeeds(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.repo = Path(self.scratch.name)
        self.path = self.repo / "ops/config/snapshot-seed-coverage.json"
        self.path.parent.mkdir(parents=True)
        self.seed = {"table": "public.fixture", "migration": "9999_fixture.sql",
                     "where": "key='reviewed'", "order_by": "key", "exact_rows": 1,
                     "columns": ["key"]}
        self.config = {"carried_subset": {"fixture": "reviewed synthetic configuration"},
                       "renders": [self.seed]}
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.config))

    def test_excluded_table_cannot_be_exported(self):
        self.config["excluded"] = self.config.pop("carried_subset")
        self.save()
        with self.assertRaisesRegex(ValueError, "not classified for carrying: public.fixture"):
            seeds.declarations(self.repo)

    def test_pending_migration_does_not_read_seed_rows(self):
        with patch.object(seeds, "query", return_value=[]) as query:
            self.assertEqual(seeds.selected_rows(self.repo, ["psql"]), [])
            self.assertEqual(query.call_count, 1)

    def test_missing_applied_seed_names_table_and_migration(self):
        with patch.object(seeds, "query", side_effect=[["9999_fixture.sql"], []]):
            with self.assertRaisesRegex(ValueError, "public.fixture from 9999_fixture.sql"):
                seeds.selected_rows(self.repo, ["psql"])

    def test_only_reviewed_columns_enter_restore(self):
        with patch.object(seeds, "query", side_effect=[["9999_fixture.sql"], [{"key": "reviewed"}]]) as query:
            selected = seeds.selected_rows(self.repo, ["psql"])
        self.assertIn('select "key" from public.fixture', query.call_args.args[1])
        self.assertIn('(\"key\") select \"key\"', "\n".join(seeds.render(selected)))

    def test_changed_rebuilt_rows_name_the_seed(self):
        with patch.object(seeds, "query", side_effect=[
            ["9999_fixture.sql"], [{"key": "reviewed", "value": 31}],
            ["9999_fixture.sql"], [{"key": "reviewed", "value": 30}],
        ]):
            with self.assertRaisesRegex(ValueError, "public.fixture from 9999_fixture.sql"):
                seeds.compare(self.repo, ["source"], ["rebuilt"])


if __name__ == "__main__":
    unittest.main()
