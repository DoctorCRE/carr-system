#!/usr/bin/env python3
"""Recoverable, file-only credentials for isolated-staging login roles.

The credential is born as ``*.pending`` and is promoted only after the login
role authenticates with its exact closed authority profile.  A crash never
causes regeneration: either file is independently sufficient to resume.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import pathlib
import secrets
import stat
from dataclasses import dataclass
from typing import Callable, Iterator
from urllib.parse import parse_qsl, quote, unquote, urlsplit, urlunsplit


WRITER_KEY = "CARR_DB_STAGING_WRITER_URL"
READER_KEY = "CARR_DB_STAGING_READER_URL"
# The Gate Zero producer seat's own credential file and key. Standing-rule
# amendment 9 (2026-09-14): seat-only write is enforced by CONNECTION ROLE, so
# the one verb that records a Gate Zero read-only outcome authenticates as its
# own login role. Its file is separate from the writer's for the same reason its
# role is: a credential sharing a file with the writer's would be one edit away
# from being the writer's.
# The seat role name is CLUSTER-WIDE and identical in every environment, so the
# key and the filename are the only things that say WHICH environment's DSN this
# is. Both name staging out loud, and this module holds no production
# counterpart: the production credential is Joe's own act at a console he
# controls (Neon Database SOP `01-connections-and-roles`, then Cloudflare Edge
# SOP `02-secrets-and-tokens`), and nothing in this repository mints it.
GATE_ZERO_PRODUCER_KEY = "CARR_DB_STAGING_GATE_ZERO_WRITER_URL"
FOUNDATION_ASSURANCE_ORACLE_KEY = "CARR_DB_STAGING_FOUNDATION_ASSURANCE_WRITER_URL"


class CredentialRefusal(RuntimeError):
    """Credential state is ambiguous, unsafe, or outside isolated staging."""


@dataclass(frozen=True)
class CredentialPaths:
    final: pathlib.Path
    pending: pathlib.Path


@dataclass(frozen=True)
class CredentialProfile:
    label: str
    role_name: str
    bundle_role: str
    key: str
    paths: CredentialPaths


@dataclass(frozen=True)
class StoredCredential:
    state: str
    path: pathlib.Path
    value: str
    password: str
    endpoint: str
    port: int
    database: str


def _config_root() -> pathlib.Path:
    return pathlib.Path.home() / ".config" / "carr"


def profile(label: str, *, config_root: pathlib.Path | None = None) -> CredentialProfile:
    root = config_root or _config_root()
    profiles = {
        "writer": ("app_writer", "carr_writer", WRITER_KEY, "staging-writer.env"),
        "reader": ("app_reader", "carr_reader", READER_KEY, "staging-reader.env"),
        # A DIRECT-GRANT PROFILE: the seat is one LOGIN role and there is no
        # bundle behind it, so the bundle column names the role itself.
        "gate_zero_producer": ("carr_gate_zero_producer", "carr_gate_zero_producer",
                               GATE_ZERO_PRODUCER_KEY, "staging-gate-zero-writer.env"),
        "foundation_assurance_oracle": (
            "carr_foundation_assurance_oracle", "carr_foundation_assurance_oracle",
            FOUNDATION_ASSURANCE_ORACLE_KEY, "staging-foundation-assurance-writer.env"),
    }
    try:
        role_name, bundle_role, key, filename = profiles[label]
    except KeyError as exc:
        raise CredentialRefusal(
            "credential profile must be one of: " + ", ".join(sorted(profiles))) from exc
    final = root / filename
    return CredentialProfile(
        label, role_name, bundle_role, key,
        CredentialPaths(final=final, pending=pathlib.Path(str(final) + ".pending")),
    )


def _safe_lstat(path: pathlib.Path) -> os.stat_result | None:
    try:
        result = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(result.st_mode) or stat.S_ISLNK(result.st_mode):
        raise CredentialRefusal("credential path is not a regular non-symlink file")
    if result.st_uid != os.getuid():
        raise CredentialRefusal("credential file is not owned by the current user")
    if stat.S_IMODE(result.st_mode) != 0o600:
        raise CredentialRefusal("credential file mode must be exactly 0600")
    if result.st_nlink != 1:
        raise CredentialRefusal("credential file must have exactly one hard link")
    if result.st_size > 4096:
        raise CredentialRefusal("credential file is unexpectedly large")
    return result


def _validate_private_directory(path: pathlib.Path) -> None:
    try:
        result = path.lstat()
    except FileNotFoundError as exc:
        raise CredentialRefusal("credential directory is absent") from exc
    if not stat.S_ISDIR(result.st_mode) or stat.S_ISLNK(result.st_mode):
        raise CredentialRefusal("credential directory must be a non-symlink directory")
    if result.st_uid != os.getuid() or stat.S_IMODE(result.st_mode) & 0o077:
        raise CredentialRefusal(
            "credential directory must be current-user-owned and inaccessible by group or other"
        )


def _secure_read(path: pathlib.Path) -> bytes:
    _validate_private_directory(path.parent)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CredentialRefusal("could not open staging credential securely") from exc
    try:
        result = os.fstat(descriptor)
        if (
            not stat.S_ISREG(result.st_mode)
            or result.st_uid != os.getuid()
            or result.st_nlink != 1
            or stat.S_IMODE(result.st_mode) != 0o600
            or result.st_size > 4096
        ):
            raise CredentialRefusal("credential changed or is not a private regular file")
        chunks: list[bytes] = []
        remaining = 4097
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > 4096:
            raise CredentialRefusal("credential file is unexpectedly large")
        return raw
    finally:
        os.close(descriptor)


def validate_uri(
    value: str, *, role_name: str, expected_endpoint: str,
    expected_port: int, expected_database: str,
) -> tuple[str, str, int, str]:
    try:
        parsed = urlsplit(value)
        port = parsed.port or 5432
    except ValueError as exc:
        raise CredentialRefusal("staging credential URI is invalid") from exc
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    endpoint = (parsed.hostname or "").lower().rstrip(".")
    database = unquote(parsed.path.lstrip("/"))
    try:
        rows = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise CredentialRefusal("staging credential query is invalid") from exc
    query = dict(rows)
    query_ok = (
        len(rows) == len(query)
        and query.get("sslmode") == "require"
        and set(query).issubset({"sslmode", "channel_binding"})
        and ("channel_binding" not in query or query["channel_binding"] == "require")
    )
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or username != role_name
        or not password
        or endpoint != expected_endpoint.lower().rstrip(".")
        or port != expected_port
        or database != expected_database
        or parsed.fragment
        or not query_ok
    ):
        raise CredentialRefusal(
            "staging credential username, endpoint, port, or database is outside the pinned target"
        )
    return password, endpoint, port, database


def _read_exact(
    path: pathlib.Path, *, key: str, role_name: str, expected_endpoint: str,
    expected_port: int, expected_database: str, state: str,
) -> StoredCredential:
    _safe_lstat(path)
    raw = _secure_read(path)
    prefix = (key + "=").encode()
    if not raw.startswith(prefix) or raw.count(b"\n") != 1 or not raw.endswith(b"\n"):
        raise CredentialRefusal("credential file must contain exactly its one assigned key")
    value_bytes = raw[len(prefix):-1]
    if not value_bytes or b"\r" in value_bytes or b"\x00" in value_bytes:
        raise CredentialRefusal("credential value has an invalid shape")
    try:
        value = value_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CredentialRefusal("credential value is not UTF-8") from exc
    password, endpoint, port, database = validate_uri(
        value, role_name=role_name, expected_endpoint=expected_endpoint,
        expected_port=expected_port, expected_database=expected_database,
    )
    return StoredCredential(state, path, value, password, endpoint, port, database)


def load_existing(
    paths: CredentialPaths, *, key: str, role_name: str,
    expected_endpoint: str, expected_port: int, expected_database: str,
) -> StoredCredential:
    final = _safe_lstat(paths.final)
    pending = _safe_lstat(paths.pending)
    if final is not None and pending is not None:
        raise CredentialRefusal("both final and pending staging credentials exist")
    if final is None and pending is None:
        raise CredentialRefusal("staging credential is absent")
    if final is not None:
        return _read_exact(
            paths.final, key=key, role_name=role_name,
            expected_endpoint=expected_endpoint, expected_port=expected_port,
            expected_database=expected_database, state="final",
        )
    return _read_exact(
        paths.pending, key=key, role_name=role_name,
        expected_endpoint=expected_endpoint, expected_port=expected_port,
        expected_database=expected_database, state="pending",
    )


def file_state(paths: CredentialPaths) -> str:
    final = _safe_lstat(paths.final)
    pending = _safe_lstat(paths.pending)
    if final is not None:
        _validate_private_directory(paths.final.parent)
    if pending is not None:
        _validate_private_directory(paths.pending.parent)
    if final is not None and pending is not None:
        raise CredentialRefusal("both final and pending staging credentials exist")
    if final is not None:
        return "final"
    if pending is not None:
        return "pending"
    return "absent"


def load_for_endpoint_id(
    paths: CredentialPaths, *, key: str, role_name: str,
    expected_endpoint_id: str, expected_port: int, expected_database: str,
) -> StoredCredential:
    """Bind a secure file to a provider-resolved endpoint without DSN reveal."""
    if not expected_endpoint_id.startswith("ep-"):
        raise CredentialRefusal("provider endpoint id is invalid")
    final = _safe_lstat(paths.final)
    pending = _safe_lstat(paths.pending)
    if final is not None and pending is not None:
        raise CredentialRefusal("both final and pending staging credentials exist")
    path = paths.final if final is not None else paths.pending if pending is not None else None
    if path is None:
        raise CredentialRefusal("staging credential is absent")
    _validate_private_directory(path.parent)
    raw = _secure_read(path)
    prefix = (key + "=").encode()
    if not raw.startswith(prefix) or raw.count(b"\n") != 1 or not raw.endswith(b"\n"):
        raise CredentialRefusal("credential file must contain exactly its one assigned key")
    try:
        value = raw[len(prefix):-1].decode("utf-8")
        host = (urlsplit(value).hostname or "").lower().rstrip(".")
    except (UnicodeDecodeError, ValueError) as exc:
        raise CredentialRefusal("credential URI is invalid") from exc
    if host.split(".", 1)[0] != expected_endpoint_id or not host.endswith(".neon.tech"):
        raise CredentialRefusal("credential host is not the provider-resolved staging endpoint")
    return load_existing(
        paths, key=key, role_name=role_name, expected_endpoint=host,
        expected_port=expected_port, expected_database=expected_database,
    )


def build_role_uri(owner_uri: str, role_name: str, password: str) -> str:
    """Public (2026-08-18): tools/staging_jobs_dsn.py builds isolated staging's
    ephemeral carr_jobs DSN from the same owner URI, in the same shape, and
    validates it with validate_uri below — rule a8c55a47, one construction of a
    staging login URI rather than two that can drift. Was `_build_role_uri`; no
    caller outside this file referenced the old name."""
    try:
        parsed = urlsplit(owner_uri)
        port = parsed.port
    except ValueError as exc:
        raise CredentialRefusal("owner URI is invalid") from exc
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or unquote(parsed.username or "") != "neondb_owner"
        or not parsed.hostname
        or not parsed.path.lstrip("/")
        or not password
    ):
        raise CredentialRefusal("owner URI cannot be used to construct a staging login credential")
    host = parsed.hostname.lower().rstrip(".")
    host_port = f"{host}:{port}" if port is not None else host
    netloc = f"{quote(role_name, safe='')}:{quote(password, safe='')}@{host_port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, ""))


def _fsync_directory(path: pathlib.Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _preparing_path(paths: CredentialPaths) -> pathlib.Path:
    return pathlib.Path(str(paths.pending) + ".preparing")


def _publish_preparing(
    preparing: pathlib.Path, paths: CredentialPaths, *, key: str, role_name: str,
    expected_endpoint: str, expected_port: int, expected_database: str,
) -> StoredCredential | None:
    """Resume a durable pre-publication file, or discard only malformed debris."""
    if _safe_lstat(preparing) is None:
        return None
    try:
        staged = _read_exact(
            preparing, key=key, role_name=role_name,
            expected_endpoint=expected_endpoint, expected_port=expected_port,
            expected_database=expected_database, state="preparing",
        )
    except CredentialRefusal:
        # The private fixed-name file can only be this operation's incomplete
        # pre-publication write. It contains no usable credential, so remove it
        # and generate a complete replacement without manual surgery.
        preparing.unlink()
        _fsync_directory(preparing.parent)
        return None
    os.replace(staged.path, paths.pending)
    _fsync_directory(paths.pending.parent)
    return _read_exact(
        paths.pending, key=key, role_name=role_name,
        expected_endpoint=expected_endpoint, expected_port=expected_port,
        expected_database=expected_database, state="pending",
    )


def prepare_pending(
    paths: CredentialPaths, *, key: str, role_name: str, owner_uri: str,
    expected_endpoint: str, expected_port: int, expected_database: str,
    password_factory: Callable[[], str] = lambda: secrets.token_urlsafe(48),
    boundary: Callable[[str], None] = lambda _boundary: None,
) -> StoredCredential:
    try:
        return load_existing(
            paths, key=key, role_name=role_name,
            expected_endpoint=expected_endpoint, expected_port=expected_port,
            expected_database=expected_database,
        )
    except CredentialRefusal as exc:
        if "is absent" not in str(exc):
            raise
    paths.final.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _validate_private_directory(paths.final.parent)
    preparing = _preparing_path(paths)
    resumed = _publish_preparing(
        preparing, paths, key=key, role_name=role_name,
        expected_endpoint=expected_endpoint, expected_port=expected_port,
        expected_database=expected_database,
    )
    if resumed is not None:
        return resumed
    password = password_factory()
    if len(password.encode()) < 32 or any(ch.isspace() for ch in password):
        raise CredentialRefusal("generated password is not at least 256 bits of non-whitespace material")
    value = build_role_uri(owner_uri, role_name, password)
    validate_uri(
        value, role_name=role_name, expected_endpoint=expected_endpoint,
        expected_port=expected_port, expected_database=expected_database,
    )
    raw = f"{key}={value}\n".encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(preparing, flags, 0o600)
    except FileExistsError:
        resumed = _publish_preparing(
            preparing, paths, key=key, role_name=role_name,
            expected_endpoint=expected_endpoint, expected_port=expected_port,
            expected_database=expected_database,
        )
        if resumed is None:
            raise CredentialRefusal("credential preparation raced and left no resumable file")
        return resumed
    try:
        os.fchmod(descriptor, 0o600)
        boundary("after_open")
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise CredentialRefusal("credential write was incomplete")
            offset += written
        boundary("after_write")
        os.fsync(descriptor)
        boundary("after_fsync")
    except Exception:
        try:
            preparing.unlink()
        except OSError:
            pass
        raise
    finally:
        os.close(descriptor)
    os.replace(preparing, paths.pending)
    boundary("after_publish")
    _fsync_directory(paths.final.parent)
    return _read_exact(
        paths.pending, key=key, role_name=role_name,
        expected_endpoint=expected_endpoint, expected_port=expected_port,
        expected_database=expected_database, state="pending",
    )


def promote_pending(paths: CredentialPaths, *, key: str, expected_value: str) -> None:
    if _safe_lstat(paths.final) is not None:
        raise CredentialRefusal("final credential already exists")
    if _safe_lstat(paths.pending) is None:
        raise CredentialRefusal("pending credential is absent")
    _validate_private_directory(paths.pending.parent)
    raw = _secure_read(paths.pending)
    if raw != f"{key}={expected_value}\n".encode():
        raise CredentialRefusal("pending credential changed before promotion")
    os.replace(paths.pending, paths.final)
    os.chmod(paths.final, 0o600)
    with paths.final.open("rb") as handle:
        os.fsync(handle.fileno())
    _fsync_directory(paths.final.parent)


@contextlib.contextmanager
def exclusive_lock(path: pathlib.Path) -> Iterator[None]:
    """Process lock shared by cleanup and provisioning, with no symlink seam."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _validate_private_directory(path.parent)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        result = os.fstat(descriptor)
        if (
            not stat.S_ISREG(result.st_mode)
            or result.st_uid != os.getuid()
            or result.st_nlink != 1
            or stat.S_IMODE(result.st_mode) != 0o600
        ):
            raise CredentialRefusal("staging role lock file is not a private regular file")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except BlockingIOError as exc:
        raise CredentialRefusal("another staging role operation holds the local lock") from exc
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
