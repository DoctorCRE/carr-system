#!/usr/bin/env python3
"""Release database proof diagnostics and hosted PostgreSQL availability."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_abandon_gate', REPO / 'ops/release-abandon-local-pg-gate.py')
if spec is None or spec.loader is None:
    raise RuntimeError('cannot load the release database proof')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def workflow(name):
    result = subprocess.run(
        ['node', '-e', "const y=require('js-yaml'); const fs=require('fs'); process.stdout.write(JSON.stringify(y.load(fs.readFileSync(process.argv[1],'utf8'))))", str(REPO / '.github/workflows' / name)],
        cwd=REPO / 'mcp-server', capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


class ReleaseProofTests(unittest.TestCase):
    def test_fixture_failure_reports_the_cause_and_stays_red(self):
        error = io.StringIO()
        with patch.dict(os.environ, DATABASE_URL='postgres://fixture@127.0.0.1/fixture'), \
             patch.object(gate, 'isolated_ci_database', side_effect=RuntimeError('synthetic initdb failure')), \
             contextlib.redirect_stderr(error), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gate.main(), 1)
        self.assertIn('synthetic initdb failure', error.getvalue())
        self.assertNotIn('db-gate-proof:', error.getvalue())

    def test_hosted_database_jobs_supply_server_binaries(self):
        ci = workflow('ci.yml')['jobs']['classes']
        setup = next(step for step in ci['steps'] if step.get('name') == 'Install PostgreSQL 17')
        condition = setup.get('if', 'true')
        # Evaluate the workflow's actual predicate for every database-owning group.
        for group in ci['strategy']['matrix']['classes']:
            if not {'migration', 'gates'}.intersection(group.split(',')):
                continue
            expression = condition.replace('matrix.classes', repr(group)).replace('||', 'or').replace('&&', 'and')
            with self.subTest(group=group):
                self.assertTrue(eval(expression, {'__builtins__': {}, 'contains': lambda value, part: part in value}))
                self.assertIn('postgresql-17', setup['run'])
                self.assertIn('GITHUB_PATH', setup['run'])
        acceptance = workflow('db-acceptance.yml')['jobs']['acceptance']
        self.assertTrue(any('postgresql-17' in step.get('run', '') for step in acceptance['steps']))
        run = next(step for step in acceptance['steps'] if 'ops/local-pg-ci.py --class migration' in step.get('run', ''))
        self.assertEqual(run['env']['CARR_LOCAL_PG_BIN_DIR'], '/usr/lib/postgresql/17/bin')


if __name__ == '__main__':
    unittest.main()
