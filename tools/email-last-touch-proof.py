#!/usr/bin/env python3
"""Prove retained acknowledged email effects without reading a mailbox or writing records."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

MAX_REQUESTS = 1000
MAX_BYTES = 16 * 1024 * 1024
SQL = """
with requests as (
  select * from jsonb_to_recordset(%s::jsonb)
    as r(binding text, ref text, activity_id uuid)
), roles as (
  select 'client'::text subject_type,id,party_id,roster_ref ref,merged_into from public.client
  union all
  select 'lead',id,party_id,registry_ref,null::uuid from public.lead
  union all
  select 'vendor',id,party_id,vendor_ref,merged_into from public.vendor
), targets as (
  select r.*,s.subject_type,s.id subject_id,
    (s.merged_into is not null or p.merged_into is not null or p.deleted_at is not null) excluded,
    (s.id is not null and p.id is null) dangling
  from requests r left join roles s on s.ref=r.ref
  left join public.party p on p.id=s.party_id
), wanted as (
  select distinct subject_type,subject_id from targets where subject_id is not null
), eligible as (
  select a.occurred_at,t.subject_type,t.subject_id
  from public.activity a join public.activity_kind k on k.slug=a.kind
  cross join lateral (values ('client',a.client_id),('lead',a.lead_id),('vendor',a.vendor_id))
    t(subject_type,subject_id)
  join wanted w using(subject_type,subject_id)
  where k.is_contact or (a.kind='note' and a.summary='last-touch stamp (imported)')
    or (a.kind='note' and a.summary like 'Last touch carried from%%')
), latest as (
  select subject_type,subject_id,
    (max(occurred_at) at time zone 'America/Chicago')::date::text latest
  from eligible group by subject_type,subject_id
)
select t.binding,t.subject_type,t.subject_id::text,t.excluded,t.dangling,
  a.id::text activity_id,a.kind,a.occurred_at::text,a.email_identity,
  case t.subject_type when 'client' then a.client_id=t.subject_id
    when 'lead' then a.lead_id=t.subject_id when 'vendor' then a.vendor_id=t.subject_id
    else false end target_matches,
  a.email_context#>>'{lifecycle,state}' lifecycle,l.latest,v.last_touch::text
from targets t left join public.activity a on a.id=t.activity_id
left join latest l on l.subject_type=t.subject_type and l.subject_id=t.subject_id
left join public.v_last_touch v on v.subject_type=t.subject_type and v.subject_id=t.subject_id
order by t.binding
"""


def instant(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('offset_required')
    return result.astimezone(timezone.utc)


def load_private(path):
    with Path(path).open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('input_too_large')
    return json.loads(data)


def select_requests(state, account, start, end, clock):
    since, until = instant(start), instant(end)
    if not account or not since < until <= clock() or (until - since).total_seconds() > 31 * 86400:
        raise ValueError('invalid_window')
    selected = []
    for binding, request in state['requests'].items():
        if request.get('verb') != 'log-activity' or request.get('acknowledged') is not True:
            continue
        if request.get('disposition') not in (None, 'acknowledged'):
            continue
        created = datetime.fromtimestamp(request['created_at'], timezone.utc)
        if not since <= created < until:
            continue
        payload = json.loads(request['payload'])
        if payload.get('kind') not in ('email_in', 'email_out'):
            continue
        identity = payload.get('email_identity')
        if identity and identity.get('mailbox_account') != account:
            continue
        selected.append(dict(binding=binding, ref=payload.get('ref'),
                             activity_id=request.get('activity_id'), payload=payload))
        if len(selected) > MAX_REQUESTS:
            raise ValueError('request_limit')
    return selected


def prove(state, read, account, start, end, clock):
    selected = select_requests(state, account, start, end, clock)
    rows = read([{k: r[k] for k in ('binding', 'ref', 'activity_id')} for r in selected])
    by_binding = {r['binding']: r for r in rows}
    if len(by_binding) != len(rows) or set(by_binding) != {r['binding'] for r in selected}:
        raise ValueError('snapshot_population_mismatch')
    counts = Counter()
    active = {kind: set() for kind in ('client', 'lead', 'vendor')}
    corroborated = {kind: set() for kind in active}
    lifecycle = Counter()
    findings = []
    examples = {}
    for request in selected:
        row = by_binding[request['binding']]
        payload = request['payload']
        reasons = []
        if row['subject_id'] is None:
            counts['missing_targets'] += 1
            reasons.append('missing_target')
        elif row['dangling']:
            counts['dangling_targets'] += 1
            reasons.append('dangling_target')
        elif row['excluded']:
            counts['excluded'] += 1
            reasons.append('excluded_target')
        else:
            kind, subject = row['subject_type'], row['subject_id']
            active[kind].add(subject)
            mismatches = []
            if not request['activity_id'] or row['activity_id'] != request['activity_id']:
                mismatches.append('activity_missing')
            else:
                if row['target_matches'] is not True:
                    mismatches.append('target_mismatch')
                if row['kind'] != payload['kind']:
                    mismatches.append('kind_mismatch')
                if not payload.get('occurred_at'):
                    counts['unavailable_occurrences'] += 1
                    reasons.append('occurrence_unavailable')
                elif instant(row['occurred_at']) != instant(payload['occurred_at']):
                    mismatches.append('occurrence_mismatch')
            expected, actual = payload.get('email_identity'), row['email_identity']
            if not expected or not actual:
                counts['unavailable_identities'] += 1
                reasons.append('identity_unavailable')
            else:
                expected = {**expected, 'item_id': expected['item_id'].strip(), 'subject': subject}
                if actual != expected:
                    mismatches.append('source_identity_mismatch')
            if row['latest'] != row['last_touch']:
                mismatches.append('projection_mismatch')
            if row.get('lifecycle'):
                lifecycle[row['lifecycle']] += 1
            if mismatches:
                counts['mismatches'] += 1
                reasons.extend(mismatches)
            if not reasons:
                corroborated[kind].add(subject)
                counts['corroborated_requests'] += 1
                examples.setdefault(kind, hashlib.sha256(request['binding'].encode()).hexdigest())
        if reasons:
            findings.append(dict(binding=hashlib.sha256(request['binding'].encode()).hexdigest(), reasons=reasons))
    failures = sum(counts[k] for k in ('mismatches', 'unavailable_identities',
                                      'unavailable_occurrences', 'dangling_targets', 'missing_targets'))
    return dict(ok=not failures and all(corroborated.values()), window=dict(start=start, end=end),
                selected_requests=len(selected), mismatches=counts['mismatches'],
                unavailable_identities=counts['unavailable_identities'],
                unavailable_occurrences=counts['unavailable_occurrences'],
                excluded=counts['excluded'], dangling_targets=counts['dangling_targets'],
                missing_targets=counts['missing_targets'], corroborated_requests=counts['corroborated_requests'],
                active_targets={k: len(v) for k, v in active.items()},
                corroborated={k: len(v) for k, v in corroborated.items()},
                unproven_examples=[k for k, v in corroborated.items() if not v],
                lifecycle=dict(lifecycle), examples=examples, findings=findings)


def database_read(selected):
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row, connect_timeout=5,
                          options='-c default_transaction_read_only=on -c statement_timeout=20000 -c lock_timeout=2000') as connection:
        with connection.cursor() as cursor:
            cursor.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            cursor.execute(SQL, (json.dumps(selected),))
            return cursor.fetchall()


def main(argv=None, *, clock=lambda: datetime.now(timezone.utc), read=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--requests', required=True)
    parser.add_argument('--account', required=True)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--fixture', help='Synthetic snapshot JSON, without a database connection')
    args = parser.parse_args(argv)
    try:
        state = load_private(args.requests)
        port = read or database_read
        if args.fixture:
            snapshot = load_private(args.fixture)['rows']
            port = lambda _: snapshot
        result = prove(state, port, args.account, args.start, args.end, clock)
    except Exception as exc:
        print(json.dumps(dict(ok=False, reason='proof_unavailable', error_type=type(exc).__name__)))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
