#!/usr/bin/env python3
"""Read-only observation of merge and shipment comment markers."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import time

spec = importlib.util.spec_from_file_location('comments', Path(__file__).with_name('pr-release-comments.py'))
assert spec is not None and spec.loader is not None
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', default='DoctorCRE/carr-system')
    parser.add_argument('--waiting-pr', required=True, type=int)
    parser.add_argument('--merge-pr', required=True, type=int)
    parser.add_argument('--shipped-pr', required=True, type=int)
    parser.add_argument('--merge-sha', required=True)
    parser.add_argument('--fixture', type=Path)
    args = parser.parse_args()
    deadline = time.monotonic() + 60
    try:
        if (not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args.repository)
                or not re.fullmatch('[0-9a-f]{40}', args.merge_sha)
                or min(args.waiting_pr, args.merge_pr, args.shipped_pr) < 1):
            raise ValueError('invalid proof identity')
        for kind, number, marker in [
                ('merge', args.waiting_pr, f'<!-- carr-merge:{args.merge_pr} -->'),
                ('deploy', args.shipped_pr, f'<!-- carr-deploy:{args.merge_sha} -->')]:
            rows = []
            for page in range(1, 6):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError('proof deadline exceeded')
                if args.fixture:
                    raw = (args.fixture / f'{kind}.json').read_text()
                else:
                    command = ['gh', 'api', f'repos/{args.repository}/issues/{number}/comments?per_page=100&page={page}']
                    result = subprocess.run(command, capture_output=True, text=True, timeout=min(10, remaining))
                    result.check_returncode()
                    raw = result.stdout
                batch = bridge.parse_comments(json.loads(raw))
                rows.extend(batch)
                if len(batch) < 100:
                    break
            else:
                raise ValueError('proof comment limit exceeded')
            count = sum(bridge.has_marker(row, marker) for row in rows)
            if count != 1:
                raise ValueError(f'{kind} expected exactly one bot marker, observed {count}')
        print(json.dumps({'ok': True, 'merge': args.merge_pr, 'shipped': args.shipped_pr}))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}))
        return 78


if __name__ == '__main__':
    raise SystemExit(main())
