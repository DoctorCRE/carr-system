#!/usr/bin/env python3
"""Catch accidental proof transport bypasses by our own builders.

Coordinator decision: the threat model is ACCIDENTAL bypass, not a determined
adversary. The real failure was five proof runners using Python's default urllib
transport. Static checks cannot be complete against deliberate evasion and this
check makes no such claim. Classify capabilities rather than individual clients:
program execution requires audited non-network commands; socket/URL opening goes
through lib/release_reader.py or the declared call wrapper; dynamic imports and
string execution in runners are refused. Pure computation is outside scope.

Process declarations pin the entire runner, including indirect argv construction
and embedded scripts. Audit every command before updating a pin. The selected
call wrapper and explicit fixture executables are trusted caller inputs.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
NETWORK_MODULES = {
    'socket', '_socket', 'ssl', '_ssl', 'http.client', 'urllib.request',
    'urllib3', 'requests', 'httpx', 'aiohttp',
    'ftplib', 'smtplib', 'imaplib', 'poplib', 'nntplib', 'telnetlib',
    'xmlrpc.client', 'xmlrpc.server', 'socketserver', 'http.server',
}
PROGRAM_MODULES = {
    'subprocess', '_posixsubprocess', 'pty', 'webbrowser', 'multiprocessing',
    'ctypes', '_ctypes', 'cffi',
}
DYNAMIC_MODULES = {'importlib', 'builtins', 'runpy'}
OS_MODULES = {'os', 'posix', 'nt'}
OS_PROCESS = {'system', 'popen', 'startfile', 'fork', 'forkpty', 'posix_spawn', 'posix_spawnp'}
ASYNC_NETWORK = {'open_connection', 'open_unix_connection', 'create_connection',
                 'create_unix_connection', 'create_datagram_endpoint',
                 'start_server', 'start_unix_server', 'create_server',
                 'create_unix_server', 'connect_accepted_socket', 'sock_connect'}


def belongs(name, modules):
    return any(name == module or name.startswith(module + '.') for module in modules)


def capability(name):
    parts = name.split('.')
    if belongs(name, DYNAMIC_MODULES):
        return 'dynamic'
    if belongs(name, NETWORK_MODULES) or (
            parts[0] == 'asyncio' and any(part in ASYNC_NETWORK for part in parts[1:])):
        return 'network'
    if belongs(name, PROGRAM_MODULES) or (
            parts[0] in OS_MODULES and len(parts) > 1 and
            (parts[1] in OS_PROCESS or parts[1].startswith(('exec', 'spawn')) or parts[1] == '*')):
        return 'program'
    if name in {'lib', 'lib.release_reader', 'lib.release_reader.read'}:
        return None
    if parts[0] not in sys.stdlib_module_names:
        return 'unknown helper'
    return None


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
                kind = capability(name)
                if (getattr(node, 'level', 0) or alias.name == '*' or
                        (isinstance(node, ast.ImportFrom) and alias.name in
                         {'os', 'posix', 'nt', 'sys', 'subprocess', 'socket', 'urllib', 'modules'}) or
                        (kind is not None and (kind != 'program' or not process_allowed))):
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
        kind = capability(name) if '.' in name else None
        if ((kind in {'network', 'dynamic'} or (kind == 'program' and not process_allowed)) or
                name.startswith('sys.modules') or
                (name.startswith('lib.release_reader.') and parts[2] != 'read') or
                (isinstance(node, ast.Attribute) and node.attr in
                 {'os', 'posix', 'nt', 'sys', 'subprocess', 'socket', 'urllib'}) or
                (isinstance(node, ast.Attribute) and node.attr in ASYNC_NETWORK)):
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
