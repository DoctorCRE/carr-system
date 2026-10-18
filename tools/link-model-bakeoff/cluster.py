"""Owned temporary PostgreSQL 18 cluster. No caller-supplied DSN or TCP listener."""
import os
import secrets
import re
import subprocess
import tempfile
import threading
from pathlib import Path

import psycopg

CONNECTION_ENV_LOCK = threading.Lock()


class Cluster:
    def __init__(self, bin_dir):
        self.bin = Path(bin_dir).resolve()
        version = subprocess.check_output([self.bin / 'postgres', '--version'], text=True)
        if not re.search(r'PostgreSQL\) 18(?:\.|\s)',version):
            raise ValueError('PostgreSQL 18 required')
        self.root = Path(tempfile.mkdtemp(prefix='linkfork-', dir=os.environ.get('TMPDIR', tempfile.gettempdir()))).resolve()
        self.data, self.socket = self.root / 'data', self.root / 'socket'
        self.socket.mkdir(mode=0o700)
        self.port = 20000 + secrets.randbelow(40000)
        self.running = False
        self.version = version.strip()

    def command(self, name, *args):
        env = {k:v for k,v in os.environ.items() if not k.startswith('PG')}
        return subprocess.run([str(self.bin / name), *map(str,args)], check=True, capture_output=True, text=True,env=env)

    def __enter__(self):
        self.command('initdb','-D',self.data,'-U','bakeoff','--auth-local=trust','--auth-host=reject','--no-locale','--encoding=UTF8')
        with (self.data / 'postgresql.conf').open('a') as handle:
            handle.write(f"\nlisten_addresses = ''\nunix_socket_directories = '{self.socket}'\nport = {self.port}\nshared_buffers = '256MB'\nmax_connections = 32\nfsync = on\nsynchronous_commit = on\ntrack_io_timing = on\nlog_lock_waits = on\ndeadlock_timeout = '10ms'\n")
        try:
            self.command('pg_ctl','-D',self.data,'-l',self.root / 'server.log','-w','start')
            self.running = True
            with self.connect() as c:
                assert c.execute('SHOW listen_addresses').fetchone()[0] == ''
                assert 180000 <= c.info.server_version < 190000
        except BaseException:
            status = subprocess.run([str(self.bin / 'pg_ctl'),'-D',str(self.data),'status'],capture_output=True)
            self.running = status.returncode == 0
            self.__exit__()
            raise
        return self

    def connect(self, application_name='linkfork'):
        # libpq treats an empty service as a service name to look up. Every
        # harness connection serializes this short environment-default read.
        with CONNECTION_ENV_LOCK:
            defaults = {k:v for k,v in os.environ.items() if k.startswith('PG')}
            try:
                for key in defaults:
                    del os.environ[key]
                return psycopg.connect(host=str(self.socket),hostaddr='',port=self.port,user='bakeoff',dbname='postgres',
                                       connect_timeout=5,application_name=application_name,
                                       options='-c statement_timeout=60000 -c lock_timeout=5000')
            finally:
                os.environ.update(defaults)

    def __exit__(self, *_):
        if self.running:
            self.command('pg_ctl','-D',self.data,'-m','fast','-w','stop')
            self.running = False
        status = subprocess.run([str(self.bin / 'pg_ctl'),'-D',str(self.data),'status'],capture_output=True)
        assert status.returncode == 3, 'Owned cluster did not stop'
        destination = self.root.parent / '_to_delete'
        destination.mkdir(exist_ok=True)
        self.root.rename(destination / self.root.name)
        self.root = destination / self.root.name
