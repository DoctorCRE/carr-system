"""Selftest for ops/db-acceptance-scope.py."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env  # noqa: E402

ENV = fixture_env()
spec = importlib.util.spec_from_file_location('scope', Path(__file__).with_name('db-acceptance-scope.py'))
assert spec is not None and spec.loader is not None
scope = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scope)

failures: list[str] = []


def check(name, ok):
    print(('  ok   ' if ok else '  FAIL ') + name)
    if not ok:
        failures.append(name)


check('transitive continuity manifest runs database proof',
      scope.needs_full(['mcp-server/continuity-reference-manifest.mjs']))

# A new import outside the declared trees must enter scope automatically.
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    (root / 'mcp-server/test').mkdir(parents=True)
    (root / 'mcp-server/test/proof.mjs').write_text("import '../adapter.mjs';\n")
    (root / 'mcp-server/adapter.mjs').write_text("export * from '../shared/contract.mjs';\n")
    (root / 'shared').mkdir()
    (root / 'shared/contract.mjs').write_text('export const value = 1;\n')
    inputs = scope.relative_imports([root / 'mcp-server/test/proof.mjs'], root)
    check('a new transitive proof import cannot remain unlisted',
          'shared/contract.mjs' in inputs and 'mcp-server/adapter.mjs' in inputs)

for path in ('migrations/0363_x.sql', 'db/schema.sql', 'mcp-server/src/tools.js', 'ops/local-pg-ci.py',
             'ops/migration-shadow.py', 'tools/migrate.py', 'tools/test_migration_claims.py',
             'bin/schema-snapshot.sh', 'run.sh', '.github/workflows/db-acceptance.yml',
             'ops/config/rule-enforcement-map.json', 'ops/ci.sh'):
    check(f'{path} runs the full lane', scope.needs_full([path]))
for path in ('hooks/rule-pack-preuse-reselection.py', 'web/app.js', 'README.md',
             'docs/agents/domain.md'):
    check(f'{path} alone skips the lane', not scope.needs_full([path]))
for path in subprocess.run(['git', 'ls-files', '*.sql'], cwd=Path(__file__).resolve().parents[1],
                           env=ENV, capture_output=True, text=True, check=True).stdout.splitlines():
    check(f'tracked SQL {path} runs the lane', scope.needs_full([path]))
check('one DB file among others runs the lane',
      scope.needs_full(['README.md', 'hooks/a.py', 'migrations/0400_y.sql']))
# Derive the lane's own inputs from the files that name them, so the list cannot drift.
repo = Path(__file__).resolve().parents[1]
named: set[str] = set()
for src in ('ops/ci.sh', 'ops/local-pg-ci.py', 'ops/db_acceptance_programs.py'):
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

# ROOT-relative resources are inputs even when they live outside database directories.
tracked = set(subprocess.run(['git', 'ls-files'], cwd=repo, env=ENV,
                             capture_output=True, text=True, check=True).stdout.splitlines())
def resource_parts(node):
    if isinstance(node, ast.Name) and node.id in ('ROOT', 'REPO'):
        return []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left, right = resource_parts(node.left), resource_parts(node.right)
        if left is not None and right is not None:
            return left + right
    return None

programs = {path for path in (repo / 'ops').glob('*-gate.py') if '# ci: db-gate' in path.read_text()}
programs.update((repo / 'ops').glob('*local-pg-acceptance.py'))
resources = set()
for program in programs:
    for node in ast.walk(ast.parse(program.read_text())):
        parts = resource_parts(node)
        if parts:
            relative = Path(*parts).as_posix()
            if relative in tracked:
                resources.add(relative)
check('inventory inspects at least 70 database proof programs', len(programs) >= 70)
for path in sorted(resources):
    check(f'database proof resource {path} runs the lane', scope.needs_full([path]))

# A rename out of a protected tree must list the old path (--no-renames).
with tempfile.TemporaryDirectory() as tmp:
    def vcs(*args: str) -> str:
        return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *args], cwd=tmp, env=ENV,
                              check=True, capture_output=True, text=True).stdout.strip()
    vcs('init', '-q', '-b', 'main')
    Path(tmp, 'migrations').mkdir()
    Path(tmp, 'migrations/0001_a.sql').write_text('select 1;\n' * 20)
    vcs('add', '-A')
    vcs('commit', '-q', '-m', 'a')
    base = vcs('rev-parse', 'HEAD')
    Path(tmp, 'web').mkdir()
    Path(tmp, 'web/app.js').write_text('export const app = true;\n')
    vcs('add', 'web/app.js')
    vcs('commit', '-q', '-m', 'app-only change')
    setattr(scope, 'ROOT', Path(tmp))
    app_event = {'pull_request': {'base': {'ref': 'main', 'sha': base}, 'head': {'sha': vcs('rev-parse', 'HEAD')}}}
    event_file = Path(tmp, 'event.json')
    event_file.write_text(json.dumps(app_event))
    os.environ['GITHUB_EVENT_NAME'] = 'pull_request'
    os.environ['GITHUB_EVENT_PATH'] = str(event_file)
    check('a real app-only PR diff skips database proof', not scope.decide())
    Path(tmp, 'migrations/0002_caf\u00e9.sql').write_text('select 2;\n')
    vcs('add', 'migrations/0002_caf\u00e9.sql')
    vcs('commit', '-q', '-m', 'SQL filename requires quoting')
    quoted_event = {'pull_request': {'base': {'ref': 'main', 'sha': base}, 'head': {'sha': vcs('rev-parse', 'HEAD')}}}
    event_file.write_text(json.dumps(quoted_event))
    check('a SQL filename that git quotes still runs database proof', scope.decide())
    vcs('mv', 'migrations/0001_a.sql', 'notes.txt')
    vcs('commit', '-q', '-m', 'b')
    setattr(scope, 'ROOT', Path(tmp))
    vcs('update-ref', 'refs/remotes/origin/main', 'HEAD~1')
    pr_event: dict[str, Any] = {'pull_request': {'base': {'ref': 'main'}, 'head': {'sha': vcs('rev-parse', 'HEAD')}}}
    paths = scope.changed_paths(pr_event) or []
    check('a rename out of migrations/ lists the old path', 'migrations/0001_a.sql' in paths)
    check('a rename out of migrations/ runs the full lane', scope.needs_full(paths))
    event_file.write_text(json.dumps(pr_event))
    check('a real migration PR diff runs database proof', scope.decide())

check('an empty diff skips', not scope.needs_full([]))
for event in ('schedule', 'workflow_dispatch', 'push'):
    os.environ['GITHUB_EVENT_NAME'] = event
    check(f'{event} always runs full', scope.decide())
os.environ['GITHUB_EVENT_NAME'] = 'pull_request'
os.environ['GITHUB_EVENT_PATH'] = '/nonexistent'
check('an unreadable event runs full', scope.decide())

print(f'db-acceptance-scope-selftest: {len(failures)} failure(s)')
sys.exit(1 if failures else 0)
