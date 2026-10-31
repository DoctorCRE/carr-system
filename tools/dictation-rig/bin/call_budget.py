"""Durable, conservative dollar reservations for the Studio's call checks."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import json
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo

# Provider model reference: 64 Ki tokens at $0.042/M, output free.
# Reserve the whole context, round up, never refund uncertain/failed requests.
MODEL = 'jev-1.14.0'
REQUEST_MICRODOLLARS = 2753


class BudgetHold(RuntimeError):
    """Checks stay proposals when a dollar reservation cannot be persisted."""


def reserve(ledger: Path, *, instant: datetime | None = None) -> None:
    try:
        dollars = Decimal(os.environ.get('CALL_JEV_DAILY_DOLLARS', ''))
        if not dollars.is_finite() or dollars <= 0:
            raise ValueError
        cap = int(dollars * 1_000_000)
    except (InvalidOperation, ValueError, OverflowError):
        raise BudgetHold('Jev daily dollar budget is unconfigured or invalid.') from None
    day = (instant or datetime.now(timezone.utc)).astimezone(ZoneInfo('America/Chicago')).date().isoformat()
    if cap < REQUEST_MICRODOLLARS:
        raise BudgetHold('Jev daily dollar budget is exhausted.')
    try:
        ledger.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(ledger.with_suffix('.lock'), os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, 'a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            days = json.loads(ledger.read_text()) if ledger.exists() else {}
            if not isinstance(days, dict) or any(type(v) is not int or v < 0 for v in days.values()):
                raise ValueError('invalid dollar ledger')
            used = days.get(day, 0)
            if used + REQUEST_MICRODOLLARS > cap:
                raise BudgetHold('Jev daily dollar budget is exhausted.')
            days[day] = used + REQUEST_MICRODOLLARS
            fd, temporary = tempfile.mkstemp(dir=ledger.parent, prefix='.jev-budget-')
            try:
                with os.fdopen(fd, 'w') as handle:
                    json.dump(days, handle, sort_keys=True)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, ledger)
                directory = os.open(ledger.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                Path(temporary).unlink(missing_ok=True)
    except (OSError, ValueError, TypeError) as exc:
        raise BudgetHold('Jev dollar budget ledger is unavailable; checks held.') from exc
