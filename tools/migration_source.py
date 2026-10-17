"""Read logical migration history without restoring covered SQL to the checkout."""
from __future__ import annotations

import argparse
from functools import lru_cache
import re
import json
from pathlib import Path
import subprocess
import sys

from migration_baseline import MANIFEST, MigrationBaseline, runner_sha256
from migration_number_contract import SLOT_RE


def _git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True)
    if result.returncode:
        raise ValueError(f'migration source: Git read failed ({" ".join(args)}): '
                         + result.stderr.decode(errors='replace').strip())
    return result.stdout


@lru_cache(maxsize=32)
def _ref_names(repo: Path, ref: str) -> frozenset[str]:
    return frozenset(Path(path).name for path in _git(
        repo, 'ls-tree', '-r', '--name-only', ref, '--', 'migrations/').decode().splitlines()
        if path.startswith('migrations/') and path.count('/') == 1 and path.endswith('.sql'))


def _names(repo: Path, ref: str | None) -> set[str]:
    if ref is None:
        return {path.name for path in (repo / 'migrations').glob('*.sql')}
    revision = ref if re.fullmatch(r'[0-9a-f]{40}', ref) else _git(repo, 'rev-parse', '--verify', f'{ref}^{{commit}}').decode().strip()
    return set(_ref_names(repo, revision))


def _baseline(repo: Path, ref: str | None) -> MigrationBaseline | None:
    if ref is None:
        if not (repo / MANIFEST).exists():
            return None
        return MigrationBaseline.load(repo)
    revision = ref if re.fullmatch(r'[0-9a-f]{40}', ref) else _git(repo, 'rev-parse', '--verify', f'{ref}^{{commit}}').decode().strip()
    return _ref_baseline(repo, revision)


@lru_cache(maxsize=32)
def _ref_baseline(repo: Path, ref: str) -> MigrationBaseline | None:
    files = _git(repo, 'ls-tree', '--name-only', ref, '--', MANIFEST).decode().splitlines()
    if MANIFEST not in files:
        return None
    try:
        return MigrationBaseline.from_document(json.loads(_git(repo, 'show', f'{ref}:{MANIFEST}')))
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f'migration source: invalid baseline at {ref}: {exc}') from exc


def migration_names(repo: Path, ref: str | None = None) -> set[str]:
    """Return active and explicitly covered filenames at the requested source."""
    baseline = _baseline(repo, ref)
    return _names(repo, ref) | (set(dict(baseline.migrations)) if baseline else set())


def read_migration(repo: Path, name: str, ref: str | None = None) -> str:
    """Return runner-normalized SQL, checking any baseline-covered source."""
    if not SLOT_RE.fullmatch(name):
        raise ValueError(f'migration source: invalid filename {name!r}')
    baseline = _baseline(repo, ref)
    covered = dict(baseline.migrations) if baseline else {}
    try:
        if ref is None and (repo / 'migrations' / name).is_file():
            raw = (repo / 'migrations' / name).read_bytes()
        elif ref is not None and name in _names(repo, ref):
            raw = _git(repo, 'show', f'{ref}:migrations/{name}')
        elif name in covered:
            try:
                raw = _git(repo, 'show', f'{baseline.source_revision}:migrations/{name}')
            except ValueError as exc:
                raise ValueError(f'migration source: missing pinned Git source for {name}: {exc}') from exc
        else:
            raise ValueError(f'migration source: {name} is absent and not covered by a baseline')
        text = raw.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
    except (OSError, UnicodeError) as exc:
        raise ValueError(f'migration source: cannot read {name}: {exc}') from exc
    if name in covered and runner_sha256(text) != covered[name]:
        raise ValueError(f'migration source: checksum mismatch for {name}')
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('read', 'names'))
    parser.add_argument('name', nargs='?')
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--ref')
    args = parser.parse_args()
    try:
        if args.operation == 'read':
            if args.name is None:
                parser.error('read requires a migration filename')
            sys.stdout.write(read_migration(args.repo, args.name, args.ref))
        else:
            print('\n'.join(sorted(migration_names(args.repo, args.ref))))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
