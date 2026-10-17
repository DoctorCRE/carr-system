"""Selftest for ops/db-acceptance-scope.py."""
import importlib.util
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location('scope', Path(__file__).with_name('db-acceptance-scope.py'))
scope = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scope)

failures = []


def check(name, ok):
    print(('  ok   ' if ok else '  FAIL ') + name)
    if not ok:
        failures.append(name)


for path in ('migrations/0363_x.sql', 'db/schema.sql', 'mcp-server/src/tools.js', 'ops/local-pg-ci.py',
             'ops/migration-shadow.py', 'tools/migrate.py', 'tools/test_migration_claims.py',
             'bin/schema-snapshot.sh', 'run.sh', '.github/workflows/db-acceptance.yml',
             'ops/config/rule-enforcement-map.json', 'ops/ci.sh'):
    check(f'{path} runs the full lane', scope.needs_full([path]))
for path in ('hooks/rule-pack-preuse-reselection.py', 'ops/config/gate-baseline.json',
             'ops/rule-pack-preuse-reselection-selftest.py', 'tools/flash-run.py', 'README.md',
             'docs/agents/domain.md', 'tools/regen.py', '.github/workflows/ci.yml'):
    check(f'{path} alone skips the lane', not scope.needs_full([path]))
check('one DB file among others runs the lane',
      scope.needs_full(['README.md', 'hooks/a.py', 'migrations/0400_y.sql']))
check('an empty diff skips', not scope.needs_full([]))
import os
for event in ('schedule', 'workflow_dispatch', 'push'):
    os.environ['GITHUB_EVENT_NAME'] = event
    check(f'{event} always runs full', scope.decide())
os.environ['GITHUB_EVENT_NAME'] = 'pull_request'
os.environ['GITHUB_EVENT_PATH'] = '/nonexistent'
check('an unreadable event runs full', scope.decide())

print(f'db-acceptance-scope-selftest: {len(failures)} failure(s)')
sys.exit(1 if failures else 0)
