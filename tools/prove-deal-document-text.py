#!/usr/bin/env python3
"""Check retained document evidence only after proving release containment."""
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
from proof_release import release_revision


class InsufficientEvidence(Exception):
    pass


def prove(args):
    deadline = time.monotonic() + args.timeout

    def remaining():
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise InsufficientEvidence('deadline')
        return seconds

    def command(argv, **kwargs):
        return subprocess.run(argv, capture_output=True, timeout=remaining(), **kwargs)

    if not 0 < args.timeout <= 300:
        raise InsufficientEvidence('timeout_must_be_between_zero_and_300')
    for evidence in [args.response, args.original, args.expected_text]:
        if not evidence.is_file():
            raise InsufficientEvidence('document_evidence_unavailable')
    if not re.fullmatch('[0-9a-f]{40}', args.expected_merge):
        raise InsufficientEvidence('expected_merge_required')
    if args.release:
        release = json.loads(args.release.read_text())
    else:
        reply = read('https://api.doctorcre.com/release', timeout=remaining(), max_bytes=1048576)
        if reply.status != 200 or len(reply.body) > 1048576:
            raise InsufficientEvidence('release_unavailable')
        release = json.loads(reply.body)
    revision = release_revision(release)
    for commit in [args.expected_merge, revision]:
        if command(['git', '--no-replace-objects', 'cat-file', '-e', commit + '^{commit}'], cwd=args.repository).returncode:
            raise InsufficientEvidence('release_history_unavailable')
    if command(['git', '--no-replace-objects', 'merge-base', '--is-ancestor', args.expected_merge, revision], cwd=args.repository).returncode:
        raise InsufficientEvidence('release_does_not_contain_merge')
    result = command(['node', str(ROOT / 'tools/check-deal-document-text-proof.mjs'),
                      str(args.response), str(args.original), str(args.expected_text)], cwd=ROOT)
    remaining()
    if result.returncode:
        print(json.dumps({'status': 'assertion_failure', 'release_sha': revision,
                          'detail': result.stderr.decode().strip()}))
        return 1
    print(json.dumps({'status': 'passed', 'release_sha': revision,
                      'expected_merge': args.expected_merge, 'evidence': json.loads(result.stdout)}))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-merge', required=True)
    parser.add_argument('--release', type=Path, help='Saved /release fixture; omit for the live shared release reader')
    parser.add_argument('--repository', type=Path, default=ROOT)
    parser.add_argument('--response', type=Path, required=True)
    parser.add_argument('--original', type=Path, required=True)
    parser.add_argument('--expected-text', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=30)
    args = parser.parse_args()
    try:
        return prove(args)
    except (InsufficientEvidence, ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({'status': 'insufficient_evidence', 'reason': str(error)}))
        return 78


if __name__ == '__main__':
    raise SystemExit(main())
