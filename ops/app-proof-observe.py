#!/usr/bin/env python3
"""Prove app release ancestry and bounded public content assertions without sign-in."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ORIGIN = 'https://app.doctorcre.com'
MAX_BYTES = 1048576
MAX_REQUESTS = 20
MAX_RUNTIME = 300


def public_get(path, timeout, limit):
    result = subprocess.run([
        'curl', '-q', '--silent', '--show-error', '--fail', '--proto', '=https',
        '--max-time', str(timeout), '--max-filesize', str(limit), ORIGIN + path,
    ], capture_output=True, timeout=timeout + 1)
    if result.returncode:
        raise ValueError(f'public GET failed for {path} (curl {result.returncode})')
    return result.stdout


def observe(repo, merge_sha, assertions=(), human_steps=(), *, fetch=public_get):
    deadline = time.monotonic() + MAX_RUNTIME
    if not re.fullmatch('[0-9a-f]{40}', merge_sha):
        raise ValueError('merge SHA must be a full lowercase commit SHA')
    if len(assertions) + 1 > MAX_REQUESTS:
        raise ValueError('public requests exceed 20')
    for path, marker in assertions:
        if not re.fullmatch(r'/[A-Za-z0-9/_.-]*', path) or path.startswith('//') or '..' in path:
            raise ValueError('assertion path must be a public app path without traversal or query')
        if not marker or len(marker.encode()) > 4096:
            raise ValueError('assertion marker must contain 1 to 4096 bytes')
    if len(human_steps) > 20 or any(not step.strip() or len(step.encode()) > 4096 for step in human_steps):
        raise ValueError('human steps exceed 20 or contain invalid text')
    remaining_bytes = MAX_BYTES

    def timeout():
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            raise ValueError('proof deadline exceeded')
        return min(15, remaining - 1)

    def get(path):
        nonlocal remaining_bytes
        if remaining_bytes <= 0:
            raise ValueError('public response bytes exceed 1 MiB')
        data = fetch(path, timeout(), remaining_bytes)
        remaining_bytes -= len(data)
        if remaining_bytes < 0:
            raise ValueError('public response bytes exceed 1 MiB')
        return data

    identity = json.loads(get('/app-release'))
    if not isinstance(identity, dict) or identity.get('service') != 'doctorcre-app' or identity.get('environment') != 'production':
        raise ValueError('app release identity must identify doctorcre-app production')
    served = identity.get('source_commit')
    if not isinstance(served, str) or not re.fullmatch('[0-9a-f]{40}', served):
        raise ValueError('app release source_commit must be a full commit SHA')
    # Proofs use stored objects, independent of caller Git routing and history overlays.
    git_env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    git_env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_GRAFT_FILE=os.devnull, GIT_NO_LAZY_FETCH='1')
    git = ['git', '--no-replace-objects', '-c', 'core.useReplaceRefs=false', '-C', str(repo)]
    for sha in (merge_sha, served):
        result = subprocess.run([*git, 'cat-file', '-t', sha],
                                env=git_env, capture_output=True, timeout=timeout())
        if result.returncode or result.stdout.strip() != b'commit':
            raise ValueError('unknown or unavailable app commit; fetch app main before observing')
    result = subprocess.run([*git, 'merge-base', '--is-ancestor', merge_sha, served],
                            env=git_env, capture_output=True, timeout=timeout())
    if result.returncode:
        raise ValueError('requested merge commit is not a proven ancestor of the served app release')
    checked = []
    for path, marker in assertions:
        data = get(path)
        if marker.encode() not in data:
            raise ValueError(f'public marker absent from {path}')
        checked.append({'path': path, 'contains': marker, 'sha256': hashlib.sha256(data).hexdigest()})
    timeout()
    receipt = {'schema': 'app-public-proof-receipt.v1', 'status': 'human_pending' if human_steps else 'passed',
               'origin': ORIGIN, 'merge_commit': merge_sha, 'source_commit': served,
               'assertions': checked, 'human_steps': list(human_steps), 'requests': len(checked) + 1}
    if len(json.dumps(receipt).encode()) > MAX_BYTES:
        raise ValueError('receipt bytes exceed 1 MiB')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-repo', type=Path, required=True)
    parser.add_argument('--merge-sha', required=True)
    parser.add_argument('--assert', dest='assertions', action='append', default=[], metavar='PATH=TEXT')
    parser.add_argument('--human-step', action='append', default=[])
    args = parser.parse_args()
    try:
        assertions = []
        for value in args.assertions:
            if '=' not in value:
                raise ValueError('assertion must be PATH=TEXT')
            assertions.append(tuple(value.split('=', 1)))
        print(json.dumps(observe(args.app_repo, args.merge_sha, assertions, args.human_step), sort_keys=True))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print('App proof REFUSED: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
