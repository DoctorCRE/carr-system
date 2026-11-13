#!/usr/bin/env python3
"""Reconcile bounded engine database roles from versioned specifications.

Use --environment production, optionally with --branch for a named Neon branch.
The reader CLI keeps its existing private credential-file protocol.
Writer callers use load_spec("writer") and reconcile in-process with Cloudflare-only custody.
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
ALLOWED_FUNCTIONS = frozenset({
    'carr_business_date(timestamp with time zone)', 'practice_address_key(text)',
})
CHECKS = (
    'role_flags_match', 'no_memberships', 'no_ops_or_auth_usage', 'function_grants_match',
    'functions_executable', 'no_schema_create', 'no_database_create',
    'no_default_grants', 'no_sequence_rights', 'read_only_default', 'activity_whole_table_select',
    'ops_read_refused', 'insert_refused', 'update_refused', 'delete_refused',
    'timezone_utc', 'statement_timeout_15s', 'idle_timeout_30s',
    'effective_relation_grants_match', 'no_ownership',
)


class Refusal(RuntimeError):
    """A role or credential lies outside the authorized contract."""


def validate_spec(data):
    if data['role'] not in (ROLE, 'engine_business_writer') or data['schema'] != 'public':
        raise Refusal('role spec exceeds the authorized role or schema')
    writer = data['role'] == 'engine_business_writer'
    allowed_functions = ALLOWED_FUNCTIONS | ({'carr_is_partner_actor(uuid)',
        'carr_is_partner_made(uuid,text)'} if writer else set())
    functions = data.get('functions')
    if (not isinstance(functions, list)
            or any(not isinstance(name, str) or name not in allowed_functions for name in functions)
            or len(functions) != len(set(functions))):
        raise Refusal('function spec must be a duplicate-free subset of the authorized functions')
    names = data['tables'] + data['views']
    if 'tool_call' in names or not names or len(names) != len(set(names)) or any(
        not re.fullmatch('[a-z_][a-z_0-9]*', name) for name in names
    ):
        raise Refusal('role spec contains duplicate or invalid relation names')
    if data['settings'] != {
        'default_transaction_read_only': 'off' if writer else 'on', 'timezone': 'UTC',
        'search_path': 'pg_catalog, public',
        'statement_timeout': '15s', 'idle_in_transaction_session_timeout': '30s',
    }:
        raise Refusal('role settings differ from the role contract')
    columns = data.get('columns', {})
    if data.get('writes') or not isinstance(columns, dict) or (columns and not writer):
        raise Refusal('only the writer may grant column privileges; table writes are forbidden')
    if set(columns) - {'SELECT', 'INSERT', 'UPDATE'}:
        raise Refusal('unsupported column privilege')
    for privilege, relations in columns.items():
        if not isinstance(relations, dict):
            raise Refusal('column grants must map relations to columns')
        for name, names in relations.items():
            if (name not in data['tables'] and name != 'tool_call') or (
                privilege == 'SELECT' and name != 'tool_call'
            ) or (name == 'tool_call' and privilege == 'UPDATE'):
                raise Refusal('column grant exceeds the writer relation contract')
            if (not isinstance(names, list) or not names or any(
                    not isinstance(n, str) or not re.fullmatch('[a-z_][a-z_0-9]*', n) for n in names)
                    or len(names) != len(set(names))):
                raise Refusal('column grants require duplicate-free column identifiers')
    return data


def load_spec(kind='reader'):
    if kind not in ('reader', 'writer'):
        raise Refusal('unknown engine role kind')
    reader = validate_spec(json.loads(SPEC.read_text()))
    if kind == 'reader':
        return reader
    writer = json.loads(SPEC.with_name('engine-business-writer.json').read_text())
    if writer.pop('extends') != 'reader':
        raise Refusal('writer must extend the reader contract')
    excluded = writer.pop('exclude_reads', [])
    if (not isinstance(excluded, list) or any(not isinstance(n, str) for n in excluded)
            or len(excluded) != len(set(excluded))
            or not set(excluded) <= set(reader['tables'] + reader['views'])):
        raise Refusal('writer read exclusions must name duplicate-free reader relations')
    return validate_spec(reader | writer | {
        'tables': [name for name in reader['tables'] if name not in excluded],
        'views': [name for name in reader['views'] if name not in excluded],
        'settings': reader['settings'] | {'default_transaction_read_only': 'off'},
    })


def password_change(*, exists, rotate, authenticated):
    if exists and not rotate and not authenticated:
        raise Refusal('existing role credential unavailable or invalid; explicit rotation required')
    return not exists or rotate


def attribute_options(observed):
    options = ['LOGIN', 'NOCREATEDB', 'NOCREATEROLE', 'NOINHERIT']
    options += [disabled for enabled, disabled in zip(
        observed, ['NOSUPERUSER', 'NOREPLICATION', 'NOBYPASSRLS']) if enabled]
    return ' '.join(options)


def relation_grants(data):
    return {(name, 'SELECT') for name in data['tables'] + data['views']}


def column_grants(data):
    return {(name, column, privilege) for privilege, relations in data.get('columns', {}).items()
            for name, columns in relations.items() for column in columns}


def grant_statements(data):
    validate_spec(data)
    return [f'GRANT {privilege} ON TABLE "public"."{name}" TO "{data["role"]}"'
            for name, privilege in sorted(relation_grants(data))] + [
                f'GRANT {privilege} ("{column}") ON TABLE "public"."{name}" TO "{data["role"]}"'
                for name, column, privilege in sorted(column_grants(data))] + [
                f'GRANT EXECUTE ON FUNCTION public.{name} TO "{data["role"]}"'
                for name in data['functions']]


def connect(uri):
    options = '-c lock_timeout=10000'
    if urlsplit(uri).username not in (ROLE, 'engine_business_writer'):
        options += ' -c statement_timeout=30000'
    return psycopg.connect(uri, connect_timeout=20, options=options)


def authenticated(uri, role_name=ROLE):
    try:
        with connect(uri) as conn:
            return conn.execute('SELECT current_user = %s', (role_name,)).fetchone()[0]
    except psycopg.OperationalError:
        return False


def revocations(conn, role_name=ROLE):
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
    """, (role_name,) * 8).fetchall()]


def reconcile(conn, data, password=None):
    validate_spec(data)
    role_name = data['role']
    conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (role_name,))
    exists = conn.execute('SELECT EXISTS(SELECT FROM pg_roles WHERE rolname=%s)', (role_name,)).fetchone()[0]
    if exists:
        if conn.execute("""SELECT EXISTS(SELECT FROM pg_shdepend d JOIN pg_roles r
            ON r.oid=d.refobjid WHERE r.rolname=%s AND d.deptype='o')""", (role_name,)).fetchone()[0]:
            raise Refusal('engine role owns objects; ownership changes exceed this tool')
    for name in data['tables'] + data['views']:
        kind = conn.execute('SELECT relkind FROM pg_class WHERE oid=to_regclass(%s)',
                            ('public.' + name,)).fetchone()
        expected = ('r', 'p') if name in data['tables'] else ('v',)
        if not kind or kind[0] not in expected:
            raise Refusal('allowlisted relation missing or wrong kind: ' + name)
    for name in data['functions']:
        if not conn.execute('SELECT to_regprocedure(%s)', ('public.' + name,)).fetchone()[0]:
            raise Refusal('allowlisted function missing: ' + name)
    if not exists:
        conn.execute(sql.SQL('CREATE ROLE {} LOGIN').format(sql.Identifier(role_name)))
    observed = conn.execute('SELECT rolsuper,rolreplication,rolbypassrls FROM pg_roles '
                            'WHERE rolname=%s', (role_name,)).fetchone()
    # Managed owners cannot reassert NOSUPERUSER even when it is already false.
    conn.execute(sql.SQL('ALTER ROLE {} ' + attribute_options(observed)).format(sql.Identifier(role_name)))
    conn.execute(sql.SQL('ALTER ROLE {} RESET ALL').format(sql.Identifier(role_name)))
    for (database,) in conn.execute("""SELECT d.datname FROM pg_db_role_setting s
        JOIN pg_database d ON d.oid=s.setdatabase
        WHERE s.setrole=(SELECT oid FROM pg_roles WHERE rolname=%s)""", (role_name,)).fetchall():
        conn.execute(sql.SQL('ALTER ROLE {} IN DATABASE {} RESET ALL').format(
            sql.Identifier(role_name), sql.Identifier(database)))
    for key, value in data['settings'].items():
        setting = (sql.SQL(', ').join(sql.Identifier(name) for name in value.split(', '))
                   if key == 'search_path' else sql.Literal(value))
        conn.execute(sql.SQL('ALTER ROLE {} SET {} = {}').format(
            sql.Identifier(role_name), sql.Identifier(key), setting))
    if password is not None:
        verifier = conn.pgconn.encrypt_password(password.encode(), role_name.encode(), b'scram-sha-256').decode()
        conn.execute(sql.SQL('ALTER ROLE {} PASSWORD {}').format(
            sql.Identifier(role_name), sql.Literal(verifier)))
    for statement in revocations(conn, role_name):
        conn.execute(statement)
    conn.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(
        sql.Identifier(conn.info.dbname), sql.Identifier(role_name)))
    conn.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role_name)))
    for statement in grant_statements(data):
        conn.execute(statement)
    actual = set(conn.execute("""SELECT n.nspname,c.relname,a.privilege_type,a.is_grantable
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace,
        aclexplode(c.relacl) a JOIN pg_roles r ON r.oid=a.grantee WHERE r.rolname=%s""",
        (role_name,)).fetchall())
    expected = {('public', name, privilege, False) for name, privilege in relation_grants(data)}
    if actual != expected:
        raise Refusal('direct relation grants differ from the role spec')
    if not function_grants_match(conn, data):
        raise Refusal('direct function grants differ from the role spec')
    if conn.execute("""SELECT EXISTS(SELECT FROM pg_type t,aclexplode(t.typacl) a
        JOIN pg_roles r ON r.oid=a.grantee WHERE r.rolname=%s)""", (role_name,)).fetchone()[0]:
        raise Refusal('unsupported direct type grants; transaction rolled back')
    actual_columns = set(conn.execute("""SELECT n.nspname,c.relname,att.attname,a.privilege_type,a.is_grantable
        FROM pg_attribute att JOIN pg_class c ON c.oid=att.attrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace, aclexplode(att.attacl) a
        JOIN pg_roles r ON r.oid=a.grantee WHERE r.rolname=%s""", (role_name,)).fetchall())
    if actual_columns != {('public', n, c, p, False) for n, c, p in column_grants(data)}:
        raise Refusal('direct column grants differ from the role spec')
    if data.get('columns') and not effective_relation_grants_match(conn, data):
        raise Refusal('effective relation privileges exceed or miss the role spec')
    return not exists


def function_grants_match(conn, data):
    actual = set(conn.execute("""SELECT p.oid,a.privilege_type,a.is_grantable
        FROM pg_proc p,aclexplode(p.proacl) a
        WHERE a.grantee=(SELECT oid FROM pg_roles WHERE rolname=%s)""", (data['role'],)).fetchall())
    expected = {(conn.execute('SELECT to_regprocedure(%s)::oid', ('public.' + name,)).fetchone()[0],
                 'EXECUTE', False) for name in data['functions']}
    return actual == expected


def effective_relation_grants_match(conn, data):
    expected = {('public', name, privilege) for name, privilege in relation_grants(data)}
    rows = conn.execute("""SELECT n.nspname,c.relname,p.privilege,
        has_table_privilege(%s,c.oid,p.privilege),
        has_table_privilege(%s,c.oid,p.privilege || ' WITH GRANT OPTION')
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        CROSS JOIN (VALUES ('SELECT'),('INSERT'),('UPDATE'),('DELETE'),('TRUNCATE'),
          ('REFERENCES'),('TRIGGER')) p(privilege)
        WHERE c.relkind IN ('r','p','v','m','f')
          AND n.nspname NOT IN ('pg_catalog','information_schema')
          AND n.nspname !~ '^pg_toast'""", (data['role'],) * 2).fetchall()
    actual = {(schema, name, privilege) for schema, name, privilege, table, grantable in rows if table}
    if actual != expected or any(grantable for *_, grantable in rows):
        return False
    columns = conn.execute("""SELECT n.nspname,c.relname,att.attname,p.privilege,
        has_column_privilege(%s,c.oid,att.attnum,p.privilege),
        has_column_privilege(%s,c.oid,att.attnum,p.privilege || ' WITH GRANT OPTION')
        FROM pg_attribute att JOIN pg_class c ON c.oid=att.attrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        CROSS JOIN (VALUES ('SELECT'),('INSERT'),('UPDATE'),('REFERENCES')) p(privilege)
        WHERE c.relkind IN ('r','p','v','m','f') AND att.attnum>0 AND NOT att.attisdropped
          AND n.nspname NOT IN ('pg_catalog','information_schema')
          AND n.nspname !~ '^pg_toast'""", (data['role'],) * 2).fetchall()
    explicit = {('public', name, column, privilege) for name, column, privilege in column_grants(data)}
    observed = {(schema, name, column, privilege)
                for schema, name, column, privilege, allowed, grantable in columns
                if allowed and (schema, name, privilege) not in expected}
    return observed == explicit and all(not grantable for *_, grantable in columns)


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
        result['function_grants_match'] = function_grants_match(conn, data)
        result['functions_executable'] = all([conn.execute(
            "SELECT has_function_privilege(current_user,%s,'EXECUTE')",
            ('public.' + name,)).fetchone()[0] for name in data['functions']])
        result['no_schema_create'] = conn.execute("""SELECT NOT EXISTS(
            SELECT FROM pg_namespace WHERE has_schema_privilege(current_user,oid,'CREATE'))""").fetchone()[0]
        result['no_database_create'] = conn.execute(
            "SELECT NOT has_database_privilege(current_user,current_database(),'CREATE')").fetchone()[0]
        result['no_default_grants'] = conn.execute("""SELECT NOT EXISTS(
            SELECT FROM pg_default_acl d,aclexplode(d.defaclacl) a
            WHERE a.grantee=(SELECT oid FROM pg_roles WHERE rolname=current_user))""").fetchone()[0]
        result['no_sequence_rights'] = conn.execute("""SELECT NOT EXISTS(
            SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE c.relkind='S' AND n.nspname NOT IN ('pg_catalog','information_schema')
            AND has_sequence_privilege(current_user,c.oid,'SELECT,USAGE,UPDATE'))""").fetchone()[0]
        result['timezone_utc'] = conn.execute('SHOW TimeZone').fetchone()[0] == 'UTC'
        result['statement_timeout_15s'] = conn.execute('SHOW statement_timeout').fetchone()[0] == '15s'
        result['idle_timeout_30s'] = conn.execute(
            'SHOW idle_in_transaction_session_timeout').fetchone()[0] == '30s'
        result['read_only_default'] = (conn.execute('SHOW default_transaction_read_only').fetchone()[0]
                                       == data['settings']['default_transaction_read_only'])
        result['effective_relation_grants_match'] = effective_relation_grants_match(conn, data)
        result['no_ownership'] = not conn.execute("""SELECT EXISTS(SELECT FROM pg_shdepend d
            WHERE d.refobjid=(SELECT oid FROM pg_roles WHERE rolname=current_user)
            AND d.deptype='o')""").fetchone()[0]
        result['activity_whole_table_select'] = conn.execute(
            "SELECT has_table_privilege(current_user,'public.activity','SELECT')").fetchone()[0]
        for name in ['activity'] + data['views']:
            try:
                with conn.transaction():
                    row = conn.execute(sql.SQL('SELECT * FROM public.{} LIMIT 1').format(
                        sql.Identifier(name))).fetchone()
                    result[name] = {'read': True, 'has_rows': row is not None}
            except psycopg.Error:
                result[name] = {'read': False, 'has_rows': False}
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
    if data['role'] != ROLE:
        raise Refusal('writer credentials must stay in memory and Cloudflare; use reconcile directly')
    parsed = urlsplit(owner)
    key = 'CARR_DB_' + environment.upper().replace('-', '_') + '_ENGINE_BUSINESS_READER_URL'
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
    return dict(environment=environment, created=created is True,
                password_changed=change is True, secret_key=key,
                proof={name: result[name] is True for name in CHECKS} | {
                    name: {'read': result[name]['read'] is True,
                           'has_rows': result[name]['has_rows'] is True}
                    for name in ['activity'] + data['views']})


def print_result(output):
    print(json.dumps(output, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment', choices=['production'], required=True)
    parser.add_argument('--branch', help='named Neon branch in the production project')
    parser.add_argument('--rotate', action='store_true')
    args = parser.parse_args()
    if args.branch and not re.fullmatch('[a-z][a-z0-9-]*', args.branch):
        raise Refusal('branch must be a lowercase Neon branch name')
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
    expected = tap.dsn(branch=args.branch, project=args.environment)
    if urlsplit(expected).hostname != urlsplit(owner).hostname or urlsplit(expected).path != urlsplit(owner).path:
        raise Refusal('db-tap target differs from the selected environment')
    target = args.environment if args.branch in (None, 'production') else 'branch-' + args.branch
    output = provision(owner, data, target, args.rotate,
                       pathlib.Path.home() / '.config/carr')
    result = output['proof']
    print_result(output)
    return 0 if (all(result[k] for k in CHECKS)
                 and all(result[n]['read'] for n in ['activity'] + data['views'])) else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (Refusal, credentials.CredentialRefusal, psycopg.Error, OSError, ValueError) as error:
        # Provider and SQL errors can contain credentials or row contents.
        print('engine-db-role refused: ' + type(error).__name__ +
              (': ' + str(error) if isinstance(error, (Refusal, credentials.CredentialRefusal)) else ''),
              file=sys.stderr)
        sys.exit(1)
