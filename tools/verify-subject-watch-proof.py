#!/usr/bin/env python3
"""Capture bounded read-only watch counts and verify destination, replay and unwatch deltas."""
import argparse
import hashlib
import json
from pathlib import Path
from uuid import UUID

import psycopg


def capture(database, watched, unwatched, actor):
    if watched == unwatched:
        raise ValueError('control must be a different deal')
    with psycopg.connect(database, options='-c default_transaction_read_only=on -c statement_timeout=5000') as db:
        with db.transaction():
            db.execute('set transaction isolation level repeatable read')
            rows = db.execute("""
                select d.id, exists(select 1 from ops.subject_watch sw
                  where sw.subject_type='deal' and sw.subject_id=d.id and sw.actor_id=a.id),
                  (select count(*) from ops.notification n where n.subject_type='deal'
                    and n.subject_ref=d.id::text and n.recipient_actor=a.id
                    and n.event_source='event' and n.dedupe_key like 'subject-watch:%%'),
                  (select count(*) from ops.subject_watch_event_receipt r
                    join public.event e on e.id=r.event_id
                    where e.subject_type='deal' and e.subject_id=d.id
                    and e.organization_tenant_id='carr-internal'),
                  (select count(*) from ops.notification n where n.subject_type='deal'
                    and n.subject_ref=d.id::text and n.recipient_actor=a.id
                    and n.event_source='event' and n.dedupe_key like 'subject-watch:%%'
                    and n.deep_link='/deals?deal=' || d.id::text)
                from public.deal d join public.client c on c.id=d.client_id and c.merged_into is null
                join public.party p on p.id=c.party_id and p.deleted_at is null and p.merged_into is null
                cross join public.actor a
                where d.id in (%s,%s) and a.slug=%s
                """, (watched, unwatched, actor)).fetchall()
    by_id = {str(row[0]): row[1:] for row in rows}
    if len(by_id) != 2 or by_id[str(unwatched)][0]:
        raise ValueError('requires two live deals and an unwatched control')
    return {'selection': hashlib.sha256(f'{watched}/{unwatched}/{actor}'.encode()).hexdigest(),
            'subscribed': by_id[str(watched)][0],
            'watched': list(by_id[str(watched)][1:]), 'unwatched': list(by_id[str(unwatched)][1:])}


def verify(before, after, replay, removed, suppressed):
    samples = (before, after, replay, removed, suppressed)
    for sample in samples:
        if set(sample) != {'selection', 'subscribed', 'watched', 'unwatched'}:
            raise ValueError('invalid snapshot')
        if not isinstance(sample['selection'], str) or type(sample['subscribed']) is not bool:
            raise ValueError('invalid selection or subscription')
        for key in ('watched', 'unwatched'):
            if not isinstance(sample[key], list) or len(sample[key]) != 3 or any(
                    type(n) is not int or n < 0 for n in sample[key]):
                raise ValueError('invalid counts')
    if len({sample['selection'] for sample in samples}) != 1:
        raise ValueError('different selections')
    if [sample['subscribed'] for sample in samples] != [True, True, True, False, False]:
        raise ValueError('invalid subscription sequence')
    delta = lambda a, b, key: [y-x for x,y in zip(a[key],b[key])]
    counts = {'watched': delta(before,after,'watched')[0],
              'unwatched': delta(before,after,'unwatched')[0],
              'replay': delta(after,replay,'watched')[0],
              'suppressed': delta(removed,suppressed,'watched')[0]}
    if (delta(before,after,'watched') != [1,1,1] or
            delta(before,after,'unwatched') != [0,1,0] or replay != after or
            any(delta(replay,removed,key) != [0,0,0] for key in ('watched','unwatched')) or
            delta(removed,suppressed,'watched') != [0,1,0] or
            delta(removed,suppressed,'unwatched') != [0,0,0]):
        raise ValueError('watch proof failed ' + json.dumps(counts, sort_keys=True))
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    take = sub.add_parser('capture')
    take.add_argument('--database', default='', help='libpq connection string; defaults to configured service')
    take.add_argument('--watched', type=UUID, required=True)
    take.add_argument('--unwatched', type=UUID, required=True)
    take.add_argument('--actor', choices=('joe','dell'), required=True)
    take.add_argument('--output', type=Path, required=True)
    check = sub.add_parser('verify')
    for name in ('before','after','replay','removed','suppressed'):
        check.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'capture':
            sample = capture(args.database,args.watched,args.unwatched,args.actor)
            args.output.write_text(json.dumps(sample)+'\n')
            print('watch counts captured')
        else:
            samples=[]
            for path in (args.before,args.after,args.replay,args.removed,args.suppressed):
                if path.stat().st_size > 4096:
                    raise ValueError('snapshot exceeds 4 KiB')
                samples.append(json.loads(path.read_text()))
            print(json.dumps(verify(*samples),sort_keys=True))
    except ValueError as error:
        print(str(error))
        return 1
    except (OSError, psycopg.Error):
        print('watch proof refused')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
