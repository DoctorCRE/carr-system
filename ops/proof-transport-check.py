#!/usr/bin/env python3
"""Admit only declared imports and audited proof process commands.

Process declarations pin the entire runner, including indirect argv construction
and embedded scripts. Audit every command before updating a pin. The selected
call wrapper and explicit fixture executables are trusted caller inputs. This
source check is not a sandbox for hostile Python or substituted executables.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Exact modules and public members, never a list of known network clients.
IMPORTS = {
    'argparse': {'ArgumentParser'}, 'datetime': {'datetime', 'timezone'},
    'json': {'loads', 'dumps'}, 'math': {'isfinite'},
    'os': {'read', 'killpg'}, 'pathlib': {'Path'}, 're': {'compile', 'fullmatch', 'I'},
    'selectors': {'DefaultSelector', 'EVENT_READ'},
    'signal': {'SIGALRM', 'SIGKILL', 'ITIMER_REAL', 'signal', 'setitimer'},
    'sys': {'path', 'argv', 'executable', 'stderr', 'stdout', 'exit'},
    'tempfile': {'TemporaryFile', 'TemporaryDirectory'},
    'time': {'monotonic', 'sleep'}, 'urllib.parse': {'urlsplit', 'urlparse', 'quote', 'urlencode'},
    'uuid': {'UUID', 'uuid4'}, 'lib.release_reader': {'read'},
}
PROCESSES = json.loads((ROOT / 'ops/config/proof-process-allowlist.json').read_text())


def violations(source, path=None):
    tree = ast.parse(source)
    failures = []
    declaration = PROCESSES.get(path, {})
    process_allowed = (bool(declaration.get('commands')) and
                       hashlib.sha256(source.encode()).hexdigest() == declaration.get('sha256'))
    modules = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                module = alias.name if isinstance(node, ast.Import) else node.module or ''
                allowed = (module in IMPORTS and not getattr(node, 'level', 0) and
                           (isinstance(node, ast.Import) or alias.name in IMPORTS[module]))
                if module == 'subprocess' and process_allowed:
                    allowed = True
                if not allowed:
                    failures.append((node.lineno, f'undeclared import {module}.{alias.name}; use lib.release_reader or declare audited commands'))
                if isinstance(node, ast.Import):
                    modules[alias.asname or alias.name.split('.')[0]] = module if alias.asname else alias.name.split('.')[0]
        if isinstance(node, ast.Name) and node.id in {'__import__', 'eval', 'exec', 'compile', 'getattr', 'globals', 'locals', 'vars'}:
            failures.append((node.lineno, 'dynamic capability lookup is not admitted'))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr.startswith('__') and node.attr not in {'__class__', '__name__'}:
            failures.append((node.lineno, 'reflective capability lookup is not admitted'))
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            module = modules.get(node.value.id)
            allowed = IMPORTS.get(module, {'parse'} if module == 'urllib' else {'release_reader'} if module == 'lib' else None)
            if allowed is not None and node.attr not in allowed:
                failures.append((node.lineno, f'undeclared {module} capability {node.attr}'))
    return failures


def check(root):
    failures = []
    for path in sorted((root / 'tools').glob('prove-*.py')):
        try:
            failures.extend(f'{path.relative_to(root)}:{line}: {reason}'
                            for line, reason in violations(path.read_text(), path.relative_to(root).as_posix()))
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
