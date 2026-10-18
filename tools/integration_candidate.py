"""Validate candidate migrations against current main for disposable database proofs."""
from __future__ import annotations
import re
import subprocess
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ops'))
from git_env import scrubbed_env
from migration_baseline import MANIFEST
from migration_source import migration_names, read_migration
from migration_number_contract import (
    MigrationNumberError, validate_integration_union,
)

SHA = re.compile(r'^[0-9a-f]{40}$')


def git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(['git', *args], cwd=repo, env=scrubbed_env(), capture_output=True, timeout=60)
    if result.returncode:
        raise MigrationNumberError('integration Git read failed; refresh the exact base')
    return result.stdout


def require_baseline_unchanged(repo: Path, base: str, ref: str | None = None,
                               *, inherited_from: str | None = None) -> None:
    """An existing baseline keeps its exact provenance during integration."""
    def at(revision: str) -> bytes | None:
        return git(repo, 'show', f'{revision}:{MANIFEST}') if git(
            repo, 'ls-tree', '--name-only', revision, '--', MANIFEST).strip() else None

    expected = at(base)
    if expected is None:
        return
    if ref is None:
        path = repo / MANIFEST
        observed = path.read_bytes() if path.is_file() else None
    else:
        observed = at(ref)
    if observed is None and inherited_from is not None and at(inherited_from) is None:
        return
    if observed != expected:
        raise MigrationNumberError('current-main migration baseline cannot be edited or deleted')


def main_snapshot(repo: Path, base: str) -> dict[str, bytes]:
    if not SHA.fullmatch(base):
        raise MigrationNumberError('integration base must be an exact commit SHA')
    if git(repo, 'rev-parse', 'origin/main').decode().strip() != base:
        raise MigrationNumberError('integration base differs from current origin/main')
    return {f'migrations/{name}': read_migration(repo, name, base).encode()
            for name in sorted(migration_names(repo, base))}


def require_current_base(repo: Path, base: str, *, pending_merge: bool = False) -> None:
    if not SHA.fullmatch(base) or git(repo, "rev-parse", "origin/main").decode().strip() != base:
        raise MigrationNumberError("integration base differs from current origin/main")
    require_baseline_unchanged(repo, base)
    result = subprocess.run(['git', 'merge-base', '--is-ancestor', base, 'HEAD'], cwd=repo, env=scrubbed_env(), capture_output=True, timeout=60)
    if result.returncode:
        if not pending_merge or git(repo, 'rev-parse', 'MERGE_HEAD').decode().strip() != base:
            raise MigrationNumberError('integrate current main before generation or proof')
        for path, content in main_snapshot(repo, base).items():
            if Path(path).name not in migration_names(repo) or read_migration(repo, Path(path).name).encode() != content:
                raise MigrationNumberError(f'applied artifact missing or edited during merge: {path}')


def validate_candidate(repo: Path, base: str) -> dict:
    require_current_base(repo, base)
    if git(repo, "status", "--porcelain").strip():
        raise MigrationNumberError("commit the integrated candidate before exact-source proof")
    main = main_snapshot(repo, base)
    migrations = {Path(p).name: b for p,b in main.items() if p.startswith('migrations/')}
    candidate = {name: read_migration(repo, name).encode()
                 for name in sorted(migration_names(repo))}
    validate_integration_union(migrations, candidate)
    return {'schema': 'integration-source/v1', 'base': base,
            'head': git(repo, 'rev-parse', 'HEAD').decode().strip(),
            'tree': git(repo, 'rev-parse', 'HEAD^{tree}').decode().strip(),
            'pending_migrations': sorted(set(candidate)-set(migrations))}
