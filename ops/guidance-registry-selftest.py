#!/usr/bin/env python3
"""Seeded regressions for the typed Guidance Registry contract.

The registry replaces the dishonest assumption that every taught item is the
same kind of mechanically enforceable rule.  These tests keep classification
and delivery honest before any database migration or live-store write exists.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import sys
import tempfile


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE = os.path.join(REPO, "lib", "guidance_registry.py")
if not os.path.exists(MODULE):
    sys.exit("guidance-registry-selftest: missing lib/guidance_registry.py (expected red before implementation)")

spec = importlib.util.spec_from_file_location("guidance_registry", MODULE)
assert spec and spec.loader
registry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registry)


BASE = {
    "registry": "carr-guidance-registry",
    "schema_version": "1.0.0",
    "items": [
        {
            "guidance_id": "g-constraint",
            "source_id": "aaaaaaaa",
            "source_clause": "whole",
            "is_primary": True,
            "guidance_type": "constraint",
            "scope": {"tenant": "carr", "actor": "all"},
            "activation": {"trigger": "before a governed write"},
            "consumer": "control_plane",
            "verification": {"mechanism": "deny fixture"},
            "provenance": {"source": "rule", "preserve_source_record": True},
            "lifecycle": {"status": "proposed", "version": 1},
            "delivery": {
                "projection": "constraint_enforcement",
                "enforcement_control": "fixture-control",
                "evidence": ["hooks/fixture.py"],
                "tests": ["ops/fixture-selftest.py"],
            },
        },
        {
            "guidance_id": "g-procedure",
            "source_id": "bbbbbbbb",
            "source_clause": "whole",
            "is_primary": True,
            "guidance_type": "procedure",
            "scope": {"tenant": "carr", "actor": "all"},
            "activation": {"trigger": "a task begins", "entry_condition": "task is in scope"},
            "consumer": "workflow_startup",
            "verification": {"mechanism": "completion receipt", "completion_condition": "receipt exists"},
            "provenance": {"source": "rule", "preserve_source_record": True},
            "lifecycle": {"status": "proposed", "version": 1},
            "delivery": {"projection": "procedure_workflow"},
        },
        {
            "guidance_id": "g-doctrine",
            "source_id": "cccccccc",
            "source_clause": "whole",
            "is_primary": True,
            "guidance_type": "doctrine",
            "scope": {"tenant": "carr", "actor": "all"},
            "activation": {"trigger": "a matching situation is detected", "situation_mappings": ["fixture-situation"]},
            "consumer": "situation_retrieval",
            "verification": {"mechanism": "retrieval golden case"},
            "provenance": {"source": "rule", "preserve_source_record": True},
            "lifecycle": {"status": "proposed", "version": 1},
            "delivery": {"projection": "doctrine_retrieval"},
        },
        {
            "guidance_id": "g-rubric",
            "source_id": "dddddddd",
            "source_clause": "whole",
            "is_primary": True,
            "guidance_type": "rubric",
            "scope": {"tenant": "carr", "actor": "all"},
            "activation": {"trigger": "an artifact is ready for review"},
            "consumer": "independent_verifier",
            "verification": {"mechanism": "review", "verifier": "independent", "acceptance_criteria": ["criterion"]},
            "provenance": {"source": "rule", "preserve_source_record": True},
            "lifecycle": {"status": "proposed", "version": 1},
            "delivery": {"projection": "verification_rubric"},
        },
        {
            "guidance_id": "g-preference",
            "source_id": "eeeeeeee",
            "source_clause": "whole",
            "is_primary": True,
            "guidance_type": "preference",
            "scope": {"tenant": "carr", "actor": "joe"},
            "activation": {"trigger": "Joe is the relevant partner"},
            "consumer": "partner_context",
            "verification": {"mechanism": "relevant-partner selector"},
            "provenance": {"source": "rule", "preserve_source_record": True},
            "lifecycle": {"status": "proposed", "version": 1},
            "delivery": {"projection": "scoped_preference"},
        },
        {
            "guidance_id": "g-precedent",
            "source_id": "ffffffff",
            "source_clause": "whole",
            "is_primary": True,
            "guidance_type": "precedent",
            "scope": {"tenant": "carr", "actor": "all"},
            "activation": {"trigger": "a similar decision is under review"},
            "consumer": "decision_history",
            "verification": {"mechanism": "searchable rationale"},
            "provenance": {"source": "rule", "preserve_source_record": True},
            "lifecycle": {"status": "proposed", "version": 1},
            "delivery": {"projection": "precedent_search"},
        },
        {
            "guidance_id": "g-example",
            "source_id": "11111111",
            "source_clause": "whole",
            "is_primary": True,
            "guidance_type": "example",
            "scope": {"tenant": "carr", "actor": "all"},
            "activation": {"trigger": "a matching teaching case is useful"},
            "consumer": "contextual_example",
            "verification": {"mechanism": "source-linked case readback"},
            "provenance": {"source": "rule", "preserve_source_record": True},
            "lifecycle": {"status": "proposed", "version": 1},
            "delivery": {"projection": "example_retrieval"},
        },
    ],
    "source_analysis": [],
}


def mutate(path, value):
    data = copy.deepcopy(BASE)
    target = data
    for key in path[:-1]:
        target = target[key]
    if value is None:
        target.pop(path[-1], None)
    else:
        target[path[-1]] = value
    return data


def case(name, data, expected_fragment=None):
    errors = registry.validate_registry(data)
    passed = not errors if expected_fragment is None else any(expected_fragment in error for error in errors)
    print(f"{'PASS' if passed else 'FAIL'}  {name}: {errors or 'valid'}")
    return passed


def main():
    schema_path = os.path.join(REPO, "ops", "config", "guidance-registry.schema.v1.json")
    with open(schema_path, encoding="utf-8") as fh:
        schema = json.load(fh)
    schema_types = schema["$defs"]["guidanceType"]["enum"]
    schema_pass = tuple(schema_types) == registry.GUIDANCE_TYPES
    print(f"{'PASS' if schema_pass else 'FAIL'}  JSON schema and runtime type vocabulary agree")
    cases = [
        schema_pass,
        case("all seven valid types", BASE),
        case("constraint requires enforcement evidence",
             mutate(["items", 0, "delivery", "evidence"], None), "enforcement evidence"),
        case("constraint requires a test fixture",
             mutate(["items", 0, "delivery", "tests"], []), "enforcement tests"),
        case("procedure requires entry condition",
             mutate(["items", 1, "activation", "entry_condition"], None), "entry condition"),
        case("procedure requires completion condition",
             mutate(["items", 1, "verification", "completion_condition"], None), "completion condition"),
        case("doctrine requires situation activation",
             mutate(["items", 2, "activation", "situation_mappings"], []), "situation mapping"),
        case("rubric requires acceptance criteria",
             mutate(["items", 3, "verification", "acceptance_criteria"], []), "acceptance criteria"),
        case("rubric requires an independent verifier",
             mutate(["items", 3, "verification", "verifier"], None), "verifier"),
        case("preference requires a scoped actor",
             mutate(["items", 4, "scope", "actor"], "all"), "scoped actor"),
        case("unknown type is refused",
             mutate(["items", 6, "guidance_type"], "suggestion"), "guidance type"),
        case("wrong delivery projection is refused",
             mutate(["items", 2, "delivery", "projection"], "standing_context"), "delivery projection"),
    ]

    mixed = copy.deepcopy(BASE)
    mixed["source_analysis"] = [{
        "source_id": "cccccccc", "mixed_type_detected": True,
        "clause_types": ["doctrine", "constraint"], "split_group_id": "split-c"}]
    cases.append(case("mixed source cannot masquerade as one record", mixed, "split records"))
    mixed["items"].append({
        **copy.deepcopy(mixed["items"][0]),
        "guidance_id": "g-constraint-split",
        "source_id": "cccccccc",
        "source_clause": "mechanical-clause",
        "is_primary": False,
        "split_group_id": "split-c",
    })
    mixed["items"][2]["source_clause"] = "judgment-clause"
    mixed["items"][2]["split_group_id"] = "split-c"
    cases.append(case("explicit mixed-source split is valid", mixed))

    compound = copy.deepcopy(BASE)
    compound_child = copy.deepcopy(compound["items"][1])
    compound_child.update({
        "guidance_id": "g-procedure-second-clause",
        "source_clause": "second-procedure-clause",
        "is_primary": False,
        "split_group_id": "split-b",
    })
    compound["items"][1]["source_clause"] = "first-procedure-clause"
    compound["items"][1]["split_group_id"] = "split-b"
    compound["items"].append(compound_child)
    compound["source_analysis"] = [{
        "source_id": "bbbbbbbb", "mixed_type_detected": False,
        "clause_types": ["procedure"], "split_group_id": "split-b"}]
    compound_ok = not registry.validate_registry(compound)
    compound_ok = compound_ok and not registry.coverage_errors(
        compound, {item["source_id"] for item in BASE["items"]})
    print(f"{'PASS' if compound_ok else 'FAIL'}  explicit same-type compound split is valid")
    cases.append(compound_ok)

    active = {item["source_id"] for item in BASE["items"]}
    cases.append(case("exact active coverage", BASE) and not registry.coverage_errors(BASE, active))
    missing = set(active)
    missing.add("22222222")
    coverage = registry.coverage_errors(BASE, missing)
    coverage_pass = any("missing active source" in error for error in coverage)
    print(f"{'PASS' if coverage_pass else 'FAIL'}  missing active source is refused: {coverage}")
    cases.append(coverage_pass)

    duplicate = copy.deepcopy(BASE)
    duplicate["items"].append({**copy.deepcopy(duplicate["items"][0]), "guidance_id": "g-duplicate"})
    duplicate_errors = registry.coverage_errors(duplicate, active)
    duplicate_pass = any("multiple unsplit primary records" in error for error in duplicate_errors)
    print(f"{'PASS' if duplicate_pass else 'FAIL'}  duplicate unsplit source is refused: {duplicate_errors}")
    cases.append(duplicate_pass)

    source_map = {
        "control_catalog": {
            "deny-fixture": {
                "implementation": ["hooks/deny-fixture.py"],
                "test": ["ops/deny-fixture-selftest.py"],
                "failure_mode": "deny",
            },
            "surface-fixture": {
                "implementation": ["hooks/surface-fixture.py"],
                "test": ["ops/surface-fixture-selftest.py"],
                "failure_mode": "surface",
            },
        },
        "rule_controls": {
            "aaaaaaaa": {
                "category": "hard_pre_action", "enforcement_class": "deny_gate",
                "binding_moment": "before governed write", "control": "deny-fixture", "exceptions": "none",
            },
            "bbbbbbbb": {
                "category": "session_task_rail", "enforcement_class": "surfacing",
                "binding_moment": "at task startup", "control": "surface-fixture", "exceptions": "none",
            },
            "cccccccc": {
                "category": "judgment_advisory", "enforcement_class": "judgment_ambient",
                "why_unenforceable": "contextual quality judgment",
            },
        },
        "active_rule_ids": {"shared": ["aaaaaaaa", "bbbbbbbb"], "joe": ["cccccccc"]},
    }
    migration_manifest = {
        "manifest": "carr-guidance-migration",
        "schema_version": "1.0.0",
        "source_classification": "judgment_ambient",
        "entries": [{
            "source_id": "cccccccc", "name": "Fixture quality standard",
            "proposed_type": "rubric", "rationale": "quality is independently reviewable",
            "destination": "verification_rubric", "activation_trigger": "artifact is ready",
            "consumer": "independent_verifier", "verification": "fixture criterion review",
            "acceptance_criteria": ["fixture criterion passes"], "split_records": [],
        }],
    }
    compiled, compile_errors = registry.build_registry(source_map, migration_manifest)
    compile_pass = not compile_errors and not registry.validate_registry(compiled)
    compile_pass = compile_pass and not registry.coverage_errors(
        compiled, {"aaaaaaaa", "bbbbbbbb", "cccccccc"})
    compile_pass = compile_pass and registry.type_counts(compiled) == {
        "constraint": 1, "procedure": 1, "doctrine": 0, "rubric": 1,
        "preference": 0, "precedent": 0, "example": 0,
    }
    print(f"{'PASS' if compile_pass else 'FAIL'}  deterministic map + manifest compilation: {compile_errors}")
    cases.append(compile_pass)

    unbuilt_map = copy.deepcopy(source_map)
    unbuilt_map["rule_controls"]["aaaaaaaa"] = {
        "category": "judgment_advisory", "enforcement_class": "unbuilt",
        "planned_control": "future fixture",
    }
    _, unbuilt_errors = registry.build_registry(unbuilt_map, migration_manifest)
    unbuilt_pass = any("unbuilt control" in error for error in unbuilt_errors)
    print(f"{'PASS' if unbuilt_pass else 'FAIL'}  unbuilt constraint cannot enter active projection: {unbuilt_errors}")
    cases.append(unbuilt_pass)

    missing_manifest = copy.deepcopy(migration_manifest)
    missing_manifest["entries"] = []
    _, manifest_errors = registry.build_registry(source_map, missing_manifest)
    manifest_pass = any("judgment manifest coverage" in error for error in manifest_errors)
    print(f"{'PASS' if manifest_pass else 'FAIL'}  judgment manifest must exactly cover ambient set: {manifest_errors}")
    cases.append(manifest_pass)

    with tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False) as fixture:
        fixture.write(
            "source_id\tplain_name\told_classification\tproposed_type\trationale\t"
            "destination\tactivation_trigger\tconsumer\tverification\tsplit_records_json\n"
            "cccccccc\tFixture quality\tjudgment_ambient\trubric\tContextual quality standard\t"
            "verification-rubric\tartifact ready\tindependent verifier\tcriterion reviewed\t[]\n")
        fixture_path = fixture.name
    try:
        loaded, load_errors = registry.load_migration_manifest(fixture_path)
    finally:
        os.unlink(fixture_path)
    loader_pass = not load_errors and loaded["entries"][0]["source_id"] == "cccccccc"
    print(f"{'PASS' if loader_pass else 'FAIL'}  TSV migration manifest loader: {load_errors}")
    cases.append(loader_pass)

    with tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False) as fixture:
        fixture.write(
            "source_id\tplain_name\told_classification\tproposed_type\trationale\t"
            "destination\tactivation_trigger\tconsumer\tverification\tsplit_records_json\n"
            "cccccccc\tFixture quality\tjudgment_ambient\trubric\tGuidance for fixture quality.\t"
            "rubric-guidance\twhen fixture quality is relevant\tsession agent\t"
            "review application against the rule's stated outcome\t"
            "[{\"clause\":\"separable mechanical clause; enforcement-backlog candidate\","
            "\"type\":\"procedure\",\"destination\":\"rubric-guidance\","
            "\"activation_trigger\":\"when fixture quality is relevant\","
            "\"consumer\":\"session agent\",\"verification\":\"review application against the rule's stated outcome\"}]\n")
        fixture_path = fixture.name
    try:
        _, boilerplate_errors = registry.load_migration_manifest(fixture_path)
    finally:
        os.unlink(fixture_path)
    boilerplate_pass = any("generated boilerplate" in error for error in boilerplate_errors)
    print(f"{'PASS' if boilerplate_pass else 'FAIL'}  generated manifest boilerplate is refused: {boilerplate_errors}")
    cases.append(boilerplate_pass)

    # The reviewed manifest has fourteen compound sources.  Their child types
    # are deliberately asserted individually: a parent-field fallback used to
    # compile every child as the parent's proposed type.
    with open(os.path.join(REPO, "ops", "config", "rule-enforcement-map.json"), encoding="utf-8") as fh:
        reviewed_map = json.load(fh)
    reviewed_manifest, reviewed_errors = registry.load_migration_manifest(
        os.path.join(REPO, "audits", "guidance-migration-manifest.v1.tsv"))
    reviewed_registry, reviewed_compile_errors = registry.build_registry(
        reviewed_map, reviewed_manifest)
    expected_split_types = {
        "0c1bac27": ["rubric"], "179be4b8": ["procedure"],
        "1c42d80c": ["rubric"], "3d185f2b": ["procedure"],
        "424ba0cc": ["preference"], "6172e7d5": ["preference"],
        "6901cc3b": ["procedure"], "70e372f0": ["procedure"],
        "86647daf": ["procedure"], "9873a0d2": ["procedure"],
        "9e3fb6d0": ["procedure"], "eeb3d106": ["procedure"],
        "f0f9156e": ["preference"], "f5beac20": ["procedure"],
    }
    actual_split_types = {}
    for item in reviewed_registry.get("items", []):
        if not item.get("is_primary"):
            actual_split_types.setdefault(item["source_id"], []).append(item["guidance_type"])
    # These counts follow the ACTIVE rule set and have to move with it. A rule
    # absent from audits/guidance-migration-manifest.v1.tsv compiles as a
    # constraint, so activating one changes exactly one number here.
    # constraint 74 -> 75 on 2026-08-16, when rule 937252fb was activated: a
    # capability is not reported working until the first action a partner would
    # take has been performed against the real target. It is a constraint by
    # construction — it binds at a named moment and prohibits a claim — so it
    # needs no manifest row of its own, only this total following the store.
    # procedure 76 -> 77 on 2026-08-18, when rule bd4a6d22 was activated: a
    # permanently chosen state is accepted BY NAME in the check rather than
    # left red. Judging whether a state was chosen is not mechanical, but what
    # to do once it has been is, so it carries a manifest row typed procedure
    # rather than compiling as a constraint the way 937252fb did.
    # doctrine 14 -> 13 on 2026-08-20 when e7a620cc was retired: the broad
    # cross-session Dr. CRE persona was withdrawn while the real Doc app
    # doctrine remained intact.
    # procedure 77 -> 78 on 2026-08-23, when rule 3fa422b7 entered the map:
    # planned delivery mechanics — commit, push, PR, merge, clean up — are the
    # session's own to run and never a question for a partner. It is a session
    # rail rather than a prohibition, so it compiles as a procedure.
    # constraint 75 -> 76 on 2026-08-24, when rule 6cfb67f5 was activated: a
    # delegation names its specific model and reasoning effort, cheapest
    # qualified, capability as the floor. It binds at the dispatch moment and
    # prohibits a nameless handoff — a constraint by construction, like
    # 937252fb before it, so no manifest row, only this total following the
    # store.
    # constraint 76 -> 75, procedure 78 -> 79 on 2026-08-27 (WR-000019 slice
    # S4), when rule fb110a39 was reassigned from deny_gate to surfacing:
    # hooks/delegation-gate.py stopped denying anything (see
    # ops/config/rule-enforcement-map.json's note on this rule), so it no
    # longer binds as a governed refusal the compiler must type "constraint"
    # (enforcement_class in {deny_gate, schema, stop_gate}) — it is a session
    # rail that surfaces a reminder, exactly the shape the compiler types
    # "procedure" for any enforcement_class == "surfacing" rule, the same
    # transition 3fa422b7 made on 2026-08-23.
    # constraint 75 -> 73, procedure 79 -> 73, doctrine 13 -> 12, rubric
    # 37 -> 36, preference 12 -> 11 on 2026-08-27 (WR-000019 rule-retirement
    # batch, 11 ids retired): e065aa82 (deny_gate) and 937252fb (stop_gate)
    # each drop one constraint; 14e0408b, 3fa422b7, 581cb3fe, 634a2d94,
    # 75c2e4c9, and dff58fef (all surfacing) each drop one procedure;
    # d367188d (doctrine), 8117b414 (rubric), and 006a7eaa (preference) --
    # the three retired judgment_ambient ids -- each drop one of their
    # manifest-declared type. No rule was reclassified; every dropped count
    # is a whole rule leaving the active set, not a type change.
    # procedure 77 -> 78 on 2026-09-29: rule ede4b241 (cloud model choice) is a
    # judgment_ambient rule and owes a manifest row (procedure); one whole rule
    # ENTERING the active set, nothing reclassified.
    reviewed_counts = {
        "constraint": 70, "procedure": 78, "doctrine": 12, "rubric": 35,
        "preference": 12, "precedent": 3, "example": 0,
    }
    split_compile_pass = (
        not reviewed_errors and not reviewed_compile_errors
        and actual_split_types == expected_split_types
        and registry.type_counts(reviewed_registry) == reviewed_counts)
    print(f"{'PASS' if split_compile_pass else 'FAIL'}  all 14 split clauses retain their declared types")
    cases.append(split_compile_pass)

    # The map is a complete inventory of ACTIVE rules; the registry's corpus is
    # that inventory minus the one surface it does not compile. Ask the compiler
    # which ids those are rather than restating the rule here (2026-08-23).
    reviewed_excluded = registry.excluded_source_ids(reviewed_map)
    active_source_ids = sorted({
        source_id for scope in reviewed_map["active_rule_ids"].values()
        for source_id in scope if source_id not in reviewed_excluded})
    source_rule_ids = {
        source_id: f"00000000-0000-0000-0000-{index:012x}"
        for index, source_id in enumerate(active_source_ids, start=1)
    }
    doctrine_ids = sorted(item["guidance_id"] for item in reviewed_registry["items"]
                         if item["guidance_type"] == "doctrine")
    doctrine_bindings = {
        guidance_id: [{
            "concept_id": "10000000-0000-0000-0000-000000000001",
            "doctrine_section_id": "20000000-0000-0000-0000-000000000001",
            "reason": "fixture approved situation binding",
        }]
        for guidance_id in doctrine_ids
    }
    constitution = sorted(item["guidance_id"] for item in reviewed_registry["items"]
                          if item.get("is_primary"))[:8]
    manifest_args = {
        "constitution_guidance_ids": constitution,
        "source_manifest_provenance": {
            "path": "audits/guidance-migration-manifest.v1.tsv", "sha256": "a" * 64,
            "manifest": "carr-guidance-migration", "schema_version": "1.0.0",
            "source_classification": "judgment_ambient", "entry_count": 94,
        },
        "base_inventory": {
            "path": "ops/config/rule-enforcement-map.json", "sha256": "b" * 64,
            "active_source_ids": active_source_ids, "source_rule_ids": source_rule_ids,
        },
        "situation_mapping_bindings": doctrine_bindings,
    }
    activation_manifest, activation_errors = registry.build_activation_manifest(
        reviewed_registry, **manifest_args)
    activation_again, activation_again_errors = registry.build_activation_manifest(
        reviewed_registry, **manifest_args)
    tampered_activation_manifest = copy.deepcopy(activation_manifest)
    tampered_activation_manifest["source_manifest"]["sha256"] = "c" * 64
    activation_pass = (
        not activation_errors and not activation_again_errors
        and activation_manifest == activation_again
        and activation_manifest["schema"] == "guidance-activation-manifest/v1"
        and activation_manifest["canonicalization"] == "utf8-json-sort-keys-compact-newline/v1"
        and activation_manifest["constitution_source_rule_ids"] == sorted(
            source_rule_ids[item["source_id"]]
            for item in reviewed_registry["items"] if item["guidance_id"] in constitution)
        # 216 -> 217 on 2026-08-16 with rule 937252fb, 217 -> 218 on 2026-08-18
        # with rule bd4a6d22, then 218 -> 217 when e7a620cc was retired on
        # 2026-08-20. One entry per compiled guidance item, so this total
        # follows the same lifecycle changes as the counts above, and the
        # manifest entry_count above moves with the TSV that feeds it.
        # 217 -> 218 on 2026-08-23 with rule 3fa422b7, one compiled item.
        # 218 -> 219 on 2026-08-24 with rule 6cfb67f5, one compiled item.
        # 219 -> 208 on 2026-08-27 (WR-000019 rule-retirement batch): 11
        # rules left the active set (no splits among them), one compiled
        # item fewer each. Manifest entry_count above moves 93 -> 90 in the
        # same change (3 of the 11 retired ids -- 006a7eaa, 8117b414,
        # d367188d -- were judgment_ambient rows in the TSV; the other 8
        # were built classes with no manifest row to begin with).
        # 90 -> 91 and 208 -> 209 on 2026-09-03 (WR-000048 activation unblock):
        # rule 1fcaa63a, the heavy-build-protocol rule taught 2026-09-01, was
        # added to the enforcement map as judgment_ambient, so it owes a
        # manifest row (procedure) and compiles to one more guidance item.
        # procedure 73 -> 74. Nothing was reclassified; this is one whole rule
        # ENTERING the active set, the mirror of the 2026-08-27 retirement batch.
        # Current map tracks the live active rule set; retired rows leave the manifest.
        # Historical 209 -> 211 on 2026-09-05: the isolated-delivery and create-missing-
        # verbs rules are both session rails, so each compiles as one procedure.
        and len(activation_manifest["entries"]) == len(reviewed_registry["items"])
        and registry.activation_manifest_bytes(activation_manifest).endswith(b"\n")
        and len(registry.activation_manifest_sha256(activation_manifest)) == 64
        and registry.activation_manifest_sha256(activation_manifest)
        != registry.activation_manifest_sha256(tampered_activation_manifest))
    print(f"{'PASS' if activation_pass else 'FAIL'}  canonical activation manifest binds exact revisions and inputs: {activation_errors}")
    cases.append(activation_pass)

    bad_bindings = copy.deepcopy(doctrine_bindings)
    bad_bindings[doctrine_ids[0]] = ["free-form-retrieval-key"]
    _, bad_binding_errors = registry.build_activation_manifest(
        reviewed_registry, **{**manifest_args, "situation_mapping_bindings": bad_bindings})
    binding_refusal_pass = any("must be an object" in error for error in bad_binding_errors)
    print(f"{'PASS' if binding_refusal_pass else 'FAIL'}  activation manifest refuses free-form doctrine bindings")
    cases.append(binding_refusal_pass)

    empty_bindings = copy.deepcopy(doctrine_bindings)
    empty_bindings[doctrine_ids[0]] = []
    _, empty_binding_errors = registry.build_activation_manifest(
        reviewed_registry, **{**manifest_args, "situation_mapping_bindings": empty_bindings})
    empty_binding_refusal = any("non-empty array" in error for error in empty_binding_errors)
    print(f"{'PASS' if empty_binding_refusal else 'FAIL'}  activation manifest refuses unmapped doctrine")
    cases.append(empty_binding_refusal)

    if not all(cases):
        return 1
    print(f"guidance-registry-selftest: {len(cases)} seeded cases passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
