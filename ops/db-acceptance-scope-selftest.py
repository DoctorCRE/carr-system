"""Selftest for ops/db-acceptance-scope.py."""
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any

spec = importlib.util.spec_from_file_location('scope', Path(__file__).with_name('db-acceptance-scope.py'))
assert spec is not None and spec.loader is not None
scope = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scope)

failures: list[str] = []


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
# Derive the lane's own inputs from the files that name them, so the list cannot drift.
repo = Path(__file__).resolve().parents[1]
named: set[str] = set()
for src in ('ops/local-pg-ci.py', 'ops/db_acceptance_shards.py'):
    for lit in re.findall(r'["\']([A-Za-z0-9_./-]+\.(?:py|mjs|sql|sh))["\']', (repo / src).read_text()):
        if '/' in lit and (repo / lit).exists():
            named.add(lit)
        elif lit.endswith('.test.mjs') and (repo / 'mcp-server/test' / lit).exists():
            named.add('mcp-server/test/' + lit)
check('the lane names at least 15 repo inputs', len(named) >= 15)
for path in sorted(named):
    check(f'lane input {path} runs the full lane', scope.needs_full([path]))
for path in ('mcp-server/test/find-rule-supersedes.test.mjs', 'mcp-server/test/catch-me-up-writer-route.test.mjs',
             'mcp-server/test/lead-workspace-pg.test.mjs', 'tools/test-f03-production-migration.py',
             'ops/f03-receipt-validator.candidate.sql'):
    check(f'{path} runs the full lane', scope.needs_full([path]))

# A rename out of a protected tree must list the old path (--no-renames).
with tempfile.TemporaryDirectory() as tmp:
    def vcs(*args: str) -> str:
        return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *args], cwd=tmp,
                              check=True, capture_output=True, text=True).stdout.strip()
    vcs('init', '-q', '-b', 'main')
    Path(tmp, 'migrations').mkdir()
    Path(tmp, 'migrations/0001_a.sql').write_text('select 1;\n' * 20)
    vcs('add', '-A')
    vcs('commit', '-q', '-m', 'a')
    vcs('mv', 'migrations/0001_a.sql', 'notes.txt')
    vcs('commit', '-q', '-m', 'b')
    setattr(scope, 'ROOT', Path(tmp))
    vcs('update-ref', 'refs/remotes/origin/main', 'HEAD~1')
    pr_event: dict[str, Any] = {'pull_request': {'base': {'ref': 'main'}, 'head': {'sha': vcs('rev-parse', 'HEAD')}}}
    paths = scope.changed_paths(pr_event) or []
    check('a rename out of migrations/ lists the old path', 'migrations/0001_a.sql' in paths)
    check('a rename out of migrations/ runs the full lane', scope.needs_full(paths))

check('an empty diff skips', not scope.needs_full([]))
for event in ('schedule', 'workflow_dispatch', 'push'):
    os.environ['GITHUB_EVENT_NAME'] = event
    check(f'{event} always runs full', scope.decide())
os.environ['GITHUB_EVENT_NAME'] = 'pull_request'
os.environ['GITHUB_EVENT_PATH'] = '/nonexistent'
check('an unreadable event runs full', scope.decide())

print(f'db-acceptance-scope-selftest: {len(failures)} failure(s)')
sys.exit(1 if failures else 0)
