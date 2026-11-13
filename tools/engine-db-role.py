#!/usr/bin/env python3
"""Reconcile bounded engine database roles from versioned specifications.

Use --environment production, optionally with --branch for a named Neon branch.
The reader CLI keeps its existing private credential-file protocol.
Writer --writer runs the reviewed procedure with in-memory credential custody.
Writer --writer --recover disables an orphan after inspecting Hyperdrive.
See docs/engine-role-procedure.md before any authorized production execution.
Reader --rotate publishes to Hyperdrive without writing a credential file.
The --rotate flag is the only way to reset an existing login's password.
"""
import argparse
import importlib.util
import json
import os
import pathlib
import re
import shutil
import secrets
import sys
from urllib.parse import urlsplit

import psycopg
from psycopg import sql

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
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
    if password is not None:
        set_password(conn, role_name, password)
    return not exists


LOGGING_GUARDS = {
    'log_statement': 'none', 'log_min_error_statement': 'panic',
    'log_min_duration_statement': '-1', 'log_parameter_max_length': '0',
}


def set_password(conn, role_name, password):
    """Neon forwards plaintext at commit; suppress every controllable SQL log."""
    if role_name not in (ROLE, 'engine_business_writer'):
        raise Refusal('password target exceeds the engine role contract')
    if not isinstance(password, str) or not password or '\x00' in password:
        raise Refusal('invalid password input')
    for key, value in LOGGING_GUARDS.items():
        try:
            with conn.transaction():
                conn.execute(sql.SQL('SET LOCAL {} = {}').format(sql.Identifier(key), sql.Literal(value)))
        except psycopg.errors.InsufficientPrivilege:
            pass
    # Refuse predictable statement failures before sending any secret.
    state = conn.execute("""SELECT r.rolsuper,r.rolcanlogin,
        current_setting('transaction_read_only')='off'
        FROM pg_roles r WHERE r.rolname=%s""", (role_name,)).fetchone()
    if not state or state[0] or not state[1] or not state[2]:
        raise Refusal('password target must be an existing nonsuperuser login in a writable transaction')
    # Successful non-secret ALTER proves role administration before password SQL.
    conn.execute(sql.SQL('ALTER ROLE {} LOGIN').format(sql.Identifier(role_name)))
    try:
        conn.execute(sql.SQL('ALTER ROLE {} PASSWORD {}').format(
            sql.Identifier(role_name), sql.Literal(password)))
    except Exception:
        raise Refusal('password statement failed; transaction must roll back') from None


def invalidate_credential(conn, role_name):
    if role_name not in (ROLE, 'engine_business_writer'):
        raise Refusal('invalidation target exceeds the engine role contract')
    conn.execute(sql.SQL('ALTER ROLE {} NOLOGIN PASSWORD NULL').format(sql.Identifier(role_name)))


def clear_password_statements(owner, role_name):
    """Remove committed password utility text from the cluster-wide statistics."""
    from psycopg.conninfo import make_conninfo
    with connect(make_conninfo(owner, dbname='postgres')) as conn:
        installed = conn.execute("SELECT to_regclass('pg_stat_statements')").fetchone()[0]
        if installed is None:
            if 'pg_stat_statements' in conn.execute('SHOW shared_preload_libraries').fetchone()[0]:
                raise Refusal('password statistics are enabled but the reset interface is unavailable')
            return
        schema = conn.execute("""SELECT n.nspname FROM pg_extension e
            JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='pg_stat_statements'""").fetchone()
        if not schema:
            raise Refusal('password statistics extension cannot be located')
        view = sql.Identifier(schema[0], 'pg_stat_statements')
        # Parameters select only statement IDs. Never fetch or print the utility text.
        prefixes = [sql.SQL('ALTER ROLE {} PASSWORD ').format(sql.Identifier(role_name)).as_string(),
                    'ALTER ROLE ' + role_name + ' PASSWORD ']
        predicate = sql.SQL('(left(query,length(%s))=%s OR left(query,length(%s))=%s)')
        params = (prefixes[0], prefixes[0], prefixes[1], prefixes[1])
        rows = conn.execute(sql.SQL('SELECT userid,dbid,queryid FROM {} WHERE ').format(view)
                            + predicate, params).fetchall()
        for userid, dbid, queryid in rows:
            conn.execute(sql.SQL('SELECT {}(%s::oid,%s::oid,%s::bigint)').format(
                sql.Identifier(schema[0], 'pg_stat_statements_reset')), (userid, dbid, queryid))
        if conn.execute(sql.SQL('SELECT EXISTS(SELECT FROM {} WHERE ').format(view)
                        + predicate + sql.SQL(')'), params).fetchone()[0]:
            raise Refusal('password statistics entry remains after reset')


def credential_lock(conn, role_name):
    if '-pooler.' in conn.info.host:
        raise Refusal('credential rotation requires a direct session, never a pooler')
    # Session ownership survives the password commit and ends when the connection closes.
    if not conn.execute('SELECT pg_try_advisory_lock(hashtext(%s))', (role_name,)).fetchone()[0]:
        raise Refusal('another credential rotation is in progress')
    conn.commit()


def recover_credential(owner, data, inspect_hyperdrive):
    """An interrupted publish must be inspected under the same lock as rotation."""
    try:
        with connect(owner) as conn:
            credential_lock(conn, data['role'])
            config_id = inspect_hyperdrive()
            exists = conn.execute('SELECT EXISTS(SELECT FROM pg_roles WHERE rolname=%s)',
                                  (data['role'],)).fetchone()[0]
            if config_id:
                if not exists:
                    raise Refusal('Hyperdrive exists without its role; inspect before recovery')
                return {'hyperdrive_exists': True, 'invalidated': False}
            if exists:
                invalidate_credential(conn, data['role'])
                conn.commit()
                clear_password_statements(owner, data['role'])
            return {'hyperdrive_exists': False, 'invalidated': bool(exists)}
    except Refusal:
        raise
    except BaseException:
        raise Refusal('credential recovery failed; inspect metadata before retry') from None


def publish_credential(owner, data, publish, *, rotate=False, connection=None):
    """Hold the role lock through commit, statistics cleanup, login and publication."""
    from contextlib import nullcontext
    try:
        with (nullcontext(connection) if connection is not None else connect(owner)) as conn:
            credential_lock(conn, data['role'])
            exists = conn.execute('SELECT EXISTS(SELECT FROM pg_roles WHERE rolname=%s)',
                                  (data['role'],)).fetchone()[0]
            if exists != rotate:
                raise Refusal('creation requires an absent role; rotation requires an existing role')
            password = secrets.token_urlsafe(48)
            try:
                created = reconcile(conn, data, password)
                conn.commit()
            except BaseException:
                raise Refusal('credential reconciliation or commit failed') from None
            try:
                clear_password_statements(owner, data['role'])
                uri = credentials.build_role_uri(owner, data['role'], password)
                if not authenticated(uri, data['role']):
                    raise Refusal('post-commit login failed')
                publish(password)
            except BaseException:
                try:
                    conn.rollback()
                    invalidate_credential(conn, data['role'])
                    conn.commit()
                except BaseException:
                    raise Refusal('credential publication failed; INVALIDATION FAILED, recovery required') from None
                try:
                    clear_password_statements(owner, data['role'])
                except BaseException:
                    raise Refusal('credential invalidated; password statistics cleanup failed, recovery required') from None
                raise Refusal('credential publication failed; credential invalidated; inspect Hyperdrive before retry') from None
            return {'created': created is True, 'password_changed': True}
    except Refusal:
        raise
    except BaseException:
        raise Refusal('credential operation failed; inspect recovery status') from None


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


def hyperdrive_publisher(owner, data, account, config_id=None):
    """Preflight metadata before SQL; publish once after the fresh login."""
    import urllib.request
    from lib.release_reader import read, NoRedirect
    from lib.token_store import read as token
    if not re.fullmatch('[a-f0-9]{32}', account) or (config_id is not None
            and not re.fullmatch('[a-f0-9]{32}', config_id)):
        raise Refusal('invalid Hyperdrive account or config ID')
    parsed = urlsplit(owner)
    if parsed.username != 'neondb_owner' or parsed.path != '/neondb' or '-pooler.' in (parsed.hostname or ''):
        raise Refusal('direct owner origin required')
    name = 'doctorcre-engine-db-' + ('reader' if data['role'] == ROLE else 'writer')
    base = 'https://api.cloudflare.com/client/v4/accounts/' + account + '/hyperdrive/configs'
    headers = {'Authorization': 'Bearer ' + token('CLOUDFLARE_API_TOKEN'),
               'Content-Type': 'application/json'}
    try:
        reply = read(base + ('/' + config_id if config_id else ''), headers=headers)
        response = json.loads(reply.body)
        if reply.status != 200 or not response.get('success'):
            raise Refusal('Hyperdrive metadata unavailable')
        metadata = response['result']
        if config_id:
            if (metadata['origin']['host'] != parsed.hostname
                    or metadata['origin']['database'] != 'neondb'
                    or metadata['origin']['user'] != data['role']):
                raise Refusal('Hyperdrive target differs from requested role')
            name = metadata['name']
            if not isinstance(metadata.get('caching'), dict) or not isinstance(
                    metadata.get('origin_connection_limit'), int):
                raise Refusal('Hyperdrive settings unavailable; refusing replacement')
        elif response.get('result_info', {}).get('total_pages', 1) > 1 or any(
                item['name'] == name for item in metadata):
            raise Refusal('Hyperdrive name exists or absence is unproven')
    except Exception:
        raise Refusal('Hyperdrive preflight failed') from None

    def publish(password):
        payload = {'name': name, 'origin': {'scheme': 'postgres', 'host': parsed.hostname,
            'port': parsed.port or 5432, 'database': 'neondb', 'user': data['role'],
            'password': password},
            'caching': metadata['caching'] if config_id else {'disabled': True},
            'origin_connection_limit': metadata['origin_connection_limit'] if config_id else 10}
        request = urllib.request.Request(base + ('/' + config_id if config_id else ''),
            method='PUT' if config_id else 'POST', headers=headers, data=json.dumps(payload).encode())
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as reply:
                if not json.load(reply).get('success'):
                    raise Refusal('Hyperdrive publication refused')
        except Exception:
            raise Refusal('Hyperdrive publication failed or outcome unknown') from None
    return publish


PRODUCTION_WRITER_HOST = 'ep-restless-resonance-awbp35k3.c-12.us-east-1.aws.neon.tech'
PRODUCTION_HYPERDRIVE_ACCOUNT = '12ccca77eb49142a6be8eb84c0d6a3a0'


def validate_writer_owner(owner):
    parsed = urlsplit(owner)
    if os.environ.get('CARR_BREAK_GLASS') != '1':
        raise Refusal('writer requires db-tap production break-glass receipt')
    if (parsed.hostname != PRODUCTION_WRITER_HOST or parsed.username != 'neondb_owner'
            or parsed.path != '/neondb' or parsed.port not in (None, 5432)):
        raise Refusal('writer requires the pinned direct production owner endpoint')


def inspect_writer_hyperdrive(owner, data):
    from lib.release_reader import read
    from lib.token_store import read as token
    try:
        response = read('https://api.cloudflare.com/client/v4/accounts/'
            + PRODUCTION_HYPERDRIVE_ACCOUNT + '/hyperdrive/configs',
            headers={'Authorization': 'Bearer ' + token('CLOUDFLARE_API_TOKEN')})
        body = json.loads(response.body)
        if (response.status != 200 or body.get('success') is not True
                or body.get('result_info', {}).get('total_pages', 1) != 1):
            raise Refusal('Hyperdrive absence is unproven')
        matches = [item for item in body['result'] if item['name'] == 'doctorcre-engine-db-writer']
        if not matches:
            return None
        if len(matches) != 1:
            raise Refusal('multiple writer Hyperdrives require inspection')
        item = matches[0]
        if (item['origin']['host'] != urlsplit(owner).hostname
                or item['origin']['database'] != 'neondb' or item['origin']['user'] != data['role']
                or not re.fullmatch('[a-f0-9]{32}', item['id'])):
            raise Refusal('writer Hyperdrive target differs')
        return item['id']
    except BaseException:
        raise Refusal('writer Hyperdrive inspection failed; recovery refused') from None


def writer_procedure(owner, *, rotate=False, recover=False):
    """Reviewed production entrypoint. Recovery never publishes or reveals a secret."""
    validate_writer_owner(owner)
    data = load_spec('writer')
    if recover:
        return recover_credential(owner, data, lambda: inspect_writer_hyperdrive(owner, data))
    with connect(owner) as locked:
        credential_lock(locked, data['role'])
        with connect(owner) as conn:
            observed = {key: conn.execute('SHOW ' + key).fetchone()[0] for key in LOGGING_GUARDS}
        if observed != {'log_statement': 'none', 'log_min_error_statement': 'panic',
                        'log_min_duration_statement': '-1', 'log_parameter_max_length': '-1'}:
            raise Refusal('owner logging settings differ; reassess risk before password SQL')
        config_id = inspect_writer_hyperdrive(owner, data)
        if bool(config_id) != rotate:
            raise Refusal('creation requires absent Hyperdrive; rotation requires matching Hyperdrive')
        publisher = hyperdrive_publisher(owner, data, PRODUCTION_HYPERDRIVE_ACCOUNT, config_id)
        # Recovery can leave an existing NOLOGIN role. Creation may safely reuse only that state.
        if not rotate:
            with connect(owner) as conn:
                state = conn.execute('SELECT rolcanlogin FROM pg_roles WHERE rolname=%s',
                                     (data['role'],)).fetchone()
            if state:
                if state[0]:
                    raise Refusal('writer login already exists; run --recover first')
                rotate = True
        def checked_publish(password):
            checks = proof(credentials.build_role_uri(owner, data['role'], password), data)
            if not (all(checks[k] for k in CHECKS)
                    and all(checks[n]['read'] for n in ['activity'] + data['views'])):
                raise Refusal('fresh writer proof failed')
            publisher(password)
        return publish_credential(owner, data, checked_publish, rotate=rotate, connection=locked)


def provision(owner, data, environment, rotate, root, publish=None):
    if data['role'] != ROLE:
        raise Refusal('writer credentials must stay in memory and Cloudflare; use the reviewed --writer procedure')
    if rotate:
        if publish is None:
            raise Refusal('rotation requires an in-memory credential publisher')
        def checked_publish(password):
            result = proof(credentials.build_role_uri(owner, data['role'], password), data)
            if not (all(result[k] for k in CHECKS)
                    and all(result[n]['read'] for n in ['activity'] + data['views'])):
                raise Refusal('post-commit role proof failed')
            publish(password)
        return publish_credential(owner, data, checked_publish, rotate=True)
    parsed = urlsplit(owner)
    key = 'CARR_DB_' + environment.upper().replace('-', '_') + '_ENGINE_BUSINESS_READER_URL'
    final = root / (environment + '-engine-business-reader.env')
    paths = credentials.CredentialPaths(final, pathlib.Path(str(final) + '.pending'))
    params = dict(key=key, role_name=ROLE, owner_uri=owner,
                  expected_endpoint=parsed.hostname, expected_port=parsed.port or 5432,
                  expected_database=parsed.path.lstrip('/'))
    with credentials.exclusive_lock(root / (environment + '-engine-business-reader.lock')):
        with connect(owner) as conn:
            credential_lock(conn, ROLE)
            exists = conn.execute('SELECT EXISTS(SELECT FROM pg_roles WHERE rolname=%s)', (ROLE,)).fetchone()[0]
            if exists and credentials.file_state(paths) == 'absent' and not rotate:
                raise Refusal('existing role has no local secret; explicit rotation required')
            stored = credentials.prepare_pending(paths, **params)
            change = password_change(exists=exists, rotate=rotate,
                                     authenticated=exists and authenticated(stored.value))
            created = reconcile(conn, data, stored.password if change else None)
            conn.commit()
            if change:
                clear_password_statements(owner, ROLE)
            if not authenticated(stored.value):
                raise Refusal('post-commit authentication failed; pending secret retained')
            if stored.state == 'pending':
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
    parser.add_argument('--writer', action='store_true', help='run the reviewed production writer procedure')
    parser.add_argument('--recover', action='store_true', help='inspect writer Hyperdrive and disable an orphan role')
    parser.add_argument('--hyperdrive-account')
    parser.add_argument('--hyperdrive-config')
    args = parser.parse_args()
    if args.branch and not re.fullmatch('[a-z][a-z0-9-]*', args.branch):
        raise Refusal('branch must be a lowercase Neon branch name')
    data = load_spec()
    if args.recover and (not args.writer or args.rotate):
        raise Refusal('--recover requires --writer and cannot combine with --rotate')
    if args.writer and args.branch:
        raise Refusal('writer procedure requires production without a branch override')
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
    if args.writer:
        print_result(writer_procedure(owner, rotate=args.rotate, recover=args.recover))
        return 0
    target = args.environment if args.branch in (None, 'production') else 'branch-' + args.branch
    publisher = None
    if args.rotate:
        if not args.hyperdrive_account or not args.hyperdrive_config:
            raise Refusal('rotation requires --hyperdrive-account and --hyperdrive-config')
        publisher = hyperdrive_publisher(owner, data, args.hyperdrive_account, args.hyperdrive_config)
    output = provision(owner, data, target, args.rotate,
                       pathlib.Path.home() / '.config/carr', publish=publisher)
    if args.rotate:
        print_result(output)
        return 0
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
