"""Copy a stopped snapshot cluster; each proof owns all database and role state."""
from pathlib import Path
import shutil


def start_snapshot(fixture, binaries, schema, source=None, *, template=False):
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
    if template:
        fixture.run([binaries / 'pg_ctl', '-D', data, '-m', 'fast', '-w', 'stop'],
                    check=True, capture_output=True, text=True, timeout=120)
