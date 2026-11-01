"""Shared filing deadline for the Outlook intake and orchestrator watchdog."""
from datetime import datetime, timedelta


def business_day_deadline(since: datetime, timezone) -> datetime:
    """Same local wall time on the next Monday-Friday day; holidays are not excluded."""
    deadline = since.astimezone(timezone) + timedelta(days=1)
    while deadline.weekday() >= 5:
        deadline += timedelta(days=1)
    return deadline
