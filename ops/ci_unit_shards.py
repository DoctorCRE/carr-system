#!/usr/bin/env python3
"""Partition MCP suites and verify execution receipts against current discovery."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
COUNT = 4


def plan(root=ROOT):
    shards = [[] for _ in range(COUNT)]
    for file in sorted((root / 'mcp-server/test').iterdir()):
        if file.is_file() and re.search(r'\.test\.(?:js|mjs)$', file.name):
            index = int.from_bytes(hashlib.sha256(file.name.encode()).digest()[:8], 'big') % COUNT
            shards[index].append(file.name)
    if any(not suites for suites in shards):
        raise ValueError('unit shard has zero suites')
    return shards


def verify(receipts, root=ROOT):
    expected = plan(root)
    ids = [receipt['shard'] for receipt in receipts]
    if sorted(ids) != list(range(1, COUNT + 1)):
        raise ValueError('require exactly one receipt for each of four shards')
    for receipt in receipts:
        actual = receipt['suites']
        if not actual:
            raise ValueError(f"shard {receipt['shard']} ran zero suites")
        wanted = expected[receipt['shard'] - 1]
        if Counter(actual) != Counter(wanted):
            missing = sorted((Counter(wanted) - Counter(actual)).elements())
            extra = sorted((Counter(actual) - Counter(wanted)).elements())
            raise ValueError(f"shard {receipt['shard']} coverage mismatch: missing={missing}, unexpected={extra}")
    print(f'Unit shard coverage: {sum(map(len, expected))} suites exactly once across four nonempty shards')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('action', choices=('plan', 'verify'))
    parser.add_argument('receipts', nargs='*', type=Path)
    args = parser.parse_args()
    try:
        if args.action == 'plan':
            print(json.dumps(plan(args.root)))
        else:
            verify([json.loads(file.read_text()) for file in args.receipts], args.root)
    except (ValueError, KeyError, OSError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
