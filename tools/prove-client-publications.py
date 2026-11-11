#!/usr/bin/env python3
"""Compare partner preview with an independently captured authenticated client response.

No passwords or portal sessions are acquired. The evidence bundle contains
independent authenticated partner and client reads, an existing versioned
publication receipt, and their release identity. Missing evidence exits 78.
The publication_receipt contains deal_id and the original API response.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.release_reader import read


def verify(preview, authored, client, receipt, deal_id):
    if preview.get('schema_version') != 'client-publications.v1' or authored.get('schema_version') != 'client-publications.v1':
        raise ValueError('publication_contract')
    portal = preview.get('portal')
    if not isinstance(portal, dict) or client.get('portal') != portal:
        raise ValueError('projection_mismatch')
    if portal.get('roadmap') != {'current_phase': None, 'phases': []}:
        raise ValueError('automatic_phases_exposed')
    rows = authored.get('publications')
    visible = portal.get('publications')
    if not isinstance(rows, list) or not isinstance(visible, list):
        raise ValueError('publication_rows_invalid')
    by_id = {row['id']: row for row in rows}
    if not isinstance(receipt, dict) or not isinstance(receipt.get('response'), dict):
        return 78
    if receipt.get('deal_id') != deal_id:
        raise ValueError('publication_receipt_scope')
    response = receipt['response']
    if response.get('ok') is not True or response.get('schema_version') != 'client-publications.v1':
        raise ValueError('publication_receipt_contract')
    recorded = response.get('publication')
    if not isinstance(recorded, dict):
        return 78
    source = by_id.get(recorded.get('id'))
    if not source or source.get('version') != recorded.get('version'):
        return 78
    if recorded != {key: source.get(key) for key in ('id', 'version', 'published')}:
        raise ValueError('publication_receipt_mismatch')
    expected_text = {row['id'] for row in rows if row.get('published') is True and row.get('kind') != 'deliverable'}
    visible_text = {row.get('id') for row in visible if row.get('kind') != 'deliverable'}
    if expected_text != visible_text:
        raise ValueError('publication_text_membership')
    for row in visible:
        source = by_id.get(row.get('id'))
        if not source or source.get('published') is not True:
            raise ValueError('draft_exposed')
        for key in ('kind', 'title'):
            if row.get(key) != source.get(key):
                raise ValueError('published_value_mismatch')
        if row['kind'] != 'deliverable' and row.get('content') != source.get('content'):
            raise ValueError('published_text_mismatch')
    if not rows or not visible or not any(row.get('published') is False for row in rows):
        return 78
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-revision', required=True)
    parser.add_argument('--deal-id', required=True)
    parser.add_argument('--client-response', type=Path, required=True,
                        help='Partner/client read bundle with publication_receipt and source_commit; no password')
    parser.add_argument('--fixture', type=Path, help='Real handler responses from a disposable PostgreSQL test')
    args = parser.parse_args()
    deadline = time.monotonic() + 30
    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError('deadline')
        return value
    def git(*parts):
        return subprocess.run(['git', *parts], cwd=ROOT, capture_output=True, text=True,
                              timeout=remaining(), check=False)
    try:
        expected = git('rev-parse', '--verify', args.expected_revision + '^{commit}')
        if expected.returncode:
            return 78
        expected = expected.stdout.strip()
        receipt = json.loads(args.client_response.read_text())
        fixture = json.loads(args.fixture.read_text()) if args.fixture else None
        if fixture:
            release = fixture['release']
        else:
            reply = read('https://api.doctorcre.com/release', timeout=remaining(), max_bytes=1048576)
            if reply.status != 200:
                return 78
            release = json.loads(reply.body)
        if not isinstance(receipt, dict) or not isinstance(release, dict) or not isinstance(release.get('git_sha'), dict):
            return 78
        revision = release['git_sha'].get('value')
        if not isinstance(revision, str) or git('merge-base', '--is-ancestor', expected, revision).returncode:
            return 78
        if receipt.get('source_commit') != revision:
            return 78
        observations = fixture if fixture else receipt.get('partner')
        if not isinstance(observations, dict):
            return 78
        def call(verb):
            return observations[verb]
        return verify(call('preview-deal-client-view'), call('list-deal-client-publications'),
                      receipt['response'], receipt.get('publication_receipt'), args.deal_id)
    except (OSError, KeyError, json.JSONDecodeError, subprocess.SubprocessError, TimeoutError):
        return 78
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
