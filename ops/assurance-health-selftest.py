#!/usr/bin/env python3
"""Executable acceptance tests for the A01 assurance health projection.

These tests are deliberately provider-free, network-free and database-free: the
projection under test is a pure function, so every fact it needs is supplied
here as data.  They pin the decisions this module must make before any loader,
CLI or persistence phase is allowed near it:

  * the six display states, each reached only by its own evidence, and a
    deterministic precedence between them;
  * a matrix in which EVERY layer, degraded EVERY way the settled contract
    names, never renders green;
  * four preactivation receipts with four distinct exact identities, each of
    which independently blocks activation when it is absent, failed, stale,
    mismatched, self-attested or substituted;
  * an actual business outcome that is its own evidence slot and cannot be
    filled by completion enforcement_closure, merge/CI, enabled configuration,
    telemetry, activation, or a candidate-outcome pass;
  * one workflow degrading act -> draft -> read -> unavailable while an
    unrelated explicitly bound scope keeps its own evidence-derived state,
    byte for byte;
  * F09 workflow truth governing disabled, conflicting and not-operational, with
    no second workflow/status/receipt/incident registry anywhere in the module.

The workflow truth rows below are built by calling the real V5-F09 adapter, not
by hand: if the two modules ever disagree about a workflow's state, these tests
fail rather than paper over it.
"""
from __future__ import annotations

import copy
import itertools
import json
import pickle
import re
import sys
import types
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

failures: list[str] = []
total = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global total
    total += 1
    ok = bool(condition)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}{'' if ok or not detail else ' — ' + detail}")
    if not ok:
        failures.append(name)


NOW = "2026-09-09T12:00:00+00:00"
OBSERVED = "2026-09-09T11:00:00+00:00"
CURRENT_UNTIL = "2026-09-09T18:00:00+00:00"
ALREADY_EXPIRED = "2026-09-09T06:00:00+00:00"
AFTER_NOW = "2026-09-09T23:00:00+00:00"
MAX_AGE = 900

SCOPE = {
    "workflow_key": "assurance-fabric-child",
    "workflow_version": 1,
    "work_request_id": "wr-a01-0001",
    "owner": "assurance_fabric_child_owner",
    "incident_refs": ["incident:2026-09-08:outcome-feedback-drift"],
    "recovery_refs": ["recovery:2026-09-08:reissue-independent-artifact-review"],
}
UNRELATED = {
    "workflow_key": "notes-sweep-hourly",
    "workflow_version": 1,
    "work_request_id": "wr-a01-0002",
    "owner": "notes_owner",
}

# Every projected row this suite produces, so vocabulary closure and the
# six-state coverage can be asserted over the whole matrix rather than a sample.
PROJECTED: list[dict] = []


def _identity(scope):
    return {name: scope[name] for name in
            ("workflow_key", "workflow_version", "work_request_id") if name in scope}


def _truth(scope, *, enabled=True, shadow="accepted", canary="accepted",
           declared=True, registered=True, extra_acceptances=()):
    """One row from the real V5-F09 adapter; this suite invents no workflow truth."""
    from lib.control_plane_workflow_truth import workflow_truth_row
    key, version = scope["workflow_key"], scope["workflow_version"]
    acceptances = []
    for mode, status in (("shadow", shadow), ("canary", canary)):
        if status:
            acceptances.append({"workflow_key": key, "workflow_version": version,
                                "mode": mode, "status": status})
    acceptances.extend(extra_acceptances)
    return workflow_truth_row(
        workflow_key=key, workflow_version=version,
        declaration=({"key": key, "version": version, "enabled": enabled,
                      "legacy_schedule": {"provider": "none", "status": "disabled"}}
                     if declared else None),
        definition=({"key": key, "version": version, "enabled": enabled,
                     "execution_contract": {}, "legacy_disabled_at": None}
                    if registered else None),
        acceptances=acceptances, surfaces=[], completion=None,
        duplicate_group_identities={}, observation_max_age_seconds=MAX_AGE, now=NOW)


SLOT_BINDING = {
    "artifact_assessment": {
        "repository_commit_sha": "968541ce072af2aee9a0706b32d81fb46879c4c3",
        "repository_tree_sha": "d6f695df31341b5613503f55331a8bfa1be6967e",
        "reviewer_fact_id": "reviewer-fact:a01:0001"},
    "execution_assessment": {
        "attempt_id": "attempt:a01:0001", "envelope_digest": "envelope:a01:0001",
        "plan_hash": "plan-hash:a01:0001"},
    "controller_assessment": {
        "controller_state": "running", "readback_source": "controller:carr-jobs",
        "readback_at": OBSERVED},
    "candidate_outcome_oracle": {
        "governed_data_ref": "governed:a01:fixture:0001", "environment": "isolated-disposable",
        "expected_result_ref": "expected:a01:0001", "equivalence_comparator": "exact-json.v1",
        "component_versions": {"comparator": "1.4.0", "runtime": "3.12.4"}},
    "activation_readback": {
        "activation_id": "activation:a01:0001", "readback_source": "controller:carr-jobs",
        "readback_at": OBSERVED},
    "actual_business_outcome": {
        "outcome_feedback_ref": "sourced-feedback:a01:0001",
        "outcome_feedback_hash": "feedback-hash:a01:0001",
        "acceptance_receipt_id": "acceptance-receipt:a01:0001"},
}


def _ev(slot, *, scope=SCOPE, status="pass", expires=CURRENT_UNTIL, observed=OBSERVED,
        **overrides):
    from lib.assurance_health import _SLOT_ADMISSIBLE_BASES
    record = {
        "layer": slot,
        "basis": _SLOT_ADMISSIBLE_BASES[slot][0],
        "status": status,
        "evidence_ref": f"ref:{slot}:0001",
        "evidence_digest": f"digest:{slot}:0001",
        "evaluator_identity": f"evaluator:{slot}",
        "subject_identity": f"subject:{slot}",
        "observed_at": observed,
        "expires_at": expires,
        "scope": _identity(scope),
    }
    record.update(SLOT_BINDING[slot])
    record.update(overrides)
    return record


def _evidence(scope=SCOPE, **overrides):
    from lib.assurance_health import _EVIDENCE_SLOTS
    bundle = {slot: _ev(slot, scope=scope) for slot in _EVIDENCE_SLOTS}
    bundle.update(overrides)
    return bundle


# A sentinel, because ``None`` is itself a meaningful input to every one of these
# arguments and must be passable through to the projection unchanged.
_DEFAULT = object()


def _row(scope=SCOPE, truth=_DEFAULT, evidence=_DEFAULT, now=NOW):
    """One verdict from the MODULE-PRIVATE label logic, through the test-only hook.

    THE ONLY DOOR TO THE CLASSIFIER.  The predicate is not exported and is not in
    ``lib.assurance_health.__all__``; this suite reaches it through
    ``_test_only_hypothetical_row``, whose answer comes back under
    ``would_be_healthy_if_authoritative`` because that is what it is -- what the
    ladder WOULD say if these fixture shapes were evidence an owner had admitted.
    No public callable in that module will produce this answer from these shapes,
    and ``public_surface_guard_checks`` parses the file to keep it that way.

    Fixture-shaped evidence is exactly what the ladder must be tested against --
    including the six-passing shape, which is the only way to prove it really
    requires all six layers.
    """
    from lib.assurance_health import _test_only_hypothetical_row
    row = _test_only_hypothetical_row(
        scope=scope,
        workflow_truth=_truth(scope) if truth is _DEFAULT else truth,
        evidence=_evidence(scope) if evidence is _DEFAULT else evidence,
        now=now)["would_be_healthy_if_authoritative"]
    PROJECTED.append(row)
    return row



def display_state_checks(health) -> None:
    """Each of the six states, reached only by its own evidence."""
    healthy = _row()
    check("six current, distinct, independently evaluated, exactly bound passing facts "
          "with F09 live admission are the only route to healthy",
          healthy["state"] == "healthy" and healthy["green"] is True
          and healthy["capability_stage"] == "act"
          and healthy["capabilities_withdrawn"] == []
          and healthy["recovery"]["required_evidence"] == [],
          json.dumps(healthy["state_reason"]))

    degraded = _row(evidence=_evidence(actual_business_outcome=_ev(
        "actual_business_outcome", status="fail")))
    check("a failed layer degrades the scope and withdraws exactly the capability it earned",
          degraded["state"] == "degraded" and degraded["green"] is False
          and degraded["capability_stage"] == "draft"
          and degraded["capabilities_withdrawn"] == ["act"]
          and degraded["capabilities_retained"] == ["draft", "read"],
          json.dumps(degraded["capability_stage"]))

    failed = _row(evidence=_evidence(artifact_assessment=_ev(
        "artifact_assessment", status="fail")))
    check("a failure that withdraws even read capability is failed, not degraded",
          failed["state"] == "failed" and failed["capability_stage"] == "unavailable"
          and failed["capabilities_retained"] == [])

    from lib.control_plane_workflow_truth import UNREADABLE
    unknown = _row(truth=UNREADABLE)
    check("unreadable authoritative workflow truth is unknown, never a guessed state",
          unknown["state"] == "unknown" and unknown["green"] is False
          and unknown["workflow_truth"]["available"] is False)

    disabled = _row(truth=_truth(SCOPE, enabled=False))
    check("disabled is emitted only from authoritative F09 workflow truth",
          disabled["state"] == "disabled" and disabled["green"] is False
          and disabled["workflow_truth"]["state"] == "declared_disabled")

    not_yet = _row(evidence=_evidence(activation_readback=None,
                                      actual_business_outcome=None))
    check("four passing preactivation receipts with no activation or outcome evidence "
          "are not-yet-operational, never healthy",
          not_yet["state"] == "not-yet-operational"
          and not_yet["preactivation_receipts_passing"] is True
          and not_yet["green"] is False)

    observed_states = {row["state"] for row in PROJECTED}
    check("all six display states are reachable and no seventh exists",
          observed_states == set(health._DISPLAY_STATES),
          f"missing={sorted(set(health._DISPLAY_STATES) - observed_states)} "
          f"extra={sorted(observed_states - set(health._DISPLAY_STATES))}")

    # DETERMINISM. Same inputs, same bytes -- and the order a caller happened to
    # build its evidence mapping in is not an input.
    repeated = json.dumps(_row(), sort_keys=True)
    reordered = _evidence()
    shuffled = {slot: reordered[slot] for slot in reversed(list(reordered))}
    check("the projection is deterministic and independent of caller key order",
          repeated == json.dumps(_row(), sort_keys=True)
          and repeated == json.dumps(_row(evidence=shuffled), sort_keys=True))


def never_green_matrix_checks(health) -> None:
    """EVERY layer, degraded EVERY way the contract names, never renders green."""
    from lib.control_plane_workflow_truth import UNREADABLE

    def degradations(slot):
        borrowed = ("execution_assessment" if slot == "artifact_assessment"
                    else "artifact_assessment")
        return {
            "absent": (None, "missing"),
            "unreadable": (UNREADABLE, "unreadable"),
            "mismatched": (_ev(slot, scope=UNRELATED), "mismatched"),
            "stale": (_ev(slot, expires=ALREADY_EXPIRED), "stale"),
            "self_attested": (_ev(slot, subject_identity=f"evaluator:{slot}"), "self_attested"),
            "skipped": (_ev(slot, status="skipped"), "skipped"),
            "untested": (_ev(slot, status="untested"), "untested"),
            "failed": (_ev(slot, status="fail"), "failed"),
            "errored": (_ev(slot, status="error"), "error"),
            "conflicting": (_ev(slot, status="conflicting"), "conflicting"),
            "future_dated": (_ev(slot, observed=AFTER_NOW), "conflicting"),
            "refused_substitute": (_ev(slot, basis="enforcement_closure"), "refused_substitute"),
            "indistinct": (_ev(slot, evidence_ref=f"ref:{borrowed}:0001",
                               evidence_digest=f"digest:{borrowed}:0001"), "indistinct"),
        }

    greens: list[str] = []
    misclassified: list[str] = []
    cells = 0
    for slot in health._EVIDENCE_SLOTS:
        for label, (value, expected) in degradations(slot).items():
            cells += 1
            row = _row(evidence=_evidence(**{slot: value}))
            if row["green"] or row["state"] == "healthy":
                greens.append(f"{slot}/{label}")
            if row["evidence"][slot]["state"] != expected:
                misclassified.append(
                    f"{slot}/{label}={row['evidence'][slot]['state']} (want {expected})")
    check(f"no degraded layer ever renders green ({cells} matrix cells)",
          not greens, f"green={greens}")
    check("every degradation is classified as the exact evidence state it is",
          not misclassified, f"misclassified={misclassified}")

    # The same fact stated the way the acceptance predicate states it.
    unproven = _row(evidence=_evidence(
        artifact_assessment=None, execution_assessment=UNREADABLE,
        controller_assessment=_ev("controller_assessment", status="skipped"),
        candidate_outcome_oracle=_ev("candidate_outcome_oracle", expires=ALREADY_EXPIRED)))
    check("missing, unreadable, skipped and stale evidence together are never green and "
          "each is named separately",
          unproven["green"] is False
          and unproven["evidence"]["artifact_assessment"]["state"] == "missing"
          and unproven["evidence"]["execution_assessment"]["state"] == "unreadable"
          and unproven["evidence"]["controller_assessment"]["state"] == "skipped"
          and unproven["evidence"]["candidate_outcome_oracle"]["state"] == "stale")

    check("an absent layer is reported as absent, never as an empty passing receipt",
          unproven["evidence"]["artifact_assessment"]["present"] is False
          and "evidence_ref" not in unproven["evidence"]["artifact_assessment"]
          and unproven["missing_layers"] == ["artifact_assessment"])


def preactivation_chain_checks(health) -> None:
    """Four layers, four distinct exact identities, four independent blocks."""
    healthy = _row()
    identities = [(healthy["evidence"][slot]["evidence_ref"],
                   healthy["evidence"][slot]["evidence_digest"])
                  for slot in health._EVIDENCE_SLOTS]
    check("the six layers carry six distinct exact evidence identities",
          len(set(identities)) == len(health._EVIDENCE_SLOTS))

    non_blocking: list[str] = []
    for slot in health._PREACTIVATION_SLOTS:
        for label, value in (("absent", None),
                             ("failed", _ev(slot, status="fail")),
                             ("mismatched", _ev(slot, scope=UNRELATED))):
            row = _row(evidence=_evidence(**{slot: value}))
            if row["green"] or slot not in row["impact"]["blocking_act"]:
                non_blocking.append(f"{slot}/{label}")
    check("removing, failing or mismatching each preactivation receipt independently "
          "blocks activation and names itself as the blocker",
          not non_blocking, f"did-not-block={non_blocking}")

    reused = _row(evidence=_evidence(
        candidate_outcome_oracle=_ev("candidate_outcome_oracle",
                                     evidence_digest="digest:execution_assessment:0001")))
    check("one receipt can never satisfy two layers: a reused digest is indistinct, "
          "not two independent facts",
          reused["green"] is False
          and reused["evidence"]["candidate_outcome_oracle"]["state"] == "indistinct"
          and reused["evidence"]["execution_assessment"]["state"] == "indistinct"
          and reused["state"] == "unknown")

    self_signed = _row(evidence=_evidence(artifact_assessment=_ev(
        "artifact_assessment", evaluator_identity="session:a01-author",
        subject_identity="session:a01-author")))
    check("an assessment whose evaluator is its own subject is self-attested, not review",
          self_signed["green"] is False
          and self_signed["evidence"]["artifact_assessment"]["state"] == "self_attested")

    controller_by_config = _row(evidence=_evidence(controller_assessment=_ev(
        "controller_assessment", basis="enabled_configuration")))
    check("enabled configuration is refused as controller truth: it is never a readback",
          controller_by_config["green"] is False
          and controller_by_config["evidence"]["controller_assessment"]["state"]
          == "refused_substitute")

    enabled_only = _row(truth=_truth(SCOPE, canary=None))
    check("an enabled definition without the acceptance its live tier requires is "
          "not-yet-operational even when all six evidence layers pass",
          enabled_only["state"] == "not-yet-operational" and enabled_only["green"] is False
          and enabled_only["workflow_truth"]["state"] == "enabled_canary_eligible"
          and "workflow_live_admissible" in enabled_only["impact"]["blocking_act"])


def actual_outcome_separation_checks(health) -> None:
    """The last slot is its own fact, and nothing else may fill it."""
    activated = _row(evidence=_evidence(actual_business_outcome=None))
    check("four preactivation passes plus activation readback, with no accepted outcome "
          "receipt, remains not-yet-operational",
          activated["state"] == "not-yet-operational" and activated["green"] is False
          and activated["evidence"]["activation_readback"]["state"] == "passing"
          and activated["evidence"]["actual_business_outcome"]["state"] == "missing")

    accepted = _row()
    check("only an exact current accepted sourced outcome-feedback receipt earns healthy",
          accepted["state"] == "healthy"
          and accepted["evidence"]["actual_business_outcome"]["basis"]
          == "accepted_sourced_outcome_feedback_receipt"
          and accepted["evidence"]["actual_business_outcome"]["outcome_feedback_hash"]
          == "feedback-hash:a01:0001"
          and accepted["evidence"]["actual_business_outcome"]["acceptance_receipt_id"]
          == "acceptance-receipt:a01:0001")

    accepted_elsewhere = _row(evidence=_evidence(actual_business_outcome=_ev(
        "actual_business_outcome",
        scope={**UNRELATED, "work_request_id": "wr-a01-0099"})))
    check("an accepted outcome receipt for a different Work Request identity never fills "
          "this scope's outcome slot",
          accepted_elsewhere["green"] is False
          and accepted_elsewhere["evidence"]["actual_business_outcome"]["state"]
          == "mismatched")

    admitted: list[str] = []
    for basis in ("enforcement_closure", "completion_lifecycle", "merge_or_ci_result",
                  "enabled_configuration", "telemetry", "self_attestation",
                  "candidate_outcome_pass"):
        row = _row(evidence=_evidence(actual_business_outcome=_ev(
            "actual_business_outcome", basis=basis)))
        if row["green"] or row["evidence"]["actual_business_outcome"]["state"] \
                != "refused_substitute":
            admitted.append(basis)
        if basis == "enforcement_closure":
            check("completion enforcement_closure offered as the business outcome is "
                  "refused by name, and the refusal is printed",
                  row["state"] == "degraded"
                  and any("enforcement_closure is refused as a substitute" in reason
                          for reason in row["reasons"]),
                  json.dumps(row["reasons"]))
    check("no lifecycle, merge/CI, configuration, telemetry, self-attested or "
          "candidate-outcome substitute can fill the actual-business-outcome slot",
          not admitted, f"admitted={admitted}")

    try:
        _row(evidence=_evidence(actual_business_outcome=_ev("candidate_outcome_oracle")))
        cross_layer_refused = False
    except health._AssuranceHealthContractError:
        cross_layer_refused = True
    check("a candidate-outcome receipt handed to the outcome slot is refused outright: "
          "no layer is ever inferred from another",
          cross_layer_refused)

    out_of_order = _row(evidence=_evidence(activation_readback=None))
    check("an accepted outcome claimed for a scope with no current activation readback "
          "is contradictory, never a pass",
          out_of_order["green"] is False
          and out_of_order["evidence"]["actual_business_outcome"]["state"] == "conflicting"
          and out_of_order["state"] == "unknown")


def scoped_degradation_checks(health) -> None:
    """One workflow walks the ladder; an unrelated bound scope does not move."""
    from lib.assurance_health import _test_only_hypothetical_census
    def assurance_health(**kwargs):
        return _test_only_hypothetical_census(**kwargs)["would_be_healthy_if_authoritative"]

    def project(**failing):
        bundles = [
            {"scope": SCOPE, "workflow_truth": _truth(SCOPE),
             "evidence": _evidence(**{slot: _ev(slot, status="fail")
                                      for slot in failing})},
            {"scope": UNRELATED, "workflow_truth": _truth(UNRELATED),
             "evidence": _evidence(UNRELATED)},
        ]
        projection = assurance_health(scopes=bundles, now=NOW)
        PROJECTED.extend(projection["rows"])
        rows = {row["scope"]["work_request_id"]: row for row in projection["rows"]}
        return projection, rows

    steps = [
        ({}, "healthy", "act"),
        ({"actual_business_outcome": True}, "degraded", "draft"),
        ({"actual_business_outcome": True, "controller_assessment": True},
         "degraded", "read"),
        ({"actual_business_outcome": True, "controller_assessment": True,
          "artifact_assessment": True}, "failed", "unavailable"),
    ]
    walked: list[str] = []
    unrelated_rows: list[str] = []
    exposure_gaps: list[str] = []
    for failing, expected_state, expected_stage in steps:
        projection, rows = project(**failing)
        affected = rows["wr-a01-0001"]
        walked.append(f"{affected['state']}/{affected['capability_stage']}")
        if affected["state"] != expected_state or affected["capability_stage"] != expected_stage:
            walked[-1] += f" (want {expected_state}/{expected_stage})"
        unrelated_rows.append(json.dumps(rows["wr-a01-0002"], sort_keys=True))
        if affected["state"] in ("degraded", "failed"):
            recovery = affected["recovery"]
            named = {item["slot"] for item in recovery["required_evidence"]}
            if not (recovery["owner"] == SCOPE["owner"]
                    and set(failing).issubset(named)
                    and recovery["incident_refs"] == SCOPE["incident_refs"]
                    and recovery["recovery_refs"] == SCOPE["recovery_refs"]
                    and affected["impact"]["capabilities_withdrawn"]
                    and affected["impact"]["scope_limited_to"] == _identity(SCOPE)
                    and all(affected["evidence"][slot]["evidence_ref"] for slot in failing)):
                exposure_gaps.append(affected["state"])

    check("one workflow degrades act -> draft -> read -> unavailable, one stage per "
          "injected failure and never by a jump",
          walked == ["healthy/act", "degraded/draft", "degraded/read", "failed/unavailable"],
          " | ".join(walked))
    check("every degradation exposes owner, the exact failing evidence, the impact it "
          "bounds and the recovery it requires",
          not exposure_gaps, f"incomplete={exposure_gaps}")
    check("an unrelated explicitly bound scope keeps its evidence-derived state, byte "
          "for byte, through every injected failure",
          len(set(unrelated_rows)) == 1
          and json.loads(unrelated_rows[0])["state"] == "healthy")

    projection, rows = project(actual_business_outcome=True)
    check("the projection summary counts what it projected and scopes the degradation "
          "to the exact bound identity",
          projection["summary"]["scopes"] == 2
          and projection["summary"]["states"]["degraded"] == 1
          and projection["summary"]["states"]["healthy"] == 1
          and projection["summary"]["green"] == 1
          # Named by workflow AND Work Request: a bare Work Request id does not say
          # which workflow degraded, and a workflow-only scope has no such id at all.
          and projection["summary"]["degraded_scopes"]
          == ["assurance-fabric-child@v1/wr-a01-0001"])


def workflow_truth_governance_checks(health) -> None:
    """F09 owns workflow truth; this projection consumes it and builds no second one."""
    from lib.control_plane_workflow_truth import (
        SCHEMA_VERSION as F09_SCHEMA_VERSION, UNREADABLE)

    check("the workflow authority is the delivered F09 adapter, not a local copy",
          health._WORKFLOW_TRUTH_SCHEMA_VERSION == F09_SCHEMA_VERSION)

    conflicting = _row(truth=_truth(SCOPE, extra_acceptances=[
        {"workflow_key": SCOPE["workflow_key"], "workflow_version": 1,
         "mode": "shadow", "status": "rejected"}]))
    check("F09 conflicting workflow truth governs the projection: contradictory truth "
          "is unknown even when all six evidence layers pass",
          conflicting["workflow_truth"]["state"] == "conflict"
          and conflicting["state"] == "unknown" and conflicting["green"] is False)

    unregistered = _row(truth=_truth(SCOPE, registered=False))
    check("a declared workflow with no registered definition is not-yet-operational",
          unregistered["workflow_truth"]["state"] == "unregistered"
          and unregistered["state"] == "not-yet-operational"
          and unregistered["green"] is False)

    disabled = _row(truth=_truth(SCOPE, enabled=False),
                    evidence=_evidence(artifact_assessment=_ev(
                        "artifact_assessment", status="fail")))
    check("a deliberately disabled workflow reads disabled and still prints its failing "
          "evidence rather than being called a live failure",
          disabled["state"] == "disabled"
          and disabled["evidence"]["artifact_assessment"]["state"] == "failed"
          and disabled["failing_layers"] == ["artifact_assessment"]
          and disabled["green"] is False)

    check("an unreadable census input holds the whole scope at unknown",
          _row(truth=UNREADABLE)["state"] == "unknown")

    forged = dict(_truth(SCOPE))
    forged["schema_version"] = "assurance-health-local-workflow-registry.v1"
    try:
        _row(truth=forged)
        second_registry_refused = False
    except health._AssuranceHealthContractError:
        second_registry_refused = True
    check("a workflow row from anywhere but the F09 adapter is refused, so no second "
          "workflow/status registry can grow here",
          second_registry_refused)

    try:
        _row(truth=_truth(UNRELATED))
        wrong_row_refused = False
    except health._AssuranceHealthContractError:
        wrong_row_refused = True
    check("a workflow truth row for a different workflow is refused, never joined",
          wrong_row_refused)


    # Mutate the real F09 projection after constructing valid inputs.  Coercible
    # values must not borrow the healthy result of the integer version they mimic.
    bad_versions: tuple[Any, ...] = (True, False, 1.9, 1.0, "1", "01", None, [], {}, 0, -1, 2)
    for bad_version in bad_versions:
        truth = {**_truth(SCOPE), "workflow_version": bad_version}
        try:
            _row(truth=truth)
            refused = False
        except health._AssuranceHealthContractError:
            refused = True
        check(f"workflow truth version {bad_version!r} ({type(bad_version).__name__}) "
              "cannot join integer scope version 1", refused)

    bad_keys: tuple[Any, ...] = (None, 123, True, [], {}, "", " " + str(SCOPE["workflow_key"]))
    for bad_key in bad_keys:
        try:
            _row(truth={**_truth(SCOPE), "workflow_key": bad_key})
            refused = False
        except health._AssuranceHealthContractError:
            refused = True
        check(f"workflow truth key {bad_key!r} cannot join a different scope", refused)

    check("the exact F09 string key and positive integer version remain healthy",
          _row(truth=_truth(SCOPE))["green"] is True)


def scope_identity_checks(health) -> None:
    """Exact identities only: no name, label or title ever participates in a join."""
    check("a scope is bound by exact identities and nothing else",
          health._SCOPE_IDENTITY_FIELDS
          == ("workflow_key", "workflow_version", "work_request_id"))

    labelled = _row(
        scope={**SCOPE, "human_label": "Assurance Fabric — child A",
               "title": "child outcome"},
        evidence=_evidence(artifact_assessment=_ev(
            "artifact_assessment",
            scope={**_identity(SCOPE), "human_label": "a completely different label"})))
    check("human labels are carried by neither the scope nor the evidence join: the same "
          "identities project identically however they are labelled",
          json.dumps(labelled, sort_keys=True) == json.dumps(_row(), sort_keys=True))

    same_label = _row(evidence=_evidence(execution_assessment=_ev(
        "execution_assessment",
        scope={"workflow_key": SCOPE["workflow_key"], "workflow_version": 2,
               "work_request_id": SCOPE["work_request_id"]})))
    check("an identical label with an unequal exact identity is mismatched, never matched",
          same_label["evidence"]["execution_assessment"]["state"] == "mismatched"
          and same_label["green"] is False and same_label["state"] == "unknown")

    wrong_request = _row(evidence=_evidence(actual_business_outcome=_ev(
        "actual_business_outcome",
        scope={**_identity(SCOPE), "work_request_id": "wr-a01-0777"})))
    check("evidence bound to another Work Request identity is refused for this scope",
          wrong_request["evidence"]["actual_business_outcome"]["state"] == "mismatched"
          and wrong_request["green"] is False)

    from lib.assurance_health import _test_only_hypothetical_census
    def assurance_health(**kwargs):
        return _test_only_hypothetical_census(**kwargs)["would_be_healthy_if_authoritative"]
    try:
        assurance_health(scopes=[
            {"scope": SCOPE, "workflow_truth": _truth(SCOPE), "evidence": _evidence()},
            {"scope": SCOPE, "workflow_truth": _truth(SCOPE), "evidence": _evidence()},
        ], now=NOW)
        duplicate_refused = False
    except health._AssuranceHealthContractError:
        duplicate_refused = True
    check("one exact scope has exactly one projected state; a doubly bound scope is refused",
          duplicate_refused)


def refusal_checks(health) -> None:
    """A malformed input is refused, never guessed at."""
    from lib.control_plane_workflow_truth import UNREADABLE

    incomplete_scope_evidence = _ev("execution_assessment")
    del incomplete_scope_evidence["scope"]["workflow_version"]

    # The malformed input is the ONLY malformed thing in each case: a bad scope is
    # paired with sound truth and evidence so the refusal cannot come from elsewhere.
    accepted: list[str] = []
    for label, kwargs in (
        ("evidence that does not name every layer explicitly",
         {"evidence": {slot: _ev(slot) for slot in health._PREACTIVATION_SLOTS}}),
        ("an evidence slot outside the closed set",
         {"evidence": {**_evidence(), "vibes_assessment": _ev("artifact_assessment")}}),
        ("an evidence status outside the closed set",
         {"evidence": _evidence(artifact_assessment=_ev(
             "artifact_assessment", status="looks-fine"))}),
        ("a basis outside the closed set",
         {"evidence": _evidence(controller_assessment=_ev(
             "controller_assessment", basis="someone-said-so"))}),
        ("evidence with no expiry from its own source",
         {"evidence": _evidence(candidate_outcome_oracle={
             key: value for key, value in _ev("candidate_outcome_oracle").items()
             if key != "expires_at"})}),
        ("evidence with no exact scope identity",
         {"evidence": _evidence(
             execution_assessment=incomplete_scope_evidence)}),
        ("evidence missing its layer-specific exact binding",
         {"evidence": _evidence(artifact_assessment={
             key: value for key, value in _ev("artifact_assessment").items()
             if key != "repository_tree_sha"})}),
        ("an oracle receipt with no component versions",
         {"evidence": _evidence(candidate_outcome_oracle=_ev(
             "candidate_outcome_oracle", component_versions={}))}),
        ("a scope with no owner",
         {"scope": {key: value for key, value in SCOPE.items() if key != "owner"},
          "truth": _truth(SCOPE), "evidence": _evidence()}),
        # A scope with no Work Request identity is NOT refused any more: it is a
        # legitimate workflow-only binding whose outcome layer is unbindable, and
        # workflow_only_scope_checks proves it can never be green.  What is still
        # refused is a work_request_id that is present and malformed.
        ("a scope whose work request identity is present and empty",
         {"scope": {**SCOPE, "work_request_id": "  "},
          "truth": _truth(SCOPE), "evidence": _evidence()}),
        ("a scope version that is not a positive integer",
         {"scope": {**SCOPE, "workflow_version": 0},
          "truth": _truth(SCOPE), "evidence": _evidence()}),
        ("an incident reference that is not an existing ref",
         {"scope": {**SCOPE, "incident_refs": [{"note": "invented"}]}}),
        ("a workflow truth input that was neither read nor declared unreadable",
         {"truth": None}),
        ("an instant that is not ISO-8601", {"now": "yesterday"}),
    ):
        try:
            _row(**kwargs)
        except health._AssuranceHealthContractError:
            continue
        except Exception as exc:  # a refusal must be the declared contract error
            accepted.append(f"{label} raised {type(exc).__name__}")
            continue
        accepted.append(label)
    check("every malformed or unbound input is refused with the declared contract error",
          not accepted, f"accepted={accepted}")

    check("UNREADABLE and absent are different facts and are reported differently",
          _row(evidence=_evidence(controller_assessment=UNREADABLE))
          ["evidence"]["controller_assessment"]["state"] == "unreadable"
          and _row(evidence=_evidence(controller_assessment=None))
          ["evidence"]["controller_assessment"]["state"] == "missing")


def output_discipline_checks(health) -> None:
    """What the canonical output must always carry, and must never carry."""
    row = _row(evidence=_evidence(controller_assessment=_ev(
        "controller_assessment",
        api_token="SUPERSECRET-DO-NOT-PRINT",
        connection_string="SYNTHETIC-CONNECTION-DO-NOT-PRINT")))
    rendered = json.dumps(row)
    check("evidence identity is echoed and adjacent secrets never are",
          "SUPERSECRET-DO-NOT-PRINT" not in rendered and "SYNTHETIC-CONNECTION-DO-NOT-PRINT" not in rendered
          and row["evidence"]["controller_assessment"]["evidence_ref"]
          == "ref:controller_assessment:0001")

    healthy = _row()
    check("the canonical row always exposes state, scope, owner and every layer's "
          "identity and currentness",
          set(healthy["evidence"]) == set(health._EVIDENCE_SLOTS)
          and healthy["scope"]["owner"] == SCOPE["owner"]
          and healthy["scope"]["completion_subject_key"]
          == "workflow:assurance-fabric-child:v1"
          and all(healthy["evidence"][slot]["expires_at"] == CURRENT_UNTIL
                  and healthy["evidence"][slot]["observed_at"] == OBSERVED
                  for slot in health._EVIDENCE_SLOTS))
    check("no state is authored: healthy is a derived label with its derivation printed",
          bool(healthy["state_reason"]) and bool(healthy["reasons"])
          and healthy["workflow_truth"]["source"]
          == "lib/control_plane_workflow_truth.py")

    outside: list[str] = []
    for projected in PROJECTED:
        if projected["state"] not in health._DISPLAY_STATES:
            outside.append(f"state={projected['state']}")
        if projected["capability_stage"] not in health._CAPABILITY_STAGES:
            outside.append(f"stage={projected['capability_stage']}")
        for slot, public in projected["evidence"].items():
            if public["state"] not in health._EVIDENCE_STATES:
                outside.append(f"{slot}={public['state']}")
        if projected["green"] != (projected["state"] == "healthy"):
            outside.append("green disagrees with state")
        if projected["state"] != "healthy" and not projected["recovery"]["required_evidence"] \
                and projected["state"] not in ("disabled", "unknown", "not-yet-operational"):
            outside.append(f"{projected['state']} named no required evidence")
    check(f"every projected row in this suite stays inside the closed vocabularies "
          f"({len(PROJECTED)} rows)", not outside, f"outside={sorted(set(outside))[:5]}")

    source = (REPO / "lib" / "assurance_health.py").read_text(encoding="utf-8")
    modules: set[str] = set()
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("import "):
            modules.update(part.strip().split(" as ")[0].split(".")[0]
                           for part in stripped[len("import "):].split(","))
        elif stripped.startswith("from ") and " import " in stripped:
            modules.add(stripped[len("from "):].split(" import ")[0].strip().split(".")[0])
    forbidden = sorted(modules & {
        "os", "subprocess", "socket", "urllib", "http", "requests", "psycopg", "psycopg2",
        "sqlite3", "pathlib", "shutil", "random", "time", "secrets", "logging"})
    check("the projection is pure: it imports no clock, filesystem, process, network "
          "or database route",
          not forbidden, f"forbidden={forbidden}")
    check("the projection reuses the F09 workflow adapter rather than restating it",
          "from lib.control_plane_workflow_truth import" in source
          and "def workflow_truth" not in source)


WORKFLOW_ONLY = {
    "workflow_key": "assurance-fabric-child",
    "workflow_version": 1,
    "owner": "assurance_fabric_child_owner",
}


def workflow_only_scope_checks(health) -> None:
    """A scope may be bound by workflow identity alone, and then it cannot be green.

    THE DEFECT THIS FIXES. The first cut of this module made ``work_request_id``
    part of every scope's identity, so a truthful-health surface that reads the
    workflow census -- which carries no Work Request identity at all -- could bind
    no scope and therefore could not be wired to this projection.  Requiring an
    identity the authoritative reading does not carry does not make a surface
    honest; it makes it silent.

    The rule instead: the Work Request identity is OPTIONAL on the scope and
    REQUIRED for exactly the layer that joins through it.  A workflow-only scope
    projects every other layer normally and reports ``actual_business_outcome`` as
    ``unbindable`` -- so act capability, and therefore green, is unreachable until
    the binding exists.  That is the settled position (Q043: no layer may be
    inferred from another) expressed as a join rather than as silence.
    """
    row = _row(scope=WORKFLOW_ONLY, evidence=_evidence(WORKFLOW_ONLY))
    check("a scope bound by workflow identity alone is projected, not refused",
          row["scope"]["workflow_key"] == "assurance-fabric-child"
          and row["scope"]["work_request_id"] is None,
          json.dumps(row["scope"]))
    outcome = row["evidence"]["actual_business_outcome"]
    check("a workflow-only scope reports the business outcome layer as unbindable",
          outcome["state"] == "unbindable", outcome["state"])
    check("unbindable names the exact missing identity, not a vague absence",
          any("work_request_id" in reason for reason in outcome["reasons"]),
          json.dumps(outcome["reasons"]))
    check("a perfect outcome receipt cannot fill a layer the scope cannot join",
          outcome["state"] == "unbindable" and outcome.get("status") == "pass",
          json.dumps(outcome))
    check("a workflow-only scope is never green however good its other evidence is",
          row["state"] != health._GREEN_STATE and not row["green"], row["state"])
    check("a workflow-only scope with five passing layers is not-yet-operational",
          row["state"] == "not-yet-operational", row["state_reason"])
    check("act capability is unreachable without the Work Request join",
          row["capability_stage"] == "draft"
          and "actual_business_outcome" in row["impact"]["blocking_act"],
          f"{row['capability_stage']} {row['impact']['blocking_act']}")
    check("the recovery block names the outcome evidence the owner still owes",
          any(item["slot"] == "actual_business_outcome"
              and item["evidence_state"] == "unbindable"
              for item in row["recovery"]["required_evidence"]),
          json.dumps(row["recovery"]["required_evidence"]))

    explicit = _row(scope={**WORKFLOW_ONLY, "work_request_id": None},
                    evidence=_evidence(WORKFLOW_ONLY))
    check("an explicit null Work Request identity binds exactly as its absence does",
          explicit["state"] == row["state"]
          and explicit["evidence"]["actual_business_outcome"]["state"] == "unbindable",
          explicit["state"])

    bound_evidence = _evidence(WORKFLOW_ONLY)
    bound_evidence["artifact_assessment"] = _ev("artifact_assessment", scope=SCOPE)
    joined = _row(scope=WORKFLOW_ONLY, evidence=bound_evidence)
    check("evidence carrying a Work Request identity cannot join a workflow-only scope",
          joined["evidence"]["artifact_assessment"]["state"] == "mismatched",
          joined["evidence"]["artifact_assessment"]["state"])

    both = health._test_only_hypothetical_census(scopes=[
        {"scope": WORKFLOW_ONLY, "workflow_truth": _truth(WORKFLOW_ONLY),
         "evidence": _evidence(WORKFLOW_ONLY)},
        {"scope": SCOPE, "workflow_truth": _truth(SCOPE), "evidence": _evidence(SCOPE)},
    ], now=NOW)["would_be_healthy_if_authoritative"]
    PROJECTED.extend(both["rows"])
    check("the same workflow bound with and without a Work Request is two scopes",
          both["summary"]["scopes"] == 2, json.dumps(both["summary"]))
    check("a workflow-only row and a Work Request row sort deterministically",
          [r["scope"]["work_request_id"] for r in both["rows"]] == [None, "wr-a01-0001"],
          json.dumps([r["scope"]["work_request_id"] for r in both["rows"]]))

    bad_identities: tuple[Any, ...] = ("", "   ", 7, True, [], {})
    for bad in bad_identities:
        try:
            health._test_only_hypothetical_row(
                scope={**WORKFLOW_ONLY, "work_request_id": bad},
                workflow_truth=_truth(WORKFLOW_ONLY), evidence=_evidence(WORKFLOW_ONLY),
                now=NOW)
            refused = False
        except health._AssuranceHealthContractError:
            refused = True
        check(f"a malformed Work Request identity {bad!r} is refused, never coerced to unbound",
              refused)

    # TWO DEGRADED WORKFLOW-ONLY SCOPES, THROUGH THE LABEL LOGIC ITSELF.
    #
    # THE DEFECT THIS PINS.  The summary listed its degraded scopes by
    # ``work_request_id``, which every scope the live census binds leaves absent.
    # One degraded scope never compared anything, so every fixture stayed green
    # while the first census carrying two of them raised TypeError on ``None <
    # None`` inside the projection -- which the adapter does not catch.  The
    # assertion has to carry TWO of them, and it lives here rather than on the
    # health surface because no reading that surface can perform is determinate
    # enough to degrade a scope at all: it has no admitted evidence to fail.
    other_workflow_only = {"workflow_key": "also-degrading", "workflow_version": 1,
                           "owner": "assurance_fabric_child_owner"}
    degraded_pair = health._test_only_hypothetical_census(scopes=[
        {"scope": WORKFLOW_ONLY, "workflow_truth": _truth(WORKFLOW_ONLY),
         "evidence": _evidence(WORKFLOW_ONLY, controller_assessment=_ev(
             "controller_assessment", scope=WORKFLOW_ONLY, status="fail"))},
        {"scope": other_workflow_only, "workflow_truth": _truth(other_workflow_only),
         "evidence": _evidence(other_workflow_only, controller_assessment=_ev(
             "controller_assessment", scope=other_workflow_only, status="fail"))},
    ], now=NOW)["would_be_healthy_if_authoritative"]
    PROJECTED.extend(degraded_pair["rows"])
    check("two degraded scopes with no Work Request identity are both projected",
          degraded_pair["summary"]["scopes"] == 2
          and degraded_pair["summary"]["states"]["degraded"] == 2,
          json.dumps(degraded_pair["summary"]["states"]))
    check("the degraded list orders and names both by the identity they actually have",
          degraded_pair["summary"]["degraded_scopes"]
          == ["also-degrading@v1", "assurance-fabric-child@v1"],
          json.dumps(degraded_pair["summary"]["degraded_scopes"]))
    check("a Work Request-bound scope keeps its id in the same degraded list",
          health._test_only_hypothetical_census(scopes=[
              {"scope": WORKFLOW_ONLY, "workflow_truth": _truth(WORKFLOW_ONLY),
               "evidence": _evidence(WORKFLOW_ONLY, controller_assessment=_ev(
                   "controller_assessment", scope=WORKFLOW_ONLY, status="fail"))},
              {"scope": SCOPE, "workflow_truth": _truth(SCOPE),
               "evidence": _evidence(SCOPE, controller_assessment=_ev(
                   "controller_assessment", status="fail"))},
          ], now=NOW)["would_be_healthy_if_authoritative"]["summary"]["degraded_scopes"]
          == ["assurance-fabric-child@v1", "assurance-fabric-child@v1/wr-a01-0001"])

    check("unbindable is a declared evidence state that is not a passing one",
          "unbindable" in health._EVIDENCE_STATES
          and "unbindable" not in health._DETERMINATE_NONPASS_EVIDENCE_STATES
          and "unbindable" not in health._INDETERMINATE_EVIDENCE_STATES)


def caller_evidence_is_never_authority_checks(health) -> None:
    """THE EXHAUSTIVE UNREACHABILITY MATRIX for the PUBLIC surface.

    A caller-supplied label, class string, flag, receipt object or injected
    holder is never authority.  ``lib/assurance_health`` owns the label logic and
    owns no evidence: NOT ONE of its six layers has an evidence owner anywhere in
    this repository, so there is no admission point through which a caller could
    make any of them passing, whatever it passes in.  This iterates every
    caller-controlled input shape the public entry points accept and asserts the
    privileged outcomes -- ``passing``, ``healthy``/green and ``act`` -- are
    unreachable rather than merely undocumented.
    """
    check("no layer has an evidence owner in this repository",
          health._EVIDENCE_OWNERS == {}, json.dumps(health._EVIDENCE_OWNERS))
    check("every layer names the seam that is owed before it could ever pass",
          all(slot in health._OWED_EVIDENCE_OWNER_SEAMS for slot in health._EVIDENCE_SLOTS)
          and all(health._OWED_EVIDENCE_OWNER_SEAMS[slot].strip()
                  for slot in health._EVIDENCE_SLOTS),
          json.dumps(sorted(health._OWED_EVIDENCE_OWNER_SEAMS)))
    check("the controller layer's owed seam names a READ, not a shape to trust",
          "ops.legacy_schedule_observation_receipt"
          in health._OWED_EVIDENCE_OWNER_SEAMS["controller_assessment"]
          and "tools/health-check.py"
          in health._OWED_EVIDENCE_OWNER_SEAMS["controller_assessment"],
          health._OWED_EVIDENCE_OWNER_SEAMS["controller_assessment"])

    # ---- every caller-controlled shape, for every slot ----------------------
    far_future = "2099-01-01T00:00:00+00:00"

    class _LooksAdmitted:
        """An injected holder wearing the admitted shape.  Shape is not authority."""
        def __init__(self, slot, record):
            self.slot = slot
            self.owner = "receipt-producer:ops.legacy_schedule_observation_receipt"
            self.record = record

    class _EvidenceDict(dict):
        """A dict subclass, in case isinstance(..., dict) were the whole test."""

    def caller_shapes(slot):
        """Every shape a caller can hand this slot, most plausible first."""
        perfect = _ev(slot)
        yield f"{slot}: a perfectly shaped passing record", perfect
        yield f"{slot}: a record whose expiry is decades away", _ev(slot, expires=far_future)
        yield (f"{slot}: a record naming this repository's own admission function",
               _ev(slot, evaluator_identity="lib/assurance_health.admit_evidence"))
        yield (f"{slot}: a record claiming the retired admission path's evaluator",
               _ev(slot, evaluator_identity=
                   "receipt-producer:ops.legacy_schedule_observation_receipt"))
        yield (f"{slot}: a record carrying an extra admitted/verified flag",
               dict(perfect, admitted=True, verified=True, authority="authoritative"))
        yield f"{slot}: the same record as a dict subclass", _EvidenceDict(perfect)
        yield f"{slot}: an injected holder wearing the admitted shape", _LooksAdmitted(slot, perfect)
        yield (f"{slot}: an object whose attributes spell an admitted record",
               type("Forged", (), {"slot": slot, "owner": "authoritative", "record": perfect})())

    forged_green: list[str] = []
    forged_passing: list[str] = []
    for slot in health._EVIDENCE_SLOTS:
        for label, shape in caller_shapes(slot):
            bundle = _evidence()
            bundle[slot] = shape
            try:
                row = health._assurance_health_row(
                    scope=SCOPE, workflow_truth=_truth(SCOPE), evidence=bundle, now=NOW)
            except health._AssuranceHealthContractError:
                continue  # a refusal is an acceptable non-green outcome
            PROJECTED.append(row)
            if row["green"] or row["state"] == health._GREEN_STATE \
                    or row["capability_stage"] == "act":
                forged_green.append(label)
            if row["evidence"][slot]["state"] == "passing":
                forged_passing.append(label)
    check("no caller-controlled evidence shape reaches passing on any layer",
          not forged_passing, json.dumps(forged_passing[:4]))
    check("no caller-controlled evidence shape reaches green or act capability",
          not forged_green, json.dumps(forged_green[:4]))

    # ---- the whole bundle at once, through both projection entry points -----
    whole = health._assurance_health_row(
        scope=SCOPE, workflow_truth=_truth(SCOPE), evidence=_evidence(), now=NOW)
    PROJECTED.append(whole)
    check("six perfectly shaped caller-supplied receipts are unreadable, never passing",
          all(whole["evidence"][slot]["state"] == "unreadable"
              for slot in health._EVIDENCE_SLOTS)
          and not whole["green"] and whole["capability_stage"] == "unavailable",
          json.dumps({slot: whole["evidence"][slot]["state"]
                      for slot in health._EVIDENCE_SLOTS}))
    check("each unreadable reason names the owed seam rather than a generic absence",
          all(any("no evidence owner" in reason
                  for reason in whole["evidence"][slot]["reasons"])
              for slot in health._EVIDENCE_SLOTS),
          json.dumps(whole["evidence"]["controller_assessment"]["reasons"]))
    census = health._assurance_health(scopes=[
        {"scope": SCOPE, "workflow_truth": _truth(SCOPE), "evidence": _evidence()},
        {"scope": WORKFLOW_ONLY, "workflow_truth": _truth(WORKFLOW_ONLY),
         "evidence": _evidence(WORKFLOW_ONLY)}], now=NOW)
    PROJECTED.extend(census["rows"])
    check("a whole census of caller-supplied receipts renders no green row",
          census["summary"]["green"] == 0
          and census["summary"]["states"]["healthy"] == 0,
          json.dumps(census["summary"]["states"]))

    # ---- the retired admission path is gone, not renamed --------------------
    # THE DEFECT THIS PINS.  This module used to export an admission function
    # that minted a PASSING controller record out of an F09-SHAPED ROW its caller
    # supplied, and a review reproduced ``controller_assessment: passing`` from a
    # hand-written census.  Nothing in that chain was a read.  A rename would have
    # left the route open, so the function and its holder were removed.
    retired = [name for name in
               ("admit_scheduler_observation_receipt", "AdmittedEvidence",
                "admit_evidence", "admit")
               if hasattr(health, name)]
    check("no admission function survives anywhere on the module, under any name",
          not retired, json.dumps(retired))
    minting = [name for name in dir(health)
               if not name.startswith("_") and "admit" in name.lower()]
    check("nothing named like an admission point is reachable on the module",
          not minting, json.dumps(minting))


# THE PRIVILEGED STRINGS IN THEIR DECISIVE FORM, shared by both surface guards so
# the two halves of this surface are probed for the same thing.  A bare "passing"
# also occurs inside prose ("a current passing receipt ... is required"), and a
# bare "act" inside ``capabilities_withdrawn``, which is the OPPOSITE of the
# privileged outcome -- so the probe looks for each one exactly where it decides
# something.  Each guard ends with a control proving the probe really fires.
PRIVILEGED_OUTCOME_TOKENS = ('"state": "healthy"', '"state": "passing"',
                             '"capability_stage": "act"', '"green": true')


def _module_bindings(path: Path) -> tuple[set[str], list[str]]:
    """The top-level names a module BINDS and the ones it EXPORTS, read by parser.

    WHY A PARSER AND NOT A REGEX.  The claim these guards make is about a module's
    PUBLIC SURFACE, and that is a syntactic fact: which top-level names it binds,
    and which of them ``__all__`` exports.  A regex over the text can be fooled by
    a name inside a docstring, a comment or a string, in either direction -- and a
    guard that can be fooled is worse than none, because it reports green.

    One reader, used by both surface guards, so the two halves of this surface
    cannot be judged by two different definitions of "public".
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    bound: set[str] = set()
    declared_all: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bound.add(target.id)
                    if target.id == "__all__" and isinstance(node.value, (ast.List, ast.Tuple)):
                        declared_all = [element.value for element in node.value.elts
                                        if isinstance(element, ast.Constant)
                                        and isinstance(element.value, str)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            bound.add(node.target.id)
    return bound, declared_all


def _probe_public_callables(namespace: Any, names: list[str],
                            shapes: dict[str, tuple[Any, ...]]) -> tuple[int, list[str]]:
    """Call every callable of ``names`` on ``namespace`` with every caller shape.

    Returns the number of calls made and the calls whose returned value rendered a
    privileged outcome.  It is a FUNCTION rather than a loop inside one guard so
    that the same probe can be pointed at a control namespace -- one that DOES
    publish a route reaching healthy -- and shown to catch it.  A probe that has
    never caught anything is not evidence that there is nothing to catch.
    """
    import inspect

    reached: list[str] = []
    called = 0
    for name in sorted(set(names)):
        member = getattr(namespace, name, None)
        if not callable(member) or inspect.isclass(member):
            continue
        try:
            signature = inspect.signature(member)
        except (TypeError, ValueError):  # pragma: no cover - builtins have none
            continue
        options = []
        for parameter in signature.parameters.values():
            if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
                continue
            options.append([(parameter.name, value)
                            for value in shapes.get(parameter.name, (None, "healthy"))])
        if not options:
            continue
        for combination in itertools.product(*options):
            called += 1
            try:
                returned = member(**dict(combination))
            except Exception:
                continue  # a refusal is an acceptable non-privileged outcome
            rendered = json.dumps(returned, default=str)
            if any(token in rendered for token in PRIVILEGED_OUTCOME_TOKENS):
                reached.append(f"{name}({', '.join(key for key, _ in combination)})")
    return called, reached


def public_surface_guard_checks(health) -> None:
    """PARSER-BACKED. The public names of the module, read out of its own syntax.

    WHY A PARSER AND NOT A REGEX.  The claim being made is about the module's
    PUBLIC SURFACE, and that is a syntactic fact: which top-level names it binds,
    and which of them ``__all__`` exports.  A regex over the text can be fooled by
    a name inside a docstring, a comment or a string, in either direction -- and a
    guard that can be fooled is worse than none, because it reports green.  So
    this parses ``lib/assurance_health.py`` with ``ast`` and reads the bindings.

    IT READS EXACTLY ONE FILE, AND THAT WAS ITS OWN DEFECT ONCE.  This surface is
    two modules -- the domain and its adapter -- and a guard bound to one half
    reported green while ``lib/assurance_health_sources`` exported a route that
    handed a caller's own census row back as ``workflow_truth``.  The adapter's
    half is guarded by ``sources_public_surface_guard_checks`` below, through the
    same parser and the same probe.

    WHAT IT FORBIDS, exactly: no public name may be a route that turns
    caller-controlled input into a privileged outcome.  Two checks carry that.
    (1) The classifier and every admission-shaped name must be absent from both
    ``__all__`` and the module's non-underscore top-level bindings.  (2) Every
    public callable is CALLED with caller-controlled shapes, and the privileged
    strings must not appear anywhere in what comes back.
    """
    import inspect

    bound, declared_all = _module_bindings(REPO / "lib" / "assurance_health.py")

    check("the module declares an explicit public export list",
          bool(declared_all), json.dumps(declared_all))
    public = {name for name in bound if not name.startswith("_")}
    check("every exported name is actually bound at module level",
          not sorted(set(declared_all) - bound),
          json.dumps(sorted(set(declared_all) - bound)))
    check("the parser and the imported module agree on the public names",
          public == {name for name in dir(health) if not name.startswith("_")},
          json.dumps(sorted(public
                            ^ {name for name in dir(health) if not name.startswith("_")})))

    # THE FORBIDDEN ROUTES, by the shape of what they do rather than by one name:
    # anything that classifies caller shapes into a label, and anything that
    # admits caller shapes as evidence.
    forbidden_public = sorted(
        name for name in public | set(declared_all)
        if any(token in name.lower() for token in
               ("predicate", "unwired", "admit", "fixture", "compile", "classify")))
    check("no classifier, admission, fixture or compile route is public",
          not forbidden_public, json.dumps(forbidden_public))
    for name in ("_label_predicate", "_label_predicate_row",
                 "_test_only_hypothetical_row", "_test_only_hypothetical_census"):
        check(f"{name} is module-private and unexported",
              name.startswith("_") and name in bound and name not in declared_all)

    # ---- what is left public, and every caller-controlled shape ------------
    #
    # THE ELEVENTH CORRECTION IS PINNED HERE.  The domain module used to export
    # twenty-six names, and a review's traversal of those exported values found
    # thirty-three privileged hits in them.  They are module-private now, so this
    # guard asserts the export list is exactly one clean schema identifier -- and
    # then still runs the caller-shape probe over whatever IS public, because an
    # assertion about a list is not an assertion about behaviour.
    check("the domain module exports exactly one name, a schema identifier, and it "
          "carries no word of the privileged union",
          sorted(declared_all) == ["SCHEMA_VERSION"]
          and public - {"annotations"} == {"SCHEMA_VERSION"}
          and not any(word in health.SCHEMA_VERSION.lower()
                      for word in PRIVILEGED_WORD_UNION),
          json.dumps({"declared": sorted(declared_all), "public": sorted(public),
                      "value": health.SCHEMA_VERSION}))

    privileged = PRIVILEGED_OUTCOME_TOKENS
    perfect_evidence = _evidence()
    perfect_bundle = {"scope": SCOPE, "workflow_truth": _truth(SCOPE),
                      "evidence": perfect_evidence}
    shapes: dict[str, tuple[Any, ...]] = {
        "scope": (SCOPE, WORKFLOW_ONLY, {"workflow_key": "k", "workflow_version": 1},
                  {"workflow_key": "k", "workflow_version": 1, "state": "healthy"}),
        "scopes": ([perfect_bundle], [perfect_bundle, dict(perfect_bundle, scope=WORKFLOW_ONLY)],
                   [dict(perfect_bundle, workflow_truth={"state": "healthy",
                                                         "green": True})]),
        "workflow_truth": (_truth(SCOPE), {"state": "healthy", "green": True},
                           {"schema_version": "assurance-health.v1", "state": "healthy"}),
        "evidence": (perfect_evidence,
                     {slot: dict(_ev(slot), state="passing", admitted=True)
                      for slot in health._EVIDENCE_SLOTS},
                     {slot: "passing" for slot in health._EVIDENCE_SLOTS}),
        "now": (NOW,),
        "field": ("scope",),
    }

    called, reached = _probe_public_callables(
        health, sorted(set(declared_all) | public), shapes)
    check(f"no public callable yields a privileged outcome from any caller shape "
          f"({called} calls)", not reached, json.dumps(sorted(set(reached))[:4]))

    # THE CONTROL FOR THE PROBE ITSELF, and it is the answer to "a probe that
    # calls nothing proves nothing".  The same function is pointed at a namespace
    # that publishes the module-private hypothetical row builder under the name
    # this module used to export.  That route DOES reach healthy from these
    # caller shapes, the probe catches it, and so the empty result above is a
    # measurement rather than an absence of measurement.
    control_namespace = types.SimpleNamespace(
        assurance_health_row=health._test_only_hypothetical_row,
        assurance_health=health._test_only_hypothetical_census)
    control_called, control_reached = _probe_public_callables(
        control_namespace, ["assurance_health_row", "assurance_health"], shapes)
    check(f"the probe is live: a namespace that DOES publish a route to healthy is "
          f"caught by it ({control_called} calls, {len(control_reached)} privileged)",
          control_called >= 20 and bool(control_reached),
          json.dumps({"calls": control_called,
                      "reached": sorted(set(control_reached))[:4]}))

    # THE CONTROL. The same shapes, through the module-private classifier, DO
    # reach healthy -- which is what makes the assertion above a measurement of
    # the public surface rather than of a fixture that could never be green.
    control = health._test_only_hypothetical_row(
        scope=SCOPE, workflow_truth=_truth(SCOPE), evidence=perfect_evidence,
        now=NOW)["would_be_healthy_if_authoritative"]
    check("the control proves these shapes WOULD be healthy if they were authority",
          control["state"] == "healthy" and control["green"] is True,
          control["state"])
    rendered_control = json.dumps(control, default=str)
    check("the probe above detects every privileged string when one is really there",
          all(token in rendered_control for token in privileged),
          json.dumps([token for token in privileged if token not in rendered_control]))


MANIFEST_OWNER = "ops.job dispatcher"


def _census(*, enabled=True, surfaces=(), keys=(("assurance-fabric-child", 1),),
            shadow="accepted", canary="accepted", now=NOW):
    """One census from the real V5-F09 projection; this suite invents no census."""
    from lib.control_plane_workflow_truth import workflow_truth
    declarations, definitions, acceptances = [], [], []
    for key, version in keys:
        declarations.append({"key": key, "version": version, "enabled": enabled,
                             "legacy_schedule": {"provider": "none", "status": "disabled"}})
        definitions.append({"key": key, "version": version, "enabled": enabled,
                            "execution_contract": {}, "legacy_disabled_at": None})
        for mode, status in (("shadow", shadow), ("canary", canary)):
            if status:
                acceptances.append({"workflow_key": key, "workflow_version": version,
                                    "mode": mode, "status": status})
    return workflow_truth(
        declarations=declarations, definitions=definitions, acceptances=acceptances,
        surfaces=list(surfaces), completion={}, observation_max_age_seconds=MAX_AGE, now=now)


# Five minutes before the projection instant, so it is inside the registry's own
# 900-second observation window; OBSERVED (one hour back) is outside it and is
# used deliberately for the stale case below.
CURRENT_OBSERVED = "2026-09-09T11:55:00+00:00"


def _surface(surface_id, *, key="assurance-fabric-child", version=1,
             scheduler_state="enabled", observed=CURRENT_OBSERVED):
    surface = {"workflow_key": key, "workflow_version": version, "surface_id": surface_id,
               "locator": f"com.carr.{surface_id}", "scheduler_kind": "launchd",
               "duplicate_group": None, "disable_receipt_ref": None, "observation": None}
    if scheduler_state is not None:
        surface["observation"] = {"scheduler_state": scheduler_state, "observed_at": observed}
    return surface


def _reading(*, surfaces=(), owners=_DEFAULT, **census_kwargs):
    census = _census(surfaces=surfaces, **census_kwargs)
    return {"available": True, "census": census, "surfaces": list(surfaces),
            "owners": ({"assurance-fabric-child@v1": MANIFEST_OWNER}
                       if owners is _DEFAULT else owners)}


def _raises_type_error(call) -> bool:
    """True when this call refuses with TypeError, which is the closed door."""
    try:
        call()
    except TypeError:
        return True
    except Exception:
        return False
    return False


def _raises_attribute_error(call) -> bool:
    """True when this call refuses with AttributeError.

    The handle declares no slots and no ``__dict__``, so the interpreter itself
    refuses a write or a read of state on it before any code of this module's
    runs -- and that refusal is an ``AttributeError``, not a ``TypeError``.  Both
    are closed doors; they are told apart here so a probe cannot pass by having
    hit the wrong one.
    """
    try:
        call()
    except AttributeError:
        return True
    except Exception:
        return False
    return False


# THE ONE ANSWER THE PUBLIC LABEL ROUTE GIVES, WRITTEN OUT RATHER THAN IMPORTED.
# Reading the expected values off the module under test would make every
# assertion below tautological: the route could change its reason to anything and
# the suite would follow it. These are the literal contract -- the reason id the
# review named, the disposition the item is carried under, and a seam that names
# a DURABLE STORE rather than another in-process object.
LABEL_ROUTE_REASON = "handle_integrity_unprovable"
LABEL_ITEM_DISPOSITION = "not_proven"
LABEL_OWED_SEAM = "durable_signed_census_store_seam"
CENSUS_ROUTE_SCHEMA = "control-plane-workflow-truth-census.v1"
# THE RENAME, AND WHY IT IS NOT A COSMETIC ONE.  The reason id used to be spelled
# ``reading_handle_integrity_unprovable``, which carries the privileged union word
# "read" as a substring -- and the ninth cut therefore had to carve that string
# out of its own sweep, which is the exemption the tenth review refused.  The
# reason id is spelled without it now, so the sweep needs no carve-out at all and
# the rule's union is swept whole.


def _is_label_fallback(answer: Any) -> bool:
    """True only for the invariant not-proven answer, with nothing else in it.

    The decision procedure, in order: is it a mapping; does it carry the adapter's
    schema version; is ``available`` exactly ``False``; is the reason exactly the
    reason id the review named; is the disposition exactly ``not_proven``; does it
    name a durable store as the owed seam; and does it carry NO projection, no
    scopes and no rows -- because a fallback that still shipped a census beside its
    refusal would be the refusal in name only.
    """
    if not isinstance(answer, Mapping):
        return False
    return (answer.get("schema_version") == "assurance-health-sources.v1"
            and answer.get("available") is False
            and answer.get("reason") == LABEL_ROUTE_REASON
            and answer.get("item_disposition") == LABEL_ITEM_DISPOSITION
            and answer.get("owed_seam") == LABEL_OWED_SEAM
            and not {"projection", "scopes", "rows", "unprojectable", "input_notes"}
            & set(answer))


def _hypothetical(workflows: Any, *, now: Any = NOW) -> dict[str, Any]:
    """Reach the adapter's classification the ONLY way a test is allowed to.

    ``lib.assurance_health_sources`` exports one callable and the one value it
    accepts is an opaque reading handle the F09 reader minted for a read THAT
    MODULE performed -- never a census, because an adapter that accepts a census
    accepts its caller's assertion about the control plane.  This suite cannot
    read a control plane and cannot mint a handle, so it drives the
    classification through the module's
    UNEXPORTED hook, which returns its answer under
    ``would_be_census_if_authoritative`` -- a name no consumer can read as a
    state any surface holds.  ``sources_public_surface_guard_checks`` parses the
    module and fails the moment that hook becomes public.
    """
    import lib.assurance_health_sources as sources
    return sources._would_be_assurance_health_if_authoritative(
        workflows, now=now)["would_be_census_if_authoritative"]


def source_adapter_checks() -> None:
    """The seam that binds this projection to the ONE F09 reading the reader performed.

    WHAT THESE PIN.  EVERY layer is declared unread and named, never defaulted --
    the controller readback included, because reading the observation receipts is
    not the same act as vouching that one of them is the live state of that
    scheduler.  No reading that passes through this seam can produce a green or
    even a degraded row: there is nothing here that could become a determinate
    fact about a scope.  Every census below is a FIXTURE, so every one of them is
    driven through the unexported hook rather than the public entry.

    THE SECOND HALF OF THIS FUNCTION pins the DELETION the tenth correction made:
    the reader's minting, snapshot and rendering surface is gone, no file in the
    tree reaches for it, and neither route has an argument or a global left to
    steer.
    """
    import inspect

    import lib.assurance_health_sources as sources

    unavailable = _hypothetical(
        {"available": False, "reason": "control-plane rows unreadable (OperationalError)"},
        now=NOW)
    check("an unavailable reading is reported unavailable, never as an empty census",
          unavailable["available"] is False
          and "OperationalError" in unavailable["reason"], json.dumps(unavailable))
    for bad, label in ((None, "no reading at all"), ({}, "an empty reading"),
                       ({"available": True, "census": {"rows": []}}, "a census from nowhere")):
        result = _hypothetical(bad, now=NOW)
        check(f"{label} is refused rather than projected",
              result["available"] is False, json.dumps(result))

    reading = _reading(surfaces=[_surface("assurance-fabric-child.launchd.v1")])
    projected = _hypothetical(reading, now=NOW)
    check("a reading with one exact controller readback projects a bound scope",
          projected["available"] and projected["projection"]["summary"]["scopes"] == 1,
          json.dumps(projected.get("reason", "")))
    row = projected["projection"]["rows"][0]
    PROJECTED.append(row)
    controller = row["evidence"]["controller_assessment"]
    # THE DEFECT THIS PINS. This reading DOES hold a well-formed, in-window
    # observation receipt for this exact workflow, and the layer is still unread:
    # the snapshot was written by this test, and a receipt a caller wrote is not a
    # reading of the store that owns it. The earlier cut rendered it ``passing``.
    check("a well-formed in-window receipt in the supplied snapshot is still UNREAD",
          controller["state"] == "unreadable", controller["state"])
    check("the surface says what the reading held and what a reading would owe",
          any("holds one scheduler observation receipt" in note
              and "ops.legacy_schedule_observation_receipt" in note
              for note in projected["input_notes"]["assurance-fabric-child@v1"]),
          json.dumps(projected["input_notes"]["assurance-fabric-child@v1"]))
    for slot in ("artifact_assessment", "execution_assessment", "candidate_outcome_oracle",
                 "activation_readback"):
        check(f"{slot} is declared unread by this surface rather than defaulted",
              row["evidence"][slot]["state"] == "unreadable", row["evidence"][slot]["state"])
    check("the business outcome layer is unbindable: the census carries no Work Request",
          row["evidence"]["actual_business_outcome"]["state"] == "unbindable",
          row["evidence"]["actual_business_outcome"]["state"])
    check("no reading that reaches this surface can render green",
          projected["projection"]["summary"]["green"] == 0
          and not row["green"], row["state"])
    notes = projected["input_notes"]["assurance-fabric-child@v1"]
    for slot in sources._UNREAD_LAYER_SOURCE:
        if slot == "controller_assessment":
            continue  # carries its own note, asserted above with what the reading held
        check(f"the reader is told which surface would supply {slot}",
              any(note.startswith(f"{slot}:") and "would come from" in note for note in notes),
              json.dumps(notes))

    none_read = _hypothetical(_reading(), now=NOW)
    absent = none_read["projection"]["rows"][0]["evidence"]["controller_assessment"]
    PROJECTED.append(none_read["projection"]["rows"][0])
    check("a reading holding no receipt is unread too, and says it held none",
          absent["state"] == "unreadable"
          and any("holds no scheduler observation receipt" in note
                  for note in none_read["input_notes"]["assurance-fabric-child@v1"]),
          absent["state"])

    two = _hypothetical(_reading(surfaces=[
        _surface("assurance-fabric-child.launchd.v1"),
        _surface("assurance-fabric-child.launchd.v2")]), now=NOW)
    ambiguous = two["projection"]["rows"][0]["evidence"]["controller_assessment"]
    PROJECTED.append(two["projection"]["rows"][0])
    check("two receipts for one workflow are not silently reduced to one controller fact",
          ambiguous["state"] == "unreadable", ambiguous["state"])
    check("the ambiguity names both surface identities rather than picking one",
          all(surface_id in " ".join(two["input_notes"]["assurance-fabric-child@v1"])
              for surface_id in ("assurance-fabric-child.launchd.v1",
                                 "assurance-fabric-child.launchd.v2")),
          json.dumps(two["input_notes"]))

    # NO SUPPLIED RECEIPT MOVES A LABEL, in either direction. A receipt inside the
    # registry's own window and one two hours outside it produce the same row,
    # because neither was read by anything that could vouch for it.
    stale = _hypothetical(
        _reading(surfaces=[_surface("assurance-fabric-child.launchd.v1",
                                    observed=OBSERVED)]), now=NOW)
    stale_row = stale["projection"]["rows"][0]
    PROJECTED.append(stale_row)
    check("an out-of-window supplied readback is unread, exactly as an in-window one is",
          stale_row["evidence"]["controller_assessment"]["state"] == "unreadable",
          stale_row["evidence"]["controller_assessment"]["state"])
    check("the freshness of a supplied receipt changes no label on this surface",
          stale_row["state"] == row["state"] and not stale_row["green"],
          f"{stale_row['state']} vs {row['state']}")
    # Capability missing because a layer could not be READ is unproven, never
    # withdrawn: nothing on this surface withdrew anything, so the row claims no
    # state at all rather than reporting a failure it cannot evidence.
    check("a row with nothing determinate claims no state rather than a failure",
          stale_row["state"] == "unknown"
          and any("no state is claimed" in reason for reason in stale_row["reasons"])
          and not any("withdrew" in reason for reason in stale_row["reasons"]),
          json.dumps(stale_row["reasons"]))

    ownerless = _hypothetical(
        _reading(surfaces=[_surface("assurance-fabric-child.launchd.v1")], owners={}), now=NOW)
    check("a workflow with no declared owner is reported unprojectable, not given one",
          ownerless["projection"]["summary"]["scopes"] == 0
          and len(ownerless["unprojectable"]) == 1
          and "inventory.owner" in ownerless["unprojectable"][0]["reason"],
          json.dumps(ownerless["unprojectable"]))

    disabled = _hypothetical(
        _reading(enabled=False, surfaces=[_surface("assurance-fabric-child.launchd.v1")]),
        now=NOW)
    disabled_row = disabled["projection"]["rows"][0]
    PROJECTED.append(disabled_row)
    check("a disabled workflow is disabled on this surface too, and disabled is not green",
          disabled_row["state"] == "disabled" and not disabled_row["green"],
          disabled_row["state"])

    # EACH SCOPE'S EVIDENCE IS ITS OWN. Two workflows, two receipts, one of them
    # outside the registry's window: each row's notes must describe the receipt
    # that binds IT, so nothing from one scope travels to the other through the seam.
    fenced = _reading(
        keys=(("degrading", 1), ("unaffected", 1)),
        surfaces=[_surface("degrading.launchd.v1", key="degrading", observed=OBSERVED),
                  _surface("unaffected.launchd.v1", key="unaffected")],
        owners={"degrading@v1": MANIFEST_OWNER, "unaffected@v1": MANIFEST_OWNER})
    fenced_rows = {row["scope"]["workflow_key"]: row
                   for row in _hypothetical(
                       fenced, now=NOW)["projection"]["rows"]}
    PROJECTED.extend(fenced_rows.values())
    fenced_notes = _hypothetical(fenced, now=NOW)["input_notes"]
    for key in ("degrading", "unaffected"):
        check(f"{key}'s notes name the receipt that binds {key}, not another scope's",
              any(f"{key}.launchd.v1" in note for note in fenced_notes[f"{key}@v1"])
              and not any(f"{'unaffected' if key == 'degrading' else 'degrading'}.launchd.v1"
                          in note for note in fenced_notes[f"{key}@v1"]),
              json.dumps(fenced_notes[f"{key}@v1"]))
    check("neither scope's controller layer is readable, whatever its receipt said",
          all(row["evidence"]["controller_assessment"]["state"] == "unreadable"
              for row in fenced_rows.values()),
          json.dumps({k: v["evidence"]["controller_assessment"]["state"]
                      for k, v in fenced_rows.items()}))
    check("with nothing determinate read, neither scope is degraded or failed",
          all(row["state"] not in ("degraded", "failed") for row in fenced_rows.values()),
          json.dumps({k: v["state"] for k, v in fenced_rows.items()}))

    # TWO UNBOUND SCOPES. The live census binds every scope by workflow identity
    # alone, so the summary cannot key any per-scope list on a Work Request
    # identity that is absent from all of them. (The degraded LIST itself is
    # exercised with two genuinely degraded scopes in scoped_degradation_checks;
    # nothing this seam can read is determinate enough to degrade a scope.)
    both_stale = _reading(
        keys=(("degrading", 1), ("also-degrading", 1)),
        surfaces=[_surface("degrading.launchd.v1", key="degrading", observed=OBSERVED),
                  _surface("also-degrading.launchd.v1", key="also-degrading",
                           observed=OBSERVED)],
        owners={"degrading@v1": MANIFEST_OWNER, "also-degrading@v1": MANIFEST_OWNER})
    many = _hypothetical(both_stale, now=NOW)
    check("a census of unbound scopes summarises without an identity to sort on",
          many["available"] is True, json.dumps(many.get("reason", "")))
    if many["available"]:
        PROJECTED.extend(many["projection"]["rows"])
        ordered = [f"{r['scope']['workflow_key']}@v{r['scope']['workflow_version']}"
                   for r in many["projection"]["rows"]]
        check("two Work Request-less scopes sort against each other without an identity",
              ordered == ["also-degrading@v1", "degrading@v1"], json.dumps(ordered))

    # ---- THE DELETION ITSELF, PROVEN BY PARSER AND BY IMPORT ---------------
    #
    # WHAT USED TO BE HERE, AND WHY IT IS NOT.  Six hundred lines of this function
    # probed the reader's minting machinery: that a handle carried no state, that
    # a retyped handle still rendered its own capture, that a hostile mapping key
    # could not split a render, that an ``object.__new__`` shell was refused.
    # Every one of those assertions was about an object that no longer exists.
    # Ten rounds of narrowing an in-process handle each closed the door that had
    # just been used and left the class open; the tenth review beat the ninth cut
    # from two new directions at once (a ``__del__`` inside the label route, a
    # rebound snapshot function on the census route), and the correction is
    # deletion.  So what is pinned here is the ABSENCE of that surface, which is
    # the only property a later editor cannot quietly undo by adding a check.
    import ast as _reader_ast

    import lib.control_plane_workflow_truth_reader as reader

    RETIRED = ("read_workflow_truth_snapshot", "read_workflow_truth_reading",
               "render_reading", "WorkflowTruthReading", "WorkflowTruthReadingError",
               "READING_NOT_MINTED", "_MINT", "_MINTED", "_FrozenMapping",
               "_WeakReferenceable", "_frozen", "_thawed", "_content_digest",
               "_entry", "_entry_or_refuse", "_read_json", "ROWS_SQL", "COMPLETION_SQL")
    still_there = [name for name in RETIRED if hasattr(reader, name)]
    check("every minting, snapshot and rendering name is gone from the imported "
          "reader module", not still_there, json.dumps(still_there))
    reader_bound, reader_all = _module_bindings(
        REPO / "lib" / "control_plane_workflow_truth_reader.py")
    check("and gone from the file, not merely unexported: the parser finds none of "
          "them bound at module level",
          not sorted(set(RETIRED) & reader_bound),
          json.dumps(sorted(set(RETIRED) & reader_bound)))
    reader_public = {name for name in dir(reader)
                     if not name.startswith("_")} - {"annotations"}
    check("the reader's whole public surface is the frozen answer, its schema id "
          "and one no-argument callable",
          reader_public == {"SCHEMA_VERSION", "CENSUS_ROUTE_ANSWER",
                            "workflow_truth_census"}
          and reader_public == set(reader_all),
          json.dumps(sorted(reader_public)))
    reader_tree = _reader_ast.parse(
        (REPO / "lib" / "control_plane_workflow_truth_reader.py").read_text(
            encoding="utf-8"))
    query_calls = sorted(
        node.func.attr if isinstance(node.func, _reader_ast.Attribute) else node.func.id
        for node in _reader_ast.walk(reader_tree)
        if isinstance(node, _reader_ast.Call)
        and isinstance(node.func, (_reader_ast.Name, _reader_ast.Attribute))
        and (getattr(node.func, "attr", getattr(node.func, "id", "")) in
             ("run", "check_output", "Popen", "connect", "system", "popen")))
    check("the reader runs no process and opens no connection any more",
          not query_calls, json.dumps(query_calls))

    # NO CONSUMER ANYWHERE IN THE TREE STILL REACHES FOR ONE OF THEM.  A grep, but
    # a parsed one: every .py file under lib/, tools/, ops/ and bin/ is parsed and
    # every imported name, attribute and bare name is compared against the retired
    # set.  This file is excluded BY NAME and for one stated reason -- it is the
    # file that has to mention them in order to assert they are gone.
    consumers: dict[str, list[str]] = {}
    unmistakable = {"read_workflow_truth_snapshot", "read_workflow_truth_reading",
                    "render_reading", "WorkflowTruthReading", "WorkflowTruthReadingError",
                    "READING_NOT_MINTED"}
    for folder in ("lib", "tools", "ops", "bin"):
        for path in sorted((REPO / folder).rglob("*.py")):
            if path.resolve() == Path(__file__).resolve():
                continue
            try:
                tree = _reader_ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover - not this slice's business
                continue
            names: set[str] = set()
            for node in _reader_ast.walk(tree):
                if isinstance(node, _reader_ast.ImportFrom):
                    names.update(alias.name for alias in node.names)
                elif isinstance(node, _reader_ast.Attribute):
                    names.add(node.attr)
                elif isinstance(node, _reader_ast.Name):
                    names.add(node.id)
            hit = sorted(names & unmistakable)
            if hit:
                consumers[str(path.relative_to(REPO))] = hit
    check("no file in the tree imports, calls or names a deleted reader symbol",
          not consumers, json.dumps(consumers))
    # THE CONTROL: the same walk over a file that really does name one finds it,
    # so "no consumers" is a measurement and not a walk that quietly matched
    # nothing.
    control_tree = _reader_ast.parse(
        "from lib.control_plane_workflow_truth_reader import render_reading\n"
        "x = render_reading(handle)\n")
    control_names = {node.name for node in _reader_ast.walk(control_tree)
                     if isinstance(node, _reader_ast.alias)} | {
        node.id for node in _reader_ast.walk(control_tree)
        if isinstance(node, _reader_ast.Name)}
    check("the consumer walk detects a deleted symbol when one is really there",
          bool(control_names & unmistakable), json.dumps(sorted(control_names)))

    # ---- THE LABEL ROUTE HAS NO DOOR LEFT, AND THE BYTECODE SAYS SO ---------
    #
    # THE DEFECT THIS REPLACES.  The ninth cut took a parameter it did not use,
    # wrote ``del reading`` to say so, and then read three module globals to build
    # its answer.  ``del`` on the last reference to a caller's object runs that
    # object's ``__del__`` -- caller code, inside the route, between entry and
    # those reads -- and the review used it to rebind all three.  Both halves of
    # that are asserted away here, and neither assertion is about a string in the
    # source: ``co_varnames``/``co_argcount`` say there is no parameter to receive
    # an object, and ``co_names`` EMPTY says the function performs no global and
    # no attribute lookup at all, so there is nothing rebindable for a ``__del__``
    # to have rebound even if one could run.
    entry = sources.assurance_health_census
    code = entry.__code__
    check("the label route takes no argument of any kind",
          code.co_argcount == 0 and code.co_kwonlyargcount == 0
          and not (code.co_flags & 0x04) and not (code.co_flags & 0x08)
          and code.co_varnames == (),
          json.dumps({"argcount": code.co_argcount, "varnames": list(code.co_varnames),
                      "flags": code.co_flags}))
    check("the label route reads no module global and no attribute: its name table "
          "is empty and its only free variable is the frozen answer",
          code.co_names == () and code.co_freevars == ("answer",),
          json.dumps({"names": list(code.co_names), "free": list(code.co_freevars)}))
    route_tree = _reader_ast.parse(
        "\n".join(line[4:] if line.startswith("    ") else line
                  for line in inspect.getsource(entry).split("\n")))
    check("the label route deletes nothing, calls nothing and branches on nothing",
          not [node for node in _reader_ast.walk(route_tree)
               if isinstance(node, (_reader_ast.Delete, _reader_ast.Call,
                                    _reader_ast.If, _reader_ast.Try))],
          inspect.getsource(entry)[:200])
    census_code = reader.workflow_truth_census.__code__
    check("the census route is built the same way: no argument, no global, one "
          "frozen free variable",
          census_code.co_argcount == 0 and census_code.co_names == ()
          and census_code.co_freevars == ("answer",),
          json.dumps({"names": list(census_code.co_names),
                      "free": list(census_code.co_freevars)}))

    # ---- WHAT THE TWO ROUTES ANSWER ----------------------------------------
    label = entry()
    census = reader.workflow_truth_census()
    check("the label route returns the module's frozen answer object itself, not a "
          "copy a caller could edit",
          label is sources.LABEL_ROUTE_ANSWER
          and isinstance(label, Mapping) and not isinstance(label, dict),
          f"{type(label).__name__}")
    # A ``MappingProxyType`` refuses a write two different ways and both are the
    # closed door: item assignment raises TypeError, and the mutating methods of
    # ``dict`` are not on it at all, so ``update``/``clear``/``pop`` are an
    # AttributeError before any argument is looked at.  Both are asserted rather
    # than one, so a future swap to a plain dict fails here instead of passing.
    def _write(target: Any) -> None:
        target["reason"] = CALLER_REASON

    def _erase(target: Any) -> None:
        del target["reason"]

    refusals = {}
    for name, write in (("setitem", _write), ("delitem", _erase)):
        try:
            write(label)
            refusals[name] = "ADMITTED"
        except Exception as exc:
            refusals[name] = type(exc).__name__
    check("the frozen answer refuses every mutation",
          refusals == {"setitem": "TypeError", "delitem": "TypeError"}
          and not any(hasattr(label, name) for name in
                      ("update", "clear", "pop", "popitem", "setdefault")),
          json.dumps({"refusals": refusals, "answer": dict(label)}))
    # THE CONTROL: the same two writes land on an ordinary dict, so "refused"
    # above is a property of the frozen mapping and not of the probe.
    editable = dict(label)
    _write(editable)
    _erase(editable)
    check("the mutation probe really does write to something that is not frozen",
          "reason" not in editable, json.dumps(editable))
    check("the label route carries the reason id, the not-proven disposition and "
          "the durable-store seam name, exactly",
          dict(label) == {"schema_version": "assurance-health-sources.v1",
                          "available": False, "reason": LABEL_ROUTE_REASON,
                          "item_disposition": LABEL_ITEM_DISPOSITION,
                          "owed_seam": LABEL_OWED_SEAM},
          json.dumps(dict(label)))
    check("the census route carries the same reason, disposition and seam under its "
          "own schema id",
          dict(census) == {"schema_version": CENSUS_ROUTE_SCHEMA, "available": False,
                           "reason": LABEL_ROUTE_REASON,
                           "item_disposition": LABEL_ITEM_DISPOSITION,
                           "owed_seam": LABEL_OWED_SEAM},
          json.dumps(dict(census)))
    check("neither route's own strings carry a word from the privileged union",
          not [word for word in PRIVILEGED_WORD_UNION
               for text in list(dict(label).values()) + list(dict(census).values())
               if isinstance(text, str) and word in text.lower()],
          json.dumps(sorted({word for word in PRIVILEGED_WORD_UNION
                             for text in list(dict(label).values())
                             + list(dict(census).values())
                             if isinstance(text, str) and word in text.lower()})))
    # THE CONSUMER IS WIRED TO THEM, and the wiring is parsed rather than grepped.
    health_source = (REPO / "tools" / "health-check.py").read_text(encoding="utf-8")
    health_tree = _reader_ast.parse(health_source)
    health_imports = sorted({alias.name for node in _reader_ast.walk(health_tree)
                             if isinstance(node, _reader_ast.ImportFrom)
                             and node.module == "lib.control_plane_workflow_truth_reader"
                             for alias in node.names})
    check("the health surface imports exactly the census route from the reader, and "
          "nothing else",
          health_imports == ["workflow_truth_census"], json.dumps(health_imports))
    check("and it calls both routes with no argument at all",
          "workflow_truth_census()" in health_source
          and "sources.assurance_health_census()" in health_source
          and "assurance_health_census(reading)" not in health_source,
          "a consumer still hands something to a route")


# THE CALLER-CONSTRUCTED CENSUS THE NINTH AND TENTH REVIEWS BUILT, reproduced
# verbatim in shape: an OPERATIONAL workflow with a caller-chosen key and a
# caller-chosen owner, plus the caller-chosen reason and disposition the tenth
# review pulled out of the label route.  The probes below drive the two routes the
# tenth review used and assert that neither of them moves.
CALLER_WORKFLOW_KEY = "caller-operational"
CALLER_OWNER = "caller-owner"
CALLER_REASON = "caller_reason"
CALLER_DISPOSITION = "verified"
CALLER_SEAM = "caller_seam"


def _caller_operational_reading() -> dict[str, Any]:
    """A complete reading-shaped census a caller wrote, claiming an operational row."""
    return {
        "available": True,
        "census": {"schema_version": "control-plane-workflow-truth.v1",
                   "rows": [{"workflow_key": CALLER_WORKFLOW_KEY, "workflow_version": 1,
                             "state": "operational", "green": True, "reasons": []}],
                   "summary": {"states": {"operational": 1}, "false_operational": 0,
                               "duplicate_open": 0, "dispositions": {},
                               "distinct_identity_excluded_groups": []}},
        "surfaces": [],
        "owners": {f"{CALLER_WORKFLOW_KEY}@v1": CALLER_OWNER},
    }


def label_route_fallback_checks() -> None:
    """THE TENTH REVIEW'S TWO PROBES, REPRODUCED, AGAINST THE DELETED ROUTES.

    PROBE ONE, THE ``__del__`` REBINDING.  The ninth cut's label route took a
    reading it did not use, ran ``del reading`` on it, and then read three
    module-level strings to build its answer.  ``del`` on the last reference runs
    the object's ``__del__``, which is caller code executing INSIDE the route
    between entry and those reads, and the review used it to get

        {"available": false, "reason": "caller_reason",
         "item_disposition": "verified", "owed_seam": "caller_seam"}

    out of a route documented as invariant.  The probe is reproduced here in full:
    an object whose ``__del__`` rebinds every name the old route read, plus the new
    frozen answer's own export, is built and dropped, and its ``__del__`` is
    ASSERTED to have fired and to have really landed those rebindings.  Then the
    route is called.  The bar is byte-identity with the answer captured before the
    probe existed, and object identity with the literal bound at import.

    PROBE TWO, THE SNAPSHOT REBINDING.  The review rebound the reader's
    module-level ``read_workflow_truth_snapshot``, which the old
    ``read_workflow_truth_reading`` resolved at call time, and got a caller's
    census printed by ``tools/health-check.py`` as the control plane's own answer
    -- "0 declared/registered workflow(s): 7 evidence-backed operational" -- which
    ``run.sh health`` shows.  The probe is reproduced by rebinding that name, the
    old render function's name, and the new frozen census export, all three on the
    live module, and asserting the census route does not move.  The same probe is
    driven through the real ``tools/health-check.py`` process in
    ``census_route_invariance_checks``, because a route is only as honest as what
    its consumer prints.

    WHY THESE CAN NO LONGER BE "CONTROLLED" THE OLD WAY.  The ninth suite proved
    each probe still worked before proving the route ignored it.  Half of that is
    now impossible on purpose: there is no minting function left to rebind, so the
    control asserts the rebinding LANDED ON THE MODULE (the attribute really does
    hold the caller's object afterwards) rather than that it changed a reading.
    That is the honest control for a deletion: the caller can still write whatever
    it likes onto these modules, and no route consults any of it.
    """
    import gc

    import lib.assurance_health_sources as sources
    import lib.control_plane_workflow_truth_reader as reader

    caller = _caller_operational_reading()
    caller_strings = (CALLER_WORKFLOW_KEY, CALLER_OWNER, CALLER_REASON,
                      CALLER_DISPOSITION, CALLER_SEAM, '"state": "operational"')

    # The answers as they stand BEFORE any probe exists, captured as bytes.
    label_before = json.dumps(dict(sources.assurance_health_census()), sort_keys=True)
    census_before = json.dumps(dict(reader.workflow_truth_census()), sort_keys=True)
    label_object = sources.assurance_health_census()
    census_object = reader.workflow_truth_census()

    # ---- PROBE 1: a __del__ that rebinds every name the old route read ------
    fired: list[str] = []

    class _RebindingOnDelete:
        """The review's probe object: its __del__ rewrites the module under the route."""

        def __del__(self) -> None:
            fired.append("del")
            # setattr rather than dotted assignment for one reason: three of these
            # names no longer exist on the module, which is the point of the probe,
            # and mypy is right to say so. The write is identical at runtime --
            # this is exactly what the tenth review's __del__ did.
            setattr(sources, "LABEL_ROUTE_UNAVAILABLE_REASON", CALLER_REASON)
            setattr(sources, "LABEL_ITEM_DISPOSITION", CALLER_DISPOSITION)
            setattr(sources, "OWED_LABEL_AUTHORITY_SEAM", CALLER_SEAM)
            setattr(sources, "LABEL_ROUTE_ANSWER", {
                "schema_version": "assurance-health-sources.v1", "available": False,
                "reason": CALLER_REASON, "item_disposition": CALLER_DISPOSITION,
                "owed_seam": CALLER_SEAM})

    probe = _RebindingOnDelete()
    del probe
    gc.collect()
    check("probe 1 control: the __del__ fired and really did land the caller's "
          "strings on the module the route lives in",
          fired == ["del"]
          and getattr(sources, "LABEL_ROUTE_UNAVAILABLE_REASON") == CALLER_REASON
          and getattr(sources, "LABEL_ITEM_DISPOSITION") == CALLER_DISPOSITION
          and sources.LABEL_ROUTE_ANSWER["reason"] == CALLER_REASON,
          json.dumps({"fired": fired,
                      "module_answer": dict(sources.LABEL_ROUTE_ANSWER)}))
    after_del = sources.assurance_health_census()
    check("probe 1: the label route answers byte-identically to before the probe "
          "existed, and returns the very object bound at import",
          json.dumps(dict(after_del), sort_keys=True) == label_before
          and after_del is label_object
          and not any(token in json.dumps(dict(after_del)) for token in caller_strings),
          json.dumps(dict(after_del)))
    # The module is put back the way it was found, so nothing downstream -- the
    # union sweep above all -- reads a forgery this probe left lying about.
    sources.LABEL_ROUTE_ANSWER = after_del
    for name in ("LABEL_ROUTE_UNAVAILABLE_REASON", "LABEL_ITEM_DISPOSITION",
                 "OWED_LABEL_AUTHORITY_SEAM"):
        delattr(sources, name)
    check("and those three names were not module state the route depends on: the "
          "route answers identically once they are removed again",
          json.dumps(dict(sources.assurance_health_census()), sort_keys=True)
          == label_before
          and not any(hasattr(sources, name) for name in
                      ("LABEL_ROUTE_UNAVAILABLE_REASON", "LABEL_ITEM_DISPOSITION",
                       "OWED_LABEL_AUTHORITY_SEAM")),
          json.dumps(dict(sources.assurance_health_census())))

    # ---- PROBE 2: rebinding the reader's snapshot and render names ----------
    # Same reason for setattr here: these two names are DELETED, and writing them
    # onto the module anyway is the probe.
    setattr(reader, "read_workflow_truth_snapshot", lambda: copy.deepcopy(caller))
    setattr(reader, "render_reading", lambda handle: copy.deepcopy(caller))
    setattr(reader, "CENSUS_ROUTE_ANSWER",
            {"schema_version": CENSUS_ROUTE_SCHEMA, "available": True,
             "reason": CALLER_REASON, "item_disposition": CALLER_DISPOSITION,
             "owed_seam": CALLER_SEAM})
    try:
        check("probe 2 control: the rebinding really landed -- the module now holds "
              "the caller's functions and the caller's answer",
              getattr(reader, "read_workflow_truth_snapshot")()["owners"]
              == {f"{CALLER_WORKFLOW_KEY}@v1": CALLER_OWNER}
              and reader.CENSUS_ROUTE_ANSWER["available"] is True,
              json.dumps(reader.CENSUS_ROUTE_ANSWER))
        after_rebind = reader.workflow_truth_census()
        check("probe 2: the census route answers byte-identically to before, and "
              "returns the very object bound at import",
              json.dumps(dict(after_rebind), sort_keys=True) == census_before
              and after_rebind is census_object
              and not any(token in json.dumps(dict(after_rebind))
                          for token in caller_strings),
              json.dumps(dict(after_rebind)))
    finally:
        setattr(reader, "CENSUS_ROUTE_ANSWER", census_object)
        delattr(reader, "read_workflow_truth_snapshot")
        delattr(reader, "render_reading")

    # ---- THE SIGNATURE IS THE DOOR, AND IT IS SHUT -------------------------
    # There is no input to be invariant OVER any more: every calling form that
    # could carry a census is a TypeError before a line of either route runs.
    shapes: list[Any] = [_caller_operational_reading(), None, "healthy", 1, True,
                         object(), {"state": "healthy", "green": True}, [], _census()]
    keywords = ("reading", "workflows", "census", "snapshot", "rows", "scopes",
                "value", "now")
    admitted = []
    attempts = 0
    for route_name, route in (("assurance_health_census", sources.assurance_health_census),
                              ("workflow_truth_census", reader.workflow_truth_census)):
        for shape in shapes:
            for args, kwargs in ([((shape,), {})]
                                 + [((), {keyword: shape}) for keyword in keywords]):
                attempts += 1
                if not _raises_type_error(lambda: route(*args, **kwargs)):
                    admitted.append(f"{route_name}({args!r}, {kwargs!r})")
    expected = 2 * len(shapes) * (1 + len(keywords))
    check(f"neither route admits any caller input in any calling form "
          f"({attempts} attempts: 2 routes x {len(shapes)} shapes x "
          f"{1 + len(keywords)} forms, every one of them a TypeError)",
          not admitted and attempts == expected, json.dumps(admitted[:4]))

    # ---- AND THE ANSWER IS ONE ANSWER --------------------------------------
    repeated = [sources.assurance_health_census() for _ in range(5)]
    check("the label route returns one identical answer every time it is called",
          len({json.dumps(dict(answer), sort_keys=True) for answer in repeated}) == 1
          and all(answer is label_object for answer in repeated)
          and all(_is_label_fallback(answer) for answer in repeated),
          json.dumps(dict(repeated[0])))
    check("the invariant names the reason id, the not-proven disposition and the "
          "durable-store seam the ruling asked for",
          repeated[0]["reason"] == "handle_integrity_unprovable"
          and repeated[0]["item_disposition"] == "not_proven"
          and repeated[0]["owed_seam"] == "durable_signed_census_store_seam",
          json.dumps(dict(repeated[0])))


# ---------------------------------------------------------------------------
# THE CLOSED UNION, EXACTLY AS THE STANDING RULE STATES IT.  Twenty-eight literal
# words plus two families, swept as EXACT MATCH and as SUBSTRING, one test each,
# across every export of both the reader and the adapter -- render_reading
# included -- with no word exempted.
#
# WHY A UNION AND NOT THE FOUR DECISIVE TOKENS IT REPLACES.  The previous sweep
# probed four strings in their decisive form ('"state": "healthy"' and friends),
# which measured the four labels anyone had thought of.  The review's objection
# was that the rule's union is thirty items wide and a surface can hand back
# "operational", "active", "verified" or "complete" without any of the four
# appearing.  So the union is written out here, in the rule's own order.
PRIVILEGED_WORD_UNION = (
    "allow", "commit", "prompt", "suppress", "release", "read", "covered",
    "drafted", "proposed", "queued", "healthy", "passing", "ok", "pass",
    "satisfied", "complete", "admitted", "resumed", "attended", "verified",
    "present", "equivalent", "operational", "active", "green", "joins_exactly",
    "coverage_complete", "favorable",
)

# The two families, case-insensitive, which the rule states as patterns rather
# than words.  ``_would_be_assurance_health_if_authoritative`` is deliberately
# NOT swept: it is not an export, and the rule itself prescribes exactly that
# name for a non-exported test hook.
PRIVILEGED_WORD_PATTERNS = (
    ("^would_ (any case)", r"^would_"),
    ("_if_authoritative (any case)", r"_if_authoritative"),
)

# NO CARVE-OUT.  THERE IS NO ALLOWLIST IN THIS FILE ANY MORE.
#
# WHAT WAS HERE, AND WHY IT IS GONE.  The ninth cut carried an
# ``ALLOWLISTED_LITERAL_PATTERNS`` tuple: three strings the sweep was told to
# ignore when it found a union word inside them, because the route's own reason id
# was spelled ``reading_handle_integrity_unprovable`` and its owed seam was a
# paragraph of prose about READINGS.  The tenth review's finding was exact: remove
# the allowlist and the committed suite fails its own "read" test, so the green
# depended on the exemption.  An exemption a suite depends on is not an exemption,
# it is a hole.
#
# WHAT REPLACED IT.  The strings themselves were renamed until no union word
# appears in any of them -- ``handle_integrity_unprovable``, ``not_proven``,
# ``durable_signed_census_store_seam`` -- so the sweep runs over the whole closed
# union with nothing suppressed, nothing skipped and no filter on what it looks
# at.  The prose that used to be the owed seam's value is still written down; it
# lives in ``_OWED_LABEL_AUTHORITY_SEAM``, which is module-private and is not an
# export of anything.


def _outcome_scalars(value: Any, key: str | None = None,
                     out: list[tuple[str | None, Any]] | None = None
                     ) -> list[tuple[str | None, Any]]:
    """Every scalar a consumer could read out of a returned value, with its key.

    Mappings recurse by key, sequences keep their parent's key, booleans and
    strings are collected, numbers and ``None`` carry no word, and anything else
    -- an opaque handle, say -- contributes its ``repr``, because a repr is what a
    consumer or a log actually sees of it.
    """
    out = [] if out is None else out
    if isinstance(value, Mapping):
        if not value:
            out.append((key, None))  # an empty mapping still HAS the key above it
        for mapping_key, item in value.items():
            # THE PATH, not the innermost key alone.  ``{"verified": {"deep": "x"}}``
            # used to arrive as key ``deep`` and the ``verified`` above it was
            # simply gone, so a privileged key with a mapping under it was
            # invisible however deep the sweep went.
            child = str(mapping_key) if key is None else f"{key}.{mapping_key}"
            _outcome_scalars(item, child, out)
    elif isinstance(value, (list, tuple, set)):
        if not value:
            out.append((key, None))
        for item in value:
            _outcome_scalars(item, key, out)
    elif isinstance(value, bool) or isinstance(value, str):
        out.append((key, value))
    elif value is None or isinstance(value, (int, float)):
        # THE ELEVENTH CORRECTION.  These used to be dropped, which silently
        # dropped their KEY as well -- so ``{"verified": None}`` and
        # ``{"verified": 1}`` were invisible to a sweep whose whole subject is
        # what a consumer can read.  The value carries no word; the key does, and
        # the key is kept.  ``_word_hits`` reads a non-string scalar for its key
        # alone.
        out.append((key, value))
    else:
        out.append((key, repr(value)))
    return out


def _word_hits(scalars: list[tuple[str | None, Any]], word: str) -> list[str]:
    """Every privileged appearance of ``word``, exact form and substring form.

    The decision procedure, per scalar, in order, WITH NO EXEMPTION AT ANY STEP:
      1. a KEY that equals or contains the word is a privileged outcome, WHATEVER
         its value is and whatever type that value has;
      2. a string VALUE that equals the word (case-insensitively) is the exact form;
      3. a string VALUE that contains the word is the substring form;
      4. a non-string value contributes no word of its own -- its key was already
         read at step 1.

    WHY STEP 1 NO LONGER ASKS WHAT THE VALUE IS.  It used to read the key only
    when the value was boolean ``True``, and the tenth cut's "nested key coverage"
    was therefore vacuous: ``{"verified": "neutral"}`` and ``{"verified": false}``
    both produced zero hits, so the only mutation the suite could actually fail on
    was a string LEAF.  A consumer does not read a key conditionally -- a
    ``verified`` key is a claim about verification whether it is set to ``false``,
    to ``"neutral"``, to ``None`` or to a handle whose repr says nothing.  The key
    is the claim; the value is the value.
    """
    hits = []
    for key, scalar in scalars:
        if key and word in key.lower():
            hits.append(f"key {key}={scalar!r:.60}")
        if not isinstance(scalar, str) or isinstance(scalar, bool):
            continue
        low = scalar.lower()
        if low == word:
            hits.append(f"exact {key}={scalar!r}")
        elif word in low:
            hits.append(f"substring {key}={scalar[:60]!r}")
    return hits


def _pattern_hits(scalars: list[tuple[str | None, Any]], pattern: str) -> list[str]:
    import re

    expression = re.compile(pattern, re.IGNORECASE)
    hits = [f"{key}={scalar[:60]!r}" for key, scalar in scalars
            if isinstance(scalar, str) and not isinstance(scalar, bool)
            and expression.search(scalar)]
    # Keys, on the same footing as ``_word_hits`` reads them: a key named
    # ``would_be_green_if_authoritative`` is the same claim as a value spelling it.
    # Per PATH SEGMENT, because the two patterns are anchored differently: keys
    # arrive as ``outer.would_be_green_if_authoritative`` and ``^would_`` must
    # still find the segment it names.
    hits += [f"key {key}" for key, _ in scalars
             if isinstance(key, str)
             and any(expression.search(segment) for segment in key.split("."))]
    return hits


# THE HERMETIC RUN OF THE REAL CONSUMER, used by the sweep and by
# ``census_route_invariance_checks``.  It applies the tenth review's rebinding
# probe to the reader module FIRST, then drives tools/health-check.py end to end
# in that process with every subprocess refused, so the two sections this slice
# owns are printed under attack and with no database anywhere near them.
HEALTH_SURFACE_PROBE = r"""
import runpy, subprocess, sys

REPO = sys.argv[1]
sys.path.insert(0, REPO)
# tools/health-check.py imports its siblings by bare name, exactly as it does
# when run as a script from that directory.
sys.path.insert(0, REPO + "/tools")
import lib.control_plane_workflow_truth_reader as reader

# THE TENTH REVIEW'S PROBE, REPRODUCED AGAINST THE CONSUMER.  Rebinding
# read_workflow_truth_snapshot made this surface print a caller's census as the
# control plane's own answer.  All three names are rebound here -- the snapshot
# read, the render, and the frozen census export -- before the surface runs.
reader.read_workflow_truth_snapshot = lambda: {
    "available": True, "reason": "caller_reason",
    "census": {"schema_version": "control-plane-workflow-truth.v1",
               "rows": [{"workflow_key": "caller-operational", "workflow_version": 1,
                         "state": "operational", "green": True, "reasons": []}],
               "summary": {"states": {"operational": 7}, "false_operational": 0,
                           "duplicate_open": 0, "dispositions": {},
                           "distinct_identity_excluded_groups": []}},
    "surfaces": [], "owners": {"caller-operational@v1": "caller-owner"}}
reader.render_reading = lambda handle: reader.read_workflow_truth_snapshot()
reader.CENSUS_ROUTE_ANSWER = {"available": True, "reason": "caller_reason",
                              "item_disposition": "verified",
                              "owed_seam": "caller_seam"}


class _Refused:
    returncode, stdout, stderr = 1, "", "no database tap in this hermetic run"


# Hermetic: every canonical read this surface performs goes through subprocess,
# and none of them may touch a database inside an acceptance run.
subprocess.run = lambda *a, **k: _Refused()

sys.argv = ["health-check.py", "--canonical", "--section", "jobs"]
try:
    runpy.run_path(REPO + "/tools/health-check.py", run_name="__main__")
except SystemExit:
    pass
"""


def _health_surface_sections() -> tuple[str, list[str]]:
    """Run the real consumer under the rebinding probe; return its two sections.

    Returns the whole stdout and the lines of the F09 census section and the A01
    assurance section -- the header line plus every indented line under it, which
    is the entirety of what this slice prints on that surface.
    """
    import os
    import subprocess
    import tempfile

    handle, path = tempfile.mkstemp(suffix=".py", prefix="assurance-health-surface-probe-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(HEALTH_SURFACE_PROBE)
        proc = subprocess.run([sys.executable, path, str(REPO)],
                              cwd=str(REPO), text=True, capture_output=True, timeout=180)
    finally:
        os.unlink(path)
    lines = (proc.stdout + proc.stderr).split("\n")
    section_lines: list[str] = []
    collecting = False
    for line in lines:
        if line.startswith("Workflow truth —") or line.startswith("Assurance health —"):
            collecting = True
            section_lines.append(line)
        elif collecting and line.startswith(" "):
            section_lines.append(line)
        else:
            collecting = False
    return proc.stdout + proc.stderr, section_lines


# THE ROUTE, BY NAME.  Every function in tools/health-check.py that this slice
# owns: the two sections that print it, and -- added by the twelfth correction --
# the store-derived contradiction alarm, its predicate, the frozen-answer builder
# and the unexported test hook.  Every string literal in each of them is swept,
# docstrings excepted (see below), so a privileged word cannot enter the alarm's
# vocabulary without failing this suite.
ROUTE_FUNCTIONS = (
    "_canonical_workflow_truth",
    "_canonical_assurance_health",
    "_contradiction_groups",
    "_alarm_answer",
    "_workflow_truth_contradiction_alarm",
    "_hypothetical_contradiction_alarm",
    "_canonical_contradiction_alarm",
)

# The seeded row sets the alarm's test hook is driven with.  These are ROWS, the
# shape ops.workflow_acceptance and ops.legacy_schedule_observation_receipt hold,
# not a census and not a projection: the alarm's whole design is that its input
# is the store and nothing else, so the only way to test its predicate is to hand
# the unexported hook rows that a store could have held.
ALARM_SEEDS: dict[str, Any] = {
    "an acceptance subject that is both accepted and rejected": {
        "acceptance_rows": [
            {"workflow_key": "seeded-subject", "workflow_version": 1,
             "mode": "shadow", "status": "accepted"},
            {"workflow_key": "seeded-subject", "workflow_version": 1,
             "mode": "shadow", "status": "rejected"}],
        "schedule_rows": []},
    "two surfaces of one workflow disagreeing about the schedule": {
        "acceptance_rows": [],
        "schedule_rows": [
            {"workflow_key": "seeded-subject", "workflow_version": 1,
             "surface_id": "seeded-subject.launchd.v1", "scheduler_state": "enabled"},
            {"workflow_key": "seeded-subject", "workflow_version": 1,
             "surface_id": "seeded-subject.claude-code.v1",
             "scheduler_state": "disabled"}]},
    # THE MUTATION CONTROL, and it is the same set with ONE row taken out.  If
    # the verdict came from the shape of the seed rather than from the
    # contradiction inside it, this would still be red.
    "the same acceptance subject with the contradicting row taken out": {
        "acceptance_rows": [
            {"workflow_key": "seeded-subject", "workflow_version": 1,
             "mode": "shadow", "status": "accepted"}],
        "schedule_rows": []},
    "one workflow whose two surfaces agree": {
        "acceptance_rows": [],
        "schedule_rows": [
            {"workflow_key": "seeded-subject", "workflow_version": 1,
             "surface_id": "seeded-subject.launchd.v1", "scheduler_state": "enabled"},
            {"workflow_key": "seeded-subject", "workflow_version": 1,
             "surface_id": "seeded-subject.claude-code.v1",
             "scheduler_state": "enabled"}]},
    "a store holding no rows at all": {"acceptance_rows": [], "schedule_rows": []},
}

RED_SEEDS = ("an acceptance subject that is both accepted and rejected",
             "two surfaces of one workflow disagreeing about the schedule")

# The alarm's route is defined to take NO input, so the only way to put chosen
# rows in front of its predicate is to reach the unexported hook inside a child
# interpreter.  tools/health-check.py is a SCRIPT -- importing it runs the whole
# health surface -- so the probe executes the real file with the tap refused, and
# keeps the namespace the run left behind.  Nothing is retyped here: the hook,
# the predicate and the literals are the file's own objects.
_ALARM_HOOK_PROBE = r"""
import json, subprocess, sys, tempfile

REPO, seeds_path = sys.argv[1], sys.argv[2]
sys.path.insert(0, REPO)
sys.path.insert(0, REPO + "/tools")


class _Refused:
    returncode, stdout, stderr = 1, "", "no database tap in this hermetic run"


subprocess.run = lambda *a, **k: _Refused()

with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
    json.dump({"observed_at": "2026-09-09T12:00:00+00:00", "exports": None,
               "errors": [], "job_definitions": [], "jobs": []}, fh)
    fixture = fh.name
sys.argv = ["health-check.py", "--section", "jobs", "--fixture", fixture]

path = REPO + "/tools/health-check.py"
namespace = {"__name__": "__main__", "__file__": path}
with open(path, encoding="utf-8") as handle:
    source = handle.read()
try:
    exec(compile(source, path, "exec"), namespace)
except SystemExit:
    pass

with open(seeds_path, encoding="utf-8") as handle:
    seeds = json.load(handle)
hook = namespace["_hypothetical_contradiction_alarm"]
answers = {label: hook(rows) for label, rows in seeds.items()}
# The REAL route as well, called the only way it can be called -- with nothing --
# so its own answer is swept beside the hypotheticals.
answers["the route itself, with the tap refused"] = dict(
    namespace["_workflow_truth_contradiction_alarm"]())
answers["the route's parameter count"] = len(
    __import__("inspect").signature(
        namespace["_workflow_truth_contradiction_alarm"]).parameters)
print("ALARM_HOOK_JSON=" + json.dumps(answers))
"""

_ALARM_ANSWERS: dict[str, Any] | None = None


def _alarm_hook_answers() -> dict[str, Any]:
    """Drive the alarm's unexported hook over every seeded row set, once."""
    global _ALARM_ANSWERS
    if _ALARM_ANSWERS is not None:
        return _ALARM_ANSWERS
    import os
    import subprocess
    import tempfile

    directory = tempfile.mkdtemp(prefix="assurance-health-alarm-probe-")
    probe = os.path.join(directory, "probe.py")
    seeds = os.path.join(directory, "seeds.json")
    try:
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write(_ALARM_HOOK_PROBE)
        with open(seeds, "w", encoding="utf-8") as fh:
            json.dump(ALARM_SEEDS, fh)
        proc = subprocess.run([sys.executable, probe, str(REPO), seeds],
                              cwd=str(REPO), text=True, capture_output=True,
                              timeout=180)
    finally:
        for path in (probe, seeds):
            if os.path.exists(path):
                os.unlink(path)
        os.rmdir(directory)
    answers: dict[str, Any] = {}
    for line in (proc.stdout + proc.stderr).splitlines():
        if line.startswith("ALARM_HOOK_JSON="):
            answers = json.loads(line.split("=", 1)[1])
    _ALARM_ANSWERS = answers
    return answers


def contradiction_alarm_checks() -> None:
    """THE ALARM THE TENTH ROUND REMOVED WITHOUT SAYING SO, AND ITS CONTROLS.

    WHAT IT IS.  ``run.sh health`` goes RED when the control plane's own durable
    rows contradict themselves -- a (workflow, version, mode) both accepted and
    rejected in ops.workflow_acceptance, or two registered surfaces of one
    workflow whose latest receipts disagree about the native schedule.  The tenth
    correction deleted the census route that used to carry that signal and the
    signal went with it; the eleventh round filed defect 26c1e6d8 rather than
    blessing the loss, and this is the restoration.

    WHY IT IS NOT THE ROUTE THAT WAS DELETED.  The deleted one rendered a census
    a caller could compose.  This one takes no argument at all, so there is no
    shape to forge: the verdict comes out of one read-only statement against the
    store and is one of two module-level literals.  What is tested here is the
    PREDICATE, reached through the unexported hook with rows this suite composed,
    plus the route's own answer with no tap in front of it.

    THE CONTROLS, because a red-on-red test proves nothing on its own: each
    contradictory seed has a sibling that differs by exactly one row, and the
    sibling must come back not-red.  A predicate that answered red for any set of
    two rows, or that had gone dead and answered not-red for everything, fails
    here rather than passing quietly.
    """
    answers = _alarm_hook_answers()
    check("the alarm's hook answered every seeded row set",
          set(answers) == set(ALARM_SEEDS) | {"the route itself, with the tap refused",
                                              "the route's parameter count"},
          json.dumps(sorted(answers)))
    for label in RED_SEEDS:
        answer = answers.get(label) or {}
        check(f"the alarm is RED on a seeded contradiction: {label}",
              answer.get("hypothetical-alarm") == "red"
              and answer.get("hypothetical-group-count") == 1
              and bool(answer.get("hypothetical-groups"))
              and all("seeded-subject" in name
                      for name in answer["hypothetical-groups"]),
              json.dumps(answer))
    for label in sorted(set(ALARM_SEEDS) - set(RED_SEEDS)):
        answer = answers.get(label) or {}
        check(f"the alarm stays NOT RED on a set that does not contradict itself: "
              f"{label}",
              answer.get("hypothetical-alarm") == "not_red"
              and answer.get("hypothetical-group-count") == 0
              and answer.get("hypothetical-groups") == [],
              json.dumps(answer))
    check("the route takes no argument, so there is no caller input to forge",
          answers.get("the route's parameter count") == 0,
          json.dumps(answers.get("the route's parameter count")))
    refused = answers.get("the route itself, with the tap refused") or {}
    check("with the store out of reach the route says unavailable, never not-red, "
          "and counts nothing",
          refused.get("alarm") == "unavailable"
          and refused.get("reason") == "store_unreachable"
          and refused.get("groups") == 0 and refused.get("group_names") == [],
          json.dumps(refused))

    # ---- no public name anywhere on the alarm route -------------------------
    import ast

    tree = ast.parse((REPO / "tools" / "health-check.py").read_text(encoding="utf-8"))
    alarm_names = sorted(
        node.name for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and ("contradiction" in node.name or "alarm" in node.name))
    check("every function on the alarm route is module-private; there is no "
          "exported classifier",
          bool(alarm_names) and all(name.startswith("_") for name in alarm_names)
          and len(alarm_names) >= 4, json.dumps(alarm_names))


def _route_source_strings() -> list[tuple[str, str]]:
    """Every string a section function can EMIT, which is every string constant in
    its body except its own docstring.

    THE ONE EXCLUSION AND THE PROCEDURE BEHIND IT.  A docstring is prose ABOUT the
    route -- it recounts the ten review rounds, so it necessarily contains the
    words those rounds were about -- and this script never prints it: it is not an
    exported value, it does not reach a consumer, and no grep of the health
    surface can lift it out of an output line.  What CAN reach a consumer is a
    string the function passes to ``print``, and every one of those is swept, with
    nothing exempted inside that set.  The test that separates them is syntactic
    and is applied by the parser, not by judgement: the docstring is
    ``ast.get_docstring``'s node, and everything else is in.
    """
    import ast

    tree = ast.parse((REPO / "tools" / "health-check.py").read_text(encoding="utf-8"))
    out: list[tuple[str, str]] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in ROUTE_FUNCTIONS:
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body = body[1:]
            for statement in body:
                for inner in ast.walk(statement):
                    if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                        out.append((node.name, inner.value))
    return out


def closed_union_sweep_checks() -> None:
    """ONE TEST PER PRIVILEGED WORD, WITH NO EXEMPTION OF ANY KIND.

    WHAT IT SWEEPS, and the procedure is the definition -- there is no list of
    things it skips:

      1. EVERY PUBLIC NAME of ``lib.assurance_health_sources`` and of
         ``lib.control_plane_workflow_truth_reader``.  Data values, not only
         callables: the ninth cut filtered the census to callables and so never
         looked at seven exported data values, several carrying "read" and one
         carrying "commit".  Each value is recursed into -- nested mappings by
         key, sequences, string leaves -- and the exported NAME itself is swept as
         a key, so a constant called ``READ_SOURCES`` is a hit even if its value
         is clean.
      2. EVERY CALLABLE among them, called with no arguments where the signature
         allows it, and with every caller-controlled shape positionally and under
         every plausible keyword where it does not; whatever comes back is swept.
         Classes are constructors and are called the same way.
      3. THE A01/F09 ROUTE THROUGH ``tools/health-check.py``.  That file is a
         SCRIPT, not a library -- importing it runs the whole health surface --
         and its module-level constants belong to unrelated legacy sections (a
         Drive path that contains "ok" inside a surname, a watch list describing a
         "human-review cadence").  So the scope swept here is the ROUTE, defined
         by a procedure rather than by exemptions: every string literal inside the
         two functions that print this slice's sections, AND every line those
         sections actually print in a hermetic canonical run with the tenth
         review's rebinding probe applied first.  Nothing inside that scope is
         exempt.

    WHAT IT ASSERTS.  For each of the twenty-eight words and each of the two
    patterns: the word never appears -- as an exact value, as a substring of a
    value, or as the name of a key set to ``True``.  Each check carries its own
    CONTROL, a synthetic structure holding the word in all three of those
    positions, so a word whose probe had gone dead fails its own test rather than
    passing quietly.

    WHAT IT DOES NOT SCAN, said plainly.  An exception is a refusal, not a
    returned outcome, so a raise contributes nothing -- a consumer cannot read a
    label out of a traceback.  And the private classification hook
    ``_would_be_assurance_health_if_authoritative`` is not an export and is not
    swept; the standing rule prescribes exactly that name for exactly that hook.
    """
    import inspect

    import lib.assurance_health as domain
    import lib.assurance_health_sources as sources
    import lib.control_plane_workflow_truth_reader as reader

    def _parameter_count(member: Any) -> int:
        try:
            return len(inspect.signature(member).parameters)
        except (TypeError, ValueError):  # pragma: no cover - builtins have none
            return 0

    shapes: dict[str, Any] = {
        "a caller's operational census": _caller_operational_reading(),
        "a caller's healthy scope row": {"state": "healthy", "green": True,
                                         "capability_stage": "act"},
        "a caller's evidence bundle": {"scope": SCOPE, "workflow_truth": _truth(SCOPE),
                                       "evidence": _evidence()},
        "a bare census": _census(),
        "None": None,
        "a bare privileged string": "healthy",
        "True": True,
        "an int": 1,
        "a list of rows": [{"state": "operational", "green": True}],
        "a caller's reason and disposition": {"reason": CALLER_REASON,
                                              "item_disposition": CALLER_DISPOSITION},
    }
    keywords = ("reading", "workflows", "census", "snapshot", "rows", "scopes",
                "value", "now")

    observed: list[tuple[str, str | None, Any]] = []
    exports: list[tuple[str, Any]] = []
    for module in (domain, sources, reader):
        for name in sorted(n for n in dir(module) if not n.startswith("_")):
            if name == "annotations":
                continue  # bound by `from __future__ import annotations`, not a value
            exports.append((f"{module.__name__}.{name}", getattr(module, name)))
    check("the sweep found every public name of ALL THREE modules, data values "
          "included",
          sorted(label for label, _ in exports) == [
              "lib.assurance_health.SCHEMA_VERSION",
              "lib.assurance_health_sources.LABEL_ROUTE_ANSWER",
              "lib.assurance_health_sources.SCHEMA_VERSION",
              "lib.assurance_health_sources.assurance_health_census",
              "lib.control_plane_workflow_truth_reader.CENSUS_ROUTE_ANSWER",
              "lib.control_plane_workflow_truth_reader.SCHEMA_VERSION",
              "lib.control_plane_workflow_truth_reader.workflow_truth_census"],
          json.dumps(sorted(label for label, _ in exports)))

    # ---- THE DOMAIN MODULE'S PRIVATE VOCABULARY, NAME BY NAME, WITH ITS
    # ---- CONSUMER.  NOT AN ALLOWLIST: NOTHING HERE IS EXEMPTED FROM ANYTHING.
    #
    # The eleventh correction's rule: where the domain module legitimately carries
    # domain states as strings, they do not get an exemption from the closed
    # union -- they come off the public surface.  They did.  This table is the
    # receipt for that move, and it fails in BOTH directions: a name that is still
    # public fails (the sweep above would also carry its words), and a name whose
    # named consumer has stopped importing it fails too, because a private name
    # nobody reads is dead code wearing a justification.
    DOMAIN_PRIVATE_CONSUMERS = {
        "_DISPLAY_STATES": "ops/assurance-health-selftest.py",
        "_GREEN_STATE": "ops/assurance-health-selftest.py",
        "_CAPABILITY_STAGES": "ops/assurance-health-selftest.py",
        "_EVIDENCE_STATES": "ops/assurance-health-selftest.py",
        "_INDETERMINATE_EVIDENCE_STATES": "ops/assurance-health-selftest.py",
        "_DETERMINATE_NONPASS_EVIDENCE_STATES": "ops/assurance-health-selftest.py",
        "_EVIDENCE_OWNERS": "ops/assurance-health-selftest.py",
        "_PREACTIVATION_SLOTS": "ops/assurance-health-selftest.py",
        "_SCOPE_IDENTITY_FIELDS": "ops/assurance-health-selftest.py",
        "_SLOT_ADMISSIBLE_BASES": "ops/assurance-health-selftest.py",
        "_WORKFLOW_TRUTH_SCHEMA_VERSION": "ops/assurance-health-selftest.py",
        "_assurance_health_row": "ops/assurance-health-selftest.py",
        "_EVIDENCE_SLOTS": "lib/assurance_health_sources.py",
        "_OWED_EVIDENCE_OWNER_SEAMS": "lib/assurance_health_sources.py",
        "_AssuranceHealthContractError": "lib/assurance_health_sources.py",
        "_assurance_health": "lib/assurance_health_sources.py",
    }
    domain_bound, domain_all = _module_bindings(REPO / "lib" / "assurance_health.py")

    def _reference_count(text: str, name: str) -> int:
        """How many times ``name`` is really REFERENCED in ``text``.

        Word-bounded, and not preceded by a word character, so ``_assurance_health``
        is not counted inside ``_assurance_health_scopes``.  The table above lives
        in this file, so for this file's own entries the reference form asked for
        is the attribute one (``health._NAME``); a name that appeared only as a
        key of that table would otherwise certify itself.
        """
        return len(re.findall(r"(?<![\w])" + re.escape(name) + r"\b", text))

    for private_name, consumer in sorted(DOMAIN_PRIVATE_CONSUMERS.items()):
        consumer_text = (REPO / consumer).read_text(encoding="utf-8")
        references = (_reference_count(consumer_text, "health." + private_name)
                      + _reference_count(consumer_text, "import " + private_name)
                      if consumer.endswith("assurance-health-selftest.py")
                      else _reference_count(consumer_text, private_name))
        check(f"{private_name} is module-private, unexported, and really read by "
              f"{consumer}",
              private_name.startswith("_") and private_name in domain_bound
              and private_name not in domain_all
              and not hasattr(domain, private_name[1:])
              and references >= (1 if consumer.endswith("selftest.py") else 2),
              json.dumps({"bound": private_name in domain_bound,
                          "exported": private_name in domain_all,
                          "public_twin": hasattr(domain, private_name[1:]),
                          "references_in_consumer": references}))
    check("every privileged word the domain module carries is carried privately: "
          "no public name of it survives beyond the schema id",
          {name for name in domain_bound if not name.startswith("_")}
          == {"SCHEMA_VERSION", "annotations"},
          json.dumps(sorted(name for name in domain_bound
                            if not name.startswith("_"))))

    calls = returned = 0
    for label, member in exports:
        # (1) the exported NAME itself, and the value it is bound to
        observed.append((label, label.rsplit(".", 1)[1], label.rsplit(".", 1)[1]))
        for key, scalar in _outcome_scalars(member):
            observed.append((label, key, scalar))
        if not callable(member):
            continue
        # (2) every calling form
        attempts: list[tuple[tuple, dict]] = [((), {})]
        if _parameter_count(member):
            for shape in shapes.values():
                attempts.append(((shape,), {}))
                attempts += [((), {keyword: shape}) for keyword in keywords]
        for args, kwargs in attempts:
            calls += 1
            try:
                answer = member(*args, **kwargs)
            except Exception:
                continue  # a refusal is not a returned outcome; see the docstring
            returned += 1
            for key, scalar in _outcome_scalars(answer):
                observed.append((label, key, scalar))
    check(f"the sweep called every callable export over every calling form "
          f"({calls} calls, {returned} of them returning a value)",
          calls == sum(1 + (len(shapes) * (1 + len(keywords))
                            if callable(member) and _parameter_count(member) else 0)
                       for _, member in exports if callable(member))
          and returned >= 2 and len(observed) >= 16,
          json.dumps({"calls": calls, "returned": returned, "scalars": len(observed)}))

    # (3) the route through the real consumer, under the rebinding probe
    out, section_lines = _health_surface_sections()
    check("the hermetic run really did print both of this slice's sections",
          any(line.startswith("Workflow truth —") for line in section_lines)
          and any(line.startswith("Assurance health —") for line in section_lines)
          and len(section_lines) >= 5, json.dumps(section_lines))
    check("and it printed neither the caller's census nor the caller's reason, "
          "with the snapshot, render and frozen-answer names all rebound",
          not any(token in out for token in
                  ("caller-operational", "caller-owner", "caller_reason",
                   "caller_seam", "evidence-backed operational")),
          "\n".join(section_lines))
    for index, line in enumerate(section_lines):
        observed.append(("tools/health-check.py::section", f"line{index}", line))
    route_strings = _route_source_strings()
    check("the sweep found the string literals of every function on this route, "
          "the alarm's four included",
          len(route_strings) >= 8
          and {name for name, _ in route_strings} == set(ROUTE_FUNCTIONS),
          json.dumps(sorted({name for name, _ in route_strings})))
    for name, text in route_strings:
        observed.append((f"tools/health-check.py::{name}", name, text))

    # (4) THE ALARM'S OUTPUTS, through its unexported hook.  The twelfth
    # correction's route answers a question no other surface here answers, so its
    # answers are swept on the same footing as every export: keys and values, over
    # every seeded row set, plus the real route's own answer with the tap refused.
    alarm_answers = _alarm_hook_answers()
    check("the sweep reached the alarm's answers as well",
          len(alarm_answers) >= len(ALARM_SEEDS) + 1
          and any(isinstance(value, dict) and "hypothetical-alarm" in value
                  for value in alarm_answers.values()),
          json.dumps(sorted(alarm_answers)))
    for label, answer in sorted(alarm_answers.items()):
        observed.append((f"tools/health-check.py::alarm", label, label))
        for key, scalar in _outcome_scalars(answer):
            observed.append((f"tools/health-check.py::alarm", key, scalar))

    scalars = [(key, scalar) for _, key, scalar in observed]
    by_call = {label for label, _, _ in observed}
    check("all three surfaces are represented in what the sweep actually collected",
          any(label.startswith("lib.assurance_health_sources") for label in by_call)
          and any(label.startswith("lib.control_plane_workflow_truth_reader")
                  for label in by_call)
          and any(label.startswith("tools/health-check.py") for label in by_call),
          json.dumps(sorted(by_call)))

    # ---- ONE TEST PER WORD, each with its own control -----------------------
    for word in PRIVILEGED_WORD_UNION:
        hits = _word_hits(scalars, word)
        control = _outcome_scalars({"state": word, word: True,
                                    "note": f"prefix-{word}-suffix"})
        detected = _word_hits(control, word)

        # THE KEY-COVERAGE CONTROLS, AND WHY THEY ARE PART OF THIS CHECK.  The
        # tenth cut's nested-key coverage was vacuous: the recursion kept the
        # innermost key only, and the key was read only when its value was boolean
        # ``True``.  A review mutated the suite with ``{"verified": "neutral"}``
        # and ``{"verified": false}`` and got ZERO failures out of either -- only a
        # string leaf could fail it.  Both of those are the first two shapes here,
        # and every one of these must be detected or this word's check fails, so
        # the coverage cannot go quietly dead again.
        key_controls = {
            "nested string value": {"outer": {word: "neutral"}},
            "nested false value": {"outer": {word: False}},
            "nested null value": {"outer": {word: None}},
            "nested zero value": {"outer": {word: 0}},
            "nested mapping value": {"outer": [{word: {"deep": "x"}}]},
            "nested empty mapping": {"outer": {word: {}}},
            "nested opaque handle": {"outer": {word: object()}},
        }
        undetected = sorted(label for label, shape in key_controls.items()
                            if not _word_hits(_outcome_scalars(shape), word))
        check(f"no swept surface carries the privileged word {word!r}, exact or "
              f"substring, and every key-bearing shape of it is detectable",
              not hits and len(detected) >= 3 and not undetected,
              json.dumps({"hits": hits[:4], "control_detected": detected,
                          "undetected_key_shapes": undetected}))

    for label, pattern in PRIVILEGED_WORD_PATTERNS:
        hits = _pattern_hits(scalars, pattern)
        control = _outcome_scalars({"state": "would_be_green_if_authoritative"})
        # The KEY control, on the same footing the word checks now use: a nested
        # key spelling the pattern is the same claim as a value spelling it, and
        # without this control the key half of ``_pattern_hits`` could go dead
        # without the suite noticing.
        key_control = _outcome_scalars(
            {"outer": {"would_be_green_if_authoritative": False},
             "inner": {"state_if_authoritative": None}})
        check(f"no swept surface carries a string matching {label}, as a value or "
              f"as a key",
              not hits and bool(_pattern_hits(control, pattern))
              and bool(_pattern_hits(key_control, pattern)),
              json.dumps({"hits": hits[:4],
                          "control_detected": _pattern_hits(control, pattern),
                          "key_control_detected": _pattern_hits(key_control, pattern)}))


def sources_public_surface_guard_checks(health) -> None:
    """PARSER-BACKED, for the ADAPTER half of this surface.

    THE DEFECT THIS EXISTS FOR.  ``public_surface_guard_checks`` parses
    ``lib/assurance_health.py`` and only that file.  While it reported green,
    ``lib/assurance_health_sources`` had no ``__all__`` at all and exported
    ``assurance_health_scopes(workflows)``, which validated a caller's census
    schema and each row's key and version and then returned THE CALLER'S OWN ROW
    back as ``workflow_truth``.  A review fed it a hand-written census and
    reproduced ``{"state": "healthy", "green": true, "capability_stage": "act"}``
    through that exported function.  A guard bound to one file of a two-file
    surface certifies the half it reads and says nothing about the other.

    WHAT IT PINS, in order.  (1) The module declares ``__all__`` and its public
    names are EXACTLY that list -- nothing is public by accident here.  (2) No
    public name carries a classifier, admission, fixture, snapshot or scopes
    shape.  (3) The two retired entry points are gone from the module under any
    name.  (4) The private classification names exist, start with an underscore
    and are unexported.  (5) NO PUBLIC CALLABLE ACCEPTS ANY PARAMETER AT
    ALL.  That is stronger than the ninth cut, which allowed exactly one -- the
    reader's own reading handle -- and it is stronger for a measured reason: the
    tenth review ran caller code INSIDE the route through the ``del`` of that
    unused parameter's object.  A parameter that is never used is not
    documentation, it is a window.  (6) Every caller-controlled census shape is
    pushed at every public callable positionally and under every plausible
    keyword, and every one of them is refused by the signature before a line of
    the route runs; nothing comes back at all, so neither a privileged string nor
    the caller's own sentinel can.  (7) The public entry is called for real, with
    nothing, and its answer carries no privileged string.  (8) A control proves
    the probe fires.
    """
    import inspect

    import lib.assurance_health_sources as sources

    bound, declared_all = _module_bindings(REPO / "lib" / "assurance_health_sources.py")
    check("the adapter declares an explicit public export list",
          bool(declared_all), json.dumps(declared_all))
    public = {name for name in bound if not name.startswith("_")}
    check("every exported adapter name is actually bound at module level",
          not sorted(set(declared_all) - bound),
          json.dumps(sorted(set(declared_all) - bound)))
    imported_public = {name for name in dir(sources) if not name.startswith("_")}
    check("the parser and the imported adapter agree on the public names",
          public == imported_public, json.dumps(sorted(public ^ imported_public)))
    # STRICTER THAN THE DOMAIN HALF ON PURPOSE: every import in this module is
    # aliased under an underscore, so its public surface is its export list
    # exactly and a new public name cannot arrive by accident. ``annotations`` is
    # the one unavoidable exception -- ``from __future__ import annotations``
    # binds it and there is no aliased form of that statement.
    surface = public - {"annotations"}
    check("the adapter's public surface is exactly its export list",
          surface == set(declared_all),
          json.dumps(sorted(surface ^ set(declared_all))))
    forbidden_public = sorted(
        name for name in public | set(declared_all)
        if any(token in name.lower() for token in
               ("predicate", "unwired", "admit", "fixture", "compile", "classify",
                "snapshot", "scopes")))
    check("no classifier, admission, fixture, snapshot or scopes route is public",
          not forbidden_public, json.dumps(forbidden_public))
    retired = [name for name in ("assurance_health_scopes", "assurance_health_from_snapshot")
               if hasattr(sources, name)]
    check("both retired census-accepting entry points are gone, not renamed",
          not retired, json.dumps(retired))
    for name in ("_assurance_health_scopes", "_project",
                 "_would_be_assurance_health_if_authoritative"):
        check(f"{name} is module-private and unexported",
              name.startswith("_") and name in bound and name not in declared_all)

    # ---- (5) the structural claim: no public callable takes anything ---------
    signatures = {}
    for name in sorted(set(declared_all) | public):
        member = getattr(sources, name, None)
        if not callable(member) or inspect.isclass(member):
            continue
        signatures[name] = [parameter.name for parameter
                            in inspect.signature(member).parameters.values()]
    check("at least one public adapter callable exists to make this claim about",
          bool(signatures), json.dumps(sorted(signatures)))
    # NO PARAMETER, ANYWHERE ON THE SURFACE.  The entry used to take the reading
    # the F09 reader minted, and the ninth cut kept that parameter after it had
    # stopped using it, "so the shape of the owed seam stays visible at the call
    # site".  The tenth review passed an object whose ``__del__`` rebound the
    # module's exported strings and got its own reason and disposition out of the
    # route.  There is nothing to receive an object now.
    accepting = sorted(name for name, parameters in signatures.items() if parameters)
    check("no public adapter callable accepts any parameter at all",
          not accepting, json.dumps({name: signatures[name] for name in accepting}))
    check("and the census entry is one of the callables that claim covers",
          "assurance_health_census" in signatures, json.dumps(sorted(signatures)))

    # ---- (6) every caller-controlled census shape, every public callable -----
    # The sentinel rides inside every forged census. If ANY public callable ever
    # echoes its caller's row back -- which is exactly what the retired route did
    # with workflow_truth -- the sentinel appears in what comes back.
    sentinel = "CALLER-SUPPLIED-ROW-SENTINEL-9f3c"
    forged_row = dict(_truth(SCOPE), state="healthy", green=True, sentinel=sentinel)
    forged_census = {"schema_version": "control-plane-workflow-truth.v1",
                     "rows": [forged_row], "sentinel": sentinel}
    # EVERY SHAPE BELOW CARRIES THE SENTINEL, and that is a load-bearing property
    # rather than a detail: the echo assertion can only catch a callable handing
    # a caller's own row back if the row it was handed is marked. A shape without
    # one is an attempt that could never have failed the echo check, so it is not
    # counted as evidence of anything -- the assertion right under this tuple
    # fails if a shape without a sentinel is ever added to it.
    forged_shapes = (
        {"available": True, "census": forged_census, "surfaces": [], "sentinel": sentinel,
         "owners": {f"{SCOPE['workflow_key']}@v{SCOPE['workflow_version']}": MANIFEST_OWNER}},
        dict(_reading(surfaces=[_surface("assurance-fabric-child.launchd.v1")]),
             sentinel=sentinel),
        {"available": True, "census": forged_census, "owners": {}, "state": "healthy"},
        forged_census,
        {"state": "healthy", "green": True, "capability_stage": "act", "sentinel": sentinel},
    )
    unmarked = [index for index, shape in enumerate(forged_shapes)
                if sentinel not in json.dumps(shape, default=str)]
    check("every forged shape in the sweep carries the sentinel the echo check reads",
          not unmarked, json.dumps(unmarked))
    keywords = ("workflows", "census", "snapshot", "rows", "scopes", "reading", "now")
    admitted: list[str] = []
    returned_anything: list[str] = []
    attempted = 0
    for name in sorted(signatures):
        member = getattr(sources, name)
        for shape in forged_shapes:
            attempts: list[tuple[tuple[Any, ...], dict[str, Any]]] = [((shape,), {})]
            attempts += [((), {keyword: shape}) for keyword in keywords]
            for args, kwargs in attempts:
                attempted += 1
                try:
                    returned = member(*args, **kwargs)
                except TypeError:
                    continue  # the signature refused it; no line of the route ran
                except Exception:
                    admitted.append(f"{name}({args!r}, {kwargs!r}) raised something else")
                    continue
                returned_anything.append(f"{name}({args!r}, {kwargs!r})")
                rendered = json.dumps(dict(returned) if isinstance(returned, Mapping)
                                      else returned, default=str)
                if any(token in rendered for token in PRIVILEGED_OUTCOME_TOKENS) \
                        or sentinel in rendered:
                    admitted.append(f"{name}({args!r}, {kwargs!r}) -> {rendered[:80]}")
    expected_attempts = len(signatures) * len(forged_shapes) * (1 + len(keywords))
    check(f"every caller-supplied census is refused by the signature of every public "
          f"adapter callable ({attempted} attempts: {len(signatures)} public "
          f"callable(s) x {len(forged_shapes)} sentinel-bearing shapes x "
          f"{1 + len(keywords)} calling forms, every one a TypeError)",
          not admitted and not returned_anything,
          json.dumps({"admitted": sorted(set(admitted))[:4],
                      "returned": sorted(set(returned_anything))[:4]}))
    # THE COUNT IS ASSERTED EXACTLY, not as a floor. A floor lets the claim made
    # about this sweep drift above what the sweep performs, which is how the
    # report came to say 96 of a sweep that ran 48.
    check(f"the guard attempted exactly the sweep it claims ({expected_attempts})",
          attempted == expected_attempts, f"{attempted} != {expected_attempts}")
    # THE CONTROL FOR THAT REFUSAL. A signature that refuses everything would
    # also refuse a call that SHOULD work, and then the claim above would be
    # about a route nobody can reach. The no-argument call is the route, and it
    # answers the invariant.
    check("the refusal is the signature and not a dead route: called with nothing, "
          "the entry answers its invariant",
          _is_label_fallback(sources.assurance_health_census()),
          json.dumps(dict(sources.assurance_health_census())))

    # ---- (7) the public entry, called for real ------------------------------
    # No reader performs anything here any more: the reading route is deleted, so
    # the entry is called the only way it can be called, with nothing at all.
    read = sources.assurance_health_census()
    rendered_read = json.dumps(dict(read), default=str)
    check("the public entry answers the invariant not-proven result on this machine",
          _is_label_fallback(read), rendered_read[:300])
    check("the public entry's own answer carries no privileged outcome either",
          not any(token in rendered_read for token in PRIVILEGED_OUTCOME_TOKENS),
          json.dumps([token for token in PRIVILEGED_OUTCOME_TOKENS
                      if token in rendered_read]))
    check("every call returns the identical object, byte for byte",
          all(sources.assurance_health_census() is read for _ in range(3)),
          rendered_read[:200])

    # ---- (8) THE CONTROL ----------------------------------------------------
    # The same probe over the domain module's private hook DOES find every
    # privileged string, which is what makes the assertions above a measurement
    # of these public surfaces rather than of a probe that never fires.
    control = health._test_only_hypothetical_row(
        scope=SCOPE, workflow_truth=_truth(SCOPE), evidence=_evidence(),
        now=NOW)["would_be_healthy_if_authoritative"]
    rendered_control = json.dumps(control, default=str)
    check("the adapter probe detects every privileged string when one is really there",
          all(token in rendered_control for token in PRIVILEGED_OUTCOME_TOKENS),
          json.dumps([token for token in PRIVILEGED_OUTCOME_TOKENS
                      if token not in rendered_control]))
    # And the sentinel probe fires too: a dict that really does carry the caller's
    # row renders the sentinel, so "not echoed" above is a measurement.
    check("the echo probe detects a caller's own row when one is really echoed",
          sentinel in json.dumps({"workflow_truth": forged_row}, default=str))


def surface_wiring_checks() -> None:
    """The health surface itself, driven hermetically through its own TEST DOOR.

    A projection nothing reads is not wired to anything, so this drives the real
    ``tools/health-check.py`` canonical reader against a fixture snapshot -- no
    database, no network, no clock of ours.

    AND THE DOOR NOW SAYS WHAT IT IS.  The adapter takes only the reading the F09
    reader minted and accepts no census from a caller, and a --fixture file is
    JSON and can hold no handle, so a fixture can no longer reach the read path
    at all: under ``--fixture`` the surface announces on its first line that nothing
    below was read, reaches the adapter's unexported hook, and renders EVERY state
    as ``would-be-<state>-if-authoritative``.  These checks pin that labelling as
    hard as they pin the counts -- a test door that prints an unhedged state is
    the same defect as an adapter that accepts a census, one surface further out.
    """
    import os
    import re
    import subprocess
    import tempfile
    from datetime import datetime, timedelta, timezone
    from lib.control_plane_workflow_truth import workflow_truth

    now = datetime.now(timezone.utc).isoformat()
    surfaces = [{"workflow_key": "assurance-fabric-child", "workflow_version": 1,
                 "surface_id": "assurance-fabric-child.launchd.v1",
                 "locator": "com.carr.assurance-fabric-child", "scheduler_kind": "launchd",
                 "duplicate_group": None, "disable_receipt_ref": None,
                 "observation": {"scheduler_state": "enabled", "observed_at": now}}]
    census = workflow_truth(
        declarations=[{"key": "assurance-fabric-child", "version": 1, "enabled": True,
                       "legacy_schedule": {"provider": "none", "status": "disabled"}},
                      {"key": "ownerless-workflow", "version": 1, "enabled": True,
                       "legacy_schedule": {"provider": "none", "status": "disabled"}}],
        definitions=[{"key": "assurance-fabric-child", "version": 1, "enabled": True,
                      "execution_contract": {}, "legacy_disabled_at": None}],
        acceptances=[{"workflow_key": "assurance-fabric-child", "workflow_version": 1,
                      "mode": "shadow", "status": "accepted"}],
        surfaces=surfaces, completion={}, observation_max_age_seconds=900, now=now)
    snapshot = {"errors": [], "workflows": {
        "available": True, "census": census, "surfaces": surfaces,
        "owners": {"assurance-fabric-child@v1": "ops.job dispatcher"}}}

    # OUTSIDE THE REPOSITORY ON PURPOSE: a selftest that writes into the tree it
    # is invoked in is the exact thing ops/selftest-git-isolation-check.py exists
    # to catch, and a shared fixture path would cross-wire concurrent CI runs.
    handle, path = tempfile.mkstemp(suffix=".json", prefix="assurance-health-surface-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh)
        proc = subprocess.run(
            [sys.executable, str(REPO / "tools/health-check.py"), "--canonical",
             "--section", "jobs", "--fixture", path],
            cwd=str(REPO), text=True, capture_output=True, timeout=120)
    finally:
        os.unlink(path)
    out = proc.stdout

    check("a fixture-fed run announces on its first line that it read nothing",
          out.startswith("FIXTURE-DERIVED RUN — --fixture ")
          and "no line of it is evidence of health" in out, out[:300])
    check("the canonical health surface prints an assurance-health section at all",
          "Assurance health — FIXTURE-DERIVED HYPOTHETICAL, NOT A READING (test door)"
          in out, out[-400:])
    check("the section says the adapter did not read the control plane",
          "the adapter did NOT read the control plane" in out
          and "no finding is recorded from it" in out, out[-600:])
    check("the section reports a bound scope and no would-be-green one",
          "1 bound scope(s) WOULD BE" in out
          and "; 0 would-be-green-if-authoritative" in out, out[-400:])
    check("a workflow whose owner the manifest never declared is named, not projected",
          "WOULD BE UNPROJECTABLE ownerless-workflow@v1" in out
          and "inventory.owner" in out, out[-400:])
    check("the layers this surface does not read are printed as the gap they are",
          all(slot in out for slot in
              ("artifact_assessment", "execution_assessment", "candidate_outcome_oracle",
               "activation_readback"))
          and "NOT READ BY THIS SURFACE" in out, out[-400:])
    check("the unbindable outcome layer is named on the surface, not silently dropped",
          "UNBINDABLE ON THIS CENSUS" in out and "actual_business_outcome" in out, out[-400:])
    # Scoped to THIS section's own findings on purpose: the fixture carries no job
    # ledger, so the surrounding jobs section reports its own absence and the
    # process rc belongs to that, not to assurance health.
    check("a reading that holds no failure records no assurance-health finding",
          "CANONICAL_FINDING assurance_health_failed" not in out
          and "CANONICAL_FINDING assurance_health_degraded" not in out, out[-400:])
    summary_line = next(line for line in out.splitlines() if "bound scope(s)" in line)
    check("no scope reached a healthy label on a reading that cannot contain one",
          "0 would-be-healthy-if-authoritative" in summary_line
          and "1 would-be-unknown-if-authoritative" in summary_line, summary_line)
    # THE LABELLING ASSERTION, and it is the point of keeping this door at all.
    # Every count on the summary line is hedged; not one bare state name survives
    # on it, so no reader and no grep can lift a health claim out of a fixture.
    check("the fixture summary states every count as an explicit hypothetical",
          "WOULD BE:" in summary_line
          and all(f"would-be-{state}-if-authoritative" in summary_line
                  for state in ("healthy", "degraded", "failed", "unknown", "disabled",
                                "not-yet-operational", "green"))
          and not re.search(r"\d+ (?:healthy|green|degraded|failed|unknown|disabled)",
                            summary_line),
          summary_line)

    # INJECTION ON THE REAL SURFACE, AND WHAT IT MAY NOT DO. One workflow's
    # controller readback is pushed outside the registry's own observation window
    # and the other's is left current -- and NEITHER scope may move, because both
    # receipts were written into this fixture by this test. A snapshot a caller
    # composed is the caller's assertion about a store, not a reading of it, and
    # the review that produced this correction reproduced a passing controller
    # layer out of exactly such a snapshot. The surface may print what it does not
    # know; it may not print a finding it cannot evidence.
    stale_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    injected_surfaces = [
        dict(surfaces[0], surface_id="degrading.launchd.v1", workflow_key="degrading",
             observation={"scheduler_state": "enabled", "observed_at": stale_at}),
        dict(surfaces[0], surface_id="unaffected.launchd.v1", workflow_key="unaffected",
             observation={"scheduler_state": "enabled", "observed_at": now}),
    ]
    injected_census = workflow_truth(
        declarations=[{"key": key, "version": 1, "enabled": True,
                       "legacy_schedule": {"provider": "none", "status": "disabled"}}
                      for key in ("degrading", "unaffected")],
        definitions=[{"key": key, "version": 1, "enabled": True,
                      "execution_contract": {}, "legacy_disabled_at": None}
                     for key in ("degrading", "unaffected")],
        acceptances=[{"workflow_key": key, "workflow_version": 1,
                      "mode": "shadow", "status": "accepted"}
                     for key in ("degrading", "unaffected")],
        surfaces=injected_surfaces, completion={}, observation_max_age_seconds=900, now=now)
    injected = {"errors": [], "workflows": {
        "available": True, "census": injected_census, "surfaces": injected_surfaces,
        "owners": {"degrading@v1": "ops.job dispatcher",
                   "unaffected@v1": "ops.job dispatcher"}}}
    handle, path = tempfile.mkstemp(suffix=".json", prefix="assurance-health-injected-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(injected, fh)
        injected_proc = subprocess.run(
            [sys.executable, str(REPO / "tools/health-check.py"), "--canonical",
             "--section", "jobs", "--fixture", path],
            cwd=str(REPO), text=True, capture_output=True, timeout=120)
    finally:
        os.unlink(path)
    section = injected_proc.stdout.split("Assurance health —", 1)[-1]

    check("an injected receipt cannot degrade a scope on the real surface",
          "degrading v1 DEGRADED" not in section
          and "unaffected v1 DEGRADED" not in section
          and "WOULD BE DEGRADED IF AUTHORITATIVE" not in section, section[:600])
    # PRINTED, not recorded: _canonical_finding writes one stdout line and there
    # is no record-layer seam behind it.  The claim is kept exactly that size --
    # and here the line is not printed at all, because nothing was evidenced.
    check("no CANONICAL_FINDING line is printed out of a composed snapshot",
          "CANONICAL_FINDING assurance_health_degraded" not in section
          and "CANONICAL_FINDING assurance_health_failed" not in section, section[:600])
    check("both scopes are carried as unknown rather than dropped or guessed at",
          "2 bound scope(s) WOULD BE: 0 would-be-healthy-if-authoritative, "
          "0 would-be-degraded-if-authoritative, 0 would-be-failed-if-authoritative, "
          "2 would-be-unknown-if-authoritative" in section, section[:600])
    check("the surface names the controller layer as one it did not read",
          "NOT READ BY THIS SURFACE" in section
          and "controller_assessment" in section, section[:600])

    # TWO WORKFLOW-ONLY SCOPES, ON THE REAL SURFACE.
    #
    # THE DEFECT THIS PINS.  Every scope the live census binds is bound by
    # workflow identity alone and carries NO Work Request id, and the summary
    # ordered and listed its scopes by that absent identity.  One scope never
    # compared anything, so every fixture stayed green while the first live
    # reading with two of them raised TypeError inside the projection -- which the
    # adapter does not catch, so the whole section printed "UNAVAILABLE
    # (TypeError)" instead of the census it holds.  The assertion is therefore
    # driven through the surface with TWO of them.  The summary's degraded LIST,
    # which is the other half of that sort, is exercised with two genuinely
    # degraded scopes in workflow_only_scope_checks, because no reading this
    # surface can perform is determinate enough to degrade anything.
    both_degraded_surfaces = [
        dict(surfaces[0], surface_id=f"{key}.launchd.v1", workflow_key=key,
             observation={"scheduler_state": "enabled", "observed_at": stale_at})
        for key in ("degrading", "also-degrading")]
    both_degraded_census = workflow_truth(
        declarations=[{"key": key, "version": 1, "enabled": True,
                       "legacy_schedule": {"provider": "none", "status": "disabled"}}
                      for key in ("degrading", "also-degrading")],
        definitions=[{"key": key, "version": 1, "enabled": True,
                      "execution_contract": {}, "legacy_disabled_at": None}
                     for key in ("degrading", "also-degrading")],
        acceptances=[{"workflow_key": key, "workflow_version": 1,
                      "mode": "shadow", "status": "accepted"}
                     for key in ("degrading", "also-degrading")],
        surfaces=both_degraded_surfaces, completion={},
        observation_max_age_seconds=900, now=now)
    both_degraded = {"errors": [], "workflows": {
        "available": True, "census": both_degraded_census,
        "surfaces": both_degraded_surfaces,
        "owners": {"degrading@v1": "ops.job dispatcher",
                   "also-degrading@v1": "ops.job dispatcher"}}}
    handle, path = tempfile.mkstemp(suffix=".json", prefix="assurance-health-two-degraded-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(both_degraded, fh)
        two_proc = subprocess.run(
            [sys.executable, str(REPO / "tools/health-check.py"), "--canonical",
             "--section", "jobs", "--fixture", path],
            cwd=str(REPO), text=True, capture_output=True, timeout=120)
    finally:
        os.unlink(path)
    two_section = two_proc.stdout.split("Assurance health —", 1)[-1]
    check("two workflow-only scopes do not make the surface unavailable",
          "assurance health   UNAVAILABLE" not in two_section
          and "TypeError" not in two_section, two_section[:600])
    check("both workflow-only scopes are counted on the surface",
          "2 bound scope(s) WOULD BE: 0 would-be-healthy-if-authoritative, "
          "0 would-be-degraded-if-authoritative, 0 would-be-failed-if-authoritative, "
          "2 would-be-unknown-if-authoritative" in two_section, two_section[:600])
    # The census-level row sort is the half of that defect this surface still
    # exercises: two rows, both with work_request_id None, ordered against each
    # other. The summary's degraded LIST is the other half, and it is driven with
    # two genuinely degraded scopes in workflow_only_scope_checks.
    check("the summary was computed over both unbound rows rather than refused",
          "; 0 would-be-green-if-authoritative" in two_section
          and "0 would-be-disabled-if-authoritative, "
              "0 would-be-not-yet-operational-if-authoritative" in two_section,
          two_section[:600])


def census_route_invariance_checks() -> None:
    """THE CONSUMER, DRIVEN END TO END UNDER THE TENTH REVIEW'S PROBE.

    THE DEFECT THIS EXISTS FOR, quoted from the review that found it.  Rebinding
    the reader's module-level ``read_workflow_truth_snapshot`` -- which the old
    ``read_workflow_truth_reading`` resolved at call time -- made
    ``tools/health-check.py`` print

        0 declared/registered workflow(s): 7 evidence-backed operational, ...

    and that line is what ``run.sh health`` shows a human.  The label route had
    already taken the fallback by then; this was the OTHER route, one surface
    further out, and it was still rendering caller content as the control plane's
    own answer.

    WHAT REPLACED IT.  The reader mints and renders nothing; both this surface's
    F09 section and its A01 section print one invariant unavailable line built
    from frozen literals.  This check drives the real script in a subprocess with
    all three names rebound -- the snapshot read, the render, and the frozen
    census export -- and every subprocess refused, then asserts:

      1. both sections printed;
      2. not one of the caller's strings reached the output;
      3. both sections carry the same reason id, the same ``not_proven``
         disposition and the same owed seam name;
      4. no count, no state and no census row is printed by either of them;
      5. the control -- that the rebinding really did land in that process, which
         is asserted by the probe itself failing to change anything only because
         nothing reads it, not because the write silently failed.
    """
    out, section_lines = _health_surface_sections()
    census_section = [line for line in section_lines
                      if line.startswith("Workflow truth —")
                      or (section_lines.index(line) < len(section_lines)
                          and "workflow census" in line)]
    check("the canonical surface ran far enough to print both sections",
          "Workflow truth —" in out and "Assurance health —" in out, out[-600:])
    check("the F09 census section reports unavailable with the frozen reason, the "
          "not-proven disposition and the owed seam",
          f"  -- workflow census   UNAVAILABLE — {LABEL_ROUTE_REASON}; item carried "
          f"as {LABEL_ITEM_DISPOSITION}; owed seam {LABEL_OWED_SEAM}" in out,
          "\n".join(section_lines))
    check("the A01 assurance section reports the same three facts",
          f"  -- assurance health   UNAVAILABLE — {LABEL_ROUTE_REASON}; item carried "
          f"as {LABEL_ITEM_DISPOSITION}" in out
          and f"  -- OWED SEAM {LABEL_OWED_SEAM}" in out, "\n".join(section_lines))
    check("neither section prints a count, a state or a census row",
          not any(token in "\n".join(section_lines) for token in
                  ("declared/registered", "bound scope(s)", "evidence-backed",
                   "CANONICAL_FINDING assurance_health", "workflow_truth_conflict")),
          "\n".join(section_lines))
    check("none of the caller's strings reached the surface, with the snapshot, the "
          "render and the frozen census answer all rebound before the run",
          not any(token in out for token in ("caller-operational", "caller-owner",
                                             "caller_reason", "caller_seam",
                                             "verified")),
          "\n".join(section_lines))
    # THE CONTROL FOR THE PROBE ITSELF.  The rebinding has to be shown to LAND --
    # otherwise "nothing changed" could mean the probe never ran.  The same three
    # writes are performed here, in this process, and read back.
    import lib.control_plane_workflow_truth_reader as reader

    frozen = reader.workflow_truth_census()
    setattr(reader, "read_workflow_truth_snapshot", lambda: {"available": True})
    try:
        check("the probe's write really does land on the reader module: the name it "
              "rebinds exists afterwards and holds the caller's function",
              getattr(reader, "read_workflow_truth_snapshot")() == {"available": True}
              and reader.workflow_truth_census() is frozen,
              json.dumps(dict(reader.workflow_truth_census())))
    finally:
        delattr(reader, "read_workflow_truth_snapshot")
    check("and the census route is unchanged once the probe is cleaned up",
          reader.workflow_truth_census() is frozen
          and dict(reader.workflow_truth_census())["reason"] == LABEL_ROUTE_REASON,
          json.dumps(dict(reader.workflow_truth_census())))


def main() -> int:
    try:
        import lib.assurance_health as health
    except Exception as exc:  # red until the implementation exists
        print(f"assurance-health-selftest: implementation unavailable: {exc}")
        return 1

    display_state_checks(health)
    never_green_matrix_checks(health)
    preactivation_chain_checks(health)
    actual_outcome_separation_checks(health)
    scoped_degradation_checks(health)
    workflow_truth_governance_checks(health)
    scope_identity_checks(health)
    workflow_only_scope_checks(health)
    caller_evidence_is_never_authority_checks(health)
    public_surface_guard_checks(health)
    source_adapter_checks()
    sources_public_surface_guard_checks(health)
    label_route_fallback_checks()
    contradiction_alarm_checks()
    closed_union_sweep_checks()
    surface_wiring_checks()
    census_route_invariance_checks()
    refusal_checks(health)
    output_discipline_checks(health)

    print(f"\nassurance-health-selftest: {total-len(failures)}/{total} passed")
    if failures:
        print("FAILURES: " + ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
