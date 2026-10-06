"""Focused invariants for the pure Assurance Slice Compiler v1."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
BRIDGE = ROOT / "tools/room-bridge"
sys.path.insert(0, str(BRIDGE))
import assurance_slice_compiler as compiler  # noqa: E402
import execution_contract  # noqa: E402

FIXTURE = ROOT / "control-room/contracts/fixtures/execution-fabric/assurance-compiler.valid.v1.json"


def schema_valid(schema: dict, entry: str, value: object) -> bool:
    source = """
import fs from "node:fs";
import {compileSchema} from "./workspace/contracts/schema-validator.mjs";
const payload = JSON.parse(fs.readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify(compileSchema(payload.schema, payload.schema.$defs[payload.entry])(payload.value)));
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source], cwd=ROOT,
        input=json.dumps({"schema": schema, "entry": entry, "value": value}),
        text=True, capture_output=True, check=True,
    )
    return json.loads(result.stdout)["valid"] is True


def _digest_without(value: dict, field: str) -> str:
    return execution_contract.canonical_digest({key: item for key, item in value.items() if key != field})


def seal_plan(value: dict) -> None:
    value["engineering_slice_plan"]["plan_digest"] = _digest_without(value["engineering_slice_plan"], "plan_digest")


def seal_rules(value: dict, *, update_binding: bool = True) -> None:
    rules = value["applicable_rules"]
    rules["rules"] = compiler._sort_set(rules["rules"])
    rules["snapshot_digest"] = execution_contract.canonical_digest({"snapshot_ref": rules["snapshot_ref"], "rules": rules["rules"]})
    if update_binding:
        value["assurance_slice"]["rule_snapshot_binding"] = {"snapshot_ref": rules["snapshot_ref"], "snapshot_digest": rules["snapshot_digest"]}


def seal_coordination(value: dict) -> None:
    coord = value["coordination_snapshot"]
    coord["leases"] = compiler._sort_set(coord["leases"])
    for lease in coord["leases"]:
        lease["claims"] = compiler._sort_set(lease["claims"])
    coord["dependencies"] = compiler._sort_set(coord["dependencies"])
    coord["snapshot_digest"] = _digest_without(coord, "snapshot_digest")


def ownership_preimage(contract: dict) -> dict:
    return {
        key: copy.deepcopy(item)
        for key, item in contract.items()
        if key not in {"contract_digest", "ownership_contract_digest", "lease_binding"}
    }


def seal_contract(value: dict) -> None:
    contract = compiler._normalized_contract(value["assurance_slice"])
    contract["ownership_contract_digest"] = execution_contract.canonical_digest(
        ownership_preimage(contract)
    )
    contract["contract_digest"] = _digest_without(contract, "contract_digest")
    value["assurance_slice"] = contract


def seal(value: dict) -> dict:
    seal_plan(value)
    value["assurance_slice"]["engineering_slice_plan_digest"] = value["engineering_slice_plan"]["plan_digest"]
    seal_rules(value)
    seal_coordination(value)
    seal_contract(value)
    return value


def valid_input() -> dict:
    return json.loads(FIXTURE.read_text())


def refusal(value: dict, code: str, causal_object: str) -> dict:
    result = compiler.compile_assurance_slice(value)
    assert result["ok"] is False, result
    assert result["refusal"]["code"] == code, result
    assert result["refusal"]["causal_object"] == causal_object, result
    assert "expected" in result["refusal"] and "actual" in result["refusal"]
    return result["refusal"]


def test_valid_compilation_is_deterministic_and_manifest_is_immutable_posture():
    value = valid_input()
    first = compiler.compile_assurance_slice(value)
    second = compiler.compile_assurance_slice(copy.deepcopy(value))
    assert first == second and first["ok"] is True
    manifest = first["manifest"]
    assert manifest["authority_state"] == "compiled_not_authorized"
    assert manifest["verification_state"] == "unverified"
    assert manifest["self_certification"] is False
    assert manifest["currentness"]["authorizes_action"] is False
    assert manifest["currentness"]["currentness_state"] == "declared_window_consistent_not_live_verified"
    assert manifest["currentness"]["live_currentness_verified"] is False
    assert manifest["currentness"]["declared_evaluation_time"] == value["declared_evaluation_time"]
    assert manifest["currentness"]["snapshot_as_of"] == value["coordination_snapshot"]["as_of"]
    assert manifest["currentness"]["snapshot_valid_until"] == value["coordination_snapshot"]["valid_until"]
    assert manifest["currentness"]["lease_expires_at"] == value["coordination_snapshot"]["leases"][0]["expires_at"]
    assert manifest["currentness"]["recompile_against_resulting_commit_tree_before"] == ["commit", "push", "pr_update", "review", "merge", "runtime_action"]
    assert manifest["currentness"]["usable_only_as_preflight_for"] == []
    assert manifest["currentness"]["requires_live_currentness_check_before"][:2] == ["write", "test"]
    assert manifest["slice"]["required_tests"][0]["check_profile_ref"] == "check-profile:assurance-compiler-v1"
    assert manifest["slice"]["required_tests"][0]["runner"] == "python_pytest"
    assert manifest["slice"]["required_tests"][0]["argv"] == ["python3", "-m", "pytest", "-q", "tools/test-assurance-slice-compiler.py"]
    assert manifest["slice"]["required_tests"][0]["cwd"] == "."
    assert manifest["input_bindings"]["assurance_slice_ownership_contract_digest"] == value["assurance_slice"]["ownership_contract_digest"]
    required = manifest["slice"]["required_tests"][0]
    assert required["environment_gate"] == compiler._CHECK_PROFILES["check:compiler"]["environment_gate"]
    assert required["environment_gate"]["must_pass_before_test"] is True
    assert required["environment_gate"]["argv"][0] == required["environment"]["runtime"] == required["argv"][0]
    for binding in (required["test_artifact"], required["environment"]["version_source"], required["environment"]["dependency_lock"]):
        actual = "sha256:" + hashlib.sha256((ROOT / binding["path"]).read_bytes()).hexdigest()
        assert binding["digest"] == actual
    assert manifest["manifest_hash"] == execution_contract.canonical_digest({k: v for k, v in manifest.items() if k != "manifest_hash"})


def test_declared_set_reordering_preserves_manifest_hash():
    left = valid_input(); right = copy.deepcopy(left)
    right["assurance_slice"]["path_claims"].reverse()
    right["assurance_slice"]["required_tests"][0]["evidence_fields"].reverse()
    right["applicable_rules"]["rules"].reverse()
    right["coordination_snapshot"]["leases"][0]["claims"].reverse()
    assert compiler.compile_assurance_slice(left)["manifest"]["manifest_hash"] == compiler.compile_assurance_slice(right)["manifest"]["manifest_hash"]


def test_ownership_digest_is_prelease_stable_but_executor_identity_is_owned():
    baseline = valid_input()
    expected = baseline["assurance_slice"]["ownership_contract_digest"]
    for field, replacement in (("lease_id", "lease:replacement"), ("fencing_generation", 99)):
        changed = copy.deepcopy(baseline)
        changed["assurance_slice"]["lease_binding"][field] = replacement
        assert compiler.compile_ownership_contract_digest(ownership_preimage(changed["assurance_slice"])) == expected

    for field, replacement in (("holder_session_id", "session:other"), ("holder_host_id", "host:other")):
        changed = copy.deepcopy(baseline)
        changed["assurance_slice"]["lease_binding"][field] = replacement
        seal_contract(changed)
        assert changed["assurance_slice"]["ownership_contract_digest"] == expected
        refusal(changed, "LEASE_BINDING_MISMATCH", "assurance_slice.lease_binding")

    coherent = copy.deepcopy(baseline)
    identity = coherent["assurance_slice"]["executor_identity"]
    identity.update({"actor_ref": "actor:other", "session_ref": "session:other", "host_ref": "host:other"})
    policy = coherent["assurance_slice"]["reviewer_policy"]
    policy.update({"executor_actor_ref": "actor:other", "executor_session_ref": "session:other"})
    assert compiler.compile_ownership_contract_digest(ownership_preimage(coherent["assurance_slice"])) != expected


def test_every_mutable_ownership_category_changes_prelease_digest():
    baseline = ownership_preimage(valid_input()["assurance_slice"])
    expected = compiler.compile_ownership_contract_digest(baseline)
    variants = []

    def variant(edit):
        changed = copy.deepcopy(baseline); edit(changed); variants.append(changed)

    variant(lambda row: row["work_request"].update(state_version=2))
    variant(lambda row: row["accepted_plan_revision"].update(revision=3))
    variant(lambda row: row.update(engineering_slice_plan_digest="sha256:" + "1" * 64))
    variant(lambda row: row.update(slice_ref="slice:other"))
    variant(lambda row: row.update(outcome="Changed owned outcome"))
    variant(lambda row: row["risk"].update(summary="Changed owned risk"))
    variant(lambda row: row["path_claims"][0].update(operation="rename_source"))
    variant(lambda row: row["forbidden_paths"][0].update(path="migrations/other.sql"))
    variant(lambda row: row["dependencies"][0].update(required_state="completed"))
    variant(lambda row: row["required_tests"][0]["evidence_fields"].append("new_owned_field"))
    variant(lambda row: row["evidence_requirements"][0]["required_fields"].append("new_owned_field"))
    variant(lambda row: row["reviewer_policy"].update(minimum_independent_reviewers=3))
    variant(lambda row: row["observable_output"].update(description="Changed owned output"))
    variant(lambda row: row["rollback"].update(strategy="Changed owned rollback"))
    variant(lambda row: row.update(release_class="none"))
    variant(lambda row: row["unfinished_work"].append("Changed owned unfinished work"))
    variant(lambda row: row["repository_binding"].update(commit_sha="1" * 40))
    variant(lambda row: row["rule_snapshot_binding"].update(snapshot_digest="sha256:" + "1" * 64))
    variant(lambda row: row["executor_identity"].update(actor_ref="actor:other"))
    for changed in variants:
        assert compiler.compile_ownership_contract_digest(changed) != expected


def test_final_digest_covers_all_lease_fields_and_the_ownership_digest():
    contract = compiler._normalized_contract(valid_input()["assurance_slice"])
    expected = _digest_without(contract, "contract_digest")
    replacements = {
        "lease_id": "lease:replacement", "fencing_generation": 99,
        "holder_session_id": "session:other", "holder_host_id": "host:other",
    }
    for field, replacement in replacements.items():
        changed = copy.deepcopy(contract); changed["lease_binding"][field] = replacement
        assert _digest_without(changed, "contract_digest") != expected
    changed = copy.deepcopy(contract)
    changed["ownership_contract_digest"] = "sha256:" + "1" * 64
    assert _digest_without(changed, "contract_digest") != expected


def test_ownership_preimage_schema_is_exactly_closed_and_matches_compiler_fields():
    schema = json.loads((ROOT / "control-room/contracts/assurance-slice-contract.v1.schema.json").read_text())
    definition = schema["$defs"]["OwnershipContractPreimage"]
    expected_fields = set(valid_input()["assurance_slice"]) - {"contract_digest", "ownership_contract_digest", "lease_binding"}
    assert definition["additionalProperties"] is False
    assert set(definition["required"]) == set(definition["properties"]) == expected_fields == compiler._OWNERSHIP_CONTRACT_FIELDS
    preimage = ownership_preimage(valid_input()["assurance_slice"])
    assert schema_valid(schema, "OwnershipContractPreimage", preimage)
    for forbidden in ("contract_digest", "ownership_contract_digest", "lease_binding"):
        altered = copy.deepcopy(preimage); altered[forbidden] = valid_input()["assurance_slice"][forbidden]
        assert not schema_valid(schema, "OwnershipContractPreimage", altered)


def test_ownership_digest_normalization_and_refusal_precedence_are_deterministic():
    left = ownership_preimage(valid_input()["assurance_slice"])
    right = copy.deepcopy(left)
    right["path_claims"].reverse()
    right["required_tests"][0]["evidence_fields"].reverse()
    assert compiler.compile_ownership_contract_digest(left) == compiler.compile_ownership_contract_digest(right)

    value = valid_input()
    value["assurance_slice"]["ownership_contract_digest"] = "sha256:" + "8" * 64
    value["assurance_slice"]["contract_digest"] = "sha256:" + "9" * 64
    refusal(value, "OWNERSHIP_CONTRACT_DIGEST_MISMATCH", "assurance_slice.ownership_contract_digest")
    value["assurance_slice"]["ownership_contract_digest"] = compiler.compile_ownership_contract_digest(
        ownership_preimage(value["assurance_slice"])
    )
    refusal(value, "ASSURANCE_CONTRACT_DIGEST_MISMATCH", "assurance_slice.contract_digest")


def test_foundation_metadata_and_compiler_versions_advance_together():
    paths = [
        "control-room/contracts/assurance-slice-contract.v1.schema.json",
        "control-room/contracts/assurance-compiler-input.v1.schema.json",
        "control-room/contracts/assurance-execution-manifest.v1.schema.json",
    ]
    schemas = [json.loads((ROOT / path).read_text()) for path in paths]
    assert {schema["version"] for schema in schemas} == {"1.0.0"}
    assert {schema["x-carr-foundation-revision"] for schema in schemas} == {compiler.COMPILER_VERSION} == {"1.1.0"}
    assert {schema["status"] for schema in schemas} == {"phase1_contract_foundation_not_deployed"}


def test_a1a_foundation_hashes_are_superseded_and_a1b_is_resealed():
    old = {
        "fixture": "sha256:f7d21cde58fba958187d2c8e075160c79dccc7a57366693cbc39b263abc0f488",
        "input": "sha256:3405984739709066fc4240cba271bc6c4a47708073375156777f8bf5555ef527",
        "ownership": "sha256:e3a7a26ee6d25c41314d9aefb1a1bf591c742d54b2364406c6fe71d824e93f64",
        "final": "sha256:7bab89fe9bfd96c56cd47fed548f3027399e1a1bc7ad934397a596babf0438ea",
        "manifest": "sha256:cb13135536704318b7e1905d5c28bde1565f8fc3fc75d7c2b70a92b668726ec1",
    }
    value = valid_input()
    result = compiler.compile_assurance_slice(value)
    assert result["ok"] is True, result
    manifest = result["manifest"]
    current = {
        "fixture": "sha256:" + hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        "input": manifest["input_digest"],
        "ownership": value["assurance_slice"]["ownership_contract_digest"],
        "final": value["assurance_slice"]["contract_digest"],
        "manifest": manifest["manifest_hash"],
    }
    assert all(current[name] != old[name] for name in old)
    assert current["ownership"] == compiler.compile_ownership_contract_digest(ownership_preimage(value["assurance_slice"]))
    assert current["final"] == _digest_without(compiler._normalized_contract(value["assurance_slice"]), "contract_digest")
    assert current["input"] == execution_contract.canonical_digest(compiler._normalized_input(value))
    assert current["manifest"] == execution_contract.canonical_digest({key: item for key, item in manifest.items() if key != "manifest_hash"})


def test_unknown_missing_and_absent_extension_refuse_exactly():
    value = valid_input(); value["surprise"] = True
    refusal(value, "INPUT_UNKNOWN_FIELD", "compiler_input")
    value = valid_input(); del value["repository"]
    refusal(value, "INPUT_MISSING_FIELD", "compiler_input")
    value = valid_input(); value["assurance_slice"] = None
    refusal(value, "ASSURANCE_SLICE_ABSENT", "assurance_slice")


def test_work_request_plan_and_slice_binding_mismatches_are_distinct():
    value = valid_input(); value["work_request"]["state_version"] += 1
    refusal(value, "WORK_REQUEST_BINDING_MISMATCH", "work_request")
    value = valid_input(); value["accepted_plan_revision"]["revision"] += 1
    refusal(value, "ACCEPTED_PLAN_BINDING_MISMATCH", "accepted_plan_revision")
    value = valid_input(); value["assurance_slice"]["engineering_slice_plan_digest"] = "sha256:" + "9" * 64; seal_contract(value)
    refusal(value, "ENGINEERING_SLICE_PLAN_BINDING_MISMATCH", "assurance_slice.engineering_slice_plan_digest")
    value = valid_input(); value["assurance_slice"]["slice_ref"] = "slice:missing"; seal_contract(value)
    refusal(value, "SLICE_BINDING_MISMATCH", "assurance_slice.slice_ref")


@pytest.mark.parametrize("bad", ["/absolute.py", "./dot.py", "a/../b.py", "a\\b.py", "a/*.py", "unicodé.py"])
def test_invalid_path_forms_refuse_with_the_exact_path_object(bad):
    value = valid_input(); value["assurance_slice"]["path_claims"][0]["path"] = bad
    refusal(value, "PATH_INVALID", "assurance_slice.path_claims[0].path")


def test_case_alias_and_allowed_forbidden_component_ancestry_refuse():
    value = valid_input(); value["assurance_slice"]["path_claims"][0]["path"] = "tools/space path.py"
    refusal(value, "PATH_INVALID", "assurance_slice.path_claims[0].path")
    value = valid_input(); value["assurance_slice"]["forbidden_paths"].append({"path":"Tools/room-bridge/other.py","mode":"file"})
    refusal(value, "PATH_CASE_ALIAS", "assurance_slice.path_claims")
    value = valid_input(); value["assurance_slice"]["forbidden_paths"] = [{"path":"tools","mode":"tree"}]
    refusal(value, "PATH_SCOPE_COLLISION", "tools/room-bridge/assurance_slice_compiler.py")


def test_dependency_missing_and_unsatisfied_have_causal_slice_ref():
    value = valid_input(); value["coordination_snapshot"]["dependencies"] = []; seal_coordination(value)
    refusal(value, "DEPENDENCY_MISSING", "slice:contracts")
    value = valid_input(); dep = value["coordination_snapshot"]["dependencies"][0]; dep["state"] = "pending"; dep["evidence_digest"] = None; seal_coordination(value)
    refusal(value, "DEPENDENCY_UNSATISFIED", "slice:contracts")


@pytest.mark.parametrize("operation", ["write", "rename_source", "rename_destination"])
def test_active_foreign_file_or_tree_lease_collision_includes_rename_claims(operation):
    value = valid_input()
    value["coordination_snapshot"]["leases"].append({"lease_id":"lease:foreign","state":"active","holder_session_id":"session:foreign","holder_host_id":"host:other","expires_at":"2026-08-30T15:30:00Z","fencing_generation":2,"claims":[{"path":"tools/room-bridge","mode":"tree","operation":operation}]})
    seal_coordination(value)
    refusal(value, "FOREIGN_LEASE_COLLISION", "tools/room-bridge/assurance_slice_compiler.py")
    value = valid_input()
    value["coordination_snapshot"]["leases"].append({"lease_id":"lease:case-alias","state":"active","holder_session_id":"session:foreign","holder_host_id":"host:other","expires_at":"2026-08-30T15:30:00Z","fencing_generation":2,"claims":[{"path":"Tools/room-bridge/assurance_slice_compiler.py","mode":"file","operation":operation}]})
    seal_coordination(value)
    refusal(value, "FOREIGN_LEASE_COLLISION", "tools/room-bridge/assurance_slice_compiler.py")


def test_stale_snapshot_expired_lease_and_released_lease_are_not_equivalent():
    value = valid_input(); value["coordination_snapshot"]["valid_until"] = value["coordination_snapshot"]["as_of"]
    refusal(value, "COORDINATION_SNAPSHOT_STALE", "coordination_snapshot.valid_until")
    value = valid_input(); value["declared_evaluation_time"] = "2026-08-30T21:37:24Z"
    refusal(value, "COORDINATION_SNAPSHOT_STALE", "coordination_snapshot.valid_until")
    for field, bad in (("as_of", "2026-13-01T15:00:00Z"), ("valid_until", "2026-02-30T16:00:00Z")):
        value = valid_input(); value["coordination_snapshot"][field] = bad
        refusal(value, "FIELD_INVALID", f"coordination_snapshot.{field}")
    value = valid_input(); value["coordination_snapshot"]["leases"][0]["expires_at"] = "2026-02-30T16:00:00Z"; seal_coordination(value)
    refusal(value, "FIELD_INVALID", "coordination_snapshot.leases[0].expires_at")
    value = valid_input()
    value["declared_evaluation_time"] = "2000-01-01T00:00:01Z"
    value["coordination_snapshot"]["as_of"] = "2000-01-01T00:00:00Z"
    value["coordination_snapshot"]["valid_until"] = "2000-01-01T01:00:00Z"
    value["coordination_snapshot"]["leases"][0]["expires_at"] = "2000-01-01T01:00:00Z"
    seal_coordination(value)
    result = compiler.compile_assurance_slice(value)
    assert result["ok"] is True
    assert result["manifest"]["currentness"]["currentness_state"] == "declared_window_consistent_not_live_verified"
    assert result["manifest"]["currentness"]["usable_only_as_preflight_for"] == []
    value = valid_input(); value["coordination_snapshot"]["leases"][0]["expires_at"] = "2026-08-30T14:59:59Z"; seal_coordination(value)
    refusal(value, "LEASE_EXPIRED", "lease:lease:a1a")
    value = valid_input(); value["coordination_snapshot"]["leases"][0]["state"] = "released"; seal_coordination(value)
    refusal(value, "LEASE_RELEASED", "lease:lease:a1a")


def test_stale_rule_digest_and_repository_identity_mismatch_refuse():
    value = valid_input(); value["applicable_rules"]["rules"][0]["revision"] += 1; seal_rules(value, update_binding=False)
    refusal(value, "RULE_SNAPSHOT_STALE", "applicable_rules")
    value = valid_input(); value["repository"]["tree_sha"] = "9" * 40
    refusal(value, "REPOSITORY_IDENTITY_MISMATCH", "repository")


def test_owner_acceptance_cannot_substitute_for_independent_review():
    value = valid_input(); value["assurance_slice"]["reviewer_policy"]["owner_acceptance_is_review"] = True
    refusal(value, "REVIEWER_POLICY_INVALID", "assurance_slice.reviewer_policy")


def test_required_commands_refine_exact_planned_checks_and_reviewer_names_executor():
    value = valid_input(); value["assurance_slice"]["required_tests"][0]["check_ref"] = "check:unplanned"; seal_contract(value)
    refusal(value, "SLICE_BINDING_MISMATCH", "assurance_slice.required_tests")
    value = valid_input(); value["assurance_slice"]["required_tests"][0]["planned_check_digest"] = "sha256:" + "9" * 64; seal_contract(value)
    refusal(value, "REQUIRED_TEST_BINDING_MISMATCH", "check:compiler")
    value = valid_input(); value["assurance_slice"]["required_tests"][0]["argv"] = ["true", "tools/test-assurance-slice-compiler.py"]; seal_contract(value)
    refusal(value, "REQUIRED_TEST_BINDING_MISMATCH", "check:compiler")
    value = valid_input(); required = value["assurance_slice"]["required_tests"][0]; required["test_artifact"] = {"path":"tools/room-bridge/assurance_slice_compiler.py","digest":"sha256:" + "8" * 64}; required["argv"][-1] = required["test_artifact"]["path"]; seal_contract(value)
    refusal(value, "REQUIRED_TEST_BINDING_MISMATCH", "check:compiler")
    value = valid_input(); required = value["assurance_slice"]["required_tests"][0]; required["environment"]["runtime"] = "true"; required["argv"][0] = "true"; required["environment_gate"]["argv"][0] = "true"; seal_contract(value)
    refusal(value, "REQUIRED_TEST_BINDING_MISMATCH", "check:compiler")
    value = valid_input(); required = value["assurance_slice"]["required_tests"][0]; required["test_artifact"] = {"path":"tools/room-bridge/test_engineering_passport_unit.py","digest":"sha256:" + "7" * 64}; required["argv"][-1] = required["test_artifact"]["path"]; seal_contract(value)
    refusal(value, "REQUIRED_TEST_BINDING_MISMATCH", "check:compiler")
    value = valid_input(); value["assurance_slice"]["required_tests"][0]["check_profile_ref"] = "check-profile:foreign"; seal_contract(value)
    refusal(value, "REQUIRED_TEST_BINDING_MISMATCH", "check:compiler")
    value = valid_input(); value["assurance_slice"]["required_tests"][0]["environment_gate"]["must_pass_before_test"] = False
    refusal(value, "FIELD_INVALID", "assurance_slice.required_tests[0].environment_gate.must_pass_before_test")
    value = valid_input(); value["assurance_slice"]["required_tests"][0]["causal_failure"]["expected"] = "anything exits nonzero"; seal_contract(value)
    refusal(value, "REQUIRED_TEST_BINDING_MISMATCH", "check:compiler")
    value = valid_input(); value["assurance_slice"]["required_tests"][0]["causal_failure"]["code"] = "generic_nonzero"; seal_contract(value)
    refusal(value, "REQUIRED_TEST_BINDING_MISMATCH", "check:compiler")
    value = valid_input(); value["assurance_slice"]["required_tests"][0]["causal_failure"]["object"] = "anything"; seal_contract(value)
    refusal(value, "REQUIRED_TEST_BINDING_MISMATCH", "check:compiler")
    value = valid_input(); artifact = value["assurance_slice"]["required_tests"][0]["test_artifact"]["path"]
    value["assurance_slice"]["path_claims"] = [claim for claim in value["assurance_slice"]["path_claims"] if claim["path"] != artifact]
    value["coordination_snapshot"]["leases"][0]["claims"] = [claim for claim in value["coordination_snapshot"]["leases"][0]["claims"] if claim["path"] != artifact]
    seal_coordination(value); seal_contract(value)
    assert compiler.compile_assurance_slice(value)["ok"] is True
    value = valid_input(); value["assurance_slice"]["reviewer_policy"]["executor_actor_ref"] = "actor:someone-else"; seal_contract(value)
    refusal(value, "REVIEWER_POLICY_INVALID", "assurance_slice.reviewer_policy")


def test_lease_holder_is_the_bound_executor_session_and_host():
    value = valid_input(); value["assurance_slice"]["executor_identity"]["host_ref"] = "host:other"; seal_contract(value)
    refusal(value, "LEASE_BINDING_MISMATCH", "assurance_slice.lease_binding")
    value = valid_input(); value["coordination_snapshot"]["requesting_session_id"] = "session:foreign"; seal_coordination(value)
    fact = refusal(value, "REQUESTER_IDENTITY_MISMATCH", "coordination_snapshot.requesting_session_id")
    assert fact["expected"] == "session:a1a" and fact["actual"] == "session:foreign"
    value = valid_input(); value["coordination_snapshot"]["requesting_host_id"] = "host:other"; seal_coordination(value)
    fact = refusal(value, "REQUESTER_IDENTITY_MISMATCH", "coordination_snapshot.requesting_host_id")
    assert fact["expected"] == "host:codex" and fact["actual"] == "host:other"


def test_output_cannot_claim_authorization_verification_or_self_certification():
    manifest = compiler.compile_assurance_slice(valid_input())["manifest"]
    schema = json.loads((ROOT / "control-room/contracts/assurance-execution-manifest.v1.schema.json").read_text())
    assert schema["properties"]["authority_state"]["const"] == manifest["authority_state"]
    assert schema["properties"]["verification_state"]["const"] == manifest["verification_state"]
    assert schema["properties"]["self_certification"]["const"] is manifest["self_certification"] is False
    for name in ("Risk", "DependencyGate", "EvidenceRequirement", "ReviewerPolicy", "ObservableOutput", "Rollback", "LeaseBinding", "ExecutorIdentity"):
        assert schema["$defs"][name]["additionalProperties"] is False
    slice_schema = json.loads((ROOT / "control-room/contracts/assurance-slice-contract.v1.schema.json").read_text())
    input_schema = json.loads((ROOT / "control-room/contracts/assurance-compiler-input.v1.schema.json").read_text())
    required_schema_value = valid_input()["assurance_slice"]["required_tests"][0]
    assert schema_valid(slice_schema, "RequiredTest", required_schema_value)
    altered = copy.deepcopy(required_schema_value); altered["environment"]["runtime"] = "true"; altered["argv"][0] = "true"
    assert not schema_valid(slice_schema, "RequiredTest", altered)
    revised = valid_input()
    planned = revised["engineering_slice_plan"]["slices"][1]["planned_checks"][0]
    planned["failure_condition"] = "a revised accepted compiler failure condition"
    seal_plan(revised)
    revised["assurance_slice"]["engineering_slice_plan_digest"] = revised["engineering_slice_plan"]["plan_digest"]
    required = revised["assurance_slice"]["required_tests"][0]
    required["planned_check_digest"] = execution_contract.canonical_digest(planned)
    required["causal_failure"]["expected"] = planned["failure_condition"]
    seal_contract(revised)
    revised_result = compiler.compile_assurance_slice(revised)
    assert revised_result["ok"] is True, revised_result
    assert schema_valid(slice_schema, "RequiredTest", required)
    assert revised_result["manifest"]["slice"]["required_tests"][0] == required
    assert schema_valid(slice_schema, "Risk", {"risk_class":"R1","summary":"bounded"})
    assert not schema_valid(slice_schema, "Risk", {"risk_class":1,"summary":"bounded"})
    for bad in ("tools//test.py", "tools/", "tools/space path.py"):
        assert not schema_valid(slice_schema, "RepoPath", bad)
    assert schema_valid(slice_schema, "RepoPath", "tools/test.py")
    assert schema_valid(input_schema, "Timestamp", "2026-08-30T15:00:00Z")
    for bad in ("2026-13-01T15:00:00Z", "2026-02-30T16:00:00Z"):
        assert not schema_valid(input_schema, "Timestamp", bad)
        assert not schema_valid(schema, "Timestamp", bad)
    value = valid_input(); fields = value["assurance_slice"]["required_tests"][0]["evidence_fields"]; fields.append(fields[0]); seal_contract(value)
    refusal(value, "FIELD_INVALID", "assurance_slice.required_tests[0].evidence_fields")
    value = valid_input(); value["assurance_slice"]["evidence_requirements"][0]["required_fields"] = [1]; seal_contract(value)
    refusal(value, "FIELD_INVALID", "assurance_slice.evidence_requirements[0].required_fields")


def test_every_bound_category_and_compiler_version_changes_manifest_hash():
    baseline = valid_input()
    baseline_hash = compiler.compile_assurance_slice(baseline)["manifest"]["manifest_hash"]
    variants = []
    contract_change = copy.deepcopy(baseline); contract_change["assurance_slice"]["unfinished_work"].append("A serial consumer changed"); seal_contract(contract_change); variants.append(contract_change)
    rule_change = copy.deepcopy(baseline); rule_change["applicable_rules"]["rules"][0]["revision"] += 1; seal_rules(rule_change); seal_contract(rule_change); variants.append(rule_change)
    coord_change = copy.deepcopy(baseline); coord_change["coordination_snapshot"]["valid_until"] = "2026-08-30T16:30:00Z"; coord_change["coordination_snapshot"]["leases"][0]["expires_at"] = "2026-08-30T16:30:00Z"; seal_coordination(coord_change); variants.append(coord_change)
    repo_change = copy.deepcopy(baseline); repo_change["repository"]["commit_sha"] = "9" * 40; repo_change["assurance_slice"]["repository_binding"]["commit_sha"] = "9" * 40; seal_contract(repo_change); variants.append(repo_change)
    for value in variants:
        result = compiler.compile_assurance_slice(value)
        assert result["ok"] is True, result
        assert result["manifest"]["manifest_hash"] != baseline_hash
    versioned = compiler.compile_assurance_slice(baseline, compiler_version="1.0.1")
    assert versioned["ok"] is True and versioned["manifest"]["manifest_hash"] != baseline_hash


def _as_v2_plan(value: dict) -> dict:
    """The fixture's plan re-declared as engineering-slice-plan.v2 with a closed design contract per slice."""
    sys.path.insert(0, str(BRIDGE))
    import test_engineering_passport_unit as passport_fixture  # noqa: E402

    plan = value["engineering_slice_plan"]
    plan["schema_version"] = "engineering-slice-plan.v2"
    for row in plan["slices"]:
        row["design_contract"] = passport_fixture.design_contract(row)
    return seal(value)


def test_compiles_a_registrable_v2_plan_and_still_reads_a_stored_v1_plan():
    """Since migration 0616 only engineering-slice-plan.v2 registers; stored v1 plans stay readable."""
    import engineering_passport  # noqa: E402

    v1 = valid_input()
    assert v1["engineering_slice_plan"]["schema_version"] == "engineering-slice-plan.v1"
    assert compiler.compile_assurance_slice(copy.deepcopy(v1))["ok"] is True

    v2 = _as_v2_plan(copy.deepcopy(v1))
    assert engineering_passport.validate_engineering_slice_plan(copy.deepcopy(v2["engineering_slice_plan"]))[
        "schema_version"] == "engineering-slice-plan.v2"
    result = compiler.compile_assurance_slice(v2)
    assert result["ok"] is True, result
    assert result["manifest"]["input_bindings"]["engineering_slice_plan_digest"] == v2["engineering_slice_plan"]["plan_digest"]

    broken = copy.deepcopy(v2)
    del broken["engineering_slice_plan"]["slices"][0]["design_contract"]
    seal(broken)
    detail = refusal(broken, "ENGINEERING_SLICE_PLAN_INVALID", "engineering_slice_plan")
    assert "engineering-slice-plan.v2" in detail["expected"]


def test_module_has_no_provider_network_git_database_model_or_write_imports():
    source = (BRIDGE / "assurance_slice_compiler.py").read_text()
    for forbidden in ("requests", "urllib", "subprocess", "psycopg", "openai", "anthropic", ".write_text(", "open("):
        assert forbidden not in source
    preimage = ownership_preimage(valid_input()["assurance_slice"])
    assert compiler.compile_ownership_contract_digest(preimage) == valid_input()["assurance_slice"]["ownership_contract_digest"]


if __name__ == "__main__":
    raise SystemExit(pytest.main(["-q", __file__]))
