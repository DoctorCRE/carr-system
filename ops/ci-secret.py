#!/usr/bin/env python3
"""Run secret-class qualification and scans, preserving logs and program evidence."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import time


def qualification_programs(root=Path('.')):
    return sorted(path.relative_to(root).as_posix() for path in root.glob('security/test_*.py'))


def check(logdir, python):
    diff_range = os.environ.get('CARR_CI_RANGE', '')
    range_args = ['--range', *re.findall(r'[^ \t\n]+', diff_range)] if diff_range else []
    leak_args = ['--ci'] if os.environ.get('GITHUB_EVENT_NAME') == 'pull_request' else range_args or ['--staged']
    receiver = os.environ.get('CARR_PUSH_RECEIVER', '')
    if receiver:
        leak_args = [*leak_args, '--remote', receiver]
    scope = 'blobs pushed in ' + diff_range if diff_range else 'tracked files'
    checks = [
        ('leak-guard-tests', [python, test], 'leak guard qualification failed')
        for test in qualification_programs() or ['security/test_*.py']
    ]
    checks.extend((
        ('leak-guard', [python, 'security/leak_guard.py', *leak_args],
         'secret or client data in introduced Git blobs'),
        ('secret', [python, 'ops/ci-secret-scan.py', *range_args],
         'credential-shaped content found in ' + scope),
    ))
    for name, command, failure in checks:
        log = logdir / (name + '.log')
        start = time.time()
        with log.open('wb') as output:
            try:
                rc = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT).returncode
                if rc < 0:
                    rc = 128 - rc
            except OSError as error:
                output.write(f'{command[0]}: {error.strerror}\n'.encode())
                rc = 127
        Path(str(log) + '.rc').write_text(f'{rc}\n')
        with (logdir / 'programs.tsv').open('a') as records:
            records.write(f'secret\t{name}\t{rc}\t{start:.6f}\t{time.time():.6f}\n')
        if rc:
            diagnostics = []
            for line in log.read_text(errors='replace').splitlines()[:20]:
                finding = re.fullmatch(r'\s*(.+?:[0-9]+)\s+([A-Za-z0-9_-]+)(?:\s.*)?', line)
                if finding:
                    diagnostics.append(f'{finding[1]} {finding[2]}')
            redacted = subprocess.run([python, 'ops/ci-secret-scan.py', '--redact'],
                input='\n'.join(diagnostics), text=True, capture_output=True)
            if redacted.returncode == 0:
                print(redacted.stdout, file=sys.stderr)
            print(failure)
            return 1
    print('no shaped credential in ' + scope)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--logdir', required=True, type=Path)
    parser.add_argument('--python', default=sys.executable)
    args = parser.parse_args()
    return check(args.logdir, args.python)


if __name__ == '__main__':
    sys.exit(main())
