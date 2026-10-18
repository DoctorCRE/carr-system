#!/usr/bin/env python3
"""Fail-closed R03 stage-5 settlement-sweep runner.

The single-use capability supplies the three read-only descriptors this program
accepts for execution. It has no execution manifest-path, allowlist-path, or receipt-path
arguments: the approved bytes must be the bytes the capability admitted.

Authoring uses ``--author-template`` with repeated ``--approve-restore-path``
literal names. It emits an unapproved v2 manifest only after full validation;
the operator admits those bytes through the existing settlement capability.
Restore entries bind pinned blobs and the approved observed index/worktree state.
Execution with a nonempty restore set is held: this runner has no enforced
writer exclusion spanning verification and restoration. Authoring and dry-run
do not grant that missing guarantee.

Without ``--execute`` this runner only obtains and validates the manifest's
``git clean -nd`` diff, prints the full planned sequence, and exits without
changing the repository.  Execute mode refuses the canonical checkout and any
child of it in this build; executable coverage is restricted to disposable
fixtures until an explicitly authorized production activation changes that
boundary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Callable, Iterable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
from r03_settlement_restore_set import RestoreSetRefusal, build_restore_set
from git_env import scrubbed_env


MANIFEST_SCHEMA = "carr.r03-stage5-settlement-sweep.v2"
ALLOWLIST_SCHEMA = "carr.settlement-command-pathspec-allowlist.v1"
RECEIPT_SCHEMA = "carr.settlement-capability-redemption.v1"
CAPABILITY_KEY = "R03C.settlement-capability.v1"
CANONICAL_CHECKOUT = Path(os.environ.get("CARR_ROOT") or (Path.home() / "carr-system"))
MAX_FD_BYTES = 8 * 1024 * 1024


class SweepError(RuntimeError):
    """A fail-closed validation or execution refusal."""


class SweepHeld(SweepError):
    """A stage-5 precondition is not presently true."""


def _read_fd(fd: int, label: str) -> bytes:
    if fd < 0:
        raise SweepError(f"{label} descriptor is invalid")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(fd, 64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_FD_BYTES:
            raise SweepError(f"{label} exceeds {MAX_FD_BYTES} bytes")
        chunks.append(chunk)
    if not chunks:
        raise SweepError(f"{label} descriptor is empty")
    return b"".join(chunks)


def _json_object(body: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SweepError(f"{label} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise SweepError(f"{label} must be a JSON object")
    return value


def _sha256(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _clean_git_env() -> dict[str, str]:
    """Keep direct fixture execution isolated from inherited hook Git state."""
    return scrubbed_env()


def _run(argv: Sequence[str], *, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        raw = subprocess.run(
            list(argv), cwd=str(cwd), env=_clean_git_env(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SweepError(f"command failed or timed out: {argv[0]}") from exc
    completed = subprocess.CompletedProcess(
        raw.args, raw.returncode, raw.stdout.decode("utf-8", "surrogateescape"),
        raw.stderr.decode("utf-8", "surrogateescape"))
    if check and completed.returncode:
        rendered = " ".join(argv)
        raise SweepError(f"command failed ({completed.returncode}): {rendered}\n{completed.stderr.strip()}")
    return completed


def _git(repository: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run(("git", *args), cwd=repository, check=check)


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise SweepError(f"{label} must be a non-empty string")
    return value


def _require_oid(value: object, label: str) -> str:
    oid = _require_string(value, label).lower()
    if len(oid) not in (40, 64) or any(char not in "0123456789abcdef" for char in oid):
        raise SweepError(f"{label} must be a hexadecimal git object id")
    return oid


def _relative_path(value: object, label: str) -> str:
    path = _require_string(value, label)
    candidate = Path(path)
    if candidate.is_absolute() or path in (".", "..") or "\\" in path:
        raise SweepError(f"{label} must be a non-root relative POSIX path")
    normalized = Path(os.path.normpath(path)).as_posix()
    if normalized.startswith("../") or normalized == ".":
        raise SweepError(f"{label} escapes the repository")
    return normalized.rstrip("/")


def _relative_paths(value: object, label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise SweepError(f"{label} must be a {'possibly empty ' if allow_empty else 'non-empty '}array")
    paths = [_relative_path(item, f"{label}[{index}]") for index, item in enumerate(value)]
    if len(set(paths)) != len(paths):
        raise SweepError(f"{label} repeats a path")
    return paths


def _is_descendant(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")


def _require_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise SweepError(f"{label} must be an object")
    return value


def validate_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version", "run_id", "approved", "pinned_origin_main", "preconditions",
        "never_cleanable", "clean", "restore", "park", "branches", "closing",
    }
    if set(manifest) != required:
        missing, unknown = required - set(manifest), set(manifest) - required
        raise SweepError(f"manifest fields are not exact; missing={sorted(missing)} unknown={sorted(unknown)}")
    if manifest["schema_version"] != MANIFEST_SCHEMA:
        raise SweepError("manifest schema is not the registered R03 runner schema")
    run_id = _require_string(manifest["run_id"], "manifest.run_id")
    if any(char.isspace() for char in run_id) or "/" in run_id:
        raise SweepError("manifest.run_id may not contain whitespace or a slash")
    if manifest["approved"] is not True:
        raise SweepHeld("manifest is not approved")
    pinned = _require_oid(manifest["pinned_origin_main"], "manifest.pinned_origin_main")

    preconditions = _require_mapping(manifest["preconditions"], "manifest.preconditions")
    if set(preconditions) != {"capability_denial_tests_passed", "fresh_verified_production_backup"}:
        raise SweepError("manifest.preconditions fields are not exact")
    for key, value in preconditions.items():
        if value is not True:
            raise SweepHeld(f"stage 5 HELD: precondition {key} is not verified true")

    never_cleanable = _relative_paths(manifest["never_cleanable"], "manifest.never_cleanable")
    clean = _require_mapping(manifest["clean"], "manifest.clean")
    if set(clean) != {"pathspecs", "expected"}:
        raise SweepError("manifest.clean fields are not exact")
    # A fixture can declare no clean set. This is
    # only safe because an empty list is now proven to SKIP the clean rather than
    # widen into `git clean -fd --` over the whole tree; the two changes belong
    # together and neither is correct without the other.
    clean_pathspecs = _relative_paths(clean["pathspecs"], "manifest.clean.pathspecs", allow_empty=True)
    clean_expected = _relative_paths(clean["expected"], "manifest.clean.expected", allow_empty=True)

    restores = manifest["restore"]
    if not isinstance(restores, list):
        raise SweepError("manifest.restore must be an array")
    restore_paths: list[str] = []
    for index, item in enumerate(restores):
        entry = _require_mapping(item, f"manifest.restore[{index}]")
        if set(entry) != {"path", "blob_oid", "observed_state"}:
            raise SweepError(f"manifest.restore[{index}] fields are not exact")
        restore_paths.append(_relative_path(entry["path"], f"manifest.restore[{index}].path"))
        _require_oid(entry["blob_oid"], f"manifest.restore[{index}].blob_oid")
        observed = _require_string(entry["observed_state"], f"manifest.restore[{index}].observed_state")
        if not observed.startswith("sha256:") or len(observed) != 71 or any(
                c not in "0123456789abcdef" for c in observed[7:]):
            raise SweepError("restore observed state must be a SHA-256 digest")
    if len(set(restore_paths)) != len(restore_paths):
        raise SweepError("manifest.restore repeats a path")

    park = _require_mapping(manifest["park"], "manifest.park")
    if set(park) != {"paths", "archive"}:
        raise SweepError("manifest.park fields are not exact")
    park_paths = _relative_paths(park["paths"], "manifest.park.paths", allow_empty=True)
    archive = park["archive"]
    if park_paths:
        archive = _require_mapping(archive, "manifest.park.archive")
        if set(archive) != {"directory", "recipient", "identity"}:
            raise SweepError("manifest.park.archive fields are not exact")
        archive_dir = Path(_require_string(archive["directory"], "manifest.park.archive.directory"))
        identity = Path(_require_string(archive["identity"], "manifest.park.archive.identity"))
        if not archive_dir.is_absolute() or not identity.is_absolute():
            raise SweepError("archive directory and identity must be absolute paths")
        _require_string(archive["recipient"], "manifest.park.archive.recipient")
    elif archive is not None:
        raise SweepError("manifest.park.archive must be null when no paths are parked")

    branches = manifest["branches"]
    if not isinstance(branches, list):
        raise SweepError("manifest.branches must be an array")
    branch_names: set[str] = set()
    for index, item in enumerate(branches):
        entry = _require_mapping(item, f"manifest.branches[{index}]")
        allowed = {"name", "tip", "classification", "tip_backup_ref", "host_confirmation"}
        if set(entry) != allowed:
            raise SweepError(f"manifest.branches[{index}] fields are not exact")
        name = _require_string(entry["name"], f"manifest.branches[{index}].name")
        if name in branch_names or name == "main" or name.startswith("-") or ".." in name:
            raise SweepError(f"manifest.branches[{index}].name is unsafe")
        branch_names.add(name)
        _require_oid(entry["tip"], f"manifest.branches[{index}].tip")
        classification = entry["classification"]
        if classification not in {"ancestry_merged", "squash_merged", "unmerged_without_pr"}:
            raise SweepError(f"manifest.branches[{index}].classification is unknown")
        backup_ref = entry["tip_backup_ref"]
        if backup_ref is not None:
            backup_ref = _require_string(backup_ref, f"manifest.branches[{index}].tip_backup_ref")
            if not backup_ref.startswith(f"refs/backup/{run_id}/branch/"):
                raise SweepError(f"manifest.branches[{index}].tip_backup_ref is outside this run")
        confirmation = entry["host_confirmation"]
        if classification == "squash_merged":
            confirmation = _require_mapping(confirmation, f"manifest.branches[{index}].host_confirmation")
            if set(confirmation) != {"provider", "state", "base_ref", "head_oid", "evidence_id"}:
                raise SweepError(f"manifest.branches[{index}].host_confirmation fields are not exact")
            if (confirmation["provider"], confirmation["state"], confirmation["base_ref"]) != ("github", "MERGED", "main"):
                raise SweepError(f"manifest.branches[{index}] lacks merged-into-main host evidence")
            if _require_oid(confirmation["head_oid"], f"manifest.branches[{index}].host_confirmation.head_oid") != entry["tip"]:
                raise SweepError(f"manifest.branches[{index}] host evidence does not bind its current tip")
            _require_string(confirmation["evidence_id"], f"manifest.branches[{index}].host_confirmation.evidence_id")
        elif confirmation is not None:
            raise SweepError(f"manifest.branches[{index}] has inappropriate host confirmation")

    closing = _require_mapping(manifest["closing"], "manifest.closing")
    if set(closing) != {"expected_head", "expected_branch_count"}:
        raise SweepError("manifest.closing fields are not exact")
    if _require_oid(closing["expected_head"], "manifest.closing.expected_head") != pinned:
        raise SweepError("closing expected head must equal pinned origin/main")
    if not isinstance(closing["expected_branch_count"], int) or closing["expected_branch_count"] < 1:
        raise SweepError("closing expected branch count must be a positive integer")

    return {
        "run_id": run_id, "pinned": pinned, "never_cleanable": never_cleanable,
        "clean_pathspecs": clean_pathspecs, "clean_expected": clean_expected,
        "restore_paths": restore_paths,
    }


def validate_allowlist(allowlist: Mapping[str, Any]) -> None:
    if set(allowlist) != {"schema_version", "runner_argv", "commands"}:
        raise SweepError("allowlist fields are not exact")
    if allowlist["schema_version"] != ALLOWLIST_SCHEMA:
        raise SweepError("allowlist schema is not registered")
    runner_argv = allowlist["runner_argv"]
    if not isinstance(runner_argv, list) or not all(isinstance(value, str) and value for value in runner_argv):
        raise SweepError("allowlist.runner_argv is malformed")
    flattened = "\n".join(runner_argv)
    for placeholder in ("{manifest_fd}", "{allowlist_fd}", "{capability_receipt_fd}"):
        if flattened.count(placeholder) != 1:
            raise SweepError(f"allowlist.runner_argv must include {placeholder} exactly once")
    commands = allowlist["commands"]
    if not isinstance(commands, list) or not commands:
        raise SweepError("allowlist.commands must be a non-empty array")
    ids: set[str] = set()
    for index, command in enumerate(commands):
        item = _require_mapping(command, f"allowlist.commands[{index}]")
        if set(item) != {"id", "argv", "pathspecs"}:
            raise SweepError(f"allowlist.commands[{index}] fields are not exact")
        command_id = _require_string(item["id"], f"allowlist.commands[{index}].id")
        if command_id in ids:
            raise SweepError(f"allowlist command id repeats: {command_id}")
        ids.add(command_id)
        argv = item["argv"]
        if not isinstance(argv, list) or not argv or not all(isinstance(value, str) and value for value in argv):
            raise SweepError(f"allowlist.commands[{index}].argv is malformed")
        options = argv[:argv.index("--")] if "--" in argv else argv
        if "clean" in options and any(
                value.startswith("-") and not value.startswith("--") and "x" in value.lower()
                for value in options[options.index("clean") + 1:]):
            raise SweepError(f"allowlist.commands[{index}] permits ignored-file cleaning")
        _relative_paths(item["pathspecs"], f"allowlist.commands[{index}].pathspecs", allow_empty=True)


def validate_receipt(receipt: Mapping[str, Any], manifest_bytes: bytes) -> None:
    required = {
        "schema_version", "capability_key", "token_digest", "operator_binding_digest",
        "approved_manifest_digest", "repository_identity_digest", "starting_object_id",
        "allowlist_digest", "consumed_at_ns",
    }
    if set(receipt) != required:
        raise SweepError("capability receipt fields are not exact")
    if receipt["schema_version"] != RECEIPT_SCHEMA or receipt["capability_key"] != CAPABILITY_KEY:
        raise SweepHeld("stage 5 HELD: capability receipt is not a live R03 redemption")
    if receipt["approved_manifest_digest"] != _sha256(manifest_bytes):
        raise SweepHeld("stage 5 HELD: capability receipt does not bind these manifest bytes")
    if not isinstance(receipt["consumed_at_ns"], int) or receipt["consumed_at_ns"] <= 0:
        raise SweepHeld("stage 5 HELD: capability receipt has no live redemption timestamp")


def _require_allowed(allowlist: Mapping[str, Any], command_id: str, argv: list[str], pathspecs: list[str]) -> None:
    commands = allowlist["commands"]
    for command in commands:
        if command["id"] == command_id:
            if command["argv"] != argv or command["pathspecs"] != pathspecs:
                raise SweepError(f"allowlist mismatch for {command_id}")
            return
    raise SweepError(f"allowlist omits required command {command_id}")


def _clean_candidates(repository: Path, pathspecs: list[str]) -> list[str]:
    result = _git(repository, "clean", "-nd", "--", *pathspecs)
    candidates: list[str] = []
    for line in result.stdout.splitlines():
        prefix = "Would remove "
        if not line.startswith(prefix):
            raise SweepError(f"unexpected git clean dry-run output: {line!r}")
        candidates.append(_relative_path(line[len(prefix):].rstrip("/"), "git clean candidate"))
    if len(set(candidates)) != len(candidates):
        raise SweepError("git clean dry-run reported a duplicate candidate")
    return sorted(candidates)


def _assert_clean_diff(manifest: Mapping[str, Any], parsed: Mapping[str, Any], candidates: list[str]) -> None:
    expected = sorted(parsed["clean_expected"])
    if candidates != expected:
        raise SweepError(f"git clean dry-run diverges from manifest; expected={expected} actual={candidates}")
    protected = [candidate for candidate in candidates
                 if any(_is_descendant(candidate, root) for root in parsed["never_cleanable"])]
    if protected:
        raise SweepError(f"git clean dry-run includes never-cleanable path(s): {protected}")


def _path_record(path: Path, relative: str) -> tuple[str, str, str, str]:
    details = path.lstat()
    mode = oct(stat.S_IMODE(details.st_mode))
    if path.is_symlink():
        return (relative, "symlink", mode, os.readlink(path))
    if path.is_dir():
        return (relative, "directory", mode, "")
    if not path.is_file():
        raise SweepError(f"unsupported archive entry type: {relative}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return (relative, "file", mode, digest)


def canonical_tree_manifest(root: Path, paths: Iterable[str]) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    for relative in sorted(paths, key=os.fsencode):
        target = root / relative
        if not target.exists() and not target.is_symlink():
            raise SweepError(f"archive path disappeared: {relative}")
        rows.append(_path_record(target, relative))
        if target.is_dir() and not target.is_symlink():
            for current, directories, filenames in os.walk(target, followlinks=False):
                directories.sort(key=os.fsencode)
                filenames.sort(key=os.fsencode)
                current_path = Path(current)
                for name in directories + filenames:
                    child = current_path / name
                    child_relative = child.relative_to(root).as_posix()
                    rows.append(_path_record(child, child_relative))
    return sorted(rows, key=lambda row: os.fsencode(row[0]))


def _write_private(path: Path, body: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, body)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(path, 0o600)


def _archive_and_verify(repository: Path, manifest: Mapping[str, Any], parsed: Mapping[str, Any]) -> None:
    park = manifest["park"]
    paths: list[str] = parsed["park_paths"] if "park_paths" in parsed else _relative_paths(park["paths"], "manifest.park.paths", allow_empty=True)
    if not paths:
        return
    archive = park["archive"]
    archive_dir = Path(archive["directory"])
    if archive_dir.resolve(strict=False) == repository or repository in archive_dir.resolve(strict=False).parents:
        raise SweepError("archive directory must be outside the repository")
    old_umask = os.umask(0o077)
    try:
        archive_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    finally:
        os.umask(old_umask)
    os.chmod(archive_dir, 0o700)
    if stat.S_IMODE(archive_dir.stat().st_mode) != 0o700:
        raise SweepError("archive directory is not mode 700")
    archive_path = archive_dir / f"repo-hygiene-park-{parsed['run_id']}.tar.gz.age"
    archive_manifest_path = archive_dir / f"repo-hygiene-park-{parsed['run_id']}.manifest.json"
    before = canonical_tree_manifest(repository, paths)
    _write_private(archive_manifest_path, (json.dumps(before, separators=(",", ":")) + "\n").encode("utf-8"))
    with archive_path.open("wb") as archive_file:
        tar = subprocess.Popen(
            ["tar", "-C", str(repository), "--no-xattrs", "-czpf", "-", *paths],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_clean_git_env(),
        )
        assert tar.stdout is not None
        age = subprocess.Popen(
            ["age", "-r", archive["recipient"]], stdin=tar.stdout, stdout=archive_file,
            stderr=subprocess.PIPE, env=_clean_git_env(),
        )
        tar.stdout.close()
        age_stderr = age.communicate()[1]
        tar_stderr = tar.communicate()[1]
    if tar.returncode or age.returncode:
        try:
            archive_path.unlink()
        except FileNotFoundError:
            pass
        raise SweepError(f"age archive failed: tar={tar.returncode} age={age.returncode} "
                         f"{tar_stderr.decode(errors='replace')} {age_stderr.decode(errors='replace')}")
    os.chmod(archive_path, 0o600)
    if stat.S_IMODE(archive_path.stat().st_mode) != 0o600:
        raise SweepError("encrypted archive is not mode 600")
    digest_path = archive_dir / f"repo-hygiene-park-{parsed['run_id']}.tar.gz.age.sha256"
    # Hash the archive in chunks rather than reading it whole. The archive is
    # written by a streaming tar|age pipe and can run to hundreds of megabytes
    # -- the parked set here is 1.3GB before compression -- so read_bytes() put
    # the entire file in memory for no reason. On 2026-09-02 a settlement run on
    # this host was killed outright with swap at 500MB free, and a single
    # allocation that size is exactly the shape that dies. Chunked hashing gives
    # the identical digest at constant memory.
    archive_hash = hashlib.sha256()
    with archive_path.open("rb") as archive_reader:
        for block in iter(lambda: archive_reader.read(1024 * 1024), b""):
            archive_hash.update(block)
    _write_private(digest_path, (archive_hash.hexdigest() + "\n").encode("ascii"))

    with tempfile.TemporaryDirectory(prefix="r03-park-restore-") as temporary:
        restore_root = Path(temporary)
        age = subprocess.Popen(
            ["age", "-d", "-i", archive["identity"], str(archive_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_clean_git_env(),
        )
        assert age.stdout is not None
        tar = subprocess.Popen(
            ["tar", "-C", str(restore_root), "-xzp"], stdin=age.stdout,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_clean_git_env(),
        )
        age.stdout.close()
        tar_stderr = tar.communicate()[1]
        age_stderr = age.communicate()[1]
        if age.returncode or tar.returncode:
            raise SweepError(f"archive restore verification failed: age={age.returncode} tar={tar.returncode} "
                             f"{age_stderr.decode(errors='replace')} {tar_stderr.decode(errors='replace')}")
        after = canonical_tree_manifest(restore_root, paths)
    if before != after:
        raise SweepError("archive every-path round-trip verification failed")
    print(f"STAGE 3 archive verified every path: {archive_path}")


def fingerprint_tree(repository: Path) -> str:
    status = _git(repository, "status", "--porcelain=v1", "-z").stdout.encode("utf-8", "surrogateescape")
    tracked = _git(repository, "ls-files", "-z").stdout.encode("utf-8", "surrogateescape").split(b"\0")
    untracked = _git(repository, "ls-files", "--others", "--exclude-standard", "-z").stdout.encode("utf-8", "surrogateescape").split(b"\0")
    rows: list[tuple[str, str, str, str]] = []
    for raw in tracked + untracked:
        if not raw:
            continue
        relative = raw.decode("utf-8", "surrogateescape")
        path = repository / relative
        if path.exists() or path.is_symlink():
            rows.append(_path_record(path, relative))
        else:
            rows.append((relative, "missing", "", ""))
    body = status + b"\n" + json.dumps(sorted(rows), separators=(",", ":"), ensure_ascii=False).encode("utf-8", "surrogateescape")
    return hashlib.sha256(body).hexdigest()


def _is_canonical_or_child(repository: Path) -> bool:
    resolved = repository.resolve(strict=False)
    canonical = CANONICAL_CHECKOUT.resolve(strict=False)
    return resolved == canonical or canonical in resolved.parents


def _stage3_backup(repository: Path, manifest: Mapping[str, Any], parsed: Mapping[str, Any],
                   allowlist: Mapping[str, Any]) -> None:
    _git(repository, "fetch", "origin", "main")
    fetched_pin = _git(repository, "rev-parse", "refs/remotes/origin/main").stdout.strip().lower()
    if fetched_pin != parsed["pinned"]:
        # origin/main ADVANCING is the normal state of a repository many sessions merge into,
        # and a manifest must not expire the instant an unrelated PR lands -- under strict
        # equality every re-authoring raced the next merge and the sweep could never fire.
        # What is never acceptable is origin/main moving somewhere the pin cannot reach: a
        # rewind, a force-push, or a rewritten history invalidates every merged-ancestry claim
        # the manifest was authored against, so that still refuses.
        if _git(repository, "merge-base", "--is-ancestor", parsed["pinned"], fetched_pin,
                check=False).returncode:
            raise SweepError(
                f"origin/main {fetched_pin} does not descend from manifest pin {parsed['pinned']}: "
                "history was rewound or rewritten; re-author the manifest")
        print(f"STAGE 3 freshness: origin/main advanced to {fetched_pin}; "
              f"manifest pin {parsed['pinned']} is an ancestor (accepted)")
    head = _git(repository, "rev-parse", "HEAD").stdout.strip().lower()
    base = f"refs/backup/{parsed['run_id']}"
    _git(repository, "update-ref", f"{base}/starting-head", head)
    _git(repository, "update-ref", f"{base}/pinned-origin-main", fetched_pin)
    stash = _git(repository, "stash", "create", f"R03 post-window {parsed['run_id']}").stdout.strip()
    _git(repository, "update-ref", f"{base}/tracked-index", stash or head)
    remote_refspecs: list[str] = []
    remote_readback: list[tuple[str, str]] = []
    for branch in manifest["branches"]:
        backup_ref = branch["tip_backup_ref"]
        if backup_ref is not None:
            _verify_branch_tip(repository, branch)
            _git(repository, "update-ref", backup_ref, branch["tip"])
            remote_refspecs.append(f"refs/heads/{branch['name']}:{backup_ref}")
            remote_readback.append((backup_ref, branch["tip"]))
    if remote_refspecs:
        push_argv = ["git", "push", "--atomic", "origin", *remote_refspecs]
        _require_allowed(allowlist, "stage3.branch-backup.push", push_argv, [])
        _git(repository, "push", "--atomic", "origin", *remote_refspecs)
        for backup_ref, tip in remote_readback:
            remote_tip = _git(repository, "ls-remote", "--refs", "origin", backup_ref).stdout.strip().split()
            if len(remote_tip) != 2 or remote_tip[0].lower() != tip or remote_tip[1] != backup_ref:
                raise SweepError(f"remote branch backup readback failed: {backup_ref}")
    _archive_and_verify(repository, manifest, parsed)
    print(f"STAGE 3 backup anchored under {base}")


def _verify_stage5_preconditions(manifest: Mapping[str, Any], receipt: Mapping[str, Any], manifest_bytes: bytes) -> None:
    # Re-reading every component here is deliberate: stages 3 and 4 never waive
    # the three independent stage-5 gates.
    validate_manifest(manifest)
    validate_receipt(receipt, manifest_bytes)
    print("STAGE 5 preconditions HELD: capability live, manifest approved, production backup verified")


def _verify_branch_tip(repository: Path, branch: Mapping[str, Any]) -> bool:
    result = _git(repository, "rev-parse", "--verify", f"refs/heads/{branch['name']}", check=False)
    if result.returncode:
        raise SweepError(f"branch disappeared before settlement: {branch['name']}")
    if result.stdout.strip().lower() != branch["tip"]:
        raise SweepError(f"branch tip drifted before settlement: {branch['name']}")
    return True


def _branch_set(repository: Path) -> set[str]:
    """Every local branch name, as a set."""
    out = _git(repository, "for-each-ref", "--format=%(refname)", "refs/heads").stdout
    return {line.removeprefix("refs/heads/") for line in out.splitlines() if line}


def _delete_branches(repository: Path, manifest: Mapping[str, Any], parsed: Mapping[str, Any],
                     allowlist: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    """Apply the branch law and REPORT what it actually did.

    Returns ``(deleted, retained)``.  The closing readback asserts against these
    observed sets rather than re-deriving which branches the law should have
    removed: a second implementation of the same rule is a rule that drifts, and
    only one of the two copies would ever be exercised.
    """
    deleted: set[str] = set()
    retained: set[str] = set()
    for branch in manifest["branches"]:
        _verify_branch_tip(repository, branch)
        name = branch["name"]
        classification = branch["classification"]
        backup_ref = branch["tip_backup_ref"]
        if classification == "unmerged_without_pr":
            print(f"STAGE 5 retained unmerged-without-PR branch: {name}")
            retained.add(name)
            continue
        if backup_ref is None or _git(repository, "show-ref", "--verify", "--quiet", backup_ref, check=False).returncode:
            print(f"STAGE 5 retained branch lacking tip backup ref: {name}")
            retained.add(name)
            continue
        if classification == "ancestry_merged":
            if _git(repository, "merge-base", "--is-ancestor", branch["tip"], parsed["pinned"], check=False).returncode:
                raise SweepError(f"declared ancestry-merged branch is not an ancestor of pin: {name}")
            argv = ["git", "branch", "-d", name]
            _require_allowed(allowlist, f"stage5.branch.safe.{name}", argv, [])
            _git(repository, "branch", "-d", name)
            deleted.add(name)
            print(f"STAGE 5 safe-deleted ancestry-merged branch: {name}")
        elif classification == "squash_merged":
            confirmation = branch["host_confirmation"]
            if confirmation is None:  # validate_manifest makes this unreachable; retain the guard.
                raise SweepError(f"squash branch has no host confirmation: {name}")
            argv = ["git", "branch", "-D", name]
            _require_allowed(allowlist, f"stage5.branch.squash.{name}", argv, [])
            _git(repository, "branch", "-D", name)
            deleted.add(name)
            print(f"STAGE 5 force-deleted host-confirmed squash-merged branch: {name}")
    return deleted, retained


def _safe_remove(repository: Path, pathspec: str) -> None:
    target = repository / pathspec
    if not target.exists() and not target.is_symlink():
        raise SweepError(f"park path disappeared before removal: {pathspec}")
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)
    else:
        raise SweepError(f"unsupported park removal type: {pathspec}")


def _stage6_readback(repository: Path, manifest: Mapping[str, Any], parsed: Mapping[str, Any],
                     starting_branches: set[str], deleted: set[str]) -> None:
    head = _git(repository, "rev-parse", "HEAD").stdout.strip().lower()
    if head != parsed["pinned"]:
        raise SweepError(f"closing readback HEAD differs from pin: {head}")
    status = _git(repository, "status", "--porcelain=v1").stdout
    if status:
        raise SweepError(f"closing readback has remaining tracked/untracked dirt: {status!r}")

    # Branch closing is asserted as SETS, not as a pinned integer.  A count fixed at
    # authoring time is invalidated by any concurrent session creating a branch -- which
    # happens continuously here -- so the integer failed for reasons that had nothing to do
    # with this settlement while still not proving the right branches went.  These two
    # assertions are strictly stronger and are immune to unrelated branches appearing:
    # everything the runner deleted is really gone, and nothing else was lost.
    surviving = _branch_set(repository)
    resurrected = deleted & surviving
    if resurrected:
        raise SweepError(f"closing readback: deleted branches still present: {sorted(resurrected)[:10]}")
    collateral = (starting_branches - deleted) - surviving
    if collateral:
        raise SweepError(
            f"closing readback: branches vanished that this settlement never deleted: {sorted(collateral)[:10]}")

    expected_count = manifest["closing"]["expected_branch_count"]
    if len(surviving) != expected_count:
        # Advisory only: the set assertions above are the binding guarantee.
        print(f"STAGE 6 note: branch count {len(surviving)} differs from the manifest's "
              f"authoring-time expectation {expected_count}; "
              f"{len(surviving - (starting_branches - deleted))} branch(es) appeared during the run")
    print(f"STAGE 6 closing readback passed: head={head} deleted={len(deleted)} surviving={len(surviving)}")


def _verify_restore_state(repository: Path, manifest: Mapping[str, Any]) -> None:
    try:
        actual = build_restore_set(repository, manifest["pinned_origin_main"],
                                   [entry["path"] for entry in manifest["restore"]])
    except RestoreSetRefusal as exc:
        raise SweepError(f"restore observed state refused: {exc}") from exc
    if actual != manifest["restore"]:
        raise SweepError("restore observed state differs from the authored manifest")


def _author_manifest(repository: Path, template: Path, allowed: list[str]) -> dict[str, Any]:
    """Produce unapproved bytes for capability admission, without mutating Git."""
    body = template.read_bytes()
    if len(body) > MAX_FD_BYTES:
        raise SweepError("author template exceeds the manifest size limit")
    manifest = _json_object(body, "author template")
    if manifest.get("approved") is not False or manifest.get("restore") != []:
        raise SweepError("author template must be unapproved with an empty restore set")
    validate_manifest({**manifest, "approved": True})
    try:
        manifest["restore"] = build_restore_set(repository, manifest["pinned_origin_main"], allowed)
    except RestoreSetRefusal as exc:
        raise SweepError(f"manifest authoring refused: {exc}") from exc
    # Validate the complete shape while keeping approval a separate operator act.
    validate_manifest({**manifest, "approved": True})
    return manifest


def run_settlement(*, repository: Path, manifest_fd: int, allowlist_fd: int, capability_receipt_fd: int,
                   execute: bool,
                   before_disposal: Callable[[], None] | None = None) -> None:
    """Run one admitted settlement against *repository*.

    ``before_disposal`` is an in-process test seam, intentionally unavailable
    from the command line.  It proves the stage-4 fingerprint stops a changed
    fixture before any restore, clean, park removal, or branch operation.
    """
    repository = repository.resolve(strict=True)
    manifest_bytes = _read_fd(manifest_fd, "manifest")
    allowlist_bytes = _read_fd(allowlist_fd, "allowlist")
    receipt_bytes = _read_fd(capability_receipt_fd, "capability receipt")
    manifest = _json_object(manifest_bytes, "manifest")
    allowlist = _json_object(allowlist_bytes, "allowlist")
    receipt = _json_object(receipt_bytes, "capability receipt")
    parsed = validate_manifest(manifest)
    parsed["park_paths"] = _relative_paths(manifest["park"]["paths"], "manifest.park.paths", allow_empty=True)
    validate_allowlist(allowlist)
    validate_receipt(receipt, manifest_bytes)
    _verify_restore_state(repository, manifest)

    # AN EMPTY PATHSPEC LIST MEANS CLEAN NOTHING, NEVER CLEAN EVERYTHING.
    # `git clean -fd --` with no pathspec removes every untracked file in the
    # repository. A manifest that declares no clean set is asking for no clean,
    # so the command must not be built at all -- passing the empty list through
    # would turn "nothing to tidy" into "delete all untracked work", which is
    # the single most destructive thing this tool could do by accident.
    candidates: list[str] = []
    if parsed["clean_pathspecs"]:
        dry_argv = ["git", "clean", "-nd", "--", *parsed["clean_pathspecs"]]
        _require_allowed(allowlist, "stage5.clean.dry", dry_argv, parsed["clean_pathspecs"])
        candidates = _clean_candidates(repository, parsed["clean_pathspecs"])
    _assert_clean_diff(manifest, parsed, candidates)

    # THE SETTLEMENT SETTLES A CURRENT TREE; IT NEVER MOVES HEAD.  Stage 6 requires HEAD to
    # equal the pin, and no stage in between changes HEAD, so a checkout that is behind the
    # pin can never satisfy the closing readback -- previously that surfaced as a confusing
    # stage-6 failure AFTER the destructive work had already run.  Checking it up front turns
    # an unsatisfiable run into an honest refusal that names the remedy.  Bringing the
    # checkout current is a separate, adjudicated step and deliberately not this tool's job.
    head_now = _git(repository, "rev-parse", "HEAD").stdout.strip().lower()
    head_is_pinned = head_now == parsed["pinned"]
    if not head_is_pinned:
        behind = _git(repository, "rev-list", "--count", f"{head_now}..{parsed['pinned']}",
                      check=False).stdout.strip() or "?"
        precondition = (
            f"repository HEAD {head_now} is not the settled pin {parsed['pinned']} "
            f"({behind} commit(s) behind). The settlement settles a CURRENT tree and never moves "
            "HEAD, so the stage-6 closing readback could not hold. Bring the checkout to the pin "
            "first (a separate, adjudicated step), then re-run.")

    if not execute:
        print("DRY-RUN: stages 3-6 would execute in this order:")
        print(f"  stage 3 backup: fetch and pin {parsed['pinned']}; anchor refs/backup/{parsed['run_id']}/...")
        print("  stage 4 fingerprint: tracked plus untracked-nonignored tree state")
        print("  stage 5 gate: re-verify capability, approval, and production-backup preconditions")
        print(f"  stage 5 clean diff: {candidates}")
        print(f"  stage 5 restore paths: {parsed['restore_paths']}")
        if parsed["restore_paths"]:
            print("  PRECONDITION NOT MET -- restore execution requires enforced writer exclusion")
        print(f"  stage 5 park paths: {parsed['park_paths']}")
        print("  stage 5 branch law: ancestry safe-delete; host-confirmed squash + backup force-delete; unmerged retained")
        print("  stage 6 closing readback: pinned head, clean tree, deleted-set gone, no collateral loss")
        if head_is_pinned:
            print(f"  PRECONDITION OK: HEAD is the settled pin {parsed['pinned']}")
        else:
            print(f"  PRECONDITION NOT MET -- an --execute run would refuse: {precondition}")
        return

    if not head_is_pinned:
        raise SweepHeld(precondition)
    # State checks cannot exclude an editor between verification and checkout.
    # Refuse before stage 3 writes refs or archives, even for admitted bytes.
    # No bypass or cooperative-lock assertion can enable unsafe restoration.
    if parsed["restore_paths"]:
        raise SweepHeld("restore execution requires enforced writer exclusion; "
                        "authoring and dry-run remain available, repository unchanged")
    starting_branches = _branch_set(repository)

    if _is_canonical_or_child(repository):
        raise SweepError("execute mode refuses the canonical checkout tree; disposable fixtures only")
    _stage3_backup(repository, manifest, parsed, allowlist)
    fingerprint = fingerprint_tree(repository)
    print(f"STAGE 4 fingerprint captured: {fingerprint}")
    if before_disposal is not None:
        before_disposal()
    _verify_stage5_preconditions(manifest, receipt, manifest_bytes)
    if fingerprint_tree(repository) != fingerprint:
        raise SweepError("tree fingerprint changed between stage 4 and disposal")

    _verify_restore_state(repository, manifest)

    if parsed["park_paths"]:
        for pathspec in parsed["park_paths"]:
            _require_allowed(allowlist, f"stage5.park.remove.{pathspec}", ["remove", "--", pathspec], [pathspec])
            if any(_is_descendant(pathspec, root) for root in parsed["never_cleanable"]):
                raise SweepError(f"manifest tries to park never-cleanable path: {pathspec}")
            _safe_remove(repository, pathspec)

    if parsed["clean_pathspecs"]:
        execute_argv = ["git", "clean", "-fd", "--", *parsed["clean_pathspecs"]]
        _require_allowed(allowlist, "stage5.clean.execute", execute_argv, parsed["clean_pathspecs"])
        _git(repository, "clean", "-fd", "--", *parsed["clean_pathspecs"])
    else:
        print("STAGE 5 clean skipped: the manifest declares no clean set, and an empty "
              "pathspec list must never widen into cleaning the whole tree")
    deleted, retained = _delete_branches(repository, manifest, parsed, allowlist)
    print(f"STAGE 5 branch law applied: deleted={len(deleted)} retained={len(retained)}")
    _stage6_readback(repository, manifest, parsed, starting_branches, deleted)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-fd", type=int)
    parser.add_argument("--allowlist-fd", type=int)
    parser.add_argument("--capability-receipt-fd", type=int)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--execute", action="store_true", help="allow fixture-only destructive stage-5 operations")
    parser.add_argument("--author-template", type=Path,
                        help="read an unapproved v2 template; emit state-bound manifest JSON for admission")
    parser.add_argument("--approve-restore-path", action="append", default=[],
                        help="literal tracked path explicitly authorized for restoration (repeatable)")
    args = parser.parse_args(argv)
    try:
        descriptors = (args.manifest_fd, args.allowlist_fd, args.capability_receipt_fd)
        if args.author_template:
            if args.execute or any(fd is not None for fd in descriptors):
                raise SweepError("authoring cannot execute or accept capability descriptors")
            authored = _author_manifest(args.repository.resolve(strict=True), args.author_template,
                                        args.approve_restore_path)
            print(json.dumps(authored, sort_keys=True, separators=(",", ":")))
            return 0
        if args.approve_restore_path or any(fd is None for fd in descriptors):
            raise SweepError("execution requires all three capability descriptors; paths belong to authoring")
        run_settlement(
            repository=args.repository, manifest_fd=args.manifest_fd, allowlist_fd=args.allowlist_fd,
            capability_receipt_fd=args.capability_receipt_fd, execute=args.execute,
        )
    except SweepHeld as exc:
        print(f"HELD: {exc}")
        return 75 if args.execute else 0
    except (SweepError, OSError) as exc:
        print(f"ABORT: {exc}", file=sys.stderr)
        return 78
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
