"""Current-main union and integration-time migration/registry allocation.

Used by registry generator writes and disposable database proofs. This module
never changes applied artifacts or guesses how a domain generator renders SQL.
The source author renders the allocated successor, then proves its committed tree.
This module does not execute arbitrary renderers or supervise their processes.
"""
from __future__ import annotations
import hashlib
import fcntl
import os
import tempfile
import json
import re
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ops'))
from git_env import scrubbed_env
from migration_number_contract import (
    MigrationNumberError, allocate_integration_successors,
    allocate_registry_successor, validate_integration_union,
)

REGISTRY = re.compile(r'^mcp-server/src/scac-mutation-registry\.v([1-9][0-9]*)\.generated\.js$')
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
    paths = git(repo, 'ls-tree', '-r', '--name-only', base, '--', 'migrations', 'mcp-server/src').decode().splitlines()
    return {p: git(repo, 'show', f'{base}:{p}') for p in paths
            if (p.startswith('migrations/') and p.endswith('.sql')) or REGISTRY.fullmatch(p) or p=='mcp-server/src/scac-mutation-registry.generated.js'}


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


CHAIN_PATH = 'ops/config/scac-registry-chain.json'
CURRENT_PATH = 'mcp-server/src/scac-mutation-registry.current.generated.js'


def main_registry_pins(repo: Path, base: str) -> list[dict]:
    paths = git(repo, 'ls-tree', '-r', '--name-only', base, '--', CHAIN_PATH).decode().splitlines()
    if CHAIN_PATH in paths:
        return json.loads(git(repo, 'show', f'{base}:{CHAIN_PATH}'))['versions']
    return [{'number': int(match.group(1)) if match else 1,
             'artifact_sha256': hashlib.sha256(content).hexdigest()}
            for path, content in main_snapshot(repo, base).items()
            if (match := REGISTRY.fullmatch(path)) or path == 'mcp-server/src/scac-mutation-registry.generated.js']


def validate_registry_history(repo: Path, base: str) -> list[dict]:
    before = main_registry_pins(repo, base)
    candidate = json.loads((repo / CHAIN_PATH).read_text())['versions']
    by_number = {row['number']: row for row in candidate}
    for pin in before:
        row = by_number.get(pin['number'])
        if row is None or any(row.get(key) != value for key, value in pin.items()):
            raise MigrationNumberError('applied registry history pin was rewritten')
    frontier = max(row['number'] for row in before)
    added = sorted(row['number'] for row in candidate if row['number'] > frontier)
    if added != list(range(frontier+1, frontier+1+len(added))):
        raise MigrationNumberError('registry must be an ordered successor of current main')
    current = candidate[-1]
    content = (repo / CURRENT_PATH).read_bytes()
    if hashlib.sha256(content).hexdigest() != current['artifact_sha256']:
        raise MigrationNumberError('current runtime artifact pin drifted')
    return before


def allocation_plan(repo: Path, base: str, pending: list[str]) -> dict:
    snapshot = main_snapshot(repo, base)
    migrations = [Path(p).name for p in snapshot if p.startswith('migrations/')]
    pins = main_registry_pins(repo, base)
    predecessor, successor = allocate_registry_successor([row['number'] for row in pins if row['number'] != 1])
    predecessor_pin = next(row for row in pins if row['number'] == predecessor)
    return {'schema': 'integration-allocation/v1', 'base': base,
            'migration_names': allocate_integration_successors(migrations, pending),
            'registry_predecessor': predecessor, 'registry_successor': successor,
            'predecessor_sha256': predecessor_pin['artifact_sha256']}


def validate_candidate(repo: Path, base: str) -> dict:
    require_current_base(repo, base)
    if git(repo, "status", "--porcelain").strip():
        raise MigrationNumberError("commit the integrated candidate before exact-source proof")
    main = main_snapshot(repo, base)
    migrations = {Path(p).name: b for p,b in main.items() if p.startswith('migrations/')}
    candidate = {p.name: p.read_bytes() for p in (repo/'migrations').glob('*.sql')}
    validate_integration_union(migrations, candidate)
    main_versions = []
    manifest = (repo / CHAIN_PATH).is_file()
    if manifest:
        main_versions = [row['number'] for row in validate_registry_history(repo, base) if row['number'] != 1]
    for p, content in main.items():
        if p.startswith('mcp-server/src/'):
            if manifest:
                # validate_registry_history already preserves these Git byte pins.
                continue
            match = REGISTRY.fullmatch(p)
            if match: main_versions.append(int(match.group(1)))
            if not (repo/p).is_file() or (repo/p).read_bytes() != content:
                raise MigrationNumberError(f'applied registry missing or edited: {p}')
    predecessor, successor = allocate_registry_successor(main_versions)
    new = []
    for candidate_path in (repo/'mcp-server/src').glob('scac-mutation-registry.v*.generated.js'):
        relative = candidate_path.relative_to(repo).as_posix()
        match = REGISTRY.fullmatch(relative)
        if match and relative not in main:
            version = int(match.group(1)); new.append(version)
            expected = f'export const SCAC_MUTATION_REGISTRY_VERSION = "scac-mutation-registry.v{version}";'
            if expected not in candidate_path.read_text():
                raise MigrationNumberError('generated registry filename/version mismatch')
    if sorted(new) != list(range(successor, successor + len(new))):
        raise MigrationNumberError('registry must be an ordered successor of current main')
    return {'schema': 'integration-source/v1', 'base': base,
            'head': git(repo, 'rev-parse', 'HEAD').decode().strip(),
            'tree': git(repo, 'rev-parse', 'HEAD^{tree}').decode().strip(),
            'registry_predecessor': predecessor,
            'pending_migrations': sorted(set(candidate)-set(migrations))}


def check_generated_write(repo: Path, target: Path, content: bytes, base: str) -> None:
    """Guard the actual generator sink, preserving byte-exact historical rebuilds."""
    require_current_base(repo, base, pending_merge=True)
    relative = target.resolve().relative_to(repo.resolve()).as_posix()
    if relative == CURRENT_PATH:
        pins = main_registry_pins(repo, base)
        frontier = max(row['number'] for row in pins)
        current_match = re.search(rb'SCAC_MUTATION_REGISTRY_VERSION = "scac-mutation-registry.v([0-9]+)";', content)
        if not current_match:
            raise MigrationNumberError('current registry lacks a version')
        number = int(current_match[1])
        if number == frontier:
            pin = next(row for row in pins if row['number'] == frontier)
            if hashlib.sha256(content).hexdigest() != pin['artifact_sha256']:
                raise MigrationNumberError('sealed main artifact cannot be resealed')
        elif number != frontier + 1:
            raise MigrationNumberError('current runtime must be the next registry successor')
        return
    main = git(repo,'ls-tree','-r','--name-only',base,'--','migrations','mcp-server/src').decode().splitlines()
    if relative in main:
        if content != git(repo,'show',f'{base}:{relative}') and (relative.startswith('migrations/') or REGISTRY.fullmatch(relative) or relative=='mcp-server/src/scac-mutation-registry.generated.js'):
            raise MigrationNumberError('sealed main artifact cannot be resealed; allocate a successor at integration')
        return
    if relative.startswith('migrations/'):
        if Path(relative).parent.as_posix() != 'migrations':
            raise MigrationNumberError('generated migrations require the canonical migrations directory')
        pending = sorted(p.name for p in (repo/'migrations').glob('*.sql')
                         if 'migrations/'+p.name not in main and p.resolve() != target.resolve())
        names = [Path(p).name for p in main if p.startswith('migrations/')]
        validate_integration_union({n: b'' for n in names}, {**{n: b'' for n in names},
                                  **{n: b'' for n in pending}, target.name: b''})
    elif (match := REGISTRY.fullmatch(relative)):
        versions = [int(version_match.group(1)) for p in main if (version_match := REGISTRY.fullmatch(p))]
        predecessor, successor = allocate_registry_successor(versions)
        if int(match.group(1)) != successor:
            raise MigrationNumberError(f'generator must allocate v{successor} after current-main v{predecessor}')
    else:
        # The inventory also emits non-seal fixtures; their existing checks apply.
        return


def _ownership_path(repo: Path) -> Path:
    common = Path(git(repo, 'rev-parse', '--git-common-dir').decode().strip())
    if not common.is_absolute(): common = repo/common
    return common/'integration-generation.lock'


@contextmanager
def _exclusive(lock_path: Path):
    """Yield whether this process now holds the shared Git-root generation lock."""
    with lock_path.open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: yield False
        else: yield True


def write_generated_artifact(repo: Path, target: Path, content: bytes) -> None:
    """Publish a successor under the shared Git-root generation lock.

    Historical artifacts stay create-only. The current projection can advance
    from its pinned predecessor; byte-exact retries leave its inode unchanged.
    """
    repo, target = repo.resolve(), target.resolve()
    with _exclusive(_ownership_path(repo)) as acquired:
        if not acquired:
            raise MigrationNumberError('integration generation already owned')
        base = git(repo, 'rev-parse', 'origin/main').decode().strip()
        head = git(repo, 'rev-parse', 'HEAD').decode().strip()
        check_generated_write(repo, target, content, base)
        previous = target.read_bytes() if target.exists() else None
        replacing_current = target.relative_to(repo).as_posix() == CURRENT_PATH and previous is not None
        if previous == content:
            require_current_base(repo, base, pending_merge=True)
            return
        if previous is not None:
            if not replacing_current:
                raise MigrationNumberError('existing artifact differs; allocate a fresh successor')
            pin = max(main_registry_pins(repo, base), key=lambda row: row['number'])
            if hashlib.sha256(previous).hexdigest() != pin['artifact_sha256']:
                raise MigrationNumberError('current predecessor differs from its sealed pin')
        fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=f'.{target.name}.')
        try:
            with os.fdopen(fd, 'wb') as out:
                out.write(content); out.flush(); os.fsync(out.fileno())
            os.chmod(temporary, 0o644)
            require_current_base(repo, base, pending_merge=True)
            if git(repo, 'rev-parse', 'HEAD').decode().strip() != head:
                raise MigrationNumberError('HEAD moved during artifact publication')
            if replacing_current:
                if target.read_bytes() != previous:
                    raise MigrationNumberError('current predecessor changed during publication')
                os.replace(temporary, target)
            else:
                try:
                    os.link(temporary, target)
                except FileExistsError:
                    raise MigrationNumberError('artifact appeared during publication; refresh main and allocate again') from None
            # A racing main update can invalidate a newly created candidate,
            # but can never make this sink overwrite a promoted seal. Refuse
            # success and leave the candidate visible for source reconciliation.
            require_current_base(repo, base, pending_merge=True)
            if git(repo, 'rev-parse', 'HEAD').decode().strip() != head:
                raise MigrationNumberError('HEAD moved during artifact publication')
        finally:
            Path(temporary).unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    import argparse
    class SanitizedParser(argparse.ArgumentParser):
        def error(self, message):
            self.exit(78, 'integration candidate refused: unsupported arguments; use allocation, --write or --verify\n')
    parser = SanitizedParser(description=__doc__)
    parser.add_argument('--base')
    parser.add_argument('--pending', action='append', default=[])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--write', type=Path, help='owned check-and-write of stdin bytes to one source artifact')
    mode.add_argument('--verify', action='store_true')
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    try:
        if args.write:
            write_generated_artifact(repo, args.write, sys.stdin.buffer.read())
            return 0
        if args.base is None: raise MigrationNumberError('an exact --base is required')
        print(json.dumps(validate_candidate(repo,args.base) if args.verify else allocation_plan(repo,args.base,args.pending), sort_keys=True))
        return 0
    except MigrationNumberError as exc:
        print(f'integration candidate refused: {exc}', file=sys.stderr)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        # Other exception text can carry renderer argv, paths or child output.
        print(f'integration candidate refused: {type(exc).__name__}', file=sys.stderr)
    return 78


if __name__ == '__main__':
    raise SystemExit(main())
