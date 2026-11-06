#!/usr/bin/env python3
"""Reconcile an immutable email-touch journal without sending or writing."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sys
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.release_reader import read as read_http
ROLES = {'client':'client', 'lead':'prospect', 'vendor':'vendor'}
FIELDS = {'source_id', 'source_version', 'original_key', 'role', 'target_type', 'target_id', 'occurred_at'}
COUNTS = ('expected', 'applied', 'missing', 'dangling', 'conflicting', 'tombstoned',
          'out_of_window', 'extra', 'orphan')


class Refused(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise Refused(reason)


def timestamp(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(stamp.tzinfo is not None, 'invalid_journal')
    return stamp.astimezone(timezone.utc)


def load_journal(path, reference, digest):
    require(path is not None and path.is_file(), 'missing_journal')
    require(isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest)
            and reference == 'sha256:'+digest, 'invalid_journal_reference')
    with path.open('rb') as stream:
        raw = stream.read(1048577)
    require(len(raw) <= 1048576, 'journal_size')
    require(hashlib.sha256(raw).hexdigest() == digest, 'digest_mismatch')
    journal = json.loads(raw)
    required = {'schema', 'principal_slug', 'actor_id', 'actor_slug', 'tenant', 'release_sha',
                'start', 'end', 'expected_count', 'expected'}
    require(isinstance(journal, dict) and required <= journal.keys()
            and journal.keys() <= required | {'recorded_start', 'recorded_end'}, 'invalid_journal')
    require(journal['schema'] == 'email-touch-expectations.v1'
            and journal['tenant'] == 'carr-internal', 'invalid_journal')
    for field in ('principal_slug', 'actor_slug'):
        require(isinstance(journal[field], str) and 0 < len(journal[field]) <= 100, 'invalid_journal')
    UUID(journal['actor_id'])
    require(re.fullmatch('[0-9a-f]{40}', journal['release_sha']), 'invalid_journal')
    require(timestamp(journal['start']) < timestamp(journal['end']), 'invalid_journal')
    require(('recorded_start' in journal) == ('recorded_end' in journal), 'invalid_journal')
    if 'recorded_start' in journal:
        require(timestamp(journal['recorded_start']) < timestamp(journal['recorded_end']), 'invalid_journal')
    expected = journal['expected']
    require(isinstance(expected, list) and 1 <= len(expected) <= 500, 'invalid_journal')
    require(type(journal['expected_count']) is int and journal['expected_count'] == len(expected), 'count_mismatch')
    keys, effects = set(), set()
    for item in expected:
        require(isinstance(item, dict) and item.keys() == FIELDS, 'invalid_journal')
        for field, cap in (('source_id', 500), ('source_version', 200)):
            require(isinstance(item[field], str) and 0 < len(item[field]) <= cap, 'invalid_journal')
        require(item['target_type'] in ROLES and item['role'] == ROLES[item['target_type']], 'invalid_journal')
        for field in ('original_key', 'target_id'):
            require(str(UUID(item[field])) == item[field], 'invalid_journal')
        stamp = timestamp(item['occurred_at']).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        require(item['occurred_at'] == stamp, 'invalid_journal')
        keys.add(item['original_key'])
        effects.add(tuple(item[k] for k in ('source_id', 'source_version', 'target_type', 'target_id')))
    require(len(keys) == len(expected) == len(effects), 'duplicate_expectation')
    return journal


def begin_read_only(conn):
    conn.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
    conn.execute("SET LOCAL statement_timeout = '10s'")
    conn.execute("SET LOCAL lock_timeout = '2s'")
    conn.execute("SET LOCAL idle_in_transaction_session_timeout = '15s'")


def acquire(conn, journal):
    from psycopg.types.json import Jsonb
    actor = conn.execute('select id::text from actor where id=%s and slug=%s',
                         (journal['actor_id'], journal['actor_slug'])).fetchall()
    require(len(actor) == 1, 'actor_mismatch')
    params = dict(expected=Jsonb(journal['expected']), actor=journal['actor_id'],
                  tenant=journal['tenant'], start=journal['start'], end=journal['end'])
    events = conn.execute("""
with expected as (select value x from jsonb_array_elements(%(expected)s::jsonb))
select e.idempotency_key as key, e.new_value#>'{email_application,binding}' as binding,
 e.recorded_at, a.id::text as activity_id, a.kind, a.occurred_at,
 a.client_id::text, a.lead_id::text, a.vendor_id::text,
 a.email_context->>'source_activity_id' as source_reference, s.id::text as source_id,
 coalesce(s.email_context,a.email_context)#>>'{lifecycle,state}' as lifecycle,
 exists(select 1 from v_ref_index r where
   r.subject_type=e.new_value#>>'{email_application,binding,target_type}'
   and r.subject_id::text=e.new_value#>>'{email_application,binding,target_id}') as target_exists,
 exists(select 1 from v_ref_index r join party p on p.id=r.party_id where
   r.subject_type=e.new_value#>>'{email_application,binding,target_type}'
   and r.subject_id::text=e.new_value#>>'{email_application,binding,target_id}'
   and not r.merged and p.deleted_at is null) as target_live
from event e
left join activity a on a.id::text=e.new_value->>'activity'
left join activity s on s.id::text=a.email_context->>'source_activity_id'
where e.verb='log-activity' and e.actor_id=%(actor)s and e.organization_tenant_id=%(tenant)s
 and (exists(select 1 from expected where x->>'original_key'=e.idempotency_key)
 or (e.new_value ? 'email_application' and
   (e.new_value#>>'{email_application,binding,occurred_at}')::timestamptz >= %(start)s::timestamptz
   and (e.new_value#>>'{email_application,binding,occurred_at}')::timestamptz < %(end)s::timestamptz
   and exists(select 1 from expected where
     x->>'target_type'=e.new_value#>>'{email_application,binding,target_type}'
     and x->>'target_id'=e.new_value#>>'{email_application,binding,target_id}')))
limit 1001
""", params).fetchall()
    require(len(events) <= 1000, 'row_cap')
    activities = conn.execute("""
with expected as (select value x from jsonb_array_elements(%(expected)s::jsonb))
select a.id::text as activity_id from activity a
where a.actor_id=%(actor)s and a.kind in ('email_in','email_out')
 and a.occurred_at >= %(start)s::timestamptz and a.occurred_at < %(end)s::timestamptz
 and exists(select 1 from expected where x->>'target_id'=case x->>'target_type'
   when 'client' then a.client_id::text when 'lead' then a.lead_id::text when 'vendor' then a.vendor_id::text end)
limit 1001
""", params).fetchall()
    require(len(events) + len(activities) <= 1000, 'row_cap')
    return events, activities


def reconcile(journal, events, activities):
    counts = dict.fromkeys(COUNTS, 0)
    counts['expected'] = len(journal['expected'])
    by_key = {}
    for event in events:
        by_key.setdefault(event['key'], []).append(event)
    keys = {x['original_key'] for x in journal['expected']}
    counts['extra'] = sum(e['key'] not in keys for e in events)
    for effect in journal['expected']:
        matches = by_key.get(effect['original_key'], [])
        if not timestamp(journal['start']) <= timestamp(effect['occurred_at']) < timestamp(journal['end']):
            disposition = 'out_of_window'
        elif not matches or all(e['binding'] is None for e in matches):
            disposition = 'missing'
        elif len(matches) != 1 or matches[0]['binding'] != effect:
            disposition = 'conflicting'
        else:
            event = matches[0]
            if 'recorded_start' in journal and not (
                    timestamp(journal['recorded_start']) <= event['recorded_at'] < timestamp(journal['recorded_end'])):
                disposition = 'out_of_window'
            elif not event['activity_id'] or not event['target_exists'] or (
                    event['source_reference'] is not None and event['source_id'] is None):
                disposition = 'dangling'
            elif (event[effect['target_type']+'_id'] != effect['target_id']
                  or event['occurred_at'] != timestamp(effect['occurred_at'])
                  or event['kind'] not in ('email_in', 'email_out')):
                disposition = 'conflicting'
            elif not event['target_live'] or event['lifecycle'] not in (None, 'live'):
                disposition = 'tombstoned'
            else:
                disposition = 'applied'
        counts[disposition] += 1
    linked = {e['activity_id'] for e in events if e['binding'] is not None}
    counts['orphan'] = sum(a['activity_id'] not in linked for a in activities)
    for field, reason in (('out_of_window','out_of_window'), ('missing','missing_lineage'),
                          ('conflicting','conflicting'), ('dangling','dangling'),
                          ('tombstoned','tombstoned'), ('extra','extra_touch'), ('orphan','orphan_touch')):
        if counts[field]:
            return reason, counts
    require(counts['applied'] > 0 and counts['applied'] == counts['expected'] == len(events), 'count_mismatch')
    return 'matched', counts


def live_release():
    try:
        response = read_http('https://api.doctorcre.com/release', timeout=5, max_bytes=65536)
        require(response.status == 200, 'release_unavailable')
        data = response.body
        require(len(data) <= 65536, 'release_unavailable')
        release = json.loads(data)
        require(release.get('ok') is True and release['env']['value'] == 'production', 'release_unavailable')
        sha = release['git_sha']['value']
        require(isinstance(sha, str) and re.fullmatch('[0-9a-f]{40}', sha), 'release_unavailable')
        return sha
    except Refused:
        raise
    except Exception:
        raise Refused('release_unavailable') from None


def evaluate(args):
    journal = load_journal(args.journal, args.journal_ref, args.journal_sha256)
    if args.fixture_socket:
        socket = args.fixture_socket.resolve()
        require(socket.is_dir() and socket.name.startswith('email-proof-')
                and socket.stat().st_uid == os.getuid()
                and (socket / '.s.PGSQL.55474').exists(), 'invalid_fixture')
        dsn = dict(host=str(socket), port=55474, dbname='postgres')
        principal, release = args.fixture_principal, args.fixture_release_sha
    else:
        require(args.fixture_principal is None and args.fixture_release_sha is None, 'invalid_fixture')
        from lib.local_principal import local_actor_slug
        principal = local_actor_slug()
        require(bool(os.environ.get('DATABASE_URL')), 'transport_unavailable')
        dsn = dict(conninfo=os.environ['DATABASE_URL'])
        release = live_release()
    require(principal == journal['principal_slug'], 'principal_mismatch')
    require(release == journal['release_sha'], 'release_mismatch')
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(**dsn, connect_timeout=5, autocommit=True, row_factory=dict_row) as conn:
        begin_read_only(conn)
        events, activities = acquire(conn, journal)
        result = reconcile(journal, events, activities)
        conn.rollback()
    return result


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Refused('invalid_arguments')


def deadline(_signum, _frame):
    raise Refused('deadline')


def main(argv=None):
    mode, counts = 'production', dict.fromkeys(COUNTS, 0)
    try:
        parser = Parser(description=__doc__)
        parser.add_argument('--journal', type=Path)
        parser.add_argument('--journal-ref')
        parser.add_argument('--journal-sha256')
        parser.add_argument('--fixture-socket', type=Path)
        parser.add_argument('--fixture-principal')
        parser.add_argument('--fixture-release-sha')
        args = parser.parse_args(argv)
        mode = 'fixture' if args.fixture_socket else 'production'
        signal.signal(signal.SIGALRM, deadline)
        signal.alarm(60)
        reason, counts = evaluate(args)
    except Refused as error:
        reason = str(error)
    except (ValueError, TypeError, KeyError, AttributeError):
        reason = 'invalid_journal'
    except Exception:
        reason = 'acquisition_failed'
    finally:
        signal.alarm(0)
    print(json.dumps(dict(schema='email-touch-cohort-proof.v1', mode=mode,
                         verdict='proven' if reason == 'matched' else 'refused', reason=reason, counts=counts)))
    return 0 if reason == 'matched' else 1


if __name__ == '__main__':
    sys.exit(main())
