#!/usr/bin/env python3
"""Hermetic tests for the dormant carr_backup rotation primitives.

Backup mutation is deliberately unavailable until a canonical server-validated
receipt exists.  These tests therefore exercise no provider or database: they
cover the fail-closed entrypoints and the future-enablement helpers only.
"""
from __future__ import annotations

import contextlib
import ast
import importlib.util
import io
import os
import stat
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("rotate_credential", REPO / "tools" / "rotate-credential.py")
if SPEC is None or SPEC.loader is None:
    raise SystemExit("rotate-credential-mint-selftest: cannot load credential tool")
rc: Any = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rc)

FAILURES: list[str] = []
OWNER = "postgresql://owner:ownerpw@ep-x-123.us-east-2.aws.neon.tech/neondb?sslmode=require"  # ci-secret-scan: allow — hermetic fixture
PEER = "postgresql://carr_jobs:oldpw@ep-x-123.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"  # ci-secret-scan: allow — hermetic fixture


def check(label: str, ok: bool) -> None:
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        FAILURES.append(label)


def refused(call) -> str:
    try:
        call()
    except SystemExit as exc:
        return str(exc)
    return ""


@contextlib.contextmanager
def isolated_state():
    with tempfile.TemporaryDirectory() as raw:
        previous = rc.ENV_PATH
        rc.ENV_PATH = str(Path(raw) / "db.env")
        try:
            yield Path(raw)
        finally:
            rc.ENV_PATH = previous


class Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self, row):
        self.row = row
        self.query = ""

    def execute(self, query):
        self.query = str(query)
        return Result(self.row)


def verifier_rotation_cases() -> None:
    role = "carr_program5_forward_fix_verifier"
    key = "CARR_DB_PROGRAM5_FORWARD_FIX_VERIFIER_URL"
    existing = PEER.replace("carr_jobs", role)
    password = "V" * 40
    events: list[object] = []
    identity: tuple[str, str, bool] | tuple[str] | None = (role, role, True)

    class Statement:
        def __init__(self, template):
            self.template = template

        def format(self, *args):
            return (self.template, *args)

    class RotationConnection:
        def __enter__(self):
            return self

        def __exit__(self, *unused):
            return False

        def execute(self, query):
            events.append(("execute", query))
            return Result((identity[1],) if query == "select current_user"
                          and identity and len(identity) == 3 else identity)

        def commit(self):
            events.append("commit")

    def connect(dsn):
        events.append(("connect", dsn))
        return RotationConnection()

    def write(key, value):
        events.append(("write", key, value))

    fake_psycopg = types.ModuleType("psycopg")
    fake_psycopg.connect = connect  # type: ignore[attr-defined]
    fake_psycopg.sql = types.SimpleNamespace(  # type: ignore[attr-defined]
        SQL=Statement, Identifier=lambda value: value, Literal=lambda value: value)
    with patch.dict(os.environ, {"DATABASE_URL": OWNER}, clear=True), \
            patch.dict(sys.modules, {"psycopg": fake_psycopg}), \
            patch.object(rc, "credential_env_lock", contextlib.nullcontext), \
            patch.object(rc, "read_env", return_value={key: existing}), \
            patch.object(rc, "new_password", return_value=password), \
            patch.object(rc, "write_env_key", write):
        # Every rejected URI must stop before opening a connection or generating
        # a password; the fake records mutations independently of its identity.
        invalid = {
            "malformed URI": "not-a-postgres-uri",
            "wrong host": existing.replace("ep-x-123", "ep-other-456"),
            "wrong port": existing.replace("/neondb", ":5433/neondb"),
            "wrong database": existing.replace("/neondb", "/otherdb"),
            "wrong login": PEER,
            "query login override": existing + "&user=other",
            "encoded startup role override": existing + "&options=-c%20role%3D" + role,
            "duplicate TLS parameter": existing + "&sslmode=require",
            "malformed port": existing.replace("/neondb", ":bad/neondb"),
            "zero port": existing.replace("/neondb", ":0/neondb"),
            "negative port": existing.replace("/neondb", ":-1/neondb"),
            "out-of-range port": existing.replace("/neondb", ":65536/neondb"),
        }
        for label, dsn in invalid.items():
            events.clear()
            with patch.object(rc, "read_env", return_value={key: dsn}), \
                    patch.object(rc, "new_password", side_effect=AssertionError("generation reached")):
                try:
                    error = refused(lambda: rc.rotate_role(role, True))
                except AssertionError:
                    error = ""
            check(label + " refuses before all database mutation", bool(error) and events == [])
        for label, owner in {
            "malformed owner": "malformed",
            "zero owner port": OWNER.replace("/neondb", ":0/neondb"),
            "negative owner port": OWNER.replace("/neondb", ":-1/neondb"),
            "out-of-range owner port": OWNER.replace("/neondb", ":65536/neondb"),
        }.items():
            events.clear()
            with patch.dict(os.environ, {"DATABASE_URL": owner}), \
                    patch.object(rc, "new_password", side_effect=AssertionError("generation reached")):
                try:
                    error = refused(lambda: rc.rotate_role(role, True))
                except AssertionError:
                    error = ""
            check(label + " refuses before all database mutation", bool(error) and events == [])

        for port in (1, 5432, 65535):
            _, target = rc._postgres_parts(existing.replace("/neondb", f":{port}/neondb"), key)
            check(f"valid explicit port {port} is preserved", target[1] == port)

        for identity, label in [
            (("other_login", role, True), "different session login"),
            ((role, "other_role", True), "different effective role"),
            ((role, role, False), "missing verifier membership"),
            (None, "missing identity row"),
        ]:
            events.clear()
            error = refused(lambda: rc.rotate_role(role, True))
            check(label + " refuses without publishing", bool(error) and not any(
                isinstance(event, tuple) and event[0] == "write" for event in events))

        identity = (role, role, True)
        events.clear()
        result = rc.rotate_role(role, True)
        check("verifier rotates and preserves URL options after exact identity verification",
              result == 0 and events == [
                  ("connect", OWNER),
                  ("execute", ("alter role {} with password {}", role, password)),
                  "commit",
                  ("connect", existing.replace("oldpw", password)),
                  ("execute", "select session_user,current_user,pg_has_role(session_user,'carr_program5_forward_fix_verifiers','member')"),
                  ("write", key, existing.replace("oldpw", password)),
              ])
        for prior_role in ("carr_jobs", "app_exporter_local"):
            events.clear()
            identity = (prior_role,)
            prior = PEER.replace("carr_jobs", prior_role)
            with patch.object(rc, "read_env", return_value={rc.ROLE_ENV[prior_role]: prior}):
                result = rc.rotate_role(prior_role, True)
            check(prior_role + " retains rotation and current-user verification",
                  result == 0 and events[-2:] == [
                      ("execute", "select current_user"),
                      ("write", rc.ROLE_ENV[prior_role], prior.replace("oldpw", password)),
                  ])
        events.clear()
        with patch.object(rc, "read_env", return_value={}), \
                patch.object(rc, "new_password", side_effect=AssertionError("generation reached")):
            missing = refused(lambda: rc.rotate_role(role, True))
        check("verifier cannot mint a missing connection", "nothing to rotate" in missing and events == [])


def main() -> int:
    verifier_rotation_cases()
    source = (REPO / "tools" / "rotate-credential.py").read_text(encoding="utf-8")
    workflow = (REPO / ".github" / "workflows" / "backup-nightly.yml").read_text(encoding="utf-8")
    tree = ast.parse(source)
    function_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    check("no callable carr_backup ALTER ROLE path remains",
          "_rotate_backup_role_enabled" not in function_names
          and 'sql.Identifier("carr_backup")' not in source
          and "alter role carr_backup" not in source.lower()
          and {"rotate_role", "rotate_backup_role"}.issubset(function_names)
          and rc.MINTABLE == set())
    check("workflow documentation does not sanction direct backup credential mutation",
          "PROVISIONING IS DISABLED" in workflow
          and "alter role carr_backup password" not in workflow.lower()
          and "gh secret set BACKUP_DATABASE_URL" not in workflow
          and "Provisioning steps are in this workflow" not in workflow
          and "run it manually (workflow_dispatch)" not in workflow)
    _, owner_target = rc._postgres_parts(OWNER, "owner")
    minted = rc.mint_url("carr_backup", {"CARR_DB_JOBS_URL": PEER}, "A" * 40, owner_target)
    check("canonical TLS-only peer mints a pinned backup URL",
          minted.endswith("?sslmode=require") and "oldpw" not in minted)
    exact = (
        "sslmode=require&host=evil", "sslmode=require&hostaddr=127.0.0.1",
        "sslmode=require&port=5433", "sslmode=require&dbname=other",
        "sslmode=require&user=other", "sslmode=require&service=other",
        "sslmode=require&options=-csearch_path=evil", "sslmode=require&sslmode=require",
        "channel_binding=require&sslmode=require", "sslmode=verify-full",
    )
    for query in exact:
        dsn = f"postgresql://x:y@host/db?{query}"
        check(f"libpq override refuses {query.split('=', 1)[0]}",
              "ambiguous libpq query override" in refused(lambda dsn=dsn: rc._postgres_parts(dsn, "test")))
    check("owner, peer, existing, and pending share strict parser",
          all("ambiguous libpq query override" in refused(lambda value=value: rc._postgres_parts(value, "test"))
              for value in ("postgresql://x:y@host/db?sslmode=require&user=x",) * 4))

    # Both public backup entrypoints must stop before every local or provider
    # primitive.  Replacements would throw if any one were reached.
    real_lock, real_read, real_run = rc.credential_env_lock, rc.read_env, rc.subprocess.run
    rc.credential_env_lock = lambda: (_ for _ in ()).throw(AssertionError("lock reached"))
    rc.read_env = lambda: (_ for _ in ()).throw(AssertionError("env reached"))
    rc.subprocess.run = lambda *a, **k: (_ for _ in ()).throw(AssertionError("subprocess reached"))
    try:
        with patch.dict(os.environ, {"CARR_BREAK_GLASS": "1"}, clear=True):
            check("rotate_role backup refusal precedes all mutation primitives even with break-glass",
                  "disabled" in refused(lambda: rc.rotate_role("carr_backup", True, True)))
            check("direct backup entrypoint refusal precedes all mutation primitives even with break-glass",
                  "disabled" in refused(lambda: rc.rotate_backup_role(True)))
        check("generic internal helper cannot bypass carr_backup refusal",
              "permitted only" in refused(lambda: rc._rotate_existing_role("carr_backup", True)))
        check("deepest ALTER ROLE helper cannot bypass carr_backup refusal",
              "permitted only" in refused(lambda: rc._rotate_existing_role_locked("carr_backup", True)))
    finally:
        rc.credential_env_lock, rc.read_env, rc.subprocess.run = real_lock, real_read, real_run

    with isolated_state() as raw:
        pending = "postgresql://carr_backup:Z@host/db?sslmode=require"
        rc._write_pending_backup_url(pending)
        path = Path(rc._private_pending_path())
        check("pending canonical is private regular 0600 with one link",
              path.read_text(encoding="utf-8") == pending + "\n"
              and stat.S_IMODE(path.stat().st_mode) == 0o600 and path.stat().st_nlink == 1)
        check("pending canonical publication never overwrites",
              "already exists" in refused(lambda: rc._write_pending_backup_url(pending)))
        prefix = rc._prepublication_prefix(str(path))
        fd, twin = tempfile.mkstemp(dir=raw, prefix=prefix)
        os.write(fd, (pending + "\n").encode())
        os.fsync(fd)
        os.close(fd)
        os.chmod(twin, 0o600)
        os.unlink(path)
        os.link(twin, path)
        rc._clean_prepublication_temps(str(path))
        check("only a validated same-inode publication twin is cleaned", path.stat().st_nlink == 1)
        rc._clear_pending_backup_url()
        real_link = rc.os.link
        rc.os.link = lambda *unused: (_ for _ in ()).throw(RuntimeError("injected prepublish failure"))
        try:
            try:
                rc._write_pending_backup_url(pending)
            except RuntimeError:
                pass
            check("prepublication crash leaves canonical pending state absent", not path.exists())
            check("prepublication temp is safely cleaned on next attempt",
                  not list(raw.glob(path.name + ".prepublish.*")))
        finally:
            rc.os.link = real_link

        lock = Path(rc.ENV_PATH + ".rotate-credential.lock")
        target = raw / "not-a-lock"
        target.write_text("x", encoding="utf-8")
        lock.symlink_to(target)
        check("symlink lock is refused", "cannot be opened safely" in refused(lambda: rc.credential_env_lock().__enter__()))
        lock.unlink()
        lock.write_text("x", encoding="utf-8")
        os.chmod(lock, 0o644)
        check("non-0600 lock is refused", "not a private regular" in refused(lambda: rc.credential_env_lock().__enter__()))
        lock.unlink()
        lock.write_text("x", encoding="utf-8")
        os.chmod(lock, 0o600)
        sibling = raw / "lock-hardlink"
        os.link(lock, sibling)
        check("multi-link lock is refused", "not a private regular" in refused(lambda: rc.credential_env_lock().__enter__()))
        sibling.unlink()
        lock.unlink()
        with rc.credential_env_lock():
            check("private regular lock can be acquired", True)

    recorded: dict[str, object] = {}
    real_run, old_host = rc.subprocess.run, os.environ.get("GH_HOST")
    rc.subprocess.run = lambda argv, **kw: (recorded.update(argv=argv, **kw) or types.SimpleNamespace(returncode=0))
    try:
        rc.set_github_secret("postgresql://carr_backup:NEVERPRINT@host/db?sslmode=require")  # ci-secret-scan: allow — hermetic fixture
        gh_environment = cast(dict[str, str], recorded["env"])
        check("GitHub call pins repository, host, and a bounded timeout",
              recorded["argv"] == ["gh", "secret", "set", "BACKUP_DATABASE_URL", "--repo", "jbookout/carr-system"]
              and gh_environment["GH_HOST"] == "github.com" and recorded["timeout"] == 30)
        rc.subprocess.run = lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("gh", 30))
        check("GitHub timeout is secret-free and resumable", "timed out" in refused(
            lambda: rc.set_github_secret("postgresql://carr_backup:NEVERPRINT@host/db?sslmode=require")))  # ci-secret-scan: allow — hermetic fixture
        os.environ["GH_HOST"] = "evil.example"
        check("ambient GitHub host redirect is refused", "non-github.com" in refused(
            lambda: rc.set_github_secret("postgresql://carr_backup:NEVERPRINT@host/db?sslmode=require")))  # ci-secret-scan: allow — hermetic fixture
    finally:
        rc.subprocess.run = real_run
        if old_host is None:
            os.environ.pop("GH_HOST", None)
        else:
            os.environ["GH_HOST"] = old_host

    identity = Connection(("carr_backup", "carr_backup"))
    rc.verify_backup_connection(identity)
    check("credential identity requires session_user and current_user", "session_user" in identity.query)
    check("credential identity rejects SET ROLE/proxy mismatch",
          "session identity" in refused(lambda: rc.verify_backup_connection(Connection(("owner", "carr_backup")))))
    owner_ok = Connection((True,) * 9)
    rc.verify_backup_least_privilege(owner_ok)
    check("owner-derived verification covers powerful attrs, reachability, ownership, ACLs, tables and sequences",
          all(token in owner_ok.query for token in ("rolbypassrls", "reachable", "pg_database",
              "pg_proc", "aclexplode", "has_table_privilege", "has_sequence_privilege", "is_grantable")))
    check("owner-derived verification fails closed on any missing contract element",
          "least-privilege" in refused(lambda: rc.verify_backup_least_privilege(Connection((True,) * 8 + (False,)))))

    # db.env HAS TWO PARSERS AND ONLY ONE OF THEM IS PYTHON.  These cover the
    # shell one.  The failure being fenced is real and cost six days: a value
    # carrying `&channel_binding=require` reached db.env unquoted on 2026-08-20,
    # zsh aborted the whole `set -a; . db.env` at that line, and every key in the
    # file — not just the bad one — went missing for every job on the machine.
    with isolated_state() as raw:
        broken = raw / "broken.env"
        broken.write_text(
            "CARR_X_URL=postgresql://u:pw@h.example/db?sslmode=require&channel_binding=require\n",  # ci-secret-scan: allow — hermetic fixture
            encoding="utf-8")
        quoted = raw / "quoted.env"
        quoted.write_text(
            "CARR_X_URL='postgresql://u:pw@h.example/db?sslmode=require&channel_binding=require'\n",  # ci-secret-scan: allow — hermetic fixture
            encoding="utf-8")
        check("an unquoted ampersand DSN is reported as a shell parse failure",
              rc.shell_parse_failure(str(broken)) == "1")
        check("the single-quoted form of the same DSN parses",
              rc.shell_parse_failure(str(quoted)) is None)
        check("a shell-unsourceable candidate is refused by name and line",
              "does not parse as shell at line 1"
              in refused(lambda: rc._require_shell_sourceable(str(broken))))
        # A machine without the probe is where this tool already stood; the
        # guard must not turn "cannot ask" into "must not rotate".
        real_zsh = rc.ZSH
        rc.ZSH = str(raw / "no-such-shell")
        try:
            check("an unrunnable shell probe never blocks a rotation",
                  rc.shell_parse_failure(str(broken)) is None
                  and rc._require_shell_sourceable(str(broken)) is None)
        finally:
            rc.ZSH = real_zsh

    with isolated_state() as raw:
        env_path = Path(rc.ENV_PATH)
        env_path.write_text("CARR_KEEP_URL='postgresql://k:kp@h.example/db'\n# a comment\n\n",  # ci-secret-scan: allow — hermetic fixture
                            encoding="utf-8")
        env_path.chmod(0o600)
        before = env_path.read_bytes()
        rc.write_env_key("CARR_X_URL", "postgresql://u:pw@h.example/db?sslmode=require&channel_binding=require")  # ci-secret-scan: allow — hermetic fixture
        after = env_path.read_text(encoding="utf-8").splitlines()
        check("a written value is single-quoted and the rest of the file survives",
              after[-1].startswith("CARR_X_URL='") and after[-1].endswith("'")
              and after[:3] == ["CARR_KEEP_URL='postgresql://k:kp@h.example/db'",  # ci-secret-scan: allow — hermetic fixture
                                "# a comment", ""])
        check("the file rotate-credential just wrote is sourceable",
              rc.shell_parse_failure(str(env_path)) is None)

        # The quoting and the guard are two claims, and only the second one
        # survives a future writer that forgets the first.  Disabling the quoting
        # is the only way to prove write_env_key is actually wired to the guard
        # rather than merely being correct today.
        env_path.write_bytes(before)
        real_quote = rc.shell_quote
        rc.shell_quote = lambda value: value
        try:
            check("write_env_key refuses a value the shell could not source",
                  "does not parse as shell at line" in refused(
                      lambda: rc.write_env_key(
                          "CARR_X_URL",
                          "postgresql://u:pw@h.example/db?sslmode=require&channel_binding=require")))  # ci-secret-scan: allow — hermetic fixture
        finally:
            rc.shell_quote = real_quote
        check("the refused write left db.env exactly as it was",
              env_path.read_bytes() == before)

        # The guard must fence the SWAP, not merely report afterwards: a
        # credential file that fails its contract may not be live for an instant.
        env_path.write_bytes(before)
        original = env_path.read_bytes()
        def _always_bad(_candidate: str) -> None:
            raise SystemExit("verify refused")
        try:
            rc._durable_replace(rc.ENV_PATH, "CARR_X_URL=broken&value\n", prefix=".db.env.",
                                verify=_always_bad)
        except SystemExit:
            pass
        leftovers = [entry.name for entry in env_path.parent.iterdir()
                     if entry.name.startswith(".db.env.")]
        check("a refused write leaves the live file byte-identical and no temp behind",
              env_path.read_bytes() == original and leftovers == [])

    if FAILURES:
        print(f"rotate-credential-mint-selftest: {len(FAILURES)} FAILED")
        return 1
    print("rotate-credential-mint-selftest: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
