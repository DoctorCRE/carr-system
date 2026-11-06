#!/usr/bin/env python3
"""Read closing anniversaries and verify canonical natural evidence without writes."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import signal
import subprocess
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.release_reader import read as read_http

MAX_BYTES = 1048576
PARSER = """
import {parseClientAnniversaryResponse} from './mcp-server/src/client-anniversary-read.ts';
let body='';for await(const chunk of process.stdin)body+=chunk;
console.log(JSON.stringify(parseClientAnniversaryResponse(JSON.parse(body))));
"""


def parse_response(value):
    result = subprocess.run(['node', '--experimental-strip-types', '--input-type=module', '-e', PARSER],
                            input=json.dumps(value), text=True, capture_output=True, cwd=ROOT, timeout=10)
    if result.returncode:
        raise ValueError('response_contract_refused')
    return json.loads(result.stdout)


def observe(response, reference):
    value = parse_response(response)
    if value['state'] != 'ready' or not value['coverage']['complete'] or not value['rows']:
        raise ValueError('natural_rows_required')
    row = value['rows'][0]
    for left, right in [('deal_id', 'id'), ('client_id', 'client_id'), ('closed_on', 'closed_on'),
                        ('deal_version', 'base_version')]:
        if row[left] != reference[right]:
            raise ValueError('canonical_evidence_changed')
    if reference['outcome'] != 'won' or reference['phase'] not in ('closed', 'invoiced'):
        raise ValueError('not_confirmed_closed_won')
    return {'state': 'passed', 'rows': len(value['rows']), 'canonical_match': True,
            'evidence_hash': hashlib.sha256(row['evidence_ref'].encode()).hexdigest()}


def deadline(*_):
    raise TimeoutError('deadline')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path)
    parser.add_argument('--fixture-failure', action='store_true')
    parser.add_argument('--production', action='store_true')
    parser.add_argument('--natural', action='store_true')
    parser.add_argument('--active-cookie-file', type=Path)
    parser.add_argument('--merge-sha')
    args = parser.parse_args()
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(45)
    try:
        if args.fixture:
            if args.production or args.fixture.stat().st_size > MAX_BYTES:
                raise ValueError('invalid_fixture_mode')
            fixture = json.loads(args.fixture.read_text())
            response, reference = fixture['response'], fixture['reference']
            if args.fixture_failure:
                response['rows'][0]['closed_on'] = '2020-02-30'
        else:
            if not (args.production and args.natural and args.active_cookie_file and args.merge_sha):
                raise ValueError('private_session_and_merge_identity_required')
            if args.active_cookie_file.stat().st_mode & 0o077:
                raise ValueError('session_file_must_be_private')
            if args.active_cookie_file.stat().st_size > 8192:
                raise ValueError('session_file_too_large')
            cookie = args.active_cookie_file.read_text().strip()
            def read(path):
                reply = read_http('https://api.doctorcre.com' + path, headers={'Cookie': cookie},
                                  timeout=8, max_bytes=MAX_BYTES)
                if reply.status != 200 or len(reply.body) > MAX_BYTES:
                    raise ValueError('authenticated_read_unavailable')
                return json.loads(reply.body)
            release = read('/release')
            sha = release.get('source_commit', '')
            if sha != args.merge_sha:
                check = subprocess.run(['git', 'merge-base', '--is-ancestor', args.merge_sha, sha], cwd=ROOT,
                                       capture_output=True, timeout=5)
                if check.returncode:
                    raise ValueError('release_merge_identity_unproved')
            response = parse_response(read('/api/v1/intelligence/client-anniversary-read?horizon_days=90&limit=1'))
            if response['as_of'] != datetime.now(ZoneInfo('America/Chicago')).date().isoformat():
                raise ValueError('observation_date_not_current')
            if not response['rows']:
                raise ValueError('natural_rows_required')
            raise ValueError('release_reader POST support for read-only MCP verbs')
        receipt = observe(response, reference)
        if args.production:
            receipt['release_sha'] = sha
            receipt['merge_sha'] = args.merge_sha
        print(json.dumps(receipt))
        return 0
    except (ValueError, KeyError, TypeError, OSError, TimeoutError, subprocess.SubprocessError) as error:
        receipt = {'state': 'insufficient_evidence'}
        if str(error) == 'release_reader POST support for read-only MCP verbs':
            receipt['missing_contract'] = str(error)
        print(json.dumps(receipt))
        return 78
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    raise SystemExit(main())
