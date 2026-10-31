#!/usr/bin/env python3
"""Reuse runner packages and package archives before contacting apt mirrors."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument('--postgres', action='store_true')
parser.add_argument('--documents', action='store_true')
args = parser.parse_args()
archives = Path(os.environ['CARR_CI_APT_ARCHIVES'])
archives.mkdir(parents=True, exist_ok=True)
options = ['-o', f'Dir::Cache::archives={archives}', '-o', 'APT::Keep-Downloaded-Packages=true']
postgres_ready = all((Path(f'/usr/lib/postgresql/{version}/bin/initdb').is_file()
                      and Path(f'/usr/share/postgresql/{version}/extension/vector.control').is_file())
                     for version in (17, 18))
if args.postgres and postgres_ready:
    with open(os.environ['GITHUB_PATH'], 'a') as output:
        output.write('/usr/lib/postgresql/17/bin\n')
if args.postgres and not postgres_ready:
    subprocess.run(['bash', 'ops/ci-postgres.sh'], check=True, timeout=240)
if args.documents and (not shutil.which('pdftotext') or not shutil.which('libreoffice')):
    subprocess.run(['sudo', 'apt-get', *options, 'update', '-qq'], check=True, timeout=120)
    subprocess.run(['sudo', 'apt-get', *options, 'install', '-y', '--no-install-recommends',
                    'libreoffice-calc', 'poppler-utils'], check=True, timeout=180)
