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
import tempfile
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
    # comments, SQL text and function bodies byte for byte. Both inputs must
    # already be owner-free and ACL-free pg_dump output.
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
        fromfile='production plus pending', tofile='committed snapshot', lineterm=''))


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


def schema_dump(dsn, bins, env, snapshot=None, include_acl=False):
    from psycopg.pq import Conninfo
    command = [bins / 'pg_dump', '--schema-only', '--no-owner']
    if not include_acl:
        command.append('--no-acl')
    if snapshot:
        command += ['--snapshot', snapshot]
    dump_env = {key: value for key, value in env.items() if not key.startswith('PG')}
    for option in Conninfo.parse(dsn.encode()):
        if option.envvar and option.val:
            dump_env[option.envvar.decode()] = option.val.decode()
    dump_env['PGOPTIONS'] = '-c default_transaction_read_only=on -c statement_timeout=120000'
    return checked(command, dump_env, 'read-only schema dump')


def privilege_catalog(conn):
    roles = [row[0] for row in conn.execute('select rolname from pg_roles order by rolname')]
    privileges = conn.execute("""
        with objects(kind, identity, subobject, acl) as (
          select case when c.relkind='S' then 'SEQUENCE' else 'RELATION' end,
                 format('%I.%I', n.nspname, c.relname), '',
                 coalesce(c.relacl, acldefault(case when c.relkind='S' then 's'::"char" else 'r'::"char" end, c.relowner))
            from pg_class c join pg_namespace n on n.oid=c.relnamespace
           where c.relkind in ('r','p','v','m','f','S')
             and n.nspname not in ('pg_catalog','information_schema')
             and n.nspname not like 'pg_toast%'
          union all
          select 'COLUMN', format('%I.%I', n.nspname, c.relname), a.attname, a.attacl
            from pg_attribute a join pg_class c on c.oid=a.attrelid
            join pg_namespace n on n.oid=c.relnamespace
           where a.attnum>0 and not a.attisdropped and a.attacl is not null
          union all
          select 'SCHEMA', quote_ident(nspname), '', coalesce(nspacl, acldefault('n', nspowner))
            from pg_namespace where nspname not in ('pg_catalog','information_schema')
             and nspname not like 'pg_toast%' and nspname not like 'pg_temp%'
          union all
          select 'ROUTINE', format('%I.%I(%s)', n.nspname, p.proname, pg_get_function_identity_arguments(p.oid)), '',
                 coalesce(p.proacl, acldefault('f', p.proowner))
            from pg_proc p join pg_namespace n on n.oid=p.pronamespace
           where n.nspname not in ('pg_catalog','information_schema')
          union all
          select 'TYPE', format('%I.%I', n.nspname, t.typname), '', coalesce(t.typacl, acldefault('T', t.typowner))
            from pg_type t join pg_namespace n on n.oid=t.typnamespace
           where n.nspname not in ('pg_catalog','information_schema')
             and n.nspname not like 'pg_toast%'
          union all
          select 'LANGUAGE', quote_ident(lanname), '', coalesce(lanacl, acldefault('l', lanowner)) from pg_language
          union all
          select 'FOREIGN DATA WRAPPER', quote_ident(fdwname), '', coalesce(fdwacl, acldefault('F', fdwowner)) from pg_foreign_data_wrapper
          union all
          select 'FOREIGN SERVER', quote_ident(srvname), '', coalesce(srvacl, acldefault('S', srvowner)) from pg_foreign_server
          union all
          select 'DEFAULT FOR ' || r.rolname, quote_ident(coalesce(n.nspname, '*')), d.defaclobjtype::text, d.defaclacl
            from pg_default_acl d join pg_roles r on r.oid=d.defaclrole
            left join pg_namespace n on n.oid=d.defaclnamespace
        )
        select distinct kind, identity, subobject,
               case when a.grantee=0 then 'PUBLIC' else pg_get_userbyid(a.grantee) end,
               a.privilege_type, a.is_grantable
          from objects cross join lateral aclexplode(acl) a
         order by 1,2,3,4,5,6
    """).fetchall()
    memberships = conn.execute("""select r.rolname, m.rolname,
        a.admin_option, a.inherit_option, a.set_option
        from pg_auth_members a join pg_roles r on r.oid=a.roleid
        join pg_roles m on m.oid=a.member order by 1,2,3,4,5""").fetchall()
    privileges += [('ROLE', role, '', member, f'MEMBER inherit={inherit} set={set_option}', admin)
                   for role, member, admin, inherit, set_option in memberships]
    return dict(roles=['PUBLIC'] + roles, privileges=privileges, memberships=memberships)


def privilege_difference(production, snapshot):
    left_roles, right_roles = set(production['roles']), set(snapshot['roles'])
    common = left_roles & right_roles
    def comparable(row):
        return (row[3] in common
                and (row[0] != 'ROLE' or row[1] in common)
                and (not row[0].startswith('DEFAULT FOR ') or row[0][12:] in common))
    left = {tuple(row) for row in production['privileges'] if comparable(row)}
    right = {tuple(row) for row in snapshot['privileges'] if comparable(row)}
    return dict(ok=left == right, common_roles=sorted(common),
                production_only_roles=sorted(left_roles - right_roles),
                snapshot_only_roles=sorted(right_roles - left_roles),
                production_only=[list(row) for row in sorted(left - right)],
                snapshot_only=[list(row) for row in sorted(right - left)])


def build_schema(schema_path, bins, repo=ROOT, production_ledger=None, grants=None):
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
        from psycopg import sql
        with psycopg.connect(dsn) as conn:
            for name in sorted(set(grants['roles'] if grants else ()) | {'neondb_owner'}):
                if name not in {'PUBLIC', 'carr_ci'} and not name.startswith('pg_'):
                    conn.execute(sql.SQL('create role {}').format(sql.Identifier(name)))
            for role, member, admin, inherit, set_option in (grants['memberships'] if grants else ()):
                conn.execute(sql.SQL('grant {} to {} with admin {}, inherit {}, set {}').format(
                    sql.Identifier(role), sql.Identifier(member), sql.Literal(admin),
                    sql.Literal(inherit), sql.Literal(set_option)))
        checked([bins / 'psql', '-X', '-d', dsn, '-v', 'ON_ERROR_STOP=1', '-q', '-f', schema_path],
                dict(env, PGOPTIONS='--client-min-messages=warning'), 'restore schema into disposable database')
        if production_ledger is not None:
            with psycopg.connect(dsn) as conn:
                with conn.cursor() as cursor:
                    cursor.executemany('insert into schema_migrations(filename, sha256) values (%s,%s)',
                                       sorted(production_ledger.items()))
            checked([sys.executable, repo / 'tools/migrate.py', '--apply', '--yes'],
                    dict(env, DATABASE_URL=dsn), 'apply pending migrations to disposable production schema')
        ledger, schema, grants = read_source(dsn, bins)
        return dict(dump=schema, grants=grants, ledger=ledger)


def read_source(dsn, bins=None, include_acl=False):
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
        production = schema_dump(dsn, bins, os.environ, exported, include_acl=include_acl)
        grants = privilege_catalog(conn)
        conn.rollback()
    return applied, production, grants


def live_proof(ledger_only=False):
    import psycopg
    repository = {name: digest for name, sql, digest in migrate.load_migrations()}
    shadow = load('production_proof_shadow', ROOT / 'ops/migration-shadow.py')
    bins = shadow.find_pg18(None) if not ledger_only else None
    if not ledger_only and bins is None:
        raise RuntimeError('PostgreSQL 18 binaries required for schema equivalence')
    applied, production, grants = read_source(production_dsn(), bins, include_acl=True)
    result = dict(ledger=ledger_proof(repository, applied))
    if ledger_only or not result['ledger']['ok']:
        return result
    result['equivalence'] = dict(ok=False,
        snapshot_sha256=hashlib.sha256((ROOT / 'db/schema.sql').read_bytes()).hexdigest(),
        normalization='pg_dump --no-owner --no-acl for structural comparison; version banners, restrict nonces, separator comments and object order only; privileges compared separately by common role, without grantor identity')
    try:
        reference = build_schema(ROOT / 'db/schema.sql', bins)
        result['grants'] = dict(privilege_difference(grants, reference['grants']), stage='production before pending')
        with tempfile.TemporaryDirectory(prefix='production-schema-proof-') as directory:
            source = Path(directory) / 'production.sql'
            source.write_text(production)
            built = build_schema(source, bins, production_ledger=applied, grants=grants)
        difference = schema_difference(built['dump'], reference['dump'])
        result['grants'] = dict(privilege_difference(built['grants'], reference['grants']), stage='production plus pending')
        result['equivalence'].update(ok=built['ledger'] == reference['ledger'] == repository and not difference,
            ledger_matches=built['ledger'] == reference['ledger'] == repository, schema_diff=difference)
    except RuntimeError as exc:
        result['equivalence'].update(error=str(exc))
    except psycopg.Error as exc:
        result['equivalence'].update(error=f'disposable reconstruction failed ({exc.sqlstate}): {exc.diag.message_primary}')
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
    result['ok'] = all(item['ok'] for key, item in result.items() if key in {'ledger', 'equivalence', 'grants'}) if 'ledger' in result else False
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + '\n')
    if 'ledger' in result:
        print('ledger:', 'PASS' if result['ledger']['ok'] else 'FAIL', json.dumps(result['ledger']))
    if 'equivalence' in result:
        item = result['equivalence']
        print('equivalence:', 'PASS' if item['ok'] else 'FAIL',
              f"ledger_matches={item.get('ledger_matches', 'not run')} schema_diff_lines={len(item.get('schema_diff', []))}")
        if 'error' in item:
            print(item['error'])
        for line in item.get('schema_diff', [])[:8]:
            print(line[:1000])
    if 'grants' in result:
        print('grants:', 'PASS' if result['grants']['ok'] else 'FAIL',
              f"production_only={len(result['grants']['production_only'])} snapshot_only={len(result['grants']['snapshot_only'])}")
    if 'detail' in result:
        print(result['detail'])
    print('proof report:', args.report)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
