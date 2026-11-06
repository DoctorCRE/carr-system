#!/usr/bin/env python3
"""Observe terminal email preview refusal without changing production records."""
import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.record_call import call_verb, _run_process


class Unavailable(Exception):
    pass


class Contradiction(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise Unavailable('redirect_refused')


def observe(origin, wrapper, seconds):
    deadline = time.monotonic() + seconds
    receipt = {'schema': 'email-next-step-closed-date-proof.v1', 'requests': 0,
               'inspected_records': 0, 'eligible_closed': 0, 'eligible_active': 0, 'assertions': []}

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise Unavailable('deadline_exhausted')
        return value

    def request():
        remaining()
        receipt['requests'] += 1
        if receipt['requests'] > 24:
            raise Unavailable('request_budget_exhausted')

    def release():
        request()
        with urllib.request.build_opener(NoRedirect()).open(origin.rstrip('/') + '/release', timeout=remaining()) as response:
            value = json.loads(response.read(1048577))
        remaining()
        sha = value.get('git_sha', {}).get('value')
        if (value.get('ok') is not True or value.get('env', {}).get('value') != 'production'
                or not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha)):
            raise Unavailable('release_identity_unavailable')
        return sha

    def call(verb, payload):
        request()

        def runner(argv, **kwargs):
            return _run_process([str(Path(wrapper).resolve()), *argv[1:]], **kwargs)

        result = call_verb(verb, payload, timeout=remaining(), runner=runner)
        remaining()
        if result.kind not in ('ok', 'refused') or not isinstance(result.reply, dict):
            raise Unavailable('transport_unavailable')
        if result.kind == 'refused' and result.error != 'email_next_step_evidence_not_found':
            raise Unavailable('verb_refused')
        return result

    def read(verb, payload, schema):
        result = call(verb, payload)
        if result.kind != 'ok' or result.reply.get('schema_version') != schema:
            raise Unavailable('read_contract_unavailable')
        return result.reply

    def expired(_signal, _frame):
        raise Unavailable('deadline_exhausted')

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        receipt['release_sha'] = release()
        board = read('deal-room-board', {'workspace': 'all'}, 'local-deals-board.v2')
        if any(not isinstance(board.get(key), list) for key in ('deals', 'closed_deals', 'lost_deals')):
            raise Unavailable('board_contract_unavailable')
        candidates = [*board['closed_deals'], *board['lost_deals'], *board['deals']]
        seen = set()
        contradiction = None
        for candidate in candidates:
            if receipt['inspected_records'] >= 10:
                break
            deal = candidate.get('id')
            if not isinstance(deal, str) or not re.fullmatch('[0-9a-f-]{36}', deal) or deal in seen:
                continue
            seen.add(deal)
            receipt['inspected_records'] += 1
            room = read('get-deal-room', {'deal': deal}, 'deal-timeline.v1')
            if (room.get('id') != deal or room.get('deal_id') != deal or not isinstance(room.get('activities'), list)
                    or not {'phase', 'closed_on', 'invoiced_on'} <= room.keys()
                    or not isinstance(room['phase'], str)):
                raise Unavailable('room_contract_unavailable')
            closed = (room.get('closed_on') is not None and room.get('invoiced_on') is None
                      and room.get('phase') != 'closed')
            active = (room.get('closed_on') is None and room.get('invoiced_on') is None
                      and room.get('phase') != 'closed')
            cohort = 'closed' if closed else 'active' if active else None
            if cohort is None or receipt['eligible_' + cohort]:
                continue
            source = None
            for activity in room['activities'][:500]:
                detail = activity.get('detail')
                if activity.get('kind') not in ('email_in', 'email_out') or not isinstance(detail, str):
                    continue
                try:
                    occurred = datetime.fromisoformat(activity['occurred_at'].replace('Z', '+00:00'))
                    if occurred > datetime.now(timezone.utc):
                        continue
                except (KeyError, ValueError, TypeError):
                    continue
                if len(detail.strip()) >= 10 and isinstance(activity.get('id'), str):
                    source = (activity['id'], detail[:2000])
                    break
            if source is None:
                continue
            result = call('preview-email-next-step', {'deal': deal, 'activity_id': source[0],
                          'evidence_quote': source[1], 'text': 'Review captured email evidence',
                          'next_date': date.today().isoformat(), 'owner': 'joe'})
            if closed:
                if result.kind != 'refused' or result.error != 'email_next_step_evidence_not_found':
                    contradiction = 'closed_date_preview_not_refused'
                    break
                receipt['assertions'].append('closed_date_preview_refused')
            else:
                if result.kind == 'refused':
                    continue
                value = result.reply
                if (value.get('contract') != 'email-next-step.v1' or value.get('status') != 'proposed'
                        or value.get('deal') != deal or value.get('source', {}).get('activity_id') != source[0]
                        or value.get('evidence_quote') != source[1] or value.get('requires_confirmation') is not True
                        or not re.fullmatch('sha256:[0-9a-f]{64}', str(value.get('proposal_digest')))):
                    contradiction = 'active_preview_contract_invalid'
                    break
                receipt['assertions'].append('active_preview_proposed')
            receipt['eligible_' + cohort] += 1
            if receipt['eligible_closed'] and receipt['eligible_active']:
                break
        if release() != receipt['release_sha']:
            raise Unavailable('release_changed')
        receipt['assertions'].append('release_identity_stable')
        if contradiction:
            raise Contradiction(contradiction)
        if not receipt['eligible_closed'] or not receipt['eligible_active']:
            raise Unavailable('natural_cohort_or_source_unavailable')
        return 0, {**receipt, 'status': 'passed'}
    except Contradiction as error:
        return 1, {**receipt, 'status': 'contradiction', 'reason': str(error)}
    except Unavailable as error:
        return 78, {**receipt, 'status': 'unavailable', 'reason': str(error)}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError):
        return 78, {**receipt, 'status': 'unavailable', 'reason': 'source_unavailable'}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin', required=True)
    parser.add_argument('--call-wrapper', required=True)
    parser.add_argument('--timeout-seconds', type=float, default=30)
    args = parser.parse_args()
    origin = urllib.parse.urlsplit(args.origin)
    if (origin.scheme not in ('https', 'http') or not origin.netloc or origin.username or origin.password
            or origin.query or origin.fragment or origin.path not in ('', '/')
            or (origin.scheme == 'http' and origin.hostname not in ('127.0.0.1', 'localhost', '::1'))
            or not 0 < args.timeout_seconds <= 30):
        parser.error('Use an HTTPS origin or local HTTP fixture and a deadline of at most thirty seconds.')
    code, receipt = observe(args.origin, args.call_wrapper, args.timeout_seconds)
    print(json.dumps(receipt, sort_keys=True))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
