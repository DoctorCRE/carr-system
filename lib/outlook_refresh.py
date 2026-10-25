"""Refresh intake's local preview queue on the intake host, without filing mail.

The intake lock serializes queue updates. Opening a row only stamps opened_at.
Intake removes the item after decoding and acknowledging its full-text note.
"""
from datetime import datetime, timedelta, timezone
import fcntl
import os
from pathlib import Path

from lib import outlook_intake as intake
from lib import outlook_triage as triage
from lib.outlook_send import account
from lib.outlook_ui import (CuaError, Outcome, UiError, Waits, Window,
                            outlook_pid, prepare_outlook, usable_window)


def run(repo: Path, now: datetime, waits: Waits = Waits(),
        now_fn=lambda: datetime.now(timezone.utc)) -> Outcome:
    if not intake.in_window(now):
        return Outcome('skipped', 'outside_window')
    path = repo / intake.OUT / intake.REFRESH_LIST
    if not path.exists():
        return Outcome('skipped', 'nothing_pending')
    fd = intake._private_fd(path.parent / 'lock', os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return Outcome('skipped', 'previous_run_active')
        if not triage.read_refresh_list(path):
            return Outcome('skipped', 'nothing_pending')
        idle = triage.hid_idle_seconds()
        if idle is None:
            return Outcome('failed', 'idle_time_unreadable')
        if idle < triage.IDLE_SECONDS:
            return Outcome('skipped', 'user_active')
        blocked = prepare_outlook(waits)
        if blocked:
            return blocked
        pid = outlook_pid()
        found = usable_window(pid) if pid else None
        if not found or pid is None:
            return Outcome('failed', 'no_usable_main_window')
        try:
            expected_account = account(Path.home() / '.config/carr/outlook-account')
        except (OSError, ValueError):
            return Outcome('failed', 'account_config_invalid')
        if not found.get('title', '').endswith(f' • {expected_account}'):
            return Outcome('failed', 'wrong_account')
        window = Window(pid, found['window_id'], waits)
        deadline = now_fn() + timedelta(minutes=10)
        closed = lambda: now_fn() >= deadline or not intake.in_window(now_fn())
        report = triage.Report()
        try:
            window.front()
            return triage.preserve_unread(window, report, lambda tabs: (
                triage.refresh_all(window, path, triage.Limits(), report, closed)
                or Outcome('succeeded', 'refreshed')))
        finally:
            window.show_folder_pane(True)
    except ValueError:
        return Outcome('failed', 'refresh_list_invalid')
    except (OSError, UiError, CuaError):
        return Outcome('failed', 'refresh_failed')
    finally:
        os.close(fd)


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    outcome = run(repo, datetime.now(timezone.utc))
    print(f'outlook-refresh {outcome.state} {outcome.reason}')
    return triage.EXIT[outcome.state]
