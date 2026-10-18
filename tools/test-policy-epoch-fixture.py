#!/usr/bin/env python3
"""Rollback-only fixture construction must leave usable planner estimates."""
import importlib.util
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "ops"))


class PlannerCursor:
    """Model PostgreSQL's un-analyzed bulk inserts: rows change, estimates do not."""
    def __init__(self):
        self.rows = 0
        self.estimate = -1
        self.value = 1

    def execute(self, query, params=()):
        normalized = " ".join(query.lower().split())
        if normalized.startswith("insert into ops.rule_load_layer"):
            self.rows += 1
        if normalized.startswith("analyze") and "ops.rule_load_layer" in normalized:
            self.estimate = self.rows
        self.value = 1
        return self

    def fetchone(self):
        return (self.value,)


class FixturePlannerTests(unittest.TestCase):
    def test_bulk_fixture_estimates_match_inserted_rows(self):
        for name in ("siep12-policy-epoch-local-pg-gate", "siep18-reference-monitor-local-pg-gate"):
            with self.subTest(gate=name):
                spec = importlib.util.spec_from_file_location(name, REPO / "ops" / (name + ".py"))
                assert spec and spec.loader
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                cursor = PlannerCursor()
                module.seed_reviewed_rule_projection(cursor)
                self.assertGreater(cursor.rows, 0)
                self.assertEqual(cursor.estimate, cursor.rows,
                                 "rollback-only fixture rows cannot be analyzed by autovacuum")


if __name__ == "__main__":
    unittest.main()
