#!/usr/bin/env python3
"""Reject ambient JavaScript clocks in MCP tests unless live time is required."""
from pathlib import Path
import re
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
    'relationship-network-postgres.test.mjs': 'Network hold fixtures are relative to the database clock.',
}
CLOCK = re.compile(r'\bDate\s*\.\s*now\s*\(|\bnew\s+Date\s*\(\s*\)')


def code_only(source):
    masked = list(source)
    stack, previous, i = [], '', 0
    def hide(start, end, literal=False):
        for offset in range(start, end):
            if source[offset] != '\n':
                masked[offset] = '_' if literal else ' '
    while i < len(source):
        start, char = i, source[i]
        if stack and stack[-1] == 'template':
            if source.startswith('${', i):
                stack.append(0)
                i += 2
                previous = '('
            elif char == '`':
                stack.pop()
                hide(i, i + 1, True)
                i += 1
            else:
                i += 2 if char == '\\' else 1
                hide(start, min(i, len(source)), True)
            continue
        if char.isspace():
            i += 1
            continue
        if source.startswith('//', i) or source.startswith('/*', i):
            closing = '\n' if source[i + 1] == '/' else '*/'
            end = source.find(closing, i + 2)
            i = len(source) if end < 0 else end + len(closing)
            hide(start, i)
            continue
        if char in "\"'" or (char == '/' and previous in {'', '=', '(', '[', ',', ':', 'return', '=>', '!', '?', ';', '{'}):
            quote, bracket = char, False
            i += 1
            while i < len(source):
                current = source[i]
                if current == '\\':
                    i += 2
                    continue
                if quote == '/':
                    if current == '[': bracket = True
                    if current == ']': bracket = False
                i += 1
                if current == quote and not bracket:
                    break
            hide(start, min(i, len(source)), True)
            previous = '_'
            continue
        if char == '`':
            stack.append('template')
            hide(i, i + 1, True)
        elif stack and isinstance(stack[-1], int):
            if char == '{': stack[-1] += 1
            if char == '}':
                if stack[-1] == 0:
                    stack.pop()
                else:
                    stack[-1] -= 1
        word = re.match(r'[\w$]+|=>|.', source[i:])[0]
        previous = word
        i += len(word)
    return ''.join(masked)


def violations(root, paths=None):
    failures = []
    for path in sorted(root.rglob('*') if paths is None else paths):
        if path.suffix not in {'.js', '.mjs', '.cjs', '.ts'}:
            continue
        relative = path.relative_to(root).as_posix()
        if relative == 'helpers/fixed-clock.mjs' or relative in ALLOW:
            continue
        source = code_only(path.read_text())
        for match in CLOCK.finditer(source):
            line = source.count('\n', 0, match.start()) + 1
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
