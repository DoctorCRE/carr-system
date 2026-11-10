"""Verify release containment before running the Chromium safety characterization."""
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


def prove(args):
    deadline = time.monotonic() + args.timeout

    def remaining():
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise ValueError('deadline')
        return seconds

    def command(argv):
        return subprocess.run(argv, cwd=args.repository, capture_output=True, timeout=remaining())

    if not 0 < args.timeout <= 300 or not re.fullmatch('[0-9a-f]{40}', args.expected_merge):
        raise ValueError('valid merge and timeout required')
    if args.release:
        payload = args.release.read_bytes()
    else:
        reply = read('https://api.doctorcre.com/release', timeout=remaining(), max_bytes=1048576)
        if reply.status != 200 or len(reply.body) > 1048576:
            raise ValueError('release unavailable')
        payload = reply.body
    revision = release_revision(json.loads(payload))
    for commit in [args.expected_merge, revision]:
        if command(['git', '--no-replace-objects', 'cat-file', '-e', commit + '^{commit}']).returncode:
            raise ValueError('release history unavailable; fetch the merge and deployed revision')
    if command(['git', '--no-replace-objects', 'merge-base', '--is-ancestor', args.expected_merge, revision]).returncode:
        raise ValueError('release does not contain merge')
    suite = args.repository / 'mcp-server/test/chromium-safety-args.test.ts'
    if not suite.is_file():
        raise ValueError('characterization unavailable')
    result = command(['node', '--test', str(suite)])
    remaining()
    print(json.dumps({'status': 'passed' if result.returncode == 0 else 'assertion_failure',
                      'release_sha': revision, 'expected_merge': args.expected_merge,
                      'stdout': result.stdout.decode(), 'stderr': result.stderr.decode()}))
    return 0 if result.returncode == 0 else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-merge', required=True)
    parser.add_argument('--repository', type=Path, default=ROOT)
    parser.add_argument('--release', type=Path, help='Recorded /release response; omit for the shared live reader')
    parser.add_argument('--timeout', type=float, default=60)
    args = parser.parse_args()
    try:
        return prove(args)
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({'status': 'insufficient_evidence', 'reason': str(error)}))
        return 78


if __name__ == '__main__':
    raise SystemExit(main())
