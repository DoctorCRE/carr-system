#!/usr/bin/env python3
"""Observe bounded public lead reads without inventing missing address evidence."""
import argparse
import json
from pathlib import Path
import re
import signal
import subprocess
import time
import urllib.parse
import urllib.request


class Unavailable(Exception):
    pass


class Contradiction(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args):
        raise Unavailable('release_redirect')


def observe(origin, wrapper, seconds):
    deadline = time.monotonic() + seconds
    receipt = dict(schema='lead-contact-address-membership-proof.v1', release_sha=None,
                   assertions=0, eligible_records=0, exact_activities=0, detail_reads=0)
    seen = set()

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise Unavailable('deadline_exhausted')
        return value

    def release():
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(origin.rstrip('/') + '/release', timeout=remaining()) as response:
            payload = json.loads(response.read(1048577))
        value = payload.get('git_sha', {}).get('value')
        if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{40}', value):
            raise Unavailable('release_identity_unavailable')
        return value

    def call(verb, args):
        result = subprocess.run([str(Path(wrapper).resolve()), 'call', verb, json.dumps(args)],
                                capture_output=True, timeout=remaining())
        if result.returncode or len(result.stdout) > 1048576:
            raise Unavailable('read_unavailable')
        value = json.loads(result.stdout)
        if not isinstance(value, dict) or value.get('error') or value.get('ok') is False:
            raise Unavailable('read_unavailable')
        return value

    def expired(_signal, _frame):
        raise Unavailable('deadline_exhausted')

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        receipt['release_sha'] = release()
        board = call('lead-board', {'workspace': 'leads'})
        preview = call('lead-stage-preview', {})
        if board.get('schema_version') != 'lead-workspace.v1' or preview.get('contract') != 'lead-automation.v1' or preview.get('dry_run') is not True:
            raise Unavailable('public_contract_unavailable')
        if not isinstance(board.get('leads'), list) or not isinstance(preview.get('moves'), list):
            raise Unavailable('public_contract_unavailable')
        for lead in board['leads'][:20]:
            args = {'workspace': 'leads', 'lead_id': lead['id']}
            for _page in range(2):
                if receipt['detail_reads'] >= 20:
                    break
                receipt['detail_reads'] += 1
                result = call('lead-board', args)
                detail = result.get('detail')
                if not isinstance(detail, dict) or detail.get('id') != lead['id']:
                    raise Unavailable('detail_unavailable')
                for activity in detail['correspondence']:
                    metadata = activity.get('detail') or {}
                    if isinstance(metadata, str):
                        metadata = json.loads(metadata)
                    if not isinstance(metadata, dict):
                        continue
                    identity = (lead['id'], activity['id'])
                    if identity in seen:
                        continue
                    seen.add(identity)
                    if activity.get('source') not in ('local_mail', 'calendar'):
                        continue
                    if metadata.get('match') == 'exact':
                        receipt['exact_activities'] += 1
                    for move in preview['moves']:
                        if (move.get('lead_id') == lead['id'] and move.get('activity_id') == activity['id']
                                and move.get('strength') == 'strong' and move.get('status') == 'applied'):
                            receipt['assertions'] += 1
                            if metadata.get('match') != 'exact' or metadata.get('evidence_ref') != move.get('evidence_ref'):
                                raise Contradiction('preview_disagrees_with_recorded_match')
                page = detail.get('correspondence_page') or {}
                if not page.get('has_more'):
                    break
                cursor = page.get('next_cursor')
                if not isinstance(cursor, dict):
                    raise Unavailable('cursor_unavailable')
                args = {**args, 'correspondence_cursor': cursor}
            if receipt['detail_reads'] >= 20:
                break
        if release() != receipt['release_sha']:
            raise Unavailable('release_changed')
        # record-lead-contact deliberately omits the captured address from detail.
        # Neither exact metadata nor a preview proves which stored token matched.
        raise Unavailable('captured_address_not_exposed_by_public_contract')
    except Contradiction as error:
        return 1, {**receipt, 'status': 'contradiction', 'reason': str(error)}
    except Unavailable as error:
        return 78, {**receipt, 'status': 'insufficient_evidence', 'reason': str(error)}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.TimeoutExpired):
        return 78, {**receipt, 'status': 'insufficient_evidence', 'reason': 'source_unavailable'}
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
