"""Own snapshot databases on a disposable CI service and restore cluster role state."""
from contextlib import contextmanager
import os
import re
import subprocess
from urllib.parse import unquote, urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import sql

ATTRIBUTES = ('rolsuper', 'rolinherit', 'rolcreaterole', 'rolcreatedb', 'rolcanlogin',
              'rolreplication', 'rolbypassrls', 'rolconnlimit')
OPTIONS = ('SUPERUSER', 'INHERIT', 'CREATEROLE', 'CREATEDB', 'LOGIN', 'REPLICATION', 'BYPASSRLS')


def role_state(admin):
    roles = {row[0]: row[1:] for row in admin.execute(
        'select rolname,' + ','.join(ATTRIBUTES) + ',rolconfig from pg_roles')}
    members = set(admin.execute('''select r.rolname,m.rolname,g.rolname,a.admin_option,a.inherit_option,a.set_option
        from pg_auth_members a join pg_roles r on r.oid=a.roleid
        join pg_roles m on m.oid=a.member join pg_roles g on g.oid=a.grantor'''))
    return roles, members


def restore_roles(admin, before):
    roles, members = before
    current, current_members = role_state(admin)
    for role, member, grantor, *_ in current_members - members:
        admin.execute(sql.SQL('revoke {} from {} granted by {} cascade').format(
            *map(sql.Identifier, (role, member, grantor))))
    for role, member, grantor, adm, inherit, setting in members - current_members:
        admin.execute(sql.SQL('grant {} to {} with admin {}, inherit {}, set {} granted by {}').format(
            sql.Identifier(role), sql.Identifier(member), sql.Literal(adm), sql.Literal(inherit),
            sql.Literal(setting), sql.Identifier(grantor)))
    for name in current.keys() - roles.keys():
        admin.execute(sql.SQL('drop role {}').format(sql.Identifier(name)))
    for name, state in roles.items():
        if current.get(name) == state:
            continue
        flags = [option if value else 'NO' + option for option, value in zip(OPTIONS, state[:7])]
        admin.execute(sql.SQL('alter role {} ' + ' '.join(flags) + ' connection limit {}').format(
            sql.Identifier(name), sql.Literal(state[7])))
        if current.get(name, (None,) * 9)[8] != state[8]:
            with admin.transaction():
                admin.execute(sql.SQL('alter role {} reset all').format(sql.Identifier(name)))
                for setting in state[8] or []:
                    key, value = setting.split('=', 1)
                    admin.execute('select pg_catalog.set_config(%s,%s,true)', (key, value))
                    admin.execute(sql.SQL('alter role {} set {} from current').format(
                        sql.Identifier(name), sql.Identifier(key)))


@contextmanager
def service_database(dsn, prefix, schema=None, source=None, *, template=False, migrate=False):
    url = urlsplit(dsn)
    if url.scheme not in ('postgres', 'postgresql') or url.hostname not in ('localhost', '127.0.0.1') or url.query or url.fragment:
        raise ValueError('snapshot service requires a disposable loopback URL without overrides')
    name = re.sub('[^a-z0-9_]', '_', prefix.lower()).strip('_')[:28] + '_' + uuid4().hex
    if source and not re.fullmatch(r'mcp_snapshot_[a-f0-9]{32}', source):
        raise ValueError('invalid service snapshot template name')
    connection = urlunsplit(url._replace(path='/' + name))
    with psycopg.connect(dsn, hostaddr='127.0.0.1', autocommit=True) as admin:
        before = role_state(admin)
        created = False
        try:
            admin.execute(sql.SQL('create database {} template {}').format(
                sql.Identifier(name), sql.Identifier(source or 'template0')))
            created = True
            if not source and schema is not None:
                admin.execute('do $$ begin create role neondb_owner; exception when duplicate_object then null; end $$')
                restore_env = {**os.environ, 'PGHOST': url.hostname, 'PGHOSTADDR': '127.0.0.1',
                               'PGPORT': str(admin.info.port), 'PGUSER': admin.info.user, 'PGDATABASE': name}
                if url.password is not None:
                    restore_env['PGPASSWORD'] = unquote(url.password)
                result = subprocess.run(['psql', '-X', '-q', '-v', 'ON_ERROR_STOP=1', '-f', str(schema)],
                    env=restore_env, capture_output=True, text=True, timeout=120)
                if result.returncode:
                    raise RuntimeError('CI snapshot restore failed: ' + result.stderr[-2000:])
            if migrate:
                from postgres_snapshot_fixture import migrate_snapshot
                migrate_snapshot(schema, connection)
            yield {'connection': {'connectionString': connection}, 'dsn': connection,
                   'snapshot': name if template else None}
        finally:
            if created:
                admin.execute(sql.SQL('drop database {} with (force)').format(sql.Identifier(name)))
            restore_roles(admin, before)
