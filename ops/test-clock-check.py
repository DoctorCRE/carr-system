#!/usr/bin/env python3
"""Reject ambient JavaScript clocks in MCP tests unless live time is required."""
from pathlib import Path
import json
import sys
import subprocess

# These tests cross a process or database clock that Date-only mocking cannot replace.
ALLOW = {
    'lead-automation-concurrency.postgres.mjs': 'Contact timestamps must match the database-created lead lifetime.',
    'invoice-review-regressions.postgres.mjs': 'Invoice occurrence is validated against the database clock.',
    'call-mode-rehearsal.test.mjs': 'Python capture consent checks freshness in another process.',
    'recovery-matrix.v5.test.mjs': 'The CLI intentionally validates receipt freshness using its own clock.',
    'salesforce-read-run-store-rw02-postgres.test.mjs': 'Asserts agreement between host and database clocks.',
    'program-controller-census-role-boundary.v5.test.mjs': 'Database census records require live freshness.',
    'shared-oauth-worker.test.mjs': 'Token expiry runs in a separate Workerd clock.',
}
# Acorn supplies JavaScript syntax; Python owns the clock policy and reporting.
PARSER = r"""
const {parse} = require(process.argv[1]);
const tree = parse(require('fs').readFileSync(0, 'utf8'),
  {ecmaVersion: 'latest', sourceType: 'module', locations: true});
const nodes = [];
function visit(node) {
  if (!node || typeof node !== 'object') return;
  if (node.type === 'CallExpression' || node.type === 'NewExpression') {
    const name = node.callee.type === 'Identifier' ? node.callee.name :
      node.callee.type === 'MemberExpression' && !node.callee.computed ?
      `${node.callee.object.name}.${node.callee.property.name}` : '';
    nodes.push([node.type === 'NewExpression', name, node.arguments.length, node.loc.start.line]);
  }
  for (const value of Object.values(node)) {
    if (Array.isArray(value)) value.forEach(visit);
    else if (value && typeof value === 'object') visit(value);
  }
}
visit(tree);
console.log(JSON.stringify(nodes));
"""


def violations(root, paths=None):
    failures = []
    for path in sorted(root.rglob('*') if paths is None else paths):
        if path.suffix not in {'.js', '.mjs', '.cjs'}:
            continue
        relative = path.relative_to(root).as_posix()
        if relative == 'helpers/fixed-clock.mjs' or relative in ALLOW:
            continue
        parsed = subprocess.check_output(
            ['node', '-e', PARSER, str(Path(__file__).resolve().parents[1] /
                                      'mcp-server/node_modules/acorn')],
            input=path.read_text(), text=True)
        for is_new, expression, arguments, line in json.loads(parsed):
            if (is_new and expression == 'Date' and arguments == 0) or (not is_new and expression == 'Date.now'):
                failures.append(f'{path}:{line}: inject a fixed clock or document a live-time exception')
    return failures


if __name__ == '__main__':
    root = Path('mcp-server/test')
    if len(sys.argv) > 1:
        errors = violations(Path(sys.argv[1]))
    else:
        base = subprocess.check_output(['git', 'merge-base', 'origin/main', 'HEAD'], text=True).strip()
        changed = subprocess.check_output(
            ['git', 'diff', '--name-only', '--diff-filter=ACMR', base, '--', str(root)], text=True)
        untracked = subprocess.check_output(
            ['git', 'ls-files', '--others', '--exclude-standard', '--', str(root)], text=True)
        errors = violations(root, {Path(p) for p in (changed + untracked).splitlines()})
    print('\n'.join(errors) if errors else 'test clocks: OK')
    sys.exit(bool(errors))
