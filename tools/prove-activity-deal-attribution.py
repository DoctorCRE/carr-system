#!/usr/bin/env python3
"""Prove direct and unresolved natural activity attribution without writing records."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.release_reader import read

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
    # Session transport is supplied as a private headers file, never a CLI token.
    if not args.session_headers_file or not args.merge_sha:
        raise ValueError('private_session_and_merge_identity_required')
    headers_path = Path(args.session_headers_file)
    if headers_path.stat().st_mode & 0o077:
        raise ValueError('session_file_must_be_private')
    headers = json.loads(headers_path.read_text())
    if set(headers) - {'cookie', 'authorization'}:
        raise ValueError('session_header_contract')
    calls = 0
    def fetch(path, authenticated=True):
        nonlocal calls
        calls += 1
        if calls > 32:
            raise ValueError('request_cap')
        reply = read('https://api.doctorcre.com' + path, headers=headers if authenticated else {},
                     timeout=10, max_bytes=1048576)
        if reply.status != 200 or len(reply.body) > 1048576:
            raise ValueError('source_unavailable')
        return json.loads(reply.body)
    def call(verb, arguments):
        # Only fixed registered read verbs reach the existing authenticated MCP transport.
        if verb not in ('email-router-queue', 'get-deal-room'):
            raise ValueError('read_not_allowed')
        nonlocal calls
        calls += 1
        if calls > 32:
            raise ValueError('request_cap')
        result = subprocess.run([str(Path(args.call_wrapper).resolve()), 'call', verb, json.dumps(arguments)],
                                capture_output=True, text=True, timeout=10, check=True)
        if len(result.stdout) > 1048576:
            raise ValueError('canonical_read_cap')
        data = json.loads(result.stdout)
        if data.get('error') or data.get('ok') is False:
            raise ValueError('canonical_read_unavailable')
        return data
    release = fetch('/release', authenticated=False)
    sha = release.get('git_sha', {}).get('value')
    if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha):
        raise ValueError('release_identity_missing')
    if not re.fullmatch('[0-9a-f]{40}', args.merge_sha):
        raise ValueError('merge_identity_invalid')
    if subprocess.run(['git', 'merge-base', '--is-ancestor', args.merge_sha, sha], cwd=ROOT,
                      capture_output=True, timeout=10).returncode:
        raise ValueError('release_does_not_contain_merge')
    queue = call('email-router-queue', {'limit': 25, 'days': 7})
    if queue.get('schema_version') != 'email-router-queue.v2':
        raise ValueError('canonical_list_contract')
    emails = queue['emails']
    if not isinstance(emails, list) or len(emails) > 25:
        raise ValueError('canonical_list_cap')
    canonical, responses = [], []
    for email in emails:
        activity_id = email['activity_id']
        if not re.fullmatch('[0-9a-fA-F-]{36}', activity_id):
            raise ValueError('activity_id_invalid')
        response = fetch('/api/v1/intelligence/activity-deal-attribution?activity_id=' + activity_id)
        deal_id = email.get('deal')
        if response.get('state') == 'needs_input' and deal_id is not None:
            raise ValueError('canonical_unresolved_mismatch')
        if response.get('state') == 'ready':
            if deal_id != response['direct']['deal_id']:
                raise ValueError('canonical_deal_mismatch')
            room = call('get-deal-room', {'deal': response['direct']['deal_id']})
            if room.get('schema_version') != 'deal-timeline.v1' or not any(a['id'] == activity_id for a in room['activities']):
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
    parser.add_argument('--session-headers-file')
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
        print(json.dumps({'schema': 'activity-deal-attribution-proof.v1', 'state': 'insufficient_evidence'}))
        return 78


if __name__ == '__main__':
    sys.exit(main())
