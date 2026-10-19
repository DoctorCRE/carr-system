#!/usr/bin/env python3
"""Run-owned synthetic lease, accepted Tour and confidential Share fixtures."""
import argparse
import importlib
import json
import os
import pathlib
import subprocess
import sys
import uuid

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
provisioner = importlib.import_module('tools.provision-staging-app-writer')
guard = importlib.import_module('tools.staging-fixtures')

NAMESPACE = uuid.UUID('3fe45426-69e5-4295-aa47-b519b134c27d')


def identities(run):
    parsed = uuid.UUID(run)
    if parsed.version != 4 or str(parsed) != run:
        raise ValueError('fixture run must be a canonical random UUID')
    return {name: str(uuid.uuid5(NAMESPACE, f'{run}:{name}')) for name in (
        'party', 'client', 'lease', 'tour', 'route', 'projection', 'rights', 'evidence',
        'property_a', 'property_b', 'unrelated_property', 'stop_a', 'stop_b')}


TENANT = 'carr-internal'
SCHEMA = 'doctorcre-staging-journey-fixture.v1'


def scalar(cur, query, args=()):
    cur.execute(query, args)
    return cur.fetchone()[0]


def owner(cur):
    if scalar(cur, "select session_user=current_user and session_user='neondb_owner' and current_database()='neondb'") is not True:
        raise ValueError('fixture requires exact owner session on verified staging database')
    cur.execute("set local lock_timeout='5s'")
    cur.execute("set local statement_timeout='30s'")


def manifest(run):
    ids = identities(run)
    return {'schema': SCHEMA, 'run': run,
            'lease': {'id': ids['lease'], 'client_id': ids['client'], 'client_name': f'Synthetic staging QA {run[:8]} lease'},
            'tour': {'id': ids['tour'], 'name': 'Synthetic staging QA accepted tour', 'route_id': ids['route'],
                     'property_ids': [ids['property_a'], ids['property_b']], 'stop_ids': [ids['stop_a'], ids['stop_b']],
                     'property_names': ['Synthetic Harbor Office', 'Synthetic Garden Office'],
                     'projection_id': ids['projection'], 'unrelated_property_id': ids['unrelated_property']}}


def insert(cur, table, **fields):
    cur.execute(sql.SQL('insert into {} ({}) values ({})').format(
        sql.Identifier(*table.split('.')), sql.SQL(',').join(map(sql.Identifier, fields)),
        sql.SQL(',').join(sql.Placeholder() for _ in fields)), tuple(fields.values()))


def read(conn, run):
    ids = identities(run)
    with conn.cursor() as cur:
        owner(cur)
        lease = scalar(cur, 'select leases from public.v_client_lease_radar')
        lease = next((row for row in lease if row['id'] == ids['lease']), None)
        tour = scalar(cur, 'select ops.read_tour_internal_detail(%s,%s,%s)', (TENANT, ids['tour'], 'joe'))
        return {'lease': lease, 'tour': tour}


def prepare(conn, run):
    ids, result = identities(run), manifest(run)
    try:
        with conn.cursor() as cur:
            owner(cur)
            scalar(cur, 'select pg_advisory_xact_lock(hashtextextended(%s,0))', (run,))
            existing = scalar(cur, 'select count(*) from public.party where id=%s', (ids['party'],))
            if existing:
                verify(conn, run)
                conn.commit()
                return result
            joe = scalar(cur, "select id from public.actor where slug='joe'")
            insert(cur, 'public.party', id=ids['party'], kind='org', name=result['lease']['client_name'],
                   notes_path=f'fixtures/staging-journey/{run}', created_by=joe, updated_by=joe)
            insert(cur, 'public.client', id=ids['client'], party_id=ids['party'], status='past_client',
                   owner_id=joe, notes_path=f'fixtures/staging-journey/{run}', created_by=joe, updated_by=joe)
            cur.execute("insert into public.lease(id,client_id,owner_id,expiration_on,source,created_by) values(%s,%s,%s,(now() at time zone 'America/Chicago')::date+365,%s,%s)",
                        (ids['lease'], ids['client'], joe, f'synthetic-staging-journey:{run}', joe))
            stamp = scalar(cur, "select now()-interval '1 minute'")
            digest = 'sha256:' + 'a' * 64
            insert(cur, 'ops.tour_rights_receipt', id=ids['rights'], organization_tenant_id=TENANT,
                   provider=f'synthetic-{run}', sku='synthetic', policy_key=f'synthetic-{run}', receipt_version=1,
                   receipt_digest=digest, terms_url='https://example.test/synthetic', reviewed_at=stamp,
                   reviewer='synthetic-fixture', intended_use='synthetic staging test only',
                   allowed_field_classes=Jsonb(['*']), allowed_use_classes=Jsonb(['client_public_display','internal_analysis','route_planning','source_intake','canonical_fact']),
                   effective_at=stamp, status='active')
            insert(cur, 'ops.tour_source_evidence', id=ids['evidence'], organization_tenant_id=TENANT,
                   stable_locator=f'fixtures/staging-journey/{run}', evidence_class='direct_source', retrieved_at=stamp,
                   retrieval_status='read', content_digest=digest, rights_receipt_id=ids['rights'],
                   rights_provider=f'synthetic-{run}', rights_policy_key=f'synthetic-{run}', data_classification='public')
            facts = []
            for suffix, name in zip(('a','b','unrelated'), (*result['tour']['property_names'], 'Synthetic Unrelated Office')):
                prop = ids['unrelated_property'] if suffix == 'unrelated' else ids[f'property_{suffix}']
                insert(cur, 'ops.tour_property', id=prop, organization_tenant_id=TENANT, property_status='active')
                for field, value in (('display.name', name), ('display.address', f'{100 if suffix == "a" else 200} Example Way')):
                    assertion = str(uuid.uuid5(NAMESPACE, f'{run}:{suffix}:{field}'))
                    insert(cur, 'ops.tour_field_assertion', id=assertion, organization_tenant_id=TENANT,
                           property_id=prop, field_key=field, value=Jsonb(value), source_evidence_id=ids['evidence'],
                           rights_receipt_id=ids['rights'], observed_at=stamp, effective_from=stamp,
                           confidence='high', data_classification='public', review_state='reviewed')
                    if suffix != 'unrelated':
                        facts.append({'property_id': prop, 'field_assertion_id': assertion, 'display_field_key': field})
            insert(cur, 'ops.tour', id=ids['tour'], organization_tenant_id=TENANT, tour_name=result['tour']['name'],
                   tour_status='active', route_version=1, canonical_dataset_version=f'synthetic-staging-journey:{run}',
                   subject_type='client', subject_id=ids['client'], subject_bound_at=stamp)
            insert(cur, 'ops.tour_route_version', id=ids['route'], organization_tenant_id=TENANT, tour_id=ids['tour'],
                   route_version=1, start_point=Jsonb({}), end_point=Jsonb({}), routing_source='manual', created_by_actor_id='joe')
            for index, suffix in enumerate(('a','b'), 1):
                prop, stop = ids[f'property_{suffix}'], ids[f'stop_{suffix}']
                insert(cur, 'ops.tour_route_stop', id=stop, organization_tenant_id=TENANT, route_version_id=ids['route'],
                       property_id=prop, route_sequence=index, route_label=suffix.upper(), stop_state='active',
                       dwell_minutes=20, buffer_minutes=5, assertion_set_digest=digest, created_by_actor_id='joe')
                insert(cur, 'ops.tour_property_membership', organization_tenant_id=TENANT, tour_id=ids['tour'],
                       property_id=prop, route_version=1, route_sequence=index, route_label=suffix.upper(), assertion_set_digest=digest)
            detail = scalar(cur, 'select ops.read_tour_internal_detail(%s,%s,%s)', (TENANT, ids['tour'], 'joe'))
            insert(cur, 'ops.tour_route_version_acceptance', organization_tenant_id=TENANT, tour_id=ids['tour'],
                   route_version_id=ids['route'], expected_prior_route_version=0, accepted_by_actor_id='joe',
                   acceptance_digest=detail['routes'][0]['acceptance_digest'])
            insert(cur, 'ops.tour_public_projection', id=ids['projection'], organization_tenant_id=TENANT,
                   tour_id=ids['tour'], projection_version=1, route_version=1, as_of=scalar(cur, 'select now()'), facts_only=True,
                   projection_digest=digest, status='draft')
            scalar(cur, 'select ops.seal_tour_public_projection(%s,%s,%s,%s,%s)',
                   (TENANT, ids['projection'], Jsonb(facts), 'joe', digest))
            verify(conn, run)
        conn.commit()
        return result
    except BaseException:
        conn.rollback()
        raise


def verify(conn, run):
    ids, expected = identities(run), manifest(run)
    with conn.cursor() as cur:
        owner(cur)
        if scalar(cur, 'select name=%s and notes_path=%s and deleted_at is null and merged_into is null from public.party where id=%s',
                  (expected['lease']['client_name'], f'fixtures/staging-journey/{run}', ids['party'])) is not True:
            raise ValueError('fixture party identity or provenance changed')
        if scalar(cur, 'select party_id=%s and notes_path=%s from public.client where id=%s',
                  (ids['party'], f'fixtures/staging-journey/{run}', ids['client'])) is not True:
            raise ValueError('fixture client identity or provenance changed')
        if scalar(cur, 'select client_id=%s and source=%s from public.lease where id=%s',
                  (ids['client'], f'synthetic-staging-journey:{run}', ids['lease'])) is not True:
            raise ValueError('fixture lease identity or provenance changed')
        if scalar(cur, 'select canonical_dataset_version=%s and subject_id=%s from ops.tour where id=%s',
                  (f'synthetic-staging-journey:{run}', ids['client'], ids['tour'])) is not True:
            raise ValueError('fixture Tour identity or provenance changed')
        if scalar(cur, 'select stable_locator=%s from ops.tour_source_evidence where id=%s',
                  (f'fixtures/staging-journey/{run}', ids['evidence'])) is not True:
            raise ValueError('fixture evidence provenance changed')
    value = read(conn, run)
    if not value['lease'] or value['lease']['client_name'] != expected['lease']['client_name']:
        raise ValueError('fixture lease did not reach the radar')
    route = value['tour']['routes'][0]
    if route['id'] != ids['route'] or not route['accepted'] or [s['property_id'] for s in route['stops']] != expected['tour']['property_ids']:
        raise ValueError('accepted fixture route identity or order changed')
    return value


def owned_rows(run):
    ids = identities(run)
    projection = "projection_id=%s"
    grants = 'share_grant_id in (select id from ops.tour_share_grant where projection_id=%s)'
    return [
        ('public.tool_call', "verb in ('issue-tour-share-grant','rotate-tour-share-grant','revoke-tour-share-grant') and response->>'share_grant_id' in (select id::text from ops.tour_share_grant where projection_id=%s)", (ids['projection'],)),
        ('public.event', "subject_type='tour_share_grant' and subject_id in (select id from ops.tour_share_grant where projection_id=%s)", (ids['projection'],)),
        ('ops.tour_share_feedback_event', projection, (ids['projection'],)),
        ('ops.tour_share_session', grants, (ids['projection'],)),
        ('ops.tour_share_grant_revocation_receipt', grants, (ids['projection'],)),
        ('ops.tour_share_grant', projection, (ids['projection'],)),
        ('ops.tour_public_projection_seal_receipt', projection, (ids['projection'],)),
        ('ops.tour_public_projection_map_point', projection, (ids['projection'],)),
        ('ops.tour_public_projection_fact', projection, (ids['projection'],)),
        ('ops.tour_public_projection', 'id=%s', (ids['projection'],)),
        ('ops.tour_route_version_acceptance', 'route_version_id=%s', (ids['route'],)),
        ('ops.tour_property_membership', 'tour_id=%s', (ids['tour'],)),
        ('ops.tour_route_stop', 'route_version_id=%s', (ids['route'],)),
        ('ops.tour_route_version', 'id=%s', (ids['route'],)),
        ('ops.tour', 'id=%s', (ids['tour'],)),
        ('ops.tour_field_assertion', 'source_evidence_id=%s', (ids['evidence'],)),
        ('ops.tour_source_evidence', 'id=%s', (ids['evidence'],)),
        ('ops.tour_rights_receipt', 'id=%s', (ids['rights'],)),
        ('ops.tour_property', 'id in (%s,%s,%s)', (ids['property_a'], ids['property_b'], ids['unrelated_property'])),
        ('public.lease', 'id=%s', (ids['lease'],)),
        ('public.client', 'id=%s', (ids['client'],)),
        ('public.party', 'id=%s', (ids['party'],))]


def remaining(conn, run):
    with conn.cursor() as cur:
        owner(cur)
        return sum(scalar(cur, f'select count(*) from {table} where {where}', args) for table, where, args in owned_rows(run))


def cleanup(conn, run):
    try:
        with conn.cursor() as cur:
            owner(cur)
            scalar(cur, 'select pg_advisory_xact_lock(hashtextextended(%s,0))', (run,))
            if remaining(conn, run) == 0:
                conn.commit()
                return
            verify(conn, run)
            for table, where, args in owned_rows(run):
                cur.execute('select tgname from pg_trigger where tgrelid=%s::regclass and not tgisinternal and tgenabled=\'O\'', (table,))
                triggers = [row[0] for row in cur.fetchall()]
                for trigger in triggers:
                    cur.execute(sql.SQL('alter table {} disable trigger {}').format(sql.Identifier(*table.split('.')), sql.Identifier(trigger)))
                cur.execute(f'delete from {table} where {where}', args)
                for trigger in triggers:
                    cur.execute(sql.SQL('alter table {} enable trigger {}').format(sql.Identifier(*table.split('.')), sql.Identifier(trigger)))
            if remaining(conn, run):
                raise ValueError('fixture cleanup left owned rows')
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def staging_owner():
    config = json.loads((ROOT / 'ops/config/release-pipeline.v1.json').read_text())
    operation = provisioner._canonical_uuid4(config['worker']['staging_candidate_operation_id'], 'candidate operation id')
    env = provisioner.provider_environment(os.environ)
    _, _, candidate = provisioner.replacement.resolve_existing_scopes(operation, run=subprocess.run, environ=env)
    if candidate is None:
        raise ValueError('configured replacement candidate does not exist; no fallback allowed')
    owner = provisioner.provider_dsn(provisioner._local_scope(candidate), 'neondb_owner',
                                    neonctl=str(provisioner.replacement.NEONCTL), run=subprocess.run, environ=env)
    guard.validate_target(owner.value, candidate.project_id, candidate.endpoint_id)
    guard.validate_provider_binding(owner.value, candidate.project_id, candidate.endpoint_id, run=subprocess.run, environ=env)
    return owner


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        'Every action resolves the configured replacement staging project and owner '
        'connection through the staging provisioner. Reuse --run for readback and '
        'cleanup after an interrupted invocation.'))
    parser.add_argument('action', choices=('prepare', 'read', 'cleanup', 'remaining'))
    parser.add_argument('--run', required=True)
    args = parser.parse_args()
    identities(args.run)
    owner_dsn = staging_owner()
    with psycopg.connect(owner_dsn.value, connect_timeout=10) as conn:
        if args.action == 'prepare':
            result = prepare(conn, args.run)
        elif args.action == 'read':
            result = verify(conn, args.run)
        elif args.action == 'cleanup':
            cleanup(conn, args.run)
            result = {'schema': SCHEMA, 'run': args.run, 'remaining': remaining(conn, args.run)}
        else:
            result = {'schema': SCHEMA, 'run': args.run, 'remaining': remaining(conn, args.run)}
        print(json.dumps(result, default=str))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('staging journey fixture refused; verify staging owner target and fixture state (diagnostics suppressed)', file=sys.stderr)
        raise SystemExit(2)
