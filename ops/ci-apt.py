#!/usr/bin/env python3
"""Validate CI image tools; retain acquisition for pinned external consumers."""
import argparse
import os
from pathlib import Path
import re
import sys
import shutil
import subprocess

SETUP = Path('/usr/share/postgresql-common/pgdg/apt.postgresql.org.sh')
PACKAGES = ('postgresql-17', 'postgresql-18', 'postgresql-client-18',
            'postgresql-17-pgvector', 'postgresql-18-pgvector')


def install():
    if not SETUP.is_file():
        print('PostgreSQL: bootstrap signed distro postgresql-common', flush=True)
        subprocess.run(['sudo', 'apt-get', 'update', '-qq'], check=True)
        subprocess.run(['sudo', 'apt-get', 'install', '-y', '-qq',
                        'postgresql-common', 'ca-certificates'], check=True)
    # Upstream's script carries the PGDG key and configures the actual distro.
    print('PostgreSQL: official packaged PGDG setup and signed index refresh', flush=True)
    subprocess.run(['sudo', str(SETUP), '-y'], check=True)
    print('PostgreSQL: acquire required signed packages', flush=True)
    subprocess.run(['sudo', 'apt-get', '-o',
                    f'Dir::Cache::archives={os.environ.get("CARR_CI_APT_ARCHIVES", "/var/cache/apt/archives")}',
                    '-o', 'APT::Keep-Downloaded-Packages=true',
                    'install', '-y', '-qq', *PACKAGES], check=True)
    for version in (17, 18):
        for binary in ('initdb', 'postgres', 'psql'):
            command = [f'/usr/lib/postgresql/{version}/bin/{binary}', '--version']
            result = subprocess.run(command, check=True, capture_output=True, text=True)
            if not re.search(rf'\(PostgreSQL\) {version}(?:\.|\b)', result.stdout):
                raise RuntimeError(f'wrong PostgreSQL version: {command[0]}: {result.stdout}')
            print(result.stdout.strip(), flush=True)
        for artifact in (f'/usr/share/postgresql/{version}/extension/vector.control',
                         f'/usr/lib/postgresql/{version}/lib/vector.so'):
            if not Path(artifact).is_file():
                raise RuntimeError(f'missing pgvector artifact: {artifact}')
    with open(os.environ['GITHUB_PATH'], 'a') as output:
        output.write('/usr/lib/postgresql/17/bin\n')


def provision(args):
    if args == ['--postgres-attempt']:
        install()
        return 0
    if args:
        raise ValueError('expected no arguments or --postgres-attempt')
    for attempt in (1, 2):
        # GNU timeout bounds the whole process group, including apt scripts.
        result = subprocess.run(['timeout', '--kill-after=5s', '110s',
                                 sys.executable, str(Path(__file__).resolve()), '--postgres-attempt'])
        if result.returncode == 0:
            return 0
        print(f'PostgreSQL install attempt {attempt} failed (exit {result.returncode})', file=sys.stderr)
    return 1


def qualify_image(postgres=True, documents=False):
    if os.geteuid() == 0:
        raise RuntimeError('CI image checks must run as an unprivileged user')
    if postgres:
        for version in (17, 18):
            for binary in ('initdb', 'pg_ctl', 'postgres', 'psql', 'createdb', 'pg_dump', 'pg_restore'):
                command = [f'/usr/lib/postgresql/{version}/bin/{binary}', '--version']
                result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=5)
                if not re.search(rf'\(PostgreSQL\) {version}(?:\.|\b)', result.stdout):
                    raise RuntimeError(f'wrong PostgreSQL version: {command[0]}: {result.stdout}')
                print(result.stdout.strip(), flush=True)
            for artifact in (f'/usr/share/postgresql/{version}/extension/vector.control',
                             f'/usr/lib/postgresql/{version}/lib/vector.so'):
                if not Path(artifact).is_file():
                    raise RuntimeError(f'missing pgvector artifact: {artifact}')
    if documents and (not shutil.which('pdftotext') or not shutil.which('libreoffice')):
        raise RuntimeError('CI image is missing document tools')
    if postgres:
        with open(os.environ['GITHUB_PATH'], 'a') as output:
            output.write('/usr/lib/postgresql/17/bin\n')
    return 0


def main(args):
    if os.environ.get('CARR_CI_POSTGRES_IMAGE') == 'both':
        if args == ['--provision-postgres']:
            return qualify_image()
        parser = argparse.ArgumentParser()
        parser.add_argument('--postgres', action='store_true')
        parser.add_argument('--documents', action='store_true')
        selected = parser.parse_args(args)
        return qualify_image(selected.postgres, selected.documents)
    if args in (['--provision-postgres'], ['--postgres-attempt']):
        return provision([] if args == ['--provision-postgres'] else args)
    parser = argparse.ArgumentParser()
    parser.add_argument('--postgres', action='store_true')
    parser.add_argument('--documents', action='store_true')
    args = parser.parse_args(args)
    archives = Path(os.environ['CARR_CI_APT_ARCHIVES'])
    archives.mkdir(parents=True, exist_ok=True)
    options = ['-o', f'Dir::Cache::archives={archives}', '-o', 'APT::Keep-Downloaded-Packages=true']
    postgres_ready = all((Path(f'/usr/lib/postgresql/{version}/bin/initdb').is_file()
                          and Path(f'/usr/share/postgresql/{version}/extension/vector.control').is_file())
                         for version in (17, 18))
    packages = []
    if args.postgres and not postgres_ready:
        packages += PACKAGES
    if args.documents and (not shutil.which('pdftotext') or not shutil.which('libreoffice')):
        packages += ['libreoffice-calc', 'poppler-utils']
    if packages:
        cached = subprocess.run(['sudo', 'apt-get', *options, '--no-download', 'install', '-y',
                                 '--no-install-recommends', *packages], check=False, timeout=120)
        if cached.returncode != 0:
            if args.postgres and not postgres_ready:
                subprocess.run(['bash', 'ops/ci-postgres.sh'], check=True, timeout=240)
            if args.documents and (not shutil.which('pdftotext') or not shutil.which('libreoffice')):
                subprocess.run(['sudo', 'apt-get', *options, 'update', '-qq'], check=True, timeout=120)
                subprocess.run(['sudo', 'apt-get', *options, 'install', '-y', '--no-install-recommends',
                                'libreoffice-calc', 'poppler-utils'], check=True, timeout=180)
    if args.postgres:
        with open(os.environ['GITHUB_PATH'], 'a') as output:
            output.write('/usr/lib/postgresql/17/bin\n')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
