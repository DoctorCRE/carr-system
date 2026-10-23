#!/usr/bin/env python3
"""Hermetic checks for retained foundation assurance staging reads."""
from __future__ import annotations
import contextlib
import dataclasses
import hashlib
import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import uuid
from unittest.mock import patch

REPO = pathlib.Path(__file__).resolve().parents[1]
CANDIDATE_OPERATION_ID = uuid.UUID("f870b3e2-f99a-4bf2-ba16-629d9725ba6d")
RECEIPT_ID = uuid.UUID("c4cddf05-03bd-4f9e-8691-b54dac7be8f4")
EXPECTED_SHA = "07d13398824dad987c40331ae7c2092db07b75d8"

def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

def main():
    provision = load_module("staging_assurance_binding", REPO / "lib/foundation_assurance_binding.py")
    facts = load_module("staging_assurance_facts", REPO / "ops/foundation-assurance-candidate-rehearsal.py")
    def check(label, condition):
        if not condition:
            raise AssertionError(label)
        print("ok " + label)
    args = ["--current-sha", EXPECTED_SHA, "--candidate-operation-id", str(CANDIDATE_OPERATION_ID),
            "--receipt-id", str(RECEIPT_ID), "--execute"]
    with patch.object(facts, "exact_sha", return_value=EXPECTED_SHA), contextlib.redirect_stderr(io.StringIO()):
        try:
            facts.run(args)
        except SystemExit as exc:
            check("retired recovery execution is rejected by the CLI", exc.code == 2)
        except Exception as exc:
            raise AssertionError("retired recovery execution remained available") from exc
        else:
            raise AssertionError("retired recovery execution remained available")
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


    with patch.object(provision.replacement, "resolve_existing_scopes", return_value=(production, old, None)):
        try:
            provision.resolve_replacement_binding(target, environ={}, run=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("unexpected process")),
                connect=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("unexpected connection")))
        except provision.ProvisioningRefusal:
            check("missing candidate refuses without fallback", True)
        else:
            raise AssertionError("missing candidate accepted")
    class ReadCursor:
        def __init__(self, identity, value):
            self.identity, self.value = identity, value
            self.statements = []
        def execute(self, statement, params=None):
            self.statements.append(str(statement))
        def fetchone(self):
            return self.identity if "session_user,current_user" in self.statements[-1] else (self.value,)
    class ReadConnection:
        def __init__(self, identity, value):
            self.cur = ReadCursor(identity, value)
            self.rolled_back = self.closed = False
        def cursor(self):
            return self.cur
        def rollback(self):
            self.rolled_back = True
        def close(self):
            self.closed = True
    verifier = ReadConnection((provision.REPLACEMENT_VERIFIER_ROLE, provision.REPLACEMENT_VERIFIER_ROLE, True), exact_receipt)
    with patch.object(replacement, "resolve_existing_scopes", return_value=(production, old, candidate)), \
         patch.object(provision, "load_replacement_source_manifest", return_value=source_manifest), \
         patch.object(provision.credential, "load_for_endpoint_id", return_value=type("Credential", (), {"value": "fixture-verifier"})()), \
         patch.object(replacement, "derive_dsn", return_value=replacement.SecretDsn(candidate, "fixture-owner")):
        binding = provision.resolve_replacement_binding(target, environ={}, connect=lambda _: verifier)
    check("assurance returns the exact receipted candidate owner", binding.owner.value == "fixture-owner" and binding.owner.endpoint == candidate.endpoint_host)
    check("receipt acquisition rolls back its read-only transaction and closes", verifier.cur.statements[0] == "begin transaction read only" and verifier.rolled_back and verifier.closed)
    owner = ReadConnection(("neondb_owner", "neondb_owner"), {"migration": "fixture-migration", "oracle_role": True})
    observed = facts.foundation_assurance_facts("fixture-owner", connect=lambda _: owner)
    check("foundation facts retain a read-only owner transaction", observed["oracle_role"] is True and owner.cur.statements[0] == "begin transaction read only" and owner.rolled_back and owner.closed)
    with patch.object(facts, "exact_sha", return_value=EXPECTED_SHA), \
         patch.object(facts, "load_provision_module", return_value=provision), \
         patch.object(facts, "candidate_snapshot_dsn", return_value="fixture-owner"), \
         patch.object(facts, "foundation_assurance_facts", return_value=observed), \
         contextlib.redirect_stdout(io.StringIO()) as output:
        result = facts.run(args[:-1] + ["--read-foundation-facts"])
    check("the sealer's existing facts CLI returns its JSON projection", result == 0 and json.loads(output.getvalue()) == observed)
    writes = ReadCursor((), {})
    try:
        replacement.call_json(writes, "ops.prepare_staging_replacement_project", CANDIDATE_OPERATION_ID)
    except replacement.ReplacementRefusal:
        check("receipt reader rejects the retired mutation functions before SQL", writes.statements == [])
    else:
        raise AssertionError("receipt reader still accepts staging writes")
    print("foundation staging read contracts passed")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
