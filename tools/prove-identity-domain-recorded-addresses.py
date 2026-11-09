#!/usr/bin/env python3
"""Observe a private natural correction receipt. Never create or seed a correction.

Receipt v1 requires schema, event_id (UUID), party_ref (P-n), party_id (UUID),
correction_release_git_sha (40 hex), field (name/org/state), corrected_value,
evidence_domain, and stored_before_email. Keep this input private. Stdout hashes
identities, addresses, domains and values. Missing observation exits 78, a
contradiction exits 1, and only a fully bound correction may exit 0. Current
registered readers omit event identity and old/new evidence, so cannot exit 0.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import urllib.parse
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import release_reader

MAX_BYTES = 65536


class Unavailable(Exception):
    pass


class Contradiction(Exception):
    pass


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def observe(args):
    deadline = time.monotonic() + args.timeout_seconds
    output = {'schema': 'identity-domain-recorded-addresses-proof.v1', 'eligible_records': 0,
              'wrapper_reads': 0, 'release_reads': 0}

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise Unavailable('total_deadline')
        return value

    def process(command):
        child = None
        try:
            mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
            try:
                child = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, start_new_session=True)
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, mask)
            stdout, _stderr = child.communicate(timeout=remaining())
            remaining()
            if child.returncode or len(stdout) > MAX_BYTES:
                raise Unavailable('process_or_auth_unavailable')
            return stdout.decode()
        finally:
            if child is not None:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()
                child.stdout.close()
                child.stderr.close()

    def release():
        output['release_reads'] += 1
        response = release_reader.read(args.origin.rstrip('/') + '/release', timeout=remaining())
        remaining()
        if response.status != 200:
            raise Unavailable('release_http_unavailable')
        raw = response.body
        if len(raw) > MAX_BYTES:
            raise Unavailable('release_size')
        value = json.loads(raw)
        sha = value.get('git_sha', {}).get('value')
        if value.get('ok') is not True or not isinstance(sha, str) or not re.fullmatch('[a-f0-9]{40}', sha):
            raise Unavailable('release_identity_unavailable')
        return sha

    def call(verb, params):
        output['wrapper_reads'] += 1
        value = json.loads(process([str(Path(args.call_wrapper).resolve()), 'call', verb, json.dumps(params)]))
        if not isinstance(value, dict) or value.get('error') or value.get('ok') is False:
            raise Unavailable('read_unavailable')
        return value

    def expired(_signal, _frame):
        raise Unavailable('total_deadline')

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, args.timeout_seconds)
    try:
        with args.receipt.open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise Unavailable('receipt_size')
        receipt = json.loads(raw)
        required = {'schema', 'event_id', 'party_ref', 'party_id', 'correction_release_git_sha',
                    'field', 'corrected_value', 'evidence_domain', 'stored_before_email'}
        if (not isinstance(receipt, dict) or set(receipt) != required
                or any(not isinstance(v, str) or not v.strip() for v in receipt.values())
                or receipt['schema'] != 'identity-domain-recorded-addresses-receipt.v1'
                or not re.fullmatch(r'P-\d+', receipt['party_ref'])
                or not re.fullmatch('[a-f0-9]{40}', receipt['correction_release_git_sha'])
                or receipt['field'] not in ('name', 'org', 'state')):
            raise Unavailable('receipt_contract')
        UUID(receipt['event_id'])
        UUID(receipt['party_id'])
        for key in ('event_id', 'party_ref', 'party_id', 'corrected_value', 'evidence_domain', 'stored_before_email'):
            output[key + '_sha256'] = digest(receipt[key])
        sha = release()
        output['release_sha'] = sha
        change = process(['git', 'log', '-1', '--format=%H', '--', 'mcp-server/src/party-identity.ts']).strip()
        process(['git', 'merge-base', '--is-ancestor', change, receipt['correction_release_git_sha']])
        process(['git', 'merge-base', '--is-ancestor', receipt['correction_release_git_sha'], sha])
        research = call('prospect-research', {'subject': receipt['party_ref']})
        history = call('catch-me-up', {'ref': receipt['party_ref'], 'limit': 100})
        if (research.get('schema_version') != 'prospect-research.v1' or research.get('sent') is not False
                or not isinstance(research.get('research', {}).get('evidence'), list)
                or research.get('research', {}).get('coverage') != 'recorded_findings_only'
                or not isinstance(history.get('timeline'), list) or not isinstance(history.get('subject'), dict)):
            raise Unavailable('registered_read_contract_unavailable')
        if research.get('id') != receipt['party_id'] or research.get('ref') != receipt['party_ref']:
            raise Contradiction('research_party_differs')
        if history['subject'].get('type') != 'party' or history['subject'].get('id') != receipt['party_id']:
            raise Unavailable('timeline_does_not_resolve_exact_party')
        if release() != sha:
            raise Unavailable('release_changed')
        field = receipt['field']
        if field in ('name', 'state') and field in research and research[field] != receipt['corrected_value']:
            raise Contradiction('stored_corrected_value_differs')
        raise Unavailable('read_contract_omits_event_id_correction_evidence_and_stored_before_addresses')
    except Contradiction as error:
        return 1, {**output, 'status': 'contradiction', 'reason': str(error)}
    except Unavailable as error:
        return 78, {**output, 'status': 'insufficient_evidence', 'reason': str(error)}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError):
        return 78, {**output, 'status': 'insufficient_evidence', 'reason': 'receipt_or_source_unavailable'}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin', required=True)
    parser.add_argument('--call-wrapper', required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--timeout-seconds', type=float, default=30)
    args = parser.parse_args()
    origin = urllib.parse.urlsplit(args.origin)
    if (origin.scheme not in ('https', 'http') or not origin.netloc or origin.username or origin.password
            or origin.path not in ('', '/') or origin.query or origin.fragment
            or (origin.scheme == 'http' and origin.hostname not in ('127.0.0.1', 'localhost', '::1'))
            or not 0 < args.timeout_seconds <= 30):
        parser.error('Use an HTTPS origin or local HTTP fixture and a deadline of at most thirty seconds.')
    code, output = observe(args)
    print(json.dumps(output, sort_keys=True))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
