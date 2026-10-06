"""Tests for tools/pg-env-exec.py: the URL moves into PG* variables, never argv.

  .venv/bin/python -m unittest tools/test_pg_env_exec.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "pg-env-exec.py"
spec = importlib.util.spec_from_file_location("pg_env_exec", TOOL)
if spec is None or spec.loader is None:
    raise ImportError(f"cannot load {TOOL}")
pe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pe)

URL = "postgresql://owner%40x:p%2Fss%20w@ep-host.example:6543/neondb?sslmode=require&channel_binding=require&options=-c%20search_path%3Dops"  # ci-secret-scan: allow (made-up fixture password)


class EnvFor(unittest.TestCase):
    def test_every_part_lands_in_its_variable_decoded(self):
        env = pe.env_for(URL, {"PGOPTIONS": "-c default_transaction_read_only=on", "PGSERVICE": "stale", "KEEP": "1"})
        self.assertEqual(env["PGHOST"], "ep-host.example")
        self.assertEqual(env["PGPORT"], "6543")
        self.assertEqual(env["PGUSER"], "owner@x")
        self.assertEqual(env["PGPASSWORD"], "p/ss w")
        self.assertEqual(env["PGDATABASE"], "neondb")
        self.assertEqual(env["PGSSLMODE"], "require")
        self.assertEqual(env["PGCHANNELBINDING"], "require")
        # the caller's read-only guard survives beside the URL's own options
        self.assertEqual(env["PGOPTIONS"], "-c default_transaction_read_only=on -c search_path=ops")
        self.assertNotIn("PGSERVICE", env)
        self.assertEqual(env["KEEP"], "1")

    def test_unknown_parameters_and_non_postgres_urls_are_refused(self):
        with self.assertRaisesRegex(ValueError, "unrecognised"):
            pe.env_for("postgresql://u@h/db?sslmode=require&weird=1", {})
        with self.assertRaises(ValueError):
            pe.env_for("mysql://u@h/db", {})


class Exec(unittest.TestCase):
    def test_the_child_sees_the_password_in_its_environment_and_not_in_argv(self):
        probe = "import json,os,sys; print(json.dumps({'argv': sys.argv, 'pw': os.environ.get('PGPASSWORD'), 'url': os.environ.get('SECRET_URL')}))"
        out = subprocess.run([sys.executable, str(TOOL), "SECRET_URL", sys.executable, "-c", probe],
                             env={**os.environ, "SECRET_URL": URL}, capture_output=True, text=True, check=True)
        got = json.loads(out.stdout)
        self.assertEqual(got["pw"], "p/ss w")
        self.assertIsNone(got["url"])  # the URL variable itself is not passed on
        self.assertFalse(any("p%2Fss" in a or "p/ss" in a for a in got["argv"]))

    def test_an_empty_variable_is_refused(self):
        env = {k: v for k, v in os.environ.items() if k != "NOPE"}
        got = subprocess.run([sys.executable, str(TOOL), "NOPE", "true"], env=env, capture_output=True, text=True)
        self.assertEqual(got.returncode, 2)
        self.assertIn("$NOPE is empty", got.stderr)


class RehearsalUsesIt(unittest.TestCase):
    def test_no_connection_url_is_on_a_psql_command_line_in_the_rehearsal(self):
        text = (REPO / "bin" / "restore-rehearse.sh").read_text()
        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                continue
            self.assertNotRegex(line, r'psql\s+"\$(PROD_URL|RESTORE_URL|BRANCH_ADMIN_URL)"', line)


if __name__ == "__main__":
    unittest.main()
