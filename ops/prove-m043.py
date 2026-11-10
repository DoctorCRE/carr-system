#!/usr/bin/env python3
"""Verify M043 consumers on a release containing the expected merge."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.release_reader import read
from lib.loadpy import load_module_from_path
from ops.git_env import scrubbed_env
from tools.proof_release import release_revision


TIMEOUT = load_module_from_path('m043_timeout', str(Path(__file__).resolve().parents[1] / 'bin/with-timeout.py'))

CHECKS = (
    ('python3', 'ops/strict-projects-selftest.py'),
    ('npm', '--prefix', 'mcp-server', 'run', 'check:staged'),
    ('node', '--test', 'practice-plugin/test/calculators.test.mjs',
     'practice-plugin/test/verticals.test.mjs'),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-merge', required=True)
    parser.add_argument('--release-url', default='https://api.doctorcre.com/release')
    parser.add_argument('--deadline-seconds', type=float, default=120)
    args = parser.parse_args()
    deadline = time.monotonic() + min(300, max(1, args.deadline_seconds))

    def remaining():
        budget = deadline - time.monotonic()
        if budget <= 0:
            raise TimeoutError('Total proof deadline expired')
        return budget

    try:
        reply = read(args.release_url, timeout=remaining(), max_bytes=65536)
        if reply.status != 200:
            raise ValueError(f'Release HTTP {reply.status}')
        revision = release_revision(json.loads(reply.body))
        ancestry = subprocess.run(
            ['git', 'merge-base', '--is-ancestor', args.expected_merge, revision],
            env=scrubbed_env(), capture_output=True, timeout=remaining())
        if ancestry.returncode:
            raise ValueError('Release does not prove containment of the expected merge')
        for command in CHECKS:
            status = TIMEOUT.main(['with-timeout.py', str(remaining()), *command])
            if status in (124, 127):
                raise ValueError('Assertion evidence unavailable or total deadline expired')
            if status:
                print('Containing release assertion failed')
                return 1
        remaining()
        print('Containing release and both consumer suites verified')
        return 0
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired) as error:
        print(f'Insufficient evidence: {error}', file=sys.stderr)
        return 78


if __name__ == '__main__':
    sys.exit(main())
