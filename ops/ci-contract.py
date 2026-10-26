#!/usr/bin/env python3
"""Run the PostgreSQL path policy and existing workspace and Control Room contracts."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    failures = []
    try:
        paths = subprocess.run(
            ['git', 'grep', '-n', '-F', '/opt/homebrew/opt/' + 'postgresql@17', '--',
             '*.py', ':!lib/disposable_pg_fixture.py'], cwd=ROOT,
            stdout=subprocess.PIPE, text=True, errors='replace')
        if paths.returncode != 1:
            failures.append('PostgreSQL-paths')
            print('PostgreSQL paths must use lib/disposable_pg_fixture.py '
                  f'(grep exit {paths.returncode})')
            print(paths.stdout.rstrip())
    except OSError as exc:
        failures.append('PostgreSQL-paths')
        print(f'PostgreSQL path check could not run ({type(exc).__name__})')
    checks = (
        ('workspace/contracts/validate.mjs', 'workspace/contracts', ['node']),
        ('control-room/test/contracts.test.mjs', 'control-room/test/contracts.test.mjs', ['node', '--test']),
        ('control-room/test/work-request-projection.test.mjs',
         'control-room/test/work-request-projection.test.mjs', ['node', '--test']),
    )
    for path, label, command in checks:
        if not (ROOT / path).is_file():
            continue
        try:
            result = subprocess.run([*command, path], cwd=ROOT, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, errors='replace')
            if result.returncode == 0:
                continue
            print('\n'.join(result.stdout.splitlines()[-20:]))
        except OSError as exc:
            print(f'{label}: command could not run ({type(exc).__name__})')
        failures.append(label)
    if failures:
        print('contract checks failed: ' + ' '.join(failures))
    return int(bool(failures))


if __name__ == '__main__':
    raise SystemExit(main())
