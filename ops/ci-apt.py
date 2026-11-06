#!/usr/bin/env python3
"""Acquire signed system packages and verify required executable capabilities."""
import argparse
import os
from pathlib import Path
import re
import sys
import subprocess

SETUP = Path('/usr/share/postgresql-common/pgdg/apt.postgresql.org.sh')
PACKAGES = ('postgresql-17', 'postgresql-18', 'postgresql-client-18',
            'postgresql-17-pgvector', 'postgresql-18-pgvector')


def install():
    if not SETUP.is_file():
        print('PostgreSQL: bootstrap signed distro postgresql-common', flush=True)
        subprocess.run(['sudo', 'apt-get', '-o', 'Acquire::Retries=0', 'update', '--error-on=any', '-qq'], check=True, timeout=120)
        subprocess.run(['sudo', 'apt-get', 'install', '-y', '-qq',
                        'postgresql-common', 'ca-certificates'], check=True, timeout=120)
    # Upstream's script carries the PGDG key and configures the actual distro.
    print('PostgreSQL: official packaged PGDG setup and signed index refresh', flush=True)
    subprocess.run(['sudo', str(SETUP), '-y'], check=True, timeout=120)
    print('PostgreSQL: acquire required signed packages', flush=True)
    subprocess.run(['sudo', 'apt-get', '-o', 'Acquire::Retries=0',
                    'install', '-y', '-qq', *PACKAGES], check=True, timeout=120)
    for version in (17, 18):
        for binary in ('initdb', 'pg_ctl', 'postgres', 'psql', 'pg_dump', 'pg_restore'):
            command = [f'/usr/lib/postgresql/{version}/bin/{binary}', '--version']
            result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=10)
            if not re.search(rf'\(PostgreSQL\) {version}(?:\.|\b)', result.stdout):
                raise RuntimeError(f'wrong PostgreSQL version: {command[0]}: {result.stdout}')
            print(result.stdout.strip(), flush=True)
        for artifact in (f'/usr/share/postgresql/{version}/extension/vector.control',
                         f'/usr/lib/postgresql/{version}/lib/vector.so'):
            if not Path(artifact).is_file():
                raise RuntimeError(f'missing pgvector artifact: {artifact}')
    with open(os.environ['GITHUB_PATH'], 'a') as output:
        output.write('/usr/lib/postgresql/17/bin\n')


def main(args):
    parser = argparse.ArgumentParser()
    parser.add_argument('--postgres', '--provision-postgres', action='store_true')
    parser.add_argument('--documents', action='store_true')
    from ci_unit_shards import COUNT
    parser.add_argument('--unit-shard', type=int, choices=range(1, COUNT + 1))
    args = parser.parse_args(args)
    if args.unit_shard:
        from ci_unit_shards import plan
        args.documents = 'fact-store-postgres.test.mjs' in plan()[args.unit_shard - 1]
    if args.postgres:
        install()
    if args.documents:
        try:
            for arguments, limit in ((['update', '--error-on=any', '-qq'], 120),
                                     (['install', '-y', '--no-install-recommends',
                                       'libreoffice-calc', 'poppler-utils'], 3600)):
                subprocess.run(['timeout', '--kill-after=5s', f'{limit}s',
                                'sudo', 'apt-get', '-o', 'Acquire::Retries=2',
                                '-o', 'Acquire::http::Timeout=15',
                                '-o', 'Acquire::https::Timeout=15', *arguments],
                               check=True, timeout=limit + 10)
            subprocess.run(['pdftotext', '-v'], check=True, timeout=10)
            subprocess.run(['libreoffice', '--version'], check=True, timeout=10)
        except (subprocess.SubprocessError, OSError) as error:
            print(f'::error::Document setup failed within bounded signed apt acquisition: {error}',
                  file=sys.stderr, flush=True)
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
