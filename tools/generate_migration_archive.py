"""Generate the minimal deterministic archive from the immutable provenance commit.

Only this explicit audit operation needs history. Ordinary readers never fetch it.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import runpy
import zipfile

from migration_baseline import MigrationBaseline, runner_sha256
from migration_source import ARCHIVE, _git, read_baseline_snapshot, read_migration


def generate(repo: Path) -> bytes:
    baseline = MigrationBaseline.load(repo)
    members = {'db/schema.sql': _git(repo, 'show', f'{baseline.source_revision}:db/schema.sql')}
    if hashlib.sha256(members['db/schema.sql']).hexdigest() != baseline.snapshot_sha256:
        raise ValueError('historical snapshot checksum mismatch')
    parse = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'ops/migration-safety-gate.py'))['ledger_rows']
    if parse(members['db/schema.sql'].decode()) != dict(baseline.migrations):
        raise ValueError('historical snapshot ledger differs from baseline')
    for name, digest in baseline.migrations:
        original = _git(repo, 'show', f'{baseline.source_revision}:migrations/{name}')
        if runner_sha256(original.decode()) != digest:
            raise ValueError(f'historical migration checksum mismatch: {name}')
        retained = repo / 'migrations' / name
        if retained.is_file():
            if runner_sha256(retained.read_text()) != digest:
                raise ValueError(f'retained migration checksum mismatch: {name}')
        else:
            members[f'migrations/{name}'] = original
    import io
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, raw in sorted(members.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, raw, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return output.getvalue()


def verify(repo: Path) -> None:
    baseline = MigrationBaseline.load(repo)
    for name, _ in baseline.migrations:
        read_migration(repo, name)
    snapshot = read_baseline_snapshot(repo)
    parse = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'ops/migration-safety-gate.py'))['ledger_rows']
    if parse(snapshot.decode()) != dict(baseline.migrations):
        raise ValueError('archived snapshot ledger differs from baseline')
    with zipfile.ZipFile(repo / ARCHIVE) as archive:
        for name in archive.namelist():
            if name != 'db/schema.sql':
                from migration_source import _archive_member
                _archive_member(repo, name, baseline, None)
    print(f'{len(baseline.migrations)} migration hashes and exact historical snapshot ledger verified')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--generate', action='store_true')
    args = parser.parse_args()
    if args.generate:
        raw = generate(args.repo)
        (args.repo / ARCHIVE).write_bytes(raw)
        print(f'{ARCHIVE}: {len(raw)} bytes; sha256 {hashlib.sha256(raw).hexdigest()}')
    verify(args.repo)


if __name__ == '__main__':
    main()
