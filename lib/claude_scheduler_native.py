"""Read Claude Desktop's native scheduled-task state without caller assertions."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.control_plane_scheduler_cutover import CutoverRefusal


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def discover_snapshot(home: Path) -> Path:
    root = home / "Library" / "Application Support" / "Claude" / "claude-code-sessions"
    matches = sorted(root.glob("*/*/scheduled-tasks.json"))
    if len(matches) != 1 or not matches[0].is_file():
        raise CutoverRefusal("Claude native scheduled-task snapshot is absent or ambiguous")
    return matches[0]


def system_timezone(localtime_path: Path = Path("/etc/localtime")) -> str:
    """Resolve the IANA zone used by Claude's host-local cron scheduler.

    /etc/localtime resolves through a directory literally named "zoneinfo" on
    macOS 26 and earlier. macOS 27 (confirmed on the Studio, BuildVersion
    26A425, found while restoring the six scheduled-task routines after the
    2026-09-23 stoppage) resolves it through "zoneinfo.default" instead:
    /usr/share/zoneinfo.default/America/Chicago. A literal "/zoneinfo/"
    substring match refuses every macOS 27 host, which would have made this
    routine's own standing check unable to observe anything running there.
    Accept any path component named "zoneinfo" or "zoneinfo.<anything>". Scan
    from the END of the path, not the start: an ancestor directory that
    happens to be named e.g. "zoneinfo.bak" earlier in the path (fixture:
    /home/zoneinfo.bak/usr/share/zoneinfo/America/Chicago) must not steal the
    match from the real zoneinfo directory that actually owns the tz name.
    (Flagged in review; the failure was already closed downstream because
    read_native_task compares the result against expected_timezone, but the
    case is worth removing outright rather than relying on that catch.)
    """
    try:
        resolved = str(localtime_path.resolve(strict=True))
    except OSError as exc:
        raise CutoverRefusal("Claude scheduler host timezone is unavailable") from exc
    parts = resolved.split("/")
    zone_index = next(
        (i for i in range(len(parts) - 1, -1, -1) if parts[i] == "zoneinfo" or parts[i].startswith("zoneinfo.")),
        None,
    )
    if zone_index is None or zone_index == len(parts) - 1:
        raise CutoverRefusal("Claude scheduler host timezone is not an IANA zone")
    zone = "/".join(parts[zone_index + 1:])
    if not zone:
        raise CutoverRefusal("Claude scheduler host timezone is empty")
    return zone


def read_native_task(*, home: Path, repo: Path, locator: str, expected_cron: str,
                     expected_timezone: str, portable_definition_sha256: str,
                     snapshot_path: Path | None = None, observed_at: datetime | None = None,
                     host_timezone: str | None = None) -> dict[str, Any]:
    """Read one exact task from provider-owned state and verify its live definition."""
    path = snapshot_path or discover_snapshot(home)
    try:
        raw = path.read_bytes()
        document = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise CutoverRefusal("Claude native scheduled-task snapshot is unreadable") from exc
    tasks = document.get("scheduledTasks") if isinstance(document, dict) else None
    if not isinstance(tasks, list):
        raise CutoverRefusal("Claude native scheduled-task snapshot has no task list")
    rows = [row for row in tasks if isinstance(row, dict) and row.get("id") == locator]
    if len(rows) != 1:
        raise CutoverRefusal("Claude native scheduler task identity is absent or ambiguous")
    row = rows[0]
    if row.get("cronExpression") != expected_cron or type(row.get("enabled")) is not bool:
        raise CutoverRefusal("Claude native scheduler recurrence/state does not match the registered contract")
    resolved_host_timezone = host_timezone if host_timezone is not None else system_timezone()
    if resolved_host_timezone != expected_timezone:
        raise CutoverRefusal("Claude scheduler host timezone does not match the registered contract")
    expected_file = home / ".claude" / "scheduled-tasks" / locator / "SKILL.md"
    if row.get("filePath") != str(expected_file):
        raise CutoverRefusal("Claude native scheduler definition path is not the registered task path")
    portable = repo / "ops" / "scheduled-tasks" / f"{locator}.SKILL.md"
    try:
        portable_bytes = portable.read_bytes()
        live_text = expected_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise CutoverRefusal("Claude scheduled-task definition is unavailable") from exc
    if hashlib.sha256(portable_bytes).hexdigest() != portable_definition_sha256:
        raise CutoverRefusal("checked-in scheduler definition changed after contract sync")
    cwd = row.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        raise CutoverRefusal("Claude native scheduler task has no working-directory provenance")
    canonical_live = live_text
    replacements = ((str(repo), "{{REPO}}"), (cwd, "{{VAULT}}"), (str(home), "{{HOME}}"))
    for real, token in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
        canonical_live = canonical_live.replace(real, token)
    if canonical_live.encode("utf-8") != portable_bytes:
        raise CutoverRefusal("Claude live scheduled-task definition does not match the tracked portable source")
    projection = {
        "id": locator, "cronExpression": row["cronExpression"], "enabled": row["enabled"],
        "filePath": row["filePath"], "cwd": cwd, "createdAt": row.get("createdAt"),
        "lastRunAt": row.get("lastRunAt"), "lastScheduledFor": row.get("lastScheduledFor"),
        "hostTimezone": resolved_host_timezone,
    }
    instant = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "provider_task_id": locator, "cron_expression": expected_cron,
        "timezone": resolved_host_timezone, "enabled": row["enabled"],
        "definition_sha256": portable_definition_sha256,
        "provider_revision": hashlib.sha256(raw).hexdigest(),
        "source_fingerprint": hashlib.sha256(_canonical(projection)).hexdigest(),
        "observed_at": instant.isoformat().replace("+00:00", "Z"),
    }
