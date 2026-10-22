#!/usr/bin/env python3
"""Resolve an immutable Worker candidate. Exit 3 means no retained match."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')


def resolve(wrangler: str, sha: str, digest: str, *, version: str | None = None) -> str | None:
    def read(*args):
        result = subprocess.run([wrangler, 'versions', *args, '--json'],
                                capture_output=True, text=True, timeout=120,
                                stdin=subprocess.DEVNULL)
        if result.returncode:
            raise ValueError('provider version read unavailable')
        return json.loads(result.stdout)

    versions = [{'id': version}] if version else read('list')
    if not isinstance(versions, list):
        raise ValueError('provider version list is malformed')
    matches = set()
    for row in versions:
        identifier = row.get('id') if isinstance(row, dict) else None
        if not isinstance(identifier, str) or not UUID.fullmatch(identifier):
            raise ValueError('provider version list has no immutable identity')
        tag = (row.get('annotations') or {}).get('workers/tag')
        if not version and tag and tag != 'carr-' + sha:
            continue
        detail = read('view', identifier)
        if not isinstance(detail, dict) or detail.get('id') != identifier:
            raise ValueError('provider detail identity differs')
        bindings = detail.get('resources', {}).get('bindings')
        if not isinstance(bindings, list) or any(not isinstance(b, dict) for b in bindings):
            raise ValueError('provider bindings unavailable')
        values = {}
        for binding in bindings:
            name = binding.get('name')
            if name not in {'GIT_SHA', 'CANDIDATE_MANIFEST_DIGEST'}:
                continue
            if name in values or binding.get('type') != 'plain_text' or not isinstance(binding.get('text'), str):
                raise ValueError('candidate identity binding is ambiguous')
            values[name] = binding['text']
        if values.get('GIT_SHA') == sha and values.get('CANDIDATE_MANIFEST_DIGEST') == digest:
            matches.add(identifier)
    if len(matches) > 1:
        raise ValueError('multiple provider versions bind this exact candidate')
    if matches:
        return matches.pop()
    if version:
        raise ValueError('candidate identity is mismatched')
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wrangler', required=True)
    parser.add_argument('--sha', required=True)
    parser.add_argument('--digest', required=True)
    parser.add_argument('--version')
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9a-f]{40}', args.sha) or not re.fullmatch(r'sha256:[0-9a-f]{64}', args.digest):
        parser.error('exact source SHA and candidate digest are required')
    if args.version and not UUID.fullmatch(args.version):
        parser.error('version must be an immutable UUID')
    try:
        version = resolve(args.wrangler, args.sha, args.digest, version=args.version)
    except (ValueError, KeyError, TypeError, AttributeError, OSError, subprocess.SubprocessError):
        print('worker upload readback unverifiable; no permission to repeat upload', file=sys.stderr)
        return 1
    if version is None:
        return 3
    print(version)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
