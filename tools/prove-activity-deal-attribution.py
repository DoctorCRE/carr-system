#!/usr/bin/env python3
"""Prove direct and unresolved natural activity attribution without writing records."""
import argparse
import hashlib
import os
import signal
import time
import json
from pathlib import Path
from uuid import UUID
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.release_reader import read
from proof_release import release_revision

MAX_BYTES = 1048576

PARSER = """
import {parseActivityDealAttribution} from './mcp-server/src/activity-deal-attribution.ts';
let data='';for await(const part of process.stdin)data+=part;
for(const response of JSON.parse(data))parseActivityDealAttribution(response);
"""


def validate(cohort):
    responses = cohort['responses']
    if not isinstance(responses, list) or not 2 <= len(responses) <= 100:
        raise ValueError('insufficient_evidence')
    subprocess.run(['node', '--experimental-strip-types', '--input-type=module', '-e', PARSER],
                   input=json.dumps(responses), text=True, cwd=ROOT, capture_output=True,
                   timeout=15, check=True)
    canonical = {row['activity_id']: row for row in cohort['canonical']}
    direct, unresolved = set(), set()
    for response in responses:
        activity = response.get('activity')
        if not activity:
            continue
        source = canonical.get(activity['id'])
        if not source:
            raise ValueError('canonical_evidence_missing')
        if response['state'] == 'ready':
            if source['deal_id'] != response['direct']['deal_id']:
                raise ValueError('canonical_deal_mismatch')
            direct.add(activity['id'])
        elif response['state'] == 'needs_input' and response['attribution'] in ('unresolved', 'ambiguous'):
            if source['deal_id'] is not None:
                raise ValueError('canonical_unresolved_mismatch')
            unresolved.add(activity['id'])
    if not direct or not unresolved or direct & unresolved:
        raise ValueError('insufficient_evidence')
    return {'direct_count': len(direct), 'unresolved_count': len(unresolved),
            'activity_ids_hash': hashlib.sha256(json.dumps(sorted(direct | unresolved)).encode()).hexdigest()}


def production(args):
    if not args.merge_sha:
        raise ValueError('merge_identity_required')
    reply = read('https://api.doctorcre.com/release', timeout=10, max_bytes=MAX_BYTES)
    if reply.status != 200 or len(reply.body) > MAX_BYTES:
        raise ValueError('release_unavailable')
    sha = release_revision(json.loads(reply.body))
    if sha != args.merge_sha:
        raise ValueError('release_does_not_match_merge')
    calls = 0
    deadline = time.monotonic() + 60

    def call(verb, arguments):
        nonlocal calls
        if verb not in ('email-router-queue', 'get-deal-room', 'read-activity-deal-attribution'):
            raise ValueError('read_not_allowed')
        calls += 1
        remaining = deadline - time.monotonic()
        if calls > 32 or remaining <= 0:
            raise ValueError('read_cap')
        with subprocess.Popen([str(Path(args.call_wrapper).resolve()), 'call', verb, json.dumps(arguments)],
                              cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              start_new_session=True) as process:
            try:
                output, _ = process.communicate(timeout=min(10, remaining))
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if process.returncode or len(output) > MAX_BYTES:
            raise ValueError('authenticated_read_unavailable')
        value = json.loads(output)
        if not isinstance(value, dict) or value.get('error') or value.get('ok') is False:
            raise ValueError('authenticated_read_unavailable')
        return value
    queue = call('email-router-queue', {'limit': 25, 'days': 7})
    if queue.get('schema_version') != 'email-router-queue.v2':
        raise ValueError('canonical_list_contract')
    emails = queue['emails']
    if not isinstance(emails, list) or len(emails) > 25:
        raise ValueError('canonical_list_cap')
    canonical, responses = [], []
    for email in emails:
        activity_id = email['activity_id']
        if str(UUID(activity_id)) != activity_id:
            raise ValueError('activity_id_invalid')
        response = call('read-activity-deal-attribution', {'activity_id': activity_id})
        activity = response.get('activity')
        if not isinstance(activity, dict) or activity.get('id') != activity_id:
            raise ValueError('activity_identity_mismatch')
        deal_id = email.get('deal')
        if response.get('state') == 'needs_input' and deal_id is not None:
            raise ValueError('canonical_unresolved_mismatch')
        if response.get('state') == 'ready':
            if deal_id != response['direct']['deal_id']:
                raise ValueError('canonical_deal_mismatch')
            room = call('get-deal-room', {'deal': response['direct']['deal_id']})
            if room.get('schema_version') != 'deal-timeline.v1' or room.get('deal_id') != deal_id or not any(a['id'] == activity_id for a in room['activities']):
                raise ValueError('canonical_timeline_evidence_missing')
            deal_id = response['direct']['deal_id']
        canonical.append({'activity_id': activity_id, 'deal_id': deal_id})
        responses.append(response)
    result = validate({'responses': responses, 'canonical': canonical})
    return {**result, 'release_sha': sha}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture')
    parser.add_argument('--fixture-failure', action='store_true')
    parser.add_argument('--production', action='store_true')
    parser.add_argument('--natural', action='store_true')
    parser.add_argument('--merge-sha')
    parser.add_argument('--call-wrapper', default=str(ROOT / 'run.sh'))
    args = parser.parse_args(argv)
    try:
        if args.fixture and not args.production:
            cohort = json.loads(Path(args.fixture).read_text())
            if args.fixture_failure:
                cohort['responses'] = cohort['responses'][:1]
            result = validate(cohort)
        elif args.production and args.natural and not args.fixture:
            result = production(args)
        else:
            raise ValueError('explicit_mode_required')
        print(json.dumps({'schema': 'activity-deal-attribution-proof.v1', 'state': 'passed', **result}))
        return 0
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
        receipt = {'schema': 'activity-deal-attribution-proof.v1', 'state': 'insufficient_evidence'}
        print(json.dumps(receipt))
        return 78


if __name__ == '__main__':
    sys.exit(main())
