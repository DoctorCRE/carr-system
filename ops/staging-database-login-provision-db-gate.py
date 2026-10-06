#!/usr/bin/env python3
# ci: db-gate
"""PostgreSQL 17 proof for the staging login profiles, against a real database.

TWO SHAPES OF PROFILE, AND THE SECOND ONE IS WHY THIS GATE GREW (2026-09-13).
`reader` and `writer` are roles this tool CREATES and credentials in one
operation. `gate_zero_producer` is not: migration 0502 creates
`carr_gate_zero_producer` as a LOGIN role with NO PASSWORD, so its first
provisioning is an ADOPTION -- the owner connection sets the password on a role
that is already there. That transition, and the state that looks identical to it
from outside the database, are proved here rather than against fakes:

- FIRST PASSWORDLESS TRANSITION: pg_authid says the seat has no password, the
  adopt path sets one, and the seat then AUTHENTICATES for real with the exact
  canonical closure.
- LOST LOCAL FILE: the same role, the same absent credential file, and the one
  difference the database can see -- a password is already set. Adoption must
  refuse and must leave the live credential working, because the local file
  lives under one machine's home directory and its absence proves nothing.

The seat is restored to exactly the state 0502 left behind before this gate
returns: no password, no role-level settings.
"""

from __future__ import annotations

import importlib.util
import ipaddress
import os
import pathlib
import sys
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict


REPO = pathlib.Path(__file__).resolve().parents[1]
PROVISIONER = REPO / "tools/provision-staging-app-writer.py"


def load_provisioner():
    spec = importlib.util.spec_from_file_location("staging_database_login_gate", PROVISIONER)
    if spec is None or spec.loader is None:
        raise RuntimeError(PROVISIONER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def role_dsn(owner_dsn: str, role: str, password: str) -> str:
    parsed = urlsplit(owner_dsn)
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    return urlunsplit((parsed.scheme, f"{quote(role)}:{quote(password)}@{host}{port}",
                       parsed.path, parsed.query, ""))


def require_loopback(dsn: str) -> None:
    try:
        conninfo = conninfo_to_dict(dsn)
    except psycopg.Error as exc:
        raise RuntimeError("disposable-only gate requires valid explicit conninfo") from exc
    if conninfo.get("service") or conninfo.get("servicefile"):
        raise RuntimeError("disposable-only gate refuses libpq service indirection")
    hosts: list[str] = []
    for key in ("host", "hostaddr"):
        value = str(conninfo.get(key) or "")
        if not value:
            continue
        if "," in value:
            raise RuntimeError("disposable-only gate refuses multi-host conninfo")
        hosts.append(value)
    if not hosts or "," in str(conninfo.get("port") or ""):
        raise RuntimeError("disposable-only gate requires one explicit loopback target")
    for host in hosts:
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == "localhost"
        if not loopback:
            raise RuntimeError("disposable-only gate refuses every non-loopback DATABASE_URL")
    if len(set(hosts)) > 1 and not all(
        host == "localhost" or ipaddress.ip_address(host).is_loopback for host in hosts
    ):
        raise RuntimeError("disposable-only gate refuses every non-loopback DATABASE_URL")


def expect_denied(cur, statement: str, label: str) -> None:
    cur.execute("savepoint denied_operation")
    try:
        cur.execute(statement)
    except psycopg.errors.InsufficientPrivilege:
        cur.execute("rollback to savepoint denied_operation")
        return
    raise RuntimeError(f"app_reader unexpectedly allowed {label}")


def main() -> int:
    admin_dsn = os.environ.get("DATABASE_URL")
    if not admin_dsn:
        raise RuntimeError("DATABASE_URL is required")
    require_loopback(admin_dsn)
    provision = load_provisioner()
    profiles = {profile.label: profile for profile in provision.PROFILES}
    plans = {
        label: provision.snapshot_grants.load_current_grants_to_role(
            provision.SCHEMA, provision.MIGRATIONS, profile.grant_role
        ) for label, profile in profiles.items()
    }
    seat = profiles["gate_zero_producer"]
    if not seat.created_by_migration:
        raise RuntimeError("the Gate Zero seat is no longer a migration-created profile")
    passwords = {"reader": "reader-fixture-" + "r" * 48,
                 "writer": "writer-fixture-" + "w" * 48,
                 "seat": "seat-fixture-" + "g" * 48,
                 "seat_rotation": "rotation-fixture-" + "x" * 44,
                 "owner": "owner-fixture-" + "o" * 48}  # ci-secret-scan: allow — disposable loopback fixture
    with psycopg.connect(admin_dsn) as admin:
        failure: BaseException | None = None
        fixture_mutated = False
        setup_committed = False
        read_all_granted = False
        try:
            with admin.cursor() as cur:
                cur.execute(
                    "select current_user,r.oid::bigint,r.rolsuper from pg_roles r "
                    "where r.rolname=current_user"
                )
                admin_identity = cur.fetchone()
                if (
                    admin_identity is None
                    or int(admin_identity[1]) != provision.BOOTSTRAP_SUPERUSER_OID
                    or admin_identity[2] is not True
                ):
                    raise RuntimeError(
                        "disposable gate requires the bootstrap PostgreSQL superuser"
                    )
                bootstrap_role = str(admin_identity[0])
                cur.execute(
                    "select rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,"
                    "rolreplication,rolbypassrls "
                    "from pg_roles where rolname='neondb_owner'"
                )
                if cur.fetchone() != (False, False, False, False, False, False):
                    raise RuntimeError("neondb_owner fixture is absent or already powerful")
                cur.execute(
                    "select granted.rolname,m.admin_option,m.inherit_option,m.set_option,"
                    "grantor.oid::bigint from pg_auth_members m "
                    "join pg_roles granted on granted.oid=m.roleid "
                    "join pg_roles member on member.oid=m.member "
                    "join pg_roles grantor on grantor.oid=m.grantor "
                    "where member.rolname='neondb_owner' "
                    "and granted.rolname in ('carr_reader','carr_writer') order by 1"
                )
                baseline_bundle_edges = tuple(cur.fetchall())
                expected_bundle_edges = (
                    ("carr_reader", False, True, True, provision.BOOTSTRAP_SUPERUSER_OID),
                    ("carr_writer", False, True, True, provision.BOOTSTRAP_SUPERUSER_OID),
                )
                if baseline_bundle_edges != expected_bundle_edges:
                    raise RuntimeError("neondb_owner fixture bundle edges are not exact")
                cur.execute(
                    "select pg_has_role('neondb_owner',%s,'MEMBER')",
                    (bootstrap_role,),
                )
                if cur.fetchone() != (False,):
                    raise RuntimeError("neondb_owner fixture reaches the bootstrap role")
                cur.execute(
                    "select rolname from pg_roles where "
                    "rolname in ('app_reader','app_writer') order by rolname"
                )
                if cur.fetchall():
                    raise RuntimeError("disposable login roles already exist before gate")
                # THE SEAT'S BASELINE, captured rather than assumed. This class
                # loads the committed schema and applies every pending migration
                # before any db-gate runs, so 0502 has created the seat and
                # nothing has credentialed it. Anything else is the finding this
                # gate exists to catch, not a reason to skip.
                # rolpassword lives ONLY in pg_authid; rolconfig lives only in
                # the pg_roles view. The baseline needs both, so it joins them.
                cur.execute(
                    "select r.rolcanlogin,a.rolpassword is null,r.rolconfig is null "
                    "from pg_roles r join pg_authid a on a.oid=r.oid "
                    "where r.rolname=%s", (seat.login_role,)
                )
                seat_baseline = cur.fetchone()
                if seat_baseline != (True, True, True):
                    raise RuntimeError(
                        f"{seat.login_role} is not the pristine passwordless LOGIN role "
                        f"migration 0502 creates: {seat_baseline!r}"
                    )
                fixture_mutated = True
                cur.execute(sql.SQL(
                    "alter role neondb_owner login createrole nosuperuser nocreatedb "
                    "noreplication nobypassrls password {}"
                ).format(sql.Literal(passwords["owner"])))
                for profile in profiles.values():
                    if profile.bundle_role is None:
                        # THE MIGRATED SEAT HAS NO BUNDLE, so the edge the owner
                        # needs is the one PostgreSQL would have created had the
                        # owner created the role: ADMIN, no INHERIT, no SET. Any
                        # other shape fails the profile's own creator-edge check,
                        # which is the point -- the gate must not hand the seat
                        # authority the real provisioning path would refuse.
                        cur.execute(sql.SQL(
                            "grant {} to neondb_owner with admin true, inherit false, set false"
                        ).format(sql.Identifier(profile.grant_role)))
                    else:
                        cur.execute(sql.SQL(
                            "grant {} to neondb_owner with admin true"
                        ).format(sql.Identifier(profile.grant_role)))
            admin.commit()
            setup_committed = True

            direct_owner_dsn = role_dsn(
                admin_dsn, "neondb_owner", passwords["owner"],
            )
            with psycopg.connect(direct_owner_dsn) as owner:
                with owner.cursor() as cur:
                    creator = provision.require_direct_owner_identity(cur)
                    cur.execute(
                        "select rolname from pg_roles where "
                        "rolname in ('app_reader','app_writer') order by rolname"
                    )
                    if cur.fetchall():
                        raise RuntimeError("disposable login roles already exist before gate")
                    for label, profile in profiles.items():
                        bundle = provision.collect_role_authority(cur, profile.grant_role)
                        if set(bundle.direct_acl_facts) != set(
                            provision.snapshot_grants.acl_facts(plans[label])
                        ):
                            raise RuntimeError(
                                f"{profile.grant_role} must already be exact; gate will not repair it"
                            )
                owner.rollback()

                for label in ("reader", "writer"):
                    provision.apply_login_profile(
                        owner, profiles[label], plans[label], passwords[label],
                        expected_creator=creator,
                    )
                    provision.validate_profile_login(
                        role_dsn(admin_dsn, profiles[label].login_role, passwords[label]),
                        profiles[label], plans[label], expected_creator=creator,
                    )

                reader_dsn = role_dsn(admin_dsn, "app_reader", passwords["reader"])
                with psycopg.connect(reader_dsn) as reader, reader.cursor() as cur:
                    cur.execute("select session_user,current_user")
                    if cur.fetchone() != ("app_reader", "app_reader"):
                        raise RuntimeError("reader authentication identity is wrong")
                    cur.execute(
                        "select has_column_privilege(current_user,'public.actor','id','select'),"
                        "has_column_privilege(current_user,'public.actor','slug','select'),"
                        "has_table_privilege(current_user,'ops.authority_receipt','select'),"
                        "has_table_privilege(current_user,'public.actor','insert'),"
                        "has_table_privilege(current_user,'public.actor','update'),"
                        "has_table_privilege(current_user,'public.actor','delete')"
                    )
                    privilege_row = cur.fetchone()
                    if privilege_row != (True, True, True, False, False, False):
                        raise RuntimeError(
                            f"reader SELECT/DML privilege boundary is wrong: {privilege_row!r}"
                        )
                    cur.execute(
                        "select coalesce(bool_or(has_sequence_privilege(current_user,c.oid,'usage')),false) "
                        "from pg_class c where c.relkind='S'"
                    )
                    if cur.fetchone() != (False,):
                        raise RuntimeError("reader has sequence usage")
                    expect_denied(cur, "create table public.reader_escalation(id integer)", "DDL")
                    expect_denied(cur, "create role reader_escalation", "role creation")
                    expect_denied(
                        cur,
                        "insert into retrieval_proposal default values",
                        "protected mutation",
                    )
                    reader.rollback()

                # ---- THE SEAT'S PASSWORDLESS WITNESS, AND WHO CAN READ IT ---
                # `rolpassword` lives only in pg_authid, which no ordinary role
                # may read. On Neon the production owner CAN: neondb_owner
                # reaches neon_superuser, which reaches pg_read_all_data -- the
                # predefined role that confers SELECT on every table including
                # pg_authid. That reachability is measured, not assumed: it is
                # the same list tools/cleanup-staging-app-writer.py pins as
                # EXPECTED_PROVIDER_REACHABLE_ROLES. This fixture's neondb_owner
                # starts WITHOUT it, so the first thing proved here is the
                # fail-closed half.
                blind_owner = psycopg.connect(direct_owner_dsn)
                try:
                    try:
                        provision.apply_login_profile(
                            blind_owner, seat, plans["gate_zero_producer"],
                            passwords["seat"], expected_creator=creator, adopt=True,
                        )
                    except provision.ProvisioningRefusal as exc:
                        if "cannot see it" not in str(exc):
                            raise RuntimeError(
                                f"an owner that cannot read pg_authid refused for the "
                                f"wrong reason: {exc}")
                    else:
                        raise RuntimeError(
                            "adoption proceeded without ever proving the seat passwordless")
                finally:
                    blind_owner.close()
                with admin.cursor() as cur:
                    cur.execute(
                        "select rolpassword is null from pg_authid where rolname=%s",
                        (seat.login_role,),
                    )
                    if cur.fetchone() != (True,):
                        raise RuntimeError(
                            "the refused adoption credentialed the seat anyway")
                    cur.execute("grant pg_read_all_data to neondb_owner")
                admin.commit()
                read_all_granted = True

                # ---- THE MIGRATED SEAT'S FIRST PASSWORDLESS TRANSITION ------
                # Not a fake anywhere below: the role is the one 0502 created,
                # the password is set by the real adopt path, and the proof is a
                # real authenticated connection.
                seat_owner = psycopg.connect(direct_owner_dsn)
                try:
                    with seat_owner.cursor() as cur:
                        if not provision.role_is_passwordless(cur, seat.login_role):
                            raise RuntimeError(
                                f"{seat.login_role} was credentialed before its own transition")
                    seat_owner.rollback()
                    provision.apply_login_profile(
                        seat_owner, seat, plans["gate_zero_producer"], passwords["seat"],
                        expected_creator=creator, adopt=True,
                    )
                    seat_dsn = role_dsn(admin_dsn, seat.login_role, passwords["seat"])
                    provision.validate_profile_login(
                        seat_dsn, seat, plans["gate_zero_producer"], expected_creator=creator,
                    )
                    with psycopg.connect(seat_dsn) as authenticated, authenticated.cursor() as cur:
                        cur.execute("select session_user,current_user")
                        if cur.fetchone() != (seat.login_role, seat.login_role):
                            raise RuntimeError("the adopted seat authenticates as the wrong role")
                        authenticated.rollback()
                    with seat_owner.cursor() as cur:
                        if provision.role_is_passwordless(cur, seat.login_role):
                            raise RuntimeError("the adopted seat still has no password")
                    seat_owner.rollback()
                    # THE STORED VERIFIER IS THE WITNESS, NOT A LOGIN ATTEMPT.
                    # This disposable cluster authenticates loopback connections
                    # on trust, so connecting with a wrong password proves
                    # nothing here. What a refused rotation must leave untouched
                    # is the SCRAM verifier itself, and that is read directly.
                    with admin.cursor() as cur:
                        cur.execute("select rolpassword from pg_authid where rolname=%s",
                                    (seat.login_role,))
                        adopted_verifier = cur.fetchone()
                    admin.rollback()
                    if not adopted_verifier or not adopted_verifier[0]:
                        raise RuntimeError("the adopted seat has no stored verifier")

                    # ---- THE LOST LOCAL FILE, IDENTICAL FROM OUTSIDE --------
                    # Same role, same absent credential file, and the one thing
                    # only the database can say: the seat is already
                    # credentialed. The decision must refuse, the mutator must
                    # refuse, and the working credential must still work.
                    with seat_owner.cursor() as cur:
                        observed = provision.role_is_passwordless(cur, seat.login_role)
                    seat_owner.rollback()
                    try:
                        provision.decide_profile_action(
                            role_exists_now=True, credential_state="absent",
                            role_created_by_migration=True, role_passwordless=observed,
                        )
                    except provision.ProvisioningRefusal as exc:
                        if "lost local file" not in str(exc):
                            raise RuntimeError(
                                f"the lost-file refusal does not name what happened: {exc}")
                    else:
                        raise RuntimeError(
                            "a credentialed seat with no local file was routed to adoption")
                    try:
                        provision.apply_login_profile(
                            seat_owner, seat, plans["gate_zero_producer"],
                            passwords["seat_rotation"], expected_creator=creator, adopt=True,
                        )
                    except provision.ProvisioningRefusal as exc:
                        if "already holds a password" not in str(exc):
                            raise RuntimeError(f"adoption refused for the wrong reason: {exc}")
                    else:
                        raise RuntimeError(
                            "adoption rotated a credential that was still in use")
                    provision.validate_profile_login(
                        seat_dsn, seat, plans["gate_zero_producer"], expected_creator=creator,
                    )
                    with admin.cursor() as cur:
                        cur.execute("select rolpassword from pg_authid where rolname=%s",
                                    (seat.login_role,))
                        if cur.fetchone() != adopted_verifier:
                            raise RuntimeError(
                                "the refused rotation changed the seat's stored verifier")
                    admin.rollback()
                finally:
                    seat_owner.close()
        except BaseException as exc:  # preserve maker failure if cleanup also fails
            failure = exc
        try:
            if not setup_committed:
                admin.rollback()
            else:
                admin.rollback()
                with admin.cursor() as cur:
                    cur.execute("drop role if exists app_writer")
                    cur.execute("drop role if exists app_reader")
                    for profile in profiles.values():
                        if profile.bundle_role is None:
                            # The seat is NOT dropped -- it belongs to the
                            # schema. It is returned to exactly what 0502 left:
                            # no password, no role-level settings, no edge to
                            # the owner that this gate invented.
                            cur.execute(sql.SQL("revoke {} from neondb_owner").format(
                                sql.Identifier(profile.grant_role)))
                            cur.execute(sql.SQL("alter role {} password null").format(
                                sql.Identifier(profile.login_role)))
                            cur.execute(sql.SQL(
                                "alter role {} reset statement_timeout"
                            ).format(sql.Identifier(profile.login_role)))
                            cur.execute(sql.SQL(
                                "alter role {} reset idle_in_transaction_session_timeout"
                            ).format(sql.Identifier(profile.login_role)))
                            continue
                        cur.execute(sql.SQL(
                            "revoke admin option for {} from neondb_owner"
                        ).format(sql.Identifier(profile.grant_role)))
                    if read_all_granted:
                        cur.execute("revoke pg_read_all_data from neondb_owner")
                    cur.execute("alter role neondb_owner nologin nocreaterole password null")
                admin.commit()
                with admin.cursor() as cur:
                    cur.execute(
                        "select count(*) from pg_roles where "
                        "rolname in ('app_reader','app_writer')"
                    )
                    if cur.fetchone() != (0,):
                        raise RuntimeError("disposable login roles were not removed")
                    cur.execute(
                        "select granted.rolname,m.admin_option,m.inherit_option,m.set_option,"
                        "grantor.oid::bigint from pg_auth_members m "
                        "join pg_roles granted on granted.oid=m.roleid "
                        "join pg_roles member on member.oid=m.member "
                        "join pg_roles grantor on grantor.oid=m.grantor "
                        "where member.rolname='neondb_owner' "
                        "and granted.rolname in ('carr_reader','carr_writer') order by 1"
                    )
                    if tuple(cur.fetchall()) != baseline_bundle_edges:
                        raise RuntimeError("neondb_owner fixture bundle edges were not restored")
                    cur.execute(
                        "select rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,"
                        "rolreplication,rolbypassrls "
                        "from pg_roles where rolname='neondb_owner'"
                    )
                    if cur.fetchone() != (False, False, False, False, False, False):
                        raise RuntimeError("neondb_owner fixture attributes were not restored")
                    cur.execute(
                        "select r.rolcanlogin,a.rolpassword is null,r.rolconfig is null "
                        "from pg_roles r join pg_authid a on a.oid=r.oid "
                        "where r.rolname=%s", (seat.login_role,)
                    )
                    if cur.fetchone() != seat_baseline:
                        raise RuntimeError(
                            f"{seat.login_role} was not restored to its migration state")
                    cur.execute(
                        "select count(*) from pg_auth_members m "
                        "join pg_roles granted on granted.oid=m.roleid "
                        "join pg_roles member on member.oid=m.member "
                        "where member.rolname='neondb_owner' and granted.rolname=%s",
                        (seat.login_role,),
                    )
                    if cur.fetchone() != (0,):
                        raise RuntimeError(
                            f"the gate left neondb_owner holding {seat.login_role}")
                    cur.execute(
                        "select pg_has_role('neondb_owner','pg_read_all_data','USAGE')")
                    if cur.fetchone() != (False,):
                        raise RuntimeError(
                            "the gate left neondb_owner holding pg_read_all_data")
            if fixture_mutated and not setup_committed:
                # All fixture writes were transactional and must have rolled back.
                with admin.cursor() as cur:
                    cur.execute(
                        "select count(*) from pg_roles where "
                        "rolname in ('app_reader','app_writer')"
                    )
                    if cur.fetchone() != (0,):
                        raise RuntimeError("rolled-back fixture unexpectedly created login roles")
                    cur.execute(
                        "select granted.rolname,m.admin_option,m.inherit_option,m.set_option,"
                        "grantor.oid::bigint from pg_auth_members m "
                        "join pg_roles granted on granted.oid=m.roleid "
                        "join pg_roles member on member.oid=m.member "
                        "join pg_roles grantor on grantor.oid=m.grantor "
                        "where member.rolname='neondb_owner' "
                        "and granted.rolname in ('carr_reader','carr_writer') order by 1"
                    )
                    if tuple(cur.fetchall()) != baseline_bundle_edges:
                        raise RuntimeError("rolled-back owner fixture bundle edges drifted")
                    cur.execute(
                        "select rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,"
                        "rolreplication,rolbypassrls "
                        "from pg_roles where rolname='neondb_owner'"
                    )
                    if cur.fetchone() != (False, False, False, False, False, False):
                        raise RuntimeError("rolled-back owner fixture attributes drifted")
        except BaseException as cleanup_exc:
            if failure is not None:
                failure.add_note(f"cleanup also failed: {cleanup_exc}")
            else:
                raise
        if failure is not None:
            raise failure
    print("PASS (DISPOSABLE LOOPBACK ONLY): SQL-created app_reader/app_writer authenticate with exact closed profiles; "
          "reader DML/DDL/sequence/role escalation is denied; "
          f"{seat.login_role} refuses adoption when pg_authid is unreadable, adopts its first "
          "password from the passwordless state 0502 leaves, authenticates with the exact "
          "canonical closure, refuses a second adoption once credentialed without touching its "
          "stored verifier, and is restored to its migration state")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"staging-database-login-provision-db-gate: FAIL — {exc}", file=sys.stderr)
        raise SystemExit(1)
