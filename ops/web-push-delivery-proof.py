#!/usr/bin/env python3
"""Verify a containing release, owner delivery status and external phone receipt."""
import argparse
import json
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.release_reader import read
from git_env import scrubbed_env

PARSER = """
import { readFileSync } from 'node:fs';
import { Validator } from '@cfworker/json-schema';
const value = JSON.parse(readFileSync(0, 'utf8'));
const schema = JSON.parse(readFileSync('contracts/web-push-delivery.v1.json', 'utf8'));
process.stdout.write(JSON.stringify(new Validator(schema).validate(value).valid));
"""


class InsufficientEvidence(ValueError):
    pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--merge-sha', required=True)
    parser.add_argument('--origin', default='https://api.doctorcre.com')
    parser.add_argument('--evidence', type=Path, required=True,
                        help='Recorded owner-only status read and external phone/revocation receipts, bound to release_sha')
    parser.add_argument('--fixture', action='store_true', help='Offline only; use evidence.release instead of HTTP')
    parser.add_argument('--repository', type=Path, default=ROOT, help='Repository containing release and merge objects')
    parser.add_argument('--timeout', type=float, default=20)
    args = parser.parse_args(argv)
    deadline = time.monotonic() + args.timeout

    def remaining():
        budget = deadline - time.monotonic()
        if budget <= 0:
            raise InsufficientEvidence('deadline')
        return budget

    def expired(_signum, _frame):
        raise InsufficientEvidence('deadline')

    previous = signal.signal(signal.SIGALRM, expired)
    try:
        if not 0 < args.timeout <= 60:
            raise InsufficientEvidence('invalid_deadline')
        signal.setitimer(signal.ITIMER_REAL, remaining())
        if args.evidence.stat().st_size > 1048576:
            raise InsufficientEvidence('evidence_too_large')
        bundle = json.loads(args.evidence.read_text())
        if args.fixture:
            release = bundle.get('release')
        else:
            reply = read(args.origin.rstrip('/') + '/release', timeout=remaining(), max_bytes=1048576)
            if reply.status != 200 or len(reply.body) > 1048576:
                raise InsufficientEvidence('release_unavailable')
            release = json.loads(reply.body)
        sha = release['git_sha']['value']
        if (release.get('ok') is not True or release['env']['value'] != 'production'
                or not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha)
                or not re.fullmatch('[0-9a-f]{40}', args.merge_sha)):
            raise InsufficientEvidence('release_identity_unavailable')
        result = subprocess.run(['git', '--no-replace-objects', 'merge-base', '--is-ancestor',
            args.merge_sha, sha], cwd=args.repository, env=scrubbed_env(), capture_output=True, timeout=remaining())
        if result.returncode:
            raise InsufficientEvidence('release_does_not_prove_merge')
        provenance = bundle.get('provenance', {})
        if (provenance.get('release_sha') != sha or provenance.get('actor_slug') not in ('joe', 'dell')
                or provenance.get('transport') != 'signed-in-app-origin/mcp'
                or provenance.get('app_origin') != 'https://app.doctorcre.com'
                or provenance.get('queries') != [
                    {'verb': 'read-web-push-delivery-status-v1', 'args': {}}]):
            raise InsufficientEvidence('authenticated_read_provenance_required')
        status = bundle.get('status')
        phone = bundle.get('phone')
        revoked = bundle.get('revoked')
        if not all(isinstance(value, dict) for value in (status, phone, revoked)):
            raise InsufficientEvidence('status_and_external_receipts_required')
        if (phone.get('release_sha') != sha or phone.get('actor_slug') != provenance['actor_slug']
                or not isinstance(phone.get('observed_at'), str)):
            raise InsufficientEvidence('phone_receipt_provenance_required')
        parsed = subprocess.run(['node', '--input-type=module', '-e', PARSER], cwd=ROOT / 'mcp-server',
            input=json.dumps(status), text=True, capture_output=True, timeout=remaining())
        if parsed.returncode:
            raise InsufficientEvidence('contract_validator_unavailable')
        if json.loads(parsed.stdout) is not True:
            raise AssertionError('delivery_response_contract')
        rows = status['deliveries']
        matches = [row for row in rows if row['id'] == phone.get('outbox_id')]
        if len(matches) != 1:
            raise AssertionError('phone_outbox_row')
        row = matches[0]
        if (row['state'] != 'provider_accepted' or not row['attempt_id'] or row['attempt_count'] < 1
                or any(row[field] != phone.get(field) for field in
                       ('notification_id', 'attempt_id', 'installation_id', 'generation'))
                or phone.get('notification_observed') is not True or phone.get('exact_card_opened') is not True):
            raise AssertionError('phone_attempt_correlation')
        old = [item for item in rows if item['id'] == revoked.get('outbox_id')]
        if (len(old) != 1 or old[0]['state'] != 'cancelled' or old[0]['attempt_count'] != 0
                or old[0]['attempt_id'] is not None or revoked.get('submissions') != 0
                or old[0]['installation_id'] != row['installation_id']
                or old[0]['generation'] >= row['generation']
                or any(old[0][field] != revoked.get(field) for field in ('installation_id', 'generation'))):
            raise AssertionError('revoked_generation_submission')
        remaining()
        code, status = 0, 'passed'
    except AssertionError as error:
        code, status = 1, str(error)
    except (ValueError, OSError, TypeError, KeyError, AttributeError, subprocess.SubprocessError):
        code, status = 78, 'insufficient_evidence'
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    print(json.dumps({'status': status, 'mode': 'fixture' if args.fixture else 'production',
                      'phone_evidence': 'externally_supplied'}))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
