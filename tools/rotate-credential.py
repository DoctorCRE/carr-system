#!/usr/bin/env python3
"""
rotate-credential.py — rotate a CARR credential without any value ever being
displayed, echoed, logged, or placed on a command line.

RENAMED FROM tools/set-jobs-password.py on 2026-08-14 (no callers anywhere; the
old name described one role and the job is now three credentials, and a name
that does not match its behaviour is the thing rule 3578d799 forbids). The
carr_jobs path below is the original tool's logic, kept intact — the interactive
prompt still works exactly as it did.

WHY IT EXISTS IN THIS FORM. On 2026-08-14 a session diagnosing a silent failure
in bin/migrate-prod.sh ran it under `zsh -x`. The trace expanded the line that
sources ~/.config/carr/db.env and printed all three secrets in full: the local
exporter DSN, the nightly jobs DSN, and NEON_API_KEY — the last of which is not
scoped to one database and does not expire. Rotation was the remedy, and doing
it by hand would have meant a password on a command line, which is the same
class of mistake one step further along.

SUPPORTED ROTATIONS (AND ONE DISABLED BACKUP PATH)

  --role carr_jobs             ALTER ROLE, then rewrite CARR_DB_JOBS_URL
  --role app_exporter_local    ALTER ROLE, then rewrite CARR_DB_EXPORTER_URL
  --role carr_program5_forward_fix_verifier
                              ALTER ROLE, then rewrite CARR_DB_PROGRAM5_FORWARD_FIX_VERIFIER_URL
  --role carr_backup           DISABLED: refuses before local or provider work
                               until a canonical server-validated receipt exists
  --neon-api-key               mint a new Neon API key, then revoke the old one

  --generate makes it non-interactive: a 40-character password from the
  URL-safe alphabet, generated in-process, never shown. Without it the tool
  prompts twice with hidden input, which is the original behaviour. A mint is
  --generate only: a credential no human ever types should not be one a human
  chooses.

  --github-secret is reserved for a future receipt-bound carr_backup path. It
  is never invoked by the disabled public backup route. A future implementation
  must keep its value on stdin, never a command line or shell history.

  carr_backup rotation is intentionally disabled until a server-validated,
  immutable receipt binds Joe's approval and metering admission to this exact
  target and credential material.  A caller-supplied string is not authority.

THE PASSWORD IS SWAPPED INTO THE EXISTING URL, NOT REBUILT FROM THE OWNER URL.
The original tool derived the jobs URL from the owner DSN by substituting the
userinfo, which silently inherits the owner's host and query string. The jobs
entry carries `&channel_binding=require` and the exporter entry does not, so
rebuilding from the owner would have quietly changed connection semantics for
one of them. Only the password is replaced; everything else in the line is left
byte-identical.

ORDER OF OPERATIONS FOR THE API KEY, and it matters: mint, verify the new key
works, write it to db.env, and only then revoke the old one. Revoking first
would take out every unattended path at once — bin/migrate-prod.sh,
bin/import-doctrine.sh, bin/restore-rehearse.sh and the partner ping all derive
their connection through neonctl — with no way back.

  DATABASE_URL=<owner> .venv/bin/python tools/rotate-credential.py --role carr_jobs --generate
  .venv/bin/python tools/rotate-credential.py --neon-api-key

The owner DSN comes from tools/db-tap.py's break-glass run mode, so it is never
typed. Nothing here prints a secret; the only place any value exists afterward
is db.env, mode 600.
"""

import argparse
from contextlib import contextmanager
import fcntl
import getpass
import json
import os
import re
import secrets
import stat
import string
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from typing import Callable, NoReturn
from urllib.parse import quote, unquote, urlsplit, urlunsplit

ENV_PATH = os.path.expanduser("~/.config/carr/db.env")
NEON_API = "https://console.neon.tech/api/v2"
# The same organization tools/db-tap.py pins, for the same reason: a lookup that
# can drift is not an identity.
NEON_ORG = "org-dry-dew-75906281"

# The env var each role's connection string lives in.
ROLE_ENV = {
    "carr_jobs": "CARR_DB_JOBS_URL",
    "app_exporter_local": "CARR_DB_EXPORTER_URL",
    "carr_backup": "CARR_DB_BACKUP_URL",
    "carr_program5_forward_fix_verifier": "CARR_DB_PROGRAM5_FORWARD_FIX_VERIFIER_URL",
}

# Both generic rotation entrypoints share this allowlist. Backup stays disabled.
ROTATABLE_ROLES = frozenset({
    "carr_jobs",
    "app_exporter_local",
    "carr_program5_forward_fix_verifier",
})

# No role may mint a connection while the backup provider mutation is disabled.
#
# The pure URI/pending helpers below remain tested so future receipt-bound
# enablement has a narrow foundation, but absent db.env keys always refuse now.
#
# The control-plane declaration says external_human_approval.  There is not yet
# a canonical, server-validated receipt that binds that approval and metering
# admission to this exact target/material, so the public backup entrypoints
# fail closed rather than pretending a caller-provided string is authority.
MINTABLE: set[str] = set()

# The database is the same one every routine credential already points at, so
# the host and database name are COPIED from a routine DSN that is known to
# work rather than typed or derived from the owner. Deriving from the owner is
# what the header of this file warns against; deriving from a peer is not the
# same act, because a peer has already proven the host reachable and carries no
# privilege the new line could inherit.
#
# The query string is SET, never inherited. CARR_DB_JOBS_URL carries
# &channel_binding=require and CARR_DB_EXPORTER_URL does not; copying whichever
# happened to be present would make the backup credential's connection
# semantics depend on which peer was read first. sslmode=require is the shape
# .github/workflows/backup-nightly.yml documents and the shape the cloud backup
# has been running on since 2026-08-14.
MINT_SOURCE_KEYS = ("CARR_DB_EXPORTER_URL", "CARR_DB_JOBS_URL")
MINT_QUERY = "sslmode=require"

# The GitHub repository secret the cloud nightly reads. Same script, same role,
# same database — see .github/workflows/backup-nightly.yml.
GITHUB_SECRET = "BACKUP_DATABASE_URL"
GITHUB_REPOSITORY = "jbookout/carr-system"
GITHUB_HOST = "github.com"
GITHUB_TIMEOUT_SECONDS = 30

# No spaces, quotes, @ / : ? # — every one of them changes how a URL parses.
ALPHABET = string.ascii_letters + string.digits


def _fsync_directory(path: str) -> None:
    """Make a preceding rename or unlink durable, not merely atomic."""
    fd = os.open(path or ".", os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _durable_replace(path: str, content: str, *, prefix: str,
                     verify: Callable[[str], None] | None = None) -> None:
    """Write a 0600 file, fsync it, atomically replace it, then fsync its dir.

    ``verify`` is handed the finished TEMPORARY file, before the swap. Raising
    from it aborts the write: the temporary is unlinked by the handler below and
    ``path`` is never touched. Checking the candidate rather than the installed
    file is the whole point — a credential file that fails its contract must
    never become the live one, not even for the instant it takes to notice.
    """
    directory = os.path.dirname(path) or "."
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=prefix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        if verify is not None:
            verify(temporary)
        os.replace(temporary, path)
        _fsync_directory(directory)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def _private_pending_path() -> str:
    return ENV_PATH + ".carr_backup.pending"


@contextmanager
def credential_env_lock():
    """Serialize every db.env writer in this tool through one hardened lock."""
    path = ENV_PATH + ".rotate-credential.lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        sys.exit("rotate-credential: credential lock cannot be opened safely")
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1
                or stat.S_IMODE(st.st_mode) != (stat.S_IRUSR | stat.S_IWUSR)):
            sys.exit("rotate-credential: credential lock is not a private regular 0600 file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit("rotate-credential: another credential rotation is in progress")
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _postgres_parts(url: str, label: str):
    """Parse only the narrow URI form this tool can safely republish."""
    if not isinstance(url, str) or not url or any(ch.isspace() for ch in url):
        sys.exit(f"rotate-credential: {label} is not a strict PostgreSQL URI")
    try:
        parts = urlsplit(url)
        # urlsplit accepts a second raw @ as part of userinfo.  PostgreSQL URI
        # passwords must percent-encode it, so accepting it would make the
        # endpoint parsed by this tool differ from the endpoint libpq receives.
        if "@" in parts.netloc.rsplit("@", 1)[0]:
            raise ValueError("multiple at signs")
        port = parts.port  # force urlsplit to reject a non-numeric port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("invalid port")
    except ValueError:
        sys.exit(f"rotate-credential: {label} is not a strict PostgreSQL URI")
    # A libpq query parameter can replace the URI's authority, database,
    # identity, service profile, or connection behavior.  The backup contract
    # accepts precisely the one TLS form used by the workflow, with its optional
    # channel-binding hardening; no duplicate or encoded spelling is accepted.
    if parts.query not in {"sslmode=require", "sslmode=require&channel_binding=require"}:
        sys.exit(f"rotate-credential: {label} has an ambiguous libpq query override")
    database = unquote(parts.path[1:]) if parts.path.startswith("/") else ""
    if (parts.scheme not in {"postgres", "postgresql"} or parts.fragment
            or not parts.hostname or not parts.username or parts.password is None
            or not database or "/" in database):
        sys.exit(f"rotate-credential: {label} is not a strict PostgreSQL URI")
    return parts, (parts.hostname.lower(), 5432 if port is None else port, database)


def _url_for_role(parts, role: str, password: str, query: str) -> str:
    host = parts.hostname
    assert host is not None  # _postgres_parts already refused an absent host.
    rendered_host = f"[{host}]" if ":" in host else host
    try:
        port = parts.port
    except ValueError:
        sys.exit("rotate-credential: strict PostgreSQL URI port check failed")
    if port is not None:
        rendered_host += f":{port}"
    return urlunsplit(("postgresql", f"{quote(role, safe='')}:{quote(password, safe='')}@{rendered_host}",
                       parts.path, query, ""))


def _read_pending_backup_url(owner_target: tuple[str, int, str]) -> str | None:
    path = _private_pending_path()
    _clean_prepublication_temps(path)
    try:
        st = os.lstat(path)
        if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1
                or stat.S_IMODE(st.st_mode) != (stat.S_IRUSR | stat.S_IWUSR)):
            sys.exit("rotate-credential: pending backup state is not mode 0600")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, encoding="utf-8") as handle:
            value = handle.read()
    except FileNotFoundError:
        return None
    except OSError:
        sys.exit("rotate-credential: cannot read pending backup state")
    if not value.endswith("\n") or value.count("\n") != 1:
        sys.exit("rotate-credential: pending backup state has an invalid shape")
    url = value[:-1]
    parts, target = _postgres_parts(url, "pending backup state")
    if parts.username != "carr_backup" or target != owner_target:
        sys.exit("rotate-credential: pending backup state does not match this owner target")
    return url


def _prepublication_prefix(path: str) -> str:
    return os.path.basename(path) + ".prepublish."


def _clean_prepublication_temps(path: str) -> None:
    """Remove only stale, private temp files created before canonical publish."""
    directory = os.path.dirname(path) or "."
    prefix = _prepublication_prefix(path)
    removed = False
    try:
        canonical = os.lstat(path)
    except FileNotFoundError:
        canonical = None
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return
    for entry in entries:
        if not entry.name.startswith(prefix):
            continue
        try:
            st = os.lstat(entry.path)
        except FileNotFoundError:
            continue
        orphan = (stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and st.st_uid == os.getuid()
                  and stat.S_IMODE(st.st_mode) == (stat.S_IRUSR | stat.S_IWUSR))
        published_twin = (canonical is not None and stat.S_ISREG(st.st_mode)
                          and st.st_nlink == 2 and st.st_uid == os.getuid()
                          and stat.S_IMODE(st.st_mode) == (stat.S_IRUSR | stat.S_IWUSR)
                          and (st.st_dev, st.st_ino) == (canonical.st_dev, canonical.st_ino))
        if orphan or published_twin:
            try:
                os.unlink(entry.path)
                removed = True
            except FileNotFoundError:
                pass
    if removed:
        _fsync_directory(directory)


def _write_pending_backup_url(url: str) -> None:
    """Durably publish one resume value without ever overwriting canonical state."""
    path = _private_pending_path()
    directory = os.path.dirname(path) or "."
    _clean_prepublication_temps(path)
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=_prepublication_prefix(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(url + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        # link(2) is an atomic no-overwrite publication: FileExistsError means
        # a different canonical state won, and the caller must resume that one.
        os.link(temporary, path)
        _fsync_directory(directory)
        os.unlink(temporary)
        _fsync_directory(directory)
    except FileExistsError:
        sys.exit("rotate-credential: pending backup state already exists; rerun to resume it")
    except BaseException:
        raise
    finally:
        # Before publication it has one link and is safe to remove; after a
        # crash immediately after link(2), it has two links and is deliberately
        # left for the next locked run to inspect rather than guessed away.
        try:
            st = os.lstat(temporary)
            if (stat.S_ISREG(st.st_mode) and st.st_nlink == 1
                    and stat.S_IMODE(st.st_mode) == (stat.S_IRUSR | stat.S_IWUSR)):
                os.unlink(temporary)
                _fsync_directory(directory)
        except FileNotFoundError:
            pass


def _clear_pending_backup_url() -> None:
    path = _private_pending_path()
    try:
        os.unlink(path)
        _fsync_directory(os.path.dirname(path) or ".")
    except FileNotFoundError:
        sys.exit("rotate-credential: pending backup state disappeared; refusing to guess")


def _backup_mutation_unavailable() -> NoReturn:
    """Fail closed: no canonical receipt seam exists for this provider mutation."""
    sys.exit("rotate-credential: carr_backup rotation is disabled: no server-validated immutable "
             "approval-and-metering receipt binds this exact target and credential material")


def read_env() -> dict[str, str]:
    """Values from db.env. Never logged, never returned to a caller that prints."""
    out: dict[str, str] = {}
    try:
        for line in open(ENV_PATH, encoding="utf-8").read().splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            out[k.strip()] = v.strip().strip("\"'")
    except OSError as e:
        sys.exit(f"rotate-credential: cannot read {ENV_PATH}: {e}")
    return out


def shell_quote(value: str) -> str:
    """Single-quote a value so `set -a; . db.env` survives it.

    THIS FILE HAS TWO PARSERS AND THEREFORE TWO CONTRACTS — the rule that a config
    file read by two parsers has two contracts, and fixing one breaks the other
    unless you check (73381d78). Python
    readers split on '=' and strip quotes, so they do not care. zsh SOURCES this
    file — bin/migrate-prod.sh, bin/nightly.sh and every other shell job do
    `set -a; . db.env` — and an unquoted '&' in a DSN is a background operator,
    not a character.

    The first version of this tool wrote the value bare. Every Python check
    passed, both roles connected, and bin/migrate-prod.sh died with
    "db.env:3: parse error near `&`" — the jobs DSN carries
    &channel_binding=require. Caught only because a completion gate demanded a
    fresh run of the shell path rather than a restatement of the Python one.

    Single quotes because a postgres URL cannot contain one; the escape below
    handles it anyway rather than trusting that."""
    return "'" + value.replace("'", "'\\''") + "'"


ZSH = "/bin/zsh"
SHELL_PROBE_TIMEOUT_SECONDS = 10
_ZSH_DIAGNOSTIC_LINE = re.compile(r":(\d+):")


def shell_parse_failure(path: str) -> str | None:
    """Line number in `path` that `set -a; . path` dies on, or None if it parses.

    THE SECOND PARSER, ASKED DIRECTLY. shell_quote() above states the contract;
    this states whether the file we are about to install actually meets it, which
    is not the same claim. `zsh -n` parses and runs NOTHING: it never connects,
    never expands, never echoes a value, and costs a few milliseconds.

    Why it exists at all, when the only writer here already quotes: on 2026-08-20
    CARR_DB_PROGRAM5_FORWARD_FIX_VERIFIER_URL reached db.env unquoted from outside
    this tool. Its `&channel_binding=require` is a background operator to zsh, so
    the source line died on a parse error and took ALL FIVE keys in the file with
    it. bin/migrate-prod.sh — the one sanctioned door to production migrations —
    was closed from then until 2026-08-26 for every caller that did not already
    have NEON_API_KEY exported, and the error text named neither the file's role
    nor the script, so it read as a broken script. A guard that only trusts this
    tool's own quoting would not have caught it; a guard that reads the finished
    file does.

    Only the LINE NUMBER crosses back. zsh's message can quote a token from the
    offending line, and every value here is a credential.

    None on a missing or unusable shell: a rotation must not fail because the
    probe could not run. The pre-swap caller treats that as "unproven, proceed",
    which is exactly where this tool stood before the probe existed.
    """
    try:
        probe = subprocess.run([ZSH, "-n", path], capture_output=True, text=True,
                               timeout=SHELL_PROBE_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if probe.returncode == 0:
        return None
    found = _ZSH_DIAGNOSTIC_LINE.search(probe.stderr)
    return found.group(1) if found else "unknown"


def _require_shell_sourceable(candidate: str) -> None:
    """Refuse to install a db.env that zsh cannot source. Never prints a value."""
    line = shell_parse_failure(candidate)
    if line is None:
        return
    sys.exit(
        f"rotate-credential: refusing to install {ENV_PATH} — the new file does not "
        f"parse as shell at line {line}, so `set -a; . db.env` would discard EVERY "
        f"key in it and every routine that sources it would report its credential "
        f"missing. Nothing was changed. A value carrying '&', ';', '(' or a space "
        f"must be single-quoted; see shell_quote() in this file.")


def write_env_key(key: str, value: str) -> None:
    """Replace exactly one key in db.env, atomically, preserving every other line
    including comments and blank lines. The original tool rebuilt the file from a
    filtered list, which is fine until a write is interrupted — os.replace makes
    the swap atomic so a crash can never leave a half-written credentials file."""
    value = shell_quote(value)
    try:
        original = open(ENV_PATH, encoding="utf-8").read().splitlines()
    except OSError:
        original = []

    replaced = False
    lines: list[str] = []
    for line in original:
        if line.startswith(f"{key}="):
            lines.append(f"{key}={value}")
            replaced = True
        else:
            lines.append(line)
    if not replaced:
        lines.append(f"{key}={value}")

    _durable_replace(ENV_PATH, "\n".join(lines) + "\n", prefix=".db.env.",
                     verify=_require_shell_sourceable)


def swap_password(url: str, password: str) -> str:
    """Replace ONLY the password inside a postgres URL. Host, user, database and
    every query parameter survive byte-identical."""
    m = re.match(r"^(?P<pre>\w+://)(?P<user>[^:/@]+):(?P<pw>[^@]*)@(?P<rest>.+)$", url)
    if not m:
        sys.exit("rotate-credential: the existing URL is not in scheme://user:pass@host form — "
                 "refusing to guess at its shape")
    return f"{m.group('pre')}{m.group('user')}:{password}@{m.group('rest')}"


def new_password() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(40))


def mint_url(role: str, env: dict[str, str], password: str,
             owner_target: tuple[str, int, str]) -> str:
    """Build a first connection string for `role` from a peer routine DSN.

    Host and database are copied from a peer that already connects; user is the
    new role; the query string is SET to MINT_QUERY rather than inherited. See
    the MINTABLE block above for why this exists and why it is one role only.

    Returns a URL. Never logs it — the caller verifies it, writes it to a 0600
    file, and prints only that it did so."""
    for key in MINT_SOURCE_KEYS:
        peer = env.get(key)
        if not peer:
            continue
        parts, peer_target = _postgres_parts(peer, f"{key} value")
        if peer_target != owner_target:
            sys.exit("rotate-credential: routine peer target does not match the owner target")
        return _url_for_role(parts, role, password, MINT_QUERY)
    sys.exit(f"rotate-credential: cannot mint {ROLE_ENV[role]} — none of "
             f"{', '.join(MINT_SOURCE_KEYS)} is present in {ENV_PATH} in "
             f"strict PostgreSQL URI form.")


def set_github_secret(url: str) -> None:
    """Push the same value to the repo secret the cloud nightly reads.

    BOTH ENDS OR NEITHER. bin/backup-dump.sh serves two callers (rule a8c55a47):
    this Mac through db.env, and .github/workflows/backup-nightly.yml through
    this secret. Rotating one and not the other does not degrade the backup — it
    breaks whichever end was left behind, silently, until a night nobody is
    watching. On 2026-08-20 the cloud end was the ONLY working backup path, so
    the end that would have broken was the one actually protecting the data.

    The value goes in on stdin, never on a command line, so it stays out of the
    process table and out of shell history."""
    ambient_host = os.environ.get("GH_HOST")
    if ambient_host not in (None, "", GITHUB_HOST):
        sys.exit("rotate-credential: refusing a non-github.com GH_HOST for the pinned repository secret")
    safe_env = dict(os.environ)
    safe_env["GH_HOST"] = GITHUB_HOST
    try:
        proc = subprocess.run(["gh", "secret", "set", GITHUB_SECRET,
                               "--repo", GITHUB_REPOSITORY],
                              input=url, text=True, capture_output=True,
                              timeout=GITHUB_TIMEOUT_SECONDS, env=safe_env)
    except subprocess.TimeoutExpired:
        sys.exit("rotate-credential: GitHub secret update timed out; pending state retained "
                 "for a same-value resume")
    except OSError:
        sys.exit("rotate-credential: GitHub secret update could not be started; pending state retained")
    if proc.returncode != 0:
        # Do not render gh stderr: a future gh version or proxy must never turn
        # a secret-bearing request into this tool's stderr.  The O_EXCL pending
        # file remains, so retry always publishes exactly the same value.
        sys.exit("rotate-credential: GitHub secret update was not confirmed; pending state retained "
                 "for a same-value resume")
    print(f"  github secret {GITHUB_SECRET}: set for {GITHUB_REPOSITORY} (value passed on stdin)")


def verify_backup_connection(conn) -> None:
    """The credential path itself must have no SET ROLE or proxy identity."""
    row = conn.execute("select session_user, current_user").fetchone()
    if row != ("carr_backup", "carr_backup"):
        sys.exit("rotate-credential: carr_backup session identity verification failed; pending state retained")


def verify_backup_least_privilege(owner_conn) -> None:
    """Use owner-visible catalogs, never a restricted role's partial metadata view."""
    row = owner_conn.execute("""
        with recursive backup as (
          select oid from pg_roles where rolname = 'carr_backup'
        ), reachable(roleid) as (
          select m.roleid from pg_auth_members m join backup b on b.oid = m.member
          union
          select m.roleid from pg_auth_members m join reachable r on r.roleid = m.member
        ), objects as (
          select c.oid, c.relkind, c.relnamespace, c.relowner, c.relacl,
                 c.relrowsecurity, c.relforcerowsecurity
           from pg_class c join pg_namespace n on n.oid = c.relnamespace
           where n.nspname in ('public', 'ops')
        ), all_objects as (
          select c.oid, c.relkind, c.relnamespace, c.relowner, c.relacl
            from pg_class c
        )
        select
          exists (select 1 from pg_roles r
                  where r.rolname = 'carr_backup' and r.rolcanlogin
                    and not (r.rolsuper or r.rolcreatedb or r.rolcreaterole
                             or r.rolreplication or r.rolbypassrls)),
          not exists (select 1 from reachable),
          not exists (select 1 from objects o join backup b on o.relowner = b.oid)
            and not exists (select 1 from pg_namespace n join backup b on n.nspowner = b.oid)
            and not exists (select 1 from pg_database d join backup b on d.datdba = b.oid)
            and not exists (select 1 from pg_proc p join backup b on p.proowner = b.oid),
          not exists (select 1 from pg_database d cross join backup b
                      cross join lateral aclexplode(coalesce(d.datacl, acldefault('d', d.datdba))) a
                      where a.grantee = b.oid and (a.privilege_type <> 'CONNECT' or a.is_grantable)),
          has_database_privilege('carr_backup', current_database(), 'CONNECT')
            and not has_database_privilege('carr_backup', current_database(), 'CREATE')
            and not has_database_privilege('carr_backup', current_database(), 'TEMPORARY'),
          not exists (select 1 from pg_namespace n cross join backup b
                      cross join lateral aclexplode(coalesce(n.nspacl, acldefault('n', n.nspowner))) a
                      where a.grantee = b.oid
                        and (n.nspname not in ('public', 'ops') or a.privilege_type <> 'USAGE' or a.is_grantable)),
          not exists (select 1 from pg_namespace n
                      where n.nspname in ('public', 'ops')
                        and (not has_schema_privilege('carr_backup', n.oid, 'USAGE')
                             or has_schema_privilege('carr_backup', n.oid, 'CREATE')))
            and not exists (select 1 from pg_namespace n
                            where n.nspname not in ('public', 'ops', 'pg_catalog', 'information_schema')
                              and (has_schema_privilege('carr_backup', n.oid, 'USAGE')
                                   or has_schema_privilege('carr_backup', n.oid, 'CREATE'))),
          not exists (select 1 from all_objects o cross join backup b
                      cross join lateral aclexplode(coalesce(o.relacl,
                        acldefault(case when o.relkind = 'S' then 'S'::"char" else 'r'::"char" end,
                                   o.relowner))) a
                      where a.grantee = b.oid
                        and (o.relnamespace not in (select oid from pg_namespace
                                                     where nspname in ('public', 'ops'))
                             or o.relkind not in ('r', 'p', 'S')
                             or a.privilege_type <> 'SELECT' or a.is_grantable)),
          not exists (select 1 from pg_proc p cross join backup b
                      cross join lateral aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
                      where a.grantee = b.oid),
          not exists (select 1 from objects o
                      where o.relkind in ('r', 'p')
                        and (o.relrowsecurity or o.relforcerowsecurity
                             or not has_table_privilege('carr_backup', o.oid, 'SELECT')
                             or has_table_privilege('carr_backup', o.oid,
                                   'INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER'))),
          not exists (select 1 from objects o
                      where o.relkind = 'S'
                        and (not has_sequence_privilege('carr_backup', o.oid, 'SELECT')
                             or has_sequence_privilege('carr_backup', o.oid, 'USAGE, UPDATE'))),
          not exists (select 1 from pg_proc p
                      where has_function_privilege('carr_backup', p.oid, 'EXECUTE'))
    """).fetchone()
    if not row or not all(row):
        sys.exit("rotate-credential: carr_backup least-privilege contract failed; pending state retained")


def rotate_role(role: str, generate: bool, github_secret: bool = False) -> int:
    # Refuse an accidental backup-secret overwrite before password generation,
    # database imports, db.env reads, or any provider work.
    if github_secret and role != "carr_backup":
        sys.exit("rotate-credential: --github-secret is permitted only with --role carr_backup")

    if role == "carr_backup":
        _backup_mutation_unavailable()

    return _rotate_existing_role(role, generate)


def _rotate_existing_role(role: str, generate: bool) -> int:
    # This private helper is intentionally an allowlist too: callers importing
    # it must not bypass the public carr_backup refusal before any lock, import,
    # environment read, password generation, or database work.
    if role not in ROTATABLE_ROLES:
        sys.exit("rotate-credential: generic rotation is permitted only for "
                 + ", ".join(sorted(ROTATABLE_ROLES)))
    with credential_env_lock():
        return _rotate_existing_role_locked(role, generate)


def _rotate_existing_role_locked(role: str, generate: bool) -> int:
    # Defense in depth for imported/private callers: this is the deepest helper
    # that holds ALTER ROLE, so it carries the same closed non-backup allowlist.
    if role not in ROTATABLE_ROLES:
        sys.exit("rotate-credential: generic rotation is permitted only for "
                 + ", ".join(sorted(ROTATABLE_ROLES)))
    import psycopg
    from psycopg import sql

    owner = os.environ.get("DATABASE_URL")
    if not owner:
        sys.exit("rotate-credential: DATABASE_URL is not set. This needs the OWNER credential — "
                 "run it through tools/db-tap.py's break-glass run mode so the DSN is never typed.")

    env_key = ROLE_ENV[role]
    env = read_env()
    existing = env.get(env_key)
    if not existing:
        sys.exit(f"rotate-credential: {env_key} is not in {ENV_PATH} — nothing to rotate. "
                 "Add the line first; this tool changes a password, it does not mint a connection.")

    # Validate the effective libpq target before generating a password or
    # opening the owner connection. The narrow parser rejects query overrides
    # of the host, login, database, or startup role as well as malformed URIs.
    _, owner_target = _postgres_parts(owner, "owner connection")
    parts, target = _postgres_parts(existing, env_key)
    if unquote(parts.username) != role:
        sys.exit(f"rotate-credential: {env_key} has the wrong login — nothing changed")
    if target != owner_target:
        sys.exit(f"rotate-credential: {env_key} and owner target differ — nothing changed")

    if generate:
        pw = new_password()
    else:
        pw = getpass.getpass(f"New {role} password (hidden): ")
        if pw != getpass.getpass("Again: "):
            sys.exit("passwords did not match — nothing changed")
        if len(pw) < 12:
            sys.exit("use at least 12 characters — nothing changed")
        if any(c in pw for c in " '\"@/:?#"):
            sys.exit("avoid spaces, quotes, and @ / : ? # (they break the URL form) — nothing changed")

    new_url = swap_password(existing, pw)
    with psycopg.connect(owner) as conn:
        conn.execute(sql.SQL("alter role {} with password {}").format(
            sql.Identifier(role), sql.Literal(pw)))
        conn.commit()

    # PROVE IT BEFORE WRITING IT. If the new credential does not connect, the old
    # line stays in db.env and the only damage is a role whose password no longer
    # matches a file — recoverable by re-running. Writing first and verifying
    # after would leave an unusable file if the connection failed.
    with psycopg.connect(new_url) as conn:
        expected: tuple[str, str, bool] | tuple[str]
        if role == "carr_program5_forward_fix_verifier":
            row = conn.execute("select session_user,current_user,pg_has_role(session_user,'carr_program5_forward_fix_verifiers','member')").fetchone()
            expected = (role, role, True)
        else:
            row = conn.execute("select current_user").fetchone()
            expected = (role,)
        if row != expected:
            sys.exit(f"rotate-credential: verification is not the exact scoped identity "
                     f"expected for {role} — db.env NOT written")

    write_env_key(env_key, new_url)
    print(f"{role}: password rotated · {env_key} rewritten · verified connection as {role}")

    return 0


def rotate_backup_role(generate: bool) -> int:
    """Public entrypoint deliberately refuses before touching local state."""
    _backup_mutation_unavailable()


def neon(method: str, path: str, key: str, body: dict | None = None) -> dict | list:
    req = urllib.request.Request(
        f"{NEON_API}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {key}",
                 "Accept": "application/json",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        sys.exit(f"rotate-credential: Neon API {method} {path} returned {e.code}: "
                 f"{e.read().decode()[:200]}")
    except urllib.error.URLError as e:
        sys.exit(f"rotate-credential: cannot reach the Neon API: {e}")


def rotate_api_key(label: str) -> int:
    with credential_env_lock():
        return _rotate_api_key_locked(label)


def _rotate_api_key_locked(label: str) -> int:
    env = read_env()
    old = env.get("NEON_API_KEY")
    if not old:
        sys.exit(f"rotate-credential: NEON_API_KEY is not in {ENV_PATH}")

    before = neon("GET", "/api_keys", old)
    old_ids = {k["id"] for k in before} if isinstance(before, list) else set()

    created = neon("POST", "/api_keys", old, {"key_name": label})
    # Neon answers this endpoint with an object. A list back means the API
    # shape moved under us, and rotating a credential is the last place to
    # guess: say so and leave the working key alone.
    if not isinstance(created, dict):
        sys.exit("rotate-credential: Neon returned an unexpected shape for the new key "
                 "— nothing changed, old key intact")
    new_key = created.get("key")
    new_id = created.get("id")
    if not new_key or not new_id:
        sys.exit("rotate-credential: Neon did not return a key — nothing changed, old key intact")

    # VERIFY THE NEW KEY WORKS BEFORE ANYTHING IRREVERSIBLE HAPPENS.
    #
    # /projects requires org_id on this account and answers 400 without it — a
    # 400 that says nothing about whether the key is valid. The first run of this
    # tool hit exactly that, after the key was already minted and before db.env
    # was written, which left an orphaned key on the account and the old key
    # still in the file. Fixed by asking a question the API can actually answer.
    # The org is the same constant tools/db-tap.py pins.
    neon("GET", f"/projects?org_id={NEON_ORG}", new_key)

    write_env_key("NEON_API_KEY", new_key)

    # ONLY NOW IS THE OLD ONE SAFE TO REMOVE, and it is removed BY ID.
    #
    # Neon never returns key material on a list, so there is no way to look at
    # two keys and tell which one db.env held. The only honest identification is
    # "every key that existed before this run" — which is exact when the account
    # holds one key and dangerous when it holds several, because one of the
    # others could belong to something this tool knows nothing about. So: one
    # prior key is revoked without ceremony; more than one is REPORTED and left
    # alone, because guessing wrong there breaks something invisible.
    revoked: list[str] = []
    if len(old_ids) == 1:
        kid = next(iter(old_ids))
        neon("DELETE", f"/api_keys/{kid}", new_key)
        revoked.append(str(kid))
    elif len(old_ids) > 1:
        print(f"  NOTE: {len(old_ids)} prior keys exist and NONE were revoked — this tool "
              f"cannot tell which one db.env held, and revoking the wrong one breaks "
              f"something invisible. Revoke the exposed key by id in the Neon console:")
        for k in before:
            print(f"    id={k['id']} name={k.get('name')!r} last_used={k.get('last_used_at')}")

    remaining = neon("GET", "/api_keys", new_key)
    print(f"neon api key: minted '{label}' · db.env rewritten · verified against /projects · "
          f"revoked {len(revoked)} prior key(s){' (' + ', '.join(revoked) + ')' if revoked else ''} · "
          f"{len(remaining)} key(s) now on the account")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="rotate a CARR credential; no value is ever displayed")
    p.add_argument("--role", choices=sorted(ROLE_ENV))
    p.add_argument("--neon-api-key", action="store_true")
    p.add_argument("--generate", action="store_true",
                   help="generate the password in-process instead of prompting")
    p.add_argument("--github-secret", action="store_true",
                   help=f"also set the {GITHUB_SECRET} repo secret the cloud nightly reads, "
                        "only permitted for carr_backup")
    p.add_argument("--label", default="carr-system",
                   help="name for the new Neon API key")
    args = p.parse_args()

    if bool(args.role) == bool(args.neon_api_key):
        p.error("choose exactly one of --role or --neon-api-key")
    if args.github_secret and args.role != "carr_backup":
        p.error("--github-secret is permitted only with --role carr_backup")

    return (rotate_role(args.role, args.generate, args.github_secret)
            if args.role else rotate_api_key(args.label))


if __name__ == "__main__":
    sys.exit(main())
