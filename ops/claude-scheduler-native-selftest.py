#!/usr/bin/env python3
"""Hermetic tests for the provider-native Claude scheduler reader."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lib.claude_scheduler_native import discover_snapshot, read_native_task, system_timezone
from lib.control_plane_scheduler_cutover import CutoverRefusal

FAILED: list[str] = []


def check(label: str, condition: bool) -> None:
    print(("  ok    " if condition else "  FAIL  ") + label)
    if not condition:
        FAILED.append(label)


def refuses(fn) -> bool:
    try:
        fn()
    except CutoverRefusal:
        return True
    return False


def value_or_none(fn):
    """Run fn and return its result, or None on CutoverRefusal -- so a check
    on the RESULT can fail cleanly with a FAIL line instead of the refusal
    propagating out of check()'s own argument evaluation and crashing the
    whole selftest with a traceback. On main's pre-fix code this is exactly
    what happens to the zoneinfo.default/ case below: system_timezone()
    raises before check() is ever called."""
    try:
        return fn()
    except CutoverRefusal:
        return None


def check_system_timezone() -> None:
    """system_timezone() reads the REAL host probe path, unlike every other
    check in this file, which passes host_timezone= directly and so never
    exercised it. That gap is exactly how a macOS 27 regression shipped
    silently: /etc/localtime resolves through /usr/share/zoneinfo/ on macOS
    26 and earlier, but through /usr/share/zoneinfo.default/ on macOS 27
    (confirmed on the Studio, BuildVersion 26A425), and the old literal
    "/zoneinfo/" match refused every macOS 27 host."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for zonedir in ("zoneinfo", "zoneinfo.default"):
            zone_target = root / zonedir / "America" / "Chicago"
            zone_target.parent.mkdir(parents=True, exist_ok=True)
            zone_target.write_text("tzdata", encoding="utf-8")
            localtime = root / f"localtime-{zonedir}"
            localtime.symlink_to(zone_target)
            check(f"system_timezone resolves through {zonedir}/",
                  value_or_none(lambda localtime=localtime: system_timezone(localtime)) == "America/Chicago")
        no_zone_target = root / "not-a-zone-dir" / "file"
        no_zone_target.parent.mkdir(parents=True, exist_ok=True)
        no_zone_target.write_text("x", encoding="utf-8")
        no_zone_localtime = root / "localtime-no-zone"
        no_zone_localtime.symlink_to(no_zone_target)
        check("system_timezone refuses a resolved path with no zoneinfo component",
              refuses(lambda: system_timezone(no_zone_localtime)))
        # Flagged in review: an ancestor directory that happens to be named
        # zoneinfo.<anything> earlier in the path must not steal the match
        # from the real zoneinfo directory that owns the tz name.
        lookalike_target = root / "zoneinfo.bak" / "usr" / "share" / "zoneinfo" / "America" / "Chicago"
        lookalike_target.parent.mkdir(parents=True, exist_ok=True)
        lookalike_target.write_text("tzdata", encoding="utf-8")
        lookalike_localtime = root / "localtime-lookalike-ancestor"
        lookalike_localtime.symlink_to(lookalike_target)
        check("system_timezone matches the LAST zoneinfo component, not an earlier lookalike ancestor",
              value_or_none(lambda: system_timezone(lookalike_localtime)) == "America/Chicago")
        missing = root / "localtime-missing"
        check("system_timezone refuses when nothing exists to resolve",
              refuses(lambda: system_timezone(missing)))


def main() -> int:
    check_system_timezone()
    locator = "cc-update-audit"
    portable = REPO / "ops/scheduled-tasks/cc-update-audit.SKILL.md"
    digest = hashlib.sha256(portable.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        vault = home / "My Drive" / "CARR AI"
        live = home / ".claude/scheduled-tasks" / locator / "SKILL.md"
        live.parent.mkdir(parents=True)
        live.write_text(portable.read_text(encoding="utf-8").replace("{{HOME}}", str(home))
                        .replace("{{REPO}}", str(REPO)).replace("{{VAULT}}", str(vault)), encoding="utf-8")
        snapshot = home / "Library/Application Support/Claude/claude-code-sessions/account/session/scheduled-tasks.json"
        snapshot.parent.mkdir(parents=True)
        task = {"id": locator, "cronExpression": "45 9 * * 1", "enabled": True,
                "filePath": str(live), "cwd": str(vault), "createdAt": 1,
                "lastRunAt": "2026-08-16T14:45:00Z", "lastScheduledFor": "2026-08-16T14:45:00Z"}
        snapshot.write_text(json.dumps({"scheduledTasks": [task]}), encoding="utf-8")
        observed = read_native_task(home=home, repo=REPO, locator=locator, expected_cron="45 9 * * 1",
                                    expected_timezone="America/Chicago", portable_definition_sha256=digest,
                                    observed_at=datetime(2026, 8, 16, 18, 0, tzinfo=timezone.utc),
                                    host_timezone="America/Chicago")
        check("native reader derives enabled state and provenance from Claude-owned snapshot",
              observed["enabled"] is True and observed["timezone"] == "America/Chicago"
              and len(observed["source_fingerprint"]) == 64
              and len(observed["provider_revision"]) == 64)
        check("native snapshot discovery requires one exact provider state file", discover_snapshot(home) == snapshot)
        bad = dict(task); bad["enabled"] = "false"
        snapshot.write_text(json.dumps({"scheduledTasks": [bad]}), encoding="utf-8")
        check("string/caller-like enabled state is refused", refuses(lambda: read_native_task(
            home=home, repo=REPO, locator=locator, expected_cron="45 9 * * 1",
            expected_timezone="America/Chicago", portable_definition_sha256=digest,
            host_timezone="America/Chicago")))
        snapshot.write_text(json.dumps({"scheduledTasks": [task]}), encoding="utf-8")
        live.write_text("drifted", encoding="utf-8")
        check("live task definition drift is refused", refuses(lambda: read_native_task(
            home=home, repo=REPO, locator=locator, expected_cron="45 9 * * 1",
            expected_timezone="America/Chicago", portable_definition_sha256=digest,
            host_timezone="America/Chicago")))
        live.write_text(portable.read_text(encoding="utf-8").replace("{{HOME}}", str(home))
                        .replace("{{REPO}}", str(REPO)).replace("{{VAULT}}", str(vault)), encoding="utf-8")
        check("host timezone drift is refused instead of assigning the expected zone",
              refuses(lambda: read_native_task(
                  home=home, repo=REPO, locator=locator, expected_cron="45 9 * * 1",
                  expected_timezone="America/Chicago", portable_definition_sha256=digest,
                  host_timezone="America/New_York")))
        second = snapshot.parent.parent / "other/scheduled-tasks.json"
        second.parent.mkdir(parents=True); second.write_text(snapshot.read_text(encoding="utf-8"), encoding="utf-8")
        check("ambiguous provider snapshot is refused", refuses(lambda: discover_snapshot(home)))
    print(f"Claude scheduler native selftest — {len(FAILED)} failure(s)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
