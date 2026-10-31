"""Copy a stopped snapshot cluster; each proof owns all database and role state."""
from pathlib import Path
import shutil
import hashlib
import os
import subprocess
import sys


def snapshot_key(schema, baseline=False):
    root = Path(schema).parent.parent
    digest = hashlib.sha256()
    for path in [Path(schema), *sorted((root / 'migrations').glob('*.sql'))]:
        digest.update(str(path.relative_to(root)).encode() + b'\0')
        with path.open('rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(chunk)
    return digest.hexdigest() + (':baseline' if baseline else ':current')


def migrate_snapshot(schema, dsn):
    root = Path(schema).parent.parent
    result = subprocess.run([sys.executable, str(root / 'tools/migrate.py'), '--apply', '--yes'],
        cwd=root, env={**os.environ, 'DATABASE_URL': dsn}, capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise RuntimeError('snapshot migrations failed: ' + (result.stdout + result.stderr)[-4000:])


def start_snapshot(fixture, binaries, schema, source=None, *, template=False, migrate=False):
    data = fixture.root / 'data'
    if source:
        source = Path(source)
        if (source / 'postmaster.pid').exists():
            raise ValueError('snapshot template must be stopped before copying')
        shutil.copytree(source, data)
    else:
        fixture.run([binaries / 'initdb', '-D', data, '-U', 'carr_ci', '--auth=trust', '--no-locale'],
                    check=True, capture_output=True, text=True, timeout=120)
    fixture.register(data)
    fixture.run([binaries / 'pg_ctl', '-D', data, '-l', fixture.root / 'server.log',
                 '-o', f"-k {fixture.root} -h ''", '-w', 'start'],
                check=True, capture_output=True, text=True, timeout=120)
    if not source:
        connection = ['-h', fixture.root, '-U', 'carr_ci']
        fixture.run([binaries / 'createdb', *connection, 'carr_ci'],
                    check=True, capture_output=True, text=True, timeout=120)
        fixture.run([binaries / 'psql', *connection, '-d', 'carr_ci', '-X',
                     '-v', 'ON_ERROR_STOP=1', '-q', '-c', 'create role neondb_owner', '-f', schema],
                    check=True, capture_output=True, text=True, timeout=120)
    if migrate:
        from urllib.parse import quote
        migrate_snapshot(schema, f'postgresql://carr_ci@localhost/carr_ci?host={quote(str(fixture.root), safe="")}')
    if template:
        fixture.run([binaries / 'pg_ctl', '-D', data, '-m', 'fast', '-w', 'stop'],
                    check=True, capture_output=True, text=True, timeout=120)
