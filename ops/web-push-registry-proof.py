#!/usr/bin/env python3
"""Observe a containing CARR release and replay signed-in registry read evidence."""
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

VERBS = {'register-web-push-subscription-v1': True, 'revoke-web-push-subscription-v1': True,
         'read-web-push-subscriptions-v1': False, 'invalidate-web-push-session-v1': True}
PARSER = """
import { readFileSync } from 'node:fs';
import { Validator } from '@cfworker/json-schema';
const value = JSON.parse(readFileSync(0, 'utf8'));
const schema = JSON.parse(readFileSync('contracts/web-push-registry.v1.json', 'utf8'));
process.exit(new Validator(schema).validate(value).valid ? 0 : 1);
"""


class InsufficientEvidence(ValueError):
    pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--merge-sha', required=True)
    parser.add_argument('--origin', default='https://api.doctorcre.com')
    parser.add_argument('--evidence', type=Path, required=True,
                        help='Recorded signed-in app-origin list-verbs and registry read, bound to release_sha')
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
            args.merge_sha, sha], cwd=args.repository, capture_output=True, timeout=remaining())
        if result.returncode:
            raise InsufficientEvidence('release_does_not_prove_merge')
        provenance = bundle.get('provenance', {})
        if (provenance.get('release_sha') != sha or provenance.get('actor_slug') not in ('joe', 'dell')
                or provenance.get('transport') != 'signed-in-app-origin/mcp'
                or provenance.get('app_origin') != 'https://app.doctorcre.com'
                or provenance.get('queries') != [
                    {'verb': 'list-verbs', 'args': {'filter': 'web-push'}},
                    {'verb': 'read-web-push-subscriptions-v1', 'args': {}}]):
            raise InsufficientEvidence('authenticated_read_provenance_required')
        catalog = bundle.get('catalog', {})
        registry = bundle.get('registry', {})
        if catalog.get('ok') is not True or not isinstance(catalog.get('verbs'), list):
            raise InsufficientEvidence('catalog_unavailable')
        if registry.get('ok') is not True or registry.get('error'):
            raise InsufficientEvidence('authenticated_registry_unavailable')
        parsed = subprocess.run(['node', '--input-type=module', '-e', PARSER], cwd=ROOT / 'mcp-server',
            input=json.dumps(registry), text=True, capture_output=True, timeout=remaining())
        if parsed.returncode:
            raise AssertionError('registry_response_contract')
        if not isinstance(registry.get('subscriptions'), list):
            raise AssertionError('registry_list_required')
        names = {row['name']: row for row in catalog['verbs']}
        for name, write in VERBS.items():
            if name not in names or names[name].get('write') is not write:
                raise AssertionError('registry_verb_missing_or_misclassified')
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
                      'controlled_write_proof': False}))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
