#!/usr/bin/env python3
"""Reconcile the engine's bounded reader login through db-tap run mode.

Use --environment staging or production. Passwords stay in private CARR files.
The --rotate flag is the only way to reset an existing login's password.
"""
import argparse
import importlib.util
import json
import os
import pathlib
import re
import shutil
import sys
from urllib.parse import urlsplit

import psycopg
from psycopg import sql

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import staging_database_credential as credentials

ROLE = 'engine_business_reader'
SPEC = ROOT / 'tools/config/engine-business-reader.json'


class Refusal(RuntimeError):
    """A role or credential lies outside the authorized contract."""


def validate_spec(data):
    if data['role'] != ROLE or data['schema'] != 'public':
        raise Refusal('role spec exceeds the authorized role or schema')
    names = data['tables'] + data['views']
    if not names or len(names) != len(set(names)) or any(
        not re.fullmatch('[a-z_][a-z_0-9]*', name) for name in names
    ):
        raise Refusal('role spec contains duplicate or invalid relation names')
    if data['settings'] != {
        'default_transaction_read_only': 'on', 'timezone': 'UTC',
        'search_path': 'pg_catalog, public',
    }:
        raise Refusal('role settings differ from the reader contract')
    return data


def load_spec():
    return validate_spec(json.loads(SPEC.read_text()))


def password_change(*, exists, rotate, authenticated):
    if exists and not rotate and not authenticated:
        raise Refusal('existing role credential unavailable or invalid; explicit rotation required')
    return not exists or rotate


def grant_statements(data):
    return [f'GRANT SELECT ON TABLE "public"."{name}" TO "{ROLE}"'
            for name in data['tables'] + data['views']]


def connect(uri):
    return psycopg.connect(uri, connect_timeout=20,
                           options='-c statement_timeout=30000 -c lock_timeout=10000')


def authenticated(uri):
    try:
        with connect(uri) as conn:
            return conn.execute('SELECT current_user = %s', (ROLE,)).fetchone()[0]
    except psycopg.OperationalError:
        return False


def revocations(conn):
    """Only this role's explicit ACLs change; PUBLIC and incumbent ACLs stay intact."""
    return [row[0] for row in conn.execute("""
      WITH target AS (SELECT oid FROM pg_roles WHERE rolname = %s::text)
      SELECT DISTINCT format('REVOKE ALL ON %%s %%I.%%I FROM %%I',
        CASE WHEN c.relkind = 'S' THEN 'SEQUENCE' ELSE 'TABLE' END,
        n.nspname, c.relname, %s::text)
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace,
        aclexplode(c.relacl) a, target t WHERE a.grantee=t.oid
      UNION ALL
      SELECT DISTINCT format('REVOKE ALL (%%I) ON TABLE %%I.%%I FROM %%I',
        att.attname,n.nspname,c.relname,%s::text)
      FROM pg_attribute att JOIN pg_class c ON c.oid=att.attrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace,
        aclexplode(att.attacl) a, target t WHERE a.grantee=t.oid
      UNION ALL
      SELECT DISTINCT format('REVOKE ALL ON SCHEMA %%I FROM %%I',n.nspname,%s::text)
      FROM pg_namespace n, aclexplode(n.nspacl) a,target t WHERE a.grantee=t.oid
      UNION ALL
      SELECT DISTINCT format('REVOKE ALL ON ROUTINE %%I.%%I(%%s) FROM %%I',
        n.nspname,p.proname,pg_get_function_identity_arguments(p.oid),%s::text)
      FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace,
        aclexplode(p.proacl) a,target t WHERE a.grantee=t.oid
      UNION ALL
      SELECT DISTINCT format('REVOKE ALL ON DATABASE %%I FROM %%I',d.datname,%s::text)
      FROM pg_database d, aclexplode(d.datacl) a,target t WHERE a.grantee=t.oid
      UNION ALL
      SELECT DISTINCT format('REVOKE %%I FROM %%I',r.rolname,%s::text)
      FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.roleid,target t WHERE m.member=t.oid
      UNION ALL
      SELECT DISTINCT format('ALTER DEFAULT PRIVILEGES FOR ROLE %%I %%s REVOKE ALL ON %%s FROM %%I',
        r.rolname,CASE WHEN d.defaclnamespace=0 THEN '' ELSE format('IN SCHEMA %%I',n.nspname) END,
        CASE d.defaclobjtype WHEN 'r' THEN 'TABLES' WHEN 'S' THEN 'SEQUENCES'
          WHEN 'f' THEN 'FUNCTIONS' WHEN 'T' THEN 'TYPES' WHEN 'n' THEN 'SCHEMAS' END,%s::text)
      FROM pg_default_acl d JOIN pg_roles r ON r.oid=d.defaclrole
        LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace,
        aclexplode(d.defaclacl) a,target t WHERE a.grantee=t.oid
    """, (ROLE,) * 8).fetchall()]


def reconcile(conn, data, password=None):
    conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (ROLE,))
    exists = conn.execute('SELECT EXISTS(SELECT FROM pg_roles WHERE rolname=%s)', (ROLE,)).fetchone()[0]
    if exists:
        if conn.execute("""SELECT EXISTS(SELECT FROM pg_shdepend d JOIN pg_roles r
            ON r.oid=d.refobjid WHERE r.rolname=%s AND d.deptype='o')""", (ROLE,)).fetchone()[0]:
            raise Refusal('reader owns objects; ownership changes exceed this tool')
    for name in data['tables'] + data['views']:
        kind = conn.execute('SELECT relkind FROM pg_class WHERE oid=to_regclass(%s)',
                            ('public.' + name,)).fetchone()
        expected = ('r', 'p') if name in data['tables'] else ('v',)
        if not kind or kind[0] not in expected:
            raise Refusal('allowlisted relation missing or wrong kind: ' + name)
    if not exists:
        conn.execute(sql.SQL('CREATE ROLE {} LOGIN').format(sql.Identifier(ROLE)))
    conn.execute(sql.SQL('ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
                         'NOREPLICATION NOBYPASSRLS NOINHERIT').format(sql.Identifier(ROLE)))
    conn.execute(sql.SQL('ALTER ROLE {} RESET ALL').format(sql.Identifier(ROLE)))
    for key, value in data['settings'].items():
        conn.execute(sql.SQL('ALTER ROLE {} SET {} = {}').format(
            sql.Identifier(ROLE), sql.Identifier(key), sql.Literal(value)))
    if password is not None:
        conn.execute(sql.SQL('ALTER ROLE {} PASSWORD {}').format(
            sql.Identifier(ROLE), sql.Literal(password)))
    for statement in revocations(conn):
        conn.execute(statement)
    conn.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(
        sql.Identifier(conn.info.dbname), sql.Identifier(ROLE)))
    conn.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(ROLE)))
    for statement in grant_statements(data):
        conn.execute(statement)
    actual = set(conn.execute("""SELECT n.nspname,c.relname,a.privilege_type,a.is_grantable
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace,
        aclexplode(c.relacl) a JOIN pg_roles r ON r.oid=a.grantee WHERE r.rolname=%s""",
        (ROLE,)).fetchall())
    expected = {('public', name, 'SELECT', False) for name in data['tables'] + data['views']}
    if actual != expected:
        raise Refusal('direct relation grants differ from the role spec')
    if conn.execute("""SELECT EXISTS(SELECT FROM pg_type t,aclexplode(t.typacl) a
        JOIN pg_roles r ON r.oid=a.grantee WHERE r.rolname=%s)""", (ROLE,)).fetchone()[0]:
        raise Refusal('unsupported direct type grants; transaction rolled back')
    return not exists


def proof(uri, data):
    result = {}
    with connect(uri) as conn:
        result['role_flags_match'] = conn.execute("""SELECT rolcanlogin AND NOT
            (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls OR rolinherit)
            FROM pg_roles WHERE rolname=current_user""").fetchone()[0]
        result['no_memberships'] = conn.execute("""SELECT NOT EXISTS(SELECT FROM pg_auth_members
            WHERE member=(SELECT oid FROM pg_roles WHERE rolname=current_user))""").fetchone()[0]
        result['no_ops_or_auth_usage'] = conn.execute("""SELECT NOT (
            has_schema_privilege(current_user,'ops','USAGE') OR
            has_schema_privilege(current_user,'neon_auth','USAGE'))""").fetchone()[0]
        result['no_direct_routine_grants'] = conn.execute("""SELECT NOT EXISTS(
            SELECT FROM pg_proc p,aclexplode(p.proacl) a
            WHERE a.grantee=(SELECT oid FROM pg_roles WHERE rolname=current_user))""").fetchone()[0]
        result['no_default_grants'] = conn.execute("""SELECT NOT EXISTS(
            SELECT FROM pg_default_acl d,aclexplode(d.defaclacl) a
            WHERE a.grantee=(SELECT oid FROM pg_roles WHERE rolname=current_user))""").fetchone()[0]
        result['no_sequence_rights'] = conn.execute("""SELECT NOT EXISTS(
            SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE c.relkind='S' AND n.nspname NOT IN ('pg_catalog','information_schema')
            AND has_sequence_privilege(current_user,c.oid,'SELECT,USAGE,UPDATE'))""").fetchone()[0]
        result['TimeZone'] = conn.execute('SHOW TimeZone').fetchone()[0]
        result['read_only_default'] = conn.execute('SHOW default_transaction_read_only').fetchone()[0] == 'on'
        result['activity_whole_table_select'] = conn.execute(
            "SELECT has_table_privilege(current_user,'public.activity','SELECT')").fetchone()[0]
        for name in ['activity'] + data['views']:
            try:
                with conn.transaction():
                    row = conn.execute(sql.SQL('SELECT * FROM public.{} LIMIT 1').format(
                        sql.Identifier(name))).fetchone()
                    result[name] = {'read': True, 'has_rows': row is not None}
            except psycopg.Error as exc:
                result[name] = {'read': False, 'sqlstate': exc.sqlstate}
        conn.rollback()
        conn.execute('SET TRANSACTION READ WRITE')
        for label, statement in {
            'ops_read_refused': 'SELECT 1 FROM ops.client_portal LIMIT 1',
            'insert_refused': 'EXPLAIN INSERT INTO public.activity DEFAULT VALUES',
            'update_refused': 'EXPLAIN UPDATE public.activity SET id=id WHERE false',
            'delete_refused': 'EXPLAIN DELETE FROM public.activity WHERE false',
        }.items():
            try:
                with conn.transaction():
                    conn.execute(statement)
                result[label] = False
            except psycopg.errors.InsufficientPrivilege:
                result[label] = True
        conn.rollback()
    return result


def provision(owner, data, environment, rotate, root):
    parsed = urlsplit(owner)
    key = 'CARR_DB_' + environment.upper() + '_ENGINE_BUSINESS_READER_URL'
    final = root / (environment + '-engine-business-reader.env')
    paths = credentials.CredentialPaths(final, pathlib.Path(str(final) + '.pending'))
    if rotate:
        paths = credentials.CredentialPaths(pathlib.Path(str(final) + '.rotate'),
                                            pathlib.Path(str(final) + '.rotate.pending'))
    params = dict(key=key, role_name=ROLE, owner_uri=owner,
                  expected_endpoint=parsed.hostname, expected_port=parsed.port or 5432,
                  expected_database=parsed.path.lstrip('/'))
    with credentials.exclusive_lock(root / (environment + '-engine-business-reader.lock')):
        with connect(owner) as conn:
            conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (ROLE,))
            exists = conn.execute('SELECT EXISTS(SELECT FROM pg_roles WHERE rolname=%s)', (ROLE,)).fetchone()[0]
            if exists and credentials.file_state(paths) == 'absent' and not rotate:
                raise Refusal('existing role has no local secret; explicit rotation required')
            stored = credentials.prepare_pending(paths, **params)
            change = password_change(exists=exists, rotate=rotate,
                                     authenticated=exists and authenticated(stored.value))
            created = reconcile(conn, data, stored.password if change else None)
        if not authenticated(stored.value):
            raise Refusal('post-commit authentication failed; pending secret retained')
        if rotate:
            if paths.pending.exists():
                os.replace(paths.pending, final)
                credentials._fsync_directory(root)
        elif stored.state == 'pending':
            credentials.promote_pending(paths, key=key, expected_value=stored.value)
        result = proof(stored.value, data)
    return dict(environment=environment, created=created,
                password_changed=change, secret_key=key, proof=result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment', choices=['staging', 'production'], required=True)
    parser.add_argument('--rotate', action='store_true')
    args = parser.parse_args()
    data = load_spec()
    owner = os.environ.get('DATABASE_URL')
    if not owner:
        raise Refusal('run through tools/db-tap.py run mode')
    if args.environment == 'production' and os.environ.get('CARR_BREAK_GLASS') != '1':
        raise Refusal('production requires db-tap break-glass and its reason receipt')
    if urlsplit(owner).username != 'neondb_owner' or urlsplit(owner).path != '/neondb':
        raise Refusal('db-tap owner role and database required')
    tap_spec = importlib.util.spec_from_file_location('engine_role_db_tap', ROOT / 'tools/db-tap.py')
    tap = importlib.util.module_from_spec(tap_spec)
    tap_spec.loader.exec_module(tap)
    if not pathlib.Path(tap.NEONCTL).exists():
        tap.NEONCTL = shutil.which('neonctl') or tap.NEONCTL
    expected = tap.dsn(project=args.environment)
    if urlsplit(expected).hostname != urlsplit(owner).hostname or urlsplit(expected).path != urlsplit(owner).path:
        raise Refusal('db-tap target differs from the selected environment')
    output = provision(owner, data, args.environment, args.rotate,
                       pathlib.Path.home() / '.config/carr')
    result = output['proof']
    print(json.dumps(output, indent=2))
    return 0 if (all(result[k] is True for k in [
        'role_flags_match', 'no_memberships', 'no_ops_or_auth_usage', 'no_direct_routine_grants',
        'no_default_grants', 'no_sequence_rights', 'read_only_default', 'activity_whole_table_select',
        'ops_read_refused', 'insert_refused', 'update_refused', 'delete_refused'])
        and result['TimeZone'] == 'UTC' and all(result[n]['read'] for n in ['activity'] + data['views'])) else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (Refusal, credentials.CredentialRefusal, psycopg.Error, OSError, ValueError) as error:
        # Provider and SQL errors can contain credentials or row contents.
        print('engine-db-role refused: ' + type(error).__name__ +
              (': ' + str(error) if isinstance(error, (Refusal, credentials.CredentialRefusal)) else ''),
              file=sys.stderr)
        sys.exit(1)
