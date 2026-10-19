"""Cheap recurrence decisions shared by task prompts and the headless runner."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
from zoneinfo import ZoneInfo

CHICAGO = ZoneInfo('America/Chicago')


def stop_reason(task_id: str, now: datetime, rows: list[dict[str, Any]],
                *, monthly_days: list[int] | None = None) -> str | None:
    if now.tzinfo is None:
        raise ValueError('STEP-0 requires an aware clock')
    local = now.astimezone(CHICAGO)
    if monthly_days is not None and local.day not in monthly_days:
        return 'outside_monthly_window'
    if task_id.endswith('-weekly'):
        week = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        week = week.astimezone(timezone.utc)
        for row in rows:
            if (row.get('status') != 'success' or row.get('exit_code') != 0
                    or row.get('canonical_recorded') is not True):
                continue
            ended = datetime.fromisoformat(row['end'].replace('Z', '+00:00'))
            if ended.tzinfo is None:
                raise ValueError('completion requires an aware timestamp')
            window = datetime.fromisoformat(row['window'].replace('Z', '+00:00'))
            if window.tzinfo is None:
                raise ValueError('scheduled window requires an aware timestamp')
            if week <= window.astimezone(timezone.utc) <= ended.astimezone(timezone.utc) <= now.astimezone(timezone.utc):
                return 'weekly_completed'
    return None


def monthly_gate(repo: Path, task_id: str) -> int:
    try:
        return subprocess.run([sys.executable, str(repo/'bin/monthly-gate.py'), task_id, '--quiet'],
                              cwd=repo, timeout=30).returncode
    except subprocess.TimeoutExpired:
        print('PROCEED monthly_ledger_timeout')
        return 0


def version_unchanged(current: str, last: str) -> bool:
    return bool(re.fullmatch(r'cli=\d+\.\d+\.\d+ app=\d+\.\d+\.\d+', current)) and current == last


def version_gate(repo: Path, home: Path) -> bool:
    try:
        result = subprocess.run([str(repo/'bin/cc-version-string.sh')],
                                capture_output=True, text=True, timeout=30, check=True)
        current = result.stdout.strip()
        sentinel = home/'.claude/scheduled-tasks/cc-update-audit/last-audited-version.txt'
        last = sentinel.read_text().strip() if sentinel.exists() else ''
    except (OSError, UnicodeError, subprocess.SubprocessError):
        print('PROCEED version_observation_unavailable')
        return False
    for label, value in [('current', current), ('last_audited', last)]:
        safe = value if re.fullmatch(r'cli=(?:\d+\.\d+\.\d+|none) app=(?:\d+\.\d+\.\d+|none)', value) else 'unavailable'
        print(f'{label}: {safe}')
    return version_unchanged(current, last)
