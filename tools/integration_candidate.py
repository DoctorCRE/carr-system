"""Validate candidate migrations against current main for disposable database proofs."""
from __future__ import annotations
import re
import subprocess
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ops'))
from git_env import scrubbed_env
from migration_number_contract import (
    MigrationNumberError, validate_integration_union,
)

SHA = re.compile(r'^[0-9a-f]{40}$')


def git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(['git', *args], cwd=repo, env=scrubbed_env(), capture_output=True, timeout=60)
    if result.returncode:
        raise MigrationNumberError('integration Git read failed; refresh the exact base')
    return result.stdout


def main_snapshot(repo: Path, base: str) -> dict[str, bytes]:
    if not SHA.fullmatch(base):
        raise MigrationNumberError('integration base must be an exact commit SHA')
    if git(repo, 'rev-parse', 'origin/main').decode().strip() != base:
        raise MigrationNumberError('integration base differs from current origin/main')
    paths = git(repo, 'ls-tree', '-r', '--name-only', base, '--', 'migrations').decode().splitlines()
    return {p: git(repo, 'show', f'{base}:{p}') for p in paths
            if p.endswith('.sql')}


def require_current_base(repo: Path, base: str, *, pending_merge: bool = False) -> None:
    if not SHA.fullmatch(base) or git(repo, "rev-parse", "origin/main").decode().strip() != base:
        raise MigrationNumberError("integration base differs from current origin/main")
    result = subprocess.run(['git', 'merge-base', '--is-ancestor', base, 'HEAD'], cwd=repo, env=scrubbed_env(), capture_output=True, timeout=60)
    if result.returncode:
        if not pending_merge or git(repo, 'rev-parse', 'MERGE_HEAD').decode().strip() != base:
            raise MigrationNumberError('integrate current main before generation or proof')
        for path, content in main_snapshot(repo, base).items():
            if not (repo/path).is_file() or (repo/path).read_bytes() != content:
                raise MigrationNumberError(f'applied artifact missing or edited during merge: {path}')


def validate_candidate(repo: Path, base: str) -> dict:
    require_current_base(repo, base)
    if git(repo, "status", "--porcelain").strip():
        raise MigrationNumberError("commit the integrated candidate before exact-source proof")
    main = main_snapshot(repo, base)
    migrations = {Path(p).name: b for p,b in main.items() if p.startswith('migrations/')}
    candidate = {p.name: p.read_bytes() for p in (repo/'migrations').glob('*.sql')}
    validate_integration_union(migrations, candidate)
    return {'schema': 'integration-source/v1', 'base': base,
            'head': git(repo, 'rev-parse', 'HEAD').decode().strip(),
            'tree': git(repo, 'rev-parse', 'HEAD^{tree}').decode().strip(),
            'pending_migrations': sorted(set(candidate)-set(migrations))}
