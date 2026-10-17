#!/usr/bin/env python3
# ci: db-gate
# doctrine: playbook-review
"""Rollback-only Postgres flow gate for the 0303 activation seam.

This deliberately exercises the real Program 6 proposal -> acceptance path,
then compiles and activates the exact plan-bound bundle.  It is kept as a DB
gate (rather than a SQL string assertion) so a circular preimage or a caller
supplied bundle cannot pass in a fresh throwaway database.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import uuid

import psycopg
from psycopg.types.json import Jsonb
from gate_runtime_role import rollback_only_connection, set_local_role


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "room-bridge"))
import execution_contract  # noqa: E402

REFUSAL_ASSERTIONS = 0


def _program6_helpers():
    path = ROOT / "ops" / "program6-ready-plan-gate.py"
    spec = importlib.util.spec_from_file_location("program6_ready_plan_gate", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Program 6 fixture helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def refusal(cur, sql: str, params: tuple, label: str) -> None:
    global REFUSAL_ASSERTIONS
    cur.execute("savepoint activation_refusal")
    try:
        cur.execute(sql, params)
    except psycopg.Error:
        cur.execute("rollback to savepoint activation_refusal")
        REFUSAL_ASSERTIONS += 1
        return
    cur.execute("rollback to savepoint activation_refusal")
    raise RuntimeError(f"{label} was accepted")


def _canonical_json(value: object) -> str:
    if isinstance(value, dict):
        return "{" + ",".join(
            json.dumps(key, ensure_ascii=False, separators=(",", ":")) + ":" + _canonical_json(value[key])
            for key in sorted(value)
        ) + "}"
    if isinstance(value, list):
        return "[" + ",".join(_canonical_json(item) for item in value) + "]"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _plugin_manifest(provider_key: str) -> dict:
    manifest = {
        "schema_version": "execution-environment-provider.v1",
        "provider_key": provider_key,
        "provider_version": 1,
        "display_name": "Fixture Environment Provider",
        "source_class": "plugin",
        "backend_kind": "remote",
        "implementation_ref": "fixture.environment.Provider",
        "implementation_digest": "sha256:" + "1" * 64,
        "capability_refs": ["environment:exec"],
        "operation_refs": ["operation:create", "operation:exec", "operation:cancel", "operation:destroy", "operation:health"],
        "isolation_class": "remote_host",
        "egress_policy_ref": "egress:deny-default",
        "secret_policy_ref": "secrets:brokered-only",
        "persistence_mode": "command_scoped",
        "resource_policy_ref": "resources:fixture-v1",
        "cleanup_policy_ref": "cleanup:fixture-v1",
        "threat_model_ref": "threat-model:fixture-v1",
        "conformance_contract_ref": "conformance:execution-environment-v1",
        "conformance_contract_digest": "sha256:" + "2" * 64,
        "configuration_schema_digest": "sha256:" + "3" * 64,
        "package_provenance": {
            "package_ref": "package:fixture-provider",
            "package_digest": "sha256:" + "4" * 64,
            "signature_ref": "signature:fixture-provider",
            "sbom_ref": "sbom:fixture-provider",
        },
        "collision_policy": "digest_pinned",
        "contains_secrets": False,
    }
    manifest["manifest_digest"] = "sha256:" + hashlib.sha256(_canonical_json(manifest).encode()).hexdigest()
    return manifest


def _conformance_observation(
    provider_ref: str,
    manifest: dict,
    observed_at: dt.datetime,
    *,
    run_ref: str,
    status: str,
    evidence_refs: list[str],
) -> dict:
    observation = {
        "schema_version": "execution-environment-conformance.v1",
        "provider_ref": provider_ref,
        "manifest_digest": manifest["manifest_digest"],
        "implementation_digest": manifest["implementation_digest"],
        "package_digest": manifest["package_provenance"]["package_digest"],
        "package_revision_ref": "git:fixture-provider-v1",
        "configuration_schema_digest": manifest["configuration_schema_digest"],
        "contract_ref": manifest["conformance_contract_ref"],
        "contract_digest": manifest["conformance_contract_digest"],
        "run_ref": run_ref,
        "status": status,
        "check_results": {"check:fixture": status == "passed"},
        "version_ref": "fixture-provider-v1",
        "backend_kind": manifest["backend_kind"],
        "evidence_refs": evidence_refs,
        "contains_secrets": False,
        "observed_at": observed_at.isoformat(),
    }
    digest_body = {key: value for key, value in observation.items() if key != "observed_at"}
    observation["run_digest"] = "sha256:" + hashlib.sha256(_canonical_json(digest_body).encode()).hexdigest()
    return observation


def _assert_provider_transition_serializes(dsn: str) -> None:
    """Two authority CAS calls cannot both inspect the same lifecycle head."""
    first = psycopg.connect(dsn)
    second = psycopg.connect(dsn)
    try:
        first.execute("set session authorization carr_authority_joe")
        second.execute("set session authorization carr_authority_joe")
        first.execute(
            "select * from ops.transition_execution_environment_provider(%s,%s,%s,%s,%s)",
            (
                "environment-provider:hermes-local:v1", "active", "disabled",
                Jsonb(["evidence:concurrent-cas-first"]), uuid.uuid4(),
            ),
        ).fetchone()
        second.execute("set local lock_timeout='200ms'")
        try:
            second.execute(
                "select * from ops.transition_execution_environment_provider(%s,%s,%s,%s,%s)",
                (
                    "environment-provider:hermes-local:v1", "active", "retired",
                    Jsonb(["evidence:concurrent-cas-second"]), uuid.uuid4(),
                ),
            ).fetchone()
        except psycopg.Error as exc:
            if exc.sqlstate != "55P03":
                raise RuntimeError(f"concurrent provider CAS failed for the wrong reason: {exc.sqlstate}") from exc
        else:
            raise RuntimeError("concurrent provider CAS callers both passed the same lifecycle head")
    finally:
        second.rollback()
        first.rollback()
        second.close()
        first.close()


def assert_required_item_ref_and_classification_frozen(cur, work_ref: str, plan_ref: str, bundle: dict) -> None:
    """A caller cannot demote or replace a compiler-selected required item."""
    tampered = json.loads(json.dumps(bundle))
    required_item = tampered["items"][0]
    if required_item.get("required") is not True or required_item.get("requirement_class") != "required":
        raise RuntimeError("compiler fixture did not contain a required item")
    required_item["required"] = False
    required_item["requirement_class"] = "advisory"
    required_item["canonical_ref"] = required_item["canonical_ref"] + ":replacement"
    # Recompute the caller-controlled body digest.  The server must still
    # refuse because the accepted plan freezes the original digest and item
    # refs before it checks this recomputed body digest.
    body = dict(tampered)
    body.pop("bundle_digest", None)
    digest_body = {
        "schema_version": body["schema_version"],
        "header": {key: value for key, value in body["header"].items() if key not in {"issued_at", "expires_at", "binding_id"}},
        "items": body["items"],
    }
    tampered["bundle_digest"] = "sha256:" + hashlib.sha256(_canonical_json(digest_body).encode("utf-8")).hexdigest()
    refusal(
        cur,
        "select * from ops.activate_context_bundle(%s,%s,%s,%s)",
        (work_ref, plan_ref, Jsonb(tampered), uuid.uuid4()),
        "required frozen item classification/ref alteration",
    )


def assert_frozen_revisions_render_exactly(cur, work_ref: str, binding_ref: str, bundle: dict) -> None:
    """Fresh-session rendering must use the frozen revision, never current text."""
    rendered = cur.execute(
        "select ops.render_context_activation_for_brief(%s,%s)",
        (work_ref, binding_ref),
    ).fetchone()[0]
    rendered_by_ref = {item["canonical_ref"]: item for item in rendered}
    for frozen in bundle["items"]:
        item = rendered_by_ref.get(frozen["canonical_ref"])
        if frozen["required"]:
            if item is None or item.get("state") != "rendered":
                raise RuntimeError(f"required frozen revision did not render: {frozen['canonical_ref']}")
            if item.get("revision") != frozen["revision"] or item.get("content_digest") != frozen["digest"]:
                raise RuntimeError(f"renderer silently substituted a current revision: {frozen['canonical_ref']}")


def assert_stale_work_request_refuses_all_activation_admission(
    cur, work_ref: str, request_id: object, binding_ref: str, plan_hash: str,
    envelope_digest: str, binding_pk: object, receipt: dict,
) -> None:
    """A frozen binding never silently follows a later WR/plan revision."""
    global REFUSAL_ASSERTIONS
    cur.execute("savepoint stale_work_request_binding")
    try:
        # Program 6 rightly prevents arbitrary production WR updates.  This
        # rollback-only fixture needs the otherwise-unreachable historical
        # state solely to prove every admission door refuses it.
        cur.execute("set local session_replication_role = replica")
        cur.execute("update ops.work_request set version=version+1 where id=%s", (request_id,))
        cur.execute("set local session_replication_role = origin")
        for sql, params in (
            ("select ops.render_context_activation_for_brief(%s,%s)", (work_ref, binding_ref)),
            ("select ops.context_activation_brief_assignment(%s,%s)", (work_ref, binding_ref)),
            ("select * from ops.issue_execution_envelope_v1(%s,%s,%s)", (work_ref, binding_ref, uuid.uuid4())),
            ("select * from ops.record_attempt_receipt(%s,%s,%s,%s,%s,%s)", (work_ref, plan_hash, envelope_digest, binding_pk, Jsonb(receipt), uuid.uuid4())),
        ):
            try:
                cur.execute(sql, params)
            except psycopg.Error:
                REFUSAL_ASSERTIONS += 1
                continue
            raise RuntimeError(f"stale Work Request version was admitted by {sql}")
    finally:
        cur.execute("rollback to savepoint stale_work_request_binding")


def assert_joined_mcp_db_browser_path(
    canonical_projection: dict,
    activation_read_projection: dict,
    admitted_receipt: dict,
    observatory_projection: dict,
) -> dict:
    joined = subprocess.run(
        ["node", str(ROOT / "mcp-server" / "test" / "job-passport-panel.test.mjs"), "--evidence-activation-joined-path"],
        input=json.dumps({
            "db_projection": canonical_projection,
            "activation_read_projection": activation_read_projection,
            "admitted_receipt": admitted_receipt,
            "observatory_projection": observatory_projection,
        }),
        text=True,
        capture_output=True,
        cwd=ROOT,
        check=False,
    )
    if joined.returncode != 0:
        raise RuntimeError(f"joined MCP -> DB read -> browser helper failed: {joined.stderr or joined.stdout}")
    try:
        joined_result = json.loads(joined.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"joined helper returned non-JSON output: {joined.stdout}") from exc
    if joined_result.get("ok") is not True:
        raise RuntimeError(f"joined MCP -> DB read -> browser assertion failed: {joined_result}")
    return joined_result


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("evidence-activation-db-gate: FAIL — DATABASE_URL is required", file=sys.stderr)
        return 1
    try:
        p6 = _program6_helpers()
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            p6.ensure_authority_roles(cur)
            _assert_provider_transition_serializes(dsn)
            joe_id = cur.execute(
                "select id from actor where slug='joe' and active and kind='human'"
            ).fetchone()[0]
            # The committed schema snapshot carries profile structure and the
            # pre-0300 seed row, while the disposable migration harness marks
            # already-applied data migrations as complete. Reproduce 0300's
            # canonical active Builder projection explicitly for this rolled-
            # back acceptance fixture; production received it through 0300.
            cur.execute(
                """insert into agent_profile
                       (profile_key,display_name,charter,status,current_model,current_desk)
                     values
                       ('builder','Builder','[]'::jsonb,'active',
                        'openrouter/stealth/ox-alpha','hermes-desktop')
                     on conflict (profile_key) do update
                       set status='active', current_model=excluded.current_model,
                           current_desk=excluded.current_desk,
                           version=agent_profile.version+1, updated_at=now()"""
            )
            if cur.rowcount != 1:
                raise RuntimeError("active canonical Builder profile fixture is unavailable")
            source_section, source_revision, origin_ref, _, _, runbook_ref = p6.doctrine_fixture(cur, joe_id)

            set_local_role(cur, "carr_writer")
            request_id, ref, _, captured_version = p6.capture(
                cur, source_section, source_revision, origin_ref, "activation"
            )
            cur.execute("reset role")
            triaged = p6.triage(cur, ref, captured_version, "joe")
            proposal = p6.propose(cur, ref, triaged[3], runbook_ref, uuid.uuid4())
            cur.execute("set session authorization carr_authority_joe")
            accepted = cur.execute(
                "select * from ops.accept_sourced_work_request_plan(%s,%s,%s,%s)",
                (ref, triaged[3], proposal[2], uuid.uuid4()),
            ).fetchone()
            cur.execute("reset session authorization")
            if accepted[2] != "ready" or accepted[5] != proposal[1]:
                raise RuntimeError(f"canonical plan acceptance failed: {accepted}")

            # The compiler is server-derived: callers submit only WR/plan refs
            # and tenant context, never required flags, revisions, or bodies.
            set_local_role(cur, "carr_writer")
            cur.execute("select set_config('carr.organization_tenant_id','carr-internal',true)")
            bundle = cur.execute(
                "select ops.compile_context_bundle(%s,%s,%s)",
                (ref, proposal[1], "carr-internal"),
            ).fetchone()[0]
            first = cur.execute(
                "select * from ops.activate_context_bundle(%s,%s,%s,%s)",
                (ref, proposal[1], Jsonb(bundle), uuid.uuid4()),
            ).fetchone()
            replay_key = uuid.uuid4()
            # First activation under a stable idempotency key.
            replay_first = cur.execute(
                "select * from ops.activate_context_bundle(%s,%s,%s,%s)",
                (ref, proposal[1], Jsonb(bundle), replay_key),
            ).fetchone()
            replay_second = cur.execute(
                "select * from ops.activate_context_bundle(%s,%s,%s,%s)",
                (ref, proposal[1], Jsonb(bundle), replay_key),
            ).fetchone()
            cur.execute("reset role")
            if first[1] != bundle["bundle_digest"] or replay_first[2] is not False:
                raise RuntimeError("compiled bundle did not activate against accepted plan")
            if replay_second[:2] != replay_first[:2] or replay_second[2] is not True:
                raise RuntimeError("activation replay did not return the immutable binding")

            set_local_role(cur, "carr_writer")
            cur.execute("select set_config('carr.organization_tenant_id','carr-internal',true)")
            assert_frozen_revisions_render_exactly(cur, ref, replay_first[0], bundle)
            cur.execute("reset role")

            tampered = dict(bundle)
            tampered_header = dict(tampered["header"])
            tampered_header["tenant_id"] = "tenant-forgery"
            tampered["header"] = tampered_header
            set_local_role(cur, "carr_writer")
            cur.execute("select set_config('carr.organization_tenant_id','carr-internal',true)")
            refusal(
                cur,
                "select * from ops.activate_context_bundle(%s,%s,%s,%s)",
                (ref, proposal[1], Jsonb(tampered), uuid.uuid4()),
                "tampered tenant bundle",
            )
            assert_required_item_ref_and_classification_frozen(cur, ref, proposal[1], bundle)
            privileges = cur.execute(
                "select has_table_privilege('carr_writer','ops.context_activation_binding','INSERT'), "
                "has_table_privilege('carr_writer','ops.context_activation_item','INSERT')"
            ).fetchone()
            cur.execute("reset role")
            if privileges != (False, False):
                raise RuntimeError(f"raw activation table INSERT leaked: {privileges}")

            binding = cur.execute(
                "select bundle_digest,plan_hash,work_request_id from ops.context_activation_binding where binding_id=%s",
                (replay_first[0],),
            ).fetchone()
            if binding is None or binding[0] != bundle["bundle_digest"] or binding[1] != proposal[2] or binding[2] != request_id:
                raise RuntimeError("activation readback lost exact plan/WR binding")
            readback = cur.execute(
                "select ops.read_context_activation(%s,%s)", (ref, replay_first[0])
            ).fetchone()[0]
            register = readback.get("evidence_register", {}) if isinstance(readback, dict) else {}
            required_register_fields = {
                "source_ref", "source_digest", "admission_ref", "retrieval_evidence_ref",
                "operator_surface", "telemetry_ref", "canary", "rollback_ref", "freshness", "items",
            }
            if set(register) != required_register_fields | {"work_request_ref"} or not register["items"]:
                raise RuntimeError("activation read projection omitted source-linked evidence register")
            if register["canary"].get("evidence_availability") != "not_recorded":
                raise RuntimeError("activation read projection invented canary evidence")

            # The extension persists the existing AttemptReceipt v1 rather
            # than a parallel opaque facts object.  This fixture is the
            # repository's complete strict receipt shape; the server door also
            # rejects raw fields before an append can occur.
            receipt = json.loads((ROOT / "control-room" / "contracts" / "fixtures" / "execution-fabric" / "codex_desktop.attempt-receipt.v1.json").read_text())
            receipt["attempt_id"] = "attempt:evidence-activation-db"
            refusal(
                cur,
                "select * from ops.issue_execution_envelope_v1(%s,%s,%s)",
                (ref, replay_first[0], uuid.uuid4()),
                "unassigned execution profile lane",
            )
            cur.execute("set session authorization carr_writer")
            cur.execute("select set_config('carr.organization_tenant_id','carr-internal',true)")
            refusal(
                cur,
                "select * from ops.assign_execution_profile(%s,%s,%s,%s,%s,%s)",
                (ref, "builder", "rehearsal", "policy:execution-lane-v1", "sha256:" + "c" * 64, uuid.uuid4()),
                "non-authority execution profile assignment",
            )
            refusal(
                cur,
                "select * from ops.register_execution_environment_provider(%s,%s)",
                (Jsonb(_plugin_manifest("fixture-remote")), uuid.uuid4()),
                "non-authority execution environment registration",
            )
            cur.execute("reset session authorization")
            cur.execute("set session authorization carr_authority_joe")
            cur.execute("select set_config('carr.organization_tenant_id','carr-internal',true)")
            refusal(
                cur,
                "select * from ops.register_execution_environment_provider(%s,%s)",
                (Jsonb(_plugin_manifest("hermes-local")), uuid.uuid4()),
                "plugin shadow of protected built-in provider",
            )
            fixture_manifest = _plugin_manifest("fixture-remote")
            quarantined = cur.execute(
                "select * from ops.register_execution_environment_provider(%s,%s)",
                (Jsonb(fixture_manifest), uuid.uuid4()),
            ).fetchone()
            if quarantined is None or quarantined[2] != "discovered" or quarantined[3] is not False:
                raise RuntimeError("provider registration did not remain discovered and unpromoted")
            quarantined_transition = cur.execute(
                "select * from ops.transition_execution_environment_provider(%s,%s,%s,%s,%s)",
                (quarantined[0], "discovered", "quarantined", Jsonb(["evidence:fixture-review"]), uuid.uuid4()),
            ).fetchone()
            if quarantined_transition is None or quarantined_transition[1] != "quarantined":
                raise RuntimeError("provider did not enter quarantine before conformance")
            refusal(
                cur,
                "select * from ops.transition_execution_environment_provider(%s,%s,%s,%s,%s)",
                (quarantined[0], "quarantined", "conformance_passed", Jsonb(["evidence:fixture-review"]), uuid.uuid4()),
                "provider promotion without passed conformance",
            )
            conformance_key = uuid.uuid4()
            passed_at = cur.execute("select clock_timestamp()-interval '1 second'").fetchone()[0]
            passed_observation = _conformance_observation(
                quarantined[0], fixture_manifest, passed_at,
                run_ref="conformance-run:fixture-passed", status="passed",
                evidence_refs=["evidence:fixture-conformance"],
            )
            conformance = cur.execute(
                "select * from ops.attest_execution_environment_conformance(%s,%s,%s)",
                (quarantined[0], Jsonb(passed_observation), conformance_key),
            ).fetchone()
            if conformance is None or conformance[1] is not False:
                raise RuntimeError("provider conformance was not appended")
            refusal(
                cur,
                "select * from ops.attest_execution_environment_conformance(%s,%s,%s)",
                (quarantined[0], Jsonb({**passed_observation, "evidence_refs": ["evidence:changed"]}), conformance_key),
                "provider conformance idempotency evidence conflict",
            )
            forged_observation = json.loads(json.dumps(passed_observation))
            forged_observation["implementation_digest"] = "sha256:" + "f" * 64
            forged_body = {key: value for key, value in forged_observation.items() if key not in {"run_digest", "observed_at"}}
            forged_observation["run_digest"] = "sha256:" + hashlib.sha256(_canonical_json(forged_body).encode()).hexdigest()
            refusal(
                cur,
                "select * from ops.attest_execution_environment_conformance(%s,%s,%s)",
                (quarantined[0], Jsonb(forged_observation), uuid.uuid4()),
                "provider conformance with forged implementation digest",
            )
            conformance_passed = cur.execute(
                "select * from ops.transition_execution_environment_provider(%s,%s,%s,%s,%s)",
                (quarantined[0], "quarantined", "conformance_passed", Jsonb(["evidence:fixture-conformance"]), uuid.uuid4()),
            ).fetchone()
            if conformance_passed is None or conformance_passed[1] != "conformance_passed":
                raise RuntimeError("passed conformance did not permit the human lifecycle transition")
            failed_at = cur.execute("select clock_timestamp()").fetchone()[0]
            failed_observation = _conformance_observation(
                quarantined[0], fixture_manifest, failed_at,
                run_ref="conformance-run:fixture-regression", status="failed",
                evidence_refs=["evidence:fixture-regression"],
            )
            failed_observation["implementation_digest"] = "sha256:" + "d" * 64
            failed_observation["package_digest"] = "sha256:" + "e" * 64
            failed_observation["contains_secrets"] = True
            failed_observation["check_results"].update({
                "check:implementation-digest-exact": False,
                "check:package-provenance-exact": False,
                "check:source-secret-scan": False,
            })
            failed_body = {key: value for key, value in failed_observation.items() if key not in {"run_digest", "observed_at"}}
            failed_observation["run_digest"] = "sha256:" + hashlib.sha256(_canonical_json(failed_body).encode()).hexdigest()
            cur.execute(
                "select * from ops.attest_execution_environment_conformance(%s,%s,%s)",
                (quarantined[0], Jsonb(failed_observation), uuid.uuid4()),
            )
            refusal(
                cur,
                "select * from ops.transition_execution_environment_provider(%s,%s,%s,%s,%s)",
                (quarantined[0], "conformance_passed", "shadow", Jsonb(["evidence:fixture-regression"]), uuid.uuid4()),
                "provider promotion after latest conformance regression",
            )
            assignment = cur.execute(
                "select * from ops.assign_execution_profile(%s,%s,%s,%s,%s,%s)",
                (ref, "builder", "rehearsal", "policy:execution-lane-v1", "sha256:" + "c" * 64, uuid.uuid4()),
            ).fetchone()
            cur.execute("reset session authorization")
            set_local_role(cur, "carr_writer")
            cur.execute("select set_config('carr.organization_tenant_id','carr-internal',true)")
            if assignment is None or assignment[1] is not False:
                raise RuntimeError("authoritative policy gateway did not create execution assignment")
            providers = cur.execute("select ops.read_execution_environment_providers()").fetchone()[0]
            local_provider = next((row for row in providers if row.get("provider_ref") == "environment-provider:hermes-local:v1"), None)
            if not local_provider or local_provider.get("state") != "active" or local_provider.get("conformance", {}).get("state") != "passed" or local_provider.get("grants_authority") is not False:
                raise RuntimeError(f"reference execution environment provider is not active, conformant, and non-authoritative: {providers}")
            local_conformance = local_provider["conformance"]
            if (
                local_provider.get("manifest_digest") != "sha256:9f1ac4e93a50163aef414f4084046e3e0740332e15c59baca0ef8ed289fcd6c8"
                or local_conformance.get("run_digest") != "sha256:d9f3f6e889f7630b0f503db4fee66acc96fc21c30b9b7110e484c85910731333"
                or local_conformance.get("implementation_digest") != "sha256:7d680c252bedc88ff7b80d50a5bfbdb9b926823d8bbc521f606e7b58237cbc1e"
                or local_conformance.get("manifest_digest") != local_provider.get("manifest_digest")
            ):
                raise RuntimeError(f"reference provider database attestation diverged from the exact installed probe: {local_provider}")
            issued_envelope = cur.execute(
                "select * from ops.issue_execution_envelope_v1(%s,%s,%s)",
                (ref, replay_first[0], uuid.uuid4()),
            ).fetchone()
            if issued_envelope is None or issued_envelope[3] is not False:
                raise RuntimeError("server did not issue immutable ExecutionEnvelope v1")
            execution_contract.validate_execution_envelope(issued_envelope[2])
            if issued_envelope[2]["runtime_profile"].get("profile_key") != "builder" or issued_envelope[2]["server_binding"]["authority"]["environment"] != "rehearsal":
                raise RuntimeError("ExecutionEnvelope did not derive its assigned profile/environment")
            runtime_environment = issued_envelope[2]["runtime_profile"]
            if runtime_environment.get("environment_provider_ref") != "environment-provider:hermes-local:v1" or runtime_environment.get("environment_source_class") != "built_in" or runtime_environment.get("environment_binding_digest") is None:
                raise RuntimeError("ExecutionEnvelope did not bind the exact admitted execution environment")
            registration = cur.execute(
                "select ops.hermes_runtime_admission_for_brief(%s,%s,%s,%s,%s)",
                ("hermes-pilot", "builder", "joe", ref, replay_first[0]),
            ).fetchone()[0]
            expected_registration = {
                "status": "registered",
                "authorized": True,
                "registration_scope": "execution_envelope",
                "runtime_principal": "runtime:builder",
                "agent_principal_id": "agent:builder",
                "organization_tenant_id": "carr-internal",
                "sponsoring_human_slug": "joe",
                "work_request": ref,
                "activation_binding_id": replay_first[0],
                "profile_version": issued_envelope[2]["runtime_profile"]["profile_version"],
                "surface": "hermes_desktop",
                "adapter_id": "adapter:hermes-desktop",
                "read_only": True,
                "grants_authority": False,
                "device_binding_status": "not_asserted",
                "envelope_digest": issued_envelope[1],
                "environment_provider_ref": runtime_environment["environment_provider_ref"],
                "environment_binding_digest": runtime_environment["environment_binding_digest"],
                "environment_conformance_digest": runtime_environment["environment_conformance_digest"],
            }
            for key, expected in expected_registration.items():
                if registration.get(key) != expected:
                    raise RuntimeError(f"Hermes runtime admission lost exact {key}: {registration}")
            for runtime_slug, profile_key, sponsor_slug, expected_status in (
                ("missing-runtime", "builder", "joe", "not_registered"),
                ("codex-reviewer", "builder", "joe", "not_registered"),
                ("hermes-pilot", "deal-steward", "joe", "stale"),
                ("hermes-pilot", "builder", "dell", "stale"),
            ):
                refused = cur.execute(
                    "select ops.hermes_runtime_admission_for_brief(%s,%s,%s,%s,%s)",
                    (runtime_slug, profile_key, sponsor_slug, ref, replay_first[0]),
                ).fetchone()[0]
                if refused.get("authorized") is not False or refused.get("status") != expected_status:
                    raise RuntimeError(f"Hermes runtime admission accepted altered server binding: {refused}")
            issued_replay = cur.execute(
                "select * from ops.issue_execution_envelope_v1(%s,%s,%s)",
                (ref, replay_first[0], uuid.UUID("11111111-1111-4111-8111-111111111111")),
            ).fetchone()
            # A second fixed request key is used below only to prove the
            # no-mutable-server-metadata replay property.
            issued_replay_again = cur.execute(
                "select * from ops.issue_execution_envelope_v1(%s,%s,%s)",
                (ref, replay_first[0], uuid.UUID("11111111-1111-4111-8111-111111111111")),
            ).fetchone()
            if issued_replay[1] != issued_replay_again[1] or issued_replay_again[3] is not True:
                raise RuntimeError("server-issued ExecutionEnvelope v1 replay was not immutable")
            # Human acceptance/outcome is deliberately re-used from Program 6,
            # not claimed by the executor receipt.  The disposable fixture may
            # create an authority-accepted feedback row; production must wait
            # for a real outcome horizon and report unavailable until then.
            work_version, criteria = cur.execute(
                "select version,acceptance_criteria from ops.work_request where id=%s", (request_id,)
            ).fetchone()
            feedback = cur.execute(
                "select * from ops.propose_sourced_work_request_outcome_feedback(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (ref, work_version, proposal[2], Jsonb([{"id": item["id"], "result": "met"} for item in criteria]),
                 Jsonb(["safe:evidence:activation-canary"]), "none", "Observed fixture result", 1, "mcp", False, 0, uuid.uuid4()),
            ).fetchone()
            cur.execute("reset role")
            cur.execute("set session authorization carr_authority_joe")
            accepted_feedback = cur.execute(
                "select * from ops.accept_sourced_work_request_outcome_feedback(%s,%s,%s,%s)",
                (ref, work_version, feedback[2], uuid.uuid4()),
            ).fetchone()
            cur.execute("reset session authorization")
            set_local_role(cur, "carr_writer")
            cur.execute("select set_config('carr.organization_tenant_id','carr-internal',true)")
            if accepted_feedback is None or accepted_feedback[5] != feedback[1] or accepted_feedback[6] != feedback[2]:
                raise RuntimeError("fixture lacked authoritative accepted Work Request outcome feedback")
            receipt["envelope_digest"] = issued_envelope[1]
            receipt["result"]["job_ref"] = f"job:{ref}"
            receipt["knowledge_activation"] = {
                "bundle_digest": bundle["bundle_digest"],
                "canonical_binding": {"work_request_id": ref, "work_request_version": work_version, "accepted_plan_digest": proposal[2], "envelope_digest": issued_envelope[1], "activation_binding_ref": replay_first[0]},
                "item_dispositions": [
                    {"item_ref": item["canonical_ref"], "disposition": "applied", "evidence_refs": ["evidence:activation"], "stage_ref": "stage:activation", "reason_ref": "reason:server-bound-fixture"}
                    for item in bundle["items"]
                ],
                "closure": {"state": "not_activated", "unresolved_required_item_refs": [], "derived_by": "server"},
                "mode": "shadow",
            }
            # The existing receipt remains the persisted object; these are
            # its strict, redacted reliability extension fields.  This is a
            # one-Work-Request shadow/canary admission fixture, not a claim
            # that production mode has been enabled.
            receipt["reliability"] = {
                "grounding_sufficiency": {"state": "sufficient", "evidence_refs": ["evidence:grounding"], "required_supplied": [item["canonical_ref"] for item in bundle["items"]], "required_used": [item["canonical_ref"] for item in bundle["items"]], "required_missing": [], "advisory_supplied": [], "advisory_used": [], "freshness_failures": [], "retrieval_failures": []},
                "deterministic_checks": [{"check_id": check_ref, "state": "passed", "critical": True, "evidence_refs": ["evidence:binding"]} for check_ref in issued_envelope[2]["evaluation_plan"]["required_deterministic_check_refs"]],
                "model_judgement": {"state": "pass", "judge_ref": "actor:model-judge", "evidence_refs": ["evidence:judge"]},
                "human_acceptance": {"state": "accepted", "actor_ref": "actor:joe", "evidence_refs": ["evidence:human"], "outcome_feedback_ref": feedback[1], "outcome_feedback_hash": feedback[2]},
                "trajectory": [{"sequence": 1, "stage_ref": "stage:activation", "parent_event_ref": None, "decision_class": "decision:fixture", "tool_class": "tool:metadata", "result_state": "succeeded", "fallback_state": "not_used", "guardrail_state": "clear", "latency_ms": 1, "evidence_refs": ["evidence:trajectory"]}],
                "evaluator_results": [{"kind": "deterministic", "evaluator_ref": "evaluator:deterministic", "rubric_ref": "rubric:fixture", "evaluator_version": "v1", "evaluator_digest": "sha256:" + "1" * 64, "status": "passed", "confidence": "high", "critical": True, "independence_state": "not_independent", "held_out_case_count": 1, "check_refs": [check_ref], "dimension_refs": issued_envelope[2]["evaluation_plan"]["critical_dimensions"], "evidence_refs": ["evidence:binding"], "judge_provenance": "provenance:deterministic", "calibration_evidence_refs": []} for check_ref in issued_envelope[2]["evaluation_plan"]["required_deterministic_check_refs"]] + [{"kind": "judge", "evaluator_ref": "evaluator:judge", "rubric_ref": "rubric:fixture", "evaluator_version": "v1", "evaluator_digest": "sha256:" + "2" * 64, "status": "passed", "confidence": "high", "critical": False, "independence_state": "not_independent", "held_out_case_count": 1, "check_refs": [], "dimension_refs": issued_envelope[2]["evaluation_plan"]["critical_dimensions"], "evidence_refs": ["evidence:judge"], "judge_provenance": "provenance:judge", "calibration_evidence_refs": ["evidence:judge-calibration"]}, {"kind": "human_acceptance", "evaluator_ref": "evaluator:human", "rubric_ref": "rubric:fixture", "evaluator_version": "v1", "evaluator_digest": "sha256:" + "3" * 64, "status": "passed", "confidence": "high", "critical": False, "independence_state": "not_independent", "held_out_case_count": 1, "check_refs": [], "dimension_refs": issued_envelope[2]["evaluation_plan"]["critical_dimensions"], "evidence_refs": ["evidence:human"], "judge_provenance": "provenance:human", "calibration_evidence_refs": []}],
                "corrections": [{"event_ref": "correction:activation-fixture", "kind": "correction", "evidence_refs": ["evidence:correction"], "summary": "taxonomy:fixture"}], "defects": [], "incidents": [],
                "downstream_outcome": {"state": "observed", "brokerage_ref": "deal:pending", "evidence_refs": ["evidence:outcome"], "outcome_feedback_ref": feedback[1], "outcome_feedback_hash": feedback[2]},
                "outcome_horizon": {"state": "mature", "ends_at": "2026-08-24T12:00:00Z", "as_of": "2026-08-24T12:00:00Z", "evidence_refs": ["evidence:horizon"]},
                "process_metrics": {"latency_ms": 1, "cost_usd": 0, "input_tokens": 1, "output_tokens": 1, "cached_input_tokens": 0, "retry_count": 0, "recovery_count": 0, "context_reconstruction_ms": 0, "human_intervention_count": 0, "security_event_refs": []},
                "eval_candidates": [], "shadow_comparisons": [],
                "telemetry": [],
                "route_digest": issued_envelope[2]["runtime_profile"]["digest"],
                "topology_digest": issued_envelope[2]["execution_topology"]["digest"],
                "evaluation_plan_digest": issued_envelope[2]["evaluation_plan"]["digest"],
                "environment_binding_digest": runtime_environment["environment_binding_digest"],
                "environment_evidence": {
                    "binding_digest": runtime_environment["environment_binding_digest"],
                    "session_ref": "environment-session:activation-canary",
                    "lease_state": "released",
                    "operation_count": 1,
                    "policy_refusal_refs": [],
                    "security_event_refs": [],
                    "cleanup_state": "verified",
                    "cleanup_evidence_refs": ["evidence:environment-cleanup"],
                    "side_effect_state": "none",
                    "resource_usage": {"cpu_ms": 1, "memory_peak_mb": 1, "disk_peak_mb": 0, "network_egress_bytes": 0},
                    "evidence_refs": ["evidence:environment-session"],
                },
                "learning_disposition": "none", "closure": {"state": "insufficient_evidence", "reasons": ["reason:authority_evaluation_evidence_missing"], "derived_by": "server"},
            }
            # Raw tables stay unavailable to application writers; return to
            # the harness owner only for this readback assertion.
            cur.execute("reset role")
            binding_pk = cur.execute(
                "select id from ops.context_activation_binding where binding_id=%s", (replay_first[0],)
            ).fetchone()[0]
            recorded = cur.execute(
                "select * from ops.record_attempt_receipt(%s,%s,%s,%s,%s,%s)",
                (ref, proposal[2], receipt["envelope_digest"], binding_pk, Jsonb(receipt), uuid.uuid4()),
            ).fetchone()
            if recorded[1] != receipt["attempt_id"] or recorded[2] is not False:
                raise RuntimeError("existing AttemptReceipt was not persisted by the strict door")
            forged_environment = json.loads(json.dumps(receipt))
            forged_environment["attempt_id"] = "attempt:evidence-activation-forged-environment"
            forged_environment["reliability"]["environment_evidence"]["binding_digest"] = "sha256:" + "f" * 64
            refusal(
                cur,
                "select * from ops.record_attempt_receipt(%s,%s,%s,%s,%s,%s)",
                (ref, proposal[2], receipt["envelope_digest"], binding_pk, Jsonb(forged_environment), uuid.uuid4()),
                "forged execution environment binding",
            )
            missing_cleanup = json.loads(json.dumps(receipt))
            missing_cleanup["attempt_id"] = "attempt:evidence-activation-missing-cleanup"
            missing_cleanup["reliability"]["environment_evidence"]["cleanup_evidence_refs"] = []
            refusal(
                cur,
                "select * from ops.record_attempt_receipt(%s,%s,%s,%s,%s,%s)",
                (ref, proposal[2], receipt["envelope_digest"], binding_pk, Jsonb(missing_cleanup), uuid.uuid4()),
                "verified environment cleanup without evidence",
            )
            forged_resource = json.loads(json.dumps(receipt))
            forged_resource["attempt_id"] = "attempt:evidence-activation-forged-resource"
            forged_resource["reliability"]["environment_evidence"]["resource_usage"]["cpu_ms"] = 1.5
            refusal(
                cur,
                "select * from ops.record_attempt_receipt(%s,%s,%s,%s,%s,%s)",
                (ref, proposal[2], receipt["envelope_digest"], binding_pk, Jsonb(forged_resource), uuid.uuid4()),
                "fractional execution environment resource evidence",
            )
            raw_environment_ref = json.loads(json.dumps(receipt))
            raw_environment_ref["attempt_id"] = "attempt:evidence-activation-raw-environment-ref"
            raw_environment_ref["reliability"]["environment_evidence"]["policy_refusal_refs"] = ["raw provider refusal sentence"]
            refusal(
                cur,
                "select * from ops.record_attempt_receipt(%s,%s,%s,%s,%s,%s)",
                (ref, proposal[2], receipt["envelope_digest"], binding_pk, Jsonb(raw_environment_ref), uuid.uuid4()),
                "raw execution environment evidence reference",
            )
            initial_reliability = cur.execute(
                "select ops.read_attempt_receipt_reliability(%s)", (receipt["attempt_id"],)
            ).fetchone()[0]
            initial_revision = initial_reliability.get("canonical_revision", {})
            if initial_reliability["reliability"]["state"] != "insufficient_evidence" or initial_revision.get("authority_fact_count") != 0 or initial_revision.get("learning_event_count") != 0 or not isinstance(initial_revision.get("outcome_horizon_mature"), bool):
                raise RuntimeError(f"canonical reliability did not start as server-derived insufficient evidence: {initial_reliability}")
            assert_stale_work_request_refuses_all_activation_admission(
                cur, ref, request_id, replay_first[0], proposal[2], receipt["envelope_digest"], binding_pk, receipt,
            )
            # Executor claims remain insufficient.  Only authority-attested
            # exact plan checks, independent judge, Program-6 human outcome,
            # and a mature horizon can make this attempt eligible for review.
            cur.execute("reset role")
            cur.execute("set session authorization carr_authority_joe")
            cur.execute("select set_config('carr.organization_tenant_id','carr-internal',true)")
            dimensions = issued_envelope[2]["evaluation_plan"]["critical_dimensions"]
            evaluation_metadata = {
                "evaluator_ref": issued_envelope[2]["evaluation_plan"]["evaluator_ref"],
                "rubric_ref": issued_envelope[2]["evaluation_plan"]["rubric_ref"],
                "evaluator_version": issued_envelope[2]["evaluation_plan"]["evaluator_version"],
                "evaluator_digest": issued_envelope[2]["evaluation_plan"]["evaluator_digest"],
                "confidence": "high",
                "held_out_case_count": 1,
                "calibration_refs": ["evidence:authority-calibration"],
                "lower_bound_ref": "evidence:authority-lower-bound",
            }
            for check_ref in issued_envelope[2]["evaluation_plan"]["required_deterministic_check_refs"]:
                cur.execute("select * from ops.attest_attempt_receipt_evaluation(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (receipt["attempt_id"], "deterministic", check_ref, Jsonb(dimensions), "passed", False, Jsonb(["evidence:authority-deterministic"]), Jsonb(evaluation_metadata), None, None, uuid.uuid4()))
            for kind, status, independent, evidence in (("judge", "passed", True, "evidence:authority-judge"), ("human_acceptance", "passed", False, "evidence:authority-human"), ("outcome_horizon", "mature", False, "evidence:authority-horizon")):
                feedback_ref = feedback[1] if kind in ("human_acceptance", "outcome_horizon") else None
                feedback_hash = feedback[2] if kind in ("human_acceptance", "outcome_horizon") else None
                cur.execute("select * from ops.attest_attempt_receipt_evaluation(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (receipt["attempt_id"], kind, "", Jsonb(dimensions), status, independent, Jsonb([evidence]), Jsonb(evaluation_metadata), feedback_ref, feedback_hash, uuid.uuid4()))
            cur.execute("reset session authorization")
            set_local_role(cur, "carr_writer")
            cur.execute("select set_config('carr.organization_tenant_id','carr-internal',true)")
            canonical_reliability = cur.execute("select ops.read_attempt_receipt_reliability(%s)", (receipt["attempt_id"],)).fetchone()[0]
            if canonical_reliability["reliability"]["state"] != "eligible_for_human_review" or canonical_reliability["reliability"]["reasons"]:
                raise RuntimeError("authority evaluation attestations did not derive eligible human-review posture")
            if canonical_reliability["canonical_revision"]["authority_fact_count"] <= initial_reliability["canonical_revision"]["authority_fact_count"] or not canonical_reliability["canonical_revision"]["outcome_horizon_mature"]:
                raise RuntimeError("canonical reliability did not advance via authority facts and derived horizon")
            replay_candidate_key = uuid.uuid4()
            candidate = cur.execute(
                "select * from ops.propose_eval_candidate(%s,%s,%s)",
                (receipt["attempt_id"], "correction:activation-fixture", replay_candidate_key),
            ).fetchone()
            replay_candidate_again = cur.execute(
                "select * from ops.propose_eval_candidate(%s,%s,%s)",
                (receipt["attempt_id"], "correction:activation-fixture", replay_candidate_key),
            ).fetchone()
            if candidate is None or candidate[2] != "proposed" or candidate[3] != "not_promoted" or replay_candidate_again[0] != candidate[0] or replay_candidate_again[4] is not True:
                raise RuntimeError("canonical correction did not create exactly one replay-safe proposed eval case")
            learning_revision = cur.execute("select ops.read_attempt_receipt_reliability(%s)", (receipt["attempt_id"],)).fetchone()[0]["canonical_revision"]
            if learning_revision["learning_event_count"] <= canonical_reliability["canonical_revision"]["learning_event_count"]:
                raise RuntimeError("canonical reliability revision did not advance from the append-only learning event")
            cur.execute("reset role")
            cur.execute("set session authorization carr_authority_joe")
            triaged_candidate = cur.execute(
                "select * from ops.transition_proposed_eval_candidate(%s,%s,%s,%s,%s)",
                (ref, candidate[1], "triaged", Jsonb({"reason": "reviewed"}), uuid.uuid4()),
            ).fetchone()
            accepted_candidate = cur.execute(
                "select * from ops.transition_proposed_eval_candidate(%s,%s,%s,%s,%s)",
                (ref, candidate[1], "accepted", Jsonb({"reason": "accepted"}), uuid.uuid4()),
            ).fetchone()
            cur.execute("reset session authorization")
            set_local_role(cur, "carr_writer")
            cur.execute("select set_config('carr.organization_tenant_id','carr-internal',true)")
            if triaged_candidate[1] != "triaged" or triaged_candidate[2] is True or accepted_candidate[1] != "accepted" or accepted_candidate[2] is not True:
                raise RuntimeError("human evaluation lifecycle did not create an accepted golden membership")
            learning_readback = cur.execute("select ops.read_context_activation(%s,%s)", (ref, replay_first[0])).fetchone()[0]["learning"]
            if len(learning_readback) != 1 or learning_readback[0]["lifecycle"] != "accepted" or not learning_readback[0]["golden_membership"]["active"]:
                raise RuntimeError(f"learning projection did not expose accepted source-linked membership: {learning_readback}")
            canonical_projection = cur.execute(
                "select ops.read_attempt_receipt_reliability(%s)", (receipt["attempt_id"],)
            ).fetchone()[0]
            activation_read_projection = cur.execute(
                "select ops.read_context_activation(%s,%s)", (ref, replay_first[0])
            ).fetchone()[0]
            observatory_projection = json.loads(
                (ROOT / "control-room" / "contracts" / "fixtures" / "execution-fabric" / "codex_desktop.observatory-projection.v1.json").read_text()
            )
            assert_joined_mcp_db_browser_path(
                canonical_projection, activation_read_projection, receipt, observatory_projection
            )
            cur.execute("reset role")
            cur.execute("set session authorization carr_authority_joe")
            retired_candidate = cur.execute(
                "select * from ops.transition_proposed_eval_candidate(%s,%s,%s,%s,%s)",
                (ref, candidate[1], "retired", Jsonb({"reason": "superseded"}), uuid.uuid4()),
            ).fetchone()
            cur.execute("reset session authorization")
            set_local_role(cur, "carr_writer")
            cur.execute("select set_config('carr.organization_tenant_id','carr-internal',true)")
            retired_learning = cur.execute("select ops.read_context_activation(%s,%s)", (ref, replay_first[0])).fetchone()[0]["learning"]
            if retired_candidate[1] != "retired" or retired_candidate[2] is True or retired_learning[0]["golden_membership"]["active"]:
                raise RuntimeError("retired golden membership was not append-only but effectively inactive")
            refusal(
                cur, "select * from ops.transition_proposed_eval_candidate(%s,%s,%s,%s,%s)",
                (ref, candidate[1], "accepted", Jsonb({"reason": "skip"}), uuid.uuid4()),
                "retired evaluation candidate cannot replay or skip lifecycle",
            )
            cross_tenant = cur.execute("select set_config('carr.organization_tenant_id','tenant-other',true)")
            foreign_read = cur.execute("select ops.read_context_activation(%s,%s)", (ref, replay_first[0])).fetchone()[0]
            if foreign_read is not None:
                raise RuntimeError("cross-tenant activation/evaluation read leaked")
            refusal(
                cur, "select * from ops.propose_eval_candidate(%s,%s,%s)",
                (receipt["attempt_id"], "correction:activation-fixture", uuid.uuid4()),
                "cross-tenant evaluation proposal",
            )
            cur.execute("select set_config('carr.organization_tenant_id','carr-internal',true)")
            wrong_envelope = json.loads(json.dumps(receipt))
            wrong_envelope["attempt_id"] = "attempt:evidence-activation-foreign-envelope"
            wrong_envelope["envelope_digest"] = "sha256:" + "f" * 64
            refusal(
                cur,
                "select * from ops.record_attempt_receipt(%s,%s,%s,%s,%s,%s)",
                (ref, proposal[2], wrong_envelope["envelope_digest"], binding_pk, Jsonb(wrong_envelope), uuid.uuid4()),
                "foreign execution envelope",
            )
            wrong_route = json.loads(json.dumps(receipt))
            wrong_route["attempt_id"] = "attempt:evidence-activation-wrong-route"
            wrong_route["reliability"]["route_digest"] = "sha256:" + "f" * 64
            refusal(
                cur,
                "select * from ops.record_attempt_receipt(%s,%s,%s,%s,%s,%s)",
                (ref, proposal[2], receipt["envelope_digest"], binding_pk, Jsonb(wrong_route), uuid.uuid4()),
                "receipt route digest not bound to issued envelope",
            )
            forged_receipt = dict(receipt)
            forged_receipt["reliability"] = dict(receipt["reliability"])
            forged_receipt["reliability"]["trajectory"] = [{"sequence": 1, "raw_transcript": "never-store"}]
            refusal(
                cur,
                "select * from ops.record_attempt_receipt(%s,%s,%s,%s,%s,%s)",
                (ref, proposal[2], receipt["envelope_digest"], binding_pk, Jsonb(forged_receipt), uuid.uuid4()),
                "raw attempt receipt",
            )
            raw_sentence_slots = {
                "interventions": [{"kind": "human", "occurred_at": "2026-08-24T12:00:00Z",
                                   "summary": "raw operator sentence is forbidden"}],
                "handoff_proposal": {"proposed": True, "reason": "raw operator sentence is forbidden",
                                     "replacement_session_ref": "session:replacement",
                                     "checkpoint_ref": "checkpoint:one", "requires_independent_verification": True},
                "result": {**receipt["result"], "evidence_refs": ["raw operator sentence is forbidden"]},
            }
            for field, value in raw_sentence_slots.items():
                raw_sentence = {**receipt, field: value,
                                "attempt_id": f"attempt:evidence-activation-raw-{field}"}
                refusal(
                    cur,
                    "select * from ops.record_attempt_receipt(%s,%s,%s,%s,%s,%s)",
                    (ref, proposal[2], receipt["envelope_digest"], binding_pk, Jsonb(raw_sentence), uuid.uuid4()),
                    f"raw receipt sentence in {field}",
                )
            heldout_leak = json.loads(json.dumps(receipt))
            heldout_leak["attempt_id"] = "attempt:evidence-activation-heldout-leak"
            heldout_leak["reliability"]["evaluator_results"][0]["expected_answer"] = "never-expose-to-executor-or-ui"
            refusal(
                cur,
                "select * from ops.record_attempt_receipt(%s,%s,%s,%s,%s,%s)",
                (ref, proposal[2], receipt["envelope_digest"], binding_pk, Jsonb(heldout_leak), uuid.uuid4()),
                "held-out expected answer leak",
            )
            executor_shadow = json.loads(json.dumps(receipt))
            executor_shadow["attempt_id"] = "attempt:evidence-activation-executor-shadow"
            executor_shadow["reliability"]["shadow_comparisons"] = [{"promotion_state": "active", "side_effect_ref": "effect:forged"}]
            refusal(
                cur,
                "select * from ops.record_attempt_receipt(%s,%s,%s,%s,%s,%s)",
                (ref, proposal[2], receipt["envelope_digest"], binding_pk, Jsonb(executor_shadow), uuid.uuid4()),
                "executor shadow side effect or active posture",
            )
            missing_evidence = json.loads(json.dumps(receipt))
            missing_evidence["attempt_id"] = "attempt:evidence-activation-missing-evidence"
            missing_evidence["knowledge_activation"]["item_dispositions"][0]["evidence_refs"] = []
            refusal(
                cur,
                "select * from ops.record_attempt_receipt(%s,%s,%s,%s,%s,%s)",
                (ref, proposal[2], receipt["envelope_digest"], binding_pk, Jsonb(missing_evidence), uuid.uuid4()),
                "applied activation without evidence",
            )
            required_nonapplied = json.loads(json.dumps(receipt))
            required_nonapplied["attempt_id"] = "attempt:evidence-activation-required-nonapplied"
            required_nonapplied["knowledge_activation"]["item_dispositions"][0]["disposition"] = "stale"
            required_nonapplied["knowledge_activation"]["item_dispositions"][0]["evidence_refs"] = []
            required_nonapplied["knowledge_activation"]["closure"] = {"state": "not_activated", "unresolved_required_item_refs": [bundle["items"][0]["canonical_ref"]], "derived_by": "server"}
            refusal(
                cur,
                "select * from ops.record_attempt_receipt(%s,%s,%s,%s,%s,%s)",
                (ref, proposal[2], receipt["envelope_digest"], binding_pk, Jsonb(required_nonapplied), uuid.uuid4()),
                "required non-applied activation without evidence",
            )
            forged_closure = dict(receipt)
            forged_closure["attempt_id"] = "attempt:evidence-activation-forged-closure"
            forged_closure["knowledge_activation"] = dict(receipt["knowledge_activation"])
            forged_closure["knowledge_activation"]["closure"] = {
                "state": "closed", "unresolved_required_item_refs": [], "derived_by": "server"
            }
            refusal(
                cur,
                "select * from ops.record_attempt_receipt(%s,%s,%s,%s,%s,%s)",
                (ref, proposal[2], receipt["envelope_digest"], binding_pk, Jsonb(forged_closure), uuid.uuid4()),
                "forged server-derived closure",
            )
            duplicate_disposition = json.loads(json.dumps(receipt))
            duplicate_disposition["attempt_id"] = "attempt:evidence-activation-duplicate-disposition"
            duplicate_disposition["knowledge_activation"]["item_dispositions"].append(json.loads(json.dumps(duplicate_disposition["knowledge_activation"]["item_dispositions"][0])))
            refusal(
                cur, "select * from ops.record_attempt_receipt(%s,%s,%s,%s,%s,%s)",
                (ref, proposal[2], receipt["envelope_digest"], binding_pk, Jsonb(duplicate_disposition), uuid.uuid4()),
                "duplicate disposition coverage forgery",
            )
            missing_stage = json.loads(json.dumps(receipt))
            missing_stage["attempt_id"] = "attempt:evidence-activation-missing-stage"
            del missing_stage["knowledge_activation"]["item_dispositions"][0]["stage_ref"]
            refusal(
                cur, "select * from ops.record_attempt_receipt(%s,%s,%s,%s,%s,%s)",
                (ref, proposal[2], receipt["envelope_digest"], binding_pk, Jsonb(missing_stage), uuid.uuid4()),
                "applied activation missing independently checkable stage",
            )
            judge_override = json.loads(json.dumps(receipt))
            judge_override["attempt_id"] = "attempt:evidence-activation-judge-override"
            judge_override["reliability"]["deterministic_checks"][0]["state"] = "failed"
            refusal(
                cur, "select * from ops.record_attempt_receipt(%s,%s,%s,%s,%s,%s)",
                (ref, proposal[2], receipt["envelope_digest"], binding_pk, Jsonb(judge_override), uuid.uuid4()),
                "judge cannot override critical deterministic failure",
            )
            immature_horizon = json.loads(json.dumps(receipt))
            immature_horizon["attempt_id"] = "attempt:evidence-activation-immature-horizon"
            immature_horizon["reliability"]["outcome_horizon"]["state"] = "immature"
            immature_recorded = cur.execute(
                "select * from ops.record_attempt_receipt(%s,%s,%s,%s,%s,%s)",
                (ref, proposal[2], receipt["envelope_digest"], binding_pk, Jsonb(immature_horizon), uuid.uuid4()),
            ).fetchone()
            if immature_recorded is None:
                raise RuntimeError("immature outcome receipt did not remain an evidence-only projection")
            critical_unknown = json.loads(json.dumps(receipt))
            critical_unknown["attempt_id"] = "attempt:evidence-activation-critical-unknown"
            critical_unknown["reliability"]["deterministic_checks"][0]["state"] = "unknown"
            critical_unknown["reliability"]["closure"] = {"state": "insufficient_evidence", "reasons": ["reason:authority_evaluation_evidence_missing"], "derived_by": "server"}
            critical_unknown_recorded = cur.execute(
                "select * from ops.record_attempt_receipt(%s,%s,%s,%s,%s,%s)",
                (ref, proposal[2], receipt["envelope_digest"], binding_pk, Jsonb(critical_unknown), uuid.uuid4()),
            ).fetchone()
            if critical_unknown_recorded is None:
                raise RuntimeError("critical unknown did not remain insufficient evidence")
            forged_unknown_blocked = json.loads(json.dumps(critical_unknown))
            forged_unknown_blocked["attempt_id"] = "attempt:evidence-activation-critical-unknown-forged-blocked"
            forged_unknown_blocked["reliability"]["closure"]["state"] = "blocked"
            refusal(
                cur, "select * from ops.record_attempt_receipt(%s,%s,%s,%s,%s,%s)",
                (ref, proposal[2], receipt["envelope_digest"], binding_pk, Jsonb(forged_unknown_blocked), uuid.uuid4()),
                "critical unknown cannot be forged as blocked",
            )
            missing_kind = json.loads(json.dumps(receipt))
            missing_kind["attempt_id"] = "attempt:evidence-activation-missing-evaluator-kind"
            missing_kind["reliability"]["evaluator_results"] = [row for row in missing_kind["reliability"]["evaluator_results"] if row["kind"] != "human_acceptance"]
            missing_kind["reliability"]["closure"] = {"state": "insufficient_evidence", "reasons": ["reason:authority_evaluation_evidence_missing"], "derived_by": "server"}
            if cur.execute("select * from ops.record_attempt_receipt(%s,%s,%s,%s,%s,%s)", (ref, proposal[2], receipt["envelope_digest"], binding_pk, Jsonb(missing_kind), uuid.uuid4())).fetchone() is None:
                raise RuntimeError("missing evaluator kind did not remain insufficient evidence")
            held_out_missing = json.loads(json.dumps(receipt))
            held_out_missing["attempt_id"] = "attempt:evidence-activation-heldout-insufficient"
            for evaluator in held_out_missing["reliability"]["evaluator_results"]:
                evaluator["held_out_case_count"] = 0
            held_out_missing["reliability"]["closure"] = {"state": "insufficient_evidence", "reasons": ["reason:authority_evaluation_evidence_missing"], "derived_by": "server"}
            if cur.execute("select * from ops.record_attempt_receipt(%s,%s,%s,%s,%s,%s)", (ref, proposal[2], receipt["envelope_digest"], binding_pk, Jsonb(held_out_missing), uuid.uuid4())).fetchone() is None:
                raise RuntimeError("held-out shortfall did not remain insufficient evidence")
            for attempt_id, evaluator_kind, status, independent in (
                ("attempt:evidence-activation-authority-judge-failed", "judge", "failed", True),
                ("attempt:evidence-activation-authority-human-rejected", "human_acceptance", "failed", False),
            ):
                authority_failure = json.loads(json.dumps(receipt))
                authority_failure["attempt_id"] = attempt_id
                if cur.execute("select * from ops.record_attempt_receipt(%s,%s,%s,%s,%s,%s)", (ref, proposal[2], receipt["envelope_digest"], binding_pk, Jsonb(authority_failure), uuid.uuid4())).fetchone() is None:
                    raise RuntimeError("authority failure fixture receipt was not admitted")
                cur.execute("reset role")
                cur.execute("set session authorization carr_authority_joe")
                cur.execute("select set_config('carr.organization_tenant_id','carr-internal',true)")
                feedback_ref = feedback[1] if evaluator_kind == "human_acceptance" else None
                feedback_hash = feedback[2] if evaluator_kind == "human_acceptance" else None
                cur.execute("select * from ops.attest_attempt_receipt_evaluation(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (attempt_id, evaluator_kind, "", Jsonb(dimensions), status, independent, Jsonb(["evidence:authority-failure"]), Jsonb(evaluation_metadata), feedback_ref, feedback_hash, uuid.uuid4()))
                cur.execute("reset session authorization")
                set_local_role(cur, "carr_writer")
                cur.execute("select set_config('carr.organization_tenant_id','carr-internal',true)")
                authority_posture = cur.execute("select ops.read_attempt_receipt_reliability(%s)", (attempt_id,)).fetchone()[0]
                if authority_posture["reliability"]["state"] != "blocked" or authority_posture["reliability"]["reasons"] != ["reason:critical_authority_evaluator_or_human_rejection"]:
                    raise RuntimeError(f"{evaluator_kind} authority failure did not block canonical reliability posture")
            cur.execute("reset role")
            cur.execute("savepoint execution_environment_disable")
            try:
                cur.execute("set session authorization carr_authority_joe")
                disabled = cur.execute(
                    "select * from ops.transition_execution_environment_provider(%s,%s,%s,%s,%s)",
                    ("environment-provider:hermes-local:v1", "active", "disabled", Jsonb(["evidence:rollback-canary"]), uuid.uuid4()),
                ).fetchone()
                if disabled is None or disabled[1] != "disabled":
                    raise RuntimeError("human rollback did not disable the active provider")
                cur.execute("reset session authorization")
                set_local_role(cur, "carr_writer")
                cur.execute("select set_config('carr.organization_tenant_id','carr-internal',true)")
                refusal(
                    cur,
                    "select * from ops.issue_execution_envelope_v1(%s,%s,%s)",
                    (ref, replay_first[0], uuid.uuid4()),
                    "disabled execution environment provider",
                )
                refused_registration = cur.execute(
                    "select ops.hermes_runtime_admission_for_brief(%s,%s,%s,%s,%s)",
                    ("hermes-pilot", "builder", "joe", ref, replay_first[0]),
                ).fetchone()[0]
                if refused_registration.get("authorized") is not False:
                    raise RuntimeError("Hermes Bot-Brief admitted a disabled execution environment provider")
            finally:
                cur.execute("reset role")
                cur.execute("reset session authorization")
                cur.execute("rollback to savepoint execution_environment_disable")
            telemetry_count = cur.execute("select count(*) from ops.activation_reliability_telemetry where attempt_receipt_id=%s", (recorded[0],)).fetchone()[0]
            if telemetry_count != 1:
                raise RuntimeError("accepted AttemptReceipt did not project canonical action-bound telemetry")
        print(f"PASS: proposal -> acceptance -> deterministic compile -> activation; {REFUSAL_ASSERTIONS} named tamper refusals; telemetry readback")
        return 0
    except Exception as exc:
        print(f"evidence-activation-db-gate: FAIL — {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
