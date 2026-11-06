#!/usr/bin/env python3
"""Read release identity and replay recorded registered quiet-follow-ups reads."""
import argparse
from datetime import date, datetime
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.release_reader import read as release_read


class InsufficientEvidence(ValueError):
    pass


def response(value):
    """Parse the quiet-follow-ups.v1 response consumed by this proof."""
    if not isinstance(value, dict) or value.get('schema_version') != 'quiet-follow-ups.v1':
        raise InsufficientEvidence('quiet_follow_up_response_unavailable')
    if set(value) != {'schema_version', 'cadence', 'proposals', 'sent', 'tasks_created'}:
        raise InsufficientEvidence('quiet_follow_up_response_shape')
    cadence = value['cadence']
    if (not isinstance(cadence, dict) or set(cadence) != {'lead_days', 'vendor_days'}
            or any(type(n) is not int or not 1 <= n <= 365 for n in cadence.values())):
        raise InsufficientEvidence('cadence_contract')
    rows = value['proposals']
    if not isinstance(rows, list) or len(rows) > 1000:
        raise InsufficientEvidence('proposals_contract')
    for row in rows:
        if (not isinstance(row, dict) or row.get('subject_type') not in {'lead', 'vendor'}
                or not {'subject_id', 'owner_id', 'owner', 'due_on'} <= row.keys()
                or not re.fullmatch(r'[0-9a-f-]{36}', str(row['subject_id']))
                or row['owner_id'] is not None and not re.fullmatch(r'[0-9a-f-]{36}', str(row['owner_id']))
                or row['owner'] is not None and not isinstance(row['owner'], str)):
            raise InsufficientEvidence('proposal_contract')
        if len(row['due_on']) == 10:
            date.fromisoformat(row['due_on'])
        else:
            datetime.fromisoformat(row['due_on'].replace('Z', '+00:00'))
    return value


def require(condition, reason):
    if not condition:
        raise AssertionError(reason)


def verify(bundle):
    provenance = bundle.get('provenance', {})
    slug = bundle.get('owner_slug')
    if (not isinstance(slug, str) or not slug.strip() or len(slug) > 100
            or not re.fullmatch('[0-9a-f]{40}', provenance.get('producer_revision', ''))
            or not re.fullmatch('[0-9a-f]{64}', provenance.get('producer_sha256', ''))
            or provenance.get('query') != {'verb': 'quiet-follow-ups', 'all': {},
                'owned': {'owner_slug': slug}, 'unassigned': {'owner_slug': None}}):
        raise InsufficientEvidence('query_or_revision_provenance_missing')
    values = {key: response(bundle.get(key)) for key in ('all', 'owned', 'unassigned')}
    all_rows = values['all']['proposals']
    if not any(row['owner'] == slug for row in all_rows):
        raise InsufficientEvidence('owner_cohorts_unavailable')
    for value in values.values():
        require(value['sent'] is False and value['tasks_created'] is False, 'read_side_effect_flags')
        require(value['cadence'] == values['all']['cadence'], 'cadence_changed')
        rows = value['proposals']
        require(rows == sorted(rows, key=lambda row: (row['due_on'], row['subject_type'], row['subject_id'])), 'due_order_changed')
    require(values['owned']['proposals'] == [r for r in all_rows if r['owner'] == slug], 'owned_subset_changed')
    require(values['unassigned']['proposals'] == [r for r in all_rows if r['owner_id'] is None], 'unassigned_subset_changed')
    return {'status': 'passed', 'counts': {key: len(value['proposals']) for key, value in values.items()}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin', default='https://api.doctorcre.com')
    parser.add_argument('--fixture', type=Path, help='Offline replay only, never production proof')
    parser.add_argument('--evidence', type=Path, help='Recorded signed-in app-origin /mcp reads')
    parser.add_argument('--merge-sha', help='Merged revision required in production /release')
    args = parser.parse_args(argv)
    try:
        if args.fixture:
            result = verify(json.loads(args.fixture.read_text()))
            result['mode'] = 'fixture-replay'
        else:
            reply = release_read(args.origin.rstrip('/') + '/release', timeout=15, max_bytes=1048576)
            if reply.status != 200 or len(reply.body) > 1048576:
                raise InsufficientEvidence('release_unavailable')
            release = json.loads(reply.body)
            sha = release.get('git_sha', {}).get('value')
            if (release.get('ok') is not True or release.get('env', {}).get('value') != 'production'
                    or not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha)):
                raise InsufficientEvidence('release_contract')
            if not args.evidence or not args.merge_sha:
                raise InsufficientEvidence('identity_only_owner_reads_required')
            bundle = json.loads(args.evidence.read_text())
            provenance = bundle.get('provenance', {})
            if (sha != args.merge_sha or provenance.get('release_sha') != sha
                    or provenance.get('transport') != 'signed-in-app-origin/mcp'
                    or provenance.get('app_origin') != 'https://app.doctorcre.com'):
                raise InsufficientEvidence('signed_in_reads_or_merge_identity_unavailable')
            result = verify(bundle)
            result.update(mode='production', release_sha=sha)
        code = 0
    except AssertionError as error:
        code, result = 1, {'status': 'failed', 'reason': str(error)}
    except (InsufficientEvidence, OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        code, result = 78, {'status': 'insufficient-evidence', 'reason': str(error)}
    print(json.dumps(result, sort_keys=True))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
