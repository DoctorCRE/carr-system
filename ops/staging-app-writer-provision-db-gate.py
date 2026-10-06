#!/usr/bin/env python3
# ci: db-gate
"""Read-only full-rebuild parity gate for canonical staging role bundles."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import pathlib
import sys

import psycopg


REPO = pathlib.Path(__file__).resolve().parents[1]
PROVISIONER = REPO / "tools/provision-staging-app-writer.py"


def load_provisioner():
    spec = importlib.util.spec_from_file_location("staging_bundle_parity_gate", PROVISIONER)
    if spec is None or spec.loader is None:
        raise RuntimeError(PROVISIONER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def rebuild_state(cur, provision) -> str:
    cur.execute("select filename,sha256 from public.schema_migrations")
    actual = dict(cur.fetchall())
    snapshot = provision.snapshot_grants.snapshot_applied_migrations(
        provision.SCHEMA.read_text(encoding="utf-8")
    )
    full = dict(snapshot)
    full.update(
        (path.name, hashlib.sha256(path.read_bytes()).hexdigest())
        for path in sorted(provision.MIGRATIONS.glob("*.sql"))
    )
    if actual == snapshot:
        return "snapshot-only"
    if actual == full:
        return "full-rebuild"
    raise RuntimeError("database ledger is neither exact snapshot-only nor full-rebuild")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is required")
    provision = load_provisioner()
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("begin transaction read only")
        state = rebuild_state(cur, provision)
        for profile in provision.PROFILES:
            # A DIRECT-GRANT PROFILE (bundle_role None) carries its canonical
            # ACLs on the LOGIN role itself, so the parity question is asked of
            # that role. On a snapshot-only database the seat role does not
            # exist yet -- its migration is pending -- and a plan with nothing to
            # compare it against is not a finding.
            if profile.bundle_role is None and state == "snapshot-only":
                continue
            if state == "snapshot-only":
                grants = provision.snapshot_grants.load_grants_to_role(
                    provision.SCHEMA, profile.grant_role
                )
            else:
                grants = provision.snapshot_grants.load_current_grants_to_role(
                    provision.SCHEMA, provision.MIGRATIONS, profile.grant_role
                )
            bundle = provision.collect_role_authority(cur, profile.grant_role)
            expected = set(provision.snapshot_grants.acl_facts(grants))
            if set(bundle.direct_acl_facts) != expected:
                raise RuntimeError(f"{profile.grant_role} differs from canonical {state} plan")
            if bundle.memberships or bundle.reachable_roles or bundle.owned_objects \
                    or bundle.powerful_attributes or not bundle.inherits_privileges:
                raise RuntimeError(f"{profile.grant_role} is not a closed capability role")
            if profile.bundle_role is not None and (bundle.can_login or bundle.role_config):
                raise RuntimeError(f"{profile.bundle_role} is not a closed NOLOGIN bundle")
            if profile.bundle_role is None and not bundle.can_login:
                raise RuntimeError(f"{profile.grant_role} is not the LOGIN seat it is declared as")
        conn.rollback()
    print(f"PASS: carr_reader/carr_writer exact canonical bundle parity ({state}, read-only)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"staging-app-writer-provision-db-gate: FAIL — {exc}", file=sys.stderr)
        raise SystemExit(1)
