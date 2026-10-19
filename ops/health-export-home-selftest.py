#!/usr/bin/env python3
"""Exercise recovery freshness watches without running health's external checks."""
import ast
import glob
import importlib
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from exporters import common

SOURCE = ROOT / "tools/health-check.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))
WATCH_CODE = compile(ast.Module(body=[
    node for node in TREE.body
    if (isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "WATCH"
                for target in node.targets))
    or (isinstance(node, ast.ImportFrom) and node.module == "exporters.common")
    or (isinstance(node, ast.FunctionDef) and node.name in {"newest", "age_days"})
], type_ignores=[]), str(SOURCE), "exec")
EXPORTS = {
    "GEN lead-registry": "DNA/Leads/lead-registry.xlsx",
    "GEN client-roster": "DNA/Clients/client-roster.xlsx",
    "GEN vendors": "DNA/Network/vendors.xlsx",
    "GEN deals json": "DNA/Deal Management/panhandle-team-deals.json",
}


class ExportFreshness(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "exports"
        self.vault = Path(self.temp.name) / "vault"
        self.health = dict(os=os, glob=glob, time=time, VAULT=str(self.vault))
        with patch.dict(os.environ, {"CARR_EXPORT_HOME": str(self.home)}):
            importlib.reload(common)
            exec(WATCH_CODE, self.health)
        self.rows = {row[0]: row for row in self.health["WATCH"]}

    def write(self, root, relative, age=0):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic export\n", encoding="utf-8")
        stamp = time.time() - age * 86400
        os.utime(path, (stamp, stamp))
        return str(path)

    def test_fresh_exports_are_found_in_export_home(self):
        for name, relative in EXPORTS.items():
            with self.subTest(export=name):
                expected = self.write(self.home, relative)
                _, pattern, cadence, _, _ = self.rows[name]
                actual = self.health["newest"](pattern)
                self.assertEqual(actual, expected)
                self.assertLess(self.health["age_days"](actual), cadence)

    def test_vault_copy_does_not_hide_missing_export(self):
        for name, relative in EXPORTS.items():
            with self.subTest(export=name):
                self.write(self.vault, relative)
                self.assertIsNone(self.health["newest"](self.rows[name][1]))

    def test_fresh_vault_copy_does_not_hide_stale_export(self):
        for name, relative in EXPORTS.items():
            with self.subTest(export=name):
                expected = self.write(self.home, relative, age=2)
                self.write(self.vault, relative)
                _, pattern, cadence, _, _ = self.rows[name]
                actual = self.health["newest"](pattern)
                self.assertEqual(actual, expected)
                self.assertGreater(self.health["age_days"](actual), cadence)

    def test_unrelated_relative_watch_still_reads_vault(self):
        pattern = self.rows["Lead Board"][1]
        expected = self.write(self.vault, pattern)
        self.write(self.home, pattern)
        self.assertEqual(self.health["newest"](pattern), expected)


if __name__ == "__main__":
    unittest.main()
