"""Immutable baseline identities shared by migration ledger consumers.

Coverage never authorizes SQL execution or creates a database ledger row.
The snapshot digest binds the artifact at source_revision, not a later snapshot
extended by active suffix migrations.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from migration_number_contract import SLOT_RE, validate_migration_names

MANIFEST = 'db/migration-baseline.json'
SCHEMA = 'carr-migration-baseline/v1'


def runner_sha256(text: str) -> str:
    return hashlib.sha256(text.replace('\r\n', '\n').replace('\r', '\n').encode()).hexdigest()


@dataclass(frozen=True)
class MigrationBaseline:
    source_revision: str
    snapshot_sha256: str
    cutoff: str
    migrations: tuple[tuple[str, str], ...]

    @classmethod
    def from_document(cls, document: dict) -> MigrationBaseline:
        def refuse(message):
            raise ValueError('migration baseline ledger: ' + message)

        if not isinstance(document, dict) or set(document) != {
                'schema', 'source_revision', 'snapshot', 'cutoff', 'migrations'}:
            refuse('unexpected manifest fields')
        if document['schema'] != SCHEMA:
            refuse('unsupported manifest schema')
        revision = document['source_revision']
        if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}', revision):
            refuse('source revision must be an exact Git commit')
        snapshot = document['snapshot']
        if not isinstance(snapshot, dict) or set(snapshot) != {'path', 'sha256'} \
                or snapshot['path'] != 'db/schema.sql':
            refuse('snapshot must bind db/schema.sql')
        if not isinstance(snapshot['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', snapshot['sha256']):
            refuse('invalid snapshot digest')
        rows = document['migrations']
        if not isinstance(rows, list) or not rows:
            refuse('coverage must be a nonempty ordered filename ledger')
        migrations = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {'filename', 'sha256'}:
                refuse('invalid migration row')
            name, digest = row['filename'], row['sha256']
            if not isinstance(name, str) or not SLOT_RE.fullmatch(name):
                refuse('invalid migration filename')
            if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
                refuse(f'invalid checksum for {name}')
            migrations.append((name, digest))
        names = [name for name, _ in migrations]
        if names != sorted(set(names)):
            refuse('filenames must be unique and lexically ordered')
        if document['cutoff'] != names[-1]:
            refuse('cutoff must be the final covered filename')
        validate_migration_names(names)
        return cls(revision, snapshot['sha256'], document['cutoff'], tuple(migrations))

    @classmethod
    def load(cls, repository: Path) -> MigrationBaseline:
        try:
            document = json.loads((repository / MANIFEST).read_bytes())
        except (OSError, ValueError) as exc:
            raise ValueError(f'migration baseline ledger: cannot read {MANIFEST}: {exc}') from exc
        return cls.from_document(document)

    def repository_ledger(self, files: Mapping[str, str]) -> dict[str, str]:
        ledger = dict(self.migrations)
        for name, text in files.items():
            if not SLOT_RE.fullmatch(name):
                raise ValueError(f'migration baseline ledger: invalid filename {name}')
            digest = runner_sha256(text)
            if name in ledger:
                if ledger[name] != digest:
                    raise ValueError(f'migration baseline ledger: retained {name} checksum differs')
            elif name <= self.cutoff:
                raise ValueError(f'migration baseline ledger: unregistered historical file {name}')
            ledger[name] = digest
        validate_migration_names(ledger)
        return dict(sorted(ledger.items()))

    def validate_coverage(self, applied: Mapping[str, str]) -> None:
        for name, digest in self.migrations:
            if name not in applied:
                raise ValueError(f'migration baseline ledger: missing applied coverage for {name}')
            if applied[name] != digest:
                raise ValueError(f'migration baseline ledger: applied checksum differs for {name}')


def repository_ledger(repository: Path, baseline: MigrationBaseline) -> dict[str, str]:
    directory = repository / 'migrations'
    if not directory.is_dir():
        raise ValueError('migration baseline ledger: migrations directory is missing')
    files = {path.name: path.read_text() for path in directory.glob('*.sql')}
    ledger = baseline.repository_ledger(files)
    validate_migration_names(ledger, require_frozen=True)
    return ledger
