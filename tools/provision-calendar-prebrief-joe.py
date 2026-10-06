#!/usr/bin/env python3
"""Provision Joe's three Calendar-prebrief identities and sealed local profiles.

The production owner credential is accepted *only* through
``CARR_DB_CALENDAR_PREBRIEF_PRODUCTION_OWNER_URL``.  It is never accepted as an
argument, printed, written, or passed to a child process.  The tool is bounded
to the pinned production project/default branch/read-write endpoint and writes
only Joe's live profile set; it neither reads nor creates a partner's profile
or a canary identity.

``--apply`` is intentionally required.  Dry-run validates every local input
and provider binding, but never changes roles, passwords, keys, or files.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import secrets
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from lib.machine_prerequisites import openssl_executable  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]
OWNER_ENV = "CARR_DB_CALENDAR_PREBRIEF_PRODUCTION_OWNER_URL"
PRODUCTION_BRANCH_NAME = "production"
OWNER_ROLE = "neondb_owner"
DEFAULT_VERSION = "eventkit-1.0"


def _module(name: str, path: pathlib.Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


db_tap = _module("calendar_prebrief_joe_provision_db_tap", REPO / "tools" / "db-tap.py")
PRODUCTION_PROJECT_ID = str(db_tap.PROJECTS["production"]["id"])
NEON_ORG = str(db_tap.NEON_ORG)
NEONCTL = str(db_tap.NEONCTL)


class ProvisioningRefusal(RuntimeError):
    """A request was outside this exact production-safe provisioner contract."""


@dataclass(frozen=True)
class ProductionScope:
    project_id: str
    branch_id: str
    endpoint_id: str
    endpoint_host: str
    port: int
    database: str


@dataclass(frozen=True)
class ParsedDsn:
    role: str
    password: str
    host: str
    port: int
    database: str


@dataclass(frozen=True)
class RoleProfile:
    role: str
    bundle: str
    env_key: str


NEW_PROFILES = (
    RoleProfile("carr_calendar_prebrief_attestor_joe", "carr_calendar_prebrief_attestors", "CARR_DB_CALENDAR_PREBRIEF_ATTESTOR_JOE_URL"),
    RoleProfile("carr_calendar_prebrief_resolver_joe", "carr_calendar_prebrief_email_resolver", "CARR_DB_CALENDAR_PREBRIEF_RESOLVER_JOE_URL"),
    RoleProfile("carr_calendar_prebrief_joe", "carr_calendar_prebrief_jobs", "CARR_DB_CALENDAR_PREBRIEF_JOE_URL"),
)
EXISTING_IDENTITIES = {
    "CARR_DB_AUTHORITY_JOE_URL": ("carr_authority_joe", "carr_authority"),
    "CARR_DB_JOBS_URL": ("carr_jobs", "carr_jobs"),
}


@dataclass(frozen=True)
class ProfilePaths:
    activation: pathlib.Path
    child: pathlib.Path
    runtime: pathlib.Path
    private_key: pathlib.Path
    public_key: pathlib.Path
    allowlist: pathlib.Path


def _secure_regular(path: pathlib.Path, label: str, mode: int | None = None) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ProvisioningRefusal(f"{label} is unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ProvisioningRefusal(f"{label} must be a regular non-symlink")
    if mode is not None and stat.S_IMODE(info.st_mode) != mode:
        raise ProvisioningRefusal(f"{label} must have mode {mode:04o}")


def _trust_root(value: str | pathlib.Path) -> pathlib.Path:
    path = pathlib.Path(value)
    if not path.is_absolute():
        raise ProvisioningRefusal("trust root must be an absolute path")
    _secure_regular(path, "trust root")
    if stat.S_IMODE(path.lstat().st_mode) & 0o022:
        raise ProvisioningRefusal("trust root must not be group or world writable")
    return path


def _query(value: str) -> dict[str, str]:
    try:
        pairs = parse_qsl(value, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise ProvisioningRefusal("database URI query is malformed") from exc
    query = dict(pairs)
    if len(pairs) != len(query):
        raise ProvisioningRefusal("database URI query has duplicate keys")
    return query


def _parsed_dsn(value: str) -> ParsedDsn:
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port or 5432
    except ValueError as exc:
        raise ProvisioningRefusal("database URI is malformed") from exc
    role, password = unquote(parsed.username or ""), unquote(parsed.password or "")
    host, database = (parsed.hostname or "").lower().rstrip("."), unquote(parsed.path.lstrip("/"))
    if (parsed.scheme not in {"postgres", "postgresql"} or not role or not password or not host
            or not database or parsed.fragment or port != 5432):
        raise ProvisioningRefusal("database URI has an unsafe endpoint shape")
    return ParsedDsn(role, password, host, port, database)


def _expected_query(root: pathlib.Path) -> dict[str, str]:
    return {"sslmode": "verify-full", "sslrootcert": str(root), "channel_binding": "require"}


def parse_owner_dsn(value: str, trust_root: str | pathlib.Path) -> ParsedDsn:
    """Validate the owner DSN without ever returning or logging the original text."""
    root = _trust_root(trust_root)
    parsed = _parsed_dsn(value)
    if parsed.role != OWNER_ROLE or _query(urlsplit(value.strip()).query) != _expected_query(root):
        raise ProvisioningRefusal("production owner credential must use exact owner verify-full trust")
    return parsed


def _validate_existing_query(value: str) -> None:
    query = _query(urlsplit(value.strip()).query)
    allowed = (
        {"sslmode": "require", "channel_binding": "require"},
        {"sslmode": "verify-full", "channel_binding": "require", "sslrootcert": query.get("sslrootcert", "")},
    )
    if query not in allowed or (query.get("sslmode") == "verify-full" and not query.get("sslrootcert", "").startswith("/")):
        raise ProvisioningRefusal("existing identity URI has an unsafe query")


def transform_dsn(value: str, expected_role: str, owner: ParsedDsn, trust_root: str | pathlib.Path) -> str:
    """Retain the existing role password but bind its URI to the pinned target."""
    root = _trust_root(trust_root)
    parsed = _parsed_dsn(value)
    _validate_existing_query(value)
    if (parsed.role != expected_role or (parsed.host, parsed.port, parsed.database)
            != (owner.host, owner.port, owner.database)):
        raise ProvisioningRefusal("existing identity does not target the pinned production endpoint")
    return build_dsn(parsed.role, parsed.password, owner, root)


def build_dsn(role: str, password: str, owner: ParsedDsn, trust_root: pathlib.Path) -> str:
    authority = quote(role, safe="") + ":" + quote(password, safe="")
    netloc = authority + "@" + owner.host + ":" + str(owner.port)
    return urlunsplit(("postgresql", netloc, "/" + quote(owner.database, safe=""),
                       urlencode(_expected_query(trust_root)), ""))


def production_scope(projects: Sequence[Mapping[str, Any]], branches: Sequence[Mapping[str, Any]],
                     endpoints: Sequence[Mapping[str, Any]], owner_host: str, owner_port: int,
                     owner_database: str) -> ProductionScope:
    projects_exact = [row for row in projects if str(row.get("id") or "") == PRODUCTION_PROJECT_ID]
    if len(projects_exact) != 1 or len([row for row in projects if str(row.get("id") or "") == PRODUCTION_PROJECT_ID]) != 1:
        raise ProvisioningRefusal("pinned production project was not returned exactly once")
    branch = [row for row in branches if (str(row.get("project_id") or "") == PRODUCTION_PROJECT_ID
                                         and row.get("name") == PRODUCTION_BRANCH_NAME and row.get("default") is True)]
    if len(branch) != 1 or not str(branch[0].get("id") or ""):
        raise ProvisioningRefusal("pinned production default branch was not returned exactly once")
    branch_id = str(branch[0]["id"])
    endpoint = [row for row in endpoints if str(row.get("branch_id") or "") == branch_id
                and row.get("type") in {"read_write", "read-write", "rw"}]
    if len(endpoint) != 1:
        raise ProvisioningRefusal("pinned production branch must have one read-write endpoint")
    endpoint_id = str(endpoint[0].get("id") or "")
    endpoint_host = str(endpoint[0].get("host") or "").lower().rstrip(".")
    if (not endpoint_id.startswith("ep-") or not endpoint_host.startswith(endpoint_id + ".")
            or not endpoint_host.endswith(".neon.tech") or owner_port != 5432
            or owner_database != "neondb" or owner_host != endpoint_host):
        raise ProvisioningRefusal("owner credential does not match the pinned production endpoint")
    return ProductionScope(PRODUCTION_PROJECT_ID, branch_id, endpoint_id, endpoint_host, 5432, "neondb")


Run = Callable[..., subprocess.CompletedProcess[str]]


def _provider_json(args: Sequence[str], *, run: Run, environ: Mapping[str, str]) -> Any:
    env = dict(environ)
    # The provider only needs its API credential.  The owner DSN is deliberately
    # not inherited by neonctl (or any future provider child process).
    env.pop(OWNER_ENV, None)
    api_key = db_tap._neon_api_key()
    if api_key:
        env["NEON_API_KEY"] = api_key
    try:
        result = run(list(args), capture_output=True, text=True, timeout=60, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProvisioningRefusal("production provider lookup did not complete; output suppressed") from exc
    if result.returncode:
        raise ProvisioningRefusal(f"production provider lookup failed (rc={result.returncode}); output suppressed")
    try:
        return json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ProvisioningRefusal("production provider lookup returned malformed JSON; output suppressed") from exc


def _rows(value: Any, key: str) -> list[Mapping[str, Any]]:
    rows = value if isinstance(value, list) else value.get(key) if isinstance(value, dict) else None
    if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
        raise ProvisioningRefusal("production provider response had an invalid rows shape")
    return rows


def resolve_production_scope(owner: ParsedDsn, *, run: Run = subprocess.run,
                             environ: Mapping[str, str]) -> ProductionScope:
    projects = _rows(_provider_json([NEONCTL, "projects", "list", "--org-id", NEON_ORG, "--output", "json"], run=run, environ=environ), "projects")
    branches = _rows(_provider_json([NEONCTL, "branches", "list", "--project-id", PRODUCTION_PROJECT_ID, "--output", "json"], run=run, environ=environ), "branches")
    matching = [row for row in branches if str(row.get("project_id") or "") == PRODUCTION_PROJECT_ID
                and row.get("name") == PRODUCTION_BRANCH_NAME and row.get("default") is True]
    if len(matching) != 1 or not str(matching[0].get("id") or ""):
        raise ProvisioningRefusal("pinned production branch could not be resolved")
    branch_id = str(matching[0]["id"])
    endpoints = _rows(_provider_json([NEONCTL, "api", f"/projects/{PRODUCTION_PROJECT_ID}/branches/{branch_id}/endpoints", "--output", "json"], run=run, environ=environ), "endpoints")
    return production_scope(projects, branches, endpoints, owner.host, owner.port, owner.database)


def _secure_env_file(path: pathlib.Path, label: str) -> None:
    _secure_regular(path, label, 0o600)


def load_existing_identities(path: pathlib.Path, owner: ParsedDsn, trust_root: str | pathlib.Path) -> dict[str, str]:
    _secure_env_file(path, "existing identity profile")
    found: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ProvisioningRefusal("existing identity profile is unreadable") from exc
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if key not in EXISTING_IDENTITIES:
            continue
        if not separator or key in found:
            raise ProvisioningRefusal("existing identity profile has duplicate or executable content")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not value or "$(" in value or "`" in value:
            raise ProvisioningRefusal("existing identity profile has executable content")
        found[key] = transform_dsn(value, EXISTING_IDENTITIES[key][0], owner, trust_root)
    if set(found) != set(EXISTING_IDENTITIES):
        raise ProvisioningRefusal("existing identity profile must supply exactly Joe authority and jobs credentials")
    return found


def _connect(dsn: str) -> Any:
    try:
        import psycopg
    except ImportError as exc:
        raise ProvisioningRefusal("psycopg is required for production provisioning") from exc
    return psycopg.connect(dsn)


def verify_bundles(cur: Any) -> None:
    bundles = tuple(profile.bundle for profile in NEW_PROFILES)
    cur.execute("select rolname,rolcanlogin from pg_roles where rolname = any(%s) order by rolname", (list(bundles),))
    rows = {(str(name), bool(can_login)) for name, can_login in cur.fetchall()}
    if rows != {(bundle, False) for bundle in bundles}:
        raise ProvisioningRefusal("calendar prebrief capability bundles must exist and be exact NOLOGIN roles")


def _role_state(cur: Any, role: str) -> tuple[bool, bool, bool, bool, bool, bool, bool] | None:
    cur.execute("select rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolinherit from pg_roles where rolname=%s", (role,))
    row = cur.fetchone()
    return (bool(row[0]), bool(row[1]), bool(row[2]), bool(row[3]), bool(row[4]),
            bool(row[5]), bool(row[6])) if row is not None else None


def _memberships(cur: Any, role: str) -> tuple[str, ...]:
    cur.execute("select granted.rolname from pg_auth_members m join pg_roles granted on granted.oid=m.roleid join pg_roles member on member.oid=m.member where member.rolname=%s order by granted.rolname", (role,))
    return tuple(str(row[0]) for row in cur.fetchall())


def ensure_login_role(cur: Any, profile: RoleProfile, password: str) -> None:
    try:
        from psycopg import sql
    except ImportError as exc:
        raise ProvisioningRefusal("psycopg is required for production provisioning") from exc
    state = _role_state(cur, profile.role)
    expected = (True, False, False, False, False, False, True)
    if state is None:
        # PostgreSQL's CREATE/ALTER ROLE password grammar does not accept a bind
        # placeholder. Compose the fixed identifier and escaped literal without
        # ever logging or returning the resulting statement.
        cur.execute(sql.SQL(
            "create role {} login nosuperuser nocreatedb nocreaterole "
            "noreplication nobypassrls inherit password {}"
        ).format(sql.Identifier(profile.role), sql.Literal(password)))
        cur.execute(f"grant {profile.bundle} to {profile.role}")
    elif state != expected or _memberships(cur, profile.role) != (profile.bundle,):
        raise ProvisioningRefusal("existing calendar prebrief login does not have the exact least-privilege shape")
    else:
        cur.execute(sql.SQL("alter role {} password {}").format(
            sql.Identifier(profile.role), sql.Literal(password)))
    if _role_state(cur, profile.role) != expected or _memberships(cur, profile.role) != (profile.bundle,):
        raise ProvisioningRefusal("calendar prebrief login role did not converge to its exact capability bundle")


def provision_roles(owner_dsn: str, *, connect: Callable[[str], Any] = _connect,
                    password_factory: Callable[[], str] = lambda: secrets.token_urlsafe(48),
                    prepared_passwords: Mapping[str, str] | None = None) -> dict[str, str]:
    passwords = (dict(prepared_passwords) if prepared_passwords is not None else
                 {profile.role: password_factory() for profile in NEW_PROFILES})
    if set(passwords) != {profile.role for profile in NEW_PROFILES}:
        raise ProvisioningRefusal("prepared password set does not match the Joe login set")
    if any(not value or "\n" in value or "\r" in value for value in passwords.values()):
        raise ProvisioningRefusal("password generator returned an unsafe value")
    try:
        with connect(owner_dsn) as conn, conn.cursor() as cur:
            cur.execute("select session_user,current_user")
            if tuple(cur.fetchone() or ()) != (OWNER_ROLE, OWNER_ROLE):
                raise ProvisioningRefusal("production owner database identity mismatch")
            # Password rotation and profile publication are one narrow control
            # plane operation.  Serialize concurrent local invocations so a
            # later writer cannot publish credentials it has just superseded.
            cur.execute("select pg_advisory_xact_lock(7301961134306029)")
            verify_bundles(cur)
            for profile in NEW_PROFILES:
                ensure_login_role(cur, profile, passwords[profile.role])
            conn.commit()
    except ProvisioningRefusal:
        raise
    except Exception as exc:
        raise ProvisioningRefusal("production role provisioning failed without exposing database output") from exc
    return passwords


def verify_login(dsn: str, role: str, bundle: str, *, connect: Callable[[str], Any] = _connect) -> None:
    try:
        with connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("begin transaction read only")
            cur.execute("select session_user,current_user")
            if tuple(cur.fetchone() or ()) != (role, role):
                raise ProvisioningRefusal("new login identity mismatch")
            cur.execute("select pg_has_role(current_user,%s,'member')", (bundle,))
            if tuple(cur.fetchone() or ()) != (True,):
                raise ProvisioningRefusal("new login capability membership mismatch")
            cur.execute("select rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolinherit from pg_roles where rolname=current_user")
            if tuple(bool(value) for value in (cur.fetchone() or ())) != (True, False, False, False, False, False, True):
                raise ProvisioningRefusal("new login role attributes mismatch")
    except ProvisioningRefusal:
        raise
    except Exception as exc:
        raise ProvisioningRefusal("new login verification failed without exposing database output") from exc


def compose_identity_dsns(owner: ParsedDsn, trust_root: str | pathlib.Path,
                          existing: Mapping[str, str], passwords: Mapping[str, str]) -> dict[str, str]:
    root = _trust_root(trust_root)
    values = dict(existing)
    for profile in NEW_PROFILES:
        password = passwords.get(profile.role, "")
        if not password:
            raise ProvisioningRefusal("new login password is unavailable")
        values[profile.env_key] = build_dsn(profile.role, password, owner, root)
    expected = set(EXISTING_IDENTITIES) | {profile.env_key for profile in NEW_PROFILES}
    if set(values) != expected:
        raise ProvisioningRefusal("Joe identity DSN set is incomplete")
    return values


def _secure_parent(path: pathlib.Path) -> None:
    parent = path.parent
    if not parent.exists():
        parent.mkdir(mode=0o700, parents=True)
    try:
        info = parent.lstat()
    except OSError as exc:
        raise ProvisioningRefusal("profile directory is unavailable") from exc
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
        raise ProvisioningRefusal("profile directory must be a private non-symlink directory")


def _atomic_secret_file(path: pathlib.Path, content: str) -> None:
    _secure_parent(path)
    if path.exists():
        _secure_regular(path, "existing profile", 0o600)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _profile_text(values: Mapping[str, str]) -> str:
    if any("\n" in value or "\r" in value for value in values.values()):
        raise ProvisioningRefusal("profile value contains a line break")
    return "".join(f"{key}={values[key]}\n" for key in sorted(values))


def write_profiles(paths: ProfilePaths, values: Mapping[str, str], fingerprint: str,
                   *, home: pathlib.Path | None = None) -> None:
    if len(fingerprint) != 64 or any(char not in "0123456789abcdef" for char in fingerprint):
        raise ProvisioningRefusal("collector public-key fingerprint is malformed")
    activation_keys = set(EXISTING_IDENTITIES) | {profile.env_key for profile in NEW_PROFILES}
    if set(values) != activation_keys:
        raise ProvisioningRefusal("activation profile would mix an unsupported identity")
    child_keys = {profile.env_key for profile in NEW_PROFILES}
    root = home or pathlib.Path.home()
    for path in (paths.activation, paths.child, paths.runtime, paths.private_key, paths.public_key, paths.allowlist):
        if not path.is_absolute():
            raise ProvisioningRefusal("all provisioned profile paths must be absolute")
    runtime = {
        "CARR_CALENDAR_PREBRIEF_ENABLED": "false",
        "CARR_DB_JOBS_URL": values["CARR_DB_JOBS_URL"],
        "CARR_CALENDAR_PREBRIEF_CHILD_PROFILE": str(paths.child),
        "CARR_CALENDAR_PREBRIEF_COLLECTOR_PUBLIC_KEY": str(paths.public_key),
        "CARR_CALENDAR_PREBRIEF_ALLOWLIST": str(paths.allowlist),
        "CARR_CALENDAR_PREBRIEF_COLLECTOR_PRIVATE_KEY": str(paths.private_key),
        "CARR_CALENDAR_PREBRIEF_COLLECTOR_VERSION": DEFAULT_VERSION,
        "CARR_CALENDAR_PREBRIEF_EVENTKIT_APP": str(root / "Applications" / "CARR Calendar Access.app"),
    }
    _atomic_secret_file(paths.activation, _profile_text({key: values[key] for key in activation_keys}))
    _atomic_secret_file(paths.child, _profile_text({key: values[key] for key in child_keys}))
    _atomic_secret_file(paths.runtime, _profile_text(runtime))


def _run_key_command(args: Sequence[str], *, run: Run) -> subprocess.CompletedProcess[str]:
    try:
        result = run(list(args), capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProvisioningRefusal("collector key command did not complete; output suppressed") from exc
    if result.returncode:
        raise ProvisioningRefusal(f"collector key command failed (rc={result.returncode}); output suppressed")
    return result


def ensure_keypair(paths: ProfilePaths, *, run: Run = subprocess.run) -> str:
    private_exists, public_exists = paths.private_key.exists(), paths.public_key.exists()
    if private_exists != public_exists:
        raise ProvisioningRefusal("collector keypair is incomplete")
    if private_exists:
        _secure_regular(paths.private_key, "collector private key", 0o600)
        _secure_regular(paths.public_key, "collector public key")
        derived = _run_key_command([openssl_executable(), "pkey", "-in", str(paths.private_key), "-pubout"], run=run).stdout.encode()
        actual = paths.public_key.read_bytes()
        if not derived or derived != actual:
            raise ProvisioningRefusal("collector public key does not exactly match its private key")
    else:
        _secure_parent(paths.private_key)
        _secure_parent(paths.public_key)
        with tempfile.TemporaryDirectory(prefix="carr-calendar-key-", dir=paths.private_key.parent) as raw:
            private, public = pathlib.Path(raw) / "private.pem", pathlib.Path(raw) / "public.pem"
            _run_key_command([openssl_executable(), "genpkey", "-algorithm", "ED25519", "-out", str(private)], run=run)
            private.chmod(0o600)
            _run_key_command([openssl_executable(), "pkey", "-in", str(private), "-pubout", "-out", str(public)], run=run)
            _secure_regular(private, "generated collector private key", 0o600)
            _secure_regular(public, "generated collector public key")
            _atomic_secret_file(paths.private_key, private.read_text(encoding="utf-8"))
            _atomic_secret_file(paths.public_key, public.read_text(encoding="utf-8"))
        actual = paths.public_key.read_bytes()
    return hashlib.sha256(actual).hexdigest()


def default_paths(home: pathlib.Path) -> ProfilePaths:
    config = home / ".config" / "carr"
    return ProfilePaths(config / "calendar-prebrief-joe-activation.env", config / "calendar-prebrief-joe-child.env",
                        config / "calendar-prebrief-joe.env", config / "calendar-prebrief-joe-private.pem",
                        config / "calendar-prebrief-joe-public.pem", config / "calendar-prebrief-joe-allowlist.json")


def reject_ambient_database_inputs(environ: Mapping[str, str]) -> None:
    forbidden = sorted(key for key, value in environ.items()
                       if value and (key.startswith("PG") or (key.startswith("CARR_DB_") and key != OWNER_ENV) or key == "DATABASE_URL"))
    if forbidden:
        raise ProvisioningRefusal("only the production owner credential may be supplied through environment")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--existing-identities", type=pathlib.Path,
                        default=pathlib.Path.home() / ".config" / "carr" / "db.env")
    parser.add_argument("--trust-root", type=pathlib.Path,
                        default=pathlib.Path.home() / ".config" / "carr" / "neon-production-root.pem")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        reject_ambient_database_inputs(os.environ)
        owner_text = os.environ.get(OWNER_ENV, "")
        if not owner_text:
            raise ProvisioningRefusal("production owner credential is unavailable")
        root = _trust_root(args.trust_root)
        owner = parse_owner_dsn(owner_text, root)
        resolve_production_scope(owner, environ=os.environ)
        existing = load_existing_identities(args.existing_identities, owner, root)
        if not args.apply:
            print(json.dumps({"sponsor": "joe", "mode": "live", "ready_to_apply": True}, sort_keys=True))
            return 0
        # Prepare every local artifact while the runtime is still explicitly
        # disabled. Only after those writes succeed do we rotate/create the DB
        # logins in one transaction. A local disk/key failure therefore cannot
        # strand freshly rotated production credentials.
        passwords = {profile.role: secrets.token_urlsafe(48) for profile in NEW_PROFILES}
        values = compose_identity_dsns(owner, root, existing, passwords)
        paths = default_paths(pathlib.Path.home())
        fingerprint = ensure_keypair(paths)
        write_profiles(paths, values, fingerprint)
        provision_roles(owner_text, prepared_passwords=passwords)
        for key, (role, bundle) in EXISTING_IDENTITIES.items():
            verify_login(values[key], role, bundle)
        for profile in NEW_PROFILES:
            verify_login(values[profile.env_key], profile.role, profile.bundle)
        print(json.dumps({"sponsor": "joe", "mode": "live", "provisioned": True,
                          "collector_key_fingerprint": fingerprint}, sort_keys=True))
        return 0
    except ProvisioningRefusal as exc:
        print(f"calendar prebrief Joe provisioner: REFUSE {exc}", file=sys.stderr)
        return 78


if __name__ == "__main__":
    raise SystemExit(main())
