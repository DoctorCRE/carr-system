#!/usr/bin/env python3
"""Merge current main and regenerate successor artifacts on disposable Postgres.

All preparation happens in an isolated local clone. The caller is advanced
only after generation and the successor-only comparison succeed.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import importlib.util

from git_env import scrubbed_env
from successor_ownership import domain_bytes, is_owned_file, REGISTRY_JS, _LEDGER, ACTIVE_IMPORT, validate_outputs, JSON_ARTIFACTS
from successor_generation import regenerate

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from integration_candidate import allocation_plan


class RehomeError(ValueError):
    pass


def git(repo: Path, *args: str, allowed: tuple[int, ...] = (0,)) -> bytes:
    result = subprocess.run(["git", *args], cwd=repo, env=scrubbed_env(),
                            capture_output=True, timeout=120)
    if result.returncode not in allowed:
        raise RehomeError(f"Git {args[0]} failed (exit {result.returncode})")
    return result.stdout


def file_at(repo: Path, revision: str, path: str) -> bytes | None:
    if not git(repo, "ls-tree", revision, "--", path).strip():
        return None
    return git(repo, "show", f"{revision}:{path}")


def conflicts(repo: Path, approved: str, main: str) -> list[str]:
    result = git(repo, "merge-tree", "--write-tree", "-z", approved, main,
                 allowed=(0, 1))
    records = result.split(b"\0")[1:]
    paths = set()
    for record in records:
        if not record:
            break
        if b"\t" not in record:
            raise RehomeError("cannot read merge conflict paths")
        paths.add(record.split(b"\t", 1)[1].decode("utf-8"))
    return sorted(paths)


def owned_conflict(repo: Path, base: str, approved: str, main: str, path: str) -> bool:
    before = file_at(repo, base, path)
    ours = file_at(repo, approved, path)
    theirs = file_at(repo, main, path)
    if is_owned_file(path, before, ours):
        return True
    if before is None or ours is None or theirs is None:
        return False
    return domain_bytes(path, before) == domain_bytes(path, ours) == domain_bytes(path, theirs)


def manifest(repo: Path, git_dir: Path, approved: str, main: str, rewritten: list[str]) -> Path:
    target = git_dir / "successor-rehome.json"
    data = {
        "schema": "successor-rehome/v1", "approved_sha": approved,
        "main_sha": main, "new_sha": git(repo, "rev-parse", "HEAD").decode().strip(),
        "rewritten_paths": sorted(rewritten),
    }
    temporary = git_dir / "successor-rehome.json.tmp"
    with temporary.open("w") as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)
    return target


def commit(repo, message):
    target = Path(git(repo, "rev-parse", "--absolute-git-dir").decode().strip()) / "successor-message"
    target.write_text(message + "\n")
    git(repo, "commit", "-F", str(target))



def prepare(repo, base, approved, main, conflict_paths):
    staging = Path(tempfile.mkdtemp(prefix="successor-integration-")) / "repo"
    git(repo, "clone", "--quiet", "--no-checkout", "--shared", str(repo), str(staging))
    for setting in ('user.name', 'user.email'):
        identity = git(repo, 'config', '--get', setting, allowed=(0, 1)).decode().strip()
        if identity:
            git(staging, 'config', setting, identity)
    git(staging, "remote", "set-url", "origin", git(repo, "remote", "get-url", "origin").decode().strip())
    git(staging, "fetch", "--quiet", str(repo), main)
    git(staging, "update-ref", "refs/remotes/origin/main", main)
    git(staging, "switch", "--quiet", "-c", "successor-integration", approved)
    git(staging, "merge", "--no-ff", "--no-commit", main, allowed=(0, 1))
    unresolved = git(staging, "diff", "--name-only", "--diff-filter=U").decode().splitlines()
    if unresolved != conflict_paths:
        raise RehomeError("merge changed since preflight; staging retained at " + str(staging))
    sinks = ['bin/schema-snapshot.sh', 'ops/schema-snapshot-registry-seed-selftest.py',
             'mcp-server/test/siep-11-mutation-registry.test.mjs', 'mcp-server/src/mutation-registry.js', *JSON_ARTIFACTS]
    sinks += git(staging, 'ls-files', '--', 'migrations', 'mcp-server/src/*generated.js').decode().splitlines()
    validate_outputs(staging, sinks)
    for path in conflict_paths:
        theirs = file_at(repo, main, path)
        if theirs is None:
            old = staging / path
            if old.exists():
                old.rename(staging / ".git" / ("superseded-" + old.name))
            git(staging, "add", "--", path)
        else:
            git(staging, "restore", "--source", main, "--staged", "--worktree", "--", path)
    added = git(repo, "diff", "--diff-filter=A", "--name-only", base, approved, "--", "migrations", "mcp-server/src").decode().splitlines()
    sql = [p for p in added if p.startswith('migrations/') and is_owned_file(p, None, file_at(repo, approved, p))]
    pending = [p for p in added if p.startswith('migrations/') and p.endswith('.sql')]
    regenerated = set(conflict_paths)
    if sql:
        if len(sql) != 1:
            raise RehomeError("one successor seal is required per rehome: " + ', '.join(sql))
        plan = allocation_plan(repo, main, [Path(p).name for p in pending])
        for path in pending:
            new = 'migrations/' + plan['migration_names'][Path(path).name]
            if file_at(repo, main, path) is not None:
                raise RehomeError("applied migration identity cannot be reallocated: " + path)
            target = staging / path
            if path == sql[0]:
                if target.exists():
                    target.rename(staging / '.git' / ('superseded-' + target.name))
            elif new != path:
                target.rename(staging / new)
            regenerated.update((path, new))
        for path in added:
            if REGISTRY_JS.fullmatch(path) and is_owned_file(path, None, file_at(repo, approved, path)) and file_at(repo, main, path) is None:
                (staging / path).rename(staging / '.git' / ('superseded-' + Path(path).name))
                regenerated.add(path)
        for path in ('bin/schema-snapshot.sh', 'ops/schema-snapshot-registry-seed-selftest.py', 'mcp-server/test/siep-11-mutation-registry.test.mjs', *JSON_ARTIFACTS, 'mcp-server/src/scac-mutation-registry.current.generated.js'):
            if file_at(repo, main, path) is not None:
                # Whole generated JSON and pure bookkeeping can be rebuilt.
                if path in JSON_ARTIFACTS or REGISTRY_JS.fullmatch(path) or domain_bytes(path, file_at(repo, base, path)) == domain_bytes(path, file_at(repo, approved, path)):
                    git(staging, 'restore', '--source', main, '--staged', '--worktree', '--', path)
                    regenerated.add(path)
        changed = git(staging, 'diff', '--name-only').decode().splitlines()
        if changed:
            git(staging, 'add', '--', *changed)
    git_dir = Path(git(staging, "rev-parse", "--absolute-git-dir").decode().strip())
    if (git_dir / "MERGE_HEAD").exists():
        commit(staging, "Merge current main for successor integration")
    if sql:
        modules = staging / 'mcp-server/node_modules'
        installed = Path(__file__).resolve().parents[1] / 'mcp-server/node_modules'
        if installed.is_dir():
            modules.symlink_to(installed, target_is_directory=True)
        else:
            raise RehomeError('install the rehome command checkout dependencies with npm ci in mcp-server')
        main_migrations = git(repo, 'ls-tree', '-r', '--name-only', main, '--', 'migrations').decode().splitlines()
        version = plan['registry_predecessor']
        matches = [p for p in main_migrations if p.endswith('.sql') and is_owned_file(p, None, file_at(repo, main, p)) and f"values ('scac-mutation-registry.v{version}',".encode() in file_at(repo, main, p)]
        if len(matches) != 1:
            raise RehomeError("cannot identify current-main successor SQL template")
        seal_path = staging / 'migrations' / plan['migration_names'][Path(sql[0]).name]
        domains = [staging / 'migrations' / plan['migration_names'][Path(p).name] for p in pending if p != sql[0]]
        validate_outputs(staging, [str(seal_path.relative_to(staging)), 'mcp-server/src/scac-mutation-registry.current.generated.js'])
        receipt = regenerate(staging, plan, domains, seal_path, staging / matches[0])
        regenerated.add('mcp-server/src/scac-mutation-registry.current.generated.js')
        regenerated.add('ops/config/scac-registry-chain.json')
        changed = git(staging, 'diff', '--name-only').decode().splitlines()
        untracked = git(staging, 'ls-files', '--others', '--exclude-standard').decode().splitlines()
        git(staging, 'add', '--', *sorted(set(changed + untracked)))
        commit(staging, "Regenerate successor from disposable current-main replay")
    check = subprocess.run([sys.executable, str(Path(__file__).with_name('successor-only-diff.py')), approved, git(staging, 'rev-parse', 'HEAD').decode().strip()], cwd=staging, env=scrubbed_env(), capture_output=True, timeout=120)
    if check.returncode:
        raise RehomeError("domain patch changed; staging retained at " + str(staging) + ': ' + check.stderr.decode().strip())
    return staging, sorted(regenerated)


def rehome(repo: Path) -> Path:
    repo = repo.resolve(strict=True)
    root = Path(git(repo, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    if repo != root:
        raise RehomeError("pass the worktree root")
    branch = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", allowed=(0, 1)).decode().strip()
    if not branch or branch == "main":
        raise RehomeError("use an isolated feature branch")
    git_dir = Path(git(repo, "rev-parse", "--absolute-git-dir").decode().strip())
    with (git_dir / "successor-rehome.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RehomeError("another successor rehome owns this worktree") from None
        if git(repo, "status", "--porcelain").strip():
            raise RehomeError("commit the worktree changes before rehome")
        for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"):
            if (git_dir / name).exists():
                raise RehomeError(f"unfinished Git operation: {name}")
        git(repo, "fetch", "--quiet", "origin", "main")
        main = git(repo, "rev-parse", "origin/main").decode().strip()
        approved = git(repo, "rev-parse", "HEAD").decode().strip()
        bases = git(repo, "merge-base", "--all", approved, main).decode().splitlines()
        if len(bases) != 1:
            raise RehomeError("rehome requires one merge base")
        base = bases[0]
        conflict_paths = conflicts(repo, approved, main)
        refused = [p for p in conflict_paths if not owned_conflict(repo, base, approved, main, p)]
        if refused:
            raise RehomeError("conflict outside successor ownership: " + ", ".join(refused))
        staging, rewritten = prepare(repo, base, approved, main, conflict_paths)
        if git(repo, 'symbolic-ref', '--quiet', '--short', 'HEAD', allowed=(0, 1)).decode().strip() != branch or git(repo, 'rev-parse', 'HEAD').decode().strip() != approved or git(repo, 'status', '--porcelain').strip():
            raise RehomeError('worktree changed during preparation; staging retained at ' + str(staging))
        git(repo, 'fetch', '--quiet', 'origin', 'main')
        if git(repo, 'rev-parse', 'origin/main').decode().strip() != main:
            raise RehomeError('main changed during preparation; staging retained at ' + str(staging))
        git(repo, 'fetch', '--quiet', str(staging), 'HEAD')
        new = git(repo, 'rev-parse', 'FETCH_HEAD').decode().strip()
        ref = 'refs/heads/' + branch
        if git(repo, 'symbolic-ref', '--quiet', 'HEAD', allowed=(0, 1)).decode().strip() != ref:
            raise RehomeError('selected branch changed during promotion; staging retained at ' + str(staging))
        git(repo, 'update-ref', ref, new, approved)
        if git(repo, 'symbolic-ref', '--quiet', 'HEAD', allowed=(0, 1)).decode().strip() != ref:
            raise RehomeError('selected branch changed before worktree update; staging retained at ' + str(staging))
        git(repo, 'read-tree', '-u', '-m', approved, new)
        return manifest(repo, git_dir, approved, main, rewritten)


def main() -> int:
    python = Path(__file__).resolve().parents[1] / '.venv/bin/python'
    if importlib.util.find_spec('psycopg') is None and python.is_file() and Path(sys.executable) != python:
        return subprocess.call([str(python), str(Path(__file__).resolve()), *sys.argv[1:]], env=scrubbed_env())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("worktree", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps({"ok": True, "manifest": str(rehome(args.worktree))}, sort_keys=True))
        return 0
    except (RehomeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"successor rehome refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
