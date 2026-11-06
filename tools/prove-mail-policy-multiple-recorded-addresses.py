#!/usr/bin/env python3
"""Observe a natural multi-address restriction without sending or changing records."""
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
    receipt = dict(schema='mail-policy-multiple-recorded-addresses-proof.v1',
                   release_sha=None, wrapper_calls=0, examined_rows=0, witnesses=0, addresses=0)

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise Unavailable('deadline_exhausted')
        return value

    def release():
        with urllib.request.build_opener(NoRedirect()).open(
                origin.rstrip('/') + '/release', timeout=remaining()) as response:
            data = response.read(1048577)
        if len(data) > 1048576:
            raise Unavailable('release_size_limit')
        value = json.loads(data).get('git_sha', {}).get('value')
        if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{40}', value):
            raise Unavailable('release_identity_unavailable')
        return value

    def call(verb, args):
        if receipt['wrapper_calls'] >= 20:
            raise Unavailable('wrapper_call_limit')
        receipt['wrapper_calls'] += 1
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
        matched = call('email-match-contacts', {})
        contacts = matched.get('contacts')
        if matched.get('schema_version') != 'email-match-contacts.v1' or not isinstance(contacts, list):
            raise Unavailable('contact_contract_unavailable')
        if len(contacts) > 1000:
            raise Unavailable('contact_row_limit')
        receipt['examined_rows'] = len(contacts)
        candidates = {}
        for contact in contacts:
            ref = contact.get('ref')
            snapshot = contact.get('contact_snapshot')
            if (isinstance(ref, str) and re.fullmatch(r'[PLCV]-[A-Za-z0-9-]+', ref)
                    and isinstance(snapshot, dict) and contact.get('exact') is True):
                candidates.setdefault(snapshot['party_id'], (ref, snapshot))
        for party_id, (ref, snapshot) in candidates.items():
            addresses = sorted({c['address'] for c in contacts if c.get('ref') == ref})
            if len(addresses) < 2:
                continue
            if len(addresses) > 50 or any(not re.fullmatch(r'[^@\s,;<>]+@[^@\s,;<>]+\.[A-Za-z]{2,}', a) for a in addresses):
                raise Unavailable('stored_addresses_outside_recipient_contract')
            raw = snapshot.get('values', {}).get('email')
            if not isinstance(raw, str) or not set(addresses).issubset(re.split(r'[\s,;<>]+', raw.lower())):
                raise Unavailable('snapshot_address_evidence_unavailable')
            record = call('prospect-research', {'subject': ref})
            if (record.get('schema_version') != 'prospect-research.v1' or record.get('id') != party_id
                    or record.get('sent') is not False):
                raise Unavailable('restriction_identity_unavailable')
            if record.get('contact_state') != 'do_not_contact':
                continue
            policy = call('mail-contact-policy', {'emails': addresses})
            recipients = policy.get('recipients')
            if (not isinstance(recipients, list) or len(recipients) != len(addresses)
                    or [r.get('email') for r in recipients] != addresses
                    or any(type(r.get('blocked')) is not bool for r in recipients)):
                raise Unavailable('policy_contract_unavailable')
            if any(not r['blocked'] for r in recipients):
                raise Contradiction('recorded_restriction_not_blocked')
            receipt['witnesses'] += 1
            receipt['addresses'] += len(addresses)
            break
        if release() != receipt['release_sha']:
            raise Unavailable('release_changed')
        if not receipt['witnesses']:
            raise Unavailable('no_natural_multi_address_restricted_witness')
        return 0, {**receipt, 'status': 'pass'}
    except Contradiction as error:
        try:
            if release() != receipt['release_sha']:
                raise Unavailable('release_changed')
        except (Unavailable, OSError, ValueError, TypeError, AttributeError):
            return 78, {**receipt, 'status': 'insufficient_evidence', 'reason': 'release_unavailable_or_changed'}
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
