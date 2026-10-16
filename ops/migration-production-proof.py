#!/usr/bin/env python3
"""Local-only, read-only production ledger and reconstruction proofs.

Release preflight may call this command, or --ledger-only, before any apply.
No production DDL, ledger bootstrap, data dump, or permission changes occur.
Hosted CI exercises migration-production-proof-selftest.py instead.
"""
# ci: runs-outside-ci — live production reads are local-only gates
# doctrine: scripted-release-pipeline
from __future__ import annotations

import argparse
from collections import Counter
import difflib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
import migrate
from lib.disposable_pg_fixture import DisposablePostgres, postgres_fixture_group
from lib.secret_redaction import redact_text, sensitive_env_values


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def number(name):
    match = re.match(r'^(\d+)', name)
    if not match:
        raise ValueError('invalid ledger filename')
    return int(match[1])


def ledger_proof(repository, applied):
    highest = max((number(name) for name in applied), default=-1)
    pending = sorted(repository.keys() - applied.keys())
    missing = sorted(applied.keys() - repository.keys())
    changed = sorted(name for name in applied.keys() & repository.keys() if applied[name] != repository[name])
    out_of_order = [name for name in pending if number(name) <= highest]
    return dict(ok=bool(applied) and not (missing or changed or out_of_order),
                applied=len(applied), highest=highest, pending=pending, missing=missing,
                checksum_mismatch=changed, out_of_order=out_of_order)


def dump_objects(text):
    # Only pg_dump's envelope varies independently of the catalog. Preserve
    # owners, ACLs, comments, SQL text and function bodies byte for byte.
    literals = re.compile(r'''(?P<tag>\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$).*?(?P=tag)|E'(?:\\.|''|[^'])*'|'(?:''|[^'])*'|"(?:""|[^"])*"|--[^\n]*|/\*.*?\*/''', re.S)
    masked = literals.sub(lambda match: match[0] if match[0].startswith('--') else re.sub(r'[^\n]', ' ', match[0]), text)
    pieces, current, headers = [], [], 0
    for line, visible in zip(text.split('\n'), masked.split('\n')):
        if re.match(r'^\\(?:un)?restrict \S+$', visible) or re.match(r'^-- (?:Dumped (?:from|by) .*|PostgreSQL database dump(?: complete)?)$', visible):
            continue
        if visible == '--':
            continue
        if visible.startswith('-- Name: '):
            pieces.append('\n'.join(current).strip())
            current = []
            headers += 1
        current.append(line)
    pieces.append('\n'.join(current).strip())
    if not headers:
        raise ValueError('schema dump has no object headers')
    return Counter(piece for piece in pieces if piece)


def schema_difference(reconstructed, production):
    left, right = dump_objects(reconstructed), dump_objects(production)
    if left == right:
        return []
    return list(difflib.unified_diff(
        '\n'.join(sorted(left.elements())).split('\n'), '\n'.join(sorted(right.elements())).split('\n'),
        fromfile='snapshot reconstruction', tofile='production', lineterm=''))


def checked(command, env, label, timeout=1200):
    try:
        result = subprocess.run([str(item) for item in command], env=env,
                                capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError(f'{label} could not complete') from None
    if result.returncode:
        from psycopg.conninfo import conninfo_to_dict
        secrets = sensitive_env_values(env)
        for name in ('PGDATABASE', 'DATABASE_URL'):
            if env.get(name):
                secrets.append(env[name])
                if '=' in env[name] or '://' in env[name]:
                    secrets.extend(value for key, value in conninfo_to_dict(env[name]).items() if key == 'password')
        detail = redact_text(result.stderr.decode('utf-8', errors='replace'), known_secrets=secrets)
        raise RuntimeError(f'{label} failed (exit {result.returncode}): {detail[:1500]}')
    return result.stdout.decode('utf-8')


def production_dsn():
    # This is migrate-prod.sh's existing status credential route. Capture the
    # helper's output in memory; never invoke migrate.py's writeful dry run.
    script = '''set -eu
if [ -z "${NEON_API_KEY:-}" ] && [ -f "$HOME/.config/carr/db.env" ]; then
  . "$1/bin/routine-credential-env.sh"
  carr_require_sourceable_db_env migration-production-proof >/dev/null 2>&1
  set -a; . "$HOME/.config/carr/db.env"; set +a
fi
exec "$2" "$1/tools/migrate-prod-support.py" neon-dsn --project-id steep-field-48688294
'''
    return checked(['zsh', '-c', script, 'production-proof', ROOT, sys.executable],
                   os.environ, 'production status credential resolution', timeout=100).strip()


def schema_dump(dsn, bins, env, snapshot=None):
    from psycopg.pq import Conninfo
    command = [bins / 'pg_dump', '--schema-only']
    if snapshot:
        command += ['--snapshot', snapshot]
    dump_env = {key: value for key, value in env.items() if not key.startswith('PG')}
    for option in Conninfo.parse(dsn.encode()):
        if option.envvar and option.val:
            dump_env[option.envvar.decode()] = option.val.decode()
    dump_env['PGOPTIONS'] = '-c default_transaction_read_only=on -c statement_timeout=120000'
    return checked(command, dump_env, 'read-only schema dump')


def role_catalog(conn):
    roles = conn.execute("""select rolname, rolsuper, rolinherit, rolcreaterole,
        rolcreatedb, rolcanlogin, rolreplication, rolbypassrls, rolconnlimit, rolconfig
        from pg_roles where starts_with(rolname, 'carr_') order by rolname""").fetchall()
    memberships = conn.execute("""select r.rolname, m.rolname, g.rolname,
        a.admin_option, a.inherit_option, a.set_option
        from pg_auth_members a join pg_roles r on r.oid=a.roleid
        join pg_roles m on m.oid=a.member join pg_roles g on g.oid=a.grantor
        where starts_with(r.rolname, 'carr_') or starts_with(m.rolname, 'carr_')
        order by 1,2,3""").fetchall()
    return dict(roles=roles, memberships=memberships)


def reconstruct(snapshot_path, applied, bins, repo=ROOT):
    import psycopg
    local_pg = load('production_proof_local_pg', ROOT / 'ops/local-pg-ci.py')
    env = local_pg.scrub_cloud_environment(os.environ)
    env['PATH'] = str(bins) + os.pathsep + env.get('PATH', '')
    with postgres_fixture_group(), DisposablePostgres('carr-baseline-proof.', bins / 'pg_ctl', env) as fixture:
        data = fixture.root / 'data'
        fixture.run([bins / 'initdb', '-D', data, '-U', 'carr_ci', '--auth=trust',
                     '--encoding=UTF8', '--no-locale'], check=True, capture_output=True, timeout=120)
        fixture.run([bins / 'pg_ctl', '-D', data, '-l', fixture.root / 'postgres.log',
                     '-o', f"-h '' -k {fixture.root}", '-w', 'start'],
                    check=True, capture_output=True, timeout=120)
        socket = quote(str(fixture.root), safe='')
        bootstrap = f'postgres://carr_ci@/postgres?host={socket}'
        dsn = f'postgres://carr_ci@/carr_ci?host={socket}'
        checked([bins / 'createdb', '--maintenance-db', bootstrap, 'carr_ci'], env, 'create disposable database')
        checked([bins / 'psql', '-X', '-d', dsn, '-v', 'ON_ERROR_STOP=1', '-qc',
                 'create role neondb_owner;'], env, 'CI role preamble')
        checked([bins / 'psql', '-X', '-d', dsn, '-v', 'ON_ERROR_STOP=1', '-q', '-f', snapshot_path],
                dict(env, PGOPTIONS='--client-min-messages=warning'), 'restore committed snapshot')
        with psycopg.connect(dsn) as conn:
            restored = dict(conn.execute('select filename, sha256 from schema_migrations').fetchall())
        cutoff = ledger_proof(applied, restored)
        # A snapshot ahead of production cannot be rolled back. Report that
        # fact and compare its schema, without pretending to reconstruct the cutoff.
        snapshot_only = sorted(restored.keys() - applied.keys())
        if cutoff['ok'] and not snapshot_only:
            checked([sys.executable, repo / 'tools/migrate.py', '--apply', '--yes',
                     '--through', max(applied)], dict(env, DATABASE_URL=dsn),
                    'apply production ledger prefix to disposable database')
        built, schema, roles = read_source(dsn, bins)
        return dict(dump=schema, roles=roles,
                    ledger_matches=built == applied, snapshot_only=snapshot_only,
                    snapshot_checksum_mismatch=cutoff['checksum_mismatch'])


def read_source(dsn, bins=None):
    import psycopg
    from psycopg import IsolationLevel
    with psycopg.connect(dsn, options='-c default_transaction_read_only=on -c statement_timeout=120000') as conn:
        conn.read_only = True
        conn.isolation_level = IsolationLevel.REPEATABLE_READ
        if conn.execute("select current_setting('transaction_read_only')").fetchone()[0] != 'on':
            raise RuntimeError('production read-only transaction not established')
        applied = dict(conn.execute('select filename, sha256 from schema_migrations').fetchall())
        if bins is None:
            conn.rollback()
            return applied, None, None
        exported = conn.execute('select pg_export_snapshot()').fetchone()[0]
        production = schema_dump(dsn, bins, os.environ, exported)
        roles = role_catalog(conn)
        conn.rollback()
    return applied, production, roles


def live_proof(ledger_only=False):
    repository = {name: digest for name, sql, digest in migrate.load_migrations()}
    shadow = load('production_proof_shadow', ROOT / 'ops/migration-shadow.py')
    bins = shadow.find_pg18(None) if not ledger_only else None
    if not ledger_only and bins is None:
        raise RuntimeError('PostgreSQL 18 binaries required for schema equivalence')
    applied, production, roles = read_source(production_dsn(), bins)
    result = dict(ledger=ledger_proof(repository, applied))
    if ledger_only or not result['ledger']['ok']:
        return result
    built = reconstruct(ROOT / 'db/schema.sql', applied, bins)
    difference = schema_difference(built['dump'], production)
    result['equivalence'] = dict(
        ok=built['ledger_matches'] and not difference and built['roles'] == roles,
        ledger_matches=built['ledger_matches'], snapshot_only=built['snapshot_only'],
        snapshot_checksum_mismatch=built['snapshot_checksum_mismatch'],
        schema_diff=difference, reconstructed_roles=built['roles'], production_roles=roles,
        roles_match=built['roles'] == roles,
        snapshot_sha256=hashlib.sha256((ROOT / 'db/schema.sql').read_bytes()).hexdigest(),
        normalization='pg_dump version banners, random restrict nonce, object ordering only; owners and ACLs retained')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger-only', action='store_true')
    parser.add_argument('--report', type=Path, default=ROOT / 'out/migration-production-proof/report.json')
    args = parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS', '').lower() == 'true' or os.environ.get('CARR_CI_PORTABLE_ONLY') == '1':
        print('LOCAL-ONLY: production proofs not run on hosted/portable runners')
        return 2
    try:
        result = live_proof(args.ledger_only)
    except Exception as exc:
        # Never print a database exception or CalledProcessError containing a DSN.
        result = dict(ok=False, error=type(exc).__name__, detail=str(exc) if type(exc) is RuntimeError else 'proof could not complete; no production writes attempted')
    result['ok'] = all(item['ok'] for key, item in result.items() if key in {'ledger', 'equivalence'}) if 'ledger' in result else False
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + '\n')
    if 'ledger' in result:
        print('ledger:', 'PASS' if result['ledger']['ok'] else 'FAIL', json.dumps(result['ledger']))
    if 'equivalence' in result:
        item = result['equivalence']
        print('equivalence:', 'PASS' if item['ok'] else 'FAIL',
              f"ledger_matches={item['ledger_matches']} roles_match={item['roles_match']} schema_diff_lines={len(item['schema_diff'])}")
        print('snapshot-only migrations:', json.dumps(item['snapshot_only']))
        for line in item['schema_diff'][:8]:
            print(line[:1000])
    if 'detail' in result:
        print(result['detail'])
    print('proof report:', args.report)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
