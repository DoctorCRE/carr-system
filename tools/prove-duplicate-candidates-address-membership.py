#!/usr/bin/env python3
"""Prove a natural recorded-address candidate without making an identity decision."""
import argparse
import json
from pathlib import Path
import re
import signal
import subprocess
import time
import urllib.parse
import urllib.request


UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')


class Unavailable(Exception):
    pass


class Contradiction(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args):
        raise Unavailable('release_redirect')


def observe(origin, wrapper, seconds):
    deadline = time.monotonic() + seconds
    receipt = dict(schema='duplicate-candidates-address-membership-proof.v1',
                   release_sha=None, assertions=0, wrapper_calls=0, examined_rows=0)

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise Unavailable('deadline_exhausted')
        return value

    def release():
        with urllib.request.build_opener(NoRedirect()).open(
                origin.rstrip('/') + '/release', timeout=remaining()) as response:
            payload = json.loads(response.read(1048577))
        sha = payload.get('git_sha', {}).get('value')
        if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha):
            raise Unavailable('release_identity_unavailable')
        return sha

    def call(verb, args, schema):
        if receipt['wrapper_calls'] >= 20:
            raise Unavailable('wrapper_call_bound')
        receipt['wrapper_calls'] += 1
        result = subprocess.run([str(Path(wrapper).resolve()), 'call', verb, json.dumps(args)],
                                capture_output=True, timeout=remaining())
        if result.returncode or len(result.stdout) > 1048576:
            raise Unavailable('read_unavailable')
        value = json.loads(result.stdout)
        if not isinstance(value, dict) or value.get('schema_version') != schema:
            raise Unavailable('read_contract_unavailable')
        return value

    def count(rows):
        if not isinstance(rows, list):
            raise Unavailable('rows_unavailable')
        receipt['examined_rows'] += len(rows)
        if receipt['examined_rows'] > 1000:
            raise Unavailable('row_bound')

    def expired(_signal, _frame):
        raise Unavailable('deadline_exhausted')

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        receipt['release_sha'] = release()
        contacts = call('email-match-contacts', {}, 'email-match-contacts.v1')['contacts']
        count(contacts)
        by_ref = {}
        for contact in contacts:
            ref, address = contact['ref'], contact['address']
            if not isinstance(ref, str) or not isinstance(address, str):
                raise Unavailable('contact_contract_unavailable')
            if re.fullmatch('[PLCV]-[0-9]+', ref, re.I):
                by_ref.setdefault(ref, set()).add(address)
        witness = None
        resolved = {}
        for contact in contacts:
            snapshot = contact.get('contact_snapshot')
            if not snapshot or len(by_ref.get(contact['ref'], ())) < 2:
                continue
            for ref, addresses in by_ref.items():
                if ref == contact['ref'] or not addresses.intersection(by_ref[contact['ref']]):
                    continue
                ids = []
                for subject in (contact['ref'], ref):
                    if subject not in resolved:
                        record = call('prospect-research', {'subject': subject}, 'prospect-research.v1')
                        if record.get('contact_state') is None or record.get('sent') is not False:
                            raise Unavailable('identity_contract_unavailable')
                        identity = record.get('id')
                        if not isinstance(identity, str) or not UUID.fullmatch(identity):
                            raise Unavailable('identity_uuid_unavailable')
                        resolved[subject] = identity
                    ids.append(resolved[subject])
                if ids[0] != ids[1] and snapshot.get('party_id') == ids[0]:
                    witness = tuple(sorted(ids))
                    break
            if witness:
                break
        if not witness:
            raise Unavailable('natural_witness_unavailable')
        receipt['party_ids'] = list(witness)
        args, cursor, matches = {'limit': 100}, None, 0
        while True:
            page = call('party-duplicate-candidates', args, 'party-duplicate-candidates.v1')
            if page.get('read_only') is not True or not isinstance(page.get('caution'), str):
                raise Unavailable('candidate_contract_unavailable')
            candidates = page['candidates']
            count(candidates)
            if len(candidates) > 100:
                raise Unavailable('page_bound')
            for pair in candidates:
                key = (pair['left']['id'], pair['right']['id'])
                if not all(isinstance(value, str) and UUID.fullmatch(value) for value in key):
                    raise Unavailable('pair_uuid_unavailable')
                if key[0] >= key[1] or (cursor is not None and key <= cursor):
                    raise Contradiction('pair_order_or_uniqueness')
                cursor = key
                if key == witness:
                    if pair['left']['kind'] != pair['right']['kind']:
                        raise Unavailable('cross_kind_witness')
                    matches += 1
                    if sum(r['code'] == 'same_email' for r in pair['reasons']) != 1:
                        raise Contradiction('shared_recorded_address_missing_reason')
            if page.get('has_more') is False:
                if page.get('next_cursor') is not None:
                    raise Contradiction('terminal_cursor')
                break
            next_cursor = page.get('next_cursor')
            if (page.get('has_more') is not True or not candidates or not isinstance(next_cursor, dict)
                    or (next_cursor.get('left_id'), next_cursor.get('right_id')) != cursor):
                raise Unavailable('page_cursor_unavailable')
            args = {'limit': 100, 'cursor': next_cursor}
        if release() != receipt['release_sha']:
            raise Unavailable('release_changed')
        if matches != 1:
            raise Unavailable('same_kind_natural_pair_not_observed')
        receipt['assertions'] = 3
        return 0, {**receipt, 'status': 'pass'}
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
