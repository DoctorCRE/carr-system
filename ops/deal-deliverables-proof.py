#!/usr/bin/env python3
"""Observe partner-attested writes and four read-only production observations.

The partner supplies sanitized upload/finalize/share/unshare responses, then
revokes that session. A second current session observes the file. Cookie files
are read only at runtime and never printed. Fixtures use the identical parser.
"""
import argparse
import base64
import hashlib
import json
import re
import signal
from pathlib import Path
import urllib.request
import urllib.error

MAX_BYTES = 36 * 1024 * 1024


def check(condition, message):
    if not condition:
        raise ValueError(message)


def observe(receipt, sha, read):
    check(re.fullmatch('[a-f0-9]{40}', sha), 'full release SHA required')
    check(receipt['partner'] in ('joe', 'dell') and receipt['attestation'], 'partner attestation required')
    upload = receipt['upload']
    check(upload['ok'] is True and upload['state'] == 'verified', 'verified upload required')
    final, shared, private = [receipt[key]['deliverable'] for key in ('finalize', 'share', 'unshare')]
    for key in ('finalize', 'share', 'unshare'):
        check(receipt[key]['ok'] is True and receipt[key]['schema_version'] == 'deal-deliverables.v1', 'registered response contract required')
    for row in (final, shared, private):
        check(row['id'] == upload['deliverable_id'] and row['deal_id'] == final['deal_id'] and row['state'] == 'finalized', 'same finalized deal file required')
        check(row['sha256'] == upload['sha256'] and int(row['bytes']) == upload['bytes'] and row['mime'] == upload['mime'], 'reservation digest contract mismatch')
    check((final['visibility'], shared['visibility'], private['visibility']) == ('private', 'shared', 'private'), 'visibility sequence required')
    check((final['version'], shared['version'], private['version']) == (1, 2, 3), 'expected-version writes required')
    check(read('release')['source_commit'] == sha, 'release mismatch')
    listing = read('list')
    check(listing['ok'] is True and listing['schema_version'] == 'deal-deliverables.v1', 'list response contract required')
    check(any(row == private for row in listing['deliverables']), 'current private row missing or changed')
    data = base64.b64decode(read('download')['base64'], validate=True)
    check(len(data) == int(private['bytes']) and hashlib.sha256(data).hexdigest() == private['sha256'], 'download bytes differ')
    check(read('revoked')['status'] == 401, 'revoked session retained access')
    return {'ok': True, 'release': sha, 'deliverable_id': private['id'], 'observations': 4, 'provenance': 'partner-attested writes plus read-only observations'}


def deadline_expired(*unused):
    raise TimeoutError('proof deadline exceeded')


def main():
    signal.signal(signal.SIGALRM, deadline_expired)
    signal.alarm(60)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt', required=True, type=Path)
    parser.add_argument('--sha', required=True)
    parser.add_argument('--fixture', type=Path)
    parser.add_argument('--active-cookie-file', type=Path)
    parser.add_argument('--revoked-cookie-file', type=Path)
    args = parser.parse_args()
    try:
        check(args.receipt.stat().st_size <= MAX_BYTES, 'receipt too large')
        receipt = json.loads(args.receipt.read_text())
        if args.fixture:
            check(args.fixture.stat().st_size <= MAX_BYTES, 'fixture too large')
            fixture = json.loads(args.fixture.read_text())
            read = fixture.__getitem__
        else:
            check(args.active_cookie_file and args.revoked_cookie_file, 'two partner-supplied cookie files required')
            row = receipt['finalize']['deliverable']
            check(all(re.fullmatch('[a-f0-9-]{36}', row[k]) for k in ('deal_id', 'id')), 'invalid file path')
            root = '/api/v1/deal-deliverables/' + row['deal_id']
            paths = {'release':'/release', 'list':root, 'download':root+'/'+row['id']+'/download', 'revoked':root+'/'+row['id']+'/download'}
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *unused):
                    return None
            opener = urllib.request.build_opener(NoRedirect())
            def read(key):
                cookie = (args.revoked_cookie_file if key == 'revoked' else args.active_cookie_file).read_text().strip()
                request = urllib.request.Request('https://api.doctorcre.com' + paths[key], headers={'Cookie':cookie})
                try:
                    response = opener.open(request, timeout=10)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    data = response.read(MAX_BYTES + 1)
                    check(len(data) <= MAX_BYTES, 'GET exceeds byte limit')
                    if key == 'revoked':
                        return {'status':response.status}
                    check(response.status == 200, 'GET refused')
                    return {'base64':base64.b64encode(data).decode()} if key == 'download' else json.loads(data)
        print(json.dumps(observe(receipt, args.sha, read)))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(json.dumps({'ok':False, 'error':str(error)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
