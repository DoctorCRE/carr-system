#!/usr/bin/env python3
# doctrine: engineering-workflow-sop
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import quote, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.disposable_pg_fixture import DisposablePostgres, postgres_fixture_group
ROLLBACK_ONLY_GATES = {'siep12-policy-epoch-local-pg-gate.py', 'siep18-reference-monitor-local-pg-gate.py'}

# WR-000258. PR 1708's hosted migration job spent 732 s in the database gates and
# 330 s in the proofs before them, almost all of it re-validating the sealed SCAC
# registry chain (116 successors, ~240k entries) inside single sessions.
#
# 1. The isolated gates used to start only after every serial gate had finished
#    (~230 s of the 732). They need exactly one thing from the serial gates:
#    the committed carr_authority_* login roles that
#    calendar-prebrief-projection-local-pg-gate.py creates (a gate that has to
#    run first says so with the marker below). Those gates run first, the
#    isolated gates snapshot the database, and every other serial gate runs
#    beside them. The isolated gates are a dump restored into private clusters,
#    so nothing the beside-gates do can reach them, and they pass on the cleaner
#    state (no other gate's committed leftovers) just as they did on the dirtier.
# 2. A gate listed in SHARDED_GATES is run as N sessions, each on its own copy
#    (CREATE DATABASE ... TEMPLATE) of the isolated database, with
#    CARR_GATE_SHARD=<i>/<N>. The gate declares its scenarios and each shard
#    reports the ones it ran; the union must be the whole declared set.
BEFORE_ISOLATED_MARKER = '# ci: db-gate-before-isolated'
SHARDED_GATES = {'siep12-policy-epoch-local-pg-gate.py': 2}


def postgres_binaries():
    spec = importlib.util.spec_from_file_location('ci_local_pg', ROOT / 'ops/local-pg-ci.py')
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.find_postgres_binaries().initdb.parent


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
    fixture = DisposablePostgres('ci-db-gate-', bins / 'pg_ctl', env)
    root = fixture.root
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
        return bins, fixture, owner
    except BaseException:
        fixture.close()
        raise


def run_gate(gate, dsn, log, env):
    started = time.monotonic()
    with log.open('wb') as output:
        result = subprocess.run([sys.executable, str(gate)],
                                env=dict(env, DATABASE_URL=dsn, CARR_CI_DATABASE_URL=dsn),
                                cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, timeout=1200)
    return gate, result.returncode, int(time.monotonic() - started), log


SHARD_SECONDS: dict[str, list[int]] = {}


def gate_shards(gate):
    name = Path(gate).name
    if name not in SHARDED_GATES:
        return 1
    return max(1, int(os.environ.get('CARR_GATE_SHARDS') or SHARDED_GATES[name]))


SCENARIO_LINE = re.compile(r'(?:^|NOTICE:\s+)(gate-scenarios-declared|gate-scenario-ran): ?(.*)$')


def scenario_coverage(texts):
    """'' when the shards together ran exactly the scenarios the proof declared.

    A gate prints `gate-scenarios-declared: a b c` and, per scenario it runs,
    `gate-scenario-ran: a`; psql's `NOTICE:  ` prefix is accepted so a SQL proof
    speaks the same protocol with `raise notice`. Names are space separated.
    """
    declared, ran = None, []
    for text in texts:
        for line in text.splitlines():
            found = SCENARIO_LINE.search(line)
            if not found:
                continue
            if found.group(1) == 'gate-scenarios-declared':
                names = tuple(found.group(2).split())
                if declared not in (None, names):
                    return 'shards disagree on the declared scenarios'
                declared = names
            else:
                ran.append(found.group(2).strip())
    if not declared:
        return 'no shard declared its scenarios'
    missing = sorted(set(declared) - set(ran))
    unknown = sorted(set(ran) - set(declared))
    repeated = sorted({n for n in ran if ran.count(n) > 1})
    problems = [f'{label}: {", ".join(names)}' for label, names in
                (('not run', missing), ('not declared', unknown), ('run more than once', repeated)) if names]
    return '; '.join(problems)


def run_sharded(gate, shards, bins, owner, connection, env, log):
    """Run one gate as N sessions, each on its own copy of the isolated database."""
    started = time.monotonic()
    bootstrap = connection.format(database='postgres')
    names = [f'carr_ci_shard{k}' for k in range(shards)]
    with ThreadPoolExecutor(max_workers=shards) as pool:
        # FILE_COPY (PostgreSQL 15+) copies the template's files instead of
        # writing every page to WAL: half the time here, measured.
        list(pool.map(lambda name: checked([bins / 'createdb', '--maintenance-db', bootstrap, '-O', owner,
                                            '-T', 'carr_ci', '-S', 'file_copy', name], env), names))
    dsns = [connection.format(database=name) for name in names]
    shard_logs = [log.with_name(f'{log.stem}.shard{k}{log.suffix}') for k in range(shards)]
    with ThreadPoolExecutor(max_workers=shards) as pool:
        futures = [pool.submit(run_gate, gate, dsns[k], shard_logs[k], dict(env, CARR_GATE_SHARD=f'{k}/{shards}'))
                   for k in range(shards)]
        outcomes = [f.result() for f in futures]
    texts = [path.read_text(errors='replace') for path in shard_logs]
    rc = max(code for _, code, _, _ in outcomes)
    note = ''
    if not rc:
        note = scenario_coverage(texts)
        rc = int(bool(note))
    SHARD_SECONDS[Path(gate).name] = [seconds for _, _, seconds, _ in outcomes]
    with log.open('w') as combined:
        for k, text in enumerate(texts):
            combined.write(f'==== shard {k}/{shards} ====\n{text}\n')
        if note:
            combined.write(f'{Path(gate).name}: SHARD COVERAGE FAILURE — {note}\n')
    return gate, rc, int(time.monotonic() - started), log


def needed_by_isolated(gate):
    try:
        return any(line.startswith(BEFORE_ISOLATED_MARKER) for line in Path(gate).read_text(errors='replace').splitlines())
    except OSError:
        return False


def run_isolated(gate, image, log, env):
    bins, fixture, owner = image
    root = fixture.root
    connection = f'postgres://{quote(owner, safe="")}@/{{database}}?host={quote(str(root), safe="")}'
    bootstrap = connection.format(database='postgres')
    data = root / 'data'
    try:
        fixture.run([bins / 'initdb', '-D', data, '-U', owner,
                 '--auth=trust', '--encoding=UTF8', '--no-locale'], check=True, capture_output=True, timeout=120)
        fixture.run([bins / 'pg_ctl', '-D', data, '-l', root / 'postgres.log',
                 '-o', f"-h '' -k {root}", '-w', 'start'], check=True, capture_output=True, timeout=120)
        checked([bins / 'psql', '-d', bootstrap, '-v', 'ON_ERROR_STOP=1',
                 '-f', root / 'roles.sql'], env)
        checked([bins / 'createdb', '--maintenance-db', bootstrap, '-O', owner, 'carr_ci'], env)
        dsn = connection.format(database='carr_ci')
        checked([bins / 'pg_restore', '--exit-on-error', '-d', dsn, root / 'database.dump'], env)
        shards = gate_shards(gate)
        if shards > 1:
            return run_sharded(gate, shards, bins, owner, connection, env, log)
        return run_gate(gate, dsn, log, env)
    except subprocess.CalledProcessError as exc:
        with log.open('wb') as output:
            output.write(exc.stdout or b'')
            output.write(exc.stderr or b'')
            if (root / 'postgres.log').exists():
                output.write((root / 'postgres.log').read_bytes())
        print(log.read_text(errors='replace')[-4000:], file=sys.stderr)
        raise RuntimeError(f'isolated PostgreSQL startup failed; see {log}') from None
    finally:
        fixture.close()


def run_gates(dsn, gates, logdir, *, rollback_only=ROLLBACK_ONLY_GATES):
    parsed = urlsplit(dsn)
    if parsed.scheme not in ('postgres', 'postgresql') or parsed.hostname not in ('localhost', '127.0.0.1'):
        raise ValueError('migration gates require a disposable loopback database')
    logdir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.pop('PGDATABASE', None)
    env.pop('PGSERVICE', None)
    env.pop('PGSERVICEFILE', None)
    env.pop('CARR_GATE_SHARD', None)
    SHARD_SECONDS.clear()
    started = time.monotonic()
    results = []
    serial = []
    isolated = []
    for gate in gates:
        log = logdir / f'db-gate-{gate.name}.log'
        (isolated if gate.name in rollback_only else serial).append((gate, log))
    # Gates marked BEFORE_ISOLATED_MARKER run first, in glob order; the isolated
    # gates snapshot the database after them and then run beside every other
    # serial gate, which keeps its glob order.
    head, tail = serial, []
    if isolated:
        head = [item for item in serial if needed_by_isolated(item[0])]
        tail = [item for item in serial if item not in head]
    head_started = time.monotonic()
    for gate, log in head:
        results.append(run_gate(gate, dsn, log, env))
    head_seconds = int(time.monotonic() - head_started)
    tail_seconds = 0
    if isolated:
        with postgres_fixture_group(), ExitStack() as fixtures:
            images = []
            for gate, log in isolated:
                image = snapshot(dsn, env)
                fixtures.callback(image[1].close)
                images.append((gate, image, log))
            with ThreadPoolExecutor(max_workers=len(images)) as pool:
                pending = [pool.submit(run_isolated, gate, image, log, env)
                           for gate, image, log in images]
                tail_started = time.monotonic()
                for gate, log in tail:
                    results.append(run_gate(gate, dsn, log, env))
                tail_seconds = int(time.monotonic() - tail_started)
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
    wall = int(time.monotonic() - started)
    summed = sum(s for _, _, s, _ in results) + sum(sum(v) - max(v) for v in SHARD_SECONDS.values())
    shard_note = ''.join(f' {name[:-3]}-shards={"/".join(f"{s}s" for s in secs)}' for name, secs in sorted(SHARD_SECONDS.items()))
    print(f'db-gate-schedule: wall={wall}s one-after-another={summed}s saved={summed - wall}s '
          f'(WR-000258; PR 1708 hosted baseline wall=732s) before-isolated={head_seconds}s({len(head)}) '
          f'serial-beside-isolated={tail_seconds}s({len(tail)}){shard_note}')
    if failures:
        print('db-gate-failed: ' + ' '.join(failures), file=sys.stderr)
    return int(bool(failures))


def clone_dsn(dsn, database):
    parts = urlsplit(dsn)
    return urlunsplit(parts._replace(path='/' + database))


def run_sql_proofs(dsn, proofs, logdir, psql='psql'):
    """Run rollback-only SQL proofs concurrently, each on its own copy of the database.

    proofs is a list of (path, shards). A proof with shards N is run N times with
    `-v shards=N -v shard=K`; it declares and reports its scenarios (see
    scenario_coverage) and the shards must together run each exactly once. The first task uses the
    database itself (nothing else touches it meanwhile), every other task a
    CREATE DATABASE ... TEMPLATE copy that is dropped afterwards.
    """
    parsed = urlsplit(dsn)
    if parsed.scheme not in ('postgres', 'postgresql') or parsed.hostname not in ('localhost', '127.0.0.1'):
        raise ValueError('migration proofs require a disposable loopback database')
    logdir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    for name in ('PGDATABASE', 'PGSERVICE', 'PGSERVICEFILE'):
        env.pop(name, None)
    source = parsed.path.lstrip('/')
    maintenance = clone_dsn(dsn, 'postgres')
    tasks = [(Path(path), k, shards) for path, shards in proofs for k in range(shards)]
    clones = []

    def sql(statement):
        checked([psql, '-X', '-q', '-v', 'ON_ERROR_STOP=1', '-d', maintenance, '-c', statement], env)

    def run_task(task, target):
        path, k, shards = task
        log = logdir / (f'{path.stem}.shard{k}.log' if shards > 1 else f'{path.stem}.log')
        began = time.monotonic()
        command = [psql, '-X', '-v', 'ON_ERROR_STOP=1', '-d', target, '-f', str(path)]
        if shards > 1:
            command[1:1] = ['-v', f'shards={shards}', '-v', f'shard={k}']
        with log.open('wb') as output:
            result = subprocess.run(command, env=env, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, timeout=1200)
        return path, k, shards, result.returncode, int(time.monotonic() - began), log

    started = time.monotonic()
    setup_seconds = 0
    try:
        targets = [dsn]
        clones.extend(f'ci_proof_{os.getpid()}_{i}' for i in range(1, len(tasks)))
        targets.extend(clone_dsn(dsn, name) for name in clones)
        with ThreadPoolExecutor(max_workers=max(1, len(clones))) as pool:
            list(pool.map(lambda name: sql(f'create database "{name}" template "{source}" strategy file_copy'), clones))
        setup_seconds = int(time.monotonic() - started)
        with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
            outcomes = list(pool.map(run_task, tasks, targets))
    finally:
        for name in clones:
            try:
                sql(f'drop database if exists "{name}" with (force)')
            except RuntimeError as exc:
                print(f'could not drop {name}: {exc}', file=sys.stderr)
    failures = []
    by_proof = {}
    for path, k, shards, rc, seconds, log in outcomes:
        by_proof.setdefault(path, []).append((k, rc, seconds, log))
    timings = []
    for path, runs in by_proof.items():
        texts = [log.read_text(errors='replace') for _, _, _, log in sorted(runs, key=lambda r: r[0])]
        bad = [k for k, rc, _, _ in runs if rc]
        if bad:
            failures.append(f'{path.name} (shard {",".join(map(str, bad))})')
            print('\n'.join('\n'.join(t.splitlines()[-30:]) for t in texts), file=sys.stderr)
        elif len(runs) > 1:
            problem = scenario_coverage(texts)
            if problem:
                failures.append(f'{path.name}: {problem}')
        seconds = [s for _, _, s, _ in sorted(runs, key=lambda r: r[0])]
        timings.append(f'{path.stem}={max(seconds)}s' + (f'[{"/".join(f"{s}s" for s in seconds)}]' if len(seconds) > 1 else ''))
        if len(runs) > 1:
            (logdir / f'{path.stem}.log').write_text('\n'.join(texts))
    wall = int(time.monotonic() - started)
    summed = sum(s for *_, s, _ in outcomes)
    print(f'sql-proof-timing: {" ".join(timings)} clone-setup={setup_seconds}s wall={wall}s '
          f'one-after-another={summed}s saved={summed - wall}s '
          f'(WR-000258; PR 1708 hosted baseline for the whole Tour/definer proof step: 330s)')
    if failures:
        print('sql-proof-failed: ' + ' '.join(failures), file=sys.stderr)
    return int(bool(failures))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--logdir', type=Path, required=True)
    parser.add_argument('--sql-proof', action='append', default=[], metavar='FILE[:SHARDS]',
                        help='run a rollback-only SQL proof (split into SHARDS sessions) instead of Python gates')
    parser.add_argument('--psql', default='psql')
    parser.add_argument('gates', nargs='*', type=Path)
    args = parser.parse_args()
    if args.sql_proof:
        if args.gates:
            parser.error('--sql-proof and gate files are separate runs')
        proofs = []
        for spec in args.sql_proof:
            path, _, shards = spec.partition(':')
            proofs.append((path, int(shards or 1)))
        return run_sql_proofs(os.environ['DATABASE_URL'], proofs, args.logdir, args.psql)
    if not args.gates:
        parser.error('no gates given')
    return run_gates(os.environ['DATABASE_URL'], args.gates, args.logdir)


if __name__ == '__main__':
    raise SystemExit(main())
