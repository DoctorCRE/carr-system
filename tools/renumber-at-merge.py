#!/usr/bin/env python3
"""Renumber a PR's pending migrations after rebasing onto fetched origin/main.

The serialized merge queue runs these commands on its owned PR branch:
  git fetch origin main
  git rebase origin/main
  python3 tools/renumber-at-merge.py
  git add -- <changed paths reported in the JSON summary>
  printf '%s\n' 'chore: renumber migrations at merge' > /tmp/renumber-commit.txt
  git commit -F /tmp/renumber-commit.txt
  git push --force-with-lease origin HEAD
  gh pr checks <PR> --watch
Skip add/commit when the summary's changed_paths is empty. CI remains the gate.

--dry-run prints the validated plan without writes or artifact generation. --branch REF is a
read-only preview of that branch's delta over current main, even before rebase;
requires_rebase records that limitation. It never checks out or pushes the REF.

One stale/colliding slot reallocates the ordered pending batch via the existing
integration allocator. This preserves execution order between dependent files.
Only branch-added migrations move. Main files and its snapshot ledger are
immutable evidence; no live database or provider credential is consulted.

Snapshots regenerate through ops/migration-shadow.py --write (disposable PG18).
SCAC runtime projections use the owning registry renderer. For other generated
outputs, supply their owning command with --generator PATH JSON_ARGV, e.g.
--generator migrations/0010_feature.sql '["python3","tools/render-feature.py"]'.
The PATH identifies the original output; the generator must emit its allocated
name and updated references. Commands are argv arrays, never shell snippets.
Declare each output of a multi-output generator separately for rollback.
References are full filenames and explicit migration labels/keys. Unrelated
numeric SQL values and string codes retain their bytes.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import difflib
import hashlib
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops"))
from git_env import scrubbed_env
from integration_candidate import require_baseline_unchanged
from migration_baseline import MANIFEST
from migration_source import migration_names, read_migration
from migration_number_contract import (
    MigrationNumberError, SLOT_RE, allocate_integration_successors,
    validate_integration_union,
)

SNAPSHOT = "db/schema.sql"
RUNTIME = re.compile(r"^mcp-server/src/scac-mutation-registry\.(?:current|v[1-9][0-9]*)\.generated\.js$")


def git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=repo, env=scrubbed_env(),
                            capture_output=True, timeout=60)
    if result.returncode:
        raise MigrationNumberError(f"git {args[0]} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def blob(repo: Path, revision: str, path: str) -> bytes | None:
    if not git(repo, "ls-tree", revision, "--", path):
        return None
    return git(repo, "show", f"{revision}:{path}")


def paths(repo: Path, revision: str) -> list[str]:
    return [p.decode() for p in git(repo, "ls-tree", "-rz", "--name-only", revision).split(b"\0") if p]


def read_file(repo: Path, path: str) -> bytes:
    target = repo / path
    if target.is_symlink() or not target.is_file() or not target.resolve().is_relative_to(repo):
        raise MigrationNumberError(f"expected a regular source file: {path}")
    return target.read_bytes()


def applied_names(snapshot: bytes | None) -> set[str]:
    if snapshot is None:
        return set()
    spec = importlib.util.spec_from_file_location("renumber_migration_safety", ROOT / "ops/migration-safety-gate.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    ledger = module.ledger_rows(snapshot.decode())
    if ledger is None:
        raise MigrationNumberError("main snapshot has no complete migration ledger")
    return set(ledger)


def rewrite(content: bytes, before: bytes | None, renames: dict[str, str], main_names: set[str], pending_identity: str | None = None) -> bytes:
    try:
        text = content.decode("utf-8")
        old_text = (before or b"").decode("utf-8")
    except UnicodeDecodeError:
        return content
    slots: dict[str, set[str]] = {}
    for old, new in renames.items():
        slots.setdefault(old[:4], set()).add(new[:4])
    identities = set(renames) | main_names
    filenames = re.compile(r"(?<![\w.-])(?:" + "|".join(re.escape(n) for n in sorted(identities, key=len, reverse=True)) + r")(?![\w-]|\.\w)")
    label_pattern = r"(?i:\bmigration(?:[ _-]?(?:number|slot|no|id))?[ \t:=\'\"#]+)(?:" + "|".join("0*" + str(int(n)) for n in slots) + r")(?![A-Za-z0-9_])"
    pattern = re.compile(filenames.pattern + "|" + label_pattern)
    main_slots = {name[:4] for name in main_names}

    def replace(match, *, labels, line_names):
        value = match[0]
        if value in renames:
            return renames[value]
        if value in main_names or not labels:
            return value
        label = re.fullmatch(r"(.*?)(\d+)", value)
        slot = f"{int(label[2]):04d}" if label else value
        bound = {name for name in line_names if name[:4] == slot}
        if pending_identity and pending_identity[:4] == slot:
            bound.add(pending_identity)
        if bound and bound <= main_names:
            return value
        if len(bound) > 1 or (not bound and slot in main_slots):
            raise MigrationNumberError(f"ambiguous migration label {value}; use the full migration filename")
        choices = slots[slot]
        if len(choices) != 1:
            raise MigrationNumberError(f"ambiguous migration label {value}; use the full migration filename")
        number = next(iter(choices))
        return label[1] + str(int(number)).zfill(len(label[2])) if label else number

    original, current = old_text.splitlines(keepends=True), text.splitlines(keepends=True)
    result: list[str] = []
    for tag, _, _, start, end in difflib.SequenceMatcher(None, original, current, autojunk=False).get_opcodes():
        for line in current[start:end]:
            line_names = {m[0] for m in filenames.finditer(line)}
            result.append(pattern.sub(lambda m: replace(m, labels=tag != "equal", line_names=line_names), line))
    return "".join(result).encode()


@dataclass
class Plan:
    base: str
    head: str
    requires_rebase: bool
    renames: dict[str, str]
    writes: dict[str, bytes]
    commands: list[list[str]]
    generated: dict[str, bytes]
    immutable: dict[str, bytes]

    def summary(self, dry_run: bool) -> dict:
        changed = set(self.writes) | set(self.generated)
        for old, new in self.renames.items():
            changed.update((f"migrations/{old}", f"migrations/{new}"))
        return dict(schema="migration-renumber/v1", base=self.base, head=self.head,
                    dry_run=dry_run, requires_rebase=self.requires_rebase,
                    renames=self.renames, changed_paths=sorted(changed), generators=self.commands)


def plan(repo: Path, branch: str | None, generators: dict[str, list[str]]) -> Plan:
    base = git(repo, "rev-parse", "origin/main^{commit}").decode().strip()
    head = git(repo, "rev-parse", f"{branch or 'HEAD'}^{{commit}}").decode().strip()
    ancestor = git(repo, "merge-base", base, head).decode().strip()
    if not branch and ancestor != base:
        raise MigrationNumberError("rebase the PR branch on current origin/main before renumbering")
    require_baseline_unchanged(repo, base, head if branch else None,
                               inherited_from=ancestor if branch else None)
    base_paths = paths(repo, base)
    main = {name: read_migration(repo, name, base).encode()
            for name in sorted(migration_names(repo, base))}
    comparison = ancestor if branch else base
    delta = git(repo, "diff", "--name-only", "--no-renames", "-z", comparison, *([head] if branch else [])).split(b"\0")
    changed = {p.decode() for p in delta if p}
    if not branch:
        changed.update(p.decode() for p in git(repo, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if p)
    contents = {}
    for path in sorted(changed):
        if branch:
            contents[path] = blob(repo, head, path)
        elif (repo / path).exists() or (repo / path).is_symlink():
            contents[path] = read_file(repo, path)
        else:
            contents[path] = None
    candidate_ref = head if branch else None
    candidate_names = migration_names(repo, candidate_ref)
    fork_names = migration_names(repo, ancestor) if branch else set()
    for name, content in main.items():
        candidate = (read_migration(repo, name, candidate_ref).encode() if name in candidate_names
                     else content if branch and name not in fork_names else None)
        if candidate != content:
            raise MigrationNumberError(f"applied main migration cannot be renamed, deleted or edited: {name}")
    pending = {Path(p).name: content for p, content in contents.items()
               if p.startswith("migrations/") and p.endswith(".sql") and content is not None and Path(p).name not in main}
    for name in pending:
        if not SLOT_RE.fullmatch(name) or not re.fullmatch(r"\d{4}_[a-z0-9_]+\.sql", name):
            raise MigrationNumberError(f"pending migration needs an ordinary numeric filename: {name}")
    applied = applied_names(blob(repo, base, SNAPSHOT))
    if set(pending) & applied:
        raise MigrationNumberError("applied main migration cannot be reallocated: " + ", ".join(sorted(set(pending) & applied)))
    immutable = {path: git(repo, "show", f"{base}:{path}") for path in base_paths
                 if (path.startswith("migrations/") and path.endswith(".sql")) or path == MANIFEST}
    result = Plan(base, head, ancestor != base, {}, {}, [], {}, immutable)
    try:
        validate_integration_union(main, main | pending)
        return result
    except MigrationNumberError:
        allocated = allocate_integration_successors(main, pending)
        result.renames = {old: new for old, new in allocated.items() if old != new}
    if not result.renames:
        return result
    for path, content in contents.items():
        if content is None:
            continue
        identity = Path(path).name if path.startswith("migrations/") and Path(path).name in pending else None
        transformed = rewrite(content, blob(repo, comparison, path), result.renames, set(main), identity)
        output = f"migrations/{result.renames[Path(path).name]}" if path.startswith("migrations/") and Path(path).name in result.renames else path
        if transformed != content or output != path or path in generators:
            if path == SNAPSHOT:
                result.commands.append([sys.executable, "ops/migration-shadow.py", "--base", base, "--write"])
                result.generated[output] = transformed
            elif path in generators:
                command = generators[path]
                if command not in result.commands:
                    result.commands.append(command)
                result.generated[output] = transformed
            elif RUNTIME.fullmatch(path):
                raise MigrationNumberError("declare a generator for the current runtime metadata")
            elif ".generated." in path or re.search(rb"(?i)generated by|auto-generated|do not (?:hand[- ]?)?edit", content[:2048]):
                raise MigrationNumberError(f"generated output needs its owning generator: {path}; supply --generator PATH JSON_ARGV")
            else:
                result.writes[output] = transformed
        if output != path and output not in result.writes and output not in result.generated:
            result.writes[output] = content
    for path, command in generators.items():
        if path not in contents:
            content = blob(repo, head, path) if branch else read_file(repo, path)
            if content is None:
                raise MigrationNumberError(f"declared generator output is missing: {path}")
            result.generated[path] = content
            if command not in result.commands:
                result.commands.append(command)
    if blob(repo, base, SNAPSHOT) is not None and SNAPSHOT not in result.generated:
        result.generated[SNAPSHOT] = b""
        result.commands.append([sys.executable, "ops/migration-shadow.py", "--base", base, "--write"])
    result.commands.sort(key=lambda command: len(command) > 1 and command[1] == "ops/migration-shadow.py")
    validate_integration_union(main, main | {allocated[name]: result.writes.get(f"migrations/{allocated[name]}", pending[name]) for name in pending})
    return result


def run_generator(repo: Path, command: list[str], inputs: bytes | None = None) -> None:
    with subprocess.Popen(command, cwd=repo, env=scrubbed_env(), stdin=subprocess.PIPE if inputs is not None else subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True) as process:
        try:
            _, stderr = process.communicate(input=inputs, timeout=1200)
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        if process.returncode:
            raise MigrationNumberError(f"generator failed ({command[0:2]}): {stderr.decode(errors='replace').strip()}")


def validate_outputs(repo, paths):
    """Refuse nonregular sinks and symlink ancestors before any write."""
    import stat
    root = repo.resolve(strict=True)
    for name in paths:
        target = repo / name
        if not target.is_relative_to(repo) or '..' in target.relative_to(repo).parts:
            raise MigrationNumberError(f"{name}: output escapes staging")
        for part in [target, *target.parents]:
            if part == repo:
                break
            if part.is_symlink():
                raise MigrationNumberError(f"{name}: symlink output or ancestor")
        if not target.resolve().is_relative_to(root):
            raise MigrationNumberError(f"{name}: output escapes staging")
        if target.exists() and not stat.S_ISREG(target.stat().st_mode):
            raise MigrationNumberError(f"{name}: output is not a regular file")


def apply(repo: Path, result: Plan) -> None:
    targets = set(result.writes) | set(result.generated) | {f"migrations/{old}" for old in result.renames} | set(result.immutable)
    validate_outputs(repo, targets)
    backup = {p: read_file(repo, p) if (repo / p).exists() else None for p in targets}
    complete = False
    try:
        for path, content in result.writes.items():
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        for old in result.renames:
            (repo / "migrations" / old).unlink()
        for path in result.generated:
            if path.startswith("migrations/") and (repo / path).exists():
                (repo / path).unlink()
        for command in result.commands:
            run_generator(repo, command)
        for path, content in result.immutable.items():
            if not (repo / path).is_file() or read_file(repo, path) != content:
                raise MigrationNumberError(f"generator changed an applied main migration: {path}")
        validate_integration_union(
            {name: read_migration(repo, name, result.base).encode() for name in migration_names(repo, result.base)},
            {name: read_migration(repo, name).encode() for name in migration_names(repo)},
        )
        for path, expected in result.generated.items():
            observed = read_file(repo, path)
            if path == SNAPSHOT:
                names = applied_names(observed)
                if not set(result.renames.values()) <= names or set(result.renames) & names:
                    raise MigrationNumberError("snapshot generator did not emit the allocated ledger")
            else:
                references = lambda data: set(re.findall(rb"\d{4}[a-z]?_[a-z0-9_]+\.sql", data))
                wanted = references(expected) & {n.encode() for n in result.renames.values()}
                if not wanted <= references(observed) or references(observed) & {n.encode() for n in result.renames}:
                    raise MigrationNumberError(f"generator did not emit the allocated references: {path}")
        if git(repo, "rev-parse", "origin/main").decode().strip() != result.base:
            raise MigrationNumberError("origin/main advanced during renumbering; fetch and rebase again")
        complete = True
    finally:
        if not complete:
            for path, previous in backup.items():
                target = repo / path
                if previous is None:
                    if target.exists():
                        target.unlink()
                else:
                    target.write_bytes(previous)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--branch", help="revision to preview without checkout; requires --dry-run")
    parser.add_argument("--generator", nargs=2, action="append", default=[], metavar=("PATH", "JSON_ARGV"))
    args = parser.parse_args()
    def interrupted(signum, _frame):
        raise KeyboardInterrupt(f"renumbering cancelled by signal {signum}")
    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupted)
    try:
        if args.branch and not args.dry_run:
            raise MigrationNumberError("--branch requires --dry-run")
        generators = {}
        for path, argv in args.generator:
            command = json.loads(argv)
            if not isinstance(command, list) or not command or any(not isinstance(a, str) or not a for a in command):
                raise MigrationNumberError("generator must be a nonempty JSON argv array")
            generators[path] = command
        result = plan(args.repo.resolve(), args.branch, generators)
        if not args.dry_run:
            apply(args.repo.resolve(), result)
        print(json.dumps(result.summary(args.dry_run), indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
        print(json.dumps(dict(schema="migration-renumber/v1", error=str(exc) or "renumbering cancelled", renames={}), sort_keys=True))
        return 130 if isinstance(exc, KeyboardInterrupt) else 1


if __name__ == "__main__":
    sys.exit(main())
