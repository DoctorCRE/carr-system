#!/usr/bin/env python3
"""Single-use, fail-closed capability for the R03 settlement sweep.

This is intentionally the opposite of the writer gate's fail-open posture. A
missing store, unreadable record, ambiguous native session identity, bad clock,
changed manifest, changed repository, changed object id, changed allowlist, or
changed runner executable refuses the run. Uncertainty never turns into
destructive authority.

The capability binds the native operator session, approved manifest digest,
repository identity, starting HEAD object id, command/pathspec allowlist digest,
and the runner executable's own bytes (starting_object_id pins HEAD, which a
working-tree edit leaves untouched, so the runner's content is bound separately
from its path). The raw 256-bit token exists only in a caller-named mode-0600
file. Consumption is recorded under an OS lock before the runner starts; the
runner then executes a private mode-0500 snapshot of the verified runner bytes
(not the original path) and reads read-only, unlinked copies of the digest-
verified manifest, allowlist and receipt — nothing it runs or reads is a live
path or a rewritable descriptor. SIGHUP, SIGINT, SIGTERM, normal
return, and runner failure all pass through finally revocation. SIGKILL of this
parent cannot run user-space cleanup; its already-consumed record still cannot
replay, but the runner, started in its own session, survives reparented to init
and keeps executing with no supervisor.

The single-use record carries an HMAC whose key IS the token itself, so it is an
integrity check against corruption, NOT an authenticity control against the
token holder: the store is 0700 and the token file 0600 under one uid, so
whoever can write the store can read the token and recompute the MAC. The
capability therefore defends against accidental replay, not against an operator
who edits their own store.

The token admits one reviewed R03 stage-5 runner. It does not approve the
manifest, make or verify backups, fingerprint the tree, or prove that the
separately reviewed runner correctly implements every bound allowlist entry. Its
revocation covers the TOKEN, not a RUN already admitted: an AO reviewer's abort
revokes an issued, unconsumed token and refuses its pre-consumption redemption,
but has no effect once the runner has been spawned.
"""

from __future__ import annotations

import argparse
import base64
import errno
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping, NoReturn, Sequence

from git_env import scrubbed_env


SCHEMA = "carr.settlement-run-token-record.v1"
ALLOWLIST_SCHEMA = "carr.settlement-command-pathspec-allowlist.v1"
REGISTRATION_SCHEMA = "carr.settlement-run-token-registration.v1"
CAPABILITY_KEY = "R03C.settlement-capability.v1"
REGISTRATION_PATH = Path(__file__).resolve().parent / "config" / "settlement-run-token.v1.json"
NATIVE_SESSION_KEYS = ("CODEX_THREAD_ID", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_HOST_SESSION_ID")
REQUIRED_BINDINGS = (
    "operator_session",
    "approved_run_manifest_digest",
    "repository_identity",
    "starting_object_id",
    "command_pathspec_allowlist_digest",
)
REQUIRED_PLACEHOLDERS = ("{manifest_fd}", "{allowlist_fd}", "{capability_receipt_fd}")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
OID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,511}$")

REASON_ALREADY_CONSUMED = "settlement_capability.refusal.already_consumed"
REASON_OPERATOR_MISMATCH = "settlement_capability.refusal.operator_mismatch"
REASON_MANIFEST_MISMATCH = "settlement_capability.refusal.manifest_digest_mismatch"
REASON_EXPIRED = "settlement_capability.refusal.expired"
REASON_REVOKED = "settlement_capability.refusal.revoked"
REASON_ALLOWLIST_MISMATCH = "settlement_capability.refusal.allowlist_digest_mismatch"
REASON_REPOSITORY_MISMATCH = "settlement_capability.refusal.repository_mismatch"
REASON_STARTING_OID_MISMATCH = "settlement_capability.refusal.starting_object_mismatch"
REASON_CLOCK_INVALID = "settlement_capability.refusal.clock_invalid"
REASON_STORE_UNAVAILABLE = "settlement_capability.refusal.store_unavailable"
REASON_BINDING_AMBIGUOUS = "settlement_capability.refusal.binding_ambiguous"
REASON_RUNNER_DIGEST_MISMATCH = "settlement_capability.refusal.runner_digest_mismatch"


class Refusal(RuntimeError):
    def __init__(self, reason_id: str, detail: str):
        super().__init__(detail)
        self.reason_id = reason_id
        self.detail = detail


class RunInterrupted(RuntimeError):
    def __init__(self, signum: int):
        super().__init__(f"runner interrupted by signal {signum}")
        self.signum = signum


def refuse(reason_id: str, detail: str) -> NoReturn:
    raise Refusal(reason_id, detail)


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def token_digest(token: str) -> str:
    return sha256_bytes(token.encode("ascii"))


def _secure_read(path: Path, *, label: str, maximum: int = 8 * 1024 * 1024) -> tuple[int, bytes]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError(errno.EINVAL, f"{label} is not a regular file")
        if st.st_size > maximum:
            raise OSError(errno.EFBIG, f"{label} exceeds {maximum} bytes")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        body = b"".join(chunks)
        if len(body) > maximum:
            raise OSError(errno.EFBIG, f"{label} exceeds {maximum} bytes")
        os.lseek(fd, 0, os.SEEK_SET)
        return fd, body
    except Exception:
        if "fd" in locals():
            os.close(fd)
        raise


def _json_object(body: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        refuse(REASON_BINDING_AMBIGUOUS, f"{label} is unreadable JSON: {exc}")
    if not isinstance(value, dict):
        refuse(REASON_BINDING_AMBIGUOUS, f"{label} must be one JSON object")
    return value


def load_registration(path: Path = REGISTRATION_PATH) -> dict[str, Any]:
    try:
        fd, body = _secure_read(path, label="capability registration", maximum=128 * 1024)
        os.close(fd)
    except OSError as exc:
        refuse(REASON_STORE_UNAVAILABLE, f"capability registration unavailable: {exc}")
    registration = _json_object(body, label="capability registration")
    ttl = registration.get("ttl_seconds")
    protocol = registration.get("runner_protocol")
    if (
        registration.get("schema_version") != REGISTRATION_SCHEMA
        or registration.get("capability_key") != CAPABILITY_KEY
        or registration.get("registration_state") != "registered_on_merge_via_gate_edit_pr_review"
        or registration.get("consumer") != "R03.stage5.settlement-sweep"
        or registration.get("entrypoint") != "ops/settlement-run-token.py"
        or registration.get("selftest") != "ops/settlement-run-token-selftest.py"
        or registration.get("fail_closed") is not True
        or registration.get("bindings") != list(REQUIRED_BINDINGS)
        or not isinstance(ttl, dict)
        or ttl.get("minimum") != 1
        or not isinstance(ttl.get("maximum"), int)
        or not 1 <= ttl["maximum"] <= 300
        or not isinstance(protocol, dict)
        or protocol.get("schema_version") != ALLOWLIST_SCHEMA
        or protocol.get("required_runner_placeholders") != list(REQUIRED_PLACEHOLDERS)
    ):
        refuse(REASON_BINDING_AMBIGUOUS, "capability registration is incomplete or ambiguous")
    return registration


def operator_binding_from_env(env: Mapping[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    native: dict[str, str] = {}
    for key in NATIVE_SESSION_KEYS:
        value = source.get(key, "").strip()
        if value:
            if not ID_RE.fullmatch(value):
                refuse(REASON_BINDING_AMBIGUOUS, f"native operator-session value {key} is malformed")
            native[key] = value
    if not native:
        refuse(REASON_BINDING_AMBIGUOUS, "no native Codex or Claude operator-session identity is available")
    return sha256_bytes(canonical_json(native))


def _git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True,
            timeout=15, env=scrubbed_env(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        refuse(REASON_REPOSITORY_MISMATCH, f"repository identity unavailable: {exc}")
    if result.returncode != 0 or not result.stdout.strip():
        detail = (result.stderr or result.stdout).strip()[:300]
        refuse(REASON_REPOSITORY_MISMATCH, f"repository identity unavailable: {detail}")
    return result.stdout.strip()


def repository_identity(repo_arg: str | Path) -> tuple[dict[str, Any], str]:
    supplied = Path(repo_arg)
    if not supplied.is_absolute():
        refuse(REASON_BINDING_AMBIGUOUS, "repository path must be absolute")
    try:
        supplied_real = supplied.resolve(strict=True)
        top = Path(_git(supplied_real, "rev-parse", "--show-toplevel")).resolve(strict=True)
        common = Path(_git(supplied_real, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve(strict=True)
        if supplied_real != top:
            refuse(REASON_REPOSITORY_MISMATCH, "repository path must name the exact git worktree root")
        top_stat = top.stat()
        common_stat = common.stat()
    except Refusal:
        raise
    except OSError as exc:
        refuse(REASON_REPOSITORY_MISMATCH, f"repository identity unavailable: {exc}")
    identity = {
        "worktree_realpath": str(top), "worktree_device": top_stat.st_dev,
        "worktree_inode": top_stat.st_ino, "git_common_dir_realpath": str(common),
        "git_common_dir_device": common_stat.st_dev, "git_common_dir_inode": common_stat.st_ino,
    }
    return identity, sha256_bytes(canonical_json(identity))


def current_head(repo: str | Path) -> str:
    oid = _git(Path(repo), "rev-parse", "HEAD").lower()
    if not OID_RE.fullmatch(oid):
        refuse(REASON_STARTING_OID_MISMATCH, "repository HEAD is not an unambiguous object id")
    return oid


def validate_allowlist(body: bytes, *, repository: Path) -> dict[str, Any]:
    allowlist = _json_object(body, label="command/pathspec allowlist")
    if set(allowlist) != {"schema_version", "runner_argv", "commands"}:
        refuse(REASON_BINDING_AMBIGUOUS, "allowlist has missing or unknown top-level fields")
    if allowlist.get("schema_version") != ALLOWLIST_SCHEMA:
        refuse(REASON_BINDING_AMBIGUOUS, "allowlist schema version is not registered")
    runner = allowlist.get("runner_argv")
    commands = allowlist.get("commands")
    if not isinstance(runner, list) or not runner or not all(isinstance(v, str) and v and "\x00" not in v for v in runner):
        refuse(REASON_BINDING_AMBIGUOUS, "runner_argv must be a non-empty string array")
    flattened = "\n".join(runner)
    for placeholder in REQUIRED_PLACEHOLDERS:
        if flattened.count(placeholder) != 1:
            refuse(REASON_BINDING_AMBIGUOUS, f"runner_argv must carry {placeholder} exactly once")
    executable = Path(runner[0])
    try:
        executable_real = executable.resolve(strict=True)
        executable_real.relative_to(repository)
        mode = executable_real.stat().st_mode
    except (OSError, ValueError):
        refuse(REASON_BINDING_AMBIGUOUS, "runner executable must be an existing file inside the bound repository")
    if not stat.S_ISREG(mode) or not os.access(executable_real, os.X_OK):
        refuse(REASON_BINDING_AMBIGUOUS, "runner executable must be a directly executable regular file")
    if executable != executable_real:
        refuse(REASON_BINDING_AMBIGUOUS, "runner executable must use its real absolute path")
    if not isinstance(commands, list) or not commands:
        refuse(REASON_BINDING_AMBIGUOUS, "allowlist commands must be a non-empty array")
    seen: set[str] = set()
    for entry in commands:
        if not isinstance(entry, dict) or set(entry) != {"id", "argv", "pathspecs"}:
            refuse(REASON_BINDING_AMBIGUOUS, "each allowlist command needs exactly id, argv, and pathspecs")
        command_id, argv, pathspecs = entry.get("id"), entry.get("argv"), entry.get("pathspecs")
        if not isinstance(command_id, str) or not ID_RE.fullmatch(command_id) or command_id in seen:
            refuse(REASON_BINDING_AMBIGUOUS, "allowlist command ids must be unique stable identifiers")
        seen.add(command_id)
        if not isinstance(argv, list) or not argv or not all(isinstance(v, str) and v and "\x00" not in v for v in argv):
            refuse(REASON_BINDING_AMBIGUOUS, f"allowlist command {command_id} has ambiguous argv")
        if not isinstance(pathspecs, list) or not all(isinstance(v, str) and v and "\x00" not in v for v in pathspecs):
            refuse(REASON_BINDING_AMBIGUOUS, f"allowlist command {command_id} has ambiguous pathspecs")
        if len(set(pathspecs)) != len(pathspecs):
            refuse(REASON_BINDING_AMBIGUOUS, f"allowlist command {command_id} repeats a pathspec")
    return allowlist


def _read_runner_executable(allowlist: Mapping[str, Any], *, repository: Path) -> tuple[str, bytes]:
    """Read the runner executable and return (digest, bytes).

    validate_allowlist has already proven runner_argv[0] resolves to an
    executable regular file inside the bound repository; this binds its
    content. starting_object_id pins HEAD, which a working-tree edit leaves
    untouched, so without this a `git stash`, an editor save, or a concurrent
    worktree op inside the TTL window silently changes what the token
    authorizes. The runner is the one artifact that actually does the
    destroying, so it is the one whose faith-on-path was the widest gap.

    The BYTES are returned, not just the digest, because redemption executes a
    private snapshot of exactly these verified bytes rather than the original
    path — there is no re-read between hashing and exec to race.
    """
    executable = Path(allowlist["runner_argv"][0]).resolve(strict=True)
    try:
        fd, body = _secure_read(executable, label="runner executable")
        os.close(fd)
    except OSError as exc:
        refuse(REASON_BINDING_AMBIGUOUS, f"runner executable unreadable: {exc}")
    return sha256_bytes(body), body


def _anon_fd(body: bytes, *, prefix: str) -> int:
    """Copy verified bytes into an anonymous, unlinked, READ-ONLY fd.

    The child must read exactly what was digest-checked and must not be able to
    alter it. A live on-disk fd points at an inode an in-place rewrite mutates
    invisibly; an mkstemp fd is private but O_RDWR, so the inherited descriptor
    could still rewrite its own contents. So: write the bytes, reopen the file
    O_RDONLY, drop the writer, and unlink — the child inherits a read-only fd on
    an unlinked inode. Nothing the runner sees through these fds is rewritable,
    by it or by any pathname writer.
    """
    fd, name = tempfile.mkstemp(prefix=prefix)
    read_fd: int | None = None
    try:
        view = memoryview(body)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
        read_fd = os.open(name, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
        os.lseek(read_fd, 0, os.SEEK_SET)
        return read_fd
    except OSError:
        if read_fd is not None:
            os.close(read_fd)
        raise
    finally:
        os.close(fd)
        try:
            os.unlink(name)
        except OSError:
            pass


class _RunnerSnapshot:
    """A private, mode-0500 on-disk copy of the verified runner bytes.

    B1's final gap: after the digest check the original path is still what got
    executed, and the copies of manifest/allowlist/receipt take real time to
    build, so a same-inode rewrite of the runner between check and exec is a
    real race, not a microsecond one. The portable close (macOS has no
    fexecve/memfd) is to execute a snapshot of the exact bytes that were
    verified. The snapshot lives in a fresh owner-only (0700) directory and is
    itself r-x-only (0500), so between creation and exec nothing but root can
    alter it — and it is made from the in-memory verified bytes, so there is no
    re-read to race at all. Retained through the child's startup, removed after.
    """

    def __init__(self, body: bytes, *, suffix: str):
        self.dir = Path(tempfile.mkdtemp(prefix="settlement-capability-runner-"))
        self.path = self.dir / f"runner{suffix}"
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0), 0o500)
            try:
                view = memoryview(body)
                while view:
                    view = view[os.write(fd, view):]
                os.fsync(fd)
            finally:
                os.close(fd)
            os.chmod(self.path, 0o500)
        except OSError:
            self.cleanup()
            raise

    def cleanup(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass
        try:
            os.rmdir(self.dir)
        except OSError:
            pass


def _store_dir(path_arg: str | Path, *, create: bool) -> Path:
    path = Path(path_arg)
    if not path.is_absolute():
        refuse(REASON_STORE_UNAVAILABLE, "capability store path must be absolute")
    try:
        if create:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.is_symlink():
            raise OSError(errno.ELOOP, "store path is a symlink")
        path = path.resolve(strict=True)
        st = path.stat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077:
            raise OSError(errno.EACCES, "store must be an owner-only directory")
        return path
    except OSError as exc:
        refuse(REASON_STORE_UNAVAILABLE, f"capability store unavailable: {exc}")


class StoreLock:
    def __init__(self, store: Path):
        self.store = store
        self.fd: int | None = None

    def __enter__(self) -> "StoreLock":
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            self.fd = os.open(self.store / ".settlement-run-token.lock", flags, 0o600)
            st = os.fstat(self.fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077:
                raise OSError(errno.EACCES, "store lock is not owner-only")
            fcntl.flock(self.fd, fcntl.LOCK_EX)
            return self
        except OSError as exc:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
            refuse(REASON_STORE_UNAVAILABLE, f"capability store lock unavailable: {exc}")

    def __exit__(self, *_: object) -> None:
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None


def _record_path(store: Path, digest: str) -> Path:
    if not DIGEST_RE.fullmatch(digest):
        refuse(REASON_BINDING_AMBIGUOUS, "token digest is malformed")
    return store / (digest.removeprefix("sha256:") + ".json")


def _record_mac(record: Mapping[str, Any], token: str) -> str:
    unsigned = dict(record)
    unsigned.pop("record_mac", None)
    return "hmac-sha256:" + hmac.new(token.encode("ascii"), canonical_json(unsigned), hashlib.sha256).hexdigest()


def _validate_record(record: dict[str, Any], token: str, digest: str) -> None:
    expected = _record_mac(record, token)
    required = {
        "schema_version", "capability_key", "token_digest", "state", "issued_at_ns", "expires_at_ns",
        "ttl_seconds", "operator_binding_digest", "approved_manifest_digest", "repository_identity",
        "repository_identity_digest", "starting_object_id", "allowlist_digest", "runner_executable_digest",
        "consumed_at_ns", "revoked_at_ns", "revocation_reason", "run_finalized_at_ns", "record_mac",
    }
    if (set(record) != required or record.get("schema_version") != SCHEMA
            or record.get("capability_key") != CAPABILITY_KEY or record.get("token_digest") != digest
            or not hmac.compare_digest(str(record.get("record_mac")), expected)):
        refuse(REASON_STORE_UNAVAILABLE, "capability record is missing, ambiguous, or integrity-invalid")


def _read_record(store: Path, token: str) -> tuple[Path, dict[str, Any]]:
    digest = token_digest(token)
    path = _record_path(store, digest)
    try:
        fd, body = _secure_read(path, label="capability record", maximum=256 * 1024)
        st = os.fstat(fd)
        os.close(fd)
        if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077:
            raise OSError(errno.EACCES, "capability record is not owner-only")
    except OSError as exc:
        refuse(REASON_STORE_UNAVAILABLE, f"capability record unavailable: {exc}")
    record = _json_object(body, label="capability record")
    _validate_record(record, token, digest)
    return path, record


def _atomic_write(path: Path, body: bytes, *, exclusive: bool = False) -> None:
    directory = path.parent
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    tmp_path: Path | None = None
    fd: int | None = None
    try:
        if exclusive:
            fd = os.open(path, flags, 0o600)
        else:
            fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=directory)
            tmp_path = Path(tmp_name)
            os.fchmod(fd, 0o600)
        view = memoryview(body)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
        os.close(fd)
        fd = None
        if tmp_path is not None:
            os.replace(tmp_path, path)
            tmp_path = None
        dir_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError as exc:
        if fd is not None:
            os.close(fd)
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except OSError:
                pass
        refuse(REASON_STORE_UNAVAILABLE, f"capability state write unavailable: {exc}")


def _write_record(path: Path, record: dict[str, Any], token: str, *, exclusive: bool = False) -> None:
    record["record_mac"] = _record_mac(record, token)
    _atomic_write(path, canonical_json(record) + b"\n", exclusive=exclusive)


def _read_token(path_arg: str | Path) -> str:
    try:
        fd, body = _secure_read(Path(path_arg), label="token file", maximum=4096)
        st = os.fstat(fd)
        os.close(fd)
        if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077:
            raise OSError(errno.EACCES, "token file is not owner-only")
        token = body.decode("ascii").strip()
        decoded = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        if len(decoded) != 32:
            raise ValueError("token is not 256 bits")
        return token
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        refuse(REASON_BINDING_AMBIGUOUS, f"token file unavailable or malformed: {exc}")


def issue_capability(*, store_arg: str | Path, token_file_arg: str | Path,
                     manifest_arg: str | Path, approved_manifest_digest: str,
                     allowlist_arg: str | Path, repository_arg: str | Path,
                     starting_object_id: str, ttl_seconds: int,
                     operator_binding_digest: str, now_ns: int | None = None,
                     registration_path: Path = REGISTRATION_PATH) -> dict[str, Any]:
    registration = load_registration(registration_path)
    if not DIGEST_RE.fullmatch(operator_binding_digest):
        refuse(REASON_BINDING_AMBIGUOUS, "operator-session binding digest is malformed")
    ttl = registration["ttl_seconds"]
    if not isinstance(ttl_seconds, int) or not ttl["minimum"] <= ttl_seconds <= ttl["maximum"]:
        refuse(REASON_BINDING_AMBIGUOUS, f"TTL must be {ttl['minimum']}..{ttl['maximum']} seconds")
    starting_object_id = starting_object_id.lower()
    if not OID_RE.fullmatch(starting_object_id):
        refuse(REASON_BINDING_AMBIGUOUS, "starting object id is malformed")
    if not DIGEST_RE.fullmatch(approved_manifest_digest):
        refuse(REASON_BINDING_AMBIGUOUS, "approved manifest digest is malformed")
    repository = Path(repository_arg).resolve(strict=False)
    identity, identity_digest = repository_identity(repository)
    if current_head(repository) != starting_object_id:
        refuse(REASON_STARTING_OID_MISMATCH, "repository HEAD does not equal the approved starting object id")
    try:
        manifest_fd, manifest_body = _secure_read(Path(manifest_arg), label="approved run manifest")
        os.close(manifest_fd)
        allowlist_fd, allowlist_body = _secure_read(Path(allowlist_arg), label="command/pathspec allowlist")
        os.close(allowlist_fd)
    except OSError as exc:
        refuse(REASON_BINDING_AMBIGUOUS, f"issuance binding file unavailable: {exc}")
    if sha256_bytes(manifest_body) != approved_manifest_digest:
        refuse(REASON_MANIFEST_MISMATCH, "run manifest bytes do not match the approved digest")
    allowlist = validate_allowlist(allowlist_body, repository=repository)
    allowlist_digest = sha256_bytes(allowlist_body)
    runner_digest, _ = _read_runner_executable(allowlist, repository=repository)
    store = _store_dir(store_arg, create=True)
    issued_at = time.time_ns() if now_ns is None else now_ns
    if not isinstance(issued_at, int) or issued_at <= 0:
        refuse(REASON_CLOCK_INVALID, "issuance clock is invalid")
    token = secrets.token_urlsafe(32)
    digest = token_digest(token)
    record = {
        "schema_version": SCHEMA, "capability_key": CAPABILITY_KEY,
        "token_digest": digest, "state": "issued", "issued_at_ns": issued_at,
        "expires_at_ns": issued_at + ttl_seconds * 1_000_000_000, "ttl_seconds": ttl_seconds,
        "operator_binding_digest": operator_binding_digest,
        "approved_manifest_digest": approved_manifest_digest,
        "repository_identity": identity, "repository_identity_digest": identity_digest,
        "starting_object_id": starting_object_id, "allowlist_digest": allowlist_digest,
        "runner_executable_digest": runner_digest,
        "consumed_at_ns": None, "revoked_at_ns": None, "revocation_reason": None,
        "run_finalized_at_ns": None, "record_mac": "",
    }
    with StoreLock(store):
        # Token file first, exclusively: a caller who names an existing token
        # path collides here and is refused BEFORE any record is written, so a
        # collision leaves no orphaned unreachable record behind. A record-side
        # collision would require a repeated 256-bit token, which cannot occur.
        _atomic_write(Path(token_file_arg), (token + "\n").encode("ascii"), exclusive=True)
        _write_record(_record_path(store, digest), record, token, exclusive=True)
    return {
        "ok": True, "capability_key": CAPABILITY_KEY, "token_digest": digest,
        "operator_binding_digest": operator_binding_digest,
        "approved_manifest_digest": approved_manifest_digest,
        "repository_identity_digest": identity_digest, "starting_object_id": starting_object_id,
        "allowlist_digest": allowlist_digest, "runner_executable_digest": runner_digest,
        "issued_at_ns": issued_at, "expires_at_ns": record["expires_at_ns"],
    }


def _replace_placeholders(argv: Sequence[str], values: Mapping[str, int]) -> list[str]:
    replaced: list[str] = []
    for value in argv:
        for key, fd in values.items():
            value = value.replace(key, str(fd))
        replaced.append(value)
    if any("{" in value or "}" in value for value in replaced):
        refuse(REASON_BINDING_AMBIGUOUS, "runner argv contains an unknown placeholder")
    return replaced


def _validate_redemption(*, store: Path, token: str, manifest_arg: str | Path,
                         allowlist_arg: str | Path, repository_arg: str | Path,
                         operator_binding_digest: str, now_ns: int
                         ) -> tuple[Path, dict[str, Any], bytes, bytes, dict[str, Any], bytes]:
    record_path, record = _read_record(store, token)
    # Order is deliberate: each packet-mandated denial has its own reason.
    if record.get("consumed_at_ns") is not None:
        refuse(REASON_ALREADY_CONSUMED, "token was already consumed by its first runner invocation")
    if record.get("state") == "revoked":
        refuse(REASON_REVOKED, "token was revoked before redemption")
    if operator_binding_digest != record.get("operator_binding_digest"):
        refuse(REASON_OPERATOR_MISMATCH, "native operator-session identity differs from issuance")
    # Read both binding files fully, then close the on-disk fds immediately. The
    # bytes we digest here are the bytes the caller will run, because run_capability
    # copies these same in-memory bodies into read-only unlinked fds — nothing the
    # runner reads is a live rewritable inode. So no later refusal needs fd cleanup.
    try:
        manifest_fd, manifest_body = _secure_read(Path(manifest_arg), label="approved run manifest")
        os.close(manifest_fd)
        allowlist_fd, allowlist_body = _secure_read(Path(allowlist_arg), label="command/pathspec allowlist")
        os.close(allowlist_fd)
    except OSError as exc:
        refuse(REASON_BINDING_AMBIGUOUS, f"redemption binding file unavailable: {exc}")
    if sha256_bytes(manifest_body) != record.get("approved_manifest_digest"):
        refuse(REASON_MANIFEST_MISMATCH, "run manifest drifted after approval or issuance")
    if sha256_bytes(allowlist_body) != record.get("allowlist_digest"):
        refuse(REASON_ALLOWLIST_MISMATCH, "command/pathspec allowlist drifted after issuance")
    repository = Path(repository_arg).resolve(strict=False)
    identity, identity_digest = repository_identity(repository)
    if identity_digest != record.get("repository_identity_digest") or identity != record.get("repository_identity"):
        refuse(REASON_REPOSITORY_MISMATCH, "repository identity differs from issuance")
    if current_head(repository) != record.get("starting_object_id"):
        refuse(REASON_STARTING_OID_MISMATCH, "repository HEAD drifted from the approved starting object id")
    allowlist = validate_allowlist(allowlist_body, repository=repository)
    # Bind the runner's CODE, not just its path: HEAD is unchanged by a
    # working-tree rewrite, so a runner edited in place inside the TTL window
    # would otherwise be admitted. Refused with its own distinct reason. The
    # verified bytes are RETURNED so run_capability executes a snapshot of
    # exactly them — there is no second read of the path to race against exec.
    runner_digest, runner_body = _read_runner_executable(allowlist, repository=repository)
    if runner_digest != record.get("runner_executable_digest"):
        refuse(REASON_RUNNER_DIGEST_MISMATCH, "runner executable bytes drifted after issuance")
    issued_at, expires_at = record.get("issued_at_ns"), record.get("expires_at_ns")
    if not isinstance(now_ns, int) or not isinstance(issued_at, int) or not isinstance(expires_at, int) or now_ns < issued_at:
        refuse(REASON_CLOCK_INVALID, "redemption clock is invalid or moved backwards")
    if now_ns >= expires_at:
        record["state"] = "revoked"
        record["revoked_at_ns"] = now_ns
        record["revocation_reason"] = "expired"
        _write_record(record_path, record, token)
        refuse(REASON_EXPIRED, "token TTL expired before redemption")
    return record_path, record, manifest_body, allowlist_body, allowlist, runner_body


def run_capability(*, store_arg: str | Path, token_file_arg: str | Path,
                   manifest_arg: str | Path, allowlist_arg: str | Path,
                   repository_arg: str | Path, operator_binding_digest: str,
                   now_ns: int | None = None, registration_path: Path = REGISTRATION_PATH) -> int:
    load_registration(registration_path)
    token = _read_token(token_file_arg)
    store = _store_dir(store_arg, create=False)
    actual_now = time.time_ns() if now_ns is None else now_ns
    child: subprocess.Popen[Any] | None = None
    old_handlers: dict[signal.Signals, Any] = {}
    interrupted: list[int] = []
    record_path: Path | None = None
    record: dict[str, Any] | None = None
    manifest_fd = allowlist_fd = receipt_fd = None
    snapshot: _RunnerSnapshot | None = None

    def on_signal(signum: int, _frame: object) -> None:
        interrupted.append(signum)
        if child is not None and child.poll() is None:
            # The reviewed runner may itself have children.  It starts in its
            # own process group so a capability abort stops the whole admitted
            # run, not merely its top-level Python process.  SIGKILL is confined
            # to that disposable group; the parent survives to persist finally
            # revocation before returning the original interruption reason.
            os.killpg(child.pid, signal.SIGKILL)

    with StoreLock(store):
        record_path, record, manifest_body, allowlist_body, allowlist, runner_body = _validate_redemption(
            store=store, token=token, manifest_arg=manifest_arg, allowlist_arg=allowlist_arg,
            repository_arg=repository_arg, operator_binding_digest=operator_binding_digest,
            now_ns=actual_now,
        )
        record["state"] = "consumed"
        record["consumed_at_ns"] = actual_now
        _write_record(record_path, record, token)

    receipt = {
        "schema_version": "carr.settlement-capability-redemption.v1",
        "capability_key": CAPABILITY_KEY, "token_digest": record["token_digest"],
        "operator_binding_digest": record["operator_binding_digest"],
        "approved_manifest_digest": record["approved_manifest_digest"],
        "repository_identity_digest": record["repository_identity_digest"],
        "starting_object_id": record["starting_object_id"],
        "allowlist_digest": record["allowlist_digest"], "consumed_at_ns": record["consumed_at_ns"],
    }
    try:
        # Execute a private mode-0500 SNAPSHOT of the exact bytes that were
        # verified under lock — not the original path. Between the digest check
        # and here, three fd copies are built (real time, not a microsecond), so
        # re-executing the live path would leave a same-inode-rewrite race. The
        # snapshot is made from the in-memory verified bytes, so what runs is
        # provably what was checked, with no re-read to race.
        snapshot = _RunnerSnapshot(runner_body, suffix=Path(allowlist["runner_argv"][0]).suffix)
        # The runner reads read-only, unlinked copies of the digest-verified
        # bytes — not live fds to files a concurrent writer, or the runner
        # itself, can rewrite.
        manifest_fd = _anon_fd(manifest_body, prefix="settlement-capability-manifest-")
        allowlist_fd = _anon_fd(allowlist_body, prefix="settlement-capability-allowlist-")
        receipt_fd = _anon_fd(canonical_json(receipt) + b"\n", prefix="settlement-capability-receipt-")
        argv = _replace_placeholders(allowlist["runner_argv"], {
            "{manifest_fd}": manifest_fd, "{allowlist_fd}": allowlist_fd,
            "{capability_receipt_fd}": receipt_fd,
        })
        argv[0] = str(snapshot.path)
        for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            old_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, on_signal)
        child = subprocess.Popen(
            argv, cwd=str(Path(repository_arg).resolve()), env=scrubbed_env(),
            pass_fds=(manifest_fd, allowlist_fd, receipt_fd), start_new_session=True,
        )
        if interrupted and child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
        exit_code = child.wait()
        if interrupted:
            raise RunInterrupted(interrupted[0])
        return exit_code
    finally:
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
        for fd in (manifest_fd, allowlist_fd, receipt_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        if snapshot is not None:
            snapshot.cleanup()
        if record_path is not None and record is not None:
            finalized_at = max(time.time_ns(), int(record["consumed_at_ns"]))
            with StoreLock(store):
                _, current = _read_record(store, token)
                # The token is always spent (revoked) after a run. But the run's
                # finally must NOT erase a reason already on the record — an AO
                # reviewer's abort, or an expiry — which is the only durable
                # record of it. Mirror revoke_capability's guard: claim the
                # reason only when none exists; otherwise preserve it and record
                # the finally as its own timestamped event.
                current["state"] = "revoked"
                if current.get("revocation_reason") is None:
                    current["revoked_at_ns"] = finalized_at
                    current["revocation_reason"] = "run_finally"
                current["run_finalized_at_ns"] = finalized_at
                _write_record(record_path, current, token)


def revoke_capability(*, store_arg: str | Path, token_file_arg: str | Path,
                      reason: str, now_ns: int | None = None,
                      registration_path: Path = REGISTRATION_PATH) -> dict[str, Any]:
    load_registration(registration_path)
    if not ID_RE.fullmatch(reason):
        refuse(REASON_BINDING_AMBIGUOUS, "revocation reason must be a stable identifier")
    token = _read_token(token_file_arg)
    store = _store_dir(store_arg, create=False)
    revoked_at = time.time_ns() if now_ns is None else now_ns
    if not isinstance(revoked_at, int) or revoked_at <= 0:
        refuse(REASON_CLOCK_INVALID, "revocation clock is invalid")
    with StoreLock(store):
        path, record = _read_record(store, token)
        if record.get("state") != "revoked":
            record["state"] = "revoked"
            record["revoked_at_ns"] = revoked_at
            record["revocation_reason"] = reason
            _write_record(path, record, token)
    return {
        "ok": True, "token_digest": record["token_digest"], "state": "revoked",
        "revoked_at_ns": record["revoked_at_ns"], "revocation_reason": record["revocation_reason"],
    }


def status_capability(*, store_arg: str | Path, token_file_arg: str | Path,
                      registration_path: Path = REGISTRATION_PATH) -> dict[str, Any]:
    load_registration(registration_path)
    token = _read_token(token_file_arg)
    store = _store_dir(store_arg, create=False)
    with StoreLock(store):
        _, record = _read_record(store, token)
    return {key: value for key, value in record.items() if key not in {"record_mac", "repository_identity"}}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    issue = sub.add_parser("issue")
    for flag in ("store", "token-file", "manifest", "approved-manifest-digest", "allowlist", "repository", "starting-oid"):
        issue.add_argument(f"--{flag}", required=True)
    issue.add_argument("--ttl-seconds", required=True, type=int)
    run = sub.add_parser("run")
    for flag in ("store", "token-file", "manifest", "allowlist", "repository"):
        run.add_argument(f"--{flag}", required=True)
    revoke = sub.add_parser("revoke")
    revoke.add_argument("--store", required=True)
    revoke.add_argument("--token-file", required=True)
    revoke.add_argument("--reason", default="ao_reviewer_abort")
    status_parser = sub.add_parser("status")
    status_parser.add_argument("--store", required=True)
    status_parser.add_argument("--token-file", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "issue":
            print(json.dumps(issue_capability(
                store_arg=args.store, token_file_arg=args.token_file, manifest_arg=args.manifest,
                approved_manifest_digest=args.approved_manifest_digest, allowlist_arg=args.allowlist,
                repository_arg=args.repository, starting_object_id=args.starting_oid,
                ttl_seconds=args.ttl_seconds, operator_binding_digest=operator_binding_from_env(),
            ), sort_keys=True))
            return 0
        if args.command == "run":
            return run_capability(
                store_arg=args.store, token_file_arg=args.token_file, manifest_arg=args.manifest,
                allowlist_arg=args.allowlist, repository_arg=args.repository,
                operator_binding_digest=operator_binding_from_env(),
            )
        if args.command == "revoke":
            print(json.dumps(revoke_capability(
                store_arg=args.store, token_file_arg=args.token_file, reason=args.reason,
            ), sort_keys=True))
            return 0
        print(json.dumps(status_capability(
            store_arg=args.store, token_file_arg=args.token_file,
        ), sort_keys=True))
        return 0
    except RunInterrupted as exc:
        print(f"INTERRUPTED settlement_capability.run.signal_{exc.signum}: {exc}", file=sys.stderr)
        return 128 + exc.signum
    except Refusal as exc:
        print(f"REFUSED {exc.reason_id}: {exc.detail}", file=sys.stderr)
        return 78


if __name__ == "__main__":
    raise SystemExit(main())
