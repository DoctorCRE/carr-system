#!/usr/bin/env python3
"""Guard Joe ownership without taking any capability away from Dell."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
FAILED: list[str] = []


import pathlib, sys  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root, for lib/
from lib.gate_check import Check  # noqa: E402

check = Check(detail=None, failures=FAILED)


def read(path: str) -> dict[str, Any]:
    value = json.loads((REPO / path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def decision_owners(value: Any) -> list[Any]:
    found: list[Any] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "decision_owner":
                found.append(child)
            found.extend(decision_owners(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(decision_owners(child))
    return found


def main() -> int:
    governance = read("ops/config/system-governance.v1.json")
    check("Joe is the sole required system authority",
          governance.get("required_system_authority") == "joe")
    dell = governance.get("dell", {})
    check("Dell approval is explicitly optional and nonblocking",
          dell.get("approval_required") is False
          and dell.get("participation") == "permitted_optional_nonblocking")
    check("Dell retains user, teaching, review, and voluntary decision capability",
          set(dell.get("preserved_capabilities", []))
          == {"use_authorized_system", "read_write_and_operate_authorized_business_records",
              "teach_workflows_and_rules", "review_and_comment",
              "participate_in_decisions_voluntarily"})
    compatibility = dell.get("compatibility_evidence", {})
    check("Dell compatibility evidence preserves access without becoming approval",
          "strand Dell" in compatibility.get("required_when", "")
          and "never Dell approval" in compatibility.get("meaning", ""))

    current_contracts = [
        "phase0/program.v1.json",
        "workspace/contracts/phase0-acceptance.v1.json",
        "workspace/contracts/phase0-manifest.v1.json",
        "workspace/contracts/environment-release-process.v1.json",
        "workspace/contracts/phase0-traceability.v1.json",
        "workspace/contracts/market-map-route-planning.v1.json",
        "workspace/contracts/notification-event-taxonomy.v1.json",
        "workspace/contracts/surface-registry-migration-map.v1.json",
        "workspace/contracts/threat-model.v1.json",
        "workspace/contracts/tenant-workflow-governance.v1.json",
    ]
    forbidden = (
        "Joe and Dell complete",
        "Joe and Dell each",
        "Joe/Dell cutover approval",
        "after Joe/Dell approval",
        "uncoached_joe_and_dell",
        "both partners complete",
        "required_co_acceptance",
    )
    for path in current_contracts:
        body = (REPO / path).read_text(encoding="utf-8")
        check(f"{path} has no Dell-required system gate",
              not any(phrase in body for phrase in forbidden))

    council = read("workspace/contracts/council-review-register.v1.json")
    owners = decision_owners(council)
    check("Dell is not a required owner of high-level system decisions",
          all(owner != "Dell" and not (isinstance(owner, list) and "Dell" in owner)
              for owner in owners))
    check("Dell remains an invited user-research and review participant",
          "Dell" in json.dumps(council.get("governance", {})))
    release = read("workspace/contracts/environment-release-process.v1.json")
    check("Dell sign-in remains covered as compatibility evidence, not approval",
          "Joe and Dell sign-in succeeds" in json.dumps(release)
          and "Joe uncoached cutover evidence; Dell validation is optional and nonblocking" in json.dumps(release))
    continuity = (REPO / "ops/partner-continuity-gate.py").read_text(encoding="utf-8")
    check("Dell continuity path remains available while Joe alone retires the legacy surface",
          'PARTNERS = ("joe", "dell")' in continuity
          and "joe_retirement_approval" in continuity)

    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
