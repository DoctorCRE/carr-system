#!/usr/bin/env python3
"""Local-only, read-only production ledger and reconstruction proofs.

Release preflight may request --schema for equivalence and grants before any apply.
No production DDL, ledger bootstrap, data dump, or permission changes occur.
Hosted CI exercises migration-production-proof-selftest.py instead.
"""
# ci: runs-outside-ci — live production reads are local-only gates
# doctrine: scripted-release-pipeline
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
import migrate
from schema_snapshot_grants import role_acl_facts_sql
from lib.disposable_pg_fixture import DisposablePostgres, postgres_fixture_group
from lib.secret_redaction import redact_text, sensitive_env_values


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def ledger_proof(repository, applied):
    migrations = [(name, '', digest) for name, digest in sorted(repository.items())]
    try:
        if not applied:
            raise ValueError('production migration ledger is empty')
        migrate.validate_applied_ledger(migrations, applied)
    except ValueError as exc:
        return dict(ok=False, error=str(exc))
    return dict(ok=True, applied=len(applied),
                pending=[name for name, sql, digest in migrate.pending_migrations(migrations, applied)])


comparison = load('production_proof_comparison', ROOT / 'ops/migration-shadow.py')
schema_difference = comparison.schema_difference


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
    command = [bins / 'pg_dump', '--schema-only']
    if not include_acl:
        command += ['--no-owner', '--no-acl']
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
    facts = conn.execute(role_acl_facts_sql(all_roles=True)).fetchall()
    privileges = [(kind, identity, '', role, privilege, grantable)
                  for role, kind, identity, privilege, grantable in facts
                  if kind not in {'database', 'large_object'}]
    owners = conn.execute("""with owned(owner,kind,identity,namespace) as (
        select c.relowner,case c.relkind when 'S' then 'sequence' else 'table' end,
          n.nspname||'.'||c.relname,n.nspname
          from pg_class c join pg_namespace n on n.oid=c.relnamespace
        union all select nspowner,'schema',nspname,nspname from pg_namespace
        union all select p.proowner,'function',
          n.nspname||'.'||p.proname||'('||oidvectortypes(p.proargtypes)||')',n.nspname
          from pg_proc p join pg_namespace n on n.oid=p.pronamespace
        union all select t.typowner,'type',n.nspname||'.'||t.typname,n.nspname
          from pg_type t join pg_namespace n on n.oid=t.typnamespace
        union all select lanowner,'language',lanname,'' from pg_language
        union all select fdwowner,'foreign_data_wrapper',fdwname,'' from pg_foreign_data_wrapper
        union all select srvowner,'foreign_server',srvname,'' from pg_foreign_server
        union all select a.defaclrole,'default_acl',
          coalesce(n.nspname,'*')||':'||a.defaclobjtype::text||':<owner>',coalesce(n.nspname,'')
          from pg_default_acl a left join pg_namespace n on n.oid=a.defaclnamespace
        ) select r.rolname,o.kind,o.identity from owned o join pg_roles r on r.oid=o.owner
          where o.namespace not in ('pg_catalog','information_schema')
            and o.namespace !~ '^pg_(toast|temp)' order by 1,2,3""").fetchall()
    privileges += [('owner', f'{kind}:{identity}', '', '<owner>', 'owner', False)
                   for role, kind, identity in owners]
    attributes = conn.execute("""select rolname, rolsuper, rolinherit, rolcreaterole,
        rolcreatedb, rolcanlogin, rolreplication, rolconnlimit, rolbypassrls
        from pg_roles order by rolname""").fetchall()
    memberships = conn.execute("""select r.rolname, m.rolname,
        a.admin_option, a.inherit_option, a.set_option
        from pg_auth_members a join pg_roles r on r.oid=a.roleid
        join pg_roles m on m.oid=a.member order by 1,2,3,4,5""").fetchall()
    privileges += [('ROLE', role, '', member, f'MEMBER inherit={inherit} set={set_option}', admin)
                   for role, member, admin, inherit, set_option in memberships]
    return dict(roles=['PUBLIC'] + roles, privileges=privileges,
                owner_roles=sorted({role for role, kind, identity in owners}),
                memberships=memberships, role_attributes=attributes)


def privilege_difference(production, snapshot):
    left_roles, right_roles = set(production['roles']), set(snapshot['roles'])
    common = left_roles & right_roles
    def comparable(row):
        return ((row[3] in common or row[3] == '<owner>')
                and (row[0] != 'ROLE' or row[1] in common)
                and (row[0] != 'default_acl' or row[1].rsplit(':', 1)[-1] in common | {'<owner>'}))
    left = {tuple(row) for row in production['privileges'] if comparable(row)}
    right = {tuple(row) for row in snapshot['privileges'] if comparable(row)}
    left_attributes = {tuple(row) for row in production.get('role_attributes', []) if row[0] in common}
    right_attributes = {tuple(row) for row in snapshot.get('role_attributes', []) if row[0] in common}
    owner_roles = set(production.get('owner_roles', [])) | set(snapshot.get('owner_roles', []))
    left_only_attributes, right_only_attributes = left_attributes - right_attributes, right_attributes - left_attributes
    return dict(ok=left == right and
                not any(row[0] not in owner_roles for row in left_only_attributes | right_only_attributes),
                common_roles=sorted(common),
                production_only_attributes=sorted(row for row in left_only_attributes if row[0] not in owner_roles),
                snapshot_only_attributes=sorted(row for row in right_only_attributes if row[0] not in owner_roles),
                informational_owner_attributes=dict(
                    production_only=sorted(row for row in left_only_attributes if row[0] in owner_roles),
                    snapshot_only=sorted(row for row in right_only_attributes if row[0] in owner_roles)),
                production_only_roles=sorted(left_roles - right_roles),
                snapshot_only_roles=sorted(right_roles - left_roles),
                production_only=[list(row) for row in sorted(left - right)],
                snapshot_only=[list(row) for row in sorted(right - left)])


def build_schema(schema_path, bins, repo=ROOT, production_ledger=None, grants=None):
    import psycopg
    from psycopg.conninfo import make_conninfo
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
            for row in (grants.get('role_attributes', []) if grants else ()):
                name, superuser, inherit, createrole, createdb, login, replication, limit, bypassrls = row
                if name == 'carr_ci' or name.startswith('pg_'):
                    continue
                options = ['SUPERUSER' if superuser else 'NOSUPERUSER',
                           'INHERIT' if inherit else 'NOINHERIT',
                           'CREATEROLE' if createrole else 'NOCREATEROLE',
                           'CREATEDB' if createdb else 'NOCREATEDB',
                           'LOGIN' if login else 'NOLOGIN',
                           'REPLICATION' if replication else 'NOREPLICATION',
                           'BYPASSRLS' if bypassrls else 'NOBYPASSRLS']
                conn.execute(sql.SQL('alter role {} with {} connection limit {}').format(
                    sql.Identifier(name), sql.SQL(' '.join(options)), sql.Literal(limit)))
            for role, member, admin, inherit, set_option in (grants['memberships'] if grants else ()):
                conn.execute(sql.SQL('grant {} to {} with admin {}, inherit {}, set {}').format(
                    sql.Identifier(role), sql.Identifier(member), sql.Literal(admin),
                    sql.Literal(inherit), sql.Literal(set_option)))
        checked([bins / 'psql', '-X', '-d', dsn, '-v', 'ON_ERROR_STOP=1', '-q', '-f', schema_path],
                dict(env, PGOPTIONS='--client-min-messages=warning'), 'restore schema into disposable database')
        if production_ledger is not None:
            with psycopg.connect(dsn) as conn:
                # Only this disposable ledger import lacks the SCAC config rows.
                # Pending migrations run with normal triggers in a new session.
                conn.execute("set local session_replication_role = replica")
                # A schema-only copy has no policy singleton. Use the same
                # shadow default as schema-snapshot.sh so the existing empty
                # projection bootstrap can run. Pending migrations execute in
                # a new session with every trigger active; never replace an
                # existing policy or seed rule, epoch, or registry history.
                if conn.execute("select to_regclass('ops.rule_delivery_policy')").fetchone()[0]:
                    conn.execute("""insert into ops.rule_delivery_policy(singleton,mode,changed_by,reason)
                        values(true,'shadow','production-schema-proof',
                          'Fresh schema-only reconstruction; no rule projection or cutover evidence.')
                        on conflict(singleton) do nothing""")
                with conn.cursor() as cursor:
                    cursor.executemany('insert into schema_migrations(filename, sha256) values (%s,%s)',
                                       sorted(production_ledger.items()))
            checked([sys.executable, repo / 'tools/migrate.py', '--apply', '--yes'],
                    dict(env, DATABASE_URL=make_conninfo(
                        dsn, user=(grants or {}).get('migration_role', 'carr_ci'))),
                    'apply pending migrations to disposable production schema')
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
        grants['migration_role'] = conn.execute('select session_user').fetchone()[0]
        conn.rollback()
    return applied, production, grants


def live_proof(ledger_only=True):
    import psycopg
    migrations = migrate.load_migrations()
    validate_snapshot = runpy.run_path(str(ROOT / 'ops/migration-safety-gate.py'))['ledger_problems']
    problems = validate_snapshot({name: sql for name, sql, digest in migrations},
                                (ROOT / 'db/schema.sql').read_text())
    if problems:
        raise RuntimeError('; '.join(problems))
    repository = {name: digest for name, sql, digest in migrations}
    bins = comparison.find_pg18(None) if not ledger_only else None
    if not ledger_only and bins is None:
        raise RuntimeError('PostgreSQL 18 binaries required for schema equivalence')
    applied, production, grants = read_source(production_dsn(), bins, include_acl=True)
    result = dict(ledger=ledger_proof(repository, applied))
    if ledger_only or not result['ledger']['ok']:
        return result
    result['equivalence'] = dict(ok=False,
        snapshot_sha256=hashlib.sha256((ROOT / 'db/schema.sql').read_bytes()).hexdigest(),
        scope='all pg_dump schema objects; internal namespace ACLs, database ACLs and large objects excluded; role passwords and grantor identities excluded',
        normalization='same runtime roles and migration principal; public snapshot identity-prose projection; owner and ACL preserving reconstruction; pg_dump --no-owner --no-acl for structural comparison; version banners, restrict nonces, separator comments and object order only; explicit non-owner ACLs compared by common role without grantor identity; owners normalized to <owner>; implicit ACL defaults excluded, including PUBLIC defaults; owner role attributes informational')
    result['grants'] = dict(ok=None, status='not_run', stage='production plus pending',
                            reason='disposable reconstruction did not complete')
    try:
        reference = build_schema(ROOT / 'db/schema.sql', bins, grants=grants)
        with tempfile.TemporaryDirectory(prefix='production-schema-proof-') as directory:
            source = Path(directory) / 'production.sql'
            source.write_text(production)
            built = build_schema(source, bins, production_ledger=applied, grants=grants)
        from pii_guard import load_corpus, sanitize_snapshot
        corpus = load_corpus(ROOT / 'ops/config/public-source-identities.v1.json')
        difference = schema_difference(sanitize_snapshot(built['dump'], corpus),
                                       sanitize_snapshot(reference['dump'], corpus))
        result['grants'] = dict(privilege_difference(built['grants'], reference['grants']), stage='production plus pending', status='complete')
        result['equivalence'].update(ok=built['ledger'] == reference['ledger'] == repository and not difference,
            ledger_matches=built['ledger'] == reference['ledger'] == repository, schema_diff=difference)
    except RuntimeError as exc:
        result['equivalence'].update(error=str(exc))
    except psycopg.Error as exc:
        result['equivalence'].update(error=f'disposable reconstruction failed ({exc.sqlstate}): {exc.diag.message_primary}')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--schema', action='store_true', help='also compare schema, grants and role attributes using disposable PostgreSQL 18')
    parser.add_argument('--report', type=Path, default=ROOT / 'out/migration-production-proof/report.json')
    args = parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS', '').lower() == 'true' or os.environ.get('CARR_CI_PORTABLE_ONLY') == '1':
        print('LOCAL-ONLY: production proofs not run on hosted/portable runners')
        return 2
    try:
        result = live_proof(ledger_only=not args.schema)
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
        if result['grants']['status'] == 'not_run':
            print('grants: NOT RUN', f"stage={result['grants']['stage']}", result['grants']['reason'])
        else:
            print('grants:', 'PASS' if result['grants']['ok'] else 'FAIL',
                  f"stage={result['grants']['stage']} production_only={len(result['grants']['production_only'])} snapshot_only={len(result['grants']['snapshot_only'])} "
                  f"role_attribute_differences={len(result['grants']['production_only_attributes']) + len(result['grants']['snapshot_only_attributes'])}")
            informational = result['grants']['informational_owner_attributes']
            print('owner role attributes: INFO',
                  f"differences={len(informational['production_only']) + len(informational['snapshot_only'])}")
    if 'detail' in result:
        print(result['detail'])
    print('proof report:', args.report)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
