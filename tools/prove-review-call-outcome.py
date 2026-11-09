#!/usr/bin/env python3
"""Check release-bound synthetic review observations without writing production.

The proof job supplies registered stale/review/replay responses and SQL readbacks
collected during its authorized synthetic exercise. This verifier does not create
that evidence. Missing evidence exits 78. Fixtures use the identical validator.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.release_reader import read


class InsufficientEvidence(ValueError):
    pass


def load(path):
    if path.stat().st_size > 1048576:
        raise InsufficientEvidence('response_size')
    return json.loads(path.read_text())


def inspect(observation):
    required = {'source_commit', 'session_id', 'old_version', 'current_version', 'refusal',
                'before', 'receipt', 'replay', 'counts'}
    if not isinstance(observation, dict) or not required <= observation.keys():
        raise InsufficientEvidence('missing_observations')
    session = observation['session_id']
    expected = {'ok': True, 'session_id': session, 'reviewed': True}
    checks = [
        observation['old_version'] != observation['current_version'],
        observation['before'] == {'outcome_reviewed_at': None},
        observation['receipt'] == expected,
        observation['replay'] == {**expected, 'replayed': True},
        observation['counts'] == {'events': 1, 'receipts': 1, 'stale_events': 0,
                                  'stale_receipts': 0, 'reviewed': True},
    ]
    refusal = observation['refusal']
    checks.append(isinstance(refusal, dict) and set(refusal) == {'error', 'correlation_id', 'hint'}
                  and refusal['error'] == 'source_version_changed'
                  and isinstance(refusal['correlation_id'], str) and bool(refusal['correlation_id'])
                  and refusal['hint'] == 'This call changed. Reopen the card and review the current outcome.')
    if not all(checks):
        raise AssertionError('review_atomicity_or_replay')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--merge-sha', required=True)
    parser.add_argument('--repo', type=Path, default=ROOT)
    parser.add_argument('--observations', required=True, type=Path)
    parser.add_argument('--release-fixture', type=Path)
    parser.add_argument('--timeout-seconds', type=float, default=30)
    args = parser.parse_args()
    deadline = time.monotonic() + args.timeout_seconds

    def remaining():
        left = deadline - time.monotonic()
        if left <= 0:
            raise InsufficientEvidence('deadline')
        return left

    def release():
        if args.release_fixture:
            return load(args.release_fixture)
        response = read('https://api.doctorcre.com/release', timeout=remaining(), max_bytes=1048576)
        if response.status != 200 or len(response.body) > 1048576:
            raise InsufficientEvidence('release_unavailable')
        return json.loads(response.body)

    try:
        if not 0 < args.timeout_seconds <= 60 or not re.fullmatch('[a-f0-9]{40}', args.merge_sha):
            raise InsufficientEvidence('arguments')
        identity = release()
        served = identity['git_sha']['value']
        if (identity.get('ok') is not True or identity['env']['value'] != 'production'
                or not isinstance(served, str) or not re.fullmatch('[a-f0-9]{40}', served)):
            raise InsufficientEvidence('release_contract')
        git = ['git', '--no-replace-objects', '-C', str(args.repo)]
        for revision in [args.merge_sha, served]:
            result = subprocess.run([*git, 'cat-file', '-t', revision], capture_output=True, timeout=remaining())
            if result.returncode or result.stdout.strip() != b'commit':
                raise InsufficientEvidence('unknown_release')
        result = subprocess.run([*git, 'merge-base', '--is-ancestor', args.merge_sha, served],
                                capture_output=True, timeout=remaining())
        if result.returncode:
            raise InsufficientEvidence('release_does_not_contain_merge')
        observation = load(args.observations)
        if observation.get('source_commit') != served:
            raise InsufficientEvidence('observations_from_different_release')
        inspect(observation)
        if release() != identity:
            raise InsufficientEvidence('release_changed')
        remaining()
        print(json.dumps({'status': 'passed', 'source_commit': served,
                          'coverage': 'supplied_registered_responses_and_sql_readbacks',
                          'fixture': bool(args.release_fixture)}))
        return 0
    except AssertionError as error:
        print(json.dumps({'status': 'assertion_failure', 'error': str(error)}))
        return 1
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
        print(json.dumps({'status': 'insufficient_evidence'}))
        return 78


if __name__ == '__main__':
    sys.exit(main())
