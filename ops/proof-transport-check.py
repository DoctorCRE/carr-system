#!/usr/bin/env python3
"""Require proof runners to use lib.release_reader for HTTP transport."""
import argparse
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NETWORK = {'urllib', 'http', 'requests', 'httpx', 'aiohttp', 'socket', 'httpcore'}
SAFE = {'urllib.parse', 'urllib.error', 'http.cookies', 'http.cookiejar'}


def violations(source):
    tree = ast.parse(source)
    failures = []
    for node in ast.walk(tree):
        modules = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                   else [node.module or ''] if isinstance(node, ast.ImportFrom) else [])
        for module in modules:
            if module.split('.')[0] in NETWORK and module not in SAFE:
                failures.append((node.lineno, 'HTTP transport must use lib.release_reader'))
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.strip().split(' ', 1)[0] in {'curl', 'wget'}:
                failures.append((node.lineno, 'HTTP subprocess must use lib.release_reader'))
    return failures


def check(root):
    failures = []
    for path in sorted((root / 'tools').glob('prove-*.py')):
        try:
            failures.extend(f'{path.relative_to(root)}:{line}: {reason}'
                            for line, reason in violations(path.read_text()))
        except SyntaxError as error:
            failures.append(f'{path.relative_to(root)}:{error.lineno}: invalid Python')
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    failures = check(args.root)
    print('\n'.join(failures) if failures else 'Proof HTTP transport uses lib.release_reader')
    return bool(failures)


if __name__ == '__main__':
    raise SystemExit(main())
