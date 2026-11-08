#!/usr/bin/env python3
"""Verify scheduled WATCH delivery on a containing release without production writes."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from uuid import UUID
import psycopg
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.release_reader import read
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from proof_release import release_revision


class InsufficientEvidence(Exception):
    """The controlled delivery evidence is unavailable."""


def containing(expected, observed, remaining):
    if not re.fullmatch(r'[0-9a-f]{40}', observed or ''):
        return False
    if expected == observed:
        return True
    result = subprocess.run(['git', 'merge-base', '--is-ancestor', expected, observed],
                            capture_output=True, timeout=max(.1, remaining))
    return result.returncode == 0


def capture(database, watch, remaining):
    with psycopg.connect(database, connect_timeout=max(1, int(remaining)),
                         options='-c default_transaction_read_only=on -c statement_timeout=3000') as db:
        with db.transaction():
            db.execute('set transaction isolation level repeatable read')
            row = db.execute("""
              select jsonb_build_object(
                'schema_version','subject-watch-scheduled-proof.v1','watch_id',w.id,
                'clock_receipts',(select count(*) from ops.subject_watch_change c where c.watch_generation_id=w.id
                  and c.category='lease_threshold_crossed' and c.disposition='emitted'),
                'notifications',(select count(*) from ops.notification n
                  join public.event e on e.id=n.event_ref and e.verb='subject-watch-scheduled-sweep'
                  where n.recipient_actor=w.actor_id and n.dedupe_key like 'subject-watch:'||w.id||':%'),
                'first_delivery_at',(select min(e.occurred_at) from ops.subject_watch_change c
                  join ops.notification n on n.id=c.notification_id join public.event e on e.id=n.event_ref
                  where c.watch_generation_id=w.id and c.category='lease_threshold_crossed'),
                'last_success_at',d.last_success_at,
                'scheduled_at',(select scheduled_at from ops.subject_watch_sweep where singleton),
                'business_mutations',(select count(*) from public.event e where e.recorded_at>=w.subscribed_at
                  and ((e.subject_type=w.subject_type and e.subject_id=w.subject_id)
                    or (e.subject_type='deal' and e.subject_id=l.deal_id))),
                'explicit_expiration',l.expiration_on,
                'pending',(select count(*) from ops.subject_watch_change c
                  where c.watch_generation_id=w.id and c.disposition='pending'))
              from ops.subject_watch_v2_subscription w join public.lease l on w.subject_type='lease' and l.id=w.subject_id
              join ops.subject_watch_delivery d on d.watch_id=w.id
              where w.id=%s and w.state='watching'
              """, (watch,)).fetchone()
    if not row:
        raise InsufficientEvidence('No active seeded lease watch was observed.')
    return row[0]


def verify(sample):
    if sample.get('schema_version') != 'subject-watch-scheduled-proof.v1':
        raise InsufficientEvidence('The scheduled proof contract is unavailable.')
    if not all(sample.get(k) for k in ('first_delivery_at','last_success_at','scheduled_at')):
        raise InsufficientEvidence('A scheduled delivery and later duplicate sweep are required.')
    stamp = lambda key: datetime.fromisoformat(sample[key].replace('Z', '+00:00'))
    if stamp('last_success_at') < stamp('first_delivery_at') or stamp('scheduled_at') <= stamp('first_delivery_at'):
        raise InsufficientEvidence('No later successful duplicate sweep was observed.')
    expected = {'clock_receipts': 1, 'notifications': 1, 'business_mutations': 0, 'pending': 0}
    for key, value in expected.items():
        if type(sample.get(key)) is not int or sample[key] != value:
            raise AssertionError(f'{key} expected {value}, observed {sample.get(key)}')
    if not sample.get('explicit_expiration'):
        raise AssertionError('The lease requires an explicit selected expiration.')
    return expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-merge', required=True)
    parser.add_argument('--watch-id', type=UUID, required=True)
    parser.add_argument('--database', default='')
    parser.add_argument('--release-url', default='https://api.doctorcre.com/release')
    parser.add_argument('--fixture', type=Path)
    parser.add_argument('--deadline-seconds', type=float, default=15)
    args = parser.parse_args()
    deadline = time.monotonic() + min(30, max(1, args.deadline_seconds))
    try:
        if not re.fullmatch(r'[0-9a-f]{40}', args.expected_merge):
            raise InsufficientEvidence('The expected merge requires a full commit SHA.')
        fixture = json.loads(args.fixture.read_text()) if args.fixture else None
        if fixture:
            release = fixture['release']
        else:
            response = read(args.release_url, timeout=deadline-time.monotonic(), max_bytes=65536)
            if response.status != 200:
                raise InsufficientEvidence(f'Release HTTP {response.status}')
            release = json.loads(response.body)
        if not containing(args.expected_merge, release_revision(release), deadline-time.monotonic()):
            raise InsufficientEvidence('The release does not prove containment of the expected merge.')
        sample = fixture['sample'] if fixture else capture(args.database,args.watch_id,deadline-time.monotonic())
        if sample.get('watch_id') != str(args.watch_id):
            raise AssertionError('The proof selected a different watch generation.')
        result = verify(sample)
        if time.monotonic() > deadline:
            raise InsufficientEvidence('The total proof deadline expired.')
        print(json.dumps({'status':'pass','counts':result}))
        return 0
    except AssertionError as error:
        print(json.dumps({'status':'assertion_failure','reason':str(error)}))
        return 1
    except (InsufficientEvidence,OSError,ValueError,KeyError,TypeError,psycopg.Error,subprocess.TimeoutExpired) as error:
        print(json.dumps({'status':'insufficient_evidence','reason':str(error)}))
        return 78


if __name__ == '__main__':
    sys.exit(main())
