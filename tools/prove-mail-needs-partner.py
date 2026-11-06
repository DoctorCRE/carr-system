#!/usr/bin/env python3
"""Check recorded pending mail evidence without mutating or sending."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import release_reader

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
    def read(path, cookie=None):
        headers = {'Cookie': cookie.read_text().strip()} if cookie else {}
        reply = release_reader.read('https://api.doctorcre.com' + path, headers=headers, timeout=10, max_bytes=MAX_BYTES)
        if reply.status != 200 or len(reply.body) > MAX_BYTES:
            raise ValueError('authenticated_read_unavailable')
        return json.loads(reply.body)
    release = read('/release')
    if not args.merged_sha or release.get('git_sha', {}).get('value') != args.merged_sha:
        raise ValueError('merge_identity_unproved')
    if not args.primary_cookie_file or not args.other_cookie_file:
        raise ValueError('origin_bound_partner_sessions_required')
    primary = parsed(read('/api/v1/intelligence/mail-needs-partner?limit=1', args.primary_cookie_file))
    other = parsed(read('/api/v1/intelligence/mail-needs-partner?limit=50', args.other_cookie_file))
    raise ValueError('canonical_authenticated_read_transport_unavailable')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path)
    parser.add_argument('--fixture-failure', action='store_true')
    parser.add_argument('--production', action='store_true')
    parser.add_argument('--natural', action='store_true')
    parser.add_argument('--merged-sha')
    parser.add_argument('--primary-cookie-file', type=Path)
    parser.add_argument('--other-cookie-file', type=Path)
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
