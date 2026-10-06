#!/usr/bin/env python3
"""Hermetic tests for the provider-native launchd scheduler reader."""
from __future__ import annotations

import json
import plistlib
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lib.control_plane_scheduler_cutover import CutoverRefusal, scheduler_launchd_rows
from lib.launchd_scheduler_native import _disabled_override, read_native_launchd

FAILED: list[str] = []


def check(label: str, condition: bool) -> None:
    print(("  ok    " if condition else "  FAIL  ") + label)
    if not condition:
        FAILED.append(label)


def refuses(fn: Any) -> bool:
    try:
        fn()
    except CutoverRefusal:
        return True
    return False


def expand(value: Any, repo: Path) -> Any:
    if isinstance(value, str):
        return value.replace("{{REPO}}", str(repo))
    if isinstance(value, list):
        return [expand(item, repo) for item in value]
    if isinstance(value, dict):
        return {key: expand(item, repo) for key, item in value.items()}
    return value


def main() -> int:
    registry = json.loads((REPO / "ops/config/control-plane-scheduler-cutover.v1.json").read_text())
    manifest = json.loads((REPO / "ops/config/control-plane-workflows.v1.json").read_text())
    rows = scheduler_launchd_rows(registry, manifest=manifest, repo=REPO)
    nightly = next(row for row in rows if row[2] == "nightly-record-layer.launchd.v1")
    (_workflow, _version, _surface, locator, relpath, installed_name, args_json,
     plist_sha, schedule_sha, zone) = nightly
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        installed = home / "Library/LaunchAgents" / installed_name
        installed.parent.mkdir(parents=True)
        tracked = plistlib.loads((REPO / relpath).read_bytes())
        installed.write_bytes(plistlib.dumps(expand(tracked, REPO)))

        def enabled_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
            if command[1] == "print-disabled":
                return subprocess.CompletedProcess(command, 0, f'{{ "{locator}" => false }}', "")
            return subprocess.CompletedProcess(command, 0, "native job state", "")

        observed = read_native_launchd(
            home=home, repo=REPO, locator=locator, repo_plist_relpath=relpath,
            installed_plist_name=installed_name, expected_program_arguments=json.loads(args_json),
            plist_sha256=plist_sha, schedule_sha256=schedule_sha, expected_timezone=zone,
            runner=enabled_runner, host_timezone=zone, uid=501,
            installed_repo=REPO,
            observed_at=datetime(2026, 8, 17, 1, 0, tzinfo=timezone.utc),
        )
        check("native reader proves enabled state from exact launchctl and plist evidence",
              observed["enabled"] is True and len(observed["launchctl_revision"]) == 64
              and len(observed["source_fingerprint"]) == 64)

        def disabled_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
            if command[1] == "print-disabled":
                return subprocess.CompletedProcess(command, 0, f'{{ "{locator}" => true }}', "")
            return subprocess.CompletedProcess(command, 113, "", "not found")

        disabled = read_native_launchd(
            home=home, repo=REPO, locator=locator, repo_plist_relpath=relpath,
            installed_plist_name=installed_name, expected_program_arguments=json.loads(args_json),
            plist_sha256=plist_sha, schedule_sha256=schedule_sha, expected_timezone=zone,
            runner=disabled_runner, host_timezone=zone, uid=501,
            installed_repo=REPO,
        )
        check("disabled state requires both absent job and explicit native disabled override",
              disabled["enabled"] is False and disabled["source_fingerprint"] != observed["source_fingerprint"])

        def absent_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
            if command[1] == "print-disabled":
                return subprocess.CompletedProcess(command, 0, "{ }", "")
            return subprocess.CompletedProcess(command, 113, "", "not found")

        kwargs = dict(home=home, repo=REPO, locator=locator, repo_plist_relpath=relpath,
                      installed_plist_name=installed_name, expected_program_arguments=json.loads(args_json),
                      plist_sha256=plist_sha, schedule_sha256=schedule_sha,
                      expected_timezone=zone, host_timezone=zone, uid=501)
        kwargs["installed_repo"] = REPO
        check("mere launchctl absence is refused as ambiguous rather than called disabled",
              refuses(lambda: read_native_launchd(**kwargs, runner=absent_runner)))
        def duplicate_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
            if command[1] == "print-disabled":
                return subprocess.CompletedProcess(
                    command, 0, f'{{ "{locator}" => false; "{locator}" => true }}', "")
            return subprocess.CompletedProcess(command, 0, "native job state", "")
        check("duplicate or conflicting disabled overrides are refused",
              refuses(lambda: read_native_launchd(**kwargs, runner=duplicate_runner)))
        check("host timezone drift is refused", refuses(lambda: read_native_launchd(
            **{**kwargs, "host_timezone": "America/New_York"}, runner=enabled_runner)))
        drifted = dict(expand(tracked, REPO)); drifted["RunAtLoad"] = not bool(drifted.get("RunAtLoad"))
        installed.write_bytes(plistlib.dumps(drifted))
        check("installed plist drift is refused", refuses(lambda: read_native_launchd(
            **kwargs, runner=enabled_runner)))
        installed.unlink(); installed.symlink_to(REPO / relpath)
        check("installed plist symlink is refused", refuses(lambda: read_native_launchd(
            **kwargs, runner=enabled_runner)))
        installed.unlink()
        launch_agents = home / "Library/LaunchAgents"
        launch_agents.rmdir()
        alternate = home / "alternate-launch-agents"; alternate.mkdir()
        (alternate / installed_name).write_bytes(plistlib.dumps(expand(tracked, REPO)))
        launch_agents.symlink_to(alternate, target_is_directory=True)
        check("parent-directory symlink escape is refused", refuses(lambda: read_native_launchd(
            **kwargs, runner=enabled_runner)))
    print(f"launchd scheduler native selftest — {len(FAILED)} failure(s)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())

# macOS 26 prints words, not booleans, in `launchctl print-disabled` (measured
# 2026-08-27: 0 boolean lines, 59 word lines on this Mac). Reading only the
# boolean form made a disabled job read back as enabled, which silently blocked
# every legacy-schedule disable readback.
_D = _disabled_override
check("the word form disabled reads as an override", _D('"com.carr.x" => disabled', "com.carr.x") is True)
check("the word form enabled reads as no override", _D('"com.carr.x" => enabled', "com.carr.x") is False)
check("the boolean form true still reads as an override", _D('"com.carr.x" => true', "com.carr.x") is True)
check("the boolean form false still reads as no override", _D('"com.carr.x" => false', "com.carr.x") is False)
check("an absent label is not an override", _D("nothing here", "com.carr.x") is False)
check("mixed case is accepted", _D('"com.carr.x" => Disabled', "com.carr.x") is True)
try:
    _D('"com.carr.x" => wobble', "com.carr.x")
    check("an unrecognised state word refuses rather than defaulting", False)
except CutoverRefusal:
    check("an unrecognised state word refuses rather than defaulting", True)
try:
    _D('"com.carr.x" => disabled\n"com.carr.x" => enabled', "com.carr.x")
    check("a duplicate label still refuses", False)
except CutoverRefusal:
    check("a duplicate label still refuses", True)
