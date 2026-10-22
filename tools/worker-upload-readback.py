#!/usr/bin/env python3
"""Reconcile or upload an immutable Worker candidate. Exit 3 means no retained match."""
from __future__ import annotations

import argparse
import fcntl
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import tomllib
from urllib.parse import quote

UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')


def retained_versions(wrangler: str) -> list:
    # Wrangler's list command slices to ten deployable versions. The API only
    # paginates when deployable=true is omitted.
    config = tomllib.loads(Path('wrangler.toml').read_text())
    account, script = config['account_id'], config['name']
    if not re.fullmatch(r'[0-9a-f]{32}', account) or not re.fullmatch(r'[A-Za-z0-9_-]+', script):
        raise ValueError('provider scope is malformed')
    auth = subprocess.run([wrangler, 'auth', 'token', '--json'], capture_output=True,
                          text=True, timeout=30, stdin=subprocess.DEVNULL)
    if auth.returncode:
        raise ValueError('provider read authentication unavailable')
    credential = json.loads(auth.stdout)
    token = credential.get('token')
    if credential.get('type') not in ('api_token', 'oauth') or not isinstance(token, str) or not token.strip():
        raise ValueError('provider read authentication malformed')
    header = 'header = ' + json.dumps('Authorization: Bearer ' + token.strip()) + '\n'
    versions, seen = [], set()
    for page in range(1, 1001):
        url = (f'https://api.cloudflare.com/client/v4/accounts/{account}/workers/scripts/'
               f'{quote(script)}/versions?page={page}&per_page=100')
        response = subprocess.run(['curl', '-q', '--config', '-', '--fail', '--silent', '--show-error',
                                   '--max-time', '30', '--max-filesize', '1048576', url],
                                  input=header, capture_output=True, text=True, timeout=35)
        if response.returncode:
            raise ValueError('provider version page unavailable')
        payload = json.loads(response.stdout)
        if payload.get('success') is not True:
            raise ValueError('provider version page unsuccessful')
        rows = payload['result']['items']
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError('provider version page malformed')
        for row in rows:
            identifier = row.get('id') if isinstance(row, dict) else None
            if not isinstance(identifier, str) or not UUID.fullmatch(identifier) or identifier in seen:
                raise ValueError('provider pagination has no unique immutable identity')
            seen.add(identifier)
        versions.extend(rows)
        if len(rows) < 100:
            return versions
    raise ValueError('provider version pagination did not complete')


def resolve(wrangler: str, sha: str, digest: str, *, version: str | None = None) -> str | None:
    def read(*args):
        result = subprocess.run([wrangler, 'versions', *args, '--json'],
                                capture_output=True, text=True, timeout=120,
                                stdin=subprocess.DEVNULL)
        if result.returncode:
            raise ValueError('provider version read unavailable')
        return json.loads(result.stdout)

    versions = [{'id': version}] if version else retained_versions(wrangler)
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


def upload(wrangler: str, sha: str, digest: str, common_dir: Path, args: list[str]) -> str:
    locks = common_dir / 'carr-upload-locks'
    locks.mkdir(parents=True, exist_ok=True)
    # Do not unlink the lock file: waiters must continue to share its inode.
    # The OS releases the claim on every exit, including crashes.
    with (locks / digest).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        existing = resolve(wrangler, sha, digest)
        if existing:
            if '--secrets-file' in args:
                raise ValueError('existing candidate cannot attest requested secret rotation')
            return existing
        for attempt in range(3):
            try:
                result = subprocess.run([wrangler, 'versions', 'upload', *args], capture_output=True,
                                        text=True, timeout=300, stdin=subprocess.DEVNULL)
                output, rc = result.stdout + result.stderr, result.returncode
            except subprocess.TimeoutExpired:
                output, rc = 'network timeout', 124
            identifiers = set(re.findall(r'Worker Version ID:\s*(' + UUID.pattern + ')', output.lower(), re.I))
            if len(identifiers) > 1:
                raise ValueError('upload named conflicting immutable identities')
            found = resolve(wrangler, sha, digest, version=next(iter(identifiers), None))
            if found:
                return found
            # A completed, exact provider search proved no publication. Only
            # transient transport errors justify repeating this write.
            transient = re.search(r'fetch failed|network|timed? out|timeout|ECONNRESET|ETIMEDOUT|'
                                  r'ECONNREFUSED|ENOTFOUND|EAI_AGAIN|UND_ERR_', output, re.I)
            if not rc or not transient or attempt == 2:
                raise ValueError('upload failed without a verifiable publication')
            time.sleep(2 ** (attempt + 1))
    raise ValueError('upload retry exhausted')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wrangler', required=True)
    parser.add_argument('--sha', required=True)
    parser.add_argument('--digest', required=True)
    parser.add_argument('--version')
    parser.add_argument('--upload-common-dir', type=Path)
    parser.add_argument('upload_args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9a-f]{40}', args.sha) or not re.fullmatch(r'sha256:[0-9a-f]{64}', args.digest):
        parser.error('exact source SHA and candidate digest are required')
    if args.version and not UUID.fullmatch(args.version):
        parser.error('version must be an immutable UUID')
    try:
        if args.upload_common_dir:
            version = upload(args.wrangler, args.sha, args.digest, args.upload_common_dir,
                             args.upload_args[1:] if args.upload_args[:1] == ['--'] else args.upload_args)
        else:
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
