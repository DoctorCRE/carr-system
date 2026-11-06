#!/usr/bin/env python3
"""Check recorded pending mail evidence without mutating or sending."""
import argparse
import json
import os
import signal
import time
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import release_reader
from proof_release import release_revision

MAX_BYTES = 1048576
PARSER = """import {parseMailNeedsPartnerResponse} from './mcp-server/src/mail-needs-partner.ts';
let text=''; for await(const chunk of process.stdin) text+=chunk;
process.stdout.write(JSON.stringify(parseMailNeedsPartnerResponse(JSON.parse(text))));"""


def parsed(value):
    result = subprocess.run(['node', '--experimental-strip-types', '--input-type=module', '-e', PARSER],
                            input=json.dumps(value), text=True, cwd=ROOT, capture_output=True, timeout=15)
    if result.returncode:
        raise ValueError('response_contract_invalid')
    return json.loads(result.stdout)


def observe(bundle):
    primary, other = parsed(bundle['primary']), parsed(bundle['other'])
    if primary['state'] != 'ready' or other['state'] == 'unavailable':
        raise ValueError('no_natural_evidence')
    if primary['coverage']['mailbox'] == other['coverage']['mailbox']:
        raise ValueError('second_partner_required')
    if other['more']:
        raise ValueError('second_mailbox_coverage_incomplete')
    item = primary['items'][0]
    if any(row['proposal_id'] == item['proposal_id'] for row in other['items']):
        raise ValueError('cross_partner_leak')
    canonical = bundle['canonical']
    if canonical.get('schema_version') != 'email-proposals.v1':
        raise ValueError('canonical_contract_invalid')
    witness = next((row for row in canonical['proposals'] if row['id'] == item['proposal_id']), None)
    if not witness or witness['status'] != 'pending' or witness['version'] != item['version'] or witness['source']['activity_id'] != item['source']['activity_id']:
        raise ValueError('canonical_pending_not_observed')
    if not item['reason_codes'] or not item['action']['requires_existing_review']:
        raise ValueError('review_reference_missing')
    return dict(state='observed', schema='mail-needs-partner-proof.v1', count=1,
                proposal_ids=[item['proposal_id']], pending=True, isolated=True, review_reference=True)


def production(args):
    reply = release_reader.read('https://api.doctorcre.com/release', timeout=10, max_bytes=MAX_BYTES)
    if reply.status != 200 or len(reply.body) > MAX_BYTES:
        raise ValueError('release_unavailable')
    release = json.loads(reply.body)
    if not args.merged_sha or release_revision(release) != args.merged_sha:
        raise ValueError('merge_identity_unproved')
    if not args.other_call_wrapper:
        raise ValueError('second_partner_call_wrapper_required')

    deadline = time.monotonic() + 60

    def call(wrapper, verb, payload):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('read_deadline_exhausted')
        with subprocess.Popen([str(wrapper.resolve()), 'call', verb, json.dumps(payload)],
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

    primary = parsed(call(args.primary_call_wrapper, 'read-mail-needs-partner', {'limit': 1}))
    other = parsed(call(args.other_call_wrapper, 'read-mail-needs-partner', {'limit': 50}))
    if not primary['items']:
        raise ValueError('no_natural_evidence')
    proposal_id = primary['items'][0]['proposal_id']
    cursor = None
    canonical = None
    for _ in range(20):
        payload = {'status': 'pending', 'include_non_deal': True, 'limit': 50}
        if cursor:
            payload['cursor'] = cursor
        page = call(args.primary_call_wrapper, 'list-email-proposals', payload)
        if (page.get('schema_version') != 'email-proposals.v1' or page.get('send') is not False
                or not isinstance(page.get('proposals'), list) or len(page['proposals']) > 50
                or type(page.get('more')) is not bool):
            raise ValueError('canonical_contract_invalid')
        if any(row.get('id') == proposal_id for row in page['proposals']):
            canonical = page
            break
        if not page['more']:
            break
        next_cursor = page.get('next_cursor')
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
            raise ValueError('canonical_cursor_invalid')
        cursor = next_cursor
    if canonical is None:
        raise ValueError('canonical_pending_not_observed_within_read_cap')
    receipt = observe(dict(primary=primary, other=other, canonical=canonical))
    receipt['release_sha'] = args.merged_sha
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path)
    parser.add_argument('--fixture-failure', action='store_true')
    parser.add_argument('--production', action='store_true')
    parser.add_argument('--natural', action='store_true')
    parser.add_argument('--merged-sha')
    parser.add_argument('--primary-call-wrapper', type=Path, default=ROOT / 'run.sh')
    parser.add_argument('--other-call-wrapper', type=Path, help='Existing authenticated wrapper on the second partner machine; no actor override.')
    args = parser.parse_args(argv)
    try:
        if args.fixture and not args.production:
            if args.fixture.stat().st_size > MAX_BYTES:
                raise ValueError('fixture_too_large')
            bundle = json.loads(args.fixture.read_text())
            if args.fixture_failure:
                bundle['primary']['items'][0]['status'] = 'sent'
            receipt = observe(bundle)
        elif args.production and args.natural and not args.fixture_failure:
            receipt = production(args)
        else:
            raise ValueError('fixture_or_production_natural_required')
        print(json.dumps(receipt))
        return 0
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps(dict(state='insufficient_evidence', schema='mail-needs-partner-proof.v1', reason=str(error) if isinstance(error, ValueError) else 'read_unavailable')))
        return 78


if __name__ == '__main__':
    sys.exit(main())
