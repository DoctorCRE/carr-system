#!/usr/bin/env python3
"""Collect bounded GitHub failure windows, retaining logs for per-run review."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

FIELDS = 'databaseId,workflowName,status,conclusion,headSha,event,createdAt,updatedAt,url,attempt,headBranch'


def identity(row):
    return row['databaseId'], row['attempt'], row['headSha'], row['conclusion']


def verify_entry(target, row):
    try:
        manifest = json.loads((target / 'manifest.json').read_text())
        if tuple(manifest['identity']) != identity(row):
            raise RuntimeError('cached run identity differs from inventory')
        for filename in ('failed.log', 'failed.json'):
            digest = hashlib.sha256((target / filename).read_bytes()).hexdigest()
            if digest != manifest['sha256'][filename]:
                raise RuntimeError(f'cached {filename} digest mismatch')
        if identity(json.loads((target / 'failed.json').read_text())) != identity(row):
            raise RuntimeError('cached metadata identity differs from inventory')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError('incomplete or malformed audit cache entry') from exc


def collect(repo, start, end, cache, workers=3):
    cache.mkdir(parents=True, exist_ok=True)
    environment = {**os.environ, 'XDG_CACHE_HOME': str(cache / 'cache')}

    def gh(*args):
        result = subprocess.run(['gh', *args], capture_output=True, text=True, timeout=180, env=environment)
        if result.returncode:
            raise RuntimeError(result.stderr)
        return result.stdout

    rows = {}
    cursor = start
    while cursor <= end:
        boundary = min(end, cursor + timedelta(days=1) - timedelta(seconds=1))
        window = f'{cursor.isoformat()}..{boundary.isoformat()}'
        batch = json.loads(gh('run', 'list', '--repo', repo, '--created', window,
                              '--limit', '1000', '--json', FIELDS))
        if len(batch) == 1000:
            raise RuntimeError('Window reached GitHub search cap; split it before claiming coverage')
        rows.update((row['databaseId'], row) for row in batch)
        cursor = boundary + timedelta(seconds=1)
    failures = json.loads(gh('run', 'list', '--repo', repo, '--status', 'failure',
                             '--created', f'{start.isoformat()}..{end.isoformat()}',
                             '--limit', '1000', '--json', FIELDS))
    if len(failures) == 1000:
        raise RuntimeError('Failure query reached GitHub search cap')
    if {identity(r) for r in failures} != {identity(r) for r in rows.values() if r['conclusion'] == 'failure'}:
        raise RuntimeError('Failure inventory changed during collection; reconcile before reporting')
    prefix = repo.replace('/', '-')
    (cache / f'{prefix}-window.json').write_text(json.dumps(list(rows.values()), indent=2))

    def logs(row):
        parent = cache / prefix / str(row['databaseId'])
        target = parent / str(row['attempt'])
        if target.exists():
            verify_entry(target, row)
            return row['databaseId']
        parent.mkdir(parents=True, exist_ok=True)
        command = ('run', 'view', str(row['databaseId']), '--repo', repo, '--attempt', str(row['attempt']))
        metadata = gh(*command, '--json', 'databaseId,jobs,attempt,conclusion,headSha,url')
        if identity(json.loads(metadata)) != identity(row):
            raise RuntimeError('fetched run identity differs from inventory')
        log = gh(*command, '--log-failed')
        with tempfile.TemporaryDirectory(prefix='.collect-', dir=parent) as temporary:
            staged = Path(temporary) / 'entry'
            staged.mkdir()
            digests = {}
            for filename, content in [('failed.json', metadata), ('failed.log', log)]:
                path = staged / filename
                path.write_text(content)
                digests[filename] = hashlib.sha256(path.read_bytes()).hexdigest()
            (staged / 'manifest.json').write_text(json.dumps({'identity': identity(row), 'sha256': digests}))
            verify_entry(staged, row)
            try:
                staged.rename(target)
            except OSError:
                if not target.exists():
                    raise
                verify_entry(target, row)
        return row['databaseId']

    with ThreadPoolExecutor(max_workers=workers) as pool:
        completed = list(pool.map(logs, failures))
    return {'repo': repo, 'runs': len(rows), 'failures': len(failures), 'logs': len(completed)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', action='append', required=True)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=3, choices=range(1, 7))
    args = parser.parse_args()
    start, end = (datetime.fromisoformat(value.replace('Z', '+00:00')) for value in [args.start, args.end])
    if start.tzinfo is None or end.tzinfo is None or end < start or end - start > timedelta(days=7):
        parser.error('Use an ordered, timezone-aware window of at most seven days')
    for repo in args.repo:
        print(json.dumps(collect(repo, start, end, args.cache.resolve(), args.workers)))


if __name__ == '__main__':
    main()
