#!/usr/bin/env python3
"""Seeded refusal coverage for the Phase 4 partner-continuity gate."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lib.loadpy import load_module_from_path

gate = load_module_from_path("partner_continuity_gate", str(REPO / "ops" / "partner-continuity-gate.py"))


def partner(name: str) -> dict:
    other = "Dell" if name == "joe" else "Joe"
    return {
        "read": {"status": "accepted", "actor": name, "verb": "standing-context", "evidence_ref": f"record:{name}:read"},
        "write": {"status": "accepted", "actor": name, "verb": "add-loop", "evidence_ref": f"record:{name}:write", "readback_ref": f"loop:{name}"},
        "guidance": {"status": "accepted", "actor": name, "path": "AGENTS.md", "proposal_status": "proposed", "pull_request_ref": "https://github.com/DoctorCRE/carr-system/pull/1", "evidence_ref": f"record:{name}:guidance"},
        "call_mode": {"status": "accepted", "local_partner": name, "device_id": f"{name}-mac", "weekly_labels": {"mic": name.title(), "system": other}, "evidence_ref": f"record:{name}:call"},
        "device": {"status": "accepted", "device_id": f"{name}-mac", "capture_write_readback_ref": f"capture:{name}", "database_owner_credential_used": False, "evidence_ref": f"record:{name}:device"},
    }


BASE = {
    "schema_version": 1, "partners": {"joe": partner("joe"), "dell": partner("dell")},
    "drive_retirement": {
        "readers_repointed": {"status": "accepted", "evidence_ref": "record:readers"},
        "writers_repointed": {"status": "accepted", "evidence_ref": "record:writers"},
        "recovery_verified": {"status": "accepted", "evidence_ref": "record:recovery"},
        "continuity": {"joe": {"status": "accepted", "evidence_ref": "record:joe"}, "dell": {"status": "accepted", "evidence_ref": "record:dell"}},
        "joe_retirement_approval": {"status": "pending"}, "legacy_drive_disabled": False,
    },
}

passed = 0
failed: list[str] = []


def check(label, mutation, expected_status=None, refuses=False):
    global passed
    sample = copy.deepcopy(BASE)
    mutation(sample)
    try:
        result = gate.validate_evidence(sample)
        ok = not refuses and (expected_status is None or result["drive_status"] == expected_status)
    except gate.ContinuityError:
        ok = refuses
    if ok:
        passed += 1
    else:
        failed.append(label)


check("complete prerequisites wait for Joe approval", lambda x: None, "READY_FOR_JOE_APPROVAL")
check("wrong sponsor is refused", lambda x: x["partners"]["dell"]["read"].update(actor="joe"), refuses=True)
check("guidance cannot be automatic authority", lambda x: x["partners"]["joe"]["guidance"].update(proposal_status="active"), refuses=True)
check("call labels prove the other partner", lambda x: x["partners"]["dell"]["call_mode"].update(weekly_labels={"mic": "Dell", "system": "Other participant"}), refuses=True)
check("device ids must line up", lambda x: x["partners"]["joe"]["device"].update(device_id="other"), refuses=True)
check("device owner credentials are forbidden", lambda x: x["partners"]["joe"]["device"].update(database_owner_credential_used=True), refuses=True)
check("Drive cannot disable before Joe approval", lambda x: x["drive_retirement"].update(legacy_drive_disabled=True), refuses=True)
check("Joe approval requires actual disable", lambda x: x["drive_retirement"]["joe_retirement_approval"].update(status="accepted", actor="joe", evidence_ref="approval:1"), refuses=True)
check("complete retirement is accepted", lambda x: x["drive_retirement"].update(legacy_drive_disabled=True, joe_retirement_approval={"status": "accepted", "actor": "joe", "evidence_ref": "approval:1"}), "RETIRED")

with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "evidence.json"
    path.write_text(json.dumps(BASE))
    run = subprocess.run([sys.executable, str(REPO / "ops" / "partner-continuity-gate.py"), str(path)], capture_output=True, text=True)
    if run.returncode == 2 and "Joe's explicit retirement approval" in run.stdout:
        passed += 1
    else:
        failed.append("CLI fails closed while Joe approval is pending")
    allowed = subprocess.run([sys.executable, str(REPO / "ops" / "partner-continuity-gate.py"), str(path), "--allow-waiting-joe-approval"], capture_output=True, text=True)
    if allowed.returncode == 0:
        passed += 1
    else:
        failed.append("CLI permits evidence-only prerequisite review")

print(f"partner continuity gate selftest — {passed}/{passed + len(failed)} passed")
if failed:
    print("FAILED: " + "; ".join(failed))
    raise SystemExit(1)
