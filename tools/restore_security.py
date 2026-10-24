"""Rebuild and verify disposable restore ACLs from one pinned snapshot."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from schema_snapshot_grants import (acl_facts, carr_grants_section_lines,
                                   match_generated_grant, render_acl_facts)

ROLE_BUNDLES = {'app_writer': 'carr_writer', 'carr_writer': 'carr_writer',
                'app_reader': 'carr_reader', 'carr_reader': 'carr_reader'}


def declaration(schema_text, revision, blob):
    if not re.fullmatch('[0-9a-f]{40}', revision or ''):
        raise ValueError('artifact schema revision cannot be identified')
    raw = schema_text.encode('utf-8')
    actual = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    if blob != actual:
        raise ValueError('artifact db/schema.sql blob digest does not match')
    statements = carr_grants_section_lines(schema_text)
    facts = {}
    memberships = {}
    revoked = set()
    for statement in statements:
        groups = match_generated_grant(statement).groupdict()
        if statement.startswith('revoke '):
            grant = statement.replace('revoke all', 'grant execute').replace(' from public;', ' to carr_writer;')
            revoked.update(acl_facts([grant]))
        elif groups.get('granted_role'):
            memberships.setdefault(groups['grantee'], set()).add(groups['granted_role'])
        else:
            facts.setdefault(groups['grantee'], set()).update(acl_facts([statement]))
    for bundle in set(ROLE_BUNDLES.values()):
        if not facts.get(bundle):
            raise ValueError(f'artifact declaration has no {bundle} grants')
    effective = {}
    for role, bundle in ROLE_BUNDLES.items():
        pending, seen = [bundle], set()
        effective[role] = set()
        while pending:
            item = pending.pop()
            if item in seen:
                continue
            seen.add(item)
            effective[role].update(facts.get(item, ()))
            pending.extend(memberships.get(item, ()))
    return {'schema_revision': revision, 'schema_blob': blob,
            'statements': statements, 'facts': effective, 'revoked': revoked}


def apply_grants(conn, declared):
    """Only the caller-created disposable database receives object ACL changes."""
    with conn.transaction():
        for statement in declared['statements']:
            if not match_generated_grant(statement).groupdict().get('granted_role'):
                conn.execute(statement)
        for role in ('app_writer', 'app_reader'):
            for statement in render_acl_facts(declared['facts'][role], role):
                conn.execute(statement)


PRIVILEGES_SQL = """
with ns as (select oid,nspname from pg_namespace where nspname in ('public','ops'))
select 'schema', nspname, p||s, has_schema_privilege(current_user,oid,p||s)
from ns cross join unnest(array['usage','create']) p cross join unnest(array['',' with grant option']) s
union all
select 'table',n.nspname||'.'||c.relname,p||s,has_table_privilege(current_user,c.oid,p||s)
from pg_class c join ns n on n.oid=c.relnamespace
cross join unnest(array['select','insert','update','delete','truncate','references','trigger','maintain']) p cross join unnest(array['',' with grant option']) s
where c.relkind in ('r','p','v','m','f')
union all
select 'sequence',n.nspname||'.'||c.relname,p||s,has_sequence_privilege(current_user,c.oid,p||s)
from pg_class c join ns n on n.oid=c.relnamespace
cross join unnest(array['usage','select','update']) p cross join unnest(array['',' with grant option']) s where c.relkind='S'
union all
select 'column',n.nspname||'.'||c.relname||'('||a.attname||')',p||s,
       has_column_privilege(current_user,c.oid,a.attnum,p||s)
from pg_class c join ns n on n.oid=c.relnamespace
join pg_attribute a on a.attrelid=c.oid and a.attnum>0 and not a.attisdropped
cross join unnest(array['select','insert','update','references']) p cross join unnest(array['',' with grant option']) s
where c.relkind in ('r','p','v','m','f')
union all
select 'function',n.nspname||'.'||p.proname||'('||oidvectortypes(p.proargtypes)||')',
       'execute'||s,has_function_privilege(current_user,p.oid,'execute'||s)
from pg_proc p join ns n on n.oid=p.pronamespace
cross join unnest(array['',' with grant option']) s
"""


def expected(declared, role, kind, identity, privilege):
    if privilege.endswith(' with grant option'):
        return False
    fact = (kind, identity, privilege, False)
    facts = declared['facts'][role]
    if fact in facts:
        return True
    if kind == 'column':
        return ('table', identity.rsplit('(', 1)[0], privilege, False) in facts
    if kind == 'schema':
        return identity == 'public' and privilege == 'usage'
    if kind == 'function':
        return fact not in declared['revoked']
    return False


def check_parity(conn, declared):
    """Read effective ACLs as each app role and plan allowed/denied operations."""
    from psycopg import sql
    from psycopg.errors import InsufficientPrivilege

    mismatches, roles = [], {}
    for role in ROLE_BUNDLES:
        counts = {'positive': 0, 'negative': 0, 'operations': 0}
        with conn.transaction():
            conn.execute('set transaction read only')
            conn.execute(sql.SQL('set local role {}').format(sql.Identifier(role)))
            rows = conn.execute(PRIVILEGES_SQL).fetchall()
            probe_tables = {row[0] for row in conn.execute("""
                select n.nspname||'.'||c.relname from pg_class c
                join pg_namespace n on n.oid=c.relnamespace
                where n.nspname in ('public','ops') and c.relkind in ('r','p')
            """).fetchall()}
            probes = set()
            for kind, identity, privilege, observed in rows:
                wanted = expected(declared, role, kind, identity, privilege)
                counts['positive' if wanted else 'negative'] += 1
                if observed != wanted:
                    mismatches.append(f'{role}:{kind}:{identity}:{privilege}')
                # EXPLAIN checks permissions without running triggers, sequences,
                # application functions, or data-changing statements.
                key = (privilege, wanted)
                if kind != 'column' or privilege not in ('select', 'insert', 'update') or key in probes:
                    continue
                relation, column = identity[:-1].rsplit('(', 1)
                if relation not in probe_tables:
                    continue
                schema, table = relation.split('.', 1)
                if not expected(declared, role, 'schema', schema, 'usage'):
                    continue
                relation_sql = sql.Identifier(schema, table)
                column_sql = sql.Identifier(column)
                if privilege == 'select':
                    query = sql.SQL('explain select {} from {}').format(column_sql, relation_sql)
                elif privilege == 'insert':
                    query = sql.SQL('explain insert into {} ({}) values (null)').format(relation_sql, column_sql)
                else:
                    query = sql.SQL('explain update {} set {}=null where false').format(relation_sql, column_sql)
                try:
                    with conn.transaction():
                        conn.execute(query)
                    allowed = True
                except InsufficientPrivilege:
                    allowed = False
                if allowed != wanted:
                    mismatches.append(f'{role}:operation:{identity}:{privilege}')
                probes.add(key)
                counts['operations'] += 1
        if not any(wanted for _, wanted in probes) or not any(not wanted for _, wanted in probes):
            mismatches.append(f'{role}:missing_positive_or_negative_operation')
        roles[role] = counts
    for role, counts in roles.items():
        if not all(counts.values()):
            mismatches.append(f'{role}:incomplete_probes')
    return {'verdict': 'FAIL' if mismatches else 'PASS',
            'schema_revision': declared['schema_revision'], 'schema_blob': declared['schema_blob'],
            'roles': roles, 'mismatches': mismatches}


def rebuild_security(target, declared):
    import psycopg
    with psycopg.connect(target, autocommit=True, connect_timeout=30,
                         options='-c statement_timeout=30000') as conn:
        apply_grants(conn, declared)
        return check_parity(conn, declared)
