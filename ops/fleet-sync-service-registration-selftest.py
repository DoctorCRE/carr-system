#!/usr/bin/env python3
"""Source-level registration proof for the fleet-sync LaunchAgent.

This test proves representability and the existing generic run-receipt path. It
does not inspect launchctl or the production registry, so a pass cannot be read
as an installation, bootstrap, or ops.service projection receipt.
"""
from __future__ import annotations

import importlib.util
import json
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lib import launchd_calendar  # noqa: E402
SERVICES = REPO / "ops" / "config" / "services.json"
TOMBSTONES = REPO / "ops" / "config" / "reachability-tombstones.json"
PLIST = REPO / "ops" / "launchd" / "com.carr.fleet-sync.plist"
SCHEDULER_TRUTH = REPO / "tools" / "scheduler-truth.py"
SERVICE = "fleet-sync"
FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {label}")
    else:
        FAILED.append(label)
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))


def main() -> int:
    catalog = json.loads(SERVICES.read_text(encoding="utf-8"))
    matches = [service for service in catalog["services"]
               if service.get("key") == SERVICE]
    check("fleet-sync is declared exactly once", len(matches) == 1, repr(matches))
    if len(matches) != 1:
        return 1
    service = matches[0]
    check("fleet-sync has the exact Mac-local ownership and source contract",
          (service.get("owner_actor"), service.get("family"),
           service.get("runtime"), service.get("repo_path")) ==
          ("joe", "Local Mac edge", "launchd", "bin/fleet-sync.sh"),
          repr(service))
    check("fleet-sync remains high criticality", service.get("criticality") == "high")

    environments = service.get("environments", [])
    check("fleet-sync has one production environment declaration",
          len(environments) == 1 and environments[0].get("environment") == "production",
          repr(environments))
    environment = environments[0] if len(environments) == 1 else {}
    check("fleet-sync deployment door is its canonical LaunchAgent plist",
          environment.get("deploy_mechanism") ==
          "ops/launchd/com.carr.fleet-sync.plist", repr(environment))
    check("cadence is hourly with a one-hour grace",
          (environment.get("expected_cadence_seconds"),
           environment.get("cadence_grace_seconds")) == (3600, 3600),
          repr(environment))

    with PLIST.open("rb") as handle:
        launchd = plistlib.load(handle)
    expected_arguments = [
        "/bin/zsh", "{{REPO}}/bin/run-scheduled.sh", SERVICE, "fleet.sync",
        "/bin/zsh", "{{REPO}}/bin/fleet-sync.sh",
    ]
    check("LaunchAgent records through run-scheduled under fleet-sync/fleet.sync",
          launchd.get("ProgramArguments") == expected_arguments,
          repr(launchd.get("ProgramArguments")))
    check("LaunchAgent fires hourly",
          "StartInterval" not in launchd
          and launchd_calendar.cadence_seconds(launchd) == 3600,
          repr(launchd.get("StartCalendarInterval")))
    check("LaunchAgent also fires at load (login/wake catch-up)",
          launchd.get("RunAtLoad") is True,
          repr(launchd.get("RunAtLoad")))

    spec = importlib.util.spec_from_file_location("scheduler_truth_under_test",
                                                  SCHEDULER_TRUTH)
    if spec is None or spec.loader is None:
        raise RuntimeError("scheduler-truth.py is not importable")
    scheduler_truth: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scheduler_truth)
    check("scheduler truth recognizes the generic durable wrapper",
          scheduler_truth.wrapped(launchd))
    check("scheduler truth reads the receipt service key exactly",
          scheduler_truth.wrapper_service(launchd) == SERVICE,
          scheduler_truth.wrapper_service(launchd))

    dependencies = [dependency for dependency in catalog.get("dependencies", [])
                    if dependency.get("service") == SERVICE]
    check("generic receipt projection declares the existing Neon dependency",
          len(dependencies) == 1 and
          dependencies[0].get("depends_on") == "neon-record-layer",
          repr(dependencies))

    tombstones = json.loads(TOMBSTONES.read_text(encoding="utf-8"))["tombstones"]
    check("fleet-sync no longer has a reachability tombstone",
          not any(mark.get("entry") ==
                  "ops/launchd/com.carr.fleet-sync.plist" for mark in tombstones),
          repr(tombstones))

    reachable = subprocess.run(
        [sys.executable, str(REPO / "ops" / "reachability-check.py"),
         "--repo", str(REPO), "--json"],
        capture_output=True, text=True, check=False,
    )
    try:
        findings = json.loads(reachable.stdout).get("findings", [])
    except json.JSONDecodeError:
        findings = [{"entry": reachable.stdout, "detail": reachable.stderr}]
    check("live reachability check has no fleet-sync finding",
          not any("fleet-sync" in str(finding.get("entry", ""))
                  for finding in findings), repr(findings))

    print(f"\n{14 - len(FAILED)} passed, {len(FAILED)} failed")
    if FAILED:
        print("failed: " + ", ".join(FAILED), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
