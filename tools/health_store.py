"""Read-only health queries sharing one process-local credential resolution."""
import importlib.util
from pathlib import Path
import subprocess
import sys

_tap = None
_url = None
_error = None


def query(sql):
    global _tap, _url, _error
    if _error is not None:
        return subprocess.CompletedProcess([], 1, '', _error)
    try:
        if _tap is None:
            spec = importlib.util.spec_from_file_location(
                'health_db_tap', Path(__file__).with_name('db-tap.py'))
            _tap = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = _tap
            spec.loader.exec_module(_tap)
        if _url is None:
            try:
                _url = _tap.dsn()
            except SystemExit as exc:
                _error = str(exc)
                return subprocess.CompletedProcess([], 1, '', _error)
        import psycopg
        lines = []
        with psycopg.connect(_url, options='-c default_transaction_read_only=on '
                              '-c statement_timeout=120000', connect_timeout=15) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                while True:
                    if cur.description is not None:
                        lines.extend('|'.join('' if v is None else str(v) for v in row)
                                     for row in cur.fetchall())
                    if not cur.nextset():
                        break
        return subprocess.CompletedProcess([], 0, '\n'.join(lines), '')
    except Exception as exc:
        return subprocess.CompletedProcess([], 1, '',
                                           f'health store query failed ({type(exc).__name__}); details suppressed')
