#!/usr/bin/env python3
"""Fail-closed Phase 4 evidence gate for Joe/Dell continuity and Drive retirement.

This verifier does not manufacture a partner journey, change a device, call a
write verb, or touch Drive.  It accepts only independently collected evidence
from each partner's real machine and checks the exact, mechanically knowable
parts of the contract: identity attribution, the typed read/write/guidance paths,
Call Mode's two-person labels, device provenance, replacement recovery, and
Joe's separate retirement authority.  A missing journey is not extrapolated
from a passing unit test.

The input belongs in an ephemeral evidence export, never a Markdown status
file.  Evidence references must point at the canonical record/receipt that a
reviewer can read back.  Until both machines provide them, this gate fails
closed and prints the remaining human/device work.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
PARTNERS = ("joe", "dell")
VALID_WRITE_VERBS = frozenset({"add-loop", "log-activity", "record-finding", "update-loop"})


class ContinuityError(ValueError):
    pass


def _obj(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContinuityError(f"{path} must be an object")
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContinuityError(f"{path} must be a non-empty string")
    return value.strip()


def _accepted(value: Mapping[str, Any], path: str) -> None:
    if value.get("status") != "accepted":
        raise ContinuityError(f"{path}.status must be accepted")
    _text(value.get("evidence_ref"), f"{path}.evidence_ref")


def _partner_evidence(partner: str, raw: Any) -> None:
    row = _obj(raw, f"partners.{partner}")
    for key in ("read", "write", "guidance", "call_mode", "device"):
        if key not in row:
            raise ContinuityError(f"partners.{partner}.{key} is required")

    read = _obj(row["read"], f"partners.{partner}.read")
    _accepted(read, f"partners.{partner}.read")
    if read.get("actor") != partner or read.get("verb") != "standing-context":
        raise ContinuityError(f"partners.{partner}.read must be that partner's accepted standing-context call")

    write = _obj(row["write"], f"partners.{partner}.write")
    _accepted(write, f"partners.{partner}.write")
    if write.get("actor") != partner or write.get("verb") not in VALID_WRITE_VERBS:
        raise ContinuityError(f"partners.{partner}.write must name that partner and a supported typed write verb")
    _text(write.get("readback_ref"), f"partners.{partner}.write.readback_ref")

    guidance = _obj(row["guidance"], f"partners.{partner}.guidance")
    _accepted(guidance, f"partners.{partner}.guidance")
    if guidance.get("actor") != partner:
        raise ContinuityError(f"partners.{partner}.guidance must identify the proposing partner")
    if guidance.get("path") not in {"CLAUDE.md", "AGENTS.md"} and not str(guidance.get("path", "")).endswith("/SKILL.md"):
        raise ContinuityError(f"partners.{partner}.guidance must name CLAUDE.md, AGENTS.md or a skill")
    if guidance.get("proposal_status") != "proposed":
        raise ContinuityError(f"partners.{partner}.guidance must prove proposed-only, never automatic authority")
    _text(guidance.get("pull_request_ref"), f"partners.{partner}.guidance.pull_request_ref")

    call = _obj(row["call_mode"], f"partners.{partner}.call_mode")
    _accepted(call, f"partners.{partner}.call_mode")
    if call.get("local_partner") != partner:
        raise ContinuityError(f"partners.{partner}.call_mode.local_partner must equal {partner}")
    device_id = _text(call.get("device_id"), f"partners.{partner}.call_mode.device_id")
    labels = _obj(call.get("weekly_labels"), f"partners.{partner}.call_mode.weekly_labels")
    expected = {"mic": partner.title(), "system": "Dell" if partner == "joe" else "Joe"}
    if dict(labels) != expected:
        raise ContinuityError(f"partners.{partner}.call_mode.weekly_labels must equal {expected}")

    device = _obj(row["device"], f"partners.{partner}.device")
    _accepted(device, f"partners.{partner}.device")
    if _text(device.get("device_id"), f"partners.{partner}.device.device_id") != device_id:
        raise ContinuityError(f"partners.{partner}.device.device_id must match the Call Mode device")
    _text(device.get("capture_write_readback_ref"), f"partners.{partner}.device.capture_write_readback_ref")
    if device.get("database_owner_credential_used") is not False:
        raise ContinuityError(f"partners.{partner}.device must prove no database-owner credential was used")


def _drive_evidence(raw: Any) -> str:
    row = _obj(raw, "drive_retirement")
    for key in ("readers_repointed", "writers_repointed", "recovery_verified", "continuity"):
        if key not in row:
            raise ContinuityError(f"drive_retirement.{key} is required")
    for key in ("readers_repointed", "writers_repointed", "recovery_verified"):
        value = _obj(row[key], f"drive_retirement.{key}")
        _accepted(value, f"drive_retirement.{key}")
    continuity = _obj(row["continuity"], "drive_retirement.continuity")
    for partner in PARTNERS:
        value = _obj(continuity.get(partner), f"drive_retirement.continuity.{partner}")
        _accepted(value, f"drive_retirement.continuity.{partner}")

    approval = _obj(row.get("joe_retirement_approval", {}), "drive_retirement.joe_retirement_approval")
    disabled = row.get("legacy_drive_disabled")
    if approval.get("status") == "accepted":
        if approval.get("actor") != "joe":
            raise ContinuityError("drive_retirement.joe_retirement_approval.actor must be joe")
        _text(approval.get("evidence_ref"), "drive_retirement.joe_retirement_approval.evidence_ref")
        if disabled is not True:
            raise ContinuityError("Drive retirement approval exists but legacy_drive_disabled is not true")
        return "RETIRED"
    if disabled is True:
        raise ContinuityError("legacy Drive was disabled without an accepted Joe retirement approval")
    return "READY_FOR_JOE_APPROVAL"


def validate_evidence(raw: Any) -> dict[str, str]:
    root = _obj(raw, "evidence")
    if root.get("schema_version") != 1:
        raise ContinuityError("evidence.schema_version must be 1")
    partners = _obj(root.get("partners"), "partners")
    for partner in PARTNERS:
        _partner_evidence(partner, partners.get(partner))
    return {"drive_status": _drive_evidence(root.get("drive_retirement"))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("evidence", type=Path, help="JSON export of real partner journey receipts")
    ap.add_argument("--allow-waiting-joe-approval", action="store_true",
                    help="return zero for proven prerequisites that await Joe's separate retirement approval")
    args = ap.parse_args()
    try:
        raw = json.loads(args.evidence.read_text())
        result = validate_evidence(raw)
    except (OSError, json.JSONDecodeError, ContinuityError) as exc:
        print(f"NOT READY: {exc}")
        return 2
    status = result["drive_status"]
    print(f"partner continuity evidence: Joe + Dell read/write/guidance/call-mode/device accepted; Drive={status}")
    if status == "READY_FOR_JOE_APPROVAL" and not args.allow_waiting_joe_approval:
        print("NOT READY: only Joe's explicit retirement approval and then the verified legacy-disable receipt remain")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
