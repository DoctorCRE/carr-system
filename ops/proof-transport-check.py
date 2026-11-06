#!/usr/bin/env python3
"""Admit proof network access only through the shared reader and audited commands.

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
import sys

ROOT = Path(__file__).resolve().parents[1]
# Pure standard-library computation is outside transport admission.

NETWORK_MODULES = {
    'socket', '_socket', 'ssl', '_ssl', 'http', 'urllib', 'urllib3',
    'requests', 'httpx', 'aiohttp', 'ftplib', 'smtplib', 'imaplib',
    'poplib', 'nntplib', 'telnetlib', 'xmlrpc', 'asyncio', 'socketserver',
}
INDIRECT_MODULES = {'importlib', 'ctypes', '_ctypes', 'builtins', 'runpy',
                    'multiprocessing', '_posixsubprocess', 'pty'}
OS_PROCESS = {'system', 'popen', 'startfile', 'fork', 'forkpty', 'posix_spawn', 'posix_spawnp'}


def restricted(name):
    root = name.split('.')[0]
    if name == 'urllib.parse' or name.startswith('urllib.parse.'):
        return False
    if name in {'lib', 'lib.release_reader', 'lib.release_reader.read'}:
        return False
    return (root in NETWORK_MODULES or root in INDIRECT_MODULES or
            root not in sys.stdlib_module_names)


PROCESSES = json.loads((ROOT / 'ops/config/proof-process-allowlist.json').read_text())


def violations(source, path=None):
    tree = ast.parse(source)
    failures = []
    declaration = PROCESSES.get(path, {})
    process_allowed = (bool(declaration.get('commands')) and
                       hashlib.sha256(source.encode()).hexdigest() == declaration.get('sha256'))
    bindings = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                name = alias.name if isinstance(node, ast.Import) else '.'.join(filter(None, [node.module, alias.name]))
                root = name.split('.')[0]
                if isinstance(node, ast.Import):
                    bindings[alias.asname or root] = name if alias.asname else root
                else:
                    bindings[alias.asname or alias.name] = name
                if (getattr(node, 'level', 0) or
                        (isinstance(node, ast.ImportFrom) and alias.name in
                         {'os', 'sys', 'subprocess', 'socket', 'urllib', 'modules'}) or
                        (root == 'subprocess' and not process_allowed) or
                        (name.startswith('os.') and not process_allowed and
                         (name[3:] in OS_PROCESS or name[3:].startswith(('exec', 'spawn')))) or
                        (root != 'subprocess' and restricted(name))):
                    failures.append((node.lineno, f'undeclared network/process import {name}; use lib.release_reader or declare audited commands'))
        if isinstance(node, ast.Name) and node.id in {'__import__', 'eval', 'exec', 'compile', 'getattr', 'globals', 'locals', 'vars'}:
            failures.append((node.lineno, 'dynamic capability lookup is not admitted'))

    def qualified(node):
        if isinstance(node, ast.Name):
            return bindings.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return qualified(node.value) + '.' + node.attr
        return ''

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Attribute, ast.Name)):
            continue
        name = qualified(node)
        parts = name.split('.')
        if isinstance(node, ast.Attribute) and node.attr.startswith('__') and node.attr not in {'__class__', '__name__'}:
            failures.append((node.lineno, 'reflective capability lookup is not admitted'))
        if ((name.startswith('os.') and (parts[1] in OS_PROCESS or parts[1].startswith(('exec', 'spawn')))
             and not process_allowed) or
                name.startswith('sys.modules') or
                (name.startswith('urllib.') and not name.startswith('urllib.parse')) or
                (name.startswith('lib.release_reader.') and parts[2] != 'read') or
                (isinstance(node, ast.Attribute) and node.attr in {'os', 'sys', 'subprocess', 'socket', 'urllib'})):
            failures.append((node.lineno, f'undeclared network/process capability {name}'))
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
