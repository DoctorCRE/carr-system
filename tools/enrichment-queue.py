#!/usr/bin/env python3
"""Plan weekly research slices from a read-only canonical snapshot."""
from __future__ import annotations

from datetime import datetime
from typing import Any


def instant(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


def plan(lane: str, data: dict[str, Any]) -> dict[str, Any]:
    if lane not in {'contact', 'deal-history'}:
        raise ValueError('unknown queue lane')
    count = data['weekly_subject_count']
    if type(count) is not int or count < 0:
        raise ValueError('weekly subject count must be a nonnegative integer')
    cap = 40 if lane == 'contact' else 15 if count >= 30 else 25
    parties = {p['id']: p for p in data['parties']}
    subjects = {kind: {r['id']: r for r in data[table]}
                for kind, table in [('vendor', 'vendors'), ('lead', 'leads'), ('client', 'clients')]}
    candidates: list[tuple[Any, ...]] = []
    non_contacts = set()

    def add(tier, order, kind, subject_id, reason):
        party_id = subject_id if kind == 'party' else subjects[kind].get(subject_id, {}).get('party_id')
        party = parties.get(party_id)
        if party and party.get('contact_state') != 'do_not_contact':
            candidates.append((tier, order, kind, subject_id, reason))

    if lane == 'contact':
        for row in data['expired']:
            kind, subject_id = row['subject_type'], row['subject_id']
            if kind not in {'party', 'vendor', 'lead', 'client'}:
                non_contacts.add((kind, subject_id))
                continue
            add(0, (not row.get('past_age_floor', False), -row.get('subject_touches', 0),
                    instant(row['observed_at'])), kind, subject_id, 'reverify')
        for vendor in data['vendors']:
            party = parties.get(vendor['party_id'], {})
            if vendor['disposition'] != 'active':
                continue
            if vendor.get('category_slug') is None:
                level = vendor.get('relationship_level')
                add(1, (not vendor.get('is_target', False), level is None, -(level or 0), party.get('name', '')),
                    'vendor', vendor['id'], 'missing_type')
            elif party.get('city') is None or party.get('county') is None:
                add(2, (party.get('name', ''),), 'vendor', vendor['id'], 'missing_location')
            elif not vendor.get('verticals'):
                add(3, (party.get('name', ''),), 'vendor', vendor['id'], 'missing_verticals')
        for kind, table in [('lead', 'leads'), ('client', 'clients')]:
            for row in data[table]:
                party = parties.get(row['party_id'], {})
                if (any(party.get(f) is None for f in ('title', 'org_id', 'email'))
                        or (party.get('phone') is None and party.get('cell') is None)):
                    add(4, (instant(row['created_at']),), kind, row['id'], 'missing_contact')
    else:
        verified = {(r['subject_type'], r['subject_id']) for r in data['verified']}
        imported = {d['id']: d for d in data['deals'] if d['salesforce_id'] is not None}
        for client in data['clients']:
            subject_id = client['id']
            dates = [d['created_at'] for d in imported.values() if d['client_id'] == subject_id]
            if ('client', subject_id) not in verified:
                if dates:
                    add(1, (-max(map(instant, dates)),), 'client', subject_id, 'salesforce_client')
                elif client['status'] in {'active_deal', 'engaged'}:
                    add(2, (-instant(client['created_at']),), 'client', subject_id, 'pipeline_client')
            if client['status'] is None or client['roster_ref'] is None:
                add(4, (-instant(client['created_at']),), 'client', subject_id, 'identity_anomaly')
        for participant in data['participants']:
            subject_id = participant['party_id']
            deal = imported.get(participant['deal_id'])
            if deal and subject_id and ('party', subject_id) not in verified:
                add(3, (-instant(deal['created_at']),), 'party', subject_id, 'counterparty')
    selected: dict[tuple[str, str], dict[str, str]] = {}
    for _, _, kind, subject_id, reason in sorted(candidates):
        selected.setdefault((kind, subject_id), dict(subject_type=kind, subject_id=subject_id, reason=reason))
    rows = list(selected.values())
    return dict(selected=rows[:cap], remaining=max(0, len(rows) - cap), cap=cap, trimmed=cap == 15,
                non_contact_expired=[dict(subject_type=k, subject_id=i) for k, i in sorted(non_contacts)])


SNAPSHOT_SQL = """
select jsonb_build_object(
  'parties', (select coalesce(jsonb_agg(to_jsonb(p)), '[]') from
    (select id, name, contact_state, city, county, title, org_id, email, phone, cell
       from party where merged_into is null and deleted_at is null) p),
  'vendors', (select coalesce(jsonb_agg(to_jsonb(v)), '[]') from
    (select id, party_id, disposition, category_slug, is_target, relationship_level, verticals
       from vendor where merged_into is null) v),
  'leads', (select coalesce(jsonb_agg(to_jsonb(l)), '[]') from
    (select id, party_id, created_at from lead) l),
  'clients', (select coalesce(jsonb_agg(to_jsonb(c)), '[]') from
    (select id, party_id, created_at, status, roster_ref from client where merged_into is null) c),
  'deals', (select coalesce(jsonb_agg(to_jsonb(d)), '[]') from
    (select id, client_id, salesforce_id, created_at from deal where salesforce_id is not null) d),
  'participants', (select coalesce(jsonb_agg(to_jsonb(dp)), '[]') from
    (select deal_id, party_id from deal_participant) dp),
  'expired', (select coalesce(jsonb_agg(to_jsonb(e)), '[]') from
    (select subject_type, subject_id, observed_at, past_age_floor, subject_touches
       from v_expired_verification) e),
  'verified', (select coalesce(jsonb_agg(to_jsonb(f)), '[]') from
    (select distinct subject_type, subject_id from record_flag
      where kind = 'verified' and (expires_on is null or expires_on >= current_date)) f),
  'weekly_subject_count', (select count(distinct subject_id) from v_record_flag_subject
    where observed_at >= (date_trunc('week', now() at time zone 'America/Chicago')
                          at time zone 'America/Chicago') and observed_at <= now())
)
"""


def main() -> None:
    import argparse
    import json
    import os
    import psycopg

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('lane', choices=['contact', 'deal-history'])
    args = parser.parse_args()
    with psycopg.connect(os.environ['DATABASE_URL']) as conn:
        conn.execute('set transaction read only')
        row = conn.execute(SNAPSHOT_SQL).fetchone()
        if row is None:
            raise RuntimeError('canonical snapshot unavailable')
        data = row[0]
        print(json.dumps(plan(args.lane, data)))


if __name__ == '__main__':
    main()
