#!/usr/bin/env python3
"""Provision or revoke the dedicated Dot SQL login after its migration releases.

Run from the released canonical checkout on the Studio. Both commands use the
existing db-tap break-glass boundary; never type a DSN or run under shell tracing.

Provision (generates privately, verifies login, writes an atomic mode-600 file):
  CARR_BREAK_GLASS=1 .venv/bin/python tools/db-tap.py --reason "Provision Dot review login after release" run tools/dot-reader-access.py provision

The only published credential is ~/.config/carr/dot-reader.connection, outside
this repository and outside db.env. Open it locally and paste into the Dot.
Never print it into a transcript. A .pending file survives interrupted work;
rerunning provision reuses that same value rather than generating another one.

Revoke (disables LOGIN, clears password, terminates sessions, tombstones files):
  CARR_BREAK_GLASS=1 .venv/bin/python tools/db-tap.py --reason "Revoke Dot review login" run tools/dot-reader-access.py revoke

The schema changes ship only through the migration/release pipeline. These
commands set or revoke the password after release; neither applies migrations.
To authorize writes later, a forward migration needs table DML and sequence
mutation grants together (serial/identity inserts call nextval):
  GRANT INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public, ops TO dot_reader;
  GRANT USAGE, UPDATE ON ALL SEQUENCES IN SCHEMA public, ops TO dot_reader;
The role's RLS policy already permits all sponsor rows; table privileges enforce
read-only today, including sequence mutation. Future write defaults remain an
explicit release choice. New RLS tables also need an explicit Dot policy;
SELECT defaults alone do not confer all-row visibility.
"""
import argparse
import importlib.util
import os
from pathlib import Path
import stat
import sys
from urllib.parse import unquote

SPEC = importlib.util.spec_from_file_location("rotate_credential", Path(__file__).with_name("rotate-credential.py"))
if SPEC is None or SPEC.loader is None:
    raise SystemExit("dot_reader: sanctioned credential helper unavailable")
credential = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(credential)
# Reuse the sanctioned credential implementation; do not duplicate its writer,
# shell parser, endpoint parser, URL builder, lock or random generator.
_durable_replace = credential._durable_replace
_fsync_directory = credential._fsync_directory
_postgres_parts = credential._postgres_parts
_url_for_role = credential._url_for_role
credential_env_lock = credential.credential_env_lock
read_env = credential.read_env
new_password = credential.new_password
MINT_SOURCE_KEYS = credential.MINT_SOURCE_KEYS
MINT_QUERY = credential.MINT_QUERY
DOT_CONNECTION_PATH = os.path.expanduser("~/.config/carr/dot-reader.connection")


def read_dot_pending() -> str | None:
    path = DOT_CONNECTION_PATH + ".pending"
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    except OSError:
        sys.exit("dot_reader: private pending file cannot be opened safely")
    with os.fdopen(fd, encoding="utf-8") as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600):
            sys.exit("dot_reader: pending file must be an owned regular mode-600 file")
        value = handle.read(16385)
    if len(value) > 16384 or not value.endswith("\n") or value.count("\n") != 1:
        sys.exit("dot_reader: pending file shape refused")
    return value[:-1]


def write_dot_pending(value: str) -> None:
    # The caller holds credential_env_lock. Refuse overwriting a recovery value.
    if os.path.lexists(DOT_CONNECTION_PATH + ".pending"):
        sys.exit("dot_reader: pending state already exists; resume it")
    _durable_replace(DOT_CONNECTION_PATH + ".pending", value + "\n", prefix=".dot-pending.")


def publish_dot_connection(value: str) -> None:
    _durable_replace(DOT_CONNECTION_PATH, value + "\n", prefix=".dot-connection.")
    os.unlink(DOT_CONNECTION_PATH + ".pending")
    _fsync_directory(os.path.dirname(DOT_CONNECTION_PATH))


def dot_reader_action(*, revoke: bool) -> int:
    """No provider/database exception may render credential-bearing diagnostics."""
    try:
        with credential_env_lock():
            return _dot_reader_action(revoke=revoke)
    except Exception:
        print("dot_reader: operation not confirmed; private pending state retained for recovery", file=sys.stderr)
        return 1


def _dot_reader_action(*, revoke: bool) -> int:
    import psycopg
    from psycopg import sql

    owner = os.environ.get("DATABASE_URL")
    if not owner:
        sys.exit("dot_reader: run through db-tap.py break-glass after release")
    _, target = _postgres_parts(owner, "owner target")
    with psycopg.connect(owner, autocommit=True, connect_timeout=10) as conn:
        conn.execute("set statement_timeout='30s'")
        role = conn.execute("""select rolcanlogin,rolconnlimit,rolsuper,rolcreatedb,
          rolcreaterole,rolreplication,rolbypassrls from pg_roles where rolname='dot_reader'""").fetchone()
        if role is None:
            sys.exit("dot_reader: released role missing; nothing changed")
        if revoke:
            conn.execute("alter role dot_reader nologin password null")
            terminated = conn.execute("select pg_terminate_backend(pid,5000) from pg_stat_activity where usename='dot_reader'").fetchall()
            if any(not row[0] for row in terminated):
                sys.exit("dot_reader: new logins revoked; session termination not confirmed")
            for path in (DOT_CONNECTION_PATH, DOT_CONNECTION_PATH + ".pending"):
                if os.path.lexists(path):
                    # Atomic replacement tombstones the credential without
                    # following symlinks or leaving reusable local material.
                    _durable_replace(path, "REVOKED\n", prefix=".dot-revoked.")
            print("dot_reader: login revoked, sessions terminated, private files tombstoned")
            return 0
        if role[1:] != (2, False, False, False, False, False):
            sys.exit("dot_reader: released role attributes do not match; nothing changed")
        boundary = conn.execute("""select
          not has_database_privilege('dot_reader',current_database(),'CREATE,TEMPORARY')
          and not exists(select 1 from pg_namespace where has_schema_privilege('dot_reader',oid,'CREATE'))
          and not exists(select 1 from pg_auth_members where member='dot_reader'::regrole
            or (roleid='dot_reader'::regrole and (member<>'neondb_owner'::regrole
              or not admin_option or inherit_option or set_option)))
          and not exists(select 1 from pg_class where relowner='dot_reader'::regrole)
          and not exists(select 1 from pg_proc where proowner='dot_reader'::regrole)
          and not exists(select 1 from pg_class c join pg_namespace n on n.oid=c.relnamespace
            where n.nspname in ('public','ops') and c.relkind in ('r','p','v','m','f')
              and (not has_table_privilege('dot_reader',c.oid,'SELECT')
                or has_table_privilege('dot_reader',c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN,SELECT WITH GRANT OPTION')
             or has_any_column_privilege('dot_reader',c.oid,'INSERT,UPDATE,REFERENCES,SELECT WITH GRANT OPTION')))
          and not exists(select 1 from pg_class c join pg_namespace n on n.oid=c.relnamespace
            where n.nspname in ('public','ops') and c.relkind='S'
              and case when c.relkind='S' then has_sequence_privilege('dot_reader',c.oid,'USAGE,UPDATE,SELECT WITH GRANT OPTION') else false end)
          and not has_database_privilege('dot_reader',current_database(),'CONNECT WITH GRANT OPTION')
          and not exists(select 1 from pg_namespace where has_schema_privilege('dot_reader',oid,'USAGE WITH GRANT OPTION'))
          and not exists(select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
            where n.nspname in ('public','ops') and has_function_privilege('dot_reader',p.oid,'EXECUTE WITH GRANT OPTION'))
        """).fetchone()
        if boundary != (True,):
            sys.exit("dot_reader: effective read-only boundary drifted; nothing changed")
        if not role[0]:
            sys.exit("dot_reader: login revoked; a release must explicitly restore LOGIN")
        pending = read_dot_pending()
        if pending is None:
            # Copy the proven routine endpoint, then verify it matches the
            # sanctioned owner connection. Never reuse an owner identity.
            env = read_env()
            peer = next((env[k] for k in MINT_SOURCE_KEYS if env.get(k)), None)
            if peer is None:
                sys.exit("dot_reader: routine database endpoint unavailable; nothing changed")
            peer_parts, peer_target = _postgres_parts(peer, "routine target")
            if peer_target != target:
                sys.exit("dot_reader: routine/owner target mismatch; nothing changed")
            pending = _url_for_role(peer_parts, "dot_reader", new_password(), MINT_QUERY)
            write_dot_pending(pending)
        pending_parts, pending_target = _postgres_parts(pending, "pending target")
        if pending_parts.username != "dot_reader" or pending_target != target:
            sys.exit("dot_reader: pending identity/target mismatch; nothing changed")
        conn.execute(sql.SQL("alter role dot_reader password {}").format(sql.Literal(unquote(pending_parts.password))))
        with psycopg.connect(pending, connect_timeout=10) as dot:
            if dot.execute("select session_user,current_user").fetchone() != ("dot_reader", "dot_reader"):
                sys.exit("dot_reader: login identity verification failed; pending state retained")
        publish_dot_connection(pending)
    print("dot_reader: password set, login verified, private mode-600 connection file written")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("provision", "revoke"))
    args = parser.parse_args()
    return dot_reader_action(revoke=args.action == "revoke")


if __name__ == "__main__":
    sys.exit(main())
