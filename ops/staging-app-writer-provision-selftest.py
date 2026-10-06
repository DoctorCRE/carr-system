#!/usr/bin/env python3
"""Hermetic contract tests for the staging-only app_writer provisioner."""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import types
import uuid
from typing import Any


REPO = pathlib.Path(__file__).resolve().parents[1]
PROVISIONER = REPO / "tools" / "provision-staging-app-writer.py"
REHEARSAL = REPO / "ops" / "foundation-assurance-candidate-rehearsal.py"
CANDIDATE_OPERATION_ID = uuid.UUID("f870b3e2-f99a-4bf2-ba16-629d9725ba6d")
RECEIPT_ID = uuid.UUID("c4cddf05-03bd-4f9e-8691-b54dac7be8f4")
EXPECTED_SHA = "07d13398824dad987c40331ae7c2092db07b75d8"


def load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    provision = load_module("staging_app_writer_provision", PROVISIONER)
    rehearsal = load_module("foundation_assurance_candidate_rehearsal", REHEARSAL)
    snapshot = load_module("schema_snapshot_grants_for_test", REPO / "tools/schema_snapshot_grants.py")
    checked = 0

    def check(label: str, condition: bool) -> None:
        nonlocal checked
        checked += 1
        if not condition:
            raise AssertionError(label)
        print(f"  ok  {label}")

    check("prior rollback credentials have a first-class repair door",
          callable(getattr(provision, "repair_prior_rollback_credentials", None)))

    class SnapshotCursor:
        def __init__(self):
            self.rows = []
            self.statements = []

        def execute(self, statement, _params=None):
            self.statements.append(str(statement))
            if "session_user,current_user" in str(statement):
                self.rows = [("neondb_owner", "neondb_owner")]
            elif "jsonb_build_object" in str(statement):
                self.rows = [({"foundation_evidence": 0, "j1_clocks": 0},)]
            else:
                self.rows = []

        def fetchone(self):
            return self.rows.pop(0) if self.rows else None

    class SnapshotConnection:
        def __init__(self):
            self.cur = SnapshotCursor()
            self.rollbacks = 0
            self.closed = False

        def cursor(self):
            return self.cur

        def rollback(self):
            self.rollbacks += 1

        def close(self):
            self.closed = True

    snapshot_conn = SnapshotConnection()
    snapshot_result = rehearsal.staging_snapshot(
        "postgresql://app_reader:test@candidate.invalid/neondb?sslmode=require",
        connect=lambda _dsn: snapshot_conn,
    )
    check("candidate rehearsal snapshots through an explicit read-only owner transaction",
          snapshot_result == {"foundation_evidence": 0, "j1_clocks": 0}
          and snapshot_conn.cur.statements[0] == "begin transaction read only"
          and snapshot_conn.rollbacks == 1 and snapshot_conn.closed)

    candidate_binding_calls: list[tuple[Any, ...]] = []
    def fake_replacement_target(operation, receipt, sha):
        candidate_binding_calls.append(("target", operation, receipt, sha))
        return types.SimpleNamespace(candidate_operation_id="candidate-operation")

    def fake_replacement_binding(target, *, environ):
        candidate_binding_calls.append(("binding", target, environ is os.environ))
        return types.SimpleNamespace(
            candidate=types.SimpleNamespace(endpoint_host="candidate.example"),
            owner=types.SimpleNamespace(
                role_name="neondb_owner", endpoint="candidate.example",
                database="neondb", value="candidate-owner-dsn"))

    fake_provision = types.SimpleNamespace(
        replacement=types.SimpleNamespace(OWNER_ROLE="neondb_owner"),
        replacement_target=fake_replacement_target,
        resolve_replacement_binding=fake_replacement_binding,
    )
    bound_dsn = rehearsal.candidate_snapshot_dsn(
        fake_provision, "operation-id", "receipt-id", EXPECTED_SHA)
    check("candidate rehearsal binds its snapshot owner to the exact operation, receipt, source and endpoint",
          bound_dsn == "candidate-owner-dsn"
          and candidate_binding_calls[0]
              == ("target", "operation-id", "receipt-id", EXPECTED_SHA)
          and candidate_binding_calls[1][0] == "binding"
          and candidate_binding_calls[1][2] is True)

    requested_headers: dict[str, str | None] = {}
    class JsonResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"ok":true}'

    def fake_urlopen(request, *, timeout):
        requested_headers["user-agent"] = request.get_header("User-agent")
        requested_headers["accept"] = request.get_header("Accept")
        requested_headers["timeout"] = str(timeout)
        return JsonResponse()

    real_urlopen = rehearsal.urllib.request.urlopen
    rehearsal.urllib.request.urlopen = fake_urlopen
    try:
        request_result = rehearsal.request_json("https://staging.invalid/release")
    finally:
        rehearsal.urllib.request.urlopen = real_urlopen
    check("candidate rehearsal identifies its HTTP client instead of using urllib's blocked default",
          request_result == {"ok": True}
          and requested_headers == {
              "user-agent": rehearsal.HTTP_USER_AGENT,
              "accept": "application/json",
              "timeout": "30",
          })

    real_request_json = rehearsal.request_json
    rehearsal.request_json = lambda _url: {
        "git_sha": {"value": EXPECTED_SHA, "reason": None},
        "env": {"value": "staging", "reason": None},
        "worker_version": {
            "id": "7350ff40-e650-49f3-85a8-4b63e9fd3137",
            "tag": "carr-staging-test",
            "reason": None,
        },
    }
    try:
        release_result = rehearsal.release_readback("https://staging.invalid", EXPECTED_SHA)
    finally:
        rehearsal.request_json = real_request_json
    check("candidate rehearsal parses the live typed release identity envelope",
          release_result == {
              "git_sha": EXPECTED_SHA,
              "provider_version": "7350ff40-e650-49f3-85a8-4b63e9fd3137",
          })

    schema_text = (REPO / "db/schema.sql").read_text(encoding="utf-8")
    extracted = snapshot.grants_to_role(schema_text, "carr_writer")
    section = snapshot.carr_grants_section_lines(schema_text)
    raw_writer = [line for line in section if line.endswith(" to carr_writer;")]
    check("provisioning reuses every canonical carr_writer GRANT byte-for-byte",
          extracted == raw_writer and len(extracted) >= 170)
    check("the extracted ACL set names no second grantee",
          all(line.endswith(" to carr_writer;") for line in extracted))

    current_grants = snapshot.load_current_grants_to_role(
        REPO / "db/schema.sql", REPO / "migrations", "carr_writer"
    )
    current_facts = set(snapshot.acl_facts(current_grants))
    # Pending security migrations can intentionally revoke snapshot authority,
    # so cardinality is not a valid composition invariant. Pin representative
    # current grant and revoke outcomes here; the synthetic fixture below proves
    # that arbitrary GRANT/REVOKE operations compose in filename order.
    check("the current grant plan composes every post-snapshot migration",
          ("table", "ops.work_request", "insert", False) not in current_facts
          and ("table", "ops.work_request", "update", False) in current_facts
          and ("table", "public.lease", "insert", False) not in current_facts
          and ("table", "public.lease", "update", False) not in current_facts
          and ("function",
               "ops.capture_sourced_work_request(text, text, text, jsonb, uuid, uuid, uuid)",
               "execute", False) in current_facts)

    synthetic_applied = "begin; commit;\n"
    synthetic_pending = """begin;
do $$ begin
  grant execute on function ops.capture(text,uuid) to carr_writer;
end $$;
revoke insert on ops.work_request from carr_writer;
commit;
"""
    synthetic_later = """begin;
revoke all on function ops.capture(text,uuid) from carr_writer;
grant execute on function ops.capture_v2(text,uuid) to carr_writer;
commit;
"""
    synthetic_schema = f"""COPY public.schema_migrations (filename, sha256, applied_at) FROM stdin;
0001_base.sql\t{hashlib.sha256(synthetic_applied.encode()).hexdigest()}\t2026-08-16 00:00:00+00
\\.
{snapshot.SECTION_MARKER}
grant insert, select on table ops.work_request to carr_writer;
{snapshot.SECTION_END}
"""
    synthetic_plan = snapshot.compose_grants_to_role(
        synthetic_schema,
        (("0001_base.sql", synthetic_applied),
         ("0002_pending.sql", synthetic_pending),
         ("0003_later.sql", synthetic_later)),
        "carr_writer",
    )
    check("pending GRANT and REVOKE operations compose in migrate filename order",
          set(snapshot.acl_facts(synthetic_plan)) == {
              ("table", "ops.work_request", "select", False),
              ("function", "ops.capture_v2(text, uuid)", "execute", False),
          })
    # THE PENDING CAPABILITY BUNDLE, AND THE EXEMPTION THAT LETS IT COMPOSE
    # (2026-09-14, carr_gate_zero_producer). A migration that CREATES a role
    # names the role in a DO block and in a literal-returning function, neither
    # of which confers anything. Those occurrences are forgiven; the grants in
    # the same migration still compose.
    bundle_pending = """begin;
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'carr_writer') then
    create role carr_writer nologin;
  end if;
end $$;
create or replace function ops.bundle_name() returns text language sql immutable
as $fn$ select 'carr_writer'::text $fn$;
grant execute on function ops.bundle_name() to carr_writer;
commit;
"""
    bundle_plan = snapshot.compose_grants_to_role(
        synthetic_schema,
        (("0001_base.sql", synthetic_applied), ("0002_bundle.sql", bundle_pending)),
        "carr_writer",
    )
    check("a pending migration may create the bundle it grants to",
          set(snapshot.acl_facts(bundle_plan)) == {
              ("table", "ops.work_request", "insert", False),
              ("table", "ops.work_request", "select", False),
              ("function", "ops.bundle_name()", "execute", False),
          })
    # AND THE EXEMPTION IS NARROW, proved by its falsifier rather than described:
    # the same DO block carrying an authority verb the composer did not parse is
    # still a review stop. Without this case the exemption above would forgive
    # every unparsed mention inside a dollar-quoted body.
    hidden_authority = """begin;
do $$ begin
  alter role carr_writer set statement_timeout = '5s';
end $$;
grant select on table public.actor to carr_writer;
commit;
"""
    try:
        snapshot.compose_grants_to_role(
            synthetic_schema,
            (("0001_base.sql", synthetic_applied), ("0002_hidden.sql", hidden_authority)),
            "carr_writer",
        )
    except snapshot.SnapshotGrantError:
        check("an authority verb hidden in a dollar-quoted body is still a review stop", True)
    else:
        raise AssertionError("unparsed authority inside a dollar-quoted body was accepted")

    try:
        snapshot.compose_grants_to_role(
            synthetic_schema,
            (("0001_base.sql", synthetic_applied),
             ("0002_grantable.sql",
              "grant select on table public.actor to carr_writer with grant option;")),
            "carr_writer",
        )
    except snapshot.SnapshotGrantError:
        check("a pending migration cannot make grantable authority canonical", True)
    else:
        raise AssertionError("grantable pending authority was accepted")
    try:
        snapshot.grants_to_role(schema_text.replace(snapshot.SECTION_MARKER, "missing"), "carr_writer")
    except snapshot.SnapshotGrantError:
        check("a missing canonical grants boundary is refused", True)
    else:
        raise AssertionError("a missing canonical grants boundary was accepted")
    destructive = schema_text.replace(
        snapshot.SECTION_MARKER,
        snapshot.SECTION_MARKER
        + "\ngrant select on table public.actor to carr_writer; "
          "drop table public.actor; -- to carr_writer;",
        1,
    )
    try:
        snapshot.grants_to_role(destructive, "carr_writer")
    except snapshot.SnapshotGrantError:
        check("a disguised destructive multi-statement GRANT is refused", True)
    else:
        raise AssertionError("a disguised destructive multi-statement GRANT was accepted")

    for bad_env in (
        {"CARR_BREAK_GLASS": "1"},
        {"DATABASE_URL": "postgresql://ambient.invalid/db"},
        {"CARR_DB_OWNER_URL": "postgresql://ambient.invalid/db"},
        {"CARR_DB_WRITER_URL": "postgresql://ambient.invalid/db"},
        {"PGHOST": "ambient.invalid"},
    ):
        try:
            provision.reject_unsafe_environment(bad_env)
        except provision.ProvisioningRefusal:
            pass
        else:
            raise AssertionError(f"unsafe environment was accepted: {sorted(bad_env)}")
    check("break-glass and ambient database credentials are refused", True)

    good_projects = [{"id": "staging-project", "name": "carr-staging"}]
    good_branches = [{"id": "staging-main", "project_id": "staging-project",
                      "name": "main", "default": True}]
    endpoint_host = "ep-fixture.c-10.us-east-1.aws.neon.tech"
    good_endpoints = [{"id": "ep-fixture", "branch_id": "staging-main",
                       "type": "read_write", "host": endpoint_host}]
    context = provision.validate_provider_scope(good_projects, good_branches, good_endpoints)
    check("the one carr-staging default main branch is admitted",
          context.project_id == "staging-project" and context.branch_id == "staging-main"
          and context.endpoint_id == "ep-fixture" and context.endpoint_host == endpoint_host
          and context.port == 5432 and context.database == "neondb")
    refused_scopes: tuple[Any, ...] = (
        ([{"id": provision.PRODUCTION_PROJECT_ID, "name": "carr-staging"}], good_branches,
         good_endpoints),
        ([{"id": "other", "name": "other"}], good_branches, good_endpoints),
        (good_projects, [{"id": "wrong", "project_id": "staging-project",
                          "name": "develop", "default": True}], good_endpoints),
        (good_projects, [{"id": "staging-main", "project_id": "staging-project",
                          "name": "main", "default": False}], good_endpoints),
        (good_projects, [{"id": "staging-main", "project_id": "other-project",
                          "name": "main", "default": True}], good_endpoints),
        (good_projects, good_branches, []),
        (good_projects, good_branches,
         [{"id": "ep-fixture", "branch_id": "staging-main", "type": "read_write",
           "host": "wrong.neon.tech"}]),
    )
    for projects, branches, endpoints in refused_scopes:
        try:
            provision.validate_provider_scope(projects, branches, endpoints)
        except provision.ProvisioningRefusal:
            pass
        else:
            raise AssertionError("a non-staging project/branch scope was accepted")
    check("production, another project, and a non-default/non-main branch are refused", True)

    target = provision.replacement_target(
        str(CANDIDATE_OPERATION_ID), str(RECEIPT_ID), EXPECTED_SHA,
        run=lambda args, **_kwargs: subprocess.CompletedProcess(args, 0, "", ""),
    )
    check("replacement target binds full canonical UUIDv4 values and exact merged SHA",
          target.candidate_operation_id == CANDIDATE_OPERATION_ID
          and target.receipt_id == RECEIPT_ID and target.expected_sha == EXPECTED_SHA)
    for candidate, receipt, sha in (
        (str(CANDIDATE_OPERATION_ID)[:8], str(RECEIPT_ID), EXPECTED_SHA),
        (str(CANDIDATE_OPERATION_ID), str(RECEIPT_ID)[:8], EXPECTED_SHA),
        (str(uuid.uuid1()), str(RECEIPT_ID), EXPECTED_SHA),
        (str(CANDIDATE_OPERATION_ID), str(uuid.uuid1()), EXPECTED_SHA),
        (str(CANDIDATE_OPERATION_ID), str(RECEIPT_ID), EXPECTED_SHA[:12]),
        (str(CANDIDATE_OPERATION_ID), str(RECEIPT_ID), "A" * 40),
    ):
        try:
            provision.replacement_target(
                candidate, receipt, sha,
                run=lambda args, **_kwargs: subprocess.CompletedProcess(args, 0, "", ""),
            )
        except provision.ProvisioningRefusal:
            pass
        else:
            raise AssertionError("a partial/non-v4 replacement target was accepted")
    try:
        provision.replacement_target(
            str(CANDIDATE_OPERATION_ID), str(RECEIPT_ID), EXPECTED_SHA,
            run=lambda args, **_kwargs: subprocess.CompletedProcess(args, 1, "secret", "secret"),
        )
    except provision.ProvisioningRefusal as exc:
        check("unmerged source SHA refusal suppresses child output", "secret" not in str(exc))
    else:
        raise AssertionError("a source SHA outside origin/main was accepted")

    replacement = provision.replacement
    production = replacement.ProviderScope(
        replacement.PRODUCTION_PROJECT_ID, "production", "br-production", "ep-production",
        "ep-production.c-10.us-east-1.aws.neon.tech")
    old = replacement.ProviderScope(
        "old-staging-project", replacement.STAGING_NAME, "br-old", "ep-old",
        "ep-old.c-10.us-east-1.aws.neon.tech")
    candidate = replacement.ProviderScope(
        "candidate-project", replacement.candidate_name(CANDIDATE_OPERATION_ID),
        "br-candidate", "ep-candidate", "ep-candidate.c-10.us-east-1.aws.neon.tech")
    fixture_migration = replacement.CONTRACT_MIGRATION
    fixture_migration_sha = "1" * 64
    fixture_ledger = {fixture_migration: fixture_migration_sha}
    fixture_ledger_material = f"{fixture_migration}\0{fixture_migration_sha}\n"
    source_manifest = {
        "git_sha": EXPECTED_SHA, "source_tree_oid": "2" * 40,
        "source_tree_sha256": "sha256:" + "3" * 64,
        "source_tree_entry_count": 123, "artifact_sha256": "sha256:" + "4" * 64,
        "config_sha256": "sha256:" + "5" * 64,
        "dependency_sha256": "sha256:" + "6" * 64,
        "migration_ledger": fixture_ledger, "migration_count": 1,
        "migration_highest": fixture_migration,
        "migration_ledger_sha256": "sha256:" + hashlib.sha256(
            fixture_ledger_material.encode()).hexdigest(),
    }
    exact_receipt = {
        "contract_id": str(uuid.UUID("11111111-2222-4333-8444-555555555555")),
        "receipt_id": str(RECEIPT_ID),
        "evidence_ref": "ops.staging-replacement-project:sha256:" + "7" * 64,
        "receipt_sha256": "sha256:" + "a" * 64,
        "git_sha": EXPECTED_SHA, "source_tree_oid": source_manifest["source_tree_oid"],
        "source_tree_sha256": source_manifest["source_tree_sha256"],
        "source_tree_entry_count": source_manifest["source_tree_entry_count"],
        "artifact_sha256": source_manifest["artifact_sha256"],
        "config_sha256": source_manifest["config_sha256"],
        "dependency_sha256": source_manifest["dependency_sha256"],
        "prior_staging_project_id": old.project_id,
        "replacement_project_id": candidate.project_id,
        "replacement_branch_id": candidate.branch_id,
        "replacement_endpoint_id": candidate.endpoint_id,
        "live_migration_ledger": fixture_ledger,
        "live_migration_count": 1, "live_migration_highest": fixture_migration,
        "live_migration_ledger_sha256": source_manifest["migration_ledger_sha256"],
        "synthetic_data_count": 5,
        "production_overlap_count": 0,
        "observed_at": "2026-08-26T00:00:00Z",
    }
    provision.validate_replacement_receipt(
        target, production, old, candidate, exact_receipt, source_manifest)
    for field, bad in (
        ("receipt_id", str(uuid.uuid4())), ("git_sha", "b" * 40),
        ("prior_staging_project_id", production.project_id),
        ("replacement_project_id", old.project_id),
        ("replacement_branch_id", old.branch_id),
        ("replacement_endpoint_id", old.endpoint_id),
        ("production_overlap_count", 1), ("receipt_sha256", "sha256:short"),
    ):
        changed = dict(exact_receipt); changed[field] = bad
        try:
            provision.validate_replacement_receipt(
                target, production, old, candidate, changed, source_manifest)
        except provision.ProvisioningRefusal:
            pass
        else:
            raise AssertionError(f"replacement receipt mismatch was accepted: {field}")
    wrong_production = dataclasses.replace(production, project_id="not-production")
    try:
        provision.validate_replacement_receipt(
            target, wrong_production, old, candidate, exact_receipt, source_manifest)
    except provision.ProvisioningRefusal:
        check("receipt binding refuses wrong Production identity", True)
    else:
        raise AssertionError("wrong Production identity was accepted")
    partial_receipt = dict(exact_receipt); partial_receipt.pop("live_migration_ledger")
    try:
        provision.validate_replacement_receipt(
            target, production, old, candidate, partial_receipt, source_manifest)
    except provision.ProvisioningRefusal:
        check("partial immutable receipt projection is refused", True)
    else:
        raise AssertionError("partial immutable receipt projection was accepted")
    drifted_source = dict(source_manifest)
    drifted_source["artifact_sha256"] = "sha256:" + "9" * 64
    try:
        provision.validate_replacement_receipt(
            target, production, old, candidate, exact_receipt, drifted_source)
    except provision.ProvisioningRefusal:
        check("receipt source-tree/schema projection must match merged source contract", True)
    else:
        raise AssertionError("receipt disagreed with merged source contract")

    candidate_root = provision.replacement_credential_root(CANDIDATE_OPERATION_ID)
    candidate_profiles = {
        label: provision.credential.profile(label, config_root=candidate_root)
        for label in tuple(one.label for one in provision.PROFILES)
    }
    canonical_profiles = {
        label: provision.credential.profile(label)
        for label in tuple(one.label for one in provision.PROFILES)
    }
    check("candidate app credentials live under the candidate operation private root",
          all(profile.paths.final.parent == candidate_root
              for profile in candidate_profiles.values()))
    check("candidate app credential paths never overwrite canonical old staging files",
          all(candidate_profiles[label].paths.final != canonical_profiles[label].paths.final
              for label in candidate_profiles))
    saved_scope_resolver = provision.replacement.resolve_existing_scopes
    try:
        provision.replacement.resolve_existing_scopes = lambda *_args, **_kwargs: (
            production, old, None)
        try:
            provision.resolve_replacement_binding(
                target, run=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    AssertionError("source/credential fallback should not run")),
                environ={}, connect=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    AssertionError("candidate DB fallback should not run")))
        except provision.ProvisioningRefusal:
            check("missing exact candidate refuses before any old-target fallback", True)
        else:
            raise AssertionError("missing candidate fell back to old staging")
    finally:
        provision.replacement.resolve_existing_scopes = saved_scope_resolver

    scope = provision.ProviderScope(
        "staging-project", "staging-main", "ep-fixture", endpoint_host, 5432, "neondb"
    )
    owner = provision.ScopedDsn(
        scope, "neondb_owner", endpoint_host, 5432, "neondb",
        f"postgresql://neondb_owner:owner-secret@{endpoint_host}/neondb?sslmode=require",  # ci-secret-scan: allow — hermetic non-routable fixture
    )
    writer = provision.ScopedDsn(
        scope, "app_writer", endpoint_host, 5432, "neondb",
        f"postgresql://app_writer:writer-secret@{endpoint_host}/neondb?sslmode=require",  # ci-secret-scan: allow — hermetic non-routable fixture
    )
    provision.validate_connection_scope(owner, writer)
    bad_writers = (
        provision.ScopedDsn(scope, "app_writer", "other.example", 5432, "neondb", writer.value),
        provision.ScopedDsn(scope, "app_writer", endpoint_host, 6432, "neondb", writer.value),
        provision.ScopedDsn(scope, "app_writer", endpoint_host, 5432, "other", writer.value),
        provision.ScopedDsn(provision.ProviderScope(
            "rebuilt", "staging-main", "ep-fixture", endpoint_host, 5432, "neondb"),
            "app_writer", endpoint_host, 5432, "neondb", writer.value),
    )
    for bad_writer in bad_writers:
        try:
            provision.validate_connection_scope(owner, bad_writer)
        except provision.ProvisioningRefusal:
            pass
        else:
            raise AssertionError("a mismatched app_writer DSN was accepted")
    check("owner/app_writer must share immutable scope, endpoint, port and database", True)

    class DsnRunner:
        def __init__(self, query: str = "sslmode=require&channel_binding=require"):
            self.calls: list[list[str]] = []
            self.query = query

        def __call__(self, args, **kwargs):
            self.calls.append(list(args))
            role = args[args.index("--role-name") + 1]
            return subprocess.CompletedProcess(
                args, 0,
                f"postgresql://{role}:fixture@{endpoint_host}:5432/neondb?{self.query}",  # ci-secret-scan: allow — hermetic non-routable fixture
                "",
            )

    dsn_runner = DsnRunner()
    scoped_owner = provision.provider_dsn(
        scope, "neondb_owner", neonctl="neonctl", run=dsn_runner, environ={},
    )
    check("only owner DSN uses exact immutable project/branch ids and normalized endpoint",
          dsn_runner.calls[0][2] == scope.branch_id
          and dsn_runner.calls[0][dsn_runner.calls[0].index("--project-id") + 1] == scope.project_id
          and scoped_owner.endpoint == endpoint_host
          and scoped_owner.port == 5432)
    unsafe_queries = (
        "sslmode=require&channel_binding=require&host=elsewhere",
        "sslmode=require&channel_binding=require&hostaddr=192.0.2.1",
        "sslmode=require&channel_binding=require&port=5433",
        "sslmode=require&channel_binding=require&dbname=postgres",
        "sslmode=require&channel_binding=require&user=app_writer",
        "sslmode=require&channel_binding=require&service=staging",
        "sslmode=require&channel_binding=require&options=-csearch_path%3Dpublic",
        "sslmode=require&sslmode=require&channel_binding=require",
        "sslmode=require", "channel_binding=require",
        "sslmode=verify-full&channel_binding=require",
    )
    for unsafe_query in unsafe_queries:
        try:
            provision.provider_dsn(
                scope, "neondb_owner", neonctl="neonctl",
                run=DsnRunner(unsafe_query), environ={},
            )
        except provision.ProvisioningRefusal:
            pass
        else:
            raise AssertionError(f"unsafe provider DSN query accepted: {unsafe_query}")
    check("provider DSN refuses query overrides, duplicates, missing keys and wrong values", True)
    try:
        provision.provider_dsn(
            scope, "app_writer", neonctl="neonctl", run=dsn_runner, environ={},
        )
    except provision.ProvisioningRefusal:
        check("provider app_writer connection-string reveal is removed", True)
    else:
        raise AssertionError("provider app_writer DSN path remains reachable")
    source = PROVISIONER.read_text(encoding="utf-8")
    check("official bare create response is non-authority and provider create is unused",
          '"roles", "create"' not in source and "provider_role_created" not in source)

    class WorkerRunner:
        def __init__(self):
            self.calls: list[tuple[list[str], dict[str, Any]]] = []
        def __call__(self, args, **kwargs):
            self.calls.append((list(args), kwargs))
            if "bulk" in args:
                return subprocess.CompletedProcess(args, 0, "bulk complete", "")
            return subprocess.CompletedProcess(
                args, 0, json.dumps(
                    [{"name": "CARR_MCP_TOKEN", "type": "secret_text"}]
                    + [{"name": name, "type": "secret_text"}
                       for name in provision.WORKER_DATABASE_SECRET_NAMES]
                ), ""
            )

    worker_runner = WorkerRunner()
    future_values = {name: f"future-secret-for-{name}"
                     for name in provision.WORKER_DATABASE_SECRET_NAMES}
    provision.bulk_worker_database_secrets(
        future_values, wrangler="wrangler", run=worker_runner,
        environ={"PATH": "/safe/bin", "HOME": "/safe/home",
                 "CLOUDFLARE_API_TOKEN": "cloudflare-token",
                 "UNSAFE_CHILD_SECRET": "must-not-travel"},
    )
    provision.verify_worker_database_secret_bindings(
        wrangler="wrangler", run=worker_runner,
        environ={"PATH": "/safe/bin", "HOME": "/safe/home",
                 "CLOUDFLARE_API_TOKEN": "cloudflare-token",
                 "UNSAFE_CHILD_SECRET": "must-not-travel"},
    )
    check("Worker publishes both database secrets in one stdin JSON bulk request",
          worker_runner.calls[0][0] == [
              "wrangler", "secret", "bulk", "--env", "staging",
              "--config", str(provision.WRANGLER_CONFIG),
          ]
          and json.loads(worker_runner.calls[0][1]["input"]) == future_values
          and sum("bulk" in call[0] for call in worker_runner.calls) == 1
          and all("put" not in call[0] for call in worker_runner.calls)
          and worker_runner.calls[1][0] == [
              "wrangler", "secret", "list", "--env", "staging",
              "--config", str(provision.WRANGLER_CONFIG), "--format", "json",
          ])

    class LegacyWorkerRunner:
        def __init__(self):
            self.calls: list[tuple[list[str], dict[str, Any]]] = []
        def __call__(self, args, **kwargs):
            self.calls.append((list(args), kwargs))
            if "bulk" in args:
                return subprocess.CompletedProcess(args, 0, "bulk complete", "")
            return subprocess.CompletedProcess(args, 0, json.dumps([
                {"name": "DATABASE_URL_READER", "type": "secret_text"},
                {"name": "DATABASE_URL_WRITER", "type": "secret_text"},
            ]), "")

    legacy_runner = LegacyWorkerRunner()
    legacy_values = dict(future_values)
    legacy_values["DATABASE_URL_GATE_ZERO_WRITER"] = None
    legacy_values["DATABASE_URL_FOUNDATION_ASSURANCE_WRITER"] = None
    provision.bulk_worker_database_secrets(
        legacy_values, wrangler="wrangler", run=legacy_runner, environ={})
    provision.verify_worker_database_secret_bindings(
        expected_names={"DATABASE_URL_READER", "DATABASE_URL_WRITER"},
        wrangler="wrangler", run=legacy_runner, environ={})
    check("legacy rollback atomically deletes migration-created secrets with JSON null",
          json.loads(legacy_runner.calls[0][1]["input"]) == legacy_values)

    saved_profile = provision.credential.profile
    saved_load_existing = provision.credential.load_existing
    saved_secret_names = provision.read_worker_database_secret_names
    try:
        provision.read_worker_database_secret_names = lambda: {
            "DATABASE_URL_READER", "DATABASE_URL_WRITER"}
        def load_legacy_credential(_paths, *, role_name, **_kwargs):
            if role_name in {
                provision.GATE_ZERO_PRODUCER_ROLE,
                provision.FOUNDATION_ASSURANCE_ORACLE_ROLE,
            }:
                raise provision.credential.CredentialRefusal("staging credential is absent")
            return provision.credential.StoredCredential(
                "final", pathlib.Path("/fixture/final"), f"dsn-{role_name}",
                "fixture-password", "legacy.example", 5432, "neondb")
        provision.credential.load_existing = load_legacy_credential
        rollback_values = provision.load_rollback_worker_values(
            type("OldScope", (), {"endpoint_host": "legacy.example"})())
    finally:
        provision.credential.profile = saved_profile
        provision.credential.load_existing = saved_load_existing
        provision.read_worker_database_secret_names = saved_secret_names
    check("legacy rollback derives absence only for migration-created seats",
          rollback_values == {
              "DATABASE_URL_READER": "dsn-app_reader",
              "DATABASE_URL_WRITER": "dsn-app_writer",
              "DATABASE_URL_GATE_ZERO_WRITER": None,
              "DATABASE_URL_FOUNDATION_ASSURANCE_WRITER": None,
          })
    check("Worker child environment is an exact allowlist with pinned account",
          all(set(call[1]["env"]) == {
              "PATH", "HOME", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"
          } for call in worker_runner.calls)
          and all(call[1]["env"]["CLOUDFLARE_ACCOUNT_ID"]
                  == provision.CLOUDFLARE_ACCOUNT_ID for call in worker_runner.calls)
          and all("UNSAFE_CHILD_SECRET" not in call[1]["env"] for call in worker_runner.calls))
    serialized = worker_runner.calls[0][1]["input"]
    check("secret stdin JSON never reaches argv, stdout or stderr",
          all(secret not in json.dumps(worker_runner.calls[0][0])
              and secret not in worker_runner.calls[0][1].get("stdout", "")
              and secret not in worker_runner.calls[0][1].get("stderr", "")
              for secret in future_values.values())
          and all(secret in serialized for secret in future_values.values()))
    source = PROVISIONER.read_text(encoding="utf-8")
    # THE CUTOVER MOVES A MATCHED SET, so a sequential `secret put` is forbidden
    # outright: three DSNs published one at a time can leave the Worker serving
    # half of one project and half of another. There is exactly one publication
    # door in this tool and it is the atomic bulk.
    check("no sequential Worker secret put exists anywhere in this tool",
          '"secret", "put"' not in source
          and "put_worker_database_secret" not in source
          and source.count('"secret", "bulk"') == 1)

    class SecretFailureRunner:
        def __call__(self, args, **_kwargs):
            raise subprocess.TimeoutExpired(
                args, 60, output="future-reader-secret", stderr="future-writer-secret")
    try:
        provision.bulk_worker_database_secrets(
            future_values, wrangler="wrangler", run=SecretFailureRunner(), environ={})
    except provision.ProvisioningRefusal as exc:
        check("bulk timeout suppresses every secret and child output",
              all(secret not in str(exc) for secret in future_values.values()))
    else:
        raise AssertionError("bulk timeout escaped refusal boundary")

    class WrongBindingRunner:
        def __call__(self, args, **_kwargs):
            return subprocess.CompletedProcess(
                args, 0, json.dumps([
                    {"name": "DATABASE_URL_READER", "type": "secret_text"},
                    {"name": "DATABASE_URL_OLD", "type": "secret_text"},
                ]), "")
    try:
        provision.verify_worker_database_secret_bindings(
            wrangler="wrangler", run=WrongBindingRunner(), environ={})
    except provision.ProvisioningRefusal:
        check("Worker readback requires the exact declared DATABASE_URL name set", True)
    else:
        raise AssertionError("wrong Worker database secret name list was accepted")

    rollback_events: list[tuple[str, Any]] = []
    old_values = {name: f"old-secret-for-{name}"
                  for name in provision.WORKER_DATABASE_SECRET_NAMES}
    preservation_calls = 0
    def preservation() -> None:
        nonlocal preservation_calls
        preservation_calls += 1
        rollback_events.append(("preserve", preservation_calls))
        if preservation_calls == 2:
            raise provision.ProvisioningRefusal("provider identity changed")
    def bulk(values) -> None:
        rollback_events.append(("bulk", dict(values)))
    def verify() -> None:
        rollback_events.append(("verify", None))
    try:
        provision.publish_worker_cutover(
            future_values, old_values, preserve=preservation,
            bulk=bulk, verify=verify)
    except provision.ProvisioningRefusal:
        pass
    else:
        raise AssertionError("post-publish provider drift was accepted")
    check("post-publish refusal atomically restores both old Worker secrets",
          rollback_events == [
              ("preserve", 1), ("bulk", future_values), ("verify", None),
              ("preserve", 2), ("bulk", old_values), ("verify", None),
              ("preserve", 3),
          ])
    first_bulk_events: list[tuple[str, Any]] = []
    first_bulk_calls = 0
    def fail_first_bulk(values) -> None:
        nonlocal first_bulk_calls
        first_bulk_calls += 1
        first_bulk_events.append(("bulk", dict(values)))
        if first_bulk_calls == 1:
            raise provision.ProvisioningRefusal("uncertain candidate bulk")
    try:
        provision.publish_worker_cutover(
            future_values, old_values,
            preserve=lambda: first_bulk_events.append(("preserve", None)),
            bulk=fail_first_bulk,
            verify=lambda: first_bulk_events.append(("verify", None)))
    except provision.ProvisioningRefusal:
        pass
    else:
        raise AssertionError("initial bulk failure escaped rollback")
    check("initial candidate bulk uncertainty also restores the complete old pair",
          first_bulk_events == [
              ("preserve", None), ("bulk", future_values),
              ("bulk", old_values), ("verify", None), ("preserve", None),
          ])
    uncertain_events: list[tuple[str, Any]] = []
    uncertain_preserve_calls = 0
    injected_secret = "provider-post-restore-secret-detail"
    def uncertain_preservation() -> None:
        nonlocal uncertain_preserve_calls
        uncertain_preserve_calls += 1
        uncertain_events.append(("preserve", uncertain_preserve_calls))
        if uncertain_preserve_calls == 3:
            raise provision.ProvisioningRefusal(
                f"provider post-restore failed: {injected_secret}")
    def uncertain_bulk(values) -> None:
        uncertain_events.append(("bulk", dict(values)))
    def uncertain_verify() -> None:
        uncertain_events.append(("verify", None))
    try:
        provision.publish_worker_cutover(
            future_values, old_values, preserve=uncertain_preservation,
            bulk=uncertain_bulk, verify=uncertain_verify,
            postflight=lambda: (_ for _ in ()).throw(
                provision.ProvisioningRefusal("candidate postflight refused")),
        )
    except provision.ProvisioningRefusal as exc:
        check("post-restore provider refusal occurs only after old bulk and name verification",
              uncertain_events == [
                  ("preserve", 1), ("bulk", future_values), ("verify", None),
                  ("preserve", 2), ("bulk", old_values), ("verify", None),
                  ("preserve", 3),
              ])
        check("post-restore provider refusal reports fixed redacted uncertain outcome",
              str(exc) == (
                  "Worker credential cutover refused and rollback outcome is uncertain; "
                  "output suppressed")
              and injected_secret not in str(exc)
              and "provider post-restore failed" not in str(exc))
    else:
        raise AssertionError("post-restore provider uncertainty was accepted")
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            provision.parse_args([
                "--candidate-operation-id", str(CANDIDATE_OPERATION_ID),
                "--receipt-id", str(RECEIPT_ID), "--sha", EXPECTED_SHA,
                "--rollback-to-prior-staging",
            ])
    except SystemExit as exc:
        check("explicit prior-staging rollback requires the apply gate", exc.code == 2)
    else:
        raise AssertionError("rollback mode was accepted without --apply")

    candidate_scope = provision.ProviderScope(
        candidate.project_id, candidate.branch_id, candidate.endpoint_id,
        candidate.endpoint_host, 5432, "neondb")
    candidate_owner = provision.ScopedDsn(
        candidate_scope, "neondb_owner", candidate.endpoint_host, 5432, "neondb",
        f"postgresql://neondb_owner:candidate-owner-secret@{candidate.endpoint_host}/neondb?sslmode=require",  # ci-secret-scan: allow — hermetic non-routable fixture
    )
    binding_fixture = provision.ReplacementBinding(
        target, production, old, candidate, source_manifest, exact_receipt, candidate_owner)
    valid_state = provision.SeedState(
        (("approved", 60), ("pending", 2)), 3, 4)
    worker_lock = provision.worker_cutover_lock_path()
    active_locks: set[pathlib.Path] = set()
    lock_events: list[tuple[str, pathlib.Path]] = []

    @contextlib.contextmanager
    def exclusive_lock(path: pathlib.Path):
        if path in active_locks:
            raise provision.credential.CredentialRefusal("fixture lock is already held")
        active_locks.add(path)
        lock_events.append(("enter", path))
        try:
            yield
        finally:
            lock_events.append(("exit", path))
            active_locks.remove(path)

    try:
        with exclusive_lock(worker_lock):
            with exclusive_lock(worker_lock):
                raise AssertionError("overlapping Worker cutover lock was admitted")
    except provision.credential.CredentialRefusal:
        check("one global Worker lock refuses an overlapping candidate cutover", True)
    check("global Worker lock is outside every candidate credential root",
          worker_lock.parent == pathlib.Path.home() / ".config/carr"
          and worker_lock != candidate_root / ".staging-role-operation.lock")

    class FakeOwnerCursor:
        # ``seat_passwordless`` is what pg_authid answers for the migrated Gate
        # Zero seat. True is a fresh database, where 0502 has created the role
        # and nothing has credentialed it; False is the same local state after
        # the credential file was lost, which must never adopt.
        seat_passwordless = True

        def __init__(self):
            self.statements: list[str] = []
            self._rows: list[tuple[Any, ...]] = []

        def execute(self, statement, _params=None) -> None:
            self.statements.append(str(statement))
            self._rows = (
                [(type(self).seat_passwordless,)]
                if "pg_authid" in str(statement) else []
            )

        def fetchone(self):
            return (True,)

        def fetchall(self):
            return list(self._rows)

    class FakeOwner:
        def __init__(self):
            self.autocommit = False
            self.cur = FakeOwnerCursor()
            self.closed = False

        def cursor(self):
            return self.cur

        def commit(self) -> None:
            pass

        def rollback(self) -> None:
            pass

        def execute(self, statement, _params=None) -> None:
            self.cur.execute(statement, _params)

        def close(self) -> None:
            self.closed = True

    repair_events: list[tuple[str, str]] = []
    repair_owner = FakeOwner()
    repair_saved = {
        "derive_dsn": provision.replacement.derive_dsn,
        "profile": provision.credential.profile,
        "load_existing": provision.credential.load_existing,
        "prepare_pending": provision.credential.prepare_pending,
        "promote_pending": provision.credential.promote_pending,
        "exclusive_lock": provision.credential.exclusive_lock,
        "require_owner": provision.require_direct_owner_identity,
        "apply": provision.apply_login_profile,
        "validate": provision.validate_profile_login,
    }
    repair_root = pathlib.Path("/fixture/prior-rollback")
    repair_profiles = {
        profile.label: types.SimpleNamespace(
            key=f"KEY_{profile.label}", role_name=profile.login_role,
            paths=provision.credential.CredentialPaths(
                repair_root / f"{profile.label}.env",
                repair_root / f"{profile.label}.env.pending"),
        ) for profile in provision.PROFILES
    }
    try:
        provision.replacement.derive_dsn = lambda *_args, **_kwargs: \
            types.SimpleNamespace(value="prior-owner-dsn")
        provision.credential.profile = lambda label, **_kwargs: repair_profiles[label]
        def repair_load(_paths, *, role_name, expected_endpoint, **_kwargs):
            profile = next(row for row in provision.PROFILES if row.login_role == role_name)
            if profile.created_by_migration:
                raise provision.credential.CredentialRefusal("staging credential is absent")
            return provision.credential.StoredCredential(
                "final", repair_root / f"{profile.label}.env",
                f"dsn-{profile.label}", "fixture-password", expected_endpoint, 5432, "neondb")
        provision.credential.load_existing = repair_load
        def repair_prepare(_paths, *, role_name, expected_endpoint, **_kwargs):
            profile = next(row for row in provision.PROFILES if row.login_role == role_name)
            repair_events.append(("prepare", profile.label))
            return provision.credential.StoredCredential(
                "pending", repair_root / f"{profile.label}.env.pending",
                f"dsn-{profile.label}", "fixture-password", expected_endpoint, 5432, "neondb")
        provision.credential.prepare_pending = repair_prepare
        provision.credential.promote_pending = lambda _paths, *, key, **_kwargs: \
            repair_events.append(("promote", key.removeprefix("KEY_")))
        provision.credential.exclusive_lock = exclusive_lock
        provision.require_direct_owner_identity = lambda _cur: "neondb_owner"
        provision.apply_login_profile = lambda _owner, profile, *_args, **_kwargs: \
            repair_events.append(("adopt", profile.label))
        provision.validate_profile_login = lambda _dsn, profile, *_args, **_kwargs: \
            repair_events.append(("validate", profile.label))
        repaired = provision.repair_prior_rollback_credentials(
            old, {profile.label: () for profile in provision.PROFILES},
            connect=lambda _dsn: repair_owner)
    finally:
        provision.replacement.derive_dsn = repair_saved["derive_dsn"]
        provision.credential.profile = repair_saved["profile"]
        provision.credential.load_existing = repair_saved["load_existing"]
        provision.credential.prepare_pending = repair_saved["prepare_pending"]
        provision.credential.promote_pending = repair_saved["promote_pending"]
        provision.credential.exclusive_lock = repair_saved["exclusive_lock"]
        provision.require_direct_owner_identity = repair_saved["require_owner"]
        provision.apply_login_profile = repair_saved["apply"]
        provision.validate_profile_login = repair_saved["validate"]
    check("prior rollback repair adopts only missing migration-created seats",
          repaired == {
              "reader": "reused", "writer": "reused",
              "gate_zero_producer": "adopted",
              "foundation_assurance_oracle": "adopted",
          }
          and repair_events == [
              ("validate", "reader"), ("validate", "writer"),
              ("prepare", "gate_zero_producer"), ("adopt", "gate_zero_producer"),
              ("validate", "gate_zero_producer"), ("promote", "gate_zero_producer"),
              ("prepare", "foundation_assurance_oracle"),
              ("adopt", "foundation_assurance_oracle"),
              ("validate", "foundation_assurance_oracle"),
              ("promote", "foundation_assurance_oracle"),
          ] and repair_owner.closed)

    orchestration_events: list[tuple[str, Any]] = []
    profile_roots: list[pathlib.Path | None] = []
    preserve_calls = 0
    unlocked_preserve_budget = 0
    reader_candidate = (
        f"postgresql://app_reader:candidate-reader-secret@{candidate.endpoint_host}/neondb?sslmode=require"  # ci-secret-scan: allow — hermetic non-routable fixture
    )
    writer_candidate = (
        f"postgresql://app_writer:candidate-writer-secret@{candidate.endpoint_host}/neondb?sslmode=require"  # ci-secret-scan: allow — hermetic non-routable fixture
    )
    # THE GATE ZERO PRODUCER SEAT'S OWN CREDENTIAL (standing-rule amendment 9,
    # 2026-09-14). It is a third profile rather than a reuse of the writer's,
    # because the record layer admits exactly one login role for that write and
    # revokes it from carr_writer.
    gate_zero_candidate = (
        f"postgresql://carr_gate_zero_producer:candidate-gate-zero-secret@{candidate.endpoint_host}/neondb?sslmode=require"  # ci-secret-scan: allow — hermetic non-routable fixture
    )
    foundation_candidate = (
        f"postgresql://carr_foundation_assurance_oracle:candidate-foundation-secret@{candidate.endpoint_host}/neondb?sslmode=require"  # ci-secret-scan: allow — hermetic non-routable fixture
    )
    candidate_by_role = {
        provision.READER_ROLE: reader_candidate,
        provision.APP_ROLE: writer_candidate,
        provision.GATE_ZERO_PRODUCER_ROLE: gate_zero_candidate,
        provision.FOUNDATION_ASSURANCE_ORACLE_ROLE: foundation_candidate,
    }
    expected_candidate_values = {
        "DATABASE_URL_READER": reader_candidate,
        "DATABASE_URL_WRITER": writer_candidate,
        "DATABASE_URL_GATE_ZERO_WRITER": gate_zero_candidate,
        "DATABASE_URL_FOUNDATION_ASSURANCE_WRITER": foundation_candidate,
    }
    assert set(expected_candidate_values) == set(provision.WORKER_DATABASE_SECRET_NAMES), (
        "this suite and the tool disagree about which Worker database secrets exist")
    saved_main_dependencies = {
        "reject_unsafe_environment": provision.reject_unsafe_environment,
        "replacement_target": provision.replacement_target,
        "resolve_replacement_binding": provision.resolve_replacement_binding,
        "load_rollback_worker_values": provision.load_rollback_worker_values,
        "bulk_worker_database_secrets": provision.bulk_worker_database_secrets,
        "verify_worker_database_secret_bindings": provision.verify_worker_database_secret_bindings,
        "read_seed_state": provision.read_seed_state,
        "connect": provision.psycopg.connect,
        "prove_provider_preservation": provision.replacement.prove_provider_preservation,
        "load_grants": provision.snapshot_grants.load_current_grants_to_role,
        "exclusive_lock": provision.credential.exclusive_lock,
        "credential_profile": provision.credential.profile,
        "load_existing": provision.credential.load_existing,
        "validate_profile_login": provision.validate_profile_login,
        "require_direct_owner_identity": provision.require_direct_owner_identity,
    }
    try:
        provision.reject_unsafe_environment = lambda _environment: None
        provision.replacement_target = lambda *_args, **_kwargs: target
        provision.resolve_replacement_binding = lambda *_args, **_kwargs: binding_fixture
        provision.load_rollback_worker_values = lambda _old: dict(old_values)
        provision.snapshot_grants.load_current_grants_to_role = lambda *_args, **_kwargs: []
        provision.credential.exclusive_lock = exclusive_lock

        # The default plan proves the full candidate binding but must stop before
        # credential, role, provider, or Worker mutations.
        provision.read_seed_state = lambda *_args, **_kwargs: valid_state
        provision.replacement.prove_provider_preservation = lambda *_args, **_kwargs: \
            (_ for _ in ()).throw(AssertionError("dry-run mutated/read provider scopes"))
        provision.load_rollback_worker_values = lambda *_args, **_kwargs: \
            (_ for _ in ()).throw(AssertionError("dry-run loaded secret credentials"))
        provision.credential.profile = lambda *_args, **_kwargs: \
            (_ for _ in ()).throw(AssertionError("dry-run opened candidate credential state"))
        provision.bulk_worker_database_secrets = lambda *_args, **_kwargs: \
            (_ for _ in ()).throw(AssertionError("dry-run mutated Worker secrets"))
        provision.psycopg.connect = lambda *_args, **_kwargs: \
            (_ for _ in ()).throw(AssertionError("dry-run opened candidate owner DB"))

        # An unreadable initial snapshot must refuse before any provider,
        # credential, role, or Worker mutation path becomes callable.
        provision.read_seed_state = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            provision.ProvisioningRefusal("synthetic initial read failure"))
        initial_stdout = io.StringIO()
        initial_stderr = io.StringIO()
        with contextlib.redirect_stdout(initial_stdout), contextlib.redirect_stderr(initial_stderr):
            initial_rc = provision.main([
                "--candidate-operation-id", str(CANDIDATE_OPERATION_ID),
                "--receipt-id", str(RECEIPT_ID), "--sha", EXPECTED_SHA, "--apply",
            ])
        check("initial business-state read failure refuses before every mutation path",
              initial_rc == 2 and initial_stdout.getvalue() == ""
              and "synthetic initial read failure" in initial_stderr.getvalue())

        provision.read_seed_state = lambda *_args, **_kwargs: valid_state
        dry_stdout = io.StringIO()
        with contextlib.redirect_stdout(dry_stdout):
            dry_rc = provision.main([
                "--candidate-operation-id", str(CANDIDATE_OPERATION_ID),
                "--receipt-id", str(RECEIPT_ID), "--sha", EXPECTED_SHA,
            ])
        dry_output = json.loads(dry_stdout.getvalue())
        check("main dry-run proves the exact candidate without any mutation path",
              dry_rc == 0 and dry_output["state"] == "dry_run"
              and dry_output["mutated"] is False
              and dry_output["candidate_project_id"] == candidate.project_id
              and dry_output["prior_staging_project_id"] == old.project_id
              and dry_output["production_project_id"] == production.project_id
              and dry_output["proposal_status"] == {"approved": 60, "pending": 2}
              and dry_output["target_count"] == 3 and dry_output["batch_count"] == 4)

        real_profile = saved_main_dependencies["credential_profile"]
        def candidate_profile(label: str, *, config_root=None):
            profile_roots.append(config_root)
            return real_profile(label, config_root=config_root)
        def load_candidate_credential(_paths, *, role_name, **_kwargs):
            value = candidate_by_role[role_name]
            return provision.credential.StoredCredential(
                "final", pathlib.Path("/fixture/final"), value, "fixture-password",
                candidate.endpoint_host, 5432, "neondb")
        provision.credential.profile = candidate_profile
        provision.credential.load_existing = load_candidate_credential
        provision.load_rollback_worker_values = lambda _old: dict(old_values)
        provision.validate_profile_login = lambda *_args, **_kwargs: None
        provision.require_direct_owner_identity = lambda _cur: "neondb_owner"
        provision.psycopg.connect = lambda *_args, **_kwargs: FakeOwner()
        def record_preservation(*_args, **_kwargs) -> None:
            nonlocal preserve_calls
            preserve_calls += 1
            if preserve_calls > unlocked_preserve_budget and worker_lock not in active_locks:
                raise AssertionError("provider preservation ran outside the global Worker lock")
            orchestration_events.append(("preserve", None))
        provision.replacement.prove_provider_preservation = record_preservation
        def record_bulk(values) -> None:
            if worker_lock not in active_locks:
                raise AssertionError("Worker bulk ran outside the global cutover lock")
            orchestration_events.append(("bulk", dict(values)))
        provision.bulk_worker_database_secrets = record_bulk
        def record_verify(**_kwargs) -> None:
            if worker_lock not in active_locks:
                raise AssertionError("Worker readback ran outside the global cutover lock")
            orchestration_events.append(("verify", None))
        provision.verify_worker_database_secret_bindings = record_verify

        # A successful main apply uses candidate-root credentials, one atomic
        # Worker pair, provider pre/post proof, and the complete final readback.
        seed_reads = 0
        preserve_calls = 0
        unlocked_preserve_budget = 1
        def successful_seed_read(*_args, **_kwargs):
            nonlocal seed_reads
            seed_reads += 1
            if seed_reads == 2 and worker_lock not in active_locks:
                raise AssertionError("final seed readback ran outside the global Worker lock")
            orchestration_events.append(("seed", seed_reads))
            return valid_state
        provision.read_seed_state = successful_seed_read
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            rc = provision.main([
                "--candidate-operation-id", str(CANDIDATE_OPERATION_ID),
                "--receipt-id", str(RECEIPT_ID), "--sha", EXPECTED_SHA, "--apply",
            ])
        apply_output = json.loads(stdout.getvalue())
        check("main apply routes every credential only through the candidate root",
              profile_roots == [candidate_root] * len(provision.PROFILES)
              and all(root != pathlib.Path.home() / ".config/carr" for root in profile_roots))
        check("main apply performs one atomic candidate set with provider pre/post proof",
              rc == 0 and orchestration_events == [
                  ("seed", 1), ("preserve", None), ("preserve", None),
                  ("bulk", expected_candidate_values), ("verify", None),
                  ("preserve", None), ("seed", 2),
              ])
        check("main apply completes exact final readback without fallback or disclosure",
              apply_output["state"] == "provisioned" and seed_reads == 2
              and apply_output["candidate_operation_id"] == str(CANDIDATE_OPERATION_ID)
              and apply_output["proposal_status"] == {"approved": 60, "pending": 2}
              and apply_output["target_count"] == 3 and apply_output["batch_count"] == 4
              and all(secret not in stdout.getvalue()
                      for secret in expected_candidate_values.values()))
        # A SEAT THAT ALREADY HAS A FINAL CREDENTIAL IS NEVER ADOPTED. This
        # fixture seeds all three credential files, so the run must prove each
        # role by logging in as it and rotate nothing -- the `adopted` outcome
        # appearing here would mean an existing credential had been replaced.
        check("an already-credentialed seat is reused, never adopted",
              apply_output["role_outcomes"] == {
                  "reader": "reused", "writer": "reused",
                  "gate_zero_producer": "reused",
                  "foundation_assurance_oracle": "reused"})

        # Every field in the readable business-state snapshot is immutable
        # across credential publication. Any drift restores the prior pair.
        for drift_label, drift_state in (
            ("proposal", provision.SeedState(
                (("approved", 61), ("pending", 1)), 3, 4)),
            ("target", provision.SeedState(
                (("approved", 60), ("pending", 2)), 4, 4)),
            ("batch", provision.SeedState(
                (("approved", 60), ("pending", 2)), 3, 5)),
        ):
            orchestration_events.clear()
            profile_roots.clear()
            preserve_calls = 0
            unlocked_preserve_budget = 1
            drift_reads = 0
            def drifting_seed_read(*_args, **_kwargs):
                nonlocal drift_reads
                drift_reads += 1
                if drift_reads == 2 and worker_lock not in active_locks:
                    raise AssertionError(
                        "drifted business-state read ran outside the global Worker lock")
                orchestration_events.append(("seed", drift_reads))
                return valid_state if drift_reads == 1 else drift_state
            provision.read_seed_state = drifting_seed_read
            drift_stdout = io.StringIO()
            drift_stderr = io.StringIO()
            with contextlib.redirect_stdout(drift_stdout), \
                    contextlib.redirect_stderr(drift_stderr):
                drift_rc = provision.main([
                    "--candidate-operation-id", str(CANDIDATE_OPERATION_ID),
                    "--receipt-id", str(RECEIPT_ID), "--sha", EXPECTED_SHA, "--apply",
                ])
            check(f"main {drift_label} drift restores the exact prior Worker pair",
                  drift_rc == 2 and orchestration_events == [
                      ("seed", 1), ("preserve", None), ("preserve", None),
                      ("bulk", expected_candidate_values), ("verify", None),
                      ("preserve", None), ("seed", 2),
                      ("bulk", old_values), ("verify", None), ("preserve", None),
                  ] and all(secret not in drift_stdout.getvalue() + drift_stderr.getvalue()
                            for secret in (*expected_candidate_values.values(),
                                           *old_values.values())))

        # If the complete final seed readback refuses, main must atomically
        # restore and verify the old pair before re-proving provider preservation.
        orchestration_events.clear()
        profile_roots.clear()
        failure_reads = 0
        preserve_calls = 0
        unlocked_preserve_budget = 1
        def failing_final_seed_read(*_args, **_kwargs):
            nonlocal failure_reads
            failure_reads += 1
            if failure_reads == 2 and worker_lock not in active_locks:
                raise AssertionError("failed final readback ran outside the global Worker lock")
            orchestration_events.append(("seed", failure_reads))
            if failure_reads == 2:
                raise provision.ProvisioningRefusal("synthetic final readback failure")
            return valid_state
        provision.read_seed_state = failing_final_seed_read
        failure_stdout = io.StringIO()
        failure_stderr = io.StringIO()
        with contextlib.redirect_stdout(failure_stdout), contextlib.redirect_stderr(failure_stderr):
            failure_rc = provision.main([
                "--candidate-operation-id", str(CANDIDATE_OPERATION_ID),
                "--receipt-id", str(RECEIPT_ID), "--sha", EXPECTED_SHA, "--apply",
            ])
        check("main final-readback refusal restores and provider-verifies the old pair",
              failure_rc == 2 and orchestration_events == [
                  ("seed", 1), ("preserve", None), ("preserve", None),
                  ("bulk", expected_candidate_values), ("verify", None),
                  ("preserve", None), ("seed", 2),
                  ("bulk", old_values), ("verify", None), ("preserve", None),
              ])
        check("final-readback rollback suppresses both candidate and prior secrets",
              all(secret not in failure_stdout.getvalue() + failure_stderr.getvalue()
                  for secret in (*expected_candidate_values.values(), *old_values.values())))

        # Explicit rollback shares the same global Worker lock and does no
        # candidate credential or database role work.
        orchestration_events.clear()
        preserve_calls = 0
        unlocked_preserve_budget = 0
        provision.credential.profile = lambda *_args, **_kwargs: \
            (_ for _ in ()).throw(AssertionError("rollback opened candidate credentials"))
        provision.read_seed_state = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("rollback must not read/converge candidate roles"))
        provision.psycopg.connect = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("rollback must not open candidate owner DB"))
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            rc = provision.main([
                "--candidate-operation-id", str(CANDIDATE_OPERATION_ID),
                "--receipt-id", str(RECEIPT_ID), "--sha", EXPECTED_SHA,
                "--rollback-to-prior-staging", "--apply",
            ])
        rollback_output = json.loads(stdout.getvalue())
        check("explicit rollback uses one atomic old-pair bulk with pre/post preservation",
              rc == 0 and orchestration_events == [
                  ("preserve", None), ("bulk", old_values), ("verify", None),
                  ("preserve", None),
              ])
        check("explicit rollback performs no candidate role mutation or secret disclosure",
              rollback_output["candidate_roles_mutated"] is False
              and all(secret not in stdout.getvalue() for secret in old_values.values()))
    finally:
        provision.reject_unsafe_environment = saved_main_dependencies["reject_unsafe_environment"]
        provision.replacement_target = saved_main_dependencies["replacement_target"]
        provision.resolve_replacement_binding = saved_main_dependencies["resolve_replacement_binding"]
        provision.load_rollback_worker_values = saved_main_dependencies["load_rollback_worker_values"]
        provision.bulk_worker_database_secrets = saved_main_dependencies["bulk_worker_database_secrets"]
        provision.verify_worker_database_secret_bindings = saved_main_dependencies[
            "verify_worker_database_secret_bindings"]
        provision.read_seed_state = saved_main_dependencies["read_seed_state"]
        provision.psycopg.connect = saved_main_dependencies["connect"]
        provision.replacement.prove_provider_preservation = saved_main_dependencies[
            "prove_provider_preservation"]
        provision.snapshot_grants.load_current_grants_to_role = saved_main_dependencies["load_grants"]
        provision.credential.exclusive_lock = saved_main_dependencies["exclusive_lock"]
        provision.credential.profile = saved_main_dependencies["credential_profile"]
        provision.credential.load_existing = saved_main_dependencies["load_existing"]
        provision.validate_profile_login = saved_main_dependencies["validate_profile_login"]
        provision.require_direct_owner_identity = saved_main_dependencies[
            "require_direct_owner_identity"]
    try:
        provision.worker_environment({"CLOUDFLARE_ACCOUNT_ID": "0" * 32})
    except provision.ProvisioningRefusal:
        check("mismatched ambient Cloudflare account is refused", True)
    else:
        raise AssertionError("mismatched ambient Cloudflare account was accepted")

    def timed_out(_args, **_kwargs):
        raise subprocess.TimeoutExpired("neonctl", 60, output="secret", stderr="secret")

    try:
        provision.provider_dsn(
            scope, "neondb_owner", neonctl="neonctl", run=timed_out, environ={},
        )
    except provision.ProvisioningRefusal as exc:
        check("provider timeout suppresses captured output", "secret" not in str(exc))
    else:
        raise AssertionError("provider timeout escaped the refusal boundary")

    class ScopeRunner:
        def __init__(self, project_id: str = "staging-project",
                     branch_id: str = "staging-main"):
            self.project_id = project_id
            self.branch_id = branch_id
            self.calls: list[list[str]] = []

        def __call__(self, args, **kwargs):
            self.calls.append(list(args))
            if args[1:3] == ["projects", "list"]:
                payload = {"projects": [{"id": self.project_id, "name": "carr-staging"}]}
            elif args[1:3] == ["branches", "list"]:
                payload = {"branches": [{"id": self.branch_id,
                                           "project_id": self.project_id,
                                           "name": "main", "default": True}]}
            elif args[1] == "api":
                payload = {"endpoints": [{"id": "ep-fixture",
                    "branch_id": self.branch_id, "type": "read_write",
                    "host": endpoint_host}]}
            else:
                raise AssertionError(args)
            return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    stable_scope = ScopeRunner()
    provision.verify_provider_scope(
        scope, neonctl="neonctl", run=stable_scope, environ={},
    )
    rebuilt_scope = ScopeRunner(project_id="rebuilt-project", branch_id="rebuilt-main")
    try:
        provision.verify_provider_scope(
            scope, neonctl="neonctl", run=rebuilt_scope, environ={},
        )
    except provision.ProvisioningRefusal:
        check("project/branch rebuild drift after resolution is refused", True)
    else:
        raise AssertionError("provider scope rebuild drift was accepted")

    original_target = provision.replacement_target
    original_reject = provision.reject_unsafe_environment
    try:
        provision.reject_unsafe_environment = lambda _environment: None

        def dependency_exit(*_args, **_kwargs):
            raise SystemExit("https://provider.invalid/secret-output")

        provision.replacement_target = dependency_exit
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            return_code = provision.main([
                "--candidate-operation-id", str(CANDIDATE_OPERATION_ID),
                "--receipt-id", str(RECEIPT_ID), "--sha", EXPECTED_SHA,
            ])
        check("provider dependency SystemExit is caught without leaking output",
              return_code == 2 and "secret-output" not in stderr.getvalue())
    finally:
        provision.replacement_target = original_target
        provision.reject_unsafe_environment = original_reject

    profiles = {profile.label: profile for profile in provision.PROFILES}
    plans = {
        label: snapshot.load_current_grants_to_role(
            REPO / "db/schema.sql", REPO / "migrations", profile.grant_role
        ) for label, profile in profiles.items()
    }

    # A DIRECT-GRANT PROFILE HAS NO BUNDLE BEHIND IT: the canonical ACLs sit on
    # the login role, and the login role is a member of nothing. Building the
    # fixture from the profile rather than from a fixed pair is what lets this
    # suite cover both shapes without a second copy of it.
    def profile_closure(profile, facts=None, creator="neondb_owner"):
        facts = tuple(facts if facts is not None else snapshot.acl_facts(plans[profile.label]))
        direct = profile.bundle_role is None
        return provision.ProfileClosure(
            login=provision.RoleAuthority(
                True, True, (), ("idle_in_transaction_session_timeout=120s",
                                 "statement_timeout=60s"),
                () if direct else ((profile.bundle_role, False, True, True),),
                () if direct else (profile.bundle_role,),
                facts if direct else (), (),
            ),
            bundle=None if direct
                else provision.RoleAuthority(False, True, (), (), (), (), facts, ()),
            creator_edges=((creator, True, False, False,
                            provision.BOOTSTRAP_SUPERUSER_OID),),
        )

    for label, profile in profiles.items():
        provision.validate_profile_closure(
            profile_closure(profile), profile, plans[label], exact=True,
            expected_creator="neondb_owner",
        )
    check("reader and writer exact closed authority profiles validate", True)
    class IdentityCursor:
        def __init__(self, row): self.row = row
        def execute(self, _statement): pass
        def fetchone(self): return self.row
    check("direct owner session/current identity is admitted",
          provision.require_direct_owner_identity(
              IdentityCursor(("neondb_owner", "neondb_owner", False, True))
          ) == "neondb_owner")
    for identity in (
        ("app_writer", "app_writer", False, True),
        ("neondb_owner", "app_writer", False, True),
        ("neondb_owner", "neondb_owner", True, True),
        ("neondb_owner", "neondb_owner", False, False),
        None,
    ):
        try:
            provision.require_direct_owner_identity(IdentityCursor(identity))
        except provision.ProvisioningRefusal:
            pass
        else:
            raise AssertionError(f"unsafe owner identity accepted: {identity!r}")
    check("SET ROLE, superuser, non-CREATEROLE and non-owner identities refuse", True)
    for creator_edges in (
        (),
        (("neondb_owner", True, False, False, 11),),
        (("neondb_owner", True, True, False, provision.BOOTSTRAP_SUPERUSER_OID),),
        (("neondb_owner", True, False, False, provision.BOOTSTRAP_SUPERUSER_OID),
         ("other", True, False, False, provision.BOOTSTRAP_SUPERUSER_OID)),
    ):
        try:
            provision.validate_profile_closure(
                dataclasses.replace(
                    profile_closure(profiles["writer"]), creator_edges=creator_edges,
                ),
                profiles["writer"], plans["writer"], exact=True,
                expected_creator="neondb_owner",
            )
        except provision.ProvisioningRefusal:
            pass
        else:
            raise AssertionError(f"missing or drifted creator edge accepted: {creator_edges!r}")
    check("missing, extra, option-drifted or non-bootstrap creator edges refuse", True)

    class SnapshotEdgeCursor:
        def __init__(self):
            self.statements = []
        def execute(self, statement, params=None):
            self.statements.append((statement, params))
        def fetchone(self):
            return (16392,)

    original_creator_edges = provision.collect_creator_edges
    try:
        edge_cursor = SnapshotEdgeCursor()
        edge_reads = iter((
            (("neondb_owner", True, False, False,
              provision.BOOTSTRAP_SUPERUSER_OID),
             ("neondb_owner", False, True, True, 16392)),
            (("neondb_owner", True, False, False,
              provision.BOOTSTRAP_SUPERUSER_OID),),
        ))
        provision.collect_creator_edges = lambda _cur, _role: next(edge_reads)
        check("migration-created seat repairs only the old snapshot duplicate edge",
              provision.repair_snapshot_creator_edge(
                  edge_cursor, profiles["gate_zero_producer"],
                  expected_creator="neondb_owner",
              ) is True
              and any("revoke" in repr(statement).lower()
                      and "carr_gate_zero_producer" in repr(statement)
                      for statement, _params in edge_cursor.statements))

        provision.collect_creator_edges = lambda _cur, _role: (
            ("neondb_owner", True, False, False,
             provision.BOOTSTRAP_SUPERUSER_OID),
        )
        check("already-exact migration-created creator edge is unchanged",
              provision.repair_snapshot_creator_edge(
                  SnapshotEdgeCursor(), profiles["foundation_assurance_oracle"],
                  expected_creator="neondb_owner",
              ) is False)

        provision.collect_creator_edges = lambda _cur, _role: (
            ("neondb_owner", True, False, False,
             provision.BOOTSTRAP_SUPERUSER_OID),
            ("other", False, True, True, 16392),
        )
        try:
            provision.repair_snapshot_creator_edge(
                SnapshotEdgeCursor(), profiles["gate_zero_producer"],
                expected_creator="neondb_owner",
            )
        except provision.ProvisioningRefusal:
            pass
        else:
            raise AssertionError("unsafe creator-edge drift was repaired")
        check("creator-edge repair refuses every non-snapshot shape", True)
    finally:
        provision.collect_creator_edges = original_creator_edges

    expected_actions = {
        (False, "absent"): "prepare_create",
        (False, "pending"): "create",
        (True, "pending"): "resume",
        (True, "final"): "reuse",
    }
    check("absent/create and pending/final resume matrix is exact",
          all(provision.decide_profile_action(
              role_exists_now=exists, credential_state=state) == action
              for (exists, state), action in expected_actions.items()))
    for bad in ((False, "final"), (True, "absent"), (True, "unknown")):
        try:
            provision.decide_profile_action(role_exists_now=bad[0], credential_state=bad[1])
        except provision.ProvisioningRefusal:
            pass
        else:
            raise AssertionError(f"unsafe state accepted: {bad}")
    check("orphan final, uncredentialed role and unknown state refuse", True)

    # THE MIGRATION-CREATED SEAT'S ROWS, DECIDED BY THE DATABASE. Adoption is
    # available only while pg_authid still says the role has no password; the
    # same local state with a password already set is a LOST FILE and refuses.
    check("a passwordless migrated seat adopts from absent and from pending",
          provision.decide_profile_action(
              role_exists_now=True, credential_state="absent",
              role_created_by_migration=True, role_passwordless=True) == "adopt"
          and provision.decide_profile_action(
              role_exists_now=True, credential_state="pending",
              role_created_by_migration=True, role_passwordless=True) == "adopt")
    check("a credentialed migrated seat resumes a pending file rather than re-minting",
          provision.decide_profile_action(
              role_exists_now=True, credential_state="pending",
              role_created_by_migration=True, role_passwordless=False) == "resume")
    for missing_witness in (False, None):
        try:
            provision.decide_profile_action(
                role_exists_now=True, credential_state="absent",
                role_created_by_migration=True, role_passwordless=missing_witness)
        except provision.ProvisioningRefusal as exc:
            if missing_witness is False and "lost local file" not in str(exc):
                raise AssertionError("the lost-file refusal does not name what happened")
        else:
            raise AssertionError(
                f"a migrated seat was adopted with role_passwordless={missing_witness!r}")
    check("a lost credential file never becomes a rotation, and an unknown state refuses", True)

    # ---- THE MIGRATION-CREATED SEAT'S OTHER ROWS ---------------------------
    check("adoption does not loosen the other rows",
          provision.decide_profile_action(
              role_exists_now=True, credential_state="final",
              role_created_by_migration=True, role_passwordless=False) == "reuse"
          and provision.decide_profile_action(
              role_exists_now=False, credential_state="absent",
              role_created_by_migration=True, role_passwordless=None) == "prepare_create")
    for bad in ((False, "final"), (True, "unknown")):
        try:
            provision.decide_profile_action(
                role_exists_now=bad[0], credential_state=bad[1],
                role_created_by_migration=True, role_passwordless=True)
        except provision.ProvisioningRefusal:
            pass
        else:
            raise AssertionError(f"unsafe migration-created state accepted: {bad}")
    check("an adopting profile still refuses orphan-final and unknown states", True)
    # MUTATION CONTROL for the adopt row: the SAME state, on a passwordless role,
    # asked for a profile this tool creates itself, must still refuse. If
    # adoption ever widens to every profile, this check goes red rather than the
    # suite staying green.
    try:
        provision.decide_profile_action(
            role_exists_now=True, credential_state="absent",
            role_created_by_migration=False, role_passwordless=True)
    except provision.ProvisioningRefusal:
        check("mutation control: a tool-created role with a lost credential still refuses",
              True)
    else:
        raise AssertionError("adoption widened to a tool-created profile")
    check("exactly the two dedicated producer profiles are migration-created",
          {profile.label for profile in provision.PROFILES
           if profile.created_by_migration} == {
               "gate_zero_producer", "foundation_assurance_oracle"})

    events: list[str] = []
    def converge(profile):
        events.append("converge:" + profile.label)
        return "secret-" + profile.label, "created"
    def publish(profile, value):
        events.append("publish:" + profile.label)
        if profile.label == "writer":
            raise provision.ProvisioningRefusal("synthetic publish failure")
    try:
        provision.run_profile_sequence(provision.PROFILES, converge, publish)
    except provision.ProvisioningRefusal:
        pass
    else:
        raise AssertionError("profile publish failure was accepted")
    check("reader converges/publishes before writer and failure stops at exact boundary",
          events == ["converge:reader", "publish:reader", "converge:writer", "publish:writer"])
    events.clear()
    provision.run_profile_sequence(
        provision.PROFILES, converge,
        lambda profile, _value: events.append("resume-publish:" + profile.label),
    )
    # THE ORDER IS THE TOOL'S OWN, so a profile added later is covered here the
    # day it is added rather than the day someone remembers to retype this list.
    check("rerun resumes every profile in the same deterministic order",
          events == [f"{verb}:{profile.label}"
                     for profile in provision.PROFILES
                     for verb in ("converge", "resume-publish")])

    class Cursor:
        # ``passwordless`` is what pg_authid answers for the adopt guard: True
        # (never credentialed), False (already holds one), or None to model a
        # connection that cannot read pg_authid at all.
        def __init__(self, fail_secret=None, passwordless=True):
            self.statements = []
            self.fail_secret = fail_secret
            self.passwordless = passwordless
            self.password_probe_index = None
            self._rows = []
        def execute(self, statement, params=None):
            if "pg_authid" in str(statement):
                self.password_probe_index = len(self.statements)
                self.statements.append(statement)
                if self.passwordless is None:
                    raise provision.psycopg.errors.InsufficientPrivilege(
                        "permission denied for table pg_authid")
                self._rows = [(self.passwordless,)]
                return
            self.statements.append(statement)
            self._rows = []
            if self.fail_secret:
                raise RuntimeError("database rejected " + self.fail_secret)
        def fetchone(self):
            return ("",)
        def fetchall(self):
            return list(self._rows)
    class Connection:
        def __init__(self, fail_secret=None, passwordless=True):
            self.cur = Cursor(fail_secret, passwordless)
            self.commits = 0
            self.rollbacks = 0
        def cursor(self): return self.cur
        def commit(self): self.commits += 1
        def rollback(self): self.rollbacks += 1

    profile = profiles["writer"]
    tiny_plan = plans["writer"][:3]
    original_exists = provision.role_exists
    original_bundle = provision.collect_role_authority
    original_closure = provision.collect_profile_closure
    try:
        provision.role_exists = lambda _cur, _role: False
        provision.collect_role_authority = lambda _cur, _role: profile_closure(
            profile, snapshot.acl_facts(tiny_plan)
        ).bundle
        provision.collect_profile_closure = lambda _cur, _profile: profile_closure(
            profile, snapshot.acl_facts(tiny_plan)
        )
        success = Connection()
        created = provision.apply_login_profile(
            success, profile, tiny_plan, "s" * 64, expected_creator="neondb_owner"
        )
        check("new SQL login profile creates, validates and commits once",
              created and success.commits == 1 and success.rollbacks == 0)
        check("new SQL login relies on PostgreSQL's automatic creator ADMIN edge",
              not any(
                  "sql('grant ')" in repr(statement).lower()
                  and "identifier('app_writer')" in repr(statement).lower()
                  and "identifier('neondb_owner')" in repr(statement).lower()
                  for statement in success.cur.statements
              )
              and [str(statement).lower() for statement in success.cur.statements].index(
                  "set local createrole_self_grant = ''"
              ) < next(
                  index for index, statement in enumerate(success.cur.statements)
                  if "create role" in repr(statement).lower()
              ))
        secret = "leak-check-" + "z" * 48
        failed = Connection(secret)
        try:
            provision.apply_login_profile(
                failed, profile, tiny_plan, secret, expected_creator="neondb_owner"
            )
        except provision.ProvisioningRefusal as exc:
            check("CREATE ROLE failure rolls back without exposing password",
                  failed.rollbacks == 1 and secret not in str(exc))
        else:
            raise AssertionError("secret-bearing database failure escaped")
        provision.role_exists = lambda _cur, _role: True
        reused = Connection()
        created = provision.apply_login_profile(
            reused, profile, tiny_plan, "s" * 64, expected_creator="neondb_owner"
        )
        check("exact reused SQL login profile commits without CREATE ROLE",
              not created and reused.commits == 1
              and all("create role" not in str(statement).lower()
                      for statement in reused.cur.statements))
    finally:
        provision.role_exists = original_exists
        provision.collect_role_authority = original_bundle
        provision.collect_profile_closure = original_closure

    # ======================================================================
    # THE MIGRATION-CREATED GATE ZERO SEAT, AND THE ONE STATE IT MAY BE
    # ADOPTED FROM
    # ======================================================================
    # Migration 0502 creates carr_gate_zero_producer with NO password, so
    # role-present/credential-absent is where a fresh database actually starts.
    # The dangerous neighbour of that state, and the one the fourth correction
    # could not tell apart from it, is role-present/credential-absent because the
    # LOCAL FILE WAS LOST -- a deleted file, a fresh clone, another machine. The
    # database is the only witness that separates them, and these checks are that
    # separation, executed.
    seat = profiles["gate_zero_producer"]
    seat_plan = plans["gate_zero_producer"]
    seat_facts = tuple(snapshot.acl_facts(seat_plan))
    check("the Gate Zero seat is one of the two migration-created direct-grant profiles",
          seat.created_by_migration and seat.bundle_role is None
          and seat.login_role == "carr_gate_zero_producer" and len(seat_plan) >= 5
          and {other.label for other in provision.PROFILES
               if other.created_by_migration}
          == {"gate_zero_producer", "foundation_assurance_oracle"})

    holder: dict[str, Any] = {}

    def migrated_closure(_cur, _profile):
        """What the role actually looks like: converged once the timeouts are set."""
        issued = any("statement_timeout" in str(statement)
                     for statement in holder["conn"].cur.statements)
        if issued:
            return profile_closure(seat, seat_facts)
        return provision.ProfileClosure(
            login=provision.RoleAuthority(True, True, (), (), (), (), seat_facts, ()),
            bundle=None,
            creator_edges=(("neondb_owner", True, False, False,
                            provision.BOOTSTRAP_SUPERUSER_OID),),
        )

    original_exists = provision.role_exists
    original_authority = provision.collect_role_authority
    original_closure = provision.collect_profile_closure
    original_repair = provision.repair_snapshot_creator_edge
    try:
        provision.role_exists = lambda _cur, _role: True
        # The repair helper's exact and drifted rows are exercised above. These
        # adoption fixtures model a post-repair closure and keep their cursor
        # focused on the password transaction.
        provision.repair_snapshot_creator_edge = lambda *_args, **_kwargs: False
        provision.collect_role_authority = lambda _cur, _role: provision.RoleAuthority(
            True, True, (), (), (), (), seat_facts, ())
        provision.collect_profile_closure = migrated_closure

        holder["conn"] = Connection(passwordless=True)
        created = provision.apply_login_profile(
            holder["conn"], seat, seat_plan, "a" * 64,
            expected_creator="neondb_owner", adopt=True)
        adopted_statements = [repr(statement).lower()
                              for statement in holder["conn"].cur.statements]
        check("adopting sets a password on the existing role and creates no role",
              not created and holder["conn"].commits == 1
              and any("alter role" in statement and "password" in statement
                      for statement in adopted_statements)
              and not any("create role" in statement for statement in adopted_statements))
        check("an adopted role is still held to the exact canonical closure",
              any("statement_timeout" in statement for statement in adopted_statements)
              and any("idle_in_transaction_session_timeout" in statement
                      for statement in adopted_statements))
        check("adoption verifies the migration's grants and re-issues none of them",
              seat_plan and not any(
                  statement.lstrip().startswith(("grant ", "revoke "))
                  for statement in map(str, holder["conn"].cur.statements)))
        check("the passwordless proof is read from pg_authid before the ALTER, not after",
              holder["conn"].cur.password_probe_index is not None
              and holder["conn"].cur.password_probe_index < next(
                  index for index, statement in enumerate(holder["conn"].cur.statements)
                  if "alter role" in repr(statement).lower()
                  and "password" in repr(statement).lower()))

        # THE LOST LOCAL FILE. Same role, same absent credential, and the ONE
        # difference that matters: pg_authid says the seat already holds a
        # password. Adoption must refuse, and it must not issue the ALTER.
        holder["conn"] = Connection(passwordless=False)
        try:
            provision.apply_login_profile(
                holder["conn"], seat, seat_plan, "b" * 64,
                expected_creator="neondb_owner", adopt=True)
        except provision.ProvisioningRefusal as exc:
            check("a seat that already holds a password is never silently rotated",
                  "already holds a password" in str(exc)
                  and holder["conn"].commits == 0 and holder["conn"].rollbacks == 1
                  and not any("alter role" in repr(statement).lower()
                              and "password" in repr(statement).lower()
                              for statement in holder["conn"].cur.statements))
        else:
            raise AssertionError("adoption rotated a credential that was still in use")

        # A connection that cannot READ pg_authid gets no benefit of the doubt.
        holder["conn"] = Connection(passwordless=None)
        try:
            provision.apply_login_profile(
                holder["conn"], seat, seat_plan, "b" * 64,
                expected_creator="neondb_owner", adopt=True)
        except provision.ProvisioningRefusal as exc:
            check("an unreadable pg_authid refuses adoption instead of assuming",
                  "cannot see it" in str(exc)
                  and not any("alter role" in repr(statement).lower()
                              and "password" in repr(statement).lower()
                              for statement in holder["conn"].cur.statements))
        else:
            raise AssertionError("adoption proceeded without proving the seat passwordless")

        # commit=False is the reversible shape the cutover depends on.
        holder["conn"] = Connection(passwordless=True)
        provision.apply_login_profile(
            holder["conn"], seat, seat_plan, "e" * 64,
            expected_creator="neondb_owner", adopt=True, commit=False)
        check("an adopted seat's password change is left uncommitted when asked",
              holder["conn"].commits == 0 and holder["conn"].rollbacks == 0
              and any("alter role" in repr(statement).lower()
                      and "password" in repr(statement).lower()
                      for statement in holder["conn"].cur.statements))

        # MUTATION CONTROL. The same role in the same state, converged the way
        # every other profile is (adopt=False), REFUSES -- which is exactly the
        # P0 an earlier round shipped. If the adopt path is ever deleted, this
        # refusal is what a fresh database would be left with.
        holder["conn"] = Connection(passwordless=True)
        try:
            provision.apply_login_profile(
                holder["conn"], seat, seat_plan, "b" * 64,
                expected_creator="neondb_owner")
        except provision.ProvisioningRefusal:
            check("mutation control: the pre-adopt path cannot converge a migrated seat",
                  holder["conn"].commits == 0 and holder["conn"].rollbacks == 1
                  and not any("alter role" in repr(statement).lower()
                              and "password" in repr(statement).lower()
                              for statement in holder["conn"].cur.statements))
        else:
            raise AssertionError("the pre-adopt path silently accepted a migrated seat")

        holder["conn"] = Connection(passwordless=True)
        try:
            provision.apply_login_profile(
                holder["conn"], profiles["writer"], tiny_plan, "c" * 64,
                expected_creator="neondb_owner", adopt=True)
        except provision.ProvisioningRefusal as exc:
            check("adoption is refused for a profile this tool creates itself",
                  "may not be adopted" in str(exc))
        else:
            raise AssertionError("adoption widened past the migration-created seat")

        provision.role_exists = lambda _cur, _role: False
        holder["conn"] = Connection(passwordless=True)
        try:
            provision.apply_login_profile(
                holder["conn"], seat, seat_plan, "d" * 64,
                expected_creator="neondb_owner", adopt=True)
        except provision.ProvisioningRefusal as exc:
            check("adoption refuses when the seat migration has not applied here",
                  "does not exist" in str(exc))
        else:
            raise AssertionError("adoption invented a role the migration never created")
    finally:
        provision.role_exists = original_exists
        provision.collect_role_authority = original_authority
        provision.collect_profile_closure = original_closure
        provision.repair_snapshot_creator_edge = original_repair

    # ---- PUBLISH FIRST, COMMIT SECOND, AND THE FAILURE IS THE UNDO ---------
    # The ordering is a named function precisely so it can be executed here
    # rather than read off the call site.
    class SeatConn:
        def __init__(self, commit_raises=None):
            self.commits = 0
            self._commit_raises = commit_raises
        def commit(self):
            self.commits += 1
            if self._commit_raises is not None:
                raise self._commit_raises

    seat_entry = provision.AdoptedSeat("profile", "file", "stored")
    order: list[str] = []
    settled = SeatConn()
    provision.settle_adopted_seats(
        settled, [seat_entry],
        publish=lambda: order.append("publish"),
        prove=lambda seat: order.append("prove:" + seat.profile),
        compensate=lambda: order.append("compensate"),
    )
    check("the Worker secret is published and read back before the password commits",
          order == ["publish", "prove:profile"] and settled.commits == 1)

    refused = SeatConn()
    order.clear()
    try:
        provision.settle_adopted_seats(
            refused, [seat_entry],
            publish=lambda: (_ for _ in ()).throw(
                provision.ProvisioningRefusal("Worker credential cutover refused")),
            prove=lambda seat: order.append("prove"),
            compensate=lambda: order.append("compensate"),
        )
    except provision.ProvisioningRefusal:
        check("a failed publication leaves the adopted password uncommitted and unproven",
              refused.commits == 0 and not order)
    else:
        raise AssertionError("the seat password committed after a failed publication")

    # ---- AND WHEN THE COMMIT ITSELF FAILS, THE WORKER IS TAKEN BACK ---------
    # Publication has already succeeded here, so ordering cannot help: the
    # database rolls the password away and the Worker is left holding a DSN
    # that authenticates as nothing. The only answer is compensation, and this
    # injects the commit failure to prove it runs and that nothing is proven
    # afterwards.
    published: list[str] = []
    crashed = SeatConn(commit_raises=RuntimeError("connection lost mid-commit"))
    worker_binding = ["candidate"]
    def publish_candidate() -> None:
        worker_binding[0] = "candidate"
        published.append("publish")
    def restore_prior() -> None:
        worker_binding[0] = "prior"
        published.append("compensate")
    try:
        provision.settle_adopted_seats(
            crashed, [seat_entry],
            publish=publish_candidate,
            prove=lambda seat: published.append("prove"),
            compensate=restore_prior,
        )
    except provision.ProvisioningRefusal as exc:
        check("a failed commit after publication restores the prior Worker bindings "
              "rather than leaving the Worker on an unusable DSN",
              published == ["publish", "compensate"]
              and worker_binding[0] == "prior"
              and crashed.commits == 1
              and "prior Worker bindings restored" in str(exc)
              and "connection lost mid-commit" not in str(exc))
    else:
        raise AssertionError(
            "a failed commit reported success and left the Worker on the candidate DSN")

    # A compensation that cannot restore is UNCERTAIN, and says so by name.
    uncertain = SeatConn(commit_raises=RuntimeError("connection lost mid-commit"))
    try:
        provision.settle_adopted_seats(
            uncertain, [seat_entry],
            publish=lambda: None,
            prove=lambda seat: (_ for _ in ()).throw(
                AssertionError("proved an adopted seat whose password never committed")),
            compensate=lambda: (_ for _ in ()).throw(
                provision.ProvisioningRefusal("Worker secret readback failed")),
        )
    except provision.ProvisioningRefusal as exc:
        check("a failed commit whose restoration also refuses is named uncertain, not restored",
              "restoration outcome is uncertain" in str(exc))
    else:
        raise AssertionError("an unrestored Worker cutover reported success")

    check("the deferred adoption entry is a named seat rather than a bare triple",
          seat_entry.profile == "profile" and seat_entry.credential_file == "file"
          and seat_entry.stored == "stored"
          and not isinstance(seat_entry, tuple))

    check("no production project, worker, secret or credential path exists in this tool",
          not any(hasattr(provision, name) for name in (
              "PRODUCTION_GATE_ZERO_PROFILE", "PRODUCTION_WORKER_NAME",
              "PRODUCTION_GATE_ZERO_SECRET_NAME", "put_production_gate_zero_secret",
              "verify_production_gate_zero_secret_binding",
              "provision_production_gate_zero_writer", "production_owner_dsn",
              "require_production_gate_zero_target", "require_production_release_binding"))
          and all(profile.secret_name in provision.WORKER_DATABASE_SECRET_NAMES
                  for profile in provision.PROFILES))

    for bad_argv in (
        # every staging input is required, there is no production mode to ask
        # for, and the rollback still cannot run without --apply
        ["--sha", EXPECTED_SHA, "--apply"],
        ["--candidate-operation-id", str(CANDIDATE_OPERATION_ID), "--apply"],
        ["--candidate-operation-id", str(CANDIDATE_OPERATION_ID),
         "--receipt-id", str(RECEIPT_ID), "--apply"],
        ["--candidate-operation-id", str(CANDIDATE_OPERATION_ID),
         "--receipt-id", str(RECEIPT_ID), "--sha", EXPECTED_SHA,
         "--rollback-to-prior-staging"],
        ["--candidate-operation-id", str(CANDIDATE_OPERATION_ID),
         "--receipt-id", str(RECEIPT_ID), "--sha", EXPECTED_SHA, "--apply",
         "--production-gate-zero-writer"],
        ["--candidate-operation-id", str(CANDIDATE_OPERATION_ID),
         "--receipt-id", str(RECEIPT_ID), "--sha", EXPECTED_SHA, "--apply",
         "--provider", "cloudflare-workers"],
    ):
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                provision.parse_args(bad_argv)
        except SystemExit as exc:
            if exc.code != 2:
                raise AssertionError(f"wrong exit for {bad_argv}")
        else:
            raise AssertionError(f"an incoherent argument set was accepted: {bad_argv}")
    check("every staging input is required and no production mode can be asked for", True)

    check("Production project id is imported from the canonical db-tap pin",
          provision.PRODUCTION_PROJECT_ID
          == str(provision.db_tap.PROJECTS["production"]["id"]))

    print(f"PASS: staging app_writer provisioner self-test ({checked} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
