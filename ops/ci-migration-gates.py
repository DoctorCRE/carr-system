#!/usr/bin/env python3
# doctrine: engineering-workflow-sop
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
ROLLBACK_ONLY_GATES = {'siep12-policy-epoch-local-pg-gate.py', 'siep18-reference-monitor-local-pg-gate.py'}


def postgres_binaries():
    spec = importlib.util.spec_from_file_location('ci_local_pg', ROOT / 'ops/local-pg-ci.py')
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.find_postgres_binaries().initdb.parent


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def quarantine(root):
    target = root.parent / '_to_delete'
    target.mkdir(exist_ok=True)
    shutil.move(str(root), str(target / root.name))


def checked(command, env):
    try:
        result = subprocess.run([str(c) for c in command], env=env, capture_output=True,
                                stdin=subprocess.DEVNULL, timeout=120)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f'{Path(str(command[0])).name} timed out') from None
    if result.returncode:
        raise RuntimeError(f'{Path(str(command[0])).name} failed with exit {result.returncode}')
    return result.stdout


def snapshot(dsn, env):
    bins = postgres_binaries()
    root = Path(tempfile.mkdtemp(prefix='ci-db-gate-'))
    try:
        owner = urlsplit(dsn).username
        if not owner:
            raise ValueError('disposable source must name its owner')
        roles = checked([bins / 'pg_dumpall', '-d', dsn, '--roles-only', '--no-role-passwords'], env).decode()
        quoted = '"' + owner.replace('"', '""') + '"'
        # initdb creates the original owner, so GRANTED BY retains its authority.
        creates_owner = {f'CREATE ROLE {owner};', f'CREATE ROLE {quoted};'}
        roles = '\n'.join(line for line in roles.splitlines() if line not in creates_owner) + '\n'
        (root / 'roles.sql').write_text(roles)
        checked([bins / 'pg_dump', '-d', dsn, '-Fc', '-f', root / 'database.dump'], env)
        return bins, root, owner
    except BaseException:
        quarantine(root)
        raise


def run_gate(gate, dsn, log, env):
    started = time.monotonic()
    with log.open('wb') as output:
        result = subprocess.run([sys.executable, str(gate)],
                                env=dict(env, DATABASE_URL=dsn, CARR_CI_DATABASE_URL=dsn),
                                cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, timeout=1200)
    return gate, result.returncode, int(time.monotonic() - started), log


def run_isolated(gate, image, log, env):
    bins, root, owner = image
    port = free_port()
    bootstrap = f'postgres://{quote(owner, safe="")}@127.0.0.1:{port}/postgres'
    data = root / 'data'
    try:
        checked([bins / 'initdb', '-D', data, '-U', owner,
                 '--auth=trust', '--encoding=UTF8', '--no-locale'], env)
        checked([bins / 'pg_ctl', '-D', data, '-l', root / 'postgres.log',
                 '-o', f'-h 127.0.0.1 -p {port} -k {root}', '-w', 'start'], env)
        checked([bins / 'psql', '-d', bootstrap, '-v', 'ON_ERROR_STOP=1',
                 '-f', root / 'roles.sql'], env)
        checked([bins / 'createdb', '--maintenance-db', bootstrap, '-O', owner, 'carr_ci'], env)
        dsn = f'postgres://{quote(owner, safe="")}@127.0.0.1:{port}/carr_ci'
        checked([bins / 'pg_restore', '--exit-on-error', '-d', dsn, root / 'database.dump'], env)
        return run_gate(gate, dsn, log, env)
    finally:
        subprocess.run([str(bins / 'pg_ctl'), '-D', str(data), '-m', 'fast', '-w', 'stop'],
                                 env=env, capture_output=True, timeout=60)
        status = subprocess.run([str(bins / 'pg_ctl'), '-D', str(data), 'status'],
                                env=env, capture_output=True, timeout=30)
        if status.returncode != 3:
            raise RuntimeError('private gate cluster did not stop; retained for diagnosis')
        quarantine(root)


def run_gates(dsn, gates, logdir, *, rollback_only=ROLLBACK_ONLY_GATES):
    parsed = urlsplit(dsn)
    if parsed.scheme not in ('postgres', 'postgresql') or parsed.hostname not in ('localhost', '127.0.0.1'):
        raise ValueError('migration gates require a disposable loopback database')
    logdir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.pop('PGDATABASE', None)
    env.pop('PGSERVICE', None)
    env.pop('PGSERVICEFILE', None)
    results = []
    pending = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for gate in gates:
            log = logdir / f'db-gate-{gate.name}.log'
            if gate.name in rollback_only:
                image = snapshot(dsn, env)
                pending.append(pool.submit(run_isolated, gate, image, log, env))
            else:
                results.append(run_gate(gate, dsn, log, env))
        results.extend(f.result() for f in pending)
    failures = []
    for gate, rc, seconds, log in sorted(results):
        lines = log.read_text(errors='replace').splitlines()
        for line in lines:
            if line.startswith('db-gate-proof:'):
                print(line)
        if rc:
            failures.append(gate.name)
            print('\n'.join(lines[-20:]), file=sys.stderr)
    print('db-gate-timing:' + ' '.join(f'{g.stem}={s}s' for g, _, s, _ in sorted(results, key=lambda r: -r[2])))
    if failures:
        print('db-gate-failed: ' + ' '.join(failures), file=sys.stderr)
    return int(bool(failures))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--logdir', type=Path, required=True)
    parser.add_argument('gates', nargs='+', type=Path)
    args = parser.parse_args()
    return run_gates(os.environ['DATABASE_URL'], args.gates, args.logdir)


if __name__ == '__main__':
    raise SystemExit(main())
