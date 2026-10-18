#!/usr/bin/env python3
"""Hermetic acceptance tests for read-only scheduler cutover contracts."""
from __future__ import annotations

import json
import hashlib
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from lib.control_plane_scheduler_cutover import (CutoverRefusal, observe_claude_scheduler_receipt, observe_launchd,
                                                  observe_launchd_scheduler_receipt,
                                                  prepare_disable, validate_registry,
                                                  verify_disabled)

REGISTRY = json.loads((REPO / "ops/config/control-plane-scheduler-cutover.v1.json").read_text(encoding="utf-8"))
MANIFEST = json.loads((REPO / "ops/config/control-plane-workflows.v1.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 8, 16, 18, 0, tzinfo=timezone.utc)
NOW_TEXT = "2026-08-16T18:00:00Z"
LATER_TEXT = "2026-08-16T18:01:00Z"
failures: list[str] = []
total = 0


def check(name: str, condition: bool) -> None:
    global total
    total += 1
    print(f"  {'ok  ' if condition else 'FAIL'} {name}")
    if not condition:
        failures.append(name)


def refuses(name: str, fn) -> None:
    try:
        fn()
    except CutoverRefusal:
        check(name, True)
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL {name} ({type(exc).__name__}: {exc})")
        failures.append(name)
    else:
        check(name, False)


def surface(surface_id: str) -> dict:
    return next(item for item in REGISTRY["surfaces"] if item["surface_id"] == surface_id)


def observed(surface_id: str, *, loaded: set[str], at: str = NOW_TEXT) -> dict:
    item = deepcopy(surface(surface_id))
    locator = item["locator"]
    plist = {"Label": locator, "ProgramArguments": item["canonical_program_arguments"]}
    item["canonical_plist_fingerprint"] = hashlib.sha256(
        json.dumps(plist, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    return observe_launchd(item, repo_plist=plist, installed_plist=plist,
                           repo_path=item["repo_plist_relpath"], installed_path=item["installed_plist_name"],
                           loaded_labels=loaded, observed_at=at)


def healthy_replacement() -> dict:
    return {
        "workflow_key": "nightly-record-layer", "workflow_version": 3, "healthy": True,
        "accepted_receipt_refs": ["receipt:shadow", "receipt:canary"],
    }


def receipt_verifier(ref: str) -> dict | None:
    mode = {"receipt:shadow": "shadow", "receipt:canary": "canary"}.get(ref)
    if mode is None:
        return None
    return {"kind": "workflow_acceptance_receipt", "receipt_ref": ref, "workflow_key": "nightly-record-layer",
            "workflow_version": 3, "mode": mode,
            "status": "accepted", "immutable": True}


def approval_verifier(ref: str) -> dict | None:
    if ref != "approval:joe":
        return None
    return {"kind": "human_authority_receipt", "receipt_ref": ref, "immutable": True,
            "authority_subject": "joe", "action": "disable-legacy-schedule",
            "subject": {"workflow_key": "nightly-record-layer", "workflow_version": 3,
                        "surface_id": "nightly-record-layer.launchd.v1", "locator": "com.carr.nightly-record-layer"},
            "observation_refs": {"pre": "launchd:enabled", "post": "launchd:disabled", "sibling": None}}


def prepared() -> tuple[dict, dict]:
    pre = observe_launchd_scheduler_receipt(
        surface("nightly-record-layer.launchd.v1"), launchd_receipt("enabled", "launchd:enabled", NOW_TEXT))
    return prepare_disable(REGISTRY, surface_id="nightly-record-layer.launchd.v1", observation=pre,
                           replacement=healthy_replacement(), receipt_verifier=receipt_verifier, now=NOW), pre


def provider_receipt(state: str, ref: str, at: str) -> dict:
    return {"kind": "provider_scheduler_observation_receipt", "receipt_ref": ref,
            "surface_id": "cc-update-audit.claude-code.v1", "workflow_key": "cc-update-audit",
            "workflow_version": 1, "locator": "cc-update-audit", "scheduler_state": state,
            "observed_at": at, "provider_revision": hashlib.sha256(("revision:" + ref).encode()).hexdigest(),
            "source_fingerprint": hashlib.sha256(("source:" + ref).encode()).hexdigest(),
            "device_principal_bound": True, "immutable": True}


def launchd_receipt(state: str, ref: str, at: str) -> dict:
    return {"kind": "scheduler_observation_receipt", "receipt_ref": ref,
            "surface_id": "nightly-record-layer.launchd.v1", "workflow_key": "nightly-record-layer",
            "workflow_version": 3, "locator": "com.carr.nightly-record-layer",
            "scheduler_kind": "launchd", "scheduler_state": state, "observed_at": at,
            "provider_revision": hashlib.sha256(("launchctl:" + ref).encode()).hexdigest(),
            "source_fingerprint": hashlib.sha256(("launchd-source:" + ref).encode()).hexdigest(),
            "device_principal_bound": True, "immutable": True}


def cc_receipt_verifier(ref: str) -> dict | None:
    mode = {"cc:shadow": "shadow", "cc:canary": "canary"}.get(ref)
    if mode is None:
        return None
    return {"kind": "workflow_acceptance_receipt", "receipt_ref": ref, "workflow_key": "cc-update-audit",
            "workflow_version": 1, "mode": mode, "status": "accepted", "immutable": True}


def cc_approval_verifier(ref: str) -> dict | None:
    if ref != "approval:cc":
        return None
    return {"kind": "human_authority_receipt", "receipt_ref": ref, "immutable": True,
            "authority_subject": "joe", "action": "disable-legacy-schedule",
            "subject": {"workflow_key": "cc-update-audit", "workflow_version": 1,
                        "surface_id": "cc-update-audit.claude-code.v1", "locator": "cc-update-audit"},
            "observation_refs": {"pre": "provider:enabled", "post": "provider:disabled", "sibling": None}}


def main() -> int:
    check("registry validates and inventories every non-disabled legacy workflow",
          validate_registry(REGISTRY, manifest=MANIFEST) == [])
    check("registry names all 19 non-disabled workflows and both Notes surfaces",
          len({item["workflow_key"] for item in REGISTRY["surfaces"]}) == 19
          and len(REGISTRY["surfaces"]) == 20)
    incomplete_inventory = deepcopy(REGISTRY)
    incomplete_inventory["surfaces"] = [item for item in incomplete_inventory["surfaces"]
                                          if item["workflow_key"] != "radar-weekly"]
    check("missing legacy workflow inventory is refused",
          any("missing non-disabled legacy workflow inventory" in error
              for error in validate_registry(incomplete_inventory, manifest=MANIFEST)))
    stale_surface = deepcopy(REGISTRY)
    stale_surface["surfaces"].append({"surface_id": "stale.claude-code.v1", "workflow_key": "stale",
                                       "workflow_version": 1, "scheduler_kind": "claude-code", "locator": "stale"})
    check("extra/stale scheduler surface inventory is refused",
          any("stale or extra scheduler surface" in error for error in validate_registry(stale_surface, manifest=MANIFEST)))
    wrong_manifest_version = deepcopy(REGISTRY)
    wrong_manifest_version["surfaces"][0]["workflow_version"] = 99
    check("surface workflow version must match manifest",
          any("surface version does not match manifest" in error
              for error in validate_registry(wrong_manifest_version, manifest=MANIFEST)))
    enabled = observed("nightly-record-layer.launchd.v1", loaded={"com.carr.nightly-record-layer"})
    disabled = observed("nightly-record-layer.launchd.v1", loaded=set(), at=LATER_TEXT)
    check("local observation requires repo+installed+launchctl and reports enabled",
          enabled["scheduler_state"] == "enabled" and all(enabled["sources"].values()))
    check("local readback reports disabled only from exact local sources",
          disabled["scheduler_state"] == "disabled")
    enabled = observe_launchd_scheduler_receipt(
        surface("nightly-record-layer.launchd.v1"), launchd_receipt("enabled", "launchd:enabled", NOW_TEXT))
    disabled = observe_launchd_scheduler_receipt(
        surface("nightly-record-layer.launchd.v1"), launchd_receipt("disabled", "launchd:disabled", LATER_TEXT))
    check("launchd cutover uses an immutable device-bound native receipt",
          enabled["sources"]["launchd_receipt_read"] is True)
    provider_surface = surface("cc-update-audit.claude-code.v1")
    provider_enabled = observe_claude_scheduler_receipt(
        provider_surface, provider_receipt("enabled", "provider:enabled", NOW_TEXT))
    provider_replacement = {"workflow_key": "cc-update-audit", "workflow_version": 1, "healthy": True,
                            "accepted_receipt_refs": ["cc:shadow", "cc:canary"]}
    provider_prepare = prepare_disable(
        REGISTRY, surface_id=provider_surface["surface_id"], observation=provider_enabled,
        replacement=provider_replacement, receipt_verifier=cc_receipt_verifier, now=NOW)
    check("Claude provider surface prepares only from an immutable device-bound receipt",
          provider_prepare["binding"]["surface_id"] == provider_surface["surface_id"]
          and provider_enabled["sources"]["provider_receipt_read"] is True)
    provider_disabled = observe_claude_scheduler_receipt(
        provider_surface, provider_receipt("disabled", "provider:disabled", LATER_TEXT))
    provider_verified = verify_disabled(
        REGISTRY, prepared=provider_prepare, pre_disable_observation=provider_enabled,
        post_disable_observation=provider_disabled, human_approval_ref="approval:cc",
        approval_verifier=cc_approval_verifier, now=NOW + timedelta(minutes=1))
    check("Claude verification requires the Joe receipt to bind the exact native pre/post refs",
          provider_verified["scheduler_state"] == "disabled")
    wrong_provider_approval = lambda ref: {**(cc_approval_verifier(ref) or {}),
                                            "observation_refs": {"pre": "wrong", "post": "provider:disabled", "sibling": None}}
    refuses("Claude verification refuses a Joe receipt bound to different native observations", lambda: verify_disabled(
        REGISTRY, prepared=provider_prepare, pre_disable_observation=provider_enabled,
        post_disable_observation=provider_disabled, human_approval_ref="approval:cc",
        approval_verifier=wrong_provider_approval, now=NOW + timedelta(minutes=1)))
    mutable_provider = provider_receipt("enabled", "provider:mutable", NOW_TEXT)
    mutable_provider["immutable"] = False
    refuses("mutable caller provider observation is refused", lambda: observe_claude_scheduler_receipt(
        provider_surface, mutable_provider))
    item = deepcopy(surface("nightly-record-layer.launchd.v1"))
    plist = {"Label": item["locator"], "ProgramArguments": item["canonical_program_arguments"]}
    item["canonical_plist_fingerprint"] = hashlib.sha256(json.dumps(plist, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    unknown = observe_launchd(item, repo_plist=plist, installed_plist=plist,
                              repo_path=item["repo_plist_relpath"], installed_path=item["installed_plist_name"],
                              loaded_labels=None, observed_at=NOW_TEXT)
    check("unread launchctl is unknown, never disabled", unknown["scheduler_state"] == "unknown")
    drifted_installed = dict(plist)
    drifted_installed["ProgramArguments"] = ["/bin/zsh", "unexpected"]
    same_label_drift = observe_launchd(item, repo_plist=plist, installed_plist=drifted_installed,
                                       repo_path=item["repo_plist_relpath"], installed_path=item["installed_plist_name"],
                                       loaded_labels={item["locator"]}, observed_at=NOW_TEXT)
    check("same-label plist content/ProgramArguments/fingerprint drift is unknown",
          same_label_drift["scheduler_state"] == "unknown" and not same_label_drift["sources"]["installed_plist_matches"])

    prep, pre = prepared()
    check("prepare emits typed human-only evidence without mutation command",
          prep["kind"] == "scheduler_disable_prepare" and prep["action"] == "human_approval_required"
          and "launchctl" not in json.dumps(prep))
    verified = verify_disabled(REGISTRY, prepared=prep, pre_disable_observation=pre,
                               post_disable_observation=disabled,
                               human_approval_ref="approval:joe", approval_verifier=approval_verifier,
                               now=NOW + timedelta(minutes=1))
    check("post-disable readback is typed and bound to human approval",
          verified["kind"] == "scheduler_disable_readback" and verified["scheduler_state"] == "disabled")

    db_only = deepcopy(enabled)
    db_only["legacy_disabled_at"] = "2026-08-16T17:59:00Z"
    refuses("DB-only disabled claim is refused", lambda: prepare_disable(
        REGISTRY, surface_id="nightly-record-layer.launchd.v1", observation=db_only,
        replacement=healthy_replacement(), receipt_verifier=receipt_verifier, now=NOW))
    stale = deepcopy(enabled)
    stale["observed_at"] = "2026-08-16T17:44:59Z"
    refuses("stale observation is refused", lambda: prepare_disable(
        REGISTRY, surface_id="nightly-record-layer.launchd.v1", observation=stale,
        replacement=healthy_replacement(), receipt_verifier=receipt_verifier, now=NOW))
    wrong_locator = deepcopy(enabled)
    wrong_locator["locator"] = "com.carr.other"
    refuses("wrong locator is refused", lambda: prepare_disable(
        REGISTRY, surface_id="nightly-record-layer.launchd.v1", observation=wrong_locator,
        replacement=healthy_replacement(), receipt_verifier=receipt_verifier, now=NOW))
    wrong_version = deepcopy(enabled)
    wrong_version["workflow_version"] = 1
    refuses("wrong workflow version is refused", lambda: prepare_disable(
        REGISTRY, surface_id="nightly-record-layer.launchd.v1", observation=wrong_version,
        replacement=healthy_replacement(), receipt_verifier=receipt_verifier, now=NOW))
    unhealthy = healthy_replacement()
    unhealthy["healthy"] = False
    refuses("unhealthy replacement is refused", lambda: prepare_disable(
        REGISTRY, surface_id="nightly-record-layer.launchd.v1", observation=enabled,
        replacement=unhealthy, receipt_verifier=receipt_verifier, now=NOW))
    forged_receipt = lambda ref: {**(receipt_verifier(ref) or {}), "immutable": False}
    refuses("forged mutable acceptance receipt is refused", lambda: prepare_disable(
        REGISTRY, surface_id="nightly-record-layer.launchd.v1", observation=enabled,
        replacement=healthy_replacement(), receipt_verifier=forged_receipt, now=NOW))
    wrong_receipt_version = lambda ref: {**(receipt_verifier(ref) or {}), "workflow_version": 99}
    refuses("acceptance receipt with wrong workflow version is refused", lambda: prepare_disable(
        REGISTRY, surface_id="nightly-record-layer.launchd.v1", observation=enabled,
        replacement=healthy_replacement(), receipt_verifier=wrong_receipt_version, now=NOW))
    wrong_receipt_workflow = lambda ref: {**(receipt_verifier(ref) or {}), "workflow_key": "other-workflow"}
    refuses("acceptance receipt with wrong workflow key is refused", lambda: prepare_disable(
        REGISTRY, surface_id="nightly-record-layer.launchd.v1", observation=enabled,
        replacement=healthy_replacement(), receipt_verifier=wrong_receipt_workflow, now=NOW))
    notes = observed("notes-sweep-hourly.launchd.v1", loaded={"com.carr.notes-sweep"})
    notes_replacement = healthy_replacement()
    notes_replacement.update({"workflow_key": "notes-sweep-hourly", "workflow_version": 4})
    refuses("one of two Notes legacy schedules is refused outside the coordinated contract", lambda: prepare_disable(
        REGISTRY, surface_id="notes-sweep-hourly.launchd.v1", observation=notes,
        replacement=notes_replacement, receipt_verifier=receipt_verifier, now=NOW))
    forged_notes_binding = {
        "surface_id": "notes-sweep-hourly.launchd.v1", "workflow_key": "notes-sweep-hourly",
        "workflow_version": 4, "locator": "com.carr.notes-sweep",
        "pre_disable_observation_fingerprint": notes["source_fingerprint"], "replacement_receipts": ["receipt:shadow", "receipt:canary"],
    }
    forged_notes_prepare = {"schema_version": 1, "contract": "control-plane-scheduler-cutover",
                            "kind": "scheduler_disable_prepare", "binding": forged_notes_binding,
                            "prepare_fingerprint": __import__("hashlib").sha256(json.dumps(forged_notes_binding, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()}
    notes_disabled = observed("notes-sweep-hourly.launchd.v1", loaded=set(), at=LATER_TEXT)
    refuses("verify refuses forged self-consistent one-of-two Notes prepare", lambda: verify_disabled(
        REGISTRY, prepared=forged_notes_prepare, pre_disable_observation=notes,
        post_disable_observation=notes_disabled, human_approval_ref="approval:joe", approval_verifier=approval_verifier,
        now=NOW + timedelta(minutes=1)))
    refuses("provider/launchd Notes duplicate remains fail-closed on the single-surface path", lambda: prepare_disable(
        REGISTRY, surface_id="notes-sweep-hourly.claude-code.v1", observation=notes,
        replacement=notes_replacement, receipt_verifier=receipt_verifier, now=NOW))
    refuses("verification refuses a missing human-only approval", lambda: verify_disabled(
        REGISTRY, prepared=prep, pre_disable_observation=pre, post_disable_observation=disabled,
        human_approval_ref="approval:no", approval_verifier=approval_verifier,
        now=NOW + timedelta(minutes=1)))
    dell_approval = lambda ref: {**(approval_verifier(ref) or {}), "authority_subject": "dell"}
    refuses("Dell authority receipt cannot disable Joe-only legacy schedule", lambda: verify_disabled(
        REGISTRY, prepared=prep, pre_disable_observation=pre, post_disable_observation=disabled,
        human_approval_ref="approval:joe", approval_verifier=dell_approval,
        now=NOW + timedelta(minutes=1)))
    wrong_subject_approval = lambda ref: {**(approval_verifier(ref) or {}), "subject": {}}
    refuses("authority receipt with wrong schedule subject is refused", lambda: verify_disabled(
        REGISTRY, prepared=prep, pre_disable_observation=pre, post_disable_observation=disabled,
        human_approval_ref="approval:joe", approval_verifier=wrong_subject_approval,
        now=NOW + timedelta(minutes=1)))
    wrong_action_approval = lambda ref: {**(approval_verifier(ref) or {}), "action": "accept-workflow"}
    refuses("authority receipt with wrong action is refused", lambda: verify_disabled(
        REGISTRY, prepared=prep, pre_disable_observation=pre, post_disable_observation=disabled,
        human_approval_ref="approval:joe", approval_verifier=wrong_action_approval,
        now=NOW + timedelta(minutes=1)))
    same_post = deepcopy(pre)
    same_post["scheduler_state"] = "disabled"
    refuses("verification refuses a non-distinct post-disable observation", lambda: verify_disabled(
        REGISTRY, prepared=prep, pre_disable_observation=pre, post_disable_observation=same_post,
        human_approval_ref="approval:joe", approval_verifier=approval_verifier,
        now=NOW + timedelta(minutes=1)))

    print(f"control-plane-scheduler-cutover-selftest: {total-len(failures)}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
