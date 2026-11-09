#!/usr/bin/env python3
"""Observe pending watch receipts without writes or notification delivery claims."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import signal
import subprocess
import sys
from uuid import UUID

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
from lib.release_reader import read
from proof_release import release_revision

MAX_BYTES = 1048576
PARSER = """
import {parseSubjectWatchChanges} from './mcp-server/contracts/subject-watch-change.v1.js';
let body='';for await(const chunk of process.stdin)body+=chunk;
console.log(JSON.stringify(parseSubjectWatchChanges(JSON.parse(body))));
"""


class InsufficientEvidence(ValueError):
    pass


def response(value):
    result = subprocess.run(['node', '--input-type=module', '-e', PARSER], cwd=ROOT,
                            input=json.dumps(value), text=True, capture_output=True, timeout=5)
    if result.returncode:
        raise InsufficientEvidence('watch_response_contract_refused')
    parsed = json.loads(result.stdout)
    if parsed['has_more']:
        raise InsufficientEvidence('watch_page_incomplete')
    return parsed


def capture(database, watched, subscription, actor):
    with psycopg.connect(database, options='-c default_transaction_read_only=on -c statement_timeout=5000') as db:
        with db.transaction():
            db.execute('set transaction isolation level repeatable read')
            db.execute("select set_config('carr.acting_actor_slug',%s,true),"
                       "set_config('carr.verified_human_actor_slug',%s,true)", (actor, actor))
            db.execute('set local role carr_authority_' + actor)
            page = db.execute("select ops.read_subject_watch_changes('deal',%s,%s,null,100)",
                              (watched, subscription)).fetchone()[0]
            db.execute('reset role')
            count = db.execute("select count(*) from public.event where subject_type='deal' and subject_id=%s",
                               (watched,)).fetchone()[0]
    return {'selection': hashlib.sha256(f'{watched}/{subscription}/{actor}'.encode()).hexdigest(),
            'source_count': count, 'response': response(page)}


def load(path):
    if path.stat().st_size > MAX_BYTES:
        raise InsufficientEvidence('fixture_too_large')
    return json.loads(path.read_text())


def verify(before, after, immaterial, replay):
    samples = (before, after, immaterial, replay)
    pages = []
    for sample in samples:
        if (set(sample) != {'selection', 'source_count', 'response'}
                or not re.fullmatch('[0-9a-f]{64}', sample['selection'])
                or type(sample['source_count']) is not int or sample['source_count'] < 0):
            raise InsufficientEvidence('invalid_snapshot')
        pages.append(response(sample['response']))
    if len({sample['selection'] for sample in samples}) != 1:
        raise InsufficientEvidence('different_selections')
    rows = [{row['id']: row for row in page['changes']} for page in pages]
    if any(len(row) != len(page['changes']) for row, page in zip(rows, pages)):
        raise InsufficientEvidence('duplicate_receipt_identity')
    new = set(rows[1]) - set(rows[0])
    if len(new) != 1:
        raise AssertionError('meaningful_change_requires_one_receipt')
    receipt = rows[1][next(iter(new))]
    if receipt['disposition'] != 'pending':
        raise AssertionError('meaningful_receipt_must_be_pending')
    if receipt['category'] != 'material_changed' or not receipt['changes']:
        raise AssertionError('material_evidence_required')
    if any(rows[1].get(key) != value for key, value in rows[0].items()):
        raise AssertionError('prior_receipts_changed')
    if rows[2] != rows[1]:
        raise AssertionError('immaterial_change_added_or_changed_receipts')
    if replay != immaterial:
        raise AssertionError('exact_replay_changed_observation')
    counts = [sample['source_count'] for sample in samples]
    if counts != [counts[0], counts[0]+1, counts[0]+2, counts[0]+2]:
        raise AssertionError('source_write_or_replay_not_observed')
    return {'meaningful_pending': 1, 'immaterial_pending': 0, 'replay_added': 0}


def containing_release(merge_sha, fixture):
    if not re.fullmatch('[0-9a-f]{40}', merge_sha):
        raise InsufficientEvidence('invalid_merge_sha')
    if fixture:
        release = load(fixture)
    else:
        reply = read('https://api.doctorcre.com/release', timeout=5, max_bytes=MAX_BYTES)
        if reply.status != 200 or len(reply.body) > MAX_BYTES:
            raise InsufficientEvidence('release_unavailable')
        release = json.loads(reply.body)
    revision = release_revision(release)
    check = subprocess.run(['git', 'merge-base', '--is-ancestor', merge_sha, revision],
                           cwd=ROOT, capture_output=True, timeout=5)
    if check.returncode:
        raise InsufficientEvidence('release_does_not_contain_merge')
    return revision


def deadline(*_):
    raise TimeoutError('proof_deadline')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    take = sub.add_parser('capture')
    take.add_argument('--database', default='', help='libpq connection string; prefer a configured service')
    take.add_argument('--watched', type=UUID, required=True)
    take.add_argument('--subscription', type=UUID, required=True)
    take.add_argument('--actor', choices=('joe', 'dell'), required=True)
    take.add_argument('--output', type=Path, required=True)
    check = sub.add_parser('verify')
    for name in ('before', 'after', 'immaterial', 'replay'):
        check.add_argument('--'+name, type=Path, required=True)
    check.add_argument('--merge-sha', required=True)
    check.add_argument('--release-fixture', type=Path, help='offline test only; never proves deployment')
    args = parser.parse_args()
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(30)
    try:
        if args.command == 'capture':
            args.output.write_text(json.dumps(capture(args.database, args.watched, args.subscription, args.actor))+'\n')
            print(json.dumps({'state': 'captured'}))
        else:
            revision = containing_release(args.merge_sha, args.release_fixture)
            counts = verify(*(load(path) for path in (args.before, args.after, args.immaterial, args.replay)))
            print(json.dumps({'state': 'passed', 'release_sha': revision,
                              'offline_fixture': bool(args.release_fixture), **counts}))
        return 0
    except AssertionError as error:
        print(json.dumps({'state': 'assertion_failure', 'reason': str(error)}))
        return 1
    except (ValueError, KeyError, TypeError, OSError, TimeoutError, subprocess.SubprocessError, psycopg.Error):
        print(json.dumps({'state': 'insufficient_evidence'}))
        return 78
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    raise SystemExit(main())
