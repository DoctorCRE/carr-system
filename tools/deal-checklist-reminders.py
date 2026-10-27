#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.record_call import call_verb


def failure_code(result):
    code = result.error
    return code if code and re.fullmatch(r'[a-z][a-z0-9_]{0,79}', code) else result.kind


def agenda_ids(reply):
    if (not isinstance(reply, dict) or reply.get('schema_version') != 'home-agenda.v1'
            or not isinstance(reply.get('deals'), list) or len(reply['deals']) > 1000):
        raise ValueError('invalid_agenda_reply')
    ids = []
    for deal in reply['deals']:
        if not isinstance(deal, dict) or not isinstance(deal.get('id'), str):
            raise ValueError('invalid_agenda_reply')
        ident = str(uuid.UUID(deal['id']))
        if ident != deal['id'].lower():
            raise ValueError('invalid_agenda_reply')
        ids.append(ident)
    return sorted(set(ids))


def main(argv=None, *, call=call_verb):
    parser = argparse.ArgumentParser(description='Queue due deal checklist reminders through the record layer.')
    parser.add_argument('--apply', action='store_true', help='Queue reminders. The default only reads the agenda.')
    args = parser.parse_args(argv)
    report = {'schema_version': 'checklist-reminder-sweep.v1', 'apply': args.apply,
              'deals': 0, 'queued': 0, 'deduplicated': 0, 'skipped': 0, 'failed': 0, 'errors': []}
    agenda = call('read-home-agenda', {})
    try:
        ids = agenda_ids(agenda.reply) if agenda.ok else None
    except ValueError:
        ids = None
    if ids is None:
        report['failed'] = 1
        report['errors'].append({'error': failure_code(agenda) if not agenda.ok else 'invalid_agenda_reply'})
    else:
        report['deals'] = len(ids)
    if args.apply and ids is not None:
        for ident in ids:
            result = call('ping-deal-checklist', {'deal': ident, 'idempotency_key': str(uuid.uuid4())})
            reply = result.reply
            valid = (isinstance(reply, dict) and reply.get('ok') is True
                     and reply.get('schema_version') == 'deal-checklist-pings.v1' and reply.get('deal_id') == ident
                     and all(type(reply.get(key)) is int and reply[key] >= 0 for key in ('queued', 'deduplicated')))
            if not result.ok or not valid:
                report['failed'] += 1
                report['errors'].append({'deal_id': ident,
                    'error': failure_code(result) if not result.ok else 'invalid_ping_reply'})
                continue
            report['queued'] += reply['queued']
            report['deduplicated'] += reply['deduplicated']
            report['skipped'] += int(bool(reply.get('reason')))
    print(json.dumps(report, sort_keys=True))
    return int(report['failed'] > 0)


if __name__ == '__main__':
    raise SystemExit(main())
