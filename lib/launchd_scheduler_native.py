"""Read macOS launchd state without accepting caller-asserted scheduler facts."""
from __future__ import annotations

import hashlib
import json
import os
import plistlib
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from lib.claude_scheduler_native import system_timezone
from lib.control_plane_scheduler_cutover import CutoverRefusal

Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _normalize(value: Any, *, repo: Path) -> Any:
    if isinstance(value, str):
        return value.replace(str(repo), "{{REPO}}")
    if isinstance(value, list):
        return [_normalize(item, repo=repo) for item in value]
    if isinstance(value, dict):
        return {key: _normalize(item, repo=repo) for key, item in value.items()}
    return value


def _read_plist(path: Path) -> dict[str, Any]:
    try:
        value = plistlib.loads(path.read_bytes())
    except (OSError, plistlib.InvalidFileException) as exc:
        raise CutoverRefusal("launchd plist is unavailable or malformed") from exc
    if not isinstance(value, dict):
        raise CutoverRefusal("launchd plist is not a dictionary")
    return value


def _default_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False)


# TWO VOCABULARIES, AND ONLY ONE OF THEM EXISTS ON THIS MAC. `launchctl
# print-disabled` prints a boolean on older macOS and the words disabled/enabled
# on macOS 26: measured 2026-08-27 on 26.5.2, 0 lines matched `=> true|false`
# and 59 matched `=> disabled|enabled`. Reading only the boolean form made every
# genuinely disabled job read back as ENABLED, and because a disable readback
# requires an enabled-then-disabled pair, the last step of the whole scheduler
# cutover could never be evidenced on this machine. Accept both spellings and
# refuse anything else rather than guessing a default, because "unrecognised"
# and "not disabled" are different findings (rule 88e9b5eb).
_OVERRIDE_WORDS = {"true": True, "disabled": True, "false": False, "enabled": False}


def _disabled_override(text: str, locator: str) -> bool:
    pattern = re.compile(rf'"?{re.escape(locator)}"?\s*=>\s*([A-Za-z]+)\b')
    matches = pattern.findall(text)
    if len(matches) > 1:
        raise CutoverRefusal("launchd disabled-state registry contains an ambiguous duplicate label")
    if not matches:
        return False
    word = matches[0].lower()
    if word not in _OVERRIDE_WORDS:
        raise CutoverRefusal("launchd disabled-state registry uses an unrecognised state word")
    return _OVERRIDE_WORDS[word]


def read_native_launchd(
    *, home: Path, repo: Path, locator: str, repo_plist_relpath: str,
    installed_plist_name: str, expected_program_arguments: list[str],
    plist_sha256: str, schedule_sha256: str, expected_timezone: str,
    runner: Runner = _default_runner, observed_at: datetime | None = None,
    host_timezone: str | None = None, uid: int | None = None,
    installed_repo: Path | None = None,
) -> dict[str, Any]:
    """Read exact tracked/installed plist state plus native launchctl state."""
    repo_path = repo / repo_plist_relpath
    library_path = home / "Library"
    launch_agents_path = library_path / "LaunchAgents"
    installed_path = launch_agents_path / installed_plist_name
    if any(path.is_symlink() for path in (library_path, launch_agents_path, installed_path)):
        raise CutoverRefusal("installed launchd plist path must not traverse a symlink")
    repo_plist = _read_plist(repo_path)
    runtime_repo = installed_repo or (home / "carr-system")
    installed_plist = _normalize(_read_plist(installed_path), repo=runtime_repo)
    expected = _normalize(repo_plist, repo=repo)
    if installed_plist != expected:
        raise CutoverRefusal("installed launchd plist differs from the tracked definition")
    if (expected.get("Label") != locator
            or expected.get("ProgramArguments") != expected_program_arguments
            or _digest(expected) != plist_sha256):
        raise CutoverRefusal("tracked launchd plist does not match the registered contract")
    schedule_keys = ("StartCalendarInterval", "StartInterval", "StartOnMount", "KeepAlive")
    schedule = {key: expected[key] for key in schedule_keys if key in expected}
    if not schedule or _digest(schedule) != schedule_sha256:
        raise CutoverRefusal("launchd native recurrence does not match the registered contract")
    zone = host_timezone if host_timezone is not None else system_timezone()
    if zone != expected_timezone:
        raise CutoverRefusal("launchd host timezone does not match the registered contract")

    resolved_uid = os.getuid() if uid is None else uid
    job = runner(["/bin/launchctl", "print", f"gui/{resolved_uid}/{locator}"])
    disabled = runner(["/bin/launchctl", "print-disabled", f"gui/{resolved_uid}"])
    if disabled.returncode != 0:
        raise CutoverRefusal("launchd disabled-state registry is unreadable")
    override = _disabled_override(disabled.stdout, locator)
    if job.returncode == 0 and override is not True:
        enabled = True
    elif job.returncode != 0 and override is True:
        enabled = False
    else:
        raise CutoverRefusal("launchd native state is absent, ambiguous, or contradictory")

    job_digest = hashlib.sha256((job.stdout + "\n" + job.stderr).encode("utf-8")).hexdigest()
    disabled_digest = hashlib.sha256((disabled.stdout + "\n" + disabled.stderr).encode("utf-8")).hexdigest()
    projection = {
        "label": locator, "enabled": enabled, "uid": resolved_uid, "timezone": zone,
        "plist_sha256": plist_sha256, "schedule_sha256": schedule_sha256,
        "job_returncode": job.returncode, "job_output_sha256": job_digest,
        "disabled_output_sha256": disabled_digest,
    }
    instant = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "label": locator, "timezone": zone, "enabled": enabled,
        "plist_sha256": plist_sha256, "schedule_sha256": schedule_sha256,
        "launchctl_revision": _digest({"job": job_digest, "disabled": disabled_digest}),
        "source_fingerprint": _digest(projection),
        "observed_at": instant.isoformat().replace("+00:00", "Z"),
    }
